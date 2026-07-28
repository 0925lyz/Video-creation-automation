#!/usr/bin/env python3
"""Rebuild the portrait JaguarTV end card from the approved raster assets."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


CANVAS_SIZE = (768, 1376)
ARIAL_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")


def feathered_rectangle(size: tuple[int, int], inset: int = 22) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((inset, inset, size[0] - inset, size[1] - inset), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(inset / 2))


def inpaint_region(
    image: Image.Image,
    box: tuple[int, int, int, int],
    brightness_threshold: int,
    dilation: int,
) -> None:
    rgb = np.asarray(image.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    x0, y0, x1, y1 = box
    roi = bgr[y0:y1, x0:x1]
    peak = roi.max(axis=2)
    channel_range = roi.max(axis=2) - roi.min(axis=2)

    # Text and brand pixels are brighter or more chromatic than the dark green field.
    selected = (peak >= brightness_threshold) | (
        (peak >= brightness_threshold - 25) & (channel_range >= 38)
    )
    local_mask = (selected.astype(np.uint8) * 255)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilation, dilation))
    local_mask = cv2.dilate(local_mask, kernel, iterations=1)

    mask = np.zeros(bgr.shape[:2], dtype=np.uint8)
    mask[y0:y1, x0:x1] = local_mask
    repaired = cv2.inpaint(bgr, mask, 5, cv2.INPAINT_NS)
    repaired_rgb = cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB)
    image.paste(Image.fromarray(repaired_rgb).convert("RGBA"))


def add_shadowed_text(
    image: Image.Image,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    anchor: str = "mm",
    shadow_offset: tuple[int, int] = (2, 3),
    shadow_fill: tuple[int, int, int, int] = (0, 0, 0, 145),
) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.text(
        (xy[0] + shadow_offset[0], xy[1] + shadow_offset[1]),
        text,
        font=font,
        fill=shadow_fill,
        anchor=anchor,
    )
    draw.text(xy, text, font=font, fill=fill + (255,), anchor=anchor)
    image.alpha_composite(overlay)


def replace_top_field(image: Image.Image) -> None:
    """Build a calm, texture-matched upper field from the logo-free top edge."""
    width = image.width
    height = 468
    patch = image.crop((0, 0, width, 126)).resize(
        (width, height), Image.Resampling.LANCZOS
    )
    patch = patch.filter(ImageFilter.GaussianBlur(9))

    mask = Image.new("L", (width, height), 255)
    mask_pixels = mask.load()
    for y in range(430, height):
        alpha = round(255 * (height - 1 - y) / (height - 1 - 430))
        for x in range(width):
            mask_pixels[x, y] = alpha
    image.paste(patch, (0, 0), mask)


def add_soft_backdrop(
    image: Image.Image,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int, int] = (0, 42, 30, 155),
) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).ellipse(box, fill=fill)
    overlay = overlay.filter(ImageFilter.GaussianBlur(18))
    image.alpha_composite(overlay)


def rounded_qr(qr: Image.Image, size: int = 250) -> Image.Image:
    qr = qr.convert("RGB").resize((size, size), Image.Resampling.NEAREST)
    frame = Image.new("RGBA", (size + 16, size + 16), (255, 255, 255, 255))
    frame.paste(qr, (8, 8))

    mask = Image.new("L", frame.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, frame.width - 1, frame.height - 1), radius=12, fill=255
    )
    frame.putalpha(mask)
    return frame


def build_endcard(source_path: Path, qr_path: Path, output_path: Path) -> None:
    source = Image.open(source_path).convert("RGBA")
    if source.size != CANVAS_SIZE:
        raise ValueError(f"Expected source size {CANVAS_SIZE}, got {source.size}")

    qr_source = Image.open(qr_path)
    canvas = source.copy()

    # Save the exact approved brand lockup before clearing its old centered position.
    lockup = source.crop((242, 128, 526, 401))

    # Rebuild the whole upper field from an untouched background strip. A full-width
    # vertical fade keeps the transition invisible and avoids inpainting streaks.
    replace_top_field(canvas)

    # Upper hierarchy: brand lockup on the left and scannable QR on the right.
    lockup_mask = feathered_rectangle(lockup.size, inset=18)
    canvas.paste(lockup, (48, 112), lockup_mask)

    qr = rounded_qr(qr_source, size=246)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (463, 112, 725, 374), radius=14, fill=(0, 26, 19, 125)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(qr, (451, 100))

    # Replace only the requested lower copy. The surrounding labels and icons stay intact.
    inpaint_region(canvas, (75, 858, 335, 965), brightness_threshold=78, dilation=15)
    add_soft_backdrop(canvas, (54, 855, 352, 982), fill=(0, 34, 25, 205))
    jarg_font = ImageFont.truetype(str(ARIAL_BOLD), 48)
    add_shadowed_text(canvas, (203, 907), "Jarg.top", jarg_font, (255, 255, 255))

    inpaint_region(canvas, (435, 862, 705, 987), brightness_threshold=88, dilation=11)
    downloader_font = ImageFont.truetype(str(ARIAL_BOLD), 31)
    number_font = ImageFont.truetype(str(ARIAL_BOLD), 40)
    arrow_font = ImageFont.truetype(str(ARIAL_BOLD), 35)
    subtitle_font = ImageFont.truetype(
        "/System/Library/Fonts/Supplemental/Arial.ttf", 20
    )

    add_shadowed_text(
        canvas,
        (570, 890),
        "Downloader",
        downloader_font,
        (255, 255, 255),
    )

    number_text = "2252960"
    arrow_text = "\u2192"
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    number_box = measure.textbbox((0, 0), number_text, font=number_font)
    arrow_box = measure.textbbox((0, 0), arrow_text, font=arrow_font)
    number_width = number_box[2] - number_box[0]
    arrow_width = arrow_box[2] - arrow_box[0]
    gap = 9
    group_width = number_width + gap + arrow_width
    group_left = 570 - group_width // 2

    add_shadowed_text(
        canvas,
        (group_left, 932),
        number_text,
        number_font,
        (239, 57, 55),
        anchor="lm",
    )
    add_shadowed_text(
        canvas,
        (group_left + number_width + gap, 932),
        arrow_text,
        arrow_font,
        (255, 255, 255),
        anchor="lm",
    )
    add_shadowed_text(
        canvas,
        (570, 969),
        "Android TV e TV Box",
        subtitle_font,
        (255, 255, 255),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, "PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--qr", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_endcard(args.source, args.qr, args.output)


if __name__ == "__main__":
    main()
