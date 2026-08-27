"""Auto-crop blank space from fixture images and produce README thumbnails.

Strategy: detect the background color from the border pixels, then find content
as anything that differs significantly from that background. This handles labels
that have a cream/paper background instead of pure white.
"""

from pathlib import Path
from PIL import Image
from collections import Counter

FIXTURES = Path("/opt/data/serverless/label-hold/fixtures")
THUMBS = FIXTURES / "thumbs"
THUMB_LONG_SIDE = 400  # px — keeps README compact, 5 scenarios scrollable

# How far from background (max channel diff) a pixel must be to count as content
BG_TOLERANCE = 25

# Padding around detected content bbox (px in the *original* image, before resize)
PAD = 8

# Scenarios wired into the live UI — these are the ones the README documents.
# Must match the preset list in apps/frontend/src/components/UploadPanel.tsx.
KEEP = {
    "hk-raw-tuna",
    "hk-multi-allergen",
    "hk-empty-label",
    "hk-tree-nuts-mix",
    "hk-multi-allergen-released",
}


def border_pixels(im: Image.Image, sample: int = 4) -> list[tuple[int, int, int]]:
    """Sample the outer ring of pixels — these are almost certainly background."""
    w, h = im.size
    coords: set[tuple[int, int]] = set()
    # Top and bottom rows
    for x in range(0, w, sample):
        coords.add((x, 0))
        coords.add((x, h - 1))
    # Left and right cols
    for y in range(0, h, sample):
        coords.add((0, y))
        coords.add((w - 1, y))
    return [im.getpixel(c) for c in coords]


def background_color(im: Image.Image) -> tuple[int, int, int]:
    """Return the most common border color (the background)."""
    border = border_pixels(im)
    most = Counter(border).most_common(1)[0][0]
    return most


def content_bbox(im: Image.Image, bg: tuple[int, int, int], tol: int = BG_TOLERANCE):
    """Bounding box of pixels that differ from `bg` by more than `tol` on any channel."""
    w, h = im.size
    min_x, min_y, max_x, max_y = w, h, -1, -1
    br, bg_g, bb = bg
    # Walk every row, find min/max x of content pixels
    for y in range(h):
        row_min_x = None
        row_max_x = None
        for x in range(0, w):
            r, g, b = im.getpixel((x, y))
            if abs(r - br) > tol or abs(g - bg_g) > tol or abs(b - bb) > tol:
                if row_min_x is None:
                    row_min_x = x
                row_max_x = x
        if row_min_x is not None:
            if row_min_x < min_x:
                min_x = row_min_x
            if row_max_x > max_x:
                max_x = row_max_x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
    if max_x < 0:
        return None
    return (min_x, min_y, max_x, max_y)


def crop_and_resize(src: Path, dst: Path):
    im = Image.open(src)
    if im.mode != "RGB":
        im = im.convert("RGB")
    bg = background_color(im)
    bbox = content_bbox(im, bg)
    if bbox is None:
        cropped = im
    else:
        l, t, r, b = bbox
        l = max(0, l - PAD)
        t = max(0, t - PAD)
        r = min(im.size[0], r + 1 + PAD)
        b = min(im.size[1], b + 1 + PAD)
        cropped = im.crop((l, t, r, b))
    w, h = cropped.size
    if max(w, h) > THUMB_LONG_SIDE:
        if w >= h:
            new_w = THUMB_LONG_SIDE
            new_h = round(h * (THUMB_LONG_SIDE / w))
        else:
            new_h = THUMB_LONG_SIDE
            new_w = round(w * (THUMB_LONG_SIDE / h))
        cropped = cropped.resize((new_w, new_h), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(dst, "WEBP", quality=82, method=6)
    return im.size, cropped.size, bg


def main():
    for scenario_dir in sorted(FIXTURES.iterdir()):
        if not scenario_dir.is_dir() or scenario_dir.name == "thumbs":
            continue
        if scenario_dir.name not in KEEP:
            print(f"skip  {scenario_dir.name}")
            continue
        for img_path in sorted(scenario_dir.iterdir()):
            if img_path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            dst = THUMBS / scenario_dir.name / f"{img_path.stem}.webp"
            orig, thumb, bg = crop_and_resize(img_path, dst)
            print(f"  ok  {scenario_dir.name}/{img_path.name:12s}  bg={bg}  {orig[0]}x{orig[1]} -> {thumb[0]}x{thumb[1]}  {dst.stat().st_size//1024}KB")


if __name__ == "__main__":
    main()
