/**
 * harvester-ops : migrations VMware par Forklift, onglet d'un cluster (v1.75.0)
 *
 * Trois onglets de la section « Migrations VMware » :
 * - Préparation : Forklift sur le cluster, l'image VDDK, les sources, dans
 *   l'ordre où il faut les faire, chacun avec son état et son geste ;
 * - Sources vCenter : un bloc par fournisseur vSphere de Forklift, ajout et
 *   modification en fenêtre (un vCenter de VM Import se reprend sans
 *   ressaisir son mot de passe, lu par le serveur) ;
 * - Inventaire : ce que Forklift voit d'un vCenter, en lecture seule (les
 *   vagues à chaud viennent avec l'étape suivante).
 * Toute écriture passe par l'outil harvester-forklift, en action suivie.
 */
const Forklift = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 13) => (window.Icons ? Icons.svg(n, { size }) : '');
  const badge = (cls, text, tip) =>
    `<span class="badge ${cls}${tip ? ' tip' : ''}"${tip ? ` data-tip="${esc(tip)}"` : ''}>${esc(text)}</span>`;
  const REFRESH_MS = 10000;
  const KINDS = ['prep', 'sources', 'inventory'];
  const size = (n) => {
    if (!n) return '–';
    const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let i = 0, v = Number(n);
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
  };

  let cur = null;       // { cluster, host, kind, data, store, timer }
  let lastInv = null;   // { cluster, source, kind, q, warm } : retrouvé au retour sur l'onglet (U4)

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

  // -- cycle de vie ---------------------------------------------------------
  function start(cluster, host) {
    stop();
    const kind = KINDS.includes(host.dataset.fk) ? host.dataset.fk : 'prep';
    cur = { cluster, host, kind, data: null, store: null };
    // v1.75.0 : le bouton « Ajouter un vCenter » n'a sa place que sur l'onglet Sources
    const newBtn = kind === 'sources'
      ? `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-fk="new-source" data-tip="${esc(tr('fk.t.newSource'))}">${icon('add')} ${esc(tr('fk.newSource'))}</button>` : '';
    host.innerHTML = `<div class="card na-card fk-card">
        <div class="res-tools"><span class="res-count"></span>${newBtn}
          <button type="button" class="btn btn-sm btn-secondary tip" data-fk="refresh" data-tip="${esc(tr('res.refreshTip'))}">${icon('refresh')} ${esc(tr('overview.refresh'))}</button>
        </div>
        <div class="res-feedback" data-fk="feedback"></div>
        <div data-fk="body"><p class="form-hint">${esc(tr('common.loading'))}</p></div></div>`;
    const card = host.querySelector('.fk-card');
    card.addEventListener('click', onClick);
    card.addEventListener('change', onChange);
    card.addEventListener('input', onInput);
    // l'inventaire ouvre un tunnel vers le cluster : pas de relecture automatique
    cur.timer = setInterval(() => {
      if (cur && cur.kind !== 'inventory' && cur.host.isConnected && !cur.host.closest('[hidden]')) load();
    }, REFRESH_MS);
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
    const [d, store] = await Promise.all([getJSON(`/api/forklift/${enc(c.cluster)}`),
                                          c.kind === 'prep' ? getJSON('/api/forklift-vddk') : Promise.resolve(c.store)]);
    if (c !== cur) return;
    c.data = d;
    c.store = store;
    render();
  }

  function render() {
    if (!cur || !cur.host.isConnected) return;
    const body = cur.host.querySelector('[data-fk="body"]');
    const d = cur.data;
    if (!d || d.error || d.unreachable) {
      body.innerHTML = `<div class="sto-finding sev-critical"><div class="sto-finding-title">${esc((d && d.error) || tr('fabric.unreachable'))}</div></div>`;
      return;
    }
    if (cur.kind === 'prep') body.innerHTML = prepView(d);
    else if (cur.kind === 'sources') body.innerHTML = sourcesView(d);   // U4
    else inventoryView(body, d);                                        // U4
  }

  // -- Préparation ------------------------------------------------------------
  function installLine(st) {
    const parts = [
      ['cert-manager', st.cert_manager, tr('fk.t.certManager')],
      [tr('fk.addon'), st.addon === 'ready', st.addon_message],
      [tr('fk.operator'), st.operator, tr('fk.t.operator')],
      [tr('fk.controller'), st.controller, tr('fk.t.controller')],
      [tr('fk.components'), st.controller && !st.components_missing.length,
       st.components_missing.length ? tr('fk.missing', { list: st.components_missing.join(', ') }) : tr('fk.t.components')],
    ];
    return parts.map(([label, ok, tip]) => `<span class="fk-part tip" data-tip="${esc(tip)}">${icon(ok ? 'ok' : 'fail', 12)} ${esc(label)}</span>`).join(' ');
  }

  function stepBox(id, n, title, stateHtml, bodyHtml) {
    return `<div class="fk-step" data-fk-step="${id}"><div class="fk-step-head"><span class="fk-step-n">${n}</span>
      <b>${esc(title)}</b> ${stateHtml}</div>${bodyHtml}</div>`;
  }

  function prepView(d) {
    const st = d.install;
    const addonState = { ready: ['ok', tr('fk.st.ready')], deploying: ['warn', tr('fk.st.deploying')],
                         failed: ['fail', tr('fk.st.failed')], disabled: ['warn', tr('fk.st.disabled')],
                         absent: ['warn', tr('fk.st.absent')] };
    const [cls, txt] = st.ready ? ['ok', tr('fk.st.ready')] : (addonState[st.addon] || ['warn', st.addon]);
    const canInstall = st.cert_manager || d.bundle;
    const one = stepBox('forklift', 1, tr('fk.step.forklift'), badge(cls, txt, st.addon_message),
      `<p class="form-hint">${esc(tr('fk.forkliftHint'))}</p><div class="fk-parts">${installLine(st)}</div>
       ${d.harvester_addon ? `<p class="form-hint">${esc(tr('fk.harvesterAddon'))}</p>` : ''}
       ${st.ready ? '' : `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-fk="install" ${canInstall ? '' : 'disabled'}
          data-tip="${esc(canInstall ? tr('fk.t.install') : tr('fk.t.noBundle'))}">${icon('download')} ${esc(st.addon === 'absent' ? tr('fk.install') : tr('fk.resume'))}</button>`}`);
    const v = d.vddk;
    const archives = (cur.store && cur.store.archives) || [];
    const pick = (v && archives.some(a => a.name === v.archive)) ? v.archive : (archives[0] && archives[0].name) || '';
    const two = stepBox('vddk', 2, tr('fk.step.vddk'),
      v ? badge('ok', v.image, tr('fk.t.vddkDone', { digest: v.digest.slice(0, 19), when: v.pushed_at })) : badge('warn', tr('fk.st.todo')),
      `<p class="form-hint">${esc(tr('fk.vddkHint'))}</p>
       <div class="fk-form">
         ${field('archive', tr('fk.f.archive'), `<select name="archive">${archives.length ? opts(archives.map(a => [a.name, `${a.name} (${size(a.size)})`]), pick) : `<option value="">${esc(tr('fk.noArchive'))}</option>`}</select>`, tr('fk.t.archive'))}
         <div class="fk-upload">
           <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-fk="upload-vddk" data-tip="${esc(tr('fk.t.upload'))}">${icon('upload')} ${esc(tr('fk.upload'))}</button>
           <input type="file" accept=".tar.gz" data-fk="upload-file" hidden>
           <span class="form-hint" data-fk="upload-line"></span>
         </div>
         ${field('image', tr('fk.f.image'), `<input name="image" value="${esc((v && v.image) || d.registry.image)}" placeholder="registry.lan/harvops/vddk:8.0.3">`,
                 d.registry.host ? tr('fk.t.imageHint', { host: d.registry.host }) : tr('fk.t.image'))}
         <label class="fk-check tip" data-tip="${esc(tr('fk.t.plainHttp'))}"><input type="checkbox" name="plain_http" ${d.registry.plain_http ? 'checked' : ''}> ${esc(tr('fk.f.plainHttp'))}</label>
         ${d.registry.auth ? `<label class="fk-check tip" data-tip="${esc(tr('fk.t.clusterAuth', { host: d.registry.host }))}"><input type="checkbox" name="use_cluster_auth" checked> ${esc(tr('fk.f.clusterAuth'))}</label>` : ''}
         <div data-fk="reg-creds" ${d.registry.auth ? 'hidden' : ''}>
           ${field('username', tr('fk.f.regUser'), '<input name="username" autocomplete="off">', tr('fk.t.regUser'))}
           ${field('password', tr('fk.f.regPassword'), '<input name="password" type="password" autocomplete="new-password">', tr('fk.t.regPassword'))}
         </div>
         <button type="button" class="btn btn-sm btn-primary tip needs-admin" data-fk="push-vddk" ${archives.length ? '' : 'disabled'} data-tip="${esc(tr('fk.t.push'))}">${icon('upload')} ${esc(tr('fk.push'))}</button>
       </div>`);
    const ready = d.providers.filter(p => p.ready === true).length;
    const three = stepBox('sources', 3, tr('fk.step.sources'),
      d.providers.length ? badge(ready ? 'ok' : 'warn', tr('fk.st.sources', { ready, total: d.providers.length })) : badge('warn', tr('fk.st.todo')),
      `<p class="form-hint">${esc(tr('fk.sourcesHint'))}</p>
       <button type="button" class="btn btn-sm btn-secondary tip" data-fk="goto-sources" data-tip="${esc(tr('fk.t.gotoSources'))}">${icon('cloud')} ${esc(tr('section.fkSources'))}</button>`);
    return one + two + three;
  }

  // -- Sources vCenter (U4) ----------------------------------------------------
  function sourcesView(d) {
    if (!d.install.ready) {
      return `<div class="sto-finding sev-action"><div class="sto-finding-title">${icon('warn')} ${esc(tr('fk.needInstall'))}</div>
        <button type="button" class="btn btn-sm btn-secondary tip" data-fk="goto-prep" data-tip="${esc(tr('fk.t.gotoPrep'))}">${icon('settings')} ${esc(tr('section.fkPrep'))}</button></div>`;
    }
    cur.host.querySelector('.res-count').textContent = tr('fk.count', { n: d.providers.length });
    if (!d.providers.length) return `<p class="form-hint">${esc(tr('fk.noSource'))}</p>`;
    const state = (p) => (p.ready === true ? badge('ok', tr('fk.src.ready'), p.message)
      : p.ready === false ? badge('fail', tr('fk.src.refused'), p.message) : badge('warn', tr('fk.src.checking'), p.message));
    return `<div class="fk-sources">${d.providers.map(p => `<div class="fk-source" data-fk-source="${esc(p.name)}">
        <div class="fk-step-head"><b>${esc(p.name)}</b> ${state(p)}</div>
        <div class="form-hint">${esc(p.url)}</div>
        ${p.ready === false ? `<div class="res-error">${esc(p.message)}</div>` : ''}
        <div class="form-hint">${esc(tr('fk.src.vddk', { image: p.vddk_image || tr('fk.src.noVddk') }))}</div>
        <div class="form-hint">${esc(tr('fk.src.plans', { n: p.plans.length }))}</div>
        <div class="fk-source-actions">
          <button type="button" class="btn btn-sm btn-secondary tip" data-fk="inv-source" data-name="${esc(p.name)}" ${p.ready === true ? '' : 'disabled'} data-tip="${esc(tr('fk.t.inventory'))}">${icon('general')} ${esc(tr('section.fkInventory'))}</button>
          <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-fk="edit-source" data-name="${esc(p.name)}" ${p.managed ? '' : 'disabled'} data-tip="${esc(p.managed ? tr('fk.t.edit') : tr('fk.t.notManaged'))}">${icon('edit')} ${esc(tr('fk.edit'))}</button>
          <button type="button" class="btn btn-sm btn-danger tip needs-admin" data-fk="del-source" data-name="${esc(p.name)}" ${p.plans.length ? 'disabled' : ''} data-tip="${esc(p.plans.length ? tr('fk.t.delUsed', { plans: p.plans.join(', ') }) : tr('fk.t.del'))}">${icon('trash')} ${esc(tr('fk.del'))}</button>
        </div></div>`).join('')}</div>`;
  }

  function sourceForm(p) {
    const edit = !!p;
    const d = cur.data;
    const vddk = (p && p.vddk_image) || (d.vddk && d.vddk.image) || '';
    const froms = edit ? [] : d.vmimport_sources || [];
    const form = win(`fk-src-${cur.cluster}-${edit ? p.name : 'new'}`, edit ? tr('fk.editSource', { name: p.name }) : tr('fk.newSource'),
      `<p class="form-hint">${esc(tr('fk.sourceHint'))}</p>
      ${froms.length ? field('from', tr('fk.f.from'), `<select name="from"><option value="">${esc(tr('fk.from.none'))}</option>${
        froms.map(s => `<option value="${esc(`${s.namespace}/${s.name}`)}">${esc(`${s.namespace}/${s.name} (${s.endpoint})`)}</option>`).join('')}</select>`, tr('fk.t.from')) : ''}
      ${field('name', tr('bk.f.name'), `<input name="name" required value="${esc(edit ? p.name : '')}" ${edit ? 'readonly' : ''}>`, tr('fk.t.name'))}
      <div data-fk-typed>
        ${field('url', tr('fk.f.url'), `<input name="url" required placeholder="vcenter.lan" value="${esc(edit ? p.url : '')}">`, tr('fk.t.url'))}
        ${field('user', tr('vi.f.user'), '<input name="user" autocomplete="off" placeholder="administrator@vsphere.local">', edit ? tr('fk.t.userKeep') : tr('fk.t.user'))}
        ${field('password', tr('vi.f.password'), `<input name="password" type="password" autocomplete="new-password" ${edit ? `placeholder="${esc(tr('fk.unchanged'))}"` : 'required'}>`, edit ? tr('fk.t.passwordKeep') : tr('fk.t.password'))}
        <fieldset class="fk-tls"><legend>${esc(tr('fk.f.tls'))}</legend>
          ${edit ? `<label class="fk-check tip" data-tip="${esc(tr('fk.t.tlsKeep'))}"><input type="radio" name="tls" value="keep" checked> ${esc(tr('fk.tls.keep'))}</label>` : ''}
          <label class="fk-check tip" data-tip="${esc(tr('fk.t.tlsCa'))}"><input type="radio" name="tls" value="ca" ${edit ? '' : 'checked'}> ${esc(tr('fk.tls.ca'))}</label>
          <label class="fk-check tip" data-tip="${esc(tr('fk.t.tlsInsecure'))}"><input type="radio" name="tls" value="insecure"> ${esc(tr('fk.tls.insecure'))}</label>
          ${field('cacert', tr('vi.f.ca'), '<textarea name="cacert" rows="4" class="adv-code" placeholder="-----BEGIN CERTIFICATE-----"></textarea>', tr('fk.t.cacert'))}
        </fieldset>
      </div>
      <p class="form-hint" data-fk-from-hint hidden>${esc(tr('fk.fromHint'))}</p>
      ${field('vddk_image', tr('fk.f.vddk'), `<input name="vddk_image" value="${esc(vddk)}">`, tr('fk.t.vddk'))}`, 600);
    const sync = () => {
      const from = form.querySelector('[name="from"]');
      const on = !!(from && from.value);
      form.querySelector('[data-fk-typed]').hidden = on;
      form.querySelector('[data-fk-from-hint]').hidden = !on;
      form.querySelectorAll('[data-fk-typed] [required]').forEach(x => { x.disabled = on; });
      const ca = form.querySelector('[name="tls"]:checked').value === 'ca';
      form.querySelector('[data-f="cacert"]').hidden = !ca;
      if (on && !form.querySelector('[name="name"]').value) form.querySelector('[name="name"]').value = from.value.split('/')[1];
    };
    form.addEventListener('change', sync);
    sync();
    submitWith(form, (f) => {
      const v = (n) => { const x = f.querySelector(`[name="${n}"]`); return x ? x.value.trim() : ''; };
      const spec = { name: v('name') };
      const from = v('from');
      if (from) {
        const [namespace, sname] = from.split('/');
        spec.from_vmimport = { namespace, name: sname };
      } else {
        spec.url = v('url');
        spec.user = v('user');
        spec.password = f.querySelector('[name="password"]').value;
        const tls = f.querySelector('[name="tls"]:checked').value;
        if (tls === 'insecure') spec.insecure = true;
        else if (tls === 'ca') { if (v('cacert')) spec.cacert = v('cacert'); else throw new Error(tr('fk.needCa')); }
        // « keep » : ni cacert ni insecure, le serveur reprend le réglage TLS du secret du fournisseur
        if (edit) spec.keep_credentials = true;
      }
      if (v('vddk_image')) spec.vddk_image = v('vddk_image');
      return spec;
    }, 'provider-apply', (s) => tr('fk.done.source', { name: s.name }));
  }

  // -- Inventaire (U4) ----------------------------------------------------
  const CONCERN = () => ({
    'Changed Block Tracking (CBT) not enabled': tr('fk.c.cbt'),
    'Empty Host Name': tr('fk.c.hostName'),
    'Unsupported operating system detected': tr('fk.c.os'),
    'CPU/Memory hotplug detected': tr('fk.c.hotplug'),
    'Disk serial numbers may be truncated': tr('fk.c.serial'),
    'Shareable disk detected': tr('fk.c.shareable'),
    'RDM disk detected': tr('fk.c.rdm'),
    'VM snapshot detected': tr('fk.c.snapshot'),
  });
  const concernText = (c) => {
    const m = /^Disk - (\S+) does not have CBT enabled$/.exec(c.label || '');
    return m ? tr('fk.c.diskCbt', { disk: m[1] }) : (CONCERN()[c.label] || c.label);
  };
  const SEV = { Critical: 'fail', Warning: 'warn', Information: 'info' };
  // Clés en toutes lettres : le contrôle de parité ne lit que des littéraux.
  const INV_KINDS = ['vms', 'networks', 'datastores'];
  const INV_LABEL = { vms: () => tr('fk.inv.vms'), networks: () => tr('fk.inv.networks'), datastores: () => tr('fk.inv.datastores') };
  const INV_TIP = { vms: () => tr('fk.t.inv.vms'), networks: () => tr('fk.t.inv.networks'), datastores: () => tr('fk.t.inv.datastores') };

  function openInventory(name) {
    lastInv = { cluster: cur ? cur.cluster : (window.App && App.getCurrentCluster()), source: name, kind: 'vms', q: '', warm: false };
    Sections.open('forklift', 'inventory');
  }

  async function inventoryView(body, d) {
    const ready = d.providers.filter(p => p.ready === true);
    if (!ready.length) {
      body.innerHTML = `<p class="form-hint">${esc(tr('fk.inv.noSource'))}</p>`;
      return;
    }
    if (!lastInv || lastInv.cluster !== cur.cluster || !ready.some(p => p.name === lastInv.source)) {
      lastInv = { cluster: cur.cluster, source: ready[0].name, kind: 'vms', q: '', warm: false };
    }
    const s = lastInv;
    body.innerHTML = `<div class="fk-inv-tools">
        ${field('source', tr('fk.inv.source'), `<select name="source">${opts(ready.map(p => p.name), s.source)}</select>`, tr('fk.t.invSource'))}
        <div class="sub-tabs sub-tabs-inline">${INV_KINDS.map(k => `<button type="button" class="sub-tab tip ${k === s.kind ? 'active' : ''}" data-fk="inv-kind" data-kind="${k}" data-tip="${esc(INV_TIP[k]())}">${esc(INV_LABEL[k]())}</button>`).join('')}</div>
        <input name="q" class="tip" data-tip="${esc(tr('fk.t.search'))}" placeholder="${esc(tr('fk.search'))}" value="${esc(s.q)}">
        ${s.kind === 'vms' ? `<label class="fk-check tip" data-tip="${esc(tr('fk.t.warmOnly'))}"><input type="checkbox" name="warm_only" ${s.warm ? 'checked' : ''}> ${esc(tr('fk.warmOnly'))}</label>` : ''}
        <button type="button" class="btn btn-sm btn-secondary tip" data-fk="inv-refresh" data-tip="${esc(tr('fk.t.invRefresh'))}">${icon('refresh')}</button>
      </div><div data-fk="inv-out"><p class="form-hint">${esc(tr('common.loading'))}</p></div>`;
    const c = cur;
    let res;
    try { res = await call('GET', `/api/forklift/${enc(c.cluster)}/inventory/${enc(s.source)}/${s.kind}`); }
    catch (err) { if (c === cur) body.querySelector('[data-fk="inv-out"]').innerHTML = `<p class="res-error">${esc(err.message)}</p>`; return; }
    if (c !== cur) return;
    c.invRows = res.rows || [];
    paintInventory();
  }

  function paintInventory() {
    const out = cur.host.querySelector('[data-fk="inv-out"]');
    if (!out) return;
    const s = lastInv;
    const q = s.q.toLowerCase();
    let rows = (cur.invRows || []).filter(r => !q || `${r.name} ${r.path || ''} ${r.guest || ''}`.toLowerCase().includes(q));
    if (s.kind === 'vms' && s.warm) rows = rows.filter(r => r.cbt);
    cur.host.querySelector('.res-count').textContent = tr('fk.inv.count', { n: rows.length });
    if (s.kind === 'networks') {
      out.innerHTML = `<table class="res-table" data-fk="inv-table"><thead><tr><th>${esc(tr('fk.inv.name'))}</th><th>${esc(tr('fk.inv.path'))}</th></tr></thead>
        <tbody>${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.path)}</td></tr>`).join('')}</tbody></table>`;
      return;
    }
    if (s.kind === 'datastores') {
      out.innerHTML = `<table class="res-table" data-fk="inv-table"><thead><tr><th>${esc(tr('fk.inv.name'))}</th><th>${esc(tr('fk.inv.capacity'))}</th><th>${esc(tr('fk.inv.free'))}</th></tr></thead>
        <tbody>${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${size(r.capacity)}</td><td>${size(r.free)}</td></tr>`).join('')}</tbody></table>`;
      return;
    }
    out.innerHTML = `<table class="res-table" data-fk="inv-table"><thead><tr>
        <th>${esc(tr('fk.inv.vm'))}</th><th>${esc(tr('fk.inv.power'))}</th><th>${esc(tr('fk.inv.os'))}</th>
        <th>${esc(tr('fk.inv.cpuMem'))}</th><th>${esc(tr('fk.inv.disks'))}</th><th>${esc(tr('fk.inv.warm'))}</th><th>${esc(tr('fk.inv.concerns'))}</th></tr></thead>
      <tbody>${rows.map(r => {
        const total = (r.disks || []).reduce((a, x) => a + (x.capacity || 0), 0);
        const cs = r.concerns || [];
        const shown = cs.filter(c => !/^Disk - /.test(c.label || '')).slice(0, 2);
        const rest = cs.length - shown.length;
        return `<tr data-vm="${esc(r.name)}"><td class="tip" data-tip="${esc(r.path || '')}">${esc(r.name)}</td>
          <td>${esc(r.power === 'poweredOn' ? tr('fk.inv.on') : r.power === 'poweredOff' ? tr('fk.inv.off') : r.power || '')}</td>
          <td>${esc(r.guest || '')}</td><td>${esc(`${r.cpus || '?'} / ${size((r.memory_mib || 0) * 1048576)}`)}</td>
          <td>${esc(`${(r.disks || []).length} · ${size(total)}`)}</td>
          <td>${r.cbt ? `<span class="tip" data-fk-warm="yes" data-tip="${esc(tr('fk.t.cbtOn'))}">${icon('ok')} ${esc(tr('fk.inv.cbtOn'))}</span>`
                      : `<span class="tip" data-fk-warm="no" data-tip="${esc(tr('fk.t.cbtOff'))}">${icon('fail')} ${esc(tr('fk.inv.cbtOff'))}</span>`}</td>
          <td class="fk-concerns">${shown.map(c => `<span class="tip" data-tip="${esc(c.label)}">${icon(SEV[c.category] || 'info', 12)} ${esc(concernText(c))}</span>`).join(' ')}
            ${rest > 0 ? `<span class="badge tip" data-tip="${esc(cs.map(concernText).join('\n'))}">+${rest}</span>` : ''}</td></tr>`;
      }).join('')}</tbody></table>`;
  }

  // -- Gestes -----------------------------------------------------------------
  function onClick(e) {
    const b = e.target.closest('[data-fk]');
    if (!b || !cur) return;
    const act = b.dataset.fk;
    if (!cur.data && act !== 'refresh') {
      // clic avant l'arrivée des données : on le rejoue une fois lues
      const c = cur;
      return c.ready && c.ready.then(() => { if (c === cur && cur.data) onClick(e); }, () => {});
    }
    if (act === 'refresh') return load();
    if (act === 'install') return post('install', {}, tr('fk.done.install'));
    if (act === 'goto-sources') return Sections.open('forklift', 'sources');
    if (act === 'goto-prep') return Sections.open('forklift', 'prep');
    if (act === 'upload-vddk') return cur.host.querySelector('[data-fk="upload-file"]').click();
    if (act === 'push-vddk') {
      const box = b.closest('[data-fk-step="vddk"]');
      const val = (n) => { const x = box.querySelector(`[name="${n}"]`); return x ? x.value.trim() : ''; };
      const chk = (n) => { const x = box.querySelector(`[name="${n}"]`); return !!(x && x.checked); };
      const body = { archive: val('archive'), image: val('image'), plain_http: chk('plain_http') };
      if (chk('use_cluster_auth')) body.use_cluster_auth = true;
      else if (val('username')) { body.username = val('username'); body.password = box.querySelector('[name="password"]').value; }
      return post('vddk-image', body, tr('fk.done.push', { image: body.image }));
    }
    if (act === 'new-source') return sourceForm(null);
    const name = b.dataset.name;
    const prov = name && (cur.data.providers || []).find(p => p.name === name);
    if (act === 'edit-source' && prov) return sourceForm(prov);
    if (act === 'del-source' && prov) {
      if (!confirm(tr('fk.confirm.del', { name }))) return;
      return post('provider-delete', { name }, tr('ml.done.delete', { name }));
    }
    if (act === 'inv-source' && prov) return openInventory(name);
    if (act === 'inv-kind') { lastInv.kind = b.dataset.kind; return render(); }
    if (act === 'inv-refresh') return render();
  }

  function onChange(e) {
    const t = e.target;
    if (t.name === 'use_cluster_auth') {
      const box = t.closest('.fk-form');
      const creds = box && box.querySelector('[data-fk="reg-creds"]');
      if (creds) creds.hidden = t.checked;
    } else if (t.dataset.fk === 'upload-file' && t.files[0]) {
      upload(t.files[0]);
    } else if (t.name === 'source' && lastInv) {
      lastInv.source = t.value;
      render();
    } else if (t.name === 'warm_only' && lastInv) {
      lastInv.warm = t.checked;
      paintInventory();
    }
  }

  function onInput(e) {
    if (e.target.name === 'q' && lastInv) {
      lastInv.q = e.target.value;
      paintInventory();
    }
  }

  function upload(file) {
    const line = cur.host.querySelector('[data-fk="upload-line"]');
    if (!/^VMware-vix-disklib-\d+\.\d+\.\d+-\d+\.x86_64\.tar\.gz$/.test(file.name)) {
      line.innerHTML = `<span class="res-error">${esc(tr('fk.badArchive'))}</span>`;
      return;
    }
    const c = cur;
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', `/api/forklift-vddk/${enc(file.name)}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => { line.textContent = tr('fk.uploading', { pct: Math.floor(100 * e.loaded / (e.total || file.size)) }); };
    xhr.upload.onload = () => { line.textContent = tr('fk.verifying'); };
    xhr.onload = () => {
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch { /* sans corps */ }
      if (window.Dock && Dock.poll) Dock.poll();
      line.innerHTML = xhr.status === 201 ? `${icon('ok')} ${esc(tr('fk.uploaded', { name: file.name }))}`
                                          : `<span class="res-error">${esc(d.error || `HTTP ${xhr.status}`)}</span>`;
      if (c === cur) load();
    };
    xhr.onerror = () => { line.innerHTML = `<span class="res-error">${esc(tr('fk.uploadFailed'))}</span>`; };
    xhr.send(file);
  }

  // -- Actions ------------------------------------------------------------
  async function post(action, body, doneText, into) {
    const c = cur;
    const msg = into || (c && c.host.querySelector('[data-fk="feedback"]'));
    try {
      const out = await call('POST', `/api/forklift/${enc(c.cluster)}/do/${action}`, body);
      follow(out.action_id, msg, doneText, () => { if (c === cur) setTimeout(load, 1500); });
      if (c === cur) setTimeout(load, 2500);
      return true;
    } catch (err) {
      if (msg) msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      return false;
    }
  }

  // -- Fenêtres (posées ici pour U4 : sources vCenter) -------------------------
  function win(id, title, bodyHtml, height = 640) {
    const panel = FloatingPanels.open({ id, icon: 'upload', width: 720, height, title: `${title} · ${cur.cluster}`,
      bodyHtml: `<form class="of-form" autocomplete="off">${bodyHtml}
        <div class="bk-form-actions"><button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('bk.submitTip'))}">${icon('ok')} ${esc(tr('na.save'))}</button></div>
        <div class="of-msg" role="status"></div></form>` });
    return panel.el.querySelector('.of-form');
  }
  const field = (name, label, input, tip, cls = '') => `<label class="bk-field of-field ${cls}" data-f="${name}"><span>${esc(label)}</span>${
    input.replace(/^<(input|select|textarea)/, `<$1 class="tip" data-tip="${esc(tip || '')}"`)}</label>`;
  const opts = (list, sel) => list.map(v => (Array.isArray(v) ? v : [v, v]))
    .map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(sel) ? 'selected' : ''}>${esc(l)}</option>`).join('');

  function submitWith(form, build, action, doneText) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = form.querySelector('.of-msg');
      let spec;
      try { spec = build(form); } catch (err) { msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`; return; }
      await post(action, { spec }, doneText(spec), msg);
    });
  }

  return { start, stop, openInventory };
})();
window.Forklift = Forklift;
