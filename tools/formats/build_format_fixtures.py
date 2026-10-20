"""Write synthetic CSV fixtures for tests/fixtures/formats from known_formats.json headers."""
import csv, io, json, sys
from pathlib import Path

ROOT = Path(sys.argv[1])
cat = {e["id"]: e for e in json.loads((ROOT / "ledgerstitch/ingest/data/known_formats.json").read_text())["entries"]}
D = ROOT / "tests/fixtures/formats"


def write(name, cid, rows, pre=(), post=(), quote=csv.QUOTE_MINIMAL, bom=False):
    h = cat[cid]["header"]
    buf = io.StringIO()
    w = csv.writer(buf, quoting=quote, lineterminator="\n")
    for p in pre:
        w.writerow(p)
    w.writerow(h)
    for r in rows:
        unknown = set(r) - set(h)
        assert not unknown, (cid, unknown)
        d = dict.fromkeys(h, "")
        d.update(r)
        w.writerow([d[c] for c in h])
    for p in post:
        w.writerow(p)
    (D / name).write_text(buf.getvalue(), encoding="utf-8-sig" if bom else "utf-8", newline="")
    return name, cid


out = [
 write("schwab_bank_checking.csv", "schwab_bank_checking", [
  {"Date": "01/03/2025", "Status": "Posted", "Type": "DEBIT", "Description": "SAMPLE COFFEE CO", "Withdrawal": "$4.50", "RunningBalance": "$995.50"},
  {"Date": "01/05/2025", "Status": "Posted", "Type": "ACH", "Description": "EXAMPLE PAYROLL INC", "Deposit": "$2,500.00", "RunningBalance": "$3,495.50"},
  {"Date": "01/10/2025", "Status": "Posted", "Type": "CHECK", "CheckNumber": "1001", "Description": "CHECK 1001", "Withdrawal": "$120.00", "RunningBalance": "$3,375.50"},
  {"Date": "01/12/2025", "Status": "Pending", "Type": "DEBIT", "Description": "FAKE HARDWARE STORE", "Withdrawal": "$33.10"}], quote=csv.QUOTE_ALL),
 write("dcu_checking.csv", "dcu_checking", [
  {"DATE": "02/01/2025", "TRANSACTION TYPE": "DEBIT", "DESCRIPTION": "SAMPLE COFFEE CO", "AMOUNT": "-4.50", "ID": "0001", "MEMO": "POS PURCHASE", "CURRENT BALANCE": "995.50"},
  {"DATE": "02/03/2025", "TRANSACTION TYPE": "CREDIT", "DESCRIPTION": "EXAMPLE CLIENT INC", "AMOUNT": "1200.00", "ID": "0002", "MEMO": "ACH DEPOSIT", "CURRENT BALANCE": "2195.50"},
  {"DATE": "02/07/2025", "TRANSACTION TYPE": "DEBIT", "DESCRIPTION": "FAKE UTILITY CO", "AMOUNT": "-88.20", "ID": "0003", "MEMO": "BILL PAY", "CURRENT BALANCE": "2107.30"}], quote=csv.QUOTE_ALL),
 write("mercury.csv", "mercury", [
  {"Date (UTC)": "03-02-2025", "Description": "Sample Software LLC", "Amount": "-249.00", "Status": "Sent", "Bank Description": "SAMPLE SOFTWARE LLC"},
  {"Date (UTC)": "03-04-2025", "Description": "Example Client Inc", "Amount": "5,000.00", "Status": "Sent", "Bank Description": "EXAMPLE CLIENT INC ACH", "Reference": "INV-0001", "Note": "March retainer"},
  {"Date (UTC)": "03-05-2025", "Description": "Fake Cloud Hosting", "Amount": "-31.07", "Status": "Failed", "Bank Description": "FAKE CLOUD HOSTING"},
  {"Date (UTC)": "03-09-2025", "Description": "Sample Coffee Co", "Amount": "-6.25", "Status": "Pending", "Bank Description": "SAMPLE COFFEE CO"}]),
 write("revolut.csv", "revolut", [
  {"Type": "CARD_PAYMENT", "Product": "Current", "Started Date": "2025-04-01 09:12:00", "Completed Date": "2025-04-02 10:00:00", "Description": "Sample Coffee Co", "Amount": "-4.50", "Fee": "0.00", "Currency": "USD", "State": "COMPLETED", "Balance": "995.50"},
  {"Type": "TOPUP", "Product": "Current", "Started Date": "2025-04-03 12:00:00", "Completed Date": "2025-04-03 12:00:05", "Description": "Top-up by *0000", "Amount": "500.00", "Fee": "0.00", "Currency": "USD", "State": "COMPLETED", "Balance": "1495.50"},
  {"Type": "EXCHANGE", "Product": "Current", "Started Date": "2025-04-04 08:00:00", "Completed Date": "2025-04-04 08:00:01", "Description": "Exchanged to EUR", "Amount": "-100.00", "Fee": "0.50", "Currency": "USD", "State": "COMPLETED", "Balance": "1395.00"},
  {"Type": "CARD_PAYMENT", "Product": "Current", "Started Date": "2025-04-05 18:30:00", "Description": "Example Bistro", "Amount": "-22.00", "Fee": "0.00", "Currency": "USD", "State": "PENDING"}]),
 write("paypal_activity.csv", "paypal_activity", [
  {"Date": "5/01/2025", "Time": "10:15:00", "TimeZone": "PDT", "Name": "Example Client Inc", "Type": "Express Checkout Payment", "Status": "Completed", "Currency": "USD", "Gross": "100.00", "Fee": "-3.79", "Net": "96.21", "From Email Address": "client@example.com", "To Email Address": "seller@example.com", "Transaction ID": "0SAMPLE00000001A", "Item Title": "Consulting hour", "Balance": "96.21", "Balance Impact": "Credit"},
  {"Date": "5/03/2025", "Time": "14:00:00", "TimeZone": "PDT", "Name": "Sample Software LLC", "Type": "PreApproved Payment Bill User Payment", "Status": "Completed", "Currency": "USD", "Gross": "-15.00", "Fee": "0.00", "Net": "-15.00", "Transaction ID": "0SAMPLE00000002B", "Balance": "81.21", "Balance Impact": "Debit"},
  {"Date": "5/03/2025", "Time": "14:00:00", "TimeZone": "PDT", "Type": "General Card Deposit", "Status": "Completed", "Currency": "USD", "Gross": "15.00", "Fee": "0.00", "Net": "15.00", "Transaction ID": "0SAMPLE00000003C", "Reference Txn ID": "0SAMPLE00000002B", "Balance": "96.21", "Balance Impact": "Memo"},
  {"Date": "5/10/2025", "Time": "09:00:00", "TimeZone": "PDT", "Type": "General Withdrawal", "Status": "Completed", "Currency": "USD", "Gross": "-81.21", "Fee": "0.00", "Net": "-81.21", "Transaction ID": "0SAMPLE00000004D", "Balance": "0.00", "Balance Impact": "Debit"}],
  quote=csv.QUOTE_ALL, bom=True),
 write("venmo_statement.csv", "venmo_statement", [
  {"Beginning Balance": "$0.00"},
  {"ID": "1000000000000000001", "Datetime": "2025-06-01T12:00:00", "Type": "Payment", "Status": "Complete", "Note": "lunch", "From": "Pat Sample", "To": "Sam Example", "Amount (total)": "- $12.00", "Funding Source": "Venmo balance"},
  {"ID": "1000000000000000002", "Datetime": "2025-06-02T08:30:00", "Type": "Payment", "Status": "Complete", "Note": "logo design", "From": "Example Client Inc", "To": "Pat Sample", "Amount (total)": "+ $150.00", "Destination": "Venmo balance"},
  {"ID": "1000000000000000003", "Datetime": "2025-06-05T09:00:00", "Type": "Standard Transfer", "Status": "Issued", "Amount (total)": "- $138.00", "Destination": "SAMPLE BANK *0000"},
  {"Ending Balance": "$0.00", "Statement Period Venmo Fees": "$0.00", "Year to Date Venmo Fees": "$0.00"}],
  pre=[["Account Statement - (@Pat-Sample) - June 1st to June 30th 2025 "], ["Account Activity"]],
  post=[["In case of errors or questions about your electronic transfers SAMPLE DISCLAIMER TEXT"]]),
 write("stripe_balance_itemized.csv", "stripe_balance_itemized", [
  {"balance_transaction_id": "txn_SAMPLE0000000001", "created_utc": "2025-07-01 15:04:05", "available_on_utc": "2025-07-03 00:00:00", "currency": "usd", "gross": "200.00", "fee": "6.10", "net": "193.90", "reporting_category": "charge", "description": "Invoice SAMPLE-0001"},
  {"balance_transaction_id": "txn_SAMPLE0000000002", "created_utc": "2025-07-02 10:00:00", "available_on_utc": "2025-07-04 00:00:00", "currency": "usd", "gross": "-50.00", "fee": "0.00", "net": "-50.00", "reporting_category": "refund", "description": "REFUND FOR PAYMENT"},
  {"balance_transaction_id": "txn_SAMPLE0000000003", "created_utc": "2025-07-05 00:00:00", "available_on_utc": "2025-07-05 00:00:00", "currency": "usd", "gross": "-143.90", "fee": "0.00", "net": "-143.90", "reporting_category": "payout", "description": "STRIPE PAYOUT"}], quote=csv.QUOTE_ALL),
 write("shopify_payments_transactions.csv", "shopify_payments_transactions", [
  {"Transaction Date": "2025-08-01 10:00:00 -0400", "Type": "charge", "Order": "#1001", "Card Brand": "visa", "Card Source": "online", "Payout Status": "paid", "Payout Date": "2025-08-04", "Available On": "2025-08-04", "Amount": "45.00", "Fee": "1.61", "Net": "43.39"},
  {"Transaction Date": "2025-08-02 11:00:00 -0400", "Type": "refund", "Order": "#0998", "Card Brand": "mastercard", "Card Source": "online", "Payout Status": "paid", "Payout Date": "2025-08-04", "Available On": "2025-08-04", "Amount": "-20.00", "Fee": "0.00", "Net": "-20.00"},
  {"Transaction Date": "2025-08-04 00:00:00 -0400", "Type": "payout", "Payout Status": "paid", "Payout Date": "2025-08-04", "Available On": "2025-08-04", "Amount": "-23.39", "Fee": "0.00", "Net": "-23.39"}]),
 write("coinbase_transactions.csv", "coinbase_transactions", [
  {"ID": "00000000000000000000aaaa", "Timestamp": "2025-09-01 12:00:00 UTC", "Transaction Type": "Buy", "Asset": "BTC", "Quantity Transacted": "0.001", "Price Currency": "USD", "Price at Transaction": "$60,000.00", "Subtotal": "$60.00", "Total (inclusive of fees and/or spread)": "$61.49", "Fees and/or Spread": "$1.49", "Notes": "Bought 0.001 BTC for 61.49 USD"},
  {"ID": "00000000000000000000bbbb", "Timestamp": "2025-09-03 08:00:00 UTC", "Transaction Type": "Card Spend", "Asset": "USDC", "Quantity Transacted": "-4.5", "Price Currency": "USD", "Price at Transaction": "$1.00", "Subtotal": "$4.50", "Total (inclusive of fees and/or spread)": "$4.50", "Fees and/or Spread": "$0.00", "Notes": "SAMPLE COFFEE CO"},
  {"ID": "00000000000000000000cccc", "Timestamp": "2025-09-05 09:00:00 UTC", "Transaction Type": "Rewards Income", "Asset": "USDC", "Quantity Transacted": "0.25", "Price Currency": "USD", "Price at Transaction": "$1.00", "Subtotal": "$0.25", "Total (inclusive of fees and/or spread)": "$0.25", "Fees and/or Spread": "$0.00", "Notes": "USDC rewards"}],
  pre=[["Transactions"], ["User", "Pat Sample", "00000000-0000-0000-0000-000000000000"]]),
 write("monzo.csv", "monzo", [
  {"Transaction ID": "tx_SAMPLE0001", "Date": "01/10/2025", "Time": "08:01:00", "Type": "Card payment", "Name": "Sample Coffee Co", "Category": "Eating out", "Amount": "-3.20", "Currency": "GBP", "Local amount": "-3.20", "Local currency": "GBP", "Money Out": "-3.20", "Balance": "96.80", "Balance currency": "GBP"},
  {"Transaction ID": "tx_SAMPLE0002", "Date": "02/10/2025", "Time": "12:00:00", "Type": "Faster payment", "Name": "Example Client Ltd", "Category": "Income", "Amount": "500.00", "Currency": "GBP", "Local amount": "500.00", "Local currency": "GBP", "Notes and #tags": "#invoice", "Money In": "500.00", "Balance": "596.80", "Balance currency": "GBP"},
  {"Transaction ID": "tx_SAMPLE0003", "Date": "05/10/2025", "Time": "19:45:00", "Type": "Card payment", "Name": "Fake Paris Cafe", "Category": "Holidays", "Amount": "-8.70", "Currency": "GBP", "Local amount": "-10.00", "Local currency": "EUR", "Address": "1 Sample Rue", "Money Out": "-8.70", "Balance": "588.10", "Balance currency": "GBP"}]),
]

manifest = {
 "note": "Synthetic fixtures (fake data). catalog_id refers to ledgerstitch/ingest/data/known_formats.json.",
 "files": [{"file": f, "catalog_id": c, "format": "csv"} for f, c in out] + [
  {"file": "ofx1_sgml_checking.ofx", "catalog_id": "generic_ofx1", "format": "ofx", "variant": "1.0.2 SGML, BANKMSGSRSV1, CHECKING"},
  {"file": "ofx2_xml_creditcard.ofx", "catalog_id": None, "format": "ofx", "variant": "2.2.0 XML, CREDITCARDMSGSRSV1"},
  {"file": "sample_savings.qfx", "catalog_id": None, "format": "qfx", "variant": "OFX 1.0.2 + INTU.BID/INTU.USERID, SAVINGS"},
  {"file": "sample_card.qbo", "catalog_id": None, "format": "qbo", "variant": "OFX 1.0.2 + INTU.BID, credit card"},
  {"file": "qif_bank.qif", "catalog_id": "generic_qif", "format": "qif", "variant": "!Type:Bank, mixed date styles incl. MM/DD'YY, split"},
  {"file": "qif_ccard.qif", "catalog_id": None, "format": "qif", "variant": "!Account + !Type:CCard"},
  {"file": "camt053_sample.xml", "catalog_id": "generic_camt053", "format": "camt053", "variant": "camt.053.001.08"},
  {"file": "mt940_sample.sta", "catalog_id": "generic_mt940", "format": "mt940", "variant": "block-4 body only, no {1:}{2:} envelope"},
  {"file": "bai2_sample.bai", "catalog_id": "generic_bai2", "format": "bai2", "variant": "1 group, 1 account, control totals valid"},
 ]}
(D / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(len(out), "csv fixtures")
