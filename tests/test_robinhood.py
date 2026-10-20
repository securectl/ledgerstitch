# SPDX-License-Identifier: AGPL-3.0-only
"""Robinhood Gold Card: CSV export and PDF statement layout (synthetic data only)."""
from datetime import date
from decimal import Decimal

from ledgerstitch.ingest.csv_parser import parse_csv_detailed
from ledgerstitch.ingest.detect import detect, filename_hints
from ledgerstitch.ingest.pdf_parser import Line, Word, find_period, parse_lines
from ledgerstitch.models import AccountKind, Direction

CSV = b"""Date,Time,Cardholder,Amount,Points,Balance,Status,Type,Merchant,Description
2026-10-14,"6:58 PM","Pat Doe",48.63,146,4893.46,Posted,Purchase,"Sample Grocer","123 Main St Austin TX"
2026-10-13,"5:00 PM","Pat Doe",99.88,300,4844.83,Posted,Purchase,Fuel Stop,
2026-10-12,"1:00 PM","Pat Doe",-20.00,0,4744.95,Posted,Refund,Sample Grocer,
2026-10-11,"1:00 PM","Pat Doe",-500.00,0,4764.95,Posted,Payment,Payment,
2026-10-10,"1:00 PM","Pat Doe",50.00,0,5264.95,Posted,Fee,Annual fee,
2026-10-09,"1:00 PM","Pat Doe",12.00,36,,Pending,Purchase,Coffee Bar,
2026-10-08,"1:00 PM","Pat Doe",30.00,0,,Declined,Purchase,Some Shop,
2026-10-07,"1:00 PM","Pat Doe",0.00,0,,Posted,Other,Adjustment,
"""
NAME = "ccc24101-7235-419d-b1d6-1bc1e8e821a7.csv"


def test_robinhood_csv_is_a_card_and_uses_merchant():
    p = detect(NAME, CSV)
    assert (p.id, p.institution, p.kind) == ("robinhood_card", "Robinhood", AccountKind.CREDIT_CARD)
    assert p.account_hint is None          # the download id is not a card number
    tx, warnings = parse_csv_detailed(CSV, p, "a", "i")
    assert [(t.description, t.direction) for t in tx] == [
        ("Sample Grocer", Direction.OUT), ("Fuel Stop", Direction.OUT), ("Sample Grocer", Direction.IN),
        ("Payment", Direction.IN), ("Annual fee", Direction.OUT)]
    assert not any("Pat" in t.description for t in tx)
    assert any("pending" in w for w in warnings) and any("failed" in w for w in warnings)


def test_uuid_filename_has_no_last4():
    assert filename_hints(NAME)["last4"] is None
    assert filename_hints("robinhood-card-1234.csv")["last4"] == "1234"


def L(*cells, top=0.0, page=0):
    return Line([Word(t, x0, x1, top) for t, x0, x1 in cells], page)


TEXT = ("ACCOUNT SUMMARY\nCreditLimit $10,000.00 NewBalance $0.00\nMinimumPaymentDue $0.00\n"
        "StatementClosingDate October02,2026\nPaymentDueDate October25,2026\nDaysinBillingCycle 30\n"
        "PreviousBalance $100.00\n- Payments&Credits $110.00 datelistedabove\n"
        "+ Purchases&OtherCharges $10.00 consequences\n+ FEECHARGED $0.00\n+ INTERESTCHARGED $0.00\n"
        "= NewBalance $0.00\nTOTALFEESFORTHISPERIOD 0.00\nPurchases 29.99%(v) $0.00 30 $0.00\n"
        "Robinhood Credit, Inc. 85 Discovery Street\n")


def test_robinhood_pdf_period_from_glued_closing_date():
    assert find_period(TEXT) == (date(2026, 9, 3), date(2026, 10, 2))


def test_robinhood_pdf_table_after_summary_section():
    lines = [
        L(("ACCOUNT", 40, 80), ("SUMMARY", 82, 130), top=50),
        L(("TRANSACTIONS", 40, 120), top=90),
        L(("Date", 40, 60), ("Date", 70, 90), ("ReferenceNumber", 100, 180), ("TransactionDescription", 200, 300),
          ("Amount", 520, 560), top=100),
        L(("09/02", 40, 60), ("09/03", 70, 90), ("2469216K3W4ABCD12", 100, 180), ("SAMPLE", 200, 230),
          ("STORE", 232, 260), ("10.00", 530, 560), top=112),
        L(("09/03", 40, 60), ("09/03", 70, 90), ("2469216K3W4ABCD13", 100, 180), ("PAYMENT", 200, 240),
          ("100.00-", 530, 560), top=124),
        L(("09/07", 40, 60), ("09/07", 70, 90), ("2469216K3W4ABCD14", 100, 180), ("SAMPLE", 200, 230),
          ("CREDIT", 470, 500), ("110.00-", 530, 560), top=136),
    ]
    rows, warnings, info = parse_lines(lines, TEXT)
    assert info["is_card"] is True
    got = [(r.date, " ".join(r.desc_parts), r.amount, r.direction) for r in rows]
    assert got[0] == (date(2026, 9, 2), "SAMPLE STORE", Decimal("10.00"), Direction.OUT)
    assert got[-1][2:] == (Decimal("110.00"), Direction.IN)


def test_robinhood_pdf_reconciles_against_card_summary():
    lines = [
        L(("Date", 40, 60), ("Date", 70, 90), ("Amount", 520, 560), top=100),
        L(("09/02", 40, 60), ("09/03", 70, 90), ("2469216K3W4ABCD12", 100, 180), ("SAMPLE", 200, 230),
          ("10.00", 530, 560), top=112),
        L(("09/07", 40, 60), ("09/07", 70, 90), ("2469216K3W4ABCD14", 100, 180), ("PAYMENT", 200, 240),
          ("110.00-", 530, 560), top=124),
    ]
    rows, warnings, info = parse_lines(lines, TEXT)
    assert info["reconciled"] is True, warnings
    assert info["stated_in"] == Decimal("110.00") and info["stated_out"] == Decimal("10.00")


def test_no_rows_is_never_reconciled():
    rows, warnings, info = parse_lines([L(("Date", 40, 60), ("Amount", 520, 560), top=100)], TEXT)
    assert rows == [] and info["reconciled"] is None
