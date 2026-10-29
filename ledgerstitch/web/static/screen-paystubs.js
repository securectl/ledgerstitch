// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — Pay stubs: drop the latest stub per job (yours and a spouse's) → a year-end projection,
// an estimated W-2 per employer and the free payroll signals (POST /api/paystubs). Read in memory, not
// saved; exports re-send the files to /api/paystubs/export/{csv|xlsx}. No income tax is computed.
import { api, MOCK } from './api.js';
import { h, clear, icon, money, chip, card, why, fmtDate, num, plural } from './ui.js';
import { setTopActions, setTopMeta } from './shell.js';
import { dropZone, memoryNote, exportButtons, signalsCard, warningsCard, disclaimerFoot, kpiStrip } from './tax-docs.js';

const ACCEPT = '.pdf,.txt,application/pdf,text/plain';
const ACCEPT_RE = /\.(pdf|txt)$/i;
export const W2_BOXES = { 1: 'Wages, tips, other compensation', 2: 'Federal income tax withheld', 3: 'Social Security wages',
  4: 'Social Security tax withheld', 5: 'Medicare wages and tips', 6: 'Medicare tax withheld', 10: 'Dependent care benefits' };
const BOX12 = { C: 'Group-term life over $50,000', D: '401(k) deferrals', E: '403(b) deferrals', G: '457(b) deferrals',
  AA: 'Roth 401(k)', BB: 'Roth 403(b)', DD: 'Employer health coverage (not taxable)', W: 'HSA (employee + employer)' };
const FREQ = [['', 'Detect'], ['weekly', 'Weekly'], ['biweekly', 'Every 2 weeks'], ['semimonthly', 'Twice a month'], ['monthly', 'Monthly']];
const FILING = [['', 'Detect'], ['single', 'Single'], ['mfj', 'Married filing jointly'], ['mfs', 'Married filing separately']];
const who = o => (o === 'spouse' ? 'Spouse' : 'You');

export async function renderPaystubs(main, params, ctx) {
  const files = [];                 // [{file, owner}]
  let owner = 'you';
  let report = null;
  let runId = 0;

  const ownerSeg = h('div.segmented', { role: 'group', 'aria-label': 'Whose pay stubs are you adding' },
    ['you', 'spouse'].map(o => h('button', { type: 'button', 'aria-pressed': String(o === owner), dataset: { owner: o },
      onclick: e => { owner = o; for (const b of ownerSeg.children) b.setAttribute('aria-pressed', String(b === e.currentTarget)); } }, who(o))));
  const sel = (id, label, opts) => {
    const s = h('select', { class: 'input', id }, opts.map(([v, t]) => new Option(t, v)));
    s.addEventListener('change', run);
    return h('div.field', h('label', { for: id }, label), s);
  };
  const sw = (id, label, hint) => {
    const i = h('input', { type: 'checkbox', id });
    i.addEventListener('change', run);
    return h('div.field', h('label.switch', i, h('span.track', { 'aria-hidden': 'true' }), label), hint ? h('span.hint', hint) : null);
  };
  const freqF = sel('ps-frequency', 'Pay frequency', FREQ);
  const fsF = sel('ps-filing', 'Filing status', FILING);
  const finalF = sw('ps-final', 'These are the year’s last stubs', 'Use the YTD totals as final; no projection to December 31.');
  const noEarnF = sw('ps-noearn', 'One of you has no earned income', 'Flags a dependent care FSA that would become taxable.');
  const val = id => main.querySelector(`#${id}`);

  const drop = dropZone({ id: 'ps-drop', title: 'Drop pay stubs', formats: ['PDF', 'Text'], accept: ACCEPT, acceptRe: ACCEPT_RE,
    onFiles: list => { for (const f of list) files.push({ file: f, owner }); drawFiles(); run(); } });
  const fileRows = h('div.rows.td-files');
  const results = h('div.td-results', { 'aria-live': 'polite' });

  const inputCard = card({ title: 'Pay stubs', meta: memoryNote(), cls: 'td-input', id: 'ps-input',
    body: [h('div.td-who', h('span.label', 'Adding stubs for'), ownerSeg), ...drop.el, fileRows,
      why('Options', h('div.td-opts', freqF, fsF, finalF, noEarnF),
        h('p', 'Add the latest stub from each job, yours and your spouse’s. Older stubs from the same employer are ignored. Rippling-style, ADP-style and most text-based PDF stubs are read; scanned images are not.'))] });

  setTopActions(null);
  setTopMeta(h('span.hide-sm', 'Year-end projection · estimated W-2'));
  clear(main).append(h('div.td', inputCard, results));
  drawFiles();
  drawResults();
  if (MOCK) run();   // demo: a synthetic household straight away
  return drop.cleanup;

  function formData() {
    const f = new FormData();
    for (const { file, owner: o } of files) f.append(o === 'spouse' ? 'spouse_file' : 'file', file, file.name);
    f.append('owner', 'you');
    const freq = val('ps-frequency') && val('ps-frequency').value;
    const fs = val('ps-filing') && val('ps-filing').value;
    if (freq) f.append('frequency', freq);
    if (fs) f.append('filing_status', fs);
    if (val('ps-final') && val('ps-final').checked) f.append('final', '1');
    if (val('ps-noearn') && val('ps-noearn').checked) f.append('spouse_no_earned_income', '1');
    if (MOCK && !files.length) f.append('sample', '1');
    return f;
  }

  async function run() {
    if (!files.length && !MOCK) { report = null; drawResults(); return; }
    const my = ++runId;
    clear(results).append(h('div.page-sk', { role: 'status', 'aria-busy': 'true', 'aria-label': 'Reading pay stubs' },
      h('div.card.ov-kpis.n4', [0, 1, 2, 3].map(() => h('div.ov-kpi', h('span.sk', { style: { width: '50%', height: '12px' } }), h('span.sk', { style: { width: '70%', height: '24px', marginTop: '8px' } }))))));
    try {
      const r = await api('/api/paystubs', { method: 'POST', form: formData() });
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
    const stubs = (report && report.stubs) || [];
    files.forEach((it, i) => {
      const st = stubs.find(s => s.source === it.file.name);
      const ownerSel = h('select', { class: 'pill', 'aria-label': `Whose stub is ${it.file.name}` }, ['you', 'spouse'].map(o => new Option(who(o), o, false, o === it.owner)));
      ownerSel.addEventListener('change', () => { it.owner = ownerSel.value; run(); });
      fileRows.append(h('div.trow.td-file',
        h('span.td-file-ic', icon('file', 18)),
        h('div.grow', h('div.t1', it.file.name),
          h('div.t2', st ? [st.employer || 'Employer not found', st.pay_date ? ` · paid ${fmtDate(st.pay_date)}` : '', st.warnings && st.warnings.length ? ` · ${plural(st.warnings.length, 'note')}` : ''] : 'Waiting to be read')),
        ownerSel,
        h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', title: 'Remove', 'aria-label': `Remove ${it.file.name}`,
          onclick: () => { files.splice(i, 1); drawFiles(); run(); } }, icon('x', 16))));
    });
  }

  function drawResults() {
    if (!report) {
      clear(results).append(card({ title: 'Estimated W-2', cls: 'td-empty', body: h('div.tx-empty', icon('wallet', 28),
        h('span.muted', 'Add a recent pay stub to see your projected W-2 and what you may be leaving on the table.')) }));
      return;
    }
    const w2 = report.w2 || [];
    const sum = box => w2.reduce((a, w) => a + num((w.boxes || {})[box]), 0);
    const found = (report.signals || []).filter(s => s.severity === 'money').reduce((a, s) => a + num(s.amount), 0);
    setTopActions(exportButtons('/api/paystubs', formData, `estimated-w2-${report.year}`));
    clear(results).append(
      h('div.td-label', chip(report.label, 'warn', { icon: 'alert' }), h('span.muted.small', `Tax year ${report.year} · ${filingName(report.filing_status)}`)),
      kpiStrip([
        { label: 'Projected wages (box 1)', value: sum('1'), sub: plural(w2.length, 'employer'), key: true },
        { label: 'Federal tax withheld', value: sum('2'), sub: 'Box 2, all jobs' },
        { label: 'Social Security withheld', value: sum('4'), sub: 'Box 4, all jobs' },
        { label: 'Money found', value: found, sub: found ? 'See the signals below' : 'Nothing flagged' },
      ], 'Totals'),
      h('div.td-grid',
        h('div.td-col', w2.map(w2Card)),
        h('div.td-col', signalsCard(report.signals, { title: 'Payroll signals' }), warningsCard(report.warnings))),
      disclaimerFoot(report.disclaimer));
  }
}

const filingName = fs => (FILING.find(([v]) => v === fs) || [null, fs || 'Filing status not set'])[1];

function w2Card(w) {
  const basis = w.final ? 'YTD as final' : `${plural(w.periods_remaining || 0, 'pay date')} left · ${(FREQ.find(([v]) => v === w.frequency) || [null, w.frequency || ''])[1]}`;
  const boxes = Object.keys(w.boxes || {}).sort((a, b) => Number(a) - Number(b));
  const b12 = Object.entries(w.box12 || {});
  return card({ title: w.employer || 'Employer not found', meta: basis, cls: 'flush td-w2', action: chip(who(w.owner), 'outline', { icon: 'user' }),
    body: h('div.table-wrap', h('table.simple',
      h('caption.sr-only', `Estimated W-2 from ${w.employer || 'this employer'}`),
      h('thead', h('tr', h('th', { scope: 'col' }, 'Box'), h('th', { scope: 'col' }, 'What'), h('th.r', { scope: 'col' }, 'Estimate'))),
      h('tbody',
        boxes.map(b => h('tr', h('td.line', b), h('td', W2_BOXES[b] || `Box ${b}`), h('td.r.money', money(w.boxes[b])))),
        b12.map(([code, v]) => h('tr', h('td.line', `12${code}`), h('td', BOX12[code] || `Code ${code}`), h('td.r.money', money(v)))),
        h('tr', h('td.line', '13'), h('td', 'Retirement plan'), h('td.r', w.box13_retirement_plan ? 'Yes' : 'No')),
        (w.box14 || []).map(line => h('tr', h('td.line', '14'), h('td', { colspan: 2 }, line)))))),
    foot: (w.notes || []).length ? why('How this was estimated', h('ul', w.notes.map(n => h('li', n)))) : null });
}
