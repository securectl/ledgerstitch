# SPDX-License-Identifier: AGPL-3.0-only
"""Optional add-on discovery and the plugin contract (see docs/PLUGINS.md).

Add-ons (for example the proprietary ``ledgerstitch_pro`` package) are found
through the Python entry-point group ``ledgerstitch.plugins``. Each entry point
resolves to a module with ``register(registry)`` (or to that callable itself).
The registry handed to ``register`` is bound to the plugin's *namespace* (the
module's ``NAMESPACE`` attribute, else the entry-point name), and offers:

* ``ai_categorizer(fn, feature=None)`` – ``fn(descriptors, categories, settings, plugin_store)``
  returning ``{descriptor: {category, lane, schedule_c, confidence, model,
  new_category?}}`` or an :class:`AIResponse`. ``descriptors`` are the
  already-redacted, scrubbed, unique strings from
  :func:`ledgerstitch.ai.categorizer.build_ai_payload`; the plugin never sees a
  Transaction, an amount, a date or a raw description. The core validates the
  answers and applies them. While ``feature`` is locked the add-on is not called.
* ``ai_preview(fn)`` – ``fn(descriptors, categories, settings, plugin_store) -> dict``
  merged into ``GET /api/ai/preview`` (extra details only the add-on knows).
* ``ai_feedback(fn)`` – ``fn(plugin_store, model_key, agreed)`` when the user
  confirms or corrects an AI suggestion (only the model id + a bool).
* ``api_routes(fn, gated={path: feature})`` – ``fn(app, ws_getter)`` adds routes;
  ``gated`` paths answer 402 while the feature is locked.
* ``feature(name, tier="pro", label="")`` – declares a feature the add-on provides.
* ``settings(defaults, editable, validators, secrets=())`` – add-on settings keys.
* ``vault_migrations({new_key: legacy_vault_key})`` – read-through migration for
  :class:`PluginStore`.
* ``cli_commands(fn)`` – ``fn(subparsers)`` adds CLI subcommands.
* ``ai_providers(*ids)`` – extra values allowed for the core ``provider`` setting.
* ``license_plans({plan: [tiers]}, labels={plan: name}, aliases={old: new})`` – which
  license plans unlock the tiers this add-on's features declare (merged as a union; the
  core knows ``pro``, and ``enterprise`` unlocks every tier).
* ``provide(name, fn)`` – a named service other add-ons (or the core) look up with
  ``registry().provided(name)``; e.g. ``"selftest"``: ``fn() -> (ok, detail)`` checks run by
  the desktop ``--self-test``.
* ``static_dir(path)`` – a folder of browser files (JS modules, CSS, images) served at
  ``/addons/<namespace>/…`` with the same sign-in, CSP and cache rules as the app.
* ``ui_screen(id, title, group, icon, module, feature=None, order=100)`` – a screen: a nav
  entry and the route ``#/<id>``; the shell imports ``/addons/<ns>/<module>`` and calls
  ``render(main, params, ctx)``.
* ``ui_card(slot, id, module, feature=None, order=100, title=None)`` – a card in a UI slot
  (``overview``, ``topbar``, ``ledger-toolbar``, ``export``, ``receipts``, ``app``, or a
  slot an add-on screen renders with ``ctx.slot(name, el)``); the shell calls
  ``renderCard(el, ctx)``.
* ``ui_settings(id, title, module, feature=None, order=100)`` – a section of the Settings
  screen; the shell calls ``renderSettings(el, ctx)``.

While a UI entry's ``feature`` is locked, the core shows a neutral locked card (the title
plus "Get the full product") instead of loading the module.
* ``tax_due(fn)`` – ``fn(workspace, year, today=None) -> {"date": "YYYY-MM-DD", "amount": float,
  "label": str} | None``: the next tax payment due on or after ``today``, if an add-on can
  estimate it. Runs locally; return None when unknown. Read it with :func:`tax_due`, which
  skips hooks that raise or answer something malformed.

Loading never raises: a broken add-on is logged and recorded in
``registry().errors`` and the core keeps working without it.
"""
from __future__ import annotations

import copy
import importlib
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "ledgerstitch.plugins"
_NS_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,120}$")
_UI_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_SLOT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_ICON_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_MODULE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*\.m?js$")
# Routes the core shell owns: an add-on screen may not take them over.
CORE_SCREENS = frozenset({"welcome", "setup", "overview", "import", "review", "ledger", "receipts",
                          "report", "settings"})


class PluginError(Exception):
    """A plugin tried to register something invalid."""


@dataclass
class AIResponse:
    """Extended return value of an ``ai_categorizer`` (a plain dict also works)."""
    results: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    report: Optional[dict] = None       # an add-on's run report, passed through to the UI


@dataclass
class Feature:
    name: str
    tier: str
    label: str
    plugin: str


# --------------------------------------------------------------------------- storage
class PluginStore:
    """Namespaced JSON storage inside the workspace's encrypted vault.

    Keys are stored as ``plugin:<namespace>:<key>``. ``legacy`` maps a key to an
    older un-namespaced vault key: reads fall back to it while the new key is
    missing, and the first write moves the value (the legacy key is deleted).
    """

    def __init__(self, vault, namespace: str, legacy: Optional[dict] = None, lock=None) -> None:
        if not _NS_RE.match(namespace or ""):
            raise PluginError(f"invalid plugin namespace {namespace!r}")
        self._vault = vault
        self.namespace = namespace
        self.prefix = f"plugin:{namespace}:"
        self._legacy = dict(legacy or {})
        self._lock = lock or threading.RLock()

    def _key(self, key: str) -> str:
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise PluginError(f"invalid plugin storage key {key!r}")
        return self.prefix + key

    def get_json(self, key: str, default=None):
        with self._lock:
            val = self._vault.get_json(self._key(key), None)
            if val is None and key in self._legacy:
                val = self._vault.get_json(self._legacy[key], None)
            return default if val is None else val

    def put_json(self, key: str, value) -> None:
        with self._lock:
            self._vault.put_json(self._key(key), value)
            old = self._legacy.get(key)
            if old and self._vault.get(old) is not None:
                self._vault.delete(old)

    def delete(self, key: str) -> None:
        with self._lock:
            self._vault.delete(self._key(key))
            old = self._legacy.get(key)
            if old:
                self._vault.delete(old)

    def stable_id(self, purpose: str) -> str:
        """A stable, non-identifying id for this workspace (hash of the vault's random
        KDF salt) so a plugin can find its own local artifacts, e.g. a trained model."""
        import hashlib
        try:
            seed = self._vault._header["kdf"]["salt"]          # noqa: SLF001 - read-only
        except Exception:  # noqa: BLE001
            seed = str(getattr(self._vault, "path", ""))
        return hashlib.sha256(f"{purpose}|{seed}".encode()).hexdigest()[:12]

    def keys(self) -> list[str]:
        ks = getattr(self._vault, "keys", None)
        if ks is None:
            return []
        return sorted(k[len(self.prefix):] for k in ks() if k.startswith(self.prefix))


class MemoryPluginStore(PluginStore):
    """In-memory PluginStore for pure function calls and tests."""

    class _Mem:
        def __init__(self, data: dict):
            self.data = data

        def get_json(self, k, default=None):
            v = self.data.get(k)
            return default if v is None else copy.deepcopy(v)

        def put_json(self, k, v):
            self.data[k] = copy.deepcopy(v)

        def get(self, k):
            return b"" if k in self.data else None

        def delete(self, k):
            self.data.pop(k, None)

        def keys(self):
            return list(self.data)

    def __init__(self, namespace: str = "pro", data: Optional[dict] = None,
                 legacy: Optional[dict] = None) -> None:
        self.data: dict = data if data is not None else {}
        super().__init__(self._Mem(self.data), namespace, legacy)


# --------------------------------------------------------------------------- registry
class Registry:
    def __init__(self) -> None:
        self.plugins: dict[str, str] = {}             # namespace -> where it came from
        self.errors: list[str] = []
        self.features: dict[str, Feature] = {}
        self.ai_categorizers: list[tuple[str, Callable]] = []
        self.ai_categorizer_features: dict[str, Optional[str]] = {}   # namespace -> gating feature
        self.preview_extenders: list[tuple[str, Callable]] = []
        self.feedback_hooks: list[tuple[str, Callable]] = []
        # fn(workspace, scope) before the user deletes data; scope "transactions" | "all" | "workspace".
        # For data an add-on keeps outside the vault (e.g. trained models).
        self.data_purgers: list[tuple[str, Callable]] = []
        self.route_installers: list[tuple[str, Callable]] = []
        self.gated_routes: dict[str, str] = {}
        self.cli_installers: list[tuple[str, Callable]] = []
        self.setting_defaults: dict[str, Any] = {}
        self.setting_editable: set[str] = set()
        self.setting_validators: dict[str, Callable] = {}
        self.setting_secrets: set[str] = set()
        self.setting_owner: dict[str, str] = {}
        self.legacy_keys: dict[str, dict] = {}
        self.ai_provider_ids: set[str] = set()
        self.services: dict[str, list[tuple[str, Callable]]] = {}
        self.plan_tiers: dict[str, set] = {}
        self.plan_labels: dict[str, str] = {}
        self.plan_aliases: dict[str, str] = {}
        self.static_dirs: dict[str, Path] = {}
        self.ui_screens: list[dict] = []
        self.ui_cards: list[dict] = []
        self.ui_settings: list[dict] = []
        self.tax_due_hooks: list[tuple[str, Callable]] = []

    # ---- queries
    def ai_categorizer_for(self) -> Optional[tuple[str, Callable]]:
        return self.ai_categorizers[0] if self.ai_categorizers else None

    def provided(self, name: str) -> list[tuple[str, Callable]]:
        """Every ``(namespace, fn)`` registered with ``provide(name, fn)``, in load order."""
        return list(self.services.get(name, ()))

    def has_plugins(self) -> bool:
        return bool(self.plugins)

    def bind(self, namespace: str) -> "PluginRegistry":
        return PluginRegistry(self, namespace)


class PluginRegistry:
    """The namespace-bound view passed to a plugin's ``register()``."""

    def __init__(self, reg: Registry, namespace: str) -> None:
        self._reg = reg
        self.namespace = namespace

    def ai_categorizer(self, fn: Callable, *, feature: Optional[str] = None) -> Callable:
        """``feature``: the add-on feature whose license gates the categorizer (None = always on)."""
        self._reg.ai_categorizers.append((self.namespace, fn))
        self._reg.ai_categorizer_features[self.namespace] = self._feature(feature)
        return fn

    def ai_preview(self, fn: Callable) -> Callable:
        self._reg.preview_extenders.append((self.namespace, fn))
        return fn

    def data_purger(self, fn: Callable) -> Callable:
        """``fn(workspace, scope)``: remove what this add-on keeps outside the vault."""
        self._reg.data_purgers.append((self.namespace, fn))
        return fn

    def ai_feedback(self, fn: Callable) -> Callable:
        self._reg.feedback_hooks.append((self.namespace, fn))
        return fn

    def provide(self, name: str, fn: Callable) -> Callable:
        """A named service for other add-ons (``registry().provided(name)``)."""
        if not isinstance(name, str) or not _KEY_RE.match(name) or not callable(fn):
            raise PluginError(f"invalid service {name!r}")
        self._reg.services.setdefault(name, []).append((self.namespace, fn))
        return fn

    def license_plans(self, plans: dict, *, labels: Optional[dict] = None,
                      aliases: Optional[dict] = None) -> None:
        """Which license plans unlock which feature tiers (merged as a union)."""
        from .editions import valid_id
        for plan, tiers in (plans or {}).items():
            tiers = [tiers] if isinstance(tiers, str) else list(tiers)
            if not valid_id(plan) or plan == "free" or not all(valid_id(t) for t in tiers):
                raise PluginError(f"invalid license plan {plan!r}")
            self._reg.plan_tiers.setdefault(plan, set()).update(tiers)
        for plan, label in (labels or {}).items():
            if not valid_id(plan) or not isinstance(label, str) or not 0 < len(label) <= 60:
                raise PluginError(f"invalid plan label for {plan!r}")
            self._reg.plan_labels[plan] = label
        for old, new in (aliases or {}).items():
            if not (valid_id(old) and valid_id(new)):
                raise PluginError(f"invalid plan alias {old!r}")
            self._reg.plan_aliases[old] = new

    # ---- browser UI
    def static_dir(self, path) -> None:
        """Serve the files of ``path`` at ``/addons/<namespace>/...``."""
        p = Path(path).resolve()
        if not p.is_dir():
            raise PluginError(f"static_dir {path!s} is not a folder")
        self._reg.static_dirs[self.namespace] = p

    @staticmethod
    def _module(module: str) -> str:
        if not isinstance(module, str) or not _MODULE_RE.match(module) or ".." in module.split("/"):
            raise PluginError(f"invalid UI module {module!r} (a .js file inside the static_dir)")
        return module

    @staticmethod
    def _feature(feature) -> Optional[str]:
        if feature is None:
            return None
        if not isinstance(feature, str) or not _KEY_RE.match(feature):
            raise PluginError(f"invalid feature {feature!r}")
        return feature

    @staticmethod
    def _text(value, what: str, limit: int = 60) -> str:
        if not isinstance(value, str) or not 0 < len(value.strip()) <= limit:
            raise PluginError(f"invalid {what} {value!r}")
        return value.strip()

    def ui_screen(self, id: str, title: str, group: Optional[str], icon: str, module: str,
                  feature: Optional[str] = None, order: int = 100) -> None:
        """A screen at ``#/<id>``: the shell imports the module and calls ``render(main, params, ctx)``."""
        if not isinstance(id, str) or not _UI_ID_RE.match(id) or id in CORE_SCREENS:
            raise PluginError(f"invalid screen id {id!r}")
        if not isinstance(icon, str) or not _ICON_RE.match(icon):
            raise PluginError(f"invalid icon {icon!r}")
        self._reg.ui_screens.append({
            "ns": self.namespace, "id": id, "title": self._text(title, "screen title"),
            "group": self._text(group, "nav group", 40) if group else None, "icon": icon,
            "module": self._module(module), "feature": self._feature(feature), "order": int(order)})

    def ui_card(self, slot: str, id: str, module: str, feature: Optional[str] = None,
                order: int = 100, title: Optional[str] = None) -> None:
        """A card in a UI slot: the shell imports the module and calls ``renderCard(el, ctx)``."""
        if not isinstance(slot, str) or not _SLOT_RE.match(slot):
            raise PluginError(f"invalid UI slot {slot!r}")
        if not isinstance(id, str) or not _UI_ID_RE.match(id):
            raise PluginError(f"invalid card id {id!r}")
        self._reg.ui_cards.append({
            "ns": self.namespace, "slot": slot, "id": id, "module": self._module(module),
            "feature": self._feature(feature), "order": int(order),
            "title": self._text(title, "card title") if title else None})

    def ui_settings(self, id: str, title: str, module: str, feature: Optional[str] = None,
                    order: int = 100) -> None:
        """A Settings section: the shell imports the module and calls ``renderSettings(el, ctx)``."""
        if not isinstance(id, str) or not _UI_ID_RE.match(id):
            raise PluginError(f"invalid settings id {id!r}")
        self._reg.ui_settings.append({
            "ns": self.namespace, "id": id, "title": self._text(title, "settings title"),
            "module": self._module(module), "feature": self._feature(feature), "order": int(order)})

    def tax_due(self, fn: Callable) -> Callable:
        """``fn(workspace, year, today=None) -> {date, amount, label} | None``: the next tax payment due."""
        self._reg.tax_due_hooks.append((self.namespace, fn))
        return fn

    def api_routes(self, fn: Callable, *, gated: Optional[dict] = None) -> Callable:
        self._reg.route_installers.append((self.namespace, fn))
        for path, feat in (gated or {}).items():
            self._reg.gated_routes[str(path)] = str(feat)
        return fn

    def cli_commands(self, fn: Callable) -> Callable:
        self._reg.cli_installers.append((self.namespace, fn))
        return fn

    def feature(self, name: str, *, tier: str = "pro", label: str = "") -> None:
        from .editions import normalize_tier
        try:
            tier = normalize_tier(tier)
        except ValueError as e:
            raise PluginError(str(e)) from None
        self._reg.features[name] = Feature(name, tier, label or name, self.namespace)

    def settings(self, defaults: dict, editable: Iterable[str] = (), validators: Optional[dict] = None,
                 secrets: Iterable[str] = ()) -> None:
        from .store import CORE_DEFAULT_SETTINGS
        for k in defaults:
            if k in CORE_DEFAULT_SETTINGS:
                raise PluginError(f"setting {k!r} belongs to the core")
            owner = self._reg.setting_owner.get(k)
            if owner and owner != self.namespace:
                raise PluginError(f"setting {k!r} already registered by plugin {owner!r}")
        editable = set(editable)
        secrets = set(secrets)
        unknown = (editable | secrets | set(validators or {})) - set(defaults)
        if unknown:
            raise PluginError(f"settings without defaults: {sorted(unknown)}")
        for k, v in defaults.items():
            self._reg.setting_defaults[k] = copy.deepcopy(v)
            self._reg.setting_owner[k] = self.namespace
        self._reg.setting_editable |= editable
        self._reg.setting_secrets |= secrets
        self._reg.setting_validators.update(validators or {})

    def ai_providers(self, *ids: str) -> None:
        for i in ids:
            if not re.match(r"^[a-z][a-z0-9_]{0,31}$", i) or i in ("off", "ollama"):
                raise PluginError(f"invalid provider id {i!r}")
            self._reg.ai_provider_ids.add(i)

    def vault_migrations(self, mapping: dict) -> None:
        self._reg.legacy_keys.setdefault(self.namespace, {}).update(mapping)


_lock = threading.RLock()
_registry = Registry()
_loaded = False


def registry() -> Registry:
    return _registry


def _register_into(reg: Registry, target: Any, namespace: str, source: str) -> bool:
    if not _NS_RE.match(namespace or ""):
        reg.errors.append(f"{source}: invalid namespace {namespace!r}")
        return False
    if namespace in reg.plugins:
        return False                         # already registered (e.g. locally in a test)
    fn = getattr(target, "register", target)
    if not callable(fn):
        reg.errors.append(f"{source}: no register() function")
        return False
    # Register into a scratch registry first so a failing plugin leaves no half-state.
    scratch = Registry()
    scratch.setting_owner = dict(reg.setting_owner)
    try:
        fn(scratch.bind(namespace))
    except Exception as e:  # noqa: BLE001 - a broken add-on must never break the core
        log.warning("plugin %s failed to register: %s", namespace, type(e).__name__)
        reg.errors.append(f"{namespace}: {type(e).__name__}: {e}")
        return False
    problem = _check_ui(scratch, namespace, reg)
    if problem:
        log.warning("plugin %s has an invalid UI registration: %s", namespace, problem)
        reg.errors.append(f"{namespace}: {problem}")
        return False
    reg.plugins[namespace] = source
    reg.features.update(scratch.features)
    for name in ("ai_categorizers", "preview_extenders", "feedback_hooks", "route_installers",
                 "cli_installers", "data_purgers", "ui_screens", "ui_cards", "ui_settings",
                 "tax_due_hooks"):
        getattr(reg, name).extend(getattr(scratch, name))
    for name, fns in scratch.services.items():
        reg.services.setdefault(name, []).extend(fns)
    for plan, tiers in scratch.plan_tiers.items():
        reg.plan_tiers.setdefault(plan, set()).update(tiers)
    reg.plan_labels.update(scratch.plan_labels)
    reg.plan_aliases.update(scratch.plan_aliases)
    reg.ai_categorizer_features.update(scratch.ai_categorizer_features)
    reg.static_dirs.update(scratch.static_dirs)
    reg.gated_routes.update(scratch.gated_routes)
    reg.setting_defaults.update(scratch.setting_defaults)
    reg.setting_editable |= scratch.setting_editable
    reg.setting_secrets |= scratch.setting_secrets
    reg.setting_validators.update(scratch.setting_validators)
    reg.setting_owner.update(scratch.setting_owner)
    reg.ai_provider_ids |= scratch.ai_provider_ids
    for ns, m in scratch.legacy_keys.items():
        reg.legacy_keys.setdefault(ns, {}).update(m)
    return True


def _check_ui(scratch: Registry, namespace: str, reg: Registry) -> Optional[str]:
    """UI modules must exist in the add-on's static_dir; screen ids must be unique."""
    entries = scratch.ui_screens + scratch.ui_cards + scratch.ui_settings
    if not entries:
        return None
    root = scratch.static_dirs.get(namespace)
    if root is None:
        return "UI entries need registry.static_dir(...)"
    for e in entries:
        f = (root / e["module"]).resolve()
        if not f.is_relative_to(root) or not f.is_file():
            return f"UI module {e['module']} not found in its static_dir"
    taken = {s["id"] for s in reg.ui_screens}
    ids = [s["id"] for s in scratch.ui_screens]
    if len(set(ids)) != len(ids) or taken & set(ids):
        return "duplicate screen id"
    return None


def _entry_points():
    from importlib.metadata import entry_points
    return list(entry_points(group=ENTRY_POINT_GROUP))


def load(*, entry_points: bool = True) -> Registry:
    """Discover installed add-ons once (idempotent). Skipped entirely when the
    edition is explicitly ``community``."""
    global _loaded
    with _lock:
        if _loaded:
            return _registry
        _loaded = True
        from . import editions
        if not entry_points or not editions.plugins_allowed():
            return _registry
        try:
            eps = _entry_points()
        except Exception as e:  # noqa: BLE001
            _registry.errors.append(f"entry points: {type(e).__name__}")
            return _registry
        for ep in eps:
            try:
                target = ep.load()
            except Exception as e:  # noqa: BLE001
                log.warning("plugin %s failed to import: %s", ep.name, type(e).__name__)
                _registry.errors.append(f"{ep.name}: {type(e).__name__}: {e}")
                continue
            ns = getattr(target, "NAMESPACE", None) or ep.name
            _register_into(_registry, target, ns, f"entry_point:{ep.value}")
        return _registry


def register_local(target: ModuleType | Callable | str, namespace: Optional[str] = None) -> bool:
    """Register a plugin in-process (tests, embedding). ``target`` is a module,
    an import path, or a ``register(registry)`` callable."""
    if isinstance(target, str):
        target = importlib.import_module(target)
    ns = namespace or getattr(target, "NAMESPACE", None) or getattr(target, "__name__", "local").rsplit(".", 1)[-1]
    with _lock:
        return _register_into(_registry, target, ns, "local")


def tax_due(ws, year: int, *, today=None) -> Optional[dict]:
    """The first ``tax_due`` hook that answers with a sane next payment
    ``{"date", "amount", "label", "source"}``, or None (no hook, locked, or nothing due).
    A hook that raises or returns something malformed is skipped."""
    import inspect
    from datetime import date
    for ns, fn in getattr(getattr(ws, "registry", None), "tax_due_hooks", None) or []:
        try:
            try:
                takes_today = "today" in inspect.signature(fn).parameters
            except (TypeError, ValueError):
                takes_today = False
            out = fn(ws, year, today=today) if takes_today else fn(ws, year)
        except Exception as e:  # noqa: BLE001 - an add-on must never break its caller
            log.warning("tax-due hook %s failed: %s", ns, type(e).__name__)
            continue
        if not isinstance(out, dict):
            continue
        amt = out.get("amount")
        if not isinstance(amt, (int, float)) or isinstance(amt, bool) or not 0 <= amt < 1e9:
            continue
        try:
            d = date.fromisoformat(str(out.get("date"))[:10])
        except ValueError:
            continue
        return {"date": d.isoformat(), "amount": round(float(amt), 2),
                "label": str(out.get("label") or "Due")[:80], "source": ns}
    return None


def reset(*, loaded: bool = False) -> Registry:
    """Forget every registered plugin (tests). ``loaded=True`` also stops a later
    ``load()`` from discovering entry points."""
    global _registry, _loaded
    with _lock:
        _registry = Registry()
        _loaded = loaded
        return _registry


__all__ = ["ENTRY_POINT_GROUP", "AIResponse", "Feature", "MemoryPluginStore", "PluginError",
           "PluginRegistry", "PluginStore", "Registry", "load", "register_local", "registry", "reset", "tax_due"]
