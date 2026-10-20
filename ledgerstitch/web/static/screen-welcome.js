// LedgerStitch — Welcome / Unlock / Create workspace / Recover / Restore from backup
import { api } from './api.js';
import { h, s, clear, icon, logo, initials, busy, toast, modal } from './ui.js';
import { afterUnlock } from './app.js';
import { sourceInfo } from './state.js';

const MIN_LEN = 10;

export async function renderWelcome(main) {
  let vaults = [];
  let loadError = null;
  try { const d = await api('/api/vaults'); vaults = Array.isArray(d) ? d : (d && d.vaults) || []; }
  catch (e) { loadError = e.message; }

  const panel = h('div.welcome-card');
  const page = h('div.welcome', hero(), h('div.welcome-panel', panel));
  clear(main).append(page);

  const state = { mode: vaults.length ? 'unlock' : 'create', open: null };
  draw();

  function draw() {
    clear(panel);
    if (state.mode === 'unlock') drawUnlock(); else drawCreate();
    panel.append(privacyBadges());
  }

  // -------------------------------------------------------------- unlock
  function drawUnlock() {
    panel.append(
      h('h2', 'Welcome back'),
      h('p.sub', 'Choose a workspace and enter its passphrase.'),
    );
    if (loadError) panel.append(h('div.error-box', { role: 'alert', style: { marginBottom: '14px' } }, icon('alert', 16), loadError));
    const list = h('ul.vault-list', { 'aria-label': 'Workspaces on this computer' });
    vaults.forEach((v, i) => list.append(vaultItem(v, i)));
    panel.append(list,
      h('div.or-divider', 'or'),
      h('button', { class: 'btn btn-block btn-lg', type: 'button', onclick: () => { state.mode = 'create'; draw(); panel.querySelector('input')?.focus(); } },
        icon('plus', 16), 'Create a new workspace'),
      restoreButton(),
    );
    if (state.open === null && vaults.length === 1) state.open = 0;
    if (state.open !== null && vaults[state.open]) { const i = state.open; requestAnimationFrame(() => openVault(i)); }
  }

  // -------------------------------------------------------------- restore from backup
  /** Imports a .lsbackup into a NEW workspace folder (never over an existing one), then opens its unlock form. */
  function restoreButton() {
    const file = h('input', { type: 'file', accept: '.lsbackup', hidden: true, 'aria-hidden': 'true', tabindex: '-1' });
    const btn = h('button', { class: 'btn btn-ghost btn-block', type: 'button', style: { marginTop: '10px' }, onclick: () => file.click() },
      icon('upload', 16), 'Restore from a backup');
    file.addEventListener('change', () => {
      const f = file.files && file.files[0];
      file.value = '';
      if (!f) return;
      busy(btn, async () => {
        try {
          const form = new FormData();
          form.append('file', f, f.name);
          const d = await api('/api/vaults/restore', { method: 'POST', form });
          vaults = Array.isArray(d.vaults) ? d.vaults : vaults;
          const at = d.restored ? vaults.findIndex(v => v.path === d.restored.path) : -1;
          state.mode = 'unlock'; state.open = at >= 0 ? at : null;
          draw();
          toast('Backup restored as a new workspace', { detail: 'Unlock it with the passphrase it had when you backed it up, or its recovery key.' });
        } catch (err) { toast('Couldn’t restore that backup', { kind: 'error', detail: err.message }); }
      }, 'Restoring…');
    });
    return h('div', btn, file,
      h('p.hint.small.muted', { style: { textAlign: 'center', marginTop: '6px' } }, 'A .lsbackup file from Settings → Your data. It stays encrypted and is added as a separate workspace.'));
  }

  function vaultItem(v, i) {
    const li = h('li.vault-li');
    const title = vaultTitle(v);
    const hidden = !v.name && !v.label;
    const created = fmtCreated(v.created);
    const sub = hidden ? (created ? `Name hidden until unlocked · created ${created}` : 'Name hidden until unlocked')
      : [created ? `Created ${created}` : null, v.label_hidden === false && v.label && v.name && v.label !== v.name ? 'Visible label' : null].filter(Boolean).join(' · ') || (v.path || '');
    const btn = h('button', { class: 'vault-item', type: 'button', 'aria-expanded': 'false', 'aria-controls': `unlock-${i}`, onclick: () => openVault(i) },
      h('span', { class: 'vault-avatar' + (hidden ? ' hidden-name' : ''), 'aria-hidden': 'true' }, hidden ? icon('lock', 18) : initials(title)),
      h('span.meta', h('strong', title), h('span', { title: v.path || '' }, sub)),
      h('span', { style: { color: 'var(--ink-3)' } }, icon('lock', 16)),
    );
    li.append(btn);
    return li;
  }

  function openVault(i) {
    const items = [...panel.querySelectorAll('.vault-li')];
    items.forEach((li, j) => {
      const b = li.querySelector('.vault-item');
      const existing = li.querySelector('.unlock-form');
      if (j !== i) { existing && existing.remove(); li.classList.remove('open'); b.setAttribute('aria-expanded', 'false'); return; }
      if (existing) return;
      li.classList.add('open');
      b.setAttribute('aria-expanded', 'true');
      li.append(unlockForm(vaults[i], i));
      li.querySelector('input').focus();
    });
    state.open = i;
  }

  function unlockForm(v, i) {
    const pwId = `pw-${i}`;
    const input = h('input', { class: 'input', type: 'password', id: pwId, autocomplete: 'current-password', required: true, 'aria-describedby': `pw-err-${i}` });
    const errEl = h('p', { class: 'error-text', id: `pw-err-${i}`, role: 'alert' });
    const submit = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, icon('unlock', 16), 'Unlock');
    const show = h('button', { class: 'btn btn-ghost btn-sm', type: 'button', 'aria-pressed': 'false', onclick: () => {
      const vis = input.type === 'password'; input.type = vis ? 'text' : 'password';
      show.textContent = vis ? 'Hide' : 'Show'; show.setAttribute('aria-pressed', String(vis)); input.focus();
    } }, 'Show');
    const form = h('form', { class: 'unlock-form', id: `unlock-${i}`, novalidate: true },
      h('div.field', h('label', { for: pwId }, 'Passphrase'), h('div.input-with-btn', input, show), errEl),
      h('div', { style: { marginTop: '14px' } }, submit),
      v.recovery ? h('p', { style: { textAlign: 'center', marginTop: '10px' } },
        h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => {
          input.value = '';
          const rf = recoveryForm(v, i);
          form.replaceWith(rf);
          rf.querySelector('input').focus();
        } }, 'Forgot your passphrase? Use your recovery key')) : null,
    );
    form.addEventListener('submit', async e => {
      e.preventDefault();
      errEl.textContent = '';
      input.removeAttribute('aria-invalid');
      if (!input.value) { errEl.textContent = 'Enter your passphrase.'; input.setAttribute('aria-invalid', 'true'); input.focus(); return; }
      await busy(submit, async () => {
        try {
          await api('/api/unlock', { method: 'POST', json: { path: v.path, passphrase: input.value } });
          input.value = '';
          await afterUnlock();
        } catch (err) {
          const wrong = err.status === 401 || err.status === 403;
          errEl.textContent = wrong ? 'That passphrase didn’t unlock this workspace. Check Caps Lock and try again.' : err.message;
          input.setAttribute('aria-invalid', 'true');
          input.select();
        }
      }, 'Unlocking…');
    });
    return form;
  }

  /** Forgotten passphrase: recovery key + a NEW passphrase (required — the old one stops working). */
  function recoveryForm(v, i) {
    const code = h('input', { class: 'input mono', id: `rc-${i}`, autocomplete: 'off', spellcheck: 'false', autocapitalize: 'characters',
      placeholder: 'XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX', 'aria-describedby': `rc-hint-${i}` });
    const pw = h('input', { class: 'input', id: `rc-pw-${i}`, type: 'password', autocomplete: 'new-password' });
    const pw2 = h('input', { class: 'input', id: `rc-pw2-${i}`, type: 'password', autocomplete: 'new-password' });
    const meter = h('div.strength', { 'data-score': '0', 'aria-live': 'polite' }, h('div.strength-bar', { 'aria-hidden': 'true' }, h('i'), h('i'), h('i'), h('i')), h('p.strength-label'));
    pw.addEventListener('input', () => { const r = strength(pw.value); meter.dataset.score = String(r.score); meter.querySelector('.strength-label').textContent = pw.value ? `${r.label} — ${r.tip}` : ''; });
    const errEl = h('div', { role: 'alert' });
    const submit = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, icon('key', 16), 'Unlock and set new passphrase');
    const form = h('form', { class: 'unlock-form', id: `unlock-${i}`, novalidate: true },
      h('div.field', h('label', { for: `rc-${i}` }, 'Recovery key'), code,
        h('span.hint', { id: `rc-hint-${i}` }, '32 letters and digits, from the recovery key you saved. Dashes and spaces are optional.')),
      h('div.field', h('label', { for: `rc-pw-${i}` }, 'New passphrase'), pw, meter),
      h('div.field', h('label', { for: `rc-pw2-${i}` }, 'Confirm new passphrase'), pw2),
      h('div', { style: { marginTop: '12px' } }, errEl),
      h('div', { style: { marginTop: '6px' } }, submit),
      h('p', { style: { textAlign: 'center', marginTop: '10px' } },
        h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => {
          code.value = pw.value = pw2.value = '';
          const uf = unlockForm(v, i);
          form.replaceWith(uf);
          uf.querySelector('input').focus();
        } }, '← Use my passphrase instead')),
    );
    form.addEventListener('submit', async e => {
      e.preventDefault();
      clear(errEl);
      const problems = [];
      const raw = code.value.replace(/[\s-]+/g, '');
      if (raw.length !== 32) problems.push('A recovery key has 32 letters and digits.');
      if (pw.value.length < MIN_LEN) problems.push(`New passphrase needs at least ${MIN_LEN} characters.`);
      else if (strength(pw.value).score < 2) problems.push('That new passphrase is easy to guess. Add another word or two.');
      if (pw.value !== pw2.value) problems.push('The new passphrases don’t match.');
      if (problems.length) { errEl.append(h('div.error-box', icon('alert', 16), h('div', problems.map(p => h('div', p))))); return; }
      await busy(submit, async () => {
        try {
          await api('/api/unlock/recovery', { method: 'POST', json: { path: v.path, recovery_code: code.value, new_passphrase: pw.value } });
          code.value = pw.value = pw2.value = '';
          toast('Passphrase reset', { detail: 'Use the new passphrase from now on. Consider creating a new recovery key in Settings → Security.' });
          await afterUnlock();
        } catch (err) {
          errEl.append(h('div.error-box', icon('alert', 16), err.status === 401 ? 'That recovery key didn’t unlock this workspace. Check it and try again.' : err.message));
          code.select();
        }
      }, 'Recovering…');
    });
    return form;
  }

  // -------------------------------------------------------------- create
  function drawCreate() {
    const { form, focus } = createVaultForm({
      onCreated: async () => {
        toast('Workspace created', { detail: 'Encrypted and saved on this computer.' });
        await afterUnlock('import');
      },
    });
    panel.append(
      h('h2', vaults.length ? 'Create a new workspace' : 'Set up your first workspace'),
      h('p.sub', 'Your workspace is an encrypted folder on this computer. Only your passphrase opens it.'),
      form,
    );
    if (vaults.length) {
      panel.append(h('p', { style: { textAlign: 'center', marginTop: '18px' } },
        h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => { state.mode = 'unlock'; state.open = null; draw(); } }, '← Back to my workspaces')));
    } else {
      panel.append(h('div.or-divider', 'or'), restoreButton());
    }
    requestAnimationFrame(focus);
  }
}

/**
 * The "create an encrypted workspace" form (welcome screen and setup wizard step 3).
 * `compact`: the wizard's short version (no visible-label field; details behind "Why?").
 * `onCreated({ passphrase, name, status })` runs after the vault exists (and the recovery key was shown).
 */
export function createVaultForm({ compact = false, onCreated } = {}) {
  const why = (text) => h('details.why', h('summary', 'Why?'), h('p', text));
  const name = h('input', { class: 'input', id: 'ws-name', autocomplete: 'organization', placeholder: 'e.g. Rivera Studio LLC', maxlength: 80 });
  const label = h('input', { class: 'input', id: 'ws-label', autocomplete: 'off', placeholder: 'e.g. Client 14', maxlength: 80, 'aria-describedby': 'ws-label-hint' });
  const pw = h('input', { class: 'input', id: 'ws-pw', type: 'password', autocomplete: 'new-password', 'aria-describedby': 'ws-pw-hint ws-strength' });
  const pw2 = h('input', { class: 'input', id: 'ws-pw2', type: 'password', autocomplete: 'new-password' });
  const tip = compact ? '10+ characters. A few random words.' : 'Use at least 10 characters. A few random words works well.';
  const meter = h('div.strength', { id: 'ws-strength', 'data-score': '0', 'aria-live': 'polite' },
    h('div.strength-bar', { 'aria-hidden': 'true' }, h('i'), h('i'), h('i'), h('i')),
    h('p.strength-label', tip));
  const ack = h('input', { type: 'checkbox', id: 'ws-ack' });
  const rec = h('input', { type: 'checkbox', id: 'ws-recovery', checked: true, 'aria-describedby': 'ws-recovery-hint' });
  const errEl = h('div', { role: 'alert' });
  const submit = compact
    ? h('button', { class: 'btn btn-primary btn-block btn-lg', type: 'submit' }, icon('lock', 16), 'Create my vault')
    : h('button', { class: 'btn btn-primary btn-block btn-lg', type: 'submit' }, 'Create encrypted workspace');

  pw.addEventListener('input', () => {
    const r = strength(pw.value);
    meter.dataset.score = String(r.score);
    meter.querySelector('.strength-label').textContent = pw.value ? `${r.label} — ${r.tip}` : tip;
  });

  const form = compact
    ? h('form', { novalidate: true },
      h('div.field', h('label', { for: 'ws-name' }, 'Name'), name),
      h('div.field', h('label', { for: 'ws-pw' }, 'Passphrase'), pw, meter),
      h('div.field', h('label', { for: 'ws-pw2' }, 'Confirm passphrase'), pw2),
      h('div.warning-box.compact', { style: { marginTop: '16px' } }, icon('alert', 18),
        h('div',
          h('strong', 'No reset. Save it somewhere safe.'),
          why('There’s no account and no server. Without your passphrase (or recovery key) nobody can open this vault, including us.'),
          h('label.check', { style: { marginTop: '8px' } }, ack, 'I saved my passphrase'))),
      h('div.field.rec-row', { style: { marginTop: '12px' } },
        h('label.check', rec, 'Create a recovery key'),
        h('span', { id: 'ws-recovery-hint' }, why('A one-time code that opens this vault if you forget the passphrase. Keep it offline.'))),
      h('div', { style: { marginTop: '12px' } }, errEl),
      h('div', { style: { marginTop: '6px' } }, submit))
    : h('form', { novalidate: true },
      h('div.field', h('label', { for: 'ws-name' }, 'Workspace name'), name,
        h('span.hint', 'One workspace per business or client. Stored encrypted — you can create more later.')),
      h('div.field', h('label', { for: 'ws-label' }, 'Visible label (shown before unlock)', h('span.muted', { style: { fontWeight: 400 } }, ' — optional')), label,
        h('span.hint', { id: 'ws-label-hint' }, 'Leave blank to keep the client’s name private on disk. The picker will show “Workspace (locked)” with its creation date.')),
      h('div.field', h('label', { for: 'ws-pw' }, 'Passphrase'), pw, h('span.hint', { id: 'ws-pw-hint' }, 'This encrypts everything you import.'), meter),
      h('div.field', h('label', { for: 'ws-pw2' }, 'Confirm passphrase'), pw2),
      h('div.warning-box', { style: { marginTop: '18px' } }, icon('alert', 18),
        h('div',
          h('strong', 'We can’t recover this passphrase — there’s no account and no server.'),
          h('p', { style: { marginTop: '4px', color: 'var(--ink-2)' } }, 'Write it down or save it in your password manager. Without it, this workspace can’t be opened by anyone, including us.'),
          h('label.check', { style: { marginTop: '10px' } }, ack, 'I’ve saved my passphrase somewhere safe'))),
      h('div.field', { style: { marginTop: '14px' } },
        h('label.check', rec, 'Also create a recovery key (recommended)'),
        h('span.hint', { id: 'ws-recovery-hint' }, 'A one-time code that opens this workspace if you forget the passphrase. Anyone who has it can open the workspace, so keep it offline.')),
      h('div', { style: { marginTop: '14px' } }, errEl),
      h('div', { style: { marginTop: '6px' } }, submit),
    );
  form.addEventListener('submit', async e => {
    e.preventDefault();
    clear(errEl);
    const problems = [];
    [name, pw, pw2].forEach(x => x.removeAttribute('aria-invalid'));
    if (!name.value.trim()) { problems.push('Give your workspace a name.'); name.setAttribute('aria-invalid', 'true'); }
    if (pw.value.length < MIN_LEN) { problems.push(`Passphrase needs at least ${MIN_LEN} characters.`); pw.setAttribute('aria-invalid', 'true'); }
    else if (strength(pw.value).score < 2) { problems.push('That passphrase is easy to guess. Add another word or two.'); pw.setAttribute('aria-invalid', 'true'); }
    if (pw.value !== pw2.value) { problems.push('The two passphrases don’t match.'); pw2.setAttribute('aria-invalid', 'true'); }
    if (!ack.checked) problems.push('Please confirm you’ve saved your passphrase.');
    if (problems.length) {
      errEl.append(h('div.error-box', icon('alert', 16), h('div', problems.map(p => h('div', p)))));
      (form.querySelector('[aria-invalid="true"]') || ack).focus();
      return;
    }
    await busy(submit, async () => {
      try {
        const body = { name: name.value.trim(), passphrase: pw.value, recovery: rec.checked };
        if (!compact && label.value.trim()) body.label = label.value.trim();
        const r = await api('/api/vaults', { method: 'POST', json: body });
        const passphrase = pw.value;
        pw.value = pw2.value = '';
        if (r && r.recovery_code) await showRecoveryCode(r.recovery_code, { workspace: body.name });
        if (onCreated) await onCreated({ passphrase, name: body.name, status: r });
      } catch (err) {
        errEl.append(h('div.error-box', icon('alert', 16), err.message));
      }
    }, 'Encrypting…');
  });
  return { form, focus: () => name.focus() };
}

function hero() {
  return h('section.welcome-hero', { 'aria-label': 'About LedgerStitch' },
    h('div', h('span.brand', logo({ onDark: true }))),
    h('div',
      h('h1', 'Every statement, one tidy report your CPA will love.'),
      h('p.lede', 'Drop in your bank and card statements. LedgerStitch sorts every transaction into Business, Personal, Transfer or Income and builds a Schedule C–ready workbook.'),
      h('ul.privacy-list',
        h('li', icon('laptop', 20), h('span', h('strong', 'Runs on your computer. '), 'No LedgerStitch account, no cloud, no tracking.')),
        h('li', icon('lock', 20), h('span', h('strong', 'Encrypted with your passphrase. '), 'AES-256 on disk; your files are never stored unencrypted.')),
        h('li', icon('eye', 20), h('span', h('strong', 'AI sees merchant names only. '), 'Never amounts, dates, balances or account numbers — and it’s optional.')),
      ),
    ),
    h('p.hero-foot', 'LedgerStitch prepares records for your tax professional. It isn’t tax advice.'),
    heroLanes(),
  );
}

function heroLanes() {
  const g = s('svg', { class: 'hero-lanes', viewBox: '0 0 560 300', 'aria-hidden': 'true' });
  const lanes = [[40, 420], [100, 330], [160, 250], [220, 170]];
  lanes.forEach(([y, w], i) => {
    g.append(s('rect', { x: 560 - w, y, width: w, height: 26, rx: 13, fill: '#FFFFFF', 'fill-opacity': 0.9 - i * 0.18 }));
  });
  return g;
}

export function privacyBadges() {
  const src = sourceInfo();
  return h('div.privacy-badges', { 'aria-label': 'Privacy' },
    h('span', icon('laptop', 14), 'Runs on your computer'),
    h('span', { 'aria-hidden': 'true' }, '·'),
    h('span', icon('lock', 14), 'Encrypted with your passphrase'),
    h('span', { 'aria-hidden': 'true' }, '·'),
    h('span', icon('eye', 14), 'AI sees merchant names only'),
    src.url ? h('span.source-row', h('a', { href: src.url, target: '_blank', rel: 'noopener noreferrer', 'data-source-link': '' }, `Source code (${src.license})`)) : null,
  );
}

/**
 * Show a recovery key exactly once. Copy / download as text; "Done" stays disabled until the
 * user ticks "I saved it". The code lives only in this dialog's DOM and is dropped on close.
 */
export function showRecoveryCode(code, { workspace = '', replaced = false } = {}) {
  const ack = h('input', { type: 'checkbox', id: 'rk-ack' });
  const note = h('p.small.muted', { role: 'status', 'aria-live': 'polite', style: { minHeight: '1.4em', marginTop: '6px' } });
  const codeEl = h('div.recovery-code.mono', { 'aria-label': 'Recovery key', tabindex: '0' }, code);
  const copy = h('button', { class: 'btn', type: 'button', onclick: async () => {
    try { await navigator.clipboard.writeText(code); note.textContent = 'Copied. Paste it somewhere offline, then copy something else to clear your clipboard.'; }
    catch { note.textContent = 'Couldn’t copy automatically. Select the key and copy it by hand.'; }
  } }, icon('copy', 16), 'Copy');
  const save = h('button', { class: 'btn', type: 'button', onclick: () => {
    const text = ['LedgerStitch recovery key', workspace ? `Workspace: ${workspace}` : null, `Created: ${new Date().toISOString().slice(0, 10)}`, '',
      code, '', 'Anyone with this key can open the workspace. Keep it offline (printed, or in a password manager).',
      'Use it on the unlock screen: "Forgot your passphrase? Use your recovery key".', ''].filter(x => x !== null).join('\n');
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
    const a = h('a', { href: url, download: 'ledgerstitch-recovery-key.txt', rel: 'noopener' });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    note.textContent = 'Saved as ledgerstitch-recovery-key.txt. Move it off this computer (print it, or a USB stick) and delete the download.';
  } }, icon('download', 16), 'Download as text');
  const m = modal({
    title: 'Save your recovery key', dismissible: false,
    description: replaced ? 'Your previous recovery key no longer works. This new one is shown only once.' : 'This is shown only once. LedgerStitch doesn’t keep a copy.',
    body: h('div',
      codeEl,
      h('div.row', { style: { marginTop: '12px', gap: '8px' } }, copy, save),
      note,
      h('div.warning-box', { style: { marginTop: '12px' } }, icon('alert', 18),
        h('div', h('strong', 'Anyone who has this key can open the workspace.'),
          h('p', { style: { marginTop: '4px', color: 'var(--ink-2)' } }, 'Keep it offline, away from this computer — printed, or in a password manager. If you forget your passphrase, it’s the only way back in.'))),
      h('label.check', { style: { marginTop: '14px' } }, ack, 'I’ve saved my recovery key somewhere safe')),
    actions: [{ label: 'Done', primary: true, value: true }],
    initialFocus: '.recovery-code',
  });
  const done = m.actions[0].el;
  done.disabled = true;
  ack.addEventListener('change', () => { done.disabled = !ack.checked; });
  return m.result.then(r => { codeEl.textContent = ''; return r; });
}

/** Picker title: the real name once known, else the opt-in visible label, else a neutral placeholder. */
function vaultTitle(v) {
  return v.name || v.label || 'Workspace (locked)';
}
function fmtCreated(c) {
  if (!c) return '';
  const dateOnly = typeof c === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(c);
  const d = typeof c === 'number' ? new Date(c < 1e12 ? c * 1000 : c) : new Date(dateOnly ? c + 'T00:00:00Z' : c);
  return isNaN(d) ? '' : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', ...(dateOnly ? { timeZone: 'UTC' } : {}) });
}

/** Lightweight passphrase strength estimate (no dictionary download). */
export function strength(pw) {
  if (!pw) return { score: 0, label: '', tip: '' };
  let pool = 0;
  if (/[a-z]/.test(pw)) pool += 26;
  if (/[A-Z]/.test(pw)) pool += 26;
  if (/[0-9]/.test(pw)) pool += 10;
  if (/[^a-zA-Z0-9]/.test(pw)) pool += 20;
  let bits = pw.length * Math.log2(Math.max(pool, 1));
  if (/(.)\1{2,}/.test(pw)) bits -= 12;
  if (/^(password|passphrase|letmein|qwerty|123456|ledgerstitch|admin|welcome)/i.test(pw)) bits -= 30;
  if (/^(?:abc|123|qwe)/i.test(pw)) bits -= 10;
  const words = pw.trim().split(/[\s\-_.]+/).filter(w => w.length >= 3).length;
  if (words >= 4) bits += 10;
  if (pw.length < MIN_LEN) return { score: 1, label: 'Too short', tip: `use at least ${MIN_LEN} characters` };
  if (bits < 45) return { score: 1, label: 'Weak', tip: 'add another word or two' };
  if (bits < 60) return { score: 2, label: 'Fair', tip: 'a little longer would be safer' };
  if (bits < 80) return { score: 3, label: 'Strong', tip: 'good choice' };
  return { score: 4, label: 'Very strong', tip: 'excellent' };
}
