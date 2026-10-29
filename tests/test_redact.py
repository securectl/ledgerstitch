# SPDX-License-Identifier: AGPL-3.0-only
"""Redaction tests. All descriptors are synthetic (names/numbers invented)."""
import json
import re

import pytest

from ledgerstitch.security.redact import (PERSON, Redacted, mask_identifiers, payload_preview, redact,
                                          redact_batch)

# --------------------------------------------------------------------------- exact outputs
EXACT = [
    ("ORIG CO NAME:CARDMEMBER SERV ORIG ID:5911111111 DESC DATE:251219 CO ENTRY DESCR:WEB PYMT "
     "SEC:CCD TRACE#:042000010860929 EED:251219 IND ID:***********9501 IND NAME:PAYABLE,ACCOUNTS "
     "TRN: 3530860929TC", "CARDMEMBER SERV WEB PYMT"),
    ("Zelle to John Smith on 03/12 Ref #Pp0Abc123", "ZELLE TO <PERSON>"),
    ("Zelle from Maria Gonzalez on 11/02 Ref # BAC0xyz9", "ZELLE FROM <PERSON>"),
    ("Zelle Payment To Jane Doe Jpm99b3xyz1a", "ZELLE PAYMENT TO <PERSON>"),
    ("Zelle Payment From PRIYA RAMAN BACv8q3z2k1", "ZELLE PAYMENT FROM <PERSON>"),
    ("AMAZON MKTPL*BJ1M70883", "AMAZON MKTPL"),
    ("Walmart 6750 Murphy Road 75048 TX United States", "WALMART"),
    ("GOOGLE *CLOUD 3DXZ7Q g.co/helppay#", "GOOGLE *CLOUD G.CO"),
    ("PAYPAL *JOHNDOE 4029357733", "PAYPAL *<PERSON>"),
    ("PAYPAL *NETFLIX 4029357733 CA", "PAYPAL *NETFLIX"),
    ("VENMO *KEVIN OBRIEN", "VENMO *<PERSON>"),
    ("CASH APP*JANE DOE*", "CASH APP *<PERSON>"),
    ("VENMO PAYMENT 1023456789 WEB ID: 3264681992", "VENMO PAYMENT"),
    ("GUSTO DES:NET ID:7XB2 INDN:JOHN SMITH CO ID:1234567 PPD", "GUSTO NET"),
    ("ONLINE TRANSFER REF #IBEXYZ123 TO EVERYDAY CHECKING XXXXXX1234 ON 03/12/25",
     "ONLINE TRANSFER TO EVERYDAY CHECKING"),
    ("PURCHASE AUTHORIZED ON 03/12 STARBUCKS STORE 12345 SEATTLE WA S385071234567890 CARD 1234",
     "STARBUCKS STORE"),
    ("NETFLIX.COM 866-579-7172 CA", "NETFLIX.COM"),
    ("APPLE.COM/BILL 866-712-7753 CA", "APPLE.COM/BILL"),
    ("AMAZON.COM AMZN.COM/BILLWA", "AMAZON.COM AMZN.COM/BILL"),
    ("7-ELEVEN 12345 DALLAS TX", "7-ELEVEN"),
    ("SQ *JOES COFFEE Austin TX", "SQ *JOES COFFEE"),
    ("TST* PIZZA PLACE - DALLAS TX", "TST* PIZZA PLACE"),
    ("UBER *TRIP HELP.UBER.COM CA", "UBER *TRIP HELP.UBER.COM"),
    ("ONLINE PAYMENT TO ATMOS ENERGY", "ONLINE PAYMENT TO ATMOS ENERGY"),
    ("Zelle from ACME PLUMBING LLC on 01/02", "ZELLE FROM ACME PLUMBING LLC"),
    ("APPLE CASH BALANCE", "APPLE CASH BALANCE"),
    ("MURPHY USA 1234 MURPHY TX", "MURPHY USA"),
    ("BILL PAY MR JOHN SMITH ON-LINE xxxxx1234 ON 03-12", "BILL PAY <PERSON>"),
    ("ATM WITHDRAWAL 1234 MAIN ST SEATTLE WA", "ATM WITHDRAWAL"),
    ("PAYMENT TO CHASE CARD ENDING IN 1234", "PAYMENT TO CHASE CARD"),
    ("1PASSWORD.COM", "1PASSWORD.COM"),
    ("DD *DOORDASH CHIPOTLE", "DD *DOORDASH CHIPOTLE"),
    ("Café Rouge", "CAFE ROUGE"),
    ("CHECK 1234", "CHECK"),
    ("   netflix.com   ", "NETFLIX.COM"),
    ("STARBUCKS SEATTLE WA", "STARBUCKS"),
    ("SHELL OIL 57444212345 PLANO TX", "SHELL OIL"),
    ("TRANSFER FROM SAVINGS ...4821", "TRANSFER FROM SAVINGS"),
    ("CAPITAL ONE MOBILE PMT", "CAPITAL ONE MOBILE PMT"),
    ("AUTOMATIC PAYMENT - THANK YOU", "AUTOMATIC PAYMENT THANK YOU"),
    ("", ""),
]


@pytest.mark.parametrize("raw,expected", EXACT, ids=[e[0][:40] for e in EXACT])
def test_exact(raw, expected):
    assert redact(raw).text == expected


# --------------------------------------------------------------------------- partial expectations
def test_wire_keeps_bank_drops_beneficiary():
    r = redact("WT FED#01234 ZHEJIANG TAILONG /FTR/BNF=SOME PERSON SRF# 123")
    assert "ZHEJIANG TAILONG" in r.text and r.text.startswith("WT")
    assert "SOME" not in r.text and "PERSON" not in r.text.replace(PERSON, "")
    assert "person" in r.removed


def test_wire_structured_fields():
    r = redact("WIRE TYPE:WIRE OUT DATE:251219 TIME:1103 ET TRN:2025121900123 SERVICE REF:003456 "
               "BNF:LINDA PARK ID:0012345 BNF BK:FIRST BANK ID:0959 PMT DET:INVOICE 77 RENT")
    assert "LINDA" not in r.text and "PARK" not in r.text and "RENT" not in r.text
    assert "WIRE OUT" in r.text


def test_email_removed():
    r = redact("PAYMENT RECEIVED bob.smith@gmail.com THANK YOU")
    assert "@" not in r.text and "BOB" not in r.text and "email" in r.removed


@pytest.mark.parametrize("phone", ["(800) 555-1212", "800-555-1212", "800.555.1212",
                                   "+1 800 555 1212", "1-800-FLOWERS", "+44 20 7946 0958"])
def test_phones(phone):
    r = redact(f"ACME SUPPORT {phone}")
    assert r.text == "ACME SUPPORT" and "phone" in r.removed


@pytest.mark.parametrize("mask", ["XXXX1234", "****1234", "...1234", "XXXXXXXXXXXX1234",
                                  "ENDING IN 1234", "ACCT #98765"])
def test_card_masks(mask):
    r = redact(f"CITI AUTOPAY {mask}")
    assert r.text == "CITI AUTOPAY" and "id" in r.removed


def test_url_query_dropped():
    r = redact("SPOTIFY https://spotify.com/acct?id=abc123&user=jdoe")
    assert r.text == "SPOTIFY SPOTIFY.COM"
    assert "JDOE" not in r.text


def test_amounts_and_dates_removed():
    r = redact("TARGET 01/15/2025 $45.67 2025-01-15 JAN 15 11:03AM")
    assert r.text == "TARGET"


def test_po_box_and_suite():
    assert redact("ACME CORP PO BOX 4455 SUITE 200").text == "ACME CORP"


def test_transfer_to_person():
    assert redact("ONLINE TRANSFER TO DAVID CHEN ON 05/05").text == "ONLINE TRANSFER TO <PERSON>"


def test_real_time_transfer_from_label():
    r = redact("REAL TIME TRANSFER RECD FROM ABA/CONTR BNK-021000021 FROM: JOHN SMITH REF: 123ABC")
    assert "JOHN" not in r.text and "SMITH" not in r.text


def test_p2p_memo_dropped():
    r = redact('Zelle payment from Rahul Verma for "Ana birthday"; Conf# abc123')
    assert r.text == "ZELLE PAYMENT FROM <PERSON>"


def test_person_name_containing_brand_word_is_still_person():
    # 'SHELL' is a brand keyword, but 'JOHN SHELL' is not all-known -> person
    assert redact("PAYMENT TO JOHN SHELL").text == "PAYMENT TO <PERSON>"


def test_payroll_ind_name():
    r = redact("ADP PAYROLL ORIG CO NAME:ADP WAGE PAY CO ENTRY DESCR:WAGE PAY IND NAME:SMITH, JOHN")
    assert "SMITH" not in r.text and "JOHN" not in r.text


def test_non_ascii_names_dropped():
    assert redact("ALIPAY 张伟 SHANGHAI").text == "ALIPAY SHANGHAI"


def test_removed_kinds_known():
    allowed = {"person", "digits", "phone", "email", "address", "id"}
    for raw, _ in EXACT:
        assert set(redact(raw).removed) <= allowed


def test_returns_dataclass():
    r = redact("NETFLIX.COM")
    assert isinstance(r, Redacted) and r.removed == []


def test_non_string_input():
    assert redact(None).text == ""  # type: ignore[arg-type]


# --------------------------------------------------------------------------- properties
NAMES = ["JOHN SMITH", "MARIA GONZALEZ", "PRIYA RAMAN", "KEVIN OBRIEN", "JANE DOE", "DAVID CHEN",
         "RAHUL VERMA", "LINDA PARK", "AISHA KHAN", "TOM NGUYEN"]
TEMPLATES = [
    "Zelle to {n} on 03/12 Ref #Pp0Abc123",
    "Zelle Payment From {n} BACq1234x",
    "ZELLE {n} 2025-01-04",
    "VENMO *{n}",
    "VENMO PAYMENT TO {n} 1234567",
    "CASH APP*{n}*",
    "SQC*{n} 4155551212",
    "APPLE CASH SENT MONEY TO {n}",
    "PAYPAL *{nj} 402-935-7733",
    "ONLINE TRANSFER TO {n} REF #XYZ998",
    "TRANSFER FROM {n} CONF 99812",
    "PAYMENT TO {n}",
    "BILL PAY {n} ON-LINE XXXXX1234",
    "MONEY SENT TO {n}",
    "RECEIVED FROM {n} 01/02",
    "WIRE TO {n} 0091",
    "WT FED#99812 SOME BANK /BNF/{n} SRF# 9981",
    "INTL WIRE OUT BNF={n} OBI=GIFT",
    "ORIG CO NAME:ACME CORP ORIG ID:1234567890 CO ENTRY DESCR:PAYROLL IND NAME:{n} TRN:12345",
    "GUSTO DES:NET ID:7XB2 INDN:{n} CO ID:1234567 PPD",
    "DEPOSIT FROM {n} MOBILE",
    "CHECK PAID TO {n} 0042",
    "Zelle payment to {n} for rent; Conf# k9x8b7",
]
SAMPLES = [t.format(n=n, nj=n.replace(" ", "")) for t in TEMPLATES for n in NAMES] + [e[0] for e in EXACT] + [
    "AMEX EPAYMENT ACH PMT W4826 0000001234567",
    "DISCOVER E-PAYMENT 8841 WEB ID: 2510020270",
    "IRS USATAXPYMT 270567812345678",
    "AT&T *BILL PAYMENT 800-331-0500 TX",
    "COMCAST CABLE COMM 800-COMCAST CO",
    "HOME DEPOT #6512 PLANO TX 75093",
    "BUC-EE'S #45 MELISSA TX",
    "SP * ACMESHOP.COM 4155550000 CA",
    "LYFT *RIDE TUE 8PM 8552800278 CA",
    "INTEREST PAYMENT 0.0100% APY",
    "CHECK #10234 IMAGE",
    "DEPOSIT ID NUMBER 123456",
    "Transfer from CHK 1556 Confirmation# 1234567890",
]


@pytest.mark.parametrize("raw", SAMPLES)
def test_property_no_digits_runs_email_or_names(raw):
    out = redact(raw).text
    assert not re.search(r"\d{4,}", out), out
    assert "@" not in out, out
    stripped = out.replace(PERSON, "")
    for n in NAMES:
        for part in n.split() + [n.replace(" ", "")]:
            assert not re.search(rf"\b{part}\b", stripped), (raw, out)
    assert out == out.upper()
    assert out == " ".join(out.split())
    assert re.fullmatch(r"[A-Z0-9 &'.\-*/+]*", stripped), out


@pytest.mark.parametrize("raw", SAMPLES)
def test_property_idempotent(raw):
    once = redact(raw).text
    assert redact(once).text == once


def test_property_no_mixed_ids():
    for raw in SAMPLES:
        for tok in redact(raw).text.replace(PERSON, "").split():
            assert sum(c.isdigit() for c in tok) < 3, (raw, tok)


# --------------------------------------------------------------------------- batch & payload
def test_redact_batch_keyed_by_input():
    inp = ["NETFLIX.COM", "Zelle to John Smith", "NETFLIX.COM"]
    out = redact_batch(inp)
    assert set(out) == {"NETFLIX.COM", "Zelle to John Smith"}
    assert out["Zelle to John Smith"].text == "ZELLE TO <PERSON>"


def test_payload_preview_exact_format():
    s = payload_preview(["NETFLIX.COM", "Zelle to John Smith 03/12", "netflix.com", "", "1234"])
    assert s == '{"descriptors": ["NETFLIX.COM", "ZELLE TO <PERSON>"]}'
    assert json.loads(s) == {"descriptors": ["NETFLIX.COM", "ZELLE TO <PERSON>"]}


def test_payload_preview_rereacts_raw_input():
    s = payload_preview(["AMAZON MKTPL*BJ1M70883 john@x.com 555-123-4567"])
    assert "@" not in s and not re.search(r"\d{4,}", s)


def test_payload_empty():
    assert payload_preview([]) == '{"descriptors": []}'


@pytest.mark.parametrize("raw", [
    "ZELLE TO 03/12 JOHN SMITH",
    'PAYMENT TO "JOHN SMITH"',
    "VENMO * 1234 JOHN SMITH",
    "TRANSFER TO REF #123 JOHN SMITH",
    "ZELLE FROM XXXX1234 JOHN SMITH",
    "PAYMENT TO (JOHN SMITH)",
])
def test_regression_name_after_removed_token(raw):
    out = redact(raw).text
    assert "JOHN" not in out and "SMITH" not in out and PERSON in out


# ---------------------------------------------------------------- people outside P2P phrases
@pytest.mark.parametrize("raw,expected", [
    ("SQ *JOHN SMITH", "SQ *<PERSON>"),
    ("SQUARE *JANE DOE", "SQUARE *<PERSON>"),
    ("TST* JOE SMITH", "TST *<PERSON>"),
    ("ACH CREDIT JOHN SMITH PAYROLL", "ACH CREDIT <PERSON> PAYROLL"),
    ("JANE DOE", "<PERSON>"),
    ("KIM PATEL", "<PERSON>"),
    ("SQ *JOHN SMITH PLUMBING", "SQ *<PERSON> PLUMBING"),
    ("MARIA LOPEZ DDS", "<PERSON> DDS"),
])
def test_people_behind_processors_payroll_and_bare_names(raw, expected):
    assert redact(raw).text == expected
    assert "person" in redact(raw).removed


@pytest.mark.parametrize("raw", ["SQ *JOES COFFEE", "JOHN DEERE", "TRADER JOE S", "PATEL BROTHERS",
                                 "GUSTO PAYROLL", "WELLS FARGO", "PAPA JOHNS", "SQ *KEVIN LAWN CARE",
                                 "HOME DEPOT", "GRACE CHURCH"])
def test_businesses_named_like_people_are_kept(raw):
    assert "<PERSON>" not in redact(raw).text


def test_first_and_surname_pair_mid_descriptor():
    assert redact("PAINTING BY MARK GREEN").text == "PAINTING BY <PERSON>"


# ---------------------------------------------------------------- SSNs
@pytest.mark.parametrize("raw", ["PAYROLL SSN 900-45-6789 ACME", "ACME CORP 900-45-6789", "SSN: 123456789 ACME",
                                 "SOC SEC NO 123 45 6789 ACME"])
def test_ssn_removed_from_descriptors(raw):
    r = redact(raw)
    assert not re.search(r"\d", r.text)
    assert {"id", "digits"} & set(r.removed)
    assert "6789" not in r.text and "123" not in r.text


# ---------------------------------------------------------------- mask_identifiers (pay stubs, exports)
@pytest.mark.parametrize("raw,expected", [
    ("SSN: 900-45-6789", "SSN: XXX-XX-6789"),
    ("Employee Jane Doe 900-45-6789 Acme", "Employee Jane Doe XXX-XX-6789 Acme"),
    ("SSN 123456789", "SSN XXXXX6789"),
    ("Social Security No. 987 65 4321", "Social Security No. XXX XX 4321"),
    ("Account: 000123456789", "Account: XXXXXXXX6789"),
    ("Acct # 1234 5678 9012", "Acct # XXXXXXXX9012"),
    ("Routing Number 021000021", "Routing Number XXXXX0021"),
    ("Employee ID: 00987654", "Employee ID: XXXX7654"),
    ("Company Code ZX/ABC 01234567", "Company Code ZX/ABC XXXX4567"),
    ("SSN: XXX-XX-1234", "SSN: XXX-XX-1234"),
    # a labelled number stays on its line and stops at a wide gap (1099 statements)
    ("Account 900345678\n\n1099-DIV", "Account XXXXX5678\n\n1099-DIV"),
    ("Account No. 900123456    2025 Form", "Account No. XXXXX3456    2025 Form"),
    ("Recipient TIN: XXX-XX-6789\n2025 Form 1099-DIV", "Recipient TIN: XXX-XX-6789\n2025 Form 1099-DIV"),
])
def test_mask_identifiers(raw, expected):
    assert mask_identifiers(raw) == expected
    assert mask_identifiers(expected) == expected            # idempotent


@pytest.mark.parametrize("raw", [
    "Regular   98.9584   80.00   7 916 67   47 500 02",       # space-separated amounts survive
    "Social Security Tax      -467 60      3 115 60",
    "Social Security 310 00 1 550 00",
    "Salary 5,000.00 105,000.00 Total 100000.00",
    "Pay period: 01/01/2025 - 01/15/2025",
    "Medicare 916 67 1234",
])
def test_mask_identifiers_keeps_amounts_and_dates(raw):
    assert mask_identifiers(raw) == raw
