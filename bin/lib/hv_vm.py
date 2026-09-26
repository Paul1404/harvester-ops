"""harvester-ops : les gestes sur une VM que propose l'interface de Harvester (v1.60.0).

Pause, reprise, redémarrage doux, arrêt forcé, suppression avec le choix des
volumes, clone (avec ou sans les données), template depuis une VM, éjection
d'un CD-ROM, disque branché à chaud, migration (vers un nœud choisi) et son
abandon, cloud-init. Fonctions pures : elles fabriquent les objets et les
corps de requête ; bin/harvester-resources.py `vm` les envoie.

Formes relevées sur harv1 (Harvester v1.9.0, KubeVirt 1.8.4) :
- pause / unpause / softreboot : PUT sur la sous-ressource de la VMI
  (`kubectl replace --raw …/virtualmachineinstances/N/pause -f {}`) ; le
  redémarrage doux exige l'agent invité (événement SoftRebooted) ;
- disque à chaud : PUT `…/virtualmachines/N/addvolume` puis `removevolume` ;
- les volumes d'une VM naissent de l'annotation
  `harvesterhci.io/volumeClaimTemplates` : le contrôleur de Harvester crée
  les PVC qui manquent. Un clone avec les données y met un `dataSource` qui
  désigne le volume d'origine (clone Longhorn) ;
- un template : un VirtualMachineTemplate (spec.defaultVersionId) et ses
  VirtualMachineTemplateVersion (spec.templateId, spec.vm), numérotées par
  Harvester (status.version).
"""

import copy
import json
import random
import re
import string

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
VCT = "harvesterhci.io/volumeClaimTemplates"
REMOVED_PVCS = "harvesterhci.io/removedPersistentVolumeClaims"
SSH_NAMES = "harvesterhci.io/sshNames"
SUB = "/apis/subresources.kubevirt.io/v1/namespaces/{ns}/{res}/{name}/{verb}"

# Ce que le cluster écrit sur une VM et qui ne doit pas suivre une copie :
# une MAC ou des IP recopiées entreraient en conflit avec l'original.
DROP_ANNOTATIONS = ("harvesterhci.io/mac-address", "kubevirt.io/latest-observed-api-version",
                    "kubevirt.io/storage-observed-api-version", "network.harvesterhci.io/ips",
                    REMOVED_PVCS, "kubectl.kubernetes.io/last-applied-configuration",
                    "harvesterhci.io/vmRunStrategy")


def check_name(v, what="name"):
    if not isinstance(v, str) or not NAME_RE.match(v):
        raise ValueError(f"{what}: lower-case letters, digits and dashes, 63 characters at most")
    return v


def suffix(n=5, rnd=random):
    return "".join(rnd.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def subresource(ns, name, verb, vmi=True):
    return SUB.format(ns=ns, res="virtualmachineinstances" if vmi else "virtualmachines", name=name, verb=verb)


def _tspec(vm):
    return (((vm or {}).get("spec") or {}).get("template") or {}).get("spec") or {}


def claim_templates(vm):
    raw = (((vm or {}).get("metadata") or {}).get("annotations") or {}).get(VCT)
    if not raw:
        return []
    try:
        out = json.loads(raw)
    except ValueError:
        return []
    return out if isinstance(out, list) else []


def vm_volumes(vm):
    """[{volume, disk, claim, kind}] : chaque volume de la VM avec son disque
    (disk, cdrom, lun) et son PVC s'il en a un."""
    ts = _tspec(vm)
    disks = {d.get("name"): d for d in ((ts.get("domain") or {}).get("devices") or {}).get("disks") or []}
    out = []
    for v in ts.get("volumes") or []:
        d = disks.get(v.get("name")) or {}
        kind = "cdrom" if "cdrom" in d else "lun" if "lun" in d else "disk"
        claim = (v.get("persistentVolumeClaim") or {}).get("claimName") or \
                (v.get("dataVolume") or {}).get("name")
        src = "pvc" if claim else "cloudinit" if (v.get("cloudInitNoCloud") or v.get("cloudInitConfigDrive")) \
            else "container" if v.get("containerDisk") else "other"
        out.append({"volume": v.get("name"), "disk": d, "claim": claim, "kind": kind, "source": src,
                    "hotpluggable": bool((v.get("persistentVolumeClaim") or {}).get("hotpluggable"))})
    return out


def cloudinit_secrets(vm):
    """Les Secrets cloud-init qu'une VM référence. `secretRef` est le nom JSON
    de userDataSecretRef (le nom Go) : c'est lui qu'on lit sur le cluster."""
    names = []
    for v in _tspec(vm).get("volumes") or []:
        for src in ("cloudInitNoCloud", "cloudInitConfigDrive"):
            ci = v.get(src) or {}
            for k in ("secretRef", "userDataSecretRef", "networkDataSecretRef"):
                n = (ci.get(k) or {}).get("name")
                if n and n not in names:
                    names.append(n)
    return names


# ---------------------------------------------------------------------------
# Suppression : la VM, et les volumes cochés (comme la fenêtre de Harvester)
# ---------------------------------------------------------------------------

def delete_plan(vm, remove):
    """Contrôle les volumes à supprimer avec la VM : seulement les siens.
    Rend (annotation à poser, liste des PVC)."""
    own = {x["claim"] for x in vm_volumes(vm) if x["claim"]}
    remove = [r for r in (remove or []) if r]
    stray = [r for r in remove if r not in own]
    if stray:
        raise ValueError(f"not volumes of this VM: {', '.join(stray)}")
    return {REMOVED_PVCS: ",".join(remove)}, remove


# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------

def _strip_identity(vm_meta):
    meta = {k: v for k, v in (vm_meta or {}).items()
            if k in ("labels", "annotations")}
    ann = {k: v for k, v in (meta.get("annotations") or {}).items() if k not in DROP_ANNOTATIONS}
    meta["annotations"] = ann
    return meta


def clone_manifest(vm, new_name, with_data=True, pvcs=None, start=False, rnd=random):
    """La VM copiée sous un nouveau nom, et le plan de ses volumes.

    Avec les données : chaque volume PVC devient un nouveau volume dont le
    `dataSource` est l'original (clone Longhorn, même classe, même taille).
    Sans les données : un volume issu d'une image repart de cette image, un
    volume vide repart vide. Les MAC et les IP ne suivent pas. Le cloud-init
    en Secret est copié à part (voir `cloudinit_copy`).
    """
    check_name(new_name, "new name")
    src_meta = vm.get("metadata") or {}
    ns = src_meta.get("namespace")
    pvcs = pvcs or {}
    out = {"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachine",
           "metadata": dict(_strip_identity(src_meta), name=new_name, namespace=ns),
           "spec": copy.deepcopy(vm.get("spec") or {})}
    spec = out["spec"]
    spec.pop("running", None)
    spec["runStrategy"] = "RerunOnFailure" if start else "Halted"
    tmpl = spec.setdefault("template", {})
    tmeta = tmpl.setdefault("metadata", {})
    labels = dict(tmeta.get("labels") or {})
    labels["harvesterhci.io/vmName"] = new_name
    tmeta["labels"] = labels
    ts = tmpl.setdefault("spec", {})
    if ts.get("hostname"):
        ts["hostname"] = new_name
    for iface in ((ts.get("domain") or {}).get("devices") or {}).get("interfaces") or []:
        iface.pop("macAddress", None)
    old_templates = {t.get("metadata", {}).get("name"): t for t in claim_templates(vm)}
    new_templates, renames = [], {}
    for x in vm_volumes(vm):
        claim = x["claim"]
        if not claim:
            continue
        new_claim = f"{new_name}-{x['volume']}-{suffix(rnd=rnd)}"[:63].rstrip("-")
        renames[claim] = new_claim
        src_pvc = pvcs.get(claim) or {}
        t = copy.deepcopy(old_templates.get(claim) or {})
        tspec = t.get("spec") or copy.deepcopy({k: v for k, v in (src_pvc.get("spec") or {}).items()
                                               if k in ("accessModes", "resources", "volumeMode", "storageClassName")})
        if not tspec:
            raise ValueError(f"volume {claim}: neither a claim template nor the volume itself could be read")
        req = ((src_pvc.get("spec") or {}).get("resources") or {}).get("requests", {}).get("storage")
        if req:     # un volume agrandi depuis sa création : garder sa taille actuelle
            tspec.setdefault("resources", {}).setdefault("requests", {})["storage"] = req
        tspec.pop("volumeName", None)
        tspec.pop("dataSource", None)
        tspec.pop("dataSourceRef", None)
        if not tspec.get("storageClassName") and (src_pvc.get("spec") or {}).get("storageClassName"):
            tspec["storageClassName"] = src_pvc["spec"]["storageClassName"]
        ann = dict((t.get("metadata") or {}).get("annotations") or {})
        if not ann.get("harvesterhci.io/imageId"):
            img = ((src_pvc.get("metadata") or {}).get("annotations") or {}).get("harvesterhci.io/imageId")
            if img:
                ann["harvesterhci.io/imageId"] = img
        if with_data:
            tspec["dataSource"] = {"kind": "PersistentVolumeClaim", "name": claim}
        new_templates.append({"metadata": {"name": new_claim, "annotations": ann}, "spec": tspec})
    for v in ts.get("volumes") or []:
        pvc = v.get("persistentVolumeClaim")
        if pvc and pvc.get("claimName") in renames:
            pvc["claimName"] = renames[pvc["claimName"]]
    ann = out["metadata"]["annotations"]
    if new_templates:
        ann[VCT] = json.dumps(new_templates, separators=(",", ":"))
    else:
        ann.pop(VCT, None)
    return out, renames


def cloudinit_copy(secret, new_name, rnd=random):
    """Une copie du Secret cloud-init pour le clone (même contenu)."""
    meta = secret.get("metadata") or {}
    name = f"{new_name}-{suffix(rnd=rnd)}"[:63]
    return {"apiVersion": "v1", "kind": "Secret", "type": secret.get("type") or "Opaque",
            "metadata": {"name": name, "namespace": meta.get("namespace"),
                         "labels": {k: v for k, v in (meta.get("labels") or {}).items()}},
            "data": dict(secret.get("data") or {})}


def rename_secret_refs(vm, old, new):
    for v in _tspec(vm).get("volumes") or []:
        for src in ("cloudInitNoCloud", "cloudInitConfigDrive"):
            ci = v.get(src) or {}
            for k in ("secretRef", "userDataSecretRef", "networkDataSecretRef"):
                if (ci.get(k) or {}).get("name") == old:
                    ci[k]["name"] = new
    return vm


# ---------------------------------------------------------------------------
# Template depuis une VM
# ---------------------------------------------------------------------------

def template_objects(vm, name, description="", version_name=None, images=None):
    """(template, version) tirés d'une VM existante. `images` : {claim: image
    exportée} pour un template AVEC les données (chaque volume repart de son
    image) ; sinon les volumes repartent de leur image d'origine ou vides."""
    check_name(name, "template name")
    meta = vm.get("metadata") or {}
    ns = meta.get("namespace")
    vm_meta = _strip_identity(meta)
    templates = []
    for t in claim_templates(vm):
        t = copy.deepcopy(t)
        claim = (t.get("metadata") or {}).get("name")
        spec = t.setdefault("spec", {})
        spec.pop("dataSource", None)
        spec.pop("volumeName", None)
        if images and claim in images:
            img = images[claim]
            im = img.get("metadata") or {}
            t.setdefault("metadata", {}).setdefault("annotations", {})["harvesterhci.io/imageId"] = \
                f"{im.get('namespace')}/{im.get('name')}"
            sc = (img.get("status") or {}).get("storageClassName")
            if sc:
                spec["storageClassName"] = sc
        templates.append(t)
    if templates:
        vm_meta["annotations"][VCT] = json.dumps(templates, separators=(",", ":"))
    spec = copy.deepcopy(vm.get("spec") or {})
    spec.pop("running", None)
    spec["runStrategy"] = spec.get("runStrategy") or "RerunOnFailure"
    for iface in (((spec.get("template") or {}).get("spec") or {}).get("domain") or {}).get("devices", {}).get("interfaces") or []:
        iface.pop("macAddress", None)
    tmpl = {"apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineTemplate",
            "metadata": {"name": name, "namespace": ns},
            "spec": {"description": description or f"Created from the VM {meta.get('name')}"}}
    version = {"apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineTemplateVersion",
               "metadata": {"generateName": f"{name}-", "namespace": ns},
               "spec": {"templateId": f"{ns}/{name}", "description": description or "",
                        "vm": {"metadata": vm_meta, "spec": spec}}}
    if version_name:
        version["metadata"] = {"name": check_name(version_name, "version name"), "namespace": ns}
    ssh = (meta.get("annotations") or {}).get(SSH_NAMES)
    if ssh:
        try:
            version["spec"]["keyPairIds"] = [k if "/" in k else f"{ns}/{k}" for k in json.loads(ssh)]
        except ValueError:
            pass
    return tmpl, version


def export_image_manifest(ns, claim, display_name, storage_class=None):
    """Une image exportée depuis un volume (menu « Export Image » de Harvester)."""
    body = {"displayName": display_name[:253], "sourceType": "export-from-volume", "pvcName": claim,
            "pvcNamespace": ns, "backend": "backingimage", "retry": 3}
    meta = {"generateName": "image-", "namespace": ns}
    if storage_class:
        body["targetStorageClassName"] = storage_class
        meta["annotations"] = {"harvesterhci.io/storageClassName": storage_class}
    return {"apiVersion": "harvesterhci.io/v1beta1", "kind": "VirtualMachineImage", "metadata": meta, "spec": body}


# ---------------------------------------------------------------------------
# CD-ROM, disque à chaud, migration
# ---------------------------------------------------------------------------

def eject_patch(vm, volume):
    """Retire un CD-ROM de la VM (disque, volume et son modèle de volume).
    Rend (VM modifiée, PVC du CD-ROM ou None)."""
    vols = {x["volume"]: x for x in vm_volumes(vm)}
    x = vols.get(volume)
    if not x:
        raise ValueError(f"no volume {volume} on this VM")
    if x["kind"] != "cdrom":
        raise ValueError(f"{volume} is not a CD-ROM")
    out = copy.deepcopy(vm)
    ts = _tspec(out)
    devs = (ts.get("domain") or {}).get("devices") or {}
    devs["disks"] = [d for d in devs.get("disks") or [] if d.get("name") != volume]
    ts["volumes"] = [v for v in ts.get("volumes") or [] if v.get("name") != volume]
    if x["claim"]:
        rest = [t for t in claim_templates(out) if (t.get("metadata") or {}).get("name") != x["claim"]]
        ann = out.setdefault("metadata", {}).setdefault("annotations", {})
        if rest:
            ann[VCT] = json.dumps(rest, separators=(",", ":"))
        else:
            ann.pop(VCT, None)
    return out, x["claim"]


def hotplug_body(volume, claim, bus="scsi"):
    check_name(volume, "disk name")
    if bus not in ("scsi", "virtio", "sata"):
        raise ValueError("bus: scsi, virtio or sata")
    return {"name": volume, "disk": {"name": volume, "disk": {"bus": bus}},
            "volumeSource": {"persistentVolumeClaim": {"claimName": claim, "hotpluggable": True}}}


def migration_manifest(ns, vmi, node=None):
    """Une migration à chaud ; vers un nœud choisi, par `addedNodeSelector`
    (KubeVirt 1.5 et plus), qui s'ajoute aux contraintes de la VM sans les
    remplacer."""
    spec = {"vmiName": vmi}
    if node:
        spec["addedNodeSelector"] = {"kubernetes.io/hostname": node}
    return {"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachineInstanceMigration",
            "metadata": {"generateName": f"{vmi}-", "namespace": ns}, "spec": spec}


def active_migrations(migrations, vmi):
    done = ("Succeeded", "Failed")
    return [m for m in migrations
            if (m.get("spec") or {}).get("vmiName") == vmi
            and ((m.get("status") or {}).get("phase") not in done)
            and not (m.get("metadata") or {}).get("deletionTimestamp")]


# ---------------------------------------------------------------------------
# Cloud-init
# ---------------------------------------------------------------------------

def cloudinit_secret(vm_name, ns, user_data, network_data, rnd=random):
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": f"{vm_name}-{suffix(rnd=rnd)}"[:63], "namespace": ns},
            "stringData": {"userdata": user_data or "", "networkdata": network_data or ""}}


def attach_cloudinit(vm, secret_name):
    """Branche un Secret cloud-init sur une VM qui n'en a pas (comme le fait
    Harvester : un disque `cloudinitdisk` en virtio) ou remplace la source
    inline d'un cloud-init existant par ce Secret."""
    out = copy.deepcopy(vm)
    ts = out.setdefault("spec", {}).setdefault("template", {}).setdefault("spec", {})
    vols = ts.setdefault("volumes", [])
    ref = {"secretRef": {"name": secret_name}, "networkDataSecretRef": {"name": secret_name}}
    for v in vols:
        if "cloudInitNoCloud" in v or "cloudInitConfigDrive" in v:
            key = "cloudInitNoCloud" if "cloudInitNoCloud" in v else "cloudInitConfigDrive"
            v[key] = dict(ref)
            return out
    name = "cloudinitdisk"
    taken = {v.get("name") for v in vols}
    i = 1
    while name in taken:
        i += 1
        name = f"cloudinitdisk{i}"
    vols.append({"name": name, "cloudInitNoCloud": dict(ref)})
    devs = ts.setdefault("domain", {}).setdefault("devices", {})
    devs.setdefault("disks", []).append({"name": name, "disk": {"bus": "virtio"}})
    return out


def ssh_names_annotation(names):
    return {SSH_NAMES: json.dumps(sorted({str(n) for n in names or [] if str(n).strip()}))}


AGENT_PKG = "qemu-guest-agent"
AGENT_CMD = ["systemctl", "enable", "--now", "qemu-guest-agent.service"]


def _cloud_config(user_data):
    """Le #cloud-config en dictionnaire (None si c'est un script)."""
    text = user_data or ""
    if text.strip() and not text.lstrip().startswith("#cloud-config"):
        return None
    import yaml
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as e:
        raise ValueError(f"the user-data is not valid YAML: {str(e).splitlines()[0]}") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("the user-data #cloud-config must be a mapping")
    return data


def _dump_cloud_config(data):
    import yaml
    return "#cloud-config\n" + yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)


def with_guest_agent(user_data):
    """Ce qu'ajoute la case « installer l'agent invité » de Harvester : le
    paquet qemu-guest-agent et son démarrage, sans rien retirer du reste."""
    text = user_data or ""
    if AGENT_PKG in text:
        return text
    data = _cloud_config(text)
    if data is None:
        raise ValueError("the user-data is a script, not a #cloud-config: add qemu-guest-agent to it yourself")
    pkgs = data.get("packages") or []
    if not isinstance(pkgs, list):
        raise ValueError("packages: a list is expected in the user-data")
    data["packages"] = pkgs + [AGENT_PKG]
    cmds = data.get("runcmd") or []
    if not isinstance(cmds, list):
        raise ValueError("runcmd: a list is expected in the user-data")
    data["runcmd"] = cmds + [list(AGENT_CMD)]
    return _dump_cloud_config(data)


def with_ssh_keys(user_data, keys):
    """Les clés publiques choisies (menu SSH Keys de la création), ajoutées à
    ssh_authorized_keys sans doublon."""
    keys = [k.strip() for k in keys or [] if k and k.strip()]
    if not keys:
        return user_data or ""
    data = _cloud_config(user_data)
    if data is None:
        raise ValueError("the user-data is a script, not a #cloud-config: SSH keys cannot be added to it")
    have = data.get("ssh_authorized_keys") or []
    if not isinstance(have, list):
        raise ValueError("ssh_authorized_keys: a list is expected in the user-data")
    data["ssh_authorized_keys"] = have + [k for k in keys if k not in have]
    return _dump_cloud_config(data)
