"""v1.75.0, relecture finale : accès à l'inventaire compté dans l'état de
l'installation, fournisseurs jamais repris à un autre outil, cache
d'inventaire par personne, image de base VDDK surchargeable (airgap),
erreurs de l'outil sans trace Python, génération observée facultative,
compte gardé quand seul le mot de passe change, groupe de processus tué au
délai de l'inventaire, paquet Cluster API lu jusqu'au manifeste seulement."""

import argparse
import http.client
import importlib.util
import io
import json
import os
import signal
import sys
import tarfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_forklift as hf  # noqa: E402

_spec = importlib.util.spec_from_file_location("hfk_review", ROOT / "bin" / "harvester-forklift.py")
hfk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hfk)

from tests.api.test_forklift_cli_175 import FakeKube, Clock, prov_args, SPEC  # noqa: E402
from tests.api.test_forklift_routes_175 import (  # noqa: E402,F401
    world, auth, wapp, vddk_bytes, ARCHIVE)

SA = {"metadata": {"name": hf.INVENTORY_SA, "namespace": hf.NS}}


def dep(name, ready=True):
    return {"metadata": {"name": name}, "spec": {"replicas": 1}, "status": {"availableReplicas": 1 if ready else 0}}


def running_parts():
    ok_addon = {"spec": {"enabled": True}, "status": {"status": "AddonDeploySuccessful"}}
    deploys = {n: dep(n) for n in (hf.OPERATOR_DEPLOY,) + hf.COMPONENTS}
    return ok_addon, deploys, {"kind": "ForkliftController"}, {n: dep(n) for n in hf.CERT_MANAGER[1]}


def forklift_running_without_inventory_access():
    """Forklift en marche, compte d'inventaire absent : installation arrêtée
    avant sa fin (délai des composants) ou Forklift posé autrement."""
    k = FakeKube(cert_manager=True)
    k.create(hf.namespace_manifest())
    k.create(hf.addon_manifest())
    k.create(hf.controller_manifest())
    k.calls.clear()
    return k


def installed():
    k = forklift_running_without_inventory_access()
    k.apply(hf.inventory_rbac())
    k.calls.clear()
    return k


# --- 2. accès à l'inventaire -------------------------------------------------

def test_the_install_is_ready_only_with_the_inventory_access():
    st = hf.install_state(*running_parts(), None)
    assert st["running"] is True and st["inventory_access"] is False and st["ready"] is False
    st = hf.install_state(*running_parts(), SA)
    assert st["running"] is True and st["inventory_access"] is True and st["ready"] is True


def test_the_tool_reads_the_inventory_service_account(capsys):
    k = forklift_running_without_inventory_access()
    st = hfk.install_state(k)
    assert st["running"] and not st["inventory_access"] and not st["ready"]
    k.apply(hf.inventory_rbac())
    assert hfk.install_state(k)["ready"] is True


def test_provider_apply_gives_the_inventory_access_before_the_provider(monkeypatch, capsys):
    k = forklift_running_without_inventory_access()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    c = Clock()
    assert hfk.cmd_provider_apply(prov_args(), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_OK
    assert [x[1] for x in k.calls if x[0] == "apply"] == ["ServiceAccount", "ClusterRole", "ClusterRoleBinding",
                                                          "Secret", "Provider"]
    assert ("serviceaccounts", hf.NS, hf.INVENTORY_SA) in k.objs


def test_the_tab_counts_the_inventory_access_as_a_part(world, monkeypatch):
    with wapp.app.test_client() as c:
        st = c.get("/api/forklift/harvlab2", headers=auth("eye")).get_json()["install"]
    assert st["inventory_access"] is True and st["ready"] is True
    orig = wapp._kubectl_json

    def kj(kc, verb, kind, *a, **k):
        if kind == "serviceaccounts":
            return None
        return orig(kc, verb, kind, *a, **k)
    monkeypatch.setattr(wapp, "_kubectl_json", kj)
    with wapp.app.test_client() as c:
        st = c.get("/api/forklift/harvlab2", headers=auth("eye")).get_json()["install"]
    assert st["inventory_access"] is False and st["running"] is True and st["ready"] is False


def test_a_missing_inventory_account_says_to_resume_the_installation(world, monkeypatch):
    class R:
        returncode, stdout = 1, ""
        stderr = ('STEP_EVENT|inventory|error|error: failed to create token: serviceaccounts '
                  '"harvester-ops-inventory" not found\n')
    monkeypatch.setattr(wapp, "_fk_run_tool", lambda cmd, timeout: R())
    with wapp.app.test_client() as c:
        r = c.get("/api/forklift/harvlab2/inventory/vmwlab/vms", headers=auth("eye"))
    assert r.status_code == 502
    err = r.get_json()["error"]
    assert "Resume" in err and "Preparation" in err and "not found" not in err


# --- 3. fournisseurs d'un autre outil -------------------------------------------

@pytest.mark.parametrize("existing", [
    {"metadata": {"name": "vmwlab", "namespace": "default"}, "spec": {"type": "vsphere"}},
    {"metadata": {"name": "vmwlab", "namespace": "default", "labels": {hf.L_MANAGED: "true"}},
     "spec": {"type": "openshift"}},
])
def test_provider_apply_never_takes_over_a_provider_it_did_not_make(monkeypatch, capsys, existing):
    k = installed()
    k.objs[(hf.K_PROVIDER, "default", "vmwlab")] = existing
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    c = Clock()
    assert hfk.cmd_provider_apply(prov_args(), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_REFUSED
    assert not [x for x in k.calls if x[0] == "apply"]
    assert k.objs[(hf.K_PROVIDER, "default", "vmwlab")] == existing
    err = capsys.readouterr().err
    assert "STEP_EVENT|provider|error|provider default/vmwlab exists" in err


def test_forklift_s_own_host_provider_is_never_rewritten(monkeypatch, capsys):
    k = installed()
    host = {"metadata": {"name": "host", "namespace": hf.NS}, "spec": {"type": "openshift"}}
    k.objs[(hf.K_PROVIDER, hf.NS, "host")] = host
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    c = Clock()
    rc = hfk.cmd_provider_apply(prov_args(namespace=hf.NS, name="host"), kube=k, sleep=c.sleep, now=c.now)
    assert rc == hfk.EXIT_REFUSED and k.objs[(hf.K_PROVIDER, hf.NS, "host")] == host


def test_provider_apply_changes_a_provider_it_made(monkeypatch):
    k = installed()
    k.objs[(hf.K_PROVIDER, "default", "vmwlab")] = {
        "metadata": {"name": "vmwlab", "namespace": "default", "labels": {hf.L_MANAGED: "true"}},
        "spec": {"type": "vsphere"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    c = Clock()
    assert hfk.cmd_provider_apply(prov_args(), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_OK
    prov = k.objs[(hf.K_PROVIDER, "default", "vmwlab")]
    assert prov["spec"]["settings"]["vddkInitImage"] == SPEC["vddk_image"]


def test_provider_delete_refuses_a_provider_that_is_not_a_vcenter(capsys):
    k = installed()
    k.objs[(hf.K_PROVIDER, hf.NS, "host")] = {"metadata": {"name": "host", "namespace": hf.NS},
                                             "spec": {"type": "openshift"}}
    c = Clock()
    rc = hfk.cmd_provider_delete(prov_args(namespace=hf.NS, name="host", with_secret=True),
                                 kube=k, sleep=c.sleep, now=c.now)
    assert rc == hfk.EXIT_REFUSED and not [x for x in k.calls if x[0] == "delete"]
    assert "not a vCenter" in capsys.readouterr().err


def test_a_new_source_with_a_name_already_taken_is_refused(world):
    spec = {"name": "vmwlab", "url": "vc2.lan", "user": "u@vsphere.local", "password": "N3w-S3cret", "insecure": True}
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply", json={"spec": spec}, headers=auth("adm"))
        assert r.status_code == 409
        assert "already" in r.get_json()["error"] and "N3w-S3cret" not in r.get_data(as_text=True)
        vmi = c.post("/api/forklift/harvlab2/do/provider-apply",
                     json={"spec": {"name": "vmwlab", "from_vmimport": {"namespace": "mig", "name": "vc"}}},
                     headers=auth("adm"))
        assert vmi.status_code == 409
    assert world["actions"] == []
    with wapp.app.test_client() as c:
        # une modification (keep_credentials, ou le namespace du fournisseur) reste permise
        assert c.post("/api/forklift/harvlab2/do/provider-apply",
                      json={"spec": {**spec, "keep_credentials": True}}, headers=auth("adm")).status_code == 202
        assert c.post("/api/forklift/harvlab2/do/provider-apply",
                      json={"spec": {**spec, "namespace": "forklift"}}, headers=auth("adm")).status_code == 202


# --- 4. cache d'inventaire par personne --------------------------------------

def test_two_people_never_share_a_cached_inventory(world, monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, json.dumps([{"name": "vm"}]), ""

    def run(cmd, **kw):
        calls.append(cmd)
        return R()
    monkeypatch.setattr(wapp, "_fk_run_tool", lambda cmd, timeout: run(cmd))
    with wapp.app.test_client() as c:
        assert c.get("/api/forklift/harvlab2/inventory/vmwlab/vms", headers=auth("eye")).status_code == 200
        assert c.get("/api/forklift/harvlab2/inventory/vmwlab/vms", headers=auth("adm")).status_code == 200
        assert c.get("/api/forklift/harvlab2/inventory/vmwlab/vms", headers=auth("adm")).status_code == 200
    assert len(calls) == 2


# --- 5. image de base VDDK surchargeable --------------------------------------

def push(c, **extra):
    return c.post("/api/forklift/harvlab2/do/vddk-image",
                  json={"archive": ARCHIVE, "image": "172.16.1.11:5005/harvops/vddk:8.0.3", **extra},
                  headers=auth("adm"))


def test_the_vddk_base_image_can_come_from_a_mirror(world, monkeypatch):
    with wapp.app.test_client() as c:
        assert c.put(f"/api/forklift-vddk/{ARCHIVE}", data=vddk_bytes(), headers=auth("adm")).status_code == 201
        monkeypatch.delenv("HARVESTER_OPS_VDDK_BASE", raising=False)
        assert push(c).status_code == 202
        monkeypatch.setenv("HARVESTER_OPS_VDDK_BASE", "mirror.lan:5000/bci/bci-busybox:16.0")
        assert push(c).status_code == 202
        monkeypatch.setenv("HARVESTER_OPS_VDDK_BASE", "not an image")
        bad = push(c)
        assert bad.status_code == 400 and "HARVESTER_OPS_VDDK_BASE" in bad.get_json()["error"]
    first, second = world["actions"][0][1], world["actions"][1][1]
    assert "--base" not in first
    assert second[second.index("--base") + 1] == "mirror.lan:5000/bci/bci-busybox:16.0"
    assert len(world["actions"]) == 2


# --- 7. erreurs de l'outil ------------------------------------------------------

@pytest.mark.parametrize("exc", [
    FileNotFoundError(2, "No such file or directory", "/secret/place/VMware-vix-disklib-8.0.3-1.x86_64.tar.gz"),
    tarfile.ReadError("not a gzip file"),
    EOFError("Compressed file ended before the end-of-stream marker was reached"),
    http.client.RemoteDisconnected("Remote end closed connection without response"),
    http.client.IncompleteRead(b"abc", 10),
])
def test_tool_errors_are_steps_not_tracebacks(monkeypatch, capsys, exc):
    def boom(*a, **kw):
        raise exc
    monkeypatch.setattr(hfk.op, "push_vddk_image", boom)
    rc = hfk.main(["vddk-image", "--archive", "/x/a.tar.gz", "--image", "r.lan/harvops/vddk:1"])
    assert rc == hfk.EXIT_FAIL
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.startswith("STEP_EVENT|vddk-image|error|")]
    assert lines and len(lines[0]) > len("STEP_EVENT|vddk-image|error|") + 3
    assert "Traceback" not in err and "/secret/place" not in err


# --- 8. génération observée facultative -------------------------------------

def test_a_provider_without_observed_generation_is_read_as_is():
    ready = [{"type": "Ready", "status": "True"}]
    assert hf.provider_state({"metadata": {"generation": 2}, "status": {"conditions": ready}})[0] is True
    stale = {"metadata": {"generation": 2}, "status": {"observedGeneration": 1, "conditions": ready}}
    assert hf.provider_state(stale)[0] is None


# --- 9. compte gardé quand seul le mot de passe change ------------------------

def test_a_new_password_alone_keeps_the_stored_account(world):
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply",
                   json={"spec": {"name": "vmwlab", "url": "https://vmwlab-vc.home.lo/sdk", "keep_credentials": True,
                                  "user": "", "password": "N3w-S3cret"}}, headers=auth("adm"))
    assert r.status_code == 202, r.get_json()
    _, _, sent, _ = world["actions"][0]
    assert (sent["user"], sent["password"]) == ("administrator@vsphere.local", "N3w-S3cret")
    assert sent["insecure"] is True                     # le réglage TLS du secret, toujours gardé


# --- 10. délai de l'inventaire : tout le groupe tombe -------------------------

def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


def test_an_inventory_timeout_kills_the_tool_and_its_port_forward(world, monkeypatch, tmp_path):
    pidfile = tmp_path / "child.pid"
    script = ("import subprocess, time\n"
              "p = subprocess.Popen(['sleep', '300'])\n"
              f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
              "time.sleep(300)\n")
    monkeypatch.setattr(wapp, "_fk_cmd", lambda action, kc: [sys.executable, "-c", script])
    monkeypatch.setattr(wapp, "_FK_INVENTORY_TIMEOUT", 2)
    pid = None
    try:
        with wapp.app.test_client() as c:
            r = c.get("/api/forklift/harvlab2/inventory/vmwlab/vms", headers=auth("eye"))
        assert r.status_code == 502 and "did not answer in time" in r.get_json()["error"]
        pid = int(pidfile.read_text())
        deadline = time.time() + 5
        while alive(pid) and time.time() < deadline:
            time.sleep(0.1)
        assert not alive(pid), "the tool's child (its port-forward) outlived the timeout"
    finally:
        if pid and alive(pid):
            os.kill(pid, signal.SIGKILL)


# --- 11. paquet Cluster API lu jusqu'au manifeste ------------------------------

def test_the_bundle_is_read_only_up_to_the_cert_manager_manifest(tmp_path):
    """Le paquet réel pèse 442 Mo : il n'est pas décompressé en entier. La
    preuve : une fin de paquet abîmée n'empêche pas de lire le manifeste,
    placé avant."""
    p = tmp_path / "capi-bundle-big.tar.gz"
    with tarfile.open(p, "w:gz") as tar:
        for name, data in (("capi-bundle/manifests/cert-manager/cert-manager.yaml", b"kind: Namespace\n"),
                           ("capi-bundle/images/big.tar", os.urandom(2 << 20))):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    raw = p.read_bytes()
    p.write_bytes(raw[: len(raw) // 2])
    assert Path(hfk.bundle_cert_manager(p, tmp_path)).read_text() == "kind: Namespace\n"


def test_a_bundle_cut_before_the_manifest_is_refused_clearly(tmp_path):
    p = tmp_path / "capi-bundle-cut.tar.gz"
    with tarfile.open(p, "w:gz") as tar:
        for name, data in (("capi-bundle/images/big.tar", os.urandom(1 << 20)),
                           ("capi-bundle/manifests/cert-manager/cert-manager.yaml", b"kind: Namespace\n")):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    raw = p.read_bytes()
    p.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(ValueError, match="cannot be read"):
        hfk.bundle_cert_manager(p, tmp_path)


def test_the_tool_parser_still_builds():
    ap, _ = hfk.build_parser()
    assert isinstance(ap, argparse.ArgumentParser)
