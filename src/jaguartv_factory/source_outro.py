from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageChops, ImageStat


RunCommand = Callable[..., subprocess.CompletedProcess[str]]

PROMO_TERM_DEFAULTS: dict[str, list[str]] = {
    "zh": ["关注", "私信", "微信", "公众号", "下载", "扫码", "二维码", "加群"],
    "pt": ["siga", "seguir", "inscreva-se", "baixe", "link na bio", "compartilhe", "whatsapp", "promoção"],
    "en": ["follow", "subscribe", "download", "link in bio", "scan", "whatsapp", "instagram", "tiktok", "facebook"],
}


@dataclass(frozen=True)
class SourceOutroSettings:
    enabled: bool = True
    scan_tail_sec: float = 15.0
    min_trim_sec: float = 2.0
    max_trim_sec: float = 12.0
    min_confidence: float = 0.72
    preserve_if_unsure: bool = True
    ocr_enabled: bool = True
    audio_guard_enabled: bool = True
    save_evidence_frames: bool = True
    promo_terms: dict[str, list[str]] | None = None


def source_outro_settings(config: dict[str, Any]) -> SourceOutroSettings:
    raw = config.get("source_outro_trim") or {}
    promo_terms = raw.get("promo_terms") if isinstance(raw.get("promo_terms"), dict) else PROMO_TERM_DEFAULTS
    normalized_terms: dict[str, list[str]] = {}
    for language, terms in promo_terms.items():
        if isinstance(terms, list):
            normalized_terms[str(language)] = [str(term).strip() for term in terms if str(term).strip()]
    return SourceOutroSettings(
        enabled=bool(raw.get("enabled", True)),
        scan_tail_sec=max(3.0, float(raw.get("scan_tail_sec", 15))),
        min_trim_sec=max(0.5, float(raw.get("min_trim_sec", 2))),
        max_trim_sec=max(1.0, float(raw.get("max_trim_sec", 12))),
        min_confidence=max(0.0, min(1.0, float(raw.get("min_confidence", 0.72)))),
        preserve_if_unsure=bool(raw.get("preserve_if_unsure", True)),
        ocr_enabled=bool(raw.get("ocr_enabled", True)),
        audio_guard_enabled=bool(raw.get("audio_guard_enabled", True)),
        save_evidence_frames=bool(raw.get("save_evidence_frames", True)),
        promo_terms=normalized_terms or PROMO_TERM_DEFAULTS,
    )


def normalize_source_outro_options(options: dict[str, Any] | None) -> dict[str, Any]:
    raw = (options or {}).get("source_outro_trim")
    if not isinstance(raw, dict):
        raw = options or {}
    disabled = bool(raw.get("disable_outro_trim", False) or raw.get("disable", False))
    force_value = raw.get("force_trim_end_sec")
    force_trim_end_sec: float | None = None
    if force_value not in {None, ""}:
        force_trim_end_sec = max(0.0, float(force_value))
    return {"disable_outro_trim": disabled, "force_trim_end_sec": force_trim_end_sec}


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def write_detection(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def frame_diff_score(a: Path, b: Path) -> float:
    with Image.open(a) as first, Image.open(b) as second:
        left = first.convert("RGB").resize((160, 90))
        right = second.convert("RGB").resize((160, 90))
        diff = ImageChops.difference(left, right)
        stat = ImageStat.Stat(diff)
        return float(sum(stat.mean) / (3 * 255))


def extract_tail_frames(
    media: Path,
    directory: Path,
    *,
    duration: float,
    tail_start: float,
    run_command: RunCommand,
) -> list[tuple[float, Path]]:
    points = [
        max(0.0, tail_start),
        max(0.0, duration - 9.0),
        max(0.0, duration - 6.0),
        max(0.0, duration - 3.0),
        max(0.0, duration - 1.0),
    ]
    selected = sorted(set(round(min(duration - 0.05, point), 3) for point in points if point < duration - 0.05))
    frames: list[tuple[float, Path]] = []
    for index, point in enumerate(selected, 1):
        frame = directory / f"tail_{index:02d}.jpg"
        result = run_command([
            "ffmpeg", "-y", "-ss", f"{point:.3f}", "-i", str(media),
            "-frames:v", "1", "-q:v", "3", str(frame),
        ], check=False)
        if result.returncode == 0 and frame.is_file() and frame.stat().st_size > 0:
            frames.append((point, frame))
    return frames


def extract_evidence_frame(media: Path, destination: Path, at_sec: float, run_command: RunCommand) -> str:
    result = run_command([
        "ffmpeg", "-y", "-ss", f"{max(0.0, at_sec):.3f}", "-i", str(media),
        "-frames:v", "1", "-q:v", "2", str(destination),
    ], check=False)
    if result.returncode != 0:
        return ""
    return str(destination) if destination.is_file() else ""


def tesseract_text(image: Path, run_command: RunCommand) -> str:
    if not shutil.which("tesseract"):
        return ""
    with tempfile.TemporaryDirectory(prefix="jaguartv-outro-ocr-") as temporary:
        for index, languages in enumerate(("chi_sim+chi_tra+eng+por", "eng+por", "eng")):
            output_base = Path(temporary) / f"ocr_{index}"
            result = run_command([
                "tesseract", str(image), str(output_base), "-l", languages, "--psm", "6",
            ], check=False)
            text_path = output_base.with_suffix(".txt")
            if result.returncode == 0 and text_path.is_file():
                return text_path.read_text(encoding="utf-8", errors="ignore")
        return ""


def promo_term_hits(text: str, terms: dict[str, list[str]]) -> list[str]:
    normalized = re.sub(r"\s+", " ", text or "").lower()
    hits: list[str] = []
    for values in terms.values():
        for term in values:
            lowered = term.lower()
            if lowered and lowered in normalized:
                hits.append(term)
    return list(dict.fromkeys(hits))


def visual_tail_signals(frames: list[tuple[float, Path]], tail_start: float) -> dict[str, Any]:
    if len(frames) < 2:
        return {"tail_static": False, "style_shift": False, "last_cut_sec": tail_start, "diffs": []}
    diffs = [
        {"from": frames[index - 1][0], "to": frames[index][0], "score": frame_diff_score(frames[index - 1][1], frames[index][1])}
        for index in range(1, len(frames))
    ]
    tail_diffs = [item["score"] for item in diffs[-2:]] or [0.0]
    early_diffs = [item["score"] for item in diffs[:-2]] or [0.0]
    tail_static = max(tail_diffs) <= 0.045
    style_shift = max(early_diffs) >= 0.16 and max(tail_diffs) <= 0.07
    cut_candidates = [item["to"] for item in diffs if item["score"] >= 0.16]
    return {
        "tail_static": tail_static,
        "style_shift": style_shift,
        "last_cut_sec": max(cut_candidates) if cut_candidates else tail_start,
        "diffs": [{"from": item["from"], "to": item["to"], "score": round(item["score"], 4)} for item in diffs],
    }


def audio_tail_guard(
    media: Path,
    *,
    duration: float,
    trim_sec: float,
    run_command: RunCommand,
) -> dict[str, Any]:
    if trim_sec <= 0:
        return {"enabled": False, "tail_volume_db": None, "previous_volume_db": None, "allow_trim": True}
    tail_start = max(0.0, duration - trim_sec)
    previous_start = max(0.0, tail_start - trim_sec)
    values: dict[str, float | None] = {"tail_volume_db": None, "previous_volume_db": None}
    for label, start, length in (
        ("tail_volume_db", tail_start, trim_sec),
        ("previous_volume_db", previous_start, max(0.5, tail_start - previous_start)),
    ):
        result = run_command([
            "ffmpeg", "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
            "-i", str(media), "-vn", "-af", "volumedetect", "-f", "null", "-",
        ], check=False)
        text = (result.stderr or "") + (result.stdout or "")
        match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", text)
        if match:
            values[label] = float(match.group(1))
    tail = values["tail_volume_db"]
    previous = values["previous_volume_db"]
    silent_tail = tail is not None and tail <= -42.0
    abrupt_drop = tail is not None and previous is not None and tail <= previous - 10.0
    loud_continuity = tail is not None and tail > -26.0 and not abrupt_drop
    return {
        "enabled": True,
        **values,
        "silent_tail": silent_tail,
        "abrupt_drop": abrupt_drop,
        "loud_continuity": loud_continuity,
        "allow_trim": silent_tail or abrupt_drop or not loud_continuity,
    }


def infer_trim_seconds(duration: float, settings: SourceOutroSettings, signals: dict[str, Any]) -> float:
    last_cut = float(signals.get("last_cut_sec") or max(0.0, duration - settings.min_trim_sec))
    trim = duration - last_cut
    if not math.isfinite(trim):
        return 0.0
    return max(0.0, min(settings.max_trim_sec, trim))


def classify_outro_detection(
    *,
    duration: float,
    settings: SourceOutroSettings,
    visual: dict[str, Any],
    promo_hits: list[str],
    qr_detected: bool = False,
    audio_guard: dict[str, Any] | None = None,
    force_trim_end_sec: float | None = None,
    disabled: bool = False,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "visual": visual,
        "promo_terms": promo_hits,
        "qr_detected": qr_detected,
        "audio": audio_guard or {"enabled": False},
    }
    if disabled:
        return {
            "should_trim": False, "trim_end_sec": 0.0, "confidence": 0.0,
            "reason": "disabled_by_operator",
            "evidence": evidence,
        }
    if force_trim_end_sec is not None and force_trim_end_sec > 0:
        trim = min(max(0.0, force_trim_end_sec), max(0.0, duration - 0.1))
        return {
            "should_trim": trim >= settings.min_trim_sec,
            "trim_end_sec": round(trim, 3),
            "confidence": 1.0,
            "reason": "force_trim_end_sec",
            "evidence": evidence,
        }
    if not settings.enabled:
        return {
            "should_trim": False, "trim_end_sec": 0.0, "confidence": 0.0,
            "reason": "disabled_by_config",
            "evidence": evidence,
        }
    if duration < 12.0:
        return {
            "should_trim": False, "trim_end_sec": 0.0, "confidence": 0.0,
            "reason": "source_too_short",
            "evidence": evidence,
        }

    trim_sec = infer_trim_seconds(duration, settings, visual)
    if trim_sec < settings.min_trim_sec:
        return {
            "should_trim": False, "trim_end_sec": round(trim_sec, 3), "confidence": 0.0,
            "reason": "tail_candidate_too_short",
            "evidence": evidence,
        }

    score = 0.0
    reasons: list[str] = []
    if visual.get("tail_static"):
        score += 0.34
        reasons.append("low_motion_static_tail")
    if visual.get("style_shift"):
        score += 0.22
        reasons.append("tail_style_shift_after_scene_cut")
    if promo_hits:
        score += min(0.32, 0.18 + 0.04 * len(promo_hits))
        reasons.append("promo_terms:" + ",".join(promo_hits[:5]))
    if qr_detected:
        score += 0.24
        reasons.append("qr_code_detected")
    audio = audio_guard or {}
    if audio.get("silent_tail") or audio.get("abrupt_drop"):
        score += 0.10
        reasons.append("audio_tail_silent_or_abrupt_drop")
    if audio.get("loud_continuity") and not (promo_hits or qr_detected):
        score -= 0.20
        reasons.append("audio_guard_loud_continuity")

    confidence = max(0.0, min(1.0, score))
    strong_promo_evidence = bool(promo_hits or qr_detected)
    should_trim = (
        confidence >= settings.min_confidence
        and strong_promo_evidence
        and bool(visual.get("tail_static"))
        and not (settings.audio_guard_enabled and audio and audio.get("allow_trim") is False)
    )
    if not should_trim:
        if confidence >= settings.min_confidence and settings.preserve_if_unsure:
            reason = "suggested_but_preserved_if_unsure:" + ",".join(reasons)
        elif not strong_promo_evidence:
            reason = "no_strong_promo_or_qr_evidence"
        elif not visual.get("tail_static"):
            reason = "tail_not_static_enough"
        else:
            reason = "confidence_below_threshold:" + ",".join(reasons)
    else:
        reason = "auto_trim:" + ",".join(reasons)
    return {
        "should_trim": should_trim,
        "trim_end_sec": round(trim_sec, 3),
        "confidence": round(confidence, 4),
        "reason": reason,
        "evidence": evidence,
    }


def detect_source_outro(
    config: dict[str, Any],
    media: Path,
    work_dir: Path,
    *,
    duration: float,
    options: dict[str, Any] | None = None,
    run_command: RunCommand,
) -> dict[str, Any]:
    settings = source_outro_settings(config)
    override = normalize_source_outro_options(options)
    root = Path(config.get("_root") or Path.cwd())
    output_json = work_dir / "source_outro_detection.json"
    clean_path = work_dir / "source_outro_trimmed.mp4"
    if (
        override["disable_outro_trim"]
        or (not settings.enabled and override["force_trim_end_sec"] is None)
        or (duration < 12.0 and override["force_trim_end_sec"] is None)
    ):
        reason = (
            "disabled_by_operator"
            if override["disable_outro_trim"]
            else "disabled_by_config"
            if not settings.enabled
            else "source_too_short"
        )
        result = {
            "enabled": settings.enabled,
            "should_trim": False,
            "trim_end_sec": 0.0,
            "confidence": 0.0,
            "reason": reason,
            "evidence": {"visual": {}, "promo_terms": [], "qr_detected": False, "audio": {"enabled": False}},
            "applied": False,
            "source_path": str(media),
            "clean_source_path": "",
            "before_frame": "",
            "after_frame": "",
            "scan_tail_sec": settings.scan_tail_sec,
            "min_confidence": settings.min_confidence,
            "preserve_if_unsure": settings.preserve_if_unsure,
        }
        return write_detection(output_json, result)
    tail_start = max(0.0, duration - min(settings.scan_tail_sec, max(0.0, duration - 0.1)))
    with tempfile.TemporaryDirectory(prefix="jaguartv-outro-frames-") as temporary:
        temp_dir = Path(temporary)
        frames = extract_tail_frames(media, temp_dir, duration=duration, tail_start=tail_start, run_command=run_command)
        visual = visual_tail_signals(frames, tail_start)
        text = ""
        if settings.ocr_enabled and frames:
            text = "\n".join(tesseract_text(frame, run_command) for _, frame in frames[-2:])
        promo_hits = promo_term_hits(text, settings.promo_terms or PROMO_TERM_DEFAULTS)
        qr_detected = bool(re.search(r"\bQR\b|二维码|扫码", text, flags=re.IGNORECASE))
        inferred_trim = infer_trim_seconds(duration, settings, visual)
        audio = (
            audio_tail_guard(media, duration=duration, trim_sec=inferred_trim, run_command=run_command)
            if settings.audio_guard_enabled else {"enabled": False, "allow_trim": True}
        )
        result = classify_outro_detection(
            duration=duration,
            settings=settings,
            visual=visual,
            promo_hits=promo_hits,
            qr_detected=qr_detected,
            audio_guard=audio,
            force_trim_end_sec=override["force_trim_end_sec"],
            disabled=override["disable_outro_trim"],
        )
        before_frame = ""
        after_frame = ""
        if settings.save_evidence_frames:
            trim_end_sec = float(result.get("trim_end_sec") or 0.0)
            before = work_dir / "outro_before_frame.jpg"
            after = work_dir / "outro_after_frame.jpg"
            before_frame = extract_evidence_frame(media, before, max(0.0, duration - max(0.2, trim_end_sec / 2)), run_command)
            after_frame = extract_evidence_frame(media, after, max(0.0, duration - trim_end_sec - 0.2), run_command)
        result.update({
            "enabled": settings.enabled,
            "applied": False,
            "source_path": str(media),
            "clean_source_path": "",
            "before_frame": relative_to_root(Path(before_frame), root) if before_frame else "",
            "after_frame": relative_to_root(Path(after_frame), root) if after_frame else "",
            "scan_tail_sec": settings.scan_tail_sec,
            "min_confidence": settings.min_confidence,
            "preserve_if_unsure": settings.preserve_if_unsure,
        })
    if result.get("should_trim"):
        trim_source_outro(media, clean_path, duration=duration, trim_end_sec=float(result["trim_end_sec"]), run_command=run_command)
        result["applied"] = True
        result["clean_source_path"] = str(clean_path)
    return write_detection(output_json, result)


def trim_source_outro(
    media: Path,
    output: Path,
    *,
    duration: float,
    trim_end_sec: float,
    run_command: RunCommand,
) -> Path:
    keep_duration = max(0.1, duration - max(0.0, trim_end_sec))
    target = output.with_name(f".{output.stem}.{os.getpid()}.rendering{output.suffix}")
    result = run_command([
        "ffmpeg", "-y", "-i", str(media), "-t", f"{keep_duration:.3f}",
        "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", str(target),
    ], check=False)
    if result.returncode != 0:
        result = run_command([
            "ffmpeg", "-y", "-i", str(media), "-t", f"{keep_duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target),
        ], check=False)
    if result.returncode != 0:
        raise RuntimeError("source outro trim failed:\n" + (result.stderr or result.stdout)[-4000:])
    target.replace(output)
    return output


def review_source_outro_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload or {}
    if not data:
        state = "未检测"
    elif data.get("applied"):
        state = "已自动裁剪"
    elif data.get("should_trim"):
        state = "建议裁剪"
    elif not data.get("enabled", True):
        state = "跳过"
    elif str(data.get("reason") or "").startswith("suggested_but_preserved"):
        state = "建议裁剪"
    else:
        state = "未检测" if data.get("reason") == "not_run" else "跳过"
    return {
        "state": state,
        "applied": bool(data.get("applied")),
        "trim_end_sec": float(data.get("trim_end_sec") or 0.0),
        "confidence": float(data.get("confidence") or 0.0),
        "reason": str(data.get("reason") or ""),
        "before_frame": str(data.get("before_frame") or ""),
        "after_frame": str(data.get("after_frame") or ""),
    }
