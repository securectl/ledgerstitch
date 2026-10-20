# SPDX-License-Identifier: AGPL-3.0-only
import csv
import io
import zipfile
from datetime import date
from decimal import Decimal

from ledgerstitch.models import Account, AccountKind, Direction, Lane, Transaction
from ledgerstitch.reports.csv_export import CSV_COLUMNS, export_csv
from ledgerstitch.reports.quickbooks import export_iif, export_qbo_csv

from test_summary import make_accounts, make_txns


# --------------------------------------------------------------------------- CSV
def test_csv_flat_signed_iso(tmp_path):
    p = export_csv(make_txns(), tmp_path / "ledger.csv")
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert list(rows[0].keys()) == CSV_COLUMNS
    assert len(rows) == 10                              # 11 minus the duplicate
    assert rows[0]["date"] == "2024-12-30"
    adobe = [r for r in rows if r["description"] == "ADOBE *CREATIVE CLD"]
    assert len(adobe) == 1 and adobe[0]["amount"] == "-100.00"
    refund = [r for r in rows if r["description"] == "ADOBE REFUND"][0]
    assert refund["amount"] == "20.00"


def test_csv_year_and_injection(tmp_path):
    t = Transaction("a", date(2025, 1, 1), "=cmd|'/c calc'!A1", Decimal("5"), Direction.OUT)
    p = export_csv(make_txns() + [t], tmp_path / "l.csv", year=2025)
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert len(rows) == 10
    assert any(r["description"].startswith("'=") for r in rows)


# --------------------------------------------------------------------------- QBO
def test_qbo_zip_per_account_sign_convention(tmp_path):
    p = export_qbo_csv(make_txns(), make_accounts(), tmp_path / "qbo.csv")
    assert p.suffix == ".zip"
    with zipfile.ZipFile(p) as zf:
        names = sorted(zf.namelist())
        assert len(names) == 2
        files = {n: list(csv.reader(io.StringIO(zf.read(n).decode()))) for n in names}
    card = next(v for k, v in files.items() if "4012" in k)
    chk = next(v for k, v in files.items() if "1111" in k)
    assert card[0] == ["Date", "Description", "Amount"]
    assert card[1] == ["12/30/2024", "GITHUB", "-999.00"]
    adobe = [r for r in card[1:] if r[1] == "ADOBE *CREATIVE CLD"]
    assert adobe == [["01/05/2025", "ADOBE *CREATIVE CLD", "-100.00"]]   # duplicate excluded
    assert ["01/20/2025", "ADOBE REFUND", "20.00"] in card
    assert ["03/16/2025", "PAYMENT THANK YOU", "500.00"] in card        # transfers included
    assert ["03/15/2025", "PAYMENT TO CHASE CARD", "-500.00"] in chk
    assert ["03/31/2025", "ACME PAYROLL DIRECT DEP", "2000.00"] in chk


def test_qbo_single_account_csv(tmp_path):
    txns = [t for t in make_txns() if t.account_id == "chk-1111"]
    p = export_qbo_csv(txns, list(make_accounts().values()), tmp_path / "qbo.csv", year=2025)
    assert p.suffix == ".csv"
    rows = list(csv.reader(p.open(encoding="utf-8")))
    assert rows[0] == ["Date", "Description", "Amount"]
    assert len(rows) == 1 + 3
    assert all(r[0][2] == "/" and r[0][5] == "/" for r in rows[1:])


# --------------------------------------------------------------------------- IIF
def _parse_iif(p):
    text = p.read_bytes().decode("cp1252")
    assert "\r\n" in text
    return [line.split("\t") for line in text.split("\r\n") if line]


def _blocks(rows):
    blocks, cur = [], None
    for r in rows:
        if r[0] == "TRNS":
            cur = [r]
        elif r[0] == "SPL":
            cur.append(r)
        elif r[0] == "ENDTRNS":
            blocks.append(cur)
            cur = None
    return blocks


def test_iif_structure_and_balance(tmp_path):
    p = export_iif(make_txns(), make_accounts(), tmp_path / "qb.iif",
                   {"Groceries": "Owner's Draw:Groceries"}, year=2025)
    rows = _parse_iif(p)
    assert rows[0][0] == "!ACCNT"
    heads = [r[0] for r in rows if r[0].startswith("!")]
    assert heads == ["!ACCNT", "!TRNS", "!SPL", "!ENDTRNS"]
    blocks = _blocks(rows)
    # 9 active 2025 txns, transfer pair collapses into one block -> 8
    assert len(blocks) == 8
    for b in blocks:
        assert len(b) == 2
        total = sum(Decimal(r[6]) for r in b)
        assert total == 0, b
        assert Decimal(b[0][6]) == -Decimal(b[1][6])
        assert b[0][3][2] == "/" and len(b[0][3]) == 10
        assert all(len(r[6].split(".")[1]) == 2 for r in b)

    by_memo = {b[0][7]: b for b in blocks}
    adobe = by_memo["ADOBE *CREATIVE CLD"]
    assert adobe[0][2] == "CREDIT CARD"
    assert adobe[0][4] == "Chase Freedom 4012"
    assert adobe[0][6] == "-100.00"
    assert adobe[1][4] == "LedgerStitch:Software & Subscriptions"
    assert by_memo["ADOBE REFUND"][0][2] == "CCARD REFUND"
    fee = by_memo["MONTHLY SERVICE FEE"]
    assert fee[0][2] == "CHECK" and fee[0][4] == "Chase Checking 1111"
    dep = by_memo["ACME PAYROLL DIRECT DEP"]
    assert dep[0][2] == "DEPOSIT" and dep[0][6] == "2000.00"
    assert dep[1][4] == "LedgerStitch Income:Income & Deposits"
    assert by_memo["KROGER #123"][1][4] == "Owner's Draw:Groceries"
    transfer = [b for b in blocks if b[0][2] == "TRANSFER"]
    assert len(transfer) == 1
    assert transfer[0][0][4] == "Chase Checking 1111" and transfer[0][0][6] == "-500.00"
    assert transfer[0][1][4] == "Chase Freedom 4012" and transfer[0][1][6] == "500.00"

    accnt = {r[1]: r[2] for r in rows if r[0] == "ACCNT"}
    assert accnt["LedgerStitch"] == "EXP"
    assert accnt["LedgerStitch:Business Meals"] == "EXP"
    assert accnt["Chase Freedom 4012"] == "CCARD"
    assert accnt["Chase Checking 1111"] == "BANK"
    # every account used by a transaction is declared
    used = {r[4] for b in blocks for r in b}
    assert used <= set(accnt)
    # parents come before children
    names = [r[1] for r in rows if r[0] == "ACCNT"]
    assert names.index("LedgerStitch") < names.index("LedgerStitch:Business Meals")


def test_iif_sanitizes_and_maps_accounts(tmp_path):
    accts = {"x": Account("x", "Biz Checking", "Bank", AccountKind.CHECKING)}
    t = Transaction("x", date(2025, 6, 1), "ACME\tSUPPLY\nCO \"BEST\"", Decimal("12.5"),
                    Direction.OUT, merchant="ACME\tSUPPLY", category="Office Supplies",
                    lane=Lane.BUSINESS, schedule_c="L18", note="line1\r\nline2")
    p = export_iif([t], accts, tmp_path / "x.iif",
                   {"x": "Operating Account", "Office Supplies": "Office Expense"})
    rows = _parse_iif(p)
    b = _blocks(rows)[0]
    assert all(len(r) == 8 for r in b)
    assert b[0][4] == "Operating Account" and b[1][4] == "Office Expense"
    assert b[0][5] == "ACME SUPPLY"
    assert '"' not in b[0][7] and "\n" not in b[0][7]
    assert b[0][6] == "-12.50" and b[1][6] == "12.50"


def test_iif_unpaired_transfer_goes_to_clearing(tmp_path):
    t = Transaction("chk-1111", date(2025, 7, 1), "PAYMENT TO AMEX", Decimal("300"), Direction.OUT,
                    category="Credit Card Payment", lane=Lane.TRANSFER, transfer_group=None)
    rows = _parse_iif(export_iif([t], make_accounts(), tmp_path / "c.iif", {}))
    b = _blocks(rows)[0]
    assert b[0][2] == "CHECK"
    assert b[1][4] == "LedgerStitch Clearing:Credit Card Payment"
