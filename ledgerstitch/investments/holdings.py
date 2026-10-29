# SPDX-License-Identifier: AGPL-3.0-only
"""Positions-export CSV import and a read-only portfolio snapshot.

Reads the "Positions" CSV that Fidelity, Schwab, Vanguard and Robinhood-style exports produce.
Header names differ ("Current Value" / "Market Value" / "Total Value" / "Equity"), so columns are
matched by name (exact first, then prefix) and the header row is found wherever it sits: Schwab
puts an "Positions for account ..." line above it and can repeat a header per account.

The snapshot is arithmetic on what the file says: totals, unrealized gain/loss, allocation by
account and asset class, top holdings and concentration. Account numbers are masked. Targets,
drift, rebalancing and lot selection are LedgerStitch Plus features and are not here.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from ..payroll.paystub import _date
from .forms1099 import _header_map, mask_tax_ids, money

__all__ = ["Holding", "Allocation", "HoldingsSummary", "parse_positions_csv", "is_positions_header",
           "summarize_holdings", "asset_class", "ASSET_CLASSES", "is_partnership"]

ZERO = Decimal("0")
CENT = Decimal("0.01")
ASSET_CLASSES = ("equity", "etf", "fund", "bond", "cash", "option", "crypto", "other")
CONCENTRATION_PCT = Decimal("10")         # one position above this share of the portfolio is flagged

_POS_COLS: dict[str, tuple[str, ...]] = {
    "account": ("account number", "account name", "account", "account name/number", "acct"),
    "symbol": ("symbol", "ticker", "instrument", "symbol/cusip"),
    "description": ("description", "name", "investment name", "security description", "security name",
                    "security"),
    "quantity": ("quantity", "shares", "qty", "qty (quantity)", "quantity (shares)"),
    "price": ("last price", "price", "share price", "current price", "last", "price ($)", "market price"),
    "value": ("current value", "market value", "total value", "value", "equity", "mkt val (market value)",
              "mkt val", "market value ($)", "current balance"),
    "cost": ("cost basis total", "cost basis", "total cost", "total cost basis", "cost", "cost basis ($)"),
    "avg_cost": ("average cost basis", "average cost", "avg cost", "cost/share", "average price",
                 "avg price", "cost per share"),
    "gain": ("total gain/loss dollar", "gain/loss $", "gain/loss ($)", "unrealized gain/loss", "total return",
             "gain/loss", "gain $ (gain/loss $)", "unrealized gain", "total gain/loss"),
    "type": ("security type", "asset class", "asset type", "investment type", "type"),
}
_MONEY_MARKET = {"SPAXX", "FDRXX", "FZFXX", "SPRXX", "FCASH", "CORE", "SWVXX", "SNVXX", "SNOXX", "VMFXX",
                 "VMRXX", "VUSXX", "VMSXX", "SNSXX"}
_CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "LTC", "BCH", "AVAX", "DOT", "LINK", "MATIC", "SHIB", "USDC"}
_MLP = re.compile(r"\b(?:l\.?p\.?|mlp|limited\s+partnership|partners\s+lp|common\s+units?|units\s+rep(?:resenting)?|"
                  r"partnership)\b", re.I)
_SKIP_ROW = re.compile(r"(?i)^\s*(?:account\s+total|total|grand\s+total|pending\s+activity|cash\s+&\s+cash\s+"
                       r"investments\s+total|positions\s+total)\b")


@dataclass
class Holding:
    symbol: str
    description: str = ""
    account: Optional[str] = None             # masked
    quantity: Optional[Decimal] = None
    price: Optional[Decimal] = None
    market_value: Decimal = ZERO
    cost_basis: Optional[Decimal] = None
    unrealized_gain: Optional[Decimal] = None
    asset_class: str = "equity"
    source: str = ""


@dataclass
class Allocation:
    name: str
    value: Decimal
    pct: Decimal


@dataclass
class HoldingsSummary:
    as_of: Optional[date] = None
    positions: int = 0
    market_value: Decimal = ZERO
    cost_basis: Decimal = ZERO                # positions with a known basis only
    unrealized_gain: Decimal = ZERO
    unrealized_gains: Decimal = ZERO          # sum of positions up
    unrealized_losses: Decimal = ZERO         # sum of positions down (negative)
    loss_positions: int = 0
    basis_unknown: int = 0
    by_class: list[Allocation] = field(default_factory=list)
    by_account: list[Allocation] = field(default_factory=list)
    top: list[Allocation] = field(default_factory=list)
    top5_pct: Decimal = ZERO
    concentrated: list[Allocation] = field(default_factory=list)


def is_partnership(description: str) -> bool:
    """A publicly traded partnership (MLP): it sends a Schedule K-1, not a 1099."""
    return bool(_MLP.search(description or ""))


def asset_class(symbol: str, description: str = "", type_: str = "") -> str:
    """Best guess at the asset class from the symbol, description and the export's type column."""
    sym, d, t = (symbol or "").upper().strip(), (description or "").lower(), (type_ or "").lower()
    if re.search(r"option", t) or re.search(r"\b(?:call|put)\b", d) or \
            re.fullmatch(r"[A-Z]{1,6}\d{6}[CP]\d{8}", sym.replace(" ", "")) or \
            re.match(r"^-?[A-Z]{1,6}\s+\d{2}/\d{2}/\d{4}\s+[\d.]+\s+[CP]$", sym):
        return "option"
    if sym.rstrip("*") in _MONEY_MARKET or sym.endswith("**") or re.search(r"money\s+market|sweep|cash\s+&|"
                                                                          r"^cash\b|fdic|bank\s+deposit", d) \
            or re.search(r"money\s+market|cash\s+and\s+money|cash\s+equivalent", t):
        return "cash"
    if re.search(r"crypto", t) or (sym in _CRYPTO and not re.search(r"\b(?:etf|trust|fund)\b", d)) or \
            re.search(r"\b(?:bitcoin|ethereum|dogecoin|solana)\b(?!.*\b(?:etf|trust|fund)\b)", d):
        return "crypto"
    if re.search(r"fixed\s+income|bond|treasur", t) or re.search(r"\b(?:bond|treasury|t-bill|note|cd|municipal|"
                                                                r"debenture)\b|\d+\.\d+%\s", d) or \
            re.fullmatch(r"\d{3}[A-Z0-9]{5}\d", sym):
        return "bond"
    if re.search(r"\betf\b|exchange[- ]traded", t) or re.search(r"\betf\b|ishares|spdr|exchange[- ]traded", d):
        return "etf"
    if re.search(r"mutual\s+fund|\bfund\b", t) or re.search(r"\bfund\b|admiral|investor\s+shares|"
                                                            r"institutional\s+shares|\bportfolio\b", d) or \
            re.fullmatch(r"[A-Z]{4}X", sym):
        return "fund"
    if re.search(r"equit|stock|common|adr|reit", t) or sym:
        return "equity"
    return "other"


def is_positions_header(row: list[str]) -> bool:
    m = _header_map(row, _POS_COLS)
    return ("symbol" in m or "description" in m) and ("value" in m or "quantity" in m) and \
        not re.search(r"proceeds", ",".join(row), re.I)


def parse_positions_csv(filename: str, data: bytes) -> tuple[list[Holding], Optional[date], list[str]]:
    """``(holdings, as_of, warnings)`` from a positions CSV."""
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    source = mask_tax_ids(filename)
    warnings: list[str] = []
    out: list[Holding] = []
    as_of: Optional[date] = None
    cols: Optional[dict[str, int]] = None
    account: Optional[str] = None
    for r in rows:
        cells = [c.strip() for c in r]
        nonempty = [c for c in cells if c]
        if not nonempty:
            continue
        if is_positions_header(cells):
            cols = _header_map(cells, _POS_COLS)
            continue
        joined = " ".join(nonempty)
        if as_of is None and (m := re.search(r"as\s+of\s+(?:[\d:]+\s*[AP]M\s*\w*,?\s*)?(\d{1,2}/\d{1,2}/\d{4}|"
                                             r"\d{4}-\d{2}-\d{2})", joined, re.I)):
            as_of = _date(m.group(1))
        if cols is None or len(nonempty) == 1:
            # a preamble / account line: "Positions for account Individual ...898 as of ..."
            if m := re.search(r"(?:account|acct)\s+(.*?)(?:\s+as\s+of\b|$)", joined, re.I):
                account = mask_tax_ids(m.group(1).strip())[:40] or account
            elif cols is not None and len(nonempty) == 1 and not re.search(r"(?i)disclos|brokerage services|"
                                                                            r"date downloaded|data and information",
                                                                            joined) and len(joined) < 50:
                account = mask_tax_ids(joined)
            continue

        def cell(key: str) -> str:
            i = cols.get(key)
            return cells[i] if i is not None and i < len(cells) else ""

        sym, desc = cell("symbol"), cell("description")
        if not sym and not desc:
            continue
        if _SKIP_ROW.match(sym or desc) or _SKIP_ROW.match(desc):
            continue
        qty, price, value = money(cell("quantity")), money(cell("price")), money(cell("value"))
        if value is None and qty is not None and price is not None:
            value = (qty * price).quantize(CENT)
        if value is None:
            if re.search(r"(?i)cash|money market|sweep", sym + " " + desc) and cell("quantity"):
                value = money(cell("quantity"))
            if value is None:
                continue
        cost = money(cell("cost"))
        if cost is None and (avg := money(cell("avg_cost"))) is not None and qty is not None:
            cost = (avg * qty).quantize(CENT)
        gain = money(cell("gain"))
        if gain is None and cost is not None:
            gain = value - cost
        elif gain is not None and cost is None:
            cost = value - gain
        cls = asset_class(sym, desc, cell("type"))
        if cls == "cash" and cost is None:
            cost, gain = value, ZERO
        acct = mask_tax_ids(cell("account")) if cell("account") else account
        out.append(Holding(symbol=sym.rstrip("*") or desc[:12], description=mask_tax_ids(desc), account=acct,
                           quantity=qty, price=price, market_value=value, cost_basis=cost, unrealized_gain=gain,
                           asset_class=cls, source=source))
    if cols is None:
        warnings.append(f"{source}: no positions header found (need symbol or description, and value or quantity)")
    elif not out:
        warnings.append(f"{source}: no positions found under the header")
    return out, as_of, warnings


def _pct(v: Decimal, total: Decimal) -> Decimal:
    return (v / total * 100).quantize(Decimal("0.1")) if total else ZERO


def summarize_holdings(holdings: list[Holding], as_of: Optional[date] = None, top_n: int = 10) -> HoldingsSummary:
    s = HoldingsSummary(as_of=as_of, positions=len(holdings))
    s.market_value = sum((h.market_value for h in holdings), ZERO)
    for h in holdings:
        if h.unrealized_gain is None or h.cost_basis is None:
            s.basis_unknown += 1
            continue
        s.cost_basis += h.cost_basis
        s.unrealized_gain += h.unrealized_gain
        if h.unrealized_gain >= 0:
            s.unrealized_gains += h.unrealized_gain
        else:
            s.unrealized_losses += h.unrealized_gain
            s.loss_positions += 1
    total = s.market_value

    def group(key) -> list[Allocation]:
        acc: dict[str, Decimal] = {}
        for h in holdings:
            acc[key(h)] = acc.get(key(h), ZERO) + h.market_value
        return [Allocation(k, v, _pct(v, total)) for k, v in sorted(acc.items(), key=lambda kv: -kv[1])]

    s.by_class = group(lambda h: h.asset_class)
    s.by_account = group(lambda h: h.account or "(account not given)")
    by_symbol = [a for a in group(lambda h: h.symbol.upper()) if a.name not in _MONEY_MARKET]
    s.top = by_symbol[:top_n]
    s.top5_pct = sum((a.pct for a in by_symbol[:5]), ZERO)
    cash_like = {h.symbol.upper() for h in holdings if h.asset_class in ("cash", "fund", "etf")}
    s.concentrated = [a for a in by_symbol if a.pct > CONCENTRATION_PCT and a.name not in cash_like]
    return s
