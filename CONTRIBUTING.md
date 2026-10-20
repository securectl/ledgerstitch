# Contributing to LedgerStitch

Thanks for helping! LedgerStitch turns bank and card statements into clean, CPA-ready books on the
user's own computer. Contributions are very welcome: bank-format profiles, bug reports, docs,
tests and code.

> Rename to `CONTRIBUTING.md` at the repo root at publication. Placeholders: `https://github.com/securectl/ledgerstitch`,
> `{{SECURITY_EMAIL}}`, `{{CONDUCT_EMAIL}}`, `{{CLA_URL}}`.

**Before you start:**
- Read the [Code of Conduct](CODE_OF_CONDUCT.md).
- Security problems go through [SECURITY.md](SECURITY.md), **never** a public issue.
- Code and docs contributions need a signed [CLA](CLA.md) (see §7 for why).

---

## 1. The golden rule: never put real financial data in this repo

Don't attach real statements, vaults, receipts, screenshots with real data, or API keys to
issues, pull requests or tests. That includes "redacted by hand" copies, because it's easy to
miss an account number in a PDF's metadata.
- For a bank format we don't support, use the in-app **"Report this file's format"** action. It
  exports a *structure sample* (column headers and a masked layout, no amounts or descriptions).
  Or write a synthetic fixture by hand.
- Test fixtures in `tests/fixtures/` must be **synthetic**. Use made-up merchants, fake names
  (`Jane Doe`) and amounts, and card numbers that can't be real (`4111 1111 1111 1111`, `**** 4012`).

PRs that contain real-looking financial data will be closed, and the commit history scrubbed.

## 2. Ways to contribute

| Type | How |
|---|---|
| **New bank / card format** | The most valuable contribution. Open an issue with the "Bank format" template and attach the structure sample. Better still, add a profile in `ledgerstitch/ingest/detect.py` / `csv_parser.py` / `pdf_parser.py`, plus a synthetic fixture and tests |
| **Bug report** | "Bug" template. Include the version, OS, steps, and expected vs actual. No real data |
| **Docs** | Typos, clarity, the user guide (`docs/USER_GUIDE.md`), translations |
| **Code** | Pick an issue labelled `good first issue` or `help wanted`, and comment that you're taking it. For anything larger than a bug fix, open an issue first so we can agree on the design and which edition it belongs to (see GOVERNANCE §4) |
| **Ideas** | GitHub Discussions → Ideas |

## 3. Development setup (about 5 minutes)

Requirements: **Python 3.11+** (CI tests 3.11 through the latest stable release), git, macOS or
Linux. Windows development works but is less tested.

```bash
git clone https://github.com/securectl/ledgerstitch.git
cd ledgerstitch
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"           # Community core + pytest. No AI SDKs, no network.
brew install betterleaks pre-commit          # or betterleaks from its releases page + pipx install pre-commit
pre-commit install                           # every commit: secret scan, private keys, statements, large files
.venv/bin/python -m pytest -q               # run the full suite; it should be all green
scripts/run_dev.sh                           # start the app on 127.0.0.1 and open your browser
```

- **UI work without real data:** open the app with `?mock=1` (served by `web/static/mock.js`) to
  get a demo backend with sample data. Screenshots for PRs should come from mock mode only.
- **Desktop builds:** see `docs/RELEASE.md` (PyInstaller, a dedicated `.venv-release`).
- **Add-ons:** paid add-ons are separate packages and not in this repository. The core must
  build, run and pass every test **without** them. CI checks this. The plugin API they use is
  public: [docs/PLUGINS.md](docs/PLUGINS.md).

### Project layout (core)
```
ledgerstitch/
  ingest/      detect → csv_parser / pdf_parser → normalize → reconcile
  ai/          rules + heuristics + local AI (Ollama), and the plugin hook for AI add-ons
  receipts/    stored receipts, linked to transactions
  reports/     summary, Excel, CSV, QuickBooks (QBO CSV / IIF)
  security/    vault (AES-256-GCM + Argon2id), redact, license
  store.py api.py cli.py desktop.py web/static/
docs/          ARCHITECTURE, SECURITY, PLUGINS, USER_GUIDE, ...
tests/         pytest suite + synthetic fixtures
```

## 4. Tests

- Run `.venv/bin/python -m pytest -q` before you push. The suite takes a few seconds.
- **Every bug fix needs a test that fails without the fix.**
- **Every new bank profile needs:** a synthetic fixture in `tests/fixtures/`, a detection test
  (`test_detect.py`), and a parse test (`test_csv_parser.py` / `test_pdf_parser.py`) that checks
  dates, signs/directions and amounts. For PDFs, also test the balance check:
  beginning + activity = ending.
- **Privacy-relevant changes** (anything in `security/redact.py`, AI payload building, or
  exports) need tests that check *what is sent or written*. They must not only check that "it works".
- **No network in tests.** Use the fakes in `tests/backend_fakes.py`. A test that reaches the
  internet is a bug.

## 5. Style

- **Python:** PEP 8, type hints on public functions, `from __future__ import annotations`, and
  small pure functions where possible. Long-running work (parsing, categorization) must stay
  callable as plain functions over bytes or lists, so it can later run on a job queue. Format with
  `ruff format` and lint with `ruff check`. *(Maintainers: add a `[tool.ruff]` section to
  `pyproject.toml` and a CI job before publishing. Until then, match the surrounding code.)*
- **No global process state outside the entry points.** Only `api.py`, `cli.py` and `desktop.py`
  may read environment variables, the home directory, and the like. Pass settings in.
- **Frontend:** vanilla JavaScript modules in `ledgerstitch/web/static/`. Keep **no build step,
  no frameworks and no third-party scripts, fonts or CDNs**. The app runs under a strict Content
  Security Policy (`script-src 'self'`), so inline scripts and `eval` won't work. Keep it
  accessible: labels, keyboard navigation, visible focus, and both color schemes.
- **Copy in the app:** follow the literal-truth rule. Every privacy or capability statement must
  be literally true for the edition that shows it. When in doubt, say less.
- **Dependencies:** new runtime dependencies need a reason in the PR and a license compatible with
  AGPL-3.0 (MIT, BSD, Apache-2.0, ISC, LGPL, MPL-2.0 and similar). No dependency may make a network
  call on import or start-up.
- **Commits:** clear, imperative messages ("Add Ally Bank CSV profile"). Keep PRs small and
  focused. Squash-merge is the default.

## 6. Hard rules for the Community core (CI-enforced where possible)

1. **Zero network calls by default.** The Community edition must not open any outbound connection
   unless the user explicitly starts one. The only exception is a local model server the user
   configured on `localhost`. That means no update checks, analytics, crash reporting or font
   loading.
2. **Redaction before AI, in the core.** AI plugins receive only the redacted descriptor list the
   core builds (`list[str]` plus the category list). They never receive `Transaction` objects,
   amounts, dates or raw descriptions. A test enforces this.
3. **The core never imports an add-on.** Add-ons register themselves through the
   `ledgerstitch.plugins` entry point. The core stays neutral: it offers generic hooks and UI
   slots, and never describes what a paid feature does.
4. **No usage caps in Community** (see GOVERNANCE §3). PRs that add limits to the core will be declined.
5. **Uploaded statements and receipts are never written to disk in plaintext.**

## 7. Licensing of contributions: why we use a CLA (and not only a DCO)

The core is licensed **AGPL-3.0-only**. The company behind LedgerStitch also sells proprietary
editions (paid add-ons and a hosted version) built on the same core. To do that
legally with community code, it needs the right to distribute your contribution under terms
other than the AGPL as well. That is called *dual licensing*.

- A **DCO** (Developer Certificate of Origin, the `Signed-off-by:` line) only certifies that you
  have the right to submit the code under the project's license. It does **not** give the company
  permission to also distribute it commercially. So a DCO alone doesn't work for a dual-licensed
  project.
- A **CLA** (Contributor License Agreement) does. Ours is adapted from the Apache Software
  Foundation's Individual CLA. **You keep copyright** of your contribution. You grant the company
  a broad license to use it, *including* under non-AGPL terms, plus a patent license. See [CLA.md](CLA.md).
- **What the CLA can't do:** it can't take away the AGPL version. Everything merged here stays
  available to everyone under AGPL-3.0. GOVERNANCE §3 lists our other promises.
- **How to sign:** the CLA Assistant bot comments on your first PR. Reply with the sentence it
  asks for, and that's it. It covers all your future contributions. If you contribute as part of
  your job, your employer may need to sign a Corporate CLA. Email `{{CLA_EMAIL}}`.
- **Small fixes:** typo fixes and one-line docs changes still need the CLA. Sorry, but the
  legal reasoning is the same.
- We also ask for `git commit -s` (DCO sign-off) as a lightweight provenance record. It's
  optional, but appreciated.

If you don't want to sign a CLA, that's completely fine. Please open an issue describing the bug
or idea instead, and a maintainer can implement it independently.

## 8. Pull request checklist

- [ ] Linked issue, or a clear description of the problem
- [ ] Tests added or updated; `pytest -q` passes locally
- [ ] No real financial data, keys or personal information anywhere (including in screenshots)
- [ ] No new outbound network calls in the core
- [ ] Docs updated (`docs/USER_GUIDE.md`, `docs/SUPPORTED_BANKS.md`, etc.) if behavior changed
- [ ] User-facing copy checked against the literal-truth rule
- [ ] CLA signed (the bot will tell you)

## 8a. Releases

Releases follow [Semantic Versioning](https://semver.org/). Notable changes go in
[CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`; a `vX.Y.Z` tag publishes that section as the
GitHub release notes, with checksums and build provenance. Prefer Conventional Commit PR titles
(`fix: …`, `feat: …`, `docs: …`) so changes sort into the right changelog section.

## 9. Review and response times

This is a small project with a solo maintainer. Realistic targets: first response on issues and
PRs within **5 business days**, bank-format issues within **3**. Security reports follow SECURITY.md
(2 business days). Paying customers may get priority *support*, but community PRs are reviewed on merit
and not on who paid.

## 10. Recognition

Every merged contributor goes into release notes and `CONTRIBUTORS`. Bank-format contributors are
credited in `docs/SUPPORTED_BANKS.md` next to the profile they added.
