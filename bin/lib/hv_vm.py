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


# ---------------------------------------------------------------------------
# v1.61.0 : CD-ROM à chaud, carte réseau à chaud, CPU et mémoire à chaud.
# Même logique que les actions du serveur de Harvester v1.9.0
# (pkg/api/vm/handler.go : insertCdRomVolume, ejectCdRomVolume, addNic,
# removeNic, cpuAndMemoryHotplug), refaite en objets Kubernetes.
# ---------------------------------------------------------------------------
GUEST_CLUSTER_CREATOR = ("harvesterhci.io/creator", "docker-machine-driver-harvester")


def _disks(vm):
    return ((_tspec(vm).get("domain") or {}).get("devices") or {}).get("disks") or []


def sata_cdroms(vm):
    """[(nom, vide)] : les lecteurs CD-ROM SATA de la VM ; un lecteur sans
    volume est un plateau vide où insérer une image."""
    vols = {v.get("name") for v in _tspec(vm).get("volumes") or []}
    return [(d["name"], d["name"] not in vols) for d in _disks(vm)
            if (d.get("cdrom") or {}).get("bus") == "sata"]


def _gib_up(n):
    return -(-int(n) // 2**30)


def insert_cdrom(vm, device, image, rnd=random):
    """Insérer une image dans un lecteur vide : un volume tiré de l'image
    (taille arrondie au Gio, classe de l'image) branché à chaud. Rend (VM,
    nom du PVC)."""
    drives = dict(sata_cdroms(vm))
    if device not in drives:
        raise ValueError(f"no SATA CD-ROM drive named {device} on this VM")
    if not drives[device]:
        raise ValueError(f"the drive {device} already holds an image: eject it first")
    st = (image or {}).get("status") or {}
    sc = st.get("storageClassName")
    if not sc:
        raise ValueError("this image has no storage class yet (still importing?)")
    size = max(int(st.get("virtualSize") or 0), int(st.get("size") or 0))
    if size <= 0:
        raise ValueError("the image's size is not known yet")
    im = image.get("metadata") or {}
    meta = vm.get("metadata") or {}
    claim = f"{meta.get('name')}-{device}-{suffix(rnd=rnd)}"[:63].rstrip("-")
    tmpl = {"metadata": {"name": claim, "annotations": {"harvesterhci.io/imageId": f"{im.get('namespace')}/{im.get('name')}"}},
            "spec": {"accessModes": ["ReadWriteMany"], "volumeMode": "Block", "storageClassName": sc,
                     "resources": {"requests": {"storage": f"{_gib_up(size)}Gi"}}}}
    out = copy.deepcopy(vm)
    ann = out.setdefault("metadata", {}).setdefault("annotations", {})
    ann[VCT] = json.dumps(claim_templates(vm) + [tmpl], separators=(",", ":"))
    _tspec(out).setdefault("volumes", []).append(
        {"name": device, "persistentVolumeClaim": {"claimName": claim, "hotpluggable": True}})
    return out, claim


def eject_image(vm, device):
    """Retirer l'image d'un lecteur en gardant le lecteur (plateau vide) ;
    rend (VM, PVC à supprimer)."""
    if device not in dict(sata_cdroms(vm)):
        raise ValueError(f"no SATA CD-ROM drive named {device} on this VM")
    out = copy.deepcopy(vm)
    ts = _tspec(out)
    claims = [(v.get("persistentVolumeClaim") or {}).get("claimName") for v in ts.get("volumes") or []
              if v.get("name") == device]
    claims = [c for c in claims if c]
    if not any(v.get("name") == device for v in ts.get("volumes") or []):
        raise ValueError(f"the drive {device} is already empty")
    ts["volumes"] = [v for v in ts.get("volumes") or [] if v.get("name") != device]
    rest = [t for t in claim_templates(out) if (t.get("metadata") or {}).get("name") not in claims]
    ann = out.setdefault("metadata", {}).setdefault("annotations", {})
    if rest:
        ann[VCT] = json.dumps(rest, separators=(",", ":"))
    else:
        ann.pop(VCT, None)
    return out, claims


def _nic_common(vm):
    labels = (vm.get("metadata") or {}).get("labels") or {}
    if labels.get(GUEST_CLUSTER_CREATOR[0]) == GUEST_CLUSTER_CREATOR[1]:
        raise ValueError("this VM is a node of a guest cluster: its network interfaces are managed by Rancher")
    missing = [i.get("name") for i in ((_tspec(vm).get("domain") or {}).get("devices") or {}).get("interfaces") or []
               if not i.get("macAddress")]
    if missing:
        raise ValueError(f"interfaces without a MAC address in the VM spec ({', '.join(missing)}): "
                         "Harvester writes them when the VM is stopped once")


def nad_hotpluggable(nad):
    """Un réseau de VMs se branche à chaud s'il est un pont (bridge), hors du
    namespace système (réseaux de stockage ou de migration)."""
    meta = (nad or {}).get("metadata") or {}
    if meta.get("deletionTimestamp") or meta.get("namespace") == "harvester-system":
        return False
    try:
        conf = json.loads(((nad or {}).get("spec") or {}).get("config") or "{}")
    except ValueError:
        return False
    return conf.get("type") == "bridge"


def add_nic(vm, iface, network, mac=None):
    """Une carte virtio en pont, branchée sur un réseau de VMs ; KubeVirt
    l'ajoute à la VM en marche par une migration."""
    check_name(iface, "interface name")
    _nic_common(vm)
    devs = (_tspec(vm).get("domain") or {}).get("devices") or {}
    if any(i.get("name") == iface for i in devs.get("interfaces") or []):
        raise ValueError(f"this VM already has an interface named {iface}")
    if mac and not re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", mac.lower()):
        raise ValueError("MAC address: six hexadecimal pairs separated by colons")
    out = copy.deepcopy(vm)
    ts = _tspec(out)
    new = {"name": iface, "model": "virtio", "bridge": {}}
    if mac:
        new["macAddress"] = mac.lower()
    ts["domain"]["devices"].setdefault("interfaces", []).append(new)
    ts.setdefault("networks", []).append({"name": iface, "multus": {"networkName": network}})
    return out


def remove_nic(vm, iface):
    """Débrancher une carte (état « absent ») ; seulement une carte virtio
    en pont, et pas la dernière."""
    _nic_common(vm)
    out = copy.deepcopy(vm)
    ifaces = _tspec(out)["domain"]["devices"].get("interfaces") or []
    if len(ifaces) <= 1:
        raise ValueError("the VM has a single network interface: it cannot be unplugged")
    for i in ifaces:
        if i.get("name") != iface:
            continue
        if i.get("state") == "absent":
            raise ValueError(f"{iface} is already being unplugged")
        if "bridge" not in i:
            raise ValueError(f"{iface} is not in bridge mode: only a bridge interface is unplugged while running")
        if i.get("model") not in (None, "", "virtio"):
            raise ValueError(f"{iface} does not use the virtio model")
        i["state"] = "absent"
        return out
    raise ValueError(f"no interface named {iface}")


# ---------------------------------------------------------------------------
# v1.61.0 : CPU et mémoire à chaud, migration du stockage, quota
# d'instantanés, access credentials (formats de Harvester 1.9, relevés dans
# harvester-ui-extension et pkg/api/vm/handler.go v1.9.0).
# ---------------------------------------------------------------------------
HOTPLUG_ANN = "harvesterhci.io/enableCPUAndMemoryHotplug"
QUOTA_NAME = "default-resource-quota"
UNITS = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
         "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}


def quantity(v):
    """« 4Gi », « 512Mi », « 1.5G » en octets ; None si illisible."""
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(k|M|G|T|Ki|Mi|Gi|Ti)?\s*$", str(v or ""))
    if not m:
        return None
    return int(float(m.group(1)) * UNITS[m.group(2) or ""])


def cpumem_info(vm):
    dom = _tspec(vm).get("domain") or {}
    cpu, mem = dom.get("cpu") or {}, dom.get("memory") or {}
    ann = ((vm or {}).get("metadata") or {}).get("annotations") or {}
    enabled = ann.get(HOTPLUG_ANN) == "true" and (cpu.get("cores") or 1) == 1 and (cpu.get("threads") or 1) == 1
    return {"enabled": enabled, "sockets": cpu.get("sockets") or 1, "max_sockets": cpu.get("maxSockets"),
            "memory": mem.get("guest"), "max_memory": mem.get("maxGuest")}


def cpumem_patch(vm, sockets=None, memory=None):
    """Les deux remplacements JSON de l'action cpuAndMemoryHotplug, contrôlés
    contre les maximums posés à la création (maxSockets, maxGuest)."""
    info = cpumem_info(vm)
    if not info["enabled"]:
        raise ValueError("CPU and memory hotplug is not enabled on this VM (it is chosen when the VM is created)")
    ops = []
    if sockets is not None:
        s = int(sockets)
        if s < 1 or (info["max_sockets"] and s > int(info["max_sockets"])):
            raise ValueError(f"CPU: from 1 to {info['max_sockets']}")
        if s != int(info["sockets"]):
            ops.append({"op": "replace", "path": "/spec/template/spec/domain/cpu/sockets", "value": s})
    if memory:
        b = quantity(memory)
        mx = quantity(info["max_memory"]) if info["max_memory"] else None
        if not b:
            raise ValueError("memory: a size such as 4Gi")
        if b < 2**30:     # règle de KubeVirt, vue sur harvlab
            raise ValueError("memory: at least 1Gi (KubeVirt's minimum for memory hotplug)")
        if mx and b > mx:
            raise ValueError(f"memory: at most {info['max_memory']}")
        if memory != info["memory"]:
            ops.append({"op": "replace", "path": "/spec/template/spec/domain/memory/guest", "value": memory})
    if not ops:
        raise ValueError("nothing to change")
    return ops


def storage_migration(vm, source, target_pvc, vms=()):
    """Poser `targetVolume` sur l'entrée du volume source (le contrôleur de
    Harvester fait le reste) ; mêmes contrôles que son webhook."""
    entries = claim_templates(vm)
    if any(e.get("targetVolume") for e in entries):
        raise ValueError("a storage migration is already in progress on this VM")
    claims = {x["claim"] for x in vm_volumes(vm) if x["claim"]}
    if source not in claims:
        raise ValueError(f"{source} is not a volume of this VM")
    if not any((e.get("metadata") or {}).get("name") == source for e in entries):
        raise ValueError(f"{source} has no claim template: Harvester can only migrate the VM's own volumes")
    tmeta = (target_pvc or {}).get("metadata") or {}
    tname = tmeta.get("name")
    if not tname:
        raise ValueError("the target volume does not exist")
    for other in vms:
        used = {x["claim"] for x in vm_volumes(other) if x["claim"]} | \
               {(e.get("metadata") or {}).get("name") for e in claim_templates(other)}
        if tname in used:
            raise ValueError(f"{tname} is already used by the VM {(other.get('metadata') or {}).get('name')}")
    out = copy.deepcopy(vm)
    new = []
    for e in entries:
        e = dict(e)
        if (e.get("metadata") or {}).get("name") == source:
            e["targetVolume"] = tname
        new.append(e)
    out["metadata"]["annotations"][VCT] = json.dumps(new, separators=(",", ":"))
    return out


def cancel_storage_migration(vm):
    entries = claim_templates(vm)
    back = {}
    for e in entries:
        if e.get("targetVolume"):
            back[e["targetVolume"]] = (e.get("metadata") or {}).get("name")
            e.pop("targetVolume")
    if not back:
        raise ValueError("no storage migration in progress on this VM")
    out = copy.deepcopy(vm)
    ann = out["metadata"]["annotations"]
    ann[VCT] = json.dumps(entries, separators=(",", ":"))
    ann.pop("harvesterhci.io/waitingStorageMigration", None)
    for v in _tspec(out).get("volumes") or []:
        pvc = v.get("persistentVolumeClaim")
        if pvc and pvc.get("claimName") in back:
            pvc["claimName"] = back[pvc["claimName"]]
    out["spec"].pop("updateVolumesStrategy", None)
    return out


def quota_object(existing, ns, vm_name, size):
    """La ResourceQuota de Harvester avec le quota d'instantanés de la VM
    (0 ou vide : le quota est retiré). Rend l'objet complet, ou None s'il n'y
    a rien à créer."""
    b = quantity(size) if size else 0
    if size and b is None:
        raise ValueError("quota: a size such as 10Gi, or 0 to remove it")
    obj = copy.deepcopy(existing) if existing else {
        "apiVersion": "harvesterhci.io/v1beta1", "kind": "ResourceQuota",
        "metadata": {"name": QUOTA_NAME, "namespace": ns}, "spec": {}}
    lim = obj.setdefault("spec", {}).setdefault("snapshotLimit", {})
    per_vm = lim.setdefault("vmTotalSnapshotSizeQuota", {}) or {}
    if b:
        per_vm[vm_name] = b
    else:
        if not existing:
            return None
        per_vm.pop(vm_name, None)
    lim["vmTotalSnapshotSizeQuota"] = per_vm
    return obj


USER_RE = re.compile(r"^[-._0-9a-zA-Z]+$")


def access_credential(vm, kind, users, password=None, keys=None, rnd=random):
    """Un accès « Basic Auth » (mot de passe d'un compte) ou « SSH Key » (clés
    ajoutées aux comptes), propagé par l'agent invité, comme dans Harvester.
    Rend (Secret, VM modifiée)."""
    meta = vm.get("metadata") or {}
    users = [u.strip() for u in users or [] if u and u.strip()]
    if not users:
        raise ValueError("at least one user name")
    bad = [u for u in users if not USER_RE.match(u)]
    if bad:
        raise ValueError(f"user names: letters, digits, dot, dash, underscore ({', '.join(bad)})")
    name = f"{meta.get('name')}-{suffix(rnd=rnd)}"[:63]
    secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
              "metadata": {"name": name, "namespace": meta.get("namespace"),
                           "labels": {"harvesterhci.io/cloud-init-template": "harvester"},
                           "ownerReferences": [{"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachine",
                                                "name": meta.get("name"), "uid": meta.get("uid")}]}}
    out = copy.deepcopy(vm)
    ts = _tspec(out)
    tmeta = out["spec"]["template"].setdefault("metadata", {})
    tann = tmeta.setdefault("annotations", {})
    try:
        known_users = json.loads(tann.get("harvesterhci.io/dynamic-ssh-key-users") or "[]")
    except ValueError:
        known_users = []
    if kind == "basic":
        if len(users) != 1:
            raise ValueError("one user name for a password")
        if not password or len(password) < 6:
            raise ValueError("password: 6 characters at least")
        secret["stringData"] = {users[0]: password}
        ts.setdefault("accessCredentials", []).append(
            {"userPassword": {"source": {"secret": {"secretName": name}}, "propagationMethod": {"qemuGuestAgent": {}}}})
    elif kind == "ssh":
        keys = [k for k in keys or [] if k.get("public_key")]
        if not keys:
            raise ValueError("at least one SSH key")
        secret["stringData"] = {f"{k['namespace']}-{k['name']}": k["public_key"] for k in keys}
        ts.setdefault("accessCredentials", []).append(
            {"sshPublicKey": {"source": {"secret": {"secretName": name}},
                              "propagationMethod": {"qemuGuestAgent": {"users": users}}}})
        try:
            names = json.loads(tann.get("harvesterhci.io/dynamic-ssh-key-names") or "{}")
        except ValueError:
            names = {}
        names[name] = [f"{k['namespace']}/{k['name']}" for k in keys]
        tann["harvesterhci.io/dynamic-ssh-key-names"] = json.dumps(names, separators=(",", ":"))
    else:
        raise ValueError("kind: basic or ssh")
    tann["harvesterhci.io/dynamic-ssh-key-users"] = json.dumps(
        known_users + [u for u in users if u not in known_users], separators=(",", ":"))
    return secret, out
