# Forklift sur Harvester, étape B1 : installation, image VDDK, fournisseur vCenter

**Goal:** installer Forklift sur un cluster Harvester, construire et pousser l'image VDDK de l'exploitant, déclarer le vCenter comme fournisseur et lire son inventaire, en ligne de commande (`bin/harvester-forklift.py`), vérifié en réel sur harvlab2 avec le banc vmwlab. Release v1.75.0.

**Architecture:** deux bibliothèques pures dans `bin/lib/` (`hv_forklift.py` : objets Kubernetes et lecture de leur état ; `oci_push.py` : image OCI et API de registre v2, sans outil de conteneur) et un outil `bin/harvester-forklift.py` qui les enchaîne sur un cluster par `kubectl`, avec des étapes `STEP_EVENT` sur stderr comme les autres outils. L'interface (routes et fenêtres) viendra avec C ; B2 ajoutera correspondances, plans à chaud, bascule et retour arrière.

**Tech Stack:** Python 3.11+ (bibliothèque standard seulement), kubectl, pytest.

**Conception de référence :** `docs/design/2026-09-27-migrations-vmware.md` (sous-projet B, découpé en B1 et B2 le 28/09/2026, voir la section « Ce que le réel a appris avant B »).

## Global Constraints

- Bibliothèque standard seulement dans `bin/` : l'outil tourne sur un hôte airgap (règle du projet).
- Aucun secret en argument de commande, dans un journal, un `STEP_EVENT` ou une sortie : mot de passe du vCenter et identifiants du registre arrivent en JSON sur l'entrée standard.
- Le VDDK n'est jamais dans le dépôt ni dans le livrable (licence VMware) ; les tests fabriquent une fausse archive.
- Chart `forklift-operator` 1.9.0 de `https://charts.harvesterhci.io` ; images `registry.rancher.com/harvester/harvester-forklift-*`, tag `v1.8.2` par défaut (dernier publié au 28/09/2026 ; aucun `v1.9.0` publié).
- Image de base de l'image VDDK : `registry.suse.com/bci/bci-busybox:16.0` (BCI de SUSE, pour `cp`).
- Code et noms en anglais, commentaires et docstrings en français, messages de l'outil en anglais (comme les autres outils).
- Tests : `python3 -m pytest tests/api/ -q` vert avant chaque commit.
- Commits Conventional Commits `feat(1.75.0): ...`, sans tiret cadratin ni flèche Unicode dans le contenu public.
- VERSION passe à 1.75.0 dans le commit qui apporte le changement, avec son entrée au CHANGELOG et la doc EN + FR.
- Vérification réelle obligatoire sur harvlab2 + vmwlab avant la release ; au plus deux lames allumées (node1 + node2).

## Fichiers

| Fichier | Rôle |
|---|---|
| `bin/lib/hv_forklift.py` (nouveau) | add-on, ForkliftController, cert-manager, fournisseur vSphere, lecture des états, inventaire |
| `bin/lib/oci_push.py` (nouveau) | image VDDK = base BCI busybox + archive VDDK telle quelle, poussée par l'API de registre v2 |
| `bin/lib/kube.py` (modifié) | `Kube.port_forward()` pour joindre le service d'inventaire |
| `bin/harvester-forklift.py` (nouveau) | l'outil : `status`, `install`, `vddk-image`, `provider-apply`, `provider-delete`, `inventory` |
| `container/Containerfile`, `install.sh` (modifiés) | lien `harvester-forklift` sans extension, comme `harvester-vm-transfer` |
| `tests/api/test_forklift_175.py` (nouveau) | bibliothèque `hv_forklift` |
| `tests/api/test_oci_push_175.py` (nouveau) | image VDDK contre deux registres simulés |
| `tests/api/test_forklift_cli_175.py` (nouveau) | l'outil contre un cluster en mémoire |
| `tests/api/fixtures/forklift_inventory_vms_175.json` (nouveau, relevé réel) | inventaire vSphere réel de vmwlab, pour l'analyse |
| `docs/en/capabilities.md`, `docs/fr/capabilites.md`, `CHANGELOG.md`, `VERSION`, `tools/parity/data.py` | doc, version, parité |

---

### Task 1 : `hv_forklift.py`, installation (add-on, contrôleur, cert-manager)

**Files:**
- Create: `bin/lib/hv_forklift.py`
- Test: `tests/api/test_forklift_175.py`

**Interfaces:**
- Produces: constantes `G, API, NS, K_ADDON, K_CONTROLLER, K_PROVIDER, K_PLAN, K_DEPLOY, ADDON, CONTROLLER_NAME, CHART_REPO, CHART, CHART_VERSION, IMAGE_REPO, IMAGE_TAG, OPERATOR_DEPLOY, COMPONENTS, CERT_MANAGER, INVENTORY_SA, INVENTORY_SVC, INVENTORY_PORT, L_MANAGED` ; fonctions `check_name(name, what) -> str`, `operator_values(image_tag, image_repo) -> dict`, `addon_manifest(chart_version, image_tag, image_repo) -> dict`, `namespace_manifest() -> dict`, `controller_manifest() -> dict`, `deployment_ready(dep) -> bool`, `addon_state(addon) -> (str, str)`, `pod_problems(pods) -> list[str]`, `install_state(addon, deploys, controller, cert_manager_deploys) -> dict`, `inventory_rbac() -> list[dict]`.

- [ ] **Step 1: écrire les tests**

```python
"""v1.75.0 : Forklift sur Harvester, installation. L'add-on expérimental
forklift-operator (chart de charts.harvesterhci.io) exige qu'on pose dépôt,
image et tag de l'opérateur (le défaut du chart est rancher/nginx:latest),
puis un ForkliftController fait déployer les composants, qui exigent
cert-manager (absent de Harvester)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))
import hv_forklift as hf  # noqa: E402


def dep(name, ready=True, replicas=1):
    return {"metadata": {"name": name}, "spec": {"replicas": replicas},
            "status": {"availableReplicas": replicas if ready else 0}}


def test_the_addon_always_names_the_operator_image():
    a = hf.addon_manifest()
    assert a["apiVersion"] == "harvesterhci.io/v1beta1" and a["kind"] == "Addon"
    assert a["metadata"] == {"name": "forklift-operator", "namespace": "forklift",
                             "labels": {"addon.harvesterhci.io/experimental": "true", "harvester-ops.io/managed": "true"}}
    s = a["spec"]
    assert (s["enabled"], s["repo"], s["chart"], s["version"]) == (True, "https://charts.harvesterhci.io", "forklift-operator", "1.9.0")
    values = json.loads(s["valuesContent"])       # du JSON : c'est du YAML valide pour Harvester
    op = values["forkliftOperatorAnsible"]["forkliftOperator"]
    assert (op["repo"], op["operatorImage"], op["tag"]) == ("registry.rancher.com/harvester", "harvester-forklift-operator", "v1.8.2")
    assert values["fullnameOverride"] == "harvester"   # d'où le déploiement harvester-forklift-operator-ansible
    other = json.loads(hf.addon_manifest("1.8.2", "main-head")["spec"]["valuesContent"])
    assert other["forkliftOperatorAnsible"]["forkliftOperator"]["tag"] == "main-head"


@pytest.mark.parametrize("version,tag,match", [
    ("latest", "v1.8.2", "chart version"), ("1.9", "v1.8.2", "chart version"),
    ("1.9.0", "", "image tag"), ("1.9.0", "v1 8", "image tag"), ("1.9.0", "-bad", "image tag"),
])
def test_a_version_or_tag_that_would_break_the_chart_is_refused(version, tag, match):
    with pytest.raises(ValueError, match=match):
        hf.addon_manifest(version, tag)


def test_the_controller_and_its_namespace():
    assert hf.namespace_manifest() == {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "forklift"}}
    c = hf.controller_manifest()
    assert c == {"apiVersion": "forklift.konveyor.io/v1beta1", "kind": "ForkliftController",
                 "metadata": {"name": "forklift-controller", "namespace": "forklift"},
                 "spec": {"feature_ui_plugin": "false"}}


def test_the_addon_state_follows_harvester_s_statuses():
    assert hf.addon_state(None)[0] == "absent"
    assert hf.addon_state({"spec": {"enabled": False}})[0] == "disabled"
    assert hf.addon_state({"spec": {"enabled": True}, "status": {"status": "AddonEnabling"}})[0] == "deploying"
    for ok in ("AddonDeploySuccessful", "AddonUpdateSuccessful", "AddonDeployed"):
        assert hf.addon_state({"spec": {"enabled": True}, "status": {"status": ok}})[0] == "ready"
    st, msg = hf.addon_state({"spec": {"enabled": True}, "status": {"status": "AddonDeployFailed", "conditions": [
        {"type": "OperationFailed", "status": "True", "message": "chart forklift-operator-9.9.9 not found"}]}})
    assert st == "failed" and "chart forklift-operator-9.9.9 not found" in msg


def test_a_deployment_is_ready_when_all_its_replicas_are_available():
    assert hf.deployment_ready(dep("x")) and not hf.deployment_ready(dep("x", ready=False))
    assert not hf.deployment_ready(None) and not hf.deployment_ready({"spec": {"replicas": 2}, "status": {"availableReplicas": 1}})


def test_the_install_state_is_read_in_the_order_it_is_done():
    cm = {n: dep(n) for n in hf.CERT_MANAGER[1]}
    ok_addon = {"spec": {"enabled": True}, "status": {"status": "AddonDeploySuccessful"}}
    deploys = {n: dep(n) for n in (hf.OPERATOR_DEPLOY,) + hf.COMPONENTS}
    st = hf.install_state(ok_addon, deploys, {"kind": "ForkliftController"}, cm)
    assert st["ready"] and st["cert_manager"] and st["operator"] and st["controller"] and st["components_missing"] == []
    st = hf.install_state(None, {}, None, {})
    assert not st["ready"] and st["addon"] == "absent" and st["cert_manager_missing"] == list(hf.CERT_MANAGER[1])
    deploys["forklift-validation"] = dep("forklift-validation", ready=False)
    st = hf.install_state(ok_addon, deploys, {"kind": "ForkliftController"}, cm)
    assert not st["ready"] and st["components_missing"] == ["forklift-validation"]


def test_a_pod_that_cannot_pull_or_start_is_named_with_its_reason():
    pods = [
        {"metadata": {"name": "forklift-controller-abc"}, "status": {"containerStatuses": [
            {"state": {"waiting": {"reason": "ImagePullBackOff",
                                   "message": 'Back-off pulling image "registry.rancher.com/harvester/harvester-forklift-controller:v1.9.0"'}}}]}},
        {"metadata": {"name": "forklift-api-def"}, "status": {"containerStatuses": [{"state": {"running": {}}}]}},
        {"metadata": {"name": "forklift-validation-ghi"}, "status": {"initContainerStatuses": [
            {"state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}},
    ]
    probs = hf.pod_problems(pods)
    assert probs[0].startswith("forklift-controller-abc: ImagePullBackOff") and "v1.9.0" in probs[0]
    assert probs[1] == "forklift-validation-ghi: CrashLoopBackOff" and len(probs) == 2


def test_the_inventory_reader_can_only_read_providers():
    sa, role, binding = hf.inventory_rbac()
    assert (sa["kind"], sa["metadata"]["name"], sa["metadata"]["namespace"]) == ("ServiceAccount", "harvester-ops-inventory", "forklift")
    assert role["kind"] == "ClusterRole" and role["rules"] == [
        {"apiGroups": ["forklift.konveyor.io"], "resources": ["providers"], "verbs": ["get", "list"]}]
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "harvester-ops-inventory", "namespace": "forklift"}]
```

- [ ] **Step 2: les lancer, ils échouent**

Run: `python3 -m pytest tests/api/test_forklift_175.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'hv_forklift'`

- [ ] **Step 3: écrire `bin/lib/hv_forklift.py`**

```python
"""harvester-ops : Forklift sur Harvester, migrations depuis VMware (v1.75.0).

Harvester 1.9 ne livre pas Forklift. Relevé le 28/09/2026 (dépôts
harvester/sv-addons, harvester/charts, harvester/forklift-packaging) :

- l'add-on expérimental `forklift-operator` (namespace forklift) installe
  l'opérateur depuis charts.harvesterhci.io ; les valeurs par défaut du chart
  pointent sur rancher/nginx:latest, il faut TOUJOURS poser dépôt, image et
  tag (le même tag vaut pour tous les composants) ;
- chart 1.9.0 publié, images publiées jusqu'à v1.8.2 (Forklift amont 2.9) ;
- un ForkliftController fait déployer les composants (api, controller,
  validation, volume-populator-controller), qui exigent cert-manager,
  absent de Harvester ;
- le fournisseur de destination `host` (namespace forklift) est créé par
  Forklift ; un fournisseur vSphere se déclare avec un secret étiqueté
  createdForProviderType/createdForResourceType ;
- l'inventaire se lit sur le service forklift-inventory (8443) avec un
  jeton de compte de service : le proxy de l'apiserver retire l'en-tête
  Authorization, et le kubeconfig de la console n'a qu'un certificat.

Voir docs/design/2026-09-27-migrations-vmware.md et
docs/design/2026-09-28-forklift-b1-plan.md.
"""

import json
import re

G = "forklift.konveyor.io"
API = f"{G}/v1beta1"
NS = "forklift"
K_ADDON = "addons.harvesterhci.io"
K_CONTROLLER = f"forkliftcontrollers.{G}"
K_PROVIDER = f"providers.{G}"
K_PLAN = f"plans.{G}"
K_DEPLOY = "deployments.apps"
ADDON = (NS, "forklift-operator")
CONTROLLER_NAME = "forklift-controller"
CHART_REPO = "https://charts.harvesterhci.io"
CHART = "forklift-operator"
CHART_VERSION = "1.9.0"
IMAGE_REPO = "registry.rancher.com/harvester"
IMAGE_TAG = "v1.8.2"
OPERATOR_DEPLOY = "harvester-forklift-operator-ansible"
COMPONENTS = ("forklift-api", "forklift-controller", "forklift-validation", "forklift-volume-populator-controller")
CERT_MANAGER = ("cert-manager", ("cert-manager", "cert-manager-cainjector", "cert-manager-webhook"))
INVENTORY_SA = "harvester-ops-inventory"
INVENTORY_SVC, INVENTORY_PORT = "forklift-inventory", 8443
L_MANAGED = "harvester-ops.io/managed"

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
ADDON_READY = ("AddonDeploySuccessful", "AddonUpdateSuccessful", "AddonDeployed")
POD_STUCK = ("ImagePullBackOff", "ErrImagePull", "CrashLoopBackOff", "CreateContainerConfigError", "InvalidImageName")


def check_name(name, what="name"):
    name = str(name or "").strip()
    if not NAME_RE.match(name):
        raise ValueError(f"{what}: lowercase letters, digits and '-', 63 characters at most ({name!r})")
    return name


def operator_values(image_tag=IMAGE_TAG, image_repo=IMAGE_REPO):
    """Valeurs du chart : dépôt, image et tag de l'opérateur, toujours posés."""
    if not TAG_RE.match(str(image_tag or "")):
        raise ValueError(f"image tag: {image_tag!r} is not a valid tag")
    return {"fullnameOverride": "harvester",
            "forkliftOperatorAnsible": {"forkliftOperator": {
                "repo": image_repo, "operatorImage": "harvester-forklift-operator",
                "tag": image_tag, "imagePullPolicy": "IfNotPresent"}}}


def addon_manifest(chart_version=CHART_VERSION, image_tag=IMAGE_TAG, image_repo=IMAGE_REPO):
    if not VERSION_RE.match(str(chart_version or "")):
        raise ValueError(f"chart version: {chart_version!r} is not a version such as {CHART_VERSION}")
    return {"apiVersion": "harvesterhci.io/v1beta1", "kind": "Addon",
            "metadata": {"name": ADDON[1], "namespace": NS,
                         "labels": {"addon.harvesterhci.io/experimental": "true", L_MANAGED: "true"}},
            "spec": {"enabled": True, "repo": CHART_REPO, "chart": CHART, "version": chart_version,
                     # du JSON, que Harvester lit comme du YAML (JSON en est un sous-ensemble)
                     "valuesContent": json.dumps(operator_values(image_tag, image_repo), indent=2)}}


def namespace_manifest():
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}}


def controller_manifest():
    return {"apiVersion": API, "kind": "ForkliftController",
            "metadata": {"name": CONTROLLER_NAME, "namespace": NS},
            "spec": {"feature_ui_plugin": "false"}}


def deployment_ready(dep):
    if not dep:
        return False
    want = (dep.get("spec") or {}).get("replicas", 1)
    have = (dep.get("status") or {}).get("availableReplicas") or 0
    return have >= max(want, 1)


def addon_state(addon):
    """(état, message) : absent, disabled, deploying, failed, ready. Un échec
    se lit dans le statut (…Failed) ou la condition OperationFailed."""
    if addon is None:
        return "absent", "the forklift-operator add-on is not declared"
    if not (addon.get("spec") or {}).get("enabled"):
        return "disabled", "the forklift-operator add-on is disabled"
    st = addon.get("status") or {}
    status = st.get("status") or ""
    failed = next((c.get("message") or c.get("reason") for c in st.get("conditions") or []
                   if c.get("type") == "OperationFailed" and str(c.get("status")) == "True"), "")
    if "Failed" in status or failed:
        return "failed", "the add-on failed: " + (failed or status)
    if status in ADDON_READY:
        return "ready", "the forklift-operator add-on is deployed"
    return "deploying", f"the add-on is being deployed ({status or 'pending'})"


def pod_problems(pods):
    """« pod: raison (message) » des pods bloqués : image introuvable,
    redémarrages en boucle... ce qu'un déploiement pas prêt ne dit pas."""
    out = []
    for p in pods or []:
        st = p.get("status") or {}
        for cs in (st.get("initContainerStatuses") or []) + (st.get("containerStatuses") or []):
            w = (cs.get("state") or {}).get("waiting") or {}
            if w.get("reason") in POD_STUCK:
                name = (p.get("metadata") or {}).get("name")
                out.append(f"{name}: {w['reason']}" + (f" ({w['message']})" if w.get("message") else ""))
                break
    return out


def install_state(addon, deploys, controller, cert_manager_deploys):
    """L'installation lue dans l'ordre où elle se fait. `deploys` et
    `cert_manager_deploys` : {nom: Deployment} des deux namespaces."""
    a, amsg = addon_state(addon)
    cm_missing = [n for n in CERT_MANAGER[1] if not deployment_ready(cert_manager_deploys.get(n))]
    comp_missing = [n for n in COMPONENTS if not deployment_ready(deploys.get(n))]
    operator = deployment_ready(deploys.get(OPERATOR_DEPLOY))
    return {"cert_manager": not cm_missing, "cert_manager_missing": cm_missing,
            "addon": a, "addon_message": amsg, "operator": operator,
            "controller": controller is not None, "components_missing": comp_missing,
            "ready": a == "ready" and operator and controller is not None and not comp_missing and not cm_missing}


def inventory_rbac():
    """Le compte qui lit l'inventaire : le service d'inventaire vérifie le
    jeton (TokenReview) et le droit de lire les fournisseurs."""
    labels = {L_MANAGED: "true"}
    return [
        {"apiVersion": "v1", "kind": "ServiceAccount",
         "metadata": {"name": INVENTORY_SA, "namespace": NS, "labels": labels}},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
         "metadata": {"name": INVENTORY_SA, "labels": labels},
         "rules": [{"apiGroups": [G], "resources": ["providers"], "verbs": ["get", "list"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding",
         "metadata": {"name": INVENTORY_SA, "labels": labels},
         "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": INVENTORY_SA},
         "subjects": [{"kind": "ServiceAccount", "name": INVENTORY_SA, "namespace": NS}]},
    ]
```

- [ ] **Step 4: les tests passent**

Run: `python3 -m pytest tests/api/test_forklift_175.py -q`
Expected: 12 passed

- [ ] **Step 5: commit**

```bash
git add bin/lib/hv_forklift.py tests/api/test_forklift_175.py
git commit -m "feat(1.75.0): Forklift install objects and their state

The experimental forklift-operator add-on needs its image, repo and tag
set every time (the chart defaults to rancher/nginx:latest), then a
ForkliftController deploys components that need cert-manager, which
Harvester lacks. The state is read in the order the install runs, with
pods that cannot pull or start named with their reason."
```

---

### Task 2 : `hv_forklift.py`, fournisseur vSphere

**Files:**
- Modify: `bin/lib/hv_forklift.py` (ajout en fin de fichier)
- Test: `tests/api/test_forklift_175.py` (ajout)

**Interfaces:**
- Consumes: `check_name`, `API`, `L_MANAGED` (Task 1).
- Produces: `check_url(url) -> str` (`https://<hôte>/sdk`), `check_image(ref, what) -> str`, `secret_name(provider) -> str`, `provider_secret(ns, name, spec) -> dict`, `provider_manifest(ns, name, spec) -> dict`, `provider_state(p) -> (True|False|None, str)`, `plans_using(ns, name, plans) -> list[str]`. `spec` = `{"url", "user", "password", "insecure": bool, "cacert": str?, "vddk_image": str?}`.

- [ ] **Step 1: écrire les tests (ajout à `tests/api/test_forklift_175.py`)**

```python
CA = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"


@pytest.mark.parametrize("given,want", [
    ("vc.lan", "https://vc.lan/sdk"), ("https://vc.lan", "https://vc.lan/sdk"),
    ("https://vc.lan/sdk", "https://vc.lan/sdk"), ("172.16.2.81", "https://172.16.2.81/sdk"),
    ("https://vc.lan:8443/sdk/", "https://vc.lan:8443/sdk"),
])
def test_the_vcenter_url_is_the_one_forklift_expects(given, want):
    assert hf.check_url(given) == want


@pytest.mark.parametrize("bad", ["http://vc.lan/sdk", "", "https://vc lan", "https://vc.lan/ui"])
def test_a_vcenter_url_forklift_cannot_use_is_refused(bad):
    with pytest.raises(ValueError, match="vCenter URL"):
        hf.check_url(bad)


def test_an_image_must_be_complete_and_pullable():
    for ok in ("172.16.1.11:3000/jniedergang/vddk:8.0.3", "registry.example.com/vddk@sha256:" + "a" * 64,
               "harbor.lan/proj/vddk:8.0.3-1"):
        assert hf.check_image(ok) == ok
    for bad in ("vddk", "harbor.lan/proj/vddk", "Harbor.lan/Proj/vddk:1", "harbor.lan/proj/vddk:bad tag"):
        with pytest.raises(ValueError, match="VDDK image"):
            hf.check_image(bad, "VDDK image")


def test_the_provider_secret_is_labelled_as_forklift_reads_it():
    s = hf.provider_secret("default", "vmwlab", {"url": "172.16.2.81", "user": "administrator@vsphere.local",
                                                 "password": "pw", "insecure": True})
    assert s["metadata"]["name"] == "vmwlab-vsphere" and s["metadata"]["namespace"] == "default"
    assert s["metadata"]["labels"] == {"createdForProviderType": "vsphere", "createdForResourceType": "providers",
                                       "harvester-ops.io/managed": "true"}
    assert s["stringData"] == {"user": "administrator@vsphere.local", "password": "pw",
                               "url": "https://172.16.2.81/sdk", "insecureSkipVerify": "true"}
    s = hf.provider_secret("default", "vc", {"url": "vc.lan", "user": "u", "password": "p", "cacert": CA})
    assert s["stringData"]["insecureSkipVerify"] == "false" and s["stringData"]["cacert"] == CA + "\n"
    long_name = "a" * 63
    assert len(hf.secret_name(long_name)) <= 63 and hf.secret_name(long_name).endswith("-vsphere")


@pytest.mark.parametrize("spec,match", [
    ({"url": "vc.lan", "user": "u"}, "user and password"),
    ({"url": "vc.lan", "user": "", "password": "Zq9-secret"}, "user and password"),
    ({"url": "vc.lan", "user": "u", "password": "Zq9-secret", "cacert": "not a pem"}, "PEM"),
    ({"url": "http://vc.lan", "user": "u", "password": "Zq9-secret"}, "https only"),
])
def test_an_incomplete_provider_is_refused_without_echoing_the_password(spec, match):
    with pytest.raises(ValueError, match=match) as e:
        hf.provider_secret("default", "vc", spec)
    assert "Zq9-secret" not in str(e.value)


def test_the_provider_points_at_its_secret_and_the_vddk_image():
    p = hf.provider_manifest("default", "vmwlab", {"url": "172.16.2.81", "vddk_image": "172.16.1.11:3000/ju/vddk:8.0.3"})
    assert p["apiVersion"] == "forklift.konveyor.io/v1beta1" and p["kind"] == "Provider"
    assert p["spec"] == {"type": "vsphere", "url": "https://172.16.2.81/sdk",
                         "secret": {"name": "vmwlab-vsphere", "namespace": "default"},
                         "settings": {"sdkEndpoint": "vcenter", "vddkInitImage": "172.16.1.11:3000/ju/vddk:8.0.3"}}
    assert "vddkInitImage" not in hf.provider_manifest("default", "vc", {"url": "vc.lan"})["spec"]["settings"]


def test_the_provider_state_is_ready_refused_or_being_checked():
    assert hf.provider_state(None)[0] is None
    ok = {"status": {"phase": "Ready", "conditions": [
        {"type": "ConnectionTestSucceeded", "status": "True", "category": "Required"},
        {"type": "InventoryCreated", "status": "True", "category": "Required"},
        {"type": "Ready", "status": "True", "category": "Required"}]}}
    assert hf.provider_state(ok)[0] is True
    bad = {"status": {"phase": "ConnectionFailed", "conditions": [
        {"type": "ConnectionTestFailed", "status": "True", "category": "Critical",
         "message": "Login failed: incorrect user name or password."}]}}
    res, msg = hf.provider_state(bad)
    assert res is False and "incorrect user name or password" in msg
    staging = {"status": {"phase": "Staging", "conditions": [
        {"type": "ConnectionTestSucceeded", "status": "True", "category": "Required"}]}}
    res, msg = hf.provider_state(staging)
    assert res is None and "Staging" in msg and "ConnectionTestSucceeded" in msg


def test_plans_that_use_a_provider_are_found():
    plans = [{"metadata": {"name": "wave-1", "namespace": "mig"},
              "spec": {"provider": {"source": {"name": "vmwlab", "namespace": "default"}}}},
             {"metadata": {"name": "other", "namespace": "mig"},
              "spec": {"provider": {"source": {"name": "vc2", "namespace": "default"}}}}]
    assert hf.plans_using("default", "vmwlab", plans) == ["mig/wave-1"]
    assert hf.plans_using("default", "nobody", plans) == []
```

- [ ] **Step 2: les lancer, ils échouent**

Run: `python3 -m pytest tests/api/test_forklift_175.py -q`
Expected: FAIL, `AttributeError: module 'hv_forklift' has no attribute 'check_url'`

- [ ] **Step 3: ajouter à `bin/lib/hv_forklift.py`**

```python
# --- fournisseur vSphere ---------------------------------------------------

URL_RE = re.compile(r"^(?:https://)?([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:]+\])(:\d{1,5})?(?:/sdk)?/?$")
IMAGE_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::\d{1,5})?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+"
                      r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:[0-9a-f]{64})?$")


def check_url(url):
    """https://<hôte>/sdk, comme Forklift l'attend ; l'hôte seul, avec ou sans
    https:// et /sdk, est complété. http:// est refusé."""
    u = str(url or "").strip()
    if u.lower().startswith("http://"):
        raise ValueError("vCenter URL: https only")
    m = URL_RE.match(u)
    if not m:
        raise ValueError(f"vCenter URL: {u!r} is not a host, an address or https://<host>/sdk")
    return f"https://{m.group(1)}{m.group(2) or ''}/sdk"


def check_image(ref, what="image"):
    ref = str(ref or "").strip()
    last = ref.rsplit("/", 1)[-1]
    if not IMAGE_RE.match(ref) or (":" not in last and "@" not in last):
        raise ValueError(f"{what}: {ref!r} is not a full image reference (registry/path:tag or @sha256:...)")
    return ref


def secret_name(provider):
    base = provider[:63 - len("-vsphere")].rstrip("-")
    return f"{base}-vsphere"


def provider_secret(ns, name, spec):
    """Le secret d'un fournisseur vSphere, étiqueté comme Forklift le lit. Les
    messages d'erreur ne citent jamais une valeur."""
    user = str(spec.get("user") or "").strip()
    password = str(spec.get("password") or "")
    if not user or not password:
        raise ValueError("user and password are required")
    data = {"user": user, "password": password, "url": check_url(spec.get("url")),
            "insecureSkipVerify": "true" if spec.get("insecure") else "false"}
    if spec.get("cacert"):
        ca = str(spec["cacert"]).strip()
        if "BEGIN CERTIFICATE" not in ca:
            raise ValueError("cacert: a PEM certificate")
        data["cacert"] = ca + "\n"
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": secret_name(check_name(name, "provider")), "namespace": check_name(ns, "namespace"),
                         "labels": {"createdForProviderType": "vsphere", "createdForResourceType": "providers",
                                    L_MANAGED: "true"}},
            "stringData": data}


def provider_manifest(ns, name, spec):
    settings = {"sdkEndpoint": "vcenter"}
    if spec.get("vddk_image"):
        settings["vddkInitImage"] = check_image(spec["vddk_image"], "VDDK image")
    return {"apiVersion": API, "kind": "Provider",
            "metadata": {"name": check_name(name, "provider"), "namespace": check_name(ns, "namespace"),
                         "labels": {L_MANAGED: "true"}},
            "spec": {"type": "vsphere", "url": check_url(spec.get("url")),
                     "secret": {"name": secret_name(name), "namespace": ns},
                     "settings": settings}}


def provider_state(p):
    """(True prêt, False refusé, None en cours, message). Forklift classe ses
    conditions : une « Critical » vraie est un refus (identifiants,
    certificat, adresse), « Ready » vraie la fin."""
    if p is None:
        return None, "waiting for the provider"
    st = p.get("status") or {}
    conds = st.get("conditions") or []
    crit = [c for c in conds if c.get("category") == "Critical" and str(c.get("status")) == "True"]
    if crit:
        return False, "; ".join(c.get("message") or c.get("type") or "refused" for c in crit)
    if any(c.get("type") == "Ready" and str(c.get("status")) == "True" for c in conds):
        return True, "ready: vCenter reached, inventory loaded"
    have = [c.get("type") for c in conds if str(c.get("status")) == "True"]
    return None, f"being checked ({st.get('phase') or 'pending'}" + (f": {', '.join(have)}" if have else "") + ")"


def plans_using(ns, name, plans):
    out = []
    for pl in plans or []:
        src = (((pl.get("spec") or {}).get("provider") or {}).get("source")) or {}
        if src.get("name") == name and src.get("namespace") == ns:
            m = pl.get("metadata") or {}
            out.append(f"{m.get('namespace')}/{m.get('name')}")
    return out
```

- [ ] **Step 4: les tests passent**

Run: `python3 -m pytest tests/api/test_forklift_175.py -q`
Expected: tous verts

- [ ] **Step 5: commit**

```bash
git add bin/lib/hv_forklift.py tests/api/test_forklift_175.py
git commit -m "feat(1.75.0): vSphere provider for Forklift and its state

The secret carries the labels Forklift reads to attach it, the URL is
normalised to https://<host>/sdk, the VDDK image must be a full pullable
reference, and a Critical condition is read as a refusal with its
message, never with a credential in it."
```

---

### Task 3 : `oci_push.py`, image VDDK sans outil de conteneur

**Files:**
- Create: `bin/lib/oci_push.py`
- Test: `tests/api/test_oci_push_175.py`

**Interfaces:**
- Produces: `DEFAULT_BASE = "registry.suse.com/bci/bci-busybox:16.0"`, `ENTRYPOINT`, `RegistryError`, `parse_ref(ref) -> (host, repo, ref)`, `class Registry(host, creds=None, plain_http=False, timeout=120)` avec `manifest(repo, ref) -> (bytes, media_type)`, `blob(repo, digest) -> bytes`, `has_blob(repo, digest) -> bool`, `push_blob(repo, digest, data) -> bool` (False si déjà là), `put_manifest(repo, tag, body, media_type) -> digest` ; `archive_layer(path) -> {"digest", "diff_id", "size"}` ; `vddk_config(base_config, diff_id, created) -> dict` ; `push_vddk_image(archive, image, base=DEFAULT_BASE, target_creds=None, base_creds=None, plain_http=False, base_plain_http=False, step=callable, now=time.gmtime) -> {"image", "pinned", "digest"}`.

- [ ] **Step 1: écrire les tests**

```python
"""v1.75.0 : l'image VDDK construite et poussée sans podman ni buildah,
contre deux registres simulés (API v2) : la base est recopiée couche par
couche d'un registre anonyme, l'archive VDDK de l'exploitant devient la
couche du dessus telle quelle, la cible exige un jeton Bearer, et les blobs
redirigés vers un « CDN » qui refuse tout en-tête Authorization."""

import base64
import gzip
import hashlib
import io
import json
import re
import sys
import tarfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))
import oci_push as op  # noqa: E402


def sha(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


class FakeRegistry:
    """Registre v2 en mémoire : Bearer optionnel (jeton contre Basic),
    blobs GET redirigés optionnels vers /cdn/ (refuse Authorization)."""

    def __init__(self, user=None, password=None, redirect_blobs=False):
        self.blobs, self.manifests, self.uploads = {}, {}, []
        self.user, self.password, self.redirect = user, password, redirect_blobs
        reg = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_GET(self):
                self._route()

            def do_HEAD(self):
                self._route()

            def do_POST(self):
                self._route()

            def do_PUT(self):
                self._route()

            def _route(self):
                path, _, query = self.path.partition("?")
                if path == "/token":
                    want = "Basic " + base64.b64encode(f"{reg.user}:{reg.password}".encode()).decode()
                    if self.headers.get("Authorization") != want:
                        return self._send(401, b"{}")
                    return self._send(200, json.dumps({"token": "good-token"}).encode())
                if path.startswith("/cdn/"):
                    if self.headers.get("Authorization"):
                        return self._send(400, b"only one auth mechanism allowed")
                    return self._send(200, reg.blobs[path[5:]])
                if reg.user is not None and self.headers.get("Authorization") != "Bearer good-token":
                    return self._send(401, b"{}", {"WWW-Authenticate":
                                                   f'Bearer realm="{reg.url}/token",service="fake",scope="repository:x:pull"'})
                if path == "/v2/":
                    return self._send(200, b"{}")
                m = re.match(r"^/v2/(.+?)/(blobs|manifests)/(uploads/)?(.*)$", path)
                if not m:
                    return self._send(404)
                repo, what, up, ref = m.groups()
                if what == "blobs" and up:
                    if self.command == "POST":
                        return self._send(202, headers={"Location": f"/v2/{repo}/blobs/uploads/{uuid.uuid4().hex}"})
                    data = self._body()
                    digest = urllib.parse.parse_qs(query)["digest"][0]
                    if sha(data) != digest:
                        return self._send(400, b"digest mismatch")
                    reg.blobs[digest] = data
                    reg.uploads.append(digest)
                    return self._send(201, headers={"Docker-Content-Digest": digest})
                if what == "blobs":
                    if ref not in reg.blobs:
                        return self._send(404)
                    if self.command == "GET" and reg.redirect:
                        return self._send(307, headers={"Location": f"{reg.url}/cdn/{ref}"})
                    return self._send(200, reg.blobs[ref])
                if self.command == "PUT":
                    body = self._body()
                    mt = self.headers.get("Content-Type")
                    reg.manifests[(repo, ref)] = (body, mt)
                    reg.manifests[(repo, sha(body))] = (body, mt)
                    return self._send(201, headers={"Docker-Content-Digest": sha(body)})
                if (repo, ref) not in reg.manifests:
                    return self._send(404)
                body, mt = reg.manifests[(repo, ref)]
                return self._send(200, body, {"Content-Type": mt, "Docker-Content-Digest": sha(body)})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.host = f"127.0.0.1:{self.srv.server_address[1]}"
        self.url = f"http://{self.host}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def targz(files, links=()):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
        for name, target in links:
            ti = tarfile.TarInfo(name)
            ti.type, ti.linkname = tarfile.SYMTYPE, target
            t.addfile(ti)
    return gzip.compress(raw.getvalue(), mtime=0), raw.getvalue()


@pytest.fixture
def base():
    """Une base « bci-busybox » : index multi-architecture, amd64 et arm64."""
    reg = FakeRegistry()
    images = {}
    for arch in ("amd64", "arm64"):
        layer, tar = targz({"usr/bin/cp": f"cp-{arch}".encode()})
        cfg = json.dumps({"architecture": arch, "os": "linux", "config": {"Cmd": ["/bin/sh"]},
                          "rootfs": {"type": "layers", "diff_ids": [sha(tar)]}, "history": []}).encode()
        reg.blobs[sha(layer)], reg.blobs[sha(cfg)] = layer, cfg
        man = json.dumps({"schemaVersion": 2, "mediaType": op.M_DOCKER,
                          "config": {"mediaType": op.CONFIG_TYPE[op.M_DOCKER], "digest": sha(cfg), "size": len(cfg)},
                          "layers": [{"mediaType": op.LAYER_TYPE[op.M_DOCKER], "digest": sha(layer), "size": len(layer)}]}).encode()
        reg.manifests[("bci/bci-busybox", sha(man))] = (man, op.M_DOCKER)
        images[arch] = (sha(man), sha(layer), sha(tar))
    idx = json.dumps({"schemaVersion": 2, "mediaType": op.M_DOCKER_LIST, "manifests": [
        {"mediaType": op.M_DOCKER, "digest": images[a][0], "size": 1, "platform": {"os": "linux", "architecture": a}}
        for a in ("arm64", "amd64")]}).encode()
    reg.manifests[("bci/bci-busybox", "16.0")] = (idx, op.M_DOCKER_LIST)
    reg.images = images
    yield reg
    reg.close()


@pytest.fixture
def vddk(tmp_path):
    data, tar = targz({"vmware-vix-disklib-distrib/lib64/libvixDiskLib.so.8": b"\x7fELF...",
                       "vmware-vix-disklib-distrib/doc/README": b"doc"},
                      links=[("vmware-vix-disklib-distrib/lib64/libvixDiskLib.so", "libvixDiskLib.so.8")])
    p = tmp_path / "VMware-vix-disklib-8.0.3.x86_64.tar.gz"
    p.write_bytes(data)
    return p, sha(data), sha(tar)


def test_the_vddk_archive_is_the_layer_as_is(vddk):
    p, digest, diff_id = vddk
    assert op.archive_layer(p) == {"digest": digest, "diff_id": diff_id, "size": p.stat().st_size}


@pytest.mark.parametrize("files,links,match", [
    ({"etc/passwd": b"x", "vmware-vix-disklib-distrib/lib64/libvixDiskLib.so.8": b"x"}, (), "unexpected entry"),
    ({"/vmware-vix-disklib-distrib/lib64/libvixDiskLib.so.8": b"x"}, (), "unexpected entry"),
    ({"vmware-vix-disklib-distrib/../x": b"x"}, (), "unexpected entry"),
    ({"vmware-vix-disklib-distrib/lib64/libvixDiskLib.so.8": b"x"},
     [("vmware-vix-disklib-distrib/lib64/evil", "../../../etc/shadow")], "link"),
    ({"vmware-vix-disklib-distrib/doc/README": b"x"}, (), "not a VDDK"),
])
def test_an_archive_that_is_not_a_clean_vddk_is_refused(tmp_path, files, links, match):
    data, _ = targz(files, links)
    p = tmp_path / "x.tar.gz"
    p.write_bytes(data)
    with pytest.raises(ValueError, match=match):
        op.archive_layer(p)


def test_references_are_read_like_docker_does():
    assert op.parse_ref("172.16.1.11:3000/jniedergang/vddk:8.0.3") == ("172.16.1.11:3000", "jniedergang/vddk", "8.0.3")
    assert op.parse_ref("registry.suse.com/bci/bci-busybox:16.0") == ("registry.suse.com", "bci/bci-busybox", "16.0")
    assert op.parse_ref("busybox") == ("registry-1.docker.io", "library/busybox", "latest")
    assert op.parse_ref("harbor.lan/p/vddk@sha256:" + "b" * 64)[2] == "sha256:" + "b" * 64
    with pytest.raises(ValueError):
        op.parse_ref("Harbor.lan/P/vddk:1")


def test_the_image_is_the_amd64_base_plus_the_vddk_layer(base, vddk):
    p, digest, diff_id = vddk
    target = FakeRegistry(user="ju", password="s3cret")
    try:
        res = op.push_vddk_image(p, f"{target.host}/ju/vddk:8.0.3", base=f"{base.host}/bci/bci-busybox:16.0",
                                 target_creds={"username": "ju", "password": "s3cret"},
                                 plain_http=True, base_plain_http=True, now=lambda: time.gmtime(0))
        body, mt = target.manifests[("ju/vddk", "8.0.3")]
        man = json.loads(body)
        assert mt == op.M_DOCKER and res["digest"] == sha(body)
        assert res["pinned"] == f"{target.host}/ju/vddk@{sha(body)}" and res["image"] == f"{target.host}/ju/vddk:8.0.3"
        amd_man, amd_layer, amd_tar = base.images["amd64"]
        assert [l["digest"] for l in man["layers"]] == [amd_layer, digest]
        assert man["layers"][1]["mediaType"] == op.LAYER_TYPE[op.M_DOCKER]
        cfg = json.loads(target.blobs[man["config"]["digest"]])
        assert cfg["rootfs"]["diff_ids"] == [amd_tar, diff_id]
        assert cfg["config"]["Entrypoint"] == ["cp", "-r", "/vmware-vix-disklib-distrib", "/opt"]
        assert cfg["config"]["Cmd"] is None and cfg["created"] == "1970-01-01T00:00:00Z"
        assert base.images["arm64"][1] not in target.blobs            # la seule base amd64
        assert target.blobs[digest] == p.read_bytes()                 # l'archive telle quelle
    finally:
        target.close()


def test_a_second_push_sends_no_blob_again(base, vddk):
    p, _, _ = vddk
    target = FakeRegistry()
    try:
        kw = dict(base=f"{base.host}/bci/bci-busybox:16.0", plain_http=True, base_plain_http=True,
                  now=lambda: time.gmtime(0))
        op.push_vddk_image(p, f"{target.host}/ju/vddk:8.0.3", **kw)
        first = len(target.uploads)
        op.push_vddk_image(p, f"{target.host}/ju/vddk:8.0.3", **kw)
        assert first == 3 and len(target.uploads) == first     # base, VDDK, configuration
    finally:
        target.close()


def test_redirected_blobs_are_fetched_without_the_credentials(vddk, base):
    # la base exige un jeton ET renvoie ses blobs vers un « CDN » qui refuse
    # tout en-tête Authorization : réussir prouve qu'il n'a pas suivi
    base.redirect, base.user, base.password = True, "reader", "pw"
    p, _, _ = vddk
    target = FakeRegistry()
    try:
        res = op.push_vddk_image(p, f"{target.host}/ju/vddk:1", base=f"{base.host}/bci/bci-busybox:16.0",
                                 base_creds={"username": "reader", "password": "pw"},
                                 plain_http=True, base_plain_http=True)
        assert base.images["amd64"][1] in target.blobs and res["digest"].startswith("sha256:")
    finally:
        target.close()


def test_wrong_credentials_are_said_without_the_password(base, vddk):
    p, _, _ = vddk
    target = FakeRegistry(user="ju", password="right")
    try:
        with pytest.raises(op.RegistryError, match="authentication refused") as e:
            op.push_vddk_image(p, f"{target.host}/ju/vddk:1", base=f"{base.host}/bci/bci-busybox:16.0",
                               target_creds={"username": "ju", "password": "wrong-pw"},
                               plain_http=True, base_plain_http=True)
        assert "wrong-pw" not in str(e.value)
    finally:
        target.close()


def test_a_digest_is_not_a_place_to_push_to(vddk):
    p, _, _ = vddk
    with pytest.raises(ValueError, match="tag"):
        op.push_vddk_image(p, "harbor.lan/p/vddk@sha256:" + "c" * 64)
```

- [ ] **Step 2: les lancer, ils échouent**

Run: `python3 -m pytest tests/api/test_oci_push_175.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'oci_push'`

- [ ] **Step 3: écrire `bin/lib/oci_push.py`**

```python
"""harvester-ops : l'image VDDK construite et poussée sans outil de conteneur (v1.75.0).

Forklift copie le VDDK de VMware dans ses pods par une image d'amorçage
(`vddkInitImage` du fournisseur) : le dossier vmware-vix-disklib-distrib/ à
la racine, et une commande qui le copie dans /opt. La licence de VMware
interdit de livrer le VDDK ; la console n'a ni podman ni buildah. L'image se
construit donc ici, par l'API de registre v2 :

- l'archive VDDK de VMware (tar.gz, tout sous vmware-vix-disklib-distrib/)
  EST la couche du dessus, telle quelle : son empreinte est celle du
  fichier, son diff_id celle du tar décompressé ;
- la base (bci-busybox de SUSE, pour `cp`) est recopiée couche par couche
  depuis son registre, sans être décompressée ; la version linux/amd64 d'un
  index multi-architecture ;
- jeton Bearer (realm, service, scope) ou Basic ; blobs envoyés d'un bloc ;
  redirections suivies SANS l'en-tête Authorization (les CDN des registres
  refusent un second mode d'authentification).

Bibliothèque standard seulement.
"""

import base64
import gzip
import hashlib
import json
import re
import ssl
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

VDDK_DIR = "vmware-vix-disklib-distrib"
VDDK_LIB = f"{VDDK_DIR}/lib64/libvixDiskLib.so"
DEFAULT_BASE = "registry.suse.com/bci/bci-busybox:16.0"
ENTRYPOINT = ["cp", "-r", f"/{VDDK_DIR}", "/opt"]

M_DOCKER = "application/vnd.docker.distribution.manifest.v2+json"
M_DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
M_OCI = "application/vnd.oci.image.manifest.v1+json"
M_OCI_INDEX = "application/vnd.oci.image.index.v1+json"
LAYER_TYPE = {M_DOCKER: "application/vnd.docker.image.rootfs.diff.tar.gzip",
              M_OCI: "application/vnd.oci.image.layer.v1.tar+gzip"}
CONFIG_TYPE = {M_DOCKER: "application/vnd.docker.container.image.v1+json",
               M_OCI: "application/vnd.oci.image.config.v1+json"}
ACCEPT = ", ".join((M_OCI_INDEX, M_DOCKER_LIST, M_OCI, M_DOCKER))

REF_RE = re.compile(r"^(?P<reg>[a-z0-9.-]+(?::\d{1,5})?)/"
                    r"(?P<repo>[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)"
                    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?(?:@(?P<digest>sha256:[0-9a-f]{64}))?$")


class RegistryError(Exception):
    pass


def digest_of(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def parse_ref(ref):
    """(registre, dépôt, étiquette ou empreinte), lus comme Docker : un
    premier élément sans point, deux-points ni « localhost » est Docker Hub."""
    ref = str(ref or "").strip()
    first, _, rest = ref.partition("/")
    if not rest or not ("." in first or ":" in first or first == "localhost"):
        ref = "docker.io/" + (ref if rest else "library/" + ref)
    m = REF_RE.match(ref)
    if not m:
        raise ValueError(f"image: {ref!r} is not an image reference")
    reg = "registry-1.docker.io" if m.group("reg") == "docker.io" else m.group("reg")
    return reg, m.group("repo"), m.group("digest") or m.group("tag") or "latest"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Registry:
    """Client minimal de l'API de registre v2 (distribution)."""

    def __init__(self, host, creds=None, plain_http=False, timeout=120):
        self.host = host
        self.creds = creds or None
        self.base = f"{'http' if plain_http else 'https'}://{host}"
        self.timeout = timeout
        self.auth = {}
        self.opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def _basic(self):
        if not self.creds:
            return None
        raw = f"{self.creds.get('username', '')}:{self.creds.get('password', '')}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _token(self, challenge, scope):
        p = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        if "realm" not in p:
            raise RegistryError(f"{self.host}: unreadable authentication challenge")
        q = {"service": p.get("service", "")}
        if scope:
            q["scope"] = scope
        req = urllib.request.Request(p["realm"] + "?" + urllib.parse.urlencode(q))
        if self._basic():
            req.add_header("Authorization", self._basic())
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            raise RegistryError(f"{self.host}: authentication refused (HTTP {e.code})") from None
        tok = body.get("token") or body.get("access_token")
        if not tok:
            raise RegistryError(f"{self.host}: authentication refused (no token)")
        return "Bearer " + tok

    def request(self, method, url, scope, data=None, headers=None, ok=()):
        """(code, en-têtes, corps). Un 401 fait prendre un jeton ou poser
        Basic, une fois ; une redirection est suivie sans identifiant."""
        if not url.startswith("http"):
            url = self.base + url
        for attempt in (0, 1):
            h = dict(headers or {})
            if self.auth.get(scope):
                h["Authorization"] = self.auth[scope]
            req = urllib.request.Request(url, data=data, method=method, headers=h)
            try:
                with self.opener.open(req, timeout=self.timeout) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and method in ("GET", "HEAD") and e.headers.get("Location"):
                    loc = urllib.parse.urljoin(url, e.headers["Location"])
                    with self.opener.open(urllib.request.Request(loc, method=method), timeout=self.timeout) as r:
                        return r.status, r.headers, r.read()
                if e.code == 401 and attempt == 0:
                    ch = e.headers.get("WWW-Authenticate", "")
                    if ch.lower().startswith("bearer"):
                        self.auth[scope] = self._token(ch, scope)
                    elif self._basic():
                        self.auth[scope] = self._basic()
                    else:
                        raise RegistryError(f"{self.host}: authentication required") from None
                    continue
                if e.code in ok:
                    return e.code, e.headers, b""
                if e.code == 401:
                    raise RegistryError(f"{self.host}: authentication refused") from None
                text = e.read()[:200].decode(errors="replace")
                raise RegistryError(f"{method} {self.host}{urllib.parse.urlparse(url).path}: HTTP {e.code} {text}") from None
        raise RegistryError(f"{self.host}: authentication refused")

    def manifest(self, repo, ref):
        _, h, body = self.request("GET", f"/v2/{repo}/manifests/{ref}", f"repository:{repo}:pull",
                                  headers={"Accept": ACCEPT})
        mt = (h.get("Content-Type") or "").split(";")[0].strip() or json.loads(body).get("mediaType", "")
        return body, mt

    def blob(self, repo, digest):
        _, _, body = self.request("GET", f"/v2/{repo}/blobs/{digest}", f"repository:{repo}:pull")
        if digest_of(body) != digest:
            raise RegistryError(f"{self.host}/{repo}: blob {digest[:19]} does not match its digest")
        return body

    def has_blob(self, repo, digest):
        code, _, _ = self.request("HEAD", f"/v2/{repo}/blobs/{digest}", f"repository:{repo}:pull,push", ok=(404,))
        return code == 200

    def push_blob(self, repo, digest, data):
        """Envoi d'un bloc (POST puis PUT ?digest=) ; False si déjà présent."""
        if self.has_blob(repo, digest):
            return False
        scope = f"repository:{repo}:pull,push"
        _, h, _ = self.request("POST", f"/v2/{repo}/blobs/uploads/", scope, data=b"",
                               headers={"Content-Length": "0"})
        loc = h.get("Location")
        if not loc:
            raise RegistryError(f"{self.host}/{repo}: no upload location")
        loc = urllib.parse.urljoin(self.base + "/", loc)
        sep = "&" if "?" in loc else "?"
        self.request("PUT", f"{loc}{sep}digest={urllib.parse.quote(digest)}", scope, data=data,
                     headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(data))})
        return True

    def put_manifest(self, repo, tag, body, media_type):
        _, h, _ = self.request("PUT", f"/v2/{repo}/manifests/{tag}", f"repository:{repo}:pull,push",
                               data=body, headers={"Content-Type": media_type})
        return h.get("Docker-Content-Digest") or digest_of(body)


def archive_layer(path):
    """L'archive VDDK comme couche, après contrôle : tout sous
    vmware-vix-disklib-distrib/, chemins relatifs sans « .. », liens qui ne
    sortent pas du dossier, et la bibliothèque présente."""
    h_gz, h_tar, size = hashlib.sha256(), hashlib.sha256(), 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h_gz.update(chunk)
            size += len(chunk)
    with gzip.open(path, "rb") as g:
        for chunk in iter(lambda: g.read(1 << 20), b""):
            h_tar.update(chunk)
    names = []
    with tarfile.open(path, "r:gz") as t:
        for m in t:
            n = m.name[2:] if m.name.startswith("./") else m.name
            parts = n.split("/")
            if n.startswith("/") or ".." in parts or parts[0] != VDDK_DIR:
                raise ValueError(f"VDDK archive: unexpected entry {m.name!r}, everything must be under {VDDK_DIR}/")
            if m.issym() or m.islnk():
                target = m.linkname
                if target.startswith("/") or ".." in target.split("/"):
                    raise ValueError(f"VDDK archive: link {m.name!r} points outside ({target!r})")
            names.append(n)
    if not any(n.startswith(VDDK_LIB) for n in names):
        raise ValueError(f"VDDK archive: no {VDDK_LIB}, this is not a VDDK")
    return {"digest": "sha256:" + h_gz.hexdigest(), "diff_id": "sha256:" + h_tar.hexdigest(), "size": size}


def vddk_config(base_config, diff_id, created):
    cfg = json.loads(json.dumps(base_config))
    cfg.setdefault("rootfs", {"type": "layers", "diff_ids": []}).setdefault("diff_ids", []).append(diff_id)
    c = cfg.get("config") or {}
    c["Entrypoint"] = list(ENTRYPOINT)
    c["Cmd"] = None
    c["User"] = "1001"
    c["Labels"] = dict(c.get("Labels") or {}, **{"io.harvester-ops.vddk": "true"})
    cfg["config"] = c
    cfg["created"] = created
    cfg.setdefault("history", []).append({"created": created,
                                          "created_by": f"harvester-ops: VDDK layer ({VDDK_DIR})"})
    return cfg


def push_vddk_image(archive, image, base=DEFAULT_BASE, target_creds=None, base_creds=None,
                    plain_http=False, base_plain_http=False, step=lambda msg: None, now=time.gmtime):
    t_reg, t_repo, t_tag = parse_ref(image)
    if t_tag.startswith("sha256:"):
        raise ValueError("image: give a tag to push to, not a digest")
    layer = archive_layer(archive)
    step(f"VDDK archive checked ({layer['size'] >> 20} MiB)")
    b_reg, b_repo, b_ref = parse_ref(base)
    src = Registry(b_reg, base_creds, plain_http=base_plain_http)
    dst = Registry(t_reg, target_creds, plain_http=plain_http)
    body, mt = src.manifest(b_repo, b_ref)
    if mt in (M_OCI_INDEX, M_DOCKER_LIST):
        pick = next((m for m in json.loads(body).get("manifests", [])
                     if (m.get("platform") or {}).get("os") == "linux"
                     and (m.get("platform") or {}).get("architecture") == "amd64"), None)
        if not pick:
            raise RegistryError(f"{base}: no linux/amd64 image")
        body, mt = src.manifest(b_repo, pick["digest"])
    if mt not in (M_OCI, M_DOCKER):
        raise RegistryError(f"{base}: unsupported manifest type {mt!r}")
    man = json.loads(body)
    cfg = json.loads(src.blob(b_repo, man["config"]["digest"]))
    for lay in man["layers"]:
        if not dst.has_blob(t_repo, lay["digest"]):
            dst.push_blob(t_repo, lay["digest"], src.blob(b_repo, lay["digest"]))
            step(f"base layer {lay['digest'][7:19]} copied")
    with open(archive, "rb") as f:
        if dst.push_blob(t_repo, layer["digest"], f.read()):
            step("VDDK layer sent")
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", now())
    new_cfg = json.dumps(vddk_config(cfg, layer["diff_id"], created), separators=(",", ":")).encode()
    dst.push_blob(t_repo, digest_of(new_cfg), new_cfg)
    new_man = {"schemaVersion": 2, "mediaType": mt,
               "config": {"mediaType": CONFIG_TYPE[mt], "digest": digest_of(new_cfg), "size": len(new_cfg)},
               "layers": man["layers"] + [{"mediaType": LAYER_TYPE[mt], "digest": layer["digest"],
                                           "size": layer["size"]}]}
    digest = dst.put_manifest(t_repo, t_tag, json.dumps(new_man, separators=(",", ":")).encode(), mt)
    step(f"pushed {t_reg}/{t_repo}:{t_tag}")
    return {"image": f"{t_reg}/{t_repo}:{t_tag}", "pinned": f"{t_reg}/{t_repo}@{digest}", "digest": digest}
```

- [ ] **Step 4: les tests passent**

Run: `python3 -m pytest tests/api/test_oci_push_175.py -q`
Expected: tous verts. Si `test_a_second_push_sends_no_blob_again` compte 4 envois au premier passage, c'est que la configuration part deux fois : `push_blob` rend False quand le blob existe, ne pas l'appeler deux fois.

- [ ] **Step 5: commit**

```bash
git add bin/lib/oci_push.py tests/api/test_oci_push_175.py
git commit -m "feat(1.75.0): build and push the VDDK image without a container tool

VMware's licence keeps the VDDK out of the deliverable and the console has
neither podman nor buildah, so the image is assembled through the registry
API: the operator's archive is the top layer as is, the BCI busybox base
is copied layer by layer, Bearer or Basic auth, and redirected blobs are
fetched without credentials since registry CDNs refuse a second auth."
```

---

### Task 4 : l'outil `harvester-forklift`, `install` et `status`

**Files:**
- Create: `bin/harvester-forklift.py` (mode 0755)
- Modify: `bin/lib/kube.py` (méthode `port_forward`, utilisée en Task 6)
- Modify: `container/Containerfile:59`, `install.sh:115` (lien sans extension)
- Test: `tests/api/test_forklift_cli_175.py`

**Interfaces:**
- Consumes: `hv_forklift` (Tasks 1, 2), `oci_push` (Task 3), `kube.Kube`, `kube.KubeError`, `kube.cluster_config`.
- Produces: `cmd_status(args, kube=None)`, `cmd_install(args, kube=None, sleep=time.sleep, now=time.time) -> int`, `until(fn, timeout, label, sleep, now, every=5) -> int`, codes `EXIT_OK=0, EXIT_FAIL=1, EXIT_REFUSED=2`, `main(argv) -> int`.

- [ ] **Step 1: écrire les tests**

```python
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
        self.calls.append(("patch", kind))
        self.objs[(kind, ns, name)]["spec"].update(patch.get("spec") or {})

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


def test_status_reads_everything_without_writing(capsys):
    k = FakeKube(cert_manager=True)
    run_install(k)
    k.calls.clear()
    assert hfk.cmd_status(argparse.Namespace(cluster=None, kubeconfig="kc"), kube=k) == hfk.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["install"]["ready"] is True and out["providers"] == []
    assert not [c for c in k.calls if c[0] in ("create", "apply", "patch", "delete")]
```

- [ ] **Step 2: les lancer, ils échouent**

Run: `python3 -m pytest tests/api/test_forklift_cli_175.py -q`
Expected: FAIL, `FileNotFoundError` sur `bin/harvester-forklift.py`

- [ ] **Step 3: ajouter `Kube.port_forward` à `bin/lib/kube.py` (après `raw_stream`)**

```python
    @contextlib.contextmanager
    def port_forward(self, ns, target, port, timeout=20):
        """Un port local vers `target` (svc/nom) : rend le port choisi par
        kubectl, et coupe le relais en sortie."""
        p = subprocess.Popen(self._base() + ["port-forward", "-n", ns, target, f":{port}"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + timeout
            local = None
            while time.time() < deadline and local is None:
                ready, _, _ = select.select([p.stdout], [], [], 1)
                if ready:
                    m = re.search(r"127\.0\.0\.1:(\d+)", p.stdout.readline())
                    local = int(m.group(1)) if m else None
                if p.poll() is not None:
                    break
            if local is None:
                raise KubeError("port-forward failed: " + (p.stderr.read() if p.poll() is not None else "no port")[:300])
            yield local
        finally:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
```

Et en tête de `kube.py`, s'ils manquent : `import contextlib`, `import re`, `import select`, `import time`.

- [ ] **Step 4: écrire `bin/harvester-forklift.py`**

```python
#!/usr/bin/env python3
"""Forklift sur un cluster Harvester : installation, image VDDK, fournisseur vCenter, inventaire.

    harvester-forklift status          --cluster C
    harvester-forklift install         --cluster C [--chart-version V] [--image-tag T]
                                       [--cert-manager-manifest FICHIER]
    harvester-forklift vddk-image      --archive VDDK.tar.gz --image REGISTRE/DEPOT:TAG
                                       [--base IMAGE] [--plain-http] [--auth-stdin]
    harvester-forklift provider-apply  --cluster C --namespace NS --name N   (JSON sur stdin)
    harvester-forklift provider-delete --cluster C --namespace NS --name N [--with-secret]
    harvester-forklift inventory       --cluster C --namespace NS --name N --kind vms|networks|datastores

Harvester 1.9 ne livre pas Forklift : `install` pose cert-manager (depuis le
manifeste que la console tire de son paquet Cluster API), l'add-on
expérimental forklift-operator puis le ForkliftController. Les secrets
(vCenter, registre) arrivent en JSON sur l'entrée standard, jamais en
argument. Progression sur stderr au format STEP_EVENT|<étape>|<statut>|<message>.
Codes : 0 succès, 1 échec, 2 refus. Bibliothèque standard seulement.
Voir docs/design/2026-09-27-migrations-vmware.md.
"""

import argparse
import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import hv_forklift as hf  # noqa: E402
import oci_push as op  # noqa: E402
from kube import Kube, KubeError, cluster_config  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2


def step(sid, status, msg=""):
    clean = " ".join(str(msg).split())
    sys.stderr.write(f"STEP_EVENT|{sid}|{status}|{clean}\n")
    sys.stderr.flush()


def kube_from(args):
    entry = cluster_config(args.cluster) if args.cluster else None
    kc = args.kubeconfig or (entry or {}).get("kubeconfig")
    if not kc:
        raise ValueError("give --cluster (with a kubeconfig in the configuration) or --kubeconfig")
    return Kube(kc)


def get_opt(kube, kind, ns, name):
    """Un objet d'une CRD qui peut ne pas exister encore (avant l'add-on)."""
    try:
        return kube.get(kind, ns, name)
    except KubeError as e:
        if "doesn't have a resource type" in str(e):
            return None
        raise


def read_stdin_json():
    raw = sys.stdin.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        raise ValueError("stdin: JSON expected") from None


def until(fn, timeout, label, sleep=time.sleep, now=time.time, every=5):
    """Relit `fn()` -> (True|False|None, message) jusqu'à la fin ou le délai."""
    deadline, last = now() + timeout, None
    while True:
        res, msg = fn()
        if msg != last:
            step(label, "running" if res is None else ("done" if res else "error"), msg)
            last = msg
        if res is not None:
            return EXIT_OK if res else EXIT_FAIL
        if now() >= deadline:
            step(label, "error", f"not done after {timeout} s: {msg}")
            return EXIT_FAIL
        sleep(every)


def install_state(kube):
    by_name = lambda items: {(d.get("metadata") or {}).get("name"): d for d in items}  # noqa: E731
    return hf.install_state(kube.get(hf.K_ADDON, hf.NS, hf.ADDON[1]),
                            by_name(kube.list(hf.K_DEPLOY, hf.NS)),
                            get_opt(kube, hf.K_CONTROLLER, hf.NS, hf.CONTROLLER_NAME),
                            by_name(kube.list(hf.K_DEPLOY, hf.CERT_MANAGER[0])))


def cmd_status(args, kube=None):
    kube = kube or kube_from(args)
    st = install_state(kube)
    providers = []
    if st["controller"]:
        for p in kube.list(hf.K_PROVIDER, None):
            m = p.get("metadata") or {}
            res, msg = hf.provider_state(p)
            providers.append({"namespace": m.get("namespace"), "name": m.get("name"),
                              "type": (p.get("spec") or {}).get("type"), "ready": res, "message": msg})
    print(json.dumps({"install": st, "providers": providers}))
    return EXIT_OK


def cmd_install(args, kube=None, sleep=time.sleep, now=time.time):
    kube = kube or kube_from(args)
    want = hf.addon_manifest(args.chart_version, args.image_tag)      # refuse avant d'écrire
    st = install_state(kube)
    if st["cert_manager"]:
        step("cert-manager", "done", "cert-manager is running")
    else:
        if not args.cert_manager_manifest:
            step("cert-manager", "error", "cert-manager is missing: give --cert-manager-manifest "
                 "(the console takes it from its Cluster API bundle)")
            return EXIT_REFUSED
        step("cert-manager", "running", "installing cert-manager")
        kube.run("apply", "--server-side", "--force-conflicts", "-f", args.cert_manager_manifest, timeout=300)

        def cm():
            s = install_state(kube)
            return (True, "cert-manager is running") if s["cert_manager"] else \
                (None, "waiting for " + ", ".join(s["cert_manager_missing"]))
        rc = until(cm, args.timeout, "cert-manager", sleep, now)
        if rc:
            return rc
    if kube.get("namespaces", None, hf.NS) is None:
        kube.create(hf.namespace_manifest())
    cur = kube.get(hf.K_ADDON, hf.NS, hf.ADDON[1])
    if cur is None:
        kube.create(want)
        step("addon", "running", f"forklift-operator {args.chart_version} declared, images {args.image_tag}")
    elif any((cur.get("spec") or {}).get(k) != v for k, v in want["spec"].items()):
        kube.patch(hf.K_ADDON, hf.NS, hf.ADDON[1], {"spec": want["spec"]})
        step("addon", "running", f"forklift-operator set to {args.chart_version}, images {args.image_tag}")
        sleep(3)          # le contrôleur des add-ons prend la main : l'ancien statut n'est pas la fin

    def addon():
        s = install_state(kube)
        if s["addon"] == "failed":
            return False, s["addon_message"]
        if s["addon"] == "ready" and s["operator"]:
            return True, "forklift-operator is running"
        return None, s["addon_message"] if s["addon"] != "ready" else "waiting for the operator"
    rc = until(addon, args.timeout, "addon", sleep, now)
    if rc:
        return rc
    if get_opt(kube, hf.K_CONTROLLER, hf.NS, hf.CONTROLLER_NAME) is None:
        kube.create(hf.controller_manifest())
        step("controller", "running", "ForkliftController created: the operator deploys Forklift")

    def components():
        s = install_state(kube)
        if not s["components_missing"]:
            return True, "Forklift components are running"
        probs = hf.pod_problems(kube.list("pods", hf.NS))
        return None, "waiting for " + ", ".join(s["components_missing"]) + (f" ({'; '.join(probs)})" if probs else "")
    rc = until(components, args.timeout, "controller", sleep, now)
    if rc:
        return rc
    kube.apply(hf.inventory_rbac())
    step("install", "done", "Forklift is installed")
    return EXIT_OK


def build_parser():
    ap = argparse.ArgumentParser(prog="harvester-forklift", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cluster_args(sp):
        sp.add_argument("--cluster")
        sp.add_argument("--kubeconfig")

    sp = sub.add_parser("status", help="Forklift on the cluster and its providers, as JSON")
    cluster_args(sp)
    sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("install", help="cert-manager, the forklift-operator add-on and the ForkliftController")
    cluster_args(sp)
    sp.add_argument("--chart-version", default=hf.CHART_VERSION)
    sp.add_argument("--image-tag", default=hf.IMAGE_TAG, help="tag of the harvester-forklift-* images")
    sp.add_argument("--cert-manager-manifest", help="cert-manager YAML, applied if cert-manager is missing")
    sp.add_argument("--timeout", type=int, default=900)
    sp.set_defaults(fn=cmd_install)
    return ap, sub


def main(argv=None):
    ap, _ = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, KubeError, op.RegistryError) as e:
        step(args.cmd, "error", str(e))
        return EXIT_REFUSED if isinstance(e, ValueError) else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
```

Puis : `chmod 755 bin/harvester-forklift.py`.

- [ ] **Step 5: lien sans extension dans le conteneur et à l'installation**

Dans `container/Containerfile`, sur la ligne 59 `ln -sf /usr/local/bin/harvester-vm-transfer.py /usr/local/bin/harvester-vm-transfer && \`, ajouter juste après :

```dockerfile
    ln -sf /usr/local/bin/harvester-forklift.py /usr/local/bin/harvester-forklift && \
```

Dans `install.sh`, après la ligne 115 `ln -sf "$PREFIX/harvester-vm-transfer.py" "$PREFIX/harvester-vm-transfer"`, ajouter :

```bash
    ln -sf "$PREFIX/harvester-forklift.py" "$PREFIX/harvester-forklift"
```

- [ ] **Step 6: les tests passent, suite complète comprise**

Run: `python3 -m pytest tests/api/test_forklift_cli_175.py tests/api/test_bin_helpers_are_deployed.py -q`
Expected: tous verts

- [ ] **Step 7: commit**

```bash
git add bin/harvester-forklift.py bin/lib/kube.py container/Containerfile install.sh tests/api/test_forklift_cli_175.py
git commit -m "feat(1.75.0): harvester-forklift install and status

Installs in the order the pieces depend on each other: cert-manager from
the manifest the console carries, the forklift-operator add-on with its
images named, the ForkliftController, then the service account that reads
the inventory. It refuses plainly without cert-manager, recreates nothing
on a second run, and names a pod that cannot pull its image."
```

---

### Task 5 : `vddk-image`, `provider-apply`, `provider-delete`

**Files:**
- Modify: `bin/harvester-forklift.py`
- Test: `tests/api/test_forklift_cli_175.py` (ajout)

**Interfaces:**
- Consumes: `op.push_vddk_image`, `hf.provider_secret`, `hf.provider_manifest`, `hf.provider_state`, `hf.plans_using`, `until`, `read_stdin_json`, `install_state`.
- Produces: `cmd_vddk_image(args) -> int` (imprime `{"image","pinned","digest"}` sur stdout), `cmd_provider_apply(args, kube=None, sleep, now) -> int`, `cmd_provider_delete(args, kube=None, sleep, now) -> int`.

- [ ] **Step 1: écrire les tests (ajout)**

```python
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
    k.objs[(hf.K_PROVIDER, "default", "vmwlab")] = {"metadata": {"name": "vmwlab", "namespace": "default"}, "spec": {}}
    k.objs[(hf.K_PLAN, "mig", "wave-1")] = {"metadata": {"name": "wave-1", "namespace": "mig"},
                                          "spec": {"provider": {"source": {"name": "vmwlab", "namespace": "default"}}}}
    c = Clock()
    assert hfk.cmd_provider_delete(prov_args(), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_REFUSED
    assert "mig/wave-1" in capsys.readouterr().err and not [x for x in k.calls if x[0] == "delete"]


def test_delete_takes_the_secret_only_if_the_console_made_it():
    k = installed()
    k.objs[(hf.K_PROVIDER, "default", "vmwlab")] = {"metadata": {"name": "vmwlab", "namespace": "default"},
                                                    "spec": {"secret": {"name": "vmwlab-vsphere", "namespace": "default"}}}
    k.objs[("secrets", "default", "vmwlab-vsphere")] = {"metadata": {"labels": {"harvester-ops.io/managed": "true"}}}
    c = Clock()
    assert hfk.cmd_provider_delete(prov_args(with_secret=True), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_OK
    assert ("delete", "secrets", "vmwlab-vsphere") in k.calls
    k.objs[(hf.K_PROVIDER, "default", "vmwlab")] = {"metadata": {}, "spec": {"secret": {"name": "theirs", "namespace": "default"}}}
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
```

- [ ] **Step 2: les lancer, ils échouent**

Run: `python3 -m pytest tests/api/test_forklift_cli_175.py -q`
Expected: FAIL, `AttributeError: module 'hfk' has no attribute 'cmd_provider_apply'`

- [ ] **Step 3: ajouter les commandes à `bin/harvester-forklift.py` (avant `build_parser`)**

```python
def cmd_vddk_image(args):
    creds = read_stdin_json() if args.auth_stdin else None
    if args.auth_stdin and not (creds.get("username") and creds.get("password")):
        raise ValueError('--auth-stdin: {"username": ..., "password": ...} expected on stdin')
    step("vddk", "running", f"building the VDDK image from {Path(args.archive).name}")
    res = op.push_vddk_image(args.archive, args.image, base=args.base, target_creds=creds,
                             plain_http=args.plain_http, step=lambda m: step("vddk", "running", m))
    step("vddk", "done", f"{res['image']} ({res['digest'][:19]})")
    print(json.dumps(res))
    return EXIT_OK


def cmd_provider_apply(args, kube=None, sleep=time.sleep, now=time.time):
    kube = kube or kube_from(args)
    ns, name = hf.check_name(args.namespace, "namespace"), hf.check_name(args.name, "provider")
    spec = read_stdin_json()
    secret, prov = hf.provider_secret(ns, name, spec), hf.provider_manifest(ns, name, spec)
    if not install_state(kube)["ready"]:
        step("provider", "error", "Forklift is not installed and running on this cluster: run install first")
        return EXIT_REFUSED
    kube.apply([secret, prov])
    step("provider", "running", f"provider {ns}/{name} applied: Forklift checks the vCenter")
    return until(lambda: hf.provider_state(kube.get(hf.K_PROVIDER, ns, name)), args.timeout, "provider", sleep, now)


def cmd_provider_delete(args, kube=None, sleep=time.sleep, now=time.time):
    kube = kube or kube_from(args)
    ns, name = hf.check_name(args.namespace, "namespace"), hf.check_name(args.name, "provider")
    cur = get_opt(kube, hf.K_PROVIDER, ns, name)
    if cur is None:
        raise ValueError(f"no provider {ns}/{name}")
    users = hf.plans_using(ns, name, kube.list(hf.K_PLAN, None))
    if users:
        step("provider", "error", f"provider {ns}/{name} is used by migration plans: {', '.join(users)}")
        return EXIT_REFUSED
    kube.delete(hf.K_PROVIDER, ns, name)
    ref = (cur.get("spec") or {}).get("secret") or {}
    if args.with_secret and ref.get("name"):
        sec = kube.get("secrets", ref.get("namespace") or ns, ref["name"])
        if sec and ((sec.get("metadata") or {}).get("labels") or {}).get(hf.L_MANAGED) == "true":
            kube.delete("secrets", ref.get("namespace") or ns, ref["name"])
            step("provider", "running", f"secret {ref['name']} deleted")
    return until(lambda: (True, f"provider {ns}/{name} deleted") if get_opt(kube, hf.K_PROVIDER, ns, name) is None
                 else (None, "deleting"), args.timeout, "provider", sleep, now)
```

Et dans `build_parser`, avant `return ap, sub` :

```python
    sp = sub.add_parser("vddk-image", help="build the VDDK init image from VMware's archive and push it")
    sp.add_argument("--archive", required=True, help="VMware-vix-disklib-*.x86_64.tar.gz")
    sp.add_argument("--image", required=True, help="registry/path:tag to push to")
    sp.add_argument("--base", default=op.DEFAULT_BASE, help="base image with cp")
    sp.add_argument("--plain-http", action="store_true", help="the target registry speaks plain HTTP")
    sp.add_argument("--auth-stdin", action="store_true", help='{"username": ..., "password": ...} on stdin')
    sp.set_defaults(fn=cmd_vddk_image)
    for name, fn, hlp in (("provider-apply", cmd_provider_apply, "declare or change a vCenter provider (JSON on stdin)"),
                          ("provider-delete", cmd_provider_delete, "delete a vCenter provider")):
        sp = sub.add_parser(name, help=hlp)
        cluster_args(sp)
        sp.add_argument("--namespace", required=True)
        sp.add_argument("--name", required=True)
        sp.add_argument("--timeout", type=int, default=300)
        if name == "provider-delete":
            sp.add_argument("--with-secret", action="store_true", help="also delete the secret the console created")
        sp.set_defaults(fn=fn)
```

- [ ] **Step 4: les tests passent**

Run: `python3 -m pytest tests/api/test_forklift_cli_175.py -q`
Expected: tous verts

- [ ] **Step 5: commit**

```bash
git add bin/harvester-forklift.py tests/api/test_forklift_cli_175.py
git commit -m "feat(1.75.0): VDDK image and vCenter provider from harvester-forklift

The vCenter password and the registry credentials come as JSON on stdin,
never as arguments nor in any output. A provider is declared only once
Forklift runs, followed until Forklift has reached the vCenter or says why
it could not, and is not deleted while a migration plan uses it."
```

---

### Task 6 : banc réel, installation sur harvlab2

Aucune écriture dans le dépôt : c'est la vérification réelle de Tasks 1 à 5, et le relevé qui nourrit Task 7. Scripts dans le répertoire de travail de la session (scratchpad), jamais dans le dépôt.

- [ ] **Step 1: allumer node2, le banc VMware et harvlab2**

Vérifier d'abord la règle des deux lames : node3 et node4 éteints (Redfish `PowerState` sur 172.16.1.33 et .34, identifiants Vault `secret/infra/ilo`), puis allumer node2 par son iLO (172.16.1.32) et attendre le SSH.

```bash
tests/bench/vmware/vmwlab.sh start
HARVLAB_NAME=harvlab2 HARVLAB_NODES=1 tests/bench/harvlab/harvlab.sh start
until kubectl --kubeconfig ~/.kube/harvlab2.yaml get nodes >/dev/null 2>&1; do sleep 15; done
tests/bench/vmware/vmwlab.sh status
```

Expected: ESXi et vCenter répondent ; `kubectl get nodes` de harvlab2 donne `harvlab2-n1 Ready`.

- [ ] **Step 2: registre du banc (Gitea de node1) et accès de harvlab2**

1. Jeton Gitea d'écriture de paquets (`write:package`) et jeton de lecture (`read:package`), créés par l'API Gitea avec le compte `jniedergang` (mot de passe Vault `secret/services/gitea`), valeurs écrites dans Vault `secret/services/gitea` (champs `registry_push_token`, `registry_read_token`) par l'entrée standard, jamais affichées.
2. Vérifier que harvlab2 joint le registre : `ssh rancher@172.16.2.71 "curl -s -o /dev/null -w '%{http_code}' http://172.16.1.11:3000/v2/"`. Expected: `401`.
3. Réglage `containerd-registry` de harvlab2 par la console (fichier 0600 effacé aussitôt) :

```json
{"Mirrors": {"172.16.1.11:3000": {"Endpoints": ["http://172.16.1.11:3000"]}},
 "Configs": {"172.16.1.11:3000": {"Auth": {"Username": "jniedergang", "Password": "<registry_read_token>"}}}}
```

```bash
bin/harvester-resources.py setting set --cluster harvlab2 --name containerd-registry --value-file "$F"; rm -f "$F"
```

Expected: `STEP_EVENT|...|done|...` ; relever si RKE2 redémarre et combien de temps l'API manque (piège à noter en mémoire).

- [ ] **Step 3: installer Forklift**

Manifeste cert-manager tiré du paquet Cluster API du livrable :

```bash
tar xzf dist/capi-bundle-20260926-002651-caaph.tar.gz -C "$SP" capi-bundle/manifests/cert-manager/cert-manager.yaml
bin/harvester-forklift.py install --cluster harvlab2 \
    --cert-manager-manifest "$SP/capi-bundle/manifests/cert-manager/cert-manager.yaml"
kubectl --kubeconfig ~/.kube/harvlab2.yaml get deploy -n forklift
kubectl --kubeconfig ~/.kube/harvlab2.yaml get provider -A
```

Expected: `STEP_EVENT|install|done|Forklift is installed` ; les 5 déploiements du namespace forklift `1/1` ; un fournisseur `host` créé par Forklift. Si les images `v1.8.2` ne vont pas avec le chart 1.9.0 (pods en erreur), relancer avec `--chart-version 1.8.2` et retenir la paire qui marche comme défaut dans `hv_forklift.py` (CHART_VERSION, IMAGE_TAG), test mis à jour dans le même commit.

- [ ] **Step 4: image VDDK poussée, puis tirée et copiée par harvlab2**

```bash
printf '{"username":"jniedergang","password":"%s"}' "$PUSH_TOKEN" | bin/harvester-forklift.py vddk-image \
    --archive ~/ISO/VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz \
    --image 172.16.1.11:3000/jniedergang/vddk:8.0.3 --plain-http --auth-stdin
```

(`$PUSH_TOKEN` lu dans Vault par une variable, jamais tapé ni affiché.)

Puis un pod d'essai qui fait ce que fera CDI : conteneur d'amorçage = l'image VDDK, `/opt` en `emptyDir`, conteneur principal `registry.suse.com/bci/bci-busybox:16.0` qui liste `/opt/vmware-vix-disklib-distrib/lib64/libvixDiskLib.so*`. Expected: pod `Completed`, la bibliothèque listée ; pod supprimé ensuite.

- [ ] **Step 5: fournisseur vCenter**

```bash
python3 - <<'EOF' | bin/harvester-forklift.py provider-apply --cluster harvlab2 --namespace default --name vmwlab
import json, os, subprocess
env = dict(os.environ, VAULT_ADDR="http://127.0.0.1:8200")
env["VAULT_TOKEN"] = subprocess.run(["sudo", "cat", "/data/vault/root-token"], capture_output=True, text=True).stdout
get = lambda f: subprocess.run(["vault", "kv", "get", f"-field={f}", "secret/infra/vmware-lab"],
                               capture_output=True, text=True, env=env).stdout
print(json.dumps({"url": "172.16.2.81", "user": get("sso_user"), "password": get("sso_password"),
                  "insecure": True, "vddk_image": "172.16.1.11:3000/jniedergang/vddk:8.0.3"}))
EOF
kubectl --kubeconfig ~/.kube/harvlab2.yaml get provider -n default vmwlab
```

Expected: `STEP_EVENT|provider|done|ready: vCenter reached, inventory loaded` ; `READY True CONNECTED True INVENTORY True`. Puis un faux mot de passe sur un second fournisseur `vmwlab-bad` : `STEP_EVENT|provider|error|` avec la raison de Forklift ; `provider-delete --name vmwlab-bad --with-secret`.

- [ ] **Step 6: relevé de l'inventaire pour Task 7**

Par le compte `harvester-ops-inventory` (`kubectl create token ... --duration 10m`), un `kubectl port-forward -n forklift svc/forklift-inventory :8443`, puis `GET /providers/vsphere/<uid>/vms?detail=1`, `/networks`, `/datastores` avec `Authorization: Bearer`. Si le service refuse le jeton (403), noter la vérification exacte qu'il fait (journal de `forklift-controller`, conteneur `inventory`) et élargir `inventory_rbac()` au strict nécessaire, test mis à jour.

Enregistrer la réponse `vms` dans `tests/api/fixtures/forklift_inventory_vms_175.json`, après avoir retiré tout ce qui ne sert pas au test (UUID de BIOS, chemins de fichiers `.vmx`) et vérifié qu'elle ne contient aucun secret. Expected: les trois VMs `vmwlab-src-1..3`, `changeTrackingEnabled: true`.

- [ ] **Step 7: consigner ce que le réel a appris**

Mémoire projet (`forklift-migrations-vmware.md` ou une note `forklift-b1-reel.md` liée depuis `MEMORY.md`) : paire chart/images retenue, redémarrage éventuel de RKE2 par `containerd-registry`, droits réels du service d'inventaire, forme réelle de l'inventaire, durées.

---

### Task 7 : inventaire (`inventory`)

**Files:**
- Modify: `bin/lib/hv_forklift.py` (fonction `inventory_rows`)
- Modify: `bin/harvester-forklift.py` (commande `inventory`)
- Test: `tests/api/test_forklift_175.py`, `tests/api/test_forklift_cli_175.py` (ajouts), `tests/api/fixtures/forklift_inventory_vms_175.json` (Task 6)

**Interfaces:**
- Consumes: la fixture réelle (Task 6), `Kube.port_forward` (Task 4).
- Produces: `hf.inventory_rows(kind, items) -> list[dict]` ; pour `vms` : `{"id", "name", "path", "power", "cbt": bool, "cpus", "memory_mib", "guest", "disks": [{"datastore", "capacity"}], "networks": [id...], "concerns": [{"category", "label"}]}` ; pour `networks`/`datastores` : `{"id", "name", "path"}` (+ `"capacity", "free"` pour un datastore). `cmd_inventory(args, kube=None, fetch=None) -> int`.

- [ ] **Step 1: écrire les tests (ajouts)**

Dans `tests/api/test_forklift_175.py` :

```python
FIX = Path(__file__).resolve().parent / "fixtures" / "forklift_inventory_vms_175.json"


def test_the_real_inventory_gives_what_a_wave_needs():
    rows = {r["name"]: r for r in hf.inventory_rows("vms", json.loads(FIX.read_text()))}
    assert {"vmwlab-src-1", "vmwlab-src-2", "vmwlab-src-3"} <= set(rows)
    src1 = rows["vmwlab-src-1"]
    assert src1["cbt"] is True and src1["id"].startswith("vm-") and src1["power"] == "poweredOn"
    assert src1["cpus"] == 1 and src1["memory_mib"] == 1024 and src1["disks"] and src1["networks"]
    assert rows["vmwlab-src-3"]["memory_mib"] == 4096


def test_networks_and_datastores_are_named_rows():
    rows = hf.inventory_rows("datastores", [{"id": "datastore-11", "name": "datastore1", "path": "/vmwlab-dc/datastore/datastore1",
                                             "capacity": 506806140928, "free": 400000000000, "extra": 1}])
    assert rows == [{"id": "datastore-11", "name": "datastore1", "path": "/vmwlab-dc/datastore/datastore1",
                     "capacity": 506806140928, "free": 400000000000}]
    assert hf.inventory_rows("networks", [{"id": "network-12", "name": "VM Network", "path": "/x"}]) == [
        {"id": "network-12", "name": "VM Network", "path": "/x"}]
    with pytest.raises(ValueError, match="kind"):
        hf.inventory_rows("hosts", [])
```

Dans `tests/api/test_forklift_cli_175.py` :

```python
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
    assert seen["token"] == "tok-123" and ("run", "create", "token") in k.calls
    assert json.loads(capsys.readouterr().out) == [{"id": "network-12", "name": "VM Network",
                                                    "path": "/vmwlab-dc/network/VM Network"}]
```

- [ ] **Step 2: les lancer, ils échouent**

Run: `python3 -m pytest tests/api/test_forklift_175.py tests/api/test_forklift_cli_175.py -q`
Expected: FAIL, `AttributeError: ... 'inventory_rows'` et `'cmd_inventory'`

- [ ] **Step 3: `inventory_rows` dans `hv_forklift.py`**

Les noms de champs ci-dessous sont ceux du modèle vSphere de Forklift ; la fixture réelle de Task 6 fait foi : si un champ y porte un autre nom, suivre la fixture.

```python
# --- inventaire (service forklift-inventory) --------------------------------

def inventory_rows(kind, items):
    """Lignes utiles d'un inventaire vSphere de Forklift (détail=1)."""
    items = items or []
    if kind == "vms":
        out = []
        for v in items:
            out.append({
                "id": v.get("id"), "name": v.get("name"), "path": v.get("path"),
                "power": v.get("powerState"), "cbt": bool(v.get("changeTrackingEnabled")),
                "cpus": v.get("cpuCount"), "memory_mib": v.get("memoryMB"),
                "guest": v.get("guestName") or v.get("guestId"),
                "disks": [{"datastore": ((d.get("datastore") or {}).get("id")), "capacity": d.get("capacity")}
                          for d in v.get("disks") or []],
                "networks": [n.get("id") for n in v.get("networks") or []],
                "concerns": [{"category": c.get("category"), "label": c.get("label")} for c in v.get("concerns") or []],
            })
        return out
    if kind == "networks":
        return [{"id": n.get("id"), "name": n.get("name"), "path": n.get("path")} for n in items]
    if kind == "datastores":
        return [{"id": d.get("id"), "name": d.get("name"), "path": d.get("path"),
                 "capacity": d.get("capacity"), "free": d.get("free")} for d in items]
    raise ValueError(f"inventory kind: vms, networks or datastores ({kind!r})")
```

- [ ] **Step 4: `cmd_inventory` dans `bin/harvester-forklift.py`**

```python
def fetch_json(url, token):
    """GET du service d'inventaire par le relais local : son certificat est
    celui du cluster (cert-manager), le canal est celui de kubectl."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        return json.load(r)


def cmd_inventory(args, kube=None, fetch=None):
    kube = kube or kube_from(args)
    ns, name = hf.check_name(args.namespace, "namespace"), hf.check_name(args.name, "provider")
    p = get_opt(kube, hf.K_PROVIDER, ns, name)
    if p is None:
        raise ValueError(f"no provider {ns}/{name}")
    uid = (p.get("metadata") or {}).get("uid")
    token = kube.run("create", "token", hf.INVENTORY_SA, "-n", hf.NS, "--duration", "10m").strip()
    with kube.port_forward(hf.NS, f"svc/{hf.INVENTORY_SVC}", hf.INVENTORY_PORT) as port:
        data = (fetch or fetch_json)(f"https://127.0.0.1:{port}/providers/vsphere/{uid}/{args.kind}?detail=1", token)
    print(json.dumps(hf.inventory_rows(args.kind, data)))
    return EXIT_OK
```

Et dans `build_parser`, avant `return ap, sub` :

```python
    sp = sub.add_parser("inventory", help="VMs, networks or datastores of a vCenter provider, as Forklift sees them")
    cluster_args(sp)
    sp.add_argument("--namespace", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--kind", choices=("vms", "networks", "datastores"), required=True)
    sp.set_defaults(fn=cmd_inventory)
```

- [ ] **Step 5: les tests passent, puis en réel**

Run: `python3 -m pytest tests/api/ -q`
Expected: tout vert.

Run (banc allumé) : `bin/harvester-forklift.py inventory --cluster harvlab2 --namespace default --name vmwlab --kind vms`
Expected: les trois VMs sources, `"cbt": true`.

- [ ] **Step 6: commit**

```bash
git add bin/lib/hv_forklift.py bin/harvester-forklift.py tests/api/test_forklift_175.py \
        tests/api/test_forklift_cli_175.py tests/api/fixtures/forklift_inventory_vms_175.json
git commit -m "feat(1.75.0): read a vCenter inventory as Forklift sees it

The inventory service checks a bearer token and the apiserver proxy drops
that header, so harvester-forklift asks a ten-minute token for a service
account that can only read providers and goes through a port-forward. The
rows carry what a warm wave needs: CBT, size, disks, networks and
Forklift's concerns; the test reads a real inventory of the lab."
```

---

### Task 8 : doc, parité, version, release 1.75.0

**Files:**
- Modify: `docs/en/capabilities.md`, `docs/fr/capabilites.md` (nouvelle section), `CHANGELOG.md`, `VERSION`, `tools/parity/data.py:231`, docs de parité régénérées

- [ ] **Step 1: doc EN et FR**

Nouvelle section « VMware migrations with Forklift: installation, VDDK image, vCenter provider » dans `docs/en/capabilities.md` (et « Migrations VMware par Forklift : installation, image VDDK, fournisseur vCenter » dans `docs/fr/capabilites.md`), à la suite de la section des imports de VM, disant :

- Harvester 1.9 ne livre pas Forklift ; `harvester-forklift install` pose cert-manager (manifeste du paquet Cluster API de la console), l'add-on expérimental forklift-operator (chart 1.9.0, images `v1.8.2` par défaut, `--image-tag` pour un autre) et le ForkliftController ; images tirées de `registry.rancher.com/harvester` (miroir à prévoir en airgap) ;
- `vddk-image` construit l'image d'amorçage depuis l'archive VDDK de VMware (jamais fournie avec la console) et la pousse dans le registre de l'exploitant, identifiants sur l'entrée standard ; le cluster doit pouvoir tirer de ce registre (réglage `containerd-registry`, section Advanced) ;
- `provider-apply` (JSON sur l'entrée standard : `url`, `user`, `password`, `insecure` ou `cacert`, `vddk_image`), `provider-delete` (refusé tant qu'un plan l'utilise), `inventory` (`vms`, `networks`, `datastores`) ;
- les vagues à chaud, la bascule et le retour arrière arrivent avec l'étape suivante (B2), l'écran avec C.

Relire les deux textes : ni tiret cadratin, ni flèche Unicode.

- [ ] **Step 2: parité**

`tools/parity/data.py` ligne 231 :

```python
r("part", "1.75", "Migration par forklift-operator", "Migration through forklift-operator", "installé et relié au vCenter par la console (outil harvester-forklift) ; vagues à chaud et écran à venir", "installed and connected to the vCenter by the console (harvester-forklift tool); warm waves and screen to come")
```

Run: `python3 tools/parity/gen.py --docs`
Expected: `docs/en/harvester-parity.md` et `docs/fr/parite-harvester.md` régénérés.

- [ ] **Step 3: version et CHANGELOG**

`VERSION` : `1.75.0`. En tête de `CHANGELOG.md` :

```markdown
## [1.75.0] - 2026-09-28 - Forklift installed and connected to a vCenter

### Added
- `harvester-forklift`, a new tool: `install` puts cert-manager, the experimental forklift-operator add-on (images named, v1.8.2 by default) and the ForkliftController on a Harvester cluster, in that order; `status` reads it all back.
- `harvester-forklift vddk-image` builds Forklift's VDDK init image from VMware's archive and pushes it to the operator's registry through the registry API, without podman or buildah; the VDDK never ships with the console.
- `harvester-forklift provider-apply`, `provider-delete` and `inventory`: a vCenter declared as a Forklift provider (password on stdin), followed until Forklift reaches it or says why not, and its VMs, networks and datastores as Forklift sees them (CBT, size, disks, concerns).

### Internal
- `bin/lib/hv_forklift.py`, `bin/lib/oci_push.py`, `Kube.port_forward()`.

### Tests
- `test_forklift_175.py`, `test_oci_push_175.py` (two simulated registries, Bearer auth, redirected blobs), `test_forklift_cli_175.py`; checked for real on the harvlab2 bench against the nested vCenter of vmwlab.
```

- [ ] **Step 4: suite complète, commit, push Gitea**

Run: `python3 -m pytest tests/api/ -q`
Expected: tout vert

```bash
git add VERSION CHANGELOG.md docs/en/capabilities.md docs/fr/capabilites.md tools/parity/data.py \
        docs/en/harvester-parity.md docs/fr/parite-harvester.md
git commit -m "feat(1.75.0): Forklift installed and connected to a vCenter"
git tag v1.75.0
git push gitea HEAD:main --tags
```

- [ ] **Step 5: paquet, essai du paquet, publication**

Construire le livrable avec le paquet Cluster API courant depuis un worktree (script de build de la session, `CAPI_BUNDLE=.../dist/capi-bundle-20260926-002651-caaph.tar.gz ./package.sh`), l'installer sur node1 dans un conteneur d'essai (script `pkgtest` de la session, port suivant libre), vérifier par l'unité systemd, et contrôler que `harvester-forklift --help` et `harvester-forklift status --cluster harvlab2` répondent dans le conteneur installé. Puis `git push github HEAD:refs/heads/master` et le tag, `gh release create v1.75.0` avec les notes tirées du CHANGELOG, et republier le tableau de parité (artefact de parité existant).

- [ ] **Step 6: éteindre les bancs**

```bash
tests/bench/vmware/vmwlab.sh stop
HARVLAB_NAME=harvlab2 HARVLAB_NODES=1 tests/bench/harvlab/harvlab.sh stop
```

Puis node2 par `sudo systemctl poweroff` en SSH, `PowerState: Off` vérifié par l'iLO. Mettre à jour la mémoire projet (ligne produit de `MEMORY.md` à v1.75.0).

---

## Ensuite : B2

Plan séparé, écrit après B1 avec ce que le banc aura montré : correspondances réseau et stockage (par nom ou par identifiant d'inventaire), plan à chaud (`warm: true`) par vague, suivi des copies incrémentales, bascule (`cutover`) à date ou immédiate, retour arrière vers la source, puis migrations réelles d'une VM Linux et de la VM Windows du banc. Puis C (l'écran).
