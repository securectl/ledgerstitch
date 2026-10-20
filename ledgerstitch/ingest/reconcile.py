# SPDX-License-Identifier: AGPL-3.0-only
"""Cross-file reconciliation: duplicates, transfers/card payments, refunds, income.

`reconcile(txns, accounts)` mutates transactions in place and is safe to
re-run (e.g. after every import and again after categorization):

1. **Duplicates (overlapping imports).** Rows of the same account with the
   same date, amount, direction and normalized description are compared as a
   *multiset per import*: if import A has 2 identical rows and import B
   (an overlapping re-export) has 2 as well, 2 are kept and 2 are marked
   `duplicate_of`. Repeated rows within a single file are never collapsed.
2. **Transfers / card payments.** An "out" on a bank account paired with an
   "in" on a credit-card account (or another bank account) with the same
   amount within ±6 days → lane TRANSFER, category "Credit Card Payment" /
   "Transfer Between Accounts", shared `transfer_group`, confidence 0.95.
   Unpaired rows whose description carries a payment/transfer keyword get
   the same treatment with confidence 0.8 and no group.
3. **Refunds.** An "in" on a card account that is not a payment is matched
   to the most recent earlier "out" on the same account with the same
   merchant (≤120 days, amount ≤ remaining purchase amount): `refund_of` is
   set and category/lane/schedule_c are copied so it nets against the
   purchase. Unmatched refunds → category "Refund", lane REVIEW (the user
   decides). Debit-card refunds on bank accounts are matched the same way.
4. **Income.** Any other "in" on a bank account → "Income & Deposits", lane
   INCOME, confidence 0.6, status REVIEW.

Transactions the user set (source USER / RULE or status APPROVED) are never
re-categorized; duplicates are still detected for them.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from ..models import Account, AccountKind, Direction, Lane, Source, Status, Transaction
from ..taxonomy import CATEGORY_BY_NAME, schedule_c_for
from .normalize import clean_description, normalize_merchant

__all__ = ["ReconcileReport", "reconcile"]

CC_PAYMENT = "Credit Card Payment"
TRANSFER = "Transfer Between Accounts"
REFUND = "Refund"
INCOME = "Income & Deposits"

TRANSFER_WINDOW = timedelta(days=6)
REFUND_WINDOW = timedelta(days=120)

_CC_KEYWORDS = tuple(CATEGORY_BY_NAME[CC_PAYMENT].keywords) + (
    "PAYMENT - THANK YOU", "PAYMENT THANK", "THANK YOU", "CAPITAL ONE MOBILE PYMT", "CAPITAL ONE ONLINE PYMT",
    "AMEX EPAYMENT", "APPLECARD GSBANK", "CHASE CREDIT CRD", "CITI CARD", "DISCOVER CARD", "BK OF AMER VISA",
    "WF CREDIT CARD", "PAYMENT TRANSACTION", "E-PAYMENT", "EPAYMENT", "CRCARDPMT")
_XFER_KEYWORDS = tuple(CATEGORY_BY_NAME[TRANSFER].keywords) + (
    "ONLINE TRANSFER", "INTERNAL TRANSFER", "ACCOUNT TRANSFER", "XFER", "FUNDS TRANSFER")
# Generic payment words that also appear on ordinary bill pay; on bank
# accounts they only count with a card-issuer word nearby.
_GENERIC_PAY = {"ONLINE PAYMENT", "MOBILE PAYMENT", "INTERNET PAYMENT", "AUTOPAY", "AUTOMATIC PAYMENT",
                "THANK YOU", "E-PAYMENT", "EPAYMENT"}
_ISSUER_RE = re.compile(r"CARD|CRD|CREDIT|CHASE|CITI|AMEX|AMERICAN EXPRESS|DISCOVER|CAPITAL ONE|BARCLAY|"
                        r"SYNCHRONY|GSBANK|APPLECARD|BK OF AMER|BANK OF AMERICA|WF CREDIT|GEMINI|CARDMEMBER")
_P2P_RE = re.compile(r"ZELLE|VENMO|CASH APP|PAYPAL|SQUARE CASH|APPLE CASH")
_CARD_PAY_RE = re.compile(r"\bPAYMENT\b|\bPYMT\b|\bPMT\b|AUTOPAY|THANK YOU|PAYMENT_TRANSACTION")


@dataclass
class ReconcileReport:
    duplicates: int                  # rows marked duplicate_of
    transfers: int                   # rows placed in the TRANSFER lane (both legs counted)
    refunds: int                     # refunds linked to their purchase (refund_of set)
    notes: list[str] = field(default_factory=list)


def _locked(t: Transaction) -> bool:
    return t.source in (Source.USER, Source.RULE) or t.status == Status.APPROVED


def _kind(t: Transaction, accounts: dict[str, Account]) -> AccountKind:
    a = accounts.get(t.account_id)
    return a.kind if a else AccountKind.OTHER


def _is_card(t, accounts) -> bool:
    return _kind(t, accounts) == AccountKind.CREDIT_CARD


def _text(t: Transaction) -> str:
    return clean_description(t.description) + " | " + (t.merchant or normalize_merchant(t.description))


def _keyword_category(t: Transaction, accounts) -> str | None:
    """Payment/transfer category suggested by the description alone."""
    s = _text(t)
    card = _is_card(t, accounts)
    if card and t.direction == Direction.IN and (_CARD_PAY_RE.search(s) or any(k in s for k in _CC_KEYWORDS)):
        return CC_PAYMENT
    if card:
        return None
    if _P2P_RE.search(s):
        return None
    for k in _CC_KEYWORDS:
        if k in s and (k not in _GENERIC_PAY or _ISSUER_RE.search(s.replace(k, ""))):
            return CC_PAYMENT
    for k in _XFER_KEYWORDS:
        if k in s:
            return TRANSFER
    return None


def _preset_transfer(t: Transaction) -> bool:
    """A leg the parser already knows is a transfer (payment-platform payout,
    withdrawal to the bank, Venmo funding leg): pair it even when the other side
    names PayPal/Venmo/Square, which normally blocks bank→bank pairing."""
    return t.lane == Lane.TRANSFER and t.category == TRANSFER and t.transfer_group is None


def _group_id(a: Transaction, b: Transaction) -> str:
    x, y = sorted((a.id, b.id))
    return "tg-" + hashlib.sha256(f"{x}|{y}".encode()).hexdigest()[:12]


def _set_transfer(t: Transaction, category: str, conf: float, group: str | None) -> None:
    t.category = category
    t.lane = Lane.TRANSFER
    t.schedule_c = None
    t.transfer_group = group
    t.confidence = conf
    t.source = Source.HEURISTIC
    t.status = Status.AUTO


# --------------------------------------------------------------------------- 1. duplicates

def _mark_duplicates(txns: list[Transaction]) -> int:
    for t in txns:
        t.duplicate_of = None
    groups: dict[tuple, list[tuple[int, Transaction]]] = defaultdict(list)
    for i, t in enumerate(txns):
        # normalized merchant (not raw text): the same row exported as CSV and as
        # PDF wraps/reformats the description but normalizes to the same key.
        key = (t.account_id, t.date, t.amount, t.direction,
               t.merchant or normalize_merchant(t.description) or clean_description(t.description))
        groups[key].append((i, t))
    marked = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        per_import: dict[str, int] = defaultdict(int)
        for _, t in members:
            per_import[t.import_id] += 1
        keep_n = max(per_import.values())
        if keep_n >= len(members):
            continue
        # prefer rows the user touched / already linked, then the earliest
        ranked = sorted(members, key=lambda it: (
            not _locked(it[1]), it[1].transfer_group is None and it[1].refund_of is None, it[0]))
        kept = [t for _, t in ranked[:keep_n]]
        for _, t in ranked[keep_n:]:
            target = next((k for k in kept if k.import_id != t.import_id), kept[0])
            t.duplicate_of = target.id
            marked += 1
    return marked


# --------------------------------------------------------------------------- 2. transfers

def _match_transfers(live: list[Transaction], accounts) -> tuple[int, int]:
    """Pair legs. Returns (rows in pairs, keyword-only rows)."""
    free = [t for t in live if t.transfer_group is None and t.refund_of is None and not _locked(t)]
    outs = [t for t in free if t.direction == Direction.OUT and not _is_card(t, accounts)]
    ins = [t for t in free if t.direction == Direction.IN]
    # purchases per card account+merchant to avoid treating a refund as a payment
    card_purchases = defaultdict(list)
    for t in live:
        if t.direction == Direction.OUT and _is_card(t, accounts):
            card_purchases[(t.account_id, t.merchant)].append(t)

    cands = []
    for o in outs:
        okw = _keyword_category(o, accounts) or (TRANSFER if _preset_transfer(o) else None)
        for i in ins:
            if i.account_id == o.account_id or i.amount != o.amount:
                continue
            gap = abs((i.date - o.date).days)
            if gap > TRANSFER_WINDOW.days:
                continue
            icard = _is_card(i, accounts)
            ikw = _keyword_category(i, accounts) or (TRANSFER if _preset_transfer(i) else None)
            preset = _preset_transfer(o) or _preset_transfer(i)
            if icard:
                looks_refund = bool(card_purchases.get((i.account_id, i.merchant))) and ikw is None
                if looks_refund and okw is None:
                    continue
            else:
                # bank→bank: require a transfer word on at least one side
                if okw is None and ikw is None:
                    continue
                if not preset and (_P2P_RE.search(_text(o)) or _P2P_RE.search(_text(i))):
                    continue
            score = (0 if (okw or ikw) else 1, gap, o.date, o.id, i.id)
            cands.append((score, o, i, icard))
    cands.sort(key=lambda c: c[0])
    used: set[str] = set()
    paired = 0
    for _, o, i, icard in cands:
        if o.id in used or i.id in used:
            continue
        used.update((o.id, i.id))
        cat = CC_PAYMENT if icard else TRANSFER
        g = _group_id(o, i)
        _set_transfer(o, cat, 0.95, g)
        _set_transfer(i, cat, 0.95, g)
        paired += 2
    single = 0
    for t in free:
        if t.id in used:
            continue
        cat = _keyword_category(t, accounts)
        if cat is None:
            continue
        if t.lane == Lane.TRANSFER and t.category == cat:
            continue
        if t.category and t.category != "Uncategorized" and t.confidence > 0.8:
            continue
        _set_transfer(t, cat, 0.8, None)
        single += 1
    return paired, single


# --------------------------------------------------------------------------- 3/4. refunds & income

def _same_merchant(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = a.split(), b.split()
    if len(ta) >= 2 and len(tb) >= 2 and ta[:2] == tb[:2]:
        return True
    return len(ta[0]) >= 5 and ta[0] == tb[0] and (len(ta) == 1 or len(tb) == 1)


def _copy_from_purchase(r: Transaction, p: Transaction) -> None:
    r.category = p.category
    r.lane = p.lane
    r.schedule_c = p.schedule_c
    r.source = Source.HEURISTIC
    if p.category and p.category != "Uncategorized":
        r.status = p.status if p.status != Status.APPROVED else Status.AUTO
        r.confidence = min(0.9, p.confidence or 0.9)
    else:
        r.status = Status.REVIEW
        r.confidence = 0.5


def _refunds_and_income(live: list[Transaction], accounts, notes: list[str]) -> tuple[int, int, int]:
    by_id = {t.id: t for t in live}
    remaining: dict[str, Decimal] = {}
    outs_by_acct = defaultdict(list)
    for t in live:
        if t.direction == Direction.OUT and t.lane != Lane.TRANSFER:
            outs_by_acct[t.account_id].append(t)
            remaining[t.id] = t.amount
    for lst in outs_by_acct.values():
        lst.sort(key=lambda t: (t.date, t.seq))
    # existing links consume purchase amounts; keep their categories in sync
    for t in live:
        if t.refund_of and t.refund_of in remaining:
            remaining[t.refund_of] -= t.amount
            if not _locked(t):
                _copy_from_purchase(t, by_id[t.refund_of])

    linked = unmatched = income = 0
    ins = sorted((t for t in live if t.direction == Direction.IN and t.transfer_group is None
                  and t.lane != Lane.TRANSFER and not t.refund_of), key=lambda t: (t.date, t.seq))
    for t in ins:
        if _locked(t):
            continue
        card = _is_card(t, accounts)
        merchant = t.merchant or normalize_merchant(t.description)
        cands = []
        for p in reversed(outs_by_acct.get(t.account_id, [])):
            if p.date > t.date or t.date - p.date > REFUND_WINDOW:
                continue
            if remaining.get(p.id, 0) < t.amount:
                continue
            if _same_merchant(merchant, p.merchant or normalize_merchant(p.description)):
                cands.append(p)
        # most recent purchase of the merchant, unless one matches the refund exactly
        # (same amount, or the same raw descriptor: "AMZN MKTP US*<order id>")
        raw = " ".join(t.description.split()).upper()
        match = next((p for p in cands if p.amount == t.amount), None) \
            or next((p for p in cands if " ".join(p.description.split()).upper() == raw), None) \
            or (cands[0] if cands else None)
        if match is not None:
            t.refund_of = match.id
            remaining[match.id] -= t.amount
            _copy_from_purchase(t, match)
            linked += 1
            continue
        if card:
            if t.category in (None, "Uncategorized", REFUND) or t.source in (Source.NONE, Source.HEURISTIC):
                t.category = REFUND
                t.lane = Lane.REVIEW
                t.schedule_c = None
                t.source = Source.HEURISTIC
                t.status = Status.REVIEW
                t.confidence = 0.5
                unmatched += 1
        else:
            if t.category in (None, "Uncategorized") or t.source == Source.NONE or (
                    t.source == Source.HEURISTIC and t.category == INCOME):
                t.category = INCOME
                t.lane = Lane.INCOME
                t.schedule_c = schedule_c_for(INCOME)
                t.source = Source.HEURISTIC
                t.status = Status.REVIEW
                t.confidence = 0.6
                income += 1
    if unmatched:
        notes.append(f"{unmatched} card credit(s) could not be matched to a purchase and need review")
    return linked, unmatched, income


# --------------------------------------------------------------------------- entry point

def reconcile(txns: list[Transaction], accounts: dict[str, Account]) -> ReconcileReport:
    """Mark duplicates, link transfers/card payments and refunds, flag income. Mutates `txns`."""
    notes: list[str] = []
    dups = _mark_duplicates(txns)
    if dups:
        notes.append(f"{dups} row(s) were already imported from an overlapping file and are excluded")
    live = [t for t in txns if t.duplicate_of is None]
    paired, single = _match_transfers(live, accounts)
    if paired:
        notes.append(f"{paired // 2} card payment/transfer pair(s) matched across accounts")
    if single:
        notes.append(f"{single} payment/transfer row(s) recognized by description only (other side not imported)")
    linked, unmatched, income = _refunds_and_income(live, accounts, notes)
    if income:
        notes.append(f"{income} deposit(s) marked as income — please confirm")
    transfers = sum(1 for t in live if t.lane == Lane.TRANSFER and t.category in (CC_PAYMENT, TRANSFER))
    return ReconcileReport(duplicates=dups, transfers=transfers, refunds=linked, notes=notes)
