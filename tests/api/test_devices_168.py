"""v1.68.0 : les périphériques de Harvester en fonctions pures (formes et
refus de harvester/pcidevices v1.9.0) : PCI, USB, SR-IOV réseau."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_devices as hd  # noqa: E402


def pci(name, addr, node="n3", group="7", driver="e1000e"):
    return {"metadata": {"name": name, "uid": "u-" + name, "annotations": {"harvesterhci.io/pcideviceDriver": driver}},
            "status": {"address": addr, "nodeName": node, "iommuGroup": group, "kernelDriverInUse": driver,
                       "resourceName": "intel.com/82574L", "vendorId": "8086", "deviceId": "10d3",
                       "description": "Ethernet controller: Intel 82574L"}}


def vm(name, host=(), alloc=None):
    ann = {hd.ALLOC_ANN: json.dumps(alloc)} if alloc else {}
    return {"metadata": {"namespace": "default", "name": name, "annotations": ann},
            "spec": {"template": {"spec": {"domain": {"devices": {"hostDevices": list(host)}}}}}}


def test_the_addon_carries_everything():
    assert hd.addon_enabled([{"metadata": {"namespace": "harvester-system", "name": "pcidevices-controller"},
                              "spec": {"enabled": True}}])
    assert not hd.addon_enabled([])


def test_pci_rows_say_state_group_and_users():
    devs = [pci("n3-000004000", "0000:04:00.0"), pci("n3-000004001", "0000:04:00.1"),
            pci("n3-000005010", "0000:05:01.0", group=""), pci("n1-000004000", "0000:04:00.0", node="n1")]
    claims = [{"metadata": {"name": "n3-000004000"}, "spec": {"userName": "ju"}, "status": {"passthroughEnabled": True}},
              {"metadata": {"name": "n3-000004001"}, "spec": {}, "status": {}}]
    sriov = [{"metadata": {"name": "n3-enp5s0"}, "status": {"vfPCIDevices": ["n3-000005010"]}}]
    vms = [vm("gw", alloc={"hostdevices": {"intel.com/82574L": ["n3-000004000"]}})]
    rows = {r["name"]: r for r in hd.pci_rows(devs, claims, vms, sriov, running={"default/gw"})}
    a = rows["n3-000004000"]
    assert a["state"] == "enabled" and a["claimed_by"] == "ju" and a["used_by"] == ["default/gw"]
    assert a["siblings"] == ["n3-000004001"]                      # le groupe IOMMU part avec lui
    assert rows["n3-000004001"]["state"] == "pending"
    assert rows["n3-000005010"]["vf_of"] == "n3-enp5s0" and not rows["n3-000005010"]["can_enable"]
    assert rows["n1-000004000"]["siblings"] == [] and rows["n1-000004000"]["can_enable"]


def test_a_stale_allocation_left_by_harvester_18_is_not_a_use():
    """Vu sur harvlab (pcidevices v1.8.2) : la carte retirée d'une VM arrêtée,
    l'annotation d'allocation la cite encore et Harvester refuse de rendre la
    carte (« already in use with vm »). La 1.9 recalcule l'annotation depuis
    le spec ; la console fait de même avant de désactiver."""
    stale = vm("dev168", alloc={"hostdevices": {"intel.com/82574L": ["n3-000005000"]}})
    keep = vm("gw", host=[{"name": "n3-000005000", "deviceName": "intel.com/82574L"}],
              alloc={"hostdevices": {"intel.com/82574L": ["n3-000005000"]}})
    assert hd.users([stale], running=set()) == {}
    assert hd.users([stale], running={"default/dev168"}) == {"n3-000005000": ["default/dev168"]}
    hd.pci_disable_check("n3-000005000", [stale], running=set())
    assert hd.stale_allocations("n3-000005000", [stale, keep], running=set()) == [
        ("default", "dev168", {"metadata": {"annotations": {hd.ALLOC_ANN: None}}})]
    other = vm("db", host=[{"name": "n3-000009000", "deviceName": "x.com/Y"}],
               alloc={"hostdevices": {"intel.com/82574L": ["n3-000005000"], "x.com/Y": ["n3-000009000"]}})
    [(_, _, patch)] = hd.stale_allocations("n3-000005000", [other], running=set())
    assert json.loads(patch["metadata"]["annotations"][hd.ALLOC_ANN]) == {"hostdevices": {"x.com/Y": ["n3-000009000"]}}
    assert hd.stale_allocations("n3-000005000", [stale], running={"default/dev168"}) == []


def test_a_pci_claim_is_owned_by_its_device():
    c = hd.pci_claim(pci("n3-000004000", "0000:04:00.0"), "ju")
    assert c["metadata"] == {"name": "n3-000004000", "ownerReferences": [
        {"apiVersion": hd.API, "kind": "PCIDevice", "name": "n3-000004000", "uid": "u-n3-000004000"}]}
    # sans disableResourcePooling : champ de la 1.9 que le CRD de la 1.8 refuse
    # (« strict decoding error: unknown field », vu sur harvlab) ; false est
    # de toute façon sa valeur par défaut
    assert c["spec"] == {"address": "0000:04:00.0", "nodeName": "n3", "userName": "ju"}
    with pytest.raises(ValueError, match="IOMMU"):
        hd.pci_claim(pci("x", "0000:00:01.0", group=""), "ju")


def test_a_device_in_use_is_not_given_back():
    vms = [vm("gw", host=[{"name": "n3-000004000", "deviceName": "intel.com/82574L"}])]
    with pytest.raises(ValueError, match="default/gw"):
        hd.pci_disable_check("n3-000004000", vms)
    hd.pci_disable_check("n3-000004001", vms)
    assert hd.pci_settled({"status": {"passthroughEnabled": True}}, True) == (True, "passthrough enabled")
    assert hd.pci_settled({"status": {}}, True)[0] is None
    assert hd.pci_settled(None, False)[0] is True


def test_usb_rows_claims_and_checks():
    dev = {"metadata": {"name": "n1-0627-0001-002002", "uid": "u1"},
           "status": {"nodeName": "n1", "vendorID": "0627", "productID": "0001", "description": "QEMU Tablet",
                      "resourceName": "kubevirt.io/n1-0627-0001-002002", "devicePath": "/dev/bus/usb/002/002",
                      "enabled": True}}
    claims = [{"metadata": {"name": "n1-0627-0001-002002"}, "spec": {"userName": "ju"}}]
    vms = [vm("kiosk", host=[{"name": "n1-0627-0001-002002", "deviceName": "kubevirt.io/n1-0627-0001-002002"}])]
    [r] = hd.usb_rows([dev], claims, vms)
    assert r["state"] == "enabled" and r["used_by"] == ["default/kiosk"]
    c = hd.usb_claim(dev, "ju")
    assert c["kind"] == "USBDeviceClaim" and c["spec"] == {"userName": "ju"}
    assert c["metadata"]["ownerReferences"][0]["kind"] == "USBDevice"
    with pytest.raises(ValueError, match="default/kiosk"):
        hd.usb_disable_check(dev, vms)
    assert hd.usb_settled(dev, claims[0], True)[0] is True
    assert hd.usb_settled({"status": {"enabled": False}}, None, False)[0] is True


def test_sriov_goes_through_zero_and_keeps_claimed_vfs():
    dev = {"metadata": {"name": "n3-enp5s0"}, "spec": {"nodeName": "n3", "address": "0000:05:00.0", "numVFs": 2},
           "status": {"status": "sriovNetworkDeviceEnabled", "vfAddresses": ["0000:05:10.0", "0000:05:10.2"],
                      "vfPCIDevices": ["n3-000005100", "n3-000005102"]}}
    claims = [{"metadata": {"name": "n3-000005102"}}]
    [r] = hd.sriov_rows([dev], claims)
    assert r["enabled"] and r["num_vfs"] == 2 and r["vfs_claimed"] == ["n3-000005102"] and r["interface"] == "enp5s0"
    with pytest.raises(ValueError, match="disable it first"):
        hd.sriov_patch(dev, 4, claims)
    with pytest.raises(ValueError, match="n3-000005102"):
        hd.sriov_patch(dev, 0, claims)
    assert hd.sriov_patch(dev, 0, []) == {"spec": {"numVFs": 0}}
    off = {"metadata": {"name": "x"}, "spec": {"numVFs": 0}, "status": {}}
    assert hd.sriov_patch(off, 3, []) == {"spec": {"numVFs": 3}}
    for bad in (-1, "many", 999):
        with pytest.raises(ValueError, match="virtual functions"):
            hd.sriov_patch(off, bad, [])
    assert hd.sriov_settled(dev, 2) == (True, "2 virtual functions ready")
    assert hd.sriov_settled(dev, 3)[0] is None
    assert hd.sriov_settled(off, 0)[0] is True


# -- commande et routes --------------------------------------------------------------

import argparse  # noqa: E402
import base64  # noqa: E402
import importlib.util  # noqa: E402

sys.path.insert(0, str(ROOT / "web"))
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_dev", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)
PW = "a long test password"
ADDON = {"metadata": {"namespace": "harvester-system", "name": "pcidevices-controller"}, "spec": {"enabled": True}}


class Kube:
    """Un cluster en mémoire : un claim créé devient actif à la relecture
    suivante, comme le ferait l'agent du nœud."""

    def __init__(self, objs):
        self.objs = dict(objs)
        self.calls = []

    def get(self, kind, ns, name):
        o = self.objs.get((kind, ns, name))
        if o is not None and kind == hd.K_PCICLAIM:
            o.setdefault("status", {})["passthroughEnabled"] = True
        if o is not None and kind == hd.K_USB:
            o["status"]["enabled"] = (hd.K_USBCLAIM, None, name) in self.objs
        if o is not None and kind == hd.K_SRIOV:
            n = o["spec"].get("numVFs") or 0
            o["status"] = {"status": "sriovNetworkDeviceEnabled", "vfAddresses": [f"0000:06:10.{i}" for i in range(n)]} if n else {}
        return json.loads(json.dumps(o)) if o is not None else None

    def list(self, kind, ns=None, selector=None):
        return [json.loads(json.dumps(o)) for (k, _, _), o in self.objs.items() if k == kind]

    def create(self, obj):
        kind = hd.K_PCICLAIM if obj["kind"] == "PCIDeviceClaim" else hd.K_USBCLAIM
        self.calls.append(("create", obj["kind"], obj["metadata"]["name"]))
        self.objs[(kind, None, obj["metadata"]["name"])] = obj

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))
        self.objs.pop((kind, ns, name), None)

    def patch(self, kind, ns, name, patch):
        self.calls.append(("patch", kind, name, patch))
        if "spec" in patch:
            self.objs[(kind, ns, name)]["spec"].update(patch["spec"])


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    wait = hres._wait
    monkeypatch.setattr(hres, "_wait", lambda *a, **kw: wait(*a, **{"sleep": lambda s: None, **kw}))


def _run(kube, **kw):
    a = argparse.Namespace(**{"name": None, "vfs": 0, "user": "ju", "timeout": 5, **kw})
    orig = hres.kube_from
    hres.kube_from = lambda _a: kube
    try:
        return hres.cmd_device(a)
    finally:
        hres.kube_from = orig


def _world(extra=None):
    base = {("addons.harvesterhci.io", "harvester-system", "pcidevices-controller"): ADDON,
            (hd.K_PCI, None, "n3-000005000"): pci("n3-000005000", "0000:05:00.0")}
    base.update(extra or {})
    return Kube(base)


def test_the_command_enables_and_disables_pci_passthrough():
    k = _world()
    assert _run(k, action="pci-enable", name=["n3-000005000"]) == hres.EXIT_OK
    assert k.calls == [("create", "PCIDeviceClaim", "n3-000005000")]
    with pytest.raises(ValueError, match="already enabled"):
        _run(k, action="pci-enable", name=["n3-000005000"])
    k.objs[("virtualmachines.kubevirt.io", "default", "gw")] = vm("gw", host=[{"name": "n3-000005000", "deviceName": "x"}])
    with pytest.raises(ValueError, match="default/gw"):
        _run(k, action="pci-disable", name=["n3-000005000"])
    del k.objs[("virtualmachines.kubevirt.io", "default", "gw")]
    assert _run(k, action="pci-disable", name=["n3-000005000"]) == hres.EXIT_OK
    assert k.calls[-1] == ("delete", hd.K_PCICLAIM, "n3-000005000")


def test_the_command_clears_a_stale_allocation_before_disabling():
    k = _world()
    assert _run(k, action="pci-enable", name=["n3-000005000"]) == hres.EXIT_OK
    k.objs[("virtualmachines.kubevirt.io", "default", "dev168")] = vm(
        "dev168", alloc={"hostdevices": {"intel.com/82574L": ["n3-000005000"]}})
    assert _run(k, action="pci-disable", name=["n3-000005000"]) == hres.EXIT_OK
    assert ("patch", "virtualmachines.kubevirt.io", "dev168",
            {"metadata": {"annotations": {hd.ALLOC_ANN: None}}}) in k.calls
    assert k.calls[-1] == ("delete", hd.K_PCICLAIM, "n3-000005000")


def test_the_command_refuses_without_the_addon_or_iommu():
    k = _world()
    k.objs[("addons.harvesterhci.io", "harvester-system", "pcidevices-controller")] = {**ADDON, "spec": {"enabled": False}}
    with pytest.raises(ValueError, match="add-on"):
        _run(k, action="pci-enable", name=["n3-000005000"])
    k = _world({(hd.K_PCI, None, "n3-x"): pci("n3-x", "0000:07:00.0", group="")})
    with pytest.raises(ValueError, match="IOMMU"):
        _run(k, action="pci-enable", name=["n3-000005000", "n3-x"])
    assert not k.calls                                          # rien de fait si un seul est refusé


def test_the_command_hands_usb_and_sets_virtual_functions():
    usb = {"metadata": {"name": "n1-0627-0001-002002", "uid": "u"}, "status": {"nodeName": "n1", "resourceName": "kubevirt.io/n1-0627-0001-002002"}}
    sriov = {"metadata": {"name": "n3-enp6s0"}, "spec": {"nodeName": "n3", "address": "0000:06:00.0", "numVFs": 0}, "status": {}}
    k = _world({(hd.K_USB, None, "n1-0627-0001-002002"): usb, (hd.K_SRIOV, None, "n3-enp6s0"): sriov})
    assert _run(k, action="usb-enable", name=["n1-0627-0001-002002"]) == hres.EXIT_OK
    assert _run(k, action="usb-disable", name=["n1-0627-0001-002002"]) == hres.EXIT_OK
    assert _run(k, action="sriov", name=["n3-enp6s0"], vfs=2) == hres.EXIT_OK
    assert ("patch", hd.K_SRIOV, "n3-enp6s0", {"spec": {"numVFs": 2}}) in k.calls
    with pytest.raises(ValueError, match="disable it first"):
        _run(k, action="sriov", name=["n3-enp6s0"], vfs=4)
    assert _run(k, action="sriov", name=["n3-enp6s0"], vfs=0) == hres.EXIT_OK


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
        w["actions"].append((label, cmd))
        if after:
            after()

        class Run:
            id = "dev000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    items = {"addons.harvesterhci.io": [ADDON], hd.K_PCI: [pci("n3-000005000", "0000:05:00.0")], hd.K_PCICLAIM: [],
             hd.K_USB: [], hd.K_USBCLAIM: [], hd.K_SRIOV: [], "virtualmachines.kubevirt.io": []}
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: {"items": items.get(a[1], [])})
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_devices_are_read_by_anyone_and_changed_by_administrators(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/devices/harv1", headers=auth("eye")).get_json()
        assert d["addon"] and d["pci"][0]["name"] == "n3-000005000" and d["pci"][0]["can_enable"]
        body = {"names": ["n3-000005000"]}
        assert c.post("/api/devices/harv1/do/pci-enable", json=body, headers=auth("ops")).status_code == 403
        assert c.post("/api/devices/harv1/do/explode", json=body, headers=auth("adm")).status_code == 400
        assert c.post("/api/devices/harv1/do/pci-enable", json={"names": ["Bad Name"]}, headers=auth("adm")).status_code == 400
        assert c.post("/api/devices/harv1/do/sriov", json={"name": "n3-enp6s0", "vfs": 999}, headers=auth("adm")).status_code == 400
        assert c.post("/api/devices/harv1/do/pci-enable", json=body, headers=auth("adm")).status_code == 202
        assert c.post("/api/devices/harv1/do/sriov", json={"name": "n3-enp6s0", "vfs": 2}, headers=auth("adm")).status_code == 202
    (l1, c1), (l2, c2) = world["actions"]
    assert l1 == "device:pci-enable:n3-000005000" and c1[2:4] == ["device", "pci-enable"] and c1[-2:] == ["--user", "adm"]
    assert l2 == "device:sriov:n3-enp6s0" and c2[-2:] == ["--vfs", "2"]
