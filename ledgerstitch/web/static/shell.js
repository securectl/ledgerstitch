// LedgerStitch — shell.js : the Evergreen v2 app shell.
// Grouped sidebar (≥ 1100px) → icon rail (720–1099px) → bottom tab bar (< 720px), a sticky top bar with
// the page title, an add-on slot ("topbar") and ≤ 1 screen action, plus Theme / Lock and the privacy line.
// Screens never build chrome themselves: they call setTopActions() / setTopMeta() while rendering.
// Add-on screens (status.ui.screens) join the nav group they name, or a new group after the core ones.
import { h, clear, icon, initials, avClass, confirmDialog, modal } from './ui.js';
import { store, taxYear, sourceInfo } from './state.js';
import { MOCK } from './api.js';
import { signupUrl } from './upsell.js';
import { watchLocalAi } from './local-ai.js';
import { addonScreens, renderSlot } from './addons.js';

/** The core's sidebar groups. */
export const NAV = [
  { group: null, items: [{ id: 'overview', label: 'Overview', icon: 'home' }] },
  { group: 'Transactions', items: [
    { id: 'ledger', label: 'Ledger', icon: 'list' },
    { id: 'review', label: 'Review', icon: 'inbox', count: true },
    { id: 'receipts', label: 'Receipts', icon: 'receipt' },
    { id: 'import', label: 'Import & connect', icon: 'link' },
  ] },
  { group: 'Taxes', items: [
    { id: 'report', label: 'Report', icon: 'chart' },
  ] },
];
const FOOT = { id: 'settings', label: 'Settings', icon: 'gear' };

/** NAV plus the installed add-ons' screens (by group, then their `order`). */
function navGroups() {
  const groups = NAV.map(g => ({ group: g.group, items: [...g.items] }));
  for (const s of addonScreens()) {
    const it = { id: s.id, label: s.title, icon: s.icon, locked: s.state === 'locked', order: s.order };
    let g = groups.find(x => x.group === (s.group || null) && x.group !== null);
    if (!g) { g = { group: s.group || 'More', items: [] }; groups.push(g); }
    const at = g.items.findIndex(x => x.order !== undefined && x.order > it.order);
    if (at < 0) {
      // add-on screens go before the group's core items only when the group is theirs alone
      g.items.push(it);
    } else g.items.splice(at, 0, it);
  }
  return groups;
}

let els = null;
let lockFn = () => {};
let signOutFn = null;   // server mode only
let topCleanup = null;

const visible = () => true;
const locked = it => !!it.locked;

function navLink(it) {
  const lock = locked(it);
  return h('a', { href: `#/${it.id}`, dataset: { route: it.id }, title: it.label, 'aria-label': lock ? `${it.label} (needs an upgrade)` : null },
    icon(it.icon, 20), h('span.nav-l', it.label),
    it.count ? h('b.count', { dataset: { reviewCount: '' }, hidden: true }) : null,
    it.count ? h('i.badge-dot', { dataset: { reviewDot: '' }, 'aria-hidden': 'true', hidden: true }) : null,
    lock ? h('span.nav-lock', { 'aria-hidden': 'true' }, icon('lock', 13)) : null);
}

function themeNow() { return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'; }
export function setTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  try { localStorage.setItem('ls-theme', t); } catch { /* private mode: this session only */ }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', t === 'light' ? '#F6F4EE' : '#0A120E');
  for (const b of document.querySelectorAll('[data-theme-toggle]')) {
    const ic = b.querySelector('svg');
    if (ic) ic.replaceWith(icon(t === 'light' ? 'moon' : 'sun', 16));
    b.setAttribute('aria-label', t === 'light' ? 'Switch to dark theme' : 'Switch to light theme');
  }
  document.dispatchEvent(new CustomEvent('ls:theme', { detail: t }));
}
const toggleTheme = () => setTheme(themeNow() === 'light' ? 'dark' : 'light');

function themeBtn(label = true) {
  return h('button', { type: 'button', dataset: { themeToggle: '' }, title: 'Theme', 'aria-label': themeNow() === 'light' ? 'Switch to dark theme' : 'Switch to light theme', onclick: toggleTheme },
    icon(themeNow() === 'light' ? 'moon' : 'sun', 16), label ? h('span', 'Theme') : null);
}

function wsName() { return (store.status && store.status.vault_name) || 'Workspace'; }

async function switchWorkspace() {
  if (await confirmDialog({ title: 'Switch workspace?', description: `This locks “${wsName()}”. Pick another workspace on the next screen.`, confirmLabel: 'Lock and switch' })) lockFn();
}

/** “More” sheet for the phone tab bar: every destination that isn't a tab, plus Theme and Lock. */
function openMore() {
  const items = [...navGroups().flatMap(g => g.items), FOOT].filter(it => visible(it) && !['overview', 'ledger', 'review', 'report'].includes(it.id));
  let m;
  const list = h('nav.more-list', { 'aria-label': 'More' },
    items.map(it => h('a', { href: `#/${it.id}`, onclick: () => m.close() }, icon(it.icon, 20), h('span', it.label), locked(it) ? icon('lock', 14) : null)),
    h('button', { type: 'button', onclick: () => { toggleTheme(); } }, icon('sun', 20), h('span', 'Theme')),
    h('button', { type: 'button', onclick: () => { m.close(); lockFn(); } }, icon('lock', 20), h('span', 'Lock')),
    sourceLink());
  m = modal({ title: wsName(), body: list, sheet: true });
}

function sourceLink() {
  const { url, license } = sourceInfo();
  return h('a.source-link', { 'data-source-link': '', href: url || '#', hidden: !url, target: '_blank', rel: 'noopener noreferrer' }, `Source code (${license})`);
}

// "Get the full product" in the sidebar foot (Community edition, URL known)
function communityLink() {
  const url = signupUrl();
  if (!url || !store.status || store.status.edition !== 'community') return null;
  return h('a.signup-link', { href: url, target: '_blank', rel: 'noopener noreferrer' }, icon('sparkle', 14), h('span', 'Get the full product'));
}

/** Build the chrome once. `onLock` locks the workspace. */
export function buildShell({ onLock, onSignOut }) {
  lockFn = onLock;
  signOutFn = onSignOut || null;
  const side = document.getElementById('side');
  const top = document.getElementById('topbar');
  const tabs = document.getElementById('tabbar');
  els = { side, top, tabs, title: document.getElementById('page-title'), meta: document.getElementById('top-meta'), actions: document.getElementById('top-actions'), slot: document.getElementById('top-slot') };
}

/** The current screen's context for add-ons (e.g. the ledger selection): screens register it while current. */
let screenCtx = null;
export function bindScreenContext(ctx, opts = {}) {
  const entry = { ctx, opts };
  screenCtx = entry;
  return () => { if (screenCtx === entry) screenCtx = null; };
}
export function screenContext() {
  return screenCtx && (!screenCtx.ctx || screenCtx.ctx.isCurrent()) ? screenCtx.opts : null;
}

/** (Re)draw the sidebar and tab bar for the current status (features, workspace, counts). */
function drawNav() {
  const { side, tabs } = els;
  const name = wsName();
  clear(side).append(
    h('a.brand', { href: '#/overview', 'aria-label': 'LedgerStitch, overview' }, h('span.brand-mark', icon('list', 18)), h('span.brand-name', 'LedgerStitch')),
    h('button.ws', { type: 'button', id: 'workspace-pill', title: `Workspace: ${name} · Tax year ${taxYear()}`, onclick: switchWorkspace, 'aria-label': `Workspace ${name}. Switch workspace` },
      h('span', { class: `av sm ${avClass(name)}`, 'aria-hidden': 'true' }, initials(name)),
      h('span.ws-t', h('b', { id: 'workspace-name' }, name), h('small', `Tax year ${taxYear()}`, MOCK ? h('span.demo-tag', { title: 'Demo mode: synthetic data, nothing leaves this browser tab' }, 'Demo') : null)),
      icon('chevUD', 16)),
    h('nav.nav', { 'aria-label': 'Main' }, navGroups().map(g => {
      const items = g.items.filter(visible);
      if (!items.length) return null;
      return [g.group ? h('div.nav-group', { role: 'presentation' }, g.group) : null, items.map(navLink)];
    })),
    h('div.side-foot',
      h('nav.nav', { 'aria-label': 'Settings' }, navLink(FOOT)),
      h('div.privacy', { title: 'Runs on your computer · Encrypted with your passphrase · AI sees merchant names only' }, icon('lock', 14), h('span', 'On-device · encrypted')),
      sourceLink(),
      communityLink(),   // oss-edition
      h('div.side-actions',
        themeBtn(),
        h('button', { type: 'button', id: 'lock-btn', title: 'Lock workspace', 'aria-label': 'Lock workspace', onclick: () => lockFn() }, icon('lock', 16), h('span', 'Lock')),
        store.status && store.status.server_mode && signOutFn
          ? h('button', { type: 'button', id: 'signout-btn', title: 'Sign out of this server', 'aria-label': 'Sign out', onclick: () => signOutFn() }, icon('logout', 16), h('span', 'Sign out'))
          : null)));

  const tabItems = [{ id: 'overview', label: 'Home', icon: 'home' }, { id: 'ledger', label: 'Ledger', icon: 'list' }, { id: 'review', label: 'Review', icon: 'inbox', count: true }, { id: 'report', label: 'Report', icon: 'chart' }];
  clear(tabs).append(
    tabItems.map(it => h('a', { href: `#/${it.id}`, dataset: { route: it.id } }, icon(it.icon, 22), h('span', it.label),
      it.count ? h('b.tb-count', { dataset: { reviewCount: '' }, hidden: true }) : null)),
    h('button', { type: 'button', onclick: openMore, 'aria-label': 'More: import, receipts, settings, theme and lock' }, icon('more', 22), h('span', 'More')));
}

/** Show/hide the chrome and mark the current route. Call on every navigation. */
export function syncShell(target, title) {
  const bare = target === 'welcome' || target === 'setup';
  document.body.classList.toggle('is-welcome', bare);
  document.getElementById('app').classList.toggle('bare', bare);
  els.side.hidden = bare; els.top.hidden = bare; els.tabs.hidden = bare;
  if (bare) return;
  drawNav();
  for (const a of document.querySelectorAll('#side [data-route], #tabbar [data-route]')) {
    if (a.dataset.route === target) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  els.title.textContent = title;
  drawTopSlot();
  clear(els.meta); clear(els.actions);
  const n = store.status && store.status.review_count;
  for (const b of document.querySelectorAll('[data-review-count]')) { b.textContent = n > 999 ? '999+' : String(n || 0); b.hidden = !n; }
  for (const d of document.querySelectorAll('[data-review-dot]')) d.hidden = !n;
  watchLocalAi();   // "Local AI: downloading 47%" pill in the top bar
}

/** The "topbar" add-on slot: drawn once per unlock (its modules keep their own state). */
let topDrawnFor = null;
function drawTopSlot() {
  const key = store.status && store.status.unlocked ? (store.status.vault_path || 'open') : null;
  if (!els.slot || key === topDrawnFor) return;
  topDrawnFor = key;
  if (topCleanup) { try { topCleanup(); } catch { /* ignore */ } topCleanup = null; }
  clear(els.slot);
  if (key) topCleanup = renderSlot('topbar', els.slot);
}

/** Screen-level top-bar action(s): ≤ 1 primary per screen. */
export function setTopActions(...nodes) { if (els) clear(els.actions).append(...nodes); }
export function setTopMeta(...nodes) { if (els) clear(els.meta).append(...nodes); }
