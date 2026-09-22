#!/usr/bin/env python3
"""Convert served images to WebP.

Diagrams are flat colour and type, so they go lossless — pixel-identical, just
a smaller container. Covers and photographs carry gradients, so they go lossy
at a quality where the difference is not visible at any size we serve.

The favicon stays PNG: some clients still fetch it without negotiating.
Originals for anything not regenerable by a script are kept under
assets/source-figures/ so nothing is lost by deleting the served PNG.
"""
from __future__ import annotations
import shutil
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "theme" / "assets" / "img"
KEEP_PNG = {"icon.png"}
LOSSY = ("cover", "card", "avatar", "social")     # gradients and photographs
REGENERABLE = ("-cover", "-card", "social-card")  # scripts/cover.py rebuilds these


def main() -> None:
    before = after = 0
    rows = []
    for png in sorted(IMG.glob("*.png")):
        if png.name in KEEP_PNG:
            continue
        webp = png.with_suffix(".webp")
        im = Image.open(png)
        lossy = any(k in png.stem for k in LOSSY)
        if lossy:
            im.convert("RGB").save(webp, "WEBP", quality=92, method=6)
        else:
            im.save(webp, "WEBP", lossless=True, method=6)
        b, a = png.stat().st_size, webp.stat().st_size
        before += b; after += a
        if not any(k in png.stem for k in REGENERABLE):
            shutil.copy2(png, ROOT / "assets" / "source-figures" / png.name)
        png.unlink()
        rows.append((png.name, b, a, "lossy" if lossy else "lossless"))
    w = max(len(r[0]) for r in rows) if rows else 10
    for name, b, a, how in rows:
        print(f"  {name:<{w}}  {b//1024:>4} KB → {a//1024:>4} KB  ({100-a*100//b:>2}% smaller, {how})")
    print(f"\n  total {before//1024} KB → {after//1024} KB  "
          f"({100 - after*100//before}% smaller across {len(rows)} files)")


if __name__ == "__main__":
    main()
