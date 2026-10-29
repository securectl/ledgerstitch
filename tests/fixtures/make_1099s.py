# SPDX-License-Identifier: AGPL-3.0-only
"""Generate the SYNTHETIC 1099 and positions fixtures used by tests/test_investments.py.

All payers, people, ids, securities and amounts are fabricated ("Example Brokerage",
"Sample Securities", "Specimen Markets", the sample TIN 900-45-6789, ticker symbols that
don't trade). PDFs are written with ledgerstitch.bench.pdfgen, byte-for-byte deterministic,
so the test suite can check the committed files are current.

    .venv/bin/python tests/fixtures/make_1099s.py

Files:
* 1099_example_brokerage.pdf  Fidelity-style composite: "1a Total Ordinary Dividends", 1099-B
                              summary rows "Short-term ... (Box A)" with five columns
* 1099_sample_securities.pdf  Schwab-style: "Box 1a - ...", rows "Short-Term Covered", four columns
* 1099_specimen_markets.txt   Robinhood-style text: "1a- ...", rows "A (basis reported to the IRS)"
* 1099r_sample_trust.txt      1099-R, distribution code 1
* 1099nec_example_client.txt  1099-NEC
* 1099b_lots_example.csv      Example Brokerage's 1099-B lots (they add up to its PDF summary)
* positions_example.csv       Fidelity-style positions export
* positions_sample.csv        Schwab-style positions export (preamble, $ amounts, "--")
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent))

from ledgerstitch.bench.pdfgen import PDF  # noqa: E402


def _row(label: str, *cols: str, widths=(16, 16, 18, 22, 16), first: int = 58) -> str:
    s = label.ljust(first)
    for c, w in zip(cols, widths):
        s += c.rjust(w)
    return s.rstrip()


def _box(label: str, amount: str, first: int = 62) -> str:
    return (label.ljust(first) + amount.rjust(14)).rstrip()


EXAMPLE = "\n".join([
    "Example Brokerage LLC                              2025 TAX REPORTING STATEMENT",
    "100 Sample Street, Springfield                     Account No. 900123456",
    "Payer's TIN: 98-7654321                            Recipient's TIN: 900-45-6789",
    "Recipient: Jane Doe",
    "",
    "Form 1099-DIV   Dividends and Distributions        Copy B for Recipient",
    _box("1a Total Ordinary Dividends", "11,874.31"),
    _box("1b Qualified Dividends", "9,410.22"),
    _box("2a Total Capital Gain Distr.", "1,204.18"),
    _box("2b Unrecap. Sec. 1250 Gain", "0.00"),
    _box("3 Nondividend Distributions", "0.00"),
    _box("4 Federal Income Tax Withheld", "0.00"),
    _box("5 Section 199A Dividends", "800.00"),
    _box("7 Foreign Tax Paid", "245.30"),
    "8 Foreign Country or U.S. Possession:   Various",
    _box("12 Exempt-Interest Dividends", "310.00"),
    _box("13 Specified Private Activity Bond Interest Dividends", "22.00"),
    "",
    "Form 1099-INT   Interest Income",
    _box("1 Interest Income", "412.10"),
    _box("3 Interest on U.S. Savings Bonds and Treas. Obligations", "150.00"),
    _box("4 Federal Income Tax Withheld", "0.00"),
    "",
    "Form 1099-B   Proceeds From Broker and Barter Exchange Transactions   Summary",
    _row("", "Proceeds", "Cost Basis", "Market Discount", "Wash Sale Loss Dis.", "Net Gain/Loss"),
    _row("Short-term transactions for covered tax lots (Box A)", "8,050.00", "7,400.00", "0.00", "200.00", "850.00"),
    _row("Short-term transactions for noncovered tax lots (Box B)", "3,000.00", "", "0.00", "0.00", ""),
    _row("Long-term transactions for covered tax lots (Box D)", "14,000.00", "10,600.00", "0.00", "0.00",
         "3,400.00"),
    _row("Total", "25,050.00", "18,000.00", "0.00", "200.00", "4,250.00"),
])

SAMPLE = "\n".join([
    "Sample Securities Inc.",
    "Tax Year 2025 Form 1099 Composite",
    "Account Number: 900-234567       Recipient TIN: XXX-XX-6789",
    "",
    "2025 Form 1099-DIV",
    _box("Box 1a - Total ordinary dividends", "683.15"),
    _box("Box 1b - Qualified dividends", "600.00"),
    _box("Box 2a - Total capital gain distributions", "120.00"),
    _box("Box 3 - Nondividend distributions", "50.00"),
    _box("Box 7 - Foreign tax paid", "12.50"),
    "",
    "2025 Form 1099-INT",
    _box("Box 1 - Interest income", "88.20"),
    "",
    "2025 Form 1099-MISC",
    _box("Box 3 - Other income", "25.00"),
    "",
    "2025 Form 1099-B Summary",
    _row("", "Total Proceeds", "Cost Basis", "Wash Sale Loss", "Realized Gain/(Loss)", widths=(16, 16, 16, 22)),
    _row("Short-Term Covered", "20,000.00", "26,000.00", "0.00", "(6,000.00)", widths=(16, 16, 16, 22)),
    _row("Long-Term Covered", "5,000.00", "4,000.00", "0.00", "1,000.00", widths=(16, 16, 16, 22)),
    _row("Long-Term Noncovered", "2,500.00", "2,000.00", "0.00", "500.00", widths=(16, 16, 16, 22)),
])

SPECIMEN = "\n".join([
    "Specimen Markets LLC",
    "2025 1099 Consolidated Tax Statement",
    "Account 900345678",
    "",
    "1099-DIV  Dividends and Distributions",
    "1a- Total ordinary dividends (includes lines 1b, 5, 2e)          210.00",
    "1b- Qualified dividends                                          150.00",
    "5- Section 199A dividends                                         40.00",
    "4- Federal income tax withheld                                     0.00",
    "",
    "Summary of proceeds, gains & losses, adjustments and income tax withheld",
    "1099-B",
    "Term           Form 8949 type         Proceeds   Cost basis   Market discount   Wash sale loss disallowed"
    "   Net gain or loss(-)",
    "Short          A (basis reported to the IRS)   9,500.00    10,000.00          0.00                    300.00"
    "               -200.00",
    "Total Short-term                               9,500.00    10,000.00          0.00                    300.00"
    "               -200.00",
])

R_TRUST = "\n".join([
    "Sample Retirement Trust",
    "Form 1099-R  Distributions From Pensions, Annuities, Retirement Plans, IRAs   Tax Year 2025",
    "Recipient's TIN: 900-45-6789     Account Number 900456789",
    _box("1 Gross distribution", "5,000.00"),
    _box("2a Taxable amount", "5,000.00"),
    _box("4 Federal income tax withheld", "1,000.00"),
    "7 Distribution code(s):   1",
])

NEC = "\n".join([
    "Example Client Inc",
    "Form 1099-NEC   Nonemployee Compensation   2025",
    "Payer's TIN 98-7654321   Recipient's TIN 900-45-6789",
    _box("1 Nonemployee compensation", "2,500.00"),
    _box("4 Federal income tax withheld", "0.00"),
])

LOTS = "\n".join([
    "Example Brokerage LLC 1099-B transaction detail,,,,,,,,,",
    "Account No. 900123456 - Tax Year 2025,,,,,,,,,",
    "Description,Symbol,Quantity,Date Acquired,Date Sold,Proceeds,Cost Basis,Wash Sale Loss Disallowed,"
    "Gain/Loss,Term,Covered",
    "EXAMPLE TECH CORP,EXTC,50,02/03/2025,06/10/2025,\"6,250.00\",\"5,000.00\",0.00,\"1,250.00\",Short,Covered",
    "SAMPLE MOTORS INC,SMPM,20,03/01/2025,03/20/2025,\"1,800.00\",\"2,400.00\",200.00,-400.00,Short,Covered",
    "LEGACY HOLDINGS CO,LGCY,100,Various,04/15/2025,\"3,000.00\",,0.00,,Short,Noncovered",
    "BROAD MARKET INDEX ETF,BMIX,30,05/02/2022,08/01/2025,\"12,000.00\",\"9,000.00\",0.00,\"3,000.00\",Long,Covered",
    "ENERGY PIPELINE PARTNERS LP COMMON UNITS,EPPX,40,01/10/2023,09/05/2025,\"2,000.00\",\"1,600.00\",0.00,400.00,"
    "Long,Covered",
    "Total,,,,,\"25,050.00\",\"18,000.00\",200.00,\"4,250.00\",,",
])

POSITIONS_EXAMPLE = "\n".join([
    "Account Number,Account Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value,"
    "Today's Gain/Loss Dollar,Today's Gain/Loss Percent,Total Gain/Loss Dollar,Total Gain/Loss Percent,"
    "Percent Of Account,Cost Basis Total,Average Cost Basis,Type",
    "Z90012345,Individual,SPAXX**,HELD IN MONEY MARKET,,,,$5000.00,,,,,10.00%,,,Cash",
    "Z90012345,Individual,EXTC,EXAMPLE TECH CORP,150,$120.00,+$1.00,$18000.00,+$150.00,+0.84%,+$6000.00,"
    "+50.00%,36.00%,$12000.00,$80.00,Cash",
    "Z90012345,Individual,BMIX,BROAD MARKET INDEX ETF,40,$300.00,+$2.00,$12000.00,+$80.00,+0.67%,+$2000.00,"
    "+20.00%,24.00%,$10000.00,$250.00,Cash",
    "Z90012345,Individual,EPPX,ENERGY PIPELINE PARTNERS LP COMMON UNITS,100,$45.00,-$0.50,$4500.00,-$50.00,"
    "-1.10%,-$500.00,-10.00%,9.00%,$5000.00,$50.00,Cash",
    "Z90012345,Individual,SBIO,SAMPLE BIOTECH INC,200,$20.00,-$1.00,$4000.00,-$200.00,-4.76%,-$3000.00,"
    "-42.86%,8.00%,$7000.00,$35.00,Cash",
    "Z90012345,Individual,VXFDX,SAMPLE TOTAL MARKET INDEX FUND ADMIRAL SHARES,50,$130.00,,$6500.00,,,+$1500.00,"
    "+30.00%,13.00%,$5000.00,$100.00,Cash",
    "Z90012345,Individual,Pending Activity,,,,,$-0.00,,,,,,,,",
    "",
    "\"The data and information in this spreadsheet is provided to you solely for your use.\"",
    "\"Date downloaded 10/24/2025 4:05 PM ET\"",
])

POSITIONS_SAMPLE = "\n".join([
    "\"Positions for account Individual ...898 as of 10:15 AM ET, 10/24/2025\"",
    "",
    "\"Symbol\",\"Description\",\"Quantity\",\"Price\",\"Price Change %\",\"Price Change $\",\"Market Value\","
    "\"Day Change %\",\"Day Change $\",\"Cost Basis\",\"Gain/Loss %\",\"Gain/Loss $\",\"Ratings\","
    "\"Reinvest Dividends?\",\"Capital Gains?\",\"% Of Account\",\"Security Type\"",
    "\"SMPM\",\"SAMPLE MOTORS INC\",\"100\",\"$15.00\",\"-2%\",\"-$0.30\",\"$1,500.00\",\"-2%\",\"-$30.00\","
    "\"$2,500.00\",\"-40%\",\"-$1,000.00\",\"--\",\"No\",\"--\",\"30%\",\"Equity\"",
    "\"SMPM 01/16/2026 25.00 C\",\"CALL SAMPLE MOTORS INC $25 EXP 01/16/26\",\"2\",\"$0.50\",\"--\",\"--\","
    "\"$100.00\",\"--\",\"--\",\"$400.00\",\"-75%\",\"-$300.00\",\"--\",\"--\",\"--\",\"2%\",\"Option\"",
    "\"STBX\",\"SAMPLE TREASURY BOND ETF\",\"20\",\"$100.00\",\"0%\",\"$0.00\",\"$2,000.00\",\"0%\",\"$0.00\","
    "\"$2,000.00\",\"0%\",\"$0.00\",\"--\",\"Yes\",\"--\",\"40%\",\"ETFs & Closed End Funds\"",
    "\"Cash & Cash Investments\",\"--\",\"--\",\"--\",\"--\",\"--\",\"$1,400.00\",\"0%\",\"$0.00\",\"--\",\"--\","
    "\"--\",\"--\",\"--\",\"--\",\"28%\",\"Cash and Money Market\"",
    "\"Account Total\",\"--\",\"--\",\"--\",\"--\",\"--\",\"$5,000.00\",\"--\",\"--\",\"$4,900.00\",\"--\","
    "\"-$1,300.00\",\"--\",\"--\",\"--\",\"--\",\"--\"",
])


def text_pdf(text: str) -> bytes:
    pdf = PDF(size=7)
    y = 40
    for line in text.splitlines():
        if line:
            pdf.text(24, y, line)
        y += 10
    return pdf.to_bytes()


FIXTURES = {
    "1099_example_brokerage.pdf": lambda: text_pdf(EXAMPLE),
    "1099_sample_securities.pdf": lambda: text_pdf(SAMPLE),
    "1099_specimen_markets.txt": lambda: (SPECIMEN + "\n").encode(),
    "1099r_sample_trust.txt": lambda: (R_TRUST + "\n").encode(),
    "1099nec_example_client.txt": lambda: (NEC + "\n").encode(),
    "1099b_lots_example.csv": lambda: (LOTS + "\n").encode(),
    "positions_example.csv": lambda: (POSITIONS_EXAMPLE + "\n").encode(),
    "positions_sample.csv": lambda: (POSITIONS_SAMPLE + "\n").encode(),
}


if __name__ == "__main__":
    for name, make in FIXTURES.items():
        (HERE / name).write_bytes(make())
    print("wrote synthetic 1099s and positions to", HERE)
