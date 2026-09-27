"""v1.67.0 : Advanced > Settings et Support, côté script et côté routes.

Le script remet les secrets masqués depuis le cluster, attend que Harvester
applique la valeur, écrit le kubeconfig dans un fichier privé ; les routes
masquent les secrets, réservent les écritures (et toute la page Support) aux
administrateurs, passent la valeur par un fichier privé effacé après
l'action, et ne rendent le kubeconfig qu'une fois."""

import argparse
import base64
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_settings as hs  # noqa: E402
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_set", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)
PW = "a long test password"


class Kube:
    def __init__(self, objs):
        self.objs = dict(objs)
        self.calls = []

    def get(self, kind, ns, name):
        return json.loads(json.dumps(self.objs[(kind, ns, name)])) if (kind, ns, name) in self.objs else None

    def list(self, kind, ns=None, selector=None):
        return [json.loads(json.dumps(o)) for (k, n, _), o in self.objs.items() if k == kind and (ns is None or n == ns)]

    def patch(self, kind, ns, name, patch):
        self.calls.append(("patch", kind, name, patch))
        o = self.objs[(kind, ns, name)]
        o["value"] = patch.get("value") or ""
        o["metadata"].setdefault("annotations", {})[hs.HASH_ANN] = "h-" + (o["value"] or "default")

    PLURAL = {"RoleBinding": "rolebindings", "ClusterRoleBinding": "clusterrolebindings", "ServiceAccount": "serviceaccounts",
              "Namespace": "namespaces", "SupportBundle": hs.K_BUNDLE}

    def create(self, obj):
        self.calls.append(("create", obj["kind"], obj["metadata"]["name"]))
        self.objs[(self.PLURAL[obj["kind"]], obj["metadata"].get("namespace"), obj["metadata"]["name"])] = obj
        return obj

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))
        self.objs.pop((kind, ns, name), None)

    def run(self, *a, **k):
        self.calls.append(("run",) + a)
        return "TOKEN-123\n"

    def server_host(self):
        return "10.0.0.1"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    wait = hres._wait
    monkeypatch.setattr(hres, "_wait", lambda *a, **kw: wait(*a, **{"sleep": lambda s: None, **kw}))


def _run(fn, kube, a):
    orig = hres.kube_from
    hres.kube_from = lambda _a: kube
    try:
        return fn(a)
    finally:
        hres.kube_from = orig


def setting(name, value="", default=""):
    return {"metadata": {"name": name, "annotations": {hs.HASH_ANN: "h-old"}}, "value": value, "default": default}


def test_a_masked_secret_comes_back_from_the_cluster(tmp_path):
    cur = json.dumps({"ca": "C", "publicCertificate": "P", "privateKey": "KEY"})
    k = Kube({(hs.K_SETTING, None, "ssl-certificates"): setting("ssl-certificates", cur)})
    f = tmp_path / "v"
    f.write_text(hs.redact("ssl-certificates", cur).replace('"C"', '"C2"'))
    a = argparse.Namespace(action="set", name="ssl-certificates", value_file=str(f), timeout=5)
    assert _run(hres.cmd_setting, k, a) == hres.EXIT_OK
    [(_, _, _, patch)] = [c for c in k.calls if c[0] == "patch"]
    assert json.loads(patch["value"]) == {"ca": "C2", "publicCertificate": "P", "privateKey": "KEY"}


def test_reset_writes_null_and_read_only_is_refused(tmp_path):
    k = Kube({(hs.K_SETTING, None, "log-level"): setting("log-level", "debug", "info"),
              (hs.K_SETTING, None, "server-version"): setting("server-version", "v1.9.0")})
    assert _run(hres.cmd_setting, k, argparse.Namespace(action="reset", name="log-level", value_file=None, timeout=5)) == hres.EXIT_OK
    assert k.calls[0] == ("patch", hs.K_SETTING, "log-level", {"value": None})
    with pytest.raises(ValueError, match="read-only"):
        _run(hres.cmd_setting, k, argparse.Namespace(action="reset", name="server-version", value_file=None, timeout=5))


def test_the_backup_target_test_asks_harvester(tmp_path):
    k = Kube({})
    assert _run(hres.cmd_setting, k, argparse.Namespace(action="test-backup-target", name=None, value_file=None,
                                                         timeout=5)) == hres.EXIT_OK
    assert k.calls[0][1:4] == ("get", "--raw", hs.BACKUP_HEALTH_PATH)


def test_a_kubeconfig_is_written_private_and_revoked_whole(tmp_path):
    k = Kube({("clusterroles", None, "view"): {"metadata": {"name": "view"}},
              ("namespaces", None, "default"): {"metadata": {"name": "default"}},
              ("configmaps", "harvester-system", "vip"): {"data": {"ip": "172.16.3.100"}},
              ("configmaps", hs.KC_NS, "kube-root-ca.crt"): {"data": {"ca.crt": "-----BEGIN CERTIFICATE-----\n"}}})
    out = tmp_path / "kc.yaml"
    a = argparse.Namespace(action="create", name="ci", role="view", namespace="default", duration="8h", description="",
                           out=str(out), cluster_name="harv1", cluster=None)
    assert _run(hres.cmd_kubeconfig, k, a) == hres.EXIT_OK
    text = out.read_text()
    assert "token: TOKEN-123" in text and "server: https://172.16.3.100:6443" in text
    assert oct(os.stat(out).st_mode & 0o777) == "0o600"
    assert ("run", "create", "token", "kc-ci", "-n", hs.KC_NS, "--duration=28800s") in k.calls
    assert ("create", "RoleBinding", "harvester-ops-kc-ci") in k.calls
    k.calls.clear()
    a = argparse.Namespace(action="revoke", name="ci")
    assert _run(hres.cmd_kubeconfig, k, a) == hres.EXIT_OK
    assert k.calls == [("delete", "rolebindings", "harvester-ops-kc-ci"), ("delete", "serviceaccounts", "kc-ci")]
    with pytest.raises(ValueError, match="no kubeconfig"):
        _run(hres.cmd_kubeconfig, k, a)


def test_a_support_bundle_waits_for_ready(tmp_path):
    class K(Kube):
        def get(self, kind, ns, name):
            o = super().get(kind, ns, name)
            if kind == hs.K_BUNDLE and o is not None:
                o["status"] = {"state": "ready", "filename": "b.zip", "filesize": 10}
            return o
    k = K({("namespaces", None, "default"): {"metadata": {"name": "default"}}})
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"description": "slow VM", "namespaces": ["default"], "name": "bundle-x"}))
    assert _run(hres.cmd_supportbundle, k, argparse.Namespace(action="create", spec=str(spec), name=None,
                                                             timeout=5)) == hres.EXIT_OK
    assert ("create", "SupportBundle", "bundle-x") in k.calls


# -- routes -------------------------------------------------------------------------

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
    wapp._accounts().create("adm", PW, "admin")
    wapp._accounts().create("ops", PW, "operator")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": []}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        files = {cmd[i + 1]: Path(cmd[i + 1]).read_text() for i, a in enumerate(cmd) if a in ("--value-file", "--spec")}
        if "--out" in cmd:                                   # le script écrit le kubeconfig
            Path(cmd[cmd.index("--out") + 1]).write_text("kind: Config\ntoken: SECRET\n")
        w["actions"].append((label, cmd, files))
        if after:
            after()

        class Run:
            id = "set000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    tls = json.dumps({"ca": "C", "publicCertificate": "P", "privateKey": "-----BEGIN PRIVATE KEY-----x"})
    settings = [{"metadata": {"name": "log-level"}, "value": "debug", "default": "info"},
                {"metadata": {"name": "ssl-certificates"}, "value": tls, "default": "{}"}]
    sas = [hs.kubeconfig_objects({"name": "ci", "role": "view"})[0]]
    bundles = [{"metadata": {"name": "bundle-a", "namespace": hs.BUNDLE_NS}, "spec": {"description": "d"},
                "status": {"state": "generating", "progress": 40}}]

    def kjson(kc, *a, **k):
        if a[1] == hs.K_SETTING:
            return {"items": settings}
        if a[1] == hs.K_BUNDLE:
            return bundles[0] if len(a) > 2 and a[2] == "bundle-a" else {"items": bundles}
        if a[1] == "serviceaccounts":
            return {"items": sas}
        if a[1] == "clusterroles":
            return {"items": [{"metadata": {"name": "view"}, "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]}]}]}
        if a[1] == "namespaces":
            return {"items": [{"metadata": {"name": "default"}}, {"metadata": {"name": "kube-system"}}]}
        return {"items": []}
    monkeypatch.setattr(wapp, "_kubectl_json", kjson)
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_settings_are_read_with_their_secrets_masked(world):
    with wapp.app.test_client() as c:
        r = c.get("/api/hv-settings/harv1", headers=auth("eye"))
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "PRIVATE KEY" not in body
    items = {s["name"]: s for s in r.get_json()["items"]}
    assert items["log-level"]["modified"] and hs.MASK in items["ssl-certificates"]["value"]


def test_setting_writes_are_for_administrators_and_checked_first(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/hv-settings/harv1/do/set", json={"name": "log-level", "value": "trace"},
                      headers=auth("ops")).status_code == 403
        r = c.post("/api/hv-settings/harv1/do/set", json={"name": "log-level", "value": "loud"}, headers=auth("adm"))
        assert r.status_code == 400 and "one of" in r.get_json()["error"]
        assert c.post("/api/hv-settings/harv1/do/set", json={"name": "server-version", "value": "v2"},
                      headers=auth("adm")).status_code == 400
        assert c.post("/api/hv-settings/harv1/do/set", json={"name": "log-level", "value": ""},
                      headers=auth("adm")).status_code == 400
        assert c.post("/api/hv-settings/harv1/do/set", json={"name": "log-level", "value": "trace"},
                      headers=auth("adm")).status_code == 202
        masked = json.dumps({"ca": "C2", "publicCertificate": "P", "privateKey": hs.MASK}, ensure_ascii=False)
        assert c.post("/api/hv-settings/harv1/do/set", json={"name": "ssl-certificates", "value": masked},
                      headers=auth("adm")).status_code == 202
        assert c.post("/api/hv-settings/harv1/do/reset", json={"name": "log-level"}, headers=auth("adm")).status_code == 202
        assert c.post("/api/hv-settings/harv1/do/test", json={}, headers=auth("adm")).status_code == 202
    (l1, cmd1, f1), (_, _, f2), (l3, cmd3, _), (l4, cmd4, _) = world["actions"]
    assert l1 == "setting:set:log-level" and cmd1[2:4] == ["setting", "set"] and list(f1.values()) == ["trace"]
    assert not any(Path(p).exists() for p in f1)                          # fichier privé effacé
    assert hs.MASK in list(f2.values())[0]                               # le script remettra la clé
    assert cmd3[-2:] == ["--name", "log-level"] and cmd4[2:4] == ["setting", "test-backup-target"]


def test_support_is_for_administrators_only(world):
    with wapp.app.test_client() as c:
        assert c.get("/api/hv-support/harv1", headers=auth("eye")).status_code == 403
        assert c.get("/api/hv-support/harv1", headers=auth("ops")).status_code == 403
        d = c.get("/api/hv-support/harv1", headers=auth("adm")).get_json()
    assert d["bundles"][0]["progress"] == 40 and d["kubeconfigs"][0]["name"] == "ci"
    assert d["namespaces"] == ["default"] and d["roles"][0] == {"name": "view", "exists": True, "reads_secrets": True}


def test_a_kubeconfig_is_downloaded_once_and_never_logged(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/hv-support/harv1/do/kc-create", json={"name": "x", "role": "god"},
                      headers=auth("adm")).status_code == 400
        r = c.post("/api/hv-support/harv1/do/kc-create", json={"name": "ci2", "role": "view", "namespace": "default",
                                                               "duration": "1h"}, headers=auth("adm"))
        assert r.status_code == 202
        token = r.get_json()["download"]
        label, cmd, _ = world["actions"][0]
        assert token not in " ".join(cmd) and label == "kubeconfig:create:ci2"
        out = cmd[cmd.index("--out") + 1]
        assert c.get(f"/api/hv-support/harv1/kubeconfig/{token}", headers=auth("ops")).status_code == 403
        got = c.get(f"/api/hv-support/harv1/kubeconfig/{token}", headers=auth("adm"))
        assert got.status_code == 200 and "token: SECRET" in got.get_data(as_text=True)
        assert got.headers["Cache-Control"] == "no-store" and not Path(out).exists()
        assert c.get(f"/api/hv-support/harv1/kubeconfig/{token}", headers=auth("adm")).status_code == 404
        assert c.post("/api/hv-support/harv1/do/kc-revoke", json={"name": "ci2"}, headers=auth("adm")).status_code == 202


def test_bundles_are_created_and_downloaded_when_ready(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/hv-support/harv1/do/bundle-create", json={"spec": {}}, headers=auth("adm")).status_code == 400
        r = c.post("/api/hv-support/harv1/do/bundle-create", json={"spec": {"description": "slow", "namespaces": ["default"]}},
                   headers=auth("adm"))
        assert r.status_code == 202 and r.get_json()["name"].startswith("bundle-")
        assert c.get("/api/hv-support/harv1/bundle/bundle-a/download", headers=auth("adm")).status_code == 409
        assert c.post("/api/hv-support/harv1/do/bundle-delete", json={"name": "bundle-a"}, headers=auth("adm")).status_code == 202
    _, cmd, files = world["actions"][0]
    assert cmd[2:4] == ["supportbundle", "create"] and json.loads(list(files.values())[0])["description"] == "slow"
