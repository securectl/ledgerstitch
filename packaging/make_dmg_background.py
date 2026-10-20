# SPDX-License-Identifier: AGPL-3.0-only
"""Render the DMG window background (an arrow from the app to Applications) with Pillow.

    python packaging/make_dmg_background.py OUT_DIR [--width 660 --height 400]

Writes OUT_DIR/background.png (1x) and OUT_DIR/background@2x.png (Retina). On macOS
``scripts/build_macos.sh`` combines them into a multi-resolution background.tiff with
``tiffutil -cathidpicheck``. Geometry must match the icon positions set in build_macos.sh
section 7a (app icon centre at 1/4 width, Applications at 3/4 width, both at ICON_Y).
No fonts are required: the text uses Pillow's built-in font (scalable on Pillow >= 10.1).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG_TOP = (247, 250, 248)
BG_BOTTOM = (232, 242, 237)
GREEN = (31, 111, 92)        # #1F6F5C, the brand mark colour
MUTED = (88, 110, 102)
ICON_Y = 0.46                # icon centre height as a fraction of the window height


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)        # Pillow >= 10.1: scalable default font
    except TypeError:                                   # older Pillow: fixed bitmap font
        return ImageFont.load_default()


def render(width: int, height: int, scale: int) -> Image.Image:
    w, h = width * scale, height * scale
    img = Image.new("RGB", (w, h), BG_TOP)
    d = ImageDraw.Draw(img)
    for y in range(h):                                  # soft vertical gradient
        t = y / max(h - 1, 1)
        d.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))

    # arrow between the two icon slots (icons are 128 pt wide, centred at w/4 and 3w/4)
    cy = round(h * ICON_Y)
    x0, x1 = round(w * 0.25 + 84 * scale), round(w * 0.75 - 84 * scale)
    thick, head = 6 * scale, 18 * scale
    d.rounded_rectangle([x0, cy - thick // 2, x1 - head, cy + thick // 2], radius=thick // 2, fill=GREEN)
    d.polygon([(x1 - head - 2 * scale, cy - head), (x1, cy), (x1 - head - 2 * scale, cy + head)], fill=GREEN)

    title = "Drag LedgerStitch to Applications to install"
    f = _font(15 * scale)
    tw = d.textlength(title, font=f)
    d.text(((w - tw) / 2, h * 0.80), title, fill=MUTED, font=f)
    return img


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out_dir")
    p.add_argument("--width", type=int, default=660)
    p.add_argument("--height", type=int, default=400)
    a = p.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for scale, name in ((1, "background.png"), (2, "background@2x.png")):
        img = render(a.width, a.height, scale)
        img.save(out / name, dpi=(72 * scale, 72 * scale))
        print(out / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
