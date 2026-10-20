# SPDX-License-Identifier: AGPL-3.0-only
"""Stand-ins for teammate modules, used by test_store/test_api ONLY when the real
module is not importable yet (parallel development). Once every real module
exists, `install_missing()` installs nothing and the tests run end-to-end."""
from __future__ import annotations

import csv
import importlib
import io
import json
import re
import sys
import types
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from ledgerstitch.models import AccountKind, Direction, Transaction

SAMPLE_CSV = (
    "Posting Date,Description,Amount\n"
    "01/05/2025,GITHUB INC,-21.00\n"
    "01/06/2025,STARBUCKS STORE 1234 AUSTIN TX,-5.25\n"
    "01/07/2025,ACME WIDGETS SUPPLY,-300.00\n"
    "02/07/2025,ACME WIDGETS SUPPLY,-150.00\n"
    "02/10/2025,CLIENT DEPOSIT,1000.00\n"
    "03/01/2025,NETFLIX.COM,-15.99\n"
).encode()


# ---------------------------------------------------------------- ingest.detect
@dataclass
class ColumnMapping:
    date: str
    description: str
    amount: Optional[str] = None
    debit: Optional[str] = None
    credit: Optional[str] = None
    type_col: Optional[str] = None
    category: Optional[str] = None
    date_format: Optional[str] = None
    negative_is_out: bool = True


@dataclass
class FormatProfile:
    id: str
    institution: str
    kind: AccountKind
    mapping: Optional[ColumnMapping]
    confidence: float
    account_hint: Optional[str]


def _detect(filename: str, data: bytes) -> FormatProfile:
    if data.lstrip().startswith(b"%PDF"):
        return FormatProfile("pdf", "Unknown", AccountKind.OTHER, None, 0.5, None)
    header = data.decode("utf-8", "replace").splitlines()[0].lower()
    if header.replace(" ", "") == "postingdate,description,amount":
        return FormatProfile("generic", "Unknown", AccountKind.OTHER,
                             ColumnMapping("Posting Date", "Description", "Amount"), 0.6, None)
    return FormatProfile("unknown", "Unknown", AccountKind.OTHER, None, 0.0, None)


def _parse_date(s: str) -> date:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError("bad date")


def _parse_csv(data, profile, account_id, import_id):
    m = profile.mapping
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    out = []
    for i, r in enumerate(rows):
        amt = Decimal(r[m.amount].replace(",", "").replace("$", ""))
        out.append(Transaction(account_id=account_id, date=_parse_date(r[m.date]),
                               description=r[m.description], amount=abs(amt),
                               direction=Direction.OUT if amt < 0 else Direction.IN,
                               import_id=import_id, seq=i))
    return out


def _preview_csv(data, rows=8):
    r = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    return {"columns": r[0] if r else [], "rows": r[1:1 + rows]}


# ---------------------------------------------------------------- ingest.reconcile
@dataclass
class ReconcileReport:
    duplicates: int = 0
    transfers: int = 0
    refunds: int = 0
    notes: list = field(default_factory=list)


def _reconcile(txns, accounts):
    return ReconcileReport()


# ---------------------------------------------------------------- security.redact
@dataclass
class Redacted:
    text: str
    removed: list


def _redact(d: str) -> Redacted:
    t = re.sub(r"\d{4,}", "", d.upper())
    return Redacted(" ".join(t.split()), ["digits"] if t != d.upper() else [])


def _redact_batch(ds):
    return {d: _redact(d) for d in ds}


def _payload_preview(descriptors):
    return json.dumps({"descriptors": descriptors}, indent=2)


# ---------------------------------------------------------------- security.license
@dataclass
class License:
    email: str
    plan: str
    expires: Optional[date]
    valid: bool


def _current_plan(key):
    return License("", "free", None, False)


def _verify(key):
    return License("", "free", None, False)


FAKES = {
    "ledgerstitch.ingest.detect": dict(ColumnMapping=ColumnMapping, FormatProfile=FormatProfile, detect=_detect),
    "ledgerstitch.ingest.csv_parser": dict(parse_csv=_parse_csv, preview_csv=_preview_csv),
    "ledgerstitch.ingest.reconcile": dict(reconcile=_reconcile, ReconcileReport=ReconcileReport),
    "ledgerstitch.security.redact": dict(redact=_redact, redact_batch=_redact_batch,
                                       payload_preview=_payload_preview, Redacted=Redacted),
    "ledgerstitch.security.license": dict(current_plan=_current_plan, verify=_verify, License=License),
}


def install_missing(monkeypatch) -> list[str]:
    """Install fakes for whichever teammate modules can't be imported. Returns their names."""
    faked = []
    for name, attrs in FAKES.items():
        try:
            importlib.import_module(name)
            continue
        except Exception:
            pass
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        monkeypatch.setitem(sys.modules, name, mod)
        faked.append(name)
    return faked


def assert_locked(resp, feature, installed=True):
    """The 402 body every gated route answers with while the feature is locked."""
    assert resp.status_code == 402, (feature, resp.status_code, resp.text)
    body = resp.json()
    assert body["feature"] == feature and body["upgrade"] is True and body["installed"] is installed
    assert body["error"]
    return body
