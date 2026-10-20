# SPDX-License-Identifier: AGPL-3.0-only
import json
from datetime import date
from decimal import Decimal

import pytest

from ledgerstitch.models import Account, AccountKind, Direction, Lane, Source, Status, Transaction
from ledgerstitch.reports.summary import DISCLAIMER, summarize

OUT, IN = Direction.OUT, Direction.IN


def make_accounts():
    return {
        "chk-1111": Account("chk-1111", "Chase Checking ••1111", "Chase", AccountKind.CHECKING, "1111"),
        "card-4012": Account("card-4012", "Chase Freedom ••4012", "Chase", AccountKind.CREDIT_CARD, "4012"),
    }


def make_txns():
    """Synthetic ledger. 2025 expected: business 155, personal 50, review 30,
    income 2000, transfers 500 out / 500 in; plus one 2024 business row (999)
    and one duplicate (excluded everywhere)."""
    def T(acct, d, desc, amt, direction, **kw):
        kw.setdefault("source", Source.HEURISTIC)
        kw.setdefault("status", Status.AUTO)
        return Transaction(account_id=acct, date=d, description=desc, amount=Decimal(amt),
                           direction=direction, **kw)

    adobe = T("card-4012", date(2025, 1, 5), "ADOBE *CREATIVE CLD", "100.00", OUT,
              merchant="ADOBE", category="Software & Subscriptions", lane=Lane.BUSINESS,
              schedule_c="L18", source=Source.RULE)
    txns = [
        adobe,
        T("card-4012", date(2025, 1, 20), "ADOBE REFUND", "20.00", IN, merchant="ADOBE",
          category="Software & Subscriptions", lane=Lane.BUSINESS, schedule_c="L18",
          refund_of=adobe.id),
        T("card-4012", date(2025, 2, 3), "TST* BISTRO CLIENT LUNCH", "60.00", OUT, merchant="BISTRO",
          category="Business Meals", lane=Lane.BUSINESS, schedule_c="L24b", source=Source.USER,
          note="Lunch w/ client"),
        T("card-4012", date(2025, 2, 10), "KROGER #123", "50.00", OUT, merchant="KROGER",
          category="Groceries", lane=Lane.PERSONAL),
        T("card-4012", date(2025, 3, 1), "MYSTERY VENDOR", "30.00", OUT, merchant="MYSTERY VENDOR",
          category=None, lane=Lane.REVIEW, status=Status.REVIEW, source=Source.NONE),
        T("chk-1111", date(2025, 3, 15), "PAYMENT TO CHASE CARD", "500.00", OUT,
          category="Credit Card Payment", lane=Lane.TRANSFER, transfer_group="G1"),
        T("card-4012", date(2025, 3, 16), "PAYMENT THANK YOU", "500.00", IN,
          category="Credit Card Payment", lane=Lane.TRANSFER, transfer_group="G1"),
        T("chk-1111", date(2025, 3, 31), "ACME PAYROLL DIRECT DEP", "2000.00", IN,
          category="Income & Deposits", lane=Lane.INCOME),
        T("chk-1111", date(2025, 4, 2), "MONTHLY SERVICE FEE", "15.00", OUT,
          merchant="MONTHLY SERVICE FEE", category="Bank & Merchant Fees", lane=Lane.BUSINESS,
          schedule_c="L10", status=Status.REVIEW, source=Source.AI),
        # duplicate of the Adobe charge (second import of the same statement)
        T("card-4012", date(2025, 1, 5), "ADOBE *CREATIVE CLD", "100.00", OUT, seq=7,
          merchant="ADOBE", category="Software & Subscriptions", lane=Lane.BUSINESS,
          schedule_c="L18", duplicate_of=adobe.id),
        # prior year
        T("card-4012", date(2024, 12, 30), "GITHUB", "999.00", OUT, merchant="GITHUB",
          category="Software & Subscriptions", lane=Lane.BUSINESS, schedule_c="L18"),
    ]
    return txns


def test_lane_totals_net_refunds_and_exclude_transfers_and_dupes():
    s = summarize(make_txns(), 2025)
    t = s["totals"]
    assert t["business"] == 155.00
    assert t["personal"] == 50.00
    assert t["review"] == 30.00
    assert t["expenses"] == 235.00
    assert t["income"] == 2000.00
    assert t["transfers_out"] == 500.00 and t["transfers_in"] == 500.00
    assert t["excluded"] == 500.00
    assert s["counts"]["duplicates"] == 1
    assert s["counts"]["total"] == 9
    assert s["counts"]["transfer"] == 2


def test_year_filter():
    assert summarize(make_txns(), 2024)["totals"]["business"] == 999.00
    assert summarize(make_txns(), 2024)["counts"]["total"] == 1
    assert summarize(make_txns())["totals"]["business"] == 1154.00
    assert summarize(make_txns(), 2023)["counts"]["total"] == 0


def test_by_category_nets_refund():
    s = summarize(make_txns(), 2025)
    cats = {(c["lane"], c["category"]): c for c in s["by_category"]}
    assert cats[("business", "Software & Subscriptions")]["total"] == 80.00
    assert cats[("business", "Software & Subscriptions")]["count"] == 2
    assert cats[("business", "Software & Subscriptions")]["schedule_c"] == "L18"
    assert cats[("review", "Uncategorized")]["total"] == 30.00
    assert not any(c["lane"] in ("transfer", "income") for c in s["by_category"])
    # business categories come first
    assert s["by_category"][0]["lane"] == "business"


def test_schedule_c_grouping_and_meals():
    s = summarize(make_txns(), 2025)
    lines = {r["line"]: r for r in s["by_schedule_c"]}
    assert list(lines) == ["L10", "L18", "L24b"]  # form order
    assert lines["L18"]["gross"] == 80.00 and lines["L18"]["deductible"] == 80.00
    assert lines["L18"]["name"] == "Office expense"
    assert lines["L24b"]["gross"] == 60.00
    assert lines["L24b"]["deductible_pct"] == 0.5
    assert lines["L24b"]["deductible"] == 30.00
    assert s["schedule_c_total"] == {"gross": 155.00, "deductible": 125.00}
    assert s["disclaimer"] == DISCLAIMER
    assert "50%" in s["meals_note"]


def test_review_and_uncategorized_counts():
    s = summarize(make_txns(), 2025)
    assert s["review_count"] == 2          # review lane row + business row with status REVIEW
    assert s["uncategorized_count"] == 1


def test_by_account_and_month():
    s = summarize(make_txns(), 2025)
    acct = {a["account_id"]: a for a in s["by_account"]}
    assert acct["card-4012"]["business"] == 140.00
    assert acct["chk-1111"]["business"] == 15.00
    assert acct["chk-1111"]["transfers_out"] == 500.00
    assert acct["card-4012"]["transfers_in"] == 500.00
    assert acct["chk-1111"]["income"] == 2000.00
    months = {m["month"]: m for m in s["by_month"]}
    assert months["2025-01"]["business"] == 80.00
    assert months["2025-03"]["income"] == 2000.00
    assert "2024-12" not in months


def test_json_serializable():
    s = summarize(make_txns(), 2025)
    json.dumps(s)
    assert isinstance(s["totals"]["business"], float)


def test_empty():
    s = summarize([], 2025)
    assert s["totals"]["business"] == 0.0
    assert s["by_category"] == [] and s["by_schedule_c"] == []
    json.dumps(s)


@pytest.mark.parametrize("amt", ["0.005", "0.015"])
def test_rounding_half_up(amt):
    t = Transaction("a", date(2025, 1, 1), "X", Decimal(amt), OUT, lane=Lane.BUSINESS, schedule_c="L18")
    s = summarize([t], 2025)
    assert s["totals"]["business"] == float(Decimal(amt).quantize(Decimal("0.01"), "ROUND_HALF_UP"))
