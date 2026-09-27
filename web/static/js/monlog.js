/**
 * harvester-ops : le menu « Monitoring & Logging » de Harvester (v1.70.0)
 *
 * Quatre onglets de la section du même nom :
 * - Métriques : l'instantané metrics.k8s.io (hôtes, VMs) et, si
 *   rancher-monitoring répond, les jauges du cluster et des VMs tirées de
 *   Prometheus (CPU des VMs sans le « / 1000 » faux des tableaux Harvester) ;
 * - Alertmanager : les AlertmanagerConfig, avec receivers et route (jamais
 *   une route sans receiver), et le rejet dit par l'opérateur ;
 * - Flows et Outputs : flux et sorties du logging-operator, circuits
 *   journaux / audit / événements, état réel lu dans status (appliqué,
 *   problèmes, non traité), sorties de cluster posées d'office dans
 *   cattle-logging-system ; une valeur de champ secret devient un Secret.
 */
const MonLog = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 13) => (window.Icons ? Icons.svg(n, { size }) : '');
  const REFRESH_MS = 15000;
  const CTRL_NS = 'cattle-logging-system';
  const MON_NS = 'cattle-monitoring-system';
  const pct = (v) => (v == null || Number.isNaN(v) ? '–' : `${Math.round(v * 1000) / 10} %`);
  const bytes = (n) => {
    if (n == null || Number.isNaN(n)) return '–';
    const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let i = 0, v = Number(n);
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
  };
  const cores = (n) => (n == null ? '–' : `${Math.round(n * 100) / 100}`);

  let cur = null;     // { cluster, host, kind, timer, data, metrics }

  async function call(method, url, body) {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' },
                                 body: body ? JSON.stringify(body) : undefined });
    let d = {};
    try { d = await r.json(); } catch { /* sans corps */ }
    if (!r.ok) throw new Error(d.hint || d.error || `HTTP ${r.status}`);
    return d;
  }
  const getJSON = (url) => fetch(url).then(r => (r.ok ? r.json() : null)).catch(() => null);
  const badge = (cls, text, tip) =>
    `<span class="badge ${cls}${tip ? ' tip' : ''}"${tip ? ` data-tip="${esc(tip)}"` : ''}>${esc(text)}</span>`;

  function follow(id, into, text, onDone) {
    if (window.Dock && Dock.poll) Dock.poll();
    if (window.VMActions && VMActions.follow) VMActions.follow(id, into, text, onDone);
    else if (into) into.textContent = tr('bk.started', { id });
  }

  // clés littérales (contrôle de parité i18n)
  const STATE = () => ({
    applied: ['ok', tr('ml.st.applied'), tr('ml.t.applied')], problems: ['fail', tr('ml.st.problems'), ''],
    inactive: ['dim', tr('ml.st.inactive'), tr('ml.t.inactive')], pending: ['info', tr('ml.st.pending'), tr('ml.t.pending')],
    ignored: ['warn', tr('ml.st.ignored'), ''],
  });
  const FTYPE = () => ({ logging: tr('ml.type.logging'), audit: tr('ml.type.audit'), event: tr('ml.type.event') });

  function stateBadge(r) {
    const [cls, text, tip] = STATE()[r.state] || ['dim', r.state, ''];
    return badge(cls, text, r.problems && r.problems.length ? r.problems.join(' ; ') : tip);
  }

  // -- cycle de vie ---------------------------------------------------------
  function start(cluster, host) {
    stop();
    const kind = ['metrics', 'alerts', 'flows', 'outputs'].includes(host.dataset.ml) ? host.dataset.ml : 'metrics';
    cur = { cluster, host, kind, data: null, metrics: null };
    host.innerHTML = `<div class="card na-card ml-card">
        <div class="res-tools"><span class="res-count"></span>
          ${kind === 'flows' ? `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-ml="new-flow" data-tip="${esc(tr('ml.t.newFlow'))}">${icon('add')} ${esc(tr('ml.newFlow'))}</button>
            <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-ml="new-cflow" data-tip="${esc(tr('ml.t.newCflow'))}">${icon('add')} ${esc(tr('ml.newCflow'))}</button>` : ''}
          ${kind === 'outputs' ? `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-ml="new-output" data-tip="${esc(tr('ml.t.newOutput'))}">${icon('add')} ${esc(tr('ml.newOutput'))}</button>
            <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-ml="new-coutput" data-tip="${esc(tr('ml.t.newCoutput'))}">${icon('add')} ${esc(tr('ml.newCoutput'))}</button>` : ''}
          ${kind === 'alerts' ? `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-ml="new-amc" data-tip="${esc(tr('ml.t.newAmc'))}">${icon('add')} ${esc(tr('ml.newAmc'))}</button>` : ''}
          <button type="button" class="btn btn-sm btn-secondary tip" data-ml="refresh" data-tip="${esc(tr('res.refreshTip'))}">${icon('refresh')} ${esc(tr('overview.refresh'))}</button>
        </div>
        <div class="res-feedback" data-ml="feedback"></div>
        <div data-ml="body"><p class="form-hint">${esc(tr('common.loading'))}</p></div></div>`;
    host.querySelector('.ml-card').addEventListener('click', onClick);
    cur.timer = setInterval(() => { if (cur && cur.host.isConnected && !cur.host.closest('[hidden]')) load(); }, REFRESH_MS);
    cur.ready = load();
    return cur.ready;
  }

  function stop() {
    if (cur && cur.timer) clearInterval(cur.timer);
    cur = null;
  }

  async function load() {
    if (!cur) return;
    const c = cur;
    const [d, m] = await Promise.all([getJSON(`/api/monlog/${enc(c.cluster)}`),
      c.kind === 'metrics' ? getJSON(`/api/monlog/${enc(c.cluster)}/metrics`) : Promise.resolve(null)]);
    if (c !== cur) return;
    c.data = d;
    c.metrics = m;
    render();
  }

  function banner(text, addon) {
    return `<div class="sto-finding sev-action"><div class="sto-finding-title">${icon('warn')} ${esc(text)}</div>
      ${addon ? `<button type="button" class="btn btn-sm btn-secondary tip" data-ml="open-addons" data-tip="${esc(tr('dev.t.openAddons'))}">${icon('plug')} ${esc(tr('tab.addons'))}</button>` : ''}</div>`;
  }

  function render() {
    if (!cur || !cur.host.isConnected) return;
    const body = cur.host.querySelector('[data-ml="body"]');
    const d = cur.data;
    if (!d || d.error || d.unreachable) {
      body.innerHTML = `<div class="sto-finding sev-critical"><div class="sto-finding-title">${esc((d && d.error) || tr('fabric.unreachable'))}</div></div>`;
      return;
    }
    const count = cur.host.querySelector('.res-count');
    if (cur.kind === 'metrics') { count.textContent = ''; body.innerHTML = metrics(); return; }
    if (cur.kind === 'alerts') {
      count.textContent = tr('ml.countAmc', { n: d.amcs.length });
      body.innerHTML = (!d.monitoring.enabled ? banner(tr('ml.monOff'), true)
        : d.monitoring.alertmanager === false ? banner(tr('ml.amOff'), true) : '')
        + `<p class="form-hint">${esc(tr('ml.amcHint'))}</p>` + amcTable(d.amcs);
      return;
    }
    const logOff = !d.logging.enabled ? banner(tr('ml.logOff'), true) : '';
    const broken = d.loggings.filter(l => l.ok === false);
    const check = broken.length ? `<div class="sto-finding sev-critical"><div class="sto-finding-title">${icon('fail')} ${esc(tr('ml.configCheck', { list: broken.map(l => l.name).join(', ') }))}</div></div>` : '';
    if (cur.kind === 'flows') {
      const rows = d.flows.filter(f => !f.system);
      count.textContent = tr('ml.countFlows', { n: rows.length });
      body.innerHTML = logOff + check + flowTable(rows);
    } else {
      const rows = d.outputs.filter(o => !o.system);
      count.textContent = tr('ml.countOutputs', { n: rows.length });
      body.innerHTML = logOff + check + outputTable(rows, d.flows);
    }
  }

  // -- Métriques ----------------------------------------------------------------
  function metrics() {
    const m = cur.metrics;
    if (!m || m.error) return `<p class="res-error">${esc((m && m.error) || tr('fabric.unreachable'))}</p>`;
    const p = m.prometheus;
    const tiles = p ? `<div class="hs-gauges">${[['cpu', tr('hs.gauge.cpu')], ['memory', tr('hs.gauge.memory')], ['disk', tr('hs.gauge.storage')]]
      .map(([k, l]) => `<div class="hs-gauge tip" data-tip="${esc(tr('ml.t.clusterGauge'))}"><div class="hs-gauge-head"><strong>${esc(l)}</strong><span class="res-dim">${pct(p.cluster[k])}</span></div>
          <div class="hs-gauge-bar"><i style="width:${Math.min(100, (p.cluster[k] || 0) * 100)}%"></i></div></div>`).join('')}
        <div class="hs-gauge"><div class="hs-gauge-head"><strong>${esc(tr('ml.network'))}</strong>
          <span class="res-dim">${esc(tr('ml.inOut', { in: bytes(p.cluster.net_in), out: bytes(p.cluster.net_out) }))}/s</span></div></div></div>` : '';
    const promVms = p && p.vms.length ? `<h4 class="hs-sub">${esc(tr('ml.vmsProm'))}</h4><table class="data-table"><thead><tr><th>VM</th>
        <th class="tip" data-tip="${esc(tr('ml.t.vmCpu'))}">CPU</th><th>${esc(tr('hs.gauge.memory'))}</th><th>${esc(tr('ml.network'))}</th><th>${esc(tr('ml.disk'))}</th></tr></thead><tbody>
        ${p.vms.map(v => `<tr><td><strong>${esc(v.name)}</strong> <span class="res-dim">${esc(v.namespace)}</span></td><td>${pct(v.cpu)}</td>
          <td>${pct(v.memory)}</td><td>${bytes(v.net)}/s</td><td>${bytes(v.disk)}/s</td></tr>`).join('')}</tbody></table>` : '';
    const hosts = `<h4 class="hs-sub">${esc(tr('ml.hosts'))}</h4><table class="data-table"><thead><tr><th>${esc(tr('upg.host'))}</th>
        <th>CPU</th><th>${esc(tr('hs.gauge.memory'))}</th></tr></thead><tbody>
        ${m.hosts.map(h => `<tr><td><strong>${esc(h.name)}</strong></td><td>${cores(h.cpu)} / ${cores(h.cpu_total)} ${esc(tr('ml.cores'))}</td>
          <td>${bytes(h.memory)} / ${bytes(h.memory_total)}</td></tr>`).join('')}</tbody></table>`;
    const vms = `<h4 class="hs-sub">${esc(tr('ml.vmsNow'))}</h4>${m.vms.length ? `<table class="data-table"><thead><tr><th>VM</th>
        <th class="tip" data-tip="${esc(tr('ml.t.vmNow'))}">CPU</th><th>${esc(tr('hs.gauge.memory'))}</th></tr></thead><tbody>
        ${m.vms.map(v => `<tr><td><strong>${esc(v.name)}</strong> <span class="res-dim">${esc(v.namespace)}</span></td>
          <td>${cores(v.cpu)} ${esc(tr('ml.cores'))}${v.cpu_share != null ? ` <span class="res-dim">(${pct(v.cpu_share)} ${esc(tr('ml.ofVcpus', { n: v.vcpus }))})</span>` : ''}</td>
          <td>${bytes(v.memory)}</td></tr>`).join('')}</tbody></table>` : `<p class="form-hint">${esc(tr('ml.noVm'))}</p>`}`;
    return (!m.monitoring ? banner(tr('ml.promOff'), true) : !p ? banner(tr('ml.promDown'), false) : '')
      + `<p class="form-hint">${esc(p ? tr('ml.srcProm') : tr('ml.srcNow'))}</p>` + tiles + promVms + hosts + vms;
  }

  // -- Tables --------------------------------------------------------------------
  function outputTable(rows, flows) {
    if (!rows.length) return `<p class="form-hint">${esc(tr('ml.noOutput'))}</p>`;
    const used = (r) => flows.filter(f => (r.kind === 'Output' && f.namespace === r.namespace && f.local.includes(r.name))
      || (r.kind === 'ClusterOutput' && f.global.includes(r.name))).map(f => `${f.namespace}/${f.name}`);
    return `<table class="data-table"><thead><tr><th>${esc(tr('res.col.name'))}</th><th>${esc(tr('res.col.type'))}</th>
      <th>${esc(tr('ml.circuit'))}</th><th>${esc(tr('res.col.state'))}</th><th>${esc(tr('ml.usedBy'))}</th><th></th></tr></thead><tbody>
      ${rows.map(r => { const u = used(r); return `<tr data-kind="${esc(r.kind)}" data-ns="${esc(r.namespace)}" data-name="${esc(r.name)}">
        <td><strong>${esc(r.name)}</strong> <span class="res-dim">${esc(r.kind === 'ClusterOutput' ? tr('ml.cluster') : r.namespace)}</span></td>
        <td><code>${esc(r.types.join(', ') || '–')}</code>${r.secrets.length ? ` <span class="res-dim tip" data-tip="${esc(tr('ml.t.secrets'))}">${icon('lock')} ${esc(r.secrets.join(', '))}</span>` : ''}</td>
        <td>${esc(r.audit ? tr('ml.type.audit') : tr('ml.type.logging'))}</td><td>${stateBadge(r)}</td>
        <td>${u.map(x => `<code class="res-key">${esc(x)}</code>`).join(' ') || '–'}</td>
        <td class="tpl-acts"><button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-ml="edit-output" data-tip="${esc(tr('ml.t.edit'))}">${icon('edit')}</button>
          <button type="button" class="btn btn-sm btn-danger tip needs-admin" data-ml="del-output" ${u.length ? 'disabled' : ''}
            data-tip="${esc(u.length ? tr('ml.t.inUse') : tr('ml.t.delete'))}">${icon('trash')}</button></td></tr>`; }).join('')}</tbody></table>`;
  }

  function flowTable(rows) {
    if (!rows.length) return `<p class="form-hint">${esc(tr('ml.noFlow'))}</p>`;
    const rule = (r) => `${r.mode === 'exclude' ? '−' : '+'} ${[...r.hosts.map(h => `host ${h}`), ...Object.entries(r.labels).map(([k, v]) => `${k}=${v}`),
      ...r.namespaces.map(n => `ns ${n}`), ...r.container_names.map(c => `container ${c}`)].join(', ')}`;
    return `<table class="data-table"><thead><tr><th>${esc(tr('res.col.name'))}</th><th>${esc(tr('ml.circuit'))}</th>
      <th>${esc(tr('ml.selects'))}</th><th>${esc(tr('ml.outputs'))}</th><th>${esc(tr('res.col.state'))}</th><th></th></tr></thead><tbody>
      ${rows.map(r => `<tr data-kind="${esc(r.kind)}" data-ns="${esc(r.namespace)}" data-name="${esc(r.name)}">
        <td><strong>${esc(r.name)}</strong> <span class="res-dim">${esc(r.kind === 'ClusterFlow' ? tr('ml.cluster') : r.namespace)}</span></td>
        <td>${esc(FTYPE()[r.type] || r.type)}</td>
        <td class="res-dim">${r.rules.length ? r.rules.map(x => esc(rule(x))).join('<br>') : esc(tr('ml.all'))}${r.filters ? ` · ${esc(tr('ml.filters', { n: r.filters }))}` : ''}</td>
        <td>${[...r.local, ...r.global].map(o => `<code class="res-key">${esc(o)}</code>`).join(' ')}</td><td>${stateBadge(r)}</td>
        <td class="tpl-acts"><button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-ml="edit-flow" data-tip="${esc(tr('ml.t.edit'))}">${icon('edit')}</button>
          <button type="button" class="btn btn-sm btn-danger tip needs-admin" data-ml="del-flow" data-tip="${esc(tr('ml.t.delete'))}">${icon('trash')}</button></td></tr>`).join('')}</tbody></table>`;
  }

  function amcTable(rows) {
    if (!rows.length) return `<p class="form-hint">${esc(tr('ml.noAmc'))}</p>`;
    return `<table class="data-table"><thead><tr><th>${esc(tr('res.col.name'))}</th><th>${esc(tr('ml.receivers'))}</th>
      <th>${esc(tr('ml.route'))}</th><th>${esc(tr('res.col.state'))}</th><th></th></tr></thead><tbody>
      ${rows.map(r => `<tr data-kind="AlertmanagerConfig" data-ns="${esc(r.namespace)}" data-name="${esc(r.name)}">
        <td><strong>${esc(r.name)}</strong> <span class="res-dim">${esc(r.namespace)}</span></td>
        <td>${r.receivers.map(x => `<code class="res-key">${esc(x.name)}</code> <span class="res-dim">${esc(x.types.join(', '))}</span>`).join('<br>')}</td>
        <td class="res-dim">${esc(r.route.receiver || '–')}${r.route.group_by.length ? ` · ${esc(r.route.group_by.join(', '))}` : ''}${r.route.matchers.length ? ` · ${esc(r.route.matchers.map(x => `${x.name}${x.matchType}${x.value}`).join(', '))}` : ''}</td>
        <td>${r.rejected ? badge('fail', tr('ml.st.rejected'), r.rejected) : badge('ok', tr('ml.st.accepted'), tr('ml.t.accepted'))}</td>
        <td class="tpl-acts"><button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-ml="edit-amc" data-tip="${esc(tr('ml.t.edit'))}">${icon('edit')}</button>
          <button type="button" class="btn btn-sm btn-danger tip needs-admin" data-ml="del-amc" data-tip="${esc(tr('ml.t.delete'))}">${icon('trash')}</button></td></tr>`).join('')}</tbody></table>`;
  }

  function say(html) { const fb = cur && cur.host.querySelector('[data-ml="feedback"]'); if (fb) fb.innerHTML = html; }

  async function post(action, body, doneText, into) {
    const c = cur;
    const msg = into || (c && c.host.querySelector('[data-ml="feedback"]'));
    try {
      const out = await call('POST', `/api/monlog/${enc(c.cluster)}/do/${action}`, body);
      follow(out.action_id, msg, doneText, () => { if (c === cur) setTimeout(load, 1500); });
      return true;
    } catch (err) {
      if (msg) msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      return false;
    }
  }

  function onClick(e) {
    const b = e.target.closest('[data-ml]');
    if (!b || !cur) return;
    const act = b.dataset.ml;
    const row = b.closest('tr[data-name]');
    const key = row ? { kind: row.dataset.kind, namespace: row.dataset.ns, name: row.dataset.name } : null;
    if (!cur.data && act !== 'refresh' && act !== 'open-addons') {
      // clic avant l'arrivée des données : on le rejoue une fois lues
      const c = cur;
      return c.ready && c.ready.then(() => { if (c === cur && cur.data) onClick(e); }, () => {});
    }
    const d = cur.data || {};
    if (act === 'refresh') return load();
    if (act === 'open-addons' && window.Sections) return Sections.open('addons', 'list');
    if (act === 'new-output') return outputForm({ kind: 'Output' });
    if (act === 'new-coutput') return outputForm({ kind: 'ClusterOutput' });
    if (act === 'edit-output' && key) return outputForm(d.outputs.find(o => o.kind === key.kind && o.namespace === key.namespace && o.name === key.name));
    if (act === 'new-flow') return flowForm({ kind: 'Flow' });
    if (act === 'new-cflow') return flowForm({ kind: 'ClusterFlow' });
    if (act === 'edit-flow' && key) return flowForm(d.flows.find(o => o.kind === key.kind && o.namespace === key.namespace && o.name === key.name));
    if (act === 'new-amc') return amcForm(null);
    if (act === 'edit-amc' && key) return amcForm(d.amcs.find(o => o.namespace === key.namespace && o.name === key.name));
    if (act.startsWith('del-') && key) {
      if (!confirm(tr('ml.confirm.delete', { name: `${key.namespace}/${key.name}` }))) return;
      const action = act === 'del-output' ? 'output-delete' : act === 'del-flow' ? 'flow-delete' : 'amc-delete';
      return post(action, key, tr('ml.done.delete', { name: key.name }));
    }
  }

  // -- Fenêtres ---------------------------------------------------------------------
  function win(id, title, bodyHtml, height = 640) {
    const panel = FloatingPanels.open({ id, icon: 'activity', width: 700, height, title: `${title} · ${cur.cluster}`,
      bodyHtml: `<form class="of-form" autocomplete="off">${bodyHtml}
        <div class="bk-form-actions"><button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('bk.submitTip'))}">${icon('ok')} ${esc(tr('na.save'))}</button></div>
        <div class="of-msg" role="status"></div></form>` });
    return panel.el.querySelector('.of-form');
  }
  const field = (name, label, input, tip, cls = '') => `<label class="bk-field of-field ${cls}" data-f="${name}"><span>${esc(label)}</span>${
    input.replace(/^<(input|select|textarea)/, `<$1 class="tip" data-tip="${esc(tip || '')}"`)}</label>`;
  const opts = (list, sel) => list.map(v => (Array.isArray(v) ? v : [v, v]))
    .map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(sel) ? 'selected' : ''}>${esc(l)}</option>`).join('');
  const nsSelect = (sel) => `<select name="namespace">${opts((cur.data.namespaces || []).filter(n => !/^(cattle-|kube-|fleet-|longhorn-|harvester-system|local$)/.test(n) || n === sel), sel || 'default')}</select>`;

  function secretField(name, ref) {
    return `<div class="bk-field of-field ml-secret" data-secret="${esc(name)}"><span>${esc(name)} ${icon('lock')}</span>
      <div class="ml-secret-row">
        <input class="tip" name="s-${esc(name)}-name" placeholder="${esc(tr('ml.secretName'))}" value="${esc((ref || {}).name || '')}" data-tip="${esc(tr('ml.t.secretName'))}">
        <input class="tip" name="s-${esc(name)}-key" placeholder="${esc(tr('ml.secretKey'))}" value="${esc((ref || {}).key || '')}" data-tip="${esc(tr('ml.t.secretKey'))}">
        <input class="tip" type="password" autocomplete="new-password" name="s-${esc(name)}-value" placeholder="${esc(tr('ml.secretValue'))}" data-tip="${esc(tr('ml.t.secretValue'))}">
      </div></div>`;
  }

  function submitWith(form, build, action, doneText) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = form.querySelector('.of-msg');
      let spec;
      try { spec = build(form); } catch (err) { msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`; return; }
      await post(action, { spec }, doneText(spec), msg);
    });
  }

  function outputForm(r) {
    const d = cur.data;
    const edit = !!(r && r.name);
    const type = edit ? r.types[0] : 'loki';
    const form = win(`ml-out-${cur.cluster}-${edit ? r.name : 'new'}`, edit ? tr('ml.editOutput', { name: r.name })
      : r.kind === 'ClusterOutput' ? tr('ml.newCoutput') : tr('ml.newOutput'),
    `<p class="form-hint">${esc(r.kind === 'ClusterOutput' ? tr('ml.coutputHint') : tr('ml.outputHint'))}</p>
      ${field('name', tr('bk.f.name'), `<input name="name" required value="${esc(edit ? r.name : '')}" ${edit ? 'readonly' : ''}>`, tr('ml.t.name'))}
      ${r.kind === 'Output' ? field('namespace', tr('ml.namespace'), edit ? `<input name="namespace" value="${esc(r.namespace)}" readonly>` : nsSelect('default'), tr('ml.t.namespace')) : ''}
      <label class="bk-check tip" data-tip="${esc(tr('ml.t.audit'))}"><input type="checkbox" name="audit" ${edit && r.audit ? 'checked' : ''} ${edit ? 'disabled' : ''}> <span>${esc(tr('ml.auditOnly'))}</span></label>
      ${field('type', tr('res.col.type'), `<select name="type" ${edit ? 'disabled' : ''}>${opts(Object.keys(d.shapes), type)}</select>`, tr('ml.t.outType'))}
      <div data-ml-fields></div>`);
    const paint = () => {
      const t = form.querySelector('[name="type"]').value;
      const sh = d.shapes[t] || {};
      const f = (edit && r.fields) || {};
      let html = '';
      const ex = sh.example || {};                 // chemin d'une sortie fichier : ${tag} exigé par fluentd
      (sh.text || []).forEach(n => { html += field(n, n, `<input name="f-${n}" value="${esc(f[n] || (!edit && ex[n]) || '')}" placeholder="${esc(ex[n] || '')}">`, tr('ml.t.field')); });
      (sh.number || []).forEach(n => { html += field(n, n, `<input name="f-${n}" type="number" min="1" max="65535" value="${esc(f[n] || '')}">`, tr('ml.t.field')); });
      Object.entries(sh.choice || {}).forEach(([n, ch]) => { html += field(n, n, `<select name="f-${n}"><option value=""></option>${opts(ch, f[n] || '')}</select>`, tr('ml.t.field')); });
      (sh.bool || []).forEach(n => { html += `<label class="bk-check"><input type="checkbox" name="b-${n}" ${f[n] ? 'checked' : ''}> <span>${esc(n)}</span></label>`; });
      (sh.secret || []).forEach(n => { html += secretField(n, (edit && r.secret_refs || {})[n]); });
      if (sh.server) {
        const s = (edit && r.server) || {};
        html += field('host', 'host', `<input name="srv-host" value="${esc(s.host || '')}">`, tr('ml.t.field'))
          + field('port', 'port', `<input name="srv-port" type="number" value="${esc(s.port || '')}">`, tr('ml.t.field'));
      }
      if (!html) html = `<p class="form-hint">${esc(tr('ml.noField'))}</p>`;
      form.querySelector('[data-ml-fields]').innerHTML = html;
    };
    form.querySelector('[name="type"]').addEventListener('change', paint);
    paint();
    submitWith(form, (f) => {
      const t = f.querySelector('[name="type"]').value;
      const sh = d.shapes[t] || {};
      const fields = {};
      [...(sh.text || []), ...(sh.number || []), ...Object.keys(sh.choice || {})].forEach(n => {
        const v = f.querySelector(`[name="f-${n}"]`).value.trim();
        if (v) fields[n] = v;
      });
      (sh.bool || []).forEach(n => { fields[n] = f.querySelector(`[name="b-${n}"]`).checked; });
      const secrets = {};
      (sh.secret || []).forEach(n => {
        const s = { name: f.querySelector(`[name="s-${n}-name"]`).value.trim(), key: f.querySelector(`[name="s-${n}-key"]`).value.trim(),
                    value: f.querySelector(`[name="s-${n}-value"]`).value };
        if (s.name || s.key || s.value) secrets[n] = s;
      });
      const spec = { kind: r.kind, name: f.querySelector('[name="name"]').value.trim(), type: t, fields, secrets,
                     audit: f.querySelector('[name="audit"]').checked };
      if (r.kind === 'Output') spec.namespace = f.querySelector('[name="namespace"]').value;
      if (sh.server) spec.server = { host: f.querySelector('[name="srv-host"]').value.trim(), port: f.querySelector('[name="srv-port"]').value };
      return spec;
    }, 'output-apply', (s) => tr('ml.done.saved', { name: s.name }));
  }

  function ruleRow(k, r = {}) {
    return `<div class="ml-rule" data-rule><select name="r-mode">${opts([['select', tr('ml.include')], ['exclude', tr('ml.exclude')]], r.mode || 'select')}</select>
      <input name="r-hosts" placeholder="${esc(tr('ml.hostsPh'))}" value="${esc((r.hosts || []).join(' '))}" class="tip" data-tip="${esc(tr('ml.t.hosts'))}">
      <input name="r-labels" placeholder="app=web" value="${esc(Object.entries(r.labels || {}).map(([a, b]) => `${a}=${b}`).join(', '))}" class="tip" data-tip="${esc(tr('ml.t.labels'))}">
      ${k === 'ClusterFlow' ? `<input name="r-ns" placeholder="namespaces" value="${esc((r.namespaces || []).join(' '))}" class="tip" data-tip="${esc(tr('ml.t.rns'))}">` : ''}
      <button type="button" class="btn btn-sm btn-secondary tip" data-rule-del data-tip="${esc(tr('ml.t.ruleDel'))}">${icon('trash')}</button></div>`;
  }

  function flowForm(r) {
    const d = cur.data;
    const edit = !!(r && r.name);
    const kind = r.kind;
    const form = win(`ml-flow-${cur.cluster}-${edit ? r.name : 'new'}`, edit ? tr('ml.editFlow', { name: r.name })
      : kind === 'ClusterFlow' ? tr('ml.newCflow') : tr('ml.newFlow'),
    `<p class="form-hint">${esc(kind === 'ClusterFlow' ? tr('ml.cflowHint') : tr('ml.flowHint'))}</p>
      ${field('name', tr('bk.f.name'), `<input name="name" required value="${esc(edit ? r.name : '')}" ${edit ? 'readonly' : ''}>`, tr('ml.t.name'))}
      ${kind === 'Flow' ? field('namespace', tr('ml.namespace'), edit ? `<input name="namespace" value="${esc(r.namespace)}" readonly>` : nsSelect('default'), tr('ml.t.namespace')) : ''}
      ${field('type', tr('ml.circuit'), `<select name="type" ${edit ? 'disabled' : ''}>${opts([['logging', tr('ml.type.logging')], ['audit', tr('ml.type.audit')], ['event', tr('ml.type.event')]], edit ? r.type : 'logging')}</select>`, tr('ml.t.circuit'))}
      <h4 class="hs-sub">${esc(tr('ml.rules'))}</h4><p class="form-hint">${esc(tr('ml.rulesHint'))}</p>
      <div data-rules>${(edit ? r.rules : []).map(x => ruleRow(kind, x)).join('')}</div>
      <button type="button" class="btn btn-sm btn-secondary tip" data-rule-add data-tip="${esc(tr('ml.t.ruleAdd'))}">${icon('add')} ${esc(tr('ml.ruleAdd'))}</button>
      <h4 class="hs-sub">${esc(tr('ml.outputs'))}</h4><div data-outs></div>
      ${field('filters', tr('ml.filtersField'), '<textarea name="filters" rows="4" class="adv-code" placeholder="- tag_normaliser: {}"></textarea>', tr('ml.t.filters'))}`);
    const paintOuts = () => {
      const ns = kind === 'Flow' ? form.querySelector('[name="namespace"]').value : CTRL_NS;
      const audit = form.querySelector('[name="type"]').value === 'audit';
      const sel = new Set(edit ? [...r.local.map(x => `Output/${x}`), ...r.global.map(x => `ClusterOutput/${x}`)] : []);
      const outs = d.outputs.filter(o => !o.system && o.audit === audit
        && ((o.kind === 'Output' && kind === 'Flow' && o.namespace === ns) || o.kind === 'ClusterOutput'));
      form.querySelector('[data-outs]').innerHTML = outs.length ? outs.map(o => `<label class="bk-check"><input type="checkbox" name="out" value="${esc(o.kind)}/${esc(o.name)}"
          ${sel.has(`${o.kind}/${o.name}`) ? 'checked' : ''}> <code>${esc(o.name)}</code> <span class="res-dim">${esc(o.kind === 'ClusterOutput' ? tr('ml.cluster') : o.namespace)} · ${esc(o.types.join(', '))}</span></label>`).join('')
        : `<p class="form-hint">${esc(tr('ml.noOutputFor'))}</p>`;
    };
    form.addEventListener('change', (e) => { if (['namespace', 'type'].includes(e.target.name)) paintOuts(); });
    form.querySelector('[data-rule-add]').addEventListener('click', () => form.querySelector('[data-rules]').insertAdjacentHTML('beforeend', ruleRow(kind)));
    form.addEventListener('click', (e) => { const x = e.target.closest('[data-rule-del]'); if (x) x.closest('[data-rule]').remove(); });
    paintOuts();
    submitWith(form, (f) => {
      const outs = [...f.querySelectorAll('[name="out"]:checked')].map(x => x.value.split('/'));
      const spec = { kind, name: f.querySelector('[name="name"]').value.trim(), type: f.querySelector('[name="type"]').value,
                     local: outs.filter(o => o[0] === 'Output').map(o => o[1]), global: outs.filter(o => o[0] === 'ClusterOutput').map(o => o[1]),
                     rules: [...f.querySelectorAll('[data-rule]')].map(x => ({ mode: x.querySelector('[name="r-mode"]').value,
                       hosts: x.querySelector('[name="r-hosts"]').value, labels: x.querySelector('[name="r-labels"]').value,
                       namespaces: x.querySelector('[name="r-ns"]') ? x.querySelector('[name="r-ns"]').value : '' })),
                     filters_yaml: f.querySelector('[name="filters"]').value };
      if (kind === 'Flow') spec.namespace = f.querySelector('[name="namespace"]').value;
      if (!spec.local.length && !spec.global.length) throw new Error(tr('ml.needOutput'));
      return spec;
    }, 'flow-apply', (s) => tr('ml.done.saved', { name: s.name }));
  }

  function recRow(rec = {}) {
    const types = cur.data.receiver_types || ['webhook'];
    const t = rec.type || 'webhook';
    return `<fieldset class="ml-rec" data-rec><legend>${esc(tr('ml.receiver'))}</legend>
      ${field('rname', tr('bk.f.name'), `<input name="rname" value="${esc(rec.name || '')}">`, tr('ml.t.rname'))}
      ${field('rtype', tr('res.col.type'), `<select name="rtype">${opts(types, t)}</select>`, tr('ml.t.rtype'))}
      <div data-rec-fields></div>
      <label class="bk-check"><input type="checkbox" name="resolved" ${rec.send_resolved ? 'checked' : ''}> <span>${esc(tr('ml.resolved'))}</span></label>
      <button type="button" class="btn btn-sm btn-secondary tip" data-rec-del data-tip="${esc(tr('ml.t.recDel'))}">${icon('trash')}</button></fieldset>`;
  }
  const REC_FIELDS = {
    webhook: { text: ['url'], secret: [] }, slack: { text: ['channel'], secret: ['api_url'] },
    email: { text: ['to', 'from', 'smarthost', 'auth_username'], secret: ['auth_password'] },
    pagerduty: { text: [], secret: ['routing_key'] }, opsgenie: { text: [], secret: ['api_key'] }, msteams: { text: [], secret: ['webhook_url'] },
  };
  function paintRec(box, rec = {}) {
    const t = box.querySelector('[name="rtype"]').value;
    const sh = REC_FIELDS[t] || { text: [], secret: [] };
    box.querySelector('[data-rec-fields]').innerHTML = sh.text.map(n => field(n, n, `<input name="rf-${n}" value="${esc(rec[n] || '')}">`, tr('ml.t.field'))).join('')
      + sh.secret.map(n => secretField(n, rec[n])).join('');
  }

  function amcForm(r) {
    const edit = !!r;
    const recs = edit ? r.receivers_spec.map(x => {
      const key = Object.keys(x).find(k => k.endsWith('Configs'));
      const cfg = ((x[key] || [])[0]) || {};
      const type = (key || 'webhookConfigs').replace('Configs', '');
      return { name: x.name, type, send_resolved: cfg.sendResolved, url: cfg.url, channel: cfg.channel, to: cfg.to, from: cfg.from,
               smarthost: cfg.smarthost, auth_username: cfg.authUsername, api_url: cfg.apiURL, auth_password: cfg.authPassword,
               routing_key: cfg.routingKey, api_key: cfg.apiKey, webhook_url: cfg.webhookUrl };
    }) : [{}];
    const form = win(`ml-amc-${cur.cluster}-${edit ? r.name : 'new'}`, edit ? tr('ml.editAmc', { name: r.name }) : tr('ml.newAmc'),
      `<p class="form-hint">${esc(tr('ml.amcFormHint'))}</p>
      ${field('name', tr('bk.f.name'), `<input name="name" required value="${esc(edit ? r.name : '')}" ${edit ? 'readonly' : ''}>`, tr('ml.t.name'))}
      ${field('namespace', tr('ml.namespace'), edit ? `<input name="namespace" value="${esc(r.namespace)}" readonly>` : `<select name="namespace">${opts(cur.data.namespaces, MON_NS)}</select>`, tr('ml.t.amcNs'))}
      <div data-recs>${recs.map(x => recRow(x)).join('')}</div>
      <button type="button" class="btn btn-sm btn-secondary tip" data-rec-add data-tip="${esc(tr('ml.t.recAdd'))}">${icon('add')} ${esc(tr('ml.recAdd'))}</button>
      <h4 class="hs-sub">${esc(tr('ml.route'))}</h4>
      ${field('route_receiver', tr('ml.routeReceiver'), `<input name="route_receiver" value="${esc(edit ? r.route.receiver : '')}">`, tr('ml.t.routeReceiver'))}
      ${field('group_by', tr('ml.groupBy'), `<input name="group_by" value="${esc(edit ? r.route.group_by.join(', ') : 'alertname')}">`, tr('ml.t.groupBy'))}
      ${field('group_wait', 'group wait', `<input name="group_wait" value="${esc(edit ? r.route.group_wait : '30s')}">`, tr('ml.t.duration'))}
      ${field('group_interval', 'group interval', `<input name="group_interval" value="${esc(edit ? r.route.group_interval : '5m')}">`, tr('ml.t.duration'))}
      ${field('repeat_interval', 'repeat interval', `<input name="repeat_interval" value="${esc(edit ? r.route.repeat_interval : '4h')}">`, tr('ml.t.duration'))}
      ${field('matchers', tr('ml.matchers'), `<input name="matchers" placeholder="severity=~critical|warning" value="${esc(edit ? r.route.matchers.map(x => `${x.name}${x.matchType}${x.value}`).join(', ') : '')}">`, tr('ml.t.matchers'))}`, 720);
    form.querySelectorAll('[data-rec]').forEach((box, i) => paintRec(box, recs[i]));
    form.addEventListener('change', (e) => { if (e.target.name === 'rtype') paintRec(e.target.closest('[data-rec]')); });
    form.querySelector('[data-rec-add]').addEventListener('click', () => {
      form.querySelector('[data-recs]').insertAdjacentHTML('beforeend', recRow());
      paintRec([...form.querySelectorAll('[data-rec]')].pop());
    });
    form.addEventListener('click', (e) => { const x = e.target.closest('[data-rec-del]'); if (x) x.closest('[data-rec]').remove(); });
    submitWith(form, (f) => {
      const receivers = [...f.querySelectorAll('[data-rec]')].map(box => {
        const t = box.querySelector('[name="rtype"]').value;
        const out = { name: box.querySelector('[name="rname"]').value.trim(), type: t, send_resolved: box.querySelector('[name="resolved"]').checked };
        (REC_FIELDS[t] || { text: [] }).text.forEach(n => { const v = box.querySelector(`[name="rf-${n}"]`).value.trim(); if (v) out[n] = v; });
        (REC_FIELDS[t] || { secret: [] }).secret.forEach(n => {
          const s = { name: box.querySelector(`[name="s-${n}-name"]`).value.trim(), key: box.querySelector(`[name="s-${n}-key"]`).value.trim(),
                      value: box.querySelector(`[name="s-${n}-value"]`).value };
          if (s.name || s.key) out[n] = s;
        });
        return out;
      }).filter(x => x.name);
      const matchers = f.querySelector('[name="matchers"]').value.split(',').map(x => x.trim()).filter(Boolean).map(x => {
        const m = x.match(/^([^=!~]+)(=~|!~|!=|=)(.*)$/);
        if (!m) throw new Error(tr('ml.badMatcher', { m: x }));
        return { name: m[1].trim(), matchType: m[2], value: m[3].trim() };
      });
      return { name: f.querySelector('[name="name"]').value.trim(), namespace: f.querySelector('[name="namespace"]').value, receivers,
               route: { receiver: f.querySelector('[name="route_receiver"]').value.trim(), group_by: f.querySelector('[name="group_by"]').value,
                        group_wait: f.querySelector('[name="group_wait"]').value.trim(), group_interval: f.querySelector('[name="group_interval"]').value.trim(),
                        repeat_interval: f.querySelector('[name="repeat_interval"]').value.trim(), matchers } };
    }, 'amc-apply', (s) => tr('ml.done.saved', { name: s.name }));
  }

  return { start, stop };
})();
window.MonLog = MonLog;
