# SPDX-License-Identifier: AGPL-3.0-only
"""First-run setup: system check, download allowlist, consent, install/pull jobs (faked),
the Ollama pull-stream parser, onboarding persistence and sample data. No network."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend_fakes import install_missing
from ledgerstitch import appconfig
from ledgerstitch.api import create_app
from ledgerstitch.onboarding import ollama as ol
from ledgerstitch.onboarding.jobs import JobManager

TOKEN = "t" * 43
BASE = "http://127.0.0.1:8765"
PASS = "correct horse battery"


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    install_missing(monkeypatch)


class FakeOllama:
    """Detector double: not running until something 'launches' it."""

    def __init__(self, running=False, models=(), path=None):
        self.running, self.models, self.path, self.calls = running, list(models), path, 0

    def __call__(self, url=None, **_):
        self.calls += 1
        return {"running": self.running, "version": "0.9.0" if self.running else None,
                "models": list(self.models), "url": "http://127.0.0.1:11434",
                "installed": self.running or bool(self.path), "path": self.path}


def _cp(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


ROOMY = {"disk_free_bytes": 400 * ol.GB, "disk_free_gb": 400.0, "disk_approx": False, "need_disk_gb": 25,
         "mem_total_bytes": 64 * ol.GB, "mem_total_gb": 64.0, "need_mem_gb": 24,
         "disk_ok": True, "mem_ok": True, "ok": True}


@pytest.fixture(autouse=True)
def _no_model_override(monkeypatch):
    monkeypatch.delenv("LEDGERSTITCH_LOCAL_MODEL", raising=False)
    monkeypatch.delenv("LEDGERSTITCH_OLLAMA_URL", raising=False)


@pytest.fixture
def app(tmp_path):
    a = create_app(tmp_path, TOKEN, port=8765)
    a.state.setup.background = False           # run jobs inline
    a.state.setup.sleep = lambda s: None
    a.state.setup.detector = FakeOllama()
    a.state.setup.resources = lambda: dict(ROOMY)
    a.state.setup.workdir = tmp_path / "dl"
    (tmp_path / "dl").mkdir()
    return a


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN}) as c:
        yield c


# ---------------------------------------------------------------- system check
def test_check_shape(client, app):
    r = client.get("/api/setup/check")
    assert r.status_code == 200
    d = r.json()
    for k in ("os", "disk", "port", "addons", "ollama", "onboarding"):
        assert k in d
    assert d["port"]["port"] == 8765 and d["port"]["host"] == "127.0.0.1"
    assert d["disk"]["free_bytes"] > 0
    assert d["ollama"]["running"] is False and d["ollama"]["install_allowed"] is True
    assert d["ollama"]["recommended_model"] == ol.RECOMMENDED_MODEL
    assert d["onboarding"] == {"completed": False, "needed": True, "local_ai_model": None, "local_ai_requested": None}
    assert d["server_mode"] is False


def test_check_needs_token(app):
    with TestClient(app, base_url=BASE) as c:
        assert c.get("/api/setup/check").status_code == 401


def test_status_reports_onboarding(client):
    st = client.get("/api/status").json()
    assert st["onboarding"]["needed"] is True
    client.post("/api/setup/complete", json={"skipped": True})
    st = client.get("/api/status").json()
    assert st["onboarding"]["needed"] is False and st["onboarding"]["completed"] is True


def test_existing_users_are_not_forced_through_setup(client):
    client.post("/api/vaults", json={"name": "Acme", "passphrase": PASS})
    assert client.get("/api/status").json()["onboarding"]["needed"] is False


def test_complete_and_reset_persist_outside_vaults(client, tmp_path):
    client.post("/api/setup/complete", json={"local_ai_model": ol.RECOMMENDED_MODEL})
    cfg = json.loads((tmp_path / "app.json").read_text())
    assert cfg["onboarding"]["completed"] is True
    assert cfg["onboarding"]["local_ai_model"] == ol.RECOMMENDED_MODEL
    assert ((tmp_path / "app.json").stat().st_mode & 0o777) == 0o600
    r = client.post("/api/setup/reset")
    assert r.json()["onboarding"]["needed"] is True
    assert appconfig.onboarding(tmp_path)["completed"] is False


def test_appconfig_drops_unknown_keys(tmp_path):
    appconfig.save(tmp_path, {"onboarding": {"completed": True}, "api_key": "sk-secret"})
    assert "sk-secret" not in (tmp_path / "app.json").read_text()


# ---------------------------------------------------------------- allowlist
@pytest.mark.parametrize("url", [
    "https://ollama.com/download/Ollama-darwin.zip",
    "https://ollama.com/download/OllamaSetup.exe",
    "https://github.com/ollama/ollama/releases/download/v0.9.0/Ollama-darwin.zip",
    "https://objects.githubusercontent.com/x/y",
    "https://release-assets.githubusercontent.com/x/y",
])
def test_allowlist_accepts_official(url):
    assert ol.check_download_url(url) == url


@pytest.mark.parametrize("url", [
    "http://ollama.com/download/OllamaSetup.exe",           # not HTTPS
    "https://ollama.com.evil.example/OllamaSetup.exe",       # look-alike
    "https://evil.example/ollama.com/OllamaSetup.exe",
    "https://user:pw@ollama.com/download/OllamaSetup.exe",
    "https://ollama.com:8443/download/OllamaSetup.exe",
    "https://registry.ollama.ai/v2/",
    "file:///etc/passwd",
    "ftp://ollama.com/x",
])
def test_allowlist_rejects(url):
    with pytest.raises(ol.DisallowedURL):
        ol.check_download_url(url)


def _mock_client(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_downloader_follows_allowlisted_redirects(tmp_path):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.host == "ollama.com":
            return httpx.Response(302, headers={"location": "https://github.com/ollama/ollama/releases/x.exe"})
        return httpx.Response(200, content=b"MZ" + b"\0" * 1000, headers={"content-length": "1002"})

    progress = []
    out = ol.Downloader(_mock_client(handler)).download(ol.OFFICIAL_URLS["Windows"], tmp_path / "s.exe",
                                                        lambda d, t: progress.append((d, t)))
    assert out.read_bytes()[:2] == b"MZ"
    assert seen[0].startswith("https://ollama.com/") and seen[1].startswith("https://github.com/")
    assert progress[-1] == (1002, 1002)


def test_downloader_refuses_redirect_off_allowlist(tmp_path):
    def handler(req):
        return httpx.Response(302, headers={"location": "https://evil.example/OllamaSetup.exe"})
    with pytest.raises(ol.DisallowedURL):
        ol.Downloader(_mock_client(handler)).download(ol.OFFICIAL_URLS["Windows"], tmp_path / "s.exe")
    assert not (tmp_path / "s.exe").exists()


def test_downloader_refuses_http_redirect(tmp_path):
    def handler(req):
        return httpx.Response(301, headers={"location": "http://ollama.com/OllamaSetup.exe"})
    with pytest.raises(ol.DisallowedURL):
        ol.Downloader(_mock_client(handler)).download(ol.OFFICIAL_URLS["Windows"], tmp_path / "s.exe")


def test_downloader_rejects_disallowed_start_url(tmp_path):
    def handler(req):  # pragma: no cover - must never be called
        raise AssertionError("network touched")
    with pytest.raises(ol.DisallowedURL):
        ol.Downloader(_mock_client(handler)).download("https://example.com/x.exe", tmp_path / "x")


# ---------------------------------------------------------------- install: consent + jobs
class FakeDownloader:
    def __init__(self):
        self.calls = []

    def download(self, url, dest, on_progress=lambda d, t: None):
        ol.check_download_url(url)
        self.calls.append(url)
        for i in range(1, 5):
            on_progress(i * 250, 1000)
        Path(dest).write_bytes(b"MZ fake installer")
        return dest


def _windows(app, signer_ok=True):
    s = app.state.setup
    s.system = "Windows"
    s.downloader = FakeDownloader()
    launched = []
    detector = s.detector

    def launcher(cmd):
        launched.append(cmd)
        detector.running = True             # the installer finished and Ollama came up

    def runner(cmd):
        if cmd[0] == "powershell":
            return _cp(0, "Valid\nCN=Ollama Inc., O=Ollama Inc.\n" if signer_ok else "NotSigned\n\n")
        raise AssertionError(cmd)

    s.launcher, s.runner = launcher, runner
    return s, launched


def test_no_install_without_consent(client, app):
    s, launched = _windows(app)
    for body in ({}, {"consent": False}):
        r = client.post("/api/setup/ollama/install", json=body)
        assert r.status_code == 400 and r.json()["consent_required"] is True
    assert s.downloader.calls == [] and launched == []
    r = client.post("/api/setup/ollama/pull", json={})
    assert r.status_code == 403 and r.json()["consent_required"] is True


def test_install_job_windows(client, app):
    s, launched = _windows(app)
    r = client.post("/api/setup/ollama/install", json={"consent": True})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert r.json()["plan"]["method"] == "exe"
    j = client.get(f"/api/setup/jobs/{job['id']}").json()
    assert j["state"] == "done" and j["progress"] == 1.0 and j["finished"]
    assert j["completed"] == 1000 and j["total"] == 1000
    assert "Ollama" in j["result"]["signer"]
    assert s.downloader.calls == [ol.OFFICIAL_URLS["Windows"]]
    assert launched and launched[0][0].endswith("OllamaSetup.exe")


def test_install_job_progress_is_observable(tmp_path):
    """Progress updates land on the job while it runs (polled by the UI)."""
    jm = JobManager()
    snapshots = []

    class SlowDL(FakeDownloader):
        def download(self, url, dest, on_progress=lambda d, t: None):
            def spy(d, t):
                on_progress(d, t)
                snapshots.append(job_ref[0].to_dict())
            return super().download(url, dest, spy)

    det = FakeOllama()
    job_ref = []

    def work(job):
        job_ref.append(job)
        ol.run_install(job, {"method": "exe", "auto": True, "url": ol.OFFICIAL_URLS["Windows"]},
                       downloader=SlowDL(), system="Windows", workdir=tmp_path,
                       runner=lambda cmd: _cp(0, "Valid\nCN=Ollama Inc.\n"),
                       launcher=lambda cmd: setattr(det, "running", True), detector=det, sleep=lambda s: None)

    job = jm.start("ollama.install", work, background=False)
    assert [s["progress"] for s in snapshots] == [0.25, 0.5, 0.75, 1.0]
    assert all(s["step"] == "download" and s["state"] == "running" for s in snapshots)
    assert job.state == "done"


def test_bad_signature_never_runs_installer(client, app):
    s, launched = _windows(app, signer_ok=False)
    r = client.post("/api/setup/ollama/install", json={"consent": True})
    j = client.get(f"/api/setup/jobs/{r.json()['job']['id']}").json()
    assert j["state"] == "error" and "signature" in j["error"]
    assert launched == []


def test_macos_signature_check():
    def runner(cmd):
        if cmd[:2] == ["codesign", "--verify"]:
            return _cp(0)
        return _cp(0, "", "Executable=/x\nAuthority=Developer ID Application: Ollama Inc. (3MU9H2V9Y9)\n"
                          "Authority=Developer ID Certification Authority\n")
    assert "Ollama" in ol.verify_signature(Path("/x/Ollama.app"), "Darwin", runner)

    def bad(cmd):
        if cmd[:2] == ["codesign", "--verify"]:
            return _cp(0)
        return _cp(0, "", "Authority=Developer ID Application: Someone Else (ABC)\n")
    with pytest.raises(ol.SetupError):
        ol.verify_signature(Path("/x/Ollama.app"), "Darwin", bad)
    with pytest.raises(ol.SetupError):
        ol.verify_signature(Path("/x/Ollama.app"), "Darwin", lambda cmd: _cp(1))


def test_linux_shows_command_and_runs_nothing(client, app):
    s, launched = _windows(app)
    s.system = "Linux"
    r = client.post("/api/setup/ollama/install", json={"consent": True})
    assert r.status_code == 200
    d = r.json()
    assert d["manual"] is True and d["job"] is None
    assert d["command"] == "curl -fsSL https://ollama.com/install.sh | sh"
    assert s.downloader.calls == [] and launched == []


def test_install_plan_variants():
    assert ol.install_plan("Darwin", which=lambda n: "/opt/homebrew/bin/brew")["method"] == "brew"
    p = ol.install_plan("Darwin", which=lambda n: None)
    assert p["method"] == "zip" and p["url"].startswith("https://ollama.com/")
    assert ol.install_plan("Windows", which=lambda n: None)["method"] == "exe"
    assert ol.install_plan("Linux", which=lambda n: None)["auto"] is False
    assert ol.install_plan("Darwin", installed=Path("/Applications/Ollama.app"))["method"] == "start"


def test_already_running_is_a_noop(client, app):
    app.state.setup.detector.running = True
    r = client.post("/api/setup/ollama/install", json={"consent": True})
    assert r.json()["already"] is True


def test_brew_install(client, app, monkeypatch):
    s = app.state.setup
    s.system = "Darwin"
    cmds = []

    def runner(cmd):
        cmds.append(cmd)
        if cmd[:3] == ["brew", "services", "start"]:
            s.detector.running = True
        return _cp(0)

    s.runner, s.launcher = runner, lambda cmd: None
    monkeypatch.setattr(ol.shutil, "which", lambda n: "/opt/homebrew/bin/brew" if n == "brew" else None)
    r = client.post("/api/setup/ollama/install", json={"consent": True})
    j = client.get(f"/api/setup/jobs/{r.json()['job']['id']}").json()
    assert j["state"] == "done", j
    assert ["brew", "install", "ollama"] in cmds


def test_unknown_job_404(client):
    assert client.get("/api/setup/jobs/nope").status_code == 404


# ---------------------------------------------------------------- pull stream
PULL_LINES = [
    b'{"status":"pulling manifest"}',
    b'{"status":"pulling dde5aa3fc5ff","digest":"sha256:dde5","total":2000,"completed":0}',
    b'{"status":"pulling dde5aa3fc5ff","digest":"sha256:dde5","total":2000,"completed":1000}',
    b'',
    b'not json',
    b'{"status":"pulling 966de95ca8a6","digest":"sha256:966d","total":2000,"completed":500}',
    b'{"status":"pulling dde5aa3fc5ff","digest":"sha256:dde5","total":2000,"completed":2000}',
    b'{"status":"pulling 966de95ca8a6","digest":"sha256:966d","total":2000,"completed":2000}',
    b'{"status":"verifying sha256 digest"}',
    b'{"status":"writing manifest"}',
    b'{"status":"success"}',
]


def test_pull_stream_parser():
    evs = list(ol.parse_pull_stream(PULL_LINES))
    assert evs[0]["progress"] is None and evs[0]["status"] == "pulling manifest"
    assert evs[1]["progress"] == 0.0
    assert evs[2]["progress"] == 0.5
    assert evs[3]["completed"] == 1500 and evs[3]["total"] == 4000   # second layer joins the total
    assert evs[3]["progress"] == 0.5                                  # ...but the bar never shrinks
    assert evs[5]["progress"] == 1.0 and not evs[5]["done"]
    assert evs[-1]["done"] and evs[-1]["progress"] == 1.0
    prog = [e["progress"] for e in evs if e["progress"] is not None]
    assert prog == sorted(prog)


def test_pull_stream_error():
    evs = list(ol.parse_pull_stream(['{"status":"pulling manifest"}', '{"error":"pull model manifest: file does not exist"}',
                                     '{"status":"success"}']))
    assert evs[-1]["error"].startswith("pull model manifest") and evs[-1]["done"]
    assert len(evs) == 2


def _consent(tmp_path, model=None):
    appconfig.update_onboarding(tmp_path, local_ai_consent={"at": "2026-10-19T00:00:00Z",
                                                            "model": model or ol.RECOMMENDED_MODEL,
                                                            "endpoint": "http://127.0.0.1:11434"})


def test_pull_job_via_api(client, app, tmp_path):
    app.state.setup.detector.running = True
    sent = []

    def handler(req):
        sent.append((str(req.url), json.loads(req.content)))
        return httpx.Response(200, content=b"\n".join(PULL_LINES) + b"\n")

    app.state.setup.pull_client_factory = _mock_client(handler)
    _consent(tmp_path)
    r = client.post("/api/setup/ollama/pull", json={"consent": True})
    assert r.status_code == 202, r.text
    j = client.get(f"/api/setup/jobs/{r.json()['job']['id']}").json()
    assert j["state"] == "done" and j["result"]["model"] == ol.RECOMMENDED_MODEL
    assert j["completed"] == 4000 and j["total"] == 4000
    assert sent == [("http://127.0.0.1:11434/api/pull", {"model": ol.RECOMMENDED_MODEL, "stream": True})]
    assert appconfig.onboarding(tmp_path)["local_ai_model"] == ol.RECOMMENDED_MODEL


def test_pull_refused_without_recorded_consent(client, app, tmp_path):
    app.state.setup.detector.running = True
    calls = []
    app.state.setup.pull_client_factory = _mock_client(lambda req: calls.append(req) or httpx.Response(500))
    r = client.post("/api/setup/ollama/pull", json={"consent": True})       # body flag alone isn't enough
    assert r.status_code == 403 and r.json()["consent_required"] is True
    _consent(tmp_path, model="some-other:model")                             # consent for another model
    assert client.post("/api/setup/ollama/pull", json={"consent": True}).status_code == 403
    assert calls == []


def test_pull_rejects_other_models_and_stopped_ollama(client, app, tmp_path):
    app.state.setup.detector.running = True
    _consent(tmp_path)
    for m in ("evil/model:latest", "llama3.2:3b", "qwen3.5:2b-q4_K_M", "glm-5.3-flash"):
        assert client.post("/api/setup/ollama/pull", json={"model": m, "consent": True}).status_code == 422
    app.state.setup.detector.running = False
    r = client.post("/api/setup/ollama/pull", json={"consent": True})
    assert r.status_code == 409


def test_pull_only_talks_to_loopback():
    with pytest.raises(ValueError):
        ol.run_pull(type("J", (), {"update": lambda self, **k: None})(), ol.RECOMMENDED_MODEL,
                    url="http://192.168.1.10:11434")


def test_detect_never_leaves_loopback():
    d = ol.detect("http://ollama.example.com:11434")
    assert d["running"] is False and "error" in d


# ---------------------------------------------------------------- sample data
def test_demo_workspace(client, tmp_path):
    client.post("/api/vaults", json={"name": "My Business", "passphrase": PASS})
    r = client.post("/api/setup/demo", json={"passphrase": PASS})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["unlocked"] and d["demo"]["files"] > 10 and d["counts"]["transactions"] > 100
    vaults = client.get("/api/vaults").json()
    assert len(vaults) == 2
    assert any(v["label"] == "Sample data" for v in vaults)      # visibly labelled, separate vault


def test_demo_needs_real_passphrase(client):
    assert client.post("/api/setup/demo", json={"passphrase": "short"}).status_code == 422


# ---------------------------------------------------------------- one-click local AI (background job)
class _PullOK:
    """httpx client double for Ollama's /api/pull stream."""

    def __init__(self, detector, model):
        self.detector, self.model = detector, model

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def stream(self, method, url, json=None):
        assert url.endswith("/api/pull") and json["model"] == self.model
        detector, model = self.detector, self.model

        class R:
            status_code = 200

            def __enter__(s): return s
            def __exit__(s, *a): return False

            def iter_lines(s):
                yield '{"status":"pulling manifest"}'
                yield '{"status":"pulling abc","digest":"abc","total":1000,"completed":470}'
                yield '{"status":"pulling abc","digest":"abc","total":1000,"completed":1000}'
                detector.models.append(model)
                yield '{"status":"success"}'
        return R()


def _local_ai(client, app, *, running=True, models=()):
    det = FakeOllama(running=running, models=models, path="/Applications/Ollama.app" if running else None)
    app.state.setup.detector = det
    app.state.setup.pull_client_factory = lambda: _PullOK(det, ol.RECOMMENDED_MODEL)
    return det


def test_local_ai_needs_consent_and_is_off_by_default(client, app):
    _local_ai(client, app)
    assert client.get("/api/setup/local-ai").json()["state"] == "off"
    r = client.post("/api/setup/local-ai", json={})
    assert r.status_code == 400 and r.json()["consent_required"] is True
    assert client.post("/api/setup/local-ai", json={"consent": True, "model": "evil:latest"}).status_code == 422
    assert client.get("/api/setup/local-ai").json()["state"] == "off"
    assert app.state.setup.detector.models == []                           # nothing was pulled


def test_local_ai_one_click_pulls_and_switches_categorization_on(client, app, tmp_path):
    det = _local_ai(client, app)
    r = client.post("/api/setup/local-ai", json={"consent": True})
    assert r.status_code == 202, r.text
    j = r.json()["job"]
    assert j["kind"] == "ollama.setup" and j["finished"] and j["result"] == {"model": ol.RECOMMENDED_MODEL}
    assert det.models == [ol.RECOMMENDED_MODEL]
    st = client.get("/api/setup/local-ai").json()
    assert st["state"] == "ready" and st["model"] == ol.RECOMMENDED_MODEL and st["size"] == ol.RECOMMENDED_SIZE
    ob = json.loads((tmp_path / "app.json").read_text())["onboarding"]
    assert ob["local_ai_model"] == ol.RECOMMENDED_MODEL and "local_ai_requested" not in ob
    # the vault created afterwards (the wizard continues meanwhile) gets local AI switched on
    assert client.post("/api/vaults", json={"name": "Acme", "passphrase": PASS}).status_code == 201
    s = client.get("/api/settings").json()
    assert s["ai_enabled"] is True and s["provider"] == "ollama" and s["model"] == ol.RECOMMENDED_MODEL
    assert client.get("/api/status").json()["ai"] == {"enabled": True, "provider": "ollama", "model": ol.RECOMMENDED_MODEL}
    # the user's own choice afterwards is respected: turning AI off is not undone on the next unlock
    client.put("/api/settings", json={"ai_enabled": False, "provider": "off"})
    path = client.get("/api/vaults").json()[0]["path"]
    client.post("/api/lock")
    client.post("/api/unlock", json={"path": path, "passphrase": PASS})
    assert client.get("/api/settings").json()["ai_enabled"] is False
    # a second workspace: applied once, then left alone
    client.post("/api/vaults", json={"name": "Second", "passphrase": PASS})
    assert client.get("/api/settings").json()["provider"] == "ollama"


def test_local_ai_applies_to_the_open_workspace_when_the_pull_finishes(client, app):
    _local_ai(client, app)
    assert client.post("/api/vaults", json={"name": "Acme", "passphrase": PASS}).status_code == 201
    assert client.get("/api/settings").json()["ai_enabled"] is False
    assert client.post("/api/setup/local-ai", json={"consent": True}).status_code == 202
    assert client.get("/api/settings").json()["provider"] == "ollama"


def test_local_ai_does_not_override_a_cloud_provider_choice(client, app, monkeypatch):
    from ledgerstitch.onboarding.routes import apply_local_ai
    _local_ai(client, app)
    client.post("/api/vaults", json={"name": "Acme", "passphrase": PASS})
    w = app.state.session.ws
    w.settings.update(ai_enabled=True, provider="fakecloud")
    assert apply_local_ai(w, ol.RECOMMENDED_MODEL) is False
    assert w.settings["provider"] == "fakecloud"


def test_local_ai_resumes_at_launch_and_can_be_forgotten(tmp_path):
    """Closed mid-download: the request is remembered in app.json and re-queued when the app starts."""
    appconfig.update_onboarding(tmp_path, local_ai_requested=ol.RECOMMENDED_MODEL, local_ai_error="network")
    _consent(tmp_path)
    a = create_app(tmp_path, TOKEN, port=8765)
    a.state.setup.background = False
    a.state.setup.sleep = lambda s: None
    det = FakeOllama(running=True, path="/Applications/Ollama.app")
    a.state.setup.detector = det
    a.state.setup.pull_client_factory = lambda: _PullOK(det, ol.RECOMMENDED_MODEL)
    with TestClient(a, base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN}) as c:   # lifespan runs resume
        st = c.get("/api/setup/local-ai").json()
        assert st["state"] == "ready" and det.models == [ol.RECOMMENDED_MODEL]
        assert c.get("/api/status").json()["onboarding"]["local_ai_model"] == ol.RECOMMENDED_MODEL
    # a request that was never finished can be withdrawn; nothing is uninstalled
    appconfig.update_onboarding(tmp_path, local_ai_requested=ol.RECOMMENDED_MODEL, local_ai_model=None)
    _consent(tmp_path)
    b = create_app(tmp_path, TOKEN, port=8765)
    b.state.setup.background = False
    b.state.setup.detector = FakeOllama(running=False)       # not even installed: resume fails, state=error
    b.state.setup.system = "Linux"
    with TestClient(b, base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN}) as c:
        st = c.get("/api/setup/local-ai").json()
        assert st["state"] == "error" and "Ollama" in st["error"]
        assert c.delete("/api/setup/local-ai").json()["state"] == "off"
        assert "local_ai_requested" not in appconfig.onboarding(tmp_path)
        assert "local_ai_consent" not in appconfig.onboarding(tmp_path)


def test_local_ai_on_linux_shows_the_command_and_runs_nothing(client, app):
    app.state.setup.system = "Linux"
    app.state.setup.detector = FakeOllama(running=False)
    r = client.post("/api/setup/local-ai", json={"consent": True})
    assert r.status_code == 200 and r.json()["manual"] is True and "curl" in r.json()["command"]
    assert client.get("/api/setup/local-ai").json()["state"] == "off"


def test_local_ai_install_then_pull_on_windows(client, app):
    _windows(app)
    det = app.state.setup.detector
    app.state.setup.pull_client_factory = lambda: _PullOK(det, ol.RECOMMENDED_MODEL)
    r = client.post("/api/setup/local-ai", json={"consent": True})
    assert r.status_code == 202, r.text
    j = r.json()["job"]
    assert j["state"] == "done" and j["result"]["model"] == ol.RECOMMENDED_MODEL and j["result"]["signer"]
    assert app.state.setup.downloader.calls == [ol.OFFICIAL_URLS["Windows"]]
    assert det.models == [ol.RECOMMENDED_MODEL]


def test_recommended_model_is_one_source_of_truth():
    from ledgerstitch.ai import local_ollama
    assert ol.RECOMMENDED_MODEL is local_ollama.RECOMMENDED_MODEL == local_ollama.DEFAULT_MODEL
    assert ol.RECOMMENDED_MODEL == "glm-4.7-flash:latest"
    assert list(ol.MODELS) == [ol.RECOMMENDED_MODEL]                      # one model, no picker
    assert ol.MODELS[ol.RECOMMENDED_MODEL]["size"] == ol.RECOMMENDED_SIZE == "19 GB"
    assert local_ollama.local_model() == ol.RECOMMENDED_MODEL
    assert local_ollama.wants_no_think(ol.RECOMMENDED_MODEL) is True     # GLM-4.7-Flash thinks unless told not to
    assert local_ollama.wants_no_think("llama3.2:3b") is False


# ---------------------------------------------------------------- consent, resources, one model (GLM-4.7-Flash)
def test_consent_is_recorded_with_time_model_and_endpoint(client, app, tmp_path):
    _local_ai(client, app)
    assert "local_ai_consent" not in appconfig.onboarding(tmp_path)
    r = client.post("/api/setup/local-ai", json={"consent": True})
    assert r.status_code == 202, r.text
    c = appconfig.onboarding(tmp_path)["local_ai_consent"]
    assert c["model"] == ol.RECOMMENDED_MODEL and c["endpoint"] == "http://127.0.0.1:11434"
    assert c["at"].endswith("Z") and c["download_anyway"] is False
    st = client.get("/api/setup/local-ai").json()
    assert st["consent"] == c and st["label"] == "GLM-4.7-Flash" and st["size"] == "19 GB"


def test_nothing_downloads_without_consent_even_at_launch(tmp_path):
    """A request left in app.json without a recorded consent (e.g. from an older build) is not resumed."""
    appconfig.update_onboarding(tmp_path, local_ai_requested=ol.RECOMMENDED_MODEL)
    a = create_app(tmp_path, TOKEN, port=8765)
    a.state.setup.background = False
    det = FakeOllama(running=True)
    a.state.setup.detector = det

    def no_pull():
        raise AssertionError("pulled without consent")

    a.state.setup.pull_client_factory = no_pull
    with TestClient(a, base_url=BASE, headers={"X-LedgerStitch-Token": TOKEN}) as c:
        assert c.get("/api/setup/local-ai").json()["state"] in ("queued", "off")
    assert det.models == []


@pytest.mark.parametrize("disk_gb,mem_gb,disk_ok,mem_ok", [
    (25, 24, True, True),            # exactly at the thresholds
    (24.9, 64, False, True),         # disk short
    (400, 23.9, True, False),        # RAM short
    (10, 8, False, False),
])
def test_resource_thresholds(tmp_path, disk_gb, mem_gb, disk_ok, mem_ok):
    r = ol.resources(tmp_path, remote=True, disk_free=int(disk_gb * ol.GB), mem_total=int(mem_gb * ol.GB))
    assert (r["disk_ok"], r["mem_ok"], r["ok"]) == (disk_ok, mem_ok, disk_ok and mem_ok)
    assert r["disk_approx"] is True and r["need_disk_gb"] == 25 and r["need_mem_gb"] == 24


def test_resources_measured_for_real(tmp_path):
    r = ol.resources(tmp_path, remote=False)
    assert r["disk_approx"] is False and r["disk_free_bytes"] > 0
    assert r["mem_total_bytes"] is None or r["mem_total_bytes"] > 0


def test_total_memory_reads_proc_meminfo(tmp_path):
    f = tmp_path / "meminfo"
    f.write_text("MemTotal:       32768000 kB\nMemFree:  1 kB\n")
    assert ol.total_memory(meminfo=f) == 32768000 * 1024


def test_low_resources_need_a_second_click(client, app, tmp_path):
    det = _local_ai(client, app)
    app.state.setup.resources = lambda: {**ROOMY, "mem_total_gb": 16.0, "mem_ok": False, "ok": False}
    r = client.post("/api/setup/local-ai", json={"consent": True})
    assert r.status_code == 409 and r.json()["needs_override"] is True
    assert r.json()["resources"]["mem_ok"] is False
    assert "local_ai_consent" not in appconfig.onboarding(tmp_path) and det.models == []
    r = client.post("/api/setup/local-ai", json={"consent": True, "download_anyway": True})
    assert r.status_code == 202, r.text
    assert appconfig.onboarding(tmp_path)["local_ai_consent"]["download_anyway"] is True
    assert det.models == [ol.RECOMMENDED_MODEL]


def test_check_reports_resources_and_the_one_model(client, app):
    o = client.get("/api/setup/check").json()["ollama"]
    assert o["recommended_model"] == ol.RECOMMENDED_MODEL and o["model_label"] == "GLM-4.7-Flash"
    assert o["recommended_size"] == "19 GB" and o["model_ram"] == "24 GB"
    assert o["resources"]["ok"] is True and o["show"] is True and o["fix"] is None
    assert "catalog" not in o                                                # no model picker


def test_remove_model_calls_ollama_delete_and_switches_local_ai_off(client, app, tmp_path):
    det = _local_ai(client, app)
    client.post("/api/vaults", json={"name": "Acme", "passphrase": PASS})
    assert client.post("/api/setup/local-ai", json={"consent": True}).status_code == 202
    assert client.get("/api/settings").json()["provider"] == "ollama"
    sent = []

    def handler(req):
        sent.append((req.method, str(req.url), json.loads(req.content)))
        det.models.clear()
        return httpx.Response(200)

    app.state.setup.delete_client_factory = _mock_client(handler)
    r = client.delete("/api/setup/local-ai/model")
    assert r.status_code == 200, r.text
    assert r.json()["removed"] is True and r.json()["state"] == "off"
    assert sent == [("DELETE", "http://127.0.0.1:11434/api/delete", {"model": ol.RECOMMENDED_MODEL})]
    s = client.get("/api/settings").json()
    assert s["ai_enabled"] is False and s["provider"] == "off"
    assert "local_ai_model" not in appconfig.onboarding(tmp_path)
    # already gone: a 404 from Ollama is fine
    app.state.setup.delete_client_factory = _mock_client(lambda req: httpx.Response(404))
    assert client.delete("/api/setup/local-ai/model").json()["removed"] is False
    det.running = False
    assert client.delete("/api/setup/local-ai/model").status_code == 409


def test_withdraw_consent_switches_local_ai_off(client, app, tmp_path):
    _local_ai(client, app)
    client.post("/api/vaults", json={"name": "Acme", "passphrase": PASS})
    client.post("/api/setup/local-ai", json={"consent": True})
    r = client.delete("/api/setup/local-ai")
    assert r.json()["state"] == "off" and r.json()["consent"] is None
    ob = appconfig.onboarding(tmp_path)
    assert "local_ai_consent" not in ob and "local_ai_model" not in ob
    assert client.get("/api/settings").json()["ai_enabled"] is False
    assert client.post("/api/setup/ollama/pull", json={"consent": True}).status_code == 403


def test_cancelled_pull_stops_reading_the_stream():
    from ledgerstitch.onboarding.jobs import Job
    job = Job("ollama.setup")

    def handler(req):
        def body():
            yield b'{"status":"pulling abc","digest":"abc","total":1000,"completed":10}\n'
            job.cancel()
            yield b'{"status":"pulling abc","digest":"abc","total":1000,"completed":20}\n'
            yield b'{"status":"success"}\n'
        return httpx.Response(200, content=body())

    with pytest.raises(ol.SetupError, match="Stopped"):
        ol.run_pull(job, ol.RECOMMENDED_MODEL, client_factory=_mock_client(handler))
    assert job.result.get("model") is None


def test_dev_model_override_is_env_only(client, app, monkeypatch):
    from ledgerstitch.ai import local_ollama
    monkeypatch.setenv("LEDGERSTITCH_LOCAL_MODEL", "qwen3:0.6b")
    assert local_ollama.local_model() == "qwen3:0.6b" and ol.allowed_model("qwen3:0.6b")
    assert not ol.allowed_model(ol.RECOMMENDED_MODEL)
    assert client.get("/api/status").json()["recommended_local_model"] == "qwen3:0.6b"
    det = FakeOllama(running=True)
    app.state.setup.detector = det
    app.state.setup.pull_client_factory = lambda: _PullOK(det, "qwen3:0.6b")
    assert client.post("/api/setup/local-ai", json={"consent": True}).status_code == 202
    assert det.models == ["qwen3:0.6b"]
    monkeypatch.setenv("LEDGERSTITCH_LOCAL_MODEL", "bad model; drop")      # junk falls back
    assert local_ollama.local_model() == ol.RECOMMENDED_MODEL


def test_categorization_payload_has_thinking_off():
    from ledgerstitch.ai import local_ollama
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": json.dumps({"results": [
            {"id": 1, "category": "Software", "lane": "business", "schedule_c": "L18", "confidence": 0.9,
             "new_category": False}]})}})

    p = local_ollama.LocalOllama({}, transport=httpx.MockTransport(handler))
    assert p.model == "glm-4.7-flash:latest"
    out = p.classify(["NETFLIX.COM"], [{"name": "Software", "lane": "business", "schedule_c": "L18"}])
    assert out and out[0]["category"] == "Software"
    assert seen and all(b["think"] is False for b in seen)
    assert seen[0]["model"] == "glm-4.7-flash:latest" and seen[0]["stream"] is False


# ---------------------------------------------------------------- operator endpoint (server mode only)
@pytest.fixture
def _server_flag():
    from ledgerstitch.ai import local_ollama
    yield local_ollama
    local_ollama.set_server_mode(False)


def test_operator_url_only_in_server_mode_and_only_that_url(monkeypatch, _server_flag):
    lo = _server_flag
    monkeypatch.setenv("LEDGERSTITCH_OLLAMA_URL", "http://ollama:11434")
    lo.set_server_mode(False)                    # desktop: the env var means nothing
    assert lo.operator_url() is None
    with pytest.raises(ValueError):
        lo.validate_ollama_url("http://ollama:11434")
    with pytest.raises(ValueError):
        lo.connect_url("http://ollama:11434")
    lo.set_server_mode(True)
    assert lo.operator_url() == "http://ollama:11434"
    assert lo.validate_ollama_url("http://ollama:11434/") == "http://ollama:11434"
    assert lo.connect_url("http://ollama:11434") == "http://ollama:11434"
    assert lo.resolve_url("http://127.0.0.1:11434") == "http://ollama:11434"     # the operator's service wins
    for bad in ("http://ollama:11435", "http://10.0.0.5:11434", "http://evil.example:11434", "http://ollama.evil:11434"):
        with pytest.raises(ValueError):
            lo.validate_ollama_url(bad)              # still loopback-only for anything typed in Settings
        with pytest.raises(ValueError):
            lo.connect_url(bad)
    assert lo.validate_ollama_url("http://localhost:11434") == "http://localhost:11434"
    for junk in ("http://ollama:11434/x", "ftp://ollama:11434", "http://"):             # junk is ignored
        monkeypatch.setenv("LEDGERSTITCH_OLLAMA_URL", junk)
        assert lo.operator_url() is None


def test_settings_refuse_remote_ollama_on_desktop_even_with_env(client, monkeypatch):
    monkeypatch.setenv("LEDGERSTITCH_OLLAMA_URL", "http://ollama:11434")
    client.post("/api/vaults", json={"name": "Acme", "passphrase": PASS})
    r = client.put("/api/settings", json={"ollama_url": "http://ollama:11434"})
    assert r.status_code == 422
    assert client.get("/api/setup/check").json()["ollama"]["operator_url"] is None
