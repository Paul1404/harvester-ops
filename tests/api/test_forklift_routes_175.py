"""v1.75.0 : routes de l'onglet Migrations VMware : état lu d'un coup,
écritures en actions suivies réservées aux administrateurs, secrets par
fichier privé (jamais dans une réponse ni une ligne de commande), mot de
passe repris d'une source VM Import ou du fournisseur lui-même côté serveur,
inventaire par l'outil, magasin d'archives VDDK."""

import base64
import gzip
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_forklift as hf  # noqa: E402
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

PW = "a long test password"
B64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731
REG = json.dumps({"Mirrors": {"172.16.1.11:5005": {"Endpoints": ["http://172.16.1.11:5005"]}},
                  "Configs": {"172.16.1.11:5005": {"Auth": {"Username": "harvops", "Password": "reg-S3cret"}}}})
ARCHIVE = "VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz"


def dep(ns, name):
    return {"metadata": {"namespace": ns, "name": name}, "spec": {"replicas": 1}, "status": {"availableReplicas": 1}}


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(wapp, "AUTH_OPEN_ALLOWED", False)
    monkeypatch.setattr(wapp, "HTPASSWD_PATH", tmp_path / "none")
    monkeypatch.setattr(wapp, "ROLES_PATH", tmp_path / "none.yaml")
    monkeypatch.setattr(wapp, "_roles_cache", {"mtime": None, "data": None})
    monkeypatch.setattr(wapp, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(wapp, "_ACCOUNTS", {"store": None})
    monkeypatch.setattr(wapp, "_LOCAL_SESSIONS", acc.LocalSessions())
    monkeypatch.setattr(wapp, "load_config", lambda: {"clusters": [{"name": "harvlab2", "kubeconfig": "/kc"}]})
    monkeypatch.setattr(wapp, "_cluster_reachable", lambda kc: True)
    monkeypatch.setattr(wapp, "VDDK_DIR", tmp_path / "vddk")
    bundle = tmp_path / "capi-bundle-x.tar.gz"
    bundle.write_bytes(b"x")
    monkeypatch.setattr(wapp, "_capi_bundle_active_path", lambda: bundle)
    wapp._FK_INVENTORY_CACHE.clear()
    wapp._accounts().create("adm", PW, "admin")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": [], "bundle": bundle}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        w["actions"].append((label, cmd, spec, tool))
        if after:
            after()

        class Run:
            id = "fk0000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    objs = {
        ("addons.harvesterhci.io",): {"items": [{"metadata": {"namespace": "forklift", "name": "forklift-operator",
                                                               "labels": {hf.L_MANAGED: "true"}},
                                                  "spec": {"enabled": True}, "status": {"status": "AddonDeploySuccessful"}}]},
        ("deployments.apps", "forklift"): {"items": [dep("forklift", n) for n in hf.COMPONENTS + (hf.OPERATOR_DEPLOY,)]},
        ("deployments.apps", "cert-manager"): {"items": [dep("cert-manager", n) for n in hf.CERT_MANAGER[1]]},
        (hf.K_CONTROLLER,): {"metadata": {"name": hf.CONTROLLER_NAME}},
        (hf.K_PROVIDER,): {"items": [
            {"metadata": {"namespace": "forklift", "name": "host"}, "spec": {"type": "openshift"}},
            {"metadata": {"namespace": "forklift", "name": "vmwlab", "labels": {hf.L_MANAGED: "true"}},
             "spec": {"type": "vsphere", "url": "https://vmwlab-vc.home.lo/sdk",
                      "settings": {"vddkInitImage": "172.16.1.11:5005/harvops/vddk:8.0.3"}},
             "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]},
        (hf.K_PLAN,): {"items": []},
        ("configmaps",): {"data": {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "digest": "sha256:" + "a" * 64,
                                   "archive": ARCHIVE, "pushed_at": "2026-09-28T20:00:00Z"}},
        ("settings.harvesterhci.io",): {"value": REG},
        ("vmwaresources.migration.harvesterhci.io",): {"items": [
            {"metadata": {"namespace": "mig", "name": "vc"},
             "spec": {"endpoint": "https://vmwlab-vc.home.lo/sdk", "dc": "vmwlab-dc",
                      "credentials": {"name": "vc-creds", "namespace": "mig"}}}]},
        ("vmwaresources.migration.harvesterhci.io", "mig", "vc"): {
            "metadata": {"namespace": "mig", "name": "vc"},
            "spec": {"endpoint": "https://vmwlab-vc.home.lo/sdk", "dc": "vmwlab-dc",
                     "credentials": {"name": "vc-creds", "namespace": "mig"}}},
        ("secrets", "mig", "vc-creds"): {"data": {"username": B64("administrator@vsphere.local"),
                                                  "password": B64("Very-S3cret!pw")}},
        ("secrets", "forklift", hf.secret_name("vmwlab")): {"data": {"user": B64("administrator@vsphere.local"),
                                                                      "password": B64("Old-S3cret!pw"),
                                                                      "insecureSkipVerify": B64("true")}},
        # un fournisseur fait par la CLI, vu en réel dans `default` (hors du namespace de la console)
        ("secrets", "default", hf.secret_name("vmwlab")): {"data": {"user": B64("administrator@vsphere.local"),
                                                                      "password": B64("Default-S3cret!pw"),
                                                                      "insecureSkipVerify": B64("false")}},
    }

    def kj(kc, verb, kind, *a, **k):
        ns = a[a.index("-n") + 1] if "-n" in a else None
        name = a[0] if a and not a[0].startswith("-") else None
        for key in ((kind, ns, name), (kind, ns), (kind,)):
            if key in objs:
                o = objs[key]
                if name and len(key) < 3 and "items" in o:
                    return None                    # un objet nommé absent n'est pas la liste de sa sorte
                return json.loads(json.dumps(o))
        return None
    monkeypatch.setattr(wapp, "_kubectl_json", kj)
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_tab_reads_install_vddk_registry_and_sources_at_once(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/forklift/harvlab2", headers=auth("eye")).get_json()
    assert d["install"]["ready"] and d["harvester_addon"] is False and d["bundle"] is True
    assert d["vddk"]["image"] == "172.16.1.11:5005/harvops/vddk:8.0.3"
    assert d["registry"] == {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "host": "172.16.1.11:5005",
                             "plain_http": True, "auth": True}
    [p] = d["providers"]                                   # le fournisseur « host » de Forklift n'est pas un vCenter
    assert (p["name"], p["ready"], p["plans"], p["managed"]) == ("vmwlab", True, [], True)
    assert d["vmimport_sources"] == [{"namespace": "mig", "name": "vc", "endpoint": "https://vmwlab-vc.home.lo/sdk"}]
    body = json.dumps(d)
    assert "reg-S3cret" not in body and "S3cret" not in body


def test_writes_are_for_administrators(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/forklift/harvlab2/do/install", json={}, headers=auth("eye")).status_code == 403
        assert c.post("/api/forklift/harvlab2/do/nope", json={}, headers=auth("adm")).status_code == 400
        assert c.post("/api/forklift/nope/do/install", json={}, headers=auth("adm")).status_code == 404


def test_install_brings_cert_manager_from_the_active_bundle(world):
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/install", json={}, headers=auth("adm"))
    assert r.status_code == 202 and r.get_json()["action_id"] == "fk0000000001"
    label, cmd, spec, tool = world["actions"][0]
    assert (label, tool, spec) == ("forklift:install", "harvester-forklift", None)
    assert cmd[2:5] == ["install", "--kubeconfig", "/kc"]
    assert cmd[cmd.index("--cert-manager-from-bundle") + 1] == str(world["bundle"])


def test_a_provider_password_leaves_only_through_the_private_file(world):
    spec = {"name": "vc2", "url": "vc2.lan", "user": "u@vsphere.local", "password": "N3w-S3cret", "insecure": True,
            "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3"}
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply", json={"spec": spec}, headers=auth("adm"))
        assert r.status_code == 202 and "N3w-S3cret" not in r.get_data(as_text=True)
        bad = c.post("/api/forklift/harvlab2/do/provider-apply", json={"spec": {**spec, "url": "not a url!"}},
                     headers=auth("adm"))
        assert bad.status_code == 400 and "N3w-S3cret" not in bad.get_data(as_text=True)
    label, cmd, sent, _ = world["actions"][0]
    assert label == "forklift:provider-apply:forklift/vc2" and "N3w-S3cret" not in " ".join(cmd)
    assert cmd[cmd.index("--namespace") + 1] == "forklift" and cmd[cmd.index("--name") + 1] == "vc2"
    assert sent["password"] == "N3w-S3cret" and sent["url"] == "vc2.lan"


def test_a_vcenter_of_vm_import_is_reused_with_its_password_read_by_the_server(world):
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply",
                   json={"spec": {"name": "vmwlab2", "from_vmimport": {"namespace": "mig", "name": "vc"},
                                  "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3"}}, headers=auth("adm"))
        assert r.status_code == 202
        miss = c.post("/api/forklift/harvlab2/do/provider-apply",
                      json={"spec": {"name": "x", "from_vmimport": {"namespace": "mig", "name": "absent"}}},
                      headers=auth("adm"))
        assert miss.status_code == 404
    _, cmd, sent, _ = world["actions"][0]
    assert sent == {"url": "https://vmwlab-vc.home.lo/sdk", "user": "administrator@vsphere.local",
                    "password": "Very-S3cret!pw", "insecure": True, "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3"}
    assert "Very-S3cret" not in " ".join(cmd)


def test_the_vmimport_source_lookup_is_a_named_get(world, monkeypatch):
    """La source VM Import est lue par son nom (`get <kind> <name> -n <ns>`),
    pas listée en entier (`-A`) puis filtrée côté serveur."""
    calls = []
    orig = wapp._kubectl_json

    def kj(kc, verb, kind, *a, **k):
        if kind == wapp._VM_SOURCE_KIND:
            calls.append(a)
        return orig(kc, verb, kind, *a, **k)
    monkeypatch.setattr(wapp, "_kubectl_json", kj)
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply",
                   json={"spec": {"name": "vmwlab2", "from_vmimport": {"namespace": "mig", "name": "vc"},
                                  "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3"}}, headers=auth("adm"))
    assert r.status_code == 202
    assert calls == [("vc", "-n", "mig")]


def test_changing_a_provider_without_retyping_its_password_keeps_it(world):
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply",
                   json={"spec": {"name": "vmwlab", "url": "https://vmwlab-vc.home.lo/sdk", "keep_credentials": True,
                                  "insecure": True, "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.4"}},
                   headers=auth("adm"))
    assert r.status_code == 202
    _, _, sent, _ = world["actions"][0]
    assert (sent["user"], sent["password"], sent["vddk_image"]) == ("administrator@vsphere.local", "Old-S3cret!pw",
                                                                    "172.16.1.11:5005/harvops/vddk:8.0.4")


def test_a_provider_is_deleted_with_the_secret_the_console_made(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/forklift/harvlab2/do/provider-delete", json={"name": "vmwlab"},
                      headers=auth("adm")).status_code == 202
        assert c.post("/api/forklift/harvlab2/do/provider-delete", json={"name": "Bad_Name"},
                      headers=auth("adm")).status_code == 400
    label, cmd, _, _ = world["actions"][0]
    assert label == "forklift:provider-delete:forklift/vmwlab" and cmd[-1] == "--with-secret"


def vddk_bytes():
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for name in ("vmware-vix-disklib-distrib/lib64/libvixDiskLib.so.8.0.3",):
            ti = tarfile.TarInfo(name)
            ti.size = 4
            tar.addfile(ti, io.BytesIO(b"ELF!"))
    return raw.getvalue()


def test_the_vddk_archive_is_kept_by_the_console_and_pushed_from_its_store(world):
    data = vddk_bytes()
    with wapp.app.test_client() as c:
        assert c.put(f"/api/forklift-vddk/{ARCHIVE}", data=data, headers=auth("eye")).status_code == 403
        assert c.put("/api/forklift-vddk/other.tar.gz", data=data, headers=auth("adm")).status_code == 400
        assert c.put(f"/api/forklift-vddk/{ARCHIVE}", data=b"not a gzip", headers=auth("adm")).status_code == 422
        r = c.put(f"/api/forklift-vddk/{ARCHIVE}", data=data, headers=auth("adm"))
        assert r.status_code == 201 and r.get_json()["archive"] == ARCHIVE
        assert c.put(f"/api/forklift-vddk/{ARCHIVE}", data=data, headers=auth("adm")).status_code == 409
        lst = c.get("/api/forklift-vddk", headers=auth("eye")).get_json()
        assert [(a["name"], a["version"], a["size"]) for a in lst["archives"]] == [(ARCHIVE, "8.0.3", len(data))]
        r = c.post("/api/forklift/harvlab2/do/vddk-image",
                   json={"archive": ARCHIVE, "image": "172.16.1.11:5005/harvops/vddk:8.0.3",
                         "plain_http": True, "use_cluster_auth": True}, headers=auth("adm"))
        assert r.status_code == 202
        r = c.post("/api/forklift/harvlab2/do/vddk-image",
                   json={"archive": "absent-" + ARCHIVE, "image": "r.lan/harvops/vddk:1"}, headers=auth("adm"))
        assert r.status_code == 404
        assert c.delete(f"/api/forklift-vddk/{ARCHIVE}", headers=auth("adm")).status_code == 200
        assert c.get("/api/forklift-vddk", headers=auth("eye")).get_json()["archives"] == []
    label, cmd, sent, _ = world["actions"][0]
    assert label == "forklift:vddk-image" and sent == {"username": "harvops", "password": "reg-S3cret"}
    assert cmd[cmd.index("--archive") + 1] == str(wapp.VDDK_DIR / ARCHIVE) and "--plain-http" in cmd
    assert cmd[cmd.index("--kubeconfig") + 1] == "/kc" and "reg-S3cret" not in " ".join(cmd)


def test_vddk_image_reads_the_registry_setting_from_its_default_too(world, monkeypatch):
    """Le réglage containerd-registry peut n'avoir qu'un `default` (jamais
    modifié à la main) : l'action vddk-image doit le lire comme la lecture
    de l'onglet le fait déjà (`value or default`)."""
    orig = wapp._kubectl_json

    def kj(kc, verb, kind, *a, **k):
        if kind == "settings.harvesterhci.io":
            return {"default": REG}
        return orig(kc, verb, kind, *a, **k)
    monkeypatch.setattr(wapp, "_kubectl_json", kj)
    data = vddk_bytes()
    with wapp.app.test_client() as c:
        assert c.put(f"/api/forklift-vddk/{ARCHIVE}", data=data, headers=auth("adm")).status_code == 201
        r = c.post("/api/forklift/harvlab2/do/vddk-image",
                   json={"archive": ARCHIVE, "image": "172.16.1.11:5005/harvops/vddk:8.0.3",
                         "use_cluster_auth": True}, headers=auth("adm"))
    assert r.status_code == 202
    _, _, sent, _ = world["actions"][0]
    assert sent == {"username": "harvops", "password": "reg-S3cret"}


def test_the_inventory_comes_from_the_tool_and_is_kept_briefly(world, monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, json.dumps([{"name": "vmwlab-src-1", "cbt": True}]), ""

    def run(cmd, **kw):
        calls.append(cmd)
        return R()
    monkeypatch.setattr(wapp.subprocess, "run", run)
    with wapp.app.test_client() as c:
        d = c.get("/api/forklift/harvlab2/inventory/vmwlab/vms", headers=auth("eye")).get_json()
        assert d["rows"] == [{"name": "vmwlab-src-1", "cbt": True}]
        c.get("/api/forklift/harvlab2/inventory/vmwlab/vms", headers=auth("eye"))
        assert c.get("/api/forklift/harvlab2/inventory/vmwlab/nope", headers=auth("eye")).status_code == 400
    assert len(calls) == 1
    assert calls[0][2:] == ["inventory", "--kubeconfig", "/kc", "--namespace", "forklift", "--name", "vmwlab", "--kind", "vms"]


def test_an_inventory_failure_is_said_without_paths(world, monkeypatch):
    class R:
        returncode, stdout = 1, ""
        stderr = "STEP_EVENT|inventory|error|the inventory service answered 503 (/home/ju/.kube/x.yaml)\n"
    monkeypatch.setattr(wapp.subprocess, "run", lambda cmd, **kw: R())
    with wapp.app.test_client() as c:
        r = c.get("/api/forklift/harvlab2/inventory/vmwlab/vms", headers=auth("eye"))
    assert r.status_code == 502 and "503" in r.get_json()["error"] and "/home/" not in r.get_json()["error"]


def test_an_inventory_that_never_answers_gives_a_short_timeout_message(world, monkeypatch):
    def run(cmd, **kw):
        raise wapp.subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(wapp.subprocess, "run", run)
    with wapp.app.test_client() as c:
        r = c.get("/api/forklift/harvlab2/inventory/vmwlab/vms", headers=auth("eye"))
    assert r.status_code == 502
    err = r.get_json()["error"]
    assert "did not answer in time" in err and "/" not in err


def test_a_truncated_archive_ends_the_run_in_error_not_stuck_running(world):
    """L'EOFError d'un gzip coupé net doit être une 422, pas une exception
    non attrapée qui laisserait l'ActionRun à « running » pour toujours."""
    data = vddk_bytes()
    truncated = data[: len(data) // 2]
    with wapp.app.test_client() as c:
        r = c.put(f"/api/forklift-vddk/{ARCHIVE}", data=truncated, headers=auth("adm"))
    assert r.status_code == 422
    action_id = r.get_json()["action_id"]
    run = wapp.ACTIONS[action_id]
    assert run.status == "error" and run.exit_code == 2


def test_an_upload_that_blows_up_unexpectedly_still_closes_the_run(world, monkeypatch):
    """Le filet de sécurité : une exception qui n'est ni une annulation ni
    une erreur de vérification connue (navigateur fermé, réseau coupé) ne
    doit pas s'échapper de la route et laisser le suivi bloqué."""
    def boom(run, stream, length, part):
        raise RuntimeError("connection reset by peer")
    monkeypatch.setattr(wapp, "_receive_archive", boom)
    data = vddk_bytes()
    with wapp.app.test_client() as c:
        r = c.put(f"/api/forklift-vddk/{ARCHIVE}", data=data, headers=auth("adm"))
    assert r.status_code == 400
    action_id = r.get_json()["action_id"]
    run = wapp.ACTIONS[action_id]
    assert run.status == "error" and run.exit_code == 1


def test_a_full_store_refuses_the_upload_before_receiving_it(world, monkeypatch):
    class St:
        f_bavail, f_frsize = 1, 1  # presque rien de libre
    monkeypatch.setattr(wapp.os, "statvfs", lambda d: St())
    data = vddk_bytes()
    with wapp.app.test_client() as c:
        r = c.put(f"/api/forklift-vddk/{ARCHIVE}", data=data, headers=auth("adm"))
    assert r.status_code == 507
    body = r.get_json()
    assert body["need"] == len(data) and body["free"] == 1


def test_a_stale_part_from_a_crash_is_dropped_before_a_new_upload(world):
    """Un `.part` d'un autre nom, laissé par un dépôt interrompu par un arrêt
    de la console, n'a aucune raison de rester : personne ne l'écrit plus."""
    d = wapp.VDDK_DIR
    d.mkdir(parents=True, exist_ok=True)
    other = "VMware-vix-disklib-8.0.2-99.x86_64.tar.gz"
    leftover = d / (other + wapp._PART_SUFFIX)
    leftover.write_bytes(b"half-written")
    data = vddk_bytes()
    with wapp.app.test_client() as c:
        r = c.put(f"/api/forklift-vddk/{ARCHIVE}", data=data, headers=auth("adm"))
    assert r.status_code == 201
    assert not leftover.exists()


def test_no_tls_keys_on_a_kept_edit_keeps_the_stored_insecure_flag(world):
    """`keep_credentials` gardait le mot de passe mais remettait
    silencieusement `insecure` à faux : le secret existant dit `true`."""
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply",
                   json={"spec": {"name": "vmwlab", "url": "https://vmwlab-vc.home.lo/sdk",
                                  "keep_credentials": True,
                                  "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.4"}},
                   headers=auth("adm"))
    assert r.status_code == 202
    _, _, sent, _ = world["actions"][0]
    assert sent["insecure"] is True and "cacert" not in sent


def test_no_tls_keys_on_a_kept_edit_keeps_the_stored_cacert(world, monkeypatch):
    orig = wapp._kubectl_json
    cacert = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"

    def kj(kc, verb, kind, *a, **k):
        if kind == "secrets" and a[:1] == (hf.secret_name("vmwlab"),):
            return {"data": {"user": B64("administrator@vsphere.local"), "password": B64("Old-S3cret!pw"),
                             "cacert": B64(cacert)}}
        return orig(kc, verb, kind, *a, **k)
    monkeypatch.setattr(wapp, "_kubectl_json", kj)
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply",
                   json={"spec": {"name": "vmwlab", "url": "https://vmwlab-vc.home.lo/sdk",
                                  "keep_credentials": True}},
                   headers=auth("adm"))
    assert r.status_code == 202
    _, _, sent, _ = world["actions"][0]
    assert sent["cacert"] == cacert and "insecure" not in sent


def test_an_explicit_insecure_wins_over_the_kept_tls_setting(world):
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply",
                   json={"spec": {"name": "vmwlab", "url": "https://vmwlab-vc.home.lo/sdk",
                                  "keep_credentials": True, "insecure": True,
                                  "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.4"}},
                   headers=auth("adm"))
    assert r.status_code == 202
    _, _, sent, _ = world["actions"][0]
    assert sent["insecure"] is True and "cacert" not in sent


# --- fix : un fournisseur peut vivre hors du namespace forklift -------------
# Vu en réel : un fournisseur fait par la CLI dans `default`. Les trois
# écritures et la lecture d'inventaire doivent suivre son namespace au lieu
# de toujours agir dans `hf.NS`.

def test_the_inventory_reads_a_provider_outside_forklift(world, monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, json.dumps([]), ""

    def run(cmd, **kw):
        calls.append(cmd)
        return R()
    monkeypatch.setattr(wapp.subprocess, "run", run)
    with wapp.app.test_client() as c:
        d = c.get("/api/forklift/harvlab2/inventory/vmwlab/vms?namespace=default", headers=auth("eye")).get_json()
    assert d["rows"] == []
    assert calls[0][2:] == ["inventory", "--kubeconfig", "/kc", "--namespace", "default", "--name", "vmwlab", "--kind", "vms"]


def test_the_inventory_still_defaults_to_forklift_without_the_query_param(world, monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, json.dumps([]), ""

    def run(cmd, **kw):
        calls.append(cmd)
        return R()
    monkeypatch.setattr(wapp.subprocess, "run", run)
    with wapp.app.test_client() as c:
        c.get("/api/forklift/harvlab2/inventory/vmwlab/vms", headers=auth("eye"))
    assert calls[0][calls[0].index("--namespace") + 1] == "forklift"


def test_the_inventory_refuses_an_invalid_namespace(world):
    with wapp.app.test_client() as c:
        r = c.get("/api/forklift/harvlab2/inventory/vmwlab/vms?namespace=Bad_NS", headers=auth("eye"))
    assert r.status_code == 400


def test_a_provider_outside_forklift_is_deleted_in_its_own_namespace(world):
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-delete", json={"name": "vmwlab", "namespace": "default"},
                   headers=auth("adm"))
    assert r.status_code == 202
    label, cmd, _, _ = world["actions"][0]
    assert label == "forklift:provider-delete:default/vmwlab"
    assert cmd[cmd.index("--namespace") + 1] == "default" and cmd[-1] == "--with-secret"


def test_editing_a_provider_outside_forklift_keeps_its_credentials_there(world):
    """`keep_credentials` doit relire le secret du fournisseur dans SON
    namespace (`default`), pas dans `forklift`."""
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply",
                   json={"spec": {"name": "vmwlab", "namespace": "default", "url": "https://vmwlab-vc.home.lo/sdk",
                                  "keep_credentials": True, "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.4"}},
                   headers=auth("adm"))
    assert r.status_code == 202
    label, cmd, sent, _ = world["actions"][0]
    assert label == "forklift:provider-apply:default/vmwlab"
    assert cmd[cmd.index("--namespace") + 1] == "default"
    assert sent["user"] == "administrator@vsphere.local" and sent["password"] == "Default-S3cret!pw"
    assert "Default-S3cret" not in " ".join(cmd)


# -- vu en réel sur harvlab2 (Harvester 1.9) : Auth retiré du réglage --------------

REG_19 = json.dumps({"Mirrors": {"172.16.1.11:5005": {"Endpoints": ["http://172.16.1.11:5005"]}},
                     "Configs": {"172.16.1.11:5005": {"Auth": None, "TLS": None}}, "Auths": None})
PROV_LOCAL = {"spec": {"rkeConfig": {"registries": {"configs": {
    "172.16.1.11:5005": {"authConfigSecretName": "harvester-containerd-registry-4041e0afc4370bdc"}}}}}}
AUTH_SECRET = {"type": "rke.cattle.io/auth-config",
               "data": {"username": B64("harvops"), "password": B64("reg-S3cret"), "host": B64("172.16.1.11:5005")}}


def harvester_19(monkeypatch):
    """Harvester 1.9 : le réglage n'a plus d'Auth ; les identifiants vivent dans
    un secret de fleet-local nommé par le cluster de provisionnement `local`."""
    orig = wapp._kubectl_json

    def kj(kc, verb, kind, *a, **k):
        if kind == "settings.harvesterhci.io":
            return {"value": REG_19}
        if kind == hf.PROV_CLUSTER[0]:
            return PROV_LOCAL if a[:3] == ("local", "-n", "fleet-local") else None
        if kind == "secrets" and a[:3] == ("harvester-containerd-registry-4041e0afc4370bdc", "-n", "fleet-local"):
            return AUTH_SECRET
        return orig(kc, verb, kind, *a, **k)
    monkeypatch.setattr(wapp, "_kubectl_json", kj)


def test_harvester_1_9_registry_credentials_are_found_in_their_secret(world, monkeypatch):
    harvester_19(monkeypatch)
    data = vddk_bytes()
    with wapp.app.test_client() as c:
        d = c.get("/api/forklift/harvlab2", headers=auth("eye")).get_json()
        assert d["registry"]["auth"] is True and "reg-S3cret" not in json.dumps(d)
        assert c.put(f"/api/forklift-vddk/{ARCHIVE}", data=data, headers=auth("adm")).status_code == 201
        r = c.post("/api/forklift/harvlab2/do/vddk-image",
                   json={"archive": ARCHIVE, "image": "172.16.1.11:5005/harvops/vddk:8.0.3",
                         "plain_http": True, "use_cluster_auth": True}, headers=auth("adm"))
        assert r.status_code == 202 and "reg-S3cret" not in r.get_data(as_text=True)
    _, cmd, sent, _ = world["actions"][0]
    assert sent == {"username": "harvops", "password": "reg-S3cret"} and "reg-S3cret" not in " ".join(cmd)


def test_a_registry_harvester_has_no_credentials_for_is_refused_clearly(world, monkeypatch):
    harvester_19(monkeypatch)
    data = vddk_bytes()
    with wapp.app.test_client() as c:
        assert c.put(f"/api/forklift-vddk/{ARCHIVE}", data=data, headers=auth("adm")).status_code == 201
        r = c.post("/api/forklift/harvlab2/do/vddk-image",
                   json={"archive": ARCHIVE, "image": "other.lan:5000/harvops/vddk:8.0.3",
                         "use_cluster_auth": True}, headers=auth("adm"))
    assert r.status_code == 400 and "no credentials" in r.get_json()["error"]


def test_the_vddk_store_is_private(world):
    """Vu en réel : le magasin naissait en 0755 ; l'archive est sous licence."""
    with wapp.app.test_client() as c:
        c.get("/api/forklift-vddk", headers=auth("eye"))
    assert (wapp.VDDK_DIR.stat().st_mode & 0o777) == 0o700
