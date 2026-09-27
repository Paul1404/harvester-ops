/**
 * harvester-ops — la fenêtre Backups (v1.58.0)
 *
 * Le menu « Backup and Snapshots » de Harvester, dans une fenêtre qu'on
 * garde ouverte à côté des VMs : VM Schedules, VM Backups, VM Snapshots,
 * Volume Snapshots. Ouverte par le bouton « Backups » à droite du sélecteur
 * d'espace de noms, sur l'espace choisi (ou tous). Chaque liste se filtre et
 * se trie ; chaque geste (sauvegarder, restaurer, planifier, suspendre,
 * supprimer) est une action suivie dans le dock.
 */
const Backups = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 14) => (window.Icons ? Icons.svg(n, { size }) : '');
  const TABS = ['schedules', 'vmbackups', 'vmsnapshots', 'volsnaps'];
  // Clés en toutes lettres : le contrôle de parité ne lit que des littéraux.
  const TAB_LABEL = {
    schedules: () => tr('bk.tab.schedules'), vmbackups: () => tr('bk.tab.backups'),
    vmsnapshots: () => tr('bk.tab.snapshots'), volsnaps: () => tr('bk.tab.volsnaps'),
  };
  const TAB_TIP = {
    schedules: () => tr('bk.tab.schedulesTip'), vmbackups: () => tr('bk.tab.backupsTip'),
    vmsnapshots: () => tr('bk.tab.snapshotsTip'), volsnaps: () => tr('bk.tab.volsnapsTip'),
  };
  const TAB_ICON = { schedules: 'timer', vmbackups: 'download', vmsnapshots: 'snapshot', volsnaps: 'volume' };
  const NEW_LABEL = {
    schedules: () => tr('bk.new.schedule'), vmbackups: () => tr('bk.new.backup'),
    vmsnapshots: () => tr('bk.new.snapshot'),
  };
  const NEW_TIP = {
    schedules: () => tr('bk.new.scheduleTip'), vmbackups: () => tr('bk.new.backupTip'),
    vmsnapshots: () => tr('bk.new.snapshotTip'),
  };
  const WINS = new Map();          // id -> état de la fenêtre

  // -- appels -------------------------------------------------------------------
  async function call(method, url, body) {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' },
                                 body: body ? JSON.stringify(body) : undefined });
    let d = {};
    try { d = await r.json(); } catch { /* sans corps */ }
    if (!r.ok) throw new Error(d.hint || d.error || `HTTP ${r.status}`);
    return d;
  }

  function follow(w, actionId, doneText) {
    if (window.Dock && Dock.poll) Dock.poll();
    say(w, esc(tr('bk.started', { id: actionId })));
    if (!window.SSEReconnect) return;
    let last = '';
    const es = SSEReconnect.connect(`/api/stream/${enc(actionId)}`, {
      on: {
        step: (e) => {
          try { const s = JSON.parse(e.data); if (s.message) { last = s.message; say(w, esc(s.message)); } } catch { /* ligne illisible */ }
        },
        end: (e) => {
          let d = {};
          try { d = JSON.parse(e.data); } catch { /* fin sans détail */ }
          es.close();
          say(w, d.status === 'done' ? `${icon('ok')} ${esc(doneText)}`
            : `<span class="res-error">${icon('fail')} ${esc(tr('res.error', { msg: d.error_summary || last || d.status || '?' }))}</span>`);
          if (w.list) w.list.refresh();
        },
      },
    });
  }

  function say(w, html) {
    const el = w.root.querySelector('[data-bk="feedback"]');
    if (el) el.innerHTML = html;
  }

  // -- formulaires dans la fenêtre ---------------------------------------------
  function closeForm(w) {
    const f = w.root.querySelector('[data-bk="form"]');
    f.innerHTML = '';
    f.hidden = true;
  }

  async function vmOptions(w, ns) {
    try {
      const d = await fetch(`/api/vms/${enc(w.cluster)}`).then(r => r.json());
      const list = (Array.isArray(d) ? d : (d.vms || [])).filter(v => !ns || v.namespace === ns);
      return list.map(v => ({ ns: v.namespace, name: v.name }));
    } catch { return []; }
  }

  function showForm(w, html, onSubmit) {
    const f = w.root.querySelector('[data-bk="form"]');
    f.innerHTML = `<form class="bk-form card">${html}
      <div class="bk-form-actions">
        <button type="button" class="btn btn-sm btn-secondary tip" data-bk="cancel" data-tip="${esc(tr('bk.cancelTip'))}">${esc(tr('bk.cancel'))}</button>
        <button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('bk.submitTip'))}">${esc(tr('bk.submit'))}</button>
      </div><div class="bk-form-msg"></div></form>`;
    f.hidden = false;
    const form = f.querySelector('form');
    form.querySelector('[data-bk="cancel"]').addEventListener('click', () => closeForm(w));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = form.querySelector('.bk-form-msg');
      msg.textContent = '';
      try {
        await onSubmit(new FormData(form), form);
        closeForm(w);
      } catch (err) {
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      }
    });
    form.querySelector('input, select')?.focus();
  }

  const FREQ = {
    hourly: () => tr('bk.freq.hourly'), daily: () => tr('bk.freq.daily'),
    weekly: () => tr('bk.freq.weekly'), custom: () => tr('bk.freq.custom'),
  };

  function cronOf(f) {
    const [hh, mm] = String(f.get('time') || '02:00').split(':').map(x => parseInt(x, 10) || 0);
    switch (f.get('freq')) {
      case 'hourly': return `${mm} * * * *`;
      case 'daily': return `${mm} ${hh} * * *`;
      case 'weekly': return `${mm} ${hh} * * ${f.get('weekday') || 0}`;
      default: return String(f.get('cron') || '').trim();
    }
  }

  async function newBackup(w, kind) {
    const vms = await vmOptions(w, w.namespace);
    const isSnap = kind === 'vmsnapshots';
    const opts = vms.map(v => `<option value="${esc(v.ns)}/${esc(v.name)}">${esc(v.ns)}/${esc(v.name)}</option>`).join('');
    showForm(w, `
      <h5>${esc(isSnap ? tr('bk.new.snapshot') : tr('bk.new.backup'))}</h5>
      <p class="form-hint">${esc(isSnap ? tr('bk.snapshotHint') : tr('bk.backupHint'))}</p>
      <label class="bk-field"><span>${esc(tr('bk.f.vm'))}</span>
        <select name="vm" required class="tip" data-tip="${esc(tr('bk.f.vmTip'))}">${opts}</select></label>
      <label class="bk-field"><span>${esc(tr('bk.f.name'))}</span>
        <input name="name" placeholder="${esc(tr('bk.f.namePh'))}" class="tip" data-tip="${esc(tr('bk.f.nameTip'))}"></label>
      <label class="bk-field"><span>${esc(tr('bk.f.freeze'))}</span>
        <select name="freeze" class="tip" data-tip="${esc(tr('bk.f.freezeTip'))}">
          <option value="">${esc(tr('bk.f.freezeDefault'))}</option>
          ${['5s', '10s', '30s', '1m', '3m', '5m'].map(v => `<option value="${v}">${v}</option>`).join('')}</select></label>`,
    async (f) => {
      const [ns, vm] = String(f.get('vm')).split('/');
      const out = await call('POST', `/api/backups/${enc(w.cluster)}/${enc(ns)}`,
        { vm, type: isSnap ? 'snapshot' : 'backup', name: f.get('name') || undefined, freeze: f.get('freeze') || undefined });
      follow(w, out.action_id, isSnap ? tr('bk.done.snapshot', { vm }) : tr('bk.done.backup', { vm }));
    });
  }

  async function newSchedule(w, pick) {
    const vms = await vmOptions(w, w.namespace);
    const opts = vms.map(v => `<option value="${esc(v.ns)}/${esc(v.name)}">${esc(v.ns)}/${esc(v.name)}</option>`).join('');
    const days = [0, 1, 2, 3, 4, 5, 6].map(d => `<option value="${d}">${esc(new Date(2026, 8, 27 + d).toLocaleDateString(undefined, { weekday: 'long' }))}</option>`).join('');
    showForm(w, `
      <h5>${esc(tr('bk.new.schedule'))}</h5>
      <p class="form-hint">${esc(tr('bk.scheduleHint'))}</p>
      <div class="bk-grid">
        <label class="bk-field"><span>${esc(tr('bk.f.vm'))}</span>
          <select name="vm" required class="tip" data-tip="${esc(tr('bk.f.vmTip'))}">${opts}</select></label>
        <label class="bk-field"><span>${esc(tr('bk.f.type'))}</span>
          <select name="type" class="tip" data-tip="${esc(tr('bk.f.typeTip'))}">
            <option value="backup">${esc(tr('bk.f.typeBackup'))}</option>
            <option value="snapshot">${esc(tr('bk.f.typeSnapshot'))}</option></select></label>
        <label class="bk-field"><span>${esc(tr('bk.f.name'))}</span>
          <input name="name" required pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" class="tip" data-tip="${esc(tr('bk.f.scheduleNameTip'))}"></label>
        <label class="bk-field"><span>${esc(tr('bk.f.freq'))}</span>
          <select name="freq" class="tip" data-tip="${esc(tr('bk.f.freqTip'))}">
            ${['hourly', 'daily', 'weekly', 'custom'].map(k => `<option value="${k}" ${k === 'daily' ? 'selected' : ''}>${esc(FREQ[k]())}</option>`).join('')}
          </select></label>
        <label class="bk-field" data-when="daily weekly hourly"><span>${esc(tr('bk.f.time'))}</span>
          <input type="time" name="time" value="02:00" class="tip" data-tip="${esc(tr('bk.f.timeTip'))}"></label>
        <label class="bk-field" data-when="weekly" hidden><span>${esc(tr('bk.f.weekday'))}</span>
          <select name="weekday">${days}</select></label>
        <label class="bk-field" data-when="custom" hidden><span>${esc(tr('bk.f.cron'))}</span>
          <input name="cron" placeholder="0 2 * * *" class="tip" data-tip="${esc(tr('bk.f.cronTip'))}"></label>
        <label class="bk-field"><span>${esc(tr('bk.f.retain'))}</span>
          <input type="number" name="retain" min="3" max="250" value="7" class="tip" data-tip="${esc(tr('bk.f.retainTip'))}"></label>
        <label class="bk-field"><span>${esc(tr('bk.f.maxFailure'))}</span>
          <input type="number" name="max_failure" min="2" max="249" value="3" class="tip" data-tip="${esc(tr('bk.f.maxFailureTip'))}"></label>
      </div>`,
    async (f) => {
      const [ns, vm] = String(f.get('vm')).split('/');
      const out = await call('POST', `/api/schedules/${enc(w.cluster)}/${enc(ns)}`, {
        name: f.get('name'), vm, type: f.get('type'), cron: cronOf(f),
        retain: Number(f.get('retain')), max_failure: Number(f.get('max_failure')) });
      follow(w, out.action_id, tr('bk.done.schedule', { name: f.get('name') }));
    });
    const form = w.root.querySelector('.bk-form');
    const sync = () => {
      const v = form.querySelector('[name="freq"]').value;
      form.querySelectorAll('[data-when]').forEach(el => { el.hidden = !el.dataset.when.split(' ').includes(v); });
    };
    form.querySelector('[name="freq"]').addEventListener('change', sync);
    form.querySelector('[name="vm"]').addEventListener('change', (e) => {
      const n = form.querySelector('[name="name"]');
      if (!n.dataset.touched) n.value = `${e.target.value.split('/')[1]}-${form.querySelector('[name="type"]').value}`.slice(0, 63);
    });
    form.querySelector('[name="name"]').addEventListener('input', (e) => { e.target.dataset.touched = '1'; });
    // v1.61.0 : « Create Schedule » depuis le menu d'une VM
    if (pick && [...form.querySelector('[name="vm"]').options].some(o => o.value === pick)) form.querySelector('[name="vm"]').value = pick;
    form.querySelector('[name="vm"]').dispatchEvent(new Event('change'));
    sync();
  }

  // v1.68.0 : modifier une planification (fréquence, copies, échecs), comme
  // Harvester le permet après création ; la VM et le type restent figés
  function editSchedule(w, row) {
    const parts = String(row.cron || '').split(/\s+/);
    const simple = parts.length === 5 && /^\d+$/.test(parts[0]) && parts[2] === '*' && parts[3] === '*';
    const freq = !simple ? 'custom' : parts[1] === '*' ? 'hourly' : parts[4] === '*' ? 'daily' : /^\d$/.test(parts[4]) ? 'weekly' : 'custom';
    const pad = (n) => String(n).padStart(2, '0');
    const time = simple && parts[1] !== '*' ? `${pad(parts[1])}:${pad(parts[0])}` : `02:${pad(simple ? parts[0] : 0)}`;
    const days = [0, 1, 2, 3, 4, 5, 6].map(d => `<option value="${d}" ${String(d) === parts[4] ? 'selected' : ''}>${esc(new Date(2026, 8, 27 + d).toLocaleDateString(undefined, { weekday: 'long' }))}</option>`).join('');
    showForm(w, `
      <h5>${esc(tr('bk.edit.title', { name: row.name }))}</h5>
      <p class="form-hint">${esc(tr('bk.edit.hint', { vm: row.vm }))}</p>
      <div class="bk-grid">
        <label class="bk-field"><span>${esc(tr('bk.f.freq'))}</span>
          <select name="freq" class="tip" data-tip="${esc(tr('bk.f.freqTip'))}">
            ${['hourly', 'daily', 'weekly', 'custom'].map(k => `<option value="${k}" ${k === freq ? 'selected' : ''}>${esc(FREQ[k]())}</option>`).join('')}
          </select></label>
        <label class="bk-field" data-when="daily weekly hourly"><span>${esc(tr('bk.f.time'))}</span>
          <input type="time" name="time" value="${esc(time)}" class="tip" data-tip="${esc(tr('bk.f.timeTip'))}"></label>
        <label class="bk-field" data-when="weekly" hidden><span>${esc(tr('bk.f.weekday'))}</span>
          <select name="weekday">${days}</select></label>
        <label class="bk-field" data-when="custom" hidden><span>${esc(tr('bk.f.cron'))}</span>
          <input name="cron" value="${esc(row.cron || '')}" class="tip" data-tip="${esc(tr('bk.f.cronTip'))}"></label>
        <label class="bk-field"><span>${esc(tr('bk.f.retain'))}</span>
          <input type="number" name="retain" min="3" max="250" value="${esc(row.retain)}" class="tip" data-tip="${esc(tr('bk.f.retainTip'))}"></label>
        <label class="bk-field"><span>${esc(tr('bk.f.maxFailure'))}</span>
          <input type="number" name="max_failure" min="2" max="249" value="${esc(row.max_failure)}" class="tip" data-tip="${esc(tr('bk.f.maxFailureTip'))}"></label>
      </div>`,
    async (f) => {
      const out = await call('POST', `/api/schedules/${enc(w.cluster)}/${enc(row.namespace)}/${enc(row.name)}/update`, {
        cron: cronOf(f), retain: Number(f.get('retain')), max_failure: Number(f.get('max_failure')) });
      follow(w, out.action_id, tr('bk.done.update', { name: row.name }));
    });
    const form = w.root.querySelector('.bk-form');
    const sync = () => {
      const v = form.querySelector('[name="freq"]').value;
      form.querySelectorAll('[data-when]').forEach(el => { el.hidden = !el.dataset.when.split(' ').includes(v); });
    };
    form.querySelector('[name="freq"]').addEventListener('change', sync);
    sync();
  }

  function restoreForm(w, row) {
    showForm(w, `
      <h5>${esc(tr('bk.restore.title', { name: row.name }))}</h5>
      <label class="bk-radio"><input type="radio" name="mode" value="new" checked>
        <span>${esc(tr('bk.restore.new'))}</span></label>
      <div class="bk-grid" data-when="new">
        <label class="bk-field"><span>${esc(tr('bk.restore.newName'))}</span>
          <input name="new_vm" value="${esc((row.vm || 'vm') + '-restore')}" class="tip" data-tip="${esc(tr('bk.restore.newNameTip'))}"></label>
        <label class="bk-check tip" data-tip="${esc(tr('bk.restore.keepMacTip'))}"><input type="checkbox" name="keep_mac"> <span>${esc(tr('bk.restore.keepMac'))}</span></label>
      </div>
      <label class="bk-radio"><input type="radio" name="mode" value="replace">
        <span>${esc(tr('bk.restore.replace', { vm: row.vm }))}</span></label>
      <p class="form-hint" data-when="replace" hidden>${esc(tr('bk.restore.replaceHint'))}</p>
      ${w.tab === 'vmbackups' ? `<label class="bk-field" data-when="replace" hidden><span>${esc(tr('bk.restore.previous'))}</span>
        <select name="delete_policy" class="tip" data-tip="${esc(tr('bk.restore.previousTip'))}">
          <option value="retain">${esc(tr('bk.restore.retain'))}</option>
          <option value="delete">${esc(tr('bk.restore.delete'))}</option></select></label>` : ''}
      <label class="bk-check tip" data-tip="${esc(tr('bk.restore.haltTip'))}"><input type="checkbox" name="halt"> <span>${esc(tr('bk.restore.halt'))}</span></label>`,
    async (f) => {
      const replace = f.get('mode') === 'replace';
      const drop = replace && f.get('delete_policy') === 'delete';
      if (replace && !confirm(drop ? tr('bk.restore.replaceDeleteConfirm', { vm: row.vm })
        : tr('bk.restore.replaceConfirm', { vm: row.vm }))) throw new Error(tr('bk.cancelled'));
      const out = await call('POST', `/api/backups/${enc(w.cluster)}/${enc(row.namespace)}/${enc(row.name)}/restore`, {
        replace, new_vm: replace ? undefined : f.get('new_vm'), keep_mac: !!f.get('keep_mac'), halt: !!f.get('halt'),
        delete_policy: replace ? (f.get('delete_policy') || 'retain') : undefined });
      follow(w, out.action_id, tr('bk.done.restore', { vm: replace ? row.vm : f.get('new_vm') }));
    });
    const form = w.root.querySelector('.bk-form');
    form.addEventListener('change', () => {
      const mode = form.querySelector('[name="mode"]:checked').value;
      form.querySelectorAll('[data-when]').forEach(el => { el.hidden = el.dataset.when !== mode; });
    });
  }

  function volRestoreForm(w, row) {
    showForm(w, `
      <h5>${esc(tr('bk.volrestore.title', { name: row.name }))}</h5>
      <p class="form-hint">${esc(tr('bk.volrestore.hint', { pvc: row.pvc || '?', size: row.size || '?' }))}</p>
      <label class="bk-field"><span>${esc(tr('bk.volrestore.name'))}</span>
        <input name="new_volume" required value="${esc(((row.pvc || 'volume') + '-restore').slice(0, 63))}"
               class="tip" data-tip="${esc(tr('bk.volrestore.nameTip'))}"></label>`,
    async (f) => {
      const out = await call('POST', `/api/volsnaps/${enc(w.cluster)}/${enc(row.namespace)}/${enc(row.name)}/restore`,
        { new_volume: f.get('new_volume') });
      follow(w, out.action_id, tr('bk.done.volrestore', { name: f.get('new_volume') }));
    });
  }

  // -- gestes sur une ligne -------------------------------------------------------
  async function onAction(w, act, row) {
    try {
      if (act === 'restore') return w.tab === 'volsnaps' ? volRestoreForm(w, row) : restoreForm(w, row);
      if (act === 'sched-edit') return editSchedule(w, row);
      if (act === 'delete') {
        const msg = w.tab === 'schedules' ? tr('bk.confirm.deleteSchedule', { name: row.name })
          : w.tab === 'volsnaps' ? tr('bk.confirm.deleteVolsnap', { name: row.name })
            : tr('bk.confirm.delete', { name: row.name });
        if (!confirm(msg)) return;
        const base = w.tab === 'schedules' ? 'schedules' : w.tab === 'volsnaps' ? 'volsnaps' : 'backups';
        const out = await call('DELETE', `/api/${base}/${enc(w.cluster)}/${enc(row.namespace)}/${enc(row.name)}`);
        follow(w, out.action_id, tr('bk.done.delete', { name: row.name }));
      }
      if (act === 'suspend' || act === 'resume') {
        const out = await call('POST', `/api/schedules/${enc(w.cluster)}/${enc(row.namespace)}/${enc(row.name)}/${act}`);
        follow(w, out.action_id, act === 'suspend' ? tr('bk.done.suspend', { name: row.name }) : tr('bk.done.resume', { name: row.name }));
      }
    } catch (err) {
      say(w, `<span class="res-error">${esc(err.message)}</span>`);
    }
  }

  // -- la fenêtre -------------------------------------------------------------------
  async function paintTarget(w) {
    const el = w.root.querySelector('[data-bk="target"]');
    try {
      const t = await fetch(`/api/backup-target/${enc(w.cluster)}`).then(r => r.json());
      el.innerHTML = t.set
        ? `${icon('cloud')} <span class="tip" data-tip="${esc(tr('bk.targetTip'))}">${esc(tr('bk.target', { type: t.type, endpoint: t.endpoint + (t.bucket ? ' / ' + t.bucket : '') }))}</span>`
        : `<span class="res-error tip" data-tip="${esc(tr('bk.noTargetTip'))}">${icon('warn')} ${esc(tr('bk.noTarget'))}</span>`;
    } catch { el.textContent = ''; }
  }

  function showTab(w, tab) {
    w.tab = tab;
    w.root.querySelectorAll('[data-bk-tab]').forEach(b => {
      const on = b.dataset.bkTab === tab;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    const add = w.root.querySelector('[data-bk="new"]');
    add.hidden = !NEW_LABEL[tab];
    if (NEW_LABEL[tab]) {
      add.querySelector('span').textContent = NEW_LABEL[tab]();
      add.dataset.tip = NEW_TIP[tab]();
    }
    closeForm(w);
    if (w.list) w.list.stop();
    w.list = ResourceViews.mount(tab, w.cluster, w.root.querySelector('[data-bk="list"]'), {
      namespace: w.namespace, onAction: (act, row) => onAction(w, act, row) });
    try { localStorage.setItem('harvester_ops_backups_tab', tab); } catch {}
  }

  async function fillNamespaces(w) {
    const sel = w.root.querySelector('[data-bk="ns"]');
    try {
      const d = await fetch(`/api/namespaces/${enc(w.cluster)}`).then(r => r.json());
      const names = (Array.isArray(d) ? d : (d.namespaces || [])).map(n => (typeof n === 'string' ? n : n.name)).filter(Boolean);
      sel.innerHTML = `<option value="">${esc(tr('bk.allNamespaces'))}</option>`
        + names.map(n => `<option value="${esc(n)}" ${n === w.namespace ? 'selected' : ''}>${esc(n)}</option>`).join('');
    } catch {
      sel.innerHTML = `<option value="${esc(w.namespace || '')}">${esc(w.namespace || tr('bk.allNamespaces'))}</option>`;
    }
  }

  function open(cluster, namespace, tab) {
    if (!cluster) return;
    let first = tab;
    if (!first) { try { first = localStorage.getItem('harvester_ops_backups_tab'); } catch {} }
    if (!TABS.includes(first)) first = 'vmbackups';
    const id = `backups-${cluster}`;
    const panel = FloatingPanels.open({
      id, icon: 'snapshot', width: 1060, height: 640,
      title: tr('bk.title', { cluster }),
      restoreSpec: { type: 'backups', args: { cluster, namespace: namespace || '', tab: first } },
      onClose: () => { const w = WINS.get(id); if (w && w.list) w.list.stop(); WINS.delete(id); },
      bodyHtml: `<div class="bk-win">
        <div class="bk-head">
          <label class="bk-ns tip" data-tip="${esc(tr('bk.nsTip'))}"><span>${esc(tr('vms.namespace'))}</span>
            <select data-bk="ns"></select></label>
          <span class="bk-target" data-bk="target"></span>
        </div>
        <div class="bk-bar">
          <div class="sub-tabs sub-tabs-inline bk-tabs" role="tablist">
            ${TABS.map(t => `<button type="button" class="sub-tab tip" role="tab" data-bk-tab="${t}"
              data-tip="${esc(TAB_TIP[t]())}">${icon(TAB_ICON[t])} <span>${esc(TAB_LABEL[t]())}</span></button>`).join('')}
          </div>
          <button type="button" class="btn btn-sm btn-primary tip" data-bk="new" data-tip="">${icon('add')} <span></span></button>
        </div>
        <div class="bk-feedback" data-bk="feedback"></div>
        <div class="bk-form-host" data-bk="form" hidden></div>
        <div class="bk-list" data-bk="list"></div>
      </div>`,
    });
    const root = panel.el;
    if (root.dataset.bkReady) {                 // déjà ouverte : elle revient devant
      const w = WINS.get(id);
      if (w && namespace !== undefined && namespace !== w.namespace) {
        w.namespace = namespace || '';
        root.querySelector('[data-bk="ns"]').value = w.namespace;
        showTab(w, w.tab);
      }
      return;
    }
    root.dataset.bkReady = '1';
    const w = { id, cluster, namespace: namespace || '', tab: first, root, list: null };
    WINS.set(id, w);
    root.querySelectorAll('[data-bk-tab]').forEach(b => b.addEventListener('click', () => showTab(w, b.dataset.bkTab)));
    root.querySelector('[data-bk="new"]').addEventListener('click', () => {
      if (w.tab === 'schedules') newSchedule(w); else newBackup(w, w.tab);
    });
    root.querySelector('[data-bk="ns"]').addEventListener('change', (e) => {
      w.namespace = e.target.value;
      showTab(w, w.tab);
    });
    fillNamespaces(w);
    paintTarget(w);
    showTab(w, first);
  }

  if (window.FloatingPanels && FloatingPanels.registerType) {
    FloatingPanels.registerType('backups', (a) => open(a.cluster, a.namespace, a.tab));
  }
  /** v1.61.0 : la fenêtre, onglet des planifications, formulaire ouvert sur une VM. */
  async function scheduleFor(cluster, namespace, vm) {
    open(cluster, namespace, 'schedules');
    const w = WINS.get(`backups-${cluster}`);
    if (!w) return;
    if (w.tab !== 'schedules') showTab(w, 'schedules');
    await newSchedule(w, `${namespace}/${vm}`);
  }

  return { open, scheduleFor };
})();
window.Backups = Backups;
