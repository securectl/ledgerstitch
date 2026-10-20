# SPDX-License-Identifier: AGPL-3.0-only
"""Desktop entry point (the double-clickable LedgerStitch.app / LedgerStitch.exe).

Starts the same local server as the ``ledgerstitch`` CLI (127.0.0.1 only, a free
port, a fresh random token, data in ~/LedgerStitch), opens the default browser and
shows a tiny control window ("LedgerStitch is running - Open in browser - Quit").

Single instance: the first instance holds an OS lock on ``<data-dir>/ledgerstitch.lock``
(mode 0600) and writes its pid/port/token there. A second launch finds the lock held,
reads the port, checks that the running server answers, opens the browser at it and
exits. (On macOS, double-clicking the .app again normally just re-activates the
running app, which we handle via the Dock "reopen" event.)

Flags (mainly for testing / support):
    --no-browser        don't open a browser
    --no-window         no control window; run until SIGTERM/SIGINT (headless)
    --port N            fixed port
    --data-dir DIR      data folder (default ~/LedgerStitch or $LEDGERSTITCH_DATA_DIR)
    --self-test         import every bundled heavy dependency (and the add-ons' own checks), exit
    --version

If Tk is not available in the build, the app runs without the control window
(browser only); quit it from the Dock (right-click > Quit) or Activity Monitor.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import signal
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

from . import __version__

HOST = "127.0.0.1"
LOCK_NAME = "ledgerstitch.lock"
BRAND = "#1F6F5C"

log = logging.getLogger("ledgerstitch.desktop")


# --------------------------------------------------------------------------- stdio safety
def _ensure_stdio() -> None:
    """Windowed (no-console) bundles may have no stdout/stderr; never crash on print."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))


def _say(msg: str) -> None:
    try:
        print(msg, flush=True)
    except Exception:
        pass


def _setup_logging(data_dir: Path) -> None:
    """Log only lifecycle events and errors (never transaction data) to <data>/logs/desktop.log."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        logs = data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        os.chmod(logs, 0o700)
        f = logs / "desktop.log"
        if f.exists() and f.stat().st_size > 1_000_000:
            f.replace(logs / "desktop.log.1")
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        handlers.append(logging.StreamHandler(os.fdopen(fd, "a", encoding="utf-8")))
    except OSError:
        pass
    logging.basicConfig(level=logging.WARNING, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.setLevel(logging.INFO)


# --------------------------------------------------------------------------- single instance
class InstanceLock:
    """An advisory OS lock held for the life of the process (released on crash too)."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / LOCK_NAME
        self.fd: Optional[int] = None

    def acquire(self) -> bool:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        try:
            if os.name == "nt":  # pragma: no cover - Windows, untested
                import msvcrt
                os.lseek(fd, 1 << 20, os.SEEK_SET)  # lock a byte past the JSON so it stays readable
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def write(self, info: dict) -> None:
        if self.fd is None:
            return
        data = json.dumps(info).encode()
        os.lseek(self.fd, 0, os.SEEK_SET)
        if os.name == "nt":  # pragma: no cover - don't truncate under the locked byte; NUL-pad
            os.write(self.fd, data.ljust(4096, b"\0"))
        else:
            os.ftruncate(self.fd, 0)
            os.write(self.fd, data)
        os.fsync(self.fd)

    def read(self) -> Optional[dict]:
        try:
            with open(self.path, "rb") as f:
                raw = f.read(4096).split(b"\0", 1)[0].strip()
            return json.loads(raw) if raw else None
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            if os.name == "nt":  # pragma: no cover
                os.write(self.fd, b"\0" * 4096)
            else:
                os.ftruncate(self.fd, 0)
        except OSError:
            pass
        try:
            os.close(self.fd)  # closing releases flock / msvcrt lock
        except OSError:
            pass
        self.fd = None


def _instance_alive(info: dict, timeout: float = 2.0) -> bool:
    """Is a LedgerStitch server with this token answering on this port?"""
    try:
        req = urllib.request.Request(f"http://{HOST}:{int(info['port'])}/api/status",
                                     headers={"X-LedgerStitch-Token": str(info["token"])})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


# --------------------------------------------------------------------------- server
class ServerThread:
    def __init__(self, data_dir: Path, port: int):
        import uvicorn
        from .api import create_app

        self.port = port
        self.token = secrets.token_urlsafe(32)
        self.url = f"http://{HOST}:{port}/"
        app = create_app(data_dir, self.token, port=port)
        config = uvicorn.Config(app, host=HOST, port=port, log_level="warning", access_log=False,
                                server_header=False, date_header=False, log_config=None)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._run, name="ledgerstitch-server", daemon=True)
        self.error: Optional[BaseException] = None

    def _run(self) -> None:
        try:
            self.server.run()  # uvicorn skips signal handlers off the main thread
        except BaseException as e:  # noqa: BLE001 - report anything to the main thread
            self.error = e

    def start(self, timeout: float = 30.0) -> bool:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server.started:
                return True
            if not self.thread.is_alive():
                return False
            time.sleep(0.02)
        return False

    def stop(self, timeout: float = 10.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        log.warning("could not open the browser")


# --------------------------------------------------------------------------- control window
def _tk_available() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def run_window(url: str, data_dir: Path, stop: threading.Event) -> None:
    """Tiny native control window. Returns when the user quits or ``stop`` is set."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    # Tk (Aqua) installs its own SIGTERM/SIGINT handling that exits the process without
    # running our shutdown (vault lock). Re-install ours after Tk is up.
    def on_signal(signum, _frame):
        stop.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError):
            pass
    root.title("LedgerStitch")
    root.resizable(False, False)
    try:
        root.configure(padx=18, pady=14)
    except tk.TclError:
        pass

    def do_open():
        open_browser(url)

    def do_quit(*_):
        stop.set()
        try:
            root.destroy()
        except tk.TclError:
            pass

    def do_reopen(*_):
        try:
            root.deiconify()
            root.lift()
        except tk.TclError:
            pass
        open_browser(url)

    head = tk.Frame(root)
    head.pack(fill="x")
    dot = tk.Canvas(head, width=14, height=14, highlightthickness=0)
    dot.create_oval(2, 2, 12, 12, fill=BRAND, outline=BRAND)
    dot.pack(side="left", padx=(0, 8))
    tk.Label(head, text="LedgerStitch is running", font=("TkDefaultFont", 14, "bold")).pack(side="left")
    tk.Label(root, text=url, fg="#555555").pack(anchor="w", pady=(6, 0))
    tk.Label(root, text=f"Data folder: {data_dir}", fg="#777777").pack(anchor="w")
    tk.Label(root, text="Quitting locks your vault.", fg="#777777").pack(anchor="w", pady=(0, 10))
    btns = tk.Frame(root)
    btns.pack(fill="x")
    ttk.Button(btns, text="Open in browser", command=do_open).pack(side="left")
    ttk.Button(btns, text="Quit", command=do_quit).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", do_quit)
    if sys.platform == "darwin":
        try:
            root.createcommand("::tk::mac::Quit", do_quit)                 # Cmd-Q / Dock > Quit
            root.createcommand("::tk::mac::ReopenApplication", do_reopen)  # Dock icon click
        except tk.TclError:
            pass

    def poll():
        if stop.is_set():
            do_quit()
        else:
            root.after(300, poll)
    root.after(300, poll)
    root.lift()
    root.mainloop()


# --------------------------------------------------------------------------- self-test
def _tiny_pdf(text: str) -> bytes:
    """A valid one-page PDF with a text layer, built by hand (no extra deps)."""
    stream = f"BT /F1 18 Tf 40 100 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def self_test() -> int:
    """Prove the bundle carries every heavy dependency. Exit 0 only if all checks pass."""
    import io
    import tempfile

    results: list[tuple[str, bool, str]] = []

    def check(name, fn):
        t = time.perf_counter()
        try:
            detail = fn() or ""
            results.append((name, True, f"{detail} ({time.perf_counter() - t:.2f}s)"))
        except Exception as e:  # noqa: BLE001
            results.append((name, False, f"{type(e).__name__}: {e}"))

    def t_static():
        from .api import STATIC_DIR
        need = ["index.html", "app.js", "styles.css", "disclaimer.txt", "logo-mark.svg"]
        missing = [n for n in need if not (STATIC_DIR / n).is_file()]
        if missing:
            raise FileNotFoundError(f"missing in {STATIC_DIR}: {missing}")
        return f"{len(list(STATIC_DIR.iterdir()))} files"

    def t_duckdb():
        import duckdb
        v = duckdb.connect().execute("select 40 + 2").fetchone()[0]
        assert v == 42
        return duckdb.__version__

    def t_crypto_vault():
        import argon2  # noqa: F401
        import cryptography
        from .security.vault import Vault
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.vault"
            v = Vault.create(p, "self-test-passphrase", "Self Test")
            v.lock()
            Vault.open(p, "self-test-passphrase").lock()
        try:
            from importlib.metadata import version
            av = version("argon2-cffi")
        except Exception:
            av = "?"
        return f"cryptography {cryptography.__version__}, argon2-cffi {av}"

    def t_pdfplumber():
        import pdfplumber
        with pdfplumber.open(io.BytesIO(_tiny_pdf("LedgerStitch 12.34"))) as pdf:
            txt = pdf.pages[0].extract_text() or ""
            pdf.pages[0].to_image(resolution=36)  # pypdfium2 renderer
        assert "12.34" in txt, txt
        return f"pdfplumber {pdfplumber.__version__}"

    def t_openpyxl():
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active["A1"] = "ok"
        buf = io.BytesIO()
        wb.save(buf)
        return f"openpyxl {openpyxl.__version__}, {len(buf.getvalue())} bytes"

    def t_http():
        import httpx
        return f"httpx {httpx.__version__}"

    def t_server_stack():
        import fastapi
        import multipart  # noqa: F401
        import uvicorn
        from .api import create_app
        create_app(tempfile.gettempdir(), secrets.token_urlsafe(32))
        return f"fastapi {fastapi.__version__}, uvicorn {uvicorn.__version__}"

    def t_tk():
        import tkinter
        return f"Tk {tkinter.TkVersion}"

    check("static UI files", t_static)
    check("duckdb", t_duckdb)
    check("vault (cryptography + argon2)", t_crypto_vault)
    check("pdfplumber + pdfminer + pypdfium2", t_pdfplumber)
    check("openpyxl", t_openpyxl)
    check("httpx", t_http)
    check("server stack", t_server_stack)
    check("tkinter", t_tk)
    # Add-ons bundled into this build check their own dependencies:
    # registry.provide("selftest", fn) with fn() -> [(name, check_fn), ...].
    from . import plugins
    for ns, provider in plugins.load().provided("selftest"):
        try:
            extra = list(provider() or [])
        except Exception as e:  # noqa: BLE001
            results.append((f"add-on {ns}", False, f"{type(e).__name__}: {e}"))
            continue
        for name, fn in extra:
            check(f"{ns}: {name}", fn)

    frozen = "frozen bundle" if getattr(sys, "frozen", False) else "source"
    _say(f"LedgerStitch {__version__} self-test ({frozen}, Python {sys.version.split()[0]})")
    for name, ok, detail in results:
        _say(f"  {'PASS' if ok else 'FAIL'}  {name:<34} {detail}")
    failed = [n for n, ok, _ in results if not ok]
    _say("SELF-TEST " + ("OK" if not failed else f"FAILED: {', '.join(failed)}"))
    return 0 if not failed else 1


# --------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    from .cli import DEFAULT_DATA_DIR
    p = argparse.ArgumentParser(prog="LedgerStitch", description="LedgerStitch desktop app.")
    p.add_argument("--version", action="version", version=f"LedgerStitch {__version__}")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--no-window", action="store_true", help="headless: no control window")
    p.add_argument("--self-test", action="store_true")
    return p


def _clean_argv(argv: list[str]) -> list[str]:
    # Finder used to pass -psn_0_12345 on old macOS; ignore it.
    return [a for a in argv if not a.startswith("-psn_")]


def main(argv: Optional[list[str]] = None) -> int:
    t0 = time.perf_counter()
    _ensure_stdio()
    args = build_parser().parse_args(_clean_argv(sys.argv[1:] if argv is None else argv))
    if args.self_test:
        return self_test()

    from .cli import free_port

    data_dir = Path(args.data_dir).expanduser()
    (data_dir / "vaults").mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(data_dir, 0o700)
    except OSError:
        pass
    _setup_logging(data_dir)

    lock = InstanceLock(data_dir)
    if not lock.acquire():
        info = None
        for _ in range(50):  # the other instance may still be starting up
            info = lock.read()
            if info and _instance_alive(info):
                break
            time.sleep(0.2)
        if info and _instance_alive(info):
            _say(f"LedgerStitch is already running at {info['url']}")
            if not args.no_browser:
                open_browser(info["url"])
            return 0
        _say("Another LedgerStitch is starting or not responding; try again in a moment.")
        log.warning("instance lock held but server not answering")
        return 1

    try:
        try:
            port = free_port(args.port)
        except SystemExit as e:
            _say(str(e))
            return 2
        srv = ServerThread(data_dir, port)
        if not srv.start():
            log.error("server failed to start: %r", srv.error)
            _say(f"LedgerStitch could not start: {srv.error!r}")
            return 1
        lock.write({"pid": os.getpid(), "port": port, "token": srv.token, "url": srv.url,
                    "version": __version__, "started": time.time()})
        ready = time.perf_counter() - t0
        _say(f"LedgerStitch {__version__} running privately at {srv.url} (ready in {ready:.2f}s)")
        _say(f"Data folder: {data_dir}")
        log.info("started v%s on port %d in %.2fs", __version__, port, ready)
        if not args.no_browser:
            open_browser(srv.url)

        stop = threading.Event()

        def on_signal(signum, _frame):
            stop.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, on_signal)
            except (ValueError, OSError):
                pass

        use_window = not args.no_window and _tk_available()
        if use_window:
            try:
                run_window(srv.url, data_dir, stop)
            except Exception:
                log.exception("control window failed; continuing headless")
                use_window = False
        if not use_window:
            while not stop.is_set() and srv.thread.is_alive():
                stop.wait(0.5)
        _say("Shutting down; vault locked.")
        srv.stop()
        log.info("stopped")
        return 0
    finally:
        lock.release()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
