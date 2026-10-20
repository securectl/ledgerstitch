# SPDX-License-Identifier: AGPL-3.0-only
"""In-memory workspace backed by an encrypted :class:`~ledgerstitch.security.vault.Vault`.

A ``Workspace`` owns every collection for one business/client:

* ``accounts``           – ``dict[id, Account]``
* ``transactions``       – ``dict[id, Transaction]`` (insertion ordered)
* ``rules``              – ``list[Rule]``
* ``settings``           – ``dict`` (AI keys live here, encrypted at rest)
* ``imports``            – ``list[dict]`` small audit records (never the file contents)
* ``custom_categories``  – ``list[dict]`` {name, lane, schedule_c}

Every mutating method calls :meth:`Workspace.save`, which re-encrypts each
collection into the vault. Raw uploaded files are parsed in memory and never
persisted anywhere.

Teammate modules (ingest / ai / reports / license) are reached through the thin
``_xxx`` wrappers below so they are imported lazily and can be monkeypatched in
tests.

Add-ons (ledgerstitch.plugins) contribute their own settings keys and keep their
state in a namespaced vault area (:meth:`Workspace.plugin_store`). The core has
no usage caps.
"""
from __future__ import annotations

import copy
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import (
    Account,
    AccountKind,
    Direction,
    Lane,
    Rule,
    Source,
    Status,
    Transaction,
)
from .taxonomy import DEFAULT_CATEGORIES, SCHEDULE_C_LINES, UNCATEGORIZED

log = logging.getLogger(__name__)

COLLECTIONS = ("accounts", "transactions", "rules", "settings", "imports", "custom_categories")

# Core settings. Add-ons register their own keys (e.g. AI provider keys) through
# ledgerstitch.plugins; see Workspace.default_settings().
CORE_DEFAULT_SETTINGS: dict[str, Any] = {
    # Community local AI ("ai.local"): Ollama on this computer only. Add-ons may add
    # more provider ids (e.g. Pro: anthropic, openai, ollama_cloud).
    "ai_enabled": False,
    "provider": "off",              # off | ollama | (add-on providers)
    "model": "",
    "ollama_url": "http://127.0.0.1:11434",
    "business_name": "",
    "business_profile": "",         # optional one-line description (shared with an AI add-on)
    "tax_year": None,
    "auto_lock_minutes": 30,
    "license_key": None,
    "last_backup_at": None,         # ISO time of the last encrypted backup (read-only; set by backups)
}
BACKUP_REMINDER_DAYS = 30
DEFAULT_SETTINGS = CORE_DEFAULT_SETTINGS     # backwards-compatible name

# Settings the API may change through PUT /api/settings (license goes via /api/license).
CORE_EDITABLE_SETTINGS = {"business_name", "business_profile", "tax_year", "auto_lock_minutes",
                          "ai_enabled", "provider", "model", "ollama_url"}
CORE_PROVIDERS = ("off", "ollama")
EDITABLE_SETTINGS = CORE_EDITABLE_SETTINGS   # backwards-compatible name
SECRET_FIELDS = ("license_key",)

TXN_EDITABLE = {"category", "lane", "schedule_c", "note", "status"}

EXPORT_README = """LedgerStitch data export

This archive is NOT encrypted. It holds everything in your workspace:
  ledgerstitch-data.json  accounts, imports, transactions, rules, categories,
                          settings (no passphrase or license key) and add-on data
  transactions.csv        every transaction, for spreadsheets
  receipts/               receipt images you added

Keep it somewhere safe, and delete it when you no longer need it.
Your passphrase is not included, and LedgerStitch cannot recover it.
"""


# --------------------------------------------------------------------------- errors
class StoreError(Exception):
    """Base error for workspace operations (maps to HTTP 4xx)."""


class LimitError(StoreError):
    """Feature not available on this plan (HTTP 402). The core itself has no usage caps."""


class NotFound(StoreError):
    """Unknown id (HTTP 404)."""


class ValidationError(StoreError):
    """Bad input (HTTP 422)."""


class NeedsMapping(ValidationError):
    """CSV format not recognised; the user must map the columns manually."""


# --------------------------------------------------------------------------- lazy teammate hooks
def _detect(filename: str, data: bytes):
    from .ingest.detect import detect
    return detect(filename, data)


def _parse_csv(data: bytes, profile, account_id: str, import_id: str):
    from .ingest.csv_parser import parse_csv_detailed
    return parse_csv_detailed(data, profile, account_id, import_id)


def _parse_pdf(data: bytes, account_id: str, import_id: str):
    from .ingest.pdf_parser import parse_pdf_detailed
    return parse_pdf_detailed(data, account_id, import_id)


def _preview_csv(data: bytes, rows: int = 8):
    from .ingest.dispatch import preview_any
    return preview_any(data, rows)


def _parse_any(filename: str, data: bytes, profile, account_id: str, import_id: str):
    from .ingest.dispatch import parse_any
    return parse_any(filename, data, profile, account_id, import_id)


def _is_tabular(filename: str, data: bytes) -> bool:
    from .ingest.dispatch import is_tabular
    return is_tabular(filename, data)


def _learned_lookup(filename: str, data: bytes, learned: list):
    from .ingest.learning import lookup
    return lookup(filename, data, learned)


def _learn_from_import(learned: list, filename: str, data: bytes, profile, txns, warnings, confirmed: bool):
    from .ingest import learning
    if confirmed:
        return learning.record_confirmation(learned, filename, data, profile.mapping, profile.institution,
                                            profile.kind, txns)
    return learning.record_success(learned, filename, data, profile, txns, warnings)


def _normalize_merchant(description: str) -> str:
    from .ingest.normalize import normalize_merchant
    return normalize_merchant(description)


def _reconcile(txns, accounts):
    from .ingest.reconcile import reconcile
    return reconcile(txns, accounts)


def _categorize(txns, rules, settings, categories, license=None, plugin_store=None):
    from .ai.categorizer import categorize
    return categorize(txns, rules, settings, categories, license=license, plugin_store=plugin_store)


def _plugins():
    from . import plugins
    return plugins.load()


def _build_ai_payload(txns):
    from .ai.categorizer import build_ai_payload
    return build_ai_payload(txns)


def _apply_rules(txns, rules) -> int:
    from .ai.rules import apply_rules
    return apply_rules(txns, rules)


def _apply_heuristics(txns) -> int:
    from .ai.rules import apply_heuristics
    return apply_heuristics(txns)


def _learn_rule(txn, category, lane, schedule_c) -> Rule:
    from .ai.rules import learn_rule
    return learn_rule(txn, category, lane, schedule_c)


def _current_plan(license_key):
    from .security.license import current_plan
    return current_plan(license_key)


def _verify_license(key: str):
    from .security.license import verify
    return verify(key)


def _make_mapping(mapping: Any):
    from .ingest.detect import ColumnMapping
    if mapping is None or isinstance(mapping, ColumnMapping):
        return mapping
    if isinstance(mapping, dict):
        allowed = set(ColumnMapping.__dataclass_fields__)
        clean = {k: v for k, v in mapping.items() if k in allowed and v not in ("",)}
        if not clean.get("date") or not clean.get("description"):
            raise ValidationError("Column mapping needs at least a date and a description column.")
        if not (clean.get("amount") or clean.get("debit") or clean.get("credit")):
            raise ValidationError("Column mapping needs an amount column (or debit/credit columns).")
        try:
            return ColumnMapping(**clean)
        except TypeError as e:
            raise ValidationError(f"Invalid column mapping: {e}") from None
    raise ValidationError("Column mapping must be an object.")


def _make_profile(base, mapping, institution: Optional[str] = None):
    from .ingest.detect import FormatProfile
    return FormatProfile(
        id="manual",
        institution=institution or (getattr(base, "institution", None) or "Unknown"),
        kind=getattr(base, "kind", None) or AccountKind.OTHER,
        mapping=mapping,
        confidence=1.0,
        account_hint=getattr(base, "account_hint", None),
    )


# --------------------------------------------------------------------------- helpers
def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:max_len].strip("-") or "account"


def account_to_dict(a: Account) -> dict:
    return {"id": a.id, "label": a.label, "institution": a.institution,
            "kind": _enum_val(a.kind), "last4": a.last4}


def account_from_dict(d: dict) -> Account:
    return Account(id=d["id"], label=d.get("label") or d["id"], institution=d.get("institution") or "Unknown",
                   kind=AccountKind(d.get("kind") or "other"), last4=d.get("last4"))


def _enum_val(v):
    return v.value if hasattr(v, "value") else v


def to_jsonable(obj: Any) -> Any:
    """Convert dataclasses / enums / decimals / dates into JSON-friendly values."""
    if isinstance(obj, Transaction):
        return obj.to_dict()
    if isinstance(obj, Rule):
        return obj.to_dict()
    if isinstance(obj, Account):
        return account_to_dict(obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(_enum_val(k)): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, "value") and not isinstance(obj, (str, int, float, bool)):
        return obj.value
    return obj


def _coerce_lane(v) -> Lane:
    if isinstance(v, Lane):
        return v
    try:
        return Lane(str(v).lower())
    except ValueError:
        raise ValidationError(f"Unknown lane '{v}'. Use one of: {', '.join(l.value for l in Lane)}.") from None


def _coerce_status(v) -> Status:
    if isinstance(v, Status):
        return v
    try:
        return Status(str(v).lower())
    except ValueError:
        raise ValidationError(f"Unknown status '{v}'. Use one of: {', '.join(s.value for s in Status)}.") from None


def _coerce_schedule_c(v) -> Optional[str]:
    if v in (None, ""):
        return None
    if v not in SCHEDULE_C_LINES:
        raise ValidationError(f"Unknown Schedule C line '{v}'.")
    return v


@dataclass
class ImportResult:
    import_id: str
    filename: str
    detected: dict
    account_id: str
    count: int                 # transactions parsed from the file
    new: int                   # transactions actually added (re-import => 0)
    warnings: list[str] = field(default_factory=list)
    reconciled: Optional[bool] = None   # PDF statement totals check (None = not checkable)
    reconcile_difference: Optional[str] = None   # "12.34": amount the parsed rows don't account for
    reconcile: dict = field(default_factory=dict)
    categorize: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return to_jsonable(self)


# --------------------------------------------------------------------------- workspace
# ---------------------------------------------------------------------- receipts
class ReceiptsMixin:
    """Receipt storage + matching for Workspace. Images and extracted text are stored
    under their own encrypted vault keys; only the record index is kept in memory."""

    def _live_txns(self):
        return [t for t in self.transactions.values() if not t.duplicate_of]

    def _rematch_receipts(self) -> None:
        from .receipts import rematch_records
        rematch_records(list(self.receipts.values()), self._live_txns(), self.accounts)

    def add_receipt(self, filename: str, data: bytes, reader):
        """Store a receipt read by an add-on and return its record.

        ``reader(name, data, txns, accounts) -> (ReceiptRecord, text)`` builds the record
        (and its match against ``txns``, the transactions no other receipt claims); ``text``
        must already have card numbers masked. The file and the text are stored encrypted."""
        from .receipts import ReceiptRecord, receipt_id
        name = Path(filename or "receipt").name[:200]
        rid = receipt_id(data)
        with self.lock:
            if rid in self.receipts:
                return self.receipts[rid]          # same file dropped twice
            reserved = {r.txn_id for r in self.receipts.values() if r.txn_id}
            txns = [t for t in self._live_txns() if t.id not in reserved]
        rec, text = reader(name, data, txns, self.accounts)   # CPU-bound, outside the lock
        if not isinstance(rec, ReceiptRecord) or rec.id != rid:
            raise ValidationError(f"{name}: the receipt could not be read.")
        with self.lock:
            self.vault.put(f"receipt:{rid}:file", data)
            self.vault.put(f"receipt:{rid}:text", (text or "").encode())
            self.receipts[rid] = rec
            self.save()
        return rec

    def list_receipts(self) -> list:
        return sorted(self.receipts.values(), key=lambda r: r.added or "", reverse=True)

    def get_receipt(self, rid: str):
        r = self.receipts.get(rid)
        if r is None:
            raise NotFound("Receipt not found.")
        return r

    def receipt_image(self, rid: str) -> tuple[bytes, str]:
        r = self.get_receipt(rid)
        data = self.vault.get(f"receipt:{rid}:file")
        if data is None:
            raise NotFound("Receipt image missing.")
        return data, r.mime or "application/octet-stream"

    def update_receipt(self, rid: str, fields: dict):
        allowed = {"merchant", "date", "total", "card_last4", "note"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValidationError(f"Cannot edit: {', '.join(sorted(unknown))}.")
        with self.lock:
            r = self.get_receipt(rid)
            try:
                if "merchant" in fields:
                    r.merchant = (str(fields["merchant"] or "").strip()[:120]) or None
                if "date" in fields:
                    r.date = date.fromisoformat(fields["date"]) if fields["date"] else None
                if "total" in fields:
                    r.total = Decimal(str(fields["total"]).replace("$", "").replace(",", "")) \
                        if fields["total"] not in (None, "") else None
                if "card_last4" in fields:
                    v = str(fields["card_last4"] or "").strip()
                    if v and not (len(v) == 4 and v.isdigit()):
                        raise ValueError("card_last4 must be 4 digits")
                    r.card_last4 = v or None
                if "note" in fields:
                    r.note = str(fields["note"] or "")[:500]
            except (ValueError, TypeError, ArithmeticError) as e:
                raise ValidationError(f"Invalid receipt field: {e}") from None
            if r.status != "manual":
                self._rematch_receipts()
            self.save()
            return r

    def link_receipt(self, rid: str, txn_id: Optional[str]):
        with self.lock:
            r = self.get_receipt(rid)
            if txn_id:
                self.get_transaction(txn_id)            # 404 if unknown
                for other in self.receipts.values():   # one receipt per transaction
                    if other.id != rid and other.txn_id == txn_id:
                        other.txn_id, other.status = None, "unmatched"
                r.txn_id, r.status = txn_id, "manual"
            else:
                # The user said "not this transaction": don't let auto-matching relink it.
                r.txn_id, r.status = None, "unmatched"
            self.save()
            return r

    def delete_receipt(self, rid: str) -> None:
        with self.lock:
            self.get_receipt(rid)
            for k in (f"receipt:{rid}:file", f"receipt:{rid}:text"):
                self.vault.delete(k)
            del self.receipts[rid]
            self.save()

    def receipt_for_txn(self) -> dict[str, str]:
        return {r.txn_id: r.id for r in self.receipts.values()
                if r.txn_id and r.status in ("matched", "manual")}


class Workspace(ReceiptsMixin):
    def __init__(self, vault) -> None:
        self.vault = vault
        self.lock = threading.RLock()
        self.accounts: dict[str, Account] = {
            d["id"]: account_from_dict(d) for d in (vault.get_json("accounts", []) or [])
        }
        self.transactions: dict[str, Transaction] = {}
        for d in vault.get_json("transactions", []) or []:
            t = Transaction.from_dict(d)
            self.transactions[t.id] = t
        self.rules: list[Rule] = [Rule.from_dict(d) for d in (vault.get_json("rules", []) or [])]
        self.registry = _plugins()
        # Unknown stored keys (e.g. from an add-on that is not installed now) are kept
        # untouched so re-installing the add-on restores them; they are never exposed.
        self.settings: dict = {**self.default_settings(), **(vault.get_json("settings", {}) or {})}
        if not self.settings.get("business_name"):
            self.settings["business_name"] = getattr(vault, "name", "") or ""
        self.imports: list[dict] = list(vault.get_json("imports", []) or [])
        self.custom_categories: list[dict] = list(vault.get_json("custom_categories", []) or [])
        self.learned_formats: list[dict] = list(vault.get_json("learned_formats", []) or [])
        # Receipts: index of records; image bytes and extracted text live under their own encrypted keys
        from .receipts import ReceiptRecord
        self.receipts: dict[str, ReceiptRecord] = {
            d["id"]: ReceiptRecord.from_dict(d) for d in (vault.get_json("receipts", []) or [])}

    # ------------------------------------------------------------------ persistence
    def save(self) -> None:
        with self.lock:
            self.vault.put_json("accounts", [account_to_dict(a) for a in self.accounts.values()])
            self.vault.put_json("transactions", [t.to_dict() for t in self.transactions.values()])
            self.vault.put_json("rules", [r.to_dict() for r in self.rules])
            self.vault.put_json("settings", self.settings)
            self.vault.put_json("imports", self.imports)
            self.vault.put_json("custom_categories", self.custom_categories)
            self.vault.put_json("learned_formats", self.learned_formats)
            self.vault.put_json("receipts", [r.to_dict() for r in self.receipts.values()])

    @property
    def name(self) -> str:
        return getattr(self.vault, "name", "") or ""

    # ------------------------------------------------------------------ add-ons
    def default_settings(self) -> dict:
        return {**copy.deepcopy(CORE_DEFAULT_SETTINGS), **copy.deepcopy(self.registry.setting_defaults)}

    def plugin_store(self, namespace: str):
        """Namespaced encrypted storage for an add-on (keys ``plugin:<ns>:<key>``)."""
        from .plugins import PluginStore
        return PluginStore(self.vault, namespace, legacy=self.registry.legacy_keys.get(namespace),
                           lock=self.lock)

    def feature_enabled(self, name: str) -> bool:
        from .editions import feature_enabled
        return feature_enabled(name, self.license(), self.registry)

    # ------------------------------------------------------------------ license
    def license(self):
        return _current_plan(self.settings.get("license_key"))

    def is_paid(self) -> bool:
        lic = self.license()
        return bool(getattr(lic, "valid", False)) and getattr(lic, "plan", "free") not in ("free", "", None)

    def limits(self) -> Optional[dict]:
        """Usage limits: always None. The core has no caps in any edition."""
        return None

    def set_license(self, key: str):
        key = (key or "").strip()
        if not key:
            self.settings["license_key"] = None
            self.save()
            return self.license()
        lic = _verify_license(key)
        if not getattr(lic, "valid", False):
            raise ValidationError("That license key is not valid (or has expired).")
        self.settings["license_key"] = key
        self.save()
        return lic

    # ------------------------------------------------------------------ categories
    def categories(self) -> list[dict]:
        out = [{"name": c.name, "lane": c.lane.value, "schedule_c": c.schedule_c,
                "schedule_c_label": SCHEDULE_C_LINES.get(c.schedule_c or ""), "custom": False}
               for c in DEFAULT_CATEGORIES]
        seen = {c["name"] for c in out}
        for c in self.custom_categories:
            if c["name"] in seen:
                continue
            seen.add(c["name"])
            out.append({"name": c["name"], "lane": c["lane"], "schedule_c": c.get("schedule_c"),
                        "schedule_c_label": SCHEDULE_C_LINES.get(c.get("schedule_c") or ""), "custom": True})
        return out

    def category_info(self, name: str) -> Optional[dict]:
        for c in self.categories():
            if c["name"] == name:
                return c
        return None

    def add_category(self, name: str, lane, schedule_c: Optional[str] = None) -> dict:
        name = (name or "").strip()
        if not name or len(name) > 80:
            raise ValidationError("Category name must be 1-80 characters.")
        lane = _coerce_lane(lane)
        schedule_c = _coerce_schedule_c(schedule_c)
        with self.lock:
            if self.category_info(name):
                raise ValidationError(f"Category '{name}' already exists.")
            self.custom_categories.append({"name": name, "lane": lane.value, "schedule_c": schedule_c})
            self.save()
        return self.category_info(name)

    # ------------------------------------------------------------------ accounts
    def _account_for(self, profile, account_label: Optional[str]) -> tuple[Account, bool]:
        institution = (getattr(profile, "institution", None) or "Unknown").strip() or "Unknown"
        kind = getattr(profile, "kind", None) or AccountKind.OTHER
        if not isinstance(kind, AccountKind):
            try:
                kind = AccountKind(kind)
            except ValueError:
                kind = AccountKind.OTHER
        hint = getattr(profile, "account_hint", None)
        hint = re.sub(r"[^0-9A-Za-z]", "", str(hint))[-4:] if hint else None
        label = (account_label or "").strip() or None

        if hint:
            acc_id = f"{slugify(institution, 24)}-{hint.lower()}"
        elif label:
            acc_id = slugify(label)
        else:
            acc_id = f"{slugify(institution, 24)}-{kind.value.replace('_', '-')}"

        if acc_id in self.accounts:
            return self.accounts[acc_id], False
        if not label:
            label = f"{institution} ••{hint}" if hint else f"{institution} {kind.value.replace('_', ' ').title()}"
        return Account(id=acc_id, label=label, institution=institution, kind=kind,
                       last4=hint if hint and hint.isdigit() else None), True

    def update_account(self, account_id: str, **fields) -> Account:
        with self.lock:
            acc = self.accounts.get(account_id)
            if not acc:
                raise NotFound(f"No account '{account_id}'.")
            if fields.get("label") is not None:
                label = str(fields["label"]).strip()
                if not label or len(label) > 80:
                    raise ValidationError("Account label must be 1-80 characters.")
                acc.label = label
            if fields.get("institution") is not None:
                acc.institution = str(fields["institution"]).strip() or acc.institution
            if fields.get("kind") is not None:
                try:
                    acc.kind = AccountKind(fields["kind"])
                except ValueError:
                    raise ValidationError(f"Unknown account kind '{fields['kind']}'.") from None
            if "last4" in fields and fields["last4"] is not None:
                l4 = str(fields["last4"]).strip()
                if l4 and not re.fullmatch(r"\d{4}", l4):
                    raise ValidationError("last4 must be 4 digits.")
                acc.last4 = l4 or None
            self.save()
            return acc

    # ------------------------------------------------------------------ imports
    def preview_import(self, filename: str, data: bytes) -> dict:
        if not _is_tabular(filename, data):
            raise ValidationError("Manual column mapping is only available for CSV and Excel files.")
        prev = _preview_csv(data, 8)
        try:
            prof = _detect(filename, data)
            detected = self._profile_summary(prof)
        except Exception:  # detection is best-effort here
            detected = None
        return {**prev, "detected": detected}

    @staticmethod
    def _profile_summary(profile) -> dict:
        mapping = getattr(profile, "mapping", None)
        return {
            "profile": getattr(profile, "id", None),
            "institution": getattr(profile, "institution", None),
            "kind": _enum_val(getattr(profile, "kind", None)),
            "confidence": getattr(profile, "confidence", None),
            "account_hint": getattr(profile, "account_hint", None),
            "mapping": to_jsonable(mapping) if mapping is not None else None,
            "recognition": to_jsonable(getattr(profile, "recognition", None)),
        }

    def add_import(self, filename: str, data: bytes, account_label: Optional[str] = None,
                   mapping: Any = None) -> ImportResult:
        filename = Path(filename or "upload").name[:200]
        if not data:
            raise ValidationError(f"{filename}: the file is empty.")
        import_id = uuid.uuid4().hex[:12]
        is_pdf = not _is_tabular(filename, data)   # PDF/OFX/QIF/ISO: no column mapping
        warnings: list[str] = []
        reconciled = None   # do parsed totals match the statement's printed totals?
        difference = None

        # 1. detect (learned formats for this workspace first) ---------------
        try:
            profile = _learned_lookup(filename, data, self.learned_formats) \
                if mapping is None and not is_pdf else None
        except Exception:
            profile = None
        try:
            profile = profile or _detect(filename, data)
        except Exception as e:
            if is_pdf or mapping is None:
                raise ValidationError(f"{filename}: could not read this file ({type(e).__name__}).") from None
            profile = None
        if mapping is not None and not is_pdf:
            profile = _make_profile(profile, _make_mapping(mapping))
        if not is_pdf and getattr(profile, "mapping", None) is None:
            raise NeedsMapping(f"{filename}: we don't recognise this CSV layout yet. "
                               "Map the columns manually to import it.")

        with self.lock:
            account, is_new_account = self._account_for(profile, account_label)

            # 2. parse (in memory only) --------------------------------------
            try:
                parsed, p_warnings, p_info = _parse_any(filename, data, profile, account.id, import_id)
                warnings.extend(p_warnings or [])
                reconciled = (p_info or {}).get("reconciled")
                difference = (p_info or {}).get("difference") if reconciled is not None else None
            except StoreError:
                raise
            except Exception as e:
                log.warning("parse failed for an upload: %s", type(e).__name__)
                raise ValidationError(f"{filename}: could not parse this file ({e}).") from None
            if not parsed:
                warnings.append("No transactions were found in this file.")

            # 3. merge by id (re-import = no-op) -------------------------------
            new_txns: list[Transaction] = []
            seen: set[str] = set()
            for t in parsed:
                if t.id in self.transactions or t.id in seen:
                    continue
                seen.add(t.id)
                t.account_id = t.account_id or account.id
                new_txns.append(t)

            rec_report: dict = {}
            cat_report: dict = {}
            cat_targets: set[str] = set()
            if new_txns:
                if is_new_account:
                    self.accounts[account.id] = account
                for t in new_txns:
                    if not t.merchant:
                        try:
                            t.merchant = _normalize_merchant(t.description)
                        except Exception:
                            t.merchant = " ".join(t.description.split()).upper()[:60]
                    self.transactions[t.id] = t

                # 5. reconcile across ALL transactions -----------------------
                rec_report = to_jsonable(self._run_reconcile())

                # 6. categorize only new / unreviewed: after the lock is released
                # (AI is slow; the app must stay responsive) ------
                new_ids = {t.id for t in new_txns}
                cat_targets = {t.id for t in self._categorizable() if t.id in new_ids or t.category is None}

            record = {
                "import_id": import_id,
                "filename": filename,
                "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "profile": getattr(profile, "id", None),
                "institution": account.institution,
                "account_id": account.id,
                "count": len(parsed),
                "new": len(new_txns),
                "warnings": warnings,
                "reconciled": reconciled,
                "reconcile_difference": difference,
            }
            self.imports.append(record)
            if parsed and not is_pdf:
                try:
                    self.learned_formats, _ = _learn_from_import(self.learned_formats, filename, data, profile,
                                                                 parsed, warnings, mapping is not None)
                except Exception as e:  # learning is best-effort; never fail an import
                    log.warning("format learning skipped: %s", type(e).__name__)
            if new_txns and self.receipts:
                self._rematch_receipts()
            self.save()

        if cat_targets:
            # also re-runs reconcile, so matched refunds copy their purchase's final category
            cat_report = to_jsonable(self.categorize(cat_targets))
        return ImportResult(import_id=import_id, filename=filename,
                            detected={**self._profile_summary(profile), "account_id": account.id,
                                      "account_label": account.label},
                            account_id=account.id, count=len(parsed), new=len(new_txns),
                            warnings=warnings, reconciled=reconciled, reconcile_difference=difference,
                            reconcile=rec_report,
                            categorize=cat_report)

    # ------------------------------------------------------------------ reconcile / categorize
    def _run_reconcile(self):
        return _reconcile(list(self.transactions.values()), self.accounts)

    def reconcile(self):
        with self.lock:
            report = self._run_reconcile()
            self.save()
            return report

    def _categorizable(self) -> list[Transaction]:
        """Transactions the categorizer may (re)label: not user-decided, not reconciled away."""
        out = []
        for t in self.transactions.values():
            if t.status == Status.APPROVED or t.source == Source.USER:
                continue
            if t.duplicate_of or t.transfer_group or t.refund_of:
                continue
            if t.category == "Refund" and t.lane == Lane.TRANSFER:
                continue
            out.append(t)
        return out

    def _run_categorize(self, txns: list[Transaction], result=None):
        """Categorize ``txns`` in place (caller holds the lock), or, with ``result``,
        only register the categories a finished run proposed."""
        if result is None:
            result = _categorize(txns, self.rules, self.settings, self.categories(),
                                 license=self.license(), plugin_store=self.plugin_store)
        # Register AI-proposed categories so the UI can show/select them.
        defs = getattr(result, "new_category_defs", None) or {}
        for name in getattr(result, "new_categories", None) or []:
            if name and not self.category_info(name):
                d = defs.get(name) or {}
                sample = next((t for t in txns if t.category == name), None)
                lane = d.get("lane") or (sample.lane.value if sample else Lane.REVIEW.value)
                sc = d.get("schedule_c", sample.schedule_c if sample else None)
                self.custom_categories.append({"name": name, "lane": _enum_val(lane), "schedule_c": sc})
        return result

    def categorize(self, ids: Optional[set[str]] = None):
        """Rules, heuristics, then AI. The slow part (AI models: network)
        runs on copies WITHOUT the workspace lock, so status polling, the ledger and
        edits stay responsive. Results are applied under the lock, except to
        transactions the user changed or deleted in the meantime."""
        run_lock = self.__dict__.setdefault("_categorize_run_lock", threading.Lock())
        with run_lock:                                  # one categorization run at a time
            with self.lock:
                originals = [t for t in self._categorizable() if ids is None or t.id in ids]
                before = {t.id: t.to_dict() for t in originals}
                work = [copy.deepcopy(t) for t in originals]
                rules, settings = copy.deepcopy(self.rules), copy.deepcopy(self.settings)
                categories, lic = self.categories(), self.license()
            result = _categorize(work, rules, settings, categories, license=lic,
                                 plugin_store=self.plugin_store)
            with self.lock:
                for c in work:
                    t = self.transactions.get(c.id)
                    if t is None or t.to_dict() != before[c.id]:
                        continue                        # edited/deleted meanwhile: the user wins
                    for f in ("category", "lane", "schedule_c", "confidence", "source", "status", "ai_model"):
                        setattr(t, f, getattr(c, f))
                self._run_categorize(work, result)
                self._run_reconcile()
                self.save()
                return result

    def ai_preview(self) -> list[str]:
        """Exactly the descriptors categorize() would send to the AI provider."""
        with self.lock:
            # categorize() first applies rules + heuristics and only then asks the AI
            # about what is left, so simulate that on throw-away copies.
            sim = [copy.deepcopy(t) for t in self._categorizable() if not t.duplicate_of]
            try:
                _apply_rules(sim, self.rules)
                _apply_heuristics(sim)
            except ImportError:
                pass
            return list(_build_ai_payload(sim))

    # ------------------------------------------------------------------ transactions
    def get_transaction(self, txn_id: str) -> Transaction:
        t = self.transactions.get(txn_id)
        if not t:
            raise NotFound(f"No transaction '{txn_id}'.")
        return t

    def list_transactions(self, filters: Optional[dict] = None) -> tuple[int, list[Transaction]]:
        f = {k: v for k, v in (filters or {}).items() if v not in (None, "")}
        q = str(f.get("q", "")).upper().strip()
        year = int(f["year"]) if "year" in f else None
        include_dupes = bool(f.get("include_duplicates", True))
        d_from, d_to = (self._filter_date(f.get(k), k) for k in ("date_from", "date_to"))
        direction = f.get("direction")
        if direction not in (None, "in", "out"):
            raise ValidationError("direction must be 'in' or 'out'.")
        items = []
        for t in self.transactions.values():
            if (d_from and t.date < d_from) or (d_to and t.date > d_to):
                continue
            if direction and t.direction.value != direction:
                continue
            if "lane" in f and t.lane.value != f["lane"]:
                continue
            if "category" in f and (t.category or UNCATEGORIZED) != f["category"]:
                continue
            if "account" in f and t.account_id != f["account"]:
                continue
            if "status" in f and t.status.value != f["status"]:
                continue
            if year and t.date.year != year:
                continue
            if not include_dupes and t.duplicate_of:
                continue
            if q and q not in t.description.upper() and q not in (t.merchant or "").upper() \
                    and q not in (t.category or "").upper() and q not in (t.note or "").upper() \
                    and q != str(t.amount):
                continue
            items.append(t)
        items.sort(key=lambda t: (t.date, t.account_id, t.seq), reverse=True)
        sort_key = f.get("sort")
        if sort_key and sort_key != "date":
            keyfns = {
                "amount": lambda t: t.signed_amount,
                "account_id": lambda t: (self.accounts[t.account_id].label if t.account_id in self.accounts
                                         else t.account_id).upper(),
                "description": lambda t: t.description.upper(),
                "merchant": lambda t: (t.merchant or "").upper(),
                "category": lambda t: (t.category or UNCATEGORIZED).upper(),
                "lane": lambda t: t.lane.value,
                "status": lambda t: t.status.value,
                "schedule_c": lambda t: t.schedule_c or "",
            }
            if sort_key not in keyfns:
                raise ValidationError(f"Cannot sort by '{sort_key}'.")
            # stable sort keeps newest-first as the tie-breaker
            items.sort(key=keyfns[sort_key], reverse=f.get("order") == "desc")
        elif f.get("order") == "asc":
            items.reverse()
        total = len(items)
        offset = max(0, int(f.get("offset", 0)))
        limit = f.get("limit")
        if limit is not None:
            items = items[offset: offset + max(0, int(limit))]
        else:
            items = items[offset:]
        return total, items

    @staticmethod
    def _filter_date(v, name: str) -> Optional[date]:
        if v in (None, ""):
            return None
        if isinstance(v, date):
            return v
        try:
            return date.fromisoformat(str(v).strip()[:10])
        except ValueError:
            raise ValidationError(f"{name} must be a date like 2025-03-31.") from None

    def _apply_fields(self, t: Transaction, fields: dict) -> None:
        unknown = set(fields) - TXN_EDITABLE
        if unknown:
            raise ValidationError(f"Cannot edit: {', '.join(sorted(unknown))}.")
        decision = False
        ai_before = (t.ai_model, t.category, t.lane) if t.source == Source.AI and t.ai_model else None
        if "category" in fields and fields["category"] is not None:
            cat = str(fields["category"]).strip()
            info = self.category_info(cat)
            if not info:
                raise ValidationError(f"Unknown category '{cat}'. Add it first.")
            t.category = cat
            decision = True
            if fields.get("lane") is None:
                t.lane = Lane(info["lane"])
            if "schedule_c" not in fields:
                t.schedule_c = info["schedule_c"]
        if fields.get("lane") is not None:
            t.lane = _coerce_lane(fields["lane"])
            decision = True
        if "schedule_c" in fields:
            t.schedule_c = _coerce_schedule_c(fields["schedule_c"])
            decision = True
        if "note" in fields and fields["note"] is not None:
            t.note = str(fields["note"])[:500]
        if decision:
            t.source = Source.USER
            t.confidence = 1.0
            if t.category is None:
                t.category = UNCATEGORIZED
        if fields.get("status") is not None:
            t.status = _coerce_status(fields["status"])
        elif decision:
            t.status = Status.APPROVED
        # The user's verdict on an AI suggestion is ground truth for an AI add-on's
        # learning: it receives only the model id and agree/disagree.
        if ai_before is not None and (decision or t.status == Status.APPROVED):
            model, cat, lane = ai_before
            agreed = t.category == cat and t.lane == lane
            for ns, hook in self.registry.feedback_hooks:
                try:
                    hook(self.plugin_store(ns), model, agreed)
                except Exception as e:  # noqa: BLE001 - add-on bookkeeping must not block edits
                    log.warning("AI feedback hook %s failed: %s", ns, type(e).__name__)
            t.source = Source.USER

    def update_transaction(self, txn_id: str, remember: bool = False, **fields) -> tuple[Transaction, int]:
        """Edit one transaction. With ``remember=True`` a merchant rule is learned and
        re-applied to the other not-yet-approved transactions of the same merchant.
        Returns ``(transaction, n_other_transactions_changed)``."""
        with self.lock:
            t = self.get_transaction(txn_id)
            self._apply_fields(t, fields)
            changed = 0
            if remember:
                if not t.category:
                    raise ValidationError("Pick a category before asking LedgerStitch to remember it.")
                rule = _learn_rule(t, t.category, t.lane, t.schedule_c)
                self.rules = [r for r in self.rules if r.id != rule.id] + [rule]
                changed = self._reapply_rule(rule, exclude={t.id}, merchant=t.merchant)
            self._run_reconcile()  # refunds follow their purchase's new category
            self.save()
            return t, changed

    def _reapply_rule(self, rule: Rule, exclude: set[str], merchant: Optional[str] = None) -> int:
        targets = [o for o in self.transactions.values()
                   if o.id not in exclude and o.status != Status.APPROVED and o.source != Source.USER
                   and not o.duplicate_of and not o.transfer_group
                   and (merchant is None or (o.merchant or "") == merchant)]
        if not targets:
            return 0
        before = {o.id: (o.category, o.lane, o.schedule_c) for o in targets}
        _apply_rules(targets, [rule])
        return sum(1 for o in targets if (o.category, o.lane, o.schedule_c) != before[o.id])

    def bulk_update(self, ids: Iterable[str], **fields) -> int:
        with self.lock:
            txns = [self.get_transaction(i) for i in ids]
            for t in txns:
                self._apply_fields(t, dict(fields))
            self._run_reconcile()
            self.save()
            return len(txns)

    # ------------------------------------------------------------------ rules
    def add_rule(self, rule: Rule | dict, apply: bool = True) -> tuple[Rule, int]:
        if isinstance(rule, dict):
            pattern = str(rule.get("pattern") or "").strip()
            if not pattern:
                raise ValidationError("Rule pattern is required.")
            cat = str(rule.get("category") or "").strip()
            info = self.category_info(cat)
            if not info:
                raise ValidationError(f"Unknown category '{cat}'.")
            fld = rule.get("field") or "merchant"
            if fld not in ("merchant", "description"):
                raise ValidationError("Rule field must be 'merchant' or 'description'.")
            rule = Rule(pattern=pattern, category=cat,
                        lane=_coerce_lane(rule.get("lane") or info["lane"]),
                        schedule_c=_coerce_schedule_c(rule.get("schedule_c", info["schedule_c"])),
                        field=fld, account_id=rule.get("account_id") or None, created_by=Source.USER)
        with self.lock:
            self.rules = [r for r in self.rules if r.id != rule.id] + [rule]
            changed = self._reapply_rule(rule, exclude=set()) if apply else 0
            self.save()
            return rule, changed

    def delete_rule(self, rule_id: str) -> None:
        with self.lock:
            before = len(self.rules)
            self.rules = [r for r in self.rules if r.id != rule_id]
            if len(self.rules) == before:
                raise NotFound(f"No rule '{rule_id}'.")
            self.save()

    # ------------------------------------------------------------------ deleting & purging
    def _drop_transactions(self, ids: set[str]) -> int:
        """Remove transactions and every link to them; caller holds the lock and saves."""
        ids = {i for i in ids if i in self.transactions}
        if not ids:
            return 0
        groups = {self.transactions[i].transfer_group for i in ids} - {None}
        for i in ids:
            del self.transactions[i]
        for t in self.transactions.values():
            if t.duplicate_of in ids:
                t.duplicate_of = None
            if t.refund_of in ids:
                t.refund_of = None
            if t.transfer_group in groups:
                t.transfer_group = None
        for r in self.receipts.values():
            if r.txn_id in ids:
                r.txn_id, r.status = None, "unmatched"
            if r.candidates:
                r.candidates = [c for c in r.candidates if c.get("txn_id") not in ids]
        self._run_reconcile()
        if self.receipts:
            self._rematch_receipts()
        return len(ids)

    def _drop_empty_accounts(self, candidates: Iterable[str]) -> list[str]:
        used = {t.account_id for t in self.transactions.values()} | {i.get("account_id") for i in self.imports}
        gone = [a for a in set(candidates) if a in self.accounts and a not in used]
        for a in gone:
            del self.accounts[a]
        self.rules = [r for r in self.rules if not (r.account_id and r.account_id in gone)]
        return gone

    def delete_import(self, import_id: str) -> dict:
        """Delete an imported file: its record and every transaction it added."""
        with self.lock:
            rec = next((i for i in self.imports if i.get("import_id") == import_id), None)
            if rec is None:
                raise NotFound("Import not found.")
            ids = {t.id for t in self.transactions.values() if t.import_id == import_id}
            removed = self._drop_transactions(ids)
            self.imports = [i for i in self.imports if i.get("import_id") != import_id]
            gone = self._drop_empty_accounts([rec.get("account_id")])
            self.save()
            return {"deleted_transactions": removed, "deleted_accounts": len(gone)}

    def delete_account(self, account_id: str) -> dict:
        """Delete an account, its transactions and the imports that created them."""
        with self.lock:
            if account_id not in self.accounts:
                raise NotFound(f"No account '{account_id}'.")
            ids = {t.id for t in self.transactions.values() if t.account_id == account_id}
            removed = self._drop_transactions(ids)
            before = len(self.imports)
            self.imports = [i for i in self.imports if i.get("account_id") != account_id]
            del self.accounts[account_id]
            self.rules = [r for r in self.rules if r.account_id != account_id]
            self.save()
            return {"deleted_transactions": removed, "deleted_imports": before - len(self.imports)}

    def delete_transactions(self, ids: Iterable[str]) -> dict:
        with self.lock:
            ids = set(ids)
            missing = [i for i in ids if i not in self.transactions]
            if missing and len(missing) == len(ids):
                raise NotFound("None of those transactions exist.")
            removed = self._drop_transactions(ids)
            self.save()
            return {"deleted_transactions": removed}

    PURGE_SCOPES = ("transactions", "all")

    def run_data_purgers(self, scope: str) -> list[str]:
        """Let add-ons remove what they keep outside the vault (e.g. trained models)."""
        errors = []
        for ns, fn in getattr(self.registry, "data_purgers", []):
            try:
                fn(self, scope)
            except Exception as e:  # noqa: BLE001 - one add-on must not block a delete
                log.warning("add-on %s could not purge its data: %s", ns, type(e).__name__)
                errors.append(ns)
        return errors

    def purge(self, scope: str) -> dict:
        """Delete data in bulk. "transactions": statements, imports, accounts and receipts
        (rules, categories and settings stay). "all": back to a brand-new workspace;
        only the name, passphrase, license key and last-backup time are kept."""
        if scope not in self.PURGE_SCOPES:
            raise ValidationError("scope must be 'transactions' or 'all'.")
        with self.lock:
            counts = {"transactions": len(self.transactions), "imports": len(self.imports),
                      "accounts": len(self.accounts), "receipts": len(self.receipts)}
            for rid in list(self.receipts):
                for k in (f"receipt:{rid}:file", f"receipt:{rid}:text"):
                    self.vault.delete(k)
            self.transactions, self.imports, self.accounts, self.receipts = {}, [], {}, {}
            if scope == "all":
                counts["rules"] = len(self.rules)
                self.rules, self.custom_categories, self.learned_formats = [], [], []
                keep = {k: self.settings.get(k) for k in ("license_key", "business_name", "last_backup_at")}
                self.settings = {**self.default_settings(), **keep}
                core = set(COLLECTIONS) | {"learned_formats", "receipts"}
                for k in self.vault.keys():
                    if k not in core:          # add-on data (plugin:*), AI stats, caches
                        self.vault.delete(k)
            else:
                self.rules = [r for r in self.rules if not r.account_id]
            errors = self.run_data_purgers(scope)
            self.save()
            return {"scope": scope, "deleted": counts, "addon_errors": errors}

    def export_data(self, out_dir: Path) -> Path:
        """Everything in this workspace as an UNENCRYPTED zip (data portability)."""
        import csv
        import io
        import json
        import zipfile
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/heic": ".heic", "image/webp": ".webp",
               "application/pdf": ".pdf"}
        secrets = self.secret_fields()
        with self.lock:
            data = {
                "format": "ledgerstitch-export", "version": 1,
                "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "workspace": self.name,
                "settings": {k: v for k, v in self.settings.items() if k not in secrets and not k.startswith("_")},
                "accounts": [account_to_dict(a) for a in self.accounts.values()],
                "imports": self.imports,
                "transactions": [t.to_dict() for t in self.transactions.values()],
                "rules": [r.to_dict() for r in self.rules],
                "custom_categories": self.custom_categories,
                "learned_formats": self.learned_formats,
                "receipts": [r.to_dict() for r in self.receipts.values()],
            }
            addons: dict[str, Any] = {}
            for k in self.vault.keys():
                if k.startswith("plugin:") and not k.endswith(":file"):
                    try:
                        addons[k] = self.vault.get_json(k)
                    except Exception:  # noqa: BLE001 - binary add-on data is left out
                        continue
            data["addons"] = addons
            images = []
            for r in self.receipts.values():
                blob = self.vault.get(f"receipt:{r.id}:file")
                if blob is not None:
                    images.append((f"receipts/{r.id}{ext.get(r.mime or '', '')}", blob))
            rows = sorted(self.transactions.values(), key=lambda t: (t.date, t.seq))
            labels = {a.id: a.label for a in self.accounts.values()}

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["date", "account", "description", "merchant", "amount", "direction", "category",
                    "lane", "schedule_c", "status", "note", "duplicate_of", "id"])
        for t in rows:
            w.writerow([t.date.isoformat(), labels.get(t.account_id, t.account_id), t.description, t.merchant,
                        str(t.signed_amount), _enum_val(t.direction), t.category, _enum_val(t.lane),
                        t.schedule_c, _enum_val(t.status), t.note, t.duplicate_of, t.id])
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", self.name or "workspace").strip("-") or "workspace"
        out = Path(out_dir) / f"{name}-ledgerstitch-data-{date.today().isoformat()}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.txt", EXPORT_README)
            z.writestr("ledgerstitch-data.json", json.dumps(to_jsonable(data), indent=2, ensure_ascii=False))
            z.writestr("transactions.csv", buf.getvalue())
            for path, blob in images:
                z.writestr(path, blob)
        return out

    # ------------------------------------------------------------------ encrypted backups
    def mark_backup(self, when: Optional[datetime] = None) -> str:
        """Record that an encrypted backup was just made (drives the reminder)."""
        stamp = (when or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        with self.lock:
            self.settings["last_backup_at"] = stamp
            self.vault.put_json("settings", self.settings)
        return stamp

    def backup_status(self, now: Optional[datetime] = None) -> dict:
        """``due`` once there is data and no backup yet, or the last one is older than
        BACKUP_REMINDER_DAYS."""
        last = self.settings.get("last_backup_at")
        days = None
        if last:
            try:
                then = datetime.fromisoformat(str(last))
                if then.tzinfo is None:
                    then = then.replace(tzinfo=timezone.utc)
                days = max(0, ((now or datetime.now(timezone.utc)) - then).days)
            except ValueError:
                last = None
        has_data = bool(self.transactions or self.receipts or self.accounts)
        due = has_data and (days is None or days > BACKUP_REMINDER_DAYS)
        return {"last_backup_at": last, "days_since": days, "due": due,
                "reminder_days": BACKUP_REMINDER_DAYS}

    # ------------------------------------------------------------------ settings
    def secret_fields(self) -> set[str]:
        return set(SECRET_FIELDS) | set(self.registry.setting_secrets)

    def editable_settings(self) -> set[str]:
        return set(CORE_EDITABLE_SETTINGS) | set(self.registry.setting_editable)

    def public_settings(self) -> dict:
        """Settings safe to send to the browser: only known keys (core + loaded add-ons),
        secrets replaced by has_* flags."""
        known = self.default_settings()
        secrets = self.secret_fields()
        out = {k: self.settings.get(k, known[k]) for k in known
               if k not in secrets and not k.endswith(("_api_key", "_secret", "_token"))}
        for k in sorted(secrets):
            out[f"has_{k}"] = bool(self.settings.get(k))
        return out

    def update_settings(self, changes: dict) -> dict:
        unknown = set(changes) - self.editable_settings()
        if unknown:
            raise ValidationError(f"Unknown setting(s): {', '.join(sorted(unknown))}. "
                                  "Settings of an add-on can only be changed while it is installed.")
        validators = self.registry.setting_validators
        secrets = self.secret_fields()
        with self.lock:
            for k, v in changes.items():
                if k in secrets:
                    if v is None:
                        continue                       # unchanged
                    v = str(v).strip() or None         # "" clears the key
                    if k in validators:
                        v = self._run_validator(k, validators[k], v)
                elif k in validators:
                    v = self._run_validator(k, validators[k], v)
                elif k == "ai_enabled":
                    v = bool(v)
                elif k == "provider":
                    v = "off" if v in ("offline", "none", "", None) else v
                    allowed = list(CORE_PROVIDERS) + sorted(self.registry.ai_provider_ids)
                    if v not in allowed:
                        raise ValidationError(f"Provider must be one of: {', '.join(allowed)}.")
                elif k == "model":
                    v = str(v or "").strip()[:200]
                elif k == "ollama_url":
                    from .ai.local_ollama import validate_ollama_url
                    try:
                        v = validate_ollama_url(v)    # loopback, or exactly the server operator's URL
                    except ValueError as e:
                        raise ValidationError(str(e)) from None
                elif k == "tax_year":
                    if v in (None, ""):
                        v = None
                    else:
                        try:
                            v = int(v)
                        except (TypeError, ValueError):
                            raise ValidationError("tax_year must be a year like 2025.") from None
                        if not 1990 <= v <= 2100:
                            raise ValidationError("tax_year must be a year like 2025.")
                elif k == "auto_lock_minutes":
                    try:
                        v = int(v)
                    except (TypeError, ValueError):
                        raise ValidationError("auto_lock_minutes must be a number.") from None
                    if not 1 <= v <= 24 * 60:
                        raise ValidationError("auto_lock_minutes must be between 1 and 1440.")
                elif k == "business_profile":
                    v = str(v or "").strip()[:500]
                elif k == "business_name":
                    v = str(v or "").strip()[:200]
                self.settings[k] = v
            self.save()
        return self.public_settings()

    @staticmethod
    def _run_validator(key: str, fn, value):
        try:
            return fn(value)
        except StoreError:
            raise
        except (ValueError, TypeError) as e:
            raise ValidationError(str(e) or f"Invalid value for {key}.") from None

    # ------------------------------------------------------------------ reporting helpers
    def default_year(self) -> int:
        if self.settings.get("tax_year"):
            return int(self.settings["tax_year"])
        years = [t.date.year for t in self.transactions.values()]
        if years:
            # the year with most transactions
            return max(set(years), key=years.count)
        return date.today().year - 1

    def txns_for_year(self, year: Optional[int]) -> list[Transaction]:
        txns = list(self.transactions.values())
        if year:
            txns = [t for t in txns if t.date.year == year]
        return sorted(txns, key=lambda t: (t.date, t.account_id, t.seq))

    def counts(self) -> dict:
        return {"accounts": len(self.accounts), "transactions": len(self.transactions)}

