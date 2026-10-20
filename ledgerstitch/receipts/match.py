# SPDX-License-Identifier: AGPL-3.0-only
"""Match a parsed receipt to the bank/card transaction it substantiates.

Score components (summed, clipped to 0..1):

  amount   exact to the cent ............................ +0.45
           equals total + printed tip ................... +0.40
           restaurant tip window (total < amt <= 1.30x,
           receipt shows no tip line, merchant agrees) .. +0.22
  card     last4 == txn.card_last4 (physical card) ...... +0.30
           last4 == account last4 (card unknown) ........ +0.30
           last4 == account last4 but txn card differs .. +0.15
           KNOWN last4 differs .......................... -0.40 (-0.05 if paid by a phone wallet:
                                                          Apple/Google Pay print a device number)
  date     same day +0.15 | 1-4 days later (posting lag) +0.12 | 1 day earlier +0.08
           5-10 days later +0.05 | otherwise excluded unless amount AND card match
  merchant similarity of normalized names ............... up to +0.15

decide(): "matched" when best >= 0.75, beats the runner-up by >= 0.10 and the date
agrees (same day / posting lag);
"suggested" when best >= 0.45; else "unmatched". Without any amount evidence a
candidate is capped below the suggestion threshold (card + date alone prove nothing).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Iterable, Optional, Sequence

from ..ingest.normalize import normalize_merchant
from ..models import Account, Direction, Transaction
from .fields import ReceiptFields

__all__ = ["Candidate", "MatchOutcome", "match_receipt", "decide", "match_all", "merchant_similarity",
           "MATCH_THRESHOLD", "SUGGEST_THRESHOLD", "MARGIN"]

MATCH_THRESHOLD = 0.75
SUGGEST_THRESHOLD = 0.45
MARGIN = 0.10
TOP_N = 5
TIP_WINDOW = Decimal("1.30")
_CENT = Decimal("0.005")


@dataclass
class Candidate:
    txn_id: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        return cls(txn_id=d["txn_id"], score=float(d.get("score", 0.0)), reasons=list(d.get("reasons") or []))


@dataclass
class MatchOutcome:
    status: str                       # "matched" | "suggested" | "unmatched"
    best: Optional[Candidate]
    candidates: list[Candidate]


# --------------------------------------------------------------------------- merchant similarity

_ALIASES = {
    "AMZN": "AMAZON", "AMZNCOM": "AMAZON", "AMAZONCOM": "AMAZON", "MKTP": "AMAZON", "MKTPL": "AMAZON",
    "WM": "WALMART", "WMT": "WALMART", "WALMARTCOM": "WALMART", "THD": "HOMEDEPOT",
    "HOMEDEPOT": "HOMEDEPOT", "SBUX": "STARBUCKS", "UBERTRIP": "UBER", "UBEREATS": "UBEREATS",
    "MCDONALDS": "MCDONALDS", "MCD": "MCDONALDS", "WHOLEFDS": "WHOLEFOODS", "WFM": "WHOLEFOODS",
    "EXXONMOBIL": "EXXON", "TRADERJOES": "TRADERJOE", "TRADERJOE": "TRADERJOE",
}
_STOP = {"SQ", "TST", "THE", "INC", "LLC", "LTD", "CO", "CORP", "COMPANY", "STORE", "STORES", "SHOP",
         "MARKET", "OF", "AND", "&", "AT", "POS", "PURCHASE", "DEBIT", "CARD", "PAYPAL", "WWW", "COM",
         "ONLINE", "US", "USA", "NA", "MKTPLACE", "MARKETPLACE", "HELP", "BILL", "PAY", "STATION", "GAS",
         "RESTAURANT", "CAFE", "#"}


def _tokens(name: str) -> list[str]:
    s = normalize_merchant(name or "")
    s = s.replace("'", "").replace(".COM", " ")
    toks = [t for t in re.split(r"[^A-Z0-9]+", s.upper()) if t]
    out = []
    for t in toks:
        if t.isdigit() or t in _STOP:
            continue
        out.append(_ALIASES.get(t, t))
    return out


def _squash(toks: Iterable[str]) -> str:
    return "".join(toks)


def merchant_similarity(receipt_merchant: Optional[str], txn_text: str) -> float:
    """0..1 similarity between a receipt's merchant name and a bank descriptor."""
    a, b = _tokens(receipt_merchant or ""), _tokens(txn_text or "")
    if not a or not b:
        return 0.0
    sa, sb = _squash(a), _squash(b)
    sa_alias = _ALIASES.get(sa, sa)
    sb_alias = _ALIASES.get(sb, sb)
    if sa_alias == sb_alias:
        return 1.0
    # token overlap (with prefix matching: "DEPOT" ~ "DEPOT#123", "WHOLEFDS" ~ "WHOLE")
    hits = 0
    for t in a:
        for u in b:
            if t == u or (min(len(t), len(u)) >= 4 and (t.startswith(u) or u.startswith(t))):
                hits += 1
                break
    overlap = hits / min(len(a), len(b))
    # glued words: "BLUEBOTTLECOFFEE" vs "BLUE BOTTLE COFFEE"
    contain = 0.0
    short, long_ = sorted((sa, sb), key=len)
    if len(short) >= 4 and short in long_:
        contain = 0.6 + 0.4 * len(short) / len(long_)
    elif len(short) >= 5 and long_.startswith(short[:5]):
        contain = 0.6
    ratio = SequenceMatcher(None, sa, sb).ratio()
    return round(max(overlap, contain, ratio if ratio >= 0.6 else 0.0), 3)


# --------------------------------------------------------------------------- scoring

def _txn_last4s(txn: Transaction, accounts: dict[str, Account]) -> tuple[Optional[str], Optional[str]]:
    card = getattr(txn, "card_last4", None) or None
    acct = accounts.get(txn.account_id) if accounts else None
    return card, (acct.last4 if acct and acct.last4 else None)


def _score(f: ReceiptFields, t: Transaction, accounts: dict[str, Account]) -> Optional[Candidate]:
    reasons: list[str] = []
    score = 0.0
    sim = max(merchant_similarity(f.merchant, t.merchant or ""),
              merchant_similarity(f.merchant, t.description or ""))

    # ---- amount
    amount_exact = False
    amount_evidence = False
    if f.total is not None:
        total = Decimal(f.total)
        if abs(t.amount - total) < _CENT:
            score += 0.45
            amount_exact = amount_evidence = True
            reasons.append("amount matches exactly")
        elif f.tip and abs(t.amount - (total + Decimal(f.tip))) < _CENT:
            score += 0.40
            amount_exact = amount_evidence = True
            reasons.append("amount equals total + tip")
        elif (not f.tip and total > 0 and total < t.amount <= (total * TIP_WINDOW).quantize(Decimal("0.01"))
              and (sim >= 0.3 or not f.merchant)):   # a tip only explains the gap at the same merchant
            amount_evidence = True
            score += 0.22
            pct = (t.amount - total) / total * 100
            reasons.append(f"amount within tip window (+{pct:.0f}%)")

    # ---- card
    card_match = False
    if f.card_last4:
        card4, acct4 = _txn_last4s(t, accounts)
        if card4 and card4 == f.card_last4:
            score += 0.30
            card_match = True
            reasons.append(f"card ••{f.card_last4} matches")
        elif acct4 and acct4 == f.card_last4:
            card_match = True
            if card4:
                score += 0.15
                reasons.append(f"account ••{acct4} matches (transaction made on card ••{card4})")
            else:
                score += 0.30
                reasons.append(f"account ••{acct4} matches")
        elif card4 or acct4:
            known = card4 or acct4
            if f.wallet:
                score -= 0.05
                reasons.append(f"receipt shows {f.wallet} device ••{f.card_last4}, not card ••{known}")
            else:
                score -= 0.40
                reasons.append(f"card mismatch: receipt ••{f.card_last4}, transaction ••{known}")

    # ---- date
    if f.date is not None:
        delta = (t.date - f.date).days
        if delta == 0:
            score += 0.15
            reasons.append("same day")
        elif 1 <= delta <= 4:
            score += 0.12
            reasons.append(f"posted {delta} day{'s' if delta > 1 else ''} later")
        elif delta == -1:
            score += 0.08
            reasons.append("posted 1 day earlier")
        elif 5 <= delta <= 10:
            score += 0.05
            reasons.append(f"posted {delta} days later")
        else:
            if not (amount_exact and card_match):
                return None
            reasons.append(f"date off by {delta} days")

    # ---- merchant
    if sim >= 0.3:
        score += 0.15 * sim
        reasons.append(f"merchant similar ({sim:.0%})" if sim < 0.99 else "merchant matches")
    elif f.merchant and sim < 0.15 and len(_tokens(f.merchant)) > 0:
        score -= 0.15
        reasons.append("merchant differs")

    score = max(0.0, min(1.0, score))
    if f.total is not None and not amount_evidence:
        score = min(score, SUGGEST_THRESHOLD - 0.05)
    if score <= 0.1:
        return None
    return Candidate(txn_id=t.id, score=round(score, 3), reasons=reasons)


def match_receipt(fields: ReceiptFields, txns: Sequence[Transaction],
                  accounts: dict[str, Account]) -> list[Candidate]:
    """Top-5 candidate transactions for a receipt, best first."""
    out: list[Candidate] = []
    for t in txns:
        if t.direction != Direction.OUT or t.duplicate_of is not None:
            continue
        c = _score(fields, t, accounts or {})
        if c is not None:
            out.append(c)
    out.sort(key=lambda c: (-c.score, c.txn_id))
    return out[:TOP_N]


def decide(cands: Sequence[Candidate]) -> tuple[str, Optional[Candidate]]:
    """("matched"|"suggested"|"unmatched", best candidate or None)."""
    if not cands:
        return "unmatched", None
    best = cands[0]
    runner = cands[1].score if len(cands) > 1 else 0.0
    # Auto-linking needs the date to agree too (same day or a few days of posting lag):
    # a wrong automatic link is worse than asking the user.
    dated = any(r == "same day" or r.startswith("posted ") for r in best.reasons)
    if best.score >= MATCH_THRESHOLD and best.score - runner >= MARGIN - 1e-9 and dated:
        return "matched", best
    if best.score >= SUGGEST_THRESHOLD:
        return "suggested", best
    return "unmatched", None


def _as_fields(r) -> ReceiptFields:
    if isinstance(r, ReceiptFields):
        return r
    if hasattr(r, "to_fields"):
        return r.to_fields()
    raise TypeError("match_all expects ReceiptFields or ReceiptRecord items")


def match_all(receipts: Sequence, txns: Sequence[Transaction], accounts: dict[str, Account],
              reserved: Optional[set[str]] = None) -> list[MatchOutcome]:
    """Match many receipts at once so that one transaction is claimed by at most one receipt.

    `receipts` may be ReceiptFields or ReceiptRecord objects. `reserved` holds txn ids
    already tied to a receipt by hand ("manual"); they are never offered again.
    Assignment is greedy by score (highest pair first). Returns one MatchOutcome per
    receipt, in input order; each outcome's candidate list excludes transactions
    claimed by other receipts.
    """
    reserved = set(reserved or ())
    pool = [t for t in txns if t.id not in reserved]
    all_cands = [match_receipt(_as_fields(r), pool, accounts) for r in receipts]
    pairs = sorted(((c.score, ri, ci) for ri, cs in enumerate(all_cands) for ci, c in enumerate(cs)),
                   key=lambda p: (-p[0], p[1], p[2]))
    owner: dict[str, int] = {}
    assigned: dict[int, Candidate] = {}
    for score, ri, ci in pairs:
        c = all_cands[ri][ci]
        if ri in assigned or c.txn_id in owner:
            continue
        # only lock in pairs that would at least be suggested; weaker ones don't block others
        if score < SUGGEST_THRESHOLD:
            continue
        owner[c.txn_id] = ri
        assigned[ri] = c
    outcomes: list[MatchOutcome] = []
    for ri, cs in enumerate(all_cands):
        free = [c for c in cs if owner.get(c.txn_id, ri) == ri]
        mine = assigned.get(ri)
        if mine is not None:
            free = [mine] + [c for c in free if c.txn_id != mine.txn_id]
        # mine (if any) is the best free candidate: anything better would have been assigned
        status, best = decide(free)
        outcomes.append(MatchOutcome(status=status, best=best, candidates=free))
    return outcomes
