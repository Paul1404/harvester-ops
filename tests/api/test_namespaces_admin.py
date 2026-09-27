"""v1.62.0 : le menu Namespaces de Harvester (bin/lib/hv_ns.py,
bin/harvester-resources.py namespace et les routes /api/ns-admin).

Description dans field.cattle.io/description, quota d'instantanés dans la
ResourceQuota `default-resource-quota` de Harvester ; ce que Kubernetes et
Rancher posent n'est jamais touché ; un namespace du système ne se supprime pas.
"""

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_ns as hn  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_ns", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)


def a_ns(name="team-a", labels=None, ann=None):
    return {"metadata": {"name": name,
                         "labels": {"kubernetes.io/metadata.name": name, **(labels or {})},
                         "annotations": {"cattle.io/status": "{}", hn.DESC: "old", **(ann or {})}}}


def test_manifest_and_update_keep_what_kubernetes_and_rancher_own():
    m = hn.manifest("team-a", "Équipe A", {"env": "prod"})
    assert m["metadata"] == {"name": "team-a", "annotations": {hn.DESC: "Équipe A"}, "labels": {"env": "prod"}}
    with pytest.raises(ValueError, match="namespace name"):
        hn.manifest("Team_A")
    ns = a_ns(labels={"env": "prod", "tier": "web"}, ann={"owner": "ops"})
    p = hn.update_patch(ns, "Nouvelle", {"env": "dev"}, {})
    assert p["metadata"]["annotations"] == {hn.DESC: "Nouvelle", "owner": None}
    assert p["metadata"]["labels"] == {"env": "dev", "tier": None}          # le label système n'est pas touché
    with pytest.raises(ValueError, match="managed"):
        hn.update_patch(ns, labels={"kubernetes.io/metadata.name": "x"})
    ovn = a_ns(ann={"ovn.kubernetes.io/logical_switch": "ovn-default", "lifecycle.cattle.io/create.namespace-auth": "true"})
    assert hn._visible(ovn["metadata"]["annotations"]) == {}                    # vu sur harv1 : kube-ovn annote
    assert hn.update_patch(ovn, annotations={"owner": "ju"})["metadata"]["annotations"] == {"owner": "ju"}
    with pytest.raises(ValueError, match="nothing"):
        hn.update_patch(ns)


def test_quota_and_system_namespaces():
    q = hn.quota_object(None, "team-a", 100 * 2**30)
    assert q["metadata"] == {"name": "default-resource-quota", "namespace": "team-a"}
    assert q["spec"]["snapshotLimit"]["namespaceTotalSnapshotSizeQuota"] == 100 * 2**30
    kept = hn.quota_object({"metadata": {"name": "default-resource-quota", "namespace": "team-a"},
                            "spec": {"snapshotLimit": {"namespaceTotalSnapshotSizeQuota": 5,
                                                       "vmTotalSnapshotSizeQuota": {"web": 1}}}, "status": {}}, "team-a", 0)
    assert kept["spec"]["snapshotLimit"] == {"vmTotalSnapshotSizeQuota": {"web": 1}} and "status" not in kept
    assert hn.quota_object(None, "team-a", 0) is None
    for name in ("kube-system", "harvester-system", "cattle-fleet-system", "longhorn-system", "p-abcde",
                 "caaph-system", "cluster-fleet-local-local-1a3d", "kubeovn-owner-namespace"):
        assert hn.is_system(name), name
    assert not hn.is_system("team-a") and not hn.is_system("default")      # default est montré (ses VMs)...
    assert hn.protected("default")                                         # ... mais ne se supprime pas
    with pytest.raises(ValueError, match="system"):
        hn.delete_check("default", a_ns("default"))
    assert hn.delete_check("team-a", a_ns(), [{"metadata": {"name": "web"}}], []) == {"vms": ["web"], "volumes": []}


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

    def create(self, obj):
        self.calls.append(("create", obj["kind"], obj["metadata"]["name"]))
        return obj

    def replace(self, obj):
        self.calls.append(("replace", obj["kind"], obj["metadata"]["name"]))
        return obj

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))
        self.objs.pop((kind, ns, name), None)


def nargs(**kw):
    base = {"name": "team-a", "description": None, "labels_file": None, "annotations_file": None, "size": None,
            "timeout": 30}
    base.update(kw)
    return type("A", (), base)()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)


def test_the_tool_creates_sets_the_quota_and_deletes(capsys):
    k = Kube({})
    assert hres.ns_create(k, nargs(description="A")) == hres.EXIT_OK
    assert ("create", "Namespace", "team-a") in k.calls
    k.objs[("namespaces", None, "team-a")] = a_ns()
    with pytest.raises(ValueError, match="already exists"):
        hres.ns_create(k, nargs())
    assert hres.ns_quota(k, nargs(size="50Gi")) == hres.EXIT_OK
    assert ("create", "ResourceQuota", "default-resource-quota") in k.calls
    k.objs[("virtualmachines.kubevirt.io", "team-a", "web")] = {"metadata": {"name": "web"}}
    assert hres.ns_delete(k, nargs()) == hres.EXIT_OK
    assert ("delete", "namespaces", "team-a") in k.calls
    assert "with 1 VM(s)" in capsys.readouterr().err


# -- les routes --------------------------------------------------------------------------

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
    monkeypatch.setattr(wapp, "_cluster_reachable", lambda kc: True)
    wapp._accounts().create("root", PW, "admin")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": []}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        files = {cmd[i + 1]: Path(cmd[i + 1]).read_text() for i, a in enumerate(cmd)
                 if a in ("--labels-file", "--annotations-file")}
        w["actions"].append((label, cmd, files))
        if after:
            after()

        class Run:
            id = "nsw000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    objs = {"namespaces": {"items": [a_ns("team-a", {"env": "prod"}), a_ns("kube-system")]},
            "virtualmachines.kubevirt.io": {"items": [{"metadata": {"name": "web", "namespace": "team-a"}}]},
            "persistentvolumeclaims": {"items": [{"metadata": {"name": "a", "namespace": "team-a"}},
                                                 {"metadata": {"name": "b", "namespace": "team-a"}}]},
            hn.K_QUOTA: {"items": [{"metadata": {"name": "default-resource-quota", "namespace": "team-a"},
                                    "spec": {"snapshotLimit": {"namespaceTotalSnapshotSizeQuota": 10}}}]}}
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: json.loads(json.dumps(objs.get(a[1]))))
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_list_counts_what_each_namespace_holds(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/ns-admin/harv1", headers=auth("eye")).get_json()
    rows = {r["name"]: r for r in d["items"]}
    assert rows["team-a"]["vms"] == 1 and rows["team-a"]["volumes"] == 2 and rows["team-a"]["snapshot_quota"] == 10
    assert rows["team-a"]["labels"] == {"env": "prod"} and rows["team-a"]["description"] == "old"
    assert "cattle.io/status" not in rows["team-a"]["annotations"]
    assert rows["kube-system"]["system"] and not rows["team-a"]["system"]
    assert d["items"][0]["name"] == "team-a"                                  # les namespaces du système à la fin


def test_writes_are_for_admins_and_checked(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/ns-admin/harv1", json={"name": "team-b"}, headers=auth("eye")).status_code == 403
        assert c.post("/api/ns-admin/harv1", json={"name": "Team B"}, headers=auth("root")).status_code == 400
        assert c.post("/api/ns-admin/harv1/kube-system/do/delete", headers=auth("root")).status_code == 400
        assert c.post("/api/ns-admin/harv1/team-a/do/quota", json={"size": "lots"}, headers=auth("root")).status_code == 400
        assert c.post("/api/ns-admin/harv1", json={"name": "team-b", "description": "B", "labels": {"env": "dev"}},
                      headers=auth("root")).status_code == 202
        assert c.post("/api/ns-admin/harv1/team-a/do/update", json={"description": "A", "labels": {}, "annotations": {"o": "x"}},
                      headers=auth("root")).status_code == 202
        assert c.post("/api/ns-admin/harv1/team-a/do/quota", json={"size": "20Gi"}, headers=auth("root")).status_code == 202
        assert c.post("/api/ns-admin/harv1/team-a/do/delete", headers=auth("root")).status_code == 202
    labels = [a[0] for a in world["actions"]]
    assert labels == ["namespace:create:team-b", "namespace:update:team-a", "namespace:quota:team-a",
                      "namespace:delete:team-a"]
    create = world["actions"][0]
    assert create[1][-4:-2] == ["--description", "B"] and list(create[2].values()) == ['{"env": "dev"}']
    assert not any(Path(f).exists() for a in world["actions"] for f in a[2])
    assert world["actions"][2][1][-2:] == ["--size", "20Gi"]
