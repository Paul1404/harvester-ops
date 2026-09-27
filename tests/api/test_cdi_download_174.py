"""v1.74.0 : télécharger une image CDI (hors Longhorn v1) comme Harvester :
un VirtualMachineImageDownloader du même nom, attendu jusqu'à Ready, puis
le point de téléchargement de Harvester par le proxy de service."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))
import hv_storage as hs  # noqa: E402


def test_the_downloader_is_named_after_the_image_and_asks_for_qcow2():
    assert hs.downloader_manifest("apps", "image-x") == {
        "apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineImageDownloader",
        "metadata": {"name": "image-x", "namespace": "apps"}, "spec": {"imageName": "image-x", "compressType": "qcow2"}}
    assert hs.is_cdi({"spec": {"backend": "cdi"}}) and not hs.is_cdi({"spec": {"backend": "backingimage"}})


def test_the_downloader_is_ready_only_with_its_url():
    assert hs.downloader_state(None) == (None, "creating the downloader")
    progressing = {"status": {"status": "Progressing", "conditions": [
        {"type": "Reconciling", "message": "Waiting for the corresponding deployment to be ready"}]}}
    assert hs.downloader_state(progressing) == (None, "Waiting for the corresponding deployment to be ready")
    assert hs.downloader_state({"status": {"status": "Ready"}})[0] is None
    ready = {"status": {"status": "Ready", "downloadUrl": "http://image-x-downloader.apps/images/image-x.qcow2"}}
    assert hs.downloader_state(ready)[0] is True


def test_the_download_goes_through_harvester_s_own_endpoint():
    assert hs.cdi_download_path("apps", "image-x") == (
        "/api/v1/namespaces/harvester-system/services/https:harvester:8443/proxy/v1/harvester/"
        "harvesterhci.io.virtualmachineimages/apps/image-x/download")
    with pytest.raises(ValueError, match="image name"):
        hs.cdi_download_path("apps", "../x")


# -- commande et route -------------------------------------------------------------

import argparse  # noqa: E402
import base64  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web"))
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_cdi", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)
IMPORTED = {"type": "Imported", "status": "True"}
CDI_IMG = {"metadata": {"namespace": "apps", "name": "image-x"}, "spec": {"backend": "cdi", "displayName": "cirros lvm"},
           "status": {"storageClassName": "lvm-sc", "conditions": [IMPORTED]}}


class Kube:
    def __init__(self, img):
        self.objs = {(hs.K_IMAGE, "apps", "image-x"): img}
        self.calls, self.reads = [], 0

    def get(self, kind, ns, name):
        o = self.objs.get((kind, ns, name))
        if kind == hs.K_DOWNLOADER and o is not None:
            self.reads += 1
            if self.reads > 2:                     # le déploiement est prêt à la troisième lecture
                o = dict(o, status={"status": "Ready", "downloadUrl": "http://image-x-downloader.apps/images/image-x.qcow2"})
        return json.loads(json.dumps(o)) if o is not None else None

    def create(self, obj):
        self.calls.append(("create", obj["kind"]))
        self.objs[(hs.K_DOWNLOADER, obj["metadata"]["namespace"], obj["metadata"]["name"])] = obj
        return obj

    def raw_stream(self, path):
        self.calls.append(("stream", path))
        yield b"QFI\\xfb" + b"x" * 10


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    wait = hres._wait
    monkeypatch.setattr(hres, "_wait", lambda *a, **kw: wait(*a, **{"sleep": lambda s: None, **kw}))


def run(kube, action, tmp_path):
    a = argparse.Namespace(action=action, namespace="apps", name="image-x", out=str(tmp_path / "f.qcow2"), timeout=60,
                           cluster=None, kubeconfig="/kc")
    orig = hres.kube_from
    hres.kube_from = lambda _a: kube
    try:
        return hres.cmd_image(a)
    finally:
        hres.kube_from = orig


def test_a_cdi_image_is_downloaded_through_a_downloader(tmp_path):
    k = Kube(CDI_IMG)
    assert run(k, "download", tmp_path) == hres.EXIT_OK
    assert k.calls[0] == ("create", "VirtualMachineImageDownloader")
    assert k.calls[-1] == ("stream", hs.cdi_download_path("apps", "image-x"))
    assert (tmp_path / "f.qcow2").read_bytes().startswith(b"QFI")


def test_preparing_is_refused_before_the_import_and_a_no_op_for_longhorn(tmp_path, capsys):
    importing = dict(CDI_IMG, status={"storageClassName": "lvm-sc", "conditions": []})
    with pytest.raises(ValueError, match="not imported yet"):
        run(Kube(importing), "prepare-download", tmp_path)
    lh = dict(CDI_IMG, spec={"backend": "backingimage"})
    assert run(Kube(lh), "prepare-download", tmp_path) == hres.EXIT_OK
    assert "nothing to prepare" in capsys.readouterr().err


PW = "a long test password"


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
    monkeypatch.setattr(wapp, "_kubectl_for_cluster", lambda c: "/kc")
    wapp._accounts().create("ops", PW, "operator")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"downloader": None, "actions": []}
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: CDI_IMG if a[1] == hs.K_IMAGE else w["downloader"])

    class FakeKube:
        def __init__(self, kc):
            pass

        def raw_stream(self, path):
            w["stream"] = path
            yield b"QFI\\xfbdata"
    import kube as kube_mod
    monkeypatch.setattr(kube_mod, "Kube", FakeKube)

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        w["actions"].append((label, cmd))

        class Run:
            id = "cd0000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_route_serves_the_qcow2_only_once_prepared(world):
    with wapp.app.test_client() as c:
        assert c.get("/api/image/harv1/apps/image-x/download", headers=auth("eye")).status_code == 403
        r = c.get("/api/image/harv1/apps/image-x/download", headers=auth("ops"))
        assert r.status_code == 409 and r.get_json()["code"] == "prepare-first"
        assert c.post("/api/image/harv1/apps/image-x/do/prepare-download", json={}, headers=auth("ops")).status_code == 202
        world["downloader"] = {"status": {"status": "Ready", "downloadUrl": "http://x"}}
        r = c.get("/api/image/harv1/apps/image-x/download", headers=auth("ops"))
        assert r.status_code == 200 and r.data.startswith(b"QFI")
        assert 'filename="cirros_lvm.qcow2"' in r.headers["Content-Disposition"]
    assert world["stream"] == hs.cdi_download_path("apps", "image-x")
    (label, cmd), = world["actions"]
    assert label == "image:prepare-download:apps/image-x" and cmd[-6:] == ["--kubeconfig", "/kc", "--namespace", "apps", "--name", "image-x"][-6:]


# -- le backend d'une image nouvelle suit sa classe ------------------------------

import hv_objects as ho  # noqa: E402

LVM_SC = {"metadata": {"name": "hops-lvm"}, "provisioner": "lvm.driver.harvesterhci.io", "parameters": {"vgName": "hops-vg"}}
LH1_SC = {"metadata": {"name": "harv-rep1"}, "provisioner": "driver.longhorn.io", "parameters": {"numberOfReplicas": "1"}}
LH2_SC = {"metadata": {"name": "v2"}, "provisioner": "driver.longhorn.io", "parameters": {"dataEngine": "v2"}}


def test_an_image_on_a_class_outside_longhorn_v1_is_a_cdi_image():
    """Vu en réel sur harvlab2 : l'image demandée sur une classe LVM partait en
    backingimage ; Harvester la rangeait alors en silence dans une classe
    Longhorn (lh-...), sans volume LVM. Son interface choisit cdi hors
    Longhorn v1, comme la console le faisait déjà pour l'export."""
    spec = {"url": "http://x/c.qcow2", "display_name": "c", "storage_class": "hops-lvm"}
    assert ho.normalize("image", spec, sc_obj=LVM_SC)["spec"]["backend"] == "cdi"
    assert ho.normalize("image", dict(spec, storage_class="v2"), sc_obj=LH2_SC)["spec"]["backend"] == "cdi"
    assert ho.normalize("image", dict(spec, storage_class="harv-rep1"), sc_obj=LH1_SC)["spec"]["backend"] == "backingimage"
    assert ho.normalize("image", {"url": "http://x/c.qcow2"})["spec"]["backend"] == "backingimage"   # classe par défaut
    up = hs.upload_image("default", "c", "http://x/c", "hops-lvm", sc_obj=LVM_SC)
    assert up["spec"]["backend"] == "cdi" and up["spec"]["targetStorageClassName"] == "hops-lvm"
