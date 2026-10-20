# SPDX-License-Identifier: AGPL-3.0-only
"""Tiny in-memory background jobs for first-run setup (installer download, model pull).

A job is a function run on a daemon thread that updates its own progress. The UI polls
``GET /api/setup/jobs/{id}``. Jobs are not persisted: quitting the app forgets them.
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Any, Callable, Optional

MAX_JOBS = 20


class JobCancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str) -> None:
        self.id = secrets.token_hex(8)
        self.kind = kind
        self.state = "queued"          # queued | running | waiting | done | error
        self.step = ""                 # short machine-readable step: download, verify, launch, ...
        self.message = ""              # short human caption
        self.progress: Optional[float] = None   # 0..1, None = indeterminate
        self.completed = 0
        self.total = 0
        self.error: Optional[str] = None
        self.result: dict[str, Any] = {}
        self.cancel_requested = False   # set by cancel(); long-running work checks it and stops
        self.created = time.time()
        self.updated = self.created
        self._lock = threading.Lock()

    def update(self, **kw: Any) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)
            self.updated = time.time()

    def cancel(self) -> None:
        self.cancel_requested = True

    @property
    def finished(self) -> bool:
        return self.state in ("done", "error")

    def to_dict(self) -> dict:
        with self._lock:
            return {"id": self.id, "kind": self.kind, "state": self.state, "step": self.step,
                    "message": self.message, "progress": self.progress, "completed": self.completed,
                    "total": self.total, "error": self.error, "result": dict(self.result),
                    "finished": self.finished}


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def running(self, kind: str) -> Optional[Job]:
        with self._lock:
            for j in self._jobs.values():
                if j.kind == kind and not j.finished:
                    return j
        return None

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, kind: str, fn: Callable[[Job], Any], *, background: bool = True) -> Job:
        job = Job(kind)
        with self._lock:
            if len(self._jobs) >= MAX_JOBS:
                for old in sorted((j for j in self._jobs.values() if j.finished), key=lambda j: j.created):
                    self._jobs.pop(old.id, None)
                    if len(self._jobs) < MAX_JOBS:
                        break
            self._jobs[job.id] = job

        def run() -> None:
            job.update(state="running")
            try:
                fn(job)
                if not job.finished:
                    job.update(state="done", progress=1.0)
            except Exception as e:  # noqa: BLE001 - surface every failure to the UI
                job.update(state="error", error=str(e) or type(e).__name__)

        if background:
            threading.Thread(target=run, name=f"setup-{kind}", daemon=True).start()
        else:
            run()
        return job
