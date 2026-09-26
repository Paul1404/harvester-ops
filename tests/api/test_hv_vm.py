"""v1.60.0 : bin/lib/hv_vm.py, les gestes du menu d'une VM de Harvester.

Formes relevées sur harv1 (Harvester v1.9.0, KubeVirt 1.8.4) : volumes nés de
l'annotation volumeClaimTemplates, cloud-init en Secret référencé par
`secretRef` (nom JSON), template en deux objets.
"""

import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_vm as hv  # noqa: E402

VCT = [{"metadata": {"name": "web-root", "annotations": {"harvesterhci.io/imageId": "default/leap"}},
        "spec": {"accessModes": ["ReadWriteMany"], "resources": {"requests": {"storage": "10Gi"}},
                 "volumeMode": "Block", "storageClassName": "lh-leap"}},
       {"metadata": {"name": "web-iso"}, "spec": {"accessModes": ["ReadWriteMany"],
                                                   "resources": {"requests": {"storage": "1Gi"}},
                                                   "volumeMode": "Block", "storageClassName": "lh-iso"}}]


def vm():
    return {
        "apiVersion": "kubevirt.io/v1", "kind": "VirtualMachine",
        "metadata": {"name": "web", "namespace": "default", "uid": "u1", "resourceVersion": "7",
                     "annotations": {hv.VCT: json.dumps(VCT), "harvesterhci.io/mac-address": "x",
                                     "network.harvesterhci.io/ips": "[]", hv.SSH_NAMES: '["mykey"]'},
                     "labels": {"harvesterhci.io/os": "opensuse"}},
        "spec": {"runStrategy": "RerunOnFailure", "template": {
            "metadata": {"labels": {"harvesterhci.io/vmName": "web"}},
            "spec": {"hostname": "web", "domain": {"devices": {
                "disks": [{"name": "root", "disk": {"bus": "virtio"}}, {"name": "iso", "cdrom": {"bus": "sata"}},
                          {"name": "cloudinitdisk", "disk": {"bus": "virtio"}}],
                "interfaces": [{"name": "default", "bridge": {}, "macAddress": "52:54:00:aa:bb:cc"}]}},
                "volumes": [{"name": "root", "persistentVolumeClaim": {"claimName": "web-root"}},
                            {"name": "iso", "persistentVolumeClaim": {"claimName": "web-iso"}},
                            {"name": "data", "persistentVolumeClaim": {"claimName": "extra", "hotpluggable": True}},
                            {"name": "cloudinitdisk", "cloudInitNoCloud": {"secretRef": {"name": "web-ci"},
                                                                            "networkDataSecretRef": {"name": "web-ci"}}}]}}}}


def test_volumes_and_their_kinds():
    vols = {x["volume"]: x for x in hv.vm_volumes(vm())}
    assert vols["root"]["claim"] == "web-root" and vols["root"]["kind"] == "disk"
    assert vols["iso"]["kind"] == "cdrom"
    assert vols["data"]["hotpluggable"] is True
    assert vols["cloudinitdisk"]["source"] == "cloudinit" and vols["cloudinitdisk"]["claim"] is None


def test_the_cloudinit_secret_is_read_by_its_json_name():
    # secretRef est le nom JSON de userDataSecretRef : c'est lui qui est sur le cluster
    assert hv.cloudinit_secrets(vm()) == ["web-ci"]


def test_delete_only_takes_the_vm_own_volumes():
    ann, remove = hv.delete_plan(vm(), ["web-root", "web-iso"])
    assert ann == {hv.REMOVED_PVCS: "web-root,web-iso"} and remove == ["web-root", "web-iso"]
    with pytest.raises(ValueError, match="not volumes of this VM: other"):
        hv.delete_plan(vm(), ["web-root", "other"])


EXTRA = {"spec": {"storageClassName": "harv-rep1", "accessModes": ["ReadWriteMany"], "volumeMode": "Block",
                  "resources": {"requests": {"storage": "5Gi"}}}}


def test_a_clone_with_data_copies_each_volume_from_the_original():
    pvcs = {"web-root": {"spec": {"storageClassName": "lh-leap", "resources": {"requests": {"storage": "20Gi"}}}},
            "extra": EXTRA}
    out, renames = hv.clone_manifest(vm(), "web2", with_data=True, pvcs=pvcs, rnd=random.Random(1))
    meta = out["metadata"]
    assert meta["name"] == "web2" and "uid" not in meta and "resourceVersion" not in meta
    assert "harvesterhci.io/mac-address" not in meta["annotations"]
    assert "network.harvesterhci.io/ips" not in meta["annotations"]
    ts = out["spec"]["template"]["spec"]
    assert ts["hostname"] == "web2" and "macAddress" not in ts["domain"]["devices"]["interfaces"][0]
    assert out["spec"]["template"]["metadata"]["labels"]["harvesterhci.io/vmName"] == "web2"
    assert out["spec"]["runStrategy"] == "Halted"
    claims = {v["name"]: v["persistentVolumeClaim"]["claimName"] for v in ts["volumes"] if "persistentVolumeClaim" in v}
    assert set(renames) == {"web-root", "web-iso", "extra"}
    assert all(c.startswith("web2-") and c != o for o, c in renames.items())
    assert claims["root"] == renames["web-root"]
    templates = {t["metadata"]["name"]: t for t in json.loads(meta["annotations"][hv.VCT])}
    root = templates[renames["web-root"]]
    assert root["spec"]["dataSource"] == {"kind": "PersistentVolumeClaim", "name": "web-root"}
    assert root["spec"]["resources"]["requests"]["storage"] == "20Gi"      # la taille actuelle, pas celle d'origine
    assert root["metadata"]["annotations"]["harvesterhci.io/imageId"] == "default/leap"
    # un volume branché à chaud, sans modèle : sa forme vient du PVC lu
    assert templates[renames["extra"]]["spec"]["storageClassName"] == "harv-rep1"


def test_a_clone_without_data_starts_again_from_the_images():
    out, renames = hv.clone_manifest(vm(), "web3", with_data=False, pvcs={"extra": EXTRA},
                                     rnd=random.Random(2), start=True)
    templates = json.loads(out["metadata"]["annotations"][hv.VCT])
    assert all("dataSource" not in t["spec"] for t in templates)
    assert out["spec"]["runStrategy"] == "RerunOnFailure"


def test_a_volume_whose_shape_cannot_be_read_is_refused():
    with pytest.raises(ValueError, match="volume extra"):
        hv.clone_manifest(vm(), "web4", pvcs={})


def test_the_cloudinit_copy_follows_the_clone():
    secret = {"metadata": {"name": "web-ci", "namespace": "default"}, "data": {"userdata": "eA=="}}
    cp = hv.cloudinit_copy(secret, "web2", rnd=random.Random(1))
    assert cp["metadata"]["name"].startswith("web2-") and cp["data"] == {"userdata": "eA=="}
    out, _ = hv.clone_manifest(vm(), "web2", with_data=False, pvcs={"extra": EXTRA}, rnd=random.Random(1))
    hv.rename_secret_refs(out, "web-ci", cp["metadata"]["name"])
    assert hv.cloudinit_secrets(out) == [cp["metadata"]["name"]]


def test_a_template_from_a_vm():
    tmpl, version = hv.template_objects(vm(), "web-tpl", "base web")
    assert tmpl["kind"] == "VirtualMachineTemplate" and tmpl["spec"]["description"] == "base web"
    assert version["metadata"]["generateName"] == "web-tpl-"
    assert version["spec"]["templateId"] == "default/web-tpl"
    assert version["spec"]["keyPairIds"] == ["default/mykey"]
    vm_ann = version["spec"]["vm"]["metadata"]["annotations"]
    assert "harvesterhci.io/mac-address" not in vm_ann and hv.VCT in vm_ann
    ifc = version["spec"]["vm"]["spec"]["template"]["spec"]["domain"]["devices"]["interfaces"][0]
    assert "macAddress" not in ifc


def test_a_template_with_data_points_at_the_exported_images():
    img = {"metadata": {"name": "image-x1", "namespace": "default"}, "status": {"storageClassName": "lh-x1"}}
    _, version = hv.template_objects(vm(), "web-tpl", images={"web-root": img})
    t = {x["metadata"]["name"]: x for x in json.loads(version["spec"]["vm"]["metadata"]["annotations"][hv.VCT])}
    assert t["web-root"]["metadata"]["annotations"]["harvesterhci.io/imageId"] == "default/image-x1"
    assert t["web-root"]["spec"]["storageClassName"] == "lh-x1"


def test_export_image_manifest():
    m = hv.export_image_manifest("default", "web-root", "web-tpl-root", "harv-rep1")
    assert m["spec"]["sourceType"] == "export-from-volume" and m["spec"]["pvcName"] == "web-root"
    assert m["metadata"]["annotations"]["harvesterhci.io/storageClassName"] == "harv-rep1"


def test_eject_removes_the_cdrom_disk_volume_and_template():
    out, claim = hv.eject_patch(vm(), "iso")
    ts = out["spec"]["template"]["spec"]
    assert claim == "web-iso"
    assert "iso" not in [d["name"] for d in ts["domain"]["devices"]["disks"]]
    assert "iso" not in [v["name"] for v in ts["volumes"]]
    assert [t["metadata"]["name"] for t in json.loads(out["metadata"]["annotations"][hv.VCT])] == ["web-root"]
    with pytest.raises(ValueError, match="not a CD-ROM"):
        hv.eject_patch(vm(), "root")


def test_hotplug_and_migration_bodies():
    assert hv.hotplug_body("data", "extra") == {
        "name": "data", "disk": {"name": "data", "disk": {"bus": "scsi"}},
        "volumeSource": {"persistentVolumeClaim": {"claimName": "extra", "hotpluggable": True}}}
    with pytest.raises(ValueError):
        hv.hotplug_body("data", "extra", bus="ide")
    m = hv.migration_manifest("default", "web", "n2")
    assert m["spec"] == {"vmiName": "web", "addedNodeSelector": {"kubernetes.io/hostname": "n2"}}
    assert "addedNodeSelector" not in hv.migration_manifest("default", "web")["spec"]
    migs = [{"metadata": {"name": "a"}, "spec": {"vmiName": "web"}, "status": {"phase": "Succeeded"}},
            {"metadata": {"name": "b"}, "spec": {"vmiName": "web"}, "status": {"phase": "Running"}},
            {"metadata": {"name": "c"}, "spec": {"vmiName": "other"}, "status": {"phase": "Running"}}]
    assert [m["metadata"]["name"] for m in hv.active_migrations(migs, "web")] == ["b"]


def test_attach_cloudinit_to_a_vm_without_one():
    v = vm()
    ts = v["spec"]["template"]["spec"]
    ts["volumes"] = [x for x in ts["volumes"] if x["name"] != "cloudinitdisk"]
    ts["domain"]["devices"]["disks"] = [d for d in ts["domain"]["devices"]["disks"] if d["name"] != "cloudinitdisk"]
    out = hv.attach_cloudinit(v, "web-new")
    ci = [x for x in out["spec"]["template"]["spec"]["volumes"] if "cloudInitNoCloud" in x][0]
    assert ci == {"name": "cloudinitdisk", "cloudInitNoCloud": {"secretRef": {"name": "web-new"},
                                                                "networkDataSecretRef": {"name": "web-new"}}}
    assert {"name": "cloudinitdisk", "disk": {"bus": "virtio"}} in out["spec"]["template"]["spec"]["domain"]["devices"]["disks"]


def test_attach_cloudinit_replaces_an_inline_source():
    v = vm()
    v["spec"]["template"]["spec"]["volumes"][-1] = {"name": "cloudinitdisk", "cloudInitNoCloud": {"userData": "#cloud-config\n"}}
    out = hv.attach_cloudinit(v, "web-new")
    assert hv.cloudinit_secrets(out) == ["web-new"]
    assert len(out["spec"]["template"]["spec"]["domain"]["devices"]["disks"]) == 3


def test_guest_agent_is_added_without_losing_anything():
    import yaml
    user = "#cloud-config\npackages: [htop]\nruncmd:\n  - echo hi\nusers:\n  - name: ops\n"
    out = hv.with_guest_agent(user)
    data = yaml.safe_load(out)
    assert out.startswith("#cloud-config\n")
    assert data["packages"] == ["htop", "qemu-guest-agent"]
    assert data["runcmd"] == ["echo hi", ["systemctl", "enable", "--now", "qemu-guest-agent.service"]]
    assert data["users"] == [{"name": "ops"}]
    assert hv.with_guest_agent(out) == out                      # une seule fois
    assert "qemu-guest-agent" in hv.with_guest_agent("")
    with pytest.raises(ValueError, match="script"):
        hv.with_guest_agent("#!/bin/sh\necho hi\n")


def test_ssh_keys_are_added_once():
    import yaml
    out = hv.with_ssh_keys("#cloud-config\nssh_authorized_keys:\n  - ssh-ed25519 AAA a\n",
                           ["ssh-ed25519 AAA a", "ssh-ed25519 BBB b"])
    assert yaml.safe_load(out)["ssh_authorized_keys"] == ["ssh-ed25519 AAA a", "ssh-ed25519 BBB b"]
    assert hv.with_ssh_keys("anything", []) == "anything"
