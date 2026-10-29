# SPDX-License-Identifier: AGPL-3.0-only
"""LedgerStitch local HTTP API (FastAPI).

Security model (the app only ever listens on 127.0.0.1):

* Every ``/api`` request must carry ``X-LedgerStitch-Token`` equal to the random
  per-launch token (compared in constant time). The token is injected into
  ``index.html`` at serve time, so only the page we served can call the API.
* Requests whose ``Host`` header is not ``127.0.0.1:<port>`` / ``localhost:<port>``
  are rejected (DNS-rebinding protection); cross-origin ``Origin`` headers are rejected.
* Strict CSP and friends on every response; ``Cache-Control: no-store`` on the API.
* Uploads are parsed **in memory** by our own multipart reader (Starlette's
  parser spools files >1 MB to a temp file on disk, which would break the
  "raw files never touch disk" promise). 25 MB per file.
* One vault unlocked at a time; it auto-locks after N idle minutes.
* Recovery codes are returned exactly once (create / regenerate) and never logged.
  Encrypted backups (``.lsbackup``) are copies of the already-encrypted vault
  files; a restore always goes into a new vault folder.
* Add-on routes (ledgerstitch.plugins) are installed first; add-on paths answer
  402 ``{"error", "feature", "upgrade": true}`` while the add-on's feature is locked.
  Add-on browser files are served at ``/addons/<namespace>/...`` (ledgerstitch.addons),
  behind the same sign-in, CSP and cache rules as the app.
* AGPL-3.0 §13: ``GET /api/source`` tells every user where to get the source.

Self-hosted **server mode** (``create_app(..., server=ServerAuth(...))``, see
``ledgerstitch/server_mode.py``) replaces the token + localhost Host check with an admin
password sign-in, an HttpOnly SameSite=Strict session cookie and a per-session CSRF token
in the same header. Everything else (vault passphrases, auto-lock, CSP) is unchanged.
"""
from __future__ import annotations

import asyncio
import hmac
import html
import json
import os
import sys
import logging
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from . import addons as addons_mod
from . import editions, plugins
from . import store as store_mod
from .ai import local_ollama as _local_ollama
from .onboarding.routes import onboarding_status
from .store import (
    LimitError,
    NeedsMapping,
    NotFound,
    StoreError,
    ValidationError,
    Workspace,
    slugify,
    to_jsonable,
)

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-LedgerStitch-Token"
TOKEN_PLACEHOLDER = "__LEDGERSTITCH_TOKEN__"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_REQUEST = 50
MAX_FORM_FIELD_BYTES = 64 * 1024
STATIC_DIR = Path(__file__).parent / "web" / "static"
DEFAULT_DATA_DIR = Path.home() / "LedgerStitch"
MIN_PASSPHRASE = 8
# A backup upload is parsed in memory like every upload (it is ciphertext, but we
# keep one code path). Large receipt collections may need more; see USER_GUIDE.
MAX_BACKUP_UPLOAD_BYTES = 1024 * 1024 * 1024
# AGPL-3.0 §13 network-use source offer. Placeholder until the public repo exists.
SOURCE_URL = "https://github.com/REPLACE-ME/ledgerstitch"
SOURCE_LICENSE = "AGPL-3.0-only"

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost"}
# App code: browsers revalidate on every load (cheap 304s via ETag), so an upgrade never
# runs a stale script against a new server.
REVALIDATE_SUFFIXES = (".js", ".css", ".html", ".svg")


# --------------------------------------------------------------------------- errors
class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def _err(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def _store_error_status(e: StoreError) -> int:
    if isinstance(e, LimitError):
        return 402
    if isinstance(e, NotFound):
        return 404
    if isinstance(e, ValidationError):
        return 422
    return 400


def _vault_classes():
    try:
        from .security import vault as v
        return v.Vault, v.VaultError, v.WrongPassphrase
    except Exception:  # pragma: no cover - only while teammates are mid-flight
        class _Missing(Exception):
            pass
        return None, _Missing, _Missing


# --------------------------------------------------------------------------- request bodies
class VaultCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    passphrase: str = Field(min_length=1, max_length=1024)
    # Opt-in plaintext label shown in the picker while locked. Default: none, so a
    # bookkeeper's client names are not readable on disk (the name is encrypted).
    label: Optional[str] = Field(default=None, max_length=80)
    # Also create a recovery key; the response then carries it once as recovery_code.
    recovery: bool = False


class VaultUnlock(BaseModel):
    path: Optional[str] = None
    name: Optional[str] = None
    passphrase: str = Field(min_length=1, max_length=1024)


class RecoveryUnlock(BaseModel):
    path: Optional[str] = None
    name: Optional[str] = None
    recovery_code: str = Field(min_length=1, max_length=200)
    new_passphrase: str = Field(min_length=1, max_length=1024)


class PassphraseConfirm(BaseModel):
    passphrase: str = Field(min_length=1, max_length=1024)


class PassphraseChange(BaseModel):
    old: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


class AccountPatch(BaseModel):
    label: Optional[str] = None
    institution: Optional[str] = None
    kind: Optional[str] = None
    last4: Optional[str] = None


class TxnPatch(BaseModel):
    model_config = {"extra": "forbid"}
    category: Optional[str] = None
    lane: Optional[str] = None
    schedule_c: Optional[str] = None
    note: Optional[str] = None
    status: Optional[str] = None
    remember: bool = False


class TxnDelete(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100_000)


class DataPurge(BaseModel):
    scope: str = Field(pattern="^(transactions|all)$")
    confirm: str = Field(max_length=40)        # the user types DELETE


class VaultDelete(BaseModel):
    passphrase: str = Field(min_length=1, max_length=1024)
    confirm: str = Field(max_length=40)


CONFIRM_WORD = "DELETE"


class BulkPatch(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100_000)
    category: Optional[str] = None
    lane: Optional[str] = None
    schedule_c: Optional[str] = None
    status: Optional[str] = None


class CategoryCreate(BaseModel):
    name: str
    lane: str
    schedule_c: Optional[str] = None


class RuleCreate(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    category: str
    lane: Optional[str] = None
    schedule_c: Optional[str] = None
    field: str = "merchant"
    account_id: Optional[str] = None
    apply: bool = True


class LicenseBody(BaseModel):
    key: str = Field(max_length=10_000)


# --------------------------------------------------------------------------- in-memory multipart
async def read_multipart(request: Request, max_file_bytes: int = MAX_UPLOAD_BYTES,
                         max_files: int = MAX_FILES_PER_REQUEST):
    """Parse multipart/form-data entirely in memory.

    Returns ``(fields: dict[str, str], files: list[tuple[field, filename, bytes]])``.
    Raises ApiError(413) as soon as any single file exceeds ``max_file_bytes``.
    """
    from python_multipart.multipart import MultipartParser, parse_options_header

    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    if ctype != b"multipart/form-data" or b"boundary" not in params:
        raise ApiError(422, "Expected a multipart/form-data upload.")

    fields: dict[str, str] = {}
    files: list[tuple[str, str, bytes]] = []
    cur: dict[str, Any] = {}
    hdr = {"name": b"", "value": b""}

    def on_part_begin():
        cur.clear()
        cur.update(headers={}, buf=bytearray(), name=None, filename=None)

    def on_header_field(data, start, end):
        hdr["name"] += data[start:end]

    def on_header_value(data, start, end):
        hdr["value"] += data[start:end]

    def on_header_end():
        cur["headers"][hdr["name"].lower()] = hdr["value"]
        hdr["name"], hdr["value"] = b"", b""

    def on_headers_finished():
        _, opts = parse_options_header(cur["headers"].get(b"content-disposition", b""))
        if b"name" not in opts:
            raise ApiError(422, "Malformed upload (part without a name).")
        cur["name"] = opts[b"name"].decode("utf-8", "replace")
        if b"filename" in opts:
            cur["filename"] = opts[b"filename"].decode("utf-8", "replace")
            if len(files) >= max_files:
                raise ApiError(413, f"Too many files in one upload (max {max_files}).")

    def on_part_data(data, start, end):
        buf: bytearray = cur["buf"]
        limit = max_file_bytes if cur.get("filename") is not None else MAX_FORM_FIELD_BYTES
        if len(buf) + (end - start) > limit:
            if cur.get("filename") is not None:
                raise ApiError(413, f"'{Path(cur['filename']).name}' is larger than "
                                    f"{max_file_bytes // (1024 * 1024)} MB.")
            raise ApiError(413, "Form field too large.")
        buf.extend(data[start:end])

    def on_part_end():
        if cur.get("filename") is not None:
            files.append((cur["name"], cur["filename"], bytes(cur["buf"])))
        else:
            fields[cur["name"]] = cur["buf"].decode("utf-8", "replace")

    parser = MultipartParser(params[b"boundary"], {
        "on_part_begin": on_part_begin, "on_part_data": on_part_data, "on_part_end": on_part_end,
        "on_header_field": on_header_field, "on_header_value": on_header_value,
        "on_header_end": on_header_end, "on_headers_finished": on_headers_finished,
    })
    try:
        async for chunk in request.stream():
            if chunk:
                parser.write(chunk)
        parser.finalize()
    except ApiError:
        raise
    except Exception:
        raise ApiError(422, "Malformed multipart upload.") from None
    return fields, files


def receipt_out(r, w: Workspace) -> dict:
    """A stored receipt for the UI, with its match candidates' transaction details."""
    d = r.to_dict()
    cands = []
    for c in d.get("candidates") or []:
        t = w.transactions.get(c.get("txn_id"))
        if t is not None:
            acc = w.accounts.get(t.account_id)
            c = {**c, "date": t.date.isoformat(), "description": t.description,
                 "amount": str(t.amount), "account_label": acc.label if acc else t.account_id}
        cands.append(c)
    d["candidates"] = cands
    return d


def addon_ai_enabled(w: Workspace, registry) -> bool:
    """An add-on AI categorizer is installed and its feature (if it named one) is unlocked."""
    hook = registry.ai_categorizer_for()
    if hook is None:
        return False
    feat = registry.ai_categorizer_features.get(hook[0])
    return feat is None or w.feature_enabled(feat)


# --------------------------------------------------------------------------- session state
class Session:
    """Holds the single unlocked workspace (if any)."""

    def __init__(self, data_dir: Path, idle_timeout: Optional[float]) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.vaults_dir = self.data_dir / "vaults"
        self.idle_timeout_override = idle_timeout
        self._ws: Optional[Workspace] = None
        self.vault_path: Optional[Path] = None
        self.last_activity = time.monotonic()
        self.lock = threading.RLock()
        # fn(workspace) called whenever a workspace is opened (create / unlock / recover / demo),
        # e.g. first-run setup switching categorization to the local model once it is downloaded.
        self.open_hooks: list = []

    @property
    def ws(self) -> Optional[Workspace]:
        return self._ws

    @ws.setter
    def ws(self, w: Optional[Workspace]) -> None:
        self._ws = w
        if w is None:
            return
        for fn in list(self.open_hooks):
            try:
                fn(w)
            except Exception:  # noqa: BLE001 - a hook must never block opening a vault
                log.exception("workspace open hook")

    # ---- idle handling
    def idle_timeout(self) -> float:
        if self.idle_timeout_override is not None:
            return float(self.idle_timeout_override)
        minutes = 30
        if self.ws is not None:
            try:
                minutes = int(self.ws.settings.get("auto_lock_minutes") or 30)
            except (TypeError, ValueError):
                minutes = 30
        return minutes * 60.0

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def check_idle(self) -> bool:
        """Lock if idle too long. Returns True if it locked."""
        with self.lock:
            if self.ws is not None and time.monotonic() - self.last_activity > self.idle_timeout():
                log.info("auto-locking vault after idle timeout")
                self.lock_vault()
                return True
        return False

    # ---- vault lifecycle
    def require(self) -> Workspace:
        ws = self.ws
        if ws is None:
            raise ApiError(409, "The vault is locked. Unlock it to continue.", locked=True)
        return ws

    def lock_vault(self) -> None:
        with self.lock:
            ws, self.ws, self.vault_path = self.ws, None, None
            if ws is not None:
                try:
                    ws.vault.lock()
                except Exception:  # already locked
                    pass

    def resolve_vault_path(self, path: Optional[str], name: Optional[str]) -> Path:
        base = self.vaults_dir.resolve()
        if path:
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = base / p
        elif name:
            p = base / f"{slugify(name)}.vault"
        else:
            raise ApiError(422, "Provide the vault path (or name).")
        p = p.resolve()
        if p.parent != base:
            raise ApiError(422, "Vaults must live in the LedgerStitch data folder.")
        return p

    def list_vaults(self) -> list[dict]:
        Vault, _, _ = _vault_classes()
        out = []
        if not self.vaults_dir.is_dir():
            return out
        for p in sorted(self.vaults_dir.iterdir()):
            if not p.is_dir():
                continue
            try:
                ok = Vault.exists(p) if Vault else (p / "vault.json").exists()
            except Exception:
                ok = False
            if not ok:
                continue
            label = None
            try:
                label = Vault.read_label(p) if Vault and hasattr(Vault, "read_label") else None
            except Exception:
                label = None
            created = None
            try:
                created = json.loads((p / "vault.json").read_text("utf-8")).get("created")
            except Exception:
                created = None
            unlocked = self.vault_path is not None and p.resolve() == self.vault_path
            name = label or None
            if unlocked and self.ws is not None:
                name = self.ws.name or name       # the real (encrypted) name, once unlocked
            # Legacy vaults (folder named after the business) keep showing that name.
            if name is None and not p.stem.startswith("ws-"):
                name = p.stem
            recovery = bool(Vault and hasattr(Vault, "has_recovery_key") and Vault.has_recovery_key(p))
            out.append({"name": name or "", "label": label or "", "label_hidden": not label,
                        "created": created, "path": str(p), "slug": p.stem, "unlocked": unlocked,
                        "recovery": recovery})
        return out

    def new_vault_path(self) -> Path:
        """A random, meaningless folder name (``ws-<8 hex>.vault``)."""
        import secrets as _secrets
        for _ in range(20):
            p = self.vaults_dir / f"ws-{_secrets.token_hex(4)}.vault"
            if not p.exists():
                return p
        raise ApiError(500, "Could not allocate a vault folder.")


# --------------------------------------------------------------------------- app factory
def _dev_override_active() -> bool:
    from .security.license import dev_override_plan
    return dev_override_plan() is not None


def _code_fingerprint() -> float:
    """Newest modification time of the package's Python files (0 inside frozen apps)."""
    if getattr(sys, "frozen", False):
        return 0.0
    root = Path(__file__).parent
    try:
        return max((p.stat().st_mtime for p in root.rglob("*.py")), default=0.0)
    except OSError:
        return 0.0


def create_app(data_dir: Path | str = DEFAULT_DATA_DIR, token: str = "", *,
               port: Optional[int] = None, idle_timeout: Optional[float] = None,
               static_dir: Optional[Path] = None, server: Any = None) -> FastAPI:
    """Build the LedgerStitch FastAPI app.

    ``port``: when given, the Host header must carry exactly this port.
    ``server``: a :class:`ledgerstitch.server_mode.ServerAuth` for self-hosted server mode
    (sign-in with an admin password + session cookie instead of the localhost token).
    ``None`` (desktop) keeps the localhost-only token model unchanged.
    ``idle_timeout``: seconds before auto-lock (default: the vault's
    ``auto_lock_minutes`` setting, 30 min).
    """
    if not token or len(token) < 16:
        raise ValueError("create_app needs a strong random token")
    token_bytes = token.encode()
    if os.environ.get("LEDGERSTITCH_EDITION"):
        editions.configure(env=os.environ)
    registry = plugins.load()
    _local_ollama.set_server_mode(server is not None)   # LEDGERSTITCH_OLLAMA_URL counts in server mode only
    session = Session(Path(data_dir), idle_timeout)
    code_loaded_at = _code_fingerprint()
    static_root = Path(static_dir) if static_dir else STATIC_DIR
    Vault, VaultError, WrongPassphrase = _vault_classes()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def idle_watch():
            while True:
                await asyncio.sleep(15)
                try:
                    session.check_idle()
                except Exception:  # never let the watcher die
                    log.exception("idle watcher")
        task = asyncio.create_task(idle_watch())
        resume = getattr(app.state, "setup_resume", None)
        if resume is not None:                  # first-run setup: finish an interrupted local AI download
            try:
                resume()
            except Exception:  # noqa: BLE001 - never block startup
                log.exception("local AI setup resume")
        try:
            yield
        finally:
            task.cancel()
            session.lock_vault()

    app = FastAPI(title="LedgerStitch", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.session = session
    app.state.token = token
    app.state.plugins = registry
    app.state.server = server

    # ------------------------------------------------------------------ middleware
    def host_ok(host_header: str) -> bool:
        host = (host_header or "").strip().lower()
        if not host:
            return False
        if host.startswith("["):
            return False  # we never bind IPv6
        hostname, _, hport = host.partition(":")
        if hostname not in LOCAL_HOSTNAMES:
            return False
        if port is not None:
            return hport == str(port)
        return hport == "" or hport.isdigit()

    def origin_ok(origin: Optional[str], host_header: str) -> bool:
        if not origin:
            return True
        if origin == "null":
            return False
        o = origin.lower()
        for scheme in ("http://", "https://"):
            if o.startswith(scheme):
                return host_ok(o[len(scheme):].rstrip("/")) and o[len(scheme):].rstrip("/") == host_header.lower()
        return False

    gated = dict(registry.gated_routes)

    def gated_feature(path: str) -> Optional[str]:
        for prefix, feat in gated.items():
            p = prefix.rstrip("/")
            if path == p or path.startswith(p + "/"):
                return feat
        return None

    def locked_feature_response(path: str) -> Optional[Response]:
        feat = gated_feature(path)
        if feat is None:
            return None
        if not editions.feature_available(feat, registry):
            return JSONResponse(editions.locked_reason(feat, None, registry), status_code=402)
        w = session.ws
        if w is None:
            return None                       # the route itself answers 409 (locked)
        reason = editions.locked_reason(feat, w.license(), registry)
        return JSONResponse(reason, status_code=402) if reason else None

    def require_feature(w: Workspace, feat: str) -> None:
        """402 for a paid feature checked inside a route (routes shared with free ones)."""
        reason = editions.locked_reason(feat, w.license(), registry)
        if reason:
            raise ApiError(402, reason.pop("error"), **reason)

    def server_origin_ok(origin: Optional[str], host_header: str) -> bool:
        if not origin:
            return True                       # non-browser client; the cookie/CSRF checks still apply
        o = origin.lower()
        for scheme in ("http://", "https://"):
            if o.startswith(scheme):
                return o[len(scheme):].rstrip("/") == host_header.strip().lower()
        return False                          # includes "null"

    def server_gate(request: Request, path: str, is_api: bool) -> Optional[Response]:
        """Server mode: a signed-in session for everything but the sign-in page, plus the
        session's CSRF token on every /api and /auth call."""
        from .server_mode import is_public
        if is_public(path):
            return None
        sess = server.session_for(request)
        is_auth = path.startswith("/auth/")
        if sess is None:
            if is_api or is_auth or path.startswith(addons_mod.PREFIX + "/") or request.method not in ("GET", "HEAD"):
                return _err(401, "Please sign in.", login=True)
            return RedirectResponse("/login", status_code=303)
        if (is_api or is_auth) and not hmac.compare_digest(
                request.headers.get(TOKEN_HEADER, "").encode(), sess.csrf.encode()):
            return _err(401, "Missing or invalid session token. Reload LedgerStitch.")
        request.state.csrf = sess.csrf
        return None

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        is_api = path == "/api" or path.startswith("/api/")
        host = request.headers.get("host", "")
        if server is not None:
            if not server.host_ok(host):
                resp: Response = _err(400, "Invalid Host header.")
            elif not server_origin_ok(request.headers.get("origin"), host):
                resp = _err(403, "Cross-origin requests are not allowed.")
            else:
                resp = server_gate(request, path, is_api)
                if resp is None:
                    if is_api:
                        session.check_idle()
                        if path != "/api/status":
                            session.touch()
                    locked = locked_feature_response(path) if is_api else None
                    resp = locked if locked is not None else await call_next(request)
            for k, v in SECURITY_HEADERS.items():
                resp.headers.setdefault(k, v)
            if server.is_https(request):
                resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
            if is_api or path in ("/", "/index.html", "/login") or path.startswith("/auth/"):
                resp.headers["Cache-Control"] = "no-store"
                resp.headers["Pragma"] = "no-cache"
            elif path.endswith(REVALIDATE_SUFFIXES):
                resp.headers.setdefault("Cache-Control", "no-cache")
            return resp
        if not host_ok(host):
            resp = _err(400, "Invalid Host header. Open LedgerStitch at http://127.0.0.1.")
        elif not origin_ok(request.headers.get("origin"), host):
            resp = _err(403, "Cross-origin requests are not allowed.")
        elif is_api and not hmac.compare_digest(
                request.headers.get(TOKEN_HEADER, "").encode(), token_bytes):
            resp = _err(401, "Missing or invalid session token. Reload LedgerStitch from the link it opened.")
        else:
            if is_api:
                session.check_idle()
                if path != "/api/status":   # status polling doesn't count as activity
                    session.touch()
            locked = locked_feature_response(path) if is_api else None
            resp = locked if locked is not None else await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if is_api or path in ("/", "/index.html"):
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["Pragma"] = "no-cache"
        elif path.endswith(REVALIDATE_SUFFIXES):
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp

    # ------------------------------------------------------------------ exception handlers
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return _err(e.status, e.message, **e.extra)

    @app.exception_handler(StoreError)
    async def _store_error(_: Request, e: StoreError):
        extra = {"needs_mapping": True} if isinstance(e, NeedsMapping) else {}
        if isinstance(e, LimitError):
            extra["upgrade"] = True
        return _err(_store_error_status(e), str(e), **extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        parts = []
        for err in e.errors()[:5]:
            loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path"))
            parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
        return _err(422, "; ".join(parts) or "Invalid request.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        return _err(e.status_code, str(e.detail) if e.detail else "Error")

    if VaultError is not None:
        @app.exception_handler(VaultError)
        async def _vault_error(_: Request, e: Exception):
            if WrongPassphrase is not None and isinstance(e, WrongPassphrase):
                return _err(401, "Wrong passphrase.")
            w = session.ws
            if w is None or getattr(w.vault, "locked", False):
                session.lock_vault()
                return _err(409, "The vault is locked. Unlock it to continue.", locked=True)
            return _err(400, str(e) or "Vault error.")

    # ------------------------------------------------------------------ helpers
    def ws() -> Workspace:
        return session.require()

    def status_payload() -> dict:
        w = session.ws
        out: dict[str, Any] = {
            "version": __version__,
            "edition": editions.current_edition(registry),
            # The UI is served from disk on every request, the Python code only at startup:
            # if the code changed underneath us, the UI and API may disagree.
            "restart_required": _code_fingerprint() > code_loaded_at,
            "unlocked": w is not None,
            "vault_name": w.name if w else None,
            "vault_path": str(session.vault_path) if session.vault_path else None,
            "plan": None, "limits": None, "license": None,
            "ai": {"enabled": False, "provider": None, "model": None},
            "counts": None,
            "auto_lock_minutes": round(session.idle_timeout() / 60, 2),
            "features": editions.features_status(None, registry),
            "plans": editions.plan_labels(registry),
            "ai_providers": sorted(registry.ai_provider_ids),     # provider ids the add-ons add
            "ui": addons_mod.ui_status(registry, None),
            "source": {"url": SOURCE_URL, "license": SOURCE_LICENSE},
            "signup_url": editions.signup_url(),      # "Get the full product" (None = no link)
            "recommended_local_model": _local_ollama.local_model(),
            "ollama_operator_url": _local_ollama.operator_url(),   # server mode: the compose Ollama service
            "dev_override": _dev_override_active(),
            "server_mode": server is not None,
            "onboarding": onboarding_status(session),
        }
        if w is not None:
            try:
                lic = w.license()
                plan = getattr(lic, "plan", "free") if getattr(lic, "valid", False) else "free"
                out["license"] = {"plan": plan, "email": getattr(lic, "email", None) or None,
                                  "expires": to_jsonable(getattr(lic, "expires", None)),
                                  "valid": bool(getattr(lic, "valid", False))}
                out["dev_override"] = getattr(lic, "reason", "") == "developer override"
            except Exception:
                plan = "free"
            out["plan"] = plan
            out["limits"] = None                  # the core has no usage caps
            out["features"] = editions.features_status(w.license(), registry)
            out["ui"] = addons_mod.ui_status(registry, w.license())
            ai_on = bool(w.settings.get("ai_enabled")) and (
                w.settings.get("provider") == "ollama" or addon_ai_enabled(w, registry))
            out["ai"] = {"enabled": ai_on, "provider": w.settings.get("provider") if ai_on else None,
                         "model": (w.settings.get("model") or None) if ai_on else None}
            out["counts"] = w.counts()
            out["review_count"] = sum(1 for t in w.transactions.values()
                                      if t.status.value == "review" and not t.duplicate_of)
            out["tax_year"] = w.default_year()
            out["recovery"] = recovery_info(w)
            out["backup"] = w.backup_status()
        return out

    def recovery_info(w: Workspace) -> dict:
        return {"supported": hasattr(w.vault, "set_recovery_code"),
                "enabled": bool(getattr(w.vault, "has_recovery", False)),
                "created": getattr(w.vault, "recovery_created", None)}

    def txn_out(t, w: Workspace) -> dict:
        d = t.to_dict()
        acc = w.accounts.get(t.account_id)
        d["account_label"] = acc.label if acc else t.account_id
        d["signed_amount"] = str(t.signed_amount)
        d["receipt_id"] = w.receipt_for_txn().get(t.id)
        return d

    # ------------------------------------------------------------------ add-on routes (first, so
    # they take precedence over core catch-alls such as /api/export/{fmt})
    for ns, install in registry.route_installers:
        try:
            install(app, ws)
        except Exception as e:  # noqa: BLE001 - a broken add-on must not break the core app
            log.warning("plugin %s failed to add routes: %s", ns, type(e).__name__)
            registry.errors.append(f"{ns}: routes: {type(e).__name__}")

    # ------------------------------------------------------------------ status & vaults
    @app.get("/api/source")
    def get_source():
        """AGPL-3.0 §13: the Corresponding Source of the version you are running."""
        return {"url": SOURCE_URL, "license": SOURCE_LICENSE, "version": __version__,
                "edition": editions.current_edition(registry),
                "note": "LedgerStitch Community is free software under the GNU AGPL v3. "
                        "Add-ons, if installed, are licensed separately."}

    @app.get("/api/status")
    def get_status():
        return status_payload()

    @app.get("/api/vaults")
    def list_vaults():
        return session.list_vaults()

    @app.post("/api/vaults", status_code=201)
    def create_vault(body: VaultCreate):
        if Vault is None:
            raise ApiError(500, "Vault module unavailable.")
        name = body.name.strip()
        if not name:
            raise ApiError(422, "Vault name is required.")
        if len(body.passphrase) < MIN_PASSPHRASE:
            raise ApiError(422, f"Passphrase must be at least {MIN_PASSPHRASE} characters.")
        session.vaults_dir.mkdir(parents=True, exist_ok=True)
        path = session.new_vault_path()
        label = (body.label or "").strip()
        with session.lock:
            session.lock_vault()
            v = Vault.create(path, body.passphrase, name, label=label)
            code = v.set_recovery_code(body.passphrase) if body.recovery else None
            w = Workspace(v)
            w.save()
            session.ws, session.vault_path = w, path.resolve()
            session.touch()
        out = status_payload()
        if code:
            out["recovery_code"] = code         # shown once; not stored anywhere
        return out

    @app.post("/api/unlock")
    def unlock(body: VaultUnlock):
        if Vault is None:
            raise ApiError(500, "Vault module unavailable.")
        path = session.resolve_vault_path(body.path, body.name)
        try:
            exists = Vault.exists(path)
        except Exception:
            exists = False
        if not exists:
            raise ApiError(404, "No vault found at that location.")
        with session.lock:
            session.lock_vault()
            try:
                v = Vault.open(path, body.passphrase)
            except WrongPassphrase:
                raise ApiError(401, "Wrong passphrase.") from None
            session.ws, session.vault_path = Workspace(v), path
            session.touch()
        return status_payload()

    @app.post("/api/unlock/recovery")
    def unlock_with_recovery(body: RecoveryUnlock):
        """Forgotten passphrase: open with the recovery code and set a new passphrase
        in the same step (the old passphrase stops working)."""
        from .security.vault import WrongRecoveryCode
        if Vault is None:
            raise ApiError(500, "Vault module unavailable.")
        path = session.resolve_vault_path(body.path, body.name)
        if not Vault.exists(path):
            raise ApiError(404, "No vault found at that location.")
        if not Vault.has_recovery_key(path):
            raise ApiError(422, "This workspace has no recovery key.")
        if len(body.new_passphrase) < MIN_PASSPHRASE:
            raise ApiError(422, f"New passphrase must be at least {MIN_PASSPHRASE} characters.")
        with session.lock:
            session.lock_vault()
            try:
                v = Vault.recover(path, body.recovery_code, body.new_passphrase)
            except WrongRecoveryCode:
                raise ApiError(401, "That recovery code didn't unlock this workspace.") from None
            except VaultError as e:     # malformed code or header: never echo the code
                raise ApiError(422, str(e) or "Recovery failed.") from None
            session.ws, session.vault_path = Workspace(v), path
            session.touch()
        return status_payload()

    @app.post("/api/recovery")
    def set_recovery(body: PassphraseConfirm):
        """Create or regenerate the recovery key (an earlier code stops working)."""
        w = ws()
        if not hasattr(w.vault, "set_recovery_code"):
            raise ApiError(400, "This workspace does not support recovery keys.")
        try:
            code = w.vault.set_recovery_code(body.passphrase)
        except WrongPassphrase:
            raise ApiError(401, "Wrong passphrase.") from None
        return {"recovery_code": code, **recovery_info(w)}

    @app.post("/api/recovery/remove")
    def remove_recovery(body: PassphraseConfirm):
        w = ws()
        if not hasattr(w.vault, "remove_recovery_code"):
            raise ApiError(400, "This workspace does not support recovery keys.")
        try:
            w.vault.remove_recovery_code(body.passphrase)
        except WrongPassphrase:
            raise ApiError(401, "Wrong passphrase.") from None
        return recovery_info(w)

    # ------------------------------------------------------------------ encrypted backup & restore
    @app.get("/api/backup")
    def backup():
        """The open workspace's encrypted files as one ``.lsbackup`` download."""
        from .security.backup import BACKUP_SUFFIX, create_backup
        w = ws()
        if not hasattr(w.vault, "entry_files"):
            raise ApiError(400, "This workspace cannot be backed up from here.")
        tmp = Path(tempfile.mkdtemp(prefix="ledgerstitch-backup-"))
        name = f"{slugify(w.name or 'workspace')}-{date.today().isoformat()}{BACKUP_SUFFIX}"
        try:
            create_backup(w.vault, tmp / name)
            w.mark_backup()
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return FileResponse(tmp / name, media_type="application/octet-stream", filename=name,
                            background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True))

    @app.post("/api/vaults/restore", status_code=201)
    async def restore_vault(request: Request):
        """Import a ``.lsbackup`` into a NEW vault folder (never over an existing one).
        The restored vault stays locked: unlock it with its passphrase or recovery code."""
        from .security.backup import BackupError, restore_backup
        _, files = await read_multipart(request, max_file_bytes=MAX_BACKUP_UPLOAD_BYTES, max_files=1)
        if not files:
            raise ApiError(422, "Attach a .lsbackup file.")
        data = files[0][2]
        session.vaults_dir.mkdir(parents=True, exist_ok=True)
        path = session.new_vault_path()
        try:
            await run_in_threadpool(restore_backup, data, path)
        except BackupError as e:
            raise ApiError(422, f"That backup can't be restored: {e}.") from None
        vaults = session.list_vaults()
        restored = next((v for v in vaults if v["slug"] == path.stem), None)
        return {"restored": restored, "vaults": vaults}

    @app.post("/api/lock")
    def lock():
        session.lock_vault()
        return status_payload()

    @app.post("/api/passphrase")
    def change_passphrase(body: PassphraseChange):
        w = ws()
        if len(body.new) < MIN_PASSPHRASE:
            raise ApiError(422, f"New passphrase must be at least {MIN_PASSPHRASE} characters.")
        try:
            w.vault.change_passphrase(body.old, body.new)
        except WrongPassphrase:
            raise ApiError(401, "Current passphrase is wrong.") from None
        return {"ok": True}

    @app.post("/api/vaults/delete")
    def delete_vault(body: VaultDelete):
        """Permanently delete the open workspace (passphrase + typed DELETE required)."""
        w = ws()
        if body.confirm.strip() != CONFIRM_WORD:
            raise ApiError(422, f"Type {CONFIRM_WORD} to confirm.")
        path = session.vault_path
        try:
            Vault.open(path, body.passphrase).lock()     # proves the passphrase; nothing else
        except WrongPassphrase:
            raise ApiError(401, "Wrong passphrase.") from None
        with session.lock:
            with w.lock:
                addon_errors = w.run_data_purgers("workspace")
                removed = w.vault.destroy()
            session.ws, session.vault_path = None, None
        out = status_payload()
        out["deleted"] = {"files": removed, "addon_errors": addon_errors}
        return out

    # ------------------------------------------------------------------ your data: purge & export
    @app.post("/api/data/purge")
    def purge_data(body: DataPurge):
        if body.confirm.strip() != CONFIRM_WORD:
            raise ApiError(422, f"Type {CONFIRM_WORD} to confirm.")
        return ws().purge(body.scope)

    @app.get("/api/data/export")
    def export_data():
        w = ws()
        tmp = Path(tempfile.mkdtemp(prefix="ledgerstitch-data-"))
        try:
            out = w.export_data(tmp)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return FileResponse(out, media_type="application/zip", filename=out.name,
                            background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True))

    # ------------------------------------------------------------------ imports
    @app.post("/api/imports")
    async def upload(request: Request):
        w = ws()
        fields, files = await read_multipart(request)
        files = [f for f in files if f[0] in ("file", "file[]", "files", "files[]")] or files
        if not files:
            raise ApiError(422, "Attach at least one file.")
        label = (fields.get("account_label") or "").strip() or None
        results, failures = [], []
        for _, filename, data in files:
            try:
                r = await run_in_threadpool(w.add_import, filename, data, label)
                results.append(r.to_dict())
            except StoreError as e:
                item = {"filename": Path(filename).name, "error": str(e),
                        "status": _store_error_status(e),
                        "needs_mapping": isinstance(e, NeedsMapping)}
                results.append(item)
                failures.append(item)
        if failures and len(failures) == len(results):
            first = failures[0]
            extra = {"limits": w.limits()} if first["status"] == 402 else {}
            raise ApiError(first["status"], first["error"], results=results,
                           needs_mapping=first["needs_mapping"], upgrade=first["status"] == 402, **extra)
        return results

    # ------------------------------------------------------------------ pay stubs (in memory only:
    # parsed, projected and returned; never stored in the vault or written to disk)
    def _flag(v: Optional[str]) -> bool:
        return (v or "").strip().lower() in ("1", "true", "yes", "on")

    async def paystub_report(request: Request):
        from .payroll import FILING_STATUSES, FREQUENCIES, OWNERS, analyze_paystubs
        ws()                                   # same gate as every data route: an unlocked vault
        fields, files = await read_multipart(request)
        if not files:
            raise ApiError(422, "Attach at least one pay stub.")
        owner = (fields.get("owner") or "you").strip().lower()
        if owner not in OWNERS:
            raise ApiError(422, "owner must be 'you' or 'spouse'.")
        freq = (fields.get("frequency") or "").strip().lower() or None
        if freq is not None and freq not in FREQUENCIES:
            raise ApiError(422, f"frequency must be one of {', '.join(FREQUENCIES)}.")
        fs = (fields.get("filing_status") or "").strip().lower() or None
        if fs is not None and fs not in FILING_STATUSES:
            raise ApiError(422, f"filing_status must be one of {', '.join(FILING_STATUSES)}.")
        try:
            year = int(fields["year"]) if (fields.get("year") or "").strip() else None
        except ValueError:
            raise ApiError(422, "year must be a number.")
        items = [(Path(name).name, data, "spouse" if field in ("spouse", "spouse_file", "spouse[]") else owner)
                 for field, name, data in files]
        try:
            return await run_in_threadpool(
                analyze_paystubs, items, final=_flag(fields.get("final")), frequency=freq, filing_status=fs,
                spouse_no_earned_income=_flag(fields.get("spouse_no_earned_income")), year=year)
        except ValueError as e:
            raise ApiError(422, str(e))

    @app.post("/api/paystubs")
    async def paystubs(request: Request):
        """Multipart: ``file`` (repeatable; owner from the ``owner`` field), ``spouse_file`` (repeatable),
        ``final``, ``frequency``, ``filing_status``, ``spouse_no_earned_income``, ``year``."""
        return to_jsonable(await paystub_report(request))

    @app.post("/api/paystubs/export/{fmt}")
    async def paystubs_export(fmt: str, request: Request):
        if fmt not in ("csv", "xlsx"):
            raise ApiError(404, "Unknown export format. Use csv or xlsx.")
        report = await paystub_report(request)
        tmp = Path(tempfile.mkdtemp(prefix="ledgerstitch-export-"))
        name = f"estimated-w2-{report.year}.{fmt}"
        try:
            if fmt == "csv":
                from .reports.paystub_export import export_paystub_csv
                out, media = export_paystub_csv(report, tmp / name), "text/csv"
            else:
                from .reports.paystub_export import export_paystub_xlsx
                out = export_paystub_xlsx(report, tmp / name)
                media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return FileResponse(out, media_type=media, filename=name,
                            background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True))

    # ------------------------------------------------------------------ receipts (stored ones;
    # reading new receipts is an add-on route, POST /api/receipts)
    @app.get("/api/receipts")
    def list_receipts():
        w = ws()
        return [receipt_out(r, w) for r in w.list_receipts()]

    @app.get("/api/receipts/{rid}/image")
    def receipt_image(rid: str):
        w = ws()
        data, mime = w.receipt_image(rid)
        return Response(content=data, media_type=mime,
                        headers={"Cache-Control": "no-store", "Content-Disposition": "inline"})

    @app.patch("/api/receipts/{rid}")
    async def patch_receipt(rid: str, request: Request):
        w = ws()
        body = await request.json()
        if not isinstance(body, dict):
            raise ApiError(422, "Expected a JSON object.")
        return receipt_out(w.update_receipt(rid, body), w)

    @app.post("/api/receipts/{rid}/match")
    async def match_receipt_route(rid: str, request: Request):
        w = ws()
        body = await request.json()
        txn_id = body.get("txn_id") if isinstance(body, dict) else None
        return receipt_out(w.link_receipt(rid, txn_id or None), w)

    @app.delete("/api/receipts/{rid}")
    def delete_receipt_route(rid: str):
        w = ws()
        w.delete_receipt(rid)
        return {"ok": True}

    @app.post("/api/imports/preview")
    async def upload_preview(request: Request):
        w = ws()
        _, files = await read_multipart(request, max_files=1)
        if not files:
            raise ApiError(422, "Attach a file.")
        _, filename, data = files[0]
        return to_jsonable(await run_in_threadpool(w.preview_import, filename, data))

    @app.post("/api/imports/mapped")
    async def upload_mapped(request: Request):
        w = ws()
        fields, files = await read_multipart(request, max_files=1)
        if not files:
            raise ApiError(422, "Attach a file.")
        try:
            mapping = json.loads(fields.get("mapping") or "")
        except ValueError:
            raise ApiError(422, "mapping must be JSON.") from None
        if not isinstance(mapping, dict):
            raise ApiError(422, "mapping must be a JSON object.")
        label = (fields.get("account_label") or "").strip() or None
        _, filename, data = files[0]
        r = await run_in_threadpool(w.add_import, filename, data, label, mapping)
        return r.to_dict()

    @app.get("/api/imports")
    def list_imports():
        w = ws()
        counts: dict[str, int] = {}
        for t in w.transactions.values():
            counts[t.import_id] = counts.get(t.import_id, 0) + 1
        out = []
        for rec in reversed(w.imports):
            acc = w.accounts.get(rec.get("account_id"))
            out.append({**rec, "account_label": acc.label if acc else None,
                        "transactions": counts.get(rec.get("import_id"), 0)})
        return out

    @app.delete("/api/imports/{import_id}")
    def delete_import(import_id: str):
        return ws().delete_import(import_id)

    # ------------------------------------------------------------------ accounts
    @app.get("/api/accounts")
    def list_accounts():
        w = ws()
        counts: dict[str, int] = {}
        for t in w.transactions.values():
            counts[t.account_id] = counts.get(t.account_id, 0) + 1
        return [{**to_jsonable(a), "transactions": counts.get(a.id, 0)} for a in w.accounts.values()]

    @app.delete("/api/accounts/{account_id}")
    def delete_account(account_id: str):
        return ws().delete_account(account_id)

    @app.patch("/api/accounts/{account_id}")
    def patch_account(account_id: str, body: AccountPatch):
        return to_jsonable(ws().update_account(account_id, **body.model_dump(exclude_unset=True)))

    # ------------------------------------------------------------------ transactions
    @app.get("/api/transactions")
    def list_txns(lane: Optional[str] = None, category: Optional[str] = None,
                  account: Optional[str] = None, status: Optional[str] = None,
                  q: Optional[str] = None, year: Optional[int] = None,
                  limit: int = 500, offset: int = 0, include_duplicates: bool = True,
                  sort: Optional[str] = None, order: Optional[str] = None,
                  date_from: Optional[str] = None, date_to: Optional[str] = None,
                  direction: Optional[str] = None):
        w = ws()
        limit = max(0, min(limit, 100_000))
        total, items = w.list_transactions({
            "lane": lane, "category": category, "account": account, "status": status,
            "date_from": date_from, "date_to": date_to, "direction": direction,
            "q": q, "year": year, "limit": limit, "offset": max(0, offset),
            "include_duplicates": include_duplicates, "sort": sort, "order": order})
        return {"total": total, "limit": limit, "offset": offset,
                "items": [txn_out(t, w) for t in items]}

    @app.patch("/api/transactions/{txn_id}")
    def patch_txn(txn_id: str, body: TxnPatch):
        w = ws()
        fields = body.model_dump(exclude_unset=True)
        remember = bool(fields.pop("remember", False))
        t, changed = w.update_transaction(txn_id, remember=remember, **fields)
        return {"transaction": txn_out(t, w), "changed": changed}

    @app.post("/api/transactions/delete")
    def delete_txns(body: TxnDelete):
        return ws().delete_transactions(body.ids)

    @app.post("/api/transactions/bulk")
    def bulk(body: BulkPatch):
        fields = body.model_dump(exclude_unset=True)
        ids = fields.pop("ids")
        return {"updated": ws().bulk_update(ids, **fields)}

    # ------------------------------------------------------------------ categories & rules
    @app.get("/api/categories")
    def categories():
        return ws().categories()

    @app.post("/api/categories", status_code=201)
    def add_category(body: CategoryCreate):
        return ws().add_category(body.name, body.lane, body.schedule_c)

    @app.get("/api/rules")
    def rules():
        return [r.to_dict() for r in ws().rules]

    @app.post("/api/rules", status_code=201)
    def add_rule(body: RuleCreate):
        d = body.model_dump()
        apply = d.pop("apply")
        rule, changed = ws().add_rule(d, apply=apply)
        return {"rule": rule.to_dict(), "changed": changed}

    @app.delete("/api/rules/{rule_id}")
    def delete_rule(rule_id: str):
        ws().delete_rule(rule_id)
        return {"ok": True}

    # ------------------------------------------------------------------ categorize & AI
    @app.post("/api/categorize")
    def categorize():
        w = ws()
        try:
            result = w.categorize()
        except StoreError:
            raise
        except Exception as e:  # provider/network failures: don't leak payloads
            log.warning("categorize failed: %s", type(e).__name__)
            raise ApiError(502, f"Categorization failed: {type(e).__name__}: {str(e)[:200]}") from None
        return to_jsonable(result)

    @app.get("/api/ai/preview")
    def ai_preview():
        """Exactly what an AI add-on would receive: the redacted descriptors (core, every
        edition, so the privacy boundary stays auditable). Add-ons may add details of their
        own when their feature is unlocked."""
        from .ai.categorizer import AI_SHARED_SETTINGS, _cat_dicts, _plugin_settings
        from .security.redact import payload_preview
        w = ws()
        descriptors = w.ai_preview()
        unlocked = addon_ai_enabled(w, registry)
        local = w.settings.get("provider") == "ollama" and not unlocked
        out = {"descriptors": descriptors, "payload": payload_preview(descriptors),
               "count": len(descriptors),
               "enabled": bool(w.settings.get("ai_enabled")) and (unlocked or local),
               "provider": w.settings.get("provider") if (unlocked or local) else None,
               "available": bool(registry.ai_categorizers), "unlocked": unlocked,
               "shared_settings": list(AI_SHARED_SETTINGS)}
        if local:
            from .ai.local_ollama import full_prompt
            out["local"] = True
            out["full_prompt"] = full_prompt(descriptors, _cat_dicts(w.categories()),
                                             (w.settings.get("business_profile") or "")[:500] or None)
        if unlocked:
            cats = _cat_dicts(w.categories())
            for ns, extend in registry.preview_extenders:
                try:
                    extra = extend(list(descriptors), [dict(c) for c in cats],
                                   _plugin_settings(w.settings, registry, ns), w.plugin_store(ns)) or {}
                except Exception as e:  # noqa: BLE001
                    log.warning("plugin %s preview failed: %s", ns, type(e).__name__)
                    continue
                out.update({k: v for k, v in extra.items() if k not in ("descriptors", "payload", "count")})
        return out

    @app.post("/api/ai/local-test")
    def ai_local_test():
        """Community "ai.local": send 3 made-up merchant names to Ollama on this computer."""
        from .ai.categorizer import _cat_dicts
        from .ai.local_ollama import test_connection
        w = ws()
        return test_connection(dict(w.settings), _cat_dicts(w.categories()))

    # ------------------------------------------------------------------ settings & license
    @app.get("/api/settings")
    def get_settings():
        return ws().public_settings()

    @app.put("/api/settings")
    async def put_settings(request: Request):
        try:
            body = await request.json()
        except Exception:
            raise ApiError(422, "Body must be JSON.") from None
        if not isinstance(body, dict):
            raise ApiError(422, "Body must be a JSON object.")
        # tolerate read-only keys the UI may echo back
        for k in list(body):
            if k.startswith("has_"):
                body.pop(k)
        return ws().update_settings(body)

    @app.post("/api/license")
    def set_license(body: LicenseBody):
        w = ws()
        w.set_license(body.key)
        return status_payload()

    # ------------------------------------------------------------------ reports
    @app.get("/api/summary")
    def summary(year: Optional[int] = None):
        from .reports.summary import summarize
        w = ws()
        y = year or w.default_year()
        return to_jsonable(summarize(list(w.transactions.values()), y))

    EXPORT_FORMATS = ("xlsx", "csv", "qbo", "iif")   # add-ons install their own /api/export/<x> routes

    @app.get("/api/export/{fmt}")
    def export(fmt: str, year: Optional[int] = None):
        w = ws()
        if fmt not in EXPORT_FORMATS:
            raise ApiError(404, "Unknown export format. Use xlsx, csv, qbo or iif.")
        y = year or w.default_year()
        txns = w.txns_for_year(y)
        biz = w.settings.get("business_name") or w.name
        base = f"{slugify(w.settings.get('business_name') or w.name or 'ledgerstitch')}-{y}"
        tmp = Path(tempfile.mkdtemp(prefix="ledgerstitch-export-"))
        try:
            if fmt == "xlsx":
                from .reports.excel import export_excel
                name, media = f"{base}-expense-report.xlsx", \
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                out = export_excel(txns, w.accounts, tmp / name, biz, y)
            elif fmt == "csv":
                from .reports.csv_export import export_csv
                name, media = f"{base}-transactions.csv", "text/csv"
                out = export_csv(txns, tmp / name, y)
            elif fmt == "qbo":
                from .reports.quickbooks import export_qbo_csv
                name, media = f"{base}-quickbooks-online.csv", "text/csv"
                out = export_qbo_csv(txns, w.accounts, tmp / name, y)
            else:
                from .reports.quickbooks import export_iif
                name, media = f"{base}-quickbooks-desktop.iif", "application/octet-stream"
                out = export_iif(txns, w.accounts, tmp / name, None, y)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        out = Path(out or (tmp / name))
        if out.suffix.lower() == ".zip":          # e.g. QBO export split per account
            name, media = Path(name).with_suffix(".zip").name, "application/zip"
        elif out.suffix.lower() != Path(name).suffix.lower():
            name = Path(name).with_suffix(out.suffix).name
        return FileResponse(out, media_type=media, filename=name,
                            background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True))

    # First-run setup (core): /api/setup/check, /ollama/install, /ollama/pull, /jobs/{id}, /complete, /reset, /demo
    from .onboarding import routes as setup_routes
    setup_routes.install(app, session, port=port, host="0.0.0.0" if server is not None else "127.0.0.1",
                         server_mode=server is not None, status_payload=status_payload,
                         vault_cls=Vault, workspace_cls=Workspace, min_passphrase=MIN_PASSPHRASE)
    # Server mode: /login, /auth/*, /healthz
    if server is not None:
        from .server_mode import install_routes as install_server_routes
        install_server_routes(app, server, static_dir=static_root, on_logout=session.lock_vault)

    # ------------------------------------------------------------------ add-on browser files
    @app.get(addons_mod.PREFIX + "/index.json", include_in_schema=False)
    def addons_index():
        """Installed add-ons, for demo mode (which answers /api in the browser)."""
        return addons_mod.index_payload(registry)

    @app.get(addons_mod.PREFIX + "/{ns}/{rest:path}", include_in_schema=False)
    def addon_file(ns: str, rest: str):
        found = addons_mod.resolve_static(registry, ns, rest)
        if found is None:
            raise ApiError(404, "Not found.")
        f, media = found
        return FileResponse(f, media_type=media)

    # unknown /api routes -> JSON 404 (before the static mount swallows them)
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   include_in_schema=False)
    def api_404(rest: str):
        raise ApiError(404, "Not found.")

    # ------------------------------------------------------------------ static UI
    def render_index(request: Request) -> HTMLResponse:
        # Desktop: the per-launch token. Server mode: this session's CSRF token.
        page_token = getattr(request.state, "csrf", None) if server is not None else token
        if not page_token:
            return RedirectResponse("/login", status_code=303)
        index = static_root / "index.html"
        if index.is_file():
            text = index.read_text("utf-8")
        else:
            text = ("<!doctype html><html><head><meta charset='utf-8'><title>LedgerStitch</title>"
                    f"<meta name='ledgerstitch-token' content='{TOKEN_PLACEHOLDER}'></head>"
                    "<body><p>LedgerStitch UI files are missing.</p></body></html>")
        return HTMLResponse(text.replace(TOKEN_PLACEHOLDER, html.escape(page_token, quote=True)))

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return render_index(request)

    @app.get("/index.html", include_in_schema=False)
    def index_html(request: Request):
        return render_index(request)

    if static_root.is_dir():
        app.mount("/", StaticFiles(directory=static_root, html=False, check_dir=False), name="static")

    return app


__all__ = ["create_app", "read_multipart", "TOKEN_HEADER", "MAX_UPLOAD_BYTES", "store_mod"]
