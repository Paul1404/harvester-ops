"""harvester-ops : le menu « Monitoring & Logging » de Harvester (v1.70.0).

Relevé dans harvester-ui-extension, @rancher/shell, harvester v1.9.0 et les
charts rancher-logging / rancher-monitoring servis par harv1 (scratchpad du
chantier : parity/harvester-monitoring-logging-1.9.md) :

- journalisation (logging-operator) : Flow et Output namespacés, ClusterFlow
  et ClusterOutput SEULEMENT dans cattle-logging-system (ailleurs l'opérateur
  les ignore sans rien dire) ; trois circuits : journaux (loggingRef vide),
  audit Kubernetes (loggingRef harvester-kube-audit-log-ref), événements
  (sélection app.kubernetes.io/name=event-tailer) ; aucun webhook : l'état
  réel se lit dans status.active, status.problems et configCheckResults ;
- surveillance : AlertmanagerConfig namespacée, sans status ; le webhook de
  l'opérateur refuse une route racine sans receiver (l'interface de
  Harvester la crée ainsi) ; l'opérateur ajoute à chaque route le matcher
  namespace de l'AMC ;
- métriques : metrics.k8s.io pour l'instantané, Prometheus par le proxy de
  service quand rancher-monitoring est actif ; le « / 1000 » du CPU des VMs
  dans les tableaux de Harvester est faux (la série est en secondes).

Fonctions pures ; bin/harvester-resources.py `monlog` les applique.
"""

import re

CTRL_NS = "cattle-logging-system"
MON_NS = "cattle-monitoring-system"
G_LOG = "logging.banzaicloud.io"
K_FLOW = f"flows.{G_LOG}"
K_CFLOW = f"clusterflows.{G_LOG}"
K_OUTPUT = f"outputs.{G_LOG}"
K_COUTPUT = f"clusteroutputs.{G_LOG}"
K_LOGGING = f"loggings.{G_LOG}"
K_AMC = "alertmanagerconfigs.monitoring.coreos.com"
AUDIT_REF = "harvester-kube-audit-log-ref"
EVENT_LABEL = ("app.kubernetes.io/name", "event-tailer")
TYPE_LABEL = "harvester-ops.io/log-type"
ADDON_LOG = ("cattle-logging-system", "rancher-logging")
ADDON_MON = ("cattle-monitoring-system", "rancher-monitoring")
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
DUR_RE = re.compile(r"^([0-9]+(ms|s|m|h|d|w|y))+$")

# Les cibles que propose l'interface de Harvester, et leurs champs. Un champ
# « secret » s'écrit {valueFrom: {secretKeyRef: {name, key}}} (ou mountFrom
# pour un fichier), le secret dans le namespace de l'objet.
OUTPUTS = {
    "elasticsearch": {"text": ["host", "index_name", "user", "ca_file"], "number": ["port"],
                      "choice": {"scheme": ("http", "https")}, "secret": ["password"], "bool": ["ssl_verify"]},
    "opensearch": {"text": ["host", "index_name", "user"], "number": ["port"], "choice": {"scheme": ("http", "https")},
                   "secret": ["password"], "bool": ["ssl_verify"]},
    "loki": {"text": ["url", "tenant", "username"], "secret": ["password"], "bool": ["configure_kubernetes_labels"]},
    "splunkHec": {"text": ["hec_host", "index", "source"], "number": ["hec_port"], "choice": {"protocol": ("http", "https")},
                  "secret": ["hec_token"], "bool": ["insecure_ssl"]},
    "syslog": {"text": ["host"], "number": ["port"], "choice": {"transport": ("tls", "udp", "tcp")}, "bool": ["insecure"]},
    "kafka": {"text": ["brokers", "default_topic", "username"], "secret": ["password"]},
    "forward": {"server": True},
    "s3": {"text": ["s3_endpoint", "s3_bucket", "path"], "secret": ["aws_key_id", "aws_sec_key"]},
    "http": {"text": ["endpoint"]},
    "file": {"text": ["path"], "example": {"path": "/tmp/logs/${tag}/%Y/%m/%d.%H.%M"}},
    "nullout": {},
}
# fluentd découpe le tampon d'une sortie fichier par tag : sans ${tag} dans
# le chemin, le contrôle de configuration échoue (vu sur harv1).
FILE_PATH_EXAMPLE = OUTPUTS["file"]["example"]["path"]


def _meta(o):
    return (o or {}).get("metadata") or {}


def check_name(name, what="name"):
    name = str(name or "").strip()
    if not NAME_RE.match(name):
        raise ValueError(f"{what}: a lowercase name of 63 characters at most (letters, digits, dashes)")
    return name


def addon_state(addons, which):
    for a in addons or []:
        m = _meta(a)
        if (m.get("namespace"), m.get("name")) == which:
            return {"enabled": bool((a.get("spec") or {}).get("enabled")), "status": (a.get("status") or {}).get("status") or ""}
    return {"enabled": False, "status": "missing"}


# ---------------------------------------------------------------------------
# Journalisation : sorties
# ---------------------------------------------------------------------------

def _secret(ref, mount=False):
    name, key = str((ref or {}).get("name") or "").strip(), str((ref or {}).get("key") or "").strip()
    if not name or not key:
        raise ValueError("a secret field needs a secret and one of its keys")
    return {("mountFrom" if mount else "valueFrom"): {"secretKeyRef": {"name": check_name(name, "secret"), "key": key}}}


def output_manifest(spec):
    """{kind Output|ClusterOutput, namespace, name, type, fields {..}, secrets
    {champ: {name, key}}, audit bool, server {host, port} (forward)}."""
    kind = spec.get("kind") or "Output"
    if kind not in ("Output", "ClusterOutput"):
        raise ValueError("kind: Output or ClusterOutput")
    name = check_name(spec.get("name"), "output name")
    ns = CTRL_NS if kind == "ClusterOutput" else check_name(spec.get("namespace") or "default", "namespace")
    t = spec.get("type")
    if t not in OUTPUTS:
        raise ValueError("output type: " + ", ".join(OUTPUTS))
    shape = OUTPUTS[t]
    fields = spec.get("fields") or {}
    body = {}
    for f in shape.get("text", []):
        if str(fields.get(f) or "").strip():
            body[f] = str(fields[f]).strip()
    for f in shape.get("number", []):
        if str(fields.get(f) or "").strip():
            try:
                n = int(fields[f])
            except (TypeError, ValueError):
                raise ValueError(f"{f}: a whole number") from None
            if not 1 <= n <= 65535:
                raise ValueError(f"{f}: 1 to 65535")
            body[f] = n
    for f, choices in (shape.get("choice") or {}).items():
        if fields.get(f):
            if fields[f] not in choices:
                raise ValueError(f"{f}: " + ", ".join(choices))
            body[f] = fields[f]
    for f in shape.get("bool", []):
        if f in fields:
            body[f] = bool(fields[f])
    for f, ref in (spec.get("secrets") or {}).items():
        if f not in shape.get("secret", []):
            raise ValueError(f"{f}: not a secret field of {t}")
        if ref and (ref.get("name") or ref.get("key")):
            body[f] = _secret(ref)
    if shape.get("server"):
        srv = spec.get("server") or {}
        host = str(srv.get("host") or "").strip()
        if not host:
            raise ValueError("forward: the server host")
        entry = {"host": host}
        if srv.get("port"):
            entry["port"] = int(srv["port"])
        body["servers"] = [entry]
    required = {"elasticsearch": "host", "opensearch": "host", "loki": "url", "splunkHec": "hec_host", "syslog": "host",
                "kafka": "brokers", "s3": "s3_bucket", "http": "endpoint", "file": "path"}.get(t)
    if required and required not in body:
        raise ValueError(f"{t}: {required} is required")
    if t == "file" and "${tag}" not in body["path"]:
        raise ValueError("file: the path must contain ${tag}, fluentd splits its buffer by tag "
                         f"(for example {FILE_PATH_EXAMPLE})")
    if t == "s3" and "overwrite" in fields:
        body["overwrite"] = "true" if fields["overwrite"] else "false"     # une chaîne pour l'opérateur
    out_spec = {t: body}
    if spec.get("audit"):
        out_spec["loggingRef"] = AUDIT_REF
    return {"apiVersion": f"{G_LOG}/v1beta1", "kind": kind,
            "metadata": {"name": name, "namespace": ns}, "spec": out_spec}


def _state(o):
    """applied | problems | pending (pas encore vu) | ignored (hors circuit)."""
    st = o.get("status")
    if st is None or st == {}:
        return "pending", []
    probs = list(st.get("problems") or [])
    if probs:
        return "problems", probs
    return ("applied", []) if st.get("active") else ("inactive", [])


def output_rows(items):
    out = []
    for o in items or []:
        m, sp = _meta(o), o.get("spec") or {}
        targets = [k for k in sp if k not in ("loggingRef", "enabledNamespaces", "protected")]
        state, probs = _state(o)
        if o.get("kind") == "ClusterOutput" and m.get("namespace") != CTRL_NS:
            state, probs = "ignored", [f"a cluster output is only read in {CTRL_NS}"]
        fields, refs = {}, {}
        body = (sp.get(targets[0]) or {}) if len(targets) == 1 else {}
        for k, v in body.items():
            ref = (v.get("valueFrom") or v.get("mountFrom") or {}).get("secretKeyRef") if isinstance(v, dict) else None
            if ref:
                refs[k] = {"name": ref.get("name"), "key": ref.get("key")}      # la référence, jamais la valeur
            elif not isinstance(v, (dict, list)):
                fields[k] = v
        server = ((body.get("servers") or [{}])[0]) if targets == ["forward"] else {}
        out.append({"kind": o.get("kind") or "", "namespace": m.get("namespace"), "name": m.get("name"),
                    "types": targets, "audit": sp.get("loggingRef") == AUDIT_REF, "state": state, "problems": probs,
                    "secrets": sorted({r["name"] for r in refs.values()}), "fields": fields, "secret_refs": refs,
                    "server": {"host": server.get("host") or "", "port": server.get("port") or ""} if server else {},
                    "system": bool((m.get("labels") or {}).get("harvesterhci.io/upgradeLog")),
                    "created": m.get("creationTimestamp")})
    return sorted(out, key=lambda r: (r["kind"], r["namespace"] or "", r["name"]))


# ---------------------------------------------------------------------------
# Journalisation : flux
# ---------------------------------------------------------------------------

def _labels_of(text):
    out = {}
    for part in re.split(r"[,\n]", str(text or "")):
        part = part.strip()
        if not part:
            continue
        k, sep, v = part.partition("=")
        if not sep or not k.strip():
            raise ValueError(f"label {part!r}: key=value")
        out[k.strip()] = v.strip()
    return out


def flow_manifest(spec, outputs=()):
    """{kind Flow|ClusterFlow, namespace, name, type logging|audit|event,
    rules [{mode select|exclude, hosts, labels, namespaces, container_names}],
    local [noms d'Output], global [noms de ClusterOutput], filters [..]}.
    `outputs` = les sorties existantes (lignes de output_rows) pour refuser
    d'avance ce que l'opérateur marquerait « dangling »."""
    kind = spec.get("kind") or "Flow"
    if kind not in ("Flow", "ClusterFlow"):
        raise ValueError("kind: Flow or ClusterFlow")
    name = check_name(spec.get("name"), "flow name")
    ns = CTRL_NS if kind == "ClusterFlow" else check_name(spec.get("namespace") or "default", "namespace")
    ftype = spec.get("type") or "logging"
    if ftype not in ("logging", "audit", "event"):
        raise ValueError("type: logging, audit or event")
    local = [check_name(x, "output") for x in spec.get("local") or []]
    glob = [check_name(x, "cluster output") for x in spec.get("global") or []]
    if kind == "ClusterFlow" and local:
        raise ValueError("a cluster flow sends to cluster outputs only")
    if not local and not glob:
        raise ValueError('Requires "Output" or "Cluster Output" to be selected.')
    by = {(r["kind"], r["namespace"], r["name"]): r for r in outputs or []}
    if outputs:
        for o in local:
            r = by.get(("Output", ns, o))
            if not r:
                raise ValueError(f"output {ns}/{o} does not exist (an output must be in the flow's namespace)")
            if r["audit"] != (ftype == "audit"):
                raise ValueError(f"output {o} is {'an audit' if r['audit'] else 'a logging'} output")
        for o in glob:
            r = by.get(("ClusterOutput", CTRL_NS, o))
            if not r:
                raise ValueError(f"cluster output {o} does not exist")
            if r["audit"] != (ftype == "audit"):
                raise ValueError(f"cluster output {o} is {'an audit' if r['audit'] else 'a logging'} output")
    match = []
    for rule in spec.get("rules") or []:
        mode = rule.get("mode") or "select"
        if mode not in ("select", "exclude"):
            raise ValueError("rule: select or exclude")
        body = {}
        hosts = [h.strip() for h in re.split(r"[,\s]+", str(rule.get("hosts") or "")) if h.strip()]
        if hosts:
            body["hosts"] = hosts
        labels = _labels_of(rule.get("labels"))
        if labels:
            body["labels"] = labels
        conts = [c.strip() for c in re.split(r"[,\s]+", str(rule.get("container_names") or "")) if c.strip()]
        if conts:
            body["container_names"] = conts
        nss = [n.strip() for n in re.split(r"[,\s]+", str(rule.get("namespaces") or "")) if n.strip()]
        if nss:
            if kind != "ClusterFlow":
                raise ValueError("only a cluster flow selects namespaces")
            body["namespaces"] = [check_name(n, "namespace") for n in nss]
        if body:
            match.append({mode: body})
    if ftype == "event":
        match.append({"select": {"labels": {EVENT_LABEL[0]: EVENT_LABEL[1]}}})
    fspec = {}
    if match:
        fspec["match"] = match
    if spec.get("filters"):
        if not isinstance(spec["filters"], list):
            raise ValueError("filters: a list of fluentd filters")
        fspec["filters"] = spec["filters"]
    if local:
        fspec["localOutputRefs"] = local
    if glob:
        fspec["globalOutputRefs"] = glob
    if ftype == "audit":
        fspec["loggingRef"] = AUDIT_REF
    return {"apiVersion": f"{G_LOG}/v1beta1", "kind": kind,
            "metadata": {"name": name, "namespace": ns, "labels": {TYPE_LABEL: ftype}}, "spec": fspec}


def flow_type(o):
    """Le type voulu : le label de la console, sinon déduit, mais sans le
    piège de l'interface de Harvester (tout label app.kubernetes.io/name y
    faisait une flow « Event »)."""
    m, sp = _meta(o), o.get("spec") or {}
    t = (m.get("labels") or {}).get(TYPE_LABEL)
    if t in ("logging", "audit", "event"):
        return t
    if sp.get("loggingRef") == AUDIT_REF:
        return "audit"
    for r in sp.get("match") or []:
        if ((r.get("select") or {}).get("labels") or {}).get(EVENT_LABEL[0]) == EVENT_LABEL[1]:
            return "event"
    return "logging"


def flow_rows(items):
    out = []
    for o in items or []:
        m, sp = _meta(o), o.get("spec") or {}
        state, probs = _state(o)
        if o.get("kind") == "ClusterFlow" and m.get("namespace") != CTRL_NS:
            state, probs = "ignored", [f"a cluster flow is only read in {CTRL_NS}"]
        rules = []
        for r in sp.get("match") or []:
            for mode in ("select", "exclude"):
                if mode in r:
                    b = r[mode] or {}
                    if b.get("labels") == {EVENT_LABEL[0]: EVENT_LABEL[1]} and mode == "select":
                        continue
                    rules.append({"mode": mode, "hosts": b.get("hosts") or [], "labels": b.get("labels") or {},
                                  "namespaces": b.get("namespaces") or [], "container_names": b.get("container_names") or []})
        out.append({"kind": o.get("kind") or "", "namespace": m.get("namespace"), "name": m.get("name"),
                    "type": flow_type(o), "local": sp.get("localOutputRefs") or [], "global": sp.get("globalOutputRefs") or [],
                    "rules": rules, "filters": len(sp.get("filters") or []), "state": state, "problems": probs,
                    "system": bool((m.get("labels") or {}).get("harvesterhci.io/upgradeLog")),
                    "created": m.get("creationTimestamp")})
    return sorted(out, key=lambda r: (r["kind"], r["namespace"] or "", r["name"]))


def logging_health(loggings):
    """Le contrôle de configuration de fluentd par Logging : un false garde
    l'ancienne configuration en place."""
    out = []
    for lg in loggings or []:
        res = (lg.get("status") or {}).get("configCheckResults") or {}
        out.append({"name": _meta(lg).get("name"), "loggingRef": (lg.get("spec") or {}).get("loggingRef") or "",
                    "ok": all(res.values()) if res else None, "failed": sorted(k for k, v in res.items() if v is False)})
    return out


def logging_of(loggings, audit=False):
    """Le Logging qui rend la configuration d'un objet : celui de l'audit
    (loggingRef de Harvester) ou la racine (sans loggingRef)."""
    want = AUDIT_REF if audit else ""
    for lg in loggings or []:
        if ((lg.get("spec") or {}).get("loggingRef") or "") == want:
            return lg
    return None


def checks(lg):
    """Les contrôles de configuration de fluentd d'un Logging : {hash: bool}."""
    return dict(((lg or {}).get("status") or {}).get("configCheckResults") or {})


def config_verdict(before, after):
    """Le contrôle apparu depuis `before` : (True, hash) accepté, (False,
    hash) refusé (fluentd garde l'ancienne configuration), None rien encore."""
    new = {h: v for h, v in after.items() if h not in before}
    bad = sorted(h for h, v in new.items() if v is False)
    if bad:
        return False, bad[0]
    good = sorted(h for h, v in new.items() if v is True)
    return (True, good[0]) if good else None


def config_error(text):
    """La raison que donne fluentd dans le journal de son contrôle."""
    for line in reversed((text or "").splitlines()):
        if "[error]" in line:
            m = re.search(r'error="(.*)"\s*$', line)
            return (m.group(1) if m else line.split("[error]:", 1)[-1]).strip()[:400]
    return ""


def settled(o, deadline_passed=False):
    """(True, msg) appliqué, (False, msg) problèmes, (None, msg) en attente."""
    if o is None:
        return False, "the object disappeared"
    state, probs = _state(o)
    if state == "applied":
        return True, "applied by the logging operator"
    if state == "problems":
        return False, "; ".join(probs)
    if state == "inactive":
        return (True, "saved; no flow uses it yet") if o.get("kind") in ("Output", "ClusterOutput") else (None, "waiting")
    return (False, "not processed by the logging operator (add-on disabled?)") if deadline_passed else (None, "waiting for the logging operator")


# ---------------------------------------------------------------------------
# Surveillance : AlertmanagerConfig
# ---------------------------------------------------------------------------

RECEIVERS = ("webhook", "slack", "email", "pagerduty", "opsgenie", "msteams")


def _ref(r, what):
    name, key = str((r or {}).get("name") or "").strip(), str((r or {}).get("key") or "").strip()
    if not name or not key:
        raise ValueError(f"{what}: a secret and one of its keys")
    return {"name": check_name(name, "secret"), "key": key}


def receiver(spec):
    name = str(spec.get("name") or "").strip()
    if not name:
        raise ValueError("receiver: a name")
    t = spec.get("type")
    if t not in RECEIVERS:
        raise ValueError("receiver type: " + ", ".join(RECEIVERS))
    resolved = bool(spec.get("send_resolved"))
    if t == "webhook":
        if spec.get("url"):
            if not re.match(r"^https?://", spec["url"]):
                raise ValueError("webhook: an http(s) URL")
            cfg = {"url": spec["url"]}
        else:
            cfg = {"urlSecret": _ref(spec.get("url_secret"), "webhook URL")}
        return {"name": name, "webhookConfigs": [dict(cfg, sendResolved=resolved)]}
    if t == "slack":
        cfg = {"apiURL": _ref(spec.get("api_url"), "Slack webhook URL"), "sendResolved": resolved}
        if spec.get("channel"):
            cfg["channel"] = spec["channel"]
        return {"name": name, "slackConfigs": [cfg]}
    if t == "email":
        if not spec.get("to") or not spec.get("smarthost"):
            raise ValueError("email: to and smarthost")
        cfg = {"to": spec["to"], "smarthost": spec["smarthost"], "requireTLS": bool(spec.get("require_tls")), "sendResolved": resolved}
        if spec.get("from"):
            cfg["from"] = spec["from"]
        if spec.get("auth_username"):
            cfg["authUsername"] = spec["auth_username"]
            cfg["authPassword"] = _ref(spec.get("auth_password"), "SMTP password")
        return {"name": name, "emailConfigs": [cfg]}
    if t == "pagerduty":
        return {"name": name, "pagerdutyConfigs": [{"routingKey": _ref(spec.get("routing_key"), "routing key"), "sendResolved": resolved}]}
    if t == "opsgenie":
        return {"name": name, "opsgenieConfigs": [{"apiKey": _ref(spec.get("api_key"), "API key"), "sendResolved": resolved}]}
    return {"name": name, "msteamsConfigs": [{"webhookUrl": _ref(spec.get("webhook_url"), "Teams webhook URL"), "sendResolved": resolved}]}


def amc_manifest(spec):
    """Une AlertmanagerConfig : receivers et une route racine vers l'un
    d'eux (jamais une route sans receiver : le webhook de l'opérateur la
    refuse, piège de l'interface de Harvester)."""
    ns = check_name(spec.get("namespace") or MON_NS, "namespace")
    name = check_name(spec.get("name"), "name")
    recs = [receiver(r) for r in spec.get("receivers") or []]
    names = [r["name"] for r in recs]
    if len(set(names)) != len(names):
        raise ValueError("two receivers have the same name")
    out = {"receivers": recs}
    route = spec.get("route") or {}
    if route or recs:
        rec = route.get("receiver") or (names[0] if names else "")
        if not rec:
            raise ValueError("the route needs a receiver")
        if rec not in names:
            raise ValueError(f'receiver "{rec}" not found')
        r = {"receiver": rec}
        gb = [g.strip() for g in re.split(r"[,\s]+", str(route.get("group_by") or "")) if g.strip()]
        if len(set(gb)) != len(gb):
            raise ValueError("group by: a label twice")
        if "..." in gb and len(gb) > 1:
            raise ValueError("group by: '...' must be a sole value")
        if gb:
            r["groupBy"] = gb
        for key, field in (("group_wait", "groupWait"), ("group_interval", "groupInterval"), ("repeat_interval", "repeatInterval")):
            v = str(route.get(key) or "").strip()
            if v:
                if not DUR_RE.match(v):
                    raise ValueError(f"{key}: a duration like 30s, 5m, 4h")
                r[field] = v
        ms = []
        for mt in route.get("matchers") or []:
            if not mt.get("name"):
                continue
            op = mt.get("matchType") or "="
            if op not in ("=", "!=", "=~", "!~"):
                raise ValueError("matcher: =, !=, =~ or !~")
            if op in ("=~", "!~"):
                try:
                    re.compile(mt.get("value") or "")
                except re.error:
                    raise ValueError(f"matcher {mt['name']}: not a valid regular expression") from None
            ms.append({"name": mt["name"], "value": mt.get("value") or "", "matchType": op})
        if ms:
            r["matchers"] = ms
        out["route"] = r
    return {"apiVersion": "monitoring.coreos.com/v1alpha1", "kind": "AlertmanagerConfig",
            "metadata": {"name": name, "namespace": ns}, "spec": out}


def amc_rows(items, events=()):
    rejected = {}
    for e in events or []:
        io = e.get("involvedObject") or {}
        if io.get("kind") == "AlertmanagerConfig" and e.get("reason") == "InvalidConfiguration":
            rejected[(io.get("namespace"), io.get("name"))] = e.get("message") or ""
    out = []
    for o in items or []:
        m, sp = _meta(o), o.get("spec") or {}
        recs = [{"name": r.get("name"),
                 "types": [k.replace("Configs", "") for k in r if k.endswith("Configs")]} for r in sp.get("receivers") or []]
        route = sp.get("route") or {}
        out.append({"namespace": m.get("namespace"), "name": m.get("name"), "receivers": recs,
                    "receivers_spec": sp.get("receivers") or [],
                    "route": {"receiver": route.get("receiver") or "", "group_by": route.get("groupBy") or [],
                              "group_wait": route.get("groupWait") or "", "group_interval": route.get("groupInterval") or "",
                              "repeat_interval": route.get("repeatInterval") or "", "matchers": route.get("matchers") or []},
                    "rejected": rejected.get((m.get("namespace"), m.get("name")), ""),
                    "scope": m.get("namespace"), "created": m.get("creationTimestamp")})
    return sorted(out, key=lambda r: (r["namespace"], r["name"]))


# ---------------------------------------------------------------------------
# Métriques
# ---------------------------------------------------------------------------

def prom_path(query, start=None, end=None, step=None):
    """Chemin de Prometheus par le proxy de service de l'apiserver."""
    from urllib.parse import urlencode
    base = f"/api/v1/namespaces/{MON_NS}/services/http:rancher-monitoring-prometheus:9090/proxy/api/v1/"
    if start is None:
        return base + "query?" + urlencode({"query": query})
    return base + "query_range?" + urlencode({"query": query, "start": start, "end": end, "step": step})


# CPU des VMs : fraction de leur capacité vCPU (la série est en secondes ;
# les tableaux de Harvester divisent à tort par 1000)
Q_VM_CPU = ('sum by (namespace, name) (rate(kubevirt_vmi_vcpu_seconds_total{state="running"}[5m])) / '
            'count by (namespace, name) (kubevirt_vmi_vcpu_seconds_total{state="running"})')
Q_VM_MEM = ("sum by (namespace, name) ((kubevirt_vmi_memory_available_bytes - kubevirt_vmi_memory_unused_bytes) / "
            "kubevirt_vmi_memory_available_bytes)")
Q_VM_NET = "sum by (namespace, name) (irate(kubevirt_vmi_network_receive_bytes_total[5m]) + irate(kubevirt_vmi_network_transmit_bytes_total[5m]))"
Q_VM_DISK = "sum by (namespace, name) (irate(kubevirt_vmi_storage_read_traffic_bytes_total[5m]) + irate(kubevirt_vmi_storage_write_traffic_bytes_total[5m]))"
Q_CLUSTER = {
    "cpu": '1 - avg(irate(node_cpu_seconds_total{mode="idle"}[5m]))',
    "memory": "1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes)",
    "disk": '1 - (sum(node_filesystem_free_bytes{device!~"rootfs"}) / sum(node_filesystem_size_bytes{device!~"rootfs"}))',
    "net_in": 'sum(rate(node_network_receive_bytes_total{device!~"lo|veth.*|docker.*|flannel.*|cali.*|cbr.*"}[5m]))',
    "net_out": 'sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.*|docker.*|flannel.*|cali.*|cbr.*"}[5m]))',
}


def _q(v):
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(n|u|m|Ki|Mi|Gi|Ti|k|M|G)?\s*$", str(v or ""))
    if not m:
        return None
    mul = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1, "k": 1e3, "M": 1e6, "G": 1e9,
           "Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40}[m.group(2) or ""]
    return float(m.group(1)) * mul


def snapshot(node_metrics, pod_metrics, nodes, vmis):
    """L'instantané sans rancher-monitoring : metrics.k8s.io pour les hôtes et
    pour le conteneur compute des pods virt-launcher (une VM)."""
    cap = {_meta(n).get("name"): (n.get("status") or {}).get("allocatable") or {} for n in nodes or []}
    hosts = []
    for nm in node_metrics or []:
        name = _meta(nm).get("name")
        u = nm.get("usage") or {}
        hosts.append({"name": name, "cpu": _q(u.get("cpu")), "memory": _q(u.get("memory")),
                      "cpu_total": _q(cap.get(name, {}).get("cpu")), "memory_total": _q(cap.get(name, {}).get("memory"))})
    vcpus = {}
    for v in vmis or []:
        c = (((v.get("spec") or {}).get("domain") or {}).get("cpu") or {})
        vcpus[(_meta(v).get("namespace"), _meta(v).get("name"))] = (c.get("cores") or 1) * (c.get("sockets") or 1) * (c.get("threads") or 1)
    vms = []
    for pm in pod_metrics or []:
        m = _meta(pm)
        vm = (m.get("labels") or {}).get("vm.kubevirt.io/name") or (m.get("labels") or {}).get("kubevirt.io/domain")
        if not vm:
            continue
        comp = next((c for c in pm.get("containers") or [] if c.get("name") == "compute"), None)
        if not comp:
            continue
        cpu = _q((comp.get("usage") or {}).get("cpu"))
        n = vcpus.get((m.get("namespace"), vm))
        vms.append({"namespace": m.get("namespace"), "name": vm, "cpu": cpu, "vcpus": n,
                    "cpu_share": (cpu / n) if cpu is not None and n else None, "memory": _q((comp.get("usage") or {}).get("memory"))})
    return {"hosts": sorted(hosts, key=lambda h: h["name"]),
            "vms": sorted(vms, key=lambda v: -(v["cpu"] or 0))}


def prom_vector(resp):
    """{(namespace, name) ou "": valeur} d'une réponse /query."""
    out = {}
    for r in ((resp or {}).get("data") or {}).get("result") or []:
        met = r.get("metric") or {}
        key = (met.get("namespace"), met.get("name")) if met.get("name") else ""
        try:
            out[key] = float((r.get("value") or [0, "nan"])[1])
        except (TypeError, ValueError):
            continue
    return out
