# SPDX-License-Identifier: AGPL-3.0-only
"""Core plugin system, feature gating, editions and the AI redaction boundary."""
from __future__ import annotations

import json
import re
import socket
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend_fakes import SAMPLE_CSV, install_missing
from ledgerstitch import editions, plugins, store
from ledgerstitch.ai.categorizer import build_ai_payload, categorize, scrub
from ledgerstitch.api import create_app
from ledgerstitch.models import Direction, Source, Transaction
from ledgerstitch.security.license import License
from ledgerstitch.security.vault import Vault
from ledgerstitch.taxonomy import DEFAULT_CATEGORIES

PASS = "correct horse battery"
TOKEN = "t" * 43
BASE = "http://127.0.0.1:8765"
PRO = License("a@b.co", "pro", None, True)
ENTERPRISE = License("a@b.co", "enterprise", None, True)
FREE = License("", "free", None, False)
EXPIRED_PRO = License("a@b.co", "pro", None, False)
# plans a test add-on declares with registry.license_plans
SILVER = License("a@b.co", "silver", None, True)
GOLD = License("a@b.co", "gold", None, True)
OLD_GOLD = License("a@b.co", "old_gold", None, True)               # alias of gold
TEAM = License("a@b.co", "team", None, True)


# ---------------------------------------------------------------- a fake AI add-on
class FakeAI:
    """Records exactly what the core hands to an add-on."""

    def __init__(self, answer=None):
        self.calls = []
        self.feedback = []
        self.answer = answer or (lambda d: {"category": "Professional Services", "lane": "business",
                                            "schedule_c": "L17", "confidence": 0.93, "model": "fake:m1"})

    def register(self, registry):
        registry.feature("demo.ai", tier="pro", label="Demo AI")
        registry.settings({"fake_api_key": None}, {"fake_api_key"}, {}, secrets={"fake_api_key"})
        registry.ai_providers("fakecloud")
        registry.vault_migrations({"stats": "ai_model_stats"})
        registry.ai_categorizer(self.categorize, feature="demo.ai")
        registry.ai_feedback(lambda st, model, agreed: self.feedback.append((model, agreed)))
        registry.ai_preview(lambda d, c, s, st: {"extra": {"n": len(d)}})

        def routes(app, ws_getter):
            @app.get("/api/demo/models")
            def models():
                ws_getter()                       # 409 while the vault is locked
                return {"models": ["fake"]}
        registry.api_routes(routes, gated={"/api/demo/models": "demo.ai"})

    def categorize(self, descriptors, categories, settings, plugin_store):
        self.calls.append({"descriptors": descriptors, "categories": categories,
                           "settings": settings, "store": plugin_store})
        plugin_store.put_json("stats", {"runs": len(self.calls)})
        return {d: self.answer(d) for d in descriptors}


@pytest.fixture
def fake_ai(fresh_registry):
    ai = FakeAI()
    assert plugins.register_local(ai.register, namespace="fakepro")
    return ai


def tx(desc, amount="1234.56", d=date(2025, 7, 4)):
    return Transaction("chase-4012", d, desc, Decimal(amount), Direction.OUT)


# ---------------------------------------------------------------- discovery
class _EP:
    def __init__(self, name, value, target):
        self.name, self.value, self._t = name, value, target

    def load(self):
        if isinstance(self._t, Exception):
            raise self._t
        return self._t


def test_entry_point_discovery_and_broken_plugins_are_isolated(monkeypatch, fresh_registry):
    good = SimpleNamespace(NAMESPACE="good", register=lambda r: r.feature("x.good", tier="pro"))
    bad = SimpleNamespace(register=lambda r: 1 / 0)
    monkeypatch.setattr(plugins, "_loaded", False)
    monkeypatch.setattr(plugins, "_entry_points", lambda: [
        _EP("good", "good_pkg.plugin", good), _EP("bad", "bad_pkg.plugin", bad),
        _EP("missing", "nope.plugin", ImportError("no module"))])
    reg = plugins.load()
    assert set(reg.plugins) == {"good"} and "x.good" in reg.features
    assert any(e.startswith("bad:") for e in reg.errors) and any(e.startswith("missing:") for e in reg.errors)
    assert plugins.load() is reg                     # idempotent


def test_community_edition_skips_entry_points(monkeypatch, fresh_registry):
    called = []
    monkeypatch.setattr(plugins, "_loaded", False)
    monkeypatch.setattr(plugins, "_entry_points", lambda: called.append(1) or [])
    editions.configure("community")
    try:
        plugins.load()
        assert called == [] and not plugins.registry().has_plugins()
        assert editions.current_edition() == "community"
    finally:
        editions.configure(None)


def test_configure_from_env_and_build_constant():
    try:
        assert editions.configure(env={"LEDGERSTITCH_EDITION": "PRO"}) == "pro"
        assert editions.current_edition() == "pro"
        editions.configure("Not An Edition!")              # malformed: ignored
        assert editions.declared_edition() == "pro"
        assert editions.configure("hosted") == "hosted"   # an add-on build may name its own edition
    finally:
        editions.configure(None)
    assert editions.EDITION in editions.EDITIONS


def test_multiple_plugins_get_separate_namespaces(fresh_registry, tmp_path):
    plugins.register_local(lambda r: r.feature("one.x", tier="pro"), namespace="pro")
    plugins.register_local(lambda r: r.feature("two.x", tier="gold"), namespace="tax")
    assert not plugins.register_local(lambda r: None, namespace="pro")      # duplicate ignored
    assert not plugins.register_local(lambda r: None, namespace="Bad Name!")
    v = Vault.create(tmp_path / "v", PASS, "X")
    ws = store.Workspace(v)
    ws.plugin_store("pro").put_json("k", {"a": 1})
    ws.plugin_store("tax").put_json("k", {"b": 2})
    assert ws.plugin_store("pro").get_json("k") == {"a": 1} and ws.plugin_store("tax").get_json("k") == {"b": 2}
    assert {"plugin:pro:k", "plugin:tax:k"} <= set(v.keys())
    with pytest.raises(plugins.PluginError):
        ws.plugin_store("pro").get_json("../escape")


def test_plugin_cannot_take_core_settings(fresh_registry):
    assert not plugins.register_local(lambda r: r.settings({"business_name": "x"}), namespace="evil")
    assert "evil" not in plugins.registry().plugins and plugins.registry().errors
    plugins.register_local(lambda r: r.settings({"a_key": 1}), namespace="one")
    assert not plugins.register_local(lambda r: r.settings({"a_key": 2}), namespace="two")


# ---------------------------------------------------------------- vault key migration
def test_plugin_store_reads_legacy_key_then_migrates(tmp_path):
    v = Vault.create(tmp_path / "v", PASS, "X")
    v.put_json("ai_model_stats", {"m": {"calls": 3}})
    ps = plugins.PluginStore(v, "pro", legacy={"model_stats": "ai_model_stats"})
    assert ps.get_json("model_stats") == {"m": {"calls": 3}}         # read-through
    assert "plugin:pro:model_stats" not in v.keys()
    ps.put_json("model_stats", {"m": {"calls": 4}})
    assert v.get_json("ai_model_stats") is None                      # moved on first write
    assert v.get_json("plugin:pro:model_stats") == {"m": {"calls": 4}}
    assert ps.keys() == ["model_stats"]


def test_memory_plugin_store_isolated_copies():
    ms = plugins.MemoryPluginStore("pro")
    d = {"a": [1]}
    ms.put_json("x", d)
    d["a"].append(2)
    assert ms.get_json("x") == {"a": [1]} and ms.get_json("missing", 5) == 5


# ---------------------------------------------------------------- feature gating
def test_feature_gating_matrix(fresh_registry):
    reg = fresh_registry
    # community: nothing installed, only core features
    assert editions.feature_enabled("import", None, reg) and editions.feature_enabled("vault", FREE, reg)
    assert not editions.feature_enabled("demo.ai", PRO, reg)          # add-on missing
    st = editions.features_status(PRO, reg)
    assert set(st) == set(editions.CORE_FEATURES) and all(v["unlocked"] for v in st.values())
    # add-ons installed, unlicensed / expired
    plugins.register_local(lambda r: r.feature("demo.ai", tier="pro"), namespace="pro")

    def plans(r):
        r.feature("demo.gold", tier="gold")
        r.feature("demo.team", tier="team")
        r.license_plans({"silver": ["pro"], "gold": ["pro", "gold"], "team": ["pro", "team"]},
                        labels={"gold": "Gold"}, aliases={"old_gold": "gold"})
    plugins.register_local(plans, namespace="plans")
    for lic in (None, FREE, EXPIRED_PRO):
        assert not editions.feature_enabled("demo.ai", lic, reg)
        assert editions.features_status(lic, reg)["demo.ai"]["available"] is True
    for lic in (PRO, SILVER, GOLD, OLD_GOLD, TEAM, ENTERPRISE):
        assert editions.feature_enabled("demo.ai", lic, reg)
    lics = (PRO, SILVER, GOLD, OLD_GOLD, TEAM, ENTERPRISE)
    assert {lic.plan for lic in lics if editions.feature_enabled("demo.gold", lic, reg)} == {"gold", "old_gold", "enterprise"}
    assert {lic.plan for lic in lics if editions.feature_enabled("demo.team", lic, reg)} == {"team", "enterprise"}   # not linear
    assert editions.plans_for_tier("gold", reg) == {"gold", "enterprise"}
    assert editions.plan_labels(reg)["gold"] == "Gold" and editions.normalize_plan("old_gold", reg) == "gold"
    assert not plugins.register_local(lambda r: r.feature("x", tier="Not A Tier"), namespace="bad")
    assert not plugins.register_local(lambda r: r.license_plans({"free": ["pro"]}), namespace="bad2")
    assert editions.current_edition(reg) == "pro"


# ---------------------------------------------------------------- categorize gating + privacy boundary
def test_categorize_calls_ai_only_when_installed_unlocked_and_enabled(fake_ai):
    def run(settings, lic):
        txns = [tx("ZORBLAX CONSULTING 998812")]
        return txns, categorize(txns, [], settings, DEFAULT_CATEGORIES, license=lic)
    run({"ai_enabled": False}, PRO)
    _, res = run({"ai_enabled": True, "provider": "fakecloud"}, FREE)
    assert fake_ai.calls == [] and any("needs a license" in w for w in res.warnings)
    _, res = run({"ai_enabled": True, "provider": "off"}, FREE)
    assert fake_ai.calls == [] and res.warnings == []
    txns, res = run({"ai_enabled": True}, PRO)
    assert len(fake_ai.calls) == 1 and res.by_ai == 1 and res.sent_descriptors == 1
    t = txns[0]
    assert t.source == Source.AI and t.category == "Professional Services" and t.ai_model == "fake:m1"


def test_plugin_receives_only_redacted_scrubbed_strings(fake_ai):
    """Redaction boundary: the add-on never sees a Transaction, an amount, a date or a raw description."""
    from ledgerstitch.security import redact
    raw = ["ZELLE TO JOHN SMITH 08/14 REF 99812345", "SQ *BLUE BOTTLE #0423 $45.10 07/04/2025",
           "AMAZON MKTPL*BJ1M70883 AMZN.COM/BILL WA", "WIDGETCO 4411 XXXX4012"]
    txns = [tx(d, amount=a) for d, a in zip(raw, ("1234.56", "45.10", "987.65", "9.99"))]
    settings = {"ai_enabled": True, "fake_api_key": "k", "business_name": "Secret Client LLC",
                "business_profile": "photo studio", "license_key": "LS1.secret", "tax_year": 2025}
    categorize(txns, [], settings, DEFAULT_CATEGORIES, license=PRO)
    [call] = fake_ai.calls
    sent = call["descriptors"]
    allowed = {scrub(redact.redact(r).text) for r in raw}
    assert isinstance(sent, list) and sent and all(type(d) is str for d in sent)
    assert set(sent) <= allowed and sorted(sent) == build_ai_payload([tx(r) for r in raw])
    # same unique descriptors, most important (highest spend) first; the order is all it learns
    assert sent == [scrub(redact.redact(r).text) for r in (raw[0], raw[2], raw[1], raw[3])]
    blob = json.dumps(call["descriptors"]) + json.dumps(call["categories"]) + json.dumps(call["settings"])
    for leak in ("1234", "45.10", "987.65", "2025", "07/04", "JOHN", "SMITH", "4012", "chase-4012",
                 "Secret Client", "LS1.secret"):
        assert leak not in blob, leak
    assert all(set(c) == {"name", "lane", "schedule_c"} for c in call["categories"])
    assert set(call["settings"]) == {"ai_enabled", "fake_api_key", "business_profile"}   # no business_name
    assert isinstance(call["store"], plugins.PluginStore)


def test_plugin_answers_are_validated_by_the_core(fresh_registry):
    def answer(d):
        return {"category": "Totally Made Up", "lane": "sideways", "schedule_c": "L99", "confidence": 7}
    ai = FakeAI(answer)
    plugins.register_local(ai.register, namespace="fakepro")
    real = plugins.registry().ai_categorizers[0][1]
    plugins.registry().ai_categorizers[0] = ("fakepro", lambda d, c, s, st: {
        **real(d, c, s, st), "NOT SENT": {"category": "Shopping", "lane": "personal", "confidence": 1}})
    t = tx("QWERTY THING")
    res = categorize([t], [], {"ai_enabled": True}, DEFAULT_CATEGORIES, license=PRO)
    assert t.category == "Uncategorized" and t.lane.value == "review" and t.confidence <= 0.5
    assert res.new_categories == []


def test_broken_plugin_is_a_warning(fresh_registry):
    def reg(r):
        r.feature("demo.ai")
        r.ai_categorizer(lambda *a: (_ for _ in ()).throw(RuntimeError("boom 4012")), feature="demo.ai")
    plugins.register_local(reg, namespace="broken")
    t = tx("QWERTY THING")
    res = categorize([t], [], {"ai_enabled": True}, DEFAULT_CATEGORIES, license=PRO)
    assert t.category == "Uncategorized" and any("RuntimeError" in w for w in res.warnings)
    assert not any("4012" in w for w in res.warnings)


# ---------------------------------------------------------------- Community: zero network
_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


def _loopback(host) -> bool:
    import ipaddress
    if host is None or host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(str(host).strip("[]").split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture
def no_network(monkeypatch):
    """Community may only ever talk to this computer: any non-loopback connection attempt
    (or name lookup) fails the test, even if the code under test swallowed the error."""
    attempts: list = []
    real_connect, real_connect_ex, real_gai = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def check(addr):
        host = addr[0] if isinstance(addr, tuple) else None      # AF_UNIX paths are local
        if isinstance(addr, tuple) and not _loopback(host):
            attempts.append(addr)
            raise AssertionError(f"non-loopback network call attempted: {addr!r}")

    def connect(self, addr):
        check(addr)
        return real_connect(self, addr)

    def connect_ex(self, addr):
        check(addr)
        return real_connect_ex(self, addr)

    def getaddrinfo(host, *a, **k):
        if not _loopback(host.decode() if isinstance(host, bytes) else host):
            attempts.append(("dns", host))
            raise AssertionError(f"non-loopback name lookup attempted: {host!r}")
        return real_gai(host, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield attempts
    assert attempts == [], f"non-loopback network calls: {attempts}"


class _FakeOllama:
    """A tiny Ollama /api/chat server on 127.0.0.1 that records what it receives."""

    def __init__(self, status=200, headers=None):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        outer = self
        self.bodies: list = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.bodies.append(body)
                user = body["messages"][1]["content"]
                items = json.loads(user[user.index('{"items"'):])["items"]
                out = {"message": {"content": "Sure! ```json\n" + json.dumps({"results": [
                    {"id": it["id"], "category": "Professional Services", "lane": "business",
                     "schedule_c": "L17", "confidence": 0.91, "new_category": False} for it in items]})
                    + "\n```"}}
                data = json.dumps(out).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_ollama():
    srv = _FakeOllama()
    yield srv
    srv.close()


def test_community_categorize_makes_zero_network_calls(community, no_network, tmp_path):
    # even with an add-on provider "enabled" in stored settings and a paid license, Community
    # has no add-on AI: nothing is sent anywhere
    txns = [tx("ZORBLAX CONSULTING 998812"), tx("NETFLIX.COM", "15.99")]
    res = categorize(txns, [], {"ai_enabled": True, "provider": "fakecloud"}, DEFAULT_CATEGORIES,
                     license=PRO)
    assert res.by_ai == 0 and res.sent_descriptors == 0
    v = Vault.create(tmp_path / "v", PASS, "X")
    v.put_json("settings", {"ai_enabled": True, "provider": "fakecloud",
                            "addon_list": [{"provider": "fakecloud", "model": "m"}]})
    ws = store.Workspace(v)
    ws.add_import("bank.csv", SAMPLE_CSV, account_label="Biz")
    r = ws.categorize()
    assert r.by_ai == 0 and r.report is None


def test_community_local_ollama_uses_loopback_only(community, no_network, fake_ollama, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://10.9.8.7:3128")          # must be ignored (trust_env=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://10.9.8.7:3128")
    from ledgerstitch.security import redact
    raw = ["ZORBLAX CONSULTING 998812", "ZELLE TO JOHN SMITH 08/14 REF 99812345"]
    txns = [tx(raw[0]), tx(raw[1], "250.00")]
    settings = {"ai_enabled": True, "provider": "ollama", "model": "llama3.1",
                "ollama_url": fake_ollama.url.replace("127.0.0.1", "localhost"),
                "business_name": "Secret Client LLC"}
    res = categorize(txns, [], settings, DEFAULT_CATEGORIES)          # no license needed: Community
    assert res.by_ai >= 1 and res.sent_descriptors == 2
    assert txns[0].ai_model == "ollama:llama3.1" and txns[0].source == Source.AI
    blob = json.dumps(fake_ollama.bodies)
    for leak in ("998812", "JOHN", "SMITH", "250.00", "1234.56", "2025", "Secret Client", "chase-4012"):
        assert leak not in blob, leak
    allowed = {scrub(redact.redact(r).text) for r in raw}
    user = fake_ollama.bodies[0]["messages"][1]["content"]
    sent = [i["text"] for i in json.loads(user[user.index('{"items"'):])["items"]]
    assert set(sent) <= allowed


def test_local_ollama_rejects_remote_hosts_before_connecting(community, no_network):
    from ledgerstitch.ai.local_ollama import validate_loopback_url
    for ok in ("http://127.0.0.1:11434", "localhost:11434", "http://[::1]:11434", "http://127.0.0.2",
               "https://localhost:8443/", ""):
        validate_loopback_url(ok)
    for bad in ("http://192.168.1.5:11434", "http://ollama.com", "https://ollama.com/api",
                "http://127.0.0.1.evil.com", "http://localhost.evil.com", "http://user:pw@127.0.0.1",
                "ftp://127.0.0.1", "http://0.0.0.0:11434", "http://[::ffff:8.8.8.8]", "http://10.0.0.1",
                "http://127.0.0.1:11434/../x", "http://127.0.0.1:99999"):
        with pytest.raises(ValueError):
            validate_loopback_url(bad)
    # a remote URL left in an old vault: refused at request time, nothing sent anywhere
    t = tx("ZORBLAX CONSULTING")
    res = categorize([t], [], {"ai_enabled": True, "provider": "ollama", "ollama_url": "http://10.0.0.5:11434"},
                     DEFAULT_CATEGORIES)
    assert res.by_ai == 0 and any("this computer" in w for w in res.warnings)


def test_local_ollama_does_not_follow_redirects(community, no_network):
    srv = _FakeOllama(status=302, headers={"Location": "http://203.0.113.9/api/chat"})
    try:
        t = tx("ZORBLAX CONSULTING")
        res = categorize([t], [], {"ai_enabled": True, "provider": "ollama", "ollama_url": srv.url},
                         DEFAULT_CATEGORIES)
        assert res.by_ai == 0 and any("redirect" in w for w in res.warnings)
    finally:
        srv.close()


def test_local_ollama_settings_and_api(community, no_network, fake_ollama, tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        assert c.get("/api/status").json()["features"]["ai.local"] == {
            "available": True, "unlocked": True, "tier": "community",
            "label": editions.CORE_FEATURES["ai.local"]}
        c.post("/api/vaults", json={"name": "Acme", "passphrase": PASS})
        r = c.put("/api/settings", json={"ollama_url": "http://ollama.example.com:11434"})
        assert r.status_code == 422 and "this computer" in r.json()["error"]
        assert c.put("/api/settings", json={"provider": "anthropic"}).status_code == 422
        r = c.put("/api/settings", json={"ai_enabled": True, "provider": "ollama", "model": "llama3.1",
                                         "ollama_url": fake_ollama.url})
        assert r.status_code == 200, r.text
        assert c.post("/api/ai/local-test").json()["ok"] is True
        c.post("/api/imports", files=[("file", ("b.csv", SAMPLE_CSV, "text/csv"))], data={"account_label": "Biz"})
        assert any(t["source"] == "ai" for t in c.get("/api/transactions").json()["items"])
        st = c.get("/api/status").json()
        assert st["ai"] == {"enabled": True, "provider": "ollama", "model": "llama3.1"}
        p = c.get("/api/ai/preview").json()
        assert p["local"] is True and p["enabled"] is True and "[system]" in p["full_prompt"]


# ---------------------------------------------------------------- API: status, source, 402s, settings
def _client(tmp_path, monkeypatch, license=None):
    install_missing(monkeypatch)
    if license is not None:
        monkeypatch.setattr(store, "_current_plan", lambda key: license)
    app = create_app(tmp_path, TOKEN, port=8765)
    c = TestClient(app, base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN})
    return c


def test_community_api_status_source_and_no_addon_routes(community, no_network, tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        st = c.get("/api/status").json()
        assert st["edition"] == "community"
        assert set(st["features"]) == set(editions.CORE_FEATURES) and st["features"]["import"]["unlocked"]
        assert st["ui"] == {"screens": [], "cards": [], "settings": []}
        assert set(st["plans"]) == {"free", "community", "pro", "enterprise"}
        src = c.get("/api/source").json()
        assert src["license"] == "AGPL-3.0-only" and src["url"].startswith("https://") and src["version"]
        assert c.get("/api/demo/models").status_code == 404            # no add-on, no route
        assert c.get("/addons/index.json").json()["addons"] == []
        assert c.post("/api/vaults", json={"name": "Acme", "passphrase": PASS}).status_code == 201
        c.post("/api/imports", files=[("file", ("b.csv", SAMPLE_CSV, "text/csv"))], data={"account_label": "Biz"})
        p = c.get("/api/ai/preview").json()
        assert p["descriptors"] and p["payload"] and p["unlocked"] is False and p["available"] is False
        r = c.put("/api/settings", json={"addon_list": []})
        assert r.status_code == 422 and "while it is installed" in r.json()["error"]
        assert c.put("/api/settings", json={"anthropic_api_key": "x"}).status_code == 422
        assert c.get("/api/status").json()["ai"]["enabled"] is False


def test_pro_installed_but_unlicensed_402_then_unlocked(fake_ai, tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, license=FREE) as c:
        assert c.post("/api/vaults", json={"name": "Acme", "passphrase": PASS}).status_code == 201
        r = c.get("/api/demo/models")
        assert r.status_code == 402 and r.json()["installed"] is True and r.json()["feature"] == "demo.ai"
        assert c.get("/api/status").json()["features"]["demo.ai"] == {
            "available": True, "unlocked": False, "tier": "pro", "label": "Demo AI"}
        # add-on settings are accepted once the add-on is loaded; its secrets stay hidden
        assert c.put("/api/settings", json={"fake_api_key": "SUPERSECRET", "ai_enabled": True}).status_code == 200
        assert "SUPERSECRET" not in c.get("/api/settings").text
        assert c.get("/api/settings").json()["has_fake_api_key"] is True
        assert "extra" not in c.get("/api/ai/preview").json()
    with _client(tmp_path, monkeypatch, license=PRO) as c:
        c.post("/api/unlock", json={"path": c.get("/api/vaults").json()[0]["path"], "passphrase": PASS})
        assert c.get("/api/demo/models").json() == {"models": ["fake"]}
        assert c.put("/api/settings", json={"provider": "fakecloud"}).status_code == 200
        st = c.get("/api/status").json()
        assert st["edition"] == "pro" and st["features"]["demo.ai"]["unlocked"] is True
        assert st["ai"]["enabled"] is True and st["ai"]["provider"] == "fakecloud"
        c.post("/api/imports", files=[("file", ("b.csv", SAMPLE_CSV, "text/csv"))], data={"account_label": "Biz"})
        assert fake_ai.calls                               # AI ran during import
        p = c.get("/api/ai/preview").json()
        assert p["unlocked"] is True and p["extra"] == {"n": p["count"]}
        items = c.get("/api/transactions").json()["items"]
        ai_row = next(t for t in items if t["source"] == "ai")
        c.patch(f"/api/transactions/{ai_row['id']}", json={"category": "Office Supplies"})
        assert fake_ai.feedback == [("fake:m1", False)]


def test_vault_locked_add_on_route_still_409(fake_ai, tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, license=PRO) as c:
        assert c.get("/api/demo/models").status_code == 409


# ---------------------------------------------------------------- vault folder ids / labels
def test_new_vaults_use_random_ids_and_hidden_labels(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        assert c.post("/api/vaults", json={"name": "Rivera Landscaping LLC", "passphrase": PASS}).status_code == 201
        c.post("/api/lock")
        assert c.post("/api/vaults", json={"name": "Visible Co", "passphrase": PASS,
                                           "label": "Client 02"}).status_code == 201
        c.post("/api/lock")
        vaults = c.get("/api/vaults").json()
        assert len(vaults) == 2
        for v in vaults:
            assert re.fullmatch(r"ws-[0-9a-f]{8}", v["slug"]) and v["created"]
        hidden = next(v for v in vaults if v["label_hidden"])
        assert hidden["name"] == "" and hidden["label"] == ""
        assert next(v for v in vaults if not v["label_hidden"])["name"] == "Client 02"
        on_disk = b""
        for p in (tmp_path / "vaults").rglob("*"):
            on_disk += p.name.encode() + (p.read_bytes() if p.is_file() else b"")
        assert b"Rivera" not in on_disk and b"rivera" not in on_disk and b"Visible Co" not in on_disk
        r = c.post("/api/unlock", json={"path": hidden["path"], "passphrase": PASS})
        assert r.json()["vault_name"] == "Rivera Landscaping LLC"
        assert next(v for v in c.get("/api/vaults").json() if v["unlocked"])["name"] == "Rivera Landscaping LLC"


def test_legacy_named_vault_folders_still_open(tmp_path, monkeypatch):
    legacy = tmp_path / "vaults" / "acme-studio.vault"
    legacy.parent.mkdir(parents=True)
    Vault.create(legacy, PASS, "Acme Studio", label="Acme Studio").lock()
    with _client(tmp_path, monkeypatch) as c:
        [v] = c.get("/api/vaults").json()
        assert v["name"] == "Acme Studio" and v["slug"] == "acme-studio"
        assert c.post("/api/unlock", json={"name": "Acme Studio", "passphrase": PASS}).status_code == 200


# ---------------------------------------------------------------- core / add-on boundary
def test_core_never_imports_proprietary_packages():
    """The AGPL core imports nothing from the proprietary add-on packages."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "ledgerstitch"
    bad = []
    for p in root.rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text("utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "import_module":
                names = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            bad += [f"{p.name}: {n}" for n in names if n.split(".")[0].startswith("ledgerstitch_")]
    assert bad == []


def test_core_python_files_carry_spdx_header():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "ledgerstitch"
    missing = [str(p.relative_to(root)) for p in root.rglob("*.py")
               if "SPDX-License-Identifier: AGPL-3.0-only" not in "".join(p.read_text("utf-8").splitlines(True)[:3])]
    assert missing == []


# ---------------------------------------------------------------- paid core features (2026-10-19 trim)
def test_the_core_has_only_free_features(fresh_registry):
    """Every feature of the core is free; paid features exist only where an add-on declares them."""
    reg = fresh_registry
    assert {"import", "reconcile", "rules", "review", "vault", "reports", "exports", "ai.local"} == set(editions.CORE_FEATURES)
    st = editions.features_status(FREE, reg)
    assert all(v == {"available": True, "unlocked": True, "tier": "community", "label": editions.CORE_FEATURES[k]}
               for k, v in st.items())
    assert not hasattr(editions, "CORE_PAID_FEATURES") and not hasattr(editions, "ADDON_FEATURES")
    assert editions.locked_reason("demo.ai", PRO, reg)["installed"] is False
    plugins.register_local(lambda r: r.feature("demo.ai", tier="pro", label="Demo AI"), namespace="demo")
    r = editions.locked_reason("demo.ai", FREE, reg)
    assert r == {"error": "Demo AI needs a license that includes it.", "feature": "demo.ai", "upgrade": True,
                 "installed": True}
    assert editions.locked_reason("demo.ai", PRO, reg) is None


def test_signup_url_env_override_and_placeholder(monkeypatch):
    monkeypatch.setattr(editions, "_signup_override", None)
    monkeypatch.setattr(editions, "SITE_URL", "{{SITE_URL}}")
    assert editions.signup_url() is None                                 # unfilled placeholder: no dead link
    monkeypatch.setattr(editions, "SITE_URL", "https://ledgerstitch.example")
    monkeypatch.setattr(editions, "SIGNUP_URL", "https://ledgerstitch.example/signup?ref=app-community")
    assert editions.signup_url() == "https://ledgerstitch.example/signup?ref=app-community"
    editions.configure(env={"LEDGERSTITCH_SIGNUP_URL": "http://insecure.example/x"})      # https only
    assert editions.signup_url() == "https://ledgerstitch.example/signup?ref=app-community"
    editions.configure(env={"LEDGERSTITCH_SIGNUP_URL": "https://ls.example/signup?ref=app-community"})
    assert editions.signup_url() == "https://ls.example/signup?ref=app-community"
    assert editions.SIGNUP_URL.endswith("/signup?ref=app-community")


def test_community_free_exports_and_stored_receipts(community, no_network, tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        st = c.get("/api/status").json()
        assert st["signup_url"] is None or st["signup_url"].startswith("https://")
        assert c.post("/api/vaults", json={"name": "Acme", "passphrase": PASS}).status_code == 201
        for fmt in ("xlsx", "csv", "qbo", "iif"):                          # the free exports
            assert c.get(f"/api/export/{fmt}").status_code == 200, fmt
        r = c.get("/api/export/nosuchformat")
        assert r.status_code == 404 and "xlsx, csv, qbo or iif" in r.json()["error"]
        assert c.get("/api/receipts").json() == []                        # viewing stored receipts: free
        assert c.post("/api/receipts", files=[("file", ("r.png", b"x", "image/png"))]).status_code in (404, 405)


# ---------------------------------------------------------------- tax_due hook
def test_tax_due_hook_first_sane_answer_wins(fresh_registry):
    def broken(ws, year):
        raise RuntimeError("boom")

    def malformed(ws, year, today=None):
        return {"date": "not a date", "amount": 10}

    seen = {}

    def good(ws, year, today=None):
        seen["args"] = (year, today)
        return {"date": "2026-01-15", "amount": 812.344, "label": "Q4"}

    def register(reg):
        reg.tax_due(broken)
        reg.tax_due(malformed)
        reg.tax_due(good)
    assert plugins.register_local(register, "dues")
    ws = SimpleNamespace(registry=plugins.registry())
    out = plugins.tax_due(ws, 2025, today=date(2025, 12, 1))
    assert out == {"date": "2026-01-15", "amount": 812.34, "label": "Q4", "source": "dues"}
    assert seen["args"] == (2025, date(2025, 12, 1))
    assert plugins.tax_due(SimpleNamespace(registry=plugins.Registry()), 2025) is None
