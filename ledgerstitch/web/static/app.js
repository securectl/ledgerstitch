// LedgerStitch — app.js : boot, hash router, app shell, global dialogs.
import { api, hooks, MOCK } from './api.js';
import { h, clear, icon, toast, modal, sk } from './ui.js';
import { store, loadStatus, loadSettings, loadAccounts, loadCategories, resetStore, refreshReviewBadge, featureInfo } from './state.js';
import { renderWelcome } from './screen-welcome.js';
import { renderSetup } from './screen-setup.js';
import { renderImport } from './screen-import.js';
import { renderReview } from './screen-review.js';
import { renderLedger } from './screen-ledger.js';
import { renderReceipts } from './screen-receipts.js';
import { renderReport } from './screen-report.js';
import { renderSettings } from './screen-settings.js';
import { buildShell, syncShell, setTopActions, setTopMeta, screenContext } from './shell.js';
import { renderOverview, overviewSkeleton } from './screen-overview.js';
import { signupUrl } from './upsell.js';   // "Get the full product" in the upgrade dialog
import { addonScreen, renderAddonScreen, setNavigator, startAppSlot } from './addons.js';

// Theme: set before CSS loads by theme-init.js (saved choice → ?theme= → OS); toggled from the sidebar.

// Where "Buy" goes. Null until the store is live: the backend may provide it as
// status.store_url; a dead link is worse than no link.
export const PRICING_URL = null;
const storeUrl = () => (store.status && store.status.store_url) || PRICING_URL;

const ROUTES = {
  welcome: { render: renderWelcome, title: 'Welcome', public: true },
  setup: { render: renderSetup, title: 'Setup', public: true, bare: true },
  overview: { render: renderOverview, title: 'Overview', skeleton: overviewSkeleton },
  import: { render: renderImport, title: 'Import & connect' },
  review: { render: renderReview, title: 'Review' },
  ledger: { render: renderLedger, title: 'Ledger' },
  receipts: { render: renderReceipts, title: 'Receipts' },
  report: { render: renderReport, title: 'Report' },
  settings: { render: renderSettings, title: 'Settings' },
};
// Old hashes keep working.
const ALIASES = { home: 'overview' };

/** A core route, or an add-on screen registered with ui_screen (status.ui.screens). */
function routeFor(name) {
  if (ROUTES[name]) return ROUTES[name];
  const entry = addonScreen(name);
  if (!entry) return null;
  return { title: entry.title, addon: entry,
    render: (main, params, ctx) => renderAddonScreen(entry, main, params, ctx) };
}
const HOME = 'overview';

const main = document.getElementById('main');
let cleanup = null;
let renderToken = 0;

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [name, query] = raw.split('?');
  return { name: name || '', params: new URLSearchParams(query || '') };
}

export function go(name, params) {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  const target = `#/${name}${qs}`;
  if (location.hash === target) route(); else location.hash = target;
}

async function route() {
  const { name: raw, params } = parseHash();
  const name = ALIASES[raw] || raw;
  const unlocked = !!(store.status && store.status.unlocked);
  let target = routeFor(name) ? name : (unlocked ? HOME : 'welcome');
  if (!unlocked && !(routeFor(target) || {}).public) target = 'welcome';
  if (unlocked && target === 'welcome') target = HOME;
  // First launch (no workspace yet, setup not done): the setup wizard comes before vault creation.
  if (!unlocked && target === 'welcome' && store.status && store.status.onboarding && store.status.onboarding.needed) target = 'setup';
  if (target !== raw) {
    const qs = params.toString();
    history.replaceState(null, '', `${location.pathname}${location.search}#/${target}${qs && target === name ? '?' + qs : ''}`);
  }

  const my = ++renderToken;
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  const r = routeFor(target);
  document.title = `${r.title} · LedgerStitch`;
  syncShell(target, r.title);
  clear(main);
  main.className = target === 'welcome' || target === 'setup' ? 'is-welcome' : `content screen-${target}${r.addon ? ' screen-addon' : ''}`;
  main.append(r.skeleton ? r.skeleton() : pageSkeleton());
  try {
    const c = await r.render(main, params, { isCurrent: () => my === renderToken });
    if (my === renderToken) cleanup = typeof c === 'function' ? c : null;
    else if (typeof c === 'function') c();
  } catch (e) {
    if (my !== renderToken) return;
    if (e && e.data && e.data.locked) return; // onLocked handles it
    clear(main).append(h('div.card.card-pad.load-error', { role: 'alert' },
      h('div.empty',
        h('div.empty-icon', icon('alert', 28)),
        h('h3', 'This page didn’t load'),
        h('p', (e && e.message) || 'Something went wrong.'),
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => route() }, icon('refresh', 16), 'Try again'))));
  }
  if (my === renderToken && target !== 'welcome' && target !== 'setup') {
    // Move focus to the page heading for screen readers on route change.
    const h1 = document.getElementById('page-title');
    if (h1 && !main.contains(document.activeElement)) h1.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
}

/** Generic loading skeleton (screens that know their layout draw their own). */
function pageSkeleton() {
  const kpi = () => h('div.sx-kpi', sk('56%', 12), sk('80%', 24, 8, { marginTop: '12px' }), sk('40%', 10, 6, { marginTop: '12px' }));
  return h('div.page-sk', { role: 'status', 'aria-busy': 'true', 'aria-label': 'Loading' },
    h('div.sx-kpis', kpi(), kpi(), kpi()),
    h('div.card.card-pad', h('div.sx-chart', [62, 54, 70, 48, 60, 52, 78, 58, 72, 50].map(v => h('span.sk', { style: { height: `${v}%` } }))),
      [0, 1, 2].map(() => h('div.sx-row', sk('36px', 36, 18), h('div', { style: { flex: 1 } }, sk('42%', 12), sk('24%', 10, 6, { marginTop: '8px' })), sk('72px', 20, 10), sk('64px', 14)))));
}

function buildChrome() {
  buildShell({ onLock: lockNow, onSignOut: signOut });
  setNavigator({ go, setTopActions, setTopMeta, screenContext });
  // Demo mode is shown as a “Demo” tag on the workspace switcher (shell.js), not a banner.
  void MOCK;
}

/** Server mode only: end the sign-in session (the server also locks the vault). */
export async function signOut() {
  try { await api('/auth/logout', { method: 'POST', quiet: true }); } catch { /* go to the sign-in page regardless */ }
  resetStore();
  location.href = '/login';
}

export async function lockNow() {
  try { await api('/api/lock', { method: 'POST', quiet: true }); } catch { /* lock locally regardless */ }
  resetStore();
  store.status = { unlocked: false };
  toast('Workspace locked', { detail: 'Your data is encrypted on disk. Unlock with your passphrase to continue.' });
  go('welcome');
}

/** The open workspace no longer exists (deleted): back to the picker without a lock call. */
export function goWelcome() {
  resetStore();
  store.status = { unlocked: false };
  go('welcome');
}

/** Called after create/unlock succeeds. */
export async function afterUnlock(dest) {
  await loadStatus();
  await Promise.allSettled([loadSettings(true), loadAccounts(true), loadCategories(true)]);
  refreshReviewBadge(store.status && store.status.review_count);
  startAppSlot();
  go(dest || HOME);
}

// ---------------------------------------------------------------- 402 upgrade dialog
let upgradeOpen = false;
export function showUpgrade(err) {
  if (upgradeOpen) return;
  upgradeOpen = true;
  const data = (err && err.data) || {};
  const feature = data.feature;
  const signup = signupUrl();
  // The name comes from the installed add-on's own registration (status.features); the core
  // describes nothing about it.
  const what = (feature && featureInfo(feature).label) || 'This feature';
  const keyIn = h('textarea', { class: 'input mono', rows: 3, placeholder: 'Paste your license key (starts with LS1.)',
    spellcheck: 'false', autocomplete: 'off', 'aria-label': 'License key', style: { minHeight: '72px', width: '100%' } });
  const msg = h('p.small', { role: 'status', 'aria-live': 'polite' });
  const url = storeUrl();
  const body = h('div',
    h('div.upgrade-art', icon('sparkle', 28)),
    h('p', `${what} needs a license that includes it. Your data stays where it is.`),
    h('label.small', { style: { display: 'block', margin: '14px 0 6px', fontWeight: 600 } }, 'Have a license key?'),
    keyIn, msg,
    url || signup ? null : h('p.small.muted', { style: { marginTop: '10px' } },
      'Online purchases open at launch. During the beta, ask the LedgerStitch team for a key.'),
  );
  const activate = async ({ close }) => {
    const key = keyIn.value.trim();
    if (!key) { msg.textContent = 'Paste your license key first.'; keyIn.focus(); return; }
    msg.textContent = 'Checking…';
    try {
      store.status = await api('/api/license', { method: 'POST', json: { key } });
      const st = store.status || {};
      const plan = (st.license && st.license.plan) || st.plan;
      if (!plan || plan === 'free' || (st.license && st.license.valid === false)) {
        msg.textContent = 'That key wasn’t accepted. Check that you pasted all of it.';
        return;
      }
      close();
      toast('License activated', { detail: 'Reloading so the new features appear…' });
      setTimeout(() => location.reload(), 900);
    } catch (e) {
      msg.textContent = `Not accepted: ${e.message}`;
    }
  };
  const actions = [{ label: 'Not now', value: false }];
  if (url || signup) actions.push({ label: 'Get the full product', ghost: true,
    onClick: () => window.open(url || signup, '_blank', 'noopener,noreferrer') });
  actions.push({ label: 'Activate', primary: true, onClick: activate });
  const m = modal({ title: 'Get the full product', body, actions,
    initialFocus: keyIn });
  m.result.then(() => { upgradeOpen = false; });
}

hooks.onUpgrade = showUpgrade;
hooks.onSessionExpired = () => {
  if (document.getElementById('session-banner')) return;
  const b = h('div', { id: 'session-banner', class: 'mock-banner', role: 'alert', style: { background: 'var(--danger-soft)', color: 'var(--danger)' } },
    'Session expired — reload LedgerStitch to reconnect. Your data is safe on this computer. ',
    h('button', { class: 'btn btn-sm', type: 'button', style: { marginLeft: '8px' }, onclick: () => location.reload() }, 'Reload'));
  document.body.prepend(b);
};
hooks.onLocked = () => {
  if (!store.status || !store.status.unlocked) return;
  resetStore();
  store.status = { unlocked: false };
  toast('Your workspace was locked', { kind: 'warn', detail: 'Unlock it with your passphrase to continue.' });
  go('welcome');
};

// ---------------------------------------------------------------- boot
async function boot() {
  buildChrome();
  try {
    await loadStatus();
  } catch (e) {
    store.status = { unlocked: false };
    if (e.status === 0 || e.status >= 500 || e.status === 403 || e.status === 401) {
      clear(main).append(h('div.card.card-pad', { style: { maxWidth: '560px', margin: '12vh auto' }, role: 'alert' },
        h('div.empty',
          h('div.empty-icon', icon('alert', 28)),
          h('h3', 'LedgerStitch isn’t responding'),
          h('p', e.message),
          h('button', { class: 'btn btn-primary', type: 'button', onclick: () => location.reload() }, 'Reload'))));
      return;
    }
  }
  if (store.status && store.status.unlocked) {
    await Promise.allSettled([loadSettings(), loadAccounts(), loadCategories()]);
    refreshReviewBadge(store.status.review_count);
    startAppSlot();
  }
  window.addEventListener('hashchange', route);
  route();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
