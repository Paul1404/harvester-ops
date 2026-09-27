"""v1.65.0 : le menu Networks, côté script et côté routes.

Le script refuse d'avance ce que Harvester refuserait (VMs en marche sous un
lien qui change, réseau encore utilisé, pool qui a des adresses allouées,
réseau de stockage avec des VMs en marche) ; les routes sont réservées aux
administrateurs, contrôlent la demande, la passent par un fichier privé
effacé après l'action."""

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
import hv_net as hn  # noqa: E402
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_net", ROOT / "bin" / "harvester-resources.py")
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

    def create(self, obj):
        self.calls.append(("create", obj["kind"], obj["metadata"]["name"]))
        return obj

    def replace(self, obj):
        self.calls.append(("replace", obj.get("kind"), obj["metadata"]["name"]))
        return obj

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))
        self.objs.pop((kind, ns, name), None)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    wait = hres._wait
    monkeypatch.setattr(hres, "_wait", lambda *a, **kw: wait(*a, **{"sleep": lambda s: None, **kw}))


def args(tmp_path, **kw):
    if "spec" in kw and isinstance(kw["spec"], dict):
        p = tmp_path / "spec.json"
        p.write_text(json.dumps(kw["spec"]))
        kw["spec"] = str(p)
    base = {"action": None, "name": None, "namespace": "default", "spec": None, "target": None, "ip": None,
            "description": None, "timeout": 5}
    return argparse.Namespace(**{**base, **kw})


NAD = {"metadata": {"namespace": "default", "name": "v20", "labels": {hn.CN_LABEL: "data"}},
       "spec": {"config": '{"type":"bridge","bridge":"data-br","vlan":20}'}}
VMI = {"metadata": {"namespace": "default", "name": "web"}, "status": {"nodeName": "n1"},
       "spec": {"networks": [{"multus": {"networkName": "default/v20"}}]}}
NODE = {"metadata": {"name": "n1", "labels": {"kubernetes.io/hostname": "n1"}}}
CUR_VC = hn.vlan_config({"name": "data-all", "cluster_network": "data", "nics": ["enp4s0"]})


def test_changing_the_uplink_under_running_vms_is_refused(tmp_path):
    k = Kube({(hn.K_VC, None, "data-all"): CUR_VC, (hn.K_NAD, "default", "v20"): NAD, ("nodes", None, "n1"): NODE,
              ("virtualmachineinstances.kubevirt.io", "default", "web"): VMI})
    with pytest.raises(ValueError, match="default/web"):
        _run(hres.cmd_netconfig, k, args(tmp_path, action="update", spec={
            "name": "data-all", "cluster_network": "data", "nics": ["enp4s0", "enp5s0"]}))
    assert not any(c[0] == "replace" for c in k.calls)


def _run(fn, kube, a):
    """Les commandes lisent leur Kube par kube_from : on le leur substitue."""
    orig = hres.kube_from
    hres.kube_from = lambda _a: kube
    try:
        return fn(a)
    finally:
        hres.kube_from = orig


def test_a_description_change_goes_through_with_vms_running(tmp_path):
    k = Kube({(hn.K_VC, None, "data-all"): CUR_VC, (hn.K_NAD, "default", "v20"): NAD, ("nodes", None, "n1"): NODE,
              ("virtualmachineinstances.kubevirt.io", "default", "web"): VMI,
              (hn.K_VS, None, "vs1"): {"status": {"vlanConfig": "data-all", "node": "n1",
                                                  "conditions": [{"type": "ready", "status": "True"}]}}})
    assert _run(hres.cmd_netconfig, k, args(tmp_path, action="update", spec={
        "name": "data-all", "cluster_network": "data", "nics": ["enp4s0"], "description": "rack a"})) == hres.EXIT_OK
    assert ("replace", "VlanConfig", "data-all") in k.calls


def test_a_cluster_network_in_use_is_not_deleted(tmp_path):
    k = Kube({(hn.K_CN, None, "data"): {"metadata": {"name": "data"}}, (hn.K_VC, None, "data-all"): CUR_VC})
    with pytest.raises(ValueError, match="configurations"):
        _run(hres.cmd_clusternetwork, k, args(tmp_path, action="delete", name="data"))
    k = Kube({(hn.K_CN, None, "data"): {"metadata": {"name": "data"}}, (hn.K_NAD, "default", "v20"): NAD})
    with pytest.raises(ValueError, match="default/v20"):
        _run(hres.cmd_clusternetwork, k, args(tmp_path, action="delete", name="data"))
    k = Kube({(hn.K_CN, None, "data"): {"metadata": {"name": "data"}}})
    assert _run(hres.cmd_clusternetwork, k, args(tmp_path, action="delete", name="data")) == hres.EXIT_OK


def test_a_vm_network_changes_its_vlan_only_with_its_vms_stopped(tmp_path):
    nad = {"metadata": {"namespace": "default", "name": "v20", "labels": {hn.TYPE_LABEL: "L2VlanNetwork"}},
           "spec": {"config": '{"type":"bridge","bridge":"data-br","vlan":20}'}}
    k = Kube({(hn.K_NAD, "default", "v20"): nad, ("virtualmachineinstances.kubevirt.io", "default", "web"): VMI})
    with pytest.raises(ValueError, match="stopped: default/web"):
        _run(hres.cmd_vmnet, k, args(tmp_path, action="update", name="v20", spec={"vlan": 30}))
    assert _run(hres.cmd_vmnet, k, args(tmp_path, action="update", name="v20", spec={"vlan": 20, "description": "d"})) == hres.EXIT_OK


def test_the_storage_network_waits_for_every_vm_to_be_stopped(tmp_path):
    k = Kube({("virtualmachineinstances.kubevirt.io", "default", "web"): VMI,
              (hn.K_SETTING, None, "storage-network"): {"metadata": {"name": "storage-network", "resourceVersion": "1"}}})
    with pytest.raises(ValueError, match="every VM stopped"):
        _run(hres.cmd_netsetting, k, args(tmp_path, action="set", name="storage-network",
                                          spec={"cluster_network": "data", "vlan": 100, "range": "10.100.0.0/24"}))
    # VMs arrêtées mais un volume encore attaché : Harvester refuserait aussi
    k.objs.pop(("virtualmachineinstances.kubevirt.io", "default", "web"))
    k.objs[("volumes.longhorn.io", "longhorn-system", "pvc-a")] = {
        "metadata": {"name": "pvc-a"}, "status": {"state": "attached", "kubernetesStatus": {"namespace": "default", "pvcName": "web-disk"}}}
    with pytest.raises(ValueError, match="every volume detached; still attached: default/web-disk"):
        _run(hres.cmd_netsetting, k, args(tmp_path, action="set", name="storage-network",
                                          spec={"cluster_network": "data", "vlan": 100, "range": "10.100.0.0/24"}))
    k.objs.pop(("volumes.longhorn.io", "longhorn-system", "pvc-a"))
    # revenir au réseau de gestion ne demande rien
    seq = iter([{"metadata": {"name": "storage-network", "resourceVersion": "1"}},
                {"metadata": {"name": "storage-network", "resourceVersion": "2"},
                 "status": {"conditions": [{"type": "configured", "status": "True", "reason": "Completed"}]}}])
    first = next(seq)
    k.get = lambda kind, ns, name: first if not k.calls else next(seq, None) or {
        "metadata": {"resourceVersion": "2"}, "status": {"conditions": [{"type": "configured", "status": "True", "reason": "Completed"}]}}
    assert _run(hres.cmd_netsetting, k, args(tmp_path, action="clear", name="storage-network")) == hres.EXIT_OK
    assert k.calls[0] == ("patch", hn.K_SETTING, "storage-network", {"value": ""})


def test_a_pool_with_allocations_is_released_before_deletion(tmp_path):
    pool = hn.ip_pool({"name": "lan", "ranges": [{"subnet": "10.0.0.0/24"}]})
    pool["status"] = {"allocated": {"10.0.0.7": "default/web"}}
    k = Kube({(hn.K_POOL, None, "lan"): pool})
    with pytest.raises(ValueError, match="release"):
        _run(hres.cmd_ippool, k, args(tmp_path, action="delete", name="lan"))
    k.get = lambda kind, ns, name: {"status": {"allocated": {}}} if k.calls else json.loads(json.dumps(pool))
    assert _run(hres.cmd_ippool, k, args(tmp_path, action="release", name="lan", ip="10.0.0.7")) == hres.EXIT_OK
    assert k.calls[0] == ("patch", hn.K_POOL, "lan", {"metadata": {"annotations": {hn.RELEASE: "10.0.0.7: default/web"}}})


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
    wapp._accounts().create("adm", PW, "admin")
    wapp._accounts().create("ops", PW, "operator")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": []}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        files = {cmd[i + 1]: Path(cmd[i + 1]).read_text() for i, a in enumerate(cmd) if a == "--spec"}
        w["actions"].append((label, cmd, files))
        if after:
            after()

        class Run:
            id = "net000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    items = [{"kind": "ClusterNetwork", "metadata": {"name": "mgmt"}},
             {"kind": "ClusterNetwork", "metadata": {"name": "data"}, "status": {"conditions": [{"type": "ready", "status": "True"}]}},
             {"kind": "VlanConfig", **CUR_VC}, {"kind": "NetworkAttachmentDefinition", **NAD},
             {"kind": "Node", **NODE},
             {"kind": "Setting", "metadata": {"name": "storage-network"}, "value": ""},
             {"kind": "Setting", "metadata": {"name": "ntp-servers"}, "value": "x"}]
    lm = {"status": {"linkStatus": {"n1": [{"name": "enp1s0", "state": "up", "masterIndex": 3},
                                           {"name": "enp4s0", "state": "up"}]}}}
    lbs = [hn.load_balancer({"name": "web", "listeners": [{"port": 80, "backend_port": 80}]})]

    def kjson(kc, *a, **k):
        if a[1] == hn.K_LM:
            return lm
        if a[1] == hn.K_LB:
            return {"items": [{"kind": "LoadBalancer", **x} for x in lbs]}
        if a[1] == "nodes":
            return {"items": [NODE]}
        return {"items": json.loads(json.dumps(items))}
    monkeypatch.setattr(wapp, "_kubectl_json", kjson)
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_cluster_networks_tab_reads_everything_at_once(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/net-admin/harv1", headers=auth("eye")).get_json()
        nics = c.get("/api/net-admin/harv1/nics?nodes=n1", headers=auth("eye")).get_json()
        lb = c.get("/api/cluster-objects/harv1/loadbalancers", headers=auth("eye")).get_json()
        assert c.get("/api/net-admin/harv1/nics?nodes=Bad!", headers=auth("eye")).status_code == 400
    assert [r["name"] for r in d["cluster_networks"]] == ["mgmt", "data"]
    assert d["cluster_networks"][1]["configs"][0]["name"] == "data-all"
    assert [s["name"] for s in d["settings"]] == ["storage-network", "vm-migration-network", "rwx-network"]
    assert {n["name"]: n["usable"] for n in nics["nics"]} == {"enp1s0": False, "enp4s0": True}
    assert lb["items"][0]["name"] == "web" and lb["items"][0]["ipam"] == "dhcp"


def test_network_actions_are_for_administrators_and_checked_first(world):
    spec = {"name": "data-all", "cluster_network": "data", "nics": ["enp4s0"]}
    with wapp.app.test_client() as c:
        assert c.post("/api/net-admin/harv1/do/config-create", json={"spec": spec}, headers=auth("ops")).status_code == 403
        assert c.post("/api/net-admin/harv1/do/explode", json={}, headers=auth("adm")).status_code == 400
        r = c.post("/api/net-admin/harv1/do/config-create", json={"spec": {**spec, "nics": []}}, headers=auth("adm"))
        assert r.status_code == 400 and "network card" in r.get_json()["error"]
        assert c.post("/api/net-admin/harv1/do/cn-create", json={"name": "much-too-long-name"}, headers=auth("adm")).status_code == 400
        assert c.post("/api/net-admin/harv1/do/config-create", json={"spec": spec}, headers=auth("adm")).status_code == 202
        assert c.post("/api/net-admin/harv1/do/config-migrate", json={"name": "data-all", "target": "data2"},
                      headers=auth("adm")).status_code == 202
        assert c.post("/api/net-admin/harv1/do/setting-set", json={"name": "storage-network", "spec": {
            "cluster_network": "mgmt", "vlan": 1, "range": "10.1.0.0/24"}}, headers=auth("adm")).status_code == 400
        assert c.post("/api/net-admin/harv1/do/pool-release", json={"name": "lan", "ip": "nope"}, headers=auth("adm")).status_code == 400
        assert c.post("/api/net-admin/harv1/do/lb-delete", json={"namespace": "default", "name": "web"},
                      headers=auth("adm")).status_code == 202
    label, cmd, files = world["actions"][0]
    assert label == "netconfig:create:data-all" and cmd[2:4] == ["netconfig", "create"]
    assert json.loads(list(files.values())[0]) == spec
    assert not any(Path(f).exists() for f in files)                    # le fichier privé est effacé
    assert world["actions"][1][1][-4:] == ["--name", "data-all", "--target", "data2"]
    assert world["actions"][2][0] == "lb:delete:default/web"
