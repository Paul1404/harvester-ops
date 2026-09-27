/**
 * harvester-ops — le menu Networks de Harvester (v1.65.0)
 *
 * L'onglet Cluster networks (un bloc par réseau de cluster, ses
 * configurations et leur état nœud par nœud, les réseaux de stockage, de
 * migration et RWX en tête) et toutes les fenêtres du menu : réseau de
 * cluster, configuration (cartes, bond, MTU, nœuds), migration d'une
 * configuration, réglage réseau, réseau d'hôte, équilibreur, pool d'adresses,
 * modification d'un réseau de VM. Chaque geste part à
 * /api/net-admin/<cluster>/do/<action> et se suit dans le dock.
 */
const NetAdmin = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 13) => (window.Icons ? Icons.svg(n, { size }) : '');
  const REFRESH_MS = 10000;
  const BONDS = ['active-backup', 'balance-rr', 'balance-xor', 'broadcast', '802.3ad', 'balance-tlb', 'balance-alb'];

  let cur = null;      // { cluster, host, timer, data }

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
  const field = (name, label, input, tip) => `<label class="bk-field of-field" data-f="${name}">
      <span>${esc(label)}</span>${input.replace('<input', `<input class="tip" data-tip="${esc(tip || '')}"`)
        .replace('<select', `<select class="tip" data-tip="${esc(tip || '')}"`)
        .replace('<textarea', `<textarea class="tip" data-tip="${esc(tip || '')}"`)}</label>`;
  const opts = (list, sel) => list.map(v => (Array.isArray(v) ? v : [v, v]))
    .map(([v, l, dis]) => `<option value="${esc(v)}" ${String(v) === String(sel) ? 'selected' : ''} ${dis ? 'disabled' : ''}>${esc(l)}</option>`).join('');
  const check = (name, label, tip, on) => `<label class="bk-check tip" data-f="${name}" data-tip="${esc(tip)}">
      <input type="checkbox" name="${name}" ${on ? 'checked' : ''}> <span>${esc(label)}</span></label>`;
  const post = (cluster, action, body) => call('POST', `/api/net-admin/${enc(cluster)}/do/${action}`, body);

  function follow(id, into, text, onDone) {
    if (window.Dock && Dock.poll) Dock.poll();
    if (window.VMActions && VMActions.follow) VMActions.follow(id, into, text, onDone);
    else if (into) into.textContent = tr('bk.started', { id });
  }

  // « 100-199, 300 » <-> [{min, max}]
  function parseRanges(text) {
    return String(text || '').split(/[,\s]+/).filter(Boolean).map(part => {
      const [a, b] = part.split('-');
      return { min: Number(a), max: Number(b || a) };
    });
  }
  const rangesText = (list) => (list || []).map(r => (r.min === r.max ? `${r.min}` : `${r.min}-${r.max}`)).join(', ');
  const lines = (text) => String(text || '').split('\n').map(s => s.trim()).filter(Boolean);

  // -- l'onglet Cluster networks -------------------------------------------------
  function start(cluster, host) {
    stop();
    cur = { cluster, host, data: null };
    host.innerHTML = `<div class="card na-card">
        <div class="res-tools">
          <span class="res-count"></span>
          <button type="button" class="btn btn-sm btn-secondary tip" data-na="refresh" data-tip="${esc(tr('res.refreshTip'))}">${icon('refresh')} ${esc(tr('overview.refresh'))}</button>
          <button type="button" class="btn btn-sm btn-primary tip needs-admin" data-na="new-cn" data-tip="${esc(tr('na.t.newCn'))}">${icon('add')} ${esc(tr('na.newCn'))}</button>
        </div>
        <div class="res-feedback" data-na="feedback"></div>
        <div data-na="body"><p class="form-hint">${esc(tr('common.loading'))}</p></div></div>`;
    host.querySelector('.na-card').addEventListener('click', onClick);
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
    const d = await getJSON(`/api/net-admin/${enc(c.cluster)}`);
    if (c !== cur) return;
    c.data = d;
    render();
  }

  function stateOf(st) {
    if (st.ready === true) return badge('ok', tr('na.ready'));
    if (st.ready === false) return badge('fail', tr('na.failed'), st.message);
    return badge('info', tr('na.pending'), st.message);
  }

  function settingTile(s) {
    const TITLE = { 'storage-network': tr('na.set.storage'), 'vm-migration-network': tr('na.set.migration'),
                    'rwx-network': tr('na.set.rwx') };
    const TIP = { 'storage-network': tr('na.set.storageTip'), 'vm-migration-network': tr('na.set.migrationTip'),
                  'rwx-network': tr('na.set.rwxTip') };
    const what = !s.enabled ? tr('na.set.mgmt')
      : s.share_storage ? tr('na.set.shared')
        : `${s.cluster_network} · ${s.vlan ? `VLAN ${s.vlan}` : tr('na.untagged')} · ${s.range}`;
    const st = !s.enabled ? '' : s.configured === 'True' ? badge('ok', tr('na.applied'))
      : s.reason === 'In Progress' ? badge('info', tr('na.pending'), s.message)
        : s.configured === 'False' ? badge('fail', s.reason || tr('na.failed'), s.message) : '';
    return `<div class="na-tile tip" data-tip="${esc(TIP[s.name])}">
        <div class="na-tile-title">${icon('network')} <strong>${esc(TITLE[s.name])}</strong> ${st}</div>
        <div class="res-dim">${esc(what)}</div>
        ${s.exclude && s.exclude.length ? `<div class="res-dim">${esc(tr('na.set.excluded', { list: s.exclude.join(', ') }))}</div>` : ''}
        <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-na="setting" data-name="${esc(s.name)}"
          data-tip="${esc(tr('na.t.setting'))}">${icon('edit')} ${esc(tr('na.change'))}</button></div>`;
  }

  function configRow(cn, c) {
    const applies = !Object.keys(c.selector || {}).length ? tr('na.allNodes')
      : c.selector['kubernetes.io/hostname'] && Object.keys(c.selector).length === 1 ? c.selector['kubernetes.io/hostname']
        : Object.entries(c.selector).map(([k, v]) => `${k}=${v}`).join(', ');
    const nodes = c.nodes.map(n => `<div class="na-node">${esc(n)} ${stateOf(c.status[n] || {})}</div>`).join('')
      || `<span class="res-dim">${esc(tr('na.noNode'))}</span>`;
    const acts = c.managed ? `<span class="res-dim tip" data-tip="${esc(tr('na.t.mgmtCfg'))}">${esc(tr('na.managed'))}</span>`
      : `<button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-na="edit-cfg" data-tip="${esc(tr('na.t.editCfg'))}">${icon('edit')}</button>
         <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-na="migrate-cfg" data-tip="${esc(tr('na.t.migrate'))}">${icon('migrate')}</button>
         <button type="button" class="btn btn-sm btn-danger tip needs-admin" data-na="delete-cfg" data-tip="${esc(tr('na.t.deleteCfg'))}">${icon('trash')}</button>`;
    return `<tr data-cfg="${esc(c.name)}">
        <td><strong>${esc(c.name)}</strong>${c.description ? `<div class="res-desc">${esc(c.description)}</div>` : ''}</td>
        <td>${esc(applies)}</td>
        <td>${c.nics.map(n => `<code>${esc(n)}</code>`).join(' ')}</td>
        <td>${esc(c.bond_mode)}${c.miimon != null && c.miimon >= 0 ? `<div class="res-dim">miimon ${esc(c.miimon)}</div>` : ''}</td>
        <td>${esc(c.mtu)}</td>
        <td>${nodes}</td>
        <td class="tpl-acts">${acts}
          <button type="button" class="btn btn-sm btn-secondary tip" data-na="yaml-cfg" data-tip="${esc(tr('yw.act.yamlTip'))}">${icon('code')}</button></td></tr>`;
  }

  function cnCard(cn) {
    const ready = cn.protected ? badge('ok', tr('na.ready'))
      : cn.ready === true ? badge('ok', tr('na.ready')) : badge('warn', tr('na.notReady'), tr('na.t.notReady'));
    const nets = cn.networks.length
      ? cn.networks.map(n => `<code class="res-key">${esc(n)}</code>`).join(' ')
      : `<span class="res-dim">${esc(tr('na.noVmNet'))}</span>`;
    return `<div class="card tpl-card" data-cn="${esc(cn.name)}">
        <div class="tpl-head">${icon('switch')}
          <span class="tpl-title"><strong>${esc(cn.name)}</strong> <span class="res-dim">${esc(cn.name)}-br</span> ${ready}
            ${cn.mtu ? `<span class="res-dim">MTU ${esc(cn.mtu)}</span>` : ''}
            ${cn.description ? `<span class="res-desc">${esc(cn.description)}</span>` : ''}</span>
          <span class="tpl-acts">
            ${cn.protected ? '' : `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-na="new-cfg" data-tip="${esc(tr('na.t.newCfg'))}">${icon('add')} ${esc(tr('na.newCfg'))}</button>`}
            <button type="button" class="btn btn-sm btn-secondary tip" data-na="yaml-cn" data-tip="${esc(tr('yw.act.yamlTip'))}">${icon('code')} YAML</button>
            ${cn.protected ? '' : `<button type="button" class="btn btn-sm btn-danger tip needs-admin" data-na="delete-cn"
                ${cn.configs.length || cn.networks.length ? 'disabled' : ''}
                data-tip="${esc(cn.configs.length || cn.networks.length ? tr('na.t.cnInUse') : tr('na.t.deleteCn'))}">${icon('trash')}</button>`}
          </span></div>
        ${cn.configs.length ? `<table class="data-table"><thead><tr><th>${esc(tr('na.col.config'))}</th><th>${esc(tr('na.col.applies'))}</th>
            <th>${esc(tr('na.col.nics'))}</th><th>${esc(tr('na.col.bond'))}</th><th>MTU</th><th>${esc(tr('na.col.nodes'))}</th><th></th></tr></thead>
            <tbody>${cn.configs.map(c => configRow(cn, c)).join('')}</tbody></table>`
          : `<p class="form-hint na-pad">${esc(cn.protected ? tr('na.mgmtInstalled') : tr('na.noConfig'))}</p>`}
        <div class="na-nets">${esc(tr('na.vmNets'))} ${nets}</div></div>`;
  }

  function render() {
    if (!cur || !cur.host.isConnected) return;
    const body = cur.host.querySelector('[data-na="body"]');
    const d = cur.data;
    if (!d) { body.innerHTML = `<p class="res-error">${esc(tr('fabric.unreachable'))}</p>`; return; }
    if (d.unreachable || d.error) {
      body.innerHTML = `<div class="sto-finding sev-critical"><div class="sto-finding-title">${esc(d.error || tr('fabric.unreachable'))}</div></div>`;
      return;
    }
    cur.host.querySelector('.res-count').textContent = tr('na.count', { n: d.cluster_networks.length });
    body.innerHTML = `<div class="na-tiles">${d.settings.map(settingTile).join('')}</div>
      ${d.cluster_networks.map(cnCard).join('')}`;
  }

  function say(html) {
    const fb = cur && cur.host.querySelector('[data-na="feedback"]');
    if (fb) fb.innerHTML = html;
  }

  async function onClick(e) {
    const b = e.target.closest('[data-na]');
    if (!b || !cur) return;
    const act = b.dataset.na;
    const c = cur.cluster;
    const d = cur.data || { cluster_networks: [], settings: [], nodes: [] };
    const cnName = b.closest('[data-cn]')?.dataset.cn;
    const cn = d.cluster_networks.find(x => x.name === cnName);
    const cfg = cn && cn.configs.find(x => x.name === b.closest('[data-cfg]')?.dataset.cfg);
    const again = () => setTimeout(load, 1500);
    if (act === 'refresh') return load();
    if (act === 'new-cn') return openClusterNetwork(c, again);
    if (act === 'new-cfg') return openConfig(c, cnName, null, d, again);
    if (act === 'edit-cfg' && cfg) return openConfig(c, cnName, cfg, d, again);
    if (act === 'migrate-cfg' && cfg) return openMigrate(c, cfg, d, again);
    if (act === 'setting') return openSetting(c, d.settings.find(s => s.name === b.dataset.name), d, again);
    if (act === 'yaml-cn' && window.YamlWindow) return YamlWindow.open(c, 'clusternetwork', '', cnName, { onDone: again });
    if (act === 'yaml-cfg' && cfg && window.YamlWindow) return YamlWindow.open(c, 'vlanconfig', '', cfg.name, { onDone: again });
    if (act === 'delete-cfg' && cfg) {
      if (!confirm(tr('na.confirm.deleteCfg', { name: cfg.name }))) return;
      return run(c, 'config-delete', { name: cfg.name }, tr('na.done.deleteCfg', { name: cfg.name }), again);
    }
    if (act === 'delete-cn' && cn) {
      if (!confirm(tr('na.confirm.deleteCn', { name: cn.name }))) return;
      return run(c, 'cn-delete', { name: cn.name }, tr('na.done.deleteCn', { name: cn.name }), again);
    }
  }

  async function run(cluster, action, body, doneText, onDone) {
    try {
      const out = await post(cluster, action, body);
      const into = cur && cur.host.querySelector('[data-na="feedback"]');
      follow(out.action_id, into, doneText, (ok) => { if (ok && onDone) onDone(); });
    } catch (err) {
      say(`<span class="res-error">${esc(err.message)}</span>`);
    }
  }

  // -- fenêtres ---------------------------------------------------------------------
  /** Une fenêtre à formulaire, dans la grammaire d'ObjectForms. `build`
   *  rend les champs, `request` la demande, `action` le geste. */
  function formWindow({ id, icon: ic, title, hint, width = 600, height = 620, fields, submit, onReady, request,
                        action, doneText, cluster, onDone, extra = '' }) {
    const panel = FloatingPanels.open({
      id, icon: ic, width, height, title: `${title} · ${cluster}`,
      bodyHtml: `<form class="of-form" autocomplete="off"><p class="form-hint">${esc(hint)}</p>
        <div class="of-fields">${fields}</div>${extra}
        <div class="bk-form-actions"><button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('bk.submitTip'))}">${icon('ok')} ${esc(submit)}</button></div>
        <div class="of-msg" role="status"></div></form>`,
    });
    const root = panel.el;
    const form = root.querySelector('.of-form');
    if (onReady) onReady(form, root);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = form.querySelector('button[type="submit"]');
      const msg = root.querySelector('.of-msg');
      let body;
      try { body = request(form); } catch (err) { msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`; return; }
      btn.disabled = true;
      try {
        const out = await post(cluster, typeof action === 'function' ? action(form) : action, body);
        follow(out.action_id, msg, doneText(body), (ok) => { btn.disabled = false; if (ok && onDone) onDone(); });
      } catch (err) {
        btn.disabled = false;
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      }
    });
    return root;
  }
  const val = (form, n) => form.querySelector(`[name="${n}"]`)?.value ?? '';
  const checked = (form, n) => !!form.querySelector(`[name="${n}"]`)?.checked;
  const show = (form, n, on) => { const el = form.querySelector(`[data-f="${n}"]`); if (el) el.hidden = !on; };

  function openClusterNetwork(cluster, onDone) {
    formWindow({
      id: `na-cn-${cluster}`, icon: 'switch', title: tr('na.newCn'), hint: tr('na.hint.cn'), height: 360, cluster, onDone,
      fields: field('name', tr('bk.f.name'), '<input name="name" required maxlength="12" pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?">', tr('na.t.cnName'))
        + field('description', tr('of.f.description'), '<input name="description">', tr('of.t.description')),
      submit: tr('of.create'), action: 'cn-create',
      request: (f) => ({ name: val(f, 'name').trim(), description: val(f, 'description').trim() }),
      doneText: (b) => tr('na.done.cn', { name: b.name }),
    });
  }

  function nodeNames(d) { return (d.nodes || []).filter(n => !n.witness).map(n => n.name); }

  function openConfig(cluster, cn, cfg, d, onDone) {
    const nodes = nodeNames(d);
    const sel = (cfg && cfg.selector) || {};
    const mode = !Object.keys(sel).length ? 'all'
      : (sel['kubernetes.io/hostname'] && Object.keys(sel).length === 1 ? 'node' : 'labels');
    const root = formWindow({
      id: `na-cfg-${cluster}-${cfg ? cfg.name : 'new'}`, icon: 'switch', width: 640, height: 700, cluster, onDone,
      title: cfg ? tr('na.editCfg', { name: cfg.name }) : tr('na.newCfgOn', { cn }), hint: tr('na.hint.cfg'),
      fields: field('name', tr('bk.f.name'), `<input name="name" required pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" maxlength="63"
            value="${esc(cfg ? cfg.name : `${cn}-${nodes.length > 1 ? 'all' : 'cfg'}`)}" ${cfg ? 'readonly' : ''}>`, tr('na.t.cfgName'))
        + field('description', tr('of.f.description'), `<input name="description" value="${esc(cfg ? cfg.description : '')}">`, tr('of.t.description'))
        + field('mode', tr('na.f.applies'), `<select name="mode">${opts([['all', tr('na.allNodes')], ['node', tr('na.oneNode')],
            ['labels', tr('na.byLabels')]], mode)}</select>`, tr('na.t.applies'))
        + field('node', tr('na.f.node'), `<select name="node">${opts(nodes, sel['kubernetes.io/hostname'] || nodes[0])}</select>`, tr('na.t.node'))
        + field('labels', tr('na.f.labels'), `<textarea name="labels" rows="2" placeholder="rack=a">${esc(mode === 'labels'
            ? Object.entries(sel).map(([k, v]) => `${k}=${v}`).join('\n') : '')}</textarea>`, tr('na.t.labels'))
        + `<div class="bk-field of-field" data-f="nics"><span>${esc(tr('na.f.nics'))}</span>
            <div class="na-nics tip" data-tip="${esc(tr('na.t.nics'))}"><p class="form-hint">${esc(tr('common.loading'))}</p></div></div>`
        + field('bond_mode', tr('na.f.bond'), `<select name="bond_mode">${opts(BONDS, cfg ? cfg.bond_mode : 'active-backup')}</select>`, tr('na.t.bond'))
        + field('miimon', 'miimon (ms)', `<input name="miimon" type="number" min="-1" value="${esc(cfg && cfg.miimon != null ? cfg.miimon : '')}" placeholder="100">`, tr('na.t.miimon'))
        + field('mtu', 'MTU', `<input name="mtu" type="number" min="576" max="9000" value="${esc(cfg ? cfg.mtu : 1500)}">`, tr('na.t.mtu')),
      submit: cfg ? tr('na.save') : tr('of.create'), action: cfg ? 'config-update' : 'config-create',
      request: (f) => {
        const nics = [...f.querySelectorAll('[name="nic"]:checked')].map(x => x.value);
        const labels = {};
        lines(val(f, 'labels')).forEach(l => { const [k, ...v] = l.split('='); labels[k.trim()] = v.join('=').trim(); });
        return { spec: { name: val(f, 'name').trim(), cluster_network: cn, description: val(f, 'description').trim(), nics,
                         bond_mode: val(f, 'bond_mode'), miimon: val(f, 'miimon'), mtu: val(f, 'mtu'),
                         nodes: { mode: val(f, 'mode'), node: val(f, 'node'), labels } } };
      },
      doneText: (b) => tr('na.done.cfg', { name: b.spec.name }),
      onReady: (form) => {
        const sync = () => {
          show(form, 'node', val(form, 'mode') === 'node');
          show(form, 'labels', val(form, 'mode') === 'labels');
        };
        // pas d'envoi avant la liste des cartes : sinon la demande partirait
        // sans carte (vu en réel sur une modification faite trop vite)
        const btn = form.querySelector('button[type="submit"]');
        const loadNics = async () => {
          btn.disabled = true;
          const target = val(form, 'mode') === 'node' ? [val(form, 'node')] : [];
          const q = `nodes=${enc(target.join(','))}&current=${enc(((cfg && cfg.nics) || []).join(','))}`;
          const r = await getJSON(`/api/net-admin/${enc(cluster)}/nics?${q}`);
          const box = form.querySelector('.na-nics');
          const on = new Set([...form.querySelectorAll('[name="nic"]:checked')].map(x => x.value).concat(cfg ? cfg.nics : []));
          const list = (r && r.nics) || [];
          box.innerHTML = list.length ? list.map(n => {
            const why = n.why === 'enslaved' ? tr('na.nic.enslaved', { nodes: n.enslaved_on.join(', ') })
              : n.why === 'down' ? tr('na.nic.down', { nodes: n.down_on.join(', ') }) : tr('na.nic.free');
            return `<label class="bk-check tip na-nic" data-tip="${esc(why)}"><input type="checkbox" name="nic" value="${esc(n.name)}"
                ${on.has(n.name) && n.usable ? 'checked' : ''} ${n.usable ? '' : 'disabled'}>
                <code>${esc(n.name)}</code> <span class="res-dim">${esc(n.usable ? Object.values(n.state).join('/') : why)}</span></label>`;
          }).join('') : `<p class="form-hint">${esc(tr('na.nic.none'))}</p>`;
          btn.disabled = false;
        };
        form.addEventListener('change', (e) => {
          sync();
          if (e.target.name === 'mode' || e.target.name === 'node') loadNics();
        });
        sync();
        loadNics();
      },
    });
    return root;
  }

  function openMigrate(cluster, cfg, d, onDone) {
    const from = d.cluster_networks.find(x => x.configs.some(c => c.name === cfg.name));
    const targets = d.cluster_networks.filter(x => !x.protected && x.name !== (from && from.name)).map(x => x.name);
    formWindow({
      id: `na-mig-${cluster}-${cfg.name}`, icon: 'migrate', title: tr('na.migrate', { name: cfg.name }), height: 380,
      hint: tr('na.hint.migrate', { from: from ? from.name : '?' }), cluster, onDone,
      fields: targets.length
        ? field('target', tr('na.f.target'), `<select name="target">${opts(targets, targets[0])}</select>`, tr('na.t.target'))
        : `<p class="res-error">${esc(tr('na.noTarget'))}</p>`,
      submit: tr('na.migrateBtn'), action: 'config-migrate',
      request: (f) => {
        if (!val(f, 'target')) throw new Error(tr('na.noTarget'));
        return { name: cfg.name, target: val(f, 'target') };
      },
      doneText: (b) => tr('na.done.migrate', { name: b.name, target: b.target }),
    });
  }

  function openSetting(cluster, s, d, onDone) {
    if (!s) return;
    const TITLE = { 'storage-network': tr('na.set.storage'), 'vm-migration-network': tr('na.set.migration'),
                    'rwx-network': tr('na.set.rwx') };
    const cns = d.cluster_networks.map(x => [x.name, x.name + (x.protected || x.ready ? '' : ` (${tr('na.notReady')})`),
                                             !(x.protected || x.ready)]);
    const mode = !s.enabled ? 'mgmt' : s.share_storage ? 'share' : 'dedicated';
    const modes = [['mgmt', tr('na.set.mgmt')], ['dedicated', tr('na.set.dedicated')]];
    if (s.name === 'rwx-network') modes.push(['share', tr('na.set.shared')]);
    formWindow({
      id: `na-set-${cluster}-${s.name}`, icon: 'network', title: TITLE[s.name], width: 600, height: 620, cluster, onDone,
      hint: s.name === 'storage-network' ? tr('na.hint.storage') : s.name === 'rwx-network' ? tr('na.hint.rwx') : tr('na.hint.migration'),
      fields: field('mode', tr('na.f.use'), `<select name="mode">${opts(modes, mode)}</select>`, tr('na.t.use'))
        + field('cluster_network', tr('of.f.clusterNetwork'), `<select name="cluster_network">${opts(cns, s.cluster_network || 'mgmt')}</select>`, tr('na.t.setCn'))
        + field('vlan', 'VLAN ID', `<input name="vlan" type="number" min="0" max="4094" value="${esc(s.vlan || '')}">`, tr('na.t.setVlan'))
        + field('range', tr('na.f.range'), `<input name="range" placeholder="10.100.0.0/24" value="${esc(s.range)}">`, tr('na.t.range'))
        + field('exclude', tr('na.f.exclude'), `<textarea name="exclude" rows="2" placeholder="10.100.0.0/28">${esc((s.exclude || []).join('\n'))}</textarea>`, tr('na.t.exclude'))
        + (s.name === 'storage-network' ? check('exclusive_vlan', tr('na.f.exclusive'), tr('na.t.exclusive'), s.exclusive_vlan) : ''),
      submit: tr('na.apply'), action: (f) => (val(f, 'mode') === 'mgmt' ? 'setting-clear' : 'setting-set'),
      request: (f) => {
        const m = val(f, 'mode');
        if (m === 'mgmt') return { name: s.name };
        if (m === 'share') return { name: s.name, spec: { share_storage: true } };
        return { name: s.name, spec: { cluster_network: val(f, 'cluster_network'), vlan: val(f, 'vlan'), range: val(f, 'range').trim(),
                                       exclude: lines(val(f, 'exclude')), exclusive_vlan: checked(f, 'exclusive_vlan') } };
      },
      doneText: () => tr('na.done.setting', { name: TITLE[s.name] }),
      onReady: (form) => {
        const sync = () => {
          const ded = val(form, 'mode') === 'dedicated';
          ['cluster_network', 'vlan', 'range', 'exclude', 'exclusive_vlan'].forEach(n => show(form, n, ded));
        };
        form.addEventListener('change', sync);
        sync();
      },
    });
  }

  async function openHostNet(cluster, row, onDone) {
    const d = (cur && cur.cluster === cluster && cur.data) || await getJSON(`/api/net-admin/${enc(cluster)}`) || {};
    const nodes = nodeNames(d);
    const cns = (d.cluster_networks || []).map(x => x.name);
    formWindow({
      id: `na-hnc-${cluster}-${row ? row.name : 'new'}`, icon: 'network', width: 620, height: 680, cluster, onDone,
      title: row ? tr('na.editHostNet', { name: row.name }) : tr('na.newHostNet'), hint: tr('na.hint.hostnet'),
      fields: field('name', tr('bk.f.name'), `<input name="name" required pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" maxlength="63"
            value="${esc(row ? row.name : '')}" ${row ? 'readonly' : ''}>`, tr('na.t.hnName'))
        + field('description', tr('of.f.description'), `<input name="description" value="${esc(row ? row.description : '')}">`, tr('of.t.description'))
        + field('cluster_network', tr('of.f.clusterNetwork'), `<select name="cluster_network" ${row ? 'disabled' : ''}>${opts(cns, row ? row.cluster_network : 'mgmt')}</select>`, tr('na.t.hnCn'))
        + field('vlan', 'VLAN ID', `<input name="vlan" type="number" min="1" max="4094" required value="${esc(row ? row.vlan : '')}" ${row ? 'readonly' : ''}>`, tr('na.t.hnVlan'))
        + field('mode', tr('na.f.mode'), `<select name="mode">${opts([['dhcp', 'DHCP'], ['static', tr('na.static')]], row ? row.mode : 'dhcp')}</select>`, tr('na.t.hnMode'))
        + `<div class="bk-field of-field" data-f="nodes"><span>${esc(tr('na.f.nodes'))}</span><div class="na-nodes tip" data-tip="${esc(tr('na.t.hnNodes'))}">
            ${nodes.map(n => `<label class="bk-check"><input type="checkbox" name="node" value="${esc(n)}" ${row && row.nodes.includes(n) ? 'checked' : ''}>
              <code>${esc(n)}</code> <input name="ip-${esc(n)}" class="na-ip" placeholder="10.0.20.11/24" value="${esc(row && row.ips[n] ? row.ips[n] : '')}"></label>`).join('')}
            </div></div>`
        + check('underlay', tr('na.f.underlay'), tr('na.t.underlay'), row && row.underlay),
      submit: row ? tr('na.save') : tr('of.create'), action: row ? 'hostnet-update' : 'hostnet-create',
      request: (f) => {
        const picked = [...f.querySelectorAll('[name="node"]:checked')].map(x => x.value);
        const ips = {};
        (picked.length ? picked : nodes).forEach(n => { const v = val(f, `ip-${n}`).trim(); if (v) ips[n] = v; });
        return { spec: { name: val(f, 'name').trim(), description: val(f, 'description').trim(),
                         cluster_network: row ? row.cluster_network : val(f, 'cluster_network'), vlan: val(f, 'vlan'),
                         mode: val(f, 'mode'), nodes: picked, ips: val(f, 'mode') === 'static' ? ips : {},
                         underlay: checked(f, 'underlay') } };
      },
      doneText: (b) => tr('na.done.hostnet', { name: b.spec.name }),
      onReady: (form) => {
        const sync = () => form.querySelectorAll('.na-ip').forEach(i => { i.hidden = val(form, 'mode') !== 'static'; });
        form.addEventListener('change', sync);
        sync();
      },
    });
  }

  function listenerRow(l = {}) {
    return `<div class="na-listener">
        <input name="l-name" placeholder="${esc(tr('bk.f.name'))}" value="${esc(l.name || '')}" class="tip" data-tip="${esc(tr('na.t.lName'))}">
        <input name="l-port" type="number" min="1" max="65535" placeholder="80" value="${esc(l.port || '')}" class="tip" data-tip="${esc(tr('na.t.lPort'))}">
        <select name="l-proto" class="tip" data-tip="${esc(tr('na.t.lProto'))}">${opts(['TCP', 'UDP'], l.protocol || 'TCP')}</select>
        <input name="l-back" type="number" min="1" max="65535" placeholder="8080" value="${esc(l.backendPort || '')}" class="tip" data-tip="${esc(tr('na.t.lBack'))}">
        <button type="button" class="btn btn-sm btn-secondary tip" data-l-del data-tip="${esc(tr('of.t.kvDel'))}">${icon('trash')}</button></div>`;
  }

  async function openLB(cluster, row, onDone) {
    const [pools, nsList] = await Promise.all([
      getJSON(`/api/cluster-objects/${enc(cluster)}/ippools`),
      getJSON(`/api/namespaces/${enc(cluster)}`)]);
    const names = ((pools && pools.items) || []).map(p => p.name);
    const nss = (Array.isArray(nsList) ? nsList : []).map(n => (typeof n === 'string' ? n : n.name)).filter(Boolean);
    const hc = (row && row.health) || {};
    formWindow({
      id: `na-lb-${cluster}-${row ? `${row.namespace}-${row.name}` : 'new'}`, icon: 'network', width: 660, height: 720, cluster, onDone,
      title: row ? tr('na.editLb', { name: row.name }) : tr('na.newLb'), hint: tr('na.hint.lb'),
      fields: field('namespace', tr('vms.namespace'), `<select name="namespace" ${row ? 'disabled' : ''}>${opts(nss.length ? nss : ['default'], row ? row.namespace : 'default')}</select>`, tr('of.t.namespace'))
        + field('name', tr('bk.f.name'), `<input name="name" required pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" maxlength="63" value="${esc(row ? row.name : '')}" ${row ? 'readonly' : ''}>`, tr('na.t.lbName'))
        + field('description', tr('of.f.description'), `<input name="description" value="${esc(row ? row.description : '')}">`, tr('of.t.description'))
        + field('ipam', 'IPAM', `<select name="ipam" ${row ? 'disabled' : ''}>${opts([['dhcp', tr('na.ipam.dhcp')], ['pool', tr('na.ipam.pool')]], row ? row.ipam : 'dhcp')}</select>`, tr('na.t.ipam'))
        + field('ip_pool', tr('na.f.pool'), `<select name="ip_pool">${opts([['', tr('na.poolAuto')], ...names], row ? row.ip_pool : '')}</select>`, tr('na.t.pool'))
        + `<div class="bk-field of-field" data-f="listeners"><span>${esc(tr('na.f.listeners'))}</span><div class="na-listeners">
            ${((row && row.listeners.length ? row.listeners : [{}])).map(listenerRow).join('')}</div>
            <button type="button" class="btn btn-sm btn-secondary tip" data-l-add data-tip="${esc(tr('na.t.lAdd'))}">${icon('add')} ${esc(tr('na.lAdd'))}</button></div>`
        + field('selector', tr('na.f.selector'), `<textarea name="selector" rows="3" placeholder="app=web">${esc(row
            ? Object.entries(row.selector || {}).map(([k, v]) => `${k}=${v.join(',')}`).join('\n') : '')}</textarea>`, tr('na.t.selector'))
        + check('hc', tr('na.f.health'), tr('na.t.health'), !!hc.port)
        + field('hc_port', tr('na.f.hcPort'), `<input name="hc_port" type="number" min="1" max="65535" value="${esc(hc.port || '')}">`, tr('na.t.hcPort'))
        + field('hc_period', tr('na.f.hcPeriod'), `<input name="hc_period" type="number" min="1" value="${esc(hc.periodSeconds || 5)}">`, tr('na.t.hcPeriod'))
        + field('hc_timeout', tr('na.f.hcTimeout'), `<input name="hc_timeout" type="number" min="1" value="${esc(hc.timeoutSeconds || 3)}">`, tr('na.t.hcTimeout'))
        + field('hc_success', tr('na.f.hcSuccess'), `<input name="hc_success" type="number" min="1" value="${esc(hc.successThreshold || 1)}">`, tr('na.t.hcSuccess'))
        + field('hc_failure', tr('na.f.hcFailure'), `<input name="hc_failure" type="number" min="1" value="${esc(hc.failureThreshold || 3)}">`, tr('na.t.hcFailure')),
      submit: row ? tr('na.save') : tr('of.create'), action: row ? 'lb-update' : 'lb-create',
      request: (f) => {
        const listeners = [...f.querySelectorAll('.na-listener')].map(l => ({
          name: l.querySelector('[name="l-name"]').value.trim(), port: l.querySelector('[name="l-port"]').value,
          protocol: l.querySelector('[name="l-proto"]').value, backend_port: l.querySelector('[name="l-back"]').value,
        })).filter(l => l.port || l.backend_port);
        const spec = { namespace: row ? row.namespace : val(f, 'namespace'), name: val(f, 'name').trim(),
                       description: val(f, 'description').trim(), ipam: row ? row.ipam : val(f, 'ipam'),
                       ip_pool: val(f, 'ip_pool'), listeners, selector: val(f, 'selector') };
        if (checked(f, 'hc')) {
          spec.health = { port: val(f, 'hc_port'), period: val(f, 'hc_period'), timeout: val(f, 'hc_timeout'),
                          success: val(f, 'hc_success'), failure: val(f, 'hc_failure') };
        }
        return { spec };
      },
      doneText: (b) => tr('na.done.lb', { name: b.spec.name }),
      onReady: (form) => {
        const sync = () => {
          show(form, 'ip_pool', (row ? row.ipam : val(form, 'ipam')) === 'pool');
          ['hc_port', 'hc_period', 'hc_timeout', 'hc_success', 'hc_failure'].forEach(n => show(form, n, checked(form, 'hc')));
        };
        form.addEventListener('change', sync);
        form.addEventListener('click', (e) => {
          if (e.target.closest('[data-l-add]')) form.querySelector('.na-listeners').insertAdjacentHTML('beforeend', listenerRow());
          const del = e.target.closest('[data-l-del]');
          if (del && form.querySelectorAll('.na-listener').length > 1) del.closest('.na-listener').remove();
        });
        sync();
      },
    });
  }

  function rangeRow(r = {}) {
    return `<div class="na-range">
        <input name="r-subnet" placeholder="10.0.20.0/24" value="${esc(r.subnet || '')}" class="tip" data-tip="${esc(tr('na.t.rSubnet'))}">
        <input name="r-start" placeholder="${esc(tr('na.f.rStart'))}" value="${esc(r.rangeStart || '')}" class="tip" data-tip="${esc(tr('na.t.rStart'))}">
        <input name="r-end" placeholder="${esc(tr('na.f.rEnd'))}" value="${esc(r.rangeEnd || '')}" class="tip" data-tip="${esc(tr('na.t.rEnd'))}">
        <input name="r-gw" placeholder="${esc(tr('of.f.gateway'))}" value="${esc(r.gateway || '')}" class="tip" data-tip="${esc(tr('na.t.rGw'))}">
        <button type="button" class="btn btn-sm btn-secondary tip" data-r-del data-tip="${esc(tr('of.t.kvDel'))}">${icon('trash')}</button></div>`;
  }

  async function openPool(cluster, row, onDone) {
    const fab = await getJSON(`/api/network-fabric/${enc(cluster)}`);
    const nets = ((fab && fab.networks) || []).map(n => (typeof n === 'string' ? n : n.ref || `${n.namespace}/${n.name}`)).filter(Boolean);
    const scope = ((row && row.scope) || [])[0] || {};
    const allocated = Object.entries((row && row.allocated) || {});
    const root = formWindow({
      id: `na-pool-${cluster}-${row ? row.name : 'new'}`, icon: 'network', width: 680, height: 660, cluster, onDone,
      title: row ? tr('na.editPool', { name: row.name }) : tr('na.newPool'), hint: tr('na.hint.pool'),
      fields: field('name', tr('bk.f.name'), `<input name="name" required pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" maxlength="63" value="${esc(row ? row.name : '')}" ${row ? 'readonly' : ''}>`, tr('na.t.poolName'))
        + field('description', tr('of.f.description'), `<input name="description" value="${esc(row ? row.description : '')}">`, tr('of.t.description'))
        + `<div class="bk-field of-field" data-f="ranges"><span>${esc(tr('na.f.ranges'))}</span><div class="na-ranges">
            ${((row && row.ranges.length ? row.ranges : [{}])).map(rangeRow).join('')}</div>
            <button type="button" class="btn btn-sm btn-secondary tip" data-r-add data-tip="${esc(tr('na.t.rAdd'))}">${icon('add')} ${esc(tr('na.rAdd'))}</button></div>`
        + field('network', tr('na.f.poolNet'), `<select name="network">${opts([['', tr('na.poolAnyNet')], ...nets], row ? row.network : '')}</select>`, tr('na.t.poolNet'))
        + field('priority', tr('na.f.priority'), `<input name="priority" type="number" min="0" value="${esc(row ? row.priority : 0)}">`, tr('na.t.priority'))
        + field('scope_ns', tr('na.f.scopeNs'), `<input name="scope_ns" value="${esc(scope.namespace || '*')}">`, tr('na.t.scopeNs')),
      extra: allocated.length ? `<div class="na-alloc"><div class="res-dim">${esc(tr('na.allocated'))}</div>
          ${allocated.map(([ip, lb]) => `<div class="na-alloc-row"><code>${esc(ip)}</code> <span class="res-dim">${esc(lb)}</span>
            <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-release="${esc(ip)}" data-tip="${esc(tr('na.t.release'))}">${esc(tr('na.release'))}</button></div>`).join('')}</div>` : '',
      submit: row ? tr('na.save') : tr('of.create'), action: row ? 'pool-update' : 'pool-create',
      request: (f) => ({ spec: {
        name: val(f, 'name').trim(), description: val(f, 'description').trim(), network: val(f, 'network'),
        priority: val(f, 'priority'), scope: [{ namespace: val(f, 'scope_ns').trim() || '*' }],
        ranges: [...f.querySelectorAll('.na-range')].map(r => ({
          subnet: r.querySelector('[name="r-subnet"]').value.trim(), start: r.querySelector('[name="r-start"]').value.trim(),
          end: r.querySelector('[name="r-end"]').value.trim(), gateway: r.querySelector('[name="r-gw"]').value.trim(),
        })).filter(r => r.subnet) } }),
      doneText: (b) => tr('na.done.pool', { name: b.spec.name }),
      onReady: (form, rootEl) => {
        form.addEventListener('click', async (e) => {
          if (e.target.closest('[data-r-add]')) form.querySelector('.na-ranges').insertAdjacentHTML('beforeend', rangeRow());
          const del = e.target.closest('[data-r-del]');
          if (del && form.querySelectorAll('.na-range').length > 1) del.closest('.na-range').remove();
          const rel = e.target.closest('[data-release]');
          if (rel) {
            e.preventDefault();
            if (!confirm(tr('na.confirm.release', { ip: rel.dataset.release }))) return;
            const msg = rootEl.querySelector('.of-msg');
            try {
              const out = await post(cluster, 'pool-release', { name: row.name, ip: rel.dataset.release });
              follow(out.action_id, msg, tr('na.done.release', { ip: rel.dataset.release }), (ok) => { if (ok && onDone) onDone(); });
            } catch (err) { msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`; }
          }
        });
      },
    });
    return root;
  }

  async function editVmNet(cluster, ns, name, onDone) {
    const r = await getJSON(`/api/net-admin/${enc(cluster)}/vmnet/${enc(ns)}/${enc(name)}`);
    if (!r || r.error) { alert((r && r.error) || tr('fabric.unreachable')); return; }
    if (r.type === 'OverlayNetwork') { alert(tr('na.overlayHint')); return; }
    const access = r.type === 'L2VlanNetwork' || r.type === 'UntaggedNetwork';
    const running = (r.running || []).length;
    const route = r.route || {};
    formWindow({
      id: `na-vmnet-${cluster}-${ns}-${name}`, icon: 'network', width: 600, height: 600, cluster, onDone,
      title: tr('na.editVmNet', { name: `${ns}/${name}` }),
      hint: running ? tr('na.hint.vmnetRunning', { list: r.running.join(', ') }) : tr('na.hint.vmnet'),
      fields: `<div class="res-dim na-pad">${esc(r.type)} · ${esc(r.cluster_network)}</div>`
        + field('description', tr('of.f.description'), `<input name="description" value="${esc(r.description)}">`, tr('of.t.description'))
        + (r.type === 'L2VlanNetwork' ? field('vlan', 'VLAN ID', `<input name="vlan" type="number" min="1" max="4094" value="${esc(r.vlan)}">`, tr('na.t.vmnetVlan')) : '')
        + (r.type === 'L2VlanTrunkNetwork' ? field('ranges', tr('na.f.trunk'), `<input name="ranges" value="${esc(rangesText(r.ranges))}" placeholder="100-199, 300">`, tr('na.t.trunk')) : '')
        + (access ? field('route_mode', tr('of.f.route'), `<select name="route_mode">${opts([['auto', tr('of.route.auto')], ['manual', tr('of.route.manual')]], route.mode)}</select>`, tr('of.t.route'))
          + field('dhcp_server', tr('na.f.dhcpServer'), `<input name="dhcp_server" value="${esc(route.dhcp_server)}" placeholder="192.168.10.2">`, tr('na.t.dhcpServer'))
          + field('cidr', 'CIDR', `<input name="cidr" value="${esc(route.cidr)}" placeholder="192.168.10.0/24">`, tr('of.t.cidr'))
          + field('gateway', tr('of.f.gateway'), `<input name="gateway" value="${esc(route.gateway)}" placeholder="192.168.10.1">`, tr('of.t.gateway'))
          + (route.connectivity ? `<div class="res-dim na-pad">${esc(tr('na.connectivity', { state: route.connectivity }))}</div>` : '') : ''),
      submit: tr('na.save'), action: 'vmnet-update',
      request: (f) => {
        const spec = { description: val(f, 'description').trim() };
        if (r.type === 'L2VlanNetwork') spec.vlan = val(f, 'vlan');
        if (r.type === 'L2VlanTrunkNetwork') spec.ranges = parseRanges(val(f, 'ranges'));
        if (access) Object.assign(spec, { route_mode: val(f, 'route_mode'), dhcp_server: val(f, 'dhcp_server').trim(),
                                          cidr: val(f, 'cidr').trim(), gateway: val(f, 'gateway').trim() });
        return { namespace: ns, name, spec };
      },
      doneText: () => tr('na.done.vmnet', { name }),
      onReady: (form) => {
        const sync = () => { show(form, 'cidr', val(form, 'route_mode') === 'manual'); show(form, 'gateway', val(form, 'route_mode') === 'manual'); };
        form.addEventListener('change', sync);
        sync();
      },
    });
  }

  // -- les listes (ResourceViews) --------------------------------------------------------
  function openNew(kind, cluster, onDone) {
    if (kind === 'lb') return openLB(cluster, null, onDone);
    if (kind === 'pool') return openPool(cluster, null, onDone);
    if (kind === 'hostnet') return openHostNet(cluster, null, onDone);
    return null;
  }

  async function listAction(kind, act, cluster, row, onDone) {
    if (act === 'edit') {
      if (kind === 'lb') return openLB(cluster, row, onDone);
      if (kind === 'pool') return openPool(cluster, row, onDone);
      if (kind === 'hostnet') return openHostNet(cluster, row, onDone);
    }
    if (act === 'delete') {
      const what = kind === 'lb' ? `${row.namespace}/${row.name}` : row.name;
      if (!confirm(tr('na.confirm.delete', { name: what }))) return null;
      const action = { lb: 'lb-delete', pool: 'pool-delete', hostnet: 'hostnet-delete' }[kind];
      const out = await post(cluster, action, kind === 'lb' ? { namespace: row.namespace, name: row.name } : { name: row.name });
      if (window.Dock && Dock.poll) Dock.poll();
      if (onDone) setTimeout(onDone, 3000);
      return out.action_id;
    }
    return null;
  }

  return { start, stop, refresh: load, openNew, listAction, editVmNet, parseRanges, rangesText,
           openClusterNetwork, openConfig, openMigrate, openSetting, openHostNet, openLB, openPool };
})();
window.NetAdmin = NetAdmin;
