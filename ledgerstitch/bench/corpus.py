# SPDX-License-Identifier: AGPL-3.0-only
"""Deterministic, labeled SYNTHETIC statement corpus for the accuracy benchmark.

``build_corpus(out_dir, seed)`` writes ~35 statement files for one fictional
sole proprietor (a design studio with mixed business/personal spending across
25 accounts) plus ``truth.json``. Every name, merchant descriptor, account
number and amount is fabricated. The same seed always produces byte-identical
files.

Model: a list of **events** (real-world posted transactions, one per account)
is generated first; each **statement file** then renders the events of its
account and period in that bank's layout. An event printed by two files
(overlapping exports, a PDF and a CSV of the same month) is a *duplicate*.
Card payments and own-account transfers are event *pairs*; a refund points at
its purchase event.

Ground truth per row: date, amount, direction, description (as printed),
expected category (a bookkeeper's label from the taxonomy), its default lane,
the Schedule C line when the category maps to one, and hard-case tags
(``refund``, ``card_payment``, ``transfer``, ``fx``, ``ref_noise``,
``multiline``, ``same_day_repeat``). Per statement: opening/closing balance.

Files also contain rows that must NOT be imported (pending, declined, failed,
zero-amount, summary/total lines); they are not truth rows and are counted
per file as ``noise_rows``.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable, Optional

from ..models import Lane
from ..taxonomy import CATEGORY_BY_NAME
from .pdfgen import PDF

__all__ = ["build_corpus", "CORPUS_VERSION", "truth_schedule_c"]

CORPUS_VERSION = 1
D0 = Decimal("0.00")
CENT = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def fm(d: Decimal) -> str:
    """1,234.56"""
    return f"{abs(d):,.2f}"


def f2(d: Decimal) -> str:
    return f"{d:.2f}"


def truth_schedule_c(category: Optional[str]) -> Optional[str]:
    """The Schedule C line for a truth category, when it is a (possibly) business one."""
    c = CATEGORY_BY_NAME.get(category or "")
    if c is None or c.lane not in (Lane.BUSINESS, Lane.REVIEW):
        return None
    return c.schedule_c


def truth_lane(category: Optional[str]) -> str:
    c = CATEGORY_BY_NAME.get(category or "")
    return c.lane.value if c else Lane.REVIEW.value


# --------------------------------------------------------------------------- merchants
# (descriptor, category | (category on a business account, on a personal account), low, high, weight, tags)
# {n4}/{n6}/... = random digits, {r6}/... = random upper-case alphanumerics.
BIZ_MERCHANTS = [
    ("ADOBE *CREATIVE CLD 408-536-6000 CA", "Software & Subscriptions", 54.99, 54.99, 0, ()),
    ("GOOGLE *GSUITE_SAMPLEDES CC@GOOGLE.COM CA", "Software & Subscriptions", 14.40, 14.40, 0, ()),
    ("DROPBOX*{r8} DROPBOX.COM CA", "Software & Subscriptions", 19.99, 19.99, 0, ("ref_noise",)),
    ("ZOOM.US 888-799-9666 CA", "Software & Subscriptions", 15.99, 15.99, 0, ()),
    ("FIGMA MONTHLY RENEWAL FIGMA.COM CA", "Software & Subscriptions", 45.00, 45.00, 0, ()),
    ("NOTION LABS INC NOTION.SO CA", "Software & Subscriptions", 10.00, 10.00, 1, ()),
    ("INTUIT *QBOOKS ONLINE CL.INTUIT.COM CA", "Software & Subscriptions", 35.00, 35.00, 1, ()),
    ("GITHUB, INC. GITHUB.COM CA", "Software & Subscriptions", 4.00, 21.00, 1, ()),
    ("OPENAI *CHATGPT SUBSCR OPENAI.COM CA", "Software & Subscriptions", 20.00, 20.00, 1, ()),
    ("SQSP* INV{n6} SQUARESPACE.C NY", "Software & Subscriptions", 23.00, 33.00, 1, ("ref_noise",)),
    ("1PASSWORD 1PASSWORD.COM ON", "Software & Subscriptions", 7.99, 7.99, 1, ()),
    ("CANVA* {r8} SURRY HILLS AUS", "Software & Subscriptions", 14.99, 14.99, 1, ("ref_noise",)),
    ("AMAZON WEB SERVICES AWS.AMAZON.CO WA", "Cloud & Hosting", 18.00, 74.00, 0, ()),
    ("DIGITALOCEAN.COM NY", "Cloud & Hosting", 6.00, 48.00, 1, ()),
    ("GODADDY.COM 480-505-8855 AZ", "Cloud & Hosting", 21.17, 64.99, 1, ()),
    ("NAME-CHEAP.COM* {r5} PHOENIX AZ", "Cloud & Hosting", 10.98, 15.16, 1, ("ref_noise",)),
    ("CLOUDFLARE CLOUDFLARE.COM CA", "Cloud & Hosting", 5.00, 25.00, 1, ()),
    ("STAPLES 00115 AUSTIN TX", "Office Supplies", 8.00, 140.00, 3, ()),
    ("OFFICE DEPOT #1023 AUSTIN TX", "Office Supplies", 6.00, 95.00, 2, ()),
    ("AMZN MKTP US*{r9} AMZN.COM/BILL WA", ("Office Supplies", "Shopping"), 9.00, 180.00, 6, ("ref_noise",)),
    ("APPLE.COM/US 800-676-2775 CA", ("Equipment & Supplies", "Shopping"), 29.00, 1899.00, 1, ()),
    ("B&H PHOTO 800-606-6969 NY", "Equipment & Supplies", 39.00, 1249.00, 1, ()),
    ("BEST BUY 00005678 AUSTIN TX", ("Equipment & Supplies", "Shopping"), 15.00, 650.00, 1, ()),
    ("FACEBK *{r9} FB.ME/ADS CA", "Advertising & Marketing", 25.00, 300.00, 2, ("ref_noise",)),
    ("GOOGLE *ADS{n10} CC GOOGLE.COM CA", "Advertising & Marketing", 40.00, 420.00, 2, ("ref_noise",)),
    ("LINKEDIN *JOBS/ADS LNKD.IN CA", "Advertising & Marketing", 49.00, 199.00, 1, ()),
    ("VISTAPRINT 866-614-8002 MA", "Advertising & Marketing", 18.00, 120.00, 1, ()),
    ("UPWORK -{n9}REF SAN FRANCISCO CA", "Contractor Payments", 150.00, 900.00, 2, ("ref_noise",)),
    ("FIVERR US NEW YORK NY", "Contractor Payments", 35.00, 400.00, 1, ()),
    ("USPS PO {n10} AUSTIN TX", "Freight & Shipping", 4.50, 38.00, 2, ("ref_noise",)),
    ("THE UPS STORE 4521 AUSTIN TX", "Freight & Shipping", 9.00, 64.00, 1, ()),
    ("FEDEX {n9} MEMPHIS TN", "Freight & Shipping", 12.00, 88.00, 1, ("ref_noise",)),
    ("VZWRLSS*APOCC VISB 800-922-0204 FL", "Telecommunications", 85.00, 85.00, 0, ()),
    ("COMCAST BUSINESS 800-391-3000 PA", "Telecommunications", 119.95, 119.95, 0, ()),
    ("DELTA AIR {n13} DELTA.COM GA", "Business Travel", 189.00, 640.00, 1, ("ref_noise",)),
    ("UNITED {n13} 800-932-2732 TX", "Business Travel", 159.00, 520.00, 1, ("ref_noise",)),
    ("SOUTHWES {n10} 800-435-9792 TX", "Business Travel", 98.00, 380.00, 1, ("ref_noise",)),
    ("MARRIOTT AUSTIN DWTN AUSTIN TX", "Business Travel", 180.00, 690.00, 1, ()),
    ("HILTON GARDEN INN DENVER CO", "Business Travel", 140.00, 520.00, 1, ()),
    ("HERTZ RENT-A-CAR DENVER CO", "Business Travel", 120.00, 380.00, 1, ()),
    ("HOTEL LISBOA CENTRO LISBOA PRT", "Business Travel", 160.00, 480.00, 1, ("fx",)),
    ("TAP AIR PORTUGAL LISBOA PRT", "Business Travel", 240.00, 720.00, 1, ("fx",)),
    ("TST* FRANKLIN BARBECUE AUSTIN TX", ("Business Meals", "Dining & Restaurants"), 18.00, 120.00, 2, ()),
    ("SQ *BLUE HERON CAFE AUSTIN TX", ("Business Meals", "Dining & Restaurants"), 6.00, 48.00, 2, ()),
    ("STARBUCKS STORE 05812 AUSTIN TX", ("Business Meals", "Dining & Restaurants"), 4.25, 18.50, 2, ()),
    ("UDEMY ONLINE COURSES SAN FRANCISCO CA", "Education & Training", 12.99, 94.99, 1, ()),
    ("COURSERA.ORG MOUNTAIN VIEW CA", "Education & Training", 49.00, 59.00, 1, ()),
    ("WEWORK {n6} NEW YORK NY", "Rent & Lease", 350.00, 350.00, 0, ("ref_noise",)),
    ("HISCOX INC 888-202-1527 NY", "Insurance", 42.50, 42.50, 0, ()),
    ("SHELL OIL {n11} AUSTIN TX", "Vehicle & Fuel", 28.00, 72.00, 2, ("ref_noise",)),
    ("EXXONMOBIL 4729 AUSTIN TX", "Vehicle & Fuel", 25.00, 68.00, 1, ()),
    ("BUC-EE'S #22 NEW BRAUNFELS TX", "Vehicle & Fuel", 30.00, 75.00, 1, ()),
    ("PARKMOBILE 877-727-5718 GA", "Parking & Tolls", 2.50, 18.00, 1, ()),
    ("TXTAG TOLL 888-468-9824 TX", "Parking & Tolls", 10.00, 40.00, 1, ()),
    ("SP+ PARKING 0123 AUSTIN TX", "Parking & Tolls", 8.00, 24.00, 1, ()),
    ("UBER *TRIP HELP.UBER.COM CA", "Rideshare & Transit", 9.00, 48.00, 2, ()),
    ("LYFT *RIDE TUE 5PM LYFT.COM CA", "Rideshare & Transit", 8.00, 41.00, 1, ()),
    ("LEGALZOOM* 800-773-0888 CA", "Professional Services", 79.00, 299.00, 1, ()),
    ("TX SOS FILING FEE AUSTIN TX", "Taxes & Licenses", 25.00, 300.00, 1, ()),
]
PERSONAL_MERCHANTS = [
    ("H-E-B #342 AUSTIN TX", "Groceries", 18.00, 210.00, 6, ()),
    ("WHOLEFDS LMR 10234 AUSTIN TX", "Groceries", 12.00, 160.00, 3, ()),
    ("TRADER JOE S #451 AUSTIN TX", "Groceries", 15.00, 120.00, 3, ()),
    ("COSTCO WHSE #0681 AUSTIN TX", "Groceries", 60.00, 340.00, 2, ()),
    ("SPROUTS FARMERS MAR 123 AUSTIN TX", "Groceries", 10.00, 90.00, 2, ()),
    ("STARBUCKS STORE 05812 AUSTIN TX", ("Business Meals", "Dining & Restaurants"), 4.25, 14.00, 3, ()),
    ("DOORDASH*TACODELI DOORDASH.COM CA", "Dining & Restaurants", 16.00, 58.00, 2, ()),
    ("UBER *EATS HELP.UBER.COM CA", "Dining & Restaurants", 14.00, 62.00, 2, ()),
    ("MCDONALD'S F12345 AUSTIN TX", "Dining & Restaurants", 5.00, 22.00, 1, ()),
    ("TORCHY'S TACOS 012 AUSTIN TX", "Dining & Restaurants", 9.00, 46.00, 2, ()),
    ("TST* FRANKLIN BARBECUE AUSTIN TX", ("Business Meals", "Dining & Restaurants"), 18.00, 90.00, 1, ()),
    ("TARGET 00012345 AUSTIN TX", "Shopping", 8.00, 160.00, 3, ()),
    ("WALMART SUPERCENTER #1234 AUSTIN TX", "Shopping", 9.00, 140.00, 2, ()),
    ("ETSY.COM - LUNAWEAVES BROOKLYN NY", "Shopping", 14.00, 85.00, 1, ()),
    ("NORDSTROM #0420 AUSTIN TX", "Shopping", 35.00, 260.00, 1, ()),
    ("TJ MAXX #1234 AUSTIN TX", "Shopping", 12.00, 95.00, 1, ()),
    ("AMZN MKTP US*{r9} AMZN.COM/BILL WA", ("Office Supplies", "Shopping"), 9.00, 140.00, 4, ("ref_noise",)),
    ("NETFLIX.COM 866-579-7172 CA", "Entertainment & Streaming", 15.49, 15.49, 0, ()),
    ("SPOTIFY USA 877-778-1161 NY", "Entertainment & Streaming", 11.99, 11.99, 0, ()),
    ("HULU 877-8244858 CA", "Entertainment & Streaming", 17.99, 17.99, 1, ()),
    ("DISNEY PLUS 888-905-7888 CA", "Entertainment & Streaming", 13.99, 13.99, 1, ()),
    ("AMC 4562 ONLINE 888-440-4262 KS", "Entertainment & Streaming", 14.00, 52.00, 1, ()),
    ("STEAMGAMES.COM 425-889-9642 WA", "Entertainment & Streaming", 4.99, 59.99, 1, ()),
    ("CVS/PHARMACY #07890 AUSTIN TX", "Healthcare", 6.00, 64.00, 2, ()),
    ("WALGREENS #12345 AUSTIN TX", "Healthcare", 5.00, 48.00, 1, ()),
    ("AUSTIN REGIONAL CLINIC AUSTIN TX", "Healthcare", 25.00, 180.00, 1, ()),
    ("GREAT CLIPS #1234 AUSTIN TX", "Personal Care", 18.00, 32.00, 1, ()),
    ("ULTA BEAUTY #567 AUSTIN TX", "Personal Care", 12.00, 85.00, 1, ()),
    ("PLANET FITNESS 800-1234567 NH", "Personal Care", 24.99, 24.99, 0, ()),
    ("THE HOME DEPOT #6543 AUSTIN TX", "Home & Garden", 9.00, 320.00, 2, ()),
    ("LOWE'S #1234 AUSTIN TX", "Home & Garden", 12.00, 260.00, 1, ()),
    ("IKEA ROUND ROCK ROUND ROCK TX", "Home & Garden", 25.00, 400.00, 1, ()),
    ("AMERICAN RED CROSS 800-733-2767 DC", "Charity & Donations", 25.00, 100.00, 1, ()),
    ("GOFUNDME *HELP THE SHELTER REDWOOD CITY CA", "Charity & Donations", 20.00, 75.00, 1, ()),
    ("KUT PUBLIC MEDIA AUSTIN TX", "Charity & Donations", 10.00, 10.00, 0, ()),
    ("SHELL OIL {n11} AUSTIN TX", "Vehicle & Fuel", 26.00, 64.00, 2, ("ref_noise",)),
    ("UBER *TRIP HELP.UBER.COM CA", "Rideshare & Transit", 9.00, 39.00, 1, ()),
]
# recurring monthly charges: (descriptor, day of month)
BIZ_RECURRING = [("ADOBE *CREATIVE CLD 408-536-6000 CA", 5), ("GOOGLE *GSUITE_SAMPLEDES CC@GOOGLE.COM CA", 1),
                 ("AMAZON WEB SERVICES AWS.AMAZON.CO WA", 3), ("DROPBOX*{r8} DROPBOX.COM CA", 12),
                 ("ZOOM.US 888-799-9666 CA", 18), ("FIGMA MONTHLY RENEWAL FIGMA.COM CA", 22),
                 ("VZWRLSS*APOCC VISB 800-922-0204 FL", 24), ("WEWORK {n6} NEW YORK NY", 1),
                 ("HISCOX INC 888-202-1527 NY", 15), ("COMCAST BUSINESS 800-391-3000 PA", 9)]
PERSONAL_RECURRING = [("NETFLIX.COM 866-579-7172 CA", 8), ("SPOTIFY USA 877-778-1161 NY", 14),
                      ("PLANET FITNESS 800-1234567 NH", 17), ("KUT PUBLIC MEDIA AUSTIN TX", 26)]

_BY_DESC = {m[0]: m for m in BIZ_MERCHANTS + PERSONAL_MERCHANTS}


# --------------------------------------------------------------------------- model

@dataclass
class Acct:
    key: str
    institution: str
    kind: str                 # checking | savings | credit_card
    last4: Optional[str]
    label: str
    persona: str              # business | personal
    opening: Decimal
    style: str                # descriptor style for bank-side rows
    rate: float = 8.0         # card-style purchases per month
    fx_fee: bool = False


@dataclass
class Event:
    id: str
    account: str
    date: date
    amount: Decimal
    direction: str            # out | in
    description: str
    category: str
    kind: str                 # purchase|refund|card_payment|transfer|income|fee|interest|check|atm|p2p|bill
    tags: list = field(default_factory=list)
    refund_of: Optional[str] = None
    pair: Optional[str] = None          # id of the other leg
    merchant: str = ""                  # merchant descriptor before bank wrapping
    fx: Optional[tuple] = None          # (currency, foreign amount, rate)
    check_no: Optional[str] = None
    seq: int = 0


@dataclass
class FileSpec:
    name: str
    fmt: str                  # writer key, e.g. "csv:chase_card"
    account: str
    start: date
    end: date


class _Rand:
    def __init__(self, seed: int) -> None:
        self.r = random.Random(seed)

    def digits(self, n: int) -> str:
        return "".join(self.r.choice("0123456789") for _ in range(n))

    def alnum(self, n: int) -> str:
        return "".join(self.r.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(n))

    def fill(self, template: str) -> str:
        out, i = [], 0
        while i < len(template):
            if template[i] == "{":
                j = template.index("}", i)
                tok = template[i + 1:j]
                out.append(self.digits(int(tok[1:])) if tok[0] == "n" else self.alnum(int(tok[1:])))
                i = j + 1
            else:
                out.append(template[i])
                i += 1
        return "".join(out)

    def amount(self, lo: float, hi: float) -> Decimal:
        if lo == hi:
            return money(lo)
        return money(self.r.uniform(lo, hi))


ACCOUNTS: list[Acct] = [
    # business
    Acct("chk4821", "Chase", "checking", "4821", "Chase Business Checking", "business", money(8450), "chase"),
    Acct("ink1111", "Chase", "credit_card", "1111", "Chase Ink Business", "business", money(1240.18), "card", 11),
    Acct("amex3005", "American Express", "credit_card", "3005", "Amex Blue Business", "business", money(612.40),
         "card", 7, fx_fee=True),
    Acct("visa4242", "Sample Bank", "credit_card", "4242", "Sample Bank Visa Signature", "business",
         money(388.02), "card", 9, fx_fee=True),
    Acct("sav9001", "Capital One", "savings", "9001", "Capital One 360 Savings", "business", money(15000), "c360"),
    Acct("usb5566", "US Bank", "checking", "5566", "US Bank Business Checking", "business", money(3200), "usbank"),
    Acct("mercury", "Mercury", "checking", None, "Mercury Checking", "business", money(12500), "mercury"),
    Acct("cu4400", "Sample Credit Union", "checking", "4400", "Credit Union Checking (OFX)", "business",
         money(5100), "cu"),
    Acct("cc8800", "Sample Credit Union", "credit_card", "8800", "Credit Union Visa (OFX)", "business",
         money(240.00), "card", 6),
    Acct("qbo7311", "Sample Card Co", "credit_card", "7311", "Sample Card Co (QBO)", "business", money(95.10),
         "card", 6),
    Acct("xl2468", "Sample Community Bank", "checking", "2468", "Community Bank Checking (XLSX)", "business",
         money(4300), "cu"),
    # personal / mixed
    Acct("wf7788", "Wells Fargo", "checking", "7788", "Wells Fargo Everyday Checking", "personal", money(2950), "wf"),
    Acct("sav6200", "Sample Savings Bank", "savings", "6200", "Online Savings (QFX)", "personal", money(9800), "cu"),
    Acct("citi0505", "Citi", "credit_card", "0505", "Citi Double Cash", "personal", money(410.33), "card", 8),
    Acct("cap2222", "Capital One", "credit_card", "2222", "Capital One Quicksilver", "personal", money(221.90),
         "card", 7),
    Acct("disc6011", "Discover", "credit_card", "6011", "Discover It", "personal", money(150.00), "card", 6),
    Acct("apple", "Apple Card", "credit_card", None, "Apple Card", "personal", money(0), "card", 7),
    Acct("rh", "Robinhood", "credit_card", None, "Robinhood Gold Card", "personal", money(305.12), "card", 8),
    Acct("boachk3141", "Bank of America", "checking", "3141", "BofA Advantage Checking", "personal", money(1830),
         "boa"),
    Acct("boacard2718", "Bank of America", "credit_card", "2718", "BofA Customized Cash", "personal",
         money(512.75), "card", 6),
    Acct("gem", "Gemini", "credit_card", None, "Gemini Credit Card", "personal", money(77.70), "card", 5),
    Acct("schwab", "Charles Schwab Bank", "checking", None, "Schwab Investor Checking", "personal", money(2200),
         "schwab"),
    Acct("qifchk", "Sample Bank", "checking", None, "Quicken Checking (QIF)", "personal", money(1450), "cu"),
    Acct("qifcc", "Sample Bank", "credit_card", None, "Quicken Rewards Card (QIF)", "personal", money(130.00),
         "card", 6),
    Acct("gen1357", "Sample Credit Union", "checking", "1357", "Hill Country CU Checking", "personal",
         money(2600), "cu"),
    Acct("cu8642", "Sample Credit Union", "savings", "8642", "Hill Country CU Savings", "personal", money(7300),
         "cu"),
]
ACCT = {a.key: a for a in ACCOUNTS}

Y = 2025


def _d(m: int, d: int, y: int = Y) -> date:
    return date(y, m, d)


# Import order matters: a later file that re-prints an event is the duplicate.
FILES: list[FileSpec] = [
    FileSpec("Chase4821_Activity_20250101_20250430.csv", "csv:chase_checking", "chk4821", _d(1, 1), _d(4, 30)),
    FileSpec("Chase4821_Statement_2025-05-15.pdf", "pdf:sections", "chk4821", _d(4, 16), _d(5, 15)),
    FileSpec("Chase1111_Activity20250101_20250331.csv", "csv:chase_card", "ink1111", _d(1, 1), _d(3, 31)),
    FileSpec("Chase1111_Activity20250301_20250531.csv", "csv:chase_card", "ink1111", _d(3, 1), _d(5, 31)),
    FileSpec("Chase1111_Statement_2025-06-14.pdf", "pdf:chase_card", "ink1111", _d(5, 15), _d(6, 14)),
    FileSpec("amex_3005_activity.csv", "csv:amex_extended", "amex3005", _d(1, 1), _d(5, 31)),
    FileSpec("Statement_ending_4242_2025-04-09.pdf", "pdf:issuer_card", "visa4242", _d(3, 10), _d(4, 9)),
    FileSpec("Statement_ending_4242_2025-05-09.pdf", "pdf:issuer_card", "visa4242", _d(4, 10), _d(5, 9)),
    FileSpec("360 Performance Savings 9001.csv", "csv:capital_one_360", "sav9001", _d(1, 1), _d(5, 31)),
    FileSpec("usbank-business-checking-5566.csv", "csv:us_bank", "usb5566", _d(2, 1), _d(4, 30)),
    FileSpec("mercury_transactions_q1.csv", "csv:mercury", "mercury", _d(1, 1), _d(3, 31)),
    FileSpec("cu_checking_4400.ofx", "ofx:sgml_bank", "cu4400", _d(1, 1), _d(3, 31)),
    FileSpec("cu_visa_8800.ofx", "ofx:xml_card", "cc8800", _d(1, 1), _d(3, 31)),
    FileSpec("samplecard_7311.qbo", "ofx:qbo_card", "qbo7311", _d(2, 1), _d(4, 30)),
    FileSpec("Community_Bank_Activity_2468.xlsx", "xlsx:split", "xl2468", _d(1, 1), _d(3, 31)),
    FileSpec("WF-7788-checking.csv", "csv:wells_fargo", "wf7788", _d(1, 1), _d(4, 30)),
    FileSpec("WellsFargo_Statement_7788_2025-01-14.pdf", "pdf:columns", "wf7788", _d(12, 15, 2024), _d(1, 14)),
    FileSpec("Savings_6200_Jan-Feb.qfx", "ofx:qfx_savings", "sav6200", _d(1, 1), _d(2, 28)),
    FileSpec("Savings_6200_Feb-Apr.qfx", "ofx:qfx_savings", "sav6200", _d(2, 1), _d(4, 30)),
    FileSpec("Citi-0505-activity.csv", "csv:citi", "citi0505", _d(1, 1), _d(4, 30)),
    FileSpec("Capital One Quicksilver 2222 transactions.csv", "csv:capital_one_card", "cap2222", _d(1, 1),
             _d(4, 30)),
    FileSpec("Discover-Card-6011-2025.csv", "csv:discover", "disc6011", _d(2, 1), _d(4, 30)),
    FileSpec("Apple Card Transactions - Spring.csv", "csv:apple_card", "apple", _d(3, 1), _d(5, 31)),
    FileSpec("Robinhood_Card_Statement_2025-06-02.pdf", "pdf:robinhood", "rh", _d(5, 3), _d(6, 2)),
    FileSpec("3f1c9a2e-7b44-4d0e-9a61-0c5d2e8b7f13.csv", "csv:robinhood_card", "rh", _d(6, 3), _d(8, 31)),
    FileSpec("bofa_checking_3141_q1.csv", "csv:boa_checking", "boachk3141", _d(1, 1), _d(3, 31)),
    FileSpec("bofa_card_2718_q1.csv", "csv:boa_card", "boacard2718", _d(1, 1), _d(3, 31)),
    FileSpec("gemini-credit-card-transactions.csv", "csv:gemini", "gem", _d(1, 1), _d(3, 31)),
    FileSpec("Schwab_Checking_Transactions_20250331.csv", "csv:schwab", "schwab", _d(1, 1), _d(3, 31)),
    FileSpec("quicken_checking_export.qif", "qif:bank", "qifchk", _d(1, 1), _d(3, 31)),
    FileSpec("quicken_card_export.qif", "qif:ccard", "qifcc", _d(1, 1), _d(3, 31)),
    FileSpec("hill_country_cu_1357.csv", "csv:generic", "gen1357", _d(1, 1), _d(3, 31)),
    FileSpec("HillCountryCU_Savings_8642_March.pdf", "pdf:balance", "cu8642", _d(3, 1), _d(3, 31)),
]

# card -> (paying bank account or None = not in the corpus, bank-side descriptor, card-side descriptor)
CARD_PAYMENTS = {
    "ink1111": ("chk4821", "PAYMENT TO CHASE CARD ENDING IN 1111 {mmdd}", "Payment Thank You-Mobile"),
    "amex3005": ("chk4821", "AMERICAN EXPRESS ACH PMT {r5} WEB ID: 2005032111", "AUTOPAY PAYMENT RECEIVED - THANK YOU"),
    "visa4242": ("chk4821", "SAMPLE BANK CRCARDPMT {n9} WEB ID: {n10}", "PAYMENT THANK YOU"),
    "cc8800": ("cu4400", "ONLINE PAYMENT TO VISA XXXX8800", "PAYMENT RECEIVED THANK YOU"),
    "qbo7311": (None, "", "PAYMENT - THANK YOU"),
    "citi0505": ("wf7788", "CITI CARD ONLINE PAYMENT {n9} WEB ID: CITICARDAP", "ONLINE PAYMENT, THANK YOU"),
    "cap2222": ("wf7788", "CAPITAL ONE ONLINE PYMT {r6}", "CAPITAL ONE MOBILE PYMT"),
    "disc6011": (None, "", "INTERNET PAYMENT - THANK YOU"),
    "apple": ("wf7788", "APPLECARD GSBANK PAYMENT {n7} WEB ID: 9999999999",
              "ACH DEPOSIT INTERNET TRANSFER FROM ACCOUNT ENDING IN 7788"),
    "rh": ("wf7788", "ROBINHOOD CREDIT PAYMENT {n10}", "Payment"),
    "boacard2718": ("boachk3141", "Online Banking payment to CRD 2718 Confirmation# {r10}", "PAYMENT - THANK YOU"),
    "gem": (None, "", ""),
    "qifcc": ("qifchk", "PAYMENT TO SAMPLE REWARDS CARD", "PAYMENT THANK YOU"),
}
# (from, to, day, low, high, from-descriptor, to-descriptor, months)
TRANSFERS = [
    ("chk4821", "sav9001", 2, 500, 1500, "ONLINE TRANSFER TO SAV XXXXXX9001 TRANSACTION#: {n11} {mmdd}",
     "Deposit from CHASE TRANSFER XXXXXX4821", (1, 2, 3, 4, 5)),
    ("sav9001", "chk4821", 27, 800, 2000, "Withdrawal to CHASE CHECKING XXXXXX4821",
     "ONLINE TRANSFER FROM SAV XXXXXX9001 TRANSACTION#: {n11}", (2, 4)),
    ("chk4821", "wf7788", 1, 2500, 2500, "ONLINE TRANSFER TO CHK ...7788 TRANSACTION#: {n11} {mmdd}",
     "ONLINE TRANSFER FROM CHASE BUSINESS XXXXXX4821 REF #IB{r7}", (1, 2, 3, 4, 5)),
    ("usb5566", "chk4821", 10, 1000, 3000, "ELECTRONIC WITHDRAWAL TRANSFER TO CHASE 4821",
     "ORIG CO NAME:U.S. BANK ORIG ID:{n10} DESC DATE:{yymmdd} CO ENTRY DESCR:TRANSFER SEC:PPD TRACE#:{n15}",
     (2, 3, 4)),
    ("wf7788", "sav6200", 20, 200, 600, "ONLINE TRANSFER TO SAMPLE SAVINGS XXXXXX6200 REF #IB{r7} ON {mmddyy}",
     "TRANSFER FROM CHECKING XXXXXX7788", (1, 2, 3, 4)),
    ("mercury", "xl2468", 15, 1000, 2500, "Transfer to Sample Community Bank ••2468",
     "TRANSFER FROM MERCURY CHECKING", (1, 2, 3)),
    ("gen1357", "cu8642", 5, 100, 400, "TRANSFER TO SAVINGS 8642", "TRANSFER FROM CHECKING 1357", (3,)),
]

BANK_PURCHASE = {
    "chase": "CARD PURCHASE {mmdd} {m} CARD 4821",
    "wf": "PURCHASE AUTHORIZED ON {mmdd} {m} S{n15} CARD 7788",
    "boa": "CHECKCARD {mmdd_nos} {m} {n23}",
    "usbank": "DEBIT PURCHASE -VISA {m}",
    "cu": "POS PURCHASE {m}",
    "schwab": "VISA DEBIT {m}",
    "mercury": "{m}",
    "c360": "{m}",
}


class _Gen:
    def __init__(self, seed: int) -> None:
        self.rnd = _Rand(seed)
        self.r = self.rnd.r
        self.events: list[Event] = []
        self.coverage = {}
        for f in FILES:
            lo, hi = self.coverage.get(f.account, (f.start, f.end))
            self.coverage[f.account] = (min(lo, f.start), max(hi, f.end))

    # ------------------------------------------------------------------ helpers
    def covered(self, acct: str, d: date) -> bool:
        lo, hi = self.coverage[acct]
        return lo <= d <= hi

    def add(self, acct: str, d: date, amount: Decimal, direction: str, desc: str, category: str, kind: str,
            tags=(), merchant: str = "", **kw) -> Optional[Event]:
        if not self.covered(acct, d) or amount <= 0:
            return None
        e = Event(f"e{len(self.events) + 1:05d}", acct, d, money(amount), direction, " ".join(desc.split()),
                  category, kind, list(tags), merchant=merchant or desc, seq=len(self.events), **kw)
        self.events.append(e)
        return e

    def months(self, acct: str):
        lo, hi = self.coverage[acct]
        y, m = lo.year, lo.month
        while (y, m) <= (hi.year, hi.month):
            yield y, m
            m += 1
            if m == 13:
                y, m = y + 1, 1

    @staticmethod
    def _dim(y: int, m: int) -> int:
        return ((date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)).day

    def fill(self, template: str, d: date) -> str:
        s = (template.replace("{mmdd}", d.strftime("%m/%d")).replace("{mmdd_nos}", d.strftime("%m%d"))
             .replace("{yymmdd}", d.strftime("%y%m%d")).replace("{mmddyy}", d.strftime("%m/%d/%y")))
        return self.rnd.fill(s)

    def pick(self, persona: str, pool_bias: float):
        biz = self.r.random() < pool_bias
        pool = BIZ_MERCHANTS if biz else PERSONAL_MERCHANTS
        weights = [m[4] for m in pool]
        return self.r.choices(pool, weights=weights)[0]

    @staticmethod
    def category_for(m, persona: str) -> str:
        c = m[1]
        if isinstance(c, tuple):
            return c[0] if persona == "business" else c[1]
        return c

    # ------------------------------------------------------------------ generation
    def purchase(self, a: Acct, d: date, m, bank_wrap: bool, tags=()) -> Optional[Event]:
        merchant = self.rnd.fill(m[0])
        amt = self.rnd.amount(m[2], m[3])
        desc = merchant
        tg = list(m[5]) + list(tags)
        if bank_wrap:
            auth = d - timedelta(days=self.r.choice((0, 1, 1, 2)))
            desc = self.fill(BANK_PURCHASE[a.style].replace("{m}", merchant), auth)
            if a.style in ("wf", "boa", "chase"):
                tg.append("ref_noise")
        fx = None
        if "fx" in tg:
            rate = Decimal(str(round(self.r.uniform(1.04, 1.12), 6)))
            fx = ("EURO", money(amt / rate), rate)
        e = self.add(a.key, d, amt, "out", desc, self.category_for(m, a.persona), "purchase", sorted(set(tg)),
                     merchant=merchant, fx=fx)
        if e is not None and fx is not None and a.fx_fee:
            self.add(a.key, d, money(amt * Decimal("0.027")), "out", "FOREIGN TRANSACTION FEE",
                     "Bank & Merchant Fees", "fee", ["fx"])
        return e

    def card_month(self, a: Acct, y: int, m: int) -> None:
        dim = self._dim(y, m)
        recurring = BIZ_RECURRING if a.persona == "business" else PERSONAL_RECURRING
        # each card carries a stable subset of the repeating merchants
        subset = [rc for i, rc in enumerate(recurring) if (i + len(a.key)) % 3 != 0][: 3 if a.rate > 6 else 2]
        for desc, day in subset:
            self.purchase(a, date(y, m, min(day, dim)), _BY_DESC[desc], False, ("recurring",))
        n = max(1, int(self.r.gauss(a.rate, 1.5)))
        bias = 0.8 if a.persona == "business" else 0.15
        for _ in range(n):
            d = date(y, m, self.r.randint(1, dim))
            mm = self.pick(a.persona, bias)
            e = self.purchase(a, d, mm, False)
            if e is None:
                continue
            if self.r.random() < 0.06 and mm[2] != mm[3]:
                # same merchant, same amount, same day: a legitimate repeat, not a duplicate
                self.add(a.key, d, e.amount, "out", e.description, e.category, "purchase",
                         sorted(set(e.tags + ["same_day_repeat"])), merchant=e.merchant)
                e.tags = sorted(set(e.tags + ["same_day_repeat"]))
            if self.r.random() < 0.07:
                full = self.r.random() < 0.6
                amt = e.amount if full else money(e.amount * Decimal(str(round(self.r.uniform(0.2, 0.8), 2))))
                rd = d + timedelta(days=self.r.randint(3, 21))
                self.add(a.key, rd, amt, "in", e.description, e.category, "refund", ["refund"],
                         merchant=e.merchant, refund_of=e.id)
        if a.key in ("citi0505", "disc6011") and m == 3:
            self.add(a.key, date(y, m, min(28, dim)), money(self.r.uniform(8, 30)), "out",
                     "INTEREST CHARGE ON PURCHASES", "Interest Expense", "interest")

    def bank_month(self, a: Acct, y: int, m: int) -> None:
        dim = self._dim(y, m)
        r = self.r
        if a.kind == "savings":
            self.add(a.key, date(y, m, dim), money(r.uniform(1.5, 28)), "in",
                     {"c360": "Monthly Interest Paid"}.get(a.style, "INTEREST PAYMENT"), "Interest Income",
                     "interest")
            return
        biz = a.persona == "business"
        if biz:
            for _ in range(r.randint(2, 3)):
                d = date(y, m, r.randint(1, dim))
                kind = r.choice(("ach", "ach", "stripe", "square", "mobile"))
                if kind == "ach":
                    client = r.choice(("ACME DESIGN CORP", "NORTHWIND TRADERS LLC", "BLUEFIN MEDIA INC",
                                       "CONTOSO PARTNERS LP"))
                    desc = self.fill(f"ORIG CO NAME:{client} ORIG ID:{{n10}} DESC DATE:{{yymmdd}} CO ENTRY "
                                     f"DESCR:INVOICE SEC:CCD TRACE#:{{n15}} EED:{{yymmdd}} IND ID:{{n6}} "
                                     f"IND NAME:SAMPLE DESIGN STUDIO TRN: {{n10}}TC", d)
                    cat, tags = "Business Income (Sales & Payouts)", ["ref_noise"]
                elif kind == "stripe":
                    desc, cat, tags = self.fill("STRIPE TRANSFER ST-{r12} SAMPLE DESIGN", d), \
                        "Business Income (Sales & Payouts)", ["ref_noise"]
                elif kind == "square":
                    desc, cat, tags = self.fill("SQUARE INC SQ{yymmdd} {r10}", d), \
                        "Business Income (Sales & Payouts)", ["ref_noise"]
                else:
                    desc, cat, tags = self.fill("MOBILE DEPOSIT : REF NUMBER :{n12}", d), "Income & Deposits", \
                        ["ref_noise"]
                self.add(a.key, d, money(r.choice((1, 1, 2, 3)) * r.uniform(400, 2400)), "in", desc, cat,
                         "income", tags)
            bills = [("AUSTIN ENERGY ONLINE PMT {n8}", "Utilities", 60, 190, 12),
                     ("GUSTO NET {n6} CONTRACTOR PMT", "Contractor Payments", 600, 1800, 25)]
            if m in (1, 4):
                bills.append(("IRS USATAXPYMT {n15}", "Income Tax Payments", 1200, 3400, 15))
            if m == 3:
                bills.append(("TX COMPTROLLER TAX PMT {n9}", "Taxes & Licenses", 50, 300, 20))
            if m == 2:
                bills.append(("NEXT INSURANCE PALO ALTO CA", "Insurance", 380, 380, 11))
        else:
            for day in (r.choice((1, 2)), 15):
                d = date(y, m, min(day, dim))
                self.add(a.key, d, money(r.uniform(2100, 2300)), "in",
                         self.fill("NORTHWIND HEALTH PAYROLL PPD ID: {n10}", d), "Paycheck (W-2 Wages)", "income",
                         ["ref_noise"])
            bills = [("ROCKET MORTGAGE PMT {n8}", "Loan & Mortgage Payments", 1850, 1850, 1),
                     ("TEXAS GAS SERVICE PAYMENT", "Utilities", 25, 85, 8),
                     ("GEICO *AUTO 800-841-3000 DC", "Insurance", 132.40, 132.40, 6),
                     ("BCBS TX PREMIUM {n6}", "Health Insurance", 412.00, 412.00, 3)]
        for desc, cat, lo, hi, day in bills:
            d = date(y, m, min(day, dim))
            self.add(a.key, d, self.rnd.amount(lo, hi), "out", self.fill(desc, d), cat, "bill",
                     ["ref_noise"] if "{" in desc else [])
        # debit-card purchases
        for _ in range(r.randint(2, 5) if biz else r.randint(6, 12)):
            d = date(y, m, r.randint(1, dim))
            e = self.purchase(a, d, self.pick(a.persona, 0.7 if biz else 0.1), True)
            if e is not None and r.random() < 0.03:
                self.add(a.key, d + timedelta(days=r.randint(3, 12)), e.amount, "in",
                         e.description.replace("PURCHASE", "PURCHASE RETURN", 1) if a.style == "wf" else e.description,
                         e.category, "refund", ["refund"], merchant=e.merchant, refund_of=e.id)
        if r.random() < 0.6:
            d = date(y, m, r.randint(1, dim))
            self.add(a.key, d, money(r.choice((40, 60, 100, 200))), "out",
                     self.fill("ATM WITHDRAWAL {n6} 1200 S CONGRESS AUSTIN TX", d), "Cash Withdrawal", "atm",
                     ["ref_noise"])
        if r.random() < 0.7:
            d = date(y, m, r.randint(1, dim))
            who = r.choice(("JORDAN SAMPLE", "CASEY EXAMPLE", "RILEY PLACEHOLDER"))
            self.add(a.key, d, money(r.uniform(20, 400)), "out",
                     self.fill(f"ZELLE PAYMENT TO {who} {{r11}}", d), "Zelle & P2P Payments", "p2p", ["ref_noise"])
        if a.style in ("chase", "wf", "boa", "cu") and r.random() < 0.5:
            d = date(y, m, r.randint(1, dim))
            no = str(1040 + len([e for e in self.events if e.check_no]))
            self.add(a.key, d, money(r.uniform(80, 900)), "out", f"CHECK {no}", "Checks Written", "check",
                     check_no=no)
        if a.style in ("chase", "wf") and m in (2, 5):
            self.add(a.key, date(y, m, dim), money(15 if biz else 10), "out", "MONTHLY SERVICE FEE",
                     "Bank & Merchant Fees", "fee")

    def payments(self) -> None:
        r = self.r
        for card, (bank, bdesc, cdesc) in CARD_PAYMENTS.items():
            for y, m in self.months(card):
                spend = sum((e.amount for e in self.events if e.account == card and e.direction == "out"
                             and e.date.month == (m - 1 or 12)), D0)
                amt = money(spend if spend > 0 else r.uniform(150, 900))
                bd = date(y, m, r.randint(16, 23))
                cd = bd + timedelta(days=r.choice((0, 1, 1, 2, 3)))
                ce = self.add(card, cd, amt, "in", self.fill(cdesc, cd) if cdesc else "", "Credit Card Payment",
                              "card_payment", ["card_payment"])
                be = self.add(bank, bd, amt, "out", self.fill(bdesc, bd), "Credit Card Payment", "card_payment",
                              ["card_payment"]) if bank else None
                if ce and be:
                    ce.pair, be.pair = be.id, ce.id

    def transfers(self) -> None:
        r = self.r
        for frm, to, day, lo, hi, fdesc, tdesc, months in TRANSFERS:
            for m in months:
                d = date(Y, m, day)
                amt = money(round(r.uniform(lo, hi) / 50) * 50) if lo != hi else money(lo)
                td = d + timedelta(days=r.choice((0, 0, 1, 2)))
                fe = self.add(frm, d, amt, "out", self.fill(fdesc, d), "Transfer Between Accounts", "transfer",
                              ["transfer"])
                te = self.add(to, td, amt, "in", self.fill(tdesc, td), "Transfer Between Accounts", "transfer",
                              ["transfer"])
                if fe and te:
                    fe.pair, te.pair = te.id, fe.id

    def run(self) -> list[Event]:
        for a in ACCOUNTS:
            for y, m in self.months(a.key):
                if a.kind == "credit_card":
                    self.card_month(a, y, m)
                else:
                    self.bank_month(a, y, m)
        self.transfers()
        self.payments()
        # stable chronological order per account (ties: generation order)
        self.events.sort(key=lambda e: (e.account, e.date, e.seq))
        return self.events


# --------------------------------------------------------------------------- statements

@dataclass
class Row:
    id: str
    ev: Event
    description: str          # as printed in this file
    duplicate: bool


@dataclass
class Stmt:
    spec: FileSpec
    acct: Acct
    rows: list[Row]
    opening: Decimal
    closing: Decimal
    rng: random.Random
    noise: int = 0

    def total(self, direction: str) -> Decimal:
        return sum((r.ev.amount for r in self.rows if r.ev.direction == direction), D0)


def _balance_after(a: Acct, events: list[Event], until: date, inclusive: bool) -> Decimal:
    bal = a.opening
    for e in events:
        if e.date < until or (inclusive and e.date == until):
            sign = 1 if (e.direction == "in") != (a.kind == "credit_card") else -1
            bal += sign * e.amount
    return bal


def _signed(a: Acct, e: Event) -> Decimal:
    """Effect on the printed balance (cards: balance owed)."""
    return (e.amount if e.direction == "in" else -e.amount) * (-1 if a.kind == "credit_card" else 1)


# ------------------------------------------------------------------ csv helpers

def _csv(rows: list[list], quoting=csv.QUOTE_MINIMAL, eol: str = "\r\n", bom: bool = False,
         trailing_comma: bool = False) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=quoting, lineterminator=eol)
    for r in rows:
        w.writerow(r)
    s = buf.getvalue()
    if trailing_comma:
        s = eol.join((ln + "," if i > 0 and ln else ln) for i, ln in enumerate(s.split(eol)))
    return (("﻿" if bom else "") + s).encode("utf-8")


def _md(d: date) -> str:
    return d.strftime("%m/%d/%Y")


_BANK_CAT = {"Groceries": "Groceries", "Dining & Restaurants": "Food & Drink", "Business Meals": "Food & Drink",
             "Business Travel": "Travel", "Vehicle & Fuel": "Gas", "Entertainment & Streaming": "Entertainment",
             "Healthcare": "Health & Wellness", "Personal Care": "Personal", "Telecommunications": "Bills & Utilities",
             "Utilities": "Bills & Utilities", "Software & Subscriptions": "Bills & Utilities",
             "Cloud & Hosting": "Bills & Utilities", "Bank & Merchant Fees": "Fees & Adjustments",
             "Home & Garden": "Home", "Professional Services": "Professional Services",
             "Rideshare & Transit": "Travel", "Parking & Tolls": "Travel"}


def _bank_cat(e: Event) -> str:
    if e.kind == "card_payment":
        return ""
    return _BANK_CAT.get(e.category, "Shopping")


def _newest_first(rows: list[Row]) -> list[Row]:
    return list(reversed(rows))


# ------------------------------------------------------------------ CSV writers

def w_chase_card(st: Stmt) -> bytes:
    out = [["Card", "Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"]]
    for r in _newest_first(st.rows):
        e = r.ev
        typ = {"card_payment": "Payment", "refund": "Return", "fee": "Fee", "interest": "Fee"}.get(e.kind, "Sale")
        amt = e.amount if e.direction == "in" else -e.amount
        out.append([st.acct.last4, _md(e.date), _md(e.date + timedelta(days=1)), r.description, _bank_cat(e), typ,
                    f2(amt), ""])
    return _csv(out, bom=True)


def w_chase_checking(st: Stmt) -> bytes:
    out = [["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"]]
    bal = st.opening
    rows = []
    for r in st.rows:
        e = r.ev
        bal += _signed(st.acct, e)
        typ = {"card_payment": "LOAN_PMT", "transfer": "ACCT_XFER", "income": "ACH_CREDIT", "bill": "ACH_DEBIT",
               "purchase": "DEBIT_CARD", "refund": "DEBIT_CARD", "atm": "ATM", "p2p": "QUICKPAY_DEBIT",
               "check": "CHECK_PAID", "fee": "FEE_TRANSACTION"}.get(e.kind, "MISC_DEBIT")
        if e.kind == "income" and e.description.startswith("MOBILE"):
            typ = "CHECK_DEPOSIT"
        details = "CHECK" if e.kind == "check" else ("CREDIT" if e.direction == "in" else "DEBIT")
        if e.kind == "income" and e.description.startswith("MOBILE"):
            details = "DSLIP"
        amt = e.amount if e.direction == "in" else -e.amount
        rows.append([details, _md(e.date), r.description, f2(amt), typ, f2(bal), e.check_no or ""])
    out += list(reversed(rows))
    return _csv(out, trailing_comma=True)


def w_citi(st: Stmt) -> bytes:
    out = [["Status", "Date", "Description", "Debit", "Credit"]]
    for r in _newest_first(st.rows):
        e = r.ev
        out.append(["Cleared", _md(e.date), r.description, f2(e.amount) if e.direction == "out" else "",
                    f2(-e.amount) if e.direction == "in" else ""])
    return _csv(out)


def w_capital_one_card(st: Stmt) -> bytes:
    out = [["Transaction Date", "Posted Date", "Card No.", "Description", "Category", "Debit", "Credit"]]
    for r in _newest_first(st.rows):
        e = r.ev
        cat = "Payment/Credit" if e.direction == "in" else {"Dining & Restaurants": "Dining",
                                                            "Groceries": "Grocery"}.get(e.category, "Merchandise")
        out.append([e.date.isoformat(), (e.date + timedelta(days=1)).isoformat(), st.acct.last4, r.description, cat,
                    f2(e.amount) if e.direction == "out" else "", f2(e.amount) if e.direction == "in" else ""])
    # a zero-amount authorization adjustment the bank also exports
    out.insert(2, [st.spec.start.isoformat(), st.spec.start.isoformat(), st.acct.last4, "AUTH ADJUSTMENT",
                   "Other", "0.00", ""])
    st.noise += 1
    return _csv(out)


def w_us_bank(st: Stmt) -> bytes:
    out = [["Date", "Transaction", "Name", "Memo", "Amount"]]
    for r in st.rows:
        e = r.ev
        amt = e.amount if e.direction == "in" else -e.amount
        out.append([_md(e.date), "CREDIT" if e.direction == "in" else "DEBIT", r.description,
                    f"Download from usbank.com. {r.description[:18]}", f2(amt)])
    return _csv(out, quoting=csv.QUOTE_ALL)


def w_capital_one_360(st: Stmt) -> bytes:
    out = [["Account Number", "Transaction Description", "Transaction Date", "Transaction Type",
            "Transaction Amount", "Balance"]]
    bal = st.opening
    rows = []
    for r in st.rows:
        e = r.ev
        bal += _signed(st.acct, e)
        rows.append([st.acct.last4, r.description, e.date.strftime("%m/%d/%y"),
                     "Credit" if e.direction == "in" else "Debit", f2(e.amount), f2(bal)])
    return _csv(out + list(reversed(rows)))


def w_gemini(st: Stmt) -> bytes:
    out = [["Reference Number", "Transaction Post Date", "Description of Transaction", "Transaction Type", "Amount"]]
    for r in st.rows:
        e = r.ev
        typ = {"card_payment": "payment_transaction", "refund": "purchase_return"}.get(e.kind, "clearing")
        amt = e.amount if e.direction == "out" else -e.amount
        out.append([f"{st.rng.randint(10**9, 10**10 - 1)}", e.date.strftime("%m/%d/%y"), r.description, typ, f2(amt)])
    return _csv(out)


def w_apple_card(st: Stmt) -> bytes:
    out = [["Transaction Date", "Clearing Date", "Description", "Merchant", "Category", "Type", "Amount (USD)",
            "Purchased By"]]
    for r in _newest_first(st.rows):
        e = r.ev
        typ = {"card_payment": "Payment", "refund": "Credit", "interest": "Interest"}.get(e.kind, "Purchase")
        amt = e.amount if e.direction == "out" else -e.amount
        merchant = " ".join(w.capitalize() for w in e.merchant.split()[:2])
        out.append([_md(e.date), _md(e.date + timedelta(days=1)), r.description, merchant,
                    "Payment" if e.kind == "card_payment" else _bank_cat(e) or "Other", typ, f2(amt), "Pat Sample"])
    return _csv(out)


def w_robinhood_card(st: Stmt) -> bytes:
    out = [["Date", "Time", "Cardholder", "Amount", "Points", "Balance", "Status", "Type", "Merchant", "Description"]]
    bal = st.opening
    rows = []
    for r in st.rows:
        e = r.ev
        bal += _signed(st.acct, e)
        typ = {"card_payment": "Payment", "refund": "Refund", "fee": "Fee", "interest": "Interest"}.get(e.kind,
                                                                                                       "Purchase")
        amt = e.amount if e.direction == "out" else -e.amount
        pts = int(e.amount * 3) if e.direction == "out" else 0
        rows.append([e.date.isoformat(), f"{st.rng.randint(1, 12)}:{st.rng.randint(10, 59)} PM", "Pat Sample",
                     f2(amt), str(pts), f2(bal), "Posted", typ, r.description, "123 Sample St Austin TX"])
    rows = list(reversed(rows))
    # newest rows that must not be imported: still pending, and a declined swipe
    last = st.spec.end
    rows.insert(0, [last.isoformat(), "9:15 PM", "Pat Sample", "23.40", "70", "", "Pending", "Purchase",
                    "Torchy's Tacos", ""])
    rows.insert(1, [last.isoformat(), "8:02 PM", "Pat Sample", "61.00", "0", "", "Declined", "Purchase",
                    "Sample Electronics", ""])
    st.noise += 2
    return _csv(out + rows)


def w_discover(st: Stmt) -> bytes:
    out = [["Trans. Date", "Post Date", "Description", "Amount", "Category"]]
    for r in st.rows:
        e = r.ev
        amt = e.amount if e.direction == "out" else -e.amount
        cat = "Payments and Credits" if e.kind == "card_payment" else (
            "Awards and Rebate Credits" if e.kind == "refund" else {"Groceries": "Supermarkets",
                                                                  "Dining & Restaurants": "Restaurants"}.get(
                e.category, "Merchandise"))
        out.append([_md(e.date), _md(e.date + timedelta(days=1)), r.description, f2(amt), cat])
    return _csv(out)


def w_boa_card(st: Stmt) -> bytes:
    out = [["Posted Date", "Reference Number", "Payee", "Address", "Amount"]]
    for r in _newest_first(st.rows):
        e = r.ev
        amt = e.amount if e.direction == "in" else -e.amount
        out.append([_md(e.date), "2" + "".join(st.rng.choice("0123456789") for _ in range(22)), r.description,
                    "" if e.kind == "card_payment" else "AUSTIN TX", f2(amt)])
    return _csv(out)


def w_boa_checking(st: Stmt) -> bytes:
    s, e_ = st.spec.start, st.spec.end
    pre = [["Description", "", "Summary Amt."],
           [f"Beginning balance as of {_md(s)}", "", fm(st.opening)],
           ["Total credits", "", fm(st.total("in"))],
           ["Total debits", "", "-" + fm(st.total("out"))],
           [f"Ending balance as of {_md(e_)}", "", fm(st.closing)],
           [],
           ["Date", "Description", "Amount", "Running Bal."],
           [_md(s), f"Beginning balance as of {_md(s)}", "", fm(st.opening)]]
    st.noise += 1
    bal = st.opening
    for r in st.rows:
        e = r.ev
        bal += _signed(st.acct, e)
        amt = e.amount if e.direction == "in" else -e.amount
        pre.append([_md(e.date), r.description, f"{amt:,.2f}", fm(bal) if bal >= 0 else "-" + fm(bal)])
    return _csv(pre)


def w_amex_extended(st: Stmt) -> bytes:
    out = [["Date", "Description", "Amount", "Extended Details", "Appears On Your Statement As", "Address",
            "City/State", "Zip Code", "Country", "Reference", "Category"]]
    for r in _newest_first(st.rows):
        e = r.ev
        amt = e.amount if e.direction == "out" else -e.amount
        ext = f"{st.rng.randint(10**8, 10**9 - 1)}     {r.description[:22]}\n{r.description[:30]}"
        if e.fx:
            ext += (f"\nFOREIGN SPEND AMOUNT: {f2(e.fx[1])} {e.fx[0]} COMMISSION AMOUNT: 0 "
                    f"CURRENCY EXCHANGE RATE: {e.fx[2]}")
        cat = "Fees & Adjustments-Fees & Adjustments" if e.kind in ("fee", "interest") else (
            "" if e.kind == "card_payment" else "Business Services-Other Services")
        out.append([_md(e.date), r.description, f2(amt), ext, r.description, "100 SAMPLE AVE", "AUSTIN\nTX", "78701",
                    "UNITED STATES", f"'{st.rng.randint(10**14, 10**15 - 1)}'", cat])
    return _csv(out)


def w_wells_fargo(st: Stmt) -> bytes:
    out = []
    for r in _newest_first(st.rows):
        e = r.ev
        amt = e.amount if e.direction == "in" else -e.amount
        out.append([_md(e.date), f2(amt), "*", e.check_no or "", r.description])
    return _csv(out, quoting=csv.QUOTE_ALL)


def w_generic(st: Stmt) -> bytes:
    out = [["Date Posted", "Payee / Description", "Withdrawal Amount", "Deposit Amount", "Balance"]]
    bal = st.opening
    for r in st.rows:
        e = r.ev
        bal += _signed(st.acct, e)
        out.append([e.date.strftime("%m/%d/%Y"), r.description, "$" + fm(e.amount) if e.direction == "out" else "",
                    "$" + fm(e.amount) if e.direction == "in" else "", "$" + fm(bal)])
    return _csv(out)


def w_schwab(st: Stmt) -> bytes:
    out = [["Date", "Status", "Type", "CheckNumber", "Description", "Withdrawal", "Deposit", "RunningBalance"]]
    bal = st.opening
    rows = []
    for r in st.rows:
        e = r.ev
        bal += _signed(st.acct, e)
        typ = {"income": "ACH", "transfer": "TRANSFER", "check": "CHECK", "atm": "ATM"}.get(
            e.kind, "DEBIT" if e.direction == "out" else "CREDIT")
        rows.append([_md(e.date), "Posted", typ, e.check_no or "", r.description,
                     "$" + fm(e.amount) if e.direction == "out" else "",
                     "$" + fm(e.amount) if e.direction == "in" else "", "$" + fm(bal)])
    rows = list(reversed(rows))
    rows.insert(0, [_md(st.spec.end), "Pending", "DEBIT", "", "VISA DEBIT H-E-B #342 AUSTIN TX", "$54.18", "", ""])
    st.noise += 1
    return _csv(out + rows, quoting=csv.QUOTE_ALL)


def w_mercury(st: Stmt) -> bytes:
    out = [["Date (UTC)", "Description", "Amount", "Status", "Bank Description", "Reference", "Note"]]
    rows = []
    for r in st.rows:
        e = r.ev
        amt = e.amount if e.direction == "in" else -e.amount
        rows.append([e.date.strftime("%m-%d-%Y"), r.description, f"{amt:,.2f}", "Sent", r.description.upper(), "",
                     ""])
    mid = st.spec.start + timedelta(days=40)
    rows.insert(len(rows) // 2, [mid.strftime("%m-%d-%Y"), "Sample Hosting LLC", "-31.07", "Failed",
                                 "SAMPLE HOSTING LLC", "", "card declined"])
    st.noise += 1
    return _csv(out + list(reversed(rows)))


# ------------------------------------------------------------------ OFX / QIF / XLSX

def _ofx_dt(d: date) -> str:
    return d.strftime("%Y%m%d") + "120000.000[-6:CST]"


def _ofx_trn(st: Stmt, r: Row, card: bool, xml: bool, fitid_prefix: str) -> str:
    e = r.ev
    amt = e.amount if e.direction == "in" else -e.amount
    if card:
        ttype = "PAYMENT" if e.kind == "card_payment" else ("CREDIT" if e.direction == "in" else "DEBIT")
    else:
        ttype = {"check": "CHECK", "atm": "ATM", "fee": "FEE", "income": "DIRECTDEP", "transfer": "XFER",
                 "interest": "INT", "purchase": "POS"}.get(e.kind, "CREDIT" if e.direction == "in" else "DEBIT")
    fitid = fitid_prefix + e.id[1:] + e.date.strftime("%m%d")
    # OFX NAME is limited to 32 characters: banks put the rest of the descriptor in MEMO
    name_lines = _wrap(r.description, 32)
    name, memo = name_lines[0], " ".join(name_lines[1:])
    parts = [("TRNTYPE", ttype), ("DTPOSTED", _ofx_dt(e.date)), ("TRNAMT", f2(amt)), ("FITID", fitid)]
    if e.check_no:
        parts.append(("CHECKNUM", e.check_no))
    parts.append(("NAME", name.replace("&", "&amp;") if xml else name))
    if memo:
        parts.append(("MEMO", memo.replace("&", "&amp;") if xml else memo))
    if xml:
        body = "".join(f"<{k}>{v}</{k}>" for k, v in parts)
    else:
        body = "\n".join(f"<{k}>{v}" for k, v in parts)
    return f"<STMTTRN>\n{body}\n</STMTTRN>"


def _ofx(st: Stmt, card: bool, xml: bool, header: str, intu_bid: Optional[str] = None,
         acct_type: str = "CHECKING", fitid_prefix: str = "") -> bytes:
    s, e_ = st.spec.start, st.spec.end
    trns = "\n".join(_ofx_trn(st, r, card, xml, fitid_prefix) for r in st.rows)
    bid = f"\n<INTU.BID>{intu_bid}" if intu_bid else ""
    if xml:
        bid = f"<INTU.BID>{intu_bid}</INTU.BID>" if intu_bid else ""
        son = (f"<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>"
               f"<DTSERVER>{_ofx_dt(e_)}</DTSERVER><LANGUAGE>ENG</LANGUAGE><FI><ORG>SAMPLE CREDIT UNION</ORG>"
               f"<FID>99001</FID></FI>{bid}</SONRS></SIGNONMSGSRSV1>")
    else:
        son = (f"<SIGNONMSGSRSV1>\n<SONRS>\n<STATUS>\n<CODE>0\n<SEVERITY>INFO\n</STATUS>\n<DTSERVER>{_ofx_dt(e_)}\n"
               f"<LANGUAGE>ENG\n<FI>\n<ORG>{st.acct.institution.upper()}\n<FID>99001\n</FI>{bid}\n</SONRS>\n"
               f"</SIGNONMSGSRSV1>")
    acctid = "00000000" + st.acct.last4
    if card:
        acct = f"<CCACCTFROM><ACCTID>{acctid}</ACCTID></CCACCTFROM>" if xml else \
            f"<CCACCTFROM>\n<ACCTID>{acctid}\n</CCACCTFROM>"
        wrap_o, wrap_c, rs_o, rs_c = "CREDITCARDMSGSRSV1", "CREDITCARDMSGSRSV1", "CCSTMTTRNRS", "CCSTMTRS"
    else:
        acct = (f"<BANKACCTFROM><BANKID>111000025</BANKID><ACCTID>{acctid}</ACCTID><ACCTTYPE>{acct_type}</ACCTTYPE>"
                f"</BANKACCTFROM>") if xml else \
            f"<BANKACCTFROM>\n<BANKID>111000025\n<ACCTID>{acctid}\n<ACCTTYPE>{acct_type}\n</BANKACCTFROM>"
        wrap_o, wrap_c, rs_o, rs_c = "BANKMSGSRSV1", "BANKMSGSRSV1", "STMTTRNRS", "STMTRS"
    bal = st.closing if not card else -st.closing
    nl = "" if xml else "\n"
    body = (f"<OFX>{nl}{son}{nl}<{wrap_o}>{nl}<{rs_o}>{nl}<TRNUID>1{'</TRNUID>' if xml else ''}{nl}"
            f"<STATUS>{nl}<CODE>0{'</CODE>' if xml else ''}{nl}<SEVERITY>INFO{'</SEVERITY>' if xml else ''}{nl}"
            f"</STATUS>{nl}<{rs_c}>{nl}<CURDEF>USD{'</CURDEF>' if xml else ''}{nl}{acct}{nl}<BANKTRANLIST>{nl}"
            f"<DTSTART>{_ofx_dt(s)}{'</DTSTART>' if xml else ''}{nl}<DTEND>{_ofx_dt(e_)}{'</DTEND>' if xml else ''}"
            f"\n{trns}\n</BANKTRANLIST>{nl}<LEDGERBAL>{nl}<BALAMT>{f2(bal)}{'</BALAMT>' if xml else ''}{nl}"
            f"<DTASOF>{_ofx_dt(e_)}{'</DTASOF>' if xml else ''}{nl}</LEDGERBAL>{nl}</{rs_c}>{nl}</{rs_o}>{nl}"
            f"</{wrap_c}>{nl}</OFX>\n")
    return (header + body).encode("ascii", "replace")


_SGML_HEADER = ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\nCHARSET:1252\n"
                "COMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n\n")
_XML_HEADER = ('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
               '<?OFX OFXHEADER="200" VERSION="220" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>\n')


def w_ofx_sgml_bank(st: Stmt) -> bytes:
    return _ofx(st, card=False, xml=False, header=_SGML_HEADER)


def w_ofx_xml_card(st: Stmt) -> bytes:
    return _ofx(st, card=True, xml=True, header=_XML_HEADER)


def w_qbo_card(st: Stmt) -> bytes:
    return _ofx(st, card=True, xml=False, header=_SGML_HEADER, intu_bid="99999")


def w_qfx_savings(st: Stmt) -> bytes:
    # the bank's FITIDs are stable across downloads, so overlapping files repeat them
    return _ofx(st, card=False, xml=False, header=_SGML_HEADER, intu_bid="99998", acct_type="SAVINGS",
                fitid_prefix="SV")


def _qif(st: Stmt, typ: str, quicken_dates: bool) -> bytes:
    lines = ["!Option:AutoSwitch", "!Account", f"N{st.acct.label}", "TBank" if typ == "Bank" else "TCCard", "^",
             "!Clear:AutoSwitch", f"!Type:{typ}"]
    for r in st.rows:
        e = r.ev
        amt = e.amount if e.direction == "in" else -e.amount
        d = f"{e.date.month}/{e.date.day:2d}'{e.date.strftime('%y')}" if quicken_dates else _md(e.date)
        lines += [f"D{d}", f"T{amt:,.2f}"]
        if e.check_no:
            lines.append(f"N{e.check_no}")
        lines.append(f"P{r.description}")
        if e.kind == "transfer":
            lines.append("L[Savings]")
        lines.append("^")
    return ("\r\n".join(lines) + "\r\n").encode("latin-1", "replace")


def w_qif_bank(st: Stmt) -> bytes:
    return _qif(st, "Bank", True)


def w_qif_ccard(st: Stmt) -> bytes:
    return _qif(st, "CCard", False)


_ZIP_TIME = (2025, 1, 1, 0, 0, 0)


def _deterministic_zip(data: bytes) -> bytes:
    """Rewrite a zip with fixed timestamps and order (openpyxl stamps the current time)."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in src.namelist():
            body = src.read(name)
            if name == "docProps/core.xml":
                import re
                body = re.sub(rb"<dcterms:(created|modified)([^>]*)>[^<]*</dcterms:\1>",
                              rb"<dcterms:\1\2>2025-04-01T00:00:00Z</dcterms:\1>", body)
            zi = zipfile.ZipInfo(name, _ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            z.writestr(zi, body)
    return out.getvalue()


def w_xlsx_split(st: Stmt) -> bytes:
    import datetime as _dt

    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = "Summary"
    wb.active.append([])                         # an empty first sheet
    ws = wb.create_sheet("Activity")
    ws.append(["Sample Community Bank - Account Activity"])
    ws.append(["Account", "Business Checking x2468"])
    ws.append(["Period", f"{_md(st.spec.start)} - {_md(st.spec.end)}"])
    ws.append([])
    ws.append(["Posting Date", "Description", "Debit", "Credit", "Balance"])
    bal = st.opening
    for r in st.rows:
        e = r.ev
        bal += _signed(st.acct, e)
        ws.append([_dt.datetime(e.date.year, e.date.month, e.date.day), r.description,
                   float(e.amount) if e.direction == "out" else None,
                   float(e.amount) if e.direction == "in" else None, float(bal)])
    ws.append([])
    ws.append(["Total", None, float(st.total("out")), float(st.total("in")), None])
    st.noise += 1
    wb.properties.creator = "Sample Community Bank"
    wb.properties.created = wb.properties.modified = _dt.datetime(2025, 4, 1)
    buf = io.BytesIO()
    wb.save(buf)
    return _deterministic_zip(buf.getvalue())


# ------------------------------------------------------------------ PDF writers

def _wrap(s: str, width: int) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    return lines or [""]


class _Pager:
    """Top-down line placement with page breaks and running headers."""

    def __init__(self, pdf: PDF, top: float = 50, bottom: float = 740, step: float = 11) -> None:
        self.pdf, self.top, self.bottom, self.step = pdf, top, bottom, step
        self.y = top
        self.page_no = 1
        self.on_new_page: Optional[Callable[["_Pager"], None]] = None

    def need(self, lines: int) -> None:
        if self.y + lines * self.step > self.bottom:
            self.pdf.text(40, 765, f"Page {self.page_no}")
            self.pdf.new_page()
            self.page_no += 1
            self.y = self.top
            if self.on_new_page:
                self.on_new_page(self)

    def line(self, x: float, s: str) -> None:
        self.pdf.text(x, self.y, s)
        self.y += self.step

    def row(self, cells: list[tuple[str, float, str]]) -> None:
        """cells: (align 'l'|'r', x, text)."""
        for align, x, s in cells:
            if not s:
                continue
            (self.pdf.text if align == "l" else self.pdf.rtext)(x, self.y, s)
        self.y += self.step


def _wf_case(s: str) -> str:
    out = []
    for w in s.split():
        if w.isalpha() and len(w) > 2:
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out)


def _printed_lines(r: Row, width: int, fx: bool = True) -> list[str]:
    lines = _wrap(r.description, width)
    if fx and r.ev.fx:
        cur, amt, rate = r.ev.fx
        lines += [f"{cur}", f"{f2(amt)} X {rate} (EXCHG RATE)"]
    return lines


def w_pdf_sections(st: Stmt) -> bytes:
    """Chase-like checking statement: one AMOUNT column, direction from section headers."""
    pdf = PDF()
    pg = _Pager(pdf)
    s, e_ = st.spec.start, st.spec.end
    sec = {"in": [], "checks": [], "card": [], "elec": [], "fees": []}
    for r in st.rows:
        e = r.ev
        if e.direction == "in":
            sec["in"].append(r)
        elif e.kind == "check":
            sec["checks"].append(r)
        elif e.kind in ("purchase", "atm"):
            sec["card"].append(r)
        elif e.kind == "fee":
            sec["fees"].append(r)
        else:
            sec["elec"].append(r)

    def tot(rs):
        return sum((r.ev.amount for r in rs), D0)
    pg.line(40, "JPMorgan Chase Bank, N.A.")
    pg.line(40, f"{s.strftime('%B')} {s.day}, {s.year} through {e_.strftime('%B')} {e_.day}, {e_.year}")
    pg.line(40, f"Account Number: 000000{st.acct.last4.rjust(9, '0')}")
    pg.line(40, "SAMPLE DESIGN STUDIO LLC")
    pg.y += 6
    pg.line(40, "CHECKING SUMMARY Chase Business Complete Checking")
    pg.row([("l", 40, "INSTANCES"), ("r", 570, "AMOUNT")])
    pg.row([("l", 40, "Beginning Balance"), ("r", 570, "$" + fm(st.opening))])
    for label, key, sign in (("Deposits and Additions", "in", ""), ("Checks Paid", "checks", "-"),
                             ("ATM & Debit Card Withdrawals", "card", "-"), ("Electronic Withdrawals", "elec", "-"),
                             ("Fees", "fees", "-")):
        if sec[key]:
            pg.row([("l", 40, f"{label} {len(sec[key])}"), ("r", 570, sign + fm(tot(sec[key])))])
    pg.row([("l", 40, f"Ending Balance {len(st.rows)}"), ("r", 570, "$" + fm(st.closing))])
    pg.y += 8
    current = {"title": None}

    def cont(p):
        if current["title"]:
            p.line(40, f"{current['title']} (continued)")
            p.row([("l", 40, "DATE"), ("l", 80, "DESCRIPTION"), ("r", 570, "AMOUNT")])
    pg.on_new_page = cont
    for title, key in (("DEPOSITS AND ADDITIONS", "in"), ("CHECKS PAID", "checks"),
                       ("ATM & DEBIT CARD WITHDRAWALS", "card"), ("ELECTRONIC WITHDRAWALS", "elec"),
                       ("FEES", "fees")):
        rs = sec[key]
        if not rs:
            continue
        pg.need(4)
        current["title"] = title
        pg.line(40, f"*start*{title.lower()}")
        pg.line(40, title)
        if key == "checks":
            pg.row([("l", 40, "CHECK NO."), ("l", 110, "DESCRIPTION"), ("l", 300, "DATE PAID"), ("r", 570, "AMOUNT")])
        else:
            pg.row([("l", 40, "DATE"), ("l", 80, "DESCRIPTION"), ("r", 570, "AMOUNT")])
        for r in rs:
            e = r.ev
            if key == "checks":
                pg.need(1)
                pg.row([("l", 40, e.check_no), ("l", 70, "^"), ("l", 80, e.date.strftime("%m/%d")),
                        ("r", 570, "$" + fm(e.amount))])
                continue
            lines = _printed_lines(r, 70)
            pg.need(len(lines))
            pg.row([("l", 40, e.date.strftime("%m/%d")), ("l", 80, lines[0]), ("r", 570, fm(e.amount))])
            for ln in lines[1:]:
                pg.row([("l", 80, ln)])
        pg.need(2)
        pg.row([("l", 40, f"Total {title.title()}"), ("r", 570, "$" + fm(tot(rs)))])
        pg.line(40, f"*end*{title.lower()}")
        current["title"] = None
        pg.y += 6
    pg.need(4)
    pg.line(40, "DAILY ENDING BALANCE")
    pg.row([("l", 40, "DATE"), ("r", 570, "AMOUNT")])
    bal = st.opening
    last = {}
    for r in st.rows:
        bal += _signed(st.acct, r.ev)
        last[r.ev.date] = bal
    for d, b in sorted(last.items()):
        pg.need(1)
        pg.row([("l", 40, d.strftime("%m/%d")), ("r", 570, ("-" if b < 0 else "") + fm(b))])
    pdf.text(40, 765, f"Page {pg.page_no}")
    return pdf.to_bytes()


def w_pdf_columns(st: Stmt) -> bytes:
    """Wells-Fargo-like: Credits / Debits / Ending daily balance columns, M/D dates, mixed case."""
    pdf = PDF()
    pg = _Pager(pdf, bottom=470)   # page 1 ends early: account notices box
    s, e_ = st.spec.start, st.spec.end
    stmt_date = f"{e_.strftime('%B')} {e_.day}, {e_.year}"
    pg.row([("l", 40, "Wells Fargo Everyday Checking"), ("l", 380, f"{stmt_date} Page 1")])
    pg.line(40, "Statement period activity summary")
    pg.line(40, f"Beginning balance on {s.month}/{s.day} ${fm(st.opening)}")
    pg.line(40, f"Deposits/Credits {fm(st.total('in'))}")
    pg.line(40, f"Withdrawals/Debits - {fm(st.total('out'))}")
    pg.line(40, f"Ending balance on {e_.month}/{e_.day} ${fm(st.closing)}")
    pg.line(40, f"Account number: 000000{st.acct.last4}")
    pg.line(40, "PAT SAMPLE")
    pg.y += 10

    def header(p, first: bool):
        p.line(40, "Transaction history" if first else "Transaction history (continued)")
        p.row([("l", 400, "Deposits/"), ("l", 460, "Withdrawals/"), ("l", 525, "Ending daily")])
        p.row([("l", 40, "Date"), ("l", 75, "Check"), ("l", 110, "Description"), ("r", 450, "Credits"),
               ("r", 512, "Debits"), ("r", 575, "balance")])
    header(pg, True)
    pg.on_new_page = lambda p: (p.row([("l", 380, f"{stmt_date} Page {p.page_no}")]), header(p, False))
    bal = st.opening
    rows = st.rows
    for i, r in enumerate(rows):
        e = r.ev
        bal += _signed(st.acct, e)
        last_of_day = i + 1 == len(rows) or rows[i + 1].ev.date != e.date
        lines = _wrap(r.description, 44)
        pg.need(len(lines))
        pg.row([("l", 40, f"{e.date.month}/{e.date.day}"), ("l", 75, e.check_no or ""), ("l", 110, lines[0]),
                ("r", 450, fm(e.amount) if e.direction == "in" else ""),
                ("r", 512, fm(e.amount) if e.direction == "out" else ""),
                ("r", 575, fm(bal) if last_of_day else "")])
        for ln in lines[1:]:
            pg.row([("l", 110, ln)])
    pg.need(3)
    pg.row([("l", 40, f"Ending balance on {e_.month}/{e_.day}"), ("r", 575, fm(st.closing))])
    pg.row([("l", 40, "Totals"), ("r", 450, "$" + fm(st.total("in"))), ("r", 512, "$" + fm(st.total("out")))])
    pg.y += 10
    pg.line(40, "The Ending Daily Balance does not reflect any pending withdrawals or holds on deposited funds.")
    pdf.text(40, 765, f"Page {pg.page_no}")
    return pdf.to_bytes()


def _card_rows(st: Stmt):
    pays = [r for r in st.rows if r.ev.direction == "in"]
    buys = [r for r in st.rows if r.ev.direction == "out" and r.ev.kind not in ("fee", "interest")]
    fees = [r for r in st.rows if r.ev.kind == "fee"]
    ints = [r for r in st.rows if r.ev.kind == "interest"]
    return pays, buys, fees, ints


def w_pdf_chase_card(st: Stmt) -> bytes:
    """Chase-card-like: account summary, PAYMENTS AND OTHER CREDITS / PURCHASE / FEES CHARGED sections,
    foreign-currency notes on continuation lines."""
    pdf = PDF()
    pg = _Pager(pdf)
    s, e_ = st.spec.start, st.spec.end
    pays, buys, fees, ints = _card_rows(st)
    tsum = lambda rs: sum((r.ev.amount for r in rs), D0)  # noqa: E731
    pg.line(40, "Ink Business Unlimited(R) Credit Card")
    pg.line(40, "ACCOUNT SUMMARY")
    pg.line(40, f"Account Number: XXXX XXXX XXXX {st.acct.last4}")
    for label, v in (("Previous Balance", "$" + fm(st.opening)), ("Payment, Credits", "-$" + fm(tsum(pays))),
                     ("Purchases", "+$" + fm(tsum(buys))), ("Cash Advances", "$0.00"), ("Balance Transfers", "$0.00"),
                     ("Fees Charged", "+$" + fm(tsum(fees))), ("Interest Charged", "+$" + fm(tsum(ints))),
                     ("New Balance", "$" + fm(st.closing))):
        pg.row([("l", 40, label), ("r", 300, v)])
    pg.line(40, f"Opening/Closing Date {s.strftime('%m/%d/%y')} - {e_.strftime('%m/%d/%y')}")
    pg.line(40, "Credit Access Line $25,000")
    pg.line(40, f"Payment Due Date {(e_ + timedelta(days=25)).strftime('%m/%d/%y')}")
    pg.line(40, f"Minimum Payment Due ${fm(max(money(35), money(st.closing * Decimal('0.02'))))}")
    pg.y += 8
    pg.line(40, "ACCOUNT ACTIVITY")
    pg.row([("l", 40, "Date of"), ("r", 570, "")])
    pg.row([("l", 40, "Transaction"), ("l", 100, "Merchant Name or Transaction Description"), ("r", 570, "$ Amount")])
    hdr = [("l", 40, "Date of Transaction"), ("l", 160, "Merchant Name or Transaction Description"),
           ("r", 570, "$ Amount")]
    pg.on_new_page = lambda p: (p.line(40, "ACCOUNT ACTIVITY (CONTINUED)"), p.row(hdr))
    for title, rs, neg in (("PAYMENTS AND OTHER CREDITS", pays, True), ("PURCHASE", buys, False),
                           ("FEES CHARGED", fees, False), ("INTEREST CHARGED", ints, False)):
        if not rs:
            continue
        pg.need(2)
        pg.line(40, title)
        for r in rs:
            lines = _printed_lines(r, 66)
            pg.need(len(lines))
            pg.row([("l", 40, r.ev.date.strftime("%m/%d")), ("l", 100, lines[0]),
                    ("r", 570, ("-" if neg else "") + fm(r.ev.amount))])
            for ln in lines[1:]:
                pg.row([("l", 100, ln)])
    pg.need(4)
    pg.row([("l", 40, "TOTAL FEES FOR THIS PERIOD"), ("r", 570, "$" + fm(tsum(fees)))])
    pg.row([("l", 40, "TOTAL INTEREST FOR THIS PERIOD"), ("r", 570, "$" + fm(tsum(ints)))])
    pg.line(40, f"{e_.year} Totals Year-to-Date")
    pg.row([("l", 40, f"Total fees charged in {e_.year}"), ("r", 570, "$" + fm(tsum(fees) + money(39)))])
    pg.row([("l", 40, f"Total interest charged in {e_.year}"), ("r", 570, "$" + fm(tsum(ints) + money(18.42)))])
    pdf.text(40, 765, f"Page {pg.page_no}")
    return pdf.to_bytes()


def w_pdf_issuer_card(st: Stmt) -> bytes:
    """Citi-like card statement: sale + post date columns, 'Standard Purchases' section."""
    pdf = PDF()
    pg = _Pager(pdf)
    s, e_ = st.spec.start, st.spec.end
    pays, buys, fees, ints = _card_rows(st)
    tsum = lambda rs: sum((r.ev.amount for r in rs), D0)  # noqa: E731
    credits = [r for r in pays if r.ev.kind == "refund"]
    payments = [r for r in pays if r.ev.kind != "refund"]
    pg.line(40, "Sample Bank Visa Signature Card")
    pg.line(40, f"Account number ending in: {st.acct.last4}")
    pg.line(40, f"Billing Period: {s.strftime('%m/%d/%y')}-{e_.strftime('%m/%d/%y')}")
    pg.line(40, "Account Summary")
    for label, v in (("Previous balance", "$" + fm(st.opening)), ("Payments", "-$" + fm(tsum(payments))),
                     ("Credits", "-$" + fm(tsum(credits))), ("Purchases", "+$" + fm(tsum(buys))),
                     ("Cash advances", "+$0.00"), ("Fees", "+$" + fm(tsum(fees))),
                     ("Interest", "+$" + fm(tsum(ints))), ("New balance", "$" + fm(st.closing))):
        pg.row([("l", 40, label), ("r", 300, v)])
    pg.line(40, "Credit Limit $15,000")
    pg.line(40, f"Minimum payment due ${fm(max(money(41), money(st.closing * Decimal('0.01'))))}")
    pg.line(40, f"Payment due date {(e_ + timedelta(days=25)).strftime('%m/%d/%y')}")
    pg.y += 8
    pg.line(40, "Account activity")
    pg.row([("l", 40, "Sale"), ("l", 80, "Post")])
    pg.row([("l", 40, "date"), ("l", 80, "date"), ("l", 130, "Description"), ("r", 570, "Amount")])
    for title, rs, neg in (("Payments, Credits and Adjustments", pays, True), ("Standard Purchases", buys, False),
                           ("Fees Charged", fees, False), ("Interest Charged", ints, False)):
        if not rs:
            continue
        pg.need(3)
        pg.line(40, title)
        for r in rs:
            lines = _wrap(r.description, 62)
            pg.need(len(lines))
            pg.row([("l", 40, r.ev.date.strftime("%m/%d")),
                    ("l", 80, (r.ev.date + timedelta(days=1)).strftime("%m/%d")), ("l", 130, lines[0]),
                    ("r", 570, ("-" if neg else "") + "$" + fm(r.ev.amount))])
            for ln in lines[1:]:
                pg.row([("l", 130, ln)])
        pg.row([("l", 40, f"Total {title.lower()}"), ("r", 570, ("-" if neg else "") + "$" + fm(tsum(rs)))])
    pg.need(3)
    pg.line(40, "Interest charge calculation")
    pg.row([("l", 40, "Purchases 24.74% (V)"), ("r", 400, "$0.00"), ("r", 570, "$0.00")])
    pdf.text(40, 765, f"Page {pg.page_no}")
    return pdf.to_bytes()


def w_pdf_robinhood(st: Stmt) -> bytes:
    """Robinhood-card-like: glued summary words, reference-number column, credits as '100.00-'."""
    pdf = PDF()
    pg = _Pager(pdf)
    e_ = st.spec.end
    pays, buys, fees, ints = _card_rows(st)
    tsum = lambda rs: sum((r.ev.amount for r in rs), D0)  # noqa: E731
    days = (st.spec.end - st.spec.start).days + 1
    pg.line(40, "ACCOUNT SUMMARY")
    pg.row([("l", 40, "CreditLimit"), ("l", 110, "$10,000.00"), ("l", 200, "NewBalance"),
            ("l", 270, "$" + fm(st.closing))])
    pg.row([("l", 40, "MinimumPaymentDue"), ("l", 150, "$" + fm(max(money(25), money(st.closing * Decimal("0.01")))))])
    pg.row([("l", 40, "StatementClosingDate"), ("l", 170, f"{e_.strftime('%B')}{e_.day:02d},{e_.year}")])
    pg.row([("l", 40, "PaymentDueDate"), ("l", 140, f"{(e_ + timedelta(days=23)).strftime('%B%d,%Y')}")])
    pg.row([("l", 40, "DaysinBillingCycle"), ("l", 160, str(days))])
    pg.row([("l", 40, "PreviousBalance"), ("l", 150, "$" + fm(st.opening))])
    pg.row([("l", 40, "-"), ("l", 48, "Payments&Credits"), ("l", 150, "$" + fm(tsum(pays))),
            ("l", 220, "datelistedabove")])
    pg.row([("l", 40, "+"), ("l", 48, "Purchases&OtherCharges"), ("l", 180, "$" + fm(tsum(buys))),
            ("l", 250, "consequences")])
    pg.row([("l", 40, "+"), ("l", 48, "FEECHARGED"), ("l", 150, "$" + fm(tsum(fees)))])
    pg.row([("l", 40, "+"), ("l", 48, "INTERESTCHARGED"), ("l", 150, "$" + fm(tsum(ints)))])
    pg.row([("l", 40, "="), ("l", 48, "NewBalance"), ("l", 150, "$" + fm(st.closing))])
    pg.y += 8
    pg.line(40, "TRANSACTIONS")
    hdr = [("l", 40, "Date"), ("l", 70, "Date"), ("l", 100, "ReferenceNumber"), ("l", 200, "TransactionDescription"),
           ("r", 560, "Amount")]
    pg.row(hdr)
    pg.on_new_page = lambda p: p.row(hdr)
    for r in st.rows:
        e = r.ev
        tail = "".join(st.rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(4))
        ref = f"2469216{st.rng.choice('ABCDEFGHJK')}{st.rng.randint(1, 9)}W4{tail}{st.rng.randint(10, 99)}"
        pg.need(1)
        pg.row([("l", 40, e.date.strftime("%m/%d")), ("l", 70, (e.date + timedelta(days=1)).strftime("%m/%d")),
                ("l", 100, ref), ("l", 200, r.description[:52]),
                ("r", 560, fm(e.amount) + ("-" if e.direction == "in" else ""))])
    pg.need(4)
    pg.line(40, f"TOTALFEESFORTHISPERIOD {fm(tsum(fees))}")
    pg.line(40, f"TOTALINTERESTFORTHISPERIOD {fm(tsum(ints))}")
    pg.line(40, "Robinhood Credit, Inc. 85 Sample Street")
    pdf.text(40, 765, f"Page {pg.page_no}")
    return pdf.to_bytes()


def w_pdf_balance(st: Stmt) -> bytes:
    """Small credit-union savings statement: unsigned amounts + running balance column."""
    pdf = PDF()
    pg = _Pager(pdf)
    s, e_ = st.spec.start, st.spec.end
    pg.line(40, "Hill Country Credit Union - Share Savings")
    pg.line(40, f"Statement period {_md(s)} to {_md(e_)}")
    pg.line(40, f"Account number: 0000{st.acct.last4}")
    pg.line(40, f"Beginning balance ${fm(st.opening)}")
    pg.y += 8
    pg.row([("l", 40, "Date"), ("l", 90, "Description"), ("r", 480, "Amount"), ("r", 570, "Balance")])
    bal = st.opening
    for r in st.rows:
        bal += _signed(st.acct, r.ev)
        pg.row([("l", 40, r.ev.date.strftime("%m/%d")), ("l", 90, r.description[:60]), ("r", 480, fm(r.ev.amount)),
                ("r", 570, fm(bal))])
    pg.y += 12
    pg.line(40, f"Ending balance ${fm(st.closing)}")
    pg.line(40, "Dividends are paid monthly on balances over $5.00.")
    return pdf.to_bytes()


WRITERS: dict[str, Callable[[Stmt], bytes]] = {
    "csv:chase_card": w_chase_card, "csv:chase_checking": w_chase_checking, "csv:citi": w_citi,
    "csv:capital_one_card": w_capital_one_card, "csv:us_bank": w_us_bank, "csv:capital_one_360": w_capital_one_360,
    "csv:gemini": w_gemini, "csv:apple_card": w_apple_card, "csv:robinhood_card": w_robinhood_card,
    "csv:discover": w_discover, "csv:boa_card": w_boa_card, "csv:boa_checking": w_boa_checking,
    "csv:amex_extended": w_amex_extended, "csv:wells_fargo": w_wells_fargo, "csv:generic": w_generic,
    "csv:schwab": w_schwab, "csv:mercury": w_mercury,
    "ofx:sgml_bank": w_ofx_sgml_bank, "ofx:xml_card": w_ofx_xml_card, "ofx:qbo_card": w_qbo_card,
    "ofx:qfx_savings": w_qfx_savings, "qif:bank": w_qif_bank, "qif:ccard": w_qif_ccard, "xlsx:split": w_xlsx_split,
    "pdf:sections": w_pdf_sections, "pdf:columns": w_pdf_columns, "pdf:chase_card": w_pdf_chase_card,
    "pdf:issuer_card": w_pdf_issuer_card, "pdf:robinhood": w_pdf_robinhood, "pdf:balance": w_pdf_balance,
}
_PDF_WRAP = {"pdf:sections": 70, "pdf:columns": 44, "pdf:chase_card": 66, "pdf:issuer_card": 62}
FAMILY = {"csv": "CSV", "ofx": "OFX", "qif": "QIF", "xlsx": "XLSX", "pdf": "PDF"}


def _printed_description(spec: FileSpec, e: Event) -> str:
    """What this file prints as the row's description (the truth for that row)."""
    if spec.fmt == "pdf:columns":
        return _wf_case(e.description)
    if spec.fmt == "pdf:sections" and e.kind == "check":
        return ""
    if spec.fmt == "pdf:robinhood":
        return e.description[:52]
    if spec.fmt == "pdf:balance":
        return e.description[:60]
    if spec.fmt == "csv:robinhood_card":
        return " ".join(w.capitalize() for w in e.merchant.split()[:3]) if e.kind not in ("card_payment",) \
            else e.description
    return e.description


# --------------------------------------------------------------------------- build

def build_corpus(out_dir, seed: int = 2025) -> dict:
    """Write the corpus files + ``truth.json`` to ``out_dir``; return the truth dict."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    events = _Gen(seed).run()
    by_acct: dict[str, list[Event]] = {}
    for e in events:
        by_acct.setdefault(e.account, []).append(e)
    owned: set[str] = set()
    files_meta, rows_meta = [], []
    used_events: set[str] = set()
    for fi, spec in enumerate(FILES):
        acct = ACCT[spec.account]
        evs = [e for e in by_acct.get(spec.account, []) if spec.start <= e.date <= spec.end]
        rows = []
        for e in evs:
            rid = f"f{fi + 1:02d}r{len(rows) + 1:04d}"
            rows.append(Row(rid, e, _printed_description(spec, e), e.id in owned))
            owned.add(e.id)
            used_events.add(e.id)
        allev = by_acct.get(spec.account, [])
        st = Stmt(spec, acct, rows, _balance_after(acct, allev, spec.start, False),
                  _balance_after(acct, allev, spec.end, True),
                  random.Random(int(hashlib.sha256(f"{seed}|{spec.name}".encode()).hexdigest()[:12], 16)))
        data = WRITERS[spec.fmt](st)
        (out / spec.name).write_bytes(data)
        fam, sub = spec.fmt.split(":")
        files_meta.append({
            "file": spec.name, "format": spec.fmt, "family": FAMILY[fam], "account": spec.account,
            "account_label": acct.label, "account_kind": acct.kind, "institution": acct.institution,
            "period": [spec.start.isoformat(), spec.end.isoformat()],
            "opening": f2(st.opening), "closing": f2(st.closing), "rows": len(rows), "noise_rows": st.noise,
            "sha256": hashlib.sha256(data).hexdigest(),
        })
        for r in rows:
            e = r.ev
            tags = list(e.tags)
            if r.duplicate:
                tags.append("duplicate")
            width = _PDF_WRAP.get(spec.fmt)
            if width and (len(_wrap(r.description, width)) > 1
                          or (e.fx and spec.fmt in ("pdf:sections", "pdf:chase_card"))):
                tags.append("multiline")
            rows_meta.append({
                "id": r.id, "file": spec.name, "event": e.id, "account": e.account, "date": e.date.isoformat(),
                "amount": f2(e.amount), "direction": e.direction, "description": r.description,
                "category": e.category, "lane": truth_lane(e.category), "schedule_c": truth_schedule_c(e.category),
                "kind": e.kind, "tags": sorted(set(tags)), "duplicate": r.duplicate,
            })
    ev_by_id = {e.id: e for e in events}
    transfer_pairs = sorted({tuple(sorted((e.id, e.pair))) for e in events
                             if e.pair and e.id in used_events and e.pair in used_events})
    refund_pairs = sorted((e.id, e.refund_of) for e in events
                          if e.refund_of and e.id in used_events and e.refund_of in used_events)
    truth = {
        "corpus_version": CORPUS_VERSION, "seed": seed, "synthetic": True,
        "note": "SYNTHETIC benchmark corpus. Every name, merchant, account number and amount is fabricated.",
        "accounts": [{"key": a.key, "institution": a.institution, "kind": a.kind, "last4": a.last4,
                      "label": a.label, "persona": a.persona} for a in ACCOUNTS],
        "files": files_meta,
        "rows": rows_meta,
        "pairs": {
            "transfer": [list(p) for p in transfer_pairs],
            "transfer_kind": {f"{a}|{b}": ev_by_id[a].kind for a, b in transfer_pairs},
            "refund": [list(p) for p in refund_pairs],
        },
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=1, sort_keys=True) + "\n")
    return truth


if __name__ == "__main__":  # pragma: no cover
    import sys
    t = build_corpus(sys.argv[1] if len(sys.argv) > 1 else "bench-corpus")
    print(f"{len(t['files'])} files, {len(t['rows'])} truth rows")
