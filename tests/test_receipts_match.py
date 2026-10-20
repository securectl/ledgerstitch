# SPDX-License-Identifier: AGPL-3.0-only
"""Receipt -> transaction matching (synthetic data)."""
from datetime import date
from decimal import Decimal as D

from ledgerstitch.ingest.normalize import normalize_merchant
from ledgerstitch.models import Account, AccountKind, Direction, Transaction
from ledgerstitch.receipts import (ReceiptFields, ReceiptRecord, decide, match_all, match_receipt,
                                   merchant_similarity, rematch_records)
from ledgerstitch.receipts.match import MATCH_THRESHOLD

ACCOUNTS = {
    "chase-4012": Account("chase-4012", "Chase ••4012", "Chase", AccountKind.CREDIT_CARD, "4012"),
    "citi-9876": Account("citi-9876", "Citi ••9876", "Citi", AccountKind.CREDIT_CARD, "9876"),
    "boa-chk": Account("boa-chk", "BofA checking", "Bank of America", AccountKind.CHECKING, None),
}


def T(acct, d, desc, amt, direction=Direction.OUT, card=None, seq=0, dup=None):
    t = Transaction(account_id=acct, date=d, description=desc, amount=D(amt), direction=direction,
                    merchant=normalize_merchant(desc), card_last4=card, seq=seq)
    t.duplicate_of = dup
    return t


def R(**kw):
    kw.setdefault("payment_method", "card")
    return ReceiptFields(**kw)


def test_exact_amount_and_card_is_matched():
    txns = [T("chase-4012", date(2025, 3, 15), "WHOLEFDS SFO 10234 SAN FRANCISCO CA", "11.47"),
            T("chase-4012", date(2025, 3, 15), "SHELL OIL 57444", "42.70"),
            T("citi-9876", date(2025, 3, 14), "WHOLE FOODS", "11.47")]
    f = R(merchant="Whole Foods Market", date=date(2025, 3, 14), total=D("11.47"), card_last4="4012")
    cands = match_receipt(f, txns, ACCOUNTS)
    status, best = decide(cands)
    assert status == "matched" and best.txn_id == txns[0].id
    assert any("••4012 matches" in r for r in best.reasons) and any("amount" in r for r in best.reasons)
    # the other card's same-amount, same-merchant txn is pushed down by the last4 mismatch
    other = next(c for c in cands if c.txn_id == txns[2].id)
    assert other.score < 0.45
    assert any("mismatch" in r for r in other.reasons)


def test_tip_window_restaurant():
    # receipt printed before tip (68.48); card settled at 82.18 (20% tip)
    txns = [T("chase-4012", date(2025, 1, 23), "TST* OSTERIA LUCA BOSTON MA", "82.18"),
            T("chase-4012", date(2025, 1, 23), "CVS/PHARMACY #1234", "68.48")]
    f = R(merchant="OSTERIA LUCA", date=date(2025, 1, 22), total=D("68.48"), card_last4="4012")
    cands = match_receipt(f, txns, ACCOUNTS)
    status, best = decide(cands)
    # an exact-amount charge at a different merchant ranks below the tipped restaurant charge
    assert best.txn_id == txns[0].id and status in ("matched", "suggested")
    assert any("tip window" in r for r in best.reasons)
    status, best = decide(match_receipt(f, txns[:1], ACCOUNTS))
    assert status == "matched" and best.txn_id == txns[0].id


def test_tip_window_upper_bound():
    txns = [T("chase-4012", date(2025, 1, 22), "TST* OSTERIA LUCA", "95.00")]   # +39%: not a tip
    f = R(merchant="OSTERIA LUCA", date=date(2025, 1, 22), total=D("68.48"))
    cands = match_receipt(f, txns, ACCOUNTS)
    assert decide(cands)[0] != "matched"
    assert not any("tip window" in r for c in cands for r in c.reasons)


def test_total_plus_tip():
    txns = [T("chase-4012", date(2025, 2, 8), "TST* JOES DINER AUSTIN TX", "48.59")]
    f = R(merchant="JOE'S DINER", date=date(2025, 2, 7), total=D("40.59"), tip=D("8.00"), card_last4="4012")
    status, best = decide(match_receipt(f, txns, ACCOUNTS))
    assert status == "matched" and any("total + tip" in r for r in best.reasons)


def test_tip_window_not_used_when_receipt_has_tip_line():
    txns = [T("boa-chk", date(2025, 2, 8), "TST* JOES DINER", "50.00")]
    f = R(merchant="JOE'S DINER", date=date(2025, 2, 7), total=D("48.59"), tip=D("8.00"))
    cands = match_receipt(f, txns, ACCOUNTS)
    assert not any("tip window" in r for c in cands for r in c.reasons)


def test_posting_lag():
    txns = [T("chase-4012", date(2025, 6, 21), "THE HOME DEPOT #6512 ATLANTA GA", "62.06")]
    f = R(merchant="The Home Depot", date=date(2025, 6, 18), total=D("62.06"), card_last4="4012")
    status, best = decide(match_receipt(f, txns, ACCOUNTS))
    assert status == "matched" and "posted 3 days later" in best.reasons


def test_far_date_excluded_without_card():
    txns = [T("boa-chk", date(2025, 8, 1), "THE HOME DEPOT", "62.06")]
    f = R(merchant="The Home Depot", date=date(2025, 6, 18), total=D("62.06"))
    assert match_receipt(f, txns, ACCOUNTS) == []


def test_far_date_kept_when_amount_and_card_match():
    txns = [T("chase-4012", date(2025, 7, 20), "AMAZON MKTPL*BJ1M70883", "48.71")]
    f = R(merchant="Amazon", date=date(2025, 6, 18), total=D("48.71"), card_last4="4012")
    cands = match_receipt(f, txns, ACCOUNTS)
    assert cands and "date off by 32 days" in cands[0].reasons


def test_two_cards_same_amount_resolved_by_last4():
    txns = [T("chase-4012", date(2025, 4, 3), "SHELL OIL 57444", "42.70"),
            T("citi-9876", date(2025, 4, 3), "SHELL OIL 57444", "42.70")]
    f = R(merchant="Shell", date=date(2025, 4, 3), total=D("42.70"), card_last4="9876")
    status, best = decide(match_receipt(f, txns, ACCOUNTS))
    assert status == "matched" and best.txn_id == txns[1].id


def test_two_cards_same_amount_without_last4_is_only_suggested():
    txns = [T("chase-4012", date(2025, 4, 3), "SHELL OIL 57444", "42.70"),
            T("citi-9876", date(2025, 4, 3), "SHELL OIL 57444", "42.70")]
    f = R(merchant="Shell", date=date(2025, 4, 3), total=D("42.70"))
    status, best = decide(match_receipt(f, txns, ACCOUNTS))
    assert status == "suggested"


def test_authorized_user_card_via_txn_card_last4():
    # one Chase account (••4012) with an authorized-user card ••5521 on the same statement
    txns = [T("chase-4012", date(2025, 2, 7), "STARBUCKS STORE 1234", "6.45", card="4012"),
            T("chase-4012", date(2025, 2, 7), "STARBUCKS STORE 1234", "6.45", card="5521", seq=1)]
    f = R(merchant="Starbucks", date=date(2025, 2, 7), total=D("6.45"), card_last4="5521")
    cands = match_receipt(f, txns, ACCOUNTS)
    status, best = decide(cands)
    assert status == "matched" and best.txn_id == txns[1].id
    assert "card ••5521 matches" in best.reasons
    loser = next(c for c in cands if c.txn_id == txns[0].id) if len(cands) > 1 else None
    assert loser is None or loser.score < best.score - 0.1


def test_mismatched_last4_rejected():
    txns = [T("chase-4012", date(2025, 3, 14), "WHOLE FOODS MARKET", "11.47")]
    f = R(merchant="Whole Foods Market", date=date(2025, 3, 14), total=D("11.47"), card_last4="7777")
    status, best = decide(match_receipt(f, txns, ACCOUNTS))
    assert status == "unmatched" and best is None


def test_wallet_device_number_is_soft_mismatch():
    txns = [T("chase-4012", date(2025, 3, 14), "WHOLE FOODS MARKET", "11.47")]
    f = R(merchant="Whole Foods Market", date=date(2025, 3, 14), total=D("11.47"), card_last4="7777",
          wallet="APPLE PAY")
    status, best = decide(match_receipt(f, txns, ACCOUNTS))
    assert status in ("matched", "suggested") and best.txn_id == txns[0].id


def test_unknown_account_last4_is_neutral():
    txns = [T("boa-chk", date(2025, 3, 14), "DEBIT CARD PURCHASE WHOLE FOODS", "11.47")]
    f = R(merchant="Whole Foods Market", date=date(2025, 3, 14), total=D("11.47"), card_last4="3388")
    status, best = decide(match_receipt(f, txns, ACCOUNTS))
    assert status == "matched"
    assert not any("mismatch" in r for r in best.reasons)


def test_only_outflows_and_non_duplicates():
    keep = T("chase-4012", date(2025, 3, 14), "WHOLE FOODS", "11.47")
    txns = [keep,
            T("chase-4012", date(2025, 3, 14), "WHOLE FOODS", "11.47", direction=Direction.IN, seq=1),
            T("chase-4012", date(2025, 3, 14), "WHOLE FOODS", "11.47", seq=2, dup=keep.id)]
    cands = match_receipt(R(merchant="Whole Foods", date=date(2025, 3, 14), total=D("11.47")), txns, ACCOUNTS)
    assert [c.txn_id for c in cands] == [keep.id]


def test_top5_sorted():
    txns = [T("boa-chk", date(2025, 5, 1), f"COFFEE SHOP {i}", "5.00", seq=i) for i in range(9)]
    cands = match_receipt(R(merchant="Coffee Shop", date=date(2025, 5, 1), total=D("5.00")), txns, ACCOUNTS)
    assert len(cands) == 5
    assert all(a.score >= b.score for a, b in zip(cands, cands[1:]))


def test_decide_margin_rule():
    from ledgerstitch.receipts import Candidate
    assert decide([Candidate("a", 0.9), Candidate("b", 0.85)])[0] == "suggested"
    assert decide([Candidate("a", 0.9, ["same day"]), Candidate("b", 0.79)])[0] == "matched"
    assert decide([Candidate("a", 0.9), Candidate("b", 0.79)])[0] == "suggested"   # no date evidence
    assert decide([Candidate("a", 0.5)])[0] == "suggested"
    assert decide([Candidate("a", 0.3)]) == ("unmatched", None)
    assert decide([]) == ("unmatched", None)


def test_match_all_conflict_resolution():
    # two identical coffee receipts, two identical txns on different days
    t1 = T("chase-4012", date(2025, 5, 1), "STARBUCKS STORE 1", "6.45")
    t2 = T("chase-4012", date(2025, 5, 2), "STARBUCKS STORE 1", "6.45")
    r1 = R(merchant="Starbucks", date=date(2025, 5, 1), total=D("6.45"), card_last4="4012")
    r2 = R(merchant="Starbucks", date=date(2025, 5, 2), total=D("6.45"), card_last4="4012")
    out = match_all([r1, r2], [t1, t2], ACCOUNTS)
    assert out[0].best.txn_id == t1.id and out[1].best.txn_id == t2.id
    assert out[0].best.txn_id != out[1].best.txn_id
    # each receipt's list no longer offers the other's transaction
    assert all(c.txn_id != t2.id for c in out[0].candidates)


def test_match_all_greedy_gives_contested_txn_to_best_receipt():
    t = T("chase-4012", date(2025, 5, 1), "SHELL OIL 57444", "40.00")
    strong = R(merchant="Shell", date=date(2025, 5, 1), total=D("40.00"), card_last4="4012")
    weak = R(merchant="Shell", date=date(2025, 5, 3), total=D("40.00"))
    out = match_all([weak, strong], [t], ACCOUNTS)
    assert out[1].status == "matched" and out[1].best.txn_id == t.id
    assert out[0].status == "unmatched" and out[0].best is None


def test_match_all_respects_reserved():
    t = T("chase-4012", date(2025, 5, 1), "SHELL OIL 57444", "40.00")
    r = R(merchant="Shell", date=date(2025, 5, 1), total=D("40.00"), card_last4="4012")
    assert match_all([r], [t], ACCOUNTS, reserved={t.id})[0].status == "unmatched"


def test_merchant_similarity():
    assert merchant_similarity("BLUEBOTTLECOFFEE", "SQ *BLUE BOTTLE COFFEE") > 0.8
    assert merchant_similarity("The Home Depot", "THE HOME DEPOT #6512") == 1.0
    assert merchant_similarity("Amazon", "AMZN Mktp US*2K4LD0") == 1.0
    assert merchant_similarity("Shell", "STARBUCKS") < 0.3


def test_record_roundtrip_and_rematch():
    t = T("chase-4012", date(2025, 3, 14), "WHOLE FOODS", "11.47")
    rec = ReceiptRecord(id="abc", filename="r.jpg", mime="image/jpeg", added="2025-03-14T00:00:00+00:00",
                        size=10, merchant="Whole Foods Market", date=date(2025, 3, 14), total=D("11.47"),
                        card_last4="4012", payment_method="card")
    rec2 = ReceiptRecord.from_dict(rec.to_dict() | {"unknown_future_key": 1})
    assert rec2 == rec
    assert "text" not in rec.to_dict()
    rematch_records([rec2], [t], ACCOUNTS)
    assert rec2.status == "matched" and rec2.txn_id == t.id and rec2.candidates[0]["txn_id"] == t.id
    manual = ReceiptRecord.from_dict(rec.to_dict() | {"id": "m", "status": "manual", "txn_id": t.id})
    other = ReceiptRecord.from_dict(rec.to_dict() | {"id": "o"})
    rematch_records([manual, other], [t], ACCOUNTS)
    assert manual.txn_id == t.id and other.status == "unmatched"


# ---------------------------------------------------------------- card_last4 from statement CSVs

def _parse_fixture(name):
    from pathlib import Path
    from ledgerstitch.ingest.csv_parser import parse_csv
    from ledgerstitch.ingest.detect import detect
    data = (Path(__file__).parent / "fixtures" / name).read_bytes()
    prof = detect(name, data)
    return prof, parse_csv(data, prof, "acct", "imp")


def test_chase_card_csv_carries_card_last4():
    prof, txns = _parse_fixture("chase_card.csv")
    assert prof.mapping.card == "Card"
    assert txns and all(t.card_last4 == "1111" for t in txns)


def test_capone_card_csv_carries_card_last4():
    prof, txns = _parse_fixture("capone_card.csv")
    assert prof.mapping.card == "Card No."
    assert txns and all(t.card_last4 == "2222" for t in txns)


def test_chase_card_without_card_column_and_stable_ids():
    from ledgerstitch.models import make_txn_id
    prof, txns = _parse_fixture("chase_card_nocard.csv")
    assert prof.mapping.card is None and all(t.card_last4 is None for t in txns)
    _, txns = _parse_fixture("chase_card.csv")
    t = txns[0]
    # the id does not depend on card_last4
    assert t.id == make_txn_id(t.account_id, t.date, t.description, t.amount, t.direction, t.seq)


def test_transaction_dict_roundtrip_with_card_last4():
    t = T("chase-4012", date(2025, 2, 7), "STARBUCKS", "6.45", card="5521")
    d = t.to_dict()
    assert d["card_last4"] == "5521"
    assert Transaction.from_dict(d) == t
    d.pop("card_last4")                       # records saved before this field existed
    assert Transaction.from_dict(d).card_last4 is None


def test_other_merchant_on_same_card_is_not_suggested_without_amount_evidence():
    """Regression: a coffee receipt ($10.60) must not be 'suggested' against a $12.99
    grocery charge on the same card just because of the tip window + card + date."""
    acct = Account("chase-4012", "Chase ••4012", "Chase", AccountKind.CREDIT_CARD, "4012")
    grocery = Transaction("chase-4012", date(2025, 1, 14), "INDOPAK SUPERMARKET", D("12.99"), Direction.OUT,
                          merchant=normalize_merchant("INDOPAK SUPERMARKET"))
    f = ReceiptFields(merchant="BLUE BOTTLE COFFEE", date=date(2025, 1, 15), total=D("10.60"),
                      card_last4="4012")
    status, best = decide(match_receipt(f, [grocery], {acct.id: acct}))
    assert status == "unmatched"


def test_tip_window_still_works_for_the_same_restaurant():
    acct = Account("chase-4012", "Chase ••4012", "Chase", AccountKind.CREDIT_CARD, "4012")
    dinner = Transaction("chase-4012", date(2025, 2, 8), "TST* JOES DINER", D("50.00"), Direction.OUT,
                         merchant=normalize_merchant("TST* JOES DINER"))
    f = ReceiptFields(merchant="JOE'S DINER", date=date(2025, 2, 7), total=D("43.81"), card_last4="4012")
    status, best = decide(match_receipt(f, [dinner], {acct.id: acct}))
    assert status in ("matched", "suggested") and best.txn_id == dinner.id


def test_no_auto_match_without_date_agreement():
    """Exact amount + card but a date far off must not auto-link."""
    acct = Account("chase-4012", "Chase ••4012", "Chase", AccountKind.CREDIT_CARD, "4012")
    t = Transaction("chase-4012", date(2025, 3, 30), "BLUE BOTTLE COFFEE", D("10.60"), Direction.OUT,
                    merchant=normalize_merchant("BLUE BOTTLE COFFEE"))
    f = ReceiptFields(merchant="BLUE BOTTLE COFFEE", date=date(2025, 1, 15), total=D("10.60"), card_last4="4012")
    status, best = decide(match_receipt(f, [t], {acct.id: acct}))
    assert status != "matched"
