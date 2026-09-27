"""harvester-ops : ce que montre le tableau de bord de Harvester (v1.62.0).

Relevé dans harvester-ui-extension v1.9.0 : les événements core/v1 de tous
les namespaces, rangés par `involvedObject.kind` (Node ; VirtualMachine et
VirtualMachineInstance ; PersistentVolumeClaim ; VirtualMachineImage) ; les
jauges CPU et mémoire lisent l'usage réel (metrics.k8s.io, NodeMetrics) sur
la capacité des nœuds, et le réservé dans l'annotation
management.cattle.io/pod-requests ; le stockage se lit sur les nœuds Longhorn
(disques planifiables seulement).
"""

import json
import re

KIND_GROUPS = {"Node": "hosts", "VirtualMachine": "vms", "VirtualMachineInstance": "vms",
               "PersistentVolumeClaim": "volumes", "VirtualMachineImage": "images"}
UNITS = {"": 1, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15,
         "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50,
         "n": 1e-9, "u": 1e-6, "m": 1e-3}


def qty(v):
    """Une quantité Kubernetes en nombre (cœurs, octets) ; 0 si illisible."""
    m = re.match(r"^\s*([0-9.]+(?:e[0-9]+)?)\s*([a-zA-Z]*)\s*$", str(v or ""))
    if not m or m.group(2) not in UNITS:
        return 0.0
    return float(m.group(1)) * UNITS[m.group(2)]


def _when(e):
    return (e.get("lastTimestamp") or e.get("eventTime") or (e.get("series") or {}).get("lastObservedTime")
            or (e.get("metadata") or {}).get("creationTimestamp") or "")


def events(items, limit=500):
    """Les événements du tableau de bord, du plus récent au plus ancien."""
    rows = []
    for e in items:
        io = e.get("involvedObject") or e.get("regarding") or {}
        group = KIND_GROUPS.get(io.get("kind"))
        if not group:
            continue
        rows.append({"group": group, "kind": io.get("kind"), "name": io.get("name"),
                     "namespace": io.get("namespace") or "", "type": e.get("type") or "Normal",
                     "reason": e.get("reason") or "", "message": (e.get("message") or e.get("note") or "")[:1000],
                     "count": e.get("count") or ((e.get("series") or {}).get("count")) or 1,
                     "last": _when(e), "first": e.get("firstTimestamp") or "",
                     "source": ((e.get("source") or {}).get("component") or e.get("reportingComponent")
                                or e.get("reportingController") or "")})
    rows.sort(key=lambda r: r["last"], reverse=True)
    return rows[:limit]


def usage(nodes, node_metrics, lh_nodes, over_provisioning=100):
    """Jauges du tableau de bord : CPU et mémoire (utilisé, réservé, total)
    et stockage Longhorn (utilisé, promis aux volumes, allouable, total)."""
    live = {(m.get("metadata") or {}).get("name"): m.get("usage") or {} for m in node_metrics}
    cpu = {"used": 0.0, "reserved": 0.0, "total": 0.0, "live": bool(live)}
    mem = {"used": 0.0, "reserved": 0.0, "total": 0.0, "live": bool(live)}
    for n in nodes:
        name = (n.get("metadata") or {}).get("name")
        cap = (n.get("status") or {}).get("capacity") or {}
        cpu["total"] += qty(cap.get("cpu"))
        mem["total"] += qty(cap.get("memory"))
        u = live.get(name) or {}
        cpu["used"] += qty(u.get("cpu"))
        mem["used"] += qty(u.get("memory"))
        try:
            req = json.loads(((n.get("metadata") or {}).get("annotations") or {}).get("management.cattle.io/pod-requests") or "{}")
        except ValueError:
            req = {}
        cpu["reserved"] += qty(req.get("cpu"))
        mem["reserved"] += qty(req.get("memory"))
    sto = {"used": 0, "scheduled": 0, "reserved": 0, "total": 0, "allocatable": 0}
    for ln in lh_nodes:
        spec_disks = ((ln.get("spec") or {}).get("disks")) or {}
        if (ln.get("spec") or {}).get("allowScheduling") is False:
            continue
        for dname, st in (((ln.get("status") or {}).get("diskStatus")) or {}).items():
            d = spec_disks.get(dname) or {}
            if d.get("allowScheduling") is False:
                continue
            mx, av = int(st.get("storageMaximum") or 0), int(st.get("storageAvailable") or 0)
            rs = int(d.get("storageReserved") or 0)
            sto["total"] += mx
            sto["used"] += max(mx - av, 0)
            sto["reserved"] += rs
            sto["scheduled"] += int(st.get("storageScheduled") or 0)
    sto["allocatable"] = int((sto["total"] - sto["reserved"]) * (over_provisioning or 100) / 100)
    sto["over_provisioning"] = over_provisioning
    return {"cpu": cpu, "memory": mem, "storage": sto}
