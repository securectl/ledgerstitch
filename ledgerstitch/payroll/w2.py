# SPDX-License-Identifier: AGPL-3.0-only
"""Year-end projection of a pay stub and the estimated W-2 it implies.

* ``detect_frequency``: weekly / biweekly / semi-monthly / monthly from the pay period;
* ``periods_remaining``: pay dates left in the calendar year after this stub's pay date (W-2
  wages follow the pay date, not the work period);
* ``estimate_w2``: projects every YTD amount to December 31 at this period's amount (or takes
  YTD as final), then fills boxes 1-6, 10, 12 (C, D, E, G, W, AA, BB, DD where derivable),
  13 (retirement plan) and 14 (informational).

Payroll arithmetic only: Social Security is capped at the wage base, Medicare includes the
0.9% the employer withholds above $200,000. No income tax is computed. Every result is
labelled ``ESTIMATE_LABEL``.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from .limits import limits_for
from .paystub import RETIREMENT, SEC125_KINDS, Item, PayStub

__all__ = ["ESTIMATE_LABEL", "W2Estimate", "detect_frequency", "periods_remaining", "estimate_w2",
           "FREQUENCIES"]

ESTIMATE_LABEL = "ESTIMATE — not issued by employer"
ZERO = Decimal("0")
CENT = Decimal("0.01")
FREQUENCIES = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}
BOX12_CODES = {"pretax_401k": "D", "pretax_403b": "E", "pretax_457b": "G", "roth_401k": "AA", "roth_403b": "BB"}


def q(v: Decimal) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class W2Estimate:
    owner: str
    employer: Optional[str]
    year: int
    source: str = ""
    pay_date: Optional[date] = None
    frequency: Optional[str] = None
    periods_remaining: int = 0
    final: bool = False
    label: str = ESTIMATE_LABEL
    boxes: dict[str, Decimal] = field(default_factory=dict)        # "1".."6", "10"
    box12: dict[str, Decimal] = field(default_factory=dict)        # code -> amount
    box13_retirement_plan: bool = False
    box14: list[str] = field(default_factory=list)
    # projected inputs the household signals use
    gross: Decimal = ZERO
    elective_deferrals: Decimal = ZERO     # pre-tax + Roth (402(g))
    hsa_total: Decimal = ZERO              # employee + employer
    health_fsa: Decimal = ZERO
    health_fsa_type: Optional[str] = None
    dcap_total: Decimal = ZERO
    other_taxes: dict[str, Decimal] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- frequency
def detect_frequency(start: Optional[date], end: Optional[date]) -> Optional[str]:
    """Pay frequency from the pay period dates (inclusive). None when it can't be told."""
    if not start or not end or end < start:
        return None
    days = (end - start).days + 1
    month_end = calendar.monthrange(end.year, end.month)[1]
    if start.day in (1, 16) and end.day in (15, month_end) and 13 <= days <= 16 and start.month == end.month:
        return "semimonthly"
    if 6 <= days <= 8:
        return "weekly"
    if 13 <= days <= 15:
        return "biweekly"
    if 28 <= days <= 31:
        return "monthly"
    return None


def periods_remaining(pay_date: date, frequency: str, year: Optional[int] = None) -> int:
    """Pay dates after ``pay_date`` up to December 31 of ``year`` (default: the pay date's year).

    Weekly / biweekly step 7 / 14 days. Semi-monthly assumes pay on the 15th and the last day
    of the month; monthly, once a month."""
    year = year or pay_date.year
    end = date(year, 12, 31)
    if pay_date > end:
        return 0
    if frequency in ("weekly", "biweekly"):
        step = 7 if frequency == "weekly" else 14
        return max(0, (end - pay_date).days // step)
    if frequency == "semimonthly":
        return (12 - pay_date.month) * 2 + (1 if pay_date.day <= 15 else 0)
    if frequency == "monthly":
        return 12 - pay_date.month
    raise ValueError(f"unknown pay frequency {frequency!r}")


# ----------------------------------------------------------------------------- projection
def _proj(cur: Decimal, ytd: Optional[Decimal], n: int) -> Decimal:
    base = ytd if ytd is not None else cur
    return q(base + cur * n)


def _proj_item(it: Item, n: int) -> Decimal:
    return _proj(it.current, it.ytd, n)


def _proj_er(it: Item, n: int) -> Decimal:
    if it.employer_ytd is None and it.employer_current == ZERO:
        return ZERO
    return _proj(it.employer_current, it.employer_ytd, n)


def estimate_w2(st: PayStub, final: bool = False, frequency: Optional[str] = None,
                year: Optional[int] = None) -> W2Estimate:
    """Project ``st`` to year end (``final=True``: take YTD as the year's total) and estimate the W-2."""
    year = year or st.year or date.today().year
    lim = limits_for(year)
    est = W2Estimate(owner=st.owner, employer=st.employer, year=year, source=st.source,
                     pay_date=st.pay_date or st.period_end, final=final)
    est.frequency = frequency or detect_frequency(st.period_start, st.period_end)
    n = 0
    if not final:
        if est.frequency and est.pay_date:
            n = periods_remaining(est.pay_date, est.frequency, year)
        else:
            est.final = True
            est.notes.append("Pay frequency or pay date unknown: YTD is treated as final (pass a frequency "
                             "to project).")
    est.periods_remaining = n

    gross = _proj(st.gross_current, st.gross_ytd, n)
    reimb = sum((_proj_item(e, n) for e in st.earnings if e.kind == "reimbursement"), ZERO)
    wages = gross - reimb
    est.gross = wages
    deds = [d for d in st.deductions if d.kind != "reimbursement"]
    fed_pre = sum((_proj_item(d, n) for d in deds if d.pretax), ZERO)
    fica_pre = sum((_proj_item(d, n) for d in deds if d.fica_exempt), ZERO)

    box1 = q(wages - fed_pre)
    box5 = q(wages - fica_pre)
    box3 = q(min(box5, lim.ss_wage_base))
    fit = _proj_item(st.federal_income_tax, n)
    ss_proj = _proj_item(st.social_security, n)
    box4 = min(ss_proj, lim.ss_max_tax)
    if ss_proj > lim.ss_max_tax:
        est.notes.append(f"Social Security withholding stops at the {year} wage base "
                         f"(${lim.ss_wage_base:,.0f}); box 4 is capped at ${lim.ss_max_tax:,.2f}.")
    med_proj = _proj_item(st.medicare, n)
    med_formula = q(box5 * lim.medicare_rate +
                    max(ZERO, box5 - lim.addl_medicare_withholding) * lim.addl_medicare_rate)
    box6 = med_proj
    if st.medicare.current > 0 and not est.final and med_formula > med_proj + 1:
        box6 = med_formula
        est.notes.append("Box 6 includes the 0.9% Additional Medicare Tax the employer withholds on wages "
                         "over $200,000.")
    est.boxes = {"1": box1, "2": fit, "3": box3, "4": box4, "5": box5, "6": box6}

    # box 10, 12, 13, 14
    for d in deds:
        amt, er = _proj_item(d, n), _proj_er(d, n)
        if d.kind in BOX12_CODES and (d.pretax or d.kind in ("roth_401k", "roth_403b")) and amt:
            code = BOX12_CODES[d.kind]
            est.box12[code] = est.box12.get(code, ZERO) + amt
            est.elective_deferrals += amt
        if d.kind == "hsa":
            est.hsa_total += amt + er
        if d.kind == "health_fsa":
            est.health_fsa += amt
            est.health_fsa_type = d.fsa_type or "unknown"
        if d.kind == "dcap":
            est.dcap_total += amt + er
        if d.kind in RETIREMENT and (amt or er):
            est.box13_retirement_plan = True
    if est.dcap_total:
        est.boxes["10"] = q(est.dcap_total)
        if est.dcap_total > lim.dcap:
            est.notes.append(f"Dependent care benefits over ${lim.dcap:,.0f} are taxable and are included in "
                             "boxes 1, 3 and 5 by the employer; this estimate does not add them back.")
    if est.hsa_total:
        est.box12["W"] = q(est.hsa_total)
    gtl = sum((_proj_item(e, n) for e in st.earnings if e.kind == "imputed_gtl"), ZERO)
    if gtl:
        est.box12["C"] = q(gtl)
    medical = [d for d in deds if d.kind == "sec125_medical"]
    if medical and any(d.employer_current or d.employer_ytd for d in medical):
        est.box12["DD"] = q(sum((_proj_item(d, n) + _proj_er(d, n) for d in medical), ZERO))
        est.notes.append("Box 12 DD is medical coverage (employee + employer share shown on the stub); "
                         "dental/vision count only when not separate plans.")
    elif medical:
        est.notes.append("Box 12 DD (cost of employer health coverage) is not derivable: the stub does not "
                         "show the employer share.")
    est.box12 = {k: q(v) for k, v in sorted(est.box12.items())}

    sec125 = [d for d in deds if d.kind in SEC125_KINDS and d.fica_exempt and d.kind not in ("hsa", "dcap")]
    if sec125:
        total = q(sum((_proj_item(d, n) for d in sec125), ZERO))
        names = ", ".join(sorted({d.label for d in sec125}))
        est.box14.append(f"Sec. 125 pre-tax benefits {total:,.2f} ({names}); employers often show this in "
                         "box 14 (optional, informational).")
    for t in st.other_taxes:
        est.other_taxes[t.label] = _proj_item(t, n)
    if any(d.pretax_source.startswith("inferred") for d in deds):
        est.notes.append("Some pre-tax flags were inferred from the FICA / federal taxable wage arithmetic.")
    if any(d.pretax_source.startswith("unknown") for d in deds):
        est.notes.append("Deductions the stub doesn't mark were treated as post-tax.")
    est.notes.append("Projection: YTD + this period's amount for each remaining pay date"
                     if not est.final else "YTD amounts taken as the year's totals")
    return est
