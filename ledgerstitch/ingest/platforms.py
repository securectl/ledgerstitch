# SPDX-License-Identifier: AGPL-3.0-only
"""Payment-platform and fintech ledgers (gross / fee / net / payout exports).

PayPal, Stripe, Shopify Payments, Square, Venmo, Coinbase and Revolut exports
are not plain bank statements. A naive CSV import double-counts income (platform
charge *and* the bank deposit of the payout) and hides processing fees. This
parser applies each platform's semantics (see docs/FORMATS.md):

* **Gross + fee rows.** A sale is booked at its *gross* amount and the fee
  becomes a separate money-out row, category "Payment Processing Fees"
  (Schedule C line 10). gross + fee == net, so the account still moves by
  exactly the platform's Net and the running balance is preserved.
* **Payouts / withdrawals to the bank are transfers** (lane TRANSFER,
  "Transfer Between Accounts"). reconcile pairs them with the matching bank
  deposit, so the income is counted once (from the platform side).
* **Shadow rows are skipped**: PayPal ``Balance Impact = Memo`` (authorizations,
  funding legs, currency conversions), non-completed statuses, Venmo balance /
  disclaimer rows.
* **Description**: the counterparty and note, never the funding source.
* **Unsigned amounts** (Coinbase) take their direction from the catalog's
  ``type_values`` map.

Every derived row has a deterministic ``seq`` (3*row main, 3*row+1 fee,
3*row+2 funding leg) so re-imports never duplicate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable, Optional

from ..models import Direction, Lane, Source, Status, Transaction
from .detect import parse_date, parse_money, read_rows
from .normalize import normalize_merchant

__all__ = ["PLATFORMS", "is_platform", "parse_platform"]

FEE_CATEGORY = "Payment Processing Fees"
FEE_SCHEDULE_C = "L10"
TRANSFER_CATEGORY = "Transfer Between Accounts"


@dataclass
class Platform:
    name: str
    date: tuple[str, ...]
    description: Callable[[dict, Decimal], str]
    gross: Optional[str] = None            # gross column (sale before fees)
    fee: Optional[str] = None              # fee column
    net: Optional[str] = None              # net column (gross + fee)
    amount: Optional[str] = None           # single amount column (no gross/fee split)
    fee_separate: bool = False             # fee is charged on top of `amount` (Revolut)
    type_col: Optional[str] = None
    status_col: Optional[str] = None
    keep_status: Optional[re.Pattern] = None     # rows whose status does NOT match are skipped
    skip_row: Optional[Callable[[dict], bool]] = None
    transfer_row: Optional[Callable[[dict], bool]] = None
    funding_leg: Optional[Callable[[dict, Decimal], Optional[str]]] = None
    type_values: dict[str, str] = field(default_factory=dict)    # value → in/out/transfer/either/ignore
    id_col: Optional[str] = None


def _g(row: dict, *names: str) -> str:
    for n in names:
        v = row.get(n.lower())
        if v:
            return v.strip()
    return ""


def _join(*parts: str) -> str:
    return " ".join(" - ".join(p for p in parts if p).split())


_COMPLETED = re.compile(r"^(?:completed?|complete|cleared|paid|posted|issued|success(?:ful)?|settled|)$", re.I)


PLATFORMS: dict[str, Platform] = {
    "paypal_activity": Platform(
        name="PayPal", date=("date",),
        description=lambda r, a: _join(_g(r, "name", "to email address", "from email address") or _g(r, "type"),
                                       _g(r, "item title", "subject", "note")),
        gross="gross", fee="fee", net="net", type_col="type", status_col="status", keep_status=_COMPLETED,
        skip_row=lambda r: _g(r, "balance impact").lower() == "memo",
        transfer_row=lambda r: bool(re.search(r"withdraw|transfer to bank|bank deposit to pp|transfer from bank|"
                                              r"general card deposit|add funds", _g(r, "type"), re.I)),
        id_col="transaction id"),
    "paypal_activity_personal": Platform(
        name="PayPal", date=("date",), description=lambda r, a: _g(r, "name") or _g(r, "type"),
        amount="amount", type_col="type", status_col="status", keep_status=_COMPLETED,
        transfer_row=lambda r: bool(re.search(r"withdraw|transfer to bank|bank deposit|transfer from bank",
                                              _g(r, "type"), re.I)),
        id_col="receipt id"),
    "stripe_balance_itemized": Platform(
        name="Stripe", date=("created_utc", "created", "created (utc)"),
        description=lambda r, a: _g(r, "description") or _g(r, "reporting_category"),
        gross="gross", fee="fee", net="net", type_col="reporting_category",
        transfer_row=lambda r: _g(r, "reporting_category", "type").lower() in ("payout", "payout_reversal", "transfer"),
        id_col="balance_transaction_id"),
    "shopify_payments_transactions": Platform(
        name="Shopify Payments", date=("transaction date",),
        description=lambda r, a: _join("Shopify " + (_g(r, "type") or "transaction"), _g(r, "order")),
        gross="amount", fee="fee", net="net", type_col="type",
        transfer_row=lambda r: _g(r, "type").lower() in ("payout", "transfer"),
        id_col="order"),
    "square_transactions": Platform(
        name="Square", date=("date",),
        description=lambda r, a: _join(_g(r, "customer name") or "Square sale", _g(r, "description", "details")),
        gross="total collected", fee="fees", net="net total", status_col="transaction status",
        keep_status=_COMPLETED, id_col="transaction id"),
    "venmo_statement": Platform(
        name="Venmo", date=("datetime", "date"),
        description=lambda r, a: (
            _join("Transfer to " + (_g(r, "destination") or "bank"))
            if re.search(r"transfer", _g(r, "type"), re.I) else
            _join(_g(r, "to") if a < 0 else _g(r, "from"), _g(r, "note")) or _g(r, "type")),
        amount="amount (total)", type_col="type", status_col="status",
        keep_status=re.compile(r"^(?:complete[d]?|issued|)$", re.I),
        skip_row=lambda r: not _g(r, "id"),
        transfer_row=lambda r: bool(re.search(r"standard transfer|instant transfer|bank transfer|add funds",
                                              _g(r, "type"), re.I)),
        # a payment funded by a bank/card (not the Venmo balance) brings that money in first
        funding_leg=lambda r, a: (_g(r, "funding source")
                                  if a < 0 and _g(r, "funding source") and
                                  not re.search(r"venmo balance", _g(r, "funding source"), re.I)
                                  and not re.search(r"transfer", _g(r, "type"), re.I) else None),
        id_col="id"),
    "coinbase_transactions": Platform(
        name="Coinbase", date=("timestamp",),
        description=lambda r, a: _g(r, "notes") or _join(_g(r, "transaction type"), _g(r, "asset")),
        amount="total (inclusive of fees and/or spread)", type_col="transaction type",
        type_values={"buy": "out", "sell": "in", "send": "out", "receive": "in", "convert": "ignore",
                     "rewards income": "in", "card spend": "out", "deposit": "transfer", "withdrawal": "transfer",
                     "staking income": "in", "learning reward": "in", "advanced trade buy": "out",
                     "advanced trade sell": "in"},
        id_col="id"),
    "revolut": Platform(
        name="Revolut", date=("started date", "completed date"),
        description=lambda r, a: _g(r, "description") or _g(r, "type"),
        amount="amount", fee="fee", fee_separate=True, type_col="type", status_col="state",
        keep_status=re.compile(r"^(?:completed?|)$", re.I),
        transfer_row=lambda r: _g(r, "type").upper() in ("TOPUP", "TRANSFER", "EXCHANGE"),
        id_col=None),
}


def is_platform(catalog_id: Optional[str]) -> bool:
    return bool(catalog_id) and catalog_id in PLATFORMS


def _date(raw: str, fmt: Optional[str]) -> Optional[date]:
    s = (raw or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ]", s)
    if m:
        s = m.group(1)
    return (parse_date(s, fmt) if fmt else None) or parse_date(s)


def parse_platform(data: bytes, profile, account_id: str, import_id: str
                   ) -> tuple[list[Transaction], list[str], dict]:
    cid = getattr(profile, "catalog_id", None)
    plat = PLATFORMS[cid]
    rows = read_rows(data)
    hidx = getattr(profile, "header_row", None)
    if hidx is None or hidx < 0 or hidx >= len(rows):
        hidx = 0
    header = [h.strip().lower() for h in rows[hidx]]
    mapping = getattr(profile, "mapping", None)
    fmt = getattr(mapping, "date_format", None) if mapping else None
    date_col = next((c for c in plat.date if c in header), None)
    if date_col is None and mapping is not None and mapping.date:
        date_col = mapping.date.strip().lower()
    warnings: list[str] = []
    txns: list[Transaction] = []
    skipped = {"memo": 0, "status": 0, "other": 0, "zero": 0, "ignored": 0}
    for n, r in enumerate(rows[hidx + 1:]):
        if not r or not any(r):
            continue
        row = {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
        if plat.skip_row and plat.skip_row(row):
            skipped["memo" if cid.startswith("paypal") else "other"] += 1
            continue
        d = _date(row.get(date_col, ""), fmt)
        if d is None:
            skipped["other"] += 1
            continue
        if plat.status_col and plat.keep_status is not None and \
                not plat.keep_status.match(_g(row, plat.status_col)):
            skipped["status"] += 1
            continue
        gross = parse_money(row.get(plat.gross, "")) if plat.gross else None
        fee = parse_money(row.get(plat.fee, "")) if plat.fee else None
        net = parse_money(row.get(plat.net, "")) if plat.net else None
        amount = parse_money(row.get(plat.amount, "")) if plat.amount else None
        type_val = _g(row, plat.type_col) if plat.type_col else ""
        is_transfer = bool(plat.transfer_row and plat.transfer_row(row))
        # ----- direction for unsigned amounts (type map)
        if plat.type_values and amount is not None:
            tv = plat.type_values.get(type_val.lower())
            if tv == "ignore" or tv == "either" or tv is None and amount >= 0 and type_val:
                if tv is None:
                    warnings.append(f"unknown {plat.name} type value; row skipped")
                skipped["ignored"] += 1
                continue
            if tv == "transfer":
                is_transfer = True
            if tv == "out":
                amount = -abs(amount)
            elif tv == "in":
                amount = abs(amount)
        # ----- main amount + fee
        fee_amt: Optional[Decimal] = None
        if plat.gross and gross is not None:
            main = gross
            if net is not None and net != gross:
                fee_amt = net - gross
            elif fee:
                fee_amt = -abs(fee)
        elif plat.net and net is not None:
            main = net
        elif amount is not None:
            main = amount
            if plat.fee_separate and fee:
                fee_amt = -abs(fee)
        else:
            skipped["other"] += 1
            continue
        desc = " ".join((plat.description(row, main) or plat.name).split())
        if main == 0 and not fee_amt:
            skipped["zero"] += 1
            continue
        ref = _g(row, plat.id_col) if plat.id_col else ""
        if plat.funding_leg and main != 0:
            src = plat.funding_leg(row, main)
            if src:
                t = _txn(account_id, import_id, d, f"Funding from {src} for {desc}", abs(main), Direction.IN,
                         type_val, 3 * n + 2, transfer=True)
                txns.append(t)
        if main != 0:
            txns.append(_txn(account_id, import_id, d, desc if not is_transfer or "transfer" in desc.lower()
                             else f"Transfer to bank - {desc}" if main < 0 else f"Transfer from bank - {desc}",
                             abs(main), Direction.OUT if main < 0 else Direction.IN, type_val, 3 * n,
                             transfer=is_transfer))
        if fee_amt:
            fdesc = f"{plat.name.upper()} FEE - {desc}" + (f" {ref}" if ref and cid.startswith("shopify") else "")
            txns.append(_txn(account_id, import_id, d, fdesc, abs(fee_amt),
                             Direction.OUT if fee_amt < 0 else Direction.IN, "fee", 3 * n + 1, fee=True))
    if skipped["memo"]:
        warnings.append(f"skipped {skipped['memo']} memo row(s) (no balance impact)")
    if skipped["status"]:
        warnings.append(f"skipped {skipped['status']} pending/failed/cancelled row(s)")
    if skipped["other"]:
        warnings.append(f"skipped {skipped['other']} non-transaction row(s) (balances, totals, notes)")
    if skipped["zero"]:
        warnings.append(f"skipped {skipped['zero']} zero-amount row(s)")
    if not txns:
        warnings.append("no transactions found")
    fees = sum((t.amount for t in txns if t.category == FEE_CATEGORY), Decimal(0))
    info = {"format": "CSV", "platform": plat.name, "catalog_id": cid, "reconciled": None,
            "fees_total": str(fees), "transfers": sum(1 for t in txns if t.lane == Lane.TRANSFER)}
    return txns, list(dict.fromkeys(warnings)), info


def _txn(account_id, import_id, d, desc, amount, direction, type_val, seq, *, transfer=False, fee=False):
    t = Transaction(account_id=account_id, date=d, description=desc, amount=amount, direction=direction,
                    import_id=import_id, bank_category=type_val or None, merchant=normalize_merchant(desc), seq=seq)
    if transfer:
        t.category, t.lane, t.schedule_c = TRANSFER_CATEGORY, Lane.TRANSFER, None
        t.source, t.status, t.confidence = Source.HEURISTIC, Status.AUTO, 0.9
    elif fee:
        t.category, t.lane, t.schedule_c = FEE_CATEGORY, Lane.BUSINESS, FEE_SCHEDULE_C
        t.source, t.status, t.confidence = Source.HEURISTIC, Status.AUTO, 0.9
    return t


def adopt_platform(filename: str, data: bytes, profile) -> None:
    """A manual/confirmed mapping on a known platform export keeps platform semantics
    when the user kept the catalog's date and amount columns (e.g. "Confirm columns"),
    so confirming never re-imports the rows under different ids."""
    if profile is None or getattr(profile, "catalog_id", None) or getattr(profile, "mapping", None) is None:
        return
    try:
        from .learning import scan_catalog
        hit = scan_catalog(read_rows(data), filename)
    except Exception:
        return
    if not hit or not is_platform(hit[1].get("id")):
        return
    i, e, score = hit
    if score < 1.0:
        return
    plat = PLATFORMS[e["id"]]
    m = profile.mapping
    amount_cols = {c for c in (plat.gross, plat.net, plat.amount) if c}
    chosen = {(c or "").strip().lower() for c in (m.amount, m.debit, m.credit) if c}
    if chosen and chosen <= amount_cols and (m.date or "").strip().lower() in plat.date:
        profile.catalog_id = e["id"]
        profile.header_row = i
