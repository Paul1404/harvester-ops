"""v1.75.0 : l'outil harvester-forklift sur un cluster en mémoire :
installation dans l'ordre (cert-manager, add-on, contrôleur, compte
d'inventaire), refus clair sans cert-manager, reprise sans rien recréer,
image introuvable nommée."""

import argparse
import copy
import importlib.util
import io
import json
import sys
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
        self.stuck = stuck                       # composant qui ne démarre jamais
        self.provider_status = {"phase": "Ready", "conditions": [{"type": "Ready", "status": "True"}]}
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
