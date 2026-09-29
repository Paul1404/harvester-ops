"""v1.76.0 : Forklift, étape B2, bibliothèque des vagues à chaud.

Les objets des fixtures forklift_b2_*_176.json ont été relevés sur le banc
(harvlab2 + vmwlab, namespace mig-b2) après deux vraies migrations à chaud :
vague-1 (avec conversion, trois essais ratés avant la réussite : le dernier
raté, vague-1-m3, a échoué faute de VMware Tools) et vague-2 (copie brute,
skipGuestConversion)."""

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))
import hv_forklift as hf  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def fixture(kind):
    return json.loads((FIX / f"forklift_b2_{kind}_176.json").read_text())


PLANS = {p["metadata"]["name"]: p for p in fixture("plans")["items"]}
MIGRATIONS = {m["metadata"]["name"]: m for m in fixture("migrations")["items"]}
AFTER = datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)   # après les deux vagues


def spec(**over):
    s = {"name": "wave-1", "target_namespace": "mig-b2", "provider": {"namespace": "default", "name": "vmwlab"},
         "vms": ["vm-16", "vm-18"],
         "networks": [{"source": "network-13", "destination": "default/lab-net"},
                      {"source": "network-14", "destination": "pod"}],
         "storages": [{"source": "datastore-12", "storage_class": "harv-rep1"}],
         "skip_conversion": False, "compat_mode": True, "preserve_static_ips": True}
    s.update(over)
    return s


# --- nom d'une vague ---------------------------------------------------------

def test_a_wave_name_is_rfc1123_and_40_characters_at_most():
    assert hf.check_wave_name(" vague-1 ") == "vague-1"
    assert hf.check_wave_name("a" * 40) == "a" * 40
    for bad in ("a" * 41, "Vague", "-x", "x-", "x_y", "", None):
        with pytest.raises(ValueError, match="wave"):
            hf.check_wave_name(bad)


def test_the_labels_and_annotations_of_the_console():
    assert hf.L_WAVE == "harvester-ops.io/wave"
    assert hf.A_ROLLED_BACK == "harvester-ops.io/rolled-back"
    assert hf.A_CLOSED == "harvester-ops.io/closed"


# --- objets d'une vague ------------------------------------------------------

def test_the_wave_objects_match_what_worked_on_the_bench():
    net, sto, plan = hf.wave_manifests(spec())
    labels = {hf.L_MANAGED: "true", hf.L_WAVE: "wave-1"}
    prov = {"source": {"namespace": "default", "name": "vmwlab"}, "destination": {"namespace": "forklift", "name": "host"}}
    assert net["kind"] == "NetworkMap" and net["apiVersion"] == hf.API
    assert net["metadata"] == {"name": "wave-1-net", "namespace": "forklift", "labels": labels}
    assert net["spec"]["provider"] == prov
    assert net["spec"]["map"] == [
        {"source": {"id": "network-13"}, "destination": {"type": "multus", "namespace": "default", "name": "lab-net"}},
        {"source": {"id": "network-14"}, "destination": {"type": "pod"}}]
    assert sto["kind"] == "StorageMap" and sto["metadata"]["name"] == "wave-1-sto" and sto["metadata"]["labels"] == labels
    assert sto["spec"]["map"] == [{"source": {"id": "datastore-12"}, "destination": {"storageClass": "harv-rep1"}}]
    assert sto["spec"]["provider"] == prov
    assert plan["kind"] == "Plan" and plan["metadata"] == {"name": "wave-1", "namespace": "forklift", "labels": labels}
    s = plan["spec"]
    assert s["warm"] is True and s["targetNamespace"] == "mig-b2"
    assert s["provider"] == prov
    assert s["map"] == {"network": {"namespace": "forklift", "name": "wave-1-net"},
                        "storage": {"namespace": "forklift", "name": "wave-1-sto"}}
    assert s["vms"] == [{"id": "vm-16"}, {"id": "vm-18"}]
    assert s["skipGuestConversion"] is False and s["preserveStaticIPs"] is True
    # la conversion garde le mode de compatibilité par défaut de Forklift
    assert "useCompatibilityMode" not in s
    # même réseau et même classe que les objets relevés sur le banc
    real_net = fixture("networkmap")["items"][0]["spec"]["map"][0]
    real_sto = fixture("storagemap")["items"][0]["spec"]["map"][0]
    assert net["spec"]["map"][0] == real_net and sto["spec"]["map"][0] == real_sto


def test_a_raw_copy_carries_the_compatibility_mode_as_vague_2_did():
    plan = hf.wave_manifests(spec(skip_conversion=True, compat_mode=False))[2]
    real = PLANS["vague-2"]["spec"]
    assert plan["spec"]["skipGuestConversion"] is True and plan["spec"]["useCompatibilityMode"] is False
    assert (real["skipGuestConversion"], real["useCompatibilityMode"]) == (True, False)
    assert hf.wave_manifests(spec(skip_conversion=True, compat_mode=True))[2]["spec"]["useCompatibilityMode"] is True


@pytest.mark.parametrize("over,match", [
    ({"vms": []}, "at least one VM"),
    ({"vms": ["vm-16", "vm-16"]}, "twice"),
    ({"vms": ["vm 16"]}, "VM id"),
    ({"name": "Wave"}, "wave"),
    ({"target_namespace": "Bad_NS"}, "target namespace"),
    ({"provider": {"namespace": "default", "name": ""}}, "provider"),
    ({"networks": [{"source": "network-13", "destination": "lab-net"}]}, "network destination"),
    ({"networks": [{"source": "network-13", "destination": "default/Lab"}]}, "network destination"),
    ({"networks": [{"source": "network-13", "destination": "pod"}, {"source": "network-13", "destination": "pod"}]}, "twice"),
    ({"networks": [{"source": "", "destination": "pod"}]}, "network source"),
    ({"storages": [{"source": "datastore-12", "storage_class": ""}]}, "storage class"),
    ({"storages": [{"source": "datastore-12", "storage_class": "a"}, {"source": "datastore-12", "storage_class": "b"}]}, "twice"),
])
def test_a_wave_that_cannot_work_is_refused(over, match):
    with pytest.raises(ValueError, match=match):
        hf.wave_manifests(spec(**over))


def test_a_migration_and_its_cutover():
    m = hf.migration_manifest("wave-1", 3)
    assert m == {"apiVersion": hf.API, "kind": "Migration",
                 "metadata": {"name": "wave-1-m3", "namespace": "forklift",
                              "labels": {hf.L_MANAGED: "true", hf.L_WAVE: "wave-1"}},
                 "spec": {"plan": {"namespace": "forklift", "name": "wave-1"}}}
    for bad in (0, -1, "x", True):
        with pytest.raises(ValueError, match="migration number"):
            hf.migration_manifest("wave-1", bad)
    now = datetime(2026, 9, 29, 19, 30, tzinfo=timezone.utc)
    assert hf.cutover_patch("2026-09-29T21:32:27+02:00", now=now) == {"spec": {"cutover": "2026-09-29T19:32:27Z"}}
    assert hf.cutover_patch(None, now=now) == {"spec": {"cutover": "2026-09-29T19:30:00Z"}}
    assert hf.cutover_patch("2026-09-29T19:26:00Z", now=now)["spec"]["cutover"] == "2026-09-29T19:26:00Z"   # 4 min
    with pytest.raises(ValueError, match="past"):
        hf.cutover_patch("2026-09-29T19:24:00Z", now=now)
    for bad in ("tomorrow", "2026-09-29 19:30", "2026-09-29T19:30:00"):
        with pytest.raises(ValueError, match="RFC 3339"):
            hf.cutover_patch(bad, now=now)


# --- état d'une vague (objets réels) -------------------------------------------

def test_vague_1_read_from_the_real_objects_succeeded():
    st = hf.wave_state(PLANS["vague-1"], list(MIGRATIONS.values()), now=AFTER)
    assert (st["name"], st["target_namespace"], st["state"], st["migration"]) == ("vague-1", "mig-b2", "succeeded", "vague-1-m4")
    assert st["cutover"] == "2026-09-29T19:32:27Z" and st["next_precopy"] is None
    (vm,) = st["vms"]
    assert (vm["id"], vm["name"], vm["phase"], vm["error"], vm["rolled_back"]) == ("vm-16", "vmwlab-src-1", "Succeeded", "", False)
    assert vm["precopies"] == 3
    # la dernière copie (celle de la bascule) n'a jamais de fin : on montre la dernière finie
    assert vm["last_precopy"] == {"start": "2026-09-29T19:31:02Z", "end": "2026-09-29T19:32:04Z", "seconds": 62}
    assert vm["next_precopy"] is None
    # vu en réel : une étape finie peut garder 0/1 (VirtualMachineCreation)
    assert vm["step_name"] == "VirtualMachineCreation" and vm["progress"] == {"done": 1, "total": 1}


def test_a_vm_that_failed_is_reported_completed_by_forklift_but_read_as_failed():
    """vague-1-m3, le vrai échec : phase « Completed » ET condition Failed,
    raison dans error.reasons, étape Cutover sans phase."""
    m3 = MIGRATIONS["vague-1-m3"]
    assert m3["status"]["vms"][0]["phase"] == "Completed"
    plan = copy.deepcopy(PLANS["vague-1"])
    plan["status"]["migration"]["vms"] = copy.deepcopy(m3["status"]["vms"])
    plan["status"]["conditions"] = [c for c in plan["status"]["conditions"] if c["type"] == "Ready"]
    st = hf.wave_state(plan, [m3], now=AFTER)
    assert st["state"] == "failed" and st["migration"] == "vague-1-m3"
    (vm,) = st["vms"]
    assert vm["phase"] == "Failed" and vm["step_name"] == "Cutover" and vm["step"] == "final copy"
    assert "VMware Tools is not running" in vm["error"]
    assert "VMware Tools is not running" in st["message"]
    assert vm["next_precopy"] is None


def test_vague_2_raw_copy_has_no_image_conversion_step():
    st = hf.wave_state(PLANS["vague-2"], list(MIGRATIONS.values()), now=AFTER)
    assert st["state"] == "succeeded" and st["migration"] == "vague-2-m1"
    names = [s["name"] for s in PLANS["vague-2"]["status"]["migration"]["vms"][0]["pipeline"]]
    assert names == ["Initialize", "DiskTransfer", "Cutover", "VirtualMachineCreation"]
    assert st["vms"][0]["precopies"] == 2 and st["vms"][0]["last_precopy"]["seconds"] == 66


def copying_objects(cutover=None):
    """vague-1-m4 remis en cours de copie : pas de condition finale, copie
    des disques en cours, prochaine copie prévue."""
    m = copy.deepcopy(MIGRATIONS["vague-1-m4"])
    m["spec"].pop("cutover")
    if cutover:
        m["spec"]["cutover"] = cutover
    m["status"]["conditions"] = [c for c in m["status"]["conditions"] if c["type"] == "Ready"]
    m["status"].pop("completed")
    vm = m["status"]["vms"][0]
    for k in ("completed", "conditions"):
        vm.pop(k)
    vm["phase"] = "CopyDisks"
    pipe = vm["pipeline"]
    pipe[1]["phase"] = "Running"
    pipe[1].pop("completed")
    pipe[1]["progress"] = {"completed": 4096, "total": 10240}
    for s in pipe[2:]:
        s["phase"] = "Pending"
        for k in ("started", "completed"):
            s.pop(k, None)
    vm["warm"]["precopies"] = vm["warm"]["precopies"][:2]
    plan = copy.deepcopy(PLANS["vague-1"])
    plan["status"]["conditions"] = [c for c in plan["status"]["conditions"] if c["type"] == "Ready"] + [
        {"type": "Executing", "status": "True", "category": "Advisory", "message": "The plan is EXECUTING."}]
    plan["status"]["migration"]["vms"] = [copy.deepcopy(vm)]
    plan["status"]["migration"].pop("completed")
    return plan, m


def test_a_wave_being_copied_and_its_next_precopy():
    plan, m = copying_objects()
    now = datetime(2026, 9, 29, 19, 33, tzinfo=timezone.utc)
    st = hf.wave_state(plan, [m], now=now)
    assert st["state"] == "copying" and st["cutover"] is None
    (vm,) = st["vms"]
    assert vm["phase"] == "CopyDisks" and vm["step_name"] == "DiskTransfer" and vm["step"] == "copying disks"
    assert vm["progress"] == {"done": 4096, "total": 10240} and vm["precopies"] == 2
    assert vm["next_precopy"] == "2026-09-29T19:37:04Z" == st["next_precopy"]


def failed_before_cutover_objects():
    """vague-1-m4 en échec pendant DiskTransfer (VDDK), avant toute bascule :
    ni `spec.cutover` ni l'étape Cutover n'ont jamais commencé."""
    plan, m = copying_objects()
    m["status"]["conditions"].append({"type": "Failed", "status": "True", "category": "Advisory",
                                      "message": "The migration has FAILED."})
    vm = m["status"]["vms"][0]
    vm["error"] = {"reasons": ["Unable to connect to vddk data source"]}
    vm["pipeline"][1]["error"] = {"reasons": ["Unable to connect to vddk data source"]}
    plan["status"]["migration"]["vms"] = [copy.deepcopy(vm)]
    return plan, m


def test_a_wave_failed_before_any_switchover_has_not_started_its_cutover():
    """Le bug corrigé : un échec en DiskTransfer, avant toute bascule, ne
    doit jamais se lire comme une bascule amorcée (sinon un rollback serait
    proposé alors qu'il n'y a rien à défaire)."""
    plan, m = failed_before_cutover_objects()
    st = hf.wave_state(plan, [m], now=AFTER)
    assert st["state"] == "failed"
    assert st["cutover_started"] is False
    (vm,) = st["vms"]
    assert vm["cutover_started"] is False
    assert "vddk" in vm["error"]


def test_a_succeeded_wave_did_start_its_cutover():
    st = hf.wave_state(PLANS["vague-1"], list(MIGRATIONS.values()), now=AFTER)
    assert st["cutover_started"] is True
    assert st["vms"][0]["cutover_started"] is True


def test_a_scheduled_cutover_then_a_cutover_under_way():
    plan, m = copying_objects(cutover="2026-09-29T19:40:00Z")
    before = hf.wave_state(plan, [m], now=datetime(2026, 9, 29, 19, 33, tzinfo=timezone.utc))
    assert before["state"] == "cutover-scheduled" and before["cutover"] == "2026-09-29T19:40:00Z"
    assert before["next_precopy"] == "2026-09-29T19:37:04Z"
    after = hf.wave_state(plan, [m], now=datetime(2026, 9, 29, 19, 41, tzinfo=timezone.utc))
    assert after["state"] == "cutting-over"


def test_the_current_migration_is_the_latest_of_its_plan_only():
    plan, m = copying_objects()
    other = copy.deepcopy(MIGRATIONS["vague-2-m1"])      # autre plan, plus récente
    older = copy.deepcopy(MIGRATIONS["vague-1-m3"])      # même plan, plus ancienne, échouée
    st = hf.wave_state(plan, [other, older, m], now=AFTER)
    assert st["migration"] == "vague-1-m4" and st["state"] == "copying"
    # à date égale, le numéro décide (m10 après m9)
    a, b = copy.deepcopy(m), copy.deepcopy(m)
    a["metadata"]["name"], b["metadata"]["name"] = "vague-1-m9", "vague-1-m10"
    assert hf.wave_state(plan, [b, a], now=AFTER)["migration"] == "vague-1-m10"


def plain_plan(**status):
    p = {"apiVersion": hf.API, "kind": "Plan",
         "metadata": {"name": "wave-1", "namespace": "forklift", "labels": {hf.L_MANAGED: "true", hf.L_WAVE: "wave-1"}},
         "spec": {"targetNamespace": "apps", "vms": [{"id": "vm-16"}, {"id": "vm-18"}]}}
    if status:
        p["status"] = status
    return p


def test_a_plan_not_started_is_ready_or_pending_or_invalid():
    ready = plain_plan(conditions=[{"type": "Ready", "status": "True", "category": "Required"}])
    st = hf.wave_state(ready, [], now=AFTER)
    assert st["state"] == "ready" and [v["id"] for v in st["vms"]] == ["vm-16", "vm-18"]
    assert st["vms"][0]["phase"] == "" and st["vms"][0]["precopies"] == 0 and st["vms"][0]["last_precopy"] is None
    assert hf.wave_state(plain_plan(), [], now=AFTER)["state"] == "pending"
    bad = plain_plan(conditions=[{"type": "VMNotFound", "status": "True", "category": "Critical",
                                  "message": "VMs not found."}])
    st = hf.wave_state(bad, [], now=AFTER)
    assert st["state"] == "invalid" and st["message"] == "VMs not found."


def test_rolled_back_and_closed_waves():
    p = plain_plan(conditions=[{"type": "Ready", "status": "True"}])
    p["metadata"]["annotations"] = {hf.A_ROLLED_BACK: "vm-16"}
    st = hf.wave_state(p, [], now=AFTER)
    assert st["state"] == "ready" and [v["rolled_back"] for v in st["vms"]] == [True, False]
    p["metadata"]["annotations"][hf.A_ROLLED_BACK] = "vm-18, vm-16"
    assert hf.wave_state(p, [], now=AFTER)["state"] == "rolled-back"
    p["metadata"]["annotations"][hf.A_CLOSED] = "2026-09-29T20:00:00Z"
    assert hf.wave_state(p, [], now=AFTER)["state"] == "closed"
    q = plain_plan()
    q["spec"]["archived"] = True
    assert hf.wave_state(q, [], now=AFTER)["state"] == "closed"


def test_a_plan_alone_is_read_from_its_own_status():
    """Sans les Migrations (vue globale), l'état vient du plan."""
    assert hf.wave_state(PLANS["vague-1"], [], now=AFTER)["state"] == "succeeded"
    plan, _ = copying_objects()
    assert hf.wave_state(plan, [], now=AFTER)["state"] == "copying"
    failed = plain_plan(conditions=[{"type": "Failed", "status": "True", "message": "The plan execution has FAILED."}],
                        migration={"started": "2026-09-29T19:18:03Z", "completed": "2026-09-29T19:20:34Z"})
    assert hf.wave_state(failed, [], now=AFTER)["state"] == "failed"


def test_a_canceled_migration_is_a_failure():
    plan, m = copying_objects()
    m["status"]["conditions"].append({"type": "Canceled", "status": "True", "message": "The migration has been CANCELED."})
    st = hf.wave_state(plan, [m], now=AFTER)
    assert st["state"] == "failed" and "CANCELED" in st["message"]


# --- inventaire enrichi et refus d'une VM --------------------------------------

def inv_vm(**over):
    v = {"id": "vm-16", "name": "vmwlab-src-1", "powerState": "poweredOn", "changeTrackingEnabled": True,
         "guestNameFromVmwareTools": "Debian GNU/Linux 12 (64-bit)", "ipAddress": "172.16.2.81",
         "snapshot": {"kind": "VirtualMachineSnapshot", "id": "snapshot-2004"},
         "uuid": "4231f0d5-8d0e-5a8b-0c4e-2b8f1d2a9c11"}
    v.update(over)
    return v


def test_the_inventory_rows_gain_tools_snapshot_and_uuid():
    (r,) = hf.inventory_rows("vms", [inv_vm()])
    assert (r["tools"], r["snapshot"], r["uuid"]) == (True, "snapshot-2004", "4231f0d5-8d0e-5a8b-0c4e-2b8f1d2a9c11")
    assert r["id"] == "vm-16" and r["cbt"] is True        # les clés de B1 restent
    (r,) = hf.inventory_rows("vms", [inv_vm(guestNameFromVmwareTools="", ipAddress="", snapshot=None, uuid=None)])
    assert (r["tools"], r["snapshot"], r["uuid"]) == (False, "", "")
    (r,) = hf.inventory_rows("vms", [inv_vm(guestNameFromVmwareTools="", ipAddress="10.0.0.5")])
    assert r["tools"] is True
    # relevé réel de B1 (détail=1) : pas ces champs, aucune erreur
    rows = hf.inventory_rows("vms", json.loads((FIX / "forklift_inventory_vms_175.json").read_text()))
    assert all(r["tools"] is False and r["snapshot"] == "" for r in rows)


def test_why_a_vm_cannot_join_a_warm_wave():
    row = lambda **o: hf.inventory_rows("vms", [inv_vm(**o)])[0]  # noqa: E731
    assert hf.vm_warm_blockers(row()) == []
    assert hf.vm_warm_blockers(row(changeTrackingEnabled=False)) == ["Changed Block Tracking is off"]
    # vu en réel (vague-1-m3) : allumée sans outils, la bascule échoue
    assert hf.vm_warm_blockers(row(guestNameFromVmwareTools="", ipAddress="")) == [
        "VMware Tools are not running: the switchover cannot shut the source down"]
    # éteinte, la bascule n'a rien à arrêter
    assert hf.vm_warm_blockers(row(powerState="poweredOff", guestNameFromVmwareTools="", ipAddress="")) == []
    assert len(hf.vm_warm_blockers(row(changeTrackingEnabled=False, guestNameFromVmwareTools="", ipAddress=""))) == 2


# --- VMs prises en charge (vue globale) ---------------------------------------

def test_the_host_of_a_provider():
    assert hf.provider_host({"spec": {"url": "https://VC.Home.lo/sdk"}}) == "vc.home.lo"
    assert hf.provider_host({"spec": {"url": "https://172.16.2.90:443/sdk"}}) == "172.16.2.90"
    assert hf.provider_host({"spec": {}}) == "" and hf.provider_host(None) == ""


def test_the_vms_taken_by_console_waves():
    hosts = {("default", "vmwlab"): "vc.home.lo"}

    def wave(name, vms, **ann):
        p = plain_plan(conditions=[{"type": "Ready", "status": "True"}])
        p["metadata"]["name"] = name
        p["metadata"]["labels"][hf.L_WAVE] = name
        p["metadata"]["annotations"] = ann
        p["spec"]["vms"] = [{"id": v} for v in vms]
        p["spec"]["provider"] = {"source": {"namespace": "default", "name": "vmwlab"}}
        return p

    closed = wave("old", ["vm-20"], **{hf.A_CLOSED: "2026-09-29T20:00:00Z"})
    foreign = copy.deepcopy(PLANS["vague-1"])                 # pas étiqueté console
    unknown = wave("elsewhere", ["vm-30"])
    unknown["spec"]["provider"]["source"]["name"] = "gone"
    rolled = wave("w2", ["vm-18", "vm-19"], **{hf.A_ROLLED_BACK: "vm-19"})
    taken = hf.taken_vms([wave("w1", ["vm-16"]), closed, foreign, unknown, rolled], hosts)
    assert taken == {("vc.home.lo", "vm-16"): {"wave": "w1", "state": "ready"},
                     ("vc.home.lo", "vm-18"): {"wave": "w2", "state": "ready"},
                     ("vc.home.lo", "vm-19"): {"wave": "w2", "state": "rolled-back"}}


# --- importeur CDI -------------------------------------------------------------

SUSE_IMPORTER = "registry.suse.com/suse/sles/16.0/cdi-importer:1.65.0"
UPSTREAM = "quay.io/kubevirt/cdi-importer:v1.65.0"


def cdi_deploy(image=SUSE_IMPORTER, original=None):
    d = fixture("cdi_operator")
    for c in d["spec"]["template"]["spec"]["containers"]:
        for e in c.get("env") or []:
            if e["name"] in ("IMPORTER_IMAGE", "OVIRT_POPULATOR_IMAGE"):
                e["value"] = image
    if original:
        d["metadata"].setdefault("annotations", {})[hf.A_ORIGINAL_IMPORTER] = original
    return d


def test_the_cdi_importer_image_is_recognised():
    # relevé réel : banc déjà basculé sur l'image amont, sans annotation (fait à la main)
    assert hf.cdi_importer_state(fixture("cdi_operator")) == {
        "image": UPSTREAM, "kind": "upstream", "container": "cdi-operator", "original": ""}
    assert hf.cdi_importer_state(cdi_deploy())["kind"] == "suse-no-vddk"
    st = hf.cdi_importer_state(cdi_deploy("mirror.home.lo/kubevirt/cdi-importer:v1.65.0", original=SUSE_IMPORTER))
    assert st == {"image": "mirror.home.lo/kubevirt/cdi-importer:v1.65.0", "kind": "other",
                  "container": "cdi-operator", "original": SUSE_IMPORTER}
    assert hf.cdi_importer_state(None) == {"image": "", "kind": "other", "container": "cdi-operator", "original": ""}


def test_the_importer_patch_keeps_the_original_image_once():
    p = hf.cdi_importer_patch(UPSTREAM, hf.cdi_importer_state(cdi_deploy()))
    assert p["metadata"]["annotations"] == {hf.A_ORIGINAL_IMPORTER: SUSE_IMPORTER}
    (c,) = p["spec"]["template"]["spec"]["containers"]
    assert c["name"] == "cdi-operator"
    assert c["env"] == [{"name": "IMPORTER_IMAGE", "value": UPSTREAM}, {"name": "OVIRT_POPULATOR_IMAGE", "value": UPSTREAM}]
    # déjà gardée : jamais réécrite (un second changement d'image ne l'écrase pas)
    again = hf.cdi_importer_patch("mirror.home.lo/kubevirt/cdi-importer:v1.65.0",
                                  hf.cdi_importer_state(cdi_deploy(UPSTREAM, original=SUSE_IMPORTER)))
    assert "metadata" not in again
    # retour à l'image d'origine : rien à garder
    back = hf.cdi_importer_patch(SUSE_IMPORTER, hf.cdi_importer_state(cdi_deploy(UPSTREAM, original=SUSE_IMPORTER)))
    assert "metadata" not in back and back["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] == SUSE_IMPORTER
    assert "metadata" not in hf.cdi_importer_patch(UPSTREAM, None)
    with pytest.raises(ValueError, match="importer image"):
        hf.cdi_importer_patch("cdi-importer", None)


def test_the_importer_patch_never_records_the_upstream_image_as_original():
    # déjà sur l'image amont (kind "upstream") : passer sur un miroir ne doit
    # jamais enregistrer quay comme "original" (seuls suse-no-vddk et other le sont)
    mirror = "mirror.home.lo/kubevirt/cdi-importer:v1.65.0"
    already_upstream = hf.cdi_importer_state(cdi_deploy(UPSTREAM))
    assert already_upstream["kind"] == "upstream"
    p = hf.cdi_importer_patch(mirror, already_upstream)
    assert "metadata" not in p
    assert p["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] == mirror


def test_the_importer_patch_targets_the_container_found_in_the_deployment():
    # un déploiement dont le conteneur ne s'appelle pas "cdi-operator" ne doit
    # jamais faire patcher un conteneur au hasard
    d = cdi_deploy()
    d["spec"]["template"]["spec"]["containers"][0]["name"] = "operator"
    state = hf.cdi_importer_state(d)
    assert state["container"] == "operator"
    p = hf.cdi_importer_patch(UPSTREAM, state)
    (c,) = p["spec"]["template"]["spec"]["containers"]
    assert c["name"] == "operator"
    # sans état connu (retour du CLI quand original=None) : le nom par défaut
    assert hf.cdi_importer_patch(UPSTREAM, None)["spec"]["template"]["spec"]["containers"][0]["name"] \
        == hf.CDI_OPERATOR[1]


# --- intervalle des copies --------------------------------------------------------

def test_the_precopy_interval():
    assert hf.precopy_interval(fixture("controller")) == 5      # posé à 5 sur le banc
    assert hf.precopy_interval({"spec": {}}) == 60 and hf.precopy_interval(None) == 60
    assert hf.precopy_interval({"spec": {"controller_precopy_interval": "15"}}) == 15
    assert hf.precopy_interval({"spec": {"controller_precopy_interval": "soon"}}) == 60
    assert hf.precopy_patch(15) == {"spec": {"controller_precopy_interval": 15}}
    assert hf.precopy_patch("1440")["spec"]["controller_precopy_interval"] == 1440
    for bad in (4, 1441, "x", None, True):
        with pytest.raises(ValueError, match="precopy interval"):
            hf.precopy_patch(bad)
