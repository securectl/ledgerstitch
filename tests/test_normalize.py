# SPDX-License-Identifier: AGPL-3.0-only
import pytest

from ledgerstitch.ingest.normalize import normalize_merchant

CASES = [
    ("AMAZON MKTPL*BJ1M70883", "AMAZON MKTPL"),
    ("Amazon.com*AB12CD345 Amzn.com/bill WA", "AMAZON.COM"),
    ("SQ *BLUE BOTTLE COFFEE 0123 OAKLAND CA", "SQ *BLUE BOTTLE COFFEE"),
    ("SQ*BLUE BOTTLE COFFEE", "SQ *BLUE BOTTLE COFFEE"),
    ("TST* JOES DINER 1234 AUSTIN TX", "TST* JOES DINER"),
    ("TST*JOES DINER", "TST* JOES DINER"),
    ("Purchase authorized on 02/05 Acme Tools #12 Dallas TX S385021234567 Card 1234", "ACME TOOLS"),
    ("ORIG CO NAME:ACME PAYROLL ORIG ID:1234567 DESC DATE:010125 CO ENTRY DESCR:PAYROLL", "ACME PAYROLL"),
    ("STARBUCKS STORE 12345   PLANO        TX", "STARBUCKS STORE"),
    ("Zelle to Jane Roe on 01/05 Ref #RP0ABC123", "ZELLE TO JANE ROE"),
    ("GOOGLE *YOUTUBE", "GOOGLE *YOUTUBE"),
    ("PAYPAL *NETFLIX 4029357733 CA", "PAYPAL *NETFLIX"),
    ("99 RANCH MARKET #12 PLANO TX", "99 RANCH MARKET"),
    ("SHELL OIL 57444 DALLAS TX", "SHELL OIL"),
    ("DOORDASH*CHIPOTLE SAN FRANCISCO CA", "DOORDASH*CHIPOTLE"),
    ("APPLE.COM/BILL 866-712-7753 CA", "APPLE.COM/BILL"),
    ("NETFLIX.COM", "NETFLIX.COM"),
    ("ACME CO", "ACME CO"),            # "CO" is a business suffix here, not Colorado
    ("7-ELEVEN 12345 DALLAS TX", "7-ELEVEN"),
    ("  netflix.com   ", "NETFLIX.COM"),
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_normalize_examples(raw, expected):
    assert normalize_merchant(raw) == expected


@pytest.mark.parametrize("raw,_", CASES + [("PAYMENT THANK YOU - WEB", None), ("", None), ("#123", None)])
def test_idempotent_and_upper(raw, _):
    once = normalize_merchant(raw)
    assert normalize_merchant(once) == once
    assert once == once.upper()


def test_store_numbers_collapse_to_same_key():
    assert normalize_merchant("WALGREENS #1234") == normalize_merchant("WALGREENS #9876") == "WALGREENS"


def test_empty():
    assert normalize_merchant("") == ""
    assert normalize_merchant(None) == ""
