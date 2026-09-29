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
