# SPDX-License-Identifier: AGPL-3.0-only
"""PyInstaller entry script for the LedgerStitch desktop bundle.

Double-click / no args      -> desktop app (ledgerstitch.desktop)
<exe> import|export|vaults|ai-test ...  -> the regular CLI (for support / power users)
"""
import sys

CLI_COMMANDS = {"import", "export", "vaults", "ai-test"}


def _run() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    if args and args[0] in CLI_COMMANDS:
        from ledgerstitch.cli import main as cli_main
        return cli_main(args)
    from ledgerstitch.desktop import main as desktop_main
    return desktop_main(args)


if __name__ == "__main__":
    sys.exit(_run())
