# SPDX-License-Identifier: AGPL-3.0-only
"""Descriptor redaction - the core of LedgerStitch's AI privacy promise.

The AI provider only ever receives the output of :func:`redact` (wrapped by
:func:`payload_preview`). Policy: **when in doubt, remove.** A descriptor that
comes out less useful for categorization is an acceptable cost; a descriptor
that leaks a person's name or an account number is not.

What survives: merchant / brand words, short (<=2 digit) numbers such as
"7-ELEVEN" or "99 RANCH", domain names (path and query dropped), and the
literal placeholder ``<PERSON>``.

What is removed (``Redacted.removed`` kinds):
  * ``email``   - anything with an ``@``
  * ``phone``   - US/intl phone numbers and 1-800 vanity numbers
  * ``id``      - card/account masks (XXXX1234, ****1234, ...1234), reference /
                  confirmation / trace numbers, ACH & wire metadata fields,
                  URL paths/query strings, letter+digit tokens that look like ids
  * ``digits``  - dates, times, amounts, any token with >= 3 digits, any 4+
                  digit run anywhere (final safety net)
  * ``address`` - street addresses, PO boxes, ZIP codes, trailing city /
                  state / country words
  * ``person``  - the counter-party after ZELLE / VENMO / CASH APP / APPLE
                  CASH / PAYPAL / "TRANSFER TO|FROM" / "PAYMENT TO|FROM" /
                  "WIRE TO" / "BILL PAY" ..., replaced by ``<PERSON>``.
                  Everything after a removed person (free-text memo) is dropped.
                  The counter-party is kept only when it is clearly a business
                  (LLC/INC/CORP/CO/.COM/industry word) or made solely of known
                  account / institution / brand words.

The output is upper-case ASCII, whitespace-collapsed, and contains only
``A-Z 0-9 space & ' . - * / +`` plus the ``<PERSON>`` placeholder.
It may be the empty string when nothing safe is left; callers should not
send empty descriptors (``payload_preview`` drops them).

The function is idempotent: ``redact(redact(x).text).text == redact(x).text``.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

__all__ = ["Redacted", "redact", "redact_batch", "payload_preview", "payload_object", "PERSON",
           "mask_identifiers"]

PERSON = "<PERSON>"
_GAP = " \x07 "          # internal "something was removed here" marker


@dataclass
class Redacted:
    text: str                                   # safe descriptor sent to AI
    removed: list[str] = field(default_factory=list)   # kinds removed, first-seen order


# =========================================================================== word lists

US_STATES = set("""
AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV
NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR VI GU AS MP
""".split())

COUNTRY_TAIL = {"US", "USA", "U.S.", "U.S.A.", "UNITED STATES", "UNITED STATES OF AMERICA",
                "CAN", "CANADA", "GB", "GBR", "UK", "IRL", "IE", "NLD", "NL", "LUX", "SGP", "AUS", "IND",
                "MEX", "DEU"}

# Common US cities (and the DFW suburbs our first users live in). Stripped only
# from the end of a descriptor. Deliberately excludes words that are also brands
# or ordinary words (e.g. MOBILE, ORANGE, COLUMBIA, PHOENIX, TARGET).
US_CITIES = set("""
NEW YORK|NEW YORK CITY|NYC|BROOKLYN|BRONX|QUEENS|STATEN ISLAND|MANHATTAN|LOS ANGELES|CHICAGO|HOUSTON|
SAN ANTONIO|SAN DIEGO|DALLAS|SAN JOSE|AUSTIN|JACKSONVILLE|FORT WORTH|FT WORTH|COLUMBUS|CHARLOTTE|
SAN FRANCISCO|INDIANAPOLIS|SEATTLE|DENVER|WASHINGTON|BOSTON|EL PASO|NASHVILLE|DETROIT|OKLAHOMA CITY|
PORTLAND|LAS VEGAS|MEMPHIS|LOUISVILLE|BALTIMORE|MILWAUKEE|ALBUQUERQUE|TUCSON|FRESNO|SACRAMENTO|
KANSAS CITY|ATLANTA|OMAHA|COLORADO SPRINGS|RALEIGH|MIAMI|LONG BEACH|VIRGINIA BEACH|OAKLAND|
MINNEAPOLIS|TULSA|TAMPA|ARLINGTON|NEW ORLEANS|WICHITA|CLEVELAND|BAKERSFIELD|HONOLULU|ANAHEIM|
SANTA ANA|RIVERSIDE|CORPUS CHRISTI|LEXINGTON|STOCKTON|ST LOUIS|SAINT LOUIS|ST. LOUIS|PITTSBURGH|
SAINT PAUL|ST PAUL|CINCINNATI|ANCHORAGE|HENDERSON|GREENSBORO|PLANO|NEWARK|LINCOLN|TOLEDO|ORLANDO|
CHULA VISTA|JERSEY CITY|CHANDLER|FORT WAYNE|BUFFALO|DURHAM|ST PETERSBURG|LAREDO|LUBBOCK|MADISON|
GILBERT|NORFOLK|RENO|WINSTON SALEM|GLENDALE|HIALEAH|GARLAND|SCOTTSDALE|IRVING|CHESAPEAKE|
NORTH LAS VEGAS|FREMONT|BATON ROUGE|RICHMOND|BOISE|SAN BERNARDINO|SPOKANE|DES MOINES|TACOMA|
MODESTO|FONTANA|SANTA CLARITA|BIRMINGHAM|OXNARD|FAYETTEVILLE|ROCHESTER|MORENO VALLEY|
SALT LAKE CITY|SLC|HUNTSVILLE|AMARILLO|GRAND RAPIDS|TALLAHASSEE|KNOXVILLE|WORCESTER|PROVIDENCE|
BROWNSVILLE|SANTA CLARA|SUNNYVALE|MOUNTAIN VIEW|PALO ALTO|CUPERTINO|MENLO PARK|REDMOND|BELLEVUE|
KIRKLAND|CAMBRIDGE|BERKELEY|PASADENA|IRVINE|ANN ARBOR|BOULDER|HOBOKEN|SANTA MONICA|BURBANK|
MCKINNEY|FRISCO|ALLEN|RICHARDSON|CARROLLTON|LEWISVILLE|DENTON|MESQUITE|GRAND PRAIRIE|ROWLETT|
WYLIE|SACHSE|MURPHY|ROCKWALL|ADDISON|COPPELL|FLOWER MOUND|SOUTHLAKE|GRAPEVINE|EULESS|BEDFORD|
HURST|KELLER|MANSFIELD|PROSPER|CELINA|LITTLE ELM|THE COLONY|PROSPER|FARMERS BRANCH|DESOTO|
DUNCANVILLE|CEDAR HILL|LANCASTER|FORNEY|HEATH|ROYSE CITY|MELISSA|ANNA|PRINCETON|SHERMAN|
SUGAR LAND|KATY|THE WOODLANDS|PEARLAND|ROUND ROCK|CEDAR PARK|GEORGETOWN|PFLUGERVILLE|SAN MARCOS|
COLLEGE STATION|WACO|KILLEEN|TEMPLE|TYLER|LONGVIEW|ABILENE|MIDLAND|ODESSA|BEAUMONT|MCALLEN|
EDINBURG|SPRING|HUMBLE|CONROE|LEAGUE CITY|GALVESTON|BOCA RATON|FORT LAUDERDALE|FT LAUDERDALE|
WEST PALM BEACH|NAPLES|SARASOTA|CLEARWATER|ST PETE|TEMPE|MESA|PEORIA|SCHAUMBURG|NAPERVILLE|EVANSTON|
OAK BROOK|SECAUCUS|EDISON|STAMFORD|WHITE PLAINS|YONKERS|ALEXANDRIA|RESTON|HERNDON|MCLEAN|
TYSONS|BETHESDA|ROCKVILLE|SILVER SPRING|FAIRFAX|ASHBURN|SAN MATEO|DALY CITY|SOUTH SAN FRAN|
SOUTH SAN FRANCISCO|LOS GATOS|SAN RAFAEL|WALNUT CREEK|PLEASANTON|DUBLIN|HAYWARD|CONCORD|
""".replace("\n", "").split("|")) - {""}

_STREET_SUFFIX = (
    r"ST|STREET|RD|ROAD|AVE|AV|AVENUE|BLVD|BOULEVARD|DR|DRIVE|LN|LANE|HWY|HIGHWAY|PKWY|PARKWAY|"
    r"WAY|CT|COURT|PL|PLACE|CIR|CIRCLE|TRL|TRAIL|TER|TERRACE|PIKE|FWY|FREEWAY|EXPY|EXPRESSWAY|"
    r"LOOP|PLZ|PLAZA|SQUARE|CRES|XING|CROSSING|BYP|BYPASS|TPKE|TURNPIKE|ROW|ALY|ALLEY"
)

# A counter-party containing any of these is treated as a business, not a person.
BUSINESS_WORDS = set("""
LLC L.L.C. L.L.C INC INC. CORP CORP. CORPORATION CO CO. COMPANY LTD LTD. LLP PLLC PC P.C. LP PLC
GMBH GROUP HOLDINGS SERVICES SERVICE SVC SVCS SOLUTIONS ENTERPRISES ENTERPRISE INDUSTRIES PARTNERS
ASSOCIATES AGENCY STUDIO STUDIOS CONSULTING CONSULTANTS MANAGEMENT MGMT PROPERTIES PROPERTY
REALTY REAL ESTATE CONSTRUCTION CONTRACTORS CONTRACTING PLUMBING ROOFING ELECTRIC ELECTRICAL
ENERGY INSURANCE BANK BANCORP MORTGAGE UTILITIES UTILITY WATER GAS POWER MEDICAL DENTAL
CLINIC HOSPITAL HEALTH HEALTHCARE PHARMACY SCHOOL SCHOOLS UNIVERSITY COLLEGE ACADEMY CHURCH
MOSQUE TEMPLE FOUNDATION FUND FINANCIAL FINANCE LAW LEGAL ACCOUNTING CPA TAX TAXES IRS TREASURY
COUNTY CITY DEPT DEPARTMENT ISD HOA ASSOCIATION ASSN RESTAURANT CAFE GRILL KITCHEN MARKET
STORE STORES SHOP MOTORS AUTO AUTOMOTIVE TECH TECHNOLOGIES TECHNOLOGY SOFTWARE SYSTEMS LABS
MEDIA PRINTING LOGISTICS TRANSPORT TRANSPORTATION TRUCKING RENTALS RENTAL LEASING APARTMENTS
CLEANING LANDSCAPING HVAC SUPPLY SUPPLIES WHOLESALE INTERNATIONAL INTL GLOBAL TELECOM WIRELESS
COMMUNICATIONS CABLE INTERNET NETWORKS AIRLINES AIRWAYS AIR HOTEL HOTELS INN RESORT TRAVEL
PAYROLL SECURITIES INVESTMENTS BROKERAGE
""".split())

_TLD = (r"COM|NET|ORG|IO|CO|AI|APP|DEV|US|ME|TV|LY|GG|BIZ|INFO|XYZ|SH|SO|CC|CA|UK|DE|EDU|GOV|"
        r"SHOP|STORE|ONLINE|SITE|TECH|CLOUD|FM|TO")

# Account / institution / generic banking words: a counter-party made only of
# these is an account of the user (e.g. "TRANSFER TO EVERYDAY CHECKING").
ACCOUNT_WORDS = set("""
CHK CHECKING CHECK SAV SAVINGS SVGS ACCOUNT ACCT ACCTS ACCOUNTS CARD CARDS CREDIT DEBIT LOAN LOANS
MORTGAGE BROKERAGE IRA ROTH HSA MMA MONEY MARKET EVERYDAY WAY2SAVE PREMIER PLATINUM BUSINESS
PERSONAL JOINT TOTAL SAPPHIRE FREEDOM UNLIMITED VENTURE QUICKSILVER SAVOR GOLD BLUE CASH PREFERRED
MY YOUR OWN SHARE SHARES LINE OF HOME EQUITY HELOC AUTO STUDENT ONLINE MOBILE INTERNAL EXTERNAL
BANKING BANK MEMBER PRIMARY SECONDARY SPEND RESERVE VISA MASTERCARD MC AMEX DISCOVER CHASE CITI
CITIBANK WELLS FARGO WF BOFA BOA AMERICA AMERICAN EXPRESS CAPITAL ONE CAP US USBANK APPLE GEMINI
SYNCHRONY BARCLAYS ALLY MARCUS SCHWAB FIDELITY VANGUARD ROBINHOOD COINBASE SOFI CHIME NAVY
FEDERAL PNC TD TRUIST REGIONS HUNTINGTON SANTANDER CITIZENS FROST COMERICA PAYPAL VENMO ZELLE
STRIPE SQUARE SHOPIFY BALANCE THE A AND FOR INSTANT STANDARD TRANSFER XFER PAYMENT PMT PYMT
AUTOPAY AUTOMATIC RECURRING SCHEDULED ACH WEB PPD CCD TEL ECHECK EFT REFUND RETURN REVERSAL FEE
CASHOUT ADD ADDED MONEY SENT RECEIVED REQUEST DEPOSIT WITHDRAWAL BILL IRS TREAS USATAXPYMT
""".split())

# Words that may follow a P2P brand without being a counter-party.
_P2P_SUBWORDS = (r"PAYMENT|PMT|PYMT|TRANSFER|XFER|SENT|SEND|RECEIVED|RECV|RCVD|REQUEST|DEBIT|CREDIT|"
                 r"INST|INSTANT|STANDARD|MONEY|CASHOUT|CASH OUT|ADD|ADDED|BALANCE|ECHECK|WEB|"
                 r"TRANSACTION|TRANS|ONLINE|MOBILE")

# Merchants that commonly appear behind PAYPAL * / VENMO * and are businesses.
_P2P_BRANDS = set("""
EBAY ETSY UBER LYFT NETFLIX SPOTIFY HULU ADOBE MICROSOFT MSFT GOOGLE APPLE ITUNES STEAM STEAMGAMES
VALVE DIGITALOCEAN NAMECHEAP GODADDY HOSTINGER CLOUDFLARE WALMART BESTBUY TARGET DOORDASH GRUBHUB
INSTACART AIRBNB EXPEDIA BOOKING NINTENDO PLAYSTATION SONY XBOX DISNEY DISNEYPLUS HBO HBOMAX MAX
PATREON KICKSTARTER GOFUNDME UDEMY COURSERA FIVERR UPWORK CANVA DROPBOX ZOOM SLACK NOTION GITHUB
OPENAI ANTHROPIC ATLASSIAN SHOPIFY SQUARESPACE WIX ALIEXPRESS ALIBABA TEMU SHEIN WISH NEWEGG
VERIZON ATT TMOBILE COMCAST XFINITY SPECTRUM HOMEDEPOT LOWES COSTCO SAMSCLUB KROGER
""".split())


def _brand_words() -> set[str]:
    """Single-word brand keywords from the shared taxonomy (exact-token use only)."""
    words: set[str] = set()
    try:
        from ..taxonomy import DEFAULT_CATEGORIES
    except Exception:  # noqa: BLE001 - redaction must work standalone
        return words
    for c in DEFAULT_CATEGORIES:
        for kw in c.keywords:
            for w in re.split(r"[\s*]+", kw.upper()):
                w = w.strip(".'-&")
                if len(w) >= 3 and w.isalpha():
                    words.add(w)
    return words


KNOWN_WORDS = ACCOUNT_WORDS | _P2P_BRANDS | _brand_words()

# =========================================================================== regexes

_ACH_LABELS = [
    "ORIG CO NAME", "CO ENTRY DESCR", "ORIG ID", "DESC DATE", "SEC", "TRACE#", "TRACE NUMBER",
    "TRACE NO", "TRACE", "EED", "IND ID", "IND NAME", "INDN", "TRN", "CO ID", "DES", "ID",
    "PPD ID", "WEB ID", "CCD ID", "TEL ID", "ORIG BANK NAME", "ORIG BANK", "ORIG ABA", "ABA",
    "BNF BK", "BNF ID", "BNF NAME", "BNF", "BENEFICIARY", "BENE", "OBI", "ORG", "ORIGINATOR",
    "PMT DET", "PMT INFO", "PAYMENT INFO", "WIRE TYPE", "DATE", "TIME", "SERVICE REF", "REF", "REF#",
    "REF NO", "CONF", "CONF#", "CONFIRMATION", "FROM", "TO", "NAME", "MEMO", "INFO", "NOTE", "FOR",
    "SENDER", "RECEIVER", "RECIPIENT", "ACCT", "ACCOUNT", "TRANSACTION#", "TRANSACTION ID",
    "TRANSACTION", "AUTH", "SEQ", "IMAD", "OMAD", "SRF", "SRF#", "BBK", "IBK", "RFB", "BBI",
    "CHECK#", "CK#", "ORDER#", "INVOICE", "INV",
]
_ACH_KEEP = {"ORIG CO NAME", "CO ENTRY DESCR", "DES", "WIRE TYPE"}
_ACH_RE = re.compile(
    r"(?<![A-Z0-9])(" + "|".join(re.escape(l) for l in sorted(_ACH_LABELS, key=len, reverse=True))
    + r")\s*:"
)

_WIREISH = re.compile(r"\b(?:WT|WIRE|FEDWIRE|FED#|FED\s*REF|CHIPS|SWIFT|INTL\s+WIRE|BOOK\s+TRANSFER)\b|FED#")
_WIRE_CUT = re.compile(r"/[A-Z]{2,4}/|\b(?:BNF|BENE|OBI|ORG|BBI|RFB|BBK|IBK|B/O)\s*[=/]|\bSRF\s*#|"
                       r"\bIMAD\b|\bOMAD\b|\bFOR\s+FURTHER\s+CREDIT\b|\bFFC\b")
_ANY_CUT = re.compile(r"\b(?:BNF|BENE|OBI|ORG)\s*=")

_EMAIL_RE = re.compile(r"\S*@\S*")
_URL_RE = re.compile(r"\bHTTPS?://([A-Z0-9.-]+)(\S*)")
_DOMAIN_RE = re.compile(r"(?<![A-Z0-9@.-])((?:[A-Z0-9][A-Z0-9-]*\.)+(?:" + _TLD + r"))(?![A-Z0-9-])([/?#=&]\S*)?")
_PHONE_RE = re.compile(
    r"(?<![0-9])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s*|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![0-9])"
    r"|(?<![0-9])\+\d[\d\s.-]{7,}\d"
    r"|\b1?-?8(?:00|33|44|55|66|77|88)-[A-Z0-9]{3}-?[A-Z0-9]{4}\b"
)
_MASK_RE = re.compile(
    r"(?:\bENDING\s+IN|\bENDING|\bLAST\s*4|\bACCT|\bACCOUNT|\bCARD|\bA/C)\s*(?:NO\.?|NUM(?:BER)?|#)?\s*[:#]?\s*"
    r"[X*•.\-]*\d+[X*\d]*"
    r"|[X*•]{2,}[\s-]?\d*|\.{2,}\d+|(?<![A-Z0-9])X{2,}(?![A-Z])"
)
# US Social Security / taxpayer ids: 900-45-6789, 123 45 6789 or 123456789 after an SSN label.
_SSN_RE = re.compile(r"(?<![0-9-])\d{3}-\d{2}-\d{4}(?![0-9-])"
                     r"|\b(?:SSN|SOC(?:IAL)?\s*SEC(?:URITY)?\s*(?:NO|NUMBER|#)?|TIN|ITIN)\.?\s*[:#]?\s*\d{3}\s?\d{2}\s?\d{4}\b")
_REF_LABELS = (r"REF|REFERENCE|CONF|CONFIRMATION(?:\s+NUMBER)?|CONF\s+NUMBER|TRACE(?:\s+NUMBER)?|"
               r"TRN|TXN|TRANSACTION(?:\s+ID)?|AUTH(?:\s+CODE)?|AUTHORIZATION|APPROVAL(?:\s+CODE)?|"
               r"SEQ|SRF|IMAD|OMAD|ORDER|INVOICE|INV|RECEIPT|TICKET|CONTROL|SERIAL|CASE")
_REF_HASH_RE = re.compile(r"\b(?:" + _REF_LABELS + r")\s*(?:NO\.?|NUM(?:BER)?)?\s*[#:]\s*[^\s~]+")
_REF_PLAIN_RE = re.compile(r"\b(?:" + _REF_LABELS + r")\s+(?:NO\.?\s*|NUM(?:BER)?\s*)?(?=[A-Z0-9-]*\d)[A-Z0-9-]+")
_HASH_ID_RE = re.compile(r"#\s*[A-Z0-9-]*\d[A-Z0-9-]*")
_MON = r"JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC"
_DATE_RE = re.compile(
    r"(?:\bON\s+)?(?:"
    r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b"
    r"|\b\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?\b"
    r"|\b(?:" + _MON + r")[A-Z]*\.?\s*\d{1,2}(?:ST|ND|RD|TH)?(?:,?\s*\d{2,4})?\b"
    r"|\b\d{1,2}\s*(?:" + _MON + r")[A-Z]*(?:\s*\d{2,4})?\b"
    r")"
)
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?(?:\s+(?:ET|EST|EDT|CT|CST|CDT|PT|PST|PDT|MT|UTC|GMT))?\b")
_AMOUNT_RE = re.compile(r"[$€£]\s*\d[\d,]*(?:\.\d+)?|\b\d[\d,]*\.\d{2}\b")
_ADDR_RE = re.compile(
    r"\b\d{1,6}[A-Z]?(?:-\d+)?\s+(?:[NSEW]\.?\s+|NORTH\s+|SOUTH\s+|EAST\s+|WEST\s+)?"
    r"(?:[A-Z0-9'.-]+\s+){0,3}?(?:" + _STREET_SUFFIX + r")\b\.?"
    r"(?:\s+(?:[NSEW]|NE|NW|SE|SW)\b)?"
    r"(?:\s*,?\s*(?:STE|SUITE|APT|UNIT|BLDG|FL|#)\.?\s*[A-Z0-9-]+)?"
)
_POBOX_RE = re.compile(r"\bP\.?\s*O\.?\s*BOX\s+\S+|\bPOB\s+\S+")
_SUITE_RE = re.compile(r"\b(?:STE|SUITE|APT|UNIT)\.?\s*#?\s*[A-Z]?\d+[A-Z]?\b")
_AUTH_ON_RE = re.compile(r"\b(PURCHASE RETURN|RECURRING PAYMENT|RECURRING|PURCHASE|"
                         r"NON-[A-Z]+ ATM WITHDRAWAL|ATM WITHDRAWAL|MONEY TRANSFER|PAYMENT)?\s*AUTHORI[SZ]ED\s+ON\b")

_P2P_TRIGGER = re.compile(
    r"\b(?:"
    r"(?P<brand>ZELLE|ZEL|VENMO|CASH\s?APP|SQC|SQ\s*\*\s*CASH\s?APP|APPLE\s+CASH|PAYPAL|PYPL|CHIME)"
    r"(?:\s+(?:" + _P2P_SUBWORDS + r"))*"
    r"(?P<dir1>\s+(?:TO|FROM))?"
    r"|(?P<gen>(?:ONLINE\s+|MOBILE\s+|INTERNAL\s+|EXTERNAL\s+|BOOK\s+|ACH\s+|REAL\s?TIME\s+|INSTANT\s+)?"
    r"(?:TRANSFER|XFER|TRANSFERRED|PAYMENT|PMT|WIRE|DEPOSIT|CHECK|SENT|SEND|PAID|PAY|"
    r"RECEIVED|MONEY\s+SENT|MONEY\s+RECEIVED|REQUEST)\s+(?:TO|FROM)"
    r"|BILL\s+PAY(?:MENT)?(?:\s+TO)?|PAYABLE\s+TO|BNF|BENEFICIARY)"
    r")"
    r"(?P<sep>\s*\*\s*|\s+)"
)
# Common US first names and surnames (Census/SSA top lists, abridged). Used only to
# recognise *people* hiding behind payment-processor prefixes ("SQ *JOHN SMITH"),
# payroll ACH lines and bare-name merchants. Erring toward <PERSON> is the safe side.
FIRST_NAMES = set("""
JAMES JOHN JANE ROBERT MICHAEL WILLIAM DAVID RICHARD JOSEPH THOMAS CHARLES CHRISTOPHER DANIEL MATTHEW ANTHONY
MARK DONALD STEVEN PAUL ANDREW JOSHUA KENNETH KEVIN BRIAN GEORGE TIMOTHY RONALD EDWARD JASON JEFFREY RYAN
JACOB GARY NICHOLAS ERIC JONATHAN STEPHEN LARRY JUSTIN SCOTT BRANDON BENJAMIN SAMUEL GREGORY ALEXANDER FRANK
PATRICK RAYMOND JACK DENNIS JERRY TYLER AARON JOSE ADAM NATHAN HENRY DOUGLAS ZACHARY PETER KYLE NOAH ETHAN
JEREMY WALTER CHRISTIAN KEITH ROGER TERRY AUSTIN SEAN GERALD CARL HAROLD DYLAN ARTHUR LAWRENCE JORDAN JESSE
BRYAN BILLY BRUCE GABRIEL JOE LOGAN ALAN JUAN ALBERT WILLIE ELIJAH WAYNE RANDY VINCENT MASON ROY RALPH BOBBY
RUSSELL BRADLEY PHILIP EUGENE MARY PATRICIA JENNIFER LINDA ELIZABETH BARBARA SUSAN JESSICA SARAH KAREN LISA
NANCY BETTY SANDRA MARGARET ASHLEY KIMBERLY EMILY DONNA MICHELLE CAROL AMANDA MELISSA DEBORAH STEPHANIE DOROTHY
REBECCA SHARON LAURA CYNTHIA AMY KATHLEEN ANGELA SHIRLEY BRENDA EMMA ANNA PAMELA NICOLE SAMANTHA KATHERINE
CHRISTINE HELEN DEBRA RACHEL CAROLYN JANET MARIA CATHERINE HEATHER DIANE OLIVIA JULIE JOYCE VICTORIA RUTH
VIRGINIA LAUREN KELLY CHRISTINA JOAN EVELYN JUDITH ANDREA HANNAH MEGAN CHERYL JACQUELINE MARTHA MADISON TERESA
GLORIA SARA JANICE ANN KATHRYN ABIGAIL SOPHIA FRANCES JEAN ALICE JUDY ISABELLA JULIA GRACE AMBER DENISE DANIELLE
MARILYN BEVERLY CHARLOTTE NATALIE THERESA DIANA BRITTANY DORIS KAYLA ALEXIS LORI MARIE AVA MIA HARPER AMELIA
MOHAMMED MOHAMMAD MUHAMMAD AHMED ALI OMAR HASSAN HUSSAIN ABDUL FATIMA AISHA AYESHA KHADIJA ZAINAB IMRAN USMAN
RAJ RAHUL AMIT PRIYA ANIL SUNIL RAVI VIJAY ARJUN DEEPAK SANJAY NEHA POOJA SURESH RAMESH KRISHNA LAKSHMI
WEI LI JUN MING YAN HUI LEI JING CARLOS LUIS JORGE MIGUEL PEDRO ALEJANDRO FERNANDO RICARDO ANA ROSA CARMEN
LUCIA SOFIA GABRIELA VALENTINA DIEGO JAVIER MANUEL FRANCISCO ANTONIO JESUS ROBERTO EDUARDO SERGIO KIM HYUN
""".split())
SURNAMES = set("""
SMITH DOE JOHNSON WILLIAMS BROWN JONES GARCIA MILLER DAVIS RODRIGUEZ MARTINEZ HERNANDEZ LOPEZ GONZALEZ WILSON
ANDERSON THOMAS TAYLOR MOORE JACKSON MARTIN LEE PEREZ THOMPSON WHITE HARRIS SANCHEZ CLARK RAMIREZ LEWIS
ROBINSON WALKER YOUNG ALLEN KING WRIGHT SCOTT TORRES NGUYEN HILL FLORES GREEN ADAMS NELSON BAKER HALL RIVERA
CAMPBELL MITCHELL CARTER ROBERTS GOMEZ PHILLIPS EVANS TURNER DIAZ PARKER CRUZ EDWARDS COLLINS REYES STEWART
MORRIS MORALES MURPHY COOK ROGERS GUTIERREZ ORTIZ MORGAN COOPER PETERSON BAILEY REED KELLY HOWARD RAMOS KIM
COX WARD RICHARDSON WATSON BROOKS CHAVEZ WOOD JAMES BENNETT GRAY MENDOZA RUIZ HUGHES PRICE ALVAREZ CASTILLO
SANDERS PATEL MYERS LONG ROSS FOSTER JIMENEZ POWELL JENKINS PERRY RUSSELL SULLIVAN BELL COLEMAN BUTLER
HENDERSON BARNES GONZALES FISHER VASQUEZ SIMMONS ROMERO JORDAN PATTERSON ALEXANDER HAMILTON GRAHAM REYNOLDS
GRIFFIN WALLACE MORENO WEST COLE HAYES BRYANT HERRERA GIBSON ELLIS TRAN MEDINA AGUILAR STEVENS MURRAY FORD
CASTRO MARSHALL OWENS HARRISON FERNANDEZ MCDONALD WOODS WASHINGTON KENNEDY WELLS VARGAS HENRY CHEN FREEMAN
WEBB TUCKER GUZMAN BURNS CRAWFORD OLSON SIMPSON PORTER HUNTER GORDON MENDEZ SILVA SHAW SNYDER MASON DIXON
KHAN AHMED ALI HUSSAIN SHAH SINGH KUMAR SHARMA GUPTA REDDY RAO IYER MEHTA DESAI JOSHI WANG ZHANG LIU YANG
HUANG ZHAO WU ZHOU XU SUN MA ZHU HU GUO PARK CHOI JUNG KANG CHO YOON SATO SUZUKI TANAKA WATANABE
""".split())
# Merchants whose names are (or start with) a person's name.
BRANDS_NAMED_LIKE_PEOPLE = {
    "JOHN DEERE", "PAPA JOHNS", "PAPA JOHN'S", "JIMMY JOHNS", "JIMMY JOHN'S", "TRADER JOE", "TRADER JOES",
    "BEN JERRYS", "WENDY'S", "WENDYS", "DAVID'S BRIDAL", "MARIE CALLENDERS", "KATE SPADE", "CALVIN KLEIN",
    "TOMMY HILFIGER", "RALPH LAUREN", "MICHAEL KORS", "TORY BURCH", "MARTHA STEWART", "BOB EVANS",
    "STEAK SHAKE", "PETER PIPER", "JACK BOX", "JACK IN", "LONG JOHN", "JOHN LEWIS", "JOHNNY ROCKETS",
    "CHARLES SCHWAB", "EDWARD JONES", "MORGAN STANLEY", "WELLS FARGO", "HARRIS TEETER", "TJ MAXX",
}
_PROCESSOR_RE = re.compile(r"^(SQ ?\*|SQUARE ?\*|TST ?\*|IN ?\*|SP ?\*|PP ?\*|FSP ?\*|PY ?\*|SQU ?\*)\s*(.+)$")
_PAYROLL_NAME_RE = re.compile(
    r"\b(ACH (?:CREDIT|DEBIT|DEP(?:OSIT)?|PMT)|DIRECT DEP(?:OSIT)?|DIR DEP)\s+((?:[A-Z][A-Z'.-]*\s+){1,3}?)"
    r"(PAYROLL|SALARY|REG\.?SALARY|PAYMENT|PMT|DEPOSIT)\b")


_HONORIFICS = {"MR", "MRS", "MS", "MISS", "DR", "MX", "SIR"}
_NAME_STOP = {"ON", "REF", "CONF", "FOR", "VIA", "MEMO", "ID", "AT", "ON-LINE", "WEB", "PPD", "CCD"}

_ALLOWED_CHARS = re.compile(r"[^A-Z0-9 &'.\-*/+]")


# =========================================================================== helpers

class _Ctx:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def mark(self, kind: str) -> None:
        if kind not in self.removed:
            self.removed.append(kind)

    def sub(self, pattern: re.Pattern, kind: str, s: str, repl=_GAP) -> str:
        def _r(m: re.Match) -> str:
            if not m.group(0).strip():
                return m.group(0)
            self.mark(kind)
            return repl(m) if callable(repl) else repl
        return pattern.sub(_r, s)


def _ascii_upper(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in s)
    return s.upper()


def _protect_person(s: str) -> str:
    s = re.sub(r"\*\s*" + re.escape(PERSON), " *\x06PERSON\x06 ", s)
    return s.replace(PERSON, " \x06PERSON\x06 ")


def _restore_person(s: str) -> str:
    return s.replace("\x06PERSON\x06", PERSON)


def _structured(s: str, ctx: _Ctx) -> str:
    """ACH / wire 'LABEL: value' strings: keep the leading text and the values
    of whitelisted labels (company name, entry description); drop the rest."""
    ms = list(_ACH_RE.finditer(s))
    if not ms:
        return s
    parts = [s[: ms[0].start()]]
    kept_vals: dict[str, str] = {}
    for i, m in enumerate(ms):
        label = re.sub(r"\s+", " ", m.group(1))
        end = ms[i + 1].start() if i + 1 < len(ms) else len(s)
        val = s[m.end(): end].strip()
        if label in _ACH_KEEP:
            kept_vals.setdefault(label, val)
        elif val:
            ctx.mark("person" if label in {"IND NAME", "INDN", "BNF", "BNF NAME", "BENEFICIARY",
                                            "BENE", "NAME", "FROM", "TO", "SENDER", "RECEIVER",
                                            "RECIPIENT", "ORIGINATOR", "ORG"} else "id")
    for label in ("ORIG CO NAME", "DES", "CO ENTRY DESCR", "WIRE TYPE"):
        if label in kept_vals:
            parts.append(kept_vals[label])
    return _GAP.join(p for p in parts if p.strip())


def _is_business(tokens: list[str]) -> bool:
    for t in tokens:
        bare = t.strip(",;:")
        if bare in BUSINESS_WORDS or re.search(r"\.(?:" + _TLD + r")$", bare):
            return True
    return False


def _all_known(tokens: list[str]) -> bool:
    return bool(tokens) and all(t.strip(",;:*") in KNOWN_WORDS for t in tokens)


def _name_span(words: list[str]) -> int:
    """How many leading words form a person's name (0 if none): a common first name
    followed by a surname-like word, optionally a second surname. "JOHN SMITH" -> 2,
    "KIM PATEL" -> 2, "JAMES AVERY JEWELRY" -> 0 (AVERY isn't a known surname and the
    rest is a business), "KEVIN LAWN CARE" -> 0 (a lone first name isn't identifying)."""
    if len(words) < 2 or " ".join(words[:2]) in BRANDS_NAMED_LIKE_PEOPLE:
        return 0
    people = FIRST_NAMES | SURNAMES
    alpha = [bool(re.fullmatch(r"[A-Z][A-Z'-]*", w)) for w in words]

    def plain(w: str) -> bool:      # not a business / brand / place word
        return not ((w in KNOWN_WORDS and w not in people) or w in BUSINESS_WORDS
                    or w in US_CITIES or w in US_STATES)
    w0, w1 = words[0], words[1]
    if not (alpha[0] and alpha[1] and plain(w0) and plain(w1)):
        return 0
    # With more words after it, the 2nd word must be a known surname: "KEVIN LAWN CARE" is a business.
    if w0 in FIRST_NAMES and (w1 in SURNAMES or len(words) == 2):
        span = 2
    elif w0 in SURNAMES and w1 in FIRST_NAMES and len(words) == 2:   # "SMITH JOHN"
        span = 2
    else:
        return 0
    if len(words) > 2 and alpha[2] and words[2] in SURNAMES and plain(words[2]):
        span = 3
    return span


def _named_people(s: str, ctx: _Ctx) -> str:
    """People outside P2P phrases: processor-prefixed sole proprietors ("SQ *JOHN SMITH"),
    payroll ACH lines ("ACH CREDIT JOHN SMITH PAYROLL") and bare-name merchants
    ("JANE DOE"). Only the name words are replaced; business words after them stay
    ("SQ *JOHN SMITH PLUMBING" -> "SQ *<PERSON> PLUMBING")."""
    m = _PROCESSOR_RE.match(s)
    if m:
        words = [w.strip(",;:.") for w in m.group(2).split()]
        n = _name_span(words)
        if n:
            ctx.mark("person")
            return " ".join([m.group(1).replace(" ", ""), PERSON] + words[n:])

    def payroll(m: re.Match) -> str:
        name = m.group(2).split()
        if name and not _is_business(name) and not _all_known(name):
            ctx.mark("person")
            return f"{m.group(1)} {PERSON} {m.group(3)}"
        return m.group(0)
    s = _PAYROLL_NAME_RE.sub(payroll, s)
    if PERSON not in s:
        words = [w.strip(",;:.") for w in s.split()]
        n = _name_span(words)
        if n:
            ctx.mark("person")
            return " ".join([PERSON] + words[n:])
        # "<FIRST> <SURNAME>" anywhere else in the descriptor ("PAINTING BY MARK GREEN").
        for i in range(1, len(words) - 1):
            pair = f"{words[i]} {words[i + 1]}"
            if (words[i] in FIRST_NAMES and words[i + 1] in SURNAMES
                    and pair not in BRANDS_NAMED_LIKE_PEOPLE):
                ctx.mark("person")
                return " ".join(words[:i] + [PERSON] + words[i + 2:])
    return s


def _p2p(s: str, ctx: _Ctx) -> str:
    """Replace counter-party names after P2P / transfer phrases with <PERSON>."""
    pos = 0
    while True:
        m = _P2P_TRIGGER.search(s, pos)
        if not m:
            return s
        rest = s[m.end():]
        toks = rest.split(" ")
        name: list[str] = []
        consumed = 0
        already_person = False
        for raw_t in toks:
            consumed += 1
            t = raw_t.strip("\"'(),;:[]{}")
            if t == "" or t == "*":
                continue
            if "\x06" in t:
                if not name:
                    already_person = True
                consumed -= 1
                break
            is_stop = (t == "\x07" or t in _NAME_STOP or any(ch.isdigit() for ch in t)
                       or t.startswith("#") or not any(ch.isalpha() for ch in t))
            if is_stop:
                if not name:
                    # Removed dates/ids/labels right after the trigger must not
                    # hide the name that follows them ("ZELLE TO 03/12 JOHN").
                    continue
                consumed -= 1
                break
            if t in {"TO", "FROM"} and not name:
                continue
            name.append(t)
            if len(name) >= 6:
                break
        if already_person:
            pos = m.end()
            continue
        star = "*" in m.group("sep")
        is_brand = m.group("brand") is not None
        bare = [t.strip(",;:*") for t in name if t.strip(",;:*")]
        if not bare or (is_brand and not star and not m.group("dir1") and _all_known(bare)) \
                or _is_business(bare) or _all_known(bare):
            pos = m.end()
            continue
        if star and len(bare) == 1 and bare[0] in KNOWN_WORDS:
            pos = m.end()
            continue
        # A person: replace the name and drop everything after it (free-text memo).
        ctx.mark("person")
        trigger = s[m.start(): m.end()].rstrip()
        if star:
            trigger = re.sub(r"\s*\*\s*$", " *", trigger)
            head = s[: m.start()] + trigger + "\x06PERSON\x06"
        else:
            head = s[: m.start()] + trigger + " \x06PERSON\x06"
        if any(t.strip() and t != "\x07" for t in toks[consumed:]):
            ctx.mark("person")
        return head


def _strip_location_tail(tokens: list[str], ctx: _Ctx) -> list[str]:
    protected_tail = {("MURPHY", "USA"), ("US", "BANK"), ("BANK", "OF", "AMERICA")}
    changed = True
    while changed and len(tokens) > 1:
        changed = False
        for k in (4, 3, 2, 1):
            if len(tokens) <= k:
                continue
            tail = tokens[-k:]
            if PERSON in tail:
                continue
            phrase = " ".join(tail)
            if any(tuple(tokens[-len(p):]) == p for p in protected_tail):
                return tokens
            if (k == 1 and phrase in US_STATES) or phrase in COUNTRY_TAIL or phrase in US_CITIES:
                tokens = tokens[:-k]
                ctx.mark("address")
                changed = True
                break
    return tokens


def _clean_token(t: str, ctx: _Ctx) -> str | None:
    if t == PERSON or t == "*" + PERSON:
        return t
    t = _ALLOWED_CHARS.sub("", t)
    lead_star = t.startswith("*")
    trail_star = t.endswith("*") and not lead_star
    t = t.strip("&'.-*/+,")
    if not t:
        return None
    if t.startswith("<") or t.endswith(">"):
        t = t.strip("<>")
    digits = sum(ch.isdigit() for ch in t)
    letters = sum(ch.isalpha() for ch in t)
    if digits >= 3:
        ctx.mark("digits" if letters == 0 else "id")
        return None
    if digits >= 2 and letters >= 1 and len(t) >= 5 and "." not in t:
        ctx.mark("id")
        return None
    if letters == 0 and digits == 0:
        return None
    if lead_star:
        return "*" + t
    return t + "*" if trail_star and letters else t


_TRAILING_JUNK = {"ON", "AT", "REF", "NO", "FOR", "VIA", "DATE", "ID", "CONF", "TRACE", "AND", "OF",
                  "THE", "TO", "FROM", "#", "&", "-", "/", "*", "STE", "SUITE", "APT", "UNIT", "TRN",
                  "IN"}
_LEADING_JUNK = {"ON", "AT", "REF", "&", "-", "/", "*"}


# =========================================================================== public API

def redact(description: str) -> Redacted:
    """Return a descriptor that is safe to send to an AI provider.

    Runs the rule pipeline to a fixpoint, so the result is idempotent by
    construction (a second pass can only ever remove more, never add)."""
    r = _redact_once(description)
    for _ in range(4):
        nxt = _redact_once(r.text)
        if nxt.text == r.text:
            break
        r = Redacted(nxt.text, r.removed + [k for k in nxt.removed if k not in r.removed])
    return r


def _redact_once(description: str) -> Redacted:
    ctx = _Ctx()
    if not isinstance(description, str) or not description.strip():
        return Redacted("", [])
    s = _protect_person(_ascii_upper(description))
    s = re.sub(r"\s+", " ", s).strip()

    # 1. Structured ACH / wire records.
    s = _structured(s, ctx)
    if _WIREISH.search(s):
        m = _WIRE_CUT.search(s)
        if m:
            ctx.mark("person")
            s = s[: m.start()]
    m = _ANY_CUT.search(s)
    if m:
        ctx.mark("person")
        s = s[: m.start()]

    # 2. Contact details and links.
    s = ctx.sub(_EMAIL_RE, "email", s)

    def _url(m: re.Match) -> str:
        ctx.mark("id")
        return " " + m.group(1) + " "
    s = _URL_RE.sub(_url, s)

    def _dom(m: re.Match) -> str:
        path = m.group(2) or ""
        if path in ("/BILL", "/BILLWA"):
            if path != "/BILL":
                ctx.mark("address")
            return " " + m.group(1) + "/BILL "
        if path:
            ctx.mark("id")
        return " " + m.group(1) + " "
    s = _DOMAIN_RE.sub(_dom, s)
    s = ctx.sub(_PHONE_RE, "phone", s)

    # 3. SSNs, card / account masks, reference numbers, dates, amounts, addresses.
    s = ctx.sub(_SSN_RE, "id", s)
    s = ctx.sub(_MASK_RE, "id", s)
    s = ctx.sub(_REF_HASH_RE, "id", s)
    s = ctx.sub(_REF_PLAIN_RE, "id", s)
    s = ctx.sub(_HASH_ID_RE, "id", s)
    s = ctx.sub(_TIME_RE, "digits", s)
    s = ctx.sub(_AMOUNT_RE, "digits", s)
    s = ctx.sub(_POBOX_RE, "address", s)
    s = ctx.sub(_ADDR_RE, "address", s)
    s = ctx.sub(_SUITE_RE, "address", s)
    s = _AUTH_ON_RE.sub(lambda m: " " + (m.group(1) if m.group(1) not in (None, "PURCHASE", "PAYMENT") else "") + " ", s)
    s = ctx.sub(_DATE_RE, "digits", s)

    # Split "MKTPL*BJ1M70883" into "MKTPL *BJ1M70883" so the id can be judged alone.
    s = re.sub(r"(?<=[A-Z0-9.])\*(?=[A-Z0-9])", " *", s)
    s = re.sub(r"\s+", " ", s).strip()

    # 4. People.
    s = _p2p(s, ctx)
    s = _named_people(_restore_person(s), ctx)
    s = _protect_person(s)

    # 5. Token-level cleanup.
    s = _restore_person(s)
    tokens: list[str] = []
    raw_tokens = s.split()
    for i, t in enumerate(raw_tokens):
        if t == "\x07":
            continue
        # ZIP next to a state code -> address.
        if re.fullmatch(r"\d{5}(?:-\d{4})?", t):
            nb = {raw_tokens[i - 1] if i else "", raw_tokens[i + 1] if i + 1 < len(raw_tokens) else ""}
            ctx.mark("address" if nb & US_STATES else "digits")
            continue
        c = _clean_token(t, ctx)
        if c:
            tokens.append(c)

    for _ in range(3):
        before = list(tokens)
        while tokens and tokens[-1] in _TRAILING_JUNK:
            tokens.pop()
        while tokens and tokens[0] in _LEADING_JUNK:
            tokens.pop(0)
        tokens = _strip_location_tail(tokens, ctx)
        if tokens == before:
            break

    out = " ".join(tokens)
    # 6. Final safety net - must hold no matter what the rules above missed.
    if re.search(r"\d{4,}", out):
        ctx.mark("digits")
        out = re.sub(r"\d{4,}", "", out)
    out = out.replace("@", "")
    out = re.sub(r"\s+", " ", out).strip()
    return Redacted(out, ctx.removed)


def redact_batch(descriptions: list[str]) -> dict[str, Redacted]:
    """Redact many descriptions; result keyed by the original input string."""
    out: dict[str, Redacted] = {}
    for d in descriptions:
        if d not in out:
            out[d] = redact(d)
    return out


def payload_object(descriptors: list[str]) -> dict:
    """The exact object sent to an AI provider for ``descriptors``.

    Every descriptor is (re-)redacted here as defence in depth (redaction is
    idempotent, so already-redacted input is unchanged), empties are dropped
    and duplicates collapsed, preserving first-seen order.
    """
    seen: dict[str, None] = {}
    for d in descriptors:
        t = redact(d).text
        if t and t not in seen:
            seen[t] = None
    return {"descriptors": list(seen)}


def payload_preview(descriptors: list[str]) -> str:
    """Exact JSON string that would be sent: ``{"descriptors": [...]}``."""
    return json.dumps(payload_object(descriptors), ensure_ascii=True)


# =========================================================================== identifier masking
# For documents shown back to the user or exported (pay stubs), where amounts, dates and words
# must survive: only taxpayer ids and account-like numbers are masked, keeping the last 4.

# Unlabelled SSNs only in the dashed form: spaced digit groups are amounts on some pay stubs.
_MASK_SSN_RE = re.compile(r"(?<![0-9A-Za-z-])(\d{3})(-)(\d{2})-(\d{4})(?![0-9-])")
_MASK_LABELLED_RE = re.compile(
    r"(?i)\b(SSN|SOC(?:IAL)?\.?\s*SEC(?:URITY)?\.?\s*(?:NO\.?|NUMBER|#)|TIN|ITIN|EIN|"
    r"(?:BANK\s+)?ACCOUNT(?:\s*(?:NO|NUMBER|#))?|ACCT\.?(?:\s*(?:NO|NUMBER|#))?|A/C|ROUTING(?:\s*(?:NO|NUMBER|#))?|"
    r"ABA|EMPLOYEE\s*(?:ID|NO|NUMBER|#)|EMP\.?\s*(?:ID|NO|#)|FILE\s*(?:NO|NUMBER|#)|ADVICE\s*(?:NO|NUMBER|#))"
    r"(\.?\s*[:#]?\s*)([X*\d][X*\d\s-]{2,}[X*\d])")
_MASK_LONG_DIGITS_RE = re.compile(r"(?<![\d.,/$-])\d{6,}(?![\d.,/])")


def _keep_last4(raw: str) -> str:
    digits = [c for c in raw if c.isdigit()]
    tail = "".join(digits[-4:]) if len(digits) > 4 else ""
    return "X" * max(4, len(raw.replace(" ", "")) - len(tail)) + tail if tail else "X" * max(4, len(raw.strip()))


def mask_identifiers(text: str) -> str:
    """Mask SSNs/TINs, account, routing and employee numbers in free text, keeping amounts.

    ``900-45-6789`` becomes ``XXX-XX-6789``; a labelled number ("Account: 000123456789",
    "SSN 123456789") keeps only its last 4 digits; any other run of 6+ digits that is not part
    of an amount or date keeps its last 4. Already-masked ids (``XXX-XX-1234``) are unchanged.
    Idempotent.
    """
    if not text:
        return text
    s = _MASK_SSN_RE.sub(lambda m: f"XXX{m.group(2)}XX{m.group(2)}{m.group(4)}", text)

    def _labelled(m: re.Match) -> str:
        val = m.group(3)
        if sum(c.isdigit() for c in val) <= 4:
            return m.group(0)
        if re.fullmatch(r"\d{3}([- ]?)\d{2}\1\d{4}", val):
            sep = re.sub(r"\d", "", val)[:1]
            return m.group(1) + m.group(2) + f"XXX{sep}XX{sep}{val[-4:]}"
        return m.group(1) + m.group(2) + _keep_last4(val)
    s = _MASK_LABELLED_RE.sub(_labelled, s)
    s = _MASK_LONG_DIGITS_RE.sub(lambda m: "X" * (len(m.group(0)) - 4) + m.group(0)[-4:], s)
    return s
