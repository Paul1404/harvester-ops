"""v1.61.0 : CD-ROM à chaud (insérer une image dans un lecteur vide, l'en
retirer en gardant le lecteur) et carte réseau à chaud, avec les règles du
serveur de Harvester v1.9.0 (pkg/api/vm/handler.go), vérifiées sur harv1 :
une image de 790 Mo insérée en 28 s dans une VM en marche, le réseau overlay
refusé, une carte ajoutée puis marquée absente."""

import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_vm as hv  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_hp", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)


def vm(macs=True, cd_volume=False):
    ifaces = [{"name": "default", "bridge": {}, "model": "virtio", **({"macAddress": "52:54:00:00:00:01"} if macs else {})}]
    vols = [{"name": "root", "persistentVolumeClaim": {"claimName": "web-root"}}]
    if cd_volume:
        vols.append({"name": "cd1", "persistentVolumeClaim": {"claimName": "web-cd1-abcde", "hotpluggable": True}})
    return {"metadata": {"name": "web", "namespace": "default", "resourceVersion": "5",
                         "annotations": {hv.VCT: json.dumps([{"metadata": {"name": "web-root"}, "spec": {}}] +
                                                            ([{"metadata": {"name": "web-cd1-abcde"}, "spec": {}}] if cd_volume else []))}},
            "spec": {"template": {"spec": {
                "domain": {"devices": {"disks": [{"name": "root", "disk": {"bus": "virtio"}},
                                                 {"name": "cd1", "cdrom": {"bus": "sata"}},
                                                 {"name": "cd2", "cdrom": {"bus": "scsi"}}],
                                       "interfaces": ifaces}},
                "networks": [{"name": "default", "pod": {}}], "volumes": vols}}}}


IMAGE = {"metadata": {"name": "debian", "namespace": "default"},
         "status": {"storageClassName": "lh-debian", "virtualSize": 790626304, "size": 790626304}}


def test_only_empty_sata_drives_take_an_image():
    assert hv.sata_cdroms(vm()) == [("cd1", True)]                  # un lecteur scsi n'est pas éjectable à chaud
    out, claim = hv.insert_cdrom(vm(), "cd1", IMAGE, rnd=random.Random(1))
    t = [x for x in json.loads(out["metadata"]["annotations"][hv.VCT]) if x["metadata"]["name"] == claim][0]
    assert t["spec"]["resources"]["requests"]["storage"] == "1Gi"   # 790 Mo arrondis au Gio
    assert t["spec"]["storageClassName"] == "lh-debian"             # la classe LUE sur l'image
    assert t["metadata"]["annotations"]["harvesterhci.io/imageId"] == "default/debian"
    vol = [v for v in out["spec"]["template"]["spec"]["volumes"] if v["name"] == "cd1"][0]
    assert vol["persistentVolumeClaim"] == {"claimName": claim, "hotpluggable": True}
    with pytest.raises(ValueError, match="already holds an image"):
        hv.insert_cdrom(vm(cd_volume=True), "cd1", IMAGE)
    with pytest.raises(ValueError, match="no SATA CD-ROM"):
        hv.insert_cdrom(vm(), "cd2", IMAGE)
    with pytest.raises(ValueError, match="storage class"):
        hv.insert_cdrom(vm(), "cd1", {"metadata": {}, "status": {}})


def test_eject_keeps_the_drive():
    out, claims = hv.eject_image(vm(cd_volume=True), "cd1")
    assert claims == ["web-cd1-abcde"]
    assert "cd1" in [d["name"] for d in out["spec"]["template"]["spec"]["domain"]["devices"]["disks"]]
    assert "cd1" not in [v["name"] for v in out["spec"]["template"]["spec"]["volumes"]]
    assert [t["metadata"]["name"] for t in json.loads(out["metadata"]["annotations"][hv.VCT])] == ["web-root"]
    with pytest.raises(ValueError, match="already empty"):
        hv.eject_image(vm(), "cd1")


def test_hot_nic_rules():
    out = hv.add_nic(vm(), "nic2", "default/vlan20")
    ifc = out["spec"]["template"]["spec"]["domain"]["devices"]["interfaces"][-1]
    assert ifc == {"name": "nic2", "model": "virtio", "bridge": {}}
    assert out["spec"]["template"]["spec"]["networks"][-1] == {"name": "nic2", "multus": {"networkName": "default/vlan20"}}
    with pytest.raises(ValueError, match="without a MAC"):
        hv.add_nic(vm(macs=False), "nic2", "default/vlan20")
    guest = vm()
    guest["metadata"]["labels"] = {"harvesterhci.io/creator": "docker-machine-driver-harvester"}
    with pytest.raises(ValueError, match="guest cluster"):
        hv.add_nic(guest, "nic2", "default/vlan20")
    with pytest.raises(ValueError, match="single network interface"):
        hv.remove_nic(vm(), "default")
    two = hv.add_nic(vm(), "nic2", "default/vlan20")
    for i in two["spec"]["template"]["spec"]["domain"]["devices"]["interfaces"]:
        i["macAddress"] = i.get("macAddress") or "52:54:00:00:00:02"
    gone = hv.remove_nic(two, "nic2")
    assert gone["spec"]["template"]["spec"]["domain"]["devices"]["interfaces"][-1]["state"] == "absent"
    with pytest.raises(ValueError, match="already being unplugged"):
        hv.remove_nic(gone, "nic2")


def test_which_networks_are_hot_pluggable():
    bridge = {"metadata": {"namespace": "default"}, "spec": {"config": json.dumps({"type": "bridge"})}}
    assert hv.nad_hotpluggable(bridge)
    assert not hv.nad_hotpluggable({**bridge, "metadata": {"namespace": "harvester-system"}})   # réseau de stockage...
    assert not hv.nad_hotpluggable({"metadata": {"namespace": "default"},
                                    "spec": {"config": json.dumps({"type": "kube-ovn"})}})


class Kube:
    def __init__(self, objs):
        self.objs, self.calls = dict(objs), []

    def get(self, kind, ns, name):
        return json.loads(json.dumps(self.objs.get((kind, ns, name)))) if (kind, ns, name) in self.objs else None

    def list(self, kind, ns=None, selector=None):
        return [o for (k, n, _), o in self.objs.items() if k == kind and (ns is None or n == ns)]

    def run(self, *args, input=None, timeout=None):
        self.calls.append(args)
        return "{}"

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))


def args(**kw):
    base = {"namespace": "default", "name": "web", "timeout": 30, "volume": None, "image": None,
            "iface": None, "network": None, "mac": None, "node": None}
    base.update(kw)
    return type("A", (), base)()


def test_a_nic_on_a_single_node_cluster_applies_at_the_next_restart(capsys, monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    nad = {"metadata": {"namespace": "default", "name": "vlan20"}, "spec": {"config": json.dumps({"type": "bridge"})}}
    k = Kube({(hres.K_VM, "default", "web"): vm(), ("network-attachment-definitions.k8s.cni.cncf.io", "default", "vlan20"): nad,
              (hres.K_VMI, "default", "web"): {"status": {"nodeName": "n1"}},
              ("nodes", None, "n1"): {"metadata": {"name": "n1"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}})
    assert hres.vm_add_nic(k, args(iface="nic2", network="default/vlan20")) == hres.EXIT_OK
    patch = json.loads([c for c in k.calls if c[0] == "patch"][0][-1])
    assert patch[0] == {"op": "test", "path": "/metadata/resourceVersion", "value": "5"}
    assert "applies at the next restart" in capsys.readouterr().err


def test_ejecting_deletes_the_image_volume(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    k = Kube({(hres.K_VM, "default", "web"): vm(cd_volume=True)})
    assert hres.vm_eject_image(k, args(volume="cd1")) == hres.EXIT_OK
    assert ("delete", "persistentvolumeclaims", "web-cd1-abcde") in k.calls


# -- CPU et mémoire à chaud, migration du stockage, quota, accès ------------------------

def hot_vm():
    v = vm()
    v["metadata"]["annotations"][hv.HOTPLUG_ANN] = "true"
    v["spec"]["template"]["spec"]["domain"].update(
        {"cpu": {"sockets": 1, "cores": 1, "threads": 1, "maxSockets": 4}, "memory": {"guest": "1Gi", "maxGuest": "4Gi"}})
    return v


def test_cpu_and_memory_hotplug_follows_harvester_limits():
    ops = hv.cpumem_patch(hot_vm(), 2, "2Gi")
    assert ops == [{"op": "replace", "path": "/spec/template/spec/domain/cpu/sockets", "value": 2},
                   {"op": "replace", "path": "/spec/template/spec/domain/memory/guest", "value": "2Gi"}]
    with pytest.raises(ValueError, match="from 1 to 4"):
        hv.cpumem_patch(hot_vm(), 5)
    with pytest.raises(ValueError, match="at most 4Gi"):
        hv.cpumem_patch(hot_vm(), None, "8Gi")
    with pytest.raises(ValueError, match="at least 1Gi"):      # règle de KubeVirt, vue sur harvlab
        hv.cpumem_patch(hot_vm(), None, "512Mi")
    with pytest.raises(ValueError, match="not enabled"):
        hv.cpumem_patch(vm(), 2)
    many = hot_vm()
    many["spec"]["template"]["spec"]["domain"]["cpu"]["cores"] = 2      # Harvester : un cœur par socket
    assert not hv.cpumem_info(many)["enabled"]


def test_storage_migration_marks_the_target_then_cancels():
    target = {"metadata": {"name": "web-root-fast"}}
    out = hv.storage_migration(vm(), "web-root", target, vms=[])
    e = [x for x in json.loads(out["metadata"]["annotations"][hv.VCT]) if x["metadata"]["name"] == "web-root"][0]
    assert e["targetVolume"] == "web-root-fast"
    with pytest.raises(ValueError, match="already in progress"):
        hv.storage_migration(out, "web-root", target)
    other = vm()
    other["metadata"]["name"] = "api"
    other["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] = "web-root-fast"
    with pytest.raises(ValueError, match="already used by the VM api"):
        hv.storage_migration(vm(), "web-root", target, vms=[other])
    running = json.loads(json.dumps(out))
    running["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] = "web-root-fast"
    running["spec"]["updateVolumesStrategy"] = "Migration"
    back = hv.cancel_storage_migration(running)
    assert back["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "web-root"
    assert "updateVolumesStrategy" not in back["spec"]


def test_a_late_cancel_is_refused(monkeypatch):
    """Vu sur harvlab : KubeVirt avait déjà basculé la VMI sur la cible."""
    migrating = hv.storage_migration(vm(), "web-root", {"metadata": {"name": "web-root-fast"}})
    k = Kube({(hres.K_VM, "default", "web"): migrating,
              (hres.K_VMI, "default", "web"): {"spec": {"volumes": [{"name": "root", "persistentVolumeClaim": {"claimName": "web-root-fast"}}]}}})
    with pytest.raises(ValueError, match="can no longer be cancelled"):
        hres.vm_storage_migrate(k, args(), cancel=True)


def test_the_snapshot_quota_object():
    obj = hv.quota_object(None, "default", "web", "20Gi")
    assert obj["metadata"]["name"] == "default-resource-quota" and obj["kind"] == "ResourceQuota"
    assert obj["spec"]["snapshotLimit"]["vmTotalSnapshotSizeQuota"] == {"web": 20 * 2**30}
    gone = hv.quota_object(obj, "default", "web", "0")
    assert gone["spec"]["snapshotLimit"]["vmTotalSnapshotSizeQuota"] == {}
    assert hv.quota_object(None, "default", "web", "0") is None
    with pytest.raises(ValueError):
        hv.quota_object(None, "default", "web", "lots")


def test_access_credentials_as_harvester_writes_them():
    v = vm()
    v["metadata"]["uid"] = "u-1"
    sec, out = hv.access_credential(v, "basic", ["root"], password="s3cret!", rnd=random.Random(1))
    assert sec["stringData"] == {"root": "s3cret!"}
    assert sec["metadata"]["labels"] == {"harvesterhci.io/cloud-init-template": "harvester"}
    assert sec["metadata"]["ownerReferences"][0]["uid"] == "u-1"
    cred = out["spec"]["template"]["spec"]["accessCredentials"][0]["userPassword"]
    assert cred == {"source": {"secret": {"secretName": sec["metadata"]["name"]}}, "propagationMethod": {"qemuGuestAgent": {}}}
    assert json.loads(out["spec"]["template"]["metadata"]["annotations"]["harvesterhci.io/dynamic-ssh-key-users"]) == ["root"]
    sec2, out2 = hv.access_credential(out, "ssh", ["ops"], keys=[{"namespace": "default", "name": "k1", "public_key": "ssh-ed25519 AAA"}])
    assert sec2["stringData"] == {"default-k1": "ssh-ed25519 AAA"}
    ann = out2["spec"]["template"]["metadata"]["annotations"]
    assert json.loads(ann["harvesterhci.io/dynamic-ssh-key-users"]) == ["root", "ops"]
    assert json.loads(ann["harvesterhci.io/dynamic-ssh-key-names"]) == {sec2["metadata"]["name"]: ["default/k1"]}
    with pytest.raises(ValueError, match="6 characters"):
        hv.access_credential(v, "basic", ["root"], password="short")
    with pytest.raises(ValueError, match="user names"):
        hv.access_credential(v, "basic", ["bad user"], password="longenough")
