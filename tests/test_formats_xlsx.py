# SPDX-License-Identifier: AGPL-3.0-only
"""XLSX statements: sheet → CSV conversion, header detection, CSV pipeline reuse, safety guards."""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from ledgerstitch.ingest.detect import ColumnMapping, FormatProfile, detect, sniff_format  # noqa: E402
from ledgerstitch.ingest.dispatch import parse_any, preview_any  # noqa: E402
from ledgerstitch.ingest.xlsx_parser import (detect_xlsx, is_legacy_xls, parse_xlsx, sniff_xlsx,  # noqa: E402
                                             xlsx_to_csv_bytes)
from ledgerstitch.models import AccountKind, Direction  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "formats"


def _book(rows, *, blank_first=False, title=None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    if blank_first:
        ws.title = "Cover"
        ws = wb.create_sheet("Data")
    if title:
        ws.append([title])
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def fixture_xlsx() -> bytes:
    p = FX / "dev5_statement.xlsx"
    if not p.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("mk", FX / "make_dev5_fixtures.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.make_xlsx(p)
    return p.read_bytes()


def test_sniff_by_content_not_extension(fixture_xlsx):
    assert sniff_xlsx(fixture_xlsx)
    assert sniff_format("statement.csv", fixture_xlsx) == "XLSX"
    assert not sniff_xlsx(b"Date,Amount\n")
    assert not sniff_xlsx(b"PK\x03\x04garbage")


def test_conversion_skips_blank_sheet_keeps_preamble_iso_dates_clean_floats(fixture_xlsx):
    csv_bytes, info = xlsx_to_csv_bytes(fixture_xlsx)
    text = csv_bytes.decode()
    assert info["format"] == "XLSX" and info["sheet"] == "Transactions"
    lines = text.splitlines()
    assert lines[0].startswith("Example Bank")              # preamble kept
    assert "2025-03-01" in text and "2025-03-05" in text and "14:30" not in text
    assert "-12.3" in text and "0000000" not in text           # 0.1+0.2-12.6 without float noise


def test_detect_and_parse_fixture(fixture_xlsx):
    p = detect("activity.xlsx", fixture_xlsx)
    assert p.format == "XLSX" and p.mapping is not None
    assert p.mapping.date == "Date" and p.mapping.amount == "Amount"
    txns, warnings, info = parse_any("activity.xlsx", fixture_xlsx, p, "acct", "imp")
    assert len(txns) == 4 and info["format"] == "XLSX"
    assert txns[0].date == date(2025, 3, 1) and txns[0].direction == Direction.IN
    assert txns[1].amount == Decimal("4.5") and txns[1].direction == Direction.OUT


def test_manual_mapping_roundtrip_and_preview():
    data = _book([("Booked", "Who", "Out", "In"),
                  (datetime(2025, 1, 2), "PAPER CO", 10.0, None),
                  (datetime(2025, 1, 3), "CLIENT", None, 99.5)], blank_first=True, title="My export")
    prev = preview_any(data)
    assert prev["columns"] == ["Booked", "Who", "Out", "In"]
    prof = FormatProfile(id="manual", institution="X", kind=AccountKind.CHECKING, confidence=1.0,
                         account_hint=None, mapping=ColumnMapping(date="Booked", description="Who",
                                                                  debit="Out", credit="In"))
    txns, _, _ = parse_xlsx(data, prof, "a", "i")
    assert [(t.direction, t.amount) for t in txns] == [(Direction.OUT, Decimal("10")), (Direction.IN, Decimal("99.5"))]
    assert detect_xlsx("x.xlsx", data).mapping is not None


def test_legacy_xls_rejected():
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64
    assert is_legacy_xls(ole)
    assert sniff_format("old.xls", ole) == "XLS"
    with pytest.raises(ValueError):
        xlsx_to_csv_bytes(ole)
    with pytest.raises(ValueError):
        parse_any("old.xls", ole, None, "a", "i")


def test_zip_bomb_guard_many_entries():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        for i in range(2100):
            z.writestr(f"xl/junk{i}.xml", "")
    with pytest.raises(ValueError):
        xlsx_to_csv_bytes(buf.getvalue())
