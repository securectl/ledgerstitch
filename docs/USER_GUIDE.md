# LedgerStitch User Guide

LedgerStitch turns a year of bank and credit card statements into one clean, de-duplicated,
categorized report your CPA can use, plus QuickBooks files. It runs **only on your computer**.
There's no account to create and nothing is uploaded to us.

> LedgerStitch organizes your data for review. It is not tax advice or tax preparation software.
> Always confirm deductions with a qualified tax professional.

Contents: 1 Install & launch · 2 Create a workspace · 3 Download statements from your bank ·
4 Import · 5 Review (lanes explained) · 6 Rules · 7 AI settings · 8 Reports & your CPA ·
9 QuickBooks · 10 Troubleshooting · 11 FAQ

---

## 1. Install and launch

1. Download LedgerStitch from {{DOWNLOAD_URL}}.
   - **macOS:** open the DMG and drag LedgerStitch onto Applications.
   - **Windows:** run the Setup.exe. It installs for your user only, so you don't need admin
     rights.

   Until the apps are signed, macOS and Windows warn you the first time. See
   [INSTALL.md](INSTALL.md#until-the-apps-are-signed-gatekeeper-and-smartscreen) for the
   Gatekeeper and SmartScreen steps. Technical users can use the one-line installer
   (`sh install.sh`, or `install.ps1` on Windows). Small offices can self-host with Docker.
   Both are covered in [INSTALL.md](INSTALL.md).
2. Start **LedgerStitch**. Your web browser opens at an address like `http://127.0.0.1:53817`.
   That address is your own computer. The page isn't on the internet, and other devices can't
   reach it.
3. **First launch: a 3-step setup** (about a minute):
   1. **Welcome.** What LedgerStitch promises.
   2. **Setup check.** An automatic check of your computer, plus **local AI**. Click
      **Set up local AI** to see what it means (a 19 GB model, GLM-4.7-Flash, that runs on your
      computer and sees only merchant names) and **Agree and set up**. The free Ollama app and the
      model then download in the background while you carry on. Watch the "Local AI" pill in the
      top bar. Skip it and nothing is downloaded. You can remove the model or withdraw your OK
      later in **Settings → Privacy & AI**.
   3. **Create your vault.** Then click **Try with sample data** to explore a made-up business
      in a separate vault, or **Import my first statement**.

   To run it again later, go to **Settings → Setup → Run setup again**.
4. Keep the LedgerStitch window or terminal open while you work. Closing it stops the app.
   If the browser didn't open, copy the address shown in the window into your browser.

Command-line options (optional): `ledgerstitch --port 8765 --no-browser --data-dir ~/LedgerStitch`.
Workspaces are stored in `~/LedgerStitch` unless you choose another folder.

## 2. Create a workspace

A **workspace** is an encrypted folder for one business (bookkeepers: one per client).

1. Click **Create workspace** and enter a name (for example "Sam's Studio 2025").
2. Choose a **passphrase**. Use 4 or more random words or a long password, and save it in your
   password manager.

> **Important: we can't recover your passphrase.** Your data is encrypted with a key that only
> your passphrase (or your recovery key, below) can unlock. We never see either, and there's no
> reset link. If you lose both, the workspace can't be opened by anyone, us included. Write the
> passphrase down somewhere safe or store it in a password manager **before** you import anything.

3. Leave **Also create a recovery key** ticked (recommended). After you create the workspace,
   LedgerStitch shows the recovery key **once**: a code like `ABCD-EFGH-…` (32 letters and digits).
   Click **Copy** or **Download as text**, store it offline, tick **I've saved my recovery key**
   and click **Done**.

- The workspace locks automatically after 30 minutes of inactivity (you can change this in
  Settings). Click **Lock** when you step away.
- To change the passphrase, go to Settings → **Change passphrase**. You need the current one.
  Your recovery key keeps working after a passphrase change.

### Recovery key (optional)

The recovery key is a second way into a workspace, for the day you forget your passphrase.

- **Anyone who has the recovery key can open the workspace**, just like with the passphrase.
  Keep it offline: printed and filed with your tax papers, or in a password manager. Don't keep
  it in a file next to the workspace or email it to yourself.
- **Forgot your passphrase?** On the unlock screen, choose the workspace and click
  **Forgot your passphrase? Use your recovery key**. Enter the key and a **new** passphrase. The
  old passphrase stops working. The recovery key still works afterwards; if you think someone else
  may have seen it, create a new one.
- **Settings → Security → Recovery key**: create one for a workspace that doesn't have one,
  **Create a new recovery key** (the old key stops working immediately) or **Remove** it. Each
  needs your current passphrase.
- LedgerStitch doesn't keep a copy of the recovery key. If you lose both the passphrase and the
  recovery key, the data can't be recovered.

### Encrypted backups

Settings → **Your data** → **Back up now** downloads one `.lsbackup` file holding the whole
workspace. The file is **still encrypted**: it is a copy of the encrypted workspace folder, and
opening it needs the passphrase (or recovery key) the workspace had **when you made the backup**.
Copy it to an external drive or another safe place.

- **Restore:** on the unlock screen, click **Restore from a backup** and choose the `.lsbackup`
  file. It's added as a **new** workspace next to your others; nothing existing is overwritten.
  Unlock it with its passphrase or recovery key.
- **Reminder:** once a workspace has data, Settings shows when you last backed up, and a small
  banner appears if you never have or the last backup is more than 30 days old.
- Backups larger than 1 GB (for example many receipt photos) can't be restored through the app
  yet. Copy the workspace folder instead (see the FAQ about moving to a new computer).

## 3. Download statements from your bank

**CSV is best.** It's exact and fast. **PDF works** for most text-based statements (the ones you
can highlight text in). Scanned or image-only PDFs can't be read. Download the **full year**
(Jan 1–Dec 31) for **every** account with business activity, and include personal accounts if
business purchases ever landed on them.

> Bank websites change their menus often. These steps were accurate when written. If a button has
> moved, look for words like "Download", "Export", "Statements" or "Activity". In the bank's app,
> this option usually only exists on the website, not the mobile app.

**Chase (cards and checking)**
1. Sign in at chase.com and select the account.
2. Click the **Download account activity** icon (a down arrow) above the transaction list.
3. Choose **Activity**, then file type **CSV (Spreadsheet)**, then a date range (choose a custom range).
4. Click **Download**. For PDFs, go to **Statements & documents** instead.

**Bank of America**
1. Sign in, then open the account.
2. Above the transactions, click **Download** (sometimes under "Statements & Documents" → "Download
   transactions").
3. Choose the period, set **File type: Microsoft Excel (CSV)**, and click **Download Transactions**.

**Wells Fargo**
1. Sign in, then go to **Accounts → Download Account Activity** (on some layouts this is under the
   account's "…" menu).
2. Pick the account and date range, choose **Comma Delimited (CSV)**, and click **Download**.

**Citi**
1. Sign in and open the card.
2. Click **Download** (or "Export") above the transactions.
3. Choose a time period (for example "Last year" or a custom range), set the format to **CSV**,
   then click **Export**.

**Capital One**
1. Sign in and open the card or bank account.
2. Click **View more transactions**, then **Download Transactions**.
3. Choose **CSV** and the date range, then click **Export**. Capital One has two CSV layouts.
   LedgerStitch reads both.

**American Express**
1. Sign in, select the card, then go to **Statements & Activity**.
2. Click **Download** (the down arrow near the search bar).
3. Choose **CSV**, tick "Include all additional transaction details" if offered, pick the date
   range, and click **Download**.

**Discover**
1. Sign in, go to **Activity & Statements → Search Transactions** (or "All Activity").
2. Pick the date range and click **Download**, then choose **CSV**.

**U.S. Bank**
1. Sign in and open the account.
2. Click **Download transactions** (sometimes under "Manage account" or the account's menu).
3. Choose the dates and **CSV**, then click **Download**.

**Apple Card** (on iPhone, since there's no full website export)
1. Open **Wallet**, tap **Apple Card**, then tap **Card Balance**.
2. Under **Statements**, tap a month, then tap **Export Transactions** and choose **CSV**
   (not OFX).
3. Repeat for each month (you can't export a whole year at once), then AirDrop or email the files
   to your computer.

**Others** (Gemini, credit unions and so on): any CSV with a date, description and amount column
works. If LedgerStitch doesn't recognize it, it asks you which column is which (see 4.3).

## 4. Import

1. Go to the **Import** screen and drag all your files in at once, or click to choose them.
   You can mix CSV and PDF files.
2. LedgerStitch detects the bank and account type for each file and shows a summary: transactions
   found, account, and any warnings (for example "statement year inferred from period").
3. **Name your accounts** clearly, such as "Chase Ink ••4012 (business)". Clear names make the
   report and the CPA's job easier.
4. **Unrecognized CSV?** LedgerStitch shows the first rows and asks which column is the Date, the
   Description, and the Amount (or separate Debit and Credit columns). If you have a single amount
   column, also say whether negative means money out. Your mapping is applied to the whole file.
5. Importing the **same file twice is safe**. Identical transactions are recognized and not
   duplicated.

Automatically, after import:
- **Duplicates** (the same purchase in a CSV and a PDF, or overlapping date ranges) are kept once.
- **Card payments and transfers** between your own accounts are matched on both sides and moved to
  the Transfer lane, so a $2,000 card payment isn't counted on top of the purchases it paid for.
- **Refunds** are matched to the original purchase and netted.

## 5. Review: lanes explained

Every transaction lands in one **lane**:

| Lane | Plain English | Examples |
|---|---|---|
| **Business** | Spent for your business. Counts toward Schedule C totals. | Adobe subscription, Google Ads, shipping labels, a contractor you paid |
| **Personal** | Your own life. Kept out of business totals. | Groceries, Netflix, your dentist, a family dinner |
| **Transfer** | Money moving between your own accounts. Not spending and not income. | Paying your credit card from checking, moving money to savings, a matched refund |
| **Income** | Money coming in. | Stripe or Square payouts, client payments, your paycheck, bank interest |
| **Review** | Could be either, so you decide. | Gas, phone bill, flights, Home Depot, Zelle payments, insurance |

Each transaction also gets a **category** (such as "Office Supplies") and, when it's Business, an
IRS **Schedule C line** (such as line 18, Office expense).

**The Review queue** lists everything in the Review lane plus any low-confidence guesses.
- Use **Approve** if the suggestion is right, or change the category or lane.
- Tick **"Remember for this merchant"** to create a rule, so future transactions from that merchant
  are handled the same way.
- Add a **note** for anything your CPA should know. For meals, note *who* and the *business purpose*.
- The **Ledger** screen shows everything. Filter by lane, category, account or month, search, and
  bulk-edit.

Common judgement calls:
- **Amazon, Costco, Walmart, Target** default to Personal. If you buy business supplies there, change
  those rows, or add a rule limited to your business card (see 6).
- **Phone and internet:** only the business-use share is deductible. Mark them Business only if the
  line is dedicated to the business. Otherwise, tell your CPA the percentage.
- **Vehicle:** if your CPA uses the standard mileage rate, gas and repairs are not deducted
  separately (keep them Personal). Parking and tolls for business trips still count.
- **Home rent and utilities:** keep them Personal. The home office deduction is calculated
  separately by your CPA.
- **Federal income tax payments, health insurance, charity, investments and loan principal** are not
  Schedule C expenses. LedgerStitch keeps them out of business by default.
- **Meals** are generally only 50% deductible. LedgerStitch shows the full amount and a 50% estimate.

## 6. Rules

Rules tell LedgerStitch how to categorize a merchant automatically. They run before anything else,
including AI.
- Create them from the Review queue ("Remember for this merchant") or on the **Rules** screen.
- A rule matches text in the merchant name (or, optionally, the full description). It's not
  case-sensitive.
- **Account-specific rules** win over general ones. Example: "AMAZON → Personal/Shopping" in
  general, but "AMAZON on Chase Ink ••4012 → Business/Office Supplies".
- When several rules match, the **longest pattern wins** ("AMAZON WEB SERVICES" beats "AMAZON").
- Rules never overwrite a transaction you approved or edited yourself.
- Click **Categorize** again after adding rules to apply them to existing transactions.

## 7. AI settings and what the AI sees

AI is optional, and LedgerStitch works fully without it. When it's on, it only suggests
categories for merchants that your rules and the built-in keywords couldn't place.

**Options (Settings → Privacy & AI):**
- **Off:** rules and built-in keywords only.
- **Local AI:** GLM-4.7-Flash running in Ollama on your own computer (or in the `ollama` service
  when you self-host with Docker). Nothing leaves your machine. First-run setup offers it with a
  consent dialog; you can also start it from **Settings → Privacy & AI → Local AI**. It needs
  about 25 GB of disk and 24 GB of RAM. **Remove model** and **Withdraw consent** are there too.

**What the AI sees:** only cleaned-up merchant names, such as `NETFLIX.COM`, `ADOBE *CREATIVE CLD`
or `ZELLE TO <PERSON>`, plus the category list and the optional **business profile** you write
(for example "mobile dog groomer in Phoenix"). Don't put personal details in the profile.

**Never sent:** amounts, dates, balances, account or card numbers, your name, addresses, phone
numbers, emails, reference numbers, or how often you shop somewhere.

**Check it yourself:** Settings → **Preview what AI sees** shows the exact list that will be sent.
AI suggestions with low confidence go to the Review queue. AI can be wrong, so review its picks.

## 8. Reports and handing off to your CPA

The **Report** screen shows totals by lane, a Schedule C table, monthly charts and the
"Needs review" count. Choose the **tax year** at the top.

**Excel workbook (for your CPA).** Click **Export → Excel**. Tabs:
- **Summary:** totals by lane and the business name and year.
- **Schedule C:** each line's gross total, deductible % (meals 50%) and estimate.
- **Business, Personal, Needs Review:** the transactions behind each total.
- **Excluded:** card payments, transfers, income and duplicates, with the reason each was excluded.
- **All Transactions:** every row, with how it was categorized (you, a rule, a keyword or AI).
- **By Account:** totals per account, useful to spot a missing month.

**Before you send it:** work through this checklist: every account imported, full year covered, Review queue empty, Amazon/Costco/P2P
rows checked, meals noted, and your vehicle and home office records ready. Also gather the tax
forms you received. Payment processor deposits (Stripe, Square) are *net* of fees, so give your
CPA the processor's annual summary too.

**Sending it:** exports are regular, **unencrypted** files. Send them through your CPA's secure
portal rather than plain email, and delete stray copies from Downloads.

CSV export is also available for any other tool.

### Pay stubs: estimate your W-2 before January

Run `ledgerstitch paystub latest-stub.pdf` (add `--spouse their-stub.pdf` for a spouse, and one
stub per employer). LedgerStitch projects each stub to December 31 and shows an **estimated**
W-2. It also lists payroll "money found" signals, such as excess Social Security withheld when
you had two employers, how much 401(k) room is left this year, or an HSA alongside a
general-purpose FSA. Use `--final` with your last stub of the year, and `--out w2.xlsx` to save
a copy. Stubs are read in memory and not stored. SSNs and account numbers are masked in every
output. It's an estimate for planning: your employer's W-2 is the real one, and LedgerStitch
doesn't compute your income tax.

## 9. QuickBooks

**QuickBooks Online (bank upload CSV)**
1. In LedgerStitch, choose **Export → QuickBooks Online (CSV)**. With several accounts you get a
   `.zip` with one CSV per account. QuickBooks imports one account at a time.
2. In QuickBooks Online, go to **Transactions → Bank transactions** (in some versions,
   "Banking"), then click **Upload transactions** (or **Link account ▾ → Upload from file**).
3. Choose the CSV for one account and select the matching QuickBooks account (create it if needed).
4. Map the columns if asked (Date, Description, Amount, single column), then confirm.
5. Transactions appear under **For review** in QuickBooks. Categorize or match them there. The upload only
   carries Date, Description and Amount, so keep the LedgerStitch Excel workbook open as your guide.
6. Repeat for each account. Transfers are included on both sides so QuickBooks can match them as
   transfers. Don't add them as expenses.

**QuickBooks Desktop (IIF)**
1. **Back up your company file first** (File → Back Up Company). IIF imports can't be undone.
2. In LedgerStitch, choose **Export → QuickBooks Desktop (IIF)**. It creates the accounts it needs:
   expense accounts under "LedgerStitch:<Category>", income under "LedgerStitch Income", personal
   spending to "Owner Draw", and a clearing account for transfers. Your bookkeeper can rename or merge
   these afterwards.
3. In QuickBooks Desktop: **File → Utilities → Import → IIF Files**, choose the file, then review
   the import report.
4. Check account balances against your statements.

Ask your bookkeeper or accountant if you're unsure which QuickBooks accounts to use.

## 10. Troubleshooting

| Problem | Try this |
|---|---|
| Browser didn't open | Copy the `http://127.0.0.1:…` address from the LedgerStitch window into your browser. |
| "Port already in use" | Another copy is running. Close it, or start with a different `--port`. |
| Wrong passphrase | Check Caps Lock and keyboard layout. Passphrases are case-sensitive. There's no reset. |
| PDF shows 0 transactions | It's probably a scanned or image PDF. Download the CSV instead, or the "printable" text PDF. |
| Wrong year on PDF rows | Make sure the full statement (with the "statement period" header) was imported, not a single page. |
| CSV not recognized | Use the column mapping screen. Pick the Date, Description and Amount (or Debit and Credit) columns. |
| Amounts have the wrong sign (refunds look like purchases) | Re-import with mapping and flip "negative means money out". |
| Card payment counted as an expense | Import **both** accounts (the card and the checking account that paid it). LedgerStitch needs both sides to match. Or set the row to Transfer → Credit Card Payment. |
| Duplicates | Normally removed. If two different but identical purchases were merged (for example two $5 coffees the same day), check the Excluded tab of the Excel export and tell your CPA (or email support so we can improve matching). |
| AI "connection failed" | Make sure the Ollama app (or the `ollama` service in Docker) is running and the model has finished downloading. See Settings → Privacy & AI → Local AI. |
| macOS "can't be opened" / Windows "protected your PC" | The app isn't signed yet. See [INSTALL.md](INSTALL.md#until-the-apps-are-signed-gatekeeper-and-smartscreen). |
| Setup: Install Ollama failed | Nothing was installed. Get Ollama from ollama.com, open it, then click **Check again** (or Settings → Setup → Run setup again). |
| Totals look too high | Look for card payments not marked Transfer, a statement imported for the wrong year, or personal Amazon or Costco purchases marked Business. |

## 11. FAQ

**Do you see my financial data?** No. The Community edition keeps it on your computer (or your
own server), and local AI runs there too. It sees only cleaned-up merchant names.

**Do I need to connect my bank login?** No. You download statements yourself. We never ask for bank
credentials.

**Does LedgerStitch file my taxes or give tax advice?** No. It organizes your data for you and your
tax professional. It doesn't prepare or file returns.

**Is the Schedule C table my tax return?** No. It's a starting point for your CPA. Things like home
office, mileage, depreciation, inventory and 1099 reconciliation are handled by your tax preparer.

**Can I use it for more than one business?** Yes. Create one workspace per business or client.
There's no limit on workspaces, accounts or transactions.

**What if I forget my passphrase?** If you created a recovery key, use **Forgot your passphrase?
Use your recovery key** on the unlock screen and set a new passphrase. Without a recovery key the
data can't be recovered. Keep your original statements; you can always start a new workspace and
re-import.

**Can I move to a new computer?** Yes. Make a backup (Settings → Your data → **Back up now**),
install LedgerStitch on the new computer and use **Restore from a backup**. Or copy the workspace
folder (default `~/LedgerStitch`). Either way, open it with your passphrase or recovery key. Enter
your license key again if needed.

**Does it work offline?** Yes, completely, with AI off or using local AI. (Downloading the local
model the first time needs an internet connection.)

**What about paid add-ons?** Paid add-ons extend LedgerStitch through the plugin API. Everything
in this guide is free and stays free. See {{SITE_URL}} for the add-ons. If a license expires,
only the add-on's features lock again; your workspaces and data stay on your computer.

**Support:** {{SUPPORT_EMAIL}}. Please don't send real statements. If a file won't import, we'll
help you make a small fake sample.
