# SPDX-License-Identifier: AGPL-3.0-only
"""benchmark.json + BENCHMARK.md from a benchmark result.

BENCHMARK.md is regenerated between the ``<!-- BEGIN GENERATED -->`` /
``<!-- END GENERATED -->`` markers; text outside them (hand-written context,
history) is kept when the file already exists. The Markdown holds no timings
or dates, so re-running on the same code gives the same file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

__all__ = ["write_reports", "render_markdown"]

BEGIN, END = "<!-- BEGIN GENERATED -->", "<!-- END GENERATED -->"

FORMAT_NAMES = {
    "csv:chase_card": "CSV · Chase card", "csv:chase_checking": "CSV · Chase checking", "csv:citi": "CSV · Citi",
    "csv:capital_one_card": "CSV · Capital One card", "csv:capital_one_360": "CSV · Capital One 360",
    "csv:us_bank": "CSV · US Bank", "csv:gemini": "CSV · Gemini", "csv:apple_card": "CSV · Apple Card",
    "csv:robinhood_card": "CSV · Robinhood card", "csv:discover": "CSV · Discover",
    "csv:boa_card": "CSV · Bank of America card", "csv:boa_checking": "CSV · Bank of America checking",
    "csv:amex_extended": "CSV · Amex (extended, multi-line cells)",
    "csv:wells_fargo": "CSV · Wells Fargo (no header)",
    "csv:generic": "CSV · unknown layout (generic detector)", "csv:schwab": "CSV · Schwab (catalog)",
    "csv:mercury": "CSV · Mercury (catalog)", "ofx:sgml_bank": "OFX 1.x SGML · checking",
    "ofx:xml_card": "OFX 2.x XML · card", "ofx:qbo_card": "QBO · card", "ofx:qfx_savings": "QFX · savings (overlapping)",
    "qif:bank": "QIF · bank", "qif:ccard": "QIF · card", "xlsx:split": "XLSX · debit/credit + preamble",
    "pdf:sections": "PDF · sections (Chase-like checking)", "pdf:columns": "PDF · columns (Wells-Fargo-like)",
    "pdf:chase_card": "PDF · card (Chase-like)", "pdf:issuer_card": "PDF · card (Citi-like)",
    "pdf:robinhood": "PDF · Robinhood card", "pdf:balance": "PDF · running balance only",
}
TAG_NAMES = {
    "card_payment": "Card payments", "duplicate": "Re-printed in an overlapping file", "fx": "Foreign currency",
    "multiline": "Multi-line description (PDF)", "recurring": "Recurring subscriptions",
    "ref_noise": "Reference/trace numbers in the descriptor", "refund": "Refunds",
    "same_day_repeat": "Identical same-day purchases (not duplicates)", "transfer": "Own-account transfers",
}


def pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _pr(block: dict) -> str:
    return f"{pct(block['precision'])} / {pct(block['recall'])}"


def render_markdown(r: dict) -> str:
    c, p, st = r["corpus"], r["parse"], r["statements"]
    cat = r["categories"]
    run = r.get("run", {})
    sc = st["self_check"]
    L = []
    mode = "AI on: " + str(run.get("provider")) + (f" / {run.get('model')}" if run.get("model") else "") \
        if run.get("ai") else "rules + built-in heuristics only (no AI)"
    L += [f"Corpus v{c['version']}, seed {c['seed']}: **{c['files']} statement files, {c['rows']} truth rows, "
          f"{c['accounts']} accounts** ({c['noise_rows']} more rows in the files must *not* be imported: pending, "
          f"declined, failed, zero-amount, summary lines). Categorization mode: **{mode}**. "
          f"LedgerStitch {run.get('ledgerstitch_version', '?')}.", ""]
    L += ["### Headline", "", "| Metric | Result |", "|---|---|"]
    L += [
        f"| Rows found (recall) | {pct(p['row_recall'])} ({p['matched']}/{p['truth_rows']}) |",
        f"| Rows that are real (precision) | {pct(p['row_precision'])} ({p['matched']}/{p['parsed_rows']}) |",
        f"| Exact date + amount + sign (of all truth rows) | {pct(p['exact_match'])} |",
        f"| Date / amount / sign accuracy (of found rows) | {pct(p['date_accuracy'])} / {pct(p['amount_accuracy'])}"
        f" / {pct(p['sign_accuracy'])} |",
        f"| Description captured (every descriptor word present) | {pct(p['description_accuracy'])} |",
        f"| Statements that balance (opening + parsed activity = closing) | {st['balanced']}/{st['total']} "
        f"({pct(st['pass_rate'])}) |",
        f"| App's own balance check | {sc['checked']} checkable, {sc['said_match']} \"match\", "
        f"{sc['said_mismatch']} \"doesn't match\"; agrees with truth on {sc['agrees_with_truth']}/{sc['checked']} |",
        f"| Duplicates across overlapping files, precision / recall | {_pr(r['duplicates'])} "
        f"({r['duplicates']['true_positive']}/{r['duplicates']['truth']}) |",
        f"| Transfer & card-payment pairs, precision / recall | {_pr(r['transfers'])} "
        f"({r['transfers']['true_positive']}/{r['transfers']['truth_pairs']}) |",
        f"| Refund → purchase links, precision / recall | {_pr(r['refunds'])} "
        f"({r['refunds']['true_positive']}/{r['refunds']['truth_pairs']}) |",
        f"| Category accuracy, all kept transactions | {pct(cat['all']['category_accuracy'])} (n={cat['all']['n']}) |",
        f"| Category accuracy, spending + income only (no transfers/card payments) | "
        f"{pct(cat['spend_and_income']['category_accuracy'])} (n={cat['spend_and_income']['n']}) |",
        f"| Lane accuracy | {pct(cat['all']['lane_accuracy'])} |",
        f"| Auto-approved share / accuracy of auto-approved | {pct(cat['all']['auto_rate'])} / "
        f"{pct(cat['all']['auto_precision'])} |",
        f"| Left Uncategorized | {cat['all']['uncategorized']} of {cat['all']['n']} |",
        f"| Schedule C line accuracy (rows whose category maps to a line) | {pct(cat['schedule_c']['accuracy'])} "
        f"(n={cat['schedule_c']['n']}) |",
        f"| Put in the Business lane but not business | {cat['business_lane_false_positives']['n']} |",
        f"| Account kind detected (card vs bank) | {pct(r['accounts']['kind_accuracy'])} |",
    ]
    if r.get("import_errors"):
        L += ["", "**Import errors:** " + "; ".join(f"`{e['file']}`: {e['error']}" for e in r["import_errors"])]
    if run.get("ai"):
        L += ["", f"Redacted descriptors sent to the AI provider: {r['ai']['sent_descriptors']}."
              + (" Warnings: " + "; ".join(r["ai"]["warnings"]) if r["ai"]["warnings"] else "")]

    head = ["| {} | Files | Truth rows | Recall | Precision | Exact | Balances |", "|---|---:|---:|---:|---:|---:|---:|"]
    L += ["", "### Per format", "", head[0].format("Format"), head[1]]
    for k, v in r["by_format"].items():
        L.append(f"| {FORMAT_NAMES.get(k, k)} | {v['files']} | {v['truth_rows']} | {pct(v['row_recall'])} | "
                 f"{pct(v['row_precision'])} | {pct(v['exact_match'])} | {v['balanced_statements']}/{v['files']} |")
    L += ["", head[0].format("Family"), head[1]]
    for k, v in r["by_family"].items():
        L.append(f"| {k} | {v['files']} | {v['truth_rows']} | {pct(v['row_recall'])} | {pct(v['row_precision'])} | "
                 f"{pct(v['exact_match'])} | {v['balanced_statements']}/{v['files']} |")

    L += ["", "### Pairing", "", "| | Truth | Predicted | Correct | Precision | Recall |",
          "|---|---:|---:|---:|---:|---:|"]
    d = r["duplicates"]
    L.append(f"| Duplicates | {d['truth']} | {d['predicted']} | {d['true_positive']} | {pct(d['precision'])} | "
             f"{pct(d['recall'])} |")
    for name, b in (("Transfers + card payments", r["transfers"]), ("Refunds", r["refunds"])):
        L.append(f"| {name} | {b['truth_pairs']} | {b['predicted_pairs']} | {b['true_positive']} | "
                 f"{pct(b['precision'])} | {pct(b['recall'])} |")
    bk = r["transfers"]["by_kind"]
    L += ["", f"Card payments found: {bk['card_payment']['found']}/{bk['card_payment']['truth']}; own-account "
              f"transfers found: {bk['transfer']['found']}/{bk['transfer']['truth']}. "
              f"{d['collapsed_by_id']} of the duplicates were collapsed on import because the bank's own "
              f"transaction id (OFX FITID) repeats."]
    for name, b in (("Duplicate", d), ("Transfer", r["transfers"]), ("Refund", r["refunds"])):
        if b["false_pairs"] or b["missed"]:
            L += ["", f"{name} errors (synthetic rows):", ""]
            L += [f"- wrong pair: {' ⇄ '.join(x) if isinstance(x, list) else x}" for x in b["false_pairs"][:8]]
            L += [f"- missed: {' ⇄ '.join(x) if isinstance(x, list) else x}" for x in b["missed"][:8]]

    L += ["", "### Categorization", "", "| Decided by | Rows | Category | Lane | Auto-approved | Accuracy of auto |",
          "|---|---:|---:|---:|---:|---:|"]
    for k, v in cat["by_source"].items():
        L.append(f"| {k} | {v['n']} | {pct(v['category_accuracy'])} | {pct(v['lane_accuracy'])} | "
                 f"{pct(v['auto_rate'])} | {pct(v['auto_precision'])} |")
    L += ["", "Worst categories (most errors first):", "",
          "| Expected category | Rows | Correct | Accuracy | Predicted instead |", "|---|---:|---:|---:|---|"]
    for w in cat["confusion_worst"]:
        if not w["errors"]:
            continue
        inst = ", ".join(f"{k} ({n})" for k, n in w["predicted_instead"]) or "—"
        L.append(f"| {w['category']} | {w['n']} | {w['correct']} | {pct(w['accuracy'])} | {inst} |")

    L += ["", "### Hard cases", "", "| Case | Rows | Parsed exactly | Category accuracy |", "|---|---:|---:|---:|"]
    for k, v in r["hard_cases"].items():
        L.append(f"| {TAG_NAMES.get(k, k)} | {v['rows']} | {pct(v['parsed_exact'])} | {pct(v['category_accuracy'])} |")

    bad = [f for f in r["files"] if not f["balanced"] or f["matched"] != f["truth_rows"]
           or f["parsed_rows"] != f["matched"] or f["self_check"] is False]
    if bad:
        L += ["", "### Files with problems", "", "| File | Truth rows | Parsed | Matched | Balances | App check |",
              "|---|---:|---:|---:|---|---|"]
        for f in bad:
            L.append(f"| `{f['file']}` | {f['truth_rows']} | {f['parsed_rows']} | {f['matched']} | "
                     f"{'yes' if f['balanced'] else 'no (off by $' + f['balance_difference'] + ')'} | "
                     f"{ {True: 'match', False: 'mismatch', None: '—'}[f['self_check']] } |")
    return "\n".join(L) + "\n"


_DEFAULT_HEAD = """# LedgerStitch accuracy benchmark

Generated by `ledgerstitch benchmark` on a fixed **synthetic** corpus (every name, merchant, account
number and amount is fabricated). See `ledgerstitch/bench/` for the generator and the scoring rules.

"""


def write_reports(result: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / "benchmark.json"
    # no wall-clock timing in the files: the same code and seed always write the same bytes
    stable = {**result, "run": {k: v for k, v in (result.get("run") or {}).items() if k != "seconds"}}
    jp.write_text(json.dumps(stable, indent=1, sort_keys=True, default=str) + "\n")
    mp = out_dir / "BENCHMARK.md"
    body = f"{BEGIN}\n{render_markdown(result)}{END}"
    old = mp.read_text() if mp.exists() else ""
    if BEGIN in old and END in old:
        text = old[:old.index(BEGIN)] + body + old[old.index(END) + len(END):]
    else:
        text = _DEFAULT_HEAD + body + "\n"
    mp.write_text(text)
    return jp, mp
