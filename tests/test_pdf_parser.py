# SPDX-License-Identifier: AGPL-3.0-only
"""PDF parser tests on SYNTHETIC statements (tests/fixtures/make_pdfs.py) and on
hand-built word/line structures (no PDF needed)."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ledgerstitch.ingest.pdf_parser import (Line, Word, assign_year, find_period, parse_lines, parse_pdf,
                                          parse_pdf_detailed, words_to_lines)
from ledgerstitch.models import Direction

FX = Path(__file__).parent / "fixtures"


def pdf(name):
    return parse_pdf((FX / name).read_bytes(), "acct", "imp")


def sums(txns):
    o = sum((t.amount for t in txns if t.direction == Direction.OUT), Decimal(0))
    i = sum((t.amount for t in txns if t.direction == Direction.IN), Decimal(0))
    return o, i


# --------------------------------------------------------------------------- period / year

@pytest.mark.parametrize("text,start,end", [
    ("January 1, 2025 through January 31, 2025", date(2025, 1, 1), date(2025, 1, 31)),
    ("November 29, 2025throughDecember 31, 2025", date(2025, 11, 29), date(2025, 12, 31)),
    ("Statement period 12/15/24 - 01/14/25", date(2024, 12, 15), date(2025, 1, 14)),
    ("Statement Period: 03/01/2025 to 03/31/2025", date(2025, 3, 1), date(2025, 3, 31)),
    ("February 28, 2025 Page 1\nBeginning balance on 2/1 $10.00\nEnding balance on 2/28 $5.00",
     date(2025, 2, 1), date(2025, 2, 28)),
    ("January 14, 2025\nBeginning balance on 12/15 $1.00\nEnding balance on 1/14 $2.00",
     date(2024, 12, 15), date(2025, 1, 14)),
    ("Opening/Closing Date 12/05/24 - 01/04/25", date(2024, 12, 5), date(2025, 1, 4)),
    ("nothing useful here", None, None),
])
def test_find_period(text, start, end):
    assert find_period(text) == (start, end)


def test_assign_year_rollover():
    s, e = date(2024, 12, 15), date(2025, 1, 14)
    assert assign_year(12, 20, s, e) == date(2024, 12, 20)
    assert assign_year(1, 3, s, e) == date(2025, 1, 3)
    assert assign_year(6, 1, None, date(2026, 6, 30)) == date(2026, 6, 1)   # not hard-coded
    assert assign_year(1, 1, None, None) is None


# --------------------------------------------------------------------------- real (synthetic) PDFs

def test_column_layout_pdf_with_year_rollover_and_multiline():
    tx, warnings = pdf("stmt_columns.pdf")
    assert warnings == []                        # totals reconcile
    assert len(tx) == 5                          # 0.00 row and "Items returned unpaid" ignored
    assert [t.date for t in tx] == [date(2024, 12, 16), date(2024, 12, 20), date(2024, 12, 31),
                                    date(2025, 1, 2), date(2025, 1, 9)]
    assert [t.direction for t in tx] == [Direction.OUT, Direction.IN, Direction.OUT, Direction.IN, Direction.OUT]
    assert sums(tx) == (Decimal("1145.50"), Decimal("1650.00"))
    wire = tx[2]
    assert "Ow00000000000001" in wire.description           # continuation lines joined
    assert "Page" not in wire.description
    assert tx[0].description.endswith("Card 0000")
    assert tx[0].merchant == "COPPER KETTLE SUPPLY"
    assert len({t.id for t in tx}) == 5


def test_column_layout_mismatch_warns():
    tx, warnings = pdf("stmt_columns_mismatch.pdf")
    assert len(tx) == 5
    assert any("reconciliation" in w and "money-in" in w for w in warnings)
    assert not any("money-out" in w for w in warnings)


def test_section_layout_pdf():
    tx, warnings = pdf("stmt_sections.pdf")
    assert warnings == []
    assert len(tx) == 4                          # daily-balance section skipped
    ins = [t for t in tx if t.direction == Direction.IN]
    outs = [t for t in tx if t.direction == Direction.OUT]
    assert sum(t.amount for t in ins) == Decimal("3100.00")
    assert sum(t.amount for t in outs) == Decimal("1250.00")
    assert ins[0].date == date(2024, 12, 20) and ins[1].date == date(2025, 1, 3)
    assert "Descr:Invoice" in ins[0].description
    # "12/28 12/28 Payment ..." second date dropped from the description
    card_pay = next(t for t in outs if "Payment To" in t.description)
    assert card_pay.description.startswith("Payment To")


def test_running_balance_pdf():
    tx, warnings = pdf("stmt_balance.pdf")
    assert [t.direction for t in tx] == [Direction.OUT, Direction.IN, Direction.OUT]
    assert not any("sign only" in w for w in warnings)


def test_garbage_pdf_bytes():
    tx, warnings = parse_pdf(b"%PDF-1.4 not really", "a", "i")
    assert tx == [] and warnings


# --------------------------------------------------------------------------- line-level unit tests

def L(*cells, top=0.0, page=0):
    """cells: (text, x0, x1)"""
    return Line([Word(t, x0, x1, top) for t, x0, x1 in cells], page)


def test_words_to_lines_groups_by_top():
    words = [{"text": "b", "x0": 50, "x1": 55, "top": 10.5}, {"text": "a", "x0": 10, "x1": 15, "top": 10},
             {"text": "c", "x0": 10, "x1": 15, "top": 30}]
    lines = words_to_lines(words)
    assert [l.text for l in lines] == ["a b", "c"]


def test_columns_by_x_position_not_keywords():
    # description says "Deposit" but the amount sits in the Debits column → out
    lines = [
        L(("Date", 60, 75), ("Description", 150, 190), ("Credits", 400, 435), ("Debits", 470, 503),
          ("balance", 540, 566), top=100),
        L(("1/5", 63, 75), ("Refund", 150, 175), ("Deposit", 177, 200), ("Fee", 202, 215), ("12.00", 480, 503), top=112),
        L(("1/6", 63, 75), ("Payment", 150, 180), ("sent", 182, 200), ("40.00", 410, 435), ("1,028.00", 530, 566), top=124),
    ]
    rows, w, info = parse_lines(lines, "Statement period 01/01/2025 to 01/31/2025")
    assert [r.direction for r in rows] == [Direction.OUT, Direction.IN]
    assert rows[1].balance == Decimal("1028.00")
    assert info["method"] == "columns"


def test_section_headers_card_statement():
    text = "Payment Due Date 02/25/25\nOpening/Closing Date 12/26/24 - 01/25/25\nCredit Limit $5,000"
    lines = [
        L(("PAYMENTS", 40, 90), ("AND", 92, 110), ("OTHER", 112, 140), ("CREDITS", 142, 180), top=100),
        L(("01/10", 40, 60), ("PAYMENT", 70, 110), ("THANK", 112, 140), ("YOU", 142, 160), ("-250.00", 520, 560), top=112),
        L(("PURCHASE", 40, 90), top=124),
        L(("12/28", 40, 60), ("SAMPLE", 70, 100), ("STORE", 102, 130), ("250.00", 520, 560), top=136),
        L(("01/02", 40, 60), ("OTHER", 70, 100), ("SHOP", 102, 130), ("9.99", 520, 560), top=148),
    ]
    rows, w, info = parse_lines(lines, text)
    assert info["is_card"] is True
    assert [(r.date, r.direction) for r in rows] == [
        (date(2025, 1, 10), Direction.IN), (date(2024, 12, 28), Direction.OUT), (date(2025, 1, 2), Direction.OUT)]


def test_sign_fallback_warns():
    lines = [L(("03/02", 40, 60), ("THING", 70, 100), ("-5.00", 520, 560), top=100),
             L(("03/03", 40, 60), ("OTHER", 70, 100), ("7.00", 520, 560), top=112)]
    rows, w, info = parse_lines(lines, "Statement period 03/01/2025 to 03/31/2025")
    assert [r.direction for r in rows] == [Direction.OUT, Direction.IN]
    assert any("sign only" in x for x in w)


def test_continuation_requires_indent_and_proximity():
    lines = [
        L(("Date", 60, 75), ("Description", 150, 190), ("Credits", 400, 435), ("Debits", 470, 503), top=100),
        L(("1/5", 63, 75), ("Wire", 150, 170), ("out", 172, 190), ("10.00", 480, 503), top=112),
        L(("Trn#123", 150, 190), top=122),                    # indented & close → joined
        L(("Footer", 63, 100), ("text", 102, 120), top=300),   # far away → ignored
    ]
    rows, _, _ = parse_lines(lines, "Statement period 01/01/2025 to 01/31/2025")
    assert len(rows) == 1
    assert rows[0].desc_parts == ["Wire out", "Trn#123"]


def test_no_period_skips_yearless_dates_with_warning():
    lines = [L(("1/5", 40, 60), ("THING", 70, 100), ("5.00", 520, 560), top=100)]
    rows, w, _ = parse_lines(lines, "")
    assert rows == []
    assert any("period not found" in x for x in w)


# --------------------------------------------------------------------------- found by `ledgerstitch benchmark`

def test_standard_purchases_section_is_money_out():
    # Citi-style card statements head the purchases block "Standard Purchases"
    text = ("Billing Period: 03/10/25-04/09/25\nCredit Limit $15,000\nMinimum payment due $41.00\n"
            "Previous balance $100.00\nNew balance $129.99")
    lines = [
        L(("Sale", 40, 60), top=90),
        L(("date", 40, 60), ("date", 80, 100), ("Description", 130, 190), ("Amount", 540, 570), top=100),
        L(("Payments,", 40, 85), ("Credits", 87, 120), ("and", 122, 140), ("Adjustments", 142, 200), top=112),
        L(("03/21", 40, 60), ("03/22", 80, 100), ("PAYMENT", 130, 170), ("THANK", 172, 200), ("YOU", 202, 220),
          ("-$100.00", 530, 570), top=124),
        L(("Total", 40, 65), ("payments,", 67, 110), ("credits", 112, 145), ("and", 147, 165),
          ("adjustments", 167, 220), ("-$100.00", 530, 570), top=136),
        L(("Standard", 40, 80), ("Purchases", 82, 130), top=148),
        L(("03/11", 40, 60), ("03/12", 80, 100), ("SAMPLE", 130, 160), ("STORE", 162, 190), ("$129.99", 535, 570),
          top=160),
        L(("Total", 40, 65), ("standard", 67, 105), ("purchases", 107, 150), ("$129.99", 535, 570), top=172),
    ]
    rows, w, info = parse_lines(lines, text)
    assert [(r.amount, r.direction) for r in rows] == [(Decimal("100.00"), Direction.IN),
                                                       (Decimal("129.99"), Direction.OUT)]
    assert info["reconciled"] is True and info["difference"] == Decimal("0")


def test_chase_card_summary_with_signed_amounts_reconciles():
    # "Payment, Credits -$X" / "Purchases +$Y": the account summary, not the fees-only
    # "TOTAL FEES FOR THIS PERIOD" line, is what the activity must add up to
    text = ("ACCOUNT SUMMARY\nPrevious Balance $50.00\nPayment, Credits -$50.00\nPurchases +$30.00\n"
            "Cash Advances $0.00\nFees Charged +$0.00\nInterest Charged +$0.00\nNew Balance $30.00\n"
            "Opening/Closing Date 05/15/25 - 06/14/25\nPayment Due Date 07/09/25\nMinimum Payment Due $35.00\n"
            "TOTAL FEES FOR THIS PERIOD $0.00\nTotal fees charged in 2025 $39.00\n")
    lines = [
        L(("PAYMENTS", 40, 90), ("AND", 92, 110), ("OTHER", 112, 140), ("CREDITS", 142, 180), top=100),
        L(("05/20", 40, 60), ("Payment", 100, 140), ("Thank", 142, 170), ("You", 172, 190), ("-50.00", 530, 570),
          top=112),
        L(("PURCHASE", 40, 90), top=124),
        L(("05/22", 40, 60), ("SAMPLE", 100, 130), ("STORE", 132, 160), ("30.00", 540, 570), top=136),
        L(("TOTAL", 40, 70), ("FEES", 72, 95), ("FOR", 97, 112), ("THIS", 114, 135), ("PERIOD", 137, 170),
          ("$0.00", 545, 570), top=148),
    ]
    rows, w, info = parse_lines(lines, text)
    assert info["is_card"] is True
    assert info["reconciled"] is True, w
    assert not any("reconciliation" in x for x in w)


def test_checks_paid_row_is_named_by_check_number():
    text = "April 16, 2025 through May 15, 2025\nCHECKING SUMMARY"
    lines = [
        L(("CHECKS", 40, 75), ("PAID", 77, 100), top=100),
        L(("1042", 40, 60), ("^", 70, 75), ("04/22", 80, 105), ("$424.22", 535, 570), top=112),
    ]
    rows, w, info = parse_lines(lines, text)
    assert [(" ".join(r.desc_parts), r.amount, r.direction) for r in rows] == [
        ("CHECK 1042", Decimal("424.22"), Direction.OUT)]


def test_mismatch_reports_the_difference():
    tx, warnings, info = parse_pdf_detailed((FX / "stmt_columns_mismatch.pdf").read_bytes(), "a", "i")
    assert info["reconciled"] is False and info["difference"] == "50.00"
    _, _, ok = parse_pdf_detailed((FX / "stmt_columns.pdf").read_bytes(), "a", "i")
    assert ok["reconciled"] is True and ok["difference"] == "0.00"
