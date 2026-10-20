// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — ai.js : categorization and the local AI (Ollama on this computer).
// Settings → "AI & privacy" (Off / On this computer), "What the AI receives", and running
// categorization. Add-ons that provide other AI providers add their own Settings section; while one
// of their providers is chosen, this card says so and leaves it alone.
//
// Events for add-ons (document): 'ls:categorize-start' {detail: {btn}} and 'ls:categorized'
// {detail: result} around every run; 'ls:ai-preview' {detail: {data, add(node)}} while the
// "What the AI receives" dialog is built (an add-on may add a section of its own).
import { api } from './api.js';
import { h, clear, icon, toast, modal, busy, plural, spinner, fmtInt, fmtPct, num, laneChip } from './ui.js';
import { store, loadStatus, refreshReviewBadge } from './state.js';

const fmtSec = v => { const n = num(v); return n >= 10 ? `${Math.round(n)} s` : `${n.toFixed(1)} s`; };
const coreProviders = new Set(['off', 'offline', 'ollama', '', undefined, null]);

/** "provider:model" → "model" for providers this app knows (core + installed add-ons). */
export function modelName(key) {
  const s = String(key || '');
  const i = s.indexOf(':');
  const known = ['ollama', ...((store.status && store.status.ai_providers) || [])];
  return i > 0 && known.includes(s.slice(0, i)) ? s.slice(i + 1) : s;
}

/**
 * The AI cards for Settings. `card` and `saveSettings` are the Settings screen's helpers.
 * Returns {cards: [Node], cleanup()}.
 */
export function aiSection({ st, card, saveSettings }) {
  return { cards: [localCard(st, card, saveSettings)], cleanup: () => {} };
}

/** "ai.local": Off / Ollama on this computer (loopback only). */
function localCard(st, card, saveSettings) {
  // the default local model comes from the backend (status.recommended_local_model), one source of truth
  const DEFAULT_LOCAL = (store.status && store.status.recommended_local_model) || '';
  const modelLabel = () => model.value.trim() || DEFAULT_LOCAL || '<model>';
  const fixedUrl = (store.status && store.status.ollama_operator_url) || null;   // server mode: the compose Ollama service
  const addonProvider = st.ai_enabled && !coreProviders.has(st.provider) ? st.provider : null;
  let mode = addonProvider ? 'addon' : st.ai_enabled && st.provider === 'ollama' ? 'local' : 'off';
  const choices = [['off', 'Off', 'Your rules and LedgerStitch’s built-in rules only. Nothing is sent anywhere.'],
    ['local', 'On this computer (Ollama)', 'A model running in Ollama on this computer sorts what your rules can’t. Nothing leaves your machine.']];
  if (addonProvider) choices.push(['addon', 'Set up by an add-on', 'An installed add-on provides the AI. Its settings are further down this page.']);
  const radios = h('div.choice-cards', { class: choices.length === 2 ? 'two' : '', role: 'radiogroup', 'aria-label': 'How LedgerStitch uses AI' },
    choices.map(([id, label, desc]) => {
      const r = h('input', { type: 'radio', name: 'ai-mode', id: `ai-mode-${id}`, value: id, checked: mode === id, 'aria-describedby': `ai-mode-${id}-d` });
      r.addEventListener('change', () => { if (r.checked) { mode = id; draw(); } });
      return h('label.choice', { for: r.id }, r, h('span.choice-title', label), h('span.choice-desc', { id: `ai-mode-${id}-d` }, desc));
    }));
  const model = h('input', { class: 'input', id: 'ai-model', value: st.provider === 'ollama' ? (st.model || '') : '', placeholder: DEFAULT_LOCAL || 'model name', maxlength: 200, autocomplete: 'off', spellcheck: 'false', 'aria-describedby': 'ai-model-hint' });
  const url = h('input', { class: 'input', id: 'ai-ollama', value: fixedUrl || st.ollama_url || 'http://127.0.0.1:11434', maxlength: 200, spellcheck: 'false', autocomplete: 'off', 'aria-describedby': 'ai-ollama-hint', disabled: !!fixedUrl });
  const fields = h('div');
  const results = h('div', { 'aria-live': 'polite' });
  const statusLine = h('p.small.ink-2', { 'aria-live': 'polite' });
  const test = h('button', { class: 'btn', type: 'button' }, icon('checkCircle', 16), 'Test local model');
  test.addEventListener('click', async () => {
    if (!(await doSave(test, true))) return;
    await busy(test, runTest, 'Testing…');
  });

  function draw() {
    clear(fields);
    if (mode === 'local') {
      fields.append(h('div.form-grid', { style: { marginTop: '18px' } },
        h('div.field', h('label', { for: 'ai-model' }, 'Model'), model,
          fixedUrl
            ? h('span.hint', { id: 'ai-model-hint' }, `Blank uses ${DEFAULT_LOCAL || 'the recommended model'}. Download it under Local AI.`)
            : h('span.hint', { id: 'ai-model-hint' }, `Blank uses ${DEFAULT_LOCAL || 'the recommended model'}. Run `, h('code', `ollama pull ${modelLabel()}`), ' first.')),
        h('div.field', h('label', { for: 'ai-ollama' }, 'Ollama address'), url,
          h('span.hint', { id: 'ai-ollama-hint' }, fixedUrl ? 'Set by the server: the Ollama service.' : 'Only addresses on this computer are allowed.'))),
      h('div.row', { style: { marginTop: '14px' } }, test, h('span.small.muted', 'Sends 3 made-up merchant names to your local model.')),
      results);
    } else clear(results);
    statusLine.textContent = mode === 'local'
      ? (fixedUrl ? 'AI runs in the Ollama service on this server. Redacted merchant names go only to it.'
        : 'AI runs locally through Ollama. Redacted merchant names go only to the model on this computer — nothing leaves your machine.')
      : mode === 'addon' ? 'An add-on provides the AI. Only redacted merchant names are ever sent; see its section below.'
        : 'AI is off. Only your rules and LedgerStitch’s built-in rules are used — nothing is sent anywhere.';
  }
  model.addEventListener('input', () => { const c = fields.querySelector('#ai-model-hint code'); if (c) c.textContent = `ollama pull ${modelLabel()}`; });
  draw();

  async function runTest() {
    clear(results);
    try {
      const d = await api('/api/ai/local-test', { method: 'POST' });
      const rows = (d && d.results) || [];
      results.append(h('div', { class: 'ai-test ' + (d && d.ok ? 'ok' : 'fail'), style: { marginTop: '14px' } },
        h('div.row', { style: { gap: '8px' } },
          h('span.status', icon(d && d.ok ? 'checkCircle' : 'alert', 18)),
          h('strong', d && d.ok ? `${d.model || 'Your model'} answered` : 'The local model didn’t answer correctly'),
          d && d.seconds != null ? h('span.small.muted', fmtSec(d.seconds)) : null,
          d && d.url ? h('span.small.muted.mono', d.url) : null),
        d && d.error ? h('p.ai-test-error', d.error) : null,
        (d && d.warnings || []).map(w => h('p.small', { style: { color: 'var(--warn)', marginTop: '4px' } }, w)),
        rows.length ? h('div.table-wrap.ai-test-table', { style: { marginTop: '10px' } }, h('table.simple.compact',
          h('caption.sr-only', 'Answers from your local model'),
          h('thead', h('tr', h('th', { scope: 'col' }, 'Made-up merchant'), h('th', { scope: 'col' }, 'Category'), h('th', { scope: 'col' }, 'Lane'), h('th.r', { scope: 'col' }, 'Confidence'))),
          h('tbody', rows.map(x => h('tr', h('td.mono', x.descriptor), h('td', x.category || '—'), h('td', laneChip(x.lane)),
            h('td.r.num', x.confidence != null ? fmtPct(x.confidence) : '—')))))) : null,
        d && !d.ok && !rows.length ? h('p.small.muted', { style: { marginTop: '6px' } },
          'Make sure the Ollama app is running and the model is downloaded (', h('code', `ollama pull ${modelLabel()}`), '), then test again.') : null));
    } catch (e) {
      results.append(h('div.error-box', { role: 'alert', style: { marginTop: '14px' } }, icon('alert', 16), e.message));
    }
  }

  async function doSave(btn, quietOk = false) {
    if (mode === 'addon') { toast('Nothing to change here', { detail: 'The add-on’s own section sets up its AI.' }); return false; }
    const patch = { ai_enabled: mode === 'local', provider: mode === 'local' ? 'ollama' : 'off' };
    if (mode === 'local') { patch.model = model.value.trim(); if (!fixedUrl) patch.ollama_url = url.value.trim() || 'http://127.0.0.1:11434'; }
    const ok = await saveSettings(patch, quietOk ? null : btn, quietOk ? 'Settings saved' : 'AI settings saved');
    if (ok) { loadStatus().catch(() => {}); if (!fixedUrl && store.settings && store.settings.ollama_url) url.value = store.settings.ollama_url; }
    return ok;
  }
  const save = h('button', { class: 'btn btn-primary', type: 'button', onclick: () => doSave(save) }, 'Save');
  const preview = h('button', { class: 'btn', type: 'button', onclick: () => showAiPreview() }, icon('eye', 16), 'See exactly what the AI receives');
  const run = h('button', { class: 'btn', type: 'button' }, icon('sparkle', 16), 'Run categorization');
  run.addEventListener('click', () => runCategorization(run));

  return card('ai', 'AI & privacy', 'Optional. LedgerStitch works fully offline with your rules and built-in rules.',
    h('div',
      radios,
      fields,
      h('div', { style: { marginTop: '14px' } }, statusLine),
      h('hr.divider'),
      h('h3', { style: { fontSize: '14px', marginBottom: '10px' } }, 'The AI never receives'),
      neverList()),
    [preview, run, h('span.spacer'), save]);
}

export function neverList() {
  return h('ul.never-list', ['Amounts or balances', 'Dates', 'Account or card numbers', 'Names of people', 'Addresses or phone numbers', 'Your files'].map(t => h('li', icon('shield', 15), t)));
}

// ---------------------------------------------------------------- Preview ("what the AI receives")
export async function showAiPreview() {
  const body = h('div', h('div.loading', spinner(), 'Building the exact request…'));
  const m = modal({ title: 'What the AI receives', wide: true,
    description: 'This is the complete list that would be sent the next time you run categorization. Nothing else leaves your computer.',
    body, actions: [{ label: 'Close', primary: true, value: true }] });
  try {
    const d = await api('/api/ai/preview');
    const descriptors = d.descriptors || [];
    const n = d.count ?? descriptors.length;
    const full = d.full_prompt || d.payload || '';
    const fullPre = h('pre.payload-pre', { tabindex: '0', 'aria-label': 'Full AI request' }, full);
    const extra = h('div.ai-preview-extra');
    clear(body).append(
      !d.enabled ? h('div.info-box', { style: { marginBottom: '14px' } }, icon('info', 18), h('p', 'AI is currently off, so nothing is being sent. This is what would be sent if you turned it on.')) : null,
      h('div.row-between', { style: { marginBottom: '14px' } },
        h('p', h('strong', plural(n, 'merchant name')), h('span.muted', d.local ? ' · to Ollama on this computer' : '')),
        h('span.chip.ok', icon('shield', 13), 'No amounts · no dates · no account numbers')),
      extra,
      n ? h('ul.payload-pre', { style: { listStyle: 'none', columns: '2 220px', columnGap: '24px' }, tabindex: '0', 'aria-label': 'Merchant names that would be sent' },
        descriptors.map(x => h('li', x))) : h('p.muted', 'Nothing to send — every transaction is already sorted by your rules.'),
      full ? h('details', { style: { marginTop: '16px' } },
        h('summary', { style: { cursor: 'pointer', fontWeight: 600, fontSize: '14px' } }, 'Show full request'),
        h('p.small.muted', { style: { margin: '8px 0' } }, 'The complete text sent to the AI, including instructions and your category list.'),
        fullPre,
        h('div.row', { style: { marginTop: '8px' } }, h('button', { class: 'btn btn-sm', type: 'button', onclick: async () => {
          try { await navigator.clipboard.writeText(full); toast('Copied to clipboard'); } catch { toast('Couldn’t copy', { kind: 'error' }); }
        } }, 'Copy'))) : null,
    );
    document.dispatchEvent(new CustomEvent('ls:ai-preview', { detail: { data: d, add: node => { if (node) extra.append(node); } } }));
  } catch (e) {
    clear(body).append(h('div.error-box', { role: 'alert' }, icon('alert', 16), e.message));
  }
  return m;
}

// ---------------------------------------------------------------- Run categorization
/** Runs POST /api/categorize with toasts. Returns the result, or null on failure. */
export async function runCategorization(btn) {
  let r = null;
  const go = async () => {
    document.dispatchEvent(new CustomEvent('ls:categorize-start', { detail: { btn } }));
    try { r = await api('/api/categorize', { method: 'POST' }); }
    catch (e) { toast('Couldn’t categorize', { kind: 'error', detail: e.message }); }
    finally { document.dispatchEvent(new CustomEvent('ls:categorize-end', { detail: { btn } })); }
  };
  if (btn) await busy(btn, go, 'Categorizing…'); else await go();
  if (!r) return null;
  for (const w of r.warnings || []) toast('About categorization', { kind: 'warn', detail: w });
  const done = (r.by_rules || 0) + (r.by_heuristics || 0) + (r.by_ai || 0);
  const parts = [`${plural(done, 'transaction')} sorted`, `${fmtInt(r.needs_review || 0)} left to review`];
  if (r.sent_descriptors) parts.push(`${plural(r.sent_descriptors, 'merchant name')} sent to AI`);
  toast('Categorization finished', { detail: parts.join(' · ') });
  if ((r.new_categories || []).length) toast('New categories suggested', { detail: r.new_categories.join(', ') });
  refreshReviewBadge();
  document.dispatchEvent(new CustomEvent('ls:categorized', { detail: r }));
  return r;
}
