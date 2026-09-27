"""harvester-ops : les namespaces, comme le menu Namespaces de Harvester (v1.62.0).

Relevé dans harvester-ui-extension v1.9.0 : un namespace a un nom, une
description (annotation field.cattle.io/description), des labels et des
annotations ; son quota d'instantanés est la ResourceQuota de Harvester
`default-resource-quota`, champ spec.snapshotLimit.namespaceTotalSnapshotSizeQuota
(octets). Fonctions pures ; bin/harvester-resources.py `namespace` les applique.
"""

import copy
import re

DESC = "field.cattle.io/description"
QUOTA_NAME = "default-resource-quota"
K_QUOTA = "resourcequotas.harvesterhci.io"
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
LABEL_KEY = re.compile(r"^([a-z0-9]([-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$")
LABEL_VAL = re.compile(r"^([A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?)?$")
# les namespaces du système, cachés par défaut et jamais supprimés
SYSTEM = re.compile(r"^(kube-|cattle-|fleet-|cluster-fleet-|harvester-|longhorn-|rancher-|local$|cis-operator|"
                    r"kubevirt|kubeovn-|forklift|tigera|calico|kube-ovn|capi-|caphv-|caaph|rke2-|sriov|nvidia|"
                    r"p-[a-z0-9]{5}$|c-[a-z0-9]{5}$|u-[a-z0-9]+$|user-[a-z0-9]+$)|-system$")
# montrés mais jamais supprimés : Kubernetes refuse de supprimer default
PROTECTED = ("default",)
# clés posées par Kubernetes, Rancher, Harvester, KubeVirt, Longhorn : jamais
# montrées ni touchées. Vu sur harv1 : kube-ovn annote chaque namespace
# (ovn.kubernetes.io/logical_switch, cidr...) ; les retirer casserait son réseau.
MANAGED = re.compile(r"^([a-z0-9.-]*\.)?(kubernetes\.io|k8s\.io|cattle\.io|harvesterhci\.io|kubevirt\.io|"
                     r"longhorn\.io)/")


def check_name(name):
    if not NAME_RE.match(name or ""):
        raise ValueError("namespace name: lower-case letters, digits and dashes, 63 at most")
    return name


def is_system(name, obj=None):
    labels = ((obj or {}).get("metadata") or {}).get("labels") or {}
    return bool(SYSTEM.match(name or "")) or labels.get("harvesterhci.io/system") == "true"


def _check_kv(kv, what, values=True):
    out = {}
    for k, v in (kv or {}).items():
        k = str(k).strip()
        if not LABEL_KEY.match(k) or len(k) > 316:
            raise ValueError(f"{what} {k!r}: invalid key")
        if MANAGED.match(k):
            raise ValueError(f"{what} {k!r}: managed by Kubernetes or Rancher")
        v = "" if v is None else str(v)
        if values and (len(v) > 63 or not LABEL_VAL.match(v)):
            raise ValueError(f"{what} {k!r}: value of 63 letters, digits, dash, dot or underscore at most")
        out[k] = v
    return out


def manifest(name, description="", labels=None):
    check_name(name)
    meta = {"name": name}
    if description:
        meta["annotations"] = {DESC: str(description)[:1000]}
    if labels:
        meta["labels"] = _check_kv(labels, "label")
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": meta}


def _visible(d):
    return {k: v for k, v in (d or {}).items() if not MANAGED.match(k) and k != DESC}


def update_patch(ns_obj, description=None, labels=None, annotations=None):
    """Merge patch : description, labels et annotations visibles ; ceux qu'on
    retire passent à null, ceux de Kubernetes et Rancher ne bougent jamais."""
    meta = (ns_obj or {}).get("metadata") or {}
    patch = {"metadata": {}}
    ann = {}
    if description is not None:
        ann[DESC] = str(description)[:1000] or None
    if annotations is not None:
        new = _check_kv(annotations, "annotation", values=False)
        ann.update(new)
        for k in _visible(meta.get("annotations")):
            if k not in new:
                ann[k] = None
    if ann:
        patch["metadata"]["annotations"] = ann
    if labels is not None:
        new = _check_kv(labels, "label")
        for k in _visible(meta.get("labels")):
            if k not in new:
                new[k] = None
        patch["metadata"]["labels"] = new
    if not patch["metadata"]:
        raise ValueError("nothing to change")
    return patch


def quota_object(existing, ns, size_bytes):
    """La ResourceQuota de Harvester avec le quota d'instantanés du namespace
    (0 = retiré ; l'objet reste, il porte aussi les quotas des VMs)."""
    if existing is None and not size_bytes:
        return None
    out = copy.deepcopy(existing) if existing else {
        "apiVersion": "harvesterhci.io/v1beta1", "kind": "ResourceQuota",
        "metadata": {"name": QUOTA_NAME, "namespace": ns}, "spec": {"snapshotLimit": {}}}
    lim = out.setdefault("spec", {}).setdefault("snapshotLimit", {})
    if size_bytes:
        lim["namespaceTotalSnapshotSizeQuota"] = int(size_bytes)
    else:
        lim.pop("namespaceTotalSnapshotSizeQuota", None)
    out.pop("status", None)
    return out


def protected(name, obj=None):
    return name in PROTECTED or is_system(name, obj)


def delete_check(name, obj, vms=(), pvcs=()):
    """Harvester supprime sans demander ; la console dit d'abord ce qui part
    avec, et refuse un namespace du système."""
    if protected(name, obj):
        raise ValueError(f"{name} is a system namespace: the cluster needs it")
    return {"vms": sorted((v.get("metadata") or {}).get("name") for v in vms),
            "volumes": sorted((p.get("metadata") or {}).get("name") for p in pvcs)}
