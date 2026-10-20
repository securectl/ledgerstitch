# SPDX-License-Identifier: AGPL-3.0-only
"""Format auto-learning for tabular statement exports (CSV / XLSX).

Four layers, from most to least specific:

1. **Learned formats** (this workspace, encrypted in the vault under
   ``learned_formats``): saved when the user confirms a column mapping or when
   an automatically inferred mapping imports cleanly. A file whose fingerprint
   matches exactly reuses the mapping; a near match (header-token Jaccard >= 0.8,
   same column count, agreeing value shapes) reuses it after re-inferring only
   the drifted columns.
2. **Built-in profiles** (``detect.KNOWN_PROFILES``) — unchanged.
3. **Catalog** (``ingest/data/known_formats.json``, when shipped): header
   signatures of 60+ institution exports so most banks are recognised on first
   sight.
4. **Semantic inference** (no AI): every column is profiled (date-like with
   candidate formats, money-like with sign stats, text length/entropy,
   categorical, card-number-like, balance-like) and scored for a role using
   multilingual header synonyms + the value profile. The sign convention is
   inferred from the running balance (strongest), a type column, card payment
   rows, or the majority sign.

Privacy: fingerprints keep header *names* and value *shapes* only — never cell
values. Format packs (:func:`export_format_pack`) are stricter still: header
signatures, column roles, date formats and sign conventions; header tokens with
4+ digit runs are dropped.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from ..models import AccountKind, Direction
from .detect import (ColumnMapping, FormatProfile, PAYMENT_RE, _DATE_FORMATS, decode_bytes,
                     filename_hints, parse_date, parse_money, read_rows)

__all__ = [
    "Fingerprint", "ColumnProfile", "Inference", "LearnedFormat", "MappingAssistant",
    "fingerprint", "fingerprint_rows", "infer_mapping", "infer_from_rows", "lookup", "match_catalog",
    "record_success", "record_confirmation", "export_format_pack", "import_format_pack",
    "column_shapes", "load_catalog", "scan_catalog", "ROLES", "CONFIRM_THRESHOLD",
]

ROLES = ("date", "post_date", "description", "amount", "debit", "credit", "type", "category",
         "card", "balance", "memo", "reference", "status")
CONFIRM_THRESHOLD = 0.75          # below this an inferred/drifted mapping asks the user to confirm
FUZZY_JACCARD = 0.8
PACK_KIND = "ledgerstitch.format-pack"
PACK_VERSION = 1
_MAX_LEARNED = 500

# --------------------------------------------------------------------------- header synonyms

def _n(s: str) -> str:
    """Normalize a header: lower, strip accents-ish punctuation, collapse spaces."""
    s = (s or "").strip().strip('"').lower().replace("﻿", "")
    s = re.sub(r"[^\w\s/#]", " ", s)          # keep letters/digits/_/#
    s = s.replace("_", " ")
    return " ".join(s.split())


_SYN: dict[str, tuple[str, ...]] = {
    "date": ("transaction date", "trans date", "trans  date", "date", "txn date", "purchase date",
             "transaction post date", "date of transaction", "activity date", "tran date", "trade date",
             "fecha", "fecha operacion", "fecha de operacion", "date operation", "date de l operation",
             "date opération", "datum", "transaktionsdatum", "data", "data operazione", "data transacao",
             "date posted", "dtposted", "buchungsdatum"),
    "post_date": ("post date", "posted date", "posting date", "posted", "settlement date", "clearing date",
                  "value date", "valuta", "wertstellung", "fecha valor", "date de valeur", "booking date",
                  "buchungstag", "processed date", "cleared date", "posted on"),
    "description": ("description", "desc", "payee", "payee name", "merchant", "merchant name", "name",
                    "narrative", "details", "transaction description", "description of transaction",
                    "particulars", "concepto", "descripcion", "libelle", "libellé", "verwendungszweck",
                    "beschreibung", "buchungstext", "descrizione", "descricao", "omschrijving",
                    "transaction details", "counterparty", "beneficiary", "original description",
                    "what", "payee/payer", "appears on your statement as"),
    "amount": ("amount", "amt", "transaction amount", "amount usd", "amount (usd)", "value", "importe",
               "montant", "betrag", "valor", "sum", "net amount", "how much", "amount eur", "bedrag",
               "importo", "billing amount", "amount gbp", "amount cad"),
    "debit": ("debit", "debits", "withdrawal", "withdrawals", "money out", "paid out", "outflow", "charges",
              "debit amount", "cargo", "cargos", "debito", "débit", "soll", "belastung", "spent", "out",
              "withdrawal amount", "payments out", "afschrijving", "addebiti", "uscite"),
    "credit": ("credit", "credits", "deposit", "deposits", "money in", "paid in", "inflow", "credit amount",
               "abono", "abonos", "credito", "crédit", "haben", "gutschrift", "received", "in",
               "deposit amount", "payments in", "bijschrijving", "accrediti", "entrate"),
    "type": ("type", "transaction type", "details", "dr/cr", "debit/credit", "cr/dr", "transaction", "tipo",
             "sens", "art", "credit/debit indicator", "trans type", "transaction code", "debit credit",
             "credit debit", "umsatzart", "movement type"),
    "category": ("category", "categoria", "catégorie", "categorie", "kategorie", "bank category",
                 "merchant category", "spend category"),
    "card": ("card", "card no", "card number", "card no.", "account #", "card last 4", "last 4",
             "card ending", "card #", "card member number", "kartennummer", "tarjeta"),
    "balance": ("balance", "running bal", "running bal.", "running balance", "saldo", "solde", "kontostand",
                "available balance", "ledger balance", "balance after", "closing balance"),
    "memo": ("memo", "notes", "note", "extended details", "additional info", "comment", "comments",
             "bemerkung", "remarks", "additional information", "notizen"),
    "reference": ("reference", "reference number", "ref", "ref #", "check or slip #", "check number",
                  "check #", "check no", "transaction id", "id", "fitid", "confirmation", "referenz",
                  "referencia", "cheque number", "serial number", "receipt"),
    "status": ("status", "state", "estado", "transaction status", "posted status", "statut"),
}
_SYN_N = {role: {_n(s) for s in syns} for role, syns in _SYN.items()}
_SYN_WORDS = {
    "date": ("date", "fecha", "datum", "data"),
    "post_date": ("post", "posting", "posted", "settle", "clearing", "valuta", "value date", "booking"),
    "description": ("desc", "payee", "merchant", "narrative", "detail", "particular", "libell", "beschreib"),
    "amount": ("amount", "amt", "betrag", "montant", "importe"),
    "debit": ("debit", "withdraw", "money out", "outflow", "charge"),
    "credit": ("credit", "deposit", "money in", "inflow"),
    "balance": ("balance", "saldo", "solde", "running"),
    "category": ("categor",),
    "card": ("card",),
    "memo": ("memo", "note"),
    "reference": ("ref", "check", "cheque", "confirmation"),
    "status": ("status",),
    "type": ("type",),
}


def header_score(header: str, role: str) -> float:
    """0..1: how strongly a header name suggests `role`."""
    h = _n(header)
    if not h:
        return 0.0
    if h in _SYN_N[role]:
        return 1.0
    for w in _SYN_WORDS.get(role, ()):
        if w in h:
            # "transaction date" is the purchase date; "post date" is not
            if role == "date" and any(p in h for p in ("post", "clearing", "settle", "valuta", "value")):
                return 0.2
            return 0.6
    return 0.0


# --------------------------------------------------------------------------- column profiles

_CARD_RE = re.compile(r"^(?:[Xx*•.\-\s]*\d{4}|\d{4}|(?:\d{4}[\s-]?){3}\d{4}|\d{15,16}|[Xx*]{4,}\d{4})$")
_ID_RE = re.compile(r"^[A-Za-z]{0,3}\d{7,}[A-Za-z0-9]*$")
_IN_VOCAB = re.compile(r"^(?:payment|return|refund|credit|cr|deposit|dep|incoming|transfer in|crdt|"
                       r"c|haben|abono|credito|crédit|gutschrift|statement credit|purchase_return|adjustment|"
                       r"ach_credit|reward|rewards|cash ?back)(?![a-z])", re.I)
_OUT_VOCAB = re.compile(r"^(?:sale|purchase|debit|dr|fee|fees|interest|withdrawal|check|atm|charge|dbit|d|"
                        r"soll|cargo|debito|débit|lastschrift|ach_debit|bill pay|transfer out|pos|clearing|"
                        r"installment|dslip)(?![a-z])", re.I)
_STATUS_VOCAB = re.compile(r"^(?:pending|posted|cleared|booked|authori[sz]ed|settled|complete[d]?|pendiente|"
                           r"en attente|vorgemerkt|gebucht)$", re.I)


@dataclass
class ColumnProfile:
    index: int
    header: str
    kind: str = "empty"                 # date | money | text | categorical | card | id | number | empty
    fill: float = 0.0
    date_frac: float = 0.0
    date_formats: list[str] = field(default_factory=list)
    money_frac: float = 0.0
    neg_frac: float = 0.0
    has_decimals: bool = False
    alpha_frac: float = 0.0
    len_min: int = 0
    len_max: int = 0
    len_mean: float = 0.0
    uniq: float = 0.0
    distinct: int = 0
    entropy: float = 0.0
    card_frac: float = 0.0
    id_frac: float = 0.0
    in_vocab: float = 0.0               # categorical values that mean money in / out
    out_vocab: float = 0.0
    status_vocab: float = 0.0
    balance_like: bool = False

    def shape(self) -> str:
        """Privacy-safe one-line description: what the values look like, not what they are."""
        if self.kind == "date":
            return f"date {_fmt_label(self.date_formats[0]) if self.date_formats else '?'}"
        if self.kind == "money":
            sign = "signed" if self.neg_frac > 0 else "unsigned"
            sparse = " sparse" if self.fill < 0.9 else ""
            return f"money {sign}{sparse}" + (" balance-like" if self.balance_like else "")
        if self.kind == "categorical":
            return f"categorical {self.distinct} values"
        if self.kind == "card":
            return "card-number-like"
        if self.kind == "id":
            return "id/reference"
        if self.kind == "text":
            return f"text {self.len_min}-{self.len_max} chars"
        if self.kind == "number":
            return "number"
        return "empty"

    def public(self) -> dict:
        """Fingerprint form stored in the vault — no values."""
        d = asdict(self)
        for k in ("fill", "date_frac", "money_frac", "neg_frac", "alpha_frac", "len_mean", "uniq", "entropy",
                  "card_frac", "id_frac", "in_vocab", "out_vocab", "status_vocab"):
            d[k] = round(float(d[k]), 3)
        return d


def _fmt_label(f: str) -> str:
    return (f.replace("%Y", "YYYY").replace("%y", "YY").replace("%m", "MM").replace("%d", "DD")
            .replace("%b", "Mon").replace("%B", "Month"))


def _strip_time(v: str) -> str:
    v = v.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}T", v):
        return v.split("T")[0]
    return re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*(AM|PM)?(\s*(?:UTC|GMT|Z|[A-Z]{2,4}T|[+-]\d{2}:?\d{2}))?$",
                  "", v, flags=re.I)


def _date_formats(vals: list[str]) -> tuple[float, list[str]]:
    """Fraction of values that parse as a date with *some* format + formats parsing >= 90%."""
    if not vals:
        return 0.0, []
    vals = [_strip_time(v) for v in vals]
    ok_any = 0
    per_fmt = Counter()
    for v in vals:
        hit = False
        for f in _DATE_FORMATS:
            try:
                datetime.strptime(v, f)
            except ValueError:
                continue
            per_fmt[f] += 1
            hit = True
        ok_any += hit
    n = len(vals)
    fmts = [f for f in _DATE_FORMATS if per_fmt[f] >= 0.9 * n]
    # US month-first first (the order of _DATE_FORMATS already prefers it)
    return ok_any / n, fmts


def _entropy(vals: list[str]) -> float:
    c = Counter(vals)
    n = sum(c.values())
    return -sum((k / n) * math.log2(k / n) for k in c.values()) if n else 0.0


def profile_column(index: int, header: str, vals_all: list[str], nrows: int) -> ColumnProfile:
    vals = [v.strip() for v in vals_all if v and v.strip()]
    p = ColumnProfile(index=index, header=header)
    n = len(vals)
    if not n:
        return p
    p.fill = n / max(1, nrows)
    p.date_frac, p.date_formats = _date_formats(vals[:300])
    money = []
    for v in vals:
        if re.fullmatch(r"\d{7,}", v):       # long digit strings are ids, not money
            continue
        m = parse_money(v)
        if m is not None and re.search(r"\d", v) and not re.search(r"[A-Za-z]{2,}", v.replace("USD", "")
                                                                   .replace("CR", "").replace("DR", "")):
            money.append((v, m))
    p.money_frac = len(money) / n
    p.neg_frac = (sum(1 for _, m in money if m < 0) / len(money)) if money else 0.0
    p.has_decimals = any(re.search(r"\d[.,]\d{2}\b", v) for v, _ in money)
    p.alpha_frac = sum(bool(re.search(r"[A-Za-zÀ-ɏ]{2}", v)) for v in vals) / n
    lens = [len(v) for v in vals]
    p.len_min, p.len_max, p.len_mean = min(lens), max(lens), sum(lens) / n
    distinct = set(vals)
    p.distinct = len(distinct)
    p.uniq = len(distinct) / n
    p.entropy = _entropy(vals)
    p.card_frac = sum(bool(_CARD_RE.match(v)) for v in vals) / n
    p.id_frac = sum(bool(_ID_RE.match(v)) for v in vals) / n
    p.in_vocab = sum(bool(_IN_VOCAB.match(v)) for v in vals) / n
    p.out_vocab = sum(bool(_OUT_VOCAB.match(v)) for v in vals) / n
    p.status_vocab = sum(bool(_STATUS_VOCAB.match(v)) for v in vals) / n
    # classify
    if p.date_frac >= 0.8 and p.money_frac < 0.5:
        p.kind = "date"
    elif p.card_frac >= 0.8 and not p.has_decimals and (p.distinct <= max(4, n // 5) or
                                                        any(not v.isdigit() for v in distinct)):
        p.kind = "card"
    elif p.money_frac >= 0.8 and (p.has_decimals or p.neg_frac > 0) and p.date_frac < 0.5:
        p.kind = "money"
    elif p.id_frac >= 0.8 or (p.money_frac >= 0.8 and not p.has_decimals and p.uniq > 0.9 and p.len_mean >= 6):
        p.kind = "id"
    elif p.money_frac >= 0.8:
        p.kind = "number" if not p.has_decimals and p.len_max <= 4 and p.uniq < 0.5 else "money"
    elif p.alpha_frac >= 0.5 and (p.distinct <= max(6, int(0.05 * n)) and n >= 4 or
                                  (p.distinct <= 3 and n >= 2 and p.len_max <= 20)):
        p.kind = "categorical"
    elif p.alpha_frac >= 0.3 or p.len_mean >= 6:
        p.kind = "text"
    else:
        p.kind = "number" if p.money_frac > 0.5 else "text"
    return p


# --------------------------------------------------------------------------- fingerprint

@dataclass
class Fingerprint:
    header: list[str]                   # header cells as written ("col0".. for headerless files)
    header_norm: list[str]
    signature: str                      # hash of normalized header in file order + column count
    signature_sorted: str               # order-insensitive variant
    column_count: int
    columns: list[ColumnProfile]
    delimiter: str = ","
    preamble_lines: int = 0
    encoding: str = "utf-8"
    headerless: bool = False
    header_row: Optional[int] = None

    @property
    def tokens(self) -> set[str]:
        """Word tokens of the header ("Transaction Date" → {"transaction", "date"})."""
        return {w for h in self.header_norm for w in re.split(r"[\s/#]+", h) if w}

    def to_dict(self) -> dict:
        return {"header": list(self.header), "header_norm": list(self.header_norm), "signature": self.signature,
                "signature_sorted": self.signature_sorted, "column_count": self.column_count,
                "columns": [c.public() for c in self.columns], "delimiter": self.delimiter,
                "preamble_lines": self.preamble_lines, "encoding": self.encoding, "headerless": self.headerless}

    @classmethod
    def from_dict(cls, d: dict) -> "Fingerprint":
        cols = []
        for c in d.get("columns") or []:
            known = {k: v for k, v in c.items() if k in ColumnProfile.__dataclass_fields__}
            cols.append(ColumnProfile(**known))
        return cls(header=list(d.get("header") or []), header_norm=list(d.get("header_norm") or []),
                   signature=d.get("signature", ""), signature_sorted=d.get("signature_sorted", ""),
                   column_count=int(d.get("column_count") or 0), columns=cols,
                   delimiter=d.get("delimiter", ","), preamble_lines=int(d.get("preamble_lines") or 0),
                   encoding=d.get("encoding", "utf-8"), headerless=bool(d.get("headerless")))


def _sig(parts: list[str], ncols: int) -> str:
    return hashlib.sha256(("|".join(parts) + f"#{ncols}").encode()).hexdigest()[:20]


def _sniff_delim(text: str) -> str:
    sample = text[:4096]
    counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
    return max(counts, key=lambda d: (counts[d], d == ","))


def _sniff_encoding(data: bytes) -> str:
    if data[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def _row_is_data(r: list[str]) -> bool:
    cells = [c for c in r if c]
    return len(cells) >= 2 and any(parse_date(c) is not None for c in cells) and any(
        parse_money(c) is not None and re.search(r"\d", c) and parse_date(c) is None for c in cells)


def find_header_generic(rows: list[list[str]], max_scan: int = 40) -> Optional[int]:
    """Header row index WITHOUT built-in profiles; -1 = headerless; None = not tabular."""
    for i, r in enumerate(rows[:max_scan]):
        cells = [c for c in r if c]
        if len(cells) < 2:
            continue
        if _row_is_data(r):
            nxt = [x for x in rows[i:i + 5] if x]
            if nxt and sum(_row_is_data(x) for x in nxt) >= max(1, len(nxt) - 1):
                return -1
            continue
        textish = sum(1 for c in cells if parse_date(c) is None and parse_money(c) is None)
        if textish < max(2, int(0.6 * len(cells))):
            continue
        nxt = [x for x in rows[i + 1:i + 8] if x]
        if nxt and sum(_row_is_data(x) for x in nxt) >= max(1, len(nxt) // 2) and \
                max(len(x) for x in nxt) <= len(r) + 2 and len(r) >= max(len(x) for x in nxt) - 1:
            return i
    return None


_FOOTER = re.compile(r"^\s*(?:total|totals|subtotal|grand total|balance|beginning balance|ending balance|"
                     r"opening balance|closing balance|summary)\b", re.I)


def fingerprint_rows(rows: list[list[str]], header_row: Optional[int] = None, *, delimiter: str = ",",
                     encoding: str = "utf-8") -> Optional[Fingerprint]:
    hidx = find_header_generic(rows) if header_row is None else header_row
    if hidx is None:
        return None
    if hidx < 0:
        body = [r for r in rows if r]
        width = max((len(r) for r in body[:200]), default=0)
        header = [f"col{i}" for i in range(width)]
    else:
        header = list(rows[hidx])
        while header and not header[-1]:          # Chase checking: trailing comma
            header.pop()
        body = [r for r in rows[hidx + 1:] if r]
        width = len(header)
    sample = [r for r in body[:400] if sum(1 for c in r if c) >= 2 and not _FOOTER.match(r[0] if r else "")]
    cols = [profile_column(i, header[i], [r[i] if i < len(r) else "" for r in sample], len(sample))
            for i in range(width)]
    norm = [_n(h) for h in header]
    fp = Fingerprint(header=header, header_norm=norm, signature=_sig(norm, width),
                     signature_sorted=_sig(sorted(norm), width), column_count=width, columns=cols,
                     delimiter=delimiter, preamble_lines=max(0, hidx) if hidx is not None else 0,
                     encoding=encoding, headerless=hidx < 0, header_row=hidx)
    return fp


def _tabular_rows(data: bytes) -> tuple[list[list[str]], str, str]:
    """Rows of a CSV/TSV/XLSX file (XLSX is converted first)."""
    if data[:4] == b"PK\x03\x04":
        from .xlsx_parser import xlsx_to_csv_bytes
        data, _ = xlsx_to_csv_bytes(data)
    text = decode_bytes(data)
    return read_rows(data), _sniff_delim(text), _sniff_encoding(data)


def fingerprint(data: bytes) -> Optional[Fingerprint]:
    rows, delim, enc = _tabular_rows(data)
    return fingerprint_rows(rows, delimiter=delim, encoding=enc)


def column_shapes(fp: Fingerprint) -> list[str]:
    return [c.shape() for c in fp.columns]


# --------------------------------------------------------------------------- semantic inference

@dataclass
class Inference:
    mapping: Optional[ColumnMapping]
    roles: dict[str, int]                         # role -> column index
    confidence: float
    explanation: list[str]
    kind: AccountKind
    sign_method: str = ""
    fingerprint: Optional[Fingerprint] = None
    header_row: Optional[int] = None

    @property
    def header(self) -> list[str]:
        return self.fingerprint.header if self.fingerprint else []


def _vals(body: list[list[str]], i: Optional[int]) -> list[str]:
    if i is None:
        return []
    return [(r[i] if i < len(r) else "").strip() for r in body]


def _chronological(dates: list[Optional[date]]) -> bool:
    ds = [d for d in dates if d]
    return not ds or ds[0] <= ds[-1]


def balance_check(body: list[list[str]], date_i: Optional[int], bal_i: int, amt_i: Optional[int] = None,
                  deb_i: Optional[int] = None, cre_i: Optional[int] = None,
                  date_fmt: Optional[str] = None) -> tuple[int, int, int]:
    """(matches_if_positive_is_in, matches_if_negative_is_in, comparable_pairs) on balance deltas.

    Works for newest-first and oldest-first files: rows are put in chronological
    order first (by date; file order breaks ties).
    """
    rows = []
    for r in body:
        b = parse_money(r[bal_i]) if bal_i < len(r) else None
        if b is None:
            continue
        if amt_i is not None:
            a = parse_money(r[amt_i]) if amt_i < len(r) else None
        else:
            dv = parse_money(r[deb_i]) if deb_i is not None and deb_i < len(r) else None
            cv = parse_money(r[cre_i]) if cre_i is not None and cre_i < len(r) else None
            if dv is None and cv is None:
                a = None
            else:
                a = abs(cv or 0) - abs(dv or 0)
        if a is None:
            continue
        d = parse_date(r[date_i], date_fmt) if date_i is not None and date_i < len(r) else None
        rows.append((d, a, b))
    if len(rows) < 2:
        return 0, 0, 0
    if not _chronological([d for d, _, _ in rows]):
        rows.reverse()
    pos = neg = 0
    for (_, _, b0), (_, a1, b1) in zip(rows, rows[1:]):
        delta = b1 - b0
        if delta == a1:
            pos += 1
        if delta == -a1:
            neg += 1
    return pos, neg, len(rows) - 1


def _assign_roles(fp: Fingerprint, body: list[list[str]]) -> tuple[dict[str, int], dict[str, float], list[str]]:
    cols = fp.columns
    hdr = [c.header for c in cols]
    roles: dict[str, int] = {}
    score: dict[str, float] = {}
    why: list[str] = []
    used: set[int] = set()

    def take(role: str, i: int, s: float, reason: str) -> None:
        roles[role] = i
        score[role] = round(min(1.0, s), 3)
        used.add(i)
        why.append(f"{role}: column '{hdr[i]}' ({reason})")

    # ---- dates
    dates = [c for c in cols if c.kind == "date" or (c.date_frac >= 0.8 and c.money_frac < 0.8)]
    if dates:
        def dscore(c):
            return (header_score(c.header, "date") - 0.5 * header_score(c.header, "post_date")
                    + 0.3 * c.fill, -c.index)
        best = max(dates, key=dscore)
        hs = header_score(best.header, "date")
        take("date", best.index, 0.6 + 0.4 * max(hs, 0.5 if not fp.headerless else 0.3) * best.date_frac,
             "date values" + (", header name" if hs else ""))
        for c in dates:
            if c.index != best.index and "post_date" not in roles:
                take("post_date", c.index, 0.5 + 0.5 * header_score(c.header, "post_date"), "second date column")
    # ---- money
    money = [c for c in cols if c.index not in used and c.kind == "money"]
    named_bal = [c for c in money if header_score(c.header, "balance") >= 0.6]
    for c in named_bal[:1]:
        take("balance", c.index, 0.9, "header name")
    money = [c for c in money if c.index not in used]
    named_amt = [c for c in money if header_score(c.header, "amount") >= 0.6
                 and header_score(c.header, "debit") < 0.6 and header_score(c.header, "credit") < 0.6]
    deb = [c for c in money if header_score(c.header, "debit") >= 0.6 and header_score(c.header, "credit") < 0.6]
    cre = [c for c in money if header_score(c.header, "credit") >= 0.6 and header_score(c.header, "debit") < 0.6]
    split = None
    if deb and cre:
        split = (max(deb, key=lambda c: (header_score(c.header, "debit"), c.fill)),
                 max(cre, key=lambda c: (header_score(c.header, "credit"), c.fill)))
        if named_amt and max(named_amt, key=lambda c: c.fill).fill >= 0.95:
            split = None            # a full signed amount column beats a split pair
    if split is None and not named_amt:
        # two sparse money columns that (almost) never co-occur → debit/credit
        sparse = [c for c in money if 0.02 < c.fill < 0.98]
        for a in sparse:
            for b in sparse:
                if a.index >= b.index:
                    continue
                both = sum(1 for r in body if len(r) > b.index and r[a.index] and r[b.index])
                either = sum(1 for r in body if len(r) > b.index and (r[a.index] or r[b.index]))
                if either and both <= 0.05 * either and either >= 0.9 * len(body):
                    split = (a, b)
                    break
            if split:
                break
        if split:
            a, b = split
            # decide which is out: header hints, else balance delta, else first = debit
            ha = header_score(a.header, "credit") - header_score(a.header, "debit")
            hb = header_score(b.header, "credit") - header_score(b.header, "debit")
            if ha > hb:
                split = (b, a)
            elif "balance" in roles:
                p1, n1, k = balance_check(body, roles.get("date"), roles["balance"], None, a.index, b.index)
                p2, n2, _ = balance_check(body, roles.get("date"), roles["balance"], None, b.index, a.index)
                if p2 > p1:
                    split = (b, a)
    if split:
        take("debit", split[0].index, 0.6 + 0.4 * header_score(split[0].header, "debit"), "money out column")
        take("credit", split[1].index, 0.6 + 0.4 * header_score(split[1].header, "credit"), "money in column")
    else:
        cands = named_amt or [c for c in money if c.fill >= 0.8] or money
        if cands:
            best = max(cands, key=lambda c: (header_score(c.header, "amount"), c.fill, c.money_frac, -c.index))
            take("amount", best.index, 0.55 + 0.45 * max(header_score(best.header, "amount"), 0.3 * best.fill),
                 "money values" + (", header name" if header_score(best.header, "amount") else ""))
    # ---- balance by behaviour (unnamed running balance)
    if "balance" not in roles and ("amount" in roles or "debit" in roles):
        for c in [c for c in cols if c.index not in used and c.kind == "money"]:
            p, ng, k = balance_check(body, roles.get("date"), c.index, roles.get("amount"),
                                     roles.get("debit"), roles.get("credit"))
            if k >= 2 and max(p, ng) >= 0.8 * k:
                take("balance", c.index, 0.85, "running sum consistent with amounts")
                break
    if "balance" in roles:
        cols[roles["balance"]].balance_like = True
    # ---- card numbers
    cards = [c for c in cols if c.index not in used and (c.kind == "card" or
                                                         (header_score(c.header, "card") >= 0.6 and c.card_frac >= 0.5))]
    if cards:
        best = max(cards, key=lambda c: (header_score(c.header, "card"), c.card_frac))
        take("card", best.index, 0.6 + 0.4 * header_score(best.header, "card"), "card-number-like values")
    # ---- status (Pending / Posted)
    for c in cols:
        if c.index in used:
            continue
        if c.status_vocab >= 0.8 or (header_score(c.header, "status") >= 1.0 and c.kind == "categorical"):
            take("status", c.index, 0.6 + 0.4 * c.status_vocab, "posted/pending values")
            break
    # ---- text roles
    texty = [c for c in cols if c.index not in used and c.kind in ("text", "categorical")]
    types = [c for c in texty if c.kind == "categorical" and
             (header_score(c.header, "type") >= 0.6 or (c.in_vocab + c.out_vocab) >= 0.6)
             and header_score(c.header, "category") < 0.6]
    if types:
        best = max(types, key=lambda c: (c.in_vocab + c.out_vocab, header_score(c.header, "type")))
        take("type", best.index, 0.5 + 0.25 * header_score(best.header, "type") + 0.25 * (best.in_vocab + best.out_vocab),
             "debit/credit-style values")
    cats = [c for c in texty if c.index not in used and header_score(c.header, "category") >= 0.6]
    if cats:
        take("category", cats[0].index, 0.9, "header name")
    memos = [c for c in texty if c.index not in used and header_score(c.header, "memo") >= 0.6]
    descs = [c for c in cols if c.index not in used and c.kind in ("text", "categorical")
             and header_score(c.header, "reference") < 1.0]

    def desc_score(c: ColumnProfile) -> float:
        s = 0.9 * header_score(c.header, "description") + 0.6 * min(1.0, c.uniq * 1.2)
        s += 0.4 * min(c.len_mean, 30) / 30 + 0.1 * min(c.entropy, 6) / 6 + 0.2 * c.alpha_frac
        if c.kind == "categorical":
            s -= 0.6
        if header_score(c.header, "memo") >= 0.6:
            s -= 0.35
        return s
    if descs:
        best = max(descs, key=lambda c: (desc_score(c), -c.index))
        take("description", best.index, min(1.0, 0.45 + 0.4 * desc_score(best)),
             "free-text values" + (", header name" if header_score(best.header, "description") else ""))
    for c in memos:
        if c.index not in used:
            take("memo", c.index, 0.8, "header name")
            break
    refs = [c for c in cols if c.index not in used and (header_score(c.header, "reference") >= 0.6 or c.kind == "id")]
    if refs:
        take("reference", refs[0].index, 0.7, "id-like values")
    if "category" not in roles:
        cats = [c for c in cols if c.index not in used and c.kind == "categorical" and c.distinct >= 4
                and header_score(c.header, "type") < 0.6]
        # unnamed categorical text with many classes is usually the bank category
        if cats and fp.headerless is False and header_score(cats[0].header, "category") > 0:
            take("category", cats[0].index, 0.6, "categorical values")
    return roles, score, why


def _infer_sign(fp: Fingerprint, body: list[list[str]], roles: dict[str, int], kind: AccountKind,
                date_fmt: Optional[str]) -> tuple[bool, str, float]:
    """negative_is_out, method, confidence for a single amount column."""
    ai = roles["amount"]
    if "balance" in roles:
        pos, neg, k = balance_check(body, roles.get("date"), roles["balance"], ai, date_fmt=date_fmt)
        if k >= 2 and max(pos, neg) >= 0.6 * k and pos != neg:
            return (pos > neg), "running balance agrees with amounts", 0.97
    amts = [parse_money(v) for v in _vals(body, ai)]
    if "type" in roles:
        tv = _vals(body, roles["type"])
        agree_neg_out = agree_pos_out = 0
        for t, a in zip(tv, amts):
            if a is None or a == 0 or not t:
                continue
            is_in = bool(_IN_VOCAB.match(t))
            is_out = bool(_OUT_VOCAB.match(t)) and not is_in
            if not (is_in or is_out):
                continue
            if (a < 0) == is_out:
                agree_neg_out += 1
            else:
                agree_pos_out += 1
        tot = agree_neg_out + agree_pos_out
        if tot >= 2 and agree_neg_out != agree_pos_out and max(agree_neg_out, agree_pos_out) >= 0.75 * tot:
            return agree_neg_out > agree_pos_out, "type column (sale/payment) agrees with signs", 0.9
    desc = _vals(body, roles.get("description"))
    pay = [a for d, a in zip(desc, amts) if a is not None and a != 0 and PAYMENT_RE.search(d or "")]
    if pay and kind == AccountKind.CREDIT_CARD:
        pos = sum(1 for a in pay if a > 0)
        if pos != len(pay) - pos:
            return pos > len(pay) - pos, "card payment rows ('PAYMENT THANK YOU') are credits", 0.8
    nz = [a for a in amts if a is not None and a != 0]
    if not nz:
        return True, "no signed amounts", 0.4
    negs = sum(1 for a in nz if a < 0)
    if negs == 0:
        return True, "amounts are unsigned; type column / account kind decides", 0.55
    if kind == AccountKind.CREDIT_CARD:
        return (negs >= len(nz) - negs), "majority sign = purchases (card)", 0.6
    return True, "bank default: negative = money out", 0.6


def infer_from_rows(rows: list[list[str]], filename: str = "", *, header_row: Optional[int] = None,
                    delimiter: str = ",", encoding: str = "utf-8") -> Optional[Inference]:
    fp = fingerprint_rows(rows, header_row, delimiter=delimiter, encoding=encoding)
    if fp is None:
        return None
    body = [r for r in (rows if fp.headerless else rows[fp.header_row + 1:]) if r]
    body = [r for r in body if sum(1 for c in r if c) >= 2 and not _FOOTER.match(r[0] if r else "")]
    roles, score, why = _assign_roles(fp, body)
    hints = filename_hints(filename)
    if "date" not in roles or "description" not in roles or not (
            "amount" in roles or "debit" in roles or "credit" in roles):
        return Inference(mapping=None, roles=roles, confidence=0.0, kind=hints["kind"] or AccountKind.OTHER,
                         explanation=why + ["no date/amount/description columns could be identified"],
                         fingerprint=fp, header_row=fp.header_row)
    name = lambda r: fp.header[roles[r]] if r in roles else None  # noqa: E731
    date_fmt = fp.columns[roles["date"]].date_formats[0] if fp.columns[roles["date"]].date_formats else None
    if date_fmt and len(fp.columns[roles["date"]].date_formats) > 1:
        why.append(f"date format ambiguous ({', '.join(_fmt_label(f) for f in fp.columns[roles['date']].date_formats[:3])});"
                   f" assumed {_fmt_label(date_fmt)}")
    # account kind
    kind = hints["kind"]
    desc_vals = _vals(body, roles["description"])
    if kind is None:
        pays = sum(1 for d in desc_vals if PAYMENT_RE.search(d or ""))
        if "balance" in roles:
            kind = AccountKind.CHECKING
        elif "card" in roles or pays:
            kind = AccountKind.CREDIT_CARD
        else:
            kind = AccountKind.CHECKING
        why.append(f"account kind {kind.value} inferred from content")
    mapping = ColumnMapping(date=name("date"), description=name("description"), amount=name("amount"),
                            debit=name("debit"), credit=name("credit"), type_col=name("type"),
                            category=name("category"), date_format=date_fmt, card=name("card"))
    for extra in ("status", "memo", "balance"):
        if extra in ColumnMapping.__dataclass_fields__ and extra in roles:
            setattr(mapping, extra, name(extra))
    sign_conf = 0.9
    method = "split debit/credit columns"
    if "amount" in roles:
        mapping.negative_is_out, method, sign_conf = _infer_sign(fp, body, roles, kind, date_fmt)
    elif "balance" in roles:
        p, ng, k = balance_check(body, roles.get("date"), roles["balance"], None, roles.get("debit"),
                                 roles.get("credit"), date_fmt)
        if k >= 2 and p >= 0.8 * k:
            method, sign_conf = "running balance agrees with debit/credit columns", 0.97
    why.append(f"sign: {method}")
    core = [score.get("date", 0), score.get("description", 0),
            score.get("amount", 0) or (score.get("debit", 0) + score.get("credit", 0)) / 2]
    conf = 0.5 * (sum(core) / 3) + 0.5 * sign_conf
    if fp.headerless:
        conf -= 0.1
    return Inference(mapping=mapping, roles=roles, confidence=round(max(0.05, min(0.95, conf)), 3),
                     explanation=why, kind=kind, sign_method=method, fingerprint=fp, header_row=fp.header_row)


def infer_mapping(filename: str, data: bytes) -> Optional[Inference]:
    """Semantic inference only — built-in profiles and the catalog are NOT consulted."""
    rows, delim, enc = _tabular_rows(data)
    return infer_from_rows(rows, filename, delimiter=delim, encoding=enc)


# --------------------------------------------------------------------------- learned registry

@dataclass
class LearnedFormat:
    id: str
    fingerprint: dict
    mapping: dict
    sign_convention: dict
    institution_hint: Optional[str] = None
    kind: Optional[str] = None
    source: str = "auto"               # auto | confirmed | pack
    created: str = ""
    last_used: str = ""
    uses: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    success_rate: float = 1.0
    status: str = "active"             # active | demoted
    platform: Optional[str] = None     # catalog id with payment-platform semantics (ingest.platforms)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LearnedFormat":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def _mapping_dict(m: ColumnMapping | dict) -> dict:
    d = asdict(m) if isinstance(m, ColumnMapping) else dict(m)
    return {k: v for k, v in d.items() if k in ColumnMapping.__dataclass_fields__}


def _role_columns(mapping: dict) -> dict[str, str]:
    """mapping dict → {role: header name} for the column-valued fields."""
    out = {}
    for field_, role in (("date", "date"), ("description", "description"), ("amount", "amount"),
                         ("debit", "debit"), ("credit", "credit"), ("type_col", "type"), ("category", "category"),
                         ("card", "card"), ("status", "status"), ("memo", "memo"), ("balance", "balance")):
        v = mapping.get(field_)
        if v:
            out[role] = v
    return out


_ROLE_FIELD = {"date": "date", "description": "description", "amount": "amount", "debit": "debit",
               "credit": "credit", "type": "type_col", "category": "category", "card": "card",
               "status": "status", "memo": "memo", "balance": "balance"}
_ROLE_KINDS = {"date": {"date"}, "amount": {"money", "number"}, "debit": {"money", "number", "empty"},
               "credit": {"money", "number", "empty"}, "balance": {"money"}, "description": {"text", "categorical"},
               "card": {"card", "number", "id"}}


def _profile_agrees(old: Fingerprint, new: Fingerprint, mapping: dict) -> tuple[bool, list[str]]:
    """Do the mapped columns still hold the same kind of values? Returns (ok, drifted_roles)."""
    drift = []
    by_name_new = {_n(c.header): c for c in new.columns}
    by_name_old = {_n(c.header): c for c in old.columns}
    for role, col in _role_columns(mapping).items():
        nc = by_name_new.get(_n(col))
        oc = by_name_old.get(_n(col))
        if nc is None:
            drift.append(role)
            continue
        want = _ROLE_KINDS.get(role)
        if want and nc.kind not in want and nc.kind != "empty":
            drift.append(role)
            continue
        if role == "date" and mapping.get("date_format") and nc.date_formats and \
                mapping["date_format"] not in nc.date_formats:
            drift.append("date_format")
        elif role == "date" and oc is not None and oc.date_formats and nc.date_formats and \
                not set(oc.date_formats) & set(nc.date_formats):
            drift.append("date_format")
    core_ok = not ({"date", "description"} & set(drift))
    return core_ok, drift


def _load_learned(learned: list[dict] | None) -> list[LearnedFormat]:
    out = []
    for d in learned or []:
        try:
            out.append(LearnedFormat.from_dict(d))
        except Exception:
            continue
    return out


@dataclass
class Match:
    learned: LearnedFormat
    exact: bool
    jaccard: float
    drift: list[str]
    mapping: ColumnMapping
    confidence: float
    explanation: list[str]


def _match(fp: Fingerprint, items: list[LearnedFormat], *, include_demoted: bool = False) -> Optional[Match]:
    best: Optional[Match] = None
    for lf in items:
        if lf.status == "demoted" and not include_demoted:
            continue
        try:
            ofp = Fingerprint.from_dict(lf.fingerprint)
        except Exception:
            continue
        mapping = dict(lf.mapping)
        if ofp.signature == fp.signature and ofp.column_count == fp.column_count:
            ok, drift = _profile_agrees(ofp, fp, mapping)
            if not ok:
                continue
            m = Match(lf, True, 1.0, drift, ColumnMapping(**mapping), 0.97 if not drift else 0.8,
                      ["header signature matches exactly"])
        else:
            jac = _jaccard(set(ofp.tokens), set(fp.tokens))
            if fp.headerless or ofp.headerless or jac < FUZZY_JACCARD or ofp.column_count != fp.column_count:
                continue
            ok, drift = _profile_agrees(ofp, fp, mapping)
            if not ok and not {"date", "description"} >= set(drift) - {"date_format"}:
                continue
            m = Match(lf, False, jac, drift, ColumnMapping(**mapping), round(0.6 + 0.3 * jac, 3),
                      [f"header tokens match {jac:.0%} (fuzzy)"])
        if best is None or (m.exact, m.jaccard, m.learned.source == "confirmed", m.learned.successes) > \
                (best.exact, best.jaccard, best.learned.source == "confirmed", best.learned.successes):
            best = m
    return best


def _repair_drift(m: Match, fp: Fingerprint, rows: list[list[str]], filename: str) -> Match:
    """Re-infer only the drifted roles; keep everything the user/learning already settled."""
    if not m.drift:
        return m
    inf = infer_from_rows(rows, filename, header_row=fp.header_row)
    mapping = _mapping_dict(m.mapping)
    if inf is None or inf.mapping is None:
        m.confidence = min(m.confidence, 0.4)
        m.explanation.append("format drift: could not re-infer " + ", ".join(m.drift))
        return m
    new = _mapping_dict(inf.mapping)
    for role in m.drift:
        if role == "date_format":
            mapping["date_format"] = new.get("date_format")
            m.explanation.append("date format changed; re-detected")
            continue
        f = _ROLE_FIELD.get(role)
        if not f:
            continue
        if role in ("amount", "debit", "credit"):
            # amount layout changed: take the newly inferred money columns as a set
            for k in ("amount", "debit", "credit", "negative_is_out"):
                mapping[k] = new.get(k)
        else:
            mapping[f] = new.get(f)
        m.explanation.append(f"column for {role} changed; re-inferred as '{new.get(f) or '-'}'")
    try:
        m.mapping = ColumnMapping(**mapping)
    except TypeError:
        pass
    # the re-inferred roles carry the inference's confidence
    m.confidence = round(min(m.confidence, 0.5 + 0.5 * inf.confidence * 0.9), 3)
    return m


def lookup(filename: str, data: bytes, learned: list[dict] | None,
           catalog: list[dict] | None = None) -> Optional[FormatProfile]:
    """Profile from a learned format (or the catalog) for this file, or None.

    Only tabular files (CSV/TSV/XLSX) are considered. The returned profile has
    `recognition = {"source": "learned"|"catalog"|"pack", ...}`.
    """
    try:
        rows, delim, enc = _tabular_rows(data)
    except Exception:
        return None
    fp = fingerprint_rows(rows, delimiter=delim, encoding=enc)
    if fp is None:
        return None
    items = _load_learned(learned)
    m = _match(fp, items)
    if m is None:
        return None
    m = _repair_drift(m, fp, rows, filename)
    lf = m.learned
    hints = filename_hints(filename)
    kind = AccountKind(lf.kind) if lf.kind in AccountKind._value2member_map_ else (hints["kind"] or AccountKind.OTHER)
    needs = m.confidence < CONFIRM_THRESHOLD
    source = "learned" if lf.source in ("auto", "confirmed") else lf.source
    prof = FormatProfile(id=f"learned:{lf.id}", institution=lf.institution_hint or hints["institution"] or "Unknown",
                         kind=kind, mapping=m.mapping, confidence=m.confidence,
                         account_hint=_last4_from_card_column(rows, fp, m.mapping) or hints["last4"],
                         header_row=fp.header_row,
                         notes=list(m.explanation))
    if lf.platform:
        prof.catalog_id = lf.platform
    _set_recognition(prof, source=source, fmt="XLSX" if data[:4] == b"PK\x03\x04" else "CSV",
                     needs_confirmation=needs, format_id=lf.id, explanation=m.explanation,
                     drift=m.drift, exact=m.exact)
    return prof


def _set_recognition(prof: FormatProfile, **kw) -> None:
    rec = {"source": kw.pop("source"), "format": kw.pop("fmt", "CSV"),
           "institution": prof.institution, "needs_confirmation": bool(kw.pop("needs_confirmation", False))}
    rec.update(kw)
    try:
        prof.recognition = rec
    except Exception:
        pass


def _last4_from_card_column(rows, fp: Fingerprint, mapping: ColumnMapping) -> Optional[str]:
    col = getattr(mapping, "card", None)
    if not col or fp.headerless:
        return None
    try:
        i = [_n(h) for h in fp.header].index(_n(col))
    except ValueError:
        return None
    vals = [re.sub(r"\D", "", r[i]) for r in rows[fp.header_row + 1:] if len(r) > i]
    vals = [v[-4:] for v in vals if len(v) >= 4]
    return Counter(vals).most_common(1)[0][0] if vals else None


# --------------------------------------------------------------------------- import quality

def import_quality(data: bytes, profile: FormatProfile, txns: list, warnings: list[str]) -> dict:
    """Did this import parse cleanly? {ok, parsed_ratio, dates_ok, sign_ok, balance_ok, reasons}."""
    reasons = []
    try:
        rows, _, _ = _tabular_rows(data)
    except Exception:
        return {"ok": False, "reasons": ["unreadable"]}
    hidx = profile.header_row if profile.header_row is not None else find_header_generic(rows)
    mapping = profile.mapping
    if hidx is None or mapping is None:
        return {"ok": False, "reasons": ["no header"]}
    body = [r for r in (rows if hidx < 0 else rows[hidx + 1:]) if sum(1 for c in r if c) >= 2]
    body = [r for r in body if not re.match(r"^\s*(?:total|totals|balance|beginning|ending|summary|opening|closing)\b",
                                            (r[0] if r else ""), re.I)]
    zero = sum(int(m.group(1)) for w in warnings for m in [re.search(r"skipped (\d+) zero-amount", w)] if m)
    pending = sum(int(m.group(1)) for w in warnings
                  for m in [re.search(r"skipped (\d+) (?:pending|failed|memo)", w)] if m)
    denom = max(1, len(body) - zero - pending)
    ratio = len(txns) / denom
    if ratio < 0.95:
        reasons.append(f"only {ratio:.0%} of rows parsed")
    today = date.today()
    dates_ok = all(date(1990, 1, 1) <= t.date <= today + timedelta(days=62) for t in txns) and bool(txns)
    if not dates_ok:
        reasons.append("dates out of range")
    dirs = Counter(t.direction for t in txns)
    sign_ok = True
    pays = [t for t in txns if PAYMENT_RE.search(t.description or "")]
    if profile.kind == AccountKind.CREDIT_CARD:
        if pays and sum(1 for t in pays if t.direction == Direction.OUT) > len(pays) / 2:
            sign_ok = False
        if len(txns) >= 5 and dirs[Direction.IN] > dirs[Direction.OUT] * 1.5:
            sign_ok = False
    elif len(txns) >= 5 and dirs[Direction.OUT] == 0:
        sign_ok = False
    if not sign_ok:
        reasons.append("sign sanity failed")
    balance_ok = None
    bal_col = getattr(mapping, "balance", None)
    if bal_col and hidx >= 0:
        hdr = [_n(h) for h in rows[hidx]]
        try:
            bi = hdr.index(_n(bal_col))
            di = hdr.index(_n(mapping.date))
            ai = hdr.index(_n(mapping.amount)) if mapping.amount else None
            de = hdr.index(_n(mapping.debit)) if mapping.debit else None
            ce = hdr.index(_n(mapping.credit)) if mapping.credit else None
            p, ng, k = balance_check(body, di, bi, ai, de, ce, mapping.date_format)
            if k >= 2:
                hit = p if (ai is None or mapping.negative_is_out) else ng
                balance_ok = hit >= 0.9 * k
                if not balance_ok:
                    reasons.append("running balance does not match")
        except ValueError:
            pass
    ok = ratio >= 0.95 and dates_ok and sign_ok and balance_ok is not False
    return {"ok": ok, "parsed_ratio": round(ratio, 3), "dates_ok": dates_ok, "sign_ok": sign_ok,
            "balance_ok": balance_ok, "reasons": reasons}


# --------------------------------------------------------------------------- record success / confirmation

BUILTIN_IDS = {"chase_card", "chase_checking", "citi", "capital_one_card", "capital_one", "us_bank",
               "capital_one_360", "gemini", "apple_card", "discover", "boa_card", "boa_checking",
               "amex_extended", "amex", "wells_fargo", "pdf"}


def _is_tabular(data: bytes) -> bool:
    head = data[:2048].lstrip()
    if head.startswith(b"%PDF"):
        return False
    if data[:4] == b"PK\x03\x04":
        return True
    low = head.lower()
    if low.startswith((b"ofxheader", b"<?xml", b"<ofx", b"!type", b"!account", b"!option", b"{1:", b":20:", b"01,")):
        return False
    return True


def _upsert(items: list[LearnedFormat], fp: Fingerprint, mapping: dict, *, source: str, institution: Optional[str],
            kind: Optional[str], sign: dict) -> LearnedFormat:
    for lf in items:
        try:
            ofp = Fingerprint.from_dict(lf.fingerprint)
        except Exception:
            continue
        if ofp.signature == fp.signature and ofp.column_count == fp.column_count:
            # same layout: a confirmation always wins; an auto record never overrides a confirmed one
            if source == "confirmed" or lf.source != "confirmed":
                lf.mapping = mapping
                lf.sign_convention = sign
                lf.source = "confirmed" if source == "confirmed" else lf.source
                lf.fingerprint = fp.to_dict()
                lf.status = "active"
                lf.consecutive_failures = 0
            if institution and institution != "Unknown":
                lf.institution_hint = institution
            lf.kind = kind or lf.kind
            return lf
    lf = LearnedFormat(id=uuid.uuid4().hex[:10], fingerprint=fp.to_dict(), mapping=mapping, sign_convention=sign,
                       institution_hint=institution if institution and institution != "Unknown" else None,
                       kind=kind, source=source, created=_now())
    items.append(lf)
    return lf


def _bump(lf: LearnedFormat, ok: bool) -> None:
    lf.uses += 1
    lf.last_used = _now()
    if ok:
        lf.successes += 1
        lf.consecutive_failures = 0
    else:
        lf.failures += 1
        lf.consecutive_failures += 1
    lf.success_rate = round(lf.successes / max(1, lf.uses), 3)
    if lf.consecutive_failures >= 2 or (lf.uses >= 3 and lf.success_rate < 0.5):
        lf.status = "demoted"


def _sign_dict(mapping: dict, method: str = "") -> dict:
    if mapping.get("debit") or mapping.get("credit"):
        return {"style": "split", "method": method or "debit/credit columns"}
    return {"style": "signed", "negative_is_out": bool(mapping.get("negative_is_out", True)), "method": method}


def _trim(items: list[LearnedFormat]) -> list[dict]:
    items.sort(key=lambda x: (x.source == "confirmed", x.last_used or x.created), reverse=True)
    return [x.to_dict() for x in items[:_MAX_LEARNED]]


def record_success(learned: list[dict] | None, filename: str, data: bytes, profile: FormatProfile,
                   txns: list, warnings: list[str]) -> tuple[list[dict], dict]:
    """After an automatic import: learn/score the format. Returns (learned, report).

    Built-in profiles are not recorded (their behaviour stays identical). An
    inferred mapping is saved only when the import is clean (>= 95 % of rows
    parsed, dates in range, sign sanity, balance check when there is a
    balance column). A learned format that fails is scored down and demoted.
    """
    items = _load_learned(learned)
    report = {"recorded": False}
    pid = getattr(profile, "id", "") or ""
    if not _is_tabular(data) or getattr(profile, "mapping", None) is None or pid in BUILTIN_IDS \
            or pid.startswith("catalog:"):          # catalog layouts behave like built-ins
        return [x.to_dict() for x in items], report
    rows, delim, enc = _tabular_rows(data)
    fp = fingerprint_rows(rows, profile.header_row if profile.header_row is not None else None,
                          delimiter=delim, encoding=enc)
    if fp is None:
        return [x.to_dict() for x in items], report
    q = import_quality(data, profile, txns, warnings)
    report["quality"] = q
    mapping = _mapping_dict(profile.mapping)
    if pid.startswith("learned:"):
        lid = pid.split(":", 1)[1]
        for lf in items:
            if lf.id == lid:
                _bump(lf, q["ok"])
                if q["ok"]:
                    if (getattr(profile, "recognition", None) or {}).get("drift") and lf.source != "confirmed":
                        lf.mapping = mapping            # accept the repaired mapping
                        lf.fingerprint = fp.to_dict()
                report.update(recorded=True, id=lf.id, status=lf.status)
                break
        return _trim(items), report
    if not q["ok"]:
        return [x.to_dict() for x in items], report
    rec = getattr(profile, "recognition", None) or {}
    if rec.get("needs_confirmation"):
        # low-confidence guesses are not remembered until the user confirms them
        report["reason"] = "low confidence; waiting for confirmation"
        return [x.to_dict() for x in items], report
    lf = _upsert(items, fp, mapping, source="auto", institution=profile.institution,
                 kind=getattr(profile.kind, "value", profile.kind),
                 sign=_sign_dict(mapping, rec.get("sign_method", "")))
    _bump(lf, True)
    report.update(recorded=True, id=lf.id, status=lf.status)
    return _trim(items), report


def record_confirmation(learned: list[dict] | None, filename: str, data: bytes, mapping: ColumnMapping | dict,
                        institution: Optional[str] = None, kind: Any = None,
                        txns: list | None = None) -> tuple[list[dict], dict]:
    """The user confirmed (or built) a mapping for this layout: remember it."""
    items = _load_learned(learned)
    if not _is_tabular(data):
        return [x.to_dict() for x in items], {"recorded": False}
    rows, delim, enc = _tabular_rows(data)
    md = _mapping_dict(mapping)
    hidx = None
    for i, r in enumerate(rows[:60]):
        low = [_n(c) for c in r]
        if _n(md.get("date") or "") in low and _n(md.get("description") or "") in low:
            hidx = i
            break
    if hidx is None and re.fullmatch(r"col\d+", md.get("date") or ""):
        hidx = -1
    fp = fingerprint_rows(rows, hidx, delimiter=delim, encoding=enc)
    if fp is None:
        return [x.to_dict() for x in items], {"recorded": False}
    lf = _upsert(items, fp, md, source="confirmed", institution=institution,
                 kind=getattr(kind, "value", kind), sign=_sign_dict(md, "confirmed by user"))
    if not fp.headerless:
        try:
            from .platforms import is_platform
            hit = scan_catalog(rows[fp.header_row:fp.header_row + 1])
            if hit and is_platform(hit[1].get("id")):
                lf.platform = hit[1]["id"]
                if lf.institution_hint is None or lf.institution_hint == "Unknown":
                    lf.institution_hint = hit[1].get("institution")
        except Exception:
            pass
    _bump(lf, True if txns is None else bool(txns))
    return _trim(items), {"recorded": True, "id": lf.id, "status": lf.status}


# --------------------------------------------------------------------------- catalog bootstrap

_CATALOG_PATH = Path(__file__).parent / "data" / "known_formats.json"
_catalog_cache: dict = {}


def load_catalog(path: Path | None = None) -> list[dict]:
    """Built-in catalog entries (``ingest/data/known_formats.json``), normalised to::

        {id, institution, product, kind, format, verified, header, header_norm, mapping,
         sign_convention, type_values, skip_rows_hint, intu_bid, ofx_org, ofx_fid}

    `mapping` holds only what the catalog states (amount/debit/credit, type, card,
    balance, date format, sign); date/description columns come from inference.
    Tolerant of older/hand-written schemas (``formats`` list, ``mapping`` dicts).
    """
    p = Path(path) if path else _CATALOG_PATH
    try:
        st = p.stat()
    except OSError:
        return []
    key = (str(p), st.st_mtime_ns, st.st_size)
    if _catalog_cache.get("key") == key:
        return _catalog_cache["entries"]
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(raw, dict):
        entries_raw = raw.get("entries") or raw.get("formats") or raw.get("institutions") or []
    else:
        entries_raw = raw
    out = []
    for e in entries_raw if isinstance(entries_raw, list) else []:
        if isinstance(e, dict):
            ent = _catalog_entry(e)
            if ent:
                out.append(ent)
    _catalog_cache.update(key=key, entries=out)
    return out


def _catalog_entry(e: dict) -> Optional[dict]:
    header = e.get("header") or e.get("headers") or e.get("columns")
    if isinstance(header, str):
        header = [h.strip() for h in re.split(r"[,;\t|]", header)]
    if not header and isinstance(e.get("header_signature"), str):
        header = e["header_signature"].split("|")
    header = [str(h) for h in (header or [])]
    fmt = str(e.get("format") or e.get("file_type") or "csv").upper()
    mapping: dict = {}
    # legacy hand-written schema
    if isinstance(e.get("mapping"), dict):
        mapping.update(_catalog_mapping(e["mapping"], header))
    ac = e.get("amount_columns") or {}
    if isinstance(ac, dict):
        for k in ("amount", "debit", "credit"):
            if ac.get(k):
                mapping[k] = ac[k]
    for src, dst in (("type_column", "type_col"), ("card_column", "card"), ("balance_column", "balance"),
                     ("status_column", "status"), ("date_column", "date"), ("description_column", "description")):
        if e.get(src):
            mapping[dst] = e[src]
    if e.get("date_format"):
        mapping["date_format"] = e["date_format"]
    sign = e.get("sign_convention")
    if isinstance(sign, str):
        s = sign.lower()
        if s in ("positive_is_out",) or ("positive" in s and any(w in s for w in ("charge", "out", "purchase"))):
            mapping["negative_is_out"] = False
        elif s in ("negative_is_out",) or "negative" in s:
            mapping["negative_is_out"] = True
    elif isinstance(sign, dict) and "negative_is_out" in sign:
        mapping["negative_is_out"] = bool(sign["negative_is_out"])
    if e.get("negative_is_out") is not None:
        mapping["negative_is_out"] = bool(e["negative_is_out"])
    product = e.get("product") or e.get("kind") or e.get("account_kind")
    verified = e.get("verified")
    return {
        "id": str(e.get("id") or e.get("institution") or ""),
        "institution": e.get("institution") or e.get("bank"),
        "product": product, "kind": _norm_kind(product), "format": fmt,
        "verified": True if verified is None else bool(verified),
        "header": header, "header_norm": [_n(h) for h in header],
        "mapping": {k: v for k, v in mapping.items() if k in ColumnMapping.__dataclass_fields__},
        "sign_convention": sign if isinstance(sign, str) else None,
        "type_values": e.get("type_values") if isinstance(e.get("type_values"), dict) else None,
        "skip_rows_hint": int(e.get("skip_rows_hint") or 0),
        "intu_bid": e.get("intu_bid") or e.get("INTU.BID"),
        "ofx_org": e.get("ofx_org") or e.get("org"), "ofx_fid": e.get("ofx_fid") or e.get("fid"),
        "headerless": bool(e.get("headerless") or e.get("headerless_columns")),
    }


def _norm_kind(kind) -> Optional[str]:
    k = str(kind or "").lower()
    if "card" in k or k == "credit":
        return "credit_card"
    if "saving" in k:
        return "savings"
    if "check" in k or k in ("bank", "current", "deposit"):
        return "checking"
    return None


_CAT_FIELDS = {"date": "date", "transaction_date": "date", "description": "description", "desc": "description",
               "payee": "description", "amount": "amount", "debit": "debit", "credit": "credit", "type": "type_col",
               "type_col": "type_col", "category": "category", "card": "card", "balance": "balance",
               "memo": "memo", "status": "status", "date_format": "date_format",
               "negative_is_out": "negative_is_out"}


def _catalog_mapping(m: dict, header: list[str]) -> dict:
    out: dict = {}
    for k, v in m.items():
        f = _CAT_FIELDS.get(str(k).lower())
        if f is None or v in (None, ""):
            continue
        if f == "negative_is_out":
            out[f] = bool(v)
        elif f == "date_format":
            out[f] = v
        elif isinstance(v, int) and header and 0 <= v < len(header):
            out[f] = header[v]
        elif isinstance(v, str):
            out[f] = v
    return out


_TABULAR_FORMATS = {"CSV", "TSV", "XLSX", "XLS", "CSV/XLSX", "TXT"}


def match_catalog(fp: Fingerprint, filename: str = "", catalog: list[dict] | None = None
                  ) -> Optional[tuple[dict, float]]:
    """Best catalog entry for a header fingerprint: (entry, score) or None.

    Exact = same normalized header set (score 1.0). Fuzzy = Jaccard on header
    word tokens >= 0.85 with a column count within 2 (score = Jaccard). Short
    (< 4 column) headers are claimed only on an exact match *and* a file name
    naming the same institution, so a bare ``Date,Description,Amount`` file is
    never forced into one bank's sign convention. The stated amount columns
    must exist in the file.
    """
    entries = load_catalog() if catalog is None else catalog
    if not entries or fp is None or fp.headerless:
        return None
    cols = {h for h in fp.header_norm if h}
    words = fp.tokens
    hint = filename_hints(filename)["institution"]
    best = None
    for e in entries:
        if e.get("format") not in _TABULAR_FORMATS or not e.get("header_norm"):
            continue
        ecols = {h for h in e["header_norm"] if h}
        if not ecols:
            continue
        exact = cols == ecols
        if exact:
            score = 1.0
        else:
            ewords = {w for h in ecols for w in re.split(r"[\s/#]+", h) if w}
            score = _jaccard(words, ewords)
            if score < 0.85 or abs(len(ecols) - len(cols)) > 2:
                continue
        if len(ecols) < 4 and not (exact and hint and e.get("institution") and _same_inst(hint, e["institution"])):
            continue
        m = e.get("mapping") or {}
        if any(isinstance(m.get(k), str) and _n(m[k]) not in cols for k in ("amount", "debit", "credit")):
            continue
        score += 0.05 if hint and e.get("institution") and _same_inst(hint, e["institution"]) else 0
        score += 0.01 if e.get("verified") else 0
        if best is None or score > best[1]:
            best = (e, score)
    return best


def scan_catalog(rows: list[list[str]], filename: str = "", max_scan: int = 40,
                 catalog: list[dict] | None = None) -> Optional[tuple[int, dict, float]]:
    """Find a catalog header anywhere in the first `max_scan` rows (preamble-safe):
    (header_row, entry, score) or None."""
    entries = load_catalog() if catalog is None else catalog
    if not entries:
        return None
    best = None
    for i, r in enumerate(rows[:max_scan]):
        if sum(1 for c in r if c) < 3:
            continue
        hdr = list(r)
        while hdr and not hdr[-1]:
            hdr.pop()
        norm = [_n(h) for h in hdr]
        fp = Fingerprint(header=hdr, header_norm=norm, signature="", signature_sorted="", column_count=len(hdr),
                         columns=[], header_row=i)
        hit = match_catalog(fp, filename, entries)
        if hit and (best is None or hit[1] > best[2]):
            best = (i, hit[0], hit[1])
            if hit[1] >= 1.0:
                break
    return best


def _same_inst(a: str, b: str) -> bool:
    a, b = _n(a), _n(b)
    return bool(a and b) and (a in b or b in a)


# --------------------------------------------------------------------------- format packs

_DIGITS4 = re.compile(r"\d{4,}")
_HEX_ALPHA = str.maketrans("0123456789", "ghijklmnop")   # pack signatures carry no digit runs


def export_format_pack(learned: list[dict] | None, *, include_demoted: bool = False) -> dict:
    """Privacy-safe JSON of learned layouts: header signatures, column roles,
    date formats and sign conventions only. No values, filenames, institutions,
    account numbers or usage stats."""
    out = []
    for lf in _load_learned(learned):
        if lf.status == "demoted" and not include_demoted:
            continue
        fp = lf.fingerprint or {}
        header_norm = [h if not _DIGITS4.search(h) else "#" for h in (fp.get("header_norm") or [])]
        roles = {}
        for role, col in _role_columns(lf.mapping).items():
            try:
                idx = [_n(h) for h in fp.get("header") or []].index(_n(col))
            except ValueError:
                continue
            if header_norm[idx] == "#" and not fp.get("headerless"):
                continue
            roles[role] = idx
        sign = {k: v for k, v in (lf.sign_convention or {}).items() if k in ("style", "negative_is_out")}
        if "negative_is_out" not in sign and lf.mapping.get("amount"):
            sign["negative_is_out"] = bool(lf.mapping.get("negative_is_out", True))
        out.append({
            "header": header_norm,
            "signature": _sig(header_norm, int(fp.get("column_count") or len(header_norm))).translate(_HEX_ALPHA),
            "column_count": int(fp.get("column_count") or len(header_norm)),
            "headerless": bool(fp.get("headerless")),
            "roles": roles,
            "date_format": lf.mapping.get("date_format"),
            "column_shapes": [_shape_only(c) for c in fp.get("columns") or []],
            "sign_convention": sign,
            "kind": lf.kind if lf.kind in ("credit_card", "checking", "savings") else None,
        })
    return {"kind": PACK_KIND, "version": PACK_VERSION, "formats": out,
            # TODO(ed25519): sign packs like licenses (security/license.py) before community sharing.
            "signature": None}


def _shape_only(c: dict) -> str:
    try:
        return ColumnProfile(**{k: v for k, v in c.items() if k in ColumnProfile.__dataclass_fields__}).shape()
    except Exception:
        return "?"


class PackVerifier(Protocol):
    """Interface for signed format packs (Ed25519, later). Returns True when trusted."""

    def verify(self, payload: bytes, signature: str) -> bool: ...


def import_format_pack(learned: list[dict] | None, pack: dict, *,
                       verifier: PackVerifier | None = None) -> tuple[list[dict], int]:
    """Merge a format pack into the learned registry. Never overrides layouts the
    user confirmed. Returns (learned, added)."""
    if not isinstance(pack, dict) or pack.get("kind") != PACK_KIND or int(pack.get("version") or 0) != PACK_VERSION:
        raise ValueError("not a LedgerStitch format pack")
    if verifier is not None:
        payload = json.dumps(pack.get("formats"), sort_keys=True, separators=(",", ":")).encode()
        if not pack.get("signature") or not verifier.verify(payload, pack["signature"]):
            raise ValueError("format pack signature is not valid")
    items = _load_learned(learned)
    existing = {(Fingerprint.from_dict(x.fingerprint).signature) for x in items if x.fingerprint}
    added = 0
    for f in pack.get("formats") or []:
        if not isinstance(f, dict):
            continue
        header = [str(h)[:80] for h in (f.get("header") or [])][:80]
        n = int(f.get("column_count") or len(header))
        headerless = bool(f.get("headerless"))
        if headerless:
            header = [f"col{i}" for i in range(n)]
        if len(header) != n or not n:
            continue
        roles = f.get("roles") or {}
        mapping: dict = {}
        for role, idx in roles.items():
            fld = _ROLE_FIELD.get(role)
            if fld and isinstance(idx, int) and 0 <= idx < n:
                mapping[fld] = header[idx]
        if not mapping.get("date") or not mapping.get("description") or not (
                mapping.get("amount") or mapping.get("debit") or mapping.get("credit")):
            continue
        if isinstance(f.get("date_format"), str) and re.fullmatch(r"[%A-Za-z/.\- ,]{2,20}", f["date_format"]):
            mapping["date_format"] = f["date_format"]
        sign = f.get("sign_convention") or {}
        if mapping.get("amount"):
            mapping["negative_is_out"] = bool(sign.get("negative_is_out", True))
        norm = [_n(h) for h in header]
        sig = _sig(norm, n)
        if sig in existing:
            continue
        fp = Fingerprint(header=header, header_norm=norm, signature=sig, signature_sorted=_sig(sorted(norm), n),
                         column_count=n, columns=[], headerless=headerless)
        lf = LearnedFormat(id=uuid.uuid4().hex[:10], fingerprint=fp.to_dict(), mapping=mapping,
                           sign_convention={k: v for k, v in sign.items() if k in ("style", "negative_is_out")},
                           kind=f.get("kind") if f.get("kind") in ("credit_card", "checking", "savings") else None,
                           source="pack", created=_now())
        items.append(lf)
        existing.add(sig)
        added += 1
    return _trim(items), added


# --------------------------------------------------------------------------- optional LLM assist

@runtime_checkable
class MappingAssistant(Protocol):
    """Optional helper (Pro plugin) that proposes column roles.

    It receives ONLY header names and per-column value *shapes* such as
    "date MM/DD/YYYY", "money signed", "text 12-40 chars" (see
    :func:`column_shapes`) — never raw cell values. Returns {role: header} or None.
    """

    def suggest_mapping(self, headers: list[str], shapes: list[str]) -> Optional[dict[str, str]]: ...


def assistant_request(fp: Fingerprint) -> dict:
    """The exact payload a MappingAssistant may see."""
    return {"headers": [h if not _DIGITS4.search(h) else "#" for h in fp.header], "shapes": column_shapes(fp)}
