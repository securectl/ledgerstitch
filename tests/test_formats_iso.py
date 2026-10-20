# SPDX-License-Identifier: AGPL-3.0-only
"""CAMT.053 / MT940 / BAI2 (Enterprise formats, basic support)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ledgerstitch.ingest.iso20022_parser import (iso_info, parse_bai2, parse_camt, parse_iso, parse_mt940,
                                                  sniff_bai2, sniff_camt, sniff_iso, sniff_mt940)
from ledgerstitch.models import Direction

FX = Path(__file__).parent / "fixtures" / "formats"
ISO_FIXTURES = sorted(p for pat in ("camt05*.xml", "*.sta", "*.mt940", "*.bai", "*.bai2") for p in FX.glob(pat))

OUT, IN = Direction.OUT, Direction.IN


def _camt(entries: str, bal: str = "", acct: str = "<Id><IBAN>DE89370400440532013000</IBAN></Id>") -> bytes:
    return f"""<?xml version="1.0"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt><Stmt>
<Acct>{acct}<Ccy>EUR</Ccy></Acct>{bal}{entries}</Stmt></BkToCstmrStmt></Document>""".encode()


def _ntry(amt, ind, ref="R1", sts="<Sts>BOOK</Sts>", extra="", dt="<BookgDt><Dt>2025-02-03</Dt></BookgDt>",
          details=""):
    return (f"<Ntry><NtryRef>{ref}</NtryRef><Amt Ccy='EUR'>{amt}</Amt><CdtDbtInd>{ind}</CdtDbtInd>{extra}"
            f"{sts}{dt}<AcctSvcrRef>{ref}</AcctSvcrRef>{details}</Ntry>")


def _bal(code, amt, ind="CRDT"):
    return (f"<Bal><Tp><CdOrPrtry><Cd>{code}</Cd></CdOrPrtry></Tp><Amt Ccy='EUR'>{amt}</Amt>"
            f"<CdtDbtInd>{ind}</CdtDbtInd><Dt><Dt>2025-02-01</Dt></Dt></Bal>")


# --------------------------------------------------------------------------- researcher fixtures

@pytest.mark.parametrize("path", ISO_FIXTURES, ids=lambda p: p.name)
def test_fixture_parses_and_reconciles(path):
    data = path.read_bytes()
    assert sniff_iso(data) is not None
    txns, warnings, info = parse_iso(data, "acct", "imp")
    assert txns, warnings
    assert all(t.amount > 0 for t in txns)
    assert len({t.id for t in txns}) == len(txns)
    assert info["reconciled"] in (True, None)
    # only last4 ever leaves the parser
    for a in info["accounts"]:
        assert a["last4"] is None or len(a["last4"]) == 4
    assert "000012345678" not in repr(info) + repr(warnings) + repr(iso_info(data))


def test_sample_fixtures_agree_across_formats():
    """The researcher's three samples describe the same month: same net movement."""
    nets = {}
    for name in ("camt053_sample.xml", "mt940_sample.sta", "bai2_sample.bai"):
        p = FX / name
        if not p.exists():
            pytest.skip("researcher fixtures not present")
        txns, _, info = parse_iso(p.read_bytes(), "a", "i")
        nets[name] = sum(t.signed_amount for t in txns)
        assert info["reconciled"] is True
    assert len(set(nets.values())) == 1 and next(iter(nets.values())) == Decimal("2370.50")


def test_reimport_is_stable():
    p = FX / "camt053_sample.xml"
    if not p.exists():
        pytest.skip("fixture missing")
    a = [t.id for t in parse_iso(p.read_bytes(), "a", "i")[0]]
    b = [t.id for t in parse_iso(p.read_bytes(), "a", "j")[0]]
    assert a == b


# --------------------------------------------------------------------------- CAMT

def test_camt_signs_reversal_pending_and_description():
    entries = (
        _ntry("10.00", "DBIT", "A", details="<NtryDtls><TxDtls><RltdPties><Cdtr><Nm>SHOP ONE</Nm></Cdtr>"
              "<Dbtr><Nm>ME</Nm></Dbtr></RltdPties><RmtInf><Ustrd>ORDER</Ustrd><Ustrd>42X</Ustrd></RmtInf>"
              "</TxDtls></NtryDtls>")
        + _ntry("99.00", "CRDT", "B", details="<NtryDtls><TxDtls><RltdPties><Dbtr><Nm>CLIENT</Nm></Dbtr>"
                "</RltdPties></TxDtls></NtryDtls>")
        + _ntry("5.00", "DBIT", "C", extra="<RvslInd>true</RvslInd>")
        + _ntry("7.00", "DBIT", "D", sts="<Sts><Cd>PDNG</Cd></Sts>")
        + _ntry("3.00", "DBIT", "E", dt="<ValDt><DtTm>2025-02-04T10:00:00+01:00</DtTm></ValDt>",
                extra="<AddtlNtryInf>FEE</AddtlNtryInf>")
    )
    txns, warnings, info = parse_camt(_camt(entries), "a", "i")
    assert [(t.direction, t.amount) for t in txns] == [(OUT, Decimal("10.00")), (IN, Decimal("99.00")),
                                                       (IN, Decimal("5.00")), (OUT, Decimal("3.00"))]
    assert txns[0].description == "SHOP ONE ORDER 42X"
    assert txns[1].description == "CLIENT"
    assert txns[3].description == "FEE" and txns[3].date == date(2025, 2, 4)
    assert any("pending" in w for w in warnings)
    assert info["accounts"] == [{"last4": "3000", "currency": "EUR"}]
    assert "DE89370400440532013000" not in repr(info) + repr(warnings)
    assert info["reconciled"] is None


def test_camt_balance_reconciliation_true_and_false():
    entries = _ntry("10.00", "DBIT", "A") + _ntry("50.00", "CRDT", "B")
    ok = _camt(entries, _bal("OPBD", "100.00") + _bal("CLBD", "140.00"))
    bad = _camt(entries, _bal("OPBD", "100.00") + _bal("CLBD", "150.00"))
    neg = _camt(entries, _bal("OPBD", "20.00", "DBIT") + _bal("CLBD", "20.00"))
    assert parse_camt(ok, "a", "i")[2]["reconciled"] is True
    t, w, info = parse_camt(bad, "a", "i")
    assert info["reconciled"] is False and any("closing balance" in x for x in w)
    assert parse_camt(neg, "a", "i")[2]["reconciled"] is True


def test_camt_sniff_and_versions():
    assert sniff_camt(_camt(""))
    v8 = _camt("").replace(b"camt.053.001.02", b"camt.053.001.08")
    assert sniff_camt(v8) and iso_info(v8)["format"] == "CAMT.053"
    rpt = (b"<Document xmlns='urn:iso:std:iso:20022:tech:xsd:camt.052.001.02'><BkToCstmrAcctRpt><Rpt>"
           b"<Acct><Id><Othr><Id>99887766</Id></Othr></Id></Acct>"
           + _ntry("1.00", "DBIT").encode() + b"</Rpt></BkToCstmrAcctRpt></Document>")
    t, _, info = parse_iso(rpt, "a", "i")
    assert info["format"] == "CAMT.052" and len(t) == 1 and info["accounts"][0]["last4"] == "7766"
    assert not sniff_camt(b"Date,Description,Amount\n")
    assert not sniff_camt(b"<OFX><BANKMSGSRSV1></BANKMSGSRSV1></OFX>")


def test_camt_xxe_is_refused():
    evil = b"""<?xml version="1.0"?>
<!DOCTYPE d [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt><Stmt>
<Ntry><Amt>1.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><BookgDt><Dt>2025-01-01</Dt></BookgDt>
<AddtlNtryInf>&xxe;</AddtlNtryInf></Ntry></Stmt></BkToCstmrStmt></Document>"""
    txns, warnings, _ = parse_camt(evil, "a", "i")
    assert txns == []
    assert any("refused" in w for w in warnings)
    bomb = b"""<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt><Stmt>&b;</Stmt></BkToCstmrStmt></Document>"""
    assert parse_camt(bomb, "a", "i")[0] == []


def test_camt_malformed():
    t, w, _ = parse_camt(b"<Document xmlns='urn:iso:std:iso:20022:tech:xsd:camt.053.001.02'><BkToCstmrStmt>", "a", "i")
    assert t == [] and w


# --------------------------------------------------------------------------- MT940

MT940 = b"""{1:F01DEUTDEFFAXXX0000000000}{2:I940DEUTDEFFXXXXN}{4:
:20:STMT1
:25:10020030/1234567890
:28C:1/1
:60F:C241230EUR100,00
:61:2412301230D10,50NMSCNONREF//BREF1
:86:166?00SEPA-LASTSCHRIFT?20Rechnung 42?21 Dezember?32ACME
?33GMBH
:61:2412310102C200,NTRFREF2//BREF2
:86:/EREF/E2E1/REMI/USTD//INVOICE 77/NAME/CLIENT AB/
:61:241231RC5,00NMSCNONREF//BREF3
:86:REVERSAL OF A CREDIT
WITH TWO LINES
:61:241231RD2,00NMSCNONREF//BREF4
:86:REVERSAL OF A DEBIT
:62F:C241231EUR286,50
-}
{1:F01DEUTDEFFAXXX0000000000}{4:
:20:STMT2
:25:10020030/1234567890
:60M:C241231EUR286,50
:61:250102D1,00NMSCNONREF
:62M:C250102EUR290,00
-}"""


def test_mt940_signs_subfields_multiline_and_rollover():
    assert sniff_mt940(MT940)
    txns, warnings, info = parse_mt940(MT940, "a", "i")
    assert [(t.direction, t.amount) for t in txns] == [
        (OUT, Decimal("10.50")), (IN, Decimal("200")), (OUT, Decimal("5.00")), (IN, Decimal("2.00")),
        (OUT, Decimal("1.00"))]
    assert txns[0].description == "ACME GMBH Rechnung 42 Dezember"
    assert txns[1].description == "CLIENT AB INVOICE 77"
    assert txns[1].date == date(2025, 1, 2)          # entry date MMDD after Dec value date
    assert txns[2].description == "REVERSAL OF A CREDIT WITH TWO LINES"
    assert info["institution"] == "DEUTDEFF"
    assert info["accounts"][0]["last4"] == "7890" and len(info["accounts"]) == 2
    # statement 1 reconciles, statement 2 is off by 9 → overall False with a warning
    assert info["reconciled"] is False
    assert sum("closing balance" in w for w in warnings) == 1
    assert "1234567890" not in repr(info) + repr(warnings)


def test_mt940_plain_file_without_blocks():
    p = FX / "mt940_sample.sta"
    data = p.read_bytes() if p.exists() else MT940
    assert sniff_iso(data) == "mt940"
    assert not sniff_mt940(b":20:X\n:25:Y\n")


# --------------------------------------------------------------------------- BAI2

BAI = b"""01,BANKID,CUSTID,250301,0800,1,,,2/
02,CUSTID,BANKID,1,250228,2400,USD,2/
03,9876543210,USD,010,50000,,,015,61000,,/
16,195,20000,0,BREF1,,INCOMING WIRE FROM/
88,CLIENT ONE LLC
16,475,5000,V,250301,0900,BREF2,1002,/
16,699,4000,S,1000,2000,1000,BREF3,,ACCOUNT FEE, MONTHLY/
16,701,1234,0,BREF4,,LOAN PAYMENT/
16,409,0,0,BREF5,,ZERO/
49,111234,6/
98,111234,1,8/
99,111234,1,10/
"""


def test_bai2_type_codes_funds_types_and_continuation():
    assert sniff_bai2(BAI)
    txns, warnings, info = parse_bai2(BAI, "a", "i")
    assert [(t.direction, t.amount) for t in txns] == [(IN, Decimal("200.00")), (OUT, Decimal("50.00")),
                                                       (OUT, Decimal("40.00"))]
    assert txns[0].description == "INCOMING WIRE FROM CLIENT ONE LLC"
    assert txns[1].description == "CHECK 1002"          # V funds type consumed 2 extra fields
    assert txns[2].description == "ACCOUNT FEE, MONTHLY"  # S funds type consumed 3, text keeps commas
    assert all(t.date == date(2025, 2, 28) for t in txns)
    assert any("loan/custom" in w for w in warnings)
    assert info["accounts"] == [{"last4": "3210", "currency": "USD"}]
    assert info["reconciled"] is True                     # 500 + 200 - 50 - 40 == 610 (loan row skipped)
    off = BAI.replace(b"015,61000", b"015,60100")          # closing off by 9.00
    t2, w2, i2 = parse_bai2(off, "a", "i")
    assert i2["reconciled"] is False and any("closing" in w for w in w2)
    assert "9876543210" not in repr(info) + repr(warnings)


def test_bai2_reconciles_when_balanced():
    data = BAI.replace(b"015,61000", b"015,61000").replace(b"16,195,20000", b"16,195,21000")
    # 500 + 210 - 50 - 40 = 620 → set closing to 62000
    data = data.replace(b"015,61000", b"015,62000")
    assert parse_bai2(data, "a", "i")[2]["reconciled"] is True


def test_bai2_sniff_negative():
    assert not sniff_bai2(b"01,X,Y\n02,a\n")
    assert sniff_iso(b"Date,Amount\n1/1/2025,2\n") is None
    t, w, info = parse_iso(b"hello", "a", "i")
    assert t == [] and info["format"] is None
