"""harvester-ops : sauvegardes, instantanés et planifications (v1.58.0).

Le menu « Backup and Snapshots » de Harvester, rassemblé dans la fenêtre
Backups de la vue des machines virtuelles : VM Schedules, VM Backups, VM
Snapshots, Volume Snapshots. Fonctions pures : les objets, leurs relevés et
leurs contrôles ; bin/harvester-resources.py les applique.

Faits relevés sur harv1 (Harvester v1.9.0) :
- VirtualMachineBackup `spec.type` backup (vers la cible de sauvegarde) ou
  snapshot (dans le cluster) ; `status.readyToUse`, `status.error.message`,
  `status.progress` ;
- VirtualMachineRestore : `newVM`, `keepMacAddress` (nouvelle VM seulement),
  `deletionPolicy`, `haltAfterRestore` (1.9, refusé par la 1.8) ;
- ScheduleVMBackup : `cron`, `retain`, `maxFailure`, `suspend`,
  `vmbackup.source` et `vmbackup.type` ;
- VolumeSnapshot de classe `longhorn-snapshot` ; on restaure un volume par
  un PVC dont la source est l'instantané.
"""

import re

K_BACKUP = "virtualmachinebackups.harvesterhci.io"
K_RESTORE = "virtualmachinerestores.harvesterhci.io"
K_SCHEDULE = "schedulevmbackups.harvesterhci.io"
K_VOLSNAP = "volumesnapshots.snapshot.storage.k8s.io"
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
# Un cron à cinq champs ; Harvester refuse un intervalle de moins d'une heure.
CRON_RE = re.compile(r"^\S+\s+\S+\s+\S+\s+\S+\s+\S+$")


def check_name(name, what="name"):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f"{what} must be a lower-case DNS label (a-z, 0-9, -), 63 characters at most")
    return name


def check_cron(cron):
    """Cinq champs, et pas plus souvent qu'une fois par heure : Harvester
    refuse un intervalle plus court (le webhook le dit, autant le dire avant)."""
    c = " ".join(str(cron or "").split())
    if not CRON_RE.match(c):
        raise ValueError("the schedule needs five cron fields: minute hour day month weekday")
    minute = c.split()[0]
    if minute == "*" or "/" in minute or "," in minute or "-" in minute:
        raise ValueError("Harvester runs a schedule at most once an hour: give a single minute (e.g. 0 * * * *)")
    return c


# v1.68.0 : délai de gel du système de fichiers (Harvester 1.9) : avec l'agent
# invité, Harvester gèle les systèmes de fichiers de la VM le temps de la
# copie, au plus ce délai. Les choix de l'interface de Harvester, sans « 0s »
# (gel sans limite) : Harvester n'appelle jamais le dégel, qui ne tient qu'à
# ce délai.
FREEZE = ("1s", "5s", "10s", "30s", "1m", "3m", "5m")


def crd_has_spec_field(crd, field):
    """La CRD connaît-elle ce champ de spec ? La 1.8 refuse un champ inconnu
    en décodage strict (fsFreezeDeadline, haltAfterRestore)."""
    for ver in ((crd or {}).get("spec") or {}).get("versions") or []:
        props = ((((ver.get("schema") or {}).get("openAPIV3Schema") or {})
                  .get("properties") or {}).get("spec") or {}).get("properties") or {}
        if field in props:
            return True
    return False


def backup_manifest(namespace, vm, name, kind="backup", freeze=None, supports_freeze=False):
    if kind not in ("backup", "snapshot"):
        raise ValueError("type must be backup or snapshot")
    spec = {"type": kind, "source": {"apiGroup": "kubevirt.io", "kind": "VirtualMachine",
                                     "name": check_name(vm, "vm")}}
    if freeze:
        if freeze not in FREEZE:
            raise ValueError("file system freeze deadline: " + ", ".join(FREEZE))
        if supports_freeze:
            spec["fsFreezeDeadline"] = freeze
    return {"apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineBackup",
            "metadata": {"name": check_name(name), "namespace": namespace}, "spec": spec}


def restore_manifest(namespace, backup, target, new_vm, keep_mac=False, halt=False,
                     delete_policy="retain", name=None, supports_halt=True, from_snapshot=False):
    """Restaurer une sauvegarde ou un instantané : dans une NOUVELLE VM, ou en
    REMPLACEMENT de la VM d'origine (arrêtée). En remplacement, `delete`
    supprime les anciens volumes de la VM (« Delete Previous Volumes » de
    Harvester), `retain` les garde ; un instantané les garde toujours (le
    webhook refuse delete)."""
    if delete_policy not in ("retain", "delete"):
        raise ValueError("deletion policy must be retain or delete")
    if from_snapshot and delete_policy == "delete":
        raise ValueError("a snapshot restore keeps the previous volumes (Harvester refuses delete)")
    spec = {"newVM": bool(new_vm), "deletionPolicy": delete_policy,
            "target": {"apiGroup": "kubevirt.io", "kind": "VirtualMachine", "name": check_name(target, "vm")},
            "virtualMachineBackupNamespace": namespace, "virtualMachineBackupName": check_name(backup, "backup")}
    if new_vm:
        spec["keepMacAddress"] = bool(keep_mac)
    if halt and supports_halt:
        spec["haltAfterRestore"] = True
    return {"apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineRestore",
            "metadata": {"name": check_name(name or f"restore-{backup}"[:63].rstrip("-")), "namespace": namespace},
            "spec": spec}


def _counts(retain, max_failure):
    try:
        retain, max_failure = int(retain), int(max_failure)
    except (TypeError, ValueError):
        raise ValueError("retain and max failure are whole numbers") from None
    # Règles de Harvester 1.9, vues sur harv1 : le CRD borne retain à 2..250 et
    # maxFailure à 2 au moins (« should be greater than or equal to 2 ») ; le
    # webhook exige en plus maxFailure < retain (« max failure should be less
    # than retain »). Autant le dire dans le formulaire.
    if not 3 <= retain <= 250:
        raise ValueError("keep between 3 and 250 copies")
    if not 2 <= max_failure < retain:
        raise ValueError("stop after 2 failures or more, and fewer than the copies kept")
    return retain, max_failure


def schedule_patch(cron, retain, max_failure):
    """v1.68.0 : modifier une planification, comme Harvester le permet après
    création : fréquence, copies gardées, échecs tolérés (VM et type sont
    figés)."""
    retain, max_failure = _counts(retain, max_failure)
    return {"spec": {"cron": check_cron(cron), "retain": retain, "maxFailure": max_failure}}


def schedule_manifest(namespace, name, vm, cron, retain, max_failure, kind="backup"):
    if kind not in ("backup", "snapshot"):
        raise ValueError("type must be backup or snapshot")
    retain, max_failure = _counts(retain, max_failure)
    return {"apiVersion": "harvesterhci.io/v1beta1", "kind": "ScheduleVMBackup",
            "metadata": {"name": check_name(name), "namespace": namespace},
            "spec": {"cron": check_cron(cron), "retain": retain, "maxFailure": max_failure,
                     "suspend": False,
                     "vmbackup": {"type": kind, "source": {"apiGroup": "kubevirt.io",
                                                           "kind": "VirtualMachine",
                                                           "name": check_name(vm, "vm")}}}}


def volume_restore_manifest(namespace, snapshot, name, size, storage_class=None):
    """Un nouveau volume depuis un instantané de volume (ce que fait « Restore »
    dans Volume Snapshots de Harvester)."""
    spec = {"accessModes": ["ReadWriteMany"], "volumeMode": "Block",
            "resources": {"requests": {"storage": size}},
            "dataSource": {"apiGroup": "snapshot.storage.k8s.io", "kind": "VolumeSnapshot",
                           "name": check_name(snapshot, "snapshot")}}
    if storage_class:
        spec["storageClassName"] = storage_class
    return {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
            "metadata": {"name": check_name(name), "namespace": namespace}, "spec": spec}


# ---------------------------------------------------------------------------
# Relevés pour la fenêtre Backups
# ---------------------------------------------------------------------------

def _m(o):
    return (o or {}).get("metadata") or {}


def backups(items, kind):
    out = []
    for b in items:
        spec, st = b.get("spec") or {}, b.get("status") or {}
        if (spec.get("type") or "backup") != kind:
            continue
        vols = st.get("volumeBackups") or []
        size = 0
        for v in vols:
            try:
                size += int(v.get("volumeSize") or 0)
            except (TypeError, ValueError):
                pass
        labels = _m(b).get("labels") or {}
        out.append({
            "namespace": _m(b).get("namespace"), "name": _m(b).get("name"),
            "vm": (spec.get("source") or {}).get("name"),
            "ready": bool(st.get("readyToUse")), "progress": st.get("progress"),
            "error": ((st.get("error") or {}).get("message")) or "",
            "target": ((st.get("backupTarget") or {}).get("endpoint")) or "",
            "size": size or None, "volumes": len(vols),
            "schedule": labels.get("harvesterhci.io/svmbackup"),
            "created": _m(b).get("creationTimestamp"),
        })
    return out


def schedules(items):
    out = []
    for s in items:
        spec, st = s.get("spec") or {}, s.get("status") or {}
        vb = spec.get("vmbackup") or {}
        info = st.get("vmbackupInfo") or []
        last = max((i.get("createdAt") or "" for i in info), default="") if info else ""
        out.append({
            "namespace": _m(s).get("namespace"), "name": _m(s).get("name"),
            "vm": (vb.get("source") or {}).get("name"), "type": vb.get("type") or "backup",
            "cron": spec.get("cron"), "retain": spec.get("retain"), "max_failure": spec.get("maxFailure"),
            "suspended": bool(spec.get("suspend")) or bool(st.get("suspended")),
            "failures": st.get("failure") or 0, "kept": len(info), "last": last or None,
            "created": _m(s).get("creationTimestamp"),
        })
    return out


def volume_snapshots(items):
    out = []
    for s in items:
        spec, st = s.get("spec") or {}, s.get("status") or {}
        src = spec.get("source") or {}
        err = st.get("error") or {}
        out.append({
            "namespace": _m(s).get("namespace"), "name": _m(s).get("name"),
            "pvc": src.get("persistentVolumeClaimName"), "class": spec.get("volumeSnapshotClassName"),
            "ready": bool(st.get("readyToUse")), "size": st.get("restoreSize"),
            "error": err.get("message") or "",
            "owner": next((o.get("name") for o in _m(s).get("ownerReferences") or []
                           if o.get("kind") == "VirtualMachineBackup"), None),
            "created": st.get("creationTime") or _m(s).get("creationTimestamp"),
        })
    return out
