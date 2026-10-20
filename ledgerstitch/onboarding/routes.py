# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP API for first-run setup (installed by :func:`ledgerstitch.api.create_app`).

    GET  /api/setup/check                    -> system check + local AI (Ollama) status + resources
    POST /api/setup/local-ai {consent, download_anyway?} -> 202 {job, local_ai}: records the consent,
                                                then one background job installs/starts Ollama if
                                                needed (desktop only) and pulls the model
    GET  /api/setup/local-ai                 -> {state, model, size, progress, message, job, error, consent}
    DELETE /api/setup/local-ai               -> withdraw consent: stop the download, forget the request
    DELETE /api/setup/local-ai/model         -> remove the model from Ollama (DELETE /api/delete)
    POST /api/setup/ollama/install {consent} -> 202 {job} | {manual, command} | {already}
    POST /api/setup/ollama/pull {consent}    -> 202 {job} (needs a recorded consent)
    GET  /api/setup/jobs/{id}                -> job progress
    POST /api/setup/complete {skipped?, local_ai_model?}
    POST /api/setup/reset                    -> "Run setup again"
    POST /api/setup/demo {passphrase}        -> a separate sample-data workspace, unlocked

These routes work while every vault is locked (setup runs before the first vault exists)
but still need the per-launch token (desktop) or a signed-in session (server mode).
Installing software is refused in server mode: a web page must not install programs on
a server. In server mode the model is pulled into the operator's Ollama service
(``LEDGERSTITCH_OLLAMA_URL``, the compose ``ollama`` container).

Local AI uses exactly one model (``local_model()``: GLM-4.7-Flash). Nothing is downloaded
without an explicit consent recorded in app.json (``local_ai_consent``: time, model, endpoint).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import appconfig
from ..ai import local_ollama
from ..ai.local_ollama import DEFAULT_URL, local_model
from . import ollama as ol
from .jobs import JobManager
from .system import system_check


class InstallBody(BaseModel):
    model_config = {"extra": "forbid"}
    consent: bool = False
    method: Optional[str] = Field(default=None, max_length=20)


class PullBody(BaseModel):
    model_config = {"extra": "forbid"}
    model: Optional[str] = Field(default=None, max_length=120)
    consent: bool = False


class LocalAIBody(BaseModel):
    model_config = {"extra": "forbid"}
    model: Optional[str] = Field(default=None, max_length=120)
    consent: bool = False
    download_anyway: bool = False      # the second click after a low-disk / low-RAM warning


class CompleteBody(BaseModel):
    model_config = {"extra": "forbid"}
    skipped: bool = False
    local_ai_model: Optional[str] = Field(default=None, max_length=60)


class DemoBody(BaseModel):
    model_config = {"extra": "forbid"}
    passphrase: str = Field(min_length=1, max_length=1024)


@dataclass
class SetupDeps:
    """Seams for tests: swap the downloader, process runner, launcher and detector."""
    downloader: Any = field(default_factory=ol.Downloader)
    runner: Callable = ol._run
    launcher: Optional[Callable] = None
    detector: Callable[..., dict] = ol.detect
    pull_client_factory: Optional[Callable] = None
    delete_client_factory: Optional[Callable] = None
    resources: Optional[Callable[[], dict]] = None      # tests: fake disk / memory numbers
    system: Optional[str] = None
    background: bool = True
    wait_timeout: float = 120.0
    sleep: Callable[[float], None] = time.sleep
    workdir: Optional[Path] = None


def _err(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def onboarding_status(session) -> dict:
    ob = appconfig.onboarding(session.data_dir)
    completed = bool(ob.get("completed"))
    try:
        has_vaults = bool(session.list_vaults())
    except Exception:  # noqa: BLE001
        has_vaults = False
    # Existing users (they already have a workspace) never get the wizard forced on them.
    return {"completed": completed, "needed": not completed and not has_vaults,
            "local_ai_model": ob.get("local_ai_model"),
            "local_ai_requested": ob.get("local_ai_requested")}


def consent_for(data_dir, model: str) -> Optional[dict]:
    """The recorded consent for ``model`` (from the consent dialog), or None."""
    c = appconfig.onboarding(data_dir).get("local_ai_consent")
    return c if isinstance(c, dict) and c.get("model") == model else None


LOCAL_AI_APPLIED_KEY = "onboarding:local_ai_applied"     # vault key: the model setup switched on


def apply_local_ai(w, model: str) -> bool:
    """Switch categorization to the downloaded local model, once per workspace, and only
    while AI is still off (never override a provider the user chose). Returns True if applied."""
    with w.lock:
        if w.vault.get_json(LOCAL_AI_APPLIED_KEY, None) == model:
            return False
        if w.settings.get("ai_enabled") or w.settings.get("provider") not in (None, "", "off", "ollama"):
            w.vault.put_json(LOCAL_AI_APPLIED_KEY, model)        # user already decided: don't ask again
            return False
        w.settings.update(ai_enabled=True, provider="ollama", model=model)
        w.vault.put_json(LOCAL_AI_APPLIED_KEY, model)
        w.save()
    return True


def install(app: FastAPI, session, *, port: Optional[int], host: str, server_mode: bool,
            status_payload: Callable[[], dict], vault_cls, workspace_cls, min_passphrase: int) -> None:
    deps = SetupDeps()
    jobs = JobManager()
    app.state.setup = deps
    app.state.setup_jobs = jobs

    def ollama_url() -> str:
        """The Ollama endpoint: the operator's service (server mode, LEDGERSTITCH_OLLAMA_URL), else
        the open workspace's loopback setting."""
        op = local_ollama.operator_url()
        if op:
            return op
        w = session.ws
        return (w.settings.get("ollama_url") if w is not None else None) or DEFAULT_URL

    def resources() -> dict:
        if deps.resources is not None:
            return deps.resources()
        return ol.resources(session.data_dir, remote=bool(local_ollama.operator_url()))

    # ------------------------------------------------------------ consented local AI (background)
    def local_ai_status() -> dict:
        ob = appconfig.onboarding(session.data_dir)
        job = jobs.running("ollama.setup")
        model = local_model()
        done, wanted, error = ob.get("local_ai_model"), ob.get("local_ai_requested"), ob.get("local_ai_error")
        info = ol.model_info(model)
        out = {"model": model, "label": info["label"], "size": info["size"], "ram": info["ram"],
               "job": job.to_dict() if job else None, "error": None, "progress": None, "message": "",
               "consent": consent_for(session.data_dir, model), "endpoint": ollama_url(),
               "server_mode": server_mode}
        if job is not None:
            out.update(state="running", progress=job.progress, message=job.message or "Working…",
                       completed=job.completed, total=job.total)
        elif wanted == model and error:
            out.update(state="error", error=error, message=error)
        elif wanted == model:
            out.update(state="queued", message="Waiting to start")
        elif done == model:
            out.update(state="ready", message="Ready")
        else:
            out.update(state="off")
        return out

    def start_local_ai(model: str) -> Optional[dict]:
        """Start (or re-attach to) the background setup job. Needs the recorded consent."""
        existing = jobs.running("ollama.setup")
        if existing:
            return existing.to_dict()
        url = ollama_url()

        def work(job):
            try:
                if not consent_for(session.data_dir, model):
                    raise ol.SetupError("Setting up local AI needs your OK first.")
                ol.run_setup(job, model, url=url, detector=deps.detector, system=deps.system,
                             pull_client_factory=deps.pull_client_factory,
                             allow_install=not server_mode, wait_timeout=deps.wait_timeout if server_mode else 0.0,
                             sleep=deps.sleep,
                             install_kw={"downloader": deps.downloader, "runner": deps.runner,
                                         "launcher": deps.launcher, "workdir": deps.workdir,
                                         "wait_timeout": deps.wait_timeout, "sleep": deps.sleep})
            except Exception as e:  # noqa: BLE001 - remember why, so Settings can show it
                if consent_for(session.data_dir, model):       # not after "Withdraw consent"
                    appconfig.update_onboarding(session.data_dir, local_ai_error=(str(e) or type(e).__name__)[:300])
                raise
            appconfig.update_onboarding(session.data_dir, local_ai_model=model, local_ai_requested=None,
                                        local_ai_error=None)
            w = session.ws
            if w is not None:
                apply_local_ai(w, model)

        return jobs.start("ollama.setup", work, background=deps.background).to_dict()

    def resume_local_ai() -> None:
        """At launch: finish a consented download the app was closed (or restarted) on."""
        ob = appconfig.onboarding(session.data_dir)
        wanted = ob.get("local_ai_requested")
        if wanted and ol.allowed_model(wanted) and consent_for(session.data_dir, wanted):
            appconfig.update_onboarding(session.data_dir, local_ai_error=None)
            start_local_ai(wanted)

    def on_open(w) -> None:
        model = appconfig.onboarding(session.data_dir).get("local_ai_model")
        if model:
            apply_local_ai(w, model)

    def switch_off_local(model: str) -> None:
        """The open workspace stops using local AI (after Remove model / Withdraw consent)."""
        w = session.ws
        if w is None:
            return
        with w.lock:
            if w.settings.get("provider") == "ollama" and (w.settings.get("model") or model) == model:
                w.settings.update(ai_enabled=False, provider="off")
                w.save()

    app.state.setup_resume = resume_local_ai
    session.open_hooks.append(on_open)

    @app.get("/api/setup/local-ai")
    def local_ai_get():
        return local_ai_status()

    @app.post("/api/setup/local-ai")
    def local_ai_post(body: LocalAIBody):
        if not body.consent:
            return _err(400, "Setting up local AI needs your OK first.", consent_required=True)
        model = body.model or local_model()
        if not ol.allowed_model(model):
            return _err(422, "Local AI uses one model only.", model=local_model())
        url = ollama_url()
        d = deps.detector(url)
        if not d.get("running"):
            if server_mode:                   # never install on a server: the operator starts the service
                return _err(409, "The Ollama service isn't reachable.", fix=ol.DOCKER_FIX, reachable=False)
            plan = ol.install_plan(deps.system, installed=Path(d["path"]) if d.get("path") else None)
            if not plan.get("auto"):          # Linux: the user runs the official command themselves
                return {"manual": True, "plan": plan, "command": plan.get("command"), "job": None,
                        "local_ai": local_ai_status()}
        res = resources()
        if not res["ok"] and not body.download_anyway:
            return _err(409, "This machine may be too small for the model.", resources=res, needs_override=True)
        consent = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": model, "endpoint": url,
                   "download_anyway": not res["ok"]}
        appconfig.update_onboarding(session.data_dir, local_ai_requested=model, local_ai_error=None,
                                    local_ai_consent=consent)
        job = start_local_ai(model)
        return JSONResponse({"job": job, "local_ai": local_ai_status()}, status_code=202)

    @app.delete("/api/setup/local-ai")
    def local_ai_delete():
        """Withdraw consent: stop a running download and forget the request. The model, if it
        finished downloading, stays in Ollama until "Remove model"."""
        job = jobs.running("ollama.setup")
        if job is not None:
            job.cancel()
        model = local_model()
        appconfig.update_onboarding(session.data_dir, local_ai_requested=None, local_ai_error=None,
                                    local_ai_consent=None, local_ai_consent_at=None, local_ai_model=None)
        switch_off_local(model)
        return local_ai_status()

    @app.delete("/api/setup/local-ai/model")
    def local_ai_remove_model():
        model = local_model()
        job = jobs.running("ollama.setup")
        if job is not None:
            job.cancel()
        url = ollama_url()
        if not deps.detector(url).get("running"):
            return _err(409, "Ollama isn't reachable, so the model can't be removed right now.",
                        **({"fix": ol.DOCKER_FIX} if server_mode else {}))
        try:
            removed = ol.delete_model(model, url=url, client_factory=deps.delete_client_factory)
        except (ol.SetupError, ValueError) as e:
            return _err(502, str(e))
        except Exception:  # noqa: BLE001 - httpx errors
            return _err(502, "Couldn't reach Ollama to remove the model.")
        appconfig.update_onboarding(session.data_dir, local_ai_model=None, local_ai_requested=None,
                                    local_ai_error=None)
        switch_off_local(model)
        return {**local_ai_status(), "removed": removed}

    def ollama_block() -> dict:
        url = ollama_url()
        d = deps.detector(url)
        op = local_ollama.operator_url()
        model = local_model()
        info = ol.model_info(model)
        plan = ol.install_plan(deps.system, installed=Path(d["path"]) if d.get("path") and not d.get("running") else None)
        return {**d, "plan": plan, "install_allowed": not server_mode,
                # server mode: the step shows only when the operator configured an Ollama service
                # (LEDGERSTITCH_OLLAMA_URL) or one answers anyway
                "show": (not server_mode) or bool(op) or bool(d.get("running")),
                "operator_url": op, "fix": ol.DOCKER_FIX if server_mode else None,
                "recommended_model": model, "recommended_size": info["size"], "model_label": info["label"],
                "model_ram": info["ram"], "has_recommended": ol.has_model(d.get("models") or [], model),
                "resources": resources(), "local_ai": local_ai_status()}

    @app.get("/api/setup/check")
    def setup_check():
        out = system_check(session.data_dir, port=port, host=host, server_mode=server_mode,
                           ollama=ollama_block())
        out["onboarding"] = onboarding_status(session)
        return out

    @app.post("/api/setup/ollama/install")
    def ollama_install(body: InstallBody):
        if server_mode:
            return _err(403, "Installing software is turned off in server mode. Start the Ollama "
                             f"service instead: {ol.DOCKER_FIX}")
        if not body.consent:
            return _err(400, "Installing Ollama needs your OK first.", consent_required=True)
        d = deps.detector(ollama_url())
        if d.get("running"):
            return {"already": True, "job": None, "ollama": d}
        existing = jobs.running("ollama.install")
        if existing:
            return JSONResponse({"job": existing.to_dict()}, status_code=202)
        installed = Path(d["path"]) if d.get("path") else None
        plan = ol.install_plan(deps.system, installed=installed)
        if body.method == "zip" and plan.get("method") == "brew":
            plan = {"method": "zip", "auto": True, "url": ol.OFFICIAL_URLS["Darwin"]}
        if not plan.get("auto"):
            return {"manual": True, "plan": plan, "command": plan.get("command"), "job": None}
        if plan.get("url"):
            try:
                ol.check_download_url(plan["url"])
            except ol.DisallowedURL as e:
                return _err(400, str(e))

        def work(job):
            ol.run_install(job, plan, url=ollama_url(), downloader=deps.downloader, runner=deps.runner,
                           launcher=deps.launcher, detector=deps.detector, system=deps.system,
                           workdir=deps.workdir, wait_timeout=deps.wait_timeout, sleep=deps.sleep)

        job = jobs.start("ollama.install", work, background=deps.background)
        return JSONResponse({"job": job.to_dict(), "plan": plan}, status_code=202)

    @app.post("/api/setup/ollama/pull")
    def ollama_pull(body: PullBody):
        model = body.model or local_model()
        if not ol.allowed_model(model):
            return _err(422, "Local AI uses one model only.", model=local_model())
        if not body.consent or not consent_for(session.data_dir, model):
            return _err(403, "Downloading the model needs your OK in the consent dialog first.",
                        consent_required=True)
        d = deps.detector(ollama_url())
        if not d.get("running"):
            return _err(409, "Ollama isn't running. Install or open it first.")
        existing = jobs.running("ollama.pull")
        if existing:
            return JSONResponse({"job": existing.to_dict()}, status_code=202)
        url = ollama_url()

        def work(job):
            ol.run_pull(job, model, url=url, client_factory=deps.pull_client_factory)
            appconfig.update_onboarding(session.data_dir, local_ai_model=model)

        job = jobs.start("ollama.pull", work, background=deps.background)
        return JSONResponse({"job": job.to_dict()}, status_code=202)

    @app.get("/api/setup/jobs/{job_id}")
    def setup_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            return _err(404, "No such setup job (it may be from before a restart).")
        return job.to_dict()

    @app.post("/api/setup/complete")
    def setup_complete(body: CompleteBody):
        model = body.local_ai_model if ol.allowed_model(body.local_ai_model) else None
        appconfig.update_onboarding(session.data_dir, completed=True, skipped=body.skipped,
                                    **({"local_ai_model": model} if model else {}))
        return {"onboarding": onboarding_status(session)}

    @app.post("/api/setup/reset")
    def setup_reset():
        appconfig.update_onboarding(session.data_dir, completed=False)
        return {"onboarding": {**onboarding_status(session), "needed": True}}

    @app.post("/api/setup/demo", status_code=201)
    def setup_demo(body: DemoBody):
        from .demo import DEMO_LABEL, DEMO_NAME, load_demo
        if vault_cls is None:
            return _err(500, "Vault module unavailable.")
        if len(body.passphrase) < min_passphrase:
            return _err(422, f"Passphrase must be at least {min_passphrase} characters.")
        session.vaults_dir.mkdir(parents=True, exist_ok=True)
        path = session.new_vault_path()
        with session.lock:
            session.lock_vault()
            v = vault_cls.create(path, body.passphrase, DEMO_NAME, label=DEMO_LABEL)
            w = workspace_cls(v)
            summary = load_demo(w)
            session.ws, session.vault_path = w, path.resolve()
            session.touch()
        return {**status_payload(), "demo": summary}
