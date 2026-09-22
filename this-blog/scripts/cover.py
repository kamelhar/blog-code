#!/usr/bin/env python3
"""The cover template. One grid, every post, the site's own palette.

Two variants per post:
  <slug>-banner.webp   no title  — a full-bleed strip at the very top of the
                                   article, above the real <h1>. Purely visual;
                                   it never repeats the headline, so nothing
                                   the reader sees is said twice.
  <slug>-card.webp     title set — the og:image. It has to stand alone in a
                                   link preview with no page around it, so
                                   this is the one variant that carries text.

    python3 scripts/cover.py --slug my-post \
        --title "Serving an LLM on an NVIDIA DGX Spark" \
        --kicker "Field notes · Infrastructure" \
        --logo nvidia_reverse.webp --glyph spark --product "DGX Spark" \
        --spec "128 GB unified memory · 273 GB/s · one node"

Colour is the site's own tokens (ink/paper/teal from measured.css), not a
separate "hero" palette — a cover should read as this site, inverted, not as
a different brand pasted on top of it. --logo and --glyph are both optional;
a post about routing or Kubernetes can use the same template with no device.
"""
from __future__ import annotations
import argparse, math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
FONTS, IMG = ROOT / "assets" / "fonts", ROOT / "theme" / "assets" / "img"
MARGIN = 132
SITE, BRAND = "blog.kamelhar.net", "FEDERICO KAMELHAR"

# The site's own tokens (measured.css :root) — a cover is this site inverted,
# not a separate brand. INK is literally --ink; PAPER is literally --paper.
INK = (26, 25, 23)          # --ink   #1A1917
PAPER = (250, 249, 245)     # --paper #FAF9F5
TEAL = (18, 112, 127)       # --teal  #12707F
MUTED = (160, 155, 145)     # --muted, lightened for use on a dark ground
# The device keeps its own real-world colour regardless of page theme —
# a product photo would not recolour itself to match a site either.
GOLD, GOLD_HI, GOLD_LO = (201, 169, 106), (222, 195, 140), (146, 118, 62)


def font(n, s): return ImageFont.truetype(str(FONTS / n), s)
def MONO(s): return font("JetBrainsMono-Regular.ttf", s)
def MONOM(s): return font("JetBrainsMono-Medium.ttf", s)
def SERIF(s): return font("InstrumentSerif-Regular.ttf", s)


def teal_glow(im, cx, cy):
    """A held-back version of the site's own accent, not an invented hue."""
    layer = Image.new("RGB", im.size, INK)
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - 620, cy - 420, cx + 620, cy + 420], fill=(24, 58, 63))
    d.ellipse([cx - 360, cy - 260, cx + 360, cy + 260], fill=(31, 84, 92))
    return Image.blend(im, layer.filter(ImageFilter.GaussianBlur(140)), 0.55)


def device_shadow(im, cx, cy, s):
    """A soft cast shadow, composited as its own blurred layer - drawing it
    straight onto the canvas (no blur pass) is what produces a hard black
    block instead of a shadow, so it never happens on the same draw call as
    the device itself."""
    w, h_top = s, s * 0.30
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([cx - w / 2 + 10, cy - h_top / 2 + h_top * .55,
                          cx + w / 2 + 26, cy + h_top / 2 + h_top * .55 + 22],
                         radius=s * .06, fill=(0, 0, 0, 140))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    im.alpha_composite(shadow) if im.mode == "RGBA" else im.paste(
        Image.alpha_composite(im.convert("RGBA"), shadow).convert("RGB"), (0, 0))


def glyph_spark(d, cx, cy, s):
    """150 x 150 x 50.5 mm (NVIDIA's published dimensions): a squat, nearly
    square footprint, not the tall cube an unreferenced icon would guess at.
    Drawn in isometric so the true 3:1 width-to-height ratio still reads as
    an object rather than a flat tile."""
    w, h_top, depth = s, s * 0.30, s * 0.16   # width : height : perceived depth
    x, y = cx - w / 2, cy - h_top / 2
    # right (dark) face - suggests depth without a full 3D projection
    d.polygon([(x + w, y), (x + w + depth * .4, y + depth * .5),
              (x + w + depth * .4, y + h_top + depth * .5), (x + w, y + h_top)],
             fill=GOLD_LO)
    # front face
    d.rounded_rectangle([x, y, x + w, y + h_top], radius=s * .045, fill=GOLD)
    d.rounded_rectangle([x + s * .02, y + s * .02, x + w - s * .02, y + h_top * .34],
                        radius=s * .03, fill=GOLD_HI)   # top highlight strip
    # perforation grid — the real Spark's most recognisable surface detail
    n, r = 15, s * .0032
    gx0, gx1 = x + s * .035, x + w - s * .035
    gy0, gy1 = y + h_top * .40, y + h_top - s * .02
    for i in range(n):
        for j in range(4):
            px = gx0 + (gx1 - gx0) * i / (n - 1)
            py = gy0 + (gy1 - gy0) * j / 3
            d.ellipse([px - r, py - r, px + r, py + r], fill=(30, 26, 20))
    d.ellipse([cx - s * .012, y + h_top - s * .028, cx + s * .012, y + h_top - s * .006],
             fill=TEAL)
    return y + h_top + depth * .5   # bottom edge, for caption placement


GLYPHS = {"spark": glyph_spark}

def paste_photo(im, path, cx, top, w):
    """A photo, not the drawn glyph: rounded corners, a fine border, and the
    same shadow pass so it sits in the scene rather than floating on it."""
    photo = Image.open(IMG / path).convert("RGB")
    h = int(photo.height * w / photo.width)
    photo = photo.resize((w, h), Image.LANCZOS)
    radius = 28
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
    device_shadow(im, cx, top + h / 2, max(w, h) * 0.92)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    layer.paste(photo, (int(cx - w / 2), top), mask)
    border = Image.new("RGBA", im.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(border)
    bd.rounded_rectangle([cx - w / 2, top, cx + w / 2, top + h], radius=radius,
                         outline=(255, 255, 255, 46), width=2)
    im.paste(Image.alpha_composite(Image.alpha_composite(
        im.convert("RGBA"), layer), border).convert("RGB"), (0, 0))
    return top + h


def wrap(d, text, f, max_w):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if d.textbbox((0, 0), t, font=f)[2] <= max_w:
            cur = t
        else:
            lines.append(cur); cur = word
    return [l for l in lines + [cur] if l]


def brand_bar(d, im_w):
    d.text((MARGIN, 88), BRAND, font=MONOM(26), fill=PAPER)
    d.rounded_rectangle([MARGIN, 140, MARGIN + 82, 146], radius=3, fill=TEAL)
    sw = d.textbbox((0, 0), SITE, font=MONO(26))[2]
    d.text((im_w - MARGIN - sw, 88), SITE, font=MONO(26), fill=MUTED)


def render_banner(a) -> Image.Image:
    """A masthead: brand, the real headline, and the device. No "Field notes"
    kicker, no spec-sheet line - those belonged on a card meant to stand
    alone (the og:image still carries them); a banner sitting directly above
    the real <h1> does not need to repeat what the page says two lines down,
    it needs to say the title, once, boldly."""
    W, H = 2400, 760
    im = teal_glow(Image.new("RGB", (W, H), INK), 1720, 430)
    d = ImageDraw.Draw(im)
    brand_bar(d, W)

    panel_cx, logo_top, photo_top, label_y = 1780, 176, 246, 566
    if a.logo:
        mark = Image.open(IMG / a.logo).convert("RGBA")
        lw = 240
        mark = mark.resize((lw, int(mark.height * lw / mark.width)), Image.LANCZOS)
        im.paste(mark, (panel_cx - lw // 2, logo_top), mark)
        d = ImageDraw.Draw(im)
    if a.photo:
        paste_photo(im, a.photo, panel_cx, photo_top, 440)
        d = ImageDraw.Draw(im)
    elif a.glyph in GLYPHS:
        device_shadow(im, panel_cx, photo_top + 130, 240)
        d = ImageDraw.Draw(im)
        GLYPHS[a.glyph](d, panel_cx, photo_top + 130, 240)
    if a.product:
        pf = MONOM(24)
        pw = d.textbbox((0, 0), a.product.upper(), font=pf)[2]
        d.text((panel_cx - pw / 2, label_y), a.product.upper(), font=pf, fill=MUTED)

    # the headline: the whole point of the banner, vertically centred
    # against the content band below the brand bar (y 170..H)
    tf = SERIF(84)
    max_w = 1440
    lines = wrap(d, a.title, tf, max_w)
    line_h = 84 * 1.04
    block_h = len(lines) * line_h
    y = 170 + (H - 170 - block_h) / 2
    for line in lines:
        d.text((MARGIN - 4, y), line, font=tf, fill=PAPER)
        y += line_h
    d.rounded_rectangle([MARGIN, y + 14, MARGIN + 100, y + 21], radius=3, fill=TEAL)
    return im


def render_card(a) -> Image.Image:
    """The og:image. Stands alone, so it carries the headline."""
    W, H = 2400, 1260
    panel_cx = 1790
    im = teal_glow(Image.new("RGB", (W, H), INK), panel_cx, 630)
    d = ImageDraw.Draw(im)
    brand_bar(d, W)
    top = 330
    if a.logo:
        mark = Image.open(IMG / a.logo).convert("RGBA")
        lw = 460
        mark = mark.resize((lw, int(mark.height * lw / mark.width)), Image.LANCZOS)
        im.paste(mark, (panel_cx - lw // 2, top), mark)
        d = ImageDraw.Draw(im)
        top += mark.height + 84
    if a.photo:
        top = paste_photo(im, a.photo, panel_cx, top + 20, 560) + 40
        d = ImageDraw.Draw(im)
    elif a.glyph in GLYPHS:
        device_shadow(im, panel_cx, top + 130, 380)
        d = ImageDraw.Draw(im)
        top = GLYPHS[a.glyph](d, panel_cx, top + 130, 380) + 60
    if a.product:
        pf = MONOM(28)
        pw = d.textbbox((0, 0), a.product.upper(), font=pf)[2]
        d.text((panel_cx - pw / 2, top), a.product.upper(), font=pf, fill=MUTED)

    tf = SERIF(140)
    lines = wrap(d, a.title, tf, 1280)
    block = len(lines) * (140 * 1.02) + 210
    y = (H - block) / 2 + 40
    d.text((MARGIN, y - 92), a.kicker.upper(), font=MONO(28), fill=TEAL)
    for line in lines:
        d.text((MARGIN - 4, y), line, font=tf, fill=PAPER)
        y += 140 * 1.02
    y += 34
    d.rounded_rectangle([MARGIN, y, MARGIN + 118, y + 7], radius=3, fill=TEAL)
    if a.spec:
        d.text((MARGIN, y + 42), a.spec, font=MONO(30), fill=MUTED)
    return im


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--slug", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--kicker", default="Field notes")
    p.add_argument("--logo", default="", help="a file in static/img, already reversed for dark")
    p.add_argument("--glyph", default="", choices=[""] + list(GLYPHS))
    p.add_argument("--photo", default="", help="a real/generated product image in static/img - takes priority over --glyph")
    p.add_argument("--product", default="")
    p.add_argument("--spec", default="")
    a = p.parse_args()
    for name, im in (("banner", render_banner(a)), ("card", render_card(a))):
        out = IMG / f"{a.slug}-{name}.webp"
        im.save(out, "WEBP", quality=92, method=6)
        print(f"  {out.relative_to(ROOT)}  {im.size[0]}x{im.size[1]}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
