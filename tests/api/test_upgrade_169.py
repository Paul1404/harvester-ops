"""v1.69.0 : la mise à jour de Harvester en fonctions pures : versions et
éligibilité (la règle du contrôleur, faite avant le téléchargement), objets
créés comme l'interface de Harvester, état suivi, pré-contrôles."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_upgrade as hu  # noqa: E402

SHA = "bf2e4a8d0b395a601d0ee7fbfeedbe74f7f9ce8b976ea84f38eb83b61a7dcfbf99ab9312887093f3529e65436e1fa20d55c5fb0e01bd8b2941f4dc8038d193ba"


def test_eligibility_follows_harvesters_version_guard():
    assert hu.eligible("v1.8.2", "v1.9.0", "v1.8.0") == (True, "")
    assert hu.eligible("v1.7.1", "v1.9.0", "v1.8.0")[0] is False
    assert "minimum" in hu.eligible("v1.7.1", "v1.9.0", "v1.8.0")[1]
    assert hu.eligible("v1.9.0", "v1.8.2") == (False, "downgrading is prohibited")
    assert hu.eligible("v1.9.0", "v1.9.0") == (True, "same version")
    assert "prerelease" in hu.eligible("v1.8.2", "v1.9.1-rc1")[1]
    assert hu.eligible("v1.9.1-rc1", "v1.9.1")[0] is True                    # rc vers sa version stable
    assert hu.eligible("dev", "v1.9.0")[0] is False and hu.eligible("v1.8.2", "dev")[0] is True
    assert hu.eligible("??", "v1.9.0")[0] is None


def test_a_published_version_file_and_an_iso_release_file_are_read():
    v = hu.version_from_yaml(f"apiVersion: harvesterhci.io/v1beta1\nkind: Version\nmetadata:\n  name: v1.9.0\n"
                             f"  namespace: harvester-system\nspec:\n  isoURL: https://releases.rancher.com/harvester/v1.9.0/harvester-v1.9.0-amd64.iso\n"
                             f"  isoChecksum: '{SHA}'\n  releaseDate: '20260916'\n")
    assert v["name"] == "v1.9.0" and v["checksum"] == SHA and v["release_date"] == "20260916"
    with pytest.raises(ValueError, match="Version"):
        hu.version_from_yaml("kind: ConfigMap\nmetadata: {name: x}")
    r = hu.release_info("harvester: v1.9.0\nos: Harvester v1.9.0\nkubernetes: v1.36.3+rke2r1\nminUpgradableVersion: 'v1.8.0'\n")
    assert r["harvester"] == "v1.9.0" and r["min_upgradable"] == "v1.8.0"
    with pytest.raises(ValueError, match="not a Harvester ISO"):
        hu.release_info("")


def test_objects_are_those_of_the_harvester_ui():
    m = hu.version_manifest("v1.9.0", "https://x/h.iso", SHA.upper(), "20260916", "v1.8.0")
    assert m["metadata"] == {"name": "v1.9.0", "namespace": "harvester-system"}
    assert m["spec"] == {"isoURL": "https://x/h.iso", "isoChecksum": SHA, "releaseDate": "20260916", "minUpgradableVersion": "v1.8.0"}
    for args, msg in ((("v1.9.0", "ftp://x", ""), "http"), (("v1.9.0", "https://x", "abc"), "SHA-512"), (("V 1", "https://x", ""), "name")):
        with pytest.raises(ValueError, match=msg):
            hu.version_manifest(*args)
    u = hu.upgrade_manifest(version="v1.9.0")
    assert u["metadata"] == {"generateName": "hvst-upgrade-", "namespace": "harvester-system"}
    assert u["spec"] == {"logEnabled": True, "version": "v1.9.0"}
    u = hu.upgrade_manifest(image="harvester-system/image-abc", log=False, skip_single=True)
    assert u["spec"] == {"logEnabled": False, "image": "harvester-system/image-abc"}
    assert u["metadata"]["annotations"] == {hu.A_SKIP_SINGLE: "true"}             # jamais "false"
    img = hu.os_image_manifest("harvester-v1.9.0", "http://10.0.0.1:8092/image/t", SHA)
    assert img["spec"]["backend"] == "cdi" and img["spec"]["targetStorageClassName"] == "longhorn-static"
    assert img["metadata"]["annotations"][hu.A_OS_IMAGE] == "True"
    p = hu.pause_patch({"metadata": {"annotations": {hu.A_PAUSE: '{"n1":"pause","n2":"pause"}'}}}, "n1")
    assert json.loads(p["metadata"]["annotations"][hu.A_PAUSE]) == {"n1": "unpause", "n2": "pause"}


def upg(name, state=None, cleanup=None, conds=(), nodes=None, latest=True, spec=None, created="2026-09-27T10:00:00Z", **st):
    labels = {}
    if state:
        labels[hu.L_STATE] = state
    if cleanup:
        labels[hu.L_CLEANUP] = cleanup
    if latest:
        labels[hu.L_LATEST] = "true"
    return {"metadata": {"name": name, "labels": labels, "creationTimestamp": created},
            "spec": spec or {"version": "v1.9.0", "logEnabled": True},
            "status": {"conditions": [{"type": t, "status": s, "reason": r, "message": m} for t, s, r, m in conds],
                       "nodeStatuses": nodes or {}, **st}}


def test_the_state_says_where_the_upgrade_is_and_why_it_failed():
    u = upg("hvst-upgrade-x", "UpgradingNodes", conds=[("LogReady", "True", "", ""), ("ImageReady", "True", "", ""),
                                                        ("NodesUpgraded", "Unknown", "", "")],
            nodes={"n1": {"state": "Images preloaded"}}, repoInfo="release:\n  harvester: v1.9.0\n  os: Harvester v1.9.0\n",
            upgradeLog="hvst-upgrade-x-upgradelog", previousVersion="v1.8.2")
    v = hu.upgrade_view(u, {"status": {"progress": 100}}, [{"metadata": {"name": "n1"}}, {"metadata": {"name": "n2"}}])
    assert v["completed"] is None and v["target"] == "v1.9.0" and v["previous"] == "v1.8.2" and v["image_progress"] == 100
    assert [n["state"] for n in v["nodes"]] == ["Images preloaded", "Pending"]
    assert v["can_abort"] is False and v["can_dismiss"] is False                      # phase nœuds : Harvester refuse
    assert v["notes"].endswith("/v1.9.0") and v["log_name"] == "hvst-upgrade-x-upgradelog"
    assert hu.settled(u)[0] is None
    bad = upg("u2", "Failed", "Pending", conds=[("ImageReady", "False", "RetryLimitExceeded", "404 not found"),
                                                 ("Completed", "False", "", "")])
    assert hu.failure(bad) == "ImageReady: 404 not found" and hu.settled(bad) == (False, "ImageReady: 404 not found")
    early = upg("u3", "Failed", conds=[("Completed", "False", "downgrading is prohibited", "")])
    assert hu.failure(early) == "downgrading is prohibited"                           # le texte est dans reason
    ok = upg("u4", "Succeeded", "Succeeded", conds=[("Completed", "True", "", "")])
    assert hu.settled(ok)[0] is True and hu.upgrade_view(ok)["can_dismiss"] is True
    assert hu.upgrade_view(upg("u5", conds=[("LogReady", "False", "Disabled", "")]))["failure"] == ""   # journaux non demandés


def test_what_blocks_a_new_upgrade():
    ups = [upg("old", "Succeeded", "Succeeded", latest=False), upg("fresh", None, latest=False)]
    assert hu.running(ups) == "fresh"                              # sans état : bloque aussi (sélecteur NotIn)
    assert hu.cleanup_pending([upg("a", "Failed", "Pending")]) == ["a"]
    assert hu.latest([upg("a", "Succeeded", latest=False, created="2026-01-01"), upg("b", "Succeeded", latest=True)])["metadata"]["name"] == "b"


def test_prechecks_say_what_the_webhook_would_refuse():
    nodes = [{"metadata": {"name": f"n{i}"}, "spec": {"unschedulable": i == 3},
              "status": {"conditions": [{"type": "Ready", "status": "True" if i != 2 else "False"}]}} for i in (1, 2, 3)]
    vols = [{"metadata": {"name": "pvc-1"}, "status": {"robustness": "degraded"}}]
    res = {c["key"]: c for c in hu.prechecks(nodes, vols, [], [{"metadata": {"namespace": "d", "name": "s"}, "spec": {}}],
                                             [{"metadata": {"namespace": "k", "name": "desch"}, "status": {"status": "AddonEnabling"}}],
                                             [{"metadata": {"name": "harvester"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}],
                                             [upg("x", "Succeeded", "Succeeded")])}
    assert not res["nodes-ready"]["ok"] and res["nodes-ready"]["text"] == "n2"
    assert not res["nodes-schedulable"]["ok"] and not res["volumes"]["ok"] and not res["schedules"]["ok"]
    assert not res["addons"]["ok"] and res["charts"]["ok"] and res["running"]["ok"] and res["cleanup"]["ok"]
    single = {c["key"] for c in hu.prechecks(nodes[:1], vols)}
    assert "volumes" not in single                                  # le webhook ne le vérifie qu'à 3 nœuds
