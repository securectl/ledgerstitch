# SPDX-License-Identifier: AGPL-3.0-only
"""Payroll rates and annual limits used by the pay stub projection and signals.

One row per tax year, each value with its source. These are the published dollar
limits, not tax computations. Re-check every row against the cited notice each
year before release (TAX_NOTES.md section 8).

=====================================  ===========  ===========  ==========================================
item                                   2025         2026         source
=====================================  ===========  ===========  ==========================================
Social Security wage base              176,100      184,500      SSA COLA fact sheets (Oct 2024 / Oct 2025)
Social Security rate (employee)        6.2%         6.2%         IRC §3101(a)
Medicare rate (employee)               1.45%        1.45%        IRC §3101(b)(1)
Additional Medicare rate               0.9%         0.9%         IRC §3101(b)(2); Form 8959
  threshold single / MFJ / MFS         200k/250k/125k            IRC §3101(b)(2); employer withholds over
                                                                 200k per employee, IRC §3102(f)
402(g) elective deferral limit         23,500       24,500       IRS Notice 2024-80 / Notice 2025-67
  catch-up age 50+                     7,500        8,000        IRS Notice 2024-80 / Notice 2025-67
  catch-up age 60-63                   11,250       11,250       IRS Notice 2024-80 / Notice 2025-67
HSA limit self-only / family           4,300/8,550  4,400/8,750  Rev. Proc. 2024-25 / Rev. Proc. 2025-19
  HSA catch-up age 55+                 1,000        1,000        IRC §223(b)(3)
Health FSA salary reduction limit      3,300        3,400        Rev. Proc. 2024-40 / Rev. Proc. 2025-32
Dependent care assistance (DCAP)       5,000        7,500        IRC §129(a)(2)(A); P.L. 119-21 (from 2026)
  married filing separately            2,500        3,750        IRC §129(a)(2)(A)
=====================================  ===========  ===========  ==========================================
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ["YearLimits", "LIMITS", "limits_for", "CITATIONS"]

D = Decimal


@dataclass(frozen=True)
class YearLimits:
    year: int
    ss_wage_base: Decimal
    elective_deferral: Decimal
    catch_up_50: Decimal
    catch_up_60_63: Decimal
    hsa_self: Decimal
    hsa_family: Decimal
    health_fsa: Decimal
    dcap: Decimal
    dcap_mfs: Decimal
    ss_rate: Decimal = D("0.062")
    medicare_rate: Decimal = D("0.0145")
    addl_medicare_rate: Decimal = D("0.009")
    addl_medicare_single: Decimal = D("200000")
    addl_medicare_mfj: Decimal = D("250000")
    addl_medicare_mfs: Decimal = D("125000")
    addl_medicare_withholding: Decimal = D("200000")
    hsa_catch_up: Decimal = D("1000")

    @property
    def ss_max_tax(self) -> Decimal:
        return (self.ss_wage_base * self.ss_rate).quantize(D("0.01"))

    def addl_medicare_threshold(self, filing_status: str) -> Decimal:
        return {"mfj": self.addl_medicare_mfj, "mfs": self.addl_medicare_mfs}.get(
            filing_status, self.addl_medicare_single)


LIMITS: dict[int, YearLimits] = {
    2025: YearLimits(2025, ss_wage_base=D("176100"), elective_deferral=D("23500"), catch_up_50=D("7500"),
                     catch_up_60_63=D("11250"), hsa_self=D("4300"), hsa_family=D("8550"),
                     health_fsa=D("3300"), dcap=D("5000"), dcap_mfs=D("2500")),
    2026: YearLimits(2026, ss_wage_base=D("184500"), elective_deferral=D("24500"), catch_up_50=D("8000"),
                     catch_up_60_63=D("11250"), hsa_self=D("4400"), hsa_family=D("8750"),
                     health_fsa=D("3400"), dcap=D("7500"), dcap_mfs=D("3750")),
}

CITATIONS: dict[str, dict[int, str]] = {
    "ss_wage_base": {2025: "SSA, 2025 Social Security Changes (COLA fact sheet)",
                     2026: "SSA, 2026 Social Security Changes (COLA fact sheet)"},
    "elective_deferral": {2025: "IRS Notice 2024-80; IRC §402(g)", 2026: "IRS Notice 2025-67; IRC §402(g)"},
    "hsa": {2025: "Rev. Proc. 2024-25; IRS Pub. 969", 2026: "Rev. Proc. 2025-19; IRS Pub. 969"},
    "health_fsa": {2025: "Rev. Proc. 2024-40; IRC §125(i)", 2026: "Rev. Proc. 2025-32; IRC §125(i)"},
    "dcap": {2025: "IRC §129(a)(2)(A), (b); IRS Pub. 503",
             2026: "IRC §129(a)(2)(A) as amended by P.L. 119-21; IRS Pub. 503"},
    "excess_ss": {2025: "IRS Topic 608; Form 1040 Schedule 3", 2026: "IRS Topic 608; Form 1040 Schedule 3"},
    "addl_medicare": {2025: "IRC §3101(b)(2), §3102(f); Form 8959", 2026: "IRC §3101(b)(2), §3102(f); Form 8959"},
    "hsa_fsa": {2025: "IRC §223(c)(1); IRS Pub. 969 (other health coverage)",
                2026: "IRC §223(c)(1); IRS Pub. 969 (other health coverage)"},
}


def limits_for(year: int) -> YearLimits:
    """The limits for ``year``; the nearest known year (with no inflation guess) otherwise."""
    if year in LIMITS:
        return LIMITS[year]
    known = sorted(LIMITS)
    return LIMITS[known[0] if year < known[0] else known[-1]]


def citation(key: str, year: int) -> str:
    row = CITATIONS[key]
    return row.get(year) or row[max(row)]
