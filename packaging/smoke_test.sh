#!/usr/bin/env bash
# Headless smoke test of a built LedgerStitch bundle (macOS).
#
#   packaging/smoke_test.sh [path/to/LedgerStitch.app | path/to/LedgerStitch.dmg] [port]
#
# Checks: --self-test passes; server answers / with 200 + <title>LedgerStitch; static files are
# served; /api/status is 401 without the token and 200 with it; the lock file is 0600; a second
# launch defers to the running instance; SIGTERM shuts down cleanly. Prints startup time.
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${1:-dist/LedgerStitch.app}"
PORT="${2:-8799}"
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/ledgerstitch-smoke.XXXXXX")"
MNT=""
cleanup() {
  [ -n "${PID:-}" ] && kill "$PID" 2>/dev/null || true
  [ -n "$MNT" ] && hdiutil detach "$MNT" >/dev/null 2>&1 || true
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

if [[ "$TARGET" == *.dmg ]]; then
  MNT="$SCRATCH/mnt"; mkdir -p "$MNT"
  hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$TARGET" >/dev/null
  APP="$MNT/LedgerStitch.app"
else
  APP="$TARGET"
fi
EXE="$APP/Contents/MacOS/LedgerStitch"
DATA="$SCRATCH/data"
fail() { echo "FAIL: $*" >&2; exit 1; }
now() { python3 -c 'import time;print(time.time())' 2>/dev/null || date +%s; }

echo "== $APP"
codesign --verify --deep --strict "$APP" && echo "ok   signature valid"
"$EXE" --self-test | tail -1 | grep -q "SELF-TEST OK" && echo "ok   self-test" || fail "self-test"

T0=$(now)
"$EXE" --no-browser --no-window --port "$PORT" --data-dir "$DATA" >"$SCRATCH/out.txt" 2>&1 &
PID=$!
for _ in $(seq 1 600); do curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 0.05; done
T1=$(now)
echo "ok   startup to first HTTP response: $(python3 -c "print(f'{$T1-$T0:.2f}s')" 2>/dev/null || echo "$((T1-T0))s")"

code=$(curl -s -o "$SCRATCH/index.html" -w '%{http_code}' "http://127.0.0.1:$PORT/")
[ "$code" = 200 ] || fail "GET / -> $code"
grep -q "<title>LedgerStitch" "$SCRATCH/index.html" || fail "no <title>LedgerStitch"
echo "ok   GET / -> 200 with <title>LedgerStitch"
for f in styles.css app.js disclaimer.txt logo-mark.svg; do
  c=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/$f"); [ "$c" = 200 ] || fail "GET /$f -> $c"
done
echo "ok   static files served"
c=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/status"); [ "$c" = 401 ] || fail "/api/status no token -> $c"
echo "ok   /api/status without token -> 401"
TOK=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['token'])" "$DATA/ledgerstitch.lock")
c=$(curl -s -o /dev/null -w '%{http_code}' -H "X-LedgerStitch-Token: $TOK" "http://127.0.0.1:$PORT/api/status"); [ "$c" = 200 ] || fail "/api/status token -> $c"
echo "ok   /api/status with token -> 200"
[ "$(stat -f %Lp "$DATA/ledgerstitch.lock")" = 600 ] || fail "lock file not 0600"
echo "ok   lock file is 0600"
"$EXE" --no-browser --data-dir "$DATA" | grep -q "already running" || fail "second instance did not defer"
echo "ok   second launch defers to running instance"
kill "$PID"; wait "$PID" && echo "ok   SIGTERM -> clean exit" || fail "non-zero exit after SIGTERM"
PID=""
grep -q "Shutting down" "$SCRATCH/out.txt" || fail "no clean shutdown message"
echo "ALL SMOKE TESTS PASSED"
