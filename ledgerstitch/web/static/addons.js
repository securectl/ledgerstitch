// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — addons.js : the browser side of the plugin API (docs/PLUGINS.md).
//
// /api/status → ui: {screens, cards, settings}, the registrations of the INSTALLED add-ons, each with
// `module` (/addons/<ns>/<file>.js) and `state` ('on' | 'locked'). This module:
//   · renders an add-on screen:   (await import(module)).render(main, params, ctx)
//   · renders the cards of a slot: (await import(module)).renderCard(el, ctx)
//   · renders settings sections:  (await import(module)).renderSettings(el, ctx)
//   · shows the neutral locked card (the registered title + "Get the full product") while locked.
// ctx = { api, store, go(route, params), isCurrent(), addStyle(href), slot(name, el, data), ns, id, data,
//         setTopActions(...nodes), setTopMeta(...nodes), screenContext() }
// Add-on modules may import core helpers by absolute path ('/ui.js', '/api.js', '/charts.js', '/state.js').
import { api } from './api.js';
import { store } from './state.js';
import { h, clear, icon, card } from './ui.js';
import { signupLink, openUpgrade } from './upsell.js';

let nav = { go: () => {}, setTopActions: () => {}, setTopMeta: () => {}, screenContext: () => null };
/** Wired once by app.js / shell.js (avoids an import cycle). */
export function setNavigator(fns) { nav = { ...nav, ...fns }; }

const ui = () => (store.status && store.status.ui) || { screens: [], cards: [], settings: [] };
export const addonScreens = () => ui().screens || [];
export const addonCards = slot => (ui().cards || []).filter(c => c.slot === slot);
export const addonSettings = () => ui().settings || [];
export const addonScreen = id => addonScreens().find(s => s.id === id) || null;

const styles = new Set();
/** Load an add-on stylesheet once (same-origin only: CSP style-src 'self'). */
export function addStyle(href) {
  let url = String(href || '');
  try { const u = new URL(url, location.href); if (u.origin === location.origin) url = u.pathname; } catch { return; }
  if (!url.startsWith('/addons/') || styles.has(url)) return;
  styles.add(url);
  document.head.append(h('link', { rel: 'stylesheet', href: url }));
}

/** The ctx handed to add-on modules. */
export function makeCtx(entry, { isCurrent = () => true, data = null } = {}) {
  const ctx = {
    api, store, ns: entry && entry.ns, id: entry && entry.id, data,
    go: (route, params) => nav.go(route, params),
    isCurrent,
    addStyle: href => addStyle(href && entry && !/^(\/|[a-z][a-z0-9+.-]*:)/i.test(String(href)) ? `/addons/${entry.ns}/${href}` : href),
    slot: (name, el, slotData) => renderSlot(name, el, { isCurrent, data: slotData }),
    setTopActions: (...nodes) => nav.setTopActions(...nodes),
    setTopMeta: (...nodes) => nav.setTopMeta(...nodes),
    screenContext: () => nav.screenContext(),
  };
  return ctx;
}

const modules = new Map();
function load(url) {
  if (!modules.has(url)) modules.set(url, import(url));
  return modules.get(url);
}

/** Neutral locked state: the registered title, a lock and "Get the full product". Nothing about the feature. */
export function lockedAddonCard(entry, { compact = false } = {}) {
  const title = entry.title || 'Add-on';
  const key = h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => openUpgrade(entry.feature) }, icon('key', 14), 'Have a key?');
  if (compact) {
    return h('button', { class: 'btn btn-sm addon-locked-btn', type: 'button', title: `${title}: get the full product`,
      'aria-label': `${title} (locked)`, onclick: () => openUpgrade(entry.feature) }, icon('lock', 14), h('span', title));
  }
  return card({ title, cls: 'ls-locked addon-locked', id: entry.id ? `addon-${entry.ns}-${entry.id}` : undefined,
    action: h('span.chip.outline', icon('lock', 12), 'Locked'),
    body: h('div.ls-locked-b',
      h('span.ov-lock-ic', { 'aria-hidden': 'true' }, icon('lock', 22)),
      h('div.ls-locked-a', signupLink() || h('button', { class: 'btn btn-primary', type: 'button', onclick: () => openUpgrade(entry.feature) }, icon('sparkle', 16), 'Get the full product'), key)) });
}

const COMPACT = new Set(['topbar', 'ledger-toolbar', 'export']);

/** Render every card registered for `slot` into `host`. Returns a cleanup function. */
export function renderSlot(slot, host, { isCurrent = () => true, data = null } = {}) {
  const cleanups = [];
  const entries = addonCards(slot);
  if (!host) return () => {};
  for (const entry of entries) {
    const el = h('div', { class: `addon-slot addon-slot-${slot}`, dataset: { addon: entry.ns, card: entry.id } });
    host.append(el);
    if (entry.state === 'locked') {
      if (slot !== 'app') el.append(lockedAddonCard({ ...entry }, { compact: COMPACT.has(slot) }));
      continue;
    }
    load(entry.module).then(m => {
      if (!isCurrent() || typeof m.renderCard !== 'function') return;
      return Promise.resolve(m.renderCard(el, makeCtx(entry, { isCurrent, data }))).then(c => { if (typeof c === 'function') cleanups.push(c); });
    }).catch(() => { el.remove(); });
  }
  return () => { for (const c of cleanups.splice(0)) { try { c(); } catch { /* ignore */ } } };
}

/** Render an add-on screen. Returns the module's cleanup (if any). */
export async function renderAddonScreen(entry, main, params, { isCurrent }) {
  if (entry.state === 'locked') {
    clear(main).append(h('div.addon-locked-screen', lockedAddonCard(entry)));
    return null;
  }
  const m = await load(entry.module);
  if (!isCurrent()) return null;
  if (typeof m.render !== 'function') throw new Error(`${entry.title} could not be loaded.`);
  return m.render(main, params, makeCtx(entry, { isCurrent }));
}

/** Render the add-ons' Settings sections into `host` (one card each). Returns a cleanup function. */
export function renderAddonSettings(host, { isCurrent = () => true } = {}) {
  const cleanups = [];
  for (const entry of addonSettings()) {
    const el = h('div', { class: 'addon-settings', id: `set-${entry.ns}-${entry.id}`, dataset: { addon: entry.ns } });
    host.append(el);
    if (entry.state === 'locked') { el.append(lockedAddonCard(entry)); continue; }
    load(entry.module).then(m => {
      if (!isCurrent() || typeof m.renderSettings !== 'function') return;
      return Promise.resolve(m.renderSettings(el, makeCtx(entry, { isCurrent }))).then(c => { if (typeof c === 'function') cleanups.push(c); });
    }).catch(() => { el.remove(); });
  }
  return () => { for (const c of cleanups.splice(0)) { try { c(); } catch { /* ignore */ } } };
}

/** "app" slot: modules loaded once after unlock (global listeners such as keyboard shortcuts). */
let appLoaded = false;
export function startAppSlot() {
  if (appLoaded || !store.status || !store.status.unlocked) return;
  appLoaded = true;
  const host = document.getElementById('addon-app') || document.body.appendChild(h('div', { id: 'addon-app', hidden: true }));
  renderSlot('app', host);
}
