"""v1.71.0 : imports de VM côté commande et côté routes : identifiants
changés en Secret (jamais dans la source, ni dans une réponse, ni sur une
ligne de commande), source recréée pour être revérifiée, source protégée
tant qu'un import s'en sert, import suivi jusqu'à la VM en marche avec la
progression des images, raison d'un blocage lue dans le journal du
contrôleur, écritures réservées aux administrateurs."""

import argparse
import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_vmimport as vi  # noqa: E402
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_vi", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)
PW = "a long test password"
KINDS = {"Secret": "secrets", "VirtualMachineImport": vi.K_IMPORT, **{vi.KIND[t]: vi.K_SRC[t] for t in vi.TYPES}}


class Kube:
    """Un cluster en mémoire. Le contrôleur est simulé : une source neuve
    devient prête (ou pas) à la relecture ; un import avance d'une phase à
    chaque relecture et son image progresse."""

    def __init__(self, objs=None, source_ok=True, phases=None, log=""):
        self.objs = dict(objs or {})
        self.calls, self.source_ok, self.log = [], source_ok, log
        self.phases = list(phases if phases is not None else vi.PHASES[1:])

    def get(self, kind, ns, name):
        o = self.objs.get((kind, ns, name))
        if o is not None and kind in vi.K_SRC.values() and not o.get("status"):
            o["status"] = {"status": "clusterReady" if self.source_ok else "clusterNotReady"}
        if o is not None and kind == vi.K_IMPORT and self.phases:
            ph = self.phases.pop(0)
            st = o.setdefault("status", {})
            st["importStatus"] = ph
            if ph == "diskImageSubmitted":
                st["diskImportStatus"] = [{"diskName": "a-d1.img", "diskSize": 1, "VirtualMachineImage": "image-1"}]
                self.objs[(vi.K_IMAGE, ns, "image-1")] = {"metadata": {"namespace": ns, "name": "image-1",
                                                                       "labels": {vi.L_IMPORTED: "true"}}, "status": {"progress": 50}}
            if ph == vi.DONE:
                st["importedVirtualMachineName"] = o["spec"]["virtualMachineName"].lower()
        return json.loads(json.dumps(o)) if o is not None else None

    def list(self, kind, ns=None, selector=None):
        return [json.loads(json.dumps(o)) for (k, n, _), o in self.objs.items() if k == kind and (ns is None or n == ns)]

    def create(self, obj):
        self.calls.append(("create", obj["kind"], obj["metadata"]["name"]))
        self.objs[(KINDS[obj["kind"]], obj["metadata"]["namespace"], obj["metadata"]["name"])] = json.loads(json.dumps(obj))
        return obj

    def patch(self, kind, ns, name, patch):
        self.calls.append(("patch", kind, name))

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))
        self.objs.pop((kind, ns, name), None)

    def run(self, *args, input=None, timeout=None):
        self.calls.append(("run",) + args[:3])
        return self.log


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    wait = hres._wait
    monkeypatch.setattr(hres, "_wait", lambda *a, **kw: wait(*a, **{"sleep": lambda s: None, **kw}))


def run(kube, tmp_path, action, spec=None, **kw):
    path = None
    if spec is not None:
        path = tmp_path / "spec.json"
        path.write_text(json.dumps(spec))
    a = argparse.Namespace(**{"action": action, "spec": str(path) if path else None, "type": None, "namespace": None,
                              "name": None, "with_secret": False, "timeout": 600, **kw})
    orig = hres.kube_from
    hres.kube_from = lambda _a: kube
    try:
        return hres.cmd_vmimport(a)
    finally:
        hres.kube_from = orig


VC = {"type": "vmware", "namespace": "mig", "name": "vc", "endpoint": "https://vc/sdk", "dc": "DC1",
      "credentials": {"mode": "new", "values": {"username": "admin", "password": "s3cr3t"}}}


def test_a_source_gets_its_secret_and_is_checked(tmp_path, capsys):
    k = Kube()
    assert run(k, tmp_path, "source-apply", VC) == hres.EXIT_OK
    sec = k.objs[("secrets", "mig", "vc-creds")]
    assert base64.b64decode(sec["data"]["password"]).decode() == "s3cr3t"
    assert "s3cr3t" not in json.dumps(k.objs[(vi.K_SRC["vmware"], "mig", "vc")])
    assert "ready: the controller reached it" in capsys.readouterr().err
    assert run(k, tmp_path, "source-recheck", type="vmware", namespace="mig", name="vc") == hres.EXIT_OK
    assert ("delete", vi.K_SRC["vmware"], "vc") in k.calls          # recréée : seule façon de la faire revérifier


def test_a_source_that_cannot_be_reached_says_why_from_the_controller_log(tmp_path, capsys):
    log = 'time="x" level=error msg="Failed to verify source for migration" name=vc err="ServerFaultCode: Cannot complete login"\n'
    assert run(Kube(source_ok=False, log=log), tmp_path, "source-apply", VC) == hres.EXIT_FAIL
    err = capsys.readouterr().err
    assert "not ready: ServerFaultCode: Cannot complete login" in err and "level=error" not in err


def test_a_source_used_by_an_import_in_progress_is_kept(tmp_path):
    k = Kube(phases=[])
    run(k, tmp_path, "source-apply", VC)
    k.objs[(vi.K_IMPORT, "apps", "imp")] = {"metadata": {"namespace": "apps", "name": "imp"},
                                           "spec": {"sourceCluster": {"kind": "VmwareSource", "name": "vc", "namespace": "mig"}},
                                           "status": {"importStatus": "diskImageSubmitted"}}
    with pytest.raises(ValueError, match="used by imports in progress: apps/imp"):
        run(k, tmp_path, "source-delete", type="vmware", namespace="mig", name="vc")
    k.objs[(vi.K_IMPORT, "apps", "imp")]["status"]["importStatus"] = vi.DONE
    assert run(k, tmp_path, "source-delete", type="vmware", namespace="mig", name="vc", with_secret=True) == hres.EXIT_OK
    assert ("secrets", "mig", "vc-creds") not in k.objs                # le secret créé par la console part avec


def test_refused_credentials_are_said_without_waiting_out_the_timeout(tmp_path, capsys, monkeypatch):
    """Vu contre vcsim : un mot de passe refusé laisse la source sans état,
    le contrôleur réessaie sans fin ; le journal le dit après 15 s."""
    log = ('time="x" level=error msg="error syncing \'mig/vc\': handler vmware-source-change: failed to generate client" '
           'name=vc err="ServerFaultCode: Cannot complete login due to an incorrect user name or password."\n')

    class Silent(Kube):
        def get(self, kind, ns, name):
            o = self.objs.get((kind, ns, name))
            return json.loads(json.dumps(o)) if o is not None else None     # jamais d'état
    clock = iter(range(0, 10000, 5))
    monkeypatch.setattr(hres.time, "time", lambda: next(clock))
    assert run(Silent(log=log), tmp_path, "source-apply", VC) == hres.EXIT_FAIL
    err = capsys.readouterr().err
    assert "never checked: ServerFaultCode: Cannot complete login" in err and "not finished after" not in err


OVA = {"type": "ova", "namespace": "mig", "name": "ova1", "url": "http://10.0.0.1/a.ova"}
IMP = {"namespace": "mig", "name": "imp", "vm_name": "Cirros", "source": {"type": "ova", "namespace": "mig", "name": "ova1"},
       "networks": [{"source": "VM Network", "destination": "production"}]}


def test_an_import_is_followed_to_the_running_vm(tmp_path, capsys):
    k = Kube()
    k.objs[("network-attachment-definitions.k8s.cni.cncf.io", "mig", "production")] = {"metadata": {"namespace": "mig", "name": "production"}}
    run(k, tmp_path, "source-apply", OVA)
    capsys.readouterr()
    assert run(k, tmp_path, "import-create", IMP) == hres.EXIT_OK
    err = capsys.readouterr().err
    assert "import mig/imp created: VM cirros" in err and "diskImageSubmitted (50 %)" in err and "VM cirros imported and running" in err


def test_an_import_is_refused_where_the_controller_would_loop(tmp_path):
    k = Kube()
    run(k, tmp_path, "source-apply", OVA)
    with pytest.raises(ValueError, match="no VM network mig/production"):
        run(k, tmp_path, "import-create", IMP)
    k.objs[("network-attachment-definitions.k8s.cni.cncf.io", "mig", "production")] = {"metadata": {"namespace": "mig", "name": "production"}}
    k.objs[("virtualmachines.kubevirt.io", "mig", "cirros")] = {"metadata": {"namespace": "mig", "name": "cirros"}}
    with pytest.raises(ValueError, match="a VM mig/cirros already exists"):
        run(k, tmp_path, "import-create", IMP)


def test_a_stuck_import_is_ended_with_the_controller_s_reason(tmp_path, capsys, monkeypatch):
    log = ('time="x" level=error msg="error syncing" key=mig/imp '
           'err="admission webhook denied the request: displayName is not a valid Kubernetes label value"\n')
    k = Kube(phases=["virtualMachineImportValid", "sourceReady"] + ["disksExported"] * 50, log=log)
    k.objs[(vi.K_IMPORT, "mig", "imp")] = {"metadata": {"namespace": "mig", "name": "imp"},
                                          "spec": {"virtualMachineName": "a", "sourceCluster": {"kind": "OvaSource", "name": "o"}}}
    clock = iter(range(0, 100000, 60))
    assert hres._vmi_follow(k, "mig", "imp", 10000, sleep=lambda s: None, now=lambda: next(clock)) == hres.EXIT_FAIL
    assert "displayName is not a valid Kubernetes label value" in capsys.readouterr().err


# -- routes ------------------------------------------------------------------------

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
        files = {cmd[i + 1]: Path(cmd[i + 1]).read_text() for i, a in enumerate(cmd) if a == "--spec"}
        w["actions"].append((label, cmd, files))
        if after:
            after()

        class Run:
            id = "vi0000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    objs = {"addons.harvesterhci.io": {"metadata": {"namespace": "harvester-system", "name": "vm-import-controller"},
                                       "spec": {"enabled": True}, "status": {"status": "AddonDeploySuccessful"}},
            vi.K_SRC["ova"]: {"items": [{"metadata": {"namespace": "mig", "name": "ova1"}, "spec": {"url": "http://x/a.ova"},
                                         "status": {"status": "clusterReady"}}]},
            vi.K_SRC["vmware"]: {"items": [{"metadata": {"namespace": "mig", "name": "vc"},
                                            "spec": {"endpoint": "https://vc/sdk", "dc": "DC1", "credentials": {"name": "vc-creds", "namespace": "mig"}}}]},
            vi.K_IMPORT: {"items": [{"metadata": {"namespace": "mig", "name": "imp", "creationTimestamp": "2026-09-27T10:00:00Z"},
                                     "spec": {"virtualMachineName": "a", "sourceCluster": {"kind": "OvaSource", "name": "ova1", "namespace": "mig"}},
                                     "status": {"importStatus": "diskImageSubmitted", "diskImportStatus": [
                                         {"diskName": "a-d.img", "diskSize": 5, "VirtualMachineImage": "image-1"}]}}]},
            vi.K_IMAGE: {"items": [{"metadata": {"namespace": "mig", "name": "image-1"}, "status": {"progress": 73}}]},
            "network-attachment-definitions.k8s.cni.cncf.io": {"items": [{"metadata": {"namespace": "default", "name": "production"}}]},
            "storageclasses": {"items": [{"metadata": {"name": "harv-rep1", "annotations": {"storageclass.kubernetes.io/is-default-class": "true"}}},
                                         {"metadata": {"name": "vmstate-persistence"}, "parameters": {"harvesterhci.io/isInternalStorageClass": "true"}}]},
            "namespaces": {"items": [{"metadata": {"name": "mig"}}]}}
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: objs.get(a[1]))

    class R:
        returncode, stdout = 0, 'level=error msg="x" name=vc err="ServerFaultCode: Cannot complete login due to an incorrect user name or password"\n'
    monkeypatch.setattr(wapp, "_kubectl_run", lambda *a, **k: R())
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_section_reads_sources_and_imports_with_their_progress(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/vmimport/harv1", headers=auth("eye")).get_json()
    assert d["addon"]["enabled"] and d["crds"]
    ova, vc = sorted(d["sources"], key=lambda r: r["type"])
    assert (ova["state"], ova["users"]) == ("ready", ["mig/imp"]) and vc["state"] == "pending" and vc["secret"] == "vc-creds"
    [imp] = d["imports"]
    assert imp["disks"][0]["progress"] == 73 and imp["step"] == 4
    assert d["classes"] == ["harv-rep1"] and d["default_class"] == "harv-rep1" and d["nads"] == ["default/production"]


def test_the_controller_log_is_for_administrators(world):
    with wapp.app.test_client() as c:
        assert c.get("/api/vmimport-log/harv1/mig/vc", headers=auth("ops")).status_code == 403
        d = c.get("/api/vmimport-log/harv1/mig/vc", headers=auth("adm")).get_json()
    assert "incorrect user name or password" in d["lines"][0]


def test_writes_are_for_administrators_and_credentials_leave_through_a_private_file(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/vmimport/harv1/do/source-apply", json={"spec": VC}, headers=auth("ops")).status_code == 403
        r = c.post("/api/vmimport/harv1/do/source-apply", json={"spec": {**VC, "endpoint": "vc"}}, headers=auth("adm"))
        assert r.status_code == 400 and "s3cr3t" not in r.get_data(as_text=True)
        assert c.post("/api/vmimport/harv1/do/source-apply", json={"spec": VC}, headers=auth("adm")).status_code == 202
        assert c.post("/api/vmimport/harv1/do/import-create", json={"spec": IMP}, headers=auth("adm")).status_code == 202
        assert c.post("/api/vmimport/harv1/do/source-delete", json={"type": "vmware", "namespace": "mig", "name": "vc",
                                                                    "with_secret": True}, headers=auth("adm")).status_code == 202
        assert c.post("/api/vmimport/harv1/do/source-delete", json={"type": "nope", "namespace": "mig", "name": "vc"},
                      headers=auth("adm")).status_code == 400
    (l1, c1, f1), (l2, _, f2), (l3, c3, _) = world["actions"]
    assert l1 == "vmimport:source-apply:mig/vc" and "s3cr3t" in list(f1.values())[0] and "s3cr3t" not in " ".join(c1)
    assert not any(Path(p).exists() for p in f1)                          # effacé après l'action
    assert json.loads(list(f2.values())[0])["vm_name"] == "Cirros"
    assert l3 == "vmimport:source-delete:mig/vc" and c3[c3.index("--type") + 1] == "vmware" and c3[-1] == "--with-secret"
