# SPDX-License-Identifier: AGPL-3.0-only
"""Free investment "money found" signals from 1099s and a positions snapshot (totals only).

Each signal is a dollar amount with a short explanation and a citation, the same shape as the
payroll signals:

* ``unrealized_losses``: positions below cost that could offset gains (sum and count);
* ``basis_missing``: 1099-B proceeds with no cost basis reported (box B/E, or a blank basis);
* ``wash_sale_disallowed``: wash sale losses the broker already disallowed;
* ``foreign_tax_paid``: foreign tax on 1099-DIV/INT, a credit (often without Form 1116);
* ``section_199a_dividends``: REIT dividends that qualify for the 20% deduction;
* ``exempt_interest``: tax-exempt interest and its private-activity (AMT) part;
* ``nondividend_distributions``: return of capital, which lowers basis;
* ``partnership_k1_likely``: a partnership holding that sends a Schedule K-1 in March;
* ``no_withholding``: investment income with no federal tax withheld;
* ``short_term_gains``: net gains mostly short-term (ordinary rates);
* ``capital_loss_limit``: a net capital loss above the $3,000 yearly limit;
* ``early_distribution``: a 1099-R with distribution code 1.

Details (which lots to sell, wash sales across accounts and a spouse's accounts, dates) are in
LedgerStitch Plus. These are reported figures and published thresholds, not a tax computation.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..payroll.signals import Signal
from .forms1099 import BCategory, Statement1099
from .holdings import Holding, HoldingsSummary, is_partnership

__all__ = ["Signal", "investment_signals", "box_total", "CITATIONS"]

ZERO = Decimal("0")
CENT = Decimal("0.01")
CAPITAL_LOSS_LIMIT = {"mfs": Decimal("1500")}                  # §1211(b); $3,000 otherwise
FOREIGN_TAX_NO_1116 = {"mfj": Decimal("600")}                  # §904(j); $300 otherwise
REIT_DEDUCTION_RATE = Decimal("0.20")                          # §199A(b)(1)(B)
PLUS = "LedgerStitch Plus"

CITATIONS = {
    "losses": "IRC §1211(b), §1212(b); Pub. 550 ch. 4",
    "basis": "Form 8949 instructions (boxes B and E); Pub. 551",
    "wash": "IRC §1091; Pub. 550 (Wash Sales)",
    "foreign": "IRC §901, §904(j); Form 1116 instructions",
    "reit": "IRC §199A(b)(1)(B); Form 8995 instructions",
    "exempt": "IRC §103; Form 1040 line 2a; Form 6251 line 2g",
    "roc": "IRC §301(c)(2); Pub. 550 (Nondividend Distributions)",
    "k1": "Form 1065 Schedule K-1; IRC §7704; IRC §512 (UBTI in IRAs)",
    "estimated": "IRC §6654; Form 1040-ES; Form W-4 line 4(c)",
    "short": "IRC §1222, §1(h); Schedule D instructions",
    "early": "IRC §72(t); Form 5329",
}


def _fmt(v: Decimal) -> str:
    return f"${v:,.2f}"


def box_total(statements: list[Statement1099], form: str, box: str) -> Decimal:
    return sum((s.box(form, box) for s in statements), ZERO)


def investment_signals(statements: list[Statement1099], gains: list[BCategory], holdings: list[Holding],
                       summary: Optional[HoldingsSummary], year: Optional[int] = None,
                       filing_status: Optional[str] = None) -> list[Signal]:
    """Signals for one household. ``gains``: the 1099-B totals per Form 8949 category, counted once."""
    out: list[Signal] = []
    fs = filing_status or "single"
    yr = f"{year} " if year else ""

    # positions below cost
    if summary and summary.loss_positions:
        loss = -summary.unrealized_losses
        out.append(Signal(
            "unrealized_losses", "money", None, loss,
            f"{_fmt(loss)} of unrealized losses in {summary.loss_positions} position(s)",
            f"{summary.loss_positions} position(s) are below cost by {_fmt(loss)} in total. Losses realized by "
            "December 31 offset this year's capital gains, and up to $3,000 of ordinary income ($1,500 married "
            "filing separately); the rest carries forward. Buying the same or a substantially identical security "
            "within 30 days before or after the sale (in any account, including an IRA or a spouse's) "
            f"disallows the loss. {PLUS} picks the lots, checks wash sales across accounts and dates the plan.",
            CITATIONS["losses"]))

    # 1099-B
    no_basis = [g for g in gains if g.cost_basis is None and g.proceeds]
    noncovered = [g for g in gains if g.category in ("B", "E", "X") and g.cost_basis is not None and g.proceeds]
    if no_basis:
        amt = sum((g.proceeds for g in no_basis), ZERO)
        boxes = ", ".join(sorted({g.category for g in no_basis}))
        out.append(Signal(
            "basis_missing", "warning", None, amt,
            f"{_fmt(amt)} of proceeds with no cost basis",
            f"The 1099-B reports {_fmt(amt)} of proceeds in box {boxes} without a cost basis. If no basis is "
            "entered on Form 8949, the whole proceeds count as gain. Find the purchase records (trade "
            "confirmations, the old broker's statements, inherited-basis letters) and enter the basis.",
            CITATIONS["basis"]))
    if noncovered:
        amt = sum((g.proceeds for g in noncovered), ZERO)
        out.append(Signal(
            "noncovered_basis", "info", None, amt,
            f"{_fmt(amt)} of proceeds where the basis was not reported to the IRS",
            "These sales are noncovered (box B, E or undetermined term): the broker's basis is a courtesy "
            "figure. Check it against your records before filing; you report it on Form 8949.",
            CITATIONS["basis"]))
    wash = sum((g.wash_sale_disallowed for g in gains), ZERO)
    if wash:
        out.append(Signal(
            "wash_sale_disallowed", "info", None, wash,
            f"{_fmt(wash)} of wash sale losses disallowed",
            f"The broker disallowed {_fmt(wash)} of losses (Form 8949 column g, code W). The loss is not "
            "gone: it is added to the replacement shares' basis and counts when they are sold. Brokers report "
            "wash sales only within one account and identical CUSIPs; repurchases in another account, an IRA "
            f"or a spouse's account are yours to find. {PLUS} checks across accounts.",
            CITATIONS["wash"]))

    # 1099-DIV / INT
    foreign = box_total(statements, "1099-DIV", "7") + box_total(statements, "1099-INT", "6")
    if foreign:
        limit = FOREIGN_TAX_NO_1116.get(fs, Decimal("300"))
        simple = foreign <= limit
        out.append(Signal(
            "foreign_tax_paid", "money", None, foreign,
            f"{_fmt(foreign)} of foreign tax paid",
            f"Funds paid {_fmt(foreign)} of foreign tax on your behalf. It is usually worth more as a credit "
            "(dollar for dollar against tax) than as an itemized deduction. "
            + (f"At or below {_fmt(limit)} ({'married filing jointly' if fs == 'mfj' else 'other filers'}), "
               "with only passive income on 1099s, it can be claimed without Form 1116."
               if simple else f"Above {_fmt(limit)}, Form 1116 is required."),
            CITATIONS["foreign"]))
    reit = box_total(statements, "1099-DIV", "5")
    if reit:
        ded = (reit * REIT_DEDUCTION_RATE).quantize(CENT)
        out.append(Signal(
            "section_199a_dividends", "money", None, ded,
            f"{_fmt(ded)} qualified business income deduction on REIT dividends",
            f"{_fmt(reit)} of Section 199A (REIT) dividends in 1099-DIV box 5 qualify for a deduction of 20% "
            f"({_fmt(ded)}) on Form 8995, if the shares were held more than 45 days around the ex-dividend "
            "date. Tax software takes it only if box 5 is entered.",
            CITATIONS["reit"]))
    exempt = box_total(statements, "1099-DIV", "12") + box_total(statements, "1099-INT", "8")
    if exempt:
        pab = box_total(statements, "1099-DIV", "13") + box_total(statements, "1099-INT", "9")
        out.append(Signal(
            "exempt_interest", "info", None, exempt,
            f"{_fmt(exempt)} of tax-exempt interest",
            f"{_fmt(exempt)} is exempt from federal income tax but still reported (Form 1040 line 2a). It counts "
            "toward the taxable part of Social Security benefits and the premium tax credit income, and most "
            "states tax out-of-state municipal interest."
            + (f" {_fmt(pab)} is private activity bond interest, an AMT preference item." if pab else ""),
            CITATIONS["exempt"]))
    roc = box_total(statements, "1099-DIV", "3")
    if roc:
        out.append(Signal(
            "nondividend_distributions", "info", None, roc,
            f"{_fmt(roc)} of nondividend distributions (return of capital)",
            "These are not taxable now; they lower the shares' cost basis, so a later sale shows a larger gain. "
            "Once the basis reaches zero, further distributions are capital gain.",
            CITATIONS["roc"]))

    # partnerships
    ptp = sorted({h.symbol for h in holdings if is_partnership(h.description)} |
                 {lt.symbol or lt.description[:20] for s in statements for lt in s.lots
                  if is_partnership(lt.description)})
    if ptp:
        out.append(Signal(
            "partnership_k1_likely", "info", None, None,
            f"Schedule K-1 expected for {', '.join(ptp[:5])}{' and more' if len(ptp) > 5 else ''}",
            "Publicly traded partnerships send a Schedule K-1, usually in March, not a 1099; the 1099 may show "
            "only distributions. Wait for the K-1 before filing. A sale also needs the K-1's ordinary income "
            "recapture, and partnerships held in an IRA can create unrelated business income (UBTI).",
            CITATIONS["k1"]))

    # withholding
    st_net = sum((g.gain or ZERO for g in gains if g.term == "short"), ZERO)
    lt_net = sum((g.gain or ZERO for g in gains if g.term == "long"), ZERO)
    income = (box_total(statements, "1099-DIV", "1a") + box_total(statements, "1099-DIV", "2a")
              + box_total(statements, "1099-INT", "1") + box_total(statements, "1099-MISC", "2")
              + box_total(statements, "1099-MISC", "3") + box_total(statements, "1099-NEC", "1")
              + box_total(statements, "1099-R", "2a") + max(ZERO, st_net + lt_net))
    withheld = sum((box_total(statements, f, "4") for f in ("1099-DIV", "1099-INT", "1099-MISC", "1099-NEC",
                                                            "1099-R")), ZERO)
    if income >= Decimal("1000") and not withheld:
        out.append(Signal(
            "no_withholding", "info", None, income,
            f"{_fmt(income)} of {yr}investment and 1099 income with no tax withheld",
            "No federal tax was withheld on this income. If wage withholding doesn't cover it, an underpayment "
            "penalty applies unless withholding and estimated payments reach 90% of this year's tax or 100% of "
            "last year's (110% above $150,000 AGI); extra W-4 withholding counts as paid evenly through the year.",
            CITATIONS["estimated"]))

    # term mix
    if st_net > 0 and st_net > max(ZERO, lt_net):
        out.append(Signal(
            "short_term_gains", "info", None, st_net,
            f"{_fmt(st_net)} of net short-term gains",
            f"Most of the net gain is short-term ({_fmt(st_net)} vs {_fmt(lt_net)} long-term). Short-term gains "
            "are taxed at ordinary rates; positions held more than one year qualify for the 0/15/20% rates. "
            "Losses realized now offset short-term gains first.",
            CITATIONS["short"]))
    net = st_net + lt_net + box_total(statements, "1099-DIV", "2a")
    limit = CAPITAL_LOSS_LIMIT.get(fs, Decimal("3000"))
    if net < -limit:
        out.append(Signal(
            "capital_loss_limit", "info", None, -net - limit,
            f"{_fmt(-net - limit)} of capital loss to carry forward",
            f"The reported net capital loss is {_fmt(-net)}. Only {_fmt(limit)} offsets other income this year; "
            "the rest carries forward with its short/long character. Realizing gains this year uses the "
            "loss without tax on them.",
            CITATIONS["losses"]))

    # 1099-R
    early = [s for s in statements if "1" in (s.text_boxes.get("1099-R", {}).get("7", "").replace(" ", ""))]
    if early:
        amt = sum((s.box("1099-R", "2a") or s.box("1099-R", "1") for s in early), ZERO)
        out.append(Signal(
            "early_distribution", "warning", None, amt,
            f"{_fmt(amt)} early retirement distribution (code 1)",
            "Distribution code 1 means an early distribution with no known exception: a 10% additional tax "
            "applies to the taxable amount unless an exception fits (Form 5329), such as separation from "
            "service at 55+, disability, or a qualified birth or adoption distribution (up to $5,000).",
            CITATIONS["early"]))
    return out
