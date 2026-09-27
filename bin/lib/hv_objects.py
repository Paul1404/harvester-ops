"""harvester-ops : créer et supprimer les objets des sections (v1.59.0).

Ce que l'interface de Harvester permet dans chaque menu, pour la console :
images (par URL), classes de stockage (créer, par défaut, supprimer), clés
SSH, secrets, réseaux des VMs, volumes (créer, agrandir, supprimer) et la
configuration d'un add-on. Fonctions pures : une demande de formulaire est
contrôlée et devient l'objet que Harvester attend (formes relevées sur
harv1, Harvester v1.9.0) ; bin/harvester-resources.py les applique.
"""

import json
import re

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
KEY_RE = re.compile(r"^[-._a-zA-Z0-9]{1,253}$")
SIZE_RE = re.compile(r"^([1-9][0-9]*)(Mi|Gi|Ti)$")
SSH_KEY_RE = re.compile(r"^(ssh-(rsa|ed25519|dss)|ecdsa-sha2-nistp(256|384|521)|sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com)"
                        r"\s+[A-Za-z0-9+/]+={0,3}(\s+.*)?$")
KINDS = ("image", "storageclass", "sshkey", "secret", "network", "volume")
K = {"image": "virtualmachineimages.harvesterhci.io", "storageclass": "storageclasses.storage.k8s.io",
     "sshkey": "keypairs.harvesterhci.io", "secret": "secrets",
     "network": "network-attachment-definitions.k8s.cni.cncf.io", "volume": "persistentvolumeclaims"}
NAMESPACED = {"image", "sshkey", "secret", "network", "volume"}


def _name(v, what="name"):
    if not isinstance(v, str) or not NAME_RE.match(v):
        raise ValueError(f"{what}: lower-case letters, digits and dashes, 63 characters at most")
    return v


def _size(v):
    v = str(v or "").strip()
    if not SIZE_RE.match(v):
        raise ValueError("size: a whole number with Mi, Gi or Ti (e.g. 20Gi)")
    return v


def size_bytes(v):
    m = SIZE_RE.match(str(v or "").strip())
    if not m:
        return None
    return int(m.group(1)) * {"Mi": 2**20, "Gi": 2**30, "Ti": 2**40}[m.group(2)]


def _bool(v, default=False):
    if v is None or v == "":
        return default
    return str(v).lower() in ("1", "true", "yes", "on")


def image_manifest(spec, default_class=None):
    """Une image téléchargée depuis une URL (Harvester la récupère lui-même)."""
    ns = _name(spec.get("namespace") or "default", "namespace")
    url = str(spec.get("url") or "").strip()
    if not re.match(r"^https?://\S+$", url):
        raise ValueError("url: an http:// or https:// address the cluster can reach")
    display = str(spec.get("display_name") or url.rsplit("/", 1)[-1] or "image").strip()[:253]
    sc = str(spec.get("storage_class") or default_class or "").strip()
    meta = {"generateName": "image-", "namespace": ns}
    body = {"displayName": display, "sourceType": "download", "url": url, "backend": "backingimage",
            "retry": 3}
    if sc:
        _name(sc, "storage class")
        meta["annotations"] = {"harvesterhci.io/storageClassName": sc}
        body["targetStorageClassName"] = sc
    if spec.get("description"):
        meta.setdefault("annotations", {})["field.cattle.io/description"] = str(spec["description"])[:500]
    # v1.63.0 : la somme SHA512 de Harvester (vérifiée par Longhorn)
    checksum = str(spec.get("checksum") or "").strip()
    if checksum:
        if not re.match(r"^[0-9a-fA-F]{128}$", checksum):
            raise ValueError("checksum: a SHA512 (128 hexadecimal characters)")
        body["checksum"] = checksum.lower()
    return {"apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineImage",
            "metadata": meta, "spec": body}


def storageclass_manifest(spec):
    name = _name(spec.get("name"))
    try:
        replicas = int(spec.get("replicas") or 3)
        stale = int(spec.get("stale_timeout") or 30)
    except (TypeError, ValueError):
        raise ValueError("replicas and stale timeout are whole numbers") from None
    if not 1 <= replicas <= 3:
        raise ValueError("replicas: 1 to 3")
    if not 1 <= stale <= 10080:
        raise ValueError("stale replica timeout: 1 to 10080 minutes")
    locality = spec.get("data_locality") or "disabled"
    if locality not in ("disabled", "best-effort", "strict-local"):
        raise ValueError("data locality: disabled, best-effort or strict-local")
    if locality == "strict-local" and replicas != 1:
        raise ValueError("strict-local keeps the only replica on the VM's node: replicas must be 1")
    reclaim = spec.get("reclaim") or "Delete"
    if reclaim not in ("Delete", "Retain"):
        raise ValueError("reclaim policy: Delete or Retain")
    binding = spec.get("binding") or "Immediate"
    if binding not in ("Immediate", "WaitForFirstConsumer"):
        raise ValueError("binding: Immediate or WaitForFirstConsumer")
    params = {"numberOfReplicas": str(replicas), "staleReplicaTimeout": str(stale),
              "migratable": "true" if _bool(spec.get("migratable"), True) else "false",
              "dataEngine": "v1", "encrypted": "false"}
    for key, field in (("diskSelector", "disk_selector"), ("nodeSelector", "node_selector")):
        tags = [t.strip() for t in str(spec.get(field) or "").split(",") if t.strip()]
        for t in tags:
            if not KEY_RE.match(t):
                raise ValueError(f"{field.replace('_', ' ')}: tags are letters, digits, dot, dash, underscore")
        if tags:
            params[key] = ",".join(tags)
    if locality != "disabled":
        params["dataLocality"] = locality
    return {"apiVersion": "storage.k8s.io/v1", "kind": "StorageClass",
            "metadata": {"name": name}, "provisioner": "driver.longhorn.io",
            "parameters": params, "reclaimPolicy": reclaim, "volumeBindingMode": binding,
            "allowVolumeExpansion": _bool(spec.get("expansion"), True)}


def sshkey_manifest(spec):
    ns = _name(spec.get("namespace") or "default", "namespace")
    key = " ".join(str(spec.get("public_key") or "").split())
    if not SSH_KEY_RE.match(key):
        raise ValueError("public key: one OpenSSH public key line (ssh-ed25519 AAAA..., ssh-rsa AAAA...)")
    return {"apiVersion": "harvesterhci.io/v1beta1", "kind": "KeyPair",
            "metadata": {"name": _name(spec.get("name")), "namespace": ns},
            "spec": {"publicKey": key}}


def secret_manifest(spec):
    ns = _name(spec.get("namespace") or "default", "namespace")
    data = spec.get("data") or {}
    if not isinstance(data, dict) or not data:
        raise ValueError("data: at least one key and its value")
    clean = {}
    for k, v in data.items():
        if not isinstance(k, str) or not KEY_RE.match(k):
            raise ValueError(f"key {k!r}: letters, digits, dot, dash, underscore")
        clean[k] = "" if v is None else str(v)
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": _name(spec.get("name")), "namespace": ns}, "stringData": clean}


def network_manifest(spec):
    """Un réseau de VMs (NetworkAttachmentDefinition), comme ceux que crée
    Harvester : un pont sur le réseau de cluster, VLAN, sans étiquette ou
    trunk (plages de VLAN, v1.65.0), avec sa route (DHCP ou manuelle)."""
    import hv_net  # noqa: E402  (même répertoire bin/lib)
    name = _name(spec.get("name"))
    ns = _name(spec.get("namespace") or "default", "namespace")
    cn = _name(spec.get("cluster_network") or "mgmt", "cluster network")
    kind = spec.get("type") or "vlan"
    if kind not in ("vlan", "untagged", "trunk"):
        raise ValueError("type: vlan, untagged or trunk")
    config = {"cniVersion": "0.3.1", "name": name, "type": "bridge", "bridge": f"{cn}-br",
              "promiscMode": True, "ipam": {}}
    labels = {"network.harvesterhci.io/clusternetwork": cn, "network.harvesterhci.io/ready": "true",
              "network.harvesterhci.io/type": {"vlan": "L2VlanNetwork", "untagged": "UntaggedNetwork",
                                               "trunk": "L2VlanTrunkNetwork"}[kind]}
    if kind == "vlan":
        try:
            vlan = int(spec.get("vlan"))
        except (TypeError, ValueError):
            raise ValueError("vlan: a number from 1 to 4094") from None
        if not 1 <= vlan <= 4094:
            raise ValueError("vlan: a number from 1 to 4094")
        config["vlan"] = vlan
        labels["network.harvesterhci.io/vlan-id"] = str(vlan)
    annotations = {}
    if kind == "trunk":
        # comme Harvester : vlan 0 et les plages ; pas de route en trunk
        config["vlan"] = 0
        config["vlanTrunk"] = hv_net.trunk_ranges(spec.get("ranges"))
    else:
        annotations["network.harvesterhci.io/route"] = hv_net.route_annotation(spec)
    if spec.get("description"):
        annotations[hv_net.DESC] = str(spec["description"])[:1000]
    meta = {"name": name, "namespace": ns, "labels": labels}
    if annotations:
        meta["annotations"] = annotations
    return {"apiVersion": "k8s.cni.cncf.io/v1", "kind": "NetworkAttachmentDefinition",
            "metadata": meta, "spec": {"config": json.dumps(config, separators=(",", ":"))}}


def volume_manifest(spec, image=None):
    """Un volume vide, ou fait depuis une image (sa classe `lh-…` est LUE sur
    l'image, jamais devinée : voir la mémoire du projet)."""
    name = _name(spec.get("name"))
    ns = _name(spec.get("namespace") or "default", "namespace")
    size = _size(spec.get("size"))
    meta = {"name": name, "namespace": ns}
    body = {"accessModes": ["ReadWriteMany"], "volumeMode": "Block",
            "resources": {"requests": {"storage": size}}}
    if image is not None:
        st = image.get("status") or {}
        sc = st.get("storageClassName")
        if not sc:
            raise ValueError("this image has no storage class yet (still importing?)")
        vsize = st.get("virtualSize")
        if vsize and size_bytes(size) < int(vsize):
            raise ValueError(f"size: at least the image's virtual size ({int(vsize) // 2**30 or 1} Gi)")
        im = image.get("metadata") or {}
        meta["annotations"] = {"harvesterhci.io/imageId": f"{im.get('namespace')}/{im.get('name')}"}
        body["storageClassName"] = sc
    elif spec.get("storage_class"):
        body["storageClassName"] = _name(spec["storage_class"], "storage class")
    return {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": meta, "spec": body}


def normalize(kind, spec, default_class=None, image=None):
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not isinstance(spec, dict):
        raise ValueError("a JSON object is expected")
    return {"image": lambda: image_manifest(spec, default_class),
            "storageclass": lambda: storageclass_manifest(spec),
            "sshkey": lambda: sshkey_manifest(spec),
            "secret": lambda: secret_manifest(spec),
            "network": lambda: network_manifest(spec),
            "volume": lambda: volume_manifest(spec, image)}[kind]()


# ---------------------------------------------------------------------------
# Qui s'en sert : ce qui empêche de supprimer
# ---------------------------------------------------------------------------

def _vm_spec(vm):
    return (((vm.get("spec") or {}).get("template") or {}).get("spec")) or {}


def vms_using(kind, ns, name, vms=(), pvcs=(), images=()):
    """Les VMs (ou volumes) qui empêchent de supprimer cet objet."""
    ref = f"{ns}/{name}" if ns else name
    users = []
    for vm in vms:
        vns = (vm.get("metadata") or {}).get("namespace")
        vname = f"{vns}/{(vm.get('metadata') or {}).get('name')}"
        spec = _vm_spec(vm)
        if kind == "network":
            for n in spec.get("networks") or []:
                target = (n.get("multus") or {}).get("networkName") or ""
                if (target if "/" in target else f"{vns}/{target}") == ref:
                    users.append(vname)
        elif kind == "secret":
            for v in spec.get("volumes") or []:
                for src in ("cloudInitNoCloud", "cloudInitConfigDrive"):
                    ci = v.get(src) or {}
                    for k in ("secretRef", "networkDataSecretRef"):
                        if vns == ns and (ci.get(k) or {}).get("name") == name:
                            users.append(vname)
        elif kind == "volume":
            for v in spec.get("volumes") or []:
                if vns == ns and (v.get("persistentVolumeClaim") or {}).get("claimName") == name:
                    users.append(vname)
    if kind == "image":
        users += [f"{(p.get('metadata') or {}).get('namespace')}/{(p.get('metadata') or {}).get('name')}"
                  for p in pvcs if ((p.get("metadata") or {}).get("annotations") or {}).get("harvesterhci.io/imageId") == ref]
    if kind == "storageclass":
        users += [f"{(p.get('metadata') or {}).get('namespace')}/{(p.get('metadata') or {}).get('name')}"
                  for p in pvcs if (p.get("spec") or {}).get("storageClassName") == name]
        users += [f"image {(i.get('metadata') or {}).get('namespace')}/{(i.get('metadata') or {}).get('name')}"
                  for i in images if (i.get("status") or {}).get("storageClassName") == name]
    return sorted(set(users))


def check_values(text):
    """La configuration d'un add-on (valuesContent) : du YAML lisible."""
    try:
        import yaml
    except ImportError:              # hôte sans PyYAML : Harvester jugera
        return str(text or "")
    try:
        parsed = yaml.safe_load(text or "")
    except yaml.YAMLError as e:
        raise ValueError(f"the configuration is not valid YAML: {str(e).splitlines()[0]}") from None
    if parsed is not None and not isinstance(parsed, dict):
        raise ValueError("the configuration must be a YAML mapping (key: value)")
    return str(text or "")
