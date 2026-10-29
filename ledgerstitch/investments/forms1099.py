# SPDX-License-Identifier: AGPL-3.0-only
"""1099 parser: composite brokerage statements (text-based PDF or text) and 1099-B lot CSVs.

One layout-generic parser reads the composite "Tax Reporting Statement" families brokerages
send in February (Fidelity-, Schwab- and Robinhood-style):

* a form heading ("Form 1099-DIV", "2025 1099-INT", "1099-B Summary") sets the current form;
* inside a form, a line ``[Box] 1a[-.):] Total ordinary dividends ....  1,234.56`` gives the box
  by its number; without a number, the label is matched against the form's box names;
* the 1099-B summary is a table: one row per Form 8949 category (box A, B, C, D, E, F or X,
  printed as a letter or as "Short-term ... basis reported to the IRS"), with columns proceeds,
  cost basis, accrued market discount, wash sale loss disallowed and net gain. Column headers
  give the positions (nearest right edge, as in the pay stub parser); without headers the
  amounts are taken in that order by count.

A 1099-B transaction CSV (``parse_lots_csv``) gives one :class:`Lot` per sale: description,
dates, proceeds, cost, wash sale, term and covered status.

Identifiers (TINs, account numbers) are masked before anything reads the text. This module reads
what the payer reported and labels where each total goes on the return; it computes no tax.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from ..payroll.paystub import _date, parse_amounts, pdf_text_lines
from ..security.redact import mask_identifiers

__all__ = ["BCategory", "Lot", "Statement1099", "parse_1099", "parse_1099_text", "parse_lots_csv",
           "is_lots_header", "BOXES", "DESTINATIONS", "CATEGORIES", "category_for", "mask_tax_ids"]

ZERO = Decimal("0")
CENT = Decimal("0.01")

# ----------------------------------------------------------------------------- boxes
#: form -> box -> (label, label pattern used when the line has no box number)
BOXES: dict[str, dict[str, tuple[str, str]]] = {
    "1099-DIV": {
        "1a": ("Total ordinary dividends", r"total\s+ordinary\s+dividends|ordinary\s+dividends"),
        "1b": ("Qualified dividends", r"qualified\s+dividends"),
        "2a": ("Total capital gain distributions", r"(?:total\s+)?capital\s+gain\s+distr"),
        "2b": ("Unrecaptured Sec. 1250 gain", r"unrecap(?:tured|\.)?\s+(?:sec(?:tion|\.)?\s*)?1250"),
        "2c": ("Section 1202 gain", r"(?:sec(?:tion|\.)?\s*)?1202\s+gain"),
        "2d": ("Collectibles (28%) gain", r"collectibles"),
        "3": ("Nondividend distributions", r"non-?dividend\s+distributions?"),
        "4": ("Federal income tax withheld", r"federal\s+(?:income\s+)?tax\s+withheld"),
        "5": ("Section 199A dividends", r"(?:sec(?:tion|\.)?\s*)?199a\s+dividends"),
        "6": ("Investment expenses", r"investment\s+expenses"),
        "7": ("Foreign tax paid", r"foreign\s+tax\s+paid"),
        "12": ("Exempt-interest dividends", r"exempt[- ]interest\s+dividends"),
        "13": ("Specified private activity bond interest dividends", r"private\s+activity\s+bond"),
    },
    "1099-INT": {
        "1": ("Interest income", r"interest\s+income(?!.*u\.?s\.?\s+savings)"),
        "2": ("Early withdrawal penalty", r"early\s+withdrawal\s+penalty"),
        "3": ("Interest on U.S. Savings Bonds and Treasury obligations",
              r"(?:u\.?s\.?\s+)?savings\s+bonds|treasury\s+obligations"),
        "4": ("Federal income tax withheld", r"federal\s+(?:income\s+)?tax\s+withheld"),
        "6": ("Foreign tax paid", r"foreign\s+tax\s+paid"),
        "8": ("Tax-exempt interest", r"tax[- ]exempt\s+interest"),
        "9": ("Specified private activity bond interest", r"private\s+activity\s+bond"),
    },
    "1099-MISC": {
        "2": ("Royalties", r"royalties"),
        "3": ("Other income", r"other\s+income"),
        "4": ("Federal income tax withheld", r"federal\s+(?:income\s+)?tax\s+withheld"),
    },
    "1099-NEC": {
        "1": ("Nonemployee compensation", r"nonemployee\s+compensation"),
        "4": ("Federal income tax withheld", r"federal\s+(?:income\s+)?tax\s+withheld"),
    },
    "1099-R": {
        "1": ("Gross distribution", r"gross\s+distribution"),
        "2a": ("Taxable amount", r"taxable\s+amount(?!\s+not)"),
        "4": ("Federal income tax withheld", r"federal\s+(?:income\s+)?tax\s+withheld"),
    },
}
# text (not money) boxes
TEXT_BOXES = {("1099-DIV", "8"): r"foreign\s+country", ("1099-R", "7"): r"distribution\s+code"}

#: (form, box) -> where the amount goes on the return (informational; nothing is computed)
DESTINATIONS: dict[tuple[str, str], str] = {
    ("1099-DIV", "1a"): "Form 1040 line 3b (Schedule B Part II if over $1,500)",
    ("1099-DIV", "1b"): "Form 1040 line 3a",
    ("1099-DIV", "2a"): "Schedule D line 13",
    ("1099-DIV", "2b"): "Unrecaptured Section 1250 Gain Worksheet (Schedule D instructions)",
    ("1099-DIV", "2c"): "Form 8949 / Schedule D (Section 1202 exclusion)",
    ("1099-DIV", "2d"): "28% Rate Gain Worksheet (Schedule D instructions)",
    ("1099-DIV", "3"): "Not income: reduces the shares' basis (Pub. 550)",
    ("1099-DIV", "4"): "Form 1040 line 25b",
    ("1099-DIV", "5"): "Form 8995 / 8995-A (qualified REIT dividends)",
    ("1099-DIV", "6"): "Included in box 1a; not deductible 2018-2025 and later (§67(g))",
    ("1099-DIV", "7"): "Schedule 3 line 1 (Form 1116, or without it under the §904(j) election)",
    ("1099-DIV", "12"): "Form 1040 line 2a",
    ("1099-DIV", "13"): "Form 6251 line 2g (AMT)",
    ("1099-INT", "1"): "Form 1040 line 2b (Schedule B Part I if over $1,500)",
    ("1099-INT", "2"): "Schedule 1 line 18",
    ("1099-INT", "3"): "Form 1040 line 2b (exempt from state income tax)",
    ("1099-INT", "4"): "Form 1040 line 25b",
    ("1099-INT", "6"): "Schedule 3 line 1 (Form 1116, or without it under the §904(j) election)",
    ("1099-INT", "8"): "Form 1040 line 2a",
    ("1099-INT", "9"): "Form 6251 line 2g (AMT)",
    ("1099-MISC", "2"): "Schedule E Part I",
    ("1099-MISC", "3"): "Schedule 1 line 8z",
    ("1099-MISC", "4"): "Form 1040 line 25b",
    ("1099-NEC", "1"): "Schedule C (or Schedule 1 line 8z if not a business)",
    ("1099-NEC", "4"): "Form 1040 line 25b",
    ("1099-R", "1"): "Form 1040 line 4a (IRA) or 5a (pension)",
    ("1099-R", "2a"): "Form 1040 line 4b (IRA) or 5b (pension)",
    ("1099-R", "4"): "Form 1040 line 25b",
}

#: Form 8949 category -> (term, covered / basis reported, reported on 1099-B, Schedule D line)
CATEGORIES: dict[str, tuple[str, Optional[bool], bool, str]] = {
    "A": ("short", True, True, "Form 8949 Part I box A -> Schedule D line 1a (no adjustments) or 1b"),
    "B": ("short", False, True, "Form 8949 Part I box B -> Schedule D line 2"),
    "C": ("short", None, False, "Form 8949 Part I box C -> Schedule D line 3"),
    "D": ("long", True, True, "Form 8949 Part II box D -> Schedule D line 8a (no adjustments) or 8b"),
    "E": ("long", False, True, "Form 8949 Part II box E -> Schedule D line 9"),
    "F": ("long", None, False, "Form 8949 Part II box F -> Schedule D line 10"),
    "X": ("unknown", False, True, "Undetermined term: Form 8949 box B or E by your holding period"),
}


def category_for(term: str, covered: Optional[bool], reported: bool = True) -> str:
    """Form 8949 box for a term ("short", "long", "unknown") and basis-reported flag."""
    if term not in ("short", "long"):
        return "X"
    if not reported:
        return "C" if term == "short" else "F"
    if covered:
        return "A" if term == "short" else "D"
    return "B" if term == "short" else "E"


# ----------------------------------------------------------------------------- data model
@dataclass
class BCategory:
    """One 1099-B summary row (or the sum of lots) for one Form 8949 category."""
    category: str
    proceeds: Decimal = ZERO
    cost_basis: Optional[Decimal] = None      # None: basis not reported / blank
    market_discount: Decimal = ZERO
    wash_sale_disallowed: Decimal = ZERO
    gain: Optional[Decimal] = None            # proceeds - cost + market discount + wash sale
    label: str = ""

    @property
    def term(self) -> str:
        return CATEGORIES[self.category][0]

    @property
    def destination(self) -> str:
        return CATEGORIES[self.category][3]


@dataclass
class Lot:
    """One sale from a 1099-B transaction CSV."""
    description: str
    symbol: Optional[str] = None
    quantity: Optional[Decimal] = None
    date_acquired: Optional[date] = None      # None: "Various" or blank
    date_sold: Optional[date] = None
    proceeds: Decimal = ZERO
    cost_basis: Optional[Decimal] = None
    wash_sale_disallowed: Decimal = ZERO
    market_discount: Decimal = ZERO
    term: str = "unknown"
    covered: Optional[bool] = None
    category: str = "X"
    gain: Optional[Decimal] = None
    source: str = ""


@dataclass
class Statement1099:
    source: str = ""
    kind: str = "1099"                        # "1099" (composite statement) or "1099-B lots"
    payer: Optional[str] = None
    tax_year: Optional[int] = None
    account: Optional[str] = None             # masked
    recipient_tin: Optional[str] = None       # masked
    forms: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    text_boxes: dict[str, dict[str, str]] = field(default_factory=dict)
    b_summary: list[BCategory] = field(default_factory=list)
    lots: list[Lot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def box(self, form: str, box: str) -> Decimal:
        return self.forms.get(form, {}).get(box, ZERO)


# ----------------------------------------------------------------------------- helpers
_EIN_RE = re.compile(r"(?<![\dX-])\d{2}-\d{7}(?![\d-])")


def mask_tax_ids(text: str) -> str:
    """``mask_identifiers`` plus the EIN form ``12-3456789`` (-> ``XX-XXX6789``)."""
    return _EIN_RE.sub(lambda m: "XX-XXX" + m.group(0)[-4:], mask_identifiers(text))


_FORM_RE = re.compile(r"\b(?:form\s*)?1099\s*-\s*(DIV|INT|B|MISC|NEC|R|OID|K|CONS)\b", re.I)
_YEAR_RE = re.compile(r"\b(?:tax\s+year|year)\s*:?\s*(20\d{2})\b|\b(20\d{2})\s+(?:form\s+)?1099\b|"
                      r"\b(20\d{2})\s+tax\s+(?:reporting|information)\b|"
                      r"\b1099[^\n]{0,40}?\b(20\d{2})\b", re.I)
_PAYER_RE = re.compile(r"^\s*(?:payer(?:'s)?\s+name|payer|issuer|filer(?:'s)?\s+name)\s*:\s*(.+?)(?:\s{2,}|$)",
                       re.I | re.M)
_ACCOUNT_RE = re.compile(r"\b(?:account|acct)(?:\s*(?:no\.?|number|#))?\s*:?\s*([X*•.\d][X*•.\d-]{2,}\d)", re.I)
_RTIN_RE = re.compile(r"\brecipient(?:'s)?\s+(?:tin|ssn|taxpayer\s+id(?:entification)?(?:\s+number)?)"
                      r"\s*:?\s*([X*\d][X*\d-]{3,}\d)", re.I)
_BOX_RE = re.compile(r"^\s*(?:box\s*)?(\d{1,2}[a-h]?)\s*(?:[-–.:)]\s*|\s+)(?=[A-Za-z(])(.*)$", re.I)
_PAYER_HINT = re.compile(r"\b(?:brokerage|securities|investments?|financial|markets|bank|capital|"
                         r"advisors|trust|clearing|inc|llc|corp|n\.a\.)\b\.?", re.I)
_BOILER = re.compile(r"tax\s+(?:reporting|information)\s+statement|recipient|copy\s+b|omb\s+no|"
                     r"department\s+of\s+the\s+treasury|composite|summary|form\s+1099|important\s+tax", re.I)


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _payer(lines: list[str]) -> Optional[str]:
    text = "\n".join(lines)
    if m := _PAYER_RE.search(text):
        return m.group(1).strip()
    for raw in lines[:12]:
        for chunk in re.split(r"\s{2,}", raw.strip()):
            chunk = chunk.strip()
            if 3 <= len(chunk) <= 60 and _PAYER_HINT.search(chunk) and not _BOILER.search(chunk) \
                    and not parse_amounts(chunk)[0] and not re.search(r"\d{3,}", chunk):
                return chunk
    return None


# 1099-B summary: row categories and column headers
_B_LETTER = [re.compile(r"\bbox\s*\(?([A-FX])\)?(?![A-Za-z])", re.I),
             re.compile(r"\(\s*([A-FX])\s*\)"),
             re.compile(r"^\s*([A-FX])\s*(?:[-–:]\s+|\s+\()")]
_B_NONCOVERED = re.compile(r"non-?covered|basis\s+(?:is\s+)?not\s+reported|not\s+reported\s+to\s+the\s+irs", re.I)
_B_NOT_ON_1099B = re.compile(r"not\s+reported\s+on\s+(?:form\s+)?1099-?b|1099-?b\s+not\s+received", re.I)
_B_COVERED = re.compile(r"\bcovered\b|basis\s+(?:is\s+)?reported", re.I)
_B_COLS = [("proceeds", re.compile(r"proceeds|sales\s+price", re.I)),
           ("market_discount", re.compile(r"market\s+discount|accrued", re.I)),
           ("wash", re.compile(r"wash", re.I)),
           ("cost", re.compile(r"cost|basis", re.I)),
           ("gain", re.compile(r"gain|loss|net", re.I))]
_B_ORDER = {5: ["proceeds", "cost", "market_discount", "wash", "gain"], 4: ["proceeds", "cost", "wash", "gain"],
            3: ["proceeds", "cost", "gain"], 2: ["proceeds", "cost"], 1: ["proceeds"]}


def _b_category(label: str) -> Optional[str]:
    for rx in _B_LETTER:
        if m := rx.search(label):
            return m.group(1).upper()
    t = label.lower()
    term = "short" if re.search(r"short", t) else "long" if re.search(r"long", t) else None
    if term is None:
        return "X" if re.search(r"undetermined|unknown\s+term", t) else None
    if _B_NOT_ON_1099B.search(label):
        return category_for(term, None, reported=False)
    if _B_NONCOVERED.search(label):
        return category_for(term, False)
    if _B_COVERED.search(label):
        return category_for(term, True)
    return None


def _b_header(line: str) -> Optional[dict[str, int]]:
    """Column name -> right edge, for a 1099-B summary header line."""
    if parse_amounts(line)[0] or not re.search(r"proceeds", line, re.I):
        return None
    cols: dict[str, int] = {}
    for m in re.finditer(r"\S+(?: \S+)*", line):
        cell = m.group(0)
        for name, rx in _B_COLS:
            if rx.search(cell) and name not in cols:
                cols[name] = m.end()
                break
    return cols if len(cols) >= 2 else None


def _b_assign(amts, cols: Optional[dict[str, int]]) -> dict[str, Decimal]:
    """Amounts -> columns: in order, each to a later column, least total distance between right
    edges (blank cells are skipped). Without headers, or off the columns, by count."""
    if cols and len(amts) <= len(cols):
        names = sorted(cols, key=cols.get)
        best: Optional[tuple[int, list[int]]] = None

        def rec(ai: int, ci: int, acc: list[int], cost: int) -> None:
            nonlocal best
            if best is not None and cost >= best[0]:
                return
            if ai == len(amts):
                best = (cost, list(acc))
                return
            for c in range(ci, len(names) - (len(amts) - ai) + 1):
                rec(ai + 1, c + 1, acc + [c], cost + abs(cols[names[c]] - amts[ai].end))

        rec(0, 0, [], 0)
        if best and all(abs(cols[names[c]] - amts[i].end) <= 8 for i, c in enumerate(best[1])):
            return {names[c]: amts[i].value for i, c in enumerate(best[1])}
    order = _B_ORDER.get(len(amts), _B_ORDER[5])
    return {name: a.value for name, a in zip(order, amts)}


# ----------------------------------------------------------------------------- the parser
def parse_1099(filename: str, data: bytes) -> Statement1099:
    """Parse a 1099 statement file (text-based PDF or plain text)."""
    if data.lstrip()[:5] == b"%PDF-":
        try:
            lines = pdf_text_lines(data)
        except Exception as e:  # corrupt / encrypted
            st = Statement1099(source=mask_tax_ids(filename))
            st.warnings.append(f"could not read PDF ({type(e).__name__})")
            return st
        if not any(ln.strip() for ln in lines):
            st = Statement1099(source=mask_tax_ids(filename))
            st.warnings.append("PDF has no text layer (scanned image?); OCR it or use the CSV download")
            return st
        text = "\n".join(lines)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    return parse_1099_text(text, source=filename)


def parse_1099_text(text: str, source: str = "") -> Statement1099:
    """Parse 1099 statement text (see the module docstring)."""
    text = mask_tax_ids(text.replace("\t", "    ").replace(" ", " "))
    lines = text.splitlines()
    st = Statement1099(source=mask_tax_ids(source))
    st.payer = _payer(lines)
    if m := _YEAR_RE.search(text):
        st.tax_year = int(next(g for g in m.groups() if g))
    if m := _ACCOUNT_RE.search(text):
        st.account = m.group(1).strip()
    if m := _RTIN_RE.search(text):
        st.recipient_tin = m.group(1).strip()

    form: Optional[str] = None
    b_cols: Optional[dict[str, int]] = None
    b_rows: dict[str, BCategory] = {}
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        fm = _FORM_RE.search(line)
        if fm:
            new = "1099-" + fm.group(1).upper()
            if new != form:                    # a new form: the token is a heading, not a label
                form, b_cols = new, None
                line = _FORM_RE.sub(" ", line, count=1)
                if not parse_amounts(line)[0] and not any(re.search(p, line, re.I)
                                                          for (f, _), p in TEXT_BOXES.items() if f == form):
                    continue
            elif not parse_amounts(line)[0] and not _b_category(line):
                continue
        if form is None:
            continue
        if form == "1099-B":
            if hdr := _b_header(line):
                b_cols = hdr
                continue
            amts, _ = parse_amounts(line)
            label = line[:amts[0].start] if amts else line
            cat = _b_category(label)
            if cat is None or not amts:
                continue
            vals = _b_assign(amts, b_cols)
            row = b_rows.setdefault(cat, BCategory(category=cat, label=" ".join(label.split())))
            row.proceeds += vals.get("proceeds", ZERO)
            if "cost" in vals:
                row.cost_basis = (row.cost_basis or ZERO) + vals["cost"]
            row.market_discount += vals.get("market_discount", ZERO)
            row.wash_sale_disallowed += abs(vals.get("wash", ZERO))
            if "gain" in vals:
                row.gain = (row.gain or ZERO) + vals["gain"]
            continue
        if form not in BOXES:
            continue
        _box_line(st, form, line)

    for row in b_rows.values():
        computed = None
        if row.cost_basis is not None:
            computed = row.proceeds - row.cost_basis + row.market_discount + row.wash_sale_disallowed
        if row.gain is None:
            row.gain = computed
        elif computed is not None and abs(row.gain - computed) > CENT:
            st.warnings.append(f"1099-B box {row.category}: printed net {row.gain:,.2f} differs from proceeds - "
                               f"cost + adjustments = {computed:,.2f}; the printed figure is used")
        if row.cost_basis is None and row.proceeds:
            st.warnings.append(f"1099-B box {row.category}: no cost basis reported for {row.proceeds:,.2f} "
                               "of proceeds")
    st.b_summary = sorted(b_rows.values(), key=lambda r: "ABCDEFX".index(r.category))
    if not st.forms and not st.b_summary:
        st.warnings.append("no 1099 boxes found; is this a 1099 tax statement with a text layer?")
    for f, boxes in st.forms.items():
        if f == "1099-DIV" and boxes.get("1b", ZERO) > boxes.get("1a", ZERO) > ZERO:
            st.warnings.append("1099-DIV: qualified dividends (1b) exceed total ordinary dividends (1a)")
        if f == "1099-R" and boxes.get("2a", ZERO) > boxes.get("1", ZERO) > ZERO:
            st.warnings.append("1099-R: taxable amount (2a) exceeds the gross distribution (1)")
    return st


def _box_line(st: Statement1099, form: str, line: str) -> None:
    boxes = BOXES.get(form, {})
    for (f, box), pat in TEXT_BOXES.items():
        if f == form and re.search(pat, line, re.I):
            m = re.search(pat + r"[^:]*?(?::|\s{2,})\s*(\S.*?)\s*$", line, re.I)
            if m and not parse_amounts(m.group(1))[0]:
                st.text_boxes.setdefault(form, {})[box] = m.group(1)[:40]
            return
    amts, _ = parse_amounts(line)
    if not amts:
        return
    label = line[:amts[-1].start]              # "Box 3 - ..." : the box number is not a rate
    box = None
    if m := _BOX_RE.match(label):
        if m.group(1).lower() in boxes:
            box = m.group(1).lower()
    if box is None:
        lab = _norm(label)
        for b, (_, pat) in boxes.items():
            if re.search(pat, lab, re.I):
                box = b
                break
    if box is None:
        return
    st.forms.setdefault(form, {})[box] = abs(amts[-1].value)


# ----------------------------------------------------------------------------- 1099-B lots CSV
_LOT_COLS: dict[str, tuple[str, ...]] = {
    "description": ("description", "security description", "security", "description of property", "name",
                    "security name", "investment name"),
    "symbol": ("symbol", "ticker", "symbol/cusip", "instrument"),
    "quantity": ("quantity", "shares", "qty", "quantity sold"),
    "date_acquired": ("date acquired", "acquired", "date opened", "open date", "purchase date", "acquisition date"),
    "date_sold": ("date sold", "date sold or disposed", "sale date", "closed date", "date closed", "close date"),
    "proceeds": ("proceeds", "sales proceeds", "gross proceeds", "total proceeds", "sale proceeds"),
    "cost": ("cost basis", "cost", "cost or other basis", "adjusted cost basis", "cost basis (cb)", "basis"),
    "wash": ("wash sale loss disallowed", "wash sale", "wash sale amount", "disallowed loss",
             "wash sale disallowed", "wash sale loss"),
    "market_discount": ("accrued market discount", "market discount"),
    "gain": ("gain/loss", "realized gain/loss", "gain or loss", "net gain or loss", "gain (loss)",
             "total gain/loss", "gain/(loss)"),
    "term": ("term", "holding period", "short/long", "short-term/long-term", "lt/st", "type"),
    "covered": ("covered", "covered/noncovered", "basis reported to irs", "noncovered", "reported to irs"),
    "box": ("box", "8949 box", "form 8949 box", "8949 code", "category"),
}


def _header_map(row: list[str], spec: dict[str, tuple[str, ...]]) -> dict[str, int]:
    names = [_norm(re.sub(r"[\"']", "", c)) for c in row]
    out: dict[str, int] = {}
    for pass_ in ("exact", "prefix"):
        for key, cands in spec.items():
            if key in out:
                continue
            for i, n in enumerate(names):
                if i in out.values() or not n:
                    continue
                if (pass_ == "exact" and n in cands) or (pass_ == "prefix" and any(n.startswith(c) for c in cands)):
                    out[key] = i
                    break
    return out


def is_lots_header(row: list[str]) -> bool:
    m = _header_map(row, _LOT_COLS)
    return "proceeds" in m and ("date_sold" in m or "cost" in m)


def money(s: str) -> Optional[Decimal]:
    """A CSV money cell: ``$1,234.56``, ``(12.00)``, ``-5``, ``--`` / blank -> None."""
    t = (s or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not t or t in ("--", "-", "n/a", "N/A", "NA") or t.lower() in ("missing", "unknown", "various"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").rstrip("%")
    if t.endswith("-"):
        neg, t = True, t[:-1]
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return -v if neg else v


def _term(s: str, acquired: Optional[date], sold: Optional[date]) -> str:
    t = (s or "").lower()
    if re.search(r"short|\bst\b|^s$", t):
        return "short"
    if re.search(r"long|\blt\b|^l$", t):
        return "long"
    if acquired and sold:
        try:
            anniversary = acquired.replace(year=acquired.year + 1)
        except ValueError:                 # Feb 29
            anniversary = date(acquired.year + 1, 3, 1)
        return "long" if sold > anniversary else "short"
    return "unknown"


def _covered(s: str) -> Optional[bool]:
    t = (s or "").strip().lower()
    if not t:
        return None
    if re.search(r"non-?covered|^n|^no|false|not", t):
        return False
    if re.search(r"covered|^y|^yes|true|reported", t):
        return True
    return None


def parse_lots_csv(filename: str, data: bytes) -> Statement1099:
    """Parse a 1099-B transaction-detail CSV into lots, summed per Form 8949 category."""
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    st = Statement1099(source=mask_tax_ids(filename), kind="1099-B lots")
    hi = next((i for i, r in enumerate(rows) if is_lots_header(r)), None)
    if hi is None:
        st.warnings.append("no 1099-B lot columns found (need proceeds and date sold or cost basis)")
        return st
    for r in rows[:hi]:
        joined = mask_tax_ids(" ".join(c for c in r if c))
        if m := _ACCOUNT_RE.search(joined):
            st.account = m.group(1).strip()
        if m := re.search(r"\b(20\d{2})\b", joined):
            st.tax_year = st.tax_year or int(m.group(1))
        name = re.split(r"\s+(?:form\s+)?1099|\s+tax\s+year|,", joined, maxsplit=1, flags=re.I)[0].strip()
        if st.payer is None and _PAYER_HINT.search(name) and not re.search(r"\d{3,}", name):
            st.payer = name[:60]
    cols = _header_map(rows[hi], _LOT_COLS)

    def cell(r: list[str], key: str) -> str:
        i = cols.get(key)
        return r[i].strip() if i is not None and i < len(r) else ""

    sums: dict[str, BCategory] = {}
    years: list[int] = []
    for n, r in enumerate(rows[hi + 1:], hi + 2):
        if not any(c.strip() for c in r):
            continue
        desc = mask_tax_ids(cell(r, "description") or cell(r, "symbol"))
        if re.match(r"(?i)^\s*(?:total|subtotal|grand total)\b", desc or " ".join(r)):
            continue
        proceeds = money(cell(r, "proceeds"))
        if proceeds is None:
            continue
        acquired = _date(cell(r, "date_acquired")) if cell(r, "date_acquired") else None
        sold = _date(cell(r, "date_sold")) if cell(r, "date_sold") else None
        cost = money(cell(r, "cost"))
        wash = abs(money(cell(r, "wash")) or ZERO)
        md = money(cell(r, "market_discount")) or ZERO
        term = _term(cell(r, "term"), acquired, sold)
        covered = _covered(cell(r, "covered"))
        box = cell(r, "box").upper().strip("() ")
        box = box[-1] if box and box[-1] in CATEGORIES else ""
        cat = box or category_for(term, covered if covered is not None else cost is not None)
        if box:
            term = CATEGORIES[box][0]
        gain = proceeds - cost + md + wash if cost is not None else None
        printed = money(cell(r, "gain"))
        if printed is not None and gain is not None and abs(printed - gain) > CENT:
            st.warnings.append(f"row {n}: printed gain {printed:,.2f} differs from proceeds - cost + wash sale "
                               f"= {gain:,.2f}; the computed figure is used")
        sym = cell(r, "symbol") or None
        lot = Lot(description=desc, symbol=sym, quantity=money(cell(r, "quantity")), date_acquired=acquired,
                  date_sold=sold, proceeds=proceeds, cost_basis=cost, wash_sale_disallowed=wash,
                  market_discount=md, term=term, covered=covered, category=cat, gain=gain, source=st.source)
        st.lots.append(lot)
        if sold:
            years.append(sold.year)
        s = sums.setdefault(cat, BCategory(category=cat, label=f"lots in box {cat}"))
        s.proceeds += proceeds
        if cost is not None:
            s.cost_basis = (s.cost_basis or ZERO) + cost
        s.market_discount += md
        s.wash_sale_disallowed += wash
        if gain is not None:
            s.gain = (s.gain or ZERO) + gain
    if years:
        st.tax_year = max(set(years), key=years.count)
    missing = [lt for lt in st.lots if lt.cost_basis is None]
    if missing:
        st.warnings.append(f"{len(missing)} lot(s) have no cost basis; their gain is unknown until you add it")
    st.b_summary = sorted(sums.values(), key=lambda r: "ABCDEFX".index(r.category))
    if not st.lots:
        st.warnings.append("no sales rows found under the header")
    return st
