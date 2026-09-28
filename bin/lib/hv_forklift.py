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
    se lit dans le statut (...Failed) ou la condition OperationFailed."""
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


def pick_addon(addons):
    """L'add-on forklift-operator à suivre, et s'il est celui de Harvester.
    Harvester 1.9.1 devrait livrer le sien : un add-on de ce nom SANS
    l'étiquette de la console n'est pas à elle, elle l'active sans jamais
    réécrire son chart ni ses valeurs. Sinon, celui qu'elle a déclaré."""
    mine = theirs = None
    for a in addons or []:
        m = a.get("metadata") or {}
        if m.get("name") != ADDON[1]:
            continue
        if (m.get("labels") or {}).get(L_MANAGED) == "true":
            mine = a
        else:
            theirs = a
    return (theirs, True) if theirs is not None else (mine, False)


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
