"""harvester-ops : l'image VDDK construite et poussée sans outil de conteneur (v1.75.0).

Forklift copie le VDDK de VMware dans ses pods par une image d'amorçage
(`vddkInitImage` du fournisseur) : le dossier vmware-vix-disklib-distrib/ à
la racine, et une commande qui le copie dans /opt. La licence de VMware
interdit de livrer le VDDK ; la console n'a ni podman ni buildah. L'image se
construit donc ici, par l'API de registre v2 :

- l'archive VDDK de VMware (tar.gz, tout sous vmware-vix-disklib-distrib/)
  EST la couche du dessus, telle quelle : son empreinte est celle du
  fichier, son diff_id celle du tar décompressé ;
- la base (bci-busybox de SUSE, pour `cp`) est recopiée couche par couche
  depuis son registre, sans être décompressée ; la version linux/amd64 d'un
  index multi-architecture ;
- jeton Bearer (realm, service, scope) ou Basic ; blobs envoyés d'un bloc ;
  redirections suivies SANS l'en-tête Authorization (les CDN des registres
  refusent un second mode d'authentification).

Bibliothèque standard seulement.
"""

import base64
import gzip
import hashlib
import json
import re
import ssl
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

VDDK_DIR = "vmware-vix-disklib-distrib"
VDDK_LIB = f"{VDDK_DIR}/lib64/libvixDiskLib.so"
DEFAULT_BASE = "registry.suse.com/bci/bci-busybox:16.0"
ENTRYPOINT = ["cp", "-r", f"/{VDDK_DIR}", "/opt"]

M_DOCKER = "application/vnd.docker.distribution.manifest.v2+json"
M_DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
M_OCI = "application/vnd.oci.image.manifest.v1+json"
M_OCI_INDEX = "application/vnd.oci.image.index.v1+json"
LAYER_TYPE = {M_DOCKER: "application/vnd.docker.image.rootfs.diff.tar.gzip",
              M_OCI: "application/vnd.oci.image.layer.v1.tar+gzip"}
CONFIG_TYPE = {M_DOCKER: "application/vnd.docker.container.image.v1+json",
               M_OCI: "application/vnd.oci.image.config.v1+json"}
ACCEPT = ", ".join((M_OCI_INDEX, M_DOCKER_LIST, M_OCI, M_DOCKER))

REF_RE = re.compile(r"^(?P<reg>[a-z0-9.-]+(?::\d{1,5})?)/"
                    r"(?P<repo>[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)"
                    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?(?:@(?P<digest>sha256:[0-9a-f]{64}))?$")


class RegistryError(Exception):
    pass


def digest_of(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def parse_ref(ref):
    """(registre, dépôt, étiquette ou empreinte), lus comme Docker : un
    premier élément sans point, deux-points ni « localhost » est Docker Hub."""
    ref = str(ref or "").strip()
    first, _, rest = ref.partition("/")
    if not rest or not ("." in first or ":" in first or first == "localhost"):
        ref = "docker.io/" + (ref if rest else "library/" + ref)
    m = REF_RE.match(ref)
    if not m:
        raise ValueError(f"image: {ref!r} is not an image reference")
    reg = "registry-1.docker.io" if m.group("reg") == "docker.io" else m.group("reg")
    return reg, m.group("repo"), m.group("digest") or m.group("tag") or "latest"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Registry:
    """Client minimal de l'API de registre v2 (distribution)."""

    def __init__(self, host, creds=None, plain_http=False, timeout=120):
        self.host = host
        self.creds = creds or None
        self.base = f"{'http' if plain_http else 'https'}://{host}"
        self.timeout = timeout
        self.auth = {}
        self.opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def _basic(self):
        if not self.creds:
            return None
        raw = f"{self.creds.get('username', '')}:{self.creds.get('password', '')}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _token(self, challenge, scope):
        p = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        if "realm" not in p:
            raise RegistryError(f"{self.host}: unreadable authentication challenge")
        q = {"service": p.get("service", "")}
        if scope:
            q["scope"] = scope
        req = urllib.request.Request(p["realm"] + "?" + urllib.parse.urlencode(q))
        if self._basic():
            req.add_header("Authorization", self._basic())
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            raise RegistryError(f"{self.host}: authentication refused (HTTP {e.code})") from None
        tok = body.get("token") or body.get("access_token")
        if not tok:
            raise RegistryError(f"{self.host}: authentication refused (no token)")
        return "Bearer " + tok

    def request(self, method, url, scope, data=None, headers=None, ok=()):
        """(code, en-têtes, corps). Un 401 fait prendre un jeton ou poser
        Basic, une fois ; une redirection est suivie sans identifiant."""
        if not url.startswith("http"):
            url = self.base + url
        for attempt in (0, 1):
            h = dict(headers or {})
            if self.auth.get(scope):
                h["Authorization"] = self.auth[scope]
            req = urllib.request.Request(url, data=data, method=method, headers=h)
            try:
                with self.opener.open(req, timeout=self.timeout) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and method in ("GET", "HEAD") and e.headers.get("Location"):
                    loc = urllib.parse.urljoin(url, e.headers["Location"])
                    with self.opener.open(urllib.request.Request(loc, method=method), timeout=self.timeout) as r:
                        return r.status, r.headers, r.read()
                if e.code == 401 and attempt == 0:
                    ch = e.headers.get("WWW-Authenticate", "")
                    if ch.lower().startswith("bearer"):
                        self.auth[scope] = self._token(ch, scope)
                    elif self._basic():
                        self.auth[scope] = self._basic()
                    else:
                        raise RegistryError(f"{self.host}: authentication required") from None
                    continue
                if e.code in ok:
                    return e.code, e.headers, b""
                if e.code == 401:
                    raise RegistryError(f"{self.host}: authentication refused") from None
                text = e.read()[:200].decode(errors="replace")
                raise RegistryError(f"{method} {self.host}{urllib.parse.urlparse(url).path}: HTTP {e.code} {text}") from None
        raise RegistryError(f"{self.host}: authentication refused")

    def manifest(self, repo, ref):
        _, h, body = self.request("GET", f"/v2/{repo}/manifests/{ref}", f"repository:{repo}:pull",
                                  headers={"Accept": ACCEPT})
        mt = (h.get("Content-Type") or "").split(";")[0].strip() or json.loads(body).get("mediaType", "")
        return body, mt

    def blob(self, repo, digest):
        _, _, body = self.request("GET", f"/v2/{repo}/blobs/{digest}", f"repository:{repo}:pull")
        if digest_of(body) != digest:
            raise RegistryError(f"{self.host}/{repo}: blob {digest[:19]} does not match its digest")
        return body

    def has_blob(self, repo, digest):
        code, _, _ = self.request("HEAD", f"/v2/{repo}/blobs/{digest}", f"repository:{repo}:pull,push", ok=(404,))
        return code == 200

    def push_blob(self, repo, digest, data):
        """Envoi d'un bloc (POST puis PUT ?digest=) ; False si déjà présent."""
        if self.has_blob(repo, digest):
            return False
        scope = f"repository:{repo}:pull,push"
        _, h, _ = self.request("POST", f"/v2/{repo}/blobs/uploads/", scope, data=b"",
                               headers={"Content-Length": "0"})
        loc = h.get("Location")
        if not loc:
            raise RegistryError(f"{self.host}/{repo}: no upload location")
        loc = urllib.parse.urljoin(self.base + "/", loc)
        sep = "&" if "?" in loc else "?"
        self.request("PUT", f"{loc}{sep}digest={urllib.parse.quote(digest)}", scope, data=data,
                     headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(data))})
        return True

    def put_manifest(self, repo, tag, body, media_type):
        _, h, _ = self.request("PUT", f"/v2/{repo}/manifests/{tag}", f"repository:{repo}:pull,push",
                               data=body, headers={"Content-Type": media_type})
        return h.get("Docker-Content-Digest") or digest_of(body)


def archive_layer(path):
    """L'archive VDDK comme couche, après contrôle : tout sous
    vmware-vix-disklib-distrib/, chemins relatifs sans « .. », liens qui ne
    sortent pas du dossier, et la bibliothèque présente."""
    h_gz, h_tar, size = hashlib.sha256(), hashlib.sha256(), 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h_gz.update(chunk)
            size += len(chunk)
    with gzip.open(path, "rb") as g:
        for chunk in iter(lambda: g.read(1 << 20), b""):
            h_tar.update(chunk)
    names = []
    with tarfile.open(path, "r:gz") as t:
        for m in t:
            n = m.name[2:] if m.name.startswith("./") else m.name
            parts = n.split("/")
            if n.startswith("/") or ".." in parts or parts[0] != VDDK_DIR:
                raise ValueError(f"VDDK archive: unexpected entry {m.name!r}, everything must be under {VDDK_DIR}/")
            if m.issym() or m.islnk():
                target = m.linkname
                if target.startswith("/") or ".." in target.split("/"):
                    raise ValueError(f"VDDK archive: link {m.name!r} points outside ({target!r})")
            names.append(n)
    if not any(n.startswith(VDDK_LIB) for n in names):
        raise ValueError(f"VDDK archive: no {VDDK_LIB}, this is not a VDDK")
    return {"digest": "sha256:" + h_gz.hexdigest(), "diff_id": "sha256:" + h_tar.hexdigest(), "size": size}


def vddk_config(base_config, diff_id, created):
    cfg = json.loads(json.dumps(base_config))
    cfg.setdefault("rootfs", {"type": "layers", "diff_ids": []}).setdefault("diff_ids", []).append(diff_id)
    c = cfg.get("config") or {}
    c["Entrypoint"] = list(ENTRYPOINT)
    c["Cmd"] = None
    c["User"] = "1001"
    c["Labels"] = dict(c.get("Labels") or {}, **{"io.harvester-ops.vddk": "true"})
    cfg["config"] = c
    cfg["created"] = created
    cfg.setdefault("history", []).append({"created": created,
                                          "created_by": f"harvester-ops: VDDK layer ({VDDK_DIR})"})
    return cfg


def push_vddk_image(archive, image, base=DEFAULT_BASE, target_creds=None, base_creds=None,
                    plain_http=False, base_plain_http=False, step=lambda msg: None, now=time.gmtime):
    t_reg, t_repo, t_tag = parse_ref(image)
    if t_tag.startswith("sha256:"):
        raise ValueError("image: give a tag to push to, not a digest")
    layer = archive_layer(archive)
    step(f"VDDK archive checked ({layer['size'] >> 20} MiB)")
    b_reg, b_repo, b_ref = parse_ref(base)
    src = Registry(b_reg, base_creds, plain_http=base_plain_http)
    dst = Registry(t_reg, target_creds, plain_http=plain_http)
    body, mt = src.manifest(b_repo, b_ref)
    if mt in (M_OCI_INDEX, M_DOCKER_LIST):
        pick = next((m for m in json.loads(body).get("manifests", [])
                     if (m.get("platform") or {}).get("os") == "linux"
                     and (m.get("platform") or {}).get("architecture") == "amd64"), None)
        if not pick:
            raise RegistryError(f"{base}: no linux/amd64 image")
        body, mt = src.manifest(b_repo, pick["digest"])
    if mt not in (M_OCI, M_DOCKER):
        raise RegistryError(f"{base}: unsupported manifest type {mt!r}")
    man = json.loads(body)
    cfg = json.loads(src.blob(b_repo, man["config"]["digest"]))
    for lay in man["layers"]:
        if not dst.has_blob(t_repo, lay["digest"]):
            dst.push_blob(t_repo, lay["digest"], src.blob(b_repo, lay["digest"]))
            step(f"base layer {lay['digest'][7:19]} copied")
    with open(archive, "rb") as f:
        if dst.push_blob(t_repo, layer["digest"], f.read()):
            step("VDDK layer sent")
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", now())
    new_cfg = json.dumps(vddk_config(cfg, layer["diff_id"], created), separators=(",", ":")).encode()
    dst.push_blob(t_repo, digest_of(new_cfg), new_cfg)
    new_man = {"schemaVersion": 2, "mediaType": mt,
               "config": {"mediaType": CONFIG_TYPE[mt], "digest": digest_of(new_cfg), "size": len(new_cfg)},
               "layers": man["layers"] + [{"mediaType": LAYER_TYPE[mt], "digest": layer["digest"],
                                           "size": layer["size"]}]}
    digest = dst.put_manifest(t_repo, t_tag, json.dumps(new_man, separators=(",", ":")).encode(), mt)
    step(f"pushed {t_reg}/{t_repo}:{t_tag}")
    return {"image": f"{t_reg}/{t_repo}:{t_tag}", "pinned": f"{t_reg}/{t_repo}@{digest}", "digest": digest}
