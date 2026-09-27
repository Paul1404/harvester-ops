/**
 * harvester-ops : les namespaces, comme le menu Namespaces de Harvester (v1.62.0)
 *
 * Une fenêtre par cluster : la liste (description, VMs, volumes, quota
 * d'instantanés), créer, modifier (description, labels, annotations, quota),
 * le YAML, supprimer en tapant le nom (avec ce qui part avec). Les namespaces
 * du système sont cachés par défaut et ne se suppriment pas. Chaque geste est
 * une action suivie, par bin/harvester-resources.py namespace.
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
      render(w);
    } catch (e) {
      list.innerHTML = `<p class="res-error">${esc(e.message)}</p>`;
    }
  }

  function render(w) {
    const list = w.root.querySelector('[data-nsw="list"]');
    const all = w.root.querySelector('[data-nsw="system"]').checked;
    const rows = (w.rows || []).filter(r => all || !r.system);
    const hidden = (w.rows || []).length - rows.length;
    list.innerHTML = `<table class="data-table nsw-table"><thead><tr>
        <th>${esc(tr('nsw.col.name'))}</th><th>${esc(tr('nsw.col.description'))}</th>
        <th class="num">${esc(tr('nsw.col.vms'))}</th><th class="num">${esc(tr('nsw.col.volumes'))}</th>
        <th class="num">${esc(tr('nsw.col.quota'))}</th><th>${esc(tr('nsw.col.created'))}</th><th></th></tr></thead>
      <tbody>${rows.map(r => `<tr data-ns="${esc(r.name)}">
        <td><strong>${esc(r.name)}</strong>${r.system ? ` <span class="badge tip" data-tip="${esc(tr('nsw.tip.system'))}">${esc(tr('nsw.system'))}</span>` : ''}
          ${r.phase && r.phase !== 'Active' ? ` <span class="badge warn">${esc(r.phase)}</span>` : ''}</td>
        <td>${esc(r.description)}</td><td class="num">${esc(r.vms)}</td><td class="num">${esc(r.volumes)}</td>
        <td class="num">${esc(gib(r.snapshot_quota))}</td><td>${esc((r.created || '').slice(0, 10))}</td>
        <td class="nsw-acts">
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
      </div>
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
        }
        host.hidden = true;
        host.innerHTML = '';
      } catch (err) {
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
        btn.disabled = false;
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
        <div class="bk-bar">
          <label class="bk-check tip" data-tip="${esc(tr('nsw.tip.showSystem'))}"><input type="checkbox" data-nsw="system"> <span>${esc(tr('nsw.showSystem'))}</span></label>
          <button type="button" class="btn btn-sm btn-primary tip" data-nsw="new" data-tip="${esc(tr('nsw.tip.new'))}">${icon('add')} ${esc(tr('nsw.new'))}</button>
        </div>
        <div class="hs-last" data-nsw="last" role="status" aria-live="polite"></div>
        <div data-nsw="form" hidden></div>
        <div class="nsw-list" data-nsw="list"><p class="hint">${esc(tr('hs.loading'))}</p></div>
      </div>`,
    });
    const root = panel.el;
    if (root.dataset.nswReady) return;
    root.dataset.nswReady = '1';
    const w = { id, cluster, root, rows: [] };
    WINS.set(id, w);
    root.querySelector('[data-nsw="system"]').addEventListener('change', () => render(w));
    root.querySelector('[data-nsw="new"]').addEventListener('click', () => form(w, null));
    root.querySelector('[data-nsw="list"]').addEventListener('click', (e) => {
      const b = e.target.closest('[data-nsw-act]');
      if (!b) return;
      const row = (w.rows || []).find(r => r.name === b.closest('tr').dataset.ns);
      if (!row) return;
      if (b.dataset.nswAct === 'edit') form(w, row);
      else if (b.dataset.nswAct === 'yaml' && window.YamlWindow) YamlWindow.open(cluster, 'namespace', '', row.name, { onDone: () => load(w) });
      else if (b.dataset.nswAct === 'delete') remove(w, row);
    });
    load(w);
  }

  if (window.FloatingPanels && FloatingPanels.registerType) {
    FloatingPanels.registerType('namespaces', (a) => open(a.cluster));
  }
  return { open };
})();
window.Namespaces = Namespaces;
