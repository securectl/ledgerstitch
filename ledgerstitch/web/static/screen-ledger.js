// LedgerStitch — Ledger: clean rows grouped by month, a docked bulk bar and a detail panel
// (bottom sheet under 1100px). Filters, search, sort, paging, bulk edit/delete, rules ("remember"),
// receipts, the date/direction chip (#/ledger?date_from=…&direction=…) and the "ledger-toolbar" add-on slot.
import { api, listOf } from './api.js';
import { h, clear, append, icon, toast, plural, fmtDate, fmtDateShort, fmtRange, num, signedAmount, money, LANES, LANE_LABEL, SOURCE_LABEL,
  debounce, isTypingTarget, isModalOpen, confirmDialog, busy, avatar, chip, laneTag, displayMerchant, progressBar, sk, why, announce } from './ui.js';
import { modelName } from './ai.js';
import { showReceipt } from './screen-receipts.js';
import { store, loadCategories, loadAccounts, loadSettings, accountLabel, accountShort, categoryByName, categoryOptions, taxYear, refreshReviewBadge } from './state.js';
import { setTopActions, bindScreenContext } from './shell.js';
import { renderSlot } from './addons.js';
import { exportButton } from './export-menu.js';
import { firstRunCard } from './states.js';

const PAGE_SIZES = [50, 100, 250, 500];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const TABS = [['all', 'All'], ['review', 'Review'], ['business', 'Business'], ['personal', 'Personal'], ['income', 'Income'], ['transfer', 'Transfers']];
const STATUS_LABEL = { review: 'Review', auto: 'Auto', approved: 'Approved' };
const narrow = () => window.matchMedia('(max-width: 1099px)').matches;

export async function renderLedger(main, params, ctx) {
  await Promise.all([loadCategories(), loadAccounts(), loadSettings().catch(() => null)]);
  const ty = taxYear();
  const f = {
    q: params.get('q') || '', lane: params.get('lane') || '', category: params.get('category') || '',
    account: params.get('account') || '', status: params.get('status') || '',
    year: params.has('year') ? params.get('year') : (params.get('date_from') || params.get('date_to') ? '' : String(ty)),
    // From “Ask a question” / Subscriptions: a date range and money direction (shown as a removable chip)
    date_from: params.get('date_from') || '', date_to: params.get('date_to') || '', direction: params.get('direction') || '',
  };
  let sort = { key: 'date', dir: 'desc' };
  let limit = 100, offset = 0, total = 0, exact = true;
  let rows = [];
  let counts = null;
  const selected = new Set();
  let lastClicked = null;
  let activeId = params.get('id') || null;
  let sheetOpen = false;
  let reqSeq = 0;

  if (!store.accounts.length) {
    const any = listOf(await api('/api/transactions', { query: { limit: 1, offset: 0 } })).items.length;
    if (!any) { clear(main).append(firstRunCard()); return; }
  }
  setTopActions(exportButton({ year: () => Number(f.year) || ty }));

  // -------------------------------------------------------------- toolbar
  const q = h('input', { type: 'search', placeholder: 'Search', 'aria-label': 'Search transactions', value: f.q, autocomplete: 'off' });
  const search = h('label.input.sm.lg-search', icon('search', 16), q, h('span.kbd', { 'aria-hidden': 'true' }, '/'));
  const tabs = h('div.seg.lg-tabs', { role: 'group', 'aria-label': 'Show' });
  const tabOf = () => (f.status === 'review' && !f.lane ? 'review' : f.lane || 'all');
  function drawTabs() {
    clear(tabs);
    for (const [id, label] of TABS) {
      const n = counts ? (id === 'all' ? counts.total : id === 'review' ? counts.needs_review : counts[id]) : null;
      tabs.append(h('button', { type: 'button', 'aria-pressed': String(tabOf() === id), onclick: () => {
        if (id === 'all') { f.lane = ''; if (f.status === 'review') f.status = ''; }
        else if (id === 'review') { f.lane = ''; f.status = 'review'; }
        else { f.lane = id; if (f.status === 'review') f.status = ''; }
        statusSel.value = f.status; offset = 0; selected.clear(); drawTabs(); load();
      } }, label, n != null ? h('span.num', n > 999 ? '999+' : String(n)) : null));
    }
  }
  const acctSel = h('select', { class: 'pill', 'aria-label': 'Account' }, new Option('All accounts', ''), ...store.accounts.map(a => new Option(accountShort(a.id), a.id)));
  acctSel.value = f.account;
  const catSel = categoryOptions(h('select', { class: 'pill lg-catpill', 'aria-label': 'Category' }), f.category, { includeAll: true, allLabel: 'All categories' });
  const years = [...new Set([ty + 1, ty, ty - 1, ty - 2, Number(f.year) || ty])].filter(Boolean).sort((a, b) => b - a);
  const yearSel = h('select', { class: 'pill', 'aria-label': 'Year' }, new Option('All years', ''), ...years.map(y => new Option(String(y), String(y))));
  yearSel.value = f.year;
  const statusSel = h('select', { class: 'pill', 'aria-label': 'Status' }, new Option('Any status', ''),
    new Option('Needs review', 'review'), new Option('Auto-sorted', 'auto'), new Option('Approved', 'approved'));
  statusSel.value = f.status;
  const rangeSlot = h('span.filter-range');
  const clearBtn = h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => {
    q.value = ''; acctSel.value = ''; catSel.value = ''; statusSel.value = ''; yearSel.value = String(ty);
    Object.assign(f, { q: '', lane: '', category: '', account: '', status: '', year: String(ty), date_from: '', date_to: '', direction: '' });
    offset = 0; selected.clear(); drawRange(); drawTabs(); load();
  } }, 'Clear');
  function drawRange() {
    clear(rangeSlot);
    if (!f.date_from && !f.date_to && !f.direction) return;
    const parts = [];
    if (f.date_from || f.date_to) parts.push(f.date_from && f.date_to ? fmtRange(f.date_from, f.date_to) : f.date_from ? `From ${fmtDate(f.date_from)}` : `Through ${fmtDate(f.date_to)}`);
    if (f.direction) parts.push(f.direction === 'out' ? 'Money out' : 'Money in');
    rangeSlot.append(h('span.chip.primary.filter-chip', icon('calendar', 13), parts.join(' · '),
      h('button', { class: 'chip-x', type: 'button', 'aria-label': 'Remove this date / direction filter', onclick: () => {
        Object.assign(f, { date_from: '', date_to: '', direction: '' }); if (!f.year) { f.year = String(ty); yearSel.value = f.year; }
        offset = 0; drawRange(); load();
      } }, icon('x', 12))));
  }
  const onFilter = () => {
    Object.assign(f, { q: q.value.trim(), category: catSel.value, account: acctSel.value, status: statusSel.value, year: yearSel.value });
    offset = 0; selected.clear(); drawTabs(); load();
  };
  q.addEventListener('input', debounce(onFilter, 300));
  q.addEventListener('keydown', e => { if (e.key === 'Escape') { q.value = ''; onFilter(); q.blur(); } if (e.key === 'ArrowDown') { e.preventDefault(); focusRow(0); } });
  [acctSel, catSel, statusSel].forEach(s => s.addEventListener('change', onFilter));
  yearSel.addEventListener('change', () => { onFilter(); loadCounts(); });
  // Context for add-ons (the "ledger-toolbar" slot and the top bar): the selection, and a refresh after changes.
  const askOpts = { screen: 'ledger', getSelection: () => [...selected], onApplied: async () => { selected.clear(); await load(); refreshReviewBadge(); } };
  const toolSlot = h('span.lg-addons');
  const more = h('details.lg-more',
    h('summary', { class: 'btn btn-sm icon', 'aria-label': 'More filters', title: 'More filters' }, icon('filter', 16)),
    h('div.lg-more-b', h('label', { class: 'lg-more-l' }, 'Category', catSel), h('label', { class: 'lg-more-l' }, 'Status', statusSel), clearBtn));
  const toolbar = h('div.lg-tools', { role: 'search' }, search, tabs, rangeSlot, h('span.lg-sp'), acctSel, yearSel, more, toolSlot);
  const slotCleanup = renderSlot('ledger-toolbar', toolSlot, { isCurrent: ctx.isCurrent, data: askOpts });

  // -------------------------------------------------------------- table
  const headCheck = h('button.lg-check', { type: 'button', role: 'checkbox', 'aria-checked': 'false', 'aria-label': 'Select all on this page' }, icon('check', 12));
  headCheck.addEventListener('click', () => {
    const all = rows.length && rows.every(t => selected.has(t.id));
    for (const t of rows) all ? selected.delete(t.id) : selected.add(t.id);
    drawBody(); drawBulk();
  });
  const head = h('div.lg-head', { role: 'row' });
  const scroll = h('div.lg-scroll', { role: 'table', 'aria-label': 'Transactions', 'aria-busy': 'true' });
  const pager = h('div.lg-pager');
  const bulkDock = h('div.lg-bulk-dock');
  const table = h('section.card.lg-table', { 'aria-label': 'Transactions' }, head, scroll, pager, bulkDock);
  const panel = h('aside.card.lg-panel', { 'aria-label': 'Transaction details' });
  const scrim = h('div.scrim.lg-scrim', { hidden: true, onclick: () => closeSheet() });

  const COLS = [['date', 'Date'], ['description', 'Merchant'], ['category', 'Category'], ['lane', 'Lane'], ['amount', 'Amount']];
  function drawHead() {
    clear(head).append(h('span.lg-c.lg-cb', headCheck), COLS.map(([key, label]) => {
      const on = sort.key === key;
      return h('span', { class: `lg-c lg-h-${key}${key === 'amount' ? ' lg-amt' : ''}`, role: 'columnheader', 'aria-sort': on ? (sort.dir === 'asc' ? 'ascending' : 'descending') : null },
        h('button.lg-sort', { type: 'button', onclick: () => setSort(key) }, label, on ? icon(sort.dir === 'asc' ? 'arrowUp' : 'arrowDown', 12) : null));
    }));
  }
  function setSort(key) {
    sort = sort.key === key ? { key, dir: sort.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: key === 'date' || key === 'amount' ? 'desc' : 'asc' };
    drawHead(); offset = 0; load();
  }

  const anyFilter = () => f.q || f.lane || f.category || f.account || f.status || f.date_from || f.date_to || f.direction;

  function drawBody() {
    clear(scroll);
    const n = rows.filter(t => selected.has(t.id)).length;
    headCheck.setAttribute('aria-checked', n && n === rows.length ? 'true' : n ? 'mixed' : 'false');
    headCheck.classList.toggle('is-mixed', n > 0 && n < rows.length);
    clear(headCheck).append(icon(n > 0 && n < rows.length ? 'minus' : 'check', 12));
    if (!rows.length) {
      scroll.append(h('div.lg-empty', icon('search', 28), h('b', 'No matches'),
        anyFilter() ? h('button', { class: 'btn btn-sm', type: 'button', onclick: () => clearBtn.click() }, 'Clear filters') : h('a', { class: 'btn btn-sm', href: '#/import' }, 'Import statements')));
      return;
    }
    if (sort.key === 'date') {
      const groups = [];
      for (const t of rows) {
        const key = String(t.date).slice(0, 7);
        let g = groups[groups.length - 1];
        if (!g || g.key !== key) { g = { key, rows: [] }; groups.push(g); }
        g.rows.push(t);
      }
      for (const g of groups) {
        const inn = g.rows.filter(t => !t.duplicate_of && t.direction !== 'out').reduce((a, t) => a + num(t.amount), 0);
        const out = g.rows.filter(t => !t.duplicate_of && t.direction === 'out').reduce((a, t) => a + num(t.amount), 0);
        const label = `${MONTHS[+g.key.slice(5, 7) - 1]} ${g.key.slice(0, 4)}`;
        scroll.append(h('div.lg-month', { role: 'rowgroup', 'aria-label': label },
          h('div.lg-month-h', { role: 'row' }, h('b', label), h('span.muted.num', String(g.rows.length)),
            h('span.lg-sub',
              inn ? h('span.lg-in', { title: 'Money in on this page' }, icon('arrowDown', 14), money(inn, { sign: true })) : null,
              out ? h('span.lg-out', { title: 'Money out on this page' }, icon('arrowUp', 14), money(-out)) : null)),
          g.rows.map(rowEl)));
      }
    } else {
      append(scroll, rows.map(rowEl));
    }
  }

  function catCell(t) {
    const c = t.category || 'Uncategorized';
    if (t.status === 'review') {
      const conf = Math.round(num(t.confidence) * 100);
      return [chip(`${c}?`, 'dashed', { title: conf ? `Not sure: ${conf}% confident` : 'No suggestion yet' }), conf ? h('span.lg-conf.num', { title: 'Confidence' }, `${conf}%`) : null];
    }
    return chip(c, '', { title: c });
  }

  function rowEl(t) {
    const on = selected.has(t.id), act = t.id === activeId;
    const name = displayMerchant(t);
    const v = signedAmount(t);
    const cb = h('button.lg-check', { type: 'button', role: 'checkbox', 'aria-checked': String(on), 'aria-label': `Select ${name}`, tabindex: '-1' }, icon('check', 12));
    cb.addEventListener('click', e => { e.stopPropagation(); toggleSelect(t, e.shiftKey); });
    const flags = [
      t.receipt_id ? h('button.lg-flag', { type: 'button', title: 'View receipt', 'aria-label': `View receipt for ${name}`, tabindex: '-1', onclick: e => { e.stopPropagation(); showReceipt(t.receipt_id); } }, icon('paperclip', 14)) : null,
      t.note ? h('span.lg-flag', { title: `Note: ${t.note}` }, icon('file', 14)) : null,
      t.duplicate_of ? h('span.chip.outline.lg-mini', { title: 'Same transaction appeared in another file; excluded from totals' }, 'Duplicate') : null,
      t.transfer_group ? h('span.lg-flag', { title: 'Matched with the other side of this payment/transfer' }, icon('repeat', 14)) : null,
      t.refund_of ? h('span.chip.outline.lg-mini', { title: 'Nets against the original purchase' }, 'Refund') : null,
    ];
    const r = h('div', { class: 'lg-row' + (on ? ' is-sel' : '') + (act ? ' is-active' : '') + (t.duplicate_of ? ' is-excluded' : ''), role: 'row', tabindex: act ? '0' : '-1',
      dataset: { id: t.id }, 'aria-selected': String(act), 'aria-label': `${fmtDateShort(t.date)}, ${name}, ${money(v, { sign: v > 0 })}` },
      h('span.lg-c.lg-cb', { role: 'cell' }, cb),
      h('span.lg-c.lg-date.num', { role: 'cell' }, fmtDateShort(t.date)),
      h('span.lg-c.lg-merch', { role: 'cell', title: t.description },
        avatar(name, 'sm'),
        h('span.lg-m', h('span.t1', h('span.lg-name', name), flags, t.status === 'review' ? h('i.lg-rv', { title: 'Needs review' }) : null),
          h('span.t2', { dataset: { d: fmtDateShort(t.date) } }, t.account_label ? accountShort(t.account_id) || t.account_label : accountLabel(t.account_id)))),
      h('span.lg-c.lg-cat', { role: 'cell' }, catCell(t)),
      h('span.lg-c.lg-lane', { role: 'cell' }, laneTag(t.lane)),
      h('span', { class: 'lg-c lg-amt amt' + (v > 0 ? ' in' : ''), role: 'cell' }, money(v, { sign: v > 0 })));
    r.addEventListener('click', () => openRow(t.id));
    return r;
  }

  function toggleSelect(t, shift) {
    if (shift && lastClicked) {
      const ids = rows.map(x => x.id);
      const [a, b] = [ids.indexOf(lastClicked), ids.indexOf(t.id)].sort((x, y) => x - y);
      const turnOn = !selected.has(t.id);
      for (const id of ids.slice(a, b + 1)) turnOn ? selected.add(id) : selected.delete(id);
    } else if (selected.has(t.id)) selected.delete(t.id); else selected.add(t.id);
    lastClicked = t.id;
    drawBody(); drawBulk();
    focusRow(rows.findIndex(x => x.id === t.id));
  }

  // -------------------------------------------------------------- keyboard on rows
  function focusRow(i) {
    const els = [...scroll.querySelectorAll('.lg-row')];
    if (!els.length) return;
    const el = els[Math.max(0, Math.min(els.length - 1, i))];
    for (const x of els) x.tabIndex = -1;
    el.tabIndex = 0;
    el.focus();
  }
  scroll.addEventListener('keydown', e => {
    const r = e.target.closest('.lg-row');
    if (!r || e.metaKey || e.ctrlKey || e.altKey) return;
    const els = [...scroll.querySelectorAll('.lg-row')];
    const i = els.indexOf(r);
    const t = rows.find(x => x.id === r.dataset.id);
    const k = e.key.toLowerCase();
    if (k === 'j' || e.key === 'ArrowDown') { e.preventDefault(); focusRow(i + 1); }
    else if (k === 'k' || e.key === 'ArrowUp') { e.preventDefault(); focusRow(i - 1); }
    else if (e.key === 'Enter') { e.preventDefault(); openRow(t.id); }
    else if (k === 'x' || e.key === ' ') { e.preventDefault(); toggleSelect(t, e.shiftKey); }
    else if (['b', 'p', 't', 'i'].includes(k)) { e.preventDefault(); const l = LANES.find(x => x.key.toLowerCase() === k); setLane(t, l.id); }
    else if (k === 'a' && t.status === 'review') { e.preventDefault(); approve(t); }
  });

  // -------------------------------------------------------------- saving
  async function save(t, patch, { quiet = false, msg } = {}) {
    const r = scroll.querySelector(`.lg-row[data-id="${cssEsc(t.id)}"]`);
    if (r) r.style.opacity = '0.6';
    try {
      const res = await api(`/api/transactions/${encodeURIComponent(t.id)}`, { method: 'PATCH', json: patch });
      Object.assign(t, (res && res.transaction) || patch);
      if (!quiet) toast(msg || 'Saved', { detail: `${displayMerchant(t)} → ${t.category || ''} · ${LANE_LABEL[t.lane] || ''}${patch.remember ? ' · rule saved' : ''}` });
      if (patch.status) refreshReviewBadge();
      if (patch.remember && res && res.changed) { await load(); return; }
    } catch (e) {
      toast('Couldn’t save', { kind: 'error', detail: e.message });
    }
    const fresh = rowEl(t);
    const old = scroll.querySelector(`.lg-row[data-id="${cssEsc(t.id)}"]`);
    if (old) { const hadFocus = old === document.activeElement; old.replaceWith(fresh); if (hadFocus) fresh.focus(); }
    if (t.id === activeId) drawPanel();
  }
  function lanePatch(t, lane) {
    const c = categoryByName(t.category);
    const patch = { lane, status: lane === 'review' ? 'review' : 'approved' };
    if (lane === 'transfer' && (!c || c.lane !== 'transfer')) patch.category = 'Transfer Between Accounts';
    if (lane === 'income' && (!c || c.lane !== 'income')) patch.category = 'Income & Deposits';
    if ((lane === 'business' || lane === 'personal') && c && (c.lane === 'transfer' || c.lane === 'income')) patch.category = 'Uncategorized';
    const cat = categoryByName(patch.category || t.category);
    patch.schedule_c = lane === 'business' ? ((cat && cat.schedule_c) || t.schedule_c || null) : null;
    return patch;
  }
  const setLane = (t, lane) => save(t, { ...lanePatch(t, lane), remember: rememberFor(t) }, { msg: `${LANE_LABEL[lane]}` });
  function setCategory(t, name) {
    const c = categoryByName(name);
    const patch = { category: name, status: 'approved' };
    if (c && (c.lane === 'transfer' || c.lane === 'income' || t.lane === 'review') && c.lane !== 'review') patch.lane = c.lane;
    patch.schedule_c = (patch.lane || t.lane) === 'business' ? ((c && c.schedule_c) || null) : null;
    return save(t, { ...patch, remember: rememberFor(t) });
  }
  function suggestedLane(t) {
    if (t.lane && t.lane !== 'review') return t.lane;
    const c = categoryByName(t.category);
    return c && c.lane && c.lane !== 'review' ? c.lane : null;
  }
  function approve(t) {
    const lane = suggestedLane(t);
    if (!lane) { toast('Choose a lane first', { kind: 'warn', detail: 'Business, Personal, Transfer or Income (B / P / T / I).' }); panel.querySelector('.lg-lanes button')?.focus(); return; }
    return save(t, { ...lanePatch(t, lane), category: t.category, status: 'approved', remember: rememberFor(t) }, { msg: 'Approved' });
  }
  const remember = new Map();   // txn id -> bool (panel switch)
  const rememberFor = t => !!remember.get(t.id);

  // -------------------------------------------------------------- detail panel
  function openRow(id) {
    activeId = id;
    for (const r of scroll.querySelectorAll('.lg-row')) {
      const on = r.dataset.id === id;
      r.classList.toggle('is-active', on); r.setAttribute('aria-selected', String(on)); r.tabIndex = on ? 0 : -1;
    }
    drawPanel();
    if (narrow()) { sheetOpen = true; panel.classList.add('is-open'); scrim.hidden = false; requestAnimationFrame(() => (panel.querySelector('.lg-ai-a button') || panel.querySelector('.lg-lanes button'))?.focus({ preventScroll: true })); }
  }
  function closeSheet() {
    sheetOpen = false; panel.classList.remove('is-open'); scrim.hidden = true;
    scroll.querySelector('.lg-row.is-active')?.focus();
  }

  function drawPanel() {
    const t = rows.find(x => x.id === activeId);
    clear(panel);
    if (!t) { panel.append(h('div.lg-p-empty', icon('list', 28), h('span.muted', 'Pick a transaction'))); return; }
    const name = displayMerchant(t);
    const v = signedAmount(t);
    const [whole, cents] = money(v, { sign: v > 0 }).split('.');
    const review = t.status === 'review';
    const conf = Math.round(num(t.confidence) * 100);
    const sugLane = suggestedLane(t);
    const via = t.source === 'ai' && t.ai_model ? ` via ${modelName(t.ai_model)}` : '';
    const statusChip = review ? chip('Review', 'warn', { dot: true }) : t.status === 'approved' ? chip('Approved', 'pos', { icon: 'check' }) : chip(STATUS_LABEL[t.status] || t.status, 'outline');

    const laneSeg = h('div.seg.lg-lanes', { role: 'group', 'aria-label': 'Lane' }, LANES.map(l => h('button', { type: 'button', 'aria-pressed': String(t.lane === l.id),
      class: review && sugLane === l.id && t.lane !== l.id ? 'is-sug' : null, title: `${l.label} (${l.key})`, onclick: () => setLane(t, l.id) }, l.label)));
    const catSelect = categoryOptions(h('select', { 'aria-label': 'Category' }), t.category || 'Uncategorized');
    catSelect.addEventListener('change', () => setCategory(t, catSelect.value));
    const note = h('input', { class: 'input', value: t.note || '', placeholder: 'Add a note', 'aria-label': 'Note', maxlength: 500 });
    const commitNote = () => { if ((t.note || '') !== note.value) save(t, { note: note.value }, { quiet: true }); };
    note.addEventListener('blur', commitNote);
    note.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); note.blur(); } if (e.key === 'Escape') { note.value = t.note || ''; note.blur(); } });
    const remSw = h('input', { type: 'checkbox', checked: rememberFor(t) });
    remSw.addEventListener('change', () => remember.set(t.id, remSw.checked));

    const sim = h('div.lg-sim', h('div.lg-sim-r', sk('48px', 10), sk('40%', 10), sk('64px', 10)));
    const simHead = h('span.muted.num');
    const applyAll = h('button', { class: 'btn btn-sm', type: 'button', hidden: true }, icon('layers', 14), 'Apply to all');

    append(panel, [
      h('div.lg-handle', { 'aria-hidden': 'true' }),
      h('header.lg-p-h', avatar(name, 'lg'), h('div.grow', h('h2', name), h('div.t2.muted', `${fmtDateShort(t.date)} · ${accountShort(t.account_id)}`)),
        h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', 'aria-label': 'Close details', onclick: () => (narrow() ? closeSheet() : (activeId = null, drawBody(), drawPanel())) }, icon('x', 16))),
      h('div.lg-p-amt', h('span.value', whole, cents ? h('span.cents', `.${cents}`) : null), statusChip),
      h('div.lg-p-sec', h('div.lg-raw.mono', { title: 'Bank descriptor' }, icon('bank', 14), h('span', t.description),
        h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', 'aria-label': 'Copy descriptor', onclick: async () => {
          try { await navigator.clipboard.writeText(t.description); toast('Copied'); } catch { toast('Couldn’t copy', { kind: 'warn' }); }
        } }, icon('copy', 14))),
        t.duplicate_of || t.transfer_group || t.refund_of ? h('div.row', { style: { gap: '6px' } },
          t.duplicate_of ? chip('Duplicate · excluded', 'outline') : null, t.transfer_group ? chip('Matched transfer', 'outline', { icon: 'repeat' }) : null,
          t.refund_of ? chip('Refund', 'outline') : null) : null),
      review ? h('div.lg-p-sec', h('div.lg-ai',
        h('div.lg-ai-h', icon('sparkle', 16), h('b', `${sugLane ? LANE_LABEL[sugLane] + ' · ' : ''}${t.category || 'Uncategorized'}`), h('span.num.muted', conf ? `${conf}%` : '—')),
        progressBar(conf, 'warn', { label: 'Confidence' }),
        h('div.lg-ai-a',
          h('button', { class: 'btn btn-primary btn-sm', type: 'button', onclick: () => approve(t) }, icon('check', 16), 'Approve', h('span.kbd', 'A')),
          h('button', { class: 'btn btn-sm', type: 'button', title: 'Pick another lane or category', onclick: () => laneSeg.querySelector('button')?.focus() }, 'Disapprove'),
          why('Why?', h('p', t.source && t.source !== 'none'
            ? `${SOURCE_LABEL[t.source] || 'Suggestion'}${via} suggested ${t.category || LANE_LABEL[t.lane]}${conf ? ` (${conf}% confident)` : ''}.`
            : 'No confident match for this merchant yet.'))))) : null,
      h('div.lg-p-sec.lg-fields', laneSeg,
        h('label.input.lg-select', icon('tag', 16), catSelect, icon('chevD', 16)),
        note,
        h('div.lg-rem', h('label.switch', { title: 'Saves a rule so future imports of this merchant are sorted the same way' }, remSw, h('span.track', { 'aria-hidden': 'true' }), `Remember for ${name}`))),
      h('div.lg-p-sec.lg-p-receipt', t.receipt_id
        ? [receiptThumb(name, v), h('div.grow', h('span.t1', 'Receipt'), chip('Matched', 'pos', { icon: 'check' })),
          h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', 'aria-label': 'Open receipt', onclick: () => showReceipt(t.receipt_id) }, icon('chevR', 16))]
        : [h('span.lg-receipt.empty', { 'aria-hidden': 'true' }, icon('receipt', 20)), h('div.grow', h('span.t1', 'No receipt'), h('span.t2', 'Drop one on Receipts')),
          h('a', { class: 'btn btn-ghost btn-sm icon', href: '#/receipts', 'aria-label': 'Add a receipt' }, icon('plus', 16))]),
      h('div.lg-p-sec', h('h3.lg-p-t', 'History'), h('ol.lg-tl',
        h('li', h('span.lg-tl-ic', icon('upload', 12)), h('span.grow', 'Imported'), h('span.muted.num', accountShort(t.account_id))),
        t.source && t.source !== 'none' ? h('li', h('span', { class: 'lg-tl-ic' + (t.source === 'ai' ? ' ai' : '') }, icon(t.source === 'ai' ? 'sparkle' : 'tag', 12)),
          h('span.grow', `${SOURCE_LABEL[t.source] || t.source} · ${t.category || ''}${t.source !== 'user' && conf ? ` ${conf}%` : ''}`), via ? h('span.muted', modelName(t.ai_model)) : null) : null,
        t.status === 'approved' ? h('li', h('span.lg-tl-ic.you', icon('user', 12)), h('span.grow', 'You · approved')) : null,
        t.note ? h('li', h('span.lg-tl-ic.you', icon('file', 12)), h('span.grow', `Note · ${t.note}`)) : null)),
      h('div.lg-p-sec', h('div.row-between', h('h3.lg-p-t', 'Similar ', simHead), applyAll), sim),
      h('div.lg-p-sec.lg-p-foot', h('button', { class: 'btn btn-ghost btn-sm btn-danger', type: 'button', onclick: e => removeIds([t.id], e.currentTarget) }, icon('trash', 14), 'Delete'))]);

    // similar transactions (same merchant), loaded lazily
    const my = t.id;
    api('/api/transactions', { query: { q: t.merchant || t.description, limit: 50, offset: 0, include_duplicates: 'false' }, quiet: true }).then(d => {
      if (activeId !== my) return;
      const others = listOf(d).items.filter(x => x.id !== t.id && (x.merchant || x.description) === (t.merchant || t.description));
      simHead.textContent = String(others.length);
      clear(sim);
      if (!others.length) { sim.append(h('p.muted.small', 'None')); return; }
      append(sim, others.slice(0, 4).map(x => h('div.lg-sim-r', h('span.muted.num', fmtDateShort(x.date)), h('span.grow', laneTag(x.lane)),
        h('span.amt', money(signedAmount(x), { sign: signedAmount(x) > 0 })))));
      const differ = others.filter(x => x.category !== t.category || x.lane !== t.lane || x.status !== 'approved');
      if (differ.length && t.lane !== 'review') {
        applyAll.hidden = false;
        clear(applyAll).append(icon('layers', 14), `Apply to ${differ.length}`);
        applyAll.title = `Set ${plural(differ.length, 'similar transaction')} to ${t.category} · ${LANE_LABEL[t.lane]}`;
        applyAll.onclick = () => busy(applyAll, async () => {
          try {
            const c = categoryByName(t.category);
            await api('/api/transactions/bulk', { method: 'POST', json: { ids: differ.map(x => x.id), category: t.category, lane: t.lane,
              schedule_c: t.lane === 'business' ? ((c && c.schedule_c) || t.schedule_c || null) : null, status: 'approved' } });
            toast(`Updated ${plural(differ.length, 'transaction')}`, { detail: `${name} → ${t.category} · ${LANE_LABEL[t.lane]}` });
            await load(); refreshReviewBadge();
          } catch (e) { toast('Couldn’t update', { kind: 'error', detail: e.message }); }
        }, 'Applying…');
      }
    }).catch(() => { if (activeId === my) clear(sim).append(h('p.muted.small', '—')); });
  }

  // -------------------------------------------------------------- bulk
  function drawBulk() {
    const n = selected.size;
    table.classList.toggle('has-bulk', n > 0);
    clear(bulkDock);
    if (!n) return;
    const sum = rows.filter(t => selected.has(t.id)).reduce((a, t) => a + signedAmount(t), 0);
    const cat = categoryOptions(h('select', { class: 'pill', 'aria-label': 'Set category for selected' }), '', { includeAll: true, allLabel: 'Category…' });
    cat.addEventListener('change', () => {
      if (!cat.value) return;
      const c = categoryByName(cat.value);
      const p = { category: cat.value, status: 'approved' };
      if (c && c.lane && c.lane !== 'review') { p.lane = c.lane; p.schedule_c = c.lane === 'business' ? (c.schedule_c || null) : null; }
      bulk(p);
    });
    bulkDock.append(h('div.lg-bulk', { role: 'toolbar', 'aria-label': 'Bulk edit' },
      h('span.lg-bulk-n', h('b.num', String(n)), ' selected'), h('span.muted.num', money(sum)),
      h('span.lg-bulk-sp'),
      h('div.seg.lg-bulk-lanes', { role: 'group', 'aria-label': 'Set lane' }, LANES.map(l => h('button', { type: 'button', title: `Set lane: ${l.label}`, 'aria-label': `Set lane: ${l.label}`,
        onclick: () => bulk({ lane: l.id, status: 'approved' }) }, l.key))),
      cat,
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => bulk({ status: 'review' }), title: 'Mark for review' }, icon('inbox', 14), h('span.lg-bl', 'Review')),
      h('button', { class: 'btn btn-ghost btn-sm btn-danger icon', type: 'button', 'aria-label': 'Delete selected', title: 'Delete selected', onclick: e => removeIds([...selected], e.currentTarget) }, icon('trash', 15)),
      h('button', { class: 'btn btn-primary btn-sm', type: 'button', onclick: () => bulk({ status: 'approved' }) }, icon('check', 15), 'Approve'),
      h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', 'aria-label': 'Clear selection', title: 'Clear (Esc)', onclick: () => { selected.clear(); drawBody(); drawBulk(); } }, icon('x', 16))));
  }
  async function removeIds(ids, btn) {
    if (!await confirmDialog({ title: `Delete ${plural(ids.length, 'transaction')}?`,
      description: 'They’re removed from this workspace and every report. Importing the same statement again brings them back.',
      confirmLabel: 'Delete', danger: true })) return;
    await busy(btn, async () => {
      try {
        const r = await api('/api/transactions/delete', { method: 'POST', json: { ids } });
        toast(`Deleted ${plural((r && r.deleted_transactions) ?? ids.length, 'transaction')}`);
        for (const id of ids) selected.delete(id);
        if (ids.includes(activeId)) { activeId = null; if (sheetOpen) closeSheet(); }
        await load(); loadCounts(); refreshReviewBadge();
      } catch (e) { toast('Couldn’t delete', { kind: 'error', detail: e.message }); }
    }, 'Deleting…');
  }
  async function bulk(fields) {
    const ids = [...selected];
    try {
      const r = await api('/api/transactions/bulk', { method: 'POST', json: { ids, ...fields } });
      toast(`Updated ${plural((r && r.updated) ?? ids.length, 'transaction')}`);
      selected.clear();
      await load(); loadCounts(); refreshReviewBadge();
    } catch (e) { toast('Bulk update failed', { kind: 'error', detail: e.message }); }
  }

  // -------------------------------------------------------------- pager
  function drawPager() {
    const from = rows.length ? offset + 1 : 0, to = offset + rows.length;
    const size = h('select', { class: 'pill', 'aria-label': 'Rows per page' }, ...PAGE_SIZES.map(n => new Option(`${n} / page`, String(n))));
    size.value = String(limit);
    size.addEventListener('change', () => { limit = +size.value; offset = 0; load(); });
    const hasNext = exact ? to < total : rows.length === limit;
    clear(pager).append(
      h('span.muted.num', exact ? `${from.toLocaleString('en-US')}–${to.toLocaleString('en-US')} of ${total.toLocaleString('en-US')}` : `${from}–${to}`),
      h('span.spacer'), size,
      h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', disabled: offset === 0, 'aria-label': 'Previous page', onclick: () => { offset = Math.max(0, offset - limit); load(); } }, icon('chevL', 16)),
      h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', disabled: !hasNext, 'aria-label': 'Next page', onclick: () => { offset += limit; load(); } }, icon('chevR', 16)));
  }

  function skeletonRows() {
    clear(scroll).append(Array.from({ length: 9 }, () => h('div.lg-row.is-sk', { 'aria-hidden': 'true' },
      h('span.lg-c', sk('16px', 16, 4)), h('span.lg-c', sk('40px', 12)), h('span.lg-c.lg-merch', sk('28px', 28, 14), h('span', { style: { flex: 1 } }, sk('50%', 12), sk('30%', 10, 6, { marginTop: '6px' }))),
      h('span.lg-c', sk('70%', 20, 10)), h('span.lg-c', sk('60%', 12)), h('span.lg-c.lg-amt', sk('64px', 12)))));
  }

  async function loadCounts() {
    try {
      const sum = await api('/api/summary', { query: { year: f.year || undefined }, quiet: true });
      const c = (sum && sum.counts) || {};
      counts = { total: c.total, needs_review: c.needs_review ?? sum.review_count, business: c.business, personal: c.personal, income: c.income, transfer: c.transfer };
    } catch { counts = null; }
    if (ctx.isCurrent()) drawTabs();
  }

  async function load() {
    const my = ++reqSeq;
    scroll.setAttribute('aria-busy', 'true');
    if (!rows.length) skeletonRows(); else scroll.style.opacity = '0.55';
    try {
      const d = await api('/api/transactions', { query: {
        lane: f.lane, category: f.category, account: f.account, status: f.status, q: f.q, year: f.year,
        date_from: f.date_from, date_to: f.date_to, direction: f.direction,
        limit, offset, sort: sort.key, order: sort.dir } });
      if (my !== reqSeq || !ctx.isCurrent()) return;
      const l = listOf(d);
      rows = l.items; total = l.total; exact = l.exact;
      for (const id of [...selected]) if (!rows.some(t => t.id === id)) selected.delete(id);
      if (!rows.some(t => t.id === activeId)) activeId = !narrow() && rows.length ? rows[0].id : null;
      drawBody(); drawPager(); drawBulk(); drawPanel();
      const qs = new URLSearchParams(Object.entries(f).filter(([k, v]) => v || (k === 'year' && (f.date_from || f.date_to)))).toString();
      history.replaceState(null, '', `${location.pathname}${location.search}#/ledger${qs ? '?' + qs : ''}`);
    } catch (e) {
      if (my !== reqSeq) return;
      clear(scroll).append(h('div.error-box', { role: 'alert', style: { margin: '16px' } }, icon('alert', 16), e.message));
    } finally { if (my === reqSeq) { scroll.style.opacity = ''; scroll.setAttribute('aria-busy', 'false'); } }
  }

  drawHead(); drawTabs(); drawRange();
  clear(main).append(h('div.ldg', toolbar, h('div.lg-wrap', table, panel), scrim));
  skeletonRows();
  await Promise.all([load(), loadCounts()]);
  // ?id=<txn> opens that row's details (``first`` = the first row on the page; used by demos)
  const want = params.get('id') === 'first' ? (rows[0] && rows[0].id) : params.get('id');
  if (want && rows.some(t => t.id === want)) { if (narrow()) openRow(want); else { activeId = want; drawBody(); drawPanel(); } }

  const onKey = e => {
    if (!ctx.isCurrent() || isModalOpen() || isTypingTarget(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === '/') { e.preventDefault(); q.focus(); q.select(); return; }
    if (e.key === 'Escape') {
      if (sheetOpen) { closeSheet(); return; }
      if (selected.size) { selected.clear(); drawBody(); drawBulk(); announce('Selection cleared'); }
    }
  };
  document.addEventListener('keydown', onKey);
  const unbindAsk = bindScreenContext(ctx, askOpts);
  return () => { document.removeEventListener('keydown', onKey); unbindAsk(); slotCleanup(); };
}

function receiptThumb(name, v) {
  return h('span.lg-receipt', { 'aria-hidden': 'true' }, h('b', name.toUpperCase().slice(0, 14)), h('i'), h('i'), h('i.s'), h('i'), h('span', money(Math.abs(v))));
}
function cssEsc(v) { return (window.CSS && CSS.escape) ? CSS.escape(v) : String(v).replace(/["\\]/g, '\\$&'); }
