# LedgerStitch: developer notes

Every bank and card statement in one clean, categorized, CPA-ready expense report —
privately, on your own computer.

Drop in CSV or PDF statements from any bank. LedgerStitch merges them into one
de-duplicated ledger, removes card payments and transfers between your own accounts,
nets refunds, sorts every transaction into a **lane** (Business / Personal / Transfer /
Income / Review) with a category and IRS Schedule C line, and exports an Excel
workbook, CSV, and QuickBooks files.

## Install

Requires Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"         # Community core + pytest; no network at runtime
```

## Run

```bash
.venv/bin/ledgerstitch                  # or: .venv/bin/python -m ledgerstitch
# or, from a source checkout:
scripts/run_dev.sh
```

LedgerStitch picks a free port, binds to **127.0.0.1 only**, and opens your browser at
`http://127.0.0.1:<port>/`. Options:

| flag | meaning |
|---|---|
| `--port 8765` | use a fixed port |
| `--data-dir ~/LedgerStitch` | where vaults live (default `~/LedgerStitch/vaults/<name>.vault`) |
| `--no-browser` | don't open a browser window |

Command-line extras (no browser needed; the passphrase is prompted, or read from
`LEDGERSTITCH_PASSPHRASE`):

```bash
ledgerstitch import "Acme Studio" ~/Downloads/chase.csv ~/Downloads/amex.pdf [--create] [--account-label "Chase Ink"]
ledgerstitch export "Acme Studio" --format xlsx --year 2025 --out acme-2025.xlsx   # xlsx | csv | qbo | iif
ledgerstitch vaults
```

Tests: `.venv/bin/python -m pytest`

## Architecture overview

```
browser (ledgerstitch/web/static, vanilla JS, strict CSP)
   │  JSON over http://127.0.0.1:<port>/api  + X-LedgerStitch-Token header
   ▼
api.py        FastAPI app: auth, host/origin checks, security headers, in-memory uploads,
              one unlocked vault at a time, auto-lock after 30 idle minutes
store.py      Workspace: accounts, transactions, rules, settings, import log, custom categories
   ├── ingest/    detect → csv_parser / pdf_parser → normalize → reconcile (dupes, transfers, refunds)
   ├── ai/        rules → heuristics → optional AI on redacted descriptors (local Ollama, or an add-on)
   ├── reports/   summary, Excel, CSV, QuickBooks (QBO CSV / IIF)
   ├── security/  vault (AES-256-GCM + Argon2id), redact, license (offline Ed25519)
   └── plugins.py the plugin API for add-ons (docs/PLUGINS.md)
```

The import pipeline: detect the format (or use a manual column mapping) → parse in
memory → create/locate the account → merge transactions by deterministic id (re-importing
the same file changes nothing) → normalize merchants → reconcile across *all* transactions →
categorize only new/unreviewed rows → save the encrypted vault. There are no usage caps.
Correcting a transaction with "remember for this merchant" learns a rule and re-applies it
to that merchant's other unapproved rows.

Full contract: `docs/ARCHITECTURE.md`. Plugin API for add-ons: `docs/PLUGINS.md`.
Demo with synthetic data: open the app with `?mock=1`.

## Privacy summary

- **Local only.** The server listens on `127.0.0.1`, never `0.0.0.0`. No LedgerStitch servers,
  no accounts, no telemetry.
- **Encrypted at rest.** Each business/client is a vault encrypted with AES-256-GCM under a
  key derived from your passphrase (Argon2id). Transactions, rules, settings and any secrets
  are all stored inside the vault.
- **Raw files never touch disk.** Uploaded statements are parsed in memory (LedgerStitch uses
  its own multipart reader so files are not spooled to temp files) and are never saved; the
  import log keeps only file name, date, counts and the detected format. Limit: 25 MB per file.
- **AI is optional and sees only redacted merchant descriptors** (e.g. `NETFLIX.COM`,
  `ZELLE TO <PERSON>`) — never amounts, dates, balances, account numbers, names or addresses.
  Settings → "Preview what AI sees" shows the exact payload (`GET /api/ai/preview`).
  Offline mode (built-in rules, or a local Ollama model) sends nothing anywhere.
- **Secrets are write-only.** For a settings key registered as a secret, `GET /api/settings`
  reports `has_<key>: true/false` and never returns the value.
- **Hardening.** Every API call needs a random per-launch token injected into the page;
  requests with a foreign `Host` (DNS rebinding) or cross-site `Origin` are rejected;
  strict Content-Security-Policy, `nosniff`, `no-referrer`, `frame-ancestors 'none'`,
  `Cache-Control: no-store` on the API. Only one vault is unlocked at a time, and it locks
  itself after 30 idle minutes (configurable in Settings).
- **Exports are plaintext.** Excel/CSV/QuickBooks files exist to be handed to your CPA or
  bookkeeping software, so they are *not* encrypted. LedgerStitch builds them in a temporary
  folder that is deleted immediately after download; where you save the downloaded file is
  your choice — keep it somewhere safe.

*LedgerStitch prepares figures for review by a tax professional; it is not tax advice.*
