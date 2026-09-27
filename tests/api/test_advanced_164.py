"""v1.64.0 : les menus « Advanced » de Harvester (bin/lib/hv_advanced.py) :
versions de modèles, modèles cloud-init, classes de stockage (chiffrement,
LVM, v2, topologies), secrets typés et leur modification, clés SSH."""

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_advanced as ha  # noqa: E402


def b64(s):
    return base64.b64encode(s.encode()).decode()


def test_template_versions_and_default():
    tpl = {"metadata": {"name": "web", "namespace": "default"}, "spec": {"defaultVersionId": "default/web-2"}}
    vers = [{"metadata": {"name": f"web-{i}", "namespace": "default"}, "spec": {"templateId": "default/web", "description": f"v{i}"},
             "status": {"version": i, "conditions": [{"type": "ready", "status": "True" if i != 3 else "False"}]}} for i in (1, 2, 3)]
    vers.append({"metadata": {"name": "db-1", "namespace": "default"}, "spec": {"templateId": "default/db"}, "status": {"version": 1}})
    rows = ha.versions_of(tpl, vers)
    assert [r["version"] for r in rows] == [3, 2, 1]
    assert [r["default"] for r in rows] == [False, True, False] and rows[0]["ready"] is False
    assert ha.set_default_patch("default/web-3") == {"spec": {"defaultVersionId": "default/web-3"}}
    with pytest.raises(ValueError, match="default version"):
        ha.delete_version_check(tpl, "default/web-2")
    ha.delete_version_check(tpl, "default/web-1")


def test_cloud_templates_are_labelled_configmaps():
    cm = ha.cloud_template("base", "default", "user", "#cloud-config\npackages: [htop]\n", "base tools")
    assert cm["metadata"]["labels"] == {ha.CLOUD_LABEL: "user"} and cm["data"] == {"cloudInit": "#cloud-config\npackages: [htop]\n"}
    with pytest.raises(ValueError, match="YAML"):
        ha.cloud_template("bad", "default", "user", "a: [")
    with pytest.raises(ValueError, match="user or network"):
        ha.cloud_template("x", "default", "harvester", "a: 1")
    rows = ha.cloud_templates([cm, {"metadata": {"name": "other", "namespace": "default", "labels": {ha.CLOUD_LABEL: "harvester"}}}])
    assert [r["name"] for r in rows] == ["base"] and rows[0]["description"] == "base tools"


def test_storage_class_forms():
    v1 = ha.storage_class({"name": "fast", "replicas": "2", "disk_selector": "nvme, ssd", "data_locality": "best-effort"})
    assert v1["provisioner"] == ha.LH and v1["parameters"]["diskSelector"] == "nvme,ssd" and v1["parameters"]["dataEngine"] == "v1"
    enc = ha.storage_class({"name": "enc", "encrypted": True, "secret": "default/crypto", "expand_online": True})
    p = enc["parameters"]
    assert p["encrypted"] == "true" and p["csi.storage.k8s.io/node-stage-secret-name"] == "crypto"
    assert p["csi.storage.k8s.io/node-expand-secret-namespace"] == "default"
    lvm = ha.storage_class({"name": "local", "engine": "lvm", "node": "n1", "vg": "vg-data", "lvm_type": "dm-thin"})
    assert lvm["provisioner"] == ha.LVM and lvm["volumeBindingMode"] == "WaitForFirstConsumer"
    assert lvm["allowedTopologies"] == [{"matchLabelExpressions": [{"key": "topology.lvm.csi/node", "values": ["n1"]}]}]
    v2 = ha.storage_class({"name": "v2", "engine": "longhorn-v2", "topologies": [{"key": "zone", "values": "a, b"}]})
    assert v2["parameters"]["dataEngine"] == "v2" and v2["allowedTopologies"][0]["matchLabelExpressions"][0]["values"] == ["a", "b"]
    with pytest.raises(ValueError, match="secret"):
        ha.storage_class({"name": "e", "encrypted": True})
    with pytest.raises(ValueError, match="node and its volume group"):
        ha.storage_class({"name": "l", "engine": "lvm"})


def test_crypto_secret_checked_like_the_webhook():
    s = ha.crypto_secret("crypto", "default", "a long passphrase")
    enc = {"data": {k: b64(v) for k, v in s["stringData"].items()}}
    assert ha.crypto_secret_ok(enc)
    enc["data"]["CRYPTO_KEY_HASH"] = b64("md5")
    assert not ha.crypto_secret_ok(enc)
    with pytest.raises(ValueError, match="8 characters"):
        ha.crypto_secret("c", "default", "short")


def test_typed_secrets_and_their_update():
    ba = ha.typed_secret("reg", "default", "kubernetes.io/basic-auth", {"username": "ops", "password": "pw"})
    assert ba["type"] == "kubernetes.io/basic-auth" and ba["stringData"] == {"username": "ops", "password": "pw"}
    dc = ha.typed_secret("pull", "default", "kubernetes.io/dockerconfigjson", {"server": "quay.io", "username": "u", "password": "p"})
    assert json.loads(dc["stringData"][".dockerconfigjson"]) == {"auths": {"quay.io": {"username": "u", "password": "p"}}}
    with pytest.raises(ValueError, match="PEM"):
        ha.typed_secret("t", "default", "kubernetes.io/tls", {"tls.crt": "x", "tls.key": "y"})
    existing = {"metadata": {"name": "reg", "namespace": "default", "uid": "u"}, "type": "kubernetes.io/basic-auth",
                "data": {"username": b64("ops"), "password": b64("old")}}
    up = ha.secret_update(existing, {"username": "", "password": "new"})
    assert base64.b64decode(up["data"]["password"]).decode() == "new" and base64.b64decode(up["data"]["username"]).decode() == "ops"
    with pytest.raises(ValueError, match="Opaque"):
        ha.secret_update(existing, {"remove": ["password"]})
    op = {"metadata": {"name": "o"}, "type": "Opaque", "data": {"a": b64("1"), "b": b64("2")}}
    assert set(ha.secret_update(op, {"remove": ["a"], "c": "3"})["data"]) == {"b", "c"}
    reg = {"metadata": {"name": "pull"}, "type": "kubernetes.io/dockerconfigjson",
           "data": {".dockerconfigjson": b64(json.dumps({"auths": {"quay.io": {"username": "u", "password": "p"}}}))}}
    out = json.loads(base64.b64decode(ha.secret_update(reg, {"password": "p2"})["data"][".dockerconfigjson"]))
    assert out == {"auths": {"quay.io": {"username": "u", "password": "p2"}}}


def test_keypair_update_lets_harvester_recompute_the_fingerprint():
    kp = {"metadata": {"name": "ops"}, "spec": {"publicKey": "ssh-ed25519 AAAA old"}, "status": {"fingerPrint": "aa:bb"}}
    out = ha.keypair_update(kp, "ssh-ed25519 BBBB new@host", "ops key")
    assert out["spec"]["publicKey"] == "ssh-ed25519 BBBB new@host" and "status" not in out
    assert out["metadata"]["annotations"][ha.DESC] == "ops key"
    with pytest.raises(ValueError, match="OpenSSH"):
        ha.keypair_update(kp, "not a key")


def test_ssh_key_rows_carry_their_description():
    """v1.64.0 : la fenêtre de modification part de la description actuelle."""
    import cluster_objects as co
    kp = {"metadata": {"namespace": "default", "name": "ops",
                       "annotations": {"field.cattle.io/description": "ops key"}},
          "spec": {"publicKey": "ssh-ed25519 AAAA ops"}}
    [r] = co.ssh_keys([kp], [])
    assert r["description"] == "ops key"
    [r] = co.ssh_keys([{"metadata": {"namespace": "default", "name": "x"}, "spec": {}}], [])
    assert r["description"] == ""
