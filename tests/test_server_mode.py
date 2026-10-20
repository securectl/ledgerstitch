# SPDX-License-Identifier: AGPL-3.0-only
"""Self-hosted server mode: admin sign-in, sessions, CSRF, rate limiting, TLS refusal.
The desktop (localhost token) mode must be unchanged; see test_api.py for that."""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from backend_fakes import install_missing
from ledgerstitch import cli
from ledgerstitch.api import create_app
from ledgerstitch.server_mode import RateLimiter, ServerAuth, from_env

TOKEN = "t" * 43
BASE = "https://ledger.example.com"
ADMIN = "correct horse battery staple"
VAULT_PASS = "a different vault passphrase"


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    install_missing(monkeypatch)


@pytest.fixture
def auth(tmp_path):
    return ServerAuth(tmp_path)


@pytest.fixture
def app(tmp_path, auth):
    return create_app(tmp_path, TOKEN, server=auth)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE) as c:
        yield c


def _setup(c, auth, pw=ADMIN):
    return c.post("/auth/setup", json={"setup_code": auth.setup_code, "password": pw})


def _csrf(c) -> str:
    r = c.get("/")
    assert r.status_code == 200, r.text
    m = re.search(r'name="ll-token" content="([^"]+)"', r.text)
    assert m, "token not injected"
    return m.group(1)


@pytest.fixture
def signed_in(client, auth):
    assert _setup(client, auth).status_code == 200
    client.headers["X-LedgerStitch-Token"] = _csrf(client)
    return client


# ---------------------------------------------------------------- first run
def test_unauthenticated_is_redirected_or_401(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/app.js", follow_redirects=False).status_code == 303
    r = client.get("/api/status")
    assert r.status_code == 401 and r.json().get("login") is True
    # even the desktop token doesn't help in server mode
    assert client.get("/api/status", headers={"X-LedgerStitch-Token": TOKEN}).status_code == 401
    assert client.get("/login").status_code == 200
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/auth/state").json()["configured"] is False


def test_first_run_needs_setup_code(client, auth):
    assert auth.setup_code
    r = client.post("/auth/setup", json={"setup_code": "WRONG", "password": ADMIN})
    assert r.status_code == 401
    r = client.post("/auth/setup", json={"setup_code": auth.setup_code, "password": "short"})
    assert r.status_code == 422
    r = _setup(client, auth)
    assert r.status_code == 200
    # can't be set twice through the web
    assert client.post("/auth/setup", json={"setup_code": "x", "password": ADMIN}).status_code == 409


def test_password_stored_as_argon2_hash(client, auth, tmp_path):
    _setup(client, auth)
    rec = json.loads((tmp_path / "server" / "admin.json").read_text())
    assert rec["hash"].startswith("$argon2id$")
    assert ADMIN not in (tmp_path / "server" / "admin.json").read_text()
    assert ((tmp_path / "server" / "admin.json").stat().st_mode & 0o777) == 0o600


def test_session_cookie_flags(client, auth):
    r = _setup(client, auth)
    sc = r.headers["set-cookie"]
    assert sc.startswith("__Host-ls_session=")
    low = sc.lower()
    assert "httponly" in low and "samesite=strict" in low and "secure" in low and "path=/" in low
    assert "domain=" not in low


def test_plain_http_cookie_has_no_secure_prefix(tmp_path):
    auth = ServerAuth(tmp_path, allow_http=True)
    with TestClient(create_app(tmp_path, TOKEN, server=auth), base_url="http://10.0.0.5:8080") as c:
        r = _setup(c, auth)
        assert r.headers["set-cookie"].startswith("ls_session=")
        assert "secure" not in r.headers["set-cookie"].lower()


def test_signed_in_api_needs_csrf_header(signed_in):
    assert signed_in.get("/api/status").status_code == 200
    st = signed_in.get("/api/status").json()
    assert st["server_mode"] is True
    # cookie alone (no CSRF header) is not enough
    r = signed_in.get("/api/status", headers={"X-LedgerStitch-Token": ""})
    assert r.status_code == 401
    r = signed_in.get("/api/status", headers={"X-LedgerStitch-Token": TOKEN})
    assert r.status_code == 401


def test_cross_origin_rejected(signed_in):
    r = signed_in.post("/api/lock", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = signed_in.post("/api/lock", headers={"Origin": "null"})
    assert r.status_code == 403
    assert signed_in.post("/api/lock", headers={"Origin": BASE}).status_code == 200


def test_auth_endpoints_need_json(client, auth):
    r = client.post("/auth/login", data={"password": ADMIN})   # a cross-site <form> would send this
    assert r.status_code == 415


def test_vault_passphrase_stays_separate(signed_in):
    st = signed_in.get("/api/status").json()
    assert st["unlocked"] is False                     # signing in does not open any data
    r = signed_in.post("/api/vaults", json={"name": "Acme", "passphrase": VAULT_PASS})
    assert r.status_code == 201
    signed_in.post("/api/lock")
    vaults = signed_in.get("/api/vaults").json()
    r = signed_in.post("/api/unlock", json={"path": vaults[0]["path"], "passphrase": ADMIN})
    assert r.status_code == 401                        # the admin password is not the vault key
    r = signed_in.post("/api/unlock", json={"path": vaults[0]["path"], "passphrase": VAULT_PASS})
    assert r.status_code == 200 and r.json()["unlocked"]


def test_logout_drops_session_and_locks_vault(signed_in, app):
    signed_in.post("/api/vaults", json={"name": "Acme", "passphrase": VAULT_PASS})
    assert app.state.session.ws is not None
    r = signed_in.post("/auth/logout")
    assert r.status_code == 200
    assert app.state.session.ws is None
    assert signed_in.get("/api/status").status_code == 401


def test_logout_needs_csrf(signed_in):
    r = signed_in.post("/auth/logout", headers={"X-LedgerStitch-Token": "nope"})
    assert r.status_code == 401
    assert signed_in.get("/api/status").status_code == 200   # still signed in


def test_login_after_restart(tmp_path):
    a1 = ServerAuth(tmp_path)
    with TestClient(create_app(tmp_path, TOKEN, server=a1), base_url=BASE) as c:
        _setup(c, a1)
    a2 = ServerAuth(tmp_path)                       # restart: password persisted, sessions gone
    assert a2.setup_code is None
    with TestClient(create_app(tmp_path, TOKEN, server=a2), base_url=BASE) as c:
        assert c.get("/api/status").status_code == 401
        assert c.post("/auth/login", json={"password": "wrong password!"}).status_code == 401
        r = c.post("/auth/login", json={"password": ADMIN})
        assert r.status_code == 200
        c.headers["X-LedgerStitch-Token"] = _csrf(c)
        assert c.get("/api/status").status_code == 200


def test_login_rate_limited(client, auth):
    _setup(client, auth)
    client.cookies.clear()
    for _ in range(5):
        assert client.post("/auth/login", json={"password": "wrong password!"}).status_code == 401
    r = client.post("/auth/login", json={"password": ADMIN})     # even the right one, for a while
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_rate_limiter_window_and_global():
    t = [0.0]
    rl = RateLimiter(per_client=2, overall=3, window=60, now=lambda: t[0])
    rl.fail("a"); rl.fail("a")
    assert rl.retry_after("a") > 0 and rl.retry_after("b") == 0
    rl.fail("b")
    assert rl.retry_after("c") > 0            # global cap reached
    t[0] = 61
    assert rl.retry_after("a") == 0 and rl.retry_after("c") == 0
    rl.fail("a"); rl.success("a")
    assert rl.retry_after("a") == 0


def test_session_idle_expiry(tmp_path):
    t = [1000.0]
    auth = ServerAuth(tmp_path, now=lambda: t[0])
    sid, _ = auth.create_session()
    assert auth.session(sid)
    t[0] += 9 * 3600
    assert auth.session(sid) is None


def test_change_password_signs_out_others(signed_in, auth):
    r = signed_in.post("/auth/password", json={"old": ADMIN, "new": "another long password"})
    assert r.status_code == 200
    assert auth.verify_password("another long password") and not auth.verify_password(ADMIN)


def test_allowed_hosts(tmp_path):
    auth = ServerAuth(tmp_path, allowed_hosts={"ledger.example.com"})
    app = create_app(tmp_path, TOKEN, server=auth)
    with TestClient(app, base_url="https://evil.example") as c:
        assert c.get("/login").status_code == 400
    with TestClient(app, base_url=BASE) as c:
        assert c.get("/login").status_code == 200


def test_hsts_over_https(client):
    assert "max-age" in client.get("/login").headers.get("strict-transport-security", "")


def test_setup_install_refused_in_server_mode(signed_in):
    r = signed_in.post("/api/setup/ollama/install", json={"consent": True})
    assert r.status_code == 403


def test_admin_password_file(tmp_path):
    f = tmp_path / "pw.txt"
    f.write_text(ADMIN + "\n")
    auth = from_env(tmp_path / "data", {"LEDGERSTITCH_ADMIN_PASSWORD_FILE": str(f)})
    assert auth.configured() and auth.verify_password(ADMIN) and auth.setup_code is None


def test_cli_refuses_plain_http(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LEDGERSTITCH_ALLOW_HTTP", raising=False)
    monkeypatch.delenv("LEDGERSTITCH_TLS_CERT", raising=False)
    monkeypatch.delenv("LEDGERSTITCH_TLS_KEY", raising=False)
    called = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: called.append(k))
    assert cli.main(["--server", "--data-dir", str(tmp_path)]) == 2
    assert "Refusing to start" in capsys.readouterr().err
    assert not called
    monkeypatch.setenv("LEDGERSTITCH_ALLOW_HTTP", "1")
    assert cli.main(["--server", "--data-dir", str(tmp_path), "--port", "8099"]) == 0
    assert called and called[0]["host"] == "0.0.0.0" and called[0]["port"] == 8099
    assert "Setup code" in capsys.readouterr().out


def test_cli_env_server_flag_with_tls(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: called.append(k))
    monkeypatch.setenv("LEDGERSTITCH_SERVER", "1")
    assert cli.main(["--data-dir", str(tmp_path), "--tls-cert", "c.pem", "--tls-key", "k.pem"]) == 0
    assert called[0]["ssl_certfile"] == "c.pem" and called[0]["host"] == "0.0.0.0"


def test_desktop_mode_unchanged(tmp_path):
    """No server object: the localhost token model, exactly as before."""
    app = create_app(tmp_path, TOKEN, port=8765)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        assert c.get("/api/status").status_code == 401
        st = c.get("/api/status", headers={"X-LedgerStitch-Token": TOKEN}).json()
        assert st["server_mode"] is False
        assert TOKEN in c.get("/").text
        assert c.get("/login.html").status_code == 200  # just a static file, never required
    with TestClient(app, base_url="http://ledger.example.com:8765") as c:
        assert c.get("/").status_code == 400


def test_setup_code_survives_a_restart_and_is_removed_once_used(tmp_path):
    a1 = ServerAuth(tmp_path)
    a2 = ServerAuth(tmp_path)                 # e.g. `docker compose up` again
    assert a1.setup_code and a2.setup_code == a1.setup_code
    f = a1.setup_code_file
    assert f.exists() and (f.stat().st_mode & 0o777) == 0o600
    a2.set_password(ADMIN)
    assert not f.exists() and ServerAuth(tmp_path).setup_code is None


def test_setup_code_ignores_case_spaces_and_dashes(tmp_path):
    a = ServerAuth(tmp_path)
    loose = " " + a.setup_code.replace("-", " ").lower() + " "
    assert a.check_setup_code(loose) and a.check_setup_code(a.setup_code.replace("-", ""))
    assert not a.check_setup_code("000000-000000-000000") and not a.check_setup_code("")


def test_fonts_and_theme_script_are_public_but_app_code_is_not():
    from ledgerstitch.server_mode import is_public
    assert is_public("/fonts/inter-latin-wght.woff2") and is_public("/theme-init.js")
    assert not is_public("/fonts/../app.js") and not is_public("/app.js") and not is_public("/api/status")


# ---------------------------------------------------------------- local AI via the compose "ollama" service
class _Det:
    def __init__(self, running):
        self.running, self.models, self.urls = running, [], []

    def __call__(self, url=None, **_):
        self.urls.append(url)
        return {"running": self.running, "version": "0.40.6" if self.running else None, "models": list(self.models),
                "url": url, "installed": self.running, "path": None}


@pytest.fixture
def ollama_env(monkeypatch):
    from ledgerstitch.ai import local_ollama
    monkeypatch.setenv("LEDGERSTITCH_OLLAMA_URL", "http://ollama:11434")
    monkeypatch.delenv("LEDGERSTITCH_LOCAL_MODEL", raising=False)
    yield
    local_ollama.set_server_mode(False)


def test_server_mode_local_ai_uses_only_the_operator_endpoint(ollama_env, signed_in, app, tmp_path):
    import httpx
    from ledgerstitch import appconfig
    from ledgerstitch.onboarding import ollama as ol
    det = _Det(running=False)
    app.state.setup.detector = det
    app.state.setup.background = False
    app.state.setup.resources = lambda: {"ok": True, "disk_ok": True, "mem_ok": True}
    o = signed_in.get("/api/setup/check").json()["ollama"]
    assert o["show"] is True and o["operator_url"] == "http://ollama:11434" and o["install_allowed"] is False
    assert o["fix"] == "docker compose up -d ollama"
    assert signed_in.get("/api/status").json()["ollama_operator_url"] == "http://ollama:11434"
    # not reachable: refused with the one-line fix, nothing recorded, nothing installed
    r = signed_in.post("/api/setup/local-ai", json={"consent": True})
    assert r.status_code == 409 and r.json()["fix"] == "docker compose up -d ollama"
    assert "local_ai_consent" not in appconfig.onboarding(tmp_path)
    # reachable: the consented pull goes to the compose service
    det.running = True
    sent = []

    def handler(req):
        sent.append((str(req.url), json.loads(req.content)))
        det.models.append(ol.RECOMMENDED_MODEL)
        return httpx.Response(200, content=b'{"status":"pulling a","digest":"a","total":10,"completed":10}\n{"status":"success"}\n')

    app.state.setup.pull_client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))
    r = signed_in.post("/api/setup/local-ai", json={"consent": True})
    assert r.status_code == 202, r.text
    assert sent == [("http://ollama:11434/api/pull", {"model": ol.RECOMMENDED_MODEL, "stream": True})]
    assert set(det.urls) == {"http://ollama:11434"}
    assert appconfig.onboarding(tmp_path)["local_ai_consent"]["endpoint"] == "http://ollama:11434"
    # Settings: exactly the operator URL is accepted, anything else non-loopback is still refused
    signed_in.post("/api/vaults", json={"name": "Acme", "passphrase": VAULT_PASS})
    assert signed_in.get("/api/settings").json()["provider"] == "ollama"       # switched on after the pull
    assert signed_in.put("/api/settings", json={"ollama_url": "http://ollama:11434"}).status_code == 200
    for bad in ("http://evil.example:11434", "http://192.168.1.20:11434", "http://ollama:9999"):
        assert signed_in.put("/api/settings", json={"ollama_url": bad}).status_code == 422


def test_server_mode_without_ollama_service_hides_the_step(signed_in, app, monkeypatch):
    monkeypatch.delenv("LEDGERSTITCH_OLLAMA_URL", raising=False)
    app.state.setup.detector = _Det(running=False)
    o = signed_in.get("/api/setup/check").json()["ollama"]
    assert o["show"] is False and o["operator_url"] is None
    r = signed_in.put("/api/settings", json={"ollama_url": "http://ollama:11434"})
    assert r.status_code in (409, 422)          # no vault open (409) or refused (422): never accepted


def test_compose_file_keeps_ollama_private():
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / "docker" / "docker-compose.yml"
    if not f.exists():
        pytest.skip("no docker/ folder in this tree")
    text = f.read_text()
    svc = text.split("\n  ollama:\n", 1)[1].split("\n\n", 1)[0]
    assert "image: ollama/ollama:0." in svc and "ports:" not in svc and "ollama-models:/root/.ollama" in svc
    # defaults to the private ollama service; an operator may point it at a host Ollama
    assert "LEDGERSTITCH_OLLAMA_URL: ${LEDGERSTITCH_OLLAMA_URL:-http://ollama:11434}" in text
    assert 'OLLAMA_CONTEXT_LENGTH: "8192"' in svc        # GLM-4.7-Flash fits; no OOM kill
    assert "docker.sock" not in text.replace("# ", "")
