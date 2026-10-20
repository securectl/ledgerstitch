#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# LedgerStitch installer for macOS and Linux (run from source; no system Python needed).
#
#   sh install.sh                          # from a LedgerStitch checkout: installs that checkout
#   curl --proto '=https' --tlsv1.2 -fsSL <url>/install.sh | sh -s -- ledgerstitch
#   sh install.sh 'ledgerstitch==1.0.1'    # a PyPI release
#   sh install.sh git+https://github.com/<org>/ledgerstitch.git@v1.0.1
#   sh install.sh --uninstall [--purge-data [--yes]]
#   sh install.sh --help
#
# What it does: installs `uv` if missing (official installer, HTTPS only), fetches a
# uv-managed Python 3.12, creates an isolated venv in the app dir, installs LedgerStitch into
# it, and creates launchers (a `ledgerstitch` command, plus ~/Applications/LedgerStitch.app on
# macOS or a desktop entry on Linux). Re-running upgrades in place. Never uses sudo and never
# touches your data folder (~/LedgerStitch or $LEDGERSTITCH_DATA_DIR) except with
# --uninstall --purge-data.
set -eu

MARKER="created by the LedgerStitch installer"
PY_VERSION="${LEDGERSTITCH_PYTHON:-3.12}"
UV_INSTALL_URL="https://astral.sh/uv/install.sh"

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
LedgerStitch installer (macOS / Linux)

Usage: install.sh [options] [SOURCE]

SOURCE (default: the LedgerStitch checkout this script is in, if any)
  ./path/to/checkout           a local LedgerStitch source tree
  ledgerstitch[==X.Y.Z]        a release from PyPI
  git+https://.../ledgerstitch.git[@ref]   a git URL (HTTPS or SSH, never plain http)

Options
  --extra SPEC        also install SPEC into the same venv (a pip requirement or wheel path,
                      e.g. an add-on); repeatable
  --editable          install a local checkout in editable mode (developers)
  --prefix DIR        app dir holding the venv + manifest        [env LEDGERSTITCH_PREFIX]
  --bin-dir DIR       where the `ledgerstitch` command goes        [env LEDGERSTITCH_BIN_DIR]
  --apps-dir DIR      where the app wrapper / desktop entry goes   [env LEDGERSTITCH_APPS_DIR]
  --no-app            no app wrapper / desktop entry               [env LEDGERSTITCH_NO_APP=1]
  --uninstall         remove the venv, command and app wrapper (keeps your data)
  --purge-data        with --uninstall: also delete the data folder (asks first)
  --yes, -y           don't ask for confirmation
  --allow-root        allow running as root (not recommended)
  -h, --help          show this help

Environment
  LEDGERSTITCH_DATA_DIR  data folder (default ~/LedgerStitch; only touched by --purge-data)
  LEDGERSTITCH_PYTHON    Python version for the venv (default 3.12)
  LEDGERSTITCH_SOURCE    same as the SOURCE argument
  UV                     path to an existing uv binary

Defaults
  macOS: app dir ~/Library/Application Support/LedgerStitch/app, command in ~/.local/bin,
         app wrapper ~/Applications/LedgerStitch.app
  Linux: app dir ${XDG_DATA_HOME:-~/.local/share}/ledgerstitch/app, command in ~/.local/bin,
         desktop entry ${XDG_DATA_HOME:-~/.local/share}/applications/ledgerstitch.desktop
EOF
}

# ------------------------------------------------------------------ arguments
SOURCE="${LEDGERSTITCH_SOURCE:-}"
PREFIX="${LEDGERSTITCH_PREFIX:-}"
BIN_DIR="${LEDGERSTITCH_BIN_DIR:-}"
APPS_DIR="${LEDGERSTITCH_APPS_DIR:-}"
NO_APP="${LEDGERSTITCH_NO_APP:-0}"
EXTRAS=""; EDITABLE=0; UNINSTALL=0; PURGE=0; YES=0; ALLOW_ROOT=0; GOT_SOURCE=0

need_arg() { [ $# -ge 2 ] && [ -n "$2" ] || die "$1 needs a value"; }
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --extra) need_arg "$@"; EXTRAS="$EXTRAS
$2"; shift ;;
    --extra=*) EXTRAS="$EXTRAS
${1#*=}" ;;
    --editable) EDITABLE=1 ;;
    --prefix) need_arg "$@"; PREFIX="$2"; shift ;;
    --prefix=*) PREFIX="${1#*=}" ;;
    --bin-dir) need_arg "$@"; BIN_DIR="$2"; shift ;;
    --bin-dir=*) BIN_DIR="${1#*=}" ;;
    --apps-dir) need_arg "$@"; APPS_DIR="$2"; shift ;;
    --apps-dir=*) APPS_DIR="${1#*=}" ;;
    --no-app) NO_APP=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --purge-data) PURGE=1 ;;
    -y|--yes) YES=1 ;;
    --allow-root) ALLOW_ROOT=1 ;;
    -*) die "unknown option: $1 (see --help)" ;;
    *) [ "$GOT_SOURCE" = 0 ] || die "only one SOURCE allowed"; SOURCE="$1"; GOT_SOURCE=1 ;;
  esac
  shift
done
if [ "$PURGE" = 1 ] && [ "$UNINSTALL" = 0 ]; then die "--purge-data only works together with --uninstall"; fi

if [ "$(id -u)" = 0 ] && [ "$ALLOW_ROOT" = 0 ]; then
  die "don't run this as root/sudo: LedgerStitch installs per user. (Override: --allow-root)"
fi
[ -n "${HOME:-}" ] || die "HOME is not set"

OS="$(uname -s)"
case "$OS" in
  Darwin)
    : "${PREFIX:=$HOME/Library/Application Support/LedgerStitch/app}"
    : "${APPS_DIR:=$HOME/Applications}" ;;
  Linux)
    DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
    : "${PREFIX:=$DATA_HOME/ledgerstitch/app}"
    : "${APPS_DIR:=$DATA_HOME/applications}" ;;
  *) die "unsupported OS: $OS (on Windows use install.ps1)" ;;
esac
: "${BIN_DIR:=$HOME/.local/bin}"
case "$PREFIX" in /*) ;; *) PREFIX="$(pwd)/$PREFIX" ;; esac
case "$BIN_DIR" in /*) ;; *) BIN_DIR="$(pwd)/$BIN_DIR" ;; esac
case "$APPS_DIR" in /*) ;; *) APPS_DIR="$(pwd)/$APPS_DIR" ;; esac
VENV="$PREFIX/venv"
VPY="$VENV/bin/python"
MANIFEST="$PREFIX/install-manifest.txt"
DATA_DIR="${LEDGERSTITCH_DATA_DIR:-$HOME/LedgerStitch}"
case "$DATA_DIR" in "~"/*) DATA_DIR="$HOME/${DATA_DIR#"~/"}" ;; esac
if [ "$OS" = Darwin ]; then APP_PATH="$APPS_DIR/LedgerStitch.app"; else APP_PATH="$APPS_DIR/ledgerstitch.desktop"; fi

# ------------------------------------------------------------------ helpers
# Is this path something we created? (never delete a user's own / DMG-installed app)
is_ours() {
  if [ -d "$1" ]; then
    [ -f "$1/Contents/MacOS/LedgerStitch" ] && grep -q "$MARKER" "$1/Contents/MacOS/LedgerStitch" 2>/dev/null
  else
    [ -f "$1" ] && grep -q "$MARKER" "$1" 2>/dev/null
  fi
}

remove_ours() {
  [ -e "$1" ] || [ -L "$1" ] || return 0
  if is_ours "$1"; then rm -rf "$1"; say "removed $1"
  else warn "left $1 alone (not created by this installer)"; fi
}

manifest_paths() { if [ -f "$MANIFEST" ]; then sed -n 's/^path=//p' "$MANIFEST"; fi; }

confirm() {  # confirm "question": reads the terminal even when the script is piped (curl | sh)
  [ "$YES" = 1 ] && return 0
  if ( : < /dev/tty ) 2>/dev/null; then
    printf '%s [y/N] ' "$1" > /dev/tty
    read -r ans < /dev/tty || ans=""
    case "$ans" in y|Y|yes|YES|Yes) return 0 ;; esac
    return 1
  fi
  die "no terminal to ask for confirmation: re-run with --yes"
}

on_path() { case ":$PATH:" in *":$1:"*) return 0 ;; esac; return 1; }

# ------------------------------------------------------------------ uninstall
if [ "$UNINSTALL" = 1 ]; then
  # validate + confirm a data purge BEFORE removing anything, so a "no" changes nothing
  PURGE_DIR=""
  if [ "$PURGE" = 1 ] && [ -e "$DATA_DIR" ]; then
    PURGE_DIR="$(cd "$DATA_DIR" && pwd -P)"
    case "$PURGE_DIR" in /|"$HOME"|"$(cd "$HOME" && pwd -P)") die "refusing to delete $PURGE_DIR" ;; esac
    [ -d "$PURGE_DIR/vaults" ] || die "$PURGE_DIR doesn't look like a LedgerStitch data folder (no vaults/); not deleting it"
    confirm "Uninstall AND permanently delete ALL LedgerStitch data (vaults, receipts, backups) in $PURGE_DIR?" ||
      die "cancelled; nothing was removed"
  fi
  say "uninstalling LedgerStitch from $PREFIX"
  paths="$(manifest_paths)"
  [ -n "$paths" ] || paths="$BIN_DIR/ledgerstitch
$APP_PATH"
  printf '%s\n' "$paths" | while IFS= read -r p; do [ -n "$p" ] && remove_ours "$p"; done
  if [ -d "$PREFIX" ]; then
    if [ -f "$MANIFEST" ] || [ -x "$VPY" ]; then
      rm -rf "$VENV" "$MANIFEST"; say "removed $VENV"
      rmdir "$PREFIX" 2>/dev/null || warn "kept $PREFIX (contains other files)"
      if [ "$OS" = Darwin ]; then rmdir "$(dirname "$PREFIX")" 2>/dev/null || true; fi
    else
      warn "$PREFIX has no LedgerStitch install manifest or venv; left it alone"
    fi
  fi
  if [ "$OS" = Linux ] && command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
  fi
  if [ -n "$PURGE_DIR" ]; then
    rm -rf "$PURGE_DIR"; say "deleted data folder $PURGE_DIR"
  elif [ "$PURGE" = 1 ]; then
    say "no data folder at $DATA_DIR"
  elif [ -e "$DATA_DIR" ]; then
    say "kept your data in $DATA_DIR (to delete it too: --uninstall --purge-data)"
  fi
  say "LedgerStitch uninstalled. (uv and its managed Pythons were left in place.)"
  exit 0
fi

# ------------------------------------------------------------------ source
is_checkout() { [ -f "$1/pyproject.toml" ] && grep -Eq '^name *= *"ledgerstitch"' "$1/pyproject.toml"; }

SCRIPT_DIR=""
case "$0" in */install.sh|install.sh) SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd -P)" || SCRIPT_DIR="" ;; esac

LOCAL_SRC=""
if [ -z "$SOURCE" ]; then
  if [ -n "$SCRIPT_DIR" ] && is_checkout "$SCRIPT_DIR"; then
    LOCAL_SRC="$SCRIPT_DIR"
  else
    die "no SOURCE given and this script isn't inside a LedgerStitch checkout.
  Try: sh install.sh ledgerstitch   (PyPI)   or   sh install.sh git+https://<host>/<org>/ledgerstitch.git"
  fi
else
  case "$SOURCE" in
    http://*|git+http://*) die "refusing a non-HTTPS source: $SOURCE" ;;
    git+https://*|git+ssh://*|https://*) ;;
    ledgerstitch|ledgerstitch[=\<\>!~\[]*) ;;
    *) if [ -d "$SOURCE" ] && is_checkout "$SOURCE"; then LOCAL_SRC="$(cd "$SOURCE" && pwd -P)"
       else die "unrecognised SOURCE: $SOURCE (a LedgerStitch checkout, ledgerstitch[==X.Y.Z] or git+https://...)"; fi ;;
  esac
fi
if [ "$EDITABLE" = 1 ] && [ -z "$LOCAL_SRC" ]; then die "--editable needs a local checkout"; fi
SRC_DESC="${LOCAL_SRC:-$SOURCE}"

# ------------------------------------------------------------------ uv
find_uv() {
  if [ -n "${UV:-}" ] && [ -x "$UV" ]; then return 0; fi
  UV="$(command -v uv 2>/dev/null || true)"; [ -n "$UV" ] && return 0
  for c in "${XDG_BIN_HOME:-$HOME/.local/bin}/uv" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$c" ]; then UV="$c"; return 0; fi
  done
  return 1
}
if ! find_uv; then
  say "installing uv (https://docs.astral.sh/uv/) from $UV_INSTALL_URL"
  command -v curl >/dev/null 2>&1 || die "curl is required to install uv"
  # *_NO_MODIFY_PATH: the uv installer must not edit shell profiles
  curl --proto '=https' --tlsv1.2 -fsSL "$UV_INSTALL_URL" |
    env UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null ||
    die "uv installation failed (network?)"
  find_uv || die "uv was installed but can't be found; add ~/.local/bin to PATH and re-run"
fi
say "using uv: $UV ($("$UV" --version 2>/dev/null || echo 'unknown version'))"

# ------------------------------------------------------------------ python + venv
say "fetching a uv-managed Python $PY_VERSION"
"$UV" python install "$PY_VERSION" >/dev/null 2>&1 || "$UV" python install "$PY_VERSION" ||
  die "could not install Python $PY_VERSION (network?)"
if "$UV" venv --help 2>/dev/null | grep -q -- '--managed-python'; then MANAGED="--managed-python"
else MANAGED="--python-preference=only-managed"; fi

venv_ok() {
  [ -x "$VPY" ] || return 1
  have="$("$VPY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || return 1
  [ "$have" = "$PY_VERSION" ]
}
mkdir -p "$PREFIX"
if venv_ok; then
  say "upgrading the existing install in $PREFIX"
else
  if [ -e "$VENV" ]; then say "recreating the venv (broken or not Python $PY_VERSION)"; fi
  rm -rf "$VENV"
  "$UV" venv -q "$MANAGED" --python "$PY_VERSION" "$VENV" || die "could not create the venv"
fi

# ------------------------------------------------------------------ install
if [ -n "$LOCAL_SRC" ] && [ "$EDITABLE" = 1 ]; then set -- -e "$LOCAL_SRC"
elif [ -n "$LOCAL_SRC" ]; then set -- "$LOCAL_SRC" --reinstall-package ledgerstitch   # same version, new code
else set -- "$SOURCE"; fi
if [ -n "$EXTRAS" ]; then
  OLDIFS="$IFS"; IFS='
'
  for x in $EXTRAS; do [ -n "$x" ] && set -- "$@" "$x"; done
  IFS="$OLDIFS"
fi
say "installing LedgerStitch from $SRC_DESC"
"$UV" pip install -q --python "$VPY" --upgrade "$@" || die "package installation failed"
[ -x "$VENV/bin/ledgerstitch" ] || die "the ledgerstitch command is missing from the venv"
VERSION="$("$VPY" -c 'import ledgerstitch; print(ledgerstitch.__version__)')" || die "ledgerstitch doesn't import"

# ------------------------------------------------------------------ launchers
NEW_PATHS=""
add_path() { NEW_PATHS="$NEW_PATHS$1
"; }

mkdir -p "$BIN_DIR"
SHIM="$BIN_DIR/ledgerstitch"
if [ -e "$SHIM" ] && ! is_ours "$SHIM"; then
  warn "$SHIM exists and wasn't created by this installer; not replacing it"
else
  cat > "$SHIM" <<EOF
#!/bin/sh
# LedgerStitch command ($MARKER; removed by install.sh --uninstall)
exec "$VENV/bin/ledgerstitch" "\$@"
EOF
  chmod 755 "$SHIM"; add_path "$SHIM"
fi

if [ "$NO_APP" != 1 ] && [ "$OS" = Darwin ]; then
  if [ -e "$APP_PATH" ] && ! is_ours "$APP_PATH"; then
    warn "$APP_PATH exists and isn't ours (e.g. the DMG app); not replacing it"
  else
    rm -rf "$APP_PATH"
    mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources"
    ICON_KEY=""
    if [ -n "$LOCAL_SRC" ] && [ -f "$LOCAL_SRC/packaging/LedgerStitch.icns" ]; then
      cp "$LOCAL_SRC/packaging/LedgerStitch.icns" "$APP_PATH/Contents/Resources/LedgerStitch.icns"
      ICON_KEY="<key>CFBundleIconFile</key><string>LedgerStitch</string>"
    fi
    cat > "$APP_PATH/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>LedgerStitch</string>
  <key>CFBundleDisplayName</key><string>LedgerStitch</string>
  <key>CFBundleIdentifier</key><string>com.ledgerstitch.source-install</string>
  <key>CFBundleExecutable</key><string>LedgerStitch</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  $ICON_KEY
</dict>
</plist>
EOF
    cat > "$APP_PATH/Contents/MacOS/LedgerStitch" <<EOF
#!/bin/sh
# LedgerStitch app wrapper ($MARKER; removed by install.sh --uninstall)
exec "$VENV/bin/ledgerstitch-desktop" "\$@"
EOF
    chmod 755 "$APP_PATH/Contents/MacOS/LedgerStitch"
    add_path "$APP_PATH"
  fi
elif [ "$NO_APP" != 1 ] && [ "$OS" = Linux ]; then
  mkdir -p "$APPS_DIR"
  if [ -e "$APP_PATH" ] && ! is_ours "$APP_PATH"; then
    warn "$APP_PATH exists and wasn't created by this installer; not replacing it"
  else
    ICON="$("$VPY" -c 'import os, ledgerstitch; print(os.path.join(os.path.dirname(ledgerstitch.__file__), "web", "static", "logo-mark.svg"))')"
    cat > "$APP_PATH" <<EOF
[Desktop Entry]
# $MARKER; removed by install.sh --uninstall
Type=Application
Name=LedgerStitch
Comment=Private, local expense consolidation
Exec="$VENV/bin/ledgerstitch-desktop"
Icon=$ICON
Terminal=false
Categories=Office;Finance;
EOF
    chmod 644 "$APP_PATH"; add_path "$APP_PATH"
    if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true; fi
  fi
fi

# launchers from a previous run that now live elsewhere (e.g. a different --bin-dir)
manifest_paths | while IFS= read -r old; do
  [ -n "$old" ] || continue
  case "
$NEW_PATHS" in *"
$old
"*) ;; *) remove_ours "$old" ;; esac
done

# ------------------------------------------------------------------ manifest
{
  echo "# LedgerStitch install manifest (written by install.sh; used by --uninstall)"
  echo "version=$VERSION"
  echo "python=$PY_VERSION"
  echo "source=$SRC_DESC"
  echo "extras=$(printf '%s' "$EXTRAS" | tr '\n' ' ' | sed 's/^ //')"
  echo "venv=$VENV"
  echo "installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%s' "$NEW_PATHS" | while IFS= read -r p; do if [ -n "$p" ]; then echo "path=$p"; fi; done
} > "$MANIFEST"

# ------------------------------------------------------------------ done
say "LedgerStitch $VERSION installed in $PREFIX (your data stays in $DATA_DIR)"
if ! on_path "$BIN_DIR"; then
  echo "PATH hint: $BIN_DIR is not on your PATH. Add it, e.g.:"
  echo "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.profile   (zsh: ~/.zprofile)"
fi
if [ "$OS" = Darwin ] && [ "$NO_APP" != 1 ] && is_ours "$APP_PATH"; then
  echo "Done. Start LedgerStitch: ledgerstitch  (or open \"$APP_PATH\")"
else
  echo "Done. Start LedgerStitch: ledgerstitch"
fi
