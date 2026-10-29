# SPDX-License-Identifier: AGPL-3.0-only
"""Update check (ledgerstitch.updater, `ledgerstitch update`) and scripts/update.sh.

No network and no Docker: the release API is a fake opener, `docker` and `uv` are logging stubs
on PATH, and the "upstream" is a local bare git repository."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from ledgerstitch import __version__, updater
from ledgerstitch.cli import main as cli_main

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "update.sh"
GIT = shutil.which("git")
SHELLS = [s for s in ("sh", "dash") if shutil.which(s)]
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell script")


# =========================================================================== updater.py
@pytest.mark.parametrize("a,b", [("1.2.0", "1.10.0"), ("1.2", "1.2.1"), ("1.3.0rc1", "1.3.0"),
                                 ("1.3.0a2", "1.3.0b1"), ("1.3.0.dev1", "1.3.0a1"), ("v1.2.0", "v1.2.1")])
def test_version_order(a, b):
    assert updater.parse_version(a) < updater.parse_version(b)
    assert updater.is_newer(b, a) and not updater.is_newer(a, b)


def test_version_equal_forms_and_garbage():
    assert updater.parse_version("v1.2.0") == updater.parse_version("1.2")
    with pytest.raises(ValueError):
        updater.parse_version("latest")


def test_normalize_requirement():
    n = updater.normalize_requirement
    assert n("Python_Multipart >= 0.0.9") == n("python-multipart>=0.0.9") == "python-multipart>=0.0.9"
    assert n('tomli>=2 ; python_version < "3.11"') == 'tomli>=2;python_version < "3.11"'


def test_installed_requirements_drop_extras():
    reqs = updater.installed_requirements()
    assert reqs is not None and "duckdb>=1.1" in reqs
    assert not any("pytest" in r or "pyinstaller" in r for r in reqs)


def _checkout(tmp_path, deps):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ledgerstitch"\nversion = "9.9.9"\ndependencies = [\n'
        + "".join(f'  "{d}",\n' for d in deps) + "]\n")
    return tmp_path


def test_compare_source_same_deps_is_no_deps(tmp_path):
    src = _checkout(tmp_path, ["duckdb>=1.1", "FastAPI>=0.110"])
    r = updater.compare_source(src, installed=frozenset({"duckdb>=1.1", "fastapi>=0.110"}))
    assert r["action"] == "no-deps" and not r["deps_changed"] and r["target_version"] == "9.9.9"


def test_compare_source_changed_deps_is_full(tmp_path):
    src = _checkout(tmp_path, ["duckdb>=1.2", "fastapi>=0.110", "rich>=13"])
    r = updater.compare_source(src, installed=frozenset({"duckdb>=1.1", "fastapi>=0.110"}))
    assert r["action"] == "full" and r["added"] == ["duckdb>=1.2", "rich>=13"] and r["removed"] == ["duckdb>=1.1"]


def test_compare_source_against_this_checkout_matches_installed():
    # the test environment is installed from this very pyproject.toml
    assert updater.compare_source(REPO)["action"] == "no-deps"


def test_compare_source_rejects_non_checkout(tmp_path):
    with pytest.raises(ValueError):
        updater.compare_source(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other"\n')
    with pytest.raises(ValueError):
        updater.compare_source(tmp_path)


class FakeOpener:
    def __init__(self, body=None, exc=None):
        self.body, self.exc, self.requests = body, exc, []

    def __call__(self, req, timeout):
        self.requests.append(req)
        if self.exc:
            raise self.exc
        return self.body


def test_fetch_latest_sends_nothing_identifying():
    op = FakeOpener(json.dumps({"tag_name": "v1.3.0", "html_url": "https://github.com/x/releases/tag/v1.3.0",
                                "published_at": "2026-11-01T00:00:00Z"}).encode())
    rel = updater.fetch_latest(opener=op)
    assert (rel.version, rel.tag) == ("1.3.0", "v1.3.0")
    req = op.requests[0]
    assert req.full_url == updater.RELEASES_API and req.get_method() == "GET" and req.data is None
    headers = {k.lower(): v for k, v in req.header_items()}
    assert set(headers) == {"accept", "user-agent"} and __version__ not in headers["user-agent"]


@pytest.mark.parametrize("exc,msg", [
    (urllib.error.URLError("nodename nor servname provided"), "are you offline"),
    (TimeoutError("timed out"), "are you offline"),
    (urllib.error.HTTPError(updater.RELEASES_API, 403, "rate limited", {}, None), "rate limit"),
])
def test_fetch_latest_failures_are_messages(exc, msg):
    with pytest.raises(updater.UpdateCheckError, match=msg):
        updater.fetch_latest(opener=FakeOpener(exc=exc))


def test_fetch_latest_bad_reply_and_plain_http():
    with pytest.raises(updater.UpdateCheckError, match="unexpected reply"):
        updater.fetch_latest(opener=FakeOpener(b'{"tag_name": "nightly"}'))
    with pytest.raises(updater.UpdateCheckError, match="non-HTTPS"):
        updater.fetch_latest("http://example.invalid/latest", opener=FakeOpener(b"{}"))


def test_check_reports_update_and_offline(monkeypatch):
    op = FakeOpener(json.dumps({"tag_name": "v99.0.0"}).encode())
    rep = updater.check(opener=op, kind_info=("pip", {}))
    assert rep.update_available is True and rep.latest.version == "99.0.0" and rep.error is None
    rep = updater.check(opener=FakeOpener(exc=urllib.error.URLError("down")), kind_info=("pip", {}))
    assert rep.update_available is None and "offline" in rep.error
    off = FakeOpener(exc=AssertionError("no network when offline"))
    rep = updater.check(offline=True, opener=off, kind_info=("pip", {}))
    assert rep.latest is None and off.requests == [] and rep.how == ["pip install --upgrade ledgerstitch"]


def test_install_kind(tmp_path):
    prefix = tmp_path / "app"
    (prefix / "venv" / "bin").mkdir(parents=True)
    (prefix / "install-manifest.txt").write_text(
        "# manifest\nversion=1.2.0\nsource=/src/ledgerstitch\nextras=\npath=/home/u/.local/bin/ledgerstitch\n")
    kind, info = updater.install_kind(executable=str(prefix / "venv" / "bin" / "python"), frozen=False, env={},
                                      dockerenv=tmp_path / "nope")
    assert kind == "installer" and info["source"] == "/src/ledgerstitch" and info["path"] == ["/home/u/.local/bin/ledgerstitch"]
    assert updater.install_kind(frozen=True)[0] == "desktop"
    assert updater.install_kind(frozen=False, env={"LEDGERSTITCH_IN_DOCKER": "1"}, dockerenv=tmp_path / "nope")[0] == "docker"
    how = updater.how_to_update("installer", {"source": str(tmp_path)})
    assert "scripts/update.sh" in how[0]
    assert "install.sh 'ledgerstitch==1.2.0'" in updater.how_to_update("installer", {"source": "ledgerstitch==1.2.0"})[0]
    assert updater.RELEASES_PAGE in updater.how_to_update("desktop", {})[0]


def test_cli_update(monkeypatch, capsys):
    monkeypatch.setattr(updater, "fetch_latest", lambda url, opener=None: updater.Release("99.0.0", "v99.0.0", "https://r"))
    assert cli_main(["update"]) == 0
    out = capsys.readouterr().out
    assert "Update available: 99.0.0" in out and "To update:" in out
    assert cli_main(["update", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["update_available"] is True and d["installed"] == __version__

    def offline(url, opener=None):
        raise updater.UpdateCheckError("couldn't reach api.github.com (down); are you offline? Nothing was changed.")
    monkeypatch.setattr(updater, "fetch_latest", offline)
    assert cli_main(["update"]) == 1
    cap = capsys.readouterr()
    assert "are you offline" in cap.err and "To update:" in cap.out


def test_cli_update_offline_flag_makes_no_request(monkeypatch, capsys):
    monkeypatch.setattr(updater, "fetch_latest", lambda *a, **k: pytest.fail("network used with --offline"))
    assert cli_main(["update", "--offline"]) == 0
    assert "To update:" in capsys.readouterr().out


def test_cli_compare_source(capsys):
    assert cli_main(["update", "--compare-source", str(REPO)]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "no-deps"


# =========================================================================== Dockerfile
def test_dockerfile_installs_dependencies_before_copying_the_code():
    df = (REPO / "docker" / "Dockerfile").read_text()
    assert re.search(r"^ARG PYTHON_IMAGE=python:[\w.-]+@sha256:[0-9a-f]{64}$", df, re.M)
    deps = df.index("pip install -r /tmp/requirements.txt")
    assert df.index("COPY ledgerstitch ./ledgerstitch") > deps
    assert "pip install --no-deps ." in df and "COPY --from=reqs /requirements.txt" in df
    assert df.count("FROM ${PYTHON_IMAGE}") == 3
    for lbl in ("io.ledgerstitch.deps-sha", "io.ledgerstitch.src-sha", "io.ledgerstitch.base-pin",
                "org.opencontainers.image.revision", "org.opencontainers.image.version"):
        assert lbl in df
    assert df.rstrip().endswith('io.ledgerstitch.base-pin="${LEDGERSTITCH_BASE_PIN}"')   # labels last


# =========================================================================== scripts/update.sh
def test_script_syntax():
    for sh in SHELLS:
        subprocess.run([sh, "-n", str(SCRIPT)], check=True)
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", "-s", "sh", str(SCRIPT)], check=True)


FAKE_DOCKER = r'''#!/bin/sh
F="$FAKE_DOCKER"
echo "$*" >> "$F/log"
san() { printf '%s' "$1" | tr '/:@' '___'; }
case "$1" in
  info) exit 0 ;;
  image)
    shift 2
    if [ "$1" = "--format" ]; then fmt="$2"; img="$3"; else fmt=""; img="$1"; fi
    d="$F/images/$(san "$img")"; [ -d "$d" ] || exit 1
    case "$fmt" in
      "") echo '[{}]' ;;
      *RepoDigests*) cat "$d/repodigests" 2>/dev/null ;;
      *Labels*) key=$(printf '%s' "$fmt" | sed 's/.*Labels "\([^"]*\)".*/\1/')
                if [ -f "$d/labels/$key" ]; then cat "$d/labels/$key"; else echo "<no value>"; fi ;;
    esac ;;
  buildx) f="$F/remote/$(san "$4")"; [ -f "$f" ] || exit 1
          printf 'Name:      %s\nMediaType: application/vnd.oci.image.index.v1+json\nDigest:    %s\n' "$4" "$(cat "$f")" ;;
  compose) shift
    case "$*" in
      "config --images") cat "$F/compose_images" ;;
      "ps --services --status running") cat "$F/running" 2>/dev/null || true ;;
      "ps -a -q ledgerstitch") cat "$F/cid" 2>/dev/null || true ;;
      build*) [ -f "$F/fail_build" ] && exit 1; exit 0 ;;
      *) exit 0 ;;
    esac ;;
  inspect) cat "$F/volume" 2>/dev/null || true ;;
  volume) exit 0 ;;
  run) [ -f "$F/fail_run" ] && exit 1; printf 'TARDATA' ;;
  pull) exit 0 ;;
esac
'''

FAKE_UV = '#!/bin/sh\necho "$*" >> "$FAKE_DOCKER/uv.log"\n'
FAKE_INSTALLER = ('#!/bin/sh\necho "$*" >> "$FAKE_DOCKER/install.log"\n'
                  'm="$2/install-manifest.txt"; echo "version=9.9.9" > "$m"\n')

PIN = "python:3.14-slim@sha256:" + "a" * 64


def _git(cwd, *args):
    return subprocess.run([GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Env:
    """A checkout cloned from a local "upstream", fake docker/uv on PATH."""

    def __init__(self, tmp: Path, shell: str = "sh"):
        self.tmp, self.shell = tmp, shell
        self.fake = tmp / "fake"
        for d in ("images", "remote", "bin"):
            (self.fake / d).mkdir(parents=True)
        (self.fake / "bin" / "docker").write_text(FAKE_DOCKER)
        (self.fake / "bin" / "uv").write_text(FAKE_UV)
        for f in ("docker", "uv"):
            (self.fake / "bin" / f).chmod(0o755)
        (self.fake / "compose_images").write_text("caddy:2.8\nledgerstitch:community\nollama/ollama:0.40.6\n")
        self.home = tmp / "home"
        self.home.mkdir()
        src = tmp / "seed"
        (src / "docker").mkdir(parents=True)
        (src / "scripts").mkdir()
        (src / "ledgerstitch").mkdir()
        (src / "pyproject.toml").write_text((REPO / "pyproject.toml").read_text())
        (src / "docker" / "Dockerfile").write_text(f"ARG PYTHON_IMAGE={PIN}\nFROM ${{PYTHON_IMAGE}}\n")
        (src / "docker" / "docker-compose.yml").write_text("services: {}\n")
        (src / "ledgerstitch" / "__init__.py").write_text('__version__ = "1.2.0"\n')
        shutil.copy(SCRIPT, src / "scripts" / "update.sh")
        (src / "install.sh").write_text(FAKE_INSTALLER)
        _git(tmp, "init", "-q", "-b", "main", str(src))
        _git(src, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", "-A")
        _git(src, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "seed")
        self.upstream = tmp / "upstream.git"
        _git(tmp, "clone", "-q", "--bare", str(src), str(self.upstream))
        self.seed = src
        self.co = tmp / "checkout"
        _git(tmp, "clone", "-q", str(self.upstream), str(self.co))

    # -- upstream changes
    def push(self, path: str, text: str, msg: str = "change"):
        (self.seed / path).write_text(text)
        _git(self.seed, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qam", msg)
        _git(self.seed, "push", "-q", str(self.upstream), "main")

    # -- docker state
    def image(self, ref: str, labels: dict | None = None, repodigests: str = ""):
        d = self.fake / "images" / re.sub(r"[/:@]", "_", ref)
        (d / "labels").mkdir(parents=True, exist_ok=True)
        for k, v in (labels or {}).items():
            (d / "labels" / k).write_text(v)
        (d / "repodigests").write_text(repodigests)

    def remote(self, ref: str, digest: str):
        (self.fake / "remote" / re.sub(r"[/:@]", "_", ref)).write_text(digest)

    def running(self, *services):
        (self.fake / "running").write_text("".join(s + "\n" for s in services))
        (self.fake / "cid").write_text("cid123\n")
        (self.fake / "volume").write_text("ledgerstitch_ledgerstitch-data\n")

    def log(self) -> list[str]:
        p = self.fake / "log"
        return p.read_text().splitlines() if p.exists() else []

    def builds(self) -> list[str]:
        return [ln for ln in self.log() if ln.startswith("compose build")]

    def labels_from_last_build(self):
        """What a real build would have stamped on the image."""
        args = dict(re.findall(r"--build-arg (\w+)=(\S+)", self.builds()[-1]))
        self.image("ledgerstitch:community", {
            "io.ledgerstitch.src-sha": args["LEDGERSTITCH_SRC_SHA"],
            "io.ledgerstitch.deps-sha": args["LEDGERSTITCH_DEPS_SHA"],
            "io.ledgerstitch.base-pin": args["LEDGERSTITCH_BASE_PIN"],
            "org.opencontainers.image.base.name": args["PYTHON_IMAGE"],
            "org.opencontainers.image.version": args["LEDGERSTITCH_VERSION"]})
        (self.fake / "log").unlink()

    def run(self, *args, env: dict | None = None):
        e = {"PATH": f"{self.fake / 'bin'}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.home),
             "FAKE_DOCKER": str(self.fake), "LEDGERSTITCH_PREFIX": str(self.tmp / "no-native-install"),
             "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"}
        e.update(env or {})
        return subprocess.run([self.shell, str(self.co / "scripts" / "update.sh"), *args],
                              capture_output=True, text=True, env=e, cwd=self.tmp)


@pytest.fixture(params=SHELLS)
def env(tmp_path, request):
    if not GIT:
        pytest.skip("git not installed")
    return Env(tmp_path, request.param)


def built_env(env: Env) -> Env:
    """A checkout whose image was built by update.sh and is running with its data volume."""
    r = env.run("--docker")
    assert r.returncode == 0, r.stderr
    env.labels_from_last_build()
    env.running("ledgerstitch", "caddy")
    return env


def test_docker_first_build(env):
    r = env.run("--docker", "--check")
    assert r.returncode == 10 and "not built yet" in r.stdout
    assert env.builds() == []
    r = env.run("--docker")
    assert r.returncode == 0, r.stderr
    (b,) = env.builds()
    assert f"PYTHON_IMAGE={PIN}" in b and "LEDGERSTITCH_VERSION=1.2.0" in b and "--pull" not in b
    assert not any(ln.startswith("run ") for ln in env.log())     # no image yet -> nothing to back up
    assert "no services are running" in r.stdout


def test_docker_up_to_date_does_nothing(env):
    built_env(env)
    r = env.run("--docker", "--check")
    assert r.returncode == 0 and "app image: up to date" in r.stdout and "everything is up to date" in r.stdout
    r = env.run("--docker")
    assert r.returncode == 0 and env.builds() == [] and not any(ln.startswith("compose up") for ln in env.log())


def test_docker_code_only_change(env):
    built_env(env)
    env.push("ledgerstitch/__init__.py", '__version__ = "1.2.0"\nX = 1\n')
    r = env.run("--docker", "--check")
    assert r.returncode == 10 and "app code changed -> fast rebuild" in r.stdout
    assert env.builds() == [] and _git(env.co, "rev-parse", "HEAD") != _git(env.upstream, "rev-parse", "main")
    r = env.run("--docker")
    assert r.returncode == 0, r.stderr
    assert _git(env.co, "rev-parse", "HEAD") == _git(env.upstream, "rev-parse", "main")
    log = env.log()
    run_i = next(i for i, ln in enumerate(log) if ln.startswith("run "))
    build_i = next(i for i, ln in enumerate(log) if ln.startswith("compose build"))
    up_i = next(i for i, ln in enumerate(log) if ln.startswith("compose up"))
    assert run_i < build_i < up_i                                  # backup, then build, then recreate
    assert "-v ledgerstitch_ledgerstitch-data:/data:ro" in log[run_i] and "--network none" in log[run_i]
    assert log[up_i] == "compose up -d --no-deps ledgerstitch caddy"   # only what was running
    backups = list((env.home / "LedgerStitch-docker-backups").iterdir())
    assert len(backups) == 1 and backups[0].read_text() == "TARDATA"
    assert oct(backups[0].stat().st_mode & 0o777) == "0o600"


def test_docker_dependency_change(env):
    built_env(env)
    py = (REPO / "pyproject.toml").read_text().replace('"httpx>=0.27",', '"httpx>=0.28",')
    env.push("pyproject.toml", py)
    r = env.run("--docker", "--check")
    assert r.returncode == 10 and "dependencies or Dockerfile changed" in r.stdout


def test_docker_version_bump_alone_needs_no_rebuild(env):
    built_env(env)
    py = re.sub(r'(?m)^version = ".*"$', 'version = "1.2.1"', (REPO / "pyproject.toml").read_text())
    env.push("pyproject.toml", py)
    r = env.run("--docker", "--check")
    assert r.returncode == 10 and "app image: up to date" in r.stdout      # the checkout moves, nothing builds
    r = env.run("--docker")
    assert r.returncode == 0 and env.builds() == []


def test_docker_newer_base_image(env):
    built_env(env)
    env.remote("python:3.14-slim", "sha256:" + "b" * 64)
    r = env.run("--docker", "--check")
    assert r.returncode == 0 and "rebuild on it with --refresh-base" in r.stdout    # reported, not forced
    r = env.run("--docker", "--refresh-base")
    assert r.returncode == 0, r.stderr
    (b,) = env.builds()
    assert "PYTHON_IMAGE=python:3.14-slim@sha256:" + "b" * 64 in b and b.split()[-2:] == ["--pull", "ledgerstitch"]
    env.labels_from_last_build()
    r = env.run("--docker", "--check")                     # the refreshed base is kept, not reverted to the pin
    assert r.returncode == 0 and "python:3.14-slim is current" in r.stdout


def test_docker_pulled_images_by_digest(env):
    built_env(env)
    env.image("caddy:2.8", repodigests="caddy@sha256:" + "1" * 64 + "\n")
    env.image("ollama/ollama:0.40.6", repodigests="ollama/ollama@sha256:" + "2" * 64 + "\n")
    env.remote("caddy:2.8", "sha256:" + "3" * 64)            # a newer caddy 2.8.x upstream
    env.remote("ollama/ollama:0.40.6", "sha256:" + "2" * 64)
    r = env.run("--docker", "--check")
    assert r.returncode == 10 and "caddy:2.8: newer image in the registry" in r.stdout
    assert "ollama/ollama:0.40.6: up to date" in r.stdout
    r = env.run("--docker")
    assert r.returncode == 0, r.stderr
    log = env.log()
    assert "pull --quiet caddy:2.8" in log and not any("pull --quiet ollama" in ln for ln in log)
    assert env.builds() == [] and not any(ln.startswith("run ") for ln in log)   # no app rebuild -> no backup


def test_docker_refuses_dirty_tree(env):
    built_env(env)
    env.push("ledgerstitch/__init__.py", '__version__ = "1.2.0"\nX = 2\n')
    (env.co / "docker" / "docker-compose.yml").write_text("services: {local: {}}\n")   # local settings are fine
    (env.co / "ledgerstitch" / "__init__.py").write_text("# local hack\n")
    r = env.run("--docker")
    assert r.returncode == 1 and "local changes" in r.stderr and "ledgerstitch/__init__.py" in r.stderr
    assert "docker-compose.yml" not in r.stderr and env.builds() == []
    (env.co / "ledgerstitch" / "__init__.py").write_text('__version__ = "1.2.0"\n')
    r = env.run("--docker", "--check")
    assert r.returncode == 10, r.stderr


def test_docker_offline_changes_nothing(env):
    built_env(env)
    env.upstream.rename(env.tmp / "gone.git")
    r = env.run("--docker")
    assert r.returncode == 1 and "offline" in r.stderr and env.builds() == []
    r = env.run("--docker", "--check")                       # check mode compares with what's fetched
    assert r.returncode == 0 and "couldn't look up python:3.14-slim" in r.stdout


def test_docker_failed_backup_stops_before_build(env):
    built_env(env)
    env.push("ledgerstitch/__init__.py", '__version__ = "1.2.0"\nX = 3\n')
    (env.fake / "fail_run").write_text("")
    r = env.run("--docker")
    assert r.returncode == 1 and "backing up volume" in r.stderr and env.builds() == []
    assert list((env.home / "LedgerStitch-docker-backups").iterdir()) == []
    r = env.run("--docker", "--no-backup")
    assert r.returncode == 0 and len(env.builds()) == 1


def test_docker_ref_checks_out_a_tag(env):
    built_env(env)
    _git(env.seed, "tag", "v1.2.1")
    _git(env.seed, "push", "-q", str(env.upstream), "v1.2.1")
    env.push("ledgerstitch/__init__.py", "X = 'after the tag'\n")
    r = env.run("--docker", "--ref", "v1.2.1")
    assert r.returncode == 0, r.stderr
    assert _git(env.co, "rev-parse", "HEAD") == _git(env.seed, "rev-parse", "v1.2.1")


# --------------------------------------------------------------------------- native installs
def native(env: Env, extras: str = "") -> Path:
    prefix = env.tmp / "native"
    (prefix / "venv" / "bin").mkdir(parents=True)
    py = prefix / "venv" / "bin" / "python"
    py.write_text(f'#!/bin/sh\nPYTHONPATH="{REPO}" exec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    (prefix / "install-manifest.txt").write_text(
        f"# LedgerStitch install manifest\nversion=1.2.0\npython=3.12\nsource={env.co}\nextras={extras}\n"
        f"venv={prefix / 'venv'}\npath={env.home}/.local/bin/ledgerstitch\n")
    return prefix


def test_native_code_change_reinstalls_without_dependencies(env):
    prefix = native(env)
    e = {"LEDGERSTITCH_PREFIX": str(prefix), "UV": str(env.fake / "bin" / "uv")}
    env.push("ledgerstitch/__init__.py", '__version__ = "1.2.0"\nY = 1\n')
    r = env.run("--native", "--check", env=e)
    assert r.returncode == 10 and "same dependencies" in r.stdout, r.stdout + r.stderr
    assert not (env.fake / "uv.log").exists()
    r = env.run("--native", env=e)
    assert r.returncode == 0, r.stderr
    (cmd,) = (env.fake / "uv.log").read_text().splitlines()
    assert "--no-deps --reinstall-package ledgerstitch" in cmd and cmd.endswith(str(env.co))
    m = (prefix / "install-manifest.txt").read_text()
    assert f"src_sha={_git(env.co, 'rev-parse', 'HEAD:ledgerstitch')}" in m and f"version={__version__}" in m
    r = env.run("--native", "--check", env=e)
    assert r.returncode == 0 and "up to date" in r.stdout


def test_native_dependency_change_runs_the_installer(env):
    prefix = native(env, extras="./wheels/addon.whl")
    e = {"LEDGERSTITCH_PREFIX": str(prefix), "UV": str(env.fake / "bin" / "uv")}
    env.push("pyproject.toml", (REPO / "pyproject.toml").read_text().replace('"httpx>=0.27",', '"httpx>=0.27",\n  "rich>=13",'))
    r = env.run("--native", "--check", env=e)
    assert r.returncode == 10 and "full upgrade through install.sh" in r.stdout
    r = env.run("--native", env=e)
    assert r.returncode == 0, r.stderr
    (cmd,) = (env.fake / "install.log").read_text().splitlines()
    assert cmd == (f"--prefix {prefix} --bin-dir {env.home}/.local/bin --no-app --extra ./wheels/addon.whl {env.co}")
    assert not (env.fake / "uv.log").exists()


def test_native_without_manifest(env):
    r = env.run("--native")
    assert r.returncode == 1 and "no installer manifest" in r.stderr


def test_dev_checkout(env):
    r = env.run("--dev", "--check")
    assert r.returncode == 0 and "up to date" in r.stdout
    env.push("ledgerstitch/__init__.py", "Z = 1\n")
    r = env.run("--dev")
    assert r.returncode == 0 and "nothing to reinstall" in r.stdout
    assert not (env.fake / "uv.log").exists()


def test_autodetect_refuses_ambiguity(env):
    prefix = native(env)
    built_env(env)
    r = env.run(env={"LEDGERSTITCH_PREFIX": str(prefix)})
    assert r.returncode == 1 and "more than one install" in r.stderr
