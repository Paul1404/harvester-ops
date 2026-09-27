/**
 * harvester-ops : la configuration d'un hôte, comme « Modifier la
 * configuration » de Harvester (v1.62.0)
 *
 * Une fenêtre par hôte, à onglets : Général (nom affiché, URL de console,
 * labels, tags), Disques (ajouter ou retirer un disque du stockage, ses tags
 * et sa planification), Huge pages, KSM, Accès hors bande (seeder) et Gestes
 * (CPU manager, suppression de l'hôte). Chaque enregistrement est une action
 * suivie, par bin/harvester-resources.py host ; la fenêtre se relit à la fin.
 * Le mot de passe du BMC ne part que dans le corps de la requête et ne
 * revient jamais.
 */
const HostSettings = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 14) => (window.Icons ? Icons.svg(n, { size }) : '');
  const TABS = ['general', 'disks', 'hugepages', 'ksm', 'oob', 'actions'];
  const TAB_ICON = { general: 'general', disks: 'disk', hugepages: 'compute', ksm: 'metrics', oob: 'power', actions: 'tools' };
  const TAB_LABEL = {
    general: () => tr('hs.tab.general'), disks: () => tr('hs.tab.disks'), hugepages: () => tr('hs.tab.hugepages'),
    ksm: () => tr('hs.tab.ksm'), oob: () => tr('hs.tab.oob'), actions: () => tr('hs.tab.actions'),
  };
  const TAB_TIP = {
    general: () => tr('hs.tip.general'), disks: () => tr('hs.tip.disks'), hugepages: () => tr('hs.tip.hugepages'),
    ksm: () => tr('hs.tip.ksm'), oob: () => tr('hs.tip.oob'), actions: () => tr('hs.tip.actions'),
  };
  const THP_ENABLED = ['always', 'madvise', 'never'];
  const THP_SHMEM = ['always', 'within_size', 'advise', 'never', 'deny', 'force'];
  const THP_DEFRAG = ['always', 'defer', 'defer+madvise', 'madvise', 'never'];
  const KSM_PARAMS = ['sleepMsec', 'boost', 'decay', 'minPages', 'maxPages'];
  const HIDDEN_LABEL = /(k3s|kubernetes|kubevirt|harvesterhci|k3os|cattle|longhorn)+\.io\/|^cpumanager$|^kube-ovn\//;
  const SHOWN = ['topology.kubernetes.io/zone', 'topology.kubernetes.io/region'];
  const WINS = new Map();
  // clés écrites en toutes lettres (contrôle de parité i18n)
  const POWER = {
    shutdown: { label: () => tr('hs.power.shutdown'), tip: () => tr('hs.tip.power.shutdown'), confirm: (p) => tr('hs.confirm.power.shutdown', p) },
    poweron: { label: () => tr('hs.power.poweron'), tip: () => tr('hs.tip.power.poweron'), confirm: (p) => tr('hs.confirm.power.poweron', p) },
    reboot: { label: () => tr('hs.power.reboot'), tip: () => tr('hs.tip.power.reboot'), confirm: (p) => tr('hs.confirm.power.reboot', p) },
  };

  function size(b) {
    const n = Number(b);
    if (!n) return '-';
    const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
  }

  async function call(method, url, body) {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' },
                                 body: body ? JSON.stringify(body) : undefined });
    let d = {};
    try { d = await r.json(); } catch { /* sans corps */ }
    if (!r.ok) throw new Error(d.hint || d.error || `HTTP ${r.status}`);
    return d;
  }

  const base = (w) => `/api/host/${enc(w.cluster)}/${enc(w.node)}`;

  /** Lance un geste et le suit dans `msg` ; la fenêtre se relit à la fin. */
  async function act(w, action, body, msg, doneText) {
    const last = w.root.querySelector('[data-hs="last"]');
    if (last) last.innerHTML = '';
    const out = await call('POST', `${base(w)}/do/${action}`, body || {});
    if (window.VMActions && VMActions.follow) {
      VMActions.follow(out.action_id, msg, doneText, (ok) => {
        // la fenêtre se redessine en se relisant : le compte rendu reste au-dessus des onglets
        if (last && msg) last.innerHTML = msg.innerHTML;
        if (ok) reload(w);
        if (w.onDone) w.onDone(action);
      });
    } else if (msg) {
      msg.textContent = tr('bk.started', { id: out.action_id });
    }
    return out.action_id;
  }

  /** Relit l'hôte. `soft` : relecture de fond (changement d'onglet, minuteur) ;
   *  l'onglet n'est redessiné que si l'hôte a changé et qu'aucun formulaire
   *  n'est en cours de saisie (vu sur harvlab : sans relecture, la fenêtre
   *  ignorait la mise en maintenance et laissait l'alimentation grisée). */
  async function reload(w, soft = false) {
    let fresh;
    try {
      fresh = await call('GET', `${base(w)}/settings`);
      w.root.querySelector('[data-hs="err"]').textContent = '';
    } catch (e) {
      w.root.querySelector('[data-hs="err"]').textContent = e.message;
      return;
    }
    const same = JSON.stringify(fresh) === JSON.stringify(w.data);
    w.data = fresh;
    const title = w.root.querySelector('[data-hs="name"]');
    title.textContent = fresh.custom_name ? `${fresh.custom_name} (${fresh.node})` : fresh.node;
    if (soft && (same || w.dirty)) return;
    showTab(w, w.tab);
  }

  function showTab(w, tab) {
    w.tab = tab;
    w.root.querySelectorAll('[data-hs-tab]').forEach(b => {
      const on = b.dataset.hsTab === tab;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    const host = w.root.querySelector('[data-hs="body"]');
    if (!w.data) { host.innerHTML = `<p class="hint">${esc(tr('hs.loading'))}</p>`; return; }
    host.innerHTML = RENDER[tab](w, w.data);
    w.dirty = false;
    WIRE[tab] && WIRE[tab](w, host, w.data);
    try { localStorage.setItem('harvester_ops_host_tab', tab); } catch { /* stockage indisponible */ }
  }

  const field = (label, input, tip) => `<label class="bk-field"><span>${esc(label)}</span>${
    input.replace(/^<(input|select|textarea)/, `<$1 class="tip" data-tip="${esc(tip)}"`)}</label>`;
  const check = (name, label, tip, on) => `<label class="bk-check tip" data-tip="${esc(tip)}">
    <input type="checkbox" name="${name}" ${on ? 'checked' : ''}> <span>${esc(label)}</span></label>`;
  const select = (name, values, cur) => `<select name="${name}">${values.map(v =>
    `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`;
  const saveBtn = (label, tip, extra = '') => `<button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tip)}" ${extra}>${icon('save')} ${esc(label)}</button>`;
  const msgEl = '<div class="of-msg" role="status"></div>';

  function onSubmit(form, fn) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = form.querySelector('.of-msg');
      const btn = e.submitter || form.querySelector('button[type="submit"]');
      if (btn) btn.disabled = true;
      try {
        await fn(msg, btn);
      } catch (err) {
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      } finally {
        if (btn) setTimeout(() => { btn.disabled = false; }, 1500);
      }
    });
  }

  // -- Général ----------------------------------------------------------------
  function kvRow(k = '', v = '') {
    return `<div class="vm-kv-row" data-hs-kv>
      <input type="text" data-kv="key" value="${esc(k)}" placeholder="${esc(tr('vm.edit.kvKey'))}" class="tip" data-tip="${esc(tr('hs.tip.labelKey'))}">
      <input type="text" data-kv="value" value="${esc(v)}" placeholder="${esc(tr('vm.edit.kvValue'))}" class="tip" data-tip="${esc(tr('vm.edit.tKvValue'))}">
      <button type="button" class="btn-icon-sm tip" data-kv-del data-tip="${esc(tr('vm.edit.tKvDel'))}">×</button></div>`;
  }

  const RENDER = {
    general: (w, d) => `<form class="of-form hs-form" autocomplete="off">
      <div class="hs-grid">
        ${field(tr('hs.customName'), `<input type="text" name="custom_name" maxlength="120" value="${esc(d.custom_name)}" placeholder="${esc(d.node)}">`, tr('hs.tip.customName'))}
        ${field(tr('hs.consoleUrl'), `<input type="url" name="console_url" value="${esc(d.console_url)}" placeholder="https://10.0.0.21">`, tr('hs.tip.consoleUrl'))}
      </div>
      ${d.console_url && /^[a-z]+:\/\//.test(d.console_url) ? `<p class="form-hint"><a href="${esc(d.console_url)}" target="_blank" rel="noopener noreferrer" class="tip" data-tip="${esc(tr('hs.tip.openConsole'))}">${icon('console')} ${esc(tr('hs.openConsole'))}</a></p>` : ''}
      ${field(tr('hs.hostTags'), `<input type="text" name="tags" value="${esc((d.tags || []).join(', '))}" placeholder="fast, ssd" ${d.longhorn ? '' : 'disabled'}>`, tr('hs.tip.hostTags'))}
      <h4 class="hs-sub">${esc(tr('hs.labels'))}</h4>
      <p class="form-hint">${esc(tr('hs.labelsHint'))}</p>
      <div class="vm-kv-rows" data-hs="kv">${Object.entries(d.labels || {}).map(([k, v]) => kvRow(k, v)).join('')}</div>
      <button type="button" class="btn btn-sm btn-secondary tip" data-hs="kv-add" data-tip="${esc(tr('vm.edit.tKvAdd'))}">${icon('add', 13)} ${esc(tr('vm.edit.kvAdd'))}</button>
      <div class="bk-form-actions">${saveBtn(tr('hs.save'), tr('hs.tip.save'))}</div>${msgEl}</form>`,

    disks: (w, d) => {
      const added = (d.disks || []).filter(x => x.in_longhorn || x.provisioned);
      const free = (d.disks || []).filter(x => x.addable);
      const other = (d.disks || []).filter(x => !x.addable && !x.in_longhorn && !x.provisioned);
      const row = (x) => `<form class="of-form hs-disk" data-disk="${esc(x.name)}" autocomplete="off">
        <div class="hs-disk-head">${icon('disk')} <strong>${esc(x.dev_path || x.name)}</strong>
          <span class="res-dim">${esc(size(x.size))} · ${esc(x.phase || x.state || '')}${x.provisioner ? ' · ' + esc(x.provisioner) : ''}</span>
          ${x.in_longhorn ? `<span class="badge ${x.ready && x.schedulable ? 'ok' : 'warn'} tip" data-tip="${esc(tr('hs.tip.diskState'))}">${esc(x.ready ? (x.schedulable ? tr('hs.diskReady') : tr('hs.diskNotSched')) : tr('hs.diskNotReady'))}</span>` : ''}
        </div>
        ${x.in_longhorn ? `<p class="res-dim hs-disk-use">${esc(tr('hs.diskUse', { avail: size(x.storage_available), max: size(x.storage_maximum), sched: size(x.storage_scheduled) }))}</p>
        <div class="hs-grid">
          ${field(tr('hs.diskTags'), `<input type="text" name="tags" value="${esc((x.tags || []).join(', '))}" placeholder="nvme">`, tr('hs.tip.diskTags'))}
          ${check('scheduling', tr('hs.diskScheduling'), tr('hs.tip.diskScheduling'), x.scheduling !== false)}
        </div>` : `<p class="form-hint">${esc(tr('hs.diskPending'))}</p>`}
        <div class="bk-form-actions">
          ${x.in_longhorn ? saveBtn(tr('hs.save'), tr('hs.tip.diskSave'), 'data-hs-op="disk-set"') : ''}
          ${x.removable === false ? '' : `<button type="submit" class="btn btn-sm btn-danger tip" data-hs-op="disk-remove" data-tip="${esc(tr('hs.tip.diskRemove'))}">${icon('delete')} ${esc(tr('hs.diskRemove'))}</button>`}
        </div>${msgEl}</form>`;
      const freeRow = (x) => `<form class="of-form hs-disk" data-disk="${esc(x.name)}" autocomplete="off">
        <div class="hs-disk-head">${icon('disk')} <strong>${esc(x.dev_path || x.name)}</strong>
          <span class="res-dim">${esc(size(x.size))} · ${esc(x.type || '')}</span></div>
        <div class="hs-grid">
          ${field(tr('hs.provisioner'), select('provisioner', ['LonghornV1', 'LonghornV2', 'lvm'], 'LonghornV1'), tr('hs.tip.provisioner'))}
          ${field(tr('hs.vg'), '<input type="text" name="vg" placeholder="vg-data" disabled>', tr('hs.tip.vg'))}
          ${check('format', tr('hs.format'), tr('hs.tip.format'), true)}
        </div>
        <div class="bk-form-actions"><button type="submit" class="btn btn-sm btn-primary tip" data-hs-op="disk-add" data-tip="${esc(tr('hs.tip.diskAdd'))}">${icon('add')} ${esc(tr('hs.diskAdd'))}</button></div>${msgEl}</form>`;
      return `<div class="hs-disks">
        <h4 class="hs-sub">${esc(tr('hs.disksAdded'))} <span class="res-dim">(${added.length})</span></h4>
        ${added.map(row).join('') || `<p class="hint">${esc(tr('hs.none'))}</p>`}
        <h4 class="hs-sub">${esc(tr('hs.disksFree'))} <span class="res-dim">(${free.length})</span></h4>
        <p class="form-hint">${esc(tr('hs.disksFreeHint'))}</p>
        ${free.map(freeRow).join('') || `<p class="hint">${esc(tr('hs.noFreeDisk'))}</p>`}
        ${other.length ? `<details class="vm-edit-adv"><summary>${esc(tr('hs.disksOther'))} <span class="res-dim">(${other.length})</span></summary>
          <ul class="hs-other">${other.map(x => `<li>${esc(x.dev_path || x.name)} <span class="res-dim">${esc(size(x.size))} · ${esc(x.type || '')} · ${esc(x.mounted ? tr('hs.mounted') : (x.state || ''))}</span></li>`).join('')}</ul></details>` : ''}
      </div>`;
    },

    hugepages: (w, d) => {
      if (!d.hugepages) return `<p class="hint">${esc(tr('hs.noHugepages'))}</p>`;
      const t = d.hugepages.transparent || {};
      const mi = d.hugepages.meminfo || {};
      return `<form class="of-form hs-form" autocomplete="off">
        <p class="form-hint">${esc(tr('hs.hugepagesHint'))}</p>
        <div class="hs-grid">
          ${field(tr('hs.thpEnabled'), select('enabled', THP_ENABLED, t.enabled), tr('hs.tip.thpEnabled'))}
          ${field(tr('hs.thpShmem'), select('shmem', THP_SHMEM, t.shmemEnabled), tr('hs.tip.thpShmem'))}
          ${field(tr('hs.thpDefrag'), select('defrag', THP_DEFRAG, t.defrag), tr('hs.tip.thpDefrag'))}
        </div>
        ${Object.keys(mi).length ? `<dl class="kv hs-kv">${Object.entries(mi).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>` : ''}
        <div class="bk-form-actions">${saveBtn(tr('hs.save'), tr('hs.tip.save'))}</div>${msgEl}</form>`;
    },

    ksm: (w, d) => {
      if (!d.ksmtuned) return `<p class="hint">${esc(tr('hs.noKsm'))}</p>`;
      const s = d.ksmtuned.spec || {};
      const st = d.ksmtuned.status || {};
      const p = s.ksmtunedParameters || {};
      const stats = ['sharing', 'shared', 'unshared', 'volatile', 'fullScans', 'ksmdPhase'].filter(k => st[k] !== undefined);
      return `<form class="of-form hs-form" autocomplete="off">
        <p class="form-hint">${esc(tr('hs.ksmHint'))}</p>
        <div class="hs-grid">
          ${field(tr('hs.ksmRun'), select('run', ['stop', 'run', 'prune'], s.run || 'stop'), tr('hs.tip.ksmRun'))}
          ${field(tr('hs.ksmMode'), select('mode', ['standard', 'high', 'customized'], s.mode || 'standard'), tr('hs.tip.ksmMode'))}
          ${field(tr('hs.ksmThres'), `<input type="number" name="thres" min="0" max="100" value="${esc(s.thresCoef ?? 20)}">`, tr('hs.tip.ksmThres'))}
          ${check('merge', tr('hs.ksmMerge'), tr('hs.tip.ksmMerge'), Number(s.mergeAcrossNodes) === 1)}
        </div>
        <div class="hs-grid hs-ksm-params" ${s.mode === 'customized' ? '' : 'hidden'}>
          ${KSM_PARAMS.map(k => field(k, `<input type="number" name="p-${k}" min="0" value="${esc(p[k] ?? '')}">`, tr('hs.tip.ksmParam', { name: k }))).join('')}
        </div>
        ${stats.length ? `<dl class="kv hs-kv">${stats.map(k => `<dt>${esc(k)}</dt><dd>${esc(st[k])}</dd>`).join('')}</dl>` : ''}
        <div class="bk-form-actions">${saveBtn(tr('hs.save'), tr('hs.tip.save'))}</div>${msgEl}</form>`;
    },

    oob: (w, d) => {
      if (!d.seeder) {
        return `<p class="hint">${icon('info')} ${esc((d.seeder_installed ? tr('hs.seederOff') : tr('hs.seederMissing')))}</p>`;
      }
      const o = d.oob || {};
      const ev = o.events || {};
      const maint = !!d.maintenance;
      const ready = o.status === 'inventoryNodeReady';
      const pwr = (op, ico, cls) => {
        const blocked = !d.oob ? tr('hs.powerNoOob') : !maint ? tr('hs.powerNeedMaint') : !ready ? tr('hs.powerNotReady') : '';
        return `<button type="button" class="btn btn-sm ${cls} tip" data-hs-power="${op}" ${blocked ? 'disabled' : ''}
          data-tip="${esc(blocked || POWER[op].tip())}">${icon(ico)} ${esc(POWER[op].label())}</button>`;
      };
      return `<form class="of-form hs-form" autocomplete="off">
        <p class="form-hint">${esc(tr('hs.oobHint'))}</p>
        ${o.error ? `<p class="res-error hs-oob-state">${icon('warn')} ${esc(tr('hs.oobError', { error: o.error }))}</p>` : ''}
        ${d.oob ? `<p class="hs-oob-state">${icon(ready ? 'ok' : 'pending')} ${esc(tr('hs.oobState', { status: o.status || '?', power: o.power_state || '?' }))}${o.secret ? ` <span class="res-dim">· ${esc(tr('hs.oobSecret', { secret: o.secret }))}</span>` : ''}</p>` : ''}
        <div class="hs-grid">
          ${field(tr('hs.bmcHost'), `<input type="text" name="host" value="${esc(o.host || '')}" placeholder="10.0.0.21" required>`, tr('hs.tip.bmcHost'))}
          ${field(tr('hs.bmcPort'), `<input type="number" name="port" min="1" max="65535" value="${esc(o.port || 623)}">`, tr('hs.tip.bmcPort'))}
          ${field(tr('hs.bmcUser'), '<input type="text" name="username" autocomplete="off">', tr('hs.tip.bmcUser'))}
          ${field(tr('hs.bmcPassword'), '<input type="password" name="password" autocomplete="new-password">', (d.oob ? tr('hs.tip.bmcPasswordKeep') : tr('hs.tip.bmcPassword')))}
          ${check('insecure', tr('hs.bmcInsecure'), tr('hs.tip.bmcInsecure'), !!o.insecure)}
          ${check('events', tr('hs.bmcEvents'), tr('hs.tip.bmcEvents'), ev.enabled !== false)}
          ${field(tr('hs.bmcInterval'), `<input type="text" name="interval" value="${esc(ev.pollingInterval || '1h')}" pattern="\\d+[hms]">`, tr('hs.tip.bmcInterval'))}
        </div>
        <div class="bk-form-actions">
          ${saveBtn(tr('hs.save'), tr('hs.tip.oobSave'), 'data-hs-op="oob"')}
          ${d.oob ? `<button type="submit" class="btn btn-sm btn-danger tip" data-hs-op="oob-off" data-tip="${esc(tr('hs.tip.oobOff'))}">${icon('delete')} ${esc(tr('hs.oobOff'))}</button>` : ''}
        </div>
        <h4 class="hs-sub">${esc(tr('hs.power'))}</h4>
        <p class="form-hint">${esc(tr('hs.powerHint'))}</p>
        <div class="hs-power">${pwr('shutdown', 'stop', 'btn-warn')}${pwr('poweron', 'play', 'btn-secondary')}${pwr('reboot', 'restart', 'btn-warn')}</div>
        ${msgEl}</form>`;
    },

    actions: (w, d) => {
      const cm = d.cpu_manager || {};
      const busy = ['requested', 'running'].includes(cm.status);
      const cmBlocked = d.witness ? tr('hs.cpuWitness') : cm.label === undefined || cm.label === null ? tr('hs.cpuNoLabel')
        : busy ? tr('hs.cpuBusy') : '';
      const last = d.nodes < 2;
      return `<div class="hs-actions">
        <form class="of-form hs-form" data-hs-form="cpu" autocomplete="off">
          <h4 class="hs-sub">${esc(tr('hs.cpuManager'))}</h4>
          <p class="form-hint">${esc(tr('hs.cpuHint'))}</p>
          <p>${icon(cm.enabled ? 'ok' : 'info')} ${esc(cm.enabled ? tr('hs.cpuOn') : tr('hs.cpuOff'))}${cm.status ? ` <span class="res-dim">(${esc(cm.policy || '')} · ${esc(cm.status)})</span>` : ''}</p>
          <div class="bk-form-actions"><button type="submit" class="btn btn-sm ${cm.enabled ? 'btn-warn' : 'btn-primary'} tip" ${cmBlocked ? 'disabled' : ''}
            data-tip="${esc(cmBlocked || (cm.enabled ? tr('hs.tip.cpuDisable') : tr('hs.tip.cpuEnable')))}">${icon('compute')} ${esc((cm.enabled ? tr('hs.cpuDisable') : tr('hs.cpuEnable')))}</button></div>
          ${msgEl}</form>
        <form class="of-form hs-form hs-danger" data-hs-form="delete" autocomplete="off">
          <h4 class="hs-sub">${esc(tr('hs.delete'))}</h4>
          <p class="form-hint">${esc((last ? tr('hs.deleteLast') : d.capi_machine ? tr('hs.deleteHintCapi') : tr('hs.deleteHint')))}</p>
          ${field(tr('hs.deleteConfirm', { name: d.node }), `<input type="text" name="confirm" autocomplete="off" ${last ? 'disabled' : ''}>`, tr('hs.tip.deleteConfirm'))}
          <div class="bk-form-actions"><button type="submit" class="btn btn-sm btn-danger tip" ${last ? 'disabled' : ''}
            data-tip="${esc(last ? tr('hs.deleteLast') : tr('hs.tip.delete'))}">${icon('trash')} ${esc(tr('hs.deleteBtn'))}</button></div>
          ${msgEl}</form>
      </div>`;
    },
  };

  const splitTags = (v) => String(v || '').split(',').map(s => s.trim()).filter(Boolean);
  const sameList = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

  const WIRE = {
    general: (w, host, d) => {
      const form = host.querySelector('form');
      host.querySelector('[data-hs="kv-add"]').addEventListener('click', () => {
        host.querySelector('[data-hs="kv"]').insertAdjacentHTML('beforeend', kvRow());
      });
      host.querySelector('[data-hs="kv"]').addEventListener('click', (e) => {
        if (e.target.closest('[data-kv-del]')) e.target.closest('[data-hs-kv]').remove();
      });
      onSubmit(form, async (msg) => {
        const labels = {};
        form.querySelectorAll('[data-hs-kv]').forEach(r => {
          const k = r.querySelector('[data-kv="key"]').value.trim();
          if (!k) return;
          if (HIDDEN_LABEL.test(k) && !SHOWN.includes(k)) throw new Error(`${tr('vm.edit.errKvKey')}: "${k}"`);
          if (k in labels) throw new Error(`${tr('vm.edit.errTagDup')}: ${k}`);
          labels[k] = r.querySelector('[data-kv="value"]').value;
        });
        const body = {};
        const name = form.custom_name.value.trim();
        const url = form.console_url.value.trim();
        if (name !== (d.custom_name || '')) body.custom_name = name;
        if (url !== (d.console_url || '')) body.console_url = url;
        const before = d.labels || {};
        const keys = new Set([...Object.keys(before), ...Object.keys(labels)]);
        if ([...keys].some(k => before[k] !== labels[k])) body.labels = labels;
        const tags = splitTags(form.tags.value);
        const tagsChanged = d.longhorn && !sameList(tags, d.tags || []);
        if (!Object.keys(body).length && !tagsChanged) { msg.textContent = tr('hs.nothing'); return; }
        if (Object.keys(body).length) await act(w, 'basics', body, msg, tr('hs.done.saved', { node: d.node }));
        if (tagsChanged) {
          const m2 = document.createElement('div');
          msg.after(m2);
          await act(w, 'tags', { tags }, m2, tr('hs.done.tags', { node: d.node }));
        }
      });
    },

    disks: (w, host, d) => {
      host.querySelectorAll('form.hs-disk').forEach(form => {
        const disk = form.dataset.disk;
        const prov = form.querySelector('[name="provisioner"]');
        if (prov) prov.addEventListener('change', () => { form.querySelector('[name="vg"]').disabled = prov.value !== 'lvm'; });
        onSubmit(form, async (msg, btn) => {
          const op = btn && btn.dataset.hsOp;
          const x = (d.disks || []).find(y => y.name === disk) || {};
          const label = x.dev_path || disk;
          if (op === 'disk-remove') {
            if (!confirm(tr('hs.confirm.diskRemove', { disk: label, node: d.node }))) return;
            await act(w, 'disk-remove', { disk }, msg, tr('hs.done.diskRemoved', { disk: label }));
          } else if (op === 'disk-add') {
            const format = form.format.checked;
            if (format && !confirm(tr('hs.confirm.diskFormat', { disk: label, node: d.node }))) return;
            const body = { disk, provisioner: prov.value, format };
            if (prov.value === 'lvm') body.vg = form.vg.value.trim();
            await act(w, 'disk-add', body, msg, tr('hs.done.diskAdded', { disk: label }));
          } else {
            await act(w, 'disk-set', { disk, tags: splitTags(form.tags.value), scheduling: form.scheduling.checked },
              msg, tr('hs.done.saved', { node: d.node }));
          }
        });
      });
    },

    hugepages: (w, host, d) => {
      const form = host.querySelector('form');
      if (!form) return;
      onSubmit(form, (msg) => act(w, 'hugepages',
        { enabled: form.enabled.value, shmem: form.shmem.value, defrag: form.defrag.value },
        msg, tr('hs.done.saved', { node: d.node })));
    },

    ksm: (w, host, d) => {
      const form = host.querySelector('form');
      if (!form) return;
      form.mode.addEventListener('change', () => {
        form.querySelector('.hs-ksm-params').hidden = form.mode.value !== 'customized';
      });
      onSubmit(form, (msg) => {
        const body = { run: form.run.value, mode: form.mode.value, thres: Number(form.thres.value), merge: form.merge.checked };
        if (body.mode === 'customized') {
          body.params = {};
          KSM_PARAMS.forEach(k => { body.params[k] = Number(form[`p-${k}`].value); });
        }
        return act(w, 'ksmtuned', body, msg, tr('hs.done.saved', { node: d.node }));
      });
    },

    oob: (w, host, d) => {
      const form = host.querySelector('form');
      if (!form || !form.host) return;
      form.querySelectorAll('[data-hs-power]').forEach(b => b.addEventListener('click', async () => {
        const op = b.dataset.hsPower;
        if (!confirm(POWER[op].confirm({ node: d.node }))) return;
        const msg = form.querySelector('.of-msg');
        b.disabled = true;
        try {
          await act(w, 'power', { operation: op }, msg, tr('hs.done.power', { node: d.node, op }));
        } catch (err) {
          msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
          b.disabled = false;
        }
      }));
      onSubmit(form, async (msg, btn) => {
        if (btn && btn.dataset.hsOp === 'oob-off') {
          if (!confirm(tr('hs.confirm.oobOff', { node: d.node }))) return;
          await act(w, 'oob', { off: true }, msg, tr('hs.done.oobOff', { node: d.node }));
          return;
        }
        const body = { host: form.host.value.trim(), port: Number(form.port.value) || 623, insecure: form.insecure.checked,
                       events: form.events.checked, interval: form.interval.value.trim() || '1h' };
        const pw = form.password.value;
        if (pw) { body.username = form.username.value.trim(); body.password = pw; }
        else if (!d.oob) throw new Error(tr('hs.errBmcCreds'));
        form.password.value = '';
        await act(w, 'oob', body, msg, tr('hs.done.oob', { node: d.node }));
      });
    },

    actions: (w, host, d) => {
      const cpu = host.querySelector('[data-hs-form="cpu"]');
      onSubmit(cpu, async (msg) => {
        const enable = !(d.cpu_manager || {}).enabled;
        if (!confirm((enable ? tr('hs.confirm.cpuEnable', { node: d.node }) : tr('hs.confirm.cpuDisable', { node: d.node })))) return;
        await act(w, 'cpu-manager', { enable }, msg, (enable ? tr('hs.done.cpuOn', { node: d.node }) : tr('hs.done.cpuOff', { node: d.node })));
      });
      const del = host.querySelector('[data-hs-form="delete"]');
      onSubmit(del, async (msg) => {
        if (del.confirm.value.trim() !== d.node) throw new Error(tr('hs.errConfirm', { name: d.node }));
        await act(w, 'delete', {}, msg, tr('hs.done.deleted', { node: d.node }));
      });
    },
  };

  function open(cluster, node, opts = {}) {
    if (!cluster || !node) return;
    const id = `host-${cluster}-${node}`;
    let first = opts.tab;
    if (!first) { try { first = localStorage.getItem('harvester_ops_host_tab'); } catch { /* stockage indisponible */ } }
    if (!TABS.includes(first)) first = 'general';
    const panel = FloatingPanels.open({
      id, icon: 'node', width: 760, height: 620,
      title: tr('hs.title', { node }),
      restoreSpec: { type: 'host-settings', args: { cluster, node, tab: first } },
      entity: { key: `${cluster}|node/${node}`, label: node },
      onClose: () => { const x = WINS.get(id); if (x && x.timer) clearInterval(x.timer); WINS.delete(id); },
      bodyHtml: `<div class="hs-win">
        <div class="hs-head">${icon('node')} <strong data-hs="name">${esc(node)}</strong> <span class="res-dim">${esc(cluster)}</span></div>
        <div class="sub-tabs sub-tabs-inline hs-tabs" role="tablist">
          ${TABS.map(t => `<button type="button" class="sub-tab tip" role="tab" data-hs-tab="${t}"
            data-tip="${esc(TAB_TIP[t]())}">${icon(TAB_ICON[t])} <span>${esc(TAB_LABEL[t]())}</span></button>`).join('')}
        </div>
        <p class="res-error" data-hs="err"></p>
        <div class="hs-last" data-hs="last" role="status" aria-live="polite"></div>
        <div class="hs-body" data-hs="body"></div>
      </div>`,
    });
    const root = panel.el;
    if (root.dataset.hsReady) { const w = WINS.get(id); if (w && opts.tab) showTab(w, opts.tab); return; }
    root.dataset.hsReady = '1';
    const w = { id, cluster, node, tab: first, root, data: null, onDone: opts.onDone };
    WINS.set(id, w);
    root.querySelectorAll('[data-hs-tab]').forEach(b => b.addEventListener('click', () => {
      showTab(w, b.dataset.hsTab);
      reload(w, true);
    }));
    // une saisie en cours n'est jamais effacée par une relecture de fond
    root.querySelector('[data-hs="body"]').addEventListener('input', () => { w.dirty = true; });
    w.timer = setInterval(() => { if (!document.hidden && document.body.contains(root)) reload(w, true); }, 20000);
    showTab(w, first);
    reload(w);
  }

  if (window.FloatingPanels && FloatingPanels.registerType) {
    FloatingPanels.registerType('host-settings', (a) => open(a.cluster, a.node, { tab: a.tab }));
  }
  return { open };
})();
window.HostSettings = HostSettings;
