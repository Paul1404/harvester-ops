/**
 * harvester-ops — « Edit YAML » et « Download YAML », comme dans Harvester (v1.60.0)
 *
 * Une fenêtre par objet : son YAML (sans status ni managedFields), un bouton
 * « Vérifier » qui fait juger le texte par le cluster sans rien changer (les
 * webhooks de Harvester répondent), « Enregistrer » qui remplace l'objet par
 * une action suivie, et le téléchargement. Le resourceVersion reste dans le
 * texte : si l'objet a changé entre-temps, le cluster refuse au lieu
 * d'écraser la modification de quelqu'un d'autre.
 */
const YamlWindow = (() => {
  const tr = (k, p) => (window.i18n ? i18n.t(k, p) : k);
  const enc = encodeURIComponent;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icon = (n, size = 14) => (window.Icons ? Icons.svg(n, { size }) : '');

  const q = (ns) => (ns ? `?namespace=${enc(ns)}` : '');
  const url = (cluster, kind, ns, name) =>
    `/api/yaml/${enc(cluster)}/${enc(kind)}${name ? `/${enc(name)}` : ''}${q(ns)}`;

  async function call(method, u, body) {
    const r = await fetch(u, { method, headers: { 'Content-Type': 'application/json' },
                               body: body ? JSON.stringify(body) : undefined });
    let d = {};
    try { d = await r.json(); } catch { /* sans corps */ }
    if (!r.ok) throw new Error(d.hint || d.error || `HTTP ${r.status}`);
    return d;
  }

  /** Télécharger le YAML d'un objet (un fichier <nom>.yaml). */
  function download(cluster, kind, ns, name) {
    const a = document.createElement('a');
    a.href = `${url(cluster, kind, ns, name)}${ns ? '&' : '?'}download=1`;
    a.download = `${name}.yaml`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function body(creating) {
    return `<div class="yw">
      <div class="yw-tools">
        <button type="button" class="btn btn-sm btn-secondary tip" data-yw="check" data-tip="${esc(tr('yw.checkTip'))}">${icon('ok')} ${esc(tr('yw.check'))}</button>
        <button type="button" class="btn btn-sm btn-primary tip" data-yw="save" data-tip="${esc(creating ? tr('yw.createTip') : tr('yw.saveTip'))}">${icon(creating ? 'add' : 'save')} ${esc(creating ? tr('of.create') : tr('of.save'))}</button>
        ${creating ? '' : `<button type="button" class="btn btn-sm btn-secondary tip" data-yw="reload" data-tip="${esc(tr('yw.reloadTip'))}">${icon('refresh')} ${esc(tr('yw.reload'))}</button>
        <button type="button" class="btn btn-sm btn-secondary tip" data-yw="download" data-tip="${esc(tr('yw.downloadTip'))}">${icon('download')} ${esc(tr('yw.download'))}</button>`}
        <span class="yw-ro" hidden>${icon('lock', 12)} ${esc(tr('yw.readOnly'))}</span>
      </div>
      <textarea class="yw-text tip" spellcheck="false" autocapitalize="off" autocomplete="off"
        data-tip="${esc(tr('yw.textTip'))}">${esc(tr('common.loading'))}</textarea>
      <div class="yw-msg of-msg" role="status"></div>
    </div>`;
  }

  function followInto(root, actionId, doneText, onDone) {
    const msg = root.querySelector('.yw-msg');
    if (window.Dock && Dock.poll) Dock.poll();
    msg.innerHTML = esc(tr('bk.started', { id: actionId }));
    if (!window.SSEReconnect) return;
    let last = '';
    const es = SSEReconnect.connect(`/api/stream/${enc(actionId)}`, {
      on: {
        step: (e) => { try { const s = JSON.parse(e.data); if (s.message) { last = s.message; msg.textContent = s.message; } } catch { /* ligne illisible */ } },
        end: (e) => {
          let d = {};
          try { d = JSON.parse(e.data); } catch { /* fin sans détail */ }
          es.close();
          const ok = d.status === 'done';
          msg.innerHTML = ok ? `${icon('ok')} ${esc(doneText)}`
            : `<span class="res-error">${icon('fail')} ${esc(tr('res.error', { msg: last || d.error_summary || d.status || '?' }))}</span>`;
          if (onDone) onDone(ok);
        },
      },
    });
  }

  function wire(root, ctx) {
    const ta = root.querySelector('.yw-text');
    const msg = root.querySelector('.yw-msg');
    // Tab : deux espaces (le YAML n'accepte pas de tabulation)
    ta.addEventListener('keydown', (e) => {
      if (e.key !== 'Tab' || e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return;
      e.preventDefault();
      const s = ta.selectionStart, en = ta.selectionEnd;
      ta.value = ta.value.slice(0, s) + '  ' + ta.value.slice(en);
      ta.selectionStart = ta.selectionEnd = s + 2;
    });
    const say = (html) => { msg.innerHTML = html; };
    root.querySelector('.yw-tools').addEventListener('click', async (e) => {
      const b = e.target.closest('[data-yw]');
      if (!b) return;
      const what = b.dataset.yw;
      if (what === 'download') { download(ctx.cluster, ctx.kind, ctx.namespace, ctx.name); return; }
      if (what === 'reload') { load(root, ctx); return; }
      const u = url(ctx.cluster, ctx.kind, ctx.creating ? ctx.namespace : ctx.namespace, ctx.creating ? null : ctx.name);
      const method = ctx.creating ? 'POST' : 'PUT';
      b.disabled = true;
      try {
        if (what === 'check') {
          const d = await call(method, u, { yaml: ta.value, check: true });
          say(d.ok ? `${icon('ok')} ${esc(tr('yw.checked'))}`
            : `<span class="res-error">${icon('fail')} ${esc(d.error || '?')}</span>`);
          b.disabled = false;
          return;
        }
        if (!ctx.creating && !confirm(tr('yw.confirmSave', { name: ctx.label }))) { b.disabled = false; return; }
        const out = await call(method, u, { yaml: ta.value });
        followInto(root, out.action_id, ctx.creating ? tr('yw.created', { name: out.name || '' }) : tr('yw.saved', { name: ctx.label }),
          (ok) => { b.disabled = false; if (ok && !ctx.creating) load(root, ctx, true); if (ok && ctx.onDone) ctx.onDone(); });
      } catch (err) {
        b.disabled = false;
        say(`<span class="res-error">${esc(err.message)}</span>`);
      }
    });
  }

  async function load(root, ctx, keepMsg) {
    const ta = root.querySelector('.yw-text');
    try {
      const d = await call('GET', url(ctx.cluster, ctx.kind, ctx.namespace, ctx.name));
      ta.value = d.yaml || '';
      const ro = !d.writable;
      ta.readOnly = ro;
      root.querySelector('.yw-ro').hidden = !ro;
      root.querySelectorAll('[data-yw="check"], [data-yw="save"]').forEach(b => { b.hidden = ro; });
      if (!keepMsg) root.querySelector('.yw-msg').innerHTML = '';
    } catch (err) {
      ta.value = '';
      root.querySelector('.yw-msg').innerHTML = `<span class="res-error">${esc(err.message)}</span>`;
    }
  }

  /** La fenêtre YAML d'un objet existant. */
  function open(cluster, kind, ns, name, opts = {}) {
    const label = ns ? `${ns}/${name}` : name;
    const panel = FloatingPanels.open({
      id: `yaml-${cluster}-${kind}-${ns || '_'}-${name}`, icon: 'code', width: 760, height: 620,
      title: `${tr('yw.title', { name: label })} · ${cluster}`,
      bodyHtml: body(false),
    });
    const root = panel.el;
    if (root.dataset.ywReady) return panel;
    root.dataset.ywReady = '1';
    const ctx = { cluster, kind, namespace: ns || '', name, label, creating: false, onDone: opts.onDone };
    wire(root, ctx);
    load(root, ctx);
    return panel;
  }

  /** Créer un objet à partir d'un YAML (modèle prérempli). */
  function create(cluster, kind, starter, opts = {}) {
    const panel = FloatingPanels.open({
      id: `yaml-new-${cluster}-${kind}`, icon: 'code', width: 760, height: 620,
      title: `${tr('yw.newTitle')} · ${cluster}`,
      bodyHtml: body(true),
    });
    const root = panel.el;
    if (root.dataset.ywReady) return panel;
    root.dataset.ywReady = '1';
    root.querySelector('.yw-text').value = starter || '';
    const ctx = { cluster, kind, namespace: opts.namespace || '', name: null, label: kind, creating: true, onDone: opts.onDone };
    wire(root, ctx);
    return panel;
  }

  return { open, create, download };
})();
window.YamlWindow = YamlWindow;
