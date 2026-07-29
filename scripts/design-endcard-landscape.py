#!/usr/bin/env python3
"""Rebuild the landscape JaguarTV end card from approved raster assets."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


CANVAS_SIZE = (1376, 768)
ARIAL = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
ARIAL_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")


def extract_lockup(lockup: Image.Image) -> Image.Image:
    """Remove the blue field while retaining the approved lockup pixels."""
    rgb = np.asarray(lockup.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hue, saturation, value = cv2.split(hsv)

    yellow_orange = (
        (hue >= 5) & (hue <= 45) & (saturation >= 70) & (value >= 75)
    )
    pale_highlight = (saturation <= 105) & (value >= 165)
    seed = (yellow_orange | pale_highlight).astype(np.uint8) * 255

    # Fill the outlined letters and jaguar body, then recover the nearby navy outline.
    contours, _ = cv2.findContours(seed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(seed)
    for contour in contours:
        if cv2.contourArea(contour) >= 18:
            cv2.drawContours(filled, [contour], -1, 255, thickness=cv2.FILLED)

    # The cyan TV letters share the background hue, so recover them from their
    # pale closed outline and fill the two letter shapes explicitly.
    tv_zone = np.zeros_like(seed)
    tv_zone[250:355, 295:] = 255
    tv_outline = (
        (tv_zone > 0) & (saturation <= 180) & (value >= 145)
    ).astype(np.uint8) * 255
    tv_contours, _ = cv2.findContours(
        tv_outline, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    for contour in tv_contours:
        if cv2.contourArea(contour) >= 10:
            cv2.drawContours(filled, [contour], -1, 255, thickness=cv2.FILLED)

    blue = rgb[:, :, 2].astype(np.int16)
    green = rgb[:, :, 1].astype(np.int16)
    red = rgb[:, :, 0].astype(np.int16)
    navy = (blue > green * 1.10) & (blue > red * 1.28) & (value < 140)
    nearby = cv2.dilate(
        filled, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    )
    mask = filled | ((navy & (nearby > 0)).astype(np.uint8) * 255)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    )
    mask = cv2.GaussianBlur(mask, (3, 3), 0.55)

    result = lockup.copy()
    result.putalpha(Image.fromarray(mask))
    return result


def rebuild_top_field(image: Image.Image) -> None:
    """Clear the old centered lockup using only untouched source background."""
    width = image.width
    height = 540
    patch = image.crop((0, 0, width, 54)).resize(
        (width, height), Image.Resampling.LANCZOS
    )
    patch = patch.filter(ImageFilter.GaussianBlur(8))

    mask = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(mask)
    for y in range(510, height):
        alpha = round(255 * (height - 1 - y) / (height - 1 - 510))
        draw.line((0, y, width, y), fill=alpha)
    image.paste(patch, (0, 0), mask)


def rounded_qr(qr: Image.Image, size: int = 282) -> Image.Image:
    qr = qr.convert("RGB").resize((size, size), Image.Resampling.NEAREST)
    frame = Image.new("RGBA", (size + 22, size + 22), (255, 255, 255, 255))
    frame.paste(qr, (11, 11))
    mask = Image.new("L", frame.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, frame.width - 1, frame.height - 1), radius=14, fill=255
    )
    frame.putalpha(mask)
    return frame


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
    selected = (peak >= brightness_threshold) | (
        (peak >= brightness_threshold - 22) & (channel_range >= 45)
    )
    local_mask = selected.astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilation, dilation))
    local_mask = cv2.dilate(local_mask, kernel, iterations=1)

    mask = np.zeros(bgr.shape[:2], dtype=np.uint8)
    mask[y0:y1, x0:x1] = local_mask
    repaired = cv2.inpaint(bgr, mask, 5, cv2.INPAINT_NS)
    image.paste(Image.fromarray(cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB)).convert("RGBA"))


def add_soft_backdrop(
    image: Image.Image,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int, int],
) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(box, radius=18, fill=fill)
    overlay = overlay.filter(ImageFilter.GaussianBlur(12))
    image.alpha_composite(overlay)


def add_shadowed_text(
    image: Image.Image,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    anchor: str = "mm",
    shadow_offset: tuple[int, int] = (2, 2),
) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.text(
        (xy[0] + shadow_offset[0], xy[1] + shadow_offset[1]),
        text,
        font=font,
        fill=(0, 0, 0, 150),
        anchor=anchor,
    )
    draw.text(xy, text, font=font, fill=fill + (255,), anchor=anchor)
    image.alpha_composite(overlay)


def build_endcard(source_path: Path, qr_path: Path, output_path: Path) -> None:
    source = Image.open(source_path).convert("RGBA")
    if source.size != CANVAS_SIZE:
        raise ValueError(f"Expected source size {CANVAS_SIZE}, got {source.size}")

    canvas = source.copy()
    qr_source = Image.open(qr_path)

    # Keep the approved logo, wording, font treatment, and colors as original pixels.
    lockup = extract_lockup(source.crop((487, 42, 905, 483)))
    rebuild_top_field(canvas)
    canvas.alpha_composite(lockup, (118, 39))

    qr = rounded_qr(qr_source)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (976, 74, 1300, 398), radius=18, fill=(0, 34, 54, 145)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(13))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(qr, (966, 64))

    # Remove the two old lines and replace them with the requested single CTA.
    inpaint_region(canvas, (245, 635, 405, 694), brightness_threshold=115, dilation=9)
    add_soft_backdrop(canvas, (237, 636, 411, 700), fill=(0, 54, 75, 190))
    jarg_font = ImageFont.truetype(str(ARIAL_BOLD), 31)
    add_shadowed_text(canvas, (324, 667), "Jarg.top", jarg_font, (255, 255, 255))

    # Preserve the TV icon and heading, then rebuild the copy with a larger red code.
    inpaint_region(canvas, (773, 637, 904, 694), brightness_threshold=112, dilation=8)
    inpaint_region(canvas, (735, 692, 910, 714), brightness_threshold=105, dilation=7)
    add_soft_backdrop(canvas, (760, 636, 907, 713), fill=(0, 56, 83, 182))
    downloader_font = ImageFont.truetype(str(ARIAL_BOLD), 19)
    number_font = ImageFont.truetype(str(ARIAL_BOLD), 27)
    arrow_font = ImageFont.truetype(str(ARIAL_BOLD), 22)
    subtitle_font = ImageFont.truetype(str(ARIAL), 13)

    add_shadowed_text(canvas, (840, 650), "Downloader", downloader_font, (255, 255, 255))

    number_text = "2252960"
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    number_box = measure.textbbox((0, 0), number_text, font=number_font)
    arrow_box = measure.textbbox((0, 0), "\u2192", font=arrow_font)
    number_width = number_box[2] - number_box[0]
    arrow_width = arrow_box[2] - arrow_box[0]
    group_width = number_width + 6 + arrow_width
    group_left = 840 - group_width // 2
    add_shadowed_text(
        canvas,
        (group_left, 678),
        number_text,
        number_font,
        (239, 57, 55),
        anchor="lm",
    )
    add_shadowed_text(
        canvas,
        (group_left + number_width + 6, 678),
        "\u2192",
        arrow_font,
        (255, 255, 255),
        anchor="lm",
    )
    add_shadowed_text(
        canvas,
        (840, 705),
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
