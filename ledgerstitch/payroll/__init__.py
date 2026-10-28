# SPDX-License-Identifier: AGPL-3.0-only
"""Pay stubs: import, year-end projection, estimated W-2 and household payroll signals.

    analyze_paystubs([("stub.pdf", data, "you"), ("spouse.pdf", data2, "spouse")]) -> PayrollReport

Runs entirely in memory: nothing is stored, nothing leaves the computer. SSNs and account
numbers are masked before parsing, so no output carries them. This is payroll arithmetic and
published limits; LedgerStitch does not compute income tax (docs/TAX_NOTES.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .limits import LIMITS, limits_for
from .paystub import PayStub, parse_paystub, parse_paystub_text
from .signals import Signal, household_signals
from .w2 import ESTIMATE_LABEL, FREQUENCIES, W2Estimate, estimate_w2

__all__ = ["analyze_paystubs", "PayrollReport", "PayStub", "W2Estimate", "Signal", "parse_paystub",
           "parse_paystub_text", "estimate_w2", "household_signals", "ESTIMATE_LABEL", "OWNERS",
           "FILING_STATUSES", "FREQUENCIES", "DISCLAIMER", "LIMITS", "limits_for"]

OWNERS = ("you", "spouse")
FILING_STATUSES = ("single", "mfj", "mfs")
DISCLAIMER = (ESTIMATE_LABEL + ". Projected from pay stubs for planning; the employer's W-2 is the "
              "document of record. Not tax advice; no income tax is computed.")


@dataclass
class PayrollReport:
    year: int
    filing_status: str
    stubs: list[PayStub] = field(default_factory=list)
    w2: list[W2Estimate] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    label: str = ESTIMATE_LABEL
    disclaimer: str = DISCLAIMER


def _employer_key(st: PayStub) -> str:
    return " ".join((st.employer or st.source or "?").upper().replace(",", " ").replace(".", " ").split())


def analyze_paystubs(files: list[tuple[str, bytes, str]], final: bool = False, frequency: Optional[str] = None,
                     filing_status: Optional[str] = None, spouse_no_earned_income: bool = False,
                     year: Optional[int] = None) -> PayrollReport:
    """Parse every ``(filename, data, owner)``, keep the latest stub per person and employer,
    project each to year end and compute the household signals."""
    if frequency is not None and frequency not in FREQUENCIES:
        raise ValueError(f"frequency must be one of {', '.join(FREQUENCIES)}")
    if filing_status is not None and filing_status not in FILING_STATUSES:
        raise ValueError(f"filing status must be one of {', '.join(FILING_STATUSES)}")
    stubs = []
    for name, data, owner in files:
        if owner not in OWNERS:
            raise ValueError(f"owner must be one of {', '.join(OWNERS)}")
        stubs.append(parse_paystub(name, data, owner))
    years = [s.year for s in stubs if s.year]
    y = year or (max(years) if years else date.today().year)
    warnings = [f"{s.source}: {w}" for s in stubs for w in s.warnings]

    latest: dict[tuple[str, str], PayStub] = {}
    for s in stubs:
        if s.year is not None and s.year != y:
            warnings.append(f"{s.source}: pay date is in {s.year}, not {y}; skipped")
            continue
        k = (s.owner, _employer_key(s))
        cur = latest.get(k)
        if cur is None or (s.pay_date or s.period_end or date.min) >= (cur.pay_date or cur.period_end or date.min):
            if cur is not None:
                warnings.append(f"{cur.source}: an older stub from the same employer; the latest one is used")
            latest[k] = s
        else:
            warnings.append(f"{s.source}: an older stub from the same employer; the latest one is used")
    w2s = [estimate_w2(s, final=final, frequency=frequency, year=y) for s in latest.values()]
    people = {w.owner for w in w2s}
    fs = filing_status or ("mfj" if len(people) > 1 or any(
        s.filing_status and "joint" in s.filing_status for s in latest.values()) else "single")
    if y not in LIMITS:
        warnings.append(f"no published limits for {y} in this version; using {limits_for(y).year}'s")
    sig = household_signals(w2s, y, fs, spouse_no_earned_income)
    return PayrollReport(year=y, filing_status=fs, stubs=stubs, w2=w2s, signals=sig, warnings=warnings)
