// LedgerStitch — Receipts: the receipts stored in this workspace, matched to transactions; edit, link,
// unlink or delete them. Adding new receipts is an add-on ("receipts" UI slot: renderCard(el, ctx) with
// ctx.data.added(records) to show what it stored).
// Images are fetched with the token header and shown as data: URLs (CSP allows img-src 'self' data:).
import { api } from './api.js';
import { h, clear, icon, toast, modal, busy, confirmDialog, plural, fmtMoney, fmtDate, fmtPct, num, spinner, emptyState, announce } from './ui.js';
import { loadAccounts } from './state.js';
import { setTopActions, setTopMeta } from './shell.js';
import { renderSlot } from './addons.js';
const STATUS = {
  matched: { label: 'Matched', cls: 'ok' },
  manual: { label: 'Linked by you', cls: 'ok' },
  suggested: { label: 'Suggested match', cls: 'warn' },
  unmatched: { label: 'No match yet', cls: 'outline' },
  no_text: { label: 'Couldn’t read', cls: 'danger' },
};
const NEEDS = new Set(['suggested', 'unmatched', 'no_text']);
const FILTERS = [['all', 'All'], ['matched', 'Matched'], ['attention', 'Needs attention']];

// ---------------------------------------------------------------- image cache (data: URLs)
const imageCache = new Map(); // id -> Promise<string|null>
function receiptImage(id) {
  if (!imageCache.has(id)) {
    imageCache.set(id, (async () => {
      const res = await api(`/api/receipts/${encodeURIComponent(id)}/image`, { raw: true, quiet: true });
      const blob = await res.blob();
      return await new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(String(fr.result));
        fr.onerror = () => reject(fr.error);
        fr.readAsDataURL(blob);
      });
    })().catch(() => { imageCache.delete(id); return null; }));
  }
  return imageCache.get(id);
}
const isPdf = r => /pdf/i.test(r.mime || '') || /\.pdf$/i.test(r.filename || '');

/** Image (or PDF placeholder) element that fills itself in once loaded. */
function receiptVisual(r, { large = false } = {}) {
  const box = h('div', { class: 'receipt-visual' + (large ? ' large' : '') });
  if (isPdf(r)) {
    box.append(h('div.receipt-ph', icon('file', large ? 40 : 26), h('span', 'PDF')));
    return box;
  }
  box.append(h('div.receipt-ph', spinner()));
  receiptImage(r.id).then(url => {
    clear(box);
    if (!url) { box.append(h('div.receipt-ph', icon('receipt', large ? 40 : 26), h('span', 'No preview'))); return; }
    const img = h('img', { src: url, alt: `Receipt${r.merchant ? ` from ${r.merchant}` : ''}${r.date ? `, ${fmtDate(r.date)}` : ''}`, decoding: 'async' });
    img.addEventListener('error', () => { clear(box).append(h('div.receipt-ph', icon('receipt', large ? 40 : 26), h('span', 'Preview not supported'))); });
    box.append(img);
  });
  return box;
}

/** Paperclip button for Ledger/Review rows. */
export function receiptButton(receiptId, label) {
  return h('button', { class: 'icon-btn clip-btn', type: 'button', title: 'View receipt', 'aria-label': `View receipt${label ? ` for ${label}` : ''}`,
    onclick: e => { e.stopPropagation(); showReceipt(receiptId); } }, icon('paperclip', 16));
}

/** Receipt viewer dialog. `rec` is optional (fetched if missing). */
export async function showReceipt(id, rec) {
  const body = h('div', h('div.loading', spinner(), 'Opening receipt…'));
  const m = modal({ title: 'Receipt', wide: true, body, actions: [{ label: 'Close', primary: true, value: true }] });
  try {
    let r = rec;
    if (!r) {
      const list = await api('/api/receipts');
      r = (Array.isArray(list) ? list : (list && list.items) || []).find(x => x.id === id) || { id, filename: 'Receipt' };
    }
    const facts = [
      ['Merchant', r.merchant], ['Date', r.date ? fmtDate(r.date) : null], ['Total', r.total != null ? fmtMoney(r.total) : null],
      ['Subtotal', r.subtotal != null ? fmtMoney(r.subtotal) : null], ['Tax', r.tax != null ? fmtMoney(r.tax) : null], ['Tip', r.tip != null ? fmtMoney(r.tip) : null],
      ['Card', r.card_last4 ? `${r.card_brand ? r.card_brand + ' ' : ''}•••• ${r.card_last4}` : (r.payment_method || null)],
    ].filter(([, v]) => v);
    const pdfLink = isPdf(r) ? h('button', { class: 'btn btn-sm', type: 'button', onclick: async e => {
      await busy(e.currentTarget, async () => {
        try {
          const res = await api(`/api/receipts/${encodeURIComponent(r.id)}/image`, { raw: true });
          const url = URL.createObjectURL(await res.blob());
          const a = h('a', { href: url, download: r.filename || 'receipt.pdf', rel: 'noopener' });
          document.body.append(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 4000);
        } catch (err) { toast('Couldn’t open the PDF', { kind: 'error', detail: err.message }); }
      });
    } }, icon('download', 15), 'Download PDF') : null;
    clear(body).append(h('div.receipt-viewer',
      h('div', receiptVisual(r, { large: true }), pdfLink ? h('div', { style: { marginTop: '10px' } }, pdfLink) : null),
      h('div',
        h('div.row', { style: { gap: '8px', marginBottom: '12px' } }, statusChip(r.status), cardChip(r)),
        facts.length ? h('dl.receipt-facts', facts.map(([k, v]) => [h('dt', k), h('dd', v)])) : h('p.muted', 'No details were read from this receipt.'),
        h('p.small.muted', { style: { marginTop: '12px' } }, r.filename || '', r.ocr_engine ? ` · read with ${r.ocr_engine}` : '',
          r.ocr_confidence != null ? ` (${fmtPct(r.ocr_confidence)} confident)` : ''),
        h('a', { class: 'btn btn-sm', href: '#/receipts', style: { marginTop: '12px' }, onclick: () => m.close() }, icon('receipt', 15), 'All receipts'))));
  } catch (e) {
    clear(body).append(h('div.error-box', { role: 'alert' }, icon('alert', 16), e.message));
  }
  return m;
}

function statusChip(st) {
  const s = STATUS[st] || { label: st || 'Unknown', cls: 'outline' };
  return h('span', { class: `chip ${s.cls === 'danger' ? 'danger' : s.cls}` }, s.label);
}
function cardChip(r) {
  if (!r.card_last4) return r.payment_method && r.payment_method !== 'card' ? h('span.chip.outline', r.payment_method[0].toUpperCase() + r.payment_method.slice(1)) : null;
  return h('span.chip.outline', { title: r.card_brand || 'Card' }, icon('key', 12), `${r.card_brand ? r.card_brand + ' ' : ''}•••• ${r.card_last4}`);
}

// ---------------------------------------------------------------- screen
export async function renderReceipts(main, params, ctx) {
  await loadAccounts().catch(() => null);
  let receipts = [];
  let filter = FILTERS.some(([k]) => k === params.get('filter')) ? params.get('filter') : 'all';

  const seg = h('div.seg', { role: 'group', 'aria-label': 'Show receipts' });
  const progress = h('div', { 'aria-live': 'polite' });
  const grid = h('div');
  setTopActions();
  setTopMeta(h('span.chip.outline', { title: 'Matching is still improving: check each receipt and edit anything we got wrong.' }, 'Beta'));
  const addSlot = h('div.rc-add');
  clear(main).append(h('div.rc', h('div.rc-tools', seg), addSlot, progress, grid));
  const slotCleanup = renderSlot('receipts', addSlot, { isCurrent: ctx.isCurrent, data: { added } });

  try {
    const d = await api('/api/receipts');
    receipts = Array.isArray(d) ? d : (d && d.items) || [];
  } catch (e) {
    if (!ctx.isCurrent()) return cleanup;
    grid.append(h('div.error-box', { role: 'alert', style: { marginTop: '20px' } }, icon('alert', 16), `Couldn’t load your receipts: ${e.message}`));
  }
  draw();

  function counts() {
    return {
      all: receipts.length,
      matched: receipts.filter(r => r.status === 'matched' || r.status === 'manual').length,
      attention: receipts.filter(r => NEEDS.has(r.status)).length,
    };
  }
  function drawSeg() {
    const c = counts();
    clear(seg).append(...FILTERS.map(([k, l]) => h('button', { type: 'button', 'aria-pressed': String(filter === k), onclick: () => { filter = k; draw(); } },
      l, h('span.seg-count', fmtCount(c[k])))));
  }
  function visible() {
    if (filter === 'matched') return receipts.filter(r => r.status === 'matched' || r.status === 'manual');
    if (filter === 'attention') return receipts.filter(r => NEEDS.has(r.status));
    return receipts;
  }
  function draw() {
    drawSeg();
    clear(grid);
    if (!receipts.length) {
      grid.append(h('div.card', h('div.tx-empty', icon('receipt', 28), h('span.muted', 'No receipts yet'), h('span.legal', 'Matched receipts show a paperclip in the ledger'))));
      return;
    }
    const list = visible();
    if (!list.length) {
      grid.append(h('div.card', h('div.tx-empty', icon('checkCircle', 28), h('span.muted', filter === 'attention' ? 'Nothing needs attention' : 'No matched receipts yet'),
        h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { filter = 'all'; draw(); } }, 'Show all'))));
      return;
    }
    grid.append(h('div.receipt-grid', { role: 'list', 'aria-label': 'Receipts' }, list.map(receiptCard)));
  }
  function replace(rec) {
    const i = receipts.findIndex(x => x.id === rec.id);
    if (i >= 0) receipts[i] = rec; else receipts.unshift(rec);
    const old = grid.querySelector(`[data-receipt="${cssEsc(rec.id)}"]`);
    const focusedId = old && old.contains(document.activeElement) ? document.activeElement.id : null;
    if (old && visible().some(x => x.id === rec.id)) {
      old.replaceWith(receiptCard(rec));
      drawSeg();
      if (focusedId) document.getElementById(focusedId)?.focus();
    } else draw();
  }

  // -------------------------------------------------------------- card
  function receiptCard(r) {
    const id = safeId(r.id);
    const field = (key, label, attrs) => {
      const inp = h('input', { class: 'input input-sm', id: `rc-${key}-${id}`, ...attrs });
      inp.addEventListener('change', () => commit(r, key, inp));
      return h('div.field', h('label', { for: inp.id }, label), inp);
    };
    const fields = h('div.receipt-fields',
      field('merchant', 'Merchant', { value: r.merchant || '', maxlength: 120, placeholder: 'Merchant name', autocomplete: 'off' }),
      field('date', 'Date', { type: 'date', value: r.date ? String(r.date).slice(0, 10) : '' }),
      field('total', 'Total', { value: r.total != null ? num(r.total).toFixed(2) : '', inputmode: 'decimal', placeholder: '0.00', autocomplete: 'off' }),
      field('card_last4', 'Card last 4', { value: r.card_last4 || '', inputmode: 'numeric', maxlength: 4, placeholder: '1234', autocomplete: 'off', pattern: '[0-9]{4}' }));
    const extras = [r.subtotal != null ? `Subtotal ${fmtMoney(r.subtotal)}` : null, r.tax != null ? `Tax ${fmtMoney(r.tax)}` : null,
      r.tip != null ? `Tip ${fmtMoney(r.tip)}` : null].filter(Boolean);
    const del = h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', 'aria-label': `Delete receipt ${r.filename || ''}`, title: 'Delete receipt', onclick: e => remove(r, e.currentTarget) }, icon('trash', 15));
    return h('article', { class: `card receipt-card status-${r.status || 'unmatched'}`, role: 'listitem', dataset: { receipt: r.id }, 'aria-labelledby': `rc-t-${id}` },
      h('button', { class: 'receipt-thumb', type: 'button', 'aria-label': `View receipt image ${r.filename || ''}`, onclick: () => showReceipt(r.id, r) }, receiptVisual(r)),
      h('div.receipt-main',
        h('div.row-between.receipt-head',
          h('div', { style: { minWidth: 0 } },
            h('h3', { id: `rc-t-${id}`, class: 'receipt-title' }, r.merchant || 'Unknown merchant'),
            h('p.small.muted.receipt-file', { title: r.filename || '' }, r.filename || 'Receipt', r.added ? ` · added ${fmtDate(r.added)}` : '')),
          h('div.row', { style: { gap: '6px', justifyContent: 'flex-end' } }, statusChip(r.status), cardChip(r))),
        r.status === 'no_text' ? h('div.warning-box', { style: { marginTop: '12px' } }, icon('alert', 16),
          h('p', 'We couldn’t read any text on this image. Type the merchant, date and total below and we’ll match it.')) : null,
        fields,
        h('p.legal.receipt-meta', { title: r.ocr_engine ? `Read on this computer with ${r.ocr_engine}` : null }, extras.join(' · '), extras.length && r.ocr_confidence != null ? ' · ' : '',
          r.ocr_confidence != null ? `${fmtPct(r.ocr_confidence)} confident` : ''),
        matchSection(r),
        del));
  }

  function matchSection(r) {
    const cands = (r.candidates || []).filter(c => c && c.txn_id);
    if (r.txn_id) {
      const t = r.txn || cands.find(c => c.txn_id === r.txn_id);
      const reasons = r.match_reasons || (t && t.reasons) || [];
      const unlink = h('button', { class: 'btn btn-sm', type: 'button', onclick: e => link(r, null, e.currentTarget) }, 'Unlink');
      return h('div.match-box.matched',
        h('div.row-between',
          h('span.match-label', icon('link', 14), r.status === 'manual' ? 'Linked by you' : `Matched${r.match_score != null ? ` · ${fmtPct(r.match_score)} sure` : ''}`),
          unlink),
        t ? txnLine(t) : h('p.small.muted', 'Linked to a transaction in your ledger.',),
        reasonList(reasons),
        h('a', { class: 'small', href: `#/ledger?q=${encodeURIComponent((t && t.description) || r.merchant || '')}` }, 'Show in ledger'));
    }
    if (!cands.length) {
      return h('div.match-box',
        h('span.match-label', icon('search', 14), 'No matching transaction yet'),
        h('p.small.muted', { style: { marginTop: '4px' } }, r.status === 'no_text'
          ? 'Add the total and date above to look for a match.'
          : 'Check the total, date and card above — or import the statement that has this charge.'));
    }
    return h('div.match-box',
      h('span.match-label', icon('link', 14), r.status === 'suggested' ? 'Is this the transaction?' : 'Possible transactions'),
      h('ul.cand-list', cands.slice(0, 4).map((c, i) => h('li', { class: 'cand' + (i === 0 && r.status === 'suggested' ? ' best' : '') },
        h('div', { style: { minWidth: 0 } }, txnLine(c), reasonList(c.reasons || [])),
        h('button', { class: 'btn btn-sm' + (i === 0 && r.status === 'suggested' ? ' btn-primary' : ''), type: 'button',
          'aria-label': `Link receipt to ${c.description || 'transaction'} on ${fmtDate(c.date)}`,
          onclick: e => link(r, c.txn_id, e.currentTarget) }, icon('link', 14), 'Link')))));
  }
  function txnLine(t) {
    const amt = num(t.amount);
    return h('div.txn-line',
      h('span.num.muted', fmtDate(t.date)),
      h('span.desc', { title: t.description || '' }, t.description || '—'),
      h('span.money', fmtMoney(Math.abs(amt))),
      t.account_label ? h('span.small.muted.acct', t.account_label) : null);
  }
  function reasonList(reasons) {
    if (!reasons || !reasons.length) return null;
    // Negative evidence (mismatches, wrong direction) gets a warning icon, not a check.
    const negative = /mismatch|differs|off by|earlier|not card/i;
    return h('ul.reason-list', reasons.map(x => negative.test(x)
      ? h('li.reason-neg', icon('alert', 12), x)
      : h('li', icon('check', 12), x)));
  }

  // -------------------------------------------------------------- actions
  async function commit(r, key, inp) {
    let v = inp.value.trim();
    if (key === 'total') {
      if (v === '') v = null;
      else { const n = parseFloat(v.replace(/[$,\s]/g, '')); if (!Number.isFinite(n) || n < 0) { inp.setAttribute('aria-invalid', 'true'); toast('Enter the total as a number, like 42.18', { kind: 'warn' }); return; } v = Math.round(n * 100) / 100; }
    }
    if (key === 'card_last4' && v && !/^\d{4}$/.test(v)) { inp.setAttribute('aria-invalid', 'true'); toast('Card last 4 should be 4 digits', { kind: 'warn' }); return; }
    if (key === 'date' && v === '') v = null;
    if (key === 'merchant' && v === '') v = null;
    inp.removeAttribute('aria-invalid');
    try {
      const rec = await api(`/api/receipts/${encodeURIComponent(r.id)}`, { method: 'PATCH', json: { [key]: v } });
      const before = r.status;
      replace(rec);
      if (rec.status !== before && (rec.status === 'matched' || rec.status === 'suggested')) {
        toast(rec.status === 'matched' ? 'Receipt matched' : 'Found a likely match', { detail: rec.merchant || rec.filename });
      } else announce('Saved');
    } catch (e) { toast('Couldn’t save that change', { kind: 'error', detail: e.message }); }
  }
  async function link(r, txnId, btn) {
    await busy(btn, async () => {
      try {
        const rec = await api(`/api/receipts/${encodeURIComponent(r.id)}/match`, { method: 'POST', json: { txn_id: txnId } });
        replace(rec);
        toast(txnId ? 'Receipt linked' : 'Receipt unlinked', { detail: txnId ? 'A paperclip now shows on that transaction in your ledger.' : 'It’s back in Needs attention.' });
      } catch (e) { toast(txnId ? 'Couldn’t link the receipt' : 'Couldn’t unlink the receipt', { kind: 'error', detail: e.message }); }
    });
  }
  async function remove(r, btn) {
    if (!await confirmDialog({ title: 'Delete this receipt?', description: 'The image is removed from your workspace. The transaction itself isn’t affected.', confirmLabel: 'Delete receipt', danger: true })) return;
    await busy(btn, async () => {
      try {
        await api(`/api/receipts/${encodeURIComponent(r.id)}`, { method: 'DELETE' });
        receipts = receipts.filter(x => x.id !== r.id);
        imageCache.delete(r.id);
        draw();
        toast('Receipt deleted');
      } catch (e) { toast('Couldn’t delete the receipt', { kind: 'error', detail: e.message }); }
    });
  }
  /** Records an add-on just stored: show them first. */
  function added(recs) {
    for (const rec of (recs || []).slice().reverse()) {
      const i = receipts.findIndex(x => x.id === rec.id);
      if (i >= 0) receipts[i] = rec; else receipts.unshift(rec);
    }
    if (ctx.isCurrent()) draw();
  }

  return () => slotCleanup();
}

const fmtCount = n => (n > 999 ? '999+' : String(n));
function safeId(v) { return String(v).replace(/[^a-z0-9_-]/gi, '_'); }
function cssEsc(v) { return (window.CSS && CSS.escape) ? CSS.escape(v) : String(v).replace(/["\\]/g, '\\$&'); }
