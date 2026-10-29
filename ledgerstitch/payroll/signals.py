# SPDX-License-Identifier: AGPL-3.0-only
"""Household "money found" signals across estimated W-2s (you and a spouse, any number of employers).

Each signal is a dollar amount with a short explanation and a citation:

* ``excess_social_security``: more than the year's maximum Social Security tax withheld across
  two or more employers; the excess is a credit on the return (Schedule 3);
* ``deferral_room`` / ``excess_deferral``: 402(g) elective deferral room left (or exceeded) for a
  person across all employers;
* ``hsa_fsa_conflict``: HSA contributions while the household also has a health FSA that is not
  known to be limited-purpose;
* ``hsa_over_limit`` / ``health_fsa_over_limit`` / ``dcap_over_limit``: projected contributions
  above the year's limit;
* ``dcap_no_earned_income``: dependent care benefits while a spouse has no earned income;
* ``additional_medicare_not_withheld``: combined Medicare wages above the filing-status threshold
  while employers withhold only above $200,000 per person (informational).

These are payroll facts and limits, not an income tax computation.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .limits import citation, limits_for
from .w2 import W2Estimate, q

__all__ = ["Signal", "household_signals"]

ZERO = Decimal("0")


@dataclass
class Signal:
    id: str
    severity: str                  # "money" (found money), "warning", "info"
    owner: Optional[str]           # None = household
    amount: Optional[Decimal]
    title: str
    explanation: str
    citation: str


def _fmt(v: Decimal) -> str:
    return f"${v:,.2f}"


def household_signals(w2s: list[W2Estimate], year: int, filing_status: Optional[str] = None,
                      spouse_no_earned_income: bool = False) -> list[Signal]:
    """Signals for one household from its estimated W-2s (one per person and employer).

    ``filing_status``: "single", "mfj" or "mfs" (default: "mfj" when there are W-2s for two
    people, otherwise "single"). ``spouse_no_earned_income``: the user says one spouse has no
    earned income (it decides the dependent care warning)."""
    lim = limits_for(year)
    people: dict[str, list[W2Estimate]] = defaultdict(list)
    for w in w2s:
        people[w.owner].append(w)
    fs = filing_status or ("mfj" if len(people) > 1 else "single")
    out: list[Signal] = []

    for owner, ws in sorted(people.items()):
        # excess Social Security across employers
        ss = sum((w.boxes.get("4", ZERO) for w in ws), ZERO)
        if len(ws) > 1 and ss > lim.ss_max_tax:
            excess = q(ss - lim.ss_max_tax)
            out.append(Signal(
                "excess_social_security", "money", owner, excess,
                f"{_fmt(excess)} of excess Social Security tax",
                f"{len(ws)} employers are projected to withhold {_fmt(ss)} of Social Security tax, more than "
                f"the {year} maximum of {_fmt(lim.ss_max_tax)} (6.2% of the ${lim.ss_wage_base:,.0f} wage "
                "base). Each employer withheld correctly on its own; the excess is claimed as a credit on "
                "the return. If one employer withheld too much, ask that employer for the refund instead.",
                citation("excess_ss", year)))
        # 402(g)
        deferrals = q(sum((w.elective_deferrals for w in ws), ZERO))
        room = q(lim.elective_deferral - deferrals)
        if deferrals and room < 0:
            out.append(Signal(
                "excess_deferral", "warning", owner, -room,
                f"{_fmt(-room)} over the 402(g) deferral limit",
                f"Projected pre-tax + Roth deferrals across employers are {_fmt(deferrals)}, above the {year} "
                f"limit of {_fmt(lim.elective_deferral)} (before any age 50+ catch-up of "
                f"{_fmt(lim.catch_up_50)}, or {_fmt(lim.catch_up_60_63)} at ages 60-63). Ask a plan for a "
                "corrective distribution of the excess by April 15, or lower a contribution rate now.",
                citation("elective_deferral", year)))
        elif any(w.box13_retirement_plan for w in ws) or deferrals:
            out.append(Signal(
                "deferral_room", "money" if room > 0 else "info", owner, room,
                f"{_fmt(room)} of 401(k)/403(b) deferral room left",
                f"Projected deferrals are {_fmt(deferrals)} of the {year} limit of {_fmt(lim.elective_deferral)} "
                f"(one limit per person across all employers; age 50+ adds {_fmt(lim.catch_up_50)}, ages "
                f"60-63 add {_fmt(lim.catch_up_60_63)}). Raising the contribution rate for the remaining pay "
                "dates uses the room.",
                citation("elective_deferral", year)))
        # per-person HSA / FSA limits
        hsa = q(sum((w.hsa_total for w in ws), ZERO))
        if hsa > lim.hsa_self:
            over_family = hsa > lim.hsa_family
            out.append(Signal(
                "hsa_over_limit", "warning", owner, q(hsa - (lim.hsa_family if over_family else lim.hsa_self)),
                "HSA contributions above the limit" if over_family else "HSA above the self-only limit",
                f"Projected HSA contributions (employee + employer) are {_fmt(hsa)}. The {year} limits are "
                f"{_fmt(lim.hsa_self)} self-only and {_fmt(lim.hsa_family)} family coverage (+"
                f"{_fmt(lim.hsa_catch_up)} at 55+). "
                + ("This is above the family limit." if over_family else
                   "That's fine only with family HDHP coverage."),
                citation("hsa", year)))
        for w in ws:
            if w.health_fsa > lim.health_fsa:
                out.append(Signal(
                    "health_fsa_over_limit", "warning", owner, q(w.health_fsa - lim.health_fsa),
                    "Health FSA above the salary reduction limit",
                    f"Projected health FSA contributions at {w.employer or 'an employer'} are "
                    f"{_fmt(w.health_fsa)}, above the {year} limit of {_fmt(lim.health_fsa)} per plan.",
                    citation("health_fsa", year)))

    # household: HSA + health FSA
    hsa_owners = [o for o, ws in people.items() if any(w.hsa_total for w in ws)]
    fsa = [(o, w) for o, ws in people.items() for w in ws if w.health_fsa and w.health_fsa_type != "limited"]
    if hsa_owners and fsa:
        who = ", ".join(sorted({o for o, _ in fsa}))
        out.append(Signal(
            "hsa_fsa_conflict", "warning", None, None,
            "HSA with a general-purpose health FSA",
            f"HSA contributions ({', '.join(sorted(hsa_owners))}) and a health FSA ({who}) that the stub does "
            "not show as limited-purpose. A general-purpose health FSA (yours or a spouse's that can pay "
            "your expenses) makes you ineligible to contribute to an HSA for those months. Check that the "
            "FSA is limited-purpose (dental/vision) or post-deductible.",
            citation("hsa_fsa", year)))

    # household: dependent care
    dcap = q(sum((w.dcap_total for w in w2s), ZERO))
    dcap_limit = lim.dcap_mfs if fs == "mfs" else lim.dcap
    if dcap and spouse_no_earned_income:
        out.append(Signal(
            "dcap_no_earned_income", "warning", None, dcap,
            f"{_fmt(dcap)} of dependent care benefits may become taxable",
            "The dependent care exclusion is limited to the earned income of the lower-earning spouse. With "
            "a spouse who has no earned income (and is not a full-time student or unable to care for "
            "themselves), the benefits are taxable. Consider stopping DCAP contributions.",
            citation("dcap", year)))
    if dcap > dcap_limit:
        out.append(Signal(
            "dcap_over_limit", "warning", None, q(dcap - dcap_limit),
            "Dependent care benefits above the limit",
            f"Projected dependent care benefits for the household are {_fmt(dcap)}, above the {year} "
            f"limit of {_fmt(dcap_limit)}" + (" for married filing separately" if fs == "mfs" else "")
            + ". The excess is taxable.",
            citation("dcap", year)))

    # household: Additional Medicare
    med_wages = q(sum((w.boxes.get("5", ZERO) for w in w2s), ZERO))
    threshold = lim.addl_medicare_threshold(fs)
    if med_wages > threshold:
        owed = q((med_wages - threshold) * lim.addl_medicare_rate)
        withheld = ZERO
        for owner, ws in people.items():
            for w in ws:
                withheld += max(ZERO, w.boxes.get("5", ZERO) - lim.addl_medicare_withholding) * lim.addl_medicare_rate
        gap = q(owed - withheld)
        if gap > 0:
            label = {"mfj": "married filing jointly", "mfs": "married filing separately"}.get(fs, "single")
            out.append(Signal(
                "additional_medicare_not_withheld", "info", None, gap,
                f"About {_fmt(gap)} of Additional Medicare Tax not withheld",
                f"Combined Medicare wages are projected at {_fmt(med_wages)}, above the {_fmt(threshold)} "
                f"threshold ({label}). The 0.9% Additional Medicare Tax on the excess is about {_fmt(owed)}; "
                f"an employer withholds it only on the wages over $200,000 it pays one person (about "
                f"{_fmt(q(withheld))} here). The difference is settled on Form 8959; extra withholding (Form "
                "W-4 line 4(c)) or estimated payments can cover it.",
                citation("addl_medicare", year)))
    return out
