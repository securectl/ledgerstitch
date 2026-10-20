# SPDX-License-Identifier: AGPL-3.0-only
"""Statement format detection.

`detect(filename, data)` returns a `FormatProfile` describing how to read a
bank/card export: which institution, whether it is a credit card or a bank
account, and (for CSVs) the column mapping and sign convention. Known bank
layouts are matched by their header row; anything else goes through a
heuristic column scorer. Filename hints ("Chase4012_Activity...",
"9501-Credit Card...") supply institution, last-4 and account kind.

This module also hosts small parsing helpers shared by csv_parser and
pdf_parser (money/date parsing, decoding, header location).
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from ..models import AccountKind

__all__ = [
    "ColumnMapping", "FormatProfile", "detect", "KNOWN_PROFILES",
    "parse_money", "parse_date", "infer_date_format", "decode_bytes", "read_rows",
    "find_header", "filename_hints", "PAYMENT_RE", "sniff_format",
]


# --------------------------------------------------------------------------- contract types

@dataclass
class ColumnMapping:
    date: str
    description: str
    amount: str | None = None          # single signed column
    debit: str | None = None           # or split columns
    credit: str | None = None
    type_col: str | None = None        # e.g. Chase "Type" / CapOne "Transaction"
    category: str | None = None
    date_format: str | None = None     # strptime; None = auto
    negative_is_out: bool = True       # sign convention of `amount`
    card: str | None = None            # optional per-row card number column (last4 of the physical card)
    status: str | None = None          # optional Posted/Pending column; pending rows are skipped
    memo: str | None = None            # optional memo column (description fallback)
    balance: str | None = None         # optional running-balance column (used for sign/learning checks)


@dataclass
class FormatProfile:
    id: str
    institution: str
    kind: AccountKind
    mapping: ColumnMapping | None
    confidence: float
    account_hint: str | None           # e.g. last4 found in filename/content
    # --- extras (optional, ingestion-internal) ---
    header_row: int | None = None      # index of the header row in the parsed CSV rows (-1 = headerless)
    notes: list[str] = field(default_factory=list)
    format: str = "CSV"                # CSV | XLSX | PDF | OFX | QFX | QBO | QIF | CAMT.053 | MT940 | BAI2
    recognition: dict | None = None    # {"source": builtin|catalog|learned|inferred|pack, "format", ...}
    catalog_id: str | None = None      # known_formats.json entry (drives payment-platform semantics)


# --------------------------------------------------------------------------- helpers

_MONEY_RE = re.compile(
    r"^\(?\s*([+-])?\s*\(?\s*\$?\s*([+-])?\s*(\d{1,3}(?:,\d{3})+|\d+)?(\.\d+)?\s*\)?\s*(CR|DR|-)?\s*$",
    re.I,
)


def parse_money(value) -> Optional[Decimal]:
    """Parse "$1,234.56", "(12.00)", "-5", "12.00-", "12.00 CR" → signed Decimal.

    Returns None for blanks / non-money text. Parentheses, a leading/trailing
    minus and a trailing "CR" mean negative.
    """
    if value is None:
        return None
    s = str(value).strip().replace("−", "-").replace("USD", "").strip()
    if not s:
        return None
    m = _MONEY_RE.match(s)
    if not m or (m.group(3) is None and m.group(4) is None):
        return None
    whole = (m.group(3) or "0").replace(",", "")
    frac = m.group(4) or ""
    try:
        v = Decimal(whole + frac)
    except InvalidOperation:
        return None
    neg = (m.group(1) == "-" or m.group(2) == "-" or "(" in s and ")" in s
           or (m.group(5) or "").upper() in ("CR", "-"))
    return -v if neg else v


_DATE_FORMATS = [
    "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y/%m/%d", "%m-%d-%Y", "%m-%d-%y",
    "%d/%m/%Y", "%d/%m/%y", "%d-%b-%Y", "%d-%b-%y", "%b %d, %Y", "%b %d %Y",
    "%d %b %Y", "%B %d, %Y", "%Y%m%d", "%m.%d.%Y", "%d.%m.%Y",
]


def parse_date(value: str, fmt: str | None = None) -> Optional[date]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.split("T")[0] if re.match(r"^\d{4}-\d{2}-\d{2}T", s) else s
    s = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*(AM|PM)?(\s*(?:UTC|GMT|Z|[A-Z]{2,4}T|[+-]\d{2}:?\d{2}))?$",
               "", s, flags=re.I)
    fmts = [fmt] if fmt else _DATE_FORMATS
    for f in fmts:
        try:
            return datetime.strptime(s, f).date()
        except (ValueError, TypeError):
            continue
    return None


def infer_date_format(values: list[str]) -> Optional[str]:
    """Pick the strptime format that parses the most sample values.

    US month-first wins ties; day-first only wins when some value has a
    first component > 12.
    """
    vals = [v.strip() for v in values if v and v.strip()]
    if not vals:
        return None
    best, best_n = None, 0
    for f in _DATE_FORMATS:
        n = 0
        for v in vals:
            try:
                datetime.strptime(re.sub(r"\s+\d{1,2}:\d{2}.*$", "", v), f)
                n += 1
            except ValueError:
                pass
        if n > best_n:
            best, best_n = f, n
    return best if best_n else None


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_rows(data: bytes) -> list[list[str]]:
    """All CSV rows, cells stripped, BOM removed, blank rows kept as []."""
    text = decode_bytes(data).replace("\r\n", "\n").replace("\r", "\n")
    sample = text[:4096]
    delim = ","
    try:
        if sample.count(",") < sample.count(";") or sample.count(",") < sample.count("\t"):
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # preamble lines confuse the sniffer: take the dominant non-comma delimiter
        delim = max(";\t|", key=sample.count)
    rows = []
    for r in csv.reader(io.StringIO(text), delimiter=delim):
        cells = [c.strip().lstrip("﻿").strip() for c in r]
        if not any(cells):
            rows.append([])
            continue
        rows.append(cells)
    return rows


def _norm_header(h: str) -> str:
    return " ".join(h.strip().strip('"').lower().split())


def _is_date(s: str) -> bool:
    return bool(s) and parse_date(s) is not None


def _is_money(s: str) -> bool:
    return bool(s) and parse_money(s) is not None and bool(re.search(r"\d", s))


# --------------------------------------------------------------------------- filename hints

_INSTITUTION_PATTERNS: list[tuple[str, str]] = [
    (r"chase|jpmorgan", "Chase"),
    (r"citi(?!zen)|citibank", "Citi"),
    (r"capital[\s_-]?one|capone|cap[\s_-]?venture|venture|quicksilver|savor|\b360\b", "Capital One"),
    (r"amex|american[\s_-]?express", "American Express"),
    (r"bofa|bank[\s_-]?of[\s_-]?america|\bboa\b", "Bank of America"),
    (r"wells[\s_-]?fargo|wellsfargo|\bwf\b", "Wells Fargo"),
    (r"robinhood", "Robinhood"),
    (r"discover", "Discover"),
    (r"us[\s_-]?bank|usbank", "US Bank"),
    (r"apple[\s_-]?card|\bapple\b", "Apple Card"),
    (r"gemini", "Gemini"),
]

_CARD_WORDS = re.compile(
    r"credit[\s_-]?card|\bcard\b|visa|mastercard|master[\s_-]?card|venture|quicksilver|savor|freedom|"
    r"sapphire|\bink\b|amex|discover|apple[\s_-]?card|gemini|platinum|rewards", re.I)
_BANK_WORDS = re.compile(r"checking|savings|\bdda\b|360|business[\s_-]?complete|everyday", re.I)


def filename_hints(filename: str) -> dict:
    """{"institution", "last4", "kind"} guessed from a file name (any may be None)."""
    name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", filename or "")
    out: dict = {"institution": None, "last4": None, "kind": None}
    low = name.lower()
    for pat, inst in _INSTITUTION_PATTERNS:
        if re.search(pat, low):
            out["institution"] = inst
            break
    last4 = None
    # institution name glued to 4 digits: Chase4012, Citi-0505, ending in 1234, x1234, ...1234
    m = re.search(r"(?:chase|citi|amex|discover|capone|wf|boa|bofa|usbank|apple|gemini)[\s_-]?(\d{4})(?!\d)", low)
    if not m:
        m = re.search(r"(?:ending(?:\s+in)?|acct|account|card|x|\.\.\.|\*{2,}|#)[\s_-]*(\d{4})(?!\d)", low)
    if m and re.search(r"[a-z][\s_-]\d{4}", m.group(0)) and 1990 <= int(m.group(1)) <= 2099:
        m = None   # "GEMINI-2025-statement": a year, not a card number
    if m:
        last4 = m.group(1)
    else:
        # download ids ("ccc24101-7235-419d-...") are not card numbers
        bare = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,}", " ", name, flags=re.I)
        for m in re.finditer(r"(?<!\d)(\d{4})(?!\d)", bare):
            n = int(m.group(1))
            # skip things that look like years inside date stamps
            if 1990 <= n <= 2099:
                continue
            last4 = m.group(1)
            break
    out["last4"] = last4
    if _CARD_WORDS.search(name):
        out["kind"] = AccountKind.CREDIT_CARD
    elif _BANK_WORDS.search(name):
        out["kind"] = AccountKind.SAVINGS if "saving" in low else AccountKind.CHECKING
    return out


# --------------------------------------------------------------------------- known profiles

@dataclass
class _Known:
    id: str
    institution: str
    kind: AccountKind | None           # None = decide from hints/content
    required: tuple[str, ...]          # normalized header names that must all be present
    mapping: Callable[[list[str]], ColumnMapping]
    forbidden: tuple[str, ...] = ()
    check: Callable[[list[str], list[list[str]], str], bool] | None = None  # header, rows, filename
    last4_col: str | None = None


def _col(header: list[str], *names: str) -> str | None:
    """Return the actual header cell matching any of `names` (normalized)."""
    norm = {_norm_header(h): h for h in header}
    for n in names:
        n = _norm_header(n)
        if n in norm:
            return norm[n]
    return None


def _m(**kw) -> Callable[[list[str]], ColumnMapping]:
    def build(header: list[str]) -> ColumnMapping:
        args = {}
        for k, v in kw.items():
            if isinstance(v, tuple):
                args[k] = _col(header, *v)
            elif isinstance(v, str) and k not in ("date_format",):
                args[k] = _col(header, v)
            else:
                args[k] = v
        return ColumnMapping(**args)
    return build


def _iso_dates(header, rows, fn) -> bool:
    i = [_norm_header(h) for h in header].index("date")
    vals = [r[i] for r in rows if len(r) > i and r[i]][:20]
    return bool(vals) and all(re.match(r"^\d{4}-\d{2}-\d{2}", v) for v in vals)


def _capone_like(header, rows, fn) -> bool:
    h = filename_hints(fn)
    if h["institution"] == "Capital One":
        return True
    if h["institution"] == "US Bank":
        return False
    return _iso_dates(header, rows, fn)


def _usbank_like(header, rows, fn) -> bool:
    return not _capone_like(header, rows, fn)


KNOWN_PROFILES: list[_Known] = [
    _Known("chase_card", "Chase", AccountKind.CREDIT_CARD,
           ("transaction date", "post date", "description", "type", "amount"),
           _m(date="Transaction Date", description="Description", amount="Amount", type_col="Type",
              category="Category", card="Card", negative_is_out=True),
           forbidden=("clearing date",), last4_col="Card"),
    _Known("chase_checking", "Chase", AccountKind.CHECKING,
           ("details", "posting date", "description", "amount", "type", "balance"),
           _m(date="Posting Date", description="Description", amount="Amount", type_col="Type",
              negative_is_out=True)),
    _Known("citi", "Citi", AccountKind.CREDIT_CARD,
           ("status", "date", "description", "debit", "credit"),
           _m(date="Date", description="Description", debit="Debit", credit="Credit")),
    _Known("capital_one_card", "Capital One", AccountKind.CREDIT_CARD,
           ("transaction date", "posted date", "card no.", "description", "debit", "credit"),
           _m(date="Transaction Date", description="Description", debit="Debit", credit="Credit",
              category="Category", card="Card No."), last4_col="Card No."),
    _Known("capital_one", "Capital One", None,
           ("date", "transaction", "name", "memo", "amount"),
           _m(date="Date", description="Name", amount="Amount", type_col="Transaction", negative_is_out=True),
           check=_capone_like),
    _Known("us_bank", "US Bank", None,
           ("date", "transaction", "name", "memo", "amount"),
           _m(date="Date", description="Name", amount="Amount", type_col="Transaction", negative_is_out=True),
           check=_usbank_like),
    _Known("capital_one_360", "Capital One", None,
           ("transaction date", "transaction amount", "transaction type", "transaction description"),
           _m(date="Transaction Date", description="Transaction Description", amount="Transaction Amount",
              type_col="Transaction Type", negative_is_out=True)),
    _Known("gemini", "Gemini", AccountKind.CREDIT_CARD,
           ("reference number", "transaction post date", "description of transaction", "transaction type", "amount"),
           _m(date="Transaction Post Date", description="Description of Transaction", amount="Amount",
              type_col="Transaction Type", date_format="%m/%d/%y", negative_is_out=False)),
    _Known("apple_card", "Apple Card", AccountKind.CREDIT_CARD,
           ("transaction date", "clearing date", "description", "merchant", "type", "amount (usd)"),
           _m(date="Transaction Date", description="Description", amount="Amount (USD)", type_col="Type",
              category="Category", negative_is_out=False)),
    # Robinhood Gold Card: positive = charge; "Cardholder" holds a person's name and is never used
    _Known("robinhood_card", "Robinhood", AccountKind.CREDIT_CARD,
           ("date", "cardholder", "amount", "points", "status", "type", "merchant"),
           _m(date="Date", description="Merchant", amount="Amount", type_col="Type", status="Status",
              memo="Description", date_format="%Y-%m-%d", negative_is_out=False)),
    _Known("discover", "Discover", AccountKind.CREDIT_CARD,
           ("trans. date", "post date", "description", "amount", "category"),
           _m(date="Trans. Date", description="Description", amount="Amount", category="Category",
              negative_is_out=False)),
    _Known("boa_card", "Bank of America", AccountKind.CREDIT_CARD,
           ("posted date", "reference number", "payee", "address", "amount"),
           _m(date="Posted Date", description="Payee", amount="Amount", negative_is_out=True)),
    _Known("boa_checking", "Bank of America", AccountKind.CHECKING,
           ("date", "description", "amount", "running bal."),
           _m(date="Date", description="Description", amount="Amount", negative_is_out=True)),
    _Known("amex_extended", "American Express", AccountKind.CREDIT_CARD,
           ("date", "description", "amount", "extended details"),
           _m(date="Date", description="Description", amount="Amount", category="Category",
              negative_is_out=False)),
    _Known("amex", "American Express", AccountKind.CREDIT_CARD,
           ("date", "description", "card member", "account #", "amount"),
           _m(date="Date", description="Description", amount="Amount", negative_is_out=False),
           last4_col="Account #"),
    _Known("amex", "American Express", AccountKind.CREDIT_CARD,
           ("date", "description", "amount"),
           _m(date="Date", description="Description", amount="Amount", negative_is_out=False),
           # The bare 3-column layout is shared by many banks: only claim it as Amex
           # (positive = charge) when the file name says so; otherwise the generic
           # detector infers the sign convention from content.
           check=lambda h, r, fn: len(h) <= 4 and filename_hints(fn)["institution"] == "American Express"),
]


def _match_known(header: list[str], rows: list[list[str]], filename: str) -> Optional[_Known]:
    norm = {_norm_header(h) for h in header if h}
    best, best_n = None, 0
    for k in KNOWN_PROFILES:
        if not set(k.required) <= norm:
            continue
        if any(f in norm for f in k.forbidden):
            continue
        if k.check and not k.check(header, rows, filename):
            continue
        if len(k.required) > best_n:
            best, best_n = k, len(k.required)
    return best


def find_header(rows: list[list[str]], filename: str = "", max_scan: int = 40):
    """Locate the header row. Returns (index, known_profile_or_None).

    Skips leading junk (e.g. Bank of America's summary block). Returns
    (-1, None) for headerless files whose first row already is data.
    """
    for i, r in enumerate(rows[:max_scan]):
        if len(r) < 2:
            continue
        k = _match_known(r, rows[i + 1:i + 30], filename)
        if k:
            return i, k
    # generic: first row with >=2 non-empty, non-date, non-money cells followed by data rows
    for i, r in enumerate(rows[:max_scan]):
        cells = [c for c in r if c]
        if len(cells) < 2:
            continue
        if any(_is_date(c) for c in cells) or sum(_is_money(c) for c in cells) >= 1:
            # looks like data → headerless file if the following rows have the same shape
            nxt = [x for x in rows[i:i + 5] if x]
            if len(nxt) >= 1 and all(len(x) == len(r) for x in nxt):
                return -1, None
            continue
        nxt = [x for x in rows[i + 1:i + 6] if x]
        # a preamble line ("User,<name>,<id>") is narrower than the table below it
        if nxt and len(r) < max(len(x) for x in nxt) - 1:
            continue
        if nxt and sum(any(_is_date(c) for c in x) for x in nxt) >= max(1, len(nxt) // 2):
            return i, None
    return None, None


# --------------------------------------------------------------------------- content heuristics

PAYMENT_RE = re.compile(
    r"PAYMENT\s*-?\s*THANK|THANK\s*YOU|AUTOPAY|AUTO[\s-]?PAY|AUTOMATIC PAYMENT|ONLINE PAYMENT|MOBILE PAYMENT|"
    r"INTERNET PAYMENT|E-?PAYMENT|MOBILE PYMT|ONLINE PYMT|PAYMENT RECEIVED|PAYMENT TRANSACTION|"
    r"^PAYMENT\b|\bPYMT\b|CRCARDPMT|DIRECTPAY", re.I)

_CHECKING_IN_RE = re.compile(r"PAYROLL|DIRECT DEP|DEPOSIT|ZELLE FROM|INTEREST PAID|ATM", re.I)


def _guess_kind_from_rows(desc: list[str], signed: list[Decimal], has_balance: bool) -> tuple[AccountKind, float]:
    if has_balance:
        return AccountKind.CHECKING, 0.7
    pay = sum(1 for d in desc if PAYMENT_RE.search(d or ""))
    dep = sum(1 for d in desc if _CHECKING_IN_RE.search(d or ""))
    if pay > dep and pay >= 1:
        return AccountKind.CREDIT_CARD, 0.6
    if dep > pay:
        return AccountKind.CHECKING, 0.6
    return AccountKind.CHECKING, 0.4


def _sign_convention_for_card(desc: list[str], signed: list[Decimal]) -> tuple[bool, str]:
    """Infer `negative_is_out` for a card file from payment rows, else majority sign."""
    pay_signs = [a for d, a in zip(desc, signed) if a is not None and a != 0 and PAYMENT_RE.search(d or "")]
    if pay_signs:
        pos = sum(1 for a in pay_signs if a > 0)
        neg = len(pay_signs) - pos
        if pos != neg:
            # payments are money *in* to the card
            return (pos > neg), "sign convention inferred from payment rows"
    nz = [a for a in signed if a is not None and a != 0]
    neg = sum(1 for a in nz if a < 0)
    # most rows on a card are purchases (money out)
    return (neg >= len(nz) - neg), "sign convention inferred from majority sign"


# --------------------------------------------------------------------------- generic scorer

_DATE_NAME = re.compile(r"date|posted|time", re.I)
_DESC_NAME = re.compile(r"desc|payee|merchant|name|memo|narrative|details|particulars|transaction$", re.I)
_AMT_NAME = re.compile(r"amount|amt|value|sum", re.I)
_DEBIT_NAME = re.compile(r"debit|withdraw|money out|paid out|outflow|charge|spent|payment(?!s? received)", re.I)
_CREDIT_NAME = re.compile(r"credit|deposit|money in|paid in|inflow|received", re.I)
_BAL_NAME = re.compile(r"balance|running|\bbal\b", re.I)
_TYPE_NAME = re.compile(r"^type$|transaction type|^dr/?cr$|debit/credit|details", re.I)
_CAT_NAME = re.compile(r"categor", re.I)


def _generic_mapping(header: list[str], rows: list[list[str]], headerless: bool) -> tuple[ColumnMapping | None, dict]:
    width = max((len(r) for r in rows[:50]), default=len(header))
    if headerless:
        header = [f"col{i}" for i in range(width)]
    cols = list(range(len(header)))
    sample = [r for r in rows[:200] if r]
    stats = {}
    for i in cols:
        vals = [r[i] for r in sample if len(r) > i and r[i]]
        n = max(1, len(vals))
        stats[i] = {
            "date": sum(_is_date(v) for v in vals) / n,
            "money": sum(_is_money(v) for v in vals) / n,
            "alpha": sum(bool(re.search(r"[A-Za-z]{3}", v)) for v in vals) / n,
            "len": sum(len(v) for v in vals) / n,
            "uniq": len(set(vals)) / n,
            "fill": len(vals) / max(1, len(sample)),
            "neg": sum(1 for v in vals if (parse_money(v) or 0) < 0) / n,
        }
    name = {i: header[i] if i < len(header) else "" for i in cols}

    def pick(cands, key):
        cands = list(cands)
        return max(cands, key=key) if cands else None

    date_c = pick([i for i in cols if stats[i]["date"] >= 0.6],
                  lambda i: (stats[i]["date"], bool(_DATE_NAME.search(name[i])),
                             "trans" in name[i].lower(), -i))
    if date_c is None:
        return None, {}
    bal_c = [i for i in cols if _BAL_NAME.search(name[i])]
    money_cols = [i for i in cols if i != date_c and i not in bal_c and stats[i]["money"] >= 0.3
                  and stats[i]["date"] < 0.5 and stats[i]["alpha"] < 0.3]
    debit_c = pick([i for i in money_cols if _DEBIT_NAME.search(name[i]) and not _CREDIT_NAME.search(name[i])],
                   lambda i: stats[i]["money"])
    credit_c = pick([i for i in money_cols if _CREDIT_NAME.search(name[i]) and i != debit_c
                     and not _DEBIT_NAME.search(name[i])], lambda i: stats[i]["money"])
    amount_c = None
    if not (debit_c is not None and credit_c is not None):
        debit_c = credit_c = None
        named = [i for i in money_cols if _AMT_NAME.search(name[i])]
        amount_c = pick(named or [i for i in money_cols if stats[i]["fill"] >= 0.8] or money_cols,
                        lambda i: (stats[i]["fill"], stats[i]["money"], -i))
        if amount_c is None:
            return None, {}
        # headerless split columns: two sparse money columns that never co-occur
        if headerless:
            sparse = [i for i in money_cols if 0.05 < stats[i]["fill"] < 0.95]
            if len(sparse) == 2:
                a, b = sparse
                both = sum(1 for r in sample if len(r) > b and r[a] and r[b])
                if both == 0:
                    debit_c, credit_c, amount_c = a, b, None
    text_cols = [i for i in cols if i not in (date_c, amount_c, debit_c, credit_c) and i not in bal_c
                 and stats[i]["alpha"] >= 0.5 and stats[i]["date"] < 0.5]
    type_c = pick([i for i in text_cols if _TYPE_NAME.search(name[i]) and stats[i]["uniq"] < 0.3],
                  lambda i: -stats[i]["uniq"])
    cat_c = pick([i for i in text_cols if _CAT_NAME.search(name[i])], lambda i: stats[i]["fill"])
    desc_cands = [i for i in text_cols if i not in (type_c, cat_c)]
    desc_c = pick(desc_cands, lambda i: (bool(_DESC_NAME.search(name[i])) * 0.5 + stats[i]["uniq"]
                                         + min(stats[i]["len"], 40) / 40, -i))
    if desc_c is None:
        return None, {}
    mapping = ColumnMapping(
        date=name[date_c], description=name[desc_c],
        amount=name[amount_c] if amount_c is not None else None,
        debit=name[debit_c] if debit_c is not None else None,
        credit=name[credit_c] if credit_c is not None else None,
        type_col=name[type_c] if type_c is not None else None,
        category=name[cat_c] if cat_c is not None else None,
    )
    info = {"has_balance": bool(bal_c), "date_col": date_c, "desc_col": desc_c, "amount_col": amount_c,
            "header": header}
    return mapping, info


def _column_values(header: list[str], rows: list[list[str]], col: str | None) -> list[str]:
    if col is None or col not in header:
        return []
    i = header.index(col)
    return [r[i] if len(r) > i else "" for r in rows]


# --------------------------------------------------------------------------- PDF detection

_PDF_INSTITUTIONS = [
    (r"wells\s*fargo", "Wells Fargo"), (r"jpmorgan\s*chase|chase\.com|\bchase\b", "Chase"),
    (r"bank of america", "Bank of America"), (r"citibank|\bciti\b", "Citi"),
    (r"capital\s*one", "Capital One"), (r"american express", "American Express"),
    (r"discover(?!y)", "Discover"), (r"robinhood", "Robinhood"), (r"u\.?s\.? bank", "US Bank"), (r"goldman sachs|apple card", "Apple Card"),
]
_PDF_CARD_RE = re.compile(r"minimum\s*payment\s*due|payment\s*due\s*date|credit\s*limit|new\s*balance|"
                          r"available\s*credit|credit\s*available|cash\s*advance", re.I)
# a credit limit next to a minimum payment is a card even when the fine print says "savings"
_PDF_STRONG_CARD_RE = re.compile(r"credit\s*limit", re.I)
_PDF_STRONG_CARD_RE2 = re.compile(r"minimum\s*payment\s*due", re.I)


def _detect_pdf(filename: str, data: bytes, hints: dict) -> FormatProfile:
    text = ""
    try:
        import pdfplumber  # local import: heavy
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:3])
    except Exception:  # unreadable/encrypted PDF: still return a profile
        text = ""
    inst = hints["institution"]
    if not inst and text:
        counts = Counter()
        for pat, name in _PDF_INSTITUTIONS:
            counts[name] += len(re.findall(pat, text, re.I))
        if counts and counts.most_common(1)[0][1] > 0:
            inst = counts.most_common(1)[0][0]
    kind = hints["kind"]
    if kind is None:
        if _PDF_STRONG_CARD_RE.search(text) and _PDF_STRONG_CARD_RE2.search(text):
            kind = AccountKind.CREDIT_CARD
        elif _PDF_CARD_RE.search(text) and not re.search(r"checking|savings", text, re.I):
            kind = AccountKind.CREDIT_CARD
        elif re.search(r"savings", text, re.I) and not re.search(r"checking", text, re.I):
            kind = AccountKind.SAVINGS
        elif text:
            kind = AccountKind.CHECKING
        else:
            kind = AccountKind.OTHER
    last4 = None
    m = re.search(r"account\s*(?:number|no\.?|#)?\s*:?\s*(?:[x*•.\s-]*)(\d[\d\s-]{2,}\d)", text, re.I)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) >= 4:
            last4 = digits[-4:]
    if not last4:
        m = re.search(r"(?:ending in|card ending|account ending)\s*:?\s*(\d{4})", text, re.I)
        if m:
            last4 = m.group(1)
    last4 = last4 or hints["last4"]
    return FormatProfile(id="pdf", institution=inst or "Unknown", kind=kind, mapping=None,
                         confidence=0.8 if text else 0.2, account_hint=last4)


# --------------------------------------------------------------------------- detect()

def _last4_from_col(header, rows, col) -> str | None:
    vals = [re.sub(r"\D", "", v) for v in _column_values(header, rows, _col(header, col.lower()) if col else None)]
    vals = [v[-4:] for v in vals if len(v) >= 4]
    if vals:
        v, n = Counter(vals).most_common(1)[0]
        return v
    return None


# --------------------------------------------------------------------------- standard formats

def sniff_format(filename: str, data: bytes) -> str:
    """File family by content (magic bytes / markers) first, extension second.

    Returns "PDF" | "OFX" | "QIF" | "CAMT" | "MT940" | "BAI2" | "XLSX" | "XLS" | "CSV".
    """
    head = data[:1024].lstrip()
    ext = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    if head.startswith(b"%PDF"):
        return "PDF"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "XLS"
    try:
        from .xlsx_parser import sniff_xlsx
        if data[:4] == b"PK\x03\x04" and sniff_xlsx(data):
            return "XLSX"
    except ImportError:  # pragma: no cover
        pass
    try:
        from .ofx_parser import sniff_ofx
        if sniff_ofx(data):
            return "OFX"
    except ImportError:  # pragma: no cover
        pass
    try:
        from .qif_parser import sniff_qif
        if sniff_qif(data):
            return "QIF"
    except ImportError:  # pragma: no cover
        pass
    try:
        from .iso20022_parser import sniff_iso
        k = sniff_iso(data)
        if k:
            return {"camt": "CAMT", "mt940": "MT940", "bai2": "BAI2"}[k]
    except ImportError:  # pragma: no cover
        pass
    if ext == "pdf":
        return "PDF"
    return "CSV"


def _institution_from_text(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    for pat, inst in _INSTITUTION_PATTERNS:
        if re.search(pat, low):
            return inst
    return None


def _catalog_institution(**keys) -> str | None:
    """Institution for an OFX INTU.BID / ORG / FID from the built-in catalog."""
    try:
        from .learning import load_catalog
        entries = load_catalog()
    except Exception:
        return None
    for field_, val in keys.items():
        if not val:
            continue
        for e in entries:
            ev = e.get(field_)
            if ev is not None and str(ev).strip().lower() == str(val).strip().lower() and e.get("institution"):
                return e["institution"]
    return None


def _kind(v) -> AccountKind:
    try:
        return AccountKind(v)
    except ValueError:
        return AccountKind.OTHER


def _detect_structured(fmt: str, filename: str, data: bytes, hints: dict) -> FormatProfile:
    notes: list[str] = []
    if fmt == "OFX":
        from .ofx_parser import ofx_info
        info = ofx_info(data, filename)
        accts = info.get("accounts") or []
        a = accts[0] if accts else {}
        inst = (_catalog_institution(intu_bid=info.get("intu_bid"), ofx_fid=info.get("fid"), ofx_org=info.get("org"))
                or _institution_from_text(info.get("org")) or hints["institution"]
                or (info.get("org") or "").strip() or "Unknown")
        kind = _kind(a.get("kind")) if a.get("kind") else (hints["kind"] or AccountKind.OTHER)
        if len({x.get("last4") for x in accts}) > 1:
            notes.append(f"file contains {len(accts)} accounts")
        p = FormatProfile(id=info.get("format", "OFX").lower(), institution=inst, kind=kind, mapping=None,
                          confidence=0.97, account_hint=a.get("last4") or hints["last4"], notes=notes,
                          format=info.get("format", "OFX"))
        p.recognition = {"source": "builtin", "format": p.format, "institution": inst, "needs_confirmation": False,
                         "intu_bid": info.get("intu_bid")}
        return p
    if fmt == "QIF":
        from .qif_parser import qif_info
        info = qif_info(data)
        kind = hints["kind"] or (_kind(info.get("kind")) if info.get("kind") else AccountKind.OTHER)
        p = FormatProfile(id="qif", institution=hints["institution"] or "Unknown", kind=kind, mapping=None,
                          confidence=0.9, account_hint=hints["last4"], format="QIF")
        p.recognition = {"source": "builtin", "format": "QIF", "institution": p.institution,
                         "needs_confirmation": False}
        return p
    if fmt in ("CAMT", "MT940", "BAI2"):
        from .iso20022_parser import iso_info
        info = iso_info(data)
        accts = info.get("accounts") or []
        inst = hints["institution"] or info.get("institution") or "Unknown"
        label = info.get("format") or fmt
        p = FormatProfile(id=label.lower().replace(".", ""), institution=inst,
                          kind=hints["kind"] or _kind(info.get("kind") or "checking"), mapping=None, confidence=0.9,
                          account_hint=(accts[0].get("last4") if accts else None) or hints["last4"],
                          notes=["corporate/enterprise format (basic support)"], format=label)
        p.recognition = {"source": "builtin", "format": label, "institution": inst, "needs_confirmation": False,
                         "enterprise": True}
        return p
    if fmt == "XLSX":
        from .xlsx_parser import detect_xlsx
        p = detect_xlsx(filename, data)
        p.format = "XLSX"
        if p.recognition:
            p.recognition["format"] = "XLSX"
        return p
    # XLS
    return FormatProfile(id="unknown", institution=hints["institution"] or "Unknown",
                         kind=hints["kind"] or AccountKind.OTHER, mapping=None, confidence=0.0,
                         account_hint=hints["last4"], notes=["legacy .xls is not supported; save as .xlsx or CSV"],
                         format="XLS")


def detect(filename: str, data: bytes) -> FormatProfile:
    """Identify the institution, account kind and column mapping of a statement file."""
    hints = filename_hints(filename)
    fmt = sniff_format(filename, data)
    if fmt == "PDF":
        p = _detect_pdf(filename, data, hints)
        p.format = "PDF"
        p.recognition = {"source": "builtin", "format": "PDF", "institution": p.institution,
                         "needs_confirmation": False}
        return p
    if fmt != "CSV":
        return _detect_structured(fmt, filename, data, hints)

    rows = read_rows(data)
    hidx, known = find_header(rows, filename)
    if known is not None:
        header = rows[hidx]
        body = [r for r in rows[hidx + 1:] if r]
        mapping = known.mapping(header)
        kind = known.kind
        notes = []
        if kind is None:
            if hints["kind"] is not None:
                kind = hints["kind"]
            else:
                desc = _column_values(header, body, mapping.description)
                kind, _ = _guess_kind_from_rows(desc, [], False)
                notes.append("account kind inferred from content")
        last4 = (_last4_from_col(header, body, known.last4_col) if known.last4_col else None) or hints["last4"]
        inst = known.institution
        p = FormatProfile(id=known.id, institution=inst, kind=kind, mapping=mapping, confidence=0.95,
                          account_hint=last4, header_row=hidx, notes=notes)
        p.recognition = {"source": "builtin", "format": "CSV", "institution": inst, "needs_confirmation": False}
        return p

    # --- headerless Wells Fargo: "date","-12.34","*","","DESCRIPTION"
    if hidx == -1:
        body = [r for r in rows if r]
        first = body[0] if body else []
        if (len(first) == 5 and _is_date(first[0]) and _is_money(first[1])
                and sum(1 for r in body[:30] if len(r) == 5 and r[2] in ("*", "")) >= min(len(body), 30) * 0.8):
            mapping = ColumnMapping(date="col0", description="col4", amount="col1", negative_is_out=True)
            kind = hints["kind"] or AccountKind.CHECKING
            p = FormatProfile(id="wells_fargo", institution="Wells Fargo", kind=kind, mapping=mapping,
                              confidence=0.85 if hints["institution"] in (None, "Wells Fargo") else 0.6,
                              account_hint=hints["last4"], header_row=-1)
            p.recognition = {"source": "builtin", "format": "CSV", "institution": "Wells Fargo",
                             "needs_confirmation": False}
            return p

    # --- built-in catalog of institution layouts (ingest/data/known_formats.json)
    cat = _catalog_profile(filename, rows, hidx, hints)
    if cat is not None:
        return cat

    # --- generic heuristic fallback
    if hidx is None:
        inf = _semantic(filename, rows, None)
        if inf is not None:
            return inf
        return FormatProfile(id="unknown", institution=hints["institution"] or "Unknown",
                             kind=hints["kind"] or AccountKind.OTHER, mapping=None, confidence=0.0,
                             account_hint=hints["last4"], header_row=None,
                             notes=["could not find a header row or transaction rows"])
    headerless = hidx == -1
    header = [f"col{i}" for i in range(max(len(r) for r in rows if r))] if headerless else rows[hidx]
    body = [r for r in (rows if headerless else rows[hidx + 1:]) if r]
    mapping, info = _generic_mapping(header, body, headerless)
    if mapping is None:
        inf = _semantic(filename, rows, hidx)
        if inf is not None:
            return inf
        return FormatProfile(id="unknown", institution=hints["institution"] or "Unknown",
                             kind=hints["kind"] or AccountKind.OTHER, mapping=None, confidence=0.0,
                             account_hint=hints["last4"], header_row=hidx,
                             notes=["no date/amount/description columns could be identified"])
    header = info["header"]
    desc = _column_values(header, body, mapping.description)
    notes = []
    if mapping.amount:
        signed = [parse_money(v) for v in _column_values(header, body, mapping.amount)]
    else:
        signed = []
    kind = hints["kind"]
    if kind is None:
        kind, _ = _guess_kind_from_rows(desc, signed, info["has_balance"])
        notes.append("account kind inferred from content")
    if mapping.amount and kind == AccountKind.CREDIT_CARD:
        mapping.negative_is_out, how = _sign_convention_for_card(desc, signed)
        notes.append(how)
    dates = _column_values(header, body, mapping.date)
    mapping.date_format = infer_date_format(dates[:200])
    conf = 0.5 if not headerless else 0.4
    p = FormatProfile(id="generic", institution=hints["institution"] or "Unknown", kind=kind,
                      mapping=mapping, confidence=conf, account_hint=hints["last4"], header_row=hidx,
                      notes=notes)
    _enrich_generic(p, filename, rows, hidx)
    return p


# --------------------------------------------------------------------------- catalog + semantic inference

_CORE = ("date", "description", "amount", "debit", "credit")


def _catalog_profile(filename: str, rows: list[list[str]], hidx, hints: dict) -> Optional[FormatProfile]:
    """Match a known_formats.json header anywhere in the first 40 rows (preamble-safe).

    Date/description come from semantic inference on that header row; the catalog
    supplies institution, amount columns, type/card/balance columns, date format
    and sign. Unverified entries get lower confidence and ask for confirmation
    unless inference independently agrees.
    """
    try:
        from .learning import CONFIRM_THRESHOLD, infer_from_rows, scan_catalog
        hit = scan_catalog(rows, filename)
    except Exception:
        return None
    if not hit:
        return None
    i, e, score = hit
    header = rows[i]
    inf = infer_from_rows(rows, filename, header_row=i)
    if inf is None or inf.mapping is None:
        return None
    m = inf.mapping
    inferred_core = (m.amount, m.debit, m.credit, m.negative_is_out if m.amount else None)
    cm = e.get("mapping") or {}
    if cm.get("amount"):
        m.amount, m.debit, m.credit = _col(header, cm["amount"]) or m.amount, None, None
    elif cm.get("debit") or cm.get("credit"):
        m.amount = None
        m.debit = _col(header, cm["debit"]) if cm.get("debit") else None
        m.credit = _col(header, cm["credit"]) if cm.get("credit") else None
    for k, f in (("type_col", "type_col"), ("card", "card"), ("balance", "balance"), ("status", "status"),
                 ("date", "date"), ("description", "description")):
        if cm.get(k) and _col(header, cm[k]):
            setattr(m, f, _col(header, cm[k]))
    if cm.get("date_format"):
        m.date_format = re.sub(r"[ T]%[HIM].*$", "", cm["date_format"]) or m.date_format
    if m.amount and "negative_is_out" in cm:
        m.negative_is_out = bool(cm["negative_is_out"])
    agree = inferred_core == (m.amount, m.debit, m.credit, m.negative_is_out if m.amount else None)
    verified = bool(e.get("verified", True))
    conf = (0.9 if score >= 1.0 else 0.8) if verified else (0.75 if agree else 0.6)
    needs = (not verified and not agree) or conf < CONFIRM_THRESHOLD
    kind = hints["kind"] or (_kind(e["kind"]) if e.get("kind") else None) or (
        AccountKind.OTHER if str(e.get("product") or "") in ("payment_platform", "brokerage_cash") else inf.kind)
    inst = e.get("institution") or hints["institution"] or "Unknown"
    body = [r for r in rows[i + 1:] if r]
    last4 = _last4_from_col(header, body, m.card) if m.card else None
    notes = [f"matched catalog layout '{e.get('id')}' ({min(score, 1.0):.0%})"] + list(inf.explanation)
    if not verified:
        notes.append("catalog entry is unverified")
    p = FormatProfile(id=f"catalog:{e.get('id') or inst}", institution=inst, kind=kind, mapping=m,
                      confidence=conf, account_hint=last4 or hints["last4"], header_row=i, notes=notes,
                      catalog_id=e.get("id"))
    p.recognition = {"source": "catalog", "format": "CSV", "institution": inst, "needs_confirmation": needs,
                     "catalog_id": e.get("id"), "verified": verified, "agrees": agree, "confidence": conf,
                     "explanation": notes}
    return p


def _semantic(filename: str, rows: list[list[str]], hidx) -> Optional[FormatProfile]:
    """Semantic column inference (learning.infer_from_rows) when the generic scorer gives up."""
    try:
        from .learning import CONFIRM_THRESHOLD, infer_from_rows
        inf = infer_from_rows(rows, filename, header_row=hidx)
    except Exception:
        return None
    if inf is None or inf.mapping is None:
        return None
    hints = filename_hints(filename)
    conf = round(min(0.75, inf.confidence), 3)
    p = FormatProfile(id="inferred", institution=hints["institution"] or "Unknown", kind=inf.kind,
                      mapping=inf.mapping, confidence=conf, account_hint=hints["last4"],
                      header_row=inf.header_row, notes=list(inf.explanation))
    p.recognition = {"source": "inferred", "format": "CSV", "institution": p.institution,
                     "needs_confirmation": inf.confidence < CONFIRM_THRESHOLD, "confidence": inf.confidence,
                     "explanation": list(inf.explanation), "sign_method": inf.sign_method}
    return p


def _enrich_generic(p: FormatProfile, filename: str, rows: list[list[str]], hidx) -> None:
    """Cross-check the generic scorer with semantic inference: add optional columns
    (card, status, memo, balance), take a balance-proven sign, and grade confidence."""
    rec = {"source": "inferred", "format": "CSV", "institution": p.institution, "needs_confirmation": True,
           "confidence": p.confidence}
    p.recognition = rec
    try:
        from .learning import CONFIRM_THRESHOLD, infer_from_rows
        inf = infer_from_rows(rows, filename, header_row=hidx)
    except Exception:
        return
    if inf is None or inf.mapping is None or p.mapping is None:
        return
    g, s = p.mapping, inf.mapping
    agree = all((getattr(g, k) or None) == (getattr(s, k) or None) for k in _CORE)
    for k in ("card", "status", "memo", "balance"):
        if getattr(s, k) and not getattr(g, k):
            setattr(g, k, getattr(s, k))
    if agree and g.amount and inf.sign_method.startswith("running balance"):
        g.negative_is_out = s.negative_is_out
        p.notes.append("sign convention confirmed by running balance")
    conf = inf.confidence if agree else min(p.confidence, inf.confidence)
    rec.update(confidence=round(conf, 3), needs_confirmation=(not agree) or conf < CONFIRM_THRESHOLD,
               explanation=list(inf.explanation), sign_method=inf.sign_method, agrees=agree)
