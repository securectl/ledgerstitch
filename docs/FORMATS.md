# Statement formats & format auto-learning

LedgerStitch reads statements entirely on your computer. The file's *content*
decides how it is read (magic bytes / markers); the extension is only a fallback.
One entry point parses everything: `ledgerstitch.ingest.parse_any(filename, data,
profile, account_id, import_id) -> (txns, warnings, info)` (see `ingest/dispatch.py`).

## Supported formats

| Family | Files | Parser | Column mapping? | Notes |
|---|---|---|---|---|
| CSV / TSV | `.csv` `.tsv` `.txt` | `csv_parser` | yes (auto) | 12+ built-in bank profiles, catalog, learned formats, semantic inference |
| Excel | `.xlsx` | `xlsx_parser` → CSV pipeline | yes (auto) | first sheet with a header-like row; dates → ISO; floats without noise; zip-bomb guard; legacy `.xls` rejected with a clear message |
| PDF | `.pdf` | `pdf_parser` | no | layout-generic; totals reconciled against the statement |
| OFX 1.x (SGML) / OFX 2.x (XML) | `.ofx` | `ofx_parser` | no | unclosed tags; several `STMTRS`/`CCSTMTRS` per file; `TRNAMT` negative = out; `DTPOSTED` with `[-5:EST]` offsets (date kept as written); `NAME` + `MEMO`; `FITID` → deterministic `seq` so overlapping downloads de-duplicate; only the last 4 of `ACCTID` are kept |
| QFX (Quicken) / QBO (QuickBooks bank download) | `.qfx` `.qbo` | `ofx_parser` | no | same OFX body; `INTU.BID` identifies the institution (catalog map) |
| QIF | `.qif` | `qif_parser` | no | `!Type:Bank/CCard/Cash/Oth A/Oth L`; `D` dates incl. `1/ 5'25`, `D/M` vs `M/D` resolved per file; `T`/`U` amounts; `P` `M` `N` `L`; splits ignored (record total used); investment sections skipped |
| CAMT.053 / .052 / .054 (ISO 20022) | `.xml` | `iso20022_parser` | no | **Enterprise/corporate, basic support.** `CdtDbtInd`, `RvslInd`, `BookgDt`/`ValDt`, pending entries skipped, opening/closing balance reconciliation |
| SWIFT MT940 | `.sta` `.mt940` `.txt` | `iso20022_parser` | no | **Enterprise, basic.** `:61:` D/C/RD/RC, entry-date year rollover, `:86:` narrative incl. `?20–?29` subfields, `:60F:`/`:62F:` reconciliation |
| BAI2 | `.bai` `.bai2` | `iso20022_parser` | no | **Enterprise, basic.** `16` records with funds-type variants, `88` continuations, type code 1xx–3xx = in, 4xx–6xx = out |

XML is always parsed with `defusedxml` (no DTD/entity expansion).

### Pay stubs (`payroll/paystub.py`)

Pay stubs aren't statements, so they don't go through `parse_any` or into the ledger. They have
their own entry point, `payroll.analyze_paystubs(...)` (CLI `ledgerstitch paystub`, API
`POST /api/paystubs`). The tool parses them in memory, projects them to year end and returns an
estimated W-2. It doesn't store them (see TAX_NOTES.md section 8).

| Layout | Recognized by | Notes |
|---|---|---|
| Rippling-style | "EARNINGS STATEMENT", EMPLOYEE TAXES, CURRENT / YTD columns | EMPLOYEE and COMPANY contribution columns on DEDUCTIONS rows |
| ADP-style | "this period" / "year to date", Statutory Deductions, Period Starting / Ending | space-separated amounts (`7 916 67`, `-560 98`); `*` = excluded from federal taxable wages; "Your federal taxable wages this period are $X" is cross-checked |
| Generic | any "label  current  YTD" table, with or without a header row | the last two amounts on a line are current and YTD |

Input: text-based PDFs (rendered to fixed-column text with pdfplumber, the statement parser's
library) or plain text. SSNs, account, routing and employee numbers are masked
(`security.redact.mask_identifiers`, last 4 kept) before parsing, so no output carries them.
Fixtures: `tests/fixtures/make_paystubs.py` (synthetic, deterministic).

### Payment platforms and fintech ledgers (`ingest/platforms.py`)
Selected by the catalog id. Without these rules a plain import double-counts income
(the platform charge *and* the bank deposit of the payout) and hides fees.

| Platform | Booking |
|---|---|
| PayPal activity | Gross + a separate **fee** row (gross + fee = Net). `Balance Impact = Memo` rows (authorizations, funding legs, conversions) and non-completed rows are skipped. Withdrawals and bank transfers are **transfers**. |
| Stripe balance (itemized) | Gross + fee row; `reporting_category = payout` → transfer to the bank. |
| Shopify Payments | **Transaction Date** (not Payout Date); Amount + fee row; `payout` → transfer. |
| Square transactions | Total Collected + Fees row (Net Total = sum). |
| Venmo statement | Beginning/ending-balance and disclaimer rows skipped; description = counterparty (To for payments out, From for money in) + Note, never Funding Source. Standard/Instant transfers → transfer. A payment funded by a bank/card instead of the Venmo balance gets a "Funding from …" transfer-in leg, so the bank's Venmo debit pairs as a transfer and the expense is counted once. |
| Coinbase | 2-line preamble; unsigned Total, direction from the catalog type map (Buy/Send/Card Spend out, Sell/Receive/Rewards in, Convert ignored). |
| Revolut | `State` not COMPLETED skipped; Fee column becomes its own row; TOPUP/EXCHANGE → transfer. |
| Mercury | Status Failed/Cancelled (and Pending) rows dropped (generic CSV status rule). |

Fee rows: category "Payment Processing Fees" (Schedule C line 10), lane Business.
Transfer rows: lane Transfer, "Transfer Between Accounts". `reconcile` pairs a preset
platform transfer with the matching bank deposit/withdrawal (same amount, ±6 days) even
when the bank side names PayPal/Venmo/Square, which normally blocks bank→bank pairing.
Each derived row has a deterministic seq (3·row main, +1 fee, +2 funding leg). Confirming
the auto-detected columns keeps platform semantics, so it never re-imports rows under
different ids.

### Debit cards, authorized users, pending rows
* A card-number column (`Card`, `Card No.`, masked numbers) fills `Transaction.card_last4`.
  Without one, descriptions like `DEBIT CARD PURCHASE … CARD 1234` or `XXXXXXXXXXXX1234` do.
  This is the *physical card*, useful for receipt matching and authorized-user cards.
* Rows whose status column says `Pending` are skipped with a warning; import again after they post.
  `Failed`/`Cancelled`/`Declined`/`Reverted` rows are dropped (Mercury, Revolut).

## How recognition works (tabular files)

For CSV/XLSX the store tries, in order:

1. **Learned formats (this workspace)** — `learning.lookup()` against the encrypted
   vault key `learned_formats`. Learned layouts override everything below.
2. **Built-in profiles** (`detect.KNOWN_PROFILES`) — behaviour unchanged.
3. **Catalog** — `ingest/data/known_formats.json` (115 entries). The first 40 rows are
   scanned for a catalog header, so preambles (Coinbase `User,<name>,<id>`, Venmo,
   Barclays US) are skipped. Exact match = same normalized header set; fuzzy = header
   word-token Jaccard >= 0.85 with a column count within 2. The catalog supplies the
   institution, amount/debit/credit, type, card and balance columns, date format and
   sign. Date and description columns come from semantic inference. Entries with
   `verified: false` are used at confidence <= 0.75 and ask for confirmation unless
   inference independently agrees on the amount columns and sign. Short headers
   (fewer than 4 columns) are only claimed when the file name names the same bank, so a
   bare `Date,Description,Amount` is never forced into one bank's sign convention.
   Catalog hits behave like built-ins and are not auto-recorded as learned formats.
4. **Generic scorer + semantic inference** — see below. The UI shows
   "New format — mapped automatically, please confirm".

The import result's `detected.recognition` says which one fired:
`{"source": "builtin" | "catalog" | "learned" | "pack" | "inferred", "format", "institution",
"needs_confirmation", "explanation", ...}`.

### Fingerprints
Every tabular file gets a fingerprint (`learning.fingerprint`):
* normalized header (lower-case, trimmed, punctuation stripped) → `signature`
  (file order) and `signature_sorted` (order-insensitive), column count;
* per column: kind (date / money / text / categorical / card / id), candidate date
  formats, money sign stats, text length range and entropy, categorical cardinality,
  Debit/Credit/Sale/Payment vocabulary share, card-number-likeness, balance-likeness
  (a running sum consistent with the amounts);
* delimiter, preamble lines before the header, encoding.

Fingerprints contain header *names* and value *shapes* only, never cell values.

### Semantic column inference (no AI)
Each column is scored for the roles `date, post_date, description, amount, debit,
credit, type, category, card, balance, memo, reference, status` using multilingual
header synonyms ("Transaction Date", "Posted", "Fecha", "Montant", "Betrag",
"Verwendungszweck", "Cargo/Abono", "Soll/Haben", …) plus the value profile.
Two sparse money columns that never co-occur become debit/credit.

The sign convention is inferred from, strongest first:
1. the **running balance** (balance delta equals ± amount, either row order);
2. a **type column** (Sale/Debit vs Payment/Credit agreeing with the sign);
3. **card payment rows** ("PAYMENT THANK YOU", "AUTOPAY") being credits;
4. the **majority sign** (card accounts), else the bank default (negative = out).

The result is a `ColumnMapping` plus a confidence and an explanation list.

### When a format is learned
* **User confirmation** — the manual mapping dialog or the "Confirm columns" button
  (both POST `/api/imports/mapped`) always save the layout (`source: "confirmed"`).
* **Clean automatic import** — an inferred mapping with confidence ≥ 0.75 is saved
  (`source: "auto"`) only if ≥ 95 % of data rows parsed, all dates are in range,
  the sign sanity check passes (card payments are credits, not everything one way)
  and, when there is a balance column, the running balance matches.
* Built-in profiles are not recorded (their behaviour stays exactly as before).

Each learned format keeps `uses`, `successes`, `failures` and `success_rate`.
Two consecutive failures, or a success rate below 50 % after 3 uses, **demotes** it
(no longer applied automatically).

### Near matches and drift
* Exact signature + same column count + agreeing value shapes → reuse (confidence 0.97).
* Fuzzy: Jaccard on header word tokens ≥ 0.8, same column count and value-profile
  agreement → reuse. Mapped columns that were renamed or changed kind, or a date
  format that no longer parses, are **drift**: only those roles are re-inferred.
  If the resulting confidence is below 0.75 the import asks for confirmation.

## Format packs (privacy-safe, opt-in)

`learning.export_format_pack(learned)` → JSON with, per layout, ONLY:
normalized header names (tokens with a 4+ digit run are dropped), column-role
indexes, date format, value-shape strings, sign convention and account kind. Never
sample values, file names, institutions, account numbers or usage statistics. The
pack signature field is digit-free.

`learning.import_format_pack(learned, pack)` merges a pack (`source: "pack"`) and never
overrides a layout the user confirmed.

TODO: sign packs with Ed25519 like licenses before community sharing. The interface is
in place: `import_format_pack(..., verifier=PackVerifier)` with
`verify(payload: bytes, signature: str) -> bool`.

## Optional LLM assist (interface only)

`learning.MappingAssistant.suggest_mapping(headers, shapes) -> {role: header} | None`
is an interface an add-on can implement. It receives `learning.assistant_request(fp)`: header names plus
per-column shapes such as `date MM/DD/YYYY`, `money signed`, `text 12-40 chars`, and
never raw values.

## Self-inference benchmark

With built-in profiles disabled, `learning.infer_mapping` on the founder's real exports
(7 CSVs: Citi, Capital One ×2 layouts, Chase card ×2, Chase checking, Gemini) produced
the same transactions (date, amount, direction) as the built-in profiles for 7/7 files.
On the 18 synthetic CSV fixtures it matched 18/18 on the parsed outcome. The 4 PDFs are
read by the layout-generic PDF parser (no column profile involved) and reconcile with
their statement totals.
