# SPDX-License-Identifier: AGPL-3.0-only
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ledgerstitch.ingest.ofx_parser import ofx_info, parse_ofx, parse_ofx_date, sniff_ofx
from ledgerstitch.models import Direction

FX = Path(__file__).parent / "fixtures" / "formats"
OFX_FILES = sorted(p for p in FX.glob("*") if p.suffix.lower() in (".ofx", ".qfx", ".qbo"))

SGML_HEAD = (b"OFXHEADER:100\r\nDATA:OFXSGML\r\nVERSION:102\r\nSECURITY:NONE\r\nENCODING:USASCII\r\n"
             b"CHARSET:1252\r\nCOMPRESSION:NONE\r\nOLDFILEUID:NONE\r\nNEWFILEUID:NONE\r\n\r\n")


def _trn(fitid, amt, name, dt="20250105", ttype="DEBIT", memo=None):
    s = f"<STMTTRN><TRNTYPE>{ttype}<DTPOSTED>{dt}<TRNAMT>{amt}<FITID>{fitid}<NAME>{name}"
    if memo:
        s += f"<MEMO>{memo}"
    return s + "</STMTTRN>\r\n"


def _bank(acctid, trns, accttype="CHECKING"):
    return (f"<STMTTRNRS><TRNUID>1<STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>000000000<ACCTID>{acctid}"
            f"<ACCTTYPE>{accttype}</BANKACCTFROM><BANKTRANLIST><DTSTART>20250101<DTEND>20250131\r\n"
            + "".join(trns) + "</BANKTRANLIST><LEDGERBAL><BALAMT>10.00<DTASOF>20250131</LEDGERBAL>"
            "</STMTRS></STMTTRNRS>\r\n")


def _sgml(*stmts, son="<FI><ORG>DEV BANK<FID>1234</FI>"):
    body = ("<OFX><SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20250131"
            f"<LANGUAGE>ENG{son}</SONRS></SIGNONMSGSRSV1><BANKMSGSRSV1>" + "".join(stmts)
            + "</BANKMSGSRSV1></OFX>")
    return SGML_HEAD + body.encode()


@pytest.mark.parametrize("path", OFX_FILES, ids=lambda p: p.name)
def test_parse_every_fixture(path):
    data = path.read_bytes()
    assert sniff_ofx(data)
    txns, warnings, info = parse_ofx(data, "acct", "imp", path.name)
    assert txns
    assert all(t.amount > 0 for t in txns)
    assert all(date(2000, 1, 1) <= t.date <= date(2030, 12, 31) for t in txns)
    assert all(t.description for t in txns)
    assert info["statements"] >= 1
    # only last4 of the account number is kept
    for a in info["accounts"]:
        assert a["last4"] is None or len(a["last4"]) == 4


def test_sgml_checking_signs_and_info():
    txns, warnings, info = parse_ofx((FX / "ofx1_sgml_checking.ofx").read_bytes(), "a", "i")
    assert info["format"] == "OFX" and info["version"] == "1"
    assert info["org"] == "SAMPLE BANK" and info["fid"] == "99999" and info["intu_bid"] is None
    assert info["accounts"] == [{"kind": "checking", "last4": "5678", "currency": "USD"}]
    assert info["ledger_balance"] == "3370.50"
    by = {t.description.split()[0]: t for t in txns}
    assert by["SAMPLE"].direction == Direction.OUT and by["SAMPLE"].amount == Decimal("4.50")
    assert by["EXAMPLE"].direction == Direction.IN
    assert txns[0].date == date(2025, 1, 3)        # 20250103120000.000[-5:EST]
    assert warnings == []


def test_xml_credit_card():
    txns, warnings, info = parse_ofx((FX / "ofx2_xml_creditcard.ofx").read_bytes(), "a", "i")
    assert info["version"] == "2" and info["accounts"][0]["kind"] == "credit_card"
    pay = [t for t in txns if "PAYMENT" in t.description]
    assert pay and pay[0].direction == Direction.IN
    assert sum(1 for t in txns if t.direction == Direction.OUT) == 2
    # card payments are not reported as sign/type contradictions
    assert not any("disagrees" in w for w in warnings)
    assert "SAMPLE COFFEE CO SPRINGFIELD XX" in [t.description for t in txns]   # NAME + MEMO


def test_qfx_and_qbo_detected_by_intu_bid_and_extension():
    qfx = ofx_info((FX / "sample_savings.qfx").read_bytes())
    assert qfx["format"] == "QFX" and qfx["intu_bid"] == "99999"
    assert qfx["accounts"][0]["kind"] == "savings"
    qbo = ofx_info((FX / "sample_card.qbo").read_bytes(), filename="sample_card.qbo")
    assert qbo["format"] == "QBO" and qbo["intu_bid"] == "99998" and qbo["org"] is None
    assert ofx_info((FX / "ofx1_sgml_checking.ofx").read_bytes(), filename="x.qfx")["format"] == "QFX"


def test_timezone_dates():
    assert parse_ofx_date("20250105120000.000[-5:EST]") == date(2025, 1, 5)
    assert parse_ofx_date("20250105235959[+1]") == date(2025, 1, 5)
    assert parse_ofx_date("20250105") == date(2025, 1, 5)
    assert parse_ofx_date("garbage") is None


def test_unclosed_tags_lowercase_and_closed_leaves():
    data = _sgml(_bank("111122223333", [
        _trn("A1", "-1.00", "ONE &amp; CO"),
        "<stmttrn><trntype>CREDIT</trntype><dtposted>20250106</dtposted><trnamt>2.50</trnamt>"
        "<fitid>A2</fitid><name>TWO</name></stmttrn>\r\n",
    ]))
    txns, _, info = parse_ofx(data, "a", "i")
    assert [t.description for t in txns] == ["ONE & CO", "TWO"]
    assert [t.direction for t in txns] == [Direction.OUT, Direction.IN]


def test_multiple_statements_and_last4_only():
    full_a, full_b = "987654321111", "123456782222"
    data = _sgml(_bank(full_a, [_trn("X1", "-5.00", "ALPHA")]),
                 _bank(full_b, [_trn("X1", "-6.00", "BETA")], accttype="SAVINGS"))
    txns, warnings, info = parse_ofx(data, "a", "i")
    assert len(txns) == 2 and info["statements"] == 2
    assert [a["last4"] for a in info["accounts"]] == ["1111", "2222"]
    assert any("2 accounts" in w for w in warnings)
    blob = repr(info) + repr(warnings) + repr([t.to_dict() for t in txns])
    assert full_a not in blob and full_b not in blob


def test_fitid_determinism_and_overlap():
    f1 = _sgml(_bank("0000111", [_trn("F1", "-5.00", "ALPHA"), _trn("F2", "-6.00", "BETA")]))
    f2 = _sgml(_bank("0000111", [_trn("F2", "-6.00", "BETA"), _trn("F3", "-7.00", "GAMMA")]))
    a1 = parse_ofx(f1, "a", "i1")[0]
    a1b = parse_ofx(f1, "a", "i2")[0]
    assert [t.id for t in a1] == [t.id for t in a1b]
    b = parse_ofx(f2, "a", "i3")[0]
    assert a1[1].id == b[0].id           # same FITID in overlapping download → same id
    assert len({t.id for t in a1 + b}) == 3


def test_duplicate_fitid_and_zero_rows():
    data = _sgml(_bank("0000111", [_trn("D1", "-5.00", "ALPHA"), _trn("D1", "-5.00", "ALPHA"),
                                   _trn("Z1", "0.00", "NOTHING")]))
    txns, warnings, _ = parse_ofx(data, "a", "i")
    assert len(txns) == 1
    assert any("duplicate FITID" in w for w in warnings)
    assert any("zero-amount" in w for w in warnings)


def test_checknum_when_no_name_and_sign_type_warning():
    trn = ("<STMTTRN><TRNTYPE>CHECK<DTPOSTED>20250110<TRNAMT>-120.00<FITID>C1<CHECKNUM>1001</STMTTRN>"
           "<STMTTRN><TRNTYPE>FEE<DTPOSTED>20250111<TRNAMT>3.00<FITID>C2<NAME>FEE REVERSAL?</STMTTRN>")
    txns, warnings, _ = parse_ofx(_sgml(_bank("0000111", [trn])), "a", "i")
    assert txns[0].description == "CHECK #1001"
    assert any("disagrees" in w for w in warnings)


def test_xxe_is_not_resolved(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRETVALUE")
    data = (f'<?xml version="1.0"?>\n<?OFX OFXHEADER="200" VERSION="220"?>\n'
            f'<!DOCTYPE OFX [<!ENTITY xxe SYSTEM "file://{secret}">]>\n'
            "<OFX><SIGNONMSGSRSV1><SONRS><FI><ORG>X</ORG></FI></SONRS></SIGNONMSGSRSV1>"
            "<BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>99990000</ACCTID>"
            "<ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM><BANKTRANLIST><STMTTRN><TRNTYPE>DEBIT</TRNTYPE>"
            "<DTPOSTED>20250101</DTPOSTED><TRNAMT>-1.00</TRNAMT><FITID>1</FITID><NAME>&xxe;</NAME></STMTTRN>"
            "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>").encode()
    txns, warnings, info = parse_ofx(data, "a", "i")
    blob = repr([t.to_dict() for t in txns]) + repr(info) + repr(warnings)
    assert "TOPSECRETVALUE" not in blob
    assert len(txns) == 1


def test_sniff_negative():
    assert not sniff_ofx(b"Date,Description,Amount\n01/01/2025,X,1\n")
    assert not sniff_ofx(b"!Type:Bank\nD01/01/2025\n^\n")
