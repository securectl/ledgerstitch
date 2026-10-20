# SPDX-License-Identifier: AGPL-3.0-only
"""Flat CSV export: one row per non-duplicate transaction, ISO dates, signed amount
(negative = money out, positive = money in)."""
from __future__ import annotations

import csv
from pathlib import Path

from .summary import category_of, split_active

CSV_COLUMNS = [
    "date", "account_id", "description", "merchant", "category", "lane", "schedule_c",
    "amount", "direction", "status", "source", "confidence", "transfer_group",
    "refund_of", "note", "txn_id",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_text(value) -> str:
    """Neutralize spreadsheet formula injection in free-text fields."""
    s = "" if value is None else str(value)
    if s and s[0] in _FORMULA_PREFIXES:
        return "'" + s
    return s


def export_csv(txns, path: Path, year: int | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    active, _ = split_active(txns, year)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for t in active:
            w.writerow([
                t.date.isoformat(), t.account_id, safe_text(t.description), safe_text(t.merchant),
                safe_text(category_of(t)), t.lane.value, t.schedule_c or "",
                f"{t.signed_amount:.2f}", t.direction.value, t.status.value, t.source.value,
                f"{t.confidence:.2f}", t.transfer_group or "", t.refund_of or "",
                safe_text(t.note), t.id,
            ])
    return path
