// LedgerStitch — states.js : shared empty / caught-up states (Evergreen v2).
import { api } from './api.js';
import { h, s, icon, toast, busy } from './ui.js';
import { store } from './state.js';

/** Stacked statement sheets + plus badge, drawn with shapes (no images). */
function illustration() {
  const g = (...k) => s('g', {}, ...k);
  const rect = (a) => s('rect', a);
  return s('svg', { class: 'sx-illo', viewBox: '0 0 240 160', width: 240, height: 160, 'aria-hidden': 'true', focusable: 'false' },
    s('ellipse', { cx: 120, cy: 146, rx: 92, ry: 8, style: 'fill:var(--surface-3)' }),
    s('g', { transform: 'rotate(-8 92 80)' },
      rect({ x: 52, y: 28, width: 80, height: 104, rx: 12, style: 'fill:var(--surface-2);stroke:var(--line-strong)' }),
      rect({ x: 64, y: 46, width: 44, height: 6, rx: 3, style: 'fill:var(--line-strong)' }),
      rect({ x: 64, y: 60, width: 56, height: 4, rx: 2, style: 'fill:var(--surface-3)' }),
      rect({ x: 64, y: 70, width: 50, height: 4, rx: 2, style: 'fill:var(--surface-3)' }),
      rect({ x: 64, y: 80, width: 54, height: 4, rx: 2, style: 'fill:var(--surface-3)' })),
    s('g', { transform: 'rotate(6 140 78)' },
      rect({ x: 104, y: 22, width: 84, height: 110, rx: 12, style: 'fill:var(--surface);stroke:var(--line-strong)' }),
      rect({ x: 118, y: 40, width: 36, height: 6, rx: 3, style: 'fill:var(--ink-3);opacity:.5' }),
      rect({ x: 118, y: 56, width: 56, height: 4, rx: 2, style: 'fill:var(--surface-3)' }),
      rect({ x: 118, y: 66, width: 48, height: 4, rx: 2, style: 'fill:var(--surface-3)' }),
      rect({ x: 118, y: 76, width: 56, height: 4, rx: 2, style: 'fill:var(--surface-3)' }),
      rect({ x: 118, y: 96, width: 56, height: 20, rx: 6, style: 'fill:var(--primary-soft)' }),
      s('path', { d: 'M126 106l5 5 10-10', fill: 'none', style: 'stroke:var(--primary-text);stroke-width:2.4', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })),
    g(s('circle', { cx: 196, cy: 34, r: 18, style: 'fill:var(--primary)' }),
      s('path', { d: 'M196 25v18M187 34h18', style: 'stroke:var(--primary-ink);stroke-width:2.6', 'stroke-linecap': 'round' })),
    s('path', { d: 'M40 40l2.5 6.5L49 49l-6.5 2.5L40 58l-2.5-6.5L31 49l6.5-2.5z', style: 'fill:var(--ink-3);opacity:.6' }));
}

/**
 * First run: “Connect or drop a statement”. Bank connections are not built yet, so “Connect bank” is a
 * disabled “Soon” button. A sample-data button appears only when the backend offers it
 * (status.sample_data.available → POST status.sample_data.endpoint).
 */
export function firstRunCard({ title = 'Connect or drop a statement' } = {}) {
  const sample = store.status && store.status.sample_data;
  const sampleBtn = sample && sample.available ? h('button', { class: 'btn', type: 'button', onclick: e => busy(e.currentTarget, async () => {
    try { await api(sample.endpoint || '/api/setup/sample', { method: 'POST' }); location.reload(); }
    catch (err) { toast('Couldn’t load sample data', { kind: 'error', detail: err.message }); }
  }, 'Loading…') }, icon('sparkle', 16), 'Try sample data') : null;
  return h('section.card.sx-first', { 'aria-labelledby': 'sx-first-h' },
    h('div.sx-center',
      illustration(),
      h('h2.sx-h#sx-first-h', title),
      h('div.sx-steps', { 'aria-label': 'Three steps' },
        h('span', icon('link', 16), 'Connect'), h('i.sx-dash', { 'aria-hidden': 'true' }),
        h('span', icon('upload', 16), 'Drop'), h('i.sx-dash', { 'aria-hidden': 'true' }),
        h('span', icon('sparkle', 16), 'Auto-sort')),
      h('div.sx-actions',
        h('a', { class: 'btn btn-primary', href: '#/import' }, icon('upload', 18), 'Drop a statement'),
        h('button', { class: 'btn', type: 'button', disabled: true, title: 'Bank connections are coming soon' }, icon('link', 18), 'Connect bank', h('span.chip.outline.soon', 'Soon')),
        sampleBtn),
      h('p.legal', icon('lock', 12), 'Read and encrypted on this computer')));
}

/** “All caught up” with a few static confetti marks. */
export function caughtUp({ title = 'All caught up', sub = '', action = null } = {}) {
  const dots = [[18, 22, 'var(--primary)'], [82, 16, 'var(--pos)'], [12, 70, 'var(--info)'], [88, 66, 'var(--warn)'], [30, 88, 'var(--lane-personal)'], [72, 90, 'var(--primary)']];
  return h('div.sx-center.sx-caught', { role: 'status' },
    h('div.sx-confetti', { 'aria-hidden': 'true' }, dots.map(([x, y, c], i) => h('i', { style: { left: `${x}%`, top: `${y}%`, background: c, transform: `rotate(${i * 37}deg)` } }))),
    h('div.sx-done', icon('check', 36)),
    h('h2.sx-h', title),
    sub ? h('div.muted.num', sub) : null,
    action ? h('div.sx-actions', action) : null);
}
