// LedgerStitch — export-menu.js : one Export button → a sheet listing every export format.
// Downloads go through api.download (the token header can't ride on a plain link). Add-ons add sections
// through the "export" UI slot: renderCard(el, ctx) with ctx.data = {year(), close(), item(spec)}.
import { download } from './api.js';
import { h, clear, icon, toast, modal, spinner } from './ui.js';
import { taxYear, store } from './state.js';
import { renderSlot } from './addons.js';

// [format, title, caption, icon, fallback file name]
const GROUPS = [
  ['For your CPA', [
    ['xlsx', 'Excel workbook', 'Summary, Schedule C, every transaction', 'table', 'expense-report.xlsx'],
  ]],
  ['Accounting software', [
    ['qbo', 'QuickBooks Online', 'Bank-upload CSV', 'file', 'quickbooks-online.csv'],
    ['iif', 'QuickBooks Desktop', 'IIF import file', 'file', 'quickbooks-desktop.iif'],
  ]],
  ['Files', [
    ['csv', 'CSV', 'One row per transaction', 'file', 'transactions.csv'],
  ]],
];

/** One download row of the Export sheet. spec: [format, title, caption, icon, fallback name, path?, extra query?] */
export function exportItem([fmt, title, sub, ic, fallback, path, extra], { year, close } = {}) {
  const b = h('button', { class: 'xp-item', type: 'button' },
    h('span.xp-ic', icon(ic, 18)), h('span.grow', h('span.t1', title), h('span.t2', sub)), h('span.xp-go', icon('download', 16)));
  b.addEventListener('click', async () => {
    const go = b.querySelector('.xp-go');
    const orig = [...go.childNodes];
    const y = typeof year === 'function' ? year() : (year || taxYear());
    b.disabled = true; b.setAttribute('aria-busy', 'true');
    clear(go).append(spinner());
    try {
      const name = await download(path || `/api/export/${fmt}`, { year: y, ...(extra || {}) }, `ledgerstitch-${y}-${fallback}`);
      toast('Download ready', { detail: `${name} · not encrypted, keep it safe` });
    } catch (e) {
      if (e.status !== 402) toast('Export failed', { kind: 'error', detail: e.message });
      else if (close) close();
    } finally {
      b.disabled = false; b.removeAttribute('aria-busy'); clear(go).append(...orig);
    }
  });
  return b;
}

/** The header button. opts: {year: () => number} */
export function exportButton(opts = {}) {
  return h('button', { class: 'btn', type: 'button', onclick: () => openExport(opts), 'aria-haspopup': 'dialog' }, icon('download', 18), h('span.btn-l', 'Export'));
}

export function openExport({ year } = {}) {
  let y = (typeof year === 'function' ? year() : year) || taxYear();
  const ySel = h('select', { class: 'pill', 'aria-label': 'Year to export' });
  for (const v of [...new Set([y + 1, y, y - 1, y - 2, taxYear()])].sort((a, b) => b - a)) ySel.append(new Option(String(v), String(v)));
  ySel.value = String(y);
  ySel.addEventListener('change', () => { y = Number(ySel.value); });
  const review = (store.status && store.status.review_count) || 0;
  const opts = { year: () => y, close: () => m.close() };
  const addons = h('div.xp-addons');
  const body = h('div.xp',
    h('div.xp-year', h('span.label', 'Tax year'), ySel),
    review ? h('a.xp-note', { href: '#/review', onclick: () => m.close() }, icon('inbox', 14), `${review} still in review`, icon('chevR', 14)) : null,
    GROUPS.map(([g, items]) => h('section.xp-g', h('h3', g), items.map(it => exportItem(it, opts)))),
    addons,
    h('p.legal', icon('lock', 12), 'Made on this computer · files aren’t encrypted'));
  const m = modal({ title: 'Export', body, sheet: true });
  const cleanup = renderSlot('export', addons, { data: { ...opts, item: spec => exportItem(spec, opts) } });
  m.result.then(cleanup);
  return m;
}
