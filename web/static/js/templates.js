/**
 * harvester-ops : les modèles de VM et les modèles cloud-init, comme le menu
 * « Advanced » de Harvester (v1.64.0)
 *
 * Templates.open(cluster) : les modèles de VM et leurs versions (la plus
 * récente d'abord) ; lancer une VM depuis une version prête, choisir la
 * version par défaut, supprimer une version (pas celle par défaut) ou le
 * modèle, YAML.
 * Templates.openCloud(cluster) : les modèles cloud-init (user-data et
 * network-data) ; créer, modifier, supprimer, YAML. La création de VM les
 * propose dans sa section Cloud-init.
 * Chaque geste est une action suivie, par bin/harvester-resources.py.
 */
const Templates = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 14) => (window.Icons ? Icons.svg(n, { size }) : '');

  async function call(method, url, body) {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' },
                                 body: body ? JSON.stringify(body) : undefined });
    let d = {};
    try { d = await r.json(); } catch { /* sans corps */ }
    if (!r.ok) throw new Error(d.hint || d.error || `HTTP ${r.status}`);
    return d;
  }
  const follow = (id, into, text, onDone) => {
    if (window.VMActions && VMActions.follow) VMActions.follow(id, into, text, onDone);
    else if (into) into.textContent = tr('bk.started', { id });
  };

  // -- modèles de VM -------------------------------------------------------------
  async function loadTemplates(w) {
    const list = w.root.querySelector('[data-tpl="list"]');
    try {
      const d = await call('GET', `/api/templates/${enc(w.cluster)}`);
      w.items = d.items || [];
      list.innerHTML = w.items.length ? w.items.map(t => `<div class="card tpl-card" data-tpl-ref="${esc(t.namespace)}/${esc(t.name)}">
          <div class="tpl-head">${icon('doc')}
            <span class="tpl-title"><strong>${esc(t.name)}</strong> <span class="res-dim">${esc(t.namespace)}</span>
              ${t.description ? `<span class="res-desc">${esc(t.description)}</span>` : ''}</span>
            <span class="tpl-acts">
              <button type="button" class="btn btn-sm btn-secondary tip" data-tpl-act="yaml-template" data-tip="${esc(tr('tpl.tip.yaml'))}">${icon('code')} YAML</button>
              <button type="button" class="btn btn-sm btn-danger tip" data-tpl-act="delete" data-tip="${esc(tr('tpl.tip.delete'))}">${icon('trash')}</button>
            </span></div>
          <table class="data-table tpl-versions"><thead><tr><th>${esc(tr('tpl.col.version'))}</th><th>${esc(tr('tpl.col.description'))}</th>
            <th>${esc(tr('tpl.col.state'))}</th><th></th></tr></thead><tbody>
          ${(t.versions || []).map(v => `<tr data-ver="${esc(v.ref)}">
            <td><strong>${esc(v.version ?? '?')}</strong>${v.default ? ` <span class="badge ok tip" data-tip="${esc(tr('tpl.tip.default'))}">${esc(tr('tpl.default'))}</span>` : ''}</td>
            <td>${esc(v.description)}</td>
            <td>${v.ready ? `<span class="badge ok">${esc(tr('tpl.ready'))}</span>` : `<span class="badge warn tip" data-tip="${esc(tr('tpl.tip.notReady'))}">${esc(tr('tpl.notReady'))}</span>`}</td>
            <td class="tpl-acts">
              <button type="button" class="btn btn-sm btn-primary tip" data-tpl-act="launch" ${v.ready ? '' : 'disabled'} data-tip="${esc(v.ready ? tr('tpl.tip.launch') : tr('tpl.tip.notReady'))}">${icon('play')} ${esc(tr('tpl.launch'))}</button>
              ${v.default ? '' : `<button type="button" class="btn btn-sm btn-secondary tip" data-tpl-act="set-default" data-tip="${esc(tr('tpl.tip.setDefault'))}">${icon('star')}</button>
              <button type="button" class="btn btn-sm btn-danger tip" data-tpl-act="delete-version" data-tip="${esc(tr('tpl.tip.deleteVersion'))}">${icon('trash')}</button>`}
              <button type="button" class="btn btn-sm btn-secondary tip" data-tpl-act="yaml-version" data-tip="${esc(tr('tpl.tip.yaml'))}">${icon('code')}</button>
            </td></tr>`).join('')}</tbody></table></div>`).join('')
        : `<p class="hint">${esc(tr('tpl.none'))}</p>`;
    } catch (e) {
      list.innerHTML = `<p class="res-error">${esc(e.message)}</p>`;
    }
  }

  async function tplAction(w, act, card, row) {
    const ref = card.dataset.tplRef;
    const [ns, name] = ref.split('/');
    const vref = row ? row.dataset.ver : null;
    const last = w.root.querySelector('[data-tpl="last"]');
    const run = async (action, body, done) => {
      const out = await call('POST', `/api/templates/${enc(w.cluster)}/${enc(ns)}/${enc(name)}/do/${action}`, body || {});
      follow(out.action_id, last, done, (ok) => { if (ok) loadTemplates(w); });
    };
    try {
      if (act === 'launch' && window.VMCreate) {
        VMCreate.open(w.cluster, ns, { template: ref, version: vref });
      } else if (act === 'set-default') {
        await run('set-default', { version: vref }, tr('tpl.done.default', { name }));
      } else if (act === 'delete-version') {
        if (!confirm(tr('tpl.confirm.deleteVersion', { version: vref }))) return;
        await run('delete-version', { version: vref }, tr('tpl.done.deleteVersion'));
      } else if (act === 'delete') {
        if (!confirm(tr('tpl.confirm.delete', { name: ref }))) return;
        await run('delete', {}, tr('tpl.done.delete', { name }));
      } else if (act === 'yaml-template' && window.YamlWindow) {
        YamlWindow.open(w.cluster, 'template', ns, name, { onDone: () => loadTemplates(w) });
      } else if (act === 'yaml-version' && window.YamlWindow) {
        const [vns, vname] = vref.split('/');
        YamlWindow.open(w.cluster, 'templateversion', vns, vname, { onDone: () => loadTemplates(w) });
      }
    } catch (e) {
      last.innerHTML = `<span class="res-error">${esc(e.message)}</span>`;
    }
  }

  function open(cluster) {
    if (!cluster) return;
    const id = `templates-${cluster}`;
    const panel = FloatingPanels.open({
      id, icon: 'doc', width: 900, height: 580, title: tr('tpl.title', { cluster }),
      restoreSpec: { type: 'templates', args: { cluster } },
      bodyHtml: `<div class="tpl-win"><p class="form-hint">${esc(tr('tpl.hint'))}</p>
        <div class="hs-last" data-tpl="last" role="status"></div>
        <div data-tpl="list"><p class="hint">${esc(tr('hs.loading'))}</p></div></div>`,
    });
    const root = panel.el;
    const w = { cluster, root, items: [] };
    if (!root.dataset.tplReady) {
      root.dataset.tplReady = '1';
      root.addEventListener('click', (e) => {
        const b = e.target.closest('[data-tpl-act]');
        if (!b || b.disabled) return;
        tplAction(w, b.dataset.tplAct, b.closest('[data-tpl-ref]'), b.closest('[data-ver]'));
      });
    }
    loadTemplates(w);
  }

  // -- modèles cloud-init ------------------------------------------------------------
  async function loadCloud(w) {
    const list = w.root.querySelector('[data-ct="list"]');
    try {
      const d = await call('GET', `/api/cloud-templates/${enc(w.cluster)}`);
      w.items = d.items || [];
      list.innerHTML = w.items.length ? `<table class="data-table"><thead><tr><th>${esc(tr('ct.col.name'))}</th>
          <th>${esc(tr('ct.col.type'))}</th><th>${esc(tr('ct.col.description'))}</th><th></th></tr></thead><tbody>
        ${w.items.map(t => `<tr data-ct-ref="${esc(t.namespace)}/${esc(t.name)}">
          <td><strong>${esc(t.name)}</strong> <span class="res-dim">${esc(t.namespace)}</span></td>
          <td><span class="badge">${esc(t.type === 'user' ? tr('ct.user') : tr('ct.network'))}</span></td>
          <td>${esc(t.description)}</td>
          <td class="tpl-acts">
            <button type="button" class="btn btn-sm btn-secondary tip" data-ct-act="edit" data-tip="${esc(tr('ct.tip.edit'))}">${icon('edit')}</button>
            <button type="button" class="btn btn-sm btn-secondary tip" data-ct-act="yaml" data-tip="${esc(tr('tpl.tip.yaml'))}">${icon('code')}</button>
            <button type="button" class="btn btn-sm btn-danger tip" data-ct-act="delete" data-tip="${esc(tr('ct.tip.delete'))}">${icon('trash')}</button>
          </td></tr>`).join('')}</tbody></table>` : `<p class="hint">${esc(tr('ct.none'))}</p>`;
    } catch (e) {
      list.innerHTML = `<p class="res-error">${esc(e.message)}</p>`;
    }
  }

  function cloudForm(w, row) {
    const host = w.root.querySelector('[data-ct="form"]');
    const edit = !!row;
    host.hidden = false;
    host.innerHTML = `<form class="of-form" autocomplete="off">
      <div class="hs-grid">
        <label class="bk-field"><span>${esc(tr('ct.col.name'))}</span><input name="name" required value="${esc(edit ? row.name : '')}" ${edit ? 'disabled' : ''}
          pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" class="tip" data-tip="${esc(tr('ct.tip.name'))}"></label>
        <label class="bk-field"><span>${esc(tr('vms.namespace'))}</span><input name="namespace" value="${esc(edit ? row.namespace : 'default')}" ${edit ? 'disabled' : ''}
          class="tip" data-tip="${esc(tr('ct.tip.namespace'))}"></label>
        <label class="bk-field"><span>${esc(tr('ct.col.type'))}</span><select name="type" ${edit ? 'disabled' : ''} class="tip" data-tip="${esc(tr('ct.tip.type'))}">
          <option value="user" ${edit && row.type === 'user' ? 'selected' : ''}>${esc(tr('ct.user'))}</option>
          <option value="network" ${edit && row.type === 'network' ? 'selected' : ''}>${esc(tr('ct.network'))}</option></select></label>
        <label class="bk-field"><span>${esc(tr('ct.col.description'))}</span><input name="description" value="${esc(edit ? row.description : '')}"
          class="tip" data-tip="${esc(tr('ct.tip.description'))}"></label>
      </div>
      <label class="bk-field"><span>${esc(tr('ct.text'))}</span><textarea name="text" rows="12" class="yaml-editor tip" data-tip="${esc(tr('ct.tip.text'))}"
        spellcheck="false">${esc(edit ? row.text : '#cloud-config\n')}</textarea></label>
      <div class="bk-form-actions">
        <button type="button" class="btn btn-sm btn-secondary" data-ct="cancel">${esc(tr('nsw.cancel'))}</button>
        <button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('nsw.tip.save'))}">${icon('save')} ${esc(edit ? tr('nsw.save') : tr('nsw.create'))}</button>
      </div><div class="of-msg" role="status"></div></form>`;
    const f = host.querySelector('form');
    f.querySelector('[data-ct="cancel"]').addEventListener('click', () => { host.hidden = true; host.innerHTML = ''; });
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = f.querySelector('.of-msg');
      try {
        const body = { text: f.text.value, description: f.description.value };
        let out;
        if (edit) {
          out = await call('POST', `/api/cloud-templates/${enc(w.cluster)}/${enc(row.namespace)}/${enc(row.name)}/do/update`, body);
        } else {
          out = await call('POST', `/api/cloud-templates/${enc(w.cluster)}`, { ...body, name: f.name.value.trim(), namespace: f.namespace.value.trim(), type: f.type.value });
        }
        follow(out.action_id, w.root.querySelector('[data-ct="last"]'), tr('ct.done.saved'), (ok) => { if (ok) loadCloud(w); });
        host.hidden = true;
        host.innerHTML = '';
      } catch (err) {
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      }
    });
  }

  function openCloud(cluster) {
    if (!cluster) return;
    const id = `cloudtpl-${cluster}`;
    const panel = FloatingPanels.open({
      id, icon: 'cloud', width: 860, height: 600, title: tr('ct.title', { cluster }),
      restoreSpec: { type: 'cloud-templates', args: { cluster } },
      bodyHtml: `<div class="tpl-win"><div class="bk-bar"><p class="form-hint">${esc(tr('ct.hint'))}</p>
          <button type="button" class="btn btn-sm btn-primary tip" data-ct="new" data-tip="${esc(tr('ct.tip.new'))}">${icon('add')} ${esc(tr('ct.new'))}</button></div>
        <div class="hs-last" data-ct="last" role="status"></div>
        <div data-ct="form" hidden></div>
        <div data-ct="list"><p class="hint">${esc(tr('hs.loading'))}</p></div></div>`,
    });
    const root = panel.el;
    const w = { cluster, root, items: [] };
    if (!root.dataset.ctReady) {
      root.dataset.ctReady = '1';
      root.querySelector('[data-ct="new"]').addEventListener('click', () => cloudForm(w, null));
      root.addEventListener('click', async (e) => {
        const b = e.target.closest('[data-ct-act]');
        if (!b) return;
        const [ns, name] = b.closest('[data-ct-ref]').dataset.ctRef.split('/');
        const row = w.items.find(t => t.namespace === ns && t.name === name);
        if (b.dataset.ctAct === 'edit') cloudForm(w, row);
        else if (b.dataset.ctAct === 'yaml' && window.YamlWindow) YamlWindow.open(cluster, 'cloudtemplate', ns, name, { onDone: () => loadCloud(w) });
        else if (b.dataset.ctAct === 'delete') {
          if (!confirm(tr('ct.confirm.delete', { name: `${ns}/${name}` }))) return;
          try {
            const out = await call('POST', `/api/cloud-templates/${enc(cluster)}/${enc(ns)}/${enc(name)}/do/delete`, {});
            follow(out.action_id, root.querySelector('[data-ct="last"]'), tr('ct.done.deleted'), (ok) => { if (ok) loadCloud(w); });
          } catch (err) {
            root.querySelector('[data-ct="last"]').innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
          }
        }
      });
    }
    loadCloud(w);
  }

  if (window.FloatingPanels && FloatingPanels.registerType) {
    FloatingPanels.registerType('templates', (a) => open(a.cluster));
    FloatingPanels.registerType('cloud-templates', (a) => openCloud(a.cluster));
  }
  return { open, openCloud };
})();
window.Templates = Templates;
