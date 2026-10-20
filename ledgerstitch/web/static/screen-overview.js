// LedgerStitch — Overview (home): KPI strip with sparklines, profit chart, the “Next up” checklist, and
// the cards add-ons register for the "overview" slot. Picture first; every number comes from existing
// endpoints: /api/summary (this year + last year), /api/receipts, /api/status (backup, review count).
import { api, listOf } from './api.js';
import { h, clear, icon, num, money, card, delta, sk, displayMerchant, plural } from './ui.js';
import { store, loadSettings, loadAccounts, taxYear } from './state.js';
import { sparkline, comboChart, legend } from './charts.js';
import { setTopActions } from './shell.js';
import { exportButton } from './export-menu.js';
import { firstRunCard } from './states.js';
import { renderSlot } from './addons.js';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DONE_KEY = 'ls-nextup-done';
const settle = p => (p ? p.then(v => v, () => null) : Promise.resolve(null));

export async function renderOverview(main, params, ctx) {
  await Promise.all([loadSettings().catch(() => null), loadAccounts().catch(() => null)]);
  const year = Number(params.get('year')) || taxYear();
  const now = new Date();
  const thisYear = now.getFullYear();
  setTopActions(exportButton({ year: () => year }));

  const [sum, prev, receipts, review] = await Promise.all([
    api('/api/summary', { query: { year } }),
    settle(api('/api/summary', { query: { year: year - 1 }, quiet: true })),
    settle(api('/api/receipts', { quiet: true })),
    settle(api('/api/transactions', { query: { status: 'review', limit: 5000, offset: 0, include_duplicates: 'false' }, quiet: true })),
  ]);
  if (!ctx.isCurrent()) return;

  const counts = sum.counts || {};
  if (!counts.total && !store.accounts.length) {
    clear(main).append(firstRunCard());
    return;
  }

  // ---------------------------------------------------------------- series
  const byMonth = new Map((sum.by_month || []).map(m => [String(m.month).slice(5, 7), m]));
  let last = 12;
  if (year === thisYear) last = now.getMonth() + 1;
  const lastWithData = Math.max(0, ...[...byMonth.keys()].map(Number));
  if (year !== thisYear && lastWithData) last = Math.max(lastWithData, Math.min(12, last));
  const idx = Array.from({ length: Math.max(1, last) }, (_, i) => String(i + 1).padStart(2, '0'));
  const ser = k => idx.map(mm => num((byMonth.get(mm) || {})[k]));
  const income = ser('income'), business = ser('business');
  const profit = income.map((v, i) => v - business[i]);
  const T = sum.totals || {};
  const totIncome = num(T.income), totBiz = num(T.business), totProfit = totIncome - totBiz;

  // last year, same months (so a year in progress compares like with like)
  let prevT = null;
  if (prev && ((prev.counts && prev.counts.total) || num((prev.totals || {}).income))) {
    const pm = new Map((prev.by_month || []).map(m => [String(m.month).slice(5, 7), m]));
    const ps = k => idx.reduce((a, mm) => a + num((pm.get(mm) || {})[k]), 0);
    prevT = { income: ps('income'), business: ps('business') };
    prevT.profit = prevT.income - prevT.business;
  }
  const vsLabel = `vs. ${year === thisYear && last < 12 ? `Jan–${MONTHS[last - 1]} ` : ''}${year - 1}`;

  // ---------------------------------------------------------------- KPI strip
  const kpis = [
    { label: 'Income', value: totIncome, series: income, d: prevT && delta(totIncome, prevT.income, { neutral: true, title: vsLabel }) },
    { label: 'Business expenses', value: totBiz, series: business, d: prevT && delta(totBiz, prevT.business, { neutral: true, title: vsLabel }) },
    { label: 'Net profit', value: totProfit, series: profit, key: true, d: prevT && delta(totProfit, prevT.profit, { title: vsLabel }) },
  ];
  kpis.push({ label: 'Needs review', value: null, text: String((store.status && store.status.review_count) || 0), series: [], href: '#/review', title: 'Transactions waiting for a decision' });
  const kpiStrip = h('section.card.ov-kpis', { 'aria-label': 'Key numbers', class: `n${kpis.length}` }, kpis.map(x => {
    const tag = x.href ? 'a' : 'div';
    return h(`${tag}.ov-kpi`, { class: x.key ? 'key' : '', href: x.href || null, title: x.title || null },
      h('div.ov-kpi-top', h('span.label', x.label), x.d || null),
      h('div.value', x.value == null ? x.text : money(x.value, { cents: false })),
      x.series && x.series.length > 1 ? sparkline(x.series, { color: x.key ? 'var(--chart-line)' : 'var(--ink-3)', cls: 'ov-spark' }) : h('div.ov-spark'));
  }));

  // ---------------------------------------------------------------- profit chart
  const chartHost = h('div.ov-chart');
  let mode = 'month';
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Group by' });
  for (const [id, label] of [['month', 'Month'], ['quarter', 'Quarter']]) {
    seg.append(h('button', { type: 'button', 'aria-pressed': String(mode === id), onclick: e => {
      mode = id; for (const b of seg.children) b.setAttribute('aria-pressed', String(b === e.currentTarget)); drawChart();
    } }, label));
  }
  function drawChart() {
    let labels = idx.map(mm => MONTHS[+mm - 1]), inc = income, exp = business, titles = labels.map(l => `${l} ${year}`);
    if (mode === 'quarter') {
      const nq = Math.ceil(idx.length / 3);
      labels = Array.from({ length: nq }, (_, i) => `Q${i + 1}`);
      titles = labels.map(l => `${l} ${year}`);
      const qs = arr => labels.map((_, i) => arr.slice(i * 3, i * 3 + 3).reduce((a, v) => a + v, 0));
      inc = qs(income); exp = qs(business);
    }
    clear(chartHost).append(comboChart({ labels, income: inc, expenses: exp, tipTitles: titles, height: 252,
      label: `${year} income, business expenses and net profit by ${mode}` }));
  }
  drawChart();
  const hero = card({ title: 'Profit', meta: String(year), action: seg, cls: 'ov-hero', id: 'ov-profit',
    body: [legend([['var(--chart-income)', 'Income'], ['var(--chart-expense)', 'Expenses'], ['var(--chart-line)', 'Net profit', 'line']]), chartHost] });

  // ---------------------------------------------------------------- cards
  const next = nextUpCard({ receipts, review });
  // Add-on cards ("overview" slot) join the grid: each slot wrapper uses display: contents.
  const grid = h('div.ov-grid', hero, next.el);
  clear(main).append(h('div.ov', kpiStrip, grid));
  const cleanup = renderSlot('overview', grid, { isCurrent: ctx.isCurrent, data: { year } });
  return cleanup;
}

// ---------------------------------------------------------------- next up
function loadDone() { try { return JSON.parse(localStorage.getItem(DONE_KEY) || '{}') || {}; } catch { return {}; } }
function saveDone(d) { try { localStorage.setItem(DONE_KEY, JSON.stringify(d)); } catch { /* per-viewer convenience only */ } }

function nextUpCard({ receipts, review }) {
  const items = [];
  const st = store.status || {};
  const rv = review ? listOf(review).items : [];
  const nRev = st.review_count ?? rv.length;
  if (nRev > 0) {
    const merchants = new Set(rv.map(t => (t.merchant || t.description || '').toUpperCase())).size;
    items.push({ id: 'review', sig: `review:${nRev}`, icon: 'inbox', href: '#/review', title: `Review ${plural(nRev, 'transaction')}`,
      meta: merchants ? `${plural(merchants, 'merchant')} · ~${Math.max(1, Math.round(merchants * 25 / 60))} min` : '' });
  }
  const rl = receipts ? (Array.isArray(receipts) ? receipts : (receipts.items || [])) : [];
  const open = rl.filter(r => ['suggested', 'unmatched', 'no_text'].includes(r.status));
  if (open.length) items.push({ id: 'receipts', sig: `receipts:${open.length}`, icon: 'receipt', href: '#/receipts?filter=attention',
    title: `Match ${plural(open.length, 'receipt')}`, meta: open.slice(0, 3).map(r => r.merchant ? displayMerchant({ merchant: r.merchant }) : (r.filename || 'Receipt')).join(' · ') });
  const b = st.backup;
  if (b && b.due) items.push({ id: 'backup', sig: `backup:${b.last_backup_at || 'never'}`, icon: 'shield', href: '#/settings?section=backup',
    title: 'Back up workspace', meta: b.days_since != null ? `${b.days_since} days` : 'Never backed up' });

  const done = loadDone();
  const meta = h('span');
  const body = h('div.ov-next-b');
  const el = h('section.card.ov-next-card', { 'aria-labelledby': 'ov-next-h' }, h('header.card-h', h('h2#ov-next-h', 'Next up'), meta), body);
  function draw(focusId) {
    const left = items.filter(i => done[i.id] !== i.sig);
    clear(meta).append(left.length ? h('span.ov-count.num', { 'aria-label': `${left.length} to do` }, String(left.length)) : null);
    clear(body);
    if (!left.length) {
      body.append(h('div.ov-done', { role: 'status' },
        h('div.ov-done-ring', h('span', icon('check', 34))),
        h('div.value', 'All caught up'),
        items.length ? h('div.ov-done-dots', { 'aria-hidden': 'true' }, items.map(i => h('span', { title: i.title }, icon(i.icon, 14)))) : null,
        items.length ? h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => { for (const i of items) delete done[i.id]; saveDone(done); draw(); } }, icon('refresh', 14), 'Undo') : null));
      return;
    }
    body.append(h('ul.ov-todo', items.map(i => {
      const d = done[i.id] === i.sig;
      const cb = h('button.ov-check', { type: 'button', role: 'checkbox', 'aria-checked': String(d), 'aria-label': `Mark “${i.title}” done`, dataset: { id: i.id },
        onclick: () => { if (d) delete done[i.id]; else done[i.id] = i.sig; saveDone(done); draw(i.id); } }, icon('check', 14));
      return h('li', { class: d ? 'is-done' : '' }, cb, h('span.ov-todo-ic', icon(i.icon, 16)),
        h('a.grow', { href: i.href }, h('span.t1', i.title), i.meta ? h('span.t2', i.meta) : null), icon('chevR', 16));
    })));
    if (focusId) { const f = body.querySelector(`.ov-check[data-id="${focusId}"]`) || body.querySelector('.ov-check, button'); if (f) f.focus({ preventScroll: true }); }
  }
  draw();
  return { el };
}

/** Loading skeleton shaped like the Overview (shown by the router while data loads). */
export function overviewSkeleton() {
  return h('div.ov', { 'aria-busy': 'true' },
    h('div.card.ov-kpis', [0, 1, 2, 3, 4].map(() => h('div.ov-kpi', sk('56%', 12), sk('70%', 24, 8, { marginTop: '8px' }), sk('100%', 28, 6, { marginTop: '8px' })))),
    h('div.ov-grid', h('div.card.ov-hero.card-pad', sk('30%', 14), h('div.sx-chart', [62, 54, 70, 48, 60, 52, 78, 58, 72, 50].map(v => h('span.sk', { style: { height: `${v}%` } })))),
      h('div.card.card-pad', sk('40%', 14), sk('100%', 120, 60, { marginTop: '24px' }))));
}
