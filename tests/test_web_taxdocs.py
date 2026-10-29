# SPDX-License-Identifier: AGPL-3.0-only
"""The Pay stubs and Investments screens (web/static/screen-paystubs.js, screen-investments.js,
tax-docs.js, mock-taxdocs.js): they are served and routed, they send only fields the API reads,
they read only keys the API returns, and the demo mock answers with the same shape.
Synthetic fixtures only (tests/fixtures/make_paystubs.py, make_1099s.py)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend_fakes import install_missing
from ledgerstitch.api import create_app

TOKEN = "t" * 43
PASS = "correct horse battery"
BASE = "http://127.0.0.1:8765"
STATIC = Path(__file__).parent.parent / "ledgerstitch" / "web" / "static"
FX = Path(__file__).parent / "fixtures"
SCREENS = ("screen-paystubs.js", "screen-investments.js", "tax-docs.js", "mock-taxdocs.js")
NODE = shutil.which("node")


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    install_missing(monkeypatch)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, port=8765), base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN}) as c:
        r = c.post("/api/vaults", json={"name": "Demo Studio", "passphrase": PASS})
        assert r.status_code == 201, r.text
        yield c


def js(name: str) -> str:
    return (STATIC / name).read_text("utf-8")


def stubs():
    return [("file", ("paystub_rippling.pdf", (FX / "paystub_rippling.pdf").read_bytes(), "application/pdf")),
            ("spouse_file", ("paystub_adp.pdf", (FX / "paystub_adp.pdf").read_bytes(), "application/pdf"))]


def investment_files():
    return [("file", (n, (FX / n).read_bytes(), "application/octet-stream"))
            for n in ("1099_example_brokerage.pdf", "1099b_lots_example.csv", "positions_example.csv")]


# ---------------------------------------------------------------- served and routed
@pytest.mark.parametrize("name", SCREENS)
def test_screen_files_are_served(client, name):
    r = client.get(f"/{name}")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]


def test_screens_are_in_the_nav_and_the_router():
    shell, app = js("shell.js"), js("app.js")
    assert "{ id: 'paystubs', label: 'Pay stubs'" in shell and "{ id: 'investments', label: 'Investments'" in shell
    assert "paystubs: { render: renderPaystubs" in app and "investments: { render: renderInvestments" in app
    assert "from './screen-paystubs.js'" in app and "from './screen-investments.js'" in app
    assert "from './mock-taxdocs.js'" in js("mock.js")


def test_screens_say_nothing_is_saved():
    assert "Processed in memory, not saved" in js("tax-docs.js")
    for name in ("screen-paystubs.js", "screen-investments.js"):
        assert "memoryNote()" in js(name)


# ---------------------------------------------------------------- the request the screens build
PAYSTUB_FIELDS = {"file", "spouse_file", "owner", "frequency", "filing_status", "final", "spouse_no_earned_income"}
INVESTMENT_FIELDS = {"file", "filing_status"}


@pytest.mark.parametrize("name,allowed", [("screen-paystubs.js", PAYSTUB_FIELDS), ("screen-investments.js", INVESTMENT_FIELDS)])
def test_screens_send_only_fields_the_api_reads(name, allowed):
    sent = set(re.findall(r"f\.append\((?:[^,]*\?\s*)?'([a-z_]+)'(?:\s*:\s*'([a-z_]+)')?", js(name)))
    fields = {x for pair in sent for x in pair if x} - {"sample"}   # "sample" only goes to the demo mock
    assert fields and fields <= allowed, fields - allowed
    assert re.search(r"if \(MOCK && !files\.length\) f\.append\('sample'", js(name))


def test_the_paystub_request_the_screen_builds(client):
    # what formData() sends with every option set: you + spouse files, owner, flags as "1"
    data = {"owner": "you", "frequency": "biweekly", "filing_status": "mfj", "final": "1", "spouse_no_earned_income": "1"}
    r = client.post("/api/paystubs", files=stubs(), data=data)
    assert r.status_code == 200, r.text
    assert {w["owner"] for w in r.json()["w2"]} == {"you", "spouse"}
    assert all(w["final"] for w in r.json()["w2"])
    for fmt, magic in (("csv", b"ESTIMATE"), ("xlsx", b"PK")):
        e = client.post(f"/api/paystubs/export/{fmt}", files=stubs(), data=data)
        assert e.status_code == 200 and e.content.startswith(magic)
        assert "filename=" in e.headers["content-disposition"]


def test_the_investments_request_the_screen_builds(client):
    r = client.post("/api/investments", files=investment_files(), data={"filing_status": "mfj"})
    assert r.status_code == 200, r.text
    for fmt in ("csv", "xlsx"):
        e = client.post(f"/api/investments/export/{fmt}", files=investment_files())
        assert e.status_code == 200 and "filename=" in e.headers["content-disposition"]


# ---------------------------------------------------------------- the keys the screens read
# Every report key the screens use, as paths into the API's JSON ([] = each list item).
PAYSTUB_KEYS = ["year", "filing_status", "label", "disclaimer", "warnings", "signals", "stubs[].source", "stubs[].employer",
                "stubs[].pay_date", "stubs[].warnings", "w2[].owner", "w2[].employer", "w2[].final", "w2[].periods_remaining",
                "w2[].frequency", "w2[].boxes", "w2[].box12", "w2[].box13_retirement_plan", "w2[].box14", "w2[].notes",
                "signals[].severity", "signals[].owner", "signals[].amount", "signals[].title", "signals[].explanation",
                "signals[].citation"]
INVESTMENT_KEYS = ["year", "label", "disclaimer", "warnings", "plus_features", "short_term_net", "long_term_net",
                   "statements[].source", "statements[].kind", "statements[].payer", "statements[].tax_year", "statements[].account",
                   "income[].form", "income[].box", "income[].label", "income[].amount", "income[].destination", "income[].payers",
                   "capital_gains[].category", "capital_gains[].proceeds", "capital_gains[].cost_basis",
                   "capital_gains[].wash_sale_disallowed", "capital_gains[].gain", "capital_gains[].label",
                   "holdings[].symbol", "holdings[].description", "holdings[].account", "holdings[].market_value",
                   "holdings[].cost_basis", "holdings[].unrealized_gain", "holdings[].asset_class", "holdings[].source",
                   "holdings_summary.as_of", "holdings_summary.positions", "holdings_summary.market_value",
                   "holdings_summary.unrealized_gain", "holdings_summary.unrealized_gains", "holdings_summary.unrealized_losses",
                   "holdings_summary.by_class[].name", "holdings_summary.by_class[].pct", "holdings_summary.top5_pct",
                   "holdings_summary.concentrated[].name", "signals[].severity", "signals[].title", "signals[].amount"]


def missing(doc, keys):
    out = []
    for path in keys:
        nodes = [doc]
        for part in path.split("."):
            each = part.endswith("[]")
            k = part[:-2] if each else part
            nxt = []
            for n in nodes:
                if not isinstance(n, dict) or k not in n:
                    out.append(path)
                    nxt = []
                    break
                v = n[k]
                nxt.extend(v if each else [v])
            nodes = nxt
            if not nodes:
                break
    return sorted(set(out))


def test_the_api_returns_every_key_the_screens_read(client):
    ps = client.post("/api/paystubs", files=stubs()).json()
    iv = client.post("/api/investments", files=investment_files()).json()
    assert ps["w2"] and ps["signals"] and ps["stubs"]
    assert iv["income"] and iv["capital_gains"] and iv["holdings"] and iv["statements"]
    assert missing(ps, PAYSTUB_KEYS) == []
    assert missing(iv, INVESTMENT_KEYS) == []


def test_screens_only_use_keys_listed_here():
    # a new `report.x` / `w.x` in the screens must be added to the lists above (and so be checked)
    used = set()
    for name, var in (("screen-paystubs.js", r"(?:report|w|st|s)"), ("screen-investments.js", r"(?:report|hs|r|x|c|st)")):
        code = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", js(name))          # not CSS selectors inside strings
        used |= set(re.findall(rf"\b{var}\.([a-z][a-z0-9_]+)\b", code))
    listed = {seg.replace("[]", "") for p in PAYSTUB_KEYS + INVESTMENT_KEYS for seg in p.split(".")}
    js_names = {"name", "value", "checked", "length", "file", "owner", "isCurrent", "message"}
    assert used - listed - js_names == set()


# ---------------------------------------------------------------- browser syntax and the demo mock
@pytest.mark.skipif(not NODE, reason="node is not installed")
@pytest.mark.parametrize("name", SCREENS + ("app.js", "shell.js", "mock.js"))
def test_browser_modules_parse(name):
    r = subprocess.run([NODE, "--input-type=module", "--check"], input=js(name), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_demo_mock_matches_the_api_shape(client):
    script = (
        f"import {{ taxDocRoutes }} from {json.dumps((STATIC / 'mock-taxdocs.js').as_uri())};\n"
        "const json = (d, s = 200) => new Response(JSON.stringify(d), { status: s, headers: { 'content-type': 'application/json' } });\n"
        "const err = (s, e) => json({ error: e }, s);\n"
        "const out = {};\n"
        "for (const k of ['paystubs', 'investments']) {\n"
        "  const f = new FormData(); f.append('sample', '1');\n"
        "  out[k] = await taxDocRoutes(`/api/${k}`, 'POST', { body: f }, { json, err }).json();\n"
        "  const none = await taxDocRoutes(`/api/${k}`, 'POST', { body: new FormData() }, { json, err });\n"
        "  out[k + '_empty'] = none.status;\n"
        "  const x = await taxDocRoutes(`/api/${k}/export/csv`, 'POST', { body: f }, { json, err });\n"
        "  out[k + '_export'] = x.headers.get('content-disposition');\n"
        "}\n"
        "console.log(JSON.stringify(out));\n")
    r = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    if r.returncode and "FormData is not defined" in r.stderr:
        pytest.skip("this node has no FormData")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    ps, iv = out["paystubs"], out["investments"]
    # stubs/statements are per uploaded file, and the sample run uploads none
    assert missing(ps, [k for k in PAYSTUB_KEYS if not k.startswith("stubs[]")]) == []
    assert missing(iv, [k for k in INVESTMENT_KEYS if not k.startswith("statements[]")]) == []
    assert out["paystubs_empty"] == out["investments_empty"] == 422
    assert "attachment" in out["paystubs_export"] and "attachment" in out["investments_export"]
    real = client.post("/api/paystubs", files=stubs()).json()
    assert ps["label"] == real["label"] and ps["disclaimer"] == real["disclaimer"]
    real = client.post("/api/investments", files=investment_files()).json()
    assert iv["label"] == real["label"] and iv["disclaimer"] == real["disclaimer"]
    assert iv["plus_features"] == real["plus_features"]
