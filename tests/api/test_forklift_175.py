"""v1.75.0 : Forklift sur Harvester, installation. L'add-on expérimental
forklift-operator (chart de charts.harvesterhci.io) exige qu'on pose dépôt,
image et tag de l'opérateur (le défaut du chart est rancher/nginx:latest),
puis un ForkliftController fait déployer les composants, qui exigent
cert-manager (absent de Harvester)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))
import hv_forklift as hf  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "forklift_inventory_vms_175.json"


def dep(name, ready=True, replicas=1):
    return {"metadata": {"name": name}, "spec": {"replicas": replicas},
            "status": {"availableReplicas": replicas if ready else 0}}


def test_the_addon_always_names_the_operator_image():
    a = hf.addon_manifest()
    assert a["apiVersion"] == "harvesterhci.io/v1beta1" and a["kind"] == "Addon"
    assert a["metadata"] == {"name": "forklift-operator", "namespace": "forklift",
                             "labels": {"addon.harvesterhci.io/experimental": "true", "harvester-ops.io/managed": "true"}}
    s = a["spec"]
    assert (s["enabled"], s["repo"], s["chart"], s["version"]) == (True, "https://charts.harvesterhci.io", "forklift-operator", "1.9.0")
    values = json.loads(s["valuesContent"])       # du JSON : c'est du YAML valide pour Harvester
    op = values["forkliftOperatorAnsible"]["forkliftOperator"]
    assert (op["repo"], op["operatorImage"], op["tag"]) == ("registry.rancher.com/harvester", "harvester-forklift-operator", "v1.8.2")
    assert values["fullnameOverride"] == "harvester"   # d'où le déploiement harvester-forklift-operator-ansible
    other = json.loads(hf.addon_manifest("1.8.2", "main-head")["spec"]["valuesContent"])
    assert other["forkliftOperatorAnsible"]["forkliftOperator"]["tag"] == "main-head"


@pytest.mark.parametrize("version,tag,match", [
    ("latest", "v1.8.2", "chart version"), ("1.9", "v1.8.2", "chart version"),
    ("1.9.0", "", "image tag"), ("1.9.0", "v1 8", "image tag"), ("1.9.0", "-bad", "image tag"),
])
def test_a_version_or_tag_that_would_break_the_chart_is_refused(version, tag, match):
    with pytest.raises(ValueError, match=match):
        hf.addon_manifest(version, tag)


def test_the_controller_and_its_namespace():
    assert hf.namespace_manifest() == {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "forklift"}}
    c = hf.controller_manifest()
    assert c == {"apiVersion": "forklift.konveyor.io/v1beta1", "kind": "ForkliftController",
                 "metadata": {"name": "forklift-controller", "namespace": "forklift"},
                 "spec": {"feature_ui_plugin": "false"}}


def test_the_addon_state_follows_harvester_s_statuses():
    assert hf.addon_state(None)[0] == "absent"
    assert hf.addon_state({"spec": {"enabled": False}})[0] == "disabled"
    assert hf.addon_state({"spec": {"enabled": True}, "status": {"status": "AddonEnabling"}})[0] == "deploying"
    for ok in ("AddonDeploySuccessful", "AddonUpdateSuccessful", "AddonDeployed"):
        assert hf.addon_state({"spec": {"enabled": True}, "status": {"status": ok}})[0] == "ready"
    st, msg = hf.addon_state({"spec": {"enabled": True}, "status": {"status": "AddonDeployFailed", "conditions": [
        {"type": "OperationFailed", "status": "True", "message": "chart forklift-operator-9.9.9 not found"}]}})
    assert st == "failed" and "chart forklift-operator-9.9.9 not found" in msg


def test_a_deployment_is_ready_when_all_its_replicas_are_available():
    assert hf.deployment_ready(dep("x")) and not hf.deployment_ready(dep("x", ready=False))
    assert not hf.deployment_ready(None) and not hf.deployment_ready({"spec": {"replicas": 2}, "status": {"availableReplicas": 1}})


def test_the_install_state_is_read_in_the_order_it_is_done():
    cm = {n: dep(n) for n in hf.CERT_MANAGER[1]}
    ok_addon = {"spec": {"enabled": True}, "status": {"status": "AddonDeploySuccessful"}}
    deploys = {n: dep(n) for n in (hf.OPERATOR_DEPLOY,) + hf.COMPONENTS}
    st = hf.install_state(ok_addon, deploys, {"kind": "ForkliftController"}, cm)
    assert st["ready"] and st["cert_manager"] and st["operator"] and st["controller"] and st["components_missing"] == []
    st = hf.install_state(None, {}, None, {})
    assert not st["ready"] and st["addon"] == "absent" and st["cert_manager_missing"] == list(hf.CERT_MANAGER[1])
    deploys["forklift-validation"] = dep("forklift-validation", ready=False)
    st = hf.install_state(ok_addon, deploys, {"kind": "ForkliftController"}, cm)
    assert not st["ready"] and st["components_missing"] == ["forklift-validation"]


def test_a_pod_that_cannot_pull_or_start_is_named_with_its_reason():
    pods = [
        {"metadata": {"name": "forklift-controller-abc"}, "status": {"containerStatuses": [
            {"state": {"waiting": {"reason": "ImagePullBackOff",
                                   "message": 'Back-off pulling image "registry.rancher.com/harvester/harvester-forklift-controller:v1.9.0"'}}}]}},
        {"metadata": {"name": "forklift-api-def"}, "status": {"containerStatuses": [{"state": {"running": {}}}]}},
        {"metadata": {"name": "forklift-validation-ghi"}, "status": {"initContainerStatuses": [
            {"state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}},
    ]
    probs = hf.pod_problems(pods)
    assert probs[0].startswith("forklift-controller-abc: ImagePullBackOff") and "v1.9.0" in probs[0]
    assert probs[1] == "forklift-validation-ghi: CrashLoopBackOff" and len(probs) == 2


def test_harvester_s_own_addon_wins_over_the_console_s():
    ours = {"metadata": {"name": "forklift-operator", "namespace": "forklift",
                         "labels": {"harvester-ops.io/managed": "true"}}}
    theirs = {"metadata": {"name": "forklift-operator", "namespace": "harvester-system"}, "spec": {"version": "1.9.1"}}
    other = {"metadata": {"name": "vm-import-controller", "namespace": "harvester-system"}}
    assert hf.pick_addon([ours, other]) == (ours, False)
    assert hf.pick_addon([ours, theirs, other]) == (theirs, True)
    assert hf.pick_addon([other]) == (None, False)


def test_the_inventory_reader_can_only_read_providers():
    sa, role, binding = hf.inventory_rbac()
    assert (sa["kind"], sa["metadata"]["name"], sa["metadata"]["namespace"]) == ("ServiceAccount", "harvester-ops-inventory", "forklift")
    assert role["kind"] == "ClusterRole" and role["rules"] == [
        {"apiGroups": ["forklift.konveyor.io"], "resources": ["providers"], "verbs": ["get", "list"]}]
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "harvester-ops-inventory", "namespace": "forklift"}]


CA = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"


@pytest.mark.parametrize("given,want", [
    ("vc.lan", "https://vc.lan/sdk"), ("https://vc.lan", "https://vc.lan/sdk"),
    ("https://vc.lan/sdk", "https://vc.lan/sdk"), ("172.16.2.81", "https://172.16.2.81/sdk"),
    ("https://vc.lan:8443/sdk/", "https://vc.lan:8443/sdk"),
])
def test_the_vcenter_url_is_the_one_forklift_expects(given, want):
    assert hf.check_url(given) == want


@pytest.mark.parametrize("bad", ["http://vc.lan/sdk", "", "https://vc lan", "https://vc.lan/ui"])
def test_a_vcenter_url_forklift_cannot_use_is_refused(bad):
    with pytest.raises(ValueError, match="vCenter URL"):
        hf.check_url(bad)


def test_an_image_must_be_complete_and_pullable():
    for ok in ("172.16.1.11:3000/jniedergang/vddk:8.0.3", "registry.example.com/vddk@sha256:" + "a" * 64,
               "harbor.lan/proj/vddk:8.0.3-1"):
        assert hf.check_image(ok) == ok
    for bad in ("vddk", "harbor.lan/proj/vddk", "Harbor.lan/Proj/vddk:1", "harbor.lan/proj/vddk:bad tag"):
        with pytest.raises(ValueError, match="VDDK image"):
            hf.check_image(bad, "VDDK image")


def test_the_provider_secret_is_labelled_as_forklift_reads_it():
    s = hf.provider_secret("default", "vmwlab", {"url": "172.16.2.81", "user": "administrator@vsphere.local",
                                                 "password": "pw", "insecure": True})
    assert s["metadata"]["name"] == "vmwlab-vsphere" and s["metadata"]["namespace"] == "default"
    assert s["metadata"]["labels"] == {"createdForProviderType": "vsphere", "createdForResourceType": "providers",
                                       "harvester-ops.io/managed": "true"}
    assert s["stringData"] == {"user": "administrator@vsphere.local", "password": "pw",
                               "url": "https://172.16.2.81/sdk", "insecureSkipVerify": "true"}
    s = hf.provider_secret("default", "vc", {"url": "vc.lan", "user": "u", "password": "p", "cacert": CA})
    assert s["stringData"]["insecureSkipVerify"] == "false" and s["stringData"]["cacert"] == CA + "\n"
    long_name = "a" * 63
    assert len(hf.secret_name(long_name)) <= 63 and hf.secret_name(long_name).endswith("-vsphere")


@pytest.mark.parametrize("spec,match", [
    ({"url": "vc.lan", "user": "u"}, "user and password"),
    ({"url": "vc.lan", "user": "", "password": "Zq9-secret"}, "user and password"),
    ({"url": "vc.lan", "user": "u", "password": "Zq9-secret", "cacert": "not a pem"}, "PEM"),
    ({"url": "http://vc.lan", "user": "u", "password": "Zq9-secret"}, "https only"),
])
def test_an_incomplete_provider_is_refused_without_echoing_the_password(spec, match):
    with pytest.raises(ValueError, match=match) as e:
        hf.provider_secret("default", "vc", spec)
    assert "Zq9-secret" not in str(e.value)


def test_the_provider_points_at_its_secret_and_the_vddk_image():
    p = hf.provider_manifest("default", "vmwlab", {"url": "172.16.2.81", "vddk_image": "172.16.1.11:3000/ju/vddk:8.0.3"})
    assert p["apiVersion"] == "forklift.konveyor.io/v1beta1" and p["kind"] == "Provider"
    assert p["spec"] == {"type": "vsphere", "url": "https://172.16.2.81/sdk",
                         "secret": {"name": "vmwlab-vsphere", "namespace": "default"},
                         "settings": {"sdkEndpoint": "vcenter", "vddkInitImage": "172.16.1.11:3000/ju/vddk:8.0.3"}}
    assert "vddkInitImage" not in hf.provider_manifest("default", "vc", {"url": "vc.lan"})["spec"]["settings"]


def test_the_provider_state_is_ready_refused_or_being_checked():
    assert hf.provider_state(None)[0] is None
    ok = {"status": {"phase": "Ready", "conditions": [
        {"type": "ConnectionTestSucceeded", "status": "True", "category": "Required"},
        {"type": "InventoryCreated", "status": "True", "category": "Required"},
        {"type": "Ready", "status": "True", "category": "Required"}]}}
    assert hf.provider_state(ok)[0] is True
    bad = {"status": {"phase": "ConnectionFailed", "conditions": [
        {"type": "ConnectionTestFailed", "status": "True", "category": "Critical",
         "message": "Login failed: incorrect user name or password."}]}}
    res, msg = hf.provider_state(bad)
    assert res is False and "incorrect user name or password" in msg
    staging = {"status": {"phase": "Staging", "conditions": [
        {"type": "ConnectionTestSucceeded", "status": "True", "category": "Required"}]}}
    res, msg = hf.provider_state(staging)
    assert res is None and "Staging" in msg and "ConnectionTestSucceeded" in msg


def test_plans_that_use_a_provider_are_found():
    plans = [{"metadata": {"name": "wave-1", "namespace": "mig"},
              "spec": {"provider": {"source": {"name": "vmwlab", "namespace": "default"}}}},
             {"metadata": {"name": "other", "namespace": "mig"},
              "spec": {"provider": {"source": {"name": "vc2", "namespace": "default"}}}}]
    assert hf.plans_using("default", "vmwlab", plans) == ["mig/wave-1"]
    assert hf.plans_using("default", "nobody", plans) == []


def test_the_provider_points_at_the_very_secret_that_is_created():
    spec = {"url": "vc.lan", "user": "u", "password": "p", "insecure": True}
    sec = hf.provider_secret("  default ", "  vmwlab  ", spec)
    prov = hf.provider_manifest("  default ", "  vmwlab  ", spec)
    assert prov["spec"]["secret"] == {"name": sec["metadata"]["name"], "namespace": sec["metadata"]["namespace"]}
    assert prov["metadata"]["name"] == "vmwlab" and sec["metadata"]["name"] == "vmwlab-vsphere"


def test_the_real_inventory_gives_what_a_wave_needs():
    rows = {r["name"]: r for r in hf.inventory_rows("vms", json.loads(FIX.read_text()))}
    assert {"vmwlab-src-1", "vmwlab-src-2", "vmwlab-src-3"} <= set(rows)
    src1 = rows["vmwlab-src-1"]
    assert src1["cbt"] is True and src1["id"].startswith("vm-") and src1["power"] == "poweredOn"
    assert src1["cpus"] == 1 and src1["memory_mib"] == 1024 and src1["disks"] and src1["networks"]
    assert rows["vmwlab-src-3"]["memory_mib"] == 4096


def test_networks_and_datastores_are_named_rows():
    rows = hf.inventory_rows("datastores", [{"id": "datastore-11", "name": "datastore1", "path": "/vmwlab-dc/datastore/datastore1",
                                             "capacity": 506806140928, "free": 400000000000, "extra": 1}])
    assert rows == [{"id": "datastore-11", "name": "datastore1", "path": "/vmwlab-dc/datastore/datastore1",
                     "capacity": 506806140928, "free": 400000000000}]
    assert hf.inventory_rows("networks", [{"id": "network-12", "name": "VM Network", "path": "/x"}]) == [
        {"id": "network-12", "name": "VM Network", "path": "/x"}]
    with pytest.raises(ValueError, match="kind"):
        hf.inventory_rows("hosts", [])
