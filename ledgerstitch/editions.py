# SPDX-License-Identifier: AGPL-3.0-only
"""Edition identity and the feature registry (docs/PLUGINS.md).

* **Community features** (:data:`CORE_FEATURES`) are always on, with no usage caps.
* **Add-on features** need BOTH the add-on installed (it declares the feature through
  :mod:`ledgerstitch.plugins`) AND a valid license whose plan unlocks the feature's tier.
  Add-ons also declare which license plans unlock their tiers
  (``registry.license_plans``); the core itself only knows ``pro`` and ``enterprise``
  (which unlocks every tier).

Edition identity:

* ``EDITION`` is the build-time constant (``ledgerstitch/_edition.py``, written
  by ``scripts/build_macos.sh``), ``"community"`` in a plain source checkout.
* The shells (``api.create_app``, ``cli``, ``desktop``) pass the
  ``LEDGERSTITCH_EDITION`` environment variable to :func:`configure`; this module
  never reads process state itself.
* An explicit ``community`` edition disables add-on discovery, so a dev venv
  with add-ons installed can still run as pure Community.
* Otherwise the effective edition is ``pro`` when an add-on is loaded.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

COMMUNITY = "community"
# An edition id is any short lowercase identifier (add-on builds name their own).
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
EDITIONS = ("community", "pro", "enterprise")      # the ones the core itself knows about

# License plans the core knows. Add-ons add theirs with ``registry.license_plans``.
CORE_PLAN_TIERS: dict[str, frozenset] = {"pro": frozenset({"pro"})}
ALL_TIERS_PLAN = "enterprise"                      # unlocks every tier, of every add-on
CORE_PLAN_LABELS = {"free": "Free", "community": "Community", "pro": "Pro", "enterprise": "Enterprise"}


def valid_id(value: Any) -> bool:
    """A well-formed edition, plan or tier id (lowercase letters, digits, underscores)."""
    return isinstance(value, str) and bool(_ID_RE.match(value))


def normalize_tier(tier: str) -> str:
    if not valid_id(tier) or tier == "free":
        raise ValueError(f"invalid feature tier {tier!r}")
    return tier


def _registry():
    from .plugins import registry
    return registry()


def plan_aliases(registry=None) -> dict[str, str]:
    reg = registry or _registry()
    return dict(getattr(reg, "plan_aliases", {}) or {})


def normalize_plan(plan: str, registry=None) -> str:
    return plan_aliases(registry).get(plan, plan)


def plan_tiers(registry=None) -> dict[str, frozenset]:
    """{plan: tiers it unlocks}: the core's plans merged with the add-ons' (union)."""
    reg = registry or _registry()
    out = {p: set(t) for p, t in CORE_PLAN_TIERS.items()}
    for p, t in (getattr(reg, "plan_tiers", {}) or {}).items():
        out.setdefault(p, set()).update(t)
    return {p: frozenset(t) for p, t in out.items()}


def plan_labels(registry=None) -> dict[str, str]:
    reg = registry or _registry()
    return {**CORE_PLAN_LABELS, **(getattr(reg, "plan_labels", {}) or {})}


def plans_for_tier(tier: str, registry=None) -> frozenset:
    """Which known license plans unlock a feature tier."""
    t = normalize_tier(tier)
    return frozenset({p for p, tiers in plan_tiers(registry).items() if t in tiers} | {ALL_TIERS_PLAN})


def tier_unlocked(tier: str, plan: str, registry=None) -> bool:
    if tier == COMMUNITY:
        return True
    if plan in (None, "", "free"):
        return False
    plan = normalize_plan(plan, registry)
    return plan == ALL_TIERS_PLAN or tier in plan_tiers(registry).get(plan, frozenset())


# Always on, every edition, no caps (the Community edition).
CORE_FEATURES: dict[str, str] = {
    "import": "Import CSV and PDF statements",
    "reconcile": "Duplicates, transfers and refunds",
    "rules": "Rules and built-in keyword categorization",
    "review": "Manual review",
    "vault": "Encrypted local vaults",
    "reports": "Summary and Schedule C report",
    "exports": "Excel, CSV and QuickBooks (CSV + IIF) file exports",
    "ai.local": "Local AI (Ollama on this computer)",
}

# Where "Get the full product" goes (README hero, locked cards, Community settings).
# ``{{SITE_URL}}`` is the repo-split placeholder (tools/repo_split: ``--set SITE_URL=...``);
# while it is unfilled (a plain monorepo checkout) no link is shown: a dead link is worse
# than none. ``LEDGERSTITCH_SIGNUP_URL`` overrides it (read by the shells via configure()).
# The only thing the link carries is the ``ref`` query parameter: no telemetry, no tracking.
SITE_URL = "{{SITE_URL}}"
SIGNUP_REF_APP = "app-community"
SIGNUP_REF_GITHUB = "github"
SIGNUP_URL = f"{SITE_URL}/signup?ref={SIGNUP_REF_APP}"
_signup_override: Optional[str] = None


def signup_url() -> Optional[str]:
    """The effective sign-up URL for the UI (``/api/status``), or None while unknown."""
    if _signup_override:
        return _signup_override
    if "{{" in SITE_URL:
        return None
    return SIGNUP_URL


try:  # written by the release build; absent in a source checkout
    from ._edition import EDITION as _BUILD_EDITION  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - depends on build
    _BUILD_EDITION = None
if not valid_id(_BUILD_EDITION):
    _BUILD_EDITION = None

EDITION: str = _BUILD_EDITION or COMMUNITY
_override: Optional[str] = None


def configure(edition: Optional[str] = None, *, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Set the edition explicitly (or from ``env["LEDGERSTITCH_EDITION"]``).
    Malformed values are ignored. Returns the explicit edition now in effect."""
    global _override, _signup_override
    if env is not None:
        url = (env.get("LEDGERSTITCH_SIGNUP_URL") or "").strip()
        if url.startswith("https://") and "{{" not in url:
            _signup_override = url[:500]
    if edition is None and env is not None:
        edition = (env.get("LEDGERSTITCH_EDITION") or "").strip().lower() or None
    if edition is None or valid_id(edition):
        _override = edition
    return declared_edition()


def declared_edition() -> Optional[str]:
    """The explicitly chosen edition (configure() or the build constant), if any."""
    return _override or _BUILD_EDITION


def plugins_allowed() -> bool:
    return declared_edition() != COMMUNITY


def current_edition(registry=None) -> str:
    declared = declared_edition()
    if declared:
        return declared
    reg = registry or _registry()
    return "pro" if reg.has_plugins() else COMMUNITY


def plan_of(license: Any, registry=None) -> str:
    """The effective plan of a License-like object ("free" unless valid)."""
    if license is None or not getattr(license, "valid", False):
        return "free"
    return normalize_plan(str(getattr(license, "plan", "free") or "free"), registry)


def feature_available(name: str, registry=None) -> bool:
    """Is the feature's code present in this process (core, or an installed add-on)?"""
    if name in CORE_FEATURES:
        return True
    reg = registry or _registry()
    return name in reg.features


def feature_enabled(name: str, license: Any = None, registry=None) -> bool:
    if name in CORE_FEATURES:
        return True
    reg = registry or _registry()
    f = reg.features.get(name)
    if f is None:                       # add-on not installed (or unknown feature)
        return False
    return tier_unlocked(f.tier, plan_of(license, reg), reg)


def features_status(license: Any = None, registry=None) -> dict[str, dict]:
    """{name: {available, unlocked, tier, label}} for the UI's locked states."""
    reg = registry or _registry()
    out: dict[str, dict] = {}
    for name, label in CORE_FEATURES.items():
        out[name] = {"available": True, "unlocked": True, "tier": "community", "label": label}
    for name, f in reg.features.items():
        out[name] = {"available": True, "unlocked": feature_enabled(name, license, reg),
                     "tier": f.tier, "label": f.label}
    return out


def locked_reason(name: str, license: Any = None, registry=None) -> Optional[dict]:
    """Why ``name`` can't be used right now, as the 402 body the API sends
    (``{"error", "feature", "upgrade": True, "installed"}``), or None when it is usable."""
    reg = registry or _registry()
    info = features_status(license, reg).get(name) or {}
    label = info.get("label") or name
    if not info.get("available"):
        return {"error": f"{label} is not installed in this edition.",
                "feature": name, "upgrade": True, "installed": False}
    if not info.get("unlocked"):
        return {"error": f"{label} needs a license that includes it.",
                "feature": name, "upgrade": True, "installed": True}
    return None


__all__ = ["ALL_TIERS_PLAN", "COMMUNITY", "CORE_FEATURES", "CORE_PLAN_LABELS", "CORE_PLAN_TIERS", "EDITION",
           "EDITIONS", "SIGNUP_URL", "SITE_URL", "configure", "current_edition", "declared_edition",
           "feature_available", "feature_enabled", "features_status", "locked_reason", "normalize_plan",
           "normalize_tier", "plan_aliases", "plan_labels", "plan_of", "plan_tiers", "plans_for_tier",
           "plugins_allowed", "signup_url", "tier_unlocked", "valid_id"]
