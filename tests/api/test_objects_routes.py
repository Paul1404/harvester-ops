"""v1.59.0 : les routes de création, de modification et de suppression.

Chaque écriture est une action suivie par bin/harvester-resources.py ; ce
qui change le cluster pour tous (une classe de stockage, un réseau) est aux
administrateurs ; la configuration d'un add-on, qui peut porter des mots de
passe, ne se LIT qu'en administrateur.
"""

import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "web"))
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

PW = "a long enough password"


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
    wapp._accounts().create("boss", PW, "admin")
    wapp._accounts().create("ops", PW, "operator")
    w = {"actions": []}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        w["actions"].append((label, cmd, spec))

        class Run:
            id = "obj000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: {
        "spec": {"valuesContent": "grafana:\n  adminPassword: s3cret\n", "enabled": True}})
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_an_operator_creates_images_keys_secrets_and_volumes(world):
    with wapp.app.test_client() as c:
        for kind, spec in (("image", {"url": "https://x/a.qcow2"}),
                           ("sshkey", {"name": "k", "public_key": "ssh-ed25519 AAAAC3Nza ops"}),
                           ("secret", {"name": "s", "data": {"userdata": "x"}}),
                           ("volume", {"name": "v", "size": "5Gi", "storage_class": "harv-rep1"}),
                           ("volume", {"name": "v2", "size": "20Gi", "image": "default/leap"})):
            r = c.post(f"/api/objects/harv1/{kind}", json={"spec": spec}, headers=auth("ops"))
            assert r.status_code == 202, (kind, r.get_json())
        assert c.post("/api/objects/harv1/image", json={"spec": {"url": "ftp://x"}},
                      headers=auth("ops")).status_code == 400
        assert c.post("/api/objects/harv1/pod", json={"spec": {}}, headers=auth("ops")).status_code == 400
    label, cmd, spec = world["actions"][0]
    assert cmd[2:4] == ["create", "--kubeconfig"] and cmd[-2:] == ["--kind", "image"]
    assert spec == {"url": "https://x/a.qcow2"}


def test_storage_classes_and_networks_are_for_administrators(world):
    with wapp.app.test_client() as c:
        sc = {"spec": {"name": "fast", "replicas": 2}}
        r = c.post("/api/objects/harv1/storageclass", json=sc, headers=auth("ops"))
        assert r.status_code == 403 and r.get_json()["required"] == "admin"
        assert c.post("/api/objects/harv1/storageclass", json=sc, headers=auth("boss")).status_code == 202
        assert c.delete("/api/objects/harv1/network/lab?namespace=default", headers=auth("ops")).status_code == 403
        assert c.delete("/api/objects/harv1/network/lab?namespace=default", headers=auth("boss")).status_code == 202
        assert c.post("/api/storageclasses/harv1/fast/default", headers=auth("ops")).status_code == 403
        assert c.post("/api/storageclasses/harv1/fast/default", headers=auth("boss")).status_code == 202
    cmd = world["actions"][-1][1]
    assert cmd[2] == "sc-default" and cmd[-2:] == ["--name", "fast"]


def test_a_deletion_names_its_namespace(world):
    with wapp.app.test_client() as c:
        assert c.delete("/api/objects/harv1/secret/s", headers=auth("ops")).status_code == 400
        assert c.delete("/api/objects/harv1/secret/s?namespace=default", headers=auth("ops")).status_code == 202
    cmd = world["actions"][-1][1]
    assert cmd[2] == "delete" and "--namespace" in cmd and cmd[cmd.index("--kind") + 1] == "secret"


def test_a_volume_grows_by_a_valid_size(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/volumes/harv1/default/data/expand", json={"size": "big"},
                      headers=auth("ops")).status_code == 400
        assert c.post("/api/volumes/harv1/default/data/expand", json={"size": "40Gi"},
                      headers=auth("ops")).status_code == 202
    assert world["actions"][-1][1][-2:] == ["--size", "40Gi"]


def test_an_addon_configuration_is_read_and_written_by_administrators(world):
    with wapp.app.test_client() as c:
        assert c.get("/api/addons/harv1/cattle-monitoring-system/rancher-monitoring/values",
                     headers=auth("ops")).status_code == 403
        d = c.get("/api/addons/harv1/cattle-monitoring-system/rancher-monitoring/values",
                  headers=auth("boss")).get_json()
        assert "adminPassword" in d["values"] and d["enabled"] is True
        assert c.post("/api/addons/harv1/cattle-monitoring-system/rancher-monitoring/values",
                      json={"values": "- not a mapping"}, headers=auth("boss")).status_code == 400
        assert c.post("/api/addons/harv1/cattle-monitoring-system/rancher-monitoring/values",
                      json={"values": "retention: 5d\n"}, headers=auth("boss")).status_code == 202
    label, cmd, _ = world["actions"][-1]
    assert label == "addon:values:cattle-monitoring-system/rancher-monitoring"
    values_file = cmd[cmd.index("--values") + 1]
    assert Path(values_file).read_text() == "retention: 5d\n"       # effacé après l'action réelle
    Path(values_file).unlink()
