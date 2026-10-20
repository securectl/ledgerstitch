// LedgerStitch — Import & connect: drop statements, per-file balance check (✓ / ✗ off by $x), imported files
// with delete, accounts. "Connect banks" is a disabled "Coming soon" tile: the backend isn't built yet.
import { api } from './api.js';
import { h, clear, append, icon, toast, modal, busy, plural, money, fmtDateShort, num, spinner, confirmDialog, avatar, chip, card, why, sk } from './ui.js';
import { store, loadAccounts, loadStatus, refreshReviewBadge, accountShort } from './state.js';
import { showUpgrade } from './app.js';
import { setTopActions } from './shell.js';

function catWarnings(r) {
  const w = (r && r.categorize && r.categorize.warnings) || [];
  for (const msg of w) toast('About categorization', { kind: 'warn', detail: msg });
}

const LOW_CONFIDENCE = 0.6;
const ACCEPT = '.csv,.pdf,.txt,.tsv,.ofx,.qfx,.qbo,.qif,.xlsx,.xml,.sta,.mt940,.bai,.bai2,text/csv,application/pdf,application/x-ofx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const ACCEPT_RE = /\.(csv|pdf|txt|tsv|ofx|qfx|qbo|qif|xlsx|xml|sta|mt940|bai2?)$/i;
const FORMAT_LABEL = { CSV: 'CSV', XLSX: 'Excel', PDF: 'PDF', OFX: 'OFX', QFX: 'Quicken (QFX)', QBO: 'QuickBooks (QBO)', QIF: 'QIF', 'CAMT.053': 'CAMT.053', 'CAMT.052': 'CAMT.052', 'CAMT.054': 'CAMT.054', MT940: 'MT940', BAI2: 'BAI2' };
const kindLabel = k => ({ credit_card: 'Credit card', checking: 'Checking', savings: 'Savings' })[k] || 'Account';
const fmtOf = name => (String(name).split('.').pop() || '').toUpperCase().slice(0, 4);

export async function renderImport(main) {
  const fileInput = h('input', { type: 'file', multiple: true, accept: ACCEPT, hidden: true, id: 'file-input' });
  const labelInput = h('input', { class: 'input', id: 'acct-label', placeholder: 'e.g. Chase Ink Business ••4012', maxlength: 80 });
  const files = filesCard();
  const accounts = accountsCard();
  let imported = 0;
  setTopActions(store.accounts.length ? h('a', { class: 'btn', href: '#/review' }, 'Review', icon('arrowRight', 16)) : null);

  // ---- drop zone
  const dz = h('div', { class: 'im-drop', role: 'button', tabindex: '0', 'aria-describedby': 'dz-help', 'aria-label': 'Choose statement files to import, or drop them here' },
    h('span.im-drop-ic', icon('upload', 26)),
    h('b', 'Drop statements'),
    h('span.muted', { id: 'dz-help' }, 'or ', h('span.im-link', 'choose files')),
    h('div.im-fmts', ['CSV', 'PDF', 'OFX', 'QFX', 'Excel'].map(f => chip(f, 'outline'))));
  dz.addEventListener('click', () => fileInput.click());
  dz.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } });
  let depth = 0;
  dz.addEventListener('dragenter', e => { e.preventDefault(); depth++; dz.classList.add('dragover'); });
  dz.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  dz.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; dz.classList.remove('dragover'); } });
  dz.addEventListener('drop', e => { e.preventDefault(); depth = 0; dz.classList.remove('dragover'); handleFiles([...e.dataTransfer.files]); });
  fileInput.addEventListener('change', () => { handleFiles([...fileInput.files]); fileInput.value = ''; });
  const swallow = e => { if (e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files')) e.preventDefault(); };
  window.addEventListener('dragover', swallow);
  window.addEventListener('drop', swallow);

  const dropCard = card({ title: 'Statements', meta: 'Read on this computer', cls: 'im-dropcard', body: [dz, fileInput,
    why('Options', h('div.field', h('label', { for: 'acct-label' }, 'Account name (optional)'), labelInput, h('span.hint', 'Blank: named from the file, like “Chase ••4012”.')),
      h('p', 'Any bank or card: CSV, Excel, PDF, OFX/QFX/QBO, QIF, CAMT, MT940, BAI2. Overlapping files are de-duplicated. A new layout is mapped once and remembered.')) ] });

  const connect = card({ title: 'Connect banks', meta: 'Coming soon', cls: 'im-connect', action: chip('Soon', 'outline'),
    body: [h('ol.im-steps', { 'aria-label': 'Three steps' },
      step(1, 'key', 'Get token'), h('li.im-step-line', { 'aria-hidden': 'true' }), step(2, 'copy', 'Paste here'), h('li.im-step-line', { 'aria-hidden': 'true' }), step(3, 'refresh', 'Auto-sync')),
      h('div.im-token', h('label.input', { title: 'Not available yet' }, icon('link', 16), h('input', { 'aria-label': 'Setup token', placeholder: 'Paste setup token', disabled: true })),
        h('button', { class: 'btn btn-primary', type: 'button', disabled: true, title: 'Bank connections are coming soon' }, icon('link', 18), 'Connect')),
      h('div.im-trust', h('span', icon('lock', 14), 'We never see your bank login'))] });

  clear(main).append(h('div.im', h('div.im-top', connect, dropCard), h('div.im-bottom', files.el, accounts.el)));

  async function handleFiles(list) {
    if (!list.length) return;
    for (const f of list) {
      const row = files.pending(f);
      await uploadOne(f, row);
    }
    await finish();
  }

  async function uploadOne(file, row) {
    if (!ACCEPT_RE.test(file.name)) { row.error(`Not a supported statement (CSV, Excel, PDF, OFX/QFX/QBO or QIF).`); return; }
    const form = new FormData();
    form.append('file', file, file.name);
    if (labelInput.value.trim()) form.append('account_label', labelInput.value.trim());
    try {
      const d = await api('/api/imports', { method: 'POST', form });
      const r = Array.isArray(d) ? d[0] : d;
      if (!r) { row.error('The file was read but no result came back.'); return; }
      if (r.error) {
        if (r.needs_mapping) { row.needsMapping(() => openMapping(file, row)); return; }
        if (r.status === 402) { row.error(r.error || 'Not imported: this needs an upgrade.'); showUpgrade({ data: r }); return; }
        row.error(r.error, () => uploadOne(file, row));
        return;
      }
      catWarnings(r);
      const conf = r.detected && typeof r.detected === 'object' ? num(r.detected.confidence ?? 1) : 1;
      if (r.needs_mapping || (conf < LOW_CONFIDENCE && !r.count)) { row.needsMapping(() => openMapping(file, row)); return; }
      row.done(r, () => openMapping(file, row), () => confirmMapping(file, row, r));
      imported += (r.new ?? r.count) || 0;
    } catch (e) {
      if (e.status === 402) { row.error('Not imported: the Free plan limit was reached.'); return; }
      if (e.data && e.data.needs_mapping) { row.needsMapping(() => openMapping(file, row)); return; }
      row.error(e.message, () => uploadOne(file, row));
    }
  }

  // "New format — mapped automatically": confirming re-sends the inferred mapping so the layout is remembered.
  async function confirmMapping(file, row, r) {
    const d = r.detected || {};
    if (!d.mapping) return openMapping(file, row);
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('mapping', JSON.stringify(d.mapping));
    if (d.account_label) form.append('account_label', d.account_label);
    try {
      const res = await api('/api/imports/mapped', { method: 'POST', form });
      const out = Array.isArray(res) ? res[0] : res;
      row.done({ ...out, detected: { ...(out.detected || {}), recognition: { ...((out.detected || {}).recognition || {}), source: 'confirmed', format: (d.recognition || {}).format } } });
      toast('Layout remembered', { detail: 'Next time this format is imported automatically.' });
    } catch (e) { toast('Couldn’t confirm the columns', { kind: 'error', detail: e.message }); }
  }

  function openMapping(file, row) {
    mappingDialog(file, labelInput.value.trim()).then(async r => {
      if (!r) return;
      row.done(r);
      imported += (r.new ?? r.count) || 0;
      await finish();
    });
  }

  async function finish() {
    if (!imported) return;
    await Promise.allSettled([loadAccounts(true), loadStatus()]);
    files.load(); accounts.load();
    refreshReviewBadge();
    const n = (store.status && store.status.review_count) || 0;
    toast(`${plural(imported, 'transaction')} imported`, { detail: n ? `${n} need a decision` : 'Card payments, transfers and duplicates set aside', action: n ? { label: 'Review', onClick: () => { location.hash = '#/review'; } } : undefined });
    setTopActions(h('a', { class: 'btn btn-primary', href: '#/review' }, 'Review', icon('arrowRight', 16)));
    imported = 0;
  }

  return () => {
    window.removeEventListener('dragover', swallow);
    window.removeEventListener('drop', swallow);
  };
}

const step = (n, ic, label) => h('li.im-step', h('span.im-step-ic', icon(ic, 22), h('b', String(n))), h('span.im-step-l', label));

async function categorizeNow(btn) {
  await busy(btn, async () => {
    try {
      const r = await api('/api/categorize', { method: 'POST' });
      catWarnings({ categorize: r });
      const done = (r.by_rules || 0) + (r.by_heuristics || 0) + (r.by_ai || 0);
      toast('Categorization finished', { detail: `${plural(done, 'transaction')} sorted · ${plural(r.needs_review || 0, 'needs', 'need')} your review` });
      refreshReviewBadge();
    } catch (e) { toast('Couldn’t categorize', { kind: 'error', detail: e.message }); }
  }, 'Categorizing…');
}

// ---------------------------------------------------------------- balance check
/** ✓ Balances match / ✗ Off by $x (+ Find rows) / — nothing to check. `diff` is the unaccounted amount. */
export function reconcileBadge(v, diff, { findHref } = {}) {
  if (v === true) return h('span.im-check.ok', { title: 'The imported rows add up to the statement’s printed totals and balances.' }, icon('checkCircle', 18), h('span', 'Balances match'));
  if (v === false) {
    const d = diff != null && num(diff) > 0 ? money(diff) : null;
    return h('span.im-check-w', h('span.im-check.bad', { title: 'Some transactions may be missing or duplicated. Compare the ledger with the statement.' }, icon('xCircle', 18), h('span.num', d ? `Off by ${d}` : 'Doesn’t match')),
      findHref ? h('a', { class: 'btn btn-sm', href: findHref }, icon('search', 14), 'Find rows') : null);
  }
  return h('span.im-check.na', { title: 'No printed totals or balances to check against (CSV exports usually don’t have them).' }, icon('minus', 18), h('span', 'Nothing to check'));
}

/** "Recognized: Chase CSV (built-in)" / "(learned from your earlier import)" / "New format, please confirm". */
function recognitionLine(rec, d) {
  if (!rec) return null;
  const fmt = FORMAT_LABEL[rec.format] || rec.format || '';
  const inst = (rec.institution || d.institution || '');
  const name = [inst && inst !== 'Unknown' ? inst : '', fmt].filter(Boolean).join(' ');
  let text;
  if (rec.source === 'learned' || rec.source === 'confirmed') text = `${name} · learned`;
  else if (rec.source === 'pack') text = `${name} · format pack`;
  else if (rec.source === 'builtin' || rec.source === 'catalog') text = `${name} · built-in`;
  else if (rec.source === 'inferred') text = `New format${fmt ? ` (${fmt})` : ''} · please confirm`;
  else return null;
  const whyT = Array.isArray(rec.explanation) && rec.explanation.length ? rec.explanation.slice(0, 6).join(' · ') : null;
  return h('span', { title: whyT || null }, text);
}

// ---------------------------------------------------------------- files card
/** Every imported file (uploads in progress first), with the balance check and Delete. Only parsed rows are
 *  kept, never the file, so deleting a file removes the transactions it added. */
function filesCard() {
  const rows = h('div.rows');
  const meta = h('span');
  const pendingHost = h('div.rows.im-pending');
  const catBtn = h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', title: 'Run categorization again', 'aria-label': 'Run categorization again', onclick: e => categorizeNow(e.currentTarget) }, icon('sparkle', 16));
  const el = card({ title: 'Files', meta, cls: 'flush im-files', id: 'imported-files', action: catBtn, body: [pendingHost, rows] });
  function drawMeta(list) {
    const checked = list.filter(i => i.reconciled === true || i.reconciled === false);
    clear(meta).append(checked.length ? [h('span.num', `${checked.filter(i => i.reconciled).length}/${checked.length}`), ' balanced'] : plural(list.length, 'file'));
  }
  async function load() {
    try {
      const imports = await api('/api/imports');
      const list = Array.isArray(imports) ? imports : [];
      drawMeta(list);
      clear(rows);
      if (!list.length && !pendingHost.children.length) { rows.append(h('div.tx-empty', icon('file', 28), h('span.muted', 'No files yet'))); return; }
      append(rows, list.map(i => h('div.trow.im-file',
        h('span.im-fmt', { 'aria-hidden': 'true' }, fmtOf(i.filename)),
        h('div.grow', h('span.t1', { title: i.filename }, i.filename), h('span.t2.num', `${plural(i.transactions ?? i.new ?? 0, 'row')}${i.account_label ? ` · ${i.account_label}` : ''}${i.date ? ` · ${fmtDateShort(i.date.slice(0, 10))}` : ''}`)),
        reconcileBadge(i.reconciled, i.reconcile_difference, { findHref: i.account_id ? `#/ledger?account=${encodeURIComponent(i.account_id)}&year=` : '#/ledger' }),
        h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', 'aria-label': `Delete ${i.filename}`, title: 'Delete this file and its transactions', onclick: e => deleteImport(i, e.currentTarget) }, icon('trash', 15)))));
    } catch (e) { clear(rows).append(h('div.error-box', { style: { margin: '0 24px 16px' } }, icon('alert', 16), e.message)); }
  }
  async function deleteImport(i, btn) {
    const n = i.transactions ?? i.new ?? 0;
    if (!await confirmDialog({ title: `Delete “${i.filename}”?`,
      description: `Removes the ${plural(n, 'transaction')} this file added, including categories you set. Rules stay. You can import it again later.`,
      confirmLabel: 'Delete file', danger: true })) return;
    await busy(btn, async () => {
      try {
        const r = await api(`/api/imports/${encodeURIComponent(i.import_id)}`, { method: 'DELETE' });
        toast('File deleted', { detail: `${plural(r.deleted_transactions, 'transaction')} removed${r.deleted_accounts ? ' · empty account removed' : ''}` });
        await Promise.allSettled([load(), loadStatus(), loadAccounts(true)]);
        document.dispatchEvent(new CustomEvent('ls:accounts'));
        refreshReviewBadge();
      } catch (e) { toast('Couldn’t delete the file', { kind: 'error', detail: e.message }); }
    });
  }
  /** A row for a file being uploaded; returns {done, needsMapping, error}. */
  function pending(file) {
    const st = h('span.im-check', spinner(), h('span', 'Reading…'));
    const sub = h('span.t2', 'Reading and encrypting on this computer');
    const act = h('span.im-act');
    const row = h('div.trow.im-file.is-new', h('span.im-fmt', { 'aria-hidden': 'true' }, fmtOf(file.name)), h('div.grow', h('span.t1', { title: file.name }, file.name), sub), st, act);
    pendingHost.prepend(row);
    const set = (state, subText, warnings) => { clear(sub).append(subText); row.dataset.state = state; row.title = (warnings || []).join('\n'); };
    return {
      done(r, onRemap, onConfirm) {
        const d = typeof r.detected === 'object' && r.detected ? r.detected : { institution: r.detected };
        const conf = d.confidence != null ? num(d.confidence) : null;
        const rec = d.recognition || null;
        const parts = [h('span', `${plural(r.count ?? 0, 'row')}${r.new != null && r.new !== r.count ? ` · ${r.new} new` : ''} · ${d.account_label || r.account_label || (d.account_hint ? `••${d.account_hint}` : 'New account')}`)];
        const rl = recognitionLine(rec, d); if (rl) parts.push(' · ', rl);
        set('ok', parts, r.warnings);
        st.replaceWith(r.reconciled !== undefined ? reconcileBadge(r.reconciled, r.reconcile_difference) : h('span.im-check.ok', icon('checkCircle', 18), h('span', 'Imported')));
        const unconfirmed = rec && (rec.needs_confirmation || rec.source === 'inferred') && d.mapping;
        clear(act).append(
          (r.warnings || []).length ? chip(`${r.warnings.length}`, 'warn', { icon: 'alert', title: r.warnings.join('\n') }) : null,
          onConfirm && unconfirmed ? h('button', { class: 'btn btn-primary btn-sm', type: 'button', onclick: e => busy(e.currentTarget, onConfirm, 'Saving…') }, 'Confirm columns') : null,
          onRemap && d.mapping && (unconfirmed || (conf != null && conf < 0.85)) ? h('button', { class: 'btn btn-sm', type: 'button', onclick: onRemap }, 'Fix columns') : null);
      },
      needsMapping(onMap) {
        set('needs', 'New layout: tell us which columns are which', []);
        st.replaceWith(h('span.im-check.na', icon('info', 18), h('span', 'Map columns')));
        const b = h('button', { class: 'btn btn-primary btn-sm', type: 'button', onclick: onMap }, 'Map columns');
        clear(act).append(b); b.focus();
      },
      error(msg, retry) {
        set('err', h('span', { style: { color: 'var(--danger)' }, role: 'alert' }, msg), []);
        st.replaceWith(h('span.im-check.bad', icon('xCircle', 18), h('span', 'Failed')));
        clear(act).append(retry ? h('button', { class: 'btn btn-sm', type: 'button', onclick: retry }, 'Try again') : null);
      },
    };
  }
  load();
  return { el, load, pending };
}

// ---------------------------------------------------------------- accounts card
function accountsCard() {
  const rows = h('div.rows');
  const meta = h('span');
  const el = card({ title: 'Accounts', meta, cls: 'flush im-accts', body: rows });
  async function load() {
    try {
      await loadAccounts(true);
      const list = store.accounts;
      const total = list.reduce((a, x) => a + (x.transactions || 0), 0);
      clear(meta).append(h('span.num', String(list.length)), ` · ${plural(total, 'transaction')}`);
      clear(rows);
      if (!list.length) { rows.append(h('div.tx-empty', icon('bank', 28), h('span.muted', 'No accounts yet'))); return; }
      append(rows, list.map(a => h('div.trow.im-acct',
        avatar(a.institution || a.label, ''),
        h('div.grow', h('span.t1', a.label), h('span.t2', `${kindLabel(a.kind)}${a.last4 ? ` ••${a.last4}` : ''} · ${plural(a.transactions || 0, 'transaction')}`)),
        chip('Statement', 'outline', { icon: 'file', title: 'Imported from statement files' }),
        h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', 'aria-label': `Delete account ${a.label}`, title: 'Delete account', onclick: e => deleteAccount(a, e.currentTarget) }, icon('trash', 15)))));
    } catch (e) { clear(rows).append(h('div.error-box', { style: { margin: '0 24px 16px' } }, icon('alert', 16), e.message)); }
  }
  async function deleteAccount(a, btn) {
    if (!await confirmDialog({ title: `Delete account “${a.label}”?`,
      description: `Removes the account, its ${plural(a.transactions || 0, 'transaction')}, the files imported into it and rules limited to it.`,
      confirmLabel: 'Delete account', danger: true })) return;
    await busy(btn, async () => {
      try {
        const r = await api(`/api/accounts/${encodeURIComponent(a.id)}`, { method: 'DELETE' });
        toast('Account deleted', { detail: `${plural(r.deleted_transactions, 'transaction')} removed` });
        await Promise.allSettled([load(), loadStatus()]);
        document.dispatchEvent(new CustomEvent('ls:accounts'));
        refreshReviewBadge();
      } catch (e) { toast('Couldn’t delete the account', { kind: 'error', detail: e.message }); }
    });
  }
  document.addEventListener('ls:accounts', load);
  load();
  return { el, load };
}
void accountShort; void modal; void sk;

// ---------------------------------------------------------------- mapping dialog
function guess(cols, patterns) {
  const low = cols.map(c => String(c).toLowerCase());
  for (const p of patterns) { const i = low.findIndex(c => p.test(c)); if (i >= 0) return cols[i]; }
  return '';
}

async function mappingDialog(file, defaultLabel) {
  let preview;
  const loadingModal = modal({ title: 'Reading columns…', body: h('div.loading', spinner(), 'Looking at the first few rows') , actions: [] });
  try {
    const form = new FormData();
    form.append('file', file, file.name);
    preview = await api('/api/imports/preview', { method: 'POST', form });
  } catch (e) {
    loadingModal.close();
    toast('Couldn’t read that file', { kind: 'error', detail: e.message });
    return null;
  }
  loadingModal.close();

  const cols = (preview.columns || []).map(String);
  const rows = preview.rows || [];
  const det = preview.detected && preview.detected.mapping ? preview.detected.mapping : {};
  const opt = (sel, value) => { sel.append(new Option('— Not in this file —', '')); cols.forEach(c => sel.append(new Option(c, c))); sel.value = value || ''; return sel; };

  const dateSel = opt(h('select', { class: 'input', id: 'map-date' }), det.date || guess(cols, [/^date$/, /trans.*date/, /posted/, /date/]));
  const descSel = opt(h('select', { class: 'input', id: 'map-desc' }), det.description || guess(cols, [/^description$/, /desc/, /payee/, /merchant/, /memo/, /name/]));
  const amtSel = opt(h('select', { class: 'input', id: 'map-amount' }), det.amount || guess(cols, [/^amount$/, /amount/, /^amt/]));
  const debitSel = opt(h('select', { class: 'input', id: 'map-debit' }), det.debit || guess(cols, [/debit/, /withdraw/, /money out/, /charge/]));
  const creditSel = opt(h('select', { class: 'input', id: 'map-credit' }), det.credit || guess(cols, [/credit/, /deposit/, /money in/, /payment/]));
  const fmtSel = h('select', { class: 'input', id: 'map-datefmt' },
    new Option('Detect automatically', ''), new Option('MM/DD/YYYY (US)', '%m/%d/%Y'), new Option('DD/MM/YYYY', '%d/%m/%Y'),
    new Option('YYYY-MM-DD', '%Y-%m-%d'), new Option('MM-DD-YYYY', '%m-%d-%Y'), new Option('Mon DD, YYYY', '%b %d, %Y'));
  const label = h('input', { class: 'input', id: 'map-label', value: defaultLabel || file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' '), maxlength: 80, required: true });

  let mode = det.debit || det.credit || (!amtSel.value && (debitSel.value || creditSel.value)) ? 'split' : 'single';
  let negativeIsOut = det.negative_is_out !== false;

  const modeSeg = h('div.segmented', { role: 'group', 'aria-label': 'How amounts appear' });
  const singleBtn = h('button', { type: 'button' }, 'One amount column');
  const splitBtn = h('button', { type: 'button' }, 'Separate debit & credit');
  modeSeg.append(singleBtn, splitBtn);
  const signSeg = h('div.segmented', { role: 'group', 'aria-label': 'Sign convention' });
  const negOut = h('button', { type: 'button' }, 'Negative = money out');
  const posOut = h('button', { type: 'button' }, 'Positive = money out');
  signSeg.append(negOut, posOut);
  const signHint = h('span.hint');

  const amountFields = h('div');
  const tableWrap = h('div.preview-wrap');
  const samples = h('ul.sample-list', { 'aria-live': 'polite' });
  const errEl = h('div', { role: 'alert' });

  function sync() {
    singleBtn.setAttribute('aria-pressed', String(mode === 'single'));
    splitBtn.setAttribute('aria-pressed', String(mode === 'split'));
    negOut.setAttribute('aria-pressed', String(negativeIsOut));
    posOut.setAttribute('aria-pressed', String(!negativeIsOut));
    signHint.textContent = negativeIsOut ? 'Typical for bank accounts: −45.00 is a purchase.' : 'Typical for credit cards: 45.00 is a purchase, −45.00 a payment or refund.';
    clear(amountFields).append(mode === 'single'
      ? h('div.map-grid', { style: { gridTemplateColumns: '1fr 2fr' } },
          h('div.field', h('label', { for: 'map-amount' }, 'Amount'), amtSel),
          h('div.field', h('span', { class: 'label', style: { fontWeight: 600, fontSize: '13px', color: 'var(--ink-2)' } }, 'Which way is money out?'), signSeg, signHint))
      : h('div.map-grid', { style: { gridTemplateColumns: '1fr 1fr' } },
          h('div.field', h('label', { for: 'map-debit' }, 'Money out (debit)'), debitSel),
          h('div.field', h('label', { for: 'map-credit' }, 'Money in (credit)'), creditSel)));
    drawTable();
    drawSamples();
  }

  function roles() {
    const r = {};
    if (dateSel.value) r[dateSel.value] = 'Date';
    if (descSel.value) r[descSel.value] = 'Description';
    if (mode === 'single' && amtSel.value) r[amtSel.value] = 'Amount';
    if (mode === 'split') { if (debitSel.value) r[debitSel.value] = 'Out'; if (creditSel.value) r[creditSel.value] = 'In'; }
    return r;
  }
  function drawTable() {
    const r = roles();
    const t = h('table.preview-table',
      h('thead', h('tr', cols.map(c => h('th', { class: r[c] ? 'picked' : null, scope: 'col' }, r[c] ? h('span.role', r[c]) : null, c)))),
      h('tbody', rows.slice(0, 6).map(row => h('tr', cols.map((c, i) => h('td', { class: r[c] ? 'picked' : null }, row[i] ?? ''))))),
    );
    clear(tableWrap).append(t);
  }
  const cell = (row, col) => { const i = cols.indexOf(col); return i >= 0 ? String(row[i] ?? '').trim() : ''; };
  const parseAmt = v => { if (!v) return NaN; let x = v.replace(/[$,\s]/g, ''); let neg = false; if (/^\(.*\)$/.test(x)) { neg = true; x = x.slice(1, -1); } const n = parseFloat(x); return neg ? -n : n; };
  function drawSamples() {
    clear(samples);
    for (const row of rows.slice(0, 3)) {
      let signed = NaN;
      if (mode === 'single') { const a = parseAmt(cell(row, amtSel.value)); signed = negativeIsOut ? a : -a; }
      else { const o = parseAmt(cell(row, debitSel.value)), i = parseAmt(cell(row, creditSel.value)); signed = (Number.isFinite(i) ? Math.abs(i) : 0) - (Number.isFinite(o) ? Math.abs(o) : 0); }
      const ok = Number.isFinite(signed);
      samples.append(h('li',
        h('span', cell(row, dateSel.value) || h('span.muted', 'no date')),
        h('span', cell(row, descSel.value) || h('span.muted', 'no description')),
        h('span', { class: 'money ' + (ok ? (signed < 0 ? 'neg' : 'pos') : '') }, ok ? `${signed < 0 ? '−' : '+'}${fmtMoney(Math.abs(signed))} ${signed < 0 ? 'out' : 'in'}` : '—'),
      ));
    }
  }
  [dateSel, descSel, amtSel, debitSel, creditSel].forEach(sel => sel.addEventListener('change', sync));
  singleBtn.addEventListener('click', () => { mode = 'single'; sync(); });
  splitBtn.addEventListener('click', () => { mode = 'split'; sync(); });
  negOut.addEventListener('click', () => { negativeIsOut = true; sync(); });
  posOut.addEventListener('click', () => { negativeIsOut = false; sync(); });

  const body = h('div.stack',
    h('div.map-grid',
      h('div.field', h('label', { for: 'map-date' }, 'Date'), dateSel),
      h('div.field', h('label', { for: 'map-desc' }, 'Description'), descSel),
      h('div.field', h('label', { for: 'map-datefmt' }, 'Date format'), fmtSel)),
    h('div.field', h('span', { style: { fontWeight: 600, fontSize: '13px', color: 'var(--ink-2)' } }, 'How does this file show amounts?'), modeSeg),
    amountFields,
    h('div.field', h('label', { for: 'map-label' }, 'Account name'), label, h('span.hint', 'Shown in your ledger and reports, e.g. “Wells Fargo Checking ••2201”.')),
    h('div', h('h3', { style: { fontSize: '14px', marginBottom: '8px' } }, 'Your file'), tableWrap),
    h('div', h('h3', { style: { fontSize: '14px', marginBottom: '8px' } }, 'How we’ll read it'), samples),
    errEl,
  );
  sync();

  const m = modal({
    title: 'Match your columns', wide: true,
    description: `${file.name} — pick what each column means. We’ll remember this layout.`,
    body,
    actions: [
      { label: 'Cancel', value: null },
      { label: 'Import with these columns', primary: true, onClick: async ({ close, button }) => {
        clear(errEl);
        const problems = [];
        if (!dateSel.value) problems.push('Choose the date column.');
        if (!descSel.value) problems.push('Choose the description column.');
        if (mode === 'single' && !amtSel.value) problems.push('Choose the amount column.');
        if (mode === 'split' && !debitSel.value && !creditSel.value) problems.push('Choose the debit or credit column.');
        if (!label.value.trim()) problems.push('Give the account a name.');
        if (problems.length) { errEl.append(h('div.error-box', icon('alert', 16), h('div', problems.map(p => h('div', p))))); return false; }
        const mapping = {
          date: dateSel.value, description: descSel.value,
          amount: mode === 'single' ? amtSel.value : null,
          debit: mode === 'split' ? (debitSel.value || null) : null,
          credit: mode === 'split' ? (creditSel.value || null) : null,
          date_format: fmtSel.value || null,
          negative_is_out: negativeIsOut,
        };
        const form = new FormData();
        form.append('file', file, file.name);
        form.append('mapping', JSON.stringify(mapping));
        form.append('account_label', label.value.trim());
        button.textContent = 'Importing…';
        try {
          const d = await api('/api/imports/mapped', { method: 'POST', form });
          const r = Array.isArray(d) ? d[0] : d;
          close({ filename: file.name, ...r, detected: r.detected || { institution: label.value.trim(), account_label: label.value.trim() } });
        } catch (e) {
          button.textContent = 'Import with these columns';
          if (e.status !== 402) errEl.append(h('div.error-box', icon('alert', 16), e.message));
          return false;
        }
      } },
    ],
  });
  return m.result;
}

