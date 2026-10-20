# SPDX-License-Identifier: AGPL-3.0-only
"""QuickBooks exports.

* ``export_qbo_csv`` -- QuickBooks Online bank-upload CSV (Date, Description, Amount).
  QBO imports one bank/card account at a time, so with more than one account we
  write a .zip holding one CSV per account.
* ``export_iif`` -- QuickBooks Desktop IIF (accounts + transactions).

Both exclude duplicates. Transfers are included (QBO/QBD need both legs to match).
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from ..models import Account, AccountKind, Direction, Lane, Transaction
from .summary import accounts_by_id, category_of, q2, split_active

# --------------------------------------------------------------------------- shared
_WS = re.compile(r"[\t\r\n]+")


def _clean(s, limit: int | None = None) -> str:
    """Collapse tabs/newlines, drop double quotes (IIF has no quoting)."""
    s = _WS.sub(" ", "" if s is None else str(s)).replace('"', "'").strip()
    s = " ".join(s.split())
    return s[:limit] if limit else s


def _account_name(name: str) -> str:
    """QuickBooks account names: no ':' (sub-account separator), ASCII, <= 31 chars."""
    s = _clean(name).replace(":", "-")
    s = "".join(ch for ch in s if 32 <= ord(ch) < 127)
    s = " ".join(s.split())
    return s[:31].strip() or "Account"


def _mdy(d) -> str:
    return d.strftime("%m/%d/%Y")


def _amt(d: Decimal) -> str:
    return f"{q2(d):.2f}"


def _label(acct: Account | None, account_id: str) -> str:
    return acct.label if acct else account_id


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    return s or "account"


# --------------------------------------------------------------------------- QBO
QBO_HEADER = ["Date", "Description", "Amount"]


def _qbo_rows(txns: list[Transaction]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(QBO_HEADER)
    for t in txns:
        w.writerow([_mdy(t.date), _clean(t.description), _amt(t.signed_amount)])
    return buf.getvalue()


def per_account(active: list[Transaction]) -> dict[str, list[Transaction]]:
    """Group already-filtered transactions by account id (insertion order kept)."""
    per: dict[str, list[Transaction]] = defaultdict(list)
    for t in active:
        per[t.account_id].append(t)
    return per


def account_file_names(accts: dict[str, Account], account_ids, ext: str) -> dict[str, str]:
    """{account id: unique, filesystem-safe file name} for one-file-per-account exports."""
    out: dict[str, str] = {}
    used: set[str] = set()
    for aid in sorted(account_ids):
        base = _slug(_label(accts.get(aid), aid))
        name, n = f"{base}{ext}", 2
        while name in used:
            name, n = f"{base}_{n}{ext}", n + 1
        used.add(name)
        out[aid] = name
    return out


def write_per_account(files: dict[str, str | bytes], path: Path, ext: str) -> Path:
    """One file -> ``path`` (with ``ext``); several -> a .zip next to it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(files) <= 1:
        out = path if path.suffix.lower() == ext else path.with_suffix(ext)
        body = next(iter(files.values()), "")
        if isinstance(body, str):
            out.write_text(body, encoding="utf-8", newline="")
        else:
            out.write_bytes(body)
        return out
    out = path.with_suffix(".zip")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return out


def qbo_csv_files(txns, accounts, year: int | None = None) -> dict[str, str]:
    """{file name: CSV text}, one QuickBooks Online bank-upload CSV per account."""
    accts = accounts_by_id(accounts)
    active, _ = split_active(txns, year)
    per = per_account(active)
    if not per:
        return {"transactions.csv": _qbo_rows([])}
    names = account_file_names(accts, per, ".csv")
    return {names[aid]: _qbo_rows(per[aid]) for aid in sorted(per)}


def export_qbo_csv(txns, accounts, path: Path, year: int | None = None) -> Path:
    """Write QuickBooks Online bank-upload file(s). Returns the written path:
    ``path`` (as .csv) for a single account, or ``path`` with a .zip suffix
    containing one CSV per account when there are several."""
    return write_per_account(qbo_csv_files(txns, accounts, year), path, ".csv")


# --------------------------------------------------------------------------- IIF
EXPENSE_PARENT = "LedgerStitch"
INCOME_PARENT = "LedgerStitch Income"
CLEARING_PARENT = "LedgerStitch Clearing"
OWNER_DRAW = "Owner Draw"

_ACCT_TYPE_BY_KIND = {AccountKind.CREDIT_CARD: "CCARD"}


def _is_card(acct: Account | None) -> bool:
    return bool(acct and acct.kind == AccountKind.CREDIT_CARD)


class _IIFBuilder:
    def __init__(self, accounts: dict[str, Account], account_map: dict):
        self.accts = accounts
        self.map = dict(account_map or {})
        self.accnt: dict[str, tuple[str, str]] = {}   # name -> (type, desc)

    def _add_accnt(self, name: str, typ: str, desc: str = "") -> str:
        self.accnt.setdefault(name, (typ, desc))
        return name

    def bank_account(self, account_id: str) -> str:
        a = self.accts.get(account_id)
        mapped = self.map.get(account_id) or (a and self.map.get(a.label))
        name = _clean(mapped) if mapped else _account_name(_label(a, account_id))
        typ = _ACCT_TYPE_BY_KIND.get(a.kind, "BANK") if a else "BANK"
        return self._add_accnt(name, typ, _clean(a.institution if a else ""))

    def split_account(self, t: Transaction) -> str:
        cat = category_of(t)
        mapped = self.map.get(cat)
        if t.lane == Lane.INCOME:
            if mapped:
                return self._add_accnt(_clean(mapped), "INC")
            self._add_accnt(INCOME_PARENT, "INC")
            return self._add_accnt(f"{INCOME_PARENT}:{_account_name(cat)}", "INC")
        if t.lane == Lane.TRANSFER:
            if mapped:
                return self._add_accnt(_clean(mapped), "OCASSET")
            self._add_accnt(CLEARING_PARENT, "OCASSET")
            return self._add_accnt(f"{CLEARING_PARENT}:{_account_name(cat)}", "OCASSET",
                                   "Unmatched transfer leg; reconcile")
        if t.lane == Lane.PERSONAL:
            mapped = self.map.get(cat) or self.map.get("Personal")
            return self._add_accnt(_clean(mapped) if mapped else OWNER_DRAW, "EQUITY")
        # business + review -> expense account
        if mapped:
            return self._add_accnt(_clean(mapped), "EXP")
        self._add_accnt(EXPENSE_PARENT, "EXP")
        return self._add_accnt(f"{EXPENSE_PARENT}:{_account_name(cat)}", "EXP")


def _trns_type(t: Transaction, card: bool) -> str:
    if card:
        return "CREDIT CARD" if t.direction == Direction.OUT else "CCARD REFUND"
    return "CHECK" if t.direction == Direction.OUT else "DEPOSIT"


def _block(typ: str, date: str, accnt: str, other: str, name: str, amount: Decimal, memo: str) -> list[list[str]]:
    """One balanced TRNS/SPL/ENDTRNS block; TRNS amount = -SPL amount."""
    a = q2(amount)
    return [
        ["TRNS", "", typ, date, accnt, name, _amt(a), memo],
        ["SPL", "", typ, date, other, name, _amt(-a), memo],
        ["ENDTRNS"],
    ]


def export_iif(txns, accounts, path: Path, account_map: dict | None = None,
               year: int | None = None) -> Path:
    """QuickBooks Desktop IIF.

    ``account_map`` may map a category name -> QB account name (overrides the
    default ``LedgerStitch:<Category>``), an account id or Account.label -> QB
    bank/card account name, and ``"Personal"`` -> the equity account used for
    personal-lane spending (default ``Owner Draw``).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    accts = accounts_by_id(accounts)
    b = _IIFBuilder(accts, account_map or {})
    active, _ = split_active(txns, year)

    # Pair transfer legs: exactly one out + one in, in different accounts.
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in active:
        if t.transfer_group:
            groups[t.transfer_group].append(t)
    paired: dict[str, tuple[Transaction, Transaction]] = {}
    for g, legs in groups.items():
        outs = [t for t in legs if t.direction == Direction.OUT]
        ins = [t for t in legs if t.direction == Direction.IN]
        if len(outs) == 1 and len(ins) == 1 and outs[0].account_id != ins[0].account_id:
            paired[g] = (outs[0], ins[0])

    body: list[list[str]] = []
    done: set[str] = set()
    for t in active:
        if t.id in done:
            continue
        g = t.transfer_group
        if g in paired:
            out_leg, in_leg = paired[g]
            done.update((out_leg.id, in_leg.id))
            memo = _clean(f"Transfer: {out_leg.description}", 200)
            body += _block("TRANSFER", _mdy(out_leg.date), b.bank_account(out_leg.account_id),
                           b.bank_account(in_leg.account_id), "", -out_leg.amount, memo)
            continue
        done.add(t.id)
        card = _is_card(accts.get(t.account_id))
        name = _clean(t.merchant, 41) if t.lane != Lane.TRANSFER else ""
        memo = _clean(t.description, 200)
        if t.note:
            memo = _clean(f"{memo} | {t.note}", 200)
        body += _block(_trns_type(t, card), _mdy(t.date), b.bank_account(t.account_id),
                       b.split_account(t), name, t.signed_amount, memo)

    lines: list[list[str]] = [["!ACCNT", "NAME", "ACCNTTYPE", "DESC"]]
    # parents must precede their sub-accounts
    for name in sorted(b.accnt, key=lambda n: (n.count(":"), n)):
        typ, desc = b.accnt[name]
        lines.append(["ACCNT", name, typ, desc])
    lines += [
        ["!TRNS", "TRNSID", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT", "MEMO"],
        ["!SPL", "SPLID", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT", "MEMO"],
        ["!ENDTRNS"],
    ]
    lines += body
    text = "\r\n".join("\t".join(_clean(c) for c in row) for row in lines) + "\r\n"
    path.write_bytes(text.encode("cp1252", errors="replace"))
    return path
