# SPDX-License-Identifier: AGPL-3.0-only
"""CSV statement parser.

`parse_csv(data, profile, account_id, import_id)` turns a bank/card CSV export
into `Transaction`s using the `FormatProfile` produced by `detect()` (or a
user-built manual mapping). It is robust to BOMs, quoted fields, trailing
commas, `$`/thousands separators/parentheses in amounts, blank lines, leading
summary blocks and footer totals.

Direction rules (models.py: amount always positive + direction):
  * split debit/credit columns: debit → out, credit → in (sign of the value
    is ignored — Citi writes credits as negative numbers);
  * single amount column: sign + `negative_is_out`; if the column is
    unsigned in this file (no negatives at all), the bank's Type/Details
    column (Payment/Return/Refund/Credit…) decides;
  * zero-amount rows are skipped.
`seq` is the data-row index so identical legitimate rows get distinct ids.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional

from ..models import AccountKind, Direction, Transaction
from .detect import (ColumnMapping, FormatProfile, PAYMENT_RE, find_header, infer_date_format,
                     parse_date, parse_money, read_rows)
from .normalize import normalize_merchant

__all__ = ["parse_csv", "parse_csv_detailed", "preview_csv"]

# Values of a bank "Type"/"Details"/"Transaction" column that mean money in.
_IN_TYPES = re.compile(
    r"^(?:payment|payment_transaction|return|purchase_return|refund|credit|cr|deposit|"
    r"adjustment credit|statement credit|reward|rewards|cashback|cash back|interest credit|dep|"
    r"ach_credit|credit_card_payment_credit|incoming|transfer in|xfer in)\b", re.I)
_OUT_TYPES = re.compile(
    r"^(?:sale|purchase|debit|dr|fee|fees|interest|clearing|withdrawal|check|atm|dslip|"
    r"installment|ach_debit|bill pay|transfer out|xfer out|charge)\b", re.I)


_DROPPED = re.compile(r"^\s*(?:failed|failure|cancel+ed|declined|reverted|rejected|voided|returned unpaid)\s*$", re.I)
_PENDING = re.compile(r"^\s*(?:pending|pend|authori[sz]ation|authori[sz]ed|pendiente|en attente|vorgemerkt)\s*$", re.I)
# "DEBIT CARD PURCHASE ... CARD 1234", "CHECKCARD 0115 ... XXXXXXXXXXXX1234", "CARD ENDING IN 1234"
_CARD_IN_DESC = re.compile(
    r"\bCARD\s*(?:#|NO\.?|NUMBER|ENDING(?:\s+IN)?|ENDING\s+WITH)?\s*[X*•.]*\s*(\d{4})\b(?!\s*/)"
    r"|\b[X*]{4,}(\d{4})\b", re.I)


def _index(header: list[str], col: Optional[str]) -> Optional[int]:
    if col is None:
        return None
    if col in header:
        return header.index(col)
    low = [h.strip().lower() for h in header]
    c = col.strip().lower()
    if c in low:
        return low.index(c)
    m = re.fullmatch(r"col(\d+)", c)
    if m:
        return int(m.group(1))
    return None


def _cell(row: list[str], i: Optional[int]) -> str:
    if i is None or i >= len(row):
        return ""
    return row[i].strip()


def parse_csv_detailed(data: bytes, profile: FormatProfile, account_id: str,
                       import_id: str) -> tuple[list[Transaction], list[str]]:
    """Like `parse_csv` but also returns human-readable warnings (no raw data in them)."""
    mapping: ColumnMapping | None = profile.mapping
    warnings: list[str] = []
    if mapping is None:
        return [], ["no column mapping: map the columns manually"]
    rows = read_rows(data)
    hidx = profile.header_row
    if hidx is None or (hidx >= 0 and (hidx >= len(rows) or _index(rows[hidx], mapping.date) is None
                                       or mapping.date not in rows[hidx])):
        hidx = _locate_header(rows, mapping)
    if hidx is None:
        return [], ["header row with the mapped columns was not found"]
    if hidx < 0:
        width = max((len(r) for r in rows if r), default=0)
        header = [f"col{i}" for i in range(width)]
        body = rows
    else:
        header = rows[hidx]
        body = rows[hidx + 1:]

    i_date = _index(header, mapping.date)
    i_desc = _index(header, mapping.description)
    i_amt = _index(header, mapping.amount)
    i_deb = _index(header, mapping.debit)
    i_cre = _index(header, mapping.credit)
    i_type = _index(header, mapping.type_col)
    i_cat = _index(header, mapping.category)
    i_memo = _index(header, getattr(mapping, "memo", None) or "Memo")
    i_card = _index(header, getattr(mapping, "card", None))
    i_status = _index(header, getattr(mapping, "status", None))
    if i_status is None:
        i_status = next((i for i, h in enumerate(header)
                         if h.strip().lower() in ("status", "transaction status", "state")), None)
    if i_date is None or (i_amt is None and i_deb is None and i_cre is None):
        return [], ["mapped date/amount columns are missing from the file"]

    fmt = mapping.date_format
    if not fmt:
        fmt = infer_date_format([_cell(r, i_date) for r in body if r][:300])

    # Is the single amount column signed in this file?
    unsigned = False
    if i_amt is not None:
        vals = [parse_money(_cell(r, i_amt)) for r in body if r]
        vals = [v for v in vals if v is not None]
        unsigned = bool(vals) and all(v >= 0 for v in vals)
        if unsigned and len(vals) > 3 and i_type is None:
            warnings.append("amount column has no negative values and no type column; "
                            "direction assumed from the account kind")

    is_card = profile.kind == AccountKind.CREDIT_CARD
    txns: list[Transaction] = []
    skipped_unparsed = skipped_zero = skipped_pending = skipped_failed = 0
    seq = -1
    for r in body:
        if not r:
            continue
        seq += 1
        raw_date = _cell(r, i_date)
        d: Optional[date] = parse_date(raw_date, fmt) or parse_date(raw_date)
        if d is None:
            # footer totals, "Beginning balance", repeated headers, notes
            if any(c for c in r):
                skipped_unparsed += 1
            continue
        if i_status is not None and _PENDING.match(_cell(r, i_status)):
            skipped_pending += 1
            continue
        if i_status is not None and _DROPPED.match(_cell(r, i_status)):
            skipped_failed += 1
            continue
        type_val = _cell(r, i_type)
        desc = _cell(r, i_desc)
        if not desc:
            desc = type_val or _cell(r, i_memo) or "(no description)"
        desc = " ".join(desc.split())

        direction: Optional[Direction] = None
        amount: Optional[Decimal] = None
        if i_amt is not None and (i_deb is None and i_cre is None):
            v = parse_money(_cell(r, i_amt))
            if v is None:
                skipped_unparsed += 1
                continue
            amount = abs(v)
            if v == 0:
                skipped_zero += 1
                continue
            if unsigned:
                direction = _direction_from_type(type_val, desc, is_card)
            else:
                neg = v < 0
                direction = Direction.OUT if neg == mapping.negative_is_out else Direction.IN
        else:
            dv = parse_money(_cell(r, i_deb)) if i_deb is not None else None
            cv = parse_money(_cell(r, i_cre)) if i_cre is not None else None
            if dv is None and cv is None and i_amt is not None:
                v = parse_money(_cell(r, i_amt))
                if v is not None and v != 0:
                    neg = v < 0
                    dv, cv = (abs(v), None) if neg == mapping.negative_is_out else (None, abs(v))
            dv = abs(dv) if dv else Decimal(0)
            cv = abs(cv) if cv else Decimal(0)
            net = cv - dv
            if net == 0:
                if dv == 0 and cv == 0 and not (_cell(r, i_deb) or _cell(r, i_cre)):
                    skipped_unparsed += 1
                else:
                    skipped_zero += 1
                continue
            amount = abs(net)
            direction = Direction.IN if net > 0 else Direction.OUT

        bank_cat = _cell(r, i_cat) or None
        card_digits = re.sub(r"\D", "", _cell(r, i_card)) if i_card is not None else ""
        card_last4 = card_digits[-4:] if len(card_digits) >= 4 else None
        if card_last4 is None:
            m = _CARD_IN_DESC.search(desc)
            card_last4 = (m.group(1) or m.group(2)) if m else None
        t = Transaction(account_id=account_id, date=d, description=desc, amount=amount,
                        direction=direction, import_id=import_id, bank_category=bank_cat,
                        card_last4=card_last4, merchant=normalize_merchant(desc), seq=seq)
        txns.append(t)

    if skipped_unparsed:
        warnings.append(f"skipped {skipped_unparsed} non-transaction row(s) (totals, notes or unparseable dates/amounts)")
    if skipped_zero:
        warnings.append(f"skipped {skipped_zero} zero-amount row(s)")
    if skipped_failed:
        warnings.append(f"skipped {skipped_failed} failed/cancelled row(s)")
    if skipped_pending:
        warnings.append(f"skipped {skipped_pending} pending row(s); import again once they post")
    if not txns:
        warnings.append("no transactions found")
    return txns, warnings


def _direction_from_type(type_val: str, desc: str, is_card: bool) -> Direction:
    t = (type_val or "").strip()
    if t:
        if _IN_TYPES.search(t):
            return Direction.IN
        if _OUT_TYPES.search(t):
            return Direction.OUT
    if is_card:
        return Direction.IN if PAYMENT_RE.search(desc or "") else Direction.OUT
    return Direction.IN if re.search(r"DEPOSIT|PAYROLL|ZELLE FROM|TRANSFER FROM|REFUND|INTEREST PAID",
                                     desc or "", re.I) else Direction.OUT


def _locate_header(rows: list[list[str]], mapping: ColumnMapping) -> Optional[int]:
    if re.fullmatch(r"col\d+", mapping.date or ""):
        return -1
    for i, r in enumerate(rows[:60]):
        low = [c.strip().lower() for c in r]
        if mapping.date.strip().lower() in low and mapping.description.strip().lower() in low:
            return i
    return None


def parse_csv(data: bytes, profile: FormatProfile, account_id: str, import_id: str) -> list[Transaction]:
    """Parse a CSV export into transactions (see module docstring)."""
    return parse_csv_detailed(data, profile, account_id, import_id)[0]


def preview_csv(data: bytes, rows: int = 8) -> dict:
    """First rows of a CSV for the manual column-mapping UI.

    Returns {"columns": [...], "rows": [[...]], "header_row": int}. For a
    headerless file the columns are named col0..colN.
    """
    all_rows = read_rows(data)
    hidx, _known = find_header(all_rows)
    if hidx is None:
        hidx = next((i for i, r in enumerate(all_rows) if r), 0)
    if hidx < 0:
        width = max((len(r) for r in all_rows if r), default=0)
        columns = [f"col{i}" for i in range(width)]
        body = [r for r in all_rows if r]
    else:
        columns = all_rows[hidx] if hidx < len(all_rows) else []
        body = [r for r in all_rows[hidx + 1:] if r]
    out_rows = [(r + [""] * len(columns))[:max(len(columns), len(r))] for r in body[:rows]]
    return {"columns": columns, "rows": out_rows, "header_row": hidx}
