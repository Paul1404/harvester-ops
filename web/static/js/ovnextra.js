/**
 * harvester-ops — la suite du menu kube-ovn de Harvester (v1.66.0)
 *
 * Trois fenêtres, ouvertes depuis les onglets Overlay et Underlay :
 *   - « NAT et Internet » : passerelles de VPC, IP externes, SNAT, DNAT ;
 *   - « Réseaux fournisseurs » : réseaux fournisseurs, VLANs, réseaux externes ;
 *   - « Politiques » : les NetworkPolicy qui visent des VMs.
 * En tête, la santé de kube-ovn (base OVN, CNI par nœud), dite avant tout
 * geste. Écritures par /api/kubeovn/<cluster>/extra/<type>, suivies au dock.
 */
const OvnExtra = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 13) => (window.Icons ? Icons.svg(n, { size }) : '');
  const REFRESH_MS = 5000;

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
        .replace('<select', `<select class="tip" data-tip="${esc(tip || '')}"`)}</label>`;
  const opts = (list, sel) => list.map(v => (Array.isArray(v) ? v : [v, v]))
    .map(([v, l, dis]) => `<option value="${esc(v)}" ${String(v) === String(sel) ? 'selected' : ''} ${dis ? 'disabled' : ''}>${esc(l)}</option>`).join('');
  const state = (ok, tipBad) => (ok ? badge('ok', tr('na.ready')) : badge('info', tr('na.pending'), tipBad));
  const del = (kind, name, dis, why) => `<button type="button" class="btn btn-sm btn-danger tip needs-admin" data-ox-del="${esc(kind)}"
      data-name="${esc(name)}" ${dis ? 'disabled' : ''} data-tip="${esc(dis ? why : tr('ox.t.delete'))}">${icon('trash')}</button>`;

  function follow(id, into, text, onDone) {
    if (window.Dock && Dock.poll) Dock.poll();
    if (window.VMActions && VMActions.follow) VMActions.follow(id, into, text, onDone);
    else if (into) into.textContent = tr('bk.started', { id });
  }

  function healthHtml(d) {
    if (!d) return `<p class="res-error">${esc(tr('fabric.unreachable'))}</p>`;
    if (d.kubeovn === false) return `<div class="sto-finding sev-warning"><div class="sto-finding-title">${esc(tr('ox.noKubeovn'))}</div></div>`;
    const h = d.health || {};
    if (h.healthy) return `<div class="ox-health ok">${icon('ok')} ${esc(tr('ox.healthy'))}</div>`;
    return `<div class="sto-finding sev-critical"><div class="sto-finding-title">${esc(tr('ox.unhealthy'))}</div>
      <ul>${(h.problems || []).map(p => `<li>${esc(p)}</li>`).join('')}</ul><div class="res-dim">${esc(tr('ox.unhealthyHint'))}</div></div>`;
  }

  // -- fenêtres à onglets ----------------------------------------------------------
  const WINS = {
    nat: { icon: 'network', tabs: ['gateways', 'eips', 'snats', 'dnats'], title: () => tr('ox.natTitle') },
    underlay: { icon: 'switch', tabs: ['providers', 'vlans', 'externals'], title: () => tr('ox.underlayTitle') },
  };
  const TAB_LABEL = {
    gateways: () => tr('ox.tab.gateways'), eips: () => tr('ox.tab.eips'), snats: () => tr('ox.tab.snats'),
    dnats: () => tr('ox.tab.dnats'), providers: () => tr('ox.tab.providers'), vlans: () => tr('ox.tab.vlans'),
    externals: () => tr('ox.tab.externals'),
  };
  const TAB_KIND = { gateways: 'gateway', eips: 'eip', snats: 'snat', dnats: 'dnat', providers: 'provider', vlans: 'vlan',
                     externals: 'external' };
  // clés en toutes lettres : le contrôle de parité i18n ne lit que des littéraux
  const TAB_TIP = {
    gateways: () => tr('ox.tip.gateways'), eips: () => tr('ox.tip.eips'), snats: () => tr('ox.tip.snats'),
    dnats: () => tr('ox.tip.dnats'), providers: () => tr('ox.tip.providers'), vlans: () => tr('ox.tip.vlans'),
    externals: () => tr('ox.tip.externals'),
  };
  const NONE = {
    gateways: () => tr('ox.none.gateways'), eips: () => tr('ox.none.eips'), snats: () => tr('ox.none.snats'),
    dnats: () => tr('ox.none.dnats'), providers: () => tr('ox.none.providers'), vlans: () => tr('ox.none.vlans'),
    externals: () => tr('ox.none.externals'),
  };
  const HINT = {
    gateways: () => tr('ox.hint.gateways'), eips: () => tr('ox.hint.eips'), snats: () => tr('ox.hint.snats'),
    dnats: () => tr('ox.hint.dnats'), providers: () => tr('ox.hint.providers'), vlans: () => tr('ox.hint.vlans'),
    externals: () => tr('ox.hint.externals'),
  };
  const DIR_LABEL = { ingress: () => tr('ox.f.ingress'), egress: () => tr('ox.f.egress') };
  const DIR_TIP = { ingress: () => tr('ox.t.ingress'), egress: () => tr('ox.t.egress') };

  function open(which, cluster, tab) {
    if (which === 'policies') return openPolicies(cluster);
    const def = WINS[which];
    if (!def || !cluster) return null;
    const id = `ox-${which}-${cluster}`;
    const panel = FloatingPanels.open({
      id, icon: def.icon, width: 1000, height: 640, title: `${def.title()} · ${cluster}`,
      bodyHtml: `<div class="ox-win">
          <div data-ox="health"></div>
          <div class="bk-bar"><div class="sub-tabs sub-tabs-inline ox-tabs" role="tablist">
            ${def.tabs.map(t => `<button type="button" class="sub-tab tip" role="tab" data-ox-tab="${t}" data-tip="${esc(TAB_TIP[t]())}">${esc(TAB_LABEL[t]())}</button>`).join('')}
          </div>
          <button type="button" class="btn btn-sm btn-primary tip needs-admin" data-ox="new" disabled data-tip="${esc(tr('ox.t.new'))}">${icon('add')} ${esc(tr('of.new'))}</button></div>
          <div class="hs-last" data-ox="last" role="status"></div>
          <div data-ox="form" hidden></div>
          <div data-ox="list"><p class="form-hint">${esc(tr('common.loading'))}</p></div></div>`,
    });
    const root = panel.el;
    if (root.oxWin) { if (tab) showTab(root.oxWin, tab); return root.oxWin; }
    const w = { which, cluster, root, tab: tab || def.tabs[0], data: null };
    root.oxWin = w;
    root.addEventListener('click', (e) => onClick(w, e));
    w.timer = setInterval(() => {
      if (!root.isConnected) { clearInterval(w.timer); return; }
      if (!root.querySelector('[data-ox="form"]').hidden) return;    // pas pendant une saisie
      load(w);
    }, REFRESH_MS);
    showTab(w, w.tab);
    return w;
  }

  function showTab(w, tab) {
    w.tab = tab;
    w.root.querySelectorAll('[data-ox-tab]').forEach(b => b.classList.toggle('active', b.dataset.oxTab === tab));
    closeForm(w);
    load(w);
  }

  async function load(w) {
    const d = await getJSON(`/api/kubeovn/${enc(w.cluster)}/extra`);
    w.data = d;
    // pas de formulaire avant le premier relevé : ses listes seraient vides
    // (vu en réel : un clic trop rapide ouvrait un formulaire sans carte)
    w.root.querySelector('[data-ox="new"]').disabled = !(d && d.kubeovn);
    w.root.querySelector('[data-ox="health"]').innerHTML = healthHtml(d);
    w.root.querySelector('[data-ox="list"]').innerHTML = d && d.kubeovn ? table(w.tab, d) : '';
  }

  function table(tab, d) {
    const rows = d[tab] || [];
    if (!rows.length) return `<p class="form-hint">${esc(NONE[tab]())}</p>`;
    const head = (cols) => `<table class="data-table"><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}<th></th></tr></thead><tbody>`;
    if (tab === 'providers') {
      return head([tr('res.col.name'), tr('ox.col.nic'), tr('ox.col.hosts'), tr('ox.tab.vlans'), tr('res.col.state')])
        + rows.map(r => `<tr><td><strong>${esc(r.name)}</strong> <span class="res-dim">br-${esc(r.name)}</span></td>
            <td><code>${esc(r.interface)}</code></td><td>${esc((r.ready_nodes || []).join(', ') || '–')}</td>
            <td>${esc(r.vlans.join(', ') || '–')}</td>
            <td>${r.errors.length ? badge('fail', tr('na.failed'), r.errors.join('; ')) : state(r.ready)}</td>
            <td class="tpl-acts">${del('provider', r.name, r.vlans.length, tr('ox.t.pnInUse'))}</td></tr>`).join('') + '</tbody></table>';
    }
    if (tab === 'vlans') {
      return head([tr('res.col.name'), 'ID', tr('ox.col.provider'), tr('ox.col.subnets'), tr('res.col.state')])
        + rows.map(r => `<tr><td><strong>${esc(r.name)}</strong></td><td>${esc(r.id === 0 ? `0 (${tr('na.untagged')})` : r.id)}</td>
            <td>${esc(r.provider)}</td><td>${esc(r.subnets.join(', ') || '–')}</td>
            <td>${r.conflict ? badge('fail', tr('ox.conflict'), tr('ox.t.conflict')) : badge('ok', tr('na.ready'))}</td>
            <td class="tpl-acts">${del('vlan', r.name, r.subnets.length, tr('ox.t.vlanInUse'))}</td></tr>`).join('') + '</tbody></table>';
    }
    if (tab === 'externals') {
      return head([tr('res.col.name'), tr('ox.col.lan'), tr('ox.col.free'), 'VLAN', tr('ox.col.next'), tr('res.col.state')])
        + rows.map(r => `<tr><td><strong>${esc(r.name)}</strong></td><td><code>${esc(r.cidr)}</code> <span class="res-dim">${esc(tr('ox.via', { gw: r.gateway }))}</span></td>
            <td><code>${esc(r.free || '–')}</code><div class="res-dim">${esc(tr('ox.using', { n: r.using ?? 0 }))}</div></td>
            <td>${esc(r.vlan)} <span class="res-dim">${esc(r.provider || '')}</span></td><td><code>${esc(r.next_eip || '–')}</code></td>
            <td>${state(r.ready, r.message)}</td>
            <td class="tpl-acts">${del('external', r.name, (r.using || 0) > 0, tr('ox.t.extInUse'))}</td></tr>`).join('') + '</tbody></table>';
    }
    if (tab === 'gateways') {
      return head([tr('res.col.name'), tr('ox.col.vpc'), tr('ox.col.lanIp'), tr('ox.col.external'), tr('ox.col.pod'), tr('res.col.state')])
        + rows.map(r => `<tr><td><strong>${esc(r.name)}</strong>${r.route ? ` <span class="res-dim tip" data-tip="${esc(tr('ox.t.route'))}">${icon('network', 11)}</span>` : ''}</td>
            <td>${esc(r.vpc)} / ${esc(r.subnet)}</td><td><code>${esc(r.lan_ip)}</code></td><td>${esc(r.external)}</td>
            <td>${r.pod ? `${esc(r.pod)} <span class="res-dim">${esc(r.node || '')}</span>
              <div class="res-dim">${esc(r.interfaces.map(i => `${i.name}: ${i.network} ${i.ips.join(' ')}`).join(' · '))}</div>` : '–'}</td>
            <td>${r.broken ? badge('fail', tr('ox.broken'), tr('ox.t.broken')) : state(r.ready, r.phase)}</td>
            <td class="tpl-acts">${r.broken ? `<button type="button" class="btn btn-sm btn-primary tip needs-admin" data-ox-repair="${esc(r.name)}"
                data-tip="${esc(tr('ox.t.repair'))}">${icon('tools')} ${esc(tr('ox.repair'))}</button>` : ''}
              ${del('gateway', r.name, r.eips.length, tr('ox.t.gwInUse'))}</td></tr>`).join('') + '</tbody></table>';
    }
    if (tab === 'eips') {
      return head([tr('res.col.name'), 'IP', tr('ox.col.gateway'), 'NAT', tr('ox.col.rules'), tr('res.col.state')])
        + rows.map(r => `<tr><td><strong>${esc(r.name)}</strong></td><td><code>${esc(r.ip || '–')}</code></td><td>${esc(r.gateway)}</td>
            <td>${esc(r.nat || '–')}</td><td>${esc(r.rules.join(', ') || '–')}</td><td>${state(r.ready)}</td>
            <td class="tpl-acts">${del('eip', r.name, r.rules.length, tr('ox.t.eipInUse'))}</td></tr>`).join('') + '</tbody></table>';
    }
    if (tab === 'snats') {
      return head([tr('res.col.name'), tr('ox.col.eip'), tr('ox.col.internal'), tr('res.col.state')])
        + rows.map(r => `<tr><td><strong>${esc(r.name)}</strong></td><td>${esc(r.eip)} <code>${esc(r.ip)}</code></td>
            <td><code>${esc(r.internal_cidr)}</code></td><td>${state(r.ready)}</td><td class="tpl-acts">${del('snat', r.name)}</td></tr>`).join('') + '</tbody></table>';
    }
    return head([tr('res.col.name'), tr('ox.col.from'), tr('ox.col.to'), tr('ox.col.proto'), tr('res.col.state')])
      + rows.map(r => `<tr><td><strong>${esc(r.name)}</strong></td><td><code>${esc(r.ip)}:${esc(r.external_port)}</code> <span class="res-dim">${esc(r.eip)}</span></td>
          <td><code>${esc(r.internal_ip)}:${esc(r.internal_port)}</code></td><td>${esc(r.protocol)}</td><td>${state(r.ready)}</td>
          <td class="tpl-acts">${del('dnat', r.name)}</td></tr>`).join('') + '</tbody></table>';
  }

  function closeForm(w) {
    const f = w.root.querySelector('[data-ox="form"]');
    f.hidden = true;
    f.innerHTML = '';
  }

  // -- formulaires ------------------------------------------------------------------
  function formFields(tab, d) {
    const vpcs = (d.vpcs || []).filter(v => v !== 'ovn-cluster');
    const ext = (d.externals || []).map(x => x.name);
    const gws = (d.gateways || []).map(g => g.name);
    const eips = (d.eips || []).filter(e => e.ready).map(e => [e.name, `${e.name} (${e.ip})`]);
    const name = (tip) => field('name', tr('bk.f.name'), '<input name="name" required pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" maxlength="63">', tip);
    if (tab === 'providers') {
      return field('name', tr('bk.f.name'), '<input name="name" required maxlength="12" pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?">', tr('ox.t.pnName'))
        + field('interface', tr('ox.f.nic'), `<select name="interface">${opts((d.nics || []).map(n => [n.name,
            n.taken_on.length ? `${n.name} (${tr('ox.bonded', { nodes: n.taken_on.join(', ') })})` : n.name, n.taken_on.length > 0]),
            ((d.nics || []).find(n => !n.taken_on.length) || {}).name)}</select>`, tr('ox.t.nic'))
        + `<div class="bk-field of-field" data-f="exclude"><span>${esc(tr('ox.f.exclude'))}</span><div class="na-nodes tip" data-tip="${esc(tr('ox.t.exclude'))}">
            ${(d.nodes || []).map(n => `<label class="bk-check"><input type="checkbox" name="exclude" value="${esc(n)}"> <code>${esc(n)}</code></label>`).join('')}</div></div>`;
    }
    if (tab === 'vlans') {
      return name(tr('ox.t.vlanName'))
        + field('id', 'VLAN ID', '<input name="id" type="number" min="0" max="4094" value="0" required>', tr('ox.t.vlanId'))
        + field('provider', tr('ox.col.provider'), `<select name="provider">${opts((d.providers || []).map(p => p.name))}</select>`, tr('ox.t.vlanProvider'));
    }
    if (tab === 'externals') {
      return name(tr('ox.t.extName'))
        + field('vlan', 'VLAN', `<select name="vlan">${opts((d.vlans || []).map(v => [v.name, `${v.name} (${v.provider}, ${v.id})`]))}</select>`, tr('ox.t.extVlan'))
        + field('cidr', tr('ox.f.lan'), '<input name="cidr" required placeholder="172.16.0.0/16">', tr('ox.t.lan'))
        + field('gateway', tr('of.f.gateway'), '<input name="gateway" required placeholder="172.16.0.1">', tr('ox.t.lanGw'))
        + field('free_start', tr('ox.f.freeStart'), '<input name="free_start" required placeholder="172.16.2.90">', tr('ox.t.free'))
        + field('free_end', tr('ox.f.freeEnd'), '<input name="free_end" required placeholder="172.16.2.95">', tr('ox.t.free'));
    }
    if (tab === 'gateways') {
      return name(tr('ox.t.gwName'))
        + field('vpc', tr('ox.col.vpc'), `<select name="vpc">${opts(vpcs)}</select>`, tr('ox.t.gwVpc'))
        + field('subnet', tr('ox.col.subnet'), '<select name="subnet"></select>', tr('ox.t.gwSubnet'))
        + field('lan_ip', tr('ox.col.lanIp'), '<input name="lan_ip" required>', tr('ox.t.lanIp'))
        + field('external', tr('ox.col.external'), `<select name="external">${opts(ext)}</select>`, tr('ox.t.gwExt'))
        + `<label class="bk-check tip" data-f="add_route" data-tip="${esc(tr('ox.t.addRoute'))}"><input type="checkbox" name="add_route" checked> <span>${esc(tr('ox.f.addRoute'))}</span></label>`;
    }
    if (tab === 'eips') {
      return name(tr('ox.t.eipName'))
        + field('gateway', tr('ox.col.gateway'), `<select name="gateway">${opts(gws)}</select>`, tr('ox.t.eipGw'))
        + field('external', tr('ox.col.external'), `<select name="external">${opts(ext)}</select>`, tr('ox.t.eipExt'))
        + field('ip', 'IP', '<input name="ip">', tr('ox.t.eipIp'));
    }
    if (tab === 'snats') {
      return name(tr('ox.t.ruleName'))
        + field('eip', tr('ox.col.eip'), `<select name="eip">${opts(eips)}</select>`, tr('ox.t.ruleEip'))
        + field('internal_cidr', tr('ox.col.internal'), '<input name="internal_cidr" required placeholder="10.20.0.0/24">', tr('ox.t.snatCidr'));
    }
    return name(tr('ox.t.ruleName'))
      + field('eip', tr('ox.col.eip'), `<select name="eip">${opts(eips)}</select>`, tr('ox.t.ruleEip'))
      + field('protocol', tr('ox.col.proto'), `<select name="protocol">${opts(['tcp', 'udp'], 'tcp')}</select>`, tr('ox.t.proto'))
      + field('external_port', tr('ox.f.extPort'), '<input name="external_port" type="number" min="1" max="65535" required>', tr('ox.t.extPort'))
      + field('internal_ip', tr('ox.f.intIp'), '<input name="internal_ip" required placeholder="10.20.0.10">', tr('ox.t.intIp'))
      + field('internal_port', tr('ox.f.intPort'), '<input name="internal_port" type="number" min="1" max="65535" required>', tr('ox.t.intPort'));
  }

  function lastHost(cidr) {
    const [ip, bits] = String(cidr || '').split('/');
    const p = ip.split('.').map(Number);
    if (p.length !== 4 || !bits) return '';
    const n = ((p[0] << 24) >>> 0) + (p[1] << 16) + (p[2] << 8) + p[3];
    const size = 2 ** (32 - Number(bits));
    const last = (n - (n % size)) + size - 2;
    return [(last >>> 24) & 255, (last >>> 16) & 255, (last >>> 8) & 255, last & 255].join('.');
  }

  function openForm(w) {
    const host = w.root.querySelector('[data-ox="form"]');
    const tab = w.tab;
    const d = w.data || {};
    host.hidden = false;
    host.innerHTML = `<form class="of-form ox-form" autocomplete="off"><p class="form-hint">${esc(HINT[tab]())}</p>
        <div class="of-fields">${formFields(tab, d)}</div>
        <div class="bk-form-actions">
          <button type="button" class="btn btn-sm btn-secondary" data-ox="cancel">${esc(tr('common.cancel'))}</button>
          <button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('bk.submitTip'))}">${icon('add')} ${esc(tr('of.create'))}</button></div>
        <div class="of-msg" role="status"></div></form>`;
    const form = host.querySelector('form');
    const val = (n) => form.querySelector(`[name="${n}"]`)?.value ?? '';
    const sync = () => {
      if (tab === 'gateways') {
        const subs = (d.tenant_subnets || []).filter(s => s.vpc === val('vpc'));
        const sel = form.querySelector('[name="subnet"]');
        const keep = sel.value;
        sel.innerHTML = opts(subs.map(s => [s.name, `${s.name} (${s.cidr})`]), keep);
        const s = subs.find(x => x.name === sel.value);
        const lan = form.querySelector('[name="lan_ip"]');
        if (s && !lan.dataset.touched) lan.value = lastHost(s.cidr);
      }
      if (tab === 'eips') {
        const g = (d.gateways || []).find(x => x.name === val('gateway'));
        const extSel = form.querySelector('[name="external"]');
        if (g && g.external && !extSel.dataset.touched) extSel.value = g.external;
        const x = (d.externals || []).find(e => e.name === extSel.value);
        const ip = form.querySelector('[name="ip"]');
        if (x && !ip.dataset.touched) ip.value = x.next_eip || '';
      }
      if (tab === 'snats') {
        const e = (d.eips || []).find(x => x.name === val('eip'));
        const g = e && (d.gateways || []).find(x => x.name === e.gateway);
        const s = g && (d.tenant_subnets || []).find(x => x.name === g.subnet);
        const c = form.querySelector('[name="internal_cidr"]');
        if (s && !c.dataset.touched) c.value = s.cidr;
      }
    };
    form.addEventListener('input', (e) => { if (e.target.name) e.target.dataset.touched = '1'; });
    form.addEventListener('change', (e) => { if (['external', 'lan_ip'].includes(e.target.name)) e.target.dataset.touched = '1'; sync(); });
    form.querySelector('[data-ox="cancel"]').addEventListener('click', () => closeForm(w));
    sync();
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const spec = {};
      for (const [k, v] of fd.entries()) if (k !== 'exclude' && k !== 'add_route') spec[k] = String(v).trim();
      if (tab === 'providers') spec.exclude_nodes = fd.getAll('exclude');
      if (tab === 'gateways') spec.add_route = !!form.querySelector('[name="add_route"]').checked;
      const btn = form.querySelector('button[type="submit"]');
      const msg = form.querySelector('.of-msg');
      btn.disabled = true;
      try {
        const out = await call('POST', `/api/kubeovn/${enc(w.cluster)}/extra/${TAB_KIND[tab]}`, { spec });
        follow(out.action_id, msg, tr('ox.done.create', { name: spec.name }), (ok) => {
          btn.disabled = false;
          if (ok) { closeForm(w); load(w); }
        });
      } catch (err) {
        btn.disabled = false;
        msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
      }
    });
  }

  async function remove(w, kind, name, ns) {
    if (!confirm(tr('na.confirm.delete', { name }))) return;
    const last = w.root.querySelector('[data-ox="last"]');
    try {
      const out = await call('DELETE', `/api/kubeovn/${enc(w.cluster)}/extra/${kind}/${enc(name)}${ns ? `?namespace=${enc(ns)}` : ''}`);
      follow(out.action_id, last, tr('ox.done.delete', { name }), () => load(w));
    } catch (err) {
      last.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
    }
  }

  async function repair(w, name) {
    const last = w.root.querySelector('[data-ox="last"]');
    try {
      const out = await call('POST', `/api/kubeovn/${enc(w.cluster)}/extra/repair`, { spec: { name } });
      follow(out.action_id, last, tr('ox.done.repair', { name }), () => load(w));
    } catch (err) {
      last.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
    }
  }

  function onClick(w, e) {
    const tabBtn = e.target.closest('[data-ox-tab]');
    if (tabBtn) { showTab(w, tabBtn.dataset.oxTab); return; }
    if (e.target.closest('[data-ox="new"]')) { if (w.data && w.data.kubeovn) openForm(w); return; }
    const d = e.target.closest('[data-ox-del]');
    if (d) remove(w, d.dataset.oxDel, d.dataset.name);
    const rep = e.target.closest('[data-ox-repair]');
    if (rep) repair(w, rep.dataset.oxRepair);
  }

  // -- politiques -------------------------------------------------------------------
  function ruleRow(dir, r = {}) {
    const p = (r.peers || [])[0] || { kind: 'any' };
    const value = p.kind === 'cidr' ? p.cidr : p.kind === 'namespace' ? p.namespace : p.kind === 'vms' ? (p.vms || []).join(', ') : '';
    const ports = (r.ports || []).map(x => `${x.port}/${String(x.protocol || 'TCP').toLowerCase()}`).join(', ');
    return `<div class="ox-rule" data-dir="${dir}">
        <select name="peer-kind" class="tip" data-tip="${esc(tr('ox.t.peerKind'))}">${opts([['any', tr('ox.peer.any')], ['cidr', tr('ox.peer.cidr')],
          ['namespace', tr('ox.peer.namespace')], ['vms', tr('ox.peer.vms')]], p.kind)}</select>
        <input name="peer-value" value="${esc(value)}" placeholder="172.16.0.0/16" class="tip" data-tip="${esc(tr('ox.t.peerValue'))}">
        <input name="ports" value="${esc(ports)}" placeholder="22/tcp, 80" class="tip" data-tip="${esc(tr('ox.t.ports'))}">
        <button type="button" class="btn btn-sm btn-secondary tip" data-rule-del data-tip="${esc(tr('of.t.kvDel'))}">${icon('trash')}</button></div>`;
  }

  function readRules(form, dir) {
    return [...form.querySelectorAll(`.ox-rule[data-dir="${dir}"]`)].map(row => {
      const kind = row.querySelector('[name="peer-kind"]').value;
      const v = row.querySelector('[name="peer-value"]').value.trim();
      const peers = kind === 'any' ? [] : kind === 'cidr' ? [{ kind, cidr: v }] : kind === 'namespace' ? [{ kind, namespace: v }]
        : [{ kind, vms: v.split(/[,\s]+/).filter(Boolean) }];
      const ports = row.querySelector('[name="ports"]').value.split(',').map(s => s.trim()).filter(Boolean)
        .map(s => { const [port, proto] = s.split('/'); return { port: Number(port), protocol: (proto || 'tcp').toUpperCase() }; });
      return { peers, ports };
    });
  }

  function openPolicies(cluster) {
    const id = `ox-policies-${cluster}`;
    const panel = FloatingPanels.open({
      id, icon: 'lock', width: 980, height: 640, title: `${tr('ox.polTitle')} · ${cluster}`,
      bodyHtml: `<div class="ox-win">
          <div class="bk-bar"><p class="form-hint">${esc(tr('ox.hint.policies'))}</p>
            <button type="button" class="btn btn-sm btn-primary tip needs-admin" data-pol="new" disabled data-tip="${esc(tr('ox.t.newPolicy'))}">${icon('add')} ${esc(tr('of.new'))}</button></div>
          <div class="hs-last" data-pol="last" role="status"></div>
          <div data-pol="form" hidden></div>
          <div data-pol="list"><p class="form-hint">${esc(tr('common.loading'))}</p></div></div>`,
    });
    const root = panel.el;
    if (root.polWin) return root.polWin;
    const w = { cluster, root, data: null };
    root.polWin = w;
    const loadPol = async () => {
      const d = await getJSON(`/api/kubeovn/${enc(cluster)}/policies`);
      w.data = d;
      root.querySelector('[data-pol="new"]').disabled = !d;
      const list = root.querySelector('[data-pol="list"]');
      const rows = (d && d.items) || [];
      list.innerHTML = !d ? `<p class="res-error">${esc(tr('fabric.unreachable'))}</p>` : !rows.length
        ? `<p class="form-hint">${esc(tr('ox.none.policies'))}</p>`
        : `<table class="data-table"><thead><tr><th>${esc(tr('res.col.name'))}</th><th>${esc(tr('ox.col.targets'))}</th>
            <th>${esc(tr('ox.col.in'))}</th><th>${esc(tr('ox.col.out'))}</th><th></th></tr></thead><tbody>
          ${rows.map(r => `<tr data-pol-ref="${esc(r.namespace)}/${esc(r.name)}"><td><strong>${esc(r.name)}</strong> <span class="res-dim">${esc(r.namespace)}</span>
              ${r.lax ? badge('info', 'lax', tr('ox.t.lax')) : ''}</td><td>${esc(r.target)}</td>
              ${['ingress', 'egress'].map(dir => `<td>${r.types.includes(dir === 'ingress' ? 'Ingress' : 'Egress')
                ? ((r.rules[dir] || []).map(x => `<div>${esc(x.peers.join(', '))} <span class="res-dim">${esc(x.ports.join(', '))}</span></div>`).join('')
                   || badge('fail', tr('ox.denyAll'))) : '<span class="res-dim">–</span>'}</td>`).join('')}
              <td class="tpl-acts">
                <button type="button" class="btn btn-sm btn-secondary tip needs-admin" data-pol-act="edit" ${r.editable ? '' : 'disabled'}
                  data-tip="${esc(r.editable ? tr('ox.t.editPolicy') : tr('ox.t.yamlOnly'))}">${icon('edit')}</button>
                <button type="button" class="btn btn-sm btn-secondary tip" data-pol-act="yaml" data-tip="${esc(tr('yw.act.yamlTip'))}">${icon('code')}</button>
                <button type="button" class="btn btn-sm btn-danger tip needs-admin" data-pol-act="delete" data-tip="${esc(tr('ox.t.delete'))}">${icon('trash')}</button></td></tr>`).join('')}
          </tbody></table>`;
    };
    const closePol = () => { const f = root.querySelector('[data-pol="form"]'); f.hidden = true; f.innerHTML = ''; };
    const formPol = (row) => {
      const d = w.data || { vms: {} };
      const namespaces = Object.keys(d.vms || {}).sort();
      const spec = (row && row.spec) || { namespace: namespaces[0] || 'default', vms: [], lax: true };
      const host = root.querySelector('[data-pol="form"]');
      host.hidden = false;
      const vmBoxes = (ns) => ((d.vms || {})[ns] || []).map(v => `<label class="bk-check"><input type="checkbox" name="vm" value="${esc(v)}"
          ${spec.vms.includes(v) ? 'checked' : ''}> <code>${esc(v)}</code></label>`).join('') || `<span class="res-dim">${esc(tr('ox.noVm'))}</span>`;
      host.innerHTML = `<form class="of-form ox-form" autocomplete="off"><p class="form-hint">${esc(tr('ox.hint.policyForm'))}</p>
          <div class="of-fields">
            ${field('name', tr('bk.f.name'), `<input name="name" required pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" value="${esc(row ? row.name : '')}" ${row ? 'readonly' : ''}>`, tr('ox.t.polName'))}
            ${field('namespace', tr('vms.namespace'), `<select name="namespace" ${row ? 'disabled' : ''}>${opts(namespaces.length ? namespaces : ['default'], spec.namespace)}</select>`, tr('ox.t.polNs'))}
            <div class="bk-field of-field" data-f="vms"><span>${esc(tr('ox.f.vms'))}</span><div class="na-nodes tip" data-tip="${esc(tr('ox.t.vms'))}" data-pol="vms">${vmBoxes(spec.namespace)}</div></div>
          </div>
          ${['ingress', 'egress'].map(dir => `<div class="ox-dir">
            <label class="bk-check tip" data-tip="${esc(DIR_TIP[dir]())}"><input type="checkbox" name="use-${dir}" ${spec[dir] ? 'checked' : ''}> <strong>${esc(DIR_LABEL[dir]())}</strong></label>
            <div class="ox-rules" data-dir="${dir}">${(spec[dir] || []).map(r => ruleRow(dir, r)).join('')}</div>
            <button type="button" class="btn btn-sm btn-secondary tip" data-rule-add="${dir}" data-tip="${esc(tr('ox.t.ruleAdd'))}">${icon('add')} ${esc(tr('ox.ruleAdd'))}</button></div>`).join('')}
          <label class="bk-check tip" data-tip="${esc(tr('ox.t.lax'))}"><input type="checkbox" name="lax" ${spec.lax ? 'checked' : ''}> <span>${esc(tr('ox.f.lax'))}</span></label>
          <div class="bk-form-actions"><button type="button" class="btn btn-sm btn-secondary" data-pol="cancel">${esc(tr('common.cancel'))}</button>
            <button type="submit" class="btn btn-sm btn-primary tip" data-tip="${esc(tr('bk.submitTip'))}">${icon('ok')} ${esc(row ? tr('na.save') : tr('of.create'))}</button></div>
          <div class="of-msg" role="status"></div></form>`;
      const form = host.querySelector('form');
      form.querySelector('[name="namespace"]').addEventListener('change', (e) => {
        form.querySelector('[data-pol="vms"]').innerHTML = vmBoxes(e.target.value);
      });
      form.addEventListener('click', (e) => {
        const add = e.target.closest('[data-rule-add]');
        if (add) form.querySelector(`.ox-rules[data-dir="${add.dataset.ruleAdd}"]`).insertAdjacentHTML('beforeend', ruleRow(add.dataset.ruleAdd));
        const dl = e.target.closest('[data-rule-del]');
        if (dl) dl.closest('.ox-rule').remove();
        if (e.target.closest('[data-pol="cancel"]')) closePol();
      });
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = { name: form.querySelector('[name="name"]').value.trim(),
                       namespace: row ? row.namespace : form.querySelector('[name="namespace"]').value,
                       vms: [...form.querySelectorAll('[name="vm"]:checked')].map(x => x.value),
                       lax: form.querySelector('[name="lax"]').checked };
        for (const dir of ['ingress', 'egress']) if (form.querySelector(`[name="use-${dir}"]`).checked) body[dir] = readRules(form, dir);
        const btn = form.querySelector('button[type="submit"]');
        const msg = form.querySelector('.of-msg');
        btn.disabled = true;
        try {
          const out = await call('POST', `/api/kubeovn/${enc(cluster)}/extra/policy`, { spec: body, update: !!row });
          follow(out.action_id, msg, tr('ox.done.policy', { name: body.name }), (ok) => { btn.disabled = false; if (ok) { closePol(); loadPol(); } });
        } catch (err) {
          btn.disabled = false;
          msg.innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
        }
      });
    };
    root.addEventListener('click', async (e) => {
      if (e.target.closest('[data-pol="new"]')) { formPol(null); return; }
      const b = e.target.closest('[data-pol-act]');
      if (!b) return;
      const [ns, name] = b.closest('[data-pol-ref]').dataset.polRef.split('/');
      const row = ((w.data && w.data.items) || []).find(r => r.namespace === ns && r.name === name);
      if (b.dataset.polAct === 'edit' && row) formPol(row);
      else if (b.dataset.polAct === 'yaml' && window.YamlWindow) YamlWindow.open(cluster, 'networkpolicy', ns, name, { onDone: loadPol });
      else if (b.dataset.polAct === 'delete') {
        if (!confirm(tr('na.confirm.delete', { name: `${ns}/${name}` }))) return;
        const last = root.querySelector('[data-pol="last"]');
        try {
          const out = await call('DELETE', `/api/kubeovn/${enc(cluster)}/extra/policy/${enc(name)}?namespace=${enc(ns)}`);
          follow(out.action_id, last, tr('ox.done.delete', { name }), loadPol);
        } catch (err) { last.innerHTML = `<span class="res-error">${esc(err.message)}</span>`; }
      }
    });
    w.timer = setInterval(() => {
      if (!root.isConnected) { clearInterval(w.timer); return; }
      if (root.querySelector('[data-pol="form"]').hidden) loadPol();
    }, 10000);
    loadPol();
    return w;
  }

  return { open, openPolicies, lastHost };
})();
window.OvnExtra = OvnExtra;
