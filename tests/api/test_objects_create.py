"""v1.59.0 : créer, modifier, supprimer les objets des sections.

Les objets produits ont la forme que Harvester v1.9.0 accepte (essai à blanc
côté serveur sur harv1 : image, classe de stockage, clé SSH, secret, réseau
VLAN, volume vide et volume depuis une image, tous acceptés par ses
webhooks). Chaque écriture passe par bin/harvester-resources.py.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_objects as ho  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres159", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)

PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl ops@node1"
IMAGE = {"metadata": {"name": "leap", "namespace": "default"},
         "status": {"storageClassName": "lh-123", "virtualSize": 10 * 2**30}}


# -- les objets ---------------------------------------------------------------------

def test_an_image_by_url():
    m = ho.normalize("image", {"url": "https://x/leap.qcow2"}, default_class="harv-rep1")
    assert m["metadata"]["generateName"] == "image-" and m["spec"]["sourceType"] == "download"
    assert m["spec"]["displayName"] == "leap.qcow2" and m["spec"]["targetStorageClassName"] == "harv-rep1"
    assert m["metadata"]["annotations"]["harvesterhci.io/storageClassName"] == "harv-rep1"
    with pytest.raises(ValueError, match="http"):
        ho.normalize("image", {"url": "file:///etc/passwd"})


def test_a_storage_class_like_harvesters():
    m = ho.normalize("storageclass", {"name": "fast", "replicas": "2", "disk_selector": "ssd, nvme",
                                      "migratable": False, "expansion": True})
    assert m["provisioner"] == "driver.longhorn.io"
    assert m["parameters"] == {"numberOfReplicas": "2", "staleReplicaTimeout": "30", "migratable": "false",
                               "dataEngine": "v1", "encrypted": "false", "diskSelector": "ssd,nvme"}
    assert m["allowVolumeExpansion"] is True and m["reclaimPolicy"] == "Delete"
    with pytest.raises(ValueError, match="strict-local"):
        ho.normalize("storageclass", {"name": "x", "replicas": 3, "data_locality": "strict-local"})
    with pytest.raises(ValueError):
        ho.normalize("storageclass", {"name": "x", "replicas": 4})


def test_an_ssh_key_and_a_secret():
    k = ho.normalize("sshkey", {"name": "ops", "public_key": "  " + PUB + "\n"})
    assert k["spec"]["publicKey"] == PUB
    with pytest.raises(ValueError, match="OpenSSH"):
        ho.normalize("sshkey", {"name": "ops", "public_key": "-----BEGIN OPENSSH PRIVATE KEY-----"})
    s = ho.normalize("secret", {"name": "ci", "data": {"userdata": "#cloud-config", "network.cfg": ""}})
    assert s["type"] == "Opaque" and s["stringData"] == {"userdata": "#cloud-config", "network.cfg": ""}
    with pytest.raises(ValueError):
        ho.normalize("secret", {"name": "ci", "data": {"bad key": "x"}})


def test_a_vm_network_like_harvesters():
    m = ho.normalize("network", {"name": "lab", "type": "vlan", "vlan": "3999", "cluster_network": "mgmt"})
    cfg = json.loads(m["spec"]["config"])
    assert cfg == {"cniVersion": "0.3.1", "name": "lab", "type": "bridge", "bridge": "mgmt-br",
                   "promiscMode": True, "ipam": {}, "vlan": 3999}
    assert m["metadata"]["labels"]["network.harvesterhci.io/type"] == "L2VlanNetwork"
    assert m["metadata"]["labels"]["network.harvesterhci.io/vlan-id"] == "3999"
    u = ho.normalize("network", {"name": "flat", "type": "untagged", "route_mode": "manual",
                                 "cidr": "192.168.10.0/24", "gateway": "192.168.10.1"})
    assert "vlan" not in json.loads(u["spec"]["config"])
    assert json.loads(u["metadata"]["annotations"]["network.harvesterhci.io/route"])["cidr"] == "192.168.10.0/24"
    with pytest.raises(ValueError, match="4094"):
        ho.normalize("network", {"name": "x", "type": "vlan", "vlan": 5000})


def test_a_volume_from_an_image_reads_its_class():
    """La classe d'une image est `lh-<uuid>` : lue sur l'image, jamais devinée."""
    m = ho.normalize("volume", {"name": "data", "size": "20Gi"}, image=IMAGE)
    assert m["spec"]["storageClassName"] == "lh-123"
    assert m["metadata"]["annotations"]["harvesterhci.io/imageId"] == "default/leap"
    with pytest.raises(ValueError, match="virtual size"):
        ho.normalize("volume", {"name": "data", "size": "5Gi"}, image=IMAGE)
    e = ho.normalize("volume", {"name": "data", "size": "5Gi", "storage_class": "harv-rep1"})
    assert e["spec"]["storageClassName"] == "harv-rep1" and "annotations" not in e["metadata"]
    with pytest.raises(ValueError, match="size"):
        ho.normalize("volume", {"name": "data", "size": "5GB"})


def test_who_blocks_a_deletion():
    vm = {"metadata": {"name": "web", "namespace": "default"},
          "spec": {"template": {"spec": {"networks": [{"multus": {"networkName": "lab"}}],
                                         "volumes": [{"persistentVolumeClaim": {"claimName": "data"}},
                                                     {"cloudInitNoCloud": {"secretRef": {"name": "ci"}}}]}}}}
    pvc = {"metadata": {"name": "data", "namespace": "default",
                        "annotations": {"harvesterhci.io/imageId": "default/leap"}},
           "spec": {"storageClassName": "fast"}}
    assert ho.vms_using("network", "default", "lab", [vm]) == ["default/web"]
    assert ho.vms_using("secret", "default", "ci", [vm]) == ["default/web"]
    assert ho.vms_using("volume", "default", "data", [vm]) == ["default/web"]
    assert ho.vms_using("image", "default", "leap", pvcs=[pvc]) == ["default/data"]
    assert ho.vms_using("storageclass", None, "fast", pvcs=[pvc]) == ["default/data"]
    assert ho.vms_using("network", "lab", "lab", [vm]) == []


def test_addon_values_must_be_a_yaml_mapping():
    assert ho.check_values("replicas: 2\n") == "replicas: 2\n"
    with pytest.raises(ValueError, match="YAML"):
        ho.check_values("a: [1, 2")
    with pytest.raises(ValueError, match="mapping"):
        ho.check_values("- a\n- b\n")


# -- l'outil -----------------------------------------------------------------------------

class FakeKube:
    def __init__(self, objects=None, lists=None):
        self.objects = objects or {}
        self.lists = lists or {}
        self.created, self.patched, self.deleted = [], [], []

    def get(self, kind, ns, name):
        return self.objects.get((kind, ns, name))

    def list(self, kind, ns=None, selector=None):
        return self.lists.get(kind, [])

    def create(self, obj):
        self.created.append(obj)
        meta = dict(obj["metadata"])
        meta.setdefault("name", meta.get("generateName", "x-") + "abcde")
        status = {"VirtualMachineImage": {"progress": 100, "conditions": [{"type": "Imported", "status": "True"}]},
                  "KeyPair": {"conditions": [{"type": "validated", "status": "True"}]},
                  "PersistentVolumeClaim": {"phase": "Bound"}}.get(obj["kind"], {})
        kind = {"VirtualMachineImage": ho.K["image"], "KeyPair": ho.K["sshkey"],
                "PersistentVolumeClaim": ho.K["volume"]}.get(obj["kind"], obj["kind"])
        self.objects[(kind, meta.get("namespace"), meta["name"])] = dict(obj, metadata=meta, status=status)
        return dict(obj, metadata=meta)

    def patch(self, kind, ns, name, patch):
        self.patched.append((kind, name, patch))

    def delete(self, kind, ns, name, cascade=None):
        self.deleted.append((kind, name))
        self.objects.pop((kind, ns, name), None)


def run(kube, argv, monkeypatch, tmp_path=None, spec=None):
    monkeypatch.setattr(hres, "kube_from", lambda args: kube)
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    if spec is not None:
        f = tmp_path / "spec.json"
        f.write_text(json.dumps(spec))
        argv = argv + ["--spec", str(f)]
    return hres.main(argv)


def test_an_image_is_created_and_followed_until_imported(monkeypatch, tmp_path, capsys):
    k = FakeKube(lists={ho.K["storageclass"]: [{"metadata": {"name": "harv-rep1", "annotations": {
        "storageclass.kubernetes.io/is-default-class": "true"}}}]})
    assert run(k, ["create", "--kind", "image"], monkeypatch, tmp_path,
               {"namespace": "default", "url": "https://x/a.qcow2"}) == hres.EXIT_OK
    assert k.created[0]["spec"]["targetStorageClassName"] == "harv-rep1"      # la classe par défaut
    assert "imported" in capsys.readouterr().err


def test_a_used_object_is_not_deleted(monkeypatch, capsys):
    net = ("network-attachment-definitions.k8s.cni.cncf.io", "default", "lab")
    vm = {"metadata": {"name": "web", "namespace": "default"},
          "spec": {"template": {"spec": {"networks": [{"multus": {"networkName": "default/lab"}}]}}}}
    k = FakeKube({net: {}}, {"virtualmachines.kubevirt.io": [vm]})
    assert run(k, ["delete", "--kind", "network", "--namespace", "default", "--name", "lab"],
               monkeypatch) == hres.EXIT_BLOCKED
    assert "still used by default/web" in capsys.readouterr().err and k.deleted == []
    k = FakeKube({net: {}}, {"virtualmachines.kubevirt.io": []})
    assert run(k, ["delete", "--kind", "network", "--namespace", "default", "--name", "lab"],
               monkeypatch) == hres.EXIT_OK
    assert k.deleted == [(ho.K["network"], "lab")]


def test_the_default_class_moves(monkeypatch):
    classes = [{"metadata": {"name": "harv-rep1", "annotations": {"storageclass.kubernetes.io/is-default-class": "true"}}},
               {"metadata": {"name": "fast", "annotations": {}}}]
    k = FakeKube(lists={ho.K["storageclass"]: classes})
    assert run(k, ["sc-default", "--name", "fast"], monkeypatch) == hres.EXIT_OK
    # Harvester refuse une seconde classe par défaut : l'ancienne d'abord
    assert [(n, p["metadata"]["annotations"]["storageclass.kubernetes.io/is-default-class"]) for _, n, p in k.patched] \
        == [("harv-rep1", "false"), ("fast", "true")]


def test_the_old_default_comes_back_if_the_new_one_is_refused(monkeypatch):
    classes = [{"metadata": {"name": "harv-rep1", "annotations": {"storageclass.kubernetes.io/is-default-class": "true"}}},
               {"metadata": {"name": "fast", "annotations": {}}}]

    class Refusing(FakeKube):
        def patch(self, kind, ns, name, patch):
            if name == "fast":
                raise hres.KubeError("admission webhook denied the request: nope")
            super().patch(kind, ns, name, patch)
    k = Refusing(lists={ho.K["storageclass"]: classes})
    assert run(k, ["sc-default", "--name", "fast"], monkeypatch) == hres.EXIT_FAIL
    assert [(n, p["metadata"]["annotations"]["storageclass.kubernetes.io/is-default-class"]) for _, n, p in k.patched] \
        == [("harv-rep1", "false"), ("harv-rep1", "true")]


def test_a_volume_only_grows(monkeypatch, capsys):
    pvc = {"spec": {"resources": {"requests": {"storage": "20Gi"}}, "storageClassName": "harv-rep1"},
           "status": {"capacity": {"storage": "20Gi"}}}
    k = FakeKube({(ho.K["volume"], "default", "data"): pvc,
                  (ho.K["storageclass"], None, "harv-rep1"): {"allowVolumeExpansion": True}})
    assert run(k, ["volume-expand", "--namespace", "default", "--name", "data", "--size", "10Gi"],
               monkeypatch) == hres.EXIT_BLOCKED
    assert "only grows" in capsys.readouterr().err
