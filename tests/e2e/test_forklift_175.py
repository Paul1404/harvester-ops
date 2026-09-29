"""v1.75.0 : l'onglet Migrations VMware dans un navigateur : la Préparation
en trois étapes avec leur état, l'installation et la poussée de l'image
VDDK envoyées sans secret visible, le dépôt d'une archive."""

import json

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect  # noqa: E402

ARCHIVE = "VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz"
READY = {"ready": True, "cert_manager": True, "cert_manager_missing": [], "addon": "ready",
         "addon_message": "the forklift-operator add-on is deployed", "operator": True, "controller": True,
         "components_missing": []}
DATA = {"cluster": "harv-fake", "install": READY, "harvester_addon": False, "bundle": True,
        "vddk": {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "digest": "sha256:" + "4f1c" * 16,
                 "archive": ARCHIVE, "pushed_at": "2026-09-28T20:00:00Z"},
        "registry": {"image": "172.16.1.11:5005/harvops/vddk:8.0.3", "host": "172.16.1.11:5005",
                     "plain_http": True, "auth": True},
        "providers": [{"name": "vmwlab", "namespace": "forklift", "url": "https://vmwlab-vc.home.lo/sdk", "ready": True,
                       "message": "ready: vCenter reached, inventory loaded",
                       "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3", "plans": [], "managed": True}],
        "vmimport_sources": [{"namespace": "mig", "name": "vc", "endpoint": "https://vmwlab-vc.home.lo/sdk"}]}
ABSENT = {**DATA, "install": {**READY, "ready": False, "addon": "absent", "addon_message": "the forklift-operator add-on is not declared",
                              "operator": False, "controller": False, "components_missing": ["forklift-api"]},
          "vddk": None, "providers": []}
STORE = {"archives": [{"name": ARCHIVE, "version": "8.0.3", "size": 41626848, "mtime": 1790000000}], "free": 10 ** 11}


def fulfill(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def open_tab(context, flask_server, data):
    context.add_init_script(
        "localStorage.setItem('harvester_ops_language','en');"
        "localStorage.setItem('harvester_ops_current_cluster','harv-fake');"
        "localStorage.setItem('harvester_ops_section_forklift','prep');"
        "localStorage.setItem('harvester_ops_current_tab','forklift');")
    page = context.new_page()
    sent = []

    def writes(route, req):
        sent.append((req.url.split("://")[1].split("/", 1)[1], req.method, req.post_data_json if req.method == "POST" else None))
        fulfill(route, {"action_id": "fk0000000175"}, 202)
    page.route("**/api/forklift/harv-fake", lambda r, q: fulfill(r, data))
    page.route("**/api/forklift/harv-fake/do/**", writes)
    page.route("**/api/forklift-vddk", lambda r, q: fulfill(r, STORE))
    page.route("**/api/stream/fk0000000175", lambda r, q: r.fulfill(
        status=200, content_type="text/event-stream", body='event: end\ndata: {"status": "done"}\n\n'))
    page.goto(flask_server["base_url"], wait_until="domcontentloaded")
    page.wait_for_function("window.Forklift && window.Sections && window.App && App.getCurrentCluster()")
    page.on("dialog", lambda d: d.accept())
    return page, sent


def test_preparation_shows_three_steps_with_their_state(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA)
    steps = page.locator("#tab-forklift [data-fk-step]")
    expect(steps).to_have_count(3)
    expect(steps.nth(0)).to_contain_text("ready")
    expect(steps.nth(1)).to_contain_text("172.16.1.11:5005/harvops/vddk:8.0.3")
    expect(steps.nth(2)).to_contain_text("1")
    for b in page.locator("#tab-forklift button:visible").all():
        assert b.get_attribute("data-tip") or b.get_attribute("title"), b.inner_text()


def test_install_is_offered_when_forklift_is_absent_and_sent_as_an_action(context, flask_server):
    page, sent = open_tab(context, flask_server, ABSENT)
    page.locator('#tab-forklift [data-fk="install"]').click()
    page.wait_for_timeout(500)
    assert sent[0][0] == "api/forklift/harv-fake/do/install"


def test_the_vddk_image_is_pushed_with_harvester_s_registry_credentials(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    form = page.locator('#tab-forklift [data-fk-step="vddk"]')
    expect(form.locator('[name="archive"]')).to_have_value(ARCHIVE)
    expect(form.locator('[name="image"]')).to_have_value("172.16.1.11:5005/harvops/vddk:8.0.3")
    expect(form.locator('[name="use_cluster_auth"]')).to_be_checked()
    form.locator('[data-fk="push-vddk"]').click()
    page.wait_for_timeout(500)
    url, method, body = sent[0]
    assert url == "api/forklift/harv-fake/do/vddk-image"
    assert body == {"archive": ARCHIVE, "image": "172.16.1.11:5005/harvops/vddk:8.0.3", "plain_http": True,
                    "use_cluster_auth": True}


VMS = json.load(open(__import__("pathlib").Path(__file__).resolve().parents[1] / "api" / "fixtures" / "forklift_inventory_vms_175.json"))


def rows_of(vms):
    """Les lignes que rend l'outil (hf.inventory_rows), depuis le relevé réel."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))
    import hv_forklift as hf
    return hf.inventory_rows("vms", vms)


def test_a_source_block_says_its_state_and_offers_inventory_edit_delete(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    page.evaluate("Sections.open('forklift', 'sources')")
    block = page.locator('#tab-forklift [data-fk-source="vmwlab"]')
    expect(block).to_contain_text("https://vmwlab-vc.home.lo/sdk")
    expect(block.locator('[data-fk="del-source"]')).to_be_enabled()
    block.locator('[data-fk="del-source"]').click()
    page.wait_for_timeout(400)
    assert sent[-1][0] == "api/forklift/harv-fake/do/provider-delete" and sent[-1][2] == {"name": "vmwlab"}


def test_a_source_used_by_a_plan_cannot_be_deleted(context, flask_server):
    used = {**DATA, "providers": [{**DATA["providers"][0], "plans": ["forklift/wave-1"]}]}
    page, _ = open_tab(context, flask_server, used)
    page.evaluate("Sections.open('forklift', 'sources')")
    expect(page.locator('#tab-forklift [data-fk-source="vmwlab"] [data-fk="del-source"]')).to_be_disabled()


def test_a_vcenter_of_vm_import_is_taken_without_retyping_the_password(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    page.evaluate("Sections.open('forklift', 'sources')")
    page.locator('#tab-forklift [data-fk="new-source"]').click()
    form = page.locator(".floating-panel .of-form").last
    form.locator('[name="from"]').select_option("mig/vc")
    expect(form.locator('[name="password"]')).to_be_hidden()
    form.locator('[name="name"]').fill("vmwlab2")
    form.locator('button[type="submit"]').click()
    page.wait_for_timeout(400)
    url, _, body = sent[-1]
    assert url == "api/forklift/harv-fake/do/provider-apply"
    assert body == {"spec": {"name": "vmwlab2", "from_vmimport": {"namespace": "mig", "name": "vc"},
                             "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3"}}


def test_a_typed_vcenter_goes_with_its_certificate_choice(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    page.evaluate("Sections.open('forklift', 'sources')")
    page.locator('#tab-forklift [data-fk="new-source"]').click()
    form = page.locator(".floating-panel .of-form").last
    form.locator('[name="name"]').fill("vc2")
    form.locator('[name="url"]').fill("vc2.lan")
    form.locator('[name="user"]').fill("administrator@vsphere.local")
    form.locator('[name="password"]').fill("pw")
    form.locator('[name="tls"][value="insecure"]').check()
    form.locator('button[type="submit"]').click()
    page.wait_for_timeout(400)
    assert sent[-1][2] == {"spec": {"name": "vc2", "url": "vc2.lan", "user": "administrator@vsphere.local", "password": "pw",
                                    "insecure": True, "vddk_image": "172.16.1.11:5005/harvops/vddk:8.0.3"}}


def test_editing_a_source_keeps_its_password_unless_retyped(context, flask_server):
    page, sent = open_tab(context, flask_server, DATA)
    page.evaluate("Sections.open('forklift', 'sources')")
    page.locator('#tab-forklift [data-fk-source="vmwlab"] [data-fk="edit-source"]').click()
    form = page.locator(".floating-panel .of-form").last
    expect(form.locator('[name="name"]')).to_have_attribute("readonly", "")
    form.locator('button[type="submit"]').click()
    page.wait_for_timeout(400)
    spec = sent[-1][2]["spec"]
    assert spec["keep_credentials"] is True and spec["password"] == "" and spec["name"] == "vmwlab"
    assert "insecure" not in spec and "cacert" not in spec   # « garder le réglage actuel » : le serveur reprend le TLS du secret


def test_the_inventory_shows_warm_capability_and_forklift_s_concerns(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA)
    page.route("**/api/forklift/harv-fake/inventory/vmwlab/vms", lambda r, q: fulfill(r, {"rows": rows_of(VMS)}))
    page.evaluate("Forklift.openInventory('vmwlab')")
    table = page.locator('#tab-forklift [data-fk="inv-table"]')
    expect(table.locator("tbody tr")).to_have_count(4)
    vc = table.locator('tr[data-vm="vmwlab-vc"]')
    expect(vc).to_contain_text("CBT")
    expect(vc.locator('[data-fk-warm="no"]')).to_have_count(1)
    expect(table.locator('tr[data-vm="vmwlab-src-1"] [data-fk-warm="yes"]')).to_have_count(1)
    page.locator('#tab-forklift [name="warm_only"]').check()
    expect(table.locator("tbody tr")).to_have_count(3)
    page.locator('#tab-forklift [name="q"]').fill("src-3")
    expect(table.locator("tbody tr")).to_have_count(1)


def test_an_inventory_error_is_said(context, flask_server):
    page, _ = open_tab(context, flask_server, DATA)
    page.route("**/api/forklift/harv-fake/inventory/vmwlab/vms",
               lambda r, q: fulfill(r, {"error": "the inventory service answered 503"}, 502))
    page.evaluate("Forklift.openInventory('vmwlab')")
    expect(page.locator("#tab-forklift .res-error")).to_contain_text("503")
