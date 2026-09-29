"""v1.76.0 : la vue globale « Migrations (all clusters) », à côté d'Activity
dans le menu latéral (pas sous Cluster). Un tableau à plat de toutes les VMs
prises dans une vague Forklift, sur tous les clusters déclarés, une ligne
d'en-tête par cluster, des filtres d'état et de vCenter, et un clic sur une
vague qui bascule vers son cluster et ouvre l'onglet Vagues.

Deux clusters routés : `harv-fake` (le cluster de test, joignable, trois
vagues) et `harv-second` (déclaré pour de vrai le temps du test, comme dans
test_cluster_switch.py, pour apparaître dans le sélecteur, puis marqué
injoignable côté vue globale)."""

import json
import re

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect  # noqa: E402

READY = {"ready": True, "cert_manager": True, "cert_manager_missing": [], "addon": "ready",
         "addon_message": "the forklift-operator add-on is deployed", "operator": True, "controller": True,
         "components_missing": [], "running": True, "inventory_access": True}

VM_COPY = {"id": "vm-16", "name": "vmwlab-src-1", "phase": "Running", "step": "copying disks",
           "step_name": "DiskTransfer", "progress": {"done": 5120, "total": 10240}, "precopies": 3,
           "last_precopy": {"start": "2026-09-29T09:58:00Z", "end": "2026-09-29T09:59:02Z", "seconds": 62},
           "next_precopy": "2099-01-01T00:15:00Z", "error": "", "rolled_back": False, "cutover_started": False}
WAVE_COPY = {"name": "vague-1", "target_namespace": "mig-b2", "state": "copying", "message": "",
             "migration": "vague-1-m1", "vms": [VM_COPY], "cutover": None, "cutover_started": False,
             "next_precopy": "2099-01-01T00:15:00Z"}

VM_SCHED = {"id": "vm-20", "name": "vmwlab-src-2", "phase": "Running", "step": "final copy",
            "step_name": "Cutover", "progress": {"done": 0, "total": 1}, "precopies": 5,
            "last_precopy": {"start": "2026-09-29T09:00:00Z", "end": "2026-09-29T09:01:44Z", "seconds": 104},
            "next_precopy": None, "error": "", "rolled_back": False, "cutover_started": True}
WAVE_SCHED = {"name": "vague-2", "target_namespace": "mig-b2", "state": "cutover-scheduled", "message": "",
              "migration": "vague-2-m1", "vms": [VM_SCHED], "cutover": "2099-01-01T00:05:00Z",
              "cutover_started": True, "next_precopy": None}

VM_FAILED = {"id": "vm-21", "name": "vmwlab-src-3", "phase": "Failed", "step": "copying disks",
             "step_name": "DiskTransfer", "progress": {"done": 0, "total": 0}, "precopies": 0,
             "last_precopy": None, "next_precopy": None, "error": "disk transfer failed",
             "rolled_back": False, "cutover_started": False}
WAVE_FAILED = {"name": "vague-3", "target_namespace": "mig-b2", "state": "failed",
               "message": "disk transfer failed", "migration": "vague-3-m1", "vms": [VM_FAILED],
               "cutover": None, "cutover_started": False, "next_precopy": None}

VCENTER = "https://vmwlab-vc.home.lo/sdk"
GLOBAL_DATA = {"clusters": [
    {"cluster": "harv-fake", "reachable": True, "forklift_ready": True, "cdi_importer_kind": "upstream",
     "providers": [{"name": "vmwlab", "namespace": "forklift", "url": VCENTER, "ready": True, "message": ""}],
     "waves": [WAVE_COPY, WAVE_SCHED, WAVE_FAILED]},
    {"cluster": "harv-second", "reachable": False, "forklift_ready": False, "cdi_importer_kind": None,
     "providers": [], "waves": []},
]}

FK_TAB_DATA = {"cluster": "harv-fake", "install": READY, "harvester_addon": False, "bundle": True,
               "vddk": {"image": "", "digest": "", "archive": "", "pushed_at": ""},
               "registry": {"image": "", "host": "", "plain_http": True, "auth": False},
               "providers": [{"name": "vmwlab", "namespace": "forklift", "url": VCENTER, "ready": True,
                              "message": "", "vddk_image": "", "plans": [], "managed": True}],
               "vmimport_sources": [], "cdi_importer": {"image": "", "kind": "upstream", "original": ""},
               "precopy_interval": 60, "waves": [WAVE_COPY, WAVE_SCHED, WAVE_FAILED]}
FK_TAB_UNREACHABLE = {"error": "cluster unreachable", "unreachable": True, "cluster": "harv-second",
                      "endpoint": "?:0"}


def fulfill(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


@pytest.fixture
def two_clusters(flask_server, api):
    """Déclare un second cluster le temps du test, pour qu'il apparaisse
    dans #cluster-select (même patron que test_cluster_switch.py) : la vue
    globale elle-même est entièrement routée, mais le clic sur une vague
    passe par le sélecteur réel, qui est rendu côté serveur."""
    payload = {
        "name": "harv-second",
        "description": "second cluster (vue globale Forklift)",
        "kubeconfig": str(flask_server["config"]["kubeconfig"]),
        "nodes": [{"hostname": "second-node1", "ip": "10.0.0.2", "role": "control-plane"}],
    }
    status, _ = api("POST", "/api/clusters", payload, expect_status=None)
    if status not in (201, 409):
        pytest.skip(f"impossible de déclarer le second cluster: {status}")
    yield "harv-second"
    api("DELETE", "/api/clusters/harv-second", expect_status=None)


def open_global(context, flask_server, data=GLOBAL_DATA, initial_cluster="harv-second"):
    context.add_init_script(
        "localStorage.setItem('harvester_ops_language','en');"
        f"localStorage.setItem('harvester_ops_current_cluster','{initial_cluster}');"
        "localStorage.setItem('harvester_ops_current_tab','forkliftglobal');")
    page = context.new_page()
    page.route("**/api/forklift-global", lambda r, q: fulfill(r, data() if callable(data) else data))
    page.route(re.compile(r".*/api/forklift/harv-fake(\?.*)?$"), lambda r, q: fulfill(r, FK_TAB_DATA))
    page.route(re.compile(r".*/api/forklift/harv-second(\?.*)?$"), lambda r, q: fulfill(r, FK_TAB_UNREACHABLE))
    page.goto(flask_server["base_url"], wait_until="domcontentloaded")
    page.wait_for_function("window.ForkliftGlobal && window.Sections && window.App && App.getCurrentCluster()")
    return page


def test_the_entry_sits_next_to_activity_not_under_cluster(context, flask_server):
    page = open_global(context, flask_server)
    entry = page.locator('.tab[data-tab="forkliftglobal"]')
    expect(entry).to_be_visible()
    assert entry.get_attribute("data-i18n-title") or entry.get_attribute("title")
    # ni un `.tab-child` (sous Cluster), ni dans le groupe Automation
    assert "tab-child" not in (entry.get_attribute("class") or "")
    assert page.locator('#tab-forkliftglobal')


def test_every_wave_of_every_cluster_is_a_row(context, flask_server):
    page = open_global(context, flask_server)
    table = page.locator('#tab-forkliftglobal table.data-table')
    expect(table).to_be_visible(timeout=10000)
    expect(table.locator('tbody tr')).to_have_count(3)
    names = table.locator('tbody tr td:first-child').all_inner_texts()
    assert names == ["vmwlab-src-1", "vmwlab-src-2", "vmwlab-src-3"]
    # colonnes : VM, vCenter, cluster, vague, étape, dernière copie, bascule
    row = table.locator('tr[data-fkg-wave="vague-1"]')
    expect(row).to_contain_text("vmwlab-vc.home.lo")
    expect(row).to_contain_text("harv-fake")
    expect(row).to_contain_text("vague-1")
    expect(row).to_contain_text("copying disks")


def test_the_unreachable_cluster_is_shown_without_blocking_the_other(context, flask_server):
    page = open_global(context, flask_server)
    head = page.locator('#tab-forkliftglobal [data-fkg-clusterhead="harv-second"]')
    expect(head).to_be_visible()
    expect(head).to_contain_text("Unreachable")
    ok_head = page.locator('#tab-forkliftglobal [data-fkg-clusterhead="harv-fake"]')
    expect(ok_head).to_contain_text("harv-fake")
    expect(ok_head).not_to_contain_text("Unreachable")
    expect(ok_head).to_contain_text("3 wave")


def test_status_filters_narrow_to_one_row_each(context, flask_server):
    page = open_global(context, flask_server)
    table = page.locator('#tab-forkliftglobal table.data-table')
    expect(table).to_be_visible(timeout=10000)
    sel = page.locator('#tab-forkliftglobal [data-fkg="f-status"]')
    sel.select_option("progress")
    expect(table.locator('tbody tr')).to_have_count(1)
    expect(table.locator('tbody tr')).to_contain_text("vmwlab-src-1")
    sel.select_option("cutover")
    expect(table.locator('tbody tr')).to_have_count(1)
    expect(table.locator('tbody tr')).to_contain_text("vmwlab-src-2")
    sel.select_option("failed")
    expect(table.locator('tbody tr')).to_have_count(1)
    expect(table.locator('tbody tr')).to_contain_text("vmwlab-src-3")
    sel.select_option("")
    expect(table.locator('tbody tr')).to_have_count(3)


def test_the_vcenter_filter_lists_every_declared_source(context, flask_server):
    page = open_global(context, flask_server)
    expect(page.locator('#tab-forkliftglobal table.data-table')).to_be_visible(timeout=10000)
    opts = page.locator('#tab-forkliftglobal [data-fkg="f-vcenter"] option').all_inner_texts()
    assert opts[0].lower().startswith("all")
    assert "vmwlab-vc.home.lo" in opts
    page.locator('#tab-forkliftglobal [data-fkg="f-vcenter"]').select_option("vmwlab-vc.home.lo")
    expect(page.locator('#tab-forkliftglobal table.data-table tbody tr')).to_have_count(3)


def test_clicking_a_wave_switches_cluster_and_opens_the_waves_tab(context, flask_server, two_clusters):
    page = open_global(context, flask_server, initial_cluster="harv-second")
    assert page.locator('#cluster-select').input_value() == "harv-second"
    row = page.locator('#tab-forkliftglobal tr[data-fkg-wave="vague-2"]')
    expect(row).to_be_visible(timeout=10000)
    row.click()
    page.wait_for_function("App.getCurrentCluster() === 'harv-fake'", timeout=10000)
    assert page.locator('#cluster-select').input_value() == "harv-fake"
    expect(page.locator('.tab-content.active')).to_have_id("tab-forklift")
    expect(page.locator('#tab-forklift [data-section-tab="waves"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator('#tab-forklift .section-pane[data-pane="waves"]')).to_be_visible()


def test_every_control_has_a_tooltip(context, flask_server):
    page = open_global(context, flask_server)
    expect(page.locator('#tab-forkliftglobal table.data-table')).to_be_visible(timeout=10000)
    missing = page.evaluate("""() => [...document.querySelectorAll(
        '#tab-forkliftglobal button, #tab-forkliftglobal select')]
        .filter(el => !el.getAttribute('data-tip') && !el.getAttribute('title')).map(el => el.outerHTML.slice(0, 60))""")
    assert missing == []
