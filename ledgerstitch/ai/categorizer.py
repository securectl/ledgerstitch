# SPDX-License-Identifier: AGPL-3.0-only
"""Categorization pipeline: rules -> heuristics -> (optional) AI on redacted descriptors.

Privacy (enforced here, in the auditable core, for every edition): the only
per-transaction data an AI add-on ever receives is the list returned by
build_ai_payload(): unique, sorted, redacted + scrubbed merchant descriptors,
plus the category list and the business profile. The txn -> descriptor mapping
lives only in memory for the duration of a run, and the add-on's answers are
validated (_validate) and applied to transactions here, never by the add-on.

Other AI providers come from optional add-ons registered through
ledgerstitch.plugins. The Community edition only offers
"ai.local": an Ollama server on this computer (ai.local_ollama, loopback only),
so it never makes a non-loopback network call.
"""
from __future__ import annotations

import copy
import importlib
import logging
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable, Optional

from ..models import Lane, Rule, Source, Status, Transaction
from ..taxonomy import DEFAULT_CATEGORIES, SCHEDULE_C_LINES, UNCATEGORIZED, Category
from .errors import AIError
from .rules import apply_heuristics, apply_rules, is_locked

log = logging.getLogger(__name__)

AUTO_THRESHOLD = 0.75
MAX_NEW_CATEGORIES = 10
MAX_DESCRIPTOR_LEN = 80
MAX_CATEGORY_LEN = 40


@dataclass
class CategorizeResult:
    by_rules: int = 0
    by_heuristics: int = 0
    by_ai: int = 0
    needs_review: int = 0
    new_categories: list[str] = field(default_factory=list)
    sent_descriptors: int = 0
    # additive: user-presentable notes (AI offline, refused batch, ...)
    warnings: list[str] = field(default_factory=list)
    # additive: {name: {"lane", "schedule_c"}} for each accepted new category
    new_category_defs: dict = field(default_factory=dict)
    # additive: an AI add-on's run report (shown by the add-on's own UI)
    report: Optional[dict] = None


# ---------------------------------------------------------------- categories
def _cat_dicts(categories) -> list[dict]:
    out, seen = [], set()
    for c in (categories or DEFAULT_CATEGORIES):
        if isinstance(c, Category):
            d = {"name": c.name, "lane": c.lane.value, "schedule_c": c.schedule_c}
        elif isinstance(c, dict):
            lane = c.get("lane", "review")
            d = {"name": str(c["name"]), "lane": getattr(lane, "value", lane),
                 "schedule_c": c.get("schedule_c")}
        else:
            continue
        if d["name"] not in seen:
            seen.add(d["name"])
            out.append(d)
    return out


def _name_key(name: str) -> str:
    s = name.lower().replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", s)
    words = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w != "and"]
    return "".join(words)


_CAT_NAME_OK = re.compile(r"^[A-Za-z][A-Za-z0-9 &/'()\-,.]*$")


def _clean_new_name(name: str) -> Optional[str]:
    name = " ".join((name or "").split())
    if not (2 < len(name) <= MAX_CATEGORY_LEN) or not _CAT_NAME_OK.match(name):
        return None
    if re.search(r"\d{3,}", name) or "<" in name:
        return None
    return name


# ---------------------------------------------------------------- payload
_AMOUNT = re.compile(r"[$€£]\s*\d[\d,]*(?:\.\d+)?|\b\d[\d,]*\.\d{2}\b")
_DATE = re.compile(r"\b\d{1,4}[/\-.]\d{1,2}(?:[/\-.]\d{2,4})?\b")
_HASHNUM = re.compile(r"#\s*\d+")
_LONGNUM = re.compile(r"\d{3,}")
# leftover id labels once their values were removed
_ID_WORDS = {"REF", "REF#", "REF:", "TRACE", "TRACE#", "CONF", "CONF#", "CONFIRMATION", "ID", "ID:",
             "TRN", "TXN", "AUTH", "SEQ", "NO", "NO."}


def scrub(text: str) -> str:
    """Defense in depth after redact(): strip anything amount/date/id-like that slipped through."""
    s = (text or "").upper()
    s = _AMOUNT.sub(" ", s)
    s = _DATE.sub(" ", s)
    s = _HASHNUM.sub(" ", s)
    s = _LONGNUM.sub(" ", s)
    tokens = [tok for tok in s.split()
              if re.search(r"[A-Z0-9<]", tok) and tok not in _ID_WORDS]
    s = " ".join(tokens)
    return s[:MAX_DESCRIPTOR_LEN].strip()


def _redactor():
    """Import lazily so the security module can be swapped/stubbed; no redactor -> no AI."""
    try:
        return importlib.import_module("ledgerstitch.security.redact")
    except Exception as e:  # noqa: BLE001
        raise AIError("Redaction module unavailable; AI disabled for safety.") from e


def _needs_ai(t: Transaction) -> bool:
    if is_locked(t) or t.source == Source.RULE:
        return False
    return (not t.category or t.category == UNCATEGORIZED or t.confidence < AUTO_THRESHOLD)


def _descriptor_map(txns: Iterable[Transaction]) -> dict[str, str]:
    """txn.id -> safe descriptor, for txns that still need AI help (memory only)."""
    todo = [t for t in txns if _needs_ai(t)]
    if not todo:
        return {}
    red = _redactor()
    raw = [(t.merchant or t.description) for t in todo]
    redacted = red.redact_batch(sorted(set(raw)))
    out: dict[str, str] = {}
    for t, r in zip(todo, raw):
        item = redacted.get(r)
        text = scrub(item.text if item is not None else red.redact(r).text)
        if text and re.search(r"[A-Z]", text):
            out[t.id] = text
    return out


def build_ai_payload(txns) -> list[str]:
    """Unique, sorted redacted descriptors of txns still uncategorized / low-confidence."""
    return sorted(set(_descriptor_map(txns).values()))


# ---------------------------------------------------------------- validation
def _validate(results: list[dict], payload: set[str], cats: list[dict],
              res: CategorizeResult) -> dict[str, dict]:
    by_key = {_name_key(c["name"]): c for c in cats}
    by_name = {c["name"].lower(): c for c in cats}
    lanes = {l.value for l in Lane}
    out: dict[str, dict] = {}
    for r in results or []:
        if not isinstance(r, dict):
            continue
        d = r.get("descriptor")
        if not isinstance(d, str) or d not in payload or d in out:
            continue
        lane = r.get("lane")
        lane = Lane(lane) if isinstance(lane, str) and lane in lanes else Lane.REVIEW
        sc = r.get("schedule_c")
        sc = sc if isinstance(sc, str) and sc in SCHEDULE_C_LINES else None
        try:
            conf = float(r.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = 0.0 if math.isnan(conf) else min(max(conf, 0.0), 1.0)

        name = r.get("category") if isinstance(r.get("category"), str) else ""
        name = " ".join(name.split())
        known = (by_name.get(name.lower()) or by_key.get(_name_key(name))) if name else None
        if known is not None:
            category = known["name"]          # exact or near-duplicate of an existing name
        elif r.get("new_category") is True and (clean := _clean_new_name(name)):
            if clean in res.new_categories:
                category = clean
            elif len(res.new_categories) < MAX_NEW_CATEGORIES:
                res.new_categories.append(clean)
                res.new_category_defs[clean] = {"lane": lane.value, "schedule_c": sc}
                new = {"name": clean, "lane": lane.value, "schedule_c": sc}
                by_key[_name_key(clean)] = new
                by_name[clean.lower()] = new
                category = clean
            else:
                category, lane, conf = UNCATEGORIZED, Lane.REVIEW, min(conf, 0.5)
        else:
            category, lane, conf = UNCATEGORIZED, Lane.REVIEW, min(conf, 0.5)

        if lane in (Lane.BUSINESS, Lane.REVIEW) and sc is None:
            sc = (by_name.get(category.lower()) or {}).get("schedule_c")
        if lane in (Lane.PERSONAL, Lane.TRANSFER, Lane.INCOME):
            sc = None
        out[d] = {"category": category, "lane": lane, "schedule_c": sc, "confidence": conf}
    return out


# ---------------------------------------------------------------- pipeline
# Settings an AI add-on may see besides its own registered keys. Never the
# business name (often a client's name), the license key or core-only settings.
AI_SHARED_SETTINGS = ("business_profile", "ai_enabled", "provider", "model", "ollama_url")
MAX_MODEL_LEN = 120


def _plugin_settings(settings: dict, registry, namespace: str) -> dict:
    own = {k for k, ns in registry.setting_owner.items() if ns == namespace}
    return {k: copy.deepcopy(v) for k, v in settings.items()
            if k in own or k in AI_SHARED_SETTINGS}


def categorize(txns, rules: list[Rule], settings: dict, categories,
               provider: Optional[Any] = None, *,
               license: Any = None,
               plugin_store: Optional[Callable[[str], Any]] = None,
               registry: Any = None) -> CategorizeResult:
    """Rules, then heuristics, then (optionally) AI on redacted descriptors.

    * ``provider``: any object with ``classify(descriptors, categories) -> list[dict]``
      pinned by the caller (tests / embedding). It goes through the same redaction
      and validation as an add-on.
    * otherwise, if an add-on registered an ``ai_categorizer``, its feature (if it
      named one) is unlocked for ``license`` and ``settings["ai_enabled"]`` is on, the
      add-on is called with the redacted descriptors only (unique, most important
      first: by total spend, then frequency; the order is all it learns of amounts).
    * otherwise, if ``ai_enabled`` and ``provider == "ollama"``: the Community local
      model (loopback-only Ollama, ai.local_ollama) gets the same redacted payload.
    * ``plugin_store(namespace)`` returns the add-on's namespaced vault storage
      (an in-memory store is used when omitted).
    """
    from .. import editions, plugins
    settings = settings or {}
    res = CategorizeResult()
    active = [t for t in txns if t.duplicate_of is None]
    cats = _cat_dicts(categories)

    res.by_rules = apply_rules(active, rules or [])
    res.by_heuristics = apply_heuristics(active)

    if provider is not None:
        name = f"{getattr(provider, 'name', 'provider')}:{getattr(provider, 'model', '')}"

        def call(payload: list[str]):
            out = provider.classify(payload, [dict(c) for c in cats])
            res.warnings.extend(getattr(provider, "warnings", []) or [])
            return out
        _run_ai(active, cats, res, call, default_model=name)
        return _finish(active, res)

    reg = registry or plugins.registry()
    hook = reg.ai_categorizer_for()
    if not settings.get("ai_enabled"):
        return _finish(active, res)
    feat = reg.ai_categorizer_features.get(hook[0]) if hook is not None else None
    if hook is None or (feat is not None and not editions.feature_enabled(feat, license, reg)):
        if settings.get("provider") == "ollama":
            _run_local(active, cats, res, settings)
        elif hook is not None and settings.get("provider") not in (None, "", "off", "offline"):
            res.warnings.append("The AI add-on needs a license that includes it. "
                                "Rules and built-in categorization still ran.")
        return _finish(active, res)

    namespace, fn = hook
    store = plugin_store(namespace) if plugin_store is not None else plugins.MemoryPluginStore(namespace)
    psettings = _plugin_settings(settings, reg, namespace)

    def call(payload: list[str]):
        out = fn(list(payload), [dict(c) for c in cats], psettings, store)
        if isinstance(out, plugins.AIResponse):
            res.warnings.extend(w for w in out.warnings if isinstance(w, str) and w not in res.warnings)
            if out.report is not None:
                res.report = out.report
            out = out.results
        if isinstance(out, dict):
            return [{**r, "descriptor": d} for d, r in out.items() if isinstance(r, dict)]
        return out or []
    _run_ai(active, cats, res, call, default_model=namespace, always_call=True, by_priority=True)
    return _finish(active, res)


def _run_local(active: list[Transaction], cats: list[dict], res: CategorizeResult, settings: dict) -> None:
    """Community "ai.local": loopback-only Ollama, same redaction + validation boundary."""
    from .local_ollama import LocalOllama
    try:
        prov = LocalOllama(settings)
    except AIError as e:
        res.warnings.append(str(e))
        return

    def call(payload: list[str]):
        try:
            return prov.classify(payload, [dict(c) for c in cats])
        finally:
            res.warnings.extend(w for w in prov.warnings if w not in res.warnings)
    _run_ai(active, cats, res, call, default_model=prov.key)


def _priority_order(active: list[Transaction], dmap: dict[str, str]) -> list[str]:
    """Descriptors most important first: total spend, then number of transactions.
    Only the order leaves this function (no amounts), so an add-on can spend a
    limited budget where it matters most."""
    spend: dict[str, Decimal] = {}
    count: dict[str, int] = {}
    for t in active:
        d = dmap.get(t.id)
        if d:
            spend[d] = spend.get(d, Decimal(0)) + abs(t.amount)
            count[d] = count.get(d, 0) + 1
    return sorted(spend, key=lambda d: (-spend[d], -count[d], d))


def _run_ai(active: list[Transaction], cats: list[dict], res: CategorizeResult,
            call: Callable[[list[str]], list], default_model: str, always_call: bool = False,
            by_priority: bool = False) -> None:
    """The redaction boundary: build the payload, call out, validate, apply.
    ``by_priority``: hand the (same) descriptors over most important first."""
    try:
        dmap = _descriptor_map(active)
    except AIError as e:
        res.warnings.append(str(e))
        return
    payload = _priority_order(active, dmap) if by_priority else sorted(set(dmap.values()))
    if not payload and not always_call:
        return
    res.sent_descriptors = len(payload)
    raw: list = []
    try:
        raw = call(payload)
    except AIError as e:
        res.warnings.append(str(e))
    except Exception as e:  # noqa: BLE001 - a broken add-on must not break categorization
        log.warning("AI add-on failed: %s", type(e).__name__)
        res.warnings.append(f"AI add-on failed ({type(e).__name__}); rules and heuristics still ran.")
    raw = [r for r in (raw or []) if isinstance(r, dict)]
    models = {}
    for r in raw:
        d, m = r.get("descriptor"), r.get("model")
        if isinstance(d, str) and d not in models:
            models[d] = (str(m)[:MAX_MODEL_LEN] if isinstance(m, str) and m else default_model)
    accepted = _validate(raw, set(payload), cats, res)
    for t in active:
        d = dmap.get(t.id)
        a = accepted.get(d) if d else None
        if a is not None:
            _apply(t, {**a, "model": models.get(d, default_model)}, res)


# public names for add-ons (the plugin contract, docs/PLUGINS.md)
validate_results = _validate
category_dicts = _cat_dicts


def _apply(t: Transaction, a: dict, res: CategorizeResult) -> None:
    if t.category and t.category != UNCATEGORIZED and a["confidence"] <= t.confidence:
        return  # keep the stronger heuristic guess
    t.category = a["category"]
    t.lane = a["lane"]
    t.schedule_c = a["schedule_c"]
    t.confidence = a["confidence"]
    t.source = Source.AI
    t.ai_model = a.get("model")
    t.status = (Status.REVIEW if a["confidence"] < AUTO_THRESHOLD or a["lane"] == Lane.REVIEW
                else Status.AUTO)
    res.by_ai += 1


def _finish(active: list[Transaction], res: CategorizeResult) -> CategorizeResult:
    for t in active:
        if is_locked(t):
            continue
        if not t.category:
            t.category = UNCATEGORIZED
            t.lane = Lane.REVIEW
            t.status = Status.REVIEW
    res.needs_review = sum(1 for t in active if t.status == Status.REVIEW)
    return res
