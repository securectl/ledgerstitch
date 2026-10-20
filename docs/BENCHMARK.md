# LedgerStitch accuracy benchmark

We don't make accuracy claims without a published benchmark. This is that benchmark: a **fixed, synthetic, labeled test set** and a runner
that pushes it through the real import pipeline and scores every step. Anyone can reproduce the numbers
below with one command, and CI fails if they regress.

Everything in the corpus is fabricated: names, merchants, account numbers, amounts. No real statement
was used to build it or to tune it.

## How it works

**Corpus** (`ledgerstitch/bench/corpus.py`, seed 2025, corpus v1). One fictional sole proprietor (a
design studio) with mixed business and personal spending across 26 accounts and 33 statement files,
December 2024 to August 2025:

* **CSV**: every built-in bank profile (Chase card and checking, Citi, Capital One card and 360, US Bank,
  Gemini, Apple Card, Robinhood card, Discover, Bank of America card and checking, Amex extended,
  headerless Wells Fargo), two catalog formats (Schwab, Mercury) and one layout no profile knows
  (generic detector).
* **OFX/QFX/QBO**: OFX 1.x SGML checking, OFX 2.x XML card, QBO card, two overlapping QFX downloads.
* **QIF**: bank (Quicken `1/ 5'25` dates) and card.
* **XLSX**: an empty first sheet, preamble rows, split debit/credit columns, a total row.
* **Text PDFs**: Chase-like checking (direction by section, checks paid, daily balances), Wells-Fargo-like
  (credit/debit/balance columns, two pages, December→January rollover, wrapped descriptions), Chase-like
  card (account summary, foreign-currency notes on continuation lines, year-to-date totals),
  Citi-like card (sale/post dates, "Standard Purchases"), Robinhood card (glued summary words,
  reference-number column, `100.00-` credits), and a running-balance-only savings statement.

Hard cases built in: refunds (full and partial), card payments and own-account transfers (some with the
other side not imported), the same transactions re-printed by overlapping files (CSV/CSV, CSV/PDF,
OFX/OFX), identical same-day purchases that are *not* duplicates, foreign-currency notes, reference and
trace numbers in descriptors, multi-line descriptions, and rows that must be skipped (pending, declined,
failed, zero-amount, totals and summary lines).

**Ground truth** per row: date, amount, direction, the description as printed, the expected category,
its default lane, and the Schedule C line. Per statement: opening and closing balance. Labeling rules:

* The category is what a bookkeeper would choose from the LedgerStitch taxonomy. Context-dependent
  merchants are labeled by the account's use: Amazon is *Office Supplies* on a business card and
  *Shopping* on a personal one; restaurants are *Business Meals* on business accounts and *Dining* on
  personal ones. A rules engine cannot know this, and the benchmark counts it as an error.
* Deposits whose source can't be told from the descriptor (mobile deposits) are *Income & Deposits*;
  client ACH, Stripe and Square deposits are *Business Income*.
* The lane is the category's default lane. The Schedule C line is the category's line when the
  category is business or review (for example *Vehicle & Fuel* → L9), and none for personal categories.
* One person labeled the corpus. There is no inter-annotator agreement.

**Runner** (`ledgerstitch/bench/runner.py`). It creates a throwaway encrypted vault in a temp folder
and imports every file with `Workspace.add_import`, exactly as the app does (detect → parse → merge →
reconcile → categorize). Nothing is written outside the temp folder. AI is off by default; `--ai` uses
the configured provider and sends redacted descriptors only, through the same redaction boundary as the
app.

**Metrics**

* *Rows*: per file, parsed rows are matched one-to-one to truth rows (amount and date, then
  description words). *Exact* means date, amount and sign are all right, out of all truth rows.
  *Description captured* means every word of the printed descriptor is in the parsed description.
* *Statements balance*: opening + parsed activity = closing, using the truth balances. The *app's own
  balance check* is the `reconciled` flag the Import screen shows ("Balances match ✓ / Doesn't match ✗
  (difference $X)"). Only statements that print totals or balances can be checked; most CSVs can't.
* *Pairs*: precision and recall over truth pairs. Duplicates are extra printings of one real
  transaction; transfers and card payments pair two legs; refunds link to their purchase. Identical rows
  (two $5.00 coffees on the same day) are interchangeable.
* *Categories*: the final category, lane and Schedule C line of each kept (non-duplicate) transaction,
  scored once per real transaction. *Auto-approved* is the share the app would not send to Review.

## Bugs this benchmark found

The first run of the benchmark found the problems below. All are fixed in the same change, each with a
regression test.

| # | Problem | Effect on the first run | Fix |
|---|---|---|---|
| 1 | PDF card statements that head purchases "Standard Purchases" (Citi-style) were not recognized as a money-out section. | Every purchase on both statements was skipped (28 rows). Both were off by about $2,060. | `pdf_parser`: the section pattern accepts "Standard Purchases". |
| 2 | Chase-style card summaries ("Payment, Credits -$X", "Purchases +$Y") were not read: the parser expected "Payments" and no sign. | The balance check compared all activity with the fees-only "TOTAL FEES FOR THIS PERIOD" line. A correctly parsed statement showed "doesn't match" (a false alarm). | `pdf_parser`: the summary pattern accepts "Payment," and signed `+$`/`-$` amounts. |
| 3 | Rows in "Checks paid" PDF sections had no description (the statement prints only the check number). | The check was not recognized as a duplicate of the same check in the CSV export ("CHECK 1042"). | `pdf_parser`: the row is named `CHECK <number>`. |
| 4 | A refund was linked to the most recent purchase from the same merchant, even when another purchase matched it exactly. This affects marketplace descriptors, which all normalize to "AMZN MKTP". | One wrong refund link, which also used up the purchase that a later refund belonged to. 20/23 links were right. | `reconcile`: among same-merchant candidates, prefer the same amount, then the identical descriptor, then the most recent. |
| 5 | The repo's synthetic CSV test fixtures had never been committed: `.gitignore`'s `*.CSV` also matches `.csv` on macOS's case-insensitive filesystem. | A fresh clone fails the CSV parser tests. | `.gitignore` negation for `tests/fixtures/**/*.csv`; fixtures added. |

Before and after the fixes, on the same corpus:

| Metric | Before | After |
|---|---:|---:|
| Exact date + amount + sign | 97.5% | 100.0% |
| Statements that balance | 31/33 | 33/33 |
| App's balance check agrees with truth | 6/7 | 7/7 |
| Duplicates recall | 97.6% | 100.0% |
| Refund links, precision / recall | 95.2% / 87.0% | 100.0% / 100.0% |
| Category accuracy | 82.3% | 82.3% |
| Schedule C line accuracy | 69.0% | 70.0% |

## How to read the numbers

* **Parsing at 100% is not a claim about real statements.** The files are clean text-layer synthetic
  renderings of layouts the parsers were written for. The result shows that the pipeline handles every
  listed hard case, and the floor catches regressions. It does not show how the parsers cope with layout
  drift, scanned PDFs or banks we have never seen. The real-world evidence so far is an internal run
  on real statements (6/6 PDFs and 7/7 CSVs parsed and balanced).
* **Categorization is the honest headline:** 82% of transactions get the right category with rules
  only. 72% are auto-approved, and 91% of those are right; the rest go to Review. The worst areas are in
  the table below:
  * Utilities bill-pay descriptors aren't recognized.
  * Business vs personal context isn't known (Business Meals vs Dining, Office Supplies vs Shopping).
  * Contractor payouts through a payroll service are labeled Payroll.
  * Client ACH deposits are labeled generic income.
  * "UBER *EATS" is read as a ride.
* **Schedule C line accuracy (70%)** follows from the same context errors. A meal on the business card
  labeled *Dining* gets no line.
* **No AI number is published yet.** The `--ai` path is implemented and tested with a stub model. A
  live run with local AI (GLM-4.7-Flash in Ollama) still has to be done and published here. Until
  then, no claim that AI "improves accuracy by X" should be made.

## Reproduce

```
ledgerstitch benchmark --out docs                 # rules + heuristics (this file)
ledgerstitch benchmark --ai --model llama3.1:8b   # + local Ollama on 127.0.0.1
ledgerstitch benchmark --ai --settings-from ~/LedgerStitch/vaults/ws-1234abcd.vault   # a vault's AI settings, not its data
ledgerstitch benchmark --corpus-out /tmp/corpus   # also keep the generated files + truth.json
```

Same code and seed means the same corpus bytes and the same numbers. `tests/test_benchmark.py` checks
that the generator is deterministic and that the truth balances add up. It also enforces a regression
floor: the values measured here minus a small margin. Raise the floor when accuracy improves; never lower
it to make a change pass.

**Not covered yet:**

* payment-platform exports (PayPal, Stripe, Venmo, Shopify, Square, Coinbase, Revolut);
* ISO 20022 / MT940 / BAI2;
* scanned PDFs and OCR, and receipts;
* non-US layouts;
* manual column mapping and learned formats.

## Results

<!-- BEGIN GENERATED -->
Corpus v1, seed 2025: **33 statement files, 1096 truth rows, 26 accounts** (7 more rows in the files must *not* be imported: pending, declined, failed, zero-amount, summary lines). Categorization mode: **rules + built-in heuristics only (no AI)**. LedgerStitch 1.0.0.

### Headline

| Metric | Result |
|---|---|
| Rows found (recall) | 100.0% (1096/1096) |
| Rows that are real (precision) | 100.0% (1096/1096) |
| Exact date + amount + sign (of all truth rows) | 100.0% |
| Date / amount / sign accuracy (of found rows) | 100.0% / 100.0% / 100.0% |
| Description captured (every descriptor word present) | 100.0% |
| Statements that balance (opening + parsed activity = closing) | 33/33 (100.0%) |
| App's own balance check | 7 checkable, 7 "match", 0 "doesn't match"; agrees with truth on 7/7 |
| Duplicates across overlapping files, precision / recall | 100.0% / 100.0% (42/42) |
| Transfer & card-payment pairs, precision / recall | 100.0% / 100.0% (51/51) |
| Refund → purchase links, precision / recall | 100.0% / 100.0% (23/23) |
| Category accuracy, all kept transactions | 82.3% (n=1054) |
| Category accuracy, spending + income only (no transfers/card payments) | 80.1% (n=935) |
| Lane accuracy | 89.1% |
| Auto-approved share / accuracy of auto-approved | 72.5% / 90.7% |
| Left Uncategorized | 75 of 1054 |
| Schedule C line accuracy (rows whose category maps to a line) | 70.0% (n=397) |
| Put in the Business lane but not business | 0 |
| Account kind detected (card vs bank) | 100.0% |

### Per format

| Format | Files | Truth rows | Recall | Precision | Exact | Balances |
|---|---:|---:|---:|---:|---:|---:|
| CSV · Amex (extended, multi-line cells) | 1 | 62 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Apple Card | 1 | 37 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Bank of America card | 1 | 28 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Bank of America checking | 1 | 45 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Capital One 360 | 1 | 12 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Capital One card | 1 | 53 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Chase card | 2 | 87 | 100.0% | 100.0% | 100.0% | 2/2 |
| CSV · Chase checking | 1 | 66 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Citi | 1 | 46 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Discover | 1 | 26 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Gemini | 1 | 26 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · unknown layout (generic detector) | 1 | 47 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Mercury (catalog) | 1 | 31 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Robinhood card | 1 | 36 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Schwab (catalog) | 1 | 54 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · US Bank | 1 | 36 | 100.0% | 100.0% | 100.0% | 1/1 |
| CSV · Wells Fargo (no header) | 1 | 95 | 100.0% | 100.0% | 100.0% | 1/1 |
| QBO · card | 1 | 28 | 100.0% | 100.0% | 100.0% | 1/1 |
| QFX · savings (overlapping) | 2 | 10 | 100.0% | 100.0% | 100.0% | 2/2 |
| OFX 1.x SGML · checking | 1 | 32 | 100.0% | 100.0% | 100.0% | 1/1 |
| OFX 2.x XML · card | 1 | 29 | 100.0% | 100.0% | 100.0% | 1/1 |
| PDF · running balance only | 1 | 2 | 100.0% | 100.0% | 100.0% | 1/1 |
| PDF · card (Chase-like) | 1 | 20 | 100.0% | 100.0% | 100.0% | 1/1 |
| PDF · columns (Wells-Fargo-like) | 1 | 18 | 100.0% | 100.0% | 100.0% | 1/1 |
| PDF · card (Citi-like) | 2 | 32 | 100.0% | 100.0% | 100.0% | 2/2 |
| PDF · Robinhood card | 1 | 11 | 100.0% | 100.0% | 100.0% | 1/1 |
| PDF · sections (Chase-like checking) | 1 | 12 | 100.0% | 100.0% | 100.0% | 1/1 |
| QIF · bank | 1 | 54 | 100.0% | 100.0% | 100.0% | 1/1 |
| QIF · card | 1 | 27 | 100.0% | 100.0% | 100.0% | 1/1 |
| XLSX · debit/credit + preamble | 1 | 34 | 100.0% | 100.0% | 100.0% | 1/1 |

| Family | Files | Truth rows | Recall | Precision | Exact | Balances |
|---|---:|---:|---:|---:|---:|---:|
| CSV | 18 | 787 | 100.0% | 100.0% | 100.0% | 18/18 |
| OFX | 5 | 99 | 100.0% | 100.0% | 100.0% | 5/5 |
| PDF | 7 | 95 | 100.0% | 100.0% | 100.0% | 7/7 |
| QIF | 2 | 81 | 100.0% | 100.0% | 100.0% | 2/2 |
| XLSX | 1 | 34 | 100.0% | 100.0% | 100.0% | 1/1 |

### Pairing

| | Truth | Predicted | Correct | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| Duplicates | 42 | 42 | 42 | 100.0% | 100.0% |
| Transfers + card payments | 51 | 51 | 51 | 100.0% | 100.0% |
| Refunds | 23 | 23 | 23 | 100.0% | 100.0% |

Card payments found: 29/29; own-account transfers found: 22/22. 4 of the duplicates were collapsed on import because the bank's own transaction id (OFX FITID) repeats.

### Categorization

| Decided by | Rows | Category | Lane | Auto-approved | Accuracy of auto |
|---|---:|---:|---:|---:|---:|
| heuristic | 979 | 88.6% | 91.8% | 78.0% | 90.7% |
| none | 75 | 0.0% | 53.3% | 0.0% | n/a |

Worst categories (most errors first):

| Expected category | Rows | Correct | Accuracy | Predicted instead |
|---|---:|---:|---:|---|
| Utilities | 33 | 0 | 0.0% | Uncategorized (33) |
| Business Meals | 24 | 0 | 0.0% | Dining & Restaurants (24) |
| Office Supplies | 38 | 14 | 36.8% | Shopping (24) |
| Shopping | 77 | 59 | 76.6% | Groceries (12), Uncategorized (6) |
| Contractor Payments | 24 | 8 | 33.3% | Payroll & Wages (16) |
| Business Income (Sales & Payouts) | 33 | 18 | 54.5% | Income & Deposits (15) |
| Charity & Donations | 29 | 14 | 48.3% | Uncategorized (15) |
| Dining & Restaurants | 63 | 53 | 84.1% | Rideshare & Transit (10) |
| Business Travel | 20 | 15 | 75.0% | Uncategorized (5) |
| Personal Care | 34 | 29 | 85.3% | Uncategorized (5) |
| Cloud & Hosting | 24 | 20 | 83.3% | Uncategorized (4) |
| Equipment & Supplies | 4 | 0 | 0.0% | Uncategorized (2), Shopping (2) |

### Hard cases

| Case | Rows | Parsed exactly | Category accuracy |
|---|---:|---:|---:|
| Card payments | 79 | 100.0% | 98.7% |
| Re-printed in an overlapping file | 42 | 100.0% | n/a |
| Foreign currency | 6 | 100.0% | 33.3% |
| Multi-line description (PDF) | 13 | 100.0% | 87.5% |
| Recurring subscriptions | 123 | 100.0% | 87.5% |
| Reference/trace numbers in the descriptor | 387 | 100.0% | 75.8% |
| Refunds | 25 | 100.0% | 82.6% |
| Identical same-day purchases (not duplicates) | 38 | 100.0% | 77.8% |
| Own-account transfers | 48 | 100.0% | 100.0% |
<!-- END GENERATED -->
