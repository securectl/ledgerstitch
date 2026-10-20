# SPDX-License-Identifier: AGPL-3.0-only
"""Delete imports/accounts/transactions, purge, export and delete the workspace."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend_fakes import SAMPLE_CSV, install_missing
from ledgerstitch import plugins, store
from ledgerstitch.api import create_app
from ledgerstitch.security.vault import Vault

PASS = "correct horse battery"
TOKEN = "t" * 43
BASE = "http://127.0.0.1:8765"
OTHER_CSV = b"Date,Description,Amount\n2025-03-01,SAMPLE HARDWARE,-40.00\n2025-03-02,SAMPLE CAFE,-6.50\n"


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    install_missing(monkeypatch)


@pytest.fixture
def ws(tmp_path):
    v = Vault.create(tmp_path / "vaults" / "acme.vault", PASS, "Acme Studio")
    w = store.Workspace(v)
    w.save()
    yield w
    if not v.locked:
        v.lock()


def _reopen(w):
    return store.Workspace(Vault.open(w.vault.path, PASS))


def test_delete_import_removes_its_transactions_and_empty_account(ws):
    a = ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    b = ws.add_import("other.csv", OTHER_CSV, account_label="Other Card")
    assert len(ws.transactions) == a.count + b.count
    r = ws.delete_import(a.import_id)
    assert r == {"deleted_transactions": a.count, "deleted_accounts": 1}
    assert all(t.import_id == b.import_id for t in ws.transactions.values())
    assert set(ws.accounts) == {b.account_id}
    assert [i["import_id"] for i in ws.imports] == [b.import_id]
    w2 = _reopen(ws)                       # persisted
    assert len(w2.transactions) == b.count and a.account_id not in w2.accounts
    with pytest.raises(store.NotFound):
        ws.delete_import(a.import_id)


def test_reimport_after_delete_brings_rows_back(ws):
    a = ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    ws.delete_import(a.import_id)
    again = ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    assert again.new == a.count


def test_delete_account_and_transactions(ws):
    a = ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    b = ws.add_import("other.csv", OTHER_CSV, account_label="Other Card")
    ws.add_rule({"pattern": "SAMPLE", "category": "Business Meals", "account_id": b.account_id}, apply=False)
    r = ws.delete_account(b.account_id)
    assert r == {"deleted_transactions": b.count, "deleted_imports": 1}
    assert b.account_id not in ws.accounts and not any(x.account_id == b.account_id for x in ws.rules)
    one = next(iter(ws.transactions))
    assert ws.delete_transactions([one, "nope"]) == {"deleted_transactions": 1}
    assert one not in ws.transactions and len(ws.transactions) == a.count - 1


def test_links_to_deleted_rows_are_cleared(ws):
    ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    t1, t2 = list(ws.transactions.values())[:2]
    t2.duplicate_of, t2.refund_of = t1.id, t1.id
    ws.delete_transactions([t1.id])
    assert ws.transactions[t2.id].duplicate_of is None and ws.transactions[t2.id].refund_of is None


def test_purge_transactions_keeps_rules_and_settings(ws):
    ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    ws.add_rule({"pattern": "ACME", "category": "Business Meals"}, apply=False)
    ws.update_settings({"business_profile": "Photographer"})
    r = ws.purge("transactions")
    assert r["deleted"]["transactions"] > 0
    assert not ws.transactions and not ws.imports and not ws.accounts
    assert ws.rules and ws.settings["business_profile"] == "Photographer"


def test_purge_all_resets_but_keeps_license_and_name(ws):
    ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    ws.add_rule({"pattern": "ACME", "category": "Business Meals"}, apply=False)
    ws.settings["license_key"] = "LS1.kept"
    ws.update_settings({"business_profile": "Photographer"})
    ws.plugin_store("someaddon").put_json("profile", {"x": 1})
    ws.purge("all")
    assert not ws.rules and not ws.transactions
    assert ws.settings["license_key"] == "LS1.kept" and ws.settings["business_name"] == "Acme Studio"
    assert ws.settings["business_profile"] != "Photographer"
    assert not any(k.startswith("plugin:") for k in ws.vault.keys())
    with pytest.raises(store.ValidationError):
        ws.purge("everything")


def test_add_on_purgers_run(ws, monkeypatch):
    seen = []
    monkeypatch.setattr(ws.registry, "data_purgers", [("x", lambda w, scope: seen.append(scope)),
                                                      ("bad", lambda w, scope: 1 / 0)])
    r = ws.purge("transactions")
    assert seen == ["transactions"] and r["addon_errors"] == ["bad"]


def test_export_zip_has_everything_but_secrets(ws, tmp_path):
    ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz Checking")
    ws.settings["license_key"] = "LS1.secret"
    out = ws.export_data(tmp_path)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"README.txt", "ledgerstitch-data.json", "transactions.csv"} <= names
        data = json.loads(z.read("ledgerstitch-data.json"))
        csv_rows = z.read("transactions.csv").decode().strip().splitlines()
    assert len(data["transactions"]) == len(ws.transactions) == len(csv_rows) - 1
    assert "license_key" not in data["settings"] and "LS1.secret" not in out.read_bytes().decode("latin-1")


def test_vault_destroy_removes_only_its_files(tmp_path):
    p = tmp_path / "shared"
    v = Vault.create(p, PASS, "Acme")
    v.put_json("transactions", [1, 2])
    (p / "keep-me.txt").write_text("not ours")
    assert v.destroy() >= 3 and v.locked
    assert sorted(x.name for x in p.iterdir()) == ["keep-me.txt"]
    v2 = Vault.create(tmp_path / "solo.vault", PASS, "Solo")
    v2.destroy()
    assert not (tmp_path / "solo.vault").exists()


# ---------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, port=8765), base_url=BASE,
                    headers={"X-LedgerStitch-Token": TOKEN}) as c:
        assert c.post("/api/vaults", json={"name": "Acme Studio", "passphrase": PASS}).status_code == 201
        yield c


def _upload(c, data=SAMPLE_CSV, name="bank.csv", label="Biz Checking"):
    r = c.post("/api/imports", files=[("file", (name, data, "text/csv"))], data={"account_label": label})
    assert r.status_code == 200, r.text
    return r.json()[0]


def test_api_delete_import_account_and_transactions(client):
    a = _upload(client)
    b = _upload(client, OTHER_CSV, "other.csv", "Other Card")
    lst = client.get("/api/imports").json()
    assert {i["import_id"]: i["transactions"] for i in lst} == {a["import_id"]: a["count"], b["import_id"]: b["count"]}
    assert lst[0]["account_label"] == "Other Card"
    assert client.delete(f"/api/imports/{a['import_id']}").json()["deleted_transactions"] == a["count"]
    assert client.delete("/api/imports/nope").status_code == 404
    ids = [t["id"] for t in client.get("/api/transactions").json()["items"]]
    assert client.post("/api/transactions/delete", json={"ids": ids[:1]}).json() == {"deleted_transactions": 1}
    assert client.delete(f"/api/accounts/{b['account_id']}").status_code == 200
    assert client.get("/api/status").json()["counts"] == {"accounts": 0, "transactions": 0}


def test_api_purge_needs_typed_confirmation(client):
    _upload(client)
    assert client.post("/api/data/purge", json={"scope": "all", "confirm": "yes"}).status_code == 422
    assert client.post("/api/data/purge", json={"scope": "nope", "confirm": "DELETE"}).status_code == 422
    r = client.post("/api/data/purge", json={"scope": "transactions", "confirm": "DELETE"})
    assert r.status_code == 200 and r.json()["deleted"]["transactions"] > 0


def test_api_export_download(client):
    _upload(client)
    r = client.get("/api/data/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert "ledgerstitch-data.json" in z.namelist()


def test_api_delete_workspace(client, tmp_path):
    _upload(client)
    path = Path(client.get("/api/status").json()["vault_path"])
    assert client.post("/api/vaults/delete", json={"passphrase": "wrong one!", "confirm": "DELETE"}).status_code == 401
    assert client.post("/api/vaults/delete", json={"passphrase": PASS, "confirm": "nope"}).status_code == 422
    assert path.exists()
    r = client.post("/api/vaults/delete", json={"passphrase": PASS, "confirm": "DELETE"})
    assert r.status_code == 200 and r.json()["unlocked"] is False
    assert not path.exists() and client.get("/api/vaults").json() == []
