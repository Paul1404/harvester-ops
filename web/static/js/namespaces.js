/**
 * harvester-ops : les namespaces, comme le menu Namespaces de Harvester (v1.62.0)
 *
 * Une fenêtre par cluster : la liste (description, VMs, volumes, quota
 * d'instantanés), créer, modifier (description, labels, annotations, quota),
 * le YAML, supprimer en tapant le nom (avec ce qui part avec). Les namespaces
 * du système sont cachés par défaut et ne se suppriment pas. Chaque geste est
 * une action suivie, par bin/harvester-resources.py namespace.
 *
 * v1.72.0 : les projets Rancher, comme la page Projects/Namespaces de
 * Harvester sous Rancher. Le projet d'un namespace est lu dans son
 * annotation ; une annotation d'un autre cluster (vu sur harv1) est dite.
 * Créer, modifier, supprimer un projet, déplacer un namespace et son quota
 * passent par Rancher avec le jeton de la personne (connexion par Rancher) ;
 * un compte local voit le rangement sans pouvoir le changer.
 */
const Namespaces = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 14) => (window.Icons ? Icons.svg(n, { size }) : '');
  const WINS = new Map();
  const GI = 1024 ** 3;

  async function call(method, url, body) {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' },
                                 body: body ? JSON.stringify(body) : undefined });
    let d = {};
    try { d = await r.json(); } catch { /* sans corps */ }
    if (!r.ok) throw new Error(d.hint || d.error || `HTTP ${r.status}`);
    return d;
  }

  const gib = (b) => (b ? `${Math.round((b / GI) * 10) / 10} Gi` : '-');

  function follow(w, id, doneText) {
    const last = w.root.querySelector('[data-nsw="last"]');
    if (window.VMActions && VMActions.follow) {
      VMActions.follow(id, last, doneText, (ok) => { if (ok) load(w); if (window.App && App.refreshNamespaces) App.refreshNamespaces(); });
    } else {
      last.textContent = tr('bk.started', { id });
      setTimeout(() => load(w), 3000);
    }
  }

  async function load(w) {
    const list = w.root.querySelector('[data-nsw="list"]');
    try {
      const d = await call('GET', `/api/ns-admin/${enc(w.cluster)}`);
      w.rows = d.items || [];
      w.projects = d.projects || { managed: false, items: null };
      w.quotaKeys = d.quota_keys || [];
      w.limitKeys = d.limit_keys || [];
      render(w);
    } catch (e) {
      list.innerHTML = `<p class="res-error">${esc(e.message)}</p>`;
    }
  }

  // -- projets (v1.72.0) ----------------------------------------------------------
  const projectsOf = (w) => (w.projects && w.projects.items) || [];
  const projectById = (w, id) => projectsOf(w).find(p => p.id === id);
  function projectBadge(w, pr) {
    if (!pr || pr.state === 'none') return `<span class="res-dim tip" data-tip="${esc(tr('pj.t.none'))}">${esc(tr('pj.none'))}</span>`;
    if (pr.state === 'foreign') {
      return `<span class="badge warn tip" data-tip="${esc(tr('pj.t.foreign', { cluster: pr.cluster, project: pr.project }))}">${esc(tr('pj.foreign'))}</span>`;
    }
    if (pr.state === 'unknown') return `<span class="badge warn tip" data-tip="${esc(tr('pj.t.unknown', { project: pr.project }))}">${esc(pr.project)}</span>`;
    const q = Object.keys(pr.quota || {}).length;
    return `<span class="badge info">${esc(pr.name || pr.project)}</span>${q ? ` <span class="res-dim tip" data-tip="${esc(tr('pj.t.nsQuota'))}">${icon('lock', 12)}</span>` : ''}
      ${pr.quota_ok === false ? ` <span class="badge fail tip" data-tip="${esc(pr.quota_message)}">${esc(tr('pj.quotaRefused'))}</span>` : ''}`;
  }
  const fmtLimits = (o) => Object.entries(o || {}).map(([k, v]) => `${k} ${v}`).join(', ') || '–';

  function renderProjects(w) {
    const list = w.root.querySelector('[data-nsw="list"]');
    const pj = w.projects || {};
    if (!pj.managed) {
      const ids = [...new Set((w.rows || []).map(r => r.project).filter(p => p && p.project).map(p => `${p.cluster || '?'}:${p.project}`))];
      list.innerHTML = `<div class="sto-finding sev-action"><div class="sto-finding-title">${icon('info')} ${esc(tr('pj.local'))}</div></div>
        ${ids.length ? `<p class="form-hint">${esc(tr('pj.localIds'))}</p><p>${ids.map(i => `<code class="res-key">${esc(i)}</code>`).join(' ')}</p>` : ''}`;
      return;
    }
    if (pj.error) { list.innerHTML = `<p class="res-error">${esc(pj.error)}</p>`; return; }
    const count = (id) => (w.rows || []).filter(r => r.project && r.project.project === id && r.project.state === 'member').length;
    const foreign = (w.rows || []).filter(r => r.project && r.project.state === 'foreign');
    list.innerHTML = `${foreign.length ? `<div class="sto-finding sev-action"><div class="sto-finding-title">${icon('warn')} ${esc(tr('pj.foreignNs', { n: foreign.length }))}</div>
        <p class="form-hint">${esc(foreign.map(r => r.name).join(', '))}</p></div>` : ''}
      <table class="data-table nsw-table"><thead><tr><th>${esc(tr('pj.col.name'))}</th><th>${esc(tr('pj.col.quota'))}</th>
        <th>${esc(tr('pj.col.nsDefault'))}</th><th>${esc(tr('pj.col.vmLimit'))}</th><th class="num">${esc(tr('pj.col.namespaces'))}</th><th></th></tr></thead>
      <tbody>${projectsOf(w).map(p => { const n = count(p.id); const locked = p.system || p.default; return `<tr data-pj="${esc(p.id)}">
        <td><strong>${esc(p.name)}</strong> <span class="res-dim">${esc(p.id)}</span>${p.system ? ` <span class="badge">${esc(tr('nsw.system'))}</span>` : ''}
          ${p.description ? `<div class="res-dim">${esc(p.description)}</div>` : ''}</td>
        <td>${esc(fmtLimits(p.quota))}${Object.keys(p.used || {}).length ? `<div class="res-dim tip" data-tip="${esc(tr('pj.t.used'))}">${esc(tr('pj.used'))} ${esc(fmtLimits(p.used))}</div>` : ''}</td>
        <td>${esc(fmtLimits(p.ns_default))}</td><td>${esc(fmtLimits(p.container))}</td><td class="num">${esc(n)}</td>
        <td class="nsw-acts">
          <button type="button" class="btn-icon-sm tip" data-pj-act="edit" data-tip="${esc(tr('pj.t.edit'))}">${icon('edit')}</button>
          <button type="button" class="btn-icon-sm tip" data-pj-act="members" data-tip="${esc(tr('rm.t.projectMembers'))}">${icon('user')}</button>
          <button type="button" class="btn-icon-sm tip" data-pj-act="delete" ${locked || n ? 'disabled' : ''}
            data-tip="${esc(locked ? tr('pj.t.locked') : n ? tr('pj.t.notEmpty') : tr('pj.t.delete'))}">${icon('trash')}</button></td></tr>`; }).join('')}</tbody></table>`;
  }

  function render(w) {
    if (w.tab === 'projects') { renderProjects(w); return; }
    const list = w.root.querySelector('[data-nsw="list"]');
    const all = w.root.querySelector('[data-nsw="system"]').checked;
    const rows = (w.rows || []).filter(r => all || !r.system);
    const hidden = (w.rows || []).length - rows.length;
    list.innerHTML = `<table class="data-table nsw-table"><thead><tr>
        <th>${esc(tr('nsw.col.name'))}</th><th>${esc(tr('nsw.col.description'))}</th>
        <th class="num">${esc(tr('nsw.col.vms'))}</th><th class="num">${esc(tr('nsw.col.volumes'))}</th>
        <th class="num">${esc(tr('nsw.col.quota'))}</th><th>${esc(tr('pj.col.project'))}</th><th>${esc(tr('nsw.col.created'))}</th><th></th></tr></thead>
      <tbody>${rows.map(r => `<tr data-ns="${esc(r.name)}">
        <td><strong>${esc(r.name)}</strong>${r.system ? ` <span class="badge tip" data-tip="${esc(tr('nsw.tip.system'))}">${esc(tr('nsw.system'))}</span>` : ''}
          ${r.phase && r.phase !== 'Active' ? ` <span class="badge warn">${esc(r.phase)}</span>` : ''}</td>
        <td>${esc(r.description)}</td><td class="num">${esc(r.vms)}</td><td class="num">${esc(r.volumes)}</td>
        <td class="num">${esc(gib(r.snapshot_quota))}</td><td>${projectBadge(w, r.project)}</td><td>${esc((r.created || '').slice(0, 10))}</td>
        <td class="nsw-acts">
          ${w.projects && w.projects.managed && !r.system ? `<button type="button" class="btn-icon-sm tip" data-nsw-act="move" data-tip="${esc(tr('pj.t.move'))}">${icon('migrate')}</button>` : ''}
          <button type="button" class="btn-icon-sm tip" data-nsw-act="edit" data-tip="${esc(tr('nsw.tip.edit'))}">${icon('edit')}</button>
          <button type="button" class="btn-icon-sm tip" data-nsw-act="yaml" data-tip="${esc(tr('nsw.tip.yaml'))}">${icon('code')}</button>
          ${r.system || r.protected ? '' : `<button type="button" class="btn-icon-sm tip" data-nsw-act="delete" data-tip="${esc(tr('nsw.tip.delete'))}">${icon('trash')}</button>`}
        </td></tr>`).join('')}</tbody></table>
      ${hidden ? `<p class="form-hint">${esc(tr('nsw.hidden', { n: hidden }))}</p>` : ''}`;
  }

  function kvRows(obj) {
    return Object.entries(obj || {}).map(([k, v]) => kvRow(k, v)).join('');
  }
  function kvRow(k = '', v = '') {
    return `<div class="vm-kv-row" data-nsw-kv>
      <input type="text" data-kv="key" value="${esc(k)}" placeholder="${esc(tr('vm.edit.kvKey'))}" class="tip" data-tip="${esc(tr('vm.edit.tKvKey'))}">
      <input type="text" data-kv="value" value="${esc(v)}" placeholder="${esc(tr('vm.edit.kvValue'))}" class="tip" data-tip="${esc(tr('vm.edit.tKvValue'))}">
      <button type="button" class="btn-icon-sm tip" data-kv-del data-tip="${esc(tr('vm.edit.tKvDel'))}">×</button></div>`;
  }
  function kvRead(box) {
    const out = {};
    box.querySelectorAll('[data-nsw-kv]').forEach(r => {
      const k = r.querySelector('[data-kv="key"]').value.trim();
      if (!k) return;
      if (k in out) throw new Error(`${tr('vm.edit.errTagDup')}: ${k}`);
      out[k] = r.querySelector('[data-kv="value"]').value;
    });
    return out;
  }
  const kvBox = (kind, title, obj) => `<details class="vm-edit-adv" ${Object.keys(obj || {}).length ? 'open' : ''}>
      <summary>${esc(title)} <span class="res-dim">(${Object.keys(obj || {}).length})</span></summary>
      <div class="vm-kv-rows" data-nsw-box="${kind}">${kvRows(obj)}</div>
      <button type="button" class="btn btn-sm btn-secondary tip" data-nsw-add="${kind}" data-tip="${esc(tr('vm.edit.tKvAdd'))}">${icon('add', 13)} ${esc(tr('vm.edit.kvAdd'))}</button></details>`;

  /** Le formulaire de création ou de modification, dans la fenêtre. */
  function form(w, row) {
    const host = w.root.querySelector('[data-nsw="form"]');
    const edit = !!row;
    host.hidden = false;
    host.innerHTML = `<form class="of-form nsw-form" autocomplete="off">
      <h4 class="hs-sub">${esc(edit ? tr('nsw.editTitle', { name: row.name }) : tr('nsw.newTitle'))}</h4>
      <div class="hs-grid">
        <label class="bk-field"><span>${esc(tr('nsw.col.name'))}</span><input type="text" name="name" value="${esc(edit ? row.name : '')}"
          ${edit ? 'disabled' : 'required'} pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" maxlength="63" class="tip" data-tip="${esc(tr('nsw.tip.name'))}"></label>
        <label class="bk-field"><span>${esc(tr('nsw.col.description'))}</span><input type="text" name="description" maxlength="1000"
          value="${esc(edit ? row.description : '')}" class="tip" data-tip="${esc(tr('nsw.tip.description'))}"></label>
        ${edit ? `<label class="bk-field"><span>${esc(tr('nsw.quota'))}</span><input type="number" name="quota" min="0" step="1"
          value="${esc(row.snapshot_quota ? Math.round(row.snapshot_quota / GI) : 0)}" class="tip" data-tip="${esc(tr('nsw.tip.quota'))}"></label>` : ''}
        ${!edit && w.projects && w.projects.managed ? `<label class="bk-field"><span>${esc(tr('pj.col.project'))}</span><select name="project" class="tip" data-tip="${esc(tr('pj.t.newIn'))}">
          <option value="">${esc(tr('pj.none'))}</option>${projectsOf(w).filter(p => !p.system).map(p => `<option value="${esc(p.id)}" ${p.default ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>` : ''}
      </div>
      ${edit ? nsQuotaBox(w, row) : ''}
      ${kvBox('labels', tr('nsw.labels'), edit ? row.labels : {})}
      ${edit ? kvBox('annotations', tr('nsw.annotations'), row.annotations) : ''}
      <div class="bk-form-actions">
        <button type="button" class="btn btn-sm btn-secondary tip" data-nsw="cancel" data-tip="${esc(tr('nsw.tip.cancel'))}">${esc(tr('nsw.cancel'))}</button>
        <button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('nsw.tip.save'))}">${icon('save')} ${esc(edit ? tr('nsw.save') : tr('nsw.create'))}</button>
      </div><div class="of-msg" role="status"></div></form>`;
    const f = host.querySelector('form');
    f.addEventListener('click', (e) => {
      const add = e.target.closest('[data-nsw-add]');
      if (add) f.querySelector(`[data-nsw-box="${add.dataset.nswAdd}"]`).insertAdjacentHTML('beforeend', kvRow());
      if (e.target.closest('[data-kv-del]')) e.target.closest('[data-nsw-kv]').remove();
      if (e.target.closest('[data-nsw="cancel"]')) { host.hidden = true; host.innerHTML = ''; }
    });
    (edit ? f.description : f.name).focus();
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = f.querySelector('.of-msg');
      const btn = f.querySelector('button[type="submit"]');
      btn.disabled = true;
      try {
        const labels = kvRead(f.querySelector('[data-nsw-box="labels"]'));
        if (!edit) {
          const name = f.name.value.trim();
          const out = await call('POST', `/api/ns-admin/${enc(w.cluster)}`, { name, description: f.description.value.trim(), labels });
          follow(w, out.action_id, tr('nsw.done.created', { name }));
          const pid = f.project ? f.project.value : '';
          if (pid) {
            // le namespace doit exister avant d'entrer dans le projet
            await waitAction(out.action_id);
            const o2 = await call('POST', `/api/projects/${enc(w.cluster)}/do/move`, { namespace: name, id: pid });
            follow(w, o2.action_id, tr('pj.done.moved', { name }));
          }
        } else {
          const annotations = kvRead(f.querySelector('[data-nsw-box="annotations"]'));
          const out = await call('POST', `/api/ns-admin/${enc(w.cluster)}/${enc(row.name)}/do/update`,
            { description: f.description.value.trim(), labels, annotations });
          follow(w, out.action_id, tr('nsw.done.saved', { name: row.name }));
          const q = Number(f.quota.value || 0);
          const before = row.snapshot_quota ? Math.round(row.snapshot_quota / GI) : 0;
          if (q !== before) {
            const o2 = await call('POST', `/api/ns-admin/${enc(w.cluster)}/${enc(row.name)}/do/quota`, { size: q ? `${q}Gi` : '0' });
            follow(w, o2.action_id, tr('nsw.done.quota', { name: row.name }));
          }
          const box = f.querySelector('[data-pj-nsq]');
          if (box) {
            const limit = {};
            box.querySelectorAll('input[data-key]').forEach(i => { if (i.value.trim()) limit[i.dataset.key] = i.value.trim(); });
            const was = (row.project && row.project.quota) || {};
            if (JSON.stringify(limit) !== JSON.stringify(Object.fromEntries(Object.entries(was).filter(([, v]) => v)))) {
              const o3 = await call('POST', `/api/projects/${enc(w.cluster)}/do/ns-quota`, { namespace: row.name, limit });
              follow(w, o3.action_id, tr('pj.done.nsQuota', { name: row.name }));
            }
          }
        }
        host.hidden = true;
        host.innerHTML = '';
      } catch (err) {
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
        btn.disabled = false;
      }
    });
  }

  /** Le quota du namespace dans son projet : seulement ce que le projet limite. */
  function nsQuotaBox(w, row) {
    const pr = row.project || {};
    const p = pr.state === 'member' && w.projects && w.projects.managed ? projectById(w, pr.project) : null;
    if (!p || !Object.keys(p.quota || {}).length) return '';
    return `<fieldset class="ml-rec" data-pj-nsq><legend>${esc(tr('pj.nsQuota', { project: p.name }))}</legend>
      <p class="form-hint">${esc(tr('pj.nsQuotaHint'))}</p><div class="hs-grid">
      ${Object.keys(p.quota).map(k => `<label class="bk-field"><span>${esc(k)}</span><input type="text" data-key="${esc(k)}"
        value="${esc((pr.quota || {})[k] || '')}" placeholder="${esc((p.ns_default || {})[k] || '')}" class="tip"
        data-tip="${esc(tr('pj.t.nsQuotaKey', { limit: p.quota[k] }))}"></label>`).join('')}</div></fieldset>`;
  }

  async function waitAction(id) {
    for (let i = 0; i < 60; i++) {
      try {
        const a = await call('GET', `/api/action/${enc(id)}`);
        if (a.status && !['running', 'starting'].includes(a.status)) return a.status;
      } catch { /* on réessaie */ }
      await new Promise(r => setTimeout(r, 1000));
    }
    return 'timeout';
  }

  /** Déplacer un namespace vers un projet, ou hors de tout projet. */
  function move(w, row) {
    const host = w.root.querySelector('[data-nsw="form"]');
    host.hidden = false;
    const cur = row.project && row.project.state === 'member' ? row.project.project : '';
    host.innerHTML = `<form class="of-form nsw-form" autocomplete="off">
      <h4 class="hs-sub">${esc(tr('pj.moveTitle', { name: row.name }))}</h4>
      ${row.project && row.project.state === 'foreign' ? `<p class="form-hint">${esc(tr('pj.t.foreign', { cluster: row.project.cluster, project: row.project.project }))}</p>` : ''}
      <label class="bk-field"><span>${esc(tr('pj.col.project'))}</span><select name="project" class="tip" data-tip="${esc(tr('pj.t.moveTo'))}">
        <option value="">${esc(tr('pj.none'))}</option>${projectsOf(w).filter(p => !p.system).map(p => `<option value="${esc(p.id)}" ${p.id === cur ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>
      <p class="form-hint">${esc(tr('pj.moveHint'))}</p>
      <div class="bk-form-actions">
        <button type="button" class="btn btn-sm btn-secondary tip" data-nsw="cancel" data-tip="${esc(tr('nsw.tip.cancel'))}">${esc(tr('nsw.cancel'))}</button>
        <button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('pj.t.move'))}">${icon('migrate')} ${esc(tr('pj.move'))}</button>
      </div><div class="of-msg" role="status"></div></form>`;
    const f = host.querySelector('form');
    f.querySelector('[data-nsw="cancel"]').addEventListener('click', () => { host.hidden = true; host.innerHTML = ''; });
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        const out = await call('POST', `/api/projects/${enc(w.cluster)}/do/move`, { namespace: row.name, id: f.project.value || null });
        follow(w, out.action_id, tr('pj.done.moved', { name: row.name }));
        host.hidden = true;
        host.innerHTML = '';
      } catch (err) {
        f.querySelector('.of-msg').innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      }
    });
  }

  /** Un projet : nom, description, quotas (projet et défaut des namespaces), limite par défaut des VMs. */
  function projectForm(w, p) {
    const host = w.root.querySelector('[data-nsw="form"]');
    host.hidden = false;
    const edit = !!p;
    const keys = w.quotaKeys || [];
    const qrow = (k = '', lim = '', def = '') => `<div class="pj-qrow" data-pj-q>
      <select data-q="key" class="tip" data-tip="${esc(tr('pj.t.qKey'))}">${keys.map(x => `<option value="${esc(x)}" ${x === k ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>
      <input type="text" data-q="limit" value="${esc(lim)}" placeholder="${esc(tr('pj.qLimitPh'))}" class="tip" data-tip="${esc(tr('pj.t.qLimit'))}">
      <input type="text" data-q="default" value="${esc(def)}" placeholder="${esc(tr('pj.qDefaultPh'))}" class="tip" data-tip="${esc(tr('pj.t.qDefault'))}">
      <button type="button" class="btn-icon-sm tip" data-q-del data-tip="${esc(tr('vm.edit.tKvDel'))}">×</button></div>`;
    host.innerHTML = `<form class="of-form nsw-form" autocomplete="off">
      <h4 class="hs-sub">${esc(edit ? tr('pj.editTitle', { name: p.name }) : tr('pj.newTitle'))}</h4>
      <div class="hs-grid">
        <label class="bk-field"><span>${esc(tr('pj.col.name'))}</span><input type="text" name="name" required maxlength="63" value="${esc(edit ? p.name : '')}" class="tip" data-tip="${esc(tr('pj.t.name'))}"></label>
        <label class="bk-field"><span>${esc(tr('nsw.col.description'))}</span><input type="text" name="description" value="${esc(edit ? p.description : '')}" class="tip" data-tip="${esc(tr('nsw.tip.description'))}"></label>
      </div>
      <h4 class="hs-sub">${esc(tr('pj.quotas'))}</h4><p class="form-hint">${esc(tr('pj.quotasHint'))}</p>
      <div data-pj-qs>${edit ? Object.keys(p.quota || {}).map(k => qrow(k, p.quota[k], (p.ns_default || {})[k] || '')).join('') : ''}</div>
      <button type="button" class="btn btn-sm btn-secondary tip" data-q-add data-tip="${esc(tr('pj.t.qAdd'))}">${icon('add', 13)} ${esc(tr('pj.qAdd'))}</button>
      <h4 class="hs-sub">${esc(tr('pj.col.vmLimit'))}</h4><p class="form-hint">${esc(tr('pj.vmLimitHint'))}</p>
      <div class="hs-grid">${(w.limitKeys || []).map(k => `<label class="bk-field"><span>${esc(k)}</span><input type="text" data-limit="${esc(k)}"
        value="${esc(edit ? (p.container || {})[k] || '' : '')}" placeholder="${esc(/Cpu$/.test(k) ? '500m' : '1Gi')}" class="tip" data-tip="${esc(tr('pj.t.vmLimitKey'))}"></label>`).join('')}</div>
      <div class="bk-form-actions">
        <button type="button" class="btn btn-sm btn-secondary tip" data-nsw="cancel" data-tip="${esc(tr('nsw.tip.cancel'))}">${esc(tr('nsw.cancel'))}</button>
        <button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('pj.t.save'))}">${icon('save')} ${esc(edit ? tr('nsw.save') : tr('nsw.create'))}</button>
      </div><div class="of-msg" role="status"></div></form>`;
    const f = host.querySelector('form');
    f.addEventListener('click', (e) => {
      if (e.target.closest('[data-q-add]')) f.querySelector('[data-pj-qs]').insertAdjacentHTML('beforeend', qrow());
      if (e.target.closest('[data-q-del]')) e.target.closest('[data-pj-q]').remove();
      if (e.target.closest('[data-nsw="cancel"]')) { host.hidden = true; host.innerHTML = ''; }
    });
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = f.querySelector('.of-msg');
      const quota = {}, nsDefault = {}, container = {};
      f.querySelectorAll('[data-pj-q]').forEach(r => {
        const k = r.querySelector('[data-q="key"]').value;
        const l = r.querySelector('[data-q="limit"]').value.trim(), d = r.querySelector('[data-q="default"]').value.trim();
        if (l || d) { quota[k] = l; nsDefault[k] = d; }
      });
      f.querySelectorAll('[data-limit]').forEach(i => { if (i.value.trim()) container[i.dataset.limit] = i.value.trim(); });
      const spec = { name: f.name.value.trim(), description: f.description.value.trim(), quota, ns_default: nsDefault, container };
      try {
        const out = await call('POST', `/api/projects/${enc(w.cluster)}/do/${edit ? 'update' : 'create'}`, edit ? { id: p.id, spec } : { spec });
        follow(w, out.action_id, tr('pj.done.saved', { name: spec.name }));
        host.hidden = true;
        host.innerHTML = '';
      } catch (err) {
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      }
    });
  }

  /** Supprimer : ce qui part avec est dit, et le nom se tape (comme Harvester). */
  function remove(w, row) {
    const host = w.root.querySelector('[data-nsw="form"]');
    host.hidden = false;
    host.innerHTML = `<form class="of-form nsw-form hs-danger" autocomplete="off">
      <h4 class="hs-sub">${esc(tr('nsw.deleteTitle', { name: row.name }))}</h4>
      <p class="form-hint">${esc(tr('nsw.deleteHint', { vms: row.vms, volumes: row.volumes }))}</p>
      <label class="bk-field"><span>${esc(tr('hs.deleteConfirm', { name: row.name }))}</span>
        <input type="text" name="confirm" class="tip" data-tip="${esc(tr('nsw.tip.deleteConfirm'))}"></label>
      <div class="bk-form-actions">
        <button type="button" class="btn btn-sm btn-secondary tip" data-nsw="cancel" data-tip="${esc(tr('nsw.tip.cancel'))}">${esc(tr('nsw.cancel'))}</button>
        <button type="submit" class="btn btn-sm btn-danger tip" data-tip="${esc(tr('nsw.tip.delete'))}">${icon('trash')} ${esc(tr('hs.deleteBtn'))}</button>
      </div><div class="of-msg" role="status"></div></form>`;
    const f = host.querySelector('form');
    f.confirm.focus();
    f.querySelector('[data-nsw="cancel"]').addEventListener('click', () => { host.hidden = true; host.innerHTML = ''; });
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = f.querySelector('.of-msg');
      if (f.confirm.value.trim() !== row.name) {
        msg.innerHTML = `<span class="res-error">${esc(tr('hs.errConfirm', { name: row.name }))}</span>`;
        return;
      }
      try {
        const out = await call('POST', `/api/ns-admin/${enc(w.cluster)}/${enc(row.name)}/do/delete`, {});
        follow(w, out.action_id, tr('nsw.done.deleted', { name: row.name }));
        host.hidden = true;
        host.innerHTML = '';
      } catch (err) {
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      }
    });
  }

  function open(cluster) {
    if (!cluster) return;
    const id = `namespaces-${cluster}`;
    const panel = FloatingPanels.open({
      id, icon: 'placement', width: 900, height: 560,
      title: tr('nsw.title', { cluster }),
      restoreSpec: { type: 'namespaces', args: { cluster } },
      onClose: () => WINS.delete(id),
      bodyHtml: `<div class="nsw-win">
        <div class="sub-tabs sub-tabs-inline nsw-tabs" role="tablist">
          <button type="button" class="sub-tab tip active" role="tab" aria-selected="true" data-nsw-tab="namespaces" data-tip="${esc(tr('pj.t.tabNs'))}">${icon('placement')} <span>${esc(tr('pj.tabNs'))}</span></button>
          <button type="button" class="sub-tab tip" role="tab" aria-selected="false" data-nsw-tab="projects" data-tip="${esc(tr('pj.t.tabProjects'))}">${icon('tag')} <span>${esc(tr('pj.tabProjects'))}</span></button>
        </div>
        <div class="bk-bar" data-nsw-bar="namespaces">
          <label class="bk-check tip" data-tip="${esc(tr('nsw.tip.showSystem'))}"><input type="checkbox" data-nsw="system"> <span>${esc(tr('nsw.showSystem'))}</span></label>
          <button type="button" class="btn btn-sm btn-primary tip" data-nsw="new" data-tip="${esc(tr('nsw.tip.new'))}">${icon('add')} ${esc(tr('nsw.new'))}</button>
        </div>
        <div class="bk-bar" data-nsw-bar="projects" hidden>
          <button type="button" class="btn btn-sm btn-primary tip" data-nsw="new-project" data-tip="${esc(tr('pj.t.new'))}">${icon('add')} ${esc(tr('pj.new'))}</button>
        </div>
        <div class="hs-last" data-nsw="last" role="status" aria-live="polite"></div>
        <div data-nsw="form" hidden></div>
        <div class="nsw-list" data-nsw="list"><p class="hint">${esc(tr('hs.loading'))}</p></div>
      </div>`,
    });
    const root = panel.el;
    if (root.dataset.nswReady) return;
    root.dataset.nswReady = '1';
    const w = { id, cluster, root, rows: [], tab: 'namespaces' };
    WINS.set(id, w);
    root.querySelector('[data-nsw="system"]').addEventListener('change', () => render(w));
    root.querySelector('[data-nsw="new"]').addEventListener('click', () => form(w, null));
    root.querySelectorAll('[data-nsw-tab]').forEach(b => b.addEventListener('click', () => {
      w.tab = b.dataset.nswTab;
      root.querySelectorAll('[data-nsw-tab]').forEach(x => { const on = x === b; x.classList.toggle('active', on); x.setAttribute('aria-selected', on ? 'true' : 'false'); });
      root.querySelectorAll('[data-nsw-bar]').forEach(x => { x.hidden = x.dataset.nswBar !== w.tab; });
      const host = root.querySelector('[data-nsw="form"]');
      host.hidden = true;
      host.innerHTML = '';
      render(w);
    }));
    root.querySelector('[data-nsw="new-project"]').addEventListener('click', () => {
      if (!(w.projects && w.projects.managed)) {
        root.querySelector('[data-nsw="last"]').innerHTML = `<span class="res-error">${esc(tr('pj.local'))}</span>`;
        return;
      }
      projectForm(w, null);
    });
    root.querySelector('[data-nsw="list"]').addEventListener('click', (e) => {
      const pb = e.target.closest('[data-pj-act]');
      if (pb) {
        const p = projectById(w, pb.closest('tr').dataset.pj);
        if (!p) return;
        if (pb.dataset.pjAct === 'edit') projectForm(w, p);
        else if (pb.dataset.pjAct === 'members' && window.RancherMembers) {
          // v1.73.0 : les membres du projet, dans la zone de formulaire
          const host = root.querySelector('[data-nsw="form"]');
          host.hidden = false;
          host.innerHTML = `<div class="nsw-form"><div class="bk-bar"><button type="button" class="btn btn-sm btn-secondary tip" data-nsw="cancel"
            data-tip="${esc(tr('nsw.tip.cancel'))}">${esc(tr('rm.close'))}</button></div><div data-rm-host></div></div>`;
          host.querySelector('[data-nsw="cancel"]').addEventListener('click', () => { host.hidden = true; host.innerHTML = ''; });
          RancherMembers.render(host.querySelector('[data-rm-host]'), { cluster: w.cluster, scope: 'project', project: p.id,
            title: tr('rm.projectTitle', { name: p.name }) });
        }
        else if (pb.dataset.pjAct === 'delete' && confirm(tr('pj.confirmDelete', { name: p.name }))) {
          call('POST', `/api/projects/${enc(w.cluster)}/do/delete`, { id: p.id })
            .then(out => follow(w, out.action_id, tr('pj.done.deleted', { name: p.name })))
            .catch(err => { root.querySelector('[data-nsw="last"]').innerHTML = `<span class="res-error">${esc(err.message)}</span>`; });
        }
        return;
      }
      const b = e.target.closest('[data-nsw-act]');
      if (!b) return;
      const row = (w.rows || []).find(r => r.name === b.closest('tr').dataset.ns);
      if (!row) return;
      if (b.dataset.nswAct === 'edit') form(w, row);
      else if (b.dataset.nswAct === 'yaml' && window.YamlWindow) YamlWindow.open(cluster, 'namespace', '', row.name, { onDone: () => load(w) });
      else if (b.dataset.nswAct === 'delete') remove(w, row);
      else if (b.dataset.nswAct === 'move') move(w, row);
    });
    load(w);
  }

  if (window.FloatingPanels && FloatingPanels.registerType) {
    FloatingPanels.registerType('namespaces', (a) => open(a.cluster));
  }
  return { open };
})();
window.Namespaces = Namespaces;
