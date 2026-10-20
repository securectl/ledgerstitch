# LedgerStitch Security & Privacy

This page describes how LedgerStitch protects your financial data: what we defend against,
how the encryption works, what the optional AI feature can and cannot see, and where the
limits are. It is written for customers, bookkeepers and their CPAs.

## 1. The short version

- **Local only.** LedgerStitch runs on your computer and serves its UI on `127.0.0.1`.
  There are no LedgerStitch servers, no account and no telemetry. (Self-hosting on your own
  server is opt-in and adds a sign-in; see section 7a.)
- **Encrypted at rest.** Everything LedgerStitch saves is encrypted with AES-256-GCM under a
  key derived from your passphrase with Argon2id. Uploaded statements are never written to
  disk in plaintext.
- **AI is optional and sees almost nothing.** When it is on, the model receives only
  short, redacted merchant descriptors such as `NETFLIX.COM` or `ZELLE TO <PERSON>`. It never
  receives amounts, dates, balances, account or card numbers, names, addresses, phone numbers
  or email addresses. You can preview the exact payload before anything is sent.
- **Licensing is offline.** License keys are checked with a digital signature on your
  machine, so nothing "phones home".

## 2. Threat model

### What we protect against

| Threat | Protection |
|---|---|
| Laptop lost or stolen, disk image copied, backup or cloud-sync copy leaked | All vault contents are encrypted (AES-256-GCM). Without the passphrase, the files are unreadable. |
| Offline passphrase guessing against a stolen vault | Argon2id with 64 MiB of memory and 3 passes per guess makes large-scale guessing slow and expensive, especially on GPUs. |
| Tampering with or swapping vault files | Every file is authenticated. Any modification, truncation, or swapping of one entry's file for another's is detected and refused. |
| Tampering with the vault header to weaken its key derivation | The KDF parameters and salt are bound into the encryption of the vault key, and out-of-range parameters are rejected. |
| An AI model, or an add-on that talks to one, learning about you | Only redacted descriptors are handed over (see section 5). Amounts, dates and identifiers never leave the core. |
| Other users on the same computer | Vault folders are created with owner-only permissions (`0700`), files with `0600`. |
| Other websites in your browser calling the local app | The local API requires a per-launch random token that is injected only into LedgerStitch's own page. The page uses a strict Content Security Policy with no third-party scripts or fonts. |
| Forged license keys | Keys are Ed25519-signed. The app contains only the public key, so it can verify keys but cannot create them. |

### What we do not protect against

LedgerStitch cannot keep you safe from these, and we would rather say so plainly:

- **A compromised computer.** Malware, keyloggers or someone with your logged-in user account
  can capture your passphrase or read data while a vault is unlocked.
- **A weak passphrase.** Argon2id slows guessing down but cannot save a short or common
  passphrase. Use a long, unique one, such as four or more random words.
- **A forgotten passphrase without a recovery key.** There is no backdoor or reset. The only
  way back into a vault is its passphrase or, if you created one, its recovery key (section 3).
  If both are lost, the vault's data is gone. Encrypted backups don't help here: they open with
  the same secrets.
- **Someone who has your recovery key.** The recovery key opens the vault exactly like the
  passphrase does. It is optional, and if you use it you must keep it offline.
- **Old backups and old secrets.** A backup opens with the passphrase and recovery key that were
  valid when it was made. Changing the passphrase or replacing the recovery key does not
  re-lock backups you already made.
- **Files you export.** Excel, CSV and QuickBooks exports are ordinary unencrypted files, by
  design, so your CPA can open them. Store and share them carefully.
- **Your original statements.** LedgerStitch does not alter or delete the files you uploaded.
  Those are still wherever you downloaded them.
- **Memory while unlocked.** Locking a vault zeros LedgerStitch's own copy of the key. Python
  does not guarantee every temporary copy inside the runtime and crypto libraries is erased.
  Treat this as defence in depth, not a guarantee against someone who can read process memory.

## 3. Vault encryption design

Each vault is one folder, typically one per business or bookkeeping client.

### Keys

1. **Passphrase to KEK.** Your passphrase is Unicode-normalized (NFC), then run through
   **Argon2id** (`time_cost=3`, `memory_cost=64 MiB`, `parallelism=4`, 16-byte random salt,
   32-byte output). The result is the *key-encryption key* (KEK). The KEK is never stored.
2. **DEK.** When a vault is created, a random 32-byte *data-encryption key* (DEK) is generated
   from the operating system's secure random source. The DEK encrypts all of the vault's data.
3. **Wrapping.** The DEK is stored only in wrapped form: `AES-256-GCM(KEK, DEK)` with a random
   12-byte nonce. The KDF parameters, salt and format version are bound as associated data
   (AAD). A wrong passphrase, or any change to those header fields, makes the GCM tag check
   fail, and the app reports "wrong passphrase".

**Changing your passphrase** re-derives a new KEK with a fresh salt and re-wraps the same DEK.
The data files are not touched, so the change is instant and cannot corrupt your data. Note
that someone who copied the vault *and* knew the old passphrase can still decrypt that old
copy. To fully revoke an old passphrase, create a new vault and move your data into it.

### Recovery key (optional)

A vault can have one **recovery key**, offered when you create a vault and available later in
Settings → Security (your current passphrase is required):

1. **Code.** 20 random bytes (160 bits) from the OS CSPRNG, shown as 32 base32 characters in
   groups of four (`XXXX-XXXX-…`). It is displayed **once**, with copy and "download as text"
   buttons, and LedgerStitch never stores or logs it. When you type it in, case, spaces, dashes
   and the look-alikes `0/O`, `1/I`, `8/B` are forgiven.
2. **Second wrap.** The code's bytes go through Argon2id (same parameters as above) with **their
   own 16-byte salt**. The result wraps the **same DEK** with AES-256-GCM. The recovery KDF
   parameters, salt and format version are bound as AAD under a separate prefix, so the
   recovery wrap cannot be swapped into the passphrase slot, and tampering with its header
   fields makes recovery fail. Out-of-range parameters are rejected just as for the main wrap.
3. **Use.** "Forgot your passphrase?" on the unlock screen takes the recovery key **and a new
   passphrase** in one step: the DEK is re-wrapped under the new passphrase, so the forgotten
   one stops working. The recovery key stays valid; replace it if someone else may have seen it.
4. **Replace or remove.** Creating a new recovery key overwrites the recovery wrap, so the old
   code stops working at once. Removing it deletes the wrap. Both need the current passphrase.
   Changing the passphrase leaves the recovery wrap as is.

**Be honest with yourself about the trade-off.** The recovery key is a second key to the same
data: **anyone who has it can open the vault** without your passphrase. It has far more entropy
than a typical passphrase, so guessing it is not a realistic attack; losing a copy of it is.
Keep it offline (printed, or in a password manager), never next to the vault folder or its
backups. If you'd rather have a single secret, leave it off: older vaults and vaults without a
recovery key work exactly as before.

### Encrypted backups

**Back up now** (Settings → Your data) writes one `.lsbackup` file: a zip of the vault's
**already encrypted** files (the header plus one file per entry) and a manifest with each file's
size and SHA-256. Nothing is decrypted to make it, so the backup is as protected as the vault
folder itself, and it opens only with the passphrase or recovery key that were valid when it was
made. The manifest hashes catch truncation and corruption early; protection against tampering
comes, as always, from AES-GCM on every entry and on the header's wrapped keys.

**Restore from a backup** (unlock screen) treats the file as untrusted input:

- Only the expected member names are accepted (`manifest.json`, `vault/vault.json`,
  `vault/<24 hex>.bin`). Absolute paths, `..`, sub-folders, directory entries, symlinks and other
  special files are rejected.
- File count, per-file size and total size are capped, and sizes are counted while extracting
  rather than trusted from the zip headers.
- The manifest must match the contents exactly, every file must match its checksum, and the
  vault header must pass the same format and KDF-bounds checks as a normal unlock.
- The backup always goes into a **new** vault folder with a random name, created with owner-only
  permissions. An existing vault is never overwritten. Files are created exclusively without
  following links, the header is written last, and a failed restore removes the partial folder.

The app records the time of your last backup inside the encrypted settings and reminds you if
you have data and no backup, or the last one is more than 30 days old.

### Files on disk

```
<vault folder>/            (mode 0700)
  vault.json               header: format, version, label, encrypted name, KDF params + salt,
                           wrapped DEK, creation date, optional recovery wrap
  3f9a...e1.bin            one file per stored item   (mode 0600)
  ...
```

- Each item (transactions, accounts, rules, settings, saved API keys, and so on) is stored as
  `nonce(12 bytes) || AES-256-GCM(DEK, data)`, with a fresh random nonce on every write.
- The item's **name is bound as AAD**, so a file cannot be renamed or swapped for another
  item's file without detection.
- **File names are the first 24 hex characters of SHA-256(item name).** The list of item names
  is itself stored as an encrypted entry, so nothing on disk reveals what kinds of data exist.
- **Writes are atomic.** Data goes to a temporary `0600` file in the same folder, is flushed to
  disk, then atomically renamed into place. A crash or power loss leaves either the old version
  or the new one, never a half-written file.

### What is visible without the passphrase

The header `vault.json` is the only plaintext file. It contains:

| Field | Contents | Sensitive? |
|---|---|---|
| `format`, `version` | `ledgerstitch-vault`, `1` | No |
| `kdf` | Argon2id parameters and random salt | No (salts are not secret) |
| `wrapped_dek` | The encrypted data key | No (useless without the passphrase) |
| `recovery` | Only if you created a recovery key: its KDF parameters and salt, a second wrap of the data key, and the day it was created | No (useless without the recovery key). Its presence shows that a recovery key exists. |
| `name_enc` | The vault name, **encrypted** with the DEK | No |
| `label` | Optional plaintext display label for the vault picker | **Your choice** (see below) |
| `created` | Creation date (day precision, UTC) | Minimal |

**About the label.** The vault picker needs to show something before you unlock. By default
the label is the vault name, for example "Sam's Plumbing". If the name itself is sensitive,
such as a bookkeeper's client list, set a neutral label like "Client 07". The real name stays
encrypted and appears only after unlock. Anyone with file access can also see file sizes and
modification times, which give a rough sense of how much data a vault holds.

## 4. What the AI can see

AI categorization is **off unless you turn it on**. In the Community edition it runs fully
offline: built-in rules plus a local Ollama model on your computer (or your own server). The
same rules apply to local AI and to any AI add-on plugged in through the plugin API
(docs/PLUGINS.md §3):

- Rules and built-in heuristics run first, locally. Only transactions they cannot categorize
  are candidates for AI.
- Each candidate's description is passed through LedgerStitch's **redactor** (section 5).
- The model receives exactly one JSON document per request:
  `{"descriptors": ["NETFLIX.COM", "ZELLE TO <PERSON>", ...]}`, plus the list of your
  category names. Duplicates are collapsed, so a merchant you visited 40 times is sent once.
- **Never sent:** amounts, dates, balances, account or card numbers, transaction IDs, names,
  addresses, phone numbers, email addresses, your business name, or which account a
  transaction came from.
- **Settings → "Preview what AI sees"** shows the byte-for-byte payload. The preview and the
  real request are produced by the same function, so they cannot drift apart.
- Add-ons never receive transactions, amounts or dates: the core builds the redacted list and
  validates the answers itself. Any secret an add-on stores (such as an API key) is kept inside
  the encrypted vault and is never sent to the browser.

## 5. Redaction policy

Our rule is **"when in doubt, remove."** A descriptor that comes out too vague to categorize
costs you a click in the review queue. A leaked name or account number cannot be taken back.

**Removed:**

- **Long numbers.** Any run of 4 or more digits, any token containing 3 or more digits, and
  letter-digit codes that look like IDs (`BJ1M70883`, `3DXZ7Q`).
- **Card and account masks.** `XXXX1234`, `****1234`, `...1234`, "ending in 1234",
  `ACCT #...`.
- **Reference fields.** Reference, confirmation, trace, authorization and order numbers
  (`REF #...`, `CONF# ...`, `TRACE#:...`).
- **Bank-transfer and wire metadata.** ACH fields such as `ORIG ID:`, `IND ID:`, `IND NAME:`,
  `INDN:`, `TRN:`, `EED:`, `SEC:`. Wire fields such as `BNF=`, `/FTR/`, `OBI=`, `SRF#`.
  Only the paying company's name and the entry description are kept.
- **Contact details.** Email addresses, phone numbers (including 1-800 vanity numbers), and URL
  paths and query strings. The domain is kept: `g.co/helppay#` becomes `G.CO`.
- **Dates, times and money amounts.**
- **Locations.** Street addresses, PO boxes, suites, ZIP codes, and city, state or country words
  at the end of a descriptor.
- **People.** The counter-party after `ZELLE`, `VENMO`, `CASH APP`, `APPLE CASH`, `PAYPAL *`,
  "transfer to/from", "payment to/from", "wire to", "bill pay", "deposit from" and similar
  phrases is replaced with `<PERSON>`. Any free-text memo after it is dropped, since memos
  often contain names.
- **Non-Latin text.** Characters outside basic Latin, such as names written in other scripts.

**Kept:**

- Merchant and brand words: `AMAZON MKTPL`, `GOOGLE *CLOUD`, `STARBUCKS STORE`.
- Domains: `NETFLIX.COM`, `APPLE.COM/BILL`.
- Small numbers that are part of brand names: `7-ELEVEN`, `99 RANCH`.
- A counter-party that is clearly a business, either because it contains LLC, INC, CORP, CO,
  LTD, a `.COM` domain, or an industry word such as ENERGY, INSURANCE, PLUMBING, or because it
  consists only of known bank, account or brand words (`TRANSFER TO EVERYDAY CHECKING`,
  `PAYMENT TO CHASE CARD`).

**Examples:**

| Your statement says | The AI sees |
|---|---|
| `ORIG CO NAME:CARDMEMBER SERV ORIG ID:5911111111 ... IND NAME:PAYABLE,ACCOUNTS TRN: 3530860929TC` | `CARDMEMBER SERV WEB PYMT` |
| `Zelle to John Smith on 03/12 Ref #Pp0Abc123` | `ZELLE TO <PERSON>` |
| `WT FED#01234 ZHEJIANG TAILONG /FTR/BNF=SOME PERSON SRF# 123` | `WT FED ZHEJIANG TAILONG` |
| `AMAZON MKTPL*BJ1M70883` | `AMAZON MKTPL` |
| `Walmart 6750 Murphy Road 75048 TX United States` | `WALMART` |
| `GOOGLE *CLOUD 3DXZ7Q g.co/helppay#` | `GOOGLE *CLOUD G.CO` |
| `PAYPAL *JOHNDOE 4029357733` | `PAYPAL *<PERSON>` |
| `PAYPAL *NETFLIX 4029357733 CA` | `PAYPAL *NETFLIX` |

**PayPal, Venmo and Cash App.** The name after `PAYPAL *` can be a business (`PAYPAL *EBAY`)
or a person (`PAYPAL *JOHNDOE`), and the two cannot be reliably told apart. LedgerStitch keeps
the name only if it is a known brand. Anything else becomes `<PERSON>`. Some small merchants
paid through PayPal will therefore reach the AI as `PAYPAL *<PERSON>` and land in your review
queue. Once you categorize one, a local rule remembers it, and that rule runs on your machine
using the unredacted merchant.

**Limits.** The redactor works on patterns. A personal name that appears with no recognizable
marker before it, for example as the whole merchant field of a hand-entered row, cannot always
be detected. Brand-like business names that contain a person's name, such as
`JOHN SMITH CONSULTING LLC`, are kept on purpose because they identify a business. If this
matters for your data, use offline mode, or check "Preview what AI sees" before sending.

**Testing.** The redactor's test suite covers more than 40 real-world descriptor formats. It
also includes property tests that assert no output ever contains 4 or more consecutive
digits, an `@`, or any of the test names, and that redacting already-redacted text changes
nothing.

## 6. Licensing

- A license key looks like `LS1.<payload>.<signature>`. The payload is base64url-encoded JSON
  `{email, plan, expires, issued}`. The signature is **Ed25519** over `LS1.<payload>`.
- The app contains only the seller's **public** key and checks keys entirely offline. Nothing
  is sent anywhere when you enter a key.
- An invalid, tampered or expired key falls back to Community, which has no usage limits.
  Only add-on features lock again. Your data is never locked or deleted because of licensing.
- The expiry date is inclusive and is checked against your computer's clock.
- The signing private key is kept by the seller offline, outside the source tree, with
  owner-only permissions. It is never shipped.

## 7. Cryptographic components

| Purpose | Algorithm | Library |
|---|---|---|
| Passphrase and recovery-key derivation | Argon2id (v1.3), t=3, m=64 MiB, p=4, 16-byte salt each, 32-byte output | `argon2-cffi` (`hash_secret_raw`) |
| Backup checksums | SHA-256 per file (corruption check; integrity comes from AES-GCM) | Python `hashlib` |
| Data and key encryption | AES-256-GCM, 96-bit random nonces, AAD binding | `cryptography` (`AESGCM`) |
| Randomness | OS CSPRNG | Python `secrets` |
| License signatures | Ed25519 | `cryptography` |
| File naming | SHA-256 (truncated to 96 bits) | Python `hashlib` |

The design uses only standard, widely reviewed primitives through maintained libraries.
LedgerStitch implements no custom cryptography.

## 7a. Self-hosted server mode

`ledgerstitch --server` (and the Docker image) runs LedgerStitch for other devices on a
network. The desktop token model (section 2) is meant for `127.0.0.1` only, so server mode
replaces it with a sign-in. The desktop app is unchanged: it never enables any of this.

| Concern | Protection |
|---|---|
| Who may use the server | One admin password, stored only as an Argon2id hash (`argon2-cffi` `PasswordHasher`) in `<data>/server/admin.json`, mode 0600. |
| Claiming a fresh server first | Setting the password through the web needs a one-time setup code that appears only in the server log. It can also be pre-set from a file or with `ledgerstitch admin-password`. |
| Session theft | A 256-bit random session id in an `HttpOnly; SameSite=Strict; Secure` cookie, with the `__Host-` prefix over HTTPS. The server keeps only a SHA-256 of the id, in memory. Sessions end after 8 h idle or 7 days. Signing out and restarting both end sessions. |
| CSRF | Every `/api` and `/auth` call needs the session's own CSRF token in `X-LedgerStitch-Token`. It is injected into the page after sign-in, and cross-site pages can't read it. A cross-origin `Origin` is rejected. `/auth/*` accepts JSON only, so a plain HTML form can't post to it. |
| Password guessing | 5 failed attempts per client and 30 overall per 15 minutes, then HTTP 429 with `Retry-After`. On top of that, Argon2id makes each check slow. |
| Network eavesdropping | Refuses to start without TLS unless `LEDGERSTITCH_ALLOW_HTTP=1`, which is for use behind an HTTPS proxy such as the bundled Caddy. HSTS is sent over HTTPS. |
| Host-header / DNS-rebinding tricks | Optional `LEDGERSTITCH_ALLOWED_HOSTS`. The SameSite=Strict cookie is never sent to another site's name. |
| Data at rest | Unchanged. Signing in does **not** unlock any vault. Each vault still needs its own passphrase, and signing out locks it. |
| Installing software through the web UI | Refused in server mode (HTTP 403). |

Not covered: someone with shell or root access to the server, a malicious reverse proxy,
and a weak admin password. Server mode has one user. Multi-user accounts, SSO and audit
logs belong to the paid editions.

## 7b. First-run setup downloads

The setup wizard can install Ollama and a local model. These are the only outbound requests
the Community core makes, and only with the user's explicit consent: the *Set up local AI*
consent dialog (nothing pre-ticked; **Agree and set up**), which records the consent (time,
model, Ollama address) as `local_ai_consent` in `app.json`. The API refuses a download without
`consent: true` and a recorded consent for that model, and an interrupted download resumes at the
next launch only while that consent is recorded. "Withdraw consent" in Settings → Privacy & AI
stops the download and clears it; "Remove model" deletes the model from Ollama. In server mode
nothing is installed: the model is pulled into the operator's Ollama service, the one URL in
`LEDGERSTITCH_OLLAMA_URL` (server mode only) that local AI may talk to besides loopback. The sign-up link shown in the
Community edition is a plain link (only `?ref=app-community`), opened by the user; nothing is
sent automatically.

- **Allowlisted HTTPS only.** Installer downloads go to `https://ollama.com/download/…`. Every
  redirect hop is checked against a fixed allowlist: `ollama.com`, `github.com`,
  `objects.githubusercontent.com` and `release-assets.githubusercontent.com`. Anything else,
  plain HTTP, a non-default port or credentials in the URL aborts the download.
- **Verified before it runs.** On macOS, the unpacked `Ollama.app` must pass
  `codesign --verify --deep --strict` and be signed with a Developer ID naming Ollama. On
  Windows, `OllamaSetup.exe` needs a valid Authenticode signature from Ollama. If the check
  fails, nothing runs. With Homebrew, `brew install ollama` verifies its own checksums.
- **Linux.** LedgerStitch never runs `curl | sh`. It shows the official command, and the
  user runs it.
- **Model download.** The model is pulled by the local Ollama, through its loopback-only API,
  and only from a short list of recommended models.
- **No user data.** None of these requests carry user data. `app.json` (setup done, chosen
  model) holds no secrets and no financial data.

## 8. Reporting a vulnerability

If you believe you have found a security issue, contact the LedgerStitch team privately and do
not file a public issue. Please include steps to reproduce. Never include real financial data
in a report.
