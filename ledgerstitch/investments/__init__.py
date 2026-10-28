# SPDX-License-Identifier: AGPL-3.0-only
"""Investments: 1099 import, a holdings snapshot and free investment signals.

    analyze_investments([("fidelity-1099.pdf", data), ("positions.csv", data2)]) -> InvestmentReport

Each file is recognized by its content: a 1099 statement (PDF or text), a 1099-B transaction CSV
or a positions CSV. Runs in memory: nothing is stored, nothing leaves the computer. TINs and
account numbers are masked before parsing. This adds up what payers reported and labels where it
goes on the return; LedgerStitch does not compute income tax (docs/TAX_NOTES.md section 9).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from .forms1099 import (BOXES, CATEGORIES, DESTINATIONS, BCategory, Lot, Statement1099, is_lots_header,
                        parse_1099, parse_1099_text, parse_lots_csv)
from .holdings import (Allocation, Holding, HoldingsSummary, is_positions_header, parse_positions_csv,
                       summarize_holdings)
from .signals import Signal, investment_signals

__all__ = ["analyze_investments", "InvestmentReport", "IncomeTotal", "Statement1099", "BCategory", "Lot",
           "Holding", "HoldingsSummary", "Allocation", "Signal", "parse_1099", "parse_1099_text",
           "parse_lots_csv", "parse_positions_csv", "summarize_holdings", "investment_signals", "LABEL",
           "DISCLAIMER", "FILING_STATUSES", "PLUS_FEATURES"]

ZERO = Decimal("0")
FILING_STATUSES = ("single", "mfj", "mfs", "hoh")
LABEL = "SUMMARY — totals from your 1099s and statements; not a tax return"
DISCLAIMER = (LABEL + ". The payer's 1099 is the document of record, and corrected 1099s are common "
              "through March. Not tax advice; no income tax is computed.")
#: features of LedgerStitch Plus that this Community module deliberately leaves out
PLUS_FEATURES = ("FIFO / specific-lot gains from trade history", "wash sales across accounts and a spouse's",
                 "tax-loss harvesting plan", "target allocation, drift and rebalancing", "lot selection")


@dataclass
class IncomeTotal:
    form: str
    box: str
    label: str
    amount: Decimal
    destination: str
    payers: list[str] = field(default_factory=list)


@dataclass
class InvestmentReport:
    year: Optional[int]
    filing_status: Optional[str] = None
    statements: list[Statement1099] = field(default_factory=list)
    income: list[IncomeTotal] = field(default_factory=list)
    capital_gains: list[BCategory] = field(default_factory=list)
    short_term_net: Decimal = ZERO
    long_term_net: Decimal = ZERO
    holdings: list[Holding] = field(default_factory=list)
    holdings_summary: Optional[HoldingsSummary] = None
    signals: list[Signal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    label: str = LABEL
    disclaimer: str = DISCLAIMER
    plus_features: tuple[str, ...] = PLUS_FEATURES


def _csv_kind(data: bytes) -> Optional[str]:
    text = data[:65536].decode("utf-8-sig", errors="replace")
    for row in list(csv.reader(io.StringIO(text)))[:40]:
        if is_lots_header(row):
            return "lots"
        if is_positions_header(row):
            return "positions"
    return None


def _same(a: list[BCategory], b: list[BCategory]) -> bool:
    ka = {g.category: g.proceeds for g in a}
    kb = {g.category: g.proceeds for g in b}
    return ka.keys() == kb.keys() and all(abs(ka[k] - kb[k]) <= 1 for k in ka)


def analyze_investments(files: list[tuple[str, bytes]], year: Optional[int] = None,
                        filing_status: Optional[str] = None) -> InvestmentReport:
    """Parse every ``(filename, data)``, add up the 1099s, summarize the holdings and compute the
    free signals. ``year`` keeps only statements for that tax year (default: the latest found)."""
    if filing_status is not None and filing_status not in FILING_STATUSES:
        raise ValueError(f"filing status must be one of {', '.join(FILING_STATUSES)}")
    statements: list[Statement1099] = []
    holdings: list[Holding] = []
    as_of: Optional[date] = None
    warnings: list[str] = []
    for name, data in files:
        is_pdf = data.lstrip()[:5] == b"%PDF-"
        kind = None if is_pdf else _csv_kind(data)
        if kind == "positions":
            hs, d, w = parse_positions_csv(name, data)
            holdings += hs
            as_of = max(filter(None, (as_of, d)), default=None)
            warnings += w
        elif kind == "lots":
            statements.append(parse_lots_csv(name, data))
        else:
            statements.append(parse_1099(name, data))
    years = [s.tax_year for s in statements if s.tax_year]
    y = year or (max(years) if years else None)
    kept = []
    for s in statements:
        warnings += [f"{s.source}: {w}" for w in s.warnings]
        if y and s.tax_year and s.tax_year != y:
            warnings.append(f"{s.source}: tax year {s.tax_year}, not {y}; skipped")
            continue
        kept.append(s)

    # income boxes, summed across payers
    income: dict[tuple[str, str], IncomeTotal] = {}
    for s in kept:
        for form, boxes in s.forms.items():
            for box, amt in boxes.items():
                t = income.setdefault((form, box), IncomeTotal(form, box, BOXES[form][box][0], ZERO,
                                                               DESTINATIONS.get((form, box), "")))
                t.amount += amt
                payer = s.payer or s.source
                if payer not in t.payers:
                    t.payers.append(payer)
    order = list(BOXES)

    # capital gains: a lot CSV that matches a statement's 1099-B summary is the same account
    summaries = [s for s in kept if s.kind == "1099" and s.b_summary]
    counted: list[Statement1099] = list(summaries)
    for s in kept:
        if s.kind != "1099-B lots":
            continue
        dup = next((t for t in summaries if _same(t.b_summary, s.b_summary)), None)
        if dup:
            warnings.append(f"{s.source}: lots match the 1099-B summary of {dup.payer or dup.source}; counted once")
        else:
            if summaries:
                warnings.append(f"{s.source}: lots don't match any 1099-B summary; added to the totals (check "
                                "that the CSV and a PDF aren't the same account)")
            counted.append(s)
    gains: dict[str, BCategory] = {}
    for s in counted:
        for g in s.b_summary:
            t = gains.setdefault(g.category, BCategory(category=g.category, label=CATEGORIES[g.category][3]))
            t.proceeds += g.proceeds
            if g.cost_basis is not None:
                t.cost_basis = (t.cost_basis or ZERO) + g.cost_basis
            t.market_discount += g.market_discount
            t.wash_sale_disallowed += g.wash_sale_disallowed
            if g.gain is not None:
                t.gain = (t.gain or ZERO) + g.gain
    cap = sorted(gains.values(), key=lambda g: "ABCDEFX".index(g.category))
    summary = summarize_holdings(holdings, as_of) if holdings else None
    report = InvestmentReport(
        year=y, filing_status=filing_status, statements=kept,
        income=sorted(income.values(), key=lambda t: (order.index(t.form), list(BOXES[t.form]).index(t.box))),
        capital_gains=cap,
        short_term_net=sum((g.gain or ZERO for g in cap if g.term == "short"), ZERO),
        long_term_net=sum((g.gain or ZERO for g in cap if g.term == "long"), ZERO),
        holdings=holdings, holdings_summary=summary, warnings=warnings)
    report.signals = investment_signals(kept, cap, holdings, summary, y, filing_status)
    if not files:
        report.warnings.append("no files given")
    return report
