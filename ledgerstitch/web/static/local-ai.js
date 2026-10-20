// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — local-ai.js : local AI (one model, GLM-4.7-Flash) — the consent dialog, the background
// download started after consent, the top-bar pill and the Settings → Privacy & AI card.
// GET /api/setup/local-ai → {state: off|queued|running|ready|error, model, label, size, progress, message, error, consent}
// Nothing downloads without the consent dialog's "Agree and set up" (the API records it and refuses otherwise).
// Shows a small pill in the app shell while the download runs ("Local AI: downloading 47%"), toasts once
// when the model is ready (the backend has already switched categorization to it). Polls only while
// something is happening.
import { api } from './api.js';
import { h, clear, icon, toast, modal, why, confirmDialog, busy } from './ui.js';
import { store, loadSettings, loadStatus } from './state.js';

const POLL_MS = 2500;
let current = null;          // last status
let timer = null;
let announced = false;       // the "ready" toast is shown once per page load
const listeners = new Set();

export function localAiStatus() { return current; }
export function onLocalAi(fn) { listeners.add(fn); return () => listeners.delete(fn); }
const isServer = () => !!(store.status && store.status.server_mode);
const fmtBytes = n => {
  if (!n) return '';
  const gb = n / 1e9;                     // decimal GB, as Ollama states model sizes
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`;
};

function publish(st) {
  current = st;
  for (const fn of listeners) { try { fn(st); } catch { /* a listener must not stop the poll */ } }
  drawPill();
}

async function tick() {
  clearTimeout(timer);
  timer = null;
  let st;
  try { st = await api('/api/setup/local-ai', { quiet: true }); } catch { return; }
  const was = current && current.state;
  publish(st);
  if (st.state === 'ready' && was && was !== 'ready' && !announced) {
    announced = true;
    toast('Local AI is ready', { detail: `${st.label || st.model} runs on ${st.server_mode ? 'this server' : 'this computer'}. New imports are sorted with it.` });
    // the backend switched the open workspace to the local model: refresh what the screens show
    Promise.allSettled([loadStatus(), loadSettings(true)]).catch(() => {});
  }
  if (st.state === 'running' || st.state === 'queued') timer = setTimeout(tick, POLL_MS);
}

/** Start watching (idempotent). Called from the app shell after boot and after consent starts the job. */
export function watchLocalAi() {
  if (!timer) tick();
}

// ------------------------------------------------------------ the consent dialog
/**
 * The explicit consent step (never pre-ticked). `o` is the `ollama` block of GET /api/setup/check.
 * Resolves {started: true} once the download job started, {manual: command} on Linux desktop
 * (the user runs the official installer), or null for "Not now".
 */
export function askLocalAiConsent(o) {
  const server = !!(o.install_allowed === false || isServer());
  const r = o.resources || {};
  const label = o.model_label || o.recommended_model || 'Local model';
  const size = o.recommended_size || '';
  const gb = n => (n == null ? '?' : `${Math.round(n)} GB`);
  const diskShort = r.disk_ok === false, memShort = r.mem_ok === false;
  const short = diskShort || memShort;
  const needsInstall = !server && !o.running;
  const row = (ic, title, cap, warn = false) => h('li', { class: warn ? 'is-warn' : '' },
    h('span.consent-ic', icon(warn ? 'alert' : ic, 20)), h('span.consent-t', h('strong', title), h('span', cap)));
  const have = [r.disk_free_gb != null ? `${gb(r.disk_free_gb)} free${r.disk_approx ? ' (approx.)' : ''}` : null,
    r.mem_total_gb != null ? `${gb(r.mem_total_gb)} RAM` : null].filter(Boolean).join(' · ') || 'Couldn’t measure';
  const list = h('ul.consent-grid', { 'aria-label': 'What setting up local AI means' },
    needsInstall ? row('plus', 'Installs Ollama', 'Free app · signature checked') : null,
    row('download', `Downloads ${size || 'the model'}`, `${label} · from ollama.com`),
    row(server ? 'layers' : 'laptop', server ? 'Runs on this server' : 'Runs on this computer', 'Offline after the download'),
    row('eye', 'Sees merchant names only', 'Never amounts or account numbers'),
    row('disk', `Needs ${gb(r.need_disk_gb || 25)} disk · ${gb(r.need_mem_gb || 24)} RAM`, `You have ${have}`, short),
    row('trash', 'Remove any time', 'Settings → Privacy & AI'));
  const warnBox = short ? h('div.warning-box.compact', { role: 'alert' }, icon('alert', 16), h('div',
    h('strong', diskShort && memShort ? 'Low disk and memory' : diskShort ? 'Low disk space' : 'Low memory'),
    h('p.small', memShort ? 'It may run very slowly or not start.' : 'The download may not fit.'))) : null;
  const more = why('Why?',
    h('p', `${label} is an open model that runs inside Ollama${server ? ', the Ollama service next to LedgerStitch' : ' on this computer'}. `
      + 'LedgerStitch sends it only redacted merchant names, the same as any AI option. Nothing goes to LedgerStitch or anyone else.'),
    h('p', 'The download comes from Ollama’s model registry and resumes if it stops. Your OK is saved with the time, model and address, and you can withdraw it in Settings.'),
    r.disk_approx ? h('p', 'Disk space is measured on LedgerStitch’s data volume, which on one Docker host shares a disk with the model volume.') : null,
    server ? h('p', 'In Docker on a Mac, models run on the CPU only. Give Docker at least 24 GB of memory (Docker Desktop → Settings → Resources).') : null);
  let armed = false;
  const m = modal({
    title: 'Set up local AI?',
    wide: true,
    description: `${label}${size ? ` · ${size}` : ''}`,
    body: h('div.consent', list, warnBox, more),
    actions: [
      { label: 'Not now', ghost: true, value: null },
      { label: 'Agree and set up', primary: true, onClick: async ({ close, button }) => {
        if (short && !armed) {                  // the second, deliberate click
          armed = true;
          button.textContent = 'Download anyway';
          button.classList.add('btn-danger');
          return false;
        }
        try {
          const out = await api('/api/setup/local-ai', { method: 'POST', json: { consent: true, download_anyway: armed } });
          if (out.manual) { close({ manual: out.command }); return true; }
          watchLocalAi();
          close({ started: true });
          return true;
        } catch (e) {
          if (e.data && e.data.needs_override && !armed) { armed = true; button.textContent = 'Download anyway'; button.classList.add('btn-danger'); return false; }
          toast('Local AI didn’t start', { kind: 'warn', detail: e.data && e.data.fix ? `${e.message} Run: ${e.data.fix}` : e.message });
          return false;
        }
      } },
    ],
  });
  return m.result.then(v => v || null);
}

// ------------------------------------------------------------ the shell pill
function drawPill() {
  const host = document.getElementById('topbar');   // the v2 top bar (shell.js)
  if (!host) return;
  let pill = document.getElementById('local-ai-pill');
  const st = current;
  const show = st && (st.state === 'running' || st.state === 'queued' || st.state === 'error');
  if (!show) { if (pill) pill.remove(); return; }
  if (!pill) {
    pill = h('a', { id: 'local-ai-pill', class: 'local-ai-pill', href: '#/settings?section=ai-local', role: 'status', 'aria-live': 'polite' });
    host.insertBefore(pill, document.getElementById('top-actions'));
  }
  pill.classList.toggle('is-error', st.state === 'error');
  const pct = st.progress != null ? `${Math.round(st.progress * 100)}%` : (st.state === 'error' ? '!' : '…');
  clear(pill).append(icon(st.state === 'error' ? 'alert' : 'chip', 13),
    h('span.pill-long', pillText(st)), h('span.pill-short', { 'aria-hidden': 'true' }, `AI ${pct}`));
  pill.setAttribute('aria-label', pillText(st));
  const bytes = st.total ? ` · ${fmtBytes(st.completed)} / ${fmtBytes(st.total)}` : '';
  pill.title = st.state === 'error' ? `Local AI setup stopped: ${st.error || ''}` : `${st.message || 'Setting up local AI'}${bytes}`;
}

export function pillText(st) {
  if (!st) return '';
  if (st.state === 'error') return 'Local AI: setup stopped';
  if (st.state === 'queued') return 'Local AI: waiting…';
  const pct = st.progress != null ? Math.round(st.progress * 100) : null;
  const what = /download/i.test(st.message || '') ? 'downloading' : /install|signature|homebrew/i.test(st.message || '') ? 'installing Ollama' : (st.message || 'working').toLowerCase();
  return `Local AI: ${what}${pct != null ? ` ${pct}%` : ''}`;
}

function progressEl(st) {
  const pct = st.progress != null ? Math.round(st.progress * 100) : null;
  return h('div.progress', { class: pct == null ? 'is-indeterminate' : '', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100',
    ...(pct != null ? { 'aria-valuenow': String(pct) } : {}), 'aria-label': st.message || 'Setting up local AI' },
  h('i', { style: { width: pct != null ? `${pct}%` : '35%' } }));
}

// ------------------------------------------------------------ Settings → Setup status line
/** A live status element for Settings → Setup. Shows nothing while local AI was never requested. */
export function localAiStatusLine() {
  const el = h('div.local-ai-line', { 'aria-live': 'polite' });
  const draw = st => {
    clear(el);
    if (!st || st.state === 'off') return;
    if (st.state === 'ready') {
      el.append(h('p.small', icon('checkCircle', 14), ` Local AI: ${st.label || st.model} is ready.`));
    } else if (st.state === 'error') {
      el.append(h('p.small', { style: { color: 'var(--warn)' } }, icon('alert', 14), ` Local AI setup stopped: ${st.error || 'unknown error'}`));
    } else {
      el.append(h('p.small', h('span.spinner', { 'aria-hidden': 'true' }), ` ${pillText(st)}`,
        st.size ? h('span.muted', ` · ${st.label || st.model}, ${st.size}`) : null), progressEl(st));
    }
  };
  draw(current);
  const off = onLocalAi(draw);
  el.addEventListener('ls:remove', off);
  if (!current) watchLocalAi();
  return el;
}

// ------------------------------------------------------------ Settings → Privacy & AI: the local AI card body
/** Status, model, size + Set up / Remove model / Withdraw consent. */
export function localAiManage() {
  const el = h('div.local-ai-manage', { 'aria-live': 'polite' });
  const chipRow = (...kids) => h('div.la-chips', ...kids);
  const draw = st => {
    clear(el);
    if (!st) { el.append(h('p.small.muted', h('span.spinner', { 'aria-hidden': 'true' }), ' Checking…')); return; }
    const label = st.label || st.model;
    const where = st.server_mode ? 'This server' : 'This computer';
    const stateChip = {
      ready: h('span.chip.ok', icon('checkCircle', 13), 'Ready'),
      running: h('span.chip.info', icon('download', 13), st.progress != null ? `${Math.round(st.progress * 100)}%` : 'Working'),
      queued: h('span.chip.info', icon('clock', 13), 'Waiting'),
      error: h('span.chip.warn', icon('alert', 13), 'Stopped'),
      off: h('span.chip.outline', 'Off'),
    }[st.state] || null;
    el.append(h('div.la-head',
      h('span.ai-card-icon', icon('chip', 22)),
      h('div.grow', h('strong', label), h('div.small.muted', [st.size, st.ram ? `${st.ram} RAM` : null, where].filter(Boolean).join(' · '))),
      stateChip));
    if (st.state === 'running' || st.state === 'queued') {
      el.append(h('div.job', h('div.job-head', h('span.job-msg', st.message || 'Working…'),
        h('span.job-pct', st.total ? `${fmtBytes(st.completed)} / ${fmtBytes(st.total)}` : '')), progressEl(st)));
    }
    if (st.state === 'error') el.append(h('p.small', { style: { color: 'var(--warn)' } }, icon('alert', 14), ` ${st.error || 'Setup stopped.'}`));
    const btns = [];
    if (st.state === 'off' || st.state === 'error') {
      btns.push(h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: e => setUp(e.currentTarget) }, icon('download', 14), st.state === 'error' ? 'Try again' : 'Set up local AI'));
    }
    if (st.state === 'ready') {
      btns.push(h('button', { class: 'btn btn-sm btn-danger', type: 'button', onclick: e => removeModel(e.currentTarget, st) }, icon('trash', 14), 'Remove model'));
    }
    if (st.consent) {
      btns.push(h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: e => withdraw(e.currentTarget) }, icon('xCircle', 14), 'Withdraw consent'));
    }
    if (btns.length) el.append(h('div.row', { style: { gap: '8px', marginTop: '12px' } }, ...btns));
    if (st.consent) el.append(h('p.small.muted', icon('check', 12), ` Agreed ${String(st.consent.at || '').slice(0, 10)}`));
  };
  async function setUp(btn) {
    await busy(btn, async () => {
      try {
        const c = await api('/api/setup/check');
        const o = c.ollama || {};
        if (o.install_allowed === false && !o.running) {
          toast('The Ollama service isn’t reachable', { kind: 'warn', detail: `Run: ${o.fix || 'docker compose up -d ollama'}` });
          return;
        }
        const r = await askLocalAiConsent(o);
        if (r && r.manual) toast('Install Ollama first', { kind: 'warn', detail: `Run in a terminal: ${r.manual}` });
      } catch (e) { toast('Couldn’t check local AI', { kind: 'error', detail: e.message }); }
    }, 'Checking…');
    tick();
  }
  async function removeModel(btn, st) {
    if (!(await confirmDialog({ title: `Remove ${st.label || st.model}?`, description: `Frees ${st.size || 'its disk space'}. Local AI turns off.`, confirmLabel: 'Remove model', danger: true }))) return;
    await busy(btn, async () => {
      try { publish(await api('/api/setup/local-ai/model', { method: 'DELETE' })); toast('Model removed'); loadSettings(true).catch(() => {}); }
      catch (e) { toast('Couldn’t remove the model', { kind: 'error', detail: e.data && e.data.fix ? `${e.message} Run: ${e.data.fix}` : e.message }); }
    }, 'Removing…');
  }
  async function withdraw(btn) {
    if (!(await confirmDialog({ title: 'Withdraw consent?', description: 'Stops any download. Local AI turns off. The model stays until you remove it.', confirmLabel: 'Withdraw' }))) return;
    await busy(btn, async () => {
      try { publish(await api('/api/setup/local-ai', { method: 'DELETE' })); toast('Consent withdrawn'); loadSettings(true).catch(() => {}); }
      catch (e) { toast('Couldn’t withdraw', { kind: 'error', detail: e.message }); }
    }, 'Withdrawing…');
  }
  draw(current);
  const off = onLocalAi(draw);
  el.addEventListener('ls:remove', off);
  tick();
  return el;
}
