"""harvester-ops : les imports de VM de Harvester (v1.71.0).

L'add-on vm-import-controller (migration.harvesterhci.io/v1beta1) importe une
VM depuis VMware (vCenter), OpenStack ou une archive OVA : une « source » par
fournisseur, puis un VirtualMachineImport par VM. Lu dans le code du contrôleur
v1.9.0 et vu en réel sur harv1 :

- aucun webhook ne garde ces objets : ce que l'interface de Harvester refuse,
  la console le refuse aussi avant d'écrire ;
- une source n'a pas de message d'erreur : prête (clusterReady), pas prête
  (clusterNotReady), ou sans état quand son secret manque ; la cause n'est
  que dans le journal du contrôleur ;
- une source prête n'est jamais revérifiée ;
- trois orthographes de la clé du certificat selon le type ;
- une carte de la source sans correspondance est supprimée, et la carte du
  réseau des pods n'est ajoutée que si AUCUNE ne l'est ;
- les images s'appellent « vm-import-<import>-<disque> », 63 caractères au
  plus : au-delà, l'import boucle sans fin ;
- la progression réelle est celle des images créées, pas celle de l'import.
"""

import re

G = "migration.harvesterhci.io"
API = f"{G}/v1beta1"
K_IMPORT = f"virtualmachineimports.{G}"
TYPES = ("vmware", "openstack", "ova")
K_SRC = {"vmware": f"vmwaresources.{G}", "openstack": f"openstacksources.{G}", "ova": f"ovasources.{G}"}
KIND = {"vmware": "VmwareSource", "openstack": "OpenstackSource", "ova": "OvaSource"}
TYPE_OF_KIND = {v.lower(): k for k, v in KIND.items()}
ADDON = ("harvester-system", "vm-import-controller")
CTRL_NS, CTRL_DEPLOY = "harvester-system", "harvester-vm-import-controller"
L_IMPORTED = f"{G}/imported"
K_IMAGE = "virtualmachineimages.harvesterhci.io"

# les clés du secret d'une source, lues par le contrôleur
SECRET_KEYS = {"vmware": ("username", "password"),
               "openstack": ("username", "password", "project_name", "domain_name"),
               "ova": ("username", "password")}
CA_KEY = {"vmware": "caCert", "openstack": "ca_cert", "ova": "ca.crt"}
NIC_MODELS = ("virtio", "e1000", "e1000e", "ne2k_pci", "pcnet", "rtl8139")
DISK_BUS = ("virtio", "scsi", "sata", "usb")

# la machine à états du contrôleur, dans l'ordre (valeurs exactes)
PHASES = ("", "virtualMachineImportValid", "sourceReady", "disksExported", "diskImageSubmitted",
          "diskImagesReady", "virtualMachineCreated", "virtualMachineRunning")
DONE = "virtualMachineRunning"
FAILED = ("virtualMachineImportInvalid", "VMMigrationFailed", "diskImageFailed")

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
URL_RE = re.compile(r"^https?://[^\s/]+", re.I)
IMAGE_NAME_MAX = 63


def _meta(o):
    return (o or {}).get("metadata") or {}


def check_name(name, what="name"):
    name = str(name or "").strip()
    if not NAME_RE.match(name):
        raise ValueError(f"{what}: lowercase letters, digits and '-', 63 characters at most")
    return name


def addon_state(addons):
    for a in addons or []:
        m = _meta(a)
        if (m.get("namespace"), m.get("name")) == ADDON:
            return {"enabled": bool((a.get("spec") or {}).get("enabled")), "status": (a.get("status") or {}).get("status") or ""}
    return {"enabled": False, "status": "absent"}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _int(v, what, lo=0):
    if v in (None, ""):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: a whole number") from None
    if n < lo:
        raise ValueError(f"{what}: {lo} or more")
    return n


def source_manifest(spec):
    """{type, namespace, name, endpoint, dc, region, url, http_timeout,
    retry_count, retry_delay, credentials: {mode: new|existing|none, secret,
    values {username, password, project_name, domain_name, ca}}} ->
    (source, secret) ; secret = {name, data {clé: valeur}} à écrire, ou None.
    Les valeurs ne vont jamais dans la source, seulement sa référence."""
    t = spec.get("type")
    if t not in TYPES:
        raise ValueError("type: vmware, openstack or ova")
    ns, name = check_name(spec.get("namespace") or "default", "namespace"), check_name(spec.get("name"), "source name")
    body = {}
    if t in ("vmware", "openstack"):
        ep = str(spec.get("endpoint") or "").strip()
        if not URL_RE.match(ep):
            raise ValueError("endpoint: an http or https URL" + (" (the vCenter SDK, https://vcenter/sdk)" if t == "vmware"
                                                                  else " (Keystone, https://openstack/identity)"))
        body["endpoint"] = ep
    if t == "vmware":
        dc = str(spec.get("dc") or "").strip()
        if not dc:
            raise ValueError("datacenter: the exact name of the vCenter datacenter")
        body["dc"] = dc
    if t == "openstack":
        region = str(spec.get("region") or "").strip()
        if not region:
            raise ValueError("region: the OpenStack region")
        body["region"] = region
        for f, key in (("retry_count", "uploadImageRetryCount"), ("retry_delay", "uploadImageRetryDelay")):
            n = _int(spec.get(f), f, 1)
            if n is not None:
                body[key] = n
    if t == "ova":
        url = str(spec.get("url") or "").strip()
        if not URL_RE.match(url):
            raise ValueError("url: the OVA archive, over http or https")
        body["url"] = url
        n = _int(spec.get("http_timeout"), "download timeout", 0)
        if n is not None:
            body["httpTimeoutSeconds"] = n        # 0 : sans limite ; la limite couvre tout le téléchargement
    cred = spec.get("credentials") or {}
    mode = cred.get("mode") or ("none" if t == "ova" else "new")
    secret = None
    if mode == "none":
        if t != "ova":
            raise ValueError("credentials: required for " + KIND[t])
    elif mode == "existing":
        body["credentials"] = {"name": check_name(cred.get("secret"), "secret"), "namespace": ns}
    elif mode == "new":
        vals = {k: str(v) for k, v in (cred.get("values") or {}).items() if v not in (None, "")}
        need = SECRET_KEYS[t]
        if t == "ova":
            if not vals:
                raise ValueError("credentials: a user and password, a CA certificate, or both")
            if ("username" in vals) != ("password" in vals):
                raise ValueError("credentials: the user and the password go together")
        else:
            missing = [k for k in need if k not in vals]
            if missing:
                raise ValueError("credentials: " + ", ".join(missing) + " required")
        if "ca" in vals and "BEGIN CERTIFICATE" not in vals["ca"]:
            raise ValueError("CA certificate: PEM text (-----BEGIN CERTIFICATE-----)")
        data = {k: vals[k] for k in need if k in vals}
        if "ca" in vals:
            data[CA_KEY[t]] = vals["ca"]
        secret = {"name": check_name(cred.get("secret") or f"{name}-creds", "secret"), "data": data}
        body["credentials"] = {"name": secret["name"], "namespace": ns}
    else:
        raise ValueError("credentials: new, existing or none")
    src = {"apiVersion": API, "kind": KIND[t], "metadata": {"name": name, "namespace": ns}, "spec": body}
    return src, secret


def source_state(o):
    st = ((o or {}).get("status") or {}).get("status")
    if st == "clusterReady":
        return "ready"
    if st == "clusterNotReady":
        return "notready"
    return "pending"          # jamais vérifiée : secret absent, identifiants refusés... (journal du contrôleur)


def source_rows(by_type):
    """by_type {vmware: [...], openstack: [...], ova: [...]} -> lignes."""
    out = []
    for t in TYPES:
        for o in by_type.get(t) or []:
            m, s = _meta(o), o.get("spec") or {}
            cred = s.get("credentials") or {}
            out.append({"type": t, "kind": KIND[t], "namespace": m.get("namespace"), "name": m.get("name"),
                        "endpoint": s.get("endpoint") or s.get("url") or "", "dc": s.get("dc") or "",
                        "region": s.get("region") or "", "http_timeout": s.get("httpTimeoutSeconds"),
                        "retry_count": s.get("uploadImageRetryCount"), "retry_delay": s.get("uploadImageRetryDelay"),
                        "secret": cred.get("name") or "", "secret_ns": cred.get("namespace") or "",
                        "state": source_state(o), "created": m.get("creationTimestamp")})
    return sorted(out, key=lambda r: (r["type"], r["namespace"], r["name"]))


def source_users(src, imports):
    """Les imports en cours qui s'appuient sur cette source."""
    out = []
    for i in imports or []:
        sc = (i.get("spec") or {}).get("sourceCluster") or {}
        if (TYPE_OF_KIND.get(str(sc.get("kind") or "").lower()) == src["type"] and sc.get("name") == src["name"]
                and (sc.get("namespace") or _meta(i).get("namespace")) == src["namespace"]
                and ((i.get("status") or {}).get("importStatus") or "") not in (DONE,) + FAILED):
            out.append(f"{_meta(i).get('namespace')}/{_meta(i).get('name')}")
    return out


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

def imported_name(t, vm_name):
    """Le nom de la VM créée : VMware et OVA passent le nom en minuscules ;
    OpenStack prend le nom du serveur (inconnu avant l'import)."""
    return vm_name.lower() if t in ("vmware", "ova") else None


def import_manifest(spec, sources=None, nads=None, classes=None):
    """{namespace, name, vm_name, source {type, namespace, name}, storage_class,
    networks [{source, destination, model}], default_nic_model,
    default_disk_bus, folder, force_power_off, graceful_timeout,
    skip_preflight}. `sources` (lignes), `nads` ("ns/nom"), `classes`
    (noms utilisables) : vérifiés quand ils sont donnés."""
    ns, name = check_name(spec.get("namespace") or "default", "namespace"), check_name(spec.get("name"), "import name")
    src = spec.get("source") or {}
    t = src.get("type")
    if t not in TYPES:
        raise ValueError("source: vmware, openstack or ova")
    sns, sname = check_name(src.get("namespace") or ns, "source namespace"), check_name(src.get("name"), "source")
    if sources is not None and not any((r["type"], r["namespace"], r["name"]) == (t, sns, sname) for r in sources):
        raise ValueError(f"source: no {KIND[t]} {sns}/{sname}")
    vm = str(spec.get("vm_name") or "").strip()
    if not vm:
        raise ValueError("VM: " + {"vmware": "its name in vCenter", "openstack": "the server's name or ID",
                                   "ova": "the name to give it"}[t])
    final = imported_name(t, vm)
    if final is not None and not NAME_RE.match(final):
        raise ValueError(f"VM: '{final}' is not a valid VM name in Harvester (lowercase letters, digits, '-')")
    if t == "ova" and len(f"vm-import-{name}-{final}-x.img") > IMAGE_NAME_MAX:
        # image vm-import-<import>-<vm>-<vmdk>.img : au-delà de 63, l'import boucle sans fin
        raise ValueError(f"names too long: the image of each disk is named vm-import-<import>-<vm>-<disk>.img, "
                         f"{IMAGE_NAME_MAX} characters at most; shorten the import or the VM name")
    body = {"virtualMachineName": vm,
            "sourceCluster": {"apiVersion": API, "kind": KIND[t], "name": sname, "namespace": sns},
            "skipPreflightChecks": bool(spec.get("skip_preflight"))}
    sc = str(spec.get("storage_class") or "").strip()
    if sc:
        if classes is not None and sc not in classes:
            raise ValueError(f"storage class: no usable class {sc}")
        body["storageClass"] = sc
    maps, seen = [], set()
    for n in spec.get("networks") or []:
        s, d = str(n.get("source") or "").strip(), str(n.get("destination") or "").strip()
        if not s and not d:
            continue
        if not s or not d:
            raise ValueError("network: a source network and its destination")
        if s in seen:
            raise ValueError(f"network: source network {s} appears twice")
        seen.add(s)
        full = d if "/" in d else f"{ns}/{d}"
        if nads is not None and full not in nads:
            raise ValueError(f"network: no VM network {full}")
        row = {"sourceNetwork": s, "destinationNetwork": full}
        model = n.get("model") or ""
        if model:
            if model not in NIC_MODELS:
                raise ValueError("network card model: " + ", ".join(NIC_MODELS))
            row["networkInterfaceModel"] = model
        maps.append(row)
    if maps:
        body["networkMapping"] = maps
    for f, key, allowed in (("default_nic_model", "defaultNetworkInterfaceModel", NIC_MODELS),
                            ("default_disk_bus", "defaultDiskBusType", DISK_BUS)):
        v = spec.get(f) or ""
        if v:
            if v not in allowed:
                raise ValueError(f"{f.replace('_', ' ')}: " + ", ".join(allowed))
            body[key] = v
    if t == "vmware":
        if str(spec.get("folder") or "").strip():
            body["folder"] = str(spec["folder"]).strip()
        if spec.get("force_power_off"):
            body["forcePowerOff"] = True
        n = _int(spec.get("graceful_timeout"), "guest shutdown timeout", 1)
        if n is not None:
            body["gracefulShutdownTimeoutSeconds"] = n
    elif spec.get("folder") or spec.get("force_power_off") or spec.get("graceful_timeout"):
        raise ValueError("folder, forced power off and guest shutdown timeout are for VMware sources")
    return {"apiVersion": API, "kind": "VirtualMachineImport", "metadata": {"name": name, "namespace": ns}, "spec": body}


def _cond_msg(conds, *types):
    for c in reversed(conds or []):
        if c.get("type") in types and c.get("message"):
            return c["message"]
    return ""


def import_view(o, images=None):
    """L'état d'un import : phase (index dans PHASES), fini, échec et sa
    raison quand le statut la porte, disques avec la progression de leur
    image, VM créée."""
    m, s, st = _meta(o), (o or {}).get("spec") or {}, (o or {}).get("status") or {}
    phase = st.get("importStatus") or ""
    imgs = {(_meta(i).get("namespace"), _meta(i).get("name")): i for i in images or []}
    disks = []
    for d in st.get("diskImportStatus") or []:
        iname = d.get("VirtualMachineImage") or ""
        img = imgs.get((m.get("namespace"), iname)) if iname else None
        ist = (img or {}).get("status") or {}
        ok = any(c.get("type") == "Imported" and c.get("status") == "True" for c in ist.get("conditions") or [])
        failed = next((c.get("message") or c.get("reason") for c in ist.get("conditions") or []
                       if c.get("type") == "Imported" and c.get("status") == "False" and c.get("reason") == "ImportFailed"), "")
        disks.append({"name": d.get("diskName"), "size": d.get("diskSize"), "bus": d.get("busType") or "",
                      "image": iname, "progress": 100 if ok else ist.get("progress"), "failed": failed or ""})
    sc = s.get("sourceCluster") or {}
    t = TYPE_OF_KIND.get(str(sc.get("kind") or "").lower(), "")
    failed = phase in FAILED
    reason = _cond_msg(st.get("importConditions"), "VMExportFailed") if failed else ""
    step = PHASES.index(phase) if phase in PHASES else (2 if phase == "diskImageFailed" else 0)
    return {"namespace": m.get("namespace"), "name": m.get("name"), "type": t, "source": sc.get("name") or "",
            "source_ns": sc.get("namespace") or m.get("namespace"), "vm_name": s.get("virtualMachineName") or "",
            "vm": st.get("importedVirtualMachineName") or "", "phase": phase, "step": step, "steps": len(PHASES) - 1,
            "done": phase == DONE, "failed": failed, "reason": reason, "disks": disks,
            "networks": [{"source": n.get("sourceNetwork"), "destination": n.get("destinationNetwork"),
                          "model": n.get("networkInterfaceModel") or ""} for n in s.get("networkMapping") or []],
            "storage_class": s.get("storageClass") or "", "conditions": [
                {"type": c.get("type"), "status": c.get("status"), "message": c.get("message") or "",
                 "at": c.get("lastTransitionTime") or c.get("lastUpdateTime")} for c in st.get("importConditions") or []],
            "created": m.get("creationTimestamp")}


def settled(o, images=None, stuck_reason=""):
    """(True, msg) VM en marche, (False, msg) échec, (None, msg) en cours."""
    if o is None:
        return False, "the import disappeared"
    v = import_view(o, images)
    if v["done"]:
        return True, f"VM {v['vm']} imported and running"
    if v["failed"]:
        return False, f"{v['phase']}: {v['reason'] or stuck_reason or 'see the controller log'}"
    if stuck_reason:
        return False, stuck_reason
    prog = [d["progress"] for d in v["disks"] if d["progress"] is not None]
    extra = f" ({min(prog)} %)" if prog and v["phase"] == "diskImageSubmitted" else ""
    return None, (v["phase"] or "checking the source and the request") + extra


# Lignes du journal du contrôleur qui disent pourquoi un import ou une
# source n'avance pas (le statut ne le porte pas).
_ERR_RE = re.compile(r'level=(error|warning)|"level":"(error|warning)"|error|failed|invalid|not found|cannot', re.I)
_STUCK = ("not a valid Kubernetes label value", "is invalid", "denied the request", "already exists")


def log_lines(text, name, limit=12):
    """Les dernières lignes d'erreur du journal qui citent `name`."""
    out = [ln.strip() for ln in (text or "").splitlines() if name in ln and _ERR_RE.search(ln)]
    return out[-limit:]


def log_error(line):
    """La raison d'une ligne du journal : son err="...", sinon son msg."""
    m = re.search(r'err="((?:[^"\\]|\\.)*)"', line or "") or re.search(r'msg="((?:[^"\\]|\\.)*)"', line or "")
    return (m.group(1) if m else (line or "")).replace('\\"', '"').strip()[:300]


def stuck_reason(lines):
    """Un refus qui fait boucler l'import sans changer son état (image au nom
    trop long, VM de même nom non importée...)."""
    for ln in reversed(lines or []):
        if any(k in ln for k in _STUCK):
            m = re.search(r'(?:err|error)="?([^"]+)"?', ln)
            return (m.group(1) if m else ln)[:300]
    return ""
