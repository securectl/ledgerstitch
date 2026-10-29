# SPDX-License-Identifier: AGPL-3.0-only
"""Generate the SYNTHETIC pay stub fixtures used by tests/test_paystub.py.

All employers, people, ids and amounts are fabricated ("Acme Corp", "Jane Doe", the sample
SSN 900-45-6789). PDFs are written with ledgerstitch.bench.pdfgen (no extra dependency,
byte-for-byte deterministic), so the test suite can check the committed files are current.

    .venv/bin/python tests/fixtures/make_paystubs.py

Layouts:
* paystub_rippling.pdf  Rippling-style EARNINGS STATEMENT, semi-monthly, CURRENT/YTD and COMPANY columns
* paystub_adp.pdf       ADP-style, biweekly, space-separated amounts ("7 916 67"), "*" pre-tax marks
* paystub_adp.txt       the same ADP stub as plain text
* paystub_generic.txt   generic "label  current  YTD", monthly, second employer of the same person
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent))

from ledgerstitch.bench.pdfgen import PDF  # noqa: E402


def _row(label: str, *cols: str, widths=(14, 14, 14, 14), first: int = 30) -> str:
    s = label.ljust(first)
    for c, w in zip(cols, widths):
        s += c.rjust(w)
    return s.rstrip()


RIPPLING = "\n".join([
    "EARNINGS STATEMENT",
    "Acme Corp                         Pay period: 06/01/2025 - 06/15/2025",
    "100 Example Way, Springfield      Pay date: 06/15/2025",
    "Employee: Jane Doe                SSN: 900-45-6789",
    "Federal filing status: Single     Employee ID: 00987654",
    "",
    _row("EARNINGS", "RATE", "HOURS", "CURRENT", "YTD"),
    _row("Salary", "", "", "5,000.00", "55,000.00"),
    _row("Group Term Life", "", "", "4.50", "49.50"),
    _row("GROSS PAY", "", "", "5,004.50", "55,049.50"),
    "",
    _row("EMPLOYEE TAXES", "", "", "CURRENT", "YTD"),
    _row("Federal Income Tax", "", "", "450.00", "4,950.00"),
    _row("Social Security", "", "", "280.83", "3,089.13"),
    _row("Medicare", "", "", "65.68", "722.48"),
    _row("IL State Income Tax", "", "", "180.00", "1,980.00"),
    "",
    _row("DEDUCTIONS", "EMPLOYEE CURRENT", "EMPLOYEE YTD", "COMPANY CURRENT", "COMPANY YTD", widths=(18, 16, 18, 16)),
    _row("401(k) Pre-Tax", "500.00", "5,500.00", "250.00", "2,750.00", widths=(18, 16, 18, 16)),
    _row("Medical", "200.00", "2,200.00", "600.00", "6,600.00", widths=(18, 16, 18, 16)),
    _row("Dental", "20.00", "220.00", "10.00", "110.00", widths=(18, 16, 18, 16)),
    _row("Vision", "5.00", "55.00", "", "", widths=(18, 16, 18, 16)),
    _row("HSA", "150.00", "1,650.00", "50.00", "550.00", widths=(18, 16, 18, 16)),
    _row("Health FSA", "100.00", "1,100.00", "", "", widths=(18, 16, 18, 16)),
    _row("Life Insurance", "8.00", "88.00", "", "", widths=(18, 16, 18, 16)),
    "",
    _row("NET PAY", "", "", "3,040.49", "33,445.39"),
    "",
    "DIRECT DEPOSIT",
    _row("Checking  Account: 000123456789", "", "", "3,040.49"),
])

ADP = "\n".join([
    "INITECH INC                                      Earnings Statement",
    "Company Code  ZX/ABC 01234567                    Period Starting:  03/01/2025",
    "Loc/Dept      Number 012345                      Period Ending:    03/14/2025",
    "                                                 Pay Date:         03/21/2025",
    "Filing Status: Married filing jointly",
    "Social Security Number: 987-65-4321",
    "",
    _row("Earnings", "rate", "hours", "this period", "year to date", first=22),
    _row("Regular", "98.9584", "80.00", "7 916 67", "47 500 02", first=22),
    _row("Bonus", "", "", "", "5 000 00", first=22),
    _row("            Gross Pay", "", "", "$7 916 67", "52 500 02", first=22),
    "",
    _row("Statutory Deductions", "", "", "this period", "year to date", first=22),
    _row("Federal Income Tax", "", "", "-1 012 44", "7 174 64", first=22),
    _row("Social Security Tax", "", "", "-467 60", "3 115 60", first=22),
    _row("Medicare Tax", "", "", "-109 36", "728 66", first=22),
    _row("CA State Income Tax", "", "", "-380 11", "2 280 66", first=22),
    _row("CA SDI Tax", "", "", "-95 00", "630 00", first=22),
    "",
    "Other",
    _row("401K Contrib", "", "", "-560 98*", "3 365 88", first=22),
    _row("Dental", "", "", "-22 50*", "135 00", first=22),
    _row("Medical", "", "", "-150 00*", "900 00", first=22),
    _row("Dep Care FSA", "", "", "-192 30*", "1 153 80", first=22),
    _row("Wellness Plan", "", "", "-10 00", "60 00", first=22),
    _row("Life Ins", "", "", "-12 00", "72 00", first=22),
    "",
    _row("            Net Pay", "", "", "$4 904 38", "", first=22),
    "",
    "* Excluded from federal taxable wages",
    "  Your federal taxable wages this period are $6,980.89",
    "",
    "Net Pay Distribution",
    "  Checking1  Account Number 9876543210            $4,904.38",
])

GENERIC = "\n".join([
    "PAY STUB",
    "Employer: Globex LLC",
    "Employee: Jane Doe",
    "Pay Period: 2025-06-01 to 2025-06-30",
    "Pay Date: 2025-06-30",
    "",
    _row("Description", "Current", "YTD"),
    _row("Gross Pay", "9,000.00", "54,000.00"),
    _row("Federal Income Tax", "1,100.00", "6,600.00"),
    _row("Social Security", "558.00", "3,348.00"),
    _row("Medicare", "130.50", "783.00"),
    _row("Roth 401(k)", "1,000.00", "6,000.00"),
    _row("Net Pay", "6,211.50", "37,269.00"),
])


def text_pdf(text: str) -> bytes:
    pdf = PDF(size=8)
    y = 40
    for line in text.splitlines():
        if line:
            pdf.text(36, y, line)
        y += 11
    return pdf.to_bytes()


FIXTURES = {
    "paystub_rippling.pdf": lambda: text_pdf(RIPPLING),
    "paystub_adp.pdf": lambda: text_pdf(ADP),
    "paystub_adp.txt": lambda: (ADP + "\n").encode(),
    "paystub_generic.txt": lambda: (GENERIC + "\n").encode(),
}


if __name__ == "__main__":
    for name, make in FIXTURES.items():
        (HERE / name).write_bytes(make())
    print("wrote synthetic pay stubs to", HERE)
