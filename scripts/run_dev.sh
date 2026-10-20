#!/usr/bin/env bash
# Run LedgerStitch from a source checkout (creates .venv on first run).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -e ".[dev]"
fi
exec .venv/bin/python -m ledgerstitch "$@"
