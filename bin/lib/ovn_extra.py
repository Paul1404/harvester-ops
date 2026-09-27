"""harvester-ops : la suite du menu kube-ovn de Harvester (v1.66.0).

Underlay (réseaux fournisseurs, VLANs, réseau externe d'une passerelle), NAT
(passerelles de VPC, IP externes, règles SNAT et DNAT) et politiques réseau
visant des VMs. Relevé dans harvester-ui-extension et harvester v1.9.0,
kube-ovn v1.15.4 et la doc de Harvester 1.9 (scratchpad du chantier :
parity/harvester-kubeovn-nat-formats-1.9.md).

Fonctions pures ; bin/harvester-network.py les applique.
"""

import copy
import ipaddress
import json
import re

import ovn_net as on

K_PN = "provider-networks.kubeovn.io"
K_VLAN = "vlans.kubeovn.io"
K_SUBNET = "subnets.kubeovn.io"
K_VPC = "vpcs.kubeovn.io"
K_GW = "vpc-nat-gateways.kubeovn.io"
K_EIP = "iptables-eips.kubeovn.io"
K_SNAT = "iptables-snat-rules.kubeovn.io"
K_DNAT = "iptables-dnat-rules.kubeovn.io"
K_NP = "networkpolicies.networking.k8s.io"
K_NAD = "network-attachment-definitions.k8s.cni.cncf.io"
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
IFACE_RE = re.compile(r"^[^/\s]{1,15}$")
NETWORKS_ANN = "k8s.v1.cni.cncf.io/networks"
FREE_RANGE = "harvester-ops.io/free-range"
ROUTE_ADDED = "harvester-ops.io/vpc-route"
GW_INIT = "ovn.kubernetes.io/vpc_nat_gw_init"
ENFORCEMENT = "ovn.kubernetes.io/network_policy_enforcement"
VM_LABEL = "vm.kubevirt.io/name"


def check_name(name, what="name", limit=63):
    if not NAME_RE.match(name or "") or len(name) > limit:
        raise ValueError(f"{what}: lower-case letters, digits and dashes, {limit} at most")
    return name


def _meta(o):
    return (o or {}).get("metadata") or {}


def _ip(v, what):
    try:
        return ipaddress.IPv4Address(str(v or "").strip())
    except ValueError:
        raise ValueError(f"{what}: an IPv4 address") from None


def _cidr(v, what):
    try:
        return ipaddress.IPv4Network(str(v or "").strip(), strict=False)
    except ValueError:
        raise ValueError(f"{what}: an IPv4 network like 10.0.0.0/24") from None


def _port(v, what):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: a port from 1 to 65535") from None
    if not 1 <= n <= 65535:
        raise ValueError(f"{what}: a port from 1 to 65535")
    return n


# ---------------------------------------------------------------------------
# Underlay : réseau fournisseur, VLAN, réseau externe
# ---------------------------------------------------------------------------

def provider_network(spec, taken=()):
    """Un réseau fournisseur : kube-ovn reprend la carte dans un pont OVS
    `br-<nom>` sur chaque nœud, AVEC ses adresses et ses routes. Aucune garde
    côté kube-ovn : on refuse ici une carte déjà prise (esclave d'un bond de
    Harvester, gestion comprise), `taken` = {carte: [nœuds]}."""
    name = check_name(spec.get("name"), "provider network name", 12)
    if name == "int":
        raise ValueError("provider network name: int is reserved by kube-ovn")
    iface = str(spec.get("interface") or "").strip()
    if not IFACE_RE.match(iface):
        raise ValueError("default interface: the name of a NIC present on the hosts")
    if iface in (taken or {}):
        raise ValueError(f"{iface} is already bonded on {', '.join(taken[iface])}: kube-ovn would take it "
                         "with its addresses and routes")
    body = {"defaultInterface": iface}
    custom = []
    for c in spec.get("custom") or []:
        ci = str(c.get("interface") or "").strip()
        nodes = [str(n) for n in c.get("nodes") or [] if str(n)]
        if not IFACE_RE.match(ci) or not nodes:
            raise ValueError("custom interface: a NIC name and the hosts where it is used")
        if ci in (taken or {}):
            raise ValueError(f"{ci} is already bonded on {', '.join(taken[ci])}")
        custom.append({"interface": ci, "nodes": nodes})
    if custom:
        body["customInterfaces"] = custom
    excl = [str(n) for n in spec.get("exclude_nodes") or [] if str(n)]
    if excl:
        body["excludeNodes"] = excl
    return {"apiVersion": "kubeovn.io/v1", "kind": "ProviderNetwork",
            "metadata": {"name": name, "labels": {on.MANAGED: "true"}}, "spec": body}


def taken_nics(link_status):
    """{carte: [nœuds]} des cartes déjà esclaves d'un bond (LinkMonitor nic)."""
    out = {}
    for node, links in (link_status or {}).items():
        for l in links or []:
            if l.get("masterIndex") not in (None, 0):
                out.setdefault(l.get("name"), []).append(node)
    return out


def vlan(spec):
    name = check_name(spec.get("name"), "VLAN name")
    try:
        vid = int(spec.get("id"))
    except (TypeError, ValueError):
        raise ValueError("VLAN ID: 0 (untagged) to 4094") from None
    if not 0 <= vid <= 4094:
        raise ValueError("VLAN ID: 0 (untagged) to 4094")
    provider = check_name(spec.get("provider"), "provider network", 12)
    return {"apiVersion": "kubeovn.io/v1", "kind": "Vlan",
            "metadata": {"name": name, "labels": {on.MANAGED: "true"}}, "spec": {"id": vid, "provider": provider}}


def exclude_outside(cidr, keep):
    """Les plages `excludeIps` qui ne laissent libres que les adresses de
    `keep` (liste d'IP) dans `cidr` : réserver quelques adresses d'un LAN
    existant pour la passerelle NAT, sans que kube-ovn en tire d'autres."""
    net = ipaddress.IPv4Network(cidr, strict=False)
    hosts_lo, hosts_hi = net.network_address + 1, net.broadcast_address - 1
    keep = sorted(set(ipaddress.IPv4Address(k) for k in keep))
    out, cur = [], hosts_lo
    for k in keep:
        if k < cur:
            continue
        if k > cur:
            out.append(f"{cur}..{k - 1}" if k - 1 > cur else f"{cur}")
        cur = k + 1
    if cur <= hosts_hi:
        out.append(f"{cur}..{hosts_hi}" if hosts_hi > cur else f"{cur}")
    return out


def external_network(spec):
    """Le réseau externe d'une passerelle NAT, comme la doc de Harvester 1.9
    le construit : une NAD kube-ovn en kube-system et un subnet underlay sur
    un VLAN, au vrai préfixe du LAN. Seule la première adresse de la plage
    libre reste attribuable au hasard (l'interface externe de la passerelle) ;
    les IP externes prennent les suivantes, données explicitement."""
    name = check_name(spec.get("name"), "external network name")
    vlan_name = check_name(spec.get("vlan"), "VLAN")
    net = _cidr(spec.get("cidr"), "LAN network")
    gw = _ip(spec.get("gateway"), "LAN gateway")
    if gw not in net:
        raise ValueError(f"gateway {gw} is not in {net}")
    lo, hi = _ip(spec.get("free_start"), "first free address"), _ip(spec.get("free_end"), "last free address")
    if not (lo in net and hi in net and lo <= hi):
        raise ValueError(f"free addresses: a range inside {net}, first then last")
    if lo <= gw <= hi:
        raise ValueError("the free range must not contain the LAN gateway")
    if int(hi) - int(lo) < 1:
        raise ValueError("keep at least two free addresses: one for the gateway, one for an external IP")
    ref = f"kube-system/{name}"
    nad = on.overlay_manifest(ref)
    subnet = {"apiVersion": "kubeovn.io/v1", "kind": "Subnet",
              "metadata": {"name": name, "labels": {on.MANAGED: "true"},
                           "annotations": {FREE_RANGE: f"{lo}..{hi}"}},
              "spec": {"vpc": on.DEFAULT_VPC, "protocol": "IPv4", "cidrBlock": str(net), "gateway": str(gw),
                       "provider": on.provider_of("kube-system", name), "vlan": vlan_name,
                       "excludeIps": exclude_outside(str(net), [str(lo)])}}
    return nad, subnet


def free_range(subnet):
    r = (_meta(subnet).get("annotations") or {}).get(FREE_RANGE) or ""
    if ".." not in r:
        return None
    a, b = r.split("..", 1)
    return ipaddress.IPv4Address(a), ipaddress.IPv4Address(b)


def next_eip(subnet, used):
    """La prochaine adresse de la plage réservée pour une IP externe (la
    première reste à l'interface de la passerelle)."""
    rng = free_range(subnet)
    if not rng:
        return None
    used = set(str(u) for u in used or ())
    for i in range(int(rng[0]) + 1, int(rng[1]) + 1):
        ip = str(ipaddress.IPv4Address(i))
        if ip not in used:
            return ip
    return None


# ---------------------------------------------------------------------------
# NAT : passerelle, IP externe, SNAT, DNAT
# ---------------------------------------------------------------------------

def nat_gateway(spec, subnets, external):
    """Une passerelle NAT de VPC. En CNI secondaire (Harvester), la passerelle
    DOIT porter l'annotation du réseau du locataire, sinon son pod n'a pas de
    patte côté VPC. Elle est placée sur les nœuds où le réseau fournisseur de
    son réseau externe est prêt."""
    name = check_name(spec.get("name"), "gateway name", 48)
    vpc_name = check_name(spec.get("vpc"), "VPC")
    sub = next((s for s in subnets or [] if _meta(s).get("name") == spec.get("subnet")), None)
    if not sub:
        raise ValueError(f"no subnet {spec.get('subnet')}")
    s = sub.get("spec") or {}
    if s.get("vpc") != vpc_name:
        raise ValueError(f"subnet {spec.get('subnet')} is not in VPC {vpc_name}")
    net = _cidr(s.get("cidrBlock"), "subnet")
    lan = _ip(spec.get("lan_ip"), "LAN IP")
    if lan not in net:
        raise ValueError(f"lanIP {lan} is not in the range of subnet {spec.get('subnet')}")
    provider = str(s.get("provider") or "")
    parts = provider.split(".")
    if len(parts) != 3 or parts[2] != "ovn":
        raise ValueError("the subnet is not served through an overlay network (its provider)")
    ext = next((x for x in subnets or [] if _meta(x).get("name") == spec.get("external")), None)
    if not ext or not (ext.get("spec") or {}).get("vlan"):
        raise ValueError(f"{spec.get('external')} is not an external (underlay) network")
    pn = (external or {}).get(_meta(ext).get("name"))
    body = {"vpc": vpc_name, "subnet": _meta(sub)["name"], "lanIp": str(lan), "externalSubnets": [_meta(ext)["name"]]}
    if pn:
        body["selector"] = [f"{pn}.provider-network.kubernetes.io/ready: true"]
    ann = {NETWORKS_ANN: f"{parts[1]}/{parts[0]}"}
    if spec.get("add_route", True):
        ann[ROUTE_ADDED] = "true"
    return {"apiVersion": "kubeovn.io/v1", "kind": "VpcNatGateway",
            "metadata": {"name": name, "labels": {on.MANAGED: "true"}, "annotations": ann}, "spec": body}


def default_route(lan_ip):
    return {"cidr": "0.0.0.0/0", "nextHopIP": str(lan_ip), "policy": "policyDst"}


def vpc_with_route(vpc, lan_ip, add=True):
    """La VPC avec (ou sans) la route par défaut vers la passerelle : kube-ovn
    ne l'ajoute jamais, sans elle ni SNAT ni réponse DNAT ne sortent."""
    out = copy.deepcopy(vpc)
    routes = [r for r in ((out.get("spec") or {}).get("staticRoutes") or [])
              if not (r.get("cidr") == "0.0.0.0/0" and r.get("nextHopIP") == str(lan_ip))]
    if add:
        if any(r.get("cidr") == "0.0.0.0/0" for r in routes):
            raise ValueError("the VPC already has a default route: remove it or leave the route unchecked")
        routes.append(default_route(lan_ip))
    out.setdefault("spec", {})["staticRoutes"] = routes
    return out


def eip(spec, subnets):
    name = check_name(spec.get("name"), "external IP name")
    gw = check_name(spec.get("gateway"), "gateway", 48)
    ext = next((x for x in subnets or [] if _meta(x).get("name") == spec.get("external")), None)
    if not ext:
        raise ValueError(f"no external network {spec.get('external')}")
    body = {"natGwDp": gw, "externalSubnet": _meta(ext)["name"]}
    if spec.get("ip"):
        ip = _ip(spec.get("ip"), "external IP")
        if ip not in _cidr((ext.get("spec") or {}).get("cidrBlock"), "external network"):
            raise ValueError(f"the vip {ip} is not in the range of subnet {_meta(ext)['name']}")
        body["v4ip"] = str(ip)
    return {"apiVersion": "kubeovn.io/v1", "kind": "IptablesEIP",
            "metadata": {"name": name, "labels": {on.MANAGED: "true"}}, "spec": body}


def snat(spec):
    name = check_name(spec.get("name"), "SNAT rule name")
    cidr = str(spec.get("internal_cidr") or "").strip()
    if "," in cidr:
        raise ValueError("internal CIDR: one network or one address")
    _cidr(cidr, "internal CIDR")
    return {"apiVersion": "kubeovn.io/v1", "kind": "IptablesSnatRule",
            "metadata": {"name": name, "labels": {on.MANAGED: "true"}},
            "spec": {"eip": check_name(spec.get("eip"), "external IP"), "internalCIDR": cidr}}


def dnat(spec):
    name = check_name(spec.get("name"), "DNAT rule name")
    proto = str(spec.get("protocol") or "tcp").lower()
    if proto not in ("tcp", "udp"):
        raise ValueError("protocol: tcp or udp")
    return {"apiVersion": "kubeovn.io/v1", "kind": "IptablesDnatRule",
            "metadata": {"name": name, "labels": {on.MANAGED: "true"}},
            "spec": {"eip": check_name(spec.get("eip"), "external IP"),
                     "externalPort": str(_port(spec.get("external_port"), "external port")),
                     "internalIp": str(_ip(spec.get("internal_ip"), "internal IP")),
                     "internalPort": str(_port(spec.get("internal_port"), "internal port")), "protocol": proto}}


# ---------------------------------------------------------------------------
# Relevés : états et ordre de suppression
# ---------------------------------------------------------------------------

def _cond(o, kind="Ready"):
    for c in ((o or {}).get("status") or {}).get("conditions") or []:
        if c.get("type") == kind:
            return str(c.get("status")) == "True", c.get("message") or ""
    return None, ""


def ovn_health(deploys, pods, nodes):
    """La santé de kube-ovn, dite avant tout geste : base OVN (ovn-central),
    contrôleur, et les nœuds où le CNI ne tourne pas. Vu en réel : après la
    suppression puis le retour d'un nœud, la base RAFT d'OVN restait sans
    quorum et le CNI du nœud revenu bouclait (« no ovn0 address ») pendant
    des heures, sans rien dire ailleurs."""
    by = {_meta(d).get("name"): d for d in deploys or []}
    out = {"problems": []}
    for name in ("ovn-central", "kube-ovn-controller"):
        d = by.get(name)
        if not d:
            continue
        want = (d.get("spec") or {}).get("replicas") or 0
        ready = (d.get("status") or {}).get("readyReplicas") or 0
        out[name] = {"ready": ready, "want": want}
        if ready < want:
            out["problems"].append(f"{name} {ready}/{want} ready")
    bad = sorted((p.get("spec") or {}).get("nodeName") or "?" for p in pods or []
                 if (_meta(p).get("labels") or {}).get("app") == "kube-ovn-cni"
                 and not all(c.get("ready") for c in (p.get("status") or {}).get("containerStatuses") or [{}]))
    if bad:
        out["problems"].append("kube-ovn CNI not ready on " + ", ".join(bad))
    out["cni_not_ready"] = bad
    joined = [n for n in nodes or [] if not (_meta(n).get("annotations") or {}).get("ovn.kubernetes.io/ip_address")]
    if joined and by.get("kube-ovn-controller"):
        out["problems"].append("no kube-ovn address yet for " + ", ".join(sorted(_meta(n).get("name") for n in joined)))
    out["healthy"] = not out["problems"]
    return out


def provider_rows(pns, vlans):
    out = []
    for p in pns or []:
        st, s = p.get("status") or {}, p.get("spec") or {}
        bad = [f"{c.get('node')}: {c.get('message') or c.get('reason')}" for c in st.get("conditions") or []
               if str(c.get("status")) != "True" and c.get("node")]
        out.append({"name": _meta(p).get("name"), "interface": s.get("defaultInterface"),
                    "custom": s.get("customInterfaces") or [], "exclude": s.get("excludeNodes") or [],
                    "ready": bool(st.get("ready")), "ready_nodes": st.get("readyNodes") or [],
                    "not_ready": st.get("notReadyNodes") or [], "errors": bad,
                    "vlans": sorted(_meta(v).get("name") for v in vlans or [] if (v.get("spec") or {}).get("provider") == _meta(p).get("name")),
                    "managed": (_meta(p).get("labels") or {}).get(on.MANAGED) == "true"})
    return out


def vlan_rows(vlans, subnets):
    out = []
    for v in vlans or []:
        st = v.get("status") or {}
        out.append({"name": _meta(v).get("name"), "id": (v.get("spec") or {}).get("id", 0),
                    "provider": (v.get("spec") or {}).get("provider"), "conflict": bool(st.get("conflict")),
                    "subnets": sorted(_meta(s).get("name") for s in subnets or [] if (s.get("spec") or {}).get("vlan") == _meta(v).get("name"))})
    return out


def external_rows(subnets, vlans, eips):
    vl = {_meta(v).get("name"): (v.get("spec") or {}) for v in vlans or []}
    out = []
    for s in subnets or []:
        sp, st = s.get("spec") or {}, s.get("status") or {}
        if not sp.get("vlan"):
            continue
        rng = free_range(s)
        ok, msg = _cond(s)
        out.append({"name": _meta(s).get("name"), "cidr": sp.get("cidrBlock"), "gateway": sp.get("gateway"),
                    "vlan": sp.get("vlan"), "provider": (vl.get(sp.get("vlan")) or {}).get("provider"),
                    "free": f"{rng[0]}..{rng[1]}" if rng else "", "available": st.get("v4availableIPs"),
                    "using": st.get("v4usingIPs"), "ready": ok, "message": msg,
                    "next_eip": next_eip(s, [(e.get("status") or {}).get("ip") or (e.get("spec") or {}).get("v4ip")
                                             for e in eips or []]),
                    "managed": (_meta(s).get("labels") or {}).get(on.MANAGED) == "true"})
    return out


DEFAULT_NET_ANN = "v1.multus-cni.io/default-network"


def gateway_pod_broken(gw, pod):
    """kube-ovn avant 1.16.1, en CNI secondaire (Harvester 1.8), remplace le
    réseau des pods (eth0) de la passerelle par le réseau du locataire : deux
    interfaces à la même adresse et la même MAC, et plus rien ne revient
    (issue kube-ovn #6632, vu en réel sur harvlab). Rend True si eth0 est
    sur le réseau du locataire."""
    try:
        status = json.loads((_meta(pod).get("annotations") or {}).get("k8s.v1.cni.cncf.io/network-status") or "[]")
    except ValueError:
        return False
    tenant = (_meta(gw).get("annotations") or {}).get(NETWORKS_ANN, "")
    eth0 = next((x for x in status if x.get("interface") == "eth0"), None)
    return bool(eth0 and tenant and eth0.get("name") == tenant)


def gateway_fix_patch(sts):
    """Le correctif : retirer du modèle de pod l'annotation qui remplace le
    réseau des pods. kube-ovn régénère le StatefulSet quand le conteneur de
    la passerelle redémarre : le correctif est à refaire alors (réparer)."""
    ann = (((sts or {}).get("spec") or {}).get("template") or {}).get("metadata", {}).get("annotations") or {}
    if DEFAULT_NET_ANN not in ann:
        return None
    return [{"op": "remove", "path": "/spec/template/metadata/annotations/" + DEFAULT_NET_ANN.replace("~", "~0").replace("/", "~1")}]


def gateway_rows(gws, pods, sts, eips):
    pod_by = {(_meta(p).get("labels") or {}).get("app"): p for p in pods or []}
    sts_by = {_meta(s).get("name"): s for s in sts or []}
    out = []
    for g in gws or []:
        n = _meta(g).get("name")
        pod = pod_by.get(f"vpc-nat-gw-{n}") or {}
        ss = sts_by.get(f"vpc-nat-gw-{n}") or {}
        ready = bool(((ss.get("status") or {}).get("readyReplicas") or 0) >= 1
                     and (_meta(pod).get("annotations") or {}).get(GW_INIT) == "true")
        try:
            status = json.loads((_meta(pod).get("annotations") or {}).get("k8s.v1.cni.cncf.io/network-status") or "[]")
        except ValueError:
            status = []
        s = g.get("spec") or {}
        broken = gateway_pod_broken(g, pod)
        out.append({"name": n, "vpc": s.get("vpc"), "subnet": s.get("subnet"), "lan_ip": s.get("lanIp"),
                    "external": (s.get("externalSubnets") or [""])[0], "ready": ready and not broken, "broken": broken,
                    "pod": _meta(pod).get("name"), "node": (pod.get("spec") or {}).get("nodeName"),
                    "phase": (pod.get("status") or {}).get("phase"),
                    "interfaces": [{"name": x.get("interface"), "network": x.get("name"), "ips": x.get("ips") or []} for x in status],
                    "route": (_meta(g).get("annotations") or {}).get(ROUTE_ADDED) == "true",
                    "eips": sorted(_meta(e).get("name") for e in eips or [] if (e.get("spec") or {}).get("natGwDp") == n)})
    return out


def eip_rows(eips, snats, dnats):
    out = []
    for e in eips or []:
        st, s, n = e.get("status") or {}, e.get("spec") or {}, _meta(e).get("name")
        out.append({"name": n, "gateway": s.get("natGwDp"), "external": s.get("externalSubnet"),
                    "ip": st.get("ip") or s.get("v4ip"), "ready": bool(st.get("ready")), "nat": st.get("nat") or "",
                    "rules": sorted([_meta(r).get("name") for r in (snats or []) + (dnats or []) if (r.get("spec") or {}).get("eip") == n])})
    return out


def rule_rows(rules, kind):
    out = []
    for r in rules or []:
        st, s = r.get("status") or {}, r.get("spec") or {}
        row = {"name": _meta(r).get("name"), "kind": kind, "eip": s.get("eip"), "ready": bool(st.get("ready")),
               "ip": st.get("v4ip") or ""}
        if kind == "snat":
            row["internal_cidr"] = s.get("internalCIDR")
        else:
            row.update(protocol=s.get("protocol"), external_port=s.get("externalPort"),
                       internal_ip=s.get("internalIp"), internal_port=s.get("internalPort"))
        out.append(row)
    return out


def delete_check(kind, name, state):
    """L'ordre imposé par kube-ovn (et, pour l'underlay qu'il ne garde pas,
    par la console) : règles, IP externe, passerelle ; subnets, VLAN,
    réseau fournisseur. `state` = les lignes relevées."""
    if kind == "gateway":
        g = next((x for x in state.get("gateways", []) if x["name"] == name), None)
        if g and g["eips"]:
            raise ValueError("delete its external IPs first: " + ", ".join(g["eips"]))
    if kind == "eip":
        e = next((x for x in state.get("eips", []) if x["name"] == name), None)
        if e and e["rules"]:
            raise ValueError("delete its rules first: " + ", ".join(e["rules"]))
    if kind == "external":
        x = next((x for x in state.get("externals", []) if x["name"] == name), None)
        if x and (x.get("using") or 0) > 0:
            raise ValueError("gateways or external IPs still use addresses of this network")
    if kind == "vlan":
        v = next((x for x in state.get("vlans", []) if x["name"] == name), None)
        if v and v["subnets"]:
            raise ValueError("subnets still use this VLAN: " + ", ".join(v["subnets"]))
    if kind == "provider":
        p = next((x for x in state.get("providers", []) if x["name"] == name), None)
        if p and p["vlans"]:
            raise ValueError("delete its VLANs first: " + ", ".join(p["vlans"]))


# ---------------------------------------------------------------------------
# Politiques réseau visant des VMs
# ---------------------------------------------------------------------------

def _vm_selector(vms):
    vms = [str(v) for v in vms or [] if str(v)]
    for v in vms:
        check_name(v, "VM name")
    return {"matchExpressions": [{"key": VM_LABEL, "operator": "In", "values": sorted(vms)}]} if vms else {}


def _peer(p):
    kind = p.get("kind")
    if kind == "cidr":
        net = _cidr(p.get("cidr"), "peer network")
        block = {"cidr": str(net)}
        exc = [str(_cidr(e, "excepted network")) for e in p.get("except") or [] if str(e).strip()]
        if exc:
            block["except"] = exc
        return {"ipBlock": block}
    if kind == "namespace":
        return {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": check_name(p.get("namespace"), "namespace")}}}
    if kind == "vms":
        sel = _vm_selector(p.get("vms"))
        if not sel:
            raise ValueError("peer VMs: at least one VM")
        out = {"podSelector": sel}
        if p.get("namespace"):
            out["namespaceSelector"] = {"matchLabels": {"kubernetes.io/metadata.name": check_name(p["namespace"], "namespace")}}
        return out
    raise ValueError("peer: a network (CIDR), a namespace or VMs")


def _rules(rules, direction):
    out = []
    for r in rules or []:
        rule = {}
        peers = [_peer(p) for p in r.get("peers") or []]
        if peers:
            rule["from" if direction == "ingress" else "to"] = peers
        ports = []
        for p in r.get("ports") or []:
            proto = str(p.get("protocol") or "TCP").upper()
            if proto not in ("TCP", "UDP"):
                raise ValueError("port protocol: TCP or UDP")
            ports.append({"port": _port(p.get("port"), "port"), "protocol": proto})
        if ports:
            rule["ports"] = ports
        out.append(rule)
    return out


def network_policy(spec):
    """Une NetworkPolicy qui vise des VMs par leur nom (label
    vm.kubevirt.io/name de leur virt-launcher). kube-ovn l'applique à toutes
    leurs interfaces kube-ovn (overlay et underlay), jamais à une carte sur
    un pont VLAN de Harvester. Une direction cochée sans règle refuse tout.
    `lax` ne filtre que TCP/UDP/SCTP et laisse passer le DHCP d'OVN."""
    name = check_name(spec.get("name"), "policy name")
    ns = check_name(spec.get("namespace") or "default", "namespace")
    types, body = [], {"podSelector": _vm_selector(spec.get("vms"))}
    for direction in ("ingress", "egress"):
        if spec.get(direction) is not None:
            types.append(direction.capitalize())
            body[direction] = _rules(spec[direction], direction)
    if not types:
        raise ValueError("choose incoming traffic, outgoing traffic, or both")
    body["policyTypes"] = types
    meta = {"name": name, "namespace": ns, "labels": {on.MANAGED: "true"}}
    if spec.get("lax", True):
        meta["annotations"] = {ENFORCEMENT: "lax"}
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": meta, "spec": body}


def _peer_text(p):
    if "ipBlock" in p:
        b = p["ipBlock"]
        return b.get("cidr", "") + (f" except {', '.join(b['except'])}" if b.get("except") else "")
    parts = []
    if "namespaceSelector" in p:
        lab = (p["namespaceSelector"].get("matchLabels") or {}).get("kubernetes.io/metadata.name")
        parts.append(f"namespace {lab}" if lab else "namespaces")
    if "podSelector" in p:
        vals = [v for e in p["podSelector"].get("matchExpressions") or [] if e.get("key") == VM_LABEL for v in e.get("values") or []]
        parts.append("VMs " + ", ".join(vals) if vals else "pods")
    return " / ".join(parts)


def policy_to_spec(np):
    """Une NetworkPolicy relue dans la forme du formulaire (ou None si elle
    utilise ce que le formulaire ne sait pas dire : on la modifie en YAML)."""
    s = np.get("spec") or {}
    vms = [v for e in (s.get("podSelector") or {}).get("matchExpressions") or [] if e.get("key") == VM_LABEL
           for v in e.get("values") or []]
    if (s.get("podSelector") or {}).get("matchLabels"):
        return None
    out = {"name": _meta(np).get("name"), "namespace": _meta(np).get("namespace"), "vms": vms,
           "lax": (_meta(np).get("annotations") or {}).get(ENFORCEMENT) == "lax"}
    for direction, key in (("ingress", "from"), ("egress", "to")):
        if direction.capitalize() not in (s.get("policyTypes") or []):
            continue
        rules = []
        for r in s.get(direction) or []:
            peers = []
            for p in r.get(key) or []:
                if "ipBlock" in p:
                    peers.append({"kind": "cidr", "cidr": p["ipBlock"].get("cidr"), "except": p["ipBlock"].get("except") or []})
                elif "podSelector" in p:
                    vals = [v for e in p["podSelector"].get("matchExpressions") or [] if e.get("key") == VM_LABEL
                            for v in e.get("values") or []]
                    if not vals:
                        return None
                    ns = ((p.get("namespaceSelector") or {}).get("matchLabels") or {}).get("kubernetes.io/metadata.name", "")
                    peers.append({"kind": "vms", "vms": vals, "namespace": ns})
                elif "namespaceSelector" in p:
                    ns = (p["namespaceSelector"].get("matchLabels") or {}).get("kubernetes.io/metadata.name")
                    if not ns:
                        return None
                    peers.append({"kind": "namespace", "namespace": ns})
            rules.append({"peers": peers, "ports": [{"port": p.get("port"), "protocol": p.get("protocol", "TCP")}
                                                    for p in r.get("ports") or []]})
        out[direction] = rules
    return out


def policy_rows(nps):
    out = []
    for np in nps or []:
        s = np.get("spec") or {}
        vms = [v for e in (s.get("podSelector") or {}).get("matchExpressions") or [] if e.get("key") == VM_LABEL
               for v in e.get("values") or []]
        target = ("VMs " + ", ".join(vms)) if vms else ("all" if not (s.get("podSelector") or {}).get("matchLabels") else "pods")
        rules = {}
        for direction, key in (("ingress", "from"), ("egress", "to")):
            if direction.capitalize() in (s.get("policyTypes") or []):
                rules[direction] = [{"peers": [_peer_text(p) for p in r.get(key) or []] or ["any"],
                                     "ports": [f"{p.get('protocol', 'TCP')} {p.get('port', '*')}" for p in r.get("ports") or []] or ["all ports"]}
                                    for r in s.get(direction) or []]
        out.append({"namespace": _meta(np).get("namespace"), "name": _meta(np).get("name"), "target": target, "vms": vms,
                    "types": s.get("policyTypes") or [], "rules": rules,
                    "lax": (_meta(np).get("annotations") or {}).get(ENFORCEMENT) == "lax",
                    "editable": policy_to_spec(np) is not None, "created": _meta(np).get("creationTimestamp")})
    return out
