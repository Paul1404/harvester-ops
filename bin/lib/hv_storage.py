"""harvester-ops : les gestes de Harvester sur les volumes et les images (v1.63.0).

Relevé dans le serveur de Harvester v1.9.0 (pkg/api/volume, pkg/api/image,
pkg/image) et harvester-ui-extension v1.9.0 : ce que font ses actions en
objets Kubernetes, refait ici pour kubectl (scratchpad du chantier :
parity/harvester-storage-formats-1.9.md).

Fonctions pures ; bin/harvester-resources.py `volume` et `image` les appliquent.
"""

import copy
import json
import re

K_PVC = "persistentvolumeclaims"
K_PV = "persistentvolumes"
K_IMAGE = "virtualmachineimages.harvesterhci.io"
K_SC = "storageclasses.storage.k8s.io"
K_SNAP = "volumesnapshots.snapshot.storage.k8s.io"
K_DV = "datavolumes.cdi.kubevirt.io"
K_DOWNLOADER = "virtualmachineimagedownloaders.harvesterhci.io"
DESC = "field.cattle.io/description"
IMAGE_ID = "harvesterhci.io/imageId"
SC_ANN = "harvesterhci.io/storageClassName"
LH = "driver.longhorn.io"
INTERNAL_SC = ("vmstate-persistence", "longhorn-static")
CSI_DEFAULT = {LH: {"volumeSnapshotClassName": "longhorn-snapshot", "backupVolumeSnapshotClassName": "longhorn"}}
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
SHA512_RE = re.compile(r"^[0-9a-fA-F]{128}$")
# clés d'image que Harvester pose ou fige : jamais retirées par « modifier »
IMAGE_SYSTEM_KEYS = re.compile(r"^([a-z0-9.-]*\.)?(harvesterhci\.io|kubernetes\.io|cattle\.io|longhorn\.io)/")


def check_name(name, what="name"):
    if not NAME_RE.match(name or "") or len(name) > 253:
        raise ValueError(f"{what}: lower-case letters, digits, dots and dashes")
    return name


def _meta(o):
    return (o or {}).get("metadata") or {}


def _spec(o):
    return (o or {}).get("spec") or {}


def is_longhorn_v1(sc):
    """Une classe Longhorn v1 (le backend « backingimage » des images)."""
    return bool(sc) and sc.get("provisioner") == LH and (sc.get("parameters") or {}).get("dataEngine", "v1") != "v2"


def csi_snapshot_class(setting_value, provisioner):
    """La classe d'instantané d'un fournisseur, lue dans le réglage
    csi-driver-config de Harvester (JSON), comme son action « snapshot »."""
    try:
        conf = json.loads(setting_value) if setting_value else CSI_DEFAULT
    except ValueError:
        conf = CSI_DEFAULT
    return ((conf or {}).get(provisioner) or {}).get("volumeSnapshotClassName")


# ---------------------------------------------------------------------------
# Volumes
# ---------------------------------------------------------------------------

def clone_pvc(src, new_name, with_data=True):
    """« Cloner » : avec les données, un PVC dont dataSource est l'original
    (Longhorn copie) ; sans, un volume neuf aux mêmes réglages (et, s'il
    venait d'une image, depuis la même image)."""
    check_name(new_name, "new volume name")
    m, s = _meta(src), _spec(src)
    ann = {k: v for k, v in (m.get("annotations") or {}).items() if k in (IMAGE_ID, DESC)}
    body = {k: copy.deepcopy(s[k]) for k in ("accessModes", "storageClassName", "volumeMode") if k in s}
    body["resources"] = {"requests": {"storage": ((s.get("resources") or {}).get("requests") or {}).get("storage")}}
    if with_data:
        body["dataSource"] = {"kind": "PersistentVolumeClaim", "name": m.get("name")}
    out = {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
           "metadata": {"name": new_name, "namespace": m.get("namespace")}, "spec": body}
    if ann:
        out["metadata"]["annotations"] = ann
    return out


def volume_snapshot(pvc, name, snapshot_class, provisioner):
    """L'instantané d'un volume, comme l'action « snapshot » de Harvester."""
    check_name(name, "snapshot name")
    if not snapshot_class:
        raise ValueError(f"no snapshot class for the provisioner {provisioner} (csi-driver-config)")
    m, s = _meta(pvc), _spec(pvc)
    ann = {SC_ANN: s.get("storageClassName") or "", "harvesterhci.io/storageProvisioner": provisioner or ""}
    if (m.get("annotations") or {}).get(IMAGE_ID):
        ann[IMAGE_ID] = m["annotations"][IMAGE_ID]
    return {"apiVersion": "snapshot.storage.k8s.io/v1", "kind": "VolumeSnapshot",
            "metadata": {"name": name, "namespace": m.get("namespace"), "annotations": ann,
                         "ownerReferences": [{"apiVersion": "v1", "kind": "PersistentVolumeClaim",
                                              "name": m.get("name"), "uid": m.get("uid")}]},
            "spec": {"source": {"persistentVolumeClaimName": m.get("name")},
                     "volumeSnapshotClassName": snapshot_class}}


def data_volume(src, target_name, target_sc):
    """« Migration de données » : une COPIE du volume dans une autre classe,
    par un DataVolume CDI (le volume d'origine reste)."""
    check_name(target_name, "target volume name")
    check_name(target_sc, "target storage class")
    if target_sc in INTERNAL_SC:
        raise ValueError(f"{target_sc} is an internal storage class")
    m, s = _meta(src), _spec(src)
    return {"apiVersion": "cdi.kubevirt.io/v1beta1", "kind": "DataVolume",
            "metadata": {"name": target_name, "namespace": m.get("namespace")},
            "spec": {"source": {"pvc": {"name": m.get("name"), "namespace": m.get("namespace")}},
                     "storage": {"storageClassName": target_sc,
                                 "resources": {"requests": copy.deepcopy((s.get("resources") or {}).get("requests") or {})}}}}


def resizing(pvc):
    return any(c.get("type") == "Resizing" and str(c.get("status")) == "True"
               for c in ((pvc or {}).get("status") or {}).get("conditions") or [])


def recreated_pvc(pvc):
    """Le PVC recréé par « annuler l'agrandissement » : même volume (PV),
    taille ramenée à la capacité réelle."""
    out = copy.deepcopy(pvc)
    meta = out["metadata"]
    for k in ("resourceVersion", "uid", "creationTimestamp", "managedFields", "finalizers",
              "deletionTimestamp", "deletionGracePeriodSeconds"):
        meta.pop(k, None)
    ann = meta.get("annotations") or {}
    for k in ("pv.kubernetes.io/bind-completed", "pv.kubernetes.io/bound-by-controller",
              "volume.kubernetes.io/storage-resizer", "kubectl.kubernetes.io/last-applied-configuration"):
        ann.pop(k, None)
    cap = ((pvc.get("status") or {}).get("capacity") or {}).get("storage")
    if not cap:
        raise ValueError("the volume has no known capacity")
    out["spec"].setdefault("resources", {}).setdefault("requests", {})["storage"] = cap
    out.pop("status", None)
    return out


def used_by_vms(pvc_name, ns, vms):
    """Les VMs qui citent ce volume (volumes ou modèles de volume)."""
    out = []
    for vm in vms:
        if _meta(vm).get("namespace") != ns:
            continue
        ts = (_spec(vm).get("template") or {}).get("spec") or {}
        claims = {((v.get("persistentVolumeClaim") or {}).get("claimName")) for v in ts.get("volumes") or []}
        tmpl = (_meta(vm).get("annotations") or {}).get("harvesterhci.io/volumeClaimTemplates") or "[]"
        try:
            claims |= {(t.get("metadata") or {}).get("name") for t in json.loads(tmpl)}
        except ValueError:
            pass
        if pvc_name in claims:
            out.append(_meta(vm).get("name"))
    return sorted(out)


def description_patch(obj, text):
    return {"metadata": {"annotations": {DESC: (text or "").strip()[:1000] or None}}}


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def pv_encrypted(pv):
    return str(((_spec(pv).get("csi") or {}).get("volumeAttributes") or {}).get("encrypted")) == "true"


def export_image(pvc, display_name, target_ns, target_sc, sc_obj, pvc_sc_obj, encrypted=False):
    """« Exporter en image » : backend backingimage vers une classe Longhorn v1,
    cdi sinon ; un volume hors Longhorn v1 ne va pas vers Longhorn v1."""
    display_name = (display_name or "").strip()
    if not display_name or len(display_name) > 63:
        raise ValueError("image name: 63 characters at most")
    if encrypted:
        raise ValueError("an encrypted volume cannot be exported")
    if not is_longhorn_v1(pvc_sc_obj) and is_longhorn_v1(sc_obj):
        raise ValueError("a volume outside Longhorn v1 cannot be exported to a Longhorn v1 class")
    m = _meta(pvc)
    return {"apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineImage",
            "metadata": {"generateName": "image-", "namespace": target_ns, "annotations": {SC_ANN: target_sc}},
            "spec": {"backend": "backingimage" if is_longhorn_v1(sc_obj) else "cdi", "displayName": display_name,
                     "sourceType": "export-from-volume", "pvcName": m.get("name"), "pvcNamespace": m.get("namespace"),
                     "targetStorageClassName": target_sc, "retry": 3}}


def image_state(img):
    """ready, failed ou importing. Vu sur harv1 : Harvester réessaie (spec.retry,
    3 par défaut) ; un premier échec pose status.failed=1 et la condition
    RetryLimitExceeded à False : ce n'est pas encore un échec."""
    st = (img or {}).get("status") or {}
    conds = {c.get("type"): c for c in st.get("conditions") or []}
    imp = conds.get("Imported") or {}
    if str(imp.get("status")) == "True":
        return "ready", None
    rle = conds.get("RetryLimitExceeded") or {}
    msg = rle.get("message") or imp.get("message") or (conds.get("Initialized") or {}).get("message")
    if str(rle.get("status")) == "True" or (st.get("failed") or 0) >= int(((img or {}).get("spec") or {}).get("retry") or 3):
        return "failed", msg or "failed"
    return "importing", (f"retrying: {msg}" if st.get("failed") and msg else None)


def image_edit_patch(img, description=None, labels=None):
    """« Modifier » une image : description et labels seulement (Harvester
    remplace labels et annotations, ses propres clés restent)."""
    m = _meta(img)
    ann = copy.deepcopy(m.get("annotations") or {})
    lab = copy.deepcopy(m.get("labels") or {})
    if description is not None:
        if description.strip():
            ann[DESC] = description.strip()[:1000]
        else:
            ann.pop(DESC, None)
    if labels is not None:
        for k in [k for k in lab if not IMAGE_SYSTEM_KEYS.match(k)]:
            lab.pop(k)
        for k, v in labels.items():
            if IMAGE_SYSTEM_KEYS.match(k):
                raise ValueError(f"label {k}: kept by Harvester")
            if not re.match(r"^([a-z0-9]([-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$", k):
                raise ValueError(f"label {k}: invalid key")
            if len(str(v)) > 63 or (v and not re.match(r"^[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$", str(v))):
                raise ValueError(f"label {k}: invalid value")
            lab[k] = str(v)
    return [{"op": "replace", "path": "/metadata/labels", "value": lab},
            {"op": "replace", "path": "/metadata/annotations", "value": ann}]


def clone_image(img, display_name):
    """« Cloner » : seulement une image téléchargée ; nouvelle image, même
    adresse et même classe (un nouveau téléchargement)."""
    s, m = _spec(img), _meta(img)
    if s.get("sourceType") != "download":
        raise ValueError("only an image downloaded from a URL can be cloned")
    display_name = (display_name or "").strip()
    if not display_name or len(display_name) > 63:
        raise ValueError("image name: 63 characters at most")
    body = {k: s[k] for k in ("sourceType", "url", "checksum", "backend", "targetStorageClassName", "retry") if s.get(k)}
    body["displayName"] = display_name
    ann = {k: v for k, v in (m.get("annotations") or {}).items() if k in (SC_ANN, DESC)}
    lab = {k: v for k, v in (m.get("labels") or {}).items() if k in ("harvesterhci.io/os-type", "harvesterhci.io/image-type")}
    out = {"apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineImage",
           "metadata": {"generateName": "image-", "namespace": m.get("namespace")}, "spec": body}
    if ann:
        out["metadata"]["annotations"] = ann
    if lab:
        out["metadata"]["labels"] = lab
    return out


def crypto_image(img, operation, display_name, target_sc, target_sc_obj):
    """« Chiffrer » / « déchiffrer » : une nouvelle image (clone) vers une
    classe chiffrée, ou d'une image chiffrée vers une classe claire."""
    if operation not in ("encrypt", "decrypt"):
        raise ValueError("operation: encrypt or decrypt")
    if _spec(img).get("backend", "backingimage") != "backingimage":
        raise ValueError("only a Longhorn v1 (backing image) image can be encrypted or decrypted")
    if image_state(img)[0] != "ready":
        raise ValueError("the source image is not ready")
    params = _spec(img).get("storageClassParameters") or {}
    is_enc = str(params.get("encrypted")) == "true"
    if operation == "encrypt" and is_enc:
        raise ValueError("this image is already encrypted")
    if operation == "decrypt" and not is_enc:
        raise ValueError("this image is not encrypted")
    target_enc = str(((target_sc_obj or {}).get("parameters") or {}).get("encrypted")) == "true"
    if operation == "encrypt" and not target_enc:
        raise ValueError("choose an encrypted storage class (parameter encrypted: true)")
    if operation == "decrypt" and target_enc:
        raise ValueError("choose a storage class that is not encrypted")
    display_name = (display_name or "").strip()
    if not display_name or len(display_name) > 63:
        raise ValueError("image name: 63 characters at most")
    m = _meta(img)
    return {"apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineImage",
            "metadata": {"generateName": "image-", "namespace": m.get("namespace"), "annotations": {SC_ANN: target_sc}},
            "spec": {"displayName": display_name, "sourceType": "clone", "backend": "backingimage",
                     "targetStorageClassName": target_sc, "retry": 3,
                     "securityParameters": {"cryptoOperation": operation, "sourceImageName": m.get("name"),
                                            "sourceImageNamespace": m.get("namespace")}}}


def backing_image_of(img, sc_obj):
    """Le BackingImage Longhorn d'une image : lu dans la classe de l'image
    (paramètre backingImage), jamais fabriqué."""
    bi = ((sc_obj or {}).get("parameters") or {}).get("backingImage")
    if not bi:
        raise ValueError("this image has no Longhorn backing image (still importing, or a CDI image)")
    return bi


def download_path(bi):
    """Chemin de téléchargement par le proxy de service de l'apiserver (le
    même relais que le serveur de Harvester), fichier compressé en gzip."""
    if not re.match(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$", bi or ""):
        raise ValueError("backing image name")
    return f"/api/v1/namespaces/longhorn-system/services/http:longhorn-backend:9500/proxy/v1/backingimages/{bi}/download"


def upload_image(ns, display_name, url, storage_class, checksum=None, file_name="", description=""):
    """L'image d'un fichier envoyé par le navigateur : la console le sert par
    son guichet à jetons et le cluster le télécharge (source « download »)."""
    display_name = (display_name or "").strip()
    if not display_name or len(display_name) > 63:
        raise ValueError("image name: 63 characters at most")
    if checksum and not SHA512_RE.match(checksum.strip()):
        raise ValueError("checksum: a SHA512 (128 hexadecimal characters)")
    kind = "iso" if file_name.lower().endswith(".iso") else "raw_qcow2"
    out = {"apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineImage",
           "metadata": {"generateName": "image-", "namespace": ns,
                        "labels": {"harvesterhci.io/image-type": kind},
                        "annotations": {"harvesterhci.io/image-name": file_name[:253]}},
           "spec": {"displayName": display_name, "sourceType": "download", "url": url, "backend": "backingimage",
                    "retry": 3}}
    if storage_class:
        out["metadata"]["annotations"][SC_ANN] = storage_class
        out["spec"]["targetStorageClassName"] = storage_class
    if checksum:
        out["spec"]["checksum"] = checksum.strip().lower()
    if description:
        out["metadata"]["annotations"][DESC] = description[:1000]
    return out
