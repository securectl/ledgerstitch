# Security Policy

LedgerStitch handles bank and card statements, so we treat security reports as the
highest-priority work in the project. Thank you for helping keep users safe.

> Rename this file to `SECURITY.md` at the repo root before the repository goes public.
> GitHub shows it on the "Security" tab and links it from the "Report a vulnerability" button.
> Before publishing, replace `{{SECURITY_EMAIL}}` and `{{PGP_KEY_URL}}` and turn on
> **GitHub Private Vulnerability Reporting** (Settings → Code security → Private vulnerability reporting).

## Supported versions

We support the latest minor release of each edition. Security fixes are released for Community
and the paid add-ons at the same time. Community users never wait for a paid fix.

| Version | Community (AGPL-3.0) | Paid add-ons | Status |
|---|---|---|---|
| 1.x (latest minor) | Yes | Yes | Security fixes and bug fixes |
| 1.x (previous minor) | Critical fixes only, for 90 days after the next minor ships | Same | Upgrade recommended |
| < 1.0 / pre-release / `main` builds | No | No | Not supported |

**Vault format promise.** Major versions are the only releases that may change the vault
format. Whenever a fix requires a format change, we ship a migration and give you at least one
release where you can still open older vaults.

## How to report a vulnerability

**Do not open a public GitHub issue, discussion or pull request for security problems.**

Use one of these private channels:

1. **GitHub Private Vulnerability Reporting** (preferred): the repository's *Security* tab →
   *Report a vulnerability*.
2. **Email:** `{{SECURITY_EMAIL}}`. If you want to encrypt, our PGP key is at `{{PGP_KEY_URL}}`.

Please include:
- the affected version and edition (Community, or Community plus an add-on), and your OS;
- the impact: what an attacker can do, and from where (another website in the browser, another
  local user, a stolen vault file, a malicious statement file, an AI model's response, and so on);
- clear reproduction steps or a proof of concept;
- whether you believe the issue is already public.

**Never send real financial data.** Don't include real statements, real vaults, real receipts or
API keys in a report. Use the synthetic fixtures in `tests/fixtures/` or make your own fake data.
If a real file is truly the only way to show the problem, tell us first and we will agree on a
safe method.

## What happens next

| Step | Target |
|---|---|
| We confirm we received your report | within **2 business days** |
| First assessment (valid / not valid, our severity estimate) | within **7 days** |
| Fix for Critical / High severity | aim for **30 days** |
| Fix for Medium / Low severity | next scheduled release, at most 90 days |
| Coordinated public disclosure | when the fix is released, or **90 days** after your report, whichever comes first, unless we agree otherwise |

We will keep you informed, credit you in the release notes and the advisory (unless you prefer
to remain anonymous), and request a CVE through GitHub Security Advisories where it applies.
LedgerStitch is run by a solo founder and has **no paid bug bounty at this time**. If that
changes, this page will say so.

## Scope

**In scope** (examples of what we most want to hear about):
- Anything that lets another website, browser tab or local process read or change data through
  the local server on `127.0.0.1` (bypassing the token, Host or Origin checks, CSP, DNS rebinding).
- Vault cryptography weaknesses: AES-256-GCM / Argon2id usage, key handling, tamper detection,
  KDF-parameter downgrade, file swapping.
- Plaintext financial data written to disk where our docs say it isn't (temp files, logs, caches).
- **Redaction bypasses.** Any way that an amount, date, account or card number, email, phone
  number or personal name reaches an AI model or an add-on's payload. Also any mismatch between
  "Preview what AI sees" and what is actually sent.
- Malicious statement, receipt or image files: parser crashes that corrupt a vault, path
  traversal, decompression bombs, code execution.
- Prompt injection through AI responses that changes anything beyond the category/lane of the
  targeted merchant.
- Plugin API boundaries: an add-on's static files served outside `/addons/<namespace>/`, served
  without sign-in in server mode, or reachable by path traversal.
- License-signature forgery (Ed25519 key verification).
- Supply-chain issues in our release process: build scripts, signing, notarization, release
  artifacts and checksums.
- **Community edition network behavior.** Any outbound network connection the Community
  edition makes without the user explicitly asking for it is a bug we treat as security-relevant.

**Out of scope:**
- Attacks that need an already-compromised computer or user account (malware, keyloggers,
  someone logged in as you while a vault is unlocked). See `docs/SECURITY.md`, "What we do not
  protect against".
- Weak or forgotten passphrases. There is no recovery by design.
- Exported Excel, CSV and QuickBooks files being unencrypted. This is by design and documented.
- Simply bypassing the license check in AGPL source you build yourself. The license allows you
  to modify the core. Paid add-ons are covered by their own terms. Please report
  real **signature forgery**, but not "I patched the check out".
- Reports produced only by automated scanners, with no demonstrated impact.
- Social engineering of the maintainers, and physical attacks.

## Good-faith research

If you make a good-faith effort to follow this policy, we will not pursue or support legal action
against you for your research, and we will treat it as authorized. Good faith means you:
- only test against your own installation and your own synthetic data;
- don't access, change or keep anyone else's data;
- don't degrade service for anyone else (this matters most for future hosted editions);
- give us reasonable time to fix the issue before you disclose it.

*Have a lawyer review this wording before publishing.*

## For future hosted editions

When a hosted LedgerStitch exists, this policy will gain a separate section with its scope (domains),
test-account rules and rate limits. Until then, **there is no LedgerStitch hosted service**. Any
site claiming to be one is not us. Please report it to `{{SECURITY_EMAIL}}`.

## Our own practices (summary)

- Release artifacts are code-signed and notarized (macOS), and published with SHA-256
  checksums. Tags are signed.
- Dependencies are pinned for release builds, and a software bill of materials (SBOM) is
  published with each release.
- Secret scanning runs on every push. The repository must never contain real statements, vaults,
  API keys or the license signing key.
- The design and threat model are in `docs/SECURITY.md`.
