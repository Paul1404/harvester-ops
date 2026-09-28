"""v1.75.0 : l'image VDDK construite et poussée sans podman ni buildah,
contre deux registres simulés (API v2) : la base est recopiée couche par
couche d'un registre anonyme, l'archive VDDK de l'exploitant devient la
couche du dessus telle quelle, la cible exige un jeton Bearer, et les blobs
redirigés vers un « CDN » qui refuse tout en-tête Authorization."""

import base64
import gzip
import hashlib
import io
import json
import re
import sys
import tarfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))
import oci_push as op  # noqa: E402


def sha(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


class FakeRegistry:
    """Registre v2 en mémoire : Bearer optionnel (jeton contre Basic),
    blobs GET redirigés optionnels vers /cdn/ (refuse Authorization)."""

    def __init__(self, user=None, password=None, redirect_blobs=False):
        self.blobs, self.manifests, self.uploads = {}, {}, []
        self.user, self.password, self.redirect = user, password, redirect_blobs
        self.blobs_cdn_broken = False
        reg = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_GET(self):
                self._route()

            def do_HEAD(self):
                self._route()

            def do_POST(self):
                self._route()

            def do_PUT(self):
                self._route()

            def _route(self):
                path, _, query = self.path.partition("?")
                if path == "/token":
                    want = "Basic " + base64.b64encode(f"{reg.user}:{reg.password}".encode()).decode()
                    if self.headers.get("Authorization") != want:
                        return self._send(401, b"{}")
                    return self._send(200, json.dumps({"token": "good-token"}).encode())
                if path.startswith("/cdn/"):
                    if self.headers.get("Authorization"):
                        return self._send(400, b"only one auth mechanism allowed")
                    if reg.blobs_cdn_broken:
                        return self._send(404, b"not found")
                    return self._send(200, reg.blobs[path[5:]])
                if reg.user is not None and self.headers.get("Authorization") != "Bearer good-token":
                    return self._send(401, b"{}", {"WWW-Authenticate":
                                                   f'Bearer realm="{reg.url}/token",service="fake",scope="repository:x:pull"'})
                if path == "/v2/":
                    return self._send(200, b"{}")
                m = re.match(r"^/v2/(.+?)/(blobs|manifests)/(uploads/)?(.*)$", path)
                if not m:
                    return self._send(404)
                repo, what, up, ref = m.groups()
                if what == "blobs" and up:
                    if self.command == "POST":
                        return self._send(202, headers={"Location": f"/v2/{repo}/blobs/uploads/{uuid.uuid4().hex}"})
                    data = self._body()
                    digest = urllib.parse.parse_qs(query)["digest"][0]
                    if sha(data) != digest:
                        return self._send(400, b"digest mismatch")
                    reg.blobs[digest] = data
                    reg.uploads.append(digest)
                    return self._send(201, headers={"Docker-Content-Digest": digest})
                if what == "blobs":
                    if ref not in reg.blobs:
                        return self._send(404)
                    if self.command == "GET" and reg.redirect:
                        return self._send(307, headers={"Location": f"{reg.url}/cdn/{ref}"})
                    return self._send(200, reg.blobs[ref])
                if self.command == "PUT":
                    body = self._body()
                    mt = self.headers.get("Content-Type")
                    reg.manifests[(repo, ref)] = (body, mt)
                    reg.manifests[(repo, sha(body))] = (body, mt)
                    return self._send(201, headers={"Docker-Content-Digest": sha(body)})
                if (repo, ref) not in reg.manifests:
                    return self._send(404)
                body, mt = reg.manifests[(repo, ref)]
                return self._send(200, body, {"Content-Type": mt, "Docker-Content-Digest": sha(body)})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.host = f"127.0.0.1:{self.srv.server_address[1]}"
        self.url = f"http://{self.host}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def targz(files, links=()):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
        for name, target in links:
            ti = tarfile.TarInfo(name)
            ti.type, ti.linkname = tarfile.SYMTYPE, target
            t.addfile(ti)
    return gzip.compress(raw.getvalue(), mtime=0), raw.getvalue()


@pytest.fixture
def base():
    """Une base « bci-busybox » : index multi-architecture, amd64 et arm64."""
    reg = FakeRegistry()
    images = {}
    for arch in ("amd64", "arm64"):
        layer, tar = targz({"usr/bin/cp": f"cp-{arch}".encode()})
        cfg = json.dumps({"architecture": arch, "os": "linux", "config": {"Cmd": ["/bin/sh"]},
                          "rootfs": {"type": "layers", "diff_ids": [sha(tar)]}, "history": []}).encode()
        reg.blobs[sha(layer)], reg.blobs[sha(cfg)] = layer, cfg
        man = json.dumps({"schemaVersion": 2, "mediaType": op.M_DOCKER,
                          "config": {"mediaType": op.CONFIG_TYPE[op.M_DOCKER], "digest": sha(cfg), "size": len(cfg)},
                          "layers": [{"mediaType": op.LAYER_TYPE[op.M_DOCKER], "digest": sha(layer), "size": len(layer)}]}).encode()
        reg.manifests[("bci/bci-busybox", sha(man))] = (man, op.M_DOCKER)
        images[arch] = (sha(man), sha(layer), sha(tar))
    idx = json.dumps({"schemaVersion": 2, "mediaType": op.M_DOCKER_LIST, "manifests": [
        {"mediaType": op.M_DOCKER, "digest": images[a][0], "size": 1, "platform": {"os": "linux", "architecture": a}}
        for a in ("arm64", "amd64")]}).encode()
    reg.manifests[("bci/bci-busybox", "16.0")] = (idx, op.M_DOCKER_LIST)
    reg.images = images
    yield reg
    reg.close()


@pytest.fixture
def vddk(tmp_path):
    data, tar = targz({"vmware-vix-disklib-distrib/lib64/libvixDiskLib.so.8": b"\x7fELF...",
                       "vmware-vix-disklib-distrib/doc/README": b"doc"},
                      links=[("vmware-vix-disklib-distrib/lib64/libvixDiskLib.so", "libvixDiskLib.so.8")])
    p = tmp_path / "VMware-vix-disklib-8.0.3.x86_64.tar.gz"
    p.write_bytes(data)
    return p, sha(data), sha(tar)


def test_the_vddk_archive_is_the_layer_as_is(vddk):
    p, digest, diff_id = vddk
    assert op.archive_layer(p) == {"digest": digest, "diff_id": diff_id, "size": p.stat().st_size}


@pytest.mark.parametrize("files,links,match", [
    ({"etc/passwd": b"x", "vmware-vix-disklib-distrib/lib64/libvixDiskLib.so.8": b"x"}, (), "unexpected entry"),
    ({"/vmware-vix-disklib-distrib/lib64/libvixDiskLib.so.8": b"x"}, (), "unexpected entry"),
    ({"vmware-vix-disklib-distrib/../x": b"x"}, (), "unexpected entry"),
    ({"vmware-vix-disklib-distrib/lib64/libvixDiskLib.so.8": b"x"},
     [("vmware-vix-disklib-distrib/lib64/evil", "../../../etc/shadow")], "link"),
    ({"vmware-vix-disklib-distrib/doc/README": b"x"}, (), "not a VDDK"),
])
def test_an_archive_that_is_not_a_clean_vddk_is_refused(tmp_path, files, links, match):
    data, _ = targz(files, links)
    p = tmp_path / "x.tar.gz"
    p.write_bytes(data)
    with pytest.raises(ValueError, match=match):
        op.archive_layer(p)


def test_references_are_read_like_docker_does():
    assert op.parse_ref("172.16.1.11:3000/jniedergang/vddk:8.0.3") == ("172.16.1.11:3000", "jniedergang/vddk", "8.0.3")
    assert op.parse_ref("registry.suse.com/bci/bci-busybox:16.0") == ("registry.suse.com", "bci/bci-busybox", "16.0")
    assert op.parse_ref("busybox") == ("registry-1.docker.io", "library/busybox", "latest")
    assert op.parse_ref("harbor.lan/p/vddk@sha256:" + "b" * 64)[2] == "sha256:" + "b" * 64
    with pytest.raises(ValueError):
        op.parse_ref("Harbor.lan/P/vddk:1")


def test_the_image_is_the_amd64_base_plus_the_vddk_layer(base, vddk):
    p, digest, diff_id = vddk
    target = FakeRegistry(user="ju", password="s3cret")
    try:
        res = op.push_vddk_image(p, f"{target.host}/ju/vddk:8.0.3", base=f"{base.host}/bci/bci-busybox:16.0",
                                 target_creds={"username": "ju", "password": "s3cret"},
                                 plain_http=True, base_plain_http=True, now=lambda: time.gmtime(0))
        body, mt = target.manifests[("ju/vddk", "8.0.3")]
        man = json.loads(body)
        assert mt == op.M_DOCKER and res["digest"] == sha(body)
        assert res["pinned"] == f"{target.host}/ju/vddk@{sha(body)}" and res["image"] == f"{target.host}/ju/vddk:8.0.3"
        amd_man, amd_layer, amd_tar = base.images["amd64"]
        assert [l["digest"] for l in man["layers"]] == [amd_layer, digest]
        assert man["layers"][1]["mediaType"] == op.LAYER_TYPE[op.M_DOCKER]
        cfg = json.loads(target.blobs[man["config"]["digest"]])
        assert cfg["rootfs"]["diff_ids"] == [amd_tar, diff_id]
        assert cfg["config"]["Entrypoint"] == ["cp", "-r", "/vmware-vix-disklib-distrib", "/opt"]
        assert cfg["config"]["Cmd"] is None and cfg["created"] == "1970-01-01T00:00:00Z"
        assert base.images["arm64"][1] not in target.blobs            # la seule base amd64
        assert target.blobs[digest] == p.read_bytes()                 # l'archive telle quelle
    finally:
        target.close()


def test_a_second_push_sends_no_blob_again(base, vddk):
    p, _, _ = vddk
    target = FakeRegistry()
    try:
        kw = dict(base=f"{base.host}/bci/bci-busybox:16.0", plain_http=True, base_plain_http=True,
                  now=lambda: time.gmtime(0))
        op.push_vddk_image(p, f"{target.host}/ju/vddk:8.0.3", **kw)
        first = len(target.uploads)
        op.push_vddk_image(p, f"{target.host}/ju/vddk:8.0.3", **kw)
        assert first == 3 and len(target.uploads) == first     # base, VDDK, configuration
    finally:
        target.close()


def test_redirected_blobs_are_fetched_without_the_credentials(vddk, base):
    # la base exige un jeton ET renvoie ses blobs vers un « CDN » qui refuse
    # tout en-tête Authorization : réussir prouve qu'il n'a pas suivi
    base.redirect, base.user, base.password = True, "reader", "pw"
    p, _, _ = vddk
    target = FakeRegistry()
    try:
        res = op.push_vddk_image(p, f"{target.host}/ju/vddk:1", base=f"{base.host}/bci/bci-busybox:16.0",
                                 base_creds={"username": "reader", "password": "pw"},
                                 plain_http=True, base_plain_http=True)
        assert base.images["amd64"][1] in target.blobs and res["digest"].startswith("sha256:")
    finally:
        target.close()


def test_wrong_credentials_are_said_without_the_password(base, vddk):
    p, _, _ = vddk
    target = FakeRegistry(user="ju", password="right")
    try:
        with pytest.raises(op.RegistryError, match="authentication refused") as e:
            op.push_vddk_image(p, f"{target.host}/ju/vddk:1", base=f"{base.host}/bci/bci-busybox:16.0",
                               target_creds={"username": "ju", "password": "wrong-pw"},
                               plain_http=True, base_plain_http=True)
        assert "wrong-pw" not in str(e.value)
    finally:
        target.close()


def test_a_digest_is_not_a_place_to_push_to(vddk):
    p, _, _ = vddk
    with pytest.raises(ValueError, match="tag"):
        op.push_vddk_image(p, "harbor.lan/p/vddk@sha256:" + "c" * 64)


def test_a_failing_redirect_target_is_a_registry_error(vddk, base):
    base.redirect = True
    base.blobs_cdn_broken = True          # the « CDN » answers 404
    target = FakeRegistry()
    try:
        with pytest.raises(op.RegistryError, match="HTTP 404"):
            op.push_vddk_image(vddk[0], f"{target.host}/ju/vddk:1", base=f"{base.host}/bci/bci-busybox:16.0",
                               plain_http=True, base_plain_http=True)
    finally:
        target.close()


def test_an_unreachable_registry_is_a_registry_error(vddk):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                  # nothing listens there any more
    with pytest.raises(op.RegistryError, match=f"127.0.0.1:{port}"):
        op.push_vddk_image(vddk[0], f"127.0.0.1:{port}/ju/vddk:1", base=f"127.0.0.1:{port}/bci/bci-busybox:16.0",
                           plain_http=True, base_plain_http=True)
