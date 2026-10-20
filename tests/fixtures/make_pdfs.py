# SPDX-License-Identifier: AGPL-3.0-only
"""Generate the SYNTHETIC PDF statement fixtures used by tests/test_pdf_parser.py.

All names, merchants and amounts are fabricated. Requires reportlab
(test-only): `uv pip install --python .venv/bin/python reportlab`.

    .venv/bin/python tests/fixtures/make_pdfs.py
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent


def _canvas(path: Path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 8)
    return c


def columns_statement(path: Path, totals=("1,650.00", "1,145.50")) -> None:
    """Wells-Fargo-like layout: Credits / Debits / Ending balance columns,
    statement crossing Dec→Jan, multi-line descriptions, two pages."""
    c = _canvas(path)
    H = 792
    c.drawString(40, H - 40, "Sample Bank Business Checking")
    c.drawString(400, H - 40, "January 14, 2025 Page 1 of 2")
    c.drawString(40, H - 70, "Statement period activity summary")
    c.drawString(40, H - 82, "Beginning balance on 12/15 $2,000.00")
    c.drawString(40, H - 94, "Deposits/Credits 1,650.00")
    c.drawString(40, H - 106, "Withdrawals/Debits - 1,145.50")
    c.drawString(40, H - 118, "Ending balance on 1/14 $2,504.50")
    c.drawString(40, H - 140, "Account number: 0000009876")
    c.drawString(40, H - 170, "Transaction history")

    def header(y):
        c.drawString(400, y + 10, "Deposits/")
        c.drawString(460, y + 10, "Withdrawals/")
        c.drawString(525, y + 10, "Ending daily")
        c.drawString(63, y, "Date")
        c.drawString(150, y, "Description")
        c.drawRightString(435, y, "Credits")
        c.drawRightString(503, y, "Debits")
        c.drawRightString(566, y, "balance")

    def row(y, d, desc, credit=None, debit=None, bal=None, cont=()):
        c.drawString(63, y, d)
        c.drawString(150, y, desc)
        if credit:
            c.drawRightString(435, y, credit)
        if debit:
            c.drawRightString(503, y, debit)
        if bal:
            c.drawRightString(566, y, bal)
        for i, line in enumerate(cont):
            c.drawString(150, y - 10 * (i + 1), line)
        return y - 10 * (len(cont) + 1) - 4

    y = H - 200
    header(y)
    y -= 16
    y = row(y, "12/16", "Purchase authorized on 12/14 Copper Kettle Supply Austin TX", debit="45.50",
            cont=["S000000000000001 Card 0000"])
    y = row(y, "12/20", "Mobile Deposit : Ref Number :000000000001", credit="1,200.00", bal="3,154.50")
    y = row(y, "12/31", "WT Fed#00000 Sample Trading Co /Bnf=Sample Vendor Srf#", debit="600.00",
            cont=["Ow00000000000001 Trn#000000000001 Rfb#", "Ow00000000000001"], bal="2,554.50")
    c.drawString(40, 60, "Page 1 of 2")
    c.showPage()
    c.setFont("Helvetica", 8)
    c.drawString(400, H - 40, "January 14, 2025 Page 2 of 2")
    c.drawString(40, H - 70, "Transaction History (continued)")
    y = H - 100
    header(y)
    y -= 16
    y = row(y, "1/2", "Online Transfer From Sample Savings", credit="450.00", bal="3,004.50")
    y = row(y, "1/2", "Online Transfer From Sample Savings", credit="0.00")
    y = row(y, "1/9", "Wire Trans Svc Charge - Sequence: 000000000001 Srf#", debit="500.00", bal="2,504.50")
    c.drawString(63, y - 4, "Totals")
    c.drawRightString(435, y - 4, "$" + totals[0])
    c.drawRightString(503, y - 4, "$" + totals[1])
    y -= 40
    c.drawString(63, y, "Items returned unpaid")
    c.drawString(63, y - 12, "Date Description Amount")
    c.drawString(63, y - 24, "1/5 Sample returned item 12.00")
    c.save()


def sections_statement(path: Path) -> None:
    """Chase-like layout: one AMOUNT column, direction by section headers."""
    c = _canvas(path)
    H = 792
    lines = [
        "December 15, 2024 through January 14, 2025",
        "Account Number: 000000000004321",
        "CHECKING SUMMARY",
        "Beginning Balance $1,000.00",
        "Deposits and Additions 2 3,100.00",
        "Electronic Withdrawals 2 -1,250.00",
        "Ending Balance $2,850.00",
        "*start*deposits and additions",
        "DEPOSITS AND ADDITIONS",
        "DATE DESCRIPTION AMOUNT",
        ("12/20", "Orig CO Name:Sample Client LLC Orig ID:0000000001 Desc Date:122024 CO Entry", "$3,000.00"),
        "    Descr:Invoice Sec:CCD Trace#:000000000000001",
        ("01/03", "Zelle Payment From Sample Person Ref00001", "100.00"),
        "Total Deposits and Additions $3,100.00",
        "*end*deposits and additions",
        "ELECTRONIC WITHDRAWALS",
        "DATE DESCRIPTION AMOUNT",
        ("12/28", "12/28 Payment To Sample Card Ending IN 1111", "1,000.00"),
        ("01/10", "Orig CO Name:Sample Utility Orig ID:0000000002 Desc Date:011025", "250.00"),
        "Total Electronic Withdrawals $1,250.00",
        "DAILY ENDING BALANCE",
        "DATE AMOUNT",
        ("12/20", "", "$4,000.00"),
        ("01/10", "", "2,850.00"),
    ]
    y = H - 40
    for ln in lines:
        if isinstance(ln, tuple):
            d, desc, amt = ln
            c.drawString(40, y, d)
            if desc:
                c.drawString(80, y, desc)
            c.drawRightString(570, y, amt)
        elif ln.startswith("    "):
            c.drawString(80, y, ln.strip())
        else:
            c.drawString(40, y, ln)
        y -= 12
    c.save()


def balance_statement(path: Path) -> None:
    """Unsigned amounts with a running balance column only."""
    c = _canvas(path)
    H = 792
    c.drawString(40, H - 40, "Statement period 03/01/2025 to 03/31/2025")
    c.drawString(40, H - 52, "Beginning balance $500.00")
    y = H - 80
    c.drawString(40, y, "Date")
    c.drawString(90, y, "Description")
    c.drawRightString(480, y, "Amount")
    c.drawRightString(570, y, "Balance")
    rows = [("03/02", "Lighthouse Grocer", "20.00", "480.00"),
            ("03/05", "Client Delta Payment", "300.00", "780.00"),
            ("03/09", "Kestrel Fuel", "30.00", "750.00")]
    for d, desc, amt, bal in rows:
        y -= 12
        c.drawString(40, y, d)
        c.drawString(90, y, desc)
        c.drawRightString(480, y, amt)
        c.drawRightString(570, y, bal)
    c.drawString(40, y - 24, "Ending balance $750.00")
    c.save()


if __name__ == "__main__":
    columns_statement(HERE / "stmt_columns.pdf")
    columns_statement(HERE / "stmt_columns_mismatch.pdf", totals=("1,700.00", "1,145.50"))
    sections_statement(HERE / "stmt_sections.pdf")
    balance_statement(HERE / "stmt_balance.pdf")
    print("wrote synthetic PDFs to", HERE)
