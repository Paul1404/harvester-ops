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

import base64
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
    messages d'erreur ne citent jamais une credential (user ou password)."""
    name = check_name(name, "provider")
    ns = check_name(ns, "namespace")
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
            "metadata": {"name": secret_name(name), "namespace": ns,
                         "labels": {"createdForProviderType": "vsphere", "createdForResourceType": "providers",
                                    L_MANAGED: "true"}},
            "stringData": data}


def provider_manifest(ns, name, spec):
    name = check_name(name, "provider")
    ns = check_name(ns, "namespace")
    settings = {"sdkEndpoint": "vcenter"}
    if spec.get("vddk_image"):
        settings["vddkInitImage"] = check_image(spec["vddk_image"], "VDDK image")
    return {"apiVersion": API, "kind": "Provider",
            "metadata": {"name": name, "namespace": ns,
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
    # vu en réel : juste après une modification, les conditions sont encore
    # celles de la version précédente (Ready vrai) ; rien n'est lu avant que
    # Forklift ait relu la nouvelle version
    gen = (p.get("metadata") or {}).get("generation")
    if gen is not None and (st.get("observedGeneration") or 0) < gen:
        return None, f"being checked ({st.get('phase') or 'pending'}: change not read yet)"
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


# --- ce que l'onglet de la console demande (v1.75.0) --------------------------

VDDK_CM = "harvester-ops-vddk"
CERT_MANAGER_MEMBER = "manifests/cert-manager/cert-manager.yaml"
ARCHIVE_RE = re.compile(r"^VMware-vix-disklib-(\d+\.\d+\.\d+)-\d+\.x86_64\.tar\.gz$")


def check_archive_name(name):
    """Le nom que VMware donne à l'archive VDDK ; rend sa version (8.0.3).

    `fullmatch`, pas `match` : avec `match`, le `$` de fin de motif laisse
    passer un saut de ligne final (nom collé depuis un terminal, par
    exemple), ce qui aurait accepté une archive au nom invalide."""
    m = ARCHIVE_RE.fullmatch(str(name or ""))
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
    except (ValueError, TypeError):
        return {}
    return v if isinstance(v, dict) else {}


def registry_hint(value, archive="", prov_cluster=None):
    """L'image VDDK proposée : le premier registre du réglage containerd-registry
    de Harvester (ses Configs, puis les points d'accès de ses miroirs), chemin
    harvops/vddk, étiquette = version du VDDK. Ne rend jamais d'identifiant,
    seulement s'il y en a (`auth`), dans le réglage ou, depuis Harvester 1.9,
    dans le secret que nomme le cluster `local` (`prov_cluster`)."""
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
            "auth": registry_auth(v, host) is not None or bool(registry_auth_secret(prov_cluster, host))}


def registry_auth(value, host):
    """Les identifiants que Harvester a déjà pour ce registre (Configs.<hôte>.Auth)."""
    auth = (((_registry_setting(value).get("Configs") or {}).get(host) or {}).get("Auth")) or {}
    if auth.get("Username") and auth.get("Password"):
        return {"username": str(auth["Username"]), "password": str(auth["Password"])}
    return None


PROV_CLUSTER = ("clusters.provisioning.cattle.io", "fleet-local", "local")


def registry_auth_secret(prov_cluster, host):
    """Vu en réel sur Harvester 1.9 : le réglage containerd-registry perd son
    Auth (null) ; les identifiants vont dans un Secret rke.cattle.io/auth-config
    de fleet-local, nommé par spec.rkeConfig.registries.configs.<hôte>.
    authConfigSecretName du cluster de provisionnement `local`. Rend ce nom."""
    cfg = ((((prov_cluster or {}).get("spec") or {}).get("rkeConfig") or {}).get("registries") or {}).get("configs") or {}
    return str((cfg.get(host) or {}).get("authConfigSecretName") or "")


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
