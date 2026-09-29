"""v1.75.0 : ce que l'onglet Migrations VMware demande à la bibliothèque et à
l'outil : image VDDK retenue par le cluster, registre proposé d'après
containerd-registry, demande reprise d'une source VM Import (mot de passe lu
côté serveur), secrets par fichier privé, cert-manager tiré du paquet
Cluster API de la console."""

import argparse
import base64
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_forklift as hf  # noqa: E402

_spec = importlib.util.spec_from_file_location("hfk_ui", ROOT / "bin" / "harvester-forklift.py")
hfk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hfk)

# Import helpers from test_forklift_cli_175 for cmd_install testing
from tests.api.test_forklift_cli_175 import FakeKube, Clock, install_args, run_install  # noqa: E402

REG = json.dumps({"Mirrors": {"172.16.1.11:5005": {"Endpoints": ["http://172.16.1.11:5005"]}},
                  "Configs": {"172.16.1.11:5005": {"Auth": {"Username": "harvops", "Password": "reg-S3cret"}}}})
B64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731


def test_the_archive_name_gives_the_vddk_version():
    assert hf.check_archive_name("VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz") == "8.0.3"
    for bad in ("vddk.tar.gz", "../VMware-vix-disklib-8.0.3-1.x86_64.tar.gz", "VMware-vix-disklib-8.0.3-1.x86_64.tgz", "",
                # un `.match` laisse passer un saut de ligne final à cause du `$` : `.fullmatch` le refuse
                "VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz\n"):
        with pytest.raises(ValueError):
            hf.check_archive_name(bad)


def test_the_pushed_image_is_remembered_by_the_cluster():
    cm = hf.vddk_record_manifest("172.16.1.11:5005/harvops/vddk:8.0.3", "sha256:" + "a" * 64,
                                 "VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz", "2026-09-29T10:00:00Z")
    assert (cm["kind"], cm["metadata"]["namespace"], cm["metadata"]["name"]) == ("ConfigMap", "forklift", hf.VDDK_CM)
    assert cm["metadata"]["labels"][hf.L_MANAGED] == "true"
    assert hf.vddk_record(cm) == {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "digest": "sha256:" + "a" * 64,
                                  "archive": "VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz",
                                  "pushed_at": "2026-09-29T10:00:00Z"}
    assert hf.vddk_record(None) is None and hf.vddk_record({"data": {}}) is None
    with pytest.raises(ValueError):
        hf.vddk_record_manifest("not an image", "d", "a", "t")


def test_the_registry_is_proposed_from_harvester_s_containerd_registry():
    h = hf.registry_hint(REG, "VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz")
    assert h == {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "host": "172.16.1.11:5005",
                 "plain_http": True, "auth": True}
    assert "reg-S3cret" not in json.dumps(h)
    assert hf.registry_auth(REG, "172.16.1.11:5005") == {"username": "harvops", "password": "reg-S3cret"}
    assert hf.registry_auth(REG, "other:5000") is None
    tls = json.dumps({"Mirrors": {"docker.io": {"Endpoints": ["https://mirror.lan"]}}})
    assert hf.registry_hint(tls, "x") == {"image": "mirror.lan/harvops/vddk:latest", "host": "mirror.lan",
                                          "plain_http": False, "auth": False}
    for empty in ("", None, "{}", "not json"):
        assert hf.registry_hint(empty) == {"image": "", "host": "", "plain_http": False, "auth": False}
    assert hf.registry_hint(12) == {"image": "", "host": "", "plain_http": False, "auth": False}


def test_a_vcenter_of_vm_import_becomes_a_provider_request_without_the_browser():
    src = {"metadata": {"namespace": "mig", "name": "vc"},
           "spec": {"endpoint": "https://vmwlab-vc.home.lo/sdk", "dc": "vmwlab-dc",
                    "credentials": {"name": "vc-creds", "namespace": "mig"}}}
    sec = {"data": {"username": B64("administrator@vsphere.local"), "password": B64("Very-S3cret!pw")}}
    spec = hf.spec_from_vmimport(src, sec, "r/vddk:8.0.3")
    assert spec == {"url": "https://vmwlab-vc.home.lo/sdk", "user": "administrator@vsphere.local",
                    "password": "Very-S3cret!pw", "insecure": True, "vddk_image": "r/vddk:8.0.3"}
    sec["data"]["caCert"] = B64("-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----")
    spec = hf.spec_from_vmimport(src, sec)
    assert "insecure" not in spec and spec["cacert"].startswith("-----BEGIN CERTIFICATE-----")
    with pytest.raises(ValueError):
        hf.spec_from_vmimport(src, {"data": {}})
    assert hf.secret_values(sec, "username", "nope") == {"username": "administrator@vsphere.local"}


class Kube:
    def __init__(self):
        self.applied = []

    def apply(self, docs, **kw):
        self.applied.extend(docs)
        return ""


def fake_push(seen):
    def push(archive, image, **kw):
        seen.update(kw, archive=archive, image=image)
        return {"image": image, "pinned": "r/x@sha256:" + "d" * 64, "digest": "sha256:" + "d" * 64}
    return push


def test_vddk_image_reads_its_credentials_from_a_private_file_and_records_the_image(monkeypatch, tmp_path, capsys):
    seen, kube = {}, Kube()
    monkeypatch.setattr(hfk.op, "push_vddk_image", fake_push(seen))
    monkeypatch.setattr(hfk, "kube_from", lambda args: kube)
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"username": "harvops", "password": "reg-S3cret"}))
    args = argparse.Namespace(archive="/x/VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz",
                              image="172.16.1.11:5005/harvops/vddk:8.0.3", base=hfk.op.DEFAULT_BASE,
                              plain_http=True, auth_stdin=False, spec=str(spec), cluster=None, kubeconfig="/kc")
    assert hfk.cmd_vddk_image(args) == hfk.EXIT_OK
    assert seen["target_creds"] == {"username": "harvops", "password": "reg-S3cret"}
    kinds = [(d["kind"], d["metadata"]["name"]) for d in kube.applied]
    assert kinds == [("Namespace", "forklift"), ("ConfigMap", hf.VDDK_CM)]
    assert kube.applied[1]["data"]["archive"] == "VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz"
    out = capsys.readouterr()
    assert "reg-S3cret" not in out.out + out.err


def test_vddk_image_without_a_cluster_records_nothing(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(hfk.op, "push_vddk_image", fake_push(seen))
    monkeypatch.setattr(hfk, "kube_from", lambda args: pytest.fail("no cluster was given"))
    args = argparse.Namespace(archive="/x/v.tar.gz", image="r.lan/harvops/vddk:8.0.3", base=hfk.op.DEFAULT_BASE,
                              plain_http=False, auth_stdin=False, spec=None, cluster=None, kubeconfig=None)
    assert hfk.cmd_vddk_image(args) == hfk.EXIT_OK and seen["target_creds"] is None


def test_provider_apply_takes_its_request_from_a_private_file(monkeypatch, tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"url": "vc.lan", "user": "u", "password": "p"}))
    assert hfk.read_json_input(argparse.Namespace(spec=str(f))) == {"url": "vc.lan", "user": "u", "password": "p"}
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"url": "stdin"}'))
    assert hfk.read_json_input(argparse.Namespace(spec=None)) == {"url": "stdin"}
    f.write_text("[1]")
    with pytest.raises(ValueError):
        hfk.read_json_input(argparse.Namespace(spec=str(f)))


def bundle(tmp_path, with_cm=True):
    p = tmp_path / "capi-bundle-x.tar.gz"
    with tarfile.open(p, "w:gz") as tar:
        for name, data in ((("capi-bundle/manifest.json", b"{}"),) +
                           ((("capi-bundle/manifests/cert-manager/cert-manager.yaml", b"kind: Namespace\n"),) if with_cm else ())):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    return p


def test_cert_manager_comes_from_the_console_s_cluster_api_bundle(tmp_path):
    out = hfk.bundle_cert_manager(bundle(tmp_path), tmp_path)
    assert Path(out).read_text() == "kind: Namespace\n"
    with pytest.raises(ValueError, match="no cert-manager manifest"):
        hfk.bundle_cert_manager(bundle_no_cm(tmp_path), tmp_path)


def bundle_no_cm(tmp_path):
    d = tmp_path / "nocm"
    d.mkdir()
    return bundle(d, with_cm=False)


def test_the_new_options_are_on_the_command_line():
    ap, _ = hfk.build_parser()
    a = ap.parse_args(["install", "--kubeconfig", "/kc", "--cert-manager-from-bundle", "/b.tar.gz"])
    assert a.cert_manager_from_bundle == "/b.tar.gz"
    a = ap.parse_args(["provider-apply", "--kubeconfig", "/kc", "--namespace", "forklift", "--name", "vc", "--spec", "/s"])
    assert a.spec == "/s"
    a = ap.parse_args(["vddk-image", "--archive", "/a", "--image", "r/x:1", "--spec", "/s", "--kubeconfig", "/kc"])
    assert (a.spec, a.kubeconfig) == ("/s", "/kc")


def test_install_cert_manager_from_bundle_success(tmp_path, monkeypatch):
    """cert-manager absent + paquet valide = le manifeste est appliqué et le
    répertoire temporaire est nettoyé."""
    k = FakeKube()
    created_dirs = []

    original_tempdir = hfk.tempfile.TemporaryDirectory

    class TrackingTempDir:
        def __init__(self, prefix=""):
            self._impl = original_tempdir(prefix=prefix)
            created_dirs.append(self._impl.name)

        def __getattr__(self, name):
            return getattr(self._impl, name)

        def cleanup(self):
            self._impl.cleanup()

    monkeypatch.setattr(hfk.tempfile, "TemporaryDirectory", TrackingTempDir)

    b = bundle(tmp_path)
    rc = run_install(k, cert_manager_from_bundle=str(b))
    assert rc == hfk.EXIT_OK
    assert len(created_dirs) == 1
    assert not Path(created_dirs[0]).exists(), f"temp dir {created_dirs[0]} was not cleaned up"


def test_install_cert_manager_from_bundle_no_manifest(tmp_path, monkeypatch, capsys):
    """cert-manager absent + paquet sans manifeste = refus et le répertoire
    temporaire est nettoyé malgré l'erreur."""
    k = FakeKube()
    created_dirs = []

    original_tempdir = hfk.tempfile.TemporaryDirectory

    class TrackingTempDir:
        def __init__(self, prefix=""):
            self._impl = original_tempdir(prefix=prefix)
            created_dirs.append(self._impl.name)

        def __getattr__(self, name):
            return getattr(self._impl, name)

        def cleanup(self):
            self._impl.cleanup()

    monkeypatch.setattr(hfk.tempfile, "TemporaryDirectory", TrackingTempDir)

    b = bundle_no_cm(tmp_path)
    rc = run_install(k, cert_manager_from_bundle=str(b))
    assert rc == hfk.EXIT_REFUSED
    assert "no cert-manager manifest" in capsys.readouterr().err
    assert len(created_dirs) == 1
    assert not Path(created_dirs[0]).exists(), f"temp dir {created_dirs[0]} was not cleaned up after error"


def test_harvester_1_9_keeps_registry_credentials_in_a_secret_named_by_the_local_cluster():
    """Vu en réel sur harvlab2 : Auth est null dans le réglage, le secret est
    nommé par spec.rkeConfig.registries.configs.<hôte>.authConfigSecretName."""
    reg = json.dumps({"Configs": {"172.16.1.11:5005": {"Auth": None}},
                      "Mirrors": {"172.16.1.11:5005": {"Endpoints": ["http://172.16.1.11:5005"]}}})
    prov = {"spec": {"rkeConfig": {"registries": {"configs": {
        "172.16.1.11:5005": {"authConfigSecretName": "harvester-containerd-registry-4041e0afc4370bdc"}}}}}}
    assert hf.registry_auth(reg, "172.16.1.11:5005") is None
    assert hf.registry_auth_secret(prov, "172.16.1.11:5005") == "harvester-containerd-registry-4041e0afc4370bdc"
    assert hf.registry_auth_secret(prov, "other:5000") == "" and hf.registry_auth_secret(None, "x") == ""
    assert hf.registry_hint(reg, "x")["auth"] is False
    assert hf.registry_hint(reg, "x", prov)["auth"] is True


def test_a_changed_provider_is_not_ready_before_forklift_reads_the_change():
    """Vu en réel : après une modification, Ready restait vrai (version
    précédente) et l'action disait « prêt » pendant que Forklift revérifiait."""
    ready = [{"type": "Ready", "status": "True"}]
    stale = {"metadata": {"generation": 2}, "status": {"observedGeneration": 1, "phase": "Staging", "conditions": ready}}
    assert hf.provider_state(stale)[0] is None and "not read yet" in hf.provider_state(stale)[1]
    fresh = {"metadata": {"generation": 2}, "status": {"observedGeneration": 2, "phase": "Ready", "conditions": ready}}
    assert hf.provider_state(fresh)[0] is True
    assert hf.provider_state({"status": {"conditions": ready}})[0] is True        # sans génération : comme avant
