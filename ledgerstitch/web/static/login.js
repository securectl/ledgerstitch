// SPDX-License-Identifier: AGPL-3.0-only
// LedgerStitch — login.js : server-mode sign-in / first-run admin password (login.html).
const $ = id => document.getElementById(id);
let configured = true;
let minLength = 12;

async function post(path, body) {
  const r = await fetch(path, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) });
  let data = {};
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error(data.error || `Sign-in failed (${r.status}).`);
  return data;
}

async function init() {
  try {
    const r = await fetch('/auth/state', { credentials: 'same-origin', cache: 'no-store' });
    const st = await r.json();
    if (st.signed_in) { location.replace('/'); return; }
    configured = !!st.configured;
    minLength = st.min_length || 12;
  } catch { /* show the sign-in form anyway */ }
  if (!configured) {
    $('login-title').textContent = 'Set the admin password';
    $('login-sub').textContent = 'First run. One password for this server.';
    $('code-field').hidden = false;
    $('pw2-field').hidden = false;
    $('pw-label').textContent = `New admin password (${minLength}+ characters)`;
    $('pw').autocomplete = 'new-password';
    $('submit').textContent = 'Save and sign in';
    $('setup-code').focus();
  } else {
    $('pw').focus();
  }
}

$('login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const err = $('err');
  err.textContent = '';
  const pw = $('pw').value;
  const btn = $('submit');
  if (!pw) { err.textContent = 'Enter the password.'; return; }
  if (!configured) {
    if (pw.length < minLength) { err.textContent = `Use at least ${minLength} characters.`; return; }
    if (pw !== $('pw2').value) { err.textContent = 'The passwords don’t match.'; return; }
  }
  btn.disabled = true;
  try {
    if (configured) await post('/auth/login', { password: pw });
    else await post('/auth/setup', { setup_code: $('setup-code').value.trim(), password: pw });
    $('pw').value = ''; $('pw2').value = '';
    location.replace('/');
  } catch (ex) {
    err.textContent = ex.message;
    $('pw').select();
  } finally {
    btn.disabled = false;
  }
});

init();
