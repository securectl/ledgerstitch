# SPDX-License-Identifier: AGPL-3.0-only
"""The structured fields of a receipt (what matching compares with a transaction)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

__all__ = ["ReceiptFields"]


@dataclass
class ReceiptFields:
    merchant: Optional[str] = None
    date: Optional[date] = None
    total: Optional[Decimal] = None        # final amount charged (includes a printed tip)
    subtotal: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    tip: Optional[Decimal] = None
    card_last4: Optional[str] = None
    card_brand: Optional[str] = None       # "VISA"|"MASTERCARD"|"AMEX"|"DISCOVER"|"DEBIT"|...
    payment_method: str = "unknown"        # "card"|"cash"|"unknown"
    currency: Optional[str] = None         # ISO code, e.g. "USD"
    wallet: Optional[str] = None           # "APPLE PAY"|"GOOGLE PAY"|"SAMSUNG PAY" (last4 may be a device number)
    field_confidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat() if self.date else None
        for k in ("total", "subtotal", "tax", "tip"):
            d[k] = str(getattr(self, k)) if getattr(self, k) is not None else None
        return d
