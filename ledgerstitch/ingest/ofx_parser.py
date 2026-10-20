# SPDX-License-Identifier: AGPL-3.0-only
"""OFX / QFX / QBO statement parser (pure Python).

* OFX 1.x is SGML: leaf tags are usually not closed (``<TRNAMT>-4.50``),
  aggregates are (``</STMTTRN>``). A tolerant tokenizer builds a small tree.
* OFX 2.x is XML. It is parsed with ``defusedxml`` (no DTD / entity
  expansion); if defusedxml refuses the document (DOCTYPE, entities) it falls
  back to the SGML tokenizer on the text with the DOCTYPE stripped, so
  external entities are never resolved.
* QFX (Quicken) and QBO (QuickBooks bank downloads) are OFX bodies with an
  ``<INTU.BID>`` tag in the sign-on block.

Only the last four digits of an account number are ever kept. ``FITID`` (the
bank's stable transaction id) is hashed into ``Transaction.seq`` so that two
overlapping downloads produce the same transaction ids and de-duplicate on
import (``make_txn_id`` is unchanged).
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from ..models import Direction, Transaction
from .detect import parse_money
from .normalize import normalize_merchant

__all__ = ["sniff_ofx", "ofx_info", "parse_ofx", "parse_ofx_date", "fitid_seq"]


# --------------------------------------------------------------------------- tree

@dataclass
class _Node:
    tag: str
    text: str = ""
    children: list["_Node"] = field(default_factory=list)

    def iter(self, tag: str):
        for c in self.children:
            if c.tag == tag:
                yield c
            yield from c.iter(tag)

    def find(self, tag: str) -> Optional["_Node"]:
        return next(self.iter(tag), None)

    def text_of(self, tag: str) -> str:
        """Text of the first descendant leaf named `tag` ("" if absent)."""
        n = self.find(tag)
        return n.text.strip() if n is not None else ""

    def child_text(self, tag: str) -> str:
        for c in self.children:
            if c.tag == tag:
                return c.text.strip()
        return ""


_TOKEN_RE = re.compile(r"<(/?)([A-Za-z0-9_.:-]+)[^>]*>([^<]*)", re.S)


def _sgml_tree(text: str) -> _Node:
    """Tolerant OFX SGML (and plain XML) tokenizer."""
    root = _Node("#root")
    stack = [root]
    i = text.upper().find("<OFX")
    body = text[i:] if i >= 0 else text
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"<[?!][^>]*>", "", body)
    last_leaf: Optional[_Node] = None
    for m in _TOKEN_RE.finditer(body):
        closing, tag, txt = m.group(1) == "/", m.group(2).upper(), m.group(3)
        if closing:
            if last_leaf is not None and last_leaf.tag == tag:   # </NAME> after a leaf value
                last_leaf = None
                continue
            last_leaf = None
            # pop up to the matching aggregate (ignore stray closers)
            for j in range(len(stack) - 1, 0, -1):
                if stack[j].tag == tag:
                    del stack[j:]
                    break
            continue
        value = txt.strip()
        if value:
            node = _Node(tag, html.unescape(value))
            stack[-1].children.append(node)
            last_leaf = node
        else:
            node = _Node(tag)
            stack[-1].children.append(node)
            stack.append(node)
            last_leaf = None
    return root


def _xml_tree(text: str) -> Optional[_Node]:
    try:
        import defusedxml.ElementTree as DET
    except ImportError:  # pragma: no cover - fall back to the tokenizer
        return None
    i = text.find("<OFX")
    if i < 0:
        return None
    try:
        el = DET.fromstring(text[i:].encode("utf-8"))
    except Exception:   # DTDForbidden / EntitiesForbidden / ParseError
        return None

    def conv(e) -> _Node:
        tag = (e.tag.split("}")[-1] if isinstance(e.tag, str) else str(e.tag)).upper()
        n = _Node(tag, (e.text or "").strip() if len(e) == 0 else "")
        n.children = [conv(c) for c in e]
        return n
    root = _Node("#root")
    root.children = [conv(el)]
    return root


# --------------------------------------------------------------------------- decoding

def _decode(data: bytes) -> str:
    head = data[:2048].decode("ascii", errors="ignore").upper()
    if re.search(r"CHARSET:\s*1252", head) or "WINDOWS-1252" in head:
        return data.decode("cp1252", errors="replace")
    for enc in ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", errors="replace")


def sniff_ofx(data: bytes) -> bool:
    head = data[:4096].decode("latin-1", errors="ignore").upper()
    return "OFXHEADER" in head or "<OFX>" in head or "<?OFX" in head


def _version(text: str) -> str:
    head = text[:1024].upper()
    if "<?OFX" in head or head.lstrip().startswith("<?XML"):
        return "2"
    m = re.search(r"VERSION:\s*(\d)", head)
    return "2" if m and m.group(1) == "2" else "1"


def _tree(text: str) -> _Node:
    if _version(text) == "2":
        t = _xml_tree(text)
        if t is not None:
            return t
        text = re.sub(r"<!DOCTYPE.*?(\[.*?\]\s*)?>", "", text, flags=re.S | re.I)
    return _sgml_tree(text)


# --------------------------------------------------------------------------- helpers

def parse_ofx_date(value: str) -> Optional[date]:
    """"20250105", "20250105120000", "20250105120000.000[-5:EST]" -> date (as written)."""
    m = re.match(r"\s*(\d{4})(\d{2})(\d{2})", value or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _last4(acctid: str) -> Optional[str]:
    digits = re.sub(r"\D", "", acctid or "")
    if len(digits) >= 4:
        return digits[-4:]
    clean = re.sub(r"[^0-9A-Za-z]", "", acctid or "")
    return clean[-4:] if clean else None


def fitid_seq(last4: Optional[str], fitid: str) -> int:
    """Deterministic seq derived from the bank's FITID."""
    return int(hashlib.sha256(f"{last4 or ''}|{fitid}".encode()).hexdigest()[:12], 16)


_ACCT_KIND = {"CHECKING": "checking", "SAVINGS": "savings", "MONEYMRKT": "savings",
              "CREDITLINE": "credit_card", "CD": "savings"}
_OUT_TYPES = {"DEBIT", "PAYMENT", "ATM", "POS", "CHECK", "FEE", "SRVCHG", "DIRECTDEBIT", "REPEATPMT", "CASH"}
_IN_TYPES = {"CREDIT", "INT", "DIV", "DEP", "DIRECTDEP"}


def _statements(root: _Node) -> list[tuple[_Node, dict]]:
    out = []
    for tag in ("STMTRS", "CCSTMTRS"):
        for st in root.iter(tag):
            if tag == "CCSTMTRS":
                acct = st.find("CCACCTFROM")
                kind = "credit_card"
            else:
                acct = st.find("BANKACCTFROM")
                kind = _ACCT_KIND.get((acct.text_of("ACCTTYPE") if acct else "").upper(), "other")
            last4 = _last4(acct.text_of("ACCTID")) if acct is not None else None
            out.append((st, {"kind": kind, "last4": last4, "currency": st.child_text("CURDEF") or None}))
    return out


def _info_from_tree(root: _Node, text: str, filename: str = "") -> dict:
    son = root.find("SONRS")
    org = fid = bid = None
    if son is not None:
        org = son.text_of("ORG") or None
        fid = son.text_of("FID") or None
        bid = son.text_of("INTU.BID") or None
    fn = (filename or "").lower()
    if fn.endswith(".qbo"):
        fmt = "QBO"
    elif fn.endswith(".qfx"):
        fmt = "QFX"
    else:
        fmt = "QFX" if bid else "OFX"
    return {"format": fmt, "org": org, "fid": fid, "intu_bid": bid,
            "accounts": [a for _, a in _statements(root)], "version": _version(text)}


def ofx_info(data: bytes, filename: str = "") -> dict:
    text = _decode(data)
    return _info_from_tree(_tree(text), text, filename)


# --------------------------------------------------------------------------- parse

def parse_ofx(data: bytes, account_id: str, import_id: str,
              filename: str = "") -> tuple[list[Transaction], list[str], dict]:
    """Parse an OFX/QFX/QBO file into transactions, warnings and info."""
    text = _decode(data)
    root = _tree(text)
    info = _info_from_tree(root, text, filename)
    stmts = _statements(root)
    warnings: list[str] = []
    txns: list[Transaction] = []
    seen: set[tuple] = set()
    zero = dup = contra = bad = 0
    ledger: Optional[str] = None
    row = -1
    for st, acct in stmts:
        lb = st.find("LEDGERBAL")
        if lb is not None and ledger is None:
            ledger = lb.text_of("BALAMT") or None
        last4 = acct["last4"]
        for tr in st.iter("STMTTRN"):
            row += 1
            d = parse_ofx_date(tr.child_text("DTPOSTED")) or parse_ofx_date(tr.child_text("DTUSER"))
            amt = parse_money(tr.child_text("TRNAMT"))
            if d is None or amt is None:
                bad += 1
                continue
            if amt == 0:
                zero += 1
                continue
            fitid = tr.child_text("FITID")
            if fitid:
                key = (last4, fitid)
                if key in seen:
                    dup += 1
                    continue
                seen.add(key)
                seq = fitid_seq(last4, fitid)
            else:
                seq = row
            ttype = tr.child_text("TRNTYPE").upper()
            direction = Direction.OUT if amt < 0 else Direction.IN
            if acct["kind"] == "credit_card" and ttype == "PAYMENT":
                pass   # a card payment is money *in* to the card account
            elif (ttype in _OUT_TYPES and direction == Direction.IN) or \
                    (ttype in _IN_TYPES and direction == Direction.OUT):
                contra += 1
            name = tr.child_text("NAME")
            if not name:
                payee = tr.find("PAYEE")
                name = payee.child_text("NAME") if payee is not None else ""
            memo = tr.child_text("MEMO")
            checknum = tr.child_text("CHECKNUM")
            desc = name
            if memo and memo.upper() not in name.upper():
                desc = f"{name} {memo}".strip()
            if not desc and checknum:
                desc = f"CHECK #{checknum}"
            desc = " ".join((desc or ttype or "(no description)").split())
            txns.append(Transaction(account_id=account_id, date=d, description=desc, amount=abs(amt),
                                    direction=direction, import_id=import_id,
                                    merchant=normalize_merchant(desc), seq=seq))
    last4s = {a["last4"] for _, a in stmts if a["last4"]}
    if len(last4s) > 1:
        warnings.append(f"file contains {len(last4s)} accounts; imported into one")
    if not stmts:
        warnings.append("no statement (STMTRS/CCSTMTRS) found in the OFX file")
    if zero:
        warnings.append(f"skipped {zero} zero-amount row(s)")
    if dup:
        warnings.append(f"skipped {dup} duplicate FITID row(s)")
    if bad:
        warnings.append(f"skipped {bad} row(s) with an unreadable date or amount")
    if contra:
        warnings.append(f"{contra} rows have a sign that disagrees with their OFX type; kept the sign")
    if not txns:
        warnings.append("no transactions found")
    info.update({"statements": len(stmts), "reconciled": None, "ledger_balance": ledger})
    return txns, warnings, info
