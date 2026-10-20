# SPDX-License-Identifier: AGPL-3.0-only
"""The automatic system check shown in first-run setup. Local only: nothing leaves the computer."""
from __future__ import annotations

import platform
import shutil
from pathlib import Path
from typing import Optional

MIN_FREE_BYTES = 1 * 1024 ** 3          # vaults are small; 1 GB keeps imports/backups comfortable
MODEL_FREE_BYTES = 25 * 1024 ** 3       # room for the local model (GLM-4.7-Flash, 19 GB) + pull headroom
MIN_MACOS = (12, 0)
MIN_WINDOWS = 10


def _ver_tuple(v: str) -> tuple[int, ...]:
    out = []
    for p in v.split("."):
        if not p.isdigit():
            break
        out.append(int(p))
    return tuple(out)


def os_info(system: Optional[str] = None) -> dict:
    system = system or platform.system()
    arch = platform.machine() or ""
    if system == "Darwin":
        ver = platform.mac_ver()[0] or platform.release()
        ok = _ver_tuple(ver) >= MIN_MACOS if _ver_tuple(ver) else True
        return {"name": "macOS", "version": ver, "arch": arch, "ok": ok}
    if system == "Windows":
        rel = platform.release()
        ok = not rel.isdigit() or int(rel) >= MIN_WINDOWS
        return {"name": "Windows", "version": f"{rel} ({platform.version()})", "arch": arch, "ok": ok}
    name = system or "Unknown"
    ver = platform.release()
    try:
        for line in Path("/etc/os-release").read_text("utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                name, ver = line.split("=", 1)[1].strip().strip('"'), ""
                break
    except OSError:
        pass
    return {"name": name, "version": ver, "arch": arch, "ok": True}


def disk_info(data_dir: Path) -> dict:
    p = Path(data_dir).expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        u = shutil.disk_usage(p)
    except OSError:
        return {"free_bytes": None, "free_gb": None, "ok": True, "model_ok": True}
    return {"free_bytes": u.free, "free_gb": round(u.free / 1024 ** 3, 1),
            "ok": u.free >= MIN_FREE_BYTES, "model_ok": u.free >= MODEL_FREE_BYTES}


def addon_checks() -> list[dict]:
    """Rows the installed add-ons add to the setup check (``registry.provide("setup.check", fn)``,
    ``fn() -> {"id", "label", "value", "ok", "icon"?}``). A failing add-on is skipped."""
    from .. import plugins
    rows = []
    for ns, fn in plugins.registry().provided("setup.check"):
        try:
            row = fn()
        except Exception:  # noqa: BLE001 - an add-on must never break the setup screen
            continue
        if isinstance(row, dict) and row.get("label"):
            rows.append({"ns": ns, "id": str(row.get("id") or ns)[:40], "label": str(row["label"])[:60],
                         "value": str(row.get("value") or "")[:80], "ok": bool(row.get("ok")),
                         "icon": str(row.get("icon") or "check")[:32]})
    return rows


def system_check(data_dir: Path, *, port: Optional[int], host: str, server_mode: bool,
                 ollama: dict) -> dict:
    return {
        "os": os_info(),
        "disk": disk_info(data_dir),
        "port": {"port": port, "host": host, "ok": True},
        "addons": addon_checks(),
        "ollama": ollama,
        "server_mode": server_mode,
    }
