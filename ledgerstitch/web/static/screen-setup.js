// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — First-run setup wizard: 1 Welcome · 2 Setup check (+ optional local AI) · 3 Vault.
// Visual first: icons, short captions, details behind "Why?". Nothing is installed or downloaded
// unless the user agrees in the local AI consent dialog (local-ai.js; the API records the consent and
// refuses without it); the install (desktop only) + model download then run as one background job
// while the wizard goes on.
import { api } from './api.js';
import { h, clear, icon, logo, toast, modal, busy, append } from './ui.js';
import { store, loadStatus, featureState } from './state.js';
import { afterUnlock, go } from './app.js';
import { createVaultForm } from './screen-welcome.js';
import { askLocalAiConsent, onLocalAi } from './local-ai.js';   // oss-edition: local AI consent + background pill

const STEPS = ['Welcome', 'Setup', 'Vault'];
const POLL_MS = 700;

const why = (text) => h('details.why', h('summary', 'Why?'), h('p', text));
const fmtBytes = n => {
  if (!n) return '';
  const gb = n / 1e9;                     // decimal GB, as Ollama states model sizes
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`;
};

export async function renderSetup(main, params) {
  const wasUnlocked = !!(store.status && store.status.unlocked);
  const st = {
    step: Math.min(3, Math.max(1, parseInt(params.get('step') || '1', 10) || 1)),
    check: null, checkError: null,
    job: null, jobError: null, manual: null,
    localAiStarted: false,     // oss-edition: consent given and the background download started
    aiModel: null,             // only set when the model is ALREADY present (then the vault gets AI on at once)
    created: wasUnlocked,
    passphrase: null,          // the new vault's passphrase, kept only for "Try with sample data"
  };
  let alive = true;
  let timer = null;

  const root = h('div.setup');
  clear(main).append(root);
  draw();
  loadCheck();
  return () => { alive = false; clearTimeout(timer); st.passphrase = null; if (st.unwatch) st.unwatch(); };

  // ------------------------------------------------------------ data
  async function loadCheck() {
    try {
      st.check = await api('/api/setup/check');
      st.checkError = null;
      const o = st.check.ollama || {};
      if (o.has_recommended) st.aiModel = o.recommended_model;
    } catch (e) { st.checkError = e.message; }
    if (alive && st.step === 2) draw();
  }

  async function finish(extra = {}) {
    try {
      const r = await api('/api/setup/complete', { method: 'POST', json: { ...(st.aiModel ? { local_ai_model: st.aiModel } : {}), ...extra } });
      if (store.status) store.status.onboarding = r.onboarding;
    } catch { /* not fatal: the wizard just shows again next time */ }
  }

  // ------------------------------------------------------------ frame
  function draw() {
    if (!alive) return;
    const card = h('section.setup-card.card', { 'aria-labelledby': 'setup-h' });
    if (st.step === 1) stepWelcome(card);
    else if (st.step === 2) stepCheck(card);
    else stepVault(card);
    clear(root).append(
      h('header.setup-top',
        h('span.setup-brand', logo()),
        st.step < 3 || !st.created
          ? h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: skip }, 'Skip setup')
          : null),
      progress(),
      card,
    );
    const h1 = card.querySelector('h1');
    if (h1) { h1.setAttribute('tabindex', '-1'); h1.focus({ preventScroll: true }); }
  }

  function progress() {
    return h('ol.setup-steps', { 'aria-label': 'Setup progress' },
      STEPS.map((label, i) => {
        const n = i + 1;
        const state = n < st.step ? 'done' : n === st.step ? 'current' : 'todo';
        return h('li', { class: `setup-step is-${state}`, 'aria-current': n === st.step ? 'step' : null },
          h('span.setup-dot', { 'aria-hidden': 'true' }, state === 'done' ? icon('check', 14) : String(n)),
          h('span.setup-step-label', label));
      }));
  }

  function nav(...kids) { return h('div.setup-nav', ...kids); }
  function back() { return h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => { st.step -= 1; draw(); } }, 'Back'); }
  function next(label = 'Continue') {
    return h('button', { class: 'btn btn-primary btn-lg', type: 'button', onclick: () => { st.step += 1; draw(); } },
      label, icon('arrowRight', 16));
  }

  async function skip() {
    await finish({ skipped: true });
    st.passphrase = null;
    if (store.status && store.status.unlocked) go('review'); else go('welcome');
  }

  // ------------------------------------------------------------ step 1: welcome
  function stepWelcome(card) {
    const tile = (ic, title, text) => h('li.promise',
      h('span.promise-icon', icon(ic, 30)),
      h('div.promise-text', h('strong', title), why(text)));
    append(card, [
      h('div.setup-hero',
        h('h1#setup-h', 'Welcome to LedgerStitch'),
        h('p.setup-lede', 'Your statements, sorted. Privately.')),
      h('ul.promise-grid', { 'aria-label': 'Our privacy promise' },
        store.status && store.status.server_mode
          ? tile('laptop', 'On your own server', 'Self-hosted. No LedgerStitch cloud. Your statements stay on this server.')
          : tile('laptop', 'On your computer', 'No account and no cloud. Your statements never leave this machine.'),
        tile('lock', 'Encrypted', 'Everything is locked with your passphrase (AES-256). Only you can open it.'),
        tile('checkCircle', 'You approve every change', 'Suggestions wait in Review until you accept them. Nothing changes on its own.')),
      nav(h('span'), next('Get started')),
    ]);
  }

  // ------------------------------------------------------------ step 2: setup check
  function stepCheck(card) {
    const c = st.check;
    const row = (ic, label, value, ok) => h('li.check-row', { class: ok === false ? 'is-warn' : '' },
      h('span.check-icon', icon(ic, 20)),
      h('span.check-label', label),
      h('span.check-value', value),
      h('span.check-state', { 'aria-label': ok == null ? 'Checking' : ok ? 'OK' : 'Needs attention' },
        ok == null ? h('span.spinner', { 'aria-hidden': 'true' }) : icon(ok ? 'checkCircle' : 'alert', 20)));
    const list = h('ul.check-list', { 'aria-label': 'System check' },
      c ? [
        row('laptop', 'System', [c.os.name, c.os.version, c.os.arch].filter(Boolean).join(' · '), c.os.ok),
        row('disk', 'Disk space', c.disk.free_gb != null ? `${c.disk.free_gb} GB free` : 'Unknown', c.disk.ok),
        row('plug', c.server_mode ? 'Server' : 'Private port', c.port.port ? `${c.port.host}:${c.port.port}` : c.port.host, c.port.ok),
        // rows the installed add-ons add (registry.provide("setup.check", fn))
        ...(c.addons || []).map(a => row(a.icon || 'check', a.label, a.value, a.ok)),
      ].filter(Boolean) : ['System', 'Disk space', 'Private port'].map((l, i) => row(['laptop', 'disk', 'plug'][i], l, '', null)));
    append(card, [
      h('div.setup-hero', h('h1#setup-h', 'Quick setup check'), h('p.setup-lede', 'All local. Takes a second.')),
      st.checkError ? h('div.error-box', { role: 'alert' }, icon('alert', 16), st.checkError) : null,
      list,
      h('div.ai-cards', localAiCard()),
      nav(back(), h('button', { class: 'btn btn-primary btn-lg', type: 'button', onclick: () => continueFromCheck() }, 'Continue', icon('arrowRight', 16))),
    ]);
  }

  // --- oss-edition: local AI (one model). Status chips + one "Set up local AI" button that opens the explicit
  // consent dialog (local-ai.js). Nothing is pre-selected; after "Agree and set up" one background job
  // (install/start Ollama on desktop, then pull the model) runs while the wizard goes on.
  // Server mode: shown only when the operator configured an Ollama service (LEDGERSTITCH_OLLAMA_URL).
  function localAiCard() {
    const c = st.check;
    const o = (c && c.ollama) || null;
    if (o && o.show === false) return null;
    const server = !!(o ? o.install_allowed === false : store.status && store.status.server_mode);
    const body = h('div.ai-card-body');
    const card = h('section.ai-card', { 'aria-labelledby': 'ai-local-h' },
      h('div.ai-card-head',
        h('span.ai-card-icon', icon('chip', 22)),
        h('div', h('h2#ai-local-h', 'Local AI'),
          h('p.ai-card-cap', o ? `${o.model_label || o.recommended_model}${o.recommended_size ? ` · ${o.recommended_size}` : ''}` : 'Sorts merchants. Offline.')),
        h('span.chip.ok', 'Recommended')),
      body,
      why(`Runs an open AI model ${server ? 'in the Ollama service on this server' : 'in Ollama on this computer'}. `
        + 'It sees only merchant names, never amounts or account numbers. Nothing downloads until you agree.'
        + (o && o.model_ram ? ` Needs about ${o.model_ram} of memory.` : '')));
    if (!o) { body.append(h('p.muted.small', h('span.spinner', { 'aria-hidden': 'true' }), ' Looking for Ollama…')); return card; }
    const bg = o.local_ai || {};
    const statusChip = (ok, text, title) => h('span', { class: `chip ${ok ? 'ok' : 'warn'}`, title: title || null }, icon(ok ? 'checkCircle' : 'alert', 13), text);
    const plan = o.plan || {};
    body.append(h('div.la-chips', { 'aria-label': 'Local AI status' },
      o.running ? statusChip(true, server ? 'Ollama service reachable' : 'Ollama running')
        : server ? statusChip(false, 'Ollama not reachable')
          : h('span.chip.outline', icon('download', 13), o.installed ? 'Ollama installed' : 'Ollama: installs on setup'),
      o.running ? (o.has_recommended ? statusChip(true, 'Model installed') : h('span.chip.outline', icon('download', 13), 'Model not downloaded')) : null));
    if (o.running && o.has_recommended) {
      st.aiModel = o.recommended_model;
      body.append(h('p.ai-ready', icon('checkCircle', 20), h('span', 'Ready')));
      return card;
    }
    if (bg.state === 'running' || bg.state === 'queued' || st.localAiStarted) {
      const host = h('div.job-host', jobView(bg.job || bg));
      if (st.unwatch) st.unwatch();
      st.unwatch = onLocalAi(s2 => {         // live progress without redrawing the step (keeps focus)
        if (!host.isConnected) return;
        if (s2.state === 'ready') clear(host).append(h('p.ai-ready', icon('checkCircle', 20), h('span', 'Ready')));
        else if (s2.state === 'error') clear(host).append(h('p.small', { style: { color: 'var(--warn)' } }, icon('alert', 14), ` ${s2.error || 'Stopped'}`));
        else clear(host).append(jobView(s2.job || s2));
      });
      body.append(h('p.ai-found', icon('download', 18), 'Downloading in the background'), host);
      return card;
    }
    if (server && !o.running) {
      const cmd = o.fix || 'docker compose up -d ollama';
      body.append(h('div.cmd-box', h('code.mono', cmd), copyBtn(cmd)),
        h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { st.check = null; draw(); loadCheck(); } }, icon('refresh', 14), 'Check again'));
      return card;
    }
    if (st.manual || (!o.running && !plan.auto && plan.method === 'script')) {
      const cmd = (st.manual && st.manual.command) || plan.command;
      body.append(
        h('p.small', icon('terminal', 16), ' Run this, then check again:'),
        h('div.cmd-box', h('code.mono', cmd), copyBtn(cmd)),
        h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { st.manual = null; loadCheck(); } }, icon('refresh', 14), 'Check again'));
      return card;
    }
    if (!o.running && !plan.auto) { body.append(h('p.small.muted', 'Not available on this system.')); return card; }
    if (bg.state === 'error') body.append(h('p.small', { style: { color: 'var(--warn)' } }, icon('alert', 14), ` Last attempt stopped: ${bg.error || ''}`));
    const res = o.resources || {};
    if (res.ok === false) body.append(h('p.small', { style: { color: 'var(--warn)' } }, icon('alert', 14), ' This machine may be too small'));
    body.append(h('button', { class: 'btn btn-primary', type: 'button', id: 'local-ai-setup', onclick: e => consent(e.currentTarget) },
      icon('download', 16), 'Set up local AI'));
    return card;
  }

  function copyBtn(cmd) {
    return h('button', { class: 'btn btn-sm', type: 'button', onclick: async () => {
      try { await navigator.clipboard.writeText(cmd); toast('Copied'); } catch { toast('Select and copy it by hand', { kind: 'warn' }); }
    } }, icon('copy', 14), 'Copy');
  }

  async function consent(btn) {
    btn.disabled = true;
    try {
      const r = await askLocalAiConsent(st.check.ollama);
      if (r && r.manual) st.manual = { command: r.manual };
      if (r && r.started) {
        st.localAiStarted = true;
        toast('Local AI is downloading', { detail: 'Carry on. It switches on by itself when ready.' });
        try { st.check.ollama.local_ai = await api('/api/setup/local-ai'); } catch { /* the pill keeps polling */ }
      }
    } finally { btn.disabled = false; }
    if (alive && st.step === 2) draw();
  }
  // --- /oss-edition

  function jobView(job) {
    const pct = job.progress != null ? Math.round(job.progress * 100) : null;
    const bar = h('div.progress', { role: 'progressbar', 'aria-label': job.message || 'Working',
      'aria-valuemin': '0', 'aria-valuemax': '100', ...(pct != null ? { 'aria-valuenow': String(pct) } : {}),
      class: pct == null ? 'is-indeterminate' : '' },
    h('i', { style: { width: pct != null ? `${pct}%` : '35%' } }));
    const size = job.total && job.completed ? `${fmtBytes(job.completed)} / ${fmtBytes(job.total)}` : '';
    return h('div.job', { 'aria-live': 'polite' },
      h('div.job-head', h('span.job-msg', job.message || 'Working…'), h('span.job-pct', pct != null ? `${pct}%` : '')),
      bar,
      size ? h('p.job-size', size) : null);
  }

  // Continue never downloads anything: local AI starts only from the consent dialog.
  function continueFromCheck() { st.step = 3; draw(); }


  // ------------------------------------------------------------ step 3: vault
  function stepVault(card) {
    if (!st.created) {
      const { form, focus } = createVaultForm({
        compact: true,
        onCreated: async ({ passphrase }) => {
          st.created = true;
          st.passphrase = passphrase;
          if (st.aiModel) {
            try {
              await api('/api/settings', { method: 'PUT', json: { ai_enabled: true, provider: 'ollama', model: st.aiModel } });
            } catch { /* the user can turn it on in Settings */ }
          }
          await loadStatus();
          draw();
        },
      });
      card.classList.add('is-narrow');
      append(card, [
        h('div.setup-hero', h('span.setup-hero-icon', icon('lock', 28)), h('h1#setup-h', 'Create your vault'),
          h('p.setup-lede', 'Encrypted. Only your passphrase opens it.')),
        form,
        nav(back(), h('span')),
      ]);
      requestAnimationFrame(focus);
      return;
    }
    const tile = (ic, title, cap, onclick) => h('button.start-tile', { type: 'button', onclick },
      h('span.start-icon', icon(ic, 28)), h('strong', title), h('span', cap));
    const sample = tile('chart', 'Try with sample data', 'A made-up business to explore', e => trySample(e.currentTarget));
    const imp = tile('upload', 'Import my first statement', 'CSV or PDF from your bank', async () => {
      await finish(); st.passphrase = null; await afterUnlock('import');
    });
    append(card, [
      h('div.setup-hero',
        h('span.setup-hero-icon.is-ok', icon('check', 30)),
        h('h1#setup-h', wasUnlocked ? 'Setup done' : 'Your vault is ready'),
        h('p.setup-lede', st.aiModel ? 'Local AI is on.' : st.localAiStarted ? 'Local AI is downloading. It turns on by itself.' : 'What next?')),
      h('div.start-grid', sample, imp),
      nav(h('span'), h('button', { class: 'btn btn-ghost', type: 'button', onclick: async () => { await finish(); st.passphrase = null; await afterUnlock(); } }, 'Done')),
    ]);
  }

  async function trySample(btn) {
    let pass = st.passphrase;
    if (!pass) pass = await askPassphrase();
    if (!pass) return;
    await busy(btn, async () => {
      try {
        await api('/api/setup/demo', { method: 'POST', json: { passphrase: pass } });
        st.passphrase = null;
        await finish();
        toast('Sample workspace opened', { detail: 'Made-up data. Your own vault is in the workspace list.' });
        await afterUnlock('review');
      } catch (e) { toast('Couldn’t load the sample', { kind: 'error', detail: e.message }); }
    }, 'Loading…');
  }

  function askPassphrase() {
    const input = h('input', { class: 'input', type: 'password', id: 'demo-pw', autocomplete: 'new-password' });
    const m = modal({
      title: 'Sample vault passphrase',
      description: 'The sample gets its own encrypted vault.',
      body: h('div.field', h('label', { for: 'demo-pw' }, 'Passphrase (8+ characters)'), input),
      actions: [{ label: 'Cancel', value: false }, { label: 'Create sample', primary: true, onClick: ({ close }) => {
        if (input.value.length < 8) { input.setAttribute('aria-invalid', 'true'); input.focus(); return false; }
        close(input.value); return true;
      } }],
      initialFocus: '#demo-pw',
    });
    return m.result.then(v => (typeof v === 'string' ? v : null));
  }
}
