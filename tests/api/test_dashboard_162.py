"""v1.62.0 : le tableau de bord de Harvester (bin/lib/hv_dash.py, /api/events,
/api/usage) : événements rangés par hôtes, VMs, volumes, images ; jauges
d'usage réel CPU, mémoire (metrics.k8s.io) et stockage Longhorn."""

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_dash as hd  # noqa: E402


def ev(kind, name, t, typ="Normal", ns="default", reason="Started"):
    return {"involvedObject": {"kind": kind, "name": name, "namespace": ns}, "type": typ, "reason": reason,
            "message": f"{kind} {name}", "lastTimestamp": t, "count": 2, "source": {"component": "kubelet"}}


def test_events_are_grouped_like_harvester_and_newest_first():
    rows = hd.events([ev("Pod", "x", "2026-09-27T10:00:00Z"),
                      ev("Node", "n1", "2026-09-27T09:00:00Z", "Warning", ns="", reason="NodeNotReady"),
                      ev("VirtualMachineInstance", "web", "2026-09-27T11:00:00Z"),
                      ev("VirtualMachine", "web", "2026-09-27T08:00:00Z"),
                      ev("PersistentVolumeClaim", "web-root", "2026-09-27T07:00:00Z"),
                      {"regarding": {"kind": "VirtualMachineImage", "name": "iso"}, "note": "imported",
                       "eventTime": "2026-09-27T12:00:00Z", "reportingController": "harvester"}])
    assert [r["group"] for r in rows] == ["images", "vms", "hosts", "vms", "volumes"]      # pas le Pod
    assert rows[0]["message"] == "imported" and rows[0]["source"] == "harvester"
    assert rows[2]["type"] == "Warning" and rows[2]["namespace"] == ""
    assert len(hd.events([ev("Node", f"n{i}", "2026-09-27T00:00:00Z") for i in range(20)], limit=5)) == 5


def test_usage_reads_live_metrics_requests_and_longhorn():
    nodes = [{"metadata": {"name": "n1", "annotations": {
        "management.cattle.io/pod-requests": json.dumps({"cpu": "3500m", "memory": "8Gi"})}},
        "status": {"capacity": {"cpu": "8", "memory": "32Gi"}}}]
    metrics = [{"metadata": {"name": "n1"}, "usage": {"cpu": "1500000000n", "memory": "4194304Ki"}}]
    lh = [{"spec": {"disks": {"d1": {"storageReserved": 10 * 2**30}, "d2": {"allowScheduling": False}}},
           "status": {"diskStatus": {"d1": {"storageMaximum": 100 * 2**30, "storageAvailable": 60 * 2**30,
                                           "storageScheduled": 150 * 2**30},
                                    "d2": {"storageMaximum": 999, "storageAvailable": 0}}}}]
    u = hd.usage(nodes, metrics, lh, over_provisioning=200)
    assert u["cpu"] == {"used": 1.5, "reserved": 3.5, "total": 8.0, "live": True}
    assert u["memory"]["used"] == 4 * 2**30 and u["memory"]["reserved"] == 8 * 2**30
    assert u["storage"]["used"] == 40 * 2**30 and u["storage"]["total"] == 100 * 2**30   # d2 non planifiable
    assert u["storage"]["allocatable"] == 180 * 2**30 and u["storage"]["scheduled"] == 150 * 2**30
    assert hd.usage(nodes, [], lh)["cpu"]["live"] is False
    assert hd.qty("250m") == 0.25 and hd.qty("1Gi") == 2**30 and hd.qty("nonsense") == 0


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
    wapp._accounts().create("eye", PW, "viewer")
    objs = {"events": {"items": [ev("Node", "n1", "2026-09-27T09:00:00Z", "Warning", ns=""),
                                 ev("VirtualMachine", "web", "2026-09-27T10:00:00Z")]},
            "nodes": {"items": [{"metadata": {"name": "n1"}, "status": {"capacity": {"cpu": "4", "memory": "8Gi"}}}]},
            "nodes.metrics.k8s.io": None,
            "nodes.longhorn.io": {"items": []},
            "settings.longhorn.io": {"value": "150"}}
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: json.loads(json.dumps(objs.get(a[1]))))


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_routes_for_a_viewer(world):
    with wapp.app.test_client() as c:
        e = c.get("/api/events/harv1", headers=auth("eye")).get_json()
        u = c.get("/api/usage/harv1", headers=auth("eye")).get_json()
    assert [r["group"] for r in e["items"]] == ["vms", "hosts"]
    assert e["counts"] == {"vms": 1, "hosts": 1} and e["warnings"] == {"hosts": 1}
    assert u["cpu"]["total"] == 4 and u["cpu"]["live"] is False                  # metrics-server absent
    assert u["storage"]["over_provisioning"] == 150
