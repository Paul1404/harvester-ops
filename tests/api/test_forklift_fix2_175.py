"""v1.75.0, deuxième relecture : le miroir VDDK atteint le conteneur
packagé, l'outil ne repose pas l'accès inventaire à chaque fournisseur, le
409 sur un nom déjà pris couvre aussi un namespace donné explicitement,
doc et CHANGELOG à jour."""

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_forklift as hf  # noqa: E402

from tests.api.test_forklift_cli_175 import Clock, prov_args, SPEC  # noqa: E402
from tests.api.test_forklift_review_175 import hfk, installed  # noqa: E402
from tests.api.test_forklift_routes_175 import world, auth, wapp  # noqa: E402,F401


# --- 4. l'accès à l'inventaire n'est reposé que s'il manque -----------------

def test_provider_apply_does_not_reapply_inventory_rbac_when_already_present(monkeypatch, capsys):
    k = installed()   # Forklift en marche, accès inventaire déjà posé, k.calls vidé
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    c = Clock()
    assert hfk.cmd_provider_apply(prov_args(), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_OK
    kinds = [x[1] for x in k.calls if x[0] == "apply"]
    assert kinds == ["Secret", "Provider"], kinds


def test_provider_apply_still_gives_the_inventory_access_when_missing(monkeypatch, capsys):
    from tests.api.test_forklift_review_175 import forklift_running_without_inventory_access
    k = forklift_running_without_inventory_access()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SPEC)))
    c = Clock()
    assert hfk.cmd_provider_apply(prov_args(), kube=k, sleep=c.sleep, now=c.now) == hfk.EXIT_OK
    kinds = [x[1] for x in k.calls if x[0] == "apply"]
    assert kinds == ["ServiceAccount", "ClusterRole", "ClusterRoleBinding", "Secret", "Provider"], kinds


# --- 5. le 409 vaut aussi pour un namespace donné explicitement -------------

def test_a_new_source_is_refused_in_a_namespace_where_it_already_exists(world):
    spec = {"name": "vmwlab", "namespace": "forklift", "url": "vc2.lan", "user": "u@vsphere.local",
            "password": "N3w-S3cret", "insecure": True}
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply", json={"spec": spec}, headers=auth("adm"))
    assert r.status_code == 409
    assert "already" in r.get_json()["error"]
    assert world["actions"] == []


def test_a_new_source_in_a_namespace_where_it_does_not_exist_is_allowed(world):
    spec = {"name": "vmwlab", "namespace": "default", "url": "vc2.lan", "user": "u@vsphere.local",
            "password": "N3w-S3cret", "insecure": True}
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply", json={"spec": spec}, headers=auth("adm"))
    assert r.status_code == 202


def test_an_edit_with_keep_credentials_ignores_the_taken_name_check(world):
    spec = {"name": "vmwlab", "namespace": "forklift", "url": "https://vmwlab-vc.home.lo/sdk",
            "keep_credentials": True}
    with wapp.app.test_client() as c:
        r = c.post("/api/forklift/harvlab2/do/provider-apply", json={"spec": spec}, headers=auth("adm"))
    assert r.status_code == 202


# --- 1. le miroir VDDK atteint le conteneur packagé -------------------------

def test_the_vddk_base_mirror_env_var_reaches_the_container():
    unit = (ROOT / "config" / "systemd" / "harvester-ops.service").read_text()
    import re
    joined = re.sub(r"\\\n\s*", " ", unit)
    start = " ".join(line.split("=", 1)[1] for line in joined.splitlines() if line.startswith("ExecStart="))
    assert "-e HARVESTER_OPS_VDDK_BASE=$${HARVESTER_OPS_VDDK_BASE:-}" in start


# --- 7. doc Airgap : miroir anonyme HTTPS uniquement ------------------------

def test_docs_say_the_mirror_is_pulled_anonymously_over_https():
    for doc in ("docs/en/capabilities.md", "docs/fr/capabilites.md"):
        text = (ROOT / doc).read_text()
        section = text.split("Airgap", 1)[1][:1800]
        assert "anonymous" in section.lower() or "anonyme" in section.lower(), doc
        assert "https" in section.lower(), doc
        assert "plain http" in section.lower() or "http simple" in section.lower(), doc


# --- 8. CHANGELOG 1.75.0 ------------------------------------------------------

def test_changelog_has_a_fixed_section_for_1_75_0():
    text = (ROOT / "CHANGELOG.md").read_text()
    entry = text.split("## [1.75.0]", 1)[1].split("\n## [", 1)[0]
    assert "### Fixed" in entry
    fixed = entry.split("### Fixed", 1)[1]
    assert "window reopened twice" in fixed and "no longer sends its form twice" in fixed
    assert "VM Import windows" in fixed and "cluster they were opened for" in fixed
    added = entry.split("### Added", 1)[1].split("### ", 1)[0]
    assert "provider-delete" in added and "not a vCenter" in added
    assert "provider-apply" in added and "another tool" in added
    assert entry.index("### Added") < entry.index("### Fixed")
