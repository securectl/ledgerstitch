# SPDX-License-Identifier: AGPL-3.0-only
from datetime import date
from decimal import Decimal

from ledgerstitch.ingest.normalize import normalize_merchant
from ledgerstitch.ingest.reconcile import ReconcileReport, reconcile
from ledgerstitch.models import Account, AccountKind, Direction, Lane, Source, Status, Transaction

OUT, IN = Direction.OUT, Direction.IN

ACCOUNTS = {
    "chk": Account("chk", "Checking", "Sample Bank", AccountKind.CHECKING, "0001"),
    "sav": Account("sav", "Savings", "Sample Bank", AccountKind.SAVINGS, "0002"),
    "card": Account("card", "Card", "Sample Card", AccountKind.CREDIT_CARD, "1111"),
    "card2": Account("card2", "Card 2", "Other Card", AccountKind.CREDIT_CARD, "2222"),
}


def T(acct, d, desc, amt, direction, imp="i1", seq=0, **kw):
    return Transaction(account_id=acct, date=date.fromisoformat(d), description=desc, amount=Decimal(amt),
                       direction=direction, import_id=imp, seq=seq, merchant=normalize_merchant(desc), **kw)


# --------------------------------------------------------------------------- duplicates

def test_overlapping_imports_are_deduplicated_by_multiset():
    a = [T("card", "2025-01-03", "BLUEBIRD COFFEE", "5.00", OUT, "A", 0),
         T("card", "2025-01-03", "BLUEBIRD COFFEE", "5.00", OUT, "A", 1),
         T("card", "2025-01-04", "LANTERN BOOKS", "20.00", OUT, "A", 2)]
    # second export overlaps: same two coffees + one new row, different seq
    b = [T("card", "2025-01-03", "BLUEBIRD COFFEE", "5.00", OUT, "B", 7),
         T("card", "2025-01-03", "BLUEBIRD COFFEE", "5.00", OUT, "B", 8),
         T("card", "2025-01-05", "NEW ROW", "1.00", OUT, "B", 9)]
    txns = a + b
    rep = reconcile(txns, ACCOUNTS)
    assert rep.duplicates == 2
    live = [t for t in txns if t.duplicate_of is None]
    assert len(live) == 4
    assert sum(1 for t in live if "COFFEE" in t.description) == 2     # both genuine coffees kept
    for t in txns:
        if t.duplicate_of:
            kept = next(k for k in txns if k.id == t.duplicate_of)
            assert kept.import_id != t.import_id and kept.duplicate_of is None


def test_partial_overlap_keeps_max_count():
    txns = [T("card", "2025-01-03", "X", "5.00", OUT, "A", 0),
            T("card", "2025-01-03", "X", "5.00", OUT, "B", 0),
            T("card", "2025-01-03", "X", "5.00", OUT, "B", 1)]
    rep = reconcile(txns, ACCOUNTS)
    assert rep.duplicates == 1
    assert sum(t.duplicate_of is None for t in txns) == 2


def test_repeats_within_one_file_never_collapsed():
    txns = [T("card", "2025-01-03", "X", "5.00", OUT, "A", i) for i in range(3)]
    assert reconcile(txns, ACCOUNTS).duplicates == 0


def test_same_row_on_different_accounts_is_not_duplicate():
    txns = [T("card", "2025-01-03", "X", "5.00", OUT, "A"), T("card2", "2025-01-03", "X", "5.00", OUT, "B")]
    assert reconcile(txns, ACCOUNTS).duplicates == 0


def test_rerun_is_stable():
    txns = [T("card", "2025-01-03", "X", "5.00", OUT, "A"), T("card", "2025-01-03", "X", "5.00", OUT, "B")]
    reconcile(txns, ACCOUNTS)
    first = [t.duplicate_of for t in txns]
    reconcile(txns, ACCOUNTS)
    assert [t.duplicate_of for t in txns] == first


def test_user_approved_row_is_the_one_kept():
    a = T("card", "2025-01-03", "X", "5.00", OUT, "A")
    b = T("card", "2025-01-03", "X", "5.00", OUT, "B", status=Status.APPROVED, source=Source.USER)
    reconcile([a, b], ACCOUNTS)
    assert b.duplicate_of is None and a.duplicate_of == b.id


# --------------------------------------------------------------------------- transfers

def test_card_payment_pair_matched_by_amount_and_date():
    out = T("chk", "2025-02-01", "ONLINE PAYMENT 123 TO SAMPLE CARD", "300.00", OUT)
    pay = T("card", "2025-02-04", "PAYMENT THANK YOU - WEB", "300.00", IN)
    rep = reconcile([out, pay], ACCOUNTS)
    for t in (out, pay):
        assert t.lane == Lane.TRANSFER and t.category == "Credit Card Payment"
        assert t.confidence == 0.95 and t.status == Status.AUTO and t.source == Source.HEURISTIC
        assert t.schedule_c is None
    assert out.transfer_group == pay.transfer_group is not None
    assert rep.transfers == 2


def test_amount_only_pair_without_keywords():
    out = T("chk", "2025-02-01", "SAMPLE BANK ACH 998877", "123.45", OUT)
    pay = T("card", "2025-02-06", "ACH RECEIVED", "123.45", IN)
    reconcile([out, pay], ACCOUNTS)
    assert out.transfer_group and out.transfer_group == pay.transfer_group


def test_outside_window_not_paired_but_keywords_still_mark_transfer():
    out = T("chk", "2025-02-01", "PAYMENT TO CHASE CARD ENDING IN 1111", "300.00", OUT)
    pay = T("card", "2025-02-20", "PAYMENT THANK YOU", "300.00", IN)
    reconcile([out, pay], ACCOUNTS)
    assert out.transfer_group is None and pay.transfer_group is None
    for t in (out, pay):
        assert t.lane == Lane.TRANSFER and t.category == "Credit Card Payment" and t.confidence == 0.8


def test_bank_to_bank_transfer():
    o = T("chk", "2025-03-01", "ONLINE TRANSFER TO SAVINGS XXXXXX0002", "1000.00", OUT)
    i = T("sav", "2025-03-01", "ONLINE TRANSFER FROM CHECKING XXXXXX0001", "1000.00", IN)
    reconcile([o, i], ACCOUNTS)
    assert o.category == i.category == "Transfer Between Accounts"
    assert o.transfer_group == i.transfer_group is not None


def test_bank_to_bank_needs_keyword():
    # same amount same day, no transfer words → a bill and a client payment, not a transfer
    o = T("chk", "2025-03-01", "LANDLORD RENT", "1000.00", OUT)
    i = T("sav", "2025-03-01", "CLIENT ALPHA", "1000.00", IN)
    reconcile([o, i], ACCOUNTS)
    assert o.transfer_group is None and i.transfer_group is None
    assert i.lane == Lane.INCOME


def test_bill_pay_online_payment_is_not_card_payment():
    o = T("chk", "2025-03-01", "ONLINE PAYMENT 555 TO SAMPLE UTILITY", "80.00", OUT)
    reconcile([o], ACCOUNTS)
    assert o.lane != Lane.TRANSFER


def test_zelle_is_not_a_transfer():
    o = T("chk", "2025-03-01", "ZELLE TRANSFER TO SAMPLE PERSON", "80.00", OUT)
    reconcile([o], ACCOUNTS)
    assert o.lane != Lane.TRANSFER


def test_each_leg_used_once():
    o1 = T("chk", "2025-02-01", "CARDMEMBER SERV WEB PYMT", "50.00", OUT, seq=0)
    o2 = T("chk", "2025-02-02", "CARDMEMBER SERV WEB PYMT", "50.00", OUT, seq=1)
    p = T("card", "2025-02-02", "PAYMENT THANK YOU", "50.00", IN)
    reconcile([o1, o2, p], ACCOUNTS)
    grouped = [t for t in (o1, o2) if t.transfer_group]
    assert len(grouped) == 1 and grouped[0].transfer_group == p.transfer_group
    other = o1 if grouped[0] is o2 else o2
    assert other.lane == Lane.TRANSFER and other.confidence == 0.8   # keyword-only


def test_user_categorized_rows_untouched():
    o = T("chk", "2025-02-01", "PAYMENT TO CHASE CARD", "300.00", OUT, category="Contractor Payments",
          lane=Lane.BUSINESS, source=Source.USER, status=Status.APPROVED)
    reconcile([o], ACCOUNTS)
    assert o.category == "Contractor Payments" and o.lane == Lane.BUSINESS


# --------------------------------------------------------------------------- refunds

def test_refund_matched_to_purchase_inherits_category():
    p = T("card", "2025-04-01", "ACME OFFICE SUPPLY #12", "120.00", OUT, category="Office Supplies",
          lane=Lane.BUSINESS, schedule_c="L18", confidence=0.9, source=Source.RULE, status=Status.AUTO)
    r = T("card", "2025-04-09", "ACME OFFICE SUPPLY #99", "40.00", IN)
    rep = reconcile([p, r], ACCOUNTS)
    assert r.refund_of == p.id
    assert (r.category, r.lane, r.schedule_c) == ("Office Supplies", Lane.BUSINESS, "L18")
    assert rep.refunds == 1


def test_refund_picks_most_recent_prior_purchase_and_respects_amount():
    old = T("card", "2025-01-01", "LANTERN BOOKS", "30.00", OUT, seq=0)
    small = T("card", "2025-03-01", "LANTERN BOOKS", "10.00", OUT, seq=1)
    recent = T("card", "2025-03-05", "LANTERN BOOKS", "30.00", OUT, seq=2)
    later = T("card", "2025-03-20", "LANTERN BOOKS", "50.00", OUT, seq=3)   # after the refund
    r = T("card", "2025-03-10", "LANTERN BOOKS", "25.00", IN)
    reconcile([old, small, recent, later, r], ACCOUNTS)
    assert r.refund_of == recent.id


def test_refund_window_120_days():
    p = T("card", "2025-01-01", "LANTERN BOOKS", "30.00", OUT)
    r = T("card", "2025-06-15", "LANTERN BOOKS", "30.00", IN)
    reconcile([p, r], ACCOUNTS)
    assert r.refund_of is None and r.category == "Refund"


def test_unmatched_card_credit_goes_to_review():
    r = T("card", "2025-04-09", "MYSTERY MERCHANT CREDIT", "15.00", IN)
    rep = reconcile([r], ACCOUNTS)
    assert r.category == "Refund" and r.lane == Lane.REVIEW and r.status == Status.REVIEW
    assert rep.refunds == 0 and any("could not be matched" in n for n in rep.notes)


def test_refund_category_resynced_after_purchase_categorized():
    p = T("card", "2025-04-01", "ACME OFFICE SUPPLY", "120.00", OUT)
    r = T("card", "2025-04-09", "ACME OFFICE SUPPLY", "40.00", IN)
    reconcile([p, r], ACCOUNTS)
    assert r.refund_of == p.id and r.status == Status.REVIEW
    p.category, p.lane, p.schedule_c, p.confidence = "Office Supplies", Lane.BUSINESS, "L18", 0.9
    reconcile([p, r], ACCOUNTS)
    assert r.category == "Office Supplies" and r.lane == Lane.BUSINESS


def test_debit_card_refund_on_checking_is_not_income():
    p = T("chk", "2025-04-01", "PURCHASE AUTHORIZED ON 03/30 HARBOR HARDWARE PORTLAND ME", "64.20", OUT)
    r = T("chk", "2025-04-05", "PURCHASE RETURN AUTHORIZED ON 04/04 HARBOR HARDWARE PORTLAND ME", "64.20", IN)
    reconcile([p, r], ACCOUNTS)
    assert r.refund_of == p.id and r.lane != Lane.INCOME


# --------------------------------------------------------------------------- income

def test_checking_deposit_is_income_for_review():
    d = T("chk", "2025-05-01", "MOBILE DEPOSIT REF 1", "900.00", IN)
    rep = reconcile([d], ACCOUNTS)
    assert d.category == "Income & Deposits" and d.lane == Lane.INCOME
    assert d.confidence == 0.6 and d.status == Status.REVIEW and d.source == Source.HEURISTIC
    assert isinstance(rep, ReconcileReport)


def test_duplicates_excluded_from_matching():
    out_a = T("chk", "2025-02-01", "PAYMENT TO CHASE CARD", "300.00", OUT, "A")
    out_b = T("chk", "2025-02-01", "PAYMENT TO CHASE CARD", "300.00", OUT, "B")
    pay = T("card", "2025-02-02", "PAYMENT THANK YOU", "300.00", IN, "C")
    rep = reconcile([out_a, out_b, pay], ACCOUNTS)
    assert rep.duplicates == 1
    dup = out_a if out_a.duplicate_of else out_b
    kept = out_b if dup is out_a else out_a
    assert kept.transfer_group == pay.transfer_group is not None
    assert dup.transfer_group is None


def test_unknown_account_treated_as_bank():
    d = T("mystery", "2025-05-01", "DEPOSIT", "10.00", IN)
    reconcile([d], {})
    assert d.lane == Lane.INCOME


def test_csv_and_pdf_of_same_account_dedupe_on_merchant():
    csv_row = T("chk", "2025-12-15", "Online Realtime Transfer To Sample-0002 Transaction#: 1 Reference#: 9", "50.00", OUT, "csv")
    pdf_row = T("chk", "2025-12-15", "Online Realtime Transfer To Sample-0002 Transaction#: 1 Reference#: 9 ABC", "50.00", OUT, "pdf")
    assert csv_row.merchant == pdf_row.merchant
    assert reconcile([csv_row, pdf_row], ACCOUNTS).duplicates == 1


def test_refund_prefers_the_purchase_it_exactly_matches():
    # Marketplace descriptors normalize to one merchant ("AMZN MKTP"); the refund belongs to
    # the order with the same amount / order id, not simply the most recent purchase
    older = T("card", "2025-04-22", "AMZN MKTP US*KCEDMB30E AMZN.COM/BILL WA", "118.27", OUT, seq=0)
    newer = T("card", "2025-04-26", "AMZN MKTP US*QNLMEAL94 AMZN.COM/BILL WA", "126.95", OUT, seq=1)
    r1 = T("card", "2025-05-02", "AMZN MKTP US*KCEDMB30E AMZN.COM/BILL WA", "118.27", IN, seq=2)
    r2 = T("card", "2025-05-12", "AMZN MKTP US*QNLMEAL94 AMZN.COM/BILL WA", "126.95", IN, seq=3)
    partial = T("card", "2025-05-20", "AMZN MKTP US*KCEDMB30E AMZN.COM/BILL WA", "10.00", IN, seq=4)
    rep = reconcile([older, newer, r1, r2, partial], ACCOUNTS)
    assert r1.refund_of == older.id and r2.refund_of == newer.id
    assert rep.refunds == 2 and partial.refund_of is None       # nothing left on either order


def test_partial_refund_matches_same_order_descriptor():
    a = T("card", "2025-01-05", "AMZN MKTP US*0Z3DDMJKW AMZN.COM/BILL WA", "90.32", OUT, seq=0)
    b = T("card", "2025-01-15", "AMZN MKTP US*54FX44Z2E AMZN.COM/BILL WA", "97.99", OUT, seq=1)
    r = T("card", "2025-01-23", "AMZN MKTP US*0Z3DDMJKW AMZN.COM/BILL WA", "68.64", IN, seq=2)
    reconcile([a, b, r], ACCOUNTS)
    assert r.refund_of == a.id
