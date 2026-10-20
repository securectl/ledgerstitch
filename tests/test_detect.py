# SPDX-License-Identifier: AGPL-3.0-only
from decimal import Decimal
from pathlib import Path

import pytest

from ledgerstitch.ingest.detect import (detect, filename_hints, infer_date_format, parse_date, parse_money)
from ledgerstitch.models import AccountKind

FX = Path(__file__).parent / "fixtures"


def load(name):
    return (FX / name).read_bytes()


@pytest.mark.parametrize("fixture,filename,pid,inst,kind", [
    ("chase_card.csv", "Chase1111_Activity20250101_20251231_20260101.CSV", "chase_card", "Chase", AccountKind.CREDIT_CARD),
    ("chase_card_nocard.csv", "Chase2222_Activity.CSV", "chase_card", "Chase", AccountKind.CREDIT_CARD),
    ("chase_checking.csv", "Chase3333_Activity_20260101.CSV", "chase_checking", "Chase", AccountKind.CHECKING),
    ("citi.csv", "citi-export.csv", "citi", "Citi", AccountKind.CREDIT_CARD),
    ("capone_360.csv", "4444-Credit Card - 4444_01-01-2025_12-31-2025.csv", "capital_one", "Capital One", AccountKind.CREDIT_CARD),
    ("capone_card.csv", "download.csv", "capital_one_card", "Capital One", AccountKind.CREDIT_CARD),
    ("gemini.csv", "GEMINI-2025-full-year-statement.csv", "gemini", "Gemini", AccountKind.CREDIT_CARD),
    ("amex.csv", "amex_activity.csv", "amex", "American Express", AccountKind.CREDIT_CARD),
    ("amex_member.csv", "activity.csv", "amex", "American Express", AccountKind.CREDIT_CARD),
    ("amex_extended.csv", "activity.csv", "amex_extended", "American Express", AccountKind.CREDIT_CARD),
    ("boa_checking.csv", "stmt.csv", "boa_checking", "Bank of America", AccountKind.CHECKING),
    ("boa_card.csv", "stmt.csv", "boa_card", "Bank of America", AccountKind.CREDIT_CARD),
    ("wells_fargo.csv", "Checking1.csv", "wells_fargo", "Wells Fargo", AccountKind.CHECKING),
    ("discover.csv", "Discover-Statement.csv", "discover", "Discover", AccountKind.CREDIT_CARD),
    ("us_bank.csv", "export.csv", "us_bank", "US Bank", AccountKind.CHECKING),
    ("apple_card.csv", "Apple Card Transactions - November 2025.csv", "apple_card", "Apple Card", AccountKind.CREDIT_CARD),
])
def test_known_profiles(fixture, filename, pid, inst, kind):
    p = detect(filename, load(fixture))
    assert p.id == pid
    assert p.institution == inst
    assert p.kind == kind
    assert p.mapping is not None
    assert p.confidence >= 0.8


def test_sign_conventions():
    assert detect("x.csv", load("chase_card.csv")).mapping.negative_is_out is True
    assert detect("American Express activity.csv", load("amex.csv")).mapping.negative_is_out is False
    assert detect("x.csv", load("discover.csv")).mapping.negative_is_out is False
    assert detect("x.csv", load("apple_card.csv")).mapping.negative_is_out is False
    assert detect("x.csv", load("gemini.csv")).mapping.negative_is_out is False
    citi = detect("x.csv", load("citi.csv")).mapping
    assert citi.debit == "Debit" and citi.credit == "Credit" and citi.amount is None


def test_account_hints_from_filename_and_content():
    assert detect("Chase4012_Activity20250101_20251231_20260404.CSV", load("chase_card_nocard.csv")).account_hint == "4012"
    assert detect("9501-Credit Card - 9501_01-01-2025.csv", load("capone_360.csv")).account_hint == "9501"
    # content columns win: Card / Card No.
    assert detect("whatever.csv", load("chase_card.csv")).account_hint == "1111"
    assert detect("whatever.csv", load("capone_card.csv")).account_hint == "2222"
    assert detect("GEMINI-2025-full-year-statement.csv", load("gemini.csv")).account_hint is None


def test_filename_hints():
    h = filename_hints("Chase4012_Activity20250101_20251231_20260404.CSV")
    assert h["institution"] == "Chase" and h["last4"] == "4012"
    h = filename_hints("20251231-statements-3816-.pdf")
    assert h["last4"] == "3816"
    h = filename_hints("amex_ending_in_1005.csv")
    assert h["last4"] == "1005" and h["kind"] == AccountKind.CREDIT_CARD
    assert filename_hints("my_checking_export.csv")["kind"] == AccountKind.CHECKING
    assert filename_hints("random.csv") == {"institution": None, "last4": None, "kind": None}


def test_capital_one_vs_us_bank_same_header():
    assert detect("export.csv", load("capone_360.csv")).id == "capital_one"     # ISO dates
    assert detect("export.csv", load("us_bank.csv")).id == "us_bank"            # M/D/Y dates
    assert detect("usbank.csv", load("capone_360.csv")).id == "us_bank"         # filename wins


def test_capital_one_kind_from_content_when_no_hint():
    p = detect("export.csv", load("capone_360.csv"))
    assert p.kind == AccountKind.CREDIT_CARD          # "PAYMENT THANK YOU" credit row


def test_boa_checking_skips_summary_block():
    p = detect("stmt.csv", load("boa_checking.csv"))
    assert p.header_row == 6


def test_generic_split_columns():
    p = detect("my_bank.csv", load("generic_split.csv"))
    assert p.id == "generic"
    m = p.mapping
    assert m.date == "Posted On" and m.description == "Payee Name"
    assert m.debit == "Money Out" and m.credit == "Money In"
    assert 0 < p.confidence < 0.8


def test_generic_card_sign_from_payment_rows():
    p = detect("visa card.csv", load("generic_card.csv"))
    assert p.kind == AccountKind.CREDIT_CARD
    assert p.mapping.amount == "How Much"
    # payment row is negative → negative means money in → positive is out
    assert p.mapping.negative_is_out is False


def test_generic_card_majority_sign():
    data = b"Date,Merchant,Amount\n01/01/2025,ALPHA,-5.00\n01/02/2025,BETA,-6.00\n01/03/2025,GAMMA,2.00\n"
    p = detect("rewards card.csv", data)
    assert p.mapping.negative_is_out is True


def test_unknown_layout():
    p = detect("notes.csv", b"hello world\njust text\n")
    assert p.mapping is None and p.confidence == 0.0


def test_pdf_detection_returns_pdf_profile():
    p = detect("stmt.pdf", (FX / "stmt_columns.pdf").read_bytes())
    assert p.id == "pdf" and p.mapping is None
    assert p.kind == AccountKind.CHECKING
    assert p.account_hint == "9876"


@pytest.mark.parametrize("raw,val", [
    ("$1,234.56", Decimal("1234.56")), ("(12.00)", Decimal("-12.00")), ("-5", Decimal("-5")),
    ("12.00-", Decimal("-12.00")), ("12.00 CR", Decimal("-12.00")), ("-$5.00", Decimal("-5.00")),
    ("$-5.00", Decimal("-5.00")), ("+3.10", Decimal("3.10")), ("", None), ("abc", None), (None, None),
    ("1,024.00", Decimal("1024.00")), (".50", Decimal("0.50")),
])
def test_parse_money(raw, val):
    assert parse_money(raw) == val


def test_dates():
    assert parse_date("01/02/2025").isoformat() == "2025-01-02"
    assert parse_date("2025-01-02").isoformat() == "2025-01-02"
    assert parse_date("06/01/25", "%m/%d/%y").isoformat() == "2025-06-01"
    assert parse_date("not a date") is None
    assert infer_date_format(["01/02/2025", "12/31/2025"]) == "%m/%d/%Y"
    assert infer_date_format(["13/02/2025", "31/12/2025"]) == "%d/%m/%Y"


def test_bare_date_description_amount_is_not_assumed_amex():
    """Regression: a generic bank export with this header must not be claimed as
    Amex (positive = charge) and get every purchase flipped to money-in."""
    data = (b"Date,Description,Amount\n01/02/2025,LOCAL GROCER,-45.00\n01/03/2025,FUEL STOP,-30.00\n"
            b"01/05/2025,PAYROLL DEPOSIT,1500.00\n")
    p = detect("export.csv", data)
    assert p.id == "generic" and p.institution == "Unknown"
    assert p.confidence < 0.8
    assert p.mapping.negative_is_out is True
    from ledgerstitch.ingest.csv_parser import parse_csv
    from ledgerstitch.models import Direction
    tx = parse_csv(data, p, "a", "i")
    assert [t.direction for t in tx] == [Direction.OUT, Direction.OUT, Direction.IN]
