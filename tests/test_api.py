# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend_fakes import SAMPLE_CSV, install_missing
from ledgerstitch import store
from ledgerstitch.api import create_app

TOKEN = "t" * 43
PASS = "correct horse battery"
BASE = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    install_missing(monkeypatch)


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, TOKEN, port=8765)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN}) as c:
        yield c


@pytest.fixture
def unlocked(client):
    r = client.post("/api/vaults", json={"name": "Acme Studio", "passphrase": PASS})
    assert r.status_code == 201, r.text
    return client


def _upload(c, data=SAMPLE_CSV, name="bank.csv", label="Biz Checking"):
    return c.post("/api/imports", files=[("file", (name, data, "text/csv"))],
                  data={"account_label": label})


# ---------------------------------------------------------------- auth / hardening
def test_token_required(app):
    with TestClient(app, base_url=BASE) as c:
        r = c.get("/api/status")
        assert r.status_code == 401 and "error" in r.json()
        r = c.get("/api/status", headers={"X-LedgerStitch-Token": "wrong"})
        assert r.status_code == 401
        assert c.get("/api/status", headers={"X-LedgerStitch-Token": TOKEN}).status_code == 200


def test_host_check(app):
    for base in ("http://evil.example:8765", "http://127.0.0.1:9999", "http://localhost.evil.com:8765"):
        with TestClient(app, base_url=base, headers={"X-LedgerStitch-Token": TOKEN}) as c:
            assert c.get("/api/status").status_code == 400, base
            assert c.get("/").status_code == 400
    with TestClient(app, base_url="http://localhost:8765", headers={"X-LedgerStitch-Token": TOKEN}) as c:
        assert c.get("/api/status").status_code == 200


def test_cross_origin_rejected(client):
    r = client.get("/api/status", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert client.get("/api/status", headers={"Origin": BASE}).status_code == 200


def test_security_headers_and_index_token(client):
    r = client.get("/api/status")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    page = client.get("/", headers={"X-LedgerStitch-Token": ""})   # page itself needs no token
    assert page.status_code == 200
    assert TOKEN in page.text and "__LEDGERSTITCH_TOKEN__" not in page.text
    assert page.headers["content-security-policy"]


def test_locked_returns_409(client):
    r = client.get("/api/transactions")
    assert r.status_code == 409 and r.json()["error"]


def test_validation_is_422(client):
    r = client.post("/api/vaults", json={"name": "x"})
    assert r.status_code == 422 and "error" in r.json()
    r = client.post("/api/vaults", json={"name": "x", "passphrase": "short"})
    assert r.status_code == 422


# ---------------------------------------------------------------- end-to-end flow
def test_full_flow(unlocked):
    c = unlocked
    st = c.get("/api/status").json()
    assert st["unlocked"] and st["vault_name"] == "Acme Studio" and st["plan"] == "free"
    assert st["limits"] is None                      # no usage caps in any edition
    assert st["review_count"] == 0

    r = _upload(c)
    assert r.status_code == 200, r.text
    [res] = r.json()
    assert res["count"] == 6 and res["new"] == 6 and res["import_id"]
    assert res["detected"]["account_id"] == "biz-checking"

    # re-import: no-op
    assert _upload(c).json()[0]["new"] == 0

    accts = c.get("/api/accounts").json()
    assert [a["id"] for a in accts] == ["biz-checking"] and accts[0]["transactions"] == 6
    assert c.patch("/api/accounts/biz-checking", json={"label": "Ops Checking"}).json()["label"] == "Ops Checking"

    lst = c.get("/api/transactions", params={"q": "ACME"}).json()
    assert lst["total"] == 2
    first, second = sorted(lst["items"], key=lambda t: t["date"])

    r = c.patch(f"/api/transactions/{first['id']}",
                json={"category": "Equipment & Supplies", "remember": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] == 1
    assert body["transaction"]["status"] == "approved" and body["transaction"]["lane"] == "business"
    other = [t for t in c.get("/api/transactions", params={"q": "ACME"}).json()["items"]
             if t["id"] == second["id"]][0]
    assert other["category"] == "Equipment & Supplies"
    assert len(c.get("/api/rules").json()) == 1

    assert c.patch("/api/transactions/nope", json={"note": "x"}).status_code == 404
    assert c.patch(f"/api/transactions/{first['id']}", json={"category": "Bogus"}).status_code == 422

    ids = [t["id"] for t in c.get("/api/transactions", params={"q": "NETFLIX"}).json()["items"]]
    assert c.post("/api/transactions/bulk", json={"ids": ids, "lane": "personal"}).json()["updated"] == 1

    assert c.post("/api/categories", json={"name": "Studio Props", "lane": "business",
                                           "schedule_c": "L22"}).status_code == 201
    assert any(x["name"] == "Studio Props" for x in c.get("/api/categories").json())

    rule = c.post("/api/rules", json={"pattern": "GITHUB", "category": "Software & Subscriptions"}).json()
    assert c.delete(f"/api/rules/{rule['rule']['id']}").json() == {"ok": True}
    assert c.delete("/api/rules/missing").status_code == 404

    cat = c.post("/api/categorize").json()
    assert "needs_review" in cat

    prev = c.get("/api/ai/preview").json()
    assert isinstance(prev["descriptors"], list) and isinstance(prev["payload"], str)
    for d in prev["descriptors"]:
        assert "300.00" not in d and "2025" not in d

    summ = c.get("/api/summary", params={"year": 2025})
    assert summ.status_code == 200 and "totals" in summ.json()

    x = c.get("/api/export/xlsx", params={"year": 2025})
    assert x.status_code == 200
    assert x.content[:2] == b"PK"
    assert "attachment" in x.headers["content-disposition"] and ".xlsx" in x.headers["content-disposition"]
    for fmt in ("csv", "qbo", "iif"):
        r = c.get(f"/api/export/{fmt}", params={"year": 2025})
        assert r.status_code == 200 and r.content, fmt
    assert c.get("/api/export/pdf").status_code == 404

    imports = c.get("/api/imports").json()
    assert len(imports) == 2 and "ACME" not in json.dumps(imports)

    assert c.post("/api/lock").json()["unlocked"] is False
    assert c.get("/api/transactions").status_code == 409


def test_wrong_passphrase_401_and_unlock(unlocked):
    c = unlocked
    _upload(c)
    path = c.get("/api/vaults").json()[0]["path"]
    c.post("/api/lock")
    r = c.post("/api/unlock", json={"path": path, "passphrase": "not the passphrase"})
    assert r.status_code == 401 and r.json()["error"]
    r = c.post("/api/unlock", json={"path": path, "passphrase": PASS})
    assert r.status_code == 200 and r.json()["unlocked"]
    assert c.get("/api/transactions").json()["total"] == 6
    # path traversal outside the vaults folder is refused
    assert c.post("/api/unlock", json={"path": "/etc", "passphrase": PASS}).status_code == 422


def test_settings_core_keys_and_unknown_422(unlocked):
    c = unlocked
    r = c.put("/api/settings", json={"business_profile": "Photo studio", "tax_year": 2025})
    assert r.status_code == 200, r.text
    s = c.get("/api/settings").json()
    assert s["business_profile"] == "Photo studio" and s["tax_year"] == 2025
    assert s["has_license_key"] is False and "license_key" not in s
    assert c.put("/api/settings", json={"bogus": 1}).status_code == 422


def test_no_usage_caps_without_license(unlocked):
    for i in range(3):
        data = SAMPLE_CSV.replace(b"2025", str(2016 + i).encode())
        r = _upload(unlocked, data=data, name=f"b{i}.csv", label=f"Acct {i}")
        assert r.status_code == 200, r.text
    assert len(unlocked.get("/api/accounts").json()) == 3
    assert unlocked.get("/api/status").json()["limits"] is None


def test_invalid_license_key_422(unlocked):
    assert unlocked.post("/api/license", json={"key": "not-a-license"}).status_code == 422


def test_upload_size_limit(unlocked):
    big = b"Posting Date,Description,Amount\n" + b"01/05/2025,X,-1.00\n" * 1_500_000
    assert len(big) > 25 * 1024 * 1024
    r = _upload(unlocked, data=big)
    assert r.status_code == 413 and "MB" in r.json()["error"]


def test_preview_and_mapped_import(unlocked):
    c = unlocked
    data = b"When,What,How Much\n01/02/2025,ACME WIDGETS SUPPLY,-12.50\n01/03/2025,SOMETHING ELSE,4.00\n"
    p = c.post("/api/imports/preview", files=[("file", ("w.csv", data, "text/csv"))])
    assert p.status_code == 200 and p.json()["columns"][:3] == ["When", "What", "How Much"]
    m = c.post("/api/imports/mapped", files=[("file", ("w.csv", data, "text/csv"))],
               data={"mapping": json.dumps({"date": "When", "description": "What", "amount": "How Much"}),
                     "account_label": "Misc"})
    assert m.status_code == 200, m.text
    assert m.json()["new"] == 2


def test_auto_lock_after_idle(tmp_path):
    app = create_app(tmp_path, TOKEN, port=8765, idle_timeout=0)
    with TestClient(app, base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN}) as c:
        assert c.post("/api/vaults", json={"name": "Idle", "passphrase": PASS}).status_code == 201
        import time
        time.sleep(0.01)
        assert c.get("/api/transactions").status_code == 409


# ---------------------------------------------------------------- pay stubs (in memory)
def _stubs():
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures"
    return [("file", ("paystub_rippling.pdf", (fx / "paystub_rippling.pdf").read_bytes(), "application/pdf")),
            ("file", ("paystub_generic.txt", (fx / "paystub_generic.txt").read_bytes(), "text/plain")),
            ("spouse_file", ("paystub_adp.pdf", (fx / "paystub_adp.pdf").read_bytes(), "application/pdf"))]


def test_paystubs_need_an_unlocked_vault(client):
    r = client.post("/api/paystubs", files=_stubs()[:1])
    assert r.status_code == 409 and r.json()["locked"] is True


def test_paystubs_route(unlocked):
    r = unlocked.post("/api/paystubs", files=_stubs(), data={"filing_status": "mfj"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "ESTIMATE — not issued by employer" and body["year"] == 2025
    owners = sorted((w["owner"], w["employer"]) for w in body["w2"])
    assert owners == [("spouse", "INITECH INC"), ("you", "Acme Corp"), ("you", "Globex LLC")]
    acme = next(w for w in body["w2"] if w["employer"] == "Acme Corp")
    assert acme["boxes"]["1"] == "96708.00" and acme["box12"]["D"] == "12000.00"
    ids = {s["id"] for s in body["signals"]}
    assert {"excess_social_security", "excess_deferral", "hsa_fsa_conflict",
            "additional_medicare_not_withheld"} <= ids
    for secret in ("900-45-6789", "987-65-4321", "000123456789", "9876543210"):
        assert secret not in r.text
    # nothing was stored
    assert unlocked.get("/api/imports").json() == []


def test_paystubs_route_validation(unlocked):
    assert unlocked.post("/api/paystubs", files=_stubs()[:1], data={"owner": "boss"}).status_code == 422
    assert unlocked.post("/api/paystubs", files=_stubs()[:1], data={"frequency": "daily"}).status_code == 422
    assert unlocked.post("/api/paystubs", data={"owner": "you"}).status_code == 422


def test_paystubs_export(unlocked):
    r = unlocked.post("/api/paystubs/export/csv", files=_stubs(), data={"final": "1"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.startswith("ESTIMATE")
    assert "900-45-6789" not in r.text
    r = unlocked.post("/api/paystubs/export/xlsx", files=_stubs())
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert unlocked.post("/api/paystubs/export/pdf", files=_stubs()).status_code == 404


def _investment_files():
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures"
    return [("file", (n, (fx / n).read_bytes(), "application/octet-stream"))
            for n in ("1099_example_brokerage.pdf", "1099_sample_securities.pdf", "1099b_lots_example.csv",
                      "positions_example.csv")]


def test_investments_need_an_unlocked_vault(client):
    r = client.post("/api/investments", files=_investment_files()[:1])
    assert r.status_code == 409 and r.json()["locked"] is True


def test_investments_route(unlocked):
    r = unlocked.post("/api/investments", files=_investment_files(), data={"filing_status": "mfj"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"].startswith("SUMMARY") and body["year"] == 2025
    div = next(t for t in body["income"] if t["form"] == "1099-DIV" and t["box"] == "1a")
    assert div["amount"] == "12557.46"
    assert {g["category"] for g in body["capital_gains"]} == {"A", "B", "D", "E"}
    assert body["holdings_summary"]["market_value"] == "50000.00"
    ids = {s["id"] for s in body["signals"]}
    assert {"unrealized_losses", "basis_missing", "partnership_k1_likely"} <= ids
    for secret in ("900-45-6789", "98-7654321", "900123456", "900-234567", "Z90012345"):
        assert secret not in r.text
    assert unlocked.get("/api/imports").json() == []


def test_investments_route_validation(unlocked):
    assert unlocked.post("/api/investments", files=_investment_files()[:1],
                         data={"filing_status": "joint"}).status_code == 422
    assert unlocked.post("/api/investments", files=_investment_files()[:1], data={"year": "x"}).status_code == 422
    assert unlocked.post("/api/investments", data={"year": "2025"}).status_code == 422


def test_investments_export(unlocked):
    r = unlocked.post("/api/investments/export/csv", files=_investment_files())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.startswith("SUMMARY") and "900-45-6789" not in r.text
    r = unlocked.post("/api/investments/export/xlsx", files=_investment_files())
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert unlocked.post("/api/investments/export/pdf", files=_investment_files()).status_code == 404
