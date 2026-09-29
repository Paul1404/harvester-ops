#!/usr/bin/env python3
"""Forklift sur un cluster Harvester : installation, image VDDK, fournisseur vCenter, inventaire.

    harvester-forklift status          --cluster C
    harvester-forklift install         --cluster C [--chart-version V] [--image-tag T]
                                       [--cert-manager-manifest FICHIER | --cert-manager-from-bundle PAQUET]
    harvester-forklift vddk-image      --archive VDDK.tar.gz --image REGISTRE/DEPOT:TAG
                                       [--base IMAGE] [--plain-http] [--auth-stdin | --spec FICHIER]
                                       [--cluster C | --kubeconfig K]
    harvester-forklift provider-apply  --cluster C --namespace NS --name N   (JSON sur stdin ou --spec FICHIER)
    harvester-forklift provider-delete --cluster C --namespace NS --name N [--with-secret]
    harvester-forklift inventory       --cluster C --namespace NS --name N --kind vms|networks|datastores

Harvester 1.9 ne livre pas Forklift : `install` pose cert-manager (depuis le
manifeste que la console tire de son paquet Cluster API), l'add-on
expérimental forklift-operator puis le ForkliftController. Les secrets
(vCenter, registre) arrivent en JSON sur l'entrée standard ou dans un fichier
privé (`--spec FICHIER`, ce que fait la console), jamais en argument.
Progression sur stderr au format STEP_EVENT|<étape>|<statut>|<message>.
Codes : 0 succès, 1 échec, 2 refus. Bibliothèque standard seulement.
Voir docs/design/2026-09-27-migrations-vmware.md.
"""

import argparse
import json
import ssl
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import hv_forklift as hf  # noqa: E402
import oci_push as op  # noqa: E402
from kube import Kube, KubeError, cluster_config  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2


def step(sid, status, msg=""):
    clean = " ".join(str(msg).split())
    sys.stderr.write(f"STEP_EVENT|{sid}|{status}|{clean}\n")
    sys.stderr.flush()


def kube_from(args):
    entry = cluster_config(args.cluster) if args.cluster else None
    kc = args.kubeconfig or (entry or {}).get("kubeconfig")
    if not kc:
        raise ValueError("give --cluster (with a kubeconfig in the configuration) or --kubeconfig")
    return Kube(kc)


def get_opt(kube, kind, ns, name):
    """Un objet d'une CRD qui peut ne pas exister encore (avant l'add-on)."""
    try:
        return kube.get(kind, ns, name)
    except KubeError as e:
        if "doesn't have a resource type" in str(e):
            return None
        raise


def read_stdin_json():
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("stdin: JSON expected") from None
    if not isinstance(data, dict):
        raise ValueError("stdin: a JSON object expected")
    return data


def read_json_input(args):
    """La demande : le fichier privé donné par --spec (la console), sinon l'entrée standard."""
    path = getattr(args, "spec", None)
    if not path:
        return read_stdin_json()
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        raise ValueError("--spec: a readable JSON file expected") from None
    if not isinstance(data, dict):
        raise ValueError("--spec: a JSON object expected")
    return data


def bundle_cert_manager(bundle, into):
    """Le manifeste cert-manager du paquet Cluster API de la console, écrit dans `into`."""
    try:
        with tarfile.open(bundle, "r:gz") as tar:
            m = next((m for m in tar.getmembers() if m.isfile() and m.name.endswith(hf.CERT_MANAGER_MEMBER)), None)
            if m is None:
                raise ValueError("the Cluster API bundle has no cert-manager manifest")
            data = tar.extractfile(m).read()
    except (OSError, tarfile.TarError):
        raise ValueError("the Cluster API bundle cannot be read") from None
    out = Path(into) / "cert-manager.yaml"
    out.write_bytes(data)
    return str(out)


def until(fn, timeout, label, sleep=time.sleep, now=time.time, every=5):
    """Relit `fn()` -> (True|False|None, message) jusqu'à la fin ou le délai."""
    deadline, last = now() + timeout, None
    while True:
        res, msg = fn()
        if msg != last:
            step(label, "running" if res is None else ("done" if res else "error"), msg)
            last = msg
        if res is not None:
            return EXIT_OK if res else EXIT_FAIL
        if now() >= deadline:
            step(label, "error", f"not done after {timeout} s: {msg}")
            return EXIT_FAIL
        sleep(every)


def install_state(kube):
    by_name = lambda items: {(d.get("metadata") or {}).get("name"): d for d in items}  # noqa: E731
    return hf.install_state(hf.pick_addon(kube.list(hf.K_ADDON, None))[0],
                            by_name(kube.list(hf.K_DEPLOY, hf.NS)),
                            get_opt(kube, hf.K_CONTROLLER, hf.NS, hf.CONTROLLER_NAME),
                            by_name(kube.list(hf.K_DEPLOY, hf.CERT_MANAGER[0])))


def cmd_status(args, kube=None):
    kube = kube or kube_from(args)
    st = install_state(kube)
    providers = []
    if st["controller"]:
        for p in kube.list(hf.K_PROVIDER, None):
            m = p.get("metadata") or {}
            res, msg = hf.provider_state(p)
            providers.append({"namespace": m.get("namespace"), "name": m.get("name"),
                              "type": (p.get("spec") or {}).get("type"), "ready": res, "message": msg})
    vddk = hf.vddk_record(get_opt(kube, "configmaps", hf.NS, hf.VDDK_CM))
    print(json.dumps({"install": st, "providers": providers, "vddk": vddk}))
    return EXIT_OK


def cmd_install(args, kube=None, sleep=time.sleep, now=time.time):
    kube = kube or kube_from(args)
    want = hf.addon_manifest(args.chart_version, args.image_tag)      # refuse avant d'écrire
    st = install_state(kube)
    if st["cert_manager"]:
        step("cert-manager", "done", "cert-manager is running")
    else:
        manifest = args.cert_manager_manifest
        tmp = None
        if not manifest and getattr(args, "cert_manager_from_bundle", None):
            tmp = tempfile.TemporaryDirectory(prefix="hfk-cm-")
            manifest = bundle_cert_manager(args.cert_manager_from_bundle, tmp.name)
        if not manifest:
            step("cert-manager", "error", "cert-manager is missing: give --cert-manager-manifest "
                 "(the console takes it from its Cluster API bundle)")
            return EXIT_REFUSED
        try:
            step("cert-manager", "running", "installing cert-manager")
            kube.run("apply", "--server-side", "--force-conflicts", "-f", manifest, timeout=300)
        finally:
            if tmp:
                tmp.cleanup()

        def cm():
            s = install_state(kube)
            return (True, "cert-manager is running") if s["cert_manager"] else \
                (None, "waiting for " + ", ".join(s["cert_manager_missing"]))
        rc = until(cm, args.timeout, "cert-manager", sleep, now)
        if rc:
            return rc
    if kube.get("namespaces", None, hf.NS) is None:
        kube.create(hf.namespace_manifest())
    cur, theirs = hf.pick_addon(kube.list(hf.K_ADDON, None))
    if theirs:
        # celui de Harvester (1.9.1) : activé tel quel, jamais réécrit
        m = cur.get("metadata") or {}
        if not (cur.get("spec") or {}).get("enabled"):
            kube.patch(hf.K_ADDON, m.get("namespace"), m.get("name"), {"spec": {"enabled": True}})
            sleep(3)
        step("addon", "running", f"Harvester's own forklift-operator add-on ({m.get('namespace')}, "
             f"{(cur.get('spec') or {}).get('version')}): enabled, left as Harvester ships it")
    elif cur is None:
        kube.create(want)
        step("addon", "running", f"forklift-operator {args.chart_version} declared, images {args.image_tag}")
    elif any((cur.get("spec") or {}).get(k) != v for k, v in want["spec"].items()):
        kube.patch(hf.K_ADDON, hf.NS, hf.ADDON[1], {"spec": want["spec"]})
        step("addon", "running", f"forklift-operator set to {args.chart_version}, images {args.image_tag}")
        sleep(3)          # le contrôleur des add-ons prend la main : l'ancien statut n'est pas la fin

    def addon():
        s = install_state(kube)
        if s["addon"] == "failed":
            return False, s["addon_message"]
        if s["addon"] == "ready" and s["operator"]:
            return True, "forklift-operator is running"
        return None, s["addon_message"] if s["addon"] != "ready" else "waiting for the operator"
    rc = until(addon, args.timeout, "addon", sleep, now)
    if rc:
        return rc
    if get_opt(kube, hf.K_CONTROLLER, hf.NS, hf.CONTROLLER_NAME) is None:
        kube.create(hf.controller_manifest())
        step("controller", "running", "ForkliftController created: the operator deploys Forklift")

    def components():
        s = install_state(kube)
        if not s["components_missing"]:
            return True, "Forklift components are running"
        probs = hf.pod_problems(kube.list("pods", hf.NS))
        return None, "waiting for " + ", ".join(s["components_missing"]) + (f" ({'; '.join(probs)})" if probs else "")
    rc = until(components, args.timeout, "controller", sleep, now)
    if rc:
        return rc
    kube.apply(hf.inventory_rbac())
    step("install", "done", "Forklift is installed")
    return EXIT_OK


def cmd_vddk_image(args):
    if getattr(args, "spec", None):
        creds = read_json_input(args)
    else:
        creds = read_stdin_json() if args.auth_stdin else None
    if creds is not None and not (creds.get("username") and creds.get("password")):
        raise ValueError('registry credentials: {"username": ..., "password": ...} expected')
    step("vddk", "running", f"building the VDDK image from {Path(args.archive).name}")
    res = op.push_vddk_image(args.archive, args.image, base=args.base, target_creds=creds,
                             plain_http=args.plain_http, step=lambda m: step("vddk", "running", m))
    step("vddk", "done", f"{res['image']} ({res['digest'][:19]})")
    if getattr(args, "cluster", None) or getattr(args, "kubeconfig", None):
        kube = kube_from(args)
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        kube.apply([hf.namespace_manifest(),
                    hf.vddk_record_manifest(res["image"], res["digest"], Path(args.archive).name, when)])
        step("vddk-record", "done", f"the cluster remembers {res['image']}")
    print(json.dumps(res))
    return EXIT_OK


def cmd_provider_apply(args, kube=None, sleep=time.sleep, now=time.time):
    kube = kube or kube_from(args)
    ns, name = hf.check_name(args.namespace, "namespace"), hf.check_name(args.name, "provider")
    spec = read_json_input(args)
    secret, prov = hf.provider_secret(ns, name, spec), hf.provider_manifest(ns, name, spec)
    if not install_state(kube)["ready"]:
        step("provider", "error", "Forklift is not installed and running on this cluster: run install first")
        return EXIT_REFUSED
    try:
        kube.apply([secret])
    except KubeError as e:
        msg = str(e)
        reason = msg.split("denied the request: ", 1)[1] if "denied the request: " in msg else msg
        step("provider", "error", reason)
        return EXIT_FAIL
    kube.apply([prov])
    step("provider", "running", f"provider {ns}/{name} applied: Forklift checks the vCenter")
    return until(lambda: hf.provider_state(kube.get(hf.K_PROVIDER, ns, name)), args.timeout, "provider", sleep, now)


def cmd_provider_delete(args, kube=None, sleep=time.sleep, now=time.time):
    kube = kube or kube_from(args)
    ns, name = hf.check_name(args.namespace, "namespace"), hf.check_name(args.name, "provider")
    cur = get_opt(kube, hf.K_PROVIDER, ns, name)
    if cur is None:
        raise ValueError(f"no provider {ns}/{name}")
    users = hf.plans_using(ns, name, kube.list(hf.K_PLAN, None))
    if users:
        step("provider", "error", f"provider {ns}/{name} is used by migration plans: {', '.join(users)}")
        return EXIT_REFUSED
    kube.delete(hf.K_PROVIDER, ns, name)
    ref = (cur.get("spec") or {}).get("secret") or {}
    if args.with_secret and ref.get("name"):
        sec = kube.get("secrets", ref.get("namespace") or ns, ref["name"])
        if sec and ((sec.get("metadata") or {}).get("labels") or {}).get(hf.L_MANAGED) == "true":
            kube.delete("secrets", ref.get("namespace") or ns, ref["name"])
            step("provider", "running", f"secret {ref['name']} deleted")
    return until(lambda: (True, f"provider {ns}/{name} deleted") if get_opt(kube, hf.K_PROVIDER, ns, name) is None
                 else (None, "deleting"), args.timeout, "provider", sleep, now)


def fetch_json(url, token):
    """GET du service d'inventaire par le relais local : son certificat est
    celui du cluster (cert-manager), le canal est celui de kubectl. Toute
    panne (jeton refusé, relais tombé, JSON invalide) devient une KubeError
    courte, jamais une trace Python, et ne porte jamais le jeton."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        e.close()
        raise KubeError(f"inventory service: HTTP {e.code} {e.reason}") from None
    except urllib.error.URLError as e:
        raise KubeError(f"inventory service: {e.reason}") from None
    except OSError as e:
        raise KubeError(f"inventory service: {e}") from None
    try:
        return json.loads(body)
    except ValueError:
        raise KubeError("inventory service: invalid JSON") from None


def cmd_inventory(args, kube=None, fetch=None):
    kube = kube or kube_from(args)
    ns, name = hf.check_name(args.namespace, "namespace"), hf.check_name(args.name, "provider")
    p = get_opt(kube, hf.K_PROVIDER, ns, name)
    if p is None:
        raise ValueError(f"no provider {ns}/{name}")
    uid = (p.get("metadata") or {}).get("uid")
    token = kube.run("create", "token", hf.INVENTORY_SA, "-n", hf.NS, "--duration", "10m").strip()
    with kube.port_forward(hf.NS, f"svc/{hf.INVENTORY_SVC}", hf.INVENTORY_PORT) as port:
        data = (fetch or fetch_json)(f"https://127.0.0.1:{port}/providers/vsphere/{uid}/{args.kind}?detail=1", token)
    print(json.dumps(hf.inventory_rows(args.kind, data)))
    return EXIT_OK


def build_parser():
    ap = argparse.ArgumentParser(prog="harvester-forklift",
                                 description="Forklift on a Harvester cluster: install, VDDK "
                                             "image, vCenter provider, inventory.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cluster_args(sp):
        sp.add_argument("--cluster")
        sp.add_argument("--kubeconfig")

    sp = sub.add_parser("status", help="Forklift on the cluster and its providers, as JSON")
    cluster_args(sp)
    sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("install", help="cert-manager, the forklift-operator add-on and the ForkliftController")
    cluster_args(sp)
    sp.add_argument("--chart-version", default=hf.CHART_VERSION)
    sp.add_argument("--image-tag", default=hf.IMAGE_TAG, help="tag of the harvester-forklift-* images")
    sp.add_argument("--cert-manager-manifest", help="cert-manager YAML, applied if cert-manager is missing")
    sp.add_argument("--cert-manager-from-bundle",
                    help="the console's Cluster API bundle (.tar.gz): its cert-manager manifest "
                         "is applied if cert-manager is missing")
    sp.add_argument("--timeout", type=int, default=900)
    sp.set_defaults(fn=cmd_install)
    sp = sub.add_parser("vddk-image", help="build the VDDK init image from VMware's archive and push it")
    sp.add_argument("--archive", required=True, help="VMware-vix-disklib-*.x86_64.tar.gz")
    sp.add_argument("--image", required=True, help="registry/path:tag to push to")
    sp.add_argument("--base", default=op.DEFAULT_BASE, help="base image with cp")
    sp.add_argument("--plain-http", action="store_true", help="the target registry speaks plain HTTP")
    sp.add_argument("--auth-stdin", action="store_true", help='{"username": ..., "password": ...} on stdin')
    cluster_args(sp)
    sp.add_argument("--spec", help='a private JSON file {"username": ..., "password": ...} instead of stdin')
    sp.set_defaults(fn=cmd_vddk_image)
    for name, fn, hlp in (("provider-apply", cmd_provider_apply, "declare or change a vCenter provider (JSON on stdin)"),
                          ("provider-delete", cmd_provider_delete, "delete a vCenter provider")):
        sp = sub.add_parser(name, help=hlp)
        cluster_args(sp)
        sp.add_argument("--namespace", required=True)
        sp.add_argument("--name", required=True)
        sp.add_argument("--timeout", type=int, default=300)
        if name == "provider-delete":
            sp.add_argument("--with-secret", action="store_true", help="also delete the secret the console created")
        if name == "provider-apply":
            sp.add_argument("--spec", help="a private JSON file with the request instead of stdin")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("inventory", help="VMs, networks or datastores of a vCenter provider, as Forklift sees them")
    cluster_args(sp)
    sp.add_argument("--namespace", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--kind", choices=("vms", "networks", "datastores"), required=True)
    sp.set_defaults(fn=cmd_inventory)
    return ap, sub


def main(argv=None):
    ap, _ = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, KubeError, op.RegistryError) as e:
        step(args.cmd, "error", str(e))
        return EXIT_REFUSED if isinstance(e, ValueError) else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
