// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — tax-docs.js : pieces shared by the Pay stubs and Investments screens.
// Both read documents in memory on this computer (POST multipart → a report); nothing is saved in the
// workspace, so a report lasts until you leave the screen. Exports re-send the same files.
import { api } from './api.js';
import { h, clear, icon, toast, money, chip, card, spinner, plural } from './ui.js';

/** Drop zone + hidden file input. opts: {id, title, help, formats: [labels], accept, acceptRe, onFiles(files)} */
export function dropZone({ id, title, help, formats = [], accept, acceptRe, onFiles }) {
  const input = h('input', { type: 'file', multiple: true, accept, hidden: true, id: `${id}-input` });
  const dz = h('div', { class: 'im-drop td-drop', role: 'button', tabindex: '0', 'aria-describedby': `${id}-help`, 'aria-label': `${title}: choose files, or drop them here` },
    h('span.im-drop-ic', icon('upload', 26)),
    h('b', title),
    h('span.muted', { id: `${id}-help` }, help || ['or ', h('span.im-link', 'choose files')]),
    formats.length ? h('div.im-fmts', formats.map(f => chip(f, 'outline'))) : null);
  const take = list => {
    const ok = list.filter(f => acceptRe.test(f.name));
    const bad = list.length - ok.length;
    if (bad) toast(`${plural(bad, 'file')} skipped`, { kind: 'warn', detail: `Supported: ${formats.join(', ')}` });
    if (ok.length) onFiles(ok);
  };
  dz.addEventListener('click', () => input.click());
  dz.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  let depth = 0;
  dz.addEventListener('dragenter', e => { e.preventDefault(); depth++; dz.classList.add('dragover'); });
  dz.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  dz.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; dz.classList.remove('dragover'); } });
  dz.addEventListener('drop', e => { e.preventDefault(); depth = 0; dz.classList.remove('dragover'); take([...e.dataTransfer.files]); });
  input.addEventListener('change', () => { take([...input.files]); input.value = ''; });
  // a file dropped next to the zone must not navigate the tab away
  const swallow = e => { if (e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files')) e.preventDefault(); };
  window.addEventListener('dragover', swallow);
  window.addEventListener('drop', swallow);
  return { el: [dz, input], cleanup: () => { window.removeEventListener('dragover', swallow); window.removeEventListener('drop', swallow); } };
}

/** "Read on this computer · not saved" — the honest one-liner both screens carry. */
export const memoryNote = () => h('span.td-mem', { title: 'The files are read in memory and discarded. Nothing is stored in this workspace.' },
  icon('lock', 12), 'Processed in memory, not saved');

/** POST the form and save the reply as a file (the token header can't ride on a plain link). */
export async function postDownload(path, form, fallbackName) {
  const res = await api(path, { method: 'POST', form, raw: true });
  const blob = await res.blob();
  let name = fallbackName;
  const cd = res.headers.get('content-disposition') || '';
  const m = cd.match(/filename\*=UTF-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
  if (m) { try { name = decodeURIComponent(m[1]); } catch { name = m[1]; } }
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: name, rel: 'noopener' });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  return name;
}

/** CSV and Excel download buttons; `form()` builds the multipart body again for each download. */
export function exportButtons(path, form, fallback) {
  return h('div.row.td-export', [['csv', 'CSV'], ['xlsx', 'Excel']].map(([fmt, label]) => {
    const b = h('button', { class: 'btn btn-sm', type: 'button', title: `Download as ${label}`, 'aria-label': `Download as ${label}` }, icon('download', 16), label);
    b.addEventListener('click', async () => {
      b.disabled = true;
      const orig = [...b.childNodes];
      clear(b).append(spinner(), label);
      try {
        const name = await postDownload(`${path}/export/${fmt}`, form(), `${fallback}.${fmt}`);
        toast('Download ready', { detail: `${name} · not encrypted, keep it safe` });
      } catch (e) {
        toast('Export failed', { kind: 'error', detail: e.message });
      } finally {
        b.disabled = false; clear(b).append(...orig);
      }
    });
    return b;
  }));
}

const SEVERITY = { money: ['Money found', 'pos', 'sparkle'], warning: ['Check this', 'warn', 'alert'], info: ['Good to know', 'info', 'info'] };
export const severityChip = sev => { const [t, tone, ic] = SEVERITY[sev] || SEVERITY.info; return chip(t, tone, { icon: ic }); };

/** Signals list: severity, title, whose, the explanation and its citation. Money signals first. */
export function signalsCard(signals, { title = 'Signals', empty = 'Nothing to flag' } = {}) {
  const order = { money: 0, warning: 1, info: 2 };
  const list = [...(signals || [])].sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3));
  const found = list.filter(s => s.severity === 'money' && s.amount != null).reduce((a, s) => a + Number(s.amount || 0), 0);
  return card({ title, id: 'td-signals', cls: 'flush td-signals', meta: list.length ? plural(list.length, 'item') : null,
    action: found ? chip(`${money(found, { cents: false })} found`, 'pos', { icon: 'sparkle' }) : null,
    body: list.length
      ? h('ul.td-sig-list', list.map(s => h('li.td-sig',
        h('div.td-sig-h', severityChip(s.severity), s.owner ? chip(s.owner === 'spouse' ? 'Spouse' : 'You', 'outline') : null,
          h('b.td-sig-t', s.title)),
        s.explanation ? h('p.td-sig-x', s.explanation) : null,
        s.citation ? h('p.small.muted.td-sig-c', icon('book', 12), s.citation) : null)))
      : h('div.tx-empty', icon('checkCircle', 28), h('span.muted', empty)) });
}

/** Warnings from parsing (a stub from last year, a box without basis, ...). Null when there are none. */
export function warningsCard(warnings) {
  if (!warnings || !warnings.length) return null;
  return card({ title: 'Read with care', cls: 'td-warnings', meta: plural(warnings.length, 'note'),
    body: h('ul.td-warn-list', warnings.map(w => h('li', icon('alert', 14), h('span', w)))) });
}

/** The label + disclaimer that every output carries. */
export function disclaimerFoot(text) {
  return text ? h('p.legal.td-legal', icon('info', 12), text) : null;
}

/** KPI strip in the report's style: [{label, value (money), sub, tone}] */
export function kpiStrip(items, label) {
  return h('section', { class: `card ov-kpis n${items.length} td-kpis`, 'aria-label': label },
    items.map(k => h('div.ov-kpi', { class: k.key ? 'key' : '' },
      h('div.ov-kpi-top', h('span.label', k.label)),
      h('div.value', k.text != null ? k.text : money(k.value, { cents: false, sign: !!k.sign })),
      k.sub ? h('div.t2', k.sub) : null)));
}
