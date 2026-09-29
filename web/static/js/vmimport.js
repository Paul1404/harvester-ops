/**
 * harvester-ops : les imports de VM de Harvester (v1.71.0)
 *
 * Quatre onglets de la section « VM Import » :
 * - Imports : chaque import avec ses étapes (celles du contrôleur), la
 *   progression réelle de ses images, la VM créée ; la raison d'un blocage
 *   est lue dans le journal du contrôleur, le statut ne la porte pas ;
 * - VMware, OpenStack, OVA : les sources, prêtes, pas prêtes ou jamais
 *   vérifiées ; une source prête n'étant jamais revérifiée, « Revérifier » la
 *   recrée. Les identifiants saisis deviennent un Secret.
 */
const VMImport = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 13) => (window.Icons ? Icons.svg(n, { size }) : '');
  const badge = (cls, text, tip) =>
    `<span class="badge ${cls}${tip ? ' tip' : ''}"${tip ? ` data-tip="${esc(tip)}"` : ''}>${esc(text)}</span>`;
  const REFRESH_MS = 10000;
  const TYPES = ['vmware', 'openstack', 'ova'];
  const size = (n) => {
    if (!n) return '–';
    const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let i = 0, v = Number(n);
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
  };

  let cur = null;     // { cluster, host, kind, timer, data }

  async function call(method, url, body) {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' },
                                 body: body ? JSON.stringify(body) : undefined });
    let d = {};
    try { d = await r.json(); } catch { /* sans corps */ }
    if (!r.ok) throw new Error(d.hint || d.error || `HTTP ${r.status}`);
    return d;
  }
  const getJSON = (url) => fetch(url).then(r => (r.ok ? r.json() : null)).catch(() => null);

  function follow(id, into, text, onDone) {
    if (window.Dock && Dock.poll) Dock.poll();
    if (window.VMActions && VMActions.follow) VMActions.follow(id, into, text, onDone);
    else if (into) into.textContent = tr('bk.started', { id });
  }

  // clés littérales (contrôle de parité i18n)
  const PHASE = () => ({
    '': tr('vi.ph.checking'), virtualMachineImportValid: tr('vi.ph.valid'), sourceReady: tr('vi.ph.exporting'),
    disksExported: tr('vi.ph.exported'), diskImageSubmitted: tr('vi.ph.images'), diskImagesReady: tr('vi.ph.imagesReady'),
    virtualMachineCreated: tr('vi.ph.created'), virtualMachineRunning: tr('vi.ph.running'),
    virtualMachineImportInvalid: tr('vi.ph.invalid'), VMMigrationFailed: tr('vi.ph.failed'), diskImageFailed: tr('vi.ph.imageFailed'),
  });
  const SRC_STATE = () => ({
    ready: ['ok', tr('vi.st.ready'), tr('vi.t.ready')], notready: ['fail', tr('vi.st.notready'), tr('vi.t.notready')],
    pending: ['warn', tr('vi.st.pending'), tr('vi.t.pending')],
  });
  const TYPE_LABEL = () => ({ vmware: 'VMware', openstack: 'OpenStack', ova: 'OVA' });
  const HINT = () => ({ vmware: tr('vi.hint.vmware'), openstack: tr('vi.hint.openstack'), ova: tr('vi.hint.ova') });
  const EP_TIP = () => ({ vmware: tr('vi.t.endpoint.vmware'), openstack: tr('vi.t.endpoint.openstack') });
  const VM_HINT = () => ({ vmware: tr('vi.vmHint.vmware'), openstack: tr('vi.vmHint.openstack'), ova: tr('vi.vmHint.ova') });

  // -- cycle de vie ---------------------------------------------------------
  function start(cluster, host) {
    stop();
    const kind = ['imports', ...TYPES].includes(host.dataset.vi) ? host.dataset.vi : 'imports';
    cur = { cluster, host, kind, data: null };
    const newBtn = kind === 'imports'
      ? `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-vi="new-import" data-tip="${esc(tr('vi.t.newImport'))}">${icon('add')} ${esc(tr('vi.newImport'))}</button>`
      : `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-vi="new-source" data-tip="${esc(tr('vi.t.newSource'))}">${icon('add')} ${esc(tr('vi.newSource', { type: TYPE_LABEL()[kind] }))}</button>`;
    host.innerHTML = `<div class="card na-card vi-card">
        <div class="res-tools"><span class="res-count"></span>${newBtn}
          <button type="button" class="btn btn-sm btn-secondary tip" data-vi="refresh" data-tip="${esc(tr('res.refreshTip'))}">${icon('refresh')} ${esc(tr('overview.refresh'))}</button>
        </div>
        <div class="res-feedback" data-vi="feedback"></div>
        <div data-vi="body"><p class="form-hint">${esc(tr('common.loading'))}</p></div></div>`;
    host.querySelector('.vi-card').addEventListener('click', onClick);
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
    const d = await getJSON(`/api/vmimport/${enc(c.cluster)}`);
    if (c !== cur) return;
    c.data = d;
    render();
  }

  function banner(text) {
    return `<div class="sto-finding sev-action"><div class="sto-finding-title">${icon('warn')} ${esc(text)}</div>
      <button type="button" class="btn btn-sm btn-secondary tip" data-vi="open-addons" data-tip="${esc(tr('dev.t.openAddons'))}">${icon('plug')} ${esc(tr('tab.addons'))}</button></div>`;
  }

  function render() {
    if (!cur || !cur.host.isConnected) return;
    const body = cur.host.querySelector('[data-vi="body"]');
    const d = cur.data;
    if (!d || d.error || d.unreachable) {
      body.innerHTML = `<div class="sto-finding sev-critical"><div class="sto-finding-title">${esc((d && d.error) || tr('fabric.unreachable'))}</div></div>`;
      return;
    }
    const off = !d.addon.enabled ? banner(tr('vi.addonOff')) : !d.crds ? banner(tr('vi.noCrds')) : '';
    const count = cur.host.querySelector('.res-count');
    if (cur.kind === 'imports') {
      count.textContent = tr('vi.countImports', { n: d.imports.length });
      body.innerHTML = off + `<p class="form-hint">${esc(tr('vi.importsHint'))}</p>` + importTable(d.imports);
    } else {
      const rows = d.sources.filter(s => s.type === cur.kind);
      count.textContent = tr('vi.countSources', { n: rows.length });
      body.innerHTML = off + `<p class="form-hint">${esc(HINT()[cur.kind])}</p>`
        + (cur.kind === 'vmware' ? `<p class="form-hint">${esc(tr('vi.forkliftHint'))}
        <button type="button" class="btn btn-sm btn-secondary tip" data-vi="open-forklift" data-tip="${esc(tr('vi.t.openForklift'))}">${icon('migrate')} ${esc(tr('tab.forklift'))}</button></p>` : '')
        + sourceTable(rows);
    }
  }

  // -- Tables --------------------------------------------------------------------
  function importTable(rows) {
    if (!rows.length) return `<p class="form-hint">${esc(tr('vi.noImport'))}</p>`;
    const ph = PHASE();
    return `<table class="data-table"><thead><tr><th>${esc(tr('res.col.name'))}</th><th>${esc(tr('vi.col.source'))}</th>
      <th>VM</th><th class="tip" data-tip="${esc(tr('vi.t.steps'))}">${esc(tr('vi.col.progress'))}</th><th>${esc(tr('vi.col.disks'))}</th><th></th></tr></thead><tbody>
      ${rows.map(r => {
        const pctStep = Math.round((r.step / r.steps) * 100);
        const state = r.done ? badge('ok', ph[r.phase]) : r.failed ? badge('fail', ph[r.phase] || r.phase, r.reason || tr('vi.t.why'))
          : badge('info', ph[r.phase] || r.phase, tr('vi.t.inProgress'));
        const disks = r.disks.map(dk => `<div class="vi-disk"><code>${esc(dk.name)}</code> <span class="res-dim">${esc(size(dk.size))}</span>
            ${dk.failed ? badge('fail', tr('vi.imageFailed'), dk.failed) : dk.progress != null
              ? `<span class="vi-bar tip" data-tip="${esc(tr('vi.t.imageProgress', { image: dk.image }))}"><i style="width:${Math.min(100, dk.progress)}%"></i></span> ${esc(dk.progress)} %` : ''}</div>`).join('') || '–';
        const vm = r.vm ? `<a href="#" class="tip" data-vi="open-vm" data-ns="${esc(r.namespace)}" data-vm="${esc(r.vm)}" data-tip="${esc(tr('hs.tip.openVm'))}"><strong>${esc(r.vm)}</strong></a>`
          : `<span class="res-dim tip" data-tip="${esc(tr('vi.t.vmName'))}">${esc(r.vm_name)}</span>`;
        return `<tr data-ns="${esc(r.namespace)}" data-name="${esc(r.name)}">
          <td><strong>${esc(r.name)}</strong> <span class="res-dim">${esc(r.namespace)}</span></td>
          <td>${esc(TYPE_LABEL()[r.type] || r.type)} <span class="res-dim">${esc(r.source_ns)}/${esc(r.source)}</span></td>
          <td>${vm}</td>
          <td><div class="vi-steps">${state} <span class="res-dim">${esc(tr('vi.stepOf', { n: r.step, total: r.steps }))}</span></div>
            <span class="vi-bar ${r.failed ? 'vi-fail' : ''}"><i style="width:${r.failed ? 100 : pctStep}%"></i></span>
            ${r.failed && r.reason ? `<div class="res-error">${esc(r.reason)}</div>` : ''}</td>
          <td>${disks}</td>
          <td class="tpl-acts">
            ${!r.done && !r.failed ? `<button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-vi="follow" data-tip="${esc(tr('vi.t.follow'))}">${icon('activity')}</button>` : ''}
            ${!r.done ? `<button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-vi="why" data-tip="${esc(tr('vi.t.why'))}">${icon('info')}</button>` : ''}
            <button type="button" class="btn btn-sm btn-danger tip needs-admin" data-vi="del-import" data-tip="${esc(r.done ? tr('vi.t.delDone') : tr('vi.t.delRunning'))}">${icon('trash')}</button></td></tr>`;
      }).join('')}</tbody></table>`;
  }

  function sourceTable(rows) {
    if (!rows.length) return `<p class="form-hint">${esc(tr('vi.noSource'))}</p>`;
    const st = SRC_STATE();
    const t = cur.kind;
    return `<table class="data-table"><thead><tr><th>${esc(tr('res.col.name'))}</th>
      <th>${esc(t === 'ova' ? 'URL' : tr('vi.f.endpoint'))}</th>${t === 'vmware' ? `<th>${esc(tr('vi.f.dc'))}</th>` : t === 'openstack' ? `<th>${esc(tr('vi.f.region'))}</th>` : ''}
      <th>${esc(tr('vi.col.secret'))}</th><th>${esc(tr('res.col.state'))}</th><th>${esc(tr('ml.usedBy'))}</th><th></th></tr></thead><tbody>
      ${rows.map(r => { const [cls, text, tip] = st[r.state] || ['dim', r.state, '']; return `<tr data-ns="${esc(r.namespace)}" data-name="${esc(r.name)}">
        <td><strong>${esc(r.name)}</strong> <span class="res-dim">${esc(r.namespace)}</span></td>
        <td><code>${esc(r.endpoint)}</code></td>
        ${t === 'vmware' ? `<td>${esc(r.dc)}</td>` : t === 'openstack' ? `<td>${esc(r.region)}</td>` : ''}
        <td>${r.secret ? `${icon('lock')} ${esc(r.secret)}` : '–'}</td>
        <td>${badge(cls, text, tip)}</td>
        <td>${r.users.map(u => `<code class="res-key">${esc(u)}</code>`).join(' ') || '–'}</td>
        <td class="tpl-acts">
          <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-vi="edit-source" data-tip="${esc(tr('vi.t.editSource'))}">${icon('edit')}</button>
          <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-vi="recheck" ${r.users.length ? 'disabled' : ''} data-tip="${esc(r.users.length ? tr('vi.t.inUse') : tr('vi.t.recheck'))}">${icon('refresh')}</button>
          ${r.state !== 'ready' ? `<button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-vi="why" data-tip="${esc(tr('vi.t.whySource'))}">${icon('info')}</button>` : ''}
          <button type="button" class="btn btn-sm btn-danger tip needs-admin" data-vi="del-source" ${r.users.length ? 'disabled' : ''}
            data-tip="${esc(r.users.length ? tr('vi.t.inUse') : tr('vi.t.delSource'))}">${icon('trash')}</button></td></tr>`; }).join('')}</tbody></table>`;
  }

  // -- Actions --------------------------------------------------------------
  /** `cluster` : celui d'une fenêtre, figé à son ouverture ; sinon celui de
   *  l'onglet. L'onglet n'est relu que s'il montre encore ce cluster. */
  async function post(action, body, doneText, into, cluster) {
    const c = cur;
    const target = cluster || (c && c.cluster);
    const msg = into || (c && c.host.querySelector('[data-vi="feedback"]'));
    const same = () => !!c && c === cur && c.cluster === target;
    try {
      const out = await call('POST', `/api/vmimport/${enc(target)}/do/${action}`, body);
      follow(out.action_id, msg, doneText, () => { if (same()) setTimeout(load, 1500); });
      if (same()) setTimeout(load, 2500);
      return true;
    } catch (err) {
      if (msg) msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      return false;
    }
  }

  async function why(ns, name) {
    const panel = FloatingPanels.open({ id: `vi-log-${cur.cluster}-${ns}-${name}`, icon: 'info', width: 760, height: 420,
      title: `${tr('vi.logTitle', { name })} · ${cur.cluster}`, bodyHtml: `<p class="form-hint">${esc(tr('common.loading'))}</p>` });
    const box = panel.body;
    try {
      const d = await call('GET', `/api/vmimport-log/${enc(cur.cluster)}/${enc(ns)}/${enc(name)}`);
      box.innerHTML = `<p class="form-hint">${esc(tr('vi.logHint'))}</p>
        ${d.reason ? `<div class="sto-finding sev-critical"><div class="sto-finding-title">${icon('fail')} ${esc(d.reason)}</div></div>` : ''}
        ${d.lines && d.lines.length ? `<pre class="adv-code vi-log">${esc(d.lines.join('\n'))}</pre>` : `<p class="form-hint">${esc(d.error || tr('vi.logEmpty'))}</p>`}`;
    } catch (err) {
      box.innerHTML = `<p class="res-error">${esc(err.message)}</p>`;
    }
  }

  function onClick(e) {
    const b = e.target.closest('[data-vi]');
    if (!b || !cur) return;
    const act = b.dataset.vi;
    if (act === 'open-forklift' && window.Sections) return Sections.open('forklift', 'prep');
    if (!cur.data && act !== 'refresh' && act !== 'open-addons') {
      // clic avant l'arrivée des données : on le rejoue une fois lues
      const c = cur;
      return c.ready && c.ready.then(() => { if (c === cur && cur.data) onClick(e); }, () => {});
    }
    const row = b.closest('tr[data-name]');
    const key = row ? { namespace: row.dataset.ns, name: row.dataset.name } : null;
    const d = cur.data || {};
    if (act === 'refresh') return load();
    if (act === 'open-addons' && window.Sections) return Sections.open('addons', 'list');
    if (act === 'open-vm') {
      e.preventDefault();
      if (window.VMEdit && VMEdit.open) VMEdit.open(cur.cluster, b.dataset.ns, b.dataset.vm);
      return;
    }
    if (act === 'new-import') return importForm();
    if (act === 'new-source') return sourceForm(cur.kind, null);
    if (!key) return;
    if (act === 'why') return why(key.namespace, key.name);
    if (act === 'follow') return post('import-follow', key, tr('vi.done.import'));
    if (act === 'del-import') {
      const r = d.imports.find(x => x.namespace === key.namespace && x.name === key.name);
      const nm = `${key.namespace}/${key.name}`;
      if (!confirm(r && r.done ? tr('vi.confirm.delDone', { name: nm }) : tr('vi.confirm.delRunning', { name: nm }))) return;
      return post('import-delete', key, tr('ml.done.delete', { name: key.name }));
    }
    const src = d.sources.find(x => x.type === cur.kind && x.namespace === key.namespace && x.name === key.name);
    if (act === 'edit-source') return sourceForm(cur.kind, src);
    if (act === 'recheck') return post('source-recheck', { ...key, type: cur.kind }, tr('vi.done.recheck', { name: key.name }));
    if (act === 'del-source') {
      if (!confirm(tr('vi.confirm.delSource', { name: `${key.namespace}/${key.name}` }))) return;
      return post('source-delete', { ...key, type: cur.kind, with_secret: true }, tr('ml.done.delete', { name: key.name }));
    }
  }

  // -- Fenêtres ---------------------------------------------------------------------
  function win(cluster, id, title, bodyHtml, height = 640) {
    const panel = FloatingPanels.open({ id, icon: 'upload', width: 720, height, title: `${title} · ${cluster}`,
      bodyHtml: `<form class="of-form" autocomplete="off">${bodyHtml}
        <div class="bk-form-actions"><button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('bk.submitTip'))}">${icon('ok')} ${esc(tr('na.save'))}</button></div>
        <div class="of-msg" role="status"></div></form>` });
    return panel.el.querySelector('.of-form');
  }
  const field = (name, label, input, tip, cls = '') => `<label class="bk-field of-field ${cls}" data-f="${name}"><span>${esc(label)}</span>${
    input.replace(/^<(input|select|textarea)/, `<$1 class="tip" data-tip="${esc(tip || '')}"`)}</label>`;
  const opts = (list, sel) => list.map(v => (Array.isArray(v) ? v : [v, v]))
    .map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(sel) ? 'selected' : ''}>${esc(l)}</option>`).join('');
  const userNs = (sel) => (cur.data.namespaces || []).filter(n => !/^(cattle-|kube-|fleet-|longhorn-|harvester-system|local$)/.test(n) || n === sel);

  /** `cluster` : celui de la fenêtre, figé à son ouverture (l'onglet peut
   *  passer à un autre cluster avant l'envoi). */
  function submitWith(form, build, action, doneText, cluster) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = form.querySelector('.of-msg');
      let spec;
      try { spec = build(form); } catch (err) { msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`; return; }
      await post(action, { spec }, doneText(spec), msg, cluster);
    });
  }

  function sourceForm(type, r) {
    const edit = !!r;
    const cluster = cur.cluster;
    const form = win(cluster, `vi-src-${cluster}-${type}-${edit ? r.name : 'new'}`,
      edit ? tr('vi.editSource', { name: r.name }) : tr('vi.newSource', { type: TYPE_LABEL()[type] }),
      `<p class="form-hint">${esc(HINT()[type])}</p>
      ${field('name', tr('bk.f.name'), `<input name="name" required value="${esc(edit ? r.name : '')}" ${edit ? 'readonly' : ''}>`, tr('ml.t.name'))}
      ${field('namespace', tr('ml.namespace'), edit ? `<input name="namespace" value="${esc(r.namespace)}" readonly>`
        : `<select name="namespace">${opts(userNs('default'), 'default')}</select>`, tr('vi.t.srcNs'))}
      ${type === 'ova' ? field('url', 'URL', `<input name="url" required placeholder="http://10.0.0.5/web01.ova" value="${esc(edit ? r.endpoint : '')}">`, tr('vi.t.url'))
        + field('http_timeout', tr('vi.f.timeout'), `<input name="http_timeout" type="number" min="0" placeholder="600" value="${esc(edit && r.http_timeout != null ? r.http_timeout : '')}">`, tr('vi.t.timeout'))
        : field('endpoint', tr('vi.f.endpoint'), `<input name="endpoint" required placeholder="${type === 'vmware' ? 'https://vcenter.lan/sdk' : 'https://openstack.lan/identity'}" value="${esc(edit ? r.endpoint : '')}">`, EP_TIP()[type])}
      ${type === 'vmware' ? field('dc', tr('vi.f.dc'), `<input name="dc" required value="${esc(edit ? r.dc : '')}">`, tr('vi.t.dc')) : ''}
      ${type === 'openstack' ? field('region', tr('vi.f.region'), `<input name="region" required placeholder="RegionOne" value="${esc(edit ? r.region : '')}">`, tr('vi.t.region'))
        + field('retry_count', tr('vi.f.retryCount'), `<input name="retry_count" type="number" min="1" placeholder="30" value="${esc(edit && r.retry_count ? r.retry_count : '')}">`, tr('vi.t.retryCount'))
        + field('retry_delay', tr('vi.f.retryDelay'), `<input name="retry_delay" type="number" min="1" placeholder="10" value="${esc(edit && r.retry_delay ? r.retry_delay : '')}">`, tr('vi.t.retryDelay')) : ''}
      <h4 class="hs-sub">${esc(tr('vi.credentials'))}</h4>
      ${field('mode', tr('vi.f.credMode'), `<select name="mode">${opts([...(type === 'ova' ? [['none', tr('vi.cred.none')]] : []),
        ['new', tr('vi.cred.new')], ['existing', tr('vi.cred.existing')]], edit ? (r.secret ? 'existing' : 'none') : (type === 'ova' ? 'none' : 'new'))}</select>`, tr('vi.t.credMode'))}
      <div data-vi-cred></div>`);
    const paint = () => {
      const mode = form.querySelector('[name="mode"]').value;
      let html = '';
      if (mode === 'existing') {
        html = field('secret', tr('vi.f.secret'), `<input name="secret" required value="${esc(edit ? r.secret : '')}">`, tr('vi.t.secret'));
      } else if (mode === 'new') {
        html = field('secret', tr('vi.f.secret'), `<input name="secret" placeholder="${esc((form.querySelector('[name="name"]').value || 'source') + '-creds')}">`, tr('vi.t.newSecret'))
          + field('username', tr('vi.f.user'), '<input name="username" autocomplete="off">', tr('vi.t.user'))
          + field('password', tr('vi.f.password'), '<input name="password" type="password" autocomplete="new-password">', tr('vi.t.password'))
          + (type === 'openstack' ? field('project_name', tr('vi.f.project'), '<input name="project_name">', tr('vi.t.project'))
            + field('domain_name', tr('vi.f.domain'), '<input name="domain_name" placeholder="Default">', tr('vi.t.domain')) : '')
          + field('ca', tr('vi.f.ca'), '<textarea name="ca" rows="4" class="adv-code" placeholder="-----BEGIN CERTIFICATE-----"></textarea>', tr('vi.t.ca'));
      } else {
        html = `<p class="form-hint">${esc(tr('vi.cred.noneHint'))}</p>`;
      }
      form.querySelector('[data-vi-cred]').innerHTML = html;
    };
    form.querySelector('[name="mode"]').addEventListener('change', paint);
    paint();
    submitWith(form, (f) => {
      const v = (n) => { const x = f.querySelector(`[name="${n}"]`); return x ? x.value.trim() : ''; };
      const mode = v('mode');
      const spec = { type, name: v('name'), namespace: v('namespace'), credentials: { mode } };
      if (type === 'ova') { spec.url = v('url'); if (v('http_timeout') !== '') spec.http_timeout = v('http_timeout'); }
      else spec.endpoint = v('endpoint');
      if (type === 'vmware') spec.dc = v('dc');
      if (type === 'openstack') { spec.region = v('region'); spec.retry_count = v('retry_count'); spec.retry_delay = v('retry_delay'); }
      if (mode === 'existing') spec.credentials.secret = v('secret');
      if (mode === 'new') {
        spec.credentials.secret = v('secret');
        spec.credentials.values = { username: v('username'), password: f.querySelector('[name="password"]').value,
                                    project_name: v('project_name'), domain_name: v('domain_name'), ca: f.querySelector('[name="ca"]').value.trim() };
      }
      return spec;
    }, 'source-apply', (s) => tr('vi.done.source', { name: s.name }), cluster);
  }

  // `d` : les données du cluster de la fenêtre, pas celles de l'onglet au clic
  function netRow(n = {}, d = cur.data) {
    const nads = d.nads || [];
    return `<div class="vi-net" data-net>
      <input name="n-src" placeholder="VM Network" value="${esc(n.source || '')}" class="tip" data-tip="${esc(tr('vi.t.srcNet'))}">
      <select name="n-dst" class="tip" data-tip="${esc(tr('vi.t.dstNet'))}"><option value=""></option>${opts(nads, n.destination || '')}</select>
      <select name="n-model" class="tip" data-tip="${esc(tr('vi.t.nicModel'))}"><option value="">${esc(tr('vi.fromSource'))}</option>${opts(d.nic_models, n.model || '')}</select>
      <button type="button" class="btn btn-sm btn-secondary tip" data-net-del data-tip="${esc(tr('vi.t.netDel'))}">${icon('trash')}</button></div>`;
  }

  function importForm() {
    const cluster = cur.cluster;
    const d = cur.data;
    const sources = d.sources.map(s => [`${s.type}|${s.namespace}|${s.name}`,
      `${TYPE_LABEL()[s.type]} · ${s.namespace}/${s.name} · ${(SRC_STATE()[s.state] || [])[1] || s.state}`]);
    const form = win(cluster, `vi-imp-${cluster}-new`, tr('vi.newImport'),
      `<p class="form-hint">${esc(tr('vi.importFormHint'))}</p>
      ${sources.length ? '' : `<p class="res-error">${esc(tr('vi.noSourceYet'))}</p>`}
      ${field('source', tr('vi.col.source'), `<select name="source" required>${opts(sources, '')}</select>`, tr('vi.t.source'))}
      ${field('vm_name', tr('vi.f.vmName'), '<input name="vm_name" required>', tr('vi.t.vmName'))}
      <p class="form-hint" data-vi-final></p>
      ${field('name', tr('vi.f.importName'), '<input name="name" required>', tr('vi.t.importName'))}
      ${field('namespace', tr('vi.f.targetNs'), `<select name="namespace">${opts(userNs('default'), 'default')}</select>`, tr('vi.t.targetNs'))}
      ${field('storage_class', tr('vi.f.class'), `<select name="storage_class"><option value="">${esc(tr('vi.defaultClass', { name: d.default_class || '?' }))}</option>${opts(d.classes, '')}</select>`, tr('vi.t.class'))}
      <h4 class="hs-sub">${esc(tr('vi.networks'))}</h4>
      <p class="form-hint">${esc(tr('vi.networksHint'))}</p>
      <div data-nets>${netRow({}, d)}</div>
      <button type="button" class="btn btn-sm btn-secondary tip" data-net-add data-tip="${esc(tr('vi.t.netAdd'))}">${icon('add')} ${esc(tr('vi.netAdd'))}</button>
      <details class="vi-adv"><summary>${esc(tr('vi.advanced'))}</summary>
        ${field('default_nic_model', tr('vi.f.defNic'), `<select name="default_nic_model"><option value="">virtio</option>${opts(d.nic_models, '')}</select>`, tr('vi.t.defNic'))}
        ${field('default_disk_bus', tr('vi.f.defBus'), `<select name="default_disk_bus"><option value="">virtio</option>${opts(d.disk_bus, '')}</select>`, tr('vi.t.defBus'))}
        <label class="bk-check tip" data-tip="${esc(tr('vi.t.skip'))}"><input type="checkbox" name="skip_preflight"> <span>${esc(tr('vi.f.skip'))}</span></label>
        <div data-vi-vmware>
          ${field('folder', tr('vi.f.folder'), '<input name="folder" placeholder="/Production/Web">', tr('vi.t.folder'))}
          ${field('graceful_timeout', tr('vi.f.graceful'), '<input name="graceful_timeout" type="number" min="1" placeholder="60">', tr('vi.t.graceful'))}
          <label class="bk-check tip" data-tip="${esc(tr('vi.t.force'))}"><input type="checkbox" name="force_power_off"> <span>${esc(tr('vi.f.force'))}</span></label>
        </div></details>`, 720);
    const typeOf = () => (form.querySelector('[name="source"]').value || '').split('|')[0];
    const paint = () => {
      const t = typeOf();
      form.querySelector('[data-vi-vmware]').hidden = t !== 'vmware';
      const vm = form.querySelector('[name="vm_name"]').value.trim();
      const nameIn = form.querySelector('[name="name"]');
      if (!nameIn.dataset.touched) nameIn.value = vm.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
      const fin = form.querySelector('[data-vi-final]');
      fin.textContent = vm ? (t === 'openstack' ? tr('vi.finalOpenstack') : tr('vi.finalName', { name: vm.toLowerCase() })) : VM_HINT()[t || 'ova'];
    };
    form.querySelector('[name="name"]').addEventListener('input', (e) => { e.target.dataset.touched = '1'; });
    form.querySelector('[name="source"]').addEventListener('change', paint);
    form.querySelector('[name="vm_name"]').addEventListener('input', paint);
    form.querySelector('[data-net-add]').addEventListener('click', () => form.querySelector('[data-nets]').insertAdjacentHTML('beforeend', netRow({}, d)));
    form.addEventListener('click', (e) => { const x = e.target.closest('[data-net-del]'); if (x) x.closest('[data-net]').remove(); });
    paint();
    submitWith(form, (f) => {
      const v = (n) => f.querySelector(`[name="${n}"]`).value.trim();
      const [type, sns, sname] = v('source').split('|');
      if (!sname) throw new Error(tr('vi.noSourceYet'));
      const spec = { name: v('name'), namespace: v('namespace'), vm_name: v('vm_name'), source: { type, namespace: sns, name: sname },
                     storage_class: v('storage_class'), default_nic_model: v('default_nic_model'), default_disk_bus: v('default_disk_bus'),
                     skip_preflight: f.querySelector('[name="skip_preflight"]').checked,
                     networks: [...f.querySelectorAll('[data-net]')].map(x => ({ source: x.querySelector('[name="n-src"]').value.trim(),
                       destination: x.querySelector('[name="n-dst"]').value, model: x.querySelector('[name="n-model"]').value }))
                       .filter(n => n.source || n.destination) };
      if (type === 'vmware') {
        spec.folder = v('folder');
        spec.graceful_timeout = v('graceful_timeout');
        spec.force_power_off = f.querySelector('[name="force_power_off"]').checked;
      }
      return spec;
    }, 'import-create', (s) => tr('vi.done.import'), cluster);
  }

  return { start, stop };
})();
window.VMImport = VMImport;
