# SPDX-License-Identifier: AGPL-3.0-only
from datetime import date
from decimal import Decimal

import pytest

from ledgerstitch.ai.rules import apply_heuristics, apply_rules, learn_rule, match_heuristic
from ledgerstitch.models import Direction, Lane, Rule, Source, Status, Transaction


def tx(desc, merchant="", account="chase-1111", direction=Direction.OUT, **kw):
    return Transaction(account_id=account, date=date(2025, 3, 1), description=desc,
                       amount=Decimal("12.34"), direction=direction, merchant=merchant, **kw)


# ---------------------------------------------------------------- rules
def test_longest_pattern_wins():
    t = tx("AMAZON WEB SERVICES AWS.AMAZON.CO", merchant="AMAZON WEB SERVICES")
    rules = [Rule("AMAZON", "Shopping", Lane.PERSONAL),
             Rule("AMAZON WEB SERVICES", "Cloud & Hosting", Lane.BUSINESS)]
    assert apply_rules([t], rules) == 1
    assert t.category == "Cloud & Hosting"
    assert t.lane == Lane.BUSINESS
    assert t.schedule_c == "L18"          # category default fills in
    assert t.source == Source.RULE and t.status == Status.AUTO and t.confidence == 1.0


def test_account_scoped_beats_global_even_if_shorter():
    t = tx("COSTCO WHSE #0123", merchant="COSTCO WHSE", account="biz-2222")
    rules = [Rule("COSTCO WHSE", "Groceries", Lane.PERSONAL),
             Rule("COSTCO", "Inventory & Cost of Goods", Lane.BUSINESS, "L4", account_id="biz-2222")]
    apply_rules([t], rules)
    assert t.category == "Inventory & Cost of Goods"
    other = tx("COSTCO WHSE #0123", merchant="COSTCO WHSE", account="personal-3333")
    apply_rules([other], rules)
    assert other.category == "Groceries"


def test_merchant_field_falls_back_to_description_and_description_field():
    t = tx("NETFLIX.COM LOS GATOS")          # no merchant set
    apply_rules([t], [Rule("netflix", "Entertainment & Streaming", Lane.PERSONAL)])
    assert t.category == "Entertainment & Streaming"
    t2 = tx("ACH DEBIT GUSTO PAYROLL", merchant="GUSTO")
    apply_rules([t2], [Rule("ACH DEBIT", "Bank & Merchant Fees", Lane.BUSINESS, field="description"),
                       Rule("PAYROLL", "Payroll & Wages", Lane.BUSINESS)])  # merchant field: no hit
    assert t2.category == "Bank & Merchant Fees"


def test_rules_skip_locked_transactions():
    approved = tx("STARBUCKS", merchant="STARBUCKS", status=Status.APPROVED, category="Business Meals")
    user = tx("STARBUCKS", merchant="STARBUCKS", source=Source.USER, category="Business Meals")
    dup = tx("STARBUCKS", merchant="STARBUCKS", duplicate_of="abc")
    xfer = tx("PAYMENT THANK YOU", merchant="STARBUCKS", lane=Lane.TRANSFER, confidence=0.95,
              category="Credit Card Payment")
    grouped = tx("ONLINE TRANSFER", merchant="STARBUCKS", transfer_group="g1")
    rules = [Rule("STARBUCKS", "Dining & Restaurants", Lane.PERSONAL)]
    assert apply_rules([approved, user, dup, xfer, grouped], rules) == 0
    assert approved.category == "Business Meals" and xfer.category == "Credit Card Payment"


def test_rule_with_review_lane_sets_review_status():
    t = tx("SHELL OIL 1234", merchant="SHELL OIL")
    apply_rules([t], [Rule("SHELL", "Vehicle & Fuel", Lane.REVIEW)])
    assert t.status == Status.REVIEW and t.schedule_c == "L9"


# ---------------------------------------------------------------- heuristics
@pytest.mark.parametrize("text,expected", [
    ("AMAZON WEB SERVICES AWS.AMAZON.CO WA", "Cloud & Hosting"),
    ("AMZN MKTP US*2K4", "Shopping"),
    ("UBER EATS PENDING", "Dining & Restaurants"),
    ("UBER *TRIP HELP.UBER.COM", "Rideshare & Transit"),
    ("RESTAURANT DEPOT #12", "Inventory & Cost of Goods"),
    ("RESTAURANT APPLIANCE SUPPLY", "Equipment & Supplies"),
    ("GUSTO CONTRACTOR PMT", "Contractor Payments"),
    ("GUSTO PAYROLL", "Payroll & Wages"),
    ("LINKEDIN LEARNING", "Education & Training"),
    ("MONTHLY SERVICE FEE", "Bank & Merchant Fees"),
    ("ZELLE TRANSFER TO <PERSON>", "Zelle & P2P Payments"),   # not own-account transfer
    ("ONLINE TRANSFER TO SAV 1234", "Transfer Between Accounts"),
    ("AMERICAN AIRLINES 0012", "Business Travel"),
    ("MCDONALDS F123", "Dining & Restaurants"),
    ("TACOS EL GUERO", "Dining & Restaurants"),
    ("UPS STORE 4471", "Freight & Shipping"),
])
def test_heuristic_tricky_strings(text, expected):
    c = match_heuristic(text, Direction.OUT)
    assert c is not None and c.name == expected


@pytest.mark.parametrize("text", [
    "LAWSON PRODUCTS",        # contains AWS
    "CPAP SUPPLY CO",         # contains CPA
    "GROUPSHARE",             # contains UPS
    "SQUATCH SOAP",           # no keyword
])
def test_heuristic_word_boundaries(text):
    c = match_heuristic(text)
    assert c is None or c.name not in ("Cloud & Hosting", "Professional Services", "Freight & Shipping")


def test_income_keywords_need_money_in():
    assert match_heuristic("SECURITY DEPOSIT", Direction.OUT) is None
    assert match_heuristic("MOBILE DEPOSIT", Direction.IN).name == "Income & Deposits"


def test_apply_heuristics_sets_fields_and_respects_order():
    biz = tx("ADOBE *CREATIVE CLD", merchant="ADOBE")
    fuel = tx("SHELL OIL 5744", merchant="SHELL OIL")
    groc = tx("KROGER #331", merchant="KROGER")
    ruled = tx("ADOBE", merchant="ADOBE", source=Source.RULE, category="Advertising & Marketing",
               confidence=1.0, lane=Lane.BUSINESS)
    nothing = tx("ZQX WIDGETS", merchant="ZQX WIDGETS")
    assert apply_heuristics([biz, fuel, groc, ruled, nothing]) == 3
    assert (biz.category, biz.lane, biz.schedule_c, biz.status) == \
        ("Software & Subscriptions", Lane.BUSINESS, "L18", Status.AUTO)
    assert biz.source == Source.HEURISTIC and biz.confidence == 0.7
    assert fuel.lane == Lane.REVIEW and fuel.status == Status.REVIEW and fuel.schedule_c == "L9"
    assert groc.lane == Lane.PERSONAL and groc.schedule_c is None
    assert ruled.category == "Advertising & Marketing"
    assert nothing.category is None


def test_learn_rule_uses_normalized_merchant():
    t = tx("TST* JOES BBQ 0042 DALLAS TX", merchant="TST* JOES BBQ")
    r = learn_rule(t, "Business Meals", "business", "L24b")
    assert r.pattern == "TST* JOES BBQ" and r.lane == Lane.BUSINESS and r.field == "merchant"
    assert r.created_by == Source.RULE and r.account_id is None
    t2 = tx("TST* JOES BBQ 0099 DALLAS TX", merchant="TST* JOES BBQ")
    apply_rules([t2], [r])
    assert t2.category == "Business Meals" and t2.schedule_c == "L24b"


def test_incoming_payout_from_expense_merchant_goes_to_review():
    from datetime import date
    from ledgerstitch.ai.rules import apply_heuristics
    from ledgerstitch.models import Direction, Lane, Status, Transaction
    t = Transaction(account_id="chk", date=date(2025, 3, 1), description="UPWORK ESCROW PAYOUT",
                    amount="500.00", direction=Direction.IN, merchant="UPWORK")
    apply_heuristics([t])
    if t.category:  # matched an expense category by keyword
        assert t.lane == Lane.REVIEW
        assert t.status == Status.REVIEW
