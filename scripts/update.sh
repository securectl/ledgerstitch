#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# LedgerStitch updater: update an existing install by changing only what changed.
#
#   sh scripts/update.sh --check            # report only (exit 10 when an update is available)
#   sh scripts/update.sh                    # apply (asks nothing; --check first if unsure)
#   sh scripts/update.sh --docker|--native|--dev [--ref v1.3.0] [--refresh-base] [--no-backup]
#
# Install kinds (detected; pass one of the flags when more than one is present):
#   --native  the one-line installer (install-manifest.txt). Same dependencies -> reinstall only
#             LedgerStitch (uv pip install --no-deps, seconds); changed -> re-run install.sh.
#   --docker  docker/docker-compose.yml from this checkout. The image carries labels with the
#             git tree hash of ledgerstitch/ and a hash of the dependency list + Dockerfile, so:
#               labels match            -> no build
#               only the code changed   -> rebuild; the dependency layer comes from the cache
#               dependencies changed    -> rebuild the dependency layer too
#             The Python base image is pinned by digest. A newer upstream digest (security
#             patches) is reported, and --refresh-base rebuilds on it. Pulled images (caddy,
#             ollama) are compared by digest with the registry and pulled only when they differ.
#             The data volume is backed up (an encrypted-at-rest copy) before the app container is
#             recreated; volumes are never otherwise touched. Only running services are restarted.
#   --dev     an editable checkout: git pull, and reinstall dependencies only if pyproject.toml
#             changed them.
#
# The git step fast-forwards to the upstream branch (or checks out --ref) and refuses when
# tracked files have local changes (docker/docker-compose.yml and docker/Caddyfile excepted;
# better: put local settings in docker/docker-compose.override.yml). --force overrides.
# Network: git fetch, registry digest lookups and (if needed) package downloads. Nothing else.
set -eu

say()  { printf '==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() { sed -n '3,27p' "$0" | sed 's/^# \{0,1\}//'; }

# ------------------------------------------------------------------ arguments
MODE=""; CHECK=0; REF=""; FORCE=0; BACKUP=1; REFRESH_BASE=0
APP_IMAGE="${LEDGERSTITCH_IMAGE:-ledgerstitch:community}"
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --check) CHECK=1 ;;
    --docker) MODE=docker ;;
    --native) MODE=native ;;
    --dev) MODE=dev ;;
    --ref) if [ $# -lt 2 ] || [ -z "$2" ]; then die "--ref needs a value"; fi; REF="$2"; shift ;;
    --ref=*) REF="${1#*=}" ;;
    --force) FORCE=1 ;;
    --no-backup) BACKUP=0 ;;
    --refresh-base) REFRESH_BASE=1 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

ROOT="${LEDGERSTITCH_CHECKOUT:-$(cd "$(dirname "$0")/.." && pwd -P)}"
DOCKER_DIR="$ROOT/docker"
UPDATES=0                       # set to 1 when something is (or would be) updated

# ------------------------------------------------------------------ helpers
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
  else shasum -a 256 | cut -d' ' -f1; fi
}

# The [project] dependency list of a pyproject.toml on stdin, one normalized requirement per line.
deps_lines() {
  awk '
    /^\[/ { inproj = ($0 == "[project]"); indeps = 0; next }
    inproj && /^dependencies[ \t]*=[ \t]*\[/ { indeps = 1; sub(/^[^[]*\[/, "") }
    indeps {
      line = $0; sub(/#.*/, "", line)
      while (match(line, /"[^"]*"/)) { print substr(line, RSTART + 1, RLENGTH - 2); line = substr(line, RSTART + RLENGTH) }
      if ($0 ~ /\]/) indeps = 0
    }' | tr -d ' ' | tr 'A-Z_' 'a-z-' | sort
}

project_version() { awk '/^\[/{p=($0=="[project]")} p && /^version[ \t]*=/{gsub(/.*=[ \t]*"|".*/,""); print; exit}'; }

in_git() { git -C "$1" rev-parse --git-dir >/dev/null 2>&1; }

dirty_files() {  # tracked files with local changes, except the local-settings files
  git -C "$1" status --porcelain --untracked-files=no -- . \
    ':(exclude)docker/docker-compose.yml' ':(exclude)docker/Caddyfile' ':(exclude)docker/docker-compose.override.yml'
}

# git_target DIR: sets TARGET_REV (the commit to update to) and TARGET_DESC
git_target() {
  if [ -n "$(dirty_files "$1")" ]; then
    if [ "$FORCE" = 1 ]; then warn "$1 has local changes; continuing because of --force"
    else
      dirty_files "$1" >&2
      die "$1 has local changes to tracked files (above). Commit or stash them, or pass --force."
    fi
  fi
  if [ -n "$REF" ]; then
    git -C "$1" fetch --quiet --tags 2>/dev/null || warn "git fetch failed (offline?); using the refs already here"
    TARGET_REV="$(git -C "$1" rev-parse --verify --quiet "$REF^{commit}")" || die "unknown --ref: $REF"
    TARGET_DESC="$REF"
  elif UP="$(git -C "$1" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"; then
    if ! git -C "$1" fetch --quiet 2>/dev/null; then
      [ "$CHECK" = 1 ] || die "git fetch failed (offline?); nothing was changed"
      warn "git fetch failed (offline?); comparing with the last fetched $UP"
    fi
    TARGET_REV="$(git -C "$1" rev-parse "$UP")"
    TARGET_DESC="$UP"
  else
    warn "the current branch has no upstream; checking the current commit only (or pass --ref)"
    TARGET_REV="$(git -C "$1" rev-parse HEAD)"
    TARGET_DESC="HEAD"
  fi
  HEAD_REV="$(git -C "$1" rev-parse HEAD)"
  if [ "$HEAD_REV" = "$TARGET_REV" ]; then
    note "code: at $TARGET_DESC ($(printf '%.12s' "$TARGET_REV"))"
  else
    note "code: $(printf '%.12s' "$HEAD_REV") -> $TARGET_DESC ($(printf '%.12s' "$TARGET_REV")), $(git -C "$1" rev-list --count "$HEAD_REV..$TARGET_REV" 2>/dev/null || echo '?') new commit(s)"
  fi
}

git_apply() {  # git_apply DIR: move the checkout to TARGET_REV
  [ "$(git -C "$1" rev-parse HEAD)" = "$TARGET_REV" ] && return 0
  if [ -n "$REF" ]; then git -C "$1" checkout --quiet --detach "$TARGET_REV" || die "git checkout $REF failed"
  else git -C "$1" merge --ff-only --quiet "$TARGET_REV" ||
    die "can't fast-forward $1 to $TARGET_DESC (local commits, or local edits that upstream also changed)"
  fi
  say "checkout updated to $TARGET_DESC"
}

# ------------------------------------------------------------------ docker
dc() { (cd "$DOCKER_DIR" && docker compose "$@"); }

label() {  # label IMAGE KEY: the label's value, or "" when missing
  v="$(docker image inspect --format "{{index .Config.Labels \"$2\"}}" "$1" 2>/dev/null)" || v=""
  case "$v" in "<no value>"|unknown) v="" ;; esac
  printf '%s' "$v"
}

remote_digest() {  # the registry's current digest for an image reference ("" if it can't be checked)
  docker buildx imagetools inspect "$1" 2>/dev/null | awk '/^Digest:/ { print $2; exit }' || true
}

# app_plan CUR_SRC CUR_DEPS NEW_SRC NEW_DEPS HAVE_IMAGE -> none | first | unlabeled | code | deps
app_plan() {
  if [ "$5" != 1 ]; then echo first
  elif [ -z "$1" ] || [ -z "$2" ]; then echo unlabeled
  elif [ "$2" != "$4" ]; then echo deps
  elif [ "$1" != "$3" ]; then echo code
  else echo none; fi
}

compose_images() {  # images the compose file pulls (not the one it builds)
  { dc config --images 2>/dev/null || sed -n 's/^[[:space:]]*image:[[:space:]]*//p' "$DOCKER_DIR/docker-compose.yml"; } |
    sed 's/[[:space:]]*#.*//; s/^["'\'']//; s/["'\'']$//' | grep -v -e '^$' -e "^${APP_IMAGE%%:*}:" | sort -u
}

backup_volume() {
  [ "$BACKUP" = 1 ] || { note "backup: skipped (--no-backup)"; return 0; }
  cid="$(dc ps -a -q ledgerstitch 2>/dev/null | head -n 1)"
  vol=""
  if [ -n "$cid" ]; then
    vol="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$cid" 2>/dev/null || true)"
  fi
  if [ -z "$vol" ]; then
    vol="$(docker volume ls -q --filter label=com.docker.compose.project=ledgerstitch \
           --filter label=com.docker.compose.volume=ledgerstitch-data 2>/dev/null | head -n 1)"
  fi
  [ -n "$vol" ] || { note "backup: no data volume yet, nothing to back up"; return 0; }
  dir="${LEDGERSTITCH_BACKUP_DIR:-$HOME/LedgerStitch-docker-backups}"
  mkdir -p "$dir" && chmod 700 "$dir"
  out="$dir/ledgerstitch-data-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  # read-only mount, no network, the current image's tar; the vaults inside stay encrypted
  if (umask 077 && docker run --rm --network none -v "$vol:/data:ro" --entrypoint tar "$APP_IMAGE" \
        -czf - -C /data . > "$out"); then
    say "backed up volume $vol to $out"
  else
    rm -f "$out"; die "backing up volume $vol failed; nothing was changed (or pass --no-backup)"
  fi
}

update_docker() {
  command -v docker >/dev/null 2>&1 || die "docker not found"
  docker info >/dev/null 2>&1 || die "can't talk to the Docker daemon (is Docker running?)"
  in_git "$ROOT" || die "$ROOT is not a git checkout; the Docker update needs one (git clone, then docker compose)"
  say "LedgerStitch (Docker) in $ROOT"
  git_target "$ROOT"

  new_src="$(git -C "$ROOT" rev-parse "$TARGET_REV:ledgerstitch")"
  [ -z "$(dirty_files "$ROOT")" ] || new_src="$new_src-dirty"
  new_deps="$({ git -C "$ROOT" show "$TARGET_REV:pyproject.toml" | deps_lines
               git -C "$ROOT" rev-parse "$TARGET_REV:docker/Dockerfile"; } | sha256)"
  new_pin="$(git -C "$ROOT" show "$TARGET_REV:docker/Dockerfile" | sed -n 's/^ARG PYTHON_IMAGE=//p' | head -n 1)"
  new_version="$(git -C "$ROOT" show "$TARGET_REV:pyproject.toml" | project_version)"

  have=0; docker image inspect "$APP_IMAGE" >/dev/null 2>&1 && have=1
  cur_src="$(label "$APP_IMAGE" io.ledgerstitch.src-sha)"
  cur_deps="$(label "$APP_IMAGE" io.ledgerstitch.deps-sha)"
  cur_pin="$(label "$APP_IMAGE" io.ledgerstitch.base-pin)"
  cur_base="$(label "$APP_IMAGE" org.opencontainers.image.base.name)"
  cur_version="$(label "$APP_IMAGE" org.opencontainers.image.version)"
  plan="$(app_plan "$cur_src" "$cur_deps" "$new_src" "$new_deps" "$have")"

  # base image: keep a base refreshed earlier unless the checkout pins a different one
  base_tag="${new_pin%@*}"
  if [ -n "$cur_base" ] && [ "$cur_pin" = "$new_pin" ]; then build_base="$cur_base"; else build_base="$new_pin"; fi
  upstream="$(remote_digest "$base_tag")"
  base_newer=0
  if [ -z "$upstream" ]; then note "base image: couldn't look up $base_tag upstream (offline, or no docker buildx)"
  elif [ "$upstream" != "${build_base#*@}" ]; then base_newer=1; fi

  case "$plan" in
    first)     note "app image: not built yet -> build ($new_version)" ;;
    unlabeled) note "app image: built without update labels -> one full rebuild" ;;
    deps)      note "app image: dependencies or Dockerfile changed -> rebuild incl. dependency layer (${cur_version:-?} -> $new_version)" ;;
    code)      note "app image: app code changed -> fast rebuild, dependency layer cached (${cur_version:-?} -> $new_version)" ;;
    none)      note "app image: up to date ($new_version)" ;;
  esac
  pull_base=0
  if [ "$base_newer" = 1 ]; then
    if [ "$REFRESH_BASE" = 1 ]; then
      note "base image: newer $base_tag upstream (security patches) -> rebuild on $upstream"
      build_base="$base_tag@$upstream"; pull_base=1; [ "$plan" = none ] && plan=base
    else
      note "base image: newer $base_tag upstream ($upstream); rebuild on it with --refresh-base"
    fi
  else
    [ -z "$upstream" ] || note "base image: $base_tag is current"
  fi
  [ "$plan" = none ] || UPDATES=1

  pulls=""
  for img in $(compose_images); do
    if ! docker image inspect "$img" >/dev/null 2>&1; then note "$img: not pulled here, skipped"; continue; fi
    remote="$(remote_digest "$img")"
    if [ -z "$remote" ]; then note "$img: couldn't check the registry"; continue; fi
    if docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$img" | grep -q "@$remote\$"; then
      note "$img: up to date"
    else
      note "$img: newer image in the registry -> pull"; pulls="$pulls $img"; UPDATES=1
    fi
  done

  if [ "$CHECK" = 1 ]; then
    if [ "$UPDATES" = 1 ] || [ "$HEAD_REV" != "$TARGET_REV" ]; then say "updates available (run without --check to apply)"; exit 10; fi
    say "everything is up to date"; exit 0
  fi
  if [ "$UPDATES" = 0 ] && [ "$HEAD_REV" = "$TARGET_REV" ]; then say "everything is up to date; nothing to do"; return 0; fi

  git_apply "$ROOT"
  if [ "$plan" != none ]; then
    [ "$have" = 1 ] && backup_volume
    say "building $APP_IMAGE ($plan)"
    set -- --build-arg "PYTHON_IMAGE=$build_base" \
           --build-arg "LEDGERSTITCH_REVISION=$TARGET_REV" --build-arg "LEDGERSTITCH_VERSION=$new_version" \
           --build-arg "LEDGERSTITCH_DEPS_SHA=$new_deps" --build-arg "LEDGERSTITCH_SRC_SHA=$new_src" \
           --build-arg "LEDGERSTITCH_BASE_PIN=$new_pin"
    [ "$pull_base" = 1 ] && set -- "$@" --pull
    dc build "$@" ledgerstitch || die "docker compose build failed; the running containers were not changed"
  fi
  for img in $pulls; do say "pulling $img"; docker pull --quiet "$img" >/dev/null || warn "pull of $img failed"; done

  running="$(dc ps --services --status running 2>/dev/null | tr '\n' ' ')"
  if [ -n "${running% }" ]; then
    say "recreating changed services (running: $running)"
    # shellcheck disable=SC2086 # word splitting intended: one argument per service
    dc up -d --no-deps $running
  else
    say "no services are running; start them with: cd docker && docker compose up -d"
  fi
  say "done"
}

# ------------------------------------------------------------------ native (install.sh)
native_prefix() {
  if [ -n "${LEDGERSTITCH_PREFIX:-}" ]; then printf '%s' "$LEDGERSTITCH_PREFIX"; return; fi
  case "$(uname -s)" in
    Darwin) printf '%s' "$HOME/Library/Application Support/LedgerStitch/app" ;;
    *) printf '%s' "${XDG_DATA_HOME:-$HOME/.local/share}/ledgerstitch/app" ;;
  esac
}

find_uv() {
  if [ -n "${UV:-}" ] && [ -x "$UV" ]; then return 0; fi
  UV="$(command -v uv 2>/dev/null || true)"; [ -n "$UV" ] && return 0
  for c in "${XDG_BIN_HOME:-$HOME/.local/bin}/uv" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$c" ]; then UV="$c"; return 0; fi
  done
  return 1
}

manifest_get() { sed -n "s/^$1=//p" "$MANIFEST" | head -n 1; }

manifest_set() {  # manifest_set KEY VALUE (adds or replaces one line)
  tmp="$MANIFEST.tmp.$$"
  { grep -v "^$1=" "$MANIFEST" || true; echo "$1=$2"; } > "$tmp" && mv "$tmp" "$MANIFEST"
}

update_native() {
  PREFIX="$(native_prefix)"; MANIFEST="$PREFIX/install-manifest.txt"; VPY="$PREFIX/venv/bin/python"
  [ -f "$MANIFEST" ] || die "no installer manifest at $MANIFEST (installed another way? see docs/INSTALL.md, Updating)"
  [ -x "$VPY" ] || die "the venv in $PREFIX is missing or broken: re-run install.sh to repair it"
  SRC="$(manifest_get source)"
  say "LedgerStitch $(manifest_get version) (installer) in $PREFIX, source: $SRC"
  src_tree=""
  if [ -d "$SRC" ]; then
    if in_git "$SRC"; then
      git_target "$SRC"
      src_tree="$(git -C "$SRC" rev-parse "$TARGET_REV:ledgerstitch")"
    fi
    if [ "$CHECK" = 0 ] && in_git "$SRC"; then git_apply "$SRC"; fi
    # dependencies: the installed package metadata against the checkout's pyproject.toml
    if [ "$CHECK" = 1 ] && in_git "$SRC" && [ "$HEAD_REV" != "$TARGET_REV" ]; then
      cmp_dir="$(mktemp -d)"; git -C "$SRC" show "$TARGET_REV:pyproject.toml" > "$cmp_dir/pyproject.toml"
    else
      cmp_dir="$SRC"
    fi
    plan_json="$("$VPY" -m ledgerstitch update --compare-source "$cmp_dir")" || die "couldn't compare dependencies"
    [ "$cmp_dir" = "$SRC" ] || rm -rf "$cmp_dir"
    case "$plan_json" in *'"action": "no-deps"'*) action=no-deps ;; *) action=full ;; esac
    if [ "$action" = no-deps ] && [ -n "$src_tree" ] && [ "$(manifest_get src_sha)" = "$src_tree" ]; then
      action=none
    fi
  else
    action=full   # a PyPI or git URL source: the dependencies can't be compared without resolving them
  fi
  case "$action" in
    none)    note "up to date"; [ "$CHECK" = 1 ] && exit 0; return 0 ;;
    no-deps) note "same dependencies -> reinstall only LedgerStitch (no dependency resolution)" ;;
    full)    note "dependencies changed (or can't be compared) -> full upgrade through install.sh" ;;
  esac
  if [ "$CHECK" = 1 ]; then say "updates available (run without --check to apply)"; exit 10; fi

  if [ "$action" = no-deps ]; then
    find_uv || die "uv not found (the installer put it in ~/.local/bin); re-run install.sh"
    "$UV" pip install -q --python "$VPY" --no-deps --reinstall-package ledgerstitch "$SRC" ||
      die "reinstall failed"
    manifest_set version "$("$VPY" -c 'import ledgerstitch; print(ledgerstitch.__version__)')"
  else
    installer="$ROOT/install.sh"; [ -d "$SRC" ] && [ -f "$SRC/install.sh" ] && installer="$SRC/install.sh"
    [ -f "$installer" ] || die "install.sh not found next to this script"
    set -- --prefix "$PREFIX"
    for p in $(sed -n 's/^path=//p' "$MANIFEST" | tr ' ' '\037'); do
      p="$(printf '%s' "$p" | tr '\037' ' ')"
      case "$p" in
        */ledgerstitch) set -- "$@" --bin-dir "$(dirname "$p")" ;;
        *.app|*.desktop) set -- "$@" --apps-dir "$(dirname "$p")" ;;
      esac
    done
    grep -Eq '^path=.*\.(app|desktop)$' "$MANIFEST" || set -- "$@" --no-app
    for x in $(manifest_get extras); do set -- "$@" --extra "$x"; done
    sh "$installer" "$@" "$SRC" || die "install.sh failed"
  fi
  [ -z "$src_tree" ] || manifest_set src_sha "$src_tree"
  say "LedgerStitch $(manifest_get version) is installed; restart the app to use it"
}

# ------------------------------------------------------------------ dev checkout
update_dev() {
  in_git "$ROOT" || die "$ROOT is not a git checkout"
  say "LedgerStitch dev checkout $ROOT"
  git_target "$ROOT"
  old_deps="$(git -C "$ROOT" show HEAD:pyproject.toml | deps_lines)"
  new_deps="$(git -C "$ROOT" show "$TARGET_REV:pyproject.toml" | deps_lines)"
  if [ "$old_deps" = "$new_deps" ]; then note "dependencies: unchanged"; else note "dependencies: changed -> reinstall"; fi
  if [ "$CHECK" = 1 ]; then
    if [ "$HEAD_REV" != "$TARGET_REV" ]; then say "updates available"; exit 10; fi
    say "up to date"; exit 0
  fi
  git_apply "$ROOT"
  [ "$old_deps" = "$new_deps" ] && { say "done (editable install: nothing to reinstall)"; return 0; }
  py="${LEDGERSTITCH_DEV_PYTHON:-$ROOT/.venv/bin/python}"
  [ -x "$py" ] || die "no Python at $py; reinstall with: pip install -e '${ROOT}[dev]' (or set LEDGERSTITCH_DEV_PYTHON)"
  if find_uv; then "$UV" pip install -q --python "$py" -e "${ROOT}[dev]"
  else "$py" -m pip install -q -e "${ROOT}[dev]"; fi
  say "done (dependencies reinstalled)"
}

# ------------------------------------------------------------------ main
if [ -z "$MODE" ]; then
  found=""
  [ -f "$(native_prefix)/install-manifest.txt" ] && found="$found native"
  if command -v docker >/dev/null 2>&1 && docker image inspect "$APP_IMAGE" >/dev/null 2>&1; then found="$found docker"; fi
  case "$found" in
    " native") MODE=native ;;
    " docker") MODE=docker ;;
    "") if in_git "$ROOT"; then MODE=dev; else die "no LedgerStitch install found (see --help)"; fi ;;
    *) die "found more than one install ($found ); pass --native or --docker" ;;
  esac
fi
case "$MODE" in
  docker) update_docker ;;
  native) update_native ;;
  dev) update_dev ;;
esac
