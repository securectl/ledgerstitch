# SPDX-License-Identifier: AGPL-3.0-only
"""Core data model shared by every LedgerStitch module.

Sign convention: `amount` is always a positive Decimal. `direction` says which
way the money moved from the account holder's point of view:
  - "out": money left the account (purchase, fee, bill payment, transfer out)
  - "in":  money came in (refund, deposit, card payment credit, transfer in)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


class Lane(str, Enum):
    BUSINESS = "business"    # deductible business expense
    PERSONAL = "personal"    # personal spend, excluded from business totals
    TRANSFER = "transfer"    # moves between the user's own accounts / card payments
    INCOME = "income"        # deposits, sales, payroll in
    REVIEW = "review"        # not sure yet; user must decide


class Source(str, Enum):
    USER = "user"            # user set it explicitly
    RULE = "rule"            # matched a saved rule / learned merchant mapping
    HEURISTIC = "heuristic"  # built-in keyword rules
    AI = "ai"                # AI suggestion on the redacted descriptor
    NONE = "none"


class Status(str, Enum):
    AUTO = "auto"            # categorized with high confidence
    REVIEW = "review"        # needs the user to confirm
    APPROVED = "approved"    # user confirmed


class Direction(str, Enum):
    OUT = "out"
    IN = "in"


class AccountKind(str, Enum):
    CREDIT_CARD = "credit_card"
    CHECKING = "checking"
    SAVINGS = "savings"
    OTHER = "other"


@dataclass
class Account:
    id: str                  # short stable id, e.g. "chase-4012"
    label: str               # user-facing, e.g. "Chase Freedom ••4012"
    institution: str         # "Chase", "Capital One", "Unknown"
    kind: AccountKind = AccountKind.OTHER
    last4: Optional[str] = None


@dataclass
class Transaction:
    account_id: str
    date: date
    description: str               # raw description exactly as the bank wrote it
    amount: Decimal                # always positive
    direction: Direction
    import_id: str = ""            # which upload batch produced it
    bank_category: Optional[str] = None   # category the bank supplied, if any
    card_last4: Optional[str] = None      # physical card used (authorized-user cards differ from the account last4)
    merchant: str = ""             # normalized merchant key (see ingest.normalize)
    category: Optional[str] = None
    lane: Lane = Lane.REVIEW
    schedule_c: Optional[str] = None      # e.g. "L18" (office expense)
    confidence: float = 0.0
    source: Source = Source.NONE
    status: Status = Status.REVIEW
    duplicate_of: Optional[str] = None    # id of the kept transaction
    transfer_group: Optional[str] = None  # links both legs of a transfer/card payment
    refund_of: Optional[str] = None       # id of the purchase this refunds, if known
    note: str = ""
    ai_model: Optional[str] = None        # "provider:model" that suggested the category (local only)
    seq: int = 0                   # position within the source file; disambiguates identical rows
    id: str = field(default="")

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            self.amount = Decimal(str(self.amount))
        if self.amount < 0:
            raise ValueError("Transaction.amount must be positive; use direction")
        if not self.id:
            self.id = make_txn_id(self.account_id, self.date, self.description,
                                  self.amount, self.direction, self.seq)

    @property
    def signed_amount(self) -> Decimal:
        """Negative for money out, positive for money in."""
        return -self.amount if self.direction == Direction.OUT else self.amount

    @property
    def is_excluded(self) -> bool:
        return self.duplicate_of is not None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        d["amount"] = str(self.amount)
        for k in ("lane", "source", "status", "direction"):
            d[k] = getattr(self, k).value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        d = dict(d)
        d["date"] = date.fromisoformat(d["date"])
        d["amount"] = Decimal(d["amount"])
        d["lane"] = Lane(d["lane"])
        d["source"] = Source(d["source"])
        d["status"] = Status(d["status"])
        d["direction"] = Direction(d["direction"])
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


def make_txn_id(account_id: str, d: date, description: str, amount: Decimal,
                direction: Direction, seq: int = 0) -> str:
    """Deterministic id so re-importing the same file never creates new rows."""
    raw = f"{account_id}|{d.isoformat()}|{' '.join(description.split()).upper()}|{amount}|{direction.value}|{seq}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class Rule:
    """User-visible categorization rule. Matches against Transaction.merchant
    (or the raw description when `field == "description"`)."""
    pattern: str                   # case-insensitive substring
    category: str
    lane: Lane
    schedule_c: Optional[str] = None
    field: str = "merchant"        # "merchant" | "description"
    account_id: Optional[str] = None   # restrict to one account
    created_by: Source = Source.USER
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = hashlib.sha256(
                f"{self.field}|{self.pattern.upper()}|{self.account_id}".encode()
            ).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lane"] = self.lane.value
        d["created_by"] = self.created_by.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        d = dict(d)
        d["lane"] = Lane(d["lane"])
        d["created_by"] = Source(d["created_by"])
        return cls(**d)
