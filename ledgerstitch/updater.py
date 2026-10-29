# SPDX-License-Identifier: AGPL-3.0-only
"""Update check: is a newer LedgerStitch out, and what does updating this install take?

    ledgerstitch update [--offline] [--json]          # report only; never changes anything
    sh scripts/update.sh [--check]                    # applies it (native, dev checkout or Docker)

What this module decides:

* **the install kind**: the desktop app (frozen), the one-line installer (``install-manifest.txt``
  next to the venv), an editable dev checkout, inside a Docker container, or a plain pip install;
* **the latest release**: one HTTPS GET to the public GitHub releases API, made only when the user
  runs the command. Nothing identifying is sent (no version, no install id), there is no
  background or scheduled check, and being offline is a message, not an error;
* **whether the dependencies changed** between the installed package (``Requires-Dist``) and a
  source checkout's ``pyproject.toml``. When they didn't, ``scripts/update.sh`` reinstalls only
  LedgerStitch itself (``uv pip install --no-deps``) instead of re-resolving every dependency.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import __version__

__all__ = ["RELEASES_API", "RELEASES_PAGE", "UpdateCheckError", "Release", "UpdateReport", "parse_version",
           "is_newer", "normalize_requirement", "requirement_set", "installed_requirements",
           "source_requirements", "source_version", "compare_source", "install_kind", "fetch_latest",
           "check"]

RELEASES_API = "https://api.github.com/repos/securectl/ledgerstitch/releases/latest"
RELEASES_PAGE = "https://github.com/securectl/ledgerstitch/releases/latest"
TIMEOUT = 8.0
KINDS = ("desktop", "installer", "dev", "docker", "pip")


class UpdateCheckError(Exception):
    """The latest release couldn't be looked up (offline, rate-limited, unexpected reply)."""


# ----------------------------------------------------------------------------- versions
_VER = re.compile(r"^v?(\d+(?:\.\d+)*)(?:[-.]?(a|b|rc|alpha|beta|dev)\.?(\d*))?", re.I)
_PRE = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "rc": 3}


def parse_version(v: str) -> tuple:
    """A sortable key for ``1.2.0``, ``v1.10.0``, ``1.3.0rc1``. A final release sorts after its
    pre-releases. Raises ValueError for anything that isn't a version."""
    m = _VER.match((v or "").strip())
    if not m:
        raise ValueError(f"not a version: {v!r}")
    nums = [int(x) for x in m.group(1).split(".")]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    pre = (_PRE[m.group(2).lower()], int(m.group(3) or 0)) if m.group(2) else (9, 0)
    return (tuple(nums), pre)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


# ----------------------------------------------------------------------------- dependencies
def normalize_requirement(req: str) -> str:
    """``Fast_API >= 0.110`` and ``fastapi>=0.110`` compare equal. Environment markers are kept."""
    req = req.strip()
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(.*)$", req)
    if not m:
        return req
    name = re.sub(r"[-_.]+", "-", m.group(1)).lower()
    rest = m.group(2)
    marker = ""
    if ";" in rest:
        rest, marker = rest.split(";", 1)
        marker = ";" + " ".join(marker.split())
    return name + rest.replace(" ", "") + marker


def requirement_set(reqs) -> frozenset[str]:
    return frozenset(normalize_requirement(r) for r in reqs if r and r.strip())


def installed_requirements(dist: str = "ledgerstitch") -> Optional[frozenset[str]]:
    """The installed package's runtime dependencies (``Requires-Dist`` without extras), or None
    when the package metadata isn't there (a frozen app or a bare source tree)."""
    from importlib import metadata
    try:
        reqs = metadata.requires(dist)
    except metadata.PackageNotFoundError:
        return None
    return requirement_set(r for r in (reqs or []) if not re.search(r"\bextra\s*==", r))


def _pyproject(src: Path) -> dict:
    import tomllib
    p = Path(src) / "pyproject.toml"
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{src} is not a LedgerStitch checkout (no pyproject.toml)") from None
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{p}: {e}") from None


def source_requirements(src: Path) -> frozenset[str]:
    proj = _pyproject(src).get("project", {})
    if proj.get("name") != "ledgerstitch":
        raise ValueError(f"{src} is not a LedgerStitch checkout")
    return requirement_set(proj.get("dependencies", []))


def source_version(src: Path) -> str:
    return str(_pyproject(src).get("project", {}).get("version", ""))


def compare_source(src: Path, installed: Optional[frozenset[str]] = None) -> dict:
    """What updating from the checkout ``src`` takes: ``no-deps`` (reinstall LedgerStitch only)
    or ``full`` (dependencies changed, or the installed ones are unknown)."""
    src = Path(src)
    target = source_requirements(src)
    have = installed if installed is not None else installed_requirements()
    added = sorted(target - have) if have is not None else sorted(target)
    removed = sorted(have - target) if have is not None else []
    deps_changed = have is None or bool(added or removed)
    return {"source": str(src), "installed_version": __version__, "target_version": source_version(src),
            "deps_changed": deps_changed, "added": added, "removed": removed,
            "action": "full" if deps_changed else "no-deps"}


# ----------------------------------------------------------------------------- install kind
def _manifest_for(prefix: Path) -> Optional[Path]:
    m = prefix / "install-manifest.txt"
    return m if m.is_file() else None


def read_manifest(path: Path) -> dict:
    out: dict = {"path": []}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "path":
            out["path"].append(v)
        else:
            out[k] = v
    return out


def install_kind(executable: Optional[str] = None, frozen: Optional[bool] = None,
                 env: Optional[dict] = None, dockerenv: Path = Path("/.dockerenv")) -> tuple[str, dict]:
    """``(kind, details)``. kind is one of KINDS; details carries the manifest or checkout path."""
    env = os.environ if env is None else env
    if frozen if frozen is not None else getattr(sys, "frozen", False):
        return "desktop", {}
    if dockerenv.exists() or env.get("LEDGERSTITCH_IN_DOCKER") == "1":
        return "docker", {}
    exe = Path(executable or sys.executable)
    # install.sh: <prefix>/venv/bin/python, manifest in <prefix>; install.ps1: <prefix>\venv\Scripts\python.exe
    for venv in (exe.parent.parent, exe.parent.parent.parent):
        m = _manifest_for(venv.parent) if venv.name == "venv" else None
        if m:
            info = read_manifest(m)
            info["manifest"] = str(m)
            return "installer", info
    checkout = _editable_checkout()
    if checkout:
        return "dev", {"checkout": str(checkout)}
    return "pip", {}


def _editable_checkout() -> Optional[Path]:
    from importlib import metadata
    try:
        raw = metadata.distribution("ledgerstitch").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        raw = None
    if raw:
        try:
            d = json.loads(raw)
        except ValueError:
            d = {}
        if d.get("dir_info", {}).get("editable") and str(d.get("url", "")).startswith("file://"):
            from urllib.parse import unquote, urlparse
            return Path(unquote(urlparse(d["url"]).path))
    here = Path(__file__).resolve().parent.parent
    if (here / "pyproject.toml").is_file() and (here / ".git").exists():
        return here                                   # running straight from a checkout
    return None


# ----------------------------------------------------------------------------- latest release
@dataclass
class Release:
    version: str
    tag: str
    url: str
    published: str = ""


Opener = Callable[[urllib.request.Request, float], bytes]


def _default_open(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - https only (checked)
        return r.read(1_000_000)


def fetch_latest(url: str = RELEASES_API, opener: Optional[Opener] = None, timeout: float = TIMEOUT) -> Release:
    """The latest published release. Sends only ``Accept`` and a generic ``User-Agent``."""
    if not url.startswith("https://"):
        raise UpdateCheckError(f"refusing a non-HTTPS update URL: {url}")
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "ledgerstitch-update-check"})
    try:
        body = (opener or _default_open)(req, timeout)
    except urllib.error.HTTPError as e:
        hint = " (GitHub rate limit; try again later)" if e.code in (403, 429) else ""
        raise UpdateCheckError(f"the release server answered HTTP {e.code}{hint}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        raise UpdateCheckError(f"couldn't reach {url.split('/')[2]} ({reason}); are you offline? "
                               f"Nothing was changed.") from None
    try:
        d = json.loads(body)
        tag = str(d["tag_name"])
        parse_version(tag)
    except (ValueError, KeyError, TypeError):
        raise UpdateCheckError("unexpected reply from the release server") from None
    return Release(version=tag.lstrip("v"), tag=tag, url=str(d.get("html_url") or RELEASES_PAGE),
                   published=str(d.get("published_at") or ""))


# ----------------------------------------------------------------------------- report
@dataclass
class UpdateReport:
    installed: str
    kind: str
    details: dict = field(default_factory=dict)
    latest: Optional[Release] = None
    update_available: Optional[bool] = None
    error: Optional[str] = None
    how: list[str] = field(default_factory=list)


def how_to_update(kind: str, details: dict) -> list[str]:
    if kind == "desktop":
        return [f"Download the new installer from {RELEASES_PAGE} and install it over this one "
                "(your data folder is kept)."]
    if kind == "docker":
        return ["On the Docker host, from the LedgerStitch checkout: sh scripts/update.sh --check, then "
                "sh scripts/update.sh (rebuilds only what changed and backs up the data volume first)."]
    if kind == "installer":
        src = details.get("source", "")
        if src and Path(src).is_dir():
            return [f"sh {Path(src) / 'scripts' / 'update.sh'}   (reinstalls only LedgerStitch when the "
                    "dependencies are unchanged)"]
        return [f"Re-run the installer with the same source: sh install.sh '{src or 'ledgerstitch'}' "
                "(a PyPI or git source re-resolves the dependencies)."]
    if kind == "dev":
        return [f"cd {details.get('checkout', '.')} && sh scripts/update.sh --dev   (git pull; reinstalls "
                "dependencies only if pyproject.toml changed them)"]
    return ["pip install --upgrade ledgerstitch"]


def check(offline: bool = False, opener: Optional[Opener] = None, url: Optional[str] = None,
          kind_info: Optional[tuple[str, dict]] = None) -> UpdateReport:
    kind, details = kind_info or install_kind()
    rep = UpdateReport(installed=__version__, kind=kind, details=details, how=how_to_update(kind, details))
    if offline:
        return rep
    try:
        rep.latest = fetch_latest(url or os.environ.get("LEDGERSTITCH_UPDATE_URL") or RELEASES_API, opener)
        rep.update_available = is_newer(rep.latest.version, __version__)
    except UpdateCheckError as e:
        rep.error = str(e)
    return rep
