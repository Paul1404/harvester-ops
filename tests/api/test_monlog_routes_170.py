"""v1.70.0 : Monitoring & Logging côté commande et côté routes : valeurs
secrètes changées en Secret (jamais dans la spec ni dans une réponse),
sortie refusée à la suppression tant qu'un flux s'en sert, état attendu
auprès de l'opérateur, routes réservées aux administrateurs pour écrire,
fichier privé effacé."""

import argparse
import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_monlog as ml  # noqa: E402
import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_ml", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)
PW = "a long test password"
LOG_ON = {"metadata": {"namespace": "cattle-logging-system", "name": "rancher-logging"}, "spec": {"enabled": True}}


class Kube:
    """Un cluster en mémoire ; l'opérateur marque une flow active à la
    relecture suivante."""
    logs = ""

    def __init__(self, objs):
        self.objs = dict(objs)
        self.calls = []

    def get(self, kind, ns, name):
        o = self.objs.get((kind, ns, name))
        if o is not None and kind in (ml.K_FLOW, ml.K_CFLOW):
            o["status"] = {"active": True, "problemsCount": 0}
        if o is not None and kind in (ml.K_OUTPUT, ml.K_COUTPUT):
            o["status"] = {"active": False, "problemsCount": 0}          # valide, pas encore utilisée
        return json.loads(json.dumps(o)) if o is not None else None

    def list(self, kind, ns=None, selector=None):
        return [dict(json.loads(json.dumps(o)), kind={ml.K_OUTPUT: "Output", ml.K_COUTPUT: "ClusterOutput", ml.K_FLOW: "Flow",
                                                        ml.K_CFLOW: "ClusterFlow"}.get(k, "")) for (k, _, _), o in self.objs.items() if k == kind]

    def create(self, obj):
        kind = {"Secret": "secrets", **{v: k for k, v in {ml.K_OUTPUT: "Output", ml.K_COUTPUT: "ClusterOutput", ml.K_FLOW: "Flow",
                                                          ml.K_CFLOW: "ClusterFlow", ml.K_AMC: "AlertmanagerConfig"}.items()}}[obj["kind"]]
        self.calls.append(("create", obj["kind"], obj["metadata"]["name"]))
        obj = json.loads(json.dumps(obj))
        obj["metadata"]["resourceVersion"] = "1"
        self.objs[(kind, obj["metadata"]["namespace"], obj["metadata"]["name"])] = obj
        return obj

    def replace(self, obj):
        self.calls.append(("replace", obj["kind"], obj["metadata"]["name"], obj["metadata"].get("resourceVersion")))
        return obj

    def patch(self, kind, ns, name, patch):
        self.calls.append(("patch", kind, name, patch))

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))
        self.objs.pop((kind, ns, name), None)

    def run(self, *args, input=None, timeout=None):
        self.calls.append(("run",) + args)
        return self.logs


class Checked(Kube):
    """Le contrôle de configuration de fluentd rend son verdict à la
    troisième relecture du Logging."""

    def __init__(self, objs, verdict, logs=""):
        super().__init__(objs)
        self.reads, self.verdict, self.logs = 0, verdict, logs

    def list(self, kind, ns=None, selector=None):
        if kind != ml.K_LOGGING:
            return super().list(kind, ns, selector)
        self.reads += 1
        res = {"good": True, **({"new": self.verdict} if self.reads > 3 else {})}
        return [{"metadata": {"name": "rancher-logging-root"}, "spec": {}, "status": {"configCheckResults": res}}]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    wait = hres._wait
    monkeypatch.setattr(hres, "_wait", lambda *a, **kw: wait(*a, **{"sleep": lambda s: None, **kw}))


def run(kube, tmp_path, action, spec=None, **kw):
    path = None
    if spec is not None:
        path = tmp_path / "spec.json"
        path.write_text(json.dumps(spec))
    a = argparse.Namespace(**{"action": action, "spec": str(path) if path else None, "kind": None, "namespace": None,
                              "name": None, "grace": 90, "timeout": 30, **kw})
    orig = hres.kube_from
    hres.kube_from = lambda _a: kube
    try:
        return hres.cmd_monlog(a)
    finally:
        hres.kube_from = orig


def test_a_typed_secret_becomes_a_secret_and_only_its_reference_is_kept(tmp_path):
    k = Kube({("addons.harvesterhci.io", "cattle-logging-system", "rancher-logging"): LOG_ON})
    spec = {"kind": "Output", "namespace": "apps", "name": "loki", "type": "loki", "fields": {"url": "http://loki:3100"},
            "secrets": {"password": {"name": "loki-cred", "key": "password", "value": "s3cr3t"}}}
    assert run(k, tmp_path, "output-apply", spec) == hres.EXIT_OK
    sec = k.objs[("secrets", "apps", "loki-cred")]
    assert base64.b64decode(sec["data"]["password"]).decode() == "s3cr3t"
    out = k.objs[(ml.K_OUTPUT, "apps", "loki")]
    assert "s3cr3t" not in json.dumps(out)
    assert out["spec"]["loki"]["password"] == {"valueFrom": {"secretKeyRef": {"name": "loki-cred", "key": "password"}}}
    spec["fields"]["url"] = "http://loki:3101"
    spec["secrets"]["password"]["value"] = "s3cr3t"
    assert run(k, tmp_path, "output-apply", spec) == hres.EXIT_OK            # la seconde fois : remplacement et clé posée
    assert ("replace", "Output", "loki", "1") in k.calls and any(c[0] == "patch" and c[1] == "secrets" for c in k.calls)


def test_a_flow_is_checked_against_the_outputs_and_waits_for_the_operator(tmp_path):
    k = Kube({("addons.harvesterhci.io", "cattle-logging-system", "rancher-logging"): LOG_ON,
              (ml.K_OUTPUT, "apps", "loki"): {"metadata": {"namespace": "apps", "name": "loki"}, "spec": {"loki": {"url": "u"}}}})
    with pytest.raises(ValueError, match="flow's namespace"):
        run(k, tmp_path, "flow-apply", {"kind": "Flow", "namespace": "other", "name": "f", "local": ["loki"]})
    assert run(k, tmp_path, "flow-apply", {"kind": "Flow", "namespace": "apps", "name": "f", "local": ["loki"]}) == hres.EXIT_OK
    with pytest.raises(ValueError, match="used by apps/f"):
        run(k, tmp_path, "output-delete", kind="Output", namespace="apps", name="loki")
    assert run(k, tmp_path, "flow-delete", kind="Flow", namespace="apps", name="f") == hres.EXIT_OK
    assert run(k, tmp_path, "output-delete", kind="Output", namespace="apps", name="loki") == hres.EXIT_OK


def test_with_logging_disabled_the_object_is_saved_and_said_so(tmp_path):
    k = Kube({("addons.harvesterhci.io", "cattle-logging-system", "rancher-logging"): {**LOG_ON, "spec": {"enabled": False}}})
    assert run(k, tmp_path, "output-apply", {"kind": "ClusterOutput", "name": "null", "type": "nullout"}) == hres.EXIT_OK
    assert (ml.K_COUTPUT, ml.CTRL_NS, "null") in k.objs


BAD = ('2026-09-27 [error]: config error file="/fluentd/etc/fluent.conf" error_class=Fluent::ConfigError '
       'error="Parameter \'path: /tmp/x\' doesn\'t have tag placeholder"')


def test_a_flow_is_saved_only_once_fluentd_accepted_the_configuration(tmp_path):
    base = {("addons.harvesterhci.io", "cattle-logging-system", "rancher-logging"): LOG_ON,
            (ml.K_COUTPUT, ml.CTRL_NS, "o"): {"metadata": {"namespace": ml.CTRL_NS, "name": "o"}, "spec": {"nullout": {}}}}
    ok = Checked(base, True)
    assert run(ok, tmp_path, "flow-apply", {"kind": "ClusterFlow", "name": "f", "global": ["o"]}) == hres.EXIT_OK
    assert ok.reads > 3                                                     # a attendu le verdict
    bad = Checked(base, False, BAD)
    assert run(bad, tmp_path, "flow-apply", {"kind": "ClusterFlow", "name": "f", "global": ["o"]}) == hres.EXIT_FAIL
    assert any(c[:2] == ("run", "logs") and "logging.banzaicloud.io/config-hash=new" in c for c in bad.calls)


def test_the_refusal_is_said_with_fluentd_s_own_words(tmp_path, capsys):
    base = {("addons.harvesterhci.io", "cattle-logging-system", "rancher-logging"): LOG_ON,
            (ml.K_COUTPUT, ml.CTRL_NS, "o"): {"metadata": {"namespace": ml.CTRL_NS, "name": "o"}, "spec": {"nullout": {}}}}
    run(Checked(base, False, BAD), tmp_path, "flow-apply", {"kind": "ClusterFlow", "name": "f", "global": ["o"]})
    err = capsys.readouterr().err
    assert "doesn't have tag placeholder" in err and "previous configuration stays" in err


def test_saving_an_unchanged_object_does_not_wait_for_a_check(tmp_path):
    base = {("addons.harvesterhci.io", "cattle-logging-system", "rancher-logging"): LOG_ON,
            (ml.K_COUTPUT, ml.CTRL_NS, "o"): {"metadata": {"namespace": ml.CTRL_NS, "name": "o"}, "spec": {"nullout": {}}}}
    k = Checked(base, False, BAD)
    spec = {"kind": "ClusterFlow", "name": "f", "global": ["o"]}
    k.objs[(ml.K_CFLOW, ml.CTRL_NS, "f")] = {**hres.hml.flow_manifest(spec, hres.hml.output_rows(k.list(ml.K_COUTPUT))),
                                             "metadata": {"namespace": ml.CTRL_NS, "name": "f", "resourceVersion": "3"}}
    assert run(k, tmp_path, "flow-apply", spec) == hres.EXIT_OK
    assert not any(c[0] in ("replace", "create") for c in k.calls)


# -- routes ------------------------------------------------------------------------

@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(wapp, "AUTH_OPEN_ALLOWED", False)
    monkeypatch.setattr(wapp, "HTPASSWD_PATH", tmp_path / "none")
    monkeypatch.setattr(wapp, "ROLES_PATH", tmp_path / "none.yaml")
    monkeypatch.setattr(wapp, "_roles_cache", {"mtime": None, "data": None})
    monkeypatch.setattr(wapp, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(wapp, "_ACCOUNTS", {"store": None})
    monkeypatch.setattr(wapp, "_LOCAL_SESSIONS", acc.LocalSessions())
    monkeypatch.setattr(wapp, "load_config", lambda: {"clusters": [{"name": "harv1", "kubeconfig": "/kc"}]})
    monkeypatch.setattr(wapp, "_cluster_reachable", lambda kc: True)
    monkeypatch.setattr(wapp, "_capi_work_dir", lambda: tmp_path)
    wapp._accounts().create("adm", PW, "admin")
    wapp._accounts().create("ops", PW, "operator")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": []}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        files = {cmd[i + 1]: Path(cmd[i + 1]).read_text() for i, a in enumerate(cmd) if a == "--spec"}
        w["actions"].append((label, cmd, files))
        if after:
            after()

        class Run:
            id = "ml0000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    objs = {"addons.harvesterhci.io": {"items": [LOG_ON, {"metadata": {"namespace": "cattle-monitoring-system", "name": "rancher-monitoring"},
                                                          "spec": {"enabled": False}}]},
            ml.K_OUTPUT: {"items": [{"metadata": {"namespace": "apps", "name": "loki"},
                                     "spec": {"loki": {"url": "u", "password": {"valueFrom": {"secretKeyRef": {"name": "c", "key": "p"}}}}},
                                     "status": {"active": True}}]},
            ml.K_FLOW: {"items": [{"metadata": {"namespace": "apps", "name": "f"}, "spec": {"localOutputRefs": ["loki"]},
                                   "status": {"active": True}}]},
            "namespaces": {"items": [{"metadata": {"name": "apps"}}]}}
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: objs.get(a[1], {"items": []}))
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_section_reads_state_without_any_secret_value(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/monlog/harv1", headers=auth("eye")).get_json()
    assert d["logging"]["enabled"] and not d["monitoring"]["enabled"]
    [o] = d["outputs"]
    assert o["state"] == "applied" and o["secret_refs"] == {"password": {"name": "c", "key": "p"}} and o["fields"] == {"url": "u"}
    assert d["flows"][0]["local"] == ["loki"] and "loki" in d["shapes"]


def test_writes_are_for_administrators_and_secret_values_leave_through_a_private_file(world):
    spec = {"kind": "Output", "namespace": "apps", "name": "es", "type": "elasticsearch", "fields": {"host": "es"},
            "secrets": {"password": {"name": "es", "key": "p", "value": "hunter2"}}}
    with wapp.app.test_client() as c:
        assert c.post("/api/monlog/harv1/do/output-apply", json={"spec": spec}, headers=auth("ops")).status_code == 403
        r = c.post("/api/monlog/harv1/do/output-apply", json={"spec": {**spec, "type": "ftp"}}, headers=auth("adm"))
        assert r.status_code == 400 and "hunter2" not in r.get_data(as_text=True)
        assert c.post("/api/monlog/harv1/do/output-apply", json={"spec": spec}, headers=auth("adm")).status_code == 202
        assert c.post("/api/monlog/harv1/do/flow-apply", json={"spec": {"name": "f2", "namespace": "apps", "local": ["loki"],
                                                                         "filters_yaml": "- tag_normaliser: {}"}},
                      headers=auth("adm")).status_code == 202
        assert c.post("/api/monlog/harv1/do/flow-apply", json={"spec": {"name": "f3", "local": ["x"], "filters_yaml": "a: ["}},
                      headers=auth("adm")).status_code == 400
        assert c.post("/api/monlog/harv1/do/amc-delete", json={"kind": "AlertmanagerConfig", "namespace": "cattle-monitoring-system",
                                                               "name": "ops"}, headers=auth("adm")).status_code == 202
    (l1, c1, f1), (_, _, f2), (l3, c3, _) = world["actions"]
    assert l1 == "monlog:output-apply:apps/es" and "hunter2" in list(f1.values())[0] and "hunter2" not in " ".join(c1)
    assert not any(Path(p).exists() for p in f1)                          # effacé après l'action
    assert json.loads(list(f2.values())[0])["filters"] == [{"tag_normaliser": {}}]
    assert c3[-6:] == ["--kind", "AlertmanagerConfig", "--namespace", "cattle-monitoring-system", "--name", "ops"]
