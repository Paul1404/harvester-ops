"""v1.73.0 : membres Rancher côté commande et côté routes : ajout avec un
rôle du bon contexte, retrait refusé pour un compte système ou le dernier
propriétaire (avant tout DELETE), liste nommée et rôles lus avec le jeton
de la session, recherche des utilisateurs et groupes, 409 sans session."""

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
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_mb", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)
PW = "a long test password"
CID = "c-sg2q6"
CRTBS = [{"id": f"{CID}:creator-cluster-owner", "name": "creator-cluster-owner", "userId": "user-kk67j",
          "userPrincipalId": "local://user-kk67j", "roleTemplateId": "cluster-owner"},
         {"id": f"{CID}:u-sys-admin", "name": "u-sys-admin", "userId": "u-sys", "roleTemplateId": "cluster-owner"},
         {"id": f"{CID}:crtb-abc12", "name": "crtb-abc12", "userId": "u-jn", "userPrincipalId": "keycloakoidc_user://jn",
          "roleTemplateId": "nodes-view"}]
USERS = {"user-kk67j": {"displayName": "Default Admin", "username": "admin"}, "u-sys": {"displayName": "System account for Cluster c-sg2q6"},
         "u-jn": {"displayName": "Julien Niedergang"}}
ROLES = {"cluster-member": {"id": "cluster-member", "name": "Cluster Member", "context": "cluster"},
         "nodes-view": {"id": "nodes-view", "name": "View Nodes", "context": "cluster"},
         "read-only": {"id": "read-only", "name": "Read-only", "context": "project"}}


class Rancher:
    def __init__(self):
        self.calls = []

    def __call__(self, r, method, path, body=None):
        self.calls.append((method, path, body))
        if path.startswith("/v3/roletemplates/"):
            return 200, ROLES[path.rsplit("/", 1)[1]]
        if path.startswith("/v3/users/"):
            return 200, USERS[path.rsplit("/", 1)[1]]
        if method == "GET" and path.startswith("/v3/clusterroletemplatebindings"):
            return 200, {"data": CRTBS}
        return 200, {}


@pytest.fixture
def cli(monkeypatch):
    rancher = Rancher()
    monkeypatch.setattr(hres, "_rancher_session", lambda kc: {"url": "https://rancher.lan", "cid": CID, "token": "t"})
    monkeypatch.setattr(hres, "_rancher_call", rancher)

    def run(action, **kw):
        a = argparse.Namespace(**{"action": action, "kubeconfig": "/kc", "cluster": None, "scope": "cluster", "project": None,
                                  "principal": None, "role": None, "id": None, **kw})
        return hres.cmd_member(a)
    return run, rancher


def test_a_member_is_added_with_a_role_of_the_right_context(cli):
    run, rancher = cli
    assert run("add", principal="keycloakoidc_group://ops", role="cluster-member") == hres.EXIT_OK
    assert rancher.calls[-1] == ("POST", "/v3/clusterroletemplatebindings", {
        "type": "clusterRoleTemplateBinding", "clusterId": CID, "roleTemplateId": "cluster-member",
        "groupPrincipalId": "keycloakoidc_group://ops"})
    with pytest.raises(ValueError, match="not a role for a cluster"):
        run("add", principal="local://u-x", role="read-only")
    assert run("add", scope="project", project="p-abcde", principal="local://u-x", role="read-only") == hres.EXIT_OK
    assert rancher.calls[-1][1] == "/v3/projectroletemplatebindings" and rancher.calls[-1][2]["projectId"] == f"{CID}:p-abcde"


def test_system_accounts_and_the_last_owner_are_refused_before_any_delete(cli):
    run, rancher = cli
    with pytest.raises(ValueError, match="system account"):
        run("remove", id=f"{CID}:u-sys-admin")
    with pytest.raises(ValueError, match="last cluster-owner"):
        run("remove", id=f"{CID}:creator-cluster-owner")
    assert not any(c[0] == "DELETE" for c in rancher.calls)
    assert run("remove", id=f"{CID}:crtb-abc12") == hres.EXIT_OK
    assert rancher.calls[-1][:2] == ("DELETE", f"/v3/clusterroletemplatebindings/{CID}:crtb-abc12")


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
    monkeypatch.setattr(wapp, "load_config", lambda: {"clusters": [{"name": "harv1", "kubeconfig": "/kc"}]})
    wapp._accounts().create("ops", PW, "operator")
    tok = tmp_path / "tok"
    tok.write_text("token-xyz")
    session_kc = tmp_path / "session.json"
    session_kc.write_text(json.dumps({"clusters": [{"cluster": {"server": f"https://rancher.lan/k8s/clusters/{CID}"}}],
                                      "users": [{"user": {"tokenFile": str(tok)}}]}))
    direct = tmp_path / "direct.yaml"
    direct.write_text("clusters:\n- cluster: {server: 'https://10.0.0.1:6443'}\nusers:\n- user: {token: x}\n")
    w = {"actions": [], "kc": str(session_kc), "direct": str(direct), "http": []}
    monkeypatch.setattr(wapp, "_kubectl_for_cluster", lambda c: w["kc"])

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        w["actions"].append((label, cmd))

        class Run:
            id = "mb0000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)

    def fake_request(self, method, url, headers=None, data=None):
        w["http"].append((method, url, data))
        path = url.split("rancher.lan", 1)[1]
        if path.startswith("/v3/clusterroletemplatebindings"):
            return 200, {"data": CRTBS}
        if path.startswith("/v3/roletemplates"):
            return 200, {"data": list(ROLES.values())}
        if path.startswith("/v3/users/"):
            return 200, USERS[path.rsplit("/", 1)[1]]
        if path.startswith("/v3/principals/"):
            return 404, {}
        if path.startswith("/v3/principals?action=search"):
            return 200, {"data": [{"id": "keycloakoidc_user://jn", "name": "Julien Niedergang", "loginName": "jn", "principalType": "user"},
                                  {"id": "keycloakoidc_group://ops", "name": "ops", "principalType": "group"}]}
        return 404, {}
    monkeypatch.setattr(wapp._rs.Http, "request", fake_request)
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_members_are_listed_named_with_their_roles(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/rancher-members/harv1?scope=cluster", headers=auth("ops")).get_json()
    assert d["managed"] and [r["id"] for r in d["roles"]] == ["cluster-member", "nodes-view"]
    assert [(m["name"], m["role_name"], m["system"]) for m in d["members"]] == [
        ("Default Admin", "cluster-owner", False), ("Julien Niedergang", "View Nodes", False),
        ("System account for Cluster c-sg2q6", "cluster-owner", True)]


def test_principals_are_searched_through_rancher(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/rancher-principals/harv1?q=jul", headers=auth("ops")).get_json()
    assert [(p["id"], p["kind"], p["provider"]) for p in d["items"]] == [
        ("keycloakoidc_user://jn", "user", "keycloakoidc_user"), ("keycloakoidc_group://ops", "group", "keycloakoidc_group")]
    method, url, data = world["http"][-1]
    assert method == "POST" and url.endswith("/v3/principals?action=search") and json.loads(data) == {"name": "jul"}


def test_writes_go_to_the_command_and_need_a_rancher_session(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/rancher-members/harv1/do/add", json={"principal": "jn", "role": "cluster-member"},
                      headers=auth("ops")).status_code == 400
        assert c.post("/api/rancher-members/harv1/do/add", json={"principal": "keycloakoidc_user://jn", "role": "nodes-view"},
                      headers=auth("ops")).status_code == 202
        assert c.post("/api/rancher-members/harv1/do/remove", json={"scope": "project", "project": "p-abcde", "id": f"{CID}:prtb-1"},
                      headers=auth("ops")).status_code == 202
        world["kc"] = world["direct"]
        r = c.post("/api/rancher-members/harv1/do/add", json={"principal": "keycloakoidc_user://jn", "role": "nodes-view"},
                   headers=auth("ops"))
        assert r.status_code == 409 and r.get_json()["code"] == "rancher-session-needed"
        assert c.get("/api/rancher-members/harv1", headers=auth("ops")).get_json()["managed"] is False
    (l1, c1), (l2, c2) = world["actions"]
    assert l1 == "member:add:cluster:nodes-view" and c1[-4:] == ["--principal", "keycloakoidc_user://jn", "--role", "nodes-view"]
    assert c2[-6:] == ["--scope", "project", "--project", "p-abcde", "--id", f"{CID}:prtb-1"]
