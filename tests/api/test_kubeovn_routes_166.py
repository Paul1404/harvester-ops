"""v1.66.0 : les routes de l'underlay, du NAT et des politiques kube-ovn.

Lecture de l'état en un passage (santé de kube-ovn comprise), politiques et
VMs par namespace ; écritures réservées aux administrateurs, contrôlées
d'avance, passées à bin/harvester-network.py."""

import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402
import ovn_extra as ox  # noqa: E402

PW = "a long test password"
NP = ox.network_policy({"name": "np1", "namespace": "default", "vms": ["web"],
                        "ingress": [{"peers": [{"kind": "cidr", "cidr": "172.16.0.0/16"}], "ports": [{"port": 22}]}]})


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
    monkeypatch.setattr(wapp, "BIN_DIR", ROOT / "bin")
    wapp._accounts().create("adm", PW, "admin")
    wapp._accounts().create("ops", PW, "operator")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": []}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        w["actions"].append((label, cmd, spec))

        class Run:
            id = "ovn000000166"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    data = {
        ox.K_PN: [{"metadata": {"name": "ext"}, "spec": {"defaultInterface": "enp4s0"},
                   "status": {"ready": True, "readyNodes": ["n1"]}}],
        ox.K_VLAN: [{"metadata": {"name": "ext-v0"}, "spec": {"id": 0, "provider": "ext"}}],
        ox.K_SUBNET: [{"metadata": {"name": "sn1"}, "spec": {"vpc": "vpc1", "cidrBlock": "10.1.0.0/24", "gateway": "10.1.0.1",
                                                           "provider": "sn1.default.ovn"}},
                      {"metadata": {"name": "join"}, "spec": {"vpc": "ovn-cluster"}}],
        ox.K_VPC: [{"metadata": {"name": "vpc1"}}, {"metadata": {"name": "ovn-cluster"}}],
        "deployments": [{"metadata": {"name": "ovn-central"}, "spec": {"replicas": 3}, "status": {"readyReplicas": 3}}],
        "nodes": [{"metadata": {"name": "n1", "annotations": {"ovn.kubernetes.io/ip_address": "100.64.0.2"}}}],
        "linkmonitors.network.harvesterhci.io": {"status": {"linkStatus": {"n1": [{"name": "enp1s0", "masterIndex": 3},
                                                                                  {"name": "enp4s0"}]}}},
    }

    def kjson(kc, *a, **k):
        what = a[1]
        if what == "linkmonitors.network.harvesterhci.io":
            return data[what]
        if what.startswith("networkpolicies"):
            return {"items": [{"kind": "NetworkPolicy", **NP},
                              {"kind": "VirtualMachine", "metadata": {"namespace": "default", "name": "web"}},
                              {"kind": "NetworkPolicy", "metadata": {"namespace": "kube-system", "name": "sys"}, "spec": {}}]}
        return {"items": data.get(what, [])}
    monkeypatch.setattr(wapp, "_kubectl_json", kjson)
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_underlay_and_nat_state_reads_at_once(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/kubeovn/harv1/extra", headers=auth("eye")).get_json()
    assert d["kubeovn"] and d["health"]["healthy"]
    assert d["providers"][0]["name"] == "ext" and d["vlans"][0]["id"] == 0
    assert d["tenant_subnets"] == [{"name": "sn1", "vpc": "vpc1", "cidr": "10.1.0.0/24", "gateway": "10.1.0.1", "network": "default/sn1"}]
    assert {n["name"]: n["taken_on"] for n in d["nics"]} == {"enp1s0": ["n1"], "enp4s0": []}


def test_policies_come_with_the_vms_to_target_and_hide_system_ones(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/kubeovn/harv1/policies", headers=auth("eye")).get_json()
    assert [r["name"] for r in d["items"]] == ["np1"] and d["vms"] == {"default": ["web"]}
    assert d["items"][0]["spec"]["vms"] == ["web"]


def test_writes_are_checked_and_for_administrators(world):
    with wapp.app.test_client() as c:
        spec = {"name": "ext", "interface": "enp4s0"}
        assert c.post("/api/kubeovn/harv1/extra/provider", json={"spec": spec}, headers=auth("ops")).status_code == 403
        assert c.post("/api/kubeovn/harv1/extra/explode", json={"spec": spec}, headers=auth("adm")).status_code == 400
        assert c.post("/api/kubeovn/harv1/extra/provider", json={"spec": {"name": "int", "interface": "x"}},
                      headers=auth("adm")).status_code == 400
        assert c.post("/api/kubeovn/harv1/extra/snat", json={"spec": {"name": "s", "eip": "e", "internal_cidr": "a,b"}},
                      headers=auth("adm")).status_code == 400
        r = c.post("/api/kubeovn/harv1/extra/gateway", json={"spec": {"name": "g"}, "update": True}, headers=auth("adm"))
        assert r.status_code == 400 and "freezes" in r.get_json()["error"]
        assert c.post("/api/kubeovn/harv1/extra/provider", json={"spec": spec}, headers=auth("adm")).status_code == 202
        assert c.post("/api/kubeovn/harv1/extra/policy", json={"spec": {"name": "np1", "namespace": "default", "vms": ["web"],
                                                                        "ingress": []}, "update": True},
                      headers=auth("adm")).status_code == 202
        assert c.delete("/api/kubeovn/harv1/extra/policy/np1", headers=auth("adm")).status_code == 400
        assert c.delete("/api/kubeovn/harv1/extra/policy/np1?namespace=default", headers=auth("adm")).status_code == 202
        assert c.delete("/api/kubeovn/harv1/extra/eip/eip1", headers=auth("adm")).status_code == 202
    label, cmd, spec = world["actions"][0]
    assert label == "network:provider-create:ext" and cmd[2:3] == ["apply"] and cmd[-2:] == ["--kind", "provider"] and spec["interface"] == "enp4s0"
    assert world["actions"][1][0] == "network:policy-update:default/np1" and world["actions"][1][1][-1] == "--update"
    assert world["actions"][2][1][-6:] == ["--kind", "policy", "--name", "np1", "--namespace", "default"]
    assert world["actions"][3][0] == "network:eip-delete:eip1"
