// LedgerStitch — Settings: Workspace · Privacy & AI · Security (recovery key, backup) · Your data · Plan · Rules,
// plus the installed add-ons' sections (status.ui.settings). The layout is a sticky section nav + card grid.
import { api, download } from './api.js';
import { h, clear, append, icon, toast, busy, confirmDialog, modal, plural, LANE_LABEL, SOURCE_LABEL, fmtDate, chip, avatar, laneTag, progressBar, why, sk, displayMerchant } from './ui.js';
import { store, loadSettings, loadStatus, loadAccounts, accountShort, planName, featureState, sourceInfo, refreshReviewBadge, taxYear } from './state.js';
import { aiSection } from './ai.js';
import { lockNow, showUpgrade, goWelcome, go } from './app.js';
import { strength, showRecoveryCode } from './screen-welcome.js';
import { signupLink } from './upsell.js';
import { localAiStatusLine, localAiManage } from './local-ai.js';
import { addonSettings, renderAddonSettings } from './addons.js';

const SECTIONS = [['workspace', 'Workspace', 'briefcase'], ['ai', 'Privacy & AI', 'eye'], ['security', 'Security', 'key'], ['data', 'Your data', 'layers'], ['license', 'Plan', 'tag'], ['rules', 'Rules', 'filter'], ['setup', 'Setup', 'refresh']];

export async function renderSettings(main, params, rctx = {}) {
  const [settings] = await Promise.all([loadSettings(true), loadStatus(), loadAccounts()]);
  const ai = aiSection({ st: settings, card, saveSettings });
  const addonHost = h('div.st-addons');
  const sections = [...SECTIONS];
  const extra = addonSettings();
  if (extra.length) sections.splice(2, 0, ...extra.map(e => [`${e.ns}-${e.id}`, e.title, 'layers']));
  const navEl = h('nav.st-nav', { 'aria-label': 'Settings sections' },
    sections.map(([id, label, ic]) => h('a', { href: `#/settings?section=${id}`, dataset: { sec: id }, onclick: e => { e.preventDefault(); jump(id); } }, icon(ic, 18), h('span', label))));
  const grid = h('div.st-grid', workspaceCard(settings), ...ai.cards, localAiCard(), addonHost, securityCard(settings), backupCard(), dataCard(), licenseCard(), rulesCard(), setupCard());
  clear(main).append(h('div.st-wrap', navEl, grid));
  const addonCleanup = renderAddonSettings(addonHost, { isCurrent: rctx.isCurrent || (() => true) });
  const sec = params.get('section');
  if (sec) requestAnimationFrame(() => jump(sec === 'business' ? 'workspace' : sec === 'backup' || sec === 'recovery' ? 'security' : sec));
  // highlight the section in view
  const io = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    for (const en of entries) if (en.isIntersecting) { const id = en.target.id.replace(/^set-/, ''); for (const a of navEl.querySelectorAll('a')) a.setAttribute('aria-current', a.dataset.sec === id || (id === 'ai-local' && a.dataset.sec === 'ai') ? 'true' : 'false'); }
  }, { rootMargin: '-40% 0px -55% 0px' }) : null;
  if (io) for (const el of grid.querySelectorAll('section[id^="set-"]')) io.observe(el);
  return () => { ai.cleanup(); addonCleanup(); if (io) io.disconnect(); };

  function jump(id) {
    const el = document.getElementById(`set-${id}`);
    if (!el) return;
    const det = el.matches('details') ? el : el.closest('details');
    if (det && !det.open) det.open = true;
    el.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    const f = el.matches('details') ? el.querySelector('summary') : el.querySelector('input, select, textarea, button');
    f && f.focus({ preventScroll: true });
  }
}

async function saveSettings(patch, btn, msg = 'Saved') {
  const run = async () => {
    try { store.settings = await api('/api/settings', { method: 'PUT', json: patch }); toast(msg); return true; }
    catch (e) { toast('Couldn’t save settings', { kind: 'error', detail: e.message }); return false; }
  };
  return btn ? busy(btn, run, 'Saving…') : run();
}

/** Card with the v2 anatomy. Also handed to ai.js, so the signature (id, title, sub, body, foot) stays. */
function card(id, title, sub, body, foot, { cls = '', action = null } = {}) {
  return h('section', { class: `card ${cls}`, id: `set-${id}`, 'aria-labelledby': `set-${id}-h` },
    h('header.card-h', h('h2', { id: `set-${id}-h` }, title), sub ? h('span.meta', { title: typeof sub === 'string' ? sub : null }, sub) : null, action ? h('div.act', action) : null),
    h('div.card-b', body),
    foot ? h('footer.card-f', foot) : null);
}

// ---------------------------------------------------------------- workspace
function workspaceCard(st) {
  const name = h('input', { class: 'input', id: 'biz-name', value: st.business_name || '', maxlength: 200, autocomplete: 'organization' });
  const year = h('input', { class: 'input', id: 'biz-year', type: 'number', min: 1990, max: 2100, step: 1, value: st.tax_year || '', placeholder: String(taxYear()), inputmode: 'numeric' });
  const prof = h('textarea', { class: 'input', id: 'biz-profile', maxlength: 500, rows: 2, placeholder: 'e.g. Freelance photographer; I drive to shoots and work from a home studio.' }, st.business_profile || '');
  const save = h('button', { class: 'btn btn-primary btn-sm', type: 'button' }, 'Save');
  save.addEventListener('click', () => saveSettings({ business_name: name.value.trim(), tax_year: year.value ? Number(year.value) : null, business_profile: prof.value.trim() }, save)
    .then(ok => { if (ok) { loadStatus().catch(() => {}); const ws = document.getElementById('workspace-name'); if (ws && name.value.trim()) ws.textContent = name.value.trim(); } }));
  const auto = h('select', { class: 'pill', id: 'auto-lock', 'aria-label': 'Lock automatically after' },
    ...[5, 15, 30, 60, 120, 240].map(m => new Option(m < 60 ? `${m} min` : `${m / 60} h`, String(m))));
  auto.value = String(st.auto_lock_minutes || 30);
  if (auto.value === '') { auto.append(new Option(`${st.auto_lock_minutes} min`, String(st.auto_lock_minutes))); auto.value = String(st.auto_lock_minutes); }
  auto.addEventListener('change', () => saveSettings({ auto_lock_minutes: Number(auto.value) }, null, 'Auto-lock updated'));
  const wsName = (store.status && store.status.vault_name) || 'Workspace';
  return card('workspace', 'Workspace', wsName,
    h('div.st-ws',
      h('div.st-status', avatar(wsName, 'lg'), h('div.grow', h('b.st-h', wsName), h('div.muted', (store.status && store.status.vault_path) || 'Encrypted on this computer')),
        h('label.st-inline', { for: 'auto-lock' }, icon('clock', 14), 'Auto-lock', auto)),
      h('div.form-grid',
        h('div.field', h('label', { for: 'biz-name' }, 'Business name'), name),
        h('div.field', h('label', { for: 'biz-year' }, 'Tax year'), year),
        h('div.field.full', h('label', { for: 'biz-profile' }, 'Business profile'), prof, h('span.hint', 'One sentence helps the AI pick lanes. No names or account numbers.')))),
    [h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => lockNow() }, icon('lock', 14), 'Lock now'), h('span.spacer'), save]);
}

// ---------------------------------------------------------------- security: passphrase + recovery key
function securityCard(st) {
  void st;
  const cur = h('input', { class: 'input', id: 'pp-old', type: 'password', autocomplete: 'current-password' });
  const nw = h('input', { class: 'input', id: 'pp-new', type: 'password', autocomplete: 'new-password' });
  const nw2 = h('input', { class: 'input', id: 'pp-new2', type: 'password', autocomplete: 'new-password' });
  const meter = h('div.strength', { 'data-score': '0', 'aria-live': 'polite' }, h('div.strength-bar', { 'aria-hidden': 'true' }, h('i'), h('i'), h('i'), h('i')), h('p.strength-label'));
  nw.addEventListener('input', () => { const r = strength(nw.value); meter.dataset.score = String(r.score); meter.querySelector('.strength-label').textContent = nw.value ? `${r.label} — ${r.tip}` : ''; });
  const errEl = h('div', { role: 'alert' });
  const change = h('button', { class: 'btn btn-primary btn-sm', type: 'button' }, 'Change passphrase');
  change.addEventListener('click', async () => {
    clear(errEl);
    const problems = [];
    if (!cur.value) problems.push('Enter your current passphrase.');
    if (nw.value.length < 10) problems.push('New passphrase needs at least 10 characters.');
    else if (strength(nw.value).score < 2) problems.push('That new passphrase is easy to guess. Add another word or two.');
    if (nw.value !== nw2.value) problems.push('The new passphrases don’t match.');
    if (problems.length) { errEl.append(h('div.error-box', icon('alert', 16), h('div', problems.map(p => h('div', p))))); return; }
    await busy(change, async () => {
      try {
        await api('/api/passphrase', { method: 'POST', json: { old: cur.value, new: nw.value } });
        cur.value = nw.value = nw2.value = ''; meter.dataset.score = '0'; meter.querySelector('.strength-label').textContent = '';
        toast('Passphrase changed', { detail: 'Use the new one next time you unlock. Update your password manager.' });
      } catch (e) { errEl.append(h('div.error-box', icon('alert', 16), e.status === 401 ? 'Your current passphrase didn’t match.' : e.message)); }
    }, 'Re-encrypting…');
  });
  const pass = h('details.more.st-pass', h('summary', icon('key', 14), 'Change passphrase'),
    h('div.more-b', h('div.form-grid',
      h('div.field.full', h('label', { for: 'pp-old' }, 'Current passphrase'), cur),
      h('div.field', h('label', { for: 'pp-new' }, 'New passphrase'), nw, meter),
      h('div.field', h('label', { for: 'pp-new2' }, 'Confirm new passphrase'), nw2)), errEl, h('div.row', change)));
  return card('security', 'Recovery key', null, [recoveryBlock(), pass]);
}

/** Recovery key: optional second way in. Create / regenerate / remove, each behind the current passphrase. */
function recoveryBlock() {
  const host = h('div.st-rec', { id: 'set-recovery' });
  function draw() {
    const rec = (store.status && store.status.recovery) || {};
    if (rec.supported === false) { clear(host).append(h('p.muted.small', 'Recovery keys aren’t supported for this workspace.')); return; }
    const on = !!rec.enabled;
    const make = h('button', { class: on ? 'btn btn-sm' : 'btn btn-primary btn-sm', type: 'button' }, icon(on ? 'refresh' : 'key', 14), on ? 'Regenerate' : 'Create recovery key');
    make.addEventListener('click', async () => {
      const r = await askPassphrase({
        title: on ? 'Replace your recovery key?' : 'Create a recovery key',
        description: on ? 'Your current recovery key will stop working immediately.' : 'Enter your current passphrase to create one.',
        confirmLabel: on ? 'Replace key' : 'Create key',
        run: pp => api('/api/recovery', { method: 'POST', json: { passphrase: pp } }) });
      if (!r) return;
      store.status = { ...(store.status || {}), recovery: { supported: r.supported, enabled: r.enabled, created: r.created } };
      draw();
      await showRecoveryCode(r.recovery_code, { workspace: store.status.vault_name || '', replaced: on });
      toast(on ? 'Recovery key replaced' : 'Recovery key created', { detail: on ? 'The old key no longer works.' : 'Keep it offline.' });
    });
    const remove = on ? h('button', { class: 'btn btn-ghost btn-sm btn-danger', type: 'button' }, 'Remove') : null;
    remove && remove.addEventListener('click', async () => {
      const r = await askPassphrase({ title: 'Remove the recovery key?', danger: true,
        description: 'Afterwards only your passphrase opens this workspace. If you forget it, the data is gone.',
        confirmLabel: 'Remove recovery key', run: pp => api('/api/recovery/remove', { method: 'POST', json: { passphrase: pp } }) });
      if (!r) return;
      store.status = { ...(store.status || {}), recovery: r };
      draw();
      toast('Recovery key removed');
    });
    clear(host).append(
      h('div.st-status', h('span', { class: 'st-big' + (on ? ' ok' : '') }, icon('key', 26)),
        h('div.grow', h('b.st-h', on ? 'Created' : 'Off'), h('div.muted.num', on ? (rec.created ? fmtDate(rec.created) : 'Active') : 'Optional second way in')),
        on ? chip('Active', 'pos', { icon: 'check' }) : chip('Off', 'outline')),
      h('div.st-key.mono', { 'aria-label': on ? 'Recovery key, hidden' : 'No recovery key yet' }, on ? '•••• •••• •••• •••• •••• •••• •••• ••••' : '— — — —'),
      h('div.st-actions', make, remove),
      why('Why?', h('p', on ? 'If you forget your passphrase, the recovery key opens this workspace and lets you set a new one. Anyone with the key can open it, so keep it offline.'
        : 'A one-time code you keep offline that opens this workspace if you forget your passphrase. Without it, a forgotten passphrase means the data is gone.')));
  }
  draw();
  return host;
}

// ---------------------------------------------------------------- backup
function backupCard() {
  const body = h('div.st-bk');
  const btn = h('button', { class: 'btn btn-primary btn-sm', type: 'button' }, icon('download', 14), 'Back up now');
  btn.addEventListener('click', () => busy(btn, async () => {
    try {
      const name = await download('/api/backup', null, 'ledgerstitch.lsbackup');
      await loadStatus();
      draw();
      toast('Backup saved', { detail: `${name} · encrypted. Copy it to another drive. Restore from the unlock screen.` });
    } catch (e) { toast('Couldn’t make a backup', { kind: 'error', detail: e.message }); }
  }, 'Backing up…'));
  function draw() {
    const b = (store.status && store.status.backup) || {};
    const days = b.days_since;
    const limit = b.reminder_days || 30;
    const never = !b.last_backup_at;
    const due = !!b.due;
    clear(body).append(
      h('div.st-status', h('span', { class: 'st-big ' + (due ? (never ? 'danger' : 'warn') : 'ok') }, icon('shield', 26)),
        h('div.grow', h('b.st-h.num', never ? 'Never' : days === 0 ? 'Today' : `${days} days ago`), h('div.muted', 'Last encrypted backup')),
        due ? chip(never ? 'None yet' : 'Overdue', 'warn', { icon: 'alert' }) : chip('Fresh', 'pos', { icon: 'check' })),
      h('div.st-meter', { title: never ? 'No backup yet' : `${days} of ${limit} days` }, progressBar(never ? 100 : Math.min(100, days / limit * 100), due ? 'warn' : 'pos', { label: 'Days since the last backup' }),
        h('div.st-meter-l.muted.num', h('span', '0'), h('span', `${limit} days`))),
      h('div.st-actions', chip(`Reminder · ${limit} days`, 'outline', { icon: 'clock' }), h('span.grow'), btn));
  }
  draw();
  return card('backup', 'Backup', 'Encrypted', body,
    why('Why?', h('p', 'One .lsbackup file with everything in this workspace. It stays encrypted: opening it needs this workspace’s passphrase or recovery key. Keep a copy on another drive.')));
}

/** Ask for the current passphrase, then run(passphrase). Resolves to run()'s result, or null on cancel. */
function askPassphrase({ title, description, confirmLabel, danger = false, run }) {
  const pp = h('input', { class: 'input', id: 'ask-pp', type: 'password', autocomplete: 'current-password' });
  const errEl = h('div', { role: 'alert' });
  const m = modal({ title, description,
    body: h('div', h('div.field', h('label', { for: 'ask-pp' }, 'Current passphrase'), pp), errEl),
    actions: [
      { label: 'Cancel', value: null },
      { label: confirmLabel, primary: !danger, danger, onClick: async ({ close }) => {
        clear(errEl);
        if (!pp.value) { errEl.append(h('div.error-box', icon('alert', 16), 'Enter your passphrase.')); pp.focus(); return false; }
        try { const r = await run(pp.value); pp.value = ''; close(r); }
        catch (e) { errEl.append(h('div.error-box', icon('alert', 16), e.status === 401 ? 'That passphrase didn’t match.' : e.message)); pp.select(); return false; }
      } },
    ] });
  pp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); m.actions[1].el.click(); } });
  return m.result.then(r => r || null);
}

// ---------------------------------------------------------------- your data
/** Typed confirmation: the action button stays disabled until the user types DELETE
 *  (and, for deleting the workspace, enters the passphrase). Resolves to the passphrase / true, or null. */
function typedConfirm({ title, description, items, confirmLabel, passphrase = false, run }) {
  const word = h('input', { class: 'input', id: 'confirm-word', autocomplete: 'off', spellcheck: 'false', placeholder: 'DELETE' });
  const pp = passphrase ? h('input', { class: 'input', id: 'confirm-pp', type: 'password', autocomplete: 'current-password' }) : null;
  const errEl = h('div', { role: 'alert' });
  const m = modal({ title, description,
    body: h('div',
      items ? h('ul.small.ink-2', { style: { margin: '0 0 14px 18px' } }, items.map(t => h('li', t))) : null,
      pp ? h('div.field', h('label', { for: 'confirm-pp' }, 'Your passphrase'), pp) : null,
      h('div.field', { style: { marginTop: pp ? '12px' : '0' } }, h('label', { for: 'confirm-word' }, 'Type ', h('b', 'DELETE'), ' to confirm'), word),
      errEl),
    actions: [
      { label: 'Cancel', value: null },
      { label: confirmLabel, danger: true, onClick: async ({ close }) => {
        clear(errEl);
        if (word.value.trim() !== 'DELETE') { errEl.append(h('div.error-box', icon('alert', 16), 'Type DELETE in capital letters to confirm.')); word.focus(); return false; }
        if (pp && !pp.value) { errEl.append(h('div.error-box', icon('alert', 16), 'Enter your passphrase.')); pp.focus(); return false; }
        try { close(await run({ confirm: 'DELETE', passphrase: pp ? pp.value : undefined })); }
        catch (e) { errEl.append(h('div.error-box', icon('alert', 16), e.status === 401 ? 'That passphrase didn’t match.' : e.message)); return false; }
      } },
    ] });
  const btn = m.actions[1].el;
  const sync = () => { btn.disabled = word.value.trim() !== 'DELETE' || (pp && !pp.value); };
  word.addEventListener('input', sync); pp && pp.addEventListener('input', sync); sync();
  return m.result;
}


// ---------------------------------------------------------------- your data
function dataCard() {
  const st = store.status || {};
  const counts = st.counts || {};
  const after = async (r, msg) => {
    await Promise.allSettled([loadStatus(), loadAccounts(true), loadSettings(true)]);
    refreshReviewBadge();
    const d = r.deleted || {};
    toast(msg, { detail: `${plural(d.transactions || 0, 'transaction')}, ${plural(d.imports || 0, 'file')}, ${plural(d.accounts || 0, 'account')} deleted` });
    if (r.addon_errors && r.addon_errors.length) toast('Some add-on data couldn’t be removed', { kind: 'warn', detail: r.addon_errors.join(', ') });
  };
  const row = (ic, title, sub, action, tone = '') => h('div', { class: 'st-data-row ' + tone }, h('span', { class: 'st-big ' + tone }, icon(ic, 22)), h('div.grow', h('b', title), h('div.muted', sub)), action);
  const exportBtn = h('button', { class: 'btn btn-sm', type: 'button' }, icon('download', 14), 'Export');
  exportBtn.addEventListener('click', () => busy(exportBtn, async () => {
    try { const name = await download('/api/data/export', null, 'ledgerstitch-data.zip'); toast('Data downloaded', { detail: `${name} · not encrypted, delete it when done` }); }
    catch (e) { toast('Couldn’t export your data', { kind: 'error', detail: e.message }); }
  }, '…'));
  const purgeTx = h('button', { class: 'btn btn-sm btn-danger', type: 'button', onclick: async () => {
    const r = await typedConfirm({ title: 'Delete all statement data?', description: 'Start over with the same settings.',
      items: [`Deleted: ${plural(counts.transactions || 0, 'transaction')}, every imported file, ${plural(counts.accounts || 0, 'account')}, receipts and models trained on your data.`, 'Kept: rules, categories, settings, license and passphrase.'],
      confirmLabel: 'Delete statement data', run: b => api('/api/data/purge', { method: 'POST', json: { scope: 'transactions', confirm: b.confirm } }) });
    if (r) after(r, 'Statement data deleted');
  } }, icon('trash', 14), 'Delete');
  const purgeAll = h('button', { class: 'btn btn-sm btn-danger', type: 'button', onclick: async () => {
    const r = await typedConfirm({ title: 'Reset this workspace?', description: 'Everything goes back to how it was when you created the workspace.',
      items: ['Deleted: all transactions, imported files, accounts, receipts, rules, custom categories, learned file formats, AI history, tax profile, add-on data and trained models.', 'Kept: the workspace name, your passphrase and your license key.'],
      confirmLabel: 'Reset workspace', run: b => api('/api/data/purge', { method: 'POST', json: { scope: 'all', confirm: b.confirm } }) });
    if (r) after(r, 'Workspace reset');
  } }, icon('refresh', 14), 'Reset');
  const delWs = h('button', { class: 'btn btn-sm btn-danger', type: 'button', onclick: async () => {
    const r = await typedConfirm({ title: `Delete “${st.vault_name || 'this workspace'}” permanently?`, description: 'This can’t be undone. There is no copy we can restore from.',
      items: ['The encrypted workspace folder is removed from this computer, along with models trained on it.', 'Download your data first if you might need it.', 'Copies you made yourself (Time Machine, exports) aren’t touched. Without this folder’s key file they can’t be decrypted.'],
      confirmLabel: 'Delete workspace forever', passphrase: true, run: b => api('/api/vaults/delete', { method: 'POST', json: { passphrase: b.passphrase, confirm: b.confirm } }) });
    if (r) { toast('Workspace deleted', { detail: 'Removed from this computer.' }); goWelcome(); }
  } }, icon('trash', 14), 'Delete');
  return card('data', 'Your data', `${plural(counts.transactions || 0, 'transaction')} · ${plural(counts.accounts || 0, 'account')}`,
    h('div.st-data',
      row('download', 'Export everything', 'JSON + CSV + receipts, one zip · not encrypted', exportBtn),
      row('trash', 'Delete statement data', 'Transactions, files, accounts, receipts · rules stay', purgeTx, 'warn'),
      row('refresh', 'Reset workspace', 'All data and settings · keeps passphrase and license', purgeAll, 'warn'),
      row('trash', 'Delete workspace', 'Permanent · can’t undo · needs your passphrase', delWs, 'danger')),
    h('span.legal', icon('lock', 12), 'Deletions are permanent'));
}

// ---------------------------------------------------------------- local AI (model, size, remove, withdraw consent)
function localAiCard() {
  if (featureState('ai.local') !== 'on') return null;
  return card('ai-local', 'Local AI', 'Merchant names only', localAiManage());
}

// ---------------------------------------------------------------- first-run setup again
function setupCard() {
  const btn = h('button', { class: 'btn btn-sm', type: 'button' }, icon('refresh', 14), 'Run setup again');
  btn.addEventListener('click', () => busy(btn, async () => {
    try {
      await api('/api/setup/reset', { method: 'POST' });
      go('setup');
    } catch (e) { toast('Couldn’t start setup', { kind: 'error', detail: e.message }); }
  }, 'Opening…'));
  return card('setup', 'Setup', null,
    h('div', h('div.row-between', h('div', h('strong', 'First-run setup'), h('p.small.muted', 'System check, local AI, sample data.')), btn),
      localAiStatusLine()));   // "Local AI: downloading 47%" / ready / stopped (+ retry)
}

// ---------------------------------------------------------------- plan / license
// chip text = the feature's short name: its label up to the first ":" or "("
const shortLabel = l => String(l || '').split(/[:(]/)[0].trim();
function licenseCard() {
  const host = h('div');
  const keyIn = h('textarea', { class: 'input mono', id: 'lic-key', rows: 2, placeholder: 'Paste your license key (starts with LS1.)', spellcheck: 'false', autocomplete: 'off' });
  const activate = h('button', { class: 'btn btn-primary btn-sm', type: 'button' }, icon('key', 14), 'Activate');
  activate.addEventListener('click', () => {
    if (!keyIn.value.trim()) { toast('Paste your license key first', { kind: 'warn' }); keyIn.focus(); return; }
    busy(activate, async () => {
      try {
        store.status = await api('/api/license', { method: 'POST', json: { key: keyIn.value.trim() } });
        keyIn.value = '';
        toast('License activated', { detail: `You’re on ${planName(store.status.plan)}.` });
        draw();
      } catch (e) { toast('License not accepted', { kind: 'error', detail: e.message }); }
    }, 'Checking…');
  });
  function draw() {
    const st = store.status || {};
    const plan = st.plan || 'free';
    const lic = st.license || {};
    const feats = Object.entries(st.features || {}).filter(([, f]) => f && f.tier && f.tier !== 'community');
    const installed = feats.filter(([, f]) => f.available);
    const community = !!st.features && !installed.length;
    const firstLocked = installed.find(([, f]) => !f.unlocked);
    const src = sourceInfo();
    const free = st.edition === 'community' && plan === 'free';
    if (community) {
      clear(host).append(
        h('div.st-status', h('span.st-big.ok', icon('heart', 24)), h('div.grow', h('b.st-h', 'Community'), h('div.muted', `Free and open source · ${src.license}`)), src.url ? h('a', { class: 'btn btn-sm', href: src.url, target: '_blank', rel: 'noopener noreferrer' }, 'Source') : null),
        why('Why?', h('p', 'Imports, review, rules, reports, exports and local AI work fully here, with no limits. Paid add-ons extend LedgerStitch; see the website.')),
        h('div.st-actions', signupLink('Get the full product', 'btn btn-sm btn-primary')));
      return;
    }
    clear(host).append(
      h('div.st-status', h('span.st-big.ok', icon(free ? 'heart' : 'tag', 24)),
        h('div.grow', h('b.st-h', free ? 'Community' : planName(plan)), h('div.muted', free ? `Free and open source · ${src.license}` : [lic.email, lic.expires ? `renews ${fmtDate(lic.expires)}` : null].filter(Boolean).join(' · ') || 'No license key yet')),
        st.dev_override ? chip('Dev override', 'warn') : free ? null : (firstLocked ? h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: () => showUpgrade({ data: { feature: firstLocked[0] } }) }, 'Upgrade') : null)),
      installed.length ? h('div.st-feats', { 'aria-label': 'Features' }, installed.map(([, f]) => chip(shortLabel(f.label), f.unlocked ? 'pos' : 'outline', { icon: f.unlocked ? 'check' : 'lock', title: `${f.label} · ${f.unlocked ? 'Included' : `Needs ${planName(f.tier)}`}` }))) : null,
      free ? h('div.st-actions', signupLink('Get the full product', 'btn btn-sm btn-primary')) : null,
      h('div.st-lic', h('label', { for: 'lic-key' }, plan === 'free' ? 'Have a license key?' : 'Replace license key'), keyIn,
        h('div.st-actions', h('span.legal', icon('laptop', 12), 'Checked on this computer'), h('span.grow'),
          plan !== 'free' && !st.dev_override ? h('button', { class: 'btn btn-ghost btn-sm btn-danger', type: 'button', onclick: async e => {
            if (!await confirmDialog({ title: 'Remove license from this workspace?', description: 'Paid features will be locked again here. Your data isn’t affected.', confirmLabel: 'Remove license', danger: true })) return;
            busy(e.currentTarget, async () => {
              try { store.status = await api('/api/license', { method: 'POST', json: { key: '' } }); toast('License removed'); draw(); }
              catch (err) { toast('Couldn’t remove license', { kind: 'error', detail: err.message }); }
            });
          } }, 'Remove') : null, activate)));
  }
  draw();
  return card('license', 'Plan', null, host);
}

// ---------------------------------------------------------------- rules
function rulesCard() {
  const host = h('div.rows', h('div.trow', sk('28px', 28, 14), sk('40%', 12), sk('80px', 20, 10)));
  const meta = h('span');
  const el = card('rules', 'Rules', meta, host, null, { cls: 'flush' });
  load();
  async function load() {
    try {
      const d = await api('/api/rules');
      const rules = Array.isArray(d) ? d : (d && d.items) || [];
      clear(meta).append(plural(rules.length, 'rule'));
      clear(host);
      if (!rules.length) { host.append(h('div.tx-empty', icon('filter', 28), h('span.muted', 'Rules appear as you review'))); return; }
      append(host, rules.map(r => h('div.trow', avatar(displayMerchant({ merchant: r.pattern }), 'sm'),
        h('div.grow', h('span.t1', h('span.mono', r.pattern), r.field === 'description' ? h('span.muted', ' · description') : null),
          h('span.t2', `${r.category || ''} · ${r.account_id ? accountShort(r.account_id) : 'All accounts'} · ${SOURCE_LABEL[r.created_by] === 'You' ? 'Added by you' : 'Learned'}`)),
        laneTag(r.lane || 'review'),
        h('button', { class: 'btn btn-ghost btn-sm icon', type: 'button', 'aria-label': `Delete rule ${r.pattern}`, title: 'Delete rule', onclick: async e => {
          const b = e.currentTarget;
          if (!await confirmDialog({ title: `Delete rule “${r.pattern}”?`, description: 'Transactions already sorted keep their category. Future imports won’t use this rule.', confirmLabel: 'Delete rule', danger: true })) return;
          await busy(b, async () => {
            try { await api(`/api/rules/${encodeURIComponent(r.id)}`, { method: 'DELETE' }); toast('Rule deleted'); load(); }
            catch (err) { toast('Couldn’t delete rule', { kind: 'error', detail: err.message }); }
          });
        } }, icon('trash', 15)))));
    } catch (e) { clear(host).append(h('div.error-box', { style: { margin: '0 24px 16px' } }, icon('alert', 16), e.message)); }
  }
  return el;
}
void LANE_LABEL; void modal;
