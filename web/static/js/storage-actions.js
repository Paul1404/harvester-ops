/**
 * harvester-ops : les gestes de Harvester sur un volume et sur une image (v1.63.0)
 *
 * Un menu par volume (vue Stockage) et par image (liste des images), lu sur
 * l'état réel : un geste qui ne s'applique pas est grisé et dit pourquoi.
 * Volume : cloner (avec ou sans les données), exporter en image, instantané,
 * copier vers une autre classe (migration de données de Harvester, par CDI),
 * annuler un agrandissement, description, supprimer s'il ne sert à rien.
 * Image : modifier (description, labels), cloner, chiffrer / déchiffrer,
 * télécharger, créer une VM. Et l'envoi d'un fichier depuis le navigateur.
 * Chaque geste est une action suivie, par bin/harvester-resources.py.
 */
const StorageActions = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 14) => (window.Icons ? Icons.svg(n, { size }) : '');
  let menu = null;

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

  // -- le menu --------------------------------------------------------------------
  function close() {
    if (!menu) return;
    menu.el.remove();
    document.removeEventListener('pointerdown', menu.outside, true);
    document.removeEventListener('keydown', menu.keys, true);
    if (menu.anchor) { menu.anchor.setAttribute('aria-expanded', 'false'); menu.anchor.focus(); }
    menu = null;
  }

  function place(el, anchor) {
    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth || 260, h = el.offsetHeight || 300;
    let left = r.right - w, top = r.bottom + 4;
    if (left < 8) left = 8;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
    el.style.left = `${left + window.scrollX}px`;
    el.style.top = `${top + window.scrollY}px`;
  }

  /** items : [{act, label, icon, tip, why}] ; `why` grise l'entrée et dit pourquoi. */
  async function openMenu(anchor, title, ico, load, onPick) {
    if (menu && menu.anchor === anchor) { close(); return; }
    close();
    const el = document.createElement('div');
    el.className = 'vma-menu sta-menu';
    el.setAttribute('role', 'menu');
    el.innerHTML = `<div class="vma-head">${icon(ico, 13)} <strong>${esc(title)}</strong></div>
      <div class="vma-body"><p class="form-hint">${esc(tr('common.loading'))}</p></div>`;
    document.body.appendChild(el);
    place(el, anchor);
    anchor.setAttribute('aria-expanded', 'true');
    const mine = menu = {
      el, anchor,
      outside: (e) => { if (!el.contains(e.target) && e.target !== anchor && !anchor.contains(e.target)) close(); },
      keys: (e) => {
        if (e.key === 'Escape') { e.preventDefault(); close(); return; }
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          const items = [...el.querySelectorAll('.vma-item')];
          const i = items.indexOf(document.activeElement);
          e.preventDefault();
          items[e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length]?.focus();
        }
      },
    };
    document.addEventListener('pointerdown', mine.outside, true);
    document.addEventListener('keydown', mine.keys, true);
    let ctx;
    try {
      const { items, context } = await load();
      if (menu !== mine) return;
      ctx = context;
      el.querySelector('.vma-body').innerHTML = items.map(it => `<button type="button" role="menuitem"
          class="vma-item tip${it.act === 'delete' ? ' danger' : ''}" data-sta="${esc(it.act)}"
          data-tip="${esc(it.why || it.tip)}" ${it.why ? 'aria-disabled="true"' : ''}>
          <span class="vma-ic">${icon(it.icon)}</span><span>${esc(it.label)}</span></button>`).join('');
      place(el, anchor);
      el.querySelector('.vma-item:not([aria-disabled])')?.focus();
    } catch (err) {
      if (menu !== mine) return;
      el.querySelector('.vma-body').innerHTML = `<p class="res-error">${esc(err.message)}</p>`;
    }
    el.addEventListener('click', (e) => {
      const b = e.target.closest('.vma-item');
      if (!b || b.getAttribute('aria-disabled')) return;
      close();
      onPick(b.dataset.sta, ctx);
    });
  }

  // -- une petite fenêtre de formulaire ---------------------------------------------
  const field = (name, label, input, tip) => `<label class="bk-field"><span>${esc(label)}</span>${
    input.replace(/^<(input|select|textarea)/, `<$1 name="${name}" class="tip" data-tip="${esc(tip)}"`)}</label>`;
  const check = (name, label, tip, on) => `<label class="bk-check tip" data-tip="${esc(tip)}">
    <input type="checkbox" name="${name}" ${on ? 'checked' : ''}> <span>${esc(label)}</span></label>`;
  const options = (list, sel) => list.map(o => (typeof o === 'string' ? { value: o, label: o } : o))
    .map(o => `<option value="${esc(o.value)}" ${o.value === sel ? 'selected' : ''} ${o.disabled ? 'disabled' : ''}>${esc(o.label)}</option>`).join('');

  function dialog(id, title, ico, fieldsHtml, submitLabel, onSubmit, opts = {}) {
    const panel = FloatingPanels.open({
      id: `sta-${id}`, icon: ico, width: opts.width || 480, height: opts.height || 380, title,
      bodyHtml: `<form class="of-form sta-form" autocomplete="off">${fieldsHtml}
        <div class="bk-form-actions"><button type="submit" class="btn btn-sm ${opts.danger ? 'btn-danger' : 'btn-primary'} tip"
          data-tip="${esc(tr('bk.submitTip'))}">${icon(opts.submitIcon || 'ok')} ${esc(submitLabel)}</button></div>
        <div class="of-msg" role="status"></div></form>`,
    });
    const root = panel.el;
    if (root.dataset.staReady) return root;
    root.dataset.staReady = '1';
    const form = root.querySelector('form');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = form.querySelector('button[type="submit"]');
      const msg = root.querySelector('.of-msg');
      btn.disabled = true;
      try {
        await onSubmit(form, msg);
      } catch (err) {
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      } finally {
        setTimeout(() => { btn.disabled = false; }, 1500);
      }
    });
    form.querySelector('input:not([type=hidden]):not([type=checkbox]), select')?.focus();
    return root;
  }

  // -- volumes ------------------------------------------------------------------------
  const vbase = (c, ns, n) => `/api/volume/${enc(c)}/${enc(ns)}/${enc(n)}`;

  function volumeMenu(anchor, cluster, ns, name, onDone) {
    return openMenu(anchor, `${ns}/${name}`, 'volume', async () => {
      const v = await call('GET', `${vbase(cluster, ns, name)}/info`);
      const used = (v.used_by || []).length ? tr('sta.why.usedBy', { vms: v.used_by.join(', ') }) : '';
      const items = [
        { act: 'clone', label: tr('sta.vol.clone'), icon: 'clone', tip: tr('sta.tip.volClone') },
        { act: 'export', label: tr('sta.vol.export'), icon: 'cdrom', tip: tr('sta.tip.volExport') },
        { act: 'snapshot', label: tr('sta.vol.snapshot'), icon: 'snapshot', tip: tr('sta.tip.volSnapshot'),
          why: v.snapshot_class ? '' : tr('sta.why.noSnapClass') },
        { act: 'copy', label: tr('sta.vol.copy'), icon: 'migrate', tip: tr('sta.tip.volCopy'),
          why: !v.cdi ? tr('sta.why.noCdi') : used },
        ...(v.resizing ? [{ act: 'cancel-expand', label: tr('sta.vol.cancelExpand'), icon: 'undo',
                            tip: tr('sta.tip.volCancelExpand'), why: used }] : []),
        { act: 'describe', label: tr('sta.vol.describe'), icon: 'edit', tip: tr('sta.tip.volDescribe') },
        { act: 'delete', label: tr('sta.vol.delete'), icon: 'trash', tip: tr('sta.tip.volDelete'), why: used },
      ];
      return { items, context: v };
    }, (act, v) => volumeAction(act, cluster, v, onDone));
  }

  async function volDo(cluster, v, action, body, msg, doneText, onDone) {
    const out = await call('POST', `${vbase(cluster, v.namespace, v.name)}/do/${action}`, body);
    follow(out.action_id, msg, doneText, (ok) => { if (ok && onDone) onDone(action); });
  }

  function volumeAction(act, cluster, v, onDone) {
    const id = `${act}-${cluster}-${v.namespace}-${v.name}`;
    const ref = `${v.namespace}/${v.name}`;
    const scs = (v.storage_classes || []).filter(c => !c.internal && !c.image);
    if (act === 'clone') {
      dialog(id, tr('sta.vol.clone') + ` · ${ref}`, 'clone',
        field('new_name', tr('sta.f.newVolume'), `<input type="text" required value="${esc(v.name)}-clone">`, tr('sta.tip.newVolume'))
        + check('with_data', tr('sta.f.withData'), tr('sta.tip.withData'), true),
        tr('sta.vol.clone'), (f, msg) => volDo(cluster, v, 'clone',
          { new_name: f.new_name.value.trim(), with_data: f.with_data.checked }, msg, tr('sta.done.clone', { name: f.new_name.value.trim() }), onDone));
    } else if (act === 'export') {
      const targets = scs.map(c => ({ value: c.name, label: c.name + (c.longhorn_v1 ? '' : ' (CDI)'),
                                      disabled: c.encrypted || (!v.longhorn_v1 && c.longhorn_v1) }));
      dialog(id, tr('sta.vol.export') + ` · ${ref}`, 'cdrom',
        field('display_name', tr('sta.f.imageName'), `<input type="text" required maxlength="63" value="${esc(v.name)}-image">`, tr('sta.tip.imageName'))
        + field('storage_class', tr('sta.f.storageClass'), `<select>${options(targets, v.storage_class)}</select>`, tr('sta.tip.exportClass'))
        + `<p class="form-hint">${esc(v.longhorn_v1 ? tr('sta.hint.exportLive') : tr('sta.hint.exportStopped'))}</p>`,
        tr('sta.vol.export'), (f, msg) => volDo(cluster, v, 'export',
          { display_name: f.display_name.value.trim(), storage_class: f.storage_class.value }, msg,
          tr('sta.done.export', { name: f.display_name.value.trim() }), onDone));
    } else if (act === 'snapshot') {
      const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');
      dialog(id, tr('sta.vol.snapshot') + ` · ${ref}`, 'snapshot',
        field('snapshot_name', tr('sta.f.snapshotName'), `<input type="text" required value="${esc(v.name)}-${stamp}">`, tr('sta.tip.snapshotName')),
        tr('sta.vol.snapshot'), (f, msg) => volDo(cluster, v, 'snapshot', { snapshot_name: f.snapshot_name.value.trim() }, msg,
          tr('sta.done.snapshot', { name: f.snapshot_name.value.trim() }), onDone));
    } else if (act === 'copy') {
      dialog(id, tr('sta.vol.copy') + ` · ${ref}`, 'migrate',
        field('new_name', tr('sta.f.newVolume'), `<input type="text" required value="${esc(v.name)}-copy">`, tr('sta.tip.newVolume'))
        + field('storage_class', tr('sta.f.storageClass'), `<select>${options(scs.map(c => c.name).filter(n => n !== v.storage_class))}</select>`, tr('sta.tip.copyClass'))
        + `<p class="form-hint">${esc(tr('sta.hint.copy'))}</p>`,
        tr('sta.vol.copy'), (f, msg) => volDo(cluster, v, 'copy',
          { new_name: f.new_name.value.trim(), storage_class: f.storage_class.value }, msg,
          tr('sta.done.copy', { name: f.new_name.value.trim() }), onDone));
    } else if (act === 'cancel-expand') {
      dialog(id, tr('sta.vol.cancelExpand') + ` · ${ref}`, 'undo',
        `<p class="form-hint">${esc(tr('sta.hint.cancelExpand', { size: v.capacity || '?' }))}</p>`,
        tr('sta.vol.cancelExpand'), (f, msg) => volDo(cluster, v, 'cancel-expand', {}, msg, tr('sta.done.cancelExpand', { name: v.name }), onDone),
        { danger: true });
    } else if (act === 'describe') {
      dialog(id, tr('sta.vol.describe') + ` · ${ref}`, 'edit',
        field('description', tr('sta.f.description'), `<textarea rows="3" maxlength="1000">${esc(v.description)}</textarea>`, tr('sta.tip.description')),
        tr('hs.save'), (f, msg) => volDo(cluster, v, 'describe', { description: f.description.value }, msg, tr('sta.done.describe'), onDone),
        { submitIcon: 'save' });
    } else if (act === 'delete' && window.ObjectForms) {
      ObjectForms.remove('volume', cluster, { namespace: v.namespace, name: v.name })
        .then(() => { if (onDone) setTimeout(() => onDone('delete'), 2000); })
        .catch((e) => alert(e.message));
    }
  }

  // -- images -------------------------------------------------------------------------
  const ibase = (c, ns, n) => `/api/image/${enc(c)}/${enc(ns)}/${enc(n)}`;

  function imageMenu(anchor, cluster, row, onDone) {
    return openMenu(anchor, row.display_name, 'cdrom', async () => {
      const ready = row.state === 'ready';
      const lhv1 = (row.backend || 'backingimage') === 'backingimage';
      const items = [
        { act: 'edit', label: tr('sta.img.edit'), icon: 'edit', tip: tr('sta.tip.imgEdit') },
        { act: 'clone', label: tr('sta.img.clone'), icon: 'clone', tip: tr('sta.tip.imgClone'),
          why: row.source_type !== 'download' ? tr('sta.why.cloneDownload') : '' },
        row.encrypted
          ? { act: 'decrypt', label: tr('sta.img.decrypt'), icon: 'unlock', tip: tr('sta.tip.imgDecrypt'),
              why: !lhv1 ? tr('sta.why.lhv1') : !ready ? tr('sta.why.notReady') : '' }
          : { act: 'encrypt', label: tr('sta.img.encrypt'), icon: 'lock', tip: tr('sta.tip.imgEncrypt'),
              why: !lhv1 ? tr('sta.why.lhv1') : !ready ? tr('sta.why.notReady') : '' },
        { act: 'download', label: tr('sta.img.download'), icon: 'download', tip: tr('sta.tip.imgDownload'),
          why: !lhv1 ? tr('sta.why.lhv1') : !ready ? tr('sta.why.notReady') : '' },
        { act: 'createvm', label: tr('sta.img.createVm'), icon: 'vm', tip: tr('sta.tip.imgCreateVm'),
          why: !ready ? tr('sta.why.notReady') : '' },
      ];
      return { items, context: row };
    }, (act, r) => imageAction(act, cluster, r, onDone));
  }

  async function imgDo(cluster, r, action, body, msg, doneText, onDone) {
    const out = await call('POST', `${ibase(cluster, r.namespace, r.name)}/do/${action}`, body);
    follow(out.action_id, msg, doneText, (ok) => { if (ok && onDone) onDone(action); });
  }

  function kvRows(obj) {
    return Object.entries(obj || {}).map(([k, v]) => `<div class="vm-kv-row" data-sta-kv>
      <input type="text" data-kv="key" value="${esc(k)}" class="tip" data-tip="${esc(tr('vm.edit.tKvKey'))}">
      <input type="text" data-kv="value" value="${esc(v)}" class="tip" data-tip="${esc(tr('vm.edit.tKvValue'))}">
      <button type="button" class="btn-icon-sm tip" data-kv-del data-tip="${esc(tr('vm.edit.tKvDel'))}">×</button></div>`).join('');
  }

  async function imageAction(act, cluster, r, onDone) {
    const id = `${act}-${cluster}-${r.namespace}-${r.name}`;
    if (act === 'edit') {
      const root = dialog(id, tr('sta.img.edit') + ` · ${r.display_name}`, 'edit',
        field('description', tr('sta.f.description'), `<textarea rows="3" maxlength="1000">${esc(r.description || '')}</textarea>`, tr('sta.tip.description'))
        + `<div class="bk-field"><span>${esc(tr('sta.f.labels'))}</span><div class="vm-kv-rows" data-sta="kv">${kvRows(r.labels)}</div>
           <button type="button" class="btn btn-sm btn-secondary tip" data-sta="kv-add" data-tip="${esc(tr('vm.edit.tKvAdd'))}">${icon('add', 13)} ${esc(tr('vm.edit.kvAdd'))}</button></div>`,
        tr('hs.save'), (f, msg) => {
          const labels = {};
          f.querySelectorAll('[data-sta-kv]').forEach(row => {
            const k = row.querySelector('[data-kv="key"]').value.trim();
            if (k) labels[k] = row.querySelector('[data-kv="value"]').value;
          });
          return imgDo(cluster, r, 'edit', { description: f.description.value, labels }, msg, tr('sta.done.imgEdit'), onDone);
        }, { submitIcon: 'save', height: 440 });
      if (!root.dataset.staKv) {
        root.dataset.staKv = '1';
        root.addEventListener('click', (e) => {
          if (e.target.closest('[data-sta="kv-add"]')) root.querySelector('[data-sta="kv"]').insertAdjacentHTML('beforeend', kvRows({ '': '' }));
          if (e.target.closest('[data-kv-del]')) e.target.closest('[data-sta-kv]').remove();
        });
      }
    } else if (act === 'clone') {
      dialog(id, tr('sta.img.clone') + ` · ${r.display_name}`, 'clone',
        field('display_name', tr('sta.f.imageName'), `<input type="text" required maxlength="63" value="${esc(r.display_name)}-clone">`, tr('sta.tip.imageName'))
        + `<p class="form-hint">${esc(tr('sta.hint.clone', { url: r.url || '' }))}</p>`,
        tr('sta.img.clone'), (f, msg) => imgDo(cluster, r, 'clone', { display_name: f.display_name.value.trim() }, msg,
          tr('sta.done.imgClone', { name: f.display_name.value.trim() }), onDone));
    } else if (act === 'encrypt' || act === 'decrypt') {
      let scs = [];
      try {
        const d = await call('GET', `/api/cluster-objects/${enc(cluster)}/storageclasses`);
        scs = (d.items || []).filter(c => !c.image && (((c.parameters || {}).encrypted === 'true') === (act === 'encrypt')));
      } catch { /* liste indisponible : le champ reste vide */ }
      dialog(id, (act === 'encrypt' ? tr('sta.img.encrypt') : tr('sta.img.decrypt')) + ` · ${r.display_name}`, act === 'encrypt' ? 'lock' : 'unlock',
        field('display_name', tr('sta.f.imageName'), `<input type="text" required maxlength="63" value="${esc(r.display_name)}-${act === 'encrypt' ? 'enc' : 'dec'}">`, tr('sta.tip.imageName'))
        + field('storage_class', tr('sta.f.storageClass'), `<select required>${options(scs.map(c => c.name))}</select>`,
          act === 'encrypt' ? tr('sta.tip.encClass') : tr('sta.tip.decClass'))
        + (scs.length ? '' : `<p class="res-error">${esc(act === 'encrypt' ? tr('sta.why.noEncClass') : tr('sta.why.noClearClass'))}</p>`),
        act === 'encrypt' ? tr('sta.img.encrypt') : tr('sta.img.decrypt'),
        (f, msg) => imgDo(cluster, r, act, { display_name: f.display_name.value.trim(), storage_class: f.storage_class.value }, msg,
          tr('sta.done.imgClone', { name: f.display_name.value.trim() }), onDone));
    } else if (act === 'download') {
      const a = document.createElement('a');
      a.href = `${ibase(cluster, r.namespace, r.name)}/download`;
      a.download = '';
      document.body.appendChild(a);
      a.click();
      a.remove();
    } else if (act === 'createvm' && window.VMCreate) {
      VMCreate.open(cluster, r.namespace, { image: { ref: `${r.namespace}/${r.name}`, storage_class: r.storage_class,
        virtual_size: r.virtual_size, iso: /\.iso$/i.test(r.display_name || '') || (r.labels || {})['harvesterhci.io/image-type'] === 'iso' } });
    }
  }

  // -- envoyer un fichier ---------------------------------------------------------------
  async function uploadDialog(cluster, onDone) {
    let namespaces = ['default'], scs = [];
    try {
      const d = await call('GET', `/api/namespaces/${enc(cluster)}`);
      namespaces = (Array.isArray(d) ? d : []).map(n => n.name || n).filter(Boolean);
      const c = await call('GET', `/api/cluster-objects/${enc(cluster)}/storageclasses`);
      scs = (c.items || []).filter(x => !x.image && x.provisioner === 'driver.longhorn.io' && (x.parameters || {}).encrypted !== 'true');
    } catch { /* listes indisponibles */ }
    const def = (scs.find(x => x.is_default) || scs[0] || {}).name || '';
    dialog(`upload-${cluster}`, tr('sta.up.title', { cluster }), 'upload',
      field('file', tr('sta.f.file'), '<input type="file" required accept=".qcow2,.img,.iso,.raw,.qcow">', tr('sta.tip.file'))
      + field('display_name', tr('sta.f.imageName'), '<input type="text" required maxlength="63">', tr('sta.tip.imageName'))
      + field('namespace', tr('vms.namespace'), `<select>${options(namespaces, 'default')}</select>`, tr('sta.tip.namespace'))
      + field('storage_class', tr('sta.f.storageClass'), `<select>${options(scs.map(x => x.name), def)}</select>`, tr('sta.tip.upClass'))
      + field('checksum', tr('sta.f.checksum'), '<input type="text" maxlength="128" pattern="[0-9a-fA-F]{128}">', tr('sta.tip.checksum'))
      + `<div class="sta-progress" hidden><progress max="100" value="0"></progress> <span></span></div>
         <p class="form-hint">${esc(tr('sta.hint.upload'))}</p>`,
      tr('sta.up.send'), (f, msg) => new Promise((resolve, reject) => {
        const file = f.file.files[0];
        if (!file) { reject(new Error(tr('sta.why.noFile'))); return; }
        const q = new URLSearchParams({ display_name: f.display_name.value.trim(), file_name: file.name,
                                        storage_class: f.storage_class.value, checksum: f.checksum.value.trim() });
        const xhr = new XMLHttpRequest();
        const bar = f.querySelector('.sta-progress');
        bar.hidden = false;
        xhr.upload.onprogress = (e) => {
          if (!e.lengthComputable) return;
          const pct = Math.round((e.loaded / e.total) * 100);
          bar.querySelector('progress').value = pct;
          bar.querySelector('span').textContent = `${pct} %`;
        };
        xhr.onload = () => {
          let d = {};
          try { d = JSON.parse(xhr.responseText); } catch { /* sans corps */ }
          if (xhr.status >= 300) { reject(new Error(d.error || `HTTP ${xhr.status}`)); return; }
          follow(d.action_id, msg, tr('sta.done.upload', { name: f.display_name.value.trim() }), (ok) => { if (ok && onDone) onDone('upload'); });
          resolve();
        };
        xhr.onerror = () => reject(new Error(tr('sta.why.uploadNet')));
        xhr.open('PUT', `/api/image-upload/${enc(cluster)}/${enc(f.namespace.value)}?${q}`);
        xhr.setRequestHeader('Content-Type', 'application/octet-stream');
        xhr.send(file);
      }), { height: 520, submitIcon: 'upload' });
  }

  return { volumeMenu, imageMenu, uploadDialog, close };
})();
window.StorageActions = StorageActions;
