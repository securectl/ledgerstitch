# SPDX-License-Identifier: AGPL-3.0-only
"""Report summary: the single source of truth for every total LedgerStitch shows.

Accounting rules (see docs/REPORTS.md):
  * Transactions with ``duplicate_of`` set are excluded from everything
    (they only appear on the Excel "Excluded" tab).
  * Expense lanes (BUSINESS / PERSONAL / REVIEW): net = sum(out) - sum(in).
    A matched refund inherits its purchase's category and lane, so it nets
    against that category automatically.
  * TRANSFER lane is never an expense; it is reported separately.
  * INCOME lane is reported separately as income: net = sum(in) - sum(out).
  * Schedule C uses the BUSINESS lane only. Meals (L24b) are shown gross and
    at a 50% deductible estimate; everything else at 100%.

All arithmetic is done in Decimal; the returned dict holds floats rounded to
2dp so it is JSON-serializable and convenient for the UI.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Mapping, Optional

from ..models import Account, Direction, Lane, Status, Transaction
from ..taxonomy import SCHEDULE_C_LINES, UNCATEGORIZED

DISCLAIMER = "Prepared for review by a tax professional; not tax advice."
MEALS_LINE = "L24b"
MEALS_NOTE = ("Business meals (Schedule C line 24b) are generally only 50% deductible. "
              "The deductible estimate applies 50% to meals and 100% to every other line.")
UNASSIGNED_LINE = "Unassigned"

EXPENSE_LANES = (Lane.BUSINESS, Lane.PERSONAL, Lane.REVIEW)
ZERO = Decimal("0")
CENT = Decimal("0.01")


# --------------------------------------------------------------------------- helpers
def q2(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def f2(d: Decimal) -> float:
    return float(q2(d))


def deductible_pct(line: Optional[str]) -> Decimal:
    return Decimal("0.5") if line == MEALS_LINE else Decimal("1")


def schedule_c_name(line: Optional[str]) -> str:
    if not line:
        return "Not assigned to a Schedule C line"
    return SCHEDULE_C_LINES.get(line, line)


def category_of(t: Transaction) -> str:
    return t.category or UNCATEGORIZED


def expense_value(t: Transaction) -> Decimal:
    """Contribution to an expense total: out positive, in (refund) negative."""
    return t.amount if t.direction == Direction.OUT else -t.amount


def income_value(t: Transaction) -> Decimal:
    """Contribution to income: in positive, out (e.g. chargeback/reversal) negative."""
    return t.amount if t.direction == Direction.IN else -t.amount


def in_year(t: Transaction, year: Optional[int]) -> bool:
    return year is None or t.date.year == year


def split_active(txns: Iterable[Transaction], year: Optional[int] = None
                 ) -> tuple[list[Transaction], list[Transaction]]:
    """Return (active, duplicates) for the period, both sorted by date."""
    active, dups = [], []
    for t in txns:
        if not in_year(t, year):
            continue
        (dups if t.duplicate_of is not None else active).append(t)
    key = lambda t: (t.date, t.account_id, t.seq, t.id)  # noqa: E731
    return sorted(active, key=key), sorted(dups, key=key)


def accounts_by_id(accounts) -> dict[str, Account]:
    """Accept ``dict[id, Account]``, ``list[Account]`` or None."""
    if not accounts:
        return {}
    if isinstance(accounts, Mapping):
        return dict(accounts)
    return {a.id: a for a in accounts}


def needs_review(t: Transaction) -> bool:
    return t.lane == Lane.REVIEW or t.status == Status.REVIEW


def is_uncategorized(t: Transaction) -> bool:
    return not t.category or t.category == UNCATEGORIZED


@dataclass
class ScheduleCRow:
    line: str
    name: str
    gross: Decimal
    pct: Decimal
    deductible: Decimal
    count: int


def schedule_c_rows(active: Iterable[Transaction]) -> list[ScheduleCRow]:
    """Business-lane totals grouped by Schedule C line, in form order."""
    gross: dict[str, Decimal] = defaultdict(lambda: ZERO)
    counts: dict[str, int] = defaultdict(int)
    for t in active:
        if t.lane != Lane.BUSINESS:
            continue
        line = t.schedule_c or UNASSIGNED_LINE
        gross[line] += expense_value(t)
        counts[line] += 1
    order = list(SCHEDULE_C_LINES)
    rows = []
    for line in sorted(gross, key=lambda l: (order.index(l) if l in order else len(order), l)):
        pct = deductible_pct(line)
        g = gross[line]
        rows.append(ScheduleCRow(line=line,
                                 name=schedule_c_name(None if line == UNASSIGNED_LINE else line),
                                 gross=q2(g), pct=pct, deductible=q2(g * pct), count=counts[line]))
    return rows


# --------------------------------------------------------------------------- summarize
def summarize(txns, year: int | None = None) -> dict:
    """Compute every report total. See module docstring for the rules.

    Returns::

        {"year", "totals": {"business","personal","review","expenses","excluded",
                            "transfers_out","transfers_in","income"},
         "by_category": [{"category","lane","schedule_c","total","count"}],
         "by_schedule_c": [{"line","name","gross","deductible_pct","deductible","count"}],
         "schedule_c_total": {"gross","deductible"},
         "by_account": [{"account_id","business","personal","review","transfers_out",
                         "transfers_in","income","count"}],
         "by_month": [{"month","business","personal","review","income"}],
         "counts": {"total","business","personal","review","transfer","income",
                    "duplicates","needs_review","uncategorized"},
         "review_count", "uncategorized_count", "disclaimer", "meals_note"}
    """
    active, dups = split_active(txns, year)

    lane_tot: dict[Lane, Decimal] = defaultdict(lambda: ZERO)
    lane_cnt: dict[Lane, int] = defaultdict(int)
    transfers_out = transfers_in = ZERO
    cat_tot: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    cat_cnt: dict[tuple[str, str], int] = defaultdict(int)
    cat_line: dict[tuple[str, str], Optional[str]] = {}
    acct: dict[str, dict] = {}
    month: dict[str, dict] = {}

    def acct_row(aid: str) -> dict:
        return acct.setdefault(aid, {k: ZERO for k in (
            "business", "personal", "review", "transfers_out", "transfers_in", "income")} | {"count": 0})

    def month_row(m: str) -> dict:
        return month.setdefault(m, {k: ZERO for k in ("business", "personal", "review", "income")})

    for t in active:
        lane_cnt[t.lane] += 1
        a = acct_row(t.account_id)
        a["count"] += 1
        m = month_row(f"{t.date.year:04d}-{t.date.month:02d}")
        if t.lane in EXPENSE_LANES:
            v = expense_value(t)
            lane_tot[t.lane] += v
            a[t.lane.value] += v
            m[t.lane.value] += v
            key = (t.lane.value, category_of(t))
            cat_tot[key] += v
            cat_cnt[key] += 1
            if t.lane == Lane.BUSINESS and not cat_line.get(key):
                cat_line[key] = t.schedule_c
        elif t.lane == Lane.TRANSFER:
            if t.direction == Direction.OUT:
                transfers_out += t.amount
                a["transfers_out"] += t.amount
            else:
                transfers_in += t.amount
                a["transfers_in"] += t.amount
        elif t.lane == Lane.INCOME:
            v = income_value(t)
            lane_tot[Lane.INCOME] += v
            a["income"] += v
            m["income"] += v

    lane_rank = {"business": 0, "review": 1, "personal": 2}
    by_category = [
        {"category": cat, "lane": lane, "schedule_c": cat_line.get((lane, cat)),
         "total": f2(cat_tot[(lane, cat)]), "count": cat_cnt[(lane, cat)]}
        for (lane, cat) in sorted(cat_tot, key=lambda k: (lane_rank[k[0]], -cat_tot[k], k[1]))
    ]

    sc_rows = schedule_c_rows(active)
    by_schedule_c = [
        {"line": r.line, "name": r.name, "gross": f2(r.gross), "deductible_pct": float(r.pct),
         "deductible": f2(r.deductible), "count": r.count}
        for r in sc_rows
    ]

    review_count = sum(1 for t in active if needs_review(t))
    uncategorized_count = sum(1 for t in active if is_uncategorized(t))
    expenses = lane_tot[Lane.BUSINESS] + lane_tot[Lane.PERSONAL] + lane_tot[Lane.REVIEW]

    return {
        "year": year,
        "totals": {
            "business": f2(lane_tot[Lane.BUSINESS]),
            "personal": f2(lane_tot[Lane.PERSONAL]),
            "review": f2(lane_tot[Lane.REVIEW]),
            "expenses": f2(expenses),
            # money moved between the user's own accounts (out legs); never an expense
            "excluded": f2(transfers_out),
            "transfers_out": f2(transfers_out),
            "transfers_in": f2(transfers_in),
            "income": f2(lane_tot[Lane.INCOME]),
        },
        "by_category": by_category,
        "by_schedule_c": by_schedule_c,
        "schedule_c_total": {"gross": f2(sum((r.gross for r in sc_rows), ZERO)),
                             "deductible": f2(sum((r.deductible for r in sc_rows), ZERO))},
        "by_account": [{"account_id": aid, **{k: (v if k == "count" else f2(v)) for k, v in row.items()}}
                       for aid, row in sorted(acct.items())],
        "by_month": [{"month": mo, **{k: f2(v) for k, v in row.items()}}
                     for mo, row in sorted(month.items())],
        "counts": {
            "total": len(active),
            "business": lane_cnt[Lane.BUSINESS],
            "personal": lane_cnt[Lane.PERSONAL],
            "review": lane_cnt[Lane.REVIEW],
            "transfer": lane_cnt[Lane.TRANSFER],
            "income": lane_cnt[Lane.INCOME],
            "duplicates": len(dups),
            "needs_review": review_count,
            "uncategorized": uncategorized_count,
        },
        "review_count": review_count,
        "uncategorized_count": uncategorized_count,
        "disclaimer": DISCLAIMER,
        "meals_note": MEALS_NOTE,
    }
