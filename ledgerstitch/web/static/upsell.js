// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — upsell.js : the locked state of an installed add-on feature without a license.
// status.features[name] = {available: true, unlocked: false, tier} → featureState() === 'locked'.
// One v2 card (muted, lock, 1–3 word title, one line, "Get the full product"), so every locked
// feature looks the same. Nothing here phones home: the sign-up link only carries ?ref=app-community.
import { hooks } from './api.js';
import { h, icon, card, chip } from './ui.js';
import { store, featureInfo, planName } from './state.js';

/** The hosted product's sign-up page (status.signup_url, filled by the build) or null: no dead links. */
export function signupUrl() {
  const u = store.status && store.status.signup_url;
  return typeof u === 'string' && /^https:\/\//.test(u) ? u : null;
}

/** "Get the full product" link, or null when the URL isn't known. */
export function signupLink(label = 'Get the full product', cls = 'btn btn-primary') {
  const url = signupUrl();
  if (!url) return null;
  return h('a', { class: cls, href: url, target: '_blank', rel: 'noopener noreferrer' }, label, icon('arrowRight', 16));
}

/** Opens the upgrade dialog (app.js) for a locked feature. */
export function openUpgrade(feature) {
  hooks.onUpgrade({ data: { feature, upgrade: true, installed: true } });
}

const tierName = feature => planName(featureInfo(feature).tier || 'pro');

/** Small lock chip (the plan name) for a locked control. */
export function lockChip(feature) {
  return chip(tierName(feature), 'outline', { icon: 'lock', title: `Part of LedgerStitch ${tierName(feature)}` });
}

/**
 * The locked card. opts: {feature, title (1–3 words), text (one line), iconName?, cls?, id?}
 * Returns a v2 `card`: the tier chip in the header, a lock badge, one line, the sign-up button and a
 * quiet "Have a key?" that opens the license dialog.
 */
export function lockedCard({ feature, title, text, iconName = 'lock', cls = '', id }) {
  const key = h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => openUpgrade(feature) }, icon('key', 16), 'Have a key?');
  return card({ title, id, cls: `ls-locked ov-locked ${cls}`.trim(), action: lockChip(feature),
    body: h('div.ls-locked-b',
      h('span.ov-lock-ic', { 'aria-hidden': 'true' }, icon(iconName, 22)),
      h('p.ls-locked-t', text),
      h('div.ls-locked-a', signupLink() || h('button', { class: 'btn btn-primary', type: 'button', onclick: () => openUpgrade(feature) }, icon('sparkle', 16), 'Unlock'), key)) });
}
