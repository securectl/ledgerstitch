# SPDX-License-Identifier: AGPL-3.0-only
"""Investments export (CSV or Excel) from an ``investments.InvestmentReport``.

Every file starts with the report label and disclaimer. The CSV is one flat table (section,
source, item, amount, destination / note); the workbook has the same rows on tabs (1099 income,
Capital gains, Holdings, Signals, Statements). Free text goes through ``safe_text`` / ``_text``
(no formula injection); TINs and account numbers were masked when the files were parsed.
"""
from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .csv_export import safe_text

__all__ = ["investment_rows", "export_investments_csv", "export_investments_xlsx", "INVESTMENT_COLUMNS"]

INVESTMENT_COLUMNS = ["section", "source", "item", "amount", "note"]


def _amt(v) -> str:
    return "" if v is None else f"{Decimal(v):.2f}"


def investment_rows(report) -> list[list[str]]:
    """The export as rows of strings (amounts as plain 2-decimal numbers)."""
    rows: list[list[str]] = []
    for t in report.income:
        rows.append(["1099 income", ", ".join(t.payers), f"{t.form} box {t.box} {t.label}", _amt(t.amount),
                     t.destination])
    for g in report.capital_gains:
        basis = "basis not reported" if g.cost_basis is None else f"cost {_amt(g.cost_basis)}"
        rows.append(["Capital gains", "", f"Form 8949 box {g.category} ({g.term}-term) net gain/loss",
                     _amt(g.gain), f"proceeds {_amt(g.proceeds)}; {basis}; wash sale {_amt(g.wash_sale_disallowed)}"
                                   f"; market discount {_amt(g.market_discount)}; {g.destination}"])
    if report.capital_gains:
        rows.append(["Capital gains", "", "Net short-term", _amt(report.short_term_net), "Schedule D line 7"])
        rows.append(["Capital gains", "", "Net long-term (before box 2a distributions)", _amt(report.long_term_net),
                     "Schedule D line 15 also includes 1099-DIV box 2a"])
    for h in report.holdings:
        rows.append(["Holding", h.account or "", f"{h.symbol} {h.description}".strip(), _amt(h.market_value),
                     f"{h.asset_class}; qty {h.quantity if h.quantity is not None else '-'}; cost "
                     f"{_amt(h.cost_basis) or 'unknown'}; unrealized {_amt(h.unrealized_gain) or 'unknown'}"])
    hs = report.holdings_summary
    if hs:
        rows.append(["Portfolio", "", "Market value", _amt(hs.market_value),
                     f"{hs.positions} positions" + (f" as of {hs.as_of.isoformat()}" if hs.as_of else "")])
        rows.append(["Portfolio", "", "Unrealized gain/loss", _amt(hs.unrealized_gain),
                     f"gains {_amt(hs.unrealized_gains)}, losses {_amt(hs.unrealized_losses)} "
                     f"({hs.loss_positions} positions); basis unknown for {hs.basis_unknown}"])
        for a in hs.by_class:
            rows.append(["Portfolio", "", f"Asset class: {a.name}", _amt(a.value), f"{a.pct}%"])
        for a in hs.by_account:
            rows.append(["Portfolio", "", f"Account: {a.name}", _amt(a.value), f"{a.pct}%"])
        for a in hs.top:
            rows.append(["Portfolio", "", f"Top holding: {a.name}", _amt(a.value),
                         f"{a.pct}%" + (" (concentrated)" if a in hs.concentrated else "")])
    for s in report.signals:
        rows.append(["Signal", "household", s.title, _amt(s.amount), f"{s.explanation} [{s.citation}]"])
    for st in report.statements:
        rows.append(["Statement", st.source, f"{st.payer or '(payer not found)'} {st.kind} {st.tax_year or ''}".strip(),
                     "", f"account {st.account or '-'}; forms {', '.join(st.forms) or '-'}"
                         f"{'; 1099-B summary' if st.b_summary else ''}{f'; {len(st.lots)} lots' if st.lots else ''}"])
    for w in report.warnings:
        rows.append(["Warning", "", "", "", w])
    return rows


def export_investments_csv(report, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([report.label])
        w.writerow([safe_text(report.disclaimer)])
        w.writerow(INVESTMENT_COLUMNS)
        for r in investment_rows(report):
            w.writerow([safe_text(c) if i != 3 else c for i, c in enumerate(r)])
    return path


def export_investments_xlsx(report, path: Path) -> Path:
    from openpyxl import Workbook

    from .excel import NOTE_FONT, TITLE_FONT, _header, _money, _text
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    rows = investment_rows(report)
    tabs = [("1099 income", ("1099 income",)), ("Capital gains", ("Capital gains",)),
            ("Holdings", ("Holding", "Portfolio")), ("Signals", ("Signal",)), ("Statements", ("Statement", "Warning"))]
    for i, (title, sections) in enumerate(tabs):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        _text(ws, 1, 1, report.label, font=TITLE_FONT)
        _text(ws, 2, 1, report.disclaimer, font=NOTE_FONT)
        _header(ws, 4, ["Source", "Item", "Amount", "Note"], [28, 48, 15, 90])
        r = 5
        for row in rows:
            if row[0] not in sections:
                continue
            _text(ws, r, 1, row[1])
            _text(ws, r, 2, row[2])
            if row[3]:
                _money(ws, r, 3, Decimal(row[3]))
            _text(ws, r, 4, row[4])
            r += 1
        ws.freeze_panes = "A5"
    wb.save(path)
    return path
