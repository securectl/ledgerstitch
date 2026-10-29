// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — Investments: drop 1099s (composite PDFs or text), 1099-B lot CSVs and positions CSVs →
// totals by form and box with where each goes on the return, capital gains by Form 8949 box, a holdings
// snapshot with allocation, and the free investment signals (POST /api/investments). Read in memory, not
// saved. What LedgerStitch Plus adds (lot-level gains, harvesting, rebalancing) is a locked card.
import { api, MOCK } from './api.js';
import { h, clear, icon, money, chip, card, why, num, plural, fmtDate } from './ui.js';
import { setTopActions, setTopMeta } from './shell.js';
import { ring, legend } from './charts.js';
import { signupLink, openUpgrade } from './upsell.js';
import { dropZone, memoryNote, exportButtons, signalsCard, warningsCard, disclaimerFoot, kpiStrip } from './tax-docs.js';

const ACCEPT = '.pdf,.txt,.csv,application/pdf,text/plain,text/csv';
const ACCEPT_RE = /\.(pdf|txt|csv)$/i;
const FILING = [['', 'Not set'], ['single', 'Single'], ['mfj', 'Married filing jointly'], ['mfs', 'Married filing separately'], ['hoh', 'Head of household']];
const CLASS_LABEL = { equity: 'Stocks', etf: 'ETFs', fund: 'Funds', bond: 'Bonds', cash: 'Cash', option: 'Options', crypto: 'Crypto', other: 'Other' };
const CLASS_COLOR = ['var(--chart-income)', 'var(--chart-line)', 'var(--chart-expense)', 'var(--info)', 'var(--warn)', 'var(--pos)', 'var(--ink-3)', 'var(--danger)'];
const KIND_LABEL = { 1099: 'Composite 1099', '1099-B lots': '1099-B lots' };

export async function renderInvestments(main, params, ctx) {
  const files = [];
  let report = null;
  let runId = 0;

  const fsSel = h('select', { class: 'input', id: 'iv-filing' }, FILING.map(([v, t]) => new Option(t, v)));
  fsSel.addEventListener('change', run);
  const drop = dropZone({ id: 'iv-drop', title: 'Drop 1099s and positions', formats: ['1099 PDF', 'Text', '1099-B CSV', 'Positions CSV'],
    accept: ACCEPT, acceptRe: ACCEPT_RE, onFiles: list => { files.push(...list); drawFiles(); run(); } });
  const fileRows = h('div.rows.td-files');
  const results = h('div.td-results', { 'aria-live': 'polite' });
  const inputCard = card({ title: 'Tax forms and statements', meta: memoryNote(), cls: 'td-input', id: 'iv-input',
    body: [...drop.el, fileRows,
      why('What to add', h('div.td-opts', h('div.field', h('label', { for: 'iv-filing' }, 'Filing status'), fsSel, h('span.hint', 'Only used for the capital loss limit.'))),
        h('p', 'Composite 1099s from your brokers (1099-DIV, INT, B, MISC, NEC, R), a 1099-B transaction CSV for lot detail, and a positions export (Fidelity, Schwab, Vanguard, Robinhood-style, or any CSV with symbol, quantity and value columns). Account numbers and tax IDs are masked as they are read.'))] });

  setTopActions(null);
  setTopMeta(h('span.hide-sm', '1099 totals · holdings · signals'));
  clear(main).append(h('div.td', inputCard, results));
  drawFiles();
  drawResults();
  if (MOCK) run();
  return drop.cleanup;

  function formData() {
    const f = new FormData();
    for (const file of files) f.append('file', file, file.name);
    if (fsSel.value) f.append('filing_status', fsSel.value);
    if (MOCK && !files.length) f.append('sample', '1');
    return f;
  }

  async function run() {
    if (!files.length && !MOCK) { report = null; drawResults(); return; }
    const my = ++runId;
    clear(results).append(h('div.page-sk', { role: 'status', 'aria-busy': 'true', 'aria-label': 'Reading documents' },
      h('div.card.ov-kpis.n5', [0, 1, 2, 3, 4].map(() => h('div.ov-kpi', h('span.sk', { style: { width: '50%', height: '12px' } }), h('span.sk', { style: { width: '70%', height: '24px', marginTop: '8px' } }))))));
    try {
      const r = await api('/api/investments', { method: 'POST', form: formData() });
      if (my !== runId || !ctx.isCurrent()) return;
      report = r;
    } catch (e) {
      if (my !== runId || !ctx.isCurrent()) return;
      report = null;
      clear(results).append(h('div.error-box', { role: 'alert' }, icon('alert', 16), e.message));
      return;
    }
    drawFiles();
    drawResults();
  }

  function drawFiles() {
    clear(fileRows);
    const sts = (report && report.statements) || [];
    files.forEach((file, i) => {
      const st = sts.find(s => s.source === file.name);
      const pos = report && (report.holdings || []).filter(x => x.source === file.name).length;
      fileRows.append(h('div.trow.td-file',
        h('span.td-file-ic', icon('file', 18)),
        h('div.grow', h('div.t1', file.name),
          h('div.t2', st ? [KIND_LABEL[st.kind] || st.kind, st.payer ? ` · ${st.payer}` : '', st.tax_year ? ` · ${st.tax_year}` : '', st.account ? ` · ${st.account}` : '']
            : pos ? `Positions · ${plural(pos, 'holding')}` : report ? 'Nothing recognized in this file' : 'Waiting to be read')),
        h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', title: 'Remove', 'aria-label': `Remove ${file.name}`,
          onclick: () => { files.splice(i, 1); drawFiles(); run(); } }, icon('x', 16))));
    });
  }

  function drawResults() {
    if (!report) {
      clear(results).append(card({ title: 'Investments', cls: 'td-empty', body: h('div.tx-empty', icon('trendUp', 28),
        h('span.muted', 'Add your 1099s or a positions export to see income by form, gains and losses, and your allocation.')) }));
      return;
    }
    const inc = (form, box) => num(((report.income || []).find(i => i.form === form && i.box === box) || {}).amount);
    const hs = report.holdings_summary || null;
    setTopActions(exportButtons('/api/investments', formData, `investments-${report.year}`));
    clear(results).append(
      h('div.td-label', chip(report.label, 'info', { icon: 'info' }), h('span.muted.small', `Tax year ${report.year}`)),
      kpiStrip([
        { label: 'Ordinary dividends', value: inc('1099-DIV', '1a'), sub: `Qualified ${money(inc('1099-DIV', '1b'), { cents: false })}`, key: true },
        { label: 'Interest', value: inc('1099-INT', '1'), sub: '1099-INT box 1' },
        { label: 'Net short-term', value: report.short_term_net, sub: 'Form 8949 Part I', sign: true },
        { label: 'Net long-term', value: report.long_term_net, sub: 'Form 8949 Part II', sign: true },
        hs ? { label: 'Portfolio value', value: hs.market_value, sub: `${money(hs.unrealized_gain, { cents: false, sign: true })} unrealized` }
          : { label: 'Portfolio value', text: '—', sub: 'Add a positions CSV' },
      ], 'Totals'),
      h('div.td-grid',
        h('div.td-col', incomeCard(report.income || []), gainsCard(report.capital_gains || []), hs ? holdingsCard(report.holdings || [], hs) : null),
        h('div.td-col', signalsCard(report.signals, { title: 'Investment signals' }), plusCard(report.plus_features || []), warningsCard(report.warnings))),
      disclaimerFoot(report.disclaimer));
  }
}

function incomeCard(rows) {
  if (!rows.length) return null;
  return card({ title: '1099 totals', meta: 'All brokers, by form and box', cls: 'flush td-income',
    body: h('div.table-wrap', h('table.simple',
      h('caption.sr-only', '1099 totals by form and box, with where each goes on the return'),
      h('thead', h('tr', h('th', { scope: 'col' }, 'Form · box'), h('th', { scope: 'col' }, 'What'), h('th.r', { scope: 'col' }, 'Amount'), h('th', { scope: 'col' }, 'Goes to'))),
      h('tbody', rows.map(r => h('tr',
        h('td.line', `${r.form.replace(/^1099-/, '')} ${r.box}`),
        h('td', r.label, (r.payers || []).length > 1 ? h('span.muted.small', ` · ${plural(r.payers.length, 'payer')}`) : null,
          r.destination ? h('div.small.muted.td-dest-sm', r.destination) : null),
        h('td.r.money', money(r.amount)),
        h('td.small.muted', r.destination || '')))))) });
}

function gainsCard(rows) {
  if (!rows.length) return null;
  return card({ title: 'Capital gains', meta: 'From 1099-B, by Form 8949 box', cls: 'flush td-gains',
    body: h('div.table-wrap', h('table.simple',
      h('caption.sr-only', 'Capital gains and losses by Form 8949 box'),
      h('thead', h('tr', h('th', { scope: 'col' }, 'Box'), h('th.r', { scope: 'col' }, 'Proceeds'), h('th.r', { scope: 'col' }, 'Cost basis'), h('th.r', { scope: 'col' }, 'Wash sale'), h('th.r', { scope: 'col' }, 'Gain or loss'))),
      h('tbody', rows.map(r => h('tr', { title: r.label || '' },
        h('td.line', r.category),
        h('td.r.money', money(r.proceeds)),
        h('td.r.money', r.cost_basis == null ? '—' : money(r.cost_basis)),
        h('td.r.money', num(r.wash_sale_disallowed) ? money(r.wash_sale_disallowed) : '—'),
        h('td.r.money', { class: num(r.gain) < 0 ? 'neg' : '' }, money(r.gain, { sign: true }))))))) });
}

function holdingsCard(holdings, hs) {
  const classes = hs.by_class || [];
  const segs = classes.map((c, i) => ({ p: num(c.pct), color: CLASS_COLOR[i % CLASS_COLOR.length] }));
  const top = [...holdings].sort((a, b) => num(b.market_value) - num(a.market_value)).slice(0, 12);
  const conc = new Set((hs.concentrated || []).map(c => c.name));
  return card({ title: 'Holdings', meta: hs.as_of ? `As of ${fmtDate(hs.as_of)}` : plural(hs.positions || holdings.length, 'position'), cls: 'td-holdings',
    action: (hs.concentrated || []).length ? chip(`${plural(hs.concentrated.length, 'position')} over 10%`, 'warn', { icon: 'alert' }) : null,
    body: [
      h('div.td-alloc',
        ring(100, { size: 132, stroke: 14, segs, label: money(hs.market_value, { cents: false }), sub: 'value', labelSize: 18,
          ariaLabel: `Allocation: ${classes.map(c => `${CLASS_LABEL[c.name] || c.name} ${c.pct}%`).join(', ')}` }),
        h('div.grow', legend(classes.map((c, i) => [CLASS_COLOR[i % CLASS_COLOR.length], `${CLASS_LABEL[c.name] || c.name} · ${c.pct}%`])),
          h('p.small.muted.td-alloc-n', `Top 5 positions: ${hs.top5_pct}% · unrealized gains ${money(hs.unrealized_gains, { cents: false })}, losses ${money(hs.unrealized_losses, { cents: false })}`))),
      h('div.table-wrap', h('table.simple',
        h('caption.sr-only', 'Largest holdings'),
        h('thead', h('tr', h('th', { scope: 'col' }, 'Symbol'), h('th', { scope: 'col' }, 'Account'), h('th.r', { scope: 'col' }, 'Value'), h('th.r', { scope: 'col' }, 'Unrealized'))),
        h('tbody', top.map(x => h('tr',
          h('td', h('b', x.symbol || '—'), conc.has(x.symbol) ? [' ', chip('>10%', 'warn')] : null, h('div.small.muted', x.description || '', ' · ', CLASS_LABEL[x.asset_class] || x.asset_class || '')),
          h('td.small', x.account || '—'),
          h('td.r.money', money(x.market_value)),
          h('td.r.money', { class: num(x.unrealized_gain) < 0 ? 'neg' : '' }, x.cost_basis == null ? '—' : money(x.unrealized_gain, { sign: true }))))))),
    ] });
}

/** What Plus adds, as one locked card in upsell.js's look (no add-on is installed in Community). */
function plusCard(features) {
  if (!features.length) return null;
  const key = h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => openUpgrade(null) }, icon('key', 16), 'Have a key?');
  return card({ title: 'Act on it', cls: 'ls-locked ov-locked td-plus', action: chip('Plus', 'outline', { icon: 'lock', title: 'Part of LedgerStitch Plus' }),
    body: h('div.ls-locked-b',
      h('span.ov-lock-ic', { 'aria-hidden': 'true' }, icon('lock', 22)),
      h('p.ls-locked-t', 'LedgerStitch Plus turns these totals into a plan before December 31:'),
      h('ul.td-plus-list', features.map(f => h('li', icon('check', 14), f))),
      h('div.ls-locked-a', signupLink() || h('button', { class: 'btn btn-primary', type: 'button', onclick: () => openUpgrade(null) }, icon('sparkle', 16), 'Get the full product'), key)) });
}
