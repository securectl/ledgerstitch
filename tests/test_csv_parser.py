# SPDX-License-Identifier: AGPL-3.0-only
from decimal import Decimal
from pathlib import Path

import pytest

from ledgerstitch.ingest.csv_parser import parse_csv, parse_csv_detailed, preview_csv
from ledgerstitch.ingest.detect import ColumnMapping, FormatProfile, detect
from ledgerstitch.models import AccountKind, Direction

FX = Path(__file__).parent / "fixtures"


def parse(fixture, filename="x.csv"):
    data = (FX / fixture).read_bytes()
    prof = detect(filename, data)
    return parse_csv(data, prof, "acct-1", "imp-1")


def totals(txns):
    out = sum((t.amount for t in txns if t.direction == Direction.OUT), Decimal(0))
    inn = sum((t.amount for t in txns if t.direction == Direction.IN), Decimal(0))
    return out, inn


def test_chase_card_directions_seq_and_bank_category():
    tx = parse("chase_card.csv")
    assert len(tx) == 6                          # zero row skipped
    assert [t.direction for t in tx] == [Direction.OUT, Direction.OUT, Direction.OUT, Direction.IN,
                                         Direction.IN, Direction.OUT]
    # two identical $5 coffees on the same day are both kept with distinct ids
    assert tx[0].amount == tx[1].amount == Decimal("5.00")
    assert tx[0].id != tx[1].id and tx[0].seq != tx[1].seq
    assert tx[0].bank_category == "Food & Drink"
    assert tx[0].merchant == "BLUEBIRD COFFEE"
    assert all(t.amount > 0 for t in tx)
    assert all(t.import_id == "imp-1" and t.account_id == "acct-1" for t in tx)
    assert totals(tx) == (Decimal("225.00"), Decimal("340.00"))


def test_reimport_gives_same_ids():
    a, b = parse("chase_card.csv"), parse("chase_card.csv")
    assert [t.id for t in a] == [t.id for t in b]


def test_chase_checking_trailing_comma():
    tx = parse("chase_checking.csv")
    assert len(tx) == 5
    assert totals(tx) == (Decimal("425.25"), Decimal("2625.00"))
    assert tx[1].merchant == "WIDGETCO PAYROLL"


def test_citi_negative_credits_are_money_in():
    tx = parse("citi.csv")
    assert [t.direction for t in tx] == [Direction.OUT, Direction.IN, Direction.OUT, Direction.IN]
    assert all(t.amount > 0 for t in tx)


def test_capital_one_skips_zero_and_signs():
    tx = parse("capone_360.csv", "4444-Credit Card.csv")
    assert len(tx) == 2
    assert tx[0].direction == Direction.OUT and tx[1].direction == Direction.IN


def test_capital_one_card_thousands():
    tx = parse("capone_card.csv")
    assert tx[0].amount == Decimal("1024.00") and tx[0].direction == Direction.OUT
    assert tx[1].direction == Direction.IN
    assert tx[0].bank_category == "Merchandise"


def test_gemini_positive_is_charge_and_empty_description():
    tx = parse("gemini.csv")
    assert [t.direction for t in tx] == [Direction.OUT, Direction.IN, Direction.IN]
    assert tx[1].description == "payment_transaction"      # empty description → type
    assert tx[0].date.isoformat() == "2025-06-01"


@pytest.mark.parametrize("fixture,out,inn", [
    ("amex.csv", "60.00", "60.00"),
    ("amex_member.csv", "12.00", "0"),
    ("amex_extended.csv", "99.00", "0"),
    ("discover.csv", "12.00", "12.00"),
    ("apple_card.csv", "45.00", "55.00"),
    ("boa_card.csv", "64.20", "64.20"),
    ("us_bank.csv", "27.80", "900.00"),
    ("chase_card_nocard.csv", "19.99", "19.99"),
])
def test_profile_totals(fixture, out, inn):
    name = "amex_activity.csv" if fixture.startswith("amex") else "x.csv"
    assert totals(parse(fixture, name)) == (Decimal(out), Decimal(inn))


def test_boa_checking_skips_summary_and_balance_row():
    tx = parse("boa_checking.csv")
    assert len(tx) == 2                         # "Beginning balance" row has no amount
    assert totals(tx) == (Decimal("200.00"), Decimal("500.00"))


def test_wells_fargo_headerless():
    tx = parse("wells_fargo.csv", "wf.csv")
    assert len(tx) == 3
    assert totals(tx) == (Decimal("814.99"), Decimal("1500.00"))
    assert tx[0].merchant == "NIMBUS APPS"


def test_generic_split_with_footer_totals_and_parentheses():
    data = (FX / "generic_split.csv").read_bytes()
    prof = detect("bank.csv", data)
    tx, warnings = parse_csv_detailed(data, prof, "a", "i")
    assert len(tx) == 3
    assert totals(tx) == (Decimal("1265.00"), Decimal("300.00"))
    assert any("non-transaction" in w for w in warnings)        # the Total footer


def test_generic_card():
    tx = parse("generic_card.csv", "visa card.csv")
    assert totals(tx) == (Decimal("50.00"), Decimal("50.00"))
    assert tx[1].direction == Direction.IN


def test_unsigned_amount_uses_type_column():
    data = (b"Date,Description,Type,Amount\n01/02/2025,FOO STORE,Sale,10.00\n"
            b"01/03/2025,FOO STORE,Return,4.00\n01/04/2025,THANKS,Payment,6.00\n")
    prof = FormatProfile("manual", "X", AccountKind.CREDIT_CARD,
                         ColumnMapping(date="Date", description="Description", amount="Amount", type_col="Type"),
                         1.0, None)
    tx = parse_csv(data, prof, "a", "i")
    assert [t.direction for t in tx] == [Direction.OUT, Direction.IN, Direction.IN]


def test_manual_mapping_without_header_row_and_bom_blank_lines():
    data = "﻿When,What,How Much\r\n\r\n01/02/2025,\"ACME, WIDGETS\",\"-$1,012.50\"\r\n\r\n01/03/2025,REFUND THING,4.00\r\n".encode()
    prof = FormatProfile("manual", "Unknown", AccountKind.OTHER,
                         ColumnMapping(date="When", description="What", amount="How Much"), 1.0, None)
    tx = parse_csv(data, prof, "a", "i")
    assert len(tx) == 2
    assert tx[0].description == "ACME, WIDGETS" and tx[0].amount == Decimal("1012.50")
    assert tx[0].direction == Direction.OUT and tx[1].direction == Direction.IN


def test_no_mapping_returns_nothing():
    prof = FormatProfile("unknown", "Unknown", AccountKind.OTHER, None, 0.0, None)
    tx, w = parse_csv_detailed(b"a,b\n1,2\n", prof, "a", "i")
    assert tx == [] and w


def test_preview():
    p = preview_csv((FX / "boa_checking.csv").read_bytes(), rows=2)
    assert p["columns"] == ["Date", "Description", "Amount", "Running Bal."]
    assert len(p["rows"]) == 2
    p = preview_csv((FX / "wells_fargo.csv").read_bytes())
    assert p["columns"][0] == "col0" and len(p["rows"]) == 3
    p = preview_csv(b"When,What,How Much\n01/02/2025,ACME,-12.50\n")
    assert p["columns"] == ["When", "What", "How Much"]
