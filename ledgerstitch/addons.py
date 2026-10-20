# SPDX-License-Identifier: AGPL-3.0-only
"""Browser side of the plugin API: add-on UI registrations and their static files.

* :func:`ui_status` is the ``ui`` block of ``GET /api/status``: the screens, cards and
  settings sections of the INSTALLED add-ons, each with its feature state (``on`` or
  ``locked``). An entry whose feature is not available is left out.
* :func:`index_payload` is ``GET /addons/index.json``: what demo mode needs to load the
  installed add-ons' ``mock.js`` without a backend (registrations, features, plans).
* :func:`resolve_static` maps ``/addons/<namespace>/<path>`` to a file inside that add-on's
  registered ``static_dir``, refusing anything outside it (path traversal, dotfiles,
  symlinks out of the folder) and unknown namespaces.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from . import editions

PREFIX = "/addons"
MEDIA_TYPES = {
    ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".woff2": "font/woff2", ".woff": "font/woff",
    ".txt": "text/plain; charset=utf-8",
}
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def module_url(ns: str, module: str) -> str:
    return f"{PREFIX}/{ns}/{module}"


def _state(feature: Optional[str], status: dict) -> Optional[str]:
    if feature is None:
        return "on"
    info = status.get(feature)
    if not info or not info.get("available"):
        return None
    return "on" if info.get("unlocked") else "locked"


def _label(feature: Optional[str], status: dict) -> Optional[str]:
    return (status.get(feature) or {}).get("label") if feature else None


def ui_status(registry, license: Any = None) -> dict:
    """``{screens, cards, settings}`` of the installed add-ons, sorted by ``order``."""
    status = editions.features_status(license, registry)
    out: dict[str, list] = {"screens": [], "cards": [], "settings": []}
    for key, entries in (("screens", registry.ui_screens), ("cards", registry.ui_cards),
                         ("settings", registry.ui_settings)):
        for e in entries:
            if e["ns"] not in registry.static_dirs:
                continue
            state = _state(e.get("feature"), status)
            if state is None:
                continue
            item = {**e, "module": module_url(e["ns"], e["module"]), "state": state}
            if key == "cards" and not item.get("title"):
                item["title"] = _label(e.get("feature"), status) or e["id"].replace("-", " ").capitalize()
            out[key].append(item)
        out[key].sort(key=lambda x: (x["order"], x["ns"], x["id"]))
    return out


def index_payload(registry) -> dict:
    """Installed add-ons for demo mode: namespaces, mock modules, features, plans, UI."""
    addons = []
    for ns, root in sorted(registry.static_dirs.items()):
        addons.append({"ns": ns, "mock": module_url(ns, "mock.js") if (root / "mock.js").is_file() else None})
    feats = {n: {"tier": f.tier, "label": f.label} for n, f in registry.features.items()}
    plans = {p: sorted(t) for p, t in editions.plan_tiers(registry).items()}
    ui = {"screens": [], "cards": [], "settings": []}
    for key, entries in (("screens", registry.ui_screens), ("cards", registry.ui_cards),
                         ("settings", registry.ui_settings)):
        ui[key] = sorted(({**e, "module": module_url(e["ns"], e["module"])} for e in entries
                          if e["ns"] in registry.static_dirs),
                         key=lambda x: (x["order"], x["ns"], x["id"]))
    return {"addons": addons, "features": feats, "plans": plans,
            "plan_labels": editions.plan_labels(registry), "all_tiers_plan": editions.ALL_TIERS_PLAN, "ui": ui}


def resolve_static(registry, ns: str, path: str) -> Optional[tuple[Path, str]]:
    """``(file, media type)`` for ``/addons/<ns>/<path>``, or None (-> 404)."""
    root = registry.static_dirs.get(ns)
    if root is None or not path or "\\" in path or "\0" in path:
        return None
    parts = PurePosixPath(path).parts
    if not parts or any(not _SEGMENT_RE.match(p) or p in (".", "..") for p in parts):
        return None                     # no traversal, no hidden files, no odd names
    f = root.joinpath(*parts)
    try:
        real = f.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not real.is_relative_to(root) or not real.is_file():
        return None
    media = MEDIA_TYPES.get(real.suffix.lower())
    if media is None:
        return None                     # only the browser file types above are served
    return real, media


__all__ = ["MEDIA_TYPES", "PREFIX", "index_payload", "module_url", "resolve_static", "ui_status"]
