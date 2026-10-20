// LedgerStitch — Report: lane totals, Schedule C, top categories, month by month, by account.
// Exports live in the top-bar Export sheet (export-menu.js).
import { api } from './api.js';
import { h, s, clear, icon, fmtMoney, fmtMoney0, fmtMonth, num, plural, LANE_LABEL, money, card, chip } from './ui.js';
import { store, loadSettings, loadAccounts, accountLabel, taxYear } from './state.js';
import { setTopActions, setTopMeta, bindScreenContext } from './shell.js';
import { exportButton } from './export-menu.js';
import { firstRunCard } from './states.js';

let disclaimerText = null;
async function loadDisclaimer() {
  if (disclaimerText !== null) return disclaimerText;
  try {
    const r = await fetch('disclaimer.txt', { cache: 'no-store' });
    disclaimerText = r.ok ? (await r.text()).trim() : '';
  } catch { disclaimerText = ''; }
  return disclaimerText;
}

export async function renderReport(main, params, ctx) {
  await Promise.all([loadSettings().catch(() => null), loadAccounts()]);
  let year = Number(params.get('year')) || taxYear();
  const yearSel = h('select', { class: 'pill', 'aria-label': 'Tax year', id: 'report-year' });
  for (const y of [...new Set([year + 1, year, year - 1, year - 2, taxYear()])].sort((a, b) => b - a)) yearSel.append(new Option(String(y), String(y)));
  yearSel.value = String(year);
  yearSel.addEventListener('change', () => { year = Number(yearSel.value); draw(); });
  const content = h('div.rp');
  const askOpts = { screen: 'report', mode: 'ask' };
  const bizName = (store.settings && store.settings.business_name) || (store.status && store.status.vault_name) || 'Your business';
  setTopActions(yearSel, exportButton({ year: () => year }));
  setTopMeta(h('span.hide-sm', bizName));
  clear(main).append(content);
  const unbindAsk = bindScreenContext(ctx, askOpts);
  await draw();
  return unbindAsk;

  async function draw() {
    history.replaceState(null, '', `${location.pathname}${location.search}#/report${year !== taxYear() ? `?year=${year}` : ''}`);
    clear(content).append(h('div.page-sk', { role: 'status', 'aria-busy': 'true' }, h('div.card.ov-kpis.n5', [0, 1, 2, 3, 4].map(() => h('div.ov-kpi', h('span.sk', { style: { width: '50%', height: '12px' } }), h('span.sk', { style: { width: '70%', height: '24px', marginTop: '8px' } }))))));
    let sum;
    const [sumRes, disc] = await Promise.allSettled([api('/api/summary', { query: { year } }), loadDisclaimer()]);
    if (!ctx.isCurrent()) return;
    if (sumRes.status === 'rejected') {
      clear(content).append(h('div.error-box', { role: 'alert' }, icon('alert', 16), sumRes.reason.message));
      return;
    }
    sum = sumRes.value || {};
    const T = sum.totals || {};
    const counts = sum.counts || {};
    const reviewCount = sum.review_count ?? counts.needs_review ?? 0;
    clear(content);

    if (!counts.total && !num(T.business) && !num(T.personal) && !num(T.income)) {
      content.append(store.accounts.length
        ? card({ title: String(year), body: h('div.tx-empty', icon('chart', 28), h('span.muted', `No transactions in ${year}`), h('div.row', h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { yearSel.value = String(taxYear()); year = taxYear(); draw(); } }, `Show ${taxYear()}`), h('a', { class: 'btn btn-sm btn-primary', href: '#/import' }, 'Import statements'))) })
        : firstRunCard());
      content.append(footer(sum, disc.value));
      return;
    }

    // ---- lane totals (KPI strip)
    const transfersExcluded = num(T.excluded ?? (num(T.transfers_out) + num(T.transfers_in)));
    const kpi = (label, value, sub, { href = null, key = false, lane = null } = {}) => h(href ? 'a.ov-kpi' : 'div.ov-kpi', { class: key ? 'key' : '', href },
      h('div.ov-kpi-top', h('span.label', lane ? [h('i', { class: `dot lane-dot-${lane}`, 'aria-hidden': 'true' }), ' '] : null, label)), h('div.value', money(value, { cents: false })), sub ? h('div.t2', sub) : null);
    content.append(h('section.card.ov-kpis.n5.rp-kpis', { 'aria-label': 'Totals' },
      kpi('Business', T.business, plural(counts.business ?? 0, 'transaction'), { key: true, lane: 'business', href: '#/ledger?lane=business' }),
      kpi('Personal', T.personal, plural(counts.personal ?? 0, 'transaction'), { lane: 'personal', href: '#/ledger?lane=personal' }),
      kpi('Income', T.income, plural(counts.income ?? 0, 'transaction'), { lane: 'income', href: '#/ledger?lane=income' }),
      kpi('Transfers excluded', transfersExcluded, plural(counts.transfer ?? 0, 'transaction'), { lane: 'transfer', href: '#/ledger?lane=transfer' }),
      kpi('Needs review', T.review, reviewCount ? plural(reviewCount, 'transaction') : 'All reviewed', { lane: 'review', href: '#/review' })));

    const grid = h('div.report-grid');
    grid.append(scheduleC(sum, reviewCount));
    grid.append(topCategoriesCard(sum));
    grid.append(monthlyCard(sum));
    if ((sum.by_account || []).length) grid.append(byAccountCard(sum));
    content.append(grid, footer(sum, disc.value));
  }
}

/** deductible_pct is a fraction (1.0, 0.5). Tolerate a 0–100 percent from older backends. */
function dedFrac(l) {
  const v = l.deductible_pct;
  if (v === null || v === undefined || v === '') return 1;
  const n = num(v);
  return n > 1 ? n / 100 : n;
}
const isMeals = l => /^L?24b$/i.test(String(l.line || ''));
function dedLabel(l) {
  const f = dedFrac(l);
  const p = `${Math.round(f * 100)}%`;
  return f < 1 && isMeals(l) ? `${p} (meals)` : p;
}

function scheduleC(sum, reviewCount) {
  const lines = sum.by_schedule_c || [];
  const tot = sum.schedule_c_total || {};
  const anyLimited = lines.some(l => dedFrac(l) < 1);
  const el = h('section.card.rp-sc');
  el.append(h('header.card-h', h('h2', 'Schedule C'), h('span.meta', 'Part II · business lane'),
    reviewCount ? h('div.act', h('a', { class: 'chip warn', href: '#/review', title: 'Not in these totals until reviewed' }, icon('inbox', 12), `${reviewCount} to review`)) : null));
  if (!lines.length) {
    el.append(h('div.card-b', h('div.tx-empty', icon('bars', 28), h('span.muted', 'No business expenses yet'), h('a', { class: 'btn btn-sm', href: '#/review' }, 'Review'))));
    return el;
  }
  el.append(h('div.table-wrap', h('table.simple',
    h('caption.sr-only', 'Business expenses by Schedule C line'),
    h('thead', h('tr', h('th', { scope: 'col' }, 'Line'), h('th', { scope: 'col' }, 'Expense'), h('th.r', { scope: 'col' }, 'Amount'),
      anyLimited ? h('th.r', { scope: 'col' }, 'Deductible') : null)),
    h('tbody', lines.map(l => h('tr',
      h('td.line', String(l.line || '').replace(/^L/, '')),
      h('td', l.name || l.label || l.line, l.count ? h('span.muted.small', ` · ${l.count}`) : null,
        dedFrac(l) < 1 ? [' ', chip(dedLabel(l), 'warn', { title: `Only ${dedLabel(l)} of this line is deductible` })] : null),
      h('td.r.money', fmtMoney(l.gross ?? l.total)),
      anyLimited ? h('td.r.money', fmtMoney(l.deductible ?? num(l.gross) * dedFrac(l)),
        h('div.small.muted', dedLabel(l))) : null))),
    h('tfoot', h('tr', h('td'), h('td', 'Total expenses'), h('td.r.money', fmtMoney(tot.gross ?? lines.reduce((a, l) => a + num(l.gross), 0))),
      anyLimited ? h('td.r.money', fmtMoney(tot.deductible ?? lines.reduce((a, l) => a + num(l.deductible), 0))) : null)),
  )));
  if (sum.meals_note && anyLimited) el.append(h('footer.card-f', h('span.legal', { title: sum.meals_note }, icon('info', 12), 'Meals at 50% · limits applied')));
  return el;
}

// ---------------------------------------------------------------- charts
let tip = null;
function tooltip() {
  if (!tip) { tip = h('div.chart-tooltip', { role: 'presentation', hidden: true }); document.body.append(tip); }
  return tip;
}
function showTip(e, title, value) {
  const t = tooltip();
  clear(t).append(h('b', title), value);
  t.hidden = false;
  const x = Math.min(window.innerWidth - t.offsetWidth - 12, e.clientX + 14);
  const y = Math.max(8, e.clientY - t.offsetHeight - 10);
  t.style.left = `${x}px`; t.style.top = `${y}px`;
}
function hideTip() { if (tip) tip.hidden = true; }

function topCategoriesCard(sum) {
  const cats = (sum.by_category || [])
    .filter(c => ['business', 'personal', 'review'].includes(c.lane) && num(c.total) > 0)
    .sort((a, b) => num(b.total) - num(a.total));
  const laneSeg = h('div.seg', { role: 'group', 'aria-label': 'Which spending' });
  const opts = [['business', 'Business'], ['all', 'All spending']];
  let mode = 'business';
  const chartHost = h('div');
  for (const [id, label] of opts) {
    const b = h('button', { type: 'button', 'aria-pressed': String(id === mode), onclick: () => { mode = id; laneSeg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b))); drawChart(); } }, label);
    laneSeg.append(b);
  }
  function drawChart() {
    let list = mode === 'business' ? cats.filter(c => c.lane === 'business') : cats;
    // Merge same category across lanes when showing all.
    if (mode === 'all') {
      const m = new Map();
      for (const c of list) { const x = m.get(c.category) || { category: c.category, total: 0, count: 0 }; x.total += num(c.total); x.count += c.count || 0; m.set(c.category, x); }
      list = [...m.values()].sort((a, b) => b.total - a.total);
    }
    const top = list.slice(0, 8);
    const rest = list.slice(8);
    if (rest.length) top.push({ category: `Other (${rest.length})`, total: rest.reduce((a, c) => a + num(c.total), 0), count: rest.reduce((a, c) => a + (c.count || 0), 0), other: true });
    clear(chartHost).append(top.length ? hbarChart(top) : h('p.muted', 'No spending in this lane yet.'));
  }
  drawChart();
  return card({ title: 'Top categories', action: laneSeg, body: chartHost });
}

function hbarChart(items) {
  const W = 560, rowH = 34, labelW = 190, valueW = 92, barMax = W - labelW - valueW - 12;
  const H = items.length * rowH;
  const max = Math.max(...items.map(i => num(i.total)), 1);
  const svg = s('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Spending by category: ' + items.map(i => `${i.category} ${fmtMoney0(i.total)}`).join(', ') });
  items.forEach((it, i) => {
    const y = i * rowH;
    const w = Math.max(3, (num(it.total) / max) * barMax);
    const g = s('g');
    const label = it.category.length > 26 ? it.category.slice(0, 25) + '…' : it.category;
    g.append(
      s('text', { x: 0, y: y + rowH / 2 + 4, class: 'label-ink' }, label),
      s('rect', { x: labelW, y: y + 9, width: barMax, height: 16, rx: 4, fill: 'var(--surface-2)' }),
      s('rect', { x: labelW, y: y + 9, width: w, height: 16, rx: 4, class: it.other ? 'bar bar-muted' : 'bar' }),
      s('text', { x: W, y: y + rowH / 2 + 4, 'text-anchor': 'end', class: 'value-ink' }, fmtMoney0(it.total)),
    );
    const hit = s('rect', { x: 0, y, width: W, height: rowH, class: 'hit' });
    hit.addEventListener('mousemove', e => { g.classList.add('hov'); showTip(e, it.category, `${fmtMoney(it.total)} · ${plural(it.count || 0, 'transaction')}`); });
    hit.addEventListener('mouseleave', () => { g.classList.remove('hov'); hideTip(); });
    g.append(hit);
    svg.append(g);
  });
  return svg;
}

function monthlyCard(sum) {
  const months = (sum.by_month || []).slice().sort((a, b) => (a.month < b.month ? -1 : 1));
  const year = sum.year || (months[0] && String(months[0].month).slice(0, 4));
  // Always show all 12 months so gaps are visible.
  const all = [];
  for (let m = 1; m <= 12; m++) {
    const key = `${year}-${String(m).padStart(2, '0')}`;
    all.push(months.find(x => String(x.month).slice(0, 7) === key) || { month: key, business: 0, personal: 0, review: 0, income: 0 });
  }
  let mode = 'business';
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Which amounts' });
  const host = h('div');
  for (const [id, label] of [['business', 'Business'], ['personal', 'Personal'], ['income', 'Income']]) {
    const b = h('button', { type: 'button', 'aria-pressed': String(id === mode), onclick: () => { mode = id; seg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b))); drawIt(); } }, label);
    seg.append(b);
  }
  function drawIt() { clear(host).append(columnChart(all, mode)); }
  drawIt();
  return card({ title: 'Month by month', action: seg, body: host, cls: 'span-2' });
}

function niceMax(v) {
  if (v <= 0) return 100;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

function columnChart(months, key) {
  const W = 960, H = 240, padL = 56, padB = 28, padT = 12, plotW = W - padL, plotH = H - padB - padT;
  const vals = months.map(m => num(m[key]));
  const max = niceMax(Math.max(...vals));
  const slot = plotW / months.length;
  const bw = Math.min(44, slot - 14);
  const svg = s('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': `${LANE_LABEL[key] || key} by month: ` + months.map(m => `${fmtMonth(m.month)} ${fmtMoney0(m[key])}`).join(', ') });
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i, y = padT + plotH - (v / max) * plotH;
    svg.append(s('line', { x1: padL, x2: W, y1: y, y2: y, class: 'grid' }),
      s('text', { x: padL - 10, y: y + 4, 'text-anchor': 'end' }, fmtMoney0(v).replace('.00', '')));
  }
  months.forEach((m, i) => {
    const v = num(m[key]);
    const x = padL + i * slot + (slot - bw) / 2;
    const bh = v > 0 ? Math.max(2, (v / max) * plotH) : 0;
    const y = padT + plotH - bh;
    const g = s('g');
    // Rounded top, square baseline.
    if (bh > 0) {
      const r = Math.min(4, bh, bw / 2);
      g.append(s('path', { class: 'bar', d: `M${x},${padT + plotH} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${padT + plotH} Z` }));
    }
    g.append(s('text', { x: x + bw / 2, y: H - 8, 'text-anchor': 'middle' }, fmtMonth(m.month)));
    const hit = s('rect', { x: padL + i * slot, y: padT, width: slot, height: plotH + padB, class: 'hit' });
    hit.addEventListener('mousemove', e => { g.classList.add('hov'); showTip(e, `${fmtMonth(m.month)} ${String(m.month).slice(0, 4)}`, `${LANE_LABEL[key]}: ${fmtMoney(v)}`); });
    hit.addEventListener('mouseleave', () => { g.classList.remove('hov'); hideTip(); });
    g.append(hit);
    svg.append(g);
  });
  svg.append(s('line', { x1: padL, x2: W, y1: padT + plotH, y2: padT + plotH, stroke: 'var(--border-strong)' }));
  return svg;
}

function byAccountCard(sum) {
  const rows = sum.by_account || [];
  return h('section.card.span-2',
    h('header.card-h', h('h2', 'By account'), h('span.meta', 'Check against each statement')),
    h('div.table-wrap', h('table.simple',
      h('caption.sr-only', 'Totals by account'),
      h('thead', h('tr', h('th', { scope: 'col' }, 'Account'), h('th.r', { scope: 'col' }, 'Business'), h('th.r', { scope: 'col' }, 'Personal'),
        h('th.r', { scope: 'col' }, 'Needs review'), h('th.r', { scope: 'col' }, 'Income'), h('th.r', { scope: 'col' }, 'Transfers'), h('th.r', { scope: 'col' }, 'Rows'))),
      h('tbody', rows.map(a => h('tr',
        h('td', a.label || accountLabel(a.account_id)),
        h('td.r.money', fmtMoney(a.business)), h('td.r.money', fmtMoney(a.personal)), h('td.r.money', fmtMoney(a.review)),
        h('td.r.money', fmtMoney(a.income)), h('td.r.money', fmtMoney(num(a.transfers_out) + num(a.transfers_in))),
        h('td.r.num', String(a.count ?? ''))))))));
}

function footer(sum, disclaimer) {
  const text = disclaimer || sum.disclaimer || 'LedgerStitch organizes your statements for review by you and your tax professional. It is not tax advice.';
  return h('p.legal.rp-foot', { role: 'note', title: text }, icon('info', 12), 'Not tax advice · totals exclude transfers and duplicates');
}
