"""Build ledgerstitch/ingest/data/known_formats.json (research artifact, 2026-10-18)."""
import json, sys
from pathlib import Path

OUT = Path(sys.argv[1])

S = {  # sources
 "ls": "ledgerstitch/ingest/detect.py (profile built from real exports; see docs/SUPPORTED_BANKS.md)",
 "b2y": "https://github.com/bank2ynab/bank2ynab/blob/master/bank2ynab.conf",
 "c2o": "https://github.com/reubano/csv2ofx/tree/master/csv2ofx/mappings",
 "ff": "https://github.com/firefly-iii/import-configurations",
 "hl": "https://github.com/hledgerorg/hledger/tree/main/examples/csv",
 "reds": "https://github.com/redstreet/beancount_reds_importers/tree/main/beancount_reds_importers/importers",
 "tar_rev": "https://github.com/tarioch/beancounttools/blob/master/src/tariochbctools/importers/revolut/importer.py",
 "jbms_venmo": "https://github.com/jbms/beancount-import/blob/master/beancount_import/source/venmo.py",
 "mercury": "https://github.com/mtlynch/beancount-mercury/blob/master/beancount_mercury/checking_test.py",
 "mercury_help": "https://support.mercury.com/hc/en-us/articles/28768700685844-Exporting-transaction-data",
 "tiller_pp": "https://community.tiller.com/t/paypal-line-item-importer-not-recognizing-csv-file/4093",
 "pp_dev": "https://developer.paypal.com/docs/reports/online-reports/activity-download/",
 "shopify": "https://help.shopify.com/en/manual/payments/shopify-payments/getting-paid-with-shopify-payments/view-payouts/view-details",
 "amazon": "https://developer-docs.amazon/sp-api/docs/report-type-values-payment",
 "coinbase": "https://gitlab.com/c1560/cryptofiscafacile/-/issues/595",
 "wise_kmm": "https://mail.kde.org/pipermail/kmymoney/2023-March/004062.html",
 "wise_api": "https://docs.wise.com/api-docs/api-reference/balance-statement",
 "sofi": "https://support.sofi.com/hc/en-us/articles/12905841091597-Can-I-export-my-SoFi-Money-transactions",
 "relay": "https://support.sofi.com/hc/en-us/articles/39082354621837-How-do-I-export-my-transactions-from-Relay",
 "wf": "https://www.wellsfargo.com/help/online-banking/activity-faqs/",
 "wf_qb": "https://www.wellsfargo.com/biz/online-banking/software/quickbooks/",
 "amex": "https://www.americanexpress.com/us/customer-service/faq.download-export-transactions-software.html",
 "amex_hist": "https://www.americanexpress.com/us/customer-service/faq.transaction-billing-statements-history.html",
 "boa_qb": "https://www.bankofamerica.com/smallbusiness/online-banking/quickbooks/faqs/",
 "c1_ir": "https://content.capitalone.com/TMgmt/images/Info_Reporting_Ref_Guide.pdf",
 "c1_qb": "https://content.capitalone.com/TMgmt/images/QuickBooks_Mac_2013_2016.pdf",
 "c1_hist": "https://www.capitalone.com/help-center/checking-savings/transaction-history/",
 "discover": "https://www.discover.com/mobile/data/footer/faq_txn.html",
 "discover_ofx": "https://www.discover.com/discover/data/customer/help/amm_body.shtml",
 "brex": "https://help.finaloop.com/en/articles/14319038-brex-uploading-your-transactions-or-bank-statements-csv-or-pdf-to-finaloop",
 "square": "https://community.squareup.com/t5/Product-Updates/Improvement-Transactions-Report-Enhancements/m-p/639958",
 "square_gl": "https://square.com/help/gb/en/article/5200-square-glossary",
 "stripe": "https://docs.stripe.com/reports/report-types/balance",
 "ally": "https://tiller.com/export-ally-bank-transactions/",
 "nfcu": "https://www.docuclipper.com/blog/convert-navy-federal-bank-statement-to-excel/",
 "chime": "https://docuclipper.com/blog/convert-chime-bank-statement-to-excel",
 "chase": "https://statementdecoder-web.onrender.com/convert/chase",
 "ofx": "https://financialdataexchange.org/FDX/About/OFX-Work-Group.aspx",
 "camt": "https://www.iso20022.org/iso-20022-message-definitions",
 "mt940": "https://www2.swift.com/knowledgecentre/products/Standards%20MT",
 "bai2": "https://www.bai.org/docs/default-source/libraries/site-general-downloads/cash_management_2005.pdf",
 "td_bai": "https://www.td.com/content/dam/tdcom/canada/commercial-banking/en/pdfs/wbb/bai.pdf",
}

def sig(h):
    return None if h is None else "|".join(" ".join(c.strip().lower().split()) for c in h)

E = []
def add(id, institution, product, fmt, header=None, date_format=None, amount=None, debit=None, credit=None,
        sign=None, type_column=None, type_values=None, card=None, balance=None, skip=0, encoding="utf-8",
        src="", verified=False, notes="", supported=False, headerless_columns=None):
    E.append(dict(id=id, institution=institution, product=product, format=fmt, header=header,
                  header_signature=sig(header), headerless_columns=headerless_columns,
                  date_format=date_format, amount_columns={"amount": amount, "debit": debit, "credit": credit},
                  sign_convention=sign, type_column=type_column, type_values=type_values, card_column=card,
                  balance_column=balance, skip_rows_hint=skip, encoding=encoding, source_url=src,
                  verified=verified, supported_today=supported, notes=notes))

US = "%m/%d/%Y"
# ---------------------------------------------------------------- already supported (detect.py)
add("chase_card", "Chase", "credit_card", "csv",
    ["Card","Transaction Date","Post Date","Description","Category","Type","Amount","Memo"], US, amount="Amount",
    sign="negative_is_out", type_column="Type", type_values={"Sale":"out","Return":"in","Payment":"in","Adjustment":"either","Fee":"out"},
    card="Card", encoding="utf-8-sig", src=S["ls"] + " ; shape also in " + S["b2y"], verified=True, supported=True,
    notes="'Card' column only on multi-card/business accounts; files may carry a UTF-8 BOM. Filename ChaseNNNN_Activity*.CSV gives last4.")
add("chase_card_nocard", "Chase", "credit_card", "csv",
    ["Transaction Date","Post Date","Description","Category","Type","Amount","Memo"], US, amount="Amount",
    sign="negative_is_out", type_column="Type", src=S["b2y"] + " ([US Chase Credit Card 2019], 7 columns)", verified=True, supported=True,
    notes="Same profile id chase_card in detect.py.")
add("chase_checking", "Chase", "checking", "csv",
    ["Details","Posting Date","Description","Amount","Type","Balance","Check or Slip #"], US, amount="Amount",
    sign="negative_is_out", type_column="Type", type_values={"ACH_DEBIT":"out","ACH_CREDIT":"in","DEBIT_CARD":"out","LOAN_PMT":"out","QUICKPAY_DEBIT":"out","QUICKPAY_CREDIT":"in","CHECK_PAID":"out","FEE_TRANSACTION":"out","ACCT_XFER":"either","MISC_CREDIT":"in","DEPOSIT":"in"},
    balance="Balance", src=S["ls"], verified=True, supported=True,
    notes="Data rows have a trailing comma (one more cell than header). Details = DEBIT/CREDIT/CHECK/DSLIP. Zelle shows as QUICKPAY_*; debit-card rows live here.")
add("citi", "Citi", "credit_card", "csv", ["Status","Date","Description","Debit","Credit"], US, debit="Debit", credit="Credit",
    sign="split_columns", src=S["ls"] + " ; " + S["ff"] + " (us/citi)", verified=True, supported=True,
    notes="Credit may be written negative. Some newer exports append 'Member Name' (unverified). Status = Cleared/Pending.")
add("capital_one_card", "Capital One", "credit_card", "csv",
    ["Transaction Date","Posted Date","Card No.","Description","Category","Debit","Credit"], "%Y-%m-%d", debit="Debit", credit="Credit",
    sign="split_columns", card="Card No.", src=S["c2o"] + " (capitalone.py) ; " + S["hl"] + " (banking/capital-one.csv.rules)", verified=True, supported=True,
    notes="ISO dates. Card No. = last4 of the physical card (authorized users differ).")
add("capital_one_bank_legacy", "Capital One", "checking", "csv", ["Date","Transaction","Name","Memo","Amount"], "%Y-%m-%d", amount="Amount",
    sign="negative_is_out", type_column="Transaction", type_values={"DEBIT":"out","CREDIT":"in"}, src=S["ls"], verified=True, supported=True,
    notes="Identical header to US Bank; disambiguate by ISO dates / filename. detect.py id: capital_one.")
add("capital_one_360", "Capital One", "checking", "csv",
    ["Account Number","Transaction Date","Transaction Amount","Transaction Type","Transaction Description","Balance"], "%m/%d/%y",
    amount="Transaction Amount", sign="type_column", type_column="Transaction Type", type_values={"Debit":"out","Credit":"in"}, balance="Balance",
    src=S["ls"], verified=False, supported=True,
    notes="detect.py requires the four 'Transaction *' columns (verified). Account Number/Balance columns and %m/%d/%y date are from memory - unverified. Amount is unsigned; Transaction Type decides.")
add("us_bank", "US Bank", "checking", "csv", ["Date","Transaction","Name","Memo","Amount"], US, amount="Amount",
    sign="negative_is_out", type_column="Transaction", src=S["ls"], verified=True, supported=True,
    notes="All cells quoted; Memo starts 'Download from usbank.com.'. Same header for US Bank credit cards.")
add("gemini_card", "Gemini", "credit_card", "csv",
    ["Reference Number","Transaction Post Date","Description of Transaction","Transaction Type","Amount"], "%m/%d/%y", amount="Amount",
    sign="positive_is_out", type_column="Transaction Type", type_values={"clearing":"out","payment_transaction":"in","purchase_return":"in"},
    src=S["ls"], verified=True, supported=True, notes="Empty description on payment rows -> use Transaction Type.")
add("apple_card", "Apple Card (Goldman Sachs)", "credit_card", "csv",
    ["Transaction Date","Clearing Date","Description","Merchant","Category","Type","Amount (USD)","Purchased By"], US, amount="Amount (USD)",
    sign="positive_is_out", type_column="Type", type_values={"Purchase":"out","Payment":"in","Credit":"in","Installment":"out","Interest":"out","Debit":"out"},
    card="Purchased By", src=S["ls"], verified=True, supported=True,
    notes="Monthly statement exports only (not arbitrary ranges). 'Purchased By' identifies Apple Card Family members. Card program moving from Goldman to JPMorgan Chase - watch for format change.")
add("discover_card", "Discover", "credit_card", "csv", ["Trans. Date","Post Date","Description","Amount","Category"], US, amount="Amount",
    sign="positive_is_out", src=S["reds"] + "/discover/__init__.py (header_identifier)", verified=True, supported=True,
    notes="Up to 24 months viewable online (" + S["discover"] + ").")
add("boa_card", "Bank of America", "credit_card", "csv", ["Posted Date","Reference Number","Payee","Address","Amount"], US, amount="Amount",
    sign="negative_is_out", src=S["ls"] + " ; 5-col shape in " + S["b2y"], verified=True, supported=True,
    notes="Filename like October2018_9999.csv (month+year_last4).")
add("boa_checking", "Bank of America", "checking", "csv", ["Date","Description","Amount","Running Bal."], US, amount="Amount",
    sign="negative_is_out", balance="Running Bal.", skip=6, src=S["ls"] + " ; " + S["b2y"] + " ([US Bank of America] Header Rows = 7)", verified=True, supported=True,
    notes="Summary block first: 'Description,,Summary Amt.' / Beginning balance / Total credits / Total debits / Ending balance, blank line, then header. First data row is 'Beginning balance as of' with empty Amount.")
add("amex", "American Express", "credit_card", "csv", ["Date","Description","Amount"], US, amount="Amount",
    sign="positive_is_out", src=S["ls"], verified=True, supported=True,
    notes="Bare 3-col layout is ambiguous; only claim when filename says Amex. Help center: CSV/Excel/Quicken/QuickBooks, ~2 years of transactions (" + S["amex_hist"] + ").")
add("amex_member", "American Express", "credit_card", "csv", ["Date","Description","Card Member","Account #","Amount"], US, amount="Amount",
    sign="positive_is_out", card="Account #", src=S["ls"], verified=True, supported=True,
    notes="Account # is '-NNNNN' (last5). Card Member identifies employee/authorized-user cards.")
add("amex_extended", "American Express", "credit_card", "csv",
    ["Date","Description","Amount","Extended Details","Appears On Your Statement As","Address","City/State","Zip Code","Country","Reference","Category"], US,
    amount="Amount", sign="positive_is_out", src=S["ls"], verified=True, supported=True,
    notes="Quoted multiline fields (Extended Details, City/State contain newlines). Business cards add 'Card Member','Account #' (unverified order).")
add("wells_fargo", "Wells Fargo", "checking", "csv", None, US, amount="col1", sign="negative_is_out",
    headerless_columns=["date","amount","star","check_number","description"],
    src=S["hl"] + " (banking/wellsfargo-checking.csv.rules) ; " + S["ff"] + " (us/wellsfargo)", verified=True, supported=True,
    notes="NO header row. Every cell quoted. Same layout for WF credit cards (sign also negative=purchase). Up to 18 months downloadable; default 90 days (" + S["wf"] + ").")

# ---------------------------------------------------------------- new CSV layouts: banks & credit unions
add("schwab_bank_checking", "Charles Schwab Bank", "checking", "csv",
    ["Date","Status","Type","CheckNumber","Description","Withdrawal","Deposit","RunningBalance"], US, debit="Withdrawal", credit="Deposit",
    sign="split_columns", type_column="Type", balance="RunningBalance", skip=0,
    src=S["reds"] + "/schwab/schwab_csv_checking.py ; " + S["c2o"] + " (schwabchecking.py)", verified=True,
    notes="Status = Posted/Pending - drop Pending. Amounts like '$1,234.56'. Older exports had 3 preamble rows (bank2ynab Header Rows = 3). Filename XXXXXX123_Checking_Transactions_YYYYMMDD-HHMMSS.csv.")
add("schwab_bank_savings", "Charles Schwab Bank", "savings", "csv",
    ["Date","Status","Type","CheckNumber","Description","Withdrawal","Deposit","RunningBalance"], US, debit="Withdrawal", credit="Deposit",
    sign="split_columns", balance="RunningBalance", src=S["b2y"] + " ([US Schwab Savings]) ; " + S["reds"], verified=False,
    notes="Assumed identical to checking (bank2ynab lists same 7-col shape for both); header text unverified for savings.")
add("schwab_pal", "Charles Schwab Bank", "checking", "csv",
    ["Date","TransactionType","CheckNumber","Description","Credits","Debits","RunningBalance"], US, debit="Debits", credit="Credits",
    sign="split_columns", type_column="TransactionType", balance="RunningBalance", src=S["reds"] + "/schwab/schwab_csv_creditline.py", verified=True,
    notes="Pledged Asset Line / credit line export. NOTE Credits column comes BEFORE Debits.")
add("dcu_checking", "Digital Federal Credit Union (DCU)", "checking", "csv",
    ["DATE","TRANSACTION TYPE","DESCRIPTION","AMOUNT","ID","MEMO","CURRENT BALANCE"], US, amount="AMOUNT", sign="negative_is_out",
    type_column="TRANSACTION TYPE", balance="CURRENT BALANCE", src=S["reds"] + "/dcu/__init__.py", verified=True,
    notes="All-caps header. Filename *Account_Transactions*.csv.")
add("usaa_legacy", "USAA", "checking", "csv", None, US, amount="col6", sign="negative_is_out",
    headerless_columns=["status","blank","date","original_description","description","category","amount"],
    src=S["b2y"] + " ([US USAA], Header Rows = 0)", verified=False,
    notes="Legacy bk_download.csv, no header; column count/positions verified by bank2ynab, column meanings partly inferred.")
add("usaa_2023", "USAA", "checking", "csv", ["Date","Description","Original Description","Category","Amount","Status"], "%Y-%m-%d",
    amount="Amount", sign="negative_is_out", src="unverified (forum recollection)", verified=False,
    notes="Newer export (approx. 2023+). Status = Posted/Pending. Unverified - confirm with a real file. Same layout reported for USAA credit cards.")
add("ally_bank", "Ally Bank", "checking", "csv", ["Date","Time","Amount","Type","Description"], "%Y-%m-%d", amount="Amount",
    sign="negative_is_out", type_column="Type", type_values={"Withdrawal":"out","Deposit":"in"}, src=S["ally"] + " ; https://beancount.io/tools/csv-to-beancount/ally", verified=False,
    notes="Header has a space after each comma (' Time', ' Amount') - strip header cells. 18 months max. Third-party sources only; Tiller also mentions a Balance column.")
add("navy_federal", "Navy Federal Credit Union", "checking", "csv",
    ["Posting Date","Transaction Date","Amount","Credit Debit Indicator","type","Type Group","Reference","Instructed Currency","Currency Exchange Rate","Instructed Amount","Description","Category","Check Serial Number","Card Ending"],
    US, amount="Amount", sign="type_column", type_column="Credit Debit Indicator", type_values={"Debit":"out","Credit":"in"}, card="Card Ending",
    src=S["nfcu"], verified=False,
    notes="UNVERIFIED header (2023+ redesign, recalled from user reports). CSV/OFX/QIF offered, ~18 months (third-party source). Amount unsigned; indicator column decides.")
add("td_bank_us", "TD Bank (US)", "checking", "csv",
    ["Date","Bank RTN","Account Number","Transaction Type","Description","Debit","Credit","Check Number","Account Running Balance"], US,
    debit="Debit", credit="Credit", sign="split_columns", type_column="Transaction Type", balance="Account Running Balance",
    src="unverified", verified=False, notes="UNVERIFIED (recollection). bank2ynab [US TB Bank] shows a 10-col headerless variant: Date,_,_,_,Payee,Outflow,Inflow,_,_,_ (" + S["b2y"] + ").")
add("truist_bbt", "Truist (formerly BB&T)", "checking", "csv", None, US, amount="col4", sign="negative_is_out",
    headerless_columns=["date","?","?","description","amount","?"], skip=1, src=S["b2y"] + " ([US BB&T], EXPORT.CSV, Header Rows = 1)", verified=False,
    notes="Has a 1-line header but its text is not public; 6 columns with Date at 0, Payee at 3, signed amount at 4. Truist also offers QFX/QBO (unverified).")
add("pnc", "PNC Bank", "checking", "csv", None, US, sign=None, src="https://tiller.com/export-pnc-bank-transactions/", verified=False,
    notes="Header not publicly documented. Historically: first row is an account summary line (account number, date range, balances) followed by HEADERLESS rows date,amount,description,memo,ref,type(DEBIT/CREDIT) - unverified. Virtual Wallet newer export reportedly 'Date,Description,Withdrawals,Deposits,Balance' - unverified.")
add("first_hawaiian_bank", "First Hawaiian Bank", "checking", "csv", None, "%m/%d/%Y", debit="col4", credit="col5", sign="split_columns",
    headerless_columns=["account_number","date","check","description","debit","credit","status","balance"], skip=1,
    src=S["hl"] + " (banking/first-hawaiian-bank.rules)", verified=False,
    notes="Has header; column ORDER verified via hledger rules, header TEXT not published.")
add("unify_fcu", "UNIFY Financial Credit Union", "checking", "csv", None, "%m/%d/%Y", debit="col4", credit="col5", sign="split_columns",
    headerless_columns=["account","date","code","description","amount_out","amount_in","status","balance"], skip=1,
    src=S["hl"] + " (banking/_unify.rules)", verified=False, notes="AccountHistory*.csv; order verified, header text not published.")
add("western_fcu", "Western Federal Credit Union (Digital Insight era)", "checking", "csv",
    ["account","date","amount","balance","category","description","memo","notes"], "%m/%d/%Y", amount="amount", sign="negative_is_out",
    balance="balance", src=S["hl"] + " (banking/western-checking.csv.rules)", verified=False,
    notes="Amounts like '($19.47)' and '$75.24' (parentheses = negative). Header seen in rules comments; casing varies between exports.")
add("veridian_cu", "Veridian Credit Union", "checking", "csv", None, "%m/%d/%Y", sign="negative_is_out", skip=1,
    headerless_columns=["id","transaction_date","posted_date","?","amount","?","id2","?","category","tags","?","note","description"],
    src=S["ff"] + " (us/veridian-cu)", verified=False, notes="13-column export; order from Firefly roles, header text unknown.")
add("nc_secu", "State Employees' Credit Union (NC)", "checking", "csv", None, "%m/%d/%Y", debit="col4", credit="col3", sign="split_columns", skip=1,
    headerless_columns=["date","?","description","credit","debit"], src=S["ff"] + " (us/nc-secu)", verified=False,
    notes="Credit column precedes debit. Header text unknown.")
add("credit_union_generic_c2o", "Generic credit union (csv2ofx 'creditunion')", "checking", "csv", ["Date","Description","Comments","Check Number","Amount"], US,
    amount="Amount", sign="negative_is_out", src=S["c2o"] + " (creditunion.py)", verified=False,
    notes="csv2ofx only names the columns (Date, Amount, Description, Comments, Check Number); order assumed.")
for plat, n in [("Alkami", "alkami"), ("Q2", "q2"), ("Jack Henry Banno", "banno"), ("Fiserv (Digital Insight / Architect)", "fiserv")]:
    add(f"cu_platform_{n}", f"Credit unions on {plat}", "checking", "csv", None, None, src="unverified", verified=False,
        notes=f"{plat}-hosted online banking typically offers CSV + QFX/QBO/OFX (+ sometimes QIF) downloads with a platform-standard header, but each FI can customize; no public header spec found. Rely on generic detector; prefer QFX/OFX when offered.")
add("barclays_us_card", "Barclays US", "credit_card", "csv", ["Transaction Date","Description","Category","Amount"], US, amount="Amount",
    sign="negative_is_out", skip=4, src="unverified", verified=False,
    notes="UNVERIFIED (recollection): preamble lines 'Barclays Bank Delaware', 'Account Number: XXXXXXXXXXXX1234', 'Account Balance as of ...', blank, then header.")
add("synchrony_card", "Synchrony Bank (store cards)", "credit_card", "csv", ["Transaction Date","Posting Date","Reference Number","Description","Amount"], US,
    amount="Amount", sign="positive_is_out", src="unverified", verified=False, notes="UNVERIFIED. Many store cards are PDF-statement only.")
for inst, n in [("Fifth Third", "fifth_third"), ("KeyBank", "keybank"), ("Regions", "regions"), ("Huntington", "huntington"),
                ("Citizens", "citizens"), ("M&T Bank", "mt_bank"), ("PenFed Credit Union", "penfed"), ("Discover Bank", "discover_bank")]:
    add(f"{n}_checking", inst, "checking", "csv", None, None, src="unverified", verified=False,
        notes="No public header row found (bank2ynab/csv2ofx/Firefly/hledger/beancount have none). CSV + QFX/QBO believed available; use generic detector and collect a real sample.")
add("mint_export", "Mint (Intuit, discontinued 2024)", "checking", "csv",
    ["Date","Description","Original Description","Amount","Transaction Type","Category","Account Name","Labels","Notes"], US, amount="Amount",
    sign="type_column", type_column="Transaction Type", type_values={"debit":"out","credit":"in"},
    src=S["c2o"] + " (mint.py) ; " + S["b2y"] + " ([Mint])", verified=True,
    notes="Multi-account aggregator export (Account Name per row). Amount always positive.")

# ---------------------------------------------------------------- fintech / neobanks
add("mercury", "Mercury", "checking", "csv", ["Date (UTC)","Description","Amount","Status","Bank Description","Reference","Note"], "%m-%d-%Y",
    amount="Amount", sign="negative_is_out", src=S["mercury"] + " ; " + S["mercury_help"], verified=True,
    notes="Legacy header used 'Date'. Status Failed rows must be dropped. Newer exports add columns (Last Four Digits, Name On Card, Category, GL Code, Timestamp, Original Currency, Check Number, Tags...) - unverified. CSV/XLSX from Transactions; QuickBooks/NetSuite CSV from statements.")
add("mercury_xlsx", "Mercury", "checking", "xlsx", None, None, src=S["mercury_help"], verified=True, notes="Transactions page offers .xlsx with same columns as CSV.")
add("sofi_money", "SoFi", "checking", "csv", None, None, src=S["sofi"], verified=False,
    notes="CSV export verified (web only, max 2-year range). Header not published; reported 'Date,Description,Type,Amount,Current balance,Status' - unverified.")
add("relay", "Relay (business banking)", "checking", "csv", None, None, src=S["relay"], verified=False, notes="CSV export documented; header not published.")
add("brex", "Brex", "credit_card", "csv", None, None, src=S["brex"], verified=False,
    notes="Account Activity -> filter -> CSV (emailed). Customizable CSV columns, so no fixed header - map by name.")
for inst, n, prod, note in [("Ramp", "ramp", "credit_card", "CSV export with configurable columns; ERP sync preferred."),
                            ("Novo", "novo", "checking", "CSV + PDF statements reported."),
                            ("Bluevine", "bluevine", "checking", "CSV + PDF reported."),
                            ("Lili", "lili", "checking", "PDF statements; CSV unclear."),
                            ("Found", "found", "checking", "CSV export reported."),
                            ("Axos Bank", "axos", "checking", "CSV/QFX/QBO reported."),
                            ("Varo", "varo", "checking", "PDF statements; CSV not offered (reported)."),
                            ("Current", "current", "checking", "PDF statements; CSV unclear.")]:
    add(f"{n}", inst, prod, "csv", None, None, src="unverified", verified=False, notes="Header not public. " + note)
add("chime_pdf", "Chime", "checking", "pdf", None, None, src=S["chime"], verified=False,
    notes="Monthly PDF statements are the only native export (third-party claim; one source disagrees). Credit Builder has separate PDFs.")
add("revolut", "Revolut", "checking", "csv",
    ["Type","Product","Started Date","Completed Date","Description","Amount","Fee","Currency","State","Balance"], "%Y-%m-%d %H:%M:%S",
    amount="Amount", sign="negative_is_out", type_column="Type", balance="Balance", src=S["tar_rev"] + " ; " + S["b2y"] + " ([Revolut])", verified=True,
    notes="Fee is a separate positive column (subtract). State = COMPLETED/PENDING/REVERTED. One file per currency pocket. Older GB exports were ';'-delimited with split Paid Out/Paid In (" + S["ff"] + " gb/revolut).")
add("wise_statement", "Wise", "checking", "csv",
    ["TransferWise ID","Date","Amount","Currency","Description","Payment Reference","Running Balance","Exchange From","Exchange To","Exchange Rate","Payer Name","Payee Name","Payee Account Number","Merchant","Card Last Four Digits","Card Holder Full Name","Attachment","Note","Total fees"],
    "%d-%m-%Y", amount="Amount", sign="negative_is_out", card="Card Last Four Digits", balance="Running Balance", src=S["wise_kmm"], verified=True,
    notes="Balance statement CSV (per currency). Date format varies by locale (dd-mm-yyyy seen) - unverified. Wise also offers JSON/PDF/XLSX/CAMT.053/MT940/QIF statements (" + S["wise_api"] + ").")
add("wise_transfer_history", "Wise", "checking", "csv",
    ["ID","Status","Direction","Created on","Finished on","Source fee amount","Source fee currency","Target fee amount","Target fee currency","Source name","Source amount (after fees)","Source currency","Target name","Target amount (after fees)","Target currency","Exchange rate","Reference","Batch"],
    "%Y-%m-%d %H:%M:%S", amount="Source amount (after fees)", sign="type_column", type_column="Direction", type_values={"OUT":"out","IN":"in","NEUTRAL":"transfer"},
    src=S["hl"] + " (other/Wise.rules) ; " + S["b2y"] + " ([Wise])", verified=False,
    notes="transaction-history.csv. Column ORDER verified (hledger); header capitalization inferred.")
for f in ("camt053", "mt940", "qif", "xlsx", "pdf"):
    add(f"wise_{f}", "Wise", "checking", f, None, None, src=S["wise_api"], verified=True,
        notes=f"Wise balance statements are available as {f.upper()} (API + web).")

# ---------------------------------------------------------------- payment platforms
PAYPAL_FULL = ["Date","Time","TimeZone","Name","Type","Status","Currency","Gross","Fee","Net","From Email Address","To Email Address","Transaction ID","Shipping Address","Address Status","Item Title","Item ID","Shipping and Handling Amount","Insurance Amount","Sales Tax","Option 1 Name","Option 1 Value","Option 2 Name","Option 2 Value","Reference Txn ID","Invoice Number","Custom Number","Quantity","Receipt ID","Balance","Address Line 1","Address Line 2/District/Neighborhood","Town/City","State/Province/Region/County/Territory/Prefecture/Republic","Zip/Postal Code","Country","Contact Phone Number","Subject","Note","Country Code","Balance Impact"]
add("paypal_activity", "PayPal", "payment_platform", "csv", PAYPAL_FULL, US, amount="Net", sign="negative_is_out",
    type_column="Balance Impact", type_values={"Debit":"out","Credit":"in","Memo":"ignore"}, balance="Balance", encoding="utf-8-sig",
    src=S["hl"] + " (other/paypal-2018.csv.rules) ; " + S["tiller_pp"], verified=True,
    notes="Gross/Fee/Net: Fee is negative. Rows with Balance Impact=Memo (authorizations, currency-conversion legs, 'General Card Deposit') must not be double-counted. Up to 12 months per request, 7 years back, 50k rows/file (" + S["pp_dev"] + "). Non-US locales translate headers.")
add("paypal_activity_personal", "PayPal", "payment_platform", "csv",
    ["Date","Time","TimeZone","Name","Type","Status","Currency","Amount","Receipt ID","Balance"], US, amount="Amount", sign="negative_is_out",
    balance="Balance", src=S["tiller_pp"] + " ; " + S["hl"] + " (other/paypal-2023.rules)", verified=True,
    notes="Simplified personal-account layout (2023+). hledger example uses %d-%m-%Y (non-US locale).")
add("venmo_statement", "Venmo", "payment_platform", "csv",
    ["","ID","Datetime","Type","Status","Note","From","To","Amount (total)","Amount (tip)","Amount (tax)","Amount (fee)","Tax Rate","Tax Exempt","Funding Source","Destination","Beginning Balance","Ending Balance","Statement Period Venmo Fees","Terminal Location","Year to Date Venmo Fees","Disclaimer"],
    "%Y-%m-%dT%H:%M:%S", amount="Amount (total)", sign="negative_is_out", balance="Ending Balance", skip=2,
    src=S["hl"] + " (other/Venmo.rules) ; " + S["jbms_venmo"], verified=True,
    notes="Leading empty column. 2 title lines ('Account Statement - (@user) ...','Account Activity') before header; a beginning-balance row after it and a disclaimer/footer at the end. Amount (total) like '- $12.00' / '+ $30.00'. Funding Source != 'Venmo balance' means a linked bank/card also moved (avoid double-count). Columns changed over time.")
add("cash_app", "Cash App", "payment_platform", "csv",
    ["Transaction ID","Date","Transaction Type","Currency","Amount","Fee","Net Amount","Asset Type","Asset Price","Asset Amount","Status","Notes","Name of sender/receiver","Account"],
    "%Y-%m-%d %H:%M:%S %Z", amount="Net Amount", sign="negative_is_out", type_column="Transaction Type", src="unverified", verified=False,
    notes="UNVERIFIED (recollection; no public spec found). Amounts may be '$-12.00'. Date includes timezone abbreviation.")
add("stripe_balance_itemized", "Stripe", "payment_platform", "csv",
    ["balance_transaction_id","created_utc","available_on_utc","currency","gross","fee","net","reporting_category","description"],
    "%Y-%m-%d %H:%M:%S", amount="net", sign="negative_is_out", type_column="reporting_category",
    type_values={"charge":"in","refund":"out","payout":"transfer","fee":"out","dispute":"out","adjustment":"either"},
    src=S["hl"] + " (other/stripe.csv) ; " + S["stripe"], verified=True,
    notes="'Balance change from activity' itemized report, default columns. Currency lowercase. Payouts appear as negative 'payout' rows - pair with bank deposit, don't count as expense. Columns are configurable.")
add("square_transactions", "Square", "payment_platform", "csv",
    ["Date","Time","Time Zone","Gross Sales","Discounts","Service Charges","Net Sales","Gift Card Sales","Tax","Tip","Partial Refunds","Total Collected","Source","Card","Card Entry Methods","Cash","Square Gift Card","Other Tender","Other Tender Type","Other Tender Note","Fees","Net Total","Transaction ID","Payment ID","Card Brand","PAN Suffix","Device Name","Staff Name","Staff ID","Details","Description","Event Type","Location","Dining Option","Customer ID","Customer Name","Customer Reference ID","Device Nickname","Deposit ID","Deposit Date","Deposit Details","Fee Percentage Rate","Fee Fixed Rate","Refund Reason","Discount Name","Transaction Status","Cash App","Order Reference ID","Fulfillment Note","Free Processing Applied","Channel","Unattributed Tips"],
    US, amount="Net Total", sign="negative_is_out", src=S["square"] + " ; " + S["square_gl"], verified=False,
    notes="First 14 columns seen in a Square community post; remainder recalled - UNVERIFIED and changes often (e.g. 'Free Processing Applied' added 2023). Money as '$1,234.56'; Fees negative. Time zone follows the exporting device. Payouts not in this file (see Balance/Transfers export).")
add("shopify_payments_transactions", "Shopify Payments", "payment_platform", "csv",
    ["Transaction Date","Type","Order","Card Brand","Card Source","Payout Status","Payout Date","Available On","Amount","Fee","Net"],
    "%Y-%m-%d %H:%M:%S %z", amount="Net", sign="negative_is_out", type_column="Type",
    type_values={"charge":"in","refund":"out","dispute":"out","reserve":"either","adjustment":"either","payout":"transfer","payout_failure":"transfer"},
    src=S["shopify"], verified=True,
    notes="Column list from Shopify help (verified). Newer exports append Checkout, Payment Method Name, Presentment Amount, Presentment Currency, Currency (unverified). Payout Date for pending rows is an estimate. Date format unverified.")
AMZ = ["date/time","settlement id","type","order id","sku","description","quantity","marketplace","account type","fulfillment","order city","order state","order postal","tax collection model","product sales","product sales tax","shipping credits","shipping credits tax","gift wrap credits","giftwrap credits tax","Regulatory Fee","Tax On Regulatory Fee","promotional rebates","promotional rebates tax","marketplace withheld tax","selling fees","fba fees","other transaction fees","other","total"]
add("amazon_seller_date_range", "Amazon Seller Central", "payment_platform", "csv", AMZ, "%b %d, %Y %I:%M:%S %p %Z", amount="total",
    sign="negative_is_out", type_column="type", type_values={"Order":"in","Refund":"out","Service Fee":"out","FBA Inventory Fee":"out","Transfer":"transfer","Adjustment":"either"},
    skip=7, src=S["amazon"], verified=False,
    notes="Field set matches SP-API GET_DATE_RANGE_FINANCIAL_HOLDS_DATA attributes (verified, hyphenated there: date/time, settlement-id, ...). Seller Central CSV uses spaces, ~7 'Includes Amazon Marketplace...' preamble lines and quoted numbers - UNVERIFIED. 'Transfer' = disbursement to bank.")
add("amazon_order_history", "Amazon (consumer orders)", "payment_platform", "csv",
    ["Website","Order ID","Order Date","Purchase Order Number","Currency","Unit Price","Unit Price Tax","Shipping Charge","Total Discounts","Total Owed","Shipment Item Subtotal","Shipment Item Subtotal Tax","ASIN","Product Condition","Quantity","Payment Instrument Type","Order Status","Shipment Status","Ship Date"],
    "%Y-%m-%dT%H:%M:%SZ", amount="Total Owed", sign="positive_is_out", src=S["reds"] + "/amazon/amazon_orders.py", verified=True,
    notes="'Request Your Data' Retail.OrderHistory export. Useful for receipt matching against card charges, not a ledger.")
add("amazon_returns", "Amazon (consumer returns)", "payment_platform", "csv",
    ["OrderId","ContractId","DateOfReturn","ReturnAmount","ReturnAmountCurrency","ReturnReason","Resolution"], "%Y-%m-%dT%H:%M:%SZ",
    amount="ReturnAmount", sign="positive_is_out", src=S["reds"] + "/amazon/amazon_returns.py", verified=True, notes="Positive = refund to customer (money in).")
add("payoneer", "Payoneer", "payment_platform", "csv", None, "%m/%d/%Y", debit="Debit Amount", credit="Credit Amount", sign="split_columns",
    balance="Running Balance", src=S["c2o"] + " (payoneer.py) ; " + S["ff"] + " (us/payoneer)", verified=False,
    notes="csv2ofx names: Transaction Date, Transaction Time, Description, Credit Amount, Debit Amount, Currency, Target, Running Balance, Transaction ID (order unknown). Firefly config shows a different 7-col layout with 'd M, Y' dates.")

# ---------------------------------------------------------------- crypto
add("coinbase_transactions", "Coinbase", "brokerage_cash", "csv",
    ["ID","Timestamp","Transaction Type","Asset","Quantity Transacted","Price Currency","Price at Transaction","Subtotal","Total (inclusive of fees and/or spread)","Fees and/or Spread","Notes"],
    "%Y-%m-%d %H:%M:%S %Z", amount="Total (inclusive of fees and/or spread)", sign="type_column", type_column="Transaction Type",
    type_values={"Buy":"out","Sell":"in","Send":"out","Receive":"in","Convert":"either","Rewards Income":"in","Card Spend":"out"}, skip=2,
    src=S["coinbase"], verified=True,
    notes="Preamble: 'Transactions', 'User,<name>,<uuid>' then header (verified for the newer layout). Money cells like '$1,234.56'. Coinbase Card spend appears as crypto sells / 'Card Spend' (unverified). Older layout used 'USD Spot Price at Transaction','USD Subtotal','USD Total (inclusive of fees)','USD Fees'.")
add("cryptocom_card", "Crypto.com Visa Card", "debit", "csv",
    ["Timestamp (UTC)","Transaction Description","Currency","Amount","To Currency","To Amount","Native Currency","Native Amount","Native Amount (in USD)","Transaction Kind"],
    "%Y-%m-%d %H:%M:%S", amount="Native Amount", sign="negative_is_out", src=S["b2y"] + " ([Crypto.com], 10 cols, inflow at index 7)", verified=False,
    notes="Column count & positions verified via bank2ynab; header text recalled - unverified. Filename card_transactions_record_YYYYMMDD_HHMMSS.csv.")
add("coinbase_one_card", "Coinbase One Card (Amex, 2025)", "credit_card", "csv", None, None, src="unverified", verified=False,
    notes="Issued by Cardless; export format not documented publicly. Gemini Card (supported) is the closest analogue.")

# ---------------------------------------------------------------- Canada / UK
add("rbc", "RBC Royal Bank (CA)", "checking", "csv",
    ["Account Type","Account Number","Transaction Date","Cheque Number","Description 1","Description 2","CAD$","USD$"], "%m/%d/%Y",
    amount="CAD$", sign="negative_is_out", src=S["ff"] + " (ca/rbc readme lists these 8 columns, in French)", verified=False,
    notes="Order verified via Firefly; exact English header text recalled. Multi-account file (Account Number per row). USD$ used for USD accounts.")
add("td_canada", "TD Canada Trust", "checking", "csv", None, "%m/%d/%Y", debit="col2", credit="col3", sign="split_columns",
    headerless_columns=["date","description","debit","credit","balance"], src=S["b2y"] + " ; " + S["ff"] + " (ca/td)", verified=True,
    notes="No header. accountactivity.csv. Same layout for TD Visa.")
add("barclays_uk", "Barclays UK", "checking", "csv", ["Number","Date","Account","Amount","Subcategory","Memo"], "%d/%m/%Y", amount="Amount",
    sign="negative_is_out", src=S["ff"] + " (gb/barclays)", verified=False, notes="6-col order verified via Firefly roles; header text recalled.")
add("barclaycard_uk", "Barclaycard UK", "credit_card", "csv", None, "%d %b %y", debit="col7", credit="col6", sign="split_columns",
    headerless_columns=["date","description","?","?","?","category","credit","debit"], src=S["b2y"] + " ; " + S["ff"] + " (gb/barclaycard)", verified=True,
    notes="No header; dates like '05 Jan 24'.")
add("monzo", "Monzo (UK)", "checking", "csv",
    ["Transaction ID","Date","Time","Type","Name","Emoji","Category","Amount","Currency","Local amount","Local currency","Notes and #tags","Address","Receipt","Description","Category split","Money Out","Money In","Balance","Balance currency"],
    "%d/%m/%Y", amount="Amount", sign="negative_is_out", type_column="Type", balance="Balance", src=S["hl"] + " (banking/monzo.csv.rules)", verified=True,
    notes="Local amount/currency = FX card spend. Older exports lacked Balance columns (" + S["ff"] + " gb/monzo, 18 cols).")

# ---------------------------------------------------------------- OFX family, by institution
for id_, inst, prod, fmt, src, ver, note in [
    ("chase_qfx", "Chase", "credit_card", "qfx", S["chase"], False, "Activity download offers CSV, QFX, QBO (third-party source); ~24 months, statements 7 yrs PDF."),
    ("chase_qbo", "Chase", "checking", "qbo", S["chase"], False, "QuickBooks (.qbo) download."),
    ("wells_fargo_qfx", "Wells Fargo", "checking", "qfx", S["wf"], True, "Quicken (.qfx) download; up to 18 months."),
    ("wells_fargo_qbo", "Wells Fargo", "checking", "qbo", S["wf_qb"], True, "QuickBooks (.qbo) download."),
    ("boa_qbo", "Bank of America", "checking", "qbo", S["boa_qb"], True, "Small-business QuickBooks Desktop 2018+ (.qbo) download; 12 months of transactions online."),
    ("amex_qfx", "American Express", "credit_card", "qfx", S["amex"], True, "Recent activity + past 6 statements to Quicken."),
    ("amex_qbo", "American Express", "credit_card", "qbo", S["amex"], True, "Recent activity + past 6 statements to QuickBooks."),
    ("capital_one_qbo", "Capital One (business)", "checking", "qbo", S["c1_qb"], True, "Information Reporting -> Export -> QuickBooks (OFX Format); 18 months. Overlapping ranges create duplicates."),
    ("apple_card_ofx", "Apple Card (Goldman Sachs)", "credit_card", "ofx", "unverified", False, "Wallet export offers CSV/OFX (and reportedly QFX/QBO) per monthly statement."),
    ("discover_ofx", "Discover", "credit_card", "ofx", S["discover_ofx"], False, "Discover moved Money users to OFX in 2000; current Quicken/QuickBooks download availability unverified."),
    ("schwab_bank_ofx", "Charles Schwab Bank", "checking", "ofx", S["reds"] + "/schwab/schwab_ofx_bank_ofx.py", True, "OFX download used by reds importer."),
    ("ally_qfx", "Ally Bank", "checking", "qfx", S["ally"] + " ; " + S["reds"] + "/ally", True, "reds importer reads Ally via OFX/QFX."),
    ("citi_qfx", "Citi", "credit_card", "qfx", S["reds"] + "/citi", True, "reds importer reads Citi via OFX."),
    ("becu_ofx", "BECU", "checking", "ofx", S["reds"] + "/becu", True, "Credit union OFX download."),
    ("target_card_ofx", "Target Circle Card (TD Bank)", "credit_card", "ofx", S["reds"] + "/target/__init__.py", True, "Store card via OFX."),
    ("navy_federal_ofx", "Navy Federal Credit Union", "checking", "ofx", S["nfcu"], False, "OFX and QIF offered (third-party)."),
    ("navy_federal_qif", "Navy Federal Credit Union", "checking", "qif", S["nfcu"], False, "QIF bank type."),
]:
    add(id_, inst, prod, fmt, None, None, src=src, verified=ver, notes=note)

# ---------------------------------------------------------------- corporate / generic
add("capital_one_bai2", "Capital One (treasury)", "checking", "bai2", None, "%y%m%d", src=S["c1_ir"], verified=True,
    notes="Information Reporting exports BAI, Quicken and QuickBooks formats; 18 months.")
add("td_bai2", "TD (commercial)", "checking", "bai2", None, "%y%m%d", src=S["td_bai"], verified=True, notes="TD publishes a BAI2 implementation guide.")
add("generic_camt053", "Generic ISO 20022 (JPMorgan Access, BofA CashPro, Citi, EU banks)", "checking", "camt053", None, "%Y-%m-%d",
    sign="type_column", type_column="CdtDbtInd", type_values={"DBIT":"out","CRDT":"in"}, src=S["camt"], verified=False,
    notes="Amounts always positive; CdtDbtInd decides. Sts PDNG = pending. US corporate availability per bank unverified.")
add("generic_mt940", "Generic SWIFT MT940", "checking", "mt940", None, "%y%m%d", sign="type_column", type_column=":61: D/C mark",
    type_values={"D":"out","C":"in","RD":"in","RC":"out"}, src=S["mt940"], verified=False, notes="Comma decimal separator. Many EU banks; Wise offers MT940.")
add("generic_bai2", "Generic BAI2 (US treasury portals)", "checking", "bai2", None, "%y%m%d", sign="type_column", type_column="16 type code",
    type_values={"100-399":"in","400-699":"out"}, src=S["bai2"], verified=False, notes="Amounts in cents, no decimal point; implied by currency.")
add("generic_ofx1", "Generic OFX 1.x SGML", "checking", "ofx", None, "%Y%m%d%H%M%S", amount="TRNAMT", sign="negative_is_out", src=S["ofx"], verified=False,
    encoding="cp1252", notes="Header block CHARSET:1252 common; unclosed tags; FITID for dedupe.")
add("generic_qif", "Generic QIF", "checking", "qif", None, "%m/%d/%Y", amount="T", sign="negative_is_out", src="https://en.wikipedia.org/wiki/Quicken_Interchange_Format",
    verified=False, encoding="cp1252", notes="Dates vary: MM/DD/YY, MM/DD'YY (apostrophe for 2000+), D/M/Y outside US; no unique ids.")
add("generic_pdf", "Any bank (older periods)", "checking", "pdf", None, None, src=S["amex_hist"] + " ; " + S["c1_hist"], verified=True,
    notes="Banks keep ~7 years of PDF statements but CSV/QFX activity only ~12-24 months (Amex 2y, Capital One 2y on site, WF 18 mo, Ally 18 mo, SoFi any 2y window). Older periods => PDF parser.")

ids = [e["id"] for e in E]
assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]
doc = {"schema_version": 1, "generated": "2026-10-18",
       "description": "Catalog of known bank/card/platform export formats. 'verified' = header/format seen verbatim in the cited public source (or in LedgerStitch's own detect.py profiles). Unverified entries are best-effort and must be confirmed against a real export before being promoted to a detect.py profile.",
       "entries": E}
OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
v = sum(e["verified"] for e in E)
from collections import Counter
print(len(E), "entries;", v, "verified;", len(E) - v, "unverified")
print(Counter(e["format"] for e in E))
print("csv with exact header:", sum(1 for e in E if e["format"] == "csv" and e["header"]), "verified:", sum(1 for e in E if e["format"] == "csv" and e["header"] and e["verified"]))
