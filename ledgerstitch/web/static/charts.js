// LedgerStitch — charts.js : small inline-SVG chart helpers (no chart library, no network).
// sparkline · comboChart (income/expense bars + net-profit line) · ring · gauge · rangeBar · miniBars · legend.
// Colours come from CSS tokens (style="fill:var(--…)") so both themes switch without a redraw.
// Every chart has an aria-label (or is aria-hidden when the number it decorates is already shown).
import { h, s, clear, money, moneyK, num } from './ui.js';

const css = (prop, v) => `${prop}:${v}`;

// ---------------------------------------------------------------- tooltip
let tipEl = null;
/** Show a tooltip with rows [[colourVar, label, valueText, bold?]] near (x, y); tip(null) hides it. */
export function tip(title, rows, x, y) {
  if (!tipEl) { tipEl = h('div.tip', { role: 'presentation', hidden: true }); document.body.append(tipEl); }
  if (title == null) { tipEl.hidden = true; return; }
  clear(tipEl).append(h('b', title), rows.map(([c, l, v, bold]) => h('div.r',
    h('span', c ? h('i', { style: { background: c } }) : null, l), bold ? h('b', { style: { display: 'inline', margin: 0 } }, v) : h('span', v))));
  tipEl.hidden = false;
  const r = tipEl.getBoundingClientRect();
  tipEl.style.left = `${Math.max(8, Math.min(window.innerWidth - r.width - 8, x + 14))}px`;
  tipEl.style.top = `${Math.max(8, y - r.height - 10)}px`;
}
export const hideTip = () => tip(null);

// ---------------------------------------------------------------- sparkline
/** Stretchable sparkline: 2px line + 10% fill + optional last-point dot. Decorative (aria-hidden). */
export function sparkline(values, { w = 160, h: ht = 32, color = 'var(--ink-3)', fill = true, dot = false, cls = 'spark' } = {}) {
  const vals = (values || []).map(num);
  const svg = s('svg', { class: cls, width: w, height: ht, viewBox: `0 0 ${w} ${ht}`, preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false' });
  if (vals.length < 2) return svg;
  const min = Math.min(...vals), max = Math.max(...vals), r = max - min || 1;
  const pts = vals.map((v, i) => [i / (vals.length - 1) * w, ht - 3 - (v - min) / r * (ht - 8)]);
  const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  if (fill) svg.append(s('path', { d: `${d} L${w} ${ht} L0 ${ht}Z`, style: `${css('fill', color)};opacity:.10` }));
  svg.append(s('path', { d, fill: 'none', style: `${css('stroke', color)};stroke-width:2`, 'vector-effect': 'non-scaling-stroke', 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  if (dot) { const [x, y] = pts[pts.length - 1]; svg.append(s('circle', { cx: x, cy: y, r: 2.5, style: css('fill', color) })); }
  return svg;
}

// ---------------------------------------------------------------- combo chart
function niceMax(v) {
  if (v <= 0) return 1000;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1.2 ? 1.2 : n <= 1.5 ? 1.5 : n <= 2 ? 2 : n <= 3 ? 3 : n <= 4 ? 4 : n <= 6 ? 6 : n <= 8 ? 8 : 12) * p;
}
/**
 * Income bars + expense bars + net-profit line on a shared axis, drawn at the measured width (no stretched text).
 * opts: {labels[], income[], expenses[], tipTitles[], height, annotate, label}
 * Returns a <div class="combo"> that redraws itself on resize.
 */
export function comboChart(opts) {
  const o = { height: 252, annotate: true, ...opts };
  const host = h('div.combo', { style: { height: `${o.height}px` } });
  let lastW = 0;
  const draw = () => {
    const W = Math.max(280, Math.round(host.clientWidth || 640));
    if (W === lastW) return;
    lastW = W;
    clear(host).append(comboSVG(o, W));
  };
  requestAnimationFrame(draw);
  if (window.ResizeObserver) new ResizeObserver(draw).observe(host);
  else window.addEventListener('resize', draw);
  return host;
}

function comboSVG({ labels, income, expenses, tipTitles, height: H, annotate, label }, W) {
  const inc = income.map(num), exp = expenses.map(num);
  const profit = inc.map((v, i) => v - exp[i]);
  const n = labels.length;
  const padL = 44, padR = 12, padT = 30, padB = 26;
  const lo = Math.min(0, ...profit);
  const max = niceMax(Math.max(...inc, ...exp, ...profit, 1));
  const min = lo < 0 ? -niceMax(-lo) : 0;
  const iw = W - padL - padR, ih = H - padT - padB, slot = iw / n, bw = Math.max(3, Math.min(14, slot / 4));
  const y = v => padT + ih - (v - min) / (max - min) * ih;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', class: 'combo-svg',
    'aria-label': label || `Income, expenses and net profit by period: ${labels.map((l, i) => `${l} income ${money(inc[i], { cents: false })}, expenses ${money(exp[i], { cents: false })}, profit ${money(profit[i], { cents: false })}`).join('; ')}` });
  const ticks = 3;
  for (let t = 0; t <= ticks; t++) {
    const v = min + (max - min) * t / ticks;
    svg.append(s('line', { x1: padL, x2: W - padR, y1: y(v), y2: y(v), style: css('stroke', 'var(--chart-grid)') }),
      s('text', { x: padL - 8, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 11, style: css('fill', 'var(--chart-axis)') }, v ? moneyK(v) : '$0'));
  }
  const pts = [];
  const hits = [];
  labels.forEach((l, i) => {
    const cx = padL + slot * i + slot / 2;
    const bar = (v, x, colour) => { const top = y(Math.max(0, v)), bot = y(Math.min(0, v)); if (Math.abs(bot - top) < .5) return null;
      return s('rect', { x, y: top, width: bw, height: bot - top, rx: Math.min(4, bw / 2), style: css('fill', colour) }); };
    svg.append(bar(inc[i], cx - bw - 1, 'var(--chart-income)'), bar(exp[i], cx + 1, 'var(--chart-expense)'));
    if (slot >= 22 || i % 2 === 0) svg.append(s('text', { x: cx, y: H - 8, 'text-anchor': 'middle', 'font-size': 11, style: css('fill', 'var(--chart-axis)') }, l));
    pts.push([cx, y(profit[i])]);
    hits.push(s('rect', { x: cx - slot / 2, y: padT, width: slot, height: ih, fill: 'transparent', 'data-i': i }));
  });
  if (n > 1) svg.append(s('path', { d: pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' '), fill: 'none',
    style: `${css('stroke', 'var(--chart-line)')};stroke-width:2.5`, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  pts.forEach(p => svg.append(s('circle', { cx: p[0], cy: p[1], r: 2.5, style: css('fill', 'var(--chart-line)') })));
  if (annotate && n >= 3 && profit.some(v => v)) {
    const best = profit.indexOf(Math.max(...profit)), worst = profit.indexOf(Math.min(...profit));
    const tag = (i, txt) => {
      const [x, yy] = pts[i];
      const pw = txt.length * 6.6 + 16, ph = 20, py = Math.max(2, yy - 12 - ph);
      const px = Math.max(padL, Math.min(W - padR - pw, x - pw / 2));
      return s('g', { 'aria-hidden': 'true' },
        s('line', { x1: x, x2: x, y1: py + ph, y2: yy - 5, style: css('stroke', 'var(--line-strong)') }),
        s('rect', { x: px, y: py, width: pw, height: ph, rx: 10, style: `${css('fill', 'var(--surface-2)')};${css('stroke', 'var(--line-strong)')}` }),
        s('text', { x: px + pw / 2, y: py + 14, 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 600, style: css('fill', 'var(--ink)') }, txt),
        s('circle', { cx: x, cy: yy, r: 5, style: `${css('fill', 'var(--surface)')};${css('stroke', 'var(--chart-line)')};stroke-width:2` }));
    };
    if (best !== worst) svg.append(tag(best, `Best ${moneyK(profit[best])}`), tag(worst, `Low ${moneyK(profit[worst])}`));
  }
  const cross = s('line', { x1: 0, x2: 0, y1: padT, y2: padT + ih, style: `${css('stroke', 'var(--ink-3)')};opacity:0`, 'stroke-dasharray': '3 3' });
  svg.append(cross, ...hits);
  for (const hit of hits) {
    const i = +hit.getAttribute('data-i');
    const bx = padL + slot * i + slot / 2;
    hit.addEventListener('mousemove', e => {
      cross.setAttribute('x1', bx); cross.setAttribute('x2', bx); cross.style.opacity = '1';
      tip((tipTitles && tipTitles[i]) || labels[i], [
        ['var(--chart-income)', 'Income', money(inc[i], { cents: false })],
        ['var(--chart-expense)', 'Expenses', money(exp[i], { cents: false })],
        ['var(--chart-line)', 'Net profit', money(profit[i], { cents: false }), true]], e.clientX, e.clientY);
    });
    hit.addEventListener('mouseleave', () => { hideTip(); cross.style.opacity = '0'; });
  }
  return svg;
}

/** Legend: items [[colourVar, label, 'line'?]] */
export function legend(items) {
  return h('div.legend', items.map(([c, l, kind]) => h('span', h('i', { class: kind === 'line' ? 'ln' : '', style: { background: c } }), l)));
}

// ---------------------------------------------------------------- ring
/**
 * Progress ring. segs: [{p, color}] for stacked segments. state: 'upcoming' draws a dashed outline (never an empty ring).
 * label/sub: centre text. ariaLabel: what it means in words.
 */
export function ring(p, { size = 120, stroke = 12, color = 'var(--primary)', track = 'var(--surface-3)', label = '', sub = '', state = '', segs = null, ariaLabel = '', labelSize } = {}) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, cx = size / 2;
  const svg = s('svg', { width: size, height: size, viewBox: `0 0 ${size} ${size}`, 'aria-hidden': 'true', focusable: 'false' });
  if (state === 'upcoming') {
    svg.append(s('circle', { cx, cy: cx, r, fill: 'none', style: `${css('stroke', 'var(--line-strong)')};stroke-width:2`, 'stroke-dasharray': '4 6' }));
  } else {
    svg.append(s('circle', { cx, cy: cx, r, fill: 'none', style: `${css('stroke', track)};stroke-width:${stroke}` }));
    const parts = segs || [{ p, color }];
    let off = 0;
    for (const sg of parts) {
      const len = Math.max(0, Math.min(100, num(sg.p))) / 100 * c;
      if (len > 0.5) svg.append(s('circle', { cx, cy: cx, r, fill: 'none', 'stroke-linecap': 'round', transform: `rotate(-90 ${cx} ${cx})`,
        'stroke-dasharray': `${Math.max(0.01, len - (parts.length > 1 ? 3 : 0))} ${c}`, 'stroke-dashoffset': -off, style: `${css('stroke', sg.color)};stroke-width:${stroke}` }));
      off += len;
    }
  }
  return h('div.ring', { role: 'img', 'aria-label': ariaLabel || `${label} ${sub}`.trim(), style: { width: `${size}px`, height: `${size}px` } }, svg,
    h('div.ring-c', label !== '' ? h('div.value', { style: { fontSize: `${labelSize || Math.round(size / 5)}px` } }, label) : null, sub ? h('div.ring-sub', sub) : null));
}

// ---------------------------------------------------------------- gauge
/** Semicircle gauge 0..1 with an optional marker at the fill end. Pair it with one word + $ (not colour alone). */
export function gauge(p, { size = 220, stroke = 16, color = 'var(--warn)', marker = true } = {}) {
  const v = Math.max(0, Math.min(1, num(p)));
  const r = (size - stroke) / 2, cx = size / 2, cy = size / 2, c = Math.PI * r;
  const arc = `M ${stroke / 2} ${cy} A ${r} ${r} 0 0 1 ${size - stroke / 2} ${cy}`;
  const svg = s('svg', { width: size, height: size / 2 + stroke, viewBox: `0 0 ${size} ${size / 2 + stroke}`, 'aria-hidden': 'true', focusable: 'false', class: 'gauge' },
    s('path', { d: arc, fill: 'none', 'stroke-linecap': 'round', style: `${css('stroke', 'var(--surface-3)')};stroke-width:${stroke}` }));
  if (v > 0) svg.append(s('path', { d: arc, fill: 'none', 'stroke-linecap': 'round', 'stroke-dasharray': `${v * c} ${c}`, style: `${css('stroke', color)};stroke-width:${stroke}` }));
  if (marker) {
    const a = Math.PI * (1 - v);
    svg.append(s('circle', { cx: cx + Math.cos(a) * r, cy: cy - Math.sin(a) * r, r: stroke / 2 + 3, style: `${css('fill', 'var(--surface)')};${css('stroke', 'var(--ink)')};stroke-width:2` }));
  }
  return svg;
}

// ---------------------------------------------------------------- bars
/** Savings range bar: low–high on a 0..max scale. */
export function rangeBar(low, high, max, { color = 'var(--chart-income)', title } = {}) {
  const lo = Math.max(0, num(low)), hi = Math.max(lo, num(high)), mx = Math.max(1, num(max));
  return h('div.rbar', { title: title || `${money(lo, { cents: false })} – ${money(hi, { cents: false })}`, 'aria-hidden': 'true' },
    hi > 0 ? h('i', { style: { left: `${lo / mx * 100}%`, width: `${Math.max(1.5, (hi - lo) / mx * 100)}%`, background: color } }) : null);
}
/** Small column bars with initials under them; the max bar is highlighted. values: number[]; labels: string[] */
export function miniBars(values, labels, { titles, label = '' } = {}) {
  const vals = values.map(v => Math.abs(num(v)));
  const mx = Math.max(...vals, 1);
  return h('div.mini-bars', { role: 'img', 'aria-label': label, style: { gridTemplateColumns: `repeat(${vals.length}, 1fr)` } }, vals.map((v, i) => h('div.mb', { title: titles ? titles[i] : null },
    h('i', { class: v === mx && v > 0 ? 'hi' : '', style: { height: `${v ? Math.max(6, v / mx * 100) : 2}%` } }), h('span', labels[i]))));
}
/** Stepped price line for a subscription: old → new (flat when unchanged). Decorative. */
export function stepLine(oldV, newV, { w = 96, h: ht = 28, color = 'var(--ink-3)' } = {}) {
  const a = num(oldV), b = num(newV);
  const min = Math.min(a, b), max = Math.max(a, b), r = max - min || 1;
  const yy = v => (max === min ? ht / 2 : ht - 4 - (v - min) / r * (ht - 8));
  const d = max === min ? `M2 ${yy(a)} L${w - 4} ${yy(a)}` : `M2 ${yy(a)} L${w * 0.55} ${yy(a)} L${w * 0.65} ${yy(b)} L${w - 4} ${yy(b)}`;
  return s('svg', { class: 'stepline', width: w, height: ht, viewBox: `0 0 ${w} ${ht}`, 'aria-hidden': 'true' },
    s('path', { d, fill: 'none', style: `${css('stroke', color)};stroke-width:2`, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }),
    s('circle', { cx: w - 4, cy: yy(b), r: 2.5, style: css('fill', color) }));
}
