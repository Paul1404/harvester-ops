"""v1.76.0 : routes de l'onglet Migrations VMware, étape B2 (vagues à chaud) -
état étendu (cdi_importer, precopy_interval, waves), actions de vague,
refus d'une VM déjà prise ailleurs, vue globale sur tous les clusters."""

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_forklift as hf  # noqa: E402
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

PW = "a long test password"
B64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731

KC_A, CLUSTER_A = "/kc-harvlab2", "harvlab2"
KC_B, CLUSTER_B = "/kc-harv3", "harv3"
VCENTER_URL = "https://vmwlab-vc.home.lo/sdk"


def dep(ns, name):
    return {"metadata": {"namespace": ns, "name": name}, "spec": {"replicas": 1}, "status": {"availableReplicas": 1}}


def provider(ns, name, url=VCENTER_URL, ptype="vsphere"):
    return {"metadata": {"namespace": ns, "name": name, "labels": {hf.L_MANAGED: "true"}},
            "spec": {"type": ptype, "url": url},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]}}


def wave_plan(name, vms, target="mig-b2", prov_ns="forklift", prov_name="vmwlab", ready=True, closed=False):
    m = {"namespace": "forklift", "name": name, "labels": {hf.L_MANAGED: "true", hf.L_WAVE: name}}
    if closed:
        m["annotations"] = {hf.A_CLOSED: "true"}
    spec = {"targetNamespace": target, "vms": [{"id": v} for v in vms],
            "provider": {"source": {"namespace": prov_ns, "name": prov_name},
                         "destination": {"namespace": "forklift", "name": "host"}}}
    st = {"conditions": [{"type": "Ready", "status": "True"}]} if ready else {}
    return {"apiVersion": hf.API, "kind": "Plan", "metadata": m, "spec": spec, "status": st}


def full_install_objs(kc, cluster):
    """Les lectures d'installation qu'une lecture complète de l'onglet exige
    (même patron que test_forklift_routes_175.py), pour un cluster prêt."""
    return {
        (kc, hf.K_ADDON, None, None): {"items": [
            {"metadata": {"namespace": "forklift", "name": "forklift-operator", "labels": {hf.L_MANAGED: "true"}},
             "spec": {"enabled": True}, "status": {"status": "AddonDeploySuccessful"}}]},
        (kc, hf.K_DEPLOY, "forklift", None): {"items": [dep("forklift", n)
                                                         for n in hf.COMPONENTS + (hf.OPERATOR_DEPLOY,)]},
        (kc, hf.K_DEPLOY, "cert-manager", None): {"items": [dep("cert-manager", n) for n in hf.CERT_MANAGER[1]]},
        (kc, hf.K_CONTROLLER, "forklift", hf.CONTROLLER_NAME): {"metadata": {"name": hf.CONTROLLER_NAME},
                                                                 "spec": {}},
        (kc, "serviceaccounts", "forklift", hf.INVENTORY_SA): {"metadata": {"namespace": "forklift",
                                                                             "name": hf.INVENTORY_SA}},
        (kc, "configmaps", "forklift", hf.VDDK_CM): {"data": {"image": "172.16.1.11:5005/harvops/vddk:8.0.3",
                                                               "digest": "sha256:" + "a" * 64,
                                                               "archive": "VMware-vix-disklib-8.0.3-1.x86_64.tar.gz",
                                                               "pushed_at": "2026-09-28T20:00:00Z"}},
        (kc, "settings.harvesterhci.io", None, "containerd-registry"): {"value": ""},
        (kc, hf.PROV_CLUSTER[0], hf.PROV_CLUSTER[1], hf.PROV_CLUSTER[2]): {"metadata": {"name": "local"}},
        (kc, wapp._VM_SOURCE_KIND, None, None): {"items": []},
    }


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(wapp, "AUTH_OPEN_ALLOWED", False)
    monkeypatch.setattr(wapp, "HTPASSWD_PATH", tmp_path / "none")
    monkeypatch.setattr(wapp, "ROLES_PATH", tmp_path / "none.yaml")
    monkeypatch.setattr(wapp, "_roles_cache", {"mtime": None, "data": None})
    monkeypatch.setattr(wapp, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(wapp, "_ACCOUNTS", {"store": None})
    monkeypatch.setattr(wapp, "_LOCAL_SESSIONS", acc.LocalSessions())
    monkeypatch.setattr(wapp, "load_config", lambda: {"clusters": [
        {"name": CLUSTER_A, "kubeconfig": KC_A}, {"name": CLUSTER_B, "kubeconfig": KC_B}]})
    reachable = {KC_A: True, KC_B: True}
    monkeypatch.setattr(wapp, "_cluster_reachable", lambda kc, timeout=2.0: reachable.get(kc))
    wapp._FK_INVENTORY_CACHE.clear()
    wapp._FK_GLOBAL_CACHE.clear()
    wapp._accounts().create("adm", PW, "admin")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": [], "reachable": reachable}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        w["actions"].append((label, cmd, spec, tool))
        if after:
            after()

        class Run:
            id = "fk0000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)

    objs = {}
    objs.update(full_install_objs(KC_A, CLUSTER_A))
    # cluster A : un fournisseur vCenter, une vague ouverte (vague-a, vm-9), aucune vague fermée
    objs[(KC_A, hf.K_PROVIDER, None, None)] = {"items": [provider("forklift", "vmwlab")]}
    objs[(KC_A, hf.K_PROVIDER, "forklift", "vmwlab")] = provider("forklift", "vmwlab")
    objs[(KC_A, hf.K_PLAN, None, None)] = {"items": [wave_plan("vague-a", ["vm-9"])]}
    objs[(KC_A, hf.K_MIGRATION, None, None)] = {"items": []}
    objs[(KC_A, hf.K_DEPLOY, "harvester-system", hf.CDI_OPERATOR[1])] = None

    # cluster B : même vCenter (même hôte), une vague ouverte (vague-b, vm-18)
    objs[(KC_B, hf.K_PROVIDER, None, None)] = {"items": [provider("forklift", "vmwlab")]}
    objs[(KC_B, hf.K_PLAN, None, None)] = {"items": [wave_plan("vague-b", ["vm-18"])]}
    objs[(KC_B, hf.K_MIGRATION, None, None)] = {"items": []}

    def kj(kc, verb, kind, *a, **k):
        ns = a[a.index("-n") + 1] if "-n" in a else None
        name = a[0] if a and not a[0].startswith("-") else None
        for key in ((kc, kind, ns, name), (kc, kind, ns, None), (kc, kind, None, None)):
            if key in objs:
                o = objs[key]
                if name and key[3] is None and isinstance(o, dict) and "items" in o:
                    return None
                return json.loads(json.dumps(o)) if o is not None else None
        return None
    monkeypatch.setattr(wapp, "_kubectl_json", kj)
    w["objs"] = objs
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def wave_spec(**over):
    s = {"name": "vague-c", "target_namespace": "mig-b2", "provider": {"namespace": "forklift", "name": "vmwlab"},
         "vms": ["vm-30"], "networks": [], "storages": [], "skip_conversion": False,
         "preserve_static_ips": False}
    s.update(over)
    return s


# --- GET /api/forklift/<cluster> : cdi_importer, precopy_interval, waves ----

def test_the_tab_gains_cdi_importer_precopy_interval_and_waves(world):
    with wapp.app.test_client() as c:
        d = c.get(f"/api/forklift/{CLUSTER_A}", headers=auth("eye")).get_json()
    assert d["precopy_interval"] == 60          # défaut, aucun ForkliftController.spec
    assert d["cdi_importer"]["kind"] == "other"  # aucun cdi-operator lu (déploiement absent)
    [w] = d["waves"]
    assert w["name"] == "vague-a" and w["state"] == "ready"
    assert w["vms"][0]["id"] == "vm-9"


def test_a_plan_without_the_console_labels_is_not_a_wave(world):
    plain = {"apiVersion": hf.API, "kind": "Plan",
             "metadata": {"namespace": "forklift", "name": "someone-elses-plan"},
             "spec": {"targetNamespace": "x", "vms": [], "provider": {"source": {}, "destination": {}}},
             "status": {}}
    world["objs"][(KC_A, hf.K_PLAN, None, None)] = {"items": [wave_plan("vague-a", ["vm-9"]), plain]}
    with wapp.app.test_client() as c:
        d = c.get(f"/api/forklift/{CLUSTER_A}", headers=auth("eye")).get_json()
    assert [w["name"] for w in d["waves"]] == ["vague-a"]


def test_the_wave_targets_are_read_only_when_asked(world):
    """?targets=1 (onglet Vagues) : réseaux de VM, classes (hors internes),
    classe par défaut et namespaces ; sans lui, aucune de ces lectures."""
    objs = world["objs"]
    objs[(KC_A, "network-attachment-definitions.k8s.cni.cncf.io", None, None)] = {"items": [
        {"metadata": {"namespace": "mig-b2", "name": "vlan1"}},
        {"metadata": {"namespace": "default", "name": "lab-net"}}]}
    objs[(KC_A, "storageclasses", None, None)] = {"items": [
        {"metadata": {"name": "harvester-longhorn",
                      "annotations": {"storageclass.kubernetes.io/is-default-class": "true"}}},
        {"metadata": {"name": "harv-rep1"}},
        {"metadata": {"name": "vmstate-persistence"}, "parameters": {"harvesterhci.io/isInternalStorageClass": "true"}}]}
    objs[(KC_A, "namespaces", None, None)] = {"items": [{"metadata": {"name": "default"}},
                                                        {"metadata": {"name": "mig-b2"}}]}
    with wapp.app.test_client() as c:
        plain = c.get(f"/api/forklift/{CLUSTER_A}", headers=auth("eye")).get_json()
        d = c.get(f"/api/forklift/{CLUSTER_A}?targets=1", headers=auth("eye")).get_json()
    assert "nads" not in plain and "classes" not in plain and "namespaces" not in plain
    assert d["nads"] == ["default/lab-net", "mig-b2/vlan1"]
    assert d["classes"] == ["harv-rep1", "harvester-longhorn"]
    assert d["default_class"] == "harvester-longhorn"
    assert d["namespaces"] == ["default", "mig-b2"]
    assert d["waves"][0]["name"] == "vague-a"


# --- wave-apply : validation, provider, refus multi-cluster -----------------

def test_wave_apply_validates_the_spec_before_touching_the_cluster(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-apply", json={"spec": wave_spec(vms=[])},
                   headers=auth("adm"))
    assert r.status_code == 400 and "VM" in r.get_json()["error"]
    assert world["actions"] == []


def test_wave_apply_refuses_an_unknown_provider(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-apply",
                   json={"spec": wave_spec(provider={"namespace": "forklift", "name": "nope"})},
                   headers=auth("adm"))
    assert r.status_code == 404
    assert world["actions"] == []


def test_wave_apply_refuses_a_vm_already_taken_on_another_cluster(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-apply", json={"spec": wave_spec(vms=["vm-18"])},
                   headers=auth("adm"))
    assert r.status_code == 409
    d = r.get_json()
    assert d["error"] == f"vm-18 is already in wave vague-b on cluster {CLUSTER_B}"
    assert d["skipped"] == []
    assert world["actions"] == []


def test_wave_apply_lets_a_same_cluster_duplicate_through_to_the_tool(world):
    """Une VM déjà prise sur CE cluster n'est pas refusée par la route : c'est
    l'outil qui la refuse (« close that wave first »)."""
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-apply", json={"spec": wave_spec(vms=["vm-9"])},
                   headers=auth("adm"))
    assert r.status_code == 202
    label, cmd, spec, tool = world["actions"][0]
    assert label == "forklift:wave-apply:vague-c" and tool == "harvester-forklift"
    assert spec["vms"] == ["vm-9"]
    assert cmd[2] == "wave-apply"


def test_wave_apply_reports_unreachable_clusters_as_skipped_without_blocking(world):
    world["reachable"][KC_B] = False
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-apply", json={"spec": wave_spec(vms=["vm-18"])},
                   headers=auth("adm"))
    # vm-18 n'est plus visible comme prise (harv3 injoignable) : la vague passe
    assert r.status_code == 202
    assert r.get_json()["skipped"] == [CLUSTER_B]


def test_wave_apply_writes_are_for_administrators(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-apply", json={"spec": wave_spec()}, headers=auth("eye"))
    assert r.status_code == 403
    assert world["actions"] == []


# --- autres actions de vague : ligne de commande et libellé -----------------

def test_wave_start_builds_the_command_line(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-start", json={"wave": "vague-a"}, headers=auth("adm"))
    assert r.status_code == 202
    label, cmd, spec, _ = world["actions"][0]
    assert label == "forklift:wave-start:vague-a" and spec is None
    assert cmd[2] == "wave-start" and cmd[cmd.index("--wave") + 1] == "vague-a"


def test_wave_start_validates_the_wave_name(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-start", json={"wave": "Not Valid!"}, headers=auth("adm"))
    assert r.status_code == 400
    assert world["actions"] == []


def test_wave_cutover_defaults_to_now_and_accepts_a_future_time(world):
    with wapp.app.test_client() as c:
        r1 = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-cutover", json={"wave": "vague-a"}, headers=auth("adm"))
        r2 = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-cutover",
                    json={"wave": "vague-a", "at": "2099-01-01T00:00:00Z"}, headers=auth("adm"))
    assert r1.status_code == 202 and r2.status_code == 202
    label1, cmd1, _, _ = world["actions"][0]
    label2, cmd2, _, _ = world["actions"][1]
    assert label1 == label2 == "forklift:wave-cutover:vague-a"
    assert "--at" not in cmd1
    assert cmd2[cmd2.index("--at") + 1] == "2099-01-01T00:00:00Z"


def test_wave_cutover_refuses_a_bad_time(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-cutover",
                   json={"wave": "vague-a", "at": "not a date"}, headers=auth("adm"))
    assert r.status_code == 400
    r2_client = wapp.app.test_client()
    with r2_client as c:
        far_past = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-cutover",
                          json={"wave": "vague-a", "at": "2000-01-01T00:00:00Z"}, headers=auth("adm"))
    assert far_past.status_code == 400
    assert world["actions"] == []


def test_wave_rollback_builds_repeated_vm_flags_and_validates_ids(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-rollback",
                   json={"wave": "vague-a", "vms": ["vm-9", "vm-11"]}, headers=auth("adm"))
        bad = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-rollback",
                     json={"wave": "vague-a", "vms": ["not an id!"]}, headers=auth("adm"))
    assert r.status_code == 202 and bad.status_code == 400
    label, cmd, spec, _ = world["actions"][0]
    assert label == "forklift:wave-rollback:vague-a" and spec is None
    idx = [i for i, x in enumerate(cmd) if x == "--vm"]
    assert [cmd[i + 1] for i in idx] == ["vm-9", "vm-11"]


def test_wave_rollback_without_vms_rolls_back_the_whole_wave(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-rollback", json={"wave": "vague-a"}, headers=auth("adm"))
    assert r.status_code == 202
    _, cmd, _, _ = world["actions"][0]
    assert "--vm" not in cmd


def test_wave_close_with_and_without_clean_snapshots(world):
    with wapp.app.test_client() as c:
        r1 = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-close", json={"wave": "vague-a"}, headers=auth("adm"))
        r2 = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-close",
                    json={"wave": "vague-a", "clean_snapshots": True}, headers=auth("adm"))
    assert r1.status_code == 202 and r2.status_code == 202
    _, cmd1, _, _ = world["actions"][0]
    _, cmd2, _, _ = world["actions"][1]
    assert "--clean-snapshots" not in cmd1
    assert "--clean-snapshots" in cmd2


def test_wave_delete_builds_the_command_line(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/wave-delete", json={"wave": "vague-a"}, headers=auth("adm"))
    assert r.status_code == 202
    label, cmd, _, _ = world["actions"][0]
    assert label == "forklift:wave-delete:vague-a"
    assert cmd[2] == "wave-delete" and cmd[cmd.index("--wave") + 1] == "vague-a"


# --- Préparation étendue : importeur CDI, intervalle des copies -------------

def test_cdi_importer_upstream_with_and_without_a_mirror(world):
    with wapp.app.test_client() as c:
        r1 = c.post(f"/api/forklift/{CLUSTER_A}/do/cdi-importer", json={"mode": "upstream"}, headers=auth("adm"))
        r2 = c.post(f"/api/forklift/{CLUSTER_A}/do/cdi-importer",
                    json={"mode": "upstream", "image": "quay.io/kubevirt/cdi-importer:v1.60.0"},
                    headers=auth("adm"))
    assert r1.status_code == 202 and r2.status_code == 202
    _, cmd1, _, _ = world["actions"][0]
    _, cmd2, _, _ = world["actions"][1]
    assert "--upstream" in cmd1 and "--image" not in cmd1
    assert cmd2[cmd2.index("--image") + 1] == "quay.io/kubevirt/cdi-importer:v1.60.0"


def test_cdi_importer_original_and_bad_mode(world):
    with wapp.app.test_client() as c:
        r = c.post(f"/api/forklift/{CLUSTER_A}/do/cdi-importer", json={"mode": "original"}, headers=auth("adm"))
        bad = c.post(f"/api/forklift/{CLUSTER_A}/do/cdi-importer", json={"mode": "nope"}, headers=auth("adm"))
        missing_image = c.post(f"/api/forklift/{CLUSTER_A}/do/cdi-importer",
                               json={"mode": "upstream", "image": "not-an-image"}, headers=auth("adm"))
    assert r.status_code == 202 and bad.status_code == 400 and missing_image.status_code == 400
    _, cmd, _, _ = world["actions"][0]
    assert "--original" in cmd
    assert len(world["actions"]) == 1


def test_precopy_interval_bounds_and_command_line(world):
    with wapp.app.test_client() as c:
        ok = c.post(f"/api/forklift/{CLUSTER_A}/do/precopy-interval", json={"minutes": 5}, headers=auth("adm"))
        too_low = c.post(f"/api/forklift/{CLUSTER_A}/do/precopy-interval", json={"minutes": 4}, headers=auth("adm"))
        too_high = c.post(f"/api/forklift/{CLUSTER_A}/do/precopy-interval", json={"minutes": 1441},
                          headers=auth("adm"))
    assert ok.status_code == 202 and too_low.status_code == 400 and too_high.status_code == 400
    label, cmd, spec, _ = world["actions"][0]
    assert label == "forklift:precopy-interval" and spec is None
    assert cmd[2] == "precopy-interval" and cmd[-1] == "5"
    assert len(world["actions"]) == 1


# --- vue globale --------------------------------------------------------------

def test_forklift_global_lists_every_cluster_and_flags_the_unreachable_one(world):
    world["reachable"][KC_B] = False
    with wapp.app.test_client() as c:
        d = c.get("/api/forklift-global", headers=auth("eye")).get_json()
    rows = {r["cluster"]: r for r in d["clusters"]}
    assert set(rows) == {CLUSTER_A, CLUSTER_B}
    assert rows[CLUSTER_A]["reachable"] is True
    [w] = rows[CLUSTER_A]["waves"]
    assert w["name"] == "vague-a"
    assert rows[CLUSTER_B] == {"cluster": CLUSTER_B, "reachable": False, "forklift_ready": False,
                               "cdi_importer_kind": None, "providers": [], "waves": []}


def test_forklift_global_is_viewer_readable(world):
    with wapp.app.test_client() as c:
        r = c.get("/api/forklift-global", headers=auth("eye"))
    assert r.status_code == 200


def test_forklift_global_is_cached_and_expires(world):
    with wapp.app.test_client() as c:
        first = c.get("/api/forklift-global", headers=auth("eye")).get_json()
        world["objs"][(KC_A, hf.K_PLAN, None, None)] = {"items": []}     # la vague de A disparaît
        still_cached = c.get("/api/forklift-global", headers=auth("eye")).get_json()
        # expire artificiellement l'entrée (sans dépendre d'un vrai délai) :
        # même personne, même identité de cluster -> une seule clé en cache
        [key] = list(wapp._FK_GLOBAL_CACHE.keys())
        ts, body = wapp._FK_GLOBAL_CACHE[key]
        wapp._FK_GLOBAL_CACHE[key] = (ts - wapp._FK_GLOBAL_TTL - 1, body)
        fresh = c.get("/api/forklift-global", headers=auth("eye")).get_json()
    a_first = next(r for r in first["clusters"] if r["cluster"] == CLUSTER_A)
    a_cached = next(r for r in still_cached["clusters"] if r["cluster"] == CLUSTER_A)
    a_fresh = next(r for r in fresh["clusters"] if r["cluster"] == CLUSTER_A)
    assert len(a_first["waves"]) == len(a_cached["waves"]) == 1
    assert a_fresh["waves"] == []
