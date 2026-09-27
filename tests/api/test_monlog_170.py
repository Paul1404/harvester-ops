"""v1.70.0 : Monitoring & Logging en fonctions pures : sorties et flux du
logging-operator (namespace de contrôle, circuits journaux / audit /
événements, état lu dans status), AlertmanagerConfig (jamais de route sans
receiver), métriques (instantané metrics.k8s.io, CPU des VMs sans le / 1000
faux des tableaux de Harvester)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import hv_monlog as ml  # noqa: E402


def test_outputs_are_written_as_the_operator_reads_them():
    o = ml.output_manifest({"kind": "Output", "namespace": "apps", "name": "es", "type": "elasticsearch",
                            "fields": {"host": "es.lan", "port": "9200", "scheme": "https", "index_name": "vms", "ssl_verify": False},
                            "secrets": {"password": {"name": "es-cred", "key": "password"}}})
    assert o["metadata"] == {"name": "es", "namespace": "apps"}
    assert o["spec"]["elasticsearch"] == {"host": "es.lan", "index_name": "vms", "port": 9200, "scheme": "https", "ssl_verify": False,
                                          "password": {"valueFrom": {"secretKeyRef": {"name": "es-cred", "key": "password"}}}}
    c = ml.output_manifest({"kind": "ClusterOutput", "namespace": "default", "name": "audit-file", "type": "file",
                            "fields": {"path": "/tmp/audit/${tag}"}, "audit": True})
    assert c["metadata"]["namespace"] == ml.CTRL_NS and c["spec"]["loggingRef"] == ml.AUDIT_REF     # forcé, comme Harvester
    assert ml.output_manifest({"name": "null", "type": "nullout"})["spec"] == {"nullout": {}}
    fwd = ml.output_manifest({"name": "f", "type": "forward", "server": {"host": "10.0.0.9", "port": 24224}})
    assert fwd["spec"]["forward"] == {"servers": [{"host": "10.0.0.9", "port": 24224}]}
    for spec, msg in (({"name": "x", "type": "ftp"}, "output type"), ({"name": "x", "type": "loki"}, "url is required"),
                      ({"name": "x", "type": "syslog", "fields": {"host": "h", "port": "99999"}}, "1 to 65535"),
                      ({"name": "x", "type": "loki", "fields": {"url": "u"}, "secrets": {"password": {"name": "s"}}}, "one of its keys"),
                      ({"name": "Bad", "type": "nullout"}, "lowercase")):
        with pytest.raises(ValueError, match=msg):
            ml.output_manifest(spec)


OUTS = [{"kind": "Output", "namespace": "apps", "name": "es", "audit": False},
        {"kind": "ClusterOutput", "namespace": ml.CTRL_NS, "name": "central", "audit": False},
        {"kind": "ClusterOutput", "namespace": ml.CTRL_NS, "name": "audit-file", "audit": True}]


def test_flows_follow_the_operators_rules():
    f = ml.flow_manifest({"kind": "Flow", "namespace": "apps", "name": "web", "local": ["es"],
                          "rules": [{"mode": "select", "labels": "app=web", "hosts": "n1 n2"}, {"mode": "exclude", "container_names": "sidecar"}]},
                         OUTS)
    assert f["spec"] == {"match": [{"select": {"hosts": ["n1", "n2"], "labels": {"app": "web"}}},
                                   {"exclude": {"container_names": ["sidecar"]}}], "localOutputRefs": ["es"]}
    assert f["metadata"]["labels"][ml.TYPE_LABEL] == "logging"
    ev = ml.flow_manifest({"kind": "ClusterFlow", "name": "events", "type": "event", "global": ["central"]}, OUTS)
    assert ev["metadata"]["namespace"] == ml.CTRL_NS
    assert ev["spec"]["match"][-1] == {"select": {"labels": {"app.kubernetes.io/name": "event-tailer"}}}
    au = ml.flow_manifest({"kind": "ClusterFlow", "name": "audit", "type": "audit", "global": ["audit-file"]}, OUTS)
    assert au["spec"]["loggingRef"] == ml.AUDIT_REF
    for spec, msg in (({"name": "x"}, "Output"),
                      ({"name": "x", "namespace": "other", "local": ["es"]}, "flow's namespace"),
                      ({"name": "x", "kind": "ClusterFlow", "local": ["es"]}, "cluster outputs only"),
                      ({"name": "x", "type": "audit", "global": ["central"]}, "logging output"),
                      ({"name": "x", "local": ["es"], "namespace": "apps", "rules": [{"namespaces": "a"}]}, "only a cluster flow"),
                      ({"name": "x", "local": ["es"], "namespace": "apps", "rules": [{"labels": "nokey"}]}, "key=value")):
        with pytest.raises(ValueError, match=msg):
            ml.flow_manifest(spec, OUTS)


def test_the_state_is_read_in_status_and_the_type_without_harvesters_trap():
    rows = ml.flow_rows([
        {"kind": "Flow", "metadata": {"namespace": "apps", "name": "a"}, "spec": {"localOutputRefs": ["es"]},
         "status": {"active": True, "problemsCount": 0}},
        {"kind": "Flow", "metadata": {"namespace": "apps", "name": "b"}, "spec": {"globalOutputRefs": ["gone"]},
         "status": {"active": False, "problems": ["dangling global output reference: gone"], "problemsCount": 1}},
        {"kind": "ClusterFlow", "metadata": {"namespace": "default", "name": "c"}, "spec": {}},
        {"kind": "Flow", "metadata": {"namespace": "apps", "name": "d"},
         "spec": {"match": [{"select": {"labels": {"app.kubernetes.io/name": "nginx"}}}]}}])
    by = {r["name"]: r for r in rows}
    assert by["a"]["state"] == "applied" and by["b"]["state"] == "problems" and "dangling" in by["b"]["problems"][0]
    assert by["c"]["state"] == "ignored"                               # hors du namespace de contrôle
    assert by["d"]["type"] == "logging" and by["d"]["state"] == "pending"   # l'UI de Harvester dirait « Event »
    assert ml.settled({"kind": "Flow", "status": {"active": True}}) == (True, "applied by the logging operator")
    assert ml.settled({"kind": "Output", "status": {"active": False}})[0] is True
    assert ml.settled({"kind": "Flow"}, deadline_passed=True)[0] is False
    assert ml.logging_health([{"metadata": {"name": "root"}, "spec": {}, "status": {"configCheckResults": {"a": True, "b": False}}}]) == [
        {"name": "root", "loggingRef": "", "ok": False, "failed": ["b"]}]


def test_an_alertmanager_config_never_has_a_route_without_receiver():
    a = ml.amc_manifest({"name": "ops", "receivers": [{"name": "hook", "type": "webhook", "url": "http://10.0.0.9/alert"}],
                         "route": {"group_by": "alertname, job", "repeat_interval": "4h",
                                   "matchers": [{"name": "severity", "value": "critical|warning", "matchType": "=~"}]}})
    assert a["metadata"]["namespace"] == ml.MON_NS
    assert a["spec"]["route"] == {"receiver": "hook", "groupBy": ["alertname", "job"], "repeatInterval": "4h",
                                  "matchers": [{"name": "severity", "value": "critical|warning", "matchType": "=~"}]}
    assert a["spec"]["receivers"] == [{"name": "hook", "webhookConfigs": [{"url": "http://10.0.0.9/alert", "sendResolved": False}]}]
    s = ml.receiver({"name": "s", "type": "slack", "api_url": {"name": "slack", "key": "url"}, "channel": "#ops"})
    assert s["slackConfigs"][0]["apiURL"] == {"name": "slack", "key": "url"}
    for spec, msg in (({"name": "x", "route": {"receiver": "none"}}, "the route needs|not found"),
                      ({"name": "x", "receivers": [{"name": "a", "type": "webhook", "url": "http://h"}], "route": {"receiver": "b"}}, "not found"),
                      ({"name": "x", "receivers": [{"name": "a", "type": "webhook", "url": "http://h"}] * 2}, "same name"),
                      ({"name": "x", "receivers": [{"name": "a", "type": "webhook", "url": "http://h"}], "route": {"group_by": "..., job"}}, "sole value"),
                      ({"name": "x", "receivers": [{"name": "a", "type": "webhook", "url": "http://h"}], "route": {"group_wait": "soon"}}, "duration"),
                      ({"name": "x", "receivers": [{"name": "a", "type": "email", "to": "a@b"}]}, "smarthost")):
        with pytest.raises(ValueError, match=msg):
            ml.amc_manifest(spec)
    rows = ml.amc_rows([a], [{"involvedObject": {"kind": "AlertmanagerConfig", "namespace": ml.MON_NS, "name": "ops"},
                              "reason": "InvalidConfiguration", "message": "rejected"}])
    assert rows[0]["rejected"] == "rejected" and rows[0]["receivers"] == [{"name": "hook", "types": ["webhook"]}]


def test_metrics_without_prometheus_and_the_cpu_query():
    snap = ml.snapshot(
        [{"metadata": {"name": "n1"}, "usage": {"cpu": "3290m", "memory": "32Gi"}}],
        [{"metadata": {"namespace": "default", "name": "virt-launcher-web-x", "labels": {"vm.kubevirt.io/name": "web"}},
          "containers": [{"name": "compute", "usage": {"cpu": "1455m", "memory": "8Gi"}}]},
         {"metadata": {"namespace": "kube-system", "name": "coredns"}, "containers": []}],
        [{"metadata": {"name": "n1"}, "status": {"allocatable": {"cpu": "8", "memory": "62Gi"}}}],
        [{"metadata": {"namespace": "default", "name": "web"}, "spec": {"domain": {"cpu": {"cores": 2}}}}])
    assert snap["hosts"] == [{"name": "n1", "cpu": 3.29, "memory": 32 * 2 ** 30, "cpu_total": 8, "memory_total": 62 * 2 ** 30}]
    assert snap["vms"][0]["name"] == "web" and snap["vms"][0]["cpu_share"] == pytest.approx(0.7275)
    assert "/ 1000" not in ml.Q_VM_CPU and 'state="running"' in ml.Q_VM_CPU
    assert ml.prom_path("up").startswith("/api/v1/namespaces/cattle-monitoring-system/services/http:rancher-monitoring-prometheus:9090/proxy/api/v1/query?query=up")
    assert ml.prom_vector({"data": {"result": [{"metric": {"namespace": "d", "name": "web"}, "value": [1, "0.5"]},
                                               {"metric": {}, "value": [1, "0.25"]}]}}) == {("d", "web"): 0.5, "": 0.25}


def test_a_file_output_needs_the_tag_in_its_path():
    """Vu en réel sur harv1 : fluentd découpe son tampon par tag, un chemin
    sans ${tag} fait échouer le contrôle de configuration et fluentd garde
    l'ancienne configuration, sans rien dire à qui a enregistré."""
    with pytest.raises(ValueError, match=r"\$\{tag\}"):
        ml.output_manifest({"kind": "ClusterOutput", "name": "f", "type": "file", "fields": {"path": "/tmp/x"}})
    o = ml.output_manifest({"kind": "ClusterOutput", "name": "f", "type": "file", "fields": {"path": ml.FILE_PATH_EXAMPLE}})
    assert o["spec"]["file"]["path"] == "/tmp/logs/${tag}/%Y/%m/%d.%H.%M"
    assert ml.OUTPUTS["file"]["example"]["path"] == ml.FILE_PATH_EXAMPLE


def test_the_fluentd_configuration_check_is_read_from_the_right_logging():
    root = {"metadata": {"name": "rancher-logging-root"}, "spec": {}, "status": {"configCheckResults": {"a": True}}}
    audit = {"metadata": {"name": "rancher-logging-kube-audit"}, "spec": {"loggingRef": ml.AUDIT_REF},
             "status": {"configCheckResults": {"b": True}}}
    assert ml.logging_of([audit, root], audit=False) is root and ml.logging_of([root, audit], audit=True) is audit
    assert ml.checks(root) == {"a": True} and ml.checks(None) == {}
    assert ml.config_verdict({"a": True}, {"a": True}) is None                     # rien de neuf encore
    assert ml.config_verdict({"a": True}, {"a": True, "c": False}) == (False, "c")  # refusée, l'ancienne reste
    assert ml.config_verdict({"a": True}, {"c": True}) == (True, "c")
    log = ('2026-09-27 10:29:37 +0000 [info]: starting fluentd-1.18.0 as dry run mode ruby="3.2.5"\n'
           '2026-09-27 10:29:37 +0000 [error]: config error file="/fluentd/etc/fluent.conf" error_class=Fluent::ConfigError '
           'error="Parameter \'path: /tmp/x.%Y%m%d%H%M_**.log\' doesn\'t have tag placeholder"\n')
    assert ml.config_error(log) == "Parameter 'path: /tmp/x.%Y%m%d%H%M_**.log' doesn't have tag placeholder"
    assert ml.config_error("") == ""
