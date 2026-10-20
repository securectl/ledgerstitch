# SPDX-License-Identifier: AGPL-3.0-only
"""Merchant normalization.

`normalize_merchant` turns a raw bank descriptor into a stable, upper-case
merchant key that rules, the AI cache and the reconciler can match on:

    "AMAZON MKTPL*BJ1M70883"                       -> "AMAZON MKTPL"
    "SQ *BLUE BOTTLE COFFEE 0123 OAKLAND CA"       -> "SQ *BLUE BOTTLE COFFEE"
    "TST* JOES DINER 1234 AUSTIN TX"               -> "TST* JOES DINER"
    "PURCHASE AUTHORIZED ON 02/05 ACME TOOLS #12 DALLAS TX S3850 CARD 1234"
                                                   -> "ACME TOOLS"
    "ORIG CO NAME:ACME PAYROLL ORIG ID:123 ..."    -> "ACME PAYROLL"

The function is idempotent: it is applied until a fixed point is reached, so
``normalize_merchant(normalize_merchant(x)) == normalize_merchant(x)``.
"""
from __future__ import annotations

import re

__all__ = ["normalize_merchant", "clean_description", "US_STATES"]

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC", "PR",
}
# State codes that are also common English words / business suffixes. They are
# stripped only when clearly a location (followed by a ZIP, or separated from
# the merchant by a wide gap in the raw descriptor).
_AMBIGUOUS_STATES = {"CO", "IN", "OR", "ME", "OK", "HI", "DE", "PA", "MA", "LA", "AL", "ID", "OH"}

# First words of multi-word US city names ("SAN JOSE", "FORT WORTH").
_CITY_PREFIXES = {
    "SAN", "SANTA", "LOS", "LAS", "NEW", "FORT", "FT", "ST", "SAINT", "EL", "PALO", "SALT",
    "GRAND", "NORTH", "SOUTH", "WEST", "EAST", "LONG", "CEDAR", "BATON", "CORPUS", "COLLEGE",
    "OKLAHOMA", "KANSAS", "JERSEY", "SIOUX", "ROUND", "SUGAR", "GRAPEVINE", "MOUNT", "MT",
}

# Leading boilerplate added by banks before the actual merchant.
_PREFIXES = [
    r"(?:RECURRING |RECURRING PAYMENT |)PURCHASE AUTHORIZED ON \d{1,2}/\d{1,2}(?:/\d{2,4})?",
    r"RECURRING PAYMENT AUTHORIZED ON \d{1,2}/\d{1,2}(?:/\d{2,4})?",
    r"PURCHASE RETURN AUTHORIZED ON \d{1,2}/\d{1,2}(?:/\d{2,4})?",
    r"NON-WF ATM WITHDRAWAL AUTHORIZED ON \d{1,2}/\d{1,2}",
    r"CHECKCARD \d{4}",
    r"CHECK CARD PURCHASE",
    r"DEBIT CARD PURCHASE(?: -)?",
    r"DEBIT CARD PURCHASE RETURN",
    r"POS (?:PURCHASE|DEBIT|PUR|REFUND)(?: -)?",
    r"POS",
    r"DBT PURCHASE(?: -)?",
    r"CARD PURCHASE(?: WITH PIN)?(?: -)?",
    r"PURCHASE(?: -)? \d{4}",
    r"VISA (?:PURCHASE|DEBIT)",
    r"ACH (?:DEBIT|CREDIT|WITHDRAWAL)(?: -)?",
    r"< BUSINESS TO BUSINESS ACH (?:DEBIT|CREDIT) -",
    r"BUSINESS TO BUSINESS ACH (?:DEBIT|CREDIT) -",
    r"<",
]
_PREFIX_RE = re.compile(r"^(?:" + "|".join(_PREFIXES) + r")\s+")

# Everything from these markers onward is reference noise.
_TRUNCATE_RE = re.compile(
    r"\s(?:REF\s*#|REF NUMBER|REFERENCE\s*#|REFERENCE NUMBER|TRANSACTION\s*#|CONF(?:IRMATION)?\s*#|"
    r"TRACE\s*#|TRN\s*[:#*]|SRF\s*#|ORIG ID:|DESC DATE:|CO ENTRY|IND ID:|IND NAME:|WEB ID:|PPD ID:|"
    r"CCD ID:|ID:|ATM ID|SEQUENCE:|ACCOUNT ENDING|CARD ENDING|CARD \d{4}\b|CARD X+\d{4}\b|"
    r"ON \d{1,2}/\d{1,2}(?:/\d{2,4})?\b)"
)

_ORIG_CO_RE = re.compile(r"ORIG CO NAME:\s*(.+?)(?:\s+ORIG ID:|\s+DESC DATE:|\s+CO ENTRY|$)")

_PHONE_RE = re.compile(r"\b(?:\+?1[ -.]?)?\(?\d{3}\)?[ -.]\d{3}[ -.]?\d{4}\b|\b\d{3}-[A-Z0-9]{7}\b")
_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_MASK_RE = re.compile(r"\b(?:X{2,}|\*{2,})[X*]*\d{2,}\b")
_ZIP_RE = re.compile(r"\s\d{5}(?:-\d{4})?$")

# P2P / aggregator prefixes kept as part of the name (normalized spacing).
_SQ_RE = re.compile(r"^SQ\s*\*\s*")
_TST_RE = re.compile(r"^TST\s*\*\s*")



def clean_description(description: str) -> str:
    """Whitespace-collapsed upper-case description (no other changes)."""
    return " ".join((description or "").upper().split())


def _strip_star_ids(s: str) -> str:
    """Drop `*ID...` suffixes like `AMAZON MKTPL*BJ1M70883 AMZN.COM/BILL WA`.

    A `*` followed by a token containing a digit starts an order/transaction
    id: everything from the star onward is dropped. A star followed by a word
    (``GOOGLE *YOUTUBE``, ``PAYPAL *NETFLIX``) is kept.
    """
    head = ""
    for p in ("SQ *", "TST* "):
        if s.startswith(p):
            head, s = p, s[len(p):]
            break
    for m in re.finditer(r"\*", s):
        rest = s[m.end():].lstrip()
        token = rest.split(" ", 1)[0] if rest else ""
        if token and any(ch.isdigit() for ch in token):
            s = s[: m.start()]
            break
    return (head + s).strip()


def _is_noise_token(tok: str) -> bool:
    if tok in {"#", "-", "/", "*", ".", ",", "&"}:
        return False if tok == "&" else True
    t = tok.strip(",.;:")
    if not t:
        return True
    if re.fullmatch(r"#\S*", t):
        return True
    if re.fullmatch(r"(?:NO\.?|STORE|STR|STE|UNIT)", t):
        return False
    if re.fullmatch(r"\d[\d\-./]*", t):          # pure numbers: store numbers, dates
        return True
    letters = sum(c.isalpha() for c in t)
    digits = sum(c.isdigit() for c in t)
    if digits >= 4 and digits >= letters:         # long alnum ids S3850212345
        return True
    if digits >= 5:
        return True
    return False


def _strip_location(s: str, raw: str) -> str:
    """Remove a trailing ``CITY ST`` location."""
    toks = s.split()
    if len(toks) < 2:
        return s
    last = toks[-1].strip(",.")
    if last not in US_STATES:
        return s
    raw_u = (raw or "").upper().strip()
    wide = re.split(r" {2,}", raw_u)
    has_zip = bool(re.search(r"\b%s\s+\d{5}(?:-\d{4})?\s*$" % last, raw_u))
    if last in _AMBIGUOUS_STATES and len(wide) < 2 and not has_zip:
        return s
    toks = toks[:-1]
    city: list[str] = []
    if len(wide) >= 2:
        seg = wide[-1].split()
        if seg and seg[-1].strip(",.") == last:
            seg = seg[:-1]
        if not seg and len(wide) >= 3:
            seg = wide[-2].split()
        city = [w for w in seg if not _is_noise_token(w)][-3:]
    if city and toks[-len(city):] == city and len(toks) - len(city) >= 1:
        return " ".join(toks[: len(toks) - len(city)])
    if len(toks) >= 2 and re.fullmatch(r"[A-Z][A-Z'.]*", toks[-1]) and "*" not in toks[-2]:
        toks = toks[:-1]
        if len(toks) >= 2 and toks[-1] in _CITY_PREFIXES:
            toks = toks[:-1]
        return " ".join(toks)
    return " ".join(toks)


def _normalize_once(s: str, raw: str) -> str:
    s = clean_description(s)
    if not s:
        return s
    m = _ORIG_CO_RE.search(s)
    if m:
        s = m.group(1)
    # prefixes (repeat: some banks stack them)
    for _ in range(3):
        new = _PREFIX_RE.sub("", s)
        if new == s:
            break
        s = new
    s = _SQ_RE.sub("SQ *", s)
    s = _TST_RE.sub("TST* ", s)
    m = _TRUNCATE_RE.search(" " + s)
    if m and m.start() > 0:
        s = (" " + s)[: m.start()].strip()
    s = _PHONE_RE.sub(" ", s)
    s = _MASK_RE.sub(" ", s)
    s = _DATE_RE.sub(" ", s)
    s = _strip_star_ids(" ".join(s.split()))
    s = re.sub(r"\s(?:AMZN\.COM/BILL|AMAZON\.COM/BILL|WWW\.AMAZON\.CO)\b.*$", "", s)
    s = _ZIP_RE.sub("", s)
    toks = s.split()
    if toks:
        first, rest = toks[0], toks[1:]
        rest = [t for t in rest if not _is_noise_token(t)]
        if _is_noise_token(first) and rest and not re.fullmatch(r"\d{1,3}", first):
            toks = rest
        else:
            toks = [first] + rest
    s = " ".join(toks)
    s = _strip_location(s, raw)
    s = re.sub(r"[\s,;:\-/#.*]+$", "", s)
    if s.endswith(" *") or s.endswith("*"):
        s = s.rstrip("* ").strip()
    if s in ("SQ", "TST"):
        s = s + (" *" if s == "SQ" else "*")
    return " ".join(s.split())


def normalize_merchant(description: str) -> str:
    """Return the normalized, upper-case merchant key for a raw descriptor."""
    raw = description or ""
    s = raw
    for _ in range(8):
        new = _normalize_once(s, s)
        if new == s:
            break
        s = new
    s = s or clean_description(raw)
    return s
