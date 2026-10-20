# SPDX-License-Identifier: AGPL-3.0-only
"""A tiny, dependency-free, byte-for-byte deterministic text PDF writer.

Only what synthetic statements need: US Letter pages, the built-in Courier
font (fixed width, so right-aligned amount columns are exact), and text drawn
at absolute positions. No compression, no timestamps, no ids: the same input
always produces the same bytes.
"""
from __future__ import annotations

PAGE_W, PAGE_H = 612, 792
CHAR_W = 0.6          # Courier advance width per point of font size


def _esc(s: str) -> str:
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class PDF:
    def __init__(self, size: float = 8.0) -> None:
        self.size = size
        self.pages: list[list[str]] = [[]]

    # coordinates are top-left based (y grows downwards) like pdfplumber's `top`
    def text(self, x: float, y: float, s: str, size: float | None = None) -> None:
        sz = size or self.size
        self.pages[-1].append(f"BT /F1 {sz:g} Tf 1 0 0 1 {x:.2f} {PAGE_H - y:.2f} Tm ({_esc(s)}) Tj ET")

    def rtext(self, x_right: float, y: float, s: str, size: float | None = None) -> None:
        sz = size or self.size
        self.text(x_right - len(s) * CHAR_W * sz, y, s, sz)

    def new_page(self) -> None:
        self.pages.append([])

    def to_bytes(self) -> bytes:
        objs: list[bytes] = []
        n_pages = len(self.pages)
        # 1 catalog, 2 pages, 3 font, then (page, content) pairs
        kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
        objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
        for i, ops in enumerate(self.pages):
            content = ("\n".join(ops) + "\n").encode("latin-1")
            objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                        f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode())
            objs.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream")
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)
