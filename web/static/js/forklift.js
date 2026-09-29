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
    // v1.75.0 : la création de source (U4) aura son propre bouton dans
    // l'onglet Sources vCenter, une fois sourcesView et son formulaire posés
    host.innerHTML = `<div class="card na-card fk-card">
        <div class="res-tools"><span class="res-count"></span>
          <button type="button" class="btn btn-sm btn-secondary tip" data-fk="refresh" data-tip="${esc(tr('res.refreshTip'))}">${icon('refresh')} ${esc(tr('overview.refresh'))}</button>
        </div>
        <div class="res-feedback" data-fk="feedback"></div>
        <div data-fk="body"><p class="form-hint">${esc(tr('common.loading'))}</p></div></div>`;
    const card = host.querySelector('.fk-card');
    card.addEventListener('click', onClick);
    card.addEventListener('change', onChange);
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

  // -- Sources vCenter et Inventaire : posés en U4 -----------------------------
  function sourcesView() { return `<p class="form-hint">${esc(tr('common.comingSoon'))}</p>`; }
  function inventoryView(body) { body.innerHTML = `<p class="form-hint">${esc(tr('common.comingSoon'))}</p>`; }

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
  }

  function onChange(e) {
    const t = e.target;
    if (t.name === 'use_cluster_auth') {
      const box = t.closest('.fk-form');
      const creds = box && box.querySelector('[data-fk="reg-creds"]');
      if (creds) creds.hidden = t.checked;
    } else if (t.dataset.fk === 'upload-file' && t.files[0]) {
      upload(t.files[0]);
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

  // -- Inventaire (U4) ----------------------------------------------------
  function openInventory() {
    // U3 : simple renvoi sur l'onglet ; U4 y branchera la source et la sorte
    Sections.open('forklift', 'inventory');
  }

  return { start, stop, openInventory };
})();
window.Forklift = Forklift;
