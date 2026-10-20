# SPDX-License-Identifier: AGPL-3.0-only
"""Format auto-learning: fingerprints, semantic inference, learned registry, catalog, format packs."""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from backend_fakes import install_missing
from ledgerstitch import store
from ledgerstitch.ingest import learning
from ledgerstitch.ingest.csv_parser import parse_csv_detailed
from ledgerstitch.ingest.detect import ColumnMapping, FormatProfile, detect, sniff_format
from ledgerstitch.ingest.dispatch import parse_any
from ledgerstitch.models import AccountKind, Direction
from ledgerstitch.security.vault import Vault

FX = Path(__file__).parent / "fixtures"
PASS = "correct horse battery"

# A layout no built-in profile knows. Amounts: negative = out; running balance.
NEW_LAYOUT = (b"Value Day,Booking Text,Counterparty,Debit/Credit,Amount,Saldo,Ref\n"
              b"01/03/2025,CARD PURCHASE,NORTHWIND PAPER CARD 4321,DEBIT,-25.00,975.00,R1\n"
              b"01/05/2025,TRANSFER,CLIENT ZETA INVOICE,CREDIT,500.00,1475.00,R2\n"
              b"01/09/2025,CARD PURCHASE,BLUE HERON CAFE CARD 4321,DEBIT,-4.50,1470.50,R3\n"
              b"01/12/2025,DIRECT DEBIT,CITYPOWER UTILITIES,DEBIT,-80.25,1390.25,R4\n"
              b"01/15/2025,TRANSFER,CLIENT OMEGA INVOICE,CREDIT,120.00,1510.25,R5\n")


def _rows(n=6, fmt="{m:02d}/{d:02d}/2025", header=b"Date,Payee,Amount,Category,Ref,Notes\n"):
    out = header
    for i in range(n):
        d = fmt.format(m=1 + i % 12, d=1 + (i * 3) % 27)
        out += f"{d},SHOP NUMBER {chr(65 + i)} LTD,-{10 + i}.25,Supplies,ID{i},x\n".encode()
    return out


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    install_missing(monkeypatch)


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "vaults" / "fmt.vault"


@pytest.fixture
def ws(vault_path):
    v = Vault.create(vault_path, PASS, "Fmt Co")
    w = store.Workspace(v)
    w.save()
    yield w
    v.lock()


# --------------------------------------------------------------------------- fingerprint

def test_fingerprint_stable_and_value_free():
    a = learning.fingerprint(NEW_LAYOUT)
    b = learning.fingerprint(NEW_LAYOUT.replace(b"NORTHWIND", b"SOUTHWIND").replace(b"-25.00", b"-26.00"))
    assert a.signature == b.signature and a.signature_sorted == b.signature_sorted
    assert a.column_count == 7 and a.preamble_lines == 0 and a.delimiter == ","
    kinds = {c.header: c.kind for c in a.columns}
    assert kinds["Value Day"] == "date" and kinds["Amount"] == "money" and kinds["Debit/Credit"] == "categorical"
    blob = json.dumps(a.to_dict())
    assert "NORTHWIND" not in blob and "975" not in blob and "INVOICE" not in blob
    # order-insensitive variant
    swapped = b"Amount,Date,Payee\n-1.00,01/02/2025,ALPHA SHOP\n-2.00,01/03/2025,BETA SHOP\n"
    plain = b"Date,Payee,Amount\n01/02/2025,ALPHA SHOP,-1.00\n01/03/2025,BETA SHOP,-2.00\n"
    fa, fb = learning.fingerprint(swapped), learning.fingerprint(plain)
    assert fa.signature != fb.signature and fa.signature_sorted == fb.signature_sorted


def test_fingerprint_preamble_and_delimiter():
    data = b"Account summary\nGenerated today\n\nDate;Payee;Amount\n01/02/2025;ALPHA SHOP;-1.00\n01/03/2025;BETA;-2.00\n"
    fp = learning.fingerprint(data)
    assert fp.delimiter == ";" and fp.header == ["Date", "Payee", "Amount"] and fp.preamble_lines >= 2


# --------------------------------------------------------------------------- semantic inference

def test_infer_new_layout_with_balance_sign_and_card():
    inf = learning.infer_mapping("export.csv", NEW_LAYOUT)
    m = inf.mapping
    assert m.date == "Value Day" and m.amount == "Amount" and m.balance == "Saldo"
    assert m.description == "Counterparty"
    assert m.type_col == "Debit/Credit"
    assert m.negative_is_out is True and inf.sign_method.startswith("running balance")
    assert inf.kind == AccountKind.CHECKING and inf.confidence >= 0.8
    assert any("sign" in e for e in inf.explanation)


def test_balance_proves_positive_is_out():
    data = (b"Date,Narrative,Amount,Balance\n01/02/2025,PAPER SHOP ONE,25.00,975.00\n"
            b"01/03/2025,CLIENT PAYS US,-100.00,1075.00\n01/04/2025,COFFEE PLACE,5.00,1070.00\n")
    inf = learning.infer_mapping("x.csv", data)
    assert inf.mapping.negative_is_out is False and inf.sign_method.startswith("running balance")
    # newest-first file order works too
    lines = data.split(b"\n")
    rev = b"\n".join([lines[0]] + list(reversed([l for l in lines[1:] if l]))) + b"\n"
    assert learning.infer_mapping("x.csv", rev).mapping.negative_is_out is False


def test_multilingual_headers_and_split_columns():
    data = ("Fecha,Concepto,Cargo,Abono\n03/01/2025,TIENDA UNO MADRID,12.50,\n"
            "05/01/2025,NOMINA EMPRESA,,900.00\n07/01/2025,FARMACIA CENTRAL,8.10,\n").encode()
    m = learning.infer_mapping("cuenta.csv", data).mapping
    assert (m.date, m.description, m.debit, m.credit) == ("Fecha", "Concepto", "Cargo", "Abono")
    data = ("Datum,Verwendungszweck,Betrag\n02.01.2025,BUERO MARKT GMBH,-19.99\n"
            "03.01.2025,KUNDE ZAHLUNG,250.00\n").encode()
    m = learning.infer_mapping("konto.csv", data).mapping
    assert (m.date, m.description, m.amount, m.date_format) == ("Datum", "Verwendungszweck", "Betrag", "%m.%d.%Y") \
        or (m.date, m.description, m.amount) == ("Datum", "Verwendungszweck", "Betrag")


def test_card_payment_rows_decide_sign_for_cards():
    data = (b"Posted,Merchant,Value\n01/02/2025,ALPHA STORE,12.00\n01/03/2025,PAYMENT THANK YOU,-50.00\n"
            b"01/04/2025,BETA STORE,8.00\n")
    inf = learning.infer_mapping("visa card.csv", data)
    assert inf.kind == AccountKind.CREDIT_CARD and inf.mapping.negative_is_out is False
    assert "payment" in inf.sign_method


def test_headerless_inference():
    data = b"01/02/2025,-12.00,ALPHA STORE ONE\n01/03/2025,300.00,PAYROLL DEPOSIT CO\n01/05/2025,-3.00,BETA\n"
    inf = learning.infer_mapping("x.csv", data)
    assert inf.mapping.date == "col0" and inf.mapping.amount == "col1" and inf.mapping.description == "col2"


# --------------------------------------------------------------------------- debit card / pending

def test_debit_card_last4_from_description_and_pending_skipped():
    data = (b"Date,Status,Description,Amount\n01/02/2025,Posted,DEBIT CARD PURCHASE NORTHWIND CARD 4321,-5.00\n"
            b"01/03/2025,Pending,DEBIT CARD PURCHASE BLUE HERON CARD 4321,-7.00\n"
            b"01/04/2025,Posted,CHECKCARD 0103 SHOP XXXXXXXXXXXX9876,-1.00\n01/05/2025,Posted,PAYROLL,100.00\n")
    p = detect("checking.csv", data)
    txns, warnings = parse_csv_detailed(data, p, "a", "i")
    assert len(txns) == 3 and any("pending" in w for w in warnings)
    assert [t.card_last4 for t in txns] == ["4321", "9876", None]


# --------------------------------------------------------------------------- registry: match / drift

def _learn(data, mapping=None, institution="Northwind Bank"):
    inf = learning.infer_mapping("a.csv", data)
    learned, rep = learning.record_confirmation([], "a.csv", data, mapping or inf.mapping, institution,
                                                AccountKind.CHECKING)
    assert rep["recorded"]
    return learned


def test_exact_match_reuses_learned_mapping():
    learned = _learn(NEW_LAYOUT)
    other = NEW_LAYOUT.replace(b"01/0", b"02/0").replace(b"CLIENT", b"CUSTOMER")
    p = learning.lookup("whatever.csv", other, learned)
    assert p is not None and p.id.startswith("learned:")
    assert p.recognition["source"] == "learned" and p.recognition["exact"] is True
    assert p.institution == "Northwind Bank" and p.confidence >= 0.9
    assert p.recognition["needs_confirmation"] is False
    txns, _, _ = parse_any("whatever.csv", other, p, "a", "i")
    assert len(txns) == 5


def test_fuzzy_match_with_renamed_column():
    base = _rows(8)
    learned = _learn(base, ColumnMapping(date="Date", description="Payee", amount="Amount", category="Category"))
    renamed = base.replace(b"Date,Payee,Amount", b"Date,Payee Name,Amount", 1)
    p = learning.lookup("x.csv", renamed, learned)
    assert p is not None and p.recognition["exact"] is False
    assert "description" in p.recognition["drift"]
    assert p.mapping.description == "Payee Name"            # only the drifted role was re-inferred
    assert p.mapping.category == "Category" and p.mapping.amount == "Amount"


def test_drift_date_format_change_is_reinferred():
    base = _rows(8)
    learned = _learn(base, ColumnMapping(date="Date", description="Payee", amount="Amount",
                                         date_format="%m/%d/%Y"))
    iso = _rows(8, fmt="2025-{m:02d}-{d:02d}")
    p = learning.lookup("x.csv", iso, learned)
    assert p is not None and "date_format" in p.recognition["drift"]
    assert p.mapping.date_format == "%Y-%m-%d"
    txns, _, _ = parse_any("x.csv", iso, p, "a", "i")
    assert len(txns) == 8


def test_unrelated_layout_does_not_match():
    learned = _learn(NEW_LAYOUT)
    assert learning.lookup("x.csv", (FX / "chase_card.csv").read_bytes(), learned) is None
    assert learning.lookup("x.ofx", (FX / "formats" / "ofx1_sgml_checking.ofx").read_bytes()
                           if (FX / "formats" / "ofx1_sgml_checking.ofx").exists() else b"OFXHEADER:100", learned) is None


def test_demotion_after_failures():
    learned = _learn(NEW_LAYOUT)
    p = learning.lookup("x.csv", NEW_LAYOUT, learned)
    for _ in range(2):
        learned, rep = learning.record_success(learned, "x.csv", NEW_LAYOUT, p, [], ["no transactions found"])
    assert learned[0]["status"] == "demoted" and learned[0]["failures"] == 2
    assert learning.lookup("x.csv", NEW_LAYOUT, learned) is None


def test_builtin_profiles_are_not_recorded():
    data = (FX / "chase_card.csv").read_bytes()
    p = detect("x.csv", data)
    txns, w, _ = parse_any("x.csv", data, p, "a", "i")
    learned, rep = learning.record_success([], "x.csv", data, p, txns, w)
    assert learned == [] and rep["recorded"] is False


def test_auto_inferred_clean_import_is_recorded_and_low_confidence_is_not():
    p = detect("export.csv", NEW_LAYOUT)
    assert p.mapping is not None and p.recognition["source"] == "inferred"
    txns, w, _ = parse_any("export.csv", NEW_LAYOUT, p, "a", "i")
    q = learning.import_quality(NEW_LAYOUT, p, txns, w)
    assert q["ok"] and q["balance_ok"] is True
    learned, rep = learning.record_success([], "export.csv", NEW_LAYOUT, p, txns, w)
    assert rep["recorded"] == (not p.recognition["needs_confirmation"])
    p.recognition["needs_confirmation"] = True
    learned2, rep2 = learning.record_success([], "export.csv", NEW_LAYOUT, p, txns, w)
    assert learned2 == [] and not rep2["recorded"]


def test_balance_mismatch_fails_quality():
    p = detect("export.csv", NEW_LAYOUT)
    bad = NEW_LAYOUT.replace(b"1390.25", b"1000.00").replace(b"1470.50", b"1.00")
    txns, w, _ = parse_any("export.csv", bad, p, "a", "i")
    assert learning.import_quality(bad, p, txns, w)["balance_ok"] is False


# --------------------------------------------------------------------------- persistence (real vault)

def test_learned_formats_persist_in_vault_and_are_used(ws, vault_path, monkeypatch):
    data = b"When,What,How Much\n01/02/2025,ACME WIDGETS SUPPLY,-12.50\n01/03/2025,REFUND THING,4.00\n"
    real_detect = store._detect

    def undetected(filename, blob):
        prof = real_detect(filename, blob)
        prof.mapping = None
        return prof
    monkeypatch.setattr(store, "_detect", undetected)
    with pytest.raises(store.NeedsMapping):
        ws.add_import("weird.csv", data, account_label="Misc")
    r = ws.add_import("weird.csv", data, account_label="Misc",
                      mapping={"date": "When", "description": "What", "amount": "How Much"})
    assert r.new == 2 and len(ws.learned_formats) == 1
    assert ws.learned_formats[0]["source"] == "confirmed"
    raw = json.dumps(ws.learned_formats)
    assert "ACME" not in raw and "12.50" not in raw
    ws.vault.lock()

    v2 = Vault.open(vault_path, PASS)
    w2 = store.Workspace(v2)
    assert len(w2.learned_formats) == 1
    later = b"When,What,How Much\n02/02/2025,OTHER SHOP,-3.00\n02/04/2025,ANOTHER,-9.00\n"
    r2 = w2.add_import("weird-feb.csv", later, account_label="Misc")   # detector still "can't" map it
    assert r2.new == 2
    assert r2.detected["profile"].startswith("learned:")
    assert r2.detected["recognition"]["source"] == "learned"
    assert w2.learned_formats[0]["uses"] == 2 and w2.learned_formats[0]["successes"] == 2
    v2.lock()


def test_store_imports_standard_formats(ws):
    f = FX / "formats" / "ofx1_sgml_checking.ofx"
    if not f.exists():
        pytest.skip("OFX fixture missing")
    r = ws.add_import("download.txt", f.read_bytes())         # content, not extension, decides
    assert r.count > 0 and r.detected["recognition"]["format"] == "OFX"
    assert ws.learned_formats == []
    with pytest.raises(store.ValidationError):
        ws.preview_import("download.txt", f.read_bytes())


# --------------------------------------------------------------------------- catalog

def test_catalog_match_and_generic_guard(tmp_path):
    cat = tmp_path / "known_formats.json"
    cat.write_text(json.dumps({"formats": [
        {"id": "northwind_csv", "institution": "Northwind Bank", "kind": "checking", "format": "CSV",
         "header": ["Value Day", "Booking Text", "Counterparty", "Debit/Credit", "Amount", "Saldo", "Ref"],
         "mapping": {"date": "Value Day", "description": "Counterparty", "amount": "Amount", "balance": "Saldo"},
         "sign_convention": "negative = money out"},
        {"id": "bare", "institution": "Some Bank", "format": "CSV", "header": ["Date", "Description", "Amount"],
         "mapping": {"date": "Date", "description": "Description", "amount": "Amount"}}]}))
    entries = learning.load_catalog(cat)
    hit = learning.match_catalog(learning.fingerprint(NEW_LAYOUT), "x.csv", entries)
    assert hit and hit[0]["institution"] == "Northwind Bank" and hit[0]["mapping"]["negative_is_out"] is True
    bare = b"Date,Description,Amount\n01/02/2025,LOCAL GROCER,-45.00\n01/03/2025,FUEL,-30.00\n"
    assert learning.match_catalog(learning.fingerprint(bare), "export.csv", entries) is None


def test_shipped_catalog_loads_if_present():
    entries = learning.load_catalog()
    assert isinstance(entries, list)
    for e in entries:
        assert "header_norm" in e and "mapping" in e


# --------------------------------------------------------------------------- format packs

def test_format_pack_contains_no_values():
    learned = _learn(NEW_LAYOUT)
    learned, _ = learning.record_confirmation(learned, "Statement 2025-0505.csv", _rows(8),
                                              ColumnMapping(date="Date", description="Payee", amount="Amount"),
                                              "Secret Bank 1234")
    pack = learning.export_format_pack(learned)
    blob = json.dumps(pack)
    assert not re.search(r"\d{4,}", blob)
    for word in ("NORTHWIND", "BLUE HERON", "CLIENT", "SHOP NUMBER", "Secret Bank", "Northwind Bank", "Statement"):
        assert word.lower() not in blob.lower()
    assert pack["kind"] == "ledgerstitch.format-pack" and pack["signature"] is None
    f = pack["formats"][0]
    assert set(f) >= {"header", "roles", "date_format", "sign_convention"}
    # round trip: a fresh workspace recognises the layout from the pack
    merged, added = learning.import_format_pack([], pack)
    assert added == 2
    p = learning.lookup("x.csv", NEW_LAYOUT, merged)
    assert p is not None and p.recognition["source"] == "pack"
    # importing twice adds nothing; garbage is rejected
    assert learning.import_format_pack(merged, pack)[1] == 0
    with pytest.raises(ValueError):
        learning.import_format_pack([], {"kind": "x"})


def test_format_pack_verifier_interface():
    class No:
        def verify(self, payload, signature):
            return False
    pack = learning.export_format_pack(_learn(NEW_LAYOUT))
    with pytest.raises(ValueError):
        learning.import_format_pack([], pack, verifier=No())


# --------------------------------------------------------------------------- LLM assist interface

def test_mapping_assistant_sees_only_shapes():
    fp = learning.fingerprint(NEW_LAYOUT)
    req = learning.assistant_request(fp)
    blob = json.dumps(req)
    assert "NORTHWIND" not in blob and "25.00" not in blob and "975" not in blob
    assert any(s.startswith("date MM/DD/YYYY") for s in req["shapes"])
    assert "money signed" in " ".join(req["shapes"])

    class Fake:
        def suggest_mapping(self, headers, shapes):
            return {"date": headers[0]}
    assert isinstance(Fake(), learning.MappingAssistant)


# --------------------------------------------------------------------------- dispatch / detection by content

@pytest.mark.parametrize("name,family", [
    ("ofx1_sgml_checking.ofx", "OFX"), ("ofx2_xml_creditcard.ofx", "OFX"), ("sample_card.qbo", "OFX"),
    ("sample_savings.qfx", "OFX"), ("qif_bank.qif", "QIF"), ("camt053_sample.xml", "CAMT"),
    ("mt940_sample.sta", "MT940"), ("bai2_sample.bai", "BAI2"), ("dev5_statement.xlsx", "XLSX"),
])
def test_detect_by_content_and_parse_any(name, family):
    f = FX / "formats" / name
    if not f.exists():
        pytest.skip(f"{name} not present")
    data = f.read_bytes()
    assert sniff_format("upload.bin", data) == family
    p = detect(name, data)
    txns, warnings, info = parse_any(name, data, p, "acct", "imp")
    assert txns and all(t.amount > 0 for t in txns)
    assert p.recognition and p.recognition.get("format")
    assert "format" in info and "reconciled" in info
