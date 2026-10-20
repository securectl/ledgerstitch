# SPDX-License-Identifier: AGPL-3.0-only
"""Deterministic categorization: user/learned rules, then built-in keyword heuristics.

Nothing in this module talks to the network.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable, Optional

from ..models import Direction, Lane, Rule, Source, Status, Transaction
from ..taxonomy import CATEGORY_BY_NAME, DEFAULT_CATEGORIES, UNCATEGORIZED, Category

HEURISTIC_CONFIDENCE = 0.7
RULE_CONFIDENCE = 1.0
LOCKED_TRANSFER_CONFIDENCE = 0.9

_P2P_MARKERS = re.compile(r"(?<![A-Z0-9])(ZELLE|VENMO|CASH ?APP)(?![A-Z0-9])")


# ---------------------------------------------------------------- helpers
def is_locked(t: Transaction) -> bool:
    """True when automatic categorization must not touch this transaction."""
    if t.status == Status.APPROVED or t.source == Source.USER:
        return True
    if t.duplicate_of is not None:
        return True
    # Card payments / own-account transfers paired by reconcile. A linked
    # transfer_group is always kept so both legs keep netting to zero.
    if t.transfer_group is not None:
        return True
    if t.lane == Lane.TRANSFER and t.confidence >= LOCKED_TRANSFER_CONFIDENCE:
        return True
    return False


def _is_uncategorized(t: Transaction) -> bool:
    return not t.category or t.category == UNCATEGORIZED


def _schedule_c_for_lane(category: str, lane: Lane, explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit
    if lane in (Lane.BUSINESS, Lane.REVIEW):
        c = CATEGORY_BY_NAME.get(category)
        return c.schedule_c if c else None
    return None


# ---------------------------------------------------------------- rules
def _rule_text(t: Transaction, field: str) -> str:
    if field == "description":
        return t.description.upper()
    return (t.merchant or t.description).upper()


def _best_rule(t: Transaction, rules: list[Rule]) -> Optional[Rule]:
    best: Optional[Rule] = None
    best_key = None
    for r in rules:
        pat = (r.pattern or "").strip().upper()
        if not pat:
            continue
        if r.account_id is not None and r.account_id != t.account_id:
            continue
        if pat not in _rule_text(t, r.field):
            continue
        # account-scoped beats global; then longest pattern; then user-authored beats learned
        key = (r.account_id is not None, len(pat), r.created_by == Source.USER)
        if best_key is None or key > best_key:
            best, best_key = r, key
    return best


def apply_rules(txns: Iterable[Transaction], rules: list[Rule]) -> int:
    """Apply saved user/learned rules. Returns how many transactions matched."""
    rules = list(rules or [])
    if not rules:
        return 0
    n = 0
    for t in txns:
        if is_locked(t):
            continue
        r = _best_rule(t, rules)
        if r is None:
            continue
        lane = Lane(r.lane)
        t.category = r.category
        t.lane = lane
        t.schedule_c = _schedule_c_for_lane(r.category, lane, r.schedule_c)
        t.confidence = RULE_CONFIDENCE
        t.source = Source.RULE
        t.status = Status.REVIEW if lane == Lane.REVIEW else Status.AUTO
        n += 1
    return n


# ---------------------------------------------------------------- heuristics
@lru_cache(maxsize=1)
def _keyword_patterns() -> tuple[tuple[int, int, re.Pattern, Category], ...]:
    """(length, taxonomy order, compiled regex, category) for every keyword.

    Word boundaries: a keyword that starts with a letter/digit must not be
    preceded by one ("AWS" must not hit "LAWSON"). Short keywords (<=4 chars)
    and keywords written with a trailing space in the taxonomy ("UPS ", "QT ")
    must also end on a boundary (an optional plural S is allowed). Longer
    keywords may be prefixes ("AMERICAN AIR" -> "AMERICAN AIRLINES").
    """
    out = []
    for order, cat in enumerate(DEFAULT_CATEGORIES):
        for raw in cat.keywords:
            kw = raw.strip().upper()
            if not kw:
                continue
            body = re.escape(kw)
            pre = r"(?<![A-Z0-9])" if kw[0].isalnum() else ""
            post = ""
            if kw[-1].isalnum() and (len(kw) <= 4 or raw != raw.rstrip()):
                post = r"S?(?![A-Z0-9])"
            out.append((len(kw), order, re.compile(pre + body + post), cat))
    return tuple(out)


def match_heuristic(text: str, direction=None) -> Optional[Category]:
    """Longest-keyword-wins match over the taxonomy (avoids GAS vs GAS STATION bugs)."""
    text = " ".join((text or "").upper().split())
    if not text:
        return None
    best = None
    best_key = None
    for length, order, rx, cat in _keyword_patterns():
        if cat.lane == Lane.INCOME and direction is not None and getattr(direction, "value", direction) == "out":
            continue  # "SECURITY DEPOSIT" paid out is not income
        if rx.search(text):
            key = (length, -order)
            if best_key is None or key > best_key:
                best, best_key = cat, key
    # "ZELLE TRANSFER TO <PERSON>" is a P2P payment, not a move between own accounts.
    if best is not None and best.name == "Transfer Between Accounts" and _P2P_MARKERS.search(text):
        best = CATEGORY_BY_NAME["Zelle & P2P Payments"]
    return best


def apply_heuristics(txns: Iterable[Transaction]) -> int:
    """Built-in keyword rules for transactions nothing better has categorized yet."""
    n = 0
    for t in txns:
        # Rules and AI results outrank keyword guesses; re-runs refresh heuristic ones.
        # Matched refunds already inherited their purchase's category in reconcile.
        if is_locked(t) or t.source in (Source.RULE, Source.AI) or t.refund_of:
            continue
        text = f"{t.merchant} | {t.description}" if t.merchant else t.description
        cat = match_heuristic(text, t.direction)
        if cat is None:
            continue
        # A bank deposit that reconcile already marked as income stays income unless
        # the keyword itself is an income/transfer one (ACH deposit text often names
        # the payer's software vendor, e.g. "...INTUIT..." on a payroll deposit).
        if (t.direction == Direction.IN and t.lane == Lane.INCOME
                and cat.lane not in (Lane.INCOME, Lane.TRANSFER)):
            continue
        t.category = cat.name
        t.lane = cat.lane
        # Money coming in from an expense-type merchant (Upwork/Uber/Airbnb payout,
        # unmatched refund) must not silently reduce expenses: the user decides.
        if t.direction == Direction.IN and cat.lane in (Lane.BUSINESS, Lane.PERSONAL):
            t.lane = Lane.REVIEW
        t.schedule_c = cat.schedule_c if t.lane in (Lane.BUSINESS, Lane.REVIEW) else None
        t.confidence = HEURISTIC_CONFIDENCE
        t.source = Source.HEURISTIC
        t.status = Status.REVIEW if t.lane == Lane.REVIEW else Status.AUTO
        n += 1
    return n


# ---------------------------------------------------------------- learning
def _fallback_normalize(description: str) -> str:
    s = description.upper()
    s = re.split(r"[*#]", s, maxsplit=1)[0]
    s = re.sub(r"\d{3,}", " ", s)
    return " ".join(s.split())


def _merchant_key(t: Transaction) -> str:
    if t.merchant and t.merchant.strip():
        return " ".join(t.merchant.upper().split())
    try:
        from ..ingest.normalize import normalize_merchant  # written in parallel
        key = normalize_merchant(t.description)
    except Exception:
        key = _fallback_normalize(t.description)
    return " ".join((key or "").upper().split())


def learn_rule(txn: Transaction, category: str, lane, schedule_c: Optional[str] = None) -> Rule:
    """Build a rule from a user correction: pattern = the txn's normalized merchant."""
    pattern = _merchant_key(txn)
    if not pattern:
        raise ValueError("cannot learn a rule: transaction has no merchant text")
    return Rule(
        pattern=pattern,
        category=category,
        lane=Lane(lane),
        schedule_c=schedule_c or None,
        field="merchant",
        account_id=None,
        created_by=Source.RULE,   # "learned"; user-authored rules use Source.USER
    )
