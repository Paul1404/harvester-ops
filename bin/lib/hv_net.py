"""harvester-ops : le menu « Networks » de Harvester (v1.65.0).

Réseaux de cluster et leurs configurations (VlanConfig : cartes, bond, MTU,
nœuds visés, migration vers un autre réseau), réseaux de VM (VLAN, sans
étiquette, trunk, route), équilibreurs de charge et pools d'adresses, réseaux
d'hôte (HostNetworkConfig), réseaux de stockage, de migration et RWX (réglages).
Relevé dans harvester-ui-extension, harvester, network-controller-harvester et
load-balancer-harvester v1.9.0 (scratchpad du chantier :
parity/harvester-network-formats-1.9.md).

Fonctions pures ; bin/harvester-resources.py les applique.
"""

import copy
import ipaddress
import json
import re

K_CN = "clusternetworks.network.harvesterhci.io"
K_VC = "vlanconfigs.network.harvesterhci.io"
K_VS = "vlanstatuses.network.harvesterhci.io"
K_LM = "linkmonitors.network.harvesterhci.io"
K_HNC = "hostnetworkconfigs.network.harvesterhci.io"
K_LB = "loadbalancers.loadbalancer.harvesterhci.io"
K_POOL = "ippools.loadbalancer.harvesterhci.io"
K_NAD = "network-attachment-definitions.k8s.cni.cncf.io"
K_SETTING = "settings.harvesterhci.io"
DESC = "field.cattle.io/description"
ROUTE = "network.harvesterhci.io/route"
MTU_ANN = "network.harvesterhci.io/uplink-mtu"
MATCHED = "network.harvesterhci.io/matched-nodes"
CN_LABEL = "network.harvesterhci.io/clusternetwork"
TYPE_LABEL = "network.harvesterhci.io/type"
WITNESS = "node-role.harvesterhci.io/witness"
RELEASE = "loadbalancer.harvesterhci.io/manuallyReleaseIP"
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
LABEL_KEY_RE = re.compile(r"^([a-z0-9]([-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$")
LABEL_VAL_RE = re.compile(r"^([A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?)?$")
BOND_MODES = ("active-backup", "balance-rr", "balance-xor", "broadcast", "802.3ad", "balance-tlb", "balance-alb")
NET_SETTINGS = ("storage-network", "vm-migration-network", "rwx-network")


def check_name(name, what="name", limit=63):
    if not NAME_RE.match(name or "") or len(name) > limit:
        raise ValueError(f"{what}: lower-case letters, digits and dashes, {limit} at most")
    return name


def _meta(o):
    return (o or {}).get("metadata") or {}


def _desc(o):
    return (_meta(o).get("annotations") or {}).get(DESC) or ""


def _with_desc(meta, description):
    ann = meta.setdefault("annotations", {})
    if description:
        ann[DESC] = str(description)[:1000]
    else:
        ann.pop(DESC, None)
    return meta


def _ready(o):
    """La condition ready d'un objet, ou d'une entrée par nœud (nodeStatus
    d'un réseau d'hôte : ses conditions sont à la racine de l'entrée)."""
    o = o or {}
    conds = o.get("conditions") if "conditions" in o else (o.get("status") or {}).get("conditions")
    for c in conds or []:
        if str(c.get("type", "")).lower() == "ready":
            return str(c.get("status")) == "True", c.get("message") or ""
    return None, ""


def _int(v, what, lo=None, hi=None, allow_none=True):
    if v in (None, "") and allow_none:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: a whole number") from None
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise ValueError(f"{what}: from {lo} to {hi}")
    return n


# ---------------------------------------------------------------------------
# Réseaux de cluster et configurations (VlanConfig)
# ---------------------------------------------------------------------------

def cluster_network(name, description=""):
    """Un réseau de cluster : un nom de 12 caractères au plus (le pont s'appelle
    `<nom>-br`, 15 caractères pour un nom d'interface Linux)."""
    check_name(name, "cluster network name", 12)
    if name == "mgmt":
        raise ValueError("mgmt exists already and is managed by Harvester")
    return {"apiVersion": "network.harvesterhci.io/v1beta1", "kind": "ClusterNetwork",
            "metadata": _with_desc({"name": name}, description)}


def node_selector(sel):
    """« Tous les nœuds », « un nœud » ou des étiquettes, comme le formulaire
    de Harvester. Rend le dict de spec.nodeSelector, ou None (tous)."""
    sel = sel or {}
    mode = sel.get("mode") or "all"
    if mode == "all":
        return None
    if mode == "node":
        node = str(sel.get("node") or "").strip()
        if not node:
            raise ValueError("node: pick the node this configuration applies to")
        return {"kubernetes.io/hostname": node}
    if mode == "labels":
        labels = sel.get("labels") or {}
        if not labels:
            raise ValueError("node labels: at least one key=value")
        for k, v in labels.items():
            if not LABEL_KEY_RE.match(k) or not LABEL_VAL_RE.match(str(v)):
                raise ValueError(f"node label {k}={v}: not a valid label")
        return {k: str(v) for k, v in labels.items()}
    raise ValueError("applies to: all nodes, one node, or node labels")


def selected_nodes(nodes, selector):
    """Les nœuds (objets Node) que retient un nodeSelector d'égalités, sans
    les témoins, comme l'annotation matched-nodes du mutateur."""
    out = []
    for n in nodes or []:
        labels = _meta(n).get("labels") or {}
        if WITNESS in labels:
            continue
        if all(labels.get(k) == v for k, v in (selector or {}).items()):
            out.append(_meta(n).get("name"))
    return sorted(out)


def vlan_config(spec, current=None):
    """Une configuration réseau : quelles cartes de quels nœuds forment le
    bond de ce réseau de cluster, avec son mode, son miimon et son MTU."""
    name = check_name(spec.get("name"), "configuration name")
    cn = check_name(spec.get("cluster_network"), "cluster network", 12)
    if cn == "mgmt":
        raise ValueError("the configurations of mgmt are written by Harvester from the installed bond")
    nics = [str(n).strip() for n in spec.get("nics") or [] if str(n).strip()]
    if not nics:
        raise ValueError("uplink: at least one network card")
    for n in nics:
        if not re.match(r"^[A-Za-z0-9_.:-]{1,15}$", n):
            raise ValueError(f"network card {n!r}: not an interface name")
    mode = spec.get("bond_mode") or "active-backup"
    if mode not in BOND_MODES:
        raise ValueError("bond mode: " + ", ".join(BOND_MODES))
    miimon = _int(spec.get("miimon"), "miimon", -1, 100000)
    mtu = _int(spec.get("mtu"), "MTU")
    if mtu is not None and mtu != 0 and not 576 <= mtu <= 9000:
        raise ValueError("MTU: from 576 to 9000 (0 or empty keeps 1500)")
    uplink = {"nics": nics, "bondOptions": {"mode": mode}}
    if miimon is not None:
        uplink["bondOptions"]["miimon"] = miimon
    if mtu:
        uplink["linkAttributes"] = {"mtu": mtu}
    body = {"clusterNetwork": cn, "uplink": uplink}
    if spec.get("description"):
        body["description"] = str(spec["description"])[:1000]
    sel = node_selector(spec.get("nodes"))
    if sel:
        body["nodeSelector"] = sel
    if current is not None:
        out = copy.deepcopy(current)
        if _meta(out).get("name") != name:
            raise ValueError("a configuration keeps its name")
        # les attributs que le formulaire de Harvester ne montre pas restent
        old = ((current.get("spec") or {}).get("uplink") or {}).get("linkAttributes") or {}
        la = {k: v for k, v in old.items() if k != "mtu"}
        if mtu:
            la["mtu"] = mtu
        if la:
            uplink["linkAttributes"] = la
        else:
            uplink.pop("linkAttributes", None)
        out["spec"] = body
        return out
    return {"apiVersion": "network.harvesterhci.io/v1beta1", "kind": "VlanConfig",
            "metadata": {"name": name}, "spec": body}


def nic_choices(link_status, nodes, current=()):
    """Les cartes qu'on peut proposer pour une configuration : présentes sur
    TOUS les nœuds visés, pas déjà esclaves d'un autre bond (sauf si la
    configuration les a déjà), levées partout. `link_status` est le
    status.linkStatus du LinkMonitor `nic`."""
    current = set(current or ())
    per_node = {n: {l.get("name"): l for l in (link_status or {}).get(n) or []} for n in nodes}
    if not nodes:
        return []
    names = set.intersection(*(set(v) for v in per_node.values())) if per_node else set()
    out = []
    for name in sorted(names):
        links = [per_node[n][name] for n in nodes]
        enslaved = [n for n in nodes if per_node[n][name].get("masterIndex") not in (None, 0)]
        down = [n for n in nodes if per_node[n][name].get("state") == "down"]
        why = ""
        if enslaved and name not in current:
            why = "enslaved"
        elif down:
            why = "down"
        out.append({"name": name, "usable": not why, "why": why, "enslaved_on": enslaved, "down_on": down,
                    "mac": {n: per_node[n][name].get("mac") for n in nodes},
                    "state": {n: l.get("state") for n, l in zip(nodes, links)}})
    return out


def migrate_patch(target):
    check_name(target, "target cluster network", 12)
    if target == "mgmt":
        raise ValueError("a configuration cannot move to mgmt")
    return {"spec": {"clusterNetwork": target}}


def config_status(vc, statuses):
    """L'état d'une configuration nœud par nœud, d'après ses VlanStatus."""
    name = _meta(vc).get("name")
    out = {}
    for s in statuses or []:
        st = s.get("status") or {}
        if st.get("vlanConfig") != name:
            continue
        ok, msg = _ready(s)
        out[st.get("node")] = {"ready": ok, "message": msg}
    return out


GRACE = 60      # s : l'agent d'un nœud réessaie ; vu en réel, une première
                # erreur (« set vlan filtering failed ») suivie du succès


def config_settled(vc, statuses, nodes, elapsed=None, grace=GRACE):
    """Une configuration est en place quand chaque nœud visé a un VlanStatus
    prêt ; un refus de l'agent qui dure au-delà du délai de grâce se dit tel
    quel. Rend (fini, message)."""
    sel = (vc.get("spec") or {}).get("nodeSelector")
    want = selected_nodes(nodes, sel)
    st = config_status(vc, statuses)
    bad = {n: s["message"] for n, s in st.items() if s["ready"] is False and s["message"]}
    if bad:
        n, m = next(iter(bad.items()))
        if elapsed is not None and elapsed < grace:
            return None, f"{n}: {m} (the node retries)"
        return False, f"{n}: {m}"
    missing = [n for n in want if not (st.get(n) or {}).get("ready")]
    if missing:
        return None, "waiting for " + ", ".join(missing)
    return True, f"ready on {len(want)} node(s)" if want else "no node matches this configuration"


def _nad_type(nad):
    labels = _meta(nad).get("labels") or {}
    if labels.get(TYPE_LABEL):
        return labels[TYPE_LABEL]
    try:
        c = json.loads((nad.get("spec") or {}).get("config") or "{}")
    except ValueError:
        return ""
    if c.get("type") == "kube-ovn":
        return "OverlayNetwork"
    if c.get("vlanTrunk"):
        return "L2VlanTrunkNetwork"
    return "L2VlanNetwork" if c.get("vlan") else "UntaggedNetwork"


def nad_cluster_network(nad):
    labels = _meta(nad).get("labels") or {}
    if labels.get(CN_LABEL):
        return labels[CN_LABEL]
    try:
        c = json.loads((nad.get("spec") or {}).get("config") or "{}")
    except ValueError:
        return ""
    br = c.get("bridge") or ""
    return br[:-3] if br.endswith("-br") else ""


def cluster_network_rows(cns, vcs, statuses, nads, nodes):
    """La page « Cluster Network Configuration » : chaque réseau, ses
    configurations et leur état par nœud, et les réseaux de VM qui en
    dépendent (ce qui bloque sa suppression)."""
    out = []
    for cn in sorted(cns or [], key=lambda o: (_meta(o).get("name") != "mgmt", _meta(o).get("name"))):
        name = _meta(cn).get("name")
        ok, msg = _ready(cn)
        configs = []
        for vc in vcs or []:
            spec = vc.get("spec") or {}
            if spec.get("clusterNetwork") != name:
                continue
            sel = spec.get("nodeSelector")
            up = spec.get("uplink") or {}
            configs.append({
                "name": _meta(vc).get("name"), "description": spec.get("description") or "",
                "nics": up.get("nics") or [], "bond_mode": (up.get("bondOptions") or {}).get("mode") or "active-backup",
                "miimon": (up.get("bondOptions") or {}).get("miimon"),
                "mtu": (up.get("linkAttributes") or {}).get("mtu") or 1500,
                "selector": sel or {}, "nodes": selected_nodes(nodes, sel),
                "status": config_status(vc, statuses), "managed": name == "mgmt"})
        nets = sorted(f"{_meta(n).get('namespace')}/{_meta(n).get('name')}" for n in nads or []
                      if nad_cluster_network(n) == name and _nad_type(n) != "OverlayNetwork")
        mtu = (_meta(cn).get("annotations") or {}).get(MTU_ANN)
        out.append({"name": name, "description": _desc(cn), "ready": ok if name != "mgmt" else True,
                    "message": msg, "mtu": int(mtu) if str(mtu or "").isdigit() else None,
                    "protected": name == "mgmt", "configs": sorted(configs, key=lambda c: c["name"]),
                    "networks": nets})
    return out


def delete_cluster_network_check(row):
    if row["protected"]:
        raise ValueError("mgmt cannot be deleted")
    if row["configs"]:
        raise ValueError("delete its network configurations first: " + ", ".join(c["name"] for c in row["configs"]))
    if row["networks"]:
        raise ValueError("delete the VM networks that use it first: " + ", ".join(row["networks"]))


def vms_on(vmis, refs):
    """Les VMs EN MARCHE branchées sur l'un des réseaux (ns/nom) donnés."""
    refs = set(refs or ())
    out = []
    for v in vmis or []:
        ns = _meta(v).get("namespace")
        for n in ((v.get("spec") or {}).get("networks") or []):
            net = (n.get("multus") or {}).get("networkName") or ""
            ref = net if "/" in net else f"{ns}/{net}"
            if net and ref in refs:
                out.append(f"{ns}/{_meta(v).get('name')}")
                break
    return sorted(set(out))


def update_blockers(cur, new, nodes, vmis, nads):
    """Les VMs en marche qui empêchent de modifier une configuration, selon
    la règle du webhook : réseau ou lien changés, toutes les VMs du réseau
    sur les anciens nœuds ; sinon celles des nœuds qui sortent seulement.
    Une description changée ou un nœud ajouté ne bloque rien."""
    old_s, new_s = cur.get("spec") or {}, new.get("spec") or {}
    cn = old_s.get("clusterNetwork")
    old_nodes = set(selected_nodes(nodes, old_s.get("nodeSelector")))
    if old_s.get("clusterNetwork") != new_s.get("clusterNetwork") or old_s.get("uplink") != new_s.get("uplink"):
        touched = old_nodes
    else:
        touched = old_nodes - set(selected_nodes(nodes, new_s.get("nodeSelector")))
    if not touched:
        return []
    refs = [f"{_meta(n).get('namespace')}/{_meta(n).get('name')}" for n in nads or [] if nad_cluster_network(n) == cn]
    on_nodes = [v for v in vmis or [] if ((v.get("status") or {}).get("nodeName")) in touched]
    return vms_on(on_nodes, refs)


# ---------------------------------------------------------------------------
# Réseaux de VM : trunk, route, modification
# ---------------------------------------------------------------------------

def trunk_ranges(ranges):
    """Des plages de VLAN pour un trunk : [{min, max}] (1-4094, min <= max)."""
    out = []
    for r in ranges or []:
        lo = _int(r.get("min"), "VLAN range start", 1, 4094, allow_none=False)
        hi = _int(r.get("max") if r.get("max") not in (None, "") else lo, "VLAN range end", 1, 4094, allow_none=False)
        if lo > hi:
            raise ValueError(f"VLAN range {lo}-{hi}: the start comes first")
        out.append({"minID": lo, "maxID": hi})
    if not out:
        raise ValueError("trunk: at least one VLAN range")
    return out


def route_annotation(spec):
    """L'annotation route (informative pour Harvester : un Job DHCP en auto,
    une sonde de la passerelle ensuite), comme le formulaire l'écrit."""
    mode = spec.get("route_mode") or "auto"
    route = {"mode": mode, "serverIPAddr": "", "cidr": "", "gateway": ""}
    server = str(spec.get("dhcp_server") or "").strip()
    if server:
        try:
            ipaddress.IPv4Address(server)
        except ValueError:
            raise ValueError("DHCP server: an IPv4 address") from None
        route["serverIPAddr"] = server
    if mode == "manual":
        cidr, gw = str(spec.get("cidr") or "").strip(), str(spec.get("gateway") or "").strip()
        try:
            net = ipaddress.IPv4Network(cidr, strict=False)
        except ValueError:
            raise ValueError("cidr: an IPv4 network like 192.168.10.0/24") from None
        if net.prefixlen == 0:
            raise ValueError("cidr: a real network, not 0.0.0.0/0")
        try:
            ipaddress.IPv4Address(gw)
        except ValueError:
            raise ValueError("gateway: an IPv4 address") from None
        route.update(cidr=cidr, gateway=gw)
    elif mode != "auto":
        raise ValueError("route: auto (DHCP) or manual")
    return json.dumps(route, separators=(",", ":"))


def vmnet_row(nad):
    """Ce que la fenêtre de modification d'un réseau de VM montre."""
    try:
        cfg = json.loads((nad.get("spec") or {}).get("config") or "{}")
    except ValueError:
        cfg = {}
    ann = _meta(nad).get("annotations") or {}
    try:
        route = json.loads(ann.get(ROUTE) or "{}")
    except ValueError:
        route = {}
    return {"namespace": _meta(nad).get("namespace"), "name": _meta(nad).get("name"),
            "type": _nad_type(nad), "cluster_network": nad_cluster_network(nad),
            "vlan": cfg.get("vlan") or 0,
            "ranges": [{"min": r.get("minID", r.get("id")), "max": r.get("maxID", r.get("id"))}
                       for r in cfg.get("vlanTrunk") or []],
            "route": {"mode": route.get("mode") or "auto", "dhcp_server": route.get("serverIPAddr") or "",
                      "cidr": route.get("cidr") or "", "gateway": route.get("gateway") or "",
                      "connectivity": route.get("connectivity") or ""},
            "description": _desc(nad)}


def network_update(nad, spec):
    """Modifier un réseau de VM comme Harvester le permet : description et
    route toujours ; le VLAN ou les plages du trunk seulement VMs arrêtées
    (le webhook le vérifie) ; ni le type ni le réseau de cluster."""
    out = copy.deepcopy(nad)
    meta = out.setdefault("metadata", {})
    _with_desc(meta, spec.get("description"))
    try:
        cfg = json.loads((out.get("spec") or {}).get("config") or "{}")
    except ValueError:
        raise ValueError("this network's configuration is not JSON") from None
    kind = _nad_type(nad)
    if kind == "OverlayNetwork":
        raise ValueError("an overlay network is changed through its subnet (Overlay Networks)")
    changed = False
    if kind == "L2VlanNetwork" and spec.get("vlan") not in (None, ""):
        vlan = _int(spec.get("vlan"), "vlan", 1, 4094, allow_none=False)
        changed = vlan != cfg.get("vlan")
        cfg["vlan"] = vlan
    if kind == "L2VlanTrunkNetwork" and spec.get("ranges") is not None:
        ranges = trunk_ranges(spec["ranges"])
        changed = ranges != cfg.get("vlanTrunk")
        cfg["vlanTrunk"] = ranges
    if kind in ("L2VlanNetwork", "UntaggedNetwork") and spec.get("route_mode"):
        meta.setdefault("annotations", {})[ROUTE] = route_annotation(spec)
    out.setdefault("spec", {})["config"] = json.dumps(cfg, separators=(",", ":"))
    return out, changed


# ---------------------------------------------------------------------------
# Équilibreurs de charge et pools d'adresses
# ---------------------------------------------------------------------------

def selector_map(text_or_map):
    """« clé=a,b » par ligne (ou un dict clé -> liste) vers le
    backendServerSelector : clé in (valeurs), ET entre clés."""
    if isinstance(text_or_map, dict):
        items = text_or_map.items()
    else:
        items = []
        for line in str(text_or_map or "").splitlines():
            line = line.strip()
            if not line:
                continue
            if "=" not in line:
                raise ValueError(f"selector line {line!r}: key=value[,value]")
            k, _, v = line.partition("=")
            items.append((k.strip(), v))
    out = {}
    for k, v in items:
        vals = [x.strip() for x in (v if isinstance(v, list) else str(v).split(",")) if x.strip()]
        if not LABEL_KEY_RE.match(k) or not vals or not all(LABEL_VAL_RE.match(x) for x in vals):
            raise ValueError(f"selector {k}: a label key and one or more values")
        out[k] = vals
    return out


def load_balancer(spec, current=None):
    """Un équilibreur de VMs : DHCP ou pool, écouteurs, sélecteur des VMs,
    sonde TCP ; contrôles du webhook faits d'avance."""
    name = check_name(spec.get("name"), "load balancer name")
    ns = check_name(spec.get("namespace") or "default", "namespace")
    ipam = spec.get("ipam") or "dhcp"
    if ipam not in ("dhcp", "pool"):
        raise ValueError("IPAM: dhcp or pool")
    listeners, names, fronts, backs = [], set(), set(), set()
    for i, l in enumerate(spec.get("listeners") or []):
        lname = str(l.get("name") or f"l{i + 1}").strip()
        proto = str(l.get("protocol") or "TCP").upper()
        if proto not in ("TCP", "UDP"):
            raise ValueError("listener protocol: TCP or UDP")
        port = _int(l.get("port"), f"listener {lname} port", 1, 65535, allow_none=False)
        back = _int(l.get("backend_port"), f"listener {lname} backend port", 1, 65535, allow_none=False)
        if lname in names:
            raise ValueError(f"listener name {lname} used twice")
        if (proto, port) in fronts:
            raise ValueError(f"{proto} port {port} used twice")
        if (proto, back) in backs:
            raise ValueError(f"{proto} backend port {back} used twice")
        names.add(lname), fronts.add((proto, port)), backs.add((proto, back))
        listeners.append({"name": lname, "port": port, "protocol": proto, "backendPort": back})
    if not listeners:
        raise ValueError("a load balancer needs at least one listener")
    body = {"workloadType": "vm", "ipam": ipam, "listeners": listeners,
            "backendServerSelector": selector_map(spec.get("selector") or {})}
    if ipam == "pool" and spec.get("ip_pool"):
        body["ipPool"] = check_name(spec["ip_pool"], "IP pool")
    if spec.get("description"):
        body["description"] = str(spec["description"])[:1000]
    hc = spec.get("health") or {}
    if hc.get("port"):
        hport = _int(hc.get("port"), "health check port", 1, 65535, allow_none=False)
        if ("TCP", hport) not in backs:
            raise ValueError("health check port: the backend port of a TCP listener")
        body["healthCheck"] = {"port": hport}
        for key, field in (("success", "successThreshold"), ("failure", "failureThreshold"),
                           ("period", "periodSeconds"), ("timeout", "timeoutSeconds")):
            v = _int(hc.get(key), f"health check {key}", 1, 3600)
            if v is not None:
                body["healthCheck"][field] = v
    if current is not None:
        old = current.get("spec") or {}
        if (old.get("ipam") or "pool") != ipam:
            raise ValueError("the IPAM of a load balancer cannot change (dhcp <-> pool): create another one")
        out = copy.deepcopy(current)
        out["spec"] = {**old, **body}
        if "healthCheck" not in body:
            out["spec"].pop("healthCheck", None)
        if "ipPool" not in body:
            out["spec"].pop("ipPool", None)
        return out
    return {"apiVersion": "loadbalancer.harvesterhci.io/v1beta1", "kind": "LoadBalancer",
            "metadata": {"name": name, "namespace": ns}, "spec": body}


def lb_rows(lbs):
    out = []
    for lb in lbs or []:
        s, st = lb.get("spec") or {}, lb.get("status") or {}
        ok, msg = _ready(lb)
        alloc = st.get("allocatedAddress") or {}
        out.append({"namespace": _meta(lb).get("namespace"), "name": _meta(lb).get("name"),
                    "description": s.get("description") or "", "workload": s.get("workloadType") or "vm",
                    "ipam": s.get("ipam") or "pool", "ip_pool": alloc.get("ipPool") or s.get("ipPool") or "",
                    "address": st.get("address") or (alloc.get("ip") if alloc.get("ip") not in (None, "0.0.0.0") else ""),
                    "backends": st.get("backendServers") or [], "listeners": s.get("listeners") or [],
                    "selector": s.get("backendServerSelector") or {}, "health": s.get("healthCheck") or {},
                    "ready": ok, "message": msg, "created": _meta(lb).get("creationTimestamp")})
    return out


def _net(text, what):
    try:
        net = ipaddress.IPv4Network(str(text or "").strip(), strict=False)
    except ValueError:
        raise ValueError(f"{what}: an IPv4 network like 192.168.10.0/24") from None
    return net


def pool_ranges(ranges):
    """Les plages d'un pool, normalisées comme l'allocateur de Harvester :
    début et fin dans le sous-réseau (défaut : .1 et la dernière avant la
    diffusion), passerelle dans le sous-réseau, pas de chevauchement."""
    out, spans = [], []
    for r in ranges or []:
        net = _net(r.get("subnet"), "subnet")
        entry = {"subnet": str(net)}
        hosts = (net.network_address + 1, net.broadcast_address - 1) if net.prefixlen < 31 else (net.network_address, net.broadcast_address)
        lo, hi = hosts
        for key, field in (("start", "rangeStart"), ("end", "rangeEnd"), ("gateway", "gateway")):
            v = str(r.get(key) or "").strip()
            if not v:
                continue
            try:
                ip = ipaddress.IPv4Address(v)
            except ValueError:
                raise ValueError(f"{key}: an IPv4 address") from None
            if ip not in net or (net.prefixlen < 31 and ip in (net.network_address, net.broadcast_address)):
                raise ValueError(f"{key} {ip}: inside {net}, neither its network nor its broadcast address")
            entry[field] = str(ip)
            if key == "start":
                lo = ip
            elif key == "end":
                hi = ip
        if lo > hi:
            raise ValueError(f"range {lo}-{hi}: the start comes first")
        for a, b in spans:
            if not (hi < a or lo > b):
                raise ValueError("there are overlaps between the ranges")
        spans.append((lo, hi))
        out.append(entry)
    if not out:
        raise ValueError("an IP pool needs at least one range")
    return out, spans


def ip_pool(spec, current=None):
    name = check_name(spec.get("name"), "IP pool name")
    ranges, spans = pool_ranges(spec.get("ranges"))
    body = {"ranges": ranges}
    if spec.get("description"):
        body["description"] = str(spec["description"])[:1000]
    sel = {}
    net = str(spec.get("network") or "").strip()
    if net:
        if not re.match(r"^[a-z0-9-]+/[a-z0-9-.]+$", net):
            raise ValueError("network: namespace/name of a VM network")
        sel["network"] = net
    prio = _int(spec.get("priority"), "priority", 0, 2 ** 31)
    if prio:
        sel["priority"] = prio
    scope = []
    for s in spec.get("scope") or [{"namespace": "*"}]:
        t = {k: str(s.get(k) or "").strip() for k in ("namespace", "project", "guestCluster") if str(s.get(k) or "").strip()}
        if t:
            scope.append(t)
    if scope:
        sel["scope"] = scope
    if sel:
        body["selector"] = sel
    if current is not None:
        allocated = ((current.get("status") or {}).get("allocated") or {})
        for ip in allocated:
            a = ipaddress.IPv4Address(ip)
            if not any(lo <= a <= hi for lo, hi in spans):
                raise ValueError(f"allocated IP {ip} ({allocated[ip]}) would fall outside the ranges")
        out = copy.deepcopy(current)
        out["spec"] = body
        return out
    return {"apiVersion": "loadbalancer.harvesterhci.io/v1beta1", "kind": "IPPool",
            "metadata": {"name": name}, "spec": body}


def pool_rows(pools):
    out = []
    for p in pools or []:
        s, st = p.get("spec") or {}, p.get("status") or {}
        ok, msg = _ready(p)
        sel = s.get("selector") or {}
        out.append({"name": _meta(p).get("name"), "description": s.get("description") or "",
                    "ranges": s.get("ranges") or [], "network": sel.get("network") or "",
                    "priority": sel.get("priority") or 0, "scope": sel.get("scope") or [],
                    "total": st.get("total"), "available": st.get("available"),
                    "allocated": st.get("allocated") or {}, "ready": ok, "message": msg,
                    "global": (_meta(p).get("labels") or {}).get("loadbalancer.harvesterhci.io/global-ip-pool") == "true",
                    "created": _meta(p).get("creationTimestamp")})
    return out


def delete_pool_check(pool):
    allocated = ((pool or {}).get("status") or {}).get("allocated") or {}
    if allocated:
        raise ValueError("release its allocated addresses first: " + ", ".join(f"{ip} ({lb})" for ip, lb in allocated.items()))


def release_patch(pool, ip):
    allocated = ((pool or {}).get("status") or {}).get("allocated") or {}
    if ip not in allocated:
        raise ValueError(f"{ip} is not allocated in this pool")
    return {"metadata": {"annotations": {RELEASE: f"{ip}: {allocated[ip]}"}}}


# ---------------------------------------------------------------------------
# Réseaux d'hôte (HostNetworkConfig)
# ---------------------------------------------------------------------------

def host_network(spec, nodes, current=None):
    """Une adresse sur chaque nœud visé, sur un VLAN d'un réseau de cluster
    (interface `<réseau>-br.<vlan>`), statique ou par DHCP ; underlay des
    réseaux overlay si demandé. Aucune route n'est posée par Harvester."""
    name = check_name(spec.get("name"), "host network name")
    cn = check_name(spec.get("cluster_network"), "cluster network", 12)
    vlan = _int(spec.get("vlan"), "VLAN ID", 1, 4094, allow_none=False)
    if len(f"{cn}-br.{vlan}") > 15:
        raise ValueError(f"{cn}-br.{vlan} is longer than the 15 characters of a Linux interface name")
    mode = spec.get("mode") or "dhcp"
    if mode not in ("dhcp", "static"):
        raise ValueError("mode: dhcp or static")
    targets = [str(n) for n in spec.get("nodes") or []]
    body = {"clusterNetwork": cn, "vlanID": vlan, "mode": mode, "underlay": bool(spec.get("underlay"))}
    if spec.get("description"):
        body["description"] = str(spec["description"])[:1000]
    if targets:
        body["nodeSelector"] = {"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In", "values": targets}]}
    if mode == "static":
        want = targets or sorted(_meta(n).get("name") for n in nodes or [])
        ips = {k: str(v).strip() for k, v in (spec.get("ips") or {}).items() if str(v).strip()}
        nets = set()
        for n in want:
            if n not in ips:
                raise ValueError(f"static IP not found for node {n}")
            try:
                iface = ipaddress.IPv4Interface(ips[n])
            except ValueError:
                raise ValueError(f"{n}: an address with its prefix, like 10.0.20.11/24") from None
            if "/" not in ips[n]:
                raise ValueError(f"{n}: an address with its prefix, like 10.0.20.11/24")
            nets.add(iface.network)
        if len(nets) > 1:
            raise ValueError("static IPs are not in the same subnet")
        body["ips"] = {n: ips[n] for n in want}
    if current is not None:
        old = current.get("spec") or {}
        if old.get("clusterNetwork") != cn or int(old.get("vlanID") or 0) != vlan:
            raise ValueError("the cluster network and VLAN of a host network cannot change: create another one")
        out = copy.deepcopy(current)
        out["spec"] = body
        return out
    return {"apiVersion": "network.harvesterhci.io/v1beta1", "kind": "HostNetworkConfig",
            "metadata": {"name": name}, "spec": body}


def hostnet_rows(hncs):
    out = []
    for h in hncs or []:
        s, st = h.get("spec") or {}, h.get("status") or {}
        ok, msg = _ready(h)
        per = {}
        for node, ns in (st.get("nodeStatus") or {}).items():
            nok, nmsg = _ready(ns)
            per[node] = {"ready": nok, "message": nmsg}
        exprs = ((s.get("nodeSelector") or {}).get("matchExpressions") or [])
        nodes = next((e.get("values") for e in exprs if e.get("key") == "kubernetes.io/hostname"), [])
        out.append({"name": _meta(h).get("name"), "description": s.get("description") or "",
                    "cluster_network": s.get("clusterNetwork"), "vlan": s.get("vlanID"), "mode": s.get("mode"),
                    "underlay": bool(s.get("underlay")), "ips": s.get("ips") or {}, "nodes": nodes or [],
                    "interface": f"{s.get('clusterNetwork')}-br.{s.get('vlanID')}",
                    "node_status": per, "ready": ok, "message": msg,
                    "created": _meta(h).get("creationTimestamp")})
    return out


def hostnet_settled(obj, nodes, elapsed=None, grace=GRACE):
    s = (obj or {}).get("spec") or {}
    exprs = ((s.get("nodeSelector") or {}).get("matchExpressions") or [])
    want = next((e.get("values") for e in exprs if e.get("key") == "kubernetes.io/hostname"), None) \
        or sorted(_meta(n).get("name") for n in nodes or [] if WITNESS not in (_meta(n).get("labels") or {}))
    per = ((obj or {}).get("status") or {}).get("nodeStatus") or {}
    for n in want:
        ok, msg = _ready(per.get(n) or {})
        if ok is False and msg:
            if elapsed is not None and elapsed < grace:
                return None, f"{n}: {msg} (the node retries)"
            return False, f"{n}: {msg}"
    missing = [n for n in want if _ready(per.get(n) or {})[0] is not True]
    if missing:
        return None, "waiting for " + ", ".join(missing)
    return True, f"ready on {len(want)} node(s)"


def delete_hostnet_check(obj):
    if ((obj or {}).get("spec") or {}).get("underlay"):
        raise ValueError("underlay is enabled: disable underlay first")


# ---------------------------------------------------------------------------
# Réseaux de stockage, de migration et RWX (réglages)
# ---------------------------------------------------------------------------

def _dedicated(spec, kind):
    cn = check_name(spec.get("cluster_network"), "cluster network", 12)
    vlan = _int(spec.get("vlan"), "VLAN ID", 0, 4094)
    if kind == "vm-migration-network" and not vlan:
        raise ValueError("the migration network needs a VLAN ID (1-4094)")
    if cn == "mgmt" and (vlan or 0) <= 1:
        raise ValueError(f"network with vlan id {vlan or 0} not allowed on mgmt cluster")
    rng = _net(spec.get("range"), "range")
    if str(rng.network_address) != str(spec.get("range")).strip().split("/")[0]:
        raise ValueError("range should be subnet CIDR (its network address)")
    if rng.prefixlen < 16:
        raise ValueError("range: /16 at the largest")
    value = {"clusterNetwork": cn, "range": str(rng)}
    if vlan:
        value["vlan"] = vlan
    excl = []
    for e in spec.get("exclude") or []:
        en = _net(e, "exclude")
        if en.prefixlen < 16 or not en.subnet_of(rng):
            raise ValueError(f"exclude {e}: inside {rng}, /16 at the largest")
        excl.append(str(en))
    for i, a in enumerate(excl):
        for b in excl[i + 1:]:
            if ipaddress.IPv4Network(a).overlaps(ipaddress.IPv4Network(b)):
                raise ValueError(f"excludes {a} and {b} overlap")
    if excl:
        value["exclude"] = excl
    if kind == "storage-network" and spec.get("exclusive_vlan"):
        if (vlan or 0) <= 1:
            raise ValueError("an exclusive VLAN needs a VLAN ID of 2 or more")
        value["exclusiveVlan"] = True
    return value


def setting_value(kind, spec):
    """La valeur JSON d'un des trois réglages réseau ; "" (ou le partage
    désactivé pour rwx) pour revenir au réseau de gestion."""
    if kind not in NET_SETTINGS:
        raise ValueError("setting: " + ", ".join(NET_SETTINGS))
    if spec.get("disable"):
        return json.dumps({"share-storage-network": False}) if kind == "rwx-network" else ""
    if kind == "rwx-network":
        if spec.get("share_storage"):
            return json.dumps({"share-storage-network": True}, separators=(",", ":"))
        return json.dumps({"share-storage-network": False, "network": _dedicated(spec, kind)}, separators=(",", ":"))
    return json.dumps(_dedicated(spec, kind), separators=(",", ":"))


# Les volumes que Harvester laisse attachés pendant le changement du réseau de
# stockage (webhook du réglage, getSystemVolumes) : supervision et import.
SYSTEM_VOLUME_APPS = (("cattle-monitoring-system", "prometheus"), ("cattle-monitoring-system", "alertmanager"),
                      ("cattle-monitoring-system", "grafana"), ("harvester-system", "harvester-vm-import-controller"))


def attached_volumes(volumes, pvcs):
    """Les volumes Longhorn encore attachés qui empêchent de changer le
    réseau de stockage, comme le webhook de Harvester les compte : tous sauf
    ceux de la supervision et de l'import de VMs (repérés par l'étiquette
    app.kubernetes.io/name de leur PVC). Rend ["ns/pvc (état)"]."""
    system = set()
    for pvc in pvcs or []:
        m = _meta(pvc)
        app = (m.get("labels") or {}).get("app.kubernetes.io/name")
        if (m.get("namespace"), app) in SYSTEM_VOLUME_APPS and (pvc.get("spec") or {}).get("volumeName"):
            system.add(pvc["spec"]["volumeName"])
    out = []
    for v in volumes or []:
        st = v.get("status") or {}
        if _meta(v).get("name") in system or st.get("state") == "detached":
            continue
        ks = st.get("kubernetesStatus") or {}
        who = f"{ks.get('namespace')}/{ks.get('pvcName')}" if ks.get("pvcName") else _meta(v).get("name")
        out.append(f"{who} ({st.get('state') or '?'})")
    return sorted(out)


def setting_rows(settings):
    out = []
    by = {_meta(s).get("name"): s for s in settings or []}
    for kind in NET_SETTINGS:
        s = by.get(kind) or {}
        raw = s.get("value") or ""
        try:
            val = json.loads(raw) if raw else {}
        except ValueError:
            val = {}
        cond = next((c for c in (s.get("status") or {}).get("conditions") or [] if c.get("type") == "configured"), {})
        ann = _meta(s).get("annotations") or {}
        nad = next((v for k, v in ann.items() if k.endswith("/net-attach-def") and "old-" not in k), "")
        if kind == "rwx-network":
            share = bool(val.get("share-storage-network"))
            enabled = share or bool(val.get("network"))
            net = val.get("network") or {}
        else:
            share, enabled, net = False, bool(val), val
        out.append({"name": kind, "enabled": enabled, "share_storage": share, "value": val,
                    "cluster_network": net.get("clusterNetwork") or "", "vlan": net.get("vlan") or 0,
                    "range": net.get("range") or "", "exclude": net.get("exclude") or [],
                    "exclusive_vlan": bool(net.get("exclusiveVlan")), "nad": nad,
                    "configured": cond.get("status"), "reason": cond.get("reason") or "",
                    "message": cond.get("message") or "", "exists": bool(s)})
    return out


def setting_settled(obj):
    """Appliqué = condition configured True (raison Completed pour le
    stockage) ; en cours tant que la raison est « In Progress »."""
    cond = next((c for c in ((obj or {}).get("status") or {}).get("conditions") or [] if c.get("type") == "configured"), None)
    if not cond:
        return None, "waiting for Harvester"
    if str(cond.get("status")) == "True":
        return True, cond.get("reason") or "configured"
    if (cond.get("reason") or "") in ("In Progress", "InProgress", ""):
        return None, cond.get("message") or "in progress"
    return False, f"{cond.get('reason')}: {cond.get('message') or ''}".strip()
