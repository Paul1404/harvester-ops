"""v1.69.0 : les routes de la mise à jour de Harvester : lecture de l'état
(versions et éligibilité, ISO du magasin, pré-contrôles, progression), gestes
réservés aux administrateurs et passés à la commande, journaux remis une
fois."""

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_upgrade as hu  # noqa: E402
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

PW = "a long test password"
SHA = "a" * 128


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(wapp, "AUTH_OPEN_ALLOWED", False)
    monkeypatch.setattr(wapp, "HTPASSWD_PATH", tmp_path / "none")
    monkeypatch.setattr(wapp, "ROLES_PATH", tmp_path / "none.yaml")
    monkeypatch.setattr(wapp, "_roles_cache", {"mtime": None, "data": None})
    monkeypatch.setattr(wapp, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(wapp, "_ACCOUNTS", {"store": None})
    monkeypatch.setattr(wapp, "_LOCAL_SESSIONS", acc.LocalSessions())
    monkeypatch.setattr(wapp, "load_config", lambda: {"clusters": [{"name": "harv1", "kubeconfig": "/kc"}]})
    monkeypatch.setattr(wapp, "_cluster_reachable", lambda kc: True)
    monkeypatch.setattr(wapp, "_capi_work_dir", lambda: tmp_path)
    store = tmp_path / "iso"
    store.mkdir()
    (store / "harvester-v1.9.0-amd64.iso").write_bytes(b"x")
    (store / "harvester-v1.8.2-amd64.iso").write_bytes(b"x")
    (store / "harvester-v1.9.0-amd64.sha512").write_text(f"{SHA}  harvester-v1.9.0-amd64.iso\n{'b' * 128}  harvester-v1.9.0-vmlinuz-amd64\n")
    monkeypatch.setattr(wapp, "ISO_DIR", store)
    monkeypatch.setattr(wapp, "_iso_release_info", lambda p: {"harvester": p.name.split("-")[1], "min_upgradable": "v1.8.0",
                                                             "os": "", "kubernetes": "", "rancher": ""})
    wapp._accounts().create("adm", PW, "admin")
    wapp._accounts().create("ops", PW, "operator")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": []}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        files = {cmd[i + 1]: Path(cmd[i + 1]).read_text() for i, a in enumerate(cmd) if a == "--version-file"}
        if "--out" in cmd:
            Path(cmd[cmd.index("--out") + 1]).write_bytes(b"PK zip")
        w["actions"].append((label, cmd, files))
        if after:
            after()

        class Run:
            id = "upg000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    up = {"metadata": {"name": "hvst-upgrade-x", "labels": {hu.L_STATE: "Succeeded", hu.L_LATEST: "true"},
                       "creationTimestamp": "2026-09-16T16:27:07Z"},
          "spec": {"version": "v1.9.0"}, "status": {"conditions": [{"type": "Completed", "status": "True"}],
                                                     "previousVersion": "v1.8.2", "upgradeLog": "hvst-upgrade-x-upgradelog"}}
    objs = {"settings.harvesterhci.io": {"value": "v1.8.2"},
            hu.K_VERSION: {"items": [{"metadata": {"name": "v1.9.0"}, "spec": {"isoURL": "https://x", "minUpgradableVersion": "v1.8.0"}},
                                     {"metadata": {"name": "v1.8.0"}, "spec": {"isoURL": "https://y"}}]},
            hu.K_UPGRADE: {"items": [up]},
            "nodes": {"items": [{"metadata": {"name": "n1"}, "spec": {}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]}}
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: objs.get(a[1], {"items": []}))
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_upgrade_window_reads_versions_isos_and_the_last_upgrade(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/upgrade/harv1", headers=auth("eye")).get_json()
    assert d["current"] == "v1.8.2" and d["running"] is None
    v = {r["name"]: r for r in d["versions"]}
    assert v["v1.9.0"]["eligible"] is True and v["v1.8.0"]["eligible"] is False and "downgrading" in v["v1.8.0"]["reason"]
    isos = {i["name"]: i for i in d["isos"]}
    assert isos["harvester-v1.9.0-amd64.iso"]["eligible"] and isos["harvester-v1.9.0-amd64.iso"]["sha512"] == SHA
    assert not isos["harvester-v1.8.2-amd64.iso"]["eligible"] or isos["harvester-v1.8.2-amd64.iso"]["reason"] == "same version"
    assert d["upgrade"]["completed"] is True and d["upgrade"]["can_dismiss"] is True
    assert {p["key"] for p in d["prechecks"]} >= {"running", "nodes-ready", "backups"}


def test_upgrade_gestures_are_for_administrators_and_reach_the_command(world):
    yaml = "kind: Version\nmetadata:\n  name: v1.9.1\nspec:\n  isoURL: https://releases.rancher.com/harvester/v1.9.1/h.iso\n"
    with wapp.app.test_client() as c:
        assert c.post("/api/upgrade/harv1/do/start", json={"version": "v1.9.0"}, headers=auth("ops")).status_code == 403
        assert c.post("/api/upgrade/harv1/do/explode", json={}, headers=auth("adm")).status_code == 400
        assert c.post("/api/upgrade/harv1/do/start", json={"iso": "../etc/passwd.iso"}, headers=auth("adm")).status_code == 400
        assert c.post("/api/upgrade/harv1/do/version-add", json={"yaml": "kind: Nope"}, headers=auth("adm")).status_code == 400
        assert c.post("/api/upgrade/harv1/do/version-add", json={"yaml": yaml}, headers=auth("adm")).status_code == 202
        assert c.post("/api/upgrade/harv1/do/start", json={"version": "v1.9.0", "log": False, "skip_single_replica": True},
                      headers=auth("adm")).status_code == 202
        assert c.post("/api/upgrade/harv1/do/start", json={"iso": "harvester-v1.9.0-amd64.iso"}, headers=auth("adm")).status_code == 202
        assert c.post("/api/upgrade/harv1/do/resume-node", json={"name": "hvst-upgrade-x", "node": "n1"},
                      headers=auth("adm")).status_code == 202
    (l1, c1, f1), (l2, c2, _), (l3, c3, _), (_, c4, _) = world["actions"]
    assert l1 == "upgrade:version-add:v1.9.1" and "v1.9.1" in list(f1.values())[0]
    assert not any(Path(p).exists() for p in f1)                            # fichier privé effacé
    assert c2[2:4] == ["upgrade", "start"] and c2[-4:] == ["--version", "v1.9.0", "--no-log", "--skip-single-replica"]
    assert c3[c3.index("--checksum") + 1] == SHA and c3[c3.index("--iso") + 1].endswith("harvester-v1.9.0-amd64.iso")
    assert c4[-2:] == ["--node", "n1"]


def test_upgrade_logs_are_handed_over_once(world):
    with wapp.app.test_client() as c:
        r = c.post("/api/upgrade/harv1/do/logs", json={"name": "hvst-upgrade-x"}, headers=auth("adm"))
        token = r.get_json()["download"]
        assert c.get(f"/api/upgrade/harv1/logs/{token}", headers=auth("ops")).status_code == 403
        got = c.get(f"/api/upgrade/harv1/logs/{token}", headers=auth("adm"))
        assert got.status_code == 200 and got.data == b"PK zip"
        assert c.get(f"/api/upgrade/harv1/logs/{token}", headers=auth("adm")).status_code == 404
