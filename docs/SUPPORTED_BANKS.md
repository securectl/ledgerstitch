# Supported banks & statement formats

LedgerStitch reads **CSV exports** and **PDF statements** from any bank. Known
layouts below are recognized from their header row (confidence 0.95); anything
else goes through a heuristic column detector, and if that fails the user maps
the columns by hand in the Import screen (`preview_csv` + manual mapping).

## CSV profiles

| Profile id | Institution | Kind | Header (key columns) | Sign convention |
|---|---|---|---|---|
| `chase_card` | Chase | credit card | `[Card,]Transaction Date,Post Date,Description,Category,Type,Amount,Memo` | negative = purchase |
| `chase_checking` | Chase | checking | `Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #` (rows have a trailing comma) | negative = money out |
| `citi` | Citi | credit card | `Status,Date,Description,Debit,Credit` | Debit = out, Credit = in (Credit may be written negative) |
| `capital_one` | Capital One | from file name / content | `Date,Transaction,Name,Memo,Amount` with ISO dates | negative = out |
| `capital_one_card` | Capital One | credit card | `Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit` | Debit = out, Credit = in |
| `capital_one_360` | Capital One | from file name / content | `Transaction Date,Transaction Amount,Transaction Type,Transaction Description` | negative = out |
| `us_bank` | US Bank | from file name / content | `Date,Transaction,Name,Memo,Amount` with M/D/Y dates | negative = out |
| `gemini` | Gemini | credit card | `Reference Number,Transaction Post Date,Description of Transaction,Transaction Type,Amount` (`%m/%d/%y`) | **positive = charge**; empty description → Transaction Type |
| `amex` | American Express | credit card | `Date,Description,Amount` (**only when the file name says Amex**), or `Date,Description,Card Member,Account #,Amount` | positive = charge |
| `amex_extended` | American Express | credit card | `Date,Description,Amount,Extended Details,Appears On Your Statement As,...,Category` | positive = charge |
| `boa_checking` | Bank of America | checking | summary block, then `Date,Description,Amount,Running Bal.` | negative = out |
| `boa_card` | Bank of America | credit card | `Posted Date,Reference Number,Payee,Address,Amount` | negative = purchase |
| `wells_fargo` | Wells Fargo | checking (card if the file name says so) | no header: `"date","amount","*","","description"` | negative = out |
| `discover` | Discover | credit card | `Trans. Date,Post Date,Description,Amount,Category` | positive = charge |
| `apple_card` | Apple Card | credit card | `Transaction Date,Clearing Date,Description,Merchant,Category,Type,Amount (USD),Purchased By` | positive = charge |
| `generic` | from file name | inferred | anything with a date, an amount (or debit/credit pair) and a text column | inferred (see below) |

`Capital One` vs `US Bank` share a header. The file name decides first, then
the date style (ISO = Capital One).

### What every CSV parse handles
* UTF-8 BOM, UTF-16, cp1252 fallback; `,` `;` tab `|` delimiters; quoted fields; trailing commas; blank lines.
* Amounts like `$1,234.56`, `(12.00)`, `12.00-`, `12.00 CR`, `-$5.00`.
* Leading junk / summary blocks before the header (Bank of America); footer totals and notes rows are skipped and reported as warnings.
* Zero-amount rows are skipped.
* `seq` = data-row index, so two identical $5 coffees on the same day get distinct ids. Re-importing the same file gives the same ids.
* Bank category → `Transaction.bank_category`.
* If a single amount column has no negatives at all in the file, the bank's Type/Details column (`Payment`, `Return`, `Refund`, `Credit`, `purchase_return`, …) decides in/out.

### Generic detector
Each column is scored on date-likeness, money-likeness, text-likeness,
uniqueness, and header-name hints (`date`, `desc|payee|merchant|name`,
`amount`, `debit|withdrawal|money out`, `credit|deposit|money in`, `balance`,
`type`, `category`). Debit/credit split columns are used when both exist.
Account kind comes from the file name ("card", "visa", "checking", …), then from
content (a balance column means bank; payment rows mean card). For card files the
sign convention comes from payment rows ("PAYMENT THANK YOU", "AUTOPAY", …):
payments are money in. When there are no payment rows, the majority sign is
treated as purchases. Confidence is 0.4–0.5, so the UI can ask the user to confirm.

### File-name hints
`Chase4012_Activity…` gives institution Chase and last4 4012. `9501-Credit Card - …` gives
last4 9501 and kind credit card. `…-statements-3816-.pdf` gives last4 3816.
Four-digit numbers that look like years (1990–2099) are ignored unless they are
glued to a bank name (`Chase2025`). Card-number columns (`Card`, `Card No.`,
`Account #`) override the file name.

## PDF statements

`pdf_parser.parse_pdf` is layout-generic (pdfplumber words + x/y positions):

1. **Year.** Taken from the statement period, never hard-coded:
   `January 1, 2025 through January 31, 2025`, `12/15/24 - 01/14/25`,
   `Closing Date …`, or Wells-Fargo style `Beginning balance on 12/15` / `Ending
   balance on 1/14` plus the statement date. M/D dates more than 10 days past the
   period end roll back a year, which handles Dec→Jan statements.
2. **In/out**, in order of preference:
   a. **Column position.** Amounts are matched to the right edge of the
      `Credits`/`Deposits` vs `Debits`/`Withdrawals` (and `Balance`) headers.
   b. **Section headers.** `DEPOSITS AND ADDITIONS`, `PAYMENTS AND OTHER CREDITS`
      (in); `ELECTRONIC WITHDRAWALS`, `ATM & DEBIT CARD WITHDRAWALS`, `CHECKS PAID`,
      `FEES`, `PURCHASES`, `INTEREST CHARGED` (out). `DAILY ENDING BALANCE`,
      summaries, `Items returned unpaid` and `Summary of checks written` are skipped.
   c. **Running-balance delta**, when a line has an amount and a balance.
   d. **Amount sign** as a last resort, with a warning to review.
3. **Multi-line descriptions.** Indented lines directly under a transaction are
   joined to it. Page footers are ignored.
4. **Reconciliation.** Parsed in/out totals are compared with the statement's
   `Totals` / `Total Deposits and Additions` / `Total … Withdrawals` /
   summary lines, falling back to beginning + activity = ending. Any mismatch
   becomes a warning.
5. Scanned PDFs with no text layer return a warning asking for OCR or a CSV export.

Verified locally: Wells Fargo business checking (columns layout) and Chase
Business Complete Checking (sections layout) reconcile to the cent.

## Reconciliation rules (`reconcile.py`)

1. **Duplicates.** Overlapping imports of the same account are detected by
   multiset matching on (account, date, amount, direction, normalized merchant).
   For each key, the largest count seen in any single import is kept, and the
   rest are marked `duplicate_of`. Identical rows within one file are never
   collapsed. A CSV and a PDF of the same period also dedupe, because both
   descriptions normalize to the same merchant.
2. **Transfers / card payments.**
   * A bank "out" and a card "in" with the same amount within ±6 days become
     `Credit Card Payment`. A bank "out" and a bank "in" with a transfer
     keyword become `Transfer Between Accounts`. Both legs get lane TRANSFER, a
     shared `transfer_group`, confidence 0.95, status AUTO and source HEURISTIC.
   * Unpaired rows with taxonomy keywords get the same treatment with
     confidence 0.8 and no group.
   * Guards: generic words like "ONLINE PAYMENT" on a bank account only count
     next to a card-issuer word, so utility bill-pay is not a card payment.
     Zelle/Venmo/Cash App/PayPal are never transfers. A card credit that looks
     like a merchant refund is not paired by amount alone.
3. **Refunds.** A card "in" that is not a payment is linked to the most
   recent earlier "out" on the same account with the same merchant, within 120
   days and no larger than what is left of that purchase. `refund_of` is set,
   and category, lane and schedule_c are copied so the refund nets against the
   purchase. The copy is re-synced on every run, so re-run reconcile after
   categorization. Debit-card refunds on bank accounts are matched the same way,
   so they are not counted as income.
   * **Unmatched refunds get category `Refund` and lane REVIEW** (status REVIEW,
     confidence 0.5), not TRANSFER. The user decides whether the credit offsets a
     business or a personal expense. *This differs from ARCHITECTURE.md §4.4
     ("lane TRANSFER if no purchase found") on purpose.*
4. **Income.** Any other "in" on a bank account becomes `Income & Deposits`,
   lane INCOME, confidence 0.6, status REVIEW.

Rows set by the user or by a saved rule (source USER/RULE, or status APPROVED)
are never re-categorized. Duplicate detection still applies to them, and the
user's row is preferred as the one kept.
