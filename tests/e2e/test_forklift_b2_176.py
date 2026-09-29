"""v1.76.0 : la Préparation gagne l'importeur de disques (CDI) et l'intervalle
des copies incrémentales ; l'inventaire dit les outils VMware, pourquoi une
VM ne peut pas migrer à chaud, et permet de la sélectionner."""

import json

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect  # noqa: E402

ARCHIVE = "VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz"
READY = {"ready": True, "cert_manager": True, "cert_manager_missing": [], "addon": "ready",
         "addon_message": "the forklift-operator add-on is deployed", "operator": True, "controller": True,
         "components_missing": [], "running": True, "inventory_access": True}
DATA = {"cluster": "harv-fake", "install": READY, "harvester_addon": False, "bundle": True,
        "vddk": {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "digest": "sha256:" + "4f1c" * 16,
                 "archive": ARCHIVE, "pushed_at": "2026-09-28T20:00:00Z"},
        "registry": {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "host": "172.16.1.11:5005",
                     "plain_http": True, "auth": True},
        "providers": [{"name": "vmwlab", "namespace": "forklift", "url": "https://vmwlab-vc.home.lo/sdk", "ready": True,
                       "message": "ready: vCenter reached, inventory loaded",
                       "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3", "plans": [], "managed": True}],
        "vmimport_sources": [{"namespace": "mig", "name": "vc", "endpoint": "https://vmwlab-vc.home.lo/sdk"}],
        "cdi_importer": {"image": "registry.suse.com/harvester/cdi-importer:v1.65.0", "kind": "suse-no-vddk", "original": ""},
        "precopy_interval": 60, "waves": []}
STORE = {"archives": [{"name": ARCHIVE, "version": "8.0.3", "size": 41626848, "mtime": 1790000000}], "free": 10 ** 11}

# Deux VMs éligibles (CBT actif, une allumée avec ses outils, une éteinte),
# une refusée par CBT, une refusée par les outils VMware (allumée, sans eux).
VMS_ROWS = [
    {"id": "vm-16", "name": "vmwlab-src-1", "path": "/dc/vm/vmwlab-src-1", "power": "poweredOn", "cbt": True,
     "cpus": 1, "memory_mib": 1024, "guest": "Debian GNU/Linux 11 (64-bit)", "disks": [{"datastore": "datastore-12", "capacity": 10737418240}],
     "networks": ["network-13"], "concerns": [], "tools": True, "snapshot": "", "uuid": "uuid-16"},
    {"id": "vm-17", "name": "vmwlab-off", "path": "/dc/vm/vmwlab-off", "power": "poweredOff", "cbt": True,
     "cpus": 1, "memory_mib": 1024, "guest": "Debian GNU/Linux 11 (64-bit)", "disks": [], "networks": [],
     "concerns": [], "tools": False, "snapshot": "", "uuid": "uuid-17"},
    {"id": "vm-18", "name": "vmwlab-no-cbt", "path": "/dc/vm/vmwlab-no-cbt", "power": "poweredOn", "cbt": False,
     "cpus": 1, "memory_mib": 1024, "guest": "Debian GNU/Linux 11 (64-bit)", "disks": [], "networks": [],
     "concerns": [], "tools": True, "snapshot": "", "uuid": "uuid-18"},
    {"id": "vm-19", "name": "vmwlab-src-3", "path": "/dc/vm/vmwlab-src-3", "power": "poweredOn", "cbt": True,
     "cpus": 2, "memory_mib": 4096, "guest": "Microsoft Windows Server 2019 (64-bit)", "disks": [], "networks": [],
     "concerns": [], "tools": False, "snapshot": "", "uuid": "uuid-19"},
]


def fulfill(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def open_tab(context, flask_server, data, section="prep"):
    context.add_init_script(
        "localStorage.setItem('harvester_ops_language','en');"
        "localStorage.setItem('harvester_ops_current_cluster','harv-fake');"
        f"localStorage.setItem('harvester_ops_section_forklift','{section}');"
        "localStorage.setItem('harvester_ops_current_tab','forklift');")
    page = context.new_page()
    sent = []

    def writes(route, req):
        sent.append((req.url.split("://")[1].split("/", 1)[1], req.method, req.post_data_json if req.method == "POST" else None))
        fulfill(route, {"action_id": "fk0000000176"}, 202)
    page.route("**/api/forklift/harv-fake", lambda r, q: fulfill(r, data() if callable(data) else data))
    page.route("**/api/forklift/harv-fake/do/**", writes)
    page.route("**/api/forklift-vddk", lambda r, q: fulfill(r, STORE))
    page.route("**/api/forklift/harv-fake/inventory/vmwlab/vms*", lambda r, q: fulfill(r, {"rows": VMS_ROWS}))
    page.route("**/api/stream/fk0000000176", lambda r, q: r.fulfill(
        status=200, content_type="text/event-stream", body='event: end\ndata: {"status": "done"}\n\n'))
    page.goto(flask_server["base_url"], wait_until="domcontentloaded")
    page.wait_for_function("window.Forklift && window.Sections && window.App && App.getCurrentCluster()")
    page.on("dialog", lambda d: d.accept())
    return page, sent


# --- Préparation : importeur CDI ------------------------------------------

def test_the_suse_importer_is_shown_in_red_with_its_explanation(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA)
    cdi = page.locator('#tab-forklift [data-fk-step="cdi"]')
    expect(cdi).to_contain_text("SUSE image, no VDDK")
    expect(cdi.locator(".sto-finding.sev-critical")).to_be_visible()
    expect(cdi.locator('[data-fk="cdi-upstream"]')).to_be_enabled()
    expect(cdi.locator('[data-fk="cdi-original"]')).to_be_disabled()


def test_switching_to_the_upstream_importer_is_sent_with_the_mirror_image(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    cdi = page.locator('#tab-forklift [data-fk-step="cdi"]')
    cdi.locator('[name="cdi_image"]').fill("172.16.1.11:5005/harvops/cdi-importer:v1.65.0")
    cdi.locator('[data-fk="cdi-upstream"]').click()
    page.wait_for_timeout(300)
    url, method, body = sent[-1]
    assert url == "api/forklift/harv-fake/do/cdi-importer"
    assert body == {"mode": "upstream", "image": "172.16.1.11:5005/harvops/cdi-importer:v1.65.0"}


def test_switching_to_the_upstream_importer_without_a_mirror_omits_the_image(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    page.locator('#tab-forklift [data-fk-step="cdi"] [data-fk="cdi-upstream"]').click()
    page.wait_for_timeout(300)
    assert sent[-1][2] == {"mode": "upstream"}


def test_the_upstream_importer_is_shown_in_green_and_can_go_back_to_the_original(context, flask_server):
    upstream = {**DATA, "cdi_importer": {"image": "quay.io/kubevirt/cdi-importer:v1.60.0", "kind": "upstream",
                                          "original": "registry.suse.com/harvester/cdi-importer:v1.65.0"}}
    page, sent = open_tab(context, flask_server, upstream)
    cdi = page.locator('#tab-forklift [data-fk-step="cdi"]')
    expect(cdi).to_contain_text("Upstream image")
    expect(cdi.locator(".sto-finding.sev-critical")).to_have_count(0)
    expect(cdi.locator('[data-fk="cdi-upstream"]')).to_be_disabled()
    original_btn = cdi.locator('[data-fk="cdi-original"]')
    expect(original_btn).to_be_enabled()
    assert "registry.suse.com" in (original_btn.get_attribute("data-tip") or "")
    original_btn.click()
    page.wait_for_timeout(300)
    assert sent[-1] == ("api/forklift/harv-fake/do/cdi-importer", "POST", {"mode": "original"})


def test_the_upgrade_warning_is_always_shown(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA)
    expect(page.locator('#tab-forklift [data-fk-step="cdi"]')).to_contain_text("upgrade")


# --- Préparation : intervalle des copies -----------------------------------

def test_the_precopy_interval_proposes_the_current_value(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA)
    box = page.locator('#tab-forklift [data-fk-precopy]')
    expect(box).to_contain_text("whole cluster")
    expect(box.locator('[name="precopy_minutes"]')).to_have_value("60")


def test_saving_the_interval_is_sent_in_minutes(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    box = page.locator('#tab-forklift [data-fk-precopy]')
    box.locator('[name="precopy_minutes"]').fill("15")
    box.locator('[data-fk="precopy-save"]').click()
    page.wait_for_timeout(300)
    assert sent[-1] == ("api/forklift/harv-fake/do/precopy-interval", "POST", {"minutes": 15})


def test_an_interval_out_of_bounds_is_refused_before_sending(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    box = page.locator('#tab-forklift [data-fk-precopy]')
    box.locator('[name="precopy_minutes"]').fill("4")
    box.locator('[data-fk="precopy-save"]').click()
    page.wait_for_timeout(300)
    assert not sent
    expect(box.locator('[data-fk="precopy-msg"]')).to_contain_text("5")


# --- Inventaire : outils VMware, raison, sélection --------------------------

def test_the_tools_column_says_running_or_not(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA, section="inventory")
    page.evaluate("Forklift.openInventory('vmwlab')")
    table = page.locator('#tab-forklift [data-fk="inv-table"]')
    expect(table.locator('tr[data-vm="vmwlab-src-1"] [data-fk-tools="yes"]')).to_have_count(1)
    expect(table.locator('tr[data-vm="vmwlab-no-cbt"] [data-fk-tools="yes"]')).to_have_count(1)
    expect(table.locator('tr[data-vm="vmwlab-src-3"] [data-fk-tools="no"]')).to_have_count(1)


def test_the_reason_column_explains_why_a_vm_is_refused(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA, section="inventory")
    page.evaluate("Forklift.openInventory('vmwlab')")
    table = page.locator('#tab-forklift [data-fk="inv-table"]')
    no_cbt = table.locator('tr[data-vm="vmwlab-no-cbt"] [data-fk-eligible]')
    expect(no_cbt).to_have_attribute("data-fk-eligible", "no")
    expect(no_cbt).to_contain_text("Changed Block Tracking")
    no_tools = table.locator('tr[data-vm="vmwlab-src-3"] [data-fk-eligible]')
    expect(no_tools).to_have_attribute("data-fk-eligible", "no")
    expect(no_tools).to_contain_text("VMware Tools")
    eligible = table.locator('tr[data-vm="vmwlab-src-1"] [data-fk-eligible]')
    expect(eligible).to_have_attribute("data-fk-eligible", "yes")
    expect(eligible).to_contain_text("eligible")


def test_only_eligible_vms_can_be_checked(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA, section="inventory")
    page.evaluate("Forklift.openInventory('vmwlab')")
    table = page.locator('#tab-forklift [data-fk="inv-table"]')
    expect(table.locator('tr[data-vm="vmwlab-no-cbt"] [data-fk="vm-select"]')).to_be_disabled()
    expect(table.locator('tr[data-vm="vmwlab-src-3"] [data-fk="vm-select"]')).to_be_disabled()
    expect(table.locator('tr[data-vm="vmwlab-src-1"] [data-fk="vm-select"]')).to_be_enabled()
    expect(table.locator('tr[data-vm="vmwlab-off"] [data-fk="vm-select"]')).to_be_enabled()


def test_selecting_vms_updates_the_count_and_selected_vms(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA, section="inventory")
    page.evaluate("Forklift.openInventory('vmwlab')")
    table = page.locator('#tab-forklift [data-fk="inv-table"]')
    expect(page.locator('[data-fk="inv-selected-count"]')).to_contain_text("0")
    table.locator('tr[data-vm="vmwlab-src-1"] [data-fk="vm-select"]').check()
    table.locator('tr[data-vm="vmwlab-off"] [data-fk="vm-select"]').check()
    expect(page.locator('[data-fk="inv-selected-count"]')).to_contain_text("2")
    ids = page.evaluate("Forklift.selectedVms().map(v => v.id).sort()")
    assert ids == ["vm-16", "vm-17"]
    names = page.evaluate("Forklift.selectedVms().map(v => v.name)")
    assert "vmwlab-src-1" in names and "vmwlab-off" in names
    # la source accompagne chaque VM sélectionnée (utile pour W6)
    src = page.evaluate("Forklift.selectedVms()[0].source")
    assert src == "vmwlab"


def test_the_compose_wave_button_is_disabled_until_it_is_wired_by_the_waves_tab(context, flask_server):
    """W5 : la fenêtre « Composer une vague » n'existe pas encore (W6). Le
    bouton reste désactivé même avec une sélection, tant que
    Forklift.composeWave n'est pas défini."""
    page, _ = open_tab(context, flask_server, DATA, section="inventory")
    page.evaluate("Forklift.openInventory('vmwlab')")
    table = page.locator('#tab-forklift [data-fk="inv-table"]')
    btn = page.locator('[data-fk="compose-wave"]')
    expect(btn).to_be_disabled()
    table.locator('tr[data-vm="vmwlab-src-1"] [data-fk="vm-select"]').check()
    expect(btn).to_be_disabled()
    assert "Waves" in (btn.get_attribute("data-tip") or "")


def test_the_compose_wave_button_is_enabled_once_a_composer_exists(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA, section="inventory")
    page.evaluate("window.Forklift.composeWave = () => { window.__composed = true; }")
    page.evaluate("Forklift.openInventory('vmwlab')")
    table = page.locator('#tab-forklift [data-fk="inv-table"]')
    btn = page.locator('[data-fk="compose-wave"]')
    expect(btn).to_be_disabled()   # rien de coché encore
    table.locator('tr[data-vm="vmwlab-src-1"] [data-fk="vm-select"]').check()
    expect(btn).to_be_enabled()
    btn.click()
    assert page.evaluate("window.__composed") is True
    page.evaluate("delete window.Forklift.composeWave; delete window.__composed;")


def test_switching_source_resets_the_selection(context, flask_server):
    two = {**DATA, "providers": [DATA["providers"][0], {**DATA["providers"][0], "name": "vmwlab2"}]}
    page, _ = open_tab(context, flask_server, two, section="inventory")
    page.route("**/api/forklift/harv-fake/inventory/vmwlab2/vms*", lambda r, q: fulfill(r, {"rows": []}))
    page.evaluate("Forklift.openInventory('vmwlab')")
    table = page.locator('#tab-forklift [data-fk="inv-table"]')
    table.locator('tr[data-vm="vmwlab-src-1"] [data-fk="vm-select"]').check()
    expect(page.locator('[data-fk="inv-selected-count"]')).to_contain_text("1")
    page.locator('#tab-forklift [name="source"]').select_option("forklift/vmwlab2")
    expect(page.locator('[data-fk="inv-selected-count"]')).to_contain_text("0")
