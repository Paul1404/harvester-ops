# Onglet « Migrations VMware » (1.75.0), plan d'implémentation

**Goal:** donner à la 1.75.0 l'écran de B1 : un onglet de cluster « Migrations VMware » (Préparation, Sources vCenter, Inventaire en lecture seule) qui pilote l'outil `harvester-forklift` livré par les tâches 1 à 7 de `docs/design/2026-09-28-forklift-b1-plan.md`.

**Architecture:** l'écran ne contourne jamais l'outil : les écritures partent en actions suivies (`_cli_action`) qui lancent `bin/harvester-forklift.py` avec un fichier privé pour les secrets ; les lectures d'état se font par `kubectl` avec les fonctions pures de `bin/lib/hv_forklift.py`, comme la section VM Import ; l'inventaire lance l'outil en synchrone (jeton court + port-forward). L'archive VDDK est déposée une fois dans un magasin de la console et sert à tous les clusters.

**Tech Stack:** Flask 3, JS vanilla (IIFE `window.Forklift`), `FloatingPanels`, `Sections`, i18n 5 langues, pytest + Playwright.

**Maquettes validées (29/09/2026)** : `forklift-ecrans` (serveur de maquettes) ; décisions de l'exploitant, toutes sur recommandation : navigation A (onglet sous Cluster après VM Import ; l'entrée globale « Migrations (tous clusters) » arrive avec B2, pas en 1.75.0), inventaire en lecture seule, bouton « Reprendre un vCenter de VM Import », archive VDDK gardée par la console pour tous les clusters, registre proposé d'après `containerd-registry`, lien depuis VM Import > VMware.

## Global Constraints

- L'UI ne contourne jamais `bin/harvester-forklift.py` pour écrire : `_cli_action(...)` (action suivie, dock, Activité).
- Aucun secret (mot de passe vCenter, identifiants de registre) en argument de commande, dans une réponse HTTP, un libellé d'action, un `STEP_EVENT` ou un journal : ils voyagent dans un fichier privé 0600 (`--spec FICHIER`) effacé après l'action. Un mot de passe déjà dans le cluster (source de VM Import, fournisseur existant, réglage `containerd-registry`) est relu côté serveur et ne passe jamais par le navigateur.
- Bibliothèque standard seulement dans `bin/`.
- Écritures réservées aux administrateurs (`ADMIN_ONLY_PREFIXES` + `/api/forklift`), `@requires_auth` et `@_rate_limit` sur chaque route mutative.
- Le VDDK n'est jamais dans le dépôt ni dans le livrable ; les tests fabriquent une fausse archive.
- Les fournisseurs de la console vivent dans le namespace `forklift` (`hf.NS`).
- Tooltip (`tip` + `data-tip`) sur chaque bouton, champ et icône ; `esc(...)` avant toute interpolation dans `innerHTML` ; aucun `new EventSource(` brut.
- i18n : chaque clé nouvelle dans les CINQ langues (en, fr, de, es, it) ; `tests/api/test_i18n*.py` doit rester vert.
- Code et noms en anglais, commentaires et docstrings en français, messages de l'outil en anglais, textes d'interface par i18n.
- `python3 -m pytest tests/api/ -q` vert avant chaque commit ; commits `feat(1.75.0): ...` ou `fix(1.75.0): ...`, sans tiret cadratin ni flèche Unicode.
- VERSION reste 1.74.0 jusqu'à la tâche de release (tâche 8 du plan B1), qui passe à 1.75.0 avec CHANGELOG et doc.
- Vérification réelle sur harvlab2 + vmwlab par la console de dev (port 8095) avant la release.

## Fichiers

| Fichier | Rôle |
|---|---|
| `bin/lib/hv_forklift.py` (modifié) | nom d'archive VDDK, image VDDK enregistrée (ConfigMap), registre proposé, identifiants relus d'un Secret, demande tirée d'une source VM Import |
| `bin/harvester-forklift.py` (modifié) | `--spec FICHIER` (provider-apply, vddk-image), `vddk-image --cluster/--kubeconfig` enregistre l'image, `install --cert-manager-from-bundle`, `status` rend l'image VDDK |
| `web/app.py` (modifié) | section v1.75.0 : état, actions, inventaire, magasin VDDK |
| `config/systemd/harvester-ops.service` (modifié) | `HARVESTER_OPS_VDDK_DIR=/var/lib/harvester-ops/vddk` |
| `web/templates/index.html` (modifié) | entrée de menu, section `#tab-forklift`, script |
| `web/static/js/forklift.js` (nouveau) | l'onglet |
| `web/static/js/sections.js`, `web/static/js/app.js` (modifiés) | section `forklift` |
| `web/static/js/vmimport.js` (modifié) | lien « Migrations VMware » dans l'onglet VMware |
| `web/static/js/i18n.js` (modifié) | clés `tab.forklift*`, `section.fk*`, `fk.*` en 5 langues |
| `web/static/css/style.css` (modifié) | `.fk-*` |
| `tests/api/test_forklift_ui_cli_175.py` (nouveau) | ajouts de la bibliothèque et de l'outil |
| `tests/api/test_forklift_routes_175.py` (nouveau) | routes |
| `tests/e2e/test_forklift_175.py` (nouveau) | l'onglet dans un navigateur |

---

### Task U1 : bibliothèque et outil, ce que l'écran demande

**Files:**
- Modify: `bin/lib/hv_forklift.py` (ajouts en fin de section fournisseur, avant `# --- inventaire`)
- Modify: `bin/harvester-forklift.py`
- Test: `tests/api/test_forklift_ui_cli_175.py`

**Interfaces:**
- Consumes: `hf.check_image`, `hf.namespace_manifest`, `hf.NS`, `hf.L_MANAGED`, `op.push_vddk_image` (rend `{"image","pinned","digest"}`), `kube_from`, `get_opt`, `read_stdin_json`, `step`.
- Produces (utilisés par U2) :
  - `hf.VDDK_CM = "harvester-ops-vddk"`, `hf.CERT_MANAGER_MEMBER = "manifests/cert-manager/cert-manager.yaml"`
  - `hf.check_archive_name(name) -> str` (version `8.0.3`), `ValueError` sinon
  - `hf.vddk_record_manifest(image, digest, archive, when) -> dict` (ConfigMap)
  - `hf.vddk_record(cm) -> dict | None` (`image`, `digest`, `archive`, `pushed_at`)
  - `hf.registry_hint(setting_value, archive="") -> {"image": str, "host": str, "plain_http": bool, "auth": bool}`
  - `hf.registry_auth(setting_value, host) -> {"username","password"} | None`
  - `hf.secret_values(secret, *keys) -> dict` (valeurs décodées des clés présentes)
  - `hf.spec_from_vmimport(source, secret, vddk_image="") -> dict` (demande de fournisseur)
  - outil : `provider-apply --spec F`, `vddk-image --spec F [--cluster C | --kubeconfig K]`, `install --cert-manager-from-bundle BUNDLE`, `status` rend `{"install", "providers", "vddk"}`

- [ ] **Step 1: tests qui échouent**

`tests/api/test_forklift_ui_cli_175.py` :

```python
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

REG = json.dumps({"Mirrors": {"172.16.1.11:5005": {"Endpoints": ["http://172.16.1.11:5005"]}},
                  "Configs": {"172.16.1.11:5005": {"Auth": {"Username": "harvops", "Password": "reg-S3cret"}}}})
B64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731


def test_the_archive_name_gives_the_vddk_version():
    assert hf.check_archive_name("VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz") == "8.0.3"
    for bad in ("vddk.tar.gz", "../VMware-vix-disklib-8.0.3-1.x86_64.tar.gz", "VMware-vix-disklib-8.0.3-1.x86_64.tgz", ""):
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
```

Run: `python3 -m pytest tests/api/test_forklift_ui_cli_175.py -q`
Expected: FAIL (attributs absents : `check_archive_name`, `read_json_input`, `bundle_cert_manager`...)

- [ ] **Step 2: la bibliothèque**

Dans `bin/lib/hv_forklift.py` : `import base64` et `import json` en tête (à côté de `import re`), puis, après `plans_using` et avant `# --- inventaire` :

```python
# --- ce que l'onglet de la console demande (v1.75.0) --------------------------

VDDK_CM = "harvester-ops-vddk"
CERT_MANAGER_MEMBER = "manifests/cert-manager/cert-manager.yaml"
ARCHIVE_RE = re.compile(r"^VMware-vix-disklib-(\d+\.\d+\.\d+)-\d+\.x86_64\.tar\.gz$")


def check_archive_name(name):
    """Le nom que VMware donne à l'archive VDDK ; rend sa version (8.0.3)."""
    m = ARCHIVE_RE.match(str(name or ""))
    if not m:
        raise ValueError("VDDK archive: VMware-vix-disklib-<version>-<build>.x86_64.tar.gz expected")
    return m.group(1)


def vddk_record_manifest(image, digest, archive, when):
    """La dernière image VDDK poussée pour ce cluster, gardée par le cluster
    lui-même : toute console qui le gère la retrouve (Préparation, formulaire
    de source)."""
    return {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": VDDK_CM, "namespace": NS, "labels": {L_MANAGED: "true"}},
            "data": {"image": check_image(image, "VDDK image"), "digest": str(digest),
                     "archive": str(archive), "pushed_at": str(when)}}


def vddk_record(cm):
    d = (cm or {}).get("data") or {}
    if not d.get("image"):
        return None
    return {k: d.get(k, "") for k in ("image", "digest", "archive", "pushed_at")}


def _registry_setting(value):
    if isinstance(value, dict):
        return value
    try:
        v = json.loads(value) if value and str(value).strip() else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def registry_hint(value, archive=""):
    """L'image VDDK proposée : le premier registre du réglage containerd-registry
    de Harvester (ses Configs, puis les points d'accès de ses miroirs), chemin
    harvops/vddk, étiquette = version du VDDK. Ne rend jamais d'identifiant,
    seulement s'il y en a (`auth`)."""
    v = _registry_setting(value)
    hosts, plain = [], set()
    for host in (v.get("Configs") or {}):
        hosts.append(str(host))
    for mirror in (v.get("Mirrors") or {}).values():
        for ep in (mirror or {}).get("Endpoints") or []:
            ep = str(ep)
            host = re.sub(r"^https?://", "", ep).rstrip("/")
            if ep.startswith("http://"):
                plain.add(host)
            hosts.append(host)
    host = next((h for h in hosts if h), "")
    if not host:
        return {"image": "", "host": "", "plain_http": False, "auth": False}
    try:
        tag = check_archive_name(archive)
    except ValueError:
        tag = "latest"
    return {"image": f"{host}/harvops/vddk:{tag}", "host": host, "plain_http": host in plain,
            "auth": registry_auth(v, host) is not None}


def registry_auth(value, host):
    """Les identifiants que Harvester a déjà pour ce registre (Configs.<hôte>.Auth)."""
    auth = (((_registry_setting(value).get("Configs") or {}).get(host) or {}).get("Auth")) or {}
    if auth.get("Username") and auth.get("Password"):
        return {"username": str(auth["Username"]), "password": str(auth["Password"])}
    return None


def secret_values(secret, *keys):
    """Les valeurs décodées des clés présentes d'un Secret (champ data)."""
    data = (secret or {}).get("data") or {}
    return {k: base64.b64decode(data[k]).decode() for k in keys if data.get(k)}


def spec_from_vmimport(source, secret, vddk_image=""):
    """Un vCenter déjà déclaré dans VM Import (VmwareSource et son Secret)
    devient une demande de fournisseur. Lu côté serveur : le mot de passe ne
    passe jamais par le navigateur. Sans certificat d'autorité, VM Import ne
    vérifie pas TLS : le fournisseur non plus."""
    vals = secret_values(secret, "username", "password", "caCert")
    if not vals.get("username") or not vals.get("password"):
        raise ValueError("the VM Import source has no user and password to reuse")
    spec = {"url": ((source or {}).get("spec") or {}).get("endpoint"),
            "user": vals["username"], "password": vals["password"]}
    if vals.get("caCert"):
        spec["cacert"] = vals["caCert"]
    else:
        spec["insecure"] = True
    if vddk_image:
        spec["vddk_image"] = vddk_image
    return spec
```

Note : le test attend `vddk_image` présent quand il est donné, absent sinon (deuxième appel sans image : `"insecure" not in spec`, rien sur `vddk_image`).

- [ ] **Step 3: l'outil**

Dans `bin/harvester-forklift.py` :

1. Docstring : ajouter les options aux lignes d'usage :

```
    harvester-forklift install         --cluster C [--chart-version V] [--image-tag T]
                                       [--cert-manager-manifest FICHIER | --cert-manager-from-bundle PAQUET]
    harvester-forklift vddk-image      --archive VDDK.tar.gz --image REGISTRE/DEPOT:TAG
                                       [--base IMAGE] [--plain-http] [--auth-stdin | --spec FICHIER]
                                       [--cluster C | --kubeconfig K]
    harvester-forklift provider-apply  --cluster C --namespace NS --name N   (JSON sur stdin ou --spec FICHIER)
```

et la phrase « Les secrets (vCenter, registre) arrivent en JSON sur l'entrée standard, jamais en argument. » devient « Les secrets (vCenter, registre) arrivent en JSON sur l'entrée standard ou dans un fichier privé (`--spec`, ce que fait la console), jamais en argument. ». Ajouter `import tarfile` et `import tempfile` aux imports.

2. Après `read_stdin_json` :

```python
def read_json_input(args):
    """La demande : le fichier privé donné par --spec (la console), sinon l'entrée standard."""
    path = getattr(args, "spec", None)
    if not path:
        return read_stdin_json()
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        raise ValueError("--spec: a readable JSON file expected") from None
    if not isinstance(data, dict):
        raise ValueError("--spec: a JSON object expected")
    return data


def bundle_cert_manager(bundle, into):
    """Le manifeste cert-manager du paquet Cluster API de la console, écrit dans `into`."""
    try:
        with tarfile.open(bundle, "r:gz") as tar:
            m = next((m for m in tar.getmembers() if m.isfile() and m.name.endswith(hf.CERT_MANAGER_MEMBER)), None)
            if m is None:
                raise ValueError("the Cluster API bundle has no cert-manager manifest")
            data = tar.extractfile(m).read()
    except (OSError, tarfile.TarError):
        raise ValueError("the Cluster API bundle cannot be read") from None
    out = Path(into) / "cert-manager.yaml"
    out.write_bytes(data)
    return str(out)
```

3. `cmd_install` : à l'endroit où cert-manager manque (`if not args.cert_manager_manifest:` qui échoue aujourd'hui), prendre d'abord le paquet s'il est donné. Remplacer le bloc « cert-manager absent » par :

```python
    else:
        manifest = args.cert_manager_manifest
        tmp = None
        if not manifest and getattr(args, "cert_manager_from_bundle", None):
            tmp = tempfile.TemporaryDirectory(prefix="hfk-cm-")
            manifest = bundle_cert_manager(args.cert_manager_from_bundle, tmp.name)
        if not manifest:
            # (message d'erreur existant, inchangé)
            ...
        try:
            step("cert-manager", "running", "installing cert-manager")
            kube.run("apply", "--server-side", "--force-conflicts", "-f", manifest, timeout=300)
        finally:
            if tmp:
                tmp.cleanup()
        # (attente existante de cert-manager, inchangée)
```

Garder à l'identique le message d'erreur actuel et l'attente `until(cm, ...)` ; seule la source du manifeste change. Lire la fonction entière avant d'éditer.

4. `cmd_vddk_image` :

```python
def cmd_vddk_image(args):
    if getattr(args, "spec", None):
        creds = read_json_input(args)
    else:
        creds = read_stdin_json() if args.auth_stdin else None
    if creds is not None and not (creds.get("username") and creds.get("password")):
        raise ValueError('registry credentials: {"username": ..., "password": ...} expected')
    step("vddk", "running", f"building the VDDK image from {Path(args.archive).name}")
    res = op.push_vddk_image(args.archive, args.image, base=args.base, target_creds=creds,
                             plain_http=args.plain_http, step=lambda m: step("vddk", "running", m))
    step("vddk", "done", f"{res['image']} ({res['digest'][:19]})")
    if getattr(args, "cluster", None) or getattr(args, "kubeconfig", None):
        kube = kube_from(args)
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        kube.apply([hf.namespace_manifest(),
                    hf.vddk_record_manifest(res["image"], res["digest"], Path(args.archive).name, when)])
        step("vddk-record", "done", f"the cluster remembers {res['image']}")
    print(json.dumps(res))
    return EXIT_OK
```

Le test existant `test_vddk_image_reads_the_registry_credentials_on_stdin` passe un `Namespace` sans `spec`, `cluster`, `kubeconfig` : les `getattr(..., None)` le gardent vert ; son message d'erreur attendu, s'il en vérifie un, doit rester cohérent (le relire).

5. `cmd_provider_apply` : `spec = read_json_input(args)` au lieu de `read_stdin_json()`.

6. `cmd_status` : ajouter l'image retenue :

```python
    vddk = hf.vddk_record(get_opt(kube, "configmaps", hf.NS, hf.VDDK_CM))
    print(json.dumps({"install": st, "providers": providers, "vddk": vddk}))
```

7. `build_parser` :
   - `install` : `sp.add_argument("--cert-manager-from-bundle", help="the console's Cluster API bundle (.tar.gz): its cert-manager manifest is applied if cert-manager is missing")`
   - `vddk-image` : `cluster_args(sp)` (ajoute `--cluster` et `--kubeconfig`, optionnels ici) et `sp.add_argument("--spec", help='a private JSON file {"username": ..., "password": ...} instead of stdin')`
   - `provider-apply` seulement : `sp.add_argument("--spec", help="a private JSON file with the request instead of stdin")`

Vérifier que `cluster_args` ne rend pas `--cluster` obligatoire (il ne l'est pas aujourd'hui).

- [ ] **Step 4: tests verts**

Run: `python3 -m pytest tests/api/test_forklift_ui_cli_175.py tests/api/test_forklift_cli_175.py tests/api/test_forklift_175.py -q`
Expected: PASS

- [ ] **Step 5: suite complète et commit**

Run: `python3 -m pytest tests/api/ -q`
Expected: tout vert

```bash
git add bin/lib/hv_forklift.py bin/harvester-forklift.py tests/api/test_forklift_ui_cli_175.py
git commit -m "feat(1.75.0): what the VMware migrations tab needs from harvester-forklift"
```

---

### Task U2 : routes de la console

**Files:**
- Modify: `web/app.py` (nouvelle section après la section « v1.71.0 : imports de VM », avant `@app.route("/api/storage-options/<cluster>")` ; `ADMIN_ONLY_PREFIXES`)
- Modify: `config/systemd/harvester-ops.service` (variable d'environnement)
- Test: `tests/api/test_forklift_routes_175.py`

**Interfaces:**
- Consumes: U1 (`hf.*`), `_cli_action(cluster, label, cmd, tool, spec=None, dry_run=False, after=None)` (ajoute `--spec <fichier privé>` quand `spec` est donné), `_kubectl_for_cluster`, `_cluster_reachable`, `_unreachable_payload`, `_kubectl_json(kc, "get", ..., timeout=, cluster=)`, `_res_reply`, `_invalidate_cluster_caches`, `_capi_bundle_active_path()`, `current_cluster_identity()`, `ActionRun`, `ACTIONS`, `ACTIONS_LOCK`, `_receive_archive(run, stream, length, part)`, `_UploadCancelled`, `_UPLOAD_SPARE`, `_PART_SUFFIX`, `_error_text`, `_vp.fmt_bytes`, `oci_push.archive_layer(path)` (lève `ValueError` si ce n'est pas un VDDK).
- Produces (utilisés par U3/U4) :
  - `GET /api/forklift/<cluster>` -> JSON ci-dessous
  - `POST /api/forklift/<cluster>/do/<action>` (`install`, `vddk-image`, `provider-apply`, `provider-delete`) -> `202 {"action_id", "action"}`
  - `GET /api/forklift/<cluster>/inventory/<name>/<kind>` -> `{"rows": [...]}` (lignes de `hf.inventory_rows`)
  - `GET /api/forklift-vddk` -> `{"archives": [{"name","version","size","mtime"}], "free": int}`
  - `PUT /api/forklift-vddk/<name>` (corps = l'archive) -> `201 {"action_id","archive","size"}`
  - `DELETE /api/forklift-vddk/<name>` -> `200 {"deleted": name}`

Forme de `GET /api/forklift/<cluster>` :

```json
{"cluster": "harvlab2",
 "install": {"ready": true, "cert_manager": true, "cert_manager_missing": [], "addon": "ready", "addon_message": "...",
             "operator": true, "controller": true, "components_missing": []},
 "harvester_addon": false,
 "bundle": true,
 "vddk": {"image": "...", "digest": "sha256:...", "archive": "...", "pushed_at": "..."},
 "registry": {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "host": "172.16.1.11:5005", "plain_http": true, "auth": true},
 "providers": [{"name": "vmwlab", "url": "https://vmwlab-vc.home.lo/sdk", "ready": true, "message": "...",
                "vddk_image": "...", "plans": [], "managed": true}],
 "vmimport_sources": [{"namespace": "mig", "name": "vc", "endpoint": "https://vc/sdk"}]}
```

- [ ] **Step 1: tests qui échouent**

`tests/api/test_forklift_routes_175.py` :

```python
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
        ("secrets", "mig", "vc-creds"): {"data": {"username": B64("administrator@vsphere.local"),
                                                  "password": B64("Very-S3cret!pw")}},
        ("secrets", "forklift", hf.secret_name("vmwlab")): {"data": {"user": B64("administrator@vsphere.local"),
                                                                      "password": B64("Old-S3cret!pw"),
                                                                      "insecureSkipVerify": B64("true")}},
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
    assert label == "forklift:provider-apply:vc2" and "N3w-S3cret" not in " ".join(cmd)
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
    assert label == "forklift:provider-delete:vmwlab" and cmd[-1] == "--with-secret"


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
```

Run: `python3 -m pytest tests/api/test_forklift_routes_175.py -q`
Expected: FAIL (routes absentes)

- [ ] **Step 2: les routes**

Dans `ADMIN_ONLY_PREFIXES`, après la ligne `/api/vmimport/` :

```python
    "/api/forklift",            # v1.75.0 : Forklift, image VDDK et son magasin, fournisseurs vCenter
```

Vérifier comment `ADMIN_ONLY_PREFIXES` est appliqué (méthodes mutatives seulement ? `GET /api/forklift-vddk` doit rester lisible par un viewer, le test le demande) ; si le préfixe s'applique aussi aux lectures, adapter pour que seules les méthodes `POST`/`PUT`/`DELETE` soient réservées, comme pour `/api/vmimport/`.

Nouvelle section, après `api_vmimport_do` :

```python
# ---------------------------------------------------------------------------
# v1.75.0 : migrations VMware par Forklift, onglet de cluster. Écritures par
# bin/harvester-forklift.py (parité CLI), secrets par fichier privé ; un mot
# de passe déjà dans le cluster est relu ici, jamais renvoyé au navigateur.
# Voir docs/design/2026-09-29-forklift-ui-plan.md.
# ---------------------------------------------------------------------------

import tarfile  # noqa: E402
import hv_forklift as _hf  # noqa: E402
import oci_push as _op  # noqa: E402

FORKLIFT_SCRIPT = "harvester-forklift.py"
VDDK_DIR = Path(os.environ.get(
    "HARVESTER_OPS_VDDK_DIR", str(Path.home() / ".local/share/harvester-ops/vddk")))
_FORKLIFT_DO = ("install", "vddk-image", "provider-apply", "provider-delete")
_FK_INVENTORY_CACHE = {}        # (cluster, identité, fournisseur, sorte) -> (horodatage, lignes)
_FK_INVENTORY_TTL = 20
_FK_KINDS = ("vms", "networks", "datastores")
_VM_SOURCE_KIND = "vmwaresources.migration.harvesterhci.io"
_VDDK_UPLOADS = set()
_VDDK_LOCK = threading.Lock()
_PATH_RE = re.compile(r"(?:/[^\s/:'\"()]+)+")


def _vddk_dir():
    VDDK_DIR.mkdir(parents=True, exist_ok=True)
    return VDDK_DIR


def _fk_cmd(action, kc):
    return [sys.executable, str(BIN_DIR / FORKLIFT_SCRIPT), action, "--kubeconfig", kc]


@app.route("/api/forklift/<cluster>")
@requires_auth
def api_forklift(cluster):
    """L'onglet d'un coup : installation (dans l'ordre où elle se fait), image
    VDDK retenue par le cluster, registre proposé, fournisseurs vCenter et
    les sources VMware de VM Import qu'on peut reprendre."""
    kc = _kubectl_for_cluster(cluster)
    if not kc:
        return jsonify({"error": f"unknown cluster: {cluster}"}), 404
    if _cluster_reachable(kc) is False:
        return jsonify(_unreachable_payload(cluster, kc)), 200
    from concurrent.futures import ThreadPoolExecutor
    reads = {"addons": (_hf.K_ADDON, "-A"), "deploys": (_hf.K_DEPLOY, "-n", _hf.NS),
             "cm_deploys": (_hf.K_DEPLOY, "-n", _hf.CERT_MANAGER[0]),
             "controller": (_hf.K_CONTROLLER, _hf.CONTROLLER_NAME, "-n", _hf.NS),
             "providers": (_hf.K_PROVIDER, "-A"), "plans": (_hf.K_PLAN, "-A"),
             "vddk": ("configmaps", _hf.VDDK_CM, "-n", _hf.NS),
             "registry": ("settings.harvesterhci.io", "containerd-registry"),
             "sources": (_VM_SOURCE_KIND, "-A")}
    with ThreadPoolExecutor(max_workers=len(reads)) as pool:
        futs = {k: pool.submit(_kubectl_json, kc, "get", *a, timeout=30, cluster=cluster) for k, a in reads.items()}
        got = {k: f.result() for k, f in futs.items()}
    items = lambda k: (got[k] or {}).get("items") or []  # noqa: E731
    by_name = lambda k: {(d.get("metadata") or {}).get("name"): d for d in items(k)}  # noqa: E731
    addon, theirs = _hf.pick_addon(items("addons"))
    vddk = _hf.vddk_record(got["vddk"])
    reg = got["registry"] or {}
    providers = []
    for p in items("providers"):
        spec, m = p.get("spec") or {}, p.get("metadata") or {}
        if spec.get("type") != "vsphere":
            continue
        ready, msg = _hf.provider_state(p)
        providers.append({"name": m.get("name"), "namespace": m.get("namespace"), "url": spec.get("url"),
                          "ready": ready, "message": msg,
                          "vddk_image": (spec.get("settings") or {}).get("vddkInitImage", ""),
                          "plans": _hf.plans_using(m.get("namespace"), m.get("name"), items("plans")),
                          "managed": (m.get("labels") or {}).get(_hf.L_MANAGED) == "true"})
    return jsonify({
        "cluster": cluster,
        "install": _hf.install_state(addon, by_name("deploys"), got["controller"], by_name("cm_deploys")),
        "harvester_addon": bool(theirs),
        "bundle": _capi_bundle_active_path() is not None,
        "vddk": vddk,
        "registry": _hf.registry_hint(reg.get("value") or reg.get("default") or "", (vddk or {}).get("archive", "")),
        "providers": sorted(providers, key=lambda r: r["name"] or ""),
        "vmimport_sources": sorted(({"namespace": (s.get("metadata") or {}).get("namespace"),
                                     "name": (s.get("metadata") or {}).get("name"),
                                     "endpoint": (s.get("spec") or {}).get("endpoint")} for s in items("sources")),
                                   key=lambda r: (r["namespace"] or "", r["name"] or "")),
    })


def _fk_provider_spec(kc, cluster, b):
    """La demande de fournisseur complète. Trois formes : tout saisi ; repris
    d'une source VM Import ; modifié sans ressaisir le mot de passe (repris du
    secret du fournisseur). Rend (nom, demande) ; LookupError si l'objet cité
    n'existe pas."""
    name = _hf.check_name(str(b.get("name") or ""), "provider")
    vddk = str(b.get("vddk_image") or "").strip()
    src = b.get("from_vmimport")
    if isinstance(src, dict):
        ns, sname = _hf.check_name(src.get("namespace"), "namespace"), _hf.check_name(src.get("name"), "source")
        source = _kubectl_json(kc, "get", _VM_SOURCE_KIND, sname, "-n", ns, timeout=30, cluster=cluster)
        cred = ((source or {}).get("spec") or {}).get("credentials") or {}
        secret = _kubectl_json(kc, "get", "secrets", _hf.check_name(cred.get("name"), "secret"),
                               "-n", _hf.check_name(cred.get("namespace") or ns, "namespace"),
                               timeout=30, cluster=cluster) if source else None
        if not source or not secret:
            raise LookupError(f"no VM Import source {ns}/{sname} with credentials")
        spec = _hf.spec_from_vmimport(source, secret, vddk)
    else:
        spec = {"url": b.get("url"), "user": str(b.get("user") or "").strip(), "password": str(b.get("password") or "")}
        if b.get("cacert"):
            spec["cacert"] = str(b["cacert"])
        else:
            spec["insecure"] = bool(b.get("insecure"))
        if vddk:
            spec["vddk_image"] = vddk
        if b.get("keep_credentials") and not spec["password"]:
            secret = _kubectl_json(kc, "get", "secrets", _hf.secret_name(name), "-n", _hf.NS, timeout=30, cluster=cluster)
            kept = _hf.secret_values(secret, "user", "password")
            if not kept.get("password"):
                raise LookupError(f"provider {name} has no saved credentials to keep")
            spec["user"] = spec["user"] or kept.get("user", "")
            spec["password"] = kept["password"]
    # refuse avant d'écrire, sans jamais citer d'identifiant
    _hf.provider_secret(_hf.NS, name, spec)
    _hf.provider_manifest(_hf.NS, name, spec)
    return name, spec


@app.route("/api/forklift/<cluster>/do/<action>", methods=["POST"])
@requires_auth
@_rate_limit("30/minute")
def api_forklift_do(cluster, action):
    if action not in _FORKLIFT_DO:
        return jsonify({"error": "action: " + ", ".join(_FORKLIFT_DO)}), 400
    kc = _kubectl_for_cluster(cluster)
    if not kc:
        return jsonify({"error": f"unknown cluster: {cluster}"}), 404
    b = request.get_json(silent=True) or {}
    cmd, spec = _fk_cmd(action, kc), None
    try:
        if action == "install":
            bundle = _capi_bundle_active_path()
            if bundle:
                cmd += ["--cert-manager-from-bundle", str(bundle)]
            label = "forklift:install"
        elif action == "vddk-image":
            archive = str(b.get("archive") or "")
            _hf.check_archive_name(archive)
            path = _vddk_dir() / archive
            if not path.is_file():
                return jsonify({"error": f"no VDDK archive {archive} in the console"}), 404
            image = _hf.check_image(str(b.get("image") or "").strip(), "VDDK image")
            cmd += ["--archive", str(path), "--image", image]
            if b.get("plain_http"):
                cmd.append("--plain-http")
            if b.get("use_cluster_auth"):
                reg = _kubectl_json(kc, "get", "settings.harvesterhci.io", "containerd-registry",
                                    timeout=30, cluster=cluster) or {}
                spec = _hf.registry_auth(reg.get("value") or "", image.split("/", 1)[0])
                if spec is None:
                    raise ValueError("Harvester has no credentials for this registry")
            elif b.get("username") or b.get("password"):
                spec = {"username": str(b.get("username") or ""), "password": str(b.get("password") or "")}
                if not spec["username"] or not spec["password"]:
                    raise ValueError("registry: the user and the password go together")
            label = "forklift:vddk-image"
        elif action == "provider-apply":
            name, spec = _fk_provider_spec(kc, cluster, b.get("spec") if isinstance(b.get("spec"), dict) else {})
            cmd += ["--namespace", _hf.NS, "--name", name]
            label = f"forklift:provider-apply:{name}"
        else:
            name = _hf.check_name(str(b.get("name") or ""), "provider")
            cmd += ["--namespace", _hf.NS, "--name", name, "--with-secret"]
            label = f"forklift:provider-delete:{name}"
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    run, err = _cli_action(cluster, label, cmd, "harvester-forklift", spec=spec,
                           after=lambda: _invalidate_cluster_caches(cluster))
    return _res_reply(run, err, action=action)


@app.route("/api/forklift/<cluster>/inventory/<name>/<kind>")
@requires_auth
def api_forklift_inventory(cluster, name, kind):
    """Ce que Forklift voit d'un vCenter, par l'outil (jeton court et
    port-forward) ; gardé 20 s par personne pour ne pas rouvrir un tunnel à
    chaque clic."""
    if kind not in _FK_KINDS:
        return jsonify({"error": "kind: " + ", ".join(_FK_KINDS)}), 400
    kc = _kubectl_for_cluster(cluster)
    if not kc:
        return jsonify({"error": f"unknown cluster: {cluster}"}), 404
    key = (cluster, (current_cluster_identity() or {}).get("user"), name, kind)
    hit = _FK_INVENTORY_CACHE.get(key)
    if hit and time.time() - hit[0] < _FK_INVENTORY_TTL:
        return jsonify({"rows": hit[1]})
    r = subprocess.run(_fk_cmd("inventory", kc) + ["--namespace", _hf.NS, "--name", name, "--kind", kind],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        lines = [ln.split("|", 3)[3] for ln in r.stderr.splitlines() if ln.startswith("STEP_EVENT|") and ln.count("|") >= 3]
        msg = (lines[-1] if lines else (r.stderr.strip().splitlines() or ["the inventory cannot be read"])[-1])
        return jsonify({"error": _PATH_RE.sub("<path>", msg)[:300]}), 502
    try:
        rows = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return jsonify({"error": "the inventory answer cannot be read"}), 502
    _FK_INVENTORY_CACHE[key] = (time.time(), rows)
    return jsonify({"rows": rows})


@app.route("/api/forklift-vddk")
@requires_auth
def api_forklift_vddk_list():
    """Les archives VDDK déposées : une seule suffit pour tous les clusters."""
    d = _vddk_dir()
    out = []
    for p in sorted(d.glob("VMware-vix-disklib-*.tar.gz")):
        try:
            ver, st = _hf.check_archive_name(p.name), p.stat()
        except (ValueError, OSError):
            continue
        out.append({"name": p.name, "version": ver, "size": st.st_size, "mtime": st.st_mtime})
    try:
        st = os.statvfs(d)
        free = st.f_bavail * st.f_frsize
    except OSError:
        free = 0
    return jsonify({"archives": out, "free": free})


@app.route("/api/forklift-vddk/<name>", methods=["PUT"])
@requires_auth
@_rate_limit("6 per minute")
def api_forklift_vddk_upload(name):
    """Dépose l'archive VDDK de VMware (corps de la requête), vérifiée comme
    l'outil la lira ; action suivie comme tout dépôt."""
    try:
        _hf.check_archive_name(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    length = request.content_length
    if not length:
        return jsonify({"error": "Content-Length required"}), 411
    d = _vddk_dir()
    dest, part = d / name, d / (name + _PART_SUFFIX)
    with _VDDK_LOCK:
        if dest.exists() or name in _VDDK_UPLOADS:
            return jsonify({"error": f"{name} is already in the console"}), 409
        _VDDK_UPLOADS.add(name)
    run = ActionRun(uuid.uuid4().hex[:12], f"vddk-archive-upload:{name}", "(local)", ["upload", name])
    run.cluster_user = (current_cluster_identity() or {}).get("user")
    with ACTIONS_LOCK:
        ACTIONS[run.id] = run

    def step(sid, status, msg=""):
        run.emit({"type": "step", "step_id": sid, "status": status, "message": msg, "ts": time.time()})

    run.status = "running"
    run.emit({"type": "status", "status": "running", "ts": time.time()})
    step("upload", "running", f"receiving {name} ({_vp.fmt_bytes(length)})")
    code, body = 201, None
    try:
        _receive_archive(run, request.stream, length, part)
        step("upload", "done", f"{name} received")
        step("verify", "running", "checking that this is VMware's VDDK")
        _op.archive_layer(str(part))
        os.link(part, dest)
        step("verify", "done", f"VDDK {_hf.check_archive_name(name)} kept by the console")
        run.status, run.exit_code = "done", 0
        body = {"action_id": run.id, "archive": name, "size": length}
    except _UploadCancelled:
        run.status, run.exit_code = "cancelled", 3
        run.error_summary = "cancelled, nothing kept"
        code, body = 409, {"error": "cancelled", "action_id": run.id}
    except (ValueError, OSError, tarfile.TarError) as e:
        run.status, run.exit_code = "error", 2
        run.error_summary = _error_text(e) if isinstance(e, OSError) else str(e)[:300]
        step("verify", "error", run.error_summary)
        code, body = 422, {"error": run.error_summary, "action_id": run.id}
    finally:
        part.unlink(missing_ok=True)
        with _VDDK_LOCK:
            _VDDK_UPLOADS.discard(name)
        run.ended_at = time.time()
        run.emit({"type": "status", "status": run.status, "exit_code": run.exit_code, "ts": time.time()})
        run.close()
    return jsonify(body), code


@app.route("/api/forklift-vddk/<name>", methods=["DELETE"])
@requires_auth
@_rate_limit("20/minute")
def api_forklift_vddk_delete(name):
    try:
        _hf.check_archive_name(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    p = _vddk_dir() / name
    if not p.is_file():
        return jsonify({"error": "not found"}), 404
    p.unlink()
    return jsonify({"deleted": name})
```

Points à vérifier en écrivant (lire le code, ne pas deviner) :
- `archive_layer` d'`oci_push` lève `ValueError` (pas un VDDK), `gzip.BadGzipFile` (une `OSError`) ou `tarfile.TarError` : les trois donnent 422, message sans chemin.
- `_receive_archive` écrit en 0600 : c'est voulu (le VDDK est sous licence).
- `subprocess` et `re` sont déjà importés dans `app.py` (sinon les ajouter).
- Si `_invalidate_cluster_caches` n'existe pas sous ce nom, prendre celle qu'utilise `_res_cli`.

`config/systemd/harvester-ops.service`, à côté de `HARVESTER_OPS_EXPORT_DIR` :

```
    -e HARVESTER_OPS_VDDK_DIR=/var/lib/harvester-ops/vddk \
```

- [ ] **Step 3: tests verts**

Run: `python3 -m pytest tests/api/test_forklift_routes_175.py -q`
Expected: PASS

- [ ] **Step 4: suite complète et commit**

Run: `python3 -m pytest tests/api/ -q`
Expected: tout vert (y compris les tests de contrôle des limites `_rate_limit` et du service systemd, s'il y en a)

```bash
git add web/app.py config/systemd/harvester-ops.service tests/api/test_forklift_routes_175.py
git commit -m "feat(1.75.0): console routes for Forklift, the VDDK store and vCenter sources"
```

---

### Task U3 : l'onglet, sa navigation et la Préparation

**Files:**
- Modify: `web/templates/index.html` (entrée de menu après `data-tab="vmimport"` ligne ~90 ; section après `#tab-vmimport` ligne ~335 ; `<script src="/static/js/forklift.js">` après `vmimport.js` ligne ~1108)
- Modify: `web/static/js/sections.js` (`DEF.forklift`, `MODS`)
- Modify: `web/static/js/app.js:250` (liste des sections)
- Create: `web/static/js/forklift.js`
- Modify: `web/static/js/i18n.js` (5 langues)
- Modify: `web/static/css/style.css`
- Test: `tests/e2e/test_forklift_175.py`

**Interfaces:**
- Consumes: routes de U2 ; `FloatingPanels.open({id, icon, width, height, title, bodyHtml}) -> {el, body}` ; `VMActions.follow(id, into, text, onDone)` ; `Dock.poll()` ; `Sections.open(sec, pane)` ; `Icons.svg(name, {size})` ; `i18n.t(key, params)`.
- Produces (U4) : `window.Forklift = { start(cluster, host), stop(), openInventory(name) }` ; le squelette de rendu `render()` qui aiguille sur `cur.kind` (`prep`, `sources`, `inventory`) ; `post(action, body, doneText, into)` ; `win(id, title, bodyHtml, height)` ; `field(...)`, `opts(...)`.

Menu (même forme que l'entrée VM Import) :

```html
          <a class="tab tab-child" data-tab="forklift" href="#forklift" data-i18n-title="tab.forkliftTip" title="Warm migrations of VMware VMs with Forklift: preparation, vCenter sources, inventory">
            <span class="tab-icon" data-icon="migrate"></span>
            <span class="sidebar-label" data-i18n="tab.forklift">VMware migrations</span>
          </a>
```

Section :

```html
    <!-- v1.75.0 : MIGRATIONS VMWARE (Forklift) -->
    <section id="tab-forklift" class="tab-content" data-section="forklift">
      <div class="shutdown-header">
        <div class="automation-header-text">
          <h1><span data-i18n="tab.forklift">VMware migrations</span> · <span class="cluster-name"></span></h1>
        </div>
        <div class="sub-tabs sub-tabs-inline" role="tablist">
          <button type="button" class="sub-tab tip" role="tab" data-section-tab="prep" data-i18n-title="section.fkPrepTip" title="What Forklift needs on this cluster, step by step"><span class="tab-icon" data-icon="settings"></span> <span data-i18n="section.fkPrep">Preparation</span></button>
          <button type="button" class="sub-tab tip" role="tab" data-section-tab="sources" data-i18n-title="section.fkSourcesTip" title="The vCenter servers Forklift reads VMs from"><span class="tab-icon" data-icon="cloud"></span> <span data-i18n="section.fkSources">vCenter sources</span></button>
          <button type="button" class="sub-tab tip" role="tab" data-section-tab="inventory" data-i18n-title="section.fkInventoryTip" title="VMs, networks and datastores of a vCenter as Forklift sees them"><span class="tab-icon" data-icon="list"></span> <span data-i18n="section.fkInventory">Inventory</span></button>
        </div>
      </div>
      <div class="section-pane" data-pane="prep" hidden><div class="na-host" data-fk="prep"></div></div>
      <div class="section-pane" data-pane="sources" hidden><div class="na-host" data-fk="sources"></div></div>
      <div class="section-pane" data-pane="inventory" hidden><div class="na-host" data-fk="inventory"></div></div>
    </section>
```

Vérifier que les icônes `settings`, `cloud`, `list`, `migrate` existent dans `web/static/js/icons.js` ; sinon prendre une icône existante proche.

`sections.js` :

```javascript
    // v1.75.0 : migrations VMware par Forklift
    forklift: { first: 'prep', panes: { prep: { mod: 'Forklift' }, sources: { mod: 'Forklift' },
                                        inventory: { mod: 'Forklift' } } },
```

et `const MODS = ['NetAdmin', 'Advanced', 'Devices', 'MonLog', 'VMImport', 'Forklift'];`.
`app.js:250` : ajouter `'forklift'` à la liste.

- [ ] **Step 1: test e2e qui échoue**

`tests/e2e/test_forklift_175.py` (même patron que `tests/e2e/test_vmimport_171.py`, lire sa fixture `flask_server` et `context`) :

```python
"""v1.75.0 : l'onglet Migrations VMware dans un navigateur : la Préparation
en trois étapes avec leur état, l'installation et la poussée de l'image
VDDK envoyées sans secret visible, le dépôt d'une archive."""

import json

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect  # noqa: E402

ARCHIVE = "VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz"
READY = {"ready": True, "cert_manager": True, "cert_manager_missing": [], "addon": "ready",
         "addon_message": "the forklift-operator add-on is deployed", "operator": True, "controller": True,
         "components_missing": []}
DATA = {"cluster": "harv-fake", "install": READY, "harvester_addon": False, "bundle": True,
        "vddk": {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "digest": "sha256:" + "4f1c" * 16,
                 "archive": ARCHIVE, "pushed_at": "2026-09-28T20:00:00Z"},
        "registry": {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "host": "172.16.1.11:5005",
                     "plain_http": True, "auth": True},
        "providers": [{"name": "vmwlab", "namespace": "forklift", "url": "https://vmwlab-vc.home.lo/sdk", "ready": True,
                       "message": "ready: vCenter reached, inventory loaded",
                       "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3", "plans": [], "managed": True}],
        "vmimport_sources": [{"namespace": "mig", "name": "vc", "endpoint": "https://vmwlab-vc.home.lo/sdk"}]}
ABSENT = {**DATA, "install": {**READY, "ready": False, "addon": "absent", "addon_message": "the forklift-operator add-on is not declared",
                              "operator": False, "controller": False, "components_missing": ["forklift-api"]},
          "vddk": None, "providers": []}
STORE = {"archives": [{"name": ARCHIVE, "version": "8.0.3", "size": 41626848, "mtime": 1790000000}], "free": 10 ** 11}


def fulfill(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def open_tab(context, flask_server, data):
    context.add_init_script(
        "localStorage.setItem('harvester_ops_language','en');"
        "localStorage.setItem('harvester_ops_current_cluster','harv-fake');"
        "localStorage.setItem('harvester_ops_section_forklift','prep');"
        "localStorage.setItem('harvester_ops_current_tab','forklift');")
    page = context.new_page()
    sent = []

    def writes(route, req):
        sent.append((req.url.split("://")[1].split("/", 1)[1], req.method, req.post_data_json if req.method == "POST" else None))
        fulfill(route, {"action_id": "fk0000000175"}, 202)
    page.route("**/api/forklift/harv-fake", lambda r, q: fulfill(r, data))
    page.route("**/api/forklift/harv-fake/do/**", writes)
    page.route("**/api/forklift-vddk", lambda r, q: fulfill(r, STORE))
    page.route("**/api/stream/fk0000000175", lambda r, q: r.fulfill(
        status=200, content_type="text/event-stream", body='event: end\ndata: {"status": "done"}\n\n'))
    page.goto(flask_server["base_url"], wait_until="domcontentloaded")
    page.wait_for_function("window.Forklift && window.Sections && window.App && App.getCurrentCluster()")
    page.on("dialog", lambda d: d.accept())
    return page, sent


def test_preparation_shows_three_steps_with_their_state(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA)
    steps = page.locator("#tab-forklift [data-fk-step]")
    expect(steps).to_have_count(3)
    expect(steps.nth(0)).to_contain_text("ready")
    expect(steps.nth(1)).to_contain_text("172.16.1.11:5005/harvops/vddk:8.0.3")
    expect(steps.nth(2)).to_contain_text("1")
    for b in page.locator("#tab-forklift button:visible").all():
        assert b.get_attribute("data-tip") or b.get_attribute("title"), b.inner_text()


def test_install_is_offered_when_forklift_is_absent_and_sent_as_an_action(context, flask_server):
    page, sent = open_tab(context, flask_server, ABSENT)
    page.locator('#tab-forklift [data-fk="install"]').click()
    page.wait_for_timeout(500)
    assert sent[0][0] == "api/forklift/harv-fake/do/install"


def test_the_vddk_image_is_pushed_with_harvester_s_registry_credentials(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    form = page.locator('#tab-forklift [data-fk-step="vddk"]')
    expect(form.locator('[name="archive"]')).to_have_value(ARCHIVE)
    expect(form.locator('[name="image"]')).to_have_value("172.16.1.11:5005/harvops/vddk:8.0.3")
    expect(form.locator('[name="use_cluster_auth"]')).to_be_checked()
    form.locator('[data-fk="push-vddk"]').click()
    page.wait_for_timeout(500)
    url, method, body = sent[0]
    assert url == "api/forklift/harv-fake/do/vddk-image"
    assert body == {"archive": ARCHIVE, "image": "172.16.1.11:5005/harvops/vddk:8.0.3", "plain_http": True,
                    "use_cluster_auth": True}
```

Run: `python3 -m pytest tests/e2e/test_forklift_175.py -q`
Expected: FAIL (`window.Forklift` absent)

- [ ] **Step 2: `forklift.js`, squelette et Préparation**

Créer `web/static/js/forklift.js` en calquant la structure de `web/static/js/vmimport.js` (lire le fichier entier d'abord : `start`/`stop`/`load`/`render`, `onClick` qui rejoue un clic arrivé avant les données, `post`, `win`, `field`, `opts`, `follow`). Contenu attendu :

```javascript
/**
 * harvester-ops : migrations VMware par Forklift, onglet d'un cluster (v1.75.0)
 *
 * Trois onglets de la section « Migrations VMware » :
 * - Préparation : Forklift sur le cluster, l'image VDDK, les sources, dans
 *   l'ordre où il faut les faire, chacun avec son état et son geste ;
 * - Sources vCenter : un bloc par fournisseur vSphere de Forklift, ajout et
 *   modification en fenêtre (un vCenter de VM Import se reprend sans
 *   ressaisir son mot de passe, lu par le serveur) ;
 * - Inventaire : ce que Forklift voit d'un vCenter, en lecture seule (les
 *   vagues à chaud viennent avec l'étape suivante).
 * Toute écriture passe par l'outil harvester-forklift, en action suivie.
 */
const Forklift = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 13) => (window.Icons ? Icons.svg(n, { size }) : '');
  const badge = (cls, text, tip) =>
    `<span class="badge ${cls}${tip ? ' tip' : ''}"${tip ? ` data-tip="${esc(tip)}"` : ''}>${esc(text)}</span>`;
  const REFRESH_MS = 10000;
  const KINDS = ['prep', 'sources', 'inventory'];
  const size = (n) => { /* identique à vmimport.js */ };

  let cur = null;     // { cluster, host, kind, data, store, timer, ready, inv }

  // call, getJSON, follow : identiques à vmimport.js

  function start(cluster, host) {
    stop();
    const kind = KINDS.includes(host.dataset.fk) ? host.dataset.fk : 'prep';
    cur = { cluster, host, kind, data: null, store: null, inv: cur && cur.inv && cur.inv.cluster === cluster ? cur.inv : null };
    const newBtn = kind === 'sources'
      ? `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-fk="new-source" data-tip="${esc(tr('fk.t.newSource'))}">${icon('add')} ${esc(tr('fk.newSource'))}</button>` : '';
    host.innerHTML = `<div class="card na-card fk-card">
        <div class="res-tools"><span class="res-count"></span>${newBtn}
          <button type="button" class="btn btn-sm btn-secondary tip" data-fk="refresh" data-tip="${esc(tr('res.refreshTip'))}">${icon('refresh')} ${esc(tr('overview.refresh'))}</button>
        </div>
        <div class="res-feedback" data-fk="feedback"></div>
        <div data-fk="body"><p class="form-hint">${esc(tr('common.loading'))}</p></div></div>`;
    const card = host.querySelector('.fk-card');
    card.addEventListener('click', onClick);
    card.addEventListener('change', onChange);
    // l'inventaire ouvre un tunnel vers le cluster : pas de relecture automatique
    cur.timer = setInterval(() => {
      if (cur && cur.kind !== 'inventory' && cur.host.isConnected && !cur.host.closest('[hidden]')) load();
    }, REFRESH_MS);
    cur.ready = load();
    return cur.ready;
  }

  function stop() { if (cur && cur.timer) clearInterval(cur.timer); /* garder cur.inv pour le retour sur l'onglet */ }

  async function load() {
    if (!cur) return;
    const c = cur;
    const [d, store] = await Promise.all([getJSON(`/api/forklift/${enc(c.cluster)}`),
                                          c.kind === 'prep' ? getJSON('/api/forklift-vddk') : Promise.resolve(c.store)]);
    if (c !== cur) return;
    c.data = d;
    c.store = store;
    render();
  }
```

Note sur `stop()` : `Sections` appelle `stop()` de tous les modules avant `start()` ; `cur` doit être remis à `null` comme dans `VMImport.stop()` sauf l'état d'inventaire choisi, à garder dans une variable de module `lastInv` (source et sorte choisies), pas dans `cur`. Écrire :

```javascript
  let lastInv = null;   // { cluster, source, kind, q, warm } : retrouvé au retour sur l'onglet
  function stop() {
    if (cur && cur.timer) clearInterval(cur.timer);
    cur = null;
  }
```

et dans `start`, `cur = { cluster, host, kind, data: null, store: null }` ; l'inventaire (U4) lit/écrit `lastInv`.

`render()` :

```javascript
  function render() {
    if (!cur || !cur.host.isConnected) return;
    const body = cur.host.querySelector('[data-fk="body"]');
    const d = cur.data;
    if (!d || d.error || d.unreachable) {
      body.innerHTML = `<div class="sto-finding sev-critical"><div class="sto-finding-title">${esc((d && d.error) || tr('fabric.unreachable'))}</div></div>`;
      return;
    }
    if (cur.kind === 'prep') body.innerHTML = prepView(d);
    else if (cur.kind === 'sources') body.innerHTML = sourcesView(d);   // U4
    else inventoryView(body, d);                                         // U4
  }
```

En U3, `sourcesView` et `inventoryView` rendent un simple `<p class="form-hint">` provisoire (U4 les remplit). Préparation :

```javascript
  function installLine(st) {
    const parts = [
      ['cert-manager', st.cert_manager, tr('fk.t.certManager')],
      [tr('fk.addon'), st.addon === 'ready', st.addon_message],
      [tr('fk.operator'), st.operator, tr('fk.t.operator')],
      [tr('fk.controller'), st.controller, tr('fk.t.controller')],
      [tr('fk.components'), st.controller && !st.components_missing.length,
       st.components_missing.length ? tr('fk.missing', { list: st.components_missing.join(', ') }) : tr('fk.t.components')],
    ];
    return parts.map(([label, ok, tip]) => `<span class="fk-part tip" data-tip="${esc(tip)}">${icon(ok ? 'ok' : 'fail', 12)} ${esc(label)}</span>`).join(' ');
  }

  function stepBox(id, n, title, stateHtml, bodyHtml) {
    return `<div class="fk-step" data-fk-step="${id}"><div class="fk-step-head"><span class="fk-step-n">${n}</span>
      <b>${esc(title)}</b> ${stateHtml}</div>${bodyHtml}</div>`;
  }

  function prepView(d) {
    const st = d.install;
    const addonState = { ready: ['ok', tr('fk.st.ready')], deploying: ['warn', tr('fk.st.deploying')],
                         failed: ['fail', tr('fk.st.failed')], disabled: ['warn', tr('fk.st.disabled')],
                         absent: ['warn', tr('fk.st.absent')] };
    const [cls, txt] = st.ready ? ['ok', tr('fk.st.ready')] : (addonState[st.addon] || ['warn', st.addon]);
    const canInstall = st.cert_manager || d.bundle;
    const one = stepBox('forklift', 1, tr('fk.step.forklift'), badge(cls, txt, st.addon_message),
      `<p class="form-hint">${esc(tr('fk.forkliftHint'))}</p><div class="fk-parts">${installLine(st)}</div>
       ${d.harvester_addon ? `<p class="form-hint">${esc(tr('fk.harvesterAddon'))}</p>` : ''}
       ${st.ready ? '' : `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-fk="install" ${canInstall ? '' : 'disabled'}
          data-tip="${esc(canInstall ? tr('fk.t.install') : tr('fk.t.noBundle'))}">${icon('download')} ${esc(st.addon === 'absent' ? tr('fk.install') : tr('fk.resume'))}</button>`}`);
    const v = d.vddk;
    const archives = (cur.store && cur.store.archives) || [];
    const pick = (v && archives.some(a => a.name === v.archive)) ? v.archive : (archives[0] && archives[0].name) || '';
    const two = stepBox('vddk', 2, tr('fk.step.vddk'),
      v ? badge('ok', v.image, tr('fk.t.vddkDone', { digest: v.digest.slice(0, 19), when: v.pushed_at })) : badge('warn', tr('fk.st.todo')),
      `<p class="form-hint">${esc(tr('fk.vddkHint'))}</p>
       <div class="fk-form">
         ${field('archive', tr('fk.f.archive'), `<select name="archive">${archives.length ? opts(archives.map(a => [a.name, `${a.name} (${size(a.size)})`]), pick) : `<option value="">${esc(tr('fk.noArchive'))}</option>`}</select>`, tr('fk.t.archive'))}
         <div class="fk-upload">
           <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-fk="upload-vddk" data-tip="${esc(tr('fk.t.upload'))}">${icon('upload')} ${esc(tr('fk.upload'))}</button>
           <input type="file" accept=".tar.gz" data-fk="upload-file" hidden>
           <span class="form-hint" data-fk="upload-line"></span>
         </div>
         ${field('image', tr('fk.f.image'), `<input name="image" value="${esc((v && v.image) || d.registry.image)}" placeholder="registry.lan/harvops/vddk:8.0.3">`,
                 d.registry.host ? tr('fk.t.imageHint', { host: d.registry.host }) : tr('fk.t.image'))}
         <label class="fk-check tip" data-tip="${esc(tr('fk.t.plainHttp'))}"><input type="checkbox" name="plain_http" ${d.registry.plain_http ? 'checked' : ''}> ${esc(tr('fk.f.plainHttp'))}</label>
         ${d.registry.auth ? `<label class="fk-check tip" data-tip="${esc(tr('fk.t.clusterAuth', { host: d.registry.host }))}"><input type="checkbox" name="use_cluster_auth" checked> ${esc(tr('fk.f.clusterAuth'))}</label>` : ''}
         <div data-fk="reg-creds" ${d.registry.auth ? 'hidden' : ''}>
           ${field('username', tr('fk.f.regUser'), '<input name="username" autocomplete="off">', tr('fk.t.regUser'))}
           ${field('password', tr('fk.f.regPassword'), '<input name="password" type="password" autocomplete="new-password">', tr('fk.t.regPassword'))}
         </div>
         <button type="button" class="btn btn-sm btn-primary tip needs-admin" data-fk="push-vddk" ${archives.length ? '' : 'disabled'} data-tip="${esc(tr('fk.t.push'))}">${icon('upload')} ${esc(tr('fk.push'))}</button>
       </div>`);
    const ready = d.providers.filter(p => p.ready === true).length;
    const three = stepBox('sources', 3, tr('fk.step.sources'),
      d.providers.length ? badge(ready ? 'ok' : 'warn', tr('fk.st.sources', { ready, total: d.providers.length })) : badge('warn', tr('fk.st.todo')),
      `<p class="form-hint">${esc(tr('fk.sourcesHint'))}</p>
       <button type="button" class="btn btn-sm btn-secondary tip" data-fk="goto-sources" data-tip="${esc(tr('fk.t.gotoSources'))}">${icon('cloud')} ${esc(tr('section.fkSources'))}</button>`);
    return one + two + three;
  }
```

Gestes (dans `onClick`, qui rejoue un clic arrivé avant les données comme `VMImport.onClick`) :

```javascript
    if (act === 'refresh') return load();
    if (act === 'install') return post('install', {}, tr('fk.done.install'));
    if (act === 'goto-sources') return Sections.open('forklift', 'sources');
    if (act === 'goto-prep') return Sections.open('forklift', 'prep');
    if (act === 'upload-vddk') return cur.host.querySelector('[data-fk="upload-file"]').click();
    if (act === 'push-vddk') {
      const box = b.closest('[data-fk-step="vddk"]');
      const val = (n) => { const x = box.querySelector(`[name="${n}"]`); return x ? x.value.trim() : ''; };
      const chk = (n) => { const x = box.querySelector(`[name="${n}"]`); return !!(x && x.checked); };
      const body = { archive: val('archive'), image: val('image'), plain_http: chk('plain_http') };
      if (chk('use_cluster_auth')) body.use_cluster_auth = true;
      else if (val('username')) { body.username = val('username'); body.password = box.querySelector('[name="password"]').value; }
      return post('vddk-image', body, tr('fk.done.push', { image: body.image }));
    }
```

`onChange` : la case `use_cluster_auth` montre ou cache `[data-fk="reg-creds"]` ; le champ `upload-file` lance `upload(file)` :

```javascript
  function upload(file) {
    const line = cur.host.querySelector('[data-fk="upload-line"]');
    if (!/^VMware-vix-disklib-\d+\.\d+\.\d+-\d+\.x86_64\.tar\.gz$/.test(file.name)) {
      line.innerHTML = `<span class="res-error">${esc(tr('fk.badArchive'))}</span>`;
      return;
    }
    const c = cur;
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', `/api/forklift-vddk/${enc(file.name)}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => { line.textContent = tr('fk.uploading', { pct: Math.floor(100 * e.loaded / (e.total || file.size)) }); };
    xhr.upload.onload = () => { line.textContent = tr('fk.verifying'); };
    xhr.onload = () => {
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch { /* sans corps */ }
      if (window.Dock && Dock.poll) Dock.poll();
      line.innerHTML = xhr.status === 201 ? `${icon('ok')} ${esc(tr('fk.uploaded', { name: file.name }))}`
                                          : `<span class="res-error">${esc(d.error || `HTTP ${xhr.status}`)}</span>`;
      if (c === cur) load();
    };
    xhr.onerror = () => { line.innerHTML = `<span class="res-error">${esc(tr('fk.uploadFailed'))}</span>`; };
    xhr.send(file);
  }
```

`post`, `follow`, `win`, `field`, `opts` : recopier ceux de `vmimport.js` en remplaçant `/api/vmimport/` par `/api/forklift/`, `data-vi` par `data-fk`, et le préfixe d'id de fenêtre `vi-` par `fk-`. Fin du fichier : `return { start, stop, openInventory };` (U4 définit `openInventory` ; en U3, une version qui fait `Sections.open('forklift', 'inventory')`) puis `window.Forklift = Forklift;`.

- [ ] **Step 3: i18n (5 langues) et style**

Clés à ajouter dans `en` et `fr` ci-dessous ; les mêmes clés en `de`, `es`, `it`, traduites (même ton que les clés `vi.*` voisines). Placer chaque bloc juste après les clés `vi.*` de la langue.

EN :

```javascript
    'tab.forklift': 'VMware migrations',
    'tab.forkliftTip': 'Warm migrations of VMware VMs with Forklift: preparation, vCenter sources, inventory',
    'section.fkPrep': 'Preparation',
    'section.fkPrepTip': 'What Forklift needs on this cluster, step by step',
    'section.fkSources': 'vCenter sources',
    'section.fkSourcesTip': 'The vCenter servers Forklift reads VMs from',
    'section.fkInventory': 'Inventory',
    'section.fkInventoryTip': 'VMs, networks and datastores of a vCenter as Forklift sees them',
    'fk.step.forklift': 'Forklift on this cluster',
    'fk.step.vddk': 'VDDK image',
    'fk.step.sources': 'vCenter sources',
    'fk.forkliftHint': 'Harvester 1.9 does not ship Forklift: the console installs the experimental forklift-operator add-on, cert-manager if missing (from its Cluster API bundle, without Internet) and the Forklift controller. About 2 minutes.',
    'fk.harvesterAddon': 'This cluster has Harvester\'s own forklift-operator add-on: the console only enables it, it never rewrites it.',
    'fk.addon': 'add-on', 'fk.operator': 'operator', 'fk.controller': 'controller', 'fk.components': 'components',
    'fk.missing': 'Not running yet: {list}',
    'fk.t.certManager': 'cert-manager signs the certificates of Forklift\'s services',
    'fk.t.operator': 'The operator that deploys Forklift from its controller object',
    'fk.t.controller': 'The ForkliftController object that asks for the Forklift services',
    'fk.t.components': 'API, controller, validation and volume populator of Forklift',
    'fk.install': 'Install Forklift', 'fk.resume': 'Resume the installation',
    'fk.t.install': 'Install cert-manager if missing, the forklift-operator add-on and the Forklift controller; followed in the dock',
    'fk.t.noBundle': 'cert-manager is missing and the console has no Cluster API bundle to take it from: add one in Automation > Cluster API',
    'fk.st.ready': 'ready', 'fk.st.deploying': 'deploying', 'fk.st.failed': 'failed', 'fk.st.disabled': 'disabled',
    'fk.st.absent': 'not installed', 'fk.st.todo': 'to do', 'fk.st.sources': '{ready} of {total} ready',
    'fk.vddkHint': 'VMware\'s disk library cannot ship with the console: you provide VMware\'s archive once, the console builds the image and pushes it to a registry this cluster can pull from.',
    'fk.f.archive': 'VDDK archive', 'fk.t.archive': 'An archive already given to the console; it serves every cluster',
    'fk.noArchive': 'no archive yet', 'fk.upload': 'Give an archive…',
    'fk.t.upload': 'Upload VMware-vix-disklib-<version>-<build>.x86_64.tar.gz, downloaded from VMware (Broadcom)',
    'fk.badArchive': 'Expected: VMware-vix-disklib-<version>-<build>.x86_64.tar.gz',
    'fk.uploading': 'Sending: {pct} %', 'fk.verifying': 'Checking that this is the VDDK…',
    'fk.uploaded': '{name} kept by the console', 'fk.uploadFailed': 'The upload stopped',
    'fk.f.image': 'Image to push', 'fk.t.image': 'registry/path:tag; the cluster must be able to pull from this registry (Advanced > containerd-registry)',
    'fk.t.imageHint': 'Proposed from this cluster\'s containerd-registry setting ({host})',
    'fk.f.plainHttp': 'The registry speaks plain HTTP', 'fk.t.plainHttp': 'Tick for a registry without TLS',
    'fk.f.clusterAuth': 'Use the credentials Harvester has for this registry',
    'fk.t.clusterAuth': 'The user and password of {host} in the containerd-registry setting, read by the server, never shown',
    'fk.f.regUser': 'Registry user', 'fk.t.regUser': 'An account allowed to push to this registry',
    'fk.f.regPassword': 'Registry password', 'fk.t.regPassword': 'Sent to the tool through a private file, never kept',
    'fk.push': 'Build and push', 'fk.t.push': 'Build the VDDK image from the archive and push it; the cluster remembers it',
    'fk.t.vddkDone': 'Pushed on {when}, {digest}',
    'fk.sourcesHint': 'A vCenter server Forklift can read VMs from, with an account allowed to export them.',
    'fk.t.gotoSources': 'Open the vCenter sources of this cluster',
    'fk.done.install': 'Forklift installed', 'fk.done.push': '{image} pushed',
```

FR :

```javascript
    'tab.forklift': 'Migrations VMware',
    'tab.forkliftTip': 'Migrations à chaud de VMs VMware par Forklift : préparation, sources vCenter, inventaire',
    'section.fkPrep': 'Préparation',
    'section.fkPrepTip': 'Ce qu\'il faut à Forklift sur ce cluster, étape par étape',
    'section.fkSources': 'Sources vCenter',
    'section.fkSourcesTip': 'Les serveurs vCenter dont Forklift lit les VMs',
    'section.fkInventory': 'Inventaire',
    'section.fkInventoryTip': 'VMs, réseaux et datastores d\'un vCenter tels que Forklift les voit',
    'fk.step.forklift': 'Forklift sur ce cluster',
    'fk.step.vddk': 'Image VDDK',
    'fk.step.sources': 'Sources vCenter',
    'fk.forkliftHint': 'Harvester 1.9 ne livre pas Forklift : la console installe l\'add-on expérimental forklift-operator, cert-manager s\'il manque (depuis son paquet Cluster API, sans Internet) et le contrôleur Forklift. Environ 2 minutes.',
    'fk.harvesterAddon': 'Ce cluster a l\'add-on forklift-operator de Harvester : la console se contente de l\'activer, elle ne le réécrit jamais.',
    'fk.addon': 'add-on', 'fk.operator': 'opérateur', 'fk.controller': 'contrôleur', 'fk.components': 'composants',
    'fk.missing': 'Pas encore en marche : {list}',
    'fk.t.certManager': 'cert-manager signe les certificats des services de Forklift',
    'fk.t.operator': 'L\'opérateur qui déploie Forklift d\'après son objet contrôleur',
    'fk.t.controller': 'L\'objet ForkliftController qui demande les services de Forklift',
    'fk.t.components': 'API, contrôleur, validation et peuplement des volumes de Forklift',
    'fk.install': 'Installer Forklift', 'fk.resume': 'Reprendre l\'installation',
    'fk.t.install': 'Installe cert-manager s\'il manque, l\'add-on forklift-operator et le contrôleur Forklift ; suivi dans le dock',
    'fk.t.noBundle': 'cert-manager manque et la console n\'a pas de paquet Cluster API d\'où le tirer : en ajouter un dans Automatisation > Cluster API',
    'fk.st.ready': 'prêt', 'fk.st.deploying': 'en cours de déploiement', 'fk.st.failed': 'en échec', 'fk.st.disabled': 'désactivé',
    'fk.st.absent': 'pas installé', 'fk.st.todo': 'à faire', 'fk.st.sources': '{ready} sur {total} prêtes',
    'fk.vddkHint': 'La bibliothèque de disques de VMware ne peut pas être livrée avec la console : vous fournissez une fois l\'archive de VMware, la console en fait l\'image et la pousse dans un registre que ce cluster peut tirer.',
    'fk.f.archive': 'Archive VDDK', 'fk.t.archive': 'Une archive déjà confiée à la console ; elle sert à tous les clusters',
    'fk.noArchive': 'aucune archive encore', 'fk.upload': 'Déposer une archive…',
    'fk.t.upload': 'Déposer VMware-vix-disklib-<version>-<build>.x86_64.tar.gz, téléchargée chez VMware (Broadcom)',
    'fk.badArchive': 'Attendu : VMware-vix-disklib-<version>-<build>.x86_64.tar.gz',
    'fk.uploading': 'Envoi : {pct} %', 'fk.verifying': 'Vérification que c\'est bien le VDDK…',
    'fk.uploaded': '{name} gardée par la console', 'fk.uploadFailed': 'Le dépôt s\'est interrompu',
    'fk.f.image': 'Image à pousser', 'fk.t.image': 'registre/chemin:étiquette ; le cluster doit pouvoir tirer de ce registre (Advanced > containerd-registry)',
    'fk.t.imageHint': 'Proposée d\'après le réglage containerd-registry de ce cluster ({host})',
    'fk.f.plainHttp': 'Le registre parle en HTTP simple', 'fk.t.plainHttp': 'À cocher pour un registre sans TLS',
    'fk.f.clusterAuth': 'Utiliser les identifiants que Harvester a pour ce registre',
    'fk.t.clusterAuth': 'L\'utilisateur et le mot de passe de {host} dans le réglage containerd-registry, lus par le serveur, jamais affichés',
    'fk.f.regUser': 'Utilisateur du registre', 'fk.t.regUser': 'Un compte autorisé à pousser dans ce registre',
    'fk.f.regPassword': 'Mot de passe du registre', 'fk.t.regPassword': 'Transmis à l\'outil par un fichier privé, jamais gardé',
    'fk.push': 'Construire et pousser', 'fk.t.push': 'Construit l\'image VDDK depuis l\'archive et la pousse ; le cluster la retient',
    'fk.t.vddkDone': 'Poussée le {when}, {digest}',
    'fk.sourcesHint': 'Un serveur vCenter dont Forklift peut lire les VMs, avec un compte autorisé à les exporter.',
    'fk.t.gotoSources': 'Ouvrir les sources vCenter de ce cluster',
    'fk.done.install': 'Forklift installé', 'fk.done.push': '{image} poussée',
```

Relire les textes FR et EN : ni tiret cadratin, ni flèche Unicode (les `>` de chemin de menu sont des chevrons ASCII).

Style (`web/static/css/style.css`, après les règles `.vi-net`) :

```css
/* v1.75.0 : onglet Migrations VMware */
.fk-step { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }
.fk-step-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
.fk-step-n { display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 50%;
             background: var(--accent); color: var(--bg); font-weight: 600; font-size: 12px; }
.fk-parts { display: flex; flex-wrap: wrap; gap: 10px; margin: 6px 0; }
.fk-part { display: inline-flex; align-items: center; gap: 4px; }
.fk-form { display: grid; gap: 6px; max-width: 640px; }
.fk-upload { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.fk-check { display: flex; align-items: center; gap: 6px; }
```

Vérifier les noms de variables CSS réellement utilisés (`--border`, `--bg`...) dans `style.css` et prendre les existants.

- [ ] **Step 4: tests verts**

Run: `python3 -m pytest tests/e2e/test_forklift_175.py tests/e2e/test_sections.py tests/e2e/test_vmimport_171.py -q`
Expected: PASS
Run: `python3 -m pytest tests/api/ -q`
Expected: tout vert (parité i18n, balisage, pas d'`EventSource` brut)

- [ ] **Step 5: commit**

```bash
git add web/templates/index.html web/static/js/forklift.js web/static/js/sections.js web/static/js/app.js \
        web/static/js/i18n.js web/static/css/style.css tests/e2e/test_forklift_175.py
git commit -m "feat(1.75.0): VMware migrations tab with its preparation steps"
```

---

### Task U4 : sources vCenter, inventaire, lien depuis VM Import

**Files:**
- Modify: `web/static/js/forklift.js` (`sourcesView`, fenêtre de source, `inventoryView`, `openInventory`)
- Modify: `web/static/js/vmimport.js` (lien dans l'onglet VMware)
- Modify: `web/static/js/i18n.js` (5 langues), `web/static/css/style.css`
- Test: `tests/e2e/test_forklift_175.py` (ajouts), `tests/e2e/test_vmimport_171.py` (lien)

**Interfaces:**
- Consumes: U2 (`provider-apply` accepte `{spec: {name, url, user, password, cacert | insecure, vddk_image, from_vmimport?: {namespace, name}, keep_credentials?}}`, `provider-delete {name}`, `GET .../inventory/<name>/<kind>` -> `{rows}` ou `{error}` 502), U3 (`render`, `post`, `win`, `field`, `opts`, `lastInv`).
- Produces: `Forklift.openInventory(name)`.

Lignes d'inventaire (de `hf.inventory_rows`) : VM `{id, name, path, power, cbt, cpus, memory_mib, guest, disks: [{datastore, capacity}], networks: [id], concerns: [{category, label}]}` ; réseau `{id, name, path}` ; datastore `{id, name, path, capacity, free}`.

- [ ] **Step 1: tests e2e qui échouent**

Ajouter à `tests/e2e/test_forklift_175.py` :

```python
VMS = json.load(open(__import__("pathlib").Path(__file__).resolve().parents[1] / "api" / "fixtures" / "forklift_inventory_vms_175.json"))


def rows_of(vms):
    """Les lignes que rend l'outil (hf.inventory_rows), depuis le relevé réel."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))
    import hv_forklift as hf
    return hf.inventory_rows("vms", vms)


def test_a_source_block_says_its_state_and_offers_inventory_edit_delete(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    page.evaluate("Sections.open('forklift', 'sources')")
    block = page.locator('#tab-forklift [data-fk-source="vmwlab"]')
    expect(block).to_contain_text("https://vmwlab-vc.home.lo/sdk")
    expect(block.locator('[data-fk="del-source"]')).to_be_enabled()
    block.locator('[data-fk="del-source"]').click()
    page.wait_for_timeout(400)
    assert sent[-1][0] == "api/forklift/harv-fake/do/provider-delete" and sent[-1][2] == {"name": "vmwlab"}


def test_a_source_used_by_a_plan_cannot_be_deleted(context, flask_server):
    used = {**DATA, "providers": [{**DATA["providers"][0], "plans": ["forklift/wave-1"]}]}
    page, _ = open_tab(context, flask_server, used)
    page.evaluate("Sections.open('forklift', 'sources')")
    expect(page.locator('#tab-forklift [data-fk-source="vmwlab"] [data-fk="del-source"]')).to_be_disabled()


def test_a_vcenter_of_vm_import_is_taken_without_retyping_the_password(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    page.evaluate("Sections.open('forklift', 'sources')")
    page.locator('#tab-forklift [data-fk="new-source"]').click()
    form = page.locator(".floating-panel .of-form").last
    form.locator('[name="from"]').select_option("mig/vc")
    expect(form.locator('[name="password"]')).to_be_hidden()
    form.locator('[name="name"]').fill("vmwlab2")
    form.locator('button[type="submit"]').click()
    page.wait_for_timeout(400)
    url, _, body = sent[-1]
    assert url == "api/forklift/harv-fake/do/provider-apply"
    assert body == {"spec": {"name": "vmwlab2", "from_vmimport": {"namespace": "mig", "name": "vc"},
                             "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3"}}


def test_a_typed_vcenter_goes_with_its_certificate_choice(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    page.evaluate("Sections.open('forklift', 'sources')")
    page.locator('#tab-forklift [data-fk="new-source"]').click()
    form = page.locator(".floating-panel .of-form").last
    form.locator('[name="name"]').fill("vc2")
    form.locator('[name="url"]').fill("vc2.lan")
    form.locator('[name="user"]').fill("administrator@vsphere.local")
    form.locator('[name="password"]').fill("pw")
    form.locator('[name="tls"][value="insecure"]').check()
    form.locator('button[type="submit"]').click()
    page.wait_for_timeout(400)
    assert sent[-1][2] == {"spec": {"name": "vc2", "url": "vc2.lan", "user": "administrator@vsphere.local", "password": "pw",
                                    "insecure": True, "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3"}}


def test_editing_a_source_keeps_its_password_unless_retyped(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    page.evaluate("Sections.open('forklift', 'sources')")
    page.locator('#tab-forklift [data-fk-source="vmwlab"] [data-fk="edit-source"]').click()
    form = page.locator(".floating-panel .of-form").last
    expect(form.locator('[name="name"]')).to_have_attribute("readonly", "")
    form.locator('button[type="submit"]').click()
    page.wait_for_timeout(400)
    spec = sent[-1][2]["spec"]
    assert spec["keep_credentials"] is True and spec["password"] == "" and spec["name"] == "vmwlab"
    assert "insecure" not in spec and "cacert" not in spec   # « garder le réglage actuel » : le serveur reprend le TLS du secret


def test_the_inventory_shows_warm_capability_and_forklift_s_concerns(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA)
    page.route("**/api/forklift/harv-fake/inventory/vmwlab/vms", lambda r, q: fulfill(r, {"rows": rows_of(VMS)}))
    page.evaluate("Forklift.openInventory('vmwlab')")
    table = page.locator('#tab-forklift [data-fk="inv-table"]')
    expect(table.locator("tbody tr")).to_have_count(4)
    vc = table.locator('tr[data-vm="vmwlab-vc"]')
    expect(vc).to_contain_text("CBT")
    expect(vc.locator('[data-fk-warm="no"]')).to_have_count(1)
    expect(table.locator('tr[data-vm="vmwlab-src-1"] [data-fk-warm="yes"]')).to_have_count(1)
    page.locator('#tab-forklift [name="warm_only"]').check()
    expect(table.locator("tbody tr")).to_have_count(3)
    page.locator('#tab-forklift [name="q"]').fill("src-3")
    expect(table.locator("tbody tr")).to_have_count(1)


def test_an_inventory_error_is_said(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA)
    page.route("**/api/forklift/harv-fake/inventory/vmwlab/vms",
               lambda r, q: fulfill(r, {"error": "the inventory service answered 503"}, 502))
    page.evaluate("Forklift.openInventory('vmwlab')")
    expect(page.locator("#tab-forklift .res-error")).to_contain_text("503")
```

Et dans `tests/e2e/test_vmimport_171.py`, un test : l'onglet VMware de VM Import porte un bouton `[data-vi="open-forklift"]` qui ouvre `#tab-forklift` (lire le test existant pour la façon d'ouvrir l'onglet VMware, et router `**/api/forklift/harv-fake` et `**/api/forklift-vddk` vers des réponses vides valides pour que l'onglet s'affiche).

Run: `python3 -m pytest tests/e2e/test_forklift_175.py -q`
Expected: FAIL sur les nouveaux tests

- [ ] **Step 2: sources**

```javascript
  function sourcesView(d) {
    if (!d.install.ready) {
      return `<div class="sto-finding sev-action"><div class="sto-finding-title">${icon('warn')} ${esc(tr('fk.needInstall'))}</div>
        <button type="button" class="btn btn-sm btn-secondary tip" data-fk="goto-prep" data-tip="${esc(tr('fk.t.gotoPrep'))}">${icon('settings')} ${esc(tr('section.fkPrep'))}</button></div>`;
    }
    cur.host.querySelector('.res-count').textContent = tr('fk.count', { n: d.providers.length });
    if (!d.providers.length) return `<p class="form-hint">${esc(tr('fk.noSource'))}</p>`;
    const state = (p) => (p.ready === true ? badge('ok', tr('fk.src.ready'), p.message)
      : p.ready === false ? badge('fail', tr('fk.src.refused'), p.message) : badge('warn', tr('fk.src.checking'), p.message));
    return `<div class="fk-sources">${d.providers.map(p => `<div class="fk-source" data-fk-source="${esc(p.name)}">
        <div class="fk-step-head"><b>${esc(p.name)}</b> ${state(p)}</div>
        <div class="form-hint">${esc(p.url)}</div>
        ${p.ready === false ? `<div class="res-error">${esc(p.message)}</div>` : ''}
        <div class="form-hint">${esc(tr('fk.src.vddk', { image: p.vddk_image || tr('fk.src.noVddk') }))}</div>
        <div class="form-hint">${esc(tr('fk.src.plans', { n: p.plans.length }))}</div>
        <div class="fk-source-actions">
          <button type="button" class="btn btn-sm btn-secondary tip" data-fk="inv-source" data-name="${esc(p.name)}" ${p.ready === true ? '' : 'disabled'} data-tip="${esc(tr('fk.t.inventory'))}">${icon('list')} ${esc(tr('section.fkInventory'))}</button>
          <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-fk="edit-source" data-name="${esc(p.name)}" ${p.managed ? '' : 'disabled'} data-tip="${esc(p.managed ? tr('fk.t.edit') : tr('fk.t.notManaged'))}">${icon('edit')} ${esc(tr('fk.edit'))}</button>
          <button type="button" class="btn btn-sm btn-danger tip needs-admin" data-fk="del-source" data-name="${esc(p.name)}" ${p.plans.length ? 'disabled' : ''} data-tip="${esc(p.plans.length ? tr('fk.t.delUsed', { plans: p.plans.join(', ') }) : tr('fk.t.del'))}">${icon('trash')} ${esc(tr('fk.del'))}</button>
        </div></div>`).join('')}</div>`;
  }
```

Gestes dans `onClick` :

```javascript
    if (act === 'new-source') return sourceForm(null);
    const name = b.dataset.name;
    const prov = name && (cur.data.providers || []).find(p => p.name === name);
    if (act === 'edit-source' && prov) return sourceForm(prov);
    if (act === 'del-source' && prov) {
      if (!confirm(tr('fk.confirm.del', { name }))) return;
      return post('provider-delete', { name }, tr('ml.done.delete', { name }));
    }
    if (act === 'inv-source' && prov) return openInventory(name);
```

Fenêtre :

```javascript
  function sourceForm(p) {
    const edit = !!p;
    const d = cur.data;
    const vddk = (p && p.vddk_image) || (d.vddk && d.vddk.image) || '';
    const froms = edit ? [] : d.vmimport_sources || [];
    const form = win(`fk-src-${cur.cluster}-${edit ? p.name : 'new'}`, edit ? tr('fk.editSource', { name: p.name }) : tr('fk.newSource'),
      `<p class="form-hint">${esc(tr('fk.sourceHint'))}</p>
      ${froms.length ? field('from', tr('fk.f.from'), `<select name="from"><option value="">${esc(tr('fk.from.none'))}</option>${
        froms.map(s => `<option value="${esc(`${s.namespace}/${s.name}`)}">${esc(`${s.namespace}/${s.name} (${s.endpoint})`)}</option>`).join('')}</select>`, tr('fk.t.from')) : ''}
      ${field('name', tr('bk.f.name'), `<input name="name" required value="${esc(edit ? p.name : '')}" ${edit ? 'readonly' : ''}>`, tr('fk.t.name'))}
      <div data-fk-typed>
        ${field('url', tr('fk.f.url'), `<input name="url" required placeholder="vcenter.lan" value="${esc(edit ? p.url : '')}">`, tr('fk.t.url'))}
        ${field('user', tr('vi.f.user'), '<input name="user" autocomplete="off" placeholder="administrator@vsphere.local">', edit ? tr('fk.t.userKeep') : tr('fk.t.user'))}
        ${field('password', tr('vi.f.password'), `<input name="password" type="password" autocomplete="new-password" ${edit ? `placeholder="${esc(tr('fk.unchanged'))}"` : 'required'}>`, edit ? tr('fk.t.passwordKeep') : tr('fk.t.password'))}
        <fieldset class="fk-tls"><legend>${esc(tr('fk.f.tls'))}</legend>
          ${edit ? `<label class="fk-check tip" data-tip="${esc(tr('fk.t.tlsKeep'))}"><input type="radio" name="tls" value="keep" checked> ${esc(tr('fk.tls.keep'))}</label>` : ''}
          <label class="fk-check tip" data-tip="${esc(tr('fk.t.tlsCa'))}"><input type="radio" name="tls" value="ca" ${edit ? '' : 'checked'}> ${esc(tr('fk.tls.ca'))}</label>
          <label class="fk-check tip" data-tip="${esc(tr('fk.t.tlsInsecure'))}"><input type="radio" name="tls" value="insecure"> ${esc(tr('fk.tls.insecure'))}</label>
          ${field('cacert', tr('vi.f.ca'), '<textarea name="cacert" rows="4" class="adv-code" placeholder="-----BEGIN CERTIFICATE-----"></textarea>', tr('fk.t.cacert'))}
        </fieldset>
      </div>
      <p class="form-hint" data-fk-from-hint hidden>${esc(tr('fk.fromHint'))}</p>
      ${field('vddk_image', tr('fk.f.vddk'), `<input name="vddk_image" value="${esc(vddk)}">`, tr('fk.t.vddk'))}`, 600);
    const sync = () => {
      const from = form.querySelector('[name="from"]');
      const on = !!(from && from.value);
      form.querySelector('[data-fk-typed]').hidden = on;
      form.querySelector('[data-fk-from-hint]').hidden = !on;
      form.querySelectorAll('[data-fk-typed] [required]').forEach(x => { x.disabled = on; });
      const ca = form.querySelector('[name="tls"]:checked').value === 'ca';
      form.querySelector('[data-f="cacert"]').hidden = !ca;
      if (on && !form.querySelector('[name="name"]').value) form.querySelector('[name="name"]').value = from.value.split('/')[1];
    };
    form.addEventListener('change', sync);
    sync();
    submitWith(form, (f) => {
      const v = (n) => { const x = f.querySelector(`[name="${n}"]`); return x ? x.value.trim() : ''; };
      const spec = { name: v('name') };
      const from = v('from');
      if (from) {
        const [namespace, sname] = from.split('/');
        spec.from_vmimport = { namespace, name: sname };
      } else {
        spec.url = v('url');
        spec.user = v('user');
        spec.password = f.querySelector('[name="password"]').value;
        const tls = f.querySelector('[name="tls"]:checked').value;
        if (tls === 'insecure') spec.insecure = true;
        else if (tls === 'ca') { if (v('cacert')) spec.cacert = v('cacert'); else throw new Error(tr('fk.needCa')); }
        // « keep » : ni cacert ni insecure, le serveur reprend le réglage TLS du secret du fournisseur
        if (edit) spec.keep_credentials = true;
      }
      if (v('vddk_image')) spec.vddk_image = v('vddk_image');
      return spec;
    }, 'provider-apply', (s) => tr('fk.done.source', { name: s.name }));
  }
```

En modification, le secret n'est pas relu pour l'affichage : l'option « Garder le réglage actuel » est cochée par défaut et n'envoie ni `cacert` ni `insecure` ; le serveur reprend alors `cacert` et `insecureSkipVerify` du secret du fournisseur (règle posée par le correctif de U2).

`submitWith` : recopié de `vmimport.js` (il appelle `post(action, { spec }, ...)`).

- [ ] **Step 3: inventaire**

```javascript
  const CONCERN = () => ({
    'Changed Block Tracking (CBT) not enabled': tr('fk.c.cbt'),
    'Empty Host Name': tr('fk.c.hostName'),
    'Unsupported operating system detected': tr('fk.c.os'),
    'CPU/Memory hotplug detected': tr('fk.c.hotplug'),
    'Disk serial numbers may be truncated': tr('fk.c.serial'),
    'Shareable disk detected': tr('fk.c.shareable'),
    'RDM disk detected': tr('fk.c.rdm'),
    'VM snapshot detected': tr('fk.c.snapshot'),
  });
  const concernText = (c) => {
    const m = /^Disk - (\S+) does not have CBT enabled$/.exec(c.label || '');
    return m ? tr('fk.c.diskCbt', { disk: m[1] }) : (CONCERN()[c.label] || c.label);
  };
  const SEV = { Critical: 'fail', Warning: 'warn', Information: 'info' };

  function openInventory(name) {
    lastInv = { cluster: cur ? cur.cluster : (window.App && App.getCurrentCluster()), source: name, kind: 'vms', q: '', warm: false };
    Sections.open('forklift', 'inventory');
  }

  async function inventoryView(body, d) {
    const ready = d.providers.filter(p => p.ready === true);
    if (!ready.length) {
      body.innerHTML = `<p class="form-hint">${esc(tr('fk.inv.noSource'))}</p>`;
      return;
    }
    if (!lastInv || lastInv.cluster !== cur.cluster || !ready.some(p => p.name === lastInv.source)) {
      lastInv = { cluster: cur.cluster, source: ready[0].name, kind: 'vms', q: '', warm: false };
    }
    const s = lastInv;
    body.innerHTML = `<div class="fk-inv-tools">
        ${field('source', tr('fk.inv.source'), `<select name="source">${opts(ready.map(p => p.name), s.source)}</select>`, tr('fk.t.invSource'))}
        <div class="sub-tabs sub-tabs-inline">${['vms', 'networks', 'datastores'].map(k => `<button type="button" class="sub-tab tip ${k === s.kind ? 'active' : ''}" data-fk="inv-kind" data-kind="${k}" data-tip="${esc(tr(`fk.t.inv.${k}`))}">${esc(tr(`fk.inv.${k}`))}</button>`).join('')}</div>
        <input name="q" class="tip" data-tip="${esc(tr('fk.t.search'))}" placeholder="${esc(tr('fk.search'))}" value="${esc(s.q)}">
        ${s.kind === 'vms' ? `<label class="fk-check tip" data-tip="${esc(tr('fk.t.warmOnly'))}"><input type="checkbox" name="warm_only" ${s.warm ? 'checked' : ''}> ${esc(tr('fk.warmOnly'))}</label>` : ''}
        <button type="button" class="btn btn-sm btn-secondary tip" data-fk="inv-refresh" data-tip="${esc(tr('fk.t.invRefresh'))}">${icon('refresh')}</button>
      </div><div data-fk="inv-out"><p class="form-hint">${esc(tr('common.loading'))}</p></div>`;
    const c = cur;
    let res;
    try { res = await call('GET', `/api/forklift/${enc(c.cluster)}/inventory/${enc(s.source)}/${s.kind}`); }
    catch (err) { if (c === cur) body.querySelector('[data-fk="inv-out"]').innerHTML = `<p class="res-error">${esc(err.message)}</p>`; return; }
    if (c !== cur) return;
    c.invRows = res.rows || [];
    paintInventory();
  }

  function paintInventory() {
    const out = cur.host.querySelector('[data-fk="inv-out"]');
    if (!out) return;
    const s = lastInv;
    const q = s.q.toLowerCase();
    let rows = (cur.invRows || []).filter(r => !q || `${r.name} ${r.path || ''} ${r.guest || ''}`.toLowerCase().includes(q));
    if (s.kind === 'vms' && s.warm) rows = rows.filter(r => r.cbt);
    cur.host.querySelector('.res-count').textContent = tr('fk.inv.count', { n: rows.length });
    if (s.kind === 'networks') {
      out.innerHTML = `<table class="res-table" data-fk="inv-table"><thead><tr><th>${esc(tr('fk.inv.name'))}</th><th>${esc(tr('fk.inv.path'))}</th></tr></thead>
        <tbody>${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.path)}</td></tr>`).join('')}</tbody></table>`;
      return;
    }
    if (s.kind === 'datastores') {
      out.innerHTML = `<table class="res-table" data-fk="inv-table"><thead><tr><th>${esc(tr('fk.inv.name'))}</th><th>${esc(tr('fk.inv.capacity'))}</th><th>${esc(tr('fk.inv.free'))}</th></tr></thead>
        <tbody>${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${size(r.capacity)}</td><td>${size(r.free)}</td></tr>`).join('')}</tbody></table>`;
      return;
    }
    out.innerHTML = `<table class="res-table" data-fk="inv-table"><thead><tr>
        <th>${esc(tr('fk.inv.vm'))}</th><th>${esc(tr('fk.inv.power'))}</th><th>${esc(tr('fk.inv.os'))}</th>
        <th>${esc(tr('fk.inv.cpuMem'))}</th><th>${esc(tr('fk.inv.disks'))}</th><th>${esc(tr('fk.inv.warm'))}</th><th>${esc(tr('fk.inv.concerns'))}</th></tr></thead>
      <tbody>${rows.map(r => {
        const total = (r.disks || []).reduce((a, x) => a + (x.capacity || 0), 0);
        const cs = r.concerns || [];
        const shown = cs.filter(c => !/^Disk - /.test(c.label || '')).slice(0, 2);
        const rest = cs.length - shown.length;
        return `<tr data-vm="${esc(r.name)}"><td class="tip" data-tip="${esc(r.path || '')}">${esc(r.name)}</td>
          <td>${esc(r.power === 'poweredOn' ? tr('fk.inv.on') : r.power === 'poweredOff' ? tr('fk.inv.off') : r.power || '')}</td>
          <td>${esc(r.guest || '')}</td><td>${esc(`${r.cpus || '?'} / ${size((r.memory_mib || 0) * 1048576)}`)}</td>
          <td>${esc(`${(r.disks || []).length} · ${size(total)}`)}</td>
          <td>${r.cbt ? `<span class="tip" data-fk-warm="yes" data-tip="${esc(tr('fk.t.cbtOn'))}">${icon('ok')} ${esc(tr('fk.inv.cbtOn'))}</span>`
                      : `<span class="tip" data-fk-warm="no" data-tip="${esc(tr('fk.t.cbtOff'))}">${icon('fail')} ${esc(tr('fk.inv.cbtOff'))}</span>`}</td>
          <td class="fk-concerns">${shown.map(c => `<span class="tip" data-tip="${esc(c.label)}">${icon(SEV[c.category] || 'info', 12)} ${esc(concernText(c))}</span>`).join(' ')}
            ${rest > 0 ? `<span class="badge tip" data-tip="${esc(cs.map(concernText).join('\n'))}">+${rest}</span>` : ''}</td></tr>`;
      }).join('')}</tbody></table>`;
  }
```

Gestes : `inv-kind` (change `lastInv.kind`, `render()`), `inv-refresh` (`render()`, le serveur garde 20 s), et dans `onChange` : `[name="source"]` (change `lastInv.source`, `render()`), `[name="warm_only"]` (`lastInv.warm`, `paintInventory()`). La recherche `[name="q"]` réagit à `input` (ajouter un écouteur `input` sur la carte qui met à jour `lastInv.q` et appelle `paintInventory()`). Vérifier que `Icons` a une icône `info` ; sinon `warn`.

- [ ] **Step 4: le lien depuis VM Import**

Dans `vmimport.js`, là où l'onglet d'un type de source affiche son `HINT()[type]`, pour `vmware` seulement, ajouter après le texte :

```javascript
      ${cur.kind === 'vmware' ? `<p class="form-hint">${esc(tr('vi.forkliftHint'))}
        <button type="button" class="btn btn-sm btn-secondary tip" data-vi="open-forklift" data-tip="${esc(tr('vi.t.openForklift'))}">${icon('migrate')} ${esc(tr('tab.forklift'))}</button></p>` : ''}
```

(lire `render()` de `vmimport.js` pour trouver l'endroit exact où l'onglet de source est dessiné) et dans `onClick` : `if (act === 'open-forklift' && window.Sections) return Sections.open('forklift', 'prep');`, avant la ligne qui exige `cur.data`.

- [ ] **Step 5: i18n (5 langues) et style**

EN (FR ensuite ; DE, ES, IT traduits avec les mêmes clés) :

```javascript
    'fk.needInstall': 'Forklift is not ready on this cluster yet: finish the preparation first.',
    'fk.t.gotoPrep': 'Open the preparation of this cluster',
    'fk.count': '{n} vCenter source(s)', 'fk.noSource': 'No vCenter source yet: add one.',
    'fk.newSource': 'Add a vCenter', 'fk.t.newSource': 'Declare a vCenter server Forklift can read VMs from',
    'fk.editSource': 'Change {name}',
    'fk.src.ready': 'connected', 'fk.src.refused': 'refused', 'fk.src.checking': 'being checked',
    'fk.src.vddk': 'VDDK image: {image}', 'fk.src.noVddk': 'none (disks copied without VDDK, slower)',
    'fk.src.plans': 'Used by {n} wave(s)',
    'fk.t.inventory': 'VMs, networks and datastores of this vCenter', 'fk.edit': 'Change', 'fk.del': 'Delete',
    'fk.t.edit': 'Change the address, the account or the VDDK image', 'fk.t.notManaged': 'Not declared by the console: change it where it was made',
    'fk.t.del': 'Delete this source and the secret the console made for it', 'fk.t.delUsed': 'Used by {plans}: delete them first',
    'fk.confirm.del': 'Delete the vCenter source {name}? Its secret goes with it; the vCenter is not touched.',
    'fk.sourceHint': 'Forklift checks the account and the certificate as soon as the source is declared; a refusal creates nothing.',
    'fk.f.from': 'Take a vCenter of VM Import', 'fk.from.none': '(type it)',
    'fk.t.from': 'Reuse the address and the account of a VM Import source; the server reads its password, the browser never sees it',
    'fk.fromHint': 'The address, the account and the certificate come from the VM Import source.',
    'fk.t.name': 'A short lowercase name for this source',
    'fk.f.url': 'vCenter address', 'fk.t.url': 'The vCenter host name or its SDK address (https://host/sdk)',
    'fk.t.user': 'An account allowed to read the inventory and export VMs', 'fk.t.userKeep': 'Empty keeps the current account',
    'fk.t.password': 'Sent to the tool through a private file, then kept in a Secret of the cluster',
    'fk.t.passwordKeep': 'Empty keeps the current password', 'fk.unchanged': 'unchanged',
    'fk.f.tls': 'Certificate of the vCenter', 'fk.tls.ca': 'Trust this certificate authority', 'fk.tls.insecure': 'Do not verify',
    'fk.t.tlsCa': 'Paste the PEM certificate of the authority that signed the vCenter', 'fk.t.tlsInsecure': 'TLS is not verified: for a lab only',
    'fk.tls.keep': 'Keep the current setting', 'fk.t.tlsKeep': 'The certificate authority or the choice not to verify, as saved for this source',
    'fk.t.cacert': '-----BEGIN CERTIFICATE----- ...', 'fk.needCa': 'Paste the certificate authority, or choose not to verify',
    'fk.f.vddk': 'VDDK image', 'fk.t.vddk': 'The image pushed in the preparation; without it, disks are copied more slowly',
    'fk.done.source': 'vCenter source {name} declared',
    'fk.inv.noSource': 'No connected vCenter source: add one in vCenter sources.',
    'fk.inv.source': 'Source', 'fk.t.invSource': 'The vCenter to read',
    'fk.inv.vms': 'VMs', 'fk.inv.networks': 'Networks', 'fk.inv.datastores': 'Datastores',
    'fk.t.inv.vms': 'VMs with their size, disks and what Forklift says about them',
    'fk.t.inv.networks': 'Port groups and networks of the vCenter', 'fk.t.inv.datastores': 'Datastores with their capacity',
    'fk.search': 'Search', 'fk.t.search': 'Filter by name, folder or system',
    'fk.warmOnly': 'Only VMs that can move warm', 'fk.t.warmOnly': 'Warm migration needs Changed Block Tracking on every disk',
    'fk.t.invRefresh': 'Read the inventory again',
    'fk.inv.count': '{n} item(s)', 'fk.inv.name': 'Name', 'fk.inv.path': 'Path', 'fk.inv.capacity': 'Capacity', 'fk.inv.free': 'Free',
    'fk.inv.vm': 'VM', 'fk.inv.power': 'State', 'fk.inv.os': 'System', 'fk.inv.cpuMem': 'vCPU / memory', 'fk.inv.disks': 'Disks',
    'fk.inv.warm': 'Warm', 'fk.inv.concerns': 'Points of attention', 'fk.inv.on': 'on', 'fk.inv.off': 'off',
    'fk.inv.cbtOn': 'CBT on', 'fk.inv.cbtOff': 'CBT off',
    'fk.t.cbtOn': 'Changed Block Tracking is on: only changed blocks are copied again until the switchover',
    'fk.t.cbtOff': 'Changed Block Tracking is off: this VM can only move cold (stopped for the whole copy)',
    'fk.c.cbt': 'CBT not enabled', 'fk.c.diskCbt': 'disk {disk} without CBT', 'fk.c.hostName': 'empty host name',
    'fk.c.os': 'system not supported', 'fk.c.hotplug': 'CPU or memory hot-add', 'fk.c.serial': 'disk serial numbers may be truncated',
    'fk.c.shareable': 'shared disk', 'fk.c.rdm': 'raw device mapping disk', 'fk.c.snapshot': 'VM snapshot present',
    'vi.forkliftHint': 'For dozens of VMs, moved warm with the shortest cut:',
    'vi.t.openForklift': 'Open the VMware migrations by Forklift of this cluster',
```

FR :

```javascript
    'fk.needInstall': 'Forklift n\'est pas encore prêt sur ce cluster : terminer d\'abord la préparation.',
    'fk.t.gotoPrep': 'Ouvrir la préparation de ce cluster',
    'fk.count': '{n} source(s) vCenter', 'fk.noSource': 'Aucune source vCenter pour l\'instant : en ajouter une.',
    'fk.newSource': 'Ajouter un vCenter', 'fk.t.newSource': 'Déclarer un serveur vCenter dont Forklift peut lire les VMs',
    'fk.editSource': 'Modifier {name}',
    'fk.src.ready': 'connecté', 'fk.src.refused': 'refusé', 'fk.src.checking': 'en vérification',
    'fk.src.vddk': 'Image VDDK : {image}', 'fk.src.noVddk': 'aucune (disques copiés sans VDDK, plus lentement)',
    'fk.src.plans': 'Utilisée par {n} vague(s)',
    'fk.t.inventory': 'VMs, réseaux et datastores de ce vCenter', 'fk.edit': 'Modifier', 'fk.del': 'Supprimer',
    'fk.t.edit': 'Changer l\'adresse, le compte ou l\'image VDDK', 'fk.t.notManaged': 'Pas déclarée par la console : la modifier là où elle a été faite',
    'fk.t.del': 'Supprimer cette source et le secret que la console a créé pour elle', 'fk.t.delUsed': 'Utilisée par {plans} : les supprimer d\'abord',
    'fk.confirm.del': 'Supprimer la source vCenter {name} ? Son secret part avec elle ; le vCenter n\'est pas touché.',
    'fk.sourceHint': 'Forklift vérifie le compte et le certificat dès la déclaration ; un refus ne crée rien.',
    'fk.f.from': 'Reprendre un vCenter de VM Import', 'fk.from.none': '(saisie)',
    'fk.t.from': 'Reprend l\'adresse et le compte d\'une source de VM Import ; le serveur lit son mot de passe, le navigateur ne le voit jamais',
    'fk.fromHint': 'L\'adresse, le compte et le certificat viennent de la source de VM Import.',
    'fk.t.name': 'Un nom court en minuscules pour cette source',
    'fk.f.url': 'Adresse du vCenter', 'fk.t.url': 'Le nom d\'hôte du vCenter ou son adresse SDK (https://hôte/sdk)',
    'fk.t.user': 'Un compte autorisé à lire l\'inventaire et à exporter des VMs', 'fk.t.userKeep': 'Vide : le compte actuel est gardé',
    'fk.t.password': 'Transmis à l\'outil par un fichier privé, puis gardé dans un Secret du cluster',
    'fk.t.passwordKeep': 'Vide : le mot de passe actuel est gardé', 'fk.unchanged': 'inchangé',
    'fk.f.tls': 'Certificat du vCenter', 'fk.tls.ca': 'Faire confiance à cette autorité', 'fk.tls.insecure': 'Ne pas vérifier',
    'fk.t.tlsCa': 'Coller le certificat PEM de l\'autorité qui a signé le vCenter', 'fk.t.tlsInsecure': 'TLS n\'est pas vérifié : pour un banc seulement',
    'fk.tls.keep': 'Garder le réglage actuel', 'fk.t.tlsKeep': 'L\'autorité de certification, ou le choix de ne pas vérifier, tels qu\'enregistrés pour cette source',
    'fk.t.cacert': '-----BEGIN CERTIFICATE----- ...', 'fk.needCa': 'Coller l\'autorité de certification, ou choisir de ne pas vérifier',
    'fk.f.vddk': 'Image VDDK', 'fk.t.vddk': 'L\'image poussée à la préparation ; sans elle, les disques sont copiés plus lentement',
    'fk.done.source': 'Source vCenter {name} déclarée',
    'fk.inv.noSource': 'Aucune source vCenter connectée : en ajouter une dans Sources vCenter.',
    'fk.inv.source': 'Source', 'fk.t.invSource': 'Le vCenter à lire',
    'fk.inv.vms': 'VMs', 'fk.inv.networks': 'Réseaux', 'fk.inv.datastores': 'Datastores',
    'fk.t.inv.vms': 'Les VMs avec leur taille, leurs disques et ce qu\'en dit Forklift',
    'fk.t.inv.networks': 'Groupes de ports et réseaux du vCenter', 'fk.t.inv.datastores': 'Datastores avec leur capacité',
    'fk.search': 'Rechercher', 'fk.t.search': 'Filtrer par nom, dossier ou système',
    'fk.warmOnly': 'Seulement les VMs migrables à chaud', 'fk.t.warmOnly': 'La migration à chaud exige le suivi des blocs modifiés (CBT) sur chaque disque',
    'fk.t.invRefresh': 'Relire l\'inventaire',
    'fk.inv.count': '{n} élément(s)', 'fk.inv.name': 'Nom', 'fk.inv.path': 'Chemin', 'fk.inv.capacity': 'Capacité', 'fk.inv.free': 'Libre',
    'fk.inv.vm': 'VM', 'fk.inv.power': 'État', 'fk.inv.os': 'Système', 'fk.inv.cpuMem': 'vCPU / mémoire', 'fk.inv.disks': 'Disques',
    'fk.inv.warm': 'À chaud', 'fk.inv.concerns': 'Points d\'attention', 'fk.inv.on': 'allumée', 'fk.inv.off': 'éteinte',
    'fk.inv.cbtOn': 'CBT actif', 'fk.inv.cbtOff': 'CBT inactif',
    'fk.t.cbtOn': 'Le suivi des blocs modifiés est actif : seuls les blocs changés sont recopiés jusqu\'à la bascule',
    'fk.t.cbtOff': 'Le suivi des blocs modifiés est inactif : cette VM ne peut partir qu\'à froid (arrêtée pendant toute la copie)',
    'fk.c.cbt': 'CBT inactif', 'fk.c.diskCbt': 'disque {disk} sans CBT', 'fk.c.hostName': 'nom d\'hôte vide',
    'fk.c.os': 'système non pris en charge', 'fk.c.hotplug': 'ajout à chaud de CPU ou de mémoire', 'fk.c.serial': 'numéros de série des disques peut-être tronqués',
    'fk.c.shareable': 'disque partagé', 'fk.c.rdm': 'disque en accès direct (RDM)', 'fk.c.snapshot': 'instantané de VM présent',
    'vi.forkliftHint': 'Pour des dizaines de VMs, déplacées à chaud avec la coupure la plus courte :',
    'vi.t.openForklift': 'Ouvrir les migrations VMware par Forklift de ce cluster',
```

Style :

```css
.fk-sources { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 10px; }
.fk-source { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.fk-source-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.fk-inv-tools { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.fk-concerns span { white-space: nowrap; }
.fk-tls { border: 0; padding: 0; margin: 0; display: grid; gap: 4px; }
```

- [ ] **Step 6: tests verts et commit**

Run: `python3 -m pytest tests/e2e/test_forklift_175.py tests/e2e/test_vmimport_171.py tests/e2e/test_sections.py -q`
Expected: PASS
Run: `python3 -m pytest tests/api/ -q`
Expected: tout vert

```bash
git add web/static/js/forklift.js web/static/js/vmimport.js web/static/js/i18n.js web/static/css/style.css \
        tests/e2e/test_forklift_175.py tests/e2e/test_vmimport_171.py
git commit -m "feat(1.75.0): vCenter sources and read-only inventory in the VMware migrations tab"
```

---

### Task U5 : l'écran en réel sur harvlab2 + vmwlab (contrôleur)

À faire directement : les gestes touchent le banc et le registre du banc.

- [ ] Redémarrer la console de dev (port 8095, `HARVESTER_OPS_CONFIG=~/.config/harvester-ops/config.yaml`), tuer par PID, jamais `pkill -f` ; vérifier qu'elle sert le nouveau modèle (piège de l'onglet blanc).
- [ ] Onglet Migrations VMware de harvlab2 : Préparation lue (Forklift prêt, composants), « Installer » absent puisque prêt ; relancer l'installation par l'API (`POST /api/forklift/harvlab2/do/install`) : reprise sans rien recréer, action suivie jusqu'au bout dans le dock.
- [ ] Déposer l'archive VDDK par l'écran (Playwright contre la console de dev, fichier `~/ISO/VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz`), la voir dans la liste ; pousser avec « identifiants de Harvester » (registre du banc) ; vérifier le ConfigMap `forklift/harvester-ops-vddk` sur harvlab2.
- [ ] Déclarer dans VM Import une source VMware `mig/vmwlab-vi` vers le vrai vCenter du banc (`https://vmwlab-vc.home.lo/sdk`, datacenter `vmwlab-dc`, compte du banc lu dans Vault `secret/infra/vmware-lab`, jamais affiché) ; c'est aussi la première vérification réelle d'une source VMware de VM Import contre un vrai vCenter (1.71 ne l'avait vue que contre vcsim) : noter ce qu'elle dit.
- [ ] Sources vCenter : « Ajouter un vCenter » > « Reprendre un vCenter de VM Import » `mig/vmwlab-vi`, nom `vmwlab-ui` ; attendre « connecté » ; « Modifier » sans ressaisir le mot de passe en changeant l'image VDDK ; puis « Supprimer ».
- [ ] Mauvais mot de passe saisi à la main : refus de Forklift affiché, aucun fournisseur créé (`kubectl get providers -n forklift`).
- [ ] Inventaire de `vmwlab` : 4 VMs, colonne « À chaud », points d'attention traduits ; réseaux, datastores ; filtre « à chaud ».
- [ ] Remettre le banc dans son état : supprimer `vmwlab-ui` s'il reste, la source VM Import `mig/vmwlab-vi` et son Secret ; garder `vmwlab`, le ConfigMap VDDK et l'archive.
- [ ] Captures de l'onglet (Préparation, Sources, Inventaire) pour l'exploitant.
- [ ] Tout écart trouvé : test qui le reproduit, correction, commit `fix(1.75.0): ...`, avant la release.
- [ ] Mettre à jour `memory/forklift-migrations-vmware.md` avec ce que le réel a montré.

Ensuite : tâche 8 du plan B1, avec la doc de l'onglet (voir la mise à jour de la tâche 8 dans `2026-09-28-forklift-b1-plan.md`).
