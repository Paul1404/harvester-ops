"""harvester-ops : les périphériques de Harvester (Advanced > PCI Devices,
USB Devices, SR-IOV Network Devices), v1.68.0.

Relevé dans harvester-ui-extension et harvester/pcidevices v1.9.0 (scratchpad
du chantier : parity/harvester-settings-devices-1.9.md, section 5) :

- un PCIDevice est découvert par l'agent de chaque nœud ; « activer le
  passthrough » crée un PCIDeviceClaim de MÊME nom, avec une ownerReference
  vers le PCIDevice (le contrôleur le cherche par là, sans elle il boucle) ;
  l'agent détache alors le périphérique de son pilote et le lie à vfio-pci,
  tout son groupe IOMMU avec lui ; appliqué = status.passthroughEnabled ;
- un USBDevice de même : USBDeviceClaim de même nom, ownerReference, spec
  {userName} seul ; appliqué = USBDevice.status.enabled ;
- un SriovNetworkDevice (une carte SR-IOV libre d'un nœud) : spec.numVFs,
  0 pour désactiver ; changer N passe par 0 ; chaque fonction virtuelle
  devient un PCIDevice à passer comme les autres.

Fonctions pures ; bin/harvester-resources.py les applique.
"""

import json
import re

GROUP = "devices.harvesterhci.io"
API = f"{GROUP}/v1beta1"
K_PCI = f"pcidevices.{GROUP}"
K_PCICLAIM = f"pcideviceclaims.{GROUP}"
K_USB = f"usbdevices.{GROUP}"
K_USBCLAIM = f"usbdeviceclaims.{GROUP}"
K_SRIOV = f"sriovnetworkdevices.{GROUP}"
ADDON = ("harvester-system", "pcidevices-controller")
ALLOC_ANN = "harvesterhci.io/deviceAllocationDetails"
_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
MAX_VFS = 256


def _meta(o):
    return (o or {}).get("metadata") or {}


def check_name(name, what="device"):
    name = str(name or "").strip()
    if not name or len(name) > 253 or not _NAME_RE.match(name):
        raise ValueError(f"{what}: a lowercase name (letters, digits, dashes, dots)")
    return name


def addon_enabled(addons):
    """L'add-on pcidevices-controller porte tout : sans lui, rien n'est
    découvert et les pages renvoient vers les add-ons."""
    for a in addons or []:
        m = _meta(a)
        if (m.get("namespace"), m.get("name")) == ADDON:
            return bool((a.get("spec") or {}).get("enabled"))
    return False


# ---------------------------------------------------------------------------
# Qui se sert d'un périphérique
# ---------------------------------------------------------------------------

def _vm_devices(vm):
    tpl = (((vm.get("spec") or {}).get("template") or {}).get("spec") or {}).get("domain") or {}
    devs = tpl.get("devices") or {}
    return list(devs.get("hostDevices") or []) + list(devs.get("gpus") or [])


def _alloc(vm):
    raw = (_meta(vm).get("annotations") or {}).get(ALLOC_ANN)
    if not raw:
        return {}
    try:
        return json.loads(raw) or {}
    except ValueError:
        return {}


def running_set(vmis):
    return {f"{_meta(v).get('namespace')}/{_meta(v).get('name')}" for v in vmis or []}


def users(vms, running=None):
    """{nom de périphérique ou de ressource: [ns/vm]} : par le spec de la VM
    (nom et deviceName) et, pour une VM en marche, par l'annotation
    d'allocation que Harvester pose au démarrage (les périphériques réellement
    pris). Pour une VM arrêtée, seul le spec compte, comme en 1.9 : la 1.8
    laissait l'annotation d'une carte retirée (vu sur harvlab). `running`
    à None : l'annotation compte toujours."""
    out = {}

    def add(key, who):
        if key:
            out.setdefault(key, [])
            if who not in out[key]:
                out[key].append(who)
    for vm in vms or []:
        m = _meta(vm)
        who = f"{m.get('namespace')}/{m.get('name')}"
        for d in _vm_devices(vm):
            add(d.get("name"), who)
            add(d.get("deviceName"), who)
        if running is not None and who not in running:
            continue
        alloc = _alloc(vm)
        for names in list((alloc.get("hostdevices") or alloc.get("hostDevices") or {}).values()) + \
                list((alloc.get("gpus") or {}).values()):
            for n in names or []:
                add(n, who)
    return out


def stale_allocations(name, vms, running):
    """[(ns, vm, patch)] des VMs arrêtées dont l'annotation d'allocation cite
    encore ce périphérique alors que leur spec ne le cite plus : Harvester 1.8
    refuse alors de le rendre (« already in use with vm »). Le patch refait
    l'annotation depuis le spec, comme le fait Harvester 1.9."""
    out = []
    for vm in vms or []:
        m = _meta(vm)
        if f"{m.get('namespace')}/{m.get('name')}" in (running or set()):
            continue
        alloc = _alloc(vm)
        listed = [n for names in (alloc.get("hostdevices") or {}).values() for n in names or []]
        host = [d for d in (((vm.get("spec") or {}).get("template") or {}).get("spec") or {}).get("domain", {})
                .get("devices", {}).get("hostDevices") or []]
        if name not in listed or name in {d.get("name") for d in host}:
            continue
        rebuilt = {}
        for d in host:
            rebuilt.setdefault(d.get("deviceName"), []).append(d.get("name"))
        value = json.dumps({"hostdevices": rebuilt}, separators=(",", ":")) if rebuilt else None
        out.append((m.get("namespace"), m.get("name"), {"metadata": {"annotations": {ALLOC_ANN: value}}}))
    return out


# ---------------------------------------------------------------------------
# PCI
# ---------------------------------------------------------------------------

def _vf_parents(sriovs):
    """{nom de PCIDevice d'une fonction virtuelle: carte SR-IOV}."""
    out = {}
    for s in sriovs or []:
        for vf in ((s.get("status") or {}).get("vfPCIDevices") or []):
            out[vf] = _meta(s).get("name")
    return out


def pci_rows(devices, claims, vms, sriovs=(), running=None):
    by_claim = {_meta(c).get("name"): c for c in claims or []}
    used = users(vms, running)
    parents = _vf_parents(sriovs)
    groups = {}
    for d in devices or []:
        st = d.get("status") or {}
        if st.get("iommuGroup") not in (None, ""):
            groups.setdefault((st.get("nodeName"), str(st.get("iommuGroup"))), []).append(_meta(d).get("name"))
    out = []
    for d in devices or []:
        name = _meta(d).get("name")
        st = d.get("status") or {}
        claim = by_claim.get(name)
        cst = (claim or {}).get("status") or {}
        group = st.get("iommuGroup")
        siblings = [n for n in groups.get((st.get("nodeName"), str(group)), []) if n != name] if group not in (None, "") else []
        state = "disabled"
        if claim is not None:
            state = "enabled" if cst.get("passthroughEnabled") else "pending"
        out.append({
            "name": name, "address": st.get("address") or "", "node": st.get("nodeName") or "",
            "vendor_id": st.get("vendorId") or "", "device_id": st.get("deviceId") or "",
            "class_id": st.get("classId") or "", "description": st.get("description") or "",
            "driver": st.get("kernelDriverInUse") or "",
            "original_driver": (_meta(d).get("annotations") or {}).get("harvesterhci.io/pcideviceDriver") or "",
            "resource": st.get("resourceName") or "", "iommu_group": "" if group in (None, "") else str(group),
            "siblings": siblings, "state": state, "claimed_by": ((claim or {}).get("spec") or {}).get("userName") or "",
            "used_by": sorted(set(used.get(name, []))), "vf_of": parents.get(name, ""),
            "can_enable": claim is None and group not in (None, ""),
        })
    return sorted(out, key=lambda r: (r["node"], r["address"]))


def pci_claim(device, user):
    """Le PCIDeviceClaim d'« Enable Passthrough » : même nom que le
    PCIDevice, ownerReference obligatoire."""
    m, st = _meta(device), device.get("status") or {}
    if st.get("iommuGroup") in (None, ""):
        raise ValueError(f"{m.get('name')}: no IOMMU group, passthrough is impossible (IOMMU off in the BIOS or the kernel)")
    return {"apiVersion": API, "kind": "PCIDeviceClaim",
            "metadata": {"name": m["name"], "ownerReferences": [
                {"apiVersion": API, "kind": "PCIDevice", "name": m["name"], "uid": m.get("uid")}]},
            # disableResourcePooling (1.9) n'est pas écrit : false est sa valeur
            # par défaut, et le CRD de la 1.8 refuse ce champ inconnu
            "spec": {"address": st.get("address"), "nodeName": st.get("nodeName"), "userName": user or "admin"}}


def pci_disable_check(name, vms, running=None):
    who = users(vms, running).get(name)
    if who:
        raise ValueError(f"{name} is used by {', '.join(sorted(set(who)))}: remove it from the VM first")


def pci_settled(claim, enable):
    if enable:
        if claim is None:
            return False, "the claim disappeared"
        return (True, "passthrough enabled") if (claim.get("status") or {}).get("passthroughEnabled") else (None, "binding to vfio-pci")
    return (True, "passthrough disabled, the host driver is back") if claim is None else (None, "giving the device back to its driver")


# ---------------------------------------------------------------------------
# USB
# ---------------------------------------------------------------------------

def usb_rows(devices, claims, vms, running=None):
    by_claim = {_meta(c).get("name"): c for c in claims or []}
    used = users(vms, running)
    out = []
    for d in devices or []:
        name = _meta(d).get("name")
        st = d.get("status") or {}
        claim = by_claim.get(name)
        state = "disabled"
        if claim is not None:
            state = "enabled" if st.get("enabled") else "pending"
        res = st.get("resourceName") or ""
        out.append({"name": name, "node": st.get("nodeName") or "", "vendor_id": st.get("vendorID") or "",
                    "product_id": st.get("productID") or "", "description": st.get("description") or "",
                    "path": st.get("devicePath") or "", "pci_address": st.get("pciAddress") or "",
                    "resource": res, "state": state, "message": st.get("message") or "",
                    "claimed_by": ((claim or {}).get("spec") or {}).get("userName") or "",
                    "used_by": sorted(set(used.get(name, []) + used.get(res, []))),
                    "can_enable": claim is None})
    return sorted(out, key=lambda r: (r["node"], r["name"]))


def usb_claim(device, user):
    m = _meta(device)
    return {"apiVersion": API, "kind": "USBDeviceClaim",
            "metadata": {"name": m["name"], "ownerReferences": [
                {"apiVersion": API, "kind": "USBDevice", "name": m["name"], "uid": m.get("uid")}]},
            "spec": {"userName": user or "admin"}}


def usb_disable_check(device, vms, running=None):
    name = _meta(device).get("name")
    res = (device.get("status") or {}).get("resourceName")
    u = users(vms, running)
    who = u.get(name, []) + (u.get(res, []) if res else [])
    if who:
        raise ValueError(f"{name} is used by {', '.join(sorted(set(who)))}: remove it from the VM first")


def usb_settled(device, claim, enable):
    st = (device or {}).get("status") or {}
    if enable:
        return (True, "passthrough enabled") if st.get("enabled") else (None, "handing the device to KubeVirt")
    if claim is None and not st.get("enabled"):
        return True, "passthrough disabled"
    return None, "giving the device back to the host"


# ---------------------------------------------------------------------------
# SR-IOV réseau
# ---------------------------------------------------------------------------

def sriov_rows(devices, pci_claims):
    claimed = {_meta(c).get("name") for c in pci_claims or []}
    out = []
    for d in devices or []:
        sp, st = d.get("spec") or {}, d.get("status") or {}
        vfs = list(st.get("vfPCIDevices") or [])
        out.append({"name": _meta(d).get("name"), "node": sp.get("nodeName") or "", "address": sp.get("address") or "",
                    "interface": (_meta(d).get("name") or "").split("-")[-1], "num_vfs": int(sp.get("numVFs") or 0),
                    "enabled": st.get("status") == "sriovNetworkDeviceEnabled", "status": st.get("status") or "",
                    "vf_addresses": list(st.get("vfAddresses") or []), "vf_devices": vfs,
                    "vfs_claimed": sorted(v for v in vfs if v in claimed)})
    return sorted(out, key=lambda r: (r["node"], r["name"]))


def sriov_patch(device, num_vfs, pci_claims):
    """{"spec": {"numVFs": N}} ; 0 désactive. Changer N passe par 0 (comme
    l'interface de Harvester) ; désactiver est refusé tant qu'une fonction
    virtuelle est réservée."""
    try:
        n = int(num_vfs)
    except (TypeError, ValueError):
        raise ValueError("number of virtual functions: a whole number") from None
    if n < 0 or n > MAX_VFS:
        raise ValueError(f"number of virtual functions: 0 to {MAX_VFS}")
    cur = int(((device.get("spec") or {}).get("numVFs")) or 0)
    if n and cur and n != cur:
        raise ValueError(f"{_meta(device).get('name')} already has {cur} virtual functions: disable it first, then enable it with {n}")
    if n == 0:
        vfs = set(((device.get("status") or {}).get("vfPCIDevices")) or [])
        taken = sorted(vfs & {_meta(c).get("name") for c in pci_claims or []})
        if taken:
            raise ValueError(f"virtual functions still in passthrough: {', '.join(taken)}; disable them first")
    return {"spec": {"numVFs": n}}


def sriov_settled(device, n):
    st = (device or {}).get("status") or {}
    if n == 0:
        return (True, "SR-IOV disabled") if st.get("status") != "sriovNetworkDeviceEnabled" and not st.get("vfAddresses") \
            else (None, "removing the virtual functions")
    got = len(st.get("vfAddresses") or [])
    if st.get("status") == "sriovNetworkDeviceEnabled" and got >= n:
        return True, f"{got} virtual functions ready"
    return None, f"{got}/{n} virtual functions"
