// LedgerStitch — Review: one decision per merchant. Cards view (one merchant at a time, keys
// A approve · D skip · B/P/T/I lane · C category · R remember · Z undo) or a by-merchant list.
// Decisions go through PATCH /api/transactions/{id} (remember → rule) and POST /api/transactions/bulk.
import { api, listOf } from './api.js';
import { h, clear, append, icon, toast, modal, busy, plural, money, fmtDateShort, num, signedAmount,
  LANES, LANE_LABEL, SOURCE_LABEL, isTypingTarget, isModalOpen, announce, emptyState, avatar, chip, card, progressBar, displayMerchant, why } from './ui.js';
import { store, loadCategories, loadAccounts, accountShort, categoryByName, categoryOptions, refreshReviewBadge } from './state.js';
import { runCategorization, modelName } from './ai.js';
import { receiptButton } from './screen-receipts.js';
import { ring } from './charts.js';
import { setTopActions, bindScreenContext } from './shell.js';
import { firstRunCard, caughtUp } from './states.js';

const PAGE = 5000;
const confColor = c => (c >= 0.7 ? 'var(--pos)' : c >= 0.5 ? 'var(--warn)' : 'var(--danger)');

export async function renderReview(main, params, ctx) {
  await Promise.all([loadCategories(), loadAccounts()]);
  let groups = [];
  let totalTxns = 0;
  let activeKey = null;
  let view = params.get('view') === 'list' ? 'list' : 'cards';
  let lastUndo = null;
  const decisions = new Map(); // key -> {lane, category, remember}

  const catBtn = h('button', { class: 'btn btn-ghost icon', type: 'button', title: 'Run categorization', 'aria-label': 'Run categorization', onclick: () => runCategorize(catBtn) }, icon('sparkle', 18));
  setTopActions(catBtn);
  const top = h('div.card.rv-top');
  const body = h('div');
  clear(main).append(h('div.rv', top, body));
  const askOpts = { screen: 'review', onApplied: () => load({ keepActive: true }) };
  const remaining = () => groups.reduce((a, g) => a + g.rows.length, 0);

  await load();

  // -------------------------------------------------------------- data
  async function load({ keepActive = false } = {}) {
    const [rev, all] = await Promise.all([
      api('/api/transactions', { query: { status: 'review', limit: PAGE, offset: 0 } }),
      api('/api/transactions', { query: { limit: 0, offset: 0, include_duplicates: 'false' } }).catch(() => null),
    ]);
    if (!ctx.isCurrent()) return;
    const rows = listOf(rev).items.filter(t => !t.duplicate_of && t.status === 'review');
    const allList = all ? listOf(all) : null;
    totalTxns = allList && allList.exact ? allList.total : ((store.status && store.status.counts && store.status.counts.transactions) || rows.length);
    groups = groupRows(rows);
    if (!keepActive || !groups.some(g => g.key === activeKey)) activeKey = groups[0] ? groups[0].key : null;
    refreshReviewBadge(rows.length);
    draw();
  }

  function groupRows(rows) {
    const m = new Map();
    for (const t of rows) {
      const key = (t.merchant || t.description || '').trim().toUpperCase() || t.id;
      let g = m.get(key);
      if (!g) { g = { key, merchant: displayMerchant(t), raw: t.merchant || t.description, rows: [], accounts: new Set(), min: t.date, max: t.date, total: 0 }; m.set(key, g); }
      g.rows.push(t);
      g.accounts.add(t.account_id);
      if (t.date < g.min) g.min = t.date;
      if (t.date > g.max) g.max = t.date;
      g.total += signedAmount(t);
    }
    for (const g of m.values()) {
      g.rows.sort((a, b) => (a.date < b.date ? 1 : -1));
      const votes = new Map();
      for (const t of g.rows) {
        const k = `${t.category || ''}|${t.lane || 'review'}`;
        const v = votes.get(k) || { category: t.category, lane: t.lane || 'review', n: 0, conf: 0, source: t.source, ai_model: null };
        v.n++; if (num(t.confidence) >= v.conf) { v.conf = num(t.confidence); v.source = t.source; v.ai_model = t.source === 'ai' ? (t.ai_model || null) : null; }
        votes.set(k, v);
      }
      g.suggestion = [...votes.values()].sort((a, b) => b.n - a.n || b.conf - a.conf)[0];
    }
    return [...m.values()].sort((a, b) => Math.abs(b.total) - Math.abs(a.total));
  }

  function decisionFor(g) {
    let d = decisions.get(g.key);
    if (!d) {
      const s = g.suggestion || {};
      let lane = s.lane && s.lane !== 'review' ? s.lane : null;
      const cat = s.category && s.category !== 'Uncategorized' ? s.category : (s.category || 'Uncategorized');
      if (!lane) { const c = categoryByName(cat); if (c && c.lane && c.lane !== 'review') lane = c.lane; }
      d = { lane, category: cat, remember: true };
      decisions.set(g.key, d);
    }
    return d;
  }

  // -------------------------------------------------------------- render
  function drawTop() {
    const left = remaining();
    const reviewed = Math.max(0, totalTxns - left);
    const pct = totalTxns ? reviewed / totalTxns * 100 : 100;
    clear(top).append(
      h('div.rv-prog', h('div.rv-prog-n', h('span.value.num', reviewed.toLocaleString('en-US')), h('span.muted.num', `/ ${totalTxns.toLocaleString('en-US')}`)),
        progressBar(pct, 'pos', { label: `${reviewed} of ${totalTxns} reviewed` })),
      h('div.rv-stat', { title: 'Transactions left' }, icon('inbox', 16), h('b.num', String(left))),
      h('div.rv-stat', { title: 'Estimated time' }, icon('clock', 16), h('b.num', `~${Math.max(1, Math.round(groups.length * 25 / 60))} min`)),
      h('nav.seg.rv-seg', { 'aria-label': 'View' },
        h('a', { href: '#/review', 'aria-current': view === 'cards' ? 'page' : null, onclick: e => { e.preventDefault(); setView('cards'); } }, icon('layers', 14), 'Cards'),
        h('a', { href: '#/review?view=list', 'aria-current': view === 'list' ? 'page' : null, onclick: e => { e.preventDefault(); setView('list'); } }, icon('list', 14), 'By merchant')));
  }
  function setView(v) {
    view = v;
    history.replaceState(null, '', `${location.pathname}${location.search}#/review${v === 'list' ? '?view=list' : ''}`);
    draw();
  }

  function draw() {
    drawTop();
    clear(body);
    if (!groups.length) {
      top.hidden = true;
      body.append(totalTxns ? h('div.card', caughtUp({ title: 'All caught up', sub: `${totalTxns.toLocaleString('en-US')} of ${totalTxns.toLocaleString('en-US')} sorted`,
        action: [h('a', { class: 'btn', href: '#/ledger' }, 'Ledger'), h('a', { class: 'btn btn-primary', href: '#/overview' }, icon('bars', 16), 'See overview')] })) : firstRunCard({ title: 'Nothing to review yet' }));
      return;
    }
    top.hidden = false;
    if (!groups.some(g => g.key === activeKey)) activeKey = groups[0].key;
    body.append(view === 'list' ? listView() : cardsView());
  }

  // ---- cards
  function cardsView() {
    const g = groups.find(x => x.key === activeKey) || groups[0];
    const stage = h('div.rv-stage', h('div.rv-pile', groups.length > 2 ? h('div.rv-card.peek.d2', { 'aria-hidden': 'true' }) : null, groups.length > 1 ? h('div.rv-card.peek', { 'aria-hidden': 'true' }) : null, triageCard(g)),
      h('div.rv-keys', { 'aria-label': 'Keyboard shortcuts' }, icon('keyboard', 16),
        h('span', h('span.kbd', 'A'), 'Approve'), h('span', h('span.kbd', 'D'), 'Skip'), h('span', h('span.kbd', 'B'), h('span.kbd', 'P'), h('span.kbd', 'T'), h('span.kbd', 'I'), 'Lane'),
        h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: showShortcuts }, h('span.kbd', '?'), 'All')));
    return h('div.rv-grid', stage, h('div.rv-side', queueCard(g)));
  }

  function bars(g) {
    const amts = g.rows.slice().reverse().slice(-14).map(t => Math.abs(signedAmount(t)));
    const mx = Math.max(...amts, 1);
    const same = amts.length > 1 && amts.every(a => Math.abs(a - amts[0]) < 0.01);
    return h('div', { class: 'rv-bars' + (same ? ' same' : ''), 'aria-hidden': 'true' },
      amts.map(a => h('i', { style: { height: `${Math.max(12, a / mx * 100)}%` } })), same ? chip('Same amount', 'outline', { icon: 'repeat' }) : null);
  }

  function triageCard(g) {
    const d = decisionFor(g);
    const s = g.suggestion || {};
    const conf = num(s.conf);
    const accts = [...g.accounts];
    const via = s.source === 'ai' && s.ai_model ? ` via ${modelName(s.ai_model)}` : '';
    const sel = categoryOptions(h('select', { class: 'pill', 'aria-label': 'Category', 'data-role': 'category' }), d.category);
    sel.addEventListener('change', () => {
      d.category = sel.value;
      const c = categoryByName(sel.value);
      if (c && c.lane && c.lane !== 'review' && (!d.lane || (d.lane !== c.lane && (c.lane === 'transfer' || c.lane === 'income' || d.lane === 'transfer' || d.lane === 'income')))) { d.lane = c.lane; refreshLanes(d); }
    });
    const remember = h('input', { type: 'checkbox', checked: d.remember, 'data-role': 'remember' });
    remember.addEventListener('change', () => { d.remember = remember.checked; });
    const lanes = h('div.rv-lanes', { role: 'radiogroup', 'aria-label': 'Lane' }, LANES.map(l => h('button', { type: 'button', class: 'rv-lane' + (d.lane === l.id ? ' on' : ''), role: 'radio', 'aria-checked': String(d.lane === l.id),
      dataset: { lane: l.id }, title: l.blurb, onclick: () => setLane(g, l.id) }, h('i', { class: `dot lane-dot-${l.id}`, 'aria-hidden': 'true' }), l.label, h('span.kbd', { 'aria-hidden': 'true' }, l.key))));
    lanes.addEventListener('keydown', e => {
      if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
      e.preventDefault();
      const i = LANES.findIndex(l => l.id === d.lane);
      setLane(g, LANES[(i + (e.key === 'ArrowRight' ? 1 : LANES.length - 1)) % LANES.length].id, true);
    });
    const rows = g.rows.slice(0, 6);
    const details = h('details.more', h('summary', icon('list', 14), `${plural(g.rows.length, 'charge')}`),
      h('div.more-b', h('div.rv-tx', rows.map(t => h('div.rv-txr', h('span.muted.num', fmtDateShort(t.date)), h('span.grow', { title: t.description }, accountShort(t.account_id)),
        t.receipt_id ? receiptButton(t.receipt_id, g.merchant) : null, h('span.amt', money(signedAmount(t)))))),
        g.rows.length > rows.length ? h('a.small', { href: `#/ledger?q=${encodeURIComponent(g.raw)}&status=review` }, `All ${g.rows.length} in the ledger`) : null));
    return h('article.rv-card', { 'aria-label': g.merchant, dataset: { key: g.key } },
      h('header.rv-ch', avatar(g.merchant, 'xl'),
        h('div.rv-who', h('h2', g.merchant), h('span.muted', `${plural(g.rows.length, 'charge')} · ${accts.length > 2 ? `${accts.length} accounts` : accts.map(accountShort).join(' · ')}`)),
        h('div.rv-amt', h('span.value.num', money(g.total, { sign: g.total > 0 })), h('span.muted', g.min === g.max ? fmtDateShort(g.min) : `${fmtDateShort(g.min)} – ${fmtDateShort(g.max)}`))),
      bars(g),
      h('div.rv-ai',
        ring(Math.round(conf * 100), { size: 52, stroke: 6, color: confColor(conf), label: conf ? `${Math.round(conf * 100)}%` : '—', labelSize: 13, ariaLabel: conf ? `${Math.round(conf * 100)}% confident` : 'No suggestion' }),
        h('div.rv-ai-t', h('span.label', icon('sparkle', 14), ' Suggested'), h('div.rv-ai-c', s.category || s.lane ? chip(s.category || LANE_LABEL[s.lane], 'dashed') : chip('No suggestion', 'outline'),
          why('Why?', h('p', s.source && s.source !== 'none' ? `${SOURCE_LABEL[s.source] || 'Suggestion'}${via} suggested ${s.category || LANE_LABEL[s.lane]} (${Math.round(conf * 100)}% confident).` : 'No confident match for this merchant yet.',
            g.rows.length > 1 ? ` Your choice applies to all ${g.rows.length} charges.` : '')))),
        h('label.rv-cat', icon('tag', 16), sel)),
      lanes,
      h('footer.rv-act',
        h('label.rv-remember', { title: 'Saves a rule so future imports of this merchant are sorted automatically' }, remember, icon('repeat', 14), ' Remember'),
        h('button', { class: 'btn', type: 'button', onclick: () => move(1) }, 'Skip', h('span.kbd', 'D')),
        h('button', { class: 'btn btn-primary', type: 'button', 'data-role': 'approve', onclick: () => approveGroup(g) }, icon('check', 16), g.rows.length > 1 ? `Approve ${g.rows.length}` : 'Approve', h('span.kbd', 'A'))),
      details);
  }

  function queueCard(cur) {
    const up = groups.filter(g => g.key !== cur.key).slice(0, 8);
    return card({ title: 'Up next', meta: `${plural(groups.length - 1, 'merchant')}`, cls: 'flush rv-queue', body: up.length
      ? h('div.rows', up.map(g => h('button.trow', { type: 'button', onclick: () => activate(g.key, true) }, avatar(g.merchant, 'sm'),
        h('div.grow', h('span.t1', g.merchant), h('span.t2', (g.suggestion && g.suggestion.category) || 'Uncategorized')), h('span.amt', money(g.total)))))
      : h('p.muted.small', { style: { padding: '0 24px' } }, 'Last one.') });
  }

  // ---- list
  function listView() {
    const easy = groups.filter(g => num((g.suggestion || {}).conf) >= 0.7 && decisionFor(g).lane);
    const rows = groups.map(g => {
      const d = decisionFor(g), c = num((g.suggestion || {}).conf);
      const b = h('button', { class: 'btn btn-sm icon', type: 'button', 'aria-label': `Approve ${g.merchant}`, title: `Approve ${g.rows.length} as ${d.category}`, onclick: () => approveGroup(g) }, icon('check', 14));
      return h('div', { class: 'trow rv-lr' + (g.key === activeKey ? ' sel' : ''), dataset: { key: g.key }, onclick: e => { if (!e.target.closest('button')) { activeKey = g.key; setView('cards'); } } },
        avatar(g.merchant),
        h('div.grow', h('span.t1', g.merchant), h('span.t2.num', plural(g.rows.length, 'charge'))),
        h('span.rv-lc', d.category && d.category !== 'Uncategorized' ? chip(d.category) : chip('Uncategorized', 'outline')),
        h('span.rv-conf.num', { title: 'Confidence' }, progressBar(c * 100, '', { thin: true, label: 'Confidence' }), c ? `${Math.round(c * 100)}%` : '—'),
        h('span.amt', money(g.total)), h('span.rv-la', b));
    });
    for (const r of rows) { const i = r.querySelector('.bar > i'); const g = groups.find(x => x.key === r.dataset.key); if (i) i.style.background = confColor(num((g.suggestion || {}).conf)); }
    return card({ title: 'By merchant', meta: `${plural(groups.length, 'merchant')} · ${plural(remaining(), 'charge')}`, cls: 'flush rv-list',
      action: easy.length ? h('button', { class: 'btn btn-sm', type: 'button', onclick: e => approveMany(easy, e.currentTarget) }, icon('zap', 14), `Approve all ≥70% (${easy.length})`) : null,
      body: h('div.rows', rows) });
  }

  function refreshLanes(d) {
    for (const b of body.querySelectorAll('.rv-lane')) { const on = b.dataset.lane === d.lane; b.classList.toggle('on', on); b.setAttribute('aria-checked', String(on)); }
    const sel = body.querySelector('[data-role="category"]');
    if (sel && sel.value !== d.category) sel.value = d.category;
  }

  // -------------------------------------------------------------- actions
  function activeGroup() { return groups.find(g => g.key === activeKey); }
  function focusActive() {
    const c = body.querySelector('.rv-card:not(.peek)');
    if (!c) return;
    const b = c.querySelector('.rv-lane.on') || c.querySelector('.rv-lane');
    b && b.focus({ preventScroll: true });
  }
  function activate(key, focus = false) {
    activeKey = key;
    draw();
    if (focus) focusActive();
    const g = activeGroup();
    if (g) announce(`${g.merchant}, ${plural(g.rows.length, 'charge')}, ${money(Math.abs(g.total))}`);
  }
  function move(delta) {
    if (!groups.length) return;
    const i = Math.max(0, groups.findIndex(g => g.key === activeKey));
    activate(groups[(i + delta + groups.length) % groups.length].key, true);
  }
  function setLane(g, lane, focusBtn = false) {
    const d = decisionFor(g);
    d.lane = lane;
    const c = categoryByName(d.category);
    if (lane === 'transfer' && (!c || c.lane !== 'transfer')) d.category = 'Transfer Between Accounts';
    if (lane === 'income' && (!c || c.lane !== 'income')) d.category = 'Income & Deposits';
    if ((lane === 'business' || lane === 'personal') && c && (c.lane === 'transfer' || c.lane === 'income')) d.category = 'Uncategorized';
    refreshLanes(d);
    if (focusBtn) body.querySelector(`.rv-lane[data-lane="${lane}"]`)?.focus();
    announce(`Lane: ${LANE_LABEL[lane]}`);
  }

  async function commit(g, d) {
    const cat = d.category || 'Uncategorized';
    const c = categoryByName(cat);
    const fields = { category: cat, lane: d.lane, schedule_c: d.lane === 'business' ? ((c && c.schedule_c) || null) : null, status: 'approved' };
    const ids = g.rows.map(t => t.id);
    let rest = ids, changed = 0;
    if (d.remember) {
      const r = await api(`/api/transactions/${encodeURIComponent(ids[0])}`, { method: 'PATCH', json: { ...fields, remember: true } });
      changed = (r && r.changed) || 0;
      rest = ids.slice(1);
    }
    if (rest.length) await api('/api/transactions/bulk', { method: 'POST', json: { ids: rest, ...fields } });
    return { ids, changed, cat };
  }

  async function approveGroup(g) {
    const d = decisionFor(g);
    if (!d.lane) {
      toast('Choose a lane first', { kind: 'warn', detail: 'Business, Personal, Transfer or Income: B, P, T or I.' });
      body.querySelector('.rv-lane')?.focus();
      return;
    }
    const prev = g.rows.map(t => ({ id: t.id, category: t.category, lane: t.lane, schedule_c: t.schedule_c }));
    const cardEl = body.querySelector(`.rv-card[data-key="${cssEsc(g.key)}"]`);
    const btn = cardEl && cardEl.querySelector('[data-role="approve"]');
    const idx = groups.findIndex(x => x.key === g.key);
    let res;
    try { res = btn ? await busy(btn, () => commit(g, d), 'Saving…') : await commit(g, d); }
    catch (e) { toast('Couldn’t save that decision', { kind: 'error', detail: e.message }); return; }
    if (cardEl && !matchMedia('(prefers-reduced-motion: reduce)').matches) { cardEl.classList.add('out'); await new Promise(r => setTimeout(r, 200)); }
    groups = groups.filter(x => x.key !== g.key);
    decisions.delete(g.key);
    activeKey = groups.length ? groups[Math.min(idx, groups.length - 1)].key : null;
    lastUndo = { g, prev, d: { ...d } };
    draw();
    refreshReviewBadge(remaining());
    if (view === 'cards') focusActive();
    toast(`${g.merchant} → ${LANE_LABEL[d.lane]}`, { detail: `${plural(res.ids.length, 'charge')} approved as ${res.cat}${d.remember ? ' · rule saved' : ''}`, action: { label: 'Undo', onClick: () => undo() } });
    if (d.remember && res.changed > res.ids.length - 1) load({ keepActive: true }).catch(() => {});
  }

  async function approveMany(list, btn) {
    await busy(btn, async () => {
      let n = 0;
      try {
        for (const g of list) { await commit(g, decisionFor(g)); n += g.rows.length; }
        toast(`Approved ${plural(n, 'charge')}`, { detail: `${plural(list.length, 'merchant')} with a confident suggestion` });
      } catch (e) { toast('Stopped early', { kind: 'error', detail: e.message }); }
      lastUndo = null;
      await load();
    }, 'Approving…');
  }

  async function undo() {
    const u = lastUndo;
    if (!u) return;
    lastUndo = null;
    try {
      const byPrev = new Map();
      for (const p of u.prev) {
        const k = `${p.category}|${p.lane}|${p.schedule_c}`;
        if (!byPrev.has(k)) byPrev.set(k, { ...p, ids: [] });
        byPrev.get(k).ids.push(p.id);
      }
      for (const p of byPrev.values()) await api('/api/transactions/bulk', { method: 'POST', json: { ids: p.ids, category: p.category, lane: p.lane, schedule_c: p.schedule_c, status: 'review' } });
      decisions.set(u.g.key, u.d);
      await load();
      activate(u.g.key, true);
      toast('Undone', { detail: u.d.remember ? 'Back in the queue. The saved rule is still in Settings → Rules.' : 'Back in the queue.' });
    } catch (e) { toast('Couldn’t undo', { kind: 'error', detail: e.message }); }
  }

  async function runCategorize(btn) {
    const r = await runCategorization(btn);
    if (!r || !ctx.isCurrent()) return;
    decisions.clear();
    try { await load(); } catch (e) { toast('Couldn’t refresh the queue', { kind: 'error', detail: e.message }); }
  }

  // -------------------------------------------------------------- keyboard
  function onKey(e) {
    if (!ctx.isCurrent() || isModalOpen() || e.metaKey || e.ctrlKey || e.altKey) return;
    const typing = isTypingTarget(e.target);
    if (e.key === '?' && !typing) { e.preventDefault(); showShortcuts(); return; }
    if (typing) return;
    const g = activeGroup();
    const k = e.key.toLowerCase();
    if (k === 'j' || e.key === 'ArrowDown') { e.preventDefault(); move(1); return; }
    if (k === 'k' || e.key === 'ArrowUp') { e.preventDefault(); move(-1); return; }
    if (k === 'l') { e.preventDefault(); setView(view === 'list' ? 'cards' : 'list'); return; }
    if (!g) return;
    const lane = LANES.find(l => l.key.toLowerCase() === k);
    if (lane) { e.preventDefault(); setLane(g, lane.id, view === 'cards'); return; }
    if (k === 'a' || e.key === 'Enter') {
      if (e.key === 'Enter' && e.target && (e.target.tagName === 'A' || (e.target.tagName === 'BUTTON' && !e.target.classList.contains('rv-lane') && e.target.dataset.role !== 'approve'))) return;
      e.preventDefault(); approveGroup(g); return;
    }
    if (k === 'd' || k === 's') { e.preventDefault(); move(1); return; }
    if (k === 'c') { e.preventDefault(); body.querySelector('[data-role="category"]')?.focus(); return; }
    if (k === 'r') {
      e.preventDefault();
      const cb = body.querySelector('[data-role="remember"]');
      if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); announce(cb.checked ? 'Will remember for this merchant' : 'Won’t remember'); }
      return;
    }
    if (k === 'z' || k === 'u') { e.preventDefault(); undo(); return; }
  }
  document.addEventListener('keydown', onKey);
  requestAnimationFrame(() => { if (groups.length && view === 'cards') focusActive(); });
  const unbindAsk = bindScreenContext(ctx, askOpts);
  return () => { document.removeEventListener('keydown', onKey); unbindAsk(); };
}

export function showShortcuts() {
  const rows = [
    [['A', 'Enter'], 'Approve this merchant'], [['D', 'S'], 'Skip for now'],
    [['B'], 'Business lane'], [['P'], 'Personal lane'], [['T'], 'Transfer lane (excluded)'], [['I'], 'Income lane'],
    [['C'], 'Choose category'], [['R'], 'Toggle “Remember for this merchant”'], [['Z'], 'Undo last approval'],
    [['J', 'K'], 'Next / previous merchant'], [['L'], 'Cards / by merchant'],
    [['⌘/Ctrl', 'K'], 'Ask a question or describe a change'], [['?'], 'Show this list'], [['Esc'], 'Close dialog'],
  ];
  modal({ title: 'Keyboard shortcuts', description: 'Built for speed; you never need the mouse.',
    body: h('table.shortcut-table', h('tbody', rows.map(([keys, label]) => h('tr', h('td', keys.map(k => [h('kbd', k), ' '])), h('td', label))))),
    actions: [{ label: 'Got it', primary: true, value: true }] });
}

function cssEsc(v) { return (window.CSS && CSS.escape) ? CSS.escape(v) : String(v).replace(/["\\]/g, '\\$&'); }
void append; void emptyState;
