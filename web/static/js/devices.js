/**
 * harvester-ops — les périphériques de Harvester (v1.68.0)
 *
 * Trois onglets de la section Advanced : PCI Devices, USB Devices et SR-IOV
 * Network Devices. Chaque ligne dit l'état du passthrough (activé, en cours,
 * non), le groupe IOMMU (tout le groupe part avec le périphérique), les VMs
 * qui s'en servent ; activer et désactiver se font à l'unité ou par
 * sélection, et se suivent dans le dock. Une carte SR-IOV reçoit un nombre
 * de fonctions virtuelles, qui deviennent des périphériques PCI à passer.
 */
const Devices = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 13) => (window.Icons ? Icons.svg(n, { size }) : '');
  const REFRESH_MS = 10000;

  let cur = null;      // { cluster, host, kind, timer, data, filter, node, onlyOn, picked }

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

  // -- cycle de vie (appelé par Sections) -------------------------------------
  function start(cluster, host) {
    stop();
    const kind = ['pci', 'usb', 'sriov'].includes(host.dataset.adv) ? host.dataset.adv : 'pci';
    cur = { cluster, host, kind, data: null, filter: '', node: '', onlyOn: false, picked: new Set() };
    shell();
    cur.timer = setInterval(() => {
      if (cur && cur.host.isConnected && !cur.host.closest('[hidden]')) load();
    }, REFRESH_MS);
    return load();
  }

  function stop() {
    if (cur && cur.timer) clearInterval(cur.timer);
    cur = null;
  }

  async function load() {
    if (!cur) return;
    const c = cur;
    const d = await getJSON(`/api/devices/${enc(c.cluster)}`);
    if (c !== cur) return;
    c.data = d;
    render();
  }

  function shell() {
    const bulk = cur.kind !== 'sriov';
    cur.host.innerHTML = `<div class="card na-card dev-card">
        <div class="res-tools">
          <input type="search" class="res-filter tip" data-dev="filter" data-tip="${esc(tr('dev.t.filter'))}" placeholder="${esc(tr('dev.filterPh'))}">
          <select class="tip" data-dev="node" data-tip="${esc(tr('dev.t.node'))}"><option value="">${esc(tr('dev.allNodes'))}</option></select>
          <span class="res-count"></span>
          ${bulk ? `<label class="res-system tip" data-tip="${esc(tr('dev.t.onlyOn'))}"><input type="checkbox" data-dev="only-on"> <span>${esc(tr('dev.onlyOn'))}</span></label>
          <button type="button" class="btn btn-sm btn-primary tip needs-admin" data-dev="bulk-enable" disabled data-tip="${esc(tr('dev.t.bulkEnable'))}">${icon('plug')} ${esc(tr('dev.enable'))}</button>
          <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-dev="bulk-disable" disabled data-tip="${esc(tr('dev.t.bulkDisable'))}">${icon('eject')} ${esc(tr('dev.disable'))}</button>` : ''}
          <button type="button" class="btn btn-sm btn-secondary tip" data-dev="refresh" data-tip="${esc(tr('res.refreshTip'))}">${icon('refresh')} ${esc(tr('overview.refresh'))}</button>
        </div>
        <div class="res-feedback" data-dev="feedback"></div>
        <div data-dev="body"><p class="form-hint">${esc(tr('common.loading'))}</p></div></div>`;
    const card = cur.host.querySelector('.dev-card');
    card.addEventListener('click', onClick);
    card.addEventListener('change', onChange);
    card.querySelector('[data-dev="filter"]').addEventListener('input', (e) => { cur.filter = e.target.value; render(); });
  }

  function onChange(e) {
    const t = e.target;
    if (!cur) return;
    if (t.dataset.dev === 'node') { cur.node = t.value; render(); }
    else if (t.dataset.dev === 'only-on') { cur.onlyOn = t.checked; render(); }
    else if (t.dataset.dev === 'pick') {
      if (t.checked) cur.picked.add(t.value); else cur.picked.delete(t.value);
      syncBulk();
    } else if (t.dataset.dev === 'pick-all') {
      cur.host.querySelectorAll('[data-dev="pick"]').forEach(b => {
        b.checked = t.checked;
        if (t.checked) cur.picked.add(b.value); else cur.picked.delete(b.value);
      });
      syncBulk();
    }
  }

  function rowsOf(kind) { return ((cur.data || {})[kind]) || []; }

  function syncBulk() {
    const rows = rowsOf(cur.kind).filter(r => cur.picked.has(r.name));
    const en = cur.host.querySelector('[data-dev="bulk-enable"]');
    const dis = cur.host.querySelector('[data-dev="bulk-disable"]');
    if (en) en.disabled = !rows.length || !rows.every(r => r.can_enable);
    if (dis) dis.disabled = !rows.length || !rows.every(r => r.state !== 'disabled' && !r.used_by.length);
  }

  function stateBadge(r) {
    if (r.state === 'enabled') return badge('ok', tr('dev.st.enabled'), tr('dev.t.enabled'));
    if (r.state === 'pending') return badge('info', tr('na.pending'), r.message || tr('dev.t.pending'));
    return badge('dim', tr('dev.st.host'), tr('dev.t.host'));
  }

  function usedBy(r) {
    return r.used_by.length ? r.used_by.map(v => `<code class="res-key">${esc(v)}</code>`).join(' ') : '–';
  }

  function render() {
    if (!cur || !cur.host.isConnected) return;
    const body = cur.host.querySelector('[data-dev="body"]');
    const d = cur.data;
    if (!d) { body.innerHTML = `<p class="res-error">${esc(tr('fabric.unreachable'))}</p>`; return; }
    if (d.unreachable || d.error) {
      body.innerHTML = `<div class="sto-finding sev-critical"><div class="sto-finding-title">${esc(d.error || tr('fabric.unreachable'))}</div></div>`;
      return;
    }
    if (!d.addon) {
      body.innerHTML = `<div class="sto-finding sev-action"><div class="sto-finding-title">${icon('warn')} ${esc(tr('dev.noAddon'))}</div>
        <button type="button" class="btn btn-sm btn-secondary tip" data-dev="open-addons" data-tip="${esc(tr('dev.t.openAddons'))}">${icon('plug')} ${esc(tr('tab.addons'))}</button></div>`;
      return;
    }
    const all = rowsOf(cur.kind);
    const nodes = [...new Set(all.map(r => r.node).filter(Boolean))].sort();
    const sel = cur.host.querySelector('[data-dev="node"]');
    if (sel && sel.options.length !== nodes.length + 1) {
      sel.innerHTML = `<option value="">${esc(tr('dev.allNodes'))}</option>` + nodes.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
      sel.value = nodes.includes(cur.node) ? cur.node : '';
    }
    const q = cur.filter.trim().toLowerCase();
    const rows = all.filter(r => (!cur.node || r.node === cur.node) && (!cur.onlyOn || r.state !== 'disabled')
      && (!q || JSON.stringify(r).toLowerCase().includes(q)));
    cur.picked = new Set([...cur.picked].filter(n => rows.some(r => r.name === n)));
    cur.host.querySelector('.res-count').textContent = tr('dev.count', { n: rows.length });
    if (!rows.length) {
      body.innerHTML = `<p class="form-hint">${esc(cur.kind === 'sriov' ? tr('dev.noSriov') : tr('dev.none'))}</p>`;
      syncBulk();
      return;
    }
    const pick = (r) => `<input type="checkbox" class="tip" data-dev="pick" value="${esc(r.name)}" ${cur.picked.has(r.name) ? 'checked' : ''}
        data-tip="${esc(tr('dev.t.pick'))}">`;
    const act = (r) => (r.state === 'disabled'
      ? `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-dev="enable" ${r.can_enable ? '' : 'disabled'}
          data-tip="${esc(r.can_enable ? tr('dev.t.enable') : tr('dev.t.noIommu'))}">${icon('plug')} ${esc(tr('dev.enable'))}</button>`
      : `<button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-dev="disable" ${r.used_by.length ? 'disabled' : ''}
          data-tip="${esc(r.used_by.length ? tr('dev.t.inUse') : tr('dev.t.disable'))}">${icon('eject')} ${esc(tr('dev.disable'))}</button>`);
    const head = (cols) => `<thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead>`;
    const pickAll = `<input type="checkbox" class="tip" data-dev="pick-all" data-tip="${esc(tr('dev.t.pickAll'))}">`;
    if (cur.kind === 'pci') {
      body.innerHTML = `<table class="data-table dev-table">${head([pickAll, esc(tr('dev.col.device')), esc(tr('of.f.description')),
          esc(tr('dev.col.driver')), esc(tr('dev.col.group')), esc(tr('adv.col.state')), esc(tr('dev.col.usedBy')), ''])}<tbody>
        ${rows.map(r => `<tr data-device="${esc(r.name)}"><td>${pick(r)}</td>
          <td><strong>${esc(r.address)}</strong> <span class="res-dim">${esc(r.node)}</span><div class="res-dim">${esc(r.name)}</div></td>
          <td>${esc(r.description)}<div class="res-dim">${esc(r.vendor_id)}:${esc(r.device_id)}${r.vf_of ? ` · ${esc(tr('dev.vfOf', { dev: r.vf_of }))}` : ''}</div></td>
          <td><code>${esc(r.driver || '–')}</code>${r.original_driver && r.original_driver !== r.driver ? `<div class="res-dim">${esc(tr('dev.wasDriver', { drv: r.original_driver }))}</div>` : ''}</td>
          <td>${r.iommu_group ? `<span class="tip" data-tip="${esc(r.siblings.length ? tr('dev.t.group', { list: r.siblings.join(', ') }) : tr('dev.t.groupAlone'))}">${esc(r.iommu_group)}${r.siblings.length ? ` <span class="res-dim">+${r.siblings.length}</span>` : ''}</span>` : `<span class="res-dim tip" data-tip="${esc(tr('dev.t.noIommu'))}">–</span>`}</td>
          <td>${stateBadge(r)}</td><td>${usedBy(r)}</td><td class="tpl-acts">${act(r)}</td></tr>`).join('')}</tbody></table>`;
    } else if (cur.kind === 'usb') {
      body.innerHTML = `<table class="data-table dev-table">${head([pickAll, esc(tr('dev.col.device')), esc(tr('of.f.description')),
          esc(tr('dev.col.path')), esc(tr('adv.col.state')), esc(tr('dev.col.usedBy')), ''])}<tbody>
        ${rows.map(r => `<tr data-device="${esc(r.name)}"><td>${pick(r)}</td>
          <td><strong>${esc(r.name)}</strong> <span class="res-dim">${esc(r.node)}</span></td>
          <td>${esc(r.description)}<div class="res-dim">${esc(r.vendor_id)}:${esc(r.product_id)}</div></td>
          <td><code>${esc(r.path || '–')}</code>${r.pci_address ? `<div class="res-dim">${esc(tr('dev.usbOn', { addr: r.pci_address }))}</div>` : ''}</td>
          <td>${stateBadge(r)}</td><td>${usedBy(r)}</td><td class="tpl-acts">${act(r)}</td></tr>`).join('')}</tbody></table>`;
    } else {
      body.innerHTML = `<table class="data-table dev-table">${head([esc(tr('dev.col.nic')), esc(tr('dev.col.vfs')),
          esc(tr('adv.col.state')), esc(tr('dev.col.vfsOn')), ''])}<tbody>
        ${rows.map(r => `<tr data-device="${esc(r.name)}">
          <td><strong>${esc(r.interface)}</strong> <span class="res-dim">${esc(r.node)} · ${esc(r.address)}</span><div class="res-dim">${esc(r.name)}</div></td>
          <td>${esc(r.num_vfs)}${r.vf_addresses.length ? `<div class="res-dim">${r.vf_addresses.map(esc).join(', ')}</div>` : ''}</td>
          <td>${r.enabled ? badge('ok', tr('dev.st.sriovOn')) : r.num_vfs ? badge('info', tr('na.pending')) : badge('dim', tr('dev.st.sriovOff'))}</td>
          <td>${r.vfs_claimed.length ? r.vfs_claimed.map(v => `<code class="res-key">${esc(v)}</code>`).join(' ') : '–'}</td>
          <td class="tpl-acts">${r.num_vfs
            ? `<button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-dev="sriov-off" ${r.vfs_claimed.length ? 'disabled' : ''}
                data-tip="${esc(r.vfs_claimed.length ? tr('dev.t.vfsInUse') : tr('dev.t.sriovOff'))}">${icon('eject')} ${esc(tr('dev.disable'))}</button>`
            : `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-dev="sriov-on" data-tip="${esc(tr('dev.t.sriovOn'))}">${icon('plug')} ${esc(tr('dev.enable'))}</button>`}</td></tr>`).join('')}
        </tbody></table><p class="form-hint">${esc(tr('dev.sriovHint'))}</p>`;
    }
    syncBulk();
  }

  function say(html) {
    const fb = cur && cur.host.querySelector('[data-dev="feedback"]');
    if (fb) fb.innerHTML = html;
  }

  async function run(action, body, doneText) {
    const c = cur;
    try {
      const out = await call('POST', `/api/devices/${enc(c.cluster)}/do/${action}`, body);
      const into = cur && cur.host.querySelector('[data-dev="feedback"]');
      follow(out.action_id, into, doneText, () => { if (c === cur) { cur.picked.clear(); setTimeout(load, 1000); } });
    } catch (err) {
      say(`<span class="res-error">${esc(err.message)}</span>`);
    }
  }

  function confirmEnable(rows) {
    const extra = rows.flatMap(r => r.siblings || []).filter(n => !rows.some(r => r.name === n));
    const hostDrv = rows.filter(r => r.driver && r.driver !== 'vfio-pci').map(r => `${r.address || r.name} (${r.driver})`);
    const lines = [tr('dev.confirm.enable', { list: rows.map(r => r.address || r.name).join(', ') })];
    if (hostDrv.length) lines.push(tr('dev.confirm.hostLoses', { list: hostDrv.join(', ') }));
    if (extra.length) lines.push(tr('dev.confirm.group', { list: [...new Set(extra)].join(', ') }));
    return confirm(lines.join('\n\n'));
  }

  function onClick(e) {
    const b = e.target.closest('[data-dev]');
    if (!b || !cur || b.tagName === 'INPUT' || b.tagName === 'SELECT') return;
    const act = b.dataset.dev;
    if (act === 'refresh') return load();
    if (act === 'open-addons' && window.Sections) return Sections.open('addons', 'list');
    const rows = rowsOf(cur.kind);
    const r = rows.find(x => x.name === b.closest('[data-device]')?.dataset.device);
    const bus = cur.kind === 'usb' ? 'usb' : 'pci';
    if ((act === 'enable' && r) || act === 'bulk-enable') {
      const list = r && act === 'enable' ? [r] : rows.filter(x => cur.picked.has(x.name));
      if (!list.length || !confirmEnable(list)) return;
      return run(`${bus}-enable`, { names: list.map(x => x.name) }, tr('dev.done.enable', { n: list.length }));
    }
    if ((act === 'disable' && r) || act === 'bulk-disable') {
      const list = r && act === 'disable' ? [r] : rows.filter(x => cur.picked.has(x.name));
      if (!list.length || !confirm(tr('dev.confirm.disable', { list: list.map(x => x.address || x.name).join(', ') }))) return;
      return run(`${bus}-disable`, { names: list.map(x => x.name) }, tr('dev.done.disable', { n: list.length }));
    }
    if (act === 'sriov-on' && r) return openVfs(r);
    if (act === 'sriov-off' && r) {
      if (!confirm(tr('dev.confirm.sriovOff', { name: r.interface, node: r.node }))) return;
      return run('sriov', { name: r.name, vfs: 0 }, tr('dev.done.sriovOff', { name: r.interface }));
    }
  }

  function openVfs(r) {
    const cluster = cur.cluster;
    const panel = FloatingPanels.open({
      id: `dev-vfs-${cluster}-${r.name}`, icon: 'network', width: 480, height: 330,
      title: `${tr('dev.vfsTitle', { name: r.interface })} · ${cluster}`,
      bodyHtml: `<form class="of-form" autocomplete="off"><p class="form-hint">${esc(tr('dev.vfsHint', { node: r.node }))}</p>
        <div class="of-fields"><label class="bk-field of-field" data-f="vfs"><span>${esc(tr('dev.col.vfs'))}</span>
          <input class="tip" name="vfs" type="number" min="1" max="256" value="2" required data-tip="${esc(tr('dev.t.vfs'))}"></label></div>
        <div class="bk-form-actions"><button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('bk.submitTip'))}">${icon('ok')} ${esc(tr('dev.enable'))}</button></div>
        <div class="of-msg" role="status"></div></form>`,
    });
    const form = panel.el.querySelector('.of-form');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = form.querySelector('button[type="submit"]');
      const msg = panel.el.querySelector('.of-msg');
      const n = Number(form.querySelector('[name="vfs"]').value);
      btn.disabled = true;
      try {
        const out = await call('POST', `/api/devices/${enc(cluster)}/do/sriov`, { name: r.name, vfs: n });
        follow(out.action_id, msg, tr('dev.done.sriovOn', { n, name: r.interface }), () => { btn.disabled = false; if (cur) setTimeout(load, 1000); });
      } catch (err) {
        btn.disabled = false;
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      }
    });
  }

  return { start, stop };
})();
window.Devices = Devices;
