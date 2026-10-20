# SPDX-License-Identifier: AGPL-3.0-only
"""Persistent receipt record, and re-matching stored receipts to transactions."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from ..models import Account, Transaction
from .fields import ReceiptFields
from .match import Candidate, match_all

log = logging.getLogger(__name__)

__all__ = ["ReceiptRecord", "receipt_id", "rematch_records", "apply_outcome", "RECEIPT_STATUSES"]

RECEIPT_STATUSES = ("matched", "suggested", "unmatched", "manual", "no_text")


def receipt_id(data: bytes) -> str:
    """Content id: same file dropped twice -> same id (dedupe)."""
    return hashlib.sha256(data).hexdigest()[:16]


def _dec(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v not in (None, "") else None


@dataclass
class ReceiptRecord:
    id: str
    filename: str
    mime: str
    added: str                                  # ISO-8601 UTC timestamp
    size: int
    merchant: Optional[str] = None
    date: Optional[date] = None
    total: Optional[Decimal] = None
    subtotal: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    tip: Optional[Decimal] = None
    card_last4: Optional[str] = None
    card_brand: Optional[str] = None
    payment_method: str = "unknown"             # "card"|"cash"|"unknown"
    currency: Optional[str] = None
    wallet: Optional[str] = None                # Apple/Google/Samsung Pay (last4 may be a device number)
    ocr_engine: str = "none"
    ocr_confidence: float = 0.0
    status: str = "unmatched"                   # matched|suggested|unmatched|manual|no_text
    txn_id: Optional[str] = None
    match_score: Optional[float] = None
    match_reasons: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)   # [{txn_id, score, reasons}]
    field_confidence: dict = field(default_factory=dict)
    note: str = ""

    # ---- persistence (the text read from the image is deliberately NOT part of the record)
    def to_dict(self) -> dict:
        return {
            "id": self.id, "filename": self.filename, "mime": self.mime, "added": self.added,
            "size": self.size, "merchant": self.merchant,
            "date": self.date.isoformat() if self.date else None,
            "total": str(self.total) if self.total is not None else None,
            "subtotal": str(self.subtotal) if self.subtotal is not None else None,
            "tax": str(self.tax) if self.tax is not None else None,
            "tip": str(self.tip) if self.tip is not None else None,
            "card_last4": self.card_last4, "card_brand": self.card_brand,
            "payment_method": self.payment_method, "currency": self.currency, "wallet": self.wallet,
            "ocr_engine": self.ocr_engine, "ocr_confidence": self.ocr_confidence,
            "status": self.status, "txn_id": self.txn_id, "match_score": self.match_score,
            "match_reasons": list(self.match_reasons),
            "candidates": [dict(c) for c in self.candidates],
            "field_confidence": dict(self.field_confidence), "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReceiptRecord":
        d = dict(d)
        d["date"] = date.fromisoformat(d["date"]) if d.get("date") else None
        for k in ("total", "subtotal", "tax", "tip"):
            d[k] = _dec(d.get(k))
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_fields(self) -> ReceiptFields:
        return ReceiptFields(merchant=self.merchant, date=self.date, total=self.total,
                             subtotal=self.subtotal, tax=self.tax, tip=self.tip,
                             card_last4=self.card_last4, card_brand=self.card_brand,
                             payment_method=self.payment_method, currency=self.currency,
                             wallet=self.wallet, field_confidence=dict(self.field_confidence))

    def apply_fields(self, f: ReceiptFields) -> None:
        self.merchant, self.date, self.total = f.merchant, f.date, f.total
        self.subtotal, self.tax, self.tip = f.subtotal, f.tax, f.tip
        self.card_last4, self.card_brand = f.card_last4, f.card_brand
        self.payment_method, self.currency, self.wallet = f.payment_method, f.currency, f.wallet
        self.field_confidence = dict(f.field_confidence)


def apply_outcome(rec: ReceiptRecord, status: str, best: Optional[Candidate],
                  cands: Sequence[Candidate]) -> ReceiptRecord:
    """Write a match decision into a record (does not touch "manual"/"no_text" records)."""
    if rec.status in ("manual", "no_text"):
        return rec
    rec.status = status
    rec.candidates = [c.to_dict() for c in cands]
    if best is not None and status in ("matched", "suggested"):
        rec.txn_id = best.txn_id if status == "matched" else None
        rec.match_score = best.score
        rec.match_reasons = list(best.reasons)
    else:
        rec.txn_id, rec.match_score, rec.match_reasons = None, None, []
    return rec


def rematch_records(records: Sequence[ReceiptRecord], txns: Sequence[Transaction],
                    accounts: dict[str, Account]) -> list[ReceiptRecord]:
    """Re-run matching for stored records (e.g. after a new statement import) without re-reading them.

    "manual" records keep their transaction and reserve it; "no_text" records are skipped.
    Conflicts are resolved with match_all (one transaction per receipt). Mutates and returns
    the records.
    """
    reserved = {r.txn_id for r in records if r.status == "manual" and r.txn_id}
    todo = [r for r in records if r.status not in ("manual", "no_text")]
    outcomes = match_all(todo, txns, accounts, reserved=reserved)
    for r, o in zip(todo, outcomes):
        apply_outcome(r, o.status, o.best, o.candidates)
    return list(records)
