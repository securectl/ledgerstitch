<p align="center">
  <img src="docs/img/logo.svg" alt="LedgerStitch" width="72"><!-- SCREENSHOT PLACEHOLDER: logo -->
</p>

<h1 align="center">LedgerStitch</h1>
<p align="center"><b>Statements in. CPA-ready books out.</b><br>
Open source. Runs on your computer. Zero network calls.</p>

<p align="center">
  <!-- BADGE PLACEHOLDERS: replace securectl -->
  <a href="LICENSE"><img alt="License: AGPL-3.0" src="https://img.shields.io/badge/license-AGPL--3.0-blue"></a>
  <a href="https://github.com/securectl/ledgerstitch/actions"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/securectl/ledgerstitch/ci.yml?branch=main"></a>
  <a href="https://github.com/securectl/ledgerstitch/releases"><img alt="Release" src="https://img.shields.io/github/v/release/securectl/ledgerstitch"></a>
  <img alt="Platform" src="https://img.shields.io/badge/platform-macOS%20(Apple%20silicon)-lightgrey">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <a href="CONTRIBUTING.md"><img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-brightgreen"></a>
</p>

<p align="center">
  <a href="{{SITE_URL}}/signup?ref=github"><b>→ Get the full product</b></a>: paid add-ons and a hosted
  version extend LedgerStitch through the plugin API.<br>
  <sub>Or keep reading: everything below is free and open source.</sub>
</p>

<!-- These badge images load from shields.io when someone views the README on GitHub. That's a
     website, not the app. The app itself makes no network calls. -->

<p align="center">
  <img src="docs/img/demo.gif" alt="Importing four statements from three banks, balance checks passing, transfers paired, review queue, Excel export" width="820">
  <!-- SCREENSHOT PLACEHOLDER: 30–60 s GIF from ?mock=1 -->
</p>

LedgerStitch takes the pile of bank and card statements a small business (or a bookkeeping
client) hands you, as CSV or text PDF from any mix of banks, and turns it into **one clean
ledger**. It:
- removes duplicates (including the same month exported as both PDF and CSV);
- pairs card payments and transfers between your own accounts, so they don't count as spending;
- nets refunds against the purchases they cancel;
- **checks each PDF statement's balance** (beginning + activity = ending), and shows a ✅/⚠ badge;
- sorts transactions into Business / Personal / Transfer / Income / Review lanes, with **IRS Schedule C** lines;
- exports an **8-tab Excel workbook** for the CPA, CSV, a **QuickBooks Online** bank-upload CSV and a **QuickBooks Desktop IIF**;
- sorts merchants with **local AI** (Ollama, on your computer), set up for you in the background during first run, only with your OK.

It isn't accounting software: there's no invoicing, payroll or general ledger. It's the
cleanup step before a ledger, the step that usually breaks.

## 60-second quickstart

**Mac and Windows (no Python needed)**
1. Download from [Releases](https://github.com/securectl/ledgerstitch/releases):
   - **macOS:** the DMG (Apple silicon or Intel). Drag LedgerStitch to Applications.
   - **Windows:** the Setup.exe. It's a per-user install, so you don't need admin rights.

   Check the SHA-256 published next to the download. Until signing is in place, see
   [INSTALL.md](docs/INSTALL.md#until-the-apps-are-signed-gatekeeper-and-smartscreen) for the
   one-time Gatekeeper/SmartScreen step.
2. Open it. Your browser opens to `http://127.0.0.1:<port>/`. A 3-step setup does the rest:
   - a system check;
   - **local AI**, only if you agree in its consent dialog: Ollama plus GLM-4.7-Flash (19 GB, ~24 GB RAM), downloaded in the background while you continue;
   - your first encrypted vault.

   Then pick **Try with sample data** or **Import my first statement**.
3. Work through the Review queue, then go to **Report → Export**.

**One-line install from source** (macOS/Linux; Windows: `install.ps1`). It needs no system
Python and no sudo, and it's uninstallable:
```bash
git clone https://github.com/securectl/ledgerstitch.git && cd ledgerstitch && sh install.sh   # then: ledgerstitch
sh install.sh --uninstall                                           # keeps ~/LedgerStitch
```

**Self-host (Docker + automatic HTTPS):**
```bash
cd docker && docker compose up -d                        # try it: https://localhost
docker compose logs ledgerstitch | grep "Setup code"     # first run: set the admin password
```
Open `https://localhost` (your browser warns once about Caddy's local certificate). On a server
with a real domain pointed at it, use `LEDGERSTITCH_DOMAIN=books.yourdomain.com docker compose up -d`
instead, and Caddy gets a Let's Encrypt certificate. Server mode adds an admin sign-in. Your vaults stay encrypted with their own passphrases.
Local AI is included as a private `ollama` service: the 19 GB model downloads only after you agree in the setup wizard (give Docker ~24 GB RAM; skip it with `docker compose up -d ledgerstitch caddy`).
See [docs/INSTALL.md](docs/INSTALL.md).

**Update an existing install:** `ledgerstitch update` checks for a newer release, and
`sh scripts/update.sh` applies it, rebuilding or reinstalling only what changed
([Updating](docs/INSTALL.md#6-updating)).

**From source (Python 3.11+)**
```bash
git clone https://github.com/securectl/ledgerstitch.git && cd ledgerstitch
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ledgerstitch                 # opens http://127.0.0.1:<port>/
.venv/bin/ledgerstitch --help          # CLI: import / export / vaults, no browser needed
.venv/bin/python -m pytest -q          # full test suite
```
Or use the install script, which sets up the same virtual environment for you: `sh install.sh`
(macOS/Linux) or `powershell -ExecutionPolicy Bypass -File install.ps1` (Windows). Read it before
you run it.

**Self-host with Docker**

The [`docker/`](docker/) folder has a Dockerfile and a compose file for running LedgerStitch on
your own server or NAS; the setup steps are in that folder. Vaults are stored
on a volume you control. Put the container behind your own authentication, and don't expose it
to the internet without it.
Want to try it without your own statements? Open the app URL with `?mock=1` for a demo with sample data.

<p align="center">
  <img src="docs/img/import-balance-check.png" alt="Import screen with per-statement balance-check badges" width="400">
  <img src="docs/img/report-schedule-c.png" alt="Report screen with Schedule C totals" width="400"><br>
  <img src="docs/img/review-queue.png" alt="Keyboard-driven review queue" width="400">
  <img src="docs/img/ai-preview.png" alt="Preview what AI sees: redacted merchant names only" width="400">
  <!-- SCREENSHOT PLACEHOLDERS: four PNGs from ?mock=1 -->
</p>

## Supported inputs

- **CSV:** built-in profiles for Chase (card and checking), Citi, Capital One (both export styles), American Express, Bank of America (card and checking), Wells Fargo, Discover, US Bank, Apple Card and Gemini. Any other bank's CSV works through a generic detector or a one-time column mapping.
- **PDF:** text-based statements, parsed and balance-checked. **Scanned/image-only statements aren't supported yet**, so use your bank's CSV export.
- **Pay stubs (PDF or text):** Rippling-style, ADP-style and generic layouts. `ledgerstitch paystub stub.pdf` projects the year, estimates your W-2 (labelled as an estimate) and flags payroll "money found" signals: excess Social Security across employers, 401(k) room, HSA/FSA conflicts. It reads stubs in memory, doesn't store them and computes no income tax.
- **1099s and positions:** composite 1099 PDFs (Fidelity-, Schwab-, Robinhood-style), 1099-B transaction CSVs and positions CSVs. `ledgerstitch investments 1099.pdf positions.csv` adds up dividends, interest and gains across brokers, shows where each total goes on the return, summarizes your holdings and allocation, and flags investment signals such as unrealized losses, missing cost basis and foreign tax credits. Nothing is stored, and no tax is computed.

Missing your bank? In the app, use **"Report this file's format"**. It exports a masked structure
sample with no amounts or descriptions. Then open a [Bank format issue](https://github.com/securectl/ledgerstitch/issues/new/choose).

## Editions

| | **Community** (this repo) | **Pro / Hosted** |
|---|---|---|
| Import (CSV, PDF, OFX/QFX/QBO, QIF, XLSX, ISO 20022), dedupe, transfers, balance checks | ✅ | ✅ |
| Review queue, ledger, manual and bulk edits, rules | ✅ | ✅ |
| Excel, CSV, QuickBooks CSV and IIF exports, Schedule C report | ✅ | ✅ |
| Local AI categorization (Ollama, on your computer) | ✅ | ✅ |
| Encrypted vaults, backups, recovery key, data export and delete | ✅ | ✅ |
| Self-hosted server mode (Docker) | ✅ | ✅ |
| Pay stub import, year-end projection, estimated W-2, payroll signals | ✅ | ✅ |
| 1099 import, holdings snapshot and allocation, investment signals | ✅ | ✅ |
| Lot-level gains, cross-account wash sales, harvesting plan, rebalancing | – | ✅ (Plus) |
| More features, as paid add-ons | – | [See the website]({{SITE_URL}}) |
| License | AGPL-3.0, **free, no usage limits** | Commercial |
| | [Download](https://github.com/securectl/ledgerstitch/releases) | [**Get the full product →**]({{SITE_URL}}/signup?ref=github) |

Paid add-ons extend LedgerStitch through the plugin API ([docs/PLUGINS.md](docs/PLUGINS.md)).
They are separate packages, not part of this repository. When an add-on is installed but not
licensed, its screens show a quiet locked card with a link to the website; without add-ons you
see only Community.

### What's in Community: the whole cleanup job, uncapped
Import, dedupe, transfer and refund matching, statement balance checks, rules and built-in
categorization, the Review queue, the ledger with manual and bulk edits, Schedule C mapping,
Excel / CSV / QuickBooks (CSV and IIF) exports, encrypted vaults (unlimited clients), backups and
a recovery key, self-hosting with Docker, and **local AI through Ollama** on your own machine.
There are no limits on accounts, transactions, vaults or exports, and there never will be. See
[GOVERNANCE.md](GOVERNANCE.md#3-promises-to-the-community-the-open-core-charter).

You don't need a paid add-on to do the whole job. And the plugin API is public: you can write
your own add-on. That's fine with us.

## Privacy architecture

```
 your statements ──► parsed in memory (never written to disk)
                          │
                          ▼
         encrypted vault per client (AES-256-GCM, key from passphrase via Argon2id)
                          │
     UI on 127.0.0.1 only · random per-launch token · Host/Origin checks · strict CSP
                          │
          ┌── Community: stops here. Zero network calls. Optional local Ollama on localhost.
          └── AI add-ons (opt-in): core builds a redacted list of merchant names ──► the add-on
                            (no amounts, dates, balances, account or card numbers)
```

- **Local only.** The server binds to `127.0.0.1`, never `0.0.0.0`. There's no account, no
  telemetry and no update check. The desktop app has no LedgerStitch cloud.
- **Encrypted at rest.** Transactions, rules, settings and any secrets live inside the vault.
  Only a display label you choose is stored unencrypted.
- **Redaction before AI, enforced in the core.** AI plugins receive only the redacted
  descriptor list the core builds. Names after Zelle, Venmo, Cash App, PayPal, card processors
  and bank transfers are replaced with `<PERSON>`. **Redaction is pattern-based.** Preview
  exactly what the AI sees (Settings → *Preview what AI sees*), or stay offline.
- **Licenses are checked offline** (Ed25519).
- **Exports are plaintext by design,** so your CPA can open them. Store them carefully.
- **Limits** (what we don't protect against, such as a compromised computer or a weak
  passphrase): [docs/SECURITY.md](docs/SECURITY.md).

**Verify it yourself.** Run Community under Little Snitch, LuLu or `nettop` and watch for
connections; you'll see none. Read [`ledgerstitch/security/redact.py`](ledgerstitch/security/redact.py)
and [`ledgerstitch/security/vault.py`](ledgerstitch/security/vault.py). Found a hole? See [SECURITY.md](SECURITY.md).

## Architecture (short)

```
web/static (vanilla JS, strict CSP) ──JSON over 127.0.0.1──► api.py (FastAPI)
  store.py   workspace: accounts, transactions, rules, settings
  ingest/    detect → csv_parser / pdf_parser → normalize → reconcile
  ai/        rules → heuristics → local AI (Ollama) or an AI add-on, on redacted descriptors
  receipts/  stored receipts, linked to transactions
  reports/   summary, Excel, CSV, QuickBooks (QBO CSV / IIF)
  security/  vault, redact, license
  plugins.py the plugin API: hooks, UI slots, /addons/<ns>/ static files
```
Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Writing an add-on: [docs/PLUGINS.md](docs/PLUGINS.md).

## Contributing

Bank-format profiles are the most valuable contribution, followed by docs, tests and bug
reports. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Never** attach real statements or vaults to an issue.
- Code contributions need a [CLA](CLA.md), because the company also sells commercial editions.
  You keep your copyright, and the core stays AGPL ([GOVERNANCE.md](GOVERNANCE.md)).
- Security issues go through [SECURITY.md](SECURITY.md), not public issues.

## Roadmap

The public roadmap is at [GitHub Projects](https://github.com/securectl/ledgerstitch/projects). Every item is labelled with
the edition it lands in. Up next for Community: more bank profiles, scanned-statement support,
and a signed Windows build.

## License

The LedgerStitch Community edition is licensed under the **GNU Affero General Public License
v3.0 only** ([LICENSE](LICENSE)). Paid add-ons and the hosted version are separate commercial
products. "LedgerStitch" is a trademark. If you distribute a fork, please use a
different name ([GOVERNANCE.md §7](GOVERNANCE.md#7-trademarks)).

*LedgerStitch prepares figures for review by a tax professional. It is not tax, legal or accounting advice.*
