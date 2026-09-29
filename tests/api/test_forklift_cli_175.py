"""v1.75.0 : l'outil harvester-forklift sur un cluster en mémoire :
installation dans l'ordre (cert-manager, add-on, contrôleur, compte
d'inventaire), refus clair sans cert-manager, reprise sans rien recréer,
image introuvable nommée."""

import argparse
import copy
import http.server
import importlib.util
import io
import json
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_forklift as hf  # noqa: E402
from kube import KubeError  # noqa: E402

_spec = importlib.util.spec_from_file_location("hfk", ROOT / "bin" / "harvester-forklift.py")
hfk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hfk)

KIND = {"Namespace": "namespaces", "Addon": hf.K_ADDON, "ForkliftController": hf.K_CONTROLLER,
        "ServiceAccount": "serviceaccounts", "ClusterRole": "clusterroles.rbac.authorization.k8s.io",
        "ClusterRoleBinding": "clusterrolebindings.rbac.authorization.k8s.io", "Secret": "secrets",
        "Provider": hf.K_PROVIDER}


class FakeKube:
    """Un cluster Harvester sans Forklift : l'add-on déployé fait apparaître
    l'opérateur et la CRD, le ForkliftController les composants."""

    def __init__(self, cert_manager=False, stuck=None):
        self.objs, self.calls, self.crd = {}, [], False
        self.run_args = []                       # full argument tuples of every kube.run(...)
        self.stuck = stuck                       # composant qui ne démarre jamais
        self.provider_status = {"phase": "Ready", "conditions": [{"type": "Ready", "status": "True"}]}
        self.refuse_secret = None                # message à lever quand un Secret est appliqué
        if cert_manager:
            self._cert_manager()

    def dep(self, ns, name, ready=True):
        self.objs[(hf.K_DEPLOY, ns, name)] = {"metadata": {"name": name, "namespace": ns}, "spec": {"replicas": 1},
                                             "status": {"availableReplicas": 1 if ready else 0}}

    def _cert_manager(self):
        for n in hf.CERT_MANAGER[1]:
            self.dep(hf.CERT_MANAGER[0], n)

    def get(self, kind, ns, name):
        if kind in (hf.K_CONTROLLER, hf.K_PROVIDER, hf.K_PLAN) and not self.crd:
            raise KubeError(f'error: the server doesn\'t have a resource type "{kind.split(".")[0]}"')
        return copy.deepcopy(self.objs.get((kind, ns, name)))

    def list(self, kind, ns=None, selector=None):
        if kind in (hf.K_PLAN,) and not self.crd:
            return []
        return [copy.deepcopy(o) for (k, n, _), o in self.objs.items() if k == kind and (ns is None or n == ns)]

    def run(self, *args, input=None, timeout=None):
        self.run_args.append(args)
        self.calls.append(("run",) + args[:2])
        if args[0] == "apply":
            self._cert_manager()
            return "applied"
        if args[:2] == ("create", "token"):
            return "tok-123\n"
        raise AssertionError(args)

    def create(self, obj):
        o = copy.deepcopy(obj)
        self.objs[(KIND[obj["kind"]], o["metadata"].get("namespace"), o["metadata"]["name"])] = o
        self.calls.append(("create", obj["kind"]))
        if obj["kind"] == "Addon":
            o["status"] = {"status": "AddonDeploySuccessful"}
            self.dep(hf.NS, hf.OPERATOR_DEPLOY)
            self.crd = True
        if obj["kind"] == "ForkliftController":
            for n in hf.COMPONENTS:
                self.dep(hf.NS, n, ready=n != self.stuck)
            if self.stuck:
                self.objs[("pods", hf.NS, self.stuck + "-x")] = {
                    "metadata": {"name": self.stuck + "-x"}, "status": {"containerStatuses": [
                        {"state": {"waiting": {"reason": "ImagePullBackOff", "message": "not found"}}}]}}
        return o

    def patch(self, kind, ns, name, patch):
        self.calls.append(("patch", kind, json.dumps(patch, sort_keys=True)))
        o = self.objs[(kind, ns, name)]
        o.setdefault("spec", {}).update(patch.get("spec") or {})
        if kind == hf.K_ADDON and o["spec"].get("enabled"):
            o["status"] = {"status": "AddonDeploySuccessful"}
            self.dep(hf.NS, hf.OPERATOR_DEPLOY)
            self.crd = True

    def apply(self, docs, field_manager="harvester-ops", timeout=None):
        if self.refuse_secret and any(d["kind"] == "Secret" for d in docs):
            raise KubeError(self.refuse_secret)
        for d in docs:
            o = copy.deepcopy(d)
            if d["kind"] == "Provider":
                o["status"] = copy.deepcopy(self.provider_status)
            self.objs[(KIND[d["kind"]], d["metadata"].get("namespace"), d["metadata"]["name"])] = o
            self.calls.append(("apply", d["kind"]))
        return ""

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))
        self.objs.pop((kind, ns, name), None)


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def install_args(**kw):
    a = dict(cluster=None, kubeconfig="kc", chart_version=hf.CHART_VERSION, image_tag=hf.IMAGE_TAG,
             cert_manager_manifest=None, timeout=900)
    a.update(kw)
    return argparse.Namespace(**a)


def run_install(kube, **kw):
    c = Clock()
    return hfk.cmd_install(install_args(**kw), kube=kube, sleep=c.sleep, now=c.now)


def test_install_refuses_without_cert_manager_and_says_where_it_comes_from(capsys):
    k = FakeKube()
    assert run_install(k) == hfk.EXIT_REFUSED
    assert "cert-manager is missing" in capsys.readouterr().err
    assert not [c for c in k.calls if c[0] == "create"]


def test_install_goes_cert_manager_addon_controller_then_inventory_access(tmp_path, capsys):
    k = FakeKube()
    cm = tmp_path / "cert-manager.yaml"
    cm.write_text("kind: List\n")
    assert run_install(k, cert_manager_manifest=str(cm)) == hfk.EXIT_OK
    order = [c[:2] for c in k.calls if c[0] in ("run", "create", "apply")]
    assert order == [("run", "apply"), ("create", "Namespace"), ("create", "Addon"), ("create", "ForkliftController"),
                     ("apply", "ServiceAccount"), ("apply", "ClusterRole"), ("apply", "ClusterRoleBinding")]
    addon = k.objs[(hf.K_ADDON, hf.NS, "forklift-operator")]
    assert json.loads(addon["spec"]["valuesContent"])["forkliftOperatorAnsible"]["forkliftOperator"]["tag"] == "v1.8.2"
    err = capsys.readouterr().err
    assert "STEP_EVENT|install|done|Forklift is installed" in err


def test_a_second_install_recreates_nothing(tmp_path):
    k = FakeKube(cert_manager=True)
    assert run_install(k) == hfk.EXIT_OK
    k.calls.clear()
    assert run_install(k) == hfk.EXIT_OK
    assert not [c for c in k.calls if c[0] == "create"]


def test_a_component_that_cannot_pull_its_image_is_named(capsys):
    k = FakeKube(cert_manager=True, stuck="forklift-controller")
    assert run_install(k, timeout=60) == hfk.EXIT_FAIL
    err = capsys.readouterr().err
    assert "forklift-controller-x: ImagePullBackOff (not found)" in err


def test_harvester_s_own_addon_is_only_enabled_never_rewritten(capsys):
    k = FakeKube(cert_manager=True)
    k.objs[(hf.K_ADDON, "forklift", "forklift-operator")] = {
        "metadata": {"name": "forklift-operator", "namespace": "forklift"},
        "spec": {"enabled": False, "repo": "http://harvester-cluster-repo.cattle-system.svc/charts",
                 "chart": "forklift-operator", "version": "1.9.1", "valuesContent": "theirs"}}
    assert run_install(k) == hfk.EXIT_OK
    patches = [c for c in k.calls if c[0] == "patch"]
    assert patches == [("patch", hf.K_ADDON, '{"spec": {"enabled": true}}')]
    spec = k.objs[(hf.K_ADDON, "forklift", "forklift-operator")]["spec"]
    assert (spec["version"], spec["valuesContent"]) == ("1.9.1", "theirs")
    assert not [c for c in k.calls if c[:2] == ("create", "Addon")]
    assert "Harvester's own forklift-operator add-on" in capsys.readouterr().err


def test_status_reads_everything_without_writing(capsys):
    k = FakeKube(cert_manager=True)
    run_install(k)
    k.calls.clear()
    assert hfk.cmd_status(argparse.Namespace(cluster=None, kubeconfig="kc"), kube=k) == hfk.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["install"]["ready"] is True and out["providers"] == []
    assert not [c for c in k.calls if c[0] in ("create", "apply", "patch", "delete")]


def test_a_bad_chart_version_on_the_command_line_is_refused(capsys):
    rc = hfk.main(["install", "--kubeconfig", "kc", "--chart-version", "latest"])
    assert rc == hfk.EXIT_REFUSED
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.startswith("STEP_EVENT|install|error|")]
    assert lines and "chart version" in lines[0]


def test_the_help_description_is_in_english():
    ap, _ = hfk.build_parser()
    assert ap.description == ("Forklift on a Harvester cluster: install, VDDK image, vCenter "
                              "provider, inventory.")


def installed():
    k = FakeKube(cert_manager=True)
    assert run_install(k) == hfk.EXIT_OK
    k.calls.clear()
    return k


def prov_args(**kw):
    a = dict(cluster=None, kubeconfig="kc", namespace="default", name="vmwlab", timeout=300, with_secret=False)
    a.update(kw)
    return argparse.Namespace(**a)


SPEC = {"url": "172.16.2.81", "user": "administrator@vsphere.local", "password": "Very-S3cret!pw",
        "insecure": True, "vddk_image": "172.16.1.11:3000/ju/vddk:8.0.3"}


def test_provider_apply_refused_while_forklift_is_not_installed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    c = Clock()
    assert hfk.cmd_provider_apply(prov_args(), kube=FakeKube(), sleep=c.sleep, now=c.now) == hfk.EXIT_REFUSED
    assert "run install first" in capsys.readouterr().err


def test_provider_apply_writes_the_secret_and_the_provider_and_waits(monkeypatch, capsys):
    k = installed()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    c = Clock()
    assert hfk.cmd_provider_apply(prov_args(), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_OK
    # l'accès à l'inventaire est déjà posé (install l'a fait) : pas reposé ici
    assert [x[:2] for x in k.calls if x[0] == "apply"] == [("apply", "Secret"), ("apply", "Provider")]
    sec = k.objs[("secrets", "default", "vmwlab-vsphere")]
    assert sec["stringData"]["password"] == "Very-S3cret!pw"
    prov = k.objs[(hf.K_PROVIDER, "default", "vmwlab")]
    assert prov["spec"]["settings"]["vddkInitImage"] == "172.16.1.11:3000/ju/vddk:8.0.3"
    captured = capsys.readouterr()
    assert "Very-S3cret" not in captured.err + captured.out
    assert "STEP_EVENT|provider|done|ready" in captured.err


def test_the_vcenter_refusal_is_said(monkeypatch, capsys):
    k = installed()
    k.provider_status = {"phase": "ConnectionFailed", "conditions": [
        {"type": "ConnectionTestFailed", "status": "True", "category": "Critical",
         "message": "Login failed: incorrect user name or password."}]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    c = Clock()
    assert hfk.cmd_provider_apply(prov_args(), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_FAIL
    assert "incorrect user name or password" in capsys.readouterr().err


def test_a_provider_used_by_a_plan_is_not_deleted(capsys):
    k = installed()
    k.objs[(hf.K_PROVIDER, "default", "vmwlab")] = {"metadata": {"name": "vmwlab", "namespace": "default"},
                                                    "spec": {"type": "vsphere"}}
    k.objs[(hf.K_PLAN, "mig", "wave-1")] = {"metadata": {"name": "wave-1", "namespace": "mig"},
                                          "spec": {"provider": {"source": {"name": "vmwlab", "namespace": "default"}}}}
    c = Clock()
    assert hfk.cmd_provider_delete(prov_args(), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_REFUSED
    assert "mig/wave-1" in capsys.readouterr().err and not [x for x in k.calls if x[0] == "delete"]


def test_delete_takes_the_secret_only_if_the_console_made_it():
    k = installed()
    k.objs[(hf.K_PROVIDER, "default", "vmwlab")] = {"metadata": {"name": "vmwlab", "namespace": "default"},
                                                    "spec": {"type": "vsphere",
                                                             "secret": {"name": "vmwlab-vsphere", "namespace": "default"}}}
    k.objs[("secrets", "default", "vmwlab-vsphere")] = {"metadata": {"labels": {"harvester-ops.io/managed": "true"}}}
    c = Clock()
    assert hfk.cmd_provider_delete(prov_args(with_secret=True), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_OK
    assert ("delete", "secrets", "vmwlab-vsphere") in k.calls
    k.objs[(hf.K_PROVIDER, "default", "vmwlab")] = {"metadata": {}, "spec": {"type": "vsphere",
                                                                          "secret": {"name": "theirs", "namespace": "default"}}}
    k.objs[("secrets", "default", "theirs")] = {"metadata": {"labels": {}}}
    assert hfk.cmd_provider_delete(prov_args(with_secret=True), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_OK
    assert ("delete", "secrets", "theirs") not in k.calls


def test_vddk_image_reads_the_registry_credentials_on_stdin(monkeypatch, capsys):
    seen = {}

    def fake_push(archive, image, **kw):
        seen.update(kw, archive=archive, image=image)
        kw["step"]("VDDK layer sent")
        return {"image": image, "pinned": "r/x@sha256:" + "d" * 64, "digest": "sha256:" + "d" * 64}
    monkeypatch.setattr(hfk.op, "push_vddk_image", fake_push)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"username": "ju", "password": "tok-secret"})))
    args = argparse.Namespace(archive="/x/vddk.tar.gz", image="172.16.1.11:3000/ju/vddk:8.0.3",
                              base=op_default(), plain_http=True, auth_stdin=True)
    assert hfk.cmd_vddk_image(args) == hfk.EXIT_OK
    assert seen["target_creds"] == {"username": "ju", "password": "tok-secret"} and seen["plain_http"] is True
    out = capsys.readouterr()
    assert json.loads(out.out)["digest"] == "sha256:" + "d" * 64 and "tok-secret" not in out.err + out.out


def op_default():
    return hfk.op.DEFAULT_BASE


def test_credentials_refused_by_forklift_s_webhook_create_no_provider(monkeypatch, capsys):
    """Le webhook de Forklift teste les identifiants à l'écriture du secret :
    un refus ne doit laisser aucun fournisseur orphelin."""
    k = installed()
    k.refuse_secret = ('Error from server: admission webhook "secrets.forklift.konveyor" '
                        'denied the request: Invalid credentials')
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    c = Clock()
    assert hfk.cmd_provider_apply(prov_args(), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_FAIL
    assert (hf.K_PROVIDER, "default", "vmwlab") not in k.objs
    captured = capsys.readouterr()
    assert "STEP_EVENT|provider|error|Invalid credentials" in captured.err
    assert "Very-S3cret" not in captured.err and "Very-S3cret" not in captured.out


def test_stdin_that_is_not_an_object_is_refused(monkeypatch, capsys):
    """Un JSON valide mais qui n'est pas un objet (liste, chaîne...) ne doit
    jamais faire planter un appelant en AttributeError sur .get()."""
    monkeypatch.setattr(hfk, "kube_from", lambda args: FakeKube())
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([1, 2])))
    rc = hfk.main(["provider-apply", "--kubeconfig", "kc", "--namespace", "default", "--name", "vc"])
    assert rc == 2
    assert "a JSON object expected" in capsys.readouterr().err


def test_inventory_uses_a_short_token_through_a_port_forward(capsys):
    k = installed()
    k.objs[(hf.K_PROVIDER, "default", "vmwlab")] = {"metadata": {"name": "vmwlab", "namespace": "default", "uid": "u-123"}}
    seen = {}

    class PF:
        def __init__(self, ns, target, port):
            seen["pf"] = (ns, target, port)

        def __enter__(self):
            return 40123

        def __exit__(self, *a):
            return False
    k.port_forward = PF

    def fetch(url, token):
        seen["url"], seen["token"] = url, token
        return [{"id": "network-12", "name": "VM Network", "path": "/vmwlab-dc/network/VM Network"}]
    args = argparse.Namespace(cluster=None, kubeconfig="kc", namespace="default", name="vmwlab", kind="networks")
    assert hfk.cmd_inventory(args, kube=k, fetch=fetch) == hfk.EXIT_OK
    assert seen["pf"] == ("forklift", "svc/forklift-inventory", 8443)
    assert seen["url"] == "https://127.0.0.1:40123/providers/vsphere/u-123/networks?detail=1"
    assert seen["token"] == "tok-123"
    assert ("create", "token", "harvester-ops-inventory", "-n", "forklift", "--duration", "10m") in k.run_args
    assert json.loads(capsys.readouterr().out) == [{"id": "network-12", "name": "VM Network",
                                                    "path": "/vmwlab-dc/network/VM Network"}]


def test_an_inventory_service_refusal_is_a_tool_error():
    """Un 403 du service d'inventaire devient une KubeError courte, jamais
    une trace Python, et ne porte jamais le jeton."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(403)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with pytest.raises(KubeError) as exc:
            hfk.fetch_json(f"http://127.0.0.1:{server.server_port}/x", "tok-SECRET")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert "403" in str(exc.value)
    assert "tok-SECRET" not in str(exc.value)


def test_an_unreachable_inventory_service_is_a_tool_error():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                     # nothing listens there any more
    with pytest.raises(KubeError) as exc:
        hfk.fetch_json(f"http://127.0.0.1:{port}/x", "tok-SECRET")
    assert "tok-SECRET" not in str(exc.value)


def test_inventory_errors_are_reported_not_raised(monkeypatch, capsys):
    """Un fetch_json qui échoue (jeton refusé, relais tombé...) ressort comme
    STEP_EVENT|inventory|error|..., pas comme une trace Python."""
    k = installed()
    k.objs[(hf.K_PROVIDER, "default", "vmwlab")] = {"metadata": {"name": "vmwlab", "namespace": "default", "uid": "u-123"}}

    class PF:
        def __init__(self, ns, target, port):
            pass

        def __enter__(self):
            return 40123

        def __exit__(self, *a):
            return False
    k.port_forward = PF
    monkeypatch.setattr(hfk, "kube_from", lambda args: k)

    def fail(url, token):
        raise KubeError("inventory service: HTTP 403 Forbidden")
    monkeypatch.setattr(hfk, "fetch_json", fail)
    rc = hfk.main(["inventory", "--kubeconfig", "kc", "--namespace", "default", "--name", "vmwlab", "--kind", "vms"])
    assert rc == hfk.EXIT_FAIL
    assert "STEP_EVENT|inventory|error|inventory service: HTTP 403 Forbidden" in capsys.readouterr().err
