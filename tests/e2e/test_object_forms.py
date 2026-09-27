"""v1.59.0 : créer, modifier, supprimer depuis les sections, dans un navigateur.

Chaque formulaire s'ouvre dans une fenêtre ; ce qu'il envoie est vérifié
(routes simulées), comme les gestes de ligne (supprimer, classe par défaut,
configurer un add-on).
"""

import json

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect  # noqa: E402

LISTS = {
    "images": [{"namespace": "default", "name": "image-a", "display_name": "leap.qcow2", "source_type": "download",
                "url": "https://x", "backend": "backingimage", "size": 1, "virtual_size": 10 * 2**30, "progress": 100,
                "storage_class": "lh-1", "state": "ready", "message": "", "volumes": 1, "used_by": ["default/web"],
                "created": None}],
    "storageclasses": [
        {"name": "harv-rep1", "provisioner": "driver.longhorn.io", "is_default": True, "reclaim_policy": "Delete",
         "binding": "Immediate", "expansion": True, "replicas": "1", "image": None, "volumes": 3, "parameters": {},
         "created": None},
        {"name": "fast", "provisioner": "driver.longhorn.io", "is_default": False, "reclaim_policy": "Delete",
         "binding": "Immediate", "expansion": True, "replicas": "2", "image": None, "volumes": 0, "parameters": {},
         "created": None}],
    "secrets": [],
    "addons": [{"namespace": "harvester-system", "name": "harvester-seeder", "chart": "harvester-seeder",
                "version": "1.9.0", "enabled": False, "status": "AddonDisabled", "message": "", "created": None}],
}


def fulfill(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


@pytest.fixture
def ui(context, flask_server):
    context.add_init_script(
        "localStorage.setItem('harvester_ops_language','fr');"
        "localStorage.setItem('harvester_ops_current_cluster','harv-fake');")
    page = context.new_page()
    sent = []

    def lists(route, req):
        kind = req.url.split("/api/cluster-objects/harv-fake/")[1].split("?")[0]
        fulfill(route, {"items": LISTS.get(kind, [])})

    def writes(route, req):
        sent.append((req.method, req.url.split("://")[1].split("/", 1)[1], req.post_data_json))
        fulfill(route, {"action_id": "of0000000001"}, 202)

    def values(route, req):
        if req.method == "GET":
            fulfill(route, {"values": "image:\n  tag: v1.9.0\n", "enabled": False})
        else:
            writes(route, req)
    page.route("**/api/cluster-objects/**", lists)
    page.route("**/api/namespaces/harv-fake", lambda r, q: fulfill(r, [{"name": "default"}, {"name": "lab"}]))
    page.route("**/api/network-fabric/harv-fake", lambda r, q: fulfill(r, {"cluster_networks": ["mgmt", "data"]}))
    page.route("**/api/objects/**", writes)
    page.route("**/api/storageclasses/harv-fake/**", writes)
    page.route("**/api/secret/harv-fake/**", writes)          # v1.64.0 : secrets par type
    page.route("**/api/addons/harv-fake/**/values", values)
    page.route("**/api/stream/of0000000001", lambda r, q: r.fulfill(
        status=200, content_type="text/event-stream", body='event: end\ndata: {"status": "done"}\n\n'))
    page.goto(flask_server["base_url"], wait_until="domcontentloaded")
    page.wait_for_function("window.Sections && window.ObjectForms && window.App && App.getCurrentCluster()")
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    return page, sent, dialogs


def win(page, title):
    w = page.locator(".floating-panel", has_text=title).last
    expect(w).to_be_visible(timeout=8000)
    return w


def test_an_image_by_url(ui):
    page, sent, _ = ui
    page.evaluate("Sections.open('storage', 'images')")
    pane = page.locator("#tab-storage .section-pane:not([hidden])")
    new = pane.locator(".res-new")
    assert new.get_attribute("data-tip")
    new.click()
    w = win(page, "Nouvelle image")
    expect(w.locator('[name="storage_class"]')).to_have_value("harv-rep1")     # la classe par défaut
    w.locator('[name="url"]').fill("https://example.org/cirros.img")
    w.locator('button[type="submit"]').click()
    expect(w.locator(".of-msg")).to_contain_text("importée", timeout=5000)
    assert sent[-1][:2] == ("POST", "api/objects/harv-fake/image")
    assert sent[-1][2]["spec"]["url"] == "https://example.org/cirros.img"
    # une image utilisée ne se supprime pas
    expect(pane.locator("tr", has_text="leap.qcow2").locator('[data-act="delete"]')).to_be_disabled()


def test_storage_classes_default_and_delete(ui):
    page, sent, dialogs = ui
    page.evaluate("Sections.open('storage', 'classes')")
    pane = page.locator("#tab-storage .section-pane:not([hidden])")
    expect(pane.locator("tr", has_text="harv-rep1").first.locator('[data-act="default"]')).to_have_count(0)
    expect(pane.locator("tr", has_text="harv-rep1").first.locator('[data-act="delete"]')).to_be_disabled()
    pane.locator("tr", has_text="fast").locator('[data-act="default"]').click()
    page.wait_for_timeout(500)
    assert sent[-1][:2] == ("POST", "api/storageclasses/harv-fake/fast/default") and "fast" in dialogs[-1]
    pane.locator("tr", has_text="fast").locator('[data-act="delete"]').click()
    page.wait_for_timeout(500)
    assert sent[-1][:2] == ("DELETE", "api/objects/harv-fake/storageclass/fast")


def test_a_secret_with_several_keys(ui):
    page, sent, _ = ui
    page.evaluate("Sections.open('security', 'secrets')")
    page.locator("#tab-security .section-pane:not([hidden]) .res-new").click()
    w = win(page, "Nouveau secret")
    w.locator('[name="name"]').fill("web-ci")
    w.locator('[name="kv-value"]').first.fill("#cloud-config")
    w.locator("[data-kv-add]").click()
    w.locator('[name="kv-key"]').nth(1).fill("networkdata")
    w.locator('[name="kv-value"]').nth(1).fill("version: 2")
    w.locator('button[type="submit"]').click()
    expect(w.locator(".of-msg")).to_contain_text("créé", timeout=5000)
    # v1.64.0 : un Opaque passe par la route des secrets par type
    assert sent[-1][:2] == ("POST", "api/secret/harv-fake/default/web-ci/do/create")
    assert sent[-1][2] == {"namespace": "default", "name": "web-ci", "type": "Opaque",
                           "fields": {"data": {"userdata": "#cloud-config", "networkdata": "version: 2"}}}


def test_a_vm_network_form_follows_its_type(ui):
    page, sent, _ = ui
    page.evaluate("Sections.open('network', 'vmnets')")
    btn = page.locator('#tab-network [data-section-new="network"]')
    expect(btn).to_be_visible()
    page.locator('#tab-network [data-section-tab="overlay"]').click()
    expect(btn).to_be_hidden()                          # l'action suit l'onglet
    page.locator('#tab-network [data-section-tab="vmnets"]').click()
    btn.click()
    w = win(page, "Nouveau réseau de VMs")
    w.locator('[name="name"]').fill("lab")
    w.locator('[name="type"]').select_option("untagged")
    expect(w.locator('[data-f="vlan"]')).to_be_hidden()
    w.locator('[name="route_mode"]').select_option("manual")
    w.locator('[name="cidr"]').fill("192.168.10.0/24")
    w.locator('[name="gateway"]').fill("192.168.10.1")
    w.locator('[name="cluster_network"]').select_option("data")
    w.locator('button[type="submit"]').click()
    expect(w.locator(".of-msg")).to_contain_text("créé", timeout=5000)
    spec = sent[-1][2]["spec"]
    assert spec["type"] == "untagged" and "vlan" not in spec and spec["cluster_network"] == "data"
    assert spec["route_mode"] == "manual" and spec["cidr"] == "192.168.10.0/24"


def test_a_volume_from_an_image(ui):
    page, sent, _ = ui
    page.evaluate("Sections.open('storage', 'volumes')")
    page.locator('#tab-storage [data-section-new="volume"]').click()
    w = win(page, "Nouveau volume")
    w.locator('[name="name"]').fill("data")
    w.locator('[name="size"]').fill("20Gi")
    w.locator('[name="source"]').select_option("image")
    expect(w.locator('[data-f="storage_class"]')).to_be_hidden()
    w.locator('[name="image"]').select_option("default/image-a")
    w.locator('button[type="submit"]').click()
    expect(w.locator(".of-msg")).to_contain_text("créé", timeout=5000)
    assert sent[-1][2]["spec"] == {"namespace": "default", "name": "data", "size": "20Gi", "image": "default/image-a"}


def test_an_addon_configuration_window(ui):
    page, sent, dialogs = ui
    page.evaluate("App.setTab('addons')")
    page.locator("#tab-addons tr", has_text="harvester-seeder").locator('[data-act="configure"]').click()
    w = win(page, "Configuration de harvester-seeder")
    ta = w.locator('[name="values"]')
    expect(ta).to_have_value("image:\n  tag: v1.9.0\n")
    ta.fill("image:\n  tag: v1.9.1\n")
    w.locator('button[type="submit"]').click()
    expect(w.locator(".of-msg")).to_contain_text("appliquée", timeout=5000)
    assert sent[-1] == ("POST", "api/addons/harv-fake/harvester-system/harvester-seeder/values",
                        {"values": "image:\n  tag: v1.9.1\n"})


def test_every_form_control_has_a_tooltip(ui):
    page, _, _ = ui
    page.evaluate("Sections.open('storage', 'classes')")
    page.locator("#tab-storage .section-pane:not([hidden]) .res-new").click()
    w = win(page, "Nouvelle classe de stockage")
    page.wait_for_timeout(500)
    missing = w.evaluate("""el => [...el.querySelectorAll('.of-form input, .of-form select, .of-form button[type=submit]')]
        .filter(x => !x.closest('.tip, [data-tip]') && !x.getAttribute('data-tip')).map(x => x.name || x.outerHTML.slice(0, 50))""")
    assert missing == []


def test_a_section_button_sits_on_the_tab_bar(ui):
    """v1.60.0 : « Nouveau réseau de VMs » se tenait sous la ligne des
    onglets, collé à eux (remarqué par ju). Il est dans la bande des
    onglets, centré dessus, et laisse libres les boutons ronds du coin."""
    page, _, _ = ui
    page.evaluate("Sections.open('network', 'vmnets')")
    btn = page.locator('#tab-network [data-section-new="network"]')
    expect(btn).to_be_visible()
    box = lambda sel: page.locator(sel).bounding_box()            # noqa: E731
    tabs, b, head = box("#tab-network .sub-tabs-inline"), btn.bounding_box(), box("#tab-network .shutdown-header")
    assert tabs["y"] <= b["y"] and b["y"] + b["height"] <= tabs["y"] + tabs["height"]
    mid_tabs, mid_btn = tabs["y"] + tabs["height"] / 2, b["y"] + b["height"] / 2
    assert abs(mid_tabs - mid_btn) <= 6
    assert b["x"] >= tabs["x"] + tabs["width"]                            # à droite des onglets
    assert b["x"] + b["width"] <= head["x"] + head["width"] - 150          # hors des boutons du coin
