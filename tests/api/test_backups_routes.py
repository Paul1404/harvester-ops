"""v1.58.0 : les routes de la fenêtre Backups.

Les listes passent par /api/cluster-objects (types vmbackups, vmsnapshots,
schedules, volsnaps) ; chaque écriture est une action suivie qui lance
bin/harvester-resources.py avec les bons arguments, après un contrôle qui
refuse tôt ce que Harvester refuserait (planning plus fréquent qu'une heure).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "web"))
import app as wapp  # noqa: E402

BACKUP = {"kind": "VirtualMachineBackup", "metadata": {"name": "b1", "namespace": "default",
          "creationTimestamp": "2026-09-26T10:00:00Z"},
          "spec": {"type": "backup", "source": {"name": "web"}}, "status": {"readyToUse": True}}
SNAP = {"kind": "VirtualMachineBackup", "metadata": {"name": "s1", "namespace": "lab",
        "creationTimestamp": "2026-09-26T11:00:00Z"},
        "spec": {"type": "snapshot", "source": {"name": "db"}}, "status": {"readyToUse": True}}
SCHED = {"kind": "ScheduleVMBackup", "metadata": {"name": "nightly", "namespace": "default"},
         "spec": {"cron": "0 2 * * *", "retain": 7, "maxFailure": 3,
                  "vmbackup": {"type": "backup", "source": {"name": "web"}}}}


@pytest.fixture
def cluster(monkeypatch, tmp_path):
    monkeypatch.setattr(wapp, "HTPASSWD_PATH", tmp_path / "absent")
    monkeypatch.setattr(wapp, "load_config", lambda: {"clusters": [{"name": "harv1", "kubeconfig": "/kc"}]})
    monkeypatch.setattr(wapp, "_cluster_reachable", lambda kc, **k: True)
    world = {"items": [BACKUP, SNAP, SCHED], "actions": [],
             "target": {"value": json.dumps({"type": "s3", "endpoint": "https://s3.example",
                                             "bucketName": "vms", "bucketRegion": "eu",
                                             "accessKeyId": "AKIA-SECRET", "secretAccessKey": "TOP-SECRET"})}}

    def fake_json(kc, *args, timeout=15, cluster=None):
        if args[:2] == ("get", "settings.harvesterhci.io"):
            return world["target"]
        kinds = args[1].split(",")
        wanted = {wapp._CO_KIND_OF[k] for k in kinds}
        return {"items": [i for i in world["items"] if i["kind"] in wanted]}
    monkeypatch.setattr(wapp, "_kubectl_json", fake_json)

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        world["actions"].append((label, cmd))

        class Run:
            id = "act000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    return world


def test_the_window_lists_with_a_namespace_filter(cluster):
    with wapp.app.test_client() as c:
        d = c.get("/api/cluster-objects/harv1/vmbackups").get_json()
        assert [r["name"] for r in d["items"]] == ["b1"]
        d = c.get("/api/cluster-objects/harv1/vmsnapshots?namespace=lab").get_json()
        assert [r["name"] for r in d["items"]] == ["s1"]
        assert c.get("/api/cluster-objects/harv1/vmsnapshots?namespace=default").get_json()["items"] == []
        d = c.get("/api/cluster-objects/harv1/schedules").get_json()
        assert d["items"][0]["cron"] == "0 2 * * *"
        assert c.get("/api/cluster-objects/harv1/vmbackups?namespace=Bad_NS").status_code == 400


def test_the_backup_target_never_shows_its_keys(cluster):
    with wapp.app.test_client() as c:
        d = c.get("/api/backup-target/harv1").get_json()
    assert d == {"type": "s3", "endpoint": "https://s3.example", "bucket": "vms", "region": "eu", "set": True}
    assert "SECRET" not in json.dumps(d)


def test_a_backup_and_its_restore_go_through_the_cli(cluster):
    with wapp.app.test_client() as c:
        r = c.post("/api/backups/harv1/default", json={"vm": "web", "type": "snapshot", "name": "web-s1"})
        assert r.status_code == 202 and r.get_json()["action_id"] == "act000000001"
        assert c.post("/api/backups/harv1/default", json={"vm": "Web!"}).status_code == 400
        assert c.post("/api/backups/harv1/default/b1/restore",
                      json={"new_vm": "web-copy", "keep_mac": True, "halt": True}).status_code == 202
        assert c.post("/api/backups/harv1/default/b1/restore", json={"replace": True}).status_code == 202
        assert c.post("/api/backups/harv1/default/b1/restore", json={"new_vm": ""}).status_code == 400
        assert c.delete("/api/backups/harv1/default/b1").status_code == 202
    labels = [a[0] for a in cluster["actions"]]
    assert labels == ["snapshot:create:default/web", "backup:restore:default/b1",
                      "backup:restore:default/b1", "backup:delete:default/b1"]
    create = cluster["actions"][0][1]
    assert create[2:4] == ["backup", "create"] and create[create.index("--type") + 1] == "snapshot"
    assert "--kubeconfig" in create and create[create.index("--name") + 1] == "web-s1"
    new = cluster["actions"][1][1]
    assert new[new.index("--new-vm") + 1] == "web-copy" and "--keep-mac" in new and "--halt" in new
    assert "--replace" in cluster["actions"][2][1]


def test_a_schedule_is_checked_before_harvester(cluster):
    body = {"name": "nightly2", "vm": "web", "cron": "*/30 * * * *", "retain": 7, "max_failure": 3}
    with wapp.app.test_client() as c:
        r = c.post("/api/schedules/harv1/default", json=body)
        assert r.status_code == 400 and "once an hour" in r.get_json()["error"]
        body["cron"] = "30 3 * * 0"
        assert c.post("/api/schedules/harv1/default", json=body).status_code == 202
        assert c.post("/api/schedules/harv1/default/nightly2/suspend").status_code == 202
        assert c.post("/api/schedules/harv1/default/nightly2/resume").status_code == 202
        assert c.post("/api/schedules/harv1/default/nightly2/pause").status_code == 400
        assert c.delete("/api/schedules/harv1/default/nightly2").status_code == 202
    cmd = cluster["actions"][0][1]
    assert cmd[cmd.index("--cron") + 1] == "30 3 * * 0" and cmd[cmd.index("--retain") + 1] == "7"


def test_a_volume_snapshot_restores_into_a_named_volume(cluster):
    with wapp.app.test_client() as c:
        assert c.post("/api/volsnaps/harv1/default/v1/restore", json={"new_volume": "web-root-copy"}).status_code == 202
        assert c.post("/api/volsnaps/harv1/default/v1/restore", json={"new_volume": "x", "storage_class": "Bad SC"}).status_code == 400
        assert c.delete("/api/volsnaps/harv1/default/v1").status_code == 202
    cmd = cluster["actions"][0][1]
    assert cmd[cmd.index("--new-volume") + 1] == "web-root-copy"


def test_backup_writes_need_an_operator():
    for path in ("/api/backups/harv1/default", "/api/schedules/harv1/default/s/suspend",
                 "/api/volsnaps/harv1/default/v/restore"):
        assert wapp.required_role_for(path, "POST") == "operator"
    assert wapp.required_role_for("/api/backups/harv1/default/b1", "DELETE") == "operator"
