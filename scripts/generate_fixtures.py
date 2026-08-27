"""Render three new test scenarios for the label-hold demo.

Outputs:
  fixtures/hk-raw-tuna/{spec,coa,label}.{png,jpg}
    Tuna poke bowl. Spec/CoA declare FISH (raw tuna, soy, sesame).
    Label only says CONTAINS: SOY, SESAME. Missing FISH -> held.

  fixtures/hk-tree-nuts-mix/{spec,coa,label}.{png,jpg}
    Maple almond granola. Spec/CoA declare ALMONDS, CASHEWS, HAZELNUTS.
    Label says CONTAINS: TREE NUTS (ALMONDS, CASHEWS, HAZELNUTS).
    Full declaration -> released.

  fixtures/hk-blurry-receipt/{spec,coa,label}.{png,jpg}
    Receipt-style blurry label. Spec/CoA declare MILK, WHEAT.
    Label is too noisy for OCR -> held, incomplete_packet.
"""
from __future__ import annotations

import os
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path("/opt/data/serverless/label-hold/fixtures")
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"

PAPER = (250, 247, 240)
INK = (28, 25, 20)
INK_LIGHT = (90, 80, 70)
LINE = (200, 190, 175)
ACCENT = (180, 90, 30)  # harbor-kitchen primary orange


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def wrap_lines(draw: ImageDraw.ImageDraw, text: str, font_obj, max_width: int) -> list[str]:
    out: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
        words = paragraph.split()
        line = ""
        for w in words:
            test = (line + " " + w).strip()
            bbox = draw.textbbox((0, 0), test, font=font_obj)
            if bbox[2] - bbox[0] <= max_width:
                line = test
            else:
                if line:
                    out.append(line)
                line = w
        if line:
            out.append(line)
    return out


def draw_paragraph(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font_obj,
    max_width: int,
    color=INK,
    line_spacing: int = 6,
) -> int:
    """Return the y coordinate after drawing."""
    lines = wrap_lines(draw, text, font_obj, max_width)
    for ln in lines:
        draw.text((x, y), ln, font=font_obj, fill=color)
        bbox = draw.textbbox((0, 0), ln, font=font_obj)
        y += (bbox[3] - bbox[1]) + line_spacing
    return y


def draw_box(
    draw: ImageDraw.ImageDraw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    fill=None,
    outline=LINE,
    width: int = 2,
):
    if fill is not None:
        draw.rectangle([x1, y1, x2, y2], fill=fill)
    draw.rectangle([x1, y1, x2, y2], outline=outline, width=width)


def stamp_noise(img: Image.Image, intensity: float = 0.05) -> Image.Image:
    """Add per-pixel noise (simulates low-quality scan)."""
    import io

    px = img.convert("RGB").load()
    w, h = img.size
    rnd = random.Random(42)
    for _ in range(int(w * h * intensity)):
        x = rnd.randint(0, w - 1)
        y = rnd.randint(0, h - 1)
        v = rnd.randint(0, 255)
        px[x, y] = (v, v, v)
    return img


# ---------------- scenario 1: held (fish missing on label) ----------------
def render_raw_tuna() -> None:
    out = ROOT / "hk-raw-tuna"
    out.mkdir(parents=True, exist_ok=True)

    # ---- spec.png: product specification ----
    spec = Image.new("RGB", (1100, 850), PAPER)
    d = ImageDraw.Draw(spec)
    f_h1 = font(SANS_BOLD, 36)
    f_h2 = font(SANS_BOLD, 20)
    f_body = font(SANS, 17)
    f_mono = font(MONO, 15)
    y = 40
    draw_box(d, 24, 24, 1076, 826, outline=LINE, width=2)
    d.text((50, y), "HARBOR KITCHEN", font=f_h1, fill=ACCENT)
    bbox = d.textbbox((0, 0), "HARBOR KITCHEN", font=f_h1)
    y += bbox[3] - bbox[1] + 6
    d.text((50, y), "Product Specification", font=font(SERIF, 22), fill=INK_LIGHT)
    y += 36
    d.line([(50, y), (1050, y)], fill=LINE, width=2)
    y += 24
    spec_text = (
        "Product name      : Raw Ahi Tuna Poke Bowl\n"
        "SKU               : HK-POKE-TUNA-220G\n"
        "Net weight        : 220 g (7.76 oz)\n"
        "Best-by window    : 5 days from pack date (kept at 2-4 C)\n"
        "Lot format        : HK-<LINE>-<YYMMDD>-<SEQ>\n"
        "Allergen statement: This product contains fish (yellowfin tuna),\n"
        "                    soy, and sesame. Produced in a facility that\n"
        "                    also handles wheat and tree nuts.\n"
        "Storage           : Refrigerate at 2-4 C. Do not freeze.\n"
        "Intended use      : Ready-to-eat cold dish. Consume within 24h\n"
        "                    of opening the vacuum-sealed lid.\n"
        "\n"
        "Ingredient deck (descending order of weight):\n"
        "  Yellowfin tuna (sashimi-grade, sushi cut)\n"
        "  Sushi rice (short-grain, seasoned)\n"
        "  Soy sauce (water, soybeans, wheat, salt)\n"
        "  Sesame oil, scallions, seaweed salad\n"
    )
    y = draw_paragraph(d, 50, y, spec_text, f_mono, 1000, color=INK, line_spacing=4)
    y += 12
    d.text((50, y), "Allergen Summary (canonical):", font=f_h2, fill=INK)
    y += 30
    for a in ("fish", "soy", "sesame", "wheat"):
        d.text((60, y), "  -  " + a, font=f_mono, fill=INK)
        y += 24
    spec.save(out / "spec.png", format="PNG", optimize=True)

    # ---- coa.png: certificate of analysis ----
    coa = Image.new("RGB", (1100, 850), PAPER)
    d = ImageDraw.Draw(coa)
    y = 40
    draw_box(d, 24, 24, 1076, 826, outline=LINE, width=2)
    d.text((50, y), "CERTIFICATE OF ANALYSIS", font=f_h1, fill=ACCENT)
    y += 50
    coa_rows = [
        ("Issued by", "Coastal Marine Foods Inc."),
        ("Supplier lot", "CMF-AT-2026-08-22-A"),
        ("Material", "Yellowfin Tuna (sashimi grade)"),
        ("Processed", "2026-08-22"),
        ("Analyzed",  "2026-08-23"),
        ("Analyzed by", "Dr. M. Pereira (ISO 17025 lab)"),
    ]
    for k, v in coa_rows:
        d.text((50, y), f"{k:<14}", font=f_mono, fill=INK_LIGHT)
        d.text((180, y), v, font=f_mono, fill=INK)
        y += 28
    y += 8
    d.line([(50, y), (1050, y)], fill=LINE, width=2)
    y += 16
    d.text((50, y), "Microbiological Panel (per 25g):", font=f_h2, fill=INK)
    y += 28
    panel = [
        ("Aerobic plate count",  "12,000 CFU/g", "Limit <100,000 CFU/g"),
        ("Coliforms",            "<10 CFU/g",    "Limit <100 CFU/g"),
        ("E. coli",              "Negative",     "Negative"),
        ("Salmonella",           "Negative/25g", "Negative/25g"),
        ("S. aureus",            "<10 CFU/g",    "Limit <100 CFU/g"),
        ("Listeria",             "Negative/25g", "Negative/25g"),
    ]
    for k, v, lim in panel:
        d.text((50, y), k, font=f_mono, fill=INK)
        d.text((360, y), v, font=f_mono, fill=INK)
        d.text((700, y), lim, font=f_mono, fill=INK_LIGHT)
        y += 24
    y += 12
    d.text((50, y), "Allergen declaration (per supplier):", font=f_h2, fill=INK)
    y += 28
    d.text((60, y), "Contains: FISH, SOY, SESAME, WHEAT (from soy sauce).", font=f_mono, fill=INK)
    coa.save(out / "coa.png", format="PNG", optimize=True)

    # ---- label.jpg: photo of the printed lid label (missing FISH) ----
    label = Image.new("RGB", (900, 700), (245, 240, 230))
    d = ImageDraw.Draw(label)
    # try to look like a real product label: off-white card with band
    draw_box(d, 30, 30, 870, 670, fill=(255, 255, 255), outline=INK, width=3)
    draw_box(d, 30, 30, 870, 110, fill=ACCENT, outline=ACCENT, width=0)
    d.text((50, 50), "HARBOR KITCHEN", font=font(SANS_BOLD, 32), fill=(255, 255, 255))
    d.text((50, 130), "Raw Ahi Tuna Poke Bowl", font=font(SERIF, 26), fill=INK)
    d.text((50, 170), "220 g / 7.76 oz  -  Keep Refrigerated", font=font(SANS, 18), fill=INK_LIGHT)
    d.line([(50, 210), (850, 210)], fill=LINE, width=2)
    d.text((50, 230), "INGREDIENTS:", font=font(SANS_BOLD, 18), fill=INK)
    body = (
        "Yellowfin tuna, sushi rice, soy sauce (water, soybeans,\n"
        "wheat, salt), sesame oil, scallions, seaweed salad.\n"
    )
    draw_paragraph(d, 50, 260, body, font(SANS, 17), 800, line_spacing=4)
    d.line([(50, 350), (850, 350)], fill=LINE, width=2)
    d.text((50, 370), "CONTAINS:", font=font(SANS_BOLD, 22), fill=ACCENT)
    d.text((50, 410), "SOY, SESAME, WHEAT", font=font(SANS_BOLD, 32), fill=INK)
    d.text((50, 460), "(FISH not declared)", font=font(SANS, 14), fill=INK_LIGHT)
    # barcode strip
    draw_box(d, 50, 530, 850, 600, fill=(255, 255, 255), outline=INK, width=1)
    bx = 60
    rnd = random.Random(7)
    while bx < 840:
        w = rnd.choice([2, 3, 4])
        if rnd.random() < 0.1:
            w = 6
        draw_box(d, bx, 540, bx + w, 590, fill=INK, outline=INK)
        bx += w + rnd.choice([2, 3, 4])
    d.text((330, 605), "HK-POKE-TUNA-220G", font=font(MONO_BOLD, 18), fill=INK)
    d.text((50, 640), "Best before: see date code on lid rim.", font=font(SANS, 12), fill=INK_LIGHT)
    label.save(out / "label.jpg", format="JPEG", quality=85, optimize=True)


# ---------------- scenario 2: released (tree-nuts-mix, full declaration) ----------------
def render_tree_nuts_mix() -> None:
    out = ROOT / "hk-tree-nuts-mix"
    out.mkdir(parents=True, exist_ok=True)

    spec = Image.new("RGB", (1100, 850), PAPER)
    d = ImageDraw.Draw(spec)
    f_h1 = font(SANS_BOLD, 36)
    f_h2 = font(SANS_BOLD, 20)
    f_mono = font(MONO, 15)
    y = 40
    draw_box(d, 24, 24, 1076, 826, outline=LINE, width=2)
    d.text((50, y), "HARBOR KITCHEN", font=f_h1, fill=ACCENT)
    y += 50
    d.text((50, y), "Product Specification - Maple Almond Granola", font=font(SERIF, 22), fill=INK_LIGHT)
    y += 36
    d.line([(50, y), (1050, y)], fill=LINE, width=2)
    y += 24
    spec_text = (
        "Product name      : Maple Almond Granola\n"
        "SKU               : HK-GRAN-MAPLE-300G\n"
        "Net weight        : 300 g (10.6 oz)\n"
        "Best-by window    : 9 months from pack date (sealed, ambient)\n"
        "Lot format        : HK-<LINE>-<YYMMDD>-<SEQ>\n"
        "Allergen statement: Contains tree nuts (almonds, cashews,\n"
        "                    hazelnuts) and wheat (rolled oats).\n"
        "Storage           : Cool, dry place. Reseal after opening.\n"
        "\n"
        "Ingredient deck (descending order of weight):\n"
        "  Rolled oats, almonds, cashews, hazelnuts,\n"
        "  maple syrup, sunflower oil, sea salt.\n"
    )
    y = draw_paragraph(d, 50, y, spec_text, f_mono, 1000, color=INK, line_spacing=4)
    y += 12
    d.text((50, y), "Allergen Summary (canonical):", font=f_h2, fill=INK)
    y += 30
    for a in ("almonds", "cashews", "hazelnuts", "wheat"):
        d.text((60, y), "  -  " + a, font=f_mono, fill=INK)
        y += 24
    spec.save(out / "spec.png", format="PNG", optimize=True)

    coa = Image.new("RGB", (1100, 850), PAPER)
    d = ImageDraw.Draw(coa)
    y = 40
    draw_box(d, 24, 24, 1076, 826, outline=LINE, width=2)
    d.text((50, y), "CERTIFICATE OF ANALYSIS", font=f_h1, fill=ACCENT)
    y += 50
    rows = [
        ("Issued by", "Cascadia Nut Co."),
        ("Supplier lot", "CNC-GR-2026-08-19-D"),
        ("Material", "Mixed nuts (almonds, cashews, hazelnuts)"),
        ("Processed", "2026-08-19"),
        ("Analyzed",  "2026-08-20"),
        ("Analyzed by", "K. Okafor (ISO 17025 lab)"),
    ]
    for k, v in rows:
        d.text((50, y), f"{k:<14}", font=f_mono, fill=INK_LIGHT)
        d.text((180, y), v, font=f_mono, fill=INK)
        y += 28
    y += 12
    d.line([(50, y), (1050, y)], fill=LINE, width=2)
    y += 16
    d.text((50, y), "Microbiological Panel (per 25g):", font=f_h2, fill=INK)
    y += 28
    panel = [
        ("Aerobic plate count",  "5,200 CFU/g", "Limit <50,000 CFU/g"),
        ("Yeast / Mold",         "180 CFU/g",   "Limit <1,000 CFU/g"),
        ("E. coli",              "Negative",    "Negative"),
        ("Salmonella",           "Negative/25g", "Negative/25g"),
        ("Aflatoxins",           "<2 ppb",      "Limit <20 ppb"),
    ]
    for k, v, lim in panel:
        d.text((50, y), k, font=f_mono, fill=INK)
        d.text((360, y), v, font=f_mono, fill=INK)
        d.text((700, y), lim, font=f_mono, fill=INK_LIGHT)
        y += 24
    y += 16
    d.text((50, y), "Allergen declaration (per supplier):", font=f_h2, fill=INK)
    y += 28
    d.text((60, y), "Contains: TREE NUTS (ALMONDS, CASHEWS, HAZELNUTS), WHEAT.", font=f_mono, fill=INK)
    coa.save(out / "coa.png", format="PNG", optimize=True)

    label = Image.new("RGB", (900, 700), (245, 240, 230))
    d = ImageDraw.Draw(label)
    draw_box(d, 30, 30, 870, 670, fill=(255, 255, 255), outline=INK, width=3)
    draw_box(d, 30, 30, 870, 110, fill=ACCENT, outline=ACCENT, width=0)
    d.text((50, 50), "HARBOR KITCHEN", font=font(SANS_BOLD, 32), fill=(255, 255, 255))
    d.text((50, 130), "Maple Almond Granola", font=font(SERIF, 26), fill=INK)
    d.text((50, 170), "300 g / 10.6 oz  -  Made with real maple syrup", font=font(SANS, 18), fill=INK_LIGHT)
    d.line([(50, 210), (850, 210)], fill=LINE, width=2)
    d.text((50, 230), "INGREDIENTS:", font=font(SANS_BOLD, 18), fill=INK)
    body = (
        "Rolled oats, almonds, cashews, hazelnuts,\n"
        "maple syrup, sunflower oil, sea salt.\n"
    )
    draw_paragraph(d, 50, 260, body, font(SANS, 17), 800, line_spacing=4)
    d.line([(50, 350), (850, 350)], fill=LINE, width=2)
    d.text((50, 370), "CONTAINS:", font=font(SANS_BOLD, 22), fill=ACCENT)
    d.text((50, 410), "TREE NUTS (ALMONDS,", font=font(SANS_BOLD, 28), fill=INK)
    d.text((50, 445), "CASHEWS, HAZELNUTS), WHEAT", font=font(SANS_BOLD, 28), fill=INK)
    draw_box(d, 50, 510, 850, 580, fill=(255, 255, 255), outline=INK, width=1)
    bx = 60
    rnd = random.Random(11)
    while bx < 840:
        w = rnd.choice([2, 3, 4])
        if rnd.random() < 0.1:
            w = 6
        draw_box(d, bx, 520, bx + w, 570, fill=INK, outline=INK)
        bx += w + rnd.choice([2, 3, 4])
    d.text((310, 585), "HK-GRAN-MAPLE-300G", font=font(MONO_BOLD, 18), fill=INK)
    d.text((50, 620), "Best before: see base of bag.", font=font(SANS, 12), fill=INK_LIGHT)
    label.save(out / "label.jpg", format="JPEG", quality=88, optimize=True)


# ---------------- scenario 3: held, incomplete (blurry receipt) ----------------
def render_blurry_receipt() -> None:
    out = ROOT / "hk-blurry-receipt"
    out.mkdir(parents=True, exist_ok=True)

    # Spec and CoA are normal.
    spec = Image.new("RGB", (1100, 850), PAPER)
    d = ImageDraw.Draw(spec)
    y = 40
    draw_box(d, 24, 24, 1076, 826, outline=LINE, width=2)
    d.text((50, y), "HARBOR KITCHEN", font=font(SANS_BOLD, 36), fill=ACCENT)
    y += 50
    d.text((50, y), "Product Specification - Buttermilk Biscuit Mix", font=font(SERIF, 22), fill=INK_LIGHT)
    y += 36
    d.line([(50, y), (1050, y)], fill=LINE, width=2)
    y += 24
    text = (
        "Product name      : Buttermilk Biscuit Mix\n"
        "SKU               : HK-BAK-BISC-450G\n"
        "Net weight        : 450 g (15.87 oz)\n"
        "Best-by window    : 12 months from pack date (sealed, ambient)\n"
        "Lot format        : HK-<LINE>-<YYMMDD>-<SEQ>\n"
        "Allergen statement: Contains wheat (flour) and milk (buttermilk\n"
        "                    powder). Produced in a shared facility that\n"
        "                    also handles egg and soy.\n"
    )
    y = draw_paragraph(d, 50, y, text, font(MONO, 15), 1000, line_spacing=4)
    y += 12
    d.text((50, y), "Allergen Summary (canonical):", font=font(SANS_BOLD, 20), fill=INK)
    y += 30
    for a in ("wheat", "milk"):
        d.text((60, y), "  -  " + a, font=font(MONO, 15), fill=INK)
        y += 24
    spec.save(out / "spec.png", format="PNG", optimize=True)

    coa = Image.new("RGB", (1100, 850), PAPER)
    d = ImageDraw.Draw(coa)
    y = 40
    draw_box(d, 24, 24, 1076, 826, outline=LINE, width=2)
    d.text((50, y), "CERTIFICATE OF ANALYSIS", font=font(SANS_BOLD, 36), fill=ACCENT)
    y += 50
    for k, v in [
        ("Issued by", "Heartland Mills Inc."),
        ("Supplier lot", "HM-WHL-2026-08-15-C"),
        ("Material", "Enriched wheat flour + buttermilk powder blend"),
        ("Processed", "2026-08-15"),
        ("Analyzed",  "2026-08-16"),
        ("Analyzed by", "J. Park (ISO 17025 lab)"),
    ]:
        d.text((50, y), f"{k:<14}", font=font(MONO, 15), fill=INK_LIGHT)
        d.text((180, y), v, font=font(MONO, 15), fill=INK)
        y += 28
    y += 12
    d.line([(50, y), (1050, y)], fill=LINE, width=2)
    y += 16
    d.text((50, y), "Allergen declaration (per supplier):", font=font(SANS_BOLD, 20), fill=INK)
    y += 28
    d.text((60, y), "Contains: WHEAT, MILK.", font=font(MONO, 15), fill=INK)
    coa.save(out / "coa.png", format="PNG", optimize=True)

    # Label: deliberately blurry + low-contrast so the model can't reliably OCR.
    label = Image.new("RGB", (900, 700), (210, 200, 185))
    d = ImageDraw.Draw(label)
    d = ImageDraw.Draw(label)
    draw_box(d, 30, 30, 870, 670, fill=(235, 225, 210), outline=INK_LIGHT, width=2)
    d.text((50, 60), "harbor kitchen", font=font(SANS_BOLD, 24), fill=(80, 70, 60))
    d.text((50, 95), "Buttermilk Biscuit Mix", font=font(SERIF, 20), fill=(90, 80, 70))
    d.text((50, 130), "450 g / 15.87 oz", font=font(SANS, 14), fill=(110, 100, 90))
    d.text((50, 165), "INGREDIENTS: Enriched wheat flour, buttermilk powder", font=font(SANS, 12), fill=(120, 110, 100))
    d.text((50, 185), "(wheat, milk, salt, leavening), soybean oil.", font=font(SANS, 12), fill=(120, 110, 100))
    d.text((50, 220), "CONTAINS:", font=font(SANS_BOLD, 18), fill=(180, 90, 30))
    d.text((50, 250), "WHEAT, MILK", font=font(SANS_BOLD, 28), fill=(60, 50, 45))
    # smear across the allergen line
    d.rectangle([50, 250, 350, 290], fill=(220, 210, 195))
    d.text((50, 250), "WHEAT, MILK", font=font(SANS_BOLD, 28), fill=(60, 50, 45))
    # barcode-ish squiggles
    bx = 60
    rnd = random.Random(99)
    while bx < 840:
        w = rnd.choice([2, 3, 4])
        draw_box(d, bx, 480, bx + w, 540, fill=(70, 60, 55), outline=(70, 60, 55))
        bx += w + rnd.choice([2, 3, 4])
    # blur + noise
    label = label.filter(ImageFilter.GaussianBlur(radius=1.6))
    label = stamp_noise(label, intensity=0.04)
    label = label.filter(ImageFilter.GaussianBlur(radius=0.6))
    label.save(out / "label.jpg", format="JPEG", quality=70, optimize=True)


# ---------------- scenario 4: held, incomplete (essentially blank label) ----------------
def render_blank_label() -> None:
    """Spec/CoA declare wheat+milk+eggs; label is a blank white card so
    the model legitimately cannot extract allergens -> incomplete_packet."""
    out = ROOT / "hk-empty-label"
    out.mkdir(parents=True, exist_ok=True)

    spec = Image.new("RGB", (1100, 850), PAPER)
    d = ImageDraw.Draw(spec)
    y = 40
    draw_box(d, 24, 24, 1076, 826, outline=LINE, width=2)
    d.text((50, y), "HARBOR KITCHEN", font=font(SANS_BOLD, 36), fill=ACCENT)
    y += 50
    d.text((50, y), "Product Specification - Vanilla Birthday Cake", font=font(SERIF, 22), fill=INK_LIGHT)
    y += 36
    d.line([(50, y), (1050, y)], fill=LINE, width=2)
    y += 24
    text = (
        "Product name      : Vanilla Birthday Cake (frozen, whole)\n"
        "SKU               : HK-BAK-CAKE-1.2KG\n"
        "Net weight        : 1.2 kg (42.3 oz)\n"
        "Best-by window    : 14 days from pack date (frozen at -18 C)\n"
        "Lot format        : HK-<LINE>-<YYMMDD>-<SEQ>\n"
        "Allergen statement: Contains wheat, milk, and eggs. Decorated\n"
        "                    with buttercream frosting.\n"
    )
    y = draw_paragraph(d, 50, y, text, font(MONO, 15), 1000, line_spacing=4)
    y += 12
    d.text((50, y), "Allergen Summary (canonical):", font=font(SANS_BOLD, 20), fill=INK)
    y += 30
    for a in ("wheat", "milk", "eggs"):
        d.text((60, y), "  -  " + a, font=font(MONO, 15), fill=INK)
        y += 24
    spec.save(out / "spec.png", format="PNG", optimize=True)

    coa = Image.new("RGB", (1100, 850), PAPER)
    d = ImageDraw.Draw(coa)
    y = 40
    draw_box(d, 24, 24, 1076, 826, outline=LINE, width=2)
    d.text((50, y), "CERTIFICATE OF ANALYSIS", font=font(SANS_BOLD, 36), fill=ACCENT)
    y += 50
    for k, v in [
        ("Issued by", "Sweetbriar Bakery LLC"),
        ("Supplier lot", "SB-CAKE-2026-08-21-B"),
        ("Material", "Vanilla cake batter (wheat flour, milk, eggs)"),
        ("Processed", "2026-08-21"),
        ("Analyzed",  "2026-08-22"),
        ("Analyzed by", "L. Cho (ISO 17025 lab)"),
    ]:
        d.text((50, y), f"{k:<14}", font=font(MONO, 15), fill=INK_LIGHT)
        d.text((180, y), v, font=font(MONO, 15), fill=INK)
        y += 28
    y += 12
    d.line([(50, y), (1050, y)], fill=LINE, width=2)
    y += 16
    d.text((50, y), "Allergen declaration (per supplier):", font=font(SANS_BOLD, 20), fill=INK)
    y += 28
    d.text((60, y), "Contains: WHEAT, MILK, EGGS.", font=font(MONO, 15), fill=INK)
    coa.save(out / "coa.png", format="PNG", optimize=True)

    # Label: a real-looking but genuinely-blank white card. No allergen line.
    label = Image.new("RGB", (900, 700), (250, 248, 242))
    d = ImageDraw.Draw(label)
    draw_box(d, 30, 30, 870, 670, fill=(255, 255, 255), outline=INK, width=3)
    draw_box(d, 30, 30, 870, 110, fill=ACCENT, outline=ACCENT, width=0)
    d.text((50, 50), "HARBOR KITCHEN", font=font(SANS_BOLD, 32), fill=(255, 255, 255))
    d.text((50, 130), "Vanilla Birthday Cake", font=font(SERIF, 26), fill=INK)
    d.text((50, 170), "1.2 kg / 42.3 oz  -  Keep Frozen", font=font(SANS, 18), fill=INK_LIGHT)
    d.line([(50, 210), (850, 210)], fill=LINE, width=2)
    d.text((50, 230), "INGREDIENTS:", font=font(SANS_BOLD, 18), fill=INK)
    body = (
        "Wheat flour, sugar, eggs, milk, butter,\n"
        "vanilla extract, baking powder, salt.\n"
    )
    draw_paragraph(d, 50, 260, body, font(SANS, 17), 800, line_spacing=4)
    # NO allergen line. This is the failure mode.
    d.line([(50, 350), (850, 350)], fill=LINE, width=2)
    d.text((50, 370), "[ allergen panel missing ]", font=font(SANS_BOLD, 18), fill=INK_LIGHT)
    draw_box(d, 50, 530, 850, 600, fill=(255, 255, 255), outline=INK, width=1)
    bx = 60
    rnd = random.Random(13)
    while bx < 840:
        w = rnd.choice([2, 3, 4])
        draw_box(d, bx, 540, bx + w, 590, fill=INK, outline=INK)
        bx += w + rnd.choice([2, 3, 4])
    d.text((310, 605), "HK-BAK-CAKE-1.2KG", font=font(MONO_BOLD, 18), fill=INK)
    d.text((50, 640), "Best before: see base of box.", font=font(SANS, 12), fill=INK_LIGHT)
    label.save(out / "label.jpg", format="JPEG", quality=88, optimize=True)


def main() -> None:
    print("rendering hk-raw-tuna ...")
    render_raw_tuna()
    print("rendering hk-tree-nuts-mix ...")
    render_tree_nuts_mix()
    print("rendering hk-blurry-receipt ...")
    render_blurry_receipt()
    print("rendering hk-empty-label ...")
    render_blank_label()
    print("done.")


if __name__ == "__main__":
    main()
