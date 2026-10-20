# SPDX-License-Identifier: AGPL-3.0-only
"""Run the synthetic corpus through the real import pipeline and score it.

The corpus is imported file by file with ``Workspace.add_import`` (detect ->
parse -> merge by id -> reconcile -> categorize) into a throwaway encrypted
vault inside a temporary directory, exactly as the app does. The parse output
of each file is observed by wrapping ``store._parse_any`` for the duration of
the run (read-only: the wrapper returns what the parser returned).

Scoring (see docs/BENCHMARK.md for definitions):

* rows: per file, parsed rows are matched one-to-one to truth rows (amount,
  date, then description tokens); recall/precision, exact date/amount/sign.
* statements: opening + parsed activity == closing (truth balances), and the
  app's own ``reconciled`` self-check against that.
* pairs: duplicates, transfers/card payments and refunds as precision/recall
  over truth *event* pairs.
* categories: final category / lane / Schedule C line of every kept (non
  duplicate) transaction against the bookkeeper label.
"""
from __future__ import annotations

import json
import re
import secrets
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .. import __version__
from . import DEFAULT_SEED
from .corpus import CORPUS_VERSION, build_corpus

__all__ = ["run_benchmark", "score", "match_rows"]

_TOK = re.compile(r"[A-Z0-9]+")


def _tokens(s: str) -> list[str]:
    return _TOK.findall((s or "").upper())


def _desc_recall(truth: str, parsed: str) -> float:
    t = set(_tokens(truth))
    if not t:
        return 1.0
    return len(t & set(_tokens(parsed))) / len(t)


def _ratio(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


# --------------------------------------------------------------------------- matching

def match_rows(truth_rows: list[dict], parsed: list) -> list[tuple[int, int]]:
    """One-to-one (truth index, parsed index) matches for one file (greedy by score)."""
    cands = []
    for ti, t in enumerate(truth_rows):
        td, ta = date.fromisoformat(t["date"]), Decimal(t["amount"])
        for pi, p in enumerate(parsed):
            amt_eq = p.amount == ta
            gap = abs((p.date - td).days)
            rec = _desc_recall(t["description"], p.description)
            if amt_eq and gap == 0:
                ok = True
            elif amt_eq and gap <= 3:
                ok = rec >= 0.5
            elif gap == 0:
                ok = rec >= 0.6
            else:
                ok = False
            if not ok:
                continue
            score = 3 * amt_eq + 2 * (gap == 0) + (p.direction.value == t["direction"]) + rec - 0.1 * gap
            cands.append((-score, ti, pi))
    cands.sort()
    used_t, used_p, out = set(), set(), []
    for _, ti, pi in cands:
        if ti in used_t or pi in used_p:
            continue
        used_t.add(ti)
        used_p.add(pi)
        out.append((ti, pi))
    return out


# --------------------------------------------------------------------------- running

@contextmanager
def _observe_parses(store_mod, sink: dict):
    """Record each file's parse result (by import id) while the pipeline runs."""
    original = store_mod._parse_any

    def wrapper(filename, data, profile, account_id, import_id):
        txns, warnings, info = original(filename, data, profile, account_id, import_id)
        sink[import_id] = {"txns": list(txns or []), "info": dict(info or {}), "profile": profile}
        return txns, warnings, info
    store_mod._parse_any = wrapper
    try:
        yield
    finally:
        store_mod._parse_any = original


def run_benchmark(*, ai: bool = False, out_dir=None, seed: int = DEFAULT_SEED, corpus_out=None,
                  settings: Optional[dict] = None, write: bool = True) -> dict:
    """Build the corpus, import it into a temp vault, score it. Returns the result dict
    (also written as ``benchmark.json`` + ``BENCHMARK.md`` to ``out_dir`` when given)."""
    from .. import store as store_mod
    from ..security.vault import Vault
    from ..store import StoreError, Workspace

    with tempfile.TemporaryDirectory(prefix="ls-bench-") as tmp:
        tmp = Path(tmp)
        corpus_dir = Path(corpus_out) if corpus_out else tmp / "corpus"
        truth = build_corpus(corpus_dir, seed)
        vault = Vault.create(tmp / "bench.vault", secrets.token_urlsafe(18), "Benchmark")
        ws = Workspace(vault)
        cfg = {"ai_enabled": False, "provider": "off"}
        if ai:
            cfg = {"ai_enabled": True, "provider": "ollama"}
            cfg.update(settings or {})
        ws.settings.update(cfg)
        parses: dict[str, dict] = {}
        imports: list[dict] = []
        t0 = time.perf_counter()
        with _observe_parses(store_mod, parses):
            for f in truth["files"]:
                data = (corpus_dir / f["file"]).read_bytes()
                rec = {"file": f["file"], "import_id": None, "error": None}
                try:
                    r = ws.add_import(f["file"], data, f["account_label"])
                    rec.update(import_id=r.import_id, account_id=r.account_id, count=r.count, new=r.new,
                               reconciled=r.reconciled, warnings=list(r.warnings),
                               detected_kind=(r.detected or {}).get("kind"),
                               profile=(r.detected or {}).get("profile"),
                               sent_descriptors=(r.categorize or {}).get("sent_descriptors", 0) or 0,
                               ai_warnings=list((r.categorize or {}).get("warnings") or []))
                except StoreError as e:
                    rec["error"] = f"{type(e).__name__}: {e}"
                imports.append(rec)
        elapsed = time.perf_counter() - t0
        result = score(truth, imports, parses, ws)
        result["run"] = {"seconds": round(elapsed, 2), "ai": ai,
                         "provider": cfg.get("provider") if ai else "off (rules + built-in heuristics)",
                         "model": cfg.get("model") if ai else None, "ledgerstitch_version": __version__,
                         "corpus_version": CORPUS_VERSION, "seed": seed}
        vault.lock()
    if write and out_dir is not None:
        from .report import write_reports
        write_reports(result, Path(out_dir))
    return result


# --------------------------------------------------------------------------- scoring

def _ev_label(first_row: dict, event: Optional[str]) -> str:
    """'f03r0012 2025-01-05 out 12.00 DESCRIPTION' for an event (synthetic data), or '(spurious row)'."""
    r = first_row.get(event or "")
    if r is None:
        return "(no truth row)"
    return f"{r['id']} {r['date']} {r['direction']} {r['amount']} {r['description'][:48]}"


def score(truth: dict, imports: list[dict], parses: dict, ws) -> dict:
    rows_by_file: dict[str, list[dict]] = defaultdict(list)
    for r in truth["rows"]:
        rows_by_file[r["file"]].append(r)
    row_by_id = {r["id"]: r for r in truth["rows"]}

    vault_to_row: dict[str, str] = {}          # vault txn id -> truth row id (first file that produced it)
    collapsed: list[tuple[str, str]] = []      # (truth row id of the later file, truth row id it collapsed onto)
    per_file = []
    misses: list[dict] = []
    vault_accounts: dict[str, set] = defaultdict(set)
    for f, imp in zip(truth["files"], imports):
        trs = rows_by_file.get(f["file"], [])
        parsed = (parses.get(imp.get("import_id")) or {}).get("txns", []) if imp.get("import_id") else []
        if imp.get("account_id"):
            vault_accounts[f["account"]].add(imp["account_id"])
        m = match_rows(trs, parsed)
        matched_t = {ti for ti, _ in m}
        d_ok = a_ok = s_ok = desc_ok = exact = 0
        for ti, pi in m:
            t, p = trs[ti], parsed[pi]
            de = p.date.isoformat() == t["date"]
            ae = p.amount == Decimal(t["amount"])
            se = p.direction.value == t["direction"]
            d_ok += de
            a_ok += ae
            s_ok += se
            desc_ok += _desc_recall(t["description"], p.description) >= 0.999
            exact += de and ae and se
            if not (de and ae and se):
                misses.append({"row": t["id"], "file": f["file"], "problem": ",".join(
                    k for k, ok in (("date", de), ("amount", ae), ("sign", se)) if not ok)})
            if p.id in vault_to_row and vault_to_row[p.id] != t["id"]:
                collapsed.append((t["id"], vault_to_row[p.id]))
            else:
                vault_to_row.setdefault(p.id, t["id"])
        for ti, t in enumerate(trs):
            if ti not in matched_t:
                misses.append({"row": t["id"], "file": f["file"], "problem": "missing"})
        extra = len(parsed) - len(m)
        if extra:
            misses.append({"row": None, "file": f["file"], "problem": f"{extra} extra parsed row(s)"})
        # truth-based balance check: opening + parsed activity == closing
        card = f["account_kind"] == "credit_card"
        net = sum(((p.amount if p.direction.value == "in" else -p.amount) * (-1 if card else 1)
                   for p in parsed), Decimal(0))
        balanced = bool(parsed) and abs(Decimal(f["opening"]) + net - Decimal(f["closing"])) <= Decimal("0.01")
        per_file.append({
            "file": f["file"], "format": f["format"], "family": f["family"], "account": f["account"],
            "truth_rows": len(trs), "parsed_rows": len(parsed), "matched": len(m), "exact": exact,
            "date_ok": d_ok, "amount_ok": a_ok, "sign_ok": s_ok, "desc_ok": desc_ok, "balanced": balanced,
            "balance_difference": str(abs(Decimal(f["opening"]) + net - Decimal(f["closing"]))),
            "self_check": imp.get("reconciled"), "error": imp.get("error"),
            "kind_ok": (imp.get("detected_kind") == f["account_kind"]) if imp.get("detected_kind") else False,
            "profile": imp.get("profile"),
        })

    # ------------------------------------------------------------------ rows / statements
    def agg(items):
        T = sum(x["truth_rows"] for x in items)
        P = sum(x["parsed_rows"] for x in items)
        M = sum(x["matched"] for x in items)
        return {
            "files": len(items), "truth_rows": T, "parsed_rows": P, "matched": M,
            "row_recall": _ratio(M, T), "row_precision": _ratio(M, P),
            "exact_match": _ratio(sum(x["exact"] for x in items), T),
            "date_accuracy": _ratio(sum(x["date_ok"] for x in items), M),
            "amount_accuracy": _ratio(sum(x["amount_ok"] for x in items), M),
            "sign_accuracy": _ratio(sum(x["sign_ok"] for x in items), M),
            "description_accuracy": _ratio(sum(x["desc_ok"] for x in items), M),
            "balanced_statements": sum(x["balanced"] for x in items),
            "reconciliation_pass_rate": _ratio(sum(x["balanced"] for x in items), len(items)),
        }
    by_format = defaultdict(list)
    by_family = defaultdict(list)
    for x in per_file:
        by_format[x["format"]].append(x)
        by_family[x["family"]].append(x)
    parse = agg(per_file)
    sc = [x for x in per_file if x["self_check"] is not None]
    self_check = {
        "checked": len(sc), "not_checkable": len(per_file) - len(sc),
        "said_match": sum(1 for x in sc if x["self_check"] is True),
        "said_mismatch": sum(1 for x in sc if x["self_check"] is False),
        "agrees_with_truth": sum(1 for x in sc if x["self_check"] == x["balanced"]),
        "false_alarms": [x["file"] for x in sc if x["self_check"] is False and x["balanced"]],
        "missed_mismatches": [x["file"] for x in sc if x["self_check"] is True and not x["balanced"]],
    }
    grouping = {
        "accounts": len(vault_accounts),
        "split_accounts": sorted(k for k, v in vault_accounts.items() if len(v) > 1),
        "merged_accounts": sorted({k for k, v in vault_accounts.items() for o, w in vault_accounts.items()
                                   if o != k and v & w}),
        "kind_accuracy": _ratio(sum(x["kind_ok"] for x in per_file), len(per_file)),
    }

    # ------------------------------------------------------------------ pairs
    # Events whose printed rows are indistinguishable (same account, day, amount, direction and
    # descriptor: two identical coffees) are interchangeable, so pairs are scored on a canonical event.
    txns = ws.transactions
    first_row: dict[str, dict] = {}
    for r in truth["rows"]:
        first_row.setdefault(r["event"], r)
    canon_of: dict[tuple, str] = {}
    canon: dict[str, str] = {}
    for e, r in sorted(first_row.items()):
        key = (r["account"], r["date"], r["amount"], r["direction"], " ".join(_tokens(r["description"])))
        canon[e] = canon_of.setdefault(key, e)
    row_of = lambda tid: vault_to_row.get(tid)  # noqa: E731
    cev = lambda rid: canon[row_by_id[rid]["event"]] if rid else None  # noqa: E731
    lbl = lambda e: _ev_label(first_row, e)  # noqa: E731

    # duplicates: truth = every extra printing of an event; a predicted pair is right when both rows
    # print the same (canonical) event and come from different files
    ev_rows = Counter(r["event"] for r in truth["rows"])
    class_dups: Counter = Counter()
    for e, n in ev_rows.items():
        class_dups[canon[e]] += n - 1
    truth_dups = sum(class_dups.values())
    pred_dup = [(row_of(t.id), row_of(t.duplicate_of)) for t in txns.values() if t.duplicate_of]
    pred_dup += collapsed
    tp_by_class: Counter = Counter()
    false_dup = []
    for a, b in pred_dup:
        if a and b and cev(a) == cev(b) and row_by_id[a]["file"] != row_by_id[b]["file"]:
            tp_by_class[cev(a)] += 1
        else:
            false_dup.append([lbl(row_by_id[a]["event"]) if a else "(no truth row)",
                              lbl(row_by_id[b]["event"]) if b else "(no truth row)"])
    dup_tp = sum(min(n, class_dups[c]) for c, n in tp_by_class.items())
    dups = {"truth": truth_dups, "predicted": len(pred_dup), "true_positive": dup_tp,
            "precision": _ratio(dup_tp, len(pred_dup)), "recall": _ratio(dup_tp, truth_dups),
            "collapsed_by_id": len(collapsed), "false_pairs": false_dup,
            "missed": [lbl(c) for c, n in sorted(class_dups.items()) if n and tp_by_class.get(c, 0) < n]}

    def pair_block(truth_pairs: set, pred: list) -> dict:
        tp = [p for p in pred if p in truth_pairs]
        return {"truth_pairs": len(truth_pairs), "predicted_pairs": len(pred), "true_positive": len(tp),
                "precision": _ratio(len(tp), len(pred)), "recall": _ratio(len(set(tp)), len(truth_pairs)),
                "false_pairs": [[lbl(a), lbl(b)] for a, b in pred if (a, b) not in truth_pairs],
                "missed": [[lbl(a), lbl(b)] for a, b in sorted(truth_pairs) if (a, b) not in set(pred)]}

    xkind = truth["pairs"]["transfer_kind"]
    truth_xfer = {tuple(sorted((canon[a], canon[b]))): xkind.get(f"{a}|{b}") for a, b in truth["pairs"]["transfer"]}
    groups = defaultdict(list)
    for t in txns.values():
        if t.transfer_group and not t.duplicate_of:
            groups[t.transfer_group].append(t.id)
    pred_x = [tuple(sorted((cev(row_of(a)) or f"?{a}", cev(row_of(b)) or f"?{b}")))
              for a, b in (g for g in groups.values() if len(g) == 2)]
    transfers = pair_block(set(truth_xfer), pred_x)
    transfers["by_kind"] = {}
    for kind in ("card_payment", "transfer"):
        tk = {p for p, k in truth_xfer.items() if k == kind}
        found = len(tk & set(pred_x))
        transfers["by_kind"][kind] = {"truth": len(tk), "found": found, "recall": _ratio(found, len(tk))}

    truth_ref = {(canon[a], canon[b]) for a, b in truth["pairs"]["refund"]}
    pred_r = [(cev(row_of(t.id)) or f"?{t.id}", cev(row_of(t.refund_of)) or f"?{t.refund_of}")
              for t in txns.values() if t.refund_of and not t.duplicate_of]
    refunds = pair_block(truth_ref, pred_r)

    # ------------------------------------------------------------------ categories
    kept = [(t, row_by_id[vault_to_row[t.id]]) for t in txns.values()
            if not t.duplicate_of and t.id in vault_to_row]
    seen_events: set[str] = set()
    evals = []
    for t, r in sorted(kept, key=lambda x: x[1]["id"]):
        if r["event"] in seen_events:      # a duplicate the pipeline did not catch: score the event once
            continue
        seen_events.add(r["event"])
        evals.append((t, r))

    def cat_block(items):
        n = len(items)
        ok = sum(1 for t, r in items if t.category == r["category"])
        lane_ok = sum(1 for t, r in items if t.lane.value == r["lane"])
        auto = [(t, r) for t, r in items if t.status.value == "auto"]
        return {"n": n, "category_accuracy": _ratio(ok, n), "lane_accuracy": _ratio(lane_ok, n),
                "auto_rate": _ratio(len(auto), n),
                "auto_precision": _ratio(sum(1 for t, r in auto if t.category == r["category"]), len(auto)),
                "uncategorized": sum(1 for t, r in items if t.category in (None, "Uncategorized"))}
    spend = [(t, r) for t, r in evals if r["kind"] not in ("card_payment", "transfer")]
    by_source = defaultdict(list)
    for t, r in evals:
        by_source[t.source.value].append((t, r))
    sc_items = [(t, r) for t, r in evals if r["schedule_c"]]
    sc_ok = sum(1 for t, r in sc_items if t.schedule_c == r["schedule_c"])
    false_biz = [(t, r) for t, r in evals if t.lane.value == "business" and r["lane"] != "business"]
    categories = {
        "all": cat_block(evals), "spend_and_income": cat_block(spend),
        "by_source": {k: cat_block(v) for k, v in sorted(by_source.items())},
        "schedule_c": {"n": len(sc_items), "accuracy": _ratio(sc_ok, len(sc_items)),
                       "assigned": sum(1 for t, r in sc_items if t.schedule_c)},
        "business_lane_false_positives": {"n": len(false_biz), "rate": _ratio(len(false_biz), len(evals))},
    }
    conf: dict[str, Counter] = defaultdict(Counter)
    for t, r in evals:
        conf[r["category"]][t.category or "Uncategorized"] += 1
    worst = []
    for cat, c in conf.items():
        n = sum(c.values())
        wrong = n - c.get(cat, 0)
        worst.append({"category": cat, "n": n, "correct": c.get(cat, 0), "errors": wrong,
                      "accuracy": _ratio(c.get(cat, 0), n),
                      "predicted_instead": [[k, v] for k, v in c.most_common() if k != cat][:3]})
    worst.sort(key=lambda w: (-w["errors"], w["category"]))
    categories["confusion_worst"] = worst[:12]
    categories["per_category"] = sorted(worst, key=lambda w: w["category"])

    # ------------------------------------------------------------------ hard cases
    eval_by_row = {r["id"]: t for t, r in evals}
    missing = {m["row"] for m in misses if m["problem"] == "missing"}
    inexact = {m["row"] for m in misses if m["row"] and m["problem"] != "missing"}
    hard = {}
    tags = sorted({tg for r in truth["rows"] for tg in r["tags"]})
    for tg in tags:
        rs = [r for r in truth["rows"] if tg in r["tags"]]
        ex = sum(1 for r in rs if r["id"] not in missing and r["id"] not in inexact)
        cat_rs = [(eval_by_row[r["id"]], r) for r in rs if r["id"] in eval_by_row]
        hard[tg] = {"rows": len(rs), "parsed_exact": _ratio(ex, len(rs)),
                    "category_accuracy": _ratio(sum(1 for t, r in cat_rs if t.category == r["category"]),
                                                len(cat_rs))}

    ai_info = {"sent_descriptors": sum(i.get("sent_descriptors") or 0 for i in imports),
               "warnings": sorted({w for i in imports for w in (i.get("ai_warnings") or [])})}
    return {
        "corpus": {"files": len(truth["files"]), "rows": len(truth["rows"]),
                   "accounts": len(truth["accounts"]), "noise_rows": sum(f["noise_rows"] for f in truth["files"]),
                   "seed": truth["seed"], "version": truth["corpus_version"]},
        "parse": parse,
        "by_family": {k: agg(v) for k, v in sorted(by_family.items())},
        "by_format": {k: agg(v) for k, v in sorted(by_format.items())},
        "statements": {"total": len(per_file), "balanced": parse["balanced_statements"],
                       "pass_rate": parse["reconciliation_pass_rate"], "self_check": self_check},
        "accounts": grouping,
        "duplicates": dups, "transfers": transfers, "refunds": refunds,
        "categories": categories, "hard_cases": hard, "ai": ai_info,
        "files": per_file,
        "import_errors": [{"file": i["file"], "error": i["error"]} for i in imports if i.get("error")],
        "misses": misses,
    }


def dumps(result: dict) -> str:
    return json.dumps(result, indent=1, sort_keys=True, default=str) + "\n"
