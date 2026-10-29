# LedgerStitch Tax Notes (CPA review of the default taxonomy)

Owner: Tax/Accounting domain lead. Scope: `ledgerstitch/taxonomy.py` (default categories, lanes,
Schedule C mapping, keyword heuristics) and the tax concepts customers will ask about.

> LedgerStitch organizes bank and card data for review by the taxpayer and their tax professional.
> It is not tax preparation software and nothing in it or in this document is tax advice.
> Line numbers are from the 2025 Schedule C (Form 1040). Re-check them against the final IRS form
> each year before release. [Founder: confirm against the published 2025 and 2026 PDFs.]

---

## 1. Design principles for the default taxonomy

1. **When in doubt, Review.** A wrong "Business" label overstates deductions, which is the costly
   error in an audit. A wrong "Personal" label understates them. Anything commonly mixed-use
   defaults to the **Review** lane so a person decides. The user approves it once and ticks
   "remember for this merchant", so the cost is one click per merchant.
2. **Precision over recall in keywords.** A keyword that misfires produces a confident wrong answer
   (status AUTO, not reviewed). A missed keyword just goes to AI or the Review queue. So generic
   words that prefix unrelated merchants were removed.
3. **Never hide a real expense as a transfer.** Keywords that put a row in the Transfer lane drop it
   from expense totals without anyone noticing. Those keywords have to be issuer-specific.
4. **Separate kinds of income.** W-2 pay, interest and business receipts end up on different forms.
   The CPA needs them apart to reconcile Schedule C gross receipts with the forms the taxpayer received.
5. **Non-Schedule C items are never mapped to a Schedule C line.** This covers federal income tax,
   health insurance, retirement contributions, charity and loan principal.

How matching works (from `ai/rules.py`): the longest keyword wins. A keyword must start on a word
boundary. Keywords of 4 characters or fewer, and keywords written with a trailing space, must also
end on one (a plural "S" is allowed). Income-lane keywords only match money coming in. Longer
keywords can still match as prefixes, so `"WATER"` matched `"WATERMELON CAFE"`. That's why
prefix-prone words were replaced.

---

## 2. Schedule C line table (2025)

| Key | Line | Name on form | Notes |
|---|---|---|---|
| L4 | Part I, 4 | Cost of goods sold (from Part III, line 42) | LedgerStitch totals *purchases* (Part III line 36). Opening/closing inventory, labor (37) and materials (38) are the preparer's job. The L4 total is **not** COGS until inventory is adjusted. |
| L8 | 8 | Advertising | |
| L9 | 9 | Car and truck expenses | See section 4.3 (vehicle). |
| L10 | 10 | Commissions and fees | Payment-processor fees, sales commissions. |
| L11 | 11 | Contract labor | |
| L12 | 12 | Depletion | Added for completeness; rare for our users. |
| L13 | 13 | Depreciation and section 179 expense deduction | Never assigned by default. Equipment purchases land on L22/L18. The CPA decides whether to capitalize or expense. |
| L14 | 14 | Employee benefit programs (other than on line 19) | |
| L15 | 15 | Insurance (other than health) | Health insurance is **not** here. |
| L16a | 16a | Interest: Mortgage (paid to banks, etc.) | Added. Business property mortgages only, not the home mortgage. |
| L16b | 16b | Interest: Other | Business card or loan interest. |
| L17 | 17 | Legal and professional services | |
| L18 | 18 | Office expense | Software/SaaS and hosting also mapped here (acceptable and common; some preparers prefer L27a). |
| L19 | 19 | Pension and profit-sharing plans | Added. Plans for *employees* only. The owner's own SEP/solo 401(k) goes on Schedule 1, not here. |
| L20a | 20a | Rent or lease: Vehicles, machinery, and equipment | |
| L20b | 20b | Rent or lease: Other business property | Not the home rent (see home office). |
| L21 | 21 | Repairs and maintenance | |
| L22 | 22 | Supplies (not included in Part III) | |
| L23 | 23 | Taxes and licenses | Never federal or state *income* tax. |
| L24a | 24a | Travel | |
| L24b | 24b | Deductible meals | The form wants the *deductible* amount. LedgerStitch shows gross and a 50% estimate (`reports/summary.py`). |
| L25 | 25 | Utilities | |
| L26 | 26 | Wages (less employment credits) | |
| L27a | 27a | Other expenses (from line 48) | Bank fees, shipping, education, P2P/wires once identified. Part V (line 48) needs a description per item, and the category name serves as that. |
| L27b | 27b | Energy efficient commercial buildings deduction (Form 7205) | Added for completeness; never assigned by default. |

Deliberately **not** in the table: **line 30, business use of home** (Form 8829 or simplified
method). It is computed, not mapped from transactions. See section 4.

Keys `L4` and `L24b` are unchanged because `reports/summary.py` and the tests use them. `L4`
moved to the top so `by_schedule_c` lists lines in form order.

---

## 3. Changes made to `taxonomy.py`

### 3.1 Lane changes (now Review by default)
| Category | Was | Now | Why |
|---|---|---|---|
| Business Travel | Business | Review | Airlines, hotels and Airbnb are as likely to be vacations. |
| Telecommunications | Business | Review | Personal cell phones and home internet are mixed-use. Only the business-use % is deductible. |
| Payroll & Wages | Business | Review | For a typical sole prop, "GUSTO"/"ADP" is their own or a spouse's W-2 paycheck. |
| Professional Services | Business | Review | Divorce lawyers and personal tax prep are not deductible. Now also catches TurboTax and H&R Block. |
| Taxes & Licenses | Business | Review | DMV registration is usually personal. State "Dept of Revenue" may be income or sales tax. |
| Bank & Merchant Fees | Business, L10 | Review, **L27a** | Fees on personal accounts are not deductible. Bank charges usually go on "Other expenses". Processor fees moved to a new L10 category. |
| Interest Paid → **Interest Expense** | Business | Review | Renamed: "Interest Paid" read like interest *received*, which clashes with the income keyword `INTEREST PAID`. Personal card interest is not deductible. |
| Home & Garden | Personal | Review (L22) | Home Depot and Lowe's are core suppliers for trades and landlords. |

### 3.2 New categories
| Category | Lane | Line | Purpose |
|---|---|---|---|
| Payment Processing Fees | Business | L10 | Stripe/Square/PayPal/merchant fees (split from bank fees). |
| Income Tax Payments | Personal | none | `IRS USATAXPYMT`, `IRS TREAS` and similar. Previously `"IRS "` mapped federal income tax to **L23**, a material error. |
| Health Insurance | Personal | none | Keeps premiums off L15. The self-employed health insurance deduction is Schedule 1 line 17, and the CPA handles it. |
| Parking & Tolls | Review | L9 | Business parking and tolls usually count whichever vehicle method is used. Kept separate from fuel so the CPA can use them with either method. |
| Loan & Mortgage Payments | Review | none | Principal is not deductible. Stops users approving full car-loan or SBA payments as business. The interest portion can be split out to L16a/L16b. |
| Investments & Retirement | Transfer | none | Brokerage/IRA contributions are not expenses. SEP/solo 401(k) belongs on Schedule 1. |
| Business Income (Sales & Payouts) | Income | none | Stripe/Square/Shopify payouts, separated so the CPA can reconcile them with the processor's reports. |
| Paycheck (W-2 Wages) | Income | none | W-2 pay is not Schedule C gross receipts. |
| Interest Income | Income | none | Goes on Schedule B, not Schedule C. |

`Income & Deposits` (generic deposits) and `Shopping` now have defaults: `Shopping` gets L22
when moved to Business.

### 3.3 Keyword false-positive fixes
| Keyword (old) | Problem | Fix |
|---|---|---|
| `WATER`, `ELECTRIC` (Utilities) | Prefix-matched "WATERMELON CAFE", "WATERFRONT", "ELECTRIC AVENUE" | Removed. Added `WATER UTIL/DEPT/DIST`, `ELECTRIC CO` (covers CO-OP/COMPANY) and named utilities (Duke, PG&E, ConEd, Xcel, Entergy, CenterPoint, Southwest Gas…). |
| `QT ` | Hits "QT 1234 INSIDE", a QuikTrip store purchase (snacks), not fuel | Removed; `QUIKTRIP` kept. QT store rows go to AI/Review. |
| `DART ` | Hits "DART CONTAINER" and other DART businesses | Replaced with `DART GOPASS`, `DART.ORG`. |
| `METRO` | Prefix-matched "METROPCS" (phone), "METRO DINER" | Removed. Added specific transit agencies (MTA*, WMATA, MBTA, SEPTA, Clipper, Ventra). METROPCS goes to Telecom. |
| `ADP ` | Incoming ADP payroll is a W-2 paycheck | Kept (whole-word). Payroll & Wages is now Review, and incoming `PAYROLL` goes to Paycheck (W-2). |
| `IRS ` | Federal income tax was mapped to L23 Taxes and licenses | Moved to **Income Tax Payments** (Personal). `EFTPS` (could be payroll tax deposits) goes to Taxes & Licenses (Review). |
| `AUTOPAY`, `AUTOMATIC PAYMENT`, `ONLINE PAYMENT`, `MOBILE PAYMENT`, `INTERNET PAYMENT` (Credit Card Payment) | Matched real bills ("GEICO AUTOPAY", "ATMOS ENERGY AUTOMATIC PAYMENT", BofA "ONLINE PAYMENT … TO COMCAST"), which put them in Transfer and **silently dropped real expenses** | Replaced with card-side "THANK YOU" credit forms and issuer-named debits (`CREDIT CRD AUTOPAY`, `AMEX EPAYMENT`, `APPLECARD GSBANK`, `CAPITAL ONE AUTOPAY`…). Reconcile still pairs card payments by amount and date. |
| `SHELL` | Prefix-matched "SHELLY'S SALON" | `SHELL ` (whole word), `SHELL OIL`, `SHELL SERVICE`. |
| `SQ *` (Dining) | Any Square seller (barbers, contractors, farmers markets) became "Dining", auto-approved | Removed; goes to AI/Review. `TST*` (Toast, which is mostly restaurants) kept. |
| `WENDY` | Matches the person's name | `WENDYS`, `WENDY'S`. |
| `STEAM` | "STEAM CLEANING", "STEAMBOAT" | `STEAMGAMES`, `STEAMPOWERED`, `STEAM PURCHASE`. |
| `MSFT`, `MICROSOFT` vs Xbox | Xbox Game Pass became business software | `MICROSOFT*XBOX` and similar added to Entertainment (longer wins). `MSFT` removed. |
| `INTUIT` | TurboTax (personal tax prep) became business software | `TURBOTAX` (longer) goes to Professional Services (Review). QuickBooks keywords kept. |
| `O'REILLY` | O'Reilly Media (books) became vehicle | `O'REILLY AUTO`, `OREILLY AUTO`. |
| `CHURCH` | "CHURCH'S CHICKEN" became charity | Added `CHURCH'S` and `CHURCHS CHICKEN` to Dining (longer wins). |
| `INSURANCE` | Health premiums went to L15 | Health insurer names go to Health Insurance. |
| `ZOOM`, `WIX` | Short/generic | `ZOOM.US`, `ZOOM VIDEO`, `WIX.COM`. |
| `SOUTHWEST` | "SOUTHWEST GAS" (utility) | `SOUTHWEST GAS` goes to Utilities (longer wins). Added `SOUTHWES ` (the truncated airline descriptor). |
| `TOLL` | Whole-word only, so missed "TOLLWAY" | Added `TOLLWAY`, `TOLL ROAD`, toll-tag brands. |

Kept on purpose: `AWS`, `CPA `, `UPS `, `AVIS`, `ROSS`, `TACO`, `ALDI`. The matcher's word
boundaries already protect these (LAWSON, CPAP, GROUPS, DAVIS, CROSS, TACOMA, RINALDI).
The tests in `tests/test_rules.py` cover them.

### 3.4 Known residual risks (not fixable in data alone)
- **Direction-blind non-income keywords.** An incoming Upwork, DoorDash, Uber or Airbnb *payout* (gig
  income) matches the expense category and nets against expenses instead of showing as income.
  **Recommendation to AI engineer:** in `apply_heuristics`, when `direction == IN` and the matched
  category is in an expense lane, send it to Review (or Income) instead of AUTO.
- **Amazon, Costco, Walmart and Target default to Personal.** These are the most common mixed-use
  merchants. They stay Personal because a Review default would flood the queue, and `AMZN` is pinned
  to Shopping by tests. The User Guide tells users to add account-scoped rules (e.g., "AMAZON on my
  business card → Office Supplies").
- **Fidelity/Schwab** also sell insurance and title services. Rare.
- `DIRECT DEP` now means Paycheck (W-2). An ACH from a business client labeled DIRECT DEP will be
  mislabeled. Income-lane rows are shown separately, so the CPA sees them.

---

## 4. Concepts customers will ask about

### 4.1 Meals: the 50% rule
- Business meals (Schedule C line 24b) are generally **50% deductible**: meals with clients, prospects
  or colleagues where business is discussed, or meals while traveling away from home overnight.
  The taxpayer or an employee must be present and the cost must not be lavish.
- The temporary 100% deduction for restaurant meals applied **only to 2021 and 2022**. It has expired.
- **Entertainment** (sports tickets, golf, concerts) is **not deductible** at all since 2018, even with
  a client.
- Some exceptions are 100% (e.g., food provided to the public for free, a company holiday party for
  all employees). The CPA decides those.
- **What LedgerStitch does:** a Dining transaction is Personal unless the user moves it to Business or
  "Business Meals" (default Review). The Schedule C tab shows **gross** meals and a **50% deductible
  estimate**. Users should note *who* and *what business purpose* in the transaction note, because
  substantiation needs those details. Bank data alone does not substantiate a meal.

### 4.2 Home office
- Requires **regular and exclusive** use of part of the home as the principal place of business
  (or for meeting clients, or a separate structure). Employees' home offices are not deductible
  through 2025 and beyond under current law. Sole proprietors' home offices are.
- Two methods:
  - **Simplified:** $5 per square foot, up to 300 sq ft ($1,500 max). Nothing to track except square footage.
  - **Regular (Form 8829):** the business-use percentage of rent or mortgage interest, utilities,
    insurance, repairs and depreciation. Limited to business income.
- **What LedgerStitch does:** nothing automatic. Home rent, mortgage, utilities and home insurance stay
  **Personal or Review**. Do **not** move home rent to L20b or utilities to L25 at 100%. Instead, give
  the CPA the annual totals of those Personal categories (the Personal tab has them) plus the office
  square footage, and let them compute line 30.

### 4.3 Vehicle
- The CPA chooses the method for vehicle costs. Under some methods, fuel and repairs are not
  deducted separately; business parking and tolls usually still are. Commuting is never deductible.
- **What LedgerStitch does:** shows **expenses only**. It does not track trips and does not choose
  a method. "Vehicle & Fuel" and "Parking & Tolls" default to Review on line 9, kept separate so
  the CPA can use them with either method. The L9 total is a starting point, and the report must
  not imply otherwise.

### 4.4 Other boundaries worth knowing
- **Larger equipment purchases** may need to be capitalized and depreciated rather than expensed.
  LedgerStitch maps purchases to L22/L18 and leaves the decision to the CPA.
- **Loan payments:** principal is not deductible. Only interest is, and only on business debt.
- **Owner draws and contributions** are transfers, not expenses or income.
- **Sales tax** collected and remitted: if it's included in gross receipts, the remittance goes on
  L23. Ask the CPA.
- **Self-employment tax, the QBI deduction, retirement contributions and health insurance** are
  all off Schedule C or computed elsewhere.

---

## 5. What LedgerStitch does NOT do
- It is **not tax preparation software**. It does not compute income tax, produce IRS forms, or
  check for completeness or eligibility. The one exception to "no tax arithmetic" is the pay stub
  tool (section 8): payroll projections (Social Security and Medicare at their statutory rates,
  annual contribution limits) shown as an *estimate*, never income tax. The investments tool
  (section 9) only adds up what payers reported on 1099s and labels where each total goes.
- It does **not e-file** or submit anything to the IRS, any state, or anyone else.
- It does **not give tax, legal or accounting advice**. Lanes, categories and Schedule C lines are
  suggestions for the user and their tax professional to review.
- It does not track trips, compute depreciation or home office, value inventory (COGS),
  handle accrual accounting, run payroll, or handle state-specific rules.
- It does not substantiate deductions. Receipts, logs and business-purpose notes are still needed.
- It sees only the statements the user imports. Cash expenses, a missing account, or a statement for
  the wrong period produce incomplete totals.
- AI suggestions can be wrong. Built-in keywords can be wrong.

## 6. Payment processors and income
- Bank deposits from Stripe, Square and similar processors are **net** payouts (after fees and
  refunds). LedgerStitch's "Business Income (Sales & Payouts)" total is net deposits, **not** gross
  receipts. Give the CPA the processor's annual gross report too.
- Personal Venmo or Zelle reimbursements that a platform reports as income need to be explained to
  the CPA.

## 7. "Before you send to your CPA" checklist
1. Every account that had business activity in the year is imported: checking, savings, every card,
   PayPal/Stripe if available. Check the "By Account" tab for gaps in months.
2. The date range covers the full tax year (Jan 1 to Dec 31), and the right tax year is selected.
3. The **Review queue is empty** (or you've told the CPA which items are still uncertain).
4. Card payments and transfers show in **Excluded**, not as expenses. Spot-check five.
5. Refunds are netted against the original purchase.
6. Amazon, Costco, Walmart, Target, Home Depot and P2P rows have been checked one by one for business
   use.
7. Meals have who and what in the note. Entertainment is Personal.
8. Vehicle: you've told the CPA which method they should use, and given them your vehicle records.
9. Home office: you've given square footage (office and total home). Home rent and utilities are left
   Personal.
10. Income: payouts, W-2 paychecks, interest and owner contributions are labeled correctly. You've
    gathered the tax forms you received.
11. Loan payments: only interest is marked Business (ask your lender for the interest statement).
12. Large equipment purchases are flagged for the CPA to decide on depreciation.
13. Cash expenses and receipts not on any statement are given separately.
14. Send the **Excel workbook** (Summary, Schedule C, Business, Needs Review, Excluded, All
    Transactions, By Account). The CPA can trace every total back to a transaction and its source
    (rule, keyword, AI, you).
15. Keep your own copy of the original statements and the export. LedgerStitch is not a records-retention
    system.

## 8. Pay stubs: projection, estimated W-2 and payroll signals (`ledgerstitch/payroll/`)

**Boundary.** The pay stub tool reads what an employer printed, projects it to December 31 and
compares it with published payroll rates and limits. It computes **no income tax**: no taxable
income, brackets, credits or refund. Every output is labelled **"ESTIMATE — not issued by
employer"**; the employer's W-2 is the document of record. Stubs are parsed in memory and never
stored. SSNs and account numbers are masked before parsing.

**What it estimates.** W-2 boxes 1-6, 10, 12 (C group-term life, D/E/G pre-tax deferrals, AA/BB
Roth, W HSA employee + employer, DD only when the stub shows the employer's medical share),
13 (retirement plan) and an informational box 14 line for Sec. 125 benefits. Box 1 = gross −
pre-tax retirement − Sec. 125 items; boxes 3/5 = gross − Sec. 125 items (box 3 capped at the wage
base); box 6 includes the 0.9% employers withhold above $200,000. Projection = YTD + this period's
amount × pay dates left in the year (by pay date, which is what the W-2 follows); `--final` takes
YTD as the total.

**Pre-tax inference.** Social Security tax / 6.2% should equal gross minus the Sec. 125
deductions (medical, dental, vision, HSA, FSA, DCAP); 401(k) deferrals are still FICA wages.
When the stub's flags don't add up and exactly one set of deductions makes the arithmetic work,
that set is used and marked "inferred". ADP's "Your federal taxable wages this period" line is
checked the same way. Otherwise the stub's flags stay and a warning is shown.

**Signals** (dollar amount + explanation + citation): excess Social Security across two or more
employers (claimed on Schedule 3); 402(g) deferral room or excess per person across employers
(catch-up contributions are mentioned, not assumed: the stub has no age); HSA together with a
health FSA not known to be limited-purpose (Pub. 969); HSA, health FSA and dependent care over
the limit; dependent care benefits when the user says a spouse has no earned income (§129(b));
Additional Medicare Tax not withheld when combined Medicare wages exceed $200,000 single /
$250,000 MFJ / $125,000 MFS (Form 8959; informational). Household-level planning beyond these
signals is out of scope for the Community core.

**Limits table** (`payroll/limits.py`, re-check every year before release):

| Item | 2025 | 2026 | Source |
|---|---|---|---|
| Social Security wage base | 176,100 | 184,500 | SSA COLA fact sheets |
| 402(g) elective deferrals | 23,500 | 24,500 | IRS Notice 2024-80 / Notice 2025-67 |
| Catch-up 50+ / 60-63 | 7,500 / 11,250 | 8,000 / 11,250 | IRS Notice 2024-80 / Notice 2025-67 |
| HSA self-only / family | 4,300 / 8,550 | 4,400 / 8,750 | Rev. Proc. 2024-25 / Rev. Proc. 2025-19 |
| Health FSA salary reduction | 3,300 | 3,400 | Rev. Proc. 2024-40 / Rev. Proc. 2025-32 |
| Dependent care (DCAP), MFS half | 5,000 | 7,500 | IRC §129(a)(2)(A); P.L. 119-21 from 2026 |
| Additional Medicare threshold | 200k / 250k / 125k | same | IRC §3101(b)(2), §3102(f) |

[Founder: confirm each figure against the published notices before release.]

**Known limits.** Scanned stubs need OCR first. State taxes are passed through as printed,
not checked. Dependent care above the limit and group-term life above $50,000 are not
re-computed (the employer's own figures are used). Pay dates are assumed regular, with no extra
off-cycle runs (bonuses).

## 9. Investments: 1099 import, holdings snapshot and signals (`ledgerstitch/investments/`)

**Boundary.** The investments tool adds up what payers reported on 1099s and what a positions
export says, and labels where each total goes on the return. It computes **no income tax**: no
capital gain tax, no rate stacking, no credits. Outputs are labelled **"SUMMARY — totals from your
1099s and statements; not a tax return"**; the payer's 1099 (often corrected through March) is
the document of record. Files are parsed in memory and never stored. TINs (including EINs in the
form `12-3456789`) and account numbers are masked before parsing.

**Inputs** (recognized by content, not file name):
- composite 1099 statements, text-based PDF or text (Fidelity-, Schwab- and Robinhood-style
  layouts, plus a generic "label ... amount" fallback): 1099-DIV boxes 1a, 1b, 2a-2d, 3, 4, 5, 6,
  7, 8 (country, text), 12, 13; 1099-INT 1, 2, 3, 4, 6, 8, 9; 1099-MISC 2, 3, 4; 1099-NEC 1, 4;
  1099-R 1, 2a, 4, 7 (distribution code, text); the 1099-B summary per Form 8949 category (A-F,
  X): proceeds, cost basis, accrued market discount, wash sale loss disallowed, net gain;
- a 1099-B transaction CSV (one row per sale). Term comes from the term column, else from the
  dates (more than one year = long); covered status from its column, else "basis present". When
  the lots add up to a statement's 1099-B summary (proceeds per category within $1), they are the
  same account and counted once;
- a positions CSV (Fidelity, Schwab, Vanguard, Robinhood-style headers). Asset class is a guess
  from the symbol, description and the export's type column.

**Destinations** (informational): 1099-DIV 1a → Form 1040 line 3b, 1b → 3a, 2a → Schedule D
line 13, 5 → Form 8995, 7 → Schedule 3 line 1, 12 → line 2a; 1099-INT 1 → line 2b, 8 → line 2a;
1099-B box A/B/C → Schedule D lines 1a-1b/2/3, D/E/F → 8a-8b/9/10 through Form 8949; 1099-R 1/2a →
lines 4a-4b or 5a-5b; any box 4 → line 25b. Net gain per category = proceeds − cost + market
discount + wash sale disallowed (Form 8949 column g); a printed net that differs is kept and
flagged.

**Signals** (totals only, each with a citation): unrealized losses in the positions (sum and
count; §1211(b) $3,000 / $1,500 MFS limit); 1099-B proceeds with no basis, and noncovered
basis; wash sale losses the broker disallowed (§1091; brokers see one account only); foreign tax
paid (credit, without Form 1116 up to $300 / $600 MFJ under §904(j)); Section 199A REIT
dividends (20% of box 5, Form 8995); tax-exempt interest and its AMT part; nondividend
distributions (basis reduction); a likely Schedule K-1 for partnership units; investment and
1099 income of $1,000+ with nothing withheld (§6654 underpayment rules); net gains mostly short-term;
a net capital loss above the yearly limit; a 1099-R with distribution code 1 (§72(t)).

**Not in the Community core** (available in LedgerStitch Plus): FIFO / specific-lot gains from
trade history, wash sale detection across accounts and a spouse's, the tax-loss harvesting plan
with dates, target allocation, drift and rebalancing, and lot selection.

**Known limits.** Scanned statements need OCR first (or use the broker's CSV download). Lot
detail pages in PDFs are not read; use the 1099-B CSV. State boxes (14-16) and 1099-OID, 1099-K
and K-1s are not read. Positions exports rarely carry purchase dates, so unrealized losses are not
split into short- and long-term. [Founder: confirm the §904(j) and §199A thresholds each year.]

