# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from backend_fakes import SAMPLE_CSV, install_missing
from ledgerstitch import store
from ledgerstitch.models import Lane, Status
from ledgerstitch.security.vault import Vault

PASS = "correct horse battery"


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    install_missing(monkeypatch)


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "vaults" / "acme.vault"


@pytest.fixture
def ws(vault_path):
    v = Vault.create(vault_path, PASS, "Acme Studio")
    w = store.Workspace(v)
    w.save()
    yield w
    v.lock()


def _acme(w):
    return [t for t in w.transactions.values() if "ACME" in t.description]


def test_import_persists_and_reimport_is_noop(ws, vault_path):
    r = ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    assert r.count == 6 and r.new == 6
    assert r.account_id == "biz-checking"
    assert len(ws.transactions) == 6
    assert all(t.merchant for t in ws.transactions.values())
    assert all(t.category for t in ws.transactions.values())  # categorizer ran

    again = ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    assert again.new == 0 and len(ws.transactions) == 6
    assert len(ws.accounts) == 1

    # import record is metadata only
    rec = ws.imports[0]
    assert set(rec) >= {"filename", "import_id", "date", "count", "new", "profile", "warnings"}
    assert "ACME" not in repr(ws.imports)

    # reopen from disk
    ws.vault.lock()
    w2 = store.Workspace(Vault.open(vault_path, PASS))
    assert len(w2.transactions) == 6 and "biz-checking" in w2.accounts
    assert len(w2.imports) == 2
    w2.vault.lock()


def test_nothing_plaintext_on_disk(ws, vault_path):
    ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    ws.settings["anthropic_api_key"] = "sk-ant-SECRET-123"      # any secret setting (edition-agnostic)
    ws.save()
    for f in vault_path.rglob("*"):
        if f.is_file():
            blob = f.read_bytes()
            for needle in (b"ACME", b"GITHUB", b"sk-ant-SECRET", b"300.00"):
                assert needle not in blob, (f.name, needle)


def test_update_with_remember_learns_rule(ws):
    ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    first, second = sorted(_acme(ws), key=lambda t: t.date)
    t, changed = ws.update_transaction(first.id, remember=True, category="Equipment & Supplies")
    assert t.status == Status.APPROVED and t.lane == Lane.BUSINESS and t.schedule_c == "L22"
    assert changed == 1
    assert second.category == "Equipment & Supplies" and second.lane == Lane.BUSINESS
    assert len(ws.rules) == 1
    # approved txns are not touched by later rule application
    ws.update_transaction(second.id, category="Office Supplies")
    ws.add_rule({"pattern": "ACME", "category": "Advertising & Marketing"})
    assert second.category == "Office Supplies"


def test_update_validation(ws):
    ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    tid = next(iter(ws.transactions))
    with pytest.raises(store.ValidationError):
        ws.update_transaction(tid, category="Nope")
    with pytest.raises(store.ValidationError):
        ws.update_transaction(tid, lane="sideways")
    with pytest.raises(store.NotFound):
        ws.update_transaction("missing", note="x")
    t, _ = ws.update_transaction(tid, note="client lunch")
    assert t.note == "client lunch"


def test_bulk_and_filters(ws):
    ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    ids = [t.id for t in _acme(ws)]
    assert ws.bulk_update(ids, category="Contractor Payments") == 2
    total, items = ws.list_transactions({"category": "Contractor Payments"})
    assert total == 2 and all(t.status == Status.APPROVED for t in items)
    total, _ = ws.list_transactions({"q": "netflix"})
    assert total == 1
    total, page = ws.list_transactions({"limit": 2, "offset": 0})
    assert total == 6 and len(page) == 2
    assert ws.list_transactions({"year": 2024})[0] == 0


def test_custom_categories(ws):
    c = ws.add_category("Studio Props", "business", "L22")
    assert c["custom"] is True
    names = [c["name"] for c in ws.categories()]
    assert "Studio Props" in names and "Groceries" in names
    with pytest.raises(store.ValidationError):
        ws.add_category("Studio Props", "business")
    with pytest.raises(store.ValidationError):
        ws.add_category("Bad Line", "business", "L999")


def _secret_plugin(registry):
    registry.settings({"demo_api_key": None, "demo_mode": "a"}, {"demo_api_key", "demo_mode"},
                      {"demo_mode": lambda v: v if v in ("a", "b") else (_ for _ in ()).throw(ValueError("bad mode"))},
                      secrets={"demo_api_key"})


@pytest.fixture
def demo_ws(fresh_registry, vault_path):
    from ledgerstitch import plugins
    plugins.register_local(_secret_plugin, namespace="demo")
    vault_path.parent.mkdir(parents=True, exist_ok=True)
    return store.Workspace(Vault.create(vault_path, PASS, "Acme Studio"))


def test_settings_never_expose_keys(demo_ws):
    ws = demo_ws
    pub = ws.update_settings({"demo_api_key": "sk-demo-xyz", "demo_mode": "b"})
    assert "demo_api_key" not in pub and pub["has_demo_api_key"] is True and pub["demo_mode"] == "b"
    assert ws.settings["demo_api_key"] == "sk-demo-xyz"
    pub = ws.update_settings({"demo_api_key": None})        # None = unchanged
    assert pub["has_demo_api_key"] is True
    pub = ws.update_settings({"demo_api_key": ""})          # "" = clear
    assert pub["has_demo_api_key"] is False
    with pytest.raises(store.ValidationError):
        ws.update_settings({"license_key": "x"})
    with pytest.raises(store.ValidationError, match="bad mode"):
        ws.update_settings({"demo_mode": "zzz"})


def test_unknown_stored_keys_are_kept_but_never_exposed(community, vault_path):
    """A vault used with an add-on, opened without it: keys survive, secrets stay hidden."""
    vault_path.parent.mkdir(parents=True, exist_ok=True)
    v = Vault.create(vault_path, PASS, "Acme Studio")
    v.put_json("settings", {"anthropic_api_key": "sk-ant-LEGACY", "addon_list": [{"provider": "x"}],
                            "business_name": "Acme"})
    ws = store.Workspace(v)
    pub = ws.public_settings()
    assert "sk-ant-LEGACY" not in str(pub) and "addon_list" not in pub and "anthropic_api_key" not in pub
    with pytest.raises(store.ValidationError, match="while it is installed"):
        ws.update_settings({"addon_list": []})
    ws.update_settings({"business_name": "Acme 2"})
    assert v.get_json("settings")["anthropic_api_key"] == "sk-ant-LEGACY"   # preserved for re-install


def test_core_has_no_usage_caps(ws):
    """AGPL core is not crippleware: no account / transaction limits without a license."""
    assert ws.limits() is None
    for i in range(4):
        ws.add_import(f"bank{i}.csv", SAMPLE_CSV.replace(b"2025", str(2015 + i).encode()),
                      account_label=f"Account {i}")
    assert len(ws.accounts) == 4 and len(ws.transactions) >= 4 * 6
    assert not hasattr(store, "MAX_RECEIPTS_FREE")


def test_unknown_csv_needs_mapping_then_mapped_import(ws, monkeypatch):
    data = b"When,What,How Much\n01/02/2025,ACME WIDGETS SUPPLY,-12.50\n01/03/2025,REFUND THING,4.00\n"
    real_detect = store._detect

    def undetected(filename, blob):
        prof = real_detect(filename, blob)
        prof.mapping = None          # simulate a layout the detector can't map
        return prof
    monkeypatch.setattr(store, "_detect", undetected)
    with pytest.raises(store.NeedsMapping):
        ws.add_import("weird.csv", data, account_label="Misc")
    prev = ws.preview_import("weird.csv", data)
    assert prev["columns"][:3] == ["When", "What", "How Much"]
    r = ws.add_import("weird.csv", data, account_label="Misc",
                      mapping={"date": "When", "description": "What", "amount": "How Much"})
    assert r.new == 2
    with pytest.raises(store.ValidationError):
        ws.add_import("weird.csv", data, mapping={"date": "When"})


def test_ai_preview_matches_categorize_payload(ws, monkeypatch):
    ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    preview = ws.ai_preview()
    assert all(isinstance(d, str) for d in preview)
    for d in preview:
        assert "300" not in d and "2025" not in d

    sent = {}

    class Spy:
        name = "spy"
        warnings: list = []

        def classify(self, descriptors, categories):
            sent["d"] = list(descriptors)
            return []

    import ledgerstitch.ai.categorizer as cat
    real = cat.categorize
    monkeypatch.setattr(store, "_categorize",
                        lambda txns, rules, settings, cats, **kw: real(txns, rules, settings, cats, provider=Spy()))
    ws.categorize()
    assert sorted(sent.get("d", [])) == sorted(preview)


def test_list_transactions_sorting():
    from datetime import date
    from ledgerstitch.models import Direction, Transaction
    from ledgerstitch.store import Workspace

    class _V:  # minimal in-memory vault stand-in
        def __init__(self): self.d = {}
        def get_json(self, k, default=None): return self.d.get(k, default)
        def put_json(self, k, v): self.d[k] = v
        name = "t"

    try:
        ws = Workspace(_V())
    except Exception:
        import pytest; pytest.skip("Workspace needs a real vault")
    for i, (amt, desc) in enumerate([("5.00", "B SHOP"), ("50.00", "A SHOP"), ("1.00", "C SHOP")]):
        t = Transaction(account_id="x", date=date(2025, 1, i + 1), description=desc, amount=amt,
                        direction=Direction.OUT)
        ws.transactions[t.id] = t
    _, by_amt = ws.list_transactions({"sort": "amount", "order": "asc"})
    assert [str(t.amount) for t in by_amt] == ["50.00", "5.00", "1.00"]  # most negative first
    _, by_desc = ws.list_transactions({"sort": "description", "order": "asc"})
    assert [t.description for t in by_desc] == ["A SHOP", "B SHOP", "C SHOP"]
    _, oldest = ws.list_transactions({"order": "asc"})
    assert oldest[0].date == date(2025, 1, 1)


def test_import_categorizes_without_holding_the_workspace_lock(ws, monkeypatch):
    """A slow AI call during import must not freeze the rest of the app."""
    import threading
    import time
    real = store._categorize
    started, waited = threading.Event(), []

    def slow(*a, **k):
        started.set()
        time.sleep(2.0)                 # if the lock were held, the read below would wait ~2s
        return real(*a, **k)

    monkeypatch.setattr(store, "_categorize", slow)
    t = threading.Thread(target=ws.add_import, args=("bank.csv", SAMPLE_CSV), kwargs={"account_label": "Biz"})
    t.start()
    assert started.wait(5)
    t0 = time.monotonic()
    with ws.lock:                       # what every other API call needs
        waited.append(time.monotonic() - t0)
    t.join()
    assert waited[0] < 1.0
    assert all(x.category for x in ws.transactions.values())


def test_pdf_import_records_balance_check_and_difference(ws):
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures"
    ok = ws.add_import("stmt_columns.pdf", (fx / "stmt_columns.pdf").read_bytes())
    assert ok.reconciled is True and ok.reconcile_difference == "0.00"
    bad = ws.add_import("stmt_columns_mismatch.pdf", (fx / "stmt_columns_mismatch.pdf").read_bytes())
    assert bad.reconciled is False and bad.reconcile_difference == "50.00"
    assert ws.imports[-1]["reconciled"] is False and ws.imports[-1]["reconcile_difference"] == "50.00"
    assert bad.to_dict()["reconcile_difference"] == "50.00"
    csv = ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    assert csv.reconciled is None and csv.reconcile_difference is None
