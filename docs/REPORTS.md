# LedgerStitch Reports & Exports

*Prepared for review by a tax professional; not tax advice.* Every output is produced locally.

## Accounting rules (shared by every output)
- **Duplicates** (`duplicate_of` set): left out of every total, tab and export. Shown only on the Excel **Excluded** tab, with the id of the row that was kept.
- **Business / Personal / Needs Review lanes**: net = money out − money in. A matched refund inherits the purchase's category and lane, so it reduces that category's total.
- **Transfer lane** (card payments, moves between your own accounts): never an expense. Shown separately as transfers out / in.
- **Income lane**: reported separately as income (money in − money out).
- **Schedule C**: business lane only, grouped by line. Meals (L24b) are shown gross and at a 50% "deductible estimate". Every other line is 100%.
- **Year**: only transactions dated in that calendar year. Exact decimal math, rounded half-up to cents.

## Excel workbook
Real Excel dates, accounting currency format, frozen headers and AutoFilter on detail tabs.

- **Summary**: business name, tax year, date generated. Lane totals in two columns: a static "Computed by LedgerStitch" value and a "Live formula check" linked to the detail tabs (they should match). Then counts (duplicates removed, need review, uncategorized) and a category table. Click a category name to jump to its first row in All Transactions.
- **Schedule C**: Line, Description, Gross, Deductible %, Deductible estimate, Txns, with totals, the 50% meals note and the disclaimer.
- **Business / Personal / Needs Review**: one lane each. Expense is positive for purchases and negative for refunds. The total row is `=SUBTOTAL(9,…)`, so it follows the filter.
- **Excluded**: transfers, card payments, income and duplicates, with Reason and Detail columns.
- **All Transactions**: every non-duplicate row: Date, Account, Description, Merchant, Category, Lane, Sched C, Amount (signed, out is negative), Status, Source, Note, Txn ID. The visible total is a SUBTOTAL.
- **By Account**: business, personal, review, transfers out/in, income and count per account, with SUBTOTAL totals.

### How a CPA uses it
1. Read **Schedule C** for the line totals.
2. Check "Needs review" on the Summary. Those rows are not in Business.
3. Use **Source** (User / Rule / Heuristic / AI) as the audit trail. Filter on Source = AI or Status = Review to spot-check.
4. Look at **Excluded** to confirm nothing was double counted.
5. **Txn ID** is stable across re-imports.

Static values exist because files written by a program carry no cached formula results. Excel and LibreOffice recalculate on open.

## Flat CSV
One row per non-duplicate transaction, ISO dates. Amount is signed (negative = money out). Columns: date, account_id, description, merchant, category, lane, schedule_c, amount, direction, status, source, confidence, transfer_group, refund_of, note, txn_id. Text starting with `= + - @` is prefixed with `'` (spreadsheet formula-injection guard).

## QuickBooks Online (bank upload CSV)
Columns Date (MM/DD/YYYY), Description, Amount (negative = money out). Duplicates removed. Transfers included, because QBO matches the two legs itself. With several accounts you get a `.zip` with one CSV per account.

Steps:
1. Unzip the download if needed.
2. Go to **Transactions → Bank transactions**.
3. Click **Link account ▾ → Upload from file**.
4. Drop in one account's CSV.
5. Pick or add the matching QBO account, with the right type (bank or credit card).
6. Map Date / Description / Amount as one column, date format MM/dd/yyyy. For cards, tell QBO that negative means a charge.
7. Select the rows and confirm.
8. Categorize the rows in **For review**, using LedgerStitch's categories or QBO bank rules.
9. Repeat for each account.

## QuickBooks Desktop (IIF)
Tab-delimited, Windows-1252, CRLF line endings.

The `!ACCNT` block declares every account used:
- bank (BANK) and card (CCARD) accounts, named from `account_map` or the Account label (ASCII, no colons, up to 31 characters);
- `LedgerStitch:<Category>` (EXP) for business and review rows;
- `LedgerStitch Income:<Category>` (INC);
- `LedgerStitch Clearing:<Category>` (OCASSET) for transfer legs with no matching other side;
- `Owner Draw` (EQUITY) for personal rows.

Any of these can be overridden through `account_map` (category → account, account id or label → account, `"Personal"` → equity account).

Transactions:
- One TRNS / SPL / ENDTRNS block per transaction. TRNS hits the bank or card account, SPL hits the category account, and TRNS = −SPL.
- Types: CHECK and DEPOSIT for bank accounts; CREDIT CARD and CCARD REFUND for cards. A matched transfer pair becomes a single TRANSFER from the out account to the in account.
- NAME is the merchant (up to 41 characters). MEMO is the bank description plus the note. Tabs, newlines and quotes are removed.

Steps:
1. **Back up the company file.**
2. Optionally map accounts to existing chart-of-accounts names.
3. **File → Utilities → Import → IIF Files**.
4. Choose **Import for review** (QuickBooks 2019 and later).
5. Check the registers and the P&L. `LedgerStitch Clearing` should net to zero.
6. Never import the same IIF twice: QuickBooks Desktop does not de-duplicate.

## Editions
Excel, CSV, QBO CSV and IIF are the free Community feature `exports`, with no limits. Paid
add-ons can add more export formats through the plugin API: they appear as extra sections in
the Export sheet (`ui_card("export", ...)`, docs/PLUGINS.md). See {{SITE_URL}} for what they offer.

## Disclaimer
Prepared for review by a tax professional; not tax advice. Deductibility depends on facts LedgerStitch cannot know, such as business purpose, substantiation and mixed use.
