# SPDX-License-Identifier: AGPL-3.0-only
"""Regenerate Developer-5 synthetic fixtures (all data is made up).

    .venv/bin/python tests/fixtures/formats/make_dev5_fixtures.py
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).parent


def make_xlsx(path: Path = HERE / "dev5_statement.xlsx") -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    blank = wb.active
    blank.title = "Cover"                       # empty sheet before the data
    ws = wb.create_sheet("Transactions")
    ws.append(["Example Bank — Account Activity"])
    ws.append(["Generated", "sample data only"])
    ws.append(["Date", "Description", "Amount", "Balance"])
    rows = [
        (datetime(2025, 3, 1), "OPENING DEPOSIT EXAMPLE", 1000, 1000),
        (date(2025, 3, 3), "SAMPLE COFFEE CO", -4.5, 995.5),
        (datetime(2025, 3, 5, 14, 30), "EXAMPLE OFFICE SUPPLY", 0.1 + 0.2 - 12.6, 983.2),
        (date(2025, 3, 9), "CLIENT ALPHA INVOICE", 250.75, 1233.95),
    ]
    for r in rows:
        ws.append(list(r))
    ws.append([])
    ws.append(["Total", None, 1234.65])
    wb.save(path)
    return path


if __name__ == "__main__":
    print(make_xlsx())
