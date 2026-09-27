/**
 * harvester-ops — les listes des sections de Harvester (v1.57.0)
 *
 * Storage > Images et Storage Classes, Security > Secrets et SSH Keys,
 * Add-ons : une liste lue par /api/cluster-objects/<cluster>/<type>, avec qui
 * s'en sert (les VMs, en lien vers leur fenêtre de réglages). Une seule liste
 * vit à la fois ; elle se relit toutes les 10 s tant qu'elle est à l'écran.
 * Un Secret n'arrive jamais avec ses valeurs : seulement le nom de ses clés.
 */
const ResourceViews = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 13) => (window.Icons ? Icons.svg(n, { size }) : '');
  const REFRESH_MS = 10000;

  let cur = null;      // { kind, cluster, host, timer, data, filter, showSystem, open:Set }

  // -- mise en forme --------------------------------------------------------
  function bytes(n) {
    if (n == null || n === '' || isNaN(n)) return '–';
    const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let v = Number(n), i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
  }
  function age(ts) {
    if (!ts) return '–';
    const d = (Date.now() - Date.parse(ts)) / 1000;
    if (isNaN(d)) return esc(ts);
    if (d < 3600) return tr('res.age.min', { n: Math.max(1, Math.round(d / 60)) });
    if (d < 86400) return tr('res.age.hours', { n: Math.round(d / 3600) });
    return tr('res.age.days', { n: Math.round(d / 86400) });
  }
  function vms(list) {
    if (!list || !list.length) return `<span class="res-dim">${esc(tr('res.unused'))}</span>`;
    const shown = list.slice(0, 3).map(ref => `<button type="button" class="res-chip tip" data-vm="${esc(ref)}"
        data-tip="${esc(tr('res.openVmTip'))}">${icon('vm', 11)} ${esc(ref.split('/')[1] || ref)}</button>`).join('');
    const more = list.length > 3 ? ` <span class="res-dim tip" data-tip="${esc(list.slice(3).join(', '))}">+${list.length - 3}</span>` : '';
    return shown + more;
  }
  const badge = (cls, text, tip) =>
    `<span class="badge ${cls}${tip ? ' tip' : ''}"${tip ? ` data-tip="${esc(tip)}"` : ''}>${esc(text)}</span>`;

  // -- colonnes et détails par type -----------------------------------------
  // Clés i18n en toutes lettres : le contrôle de parité ne lit que des littéraux.
  const IMG_STATE = {
    ready: () => badge('ok', tr('res.img.ready')),
    failed: (r) => badge('fail', tr('res.img.failed'), r.message),
    importing: (r) => badge('info', tr('res.img.importing', { pct: r.progress == null ? 0 : r.progress })),
  };
  const ADDON_STATE = {
    AddonDeploySuccessful: () => badge('ok', tr('res.addon.deployed')),
    AddonDisabled: () => badge('dim', tr('res.addon.disabled')),
    AddonEnabling: () => badge('info', tr('res.addon.enabling')),
    AddonDeploying: () => badge('info', tr('res.addon.enabling')),
    AddonDisabling: () => badge('info', tr('res.addon.disabling')),
    AddonUpdating: () => badge('info', tr('res.addon.updating')),
  };
  const ADDON_DESC = {
    'rancher-logging': () => tr('res.addonDesc.logging'),
    'rancher-monitoring': () => tr('res.addonDesc.monitoring'),
    'harvester-seeder': () => tr('res.addonDesc.seeder'),
    'nvidia-driver-toolkit': () => tr('res.addonDesc.nvidia'),
    'pcidevices-controller': () => tr('res.addonDesc.pcidevices'),
    'vm-import-controller': () => tr('res.addonDesc.vmimport'),
    'descheduler': () => tr('res.addonDesc.descheduler'),
    'kubeovn-operator': () => tr('res.addonDesc.kubeovn'),
  };
  const addonBusy = (r) => /ing$/.test(r.status || '');

  function backupState(r) {
    if (r.error) return badge('fail', tr('res.img.failed'), r.error);
    if (r.ready) return badge('ok', tr('res.img.ready'));
    return badge('info', r.progress != null ? tr('res.img.importing', { pct: r.progress }) : tr('bk.st.inProgress'));
  }

  // Un cron à cinq champs, dit en clair quand il suit un des modèles du
  // formulaire (toutes les heures, chaque jour, chaque semaine).
  function cronText(cron) {
    const f = String(cron || '').split(/\s+/);
    if (f.length !== 5) return cron || '';
    const [mi, h, dom, mon, dow] = f;
    const two = (x) => String(x).padStart(2, '0');
    if (/^\d+$/.test(mi) && h === '*' && dom === '*' && mon === '*' && dow === '*') return tr('bk.cron.hourly', { m: two(mi) });
    if (/^\d+$/.test(mi) && /^\d+$/.test(h) && dom === '*' && mon === '*') {
      if (dow === '*') return tr('bk.cron.daily', { t: `${two(h)}:${two(mi)}` });
      if (/^\d$/.test(dow)) {
        const day = new Date(2026, 8, 27 + Number(dow) % 7).toLocaleDateString(undefined, { weekday: 'long' });
        return tr('bk.cron.weekly', { day, t: `${two(h)}:${two(mi)}` });
      }
    }
    return cron;
  }

  const ACT_LABEL = {
    restore: () => tr('bk.act.restore'), delete: () => tr('bk.act.delete'),
    suspend: () => tr('bk.act.suspend'), resume: () => tr('bk.act.resume'),
    default: () => tr('of.act.default'), configure: () => tr('of.act.configure'),
    yaml: () => tr('yw.act.yaml'), more: () => tr('sta.act.more'), edit: () => tr('sta.img.edit'),
  };
  const ACT_TIP = {
    restore: () => tr('bk.act.restoreTip'), delete: () => tr('bk.act.deleteTip'),
    suspend: () => tr('bk.act.suspendTip'), resume: () => tr('bk.act.resumeTip'),
    default: () => tr('of.act.defaultTip'), configure: () => tr('of.act.configureTip'),
    yaml: () => tr('yw.act.yamlTip'), more: () => tr('sta.act.moreTip'), edit: () => tr('sec.t.edit'),
  };
  const act = (a, disabled, why, admin) => `<button type="button" class="btn btn-sm ${a === 'delete' ? 'btn-danger' : 'btn-secondary'} res-act tip${admin ? ' needs-admin' : ''}"
      data-act="${a}" data-tip="${esc(disabled && why ? why : ACT_TIP[a]())}" ${disabled ? 'disabled' : ''}>${esc(ACT_LABEL[a]())}</button>`;

  // v1.59.0 : les gestes des listes des sections (créer, supprimer, classe
  // par défaut, configurer un add-on), par ObjectForms
  const OBJ_KIND = { images: 'image', storageclasses: 'storageclass', sshkeys: 'sshkey', secrets: 'secret' };
  // v1.60.0 : le type YAML de chaque liste (« Edit YAML » de Harvester)
  const YAML_KIND = { images: 'image', storageclasses: 'storageclass', sshkeys: 'sshkey', secrets: 'secret',
                      addons: 'addon', schedules: 'schedule', vmbackups: 'vmbackup', vmsnapshots: 'vmbackup',
                      volsnaps: 'volsnap', loadbalancers: 'loadbalancer', ippools: 'ippool',
                      hostnetworks: 'hostnetwork' };
  // v1.65.0 : les listes du menu Networks, dont les fenêtres sont celles de NetAdmin
  const NET_KIND = { loadbalancers: 'lb', ippools: 'pool', hostnetworks: 'hostnet' };
  const YAML_ADMIN = new Set(['secrets', 'addons']);
  const yamlAct = (kind) => act('yaml', false, '', YAML_ADMIN.has(kind));
  function sectionAction(cur, a, row) {
    const done = (id) => { if (id) { if (window.Dock && Dock.poll) Dock.poll(); say(cur, esc(tr('bk.started', { id }))); setTimeout(() => load(cur), 3000); } };
    const fail = (err) => say(cur, `<span class="res-error">${esc(err.message)}</span>`);
    if (NET_KIND[cur.kind]) {
      if (window.NetAdmin) Promise.resolve(NetAdmin.listAction(NET_KIND[cur.kind], a, cur.cluster, row, () => load(cur))).then(done).catch(fail);
      return;
    }
    if (!window.ObjectForms) return;
    if (a === 'delete') ObjectForms.remove(OBJ_KIND[cur.kind], cur.cluster, row).then(done).catch(fail);
    else if (a === 'default') ObjectForms.setDefaultClass(cur.cluster, row.name).then(done).catch(fail);
    else if (a === 'configure') ObjectForms.openAddonValues(cur.cluster, row, () => load(cur));
    // v1.64.0 : modifier un secret (nouvelles valeurs) ou une clé SSH
    else if (a === 'edit' && cur.kind === 'secrets') ObjectForms.editSecret(cur.cluster, row, () => load(cur));
    else if (a === 'edit' && cur.kind === 'sshkeys') ObjectForms.editSshKey(cur.cluster, row, () => load(cur));
  }

  const VIEWS = {
    images: {
      create: 'image',
      cols: () => [tr('res.col.name'), tr('res.col.source'), tr('res.col.size'), tr('res.col.state'),
                   tr('res.col.class'), tr('res.col.usedBy'), tr('res.col.age'), ''],
      row: (r) => [
        `<strong>${esc(r.display_name)}</strong><div class="res-dim">${esc(r.namespace)}/${esc(r.name)}</div>`,
        esc(r.source_type || '–'),
        `${bytes(r.virtual_size)}<div class="res-dim">${esc(tr('res.img.file', { size: bytes(r.size) }))}</div>`,
        (IMG_STATE[r.state] || IMG_STATE.importing)(r),
        `<code>${esc(r.storage_class || '–')}</code>`, vms(r.used_by), age(r.created),
        act('more') + act('delete', r.volumes > 0, tr('of.t.inUse')) + yamlAct('images')],
      details: (r) => [[tr('res.d.url'), r.url ? `<code>${esc(r.url)}</code>` : '–'],
                       [tr('res.d.backend'), esc(r.backend || '–')],
                       [tr('res.d.volumes'), esc(r.volumes)],
                       ...(r.message ? [[tr('res.d.message'), esc(r.message)]] : [])],
      text: (r) => `${r.display_name} ${r.namespace}/${r.name} ${r.source_type} ${r.url || ''}`,
      sort: [(r) => r.display_name, (r) => r.source_type, (r) => r.virtual_size || 0, (r) => r.state,
             (r) => r.storage_class, (r) => (r.used_by || []).length, (r) => r.created, null]
    },
    storageclasses: {
      create: 'storageclass',
      cols: () => [tr('res.col.name'), tr('res.col.replicas'), tr('res.col.reclaim'), tr('res.col.binding'),
                   tr('res.col.expansion'), tr('res.col.volumes'), tr('res.col.age'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong> ${r.is_default ? badge('ok', tr('res.sc.default'), tr('res.sc.defaultTip')) : ''}
         ${r.image ? `<div class="res-dim tip" data-tip="${esc(tr('res.sc.imageTip'))}">${icon('cdrom', 11)} ${esc(r.image.display_name || r.image.ref)}</div>` : ''}`,
        esc(r.replicas || '–'), esc(r.reclaim_policy || '–'), esc(r.binding || '–'),
        r.expansion ? icon('ok') : icon('fail'), esc(r.volumes), age(r.created),
        (r.is_default || r.image ? '' : act('default', false, '', true)) + act('delete', r.volumes > 0 || !!r.image, tr('of.t.inUse'), true) + yamlAct('storageclasses')],
      details: (r) => [[tr('res.d.provisioner'), `<code>${esc(r.provisioner)}</code>`],
                       ...Object.entries(r.parameters || {}).map(([k, v]) => [k, `<code>${esc(v)}</code>`])],
      text: (r) => `${r.name} ${(r.image || {}).display_name || ''}`,
      sort: [(r) => r.name, (r) => Number(r.replicas) || 0, (r) => r.reclaim_policy, (r) => r.binding,
             (r) => (r.expansion ? 1 : 0), (r) => r.volumes, (r) => r.created, null]
    },
    sshkeys: {
      create: 'sshkey',
      cols: () => [tr('res.col.name'), tr('res.col.fingerprint'), tr('res.col.state'), tr('res.col.usedBy'), tr('res.col.age'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong><div class="res-dim">${esc(r.namespace)}</div>`,
        `<code>${esc(r.fingerprint || '–')}</code>`,
        r.validated ? badge('ok', tr('res.key.valid')) : badge('warn', tr('res.key.pending')),
        vms(r.used_by), age(r.created), act('edit') + act('delete') + yamlAct('sshkeys')],
      details: (r) => [[tr('res.d.publicKey'), `<code class="res-wrap">${esc(r.public_key || '')}</code>`]],
      text: (r) => `${r.namespace}/${r.name} ${r.fingerprint || ''}`,
      sort: [(r) => r.name, (r) => r.fingerprint, (r) => (r.validated ? 1 : 0), (r) => (r.used_by || []).length,
             (r) => r.created, null]
    },
    secrets: {
      create: 'secret',
      cols: () => [tr('res.col.name'), tr('res.col.type'), tr('res.col.keys'), tr('res.col.usedBy'), tr('res.col.age'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong><div class="res-dim">${esc(r.namespace)}</div>
         ${r.cloud_init ? badge('info', tr('res.sec.cloudinit'), tr('res.sec.cloudinitTip')) : ''}`,
        `<code>${esc(r.type)}</code>`,
        (r.keys || []).map(k => `<code class="res-key">${esc(k)}</code>`).join(' ') || '–',
        vms(r.used_by), age(r.created),
        (r.system ? '' : act('edit', false, '', true))
        + act('delete', (r.used_by || []).length > 0 || r.system, r.system ? tr('of.t.system') : tr('of.t.inUse')) + yamlAct('secrets')],
      details: null,
      text: (r) => `${r.namespace}/${r.name} ${r.type} ${(r.keys || []).join(' ')}`,
      sort: [(r) => `${r.name} ${r.namespace}`, (r) => r.type, (r) => (r.keys || []).length,
             (r) => (r.used_by || []).length, (r) => r.created, null]
    },
    addons: {
      cols: () => [tr('res.col.name'), tr('res.col.chart'), tr('res.col.state'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong><div class="res-dim">${esc(r.namespace)}</div>
         <div class="res-desc">${esc(ADDON_DESC[r.name] ? ADDON_DESC[r.name]() : '')}</div>`,
        `<code>${esc(r.chart)}</code><div class="res-dim">${esc(r.version || '')}</div>`,
        (/Failed/.test(r.status) || r.message ? badge('fail', tr('res.addon.failed'), r.message)
          : (ADDON_STATE[r.status] || (() => badge('dim', r.status || '?')))()),
        `<button type="button" class="btn btn-sm ${r.enabled ? 'btn-secondary' : 'btn-primary'} tip needs-admin"
            data-addon="${esc(r.namespace)}/${esc(r.name)}" data-enable="${r.enabled ? '0' : '1'}"
            data-tip="${esc(r.enabled ? tr('res.addon.disableTip') : tr('res.addon.enableTip'))}"
            ${addonBusy(r) ? 'disabled' : ''}>${esc(r.enabled ? tr('res.addon.disable') : tr('res.addon.enable'))}</button>
         ${act('configure', addonBusy(r), '', true)}${yamlAct('addons')}`],
      details: null,
      text: (r) => `${r.namespace}/${r.name} ${r.chart}`,
      sort: [(r) => r.name, (r) => r.chart, (r) => r.status, null],
    },
    // v1.65.0 : équilibreurs, pools d'adresses, réseaux d'hôte (menu Networks)
    loadbalancers: {
      create: 'lb',
      cols: () => [tr('res.col.name'), tr('na.col.address'), 'IPAM', tr('na.col.listeners'), tr('na.col.backends'),
                   tr('res.col.state'), tr('res.col.age'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong><div class="res-dim">${esc(r.namespace)}</div>${r.description ? `<div class="res-desc">${esc(r.description)}</div>` : ''}`,
        r.address ? `<code>${esc(r.address)}</code>` : '–',
        esc(r.ipam === 'dhcp' ? 'DHCP' : (r.ip_pool || tr('na.ipam.pool'))),
        (r.listeners || []).map(l => `<code class="res-key tip" data-tip="${esc(tr('na.t.listener'))}">${esc(l.protocol)} ${esc(l.port)}:${esc(l.backendPort)}</code>`).join(' ') || '–',
        (r.backends || []).map(ip => `<code class="res-key">${esc(ip)}</code>`).join(' ') || `<span class="res-dim">${esc(tr('na.noBackend'))}</span>`,
        r.ready ? badge('ok', tr('na.ready')) : badge(r.ready === false ? 'warn' : 'info', r.ready === false ? tr('na.notReady') : tr('na.pending'), r.message),
        age(r.created), act('edit', false, '', true) + act('delete', false, '', true) + yamlAct('loadbalancers')],
      details: (r) => [[tr('na.f.selector'), Object.entries(r.selector || {}).map(([k, v]) => `<code>${esc(k)} in (${esc(v.join(', '))})</code>`).join(' ') || '–'],
                       [tr('na.f.health'), r.health && r.health.port ? esc(tr('na.hcText', { port: r.health.port, period: r.health.periodSeconds || 5 })) : '–']],
      text: (r) => `${r.namespace}/${r.name} ${r.address || ''} ${r.ip_pool || ''} ${(r.backends || []).join(' ')}`,
      sort: [(r) => r.name, (r) => r.address, (r) => r.ipam, (r) => (r.listeners || []).length, (r) => (r.backends || []).length,
             (r) => (r.ready ? 1 : 0), (r) => r.created, null],
    },
    ippools: {
      create: 'pool',
      cols: () => [tr('res.col.name'), tr('na.f.ranges'), tr('na.f.poolNet'), tr('na.col.scope'), tr('na.col.available'),
                   tr('res.col.state'), tr('res.col.age'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong> ${r.global ? badge('info', tr('na.global'), tr('na.t.global')) : ''}${r.description ? `<div class="res-desc">${esc(r.description)}</div>` : ''}`,
        (r.ranges || []).map(x => `<code class="res-key">${esc(x.subnet)}${x.rangeStart ? ` ${esc(x.rangeStart)}-${esc(x.rangeEnd || '')}` : ''}</code>`).join(' '),
        r.network ? `<code>${esc(r.network)}</code>` : `<span class="res-dim">${esc(tr('na.poolAnyNet'))}</span>`,
        esc((r.scope || []).map(x => Object.entries(x).map(([k, v]) => `${k}=${v}`).join(' ')).join('; ') || '–'),
        `${esc(r.available ?? '–')} / ${esc(r.total ?? '–')}`,
        r.ready ? badge('ok', tr('na.ready')) : badge('info', tr('na.pending'), r.message),
        age(r.created),
        act('edit', false, '', true) + act('delete', Object.keys(r.allocated || {}).length > 0, tr('na.t.poolInUse'), true) + yamlAct('ippools')],
      details: (r) => [[tr('na.allocated'), Object.entries(r.allocated || {}).map(([ip, lb]) => `<code>${esc(ip)}</code> ${esc(lb)}`).join('<br>') || '–']],
      text: (r) => `${r.name} ${(r.ranges || []).map(x => x.subnet).join(' ')} ${r.network || ''}`,
      sort: [(r) => r.name, (r) => (r.ranges[0] || {}).subnet, (r) => r.network, null, (r) => r.available || 0,
             (r) => (r.ready ? 1 : 0), (r) => r.created, null],
    },
    hostnetworks: {
      create: 'hostnet',
      cols: () => [tr('res.col.name'), tr('na.col.interface'), tr('na.f.mode'), tr('na.col.addresses'), tr('na.f.underlay'),
                   tr('res.col.state'), tr('res.col.age'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong>${r.description ? `<div class="res-desc">${esc(r.description)}</div>` : ''}`,
        `<code>${esc(r.interface)}</code>`,
        esc(r.mode === 'static' ? tr('na.static') : 'DHCP'),
        Object.entries(r.ips || {}).map(([n, ip]) => `<div><code>${esc(ip)}</code> <span class="res-dim">${esc(n)}</span></div>`).join('')
          || `<span class="res-dim">${esc((r.nodes || []).join(', ') || tr('na.allNodes'))}</span>`,
        r.underlay ? icon('ok') : '–',
        (() => {
          const bad = Object.entries(r.node_status || {}).filter(([, s]) => s.ready === false);
          if (bad.length) return badge('fail', tr('na.failed'), bad.map(([n, s]) => `${n}: ${s.message}`).join('; '));
          return r.ready ? badge('ok', tr('na.ready')) : badge('info', tr('na.pending'));
        })(),
        age(r.created),
        act('edit', false, '', true) + act('delete', r.underlay, tr('na.t.underlayOn'), true) + yamlAct('hostnetworks')],
      details: (r) => Object.entries(r.node_status || {}).map(([n, st]) => [n, st.ready ? badge('ok', tr('na.ready')) : esc(st.message || tr('na.pending'))]),
      text: (r) => `${r.name} ${r.interface} ${Object.values(r.ips || {}).join(' ')}`,
      sort: [(r) => r.name, (r) => r.interface, (r) => r.mode, null, (r) => (r.underlay ? 1 : 0), (r) => (r.ready ? 1 : 0),
             (r) => r.created, null],
    },
    // v1.58.0 : la fenêtre Backups
    schedules: {
      cols: () => [tr('res.col.name'), tr('bk.col.vm'), tr('res.col.type'), tr('bk.col.when'), tr('bk.col.keep'),
                   tr('bk.col.last'), tr('res.col.state'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong><div class="res-dim">${esc(r.namespace)}</div>`,
        vms([`${r.namespace}/${r.vm}`]),
        esc(r.type === 'snapshot' ? tr('bk.f.typeSnapshot') : tr('bk.f.typeBackup')),
        `<span class="tip" data-tip="${esc(r.cron)}">${esc(cronText(r.cron))}</span>`,
        `${esc(r.kept)} / ${esc(r.retain)}`, r.last ? age(r.last) : '–',
        r.suspended ? badge('dim', tr('bk.st.suspended'))
          : (r.failures ? badge('warn', tr('bk.st.failures', { n: r.failures }), tr('bk.st.failuresTip', { max: r.max_failure }))
            : badge('ok', tr('bk.st.active'))),
        act(r.suspended ? 'resume' : 'suspend') + act('delete') + yamlAct('schedules')],
      details: null,
      text: (r) => `${r.namespace}/${r.name} ${r.vm} ${r.cron}`,
      sort: [(r) => r.name, (r) => r.vm, (r) => r.type, (r) => r.cron, (r) => r.kept, (r) => r.last,
             (r) => (r.suspended ? 1 : 0), null],
    },
    vmbackups: {
      cols: () => [tr('res.col.name'), tr('bk.col.vm'), tr('res.col.state'), tr('res.col.size'), tr('bk.col.target'),
                   tr('res.col.age'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong><div class="res-dim">${esc(r.namespace)}${r.schedule ? ` · ${esc(tr('bk.fromSchedule', { name: r.schedule }))}` : ''}</div>`,
        vms([`${r.namespace}/${r.vm}`]), backupState(r), bytes(r.size),
        `<span class="res-dim">${esc(r.target || '–')}</span>`, age(r.created),
        act('restore', !r.ready) + act('delete') + yamlAct('vmbackups')],
      details: null,
      text: (r) => `${r.namespace}/${r.name} ${r.vm} ${r.schedule || ''}`,
      sort: [(r) => r.name, (r) => r.vm, (r) => (r.ready ? 2 : r.error ? 0 : 1), (r) => r.size || 0, (r) => r.target,
             (r) => r.created, null],
    },
    vmsnapshots: {
      cols: () => [tr('res.col.name'), tr('bk.col.vm'), tr('res.col.state'), tr('res.col.age'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong><div class="res-dim">${esc(r.namespace)}${r.schedule ? ` · ${esc(tr('bk.fromSchedule', { name: r.schedule }))}` : ''}</div>`,
        vms([`${r.namespace}/${r.vm}`]), backupState(r), age(r.created),
        act('restore', !r.ready) + act('delete') + yamlAct('vmsnapshots')],
      details: null,
      text: (r) => `${r.namespace}/${r.name} ${r.vm} ${r.schedule || ''}`,
      sort: [(r) => r.name, (r) => r.vm, (r) => (r.ready ? 2 : r.error ? 0 : 1), (r) => r.created, null],
    },
    volsnaps: {
      cols: () => [tr('res.col.name'), tr('bk.col.volume'), tr('res.col.size'), tr('res.col.state'), tr('bk.col.owner'),
                   tr('res.col.age'), ''],
      row: (r) => [
        `<strong>${esc(r.name)}</strong><div class="res-dim">${esc(r.namespace)}</div>`,
        `<code>${esc(r.pvc || '–')}</code>`, esc(r.size || '–'),
        r.error ? badge('fail', tr('res.img.failed'), r.error) : (r.ready ? badge('ok', tr('res.img.ready')) : badge('info', tr('bk.st.inProgress'))),
        r.owner ? `<span class="res-dim tip" data-tip="${esc(tr('bk.ownerTip'))}">${icon('snapshot', 11)} ${esc(r.owner)}</span>` : '–',
        age(r.created), act('restore', !r.ready) + act('delete', !!r.owner, tr('bk.ownerTip')) + yamlAct('volsnaps')],
      details: null,
      text: (r) => `${r.namespace}/${r.name} ${r.pvc || ''} ${r.owner || ''}`,
      sort: [(r) => r.name, (r) => r.pvc, (r) => r.size, (r) => (r.ready ? 1 : 0), (r) => r.owner, (r) => r.created, null]
    },
  };

  // -- rendu ------------------------------------------------------------------
  function shell(cur) {
    const k = cur.kind;
    cur.host.innerHTML = `
      <div class="card res-card" data-kind="${esc(k)}">
        <div class="res-tools">
          <input type="search" class="res-filter tip" data-tip="${esc(tr('res.filterTip'))}"
                 placeholder="${esc(tr('res.filter'))}" value="${esc(cur.filter)}">
          <span class="res-count"></span>
          ${k === 'secrets' ? `<label class="res-system tip" data-tip="${esc(tr('res.sec.systemTip'))}">
              <input type="checkbox" class="res-system-box" ${cur.showSystem ? 'checked' : ''}> <span>${esc(tr('res.sec.system'))}</span></label>` : ''}
          <button type="button" class="btn btn-sm btn-secondary res-refresh tip" data-tip="${esc(tr('res.refreshTip'))}">${icon('refresh')} ${esc(tr('overview.refresh'))}</button>
          ${VIEWS[k].create && !(cur.opts && cur.opts.onAction) ? `<button type="button" class="btn btn-sm btn-primary res-new tip${k === 'storageclasses' || NET_KIND[k] ? ' needs-admin' : ''}"
              data-tip="${esc(tr('of.t.new'))}">${icon('add')} ${esc(tr('of.new'))}</button>` : ''}
          ${k === 'images' && !(cur.opts && cur.opts.onAction) ? `<button type="button" class="btn btn-sm btn-secondary res-upload tip"
              data-tip="${esc(tr('sta.up.tip'))}">${icon('upload')} ${esc(tr('sta.up.button'))}</button>` : ''}
        </div>
        <div class="res-feedback"></div>
        <div class="res-body"><p class="form-hint">${esc(tr('common.loading'))}</p></div>
      </div>`;
    const card = cur.host.querySelector('.res-card');
    card.querySelector('.res-filter').addEventListener('input', (e) => { cur.filter = e.target.value; render(cur); });
    card.querySelector('.res-refresh').addEventListener('click', () => load(cur));
    card.querySelector('.res-system-box')?.addEventListener('change', (e) => { cur.showSystem = e.target.checked; load(cur); });
    card.addEventListener('click', (e) => onClick(cur, e));
    card.addEventListener('keydown', (e) => {
      const head = e.target.closest('th[data-sort]');
      if (head && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); sortBy(cur, Number(head.dataset.sort)); }
    });
  }

  function render(cur) {
    if (!cur || !cur.host.isConnected) return;
    const body = cur.host.querySelector('.res-body');
    const count = cur.host.querySelector('.res-count');
    const d = cur.data;
    if (!d) return;
    if (d.unreachable) {
      body.innerHTML = `<div class="sto-finding sev-critical"><div class="sto-finding-title">${esc(tr('fabric.unreachable'))}</div></div>`;
      count.textContent = '';
      return;
    }
    if (d.error) {
      body.innerHTML = `<div class="sto-finding sev-critical"><div class="sto-finding-title">${esc(d.error)}</div>
        ${d.hint ? `<div class="res-dim">${esc(d.hint)}</div>` : ''}</div>`;
      count.textContent = '';
      return;
    }
    const v = VIEWS[cur.kind];
    const words = cur.filter.toLowerCase().split(/\s+/).filter(Boolean);
    const rows = (d.items || []).filter(r => !words.length || words.every(w => v.text(r).toLowerCase().includes(w)));
    count.textContent = cur.kind === 'secrets' && d.system_hidden
      ? tr('res.countHidden', { n: rows.length, hidden: d.system_hidden })
      : tr('res.count', { n: rows.length });
    if (!rows.length) {
      body.innerHTML = `<p class="form-hint">${esc((d.items || []).length ? tr('res.noMatch') : tr('res.empty'))}</p>`;
      return;
    }
    const cols = v.cols();
    // v1.57.0 : tri par colonne (clic sur l'en-tête, second clic : sens inverse)
    const srt = cur.sort;
    if (srt && v.sort[srt.col]) {
      const key = v.sort[srt.col];
      const val = (r) => { const x = key(r); return x == null ? '' : x; };
      rows.sort((a, b) => {
        const x = val(a), y = val(b);
        const c = (typeof x === 'number' && typeof y === 'number') ? x - y
          : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: 'base' });
        return c * srt.dir;
      });
    }
    const th = (c, i) => {
      if (!c || !v.sort[i]) return `<th>${esc(c)}</th>`;
      const on = srt && srt.col === i;
      const arrow = on ? icon(srt.dir > 0 ? 'arrowUp' : 'arrowDown', 11) : '';
      return `<th class="res-sortable tip${on ? ' is-sorted' : ''}" data-sort="${i}" role="button" tabindex="0"
        aria-sort="${on ? (srt.dir > 0 ? 'ascending' : 'descending') : 'none'}"
        data-tip="${esc(tr('res.sortTip'))}">${esc(c)} ${arrow}</th>`;
    };
    cur.rows = new Map(rows.map(r => [`${r.namespace || ''}/${r.name}`, r]));
    body.innerHTML = `<table class="data-table res-table"><thead><tr>${cols.map(th).join('')}</tr></thead><tbody>
      ${rows.map(r => {
        const id = `${r.namespace || ''}/${r.name}`;
        const open = v.details && cur.open.has(id);
        return `<tr class="${v.details ? 'res-row' : ''}${open ? ' is-open' : ''}" data-id="${esc(id)}"
            ${v.details ? `title="${esc(tr('res.detailsTip'))}"` : ''}>${v.row(r).map(c => `<td>${c}</td>`).join('')}</tr>
          ${open ? `<tr class="res-details"><td colspan="${cols.length}"><dl>${v.details(r).map(([k, val]) =>
            `<dt>${esc(k)}</dt><dd>${val}</dd>`).join('')}</dl></td></tr>` : ''}`;
      }).join('')}</tbody></table>`;
  }

  const SORT_KEY = (kind) => `harvester_ops_res_sort_${kind}`;

  function sortBy(cur, col) {
    const same = cur.sort && cur.sort.col === col;
    cur.sort = { col, dir: same ? -cur.sort.dir : 1 };
    try { localStorage.setItem(SORT_KEY(cur.kind), JSON.stringify(cur.sort)); } catch {}
    render(cur);
  }

  function onClick(cur, e) {
    const head = e.target.closest('th[data-sort]');
    if (head) { sortBy(cur, Number(head.dataset.sort)); return; }
    // v1.58.0 : un geste de ligne (restaurer, supprimer, suspendre) part à
    // qui a monté la liste (la fenêtre Backups)
    const actBtn = e.target.closest('[data-act]');
    if (actBtn) {
      const id = actBtn.closest('tr')?.dataset.id;
      const row = cur.rows && cur.rows.get(id);
      if (row && actBtn.dataset.act === 'yaml' && window.YamlWindow) {
        YamlWindow.open(cur.cluster, YAML_KIND[cur.kind], row.namespace || '', row.name, { onDone: () => load(cur) });
        return;
      }
      // v1.63.0 : le menu d'une image (modifier, cloner, chiffrer, télécharger, créer une VM)
      if (row && actBtn.dataset.act === 'more' && cur.kind === 'images' && window.StorageActions) {
        StorageActions.imageMenu(actBtn, cur.cluster, row, () => setTimeout(() => load(cur), 1500));
        return;
      }
      if (row && cur.opts && cur.opts.onAction) cur.opts.onAction(actBtn.dataset.act, row);
      else if (row) sectionAction(cur, actBtn.dataset.act, row);
      return;
    }
    if (e.target.closest('.res-upload') && window.StorageActions) {
      StorageActions.uploadDialog(cur.cluster, () => setTimeout(() => load(cur), 1500));
      return;
    }
    if (e.target.closest('.res-new') && NET_KIND[cur.kind] && window.NetAdmin) {
      NetAdmin.openNew(NET_KIND[cur.kind], cur.cluster, () => load(cur));
      return;
    }
    if (e.target.closest('.res-new') && window.ObjectForms) {
      ObjectForms.openNew(VIEWS[cur.kind].create, cur.cluster, { onDone: () => load(cur) });
      return;
    }
    const vm = e.target.closest('[data-vm]');
    if (vm) {
      const [ns, name] = vm.dataset.vm.split('/');
      if (window.VMEdit) window.VMEdit.open(cur.cluster, ns, name);
      return;
    }
    const add = e.target.closest('[data-addon]');
    if (add) { toggleAddon(cur, add); return; }
    const row = e.target.closest('tr.res-row');
    if (row && !e.target.closest('button, a, input')) {
      const id = row.dataset.id;
      if (cur.open.has(id)) cur.open.delete(id); else cur.open.add(id);
      render(cur);
    }
  }

  function say(cur, html) {
    const fb = cur && cur.host.querySelector('.res-feedback');
    if (fb) fb.innerHTML = html;
  }

  async function toggleAddon(cur, btn) {
    const ref = btn.dataset.addon;
    const enable = btn.dataset.enable === '1';
    const [ns, name] = ref.split('/');
    if (!confirm(enable ? tr('res.addon.confirmEnable', { name }) : tr('res.addon.confirmDisable', { name }))) return;
    btn.disabled = true;
    try {
      const r = await fetch(`/api/addons/${enc(cur.cluster)}/${enc(ns)}/${enc(name)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: enable }) });
      const out = await r.json();
      if (!r.ok) throw new Error(out.hint || out.error || `HTTP ${r.status}`);
      say(cur, esc(tr('res.addon.started', { name, id: out.action_id })));
      if (window.Dock && Dock.poll) Dock.poll();
      follow(cur, out.action_id, name, enable);
    } catch (e) {
      btn.disabled = false;
      say(cur, `<span class="res-error">${esc(tr('res.error', { msg: e.message }))}</span>`);
    }
  }

  function follow(cur, actionId, name, enable) {
    load(cur);
    if (!window.SSEReconnect) return;
    const es = SSEReconnect.connect(`/api/stream/${enc(actionId)}`, {
      on: {
        step: (e) => {
          try { const s = JSON.parse(e.data); if (s.message) say(cur, esc(`${name}: ${s.message}`)); } catch { /* ligne illisible */ }
        },
        end: (e) => {
          let d = {};
          try { d = JSON.parse(e.data); } catch { /* fin sans détail */ }
          es.close();
          say(cur, d.status === 'done'
            ? `${icon('ok')} ${esc(enable ? tr('res.addon.enabled', { name }) : tr('res.addon.disabledDone', { name }))}`
            : `<span class="res-error">${icon('fail')} ${esc(tr('res.error', { msg: d.error_summary || d.status || '?' }))}</span>`);
          load(cur);
        },
      },
    });
  }

  // -- cycle de vie ---------------------------------------------------------------
  // v1.58.0 : une liste est une INSTANCE (la section en cours, et chaque
  // onglet de la fenêtre Backups vivent côte à côte).
  async function load(cur) {
    if (!cur || cur.stopped) return;
    const params = new URLSearchParams();
    if (cur.kind === 'secrets' && cur.showSystem) params.set('all', '1');
    if (cur.opts && cur.opts.namespace) params.set('namespace', cur.opts.namespace);
    const q = params.toString() ? `?${params}` : '';
    try {
      const r = await fetch(`/api/cluster-objects/${enc(cur.cluster)}/${enc(cur.kind)}${q}`);
      const d = await r.json();
      if (cur.stopped) return;
      cur.data = r.ok ? d : { error: d.error === 'cluster refused' ? tr('res.refused') : (d.error || `HTTP ${r.status}`), hint: d.hint };
    } catch (e) {
      if (cur.stopped) return;
      cur.data = { error: e.message };
    }
    render(cur);
  }

  function create(kind, cluster, host, opts = {}) {
    let sort = null;
    try { sort = JSON.parse(localStorage.getItem(SORT_KEY(kind)) || 'null'); } catch {}
    const cur = { kind, cluster, host, opts, timer: null, data: null, filter: '', showSystem: false,
                  open: new Set(), sort, rows: new Map(), stopped: false };
    shell(cur);
    cur.timer = setInterval(() => { if (!document.hidden && cur.host.isConnected) load(cur); }, REFRESH_MS);
    return cur;
  }

  function halt(cur) {
    if (!cur) return;
    cur.stopped = true;
    if (cur.timer) clearInterval(cur.timer);
  }

  /** Une liste montée ailleurs que dans une section (fenêtre Backups). */
  function mount(kind, cluster, host, opts = {}) {
    if (!VIEWS[kind] || !host) return null;
    const cur = create(kind, cluster, host, opts);
    load(cur);
    return { kind, refresh: () => load(cur), stop: () => halt(cur) };
  }

  // La liste de la section courante (Storage, Security, Add-ons).
  let section = null;

  function start(kind, cluster, host) {
    if (!VIEWS[kind] || !host) return Promise.resolve();
    const same = section && section.kind === kind && section.cluster === cluster && section.host === host
      && !section.stopped;
    if (!same) {
      halt(section);
      section = create(kind, cluster, host);
    }
    return load(section);
  }

  function stop() {
    halt(section);
    section = null;
  }

  return { start, stop, mount, refresh: () => load(section), _bytes: bytes, _cronText: cronText };
})();
window.ResourceViews = ResourceViews;
