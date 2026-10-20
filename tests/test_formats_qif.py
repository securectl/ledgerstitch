# SPDX-License-Identifier: AGPL-3.0-only
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ledgerstitch.ingest.qif_parser import parse_qif, parse_qif_date, qif_info, sniff_qif
from ledgerstitch.models import Direction

FX = Path(__file__).parent / "fixtures" / "formats"
QIF_FILES = sorted(FX.glob("*.qif"))


@pytest.mark.parametrize("path", QIF_FILES, ids=lambda p: p.name)
def test_parse_every_fixture(path):
    data = path.read_bytes()
    assert sniff_qif(data)
    txns, warnings, info = parse_qif(data, "acct", "imp")
    assert txns and info["format"] == "QIF"
    assert all(t.amount > 0 for t in txns)
    assert all(date(2000, 1, 1) <= t.date <= date(2030, 12, 31) for t in txns)


def test_bank_fixture_fields_and_splits():
    txns, warnings, info = parse_qif((FX / "qif_bank.qif").read_bytes(), "a", "i")
    assert info["kind"] == "checking"
    assert len(txns) == 4
    assert txns[0].direction == Direction.OUT and txns[0].bank_category == "Meals"
    assert txns[1].date == date(2025, 1, 5) and txns[1].amount == Decimal("2500.00")   # 01/05'25
    assert txns[1].direction == Direction.IN
    split = txns[3]
    assert split.amount == Decimal("300.00")          # record total, split lines ignored


def test_ccard_with_account_header():
    data = (FX / "qif_ccard.qif").read_bytes()
    assert qif_info(data)["kind"] == "credit_card"
    txns, _, _ = parse_qif(data, "a", "i")
    assert len(txns) == 4                              # !Account block is not a transaction
    assert [t.direction for t in txns] == [Direction.OUT, Direction.OUT, Direction.IN, Direction.IN]


def test_date_styles():
    assert parse_qif_date("1/ 5'25") == date(2025, 1, 5)
    assert parse_qif_date("1/5' 5") == date(2005, 1, 5)
    assert parse_qif_date("01/05/2025") == date(2025, 1, 5)
    assert parse_qif_date("1/5/25") == date(2025, 1, 5)
    assert parse_qif_date("1/5/98") == date(1998, 1, 5)
    assert parse_qif_date("2025-01-05") == date(2025, 1, 5)
    assert parse_qif_date("01-05-2025") == date(2025, 1, 5)
    assert parse_qif_date("5.1.2025", day_first=True) == date(2025, 1, 5)
    assert parse_qif_date("nope") is None


def test_day_first_resolved_across_file():
    data = b"!Type:Bank\nD13/01/2025\nT-1.00\nPA\n^\nD02/01/2025\nT-2.00\nPB\n^\n"
    txns, _, info = parse_qif(data, "a", "i")
    assert info["day_first"] is True
    assert [t.date for t in txns] == [date(2025, 1, 13), date(2025, 1, 2)]


def test_month_first_default_noted():
    data = b"!Type:Bank\nD02/01/2025\nT-2.00\nPB\n^\n"
    txns, _, info = parse_qif(data, "a", "i")
    assert txns[0].date == date(2025, 2, 1)
    assert info.get("date_order_note") == "date order assumed month/day"


def test_u_amount_memo_fallback_and_zero():
    data = (b"!Type:Cash\r\nD1/2/2025\r\nU-1,234.56\r\nMONLY A MEMO\r\n^\r\n"
            b"D1/3/2025\r\nT0.00\r\nPZERO\r\n^\r\nD1/4/2025\r\nT-5\r\nN1002\r\n^\r\n")
    txns, warnings, _ = parse_qif(data, "a", "i")
    assert txns[0].amount == Decimal("1234.56") and txns[0].description == "ONLY A MEMO"
    assert txns[1].description == "CHECK #1002"
    assert any("zero-amount" in w for w in warnings)


def test_investment_skipped():
    data = b"!Type:Invst\nD1/2/2025\nNBuy\nYACME\nT-100\n^\n!Type:Bank\nD1/3/2025\nT-1\nPX\n^\n"
    txns, warnings, _ = parse_qif(data, "a", "i")
    assert len(txns) == 1
    assert any("investment QIF not supported" in w for w in warnings)


def test_ids_deterministic():
    data = (FX / "qif_bank.qif").read_bytes()
    assert [t.id for t in parse_qif(data, "a", "i1")[0]] == [t.id for t in parse_qif(data, "a", "i2")[0]]


def test_sniff_negative():
    assert not sniff_qif(b"Date,Description,Amount\n")
    assert not sniff_qif(b"OFXHEADER:100\n")
