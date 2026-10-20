# SPDX-License-Identifier: AGPL-3.0-only
"""Statement ingestion. ``parse_any`` is the single parsing entry point (see dispatch.py)."""


def parse_any(filename, data, profile, account_id, import_id):
    """Lazy wrapper around :func:`ledgerstitch.ingest.dispatch.parse_any`."""
    from .dispatch import parse_any as _p
    return _p(filename, data, profile, account_id, import_id)
