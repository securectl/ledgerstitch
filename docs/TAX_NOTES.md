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
- It is **not tax preparation software**. It does not compute tax, produce IRS forms, or check for
  completeness or eligibility.
- It does **not e-file** or submit anything to the IRS, any state, or anyone else.
- It does **not give tax, legal or accounting advice**. Lanes, categories and Schedule C lines are
  suggestions for the user and their tax professional to review.
- It does not track trips, compute depreciation or home office, value inventory (COGS),
  handle accrual accounting, do payroll, or handle state-specific rules.
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
