# LedgerStitch — Product Spec & Architecture Contract (Community core)

This is the source of truth for the Community core. Add-ons extend it only through the plugin
API in docs/PLUGINS.md, and are not described here. **Do not change the public
signatures below without updating this file.** Shared types live in
`ledgerstitch/models.py` and `ledgerstitch/taxonomy.py` (already written — read them first).

## 1. Product in one paragraph

LedgerStitch is a privacy-first, local app for freelancers, sole proprietors,
small-business owners and the bookkeepers who serve them. Drop in bank and card
statements (CSV or PDF, any bank). LedgerStitch merges them into one clean,
de-duplicated ledger, removes card payments and internal transfers, nets
refunds, sorts every transaction into a **lane** (Business / Personal /
Transfer / Income / Review), assigns a **category** and an IRS **Schedule C
line**, and exports a CPA-ready Excel workbook, CSV, and QuickBooks files.

**Privacy promise (marketing-critical, must be literally true):**
1. Runs on the user's machine (`127.0.0.1`). No LedgerStitch servers, no account, no telemetry.
2. Everything saved to disk is encrypted with AES-256-GCM using a key derived
   from the user's passphrase (Argon2id). Raw uploaded files are never written to disk in plaintext.
3. AI is optional. When on, the AI receives **only redacted merchant
   descriptors** (e.g. `"NETFLIX.COM"`, `"ZELLE TO <PERSON>"`) — never amounts,
   dates, balances, account/card numbers, names, addresses or phone numbers. The AI's only job is to
   categorize descriptors and propose categories/lanes. Users can preview the exact payload before it's sent.
4. A fully offline mode (built-in rules + local Ollama model) exists. It is the Community default.

## 2. Personas
- **Sam, sole proprietor** (Schedule C filer, 3–8 accounts, mixes personal & business). Wants: a done-in-an-hour year-end package for the CPA.
- **Bree, freelance bookkeeper** (15–60 clients, gets messy statement exports). Wants: one tool to turn any bank file into QuickBooks-ready data fast; one vault per client.
- **Carl, CPA** (receives the output). Wants: Schedule C totals, transaction-level audit trail, and to see *why* each item was categorized.

## 3. Package layout & ownership

```
ledgerstitch/
  models.py, taxonomy.py          # shared contract (tech lead)
  security/vault.py               # Security engineer
  security/redact.py              # Security engineer
  security/license.py             # Security engineer
  plugins.py, editions.py         # tech lead (docs/PLUGINS.md)
  ingest/detect.py                # Ingestion engineer
  ingest/csv_parser.py            # Ingestion engineer
  ingest/pdf_parser.py            # Ingestion engineer
  ingest/normalize.py             # Ingestion engineer
  ingest/reconcile.py             # Ingestion engineer
  ai/rules.py                     # AI engineer
  ai/local_ollama.py              # AI engineer
  ai/categorizer.py               # AI engineer
  reports/summary.py              # Reporting engineer
  reports/excel.py                # Reporting engineer
  reports/csv_export.py           # Reporting engineer
  reports/quickbooks.py           # Reporting engineer
  store.py                        # Backend engineer
  api.py                          # Backend engineer
  cli.py, __main__.py             # Backend engineer
  web/static/{index.html,app.js,styles.css}   # Frontend engineer
tests/                            # each owner writes tests for their modules
```

Python 3.12, deps: duckdb, cryptography, argon2-cffi, fastapi, uvicorn, python-multipart,
pdfplumber, openpyxl, pydantic, httpx, pytest.
Use the venv at `ledgerstitch/.venv` (`.venv/bin/python`, `.venv/bin/pytest`).
Never print or log raw descriptions/amounts at INFO level.

## 4. Module contracts

### 4.1 security/vault.py
```python
class VaultError(Exception): ...
class WrongPassphrase(VaultError): ...

class Vault:
    """A directory holding an encrypted workspace (one per business/client)."""
    @classmethod
    def create(cls, path: Path, passphrase: str, name: str) -> "Vault": ...
    @classmethod
    def open(cls, path: Path, passphrase: str) -> "Vault": ...   # raises WrongPassphrase
    @staticmethod
    def exists(path: Path) -> bool: ...
    @property
    def name(self) -> str: ...
    def put(self, key: str, data: bytes) -> None: ...   # encrypt + atomic write
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> None: ...
    def keys(self) -> list[str]: ...
    def put_json(self, key: str, obj) -> None: ...
    def get_json(self, key: str, default=None): ...
    def change_passphrase(self, old: str, new: str) -> None: ...  # re-wraps DEK only
    def set_recovery_code(self, passphrase: str) -> str: ...      # (re)create recovery wrap, returns code once
    def remove_recovery_code(self, passphrase: str) -> None: ...
    @classmethod
    def recover(cls, path: Path, code: str, new_passphrase: str) -> "Vault": ...  # raises WrongRecoveryCode
    def lock(self) -> None: ...   # wipe key from memory; further calls raise VaultError
```
Design: `vault.json` header {version, name, kdf: argon2id params+salt, wrapped_dek (AES-GCM with KEK), created,
optional recovery: {kdf, wrapped_dek, created}}.
`security/backup.py`: `create_backup(vault, out)` zips the encrypted files + manifest (`.lsbackup`);
`restore_backup(src, new_dir)` validates names/sizes/checksums/header and never overwrites.
Each key stored as a file `<sha256(key)[:24]>.bin` = nonce(12) || ciphertext, AAD = key name. Key names are not visible on disk.

### 4.2 security/redact.py
```python
@dataclass
class Redacted:
    text: str            # safe descriptor sent to AI, e.g. "ZELLE TO <PERSON>"
    removed: list[str]   # kinds removed: ["person","digits","phone","email","address","id"]

def redact(description: str) -> Redacted: ...
def redact_batch(descriptions: list[str]) -> dict[str, Redacted]: ...  # keyed by input
def payload_preview(descriptors: list[str]) -> str: ...   # exact JSON that would be sent
```
Must remove: any run of 4+ digits, card masks (`XXXX1234`, `****1234`), emails, phone numbers,
URLs' query strings, street addresses / ZIPs, trace/reference/confirmation ids, `IND NAME:` / `ORIG ID:` style ACH fields,
person names after ZELLE/VENMO/CASH APP/"TRANSFER TO/FROM"/"PAYMENT TO" → `<PERSON>` (keep business names that contain LLC/INC/CORP/CO/.COM etc.).
Keep: merchant brand words, domain names (NETFLIX.COM), city/state words are dropped.

### 4.3 security/license.py
```python
@dataclass
class License: email: str; plan: str; expires: date|None; valid: bool
def verify(key: str) -> License: ...              # Ed25519 signature, offline
def current_plan(license_key: str | None) -> License: ...  # Community if None/invalid
```
Public key embedded in module. Issuing keys is seller-side tooling, outside the core. The
Community edition has no usage caps; a license only unlocks add-on features (docs/PLUGINS.md §4).

### 4.4 ingest
```python
# detect.py
@dataclass
class ColumnMapping:
    date: str; description: str
    amount: str | None = None          # single signed column
    debit: str | None = None           # or split columns
    credit: str | None = None
    type_col: str | None = None        # e.g. Chase "Type" / CapOne "Transaction"
    category: str | None = None
    date_format: str | None = None     # strptime; None = auto
    negative_is_out: bool = True       # sign convention of `amount`
@dataclass
class FormatProfile:
    id: str; institution: str; kind: AccountKind; mapping: ColumnMapping | None
    confidence: float; account_hint: str | None   # e.g. last4 found in filename/content
def detect(filename: str, data: bytes) -> FormatProfile: ...

# csv_parser.py
def parse_csv(data: bytes, profile: FormatProfile, account_id: str, import_id: str) -> list[Transaction]: ...
def preview_csv(data: bytes, rows: int = 8) -> dict: ...  # {"columns": [...], "rows": [[...]]} for manual mapping UI

# pdf_parser.py
def parse_pdf(data: bytes, account_id: str, import_id: str) -> tuple[list[Transaction], list[str]]: ...
# returns (transactions, warnings). Infer year from statement period text; never hard-code.
# Determine in/out from the column position or running-balance delta, not keywords alone.

# normalize.py
def normalize_merchant(description: str) -> str: ...  # "AMAZON MKTPL*BJ1M70883" -> "AMAZON MKTPL"

# reconcile.py
@dataclass
class ReconcileReport: duplicates: int; transfers: int; refunds: int; notes: list[str]
def reconcile(txns: list[Transaction], accounts: dict[str, Account]) -> ReconcileReport: ...
# mutates in place: sets duplicate_of, transfer_group (+ lane TRANSFER, category "Credit Card Payment"/"Transfer Between Accounts"),
# refund_of (+ category "Refund" lane TRANSFER only if no purchase found; if matched, refund inherits purchase category & lane so it nets).
```
Known profiles required: Chase card, Chase checking, Citi, Capital One (both export styles), Gemini,
American Express, Bank of America (card + checking), Wells Fargo CSV, Discover, US Bank, Apple Card, generic fallback.

### 4.5 ai
```python
# rules.py
def apply_rules(txns, rules: list[Rule]) -> int  # user/learned rules first; sets source=RULE, status=AUTO
def apply_heuristics(txns) -> int                # taxonomy keywords; source=HEURISTIC; confidence 0.7
def learn_rule(txn, category, lane, schedule_c) -> Rule  # from a user correction

# local_ollama.py: the Community local model (loopback-only Ollama, GLM-4.7-Flash; docs/AI.md)
# A provider object has classify(descriptors, categories) -> list[dict]:
#   [{"descriptor","category","lane","schedule_c","confidence","new_category": bool}]
# Add-ons plug in through plugins.ai_categorizer (docs/PLUGINS.md §3), never through this module.

# categorizer.py
@dataclass
class CategorizeResult: by_rules: int; by_heuristics: int; by_ai: int; needs_review: int; new_categories: list[str]; sent_descriptors: int
def categorize(txns, rules, settings, categories) -> CategorizeResult
def build_ai_payload(txns) -> list[str]   # unique redacted descriptors of still-uncategorized txns (no amounts, no dates)
```
Low-confidence (<0.75) → status REVIEW. Never send anything but redacted descriptors + category list.

### 4.6 reports
```python
def summarize(txns, year: int | None = None) -> dict
# {"totals": {"business","personal","excluded","income","review"}, "by_category": [...],
#  "by_schedule_c": [...], "by_account": [...], "by_month": [...], "counts": {...}}
def export_excel(txns, accounts, path: Path, business_name: str, year: int) -> Path
# tabs: Summary, Schedule C, Business, Personal, Needs Review, Excluded (transfers/dupes), All Transactions, By Account
def export_csv(txns, path: Path) -> Path
def export_qbo_csv(txns, accounts, path: Path) -> Path          # QuickBooks Online bank upload (Date, Description, Amount)
def export_iif(txns, accounts, path: Path, account_map: dict) -> Path   # QuickBooks Desktop
```
Exports exclude duplicates; Business/Personal/Review lanes count as expenses only for `direction == out` minus matched refunds.

### 4.7 store.py
`Workspace` wraps an open `Vault`; holds accounts, transactions, rules, settings, imports in memory,
persists via `vault.put_json("transactions", ...)` etc. Methods: `add_import(filename, data) -> ImportResult`,
`list_transactions(filters)`, `update_transaction(id, **fields)`, `bulk_update(ids, **fields)`,
`add_rule`, `delete_rule`, `categorize()`, `reconcile()`, `save()`.

### 4.8 api.py (FastAPI, JSON). All routes under `/api`, require header `X-LedgerStitch-Token` (issued at launch, injected into index.html).
```
GET  /api/status                         -> {unlocked, vault_name, plan, edition, features, ai: {enabled, provider}, ui}
GET  /api/vaults                         -> [{name, path}]
POST /api/vaults            {name, passphrase, recovery?}  -> create + unlock (+ recovery_code once)
POST /api/unlock            {path, passphrase}
POST /api/unlock/recovery   {path, recovery_code, new_passphrase}  -> unlock + set new passphrase
POST /api/recovery          {passphrase}  -> {recovery_code, enabled, created}   # create / regenerate
POST /api/recovery/remove   {passphrase}
GET  /api/backup                          -> .lsbackup download (encrypted files only)
POST /api/vaults/restore    (multipart file) -> {restored, vaults}   # always a new folder, stays locked
POST /api/lock
POST /api/imports  (multipart file[], account_label?) -> [{import_id, filename, detected, count, warnings}]
POST /api/imports/preview (multipart file)          -> {columns, rows, detected}
POST /api/imports/mapped  (multipart file, mapping json, account_label)
GET  /api/accounts / PATCH /api/accounts/{id}
GET  /api/transactions?lane=&category=&account=&status=&q=&year=&limit=&offset=
PATCH /api/transactions/{id} {category?, lane?, schedule_c?, note?, status?, remember?: bool}
POST /api/transactions/bulk {ids, category?, lane?, status?}
GET  /api/categories / POST /api/categories {name, lane, schedule_c}
GET  /api/rules / POST /api/rules / DELETE /api/rules/{id}
POST /api/categorize                    -> CategorizeResult
GET  /api/ai/preview                    -> {descriptors: [...], payload: "..."}   # exactly what will be sent
GET/PUT /api/settings                   # ai_enabled, provider, model, business_name, tax_year; secrets stored in vault only
GET  /api/summary?year=
GET  /api/export/{xlsx|csv|qbo|iif}?year=   -> file download
POST /api/license {key}
```

### 4.9 Frontend (vanilla JS, no build, no third-party scripts/fonts — CSP `default-src 'self'`)
Screens: Unlock/Create vault → Import (drag & drop, detection result, manual column mapping fallback) →
Review queue (keyboard-friendly: approve, change category/lane, "remember for this merchant") →
Ledger (filter/search table, bulk edit) → Report (totals by lane, Schedule C table, monthly bars as inline SVG, export buttons) →
Settings (AI on/off, local AI, "preview what AI sees", business name, tax year, license, change passphrase).
Add-on screens, cards and settings sections are loaded from `/api/status.ui` (docs/PLUGINS.md §7).
Light/dark theme, works at 1024px+ and degrades to tablet width.

## 5. Quality bar
- `pytest` green; each module has unit tests; synthetic fixtures only in `tests/fixtures` (never copy real statements).
- Real statements in `../` (parent dir) may be used for **local manual verification only**, never committed, never sent to any API.
