# SPDX-License-Identifier: AGPL-3.0-only
"""Optional local AI during first-run setup: detect, install and start Ollama, pull a model.

Rules (see docs/SECURITY.md, "First-run setup"):

* Nothing here runs unless the user agreed in the setup consent dialog (the API requires
  ``consent: true`` and records the consent in app.json; a pull without a recorded consent
  for this model is refused).
* Local AI uses exactly one model, :data:`RECOMMENDED_MODEL` (GLM-4.7-Flash, see docs/AI.md).
* Installer downloads go only to HTTPS URLs on ``ALLOWED_HOSTS`` (the official
  ollama.com download links and the GitHub release storage they redirect to). Every
  redirect hop is checked against the allowlist; anything else aborts the download.
* A downloaded installer is executed only after the operating system says it carries a
  valid code signature from Ollama (macOS ``codesign``, Windows Authenticode).
* Linux: we never run ``curl | sh`` for the user. We show the official command, the user
  runs it in a terminal (it needs sudo), and we detect Ollama when it comes up.
* Talking to Ollama itself only ever goes to a loopback address (the same rule as the
  ``ai.local`` categorizer), or in server mode to exactly the operator-configured
  ``LEDGERSTITCH_OLLAMA_URL`` (the compose ``ollama`` service). The model download happens
  inside Ollama, from its registry. In server mode nothing is ever installed.
* No user data is involved anywhere in this module.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional
from urllib.parse import urljoin, urlsplit

from ..ai.local_ollama import DEFAULT_URL, RECOMMENDED_MODEL, connect_url, local_model

log = logging.getLogger(__name__)

GB = 1024 ** 3
# The one local model (RECOMMENDED_MODEL in ledgerstitch.ai.local_ollama is the single source of
# truth; docs/AI.md explains the choice). No picker: setup offers exactly this model.
MODELS = {
    RECOMMENDED_MODEL: {"label": "GLM-4.7-Flash", "size": "19 GB", "bytes": 19 * 10 ** 9,
                        "ram": "24 GB", "recommended": True},
}
RECOMMENDED_SIZE = MODELS[RECOMMENDED_MODEL]["size"]
# Below these the consent dialog warns and needs a second "Download anyway" click.
MIN_DISK_BYTES = 25 * GB          # 19 GB model + room for Ollama's temp files during the pull
MIN_RAM_BYTES = 24 * GB           # 30B-class MoE at Q4: ~19 GB weights + context + the OS
DOCKER_FIX = "docker compose up -d ollama"


def model_info(model: Optional[str] = None) -> dict:
    """Label/size of ``model`` (default: the local model). A development override
    (LEDGERSTITCH_LOCAL_MODEL) has no catalog entry and is shown by its tag."""
    model = model or local_model()
    info = MODELS.get(model)
    if info:
        return {"model": model, **info}
    return {"model": model, "label": model, "size": "", "bytes": 0, "ram": "", "recommended": True}


def allowed_model(model: Optional[str]) -> bool:
    """Setup only ever downloads the one local model."""
    return bool(model) and model == local_model()

OFFICIAL_URLS = {
    "Darwin": "https://ollama.com/download/Ollama-darwin.zip",
    "Windows": "https://ollama.com/download/OllamaSetup.exe",
}
LINUX_COMMAND = "curl -fsSL https://ollama.com/install.sh | sh"
ALLOWED_HOSTS = frozenset({
    "ollama.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})
MAX_DOWNLOAD_BYTES = 2 * 1024 ** 3
MAX_REDIRECTS = 6
SIGNER_HINT = "ollama"            # the code-signing identity must mention Ollama


class SetupError(Exception):
    pass


class DisallowedURL(SetupError):
    pass


# ---------------------------------------------------------------- URL allowlist
def check_download_url(url: str) -> str:
    """Raise DisallowedURL unless ``url`` is https on an allowlisted host (default port)."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise DisallowedURL("Not a valid URL.") from None
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https":
        raise DisallowedURL(f"Only HTTPS downloads are allowed (got {parts.scheme or 'no'} scheme).")
    if parts.username or parts.password:
        raise DisallowedURL("Download URLs must not carry credentials.")
    if port not in (None, 443):
        raise DisallowedURL("Download URLs must use the default HTTPS port.")
    if host not in ALLOWED_HOSTS:
        raise DisallowedURL(f"{host or 'That host'} is not an official Ollama download host.")
    return url


# ---------------------------------------------------------------- downloader
class Downloader:
    """HTTPS-only streaming download with per-hop allowlist checks (redirects followed by hand)."""

    def __init__(self, client_factory: Optional[Callable[[], Any]] = None, timeout: float = 30.0):
        self._client_factory = client_factory
        self.timeout = timeout

    def _client(self):
        if self._client_factory:
            return self._client_factory()
        import httpx
        return httpx.Client(follow_redirects=False, trust_env=False, timeout=self.timeout,
                            headers={"User-Agent": "LedgerStitch-setup"})

    def download(self, url: str, dest: Path, on_progress: Callable[[int, int], None] = lambda d, t: None) -> Path:
        check_download_url(url)
        with self._client() as client:
            for _ in range(MAX_REDIRECTS + 1):
                with client.stream("GET", url) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        loc = r.headers.get("location")
                        if not loc:
                            raise SetupError("Download redirect without a location.")
                        url = check_download_url(urljoin(url, loc))
                        continue
                    if r.status_code != 200:
                        raise SetupError(f"Download failed (HTTP {r.status_code}).")
                    total = int(r.headers.get("content-length") or 0)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise SetupError("The download is unexpectedly large; stopped.")
                    done = 0
                    with open(dest, "wb") as f:
                        for chunk in r.iter_bytes():
                            done += len(chunk)
                            if done > MAX_DOWNLOAD_BYTES:
                                raise SetupError("The download is unexpectedly large; stopped.")
                            f.write(chunk)
                            on_progress(done, total)
                    if total and done != total:
                        raise SetupError("The download was cut short. Try again.")
                    return dest
        raise SetupError("Too many redirects.")


# ---------------------------------------------------------------- detection (loopback only)
def detect(url: str = DEFAULT_URL, *, timeout: float = 1.5, client_factory: Optional[Callable[[], Any]] = None,
           system: Optional[str] = None) -> dict:
    """Is Ollama running on this computer? Which models does it have? Is it installed?"""
    out: dict[str, Any] = {"running": False, "version": None, "models": [], "url": None,
                           "installed": False, "path": None}
    try:
        base = connect_url(url or DEFAULT_URL)
    except ValueError as e:
        out["error"] = str(e)
        return out
    out["url"] = base
    try:
        if client_factory:
            client = client_factory()
        else:
            import httpx
            client = httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False)
        with client:
            r = client.get(base + "/api/version")
            if r.status_code == 200:
                out["running"] = True
                out["version"] = (r.json() or {}).get("version")
                t = client.get(base + "/api/tags")
                if t.status_code == 200:
                    out["models"] = sorted(m.get("name") or m.get("model") or ""
                                           for m in (t.json() or {}).get("models") or [])
    except Exception:  # noqa: BLE001 - not running / not reachable
        pass
    path = installed_path(system)
    out["installed"] = bool(path) or out["running"]
    out["path"] = str(path) if path else None
    return out


def installed_path(system: Optional[str] = None, which: Optional[Callable[[str], Optional[str]]] = None) -> Optional[Path]:
    system = system or platform.system()
    which = which or shutil.which
    cands: list[Path] = []
    if system == "Darwin":
        cands = [Path("/Applications/Ollama.app"), Path.home() / "Applications" / "Ollama.app"]
    elif system == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            cands = [Path(local) / "Programs" / "Ollama" / "ollama app.exe",
                     Path(local) / "Programs" / "Ollama" / "ollama.exe"]
    for c in cands:
        if c.exists():
            return c
    w = which("ollama")
    return Path(w) if w else None


def has_model(models: Iterable[str], model: str) -> bool:
    want = model if ":" in model else model + ":latest"
    return any(m == want or m == model for m in models)


# ---------------------------------------------------------------- install plan
def install_plan(system: Optional[str] = None, which: Optional[Callable[[str], Optional[str]]] = None,
                 installed: Optional[Path] = None) -> dict:
    """How Ollama would be installed (or started) here. ``auto`` = we can do it after one click."""
    system = system or platform.system()
    which = which or shutil.which
    if installed:
        return {"method": "start", "auto": True, "path": str(installed)}
    if system == "Darwin":
        if which("brew"):
            return {"method": "brew", "auto": True, "command": "brew install ollama"}
        return {"method": "zip", "auto": True, "url": OFFICIAL_URLS["Darwin"]}
    if system == "Windows":
        return {"method": "exe", "auto": True, "url": OFFICIAL_URLS["Windows"]}
    if system == "Linux":
        return {"method": "script", "auto": False, "command": LINUX_COMMAND}
    return {"method": None, "auto": False}


# ---------------------------------------------------------------- signatures
Runner = Callable[[list[str]], "subprocess.CompletedProcess"]


def _run(cmd: list[str], timeout: float = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def verify_signature(path: Path, system: Optional[str] = None, runner: Runner = _run) -> str:
    """Check the OS code signature of a downloaded installer/app. Returns the signer, raises on failure."""
    system = system or platform.system()
    if system == "Darwin":
        r = runner(["codesign", "--verify", "--deep", "--strict", str(path)])
        if r.returncode != 0:
            raise SetupError("The download's code signature is not valid; nothing was installed.")
        info = runner(["codesign", "-dv", "--verbose=2", str(path)])
        text = (info.stderr or "") + (info.stdout or "")
        auth = [ln.split("=", 1)[1] for ln in text.splitlines() if ln.startswith("Authority=")]
        if not auth or not auth[0].startswith("Developer ID Application:") or SIGNER_HINT not in auth[0].lower():
            raise SetupError("The download is not signed by Ollama; nothing was installed.")
        return auth[0]
    if system == "Windows":
        ps = ("$s = Get-AuthenticodeSignature -LiteralPath $args[0]; "
              "Write-Output $s.Status; Write-Output $s.SignerCertificate.Subject")
        r = runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps, str(path)])
        lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        if r.returncode != 0 or len(lines) < 2 or lines[0] != "Valid" or SIGNER_HINT not in lines[1].lower():
            raise SetupError("The installer's signature is not valid or not from Ollama; it was not run.")
        return lines[1]
    raise SetupError("Automatic install is not supported on this system.")


# ---------------------------------------------------------------- install job
def wait_until_running(url: str, job, timeout: float, detector: Callable[..., dict] = detect,
                       sleep: Callable[[float], None] = time.sleep) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        d = detector(url)
        if d.get("running"):
            return d
        if time.monotonic() > deadline:
            raise SetupError("Ollama didn't start yet. Open it once, then check again.")
        sleep(1.0)


def run_install(job, plan: dict, *, url: str = DEFAULT_URL, downloader: Optional[Downloader] = None,
                runner: Runner = _run, launcher: Optional[Callable[[list[str]], Any]] = None,
                detector: Callable[..., dict] = detect, system: Optional[str] = None,
                workdir: Optional[Path] = None, wait_timeout: float = 120.0,
                sleep: Callable[[float], None] = time.sleep) -> None:
    """Install or start Ollama according to ``plan`` (from :func:`install_plan`). Updates ``job``."""
    system = system or platform.system()
    downloader = downloader or Downloader()
    launcher = launcher or (lambda cmd: subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                                         stderr=subprocess.DEVNULL, start_new_session=True))
    method = plan.get("method")
    if method in (None, "script"):
        raise SetupError("Automatic install isn't available here. Run the command shown instead.")

    if method == "start":
        job.update(step="launch", message="Starting Ollama")
        path = plan["path"]
        if system == "Darwin" and path.endswith(".app"):
            launcher(["open", "-a", path])
        elif path.lower().endswith("ollama app.exe"):
            launcher([path])                    # the Windows tray app starts the server
        else:
            launcher([path, "serve"])
    elif method == "brew":
        job.update(step="install", message="Installing with Homebrew", progress=None)
        r = runner(["brew", "install", "ollama"])
        if r.returncode != 0:
            raise SetupError("Homebrew couldn't install Ollama. Try the download from ollama.com.")
        job.update(step="launch", message="Starting Ollama")
        r = runner(["brew", "services", "start", "ollama"])
        if r.returncode != 0:
            exe = shutil.which("ollama") or "ollama"
            launcher([exe, "serve"])
    elif method in ("zip", "exe"):
        tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="ledgerstitch-ollama-"))
        name = "Ollama-darwin.zip" if method == "zip" else "OllamaSetup.exe"
        dest = tmp / name
        job.update(step="download", message="Downloading Ollama", progress=0.0)

        def prog(done: int, total: int) -> None:
            job.update(completed=done, total=total, progress=(done / total) if total else None)

        downloader.download(plan["url"], dest, prog)
        job.update(step="verify", message="Checking signature", progress=None)
        if method == "zip":
            out = tmp / "unpacked"
            out.mkdir(exist_ok=True)
            r = runner(["ditto", "-x", "-k", str(dest), str(out)])   # keeps symlinks + permissions
            if r.returncode != 0:
                raise SetupError("Couldn't unpack the Ollama download.")
            app = out / "Ollama.app"
            if not app.is_dir():
                raise SetupError("The download didn't contain Ollama.app.")
            signer = verify_signature(app, system, runner)
            target_dir = Path("/Applications") if os.access("/Applications", os.W_OK) \
                else Path.home() / "Applications"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "Ollama.app"
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(app), str(target))
            job.update(step="launch", message="Starting Ollama", result={"signer": signer, "path": str(target)})
            launcher(["open", "-a", str(target)])
        else:
            signer = verify_signature(dest, system, runner)
            job.update(step="launch", message="Finish the Ollama installer", state="waiting",
                       result={"signer": signer})
            launcher([str(dest)])
            wait_timeout = max(wait_timeout, 900.0)   # the user clicks through the installer
    else:
        raise SetupError(f"Unknown install method {method!r}.")

    job.update(step="wait", message="Waiting for Ollama", progress=None)
    d = wait_until_running(url, job, wait_timeout, detector, sleep)
    job.update(state="done", step="done", message="Ollama is ready", progress=1.0,
               result={**job.result, "version": d.get("version"), "models": d.get("models", [])})


# ---------------------------------------------------------------- model pull
def parse_pull_stream(lines: Iterable[Any]) -> Iterator[dict]:
    """Turn Ollama's ``/api/pull`` NDJSON stream into progress events.

    Each event: ``{"status", "completed", "total", "progress" (0..1 or None), "done", "error"}``.
    Byte counts are summed over all layers (digests) seen so far; ``progress`` never moves
    backwards when a new layer shows up (a progress bar that shrinks looks broken).
    """
    totals: dict[str, int] = {}
    dones: dict[str, int] = {}
    best = 0.0
    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("error"):
            yield {"status": "error", "completed": sum(dones.values()), "total": sum(totals.values()),
                   "progress": None, "done": True, "error": str(msg["error"])[:300]}
            return
        status = str(msg.get("status") or "")
        digest = msg.get("digest")
        if digest and isinstance(msg.get("total"), (int, float)):
            totals[digest] = int(msg["total"])
            dones[digest] = int(msg.get("completed") or 0)
        total, done = sum(totals.values()), sum(dones.values())
        success = status == "success"
        if success:
            frac: Optional[float] = 1.0
        elif total:
            best = max(best, min(done / total, 1.0))
            frac = best
        else:
            frac = best or None
        yield {"status": status, "completed": done, "total": total, "progress": frac,
               "done": success, "error": None}


def run_pull(job, model: str, *, url: str = DEFAULT_URL, client_factory: Optional[Callable[[], Any]] = None) -> None:
    """Pull ``model`` through Ollama's ``/api/pull`` stream, updating ``job`` (bytes, fraction).
    Resumable: Ollama keeps finished layers and partial blobs, so a new pull continues."""
    if not allowed_model(model):
        raise SetupError("Local AI uses one model only.")
    base = connect_url(url or DEFAULT_URL)
    if client_factory:
        client = client_factory()
    else:
        import httpx
        client = httpx.Client(timeout=httpx.Timeout(30.0, read=600.0), trust_env=False, follow_redirects=False)
    job.update(step="pull", message=f"Downloading {model_info(model)['label']}", progress=0.0)
    with client:
        with client.stream("POST", base + "/api/pull", json={"model": model, "stream": True}) as r:
            if r.status_code != 200:
                raise SetupError(f"Ollama refused the download (HTTP {r.status_code}).")
            for ev in parse_pull_stream(r.iter_lines()):
                if getattr(job, "cancel_requested", False):
                    raise SetupError("Stopped. Nothing more is downloaded.")
                if ev["error"]:
                    raise SetupError(f"Ollama: {ev['error']}")
                job.update(completed=ev["completed"], total=ev["total"], progress=ev["progress"],
                           message=_pull_caption(ev["status"], model))
                if ev["done"]:
                    job.update(state="done", step="done", progress=1.0, message="Model ready",
                               result={**job.result, "model": model})
                    return
    raise SetupError("The model download stopped early. Try again.")


def run_setup(job, model: str, *, url: str = DEFAULT_URL, detector: Callable[..., dict] = detect,
              system: Optional[str] = None, install_kw: Optional[dict] = None,
              pull_client_factory: Optional[Callable[[], Any]] = None, allow_install: bool = True,
              wait_timeout: float = 0.0, sleep: Callable[[float], None] = time.sleep) -> None:
    """The consented "Set up local AI" job: start/install Ollama if needed, then pull ``model``.

    Idempotent and resumable: an already-running Ollama is left alone, a model that is already
    present is not pulled again, and Ollama itself resumes a partial pull from where it stopped.
    ``allow_install=False`` (server mode): never install anything; wait up to ``wait_timeout``
    seconds for the Ollama service to come up, then stop with the one-line fix.
    """
    if not allowed_model(model):
        raise SetupError("Local AI uses one model only.")
    d = detector(url)
    if not d.get("running") and not allow_install:
        job.update(step="wait", message="Waiting for Ollama", progress=None)
        try:
            d = wait_until_running(url, job, wait_timeout, detector, sleep)
        except SetupError:
            raise SetupError(f"The Ollama service isn't reachable. Run: {DOCKER_FIX}") from None
    if not d.get("running"):
        installed = Path(d["path"]) if d.get("path") else None
        plan = install_plan(system, installed=installed)
        if not plan.get("auto"):
            raise SetupError("Ollama isn't installed. Run the command shown in Setup, then try again.")
        if plan.get("url"):
            check_download_url(plan["url"])
        run_install(job, plan, url=url, detector=detector, system=system, **(install_kw or {}))
        job.update(state="running", step="pull", progress=None)
        d = detector(url)
    if has_model(d.get("models") or [], model):
        job.update(state="done", step="done", progress=1.0, message="Model ready", result={"model": model})
        return
    run_pull(job, model, url=url, client_factory=pull_client_factory)


def delete_model(model: str, *, url: str = DEFAULT_URL, client_factory: Optional[Callable[[], Any]] = None) -> bool:
    """Remove ``model`` from Ollama (``DELETE /api/delete``). True if removed, False if it wasn't there."""
    if not allowed_model(model):
        raise SetupError("Local AI uses one model only.")
    base = connect_url(url or DEFAULT_URL)
    if client_factory:
        client = client_factory()
    else:
        import httpx
        client = httpx.Client(timeout=30.0, trust_env=False, follow_redirects=False)
    with client:
        r = client.request("DELETE", base + "/api/delete", json={"model": model})
    if r.status_code == 404:
        return False
    if r.status_code != 200:
        raise SetupError(f"Ollama couldn't remove the model (HTTP {r.status_code}).")
    return True


# ---------------------------------------------------------------- resource check
def total_memory(system: Optional[str] = None, meminfo: Path = Path("/proc/meminfo")) -> Optional[int]:
    """Total RAM in bytes: /proc/meminfo (Linux, also inside a container, where it is the Docker
    VM's memory on macOS/Windows), else the OS. None if unknown."""
    try:
        for line in meminfo.read_text("ascii", "replace").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    system = system or platform.system()
    if system == "Windows":
        try:
            import ctypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMSTAT()
            m.dwLength = ctypes.sizeof(MEMSTAT)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):   # type: ignore[attr-defined]
                return int(m.ullTotalPhys)
        except Exception:  # noqa: BLE001
            return None
        return None
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return None


def models_dir() -> Path:
    """Where a local Ollama keeps its models (OLLAMA_MODELS, else ~/.ollama/models)."""
    env = os.environ.get("OLLAMA_MODELS")
    return Path(env).expanduser() if env else Path.home() / ".ollama" / "models"


def _free_bytes(path: Path) -> Optional[int]:
    p = Path(path).expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def resources(data_dir: Path, *, remote: bool, disk_free: Optional[int] = None,
              mem_total: Optional[int] = None) -> dict:
    """Free disk + total memory against what the model needs.

    ``remote``: Ollama runs elsewhere (the compose service). Ollama's API doesn't report free
    disk, so the LedgerStitch data volume is used as an approximation (``disk_approx``): on one
    Docker host, named volumes live on the same disk. Unknown values never block (no warning).
    """
    if disk_free is None:
        disk_free = _free_bytes(Path(data_dir) if remote else models_dir())
    if mem_total is None:
        mem_total = total_memory()
    disk_ok = disk_free is None or disk_free >= MIN_DISK_BYTES
    mem_ok = mem_total is None or mem_total >= MIN_RAM_BYTES
    return {"disk_free_bytes": disk_free, "disk_free_gb": round(disk_free / GB, 1) if disk_free is not None else None,
            "disk_approx": bool(remote), "need_disk_gb": round(MIN_DISK_BYTES / GB),
            "mem_total_bytes": mem_total, "mem_total_gb": round(mem_total / GB, 1) if mem_total is not None else None,
            "need_mem_gb": round(MIN_RAM_BYTES / GB),
            "disk_ok": disk_ok, "mem_ok": mem_ok, "ok": disk_ok and mem_ok}


def _pull_caption(status: str, model: str) -> str:
    if status.startswith("pulling") and status != "pulling manifest":
        return f"Downloading {model_info(model)['label']}"
    if status.startswith("verifying"):
        return "Verifying"
    if status in ("writing manifest", "removing any unused layers", "success"):
        return "Finishing"
    return "Preparing"


def current_system() -> str:
    return platform.system() or sys.platform
