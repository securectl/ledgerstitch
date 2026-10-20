# SPDX-License-Identifier: AGPL-3.0-only
"""Catalog bootstrap (known_formats.json) + payment-platform semantics, per manifest fixture."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from backend_fakes import install_missing
from ledgerstitch import store
from ledgerstitch.ingest import learning
from ledgerstitch.ingest.detect import detect
from ledgerstitch.ingest.dispatch import parse_any
from ledgerstitch.models import Direction, Lane
from ledgerstitch.security.vault import Vault

FX = Path(__file__).parent / "fixtures" / "formats"
MANIFEST = FX / "manifest.json"
PASS = "correct horse battery"

# file -> (transactions, net signed total). Net equals the account's balance change.
EXPECTED = {
    "schwab_bank_checking.csv": (3, "2375.50"),     # Pending row dropped
    "dcu_checking.csv": (3, "1107.30"),
    "mercury.csv": (2, "4751.00"),                  # Failed + Pending dropped
    "revolut.csv": (4, "395.00"),                   # PENDING dropped; exchange fee 0.50 as its own row
    "paypal_activity.csv": (4, "0.00"),             # gross 100 + fee -3.79, -15, withdrawal -81.21; Memo skipped
    "venmo_statement.csv": (3, "0.00"),             # balance/disclaimer rows skipped
    "stripe_balance_itemized.csv": (4, "0.00"),     # charge 200 + fee -6.10, refund -50, payout -143.90
    "shopify_payments_transactions.csv": (4, "0.00"),  # charge 45 + fee -1.61, refund -20, payout -23.39
    "coinbase_transactions.csv": (3, "-65.74"),     # 2-line preamble; direction from type map
    "monzo.csv": (3, "488.10"),                     # dd/mm/yyyy from the catalog
}


def _manifest() -> list[dict]:
    if not MANIFEST.exists():
        return []
    return json.loads(MANIFEST.read_text())["files"]


def _catalog() -> dict[str, dict]:
    return {e["id"]: e for e in learning.load_catalog()}


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    install_missing(monkeypatch)


@pytest.fixture
def ws(tmp_path):
    v = Vault.create(tmp_path / "v" / "c.vault", PASS, "Cat Co")
    w = store.Workspace(v)
    w.save()
    yield w
    v.lock()


def _parse(name: str):
    data = (FX / name).read_bytes()
    p = detect(name, data)
    txns, warnings, info = parse_any(name, data, p, "acct", "imp")
    return p, txns, warnings, info


def _net(txns) -> Decimal:
    return sum((t.signed_amount for t in txns), Decimal(0))


def test_catalog_loads_real_schema():
    cat = _catalog()
    if not cat:
        pytest.skip("known_formats.json not present")
    assert len(cat) >= 60
    e = cat.get("paypal_activity")
    assert e and e["mapping"]["amount"] == "Net" and e["format"] == "CSV"
    assert cat["schwab_bank_checking"]["mapping"]["debit"] == "Withdrawal"


@pytest.mark.parametrize("entry", _manifest(), ids=lambda e: e["file"])
def test_manifest_fixture(entry):
    f = FX / entry["file"]
    if not f.exists():
        pytest.skip("fixture missing")
    p, txns, warnings, info = _parse(entry["file"])
    assert txns, warnings
    assert all(t.amount > 0 for t in txns)
    cat = _catalog()
    cid = entry.get("catalog_id")
    if entry["format"] == "csv":
        assert cid in cat
        assert p.id == f"catalog:{cid}" and p.catalog_id == cid
        assert p.institution == cat[cid]["institution"]
        assert p.recognition["source"] == "catalog"
        n, net = EXPECTED[entry["file"]]
        assert len(txns) == n
        assert _net(txns) == Decimal(net)
    else:
        assert p.recognition and p.recognition["format"]


def test_builtin_profiles_still_win_over_catalog():
    data = (FX.parent / "chase_card.csv").read_bytes()
    assert detect("x.csv", data).id == "chase_card"
    bare = b"Date,Description,Amount\n01/02/2025,LOCAL GROCER,-45.00\n01/03/2025,FUEL STOP,-30.00\n"
    assert detect("export.csv", bare).id == "generic"


def test_coinbase_preamble_header_found():
    p, txns, _, _ = _parse("coinbase_transactions.csv")
    assert p.header_row == 2 and p.institution == "Coinbase"
    assert [t.direction for t in txns] == [Direction.OUT, Direction.OUT, Direction.IN]


def test_paypal_net_fee_memo_and_withdrawal():
    p, txns, warnings, info = _parse("paypal_activity.csv")
    fees = [t for t in txns if t.category == "Payment Processing Fees"]
    assert len(fees) == 1 and fees[0].amount == Decimal("3.79") and fees[0].schedule_c == "L10"
    assert fees[0].direction == Direction.OUT and fees[0].lane == Lane.BUSINESS
    sale = next(t for t in txns if t.direction == Direction.IN)
    assert sale.amount == Decimal("100.00")              # gross; gross + fee == Net 96.21
    wd = [t for t in txns if t.lane == Lane.TRANSFER]
    assert len(wd) == 1 and wd[0].amount == Decimal("81.21") and wd[0].direction == Direction.OUT
    assert any("memo row" in w for w in warnings)
    assert info["platform"] == "PayPal"


def test_stripe_and_shopify_payouts_are_transfers_and_dates():
    _, st, _, _ = _parse("stripe_balance_itemized.csv")
    payout = [t for t in st if t.lane == Lane.TRANSFER]
    assert [(t.amount, t.direction) for t in payout] == [(Decimal("143.90"), Direction.OUT)]
    _, sh, _, _ = _parse("shopify_payments_transactions.csv")
    charge = next(t for t in sh if t.direction == Direction.IN)
    assert charge.date.isoformat() == "2025-08-01"     # Transaction Date, not Payout Date
    assert sum(1 for t in sh if t.category == "Payment Processing Fees") == 1


def test_venmo_description_and_funding_leg():
    _, txns, _, _ = _parse("venmo_statement.csv")
    assert not any("venmo balance" in t.description.lower() for t in txns)
    assert any("lunch" in t.description for t in txns)
    data = (FX / "venmo_statement.csv").read_bytes().replace(b",Venmo balance,,,,,,,", b",SAMPLE CARD *1111,,,,,,,", 1)
    p = detect("venmo.csv", data)
    t2, _, _ = parse_any("venmo.csv", data, p, "a", "i")
    legs = [t for t in t2 if t.description.startswith("Funding from")]
    assert len(legs) == 1 and legs[0].direction == Direction.IN and legs[0].lane == Lane.TRANSFER
    assert _net(t2) == Decimal("12.00")                 # the card-funded $12 came from outside


def test_square_gross_fee_net_inline():
    cat = _catalog().get("square_transactions")
    if not cat:
        pytest.skip("no square entry")
    header = cat["header"]
    idx = {h.lower(): i for i, h in enumerate(header)}

    def row(**kw):
        r = [""] * len(header)
        for k, v in kw.items():
            r[idx[k]] = v
        return ",".join(r)
    data = "\n".join([",".join(header),
                      row(**{"date": "2025-07-01", "time": "10:00:00", "total collected": "$50.00", "fees": "-$1.75",
                             "net total": "$48.25", "description": "Custom Amount", "transaction status": "Complete"}),
                      row(**{"date": "2025-07-02", "time": "11:00:00", "total collected": "$20.00", "fees": "-$0.70",
                             "net total": "$19.30", "description": "Item", "transaction status": "Complete"})]) + "\n"
    p = detect("square.csv", data.encode())
    assert p.catalog_id == "square_transactions"
    txns, _, _ = parse_any("square.csv", data.encode(), p, "a", "i")
    assert len(txns) == 4 and _net(txns) == Decimal("67.55")


def test_unverified_catalog_entry_needs_confirmation(tmp_path, monkeypatch):
    hdr = ["Value Day", "Booking Text", "Counterparty", "Debit/Credit", "Amount", "Saldo", "Ref"]
    data = (",".join(hdr) + "\n01/03/2025,CARD,NORTHWIND PAPER,DEBIT,-25.00,975.00,R1\n"
            "01/05/2025,TRANSFER,CLIENT ZETA,CREDIT,500.00,1475.00,R2\n"
            "01/09/2025,CARD,BLUE HERON CAFE,DEBIT,-4.50,1470.50,R3\n").encode()

    def use(amount_col, sign):
        cat = tmp_path / f"k{amount_col}.json"
        cat.write_text(json.dumps({"entries": [{
            "id": "northwind", "institution": "Northwind Bank", "product": "checking", "format": "csv",
            "header": hdr, "amount_columns": {"amount": amount_col}, "sign_convention": sign,
            "verified": False}]}))
        monkeypatch.setattr(learning, "_CATALOG_PATH", cat)
        return detect("x.csv", data)
    agree = use("Amount", "negative_is_out")
    assert agree.institution == "Northwind Bank" and agree.recognition["verified"] is False
    assert agree.recognition["agrees"] is True and agree.recognition["needs_confirmation"] is False
    assert agree.confidence <= 0.75
    disagree = use("Amount", "positive_is_out")
    assert disagree.recognition["needs_confirmation"] is True and disagree.confidence < 0.75


def test_platform_payout_pairs_with_bank_deposit(ws):
    ws.add_import("stripe_balance_itemized.csv", (FX / "stripe_balance_itemized.csv").read_bytes())
    ws.add_import("paypal_activity.csv", (FX / "paypal_activity.csv").read_bytes())
    bank = (b"Date,Description,Amount\n07/06/2025,STRIPE TRANSFER ST-SAMPLE,143.90\n"
            b"05/11/2025,PAYPAL TRANSFER SAMPLE,81.21\n07/08/2025,LOCAL COFFEE,-3.00\n")
    ws.add_import("business checking.csv", bank)
    by_desc = {t.description: t for t in ws.transactions.values()}
    dep_s = by_desc["STRIPE TRANSFER ST-SAMPLE"]
    dep_p = by_desc["PAYPAL TRANSFER SAMPLE"]
    for dep, amt in ((dep_s, Decimal("143.90")), (dep_p, Decimal("81.21"))):
        assert dep.transfer_group, "bank deposit should pair with the platform payout"
        legs = [t for t in ws.transactions.values() if t.transfer_group == dep.transfer_group]
        assert len(legs) == 2 and {t.direction for t in legs} == {Direction.IN, Direction.OUT}
        assert all(t.amount == amt and t.lane == Lane.TRANSFER for t in legs)


def test_confirming_platform_mapping_does_not_duplicate(ws):
    data = (FX / "paypal_activity.csv").read_bytes()
    r = ws.add_import("paypal_activity.csv", data)
    assert r.new == 4 and r.detected["recognition"]["source"] == "catalog"
    r2 = ws.add_import("paypal_activity.csv", data, None, r.detected["mapping"])
    assert r2.new == 0 and r2.count == 4
    assert ws.learned_formats and ws.learned_formats[0]["platform"] == "paypal_activity"
