"""v1.58.0 : la fenêtre Backups (bin/lib/hv_backups.py et les commandes
`harvester-resources backup|schedule|volsnap`).

Objets et refus tels que harv1 (Harvester v1.9.0) les a montrés : le webhook
refuse un planning plus fréquent qu'une heure (« schedule granularity 30m0s
less than 1h0m0s », essai à blanc côté serveur), une restauration de la 1.9
accepte `haltAfterRestore`.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_backups as hb  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres158", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)


def test_a_schedule_runs_at_most_once_an_hour():
    assert hb.check_cron("0  2 * * *") == "0 2 * * *"
    assert hb.check_cron("15 */6 * * 1-5") == "15 */6 * * 1-5"
    for bad in ("*/30 * * * *", "* * * * *", "0,30 * * * *", "0 2 * *", ""):
        with pytest.raises(ValueError):
            hb.check_cron(bad)


def test_the_schedule_object():
    m = hb.schedule_manifest("default", "nightly", "web", "0 2 * * *", 7, 3, "snapshot")
    assert m["spec"] == {"cron": "0 2 * * *", "retain": 7, "maxFailure": 3, "suspend": False,
                         "vmbackup": {"type": "snapshot", "source": {"apiGroup": "kubevirt.io",
                                                                     "kind": "VirtualMachine", "name": "web"}}}
    with pytest.raises(ValueError):
        hb.schedule_manifest("default", "n", "web", "0 2 * * *", 3, 5)        # maxFailure > retain
    # règles de Harvester 1.9, vues sur harv1 : maxFailure >= 2 (CRD) et
    # maxFailure < retain (webhook), donc retain >= 3
    with pytest.raises(ValueError, match="2 failures"):
        hb.schedule_manifest("default", "n", "web", "0 2 * * *", 3, 1)
    with pytest.raises(ValueError, match="fewer than the copies"):
        hb.schedule_manifest("default", "n", "web", "0 2 * * *", 3, 3)
    with pytest.raises(ValueError, match="between 3 and 250"):
        hb.schedule_manifest("default", "n", "web", "0 2 * * *", 2, 2)
    assert hb.schedule_manifest("default", "n", "web", "0 2 * * *", 3, 2)["spec"]["maxFailure"] == 2
    with pytest.raises(ValueError):
        hb.schedule_manifest("default", "Bad_Name", "web", "0 2 * * *", 3, 1)


def test_the_restore_object():
    new = hb.restore_manifest("default", "web-b1", "web-copy", True, keep_mac=True, halt=True)
    assert new["spec"]["newVM"] is True and new["spec"]["keepMacAddress"] is True
    assert new["spec"]["haltAfterRestore"] is True and new["spec"]["deletionPolicy"] == "retain"
    repl = hb.restore_manifest("default", "web-b1", "web", False, keep_mac=True, halt=True, supports_halt=False)
    assert "keepMacAddress" not in repl["spec"] and "haltAfterRestore" not in repl["spec"]
    with pytest.raises(ValueError):
        hb.restore_manifest("default", "b", "vm", True, delete_policy="keep")


def test_the_volume_restore_is_a_claim_from_the_snapshot():
    m = hb.volume_restore_manifest("default", "snap-1", "restored", "20Gi", "harv-rep1")
    assert m["spec"]["dataSource"] == {"apiGroup": "snapshot.storage.k8s.io", "kind": "VolumeSnapshot",
                                       "name": "snap-1"}
    assert m["spec"]["resources"]["requests"]["storage"] == "20Gi" and m["spec"]["volumeMode"] == "Block"


def test_the_window_reads():
    items = [
        {"metadata": {"name": "b1", "namespace": "default", "creationTimestamp": "t",
                      "labels": {"harvesterhci.io/svmbackup": "nightly"}},
         "spec": {"type": "backup", "source": {"name": "web"}},
         "status": {"readyToUse": True, "backupTarget": {"endpoint": "nfs://nas/x"},
                    "volumeBackups": [{"volumeSize": 1024}, {"volumeSize": 2048}]}},
        {"metadata": {"name": "s1", "namespace": "default"}, "spec": {"type": "snapshot", "source": {"name": "web"}},
         "status": {"readyToUse": False, "error": {"message": "boom"}}},
    ]
    [b] = hb.backups(items, "backup")
    assert b["size"] == 3072 and b["target"] == "nfs://nas/x" and b["schedule"] == "nightly" and b["ready"]
    [s] = hb.backups(items, "snapshot")
    assert s["error"] == "boom" and not s["ready"]
    [sch] = hb.schedules([{"metadata": {"name": "nightly", "namespace": "default"},
                           "spec": {"cron": "0 2 * * *", "retain": 7, "maxFailure": 3, "suspend": True,
                                    "vmbackup": {"type": "backup", "source": {"name": "web"}}},
                           "status": {"vmbackupInfo": [{"createdAt": "2026-09-01"}, {"createdAt": "2026-09-02"}]}}])
    assert sch["suspended"] and sch["kept"] == 2 and sch["last"] == "2026-09-02"
    [v] = hb.volume_snapshots([{"metadata": {"name": "v1", "namespace": "default",
                                             "ownerReferences": [{"kind": "VirtualMachineBackup", "name": "s1"}]},
                                "spec": {"source": {"persistentVolumeClaimName": "web-root"},
                                         "volumeSnapshotClassName": "longhorn-snapshot"},
                                "status": {"readyToUse": True, "restoreSize": "20Gi"}}])
    assert v["owner"] == "s1" and v["pvc"] == "web-root" and v["size"] == "20Gi"


# -- commandes ----------------------------------------------------------------------

class FakeKube:
    def __init__(self, objects):
        self.objects = objects             # {(kind, ns, name): obj}
        self.created, self.patched, self.deleted = [], [], []

    def get(self, kind, ns, name):
        return self.objects.get((kind, ns, name))

    def create(self, obj):
        self.created.append(obj)
        kind = {"VirtualMachineBackup": hb.K_BACKUP, "VirtualMachineRestore": hb.K_RESTORE,
                "PersistentVolumeClaim": "persistentvolumeclaims"}.get(obj["kind"], obj["kind"])
        done = {"VirtualMachineBackup": {"readyToUse": True},
                "VirtualMachineRestore": {"complete": True},
                "PersistentVolumeClaim": {"phase": "Bound"}}.get(obj["kind"], {})
        self.objects[(kind, obj["metadata"]["namespace"], obj["metadata"]["name"])] = dict(obj, status=done)
        return obj

    def patch(self, kind, ns, name, patch):
        self.patched.append((kind, name, patch))

    def delete(self, kind, ns, name, cascade=None):
        self.deleted.append((kind, name))
        self.objects.pop((kind, ns, name), None)


def run(kube, argv, monkeypatch):
    monkeypatch.setattr(hres, "kube_from", lambda args: kube)
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    return hres.main(argv)


TARGET = ("settings.harvesterhci.io", None, "backup-target")
READY = {"spec": {"type": "backup", "source": {"name": "web"}}, "status": {"readyToUse": True}}


def test_a_backup_needs_a_target(monkeypatch):
    k = FakeKube({})
    assert run(k, ["backup", "create", "--namespace", "default", "--vm", "web"], monkeypatch) == hres.EXIT_BLOCKED
    k = FakeKube({TARGET: {"value": '{"type":"nfs","endpoint":"nfs://nas/x"}'}})
    assert run(k, ["backup", "create", "--namespace", "default", "--vm", "web", "--name", "b1"],
               monkeypatch) == hres.EXIT_OK
    assert k.created[0]["spec"]["type"] == "backup"
    # un instantané reste dans le cluster : pas de cible nécessaire
    k = FakeKube({})
    assert run(k, ["backup", "create", "--namespace", "default", "--vm", "web", "--type", "snapshot"],
               monkeypatch) == hres.EXIT_OK


def test_replacing_needs_a_stopped_vm(monkeypatch, capsys):
    vm = ("virtualmachines.kubevirt.io", "default", "web")
    vmi = ("virtualmachineinstances.kubevirt.io", "default", "web")
    k = FakeKube({(hb.K_BACKUP, "default", "b1"): READY, vm: {}, vmi: {}})
    assert run(k, ["backup", "restore", "--namespace", "default", "--name", "b1", "--replace"],
               monkeypatch) == hres.EXIT_BLOCKED
    assert "stop default/web first" in capsys.readouterr().err
    del k.objects[vmi]
    assert run(k, ["backup", "restore", "--namespace", "default", "--name", "b1", "--replace"],
               monkeypatch) == hres.EXIT_OK
    spec = k.created[-1]["spec"]
    assert spec["newVM"] is False and spec["target"]["name"] == "web"


def test_restoring_into_a_new_vm_refuses_a_taken_name(monkeypatch, capsys):
    k = FakeKube({(hb.K_BACKUP, "default", "b1"): READY,
                  ("virtualmachines.kubevirt.io", "default", "copy"): {}})
    assert run(k, ["backup", "restore", "--namespace", "default", "--name", "b1", "--new-vm", "copy"],
               monkeypatch) == hres.EXIT_BLOCKED
    assert run(k, ["backup", "restore", "--namespace", "default", "--name", "b1", "--new-vm", "copy2",
                   "--keep-mac", "--halt"], monkeypatch) == hres.EXIT_OK
    assert k.created[-1]["spec"]["newVM"] is True and k.created[-1]["spec"]["keepMacAddress"] is True


def test_a_schedule_is_suspended_and_resumed(monkeypatch):
    k = FakeKube({TARGET: {"value": "{}"}})
    assert run(k, ["schedule", "create", "--namespace", "default", "--name", "nightly", "--vm", "web",
                   "--cron", "0 2 * * *", "--retain", "7", "--max-failure", "3"], monkeypatch) == hres.EXIT_OK
    assert run(k, ["schedule", "suspend", "--namespace", "default", "--name", "nightly"], monkeypatch) == hres.EXIT_OK
    assert run(k, ["schedule", "resume", "--namespace", "default", "--name", "nightly"], monkeypatch) == hres.EXIT_OK
    assert [p[2] for p in k.patched] == [{"spec": {"suspend": True}}, {"spec": {"suspend": False}}]


def test_a_vm_snapshot_volume_is_deleted_through_its_vm_snapshot(monkeypatch, capsys):
    snap = {"metadata": {"ownerReferences": [{"kind": "VirtualMachineBackup", "name": "s1"}]},
            "status": {"readyToUse": True}}
    k = FakeKube({(hb.K_VOLSNAP, "default", "v1"): snap})
    assert run(k, ["volsnap", "delete", "--namespace", "default", "--name", "v1"], monkeypatch) == hres.EXIT_BLOCKED
    assert "belongs to the VM snapshot s1" in capsys.readouterr().err


def test_a_volume_snapshot_restores_into_a_new_volume(monkeypatch):
    snap = {"spec": {"source": {"persistentVolumeClaimName": "web-root"}},
            "status": {"readyToUse": True, "restoreSize": "20Gi"}}
    pvc = {"spec": {"storageClassName": "harv-rep1"}}
    k = FakeKube({(hb.K_VOLSNAP, "default", "v1"): snap, ("persistentvolumeclaims", "default", "web-root"): pvc})
    assert run(k, ["volsnap", "restore", "--namespace", "default", "--name", "v1", "--new-volume", "restored"],
               monkeypatch) == hres.EXIT_OK
    spec = k.created[-1]["spec"]
    assert spec["storageClassName"] == "harv-rep1" and spec["resources"]["requests"]["storage"] == "20Gi"
