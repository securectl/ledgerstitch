# Changelog

All notable changes to LedgerStitch Community are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html): **MAJOR** for breaking changes to
vault format, file formats or the plugin API; **MINOR** for new features; **PATCH** for fixes.

Each release's section is used as its GitHub release notes, so write for users, not for
maintainers. Security fixes are listed under **Security** and announced in the advisory, too.

## [Unreleased]

### Added
- **Pay stub import → year-end projection → estimated W-2.** `ledgerstitch paystub FILE...
  [--owner you|spouse] [--spouse FILE] [--final] [--out w2.xlsx]` and `POST /api/paystubs`
  (`/api/paystubs/export/{csv|xlsx}`) read Rippling-style, ADP-style (including space-separated
  amounts such as `7 916 67`) and generic pay stubs from text-based PDFs or text. The tool
  classifies deductions (401(k)/403(b), Roth, HSA, health FSA, dependent care, Sec. 125,
  post-tax, reimbursements), checks pre-tax status against the Social Security and Medicare
  arithmetic, projects to December 31 by pay frequency, and estimates W-2 boxes 1-6, 10, 12, 13
  and 14, labelled "ESTIMATE — not issued by employer".
- **Free payroll signals** for a household (you and a spouse, several employers): excess Social
  Security withheld, 402(g) deferral room or excess, HSA with a general-purpose health FSA,
  HSA / FSA / dependent care over the limit, dependent care with a spouse who has no earned
  income, and Additional Medicare Tax not withheld. Each signal has a dollar amount and a
  citation. No income tax is computed (docs/TAX_NOTES.md section 8).

### Security
- `security.redact.mask_identifiers` masks SSNs, account, routing and employee numbers (last 4
  kept) in documents shown back to the user. AI descriptor redaction now also removes SSNs.

## [1.2.0] - 2026-10-19

This repository's history starts at 1.2.0. Earlier release notes are kept below for reference.

### Changed
- **The plugin API is now generic** (contract version 2, docs/PLUGINS.md). New hooks:
  `license_plans` (add-ons declare which license plans unlock their tiers; the core knows only
  `pro` and `enterprise`), `provide` / `registry().provided` (named services, including the
  desktop `selftest`), `static_dir` (an add-on's browser files at `/addons/<namespace>/`, with the
  app's sign-in, CSP and cache rules), and `ui_screen`, `ui_card` and `ui_settings` (add-on screens,
  cards and settings sections, listed in `/api/status` under `ui`).
- Locked add-on features show one neutral locked card (title, **Get the full product**,
  **Have a key?**). The demo (`?mock=1`) of a plain public checkout shows only free features.
- Paid features now live entirely in separately distributed add-ons. Paid add-ons: internal
  changes (see the website).

### Removed
- Three single-purpose plugin hooks that served only paid add-ons. Add-ons that used them now
  use `provide` and the UI registrations.

## [1.1.1] - 2026-10-19

### Fixed
- **Local AI in Docker failed with `signal: killed`.** Ollama loaded GLM-4.7-Flash with its full
  198K-token context, far more memory than Docker Desktop allows by default. The `ollama` service
  now caps the context at 8K (plenty for merchant names), with flash attention and an 8-bit
  KV cache, one model at a time.

### Added
- **Use Ollama running on the host** (recommended on a Mac, where Docker can't use the GPU):
  `LEDGERSTITCH_OLLAMA_URL=http://host.docker.internal:11434 docker compose up -d ledgerstitch caddy`.
  `host.docker.internal` also works on Linux now (`extra_hosts`).

## [1.1.0] - 2026-10-19

### Added
- **Local AI in Docker.** `docker/docker-compose.yml` now includes a private `ollama` service
  (pinned image, `ollama-models` volume, never published to the host). The setup wizard shows the
  Local AI step on a self-hosted server too, with status chips (Ollama reachable, model installed)
  and the one-line fix `docker compose up -d ollama` when the service is down. Skip it with
  `docker compose up -d ledgerstitch caddy`.
- **An explicit consent step for local AI.** *Set up local AI* opens a short dialog: what is
  downloaded (19 GB), where it runs, what it sees (merchant names only), the disk and memory it
  needs next to what your machine has, and that you can remove it later. Nothing downloads until
  you click **Agree and set up**; with too little disk or memory it warns and needs a second click.
- **Settings → Privacy & AI → Local AI:** status, model and size, download progress,
  **Remove model** and **Withdraw consent**.

### Changed
- **Local AI uses one model: GLM-4.7-Flash** (`glm-4.7-flash:latest`, 19 GB, about 24 GB of RAM)
  instead of Qwen 3.5 2B. There's no model picker any more. Categorization runs it with thinking
  turned off. See docs/AI.md for why and for the trade-offs.
- The local AI box in setup is no longer pre-ticked: the consent dialog replaces it, on the
  desktop app as well as on a server.
- In server mode, local AI may talk to exactly the Ollama address in `LEDGERSTITCH_OLLAMA_URL`
  (set by the operator). Addresses typed into Settings are still limited to this computer.

## [1.0.3] - 2026-10-19

### Fixed
- **Self-hosted server stuck on "Starting LedgerStitch…" after signing in.** The new layout
  drew the sidebar later than the server-mode "Sign out" button expected, so the app stopped
  during startup. Sign out now sits in the sidebar next to Lock.
- **The setup code changed every time the server restarted**, so a code copied from the log
  stopped working after `docker compose up` or a reboot ("That setup code isn't right"). It now
  stays the same until the admin password is set, ignores dashes, spaces and case, and the
  setup page shows the command that prints it.
- **Fonts didn't load on the sign-in page** of a self-hosted server.
- After an upgrade, browsers could keep running the previous version's scripts. App files are
  now revalidated on every load.

## [1.0.2] - 2026-10-19

### Fixed
- **LedgerStitch didn't start on Python 3.11**, the minimum supported version. One line used
  f-string syntax that only Python 3.12 accepts, so the app failed with a `SyntaxError` at
  startup on 3.11 (the desktop app bundles its own Python and wasn't affected).
- CI: the SBOM job used an option the CycloneDX tool doesn't have, so no SBOM was produced.

### Changed
- A pre-commit hook now compiles every Python file with 3.11, so 3.12-only syntax can't slip in
  again.
- Failing tests in CI are listed as annotations on the run page, readable without signing in.

## [1.0.1] - 2026-10-19

### Fixed
- **Docker self-host didn't load.** The quick start used `ledger.example.com`, a name that can
  never get a certificate, so Caddy kept retrying and `https://localhost` didn't answer.
  `docker compose up -d` now serves `https://localhost` out of the box; set
  `LEDGERSTITCH_DOMAIN` only for a real domain pointed at your server.
- **Misleading "pull access denied" warning** on `docker compose up`. The image is built from
  your checkout and is no longer looked up on Docker Hub (`pull_policy: build`).
- **Setup check listed a paid feature in the free edition**, where it isn't available.

### Security
- Secret scanning moves to **Betterleaks 1.9.0** (pinned and checksum-verified in CI), with a
  reviewed allowlist of exact non-secret lines in `.betterleaks.toml`. The full history scans
  clean.
- **Pre-commit hooks** (`.pre-commit-config.yaml`): secret scan of staged changes, private-key
  detection, large-file check, and a block on statements, vaults, backups and key files outside
  `tests/fixtures/`. Run `pre-commit install` once per clone.

## [1.0.0] - 2026-10-19

First public release of the open-source Community edition.

### Added
- Import CSV, PDF, OFX/QFX, QIF and Excel statements from 115 bank and card layouts, with
  format auto-learning; Review queue; ledger with rules and bulk edits.
- Cross-account cleanup: duplicates, card payments, transfers and refunds.
- Excel, CSV and QuickBooks (CSV and IIF) exports; Schedule C report.
- Encrypted local vaults (Argon2id + AES-256-GCM), optional recovery key, encrypted backup and
  restore; download, purge or delete all your data.
- Local AI categorization with Ollama; first-run setup installs it and downloads a small model
  in the background, only with your consent.
- First-run setup wizard, one-line installers (macOS/Linux/Windows), Docker self-host with an
  admin sign-in and HTTPS.

### Fixed (during the pre-release beta)
- Robinhood Gold Card CSV and PDF statements imported with wrong rows and totals.
- Citi-style "Standard Purchases" sections were skipped in PDF card statements.
- Chase-style card summaries weren't read, so correct statements showed "doesn't match".
- Checks in a PDF "checks paid" section weren't matched with the same checks in CSV files.
- Refunds were paired with the latest purchase from a merchant instead of the matching one.
- Paid add-ons: internal fixes (see the website).

[1.2.0]: https://github.com/securectl/ledgerstitch/releases/tag/v1.2.0
