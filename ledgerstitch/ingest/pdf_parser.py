# SPDX-License-Identifier: AGPL-3.0-only
"""Generic PDF bank/card statement parser (pdfplumber).

Nothing here is specific to one bank. The parser:

1. finds the statement period ("Statement period 01/01/2025 to 01/31/2025",
   "January 1, 2025 through January 31, 2025", "Beginning balance on 1/1" +
   the statement date, ...) and uses it to put a year on M/D dates, handling
   Dec→Jan rollovers. The year is never hard-coded;
2. decides money in/out, in order of preference:
     a. column position: amounts are matched to the x-position of
        "Deposits/Credits" vs "Withdrawals/Debits" column headers;
     b. section headers: "DEPOSITS AND ADDITIONS" (in), "ELECTRONIC
        WITHDRAWALS" / "ATM & DEBIT CARD WITHDRAWALS" / "FEES" (out), card
        "PAYMENTS AND OTHER CREDITS" (in) / "PURCHASES" (out), ...;
     c. running-balance delta (amount followed by a balance column);
     d. last resort: the sign of the amount (warned);
3. joins multi-line descriptions (indented continuation lines);
4. reconciles parsed totals against the totals the statement prints and
   returns a warning on mismatch.

The line/column logic works on plain `Line`/`Word` structures so it can be
unit-tested without a PDF (see `parse_lines`).
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from ..models import Direction, Transaction
from .normalize import normalize_merchant

__all__ = ["parse_pdf", "parse_lines", "find_period", "assign_year", "Word", "Line", "words_to_lines"]

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"])}
_MON3 = {k[:3]: v for k, v in _MONTHS.items()}
_MONTH_NAME = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|" \
              r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"


# --------------------------------------------------------------------------- structures

@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float


@dataclass
class Line:
    words: list[Word]
    page: int = 0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words) if self.words else 0.0

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words) if self.words else 0.0


def words_to_lines(words: list[dict] | list[Word], page: int = 0, tol: float = 3.0) -> list[Line]:
    """Cluster pdfplumber words into visual lines by their `top`."""
    ws = [w if isinstance(w, Word) else Word(w["text"], float(w["x0"]), float(w["x1"]), float(w["top"]))
          for w in words]
    ws.sort(key=lambda w: (w.top, w.x0))
    lines: list[list[Word]] = []
    for w in ws:
        if lines and abs(lines[-1][0].top - w.top) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [Line(sorted(l, key=lambda w: w.x0), page) for l in lines]


# --------------------------------------------------------------------------- period / year

def _mk(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _year4(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def _named(mon: str, d: str, y: str) -> Optional[date]:
    m = _MON3.get(mon.lower().rstrip(".")[:3])
    return _mk(_year4(y), m, int(d)) if m else None


def find_period(text: str) -> tuple[Optional[date], Optional[date]]:
    """Return (start, end) of the statement period, either may be None."""
    t = " ".join(text.split())
    # "January 1, 2025 through January 31, 2025" (Chase prints "2025throughDecember")
    m = re.search(rf"({_MONTH_NAME})\s*(\d{{1,2}}),?\s*(\d{{4}})\s*(?:through|thru|to|-|–|—)\s*"
                  rf"({_MONTH_NAME})\s*(\d{{1,2}}),?\s*(\d{{4}})", t, re.I)
    if m:
        return _named(m.group(1), m.group(2), m.group(3)), _named(m.group(4), m.group(5), m.group(6))
    # "01/01/2025 to 01/31/2025", "Statement period: 12/15/24 - 01/14/25"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s*(?:through|thru|to|-|–|—)\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", t, re.I)
    if m:
        g = m.groups()
        return _mk(_year4(g[2]), int(g[0]), int(g[1])), _mk(_year4(g[5]), int(g[3]), int(g[4]))
    # "Opening/Closing Date 12/15/24 - 01/14/25" handled above; "Closing Date 01/14/25"
    m = re.search(r"closing\s*date:?\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", t, re.I)
    if m:
        return None, _mk(_year4(m.group(3)), int(m.group(1)), int(m.group(2)))
    # "Statement Closing Date October 02, 2026" (Robinhood prints it without spaces)
    m = re.search(rf"closing\s*date:?\s*({_MONTH_NAME})\s*(\d{{1,2}}),?\s*(\d{{4}})", t, re.I)
    if m:
        end = _named(m.group(1), m.group(2), m.group(3))
        days = re.search(r"days\s*in\s*billing\s*cycle:?\s*(\d{1,2})\b", t, re.I)
        start = end - timedelta(days=int(days.group(1)) - 1) if end and days else None
        return start, end
    # "Beginning balance on 1/1 ... Ending balance on 1/31" + statement date "February 28, 2025"
    b = re.search(r"beginning balance on (\d{1,2})/(\d{1,2})", t, re.I)
    e = re.search(r"ending balance on (\d{1,2})/(\d{1,2})", t, re.I)
    stmt = re.search(rf"({_MONTH_NAME})\s+(\d{{1,2}}),\s*(\d{{4}})", t, re.I)
    if stmt:
        sd = _named(stmt.group(1), stmt.group(2), stmt.group(3))
        if sd and (b or e):
            end = None
            if e:
                end = _mk(sd.year, int(e.group(1)), int(e.group(2)))
                if end and end > sd + timedelta(days=7):
                    end = _mk(sd.year - 1, int(e.group(1)), int(e.group(2)))
            start = None
            if b:
                ref = end or sd
                start = _mk(ref.year, int(b.group(1)), int(b.group(2)))
                if start and start > ref:
                    start = _mk(ref.year - 1, int(b.group(1)), int(b.group(2)))
            return start, end or sd
        if sd:
            return None, sd
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t)
    if m:
        return None, _mk(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    return None, None


def assign_year(month: int, day: int, start: Optional[date], end: Optional[date]) -> Optional[date]:
    """Put a year on a M/D date using the statement period (handles Dec→Jan)."""
    if end is None and start is None:
        return None
    ref = end or start
    d = _mk(ref.year, month, day)
    if d is None and month == 2 and day == 29:
        d = _mk(ref.year - 1, month, day) or _mk(ref.year + 1, month, day)
        return d
    if d is None:
        return None
    if end is not None and d > end + timedelta(days=10):
        d = _mk(ref.year - 1, month, day) or d
    elif start is not None and end is None and d < start - timedelta(days=10):
        d = _mk(ref.year + 1, month, day) or d
    return d


# --------------------------------------------------------------------------- tokens

_DATE_TOKEN = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$")
_REF_TOKEN = re.compile(r"^(?=(?:[A-Z]*\d){6})[A-Z0-9]{12,24}$")
_MONEY_TOKEN = re.compile(r"^(\()?(-)?\$?(-)?(\d{1,3}(?:,\d{3})*|\d+)\.(\d{2})(\))?(-|CR)?$", re.I)


def _money(tok: str) -> Optional[Decimal]:
    m = _MONEY_TOKEN.match(tok.strip())
    if not m:
        return None
    v = Decimal(m.group(4).replace(",", "") + "." + m.group(5))
    neg = bool(m.group(2) or m.group(3) or (m.group(1) and m.group(6)) or m.group(7))
    return -v if neg else v


# --------------------------------------------------------------------------- sections

_SKIP_SECTIONS = re.compile(
    r"^(?:daily (?:ending )?balance(?:s| summary)?|checking summary|savings summary|account summary|"
    r"summary of checks written.*|items returned unpaid|monthly service fee summary|account transaction fees summary|"
    r"interest charge calculation|year-to-date totals?.*|totals year-to-date|balance summary|"
    r"daily ending balance|overdraft and returned item fee summary|service fee summary)\b", re.I)
_IN_SECTIONS = re.compile(
    r"^(?:deposits and (?:other )?(?:additions|credits)|deposits|other deposits|additions|electronic deposits|"
    r"atm (?:& debit card )?deposits|payments and (?:other )?credits|payments(?:,| and) credits(?: and adjustments)?|"
    r"credits|other credits|interest (?:earned|paid))(?:\s*\(continued\))?$", re.I)
_OUT_SECTIONS = re.compile(
    r"^(?:electronic withdrawals|atm (?:& |and )?debit card withdrawals|other withdrawals|withdrawals(?: and (?:other )?(?:subtractions|debits))?|"
    r"checks paid|checks|fees(?: charged)?|service fees|interest charged|(?:standard )?purchases?(?: and (?:other )?(?:adjustments|debits))?|"
    r"card purchases|debit card purchases|atm withdrawals|other debits|debits|subtractions|cash advances?|"
    r"other subtractions|transactions? continued|electronic payments)(?:\s*\(continued\))?$", re.I)
_TABLE_START = re.compile(r"^transaction (?:history|details|activity)(?:\s*\(continued\))?$|^account activity(?:\s*\(continued\))?$", re.I)

_CREDIT_HDR = re.compile(r"^(?:deposits?/?|credits?|additions|deposits/credits|payments/credits)$", re.I)
_DEBIT_HDR = re.compile(r"^(?:withdrawals?/?|debits?|subtractions|charges|withdrawals/debits)$", re.I)
_BAL_HDR = re.compile(r"^balance$", re.I)
_AMOUNT_HDR = re.compile(r"^(?:amount|\$ ?amount)$", re.I)

_STOP_LINE = re.compile(r"^(?:page \d+ of \d+|page of|\d+ \d+|continued on next page|"
                        r"\(continued\)|.*statement period.*|account number:?.*)$", re.I)


@dataclass
class _Columns:
    credit: Optional[float] = None    # right edge x1 of the credit column header
    debit: Optional[float] = None
    balance: Optional[float] = None
    amount: Optional[float] = None
    desc_x0: Optional[float] = None

    @property
    def split(self) -> bool:
        return self.credit is not None and self.debit is not None


def _header_columns(line: Line) -> Optional[_Columns]:
    """Recognize a column header line ("Date ... Credits Debits balance")."""
    texts = [w.text for w in line.words]
    if not any(re.match(r"^date", t, re.I) for t in texts):
        return None
    c = _Columns()
    for w in line.words:
        t = w.text.strip(":")
        if _CREDIT_HDR.match(t) and c.credit is None:
            c.credit = w.x1
        elif _DEBIT_HDR.match(t) and c.debit is None:
            c.debit = w.x1
        elif _BAL_HDR.match(t):
            c.balance = w.x1
        elif _AMOUNT_HDR.match(t):
            c.amount = w.x1
        elif re.match(r"^description$", t, re.I):
            c.desc_x0 = w.x0
    if c.split or c.amount is not None or c.balance is not None:
        return c
    return None


# --------------------------------------------------------------------------- core

@dataclass
class _Raw:
    date: date
    desc_parts: list[str]
    amount: Decimal
    direction: Optional[Direction]
    balance: Optional[Decimal] = None
    signed: Optional[Decimal] = None
    page: int = 0
    top: float = 0.0
    date_x0: float = 0.0
    cont: int = 0


@dataclass
class _State:
    mode: Optional[str] = None             # "in" | "out" | "table" | "skip" | None
    columns: Optional[_Columns] = None
    stated_in: Decimal = Decimal(0)
    stated_out: Decimal = Decimal(0)
    have_stated_in: bool = False
    have_stated_out: bool = False
    used_columns: bool = False
    used_sections: bool = False
    used_balance: bool = False
    used_sign: int = 0
    skipped: int = 0


def parse_lines(lines: list[Line], full_text: str, is_card: bool | None = None
                ) -> tuple[list[_Raw], list[str], dict]:
    """Parse already-extracted lines. Returns (raw rows, warnings, info)."""
    warnings: list[str] = []
    start, end = find_period(full_text)
    if end is None and start is None:
        warnings.append("statement period not found; dates without a year were skipped")
    if is_card is None:
        strong = (re.search(r"credit\s*limit", full_text, re.I)
                  and re.search(r"minimum\s*payment\s*due", full_text, re.I))
        is_card = bool(strong) or (bool(re.search(r"minimum\s*payment\s*due|payment\s*due\s*date|credit\s*limit|"
                                                  r"available\s*credit", full_text, re.I))
                                   and not re.search(r"checking|savings", full_text, re.I))
    st = _State()
    rows: list[_Raw] = []
    beginning = _find_amount(full_text, r"beginning balance(?: on \d{1,2}/\d{1,2})?|opening balance|previous balance")
    prev_balance = beginning
    any_structure = any(_TABLE_START.match(l.text.strip()) or _IN_SECTIONS.match(l.text.strip())
                        or _OUT_SECTIONS.match(l.text.strip()) or _header_columns(l) for l in lines)

    for li, line in enumerate(lines):
        text = line.text.strip()
        if not text:
            continue
        low = text.lower()
        # Chase-style "*start*..." / "*end*..." markers
        if low.startswith("*start*") or low.startswith("*end*"):
            if low.startswith("*end*"):
                st.mode = None if st.mode != "table" else st.mode
            continue
        hdr = _header_columns(line)
        if hdr:
            st.columns = hdr
            # a table header right after a leading summary block (Robinhood) starts the table;
            # one inside a later skipped section ("Items returned unpaid") does not
            if hdr.split or st.mode is None or (st.mode == "skip" and not rows):
                st.mode = "table"
            continue
        if _SKIP_SECTIONS.match(text):
            st.mode = "skip"
            continue
        if _TABLE_START.match(text):
            st.mode = "table"
            continue
        if _IN_SECTIONS.match(text):
            st.mode, st.used_sections = "in", True
            continue
        if _OUT_SECTIONS.match(text):
            st.mode, st.used_sections = "out", True
            continue
        # totals
        if re.match(r"^totals?\b", low):
            _take_totals(line, st)
            if re.match(r"^totals\b", low) or st.mode in ("in", "out"):
                st.mode = "skip" if st.mode == "table" else None
            continue
        if any_structure and st.mode in (None, "skip"):
            continue

        words = line.words
        di = 0
        dm = _DATE_TOKEN.match(words[0].text) if words else None
        if not dm and len(words) > 2 and re.fullmatch(r"\d{2,6}\*?\^?", words[0].text) and _DATE_TOKEN.match(words[1].text):
            di, dm = 1, _DATE_TOKEN.match(words[1].text)   # "1234 ^ 12/05" (checks paid)
        if not dm and len(words) > 2 and words[1].text in ("^", "*") and _DATE_TOKEN.match(words[2].text):
            di, dm = 2, _DATE_TOKEN.match(words[2].text)
        money_idx = _trailing_money(words)
        if dm and money_idx:
            mo, dd, yy = int(dm.group(1)), int(dm.group(2)), dm.group(3)
            if not (1 <= mo <= 12 and 1 <= dd <= 31):
                continue
            d = _mk(_year4(yy), mo, dd) if yy else assign_year(mo, dd, start, end)
            if d is None:
                st.skipped += 1
                continue
            body = words[di + 1: money_idx[0]]
            # Chase prints "12/03 12/03 description" (txn date + post date)
            if body and _DATE_TOKEN.match(body[0].text):
                body = body[1:]
            # card reference number column ("2469216K3W4ABCD12") before the merchant
            if len(body) > 1 and _REF_TOKEN.match(body[0].text):
                body = body[1:]
            desc = " ".join(w.text for w in body)
            if not desc and di:
                # "Checks paid" rows print only the check number: name it like the CSV export does
                desc = f"CHECK {words[0].text.rstrip('*^')}"
            desc_parts = [desc]
            r = _classify(words, money_idx, st, is_card, prev_balance)
            if r is None:
                st.skipped += 1
                continue
            amount, direction, balance, signed = r
            if amount == 0:
                continue
            if balance is not None:
                prev_balance = balance
            rows.append(_Raw(d, desc_parts, amount, direction, balance, signed, line.page, line.top,
                             words[0].x0))
            continue
        # continuation of the previous description
        if rows and not money_idx and not dm and not _STOP_LINE.match(text):
            last = rows[-1]
            if (last.page == line.page and 0 < line.top - last.top <= 14 * (last.cont + 1) + 2
                    and line.x0 > last.date_x0 + 8 and last.cont < 4):
                last.desc_parts.append(text)
                last.cont += 1
                last.top = line.top - 0.0
                continue

    # fill directions decided by balance delta / sign
    _resolve_unknown(rows, st, is_card, beginning)
    info = {"start": start, "end": end, "is_card": is_card, "beginning": beginning}

    parsed_in = sum((r.amount for r in rows if r.direction == Direction.IN), Decimal(0))
    parsed_out = sum((r.amount for r in rows if r.direction == Direction.OUT), Decimal(0))
    card_si, card_so = _summary_totals(full_text, True) if is_card else (None, None)
    if card_si is not None:
        # a card's account summary covers all activity; "Total fees for this period" lines don't
        st.stated_in, st.have_stated_in = card_si, True
        st.stated_out, st.have_stated_out = card_so, True
    if not (st.have_stated_in or st.have_stated_out):
        si, so = _summary_totals(full_text, is_card)
        if si is not None:
            st.stated_in, st.have_stated_in = si, True
        if so is not None:
            st.stated_out, st.have_stated_out = so, True
    checked, mismatch = st.have_stated_in or st.have_stated_out, False
    # how much of the statement the parsed rows fail to account for (shown next to "doesn't match")
    difference = (abs(st.stated_in - parsed_in) if st.have_stated_in else Decimal(0)) + \
        (abs(st.stated_out - parsed_out) if st.have_stated_out else Decimal(0))
    if st.have_stated_in and abs(st.stated_in - parsed_in) > Decimal("0.01"):
        mismatch = True
        warnings.append(f"reconciliation: parsed money-in {parsed_in} differs from statement total {st.stated_in}")
    if st.have_stated_out and abs(st.stated_out - parsed_out) > Decimal("0.01"):
        mismatch = True
        warnings.append(f"reconciliation: parsed money-out {parsed_out} differs from statement total {st.stated_out}")
    if not (st.have_stated_in or st.have_stated_out):
        ending = _find_amount(full_text, r"ending balance(?: on \d{1,2}/\d{1,2})?|closing balance|new balance")
        if beginning is not None and ending is not None and rows and not is_card:
            checked = True
            difference = abs(beginning + parsed_in - parsed_out - ending)
            if difference > Decimal("0.01"):
                mismatch = True
                warnings.append("reconciliation: beginning balance + parsed activity does not equal ending balance")
    # True = parsed totals match what the statement prints; None = nothing to check against
    info["reconciled"] = (not mismatch) if checked and rows else None
    info["difference"] = difference if info["reconciled"] is not None else None
    if st.used_sign and info["reconciled"] is not True:
        warnings.append(f"{st.used_sign} transaction(s): direction inferred from the amount sign only — please review")
    if st.skipped:
        warnings.append(f"skipped {st.skipped} line(s) that looked like transactions but could not be parsed")
    if not rows:
        warnings.append("no transactions found in PDF (scanned image or unsupported layout?)")
    info.update(parsed_in=parsed_in, parsed_out=parsed_out,
                stated_in=st.stated_in if st.have_stated_in else None,
                stated_out=st.stated_out if st.have_stated_out else None,
                method=("columns" if st.used_columns else "sections" if st.used_sections
                        else "balance" if st.used_balance else "sign"))
    return rows, warnings, info


def _trailing_money(words: list[Word]) -> list[int]:
    """Indices of the money tokens at the end of a line (up to 3)."""
    idx: list[int] = []
    for i in range(len(words) - 1, 0, -1):
        if _money(words[i].text) is not None:
            idx.append(i)
            if len(idx) == 3:
                break
        else:
            break
    return sorted(idx)


def _classify(words, money_idx, st: _State, is_card: bool, prev_balance):
    """-> (amount, direction|None, balance|None, signed) or None."""
    cols = st.columns
    vals = [(words[i], _money(words[i].text)) for i in money_idx]
    if st.mode == "table" and cols is not None and cols.split:
        amt_in = amt_out = bal = None
        for w, v in vals:
            cands = [(abs(w.x1 - x), name) for name, x in
                     (("in", cols.credit), ("out", cols.debit), ("bal", cols.balance)) if x is not None]
            dist, name = min(cands)
            if dist > 45:
                continue
            if name == "in":
                amt_in = abs(v)
            elif name == "out":
                amt_out = abs(v)
            else:
                bal = v
        if amt_in is None and amt_out is None:
            return None
        st.used_columns = True
        if amt_in is not None and amt_out is not None:
            net = amt_in - amt_out
            return abs(net), (Direction.IN if net > 0 else Direction.OUT), bal, net
        if amt_in is not None:
            return amt_in, Direction.IN, bal, amt_in
        return amt_out, Direction.OUT, bal, -amt_out
    first = vals[0][1]
    balance = vals[-1][1] if len(vals) >= 2 else None
    if cols is not None and cols.balance is not None and len(vals) >= 2:
        if abs(vals[-1][0].x1 - cols.balance) > 45:
            balance = None
    if st.mode in ("in", "out"):
        return abs(first), (Direction.IN if st.mode == "in" else Direction.OUT), balance, first
    # unknown direction: decide later by balance delta or sign
    return abs(first), None, balance, first


def _resolve_unknown(rows: list[_Raw], st: _State, is_card: bool, beginning: Optional[Decimal]) -> None:
    prev = beginning
    for r in rows:
        if r.direction is not None:
            if r.balance is not None:
                prev = r.balance
            continue
        if r.balance is not None and prev is not None:
            delta = r.balance - prev
            if abs(abs(delta) - r.amount) <= Decimal("0.01"):
                r.direction = Direction.IN if delta > 0 else Direction.OUT
                if is_card:   # card balance up = money spent
                    r.direction = Direction.OUT if delta > 0 else Direction.IN
                st.used_balance = True
                prev = r.balance
                continue
        if r.balance is not None:
            prev = r.balance
        st.used_sign += 1
        neg = (r.signed or 0) < 0
        if is_card:
            r.direction = Direction.IN if neg else Direction.OUT
        else:
            r.direction = Direction.OUT if neg else Direction.IN


def _take_totals(line: Line, st: _State) -> None:
    words = line.words
    idx = _trailing_money(words)
    if not idx:
        return
    text = line.text.lower()
    cols = st.columns
    if st.mode == "table" and cols is not None and cols.split:
        for i in idx:
            w, v = words[i], abs(_money(words[i].text))
            dc, dd = abs(w.x1 - cols.credit), abs(w.x1 - cols.debit)
            if min(dc, dd) > 45:
                continue
            if dc < dd:
                st.stated_in += v
                st.have_stated_in = True
            else:
                st.stated_out += v
                st.have_stated_out = True
        return
    v = abs(_money(words[idx[0]].text))
    if st.mode == "in" or re.search(r"deposits|additions|credits|payments", text):
        if st.mode != "out":
            st.stated_in += v
            st.have_stated_in = True
            return
    if st.mode == "out" or re.search(r"withdrawals|fees|checks|purchases|debits|interest charged", text):
        st.stated_out += v
        st.have_stated_out = True


_AMT = r"-?\s*\$?\s*([\d,]+\.\d{2})"


def _find_amount(text: str, label: str) -> Optional[Decimal]:
    m = re.search(rf"(?:{label})\s*:?\s*{_AMT}", text, re.I)
    return Decimal(m.group(1).replace(",", "")) if m else None


# card summaries sign their amounts: "Payment, Credits -$500.00", "Purchases +$1,234.56"
_SAMT = r"[-+]?\s*\$?\s*([\d,]+\.\d{2})"
_CARD_IN_SUMMARY = re.compile(rf"^\s*-?\s*payments?\s*(?:&|and|,)\s*(?:other\s*)?credits\s*{_SAMT}(?![\d%])",
                              re.I | re.M)
_CARD_OUT_SUMMARY = re.compile(
    rf"^\s*\+?\s*(?:purchases(?:\s*(?:&|and)\s*(?:other\s*charges|adjustments))?|balance\s*transfers?|"
    rf"cash\s*advances?|fees?\s*charged|interest\s*charged)\s*{_SAMT}(?![\d%])", re.I | re.M)


def _summary_totals(text: str, is_card: bool = False) -> tuple[Optional[Decimal], Optional[Decimal]]:
    si = so = None
    if is_card:
        # card account summary: "- Payments & Credits $X", "+ Purchases & Other Charges $Y", ...
        ins = _CARD_IN_SUMMARY.findall(text)
        outs = _CARD_OUT_SUMMARY.findall(text)
        if ins and outs:
            return (Decimal(ins[0].replace(",", "")),
                    sum((Decimal(x.replace(",", "")) for x in outs), Decimal(0)))
    m = re.search(rf"^\s*(?:deposits/credits|deposits and (?:other )?(?:additions|credits)|total deposits)\s+(?:\d+\s+)?{_AMT}",
                  text, re.I | re.M)
    if m:
        si = Decimal(m.group(1).replace(",", ""))
    outs = re.findall(rf"^\s*(?:withdrawals/debits|electronic withdrawals|atm & debit card withdrawals|checks paid|"
                      rf"fees|other withdrawals|total withdrawals)\s+(?:\d+\s+)?{_AMT}", text, re.I | re.M)
    if outs:
        so = sum((Decimal(x.replace(",", "")) for x in outs), Decimal(0))
    return si, so


# --------------------------------------------------------------------------- public API

def parse_pdf(data: bytes, account_id: str, import_id: str) -> tuple[list[Transaction], list[str]]:
    """Parse a PDF statement into transactions + warnings (see module docstring)."""
    txns, warnings, _info = parse_pdf_detailed(data, account_id, import_id)
    return txns, warnings


def parse_pdf_detailed(data: bytes, account_id: str,
                       import_id: str) -> tuple[list[Transaction], list[str], dict]:
    """Like parse_pdf, plus info: {"reconciled": True | False | None, "start", "end", ...}."""
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover
        return [], ["pdfplumber is not installed"], {"reconciled": None}
    lines: list[Line] = []
    texts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for pi, page in enumerate(pdf.pages):
                texts.append(page.extract_text() or "")
                words = page.extract_words(x_tolerance=1.5, y_tolerance=2.5, keep_blank_chars=False)
                lines.extend(words_to_lines(words, pi))
    except Exception as e:  # corrupt / encrypted
        return [], [f"could not read PDF ({type(e).__name__})"], {"reconciled": None}
    full_text = "\n".join(texts)
    if not full_text.strip():
        return [], ["PDF has no text layer (scanned image?) — OCR it or export CSV from the bank"], {"reconciled": None}
    rows, warnings, info = parse_lines(lines, full_text)
    txns = []
    for seq, r in enumerate(rows):
        desc = " ".join(" ".join(r.desc_parts).split()) or "(no description)"
        txns.append(Transaction(account_id=account_id, date=r.date, description=desc, amount=r.amount,
                                direction=r.direction, import_id=import_id,
                                merchant=normalize_merchant(desc), seq=seq))
    out = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
           for k, v in info.items() if k in ("reconciled", "start", "end")}
    if info.get("difference") is not None:
        out["difference"] = str(info["difference"].quantize(Decimal("0.01")))
    return txns, warnings, out
