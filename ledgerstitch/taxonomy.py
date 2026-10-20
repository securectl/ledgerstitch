# SPDX-License-Identifier: AGPL-3.0-only
"""Default category taxonomy, lanes and IRS Schedule C (Form 1040) line mapping.

Users can add categories; the AI may *propose* new ones (see ai.categorizer),
but every category must resolve to a lane and, for business, a Schedule C line.

Tax review notes (see docs/TAX_NOTES.md for the full reasoning):
  * Anything commonly mixed personal/business (travel, phone/internet,
    vehicle, home improvement stores, insurance, taxes, professional fees,
    loans) defaults to the REVIEW lane so a human decides.
  * Keywords are matched by ai.rules.match_heuristic: longest keyword wins,
    a leading word boundary is always enforced, and keywords of <=4 chars or
    written with a trailing space must also end on a word boundary. Generic
    words that are prefixes of unrelated merchants ("WATER" -> "WATERMELON
    CAFE", "METRO" -> "METROPCS", "STEAM" -> "STEAM CLEANING") are avoided.
  * Federal/state *income* tax payments are never Schedule C expenses.
  * Health insurance is never line 15 (it is a Schedule 1 adjustment).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Lane

# Schedule C (Form 1040) 2025 line numbers, in form order. Names follow the
# printed form. L4 is Part I line 4, which comes from Part III line 42 (COGS).
# Line 30 (business use of home, Form 8829) is intentionally absent: it is
# computed by the preparer, not mapped from bank transactions.
SCHEDULE_C_LINES: dict[str, str] = {
    "L4": "Cost of goods sold (from Part III, line 42)",
    "L8": "Advertising",
    "L9": "Car and truck expenses",
    "L10": "Commissions and fees",
    "L11": "Contract labor",
    "L12": "Depletion",
    "L13": "Depreciation and section 179 expense deduction",
    "L14": "Employee benefit programs (other than on line 19)",
    "L15": "Insurance (other than health)",
    "L16a": "Interest: Mortgage (paid to banks, etc.)",
    "L16b": "Interest: Other",
    "L17": "Legal and professional services",
    "L18": "Office expense",
    "L19": "Pension and profit-sharing plans",
    "L20a": "Rent or lease: Vehicles, machinery, and equipment",
    "L20b": "Rent or lease: Other business property",
    "L21": "Repairs and maintenance",
    "L22": "Supplies (not included in Part III)",
    "L23": "Taxes and licenses",
    "L24a": "Travel",
    "L24b": "Deductible meals",
    "L25": "Utilities",
    "L26": "Wages (less employment credits)",
    "L27a": "Other expenses (from line 48)",
    "L27b": "Energy efficient commercial buildings deduction (Form 7205)",
}


@dataclass(frozen=True)
class Category:
    name: str
    lane: Lane                      # default lane for this category
    schedule_c: Optional[str] = None  # used when the transaction is in the business lane
    keywords: tuple[str, ...] = ()  # built-in heuristic substrings (merchant, upper-case)


DEFAULT_CATEGORIES: list[Category] = [
    # --- typically business ---
    Category("Advertising & Marketing", Lane.BUSINESS, "L8",
             ("FACEBK", "META ADS", "GOOGLE ADS", "GOOGLE *ADS", "GOOGLE*ADS", "LINKEDIN ADS",
              "TIKTOK ADS", "PINTEREST ADS", "NEXTDOOR ADS", "YELP ADS", "MAILCHIMP",
              "CONSTANT CONTACT", "VISTAPRINT", "MOO.COM")),
    Category("Software & Subscriptions", Lane.BUSINESS, "L18",
             ("ADOBE", "MICROSOFT*365", "MICROSOFT 365", "MICROSOFT*OFFICE", "MICROSOFT",
              "GITHUB", "ATLASSIAN", "SLACK", "ZOOM.US", "ZOOM VIDEO", "NOTION", "DROPBOX",
              "OPENAI", "ANTHROPIC", "CLAUDE.AI", "CHATGPT", "INTUIT *QBOOKS", "QBOOKS",
              "QUICKBOOKS", "CANVA", "1PASSWORD", "GOOGLE *GSUITE", "GOOGLE*GSUITE",
              "GOOGLE *WORKSPACE", "GOOGLE*WORKSPACE", "DOCUSIGN", "CALENDLY", "HUBSPOT",
              "SHOPIFY*")),
    Category("Cloud & Hosting", Lane.BUSINESS, "L18",
             ("AMAZON WEB SERVICES", "AWS", "GOOGLE*CLOUD", "GOOGLE CLOUD", "AZURE", "DIGITALOCEAN",
              "HOSTINGER", "RENDER.COM", "HEROKU", "VERCEL", "NETLIFY", "CLOUDFLARE",
              "GODADDY", "NAMECHEAP", "SQUARESPACE", "WIX.COM")),
    Category("Office Supplies", Lane.BUSINESS, "L18", ("STAPLES", "OFFICE DEPOT", "OFFICEMAX")),
    Category("Equipment & Supplies", Lane.BUSINESS, "L22", ("RESTAURANT APPLI", "ULINE", "GRAINGER")),
    Category("Inventory & Cost of Goods", Lane.BUSINESS, "L4",
             ("RESTAURANT DEPOT", "ALIBABA", "SYSCO", "US FOODS")),
    Category("Contractor Payments", Lane.BUSINESS, "L11", ("UPWORK", "FIVERR", "GUSTO CONTRACTOR")),
    # REVIEW: for most sole props an "ADP"/"GUSTO" line is their own (or a
    # spouse's) W-2 paycheck, not wages they paid. Employers confirm once + rule.
    Category("Payroll & Wages", Lane.REVIEW, "L26",
             ("ONLINE PAYROLL", "PAYROLL PAYMENT", "GUSTO", "ADP ", "PAYCHEX", "INTUIT PAYROLL", "QUICKBOOKS PAYROLL")),
    # REVIEW: personal legal/tax-prep fees are not deductible; business ones are.
    Category("Professional Services", Lane.REVIEW, "L17",
             ("LEGALZOOM", "CPA ", "CPAS", "ATTORNEY", "LAW OFFICE", "LAW FIRM", "LAW GROUP",
              "ACCOUNTING", "BOOKKEEPING", "TURBOTAX", "H&R BLOCK", "HRB TAX", "TAXACT",
              "FREETAXUSA")),
    Category("Payment Processing Fees", Lane.BUSINESS, "L10",
             ("STRIPE FEE", "SQUARE FEE", "PAYPAL FEE", "MERCHANT FEE", "MERCHANT SERVICE",
              "MERCHANT DISCOUNT")),
    # REVIEW: fees on a personal account are not deductible.
    Category("Bank & Merchant Fees", Lane.REVIEW, "L27a",
             ("MEMBER FEE", "ACH PMNTS INITIAL FEE", "ACH FEE", "SERVICE FEE", "MONTHLY SERVICE FEE", "MAINTENANCE FEE", "WIRE TRANS SVC", "WIRE FEE",
              "OVERDRAFT", "NSF FEE", "FOREIGN TRANSACTION FEE", "ANNUAL MEMBERSHIP FEE",
              "ANNUAL FEE", "LATE FEE")),
    # REVIEW: interest on a personal card/loan is not deductible.
    Category("Interest Expense", Lane.REVIEW, "L16b", ("INTEREST CHARGE", "PURCHASE INTEREST")),
    Category("Freight & Shipping", Lane.BUSINESS, "L27a",
             ("USPS", "FEDEX", "UPS ", "UPS*", "UPS.COM", "UPS STORE", "DHL", "SHIPSTATION",
              "PIRATE SHIP")),
    Category("Telecommunications", Lane.REVIEW, "L25",
             ("AT&T", "ATT*", "VERIZON", "T-MOBILE", "TMOBILE", "METROPCS", "COMCAST", "XFINITY",
              "SPECTRUM", "MINT MOBILE", "GOOGLE FI", "CRICKET WIRELESS")),
    Category("Business Travel", Lane.REVIEW, "L24a",
             ("TURKISH AIR", "THY ", "EMIRATES", "QATAR AIR", "BRITISH AIR", "LUFTHANSA", "AIR CANADA", "AMERICAN AIR", "DELTA AIR", "UNITED AIR", "SOUTHWEST", "SOUTHWES ", "SPIRIT AIR",
              "JETBLUE", "ALASKA AIR", "FRONTIER AIR", "AMTRAK", "MARRIOTT", "HILTON", "HYATT",
              "HOLIDAY INN", "HAMPTON INN", "BEST WESTERN", "IHG", "AIRBNB", "VRBO",
              "BOOKING.COM", "HOTELS.COM", "EXPEDIA", "HERTZ", "ENTERPRISE RENT", "AVIS",
              "BUDGET RENT", "NATIONAL CAR", "SIXT", "TURO")),
    Category("Business Meals", Lane.REVIEW, "L24b", ()),
    Category("Education & Training", Lane.BUSINESS, "L27a",
             ("UDEMY", "COURSERA", "KODEKLOUD", "PLURALSIGHT", "LINKEDIN LEARNING")),
    Category("Rent & Lease", Lane.REVIEW, "L20b", ()),
    Category("Repairs & Maintenance", Lane.REVIEW, "L21", ()),
    # REVIEW: DMV registration is usually personal; sales/franchise/payroll tax
    # deposits may be business. Federal income tax lives in "Income Tax Payments".
    Category("Taxes & Licenses", Lane.REVIEW, "L23",
             ("USCIS", "COMPTROLLER", "SECRETARY OF STATE", "DMV", "EFTPS", "FRANCHISE TAX",
              "DEPT OF REVENUE", "DEPT REVENUE", "BUSINESS LICENSE")),
    Category("Income Tax Payments", Lane.PERSONAL, None,
             ("IRS USATAXPYMT", "USATAXPYMT", "IRS TREAS", "IRS ")),
    Category("Health Insurance", Lane.PERSONAL, None,
             ("BLUE CROSS", "BCBS", "AETNA", "CIGNA", "UNITEDHEALTH", "UHC ", "HUMANA", "KAISER",
              "OSCAR HEALTH", "AMBETTER", "HEALTH INSURANCE")),
    Category("Insurance", Lane.REVIEW, "L15",
             ("GEICO", "STATE FARM", "PROGRESSIVE", "ALLSTATE", "AAA TX INSURANCE", "HISCOX",
              "NEXT INSURANCE", "INSURANCE")),
    Category("Vehicle & Fuel", Lane.REVIEW, "L9",
             ("SHELL ", "SHELL OIL", "SHELL SERVICE", "EXXON", "CHEVRON", "TEXACO", "VALERO",
              "QUIKTRIP", "RACETRAC", "MURPHY USA", "BUC-EE", "7-ELEVEN FUEL", "SUNOCO",
              "PHILLIPS 66", "MARATHON PETRO", "CIRCLE K", "SHEETZ", "SPEEDWAY", "WAWA",
              "COSTCO GAS", "KROGER FUEL", "BP ", "BP#", "JIFFY LUBE", "VALVOLINE", "AUTOZONE",
              "O'REILLY AUTO", "OREILLY AUTO", "ADVANCE AUTO", "DISCOUNT TIRE", "FIRESTONE",
              "PEP BOYS", "CAR WASH")),
    # Parking & tolls are deductible on top of the standard mileage rate.
    Category("Parking & Tolls", Lane.REVIEW, "L9",
             ("PARKING", "PARKMOBILE", "SPOTHERO", "TOLL ", "TOLLWAY", "TOLL ROAD", "NTTA",
              "TXTAG", "E-ZPASS", "EZPASS", "SUNPASS", "FASTRAK", "PEACH PASS")),
    Category("Utilities", Lane.REVIEW, "L25",
             ("UTILITY", "UTILITIES", "OCTOPUS ENERGY", "WATER UTIL", "CITY OF", "ATMOS", "TXU", "RELIANT ENERGY", "COSERV", "ONCOR", "DUKE ENERGY", "DOMINION ENERGY",
              "XCEL ENERGY", "GEORGIA POWER", "ENTERGY", "CENTERPOINT", "PG&E", "CONED",
              "CON EDISON", "NATIONAL GRID", "PSEG", "SOUTHWEST GAS", "WATER UTIL", "WATER DEPT",
              "WATER DIST", "ELECTRIC CO", "ELECTRIC UTIL")),
    Category("Wire & Vendor Payments", Lane.REVIEW, "L27a", ("WT ", "ONLINE ACH PAYMENT", "ACH PMT TO", "WIRE TRANS", "WT FED", "INTL WIRE")),
    Category("Zelle & P2P Payments", Lane.REVIEW, None, ("ZELLE", "VENMO", "CASH APP", "PAYPAL *")),
    # Principal is never deductible; only the interest part (L16a/L16b) may be.
    Category("Loan & Mortgage Payments", Lane.REVIEW, None,
             ("LOAN PMT", "LOAN PAYMENT", "LOAN PYMT", "MORTGAGE", "MTG PYMT", "SBA EIDL",
              "SHOPIFY CAPITAL", "SALLIE MAE", "NAVIENT", "NELNET", "MOHELA", "AIDVANTAGE",
              "AUTO LOAN", "TOYOTA FINANCIAL", "HONDA FINANCIAL", "FORD CREDIT", "ALLY AUTO")),
    # --- typically personal ---
    Category("Groceries", Lane.PERSONAL, None,
             ("KROGER", "WALMART", "WAL-MART", "WM SUPERCENTER", "ALDI", "COSTCO", "SAMS CLUB", "SAMSCLUB",
              "TRADER JOE", "WHOLE FOODS", "WHOLEFDS", "H-E-B", "HEB ", "TOM THUMB", "SPROUTS",
              "INDIA BAZAAR", "SUBZI MANDI", "PATEL BROTHERS", "99 RANCH", "FOOD LION", "SAFEWAY",
              "PUBLIX", "MEIJER", "WEGMANS", "INSTACART")),
    Category("Dining & Restaurants", Lane.PERSONAL, "L24b",
             ("RESTAURANT", "STARBUCKS", "MCDONALD", "CHICK-FIL-A", "CHIPOTLE", "DOORDASH", "UBER EATS",
              "GRUBHUB", "PIZZA", "TACO", "BURGER", "CAFE", "COFFEE", "DOMINO", "WENDYS", "WENDY'S",
              "SUBWAY", "CHURCH'S", "CHURCHS CHICKEN", "TST*")),
    Category("Shopping", Lane.PERSONAL, "L22",
             ("MICRO CENTER", "NEBRASKA FURNITURE", "WAYFAIR", "COSTCO.COM", "AMAZON", "AMZN", "TARGET", "EBAY", "ETSY", "BEST BUY", "ROSS", "TJ MAXX", "MARSHALLS",
              "KOHL", "MACY", "DOLLAR TREE", "CHEWY", "IKEA")),
    # REVIEW: Home Depot/Lowe's are classic mixed-use for contractors and landlords.
    Category("Home & Garden", Lane.REVIEW, "L22", ("HOME DEPOT", "LOWES", "LOWE'S", "MENARDS", "ACE HARDWARE")),
    Category("Healthcare", Lane.PERSONAL, None,
             ("CVS", "WALGREENS", "PHARMACY", "DENTAL", "MEDICAL", "CLINIC", "HOSPITAL", "LABCORP", "QUEST DIAG")),
    Category("Entertainment & Streaming", Lane.PERSONAL, None,
             ("NETFLIX", "SPOTIFY", "HULU", "DISNEY", "HBO", "MAX.COM", "YOUTUBE", "APPLE.COM/BILL",
              "PRIME VIDEO", "AMC ", "CINEMARK", "STEAMGAMES", "STEAMPOWERED", "STEAM PURCHASE",
              "PLAYSTATION", "XBOX", "MICROSOFT*XBOX", "MICROSOFT *XBOX", "NINTENDO")),
    Category("Rideshare & Transit", Lane.REVIEW, "L24a",
             ("UBER", "LYFT", "DART GOPASS", "DART.ORG", "MTA*", "WMATA", "MBTA", "SEPTA",
              "CLIPPER", "VENTRA")),
    Category("Personal Care", Lane.PERSONAL, None,
             ("SALON", "BARBER", "SPA ", "ULTA", "SEPHORA", "PLANET FITNESS", "LA FITNESS",
              "24 HOUR FITNESS", "EQUINOX", "ORANGETHEORY", "LIFETIME FITNESS")),
    Category("Education (Personal)", Lane.PERSONAL, None, ("SCHOOL", "TUITION", "ISD ")),
    Category("Charity & Donations", Lane.PERSONAL, None,
             ("DONATION", "CHARITY", "RED CROSS", "UNICEF", "GOFUNDME", "MOSQUE", "MASJID",
              "ISLAMIC CENTER", "CHURCH", "TITHE")),
    Category("Cash Withdrawal", Lane.PERSONAL, None, ("WITHDRAWAL", "ATM WITHDRAWAL", "ATM CASH", "ATM W/D", "CASH WITHDRAWAL")),
    # --- excluded from expense totals ---
    # Only card-side "thank you" credits and issuer-named checking debits. Generic
    # "AUTOPAY" / "ONLINE PAYMENT" were removed: they also appear on real bills
    # ("GEICO AUTOPAY", "ONLINE PAYMENT TO COMCAST") and would hide expenses.
    Category("Credit Card Payment", Lane.TRANSFER, None,
             ("PAYMENT THANK YOU", "PAYMENT - THANK YOU", "PAYMENT, THANK YOU", "AUTOMATIC PAYMENT - THANK",
              "AUTOPAY PAYMENT", "AUTOPAY AUTO-PMT", "CREDIT CRD AUTOPAY", "CRCARDPMT", "CARDMEMBER SERV",
              "PAYMENT TO CHASE CARD", "CAPITAL ONE MOBILE PMT", "CAPITAL ONE MOBILE PYMT",
              "CAPITAL ONE ONLINE PMT", "CAPITAL ONE AUTOPAY", "CITI AUTOPAY", "CITI CARD ONLINE",
              "AMEX EPAYMENT", "DISCOVER E-PAYMENT", "APPLECARD GSBANK", "DIRECTPAY FULL BALANCE")),
    Category("Transfer Between Accounts", Lane.TRANSFER, None,
             ("ONLINE TRANSFER", "ONLINE REALTIME TRANSFER", "TRANSFER TO", "TRANSFER FROM", "BOOK TRANSFER")),
    Category("Investments & Retirement", Lane.TRANSFER, None,
             ("VANGUARD", "FIDELITY", "SCHWAB", "ROBINHOOD", "COINBASE", "BETTERMENT", "WEALTHFRONT",
              "E*TRADE", "ETRADE", "ACORNS")),
    Category("Refund", Lane.TRANSFER, None, ()),
    Category("Business Income (Sales & Payouts)", Lane.INCOME, None,
             ("STRIPE TRANSFER", "STRIPE PAYOUT", "SQUARE INC", "SHOPIFY PAYOUT")),
    Category("Paycheck (W-2 Wages)", Lane.INCOME, None, ("PAYROLL", "DIRECT DEP", "DIR DEP")),
    Category("Interest Income", Lane.INCOME, None, ("INTEREST PAID", "INTEREST EARNED", "INTEREST PAYMENT")),
    Category("Income & Deposits", Lane.INCOME, None, ("DEPOSIT",)),
    Category("Checks Written", Lane.REVIEW, "L27a", ("CHECK ", "CHECK #", "CHECK NO")),
    Category("Uncategorized", Lane.REVIEW, None, ()),
]

CATEGORY_BY_NAME: dict[str, Category] = {c.name: c for c in DEFAULT_CATEGORIES}

UNCATEGORIZED = "Uncategorized"

# Lanes whose amounts are excluded from the expense report totals.
NON_EXPENSE_LANES = {Lane.TRANSFER, Lane.INCOME}


def lane_for(category: str) -> Lane:
    c = CATEGORY_BY_NAME.get(category)
    return c.lane if c else Lane.REVIEW


def schedule_c_for(category: str) -> Optional[str]:
    c = CATEGORY_BY_NAME.get(category)
    return c.schedule_c if c else None
