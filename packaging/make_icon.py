# SPDX-License-Identifier: AGPL-3.0-only
"""Render ledgerstitch/web/static/logo-mark.svg to app icons with Pillow (offline, pure Python).

The mark is only rounded rects and circles, so we rasterize those primitives directly
(4x supersampled) instead of needing an SVG renderer.

    python packaging/make_icon.py            -> packaging/build-icon/LedgerStitch.iconset/*.png,
                                                packaging/LedgerStitch.icns (macOS, via iconutil),
                                                packaging/LedgerStitch.ico (Windows)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "ledgerstitch" / "web" / "static" / "logo-mark.svg"
OUT = Path(__file__).resolve().parent
SS = 4  # supersampling factor


def _rgba(hex_color: str, opacity: float) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), round(255 * opacity)


def render(size: int, margin: float = 0.1) -> Image.Image:
    """Mark centered on a transparent canvas; ``margin`` per side (macOS Big Sur grid ~10%)."""
    root = ET.parse(SVG).getroot()
    vb = [float(x) for x in root.get("viewBox", "0 0 32 32").split()]
    big = size * SS
    inner = big * (1 - 2 * margin)
    scale = inner / vb[2]
    off = big * margin
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    for el in root:
        tag = el.tag.split("}")[-1]
        color = _rgba(el.get("fill", "#000000"), float(el.get("fill-opacity", "1")))
        layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        if tag == "rect":
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
            w, h = float(el.get("width")), float(el.get("height"))
            rx = float(el.get("rx", 0))
            box = [off + x * scale, off + y * scale, off + (x + w) * scale, off + (y + h) * scale]
            d.rounded_rectangle(box, radius=rx * scale, fill=color)
        elif tag == "circle":
            cx, cy, r = (float(el.get(k)) for k in ("cx", "cy", "r"))
            d.ellipse([off + (cx - r) * scale, off + (cy - r) * scale,
                       off + (cx + r) * scale, off + (cy + r) * scale], fill=color)
        else:
            continue
        canvas = Image.alpha_composite(canvas, layer)
    return canvas.resize((size, size), Image.LANCZOS)


def main() -> int:
    work = OUT / "build-icon"
    iconset = work / "LedgerStitch.iconset"
    shutil.rmtree(work, ignore_errors=True)
    iconset.mkdir(parents=True)
    for base in (16, 32, 128, 256, 512):
        render(base).save(iconset / f"icon_{base}x{base}.png")
        render(base * 2).save(iconset / f"icon_{base}x{base}@2x.png")
    render(1024).save(work / "icon_1024.png")
    # Windows .ico: full-bleed (no margin), several sizes
    render(256, margin=0).save(OUT / "LedgerStitch.ico",
                               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if sys.platform == "darwin" and shutil.which("iconutil"):
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "LedgerStitch.icns")],
                       check=True)
        print(f"wrote {OUT / 'LedgerStitch.icns'}")
    else:
        # Pillow can write .icns itself (fewer sizes, but fine as a fallback)
        render(1024).save(OUT / "LedgerStitch.icns")
        print(f"wrote {OUT / 'LedgerStitch.icns'} (Pillow fallback)")
    print(f"wrote {OUT / 'LedgerStitch.ico'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
