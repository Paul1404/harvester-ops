"""v1.63.0 : les actions de Harvester sur un volume et une image, refaites au
kubectl (bin/lib/hv_storage.py, harvester-resources volume|image, routes).

Formats relevés dans le serveur de Harvester 1.9 (pkg/api/volume,
pkg/api/image) : clone par dataSource, instantané possédé par son PVC,
copie par DataVolume CDI, annulation d'agrandissement par recréation du
PVC sur le même PV, export et chiffrement par une nouvelle image.
"""

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_storage as hs  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_sto", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)


def a_pvc(name="data", sc="harvester-longhorn", image=None, resizing=False, cap="10Gi", req="10Gi"):
    ann = {"field.cattle.io/description": "d", "pv.kubernetes.io/bind-completed": "yes"}
    if image:
        ann[hs.IMAGE_ID] = image
    return {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
            "metadata": {"name": name, "namespace": "default", "uid": "u-1", "resourceVersion": "9", "annotations": ann},
            "spec": {"accessModes": ["ReadWriteMany"], "volumeMode": "Block", "storageClassName": sc,
                     "resources": {"requests": {"storage": req}}, "volumeName": "pvc-123"},
            "status": {"phase": "Bound", "capacity": {"storage": cap},
                       "conditions": [{"type": "Resizing", "status": "True"}] if resizing else []}}


LH1 = {"provisioner": "driver.longhorn.io", "parameters": {"numberOfReplicas": "3"}}
LH2 = {"provisioner": "driver.longhorn.io", "parameters": {"dataEngine": "v2"}}
LVM = {"provisioner": "lvm.driver.harvesterhci.io", "parameters": {"vgName": "vg"}}


def an_image(src="download", backend="backingimage", ready=True, encrypted=False):
    return {"metadata": {"name": "image-a", "namespace": "default", "labels": {"harvesterhci.io/imageDisplayName": "leap", "team": "ops"},
                         "annotations": {hs.SC_ANN: "harvester-longhorn", hs.DESC: "old"}},
            "spec": {"displayName": "leap", "sourceType": src, "url": "https://x/leap.qcow2", "backend": backend,
                     "targetStorageClassName": "harvester-longhorn", "checksum": "a" * 128,
                     "storageClassParameters": {"encrypted": "true"} if encrypted else {}},
            "status": {"storageClassName": "lh-abc",
                       "conditions": [{"type": "Imported", "status": "True" if ready else "Unknown"}]}}


def test_clone_with_and_without_data():
    c = hs.clone_pvc(a_pvc(image="default/image-a"), "data2")
    assert c["spec"]["dataSource"] == {"kind": "PersistentVolumeClaim", "name": "data"}
    assert c["spec"]["resources"] == {"requests": {"storage": "10Gi"}} and c["spec"]["storageClassName"] == "harvester-longhorn"
    assert c["metadata"]["annotations"] == {hs.IMAGE_ID: "default/image-a", hs.DESC: "d"}      # pas bind-completed
    assert "dataSource" not in hs.clone_pvc(a_pvc(), "data3", with_data=False)["spec"]
    with pytest.raises(ValueError):
        hs.clone_pvc(a_pvc(), "Bad_Name")


def test_snapshot_is_owned_by_its_volume_and_uses_the_csi_setting():
    cls = hs.csi_snapshot_class("", "driver.longhorn.io")
    assert cls == "longhorn-snapshot"
    assert hs.csi_snapshot_class('{"lvm.driver.harvesterhci.io": {"volumeSnapshotClassName": "lvm-snapshot"}}',
                                 "lvm.driver.harvesterhci.io") == "lvm-snapshot"
    snap = hs.volume_snapshot(a_pvc(image="default/image-a"), "s1", cls, "driver.longhorn.io")
    assert snap["metadata"]["ownerReferences"] == [{"apiVersion": "v1", "kind": "PersistentVolumeClaim", "name": "data", "uid": "u-1"}]
    assert snap["spec"] == {"source": {"persistentVolumeClaimName": "data"}, "volumeSnapshotClassName": "longhorn-snapshot"}
    assert snap["metadata"]["annotations"][hs.IMAGE_ID] == "default/image-a"
    with pytest.raises(ValueError, match="snapshot class"):
        hs.volume_snapshot(a_pvc(), "s1", None, "x")


def test_copy_is_a_cdi_data_volume_and_cancel_expand_recreates_the_claim():
    dv = hs.data_volume(a_pvc(), "data-fast", "fast")
    assert dv["kind"] == "DataVolume" and dv["spec"]["source"] == {"pvc": {"name": "data", "namespace": "default"}}
    assert dv["spec"]["storage"] == {"storageClassName": "fast", "resources": {"requests": {"storage": "10Gi"}}}
    with pytest.raises(ValueError, match="internal"):
        hs.data_volume(a_pvc(), "x", "vmstate-persistence")
    stuck = a_pvc(resizing=True, req="100Ti", cap="10Gi")
    assert hs.resizing(stuck) and not hs.resizing(a_pvc())
    new = hs.recreated_pvc(stuck)
    assert new["spec"]["resources"]["requests"]["storage"] == "10Gi" and new["spec"]["volumeName"] == "pvc-123"
    assert "uid" not in new["metadata"] and "status" not in new
    assert "pv.kubernetes.io/bind-completed" not in new["metadata"]["annotations"]
    vms = [{"metadata": {"name": "web", "namespace": "default", "annotations": {
        "harvesterhci.io/volumeClaimTemplates": json.dumps([{"metadata": {"name": "data"}}])}}, "spec": {"template": {"spec": {}}}}]
    assert hs.used_by_vms("data", "default", vms) == ["web"] and hs.used_by_vms("data", "other", vms) == []


def test_export_chooses_the_backend_like_harvester():
    img = hs.export_image(a_pvc(), "data-img", "default", "harvester-longhorn", LH1, LH1)
    assert img["spec"]["backend"] == "backingimage" and img["spec"]["sourceType"] == "export-from-volume"
    assert img["spec"]["pvcName"] == "data" and img["metadata"]["annotations"] == {hs.SC_ANN: "harvester-longhorn"}
    assert hs.export_image(a_pvc(), "x", "default", "v2", LH2, LH1)["spec"]["backend"] == "cdi"
    with pytest.raises(ValueError, match="outside Longhorn v1"):
        hs.export_image(a_pvc(), "x", "default", "h", LH1, LVM)
    with pytest.raises(ValueError, match="encrypted"):
        hs.export_image(a_pvc(), "x", "default", "h", LH1, LH1, encrypted=True)
    assert hs.pv_encrypted({"spec": {"csi": {"volumeAttributes": {"encrypted": "true"}}}})


def test_image_edit_clone_and_crypto():
    ops = hs.image_edit_patch(an_image(), "new", {"env": "prod"})
    assert ops[0] == {"op": "replace", "path": "/metadata/labels",
                      "value": {"harvesterhci.io/imageDisplayName": "leap", "env": "prod"}}      # team retiré, système gardé
    assert ops[1]["value"][hs.DESC] == "new" and ops[1]["value"][hs.SC_ANN] == "harvester-longhorn"
    with pytest.raises(ValueError, match="kept by Harvester"):
        hs.image_edit_patch(an_image(), labels={"harvesterhci.io/os-type": "x"})
    c = hs.clone_image(an_image(), "leap-2")
    assert c["spec"]["url"] == "https://x/leap.qcow2" and c["spec"]["displayName"] == "leap-2" and c["spec"]["checksum"]
    with pytest.raises(ValueError, match="downloaded"):
        hs.clone_image(an_image(src="upload"), "x")
    enc_sc = {"parameters": {"encrypted": "true"}}
    e = hs.crypto_image(an_image(), "encrypt", "leap-enc", "enc", enc_sc)
    assert e["spec"]["sourceType"] == "clone" and e["spec"]["securityParameters"] == {
        "cryptoOperation": "encrypt", "sourceImageName": "image-a", "sourceImageNamespace": "default"}
    with pytest.raises(ValueError, match="encrypted storage class"):
        hs.crypto_image(an_image(), "encrypt", "x", "plain", {"parameters": {}})
    with pytest.raises(ValueError, match="not encrypted"):
        hs.crypto_image(an_image(), "decrypt", "x", "plain", {"parameters": {}})
    with pytest.raises(ValueError, match="not ready"):
        hs.crypto_image(an_image(ready=False), "encrypt", "x", "enc", enc_sc)
    assert hs.crypto_image(an_image(encrypted=True), "decrypt", "x", "plain", {"parameters": {}})["spec"]["securityParameters"]["cryptoOperation"] == "decrypt"


def test_a_first_download_failure_is_not_the_end():
    """Vu sur harv1 : Harvester réessaie ; RetryLimitExceeded=False au 1er échec."""
    img = an_image(ready=False)
    img["status"].update({"failed": 1, "conditions": [{"type": "RetryLimitExceeded", "status": "False", "message": "no route"}]})
    assert hs.image_state(img) == ("importing", "retrying: no route")
    img["status"]["conditions"][0]["status"] = "True"
    assert hs.image_state(img)[0] == "failed"


def test_download_and_upload_paths():
    bi = hs.backing_image_of(an_image(), {"parameters": {"backingImage": "vmi-123"}})
    assert hs.download_path(bi) == \
        "/api/v1/namespaces/longhorn-system/services/http:longhorn-backend:9500/proxy/v1/backingimages/vmi-123/download"
    with pytest.raises(ValueError, match="backing image"):
        hs.backing_image_of(an_image(), {"parameters": {}})
    up = hs.upload_image("default", "cirros", "http://10.0.0.1:8092/image/t", "harvester-longhorn",
                         checksum="B" * 128, file_name="cirros.qcow2")
    assert up["spec"]["sourceType"] == "download" and up["spec"]["checksum"] == "b" * 128
    assert up["metadata"]["labels"] == {"harvesterhci.io/image-type": "raw_qcow2"}
    assert hs.upload_image("default", "x", "http://h/i", "", file_name="a.ISO")["metadata"]["labels"]["harvesterhci.io/image-type"] == "iso"
    with pytest.raises(ValueError, match="SHA512"):
        hs.upload_image("default", "x", "http://h/i", "", checksum="abc")


# -- l'outil -----------------------------------------------------------------------

class Kube:
    def __init__(self, objs):
        self.objs = dict(objs)
        self.calls = []

    def get(self, kind, ns, name):
        return json.loads(json.dumps(self.objs[(kind, ns, name)])) if (kind, ns, name) in self.objs else None

    def list(self, kind, ns=None, selector=None):
        return [json.loads(json.dumps(o)) for (k, n, _), o in self.objs.items() if k == kind and (ns is None or n == ns)]

    def patch(self, kind, ns, name, patch):
        self.calls.append(("patch", kind, name, patch))

    def create(self, obj):
        self.calls.append(("create", obj["kind"], obj["metadata"].get("name") or obj["metadata"].get("generateName")))
        made = json.loads(json.dumps(obj))
        made["metadata"].setdefault("name", "image-new")
        return made

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))
        self.objs.pop((kind, ns, name), None)

    def run(self, *a, input=None, timeout=None):
        self.calls.append(("run",) + a)
        return ""


def vargs(**kw):
    base = {"namespace": "default", "name": "data", "new_name": None, "no_data": False, "display_name": None,
            "target_namespace": None, "storage_class": None, "snapshot_name": None, "description": None, "timeout": 30}
    base.update(kw)
    return type("A", (), base)()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    wait = hres._wait
    monkeypatch.setattr(hres, "_wait", lambda *a, **kw: wait(*a, **{"sleep": lambda s: None, **kw}))


def test_cancel_expand_follows_harvester_s_sequence():
    stuck = a_pvc(resizing=True, req="100Ti")
    k = Kube({(hs.K_PVC, "default", "data"): stuck,
              (hs.K_PV, None, "pvc-123"): {"spec": {"persistentVolumeReclaimPolicy": "Delete"}}})

    def create(obj):
        k.calls.append(("create", obj["kind"], obj["metadata"]["name"]))
        done = json.loads(json.dumps(obj))
        done["status"] = {"phase": "Bound"}
        k.objs[(hs.K_PVC, "default", "data")] = done
        return done
    k.create = create
    assert hres.vol_cancel_expand(k, vargs()) == hres.EXIT_OK
    kinds = [(c[0], c[1]) for c in k.calls]
    assert kinds[0] == ("patch", hs.K_PV) and k.calls[0][3] == {"spec": {"persistentVolumeReclaimPolicy": "Retain"}}
    assert ("delete", hs.K_PVC) in kinds and ("create", "PersistentVolumeClaim") in kinds
    assert any(c[0] == "run" and "/spec/claimRef" in c[-1] for c in k.calls)
    assert k.calls[-1] == ("patch", hs.K_PV, "pvc-123", {"spec": {"persistentVolumeReclaimPolicy": "Delete"}})
    with pytest.raises(ValueError, match="not being expanded"):
        hres.vol_cancel_expand(Kube({(hs.K_PVC, "default", "data"): a_pvc()}), vargs())


def test_copy_refuses_a_mounted_volume():
    pod = {"metadata": {"name": "virt-launcher-web"}, "status": {"phase": "Running"},
           "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": "data"}}]}}
    k = Kube({(hs.K_PVC, "default", "data"): a_pvc(), (hs.K_SC, None, "fast"): {"provisioner": "x"},
              ("pods", "default", "virt-launcher-web"): pod})
    with pytest.raises(ValueError, match="virt-launcher-web"):
        hres.vol_copy(k, vargs(new_name="data2", storage_class="fast"))


# -- les routes --------------------------------------------------------------------

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
    monkeypatch.setattr(wapp, "IMAGE_UPLOAD_DIR", tmp_path / "uploads")
    wapp._accounts().create("ops", PW, "operator")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": []}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        files = {cmd[i + 1]: Path(cmd[i + 1]).read_bytes() for i, a in enumerate(cmd) if a in ("--labels-file", "--file")}
        w["actions"].append((label, cmd, files))
        if after:
            after()

        class Run:
            id = "sto000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    objs = {hs.K_PVC: a_pvc(resizing=True), "virtualmachines.kubevirt.io": {"items": []},
            hs.K_SC: {"items": [{"metadata": {"name": "harvester-longhorn"}, **LH1},
                                {"metadata": {"name": "vmstate-persistence"}, **LH1}]},
            "crd": {"metadata": {"name": "datavolumes.cdi.kubevirt.io"}},
            "settings.harvesterhci.io": {"value": ""}}
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: json.loads(json.dumps(objs.get(a[1]))))
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_volume_info_says_what_the_actions_need(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/volume/harv1/default/data/info", headers=auth("eye")).get_json()
    assert d["resizing"] and d["cdi"] and d["snapshot_class"] == "longhorn-snapshot" and d["longhorn_v1"]
    assert [s["name"] for s in d["storage_classes"] if s["internal"]] == ["vmstate-persistence"]


def test_volume_and_image_actions_are_checked_then_run(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/volume/harv1/default/data/do/clone", json={"new_name": "x"}, headers=auth("eye")).status_code == 403
        assert c.post("/api/volume/harv1/default/data/do/explode", headers=auth("ops")).status_code == 400
        assert c.post("/api/volume/harv1/default/data/do/clone", json={"new_name": "Bad Name"}, headers=auth("ops")).status_code == 400
        assert c.post("/api/volume/harv1/default/data/do/clone", json={"new_name": "data2", "with_data": False},
                      headers=auth("ops")).status_code == 202
        assert c.post("/api/volume/harv1/default/data/do/export", json={"display_name": "img", "storage_class": "harvester-longhorn"},
                      headers=auth("ops")).status_code == 202
        assert c.post("/api/image/harv1/default/image-a/do/edit", json={"labels": {"harvesterhci.io/x": "y"}},
                      headers=auth("ops")).status_code == 400
        assert c.post("/api/image/harv1/default/image-a/do/edit", json={"description": "d", "labels": {"env": "prod"}},
                      headers=auth("ops")).status_code == 202
        assert c.post("/api/image/harv1/default/image-a/do/encrypt", json={"display_name": "e", "storage_class": "enc"},
                      headers=auth("ops")).status_code == 202
    assert world["actions"][0][1][-3:] == ["--new-name", "data2", "--no-data"]
    assert world["actions"][1][0] == "volume:export:default/data"
    label, cmd, files = world["actions"][2]
    assert label == "image:edit:default/image-a" and json.loads(list(files.values())[0]) == {"env": "prod"}
    assert not any(Path(f).exists() for f in files)
    assert world["actions"][3][1][-4:] == ["--display-name", "e", "--storage-class", "enc"]


def test_an_upload_is_received_then_offered_to_the_cluster(world):
    body = b"QFI\xfb" + b"\0" * 1000
    with wapp.app.test_client() as c:
        assert c.put("/api/image-upload/harv1/default?display_name=cirros&file_name=cirros.qcow2", data=body,
                     headers=auth("eye")).status_code == 403
        assert c.put("/api/image-upload/harv1/default?display_name=cirros&checksum=zz", data=body,
                     headers=auth("ops")).status_code == 400
        r = c.put("/api/image-upload/harv1/default?display_name=cirros&file_name=cirros.qcow2&storage_class=harvester-longhorn",
                  data=body, headers=auth("ops"))
        assert r.status_code == 202, r.get_json()
    label, cmd, files = world["actions"][-1]
    assert label == "image:upload:default/cirros" and cmd[2:4] == ["image", "upload"]
    assert list(files.values()) == [body]                          # le fichier reçu, intact
    assert not any(Path(f).exists() for f in files)                # effacé après l'action
    assert cmd[cmd.index("--file-name") + 1] == "cirros.qcow2"
