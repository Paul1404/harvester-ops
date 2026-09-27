"""v1.68.1 : le détail d'un hôte, comme sa page dans Harvester : Basics
(identité, système, matériel, NTP, jauges), Instances, Network, Events."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_host as hh  # noqa: E402

NODE = {"metadata": {"name": "n1", "creationTimestamp": "2026-09-22T10:00:00Z",
                     "labels": {"node-role.kubernetes.io/control-plane": "true", "manufacturer": "HPE", "serialNumber": "USE6236RY1"},
                     "annotations": {hh.ANN_NAME: "rack-a", hh.ANN_NTP: json.dumps({"ntpSyncStatus": "unsynced", "currentNtpServers": "pool.lan"})}},
        "spec": {}, "status": {"addresses": [{"type": "InternalIP", "address": "172.16.2.61"}],
                               "capacity": {"cpu": "8", "memory": "20Gi"}, "allocatable": {"cpu": "7800m", "memory": "19Gi"},
                               "conditions": [{"type": "Ready", "status": "True"}],
                               "nodeInfo": {"osImage": "Harvester v1.8.2", "kernelVersion": "6.4", "systemUUID": "u-1",
                                            "containerRuntimeVersion": "containerd://2", "kubeletVersion": "v1.35"}}}


def test_quantities_and_roles():
    assert hh.qty("8") == 8 and hh.qty("250m") == 0.25 and hh.qty("123456789n") == pytest.approx(0.123456789)
    assert hh.qty("2Ki") == 2048 and hh.qty("nope") is None
    assert hh.node_roles(NODE) == "management"
    assert hh.node_roles({"metadata": {"labels": {"node-role.harvesterhci.io/witness": "true"}}}) == "witness"
    assert hh.node_roles({"metadata": {"labels": {}}}) == "compute"


def test_the_host_detail_gathers_what_harvester_shows():
    lh = {"status": {"diskStatus": {"d1": {"storageMaximum": 100, "storageAvailable": 60, "storageScheduled": 30}}}}
    vmis = [{"metadata": {"namespace": "default", "name": "web"},
             "spec": {"domain": {"cpu": {"cores": 2}, "memory": {"guest": "1Gi"}}},
             "status": {"nodeName": "n1", "phase": "Running", "interfaces": [{"ipAddress": "10.52.0.9"}]}},
            {"metadata": {"namespace": "default", "name": "db"}, "spec": {}, "status": {"nodeName": "n2"}}]
    vls = [{"status": {"node": "n1", "clusterNetwork": "data", "vlanConfig": "data-all",
                       "localAreas": [{"vlanID": 20}, {"vlanID": 10}], "conditions": [{"type": "ready", "status": "True"}]}}]
    lms = [{"status": {"linkStatus": {"n1": [{"index": 2, "name": "enp1s0", "type": "device", "state": "up", "masterIndex": 3},
                                             {"index": 3, "name": "mgmt-bo", "type": "bond", "state": "up"}]}}}]
    evs = [{"involvedObject": {"kind": "Node", "name": "n1"}, "type": "Normal", "reason": "Rebooted", "message": "m",
            "lastTimestamp": "2026-09-27T08:00:00Z"},
           {"involvedObject": {"kind": "Pod", "name": "n1"}, "reason": "Other"}]
    d = hh.host_detail(NODE, {"usage": {"cpu": "1500m", "memory": "4Gi"}}, lh, vmis, vls, lms, evs)
    b = d["basics"]
    assert b["custom_name"] == "rack-a" and b["ip"] == "172.16.2.61" and b["role"] == "management"
    assert b["os"] == "Harvester v1.8.2" and b["manufacturer"] == "HPE" and b["serial"] == "USE6236RY1"
    assert b["ntp"] == {"status": "unsynced", "servers": "pool.lan"}
    assert b["cpu"] == {"capacity": 8, "allocatable": 7.8, "used": 1.5} and b["memory"]["used"] == 4 * 2 ** 30
    assert b["storage"] == {"maximum": 100, "available": 60, "scheduled": 30}
    assert d["instances"] == [{"namespace": "default", "name": "web", "phase": "Running", "ips": ["10.52.0.9"],
                               "cpu": 2, "memory": 2 ** 30, "created": None, "migrating": False}]
    assert d["vlans"] == [{"cluster_network": "data", "vlan_config": "data-all", "vlans": [10, 20], "ready": True, "message": ""}]
    assert d["nics"][0] == {"name": "enp1s0", "type": "device", "state": "up", "mac": "", "master": "mgmt-bo"}
    assert [e["reason"] for e in d["events"]] == ["Rebooted"]
