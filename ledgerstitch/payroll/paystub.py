# SPDX-License-Identifier: AGPL-3.0-only
"""Pay stub parser (text-based PDF or plain text).

One layout-generic parser handles the common pay stub families:

* **Rippling-style** "EARNINGS STATEMENT": sections EARNINGS / EMPLOYEE TAXES / DEDUCTIONS with
  CURRENT and YTD columns, plus EMPLOYEE and COMPANY contribution columns on deductions;
* **ADP-style**: rate / hours / this period / year to date, amounts sometimes printed with
  spaces instead of separators (``6 843 29`` = 6,843.29, ``-487 36`` = -487.36), ``*`` marking
  deductions "Excluded from federal taxable wages", and the line "Your federal taxable wages
  this period are $X";
* **generic** fallback: a label followed by current and YTD amounts.

How it works:

1. a PDF is rendered to fixed-column text lines (pdfplumber words, the same extraction the
   statement parser uses), so PDFs and text files go through one code path;
2. SSNs, account and employee numbers are masked (``security.redact.mask_identifiers``) before
   anything else reads the text, so no output can carry them;
3. column header lines ("CURRENT  YTD", "this period  year to date", "COMPANY CURRENT") give the
   column positions; each amount is matched to the nearest header column by its right edge.
   Without headers, the last two amounts on a line are current and YTD;
4. section headers decide what a line is (earning, tax, deduction, employer contribution,
   reimbursement); tax lines are also recognized by their label anywhere;
5. deductions are classified (``classify_deduction``) and their pre-tax status is taken from the
   stub (``*``, "Pre-Tax"), assumed from the kind, or inferred from the FICA arithmetic
   (Social Security tax / 6.2% = gross minus Section 125 deductions) and from the printed
   federal taxable wages (``cross_check``).

This module computes no tax. It reads what the stub says and checks it for consistency.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import combinations
from typing import Optional

from ..security.redact import mask_identifiers
from .limits import limits_for

__all__ = ["Item", "PayStub", "parse_paystub", "parse_paystub_text", "classify_deduction", "parse_amounts",
           "pdf_text_lines", "cross_check", "PRETAX_RETIREMENT", "SEC125_KINDS", "DEDUCTION_KINDS"]

ZERO = Decimal("0")
CENT = Decimal("0.01")

# ----------------------------------------------------------------------------- deduction kinds
#: kind -> (description, reduces federal wages when pre-tax, reduces FICA wages when pre-tax)
DEDUCTION_KINDS: dict[str, tuple[str, bool, bool]] = {
    "pretax_401k": ("401(k) pre-tax deferral", True, False),
    "pretax_403b": ("403(b) pre-tax deferral", True, False),
    "pretax_457b": ("457(b) pre-tax deferral", True, False),
    "roth_401k": ("Roth 401(k) deferral", False, False),
    "roth_403b": ("Roth 403(b) deferral", False, False),
    "ret_aftertax": ("After-tax (non-Roth) retirement contribution", False, False),
    "hsa": ("Health savings account (HSA)", True, True),
    "health_fsa": ("Health flexible spending account (FSA)", True, True),
    "dcap": ("Dependent care FSA (DCAP)", True, True),
    "sec125_medical": ("Medical premium (Sec. 125)", True, True),
    "sec125_dental": ("Dental premium (Sec. 125)", True, True),
    "sec125_vision": ("Vision premium (Sec. 125)", True, True),
    "post_tax": ("Post-tax deduction", False, False),
    "reimbursement": ("Reimbursement (non-taxable)", False, False),
    "other": ("Other deduction", False, False),
}
PRETAX_RETIREMENT = {"pretax_401k", "pretax_403b", "pretax_457b"}
ROTH = {"roth_401k", "roth_403b"}
RETIREMENT = PRETAX_RETIREMENT | ROTH | {"ret_aftertax"}
SEC125_KINDS = {"hsa", "health_fsa", "dcap", "sec125_medical", "sec125_dental", "sec125_vision"}
_ALWAYS_POST = ROTH | {"ret_aftertax", "post_tax", "reimbursement"}

_CLASSIFY: list[tuple[str, re.Pattern]] = [(k, re.compile(p, re.I)) for k, p in [
    ("reimbursement", r"\breimb|\bexpense\s+(?:repay|reimb)"),
    ("dcap", r"dep(?:endent)?\.?\s*care|\bdcap\b|\bdc\s*fsa\b|\bdcfsa\b|child\s*care\s*fsa"),
    ("hsa", r"\bhsa\b|health\s+savings"),
    ("health_fsa", r"\bfsa\b|\bhcfsa\b|\blpfsa\b|flex(?:ible)?\s*spend|health\s*care\s*(?:fsa|flex)|medical\s*(?:fsa|flex)"),
    ("post_tax", r"\bloan\b|life\b|ad&d|\badd\b|critical|accident|hospital|disab|\bltd\b|\bstd\b|legal|\bpet\b|"
                 r"union|dues|garnish|child\s+support|levy|charit|united\s+way|donation|parking|transit|gym"),
    ("roth", r"\broth\b"),
    ("ret_aftertax", r"(?:after|post)[- ]?tax\s*(?:401|403|457|ret|savings)"),
    ("pretax_403b", r"403\s*\(?b\)?"),
    ("pretax_457b", r"457\s*\(?b\)?|\b457\b"),
    ("pretax_401k", r"401\s*\(?k\)?|\b401\b|retire|\bret\b|savings\s+plan|deferred\s+comp|\bdef\s*comp\b"),
    ("sec125_dental", r"dental|\bden\b|\bdntl\b"),
    ("sec125_vision", r"vision|\bvis\b|\bvsn\b|\beye\b"),
    ("sec125_medical", r"medical|\bmed\b|health|\bhlth\b|sec(?:tion)?\.?\s*125|cafeteria|\bppo\b|\bhmo\b|\bhdhp\b|\bepo\b"),
]]


def classify_deduction(label: str) -> tuple[str, Optional[str]]:
    """``(kind, fsa_type)`` for a deduction label. ``fsa_type`` is set for health FSAs only:
    "limited", "general" or "unknown" (stubs rarely say which, and it matters next to an HSA)."""
    for kind, rx in _CLASSIFY:
        if not rx.search(label):
            continue
        if kind == "roth":
            return ("roth_403b" if re.search(r"403", label) else "roth_401k"), None
        if kind == "health_fsa":
            if re.search(r"limited|\blp\s*fsa|\blpfsa|lmt|dental\s*/\s*vision", label, re.I):
                return kind, "limited"
            if re.search(r"general|\bgp\b", label, re.I):
                return kind, "general"
            return kind, "unknown"
        return kind, None
    return "other", None


# ----------------------------------------------------------------------------- data model
@dataclass
class Item:
    """One earnings, tax or deduction line. Amounts are positive for deductions and taxes."""
    label: str
    current: Decimal = ZERO
    ytd: Optional[Decimal] = None
    kind: str = "other"
    pretax: Optional[bool] = None          # excluded from federal taxable wages (box 1)
    fica_exempt: Optional[bool] = None     # excluded from Social Security / Medicare wages
    pretax_source: str = ""                # "stub", "assumed", "inferred (FICA)", "inferred (federal)"
    employer_current: Decimal = ZERO
    employer_ytd: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    hours: Optional[Decimal] = None
    fsa_type: Optional[str] = None
    note: str = ""

    @property
    def ytd_or_current(self) -> Decimal:
        return self.ytd if self.ytd is not None else self.current


@dataclass
class PayStub:
    source: str = ""
    layout: str = "generic"                # "rippling", "adp" or "generic"
    owner: str = "you"
    employer: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    pay_date: Optional[date] = None
    filing_status: Optional[str] = None
    gross_current: Decimal = ZERO
    gross_ytd: Optional[Decimal] = None
    earnings: list[Item] = field(default_factory=list)
    federal_income_tax: Item = field(default_factory=lambda: Item("Federal income tax", kind="tax"))
    social_security: Item = field(default_factory=lambda: Item("Social Security", kind="tax"))
    medicare: Item = field(default_factory=lambda: Item("Medicare", kind="tax"))
    other_taxes: list[Item] = field(default_factory=list)
    deductions: list[Item] = field(default_factory=list)
    net_current: Optional[Decimal] = None
    net_ytd: Optional[Decimal] = None
    federal_taxable_current: Optional[Decimal] = None
    checks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def year(self) -> Optional[int]:
        d = self.pay_date or self.period_end or self.period_start
        return d.year if d else None

    @property
    def reimbursements_in_gross(self) -> tuple[Decimal, Decimal]:
        """(current, ytd) of reimbursement earnings lines included in the printed gross."""
        cur = sum((e.current for e in self.earnings if e.kind == "reimbursement"), ZERO)
        ytd = sum((e.ytd_or_current for e in self.earnings if e.kind == "reimbursement"), ZERO)
        return cur, ytd


# ----------------------------------------------------------------------------- amounts
_STD = re.compile(r"^(\()?(-)?\$?(-)?(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(\))?(-|CR)?(\*)?$", re.I)
_LEAD = re.compile(r"^(\()?(-)?\$?(-)?(\d{1,3})$")
_MID = re.compile(r"^\d{3}$")
_END = re.compile(r"^(\d{2})(\))?(-)?(\*)?$")
_NUMBER = re.compile(r"^-?\$?\d[\d,]*(?:\.\d+)?%?$")


@dataclass
class _Amt:
    value: Decimal
    start: int
    end: int
    star: bool = False


def _tokens(line: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", line)]


def parse_amounts(line: str) -> tuple[list[_Amt], int]:
    """Money amounts on a text line, and the column where the first number starts.

    Understands ``1,234.56``, ``-1,234.56``, ``(12.00)``, ``$5.00``, ``12.00-`` and the
    space-separated form ``6 843 29`` / ``-487 36`` (groups one space apart, cents last).
    A trailing ``*`` sets ``star`` (ADP: excluded from federal taxable wages)."""
    toks = _tokens(line)
    out: list[_Amt] = []
    first_num = -1
    i = 0
    while i < len(toks):
        t, s, e = toks[i]
        star = t.endswith("*") and len(t) > 1
        core = t[:-1] if star else t
        m = _STD.match(core)
        if m:
            v = Decimal(m.group(4).replace(",", "") + "." + m.group(5))
            neg = bool(m.group(2) or m.group(3) or (m.group(1) and m.group(6)) or m.group(7))
            out.append(_Amt(-v if neg else v, s, e, star or bool(m.group(8))))
            first_num = s if first_num < 0 else first_num
            i += 1
            continue
        m = _LEAD.match(t)
        if m and i + 1 < len(toks):
            j, groups, prev_end = i + 1, [m.group(4)], e
            while j < len(toks) and _MID.match(toks[j][0]) and toks[j][1] - prev_end == 1:
                groups.append(toks[j][0])
                prev_end = toks[j][2]
                j += 1
            if j < len(toks) and toks[j][1] - prev_end == 1 and _END.match(toks[j][0]):
                me = _END.match(toks[j][0])
                v = Decimal("".join(groups) + "." + me.group(1))
                neg = bool(m.group(2) or m.group(3) or (m.group(1) and me.group(2)) or me.group(3))
                out.append(_Amt(-v if neg else v, s, toks[j][2], bool(me.group(4))))
                first_num = s if first_num < 0 else first_num
                i = j + 1
                continue
        if _NUMBER.match(core) and first_num < 0 and out == [] and i > 0:
            first_num = s                      # a rate like 52.0833 ends the label
        i += 1
    return out, first_num


def _money_text(s: str) -> Optional[Decimal]:
    amts, _ = parse_amounts(s.strip())
    return abs(amts[0].value) if amts else None


# ----------------------------------------------------------------------------- dates / metadata
_MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
                                        "oct", "nov", "dec"])}
_DATE = (r"(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}-\d{1,2}-\d{4}|"
         r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})")


def _date(s: str) -> Optional[date]:
    s = s.strip()
    try:
        if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", s):
            y = int(m[3])
            return date(y + 2000 if y < 100 else y, int(m[1]), int(m[2]))
        if m := re.fullmatch(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", s, re.I):
            return date(int(m[3]), _MON[m[1].lower()], int(m[2]))
    except (ValueError, KeyError):
        return None
    return None


_PERIOD_RE = re.compile(r"(?:pay\s*period|period)(?:\s*dates?)?\s*:?\s*(" + _DATE + r")\s*(?:-|–|to|through|thru)\s*("
                        + _DATE + ")", re.I)
_PSTART_RE = re.compile(r"period\s*(?:starting|start|begin(?:ning|s)?)(?:\s*date)?\s*:?\s*(" + _DATE + ")", re.I)
_PEND_RE = re.compile(r"period\s*(?:ending|end|ends)(?:\s*date)?\s*:?\s*(" + _DATE + ")", re.I)
_PAYDATE_RE = re.compile(r"(?:pay|check|payment|advice|deposit)\s*date\s*:?\s*(" + _DATE + ")", re.I)
_FILING_RE = re.compile(r"(?:federal\s+)?(?:filing|marital|tax\s+filing)\s+status\s*:?\s*([A-Za-z][A-Za-z /-]*?)"
                        r"(?:\s{2,}|$|,|;|\s+(?:allow|exempt|state|fed\b))", re.I | re.M)
_EMPLOYER_RE = re.compile(r"^\s*(?:employer|company|employer\s+name|company\s+name)\s*:\s*(.+?)(?:\s{2,}|$)",
                          re.I | re.M)
_FED_TAXABLE_RE = re.compile(r"federal\s+taxable\s+wages\s+this\s+period\s+(?:are|is|was)?\s*:?\s*\$?\s*"
                             r"([\d][\d, ]*\.?\d{2})", re.I)
_TITLE_RE = re.compile(r"^(?:earnings\s+statement|statement\s+of\s+earnings.*|pay\s*stub|pay\s+statement|"
                       r"payroll\s+statement|earnings\s+summary|advice\s+of\s+deposit|non-negotiable.*)$", re.I)
_COMPANY_SUFFIX = re.compile(r"\b(?:inc|corp|corporation|llc|llp|ltd|co|company|group|holdings|plc|lp|"
                             r"partners|industries|labs|technologies|systems)\.?$", re.I)


def _normalize_filing(s: str) -> Optional[str]:
    t = s.strip().lower()
    if not t:
        return None
    if "joint" in t or t in ("married", "mfj", "m"):
        return "married filing jointly" if "joint" in t else "married"
    if "separ" in t or t == "mfs":
        return "married filing separately"
    if "head" in t or t == "hoh":
        return "head of household"
    if t.startswith("single") or t == "s":
        return "single"
    return t[:40]


def _employer(lines: list[str]) -> Optional[str]:
    text = "\n".join(lines)
    if m := _EMPLOYER_RE.search(text):
        return m.group(1).strip()
    candidates = []
    for raw in lines[:10]:
        for chunk in re.split(r"\s{2,}", raw.strip()):
            c = chunk.strip()
            if not c or _TITLE_RE.match(c) or ":" in c or re.search(r"\d{2}/\d{2}", c):
                continue
            if re.search(r"[A-Za-z]{2}", c) and not re.search(r"\d", c):
                candidates.append(c)
    for c in candidates:
        if _COMPANY_SUFFIX.search(c):
            return c
    return candidates[0] if candidates else None


# ----------------------------------------------------------------------------- layout / sections
_COL_RE = re.compile(
    r"(?P<er_cur>(?:company|employer|er)\s+(?:current|this\s+period|amount))|"
    r"(?P<er_ytd>(?:company|employer|er)\s+(?:ytd|year\s+to\s+date))|"
    r"(?P<cur>(?:employee\s+)?(?:current(?:\s+period)?|this\s+period|this\s+pay))|"
    r"(?P<ytd>(?:employee\s+)?(?:ytd|y-t-d|year[\s-]+to[\s-]+date))|"
    r"(?P<rate>\brate\b)|(?P<hours>\bhours\b|\bhrs\b|\bunits\b)", re.I)

_SECTIONS: list[tuple[str, re.Pattern]] = [(k, re.compile(p, re.I)) for k, p in [
    ("employer", r"(?:employer|company)(?:\s+paid)?\s+(?:contributions?|benefits|paid\s+benefits)"),
    ("pretax", r"pre[- ]?tax\s+deductions?"),
    ("posttax", r"(?:post|after)[- ]?tax\s+deductions?"),
    ("taxes", r"(?:employee\s+taxes|taxes(?:\s+withheld)?|tax\s+deductions|statutory(?:\s+deductions)?|"
              r"withholdings?|employee\s+tax\s+withholdings?)"),
    ("deductions", r"(?:deductions|voluntary\s+deductions|other\s+deductions|other|benefits(?:\s+deductions)?|"
                   r"employee\s+deductions)"),
    ("reimbursements", r"(?:reimbursements?|non[- ]?taxable(?:\s+(?:pay|earnings|items))?|other\s+pay)"),
    ("earnings", r"(?:earnings|hours\s+and\s+earnings|earnings\s+and\s+hours|gross\s+earnings\s+detail)"),
    ("stop", r"(?:net\s+pay\s+distribution|direct\s+deposits?|pay\s+distribution|deposits?|distribution|"
             r"bank\s+accounts?|time\s+off|pto\s+balances?|accruals?|important\s+notes|messages?)"),
]]

_GROSS_RE = re.compile(r"^(?:total\s+)?gross(?:\s+(?:pay|earnings|wages))?$|^total\s+(?:earnings|pay)$", re.I)
_NET_RE = re.compile(r"^(?:net\s+(?:pay|check|amount|earnings)|take[- ]home(?:\s+pay)?)$", re.I)
_TOTAL_RE = re.compile(r"^(?:total|totals|subtotal)\b", re.I)
_FIT_RE = re.compile(r"\bfed(?:eral)?\.?\s*(?:income\s*)?(?:tax|withholding|w/?h)\b|\bfitw?\b|^federal$", re.I)
_SS_RE = re.compile(r"social\s+security|\boasdi\b|fica[- ]?(?:ss|oasdi)|\bss\s+tax\b|^soc\.?\s*sec|\bss\s*ee\b", re.I)
_MED_RE = re.compile(r"medicare|fica[- ]?med|\bmed\s*ee\b|\bmed\s+tax\b|\bmwt\b", re.I)
_STATE_TAX_RE = re.compile(r"\b(?:state|local|city|county|school|sdi|sui|pfl|fli|wh|w/h|withholding|tax)\b", re.I)
_EARN_RE = re.compile(r"regular|salary|hourly|overtime|\bot\b|bonus|commission|holiday|vacation|\bpto\b|sick|"
                      r"retro|shift|differential|severance|tips?\b|group\s+term|\bgtl\b|imputed|stipend|award", re.I)
_GTL_RE = re.compile(r"group\s+term|\bgtl\b|term\s+life", re.I)
_IMPUTED_RE = re.compile(r"imputed", re.I)
_META_SKIP = re.compile(r"federal\s+taxable\s+wages|excluded\s+from|exemptions|allowances|^\s*rate\b|"
                        r"pay\s*period|period\s+(?:start|end|begin)|pay\s+date|check\s+date|filing\s+status", re.I)


def _section_of(text: str) -> Optional[str]:
    t = re.sub(r"\s*\((?:continued|cont\.?)\)\s*$", "", text.strip(), flags=re.I).rstrip(":").strip()
    for name, rx in _SECTIONS:
        if rx.fullmatch(t):
            return name
    return None


def _header_columns(line: str) -> Optional[dict[str, tuple[int, int]]]:
    if parse_amounts(line)[0]:
        return None
    cols: dict[str, tuple[int, int]] = {}
    for m in _COL_RE.finditer(line):
        name = m.lastgroup
        if name and name not in cols:
            cols[name] = (m.start(), m.end())
    if ("cur" in cols or "ytd" in cols) and len(cols) >= 2:
        return cols
    if "cur" in cols and re.search(r"\b(?:earnings|deductions|taxes|description|item)\b", line, re.I):
        return cols
    return None


def _dist(a: _Amt, col: tuple[int, int]) -> int:
    s, e = col
    return 0 if s - 2 <= a.end <= e + 2 else min(abs(a.end - e), abs(a.end - s))


def _assign(amts: list[_Amt], cols: Optional[dict[str, tuple[int, int]]]) -> dict[str, Decimal]:
    """Map amounts to header columns (monotonic, nearest right edge); without headers the last
    two amounts are current and YTD."""
    if not amts:
        return {}
    if not cols:
        if len(amts) == 1:
            return {"cur": amts[0].value}
        return {"cur": amts[-2].value, "ytd": amts[-1].value}
    names = sorted(cols, key=lambda n: cols[n][1])
    best: tuple[float, list[int]] | None = None

    def rec(ai: int, ci: int, acc: list[int], cost: float):
        nonlocal best
        if best is not None and cost >= best[0]:
            return
        if ai == len(amts):
            best = (cost, list(acc))
            return
        for c in range(ci, len(names)):
            if len(names) - c < len(amts) - ai:
                break
            rec(ai + 1, c + 1, acc + [c], cost + _dist(amts[ai], cols[names[c]]))

    if len(amts) > len(names):
        return _assign(amts, None)
    rec(0, 0, [], 0.0)
    if best is None or any(_dist(amts[i], cols[names[c]]) > 5 for i, c in enumerate(best[1])):
        return _assign(amts, None)                  # the row doesn't sit under these columns
    return {names[c]: amts[i].value for i, c in enumerate(best[1])}


# ----------------------------------------------------------------------------- PDF to text lines
def pdf_text_lines(data: bytes) -> list[str]:
    """Render a text-based PDF to fixed-column text lines (pdfplumber words, clustered into
    lines like ``ingest.pdf_parser``). Words one space apart stay one space apart, so
    space-separated amounts survive; wider gaps keep their approximate column."""
    import pdfplumber

    from ..ingest.pdf_parser import words_to_lines
    out: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for pi, page in enumerate(pdf.pages):
            words = page.extract_words(x_tolerance=1.5, y_tolerance=2.5, keep_blank_chars=False)
            lines = words_to_lines(words, pi)
            widths = sorted((w.x1 - w.x0) / len(w.text) for ln in lines for w in ln.words if w.text)
            cw = widths[len(widths) // 2] if widths else 5.0
            for ln in lines:
                s, prev_x1 = "", None
                for w in sorted(ln.words, key=lambda w: w.x0):
                    if prev_x1 is not None and (w.x0 - prev_x1) / cw < 1.6:
                        s += " " + w.text
                    else:
                        col = int(round(w.x0 / cw))
                        s = (s + "  " if s else s).ljust(col) + w.text
                    prev_x1 = w.x1
                out.append(s)
            out.append("")
    return out


# ----------------------------------------------------------------------------- the parser
def _detect_layout(text: str) -> str:
    t = text.lower()
    if ("this period" in t and "year to date" in t) or "statutory" in t or "period ending" in t \
            or "federal taxable wages" in t:
        return "adp"
    if "earnings statement" in t or ("employee taxes" in t and "ytd" in t):
        return "rippling"
    return "generic"


def parse_paystub(filename: str, data: bytes, owner: str = "you") -> PayStub:
    """Parse a pay stub file (text-based PDF or plain text) into a :class:`PayStub`."""
    if data[:5] == b"%PDF-" or data.lstrip()[:5] == b"%PDF-":
        try:
            lines = pdf_text_lines(data)
        except Exception as e:  # corrupt / encrypted
            st = PayStub(source=filename, owner=owner)
            st.warnings.append(f"could not read PDF ({type(e).__name__})")
            return st
        if not any(ln.strip() for ln in lines):
            st = PayStub(source=filename, owner=owner)
            st.warnings.append("PDF has no text layer (scanned image?); OCR it or use the text version")
            return st
        text = "\n".join(lines)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    return parse_paystub_text(text, source=filename, owner=owner)


def parse_paystub_text(text: str, source: str = "", owner: str = "you") -> PayStub:
    """Parse pay stub text (see the module docstring)."""
    text = mask_identifiers(text.replace("\t", "    ").replace(" ", " "))
    lines = text.splitlines()
    st = PayStub(source=mask_identifiers(source), owner=owner, layout=_detect_layout(text))
    st.employer = _employer(lines)
    if m := _PERIOD_RE.search(text):
        st.period_start, st.period_end = _date(m.group(1)), _date(m.group(2))
    if st.period_start is None and (m := _PSTART_RE.search(text)):
        st.period_start = _date(m.group(1))
    if st.period_end is None and (m := _PEND_RE.search(text)):
        st.period_end = _date(m.group(1))
    if m := _PAYDATE_RE.search(text):
        st.pay_date = _date(m.group(1))
    if m := _FILING_RE.search(text):
        st.filing_status = _normalize_filing(m.group(1))
    if m := _FED_TAXABLE_RE.search(text):
        st.federal_taxable_current = _money_text(m.group(1).strip())
    star_means_pretax = bool(re.search(r"excluded\s+from\s+federal\s+taxable", text, re.I)) or st.layout == "adp"

    section: Optional[str] = None
    cols: Optional[dict[str, tuple[int, int]]] = None
    seen_gross = False
    employer_rows: list[Item] = []
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        hdr = _header_columns(line)
        if hdr is not None:
            cols = hdr
            lead = line[:min(s for s, _ in hdr.values())].strip()
            sec = _section_of(lead) if lead else None
            if sec:
                section = sec
            continue
        sec = _section_of(stripped)
        amts, first_num = parse_amounts(line)
        if sec and not amts:
            section = sec
            continue
        if not amts or _META_SKIP.search(stripped) or section == "stop":
            continue
        cut = min(first_num if first_num >= 0 else len(line), amts[0].start)
        label_raw = line[:cut].strip()
        star = any(a.star for a in amts) or label_raw.endswith("*") or label_raw.startswith("*")
        label = re.sub(r"\s+", " ", label_raw.strip("*").strip()).rstrip(":").strip()
        if not label:
            continue
        vals = _assign(amts, cols)
        rate_hours = {k: v for k, v in vals.items() if k in ("rate", "hours")}
        cur = vals.get("cur", ZERO)
        ytd = vals.get("ytd")
        er_cur, er_ytd = vals.get("er_cur", ZERO), vals.get("er_ytd")
        if "cur" not in vals and "ytd" not in vals and not ("er_cur" in vals or "er_ytd" in vals):
            # only rate/hours matched: fall back to the plain rule
            vals = _assign(amts, None)
            cur, ytd = vals.get("cur", ZERO), vals.get("ytd")

        if (_GROSS_RE.match(label) or _NET_RE.match(label)) and "cur" not in vals:
            vals = _assign(amts, None)
            cur, ytd = vals.get("cur", ZERO), vals.get("ytd")
        if _GROSS_RE.match(label):
            st.gross_current, st.gross_ytd = cur, ytd
            seen_gross = True
            continue
        if _NET_RE.match(label):
            st.net_current, st.net_ytd = cur, ytd
            continue
        if _TOTAL_RE.match(label):
            continue

        if section != "earnings" and (_FIT_RE.search(label) or _SS_RE.search(label) or _MED_RE.search(label)) \
                and not re.search(r"\bstate\b|\bfsa\b|\bhsa\b|premium|insurance|dental|vision", label, re.I):
            tgt = st.social_security if _SS_RE.search(label) else st.medicare if _MED_RE.search(label) \
                else st.federal_income_tax
            if tgt is st.federal_income_tax and re.search(r"\b[A-Z]{2}\b\s+(?:state|income)", label):
                tgt = None
            if tgt is not None:
                tgt.current += abs(cur)
                tgt.ytd = (tgt.ytd or ZERO) + abs(ytd) if ytd is not None else tgt.ytd
                continue
        if section == "taxes":
            st.other_taxes.append(Item(label, abs(cur), abs(ytd) if ytd is not None else None, kind="tax"))
            continue

        is_earning = section == "earnings" or (section is None and not seen_gross and _EARN_RE.search(label)
                                               and not _STATE_TAX_RE.search(label))
        if section == "reimbursements" or (section == "earnings" and re.search(r"\breimb", label, re.I)):
            item = Item(label, abs(cur), abs(ytd) if ytd is not None else None, kind="reimbursement",
                        pretax=False, fica_exempt=False, pretax_source="stub")
            if section == "earnings":
                st.earnings.append(item)
            else:
                st.deductions.append(item)
            continue
        if is_earning:
            kind = "imputed_gtl" if _GTL_RE.search(label) else "imputed_other" if _IMPUTED_RE.search(label) \
                else "wages"
            st.earnings.append(Item(label, cur, ytd, kind=kind, rate=rate_hours.get("rate"),
                                    hours=rate_hours.get("hours")))
            continue

        kind, fsa_type = classify_deduction(label)
        if section == "employer":
            employer_rows.append(Item(label, ZERO, None, kind=kind, employer_current=abs(cur),
                                      employer_ytd=abs(ytd) if ytd is not None else None, fsa_type=fsa_type))
            continue
        item = Item(label, abs(cur), abs(ytd) if ytd is not None else None, kind=kind, fsa_type=fsa_type,
                    employer_current=abs(er_cur), employer_ytd=abs(er_ytd) if er_ytd is not None else None)
        _set_pretax(item, label, star and star_means_pretax, section)
        st.deductions.append(item)

    for er in employer_rows:                   # attach "COMPANY CONTRIBUTIONS" rows to the employee row
        match = next((d for d in st.deductions if d.kind == er.kind and er.kind != "other"
                      and d.employer_current == ZERO and d.employer_ytd is None), None)
        if match:
            match.employer_current, match.employer_ytd = er.employer_current, er.employer_ytd
        else:
            er.pretax, er.fica_exempt, er.pretax_source = None, None, "employer only"
            st.deductions.append(er)

    if st.gross_current == ZERO and st.earnings:
        st.gross_current = sum((e.current for e in st.earnings), ZERO)
        st.gross_ytd = sum((e.ytd_or_current for e in st.earnings), ZERO)
        st.warnings.append("no gross pay line found; gross is the sum of the earnings lines")
    if st.gross_current == ZERO and st.gross_ytd is None:
        st.warnings.append("no earnings found; is this a pay stub?")
    if st.pay_date is None and st.period_end is None:
        st.warnings.append("no pay date or pay period found; the projection needs one (or use --final)")
    for t in (st.federal_income_tax, st.social_security, st.medicare):
        t.ytd = t.ytd if t.ytd is not None else (t.current if t.current else None)
    cross_check(st)
    return st


def _set_pretax(item: Item, label: str, star: bool, section: Optional[str]) -> None:
    fed, fica = DEDUCTION_KINDS[item.kind][1:]
    if re.search(r"(?:post|after)[- ]?tax", label, re.I) or section == "posttax":
        item.pretax, item.fica_exempt, item.pretax_source = False, False, "stub"
        if item.kind in PRETAX_RETIREMENT:
            item.kind = "ret_aftertax"
        return
    if star or re.search(r"pre[- ]?tax", label, re.I) or section == "pretax":
        item.pretax, item.fica_exempt, item.pretax_source = True, fica and item.kind in SEC125_KINDS, "stub"
        return
    if item.kind in _ALWAYS_POST:
        item.pretax, item.fica_exempt, item.pretax_source = False, False, "assumed"
    elif item.kind == "other":
        item.pretax, item.fica_exempt, item.pretax_source = None, None, ""
    else:
        item.pretax, item.fica_exempt, item.pretax_source = fed, fica, "assumed"


# ----------------------------------------------------------------------------- cross-checks
def _subset_matching(cands: list[Item], current: set[int], target: Decimal, tol: Decimal) -> Optional[set[int]]:
    """The subset of ``cands`` (indexes) whose current amounts sum to ``target`` and that differs
    least from ``current``; None when there is no match or the best match is ambiguous."""
    n = len(cands)
    if n > 14:
        return None
    best: list[tuple[int, set[int]]] = []
    for r in range(n + 1):
        for combo in combinations(range(n), r):
            s = sum((cands[i].current for i in combo), ZERO)
            if abs(s - target) <= tol:
                sel = set(combo)
                best.append((len(sel ^ current), sel))
    if not best:
        return None
    best.sort(key=lambda x: x[0])
    if len(best) > 1 and best[0][0] == best[1][0] and best[0][1] != best[1][1]:
        return None
    return best[0][1]


def cross_check(st: PayStub) -> None:
    """Check (and where the arithmetic is unambiguous, correct) pre-tax flags from the FICA and
    federal-taxable-wage arithmetic. Records each check in ``st.checks``."""
    lim = limits_for(st.year or date.today().year)
    reimb_cur, _ = st.reimbursements_in_gross
    gross = st.gross_current - reimb_cur
    deds = [d for d in st.deductions if d.current > 0 and d.kind != "reimbursement"]
    tol = Decimal("1.00")

    ss = st.social_security.current
    ss_capped = (st.social_security.ytd or ZERO) >= lim.ss_max_tax - tol
    if ss > 0 and gross > 0 and not ss_capped:
        ss_wages = (ss / lim.ss_rate).quantize(CENT)
        target = gross - ss_wages
        cands = [d for d in deds if d.kind not in _ALWAYS_POST and d.kind not in RETIREMENT]
        current = {i for i, d in enumerate(cands) if d.fica_exempt}
        expected = sum((cands[i].current for i in current), ZERO)
        chk = {"check": "social_security_wages", "gross": str(gross), "implied_wages": str(ss_wages),
               "implied_pretax": str(target.quantize(CENT)), "flagged_pretax": str(expected)}
        if abs(target - expected) <= tol:
            chk.update(ok=True, note="Social Security tax matches gross minus the Sec. 125 deductions.")
        else:
            sel = _subset_matching(cands, current, target, tol)
            if sel is not None:
                changed = []
                for i, d in enumerate(cands):
                    want = i in sel
                    if bool(d.fica_exempt) != want:
                        d.fica_exempt = want
                        if want:
                            d.pretax = True
                        d.pretax_source = "inferred (FICA)"
                        changed.append(f"{d.label} ({'pre-tax' if want else 'post-tax'} for FICA)")
                chk.update(ok=True, inferred=changed,
                           note="Pre-tax status inferred from Social Security tax / 6.2%: " + "; ".join(changed))
            else:
                chk.update(ok=False, note=f"Social Security tax implies {target.quantize(CENT)} of pre-tax "
                                          f"(Sec. 125) deductions, the stub's lines give {expected}.")
                st.warnings.append(chk["note"])
        st.checks.append(chk)

    med = st.medicare.current
    if med > 0 and gross > 0:
        med_wages = (med / lim.medicare_rate).quantize(CENT)
        fica_pre = sum((d.current for d in deds if d.fica_exempt), ZERO)
        ok = abs((gross - fica_pre) - med_wages) <= Decimal("2.00")
        st.checks.append({"check": "medicare_wages", "implied_wages": str(med_wages),
                          "expected_wages": str(gross - fica_pre), "ok": ok,
                          "note": "Medicare tax matches." if ok else
                          "Medicare tax does not match 1.45% of gross minus Sec. 125 deductions "
                          "(Additional Medicare above $200,000, or a different pre-tax set)."})

    if st.federal_taxable_current is not None and gross > 0:
        target = gross - st.federal_taxable_current
        cands = [d for d in deds if d.kind not in _ALWAYS_POST]
        current = {i for i, d in enumerate(cands) if d.pretax}
        expected = sum((cands[i].current for i in current), ZERO)
        chk = {"check": "federal_taxable_wages", "printed": str(st.federal_taxable_current),
               "implied_pretax": str(target), "flagged_pretax": str(expected)}
        if abs(target - expected) <= tol:
            chk.update(ok=True, note="Printed federal taxable wages match gross minus the pre-tax deductions.")
        else:
            sel = _subset_matching(cands, current, target, tol)
            if sel is not None:
                changed = []
                for i, d in enumerate(cands):
                    want = i in sel
                    if bool(d.pretax) != want:
                        d.pretax = want
                        d.pretax_source = "inferred (federal)"
                        changed.append(f"{d.label} ({'pre-tax' if want else 'post-tax'})")
                chk.update(ok=True, inferred=changed, note="Pre-tax status inferred from the printed federal "
                                                           "taxable wages: " + "; ".join(changed))
            else:
                chk.update(ok=False, note=f"Printed federal taxable wages imply {target} of pre-tax deductions; "
                                          f"the flagged lines give {expected}.")
                st.warnings.append(chk["note"])
        st.checks.append(chk)
    for d in st.deductions:                    # unknown lines that no check claimed stay post-tax
        if d.pretax is None and d.pretax_source != "employer only":
            d.pretax, d.fica_exempt, d.pretax_source = False, False, "unknown (treated as post-tax)"
