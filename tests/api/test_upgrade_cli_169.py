"""v1.69.0 : la commande `harvester-resources upgrade` sur un cluster en
mémoire : refus d'avance (mise à jour en cours, version inéligible), suivi
qui tolère la coupure de l'API pendant le redémarrage, Dismiss, abandon
encadré, chemin airgap par un ISO servi (HEAD, plages, jeton)."""

import argparse
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_upgrade as hu  # noqa: E402
from kube import KubeError  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_upg", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)


class Kube:
    """Le contrôleur est simulé par une suite d'états rendus à chaque
    lecture de l'Upgrade ; None dans la suite = API injoignable."""

    def __init__(self, objs, script=()):
        self.objs = dict(objs)
        self.script = list(script)
        self.calls = []

    def get(self, kind, ns, name):
        if kind == hu.K_UPGRADE and self.script and (kind, ns, name) in self.objs:
            nxt = self.script.pop(0)
            if nxt is None:
                raise KubeError("Unable to connect to the server: dial tcp 172.16.2.70:6443: connect: no route to host")
            self.objs[(kind, ns, name)]["status"] = nxt
        o = self.objs.get((kind, ns, name))
        return json.loads(json.dumps(o)) if o is not None else None

    def list(self, kind, ns=None, selector=None):
        return [json.loads(json.dumps(o)) for (k, _, _), o in self.objs.items() if k == kind]

    def create(self, obj):
        obj = json.loads(json.dumps(obj))
        name = obj["metadata"].get("name") or obj["metadata"]["generateName"] + "abcde"
        obj["metadata"]["name"] = name
        kind = {"Upgrade": hu.K_UPGRADE, "Version": hu.K_VERSION, "VirtualMachineImage": hu.K_IMAGE}[obj["kind"]]
        if kind == hu.K_IMAGE:
            obj["status"] = {"progress": 100, "conditions": [{"type": "Imported", "status": "True"}]}
        self.objs[(kind, obj["metadata"].get("namespace"), name)] = obj
        self.calls.append(("create", obj["kind"], name, obj.get("spec")))
        return obj

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))
        self.objs.pop((kind, ns, name), None)

    def patch(self, kind, ns, name, patch):
        self.calls.append(("patch", kind, name, patch))

    def server_host(self):
        return "127.0.0.1"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    wait = hres._wait
    monkeypatch.setattr(hres, "_wait", lambda *a, **kw: wait(*a, **{"sleep": lambda s: None, **kw}))


def run(kube, **kw):
    base = {"name": None, "version": None, "version_file": None, "iso": None, "checksum": None, "port": 0,
            "advertise": "127.0.0.1", "no_log": False, "skip_single_replica": False, "node": None, "out": None,
            "timeout": 3600}
    orig = hres.kube_from
    hres.kube_from = lambda _a: kube
    try:
        return hres.cmd_upgrade(argparse.Namespace(**{**base, **kw}))
    finally:
        hres.kube_from = orig


SV = {("settings.harvesterhci.io", None, "server-version"): {"value": "v1.8.2"}}
VER = {(hu.K_VERSION, hu.NS, "v1.9.0"): {"metadata": {"name": "v1.9.0"}, "spec": {"isoURL": "https://x/h.iso",
                                                                                     "minUpgradableVersion": "v1.8.0"}}}


def cond(*pairs):
    return {"conditions": [{"type": t, "status": s} for t, s in pairs]}


def test_an_upgrade_is_started_and_followed_through_the_api_outage():
    script = [cond(("LogReady", "True")), None, None,                    # l'API tombe (RKE2, redémarrage)
              {**cond(("NodesUpgraded", "Unknown")), "nodeStatuses": {"n1": {"state": "Waiting Reboot"}}},
              cond(("SystemServicesUpgraded", "True"), ("NodesUpgraded", "True"), ("Completed", "True"))]
    k = Kube({**SV, **VER}, script)
    assert run(k, action="start", version="v1.9.0") == hres.EXIT_OK
    assert k.calls[0] == ("create", "Upgrade", "hvst-upgrade-abcde", {"logEnabled": True, "version": "v1.9.0"})


def test_a_long_api_outage_fails_the_follow_but_not_before(monkeypatch):
    clock = {"t": 0}
    monkeypatch.setattr(hres.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    k = Kube({**SV, **VER, (hu.K_UPGRADE, hu.NS, "u1"): {"metadata": {"name": "u1"}, "spec": {}}}, [None] * 400)
    assert hres._follow_upgrade(k, "u1", 10 ** 6, sleep=hres.time.sleep, now=lambda: clock["t"]) == hres.EXIT_FAIL
    assert clock["t"] > 2700                                                 # 45 min de patience


def test_what_is_refused_before_anything_is_created():
    running_up = {(hu.K_UPGRADE, hu.NS, "busy"): {"metadata": {"name": "busy", "labels": {}}, "spec": {}}}
    k = Kube({**SV, **VER, **running_up})
    with pytest.raises(ValueError, match="busy is still in progress"):
        run(k, action="start", version="v1.9.0")
    k = Kube({("settings.harvesterhci.io", None, "server-version"): {"value": "v1.7.1"}, **VER})
    with pytest.raises(ValueError, match="minimum"):
        run(k, action="start", version="v1.9.0")
    with pytest.raises(ValueError, match="add it first"):
        run(Kube(SV), action="start", version="v1.9.0")
    assert not k.calls


def test_dismiss_and_abort_are_guarded():
    live = {"metadata": {"name": "u1", "labels": {hu.L_STATE: "UpgradingNodes"}}, "spec": {},
            "status": cond(("NodesUpgraded", "Unknown"))}
    k = Kube({(hu.K_UPGRADE, hu.NS, "u1"): live})
    with pytest.raises(ValueError, match="hosts are being upgraded"):
        run(k, action="abort", name="u1")
    with pytest.raises(ValueError, match="still running"):
        run(k, action="dismiss", name="u1")
    early = {"metadata": {"name": "u2", "labels": {hu.L_STATE: "PreparingRepo"}}, "spec": {}, "status": cond(("LogReady", "True"))}
    k = Kube({(hu.K_UPGRADE, hu.NS, "u2"): early})
    assert run(k, action="abort", name="u2") == hres.EXIT_OK and ("delete", hu.K_UPGRADE, "u2") in k.calls
    done = {"metadata": {"name": "u3", "labels": {hu.L_STATE: "Succeeded"}}, "spec": {}, "status": cond(("Completed", "True"))}
    k = Kube({(hu.K_UPGRADE, hu.NS, "u3"): done})
    assert run(k, action="dismiss", name="u3") == hres.EXIT_OK
    assert k.calls[-1] == ("patch", hu.K_UPGRADE, "u3", {"metadata": {"labels": {hu.L_READ: "true"}}})


def test_the_iso_guichet_answers_head_ranges_and_only_its_token(tmp_path):
    iso = tmp_path / "h.iso"
    iso.write_bytes(bytes(range(256)) * 4)
    srv, url, stats = hres._serve_iso(iso, 0, None, "127.0.0.1")
    try:
        head = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"))
        assert head.headers["Content-Length"] == "1024"
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=0-127"}))
        assert r.status == 206 and r.read() == bytes(range(128)) and r.headers["Content-Range"] == "bytes 0-127/1024"
        assert urllib.request.urlopen(url).read() == iso.read_bytes()
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(url.rsplit("/", 1)[0] + "/wrong")
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_airgap_path_serves_the_iso_imports_it_then_upgrades(tmp_path, monkeypatch):
    iso = tmp_path / "harvester-v1.9.0-amd64.iso"
    iso.write_bytes(b"x" * 2048)
    monkeypatch.setattr(hres, "_iso_release", lambda p: {"harvester": "v1.9.0", "min_upgradable": "v1.8.0", "os": "",
                                                         "kubernetes": "", "rancher": ""})
    k = Kube(SV, [cond(("Completed", "True"))])
    sha = hres._sha512(iso)
    assert run(k, action="start", iso=str(iso), checksum=sha) == hres.EXIT_OK
    kinds = [c[1] for c in k.calls if c[0] == "create"]
    assert kinds == ["VirtualMachineImage", "Upgrade"]
    img_spec = k.calls[0][3]
    assert img_spec["backend"] == "cdi" and img_spec["targetStorageClassName"] == "longhorn-static"
    assert k.calls[1][3] == {"logEnabled": True, "image": "harvester-system/image-abcde"}
    with pytest.raises(ValueError, match="SHA-512 mismatch"):
        run(Kube(SV), action="start", iso=str(iso), checksum="0" * 128)
    monkeypatch.setattr(hres, "_iso_release", lambda p: {"harvester": "v1.8.0", "min_upgradable": "v1.7.0"})
    with pytest.raises(ValueError, match="downgrading"):
        run(Kube(SV), action="start", iso=str(iso))
