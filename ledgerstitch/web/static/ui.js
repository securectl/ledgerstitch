// LedgerStitch — ui.js : DOM helper, formatting, icons, toasts, modals.
// No innerHTML with data — every user/bank string goes through text nodes.

const SVG_NS = 'http://www.w3.org/2000/svg';

/** h('div.card#id', {attrs}, ...children) */
export function h(sel, attrs, ...children) {
  if (attrs == null || typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs)) {
    if (attrs !== undefined && attrs !== null) children.unshift(attrs);
    attrs = {};
  }
  const m = sel.match(/^([a-z0-9-]+)?((?:[.#][\w-]+)*)$/i);
  const tag = (m && m[1]) || 'div';
  const el = document.createElement(tag);
  if (m && m[2]) {
    for (const part of m[2].match(/[.#][\w-]+/g) || []) {
      if (part[0] === '.') el.classList.add(part.slice(1));
      else el.id = part.slice(1);
    }
  }
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

function applyAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class' || k === 'className') { String(v).split(/\s+/).filter(Boolean).forEach(c => el.classList.add(c)); }
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'value' && 'value' in el) el.value = v;
    else if (k === 'checked' || k === 'selected' || k === 'disabled' || k === 'multiple' || k === 'required') { el[k] = !!v; if (v) el.setAttribute(k, ''); }
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === true) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** Empty `el`. Returns a null-safe appender so `clear(el).append(a, cond ? b : null)` never prints "null". */
export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return { append: (...kids) => append(el, kids), el };
}

/** Build an SVG element. */
export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// ---------------------------------------------------------------- icons
// Small stroke icons (24x24 viewBox), drawn here so nothing loads from the network.
const ICONS = {
  lock: 'M7 11V8a5 5 0 0 1 10 0v3 M5 11h14v10H5z M12 15v2',
  unlock: 'M7 11V8a5 5 0 0 1 9.6-2 M5 11h14v10H5z M12 15v2',
  shield: 'M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z M9 12l2 2 4-4',
  laptop: 'M4 5h16v11H4z M2 19h20',
  eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  sparkle: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z',
  upload: 'M12 16V4 M7 9l5-5 5 5 M4 16v4h16v-4',
  file: 'M6 3h8l5 5v13H6z M14 3v5h5',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  checkCircle: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M8 12.5l3 3 5-6',
  alert: 'M12 3L2 20h20z M12 10v4 M12 17v.5',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 11v6 M12 7.5v.5',
  x: 'M6 6l12 12 M18 6L6 18',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M20 20l-4-4',
  download: 'M12 4v12 M7 11l5 5 5-5 M4 20h16',
  trash: 'M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13',
  copy: 'M9 9h11v11H9z M5 15H4V4h11v1',
  key: 'M14 10a4 4 0 1 0-1.2 2.8L21 21 M17 17l2-2 M15 15l2-2',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  party: 'M4 20l4-12 8 8z M14 4l1 2 M19 9l2-1 M17 3l-1 3 M20 14l-2-1 M10 8c1-3 3-4 5-4',
  arrowRight: 'M5 12h14 M13 6l6 6-6 6',
  plus: 'M12 5v14 M5 12h14',
  keyboard: 'M3 6h18v12H3z M7 10h.01 M11 10h.01 M15 10h.01 M7 14h10',
  bank: 'M3 10l9-6 9 6 M5 10v8 M9.5 10v8 M14.5 10v8 M19 10v8 M3 20h18',
  chart: 'M4 20V10 M10 20V4 M16 20v-7 M22 20H2',
  refresh: 'M20 11a8 8 0 0 0-14.9-3.9L3 9 M3 4v5h5 M4 13a8 8 0 0 0 14.9 3.9L21 15 M21 20v-5h-5',
  inbox: 'M3 13l3-8h12l3 8 M3 13v6h18v-6 M3 13h5l1.5 3h5L16 13h5',
  paperclip: 'M21 11.5l-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l7.9-7.9',
  receipt: 'M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6 M9 16h3',
  link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1 M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
  calendar: 'M4 6h16v14H4z M4 10h16 M8 3v4 M16 3v4',
  repeat: 'M17 2l3 3-3 3 M4 11V9a4 4 0 0 1 4-4h12 M7 22l-3-3 3-3 M20 13v2a4 4 0 0 1-4 4H4',
  trendUp: 'M3 17l6-6 4 4 8-8 M15 7h6v6',
  piggy: 'M5 11a6 5 0 0 1 11-2h2l2 2v3l-2 1-1 3h-3v-2H9v2H6l-1-3a4 4 0 0 1 0-4z M14 8V6 M17 12h.01',
  wallet: 'M4 7a2 2 0 0 1 2-2h11a1 1 0 0 1 1 1v2 M4 7v10a2 2 0 0 0 2 2h13a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1H6a2 2 0 0 1-2-2z M16 13h.01',
  question: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7 M12 17v.5',
  disk: 'M3 14h18v5H3z M5 14l2-9h10l2 9 M17 16.5h.01',
  plug: 'M9 2v6 M15 2v6 M6 8h12v3a6 6 0 0 1-12 0z M12 17v5',
  scan: 'M4 8V5a1 1 0 0 1 1-1h3 M16 4h3a1 1 0 0 1 1 1v3 M20 16v3a1 1 0 0 1-1 1h-3 M8 20H5a1 1 0 0 1-1-1v-3 M8 12h8',
  chip: 'M7 7h10v10H7z M10 10h4v4h-4z M9 3v4 M15 3v4 M9 17v4 M15 17v4 M3 9h4 M3 15h4 M17 9h4 M17 15h4',
  terminal: 'M3 5h18v14H3z M7 10l3 2-3 2 M12 15h5',
  // Evergreen v2 set (24px grid, stroke)
  home: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  list: 'M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01',
  shieldCheck: 'M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6zM9 12l2 2 4-4',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
  chevD: 'M6 9l6 6 6-6', chevR: 'M9 6l6 6-6 6', chevL: 'M15 6l-6 6 6 6', chevUD: 'M8 9l4-4 4 4M16 15l-4 4-4-4',
  xCircle: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM15 9l-6 6M9 9l6 6',
  car: 'M5 17h14M5 17a2 2 0 1 0 4 0M15 17a2 2 0 1 0 4 0M3 17v-5l2-5h14l2 5v5M3 12h18',
  minus: 'M5 12h14',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  arrowUp: 'M12 19V5m-6 6 6-6 6 6', arrowDown: 'M12 5v14m-6-6 6 6 6-6',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
  sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  moon: 'M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z',
  map: 'M9 4 3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14',
  building: 'M4 21V5l8-2v18M12 21V9l8 2v10M8 9h.01M8 13h.01M8 17h.01M16 13h.01M16 17h.01',
  flag: 'M4 22V4M4 4h13l-2 4 2 4H4',
  filter: 'M3 5h18l-7 8v6l-4 2v-8z',
  trendDown: 'M3 7l6 6 4-4 8 8M15 17h6v-6',
  table: 'M3 4h18v16H3zM3 10h18M9 4v16',
  eyeOff: 'M17.94 17.94A10 10 0 0 1 12 20c-7 0-11-8-11-8a18 18 0 0 1 5.06-5.94M9.9 4.24A9 9 0 0 1 12 4c7 0 11 8 11 8a18 18 0 0 1-2.16 3.19M1 1l22 22',
  cpu: 'M6 6h12v12H6zM9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3',
  cloud: 'M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z',
  heart: 'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z',
  phone: 'M5 2h14v20H5zM11 18h2',
  briefcase: 'M3 7h18v13H3zM8 7V4h8v3M3 13h18',
  percent: 'M19 5 5 19M6.5 9a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM17.5 20a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
  book: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5',
  zap: 'M13 2 3 14h9l-1 8 10-12h-9z',
  layers: 'M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z',
  tag: 'M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82zM7 7h.01',
  users: 'M16,21v-2a4,4,0,0,0-4-4H6a4,4,0,0,0-4,4v2M9,11a4,4,0,1,0,0-8,4,4,0,0,0,0,8M22,21v-2a4,4,0,0,0-3-3.87M16,3.13a4,4,0,0,1,0,7.75',
  user: 'M20,21v-2a4,4,0,0,0-4-4H8a4,4,0,0,0-4,4v2M12,11a4,4,0,1,0,0-8,4,4,0,0,0,0,8z',
  hash: 'M4 9h16M4 15h16M10 3 8 21M16 3l-2 18',
  zip: 'M6 3h12v18H6zM11 3v2M13 5v2M11 7v2M13 9v2M11 13h2v3h-2z',
  bars: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
};

export function icon(name, size = 18) {
  const d = ICONS[name] || ICONS.info;
  const el = s('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false' });
  for (const seg of d.split(' M')) el.append(s('path', { d: seg.startsWith('M') ? seg : 'M' + seg }));
  return el;
}

/** Wordmark: lane-stripe mark + "LedgerStitch". `onDark` for the green hero. */
export function logo({ onDark = false } = {}) {
  const ink = onDark ? '#FFFFFF' : 'currentColor';
  const tile = onDark ? 'rgba(255,255,255,0.14)' : '#1F6F5C';
  return s('svg', { viewBox: '0 0 184 32', height: 26, role: 'img', 'aria-label': 'LedgerStitch' },
    s('title', {}, 'LedgerStitch'),
    s('rect', { x: 0, y: 0, width: 32, height: 32, rx: 8, fill: tile }),
    s('rect', { x: 7, y: 8.5, width: 18, height: 3.2, rx: 1.6, fill: '#FFFFFF' }),
    s('rect', { x: 7, y: 14.4, width: 13, height: 3.2, rx: 1.6, fill: '#FFFFFF', 'fill-opacity': 0.72 }),
    s('rect', { x: 7, y: 20.3, width: 8, height: 3.2, rx: 1.6, fill: '#FFFFFF', 'fill-opacity': 0.45 }),
    s('circle', { cx: 23.2, cy: 21.9, r: 2.6, fill: '#9EE0C9' }),
    s('text', { x: 42, y: 22.5, fill: ink, 'font-size': 19, 'font-weight': 700, 'letter-spacing': '-0.4',
      'font-family': '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif' },
      'Ledger', s('tspan', { 'font-weight': 500, 'fill-opacity': onDark ? 0.8 : 0.62 }, 'Stitch')),
  );
}

// ---------------------------------------------------------------- formatting
const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const usd0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const int = new Intl.NumberFormat('en-US');
const pct = new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 0 });

export const num = v => { const n = typeof v === 'number' ? v : parseFloat(v); return Number.isFinite(n) ? n : 0; };
export const fmtMoney = v => usd.format(num(v));
export const fmtMoney0 = v => usd0.format(num(v));
export const fmtInt = v => int.format(num(v));
export const fmtPct = v => pct.format(num(v));
/** Signed amount: out → negative. Uses a real minus sign for legibility. */
export function signedAmount(t) {
  const a = num(t.amount);
  return t.direction === 'out' ? -a : a;
}
export function moneyCell(t) {
  const v = signedAmount(t);
  const txt = (v < 0 ? '−' : '+') + usd.format(Math.abs(v));
  return h('span', { class: 'money ' + (v < 0 ? 'neg' : 'pos'), 'aria-label': (v < 0 ? 'money out ' : 'money in ') + usd.format(Math.abs(v)) }, txt);
}
const dateFmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
const dateFmtShort = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
const monthFmt = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: 'UTC' });
export function parseISO(d) { if (!d) return null; const x = new Date(String(d).slice(0, 10) + 'T00:00:00Z'); return isNaN(x) ? null : x; }
export const fmtDate = d => { const x = parseISO(d); return x ? dateFmt.format(x) : (d || ''); };
export const fmtDateShort = d => { const x = parseISO(d); return x ? dateFmtShort.format(x) : (d || ''); };
export const fmtMonth = ym => { const x = parseISO(String(ym).slice(0, 7) + '-01'); return x ? monthFmt.format(x) : ym; };
export function fmtRange(a, b) {
  if (!a) return '';
  if (!b || a === b) return fmtDate(a);
  const ya = a.slice(0, 4), yb = b.slice(0, 4);
  return ya === yb ? `${fmtDateShort(a)} – ${fmtDateShort(b)}, ${ya}` : `${fmtDate(a)} – ${fmtDate(b)}`;
}
export const plural = (n, one, many) => `${fmtInt(n)} ${n === 1 ? one : (many || one + 's')}`;

// ---------------------------------------------------------------- lanes
export const LANES = [
  { id: 'business', label: 'Business', key: 'B', blurb: 'Deductible business expense' },
  { id: 'personal', label: 'Personal', key: 'P', blurb: 'Not on your business return' },
  { id: 'transfer', label: 'Transfer', key: 'T', blurb: 'Card payments & moves between your accounts' },
  { id: 'income', label: 'Income', key: 'I', blurb: 'Money in: sales, deposits, payouts' },
];
export const LANE_LABEL = { business: 'Business', personal: 'Personal', transfer: 'Transfer', income: 'Income', review: 'Needs review' };
export function laneChip(lane) {
  const l = lane || 'review';
  return h('span', { class: `chip lane-${l}` }, h('i.dot', { 'aria-hidden': 'true' }), LANE_LABEL[l] || l);
}
export const SOURCE_LABEL = { user: 'You', rule: 'Your rule', heuristic: 'Built-in rule', ai: 'AI', none: 'No suggestion' };

// ---------------------------------------------------------------- announcer & toasts
export function announce(msg) {
  const el = document.getElementById('announcer');
  if (!el) return;
  el.textContent = '';
  setTimeout(() => { el.textContent = msg; }, 30);
}

/** toast('Saved', {kind:'ok'|'error'|'warn', detail, action:{label,onClick}, timeout}) */
export function toast(title, opts = {}) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const kind = opts.kind || 'ok';
  const t = h('div', { class: `toast ${kind}`, role: kind === 'error' ? 'alert' : 'status' },
    h('span.ic', { 'aria-hidden': 'true' }, icon(opts.icon || (kind === 'error' ? 'alert' : kind === 'warn' ? 'clock' : 'check'), 16)),
    h('div.msg', h('strong', title), opts.detail ? h('span', opts.detail) : null),
    opts.action ? h('button', { class: 'toast-action', type: 'button', onclick: () => { opts.action.onClick(); dismiss(); } }, opts.action.label) : null,
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Dismiss notification', onclick: () => dismiss() }, icon('x', 16)),
  );
  let done = false;
  function dismiss() {
    if (done) return; done = true;
    t.classList.add('leaving');
    setTimeout(() => t.remove(), 220);
  }
  host.append(t);
  while (host.children.length > 4) host.firstElementChild.remove();
  setTimeout(dismiss, opts.timeout ?? (kind === 'error' ? 8000 : opts.action ? 7000 : 4000));
  return dismiss;
}

// ---------------------------------------------------------------- modal
let openModals = 0;
/**
 * modal({title, description, body: Node, wide, actions: [{label, primary, danger, value, onClick}], onClose})
 * Returns {el, close(value), result: Promise}.
 */
export function modal({ title, description, body, wide = false, sheet = false, actions = [], dismissible = true, initialFocus }) {
  const prevFocus = document.activeElement;
  const titleId = 'm-title-' + Math.random().toString(36).slice(2, 8);
  const descId = description ? titleId + '-d' : undefined;
  let resolve;
  const result = new Promise(r => { resolve = r; });

  const foot = h('div.modal-foot');
  const dlg = h('div', { class: 'modal' + (wide ? ' wide' : '') + (sheet ? ' sheet' : ''), role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId, 'aria-describedby': descId },
    h('div.modal-head',
      h('div', h('h2', { id: titleId }, title), description ? h('p', { id: descId }, description) : null),
      dismissible ? h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Close dialog', onclick: () => close(undefined) }, icon('x')) : null,
    ),
    body ? h('div.modal-body', body) : null,
    actions.length ? foot : null,
  );
  for (const a of actions) {
    const b = h('button', { type: a.submit ? 'submit' : 'button', class: 'btn' + (a.primary ? ' btn-primary' : '') + (a.danger ? ' btn-danger' : '') + (a.ghost ? ' btn-ghost' : '') }, a.label);
    b.addEventListener('click', async () => {
      if (a.onClick) {
        const prev = b.textContent;
        b.disabled = true;
        try {
          const r = await a.onClick({ close, button: b });
          if (r === false) { b.disabled = false; return; }
        } catch (e) { b.disabled = false; b.textContent = prev; return; }
        b.disabled = false;
      } else close(a.value);
    });
    a.el = b;
    foot.append(b);
  }
  const backdrop = h('div', { class: 'modal-backdrop' + (sheet ? ' sheet-backdrop' : '') }, dlg);
  backdrop.addEventListener('mousedown', e => { if (e.target === backdrop && dismissible) close(undefined); });
  backdrop.addEventListener('keydown', e => {
    if (e.key === 'Escape' && dismissible) { e.stopPropagation(); close(undefined); }
    if (e.key === 'Tab') trapFocus(dlg, e);
  });
  document.body.append(backdrop);
  openModals++;
  requestAnimationFrame(() => {
    const target = (initialFocus && (initialFocus instanceof Element ? initialFocus : dlg.querySelector(initialFocus))) ||
      dlg.querySelector('.modal-body input:not([type=hidden]), .modal-body select, .modal-body textarea') ||
      dlg.querySelector('.modal-foot .btn-primary') || dlg.querySelector('button');
    target && target.focus();
  });

  function close(value) {
    if (!backdrop.isConnected) return;
    backdrop.remove();
    openModals--;
    if (prevFocus && prevFocus.focus && prevFocus.isConnected) prevFocus.focus();
    resolve(value);
  }
  return { el: dlg, close, result, actions };
}
export const isModalOpen = () => openModals > 0;

function trapFocus(container, e) {
  const f = [...container.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter(x => x.offsetParent !== null);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

export function confirmDialog({ title, description, confirmLabel = 'Confirm', danger = false }) {
  const m = modal({ title, description, actions: [
    { label: 'Cancel', value: false },
    { label: confirmLabel, primary: !danger, danger, value: true },
  ] });
  return m.result.then(v => !!v);
}

// ---------------------------------------------------------------- misc
export function spinner(label) {
  return h('span', { class: 'spinner', role: label ? 'status' : null, 'aria-label': label || null, 'aria-hidden': label ? null : 'true' });
}
export function loading(text = 'Loading…') {
  return h('div', { class: 'loading', role: 'status' }, spinner(), text);
}
export function emptyState({ iconName = 'inbox', title, text, action }) {
  return h('div.empty',
    h('div.empty-icon', icon(iconName, 28)),
    h('h3', title),
    text ? h('p', text) : null,
    action || null,
  );
}
/** Put a button into a busy state while `fn` runs. */
export async function busy(btn, fn, busyText) {
  const kids = [...btn.childNodes];
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  clear(btn).append(spinner(), busyText || kids.map(k => k.textContent).join('').trim());
  try { return await fn(); }
  finally {
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    clear(btn).append(...kids);
  }
}
export function debounce(fn, ms = 250) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
export function isTypingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}
export function initials(name) {
  return String(name || '?').split(/\s+/).filter(w => /[a-z0-9]/i.test(w)).slice(0, 2).map(w => w[0].toUpperCase()).join('') || '?';
}

// ---------------------------------------------------------------- Evergreen v2 building blocks
/** Money with a real minus sign (U+2212). sign: true → also "+" for positive. cents: false → whole dollars. */
export function money(v, { sign = false, cents = true } = {}) {
  const n = num(v);
  const body = (cents ? usd : usd0).format(Math.abs(n));
  return (n < 0 ? '−' : sign && n > 0 ? '+' : '') + body;
}
/** Short money: $103k, $8.7k, $640. */
export function moneyK(v) {
  const n = num(v), a = Math.abs(n), s = n < 0 ? '−' : '';
  if (a >= 1000) return `${s}$${(a / 1000).toFixed(a >= 10000 ? 0 : 1).replace(/\.0$/, '')}k`;
  return `${s}$${Math.round(a)}`;
}
export function avClass(name) {
  let x = 0;
  for (const ch of String(name || '')) x = (x * 31 + ch.charCodeAt(0)) | 0;
  return 'av-' + (Math.abs(x) % 7);
}
/** Initials avatar (decorative: the name is always next to it). size: '' | 'sm' | 'lg' | 'xl' */
export function avatar(name, size = '') {
  return h('span', { class: `av ${size} ${avClass(name)}`, 'aria-hidden': 'true' }, initials(name));
}
/** chip('Matched', 'pos', {icon:'check', dot:true, title}) */
export function chip(text, tone = '', { icon: ic = null, dot = false, title = null, tag = 'span', attrs = {} } = {}) {
  return h(tag, { class: `chip ${tone}`.trim(), title, ...attrs }, dot ? h('i.dot', { 'aria-hidden': 'true' }) : null, ic ? icon(ic, 12) : null, text);
}
/** Lane = dot + label (never colour alone). */
export function laneTag(lane) {
  const l = lane || 'review';
  return h('span.lane', h('i', { class: `dot lane-dot-${l}`, 'aria-hidden': 'true' }), LANE_LABEL[l] || l);
}
/** Card with the one header pattern: title · meta · single action. body: Node | Node[] */
export function card({ title, meta, action, body, cls = '', id, foot, tag = 'section', label } = {}) {
  const hid = title && id ? `${id}-h` : null;
  return h(`${tag}.card`, { class: cls, id, 'aria-labelledby': hid, 'aria-label': !hid && label ? label : null },
    title ? h('header.card-h', h('h2', { id: hid }, title), meta != null && meta !== '' ? h('span.meta', meta) : null, action ? h('div.act', action) : null) : null,
    h('div.card-b', body),
    foot ? h('footer.card-f', foot) : null);
}
/** Arrow + % vs. last year. Colours only when `good` is meaningful (net profit, tax gap); otherwise neutral. */
export function delta(cur, prev, { lowerIsBetter = false, neutral = false, title = 'vs. last year' } = {}) {
  const c = num(cur), p = num(prev);
  if (!p) return null;
  const d = (c - p) / Math.abs(p) * 100;
  const good = lowerIsBetter ? d <= 0 : d >= 0;
  const cls = neutral ? 'flat' : good ? 'up' : 'down';
  return h('span', { class: `delta ${cls}`, title, 'aria-label': `${d >= 0 ? 'up' : 'down'} ${Math.abs(d).toFixed(1)}% ${title}` },
    icon(d >= 0 ? 'arrowUp' : 'arrowDown', 12), `${Math.abs(d).toFixed(1)}%`);
}
/** 8px progress track. */
export function progressBar(pct, tone = '', { label = null, thin = false } = {}) {
  const p = Math.max(0, Math.min(100, num(pct)));
  return h('div', { class: 'bar' + (thin ? ' thin' : ''), role: 'progressbar', 'aria-valuenow': Math.round(p), 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-label': label },
    h('i', { class: tone, style: { width: `${p}%` } }));
}
/** Skeleton block. */
export function sk(w, hgt, r = 6, style = {}) {
  return h('span.sk', { 'aria-hidden': 'true', style: { width: w, height: `${hgt}px`, borderRadius: `${r}px`, ...style } });
}
/** “Why?” / “Details” disclosure: paragraphs live here, not on the page. */
export function why(label, ...kids) {
  return h('details.why', h('summary', icon('info', 14), label || 'Why?'), h('div.why-b', ...kids));
}

// Clean merchant names for display. Deterministic and local; the raw bank descriptor is
// always one click away (row title, detail panel) and stored data is never changed.
const MERCHANT_ALIAS = [
  [/^ADOBE\b/, 'Adobe Creative Cloud'], [/GOOGLE \*?G ?SUITE|GOOGLE \*?WORKSPACE/, 'Google Workspace'], [/^ZOOM\.?US|^ZOOM\b/, 'Zoom'],
  [/^AMAZON WEB SERVICES|^AWS\b/, 'Amazon Web Services'], [/^AMAZON MKTPL|^AMZN MKTP|^AMAZON\.COM/, 'Amazon'], [/^VERIZON/, 'Verizon'],
  [/^FACEBK|^FACEBOOK|^META ADS/, 'Meta Ads'], [/^NETFLIX/, 'Netflix'], [/^CVS/, 'CVS Pharmacy'], [/^PAYMENT THANK YOU|^AUTOPAY PAYMENT/, 'Card payment'],
  [/^STRIPE TRANSFER/, 'Stripe'], [/^STRIPE FEE/, 'Stripe fees'], [/^COSTCO/, 'Costco'], [/^UBER\b/, 'Uber'], [/HOME DEPOT/, 'Home Depot'],
  [/^ZELLE/, 'Zelle'], [/^WAL-?MART/, 'Walmart'], [/^SHELL\b/, 'Shell'], [/^APPLE\.COM/, 'Apple'], [/^LINKEDIN/, 'LinkedIn Premium'],
  [/^WEWORK/, 'WeWork'], [/^GEICO/, 'GEICO'], [/^CHASE CREDIT CRD/, 'Chase card payment'], [/^AMEX EPAYMENT/, 'Amex payment'],
  [/^CAPITAL ONE MOBILE PMT/, 'Capital One payment'], [/^TRANSFER (TO|FROM) (SAVINGS|CHECKING)/, 'Transfer'], [/^IRS\b|USATAXPYMT/, 'IRS'],
  [/^MONTHLY SERVICE FEE/, 'Bank fee'],
];
const KEEP_UPPER = new Set(['AWS', 'CVS', 'GEICO', 'IRS', 'USA', 'US', 'ATM', 'ACH', 'LLC', 'UPS', 'USPS', 'IKEA', 'BP', 'HBO', 'AT&T', 'TJ', 'PG&E', 'DMV', 'ABC', 'CPA']);
const SMALL = new Set(['and', 'of', 'the', 'for', 'to', 'in', 'on', 'at', 'a']);
export function displayMerchant(t) {
  const raw = String((t && (t.merchant || t.description)) || '').trim();
  if (!raw) return 'Unknown';
  const up = raw.toUpperCase();
  for (const [re, name] of MERCHANT_ALIAS) if (re.test(up)) return name;
  let x = up.replace(/^(TST|SQ|SP|DD|PAYPAL|PP|IN|POS|PY|BT|EB|CKO)\s*\*\s*/, (m, p) => (p === 'PAYPAL' ? 'PAYPAL · ' : ''))
    .replace(/<[^>]+>/g, '').replace(/\s*#\s*\d+.*$/, '').replace(/\s+\d{3,}.*$/, '').replace(/\.(COM|NET|ORG|IO|CO)\b.*$/, '')
    .replace(/\s+(INC|LLC|CORP|CO|WHSE|PMT|PYMT)\.?$/, '').replace(/\s{2,}/g, ' ').trim();
  if (!x) x = up;
  return x.split(' ').map((w, i) => {
    if (KEEP_UPPER.has(w)) return w;
    const l = w.toLowerCase();
    if (i && SMALL.has(l)) return l;
    return l.replace(/(^|[-/&'·.])([a-z])/g, (m, a, b) => a + b.toUpperCase());
  }).join(' ');
}
