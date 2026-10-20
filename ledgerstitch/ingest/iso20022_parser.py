# SPDX-License-Identifier: AGPL-3.0-only
"""Corporate / treasury statement formats: ISO 20022 CAMT, SWIFT MT940, BAI2.

These are **Enterprise / corporate formats**. LedgerStitch ships *basic*
support so a small business whose bank only offers "cash management" exports
can still import them; edge cases of the full standards (batch bookings split
per TxDtls, charges breakdowns, multi-currency equivalents, BAI2 custom type
codes) are reduced to one transaction per booked entry.

* **CAMT.053 / .052 / .054** (XML, any schema version, namespace-agnostic,
  parsed with ``defusedxml`` — DTDs/entities are refused): ``Stmt|Rpt|Ntfctn``
  → ``Ntry`` with ``Amt``, ``CdtDbtInd`` (DBIT = out, CRDT = in), ``RvslInd``
  (true flips the direction), ``BookgDt`` (``ValDt`` fallback), ``Sts``
  (pending ``PDNG`` entries are skipped with a warning). Description = the
  counterparty name (creditor for debits, debtor for credits) + unstructured
  remittance info, falling back to ``AddtlNtryInf``.
* **MT940**: ``:25:`` account, ``:60F:/:60M:`` opening, ``:61:`` statement
  lines (``C``/``D``/``RC``/``RD``; RC = reversal of a credit → out, RD =
  reversal of a debit → in), ``:86:`` information (multi-line, ``?20``–``?29``
  and ``?32/?33`` subfields or ``/REMI/…/NAME/…`` style), ``:62F:/:62M:``
  closing. Several statements per file are supported.
* **BAI2**: ``01/02/03/16/88/49/98/99`` records; ``16`` type codes 100–399
  are credits (in), 400–699 debits (out); others are skipped with a warning.
  Amounts are in cents. ``88`` continuation records extend the previous record.

Only the last four digits of an account / IBAN are ever kept. Bank references
(AcctSvcrRef, MT940 bank reference, BAI2 bank reference) are hashed into
``Transaction.seq`` so re-importing the same file is a no-op and two identical
same-day entries still get distinct ids. Statements carrying opening/closing
balances are reconciled: ``info["reconciled"]`` is True / False / None.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from ..models import Direction, Transaction
from .normalize import normalize_merchant

__all__ = ["sniff_camt", "sniff_mt940", "sniff_bai2", "sniff_iso", "iso_info",
           "parse_camt", "parse_mt940", "parse_bai2", "parse_iso"]

_HEAD = 4096


def _text(data: bytes, n: Optional[int] = None) -> str:
    chunk = data if n is None else data[:n]
    if chunk[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return chunk.decode("utf-16", errors="replace")
        except Exception:  # pragma: no cover
            pass
    try:
        return chunk.decode("utf-8-sig")
    except UnicodeDecodeError:
        return chunk.decode("latin-1", errors="replace")


def _last4(acct: str | None) -> Optional[str]:
    digits = re.sub(r"\D", "", acct or "")
    return digits[-4:] if len(digits) >= 4 else None


def _ref_seq(ref: str, seen: set[str], index: int) -> int:
    """Stable seq from a bank reference (index fallback / disambiguation)."""
    ref = (ref or "").strip()
    if not ref or ref.upper() in ("NONREF", "NOTPROVIDED", "NONE"):
        return index
    key = ref
    n = 1
    while key in seen:          # same reference twice in a file: keep both rows
        n += 1
        key = f"{ref}#{n}"
    seen.add(key)
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)


def _dec(s: str | None) -> Optional[Decimal]:
    if s is None:
        return None
    s = s.strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    s = s.replace(",", "")
    if s.endswith("."):
        s += "0"
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _mk_txn(account_id: str, import_id: str, d: date, desc: str, amount: Decimal,
            direction: Direction, seq: int) -> Transaction:
    desc = " ".join((desc or "").split()) or "(no description)"
    return Transaction(account_id=account_id, date=d, description=desc, amount=abs(amount),
                       direction=direction, import_id=import_id, merchant=normalize_merchant(desc),
                       seq=seq)


def _reconcile(opening: Optional[Decimal], closing: Optional[Decimal], net: Decimal,
               label: str, warnings: list[str]) -> Optional[bool]:
    if opening is None or closing is None:
        return None
    ok = opening + net == closing
    if not ok:
        warnings.append(f"{label}: opening balance plus entries does not equal the closing balance "
                        f"(off by {abs(opening + net - closing)})")
    return ok


def _combine(results: list[Optional[bool]]) -> Optional[bool]:
    vals = [r for r in results if r is not None]
    if not vals:
        return None
    return all(vals)


# =========================================================================== sniffing

_CAMT_NS = re.compile(r"urn:iso:std:iso:20022:tech:xsd:camt\.05([234])")
_CAMT_ROOT = re.compile(r"<(?:\w+:)?(BkToCstmrStmt|BkToCstmrAcctRpt|BkToCstmrDbtCdtNtfctn)\b")


def sniff_camt(data: bytes) -> bool:
    head = _text(data, _HEAD * 4)
    if not head.lstrip().startswith("<"):
        return False
    return bool(_CAMT_NS.search(head) or _CAMT_ROOT.search(head))


def _strip_swift_blocks(text: str) -> str:
    # {1:F01BANK...}{2:...}{3:...}{4:\n ... \n-}{5:...}
    text = re.sub(r"\{[1235]:[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "", text)
    text = text.replace("{4:", "\n")
    return text


def sniff_mt940(data: bytes) -> bool:
    head = _text(data, _HEAD * 4)
    return (bool(re.search(r"(?m)^:20:", head)) and bool(re.search(r"(?m)^:6[0][FM]:", head))
            and bool(re.search(r"(?m)^:61:", head)))


def sniff_bai2(data: bytes) -> bool:
    text = _text(data).strip()
    if not text.startswith("01,"):
        return False
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return any(l.startswith("16,") for l in lines) and lines[-1].startswith("99,")


def sniff_iso(data: bytes) -> Optional[str]:
    """'camt' | 'mt940' | 'bai2' | None."""
    if sniff_camt(data):
        return "camt"
    if sniff_mt940(data):
        return "mt940"
    if sniff_bai2(data):
        return "bai2"
    return None


# =========================================================================== CAMT

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _strip_ns(root) -> None:
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = _local(el.tag)


def _ft(el, path: str) -> Optional[str]:
    if el is None:
        return None
    x = el.find(path)
    if x is None or x.text is None:
        return None
    return x.text.strip()


def _camt_date(el) -> Optional[date]:
    if el is None:
        return None
    s = _ft(el, "Dt") or _ft(el, "DtTm")
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _camt_load(data: bytes):
    import defusedxml.ElementTree as DET
    from defusedxml import DefusedXmlException
    try:
        root = DET.fromstring(data)
    except DefusedXmlException:
        raise ValueError("XML file declares DTD entities, which are refused for safety") from None
    except Exception as e:  # ParseError
        raise ValueError(f"malformed CAMT XML ({type(e).__name__})") from None
    _strip_ns(root)
    return root


def _camt_kind(root) -> tuple[str, str]:
    """(format label, statement element name)."""
    for el in root.iter():
        if el.tag == "BkToCstmrStmt":
            return "CAMT.053", "Stmt"
        if el.tag == "BkToCstmrAcctRpt":
            return "CAMT.052", "Rpt"
        if el.tag == "BkToCstmrDbtCdtNtfctn":
            return "CAMT.054", "Ntfctn"
    return "CAMT.053", "Stmt"


def _camt_account(stmt) -> dict:
    acct = stmt.find("Acct")
    ident = None
    ccy = None
    if acct is not None:
        ident = _ft(acct, "Id/IBAN") or _ft(acct, "Id/Othr/Id")
        ccy = _ft(acct, "Ccy")
    return {"last4": _last4(ident), "currency": ccy}


def _camt_institution(stmt) -> Optional[str]:
    acct = stmt.find("Acct")
    if acct is None:
        return None
    return (_ft(acct, "Svcr/FinInstnId/BICFI") or _ft(acct, "Svcr/FinInstnId/BIC")
            or _ft(acct, "Svcr/FinInstnId/Nm"))


def _camt_signed(el) -> Optional[Decimal]:
    amt = _dec(_ft(el, "Amt"))
    if amt is None:
        return None
    return -amt if (_ft(el, "CdtDbtInd") or "").upper() == "DBIT" else amt


def _camt_balances(stmt) -> tuple[Optional[Decimal], Optional[Decimal]]:
    opening = closing = None
    for bal in stmt.findall("Bal"):
        code = (_ft(bal, "Tp/CdOrPrtry/Cd") or _ft(bal, "Tp/CdOrPrtry/Prtry") or "").upper()
        v = _camt_signed(bal)
        if v is None:
            continue
        if code in ("OPBD", "PRCD") and opening is None:
            opening = v
        elif code == "CLBD" and closing is None:
            closing = v
    return opening, closing


def _camt_status(ntry) -> str:
    sts = ntry.find("Sts")
    if sts is None:
        return "BOOK"
    code = _ft(sts, "Cd") or (sts.text or "").strip()
    return (code or "BOOK").upper()


def _camt_desc(ntry, dbit: bool) -> str:
    parts: list[str] = []
    for tx in ntry.findall("NtryDtls/TxDtls"):
        party = tx.find("RltdPties/Cdtr" if dbit else "RltdPties/Dbtr")
        if party is not None:
            nm = _ft(party, "Pty/Nm") or _ft(party, "Nm")
            if nm and nm not in parts:
                parts.append(nm)
        for u in tx.findall("RmtInf/Ustrd"):
            if u.text and u.text.strip() and u.text.strip() not in parts:
                parts.append(u.text.strip())
        if not parts:
            ati = _ft(tx, "AddtlTxInf")
            if ati:
                parts.append(ati)
    if not parts:
        a = _ft(ntry, "AddtlNtryInf")
        if a:
            parts.append(a)
    return " ".join(parts)


def _camt_ref(ntry) -> str:
    for p in ("AcctSvcrRef", "NtryRef", "NtryDtls/TxDtls/Refs/AcctSvcrRef",
              "NtryDtls/TxDtls/Refs/EndToEndId", "NtryDtls/TxDtls/Refs/TxId"):
        v = _ft(ntry, p)
        if v and v.upper() != "NOTPROVIDED":
            return v
    return ""


def parse_camt(data: bytes, account_id: str, import_id: str) -> tuple[list[Transaction], list[str], dict]:
    warnings: list[str] = []
    try:
        root = _camt_load(data)
    except ValueError as e:
        return [], [str(e)], {"format": "CAMT", "reconciled": None, "accounts": []}
    fmt, stmt_tag = _camt_kind(root)
    stmts = [el for el in root.iter() if el.tag == stmt_tag]
    txns: list[Transaction] = []
    seen: set[str] = set()
    accounts: list[dict] = []
    recon: list[Optional[bool]] = []
    pending = unparsed = idx = 0
    institution = None
    for si, stmt in enumerate(stmts):
        accounts.append(_camt_account(stmt))
        institution = institution or _camt_institution(stmt)
        opening, closing = _camt_balances(stmt)
        net = Decimal(0)
        for ntry in stmt.findall("Ntry"):
            idx += 1
            st = _camt_status(ntry)
            if st in ("PDNG", "PENDING", "INFO"):
                pending += 1
                continue
            amt = _dec(_ft(ntry, "Amt"))
            d = _camt_date(ntry.find("BookgDt")) or _camt_date(ntry.find("ValDt"))
            ind = (_ft(ntry, "CdtDbtInd") or "").upper()
            if amt is None or d is None or ind not in ("DBIT", "CRDT"):
                unparsed += 1
                continue
            dbit = ind == "DBIT"
            out = dbit
            if (_ft(ntry, "RvslInd") or "").lower() in ("true", "1"):
                out = not out
            direction = Direction.OUT if out else Direction.IN
            net += -amt if out else amt
            if amt == 0:
                continue
            txns.append(_mk_txn(account_id, import_id, d, _camt_desc(ntry, dbit), amt, direction,
                                _ref_seq(_camt_ref(ntry), seen, idx - 1)))
        recon.append(_reconcile(opening, closing, net, f"statement {si + 1}", warnings))
    if pending:
        warnings.append(f"skipped {pending} pending (not yet booked) entr{'y' if pending == 1 else 'ies'}")
    if unparsed:
        warnings.append(f"skipped {unparsed} entr{'y' if unparsed == 1 else 'ies'} without a date, amount or direction")
    if len({a.get('last4') for a in accounts}) > 1:
        warnings.append("file contains several accounts; all entries were imported into one account")
    if not stmts:
        warnings.append(f"no {stmt_tag} element found")
    if not txns:
        warnings.append("no transactions found")
    return txns, warnings, {"format": fmt, "accounts": accounts, "institution": institution,
                            "kind": "checking", "reconciled": _combine(recon)}


# =========================================================================== MT940

_TAG_RE = re.compile(r"^:(\d{2}[A-Z]?):(.*)$")
_BAL_RE = re.compile(r"^([CD])(\d{6})([A-Z]{3})([\d,\.]+)")
_L61_RE = re.compile(
    r"^(\d{6})(\d{4})?(RC|RD|C|D)([A-Z])?(\d+(?:[,.]\d*)?)([NSF][A-Z0-9]{3})([^/\n]*)(?://([^\n]*))?")


def _yymmdd(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s, "%y%m%d").date()
    except ValueError:
        return None


def _mt_tags(text: str) -> list[tuple[str, str]]:
    """[(tag, value)] with continuation lines joined by newlines; '-' ends a message."""
    out: list[list[str]] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line:
            continue
        if line in ("-", "-}") or line.startswith("-}"):
            out.append(["-", ""])
            continue
        m = _TAG_RE.match(line)
        if m:
            out.append([m.group(1), m.group(2)])
        elif out and out[-1][0] != "-":
            out[-1][1] += "\n" + line
    return [(t, v) for t, v in out]


def _mt_balance(v: str) -> tuple[Optional[Decimal], Optional[date], Optional[str]]:
    m = _BAL_RE.match(v.strip())
    if not m:
        return None, None, None
    amt = _dec(m.group(4))
    if amt is None:
        return None, None, None
    return (-amt if m.group(1) == "D" else amt), _yymmdd(m.group(2)), m.group(3)


def _mt86_desc(v: str) -> str:
    flat = v.replace("\n", "")
    if re.search(r"\?\d{2}", flat):
        fields = dict()
        for m in re.finditer(r"\?(\d{2})([^?]*)", flat):
            fields.setdefault(m.group(1), []).append(m.group(2))
        text = "".join("".join(fields.get(str(k), [])) for k in range(20, 30))
        name = " ".join(x for x in ("".join(fields.get("32", [])), "".join(fields.get("33", []))) if x)
        return " ".join(p for p in (name.strip(), text.strip()) if p)
    if re.match(r"^/[A-Z]{2,4}/", flat):
        parts = dict()
        for m in re.finditer(r"/([A-Z]{2,4})/(.*?)(?=/[A-Z]{2,4}/|$)", flat):
            parts.setdefault(m.group(1), m.group(2))
        remi = parts.get("REMI", "")
        remi = re.sub(r"^(?:USTD|STRD)//", "", remi).strip("/ ")
        name = parts.get("NAME", "").strip("/ ")
        joined = " ".join(p for p in (name, remi) if p)
        if joined:
            return joined
    return " ".join(v.split())


def parse_mt940(data: bytes, account_id: str, import_id: str) -> tuple[list[Transaction], list[str], dict]:
    warnings: list[str] = []
    text = _strip_swift_blocks(_text(data))
    tags = _mt_tags(text)
    stmts: list[dict] = []
    cur: Optional[dict] = None
    for tag, val in tags:
        if tag == "20" or (cur is None and tag != "-"):
            cur = {"account": None, "opening": None, "closing": None, "lines": [], "ccy": None}
            stmts.append(cur)
            if tag == "20":
                continue
        if tag == "-":
            cur = None
            continue
        if tag == "25":
            cur["account"] = val.strip()
        elif tag in ("60F", "60M"):
            cur["opening"], _, cur["ccy"] = _mt_balance(val)
        elif tag in ("62F", "62M"):
            cur["closing"], _, _ = _mt_balance(val)
        elif tag == "61":
            cur["lines"].append([val, ""])
        elif tag == "86" and cur["lines"]:
            cur["lines"][-1][1] = val
    txns: list[Transaction] = []
    seen: set[str] = set()
    recon: list[Optional[bool]] = []
    accounts: list[dict] = []
    unparsed = idx = 0
    for si, st in enumerate(stmts):
        acct = st["account"] or ""
        accounts.append({"last4": _last4(acct.split("/")[-1]), "currency": st["ccy"]})
        net = Decimal(0)
        for l61, l86 in st["lines"]:
            idx += 1
            first = l61.split("\n", 1)
            m = _L61_RE.match(first[0].strip())
            if not m:
                unparsed += 1
                continue
            vdate = _yymmdd(m.group(1))
            amt = _dec(m.group(5))
            if vdate is None or amt is None:
                unparsed += 1
                continue
            d = vdate
            if m.group(2):
                mm, dd = int(m.group(2)[:2]), int(m.group(2)[2:])
                y = vdate.year
                if vdate.month == 12 and mm == 1:
                    y += 1
                elif vdate.month == 1 and mm == 12:
                    y -= 1
                try:
                    d = date(y, mm, dd)
                except ValueError:
                    d = vdate
            mark = m.group(3)
            out = mark in ("D", "RC")      # RC = reversal of a credit (money out)
            net += -amt if out else amt
            if amt == 0:
                continue
            desc = _mt86_desc(l86) if l86 else ""
            if not desc:
                supp = first[1].strip() if len(first) > 1 else ""
                desc = supp or (m.group(7) or "").strip()
            ref = (m.group(8) or "").strip() or (m.group(7) or "").strip()
            txns.append(_mk_txn(account_id, import_id, d, desc, amt,
                                Direction.OUT if out else Direction.IN, _ref_seq(ref, seen, idx - 1)))
        recon.append(_reconcile(st["opening"], st["closing"], net, f"statement {si + 1}", warnings))
    if unparsed:
        warnings.append(f"skipped {unparsed} :61: line(s) that could not be read")
    if len({a.get('last4') for a in accounts}) > 1:
        warnings.append("file contains several accounts; all entries were imported into one account")
    if not txns:
        warnings.append("no transactions found")
    return txns, warnings, {"format": "MT940", "accounts": accounts, "institution": _mt_institution(data),
                            "kind": "checking", "reconciled": _combine(recon)}


def _mt_institution(data: bytes) -> Optional[str]:
    text = _text(data, _HEAD * 4)
    m = re.search(r"\{1:F\d{2}([A-Z0-9]{8})", text)
    if m:
        return m.group(1)
    m = re.search(r"(?m)^:25:([A-Z][A-Z0-9]{3,10})/", text)
    return m.group(1) if m else None


# =========================================================================== BAI2

def _bai_records(text: str) -> list[list[str]]:
    recs: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.endswith("/"):
            line = line[:-1]
        if line.startswith("88,") and recs:
            # a 16 record's free text continues with a space; other records continue field-wise
            sep = " " if recs[-1].startswith("16,") else ","
            recs[-1] = recs[-1] + sep + line[3:]
        else:
            recs.append(line)
    return [r.split(",") for r in recs]


def _bai_date(s: str) -> Optional[date]:
    return _yymmdd((s or "").strip())


def _bai_cents(s: str, decimals: int = 2) -> Optional[Decimal]:
    s = (s or "").strip()
    if not s:
        return None
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("+-")
    if not s.isdigit():
        return None
    return sign * Decimal(int(s)).scaleb(-decimals)


def _bai_funds_skip(fields: list[str], i: int) -> int:
    """Index after the funds-type group starting at fields[i]."""
    ft = (fields[i] if i < len(fields) else "").strip().upper()
    i += 1
    if ft == "S":
        return i + 3
    if ft == "V":
        return i + 2
    if ft == "D":
        try:
            n = int(fields[i])
        except (ValueError, IndexError):
            return i + 1
        return i + 1 + 2 * n
    return i


def parse_bai2(data: bytes, account_id: str, import_id: str) -> tuple[list[Transaction], list[str], dict]:
    warnings: list[str] = []
    recs = _bai_records(_text(data))
    txns: list[Transaction] = []
    seen: set[str] = set()
    accounts: list[dict] = []
    recon: list[Optional[bool]] = []
    as_of: Optional[date] = None
    group_ccy = None
    institution = None
    acct_state: Optional[dict] = None
    skipped_codes = unparsed = idx = 0

    def close_account():
        nonlocal acct_state
        if acct_state is not None:
            recon.append(_reconcile(acct_state["opening"], acct_state["closing"], acct_state["net"],
                                    f"account {len(recon) + 1}", warnings))
        acct_state = None

    for f in recs:
        rt = f[0].strip()
        if rt == "01":
            institution = institution or (f[1].strip() if len(f) > 1 else None) or None
        elif rt == "02":
            if len(f) > 2 and f[2].strip():
                institution = f[2].strip()
            as_of = _bai_date(f[4]) if len(f) > 4 else None
            group_ccy = f[6].strip() if len(f) > 6 and f[6].strip() else None
        elif rt == "03":
            close_account()
            ccy = (f[2].strip() if len(f) > 2 else "") or group_ccy
            accounts.append({"last4": _last4(f[1] if len(f) > 1 else ""), "currency": ccy})
            acct_state = {"opening": None, "closing": None, "net": Decimal(0)}
            i = 3
            while i + 1 < len(f):
                code = f[i].strip()
                amt = _bai_cents(f[i + 1])
                if code in ("010", "040") and amt is not None and acct_state["opening"] is None:
                    acct_state["opening"] = amt
                elif code == "015" and amt is not None:
                    acct_state["closing"] = amt
                # type, amount, item count, funds type [+ funds extras]
                i = _bai_funds_skip(f, i + 3) if i + 3 < len(f) else len(f)
        elif rt == "16":
            idx += 1
            if len(f) < 3:
                unparsed += 1
                continue
            try:
                code = int(f[1].strip())
            except ValueError:
                unparsed += 1
                continue
            amt = _bai_cents(f[2])
            j = _bai_funds_skip(f, 3)
            bank_ref = f[j].strip() if j < len(f) else ""
            cust_ref = f[j + 1].strip() if j + 1 < len(f) else ""
            text = ",".join(f[j + 2:]).strip() if j + 2 < len(f) else ""
            if 100 <= code <= 399:
                out = False
            elif 400 <= code <= 699:
                out = True
            else:
                skipped_codes += 1
                continue
            if amt is None or as_of is None:
                unparsed += 1
                continue
            if acct_state is not None:
                acct_state["net"] += -amt if out else amt
            if amt == 0:
                continue
            desc = text or (f"CHECK {cust_ref}" if code in (475, 474) and cust_ref else f"BAI type {code}")
            txns.append(_mk_txn(account_id, import_id, as_of, desc, amt,
                                Direction.OUT if out else Direction.IN, _ref_seq(bank_ref, seen, idx - 1)))
        elif rt == "49":
            close_account()
    close_account()
    if skipped_codes:
        warnings.append(f"skipped {skipped_codes} detail record(s) with loan/custom BAI type codes")
    if unparsed:
        warnings.append(f"skipped {unparsed} detail record(s) that could not be read")
    if len({a.get('last4') for a in accounts}) > 1:
        warnings.append("file contains several accounts; all entries were imported into one account")
    if not txns:
        warnings.append("no transactions found")
    return txns, warnings, {"format": "BAI2", "accounts": accounts, "institution": institution,
                            "kind": "checking", "reconciled": _combine(recon)}


# =========================================================================== public

def parse_iso(data: bytes, account_id: str, import_id: str) -> tuple[list[Transaction], list[str], dict]:
    kind = sniff_iso(data)
    if kind == "camt":
        return parse_camt(data, account_id, import_id)
    if kind == "mt940":
        return parse_mt940(data, account_id, import_id)
    if kind == "bai2":
        return parse_bai2(data, account_id, import_id)
    return [], ["not a CAMT, MT940 or BAI2 file"], {"format": None, "accounts": [], "reconciled": None}


def iso_info(data: bytes) -> dict:
    """{format, accounts:[{last4, currency}], institution, kind} without keeping transactions."""
    kind = sniff_iso(data)
    if kind is None:
        return {"format": None, "accounts": [], "institution": None, "kind": "checking"}
    _t, _w, info = parse_iso(data, "_", "_")
    return {"format": info.get("format"), "accounts": info.get("accounts", []),
            "institution": info.get("institution"), "kind": "checking"}
