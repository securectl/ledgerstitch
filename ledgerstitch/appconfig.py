# SPDX-License-Identifier: AGPL-3.0-only
"""App-level, NON-secret configuration: ``<data-dir>/app.json``.

This file lives outside every vault, so it is readable without a passphrase. Only
put things here that are fine to read on a locked computer: whether first-run setup
was completed, which local AI model the user picked in setup. Never passphrases,
API keys, business names or any financial data (those belong in a vault).

Only keys in ``ALLOWED_KEYS`` are written; anything else is dropped.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

CONFIG_NAME = "app.json"
ALLOWED_KEYS = {"onboarding"}
# local_ai_model: the model that finished downloading (setup done).
# local_ai_requested: the model the user asked for in setup (consent given); cleared when done or
#   when the user turns it off. While set and not done, the download is re-queued at launch.
# local_ai_error: why the last attempt stopped (shown in Settings; cleared on retry).
# local_ai_consent: the explicit "Agree and set up" from the consent dialog:
#   {"at": ISO time, "model": tag, "endpoint": Ollama URL, "download_anyway": bool}. No download
#   runs without it; "Withdraw consent" in Settings removes it. (local_ai_consent_at: older builds.)
ONBOARDING_KEYS = {"completed", "completed_at", "version", "local_ai_model", "skipped",
                   "local_ai_requested", "local_ai_consent_at", "local_ai_consent", "local_ai_error"}
ONBOARDING_VERSION = 1

_lock = threading.Lock()


def config_path(data_dir: Path | str) -> Path:
    return Path(data_dir).expanduser() / CONFIG_NAME


def load(data_dir: Path | str) -> dict:
    try:
        raw = json.loads(config_path(data_dir).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if k in ALLOWED_KEYS} if isinstance(raw, dict) else {}


def save(data_dir: Path | str, cfg: dict) -> None:
    clean = {k: v for k, v in cfg.items() if k in ALLOWED_KEYS}
    p = config_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        fd, tmp = tempfile.mkstemp(prefix=".app-", suffix=".json", dir=p.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(clean, f, indent=1, sort_keys=True)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def onboarding(data_dir: Path | str) -> dict:
    ob = load(data_dir).get("onboarding")
    return dict(ob) if isinstance(ob, dict) else {}


def update_onboarding(data_dir: Path | str, **fields: Any) -> dict:
    cfg = load(data_dir)
    ob = cfg.get("onboarding") if isinstance(cfg.get("onboarding"), dict) else {}
    ob = {k: v for k, v in {**ob, **fields}.items() if k in ONBOARDING_KEYS and v is not None}
    if fields.get("completed"):
        ob["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ob["version"] = ONBOARDING_VERSION
    elif "completed" in fields:
        ob.pop("completed_at", None)
    cfg["onboarding"] = ob
    save(data_dir, cfg)
    return ob
