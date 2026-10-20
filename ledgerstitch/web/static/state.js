// LedgerStitch — state.js : small shared cache used by every screen.
import { api, listOf } from './api.js';

export const store = {
  status: null,        // GET /api/status
  categories: [],      // GET /api/categories
  accounts: [],        // GET /api/accounts
  settings: null,      // GET /api/settings
};

export async function loadStatus() {
  store.status = await api('/api/status');
  if (store.status && store.status.restart_required) showRestartBanner();
  syncDevBanner();
  syncBackupBanner();
  syncSourceLinks();
  return store.status;
}

// Gentle reminder while unlocked: data exists and there's no encrypted backup yet, or the last
// one is older than status.backup.reminder_days. "Not now" hides it for this browser session.
function syncBackupBanner() {
  const st = store.status || {};
  const b = st.unlocked && st.backup;
  let el = document.getElementById('backup-banner');
  let dismissed = false;
  try { dismissed = sessionStorage.getItem('ls-backup-banner') === 'off'; } catch { /* storage unavailable */ }
  if (!b || !b.due || dismissed) { if (el) el.remove(); return; }
  if (el) return;
  el = document.createElement('div');
  el.id = 'backup-banner';
  el.className = 'mock-banner backup-banner';
  el.setAttribute('role', 'status');
  const a = document.createElement('a');
  a.href = '#/settings?section=data';
  a.textContent = 'Back up now';
  const x = document.createElement('button');
  x.type = 'button'; x.className = 'btn btn-ghost btn-sm'; x.textContent = 'Not now';
  x.addEventListener('click', () => { try { sessionStorage.setItem('ls-backup-banner', 'off'); } catch { /* ignore */ } el.remove(); });
  el.append(b.last_backup_at ? `Your last encrypted backup is more than ${b.reminder_days || 30} days old. ` : 'This workspace has no backup yet. ', a, ' ', x);
  document.body.prepend(el);
}

/**
 * Feature gate from status.features[name] = {available, unlocked, tier, label}:
 *  'on'     — usable;
 *  'locked' — installed but needs a license (show the neutral locked state);
 *  'hidden' — not installed in this edition: don't show the UI at all.
 * An older backend without `features` is treated as 'on'.
 */
export function featureState(name) {
  const f = store.status && store.status.features ? store.status.features[name] : undefined;
  if (!store.status || !store.status.features) return 'on';
  if (!f) return 'hidden';
  if (!f.available) return 'hidden';
  return f.unlocked ? 'on' : 'locked';
}
export function featureInfo(name) {
  return (store.status && store.status.features && store.status.features[name]) || { tier: 'pro', label: name };
}

// Display names of license plans: the core's, plus those the installed add-ons declare (status.plans).
const PLAN_NAMES = { free: 'Free', community: 'Community', pro: 'Pro', enterprise: 'Enterprise' };
export function planName(p) {
  if (!p) return 'Free';
  const names = { ...PLAN_NAMES, ...((store.status && store.status.plans) || {}) };
  return names[p] || String(p).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// Persistent amber banner while LEDGERSTITCH_DEV_PLAN unlocks a plan for local testing.
function syncDevBanner() {
  const st = store.status || {};
  let b = document.getElementById('dev-banner');
  if (!st.dev_override) { if (b) b.remove(); return; }
  const plan = planName((st.license && st.license.plan) || st.plan || 'enterprise');
  if (!b) {
    b = document.createElement('div');
    b.id = 'dev-banner';
    b.className = 'mock-banner dev-banner';
    b.setAttribute('role', 'status');
    document.body.prepend(b);
  }
  b.textContent = `Developer mode: all ${plan} features unlocked for local testing (LEDGERSTITCH_DEV_PLAN)`;
}

// AGPL-3.0 §13: every page links to the source of the version that is running.
export function sourceInfo() {
  const src = (store.status && store.status.source) || {};
  return { url: src.url || null, license: src.license || 'AGPL-3.0' };
}
function syncSourceLinks() {
  const { url, license } = sourceInfo();
  for (const a of document.querySelectorAll('[data-source-link]')) {
    if (url) { a.href = url; a.hidden = false; a.textContent = `Source code (${license})`; } else a.hidden = true;
  }
}

// The app's code changed on disk while it was running (an update or a dev edit):
// the screens and the server may no longer agree until it is restarted.
function showRestartBanner() {
  if (document.getElementById('restart-banner')) return;
  const b = document.createElement('div');
  b.id = 'restart-banner';
  b.className = 'mock-banner';
  b.setAttribute('role', 'alert');
  b.textContent = 'LedgerStitch was updated while it was running. Quit it and open it again to finish the update — your workspace is saved.';
  document.body.prepend(b);
}
export async function loadCategories(force = false) {
  if (store.categories.length && !force) return store.categories;
  const d = await api('/api/categories');
  store.categories = (Array.isArray(d) ? d : listOf(d).items).map(c => typeof c === 'string' ? { name: c, lane: 'review', schedule_c: null } : c);
  return store.categories;
}
export async function loadAccounts(force = false) {
  if (store.accounts.length && !force) return store.accounts;
  const d = await api('/api/accounts');
  store.accounts = Array.isArray(d) ? d : listOf(d).items;
  return store.accounts;
}
export async function loadSettings(force = false) {
  if (store.settings && !force) return store.settings;
  store.settings = await api('/api/settings');
  return store.settings;
}
export function resetStore() {
  store.status = null; store.categories = []; store.accounts = []; store.settings = null;
  syncBackupBanner();
}
export const accountLabel = id => {
  const a = store.accounts.find(x => x.id === id);
  return a ? a.label : (id || '—');
};
export const categoryByName = name => store.categories.find(c => c.name === name);

/** Tax year to use for filters/reports: settings.tax_year → status → current year - 1. */
export function taxYear() {
  const y = (store.settings && store.settings.tax_year) || (store.status && store.status.tax_year);
  return y ? Number(y) : new Date().getFullYear() - 1;
}

/** <select> of categories grouped by lane (optgroups). */
const GROUPS = [
  ['business', 'Business'], ['review', 'Business or personal (you decide)'], ['personal', 'Personal'],
  ['transfer', 'Transfers (excluded)'], ['income', 'Income'],
];
export function categoryOptions(select, selected, { includeAll = false, allLabel = 'All categories' } = {}) {
  while (select.firstChild) select.removeChild(select.firstChild);
  if (includeAll) select.append(new Option(allLabel, ''));
  const seen = new Set();
  for (const [lane, label] of GROUPS) {
    const cats = store.categories.filter(c => (c.lane || 'review') === lane);
    if (!cats.length) continue;
    const g = document.createElement('optgroup');
    g.label = label;
    for (const c of cats) { g.append(new Option(c.name, c.name, false, c.name === selected)); seen.add(c.name); }
    select.append(g);
  }
  const rest = store.categories.filter(c => !seen.has(c.name));
  for (const c of rest) select.append(new Option(c.name, c.name, false, c.name === selected));
  if (selected && !store.categories.some(c => c.name === selected)) select.append(new Option(selected, selected, true, true));
  if (selected !== undefined) select.value = selected || '';
  return select;
}

/** Refresh the review count badges (sidebar, rail dot, tab bar). */
export async function refreshReviewBadge(count) {
  let n = count;
  if (n === undefined) {
    try {
      const d = await api('/api/transactions', { query: { status: 'review', limit: 1, offset: 0 }, quiet: true });
      const l = listOf(d);
      n = l.exact ? l.total : (store.status && store.status.review_count);
    } catch { n = undefined; }
  }
  if (n !== undefined && n !== null && store.status) store.status.review_count = n;
  for (const badge of document.querySelectorAll('[data-review-count]')) {
    if (n === undefined || n === null) { badge.hidden = true; continue; }
    badge.textContent = n > 999 ? '999+' : String(n);
    badge.hidden = n === 0;
    badge.setAttribute('aria-label', `${n} to review`);
  }
  for (const dot of document.querySelectorAll('[data-review-dot]')) dot.hidden = !n;
}

const INST_SHORT = { 'American Express': 'Amex', 'Capital One': 'Cap One', 'Bank of America': 'BofA', 'Wells Fargo': 'Wells', 'U.S. Bank': 'US Bank' };
/** Short account label for row second lines: “Chase ••4012”. */
export function accountShort(id) {
  const a = store.accounts.find(x => x.id === id);
  if (!a) return id || '';
  const inst = INST_SHORT[a.institution] || String(a.institution || '').split(' ')[0];
  const last4 = a.last4 || (String(a.label || '').match(/(\d{4})\s*$/) || [])[1];
  return inst && last4 ? `${inst} ••${last4}` : (a.label || id);
}
