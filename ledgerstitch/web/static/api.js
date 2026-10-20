// LedgerStitch — api.js : thin fetch wrapper for the local API (docs/ARCHITECTURE.md §4.8).
// Every request carries X-LedgerStitch-Token (read from <meta name="ll-token">).
// With ?mock=1 in the URL, requests are answered in-browser by mock.js instead.

const params = new URLSearchParams(location.search);
export const MOCK = params.get('mock') === '1';

const TOKEN = (document.querySelector('meta[name="ll-token"]') || {}).content || '';

let mockFetch = null;
async function transport(url, init) {
  if (MOCK) {
    if (!mockFetch) mockFetch = (await import('./mock.js')).mockFetch;
    return mockFetch(url, init);
  }
  return fetch(url, init);
}

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data || {};
  }
}

const FRIENDLY = {
  0: "Can't reach LedgerStitch on this computer. Is the app still running?",
  400: 'Something about that request didn’t look right.',
  401: 'Session expired — reload LedgerStitch to reconnect.',
  403: 'That request was blocked for your safety. Reload LedgerStitch from the link it opened.',
  409: 'Your workspace is locked. Unlock it to continue.',
  404: 'We couldn’t find that.',
  413: 'That file is too large to import.',
  415: 'That file type isn’t supported. Try a CSV or PDF statement.',
  422: 'Some of the details need another look.',
  500: 'Something went wrong on our side (still on your computer). Please try again.',
};

/** Hook points set by app.js */
export const hooks = { onLocked: () => {}, onUpgrade: () => {}, onSessionExpired: () => {} };
const PASSPHRASE_PATHS = ['/api/unlock', '/api/passphrase', '/api/recovery'];   // /api/unlock/recovery too

/**
 * api('/api/x', {method, json, form, query, raw})
 *  - json: object → JSON body;  form: FormData → multipart
 *  - raw: return the Response (for downloads)
 */
export async function api(path, opts = {}) {
  const { method = 'GET', json, form, query, raw = false, quiet = false } = opts;
  let url = path;
  if (query) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
    const qs = q.toString();
    if (qs) url += (url.includes('?') ? '&' : '?') + qs;
  }
  const headers = { 'X-LedgerStitch-Token': TOKEN, Accept: 'application/json' };
  let body;
  if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  else if (form) body = form;

  let res;
  try {
    res = await transport(url, { method, headers, body, credentials: 'same-origin', cache: 'no-store' });
  } catch (e) {
    throw new ApiError(FRIENDLY[0], 0);
  }

  if (raw && res.ok) return res;

  let data = null;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) { try { data = await res.json(); } catch { data = null; } }
  else if (!res.ok) { try { const t = await res.text(); data = t ? { error: t.slice(0, 300) } : null; } catch { /* ignore */ } }

  if (!res.ok) {
    const msg = errorMessage(data, res.status);
    const err = new ApiError(msg, res.status, data);
    // 402 → upgrade dialog. 409 {locked:true} → vault locked (idle auto-lock etc.).
    // 401 → wrong passphrase on /unlock & /passphrase (callers show it inline), otherwise a bad/expired session token.
    if (res.status === 402 && !quiet) hooks.onUpgrade(err);
    if (res.status === 409 && data && data.locked && !quiet) hooks.onLocked(err);
    if (res.status === 401 && !PASSPHRASE_PATHS.some(p => path.startsWith(p))) hooks.onSessionExpired(err);
    throw err;
  }
  return data;
}

function errorMessage(data, status) {
  if (data) {
    if (typeof data.error === 'string' && data.error) return data.error;
    if (typeof data.detail === 'string' && data.detail) return data.detail;         // FastAPI default
    if (Array.isArray(data.detail) && data.detail[0] && data.detail[0].msg) return 'Please check: ' + data.detail.map(d => d.msg).join('; ');
    if (data.error && data.error.message) return data.error.message;
  }
  return FRIENDLY[status] || FRIENDLY[Math.floor(status / 100) * 100] || 'Something went wrong. Please try again.';
}

/** Normalise list endpoints that may return [] or {items, total}. */
export function listOf(data) {
  if (Array.isArray(data)) return { items: data, total: data.length, exact: false };
  if (data && Array.isArray(data.items)) return { items: data.items, total: data.total ?? data.items.length, exact: data.total != null };
  if (data && Array.isArray(data.transactions)) return { items: data.transactions, total: data.total ?? data.transactions.length, exact: data.total != null };
  return { items: [], total: 0, exact: false };
}

/** Download via fetch → blob → <a download>, because the token header can't ride on a plain link. */
export async function download(path, query, fallbackName) {
  const res = await api(path, { query, raw: true });
  const blob = await res.blob();
  let name = fallbackName;
  const cd = res.headers.get('content-disposition') || '';
  const m = cd.match(/filename\*=UTF-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
  if (m) { try { name = decodeURIComponent(m[1]); } catch { name = m[1]; } }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.rel = 'noopener';
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  return name;
}
