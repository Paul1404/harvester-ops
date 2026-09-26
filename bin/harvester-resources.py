#!/usr/bin/env python3
"""harvester-resources : les objets d'un cluster Harvester que la console range
sous Cluster (Storage, Network, Add-ons, Security) et dans la fenêtre Backups.

Toute écriture de la console sur ces objets passe par ce script (parité CLI) ;
il s'utilise aussi seul.

  harvester-resources addon --cluster harv1 --namespace kube-system --name descheduler --enable
  harvester-resources addon --cluster harv1 --namespace kube-system --name descheduler --disable

  harvester-resources backup create  --cluster harv1 --namespace default --vm web [--type snapshot] [--name N]
  harvester-resources backup restore --cluster harv1 --namespace default --name B (--new-vm NAME [--keep-mac] | --replace) [--halt]
  harvester-resources backup delete  --cluster harv1 --namespace default --name B
  harvester-resources schedule create --cluster harv1 --namespace default --name S --vm web --cron "0 2 * * *" --retain 7 --max-failure 3 [--type snapshot]
  harvester-resources schedule suspend|resume|delete --cluster harv1 --namespace default --name S
  harvester-resources volsnap restore --cluster harv1 --namespace default --name SNAP --new-volume NAME
  harvester-resources volsnap delete  --cluster harv1 --namespace default --name SNAP

  harvester-resources create --cluster harv1 --kind image|storageclass|sshkey|secret|network|volume --spec request.json
  harvester-resources delete --cluster harv1 --kind KIND [--namespace default] --name NAME
  harvester-resources sc-default --cluster harv1 --name harv-rep1
  harvester-resources volume-expand --cluster harv1 --namespace default --name web-root --size 40Gi
  harvester-resources addon-values --cluster harv1 --namespace NS --name ADDON --values values.yaml
  harvester-resources yaml --cluster harv1 --kind vm --namespace default --name web --file web.yaml [--dry-run]
  harvester-resources yaml --cluster harv1 --kind image --create --file image.yaml

  harvester-resources vm pause|unpause|softreboot|restart|force-stop --cluster harv1 --namespace default --name web
  harvester-resources vm delete --cluster harv1 --namespace default --name web [--remove-volumes web-root,web-data]
  harvester-resources vm clone --cluster harv1 --namespace default --name web --new-name web2 [--with-data] [--start]
  harvester-resources vm eject --cluster harv1 --namespace default --name web --volume cdrom [--delete-volume]
  harvester-resources vm add-volume --cluster harv1 --namespace default --name web --claim data [--bus scsi]
  harvester-resources vm remove-volume --cluster harv1 --namespace default --name web --volume data
  harvester-resources vm migrate --cluster harv1 --namespace default --name web [--node n2]
  harvester-resources vm abort-migration --cluster harv1 --namespace default --name web
  harvester-resources vm template --cluster harv1 --namespace default --name web --template-name web-tpl [--with-data]
  harvester-resources vm cloudinit --cluster harv1 --namespace default --name web --user-data u.yaml [--guest-agent]

Sorties : 0 fait, 1 échec, 2 refusé par le contrôle, 3 annulé. Les étapes
s'écrivent sur stderr en `STEP_EVENT|étape|statut|message`, que la console
relaie au dock.
"""

import argparse
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from kube import Kube, KubeError, cluster_config  # noqa: E402
import hv_backups as hb  # noqa: E402
import hv_objects as ho  # noqa: E402
import hv_yaml as hy  # noqa: E402
import hv_vm as hv  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_BLOCKED, EXIT_CANCELLED = 0, 1, 2, 3
K_ADDON = "addons.harvesterhci.io"


class Cancelled(Exception):
    pass


def step(sid, status, msg=""):
    clean = " ".join(str(msg).split())
    sys.stderr.write(f"STEP_EVENT|{sid}|{status}|{clean}\n")
    sys.stderr.flush()


def _on_signal(signum, frame):
    raise Cancelled(f"signal {signum}")


def refusal(msg):
    """Un refus d'un webhook de Harvester, dit sans l'enveloppe de kubectl :
    « Error from server (BadRequest): admission webhook "validator.harvesterhci.io"
    denied the request: descheduler addon cannot be enabled as not enough
    nodes exist in the cluster » (vu sur harv1, un seul nœud)."""
    text = str(msg)
    if "denied the request:" in text:
        return "Harvester refused: " + text.split("denied the request:", 1)[1].strip()
    return text


def kube_from(args):
    entry = cluster_config(args.cluster) if args.cluster else None
    kc = args.kubeconfig or (entry or {}).get("kubeconfig")
    if not kc:
        raise SystemExit("give --cluster (with a kubeconfig in the configuration) or --kubeconfig")
    return Kube(kc)


# ---------------------------------------------------------------------------
# Add-ons (addons.harvesterhci.io)
#
# Activer un add-on installe son chart (Harvester le déploie par un HelmChart),
# le désactiver le retire. Vu sur harv1 (Harvester v1.9.0) : le statut passe
# par AddonEnabling / AddonDisabling puis AddonDeploySuccessful / AddonDisabled,
# et un échec se lit dans AddonDeployFailed ou la condition OperationFailed.
# ---------------------------------------------------------------------------

def addon_state(obj):
    """(statut, message d'échec éventuel) d'un Addon."""
    st = (obj or {}).get("status") or {}
    failed = ""
    for c in st.get("conditions") or []:
        if c.get("type") == "OperationFailed" and str(c.get("status")) == "True":
            failed = c.get("message") or c.get("reason") or "operation failed"
    return st.get("status") or "", failed


def addon_settled(status, failed, want):
    """True si l'add-on a atteint l'état voulu, False s'il a échoué, None
    s'il est encore en chemin."""
    if "Failed" in status or failed:
        return False
    if want and status in ("AddonDeploySuccessful", "AddonUpdateSuccessful", "AddonDeployed"):
        return True
    if not want and status == "AddonDisabled":
        return True
    return None


def set_addon(kube, ns, name, want, timeout=900, sleep=time.sleep, now=time.time):
    obj = kube.get(K_ADDON, ns, name)
    if obj is None:
        raise ValueError(f"no add-on {ns}/{name} on this cluster")
    label = "enable" if want else "disable"
    status, failed = addon_state(obj)
    if bool((obj.get("spec") or {}).get("enabled")) == want and addon_settled(status, "", want):
        step("patch", "done", f"{ns}/{name} is already {'enabled' if want else 'disabled'}")
        step("wait", "done", status)
        return EXIT_OK
    step("patch", "running", f"{label} {ns}/{name}")
    kube.patch(K_ADDON, ns, name, {"spec": {"enabled": want}})
    step("patch", "done", f"spec.enabled = {str(want).lower()}")
    step("wait", "running", "Harvester applies the change")
    deadline = now() + timeout
    last = None
    # Le contrôleur met quelques secondes à prendre la main : un statut encore
    # « succès » de l'état précédent ne doit pas être lu comme la fin.
    sleep(3)
    while now() < deadline:
        obj = kube.get(K_ADDON, ns, name) or {}
        status, failed = addon_state(obj)
        if status != last:
            step("wait", "running", status or "pending")
            last = status
        done = addon_settled(status, failed, want)
        if done is True and bool((obj.get("spec") or {}).get("enabled")) == want:
            step("wait", "done", status)
            return EXIT_OK
        if done is False:
            step("wait", "error", failed or status)
            return EXIT_FAIL
        sleep(5)
    step("wait", "error", f"still {last or 'pending'} after {timeout} s")
    return EXIT_FAIL


def cmd_addon(args):
    kube = kube_from(args)
    return set_addon(kube, args.namespace, args.name, args.enable, timeout=args.timeout)


# ---------------------------------------------------------------------------
# v1.58.0 : sauvegardes, instantanés, planifications, instantanés de volumes
# ---------------------------------------------------------------------------

def _wait(kube, kind, ns, name, done, timeout, sleep=time.sleep, now=time.time, label="wait"):
    """Relit l'objet jusqu'à `done(obj)` (True fini, False échec, None en cours)."""
    deadline = now() + timeout
    last = None
    while now() < deadline:
        obj = kube.get(kind, ns, name)
        res, msg = done(obj)
        if msg and msg != last:
            step(label, "running", msg)
            last = msg
        if res is True:
            step(label, "done", msg or "done")
            return EXIT_OK
        if res is False:
            step(label, "error", msg or "failed")
            return EXIT_FAIL
        sleep(5)
    step(label, "error", f"not finished after {timeout} s")
    return EXIT_FAIL


def _backup_done(obj):
    if obj is None:
        return None, "waiting for the backup object"
    st = obj.get("status") or {}
    err = (st.get("error") or {}).get("message")
    if err:
        return False, err
    if st.get("readyToUse"):
        return True, "ready"
    prog = st.get("progress")
    return None, f"{prog} %" if prog is not None else "in progress"


def cmd_backup(args):
    kube = kube_from(args)
    ns = args.namespace
    if args.action == "create":
        if not args.vm:
            raise ValueError("--vm is required")
        name = args.name or f"{args.vm}-{time.strftime('%Y%m%d-%H%M%S')}"
        if args.type == "backup":
            target = kube.get("settings.harvesterhci.io", None, "backup-target")
            if not (target or {}).get("value") or '"endpoint":""' in (target or {}).get("value", "").replace(" ", ""):
                raise ValueError("no backup target is set on this cluster (Harvester setting backup-target)")
        step("create", "running", f"{args.type} of {ns}/{args.vm}: {name}")
        kube.create(hb.backup_manifest(ns, args.vm, name, args.type))
        step("create", "done", name)
        return _wait(kube, hb.K_BACKUP, ns, name, _backup_done, args.timeout)
    if args.action == "delete":
        step("delete", "running", f"{ns}/{args.name}")
        kube.delete(hb.K_BACKUP, ns, hb.check_name(args.name, "backup"))
        return _wait(kube, hb.K_BACKUP, ns, args.name,
                     lambda o: (True, "deleted") if o is None else (None, "deleting"), args.timeout, label="delete")
    # restore
    b = kube.get(hb.K_BACKUP, ns, hb.check_name(args.name, "backup"))
    if b is None:
        raise ValueError(f"no backup or snapshot {ns}/{args.name}")
    if not (b.get("status") or {}).get("readyToUse"):
        raise ValueError(f"{args.name} is not ready yet")
    source = ((b.get("spec") or {}).get("source") or {}).get("name")
    if args.replace:
        vm = kube.get("virtualmachines.kubevirt.io", ns, source)
        if vm is None:
            raise ValueError(f"the original VM {ns}/{source} no longer exists: restore into a new VM")
        if kube.get("virtualmachineinstances.kubevirt.io", ns, source) is not None:
            raise ValueError(f"stop {ns}/{source} first: Harvester only replaces a stopped VM")
        target, new_vm = source, False
    else:
        if not args.new_vm:
            raise ValueError("give --new-vm NAME or --replace")
        if kube.get("virtualmachines.kubevirt.io", ns, args.new_vm) is not None:
            raise ValueError(f"a VM {ns}/{args.new_vm} already exists")
        target, new_vm = args.new_vm, True
    crd = kube.get("customresourcedefinitions.apiextensions.k8s.io", None, hb.K_RESTORE)
    import vm_transfer as vt
    man = hb.restore_manifest(ns, args.name, target, new_vm, keep_mac=args.keep_mac, halt=args.halt,
                              delete_policy=args.delete_policy,
                              name=f"restore-{target}-{time.strftime('%Y%m%d%H%M%S')}"[:63],
                              supports_halt=vt.restore_supports_halt(crd))
    step("restore", "running", f"{args.name} into {'new VM ' if new_vm else ''}{ns}/{target}")
    kube.create(man)
    rname = man["metadata"]["name"]

    def restored(obj):
        if obj is None:
            return None, "waiting for the restore object"
        st = obj.get("status") or {}
        for c in st.get("conditions") or []:
            if c.get("type") == "Failure" and str(c.get("status")) == "True":
                return False, c.get("message") or c.get("reason") or "restore failed"
        if st.get("complete"):
            return True, f"{ns}/{target} restored"
        prog = [c.get("message") for c in st.get("conditions") or [] if c.get("type") == "Progressing"]
        return None, prog[0] if prog and prog[0] else "restoring"
    return _wait(kube, hb.K_RESTORE, ns, rname, restored, args.timeout, label="restore")


def cmd_schedule(args):
    kube = kube_from(args)
    ns, name = args.namespace, hb.check_name(args.name)
    if args.action == "create":
        man = hb.schedule_manifest(ns, name, args.vm, args.cron, args.retain, args.max_failure, args.type)
        if args.type == "backup":
            target = kube.get("settings.harvesterhci.io", None, "backup-target")
            if not (target or {}).get("value"):
                raise ValueError("no backup target is set on this cluster: schedule snapshots, or set one")
        step("create", "running", f"{args.type} of {ns}/{args.vm}, {man['spec']['cron']}, keep {args.retain}")
        kube.create(man)
        step("create", "done", name)
        return EXIT_OK
    if args.action in ("suspend", "resume"):
        want = args.action == "suspend"
        step(args.action, "running", f"{ns}/{name}")
        kube.patch(hb.K_SCHEDULE, ns, name, {"spec": {"suspend": want}})
        step(args.action, "done", "suspended" if want else "resumed")
        return EXIT_OK
    step("delete", "running", f"{ns}/{name} (its backups stay)")
    kube.delete(hb.K_SCHEDULE, ns, name)
    step("delete", "done", name)
    return EXIT_OK


def cmd_volsnap(args):
    kube = kube_from(args)
    ns, name = args.namespace, hb.check_name(args.name, "snapshot")
    snap = kube.get(hb.K_VOLSNAP, ns, name)
    if snap is None:
        raise ValueError(f"no volume snapshot {ns}/{name}")
    if args.action == "delete":
        owner = next((o.get("name") for o in (snap.get("metadata") or {}).get("ownerReferences") or []
                      if o.get("kind") == "VirtualMachineBackup"), None)
        if owner:
            raise ValueError(f"this snapshot belongs to the VM snapshot {owner}: delete that one instead")
        step("delete", "running", f"{ns}/{name}")
        kube.delete(hb.K_VOLSNAP, ns, name)
        step("delete", "done", name)
        return EXIT_OK
    # restore
    st = snap.get("status") or {}
    if not st.get("readyToUse"):
        raise ValueError(f"{name} is not ready yet")
    new = hb.check_name(args.new_volume, "new volume")
    if kube.get("persistentvolumeclaims", ns, new) is not None:
        raise ValueError(f"a volume {ns}/{new} already exists")
    src_pvc = kube.get("persistentvolumeclaims", ns, ((snap.get("spec") or {}).get("source") or {})
                       .get("persistentVolumeClaimName") or "")
    sc = args.storage_class or ((src_pvc or {}).get("spec") or {}).get("storageClassName")
    size = args.size or st.get("restoreSize")
    step("restore", "running", f"{ns}/{name} into a new volume {new} ({size})")
    kube.create(hb.volume_restore_manifest(ns, name, new, size, sc))

    def bound(obj):
        phase = ((obj or {}).get("status") or {}).get("phase")
        return (True, f"{ns}/{new} bound") if phase == "Bound" else (None, phase or "pending")
    return _wait(kube, "persistentvolumeclaims", ns, new, bound, args.timeout, label="restore")


# ---------------------------------------------------------------------------
# v1.59.0 : créer et supprimer les objets des sections
# ---------------------------------------------------------------------------

def _read_json(path):
    import json
    raw = sys.stdin.read() if path == "-" else Path(path).read_text()
    return json.loads(raw)


def _default_class(kube):
    for sc in kube.list(ho.K["storageclass"]):
        ann = (sc.get("metadata") or {}).get("annotations") or {}
        if ann.get("storageclass.kubernetes.io/is-default-class") == "true":
            return (sc.get("metadata") or {}).get("name")
    return None


def cmd_create(args):
    kube = kube_from(args)
    spec = _read_json(args.spec)
    kind = args.kind
    image = None
    if kind == "volume" and spec.get("image"):
        ref = str(spec["image"])
        ins, iname = ref.split("/", 1) if "/" in ref else (spec.get("namespace") or "default", ref)
        image = kube.get(ho.K["image"], ins, iname)
        if image is None:
            raise ValueError(f"no image {ins}/{iname}")
    man = ho.normalize(kind, spec, default_class=_default_class(kube) if kind == "image" else None, image=image)
    meta = man["metadata"]
    ns = meta.get("namespace")
    if meta.get("name") and kube.get(ho.K[kind], ns, meta["name"]) is not None:
        raise ValueError(f"{kind} {ns + '/' if ns else ''}{meta['name']} already exists")
    step("create", "running", f"{kind} {ns + '/' if ns else ''}{meta.get('name') or meta.get('generateName', '') + '…'}")
    made = kube.create(man)
    name = (made.get("metadata") or {}).get("name") or meta.get("name")
    step("create", "done", name)
    if kind == "image":
        def imported(obj):
            st = (obj or {}).get("status") or {}
            for c in st.get("conditions") or []:
                if c.get("type") == "RetryLimitExceeded" and str(c.get("status")) == "True":
                    return False, c.get("message") or "download failed"
            prog = st.get("progress")
            if prog == 100 and any(c.get("type") == "Imported" and str(c.get("status")) == "True"
                                   for c in st.get("conditions") or []):
                return True, f"{ns}/{name} imported"
            return None, f"{prog or 0} %"
        return _wait(kube, ho.K["image"], ns, name, imported, args.timeout, label="import")
    if kind == "sshkey":
        def valid(obj):
            conds = ((obj or {}).get("status") or {}).get("conditions") or []
            if any(c.get("type") == "validated" and str(c.get("status")) == "True" for c in conds):
                return True, "key validated"
            bad = [c for c in conds if c.get("type") == "validated" and str(c.get("status")) == "False"]
            return (False, bad[0].get("message") or "invalid key") if bad else (None, "validating")
        return _wait(kube, ho.K["sshkey"], ns, name, valid, 120, label="validate")
    if kind == "volume":
        def bound(obj):
            phase = ((obj or {}).get("status") or {}).get("phase")
            return (True, f"{ns}/{name} bound") if phase == "Bound" else (None, phase or "pending")
        return _wait(kube, ho.K["volume"], ns, name, bound, args.timeout, label="bind")
    return EXIT_OK


def cmd_delete(args):
    kube = kube_from(args)
    kind, ns, name = args.kind, args.namespace, args.name
    if kind in ho.NAMESPACED and not ns:
        raise ValueError("--namespace is required")
    obj = kube.get(ho.K[kind], ns if kind in ho.NAMESPACED else None, name)
    if obj is None:
        raise ValueError(f"no {kind} {ns + '/' if ns else ''}{name}")
    vms = kube.list("virtualmachines.kubevirt.io") if kind in ("network", "secret", "volume") else []
    pvcs = kube.list("persistentvolumeclaims") if kind in ("image", "storageclass") else []
    images = kube.list(ho.K["image"]) if kind == "storageclass" else []
    users = ho.vms_using(kind, ns if kind in ho.NAMESPACED else None, name, vms, pvcs, images)
    if kind == "volume":
        pods = [p for p in kube.list("pods", ns)
                if any((v.get("persistentVolumeClaim") or {}).get("claimName") == name
                       for v in (p.get("spec") or {}).get("volumes") or [])]
        users += [f"pod {ns}/{(p.get('metadata') or {}).get('name')}" for p in pods
                  if not ((p.get("metadata") or {}).get("name") or "").startswith("virt-launcher-")]
    if users and kind != "sshkey":
        raise ValueError(f"still used by {', '.join(users[:6])}{' …' if len(users) > 6 else ''}")
    if users:
        step("check", "done", f"the VMs {', '.join(users[:6])} keep the key they received")
    step("delete", "running", f"{kind} {ns + '/' if ns else ''}{name}")
    kube.delete(ho.K[kind], ns if kind in ho.NAMESPACED else None, name)
    return _wait(kube, ho.K[kind], ns if kind in ho.NAMESPACED else None, name,
                 lambda o: (True, "deleted") if o is None else (None, "deleting"), args.timeout, label="delete")


def cmd_sc_default(args):
    """Changer la classe par défaut. Harvester refuse d'en poser une seconde
    (« default storage class harv-rep1 already exists, please reset it first »,
    vu sur harv1) : l'ancienne est retirée d'abord, puis la nouvelle posée."""
    kube = kube_from(args)
    target = ho._name(args.name, "storage class")
    classes = kube.list(ho.K["storageclass"])
    if not any((c.get("metadata") or {}).get("name") == target for c in classes):
        raise ValueError(f"no storage class {target}")
    off = {"storageclass.kubernetes.io/is-default-class": "false",
           "storageclass.beta.kubernetes.io/is-default-class": "false"}
    on = {"storageclass.kubernetes.io/is-default-class": "true",
          "storageclass.beta.kubernetes.io/is-default-class": "true"}
    previous = []
    for c in classes:
        n = (c.get("metadata") or {}).get("name")
        ann = (c.get("metadata") or {}).get("annotations") or {}
        if n != target and "true" in (ann.get("storageclass.kubernetes.io/is-default-class"),
                                      ann.get("storageclass.beta.kubernetes.io/is-default-class")):
            step("default", "running", f"{n} is no longer the default")
            kube.patch(ho.K["storageclass"], None, n, {"metadata": {"annotations": off}})
            previous.append(n)
    step("default", "running", f"{target} becomes the default class")
    try:
        kube.patch(ho.K["storageclass"], None, target, {"metadata": {"annotations": on}})
    except KubeError:
        # remettre l'ancienne : ne jamais laisser le cluster sans classe par défaut
        for n in previous:
            kube.patch(ho.K["storageclass"], None, n, {"metadata": {"annotations": on}})
        raise
    step("default", "done", target)
    return EXIT_OK


def cmd_volume_expand(args):
    kube = kube_from(args)
    ns, name = args.namespace, args.name
    new = ho._size(args.size)
    pvc = kube.get(ho.K["volume"], ns, name)
    if pvc is None:
        raise ValueError(f"no volume {ns}/{name}")
    cur = ((pvc.get("spec") or {}).get("resources") or {}).get("requests", {}).get("storage")
    if ho.size_bytes(new) is None or (ho.size_bytes(cur) and ho.size_bytes(new) <= ho.size_bytes(cur)):
        raise ValueError(f"a volume only grows: give more than {cur}")
    sc = kube.get(ho.K["storageclass"], None, (pvc.get("spec") or {}).get("storageClassName") or "")
    if sc is not None and not sc.get("allowVolumeExpansion"):
        raise ValueError("its storage class does not allow expansion")
    step("expand", "running", f"{ns}/{name}: {cur} to {new}")
    kube.patch(ho.K["volume"], ns, name, {"spec": {"resources": {"requests": {"storage": new}}}})

    def grown(obj):
        cap = ((obj or {}).get("status") or {}).get("capacity", {}).get("storage")
        if cap and ho.size_bytes(cap) and ho.size_bytes(cap) >= ho.size_bytes(new):
            return True, f"{ns}/{name} is {cap}"
        conds = ((obj or {}).get("status") or {}).get("conditions") or []
        wait = [c.get("type") for c in conds]
        if "FileSystemResizePending" in wait or "Resizing" in wait:
            return None, "resizing (a running VM sees it after a restart)"
        return None, f"requested {new}, capacity {cap}"
    return _wait(kube, ho.K["volume"], ns, name, grown, args.timeout, label="expand")


def cmd_addon_values(args):
    kube = kube_from(args)
    text = ho.check_values(Path(args.values).read_text() if args.values != "-" else sys.stdin.read())
    obj = kube.get(K_ADDON, args.namespace, args.name)
    if obj is None:
        raise ValueError(f"no add-on {args.namespace}/{args.name} on this cluster")
    step("values", "running", f"new configuration for {args.namespace}/{args.name}")
    kube.patch(K_ADDON, args.namespace, args.name, {"spec": {"valuesContent": text}})
    step("values", "done", "saved")
    if not (obj.get("spec") or {}).get("enabled"):
        step("wait", "done", "the add-on is disabled: the configuration applies when it is enabled")
        return EXIT_OK
    time.sleep(3)
    return set_addon(kube, args.namespace, args.name, True, timeout=args.timeout)


# ---------------------------------------------------------------------------
# v1.60.0 : modifier ou créer un objet par son YAML (« Edit YAML » de Harvester)
# ---------------------------------------------------------------------------

def _load_doc(kube, path):
    """Le fichier (YAML ou JSON) en objet. JSON d'abord ; puis PyYAML s'il est
    là ; sinon kubectl lui-même fait la conversion (hôte airgap sans PyYAML)."""
    import json
    raw = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(raw)
    except ValueError:
        pass
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            docs = [d for d in yaml.safe_load_all(raw) if d is not None]
        except yaml.YAMLError as e:
            raise ValueError(f"not valid YAML: {str(e).splitlines()[0]}") from None
        if len(docs) != 1:
            raise ValueError("one object at a time: the file holds " + str(len(docs)))
        return docs[0]
    out = kube.run("create", "--dry-run=client", "-o", "json", "-f", "-", input=raw)
    return json.loads(out)


def cmd_yaml(args):
    kube = kube_from(args)
    s = hy.spec_of(args.kind)
    obj = _load_doc(kube, args.file)
    creating = bool(args.create)
    if not creating and not args.name:
        raise ValueError("--name is required to replace an object (or give --create)")
    if s["namespaced"] and not creating and not args.namespace:
        raise ValueError("--namespace is required")
    obj = hy.check_target(args.kind, obj, args.namespace, args.name, creating=creating)
    meta = obj["metadata"]
    ref = f"{meta.get('namespace') + '/' if meta.get('namespace') else ''}{meta.get('name') or meta.get('generateName', '') + '…'}"
    verb = "create" if creating else "replace"
    step("check", "running", f"Harvester checks the {s['kind']} {ref}")
    import json
    body = json.dumps(obj)

    def send(*extra):
        # Le resourceVersion du texte garde contre l'écrasement : un conflit
        # peut sortir dès l'essai à blanc (vu sur harv1), il est dit en clair.
        try:
            return kube.run(verb, *extra, "-f", "-", "-o", "json", input=body)
        except KubeError as e:
            if "the object has been modified" in str(e):
                raise ValueError("someone changed this object since it was opened: "
                                 "reload it and apply your change again") from None
            raise
    send("--dry-run=server")
    step("check", "done", "accepted by the cluster's checks")
    if args.dry_run:
        return EXIT_OK
    step(verb, "running", f"{'create' if creating else 'save'} {s['kind']} {ref}")
    made = json.loads(send())
    step(verb, "done", f"{ref} {'created' if creating else 'saved'} "
                       f"(version {(made.get('metadata') or {}).get('resourceVersion', '?')})")
    return EXIT_OK


# ---------------------------------------------------------------------------
# v1.60.0 : les gestes sur une VM du menu de Harvester
# ---------------------------------------------------------------------------
K_VM = "virtualmachines.kubevirt.io"
K_VMI = "virtualmachineinstances.kubevirt.io"
K_VMIM = "virtualmachineinstancemigrations.kubevirt.io"
K_TPL = "virtualmachinetemplates.harvesterhci.io"
K_TPLV = "virtualmachinetemplateversions.harvesterhci.io"


def _put_sub(kube, path, body=None):
    import json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body or {}, f)
        tmp = f.name
    try:
        kube.run("replace", "--raw", path, "-f", tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _vmi_state(kube, ns, name):
    vmi = kube.get(K_VMI, ns, name)
    if vmi is None:
        return None, set()
    conds = {c.get("type") for c in (vmi.get("status") or {}).get("conditions") or []
             if str(c.get("status")) == "True"}
    return vmi, conds


def vm_pause(kube, args, pause=True):
    ns, name = args.namespace, args.name
    vmi, conds = _vmi_state(kube, ns, name)
    if vmi is None:
        raise ValueError(f"{ns}/{name} is not running")
    if pause and "Paused" in conds:
        step("pause", "done", f"{ns}/{name} is already paused")
        return EXIT_OK
    if not pause and "Paused" not in conds:
        step("unpause", "done", f"{ns}/{name} is not paused")
        return EXIT_OK
    verb = "pause" if pause else "unpause"
    step(verb, "running", f"{ns}/{name}")
    _put_sub(kube, hv.subresource(ns, name, verb))

    def settled(obj):
        c = {x.get("type") for x in ((obj or {}).get("status") or {}).get("conditions") or []
             if str(x.get("status")) == "True"}
        if ("Paused" in c) == pause:
            return True, f"{ns}/{name} {'paused' if pause else 'running again'}"
        return None, "waiting for KubeVirt"
    return _wait(kube, K_VMI, ns, name, settled, 120, label=verb)


def vm_softreboot(kube, args):
    ns, name = args.namespace, args.name
    vmi, conds = _vmi_state(kube, ns, name)
    if vmi is None:
        raise ValueError(f"{ns}/{name} is not running")
    if "AgentConnected" not in conds:
        raise ValueError("a soft reboot goes through the guest agent, which is not connected: "
                         "install qemu-guest-agent in the VM, or use Restart")
    step("softreboot", "running", f"{ns}/{name}: the guest reboots itself")
    _put_sub(kube, hv.subresource(ns, name, "softreboot"))
    step("softreboot", "done", "reboot requested to the guest")
    return EXIT_OK


def vm_restart(kube, args):
    ns, name = args.namespace, args.name
    if kube.get(K_VMI, ns, name) is None:
        raise ValueError(f"{ns}/{name} is not running")
    step("restart", "running", f"{ns}/{name}: stop within the grace period, then start")
    _put_sub(kube, hv.subresource(ns, name, "restart", vmi=False))
    step("restart", "done", "restart requested")
    return EXIT_OK


def vm_force_stop(kube, args):
    """Arrêt immédiat, comme « Force Stop » : la VM passe à l'arrêt voulu,
    puis son instance est supprimée sans délai de grâce."""
    ns, name = args.namespace, args.name
    if kube.get(K_VM, ns, name) is None:
        raise ValueError(f"no VM {ns}/{name}")
    step("stop", "running", f"{ns}/{name}: run strategy Halted")
    kube.patch(K_VM, ns, name, {"spec": {"runStrategy": "Halted"}})
    if kube.get(K_VMI, ns, name) is not None:
        step("stop", "running", "the instance is stopped without waiting for the guest")
        kube.run("delete", K_VMI, name, "-n", ns, "--grace-period=0", "--force", "--wait=false")
    return _wait(kube, K_VMI, ns, name, lambda o: (True, f"{ns}/{name} stopped") if o is None else (None, "stopping"),
                 180, label="stop")


def vm_delete(kube, args):
    ns, name = args.namespace, args.name
    vm = kube.get(K_VM, ns, name)
    if vm is None:
        raise ValueError(f"no VM {ns}/{name}")
    remove = [r.strip() for r in (args.remove_volumes or "").split(",") if r.strip()]
    ann, remove = hv.delete_plan(vm, remove)
    # les Secrets cloud-init qu'aucune autre VM n'utilise partent avec elle
    secrets = []
    if args.remove_cloudinit:
        others = [v for v in kube.list(K_VM, ns) if (v.get("metadata") or {}).get("name") != name]
        used = {s for v in others for s in hv.cloudinit_secrets(v)}
        secrets = [s for s in hv.cloudinit_secrets(vm) if s not in used]
    if remove:
        step("volumes", "running", f"deleted with the VM: {', '.join(remove)}")
        kube.patch(K_VM, ns, name, {"metadata": {"annotations": ann}})
    kept = [x["claim"] for x in hv.vm_volumes(vm) if x["claim"] and x["claim"] not in remove]
    if kept:
        step("volumes", "done", f"kept: {', '.join(kept)}")
    step("delete", "running", f"VM {ns}/{name}")
    kube.delete(K_VM, ns, name)
    code = _wait(kube, K_VM, ns, name, lambda o: (True, f"{ns}/{name} deleted") if o is None else (None, "deleting"),
                 args.timeout, label="delete")
    if code != EXIT_OK:
        return code
    # Harvester supprime les volumes annotés ; la console s'en assure
    for pvc in remove:
        if kube.get("persistentvolumeclaims", ns, pvc) is not None:
            try:
                kube.delete("persistentvolumeclaims", ns, pvc)
            except KubeError:
                pass
        _wait(kube, "persistentvolumeclaims", ns, pvc,
              lambda o, p=pvc: (True, f"volume {p} deleted") if o is None else (None, "deleting volume"),
              300, label="volumes")
    for s in secrets:
        try:
            kube.delete("secrets", ns, s)
            step("cloudinit", "done", f"cloud-init secret {s} deleted")
        except KubeError:
            pass
    return EXIT_OK


def vm_clone(kube, args):
    ns, name = args.namespace, args.name
    new = hv.check_name(args.new_name, "new name")
    vm = kube.get(K_VM, ns, name)
    if vm is None:
        raise ValueError(f"no VM {ns}/{name}")
    if kube.get(K_VM, ns, new) is not None:
        raise ValueError(f"a VM {ns}/{new} already exists")
    pvcs = {x["claim"]: kube.get("persistentvolumeclaims", ns, x["claim"]) or {}
            for x in hv.vm_volumes(vm) if x["claim"]}
    out, renames = hv.clone_manifest(vm, new, with_data=args.with_data, pvcs=pvcs, start=args.start)
    made_secrets = []
    for old in hv.cloudinit_secrets(vm):
        src = kube.get("secrets", ns, old)
        if src is None:
            continue
        copy_ = hv.cloudinit_copy(src, new)
        hv.rename_secret_refs(out, old, copy_["metadata"]["name"])
        made_secrets.append(copy_)
    what = "with its data" if args.with_data else "without its data (volumes start again from their image, or empty)"
    step("check", "running", f"{ns}/{name} to {ns}/{new}, {what}")
    import json
    kube.run("create", "--dry-run=server", "-f", "-", "-o", "name", input=json.dumps(out))
    step("check", "done", ", ".join(f"{a} to {b}" for a, b in renames.items()) or "no volume to copy")
    for s in made_secrets:
        kube.create(s)
        step("cloudinit", "done", f"cloud-init copied to {s['metadata']['name']}")
    step("clone", "running", f"VM {ns}/{new}")
    kube.create(out)
    step("clone", "done", f"{ns}/{new} created; Harvester creates its volumes")

    def ready(obj):
        missing = [c for c in renames.values()
                   if ((kube.get("persistentvolumeclaims", ns, c) or {}).get("status") or {}).get("phase") != "Bound"]
        if not missing:
            return True, f"{len(renames)} volume(s) ready"
        return None, f"waiting for {', '.join(missing)}"
    if renames:
        return _wait(kube, K_VM, ns, new, ready, args.timeout, label="volumes")
    return EXIT_OK


def vm_eject(kube, args):
    ns, name = args.namespace, args.name
    vm = kube.get(K_VM, ns, name)
    if vm is None:
        raise ValueError(f"no VM {ns}/{name}")
    out, claim = hv.eject_patch(vm, args.volume)
    ts = (out.get("spec") or {}).get("template", {}).get("spec", {})
    patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": vm["metadata"]["resourceVersion"]},
             {"op": "replace", "path": "/spec/template/spec/domain/devices/disks",
              "value": ts.get("domain", {}).get("devices", {}).get("disks", [])},
             {"op": "replace", "path": "/spec/template/spec/volumes", "value": ts.get("volumes", [])}]
    ann = (out.get("metadata") or {}).get("annotations") or {}
    if hv.VCT in ann:
        patch.append({"op": "replace", "path": "/metadata/annotations/" + hv.VCT.replace("/", "~1"),
                      "value": ann[hv.VCT]})
    elif hv.VCT in ((vm.get("metadata") or {}).get("annotations") or {}):
        patch.append({"op": "remove", "path": "/metadata/annotations/" + hv.VCT.replace("/", "~1")})
    import json
    step("eject", "running", f"{args.volume} out of {ns}/{name}")
    kube.run("patch", K_VM, name, "-n", ns, "--type", "json", "-p", json.dumps(patch))
    running = kube.get(K_VMI, ns, name) is not None
    step("eject", "done", "ejected" + ("; the running VM sees it after a restart" if running else ""))
    if claim and args.delete_volume:
        if running:
            step("volume", "done", f"the CD-ROM volume {claim} is kept until the VM restarts")
        else:
            kube.delete("persistentvolumeclaims", ns, claim)
            step("volume", "done", f"volume {claim} deleted")
    return EXIT_OK


def vm_hotplug(kube, args, add=True):
    ns, name = args.namespace, args.name
    vm = kube.get(K_VM, ns, name)
    if vm is None:
        raise ValueError(f"no VM {ns}/{name}")
    if kube.get(K_VMI, ns, name) is None:
        raise ValueError(f"{ns}/{name} is stopped: add or remove the disk in its settings instead")
    vols = {x["volume"]: x for x in hv.vm_volumes(vm)}
    if add:
        claim = hv.check_name(args.claim, "volume")
        pvc = kube.get("persistentvolumeclaims", ns, claim)
        if pvc is None:
            raise ValueError(f"no volume {ns}/{claim}")
        if any(x["claim"] == claim for x in vols.values()):
            raise ValueError(f"{claim} is already a disk of this VM")
        vname = args.volume or claim
        if vname in vols:
            raise ValueError(f"this VM already has a disk named {vname}")
        body = hv.hotplug_body(vname, claim, args.bus)
        step("hotplug", "running", f"{claim} into {ns}/{name} as {vname} ({args.bus})")
        _put_sub(kube, hv.subresource(ns, name, "addvolume", vmi=False), body)
    else:
        vname = args.volume
        x = vols.get(vname)
        if not x:
            raise ValueError(f"no disk {vname} on this VM")
        if not x["hotpluggable"]:
            raise ValueError(f"{vname} was not plugged while running: remove it in the VM settings (cold)")
        step("hotplug", "running", f"{vname} out of {ns}/{name}")
        _put_sub(kube, hv.subresource(ns, name, "removevolume", vmi=False), {"name": vname})

    def settled(obj):
        st = {v.get("name"): v.get("phase") for v in ((obj or {}).get("status") or {}).get("volumeStatus") or []}
        if add and st.get(vname) == "Ready":
            return True, f"{vname} attached"
        if not add and vname not in st:
            return True, f"{vname} detached"
        return None, st.get(vname) or "pending"
    return _wait(kube, K_VMI, ns, name, settled, 300, label="hotplug")


def vm_migrate(kube, args):
    ns, name = args.namespace, args.name
    vmi = kube.get(K_VMI, ns, name)
    if vmi is None:
        raise ValueError(f"{ns}/{name} is not running: nothing to migrate")
    if hv.active_migrations(kube.list(K_VMIM, ns), name):
        raise ValueError(f"{ns}/{name} is already migrating")
    here = (vmi.get("status") or {}).get("nodeName")
    if not here:
        # vu sur harvlab : un nœud momentanément inconnu laissait choisir le
        # nœud même de la VM, et la migration restait à « Scheduling »
        raise ValueError(f"the node {ns}/{name} runs on is not known yet: try again in a moment")
    if args.node:
        if args.node == here:
            raise ValueError(f"{ns}/{name} already runs on {here}")
        node = kube.get("nodes", None, args.node)
        if node is None:
            raise ValueError(f"no node {args.node}")
        if (node.get("spec") or {}).get("unschedulable"):
            raise ValueError(f"{args.node} is cordoned or in maintenance")
    step("migrate", "running", f"{ns}/{name} from {here}" + (f" to {args.node}" if args.node else ""))
    made = kube.create(hv.migration_manifest(ns, name, args.node))
    mig = (made.get("metadata") or {}).get("name")

    def done(obj):
        phase = ((obj or {}).get("status") or {}).get("phase")
        if phase == "Succeeded":
            now = ((kube.get(K_VMI, ns, name) or {}).get("status") or {}).get("nodeName")
            return True, f"{ns}/{name} now runs on {now}"
        if phase == "Failed":
            return False, "the migration failed"
        if obj is None:
            return False, "the migration was cancelled"
        return None, phase or "pending"
    return _wait(kube, K_VMIM, ns, mig, done, args.timeout, label="migrate")


def vm_abort_migration(kube, args):
    ns, name = args.namespace, args.name
    active = hv.active_migrations(kube.list(K_VMIM, ns), name)
    if not active:
        raise ValueError(f"{ns}/{name} is not migrating")
    for m in active:
        mig = m["metadata"]["name"]
        step("abort", "running", f"migration {mig}")
        kube.delete(K_VMIM, ns, mig)
    step("abort", "done", f"{ns}/{name} stays where it runs")
    return EXIT_OK


def _images_from_volumes(kube, ns, vm, label):
    """Exporte chaque volume de la VM en image et attend leur import."""
    images = {}
    for x in hv.vm_volumes(vm):
        if not x["claim"] or x["kind"] == "cdrom":
            continue
        pvc = kube.get("persistentvolumeclaims", ns, x["claim"]) or {}
        sc = (pvc.get("spec") or {}).get("storageClassName")
        step("export", "running", f"{x['claim']} to an image")
        img = kube.create(hv.export_image_manifest(ns, x["claim"], f"{label}-{x['volume']}", None if (sc or "").startswith(("lh-", "longhorn-")) else sc))
        images[x["claim"]] = img
    for claim, img in list(images.items()):
        iname = img["metadata"]["name"]

        def imported(obj):
            st = (obj or {}).get("status") or {}
            for c in st.get("conditions") or []:
                if c.get("type") == "RetryLimitExceeded" and str(c.get("status")) == "True":
                    return False, c.get("message") or "export failed"
            if st.get("progress") == 100 and any(c.get("type") == "Imported" and str(c.get("status")) == "True"
                                                  for c in st.get("conditions") or []):
                return True, f"{claim} exported to {iname}"
            return None, f"{claim}: {st.get('progress') or 0} %"
        code = _wait(kube, "virtualmachineimages.harvesterhci.io", ns, iname, imported, 7200, label="export")
        if code != EXIT_OK:
            raise RuntimeError(f"export of {claim} failed")
        images[claim] = kube.get("virtualmachineimages.harvesterhci.io", ns, iname)
    return images


def vm_template(kube, args):
    ns, name = args.namespace, args.name
    vm = kube.get(K_VM, ns, name)
    if vm is None:
        raise ValueError(f"no VM {ns}/{name}")
    tname = hv.check_name(args.template_name, "template name")
    existing = kube.get(K_TPL, ns, tname)
    images = _images_from_volumes(kube, ns, vm, tname) if args.with_data else None
    tmpl, version = hv.template_objects(vm, tname, args.description or "", images=images)
    import json
    if existing is None:
        step("template", "running", f"template {ns}/{tname}")
        kube.create(tmpl)
    else:
        step("template", "running", f"new version of the template {ns}/{tname}")
    made = kube.create(version)
    vname = made["metadata"]["name"]
    if existing is None or args.set_default:
        kube.patch(K_TPL, ns, tname, {"spec": {"defaultVersionId": f"{ns}/{vname}"}})

    def numbered(obj):
        v = ((obj or {}).get("status") or {}).get("version")
        return (True, f"{ns}/{tname} version {v} ({vname})") if v else (None, "Harvester numbers the version")
    return _wait(kube, K_TPLV, ns, vname, numbered, 120, label="template")


def vm_cloudinit(kube, args):
    ns, name = args.namespace, args.name
    vm = kube.get(K_VM, ns, name)
    if vm is None:
        raise ValueError(f"no VM {ns}/{name}")
    user = Path(args.user_data).read_text() if args.user_data else ""
    net = Path(args.network_data).read_text() if args.network_data else ""
    if args.guest_agent:
        user = hv.with_guest_agent(user)
    secrets = hv.cloudinit_secrets(vm)
    import base64
    import json
    data = {"userdata": base64.b64encode(user.encode()).decode(),
            "networkdata": base64.b64encode(net.encode()).decode()}
    if secrets:
        step("cloudinit", "running", f"secret {secrets[0]}")
        kube.patch("secrets", ns, secrets[0], {"data": data})
        step("cloudinit", "done", "saved; the VM reads it at its next boot")
    else:
        sec = hv.cloudinit_secret(name, ns, user, net)
        step("cloudinit", "running", f"new secret {sec['metadata']['name']}")
        kube.create(sec)
        out = hv.attach_cloudinit(vm, sec["metadata"]["name"])
        ts = out["spec"]["template"]["spec"]
        patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": vm["metadata"]["resourceVersion"]},
                 {"op": "replace", "path": "/spec/template/spec/volumes", "value": ts["volumes"]},
                 {"op": "replace", "path": "/spec/template/spec/domain/devices/disks",
                  "value": ts["domain"]["devices"]["disks"]}]
        kube.run("patch", K_VM, name, "-n", ns, "--type", "json", "-p", json.dumps(patch))
        step("cloudinit", "done", "attached; the VM reads it at its next boot")
    if args.ssh_names is not None:
        names = [n for n in args.ssh_names.split(",") if n.strip()]
        kube.patch(K_VM, ns, name, {"metadata": {"annotations": hv.ssh_names_annotation(names)}})
    return EXIT_OK


VM_ACTIONS = {
    "pause": lambda k, a: vm_pause(k, a, True), "unpause": lambda k, a: vm_pause(k, a, False),
    "softreboot": vm_softreboot, "restart": vm_restart, "force-stop": vm_force_stop,
    "delete": vm_delete, "clone": vm_clone, "eject": vm_eject,
    "add-volume": lambda k, a: vm_hotplug(k, a, True), "remove-volume": lambda k, a: vm_hotplug(k, a, False),
    "migrate": vm_migrate, "abort-migration": vm_abort_migration, "template": vm_template,
    "cloudinit": vm_cloudinit,
}


def cmd_vm(args):
    kube = kube_from(args)
    hv.check_name(args.name, "VM name")
    return VM_ACTIONS[args.action](kube, args)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harvester-resources", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("addon", help="enable or disable a Harvester add-on")
    sp.set_defaults(fn=cmd_addon)
    sp.add_argument("--cluster", help="cluster name in the configuration")
    sp.add_argument("--kubeconfig", help="kubeconfig of the Harvester cluster")
    sp.add_argument("--namespace", required=True)
    sp.add_argument("--name", required=True)
    grp = sp.add_mutually_exclusive_group(required=True)
    grp.add_argument("--enable", dest="enable", action="store_true")
    grp.add_argument("--disable", dest="enable", action="store_false")
    sp.add_argument("--timeout", type=int, default=900, help="seconds to wait for Harvester")

    def common(p):
        p.add_argument("--cluster", help="cluster name in the configuration")
        p.add_argument("--kubeconfig", help="kubeconfig of the Harvester cluster")
        p.add_argument("--namespace", required=True)
        p.add_argument("--timeout", type=int, default=3600, help="seconds to wait for Harvester")

    sp = sub.add_parser("backup", help="VM backups and snapshots: create, restore, delete")
    sp.set_defaults(fn=cmd_backup)
    sp.add_argument("action", choices=("create", "restore", "delete"))
    common(sp)
    sp.add_argument("--name")
    sp.add_argument("--vm")
    sp.add_argument("--type", choices=("backup", "snapshot"), default="backup")
    sp.add_argument("--new-vm")
    sp.add_argument("--replace", action="store_true", help="restore over the original VM (stopped)")
    sp.add_argument("--keep-mac", action="store_true")
    sp.add_argument("--halt", action="store_true", help="leave the restored VM stopped")
    sp.add_argument("--delete-policy", choices=("retain", "delete"), default="retain")

    sp = sub.add_parser("schedule", help="scheduled VM backups or snapshots")
    sp.set_defaults(fn=cmd_schedule)
    sp.add_argument("action", choices=("create", "suspend", "resume", "delete"))
    common(sp)
    sp.add_argument("--name", required=True)
    sp.add_argument("--vm")
    sp.add_argument("--cron")
    sp.add_argument("--retain", default=7)
    sp.add_argument("--max-failure", default=3)
    sp.add_argument("--type", choices=("backup", "snapshot"), default="backup")

    sp = sub.add_parser("volsnap", help="volume snapshots: restore into a new volume, delete")
    sp.set_defaults(fn=cmd_volsnap)
    sp.add_argument("action", choices=("restore", "delete"))
    common(sp)
    sp.add_argument("--name", required=True)
    sp.add_argument("--new-volume")
    sp.add_argument("--storage-class")
    sp.add_argument("--size")

    sp = sub.add_parser("create", help="create an image, storage class, SSH key, secret, VM network or volume")
    sp.set_defaults(fn=cmd_create)
    sp.add_argument("--cluster")
    sp.add_argument("--kubeconfig")
    sp.add_argument("--kind", choices=ho.KINDS, required=True)
    sp.add_argument("--spec", required=True, help="JSON request, '-' for stdin")
    sp.add_argument("--timeout", type=int, default=3600)

    sp = sub.add_parser("delete", help="delete one of those objects if nothing uses it")
    sp.set_defaults(fn=cmd_delete)
    sp.add_argument("--cluster")
    sp.add_argument("--kubeconfig")
    sp.add_argument("--kind", choices=ho.KINDS, required=True)
    sp.add_argument("--namespace")
    sp.add_argument("--name", required=True)
    sp.add_argument("--timeout", type=int, default=600)

    sp = sub.add_parser("sc-default", help="make a storage class the default one")
    sp.set_defaults(fn=cmd_sc_default)
    sp.add_argument("--cluster")
    sp.add_argument("--kubeconfig")
    sp.add_argument("--name", required=True)

    sp = sub.add_parser("volume-expand", help="grow a volume")
    sp.set_defaults(fn=cmd_volume_expand)
    sp.add_argument("--cluster")
    sp.add_argument("--kubeconfig")
    sp.add_argument("--namespace", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--size", required=True)
    sp.add_argument("--timeout", type=int, default=600)

    sp = sub.add_parser("addon-values", help="change the configuration (values) of an add-on")
    sp.set_defaults(fn=cmd_addon_values)
    sp.add_argument("--cluster")
    sp.add_argument("--kubeconfig")
    sp.add_argument("--namespace", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--values", required=True, help="YAML file, '-' for stdin")
    sp.add_argument("--timeout", type=int, default=900)

    sp = sub.add_parser("yaml", help="replace an object by its edited YAML, or create one from YAML")
    sp.set_defaults(fn=cmd_yaml)
    sp.add_argument("--cluster")
    sp.add_argument("--kubeconfig")
    sp.add_argument("--kind", choices=sorted(hy.KINDS), required=True)
    sp.add_argument("--namespace")
    sp.add_argument("--name", help="the object replaced (not with --create)")
    sp.add_argument("--file", required=True, help="YAML or JSON file, '-' for stdin")
    sp.add_argument("--create", action="store_true", help="create a new object instead of replacing one")
    sp.add_argument("--dry-run", action="store_true", help="only let the cluster check it")

    sp = sub.add_parser("vm", help="the VM actions of Harvester's menu")
    sp.set_defaults(fn=cmd_vm)
    sp.add_argument("action", choices=sorted(VM_ACTIONS))
    sp.add_argument("--cluster")
    sp.add_argument("--kubeconfig")
    sp.add_argument("--namespace", required=True)
    sp.add_argument("--name", required=True, help="the VM")
    sp.add_argument("--remove-volumes", help="delete: volumes (PVC names) deleted with the VM, comma separated")
    sp.add_argument("--keep-cloudinit", dest="remove_cloudinit", action="store_false",
                    help="delete: keep the cloud-init secrets")
    sp.add_argument("--new-name", help="clone: name of the copy")
    sp.add_argument("--with-data", action="store_true", help="clone/template: copy the volumes' data")
    sp.add_argument("--start", action="store_true", help="clone: start the copy")
    sp.add_argument("--volume", help="eject/add-volume/remove-volume: the disk name in the VM")
    sp.add_argument("--delete-volume", action="store_true", help="eject: delete the CD-ROM volume")
    sp.add_argument("--claim", help="add-volume: the volume (PVC) to plug")
    sp.add_argument("--bus", default="scsi", help="add-volume: scsi (default), virtio or sata")
    sp.add_argument("--node", help="migrate: the target node (any node by default)")
    sp.add_argument("--template-name", help="template: name of the template (a new version if it exists)")
    sp.add_argument("--description")
    sp.add_argument("--set-default", action="store_true", help="template: make the new version the default")
    sp.add_argument("--user-data", help="cloudinit: file with the user-data")
    sp.add_argument("--network-data", help="cloudinit: file with the network-data")
    sp.add_argument("--guest-agent", action="store_true", help="cloudinit: install qemu-guest-agent")
    sp.add_argument("--ssh-names", help="cloudinit: SSH key pairs shown on the VM, comma separated")
    sp.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)
    signal.signal(signal.SIGTERM, _on_signal)
    try:
        return args.fn(args)
    except ValueError as e:
        step("check", "error", str(e))
        return EXIT_BLOCKED
    except (KubeError, RuntimeError) as e:
        step(args.cmd, "error", refusal(e)[:300])
        return EXIT_FAIL
    except Cancelled:
        return EXIT_CANCELLED


if __name__ == "__main__":
    sys.exit(main())
