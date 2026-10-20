# SPDX-License-Identifier: AGPL-3.0-only
"""Receipts: stored receipt records and matching them to transactions.

    from ledgerstitch.receipts import ReceiptRecord, rematch_records

Receipt images and the text read from them stay in the encrypted vault; nothing here is
sent anywhere. Reading new receipts is done by an add-on, which hands the core a record
(``Workspace.add_receipt(filename, data, reader)``).
"""
from .fields import ReceiptFields
from .match import Candidate, MatchOutcome, decide, match_all, match_receipt, merchant_similarity
from .model import RECEIPT_STATUSES, ReceiptRecord, apply_outcome, receipt_id, rematch_records

__all__ = [
    "Candidate", "MatchOutcome", "decide", "match_all", "match_receipt", "merchant_similarity",
    "RECEIPT_STATUSES", "ReceiptFields", "ReceiptRecord", "apply_outcome", "receipt_id", "rematch_records",
]
