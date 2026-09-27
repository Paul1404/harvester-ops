"""v1.65.0 : le menu « Networks » de Harvester, en fonctions pures.

Formats relevés dans harvester-ui-extension, harvester, network-controller-
harvester et load-balancer-harvester v1.9.0 : réseaux de cluster et
configurations, réseaux de VM (trunk, route, modification), équilibreurs et
pools, réseaux d'hôte, réglages de stockage / migration / RWX."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_net as hn  # noqa: E402
import hv_objects as ho  # noqa: E402

NODES = [{"metadata": {"name": "n1", "labels": {"kubernetes.io/hostname": "n1", "rack": "a"}}},
         {"metadata": {"name": "n2", "labels": {"kubernetes.io/hostname": "n2", "rack": "b"}}},
         {"metadata": {"name": "w", "labels": {"kubernetes.io/hostname": "w", hn.WITNESS: "true"}}}]


# -- réseaux de cluster ---------------------------------------------------------

def test_a_cluster_network_name_leaves_room_for_the_bridge_suffix():
    assert hn.cluster_network("data", "fast")["metadata"]["annotations"][hn.DESC] == "fast"
    hn.cluster_network("abcdefghijkl")                     # 12 : data-br fait 15 caractères au plus
    with pytest.raises(ValueError, match="12"):
        hn.cluster_network("abcdefghijklm")
    with pytest.raises(ValueError, match="mgmt"):
        hn.cluster_network("mgmt")


def test_node_selector_follows_the_three_choices_of_the_form():
    assert hn.node_selector({"mode": "all"}) is None
    assert hn.node_selector({"mode": "node", "node": "n1"}) == {"kubernetes.io/hostname": "n1"}
    assert hn.node_selector({"mode": "labels", "labels": {"rack": "a"}}) == {"rack": "a"}
    with pytest.raises(ValueError):
        hn.node_selector({"mode": "labels", "labels": {}})
    assert hn.selected_nodes(NODES, None) == ["n1", "n2"]          # jamais le témoin
    assert hn.selected_nodes(NODES, {"rack": "b"}) == ["n2"]


def test_a_vlan_config_carries_nics_bond_and_mtu():
    vc = hn.vlan_config({"name": "data-all", "cluster_network": "data", "nics": ["enp4s0", "enp5s0"],
                         "bond_mode": "802.3ad", "miimon": "100", "mtu": "9000",
                         "nodes": {"mode": "node", "node": "n1"}, "description": "rack a"})
    assert vc["kind"] == "VlanConfig" and vc["spec"]["clusterNetwork"] == "data"
    assert vc["spec"]["uplink"] == {"nics": ["enp4s0", "enp5s0"], "bondOptions": {"mode": "802.3ad", "miimon": 100},
                                    "linkAttributes": {"mtu": 9000}}
    assert vc["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "n1"}
    assert "nodeSelector" not in hn.vlan_config({"name": "x", "cluster_network": "data", "nics": ["e"]})["spec"]
    for bad, msg in (({"nics": []}, "network card"), ({"mtu": 100}, "MTU"), ({"bond_mode": "lacp"}, "bond"),
                     ({"cluster_network": "mgmt"}, "mgmt")):
        spec = {"name": "x", "cluster_network": "data", "nics": ["e"], **bad}
        with pytest.raises(ValueError, match=msg):
            hn.vlan_config(spec)


def test_editing_a_config_keeps_what_the_form_does_not_show():
    cur = {"apiVersion": "network.harvesterhci.io/v1beta1", "kind": "VlanConfig",
           "metadata": {"name": "c", "resourceVersion": "7", "labels": {hn.CN_LABEL: "data"}},
           "spec": {"clusterNetwork": "data", "uplink": {"nics": ["e1"], "linkAttributes": {"mtu": 1500, "txQLen": 2000}}}}
    out = hn.vlan_config({"name": "c", "cluster_network": "data", "nics": ["e1", "e2"], "mtu": ""}, current=cur)
    assert out["metadata"]["resourceVersion"] == "7"
    assert out["spec"]["uplink"]["linkAttributes"] == {"txQLen": 2000}
    assert out["spec"]["uplink"]["nics"] == ["e1", "e2"]
    with pytest.raises(ValueError, match="keeps its name"):
        hn.vlan_config({"name": "other", "cluster_network": "data", "nics": ["e1"]}, current=cur)


def test_nic_choices_are_those_free_and_present_on_every_node():
    ls = {"n1": [{"name": "enp1s0", "state": "up", "masterIndex": 3}, {"name": "enp4s0", "state": "up"},
                 {"name": "enp5s0", "state": "down"}],
          "n2": [{"name": "enp1s0", "state": "up", "masterIndex": 3}, {"name": "enp4s0", "state": "up"},
                 {"name": "enp5s0", "state": "up"}, {"name": "eth9", "state": "up"}]}
    got = {c["name"]: c for c in hn.nic_choices(ls, ["n1", "n2"])}
    assert set(got) == {"enp1s0", "enp4s0", "enp5s0"}                 # eth9 manque sur n1
    assert got["enp4s0"]["usable"] and not got["enp1s0"]["usable"] and got["enp1s0"]["why"] == "enslaved"
    assert got["enp5s0"]["why"] == "down" and got["enp5s0"]["down_on"] == ["n1"]
    # une carte déjà dans la configuration reste proposée
    assert {c["name"]: c for c in hn.nic_choices(ls, ["n1"], current=["enp1s0"])}["enp1s0"]["usable"]
    assert hn.nic_choices(ls, []) == []


def test_config_settled_waits_for_every_matched_node_and_says_refusals():
    vc = {"metadata": {"name": "c"}, "spec": {"clusterNetwork": "data"}}
    st = lambda node, ok, msg="": {"status": {"vlanConfig": "c", "node": node,  # noqa: E731
                                              "conditions": [{"type": "ready", "status": "True" if ok else "False", "message": msg}]}}
    assert hn.config_settled(vc, [st("n1", True)], NODES) == (None, "waiting for n2")
    assert hn.config_settled(vc, [st("n1", True), st("n2", True)], NODES) == (True, "ready on 2 node(s)")
    done, msg = hn.config_settled(vc, [st("n1", False, "enp4s0 has been enslaved by the link with index 3")], NODES)
    assert done is False and "enslaved" in msg
    # vu en réel sur harvlab : une première erreur de l'agent, puis le succès
    early = hn.config_settled(vc, [st("n1", False, "set vlan filtering failed")], NODES, elapsed=5)
    assert early[0] is None and "retries" in early[1]
    assert hn.config_settled(vc, [st("n1", False, "set vlan filtering failed")], NODES, elapsed=90)[0] is False


def test_cluster_network_rows_group_configs_and_block_deletion():
    cns = [{"metadata": {"name": "data", "annotations": {hn.MTU_ANN: "9000"}},
            "status": {"conditions": [{"type": "ready", "status": "True"}]}}, {"metadata": {"name": "mgmt"}}]
    vcs = [{"metadata": {"name": "c"}, "spec": {"clusterNetwork": "data", "uplink": {"nics": ["e"], "bondOptions": {"mode": "active-backup"}}}},
           {"metadata": {"name": "mgmt-vlanconfig-n1"}, "spec": {"clusterNetwork": "mgmt", "uplink": {"nics": ["enp1s0"]}}}]
    nads = [{"metadata": {"namespace": "default", "name": "v20", "labels": {hn.CN_LABEL: "data"}},
             "spec": {"config": '{"type":"bridge","bridge":"data-br","vlan":20}'}}]
    rows = hn.cluster_network_rows(cns, vcs, [], nads, NODES)
    assert [r["name"] for r in rows] == ["mgmt", "data"]
    data = rows[1]
    assert data["mtu"] == 9000 and data["configs"][0]["nodes"] == ["n1", "n2"] and data["networks"] == ["default/v20"]
    assert rows[0]["protected"] and rows[0]["configs"][0]["managed"]
    with pytest.raises(ValueError, match="configurations"):
        hn.delete_cluster_network_check(data)
    with pytest.raises(ValueError, match="mgmt"):
        hn.delete_cluster_network_check(rows[0])


def test_only_real_changes_to_a_config_need_its_vms_stopped():
    nads = [{"metadata": {"namespace": "default", "name": "v20", "labels": {hn.CN_LABEL: "data"}},
             "spec": {"config": '{"type":"bridge","bridge":"data-br","vlan":20}'}}]
    vmis = [{"metadata": {"namespace": "default", "name": "a"}, "status": {"nodeName": "n2"},
             "spec": {"networks": [{"multus": {"networkName": "default/v20"}}]}}]
    cur = hn.vlan_config({"name": "c", "cluster_network": "data", "nics": ["e1"]})
    same = hn.vlan_config({"name": "c", "cluster_network": "data", "nics": ["e1"], "description": "d"}, current=cur)
    assert hn.update_blockers(cur, same, NODES, vmis, nads) == []                      # description seule
    nics = hn.vlan_config({"name": "c", "cluster_network": "data", "nics": ["e1", "e2"]}, current=cur)
    assert hn.update_blockers(cur, nics, NODES, vmis, nads) == ["default/a"]            # lien changé
    only_n2 = hn.vlan_config({"name": "c", "cluster_network": "data", "nics": ["e1"], "nodes": {"mode": "node", "node": "n2"}}, current=cur)
    assert hn.update_blockers(cur, only_n2, NODES, vmis, nads) == []                    # n1 sort, la VM est sur n2
    only_n1 = hn.vlan_config({"name": "c", "cluster_network": "data", "nics": ["e1"], "nodes": {"mode": "node", "node": "n1"}}, current=cur)
    assert hn.update_blockers(cur, only_n1, NODES, vmis, nads) == ["default/a"]


def test_migrating_a_config_only_changes_its_cluster_network():
    assert hn.migrate_patch("data2") == {"spec": {"clusterNetwork": "data2"}}
    with pytest.raises(ValueError):
        hn.migrate_patch("mgmt")


def test_running_vms_on_networks_are_found_with_or_without_namespace():
    vmis = [{"metadata": {"namespace": "default", "name": "a"}, "spec": {"networks": [{"multus": {"networkName": "v20"}}]}},
            {"metadata": {"namespace": "lab", "name": "b"}, "spec": {"networks": [{"multus": {"networkName": "default/v20"}}]}},
            {"metadata": {"namespace": "lab", "name": "c"}, "spec": {"networks": [{"pod": {}}]}}]
    assert hn.vms_on(vmis, ["default/v20"]) == ["default/a", "lab/b"]


# -- réseaux de VM ----------------------------------------------------------------

def test_a_trunk_network_carries_its_ranges_and_no_route():
    nad = ho.network_manifest({"name": "tr", "namespace": "default", "cluster_network": "data", "type": "trunk",
                               "ranges": [{"min": 100, "max": 199}, {"min": 300}]})
    cfg = json.loads(nad["spec"]["config"])
    assert cfg["vlan"] == 0 and cfg["vlanTrunk"] == [{"minID": 100, "maxID": 199}, {"minID": 300, "maxID": 300}]
    assert nad["metadata"]["labels"][hn.TYPE_LABEL] == "L2VlanTrunkNetwork"
    assert hn.ROUTE not in (nad["metadata"].get("annotations") or {})
    with pytest.raises(ValueError, match="start comes first"):
        hn.trunk_ranges([{"min": 20, "max": 10}])
    with pytest.raises(ValueError):
        hn.trunk_ranges([{"min": 0, "max": 10}])


def test_a_route_can_name_its_dhcp_server_or_be_manual():
    assert json.loads(hn.route_annotation({"dhcp_server": "172.16.3.6"}))["serverIPAddr"] == "172.16.3.6"
    r = json.loads(hn.route_annotation({"route_mode": "manual", "cidr": "10.0.20.0/24", "gateway": "10.0.20.1"}))
    assert r == {"mode": "manual", "serverIPAddr": "", "cidr": "10.0.20.0/24", "gateway": "10.0.20.1"}
    for bad in ({"route_mode": "manual", "cidr": "0.0.0.0/0", "gateway": "1.1.1.1"},
                {"route_mode": "manual", "cidr": "10.0.0.0/8", "gateway": "x"}, {"dhcp_server": "nope"}):
        with pytest.raises(ValueError):
            hn.route_annotation(bad)


def test_a_vm_network_row_gives_what_its_window_shows():
    nad = ho.network_manifest({"name": "t", "namespace": "default", "cluster_network": "data", "type": "trunk",
                               "ranges": [{"min": 10, "max": 20}], "description": "lab"})
    r = hn.vmnet_row(nad)
    assert r["type"] == "L2VlanTrunkNetwork" and r["ranges"] == [{"min": 10, "max": 20}] and r["description"] == "lab"
    v = ho.network_manifest({"name": "v", "namespace": "default", "cluster_network": "data", "vlan": 20,
                             "dhcp_server": "172.16.3.6"})
    r = hn.vmnet_row(v)
    assert r["vlan"] == 20 and r["route"]["dhcp_server"] == "172.16.3.6" and r["cluster_network"] == "data"


def test_editing_a_vm_network_changes_what_harvester_allows():
    nad = ho.network_manifest({"name": "v20", "namespace": "default", "cluster_network": "data", "type": "vlan", "vlan": 20})
    out, changed = hn.network_update(nad, {"vlan": 30, "description": "moved", "route_mode": "manual",
                                           "cidr": "10.0.30.0/24", "gateway": "10.0.30.1"})
    assert changed and json.loads(out["spec"]["config"])["vlan"] == 30
    assert out["metadata"]["annotations"][hn.DESC] == "moved"
    assert json.loads(out["metadata"]["annotations"][hn.ROUTE])["cidr"] == "10.0.30.0/24"
    out, changed = hn.network_update(nad, {"vlan": 20, "description": ""})
    assert not changed and hn.DESC not in out["metadata"]["annotations"]
    trunk = ho.network_manifest({"name": "t", "namespace": "default", "cluster_network": "data", "type": "trunk",
                                 "ranges": [{"min": 1, "max": 9}]})
    out, changed = hn.network_update(trunk, {"ranges": [{"min": 1, "max": 20}]})
    assert changed and json.loads(out["spec"]["config"])["vlanTrunk"] == [{"minID": 1, "maxID": 20}]
    overlay = {"metadata": {"name": "o", "labels": {hn.TYPE_LABEL: "OverlayNetwork"}}, "spec": {"config": '{"type":"kube-ovn"}'}}
    with pytest.raises(ValueError, match="subnet"):
        hn.network_update(overlay, {})


# -- équilibreurs de charge et pools ----------------------------------------------

LB = {"name": "web", "namespace": "default", "ipam": "dhcp",
      "listeners": [{"name": "http", "port": 80, "protocol": "TCP", "backend_port": 8080}],
      "selector": "app=web,api\ntier=front", "health": {"port": 8080, "period": 5}}


def test_a_load_balancer_follows_the_webhook_rules():
    lb = hn.load_balancer(LB)
    s = lb["spec"]
    assert s["workloadType"] == "vm" and s["ipam"] == "dhcp" and "ipPool" not in s
    assert s["listeners"] == [{"name": "http", "port": 80, "protocol": "TCP", "backendPort": 8080}]
    assert s["backendServerSelector"] == {"app": ["web", "api"], "tier": ["front"]}
    assert s["healthCheck"] == {"port": 8080, "periodSeconds": 5}
    for bad, msg in (({"listeners": []}, "at least one listener"),
                     ({"listeners": LB["listeners"] * 2}, "twice"),
                     ({"health": {"port": 9999}}, "health check port"),
                     ({"ipam": "static"}, "IPAM")):
        with pytest.raises(ValueError, match=msg):
            hn.load_balancer({**LB, **bad})


def test_a_load_balancer_keeps_its_ipam():
    cur = hn.load_balancer(LB)
    cur["metadata"]["resourceVersion"] = "3"
    out = hn.load_balancer({**LB, "health": {}, "description": "d"}, current=cur)
    assert out["metadata"]["resourceVersion"] == "3" and "healthCheck" not in out["spec"]
    with pytest.raises(ValueError, match="cannot change"):
        hn.load_balancer({**LB, "ipam": "pool"}, current=cur)


def test_lb_rows_hide_the_dhcp_placeholder_address():
    lb = hn.load_balancer(LB)
    lb["status"] = {"allocatedAddress": {"ip": "0.0.0.0"}, "conditions": [{"type": "Ready", "status": "False",
                                                                            "message": "no running backend servers"}]}
    [r] = hn.lb_rows([lb])
    assert r["address"] == "" and r["ready"] is False and "backend" in r["message"]
    lb["status"]["address"] = "172.16.10.50"
    assert hn.lb_rows([lb])[0]["address"] == "172.16.10.50"


def test_an_ip_pool_normalises_and_checks_its_ranges():
    with pytest.raises(ValueError, match="inside"):
        hn.ip_pool({"name": "lan", "ranges": [{"subnet": "172.16.3.0/24", "gateway": "172.16.0.1"}]})
    p = hn.ip_pool({"name": "lan", "ranges": [{"subnet": "172.16.3.0/24", "start": "172.16.3.190", "end": "172.16.3.199"}],
                    "description": "test"})
    assert p["spec"]["ranges"] == [{"subnet": "172.16.3.0/24", "rangeStart": "172.16.3.190", "rangeEnd": "172.16.3.199"}]
    assert p["spec"]["selector"] == {"scope": [{"namespace": "*"}]}          # global, comme Harvester autonome
    with pytest.raises(ValueError, match="overlaps"):
        hn.ip_pool({"name": "x", "ranges": [{"subnet": "10.0.0.0/24"}, {"subnet": "10.0.0.0/25"}]})
    with pytest.raises(ValueError, match="start comes first"):
        hn.ip_pool({"name": "x", "ranges": [{"subnet": "10.0.0.0/24", "start": "10.0.0.9", "end": "10.0.0.2"}]})
    scoped = hn.ip_pool({"name": "x", "ranges": [{"subnet": "10.0.0.0/24"}], "network": "default/v20", "priority": 5,
                         "scope": [{"namespace": "team-a"}]})
    assert scoped["spec"]["selector"] == {"network": "default/v20", "priority": 5, "scope": [{"namespace": "team-a"}]}


def test_an_ip_pool_keeps_its_allocations_and_releases_them_by_annotation():
    cur = hn.ip_pool({"name": "lan", "ranges": [{"subnet": "10.0.0.0/24"}]})
    cur["status"] = {"allocated": {"10.0.0.7": "default/web"}}
    with pytest.raises(ValueError, match="10.0.0.7"):
        hn.ip_pool({"name": "lan", "ranges": [{"subnet": "10.0.0.0/24", "start": "10.0.0.100"}]}, current=cur)
    with pytest.raises(ValueError, match="release"):
        hn.delete_pool_check(cur)
    assert hn.release_patch(cur, "10.0.0.7") == {"metadata": {"annotations": {hn.RELEASE: "10.0.0.7: default/web"}}}
    with pytest.raises(ValueError):
        hn.release_patch(cur, "10.0.0.8")


# -- réseaux d'hôte -----------------------------------------------------------------

def test_a_static_host_network_needs_one_address_per_node_in_one_subnet():
    h = hn.host_network({"name": "stor", "cluster_network": "data", "vlan": 100, "mode": "static",
                         "ips": {"n1": "10.0.100.11/24", "n2": "10.0.100.12/24"}},
                        [n for n in NODES if n["metadata"]["name"] != "w"])
    assert h["spec"] == {"clusterNetwork": "data", "vlanID": 100, "mode": "static", "underlay": False,
                         "ips": {"n1": "10.0.100.11/24", "n2": "10.0.100.12/24"}}
    only = hn.host_network({"name": "s", "cluster_network": "data", "vlan": 100, "mode": "static", "nodes": ["n1"],
                            "ips": {"n1": "10.0.100.11/24"}}, NODES)
    assert only["spec"]["nodeSelector"]["matchExpressions"][0]["values"] == ["n1"]
    for ips, msg in (({"n1": "10.0.100.11/24"}, "not found for node n2"),
                     ({"n1": "10.0.100.11/24", "n2": "10.0.101.12/24"}, "same subnet"),
                     ({"n1": "10.0.100.11", "n2": "10.0.100.12/24"}, "prefix")):
        with pytest.raises(ValueError, match=msg):
            hn.host_network({"name": "s", "cluster_network": "data", "vlan": 100, "mode": "static", "ips": ips},
                            [n for n in NODES if n["metadata"]["name"] != "w"])
    with pytest.raises(ValueError, match="15 characters"):
        hn.host_network({"name": "s", "cluster_network": "abcdefghijkl", "vlan": 100, "mode": "dhcp"}, NODES)


def test_a_host_network_keeps_its_network_and_vlan():
    cur = hn.host_network({"name": "s", "cluster_network": "data", "vlan": 100, "mode": "dhcp"}, NODES)
    out = hn.host_network({"name": "s", "cluster_network": "data", "vlan": 100, "mode": "dhcp", "description": "d"}, NODES, current=cur)
    assert out["spec"]["description"] == "d"
    with pytest.raises(ValueError, match="cannot change"):
        hn.host_network({"name": "s", "cluster_network": "data", "vlan": 101, "mode": "dhcp"}, NODES, current=cur)
    with pytest.raises(ValueError, match="underlay"):
        hn.delete_hostnet_check({"spec": {"underlay": True}})


def test_hostnet_settled_reads_each_node_status():
    obj = {"spec": {"nodeSelector": {"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In", "values": ["n1", "n2"]}]}},
           "status": {"nodeStatus": {"n1": {"conditions": [{"type": "ready", "status": "True"}]}}}}
    assert hn.hostnet_settled(obj, NODES) == (None, "waiting for n2")
    obj["status"]["nodeStatus"]["n2"] = {"conditions": [{"type": "ready", "status": "False", "message": "setup l3 connectivity failed: x"}]}
    assert hn.hostnet_settled(obj, NODES)[0] is False
    obj["status"]["nodeStatus"]["n2"] = {"conditions": [{"type": "ready", "status": "True"}]}
    assert hn.hostnet_settled(obj, NODES) == (True, "ready on 2 node(s)")


# -- réglages réseau ------------------------------------------------------------------

def test_network_settings_values_follow_the_webhook():
    v = json.loads(hn.setting_value("storage-network", {"cluster_network": "data", "vlan": 100, "range": "10.100.0.0/24",
                                                          "exclude": ["10.100.0.0/28"], "exclusive_vlan": True}))
    assert v == {"clusterNetwork": "data", "range": "10.100.0.0/24", "vlan": 100, "exclude": ["10.100.0.0/28"],
                 "exclusiveVlan": True}
    assert hn.setting_value("storage-network", {"disable": True}) == ""
    assert json.loads(hn.setting_value("rwx-network", {"share_storage": True})) == {"share-storage-network": True}
    assert json.loads(hn.setting_value("rwx-network", {"disable": True})) == {"share-storage-network": False}
    rwx = json.loads(hn.setting_value("rwx-network", {"cluster_network": "data", "vlan": 102, "range": "10.102.0.0/24"}))
    assert rwx["share-storage-network"] is False and rwx["network"]["vlan"] == 102
    for kind, spec, msg in (("vm-migration-network", {"cluster_network": "data", "range": "10.1.0.0/24"}, "VLAN ID"),
                            ("storage-network", {"cluster_network": "mgmt", "vlan": 1, "range": "10.1.0.0/24"}, "not allowed on mgmt"),
                            ("storage-network", {"cluster_network": "data", "vlan": 5, "range": "10.1.0.5/24"}, "subnet CIDR"),
                            ("storage-network", {"cluster_network": "data", "vlan": 5, "range": "10.0.0.0/8"}, "/16"),
                            ("storage-network", {"cluster_network": "data", "vlan": 5, "range": "10.1.0.0/24",
                                                 "exclude": ["10.2.0.0/28"]}, "inside"),
                            ("storage-network", {"cluster_network": "data", "vlan": 1, "range": "10.1.0.0/24",
                                                 "exclusive_vlan": True}, "exclusive")):
        with pytest.raises(ValueError, match=msg):
            hn.setting_value(kind, spec)


def test_setting_rows_and_settled_state():
    settings = [{"metadata": {"name": "storage-network",
                              "annotations": {"storage-network.settings.harvesterhci.io/net-attach-def": "harvester-system/storagenetwork-x"}},
                 "value": '{"clusterNetwork":"data","vlan":100,"range":"10.100.0.0/24"}',
                 "status": {"conditions": [{"type": "configured", "status": "True", "reason": "Completed"}]}},
                {"metadata": {"name": "rwx-network"}, "value": '{"share-storage-network":true}'}]
    rows = {r["name"]: r for r in hn.setting_rows(settings)}
    assert rows["storage-network"]["enabled"] and rows["storage-network"]["vlan"] == 100
    assert rows["storage-network"]["nad"] == "harvester-system/storagenetwork-x"
    assert rows["rwx-network"]["share_storage"] and not rows["vm-migration-network"]["exists"]
    assert hn.setting_settled(settings[0]) == (True, "Completed")
    assert hn.setting_settled({"status": {"conditions": [{"type": "configured", "status": "False", "reason": "In Progress"}]}})[0] is None
    assert hn.setting_settled({"status": {"conditions": [{"type": "configured", "status": "False", "reason": "CreateNadError",
                                                          "message": "boom"}]}}) == (False, "CreateNadError: boom")


def test_the_storage_network_counts_attached_volumes_as_harvester_does():
    vols = [{"metadata": {"name": "pvc-a"}, "status": {"state": "attached", "kubernetesStatus": {"namespace": "default", "pvcName": "web-disk"}}},
            {"metadata": {"name": "pvc-b"}, "status": {"state": "detached"}},
            {"metadata": {"name": "pvc-prom"}, "status": {"state": "attached"}},
            {"metadata": {"name": "pvc-c"}, "status": {"state": "detaching", "kubernetesStatus": {"namespace": "lab", "pvcName": "db"}}}]
    pvcs = [{"metadata": {"namespace": "cattle-monitoring-system", "name": "prometheus-db",
                          "labels": {"app.kubernetes.io/name": "prometheus"}}, "spec": {"volumeName": "pvc-prom"}},
            {"metadata": {"namespace": "default", "name": "grafana", "labels": {"app.kubernetes.io/name": "grafana"}},
             "spec": {"volumeName": "pvc-a"}}]                              # grafana hors de son namespace : compté
    assert hn.attached_volumes(vols, pvcs) == ["default/web-disk (attached)", "lab/db (detaching)"]
