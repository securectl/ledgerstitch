# SPDX-License-Identifier: AGPL-3.0-only
"""QIF (Quicken Interchange Format) statement parser.

Supports ``!Type:Bank``, ``CCard``, ``Cash``, ``Oth A`` and ``Oth L`` sections
(investment sections are skipped with a warning), optional ``!Account`` /
``!Option`` / ``!Clear`` header blocks, and the record fields

    D date   T/U amount   P payee   M memo   N number   L category   ^ end

Split lines (S/E/$) are ignored: the record total ``T`` is used. QIF dates
come in many styles (``1/ 5'25``, ``01/05/2025``, ``5.1.2025``,
``2025-01-05``); month/day order is decided once for the whole file: if any
date has a first component > 12 the file is day-first, if any has a second
component > 12 it is month-first, otherwise month-first is assumed (noted in
info). Sign: negative = money out (Quicken writes card charges negative too).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from ..models import Direction, Transaction
from .detect import decode_bytes, parse_money
from .normalize import normalize_merchant

__all__ = ["sniff_qif", "qif_info", "parse_qif", "parse_qif_date"]

_KINDS = {"bank": "checking", "ccard": "credit_card", "cash": "other", "oth a": "other",
          "oth l": "other", "invst": "investment"}


def _lines(data: bytes) -> list[str]:
    return decode_bytes(data).replace("\r\n", "\n").replace("\r", "\n").split("\n")


def sniff_qif(data: bytes) -> bool:
    try:
        head = data[:2048].decode("latin-1")
    except Exception:  # pragma: no cover
        return False
    for ln in head.lstrip("﻿\xef\xbb\xbf").splitlines():
        s = ln.strip()
        if not s:
            continue
        return bool(re.match(r"!(?:Type:|Account|Option)", s, re.I))
    return False


def _section_type(lines: list[str]) -> Optional[str]:
    for ln in lines:
        m = re.match(r"\s*!Type:\s*(.+?)\s*$", ln, re.I)
        if m:
            return m.group(1)
    return None


def qif_info(data: bytes) -> dict:
    t = _section_type(_lines(data))
    kind = _KINDS.get((t or "").strip().lower(), "other")
    return {"format": "QIF", "kind": kind, "type": t}


# --------------------------------------------------------------------------- dates

_DATE_RE = re.compile(r"^\s*(\d{1,4})\s*[/.\-]\s*(\d{1,2})\s*(?:[/.\-']\s*(\d{1,4}))?\s*$")


def _split_date(raw: str) -> Optional[tuple[int, int, int, bool]]:
    """-> (a, b, year, iso) where a/b are the first two components (iso => a=month,b=day)."""
    s = (raw or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        return int(m.group(2)), int(m.group(3)), int(m.group(1)), True
    m = re.match(r"^(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*(['/.\-])\s*(\d{1,4})$", s)
    if not m:
        return None
    a, b, sep, y = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
    yr = int(y)
    if sep == "'":
        yr = 2000 + yr if yr < 100 else yr
    elif len(y) <= 2:
        yr = 1900 + yr if yr >= 70 else 2000 + yr
    return a, b, yr, False


def parse_qif_date(raw: str, day_first: bool = False) -> Optional[date]:
    p = _split_date(raw)
    if p is None:
        return None
    a, b, y, iso = p
    mo, d = (a, b) if (iso or not day_first) else (b, a)
    try:
        return date(y, mo, d)
    except ValueError:
        return None


# --------------------------------------------------------------------------- parse

def _records(lines: list[str]):
    """Yield (section_type, {field: [values]}) for each ^-terminated record."""
    section: Optional[str] = None
    in_header = False          # inside an !Account / !Option block
    rec: dict[str, list[str]] = {}
    for ln in lines:
        s = ln.rstrip()
        if not s.strip():
            continue
        if s.startswith("!"):
            m = re.match(r"!Type:\s*(.+?)\s*$", s, re.I)
            if m:
                section, in_header = m.group(1), False
            elif re.match(r"!Account", s, re.I):
                in_header = True
            rec = {}
            continue
        if s.strip() == "^":
            if rec and not in_header:
                yield section, rec
            in_header = False if in_header else in_header
            rec = {}
            continue
        code, val = s[0], s[1:].strip()
        rec.setdefault(code, []).append(val)
    if rec and not in_header:
        yield section, rec


def parse_qif(data: bytes, account_id: str, import_id: str) -> tuple[list[Transaction], list[str], dict]:
    """Parse a QIF file into transactions, warnings and info."""
    lines = _lines(data)
    info = qif_info(data)
    warnings: list[str] = []
    recs = list(_records(lines))
    invest = [r for r in recs if (r[0] or "").strip().lower().startswith("invst")]
    recs = [r for r in recs if r not in invest]
    if invest:
        warnings.append(f"investment QIF not supported: skipped {len(invest)} record(s)")

    # decide month/day order for the whole file
    first_big = second_big = ambiguous = False
    for _, f in recs:
        p = _split_date((f.get("D") or [""])[0])
        if not p or p[3]:
            continue
        a, b = p[0], p[1]
        if a > 12:
            first_big = True
        elif b > 12:
            second_big = True
        else:
            ambiguous = True
    day_first = first_big and not second_big
    if first_big and second_big:
        warnings.append("dates mix day/month orders; assumed month/day where possible")
    if ambiguous and not first_big and not second_big:
        info["date_order_note"] = "date order assumed month/day"

    txns: list[Transaction] = []
    zero = bad = 0
    for seq, (_, f) in enumerate(recs):
        raw_d = (f.get("D") or [""])[0]
        d = parse_qif_date(raw_d, day_first) or (parse_qif_date(raw_d, not day_first) if first_big and second_big else None)
        amt = parse_money((f.get("T") or f.get("U") or [""])[0])
        if d is None or amt is None:
            bad += 1
            continue
        if amt == 0:
            zero += 1
            continue
        payee = (f.get("P") or [""])[0]
        memo = (f.get("M") or [""])[0]
        num = (f.get("N") or [""])[0]
        desc = payee or memo
        if not desc and num:
            desc = f"CHECK #{num}" if num.isdigit() else num
        desc = " ".join((desc or "(no description)").split())
        cat = (f.get("L") or [None])[0] or None
        txns.append(Transaction(account_id=account_id, date=d, description=desc, amount=abs(amt),
                                direction=Direction.OUT if amt < 0 else Direction.IN, import_id=import_id,
                                bank_category=cat, merchant=normalize_merchant(desc), seq=seq))
    if zero:
        warnings.append(f"skipped {zero} zero-amount row(s)")
    if bad:
        warnings.append(f"skipped {bad} record(s) with an unreadable date or amount")
    if not txns:
        warnings.append("no transactions found")
    info["day_first"] = day_first
    info["reconciled"] = None
    return txns, warnings, info
