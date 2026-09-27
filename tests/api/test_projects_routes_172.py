"""v1.72.0 : projets Rancher côté commande et côté routes : écritures par
l'API de Rancher avec le jeton de la session (jamais sans session Rancher),
projets Default et System gardés, projet encore peuplé non supprimé,
namespace déplacé par ses annotation et label, quota de namespace borné et
attendu jusqu'à ce que Rancher l'applique, projets lus pour la liste des
namespaces avec les annotations d'un autre cluster signalées."""

import argparse
import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_projects as pj  # noqa: E402
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_pj", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)
PW = "a long test password"
CID = "c-sg2q6"
TEAM = {"id": f"{CID}:p-abcde", "clusterId": CID, "name": "Team A",
        "resourceQuota": {"limit": {"limitsCpu": "4000m"}}, "namespaceDefaultResourceQuota": {"limit": {"limitsCpu": "2000m"}}}
DEFAULT = {"id": f"{CID}:p-nc8d2", "clusterId": CID, "name": "Default", "labels": {"authz.management.cattle.io/default-project": "true"}}


class Rancher:
    def __init__(self):
        self.calls = []

    def __call__(self, r, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path.endswith("p-abcde"):
            return 200, TEAM
        if method == "GET" and path.endswith("p-nc8d2"):
            return 200, DEFAULT
        if method == "POST":
            return 201, {"id": f"{CID}:p-new01"}
        return 200, {}


class Kube:
    def __init__(self, namespaces):
        self.ns = {n["metadata"]["name"]: n for n in namespaces}
        self.runs = []

    def list(self, kind, ns=None, selector=None):
        return list(self.ns.values())

    def get(self, kind, ns, name):
        o = self.ns.get(name)
        if o is None:
            return None
        o = json.loads(json.dumps(o))
        ann = o["metadata"].setdefault("annotations", {})
        if pj.ANN_QUOTA in ann:          # Rancher valide le quota à la relecture
            ann[pj.ANN_STATUS] = json.dumps({"Conditions": [{"Type": "ResourceQuotaValidated", "Status": "True"}]})
        return o

    def run(self, *args, input=None, timeout=None):
        self.runs.append(args)
        if args[0] == "annotate":
            ann = self.ns[args[2]]["metadata"].setdefault("annotations", {})
            for kv in args[3:]:
                if kv.endswith("-") and "=" not in kv:
                    ann.pop(kv[:-1], None)
                elif "=" in kv:
                    k, v = kv.split("=", 1)
                    ann[k] = v
        return ""


def ns(name, pid=None):
    return {"metadata": {"name": name, "annotations": {pj.ANN_PROJECT: f"{CID}:{pid}"} if pid else {}}}


@pytest.fixture
def cli(monkeypatch, tmp_path):
    rancher = Rancher()
    monkeypatch.setattr(hres, "_rancher_session", lambda kc: {"url": "https://rancher.lan", "cid": CID, "token": "t"})
    monkeypatch.setattr(hres, "_rancher_call", rancher)
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    wait = hres._wait
    monkeypatch.setattr(hres, "_wait", lambda *a, **kw: wait(*a, **{"sleep": lambda s: None, **kw}))

    def run(kube, action, spec=None, **kw):
        path = None
        if spec is not None:
            path = tmp_path / "spec.json"
            path.write_text(json.dumps(spec))
        a = argparse.Namespace(**{"action": action, "spec": str(path) if path else None, "id": None, "namespace": None,
                                  "kubeconfig": "/kc", "cluster": None, "timeout": 60, **kw})
        orig = hres.kube_from
        hres.kube_from = lambda _a: kube
        try:
            return hres.cmd_project(a)
        finally:
            hres.kube_from = orig
    return run, rancher


def test_a_project_is_created_through_rancher_with_its_quotas(cli):
    run, rancher = cli
    assert run(None, "create", {"name": "Team B", "quota": {"limitsCpu": "4"}, "ns_default": {"limitsCpu": "1"}}) == hres.EXIT_OK
    method, path, body = rancher.calls[-1]
    assert (method, path) == ("POST", "/v3/projects") and body["clusterId"] == CID
    assert body["resourceQuota"] == {"limit": {"limitsCpu": "4000m"}}


def test_default_system_and_populated_projects_are_kept(cli):
    run, rancher = cli
    with pytest.raises(ValueError, match="Default project: it stays"):
        run(Kube([]), "delete", id="p-nc8d2")
    with pytest.raises(ValueError, match="still holds namespaces \\(apps\\)"):
        run(Kube([ns("apps", "p-abcde")]), "delete", id="p-abcde")
    assert run(Kube([ns("other")]), "delete", id="p-abcde") == hres.EXIT_OK
    assert rancher.calls[-1][:2] == ("DELETE", f"/v3/projects/{CID}:p-abcde")


def test_a_namespace_moves_by_its_annotation_and_label(cli):
    run, _ = cli
    k = Kube([ns("apps")])
    assert run(k, "move", namespace="apps", id="p-abcde") == hres.EXIT_OK
    assert ("annotate", "namespace", "apps", f"{pj.ANN_PROJECT}={CID}:p-abcde", "--overwrite") in k.runs
    assert ("label", "namespace", "apps", f"{pj.ANN_PROJECT}=p-abcde", "--overwrite") in k.runs
    assert run(k, "move", namespace="apps") == hres.EXIT_OK
    assert ("annotate", "namespace", "apps", f"{pj.ANN_PROJECT}-", f"{pj.ANN_QUOTA}-") in k.runs


def test_a_namespace_quota_is_checked_then_waited_for(cli, capsys):
    run, _ = cli
    k = Kube([ns("apps", "p-abcde"), ns("loose")])
    with pytest.raises(ValueError, match="exceeds the project limit"):
        run(k, "ns-quota", {"limit": {"limitsCpu": "8"}}, namespace="apps")
    with pytest.raises(ValueError, match="not in a project"):
        run(k, "ns-quota", {"limit": {"limitsCpu": "1"}}, namespace="loose")
    assert run(k, "ns-quota", {"limit": {"limitsCpu": "3"}}, namespace="apps") == hres.EXIT_OK
    assert "quota applied by Rancher" in capsys.readouterr().err


# -- routes ------------------------------------------------------------------------

@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(wapp, "AUTH_OPEN_ALLOWED", False)
    monkeypatch.setattr(wapp, "HTPASSWD_PATH", tmp_path / "none")
    monkeypatch.setattr(wapp, "ROLES_PATH", tmp_path / "none.yaml")
    monkeypatch.setattr(wapp, "_roles_cache", {"mtime": None, "data": None})
    monkeypatch.setattr(wapp, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(wapp, "_ACCOUNTS", {"store": None})
    monkeypatch.setattr(wapp, "_LOCAL_SESSIONS", acc.LocalSessions())
    monkeypatch.setattr(wapp, "load_config", lambda: {"clusters": [{"name": "harv1", "kubeconfig": "/kc", "rancher_cluster": CID}]})
    monkeypatch.setattr(wapp, "_cluster_reachable", lambda kc: True)
    wapp._accounts().create("ops", PW, "operator")
    tok = tmp_path / "tok"
    tok.write_text("token-xyz")
    session_kc = tmp_path / "session.json"
    session_kc.write_text(json.dumps({"clusters": [{"cluster": {"server": f"https://rancher.lan/k8s/clusters/{CID}"}}],
                                      "users": [{"user": {"tokenFile": str(tok)}}]}))
    direct_kc = tmp_path / "direct.yaml"
    direct_kc.write_text("clusters:\n- cluster: {server: 'https://10.0.0.1:6443'}\nusers:\n- user: {token: x}\n")
    w = {"actions": [], "kc": str(session_kc), "direct": str(direct_kc), "rancher": []}
    monkeypatch.setattr(wapp, "_kubectl_for_cluster", lambda c: w["kc"])

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        files = {cmd[i + 1]: Path(cmd[i + 1]).read_text() for i, a in enumerate(cmd) if a == "--spec"}
        w["actions"].append((label, cmd, files))
        if after:
            after()

        class Run:
            id = "pj0000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    items = {"namespaces": {"items": [ns("apps", "p-abcde"),
                                      {"metadata": {"name": "default", "annotations": {pj.ANN_PROJECT: "c-qt5jz:p-kcgzz"}}},
                                      ns("loose")]}}
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: items.get(a[1], {"items": []}))

    def fake_request(self, method, url, headers=None, data=None):
        w["rancher"].append((method, url, headers))
        return 200, {"data": [TEAM, DEFAULT]}
    monkeypatch.setattr(wapp._rs.Http, "request", fake_request)
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_namespace_list_shows_projects_by_name_and_stale_annotations(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/ns-admin/harv1", headers=auth("ops")).get_json()
    assert d["projects"]["managed"] and d["projects"]["cid"] == CID
    assert [p["name"] for p in d["projects"]["items"]] == ["Default", "Team A"]
    by = {r["name"]: r["project"] for r in d["items"]}
    assert by["apps"]["name"] == "Team A" and by["default"]["state"] == "foreign" and by["loose"]["state"] == "none"
    method, url, headers = world["rancher"][-1]
    assert url == f"https://rancher.lan/v3/projects?clusterId={CID}" and headers["Authorization"] == "Bearer token-xyz"


def test_a_local_account_sees_the_grouping_but_cannot_manage_projects(world):
    world["kc"] = world["direct"]
    with wapp.app.test_client() as c:
        d = c.get("/api/ns-admin/harv1", headers=auth("ops")).get_json()
        assert not d["projects"]["managed"] and d["projects"]["items"] is None
        assert {r["name"]: r["project"]["state"] for r in d["items"]}["default"] == "foreign"   # id Rancher connu par la config
        r = c.post("/api/projects/harv1/do/create", json={"spec": {"name": "x"}}, headers=auth("ops"))
    assert r.status_code == 409 and r.get_json()["code"] == "rancher-session-needed"
    assert not world["rancher"]


def test_writes_are_checked_then_run_by_the_command(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/projects/harv1/do/create", json={"spec": {"name": "T", "quota": {"limitsCpu": "1"}}},
                      headers=auth("ops")).status_code == 400                 # défaut des namespaces manquant
        assert c.post("/api/projects/harv1/do/create", json={"spec": {"name": "Team B"}}, headers=auth("ops")).status_code == 202
        assert c.post("/api/projects/harv1/do/move", json={"namespace": "loose", "id": "p-abcde"}, headers=auth("ops")).status_code == 202
        assert c.post("/api/projects/harv1/do/ns-quota", json={"namespace": "apps", "limit": {"limitsCpu": "x"}},
                      headers=auth("ops")).status_code == 400
        assert c.post("/api/projects/harv1/do/delete", json={"id": "nope"}, headers=auth("ops")).status_code == 400
    (l1, c1, f1), (l2, c2, _) = world["actions"]
    assert l1 == "project:create:Team B" and c1[c1.index("--kubeconfig") + 1] == world["kc"]
    assert not any(Path(p).exists() for p in f1)
    assert l2 == "project:move:loose" and c2[-4:] == ["--id", "p-abcde", "--namespace", "loose"]
