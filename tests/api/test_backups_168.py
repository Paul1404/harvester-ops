"""v1.68.0 : ce qui manquait au menu Backup and Snapshots : le délai de gel
du système de fichiers (1.9), le choix des anciens volumes à la restauration
d'une VM remplacée, la modification d'une planification."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_backups as hb  # noqa: E402


def crd(*fields):
    return {"spec": {"versions": [{"schema": {"openAPIV3Schema": {"properties": {"spec": {
        "properties": {f: {} for f in fields}}}}}}]}}


def test_a_backup_carries_its_freeze_deadline_when_harvester_knows_it():
    m = hb.backup_manifest("default", "web", "web-1", "snapshot", freeze="5s", supports_freeze=True)
    assert m["spec"]["fsFreezeDeadline"] == "5s"
    m = hb.backup_manifest("default", "web", "web-1", "backup", freeze="5s", supports_freeze=False)
    assert "fsFreezeDeadline" not in m["spec"]                         # la 1.8 le refuserait
    assert "fsFreezeDeadline" not in hb.backup_manifest("default", "web", "web-1")["spec"]
    for bad in ("0s", "2h", "-1s", "soon"):                             # 0s = gel sans fin : pas proposé
        with pytest.raises(ValueError, match="freeze"):
            hb.backup_manifest("default", "web", "web-1", freeze=bad, supports_freeze=True)
    assert hb.crd_has_spec_field(crd("source", "type", "fsFreezeDeadline"), "fsFreezeDeadline")
    assert not hb.crd_has_spec_field(crd("source", "type"), "fsFreezeDeadline")


def test_a_schedule_is_changed_with_harvesters_rules():
    assert hb.schedule_patch("0 3 * * *", 10, 4) == {"spec": {"cron": "0 3 * * *", "retain": 10, "maxFailure": 4}}
    for args, msg in ((("*/5 * * * *", 10, 4), "once an hour"), (("0 3 * * *", 5, 5), "fewer than"),
                      (("0 3 * * *", 300, 4), "between 3 and 250"), (("0 3 * *", 10, 4), "five cron")):
        with pytest.raises(ValueError, match=msg):
            hb.schedule_patch(*args)


def test_a_snapshot_restore_keeps_the_previous_volumes():
    m = hb.restore_manifest("default", "web-1", "web", False, delete_policy="delete")
    assert m["spec"]["deletionPolicy"] == "delete" and m["spec"]["newVM"] is False
    with pytest.raises(ValueError, match="snapshot"):
        hb.restore_manifest("default", "web-1", "web", False, delete_policy="delete", from_snapshot=True)
