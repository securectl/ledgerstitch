# SPDX-License-Identifier: AGPL-3.0-only
"""Self-hosted server mode (``ledgerstitch --server`` / ``LEDGERSTITCH_SERVER=1``).

The desktop app trusts whoever can read the per-launch token from the page it served on
127.0.0.1. That is fine on one computer, but not on a network. Server mode replaces it with
a real sign-in:

* **Admin password**, set on first run, stored only as an Argon2id hash in
  ``<data>/server/admin.json`` (mode 0600). Setting it the first time needs a one-time
  setup code printed to the server log, so whoever reaches a fresh server first can't
  claim it. (Or pre-seed it: ``LEDGERSTITCH_ADMIN_PASSWORD_FILE`` / ``ledgerstitch admin-password``.)
* **Sessions**: a random 256-bit id in an ``HttpOnly; SameSite=Strict; Secure`` cookie
  (``__Host-`` prefix over HTTPS). Only a SHA-256 of the id is kept, in memory; a restart
  signs everyone out. 8 h idle / 7 days absolute.
* **CSRF**: every ``/api`` call must also carry the session's own CSRF token in the
  ``X-LedgerStitch-Token`` header (injected into the page after sign-in, just like the
  desktop token). Cross-site pages can't read it, can't set the header without a CORS
  preflight we never answer, and the SameSite=Strict cookie isn't sent cross-site anyway.
  ``/auth/*`` accepts JSON bodies only, and a cross-origin ``Origin`` is rejected.
* **Rate limiting**: 5 failed sign-ins per client per 15 min, 30 overall per 15 min,
  then HTTP 429 with ``Retry-After``.
* The **vault passphrase stays separate**: signing in opens the app, not your data.
  Workspaces are still encrypted at rest and need their passphrase; signing out locks them.
* TLS: the server refuses to start on plain HTTP unless ``LEDGERSTITCH_ALLOW_HTTP=1``
  (meant for running behind a TLS reverse proxy such as the Caddy service in
  docker/docker-compose.yml). See docs/INSTALL.md.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

log = logging.getLogger(__name__)

MIN_ADMIN_PASSWORD = 12
SESSION_IDLE = 8 * 3600
SESSION_MAX = 7 * 24 * 3600
FAIL_WINDOW = 15 * 60
FAIL_PER_CLIENT = 5
FAIL_GLOBAL = 30
MAX_SESSIONS = 50
# Reachable without signing in: the sign-in page and what it needs, plus the health check.
PUBLIC_PATHS = {"/login", "/login.js", "/styles.css", "/theme-init.js", "/logo-mark.svg", "/healthz",
                "/auth/state", "/auth/login", "/auth/setup"}
# The bundled web fonts (styles.css loads them on the sign-in page too). No data in them.
PUBLIC_PREFIXES = ("/fonts/",)


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or (path.startswith(PUBLIC_PREFIXES) and ".." not in path)


def env_flag(name: str, env: Optional[dict] = None) -> bool:
    return str((env if env is not None else os.environ).get(name, "")).strip().lower() in ("1", "true", "yes", "on")


class RateLimiter:
    def __init__(self, per_client: int = FAIL_PER_CLIENT, overall: int = FAIL_GLOBAL,
                 window: float = FAIL_WINDOW, now: Callable[[], float] = time.monotonic):
        self.per_client, self.overall, self.window, self.now = per_client, overall, window, now
        self._fails: dict[str, deque] = {}
        self._all: deque = deque()
        self._lock = threading.Lock()

    def _prune(self, q: deque, t: float) -> None:
        while q and q[0] <= t - self.window:
            q.popleft()

    def retry_after(self, client: str) -> int:
        """0 if a new attempt is allowed now, else seconds to wait."""
        with self._lock:
            t = self.now()
            q = self._fails.get(client, deque())
            self._prune(q, t)
            self._prune(self._all, t)
            waits = []
            if len(q) >= self.per_client:
                waits.append(q[0] + self.window - t)
            if len(self._all) >= self.overall:
                waits.append(self._all[0] + self.window - t)
            return max(1, int(max(waits)) + 1) if waits else 0

    def fail(self, client: str) -> None:
        with self._lock:
            t = self.now()
            self._fails.setdefault(client, deque()).append(t)
            self._all.append(t)
            if len(self._fails) > 10_000:                   # bound memory
                self._fails.pop(next(iter(self._fails)))

    def success(self, client: str) -> None:
        with self._lock:
            self._fails.pop(client, None)


@dataclass
class _Sess:
    csrf: str
    created: float
    last: float


@dataclass
class ServerAuth:
    data_dir: Path
    allow_http: bool = False
    trust_proxy: bool = False
    allowed_hosts: Optional[set[str]] = None
    now: Callable[[], float] = time.time
    limiter: RateLimiter = field(default_factory=RateLimiter)

    def __post_init__(self) -> None:
        from argon2 import PasswordHasher
        self.data_dir = Path(self.data_dir).expanduser()
        self._ph = PasswordHasher()        # Argon2id, library defaults (64 MiB, t=3)
        self._sessions: dict[str, _Sess] = {}
        self._lock = threading.Lock()
        self.setup_code: Optional[str] = None
        if not self.configured():
            self.setup_code = self._load_or_create_setup_code()

    # ------------------------------------------------------------ first-run setup code
    @property
    def setup_code_file(self) -> Path:
        return self.data_dir / "server" / "setup-code"

    def _load_or_create_setup_code(self) -> str:
        """The same code until the admin password is set, so a restart (upgrade, reboot,
        `docker compose up` again) doesn't invalidate the code someone already copied. Kept
        owner-only next to admin.json and deleted once the password is set."""
        try:
            code = self.setup_code_file.read_text("utf-8").strip()
            if re.fullmatch(r"[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}", code):
                return code
        except OSError:
            pass
        code = "-".join(secrets.token_hex(3).upper() for _ in range(3))
        try:
            d = self.setup_code_file.parent
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)
            fd = os.open(self.setup_code_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code + "\n")
        except OSError:
            log.warning("couldn't save the setup code; it changes on restart")
        return code

    def check_setup_code(self, typed: str) -> bool:
        """Constant-time compare that ignores case, spaces and dashes."""
        if not self.setup_code:
            return False
        norm = lambda s: re.sub(r"[^0-9A-F]", "", s.upper())
        return hmac.compare_digest(norm(typed).encode(), norm(self.setup_code).encode())

    # ------------------------------------------------------------ admin password
    @property
    def password_file(self) -> Path:
        return self.data_dir / "server" / "admin.json"

    def configured(self) -> bool:
        try:
            return bool(json.loads(self.password_file.read_text("utf-8")).get("hash"))
        except (OSError, ValueError, AttributeError):
            return False

    def set_password(self, password: str) -> None:
        if len(password) < MIN_ADMIN_PASSWORD:
            raise ValueError(f"The admin password needs at least {MIN_ADMIN_PASSWORD} characters.")
        d = self.password_file.parent
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        rec = {"hash": self._ph.hash(password), "alg": "argon2id",
               "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        tmp = d / ".admin.json.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        os.replace(tmp, self.password_file)
        self.setup_code = None
        try:
            self.setup_code_file.unlink()
        except OSError:
            pass

    def verify_password(self, password: str) -> bool:
        from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
        try:
            h = json.loads(self.password_file.read_text("utf-8"))["hash"]
        except (OSError, ValueError, KeyError, TypeError):
            return False
        try:
            self._ph.verify(h, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        if self._ph.check_needs_rehash(h):
            try:
                self.set_password(password)
            except (OSError, ValueError):
                pass
        return True

    # ------------------------------------------------------------ sessions
    @staticmethod
    def _key(sid: str) -> str:
        return hashlib.sha256(sid.encode()).hexdigest()

    def create_session(self) -> tuple[str, str]:
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        t = self.now()
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions, key=lambda k: self._sessions[k].last)
                self._sessions.pop(oldest, None)
            self._sessions[self._key(sid)] = _Sess(csrf, t, t)
        return sid, csrf

    def session(self, sid: Optional[str], touch: bool = True) -> Optional[_Sess]:
        if not sid or len(sid) > 200:
            return None
        k = self._key(sid)
        t = self.now()
        with self._lock:
            s = self._sessions.get(k)
            if s is None:
                return None
            if t - s.last > SESSION_IDLE or t - s.created > SESSION_MAX:
                self._sessions.pop(k, None)
                return None
            if touch:
                s.last = t
            return s

    def drop_session(self, sid: Optional[str]) -> None:
        if sid:
            with self._lock:
                self._sessions.pop(self._key(sid), None)

    def drop_all(self) -> None:
        with self._lock:
            self._sessions.clear()

    # ------------------------------------------------------------ request helpers
    def is_https(self, request: Request) -> bool:
        if request.url.scheme == "https":
            return True
        if self.trust_proxy:
            return request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"
        return False

    def cookie_name(self, request: Request) -> str:
        return "__Host-ls_session" if self.is_https(request) else "ls_session"

    def client_key(self, request: Request) -> str:
        if self.trust_proxy:
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                return xff.split(",")[-1].strip()[:64]
        return (request.client.host if request.client else "unknown")[:64]

    def session_for(self, request: Request) -> Optional[_Sess]:
        sid = request.cookies.get("__Host-ls_session") or request.cookies.get("ls_session")
        return self.session(sid)

    def set_cookie(self, request: Request, resp: Response, sid: str) -> None:
        secure = self.is_https(request)
        resp.set_cookie(self.cookie_name(request), sid, httponly=True, samesite="strict",
                        secure=secure, path="/")

    def clear_cookie(self, request: Request, resp: Response) -> None:
        for name in ("__Host-ls_session", "ls_session"):
            resp.delete_cookie(name, path="/", secure=name.startswith("__Host-"), httponly=True, samesite="strict")

    def host_ok(self, host_header: str) -> bool:
        host = (host_header or "").strip().lower()
        if not host:
            return False
        if self.allowed_hosts:
            name = host.rsplit("]", 1)[0] + "]" if host.startswith("[") else host.split(":", 1)[0]
            # loopback stays reachable for the container health check / an SSH tunnel
            return name in self.allowed_hosts or host in self.allowed_hosts or name in ("127.0.0.1", "localhost")
        return True


def from_env(data_dir: Path, env: Optional[dict] = None) -> ServerAuth:
    env = env if env is not None else os.environ
    hosts = {h.strip().lower() for h in str(env.get("LEDGERSTITCH_ALLOWED_HOSTS", "")).split(",") if h.strip()}
    auth = ServerAuth(data_dir, allow_http=env_flag("LEDGERSTITCH_ALLOW_HTTP", env),
                      trust_proxy=env_flag("LEDGERSTITCH_TRUST_PROXY", env), allowed_hosts=hosts or None)
    pw_file = env.get("LEDGERSTITCH_ADMIN_PASSWORD_FILE")
    if pw_file and not auth.configured():
        pw = Path(pw_file).read_text("utf-8").strip()
        auth.set_password(pw)
        log.warning("admin password set from LEDGERSTITCH_ADMIN_PASSWORD_FILE")
    return auth


# ---------------------------------------------------------------- routes
def _json_err(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


async def _json_body(request: Request) -> Optional[dict]:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return None
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return body if isinstance(body, dict) else None


def install_routes(app: FastAPI, auth: ServerAuth, *, static_dir: Path, on_logout: Callable[[], None]) -> None:
    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if auth.session_for(request):
            return RedirectResponse("/", status_code=303)
        f = static_dir / "login.html"
        if not f.is_file():
            return _json_err(500, "Sign-in page missing.")
        return FileResponse(f, media_type="text/html")

    @app.get("/auth/state", include_in_schema=False)
    def auth_state(request: Request):
        return {"configured": auth.configured(), "signed_in": auth.session_for(request) is not None,
                "min_length": MIN_ADMIN_PASSWORD}

    def limited(request: Request) -> Optional[JSONResponse]:
        wait = auth.limiter.retry_after(auth.client_key(request))
        if wait:
            r = _json_err(429, f"Too many attempts. Try again in {max(1, wait // 60)} min.", retry_after=wait)
            r.headers["Retry-After"] = str(wait)
            return r
        return None

    def signed_in(request: Request) -> JSONResponse:
        sid, _ = auth.create_session()
        resp = JSONResponse({"ok": True})
        auth.set_cookie(request, resp, sid)
        return resp

    @app.post("/auth/setup", include_in_schema=False)
    async def auth_setup(request: Request):
        body = await _json_body(request)
        if body is None:
            return _json_err(415, "Send JSON.")
        if auth.configured():
            return _json_err(409, "The admin password is already set. Sign in instead.")
        r = limited(request)
        if r:
            return r
        if not auth.check_setup_code(str(body.get("setup_code") or "")):
            auth.limiter.fail(auth.client_key(request))
            return _json_err(401, "That setup code doesn't match. Get the current one with: "
                                  "docker compose logs ledgerstitch | grep \"Setup code\"")
        try:
            auth.set_password(str(body.get("password") or ""))
        except ValueError as e:
            return _json_err(422, str(e))
        auth.limiter.success(auth.client_key(request))
        log.warning("admin password set via the setup page")
        return signed_in(request)

    @app.post("/auth/login", include_in_schema=False)
    async def auth_login(request: Request):
        body = await _json_body(request)
        if body is None:
            return _json_err(415, "Send JSON.")
        r = limited(request)
        if r:
            return r
        pw = str(body.get("password") or "")
        if not auth.configured() or not pw or len(pw) > 1024 or not auth.verify_password(pw):
            auth.limiter.fail(auth.client_key(request))
            return _json_err(401, "Wrong password.")
        auth.limiter.success(auth.client_key(request))
        return signed_in(request)

    @app.post("/auth/logout", include_in_schema=False)
    async def auth_logout(request: Request):
        # reached only with a valid session + CSRF header (enforced by the guard)
        sid = request.cookies.get("__Host-ls_session") or request.cookies.get("ls_session")
        auth.drop_session(sid)
        try:
            on_logout()
        finally:
            resp = JSONResponse({"ok": True})
            auth.clear_cookie(request, resp)
        return resp

    @app.post("/auth/password", include_in_schema=False)
    async def auth_password(request: Request):
        body = await _json_body(request)
        if body is None:
            return _json_err(415, "Send JSON.")
        r = limited(request)
        if r:
            return r
        if not auth.verify_password(str(body.get("old") or "")):
            auth.limiter.fail(auth.client_key(request))
            return _json_err(401, "Wrong current password.")
        try:
            auth.set_password(str(body.get("new") or ""))
        except ValueError as e:
            return _json_err(422, str(e))
        auth.drop_all()                    # sign out every other browser
        return signed_in(request)
