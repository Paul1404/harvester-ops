"""v1.67.0 : les réglages de Harvester, la cible de sauvegarde, le paquet de
support et un kubeconfig sûr, en fonctions pures (formes et refus de
Harvester v1.9.0 ; aucun secret ne ressort vers la page)."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_settings as hs  # noqa: E402


def setting(name, value="", default="", ann=None, conds=None):
    return {"metadata": {"name": name, "annotations": ann or {}}, "value": value, "default": default,
            "status": {"conditions": conds or []}}


def test_rows_group_settings_and_hide_what_the_ui_hides():
    got = hs.rows([setting("log-level", "debug", "info", {hs.HASH_ANN: "h1"}),
                   setting("server-version", "v1.9.0", "dev"), setting("vip-pools"),
                   setting("storage-network")])
    by = {r["name"]: r for r in got}
    assert set(by) == {"log-level", "server-version", "storage-network"}          # vip-pools caché
    assert by["log-level"]["modified"] and by["log-level"]["applied"] == "ok" and by["log-level"]["choices"] == ["info", "debug", "trace"]
    assert by["server-version"]["readonly"] and by["storage-network"]["elsewhere"] == "network"
    assert "vip-pools" in {r["name"] for r in hs.rows([setting("vip-pools")], show_hidden=True)}


def test_secrets_never_leave_the_cluster():
    tls = json.dumps({"ca": "C", "publicCertificate": "P", "privateKey": "-----BEGIN PRIVATE KEY-----x"})
    reg = json.dumps({"Configs": {"reg.lan": {"Auth": {"Username": "u", "Password": "p", "Auth": "dTpw"}}}})
    proxy = json.dumps({"httpProxy": "http://user:secret@proxy:3128", "noProxy": "172.16.0.0/16"})
    rows = {r["name"]: r for r in hs.rows([setting("ssl-certificates", tls), setting("containerd-registry", reg),
                                           setting("http-proxy", proxy)])}
    assert "PRIVATE KEY" not in rows["ssl-certificates"]["value"] and hs.MASK in rows["ssl-certificates"]["value"]
    assert '"Password":"' + hs.MASK in rows["containerd-registry"]["value"] and '"Username":"u"' in rows["containerd-registry"]["value"]
    assert "secret" not in rows["http-proxy"]["value"] and "user:" in rows["http-proxy"]["value"]


def test_a_value_is_checked_as_harvester_would():
    assert hs.validate("log-level", "debug") == "debug"
    assert hs.validate("max-hotplug-ratio", "5") == "5"
    assert hs.validate("log-level", "") == ""                              # retour au défaut
    for name, value, msg in (("log-level", "loud", "one of"), ("max-hotplug-ratio", "30", "from 1 to 20"),
                             ("overcommit-config", '{"cpu":90}', "undercommit"),
                             ("ntp-servers", '{"ntpServers":["http://pool.ntp.org"]}', "no http"),
                             ("ntp-servers", '{"ntpServers":["a.lan","a.lan"]}', "twice"),
                             ("default-vm-termination-grace-period-seconds", "-1", "0 or more"),
                             ("additional-guest-memory-overhead-ratio", "0.5", "1.0 to 10.0"),
                             ("longhorn-v2-data-engine-memory-size", "2049", "even"),
                             ("server-version", "v2", "read-only"), ("storage-network", "{}", "Network"),
                             ("upgrade-config", "{nope", "JSON"), ("additional-ca", "hello", "PEM")):
        with pytest.raises(ValueError, match=msg):
            hs.validate(name, value)


def test_the_backup_target_forms():
    assert json.loads(hs.validate("backup-target", json.dumps({"type": "nfs", "endpoint": "172.16.0.5:/volume1/BACKUP/lab/"}))) \
        == {"type": "nfs", "endpoint": "nfs://172.16.0.5:/volume1/BACKUP/lab/"}
    s3 = json.loads(hs.backup_target_value({"type": "s3", "endpoint": "https://s3.lan", "bucketName": "b", "bucketRegion": "r",
                                            "accessKeyId": "k", "secretAccessKey": "s", "refreshIntervalInSeconds": 60}))
    assert s3["virtualHostedStyle"] is False and s3["refreshIntervalInSeconds"] == 60
    for spec, msg in (({"type": "s3", "bucketName": "b", "bucketRegion": "r"}, "access key"),
                      ({"type": "s3", "accessKeyId": "k", "secretAccessKey": "s"}, "bucket"),
                      ({"type": "nfs"}, "endpoint"), ({"type": "ftp"}, "Invalid"),
                      ({"type": "nfs", "endpoint": "x", "refreshIntervalInSeconds": -1}, "Refresh")):
        with pytest.raises(ValueError, match=msg):
            hs.backup_target_value(spec)


def test_applied_waits_for_the_hash_and_says_errors():
    before = "h1"
    s = setting("log-level", "debug", ann={hs.HASH_ANN: "h1"})
    assert hs.settled(s, before)[0] is None
    s["metadata"]["annotations"][hs.HASH_ANN] = "h2"
    assert hs.settled(s, before) == (True, "applied by Harvester")
    bad = setting("ntp-servers", '{"ntpServers":["x"]}', conds=[{"type": "configured", "status": "False", "message": "boom"}])
    assert hs.settled(bad, None, elapsed=40) == (False, "boom")
    assert hs.settled(setting("upgrade-checker-enabled", "false"), None) == (True, "saved")         # sans contrôleur
    assert hs.settled(setting("log-level", ""), "h1") == (True, "back to the default")


def test_a_support_bundle_needs_a_description():
    class R:
        @staticmethod
        def token_hex(n):
            return "abc123"
    b = hs.bundle_manifest({"description": "slow VM", "issue_url": "https://x/1", "namespaces": ["default"], "timeout": "10"}, rnd=R)
    assert b["metadata"] == {"name": "bundle-abc123", "namespace": "harvester-system"}
    assert b["spec"] == {"description": "slow VM", "issueURL": "https://x/1", "extraCollectionNamespaces": ["default"], "timeout": 10}
    with pytest.raises(ValueError, match="description"):
        hs.bundle_manifest({})
    assert hs.bundle_download_path("bundle-abc123").endswith("/supportbundles/bundle-abc123/download?retain=true")


def test_a_kubeconfig_gives_only_its_role_and_expires():
    now = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)
    sa, rb, secs = hs.kubeconfig_objects({"name": "ci", "role": "view", "namespace": "default", "duration": "8h"}, now=now)
    assert sa["metadata"]["namespace"] == hs.KC_NS and sa["metadata"]["annotations"][hs.KC_EXPIRES] == "2026-09-27T18:00:00Z"
    assert rb["kind"] == "RoleBinding" and rb["metadata"]["namespace"] == "default" and rb["roleRef"]["name"] == "view"
    assert secs == 8 * 3600
    _, crb, _ = hs.kubeconfig_objects({"name": "ops", "role": "harvesterhci.io:edit"}, now=now)
    assert crb["kind"] == "ClusterRoleBinding"
    for spec, msg in (({"name": "x", "role": "god"}, "role"), ({"name": "x", "role": "view", "duration": "1y"}, "duration"),
                      ({"name": "x", "role": "cluster-admin", "namespace": "default"}, "whole cluster")):
        with pytest.raises(ValueError, match=msg):
            hs.kubeconfig_objects(spec)
    yml = hs.kubeconfig_yaml("harv1", "https://172.16.3.100:6443", "-----BEGIN CERTIFICATE-----\n", "TOKEN", "kc-ci")
    assert "token: TOKEN" in yml and "server: https://172.16.3.100:6443" in yml
    [row] = hs.kubeconfig_rows([sa], now=datetime(2026, 9, 28, tzinfo=timezone.utc))
    assert row["name"] == "ci" and row["expired"] and row["scope"] == "default"


def test_masked_secrets_come_back_from_the_cluster_or_are_typed_again():
    cur = json.dumps({"ca": "C", "publicCertificate": "P", "privateKey": "KEY"})
    back = json.loads(hs.unmask("ssl-certificates", hs.redact("ssl-certificates", cur).replace('"C"', '"C2"'), cur))
    assert back == {"ca": "C2", "publicCertificate": "P", "privateKey": "KEY"}
    proxy = json.dumps({"httpProxy": "http://u:pw@p:3128", "noProxy": "10.0.0.0/8"})
    shown = json.loads(hs.redact("http-proxy", proxy))
    shown["noProxy"] = "10.0.0.0/8,172.16.0.0/16"
    assert json.loads(hs.unmask("http-proxy", json.dumps(shown, ensure_ascii=False), proxy))["httpProxy"] == "http://u:pw@p:3128"
    shown["httpProxy"] = "http://u:" + hs.MASK + "@other:3128"
    with pytest.raises(ValueError, match="password again"):
        hs.unmask("http-proxy", json.dumps(shown, ensure_ascii=False), proxy)
    with pytest.raises(ValueError, match="type them again"):
        hs.unmask("backup-target", json.dumps({"type": "s3", "secretAccessKey": hs.MASK}, ensure_ascii=False), "{}")


def test_roles_say_which_ones_read_secrets():
    roles = [{"metadata": {"name": "view"}, "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "list"]}]},
             {"metadata": {"name": "harvesterhci.io:edit"}, "rules": [{"apiGroups": ["kubevirt.io"], "resources": ["*"], "verbs": ["*"]}]},
             {"metadata": {"name": "cluster-admin"}, "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]}]
    info = {r["name"]: r for r in hs.roles_info(roles)}
    assert info["view"]["reads_secrets"] and info["cluster-admin"]["reads_secrets"]
    assert not info["harvesterhci.io:edit"]["reads_secrets"] and not info["edit"]["exists"]


def test_the_rancher_import_token_is_masked_and_kept():
    bare = "http://rancher.home.lo/v3/import/nw74t7dqhcg4hjk_c-sg2q6.yaml"
    shown = hs.redact("cluster-registration-url", bare)
    assert "nw74t7" not in shown and shown.endswith("/v3/import/" + hs.MASK)
    assert hs.unmask("cluster-registration-url", shown, bare) == bare
    js = json.dumps({"url": bare, "insecureSkipTLSVerify": True})
    shown = hs.redact("cluster-registration-url", js)
    assert "nw74t7" not in shown
    back = json.loads(hs.unmask("cluster-registration-url", shown.replace("true", "false"), js))
    assert back == {"url": bare, "insecureSkipTLSVerify": False}
    with pytest.raises(ValueError, match="whole import URL"):
        hs.unmask("cluster-registration-url", "http://other/v3/import/" + hs.MASK, bare)
    [row] = hs.rows([setting("cluster-registration-url", bare)])
    assert "nw74t7" not in row["value"]
