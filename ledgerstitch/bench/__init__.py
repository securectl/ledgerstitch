# SPDX-License-Identifier: AGPL-3.0-only
"""Reproducible accuracy benchmark on a fixed SYNTHETIC corpus.

    ledgerstitch benchmark [--ai] [--out DIR] [--seed N] [--corpus-out DIR]

* ``corpus``  builds a deterministic, labeled set of statement files (every
  supported family: bank CSV profiles, OFX/QFX/QBO, QIF, XLSX, text PDFs) with
  ground truth for each row and each statement's opening/closing balance.
* ``runner``  imports the corpus through the real pipeline (``Workspace.add_import``:
  detect -> parse -> dedupe -> reconcile -> categorize) into a throwaway vault in
  a temp dir and scores the result.
* ``report``  writes ``benchmark.json`` and ``BENCHMARK.md``.

All names, merchants, numbers and amounts in the corpus are fabricated.
"""
from __future__ import annotations

__all__ = ["build_corpus", "run_benchmark", "DEFAULT_SEED"]

DEFAULT_SEED = 2025


def build_corpus(out_dir, seed: int = DEFAULT_SEED):
    from .corpus import build_corpus as _b
    return _b(out_dir, seed)


def run_benchmark(**kw):
    from .runner import run_benchmark as _r
    return _r(**kw)
