# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-only
# PyInstaller spec for the LedgerStitch desktop app (onedir, windowed).
#
#   EDITION=community .venv/bin/python -m PyInstaller packaging/ledgerstitch.spec \
#       --distpath dist --workpath build --noconfirm --clean
#
# EDITION=community (default): the AGPL core only. Add-on packages and the cloud AI SDKs are
#   excluded, so the bundle cannot make cloud AI calls.
# EDITION=pro: the core plus the ledgerstitch_pro wheel (must be installed in the build venv,
#   with its dist-info so the "ledgerstitch.plugins" entry point resolves in the frozen app).
#
# Normally run via scripts/build_macos.sh or scripts/build_windows.ps1.
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs,
                                     collect_submodules, copy_metadata)

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 (SPECPATH is injected by PyInstaller)
PKG = ROOT / "ledgerstitch"
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (PKG / "__init__.py").read_text()).group(1)
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
# Must match the wheels in the build venv (scripts/build_macos.sh checks every Mach-O).
MACOS_MIN = os.environ.get("MACOS_MIN", "12.0")
EDITION = os.environ.get("EDITION", "community").strip().lower()
if EDITION not in ("community", "pro"):
    raise SystemExit(f"EDITION must be community or pro, not {EDITION!r}")
IS_PRO = EDITION == "pro"

# ----------------------------------------------------------------------------- data files
datas = [(str(PKG / "web" / "static"), "ledgerstitch/web/static")]   # UI + disclaimer.txt + logo
datas += [(str(PKG / "ingest" / "data"), "ledgerstitch/ingest/data")]   # known_formats.json catalog
datas += collect_data_files("pdfminer")                               # cmaps / fonts for pdf parsing
datas += collect_data_files("certifi")
if IS_PRO:
    datas += collect_data_files("anthropic")
    datas += collect_data_files("openai")
    # Add-on browser files (static/*.js, *.css) ship as package data of the compiled wheel.
    datas += collect_data_files("ledgerstitch_pro", includes=["static/**/*"])
datas += collect_data_files("pypdfium2_raw")
datas += collect_data_files("pypdfium2")
# Packages that read their own dist metadata at runtime
for dist in (("ledgerstitch-pro", "anthropic", "openai") if IS_PRO else ()) + ("httpx", "pdfplumber", "pdfminer.six", "argon2-cffi",
             "duckdb", "fastapi", "uvicorn", "pydantic", "starlette", "openpyxl"):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass

binaries = []
binaries += collect_dynamic_libs("pypdfium2_raw")
binaries += collect_dynamic_libs("duckdb")

# ----------------------------------------------------------------------------- hidden imports
hiddenimports = []
hiddenimports += [m for m in collect_submodules("ledgerstitch") if ".tests" not in m]
hiddenimports += collect_submodules("uvicorn")            # loops/protocols are imported by string
hiddenimports += collect_submodules("argon2")
hiddenimports += collect_submodules("pdfminer")
hiddenimports += ["multipart", "python_multipart", "pypdfium2", "pypdfium2_raw",
                  "httpx", "h11", "anyio._backends._asyncio",
                  "email_validator", "tkinter", "tkinter.ttk"]
if IS_PRO:
    # --- compiled private add-ons ---------------------------------------------------------
    # Pro ships ONLY as a compiled native wheel (installed by scripts/build_macos.sh); see
    # the private add-on docs. We deliberately DO NOT put the source ``pro/`` dir on the
    # path here - otherwise PyInstaller would collect the readable .py instead of the .so,
    # and the extension modules would end up (as .pyc) in the PYZ archive. With only the
    # installed wheel importable, collect_submodules() resolves to the stripped .so files,
    # which PyInstaller stores as binaries (never in the PYZ).
    hiddenimports += collect_submodules("ledgerstitch_pro") + ["anthropic", "openai"]
    # --- end compiled private add-ons -----------------------------------------------------

excludes = [
    "pytest", "_pytest", "pluggy", "iniconfig", "tests",
    "reportlab",                                      # dev-only helper, not used by the app
    "sympy", "mpmath",                                # optional extras of some native wheels
    "coloredlogs", "humanfriendly",
    "matplotlib", "IPython", "jupyter", "notebook", "pandas", "scipy",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "gi",
    "setuptools", "pip", "pkg_resources._vendor", "distutils",
    "pydoc_data", "lib2to3",
]
if not IS_PRO:
    excludes += ["ledgerstitch_pro", "anthropic", "openai"]
# An add-on build's own dependencies and excludes (data files, native libraries) are listed by the
# private build tooling next to this spec; the file is absent from the public repository.
_addon_build = ROOT / "packaging" / "addon_build.py"
if IS_PRO and _addon_build.is_file():
    exec(compile(_addon_build.read_text("utf-8"), str(_addon_build), "exec"))  # noqa: S102

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],   # NOT ROOT/pro: Pro is collected from its installed compiled wheel
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

icon_mac = ROOT / "packaging" / "LedgerStitch.icns"
icon_win = ROOT / "packaging" / "LedgerStitch.ico"

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LedgerStitch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX breaks codesigning / trips antivirus
    console=False,             # windowed
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,          # build machine arch (arm64 here); see docs/RELEASE.md for universal2
    codesign_identity=None,    # signing is done by scripts/build_macos.sh
    entitlements_file=None,
    icon=str(icon_win if IS_WIN else icon_mac),
    version=str(ROOT / "packaging" / "win_version_info.txt") if IS_WIN and (ROOT / "packaging" / "win_version_info.txt").exists() else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LedgerStitch",
)

if IS_MAC:
    app = BUNDLE(  # noqa: F821
        coll,
        name="LedgerStitch.app",   # same app name for both editions; Pro unlocks with a key
        icon=str(icon_mac),
        bundle_identifier="com.ledgerstitch.app",
        version=VERSION,
        info_plist={
            "CFBundleName": "LedgerStitch",
            "CFBundleDisplayName": "LedgerStitch",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": MACOS_MIN,
            "LSApplicationCategoryType": "public.app-category.finance",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSHumanReadableCopyright": "Copyright (c) LedgerStitch. All rights reserved.",
            # Local-only server on 127.0.0.1; no network usage description needed.
        },
    )
