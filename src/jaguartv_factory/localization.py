from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from PIL import Image

from .binaries import require_binary

@dataclass(frozen=True)
class LocalizationClass:
    class_id: int
    chinese_audio: bool
    chinese_on_screen: bool
    language: str
    language_probability: float
    chinese_characters: int
    transcript: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.class_id,
            "chinese_audio": self.chinese_audio,
            "chinese_on_screen": self.chinese_on_screen,
            "language": self.language,
            "language_probability": self.language_probability,
            "chinese_characters": self.chinese_characters,
            "transcript": self.transcript,
            "reason": self.reason,
        }


def _run(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    if args and args[0] in {"ffmpeg", "ffprobe"}:
        args = [require_binary(args[0]), *args[1:]]
    return subprocess.run(args, check=check, text=True, capture_output=True)


def _probe_dimensions(media: Path) -> tuple[int, int]:
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
        "-of", "json", str(media),
    ])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    stream = json.loads(result.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _probe_duration(media: Path) -> float:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(media),
    ])
    if result.returncode != 0:
        return 0.0
    try:
        return max(0.0, float(result.stdout.strip() or 0.0))
    except ValueError:
        return 0.0


def classify_chinese_audio(media: Path, *, model_name: str = "base", threshold: int = 3) -> dict[str, Any]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError("faster-whisper is not installed; install .[asr]") from error
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(media), beam_size=5)
    transcript = " ".join(segment.text.strip() for segment in segments).strip()
    chinese_characters = sum(1 for character in transcript if "\u4e00" <= character <= "\u9fff")
    return {
        "chinese_audio": chinese_characters >= threshold,
        "language": str(getattr(info, "language", "unknown") or "unknown"),
        "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 3),
        "chinese_characters": chinese_characters,
        "transcript": transcript,
    }


def _sample_frames(media: Path, destination: Path, count: int = 8, fps: float = 2.0) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    result = _run([
        "ffmpeg", "-y", "-i", str(media), "-vf", f"fps={fps:g},scale=-2:1080", "-frames:v", str(count),
        str(destination / "frame-%03d.png"),
    ])
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])
    return sorted(destination.glob("frame-*.png"))


def _sample_frame_times(media: Path, destination: Path, count: int, fps: float) -> list[tuple[Path, float]]:
    frames = _sample_frames(media, destination, count=count, fps=fps)
    step = 1.0 / max(0.1, fps)
    return [(frame, index * step) for index, frame in enumerate(frames)]


def _is_protected_corner(x: float, y: float, w: float, h: float, ratio: float = 0.12) -> bool:
    return (x <= ratio or x + w >= 1 - ratio) and (y <= ratio or y + h >= 1 - ratio)


def _merge_regions(regions: Sequence[tuple[float, float, float, float]]) -> list[list[float]]:
    merged: list[list[float]] = []
    for current in sorted(regions, key=lambda item: item[1]):
        x0, y0, x1, y1 = current
        target = next((band for band in merged if y0 < band[3] + 0.02 and y1 > band[1] - 0.02), None)
        if target:
            target[0] = min(target[0], x0)
            target[1] = min(target[1], y0)
            target[2] = max(target[2], x1)
            target[3] = max(target[3], y1)
        else:
            merged.append([x0, y0, x1, y1])
    return [
        [max(0.0, x0 - 0.01), max(0.0, y0 - 0.02), min(1.0, x1 + 0.01), min(1.0, y1 + 0.02)]
        for x0, y0, x1, y1 in merged
        if 0 < y1 - y0 <= 0.25
    ]


def _subtitle_band_regions(regions: Sequence[Sequence[float]]) -> list[list[float]]:
    bands: list[list[float]] = []
    for x0, y0, x1, y1 in regions:
        width = x1 - x0
        height = y1 - y0
        center_y = (y0 + y1) / 2
        if center_y < 0.10 or center_y > 0.92:
            continue
        if width < 0.08 or height < 0.012 or height > 0.16:
            continue
        bands.append([
            max(0.0, x0 - 0.015),
            max(0.0, y0 - 0.008),
            min(1.0, x1 + 0.015),
            min(1.0, y1 + 0.008),
        ])
    return sorted(bands, key=lambda item: (item[1], item[0]))[:3]


def _timed_subtitle_region_events(
    frame_regions: Sequence[tuple[float, list[list[float]]]],
    *,
    duration: float,
    sample_fps: float,
) -> list[dict[str, Any]]:
    step = 1.0 / max(0.1, sample_fps)
    padding = min(0.35, step * 0.55)
    events: list[dict[str, Any]] = []
    for timestamp, regions in frame_regions:
        if not regions:
            continue
        start = max(0.0, timestamp - padding)
        end = min(max(duration, start + step), timestamp + step + padding)
        events.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "regions": regions,
        })
    return events


def _unique_regions(events: Sequence[dict[str, Any]]) -> list[list[float]]:
    seen: set[tuple[int, int, int, int]] = set()
    unique: list[list[float]] = []
    for event in events:
        for region in event.get("regions") or []:
            key = tuple(int(round(float(value) * 1000)) for value in region)
            if key in seen:
                continue
            seen.add(key)
            unique.append([float(value) for value in region])
    return unique


def _paddleocr_frame_regions(
    frame: Path, *, confidence: float
) -> list[tuple[float, float, float, float]]:
    try:
        from paddleocr import PaddleOCR
    except ImportError as error:
        raise RuntimeError("paddleocr is not installed") from error

    ocr = PaddleOCR(use_angle_cls=True, lang="ch")
    with Image.open(frame) as image:
        width, height = image.size
    try:
        result = ocr.predict(str(frame))
    except AttributeError:
        result = ocr.ocr(str(frame), cls=True)
    except TypeError:
        result = ocr.ocr(str(frame), cls=True)

    regions: list[tuple[float, float, float, float]] = []
    payloads = result if isinstance(result, list) else [result]
    for payload in payloads:
        if isinstance(payload, dict):
            boxes = payload.get("rec_boxes") or payload.get("dt_polys") or []
            texts = payload.get("rec_texts") or []
            scores = payload.get("rec_scores") or []
            for index, box in enumerate(boxes):
                text = str(texts[index] if index < len(texts) else "").strip()
                score = float(scores[index] if index < len(scores) else 1.0)
                if score < confidence or not re.search(r"[\u4e00-\u9fff]", text):
                    continue
                if len(box) == 4 and all(isinstance(value, (int, float)) for value in box):
                    left, top, right, bottom = [float(value) for value in box]
                    x, y, w, h = left / width, top / height, (right - left) / width, (bottom - top) / height
                else:
                    xs = [float(point[0]) for point in box]
                    ys = [float(point[1]) for point in box]
                    x, y = min(xs) / width, min(ys) / height
                    w, h = (max(xs) - min(xs)) / width, (max(ys) - min(ys)) / height
                if not _is_protected_corner(x, y, w, h):
                    regions.append((x, y, x + w, y + h))
            continue
        for line in payload or []:
            try:
                box, (text, score) = line
            except (TypeError, ValueError):
                continue
            if float(score) < confidence or not re.search(r"[\u4e00-\u9fff]", str(text)):
                continue
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            x, y = min(xs) / width, min(ys) / height
            w, h = (max(xs) - min(xs)) / width, (max(ys) - min(ys)) / height
            if not _is_protected_corner(x, y, w, h):
                regions.append((x, y, x + w, y + h))
    return regions


def _detect_chinese_text_regions_tesseract(
    media: Path, *, confidence: float, sample_count: int | None, sample_fps: float
) -> list[list[float]]:
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract is not installed")
    if sample_count is None:
        duration = _probe_duration(media)
        sample_count = max(8, min(120, int(max(duration, 4.0) * sample_fps)))
    regions: list[tuple[float, float, float, float]] = []
    with tempfile.TemporaryDirectory(prefix="jaguartv-ocr-") as temporary:
        for frame in _sample_frames(media, Path(temporary), sample_count, fps=sample_fps):
            with Image.open(frame) as image:
                width, height = image.size
            output_base = frame.with_suffix("")
            result = _run([
                "tesseract", str(frame), str(output_base), "-l", "chi_sim+chi_tra+eng",
                "--psm", "11", "--oem", "1", "tsv",
            ])
            tsv_path = output_base.with_suffix(".tsv")
            if result.returncode != 0 or not tsv_path.is_file():
                continue
            for line in tsv_path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
                columns = line.split("\t", 11)
                if len(columns) != 12:
                    continue
                try:
                    left, top, box_width, box_height = map(int, columns[6:10])
                    box_confidence = float(columns[10])
                except ValueError:
                    continue
                text = columns[11].strip()
                if box_confidence < confidence or not re.search(r"[\u4e00-\u9fff]", text):
                    continue
                normalized = (left / width, top / height, box_width / width, box_height / height)
                if _is_protected_corner(*normalized):
                    continue
                x, y, w, h = normalized
                regions.append((x, y, x + w, y + h))
    return _subtitle_band_regions(_merge_regions(regions))


def _detect_chinese_text_region_events_tesseract(
    media: Path, *, confidence: float, sample_count: int | None, sample_fps: float
) -> list[dict[str, Any]]:
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract is not installed")
    duration = _probe_duration(media)
    if sample_count is None:
        sample_count = max(8, min(180, int(max(duration, 4.0) * sample_fps)))
    frame_regions: list[tuple[float, list[list[float]]]] = []
    with tempfile.TemporaryDirectory(prefix="jaguartv-ocr-") as temporary:
        for frame, timestamp in _sample_frame_times(media, Path(temporary), sample_count, sample_fps):
            with Image.open(frame) as image:
                width, height = image.size
            output_base = frame.with_suffix("")
            result = _run([
                "tesseract", str(frame), str(output_base), "-l", "chi_sim+chi_tra+eng",
                "--psm", "11", "--oem", "1", "tsv",
            ])
            tsv_path = output_base.with_suffix(".tsv")
            regions: list[tuple[float, float, float, float]] = []
            if result.returncode == 0 and tsv_path.is_file():
                for line in tsv_path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
                    columns = line.split("\t", 11)
                    if len(columns) != 12:
                        continue
                    try:
                        left, top, box_width, box_height = map(int, columns[6:10])
                        box_confidence = float(columns[10])
                    except ValueError:
                        continue
                    text = columns[11].strip()
                    if box_confidence < confidence or not re.search(r"[\u4e00-\u9fff]", text):
                        continue
                    normalized = (left / width, top / height, box_width / width, box_height / height)
                    if _is_protected_corner(*normalized):
                        continue
                    x, y, w, h = normalized
                    regions.append((x, y, x + w, y + h))
            frame_regions.append((timestamp, _subtitle_band_regions(_merge_regions(regions))))
    return _timed_subtitle_region_events(frame_regions, duration=duration, sample_fps=sample_fps)


def _detect_chinese_text_regions_paddleocr(
    media: Path, *, confidence: float, sample_count: int | None, sample_fps: float
) -> list[list[float]]:
    if sample_count is None:
        duration = _probe_duration(media)
        sample_count = max(8, min(120, int(max(duration, 4.0) * sample_fps)))
    regions: list[tuple[float, float, float, float]] = []
    with tempfile.TemporaryDirectory(prefix="jaguartv-paddleocr-") as temporary:
        for frame in _sample_frames(media, Path(temporary), sample_count, fps=sample_fps):
            regions.extend(_paddleocr_frame_regions(frame, confidence=confidence / 100.0))
    return _subtitle_band_regions(_merge_regions(regions))


def _detect_chinese_text_region_events_paddleocr(
    media: Path, *, confidence: float, sample_count: int | None, sample_fps: float
) -> list[dict[str, Any]]:
    duration = _probe_duration(media)
    if sample_count is None:
        sample_count = max(8, min(180, int(max(duration, 4.0) * sample_fps)))
    frame_regions: list[tuple[float, list[list[float]]]] = []
    with tempfile.TemporaryDirectory(prefix="jaguartv-paddleocr-") as temporary:
        for frame, timestamp in _sample_frame_times(media, Path(temporary), sample_count, sample_fps):
            regions = _subtitle_band_regions(_merge_regions(_paddleocr_frame_regions(frame, confidence=confidence / 100.0)))
            frame_regions.append((timestamp, regions))
    return _timed_subtitle_region_events(frame_regions, duration=duration, sample_fps=sample_fps)


def detect_chinese_text_regions(
    media: Path,
    *,
    confidence: float = 40.0,
    sample_count: int | None = None,
    sample_fps: float = 2.0,
    backend: str = "tesseract",
) -> list[list[float]]:
    backend = backend.strip().lower()
    if backend in {"paddleocr", "paddle"}:
        return _detect_chinese_text_regions_paddleocr(
            media, confidence=confidence, sample_count=sample_count, sample_fps=sample_fps
        )
    if backend in {"tesseract", "auto"}:
        try:
            return _detect_chinese_text_regions_tesseract(
                media, confidence=confidence, sample_count=sample_count, sample_fps=sample_fps
            )
        except RuntimeError:
            if backend == "auto":
                return _detect_chinese_text_regions_paddleocr(
                    media, confidence=confidence, sample_count=sample_count, sample_fps=sample_fps
                )
            raise
    raise RuntimeError("edit.ocr_backend must be tesseract, paddleocr, or auto")


def detect_chinese_text_region_events(
    media: Path,
    *,
    confidence: float = 40.0,
    sample_count: int | None = None,
    sample_fps: float = 2.0,
    backend: str = "tesseract",
) -> list[dict[str, Any]]:
    backend = backend.strip().lower()
    if backend in {"paddleocr", "paddle"}:
        return _detect_chinese_text_region_events_paddleocr(
            media, confidence=confidence, sample_count=sample_count, sample_fps=sample_fps
        )
    if backend in {"tesseract", "auto"}:
        try:
            return _detect_chinese_text_region_events_tesseract(
                media, confidence=confidence, sample_count=sample_count, sample_fps=sample_fps
            )
        except RuntimeError:
            if backend == "auto":
                return _detect_chinese_text_region_events_paddleocr(
                    media, confidence=confidence, sample_count=sample_count, sample_fps=sample_fps
                )
            raise
    raise RuntimeError("edit.ocr_backend must be tesseract, paddleocr, or auto")


def blur_static_regions(
    media: Path, output: Path, regions: Sequence[Sequence[float]], *, sigma: int = 28
) -> Path:
    if not regions:
        shutil.copy2(media, output)
        return output
    width, height = _probe_dimensions(media)
    labels = "".join(f"[crop{index}]" for index in range(len(regions)))
    filters = [f"[0:v]split={len(regions) + 1}[base]{labels}"]
    current = "base"
    for index, (x0, y0, x1, y1) in enumerate(regions):
        x = max(0, min(width - 2, int(width * x0)))
        y = max(0, min(height - 2, int(height * y0)))
        box_width = max(2, min(width - x, int(width * (x1 - x0))))
        box_height = max(2, min(height - y, int(height * (y1 - y0))))
        filters.append(f"[crop{index}]crop={box_width}:{box_height}:{x}:{y},gblur=sigma={sigma}[blur{index}]")
        next_label = f"merged{index}"
        filters.append(f"[{current}][blur{index}]overlay={x}:{y}[{next_label}]")
        current = next_label
    output.parent.mkdir(parents=True, exist_ok=True)
    result = _run([
        "ffmpeg", "-y", "-i", str(media), "-filter_complex", ";".join(filters),
        "-map", f"[{current}]", "-map", "0:a?", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output),
    ])
    if result.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        output.unlink(missing_ok=True)
        raise RuntimeError("OCR blur render failed:\n" + result.stderr[-4000:])
    return output


def blur_timed_regions(
    media: Path,
    output: Path,
    events: Sequence[dict[str, Any]],
    *,
    sigma: int = 48,
) -> Path:
    normalized_events = [
        event for event in events
        if float(event.get("end") or 0) > float(event.get("start") or 0) and event.get("regions")
    ]
    if not normalized_events:
        shutil.copy2(media, output)
        return output
    width, height = _probe_dimensions(media)
    overlays: list[tuple[int, int, int, int, float, float]] = []
    for event in normalized_events:
        start = max(0.0, float(event.get("start") or 0.0))
        end = max(start + 0.05, float(event.get("end") or start))
        for x0, y0, x1, y1 in event.get("regions") or []:
            x = max(0, min(width - 2, int(width * float(x0))))
            y = max(0, min(height - 2, int(height * float(y0))))
            box_width = max(2, min(width - x, int(math.ceil(width * (float(x1) - float(x0))))))
            box_height = max(2, min(height - y, int(math.ceil(height * (float(y1) - float(y0))))))
            overlays.append((x, y, box_width, box_height, start, end))
    if not overlays:
        shutil.copy2(media, output)
        return output
    labels = "".join(f"[crop{index}]" for index in range(len(overlays)))
    filters = [f"[0:v]split={len(overlays) + 1}[base]{labels}"]
    current = "base"
    for index, (x, y, box_width, box_height, start, end) in enumerate(overlays):
        filters.append(f"[crop{index}]crop={box_width}:{box_height}:{x}:{y},gblur=sigma={sigma}[blur{index}]")
        next_label = f"merged{index}"
        filters.append(
            f"[{current}][blur{index}]overlay={x}:{y}:enable='between(t,{start:.3f},{end:.3f})'[{next_label}]"
        )
        current = next_label
    output.parent.mkdir(parents=True, exist_ok=True)
    result = _run([
        "ffmpeg", "-y", "-i", str(media), "-filter_complex", ";".join(filters),
        "-map", f"[{current}]", "-map", "0:a?", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output),
    ])
    if result.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        output.unlink(missing_ok=True)
        raise RuntimeError("timed OCR blur render failed:\n" + result.stderr[-4000:])
    return output


def prepare_ocr_blurred_segment(
    media: Path,
    output: Path,
    *,
    start: float,
    duration: float,
    sigma: int = 28,
    fallback_regions: Sequence[Sequence[float]] | None = None,
    backend: str = "tesseract",
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    extracted = output.with_name(f"{output.stem}_source.mp4")
    result = _run([
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(media),
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(extracted),
    ])
    if result.returncode != 0:
        raise RuntimeError("segment extraction failed:\n" + result.stderr[-4000:])
    try:
        events = detect_chinese_text_region_events(extracted, backend=backend)
    except RuntimeError as error:
        regions = [list(region) for region in (fallback_regions or [])]
        if regions:
            blur_static_regions(extracted, output, regions, sigma=sigma)
            return {
                "used": True,
                "regions": regions,
                "reason": f"fallback_regions_blurred_after_ocr_error:{error}",
                "media": str(output),
            }
        shutil.copy2(extracted, output)
        return {"used": False, "regions": [], "reason": str(error), "media": str(output)}
    if not events and fallback_regions:
        # Explicit operator fallback only; auto broad fallback is disabled in production.
        regions = [list(region) for region in fallback_regions]
        blur_static_regions(extracted, output, regions, sigma=sigma)
        return {
            "used": True,
            "regions": regions,
            "reason": "fallback_regions_blurred_no_chinese_regions_detected",
            "media": str(output),
        }
    regions = _unique_regions(events)
    blur_timed_regions(extracted, output, events, sigma=sigma)
    return {
        "used": bool(regions),
        "regions": regions,
        "timed_regions": events,
        "reason": "chinese_regions_blurred" if regions else "no_chinese_regions_detected",
        "media": str(output),
    }


def classify_localization(media: Path, *, model_name: str = "base") -> LocalizationClass:
    audio = classify_chinese_audio(media, model_name=model_name)
    if not audio["chinese_audio"]:
        return LocalizationClass(2, False, False, audio["language"], audio["language_probability"], audio["chinese_characters"], audio["transcript"], "no_chinese_audio")
    try:
        regions = detect_chinese_text_regions(media)
    except RuntimeError:
        regions = []
    class_id = 1 if regions else 3
    return LocalizationClass(
        class_id,
        True,
        bool(regions),
        audio["language"],
        audio["language_probability"],
        audio["chinese_characters"],
        audio["transcript"],
        "chinese_audio_and_screen_text" if regions else "chinese_audio_without_screen_text",
    )


def edge_tts_ptbr(
    text: str, destination: Path, *, voice: str = "pt-BR-AntonioNeural", rate: str = "+8%"
) -> Path:
    try:
        import edge_tts
    except ImportError as error:
        raise RuntimeError("edge-tts is not installed; install .[localization]") from error

    async def synthesize(path: Path) -> None:
        await edge_tts.Communicate(text, voice, rate=rate).save(str(path))

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jaguartv-tts-") as temporary:
        mp3 = Path(temporary) / "voice.mp3"
        try:
            asyncio.run(synthesize(mp3))
        except Exception as error:
            raise RuntimeError(f"edge-tts synthesis failed: {error}") from error
        result = _run(["ffmpeg", "-y", "-i", str(mp3), "-ar", "48000", "-ac", "2", str(destination)])
        if result.returncode != 0:
            raise RuntimeError("edge-tts conversion failed:\n" + result.stderr[-2000:])
    return destination


def demucs_backing_track(media: Path, work: Path, *, model: str = "htdemucs") -> Path:
    output_root = work / "demucs"
    expected = output_root / model / media.stem / "no_vocals.wav"
    if expected.exists() and expected.stat().st_size > 0:
        return expected
    result = _run([
        os.environ.get("PYTHON", sys.executable), "-m", "demucs", "-n", model, "--two-stems=vocals",
        "-o", str(output_root), str(media),
    ])
    if result.returncode != 0 or not expected.exists():
        raise RuntimeError("demucs separation failed:\n" + result.stderr[-3000:])
    return expected
