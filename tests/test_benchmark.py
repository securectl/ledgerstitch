# SPDX-License-Identifier: AGPL-3.0-only
"""Accuracy benchmark (ledgerstitch/bench): deterministic synthetic corpus, metrics, and a
regression floor so a parser/reconciler/categorizer regression fails CI.

The floors are the values measured when the benchmark was introduced (docs/BENCHMARK.md)
minus a small margin. Raise them when accuracy improves; never lower them to make a change pass.
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal

import pytest

from ledgerstitch import cli
from ledgerstitch.bench import DEFAULT_SEED
from ledgerstitch.bench.corpus import FILES, build_corpus
from ledgerstitch.bench.report import BEGIN, END, render_markdown, write_reports
from ledgerstitch.bench.runner import match_rows, run_benchmark

# measured at introduction (seed 2025, corpus v1) minus a margin
FLOORS = {
    ("parse", "row_recall"): 0.99,
    ("parse", "row_precision"): 0.99,
    ("parse", "exact_match"): 0.99,
    ("parse", "date_accuracy"): 0.99,
    ("parse", "amount_accuracy"): 0.99,
    ("parse", "sign_accuracy"): 0.99,
    ("parse", "description_accuracy"): 0.97,
    ("statements", "pass_rate"): 0.96,          # 33/33 measured: at most one statement may stop balancing
    ("duplicates", "precision"): 0.97,
    ("duplicates", "recall"): 0.95,
    ("transfers", "precision"): 0.97,
    ("transfers", "recall"): 0.95,
    ("refunds", "precision"): 0.95,
    ("refunds", "recall"): 0.90,
}
CATEGORY_FLOORS = {"category_accuracy": 0.80, "lane_accuracy": 0.87, "auto_precision": 0.88}
SCHEDULE_C_FLOOR = 0.68


@pytest.fixture(scope="module")
def result():
    return run_benchmark(write=False)


def _digest(folder) -> dict:
    import hashlib
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


# --------------------------------------------------------------------------- generator

def test_generator_is_deterministic(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    ta, tb = build_corpus(a, DEFAULT_SEED), build_corpus(b, DEFAULT_SEED)
    assert ta == tb
    assert _digest(a) == _digest(b)                  # byte-identical files, PDFs and XLSX included
    build_corpus(c, DEFAULT_SEED + 1)
    assert _digest(c) != _digest(a)


def test_corpus_covers_every_family_and_hard_case(tmp_path):
    t = build_corpus(tmp_path, DEFAULT_SEED)
    assert t["synthetic"] is True
    fams = {f["family"] for f in t["files"]}
    assert fams == {"CSV", "OFX", "QIF", "XLSX", "PDF"}
    fmts = {f["format"] for f in t["files"]}
    for need in ("csv:chase_card", "csv:chase_checking", "csv:citi", "csv:capital_one_card", "csv:us_bank",
                 "csv:capital_one_360", "csv:gemini", "csv:apple_card", "csv:robinhood_card", "csv:discover",
                 "csv:boa_card", "csv:boa_checking", "csv:amex_extended", "csv:wells_fargo", "csv:generic",
                 "ofx:sgml_bank", "ofx:xml_card", "ofx:qbo_card", "ofx:qfx_savings", "qif:bank", "qif:ccard",
                 "xlsx:split", "pdf:sections", "pdf:columns", "pdf:chase_card", "pdf:issuer_card",
                 "pdf:robinhood", "pdf:balance"):
        assert need in fmts
    tags = {tg for r in t["rows"] for tg in r["tags"]}
    assert {"refund", "card_payment", "transfer", "duplicate", "fx", "ref_noise", "multiline",
            "same_day_repeat"} <= tags
    assert t["pairs"]["transfer"] and t["pairs"]["refund"]
    assert sum(f["noise_rows"] for f in t["files"]) > 0
    assert len(t["files"]) == len(FILES) and len({f["file"] for f in t["files"]}) == len(FILES)


def test_truth_balances_add_up(tmp_path):
    """The ground truth is self-consistent: opening + truth rows == closing for every statement."""
    t = build_corpus(tmp_path, DEFAULT_SEED)
    rows: dict[str, list] = {}
    for r in t["rows"]:
        rows.setdefault(r["file"], []).append(r)
        assert Decimal(r["amount"]) > 0 and r["direction"] in ("in", "out")
        assert re.fullmatch(r"L\d+[ab]?", r["schedule_c"] or "L0")
    for f in t["files"]:
        sign = -1 if f["account_kind"] == "credit_card" else 1
        net = sum((Decimal(r["amount"]) * (1 if r["direction"] == "in" else -1) * sign
                   for r in rows.get(f["file"], [])), Decimal(0))
        assert Decimal(f["opening"]) + net == Decimal(f["closing"]), f["file"]
        s, e = (date.fromisoformat(x) for x in f["period"])
        assert all(s <= date.fromisoformat(r["date"]) <= e for r in rows.get(f["file"], []))


def test_corpus_is_fabricated(tmp_path):
    """No real-looking account numbers: every last-4 / account id in the corpus is one we invented."""
    t = build_corpus(tmp_path, DEFAULT_SEED)
    invented = {a["last4"] for a in t["accounts"] if a["last4"]}
    blob = "\n".join(r["description"] for r in t["rows"])
    for m in re.finditer(r"(?:\bCARD|ENDING IN|XXXX|\.\.\.)\s*(\d{4})\b", blob):   # not "CHECKCARD <mmdd>"
        assert m.group(1) in invented


# --------------------------------------------------------------------------- matching

def test_match_rows_is_one_to_one():
    from ledgerstitch.models import Direction, Transaction
    truth = [{"date": "2025-01-03", "amount": "5.00", "direction": "out", "description": "BLUEBIRD COFFEE"},
             {"date": "2025-01-03", "amount": "5.00", "direction": "out", "description": "BLUEBIRD COFFEE"},
             {"date": "2025-01-04", "amount": "9.00", "direction": "out", "description": "LANTERN BOOKS"}]
    parsed = [Transaction("a", date(2025, 1, 3), "BLUEBIRD COFFEE", Decimal("5.00"), Direction.OUT, seq=i)
              for i in range(3)]
    m = match_rows(truth, parsed)
    assert len(m) == 2 and {t for t, _ in m} == {0, 1}      # the third parsed coffee is spurious


# --------------------------------------------------------------------------- runner

def test_runner_reports_every_metric(result):
    for k in ("parse", "statements", "duplicates", "transfers", "refunds", "categories", "by_format",
              "by_family", "hard_cases", "files", "accounts"):
        assert k in result
    assert result["import_errors"] == []
    assert set(result["by_family"]) == {"CSV", "OFX", "QIF", "XLSX", "PDF"}
    assert result["run"]["ai"] is False and result["ai"]["sent_descriptors"] == 0
    cat = result["categories"]
    assert cat["all"]["n"] > 900 and cat["confusion_worst"]
    assert result["statements"]["self_check"]["checked"] >= 6
    assert result["accounts"]["split_accounts"] == [] and result["accounts"]["merged_accounts"] == []


@pytest.mark.parametrize("path,floor", sorted(FLOORS.items()))
def test_regression_floor(result, path, floor):
    block, key = path
    value = result[block][key]
    assert value is not None and value >= floor, f"{block}.{key} = {value} fell below the floor {floor}"


def test_regression_floor_categories(result):
    cat = result["categories"]
    for key, floor in CATEGORY_FLOORS.items():
        assert cat["all"][key] >= floor, f"categories.all.{key} = {cat['all'][key]} < {floor}"
    assert cat["schedule_c"]["accuracy"] >= SCHEDULE_C_FLOOR
    assert cat["business_lane_false_positives"]["n"] == 0


def test_self_check_never_contradicts_truth(result):
    sc = result["statements"]["self_check"]
    assert sc["false_alarms"] == [] and sc["missed_mismatches"] == []


def test_reports_are_written_and_markdown_is_stable(result, tmp_path):
    jp, mp = write_reports(result, tmp_path)
    data = json.loads(jp.read_text())
    assert data["parse"]["truth_rows"] == result["parse"]["truth_rows"]
    md = mp.read_text()
    assert BEGIN in md and END in md and "### Per format" in md and "Worst categories" in md
    assert "seconds" not in md and "seconds" not in jp.read_text()   # no timings: same code -> same files
    # hand-written text outside the markers survives a regeneration
    mp.write_text("# Intro kept\n\n" + md[md.index(BEGIN):] + "\n## Notes kept\n")
    write_reports(result, tmp_path)
    again = mp.read_text()
    assert again.startswith("# Intro kept") and again.rstrip().endswith("## Notes kept")
    assert render_markdown(result) in again


def test_ai_mode_sends_only_redacted_descriptors(monkeypatch, community):
    from ledgerstitch.ai import local_ollama
    seen: list[str] = []

    def fake_classify(self, descriptors, categories):
        seen.extend(descriptors)
        return [{"descriptor": d, "category": "Office Supplies", "lane": "business", "schedule_c": "L18",
                 "confidence": 0.9} for d in descriptors]
    monkeypatch.setattr(local_ollama.LocalOllama, "classify", fake_classify)
    r = run_benchmark(ai=True, write=False, settings={"provider": "ollama", "model": "fake-local"})
    assert r["run"]["ai"] is True and r["run"]["provider"] == "ollama"
    assert seen and r["ai"]["sent_descriptors"] > 0
    assert not any(re.search(r"\d{4}", d) for d in seen)          # no account/ref numbers, no amounts
    assert not any(re.search(r"\d+\.\d{2}", d) for d in seen)
    assert r["categories"]["by_source"].get("ai", {}).get("n", 0) > 0


def test_cli_benchmark_writes_reports(tmp_path, capsys):
    assert cli.main(["benchmark", "--out", str(tmp_path / "out"), "--corpus-out", str(tmp_path / "corpus")]) == 0
    assert (tmp_path / "out" / "benchmark.json").exists() and (tmp_path / "out" / "BENCHMARK.md").exists()
    assert (tmp_path / "corpus" / "truth.json").exists()
    out = capsys.readouterr().out
    assert "exact date+amount+sign" in out and "category accuracy" in out
