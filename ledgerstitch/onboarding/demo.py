# SPDX-License-Identifier: AGPL-3.0-only
"""The "Try with sample data" button: fill a separate demo workspace with the synthetic benchmark corpus.

Every name, merchant, account number and amount is fabricated (ledgerstitch.bench.corpus).
The files are generated in a temp folder, imported through the normal pipeline
(detect -> parse -> dedupe -> reconcile -> categorize, AI off) and deleted again.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

DEMO_NAME = "Sample data (Rivera Studio)"
DEMO_LABEL = "Sample data"


def load_demo(ws, seed: int = 2025, max_files: int | None = None) -> dict:
    from ..bench import build_corpus
    from ..store import StoreError

    ws.settings.update({"ai_enabled": False, "provider": "off", "business_name": "Rivera Studio LLC (sample)"})
    imported = failed = rows = 0
    with tempfile.TemporaryDirectory(prefix="ledgerstitch-demo-") as tmp:
        truth = build_corpus(Path(tmp), seed)
        files = truth["files"][:max_files] if max_files else truth["files"]
        for f in files:
            try:
                r = ws.add_import(f["file"], (Path(tmp) / f["file"]).read_bytes(), f["account_label"])
                imported += 1
                rows += int(getattr(r, "new", 0) or 0)
            except StoreError:
                failed += 1
    ws.save()
    return {"files": imported, "failed": failed, "transactions": rows}
