# SPDX-License-Identifier: AGPL-3.0-only
"""Single entry point that parses any supported statement file.

``parse_any(filename, data, profile, account_id, import_id) -> (txns, warnings, info)``
sniffs the file *content* (not just the extension) and routes it to the right
parser:

=========  =====================================================  ==============
family     parser                                                 needs mapping
=========  =====================================================  ==============
CSV/TSV    csv_parser.parse_csv_detailed (profile.mapping)        yes
           platforms.parse_platform for PayPal/Stripe/Shopify/    yes (catalog)
           Square/Venmo/Coinbase/Revolut (profile.catalog_id)
XLSX       xlsx_parser.parse_xlsx → CSV pipeline                  yes
PDF        pdf_parser.parse_pdf_detailed                          no
OFX/QFX/   ofx_parser.parse_ofx                                   no
QBO
QIF        qif_parser.parse_qif                                   no
CAMT/      iso20022_parser.parse_iso (Enterprise formats)         no
MT940/BAI2
=========  =====================================================  ==============

``info`` always has ``format`` and ``reconciled`` (True/False/None).
"""
from __future__ import annotations

from .detect import FormatProfile, sniff_format

__all__ = ["parse_any", "preview_any", "needs_mapping", "is_tabular", "sniff_format"]

TABULAR = {"CSV", "XLSX"}


def is_tabular(filename: str, data: bytes) -> bool:
    """CSV/TSV/XLSX: column mapping applies (and the learned-format registry)."""
    return sniff_format(filename, data) in TABULAR


def needs_mapping(filename: str, data: bytes, profile) -> bool:
    return is_tabular(filename, data) and getattr(profile, "mapping", None) is None


def parse_any(filename: str, data: bytes, profile: FormatProfile | None, account_id: str,
              import_id: str) -> tuple[list, list[str], dict]:
    fmt = sniff_format(filename, data)
    if fmt == "PDF":
        from .pdf_parser import parse_pdf_detailed
        txns, warnings, info = parse_pdf_detailed(data, account_id, import_id)
    elif fmt == "OFX":
        from .ofx_parser import parse_ofx
        txns, warnings, info = parse_ofx(data, account_id, import_id, filename=filename)
    elif fmt == "QIF":
        from .qif_parser import parse_qif
        txns, warnings, info = parse_qif(data, account_id, import_id)
    elif fmt in ("CAMT", "MT940", "BAI2"):
        from .iso20022_parser import parse_iso
        txns, warnings, info = parse_iso(data, account_id, import_id)
    elif fmt == "XLS":
        raise ValueError("legacy .xls is not supported; save the sheet as .xlsx or CSV")
    elif fmt == "XLSX":
        from .xlsx_parser import parse_xlsx
        txns, warnings, info = parse_xlsx(data, profile, account_id, import_id)
    else:
        from .platforms import adopt_platform, is_platform, parse_platform
        if getattr(profile, "id", None) == "manual":
            adopt_platform(filename, data, profile)
        if is_platform(getattr(profile, "catalog_id", None)):
            txns, warnings, info = parse_platform(data, profile, account_id, import_id)
        else:
            from .csv_parser import parse_csv_detailed
            txns, warnings = parse_csv_detailed(data, profile, account_id, import_id)
            info = {}
    info = dict(info or {})
    info.setdefault("format", getattr(profile, "format", None) or fmt)
    info.setdefault("reconciled", None)
    return list(txns or []), list(warnings or []), info


def preview_any(data: bytes, rows: int = 8) -> dict:
    """Manual-mapping preview for CSV/TSV/XLSX (XLSX is converted to CSV first)."""
    from .csv_parser import preview_csv
    if sniff_format("", data) == "XLSX":
        from .xlsx_parser import xlsx_to_csv_bytes
        data, _ = xlsx_to_csv_bytes(data)
    return preview_csv(data, rows)
