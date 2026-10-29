// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — mock-taxdocs.js : in-browser mock of POST /api/paystubs and /api/investments (+ their
// exports), for ?mock=1. Invented employers, brokers, symbols and amounts only. The uploaded files are
// not read: each one is named in the result so the screens' file rows fill in.

const YEAR = new Date().getFullYear();
const ESTIMATE = 'ESTIMATE — not issued by employer';
const SUMMARY = 'SUMMARY — totals from your 1099s and statements; not a tax return';
const PLUS_FEATURES = ['FIFO / specific-lot gains from trade history', "wash sales across accounts and a spouse's",
  'tax-loss harvesting plan', 'target allocation, drift and rebalancing', 'lot selection'];

function filesOf(form) {
  if (!form || typeof form.getAll !== 'function') return [];
  const all = [...form.getAll('file').map(f => [f, 'you']), ...form.getAll('spouse_file').map(f => [f, 'spouse'])];
  return all.filter(([f]) => f && typeof f === 'object' && 'name' in f);
}

function paystubReport(form) {
  const files = filesOf(form);
  const fs = form.get('filing_status') || 'mfj';
  const final = form.get('final') === '1';
  const w2 = [
    { owner: 'you', employer: 'Northwind Labs', year: YEAR, source: 'sample', pay_date: `${YEAR}-10-17`, frequency: 'biweekly',
      periods_remaining: final ? 0 : 5, final, label: ESTIMATE,
      boxes: { 1: '88400.00', 2: '9850.00', 3: '96000.00', 4: '5952.00', 5: '96000.00', 6: '1392.00' },
      box12: { D: '7600.00', DD: '14400.00', W: '3200.00' }, box13_retirement_plan: true,
      box14: ['Sec. 125 pre-tax benefits 4,800.00 (Medical, Dental)'],
      notes: ['Projected from the year-to-date totals at the current pay rate.', 'Box 12 DD is the cost of employer health coverage; it is not taxable.'] },
    { owner: 'spouse', employer: 'Bluebird Clinic', year: YEAR, source: 'sample', pay_date: `${YEAR}-10-15`, frequency: 'semimonthly',
      periods_remaining: final ? 0 : 5, final, label: ESTIMATE,
      boxes: { 1: '61250.00', 2: '5100.00', 3: '64000.00', 4: '3968.00', 5: '64000.00', 6: '928.00' },
      box12: { D: '2750.00' }, box13_retirement_plan: true, box14: [],
      notes: ['Projected from the year-to-date totals at the current pay rate.'] },
  ];
  const signals = [
    { id: 'deferral_room', severity: 'money', owner: 'spouse', amount: '2850.00', title: '$2,850.00 of 401(k)/403(b) deferral room left',
      explanation: 'Raising the deferral for the remaining pay dates lowers this year’s taxable wages by up to this amount.', citation: 'IRC §402(g)' },
    { id: 'hsa_with_general_fsa', severity: 'warning', owner: 'you', amount: null, title: 'HSA together with a health FSA',
      explanation: 'An HSA needs a limited-purpose FSA. If this FSA is general-purpose, the HSA contributions may not be allowed. Check the plan documents.', citation: 'IRC §223(c)(1)' },
    { id: 'additional_medicare', severity: 'info', owner: null, amount: null, title: 'Additional Medicare Tax may be due',
      explanation: 'Employers withhold it only above $200,000 at one job. Combined household wages can still owe it when you file.', citation: 'IRC §3101(b)(2); Form 8959' },
  ];
  if (form.get('spouse_no_earned_income') === '1') signals.push({ id: 'dcap_no_earned_income', severity: 'warning', owner: null, amount: null,
    title: 'Dependent care FSA with one spouse not earning', explanation: 'The exclusion is limited to the lower earner’s income, so these benefits may become taxable.', citation: 'IRC §129(b)' });
  const stubs = files.map(([f, owner], i) => ({ source: f.name, layout: 'generic', owner, employer: owner === 'spouse' ? 'Bluebird Clinic' : 'Northwind Labs',
    pay_date: `${YEAR}-10-${String(15 + (i % 3)).padStart(2, '0')}`, warnings: [] }));
  return { year: YEAR, filing_status: fs, stubs, w2, signals, warnings: [], label: ESTIMATE,
    disclaimer: `${ESTIMATE}. Projected from pay stubs for planning; the employer's W-2 is the document of record. Not tax advice; no income tax is computed.` };
}

const HOLDINGS = [
  ['MMKT', 'SAMPLE MONEY MARKET FUND', 'cash', '3600.00', '3600.00'],
  ['IDXF', 'EXAMPLE TOTAL MARKET INDEX ETF', 'etf', '21480.00', '16900.00'],
  ['EXMPL', 'EXAMPLE ROBOTICS INC', 'equity', '9150.00', '11400.00'],
  ['SAMPL', 'SAMPLE FOODS CORP', 'equity', '4320.00', '3880.00'],
  ['BNDF', 'SPECIMEN AGGREGATE BOND FUND', 'bond', '7700.00', '7950.00'],
  ['GRWF', 'SAMPLE GROWTH FUND ADMIRAL', 'fund', '6240.00', '5100.00'],
  ['COINX', 'EXAMPLE DIGITAL ASSET TRUST', 'crypto', '1510.00', '2020.00'],
];

function investmentsReport(form) {
  const files = filesOf(form).map(([f]) => f);
  const positionsFile = (files.find(f => /position/i.test(f.name)) || {}).name;
  const holdings = HOLDINGS.map(([symbol, description, asset_class, mv, cost]) => ({ symbol, description, asset_class, account: 'XXXXX4821',
    quantity: null, price: null, market_value: mv, cost_basis: cost, unrealized_gain: (Number(mv) - Number(cost)).toFixed(2), source: positionsFile || 'sample' }));
  const total = holdings.reduce((a, x) => a + Number(x.market_value), 0);
  const pct = v => (v / total * 100).toFixed(1);
  const byClass = {};
  for (const x of holdings) byClass[x.asset_class] = (byClass[x.asset_class] || 0) + Number(x.market_value);
  const top = [...holdings].sort((a, b) => b.market_value - a.market_value);
  const gains = holdings.map(x => Number(x.unrealized_gain));
  const losses = gains.filter(g => g < 0);
  const lossSum = losses.reduce((a, g) => a + g, 0);
  const holdings_summary = {
    as_of: `${YEAR}-10-24`, positions: holdings.length, market_value: total.toFixed(2),
    cost_basis: holdings.reduce((a, x) => a + Number(x.cost_basis), 0).toFixed(2),
    unrealized_gain: gains.reduce((a, g) => a + g, 0).toFixed(2), unrealized_gains: gains.filter(g => g > 0).reduce((a, g) => a + g, 0).toFixed(2),
    unrealized_losses: lossSum.toFixed(2), loss_positions: losses.length, basis_unknown: 0,
    by_class: Object.entries(byClass).sort((a, b) => b[1] - a[1]).map(([name, v]) => ({ name, value: v.toFixed(2), pct: pct(v) })),
    by_account: [{ name: 'XXXXX4821', value: total.toFixed(2), pct: '100.0' }],
    top: top.map(x => ({ name: x.symbol, value: x.market_value, pct: pct(Number(x.market_value)) })),
    top5_pct: pct(top.slice(0, 5).reduce((a, x) => a + Number(x.market_value), 0)),
    concentrated: top.filter(x => Number(x.market_value) / total > 0.1).map(x => ({ name: x.symbol, value: x.market_value, pct: pct(Number(x.market_value)) })),
  };
  const payers = ['Example Brokerage LLC', 'Specimen Markets LLC'];
  const income = [
    ['1099-DIV', '1a', 'Total ordinary dividends', '2184.60', 'Form 1040 line 3b (Schedule B Part II if over $1,500)'],
    ['1099-DIV', '1b', 'Qualified dividends', '1702.35', 'Form 1040 line 3a'],
    ['1099-DIV', '2a', 'Total capital gain distributions', '318.40', 'Schedule D line 13'],
    ['1099-DIV', '5', 'Section 199A dividends', '96.10', 'Form 8995 (qualified business income deduction)'],
    ['1099-DIV', '7', 'Foreign tax paid', '41.25', 'Schedule 3 line 1 (or Form 1116)'],
    ['1099-INT', '1', 'Interest income', '527.80', 'Form 1040 line 2b (Schedule B Part I if over $1,500)'],
  ].map(([form, box, label, amount, destination]) => ({ form, box, label, amount, destination, payers }));
  const capital_gains = [
    { category: 'A', proceeds: '14200.00', cost_basis: '15650.00', market_discount: '0.00', wash_sale_disallowed: '120.00', gain: '-1330.00',
      label: 'Form 8949 Part I box A -> Schedule D line 1a (no adjustments) or 1b' },
    { category: 'D', proceeds: '22900.00', cost_basis: '17480.00', market_discount: '0.00', wash_sale_disallowed: '0.00', gain: '5420.00',
      label: 'Form 8949 Part II box D -> Schedule D line 8a (no adjustments) or 8b' },
  ];
  const statements = files.filter(f => f.name !== positionsFile).map(f => ({ source: f.name, kind: /\.csv$/i.test(f.name) ? '1099-B lots' : '1099',
    payer: payers[0], tax_year: YEAR, account: 'XXXXX4821', forms: {}, b_summary: [], lots: [], warnings: [] }));
  const signals = [
    { id: 'unrealized_losses', severity: 'money', owner: null, amount: Math.abs(lossSum).toFixed(2),
      title: `$${Math.abs(lossSum).toLocaleString('en-US', { minimumFractionDigits: 2 })} of unrealized losses in ${losses.length} positions`,
      explanation: 'Losses realized by December 31 offset this year’s gains, then up to $3,000 of other income. The rest carries forward.', citation: 'IRC §1211(b), §1212(b)' },
    { id: 'wash_sale_reported', severity: 'warning', owner: null, amount: '120.00', title: '$120.00 of losses disallowed as wash sales',
      explanation: 'Your broker already added these to the basis of the replacement shares. They come back when those shares are sold.', citation: 'IRC §1091' },
    { id: 'foreign_tax', severity: 'info', owner: null, amount: '41.25', title: '$41.25 of foreign tax paid',
      explanation: 'Usually claimed as a credit without Form 1116 when the total is small. A credit is normally worth more than a deduction.', citation: 'IRC §901, §904(j)' },
    { id: 'no_withholding', severity: 'info', owner: null, amount: null, title: 'No tax withheld on investment income',
      explanation: 'If your paychecks don’t cover it, an estimated payment may avoid an underpayment penalty.', citation: 'IRC §6654' },
  ];
  return { year: YEAR, filing_status: form.get('filing_status') || null, statements, income, capital_gains, short_term_net: '-1330.00', long_term_net: '5420.00',
    holdings, holdings_summary, signals, warnings: [], label: SUMMARY, plus_features: PLUS_FEATURES,
    disclaimer: `${SUMMARY}. The payer's 1099 is the document of record, and corrected 1099s are common through March. Not tax advice; no income tax is computed.` };
}

function csvOf(rows) {
  const c = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
  return rows.map(r => r.map(c).join(',')).join('\n') + '\n';
}

function exportFile(kind, r, fmt) {
  const rows = kind === 'paystubs'
    ? [[r.label], [], ['Owner', 'Employer', 'Box', 'Amount'], ...r.w2.flatMap(w => Object.entries(w.boxes).map(([b, v]) => [w.owner, w.employer, b, v]))]
    : [[r.label], [], ['Form', 'Box', 'What', 'Amount', 'Goes to'], ...r.income.map(i => [i.form, i.box, i.label, i.amount, i.destination])];
  const name = `${kind === 'paystubs' ? 'estimated-w2' : 'investments'}-${r.year}${fmt === 'xlsx' ? '-demo' : ''}.csv`;   // mock can't build a workbook
  return new Response(new Blob([csvOf(rows)], { type: 'text/csv' }), { status: 200, headers: { 'content-type': 'text/csv', 'content-disposition': `attachment; filename="${name}"` } });
}

/** Answer the two routes, or undefined. ctx: {json, err} from mock.js. */
export function taxDocRoutes(path, method, init, { json, err }) {
  const m = path.match(/^\/api\/(paystubs|investments)(?:\/export\/(csv|xlsx))?$/);
  if (!m || method !== 'POST') return undefined;
  const form = init && typeof FormData !== 'undefined' && init.body instanceof FormData ? init.body : new FormData();
  if (!filesOf(form).length && form.get('sample') !== '1') return err(422, m[1] === 'paystubs' ? 'Attach at least one pay stub.' : 'Attach at least one 1099, 1099-B CSV or positions CSV.');
  const r = m[1] === 'paystubs' ? paystubReport(form) : investmentsReport(form);
  return m[2] ? exportFile(m[1], r, m[2]) : json(r);
}
