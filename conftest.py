# SPDX-License-Identifier: AGPL-3.0-only
"""Repo-level pytest config. In a checkout that also holds add-on packages (a folder with a
``ledgerstitch_<name>`` package and a ``tests/`` folder), each add-on's tests only run when
that add-on is importable and the edition isn't forced to community.

    .venv/bin/python -m pytest -q                                  # core (+ installed add-ons)
    LEDGERSTITCH_EDITION=community .venv/bin/python -m pytest -q   # core only, add-ons not loaded
"""
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMUNITY = os.environ.get("LEDGERSTITCH_EDITION", "").strip().lower() == "community"

collect_ignore_glob = []
for folder in sorted(p for p in ROOT.iterdir() if p.is_dir() and (p / "tests").is_dir() and p.name != "tests"):
    pkgs = [d.name for d in folder.iterdir() if d.name.startswith("ledgerstitch_") and (d / "__init__.py").is_file()]
    if pkgs and (COMMUNITY or any(importlib.util.find_spec(n) is None for n in pkgs)):
        collect_ignore_glob.append(f"{folder.name}/tests/*")

# Add-on tests kept in tests/ (test_<addon>_*.py) need their add-on too.
for path in sorted((ROOT / "tests").glob("test_*_*.py")):
    addon = path.name.split("_")[1]
    if (ROOT / addon).is_dir() and (COMMUNITY or importlib.util.find_spec(f"ledgerstitch_{addon}") is None):
        collect_ignore_glob.append(f"tests/{path.name}")
