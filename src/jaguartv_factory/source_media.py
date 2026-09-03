from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from .binaries import require_binary


def contains_chinese(text: str, *, minimum: int = 3) -> bool:
    return sum(1 for character in str(text or "") if "\u4e00" <= character <= "\u9fff") >= minimum


def localized_backing_required(source_transcript: str, chinese_on_screen: bool) -> bool:
    return contains_chinese(source_transcript) and bool(chinese_on_screen)


def parse_tesseract_tsv(
    content: str, *, width: int, height: int, confidence: float = 40.0
) -> tuple[list[list[float]], bool]:
    lines: dict[tuple[str, str, str, str], list[tuple[int, int, int, int, str]]] = {}
    for raw in content.splitlines()[1:]:
        columns = raw.split("\t", 11)
        if len(columns) != 12:
            continue
        try:
            left, top, box_width, box_height = map(int, columns[6:10])
            score = float(columns[10])
        except ValueError:
            continue
        text = columns[11].strip()
        if score < confidence or not text:
            continue
        key = tuple(columns[index] for index in (1, 2, 3, 4))
        lines.setdefault(key, []).append((left, top, left + box_width, top + box_height, text))

    regions: list[list[float]] = []
    chinese = False
    for words in lines.values():
        left = min(item[0] for item in words)
        top = min(item[1] for item in words)
        right = max(item[2] for item in words)
        bottom = max(item[3] for item in words)
        x0, y0, x1, y1 = left / width, top / height, right / width, bottom / height
        line_width, line_height = x1 - x0, y1 - y0
        center_y = (y0 + y1) / 2
        protected_corner = (x0 <= 0.12 or x1 >= 0.88) and (y0 <= 0.12 or y1 >= 0.88)
        if protected_corner or line_width < 0.12 or not 0.012 <= line_height <= 0.16 or not 0.10 <= center_y <= 0.92:
            continue
        regions.append([
            round(max(0.0, x0 - 0.01), 3),
            round(max(0.0, y0 - 0.01), 3),
            round(min(1.0, x1 + 0.01), 3),
            round(min(1.0, y1 + 0.01), 3),
        ])
        chinese = chinese or contains_chinese(" ".join(item[4] for item in words), minimum=1)
    return regions, chinese


def detect_source_caption_regions(
    media: Path,
    *,
    start: float,
    duration: float,
    sample_count: int = 8,
    confidence: float = 40.0,
) -> dict[str, Any]:
    ffmpeg = require_binary("ffmpeg")
    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise RuntimeError("tesseract is required for source-caption detection")
    sample_count = max(3, min(12, int(sample_count)))
    timestamps = [
        max(0.0, float(start)) + float(duration) * (index + 1) / (sample_count + 1)
        for index in range(sample_count)
    ]
    regions: list[list[float]] = []
    chinese = False
    successful_samples = 0
    with tempfile.TemporaryDirectory(prefix="jaguartv-caption-scan-") as temporary:
        for index, timestamp in enumerate(timestamps, start=1):
            frame = Path(temporary) / f"frame-{index:02d}.png"
            extracted = subprocess.run(
                [
                    ffmpeg, "-y", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(media),
                    "-frames:v", "1", "-vf", "scale=1280:-2:force_original_aspect_ratio=decrease", str(frame),
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            if extracted.returncode != 0 or not frame.is_file():
                continue
            with Image.open(frame) as image:
                frame_width, frame_height = image.size
            detected = subprocess.run(
                [
                    tesseract, str(frame), "stdout", "-l", "chi_sim+chi_tra+eng",
                    "--psm", "11", "--oem", "1", "tsv",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            if detected.returncode != 0:
                continue
            successful_samples += 1
            frame_regions, frame_chinese = parse_tesseract_tsv(
                detected.stdout, width=frame_width, height=frame_height, confidence=confidence
            )
            regions.extend(frame_regions)
            chinese = chinese or frame_chinese
    if successful_samples == 0:
        raise RuntimeError("source-caption detection could not read any sampled frame")
    unique = sorted({tuple(region) for region in regions}, key=lambda item: (item[1], item[0]))
    return {
        "regions": [list(region) for region in unique[:32]],
        "has_chinese_text": chinese,
        "samples": successful_samples,
    }


def demucs_backing_track(
    media: Path,
    work: Path,
    *,
    start: float,
    duration: float,
    model: str = "htdemucs",
    timeout: float = 900.0,
) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    source_audio = work / "source_audio.wav"
    output_root = work / "demucs"
    expected = output_root / model / source_audio.stem / "no_vocals.wav"
    if expected.is_file() and expected.stat().st_size > 0:
        return expected
    extracted = subprocess.run(
        [
            require_binary("ffmpeg"), "-y", "-v", "error", "-ss", f"{max(0.0, start):.3f}",
            "-t", f"{max(0.1, duration):.3f}", "-i", str(media), "-vn", "-ac", "2", "-ar", "44100",
            str(source_audio),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if extracted.returncode != 0 or not source_audio.is_file():
        raise RuntimeError(f"source audio extraction failed: {extracted.stderr[-2000:]}")
    separated = subprocess.run(
        [
            sys.executable, "-m", "demucs", "-n", model, "--two-stems=vocals", "--shifts", "1",
            "-j", "1", "-o", str(output_root), str(source_audio),
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=max(60.0, float(timeout)),
    )
    if separated.returncode != 0 or not expected.is_file() or expected.stat().st_size <= 0:
        raise RuntimeError(f"Demucs backing-track separation failed: {separated.stderr[-3000:]}")
    return expected
