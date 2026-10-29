# SPDX-License-Identifier: AGPL-3.0-only
"""Command-line entry point.

    ledgerstitch                      # start the local app and open the browser
    ledgerstitch --port 8765 --no-browser --data-dir ~/LedgerStitch
    ledgerstitch import <vault> <files...> [--account-label "Chase Ink"]
    ledgerstitch export <vault> --format xlsx --year 2025 --out report.xlsx
    ledgerstitch vaults               # list vaults in the data dir
    ledgerstitch benchmark [--ai] [--out DIR]   # accuracy benchmark on the synthetic corpus
    ledgerstitch paystub FILE... [--owner you|spouse] [--spouse FILE] [--final] [--out w2.xlsx]
    ledgerstitch --server [--host 0.0.0.0] [--port 8080] [--tls-cert C --tls-key K]   # self-hosted
    ledgerstitch admin-password       # set/reset the server-mode admin password

The desktop/local server binds to 127.0.0.1 only and uses a fresh random token per launch.
Server mode (``--server`` or LEDGERSTITCH_SERVER=1) binds 0.0.0.0, requires an admin
password sign-in and refuses plain HTTP unless LEDGERSTITCH_ALLOW_HTTP=1 (behind a TLS
reverse proxy). See ledgerstitch/server_mode.py and docs/INSTALL.md.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from . import __version__

DEFAULT_DATA_DIR = Path(os.environ.get("LEDGERSTITCH_DATA_DIR", "~/LedgerStitch")).expanduser()
HOST = "127.0.0.1"


def free_port(preferred: Optional[int] = None) -> int:
    if preferred:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # ignore TIME_WAIT leftovers
            try:
                s.bind((HOST, preferred))
                return preferred
            except OSError:
                raise SystemExit(f"Port {preferred} is already in use on {HOST}.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _open_browser_when_ready(url: str, port: int) -> None:
    def run():
        for _ in range(100):
            try:
                with socket.create_connection((HOST, port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        webbrowser.open(url)
    threading.Thread(target=run, daemon=True).start()


def cmd_server(args) -> int:
    """Self-hosted server mode: admin sign-in, 0.0.0.0, TLS (or an explicit HTTP opt-in)."""
    import uvicorn
    from .api import create_app
    from .server_mode import env_flag, from_env

    data_dir = Path(args.data_dir).expanduser()
    (data_dir / "vaults").mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(data_dir, 0o700)
    except OSError:
        pass
    host = args.host or os.environ.get("LEDGERSTITCH_HOST") or "0.0.0.0"
    port = int(args.port or os.environ.get("LEDGERSTITCH_PORT") or 8080)
    cert = args.tls_cert or os.environ.get("LEDGERSTITCH_TLS_CERT")
    key = args.tls_key or os.environ.get("LEDGERSTITCH_TLS_KEY")
    if bool(cert) != bool(key):
        print("Server mode needs both --tls-cert and --tls-key.", file=sys.stderr)
        return 2
    if not cert and not env_flag("LEDGERSTITCH_ALLOW_HTTP"):
        print("Refusing to start server mode without TLS.\n"
              "  Either pass --tls-cert/--tls-key (or LEDGERSTITCH_TLS_CERT/LEDGERSTITCH_TLS_KEY),\n"
              "  or put LedgerStitch behind an HTTPS reverse proxy (see docker/docker-compose.yml,\n"
              "  Caddy) and set LEDGERSTITCH_ALLOW_HTTP=1.", file=sys.stderr)
        return 2
    auth = from_env(data_dir)
    app = create_app(data_dir, secrets.token_urlsafe(32), server=auth)
    scheme = "https" if cert else "http"
    print(f"LedgerStitch {__version__} server mode on {scheme}://{host}:{port}/", flush=True)
    print(f"Data folder: {data_dir}", flush=True)
    if not cert:
        print("Plain HTTP (LEDGERSTITCH_ALLOW_HTTP=1): only expose this behind an HTTPS proxy.", flush=True)
    if auth.setup_code:
        print(f"First run: open the site and set the admin password. Setup code: {auth.setup_code}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False,
                server_header=False, date_header=False, proxy_headers=False,
                ssl_certfile=cert or None, ssl_keyfile=key or None)
    return 0


def cmd_admin_password(args) -> int:
    from .server_mode import MIN_ADMIN_PASSWORD, ServerAuth
    data_dir = Path(args.data_dir).expanduser()
    pw = getpass.getpass(f"New admin password (min {MIN_ADMIN_PASSWORD} characters): ")
    if pw != getpass.getpass("Repeat: "):
        print("The passwords don't match.", file=sys.stderr)
        return 1
    try:
        ServerAuth(data_dir).set_password(pw)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Admin password saved (Argon2id hash in {data_dir / 'server' / 'admin.json'}). "
          "Restart the server to sign out existing sessions.")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    from .api import create_app
    from .server_mode import env_flag

    if getattr(args, "server", False) or env_flag("LEDGERSTITCH_SERVER"):
        return cmd_server(args)

    data_dir = Path(args.data_dir).expanduser()
    (data_dir / "vaults").mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(data_dir, 0o700)
    except OSError:
        pass
    port = free_port(args.port)
    token = secrets.token_urlsafe(32)
    app = create_app(data_dir, token, port=port)
    url = f"http://{HOST}:{port}/"
    print(f"LedgerStitch {__version__} running privately at {url}")
    print(f"Data folder: {data_dir}")
    print("Press Ctrl+C to quit (your vault locks automatically).")
    if not args.no_browser:
        _open_browser_when_ready(url, port)
    uvicorn.run(app, host=HOST, port=port, log_level="warning", access_log=False,
                server_header=False, date_header=False)
    return 0


# --------------------------------------------------------------------------- offline subcommands
def _is_explicit_path(vault: str) -> bool:
    p = Path(vault).expanduser()
    return p.exists() or p.suffix == ".vault" or os.sep in vault


def new_vault_path(vaults_dir: Path) -> Path:
    """``ws-<8 hex>.vault``: a folder name that reveals nothing about the client."""
    for _ in range(20):
        p = vaults_dir / f"ws-{secrets.token_hex(4)}.vault"
        if not p.exists():
            return p
    raise SystemExit("Could not allocate a vault folder.")


def _vault_path(data_dir: Path, vault: str) -> Path:
    """An explicit path, or (legacy) the folder named after the vault."""
    from .store import slugify
    if _is_explicit_path(vault):
        return Path(vault).expanduser()
    return data_dir / "vaults" / f"{slugify(vault)}.vault"


def _passphrase(prompt: str = "Vault passphrase: ") -> str:
    env = os.environ.get("LEDGERSTITCH_PASSPHRASE")
    if env:
        return env
    return getpass.getpass(prompt)


def _open_workspace(args):
    from .security.vault import Vault, WrongPassphrase
    from .store import Workspace
    path = _vault_path(Path(args.data_dir).expanduser(), args.vault)
    if not Vault.exists(path):
        if not getattr(args, "create", False):
            raise SystemExit(f"No vault at {path}. Use --create to make one.")
        pw = _passphrase("New vault passphrase: ")
        if len(pw) < 8:
            raise SystemExit("Passphrase must be at least 8 characters.")
        if _is_explicit_path(args.vault):
            name = path.stem
        else:
            # A name, not a path: like the app, use a meaningless folder name so the
            # business/client name is only stored encrypted (no plaintext label).
            name, path = args.vault, new_vault_path(Path(args.data_dir).expanduser() / "vaults")
        path.parent.mkdir(parents=True, exist_ok=True)
        v = Vault.create(path, pw, name)
        print(f"Created vault {path} (use this path next time).", file=sys.stderr)
    else:
        try:
            v = Vault.open(path, _passphrase())
        except WrongPassphrase:
            raise SystemExit("Wrong passphrase.")
    return Workspace(v)


def cmd_import(args) -> int:
    from .store import StoreError
    ws = _open_workspace(args)
    rc = 0
    try:
        for f in args.files:
            p = Path(f).expanduser()
            try:
                r = ws.add_import(p.name, p.read_bytes(), args.account_label)
            except (StoreError, OSError) as e:
                print(f"  {p.name}: ERROR {e}", file=sys.stderr)
                rc = 1
                continue
            det = r.detected or {}
            print(f"  {p.name}: {det.get('institution')} ({det.get('profile')}) -> {r.account_id}: "
                  f"{r.count} rows, {r.new} new")
            for w in r.warnings:
                print(f"    warning: {w}")
        print(f"Vault now holds {len(ws.transactions)} transactions in {len(ws.accounts)} accounts.")
    finally:
        ws.vault.lock()
    return rc


def cmd_export(args) -> int:
    ws = _open_workspace(args)
    try:
        year = args.year or ws.default_year()
        txns = ws.txns_for_year(year)
        ext = {"qbo": "csv"}.get(args.format, args.format)
        stem = f"ledgerstitch-{year}"
        out = Path(args.out).expanduser() if args.out else Path.cwd() / f"{stem}.{ext}"
        if args.format == "xlsx":
            from .reports.excel import export_excel
            out = export_excel(txns, ws.accounts, out, ws.settings.get("business_name") or ws.name, year)
        elif args.format == "csv":
            from .reports.csv_export import export_csv
            out = export_csv(txns, out, year)
        elif args.format == "qbo":
            from .reports.quickbooks import export_qbo_csv
            out = export_qbo_csv(txns, ws.accounts, out, year)
        else:
            from .reports.quickbooks import export_iif
            out = export_iif(txns, ws.accounts, out, None, year)
        print(f"Wrote {out} (plaintext - store it somewhere safe).")
    finally:
        ws.vault.lock()
    return 0


def cmd_vaults(args) -> int:
    d = Path(args.data_dir).expanduser() / "vaults"
    if not d.is_dir():
        print("No vaults yet.")
        return 0
    for p in sorted(d.glob("*.vault")):
        print(p)
    return 0


_NOT_COPIED = {"business_name", "business_profile", "tax_year", "auto_lock_minutes"}


def _benchmark_settings(args) -> dict:
    """AI settings for `benchmark --ai`: copied from a vault (--settings-from) and/or --setting KEY=VALUE."""
    import json
    settings: dict = {}
    if args.settings_from:
        from .security.vault import Vault, WrongPassphrase
        path = _vault_path(Path(args.data_dir).expanduser(), args.settings_from)
        if not Vault.exists(path):
            raise SystemExit(f"No vault at {path}.")
        try:
            v = Vault.open(path, _passphrase())
        except WrongPassphrase:
            raise SystemExit("Wrong passphrase.")
        try:   # only the settings are read, never the vault's transactions
            settings = {k: val for k, val in (v.get_json("settings", {}) or {}).items() if k not in _NOT_COPIED}
        finally:
            v.lock()
    for kv in args.setting or []:
        if "=" not in kv:
            raise SystemExit(f"--setting expects KEY=VALUE, got {kv!r}")
        k, val = kv.split("=", 1)
        try:
            settings[k.strip()] = json.loads(val)
        except ValueError:
            settings[k.strip()] = val
    if args.provider:
        settings["provider"] = args.provider
    if args.model:
        settings["model"] = args.model
    return settings


def cmd_benchmark(args) -> int:
    from .bench.report import pct
    from .bench.runner import run_benchmark
    settings = _benchmark_settings(args) if args.ai else {}
    out = Path(args.out).expanduser()
    r = run_benchmark(ai=args.ai, out_dir=out, seed=args.seed, settings=settings,
                      corpus_out=Path(args.corpus_out).expanduser() if args.corpus_out else None)
    p, c = r["parse"], r["categories"]
    print(f"Synthetic corpus: {r['corpus']['files']} files, {r['corpus']['rows']} rows "
          f"(seed {r['corpus']['seed']}); {r['run']['seconds']} s")
    print(f"  rows: recall {pct(p['row_recall'])}, precision {pct(p['row_precision'])}, "
          f"exact date+amount+sign {pct(p['exact_match'])}")
    print(f"  statements balancing: {r['statements']['balanced']}/{r['statements']['total']}")
    print(f"  duplicates P/R {pct(r['duplicates']['precision'])}/{pct(r['duplicates']['recall'])}, "
          f"transfers P/R {pct(r['transfers']['precision'])}/{pct(r['transfers']['recall'])}, "
          f"refunds P/R {pct(r['refunds']['precision'])}/{pct(r['refunds']['recall'])}")
    print(f"  category accuracy {pct(c['all']['category_accuracy'])}, "
          f"Schedule C line accuracy {pct(c['schedule_c']['accuracy'])}"
          + (f", {r['ai']['sent_descriptors']} redacted descriptors sent to AI" if args.ai else ""))
    for w in r["ai"]["warnings"]:
        print(f"  AI warning: {w}")
    print(f"Wrote {out / 'benchmark.json'} and {out / 'BENCHMARK.md'}")
    return 0


def _fmt_money(v) -> str:
    return "-" if v is None else f"{v:,.2f}"


def cmd_paystub(args) -> int:
    """Pay stubs -> year-end projection, estimated W-2 and household signals. In memory, no vault."""
    import json

    from .payroll import analyze_paystubs
    from .store import to_jsonable
    files = []
    try:
        for f in args.files:
            p = Path(f).expanduser()
            files.append((p.name, p.read_bytes(), args.owner))
        for f in args.spouse or []:
            p = Path(f).expanduser()
            files.append((p.name, p.read_bytes(), "spouse"))
        report = analyze_paystubs(files, final=args.final, frequency=args.frequency,
                                  filing_status=args.filing_status,
                                  spouse_no_earned_income=args.spouse_no_earned_income, year=args.year)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.out:
        out = Path(args.out).expanduser()
        if out.suffix.lower() == ".xlsx":
            from .reports.paystub_export import export_paystub_xlsx
            export_paystub_xlsx(report, out)
        else:
            from .reports.paystub_export import export_paystub_csv
            export_paystub_csv(report, out)
        print(f"Wrote {out} (plaintext - store it somewhere safe).", file=sys.stderr)
    if args.json:
        print(json.dumps(to_jsonable(report), indent=2))
        return 0
    print(report.label)
    print(f"Tax year {report.year}, filing status {report.filing_status}")
    for w in report.w2:
        basis = "YTD as final" if w.final else f"{w.periods_remaining} pay dates left, {w.frequency}"
        print(f"\n{w.owner}: {w.employer or '(employer not found)'} ({basis})")
        for box in sorted(w.boxes, key=int):
            print(f"  box {box:>2}  {_fmt_money(w.boxes[box]):>14}")
        for code, v in w.box12.items():
            print(f"  box 12{code:<3}{_fmt_money(v):>14}")
        print(f"  box 13  retirement plan: {'yes' if w.box13_retirement_plan else 'no'}")
        for line in w.box14:
            print(f"  box 14  {line}")
    if report.signals:
        print("\nSignals")
        for s in report.signals:
            who = s.owner or "household"
            print(f"  [{s.severity}] {who}: {s.title}")
            print(f"      {s.explanation} ({s.citation})")
    for wn in report.warnings:
        print(f"warning: {wn}", file=sys.stderr)
    print(f"\n{report.disclaimer}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ledgerstitch", description="Private, local expense consolidation.")
    p.add_argument("--version", action="version", version=f"ledgerstitch {__version__}")
    p.add_argument("--port", type=int, default=None, help="port on 127.0.0.1 (default: a free port)")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="where vaults live (default ~/LedgerStitch)")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    p.add_argument("--server", action="store_true",
                   help="self-hosted server mode: admin sign-in, binds 0.0.0.0 (also LEDGERSTITCH_SERVER=1)")
    p.add_argument("--host", default=None, help="with --server: bind address (default 0.0.0.0)")
    p.add_argument("--tls-cert", default=None, help="with --server: TLS certificate (PEM)")
    p.add_argument("--tls-key", default=None, help="with --server: TLS private key (PEM)")
    sub = p.add_subparsers(dest="cmd")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=argparse.SUPPRESS)

    pi = sub.add_parser("import", parents=[common], help="import statement files into a vault")
    pi.add_argument("vault", help="vault name or path")
    pi.add_argument("files", nargs="+")
    pi.add_argument("--account-label", default=None)
    pi.add_argument("--create", action="store_true", help="create the vault if it doesn't exist")
    pi.set_defaults(func=cmd_import)

    pe = sub.add_parser("export", parents=[common], help="export a report from a vault")
    pe.add_argument("vault")
    pe.add_argument("--format", choices=["xlsx", "csv", "qbo", "iif"], default="xlsx")
    pe.add_argument("--year", type=int, default=None)
    pe.add_argument("--out", default=None)
    pe.set_defaults(func=cmd_export)

    pv = sub.add_parser("vaults", parents=[common], help="list vaults")
    pv.set_defaults(func=cmd_vaults)

    pa = sub.add_parser("admin-password", parents=[common], help="set the server-mode admin password")
    pa.set_defaults(func=cmd_admin_password)

    from .bench import DEFAULT_SEED
    pb = sub.add_parser("benchmark", parents=[common],
                        help="measure parsing/reconciliation/categorization accuracy on a synthetic corpus")
    pb.add_argument("--out", default="ledgerstitch-benchmark", help="folder for benchmark.json + BENCHMARK.md")
    pb.add_argument("--ai", action="store_true",
                    help="also categorize with the configured AI provider (redacted descriptors only)")
    pb.add_argument("--provider", default=None, help="with --ai: provider id (default: ollama, local)")
    pb.add_argument("--model", default=None, help="with --ai: model name")
    pb.add_argument("--settings-from", default=None, metavar="VAULT",
                    help="with --ai: copy the AI settings (not the data) from this vault")
    pb.add_argument("--setting", action="append", metavar="KEY=VALUE", help="with --ai: extra setting")
    pb.add_argument("--seed", type=int, default=DEFAULT_SEED, help="corpus seed (default %(default)s)")
    pb.add_argument("--corpus-out", default=None, metavar="DIR", help="also keep the generated corpus here")
    pb.set_defaults(func=cmd_benchmark)

    pp = sub.add_parser("paystub", parents=[common],
                        help="pay stubs -> year-end projection, estimated W-2 and free payroll signals")
    pp.add_argument("files", nargs="+", help="pay stub PDFs (text-based) or text files")
    pp.add_argument("--owner", choices=["you", "spouse"], default="you", help="whose stubs FILE... are")
    pp.add_argument("--spouse", action="append", metavar="FILE", help="a spouse's pay stub (repeatable)")
    pp.add_argument("--final", action="store_true", help="treat YTD as the year's final totals (no projection)")
    pp.add_argument("--frequency", choices=["weekly", "biweekly", "semimonthly", "monthly"], default=None,
                    help="pay frequency (default: detected from the pay period)")
    pp.add_argument("--filing-status", choices=["single", "mfj", "mfs"], default=None)
    pp.add_argument("--spouse-no-earned-income", action="store_true",
                    help="one spouse has no earned income (dependent care FSA warning)")
    pp.add_argument("--year", type=int, default=None)
    pp.add_argument("--json", action="store_true", help="print the full result as JSON")
    pp.add_argument("--out", default=None, help="also write the estimate to a .csv or .xlsx file")
    pp.set_defaults(func=cmd_paystub)

    # Add-ons (e.g. LedgerStitch Pro's `ai-test`) add their own subcommands.
    from . import plugins
    for ns, install in plugins.load().cli_installers:
        try:
            install(sub)
        except Exception as e:  # noqa: BLE001
            print(f"warning: add-on {ns} could not add its commands ({type(e).__name__})", file=sys.stderr)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    from . import editions
    if os.environ.get("LEDGERSTITCH_EDITION"):
        editions.configure(env=os.environ)
    args = build_parser().parse_args(argv)
    func = getattr(args, "func", None)
    try:
        return func(args) if func else cmd_serve(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
