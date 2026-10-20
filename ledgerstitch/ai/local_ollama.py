# SPDX-License-Identifier: AGPL-3.0-only
"""Community "ai.local": categorize with an Ollama server on THIS computer.

Privacy and scope:
* Only loopback servers are allowed (127.0.0.0/8, ::1, "localhost"). Any other
  host is rejected before a connection is attempted, both when the setting is
  saved and again at request time (a vault may carry an older value).
  "localhost" is connected to as 127.0.0.1, so a hosts-file entry can't send it elsewhere.
* One exception, server mode only: the operator may name the Ollama service in the
  environment (``LEDGERSTITCH_OLLAMA_URL``, e.g. ``http://ollama:11434`` in
  docker/docker-compose.yml, where Ollama is a sibling container on the compose network).
  Exactly that URL is allowed, nothing else; a URL typed into Settings is still
  loopback-only. Environment variables are set by whoever runs the server, not by a web page.
* httpx is used with ``trust_env=False`` (no HTTP(S)_PROXY detour) and without
  following redirects.
* The model receives the same redacted-descriptor payload as any AI add-on (built
  and validated by ai.categorizer); this module never sees a Transaction.

Add-ons may offer other providers; this is the minimal, auditable
single-model path of the core.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from ..taxonomy import SCHEDULE_C_LINES
from .errors import AIError

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:11434"
# The one place the local model is named (docs/AI.md "Which local model, and why"). Local AI
# uses exactly this model: first-run setup downloads it; an empty "model" setting means this one.
RECOMMENDED_MODEL = "glm-4.7-flash:latest"
DEFAULT_MODEL = RECOMMENDED_MODEL
# Development and tests ONLY (e.g. an end-to-end check with a tiny model): replaces the model
# setup downloads and the categorizer default. Not shown in the UI, not for production.
MODEL_ENV = "LEDGERSTITCH_LOCAL_MODEL"
# Server mode only: the operator-configured Ollama service (see the module docstring).
OPERATOR_URL_ENV = "LEDGERSTITCH_OLLAMA_URL"
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,80}(:[A-Za-z0-9._-]{1,40})?$")
# Model families that "think" before answering: we ask Ollama to skip that (``think: false``),
# since a merchant label needs no chain of thought and the JSON schema already constrains the
# answer. Models that don't support the flag get one retry without it.
THINKING_FAMILIES = ("glm", "qwen3", "deepseek-r1", "gemma4", "granite4", "smollm3", "lfm2.5", "magistral",
                     "nemotron")
BATCH = 40
TIMEOUT = 300.0
LANES = ["business", "personal", "transfer", "income", "review"]
TEST_DESCRIPTORS = ["NETFLIX.COM", "STAPLES", "SHELL OIL"]   # synthetic: no user data


class LocalOllamaError(AIError):
    pass


def local_model() -> str:
    """The model local AI uses: :data:`RECOMMENDED_MODEL`, unless ``LEDGERSTITCH_LOCAL_MODEL``
    names another one (development and tests only)."""
    v = (os.environ.get(MODEL_ENV) or "").strip()
    return v if v and _MODEL_RE.match(v) else RECOMMENDED_MODEL


# ---------------------------------------------------------------- loopback-only URLs
_server_mode = False


def set_server_mode(on: bool) -> None:
    """Called by create_app: the operator URL from the environment counts in server mode only."""
    global _server_mode
    _server_mode = bool(on)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _normalize(url: Optional[str], *, loopback: bool) -> str:
    raw = (url or "").strip() or DEFAULT_URL
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise ValueError("Ollama URL is not a valid URL.") from None
    if parts.scheme not in ("http", "https"):
        raise ValueError("Ollama URL must start with http:// or https://.")
    if parts.username or parts.password:
        raise ValueError("Ollama URL must not contain a user name or password.")
    if not host:
        raise ValueError("Ollama URL is not a valid URL.")
    if loopback and not _is_loopback_host(host):
        raise ValueError("Local AI only talks to Ollama on this computer "
                         "(127.0.0.1, localhost or ::1). Remote servers are not allowed.")
    if (parts.path or "/") != "/" or parts.query or parts.fragment:
        raise ValueError("Ollama URL must be just the server address, e.g. http://127.0.0.1:11434.")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return urlunsplit((parts.scheme, netloc, "", "", ""))


def validate_loopback_url(url: Optional[str]) -> str:
    """Normalize and check an Ollama URL. Raises ValueError for anything that is not
    http(s) on a loopback address."""
    return _normalize(url, loopback=True)


def operator_url() -> Optional[str]:
    """The Ollama service the server operator configured (``LEDGERSTITCH_OLLAMA_URL``), normalized.
    None outside server mode, when unset, or when the value isn't a plain http(s) address."""
    if not _server_mode:
        return None
    raw = (os.environ.get(OPERATOR_URL_ENV) or "").strip()
    if not raw:
        return None
    try:
        return _normalize(raw, loopback=False)
    except ValueError:
        log.warning("%s is not a plain http(s) server address; ignored", OPERATOR_URL_ENV)
        return None


def validate_ollama_url(url: Optional[str]) -> str:
    """For the ``ollama_url`` setting: loopback, or exactly the operator-configured URL."""
    op = operator_url()
    if op:
        try:
            if _normalize(url, loopback=False) == op:
                return op
        except ValueError:
            pass
    return validate_loopback_url(url)


def connect_url(url: Optional[str]) -> str:
    """The URL actually connected to for ``url``: the operator URL if it is exactly that,
    otherwise loopback only (raises ValueError) with 'localhost' pinned to 127.0.0.1."""
    op = operator_url()
    if op:
        try:
            if _normalize(url, loopback=False) == op:
                return op
        except ValueError:
            pass
    return _connect_url(url or DEFAULT_URL)


def resolve_url(setting: Optional[str]) -> str:
    """Where local AI talks to Ollama: the operator's service when one is configured (server
    mode), otherwise the workspace's loopback ``ollama_url`` setting."""
    return operator_url() or _connect_url(setting or DEFAULT_URL)


def _connect_url(url: str) -> str:
    """The URL actually connected to: 'localhost' pinned to 127.0.0.1."""
    url = validate_loopback_url(url)
    parts = urlsplit(url)
    if parts.hostname == "localhost":
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
        url = urlunsplit((parts.scheme, netloc, "", "", ""))
    return url


def wants_no_think(model: str) -> bool:
    """Should requests to ``model`` carry ``think: false``? (thinking-capable families only)"""
    name = (model or "").lower().split("/")[-1]
    return any(name.startswith(fam) for fam in THINKING_FAMILIES)


# ---------------------------------------------------------------- prompt (id-based)
SYSTEM_PROMPT = """\
You are a bookkeeping assistant. You categorize merchant descriptors from bank and \
credit-card statements for a small-business expense report that a CPA will review.

Input: a list of items {"id": number, "text": merchant descriptor}. Personal details \
were replaced with placeholders such as <PERSON>. You never see amounts, dates or \
account details, so judge only from the merchant text.

For EVERY item return one result object:
- id: copy the item's id exactly.
- category: a name copied exactly from the category list. Only if nothing fits, propose \
a new 2-4 word Title Case category (no merchant names) and set new_category to true; \
otherwise new_category is false.
- lane: one of business, personal, transfer, income, review (review = mixed \
business/personal use, or you cannot tell).
- schedule_c: the Schedule C line code when lane is business or review, otherwise null.
- confidence: 0 to 1. 0.9+ only for well-known, unambiguous merchants; below 0.75 when guessing.

Answer with JSON only, in the form {"results": [ ... ]}, one result per item."""

SCHEMA: dict = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"}, "category": {"type": "string"},
            "lane": {"type": "string", "enum": LANES},
            "schedule_c": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "confidence": {"type": "number"}, "new_category": {"type": "boolean"},
        },
        "required": ["id", "category", "lane", "schedule_c", "confidence", "new_category"],
    }}},
    "required": ["results"],
}


def build_user_prompt(descriptors: list[str], categories: list[dict],
                      business_profile: Optional[str] = None) -> str:
    lines = ["Categories (name | default lane | Schedule C line):"]
    lines += [f"- {c['name']} | {c['lane']} | {c.get('schedule_c') or '-'}" for c in categories]
    lines += ["", "Schedule C line codes:"]
    lines += [f"- {code}: {name}" for code, name in SCHEDULE_C_LINES.items()]
    profile = (business_profile or "").strip()
    lines += ["", "Business profile (written by the user): " + (profile[:500] if profile else "not provided.")]
    items = [{"id": i, "text": d} for i, d in enumerate(descriptors, 1)]
    return ("\n".join(lines) + f"\n\nItems to categorize ({len(items)}). Return exactly {len(items)} results:\n"
            + json.dumps({"items": items}, ensure_ascii=False))


def full_prompt(descriptors: list[str], categories: list[dict], business_profile: Optional[str] = None) -> str:
    return "[system]\n" + SYSTEM_PROMPT + "\n\n[user]\n" + build_user_prompt(descriptors, categories, business_profile)


# ---------------------------------------------------------------- lenient parsing
def _loads_lenient(text: str):
    t = (text or "").strip()
    if not t:
        raise ValueError("empty response")
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for open_, close in (("{", "}"), ("[", "]")):
        a, b = t.find(open_), t.rfind(close)
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in response")


def parse_results(text: str) -> list[dict]:
    data = _loads_lenient(text)
    if isinstance(data, list):
        results = data
    elif isinstance(data, dict):
        results = data.get("results")
        if not isinstance(results, list):
            lists = [v for v in data.values() if isinstance(v, list)]
            results = lists[0] if len(lists) == 1 else ([data] if "category" in data else None)
    else:
        results = None
    if not isinstance(results, list):
        raise ValueError("response missing a results array")
    return [r for r in results if isinstance(r, dict)]


def _norm(s) -> str:
    return " ".join(str(s).upper().replace("&LT;", "<").replace("&GT;", ">").split())


def map_back(results: list[dict], batch: list[str]) -> list[dict]:
    """Attach the original descriptor: by 1-based id, else echoed text, else position."""
    by_text = {_norm(d): d for d in batch}
    out, used = [], set()
    positional = len(results) == len(batch)
    for i, r in enumerate(results):
        d = None
        rid = r.get("id")
        try:
            rid = int(rid) if rid is not None and not isinstance(rid, bool) else None
        except (TypeError, ValueError):
            rid = None
        if rid is not None and 1 <= rid <= len(batch):
            d = batch[rid - 1]
        if d is None:
            for k in ("descriptor", "text", "merchant"):
                if isinstance(r.get(k), str) and _norm(r[k]) in by_text:
                    d = by_text[_norm(r[k])]
                    break
        if d is None and positional:
            d = batch[i]
        if d is None or d in used:
            continue
        used.add(d)
        out.append({**r, "descriptor": d})
    return out


# ---------------------------------------------------------------- the provider
class LocalOllama:
    name = "ollama"

    def __init__(self, settings: Optional[dict] = None, transport=None):
        settings = settings or {}
        try:
            self.url = resolve_url(settings.get("ollama_url"))
        except ValueError as e:
            raise LocalOllamaError(str(e)) from None
        self.model = (str(settings.get("model") or "").strip() or local_model())[:200]
        self.business_profile = settings.get("business_profile") or ""
        self.warnings: list[str] = []
        self.schema_ok: Optional[bool] = None
        self.no_think: Optional[bool] = wants_no_think(self.model) or None   # None = don't send the flag
        self._transport = transport          # tests: httpx.MockTransport

    @property
    def key(self) -> str:
        return f"ollama:{self.model}"

    def _post(self, body: dict):
        import httpx
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=TIMEOUT,
                              transport=self._transport) as client:
                return client.post(f"{self.url}/api/chat", json=body)
        except httpx.ConnectError as e:
            if operator_url():
                raise LocalOllamaError("The Ollama service isn't reachable. Start it with "
                                       "`docker compose up -d ollama`.") from e
            raise LocalOllamaError("Ollama isn't running on this computer. Start it with `ollama serve` "
                                   f"and `ollama pull {self.model}`.") from e
        except httpx.TimeoutException as e:
            raise LocalOllamaError(f"Local model '{self.model}' timed out.") from e
        except httpx.HTTPError as e:
            raise LocalOllamaError("Local Ollama request failed.") from e

    def _batch(self, batch: list[str], categories: list[dict]) -> list[dict]:
        for schema in ((True, False) if self.schema_ok is not False else (False,)):
            body = {"model": self.model, "stream": False, "options": {"temperature": 0},
                    "format": SCHEMA if schema else "json",
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                 {"role": "user", "content": build_user_prompt(batch, categories,
                                                                               self.business_profile)}]}
            if self.no_think:
                body["think"] = False
            r = self._post(body)
            if 300 <= r.status_code < 400:
                raise LocalOllamaError("Local Ollama answered with a redirect; refusing to follow it.")
            if r.status_code == 404:
                raise LocalOllamaError(f"Local model '{self.model}' not found. Run `ollama pull {self.model}`.")
            if r.status_code == 400 and self.no_think:
                self.no_think = None             # this build/model doesn't take the flag: retry without it
                return self._batch(batch, categories)
            if r.status_code >= 400:
                if schema:
                    self.schema_ok = False
                    continue
                raise LocalOllamaError(f"Local Ollama error (HTTP {r.status_code}).")
            try:
                msg = (r.json() or {}).get("message") or {}
                text = msg.get("content") or ""
                parsed = map_back(parse_results(text if text.strip() else (msg.get("thinking") or "")), batch)
            except (AttributeError, KeyError, TypeError, ValueError):
                parsed = []
            if parsed:
                self.schema_ok = self.schema_ok if not schema else True
                return parsed
        if len(batch) > 1:
            mid = len(batch) // 2
            return self._batch(batch[:mid], categories) + self._batch(batch[mid:], categories)
        self.warnings.append(f"Local model '{self.model}' returned an unreadable answer; item left for review.")
        return []

    def classify(self, descriptors: list[str], categories: list[dict]) -> list[dict]:
        out: list[dict] = []
        items = list(descriptors)
        for i in range(0, len(items), BATCH):
            out.extend(self._batch(items[i:i + BATCH], categories))
        return [{**r, "model": self.key} for r in out]


def test_connection(settings: dict, categories: list[dict], transport=None) -> dict:
    """Send three made-up merchant names to the local model. Never user data."""
    t0 = time.time()
    try:
        p = LocalOllama(settings, transport=transport)
        results = p.classify(list(TEST_DESCRIPTORS), categories)
    except AIError as e:
        return {"ok": False, "error": str(e), "sent": TEST_DESCRIPTORS, "results": []}
    ok = bool(results) and all(r.get("category") for r in results)
    return {"ok": ok, "provider": "ollama", "model": p.model, "url": p.url,
            "seconds": round(time.time() - t0, 1), "sent": TEST_DESCRIPTORS,
            "results": [{k: r.get(k) for k in ("descriptor", "category", "lane", "confidence")} for r in results],
            "warnings": p.warnings,
            **({} if ok else {"error": "The model answered, but not in the expected format."})}
