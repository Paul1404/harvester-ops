"""v1.66.0 : la suite du menu kube-ovn de Harvester, en fonctions pures.

Underlay (réseau fournisseur, VLAN, réseau externe), NAT (passerelle de VPC,
IP externe, SNAT, DNAT) et politiques réseau visant des VMs, dans les formes
de kube-ovn v1.15.4 et de la doc de Harvester 1.9."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import ovn_extra as ox  # noqa: E402

LINKS = {"n1": [{"name": "enp1s0", "masterIndex": 3}, {"name": "enp4s0"}],
         "n2": [{"name": "enp1s0", "masterIndex": 3}, {"name": "enp4s0"}]}


def test_a_provider_network_never_takes_a_bonded_nic():
    taken = ox.taken_nics(LINKS)
    assert taken == {"enp1s0": ["n1", "n2"]}
    pn = ox.provider_network({"name": "ext", "interface": "enp4s0", "exclude_nodes": ["n3"]}, taken)
    assert pn["spec"] == {"defaultInterface": "enp4s0", "excludeNodes": ["n3"]}
    with pytest.raises(ValueError, match="already bonded on n1, n2"):
        ox.provider_network({"name": "ext", "interface": "enp1s0"}, taken)
    with pytest.raises(ValueError, match="12"):
        ox.provider_network({"name": "much-too-long", "interface": "enp4s0"}, taken)
    with pytest.raises(ValueError, match="reserved"):
        ox.provider_network({"name": "int", "interface": "enp4s0"}, taken)


def test_a_vlan_zero_is_untagged():
    assert ox.vlan({"name": "ext-v0", "id": 0, "provider": "ext"})["spec"] == {"id": 0, "provider": "ext"}
    with pytest.raises(ValueError):
        ox.vlan({"name": "v", "id": 4095, "provider": "ext"})


def test_only_the_kept_addresses_stay_free_in_the_lan():
    assert ox.exclude_outside("172.16.0.0/16", ["172.16.2.90"]) == ["172.16.0.1..172.16.2.89", "172.16.2.91..172.16.255.254"]
    assert ox.exclude_outside("10.0.0.0/29", ["10.0.0.1", "10.0.0.6"]) == ["10.0.0.2..10.0.0.5"]


def test_an_external_network_is_an_underlay_subnet_at_the_real_lan_prefix():
    nad, sub = ox.external_network({"name": "ext-lan", "vlan": "ext-v0", "cidr": "172.16.0.0/16", "gateway": "172.16.0.1",
                                    "free_start": "172.16.2.90", "free_end": "172.16.2.95"})
    cfg = json.loads(nad["spec"]["config"])
    assert nad["metadata"]["namespace"] == "kube-system" and cfg["type"] == "kube-ovn" and cfg["provider"] == "ext-lan.kube-system.ovn"
    assert sub["spec"]["cidrBlock"] == "172.16.0.0/16" and sub["spec"]["vlan"] == "ext-v0"
    assert sub["spec"]["excludeIps"] == ["172.16.0.1..172.16.2.89", "172.16.2.91..172.16.255.254"]
    assert ox.next_eip(sub, []) == "172.16.2.91" and ox.next_eip(sub, ["172.16.2.91"]) == "172.16.2.92"
    for bad, msg in (({"gateway": "10.0.0.1"}, "not in"), ({"free_start": "172.16.2.95", "free_end": "172.16.2.90"}, "first then last"),
                     ({"free_start": "172.16.0.1"}, "gateway"), ({"free_end": "172.16.2.90"}, "two free")):
        with pytest.raises(ValueError, match=msg):
            ox.external_network({"name": "e", "vlan": "v", "cidr": "172.16.0.0/16", "gateway": "172.16.0.1",
                                 "free_start": "172.16.2.90", "free_end": "172.16.2.95", **bad})


SUBNETS = [{"metadata": {"name": "sn166"}, "spec": {"vpc": "vpc166", "cidrBlock": "10.166.0.0/24", "provider": "sn166.default.ovn"}},
           {"metadata": {"name": "ext-lan"}, "spec": {"vpc": "ovn-cluster", "cidrBlock": "172.16.0.0/16", "vlan": "ext-v0",
                                                      "provider": "ext-lan.kube-system.ovn"}}]


def test_a_nat_gateway_carries_the_tenant_network_and_its_placement():
    gw = ox.nat_gateway({"name": "gw166", "vpc": "vpc166", "subnet": "sn166", "lan_ip": "10.166.0.254", "external": "ext-lan"},
                        SUBNETS, {"ext-lan": "ext"})
    assert gw["metadata"]["annotations"][ox.NETWORKS_ANN] == "default/sn166"
    assert gw["spec"] == {"vpc": "vpc166", "subnet": "sn166", "lanIp": "10.166.0.254", "externalSubnets": ["ext-lan"],
                          "selector": ["ext.provider-network.kubernetes.io/ready: true"]}
    for bad, msg in (({"lan_ip": "10.0.0.1"}, "not in the range"), ({"vpc": "other"}, "not in VPC"),
                     ({"external": "sn166"}, "not an external")):
        with pytest.raises(ValueError, match=msg):
            ox.nat_gateway({"name": "g", "vpc": "vpc166", "subnet": "sn166", "lan_ip": "10.166.0.254", "external": "ext-lan", **bad},
                           SUBNETS, {})


def test_the_vpc_default_route_is_added_and_removed():
    vpc = {"metadata": {"name": "vpc166"}, "spec": {"staticRoutes": [{"cidr": "10.9.0.0/16", "nextHopIP": "10.166.0.9"}]}}
    out = ox.vpc_with_route(vpc, "10.166.0.254")
    assert out["spec"]["staticRoutes"][-1] == {"cidr": "0.0.0.0/0", "nextHopIP": "10.166.0.254", "policy": "policyDst"}
    back = ox.vpc_with_route(out, "10.166.0.254", add=False)
    assert back["spec"]["staticRoutes"] == [{"cidr": "10.9.0.0/16", "nextHopIP": "10.166.0.9"}]
    with pytest.raises(ValueError, match="already has a default route"):
        ox.vpc_with_route({"spec": {"staticRoutes": [{"cidr": "0.0.0.0/0", "nextHopIP": "10.166.0.1"}]}}, "10.166.0.254")


def test_external_ips_and_rules_follow_kube_ovn():
    e = ox.eip({"name": "eip166", "gateway": "gw166", "external": "ext-lan", "ip": "172.16.2.91"}, SUBNETS)
    assert e["spec"] == {"natGwDp": "gw166", "externalSubnet": "ext-lan", "v4ip": "172.16.2.91"}
    with pytest.raises(ValueError, match="not in the range"):
        ox.eip({"name": "e", "gateway": "gw166", "external": "ext-lan", "ip": "10.0.0.1"}, SUBNETS)
    assert ox.snat({"name": "s", "eip": "eip166", "internal_cidr": "10.166.0.0/24"})["spec"] == {"eip": "eip166", "internalCIDR": "10.166.0.0/24"}
    with pytest.raises(ValueError, match="one network"):
        ox.snat({"name": "s", "eip": "eip166", "internal_cidr": "10.0.0.0/24,10.1.0.0/24"})
    d = ox.dnat({"name": "d", "eip": "eip166", "external_port": 2222, "internal_ip": "10.166.0.10", "internal_port": 22, "protocol": "TCP"})
    assert d["spec"] == {"eip": "eip166", "externalPort": "2222", "internalIp": "10.166.0.10", "internalPort": "22", "protocol": "tcp"}
    with pytest.raises(ValueError, match="port"):
        ox.dnat({"name": "d", "eip": "e", "external_port": 0, "internal_ip": "10.0.0.1", "internal_port": 22})


def test_deletion_follows_the_order_kube_ovn_imposes():
    state = {"gateways": [{"name": "gw", "eips": ["e1"]}], "eips": [{"name": "e1", "rules": ["d1"]}],
             "vlans": [{"name": "v", "subnets": ["ext"]}], "providers": [{"name": "p", "vlans": ["v"]}],
             "externals": [{"name": "ext", "using": 2}]}
    for kind, name, msg in (("gateway", "gw", "external IPs"), ("eip", "e1", "rules"), ("vlan", "v", "subnets"),
                            ("provider", "p", "VLANs"), ("external", "ext", "still use")):
        with pytest.raises(ValueError, match=msg):
            ox.delete_check(kind, name, state)
    ox.delete_check("gateway", "other", state)


def test_rows_say_what_is_ready():
    pn = {"metadata": {"name": "ext"}, "spec": {"defaultInterface": "enp4s0"},
          "status": {"ready": False, "readyNodes": ["n1"], "notReadyNodes": ["n2"],
                     "conditions": [{"node": "n2", "type": "Ready", "status": "False", "reason": "InitOVSBridgeFailed", "message": "no enp4s0"}]}}
    [r] = ox.provider_rows([pn], [{"metadata": {"name": "ext-v0"}, "spec": {"provider": "ext", "id": 0}}])
    assert not r["ready"] and r["errors"] == ["n2: no enp4s0"] and r["vlans"] == ["ext-v0"]
    pod = {"metadata": {"name": "vpc-nat-gw-gw166-0", "labels": {"app": "vpc-nat-gw-gw166"},
                        "annotations": {ox.GW_INIT: "true", "k8s.v1.cni.cncf.io/network-status": json.dumps(
                            [{"name": "k8s-pod-network", "interface": "eth0", "ips": ["10.52.0.9"]},
                             {"name": "default/sn166", "interface": "net1", "ips": ["10.166.0.254"]}])}},
           "spec": {"nodeName": "n1"}, "status": {"phase": "Running"}}
    [g] = ox.gateway_rows([{"metadata": {"name": "gw166"}, "spec": {"vpc": "vpc166", "externalSubnets": ["ext-lan"]}}],
                          [pod], [{"metadata": {"name": "vpc-nat-gw-gw166"}, "status": {"readyReplicas": 1}}], [])
    assert g["ready"] and g["interfaces"][1] == {"name": "net1", "network": "default/sn166", "ips": ["10.166.0.254"]}


def test_a_policy_targets_vms_by_name_and_reads_back():
    np = ox.network_policy({"name": "np166", "namespace": "default", "vms": ["vm166"],
                            "ingress": [{"peers": [{"kind": "cidr", "cidr": "172.16.0.0/16"}], "ports": [{"port": 22}]}]})
    s = np["spec"]
    assert s["podSelector"] == {"matchExpressions": [{"key": "vm.kubevirt.io/name", "operator": "In", "values": ["vm166"]}]}
    assert s["policyTypes"] == ["Ingress"] and s["ingress"] == [{"from": [{"ipBlock": {"cidr": "172.16.0.0/16"}}],
                                                                  "ports": [{"port": 22, "protocol": "TCP"}]}]
    assert np["metadata"]["annotations"] == {ox.ENFORCEMENT: "lax"}
    deny = ox.network_policy({"name": "deny", "namespace": "default", "vms": [], "ingress": []})
    assert deny["spec"] == {"podSelector": {}, "ingress": [], "policyTypes": ["Ingress"]}
    back = ox.policy_to_spec(np)
    assert back["vms"] == ["vm166"] and back["ingress"][0]["peers"][0]["cidr"] == "172.16.0.0/16" and back["lax"]
    with pytest.raises(ValueError, match="incoming"):
        ox.network_policy({"name": "x", "namespace": "default"})
    [row] = ox.policy_rows([np])
    assert row["target"] == "VMs vm166" and row["rules"]["ingress"][0]["peers"] == ["172.16.0.0/16"] and row["editable"]
    other = {"metadata": {"name": "o", "namespace": "default"}, "spec": {"podSelector": {"matchLabels": {"app": "x"}}, "policyTypes": ["Ingress"]}}
    assert ox.policy_to_spec(other) is None and not ox.policy_rows([other])[0]["editable"]


def test_kube_ovn_health_says_what_is_broken():
    deploys = [{"metadata": {"name": "ovn-central"}, "spec": {"replicas": 3}, "status": {"readyReplicas": 0}},
               {"metadata": {"name": "kube-ovn-controller"}, "spec": {"replicas": 3}, "status": {"readyReplicas": 3}}]
    pods = [{"metadata": {"labels": {"app": "kube-ovn-cni"}}, "spec": {"nodeName": "n3"}, "status": {"containerStatuses": [{"ready": False}]}},
            {"metadata": {"labels": {"app": "kube-ovn-cni"}}, "spec": {"nodeName": "n1"}, "status": {"containerStatuses": [{"ready": True}]}}]
    nodes = [{"metadata": {"name": "n1", "annotations": {"ovn.kubernetes.io/ip_address": "100.64.0.2"}}}, {"metadata": {"name": "n3"}}]
    h = ox.ovn_health(deploys, pods, nodes)
    assert not h["healthy"] and h["cni_not_ready"] == ["n3"]
    assert h["problems"] == ["ovn-central 0/3 ready", "kube-ovn CNI not ready on n3", "no kube-ovn address yet for n3"]
    deploys[0]["status"]["readyReplicas"] = 3
    pods[0]["status"]["containerStatuses"][0]["ready"] = True
    nodes[1]["metadata"]["annotations"] = {"ovn.kubernetes.io/ip_address": "100.64.0.4"}
    assert ox.ovn_health(deploys, pods, nodes)["healthy"]


def test_a_gateway_whose_pod_network_was_replaced_is_seen_and_repaired():
    gw = {"metadata": {"name": "gw", "annotations": {ox.NETWORKS_ANN: "default/sn166"}}, "spec": {}}
    bad = {"metadata": {"labels": {"app": "vpc-nat-gw-gw"}, "annotations": {ox.GW_INIT: "true", "k8s.v1.cni.cncf.io/network-status": json.dumps(
        [{"interface": "eth0", "name": "default/sn166"}, {"interface": "net1", "name": "default/sn166"}])}}}
    good = {"metadata": {"labels": {"app": "vpc-nat-gw-gw"}, "annotations": {ox.GW_INIT: "true", "k8s.v1.cni.cncf.io/network-status": json.dumps(
        [{"interface": "eth0", "name": "k8s-pod-network"}, {"interface": "net1", "name": "default/sn166"}])}}}
    assert ox.gateway_pod_broken(gw, bad) and not ox.gateway_pod_broken(gw, good)
    sts = {"metadata": {"name": "vpc-nat-gw-gw"}, "status": {"readyReplicas": 1},
           "spec": {"template": {"metadata": {"annotations": {ox.DEFAULT_NET_ANN: "default/sn166"}}}}}
    assert ox.gateway_fix_patch(sts) == [{"op": "remove", "path": "/spec/template/metadata/annotations/v1.multus-cni.io~1default-network"}]
    assert ox.gateway_fix_patch({"spec": {"template": {"metadata": {"annotations": {}}}}}) is None
    [row] = ox.gateway_rows([gw], [bad], [sts], [])
    assert row["broken"] and not row["ready"]
