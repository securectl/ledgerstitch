# SPDX-License-Identifier: AGPL-3.0-only
import json
import re
import sys
import types
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from ledgerstitch.ai import categorizer as cz
from ledgerstitch.ai.categorizer import build_ai_payload, categorize, scrub
from ledgerstitch.models import Direction, Lane, Rule, Source, Status, Transaction
from ledgerstitch.taxonomy import DEFAULT_CATEGORIES


# ---------------------------------------------------------------- redact stub
@dataclass
class _Redacted:
    text: str
    removed: list


def _stub_redact(desc: str) -> _Redacted:
    s = desc.upper()
    removed = []
    s2 = re.sub(r"(ZELLE|VENMO)( TO| FROM)? [A-Z ]+", r"\1\2 <PERSON>", s)
    if s2 != s:
        removed.append("person")
    s3 = re.sub(r"[X*]{2,}\d{4}|\d{4,}", " ", s2)
    if s3 != s2:
        removed.append("digits")
    return _Redacted(" ".join(s3.split()), removed)


@pytest.fixture(autouse=True)
def stub_redact(monkeypatch):
    mod = types.ModuleType("ledgerstitch.security.redact")
    mod.Redacted = _Redacted
    mod.redact = _stub_redact
    mod.redact_batch = lambda items: {d: _stub_redact(d) for d in items}
    monkeypatch.setitem(sys.modules, "ledgerstitch.security.redact", mod)
    return mod


def tx(desc, merchant="", amount="1234.00", d=date(2025, 7, 4), **kw):
    return Transaction(account_id="chase-4012", date=d, description=desc, amount=Decimal(amount),
                       direction=kw.pop("direction", Direction.OUT), merchant=merchant, **kw)


class FakeProvider:
    name = "fake"

    def __init__(self, answers):
        self.answers = answers
        self.calls = []
        self.warnings = []

    def classify(self, descriptors, categories):
        self.calls.append((list(descriptors), categories))
        return [dict(self.answers[d], descriptor=d) for d in descriptors if d in self.answers]


def ans(category, lane, sc=None, conf=0.9, new=False):
    return {"category": category, "lane": lane, "schedule_c": sc, "confidence": conf, "new_category": new}


# ---------------------------------------------------------------- payload
def test_payload_has_no_amounts_dates_or_digit_runs():
    txns = [
        tx("ZELLE TO JOHN SMITH 08/14 REF 99812345", amount="1234.00"),
        tx("SQ *BLUE BOTTLE #0423 $45.10 07/04/2025", amount="45.10"),
        tx("POS DEBIT 4411 CARD XXXX4012 WIDGETCO", amount="9.99"),
        tx("WIDGETCO", amount="9.99"),   # duplicate descriptor collapses
        tx("ADOBE *CREATIVE CLD", merchant="ADOBE"),   # heuristic 0.7 -> still sent (<0.75)
        tx("ACME", source=Source.RULE, category="Shopping", confidence=1.0),   # rule: not sent
        tx("SECRET APPROVED VENDOR", status=Status.APPROVED, category="Shopping", confidence=0.4),
    ]
    payload = build_ai_payload(txns)
    assert payload == sorted(set(payload))
    blob = json.dumps(payload)
    assert not re.search(r"\d{3,}", blob)
    assert not re.search(r"\d+\.\d{2}", blob)
    assert not re.search(r"\d{1,2}/\d{1,2}", blob)
    assert "$" not in blob and "1234" not in blob and "4012" not in blob
    assert "JOHN" not in blob and "SMITH" not in blob
    assert "ZELLE TO <PERSON>" in payload
    assert "SECRET APPROVED VENDOR" not in blob
    assert "ACME" not in blob


def test_scrub_strips_leftovers():
    assert scrub("amc 1234 #12 $5.00 1/2") == "AMC"
    assert scrub("7-ELEVEN") == "7-ELEVEN"


def test_real_redact_module_if_present(monkeypatch):
    monkeypatch.delitem(sys.modules, "ledgerstitch.security.redact", raising=False)
    try:
        import ledgerstitch.security.redact  # noqa: F401
    except ImportError:
        pytest.skip("security.redact not written yet")
    txns = [tx("ZELLE TO JANE DOE 1234567 08/14"), tx("AMAZON MKTPL*BJ1M70883 AMZN.COM/BILL WA")]
    blob = json.dumps(build_ai_payload(txns))
    assert not re.search(r"\d{4,}", blob) and "JANE" not in blob


def test_missing_redactor_means_nothing_sent(monkeypatch):
    monkeypatch.setitem(sys.modules, "ledgerstitch.security.redact", None)
    prov = FakeProvider({})
    res = categorize([tx("MYSTERY SHOP")], [], {"ai_enabled": True}, DEFAULT_CATEGORIES, provider=prov)
    assert prov.calls == [] and res.sent_descriptors == 0
    assert any("Redaction" in w for w in res.warnings)


# ---------------------------------------------------------------- pipeline
def test_pipeline_maps_results_to_every_txn_with_descriptor():
    a1 = tx("BLUEWAVE LABS 99881", amount="10.00")
    a2 = tx("BLUEWAVE LABS 77612", amount="20.00")
    b = tx("ODDSHOP")
    dup = tx("BLUEWAVE LABS 55555", duplicate_of="x")
    ruled = tx("NETFLIX.COM")
    prov = FakeProvider({
        "BLUEWAVE LABS": ans("Software & Subscriptions", "business", None, 0.92),
        "ODDSHOP": ans("Shopping", "personal", None, 0.5),
    })
    rules = [Rule("NETFLIX", "Entertainment & Streaming", Lane.PERSONAL)]
    res = categorize([a1, a2, b, dup, ruled], rules, {}, DEFAULT_CATEGORIES, provider=prov)
    assert prov.calls[0][0] == ["BLUEWAVE LABS", "ODDSHOP"]
    for t in (a1, a2):
        assert (t.category, t.lane, t.schedule_c, t.status, t.source) == \
            ("Software & Subscriptions", Lane.BUSINESS, "L18", Status.AUTO, Source.AI)
    assert b.status == Status.REVIEW and b.source == Source.AI
    assert dup.category is None          # duplicates untouched
    assert res.by_rules == 1 and res.by_ai == 3 and res.sent_descriptors == 2
    assert res.needs_review == 1


def test_categories_sent_contain_only_names_lanes_codes():
    prov = FakeProvider({})
    categorize([tx("ODDSHOP")], [], {}, DEFAULT_CATEGORIES, provider=prov)
    cats = prov.calls[0][1]
    assert set(cats[0]) == {"name", "lane", "schedule_c"}


def test_ai_does_not_override_stronger_heuristic():
    t = tx("ADOBE *CREATIVE CLD", merchant="ADOBE")
    prov = FakeProvider({"ADOBE": ans("Shopping", "personal", None, 0.6)})
    categorize([t], [], {}, DEFAULT_CATEGORIES, provider=prov)
    assert t.category == "Software & Subscriptions" and t.source == Source.HEURISTIC
    prov2 = FakeProvider({"ADOBE": ans("Software & Subscriptions", "business", "L18", 0.95)})
    categorize([t], [], {}, DEFAULT_CATEGORIES, provider=prov2)
    assert t.source == Source.AI and t.confidence == 0.95 and t.status == Status.AUTO


def test_validation_of_bad_ai_output():
    descs = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    txns = {d: tx(d) for d in descs}
    prov = FakeProvider({
        "AAA": ans("Software & Subscriptions", "sideways", "L99", 0.9),   # bad lane + code
        "BBB": ans("Made Up Thing", "business", "L18", 0.95, new=False),  # unknown, not flagged new
        "CCC": ans("software and subscription", "business", None, 1.7),   # near-dup name, conf>1
        "DDD": ans("Pet Supplies", "personal", "L22", 0.8, new=True),     # valid new
        "EEE": ans("Office  Supplies!!", "business", None, 0.8, new=True),  # near dup of existing
        "FFF": ans("<PERSON> 12345 Stuff", "business", None, 0.9, new=True),  # rejected name
    })
    prov.classify_extra = True
    orig = prov.classify
    prov.classify = lambda d, c: orig(d, c) + [
        {"descriptor": "NOT SENT", "category": "Shopping", "lane": "personal",
         "schedule_c": None, "confidence": 1, "new_category": False},
        "garbage",
        {"descriptor": "AAA", "category": "Shopping", "lane": "personal",   # second answer ignored
         "schedule_c": None, "confidence": 1, "new_category": False},
    ]
    res = categorize(list(txns.values()), [], {}, DEFAULT_CATEGORIES, provider=prov)
    a = txns["AAA"]
    assert a.lane == Lane.REVIEW and a.schedule_c == "L18" and a.status == Status.REVIEW
    b = txns["BBB"]
    assert b.category == "Uncategorized" and b.lane == Lane.REVIEW and b.confidence <= 0.5
    c = txns["CCC"]
    assert c.category == "Software & Subscriptions" and c.confidence == 1.0 and c.schedule_c == "L18"
    d = txns["DDD"]
    assert d.category == "Pet Supplies" and d.schedule_c is None    # personal -> no Schedule C
    assert txns["EEE"].category == "Office Supplies"
    assert txns["FFF"].category == "Uncategorized"
    assert res.new_categories == ["Pet Supplies"]
    assert res.new_category_defs == {"Pet Supplies": {"lane": "personal", "schedule_c": "L22"}}


def test_new_category_cap():
    names = [f"Niche Category {chr(65 + i)}" for i in range(14)]
    descs = [f"MERCHANT {chr(65 + i)}" for i in range(14)]
    prov = FakeProvider({d: ans(n, "business", "L27a", 0.9, new=True) for d, n in zip(descs, names)})
    txns = [tx(d) for d in descs]
    res = categorize(txns, [], {}, DEFAULT_CATEGORIES, provider=prov)
    assert len(res.new_categories) == cz.MAX_NEW_CATEGORIES
    assert sum(t.category == "Uncategorized" for t in txns) == 4


def test_offline_leaves_unknowns_for_review():
    t = tx("ZQX WIDGETS")
    res = categorize([t], [], {"ai_enabled": False}, DEFAULT_CATEGORIES)
    assert res.sent_descriptors == 0 and res.by_ai == 0
    assert t.category == "Uncategorized" and t.status == Status.REVIEW and res.needs_review == 1


def test_provider_error_is_a_warning_not_a_crash():
    from ledgerstitch.ai.errors import AIError as AIOfflineError

    class Boom(FakeProvider):
        def classify(self, d, c):
            raise AIOfflineError("offline")

    t = tx("ZQX WIDGETS")
    res = categorize([t], [], {}, DEFAULT_CATEGORIES, provider=Boom({}))
    assert res.warnings == ["offline"] and t.status == Status.REVIEW


def test_accepts_category_dicts_with_user_categories():
    cats = [{"name": c.name, "lane": c.lane.value, "schedule_c": c.schedule_c} for c in DEFAULT_CATEGORIES]
    cats.append({"name": "Food Truck Supplies", "lane": "business", "schedule_c": "L22"})
    t = tx("TRUCKPARTS EXPRESS")
    prov = FakeProvider({"TRUCKPARTS EXPRESS": ans("Food Truck Supplies", "business", None, 0.9)})
    res = categorize([t], [], {}, cats, provider=prov)
    assert t.category == "Food Truck Supplies" and t.schedule_c == "L22" and res.new_categories == []
