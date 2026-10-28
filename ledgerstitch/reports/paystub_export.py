# SPDX-License-Identifier: AGPL-3.0-only
"""Estimated W-2 export (CSV or Excel) from a ``payroll.PayrollReport``.

Every file starts with the label "ESTIMATE — not issued by employer". The CSV is one flat table
(section, owner, employer, item, amount, note); the workbook has the same rows on three tabs
(Estimated W-2, Signals, Pay stubs). Free text goes through ``safe_text`` / ``_text`` (no formula
injection); SSNs and account numbers were masked when the stubs were parsed.
"""
from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .csv_export import safe_text

__all__ = ["paystub_rows", "export_paystub_csv", "export_paystub_xlsx", "PAYSTUB_COLUMNS"]

PAYSTUB_COLUMNS = ["section", "owner", "employer", "item", "amount", "note"]
BOX_NAMES = {
    "1": "Box 1 Wages, tips, other compensation", "2": "Box 2 Federal income tax withheld",
    "3": "Box 3 Social Security wages", "4": "Box 4 Social Security tax withheld",
    "5": "Box 5 Medicare wages and tips", "6": "Box 6 Medicare tax withheld",
    "10": "Box 10 Dependent care benefits",
}


def _amt(v) -> str:
    return "" if v is None else f"{Decimal(v):.2f}"


def paystub_rows(report) -> list[list[str]]:
    """The export as rows of strings (amounts as plain 2-decimal numbers)."""
    rows: list[list[str]] = []
    for w in report.w2:
        emp = w.employer or ""
        basis = "final (YTD as total)" if w.final else f"projected, {w.periods_remaining} pay dates left " \
                                                      f"({w.frequency})"
        for box in sorted(w.boxes, key=int):
            rows.append(["Estimated W-2", w.owner, emp, BOX_NAMES.get(box, f"Box {box}"), _amt(w.boxes[box]), basis])
        for code, v in w.box12.items():
            rows.append(["Estimated W-2", w.owner, emp, f"Box 12 code {code}", _amt(v), basis])
        rows.append(["Estimated W-2", w.owner, emp, "Box 13 Retirement plan",
                     "", "checked" if w.box13_retirement_plan else "not checked"])
        for line in w.box14:
            rows.append(["Estimated W-2", w.owner, emp, "Box 14 Other", "", line])
        for name, v in w.other_taxes.items():
            rows.append(["Estimated W-2", w.owner, emp, f"Other tax withheld: {name}", _amt(v), basis])
        for n in w.notes:
            rows.append(["Estimated W-2", w.owner, emp, "Note", "", n])
    for s in report.signals:
        rows.append(["Signal", s.owner or "household", "", s.title, _amt(s.amount),
                     f"{s.explanation} [{s.citation}]"])
    for st in report.stubs:
        emp = st.employer or ""
        when = (st.pay_date or st.period_end)
        rows.append(["Pay stub", st.owner, emp, f"{st.source} ({st.layout})", _amt(st.gross_current),
                     f"gross this period; pay date {when.isoformat() if when else 'unknown'}"])
        for d in st.deductions:
            rows.append(["Pay stub", st.owner, emp, d.label, _amt(d.current),
                         f"{d.kind}; pre-tax={d.pretax} ({d.pretax_source or 'n/a'}); YTD {_amt(d.ytd)}"
                         + (f"; employer {_amt(d.employer_current)}" if d.employer_current else "")])
        for c in st.checks:
            rows.append(["Pay stub check", st.owner, emp, c["check"], "", c.get("note", "")])
    for w in report.warnings:
        rows.append(["Warning", "", "", "", "", w])
    return rows


def export_paystub_csv(report, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([report.label])
        w.writerow([safe_text(report.disclaimer)])
        w.writerow(PAYSTUB_COLUMNS)
        for r in paystub_rows(report):
            w.writerow([safe_text(c) if i != 4 else c for i, c in enumerate(r)])
    return path


def export_paystub_xlsx(report, path: Path) -> Path:
    from openpyxl import Workbook

    from .excel import NOTE_FONT, TITLE_FONT, _header, _money, _text
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    rows = paystub_rows(report)
    tabs = [("Estimated W-2", ("Estimated W-2",)), ("Signals", ("Signal",)),
            ("Pay stubs", ("Pay stub", "Pay stub check", "Warning"))]
    for i, (title, sections) in enumerate(tabs):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        _text(ws, 1, 1, report.label, font=TITLE_FONT)
        _text(ws, 2, 1, report.disclaimer, font=NOTE_FONT)
        _header(ws, 4, ["Owner", "Employer", "Item", "Amount", "Note"], [10, 24, 40, 15, 90])
        r = 5
        for row in rows:
            if row[0] not in sections:
                continue
            for col, v in enumerate((row[1], row[2], row[3]), 1):
                _text(ws, r, col, v)
            if row[4]:
                _money(ws, r, 4, Decimal(row[4]))
            _text(ws, r, 5, row[5])
            r += 1
        ws.freeze_panes = "A5"
    wb.save(path)
    return path
