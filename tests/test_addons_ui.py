# SPDX-License-Identifier: AGPL-3.0-only
"""The browser side of the plugin API: static_dir, ui_screen / ui_card / ui_settings,
/api/status ``ui``, /addons/index.json, and /addons/<ns>/... serving (desktop + server mode)."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from backend_fakes import install_missing
from ledgerstitch import addons, plugins, store
from ledgerstitch.api import CSP, create_app
from ledgerstitch.security.license import License
from ledgerstitch.server_mode import ServerAuth

TOKEN = "t" * 43
BASE = "http://127.0.0.1:8765"
PASS = "correct horse battery"
FREE = License("", "free", None, False)
PRO = License("a@b.co", "pro", None, True)


@pytest.fixture
def static(tmp_path):
    d = tmp_path / "addon_static"
    (d / "sub").mkdir(parents=True)
    (d / "screen.js").write_text("export async function render(main) { main.textContent = 'hello'; }\n")
    (d / "card.js").write_text("export function renderCard(el) { el.textContent = 'card'; }\n")
    (d / "settings.js").write_text("export function renderSettings(el) {}\n")
    (d / "mock.js").write_text("export function mockRoutes() { return []; }\n")
    (d / "sub" / "style.css").write_text(".hello { color: red }\n")
    (d / ".hidden.js").write_text("secret\n")
    (d / "notes.md").write_text("not a browser file type\n")
    (tmp_path / "outside.js").write_text("outside\n")
    return d


def hello(static):
    def register(r):
        r.feature("hello.pro", tier="pro", label="Hello")
        r.static_dir(static)
        r.ui_screen("hello", "Hello", "Extras", "sparkle", "screen.js", feature="hello.pro", order=5)
        r.ui_screen("hello-free", "Hello free", None, "sparkle", "screen.js")
        r.ui_card("overview", "hello-card", "card.js", feature="hello.pro", order=7)
        r.ui_card("export", "hello-export", "card.js", title="Hello export")
        r.ui_settings("hello-settings", "Hello", "settings.js", feature="hello.pro")
    return register


@pytest.fixture
def client(fresh_registry, static, tmp_path, monkeypatch):
    install_missing(monkeypatch)
    assert plugins.register_local(hello(static), "hello"), plugins.registry().errors
    app = create_app(tmp_path / "data", TOKEN, port=8765)
    with TestClient(app, base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN}) as c:
        yield c


# ---------------------------------------------------------------- registration contract
def test_ui_registrations_are_validated(fresh_registry, static):
    def bad(fn):
        def reg(r):
            r.static_dir(static)
            fn(r)
        return reg
    cases = [
        lambda r: r.ui_screen("ledger", "X", None, "sparkle", "screen.js"),        # a core route
        lambda r: r.ui_screen("Bad Id", "X", None, "sparkle", "screen.js"),
        lambda r: r.ui_screen("x", "X", None, "sparkle", "../outside.js"),
        lambda r: r.ui_screen("x", "X", None, "sparkle", "missing.js"),
        lambda r: r.ui_screen("x", "X", None, "sparkle", "/abs.js"),
        lambda r: r.ui_screen("x", "X", None, "not an icon!", "screen.js"),
        lambda r: r.ui_card("Bad Slot", "x", "card.js"),
        lambda r: r.ui_card("overview", "x", "card.css"),
        lambda r: r.ui_settings("x", "", "settings.js"),
    ]
    for i, fn in enumerate(cases):
        assert not plugins.register_local(bad(fn), f"bad{i}"), i
    # UI entries without a static_dir fail too, and nothing half-registers
    assert not plugins.register_local(lambda r: r.ui_card("overview", "x", "card.js"), "nostatic")
    assert plugins.registry().ui_cards == []
    assert plugins.register_local(hello(static), "hello")
    assert not plugins.register_local(lambda r: (r.static_dir(static), r.ui_screen("hello", "X", None, "x", "screen.js")),
                                      "dup")                                   # screen id taken


def test_status_ui_states_follow_the_license(client, monkeypatch):
    st = client.get("/api/status").json()
    ui = st["ui"]
    assert [s["id"] for s in ui["screens"]] == ["hello", "hello-free"]          # sorted by order
    s = ui["screens"][0]
    assert s == {"ns": "hello", "id": "hello", "title": "Hello", "group": "Extras", "icon": "sparkle",
                 "module": "/addons/hello/screen.js", "feature": "hello.pro", "order": 5, "state": "locked"}
    assert ui["screens"][1]["state"] == "on" and ui["screens"][1]["feature"] is None
    card = next(c for c in ui["cards"] if c["id"] == "hello-card")
    assert card["slot"] == "overview" and card["state"] == "locked" and card["title"] == "Hello"
    assert next(c for c in ui["cards"] if c["id"] == "hello-export")["title"] == "Hello export"
    assert ui["settings"][0]["module"] == "/addons/hello/settings.js"
    monkeypatch.setattr(store, "_current_plan", lambda key: PRO)
    assert client.post("/api/vaults", json={"name": "Acme", "passphrase": PASS}).status_code == 201
    ui = client.get("/api/status").json()["ui"]
    assert {e["state"] for k in ("screens", "cards", "settings") for e in ui[k]} == {"on"}


def test_entries_of_missing_features_are_left_out(fresh_registry, static):
    def reg(r):
        r.static_dir(static)
        r.ui_card("overview", "other", "card.js", feature="someone.else")   # declared by an absent add-on
    plugins.register_local(reg, "solo")
    assert addons.ui_status(plugins.registry(), None)["cards"] == []


def test_index_json_for_demo_mode(client):
    d = client.get("/addons/index.json").json()
    assert d["addons"] == [{"ns": "hello", "mock": "/addons/hello/mock.js"}]
    assert d["features"]["hello.pro"] == {"tier": "pro", "label": "Hello"}
    assert d["plans"]["pro"] == ["pro"] and d["all_tiers_plan"] == "enterprise"
    assert [s["id"] for s in d["ui"]["screens"]] == ["hello", "hello-free"] and "state" not in d["ui"]["screens"][0]


# ---------------------------------------------------------------- serving /addons/<ns>/...
def test_static_files_are_served_with_the_app_headers(client):
    r = client.get("/addons/hello/screen.js")
    assert r.status_code == 200 and "render" in r.text
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["content-security-policy"] == CSP and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-cache"
    r = client.get("/addons/hello/sub/style.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
    # no token needed (static files are fetched by <script type=module>), like the core's own files
    assert TestClient(client.app, base_url=BASE).get("/addons/hello/card.js").status_code == 200


@pytest.mark.parametrize("path", [
    "/addons/hello/../outside.js", "/addons/hello/%2e%2e/outside.js", "/addons/hello/sub/../../outside.js",
    "/addons/hello/.hidden.js", "/addons/hello/notes.md", "/addons/hello/missing.js", "/addons/hello/",
    "/addons/nope/screen.js", "/addons/hello/sub", "/addons/hello//etc/passwd", "/addons/hello/..%2foutside.js",
])
def test_static_files_refuse_traversal_hidden_and_unknown(client, path):
    r = client.get(path)
    assert r.status_code == 404 and "outside" not in r.text and "secret" not in r.text


def test_symlink_out_of_the_folder_is_refused(fresh_registry, static, tmp_path):
    (static / "link.js").symlink_to(tmp_path / "outside.js")
    plugins.register_local(hello(static), "hello")
    assert addons.resolve_static(plugins.registry(), "hello", "link.js") is None
    assert addons.resolve_static(plugins.registry(), "hello", "screen.js") is not None


def test_community_serves_no_addons(community, tmp_path, monkeypatch):
    install_missing(monkeypatch)
    with TestClient(create_app(tmp_path, TOKEN, port=8765), base_url=BASE) as c:
        assert c.get("/addons/pro/settings-ai.js").status_code == 404
        assert c.get("/addons/index.json").json()["addons"] == []


# ---------------------------------------------------------------- server mode: sign-in required
def test_server_mode_addon_files_need_sign_in(fresh_registry, static, tmp_path, monkeypatch):
    install_missing(monkeypatch)
    plugins.register_local(hello(static), "hello")
    auth = ServerAuth(tmp_path / "srv")
    app = create_app(tmp_path / "data", TOKEN, server=auth)
    with TestClient(app, base_url="https://ledger.example.com") as c:
        for path in ("/addons/hello/screen.js", "/addons/index.json", "/addons/hello/missing.js"):
            r = c.get(path, follow_redirects=False)
            assert r.status_code == 401 and r.json()["login"] is True, path
        assert c.post("/auth/setup", json={"setup_code": auth.setup_code,
                                           "password": "correct horse battery staple"}).status_code == 200
        token = re.search(r'name="ll-token" content="([^"]+)"', c.get("/").text).group(1)
        assert c.get("/addons/hello/screen.js").status_code == 200        # a module import: no CSRF header
        assert c.get("/addons/index.json").json()["addons"][0]["ns"] == "hello"
        assert c.get("/api/status", headers={"X-LedgerStitch-Token": token}).json()["ui"]["screens"]
