from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


HIGHLIGHT_KEYWORDS = (
    "goal", "gol", "score", "scores", "penalty", "pênalti", "shoot", "chute",
    "save", "goleiro", "red card", "cartão vermelho", "defense", "defence",
    "defend", "tackle", "interception", "pressing", "pass", "through ball",
    "assist", "dribble", "skill", "进球", "破门", "射门", "点球", "绝杀", "扑救",
    "红牌", "帽子戏法", "倒钩", "防守", "抢断", "拦截", "传球", "直塞", "助攻", "过人",
)

REPLAY_KEYWORDS = ("replay", "slow motion", "again", "回放", "慢镜头", "再看")


@dataclass(frozen=True)
class SignalPoint:
    time: float
    value: float


@dataclass(frozen=True)
class TranscriptCue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class HighlightSegment:
    start: float
    duration: float
    highlight_score: float
    highlight_reasons: tuple[str, ...]
    signal_scores: dict[str, float]
    strategy: str
    fallback: bool = False

    def to_dict(self, *, index: int = 1, total: int = 1) -> dict[str, Any]:
        payload = asdict(self)
        payload.update({
            "index": index,
            "total": total,
            "start": round(self.start, 3),
            "duration": round(self.duration, 3),
            "source_start": round(self.start, 3),
            "source_end": round(self.start + self.duration, 3),
            "highlight_score": round(self.highlight_score, 2),
            "highlight_reasons": list(self.highlight_reasons),
        })
        return payload


def _require_binary(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise RuntimeError(f"Missing required binary: {name}")
    return binary


def _run_binary(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, check=False, capture_output=True)


def normalize_points(points: Sequence[SignalPoint]) -> list[SignalPoint]:
    if not points:
        return []
    values = sorted(point.value for point in points)
    low = values[max(0, int(len(values) * 0.10) - 1)]
    high = values[min(len(values) - 1, int(len(values) * 0.95))]
    spread = high - low
    if spread <= 1e-9:
        return [SignalPoint(point.time, 0.0) for point in points]
    return [SignalPoint(point.time, max(0.0, min(1.0, (point.value - low) / spread))) for point in points]


def sample_audio_envelope(media: Path, *, sample_rate: int = 8000, window_sec: float = 1.0) -> list[SignalPoint]:
    ffmpeg = _require_binary("ffmpeg")
    result = _run_binary([
        ffmpeg, "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-f", "s16le", "-",
    ])
    if result.returncode != 0 or not result.stdout:
        return []
    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) - len(result.stdout) % 2])
    if sys.byteorder != "little":
        samples.byteswap()
    window = max(1, int(sample_rate * window_sec))
    points: list[SignalPoint] = []
    for offset in range(0, len(samples), window):
        chunk = samples[offset: offset + window]
        if not chunk:
            continue
        rms = math.sqrt(sum(float(value) * float(value) for value in chunk) / len(chunk))
        points.append(SignalPoint(offset / sample_rate, rms))
    return normalize_points(points)


def sample_motion_intensity(
    media: Path, *, fps: float = 2.0, width: int = 160, height: int = 90
) -> list[SignalPoint]:
    ffmpeg = _require_binary("ffmpeg")
    filter_graph = (
        f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=gray"
    )
    result = _run_binary([
        ffmpeg, "-v", "error", "-i", str(media), "-an", "-vf", filter_graph,
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ])
    if result.returncode != 0 or not result.stdout:
        return []
    frame_size = width * height
    frame_count = len(result.stdout) // frame_size
    points: list[SignalPoint] = []
    previous: bytes | None = None
    for index in range(frame_count):
        frame = result.stdout[index * frame_size: (index + 1) * frame_size]
        if previous is not None:
            difference = sum(abs(left - right) for left, right in zip(frame, previous)) / (frame_size * 255)
            points.append(SignalPoint(index / fps, difference))
        previous = frame
    return normalize_points(points)


def detect_scene_changes(media: Path, *, threshold: float = 0.28) -> list[float]:
    ffmpeg = _require_binary("ffmpeg")
    result = _run_binary([
        ffmpeg, "-hide_banner", "-i", str(media), "-an",
        "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-",
    ])
    text = result.stderr.decode("utf-8", "replace")
    return [float(value) for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", text)]


def parse_srt(path: Path | None) -> list[TranscriptCue]:
    if not path or not path.exists():
        return []
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="ignore").strip())
    cues: list[TranscriptCue] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start_text, end_text = (part.strip() for part in lines[timing_index].split("-->", 1))
        try:
            cues.append(TranscriptCue(_srt_seconds(start_text), _srt_seconds(end_text), " ".join(lines[timing_index + 1:])))
        except ValueError:
            continue
    return cues


def _srt_seconds(value: str) -> float:
    match = re.search(r"(\d{1,2}):(\d{2}):(\d{2})(?:[,.](\d{1,3}))?", value)
    if not match:
        raise ValueError(f"invalid subtitle timestamp: {value}")
    hours, minutes, seconds, millis = match.groups(default="0")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis[:3].ljust(3, "0")) / 1000


def _window_stat(points: Sequence[SignalPoint], start: float, end: float) -> float:
    values = [point.value for point in points if start <= point.time <= end]
    if not values:
        return 0.0
    values.sort(reverse=True)
    keep = max(1, math.ceil(len(values) * 0.20))
    return sum(values[:keep]) / keep


def _surge_stat(points: Sequence[SignalPoint], start: float, end: float) -> float:
    values = [point.value for point in points if start <= point.time <= end]
    if len(values) < 2:
        return 0.0
    surges = [max(0.0, right - left) for left, right in zip(values, values[1:])]
    return max(surges, default=0.0)


def _keyword_score(cues: Sequence[TranscriptCue], start: float, end: float, words: Iterable[str]) -> float:
    text = " ".join(cue.text.lower() for cue in cues if cue.end >= start and cue.start <= end)
    hits = sum(1 for word in words if word.lower() in text)
    return min(1.0, hits / 2.0)


def _candidate_centers(
    duration: float,
    audio: Sequence[SignalPoint],
    motion: Sequence[SignalPoint],
    scenes: Sequence[float],
    cues: Sequence[TranscriptCue],
) -> list[float]:
    centers = [point.time for point in sorted(audio, key=lambda item: item.value, reverse=True)[:12]]
    centers.extend(point.time for point in sorted(motion, key=lambda item: item.value, reverse=True)[:12])
    centers.extend(scenes[:40])
    for cue in cues:
        lowered = cue.text.lower()
        if any(word.lower() in lowered for word in (*HIGHLIGHT_KEYWORDS, *REPLAY_KEYWORDS)):
            centers.append((cue.start + cue.end) / 2)
    if not centers:
        centers = [duration * fraction for fraction in (0.2, 0.4, 0.6, 0.8)]
    return sorted(set(round(max(0.0, min(duration, center)), 1) for center in centers))


def _overlap_ratio(left: HighlightSegment, right: HighlightSegment) -> float:
    overlap = max(0.0, min(left.start + left.duration, right.start + right.duration) - max(left.start, right.start))
    return overlap / max(1.0, min(left.duration, right.duration))


def rank_highlight_windows(
    *,
    source_duration: float,
    audio_points: Sequence[SignalPoint] = (),
    motion_points: Sequence[SignalPoint] = (),
    scene_times: Sequence[float] = (),
    transcript_cues: Sequence[TranscriptCue] = (),
    max_segments: int = 3,
    max_duration: float = 30.0,
    strategy: str = "sports_highlight",
) -> list[dict[str, Any]]:
    max_segments = max(1, min(10, int(max_segments)))
    segment_duration = min(max(12.0, float(max_duration)), max(1.0, source_duration))
    if source_duration <= segment_duration + 1.0:
        segment = HighlightSegment(
            start=0.0,
            duration=source_duration,
            highlight_score=50.0,
            highlight_reasons=("short_source",),
            signal_scores={"audio_peak": 0.5, "motion_peak": 0.5, "scene_change": 0.0, "keyword": 0.0, "replay": 0.0},
            strategy=strategy,
        )
        return [segment.to_dict()]

    candidates: list[HighlightSegment] = []
    centers = _candidate_centers(source_duration, audio_points, motion_points, scene_times, transcript_cues)
    for center in centers:
        start = max(0.0, min(source_duration - segment_duration, center - segment_duration * 0.42))
        end = start + segment_duration
        audio_score = _window_stat(audio_points, start, end)
        surge_score = _surge_stat(audio_points, start, end)
        motion_score = _window_stat(motion_points, start, end)
        scene_count = sum(1 for value in scene_times if start <= value <= end)
        scene_score = min(1.0, scene_count / 4.0)
        keyword_score = _keyword_score(transcript_cues, start, end, HIGHLIGHT_KEYWORDS)
        replay_score = _keyword_score(transcript_cues, start, end, REPLAY_KEYWORDS)
        score = 100 * (
            audio_score * 0.30 + surge_score * 0.05 + motion_score * 0.25 + scene_score * 0.20
            + keyword_score * 0.15 + replay_score * 0.05
        )
        component_scores = {
            "audio_peak": round(audio_score, 4),
            "audio_surge": round(surge_score, 4),
            "motion_peak": round(motion_score, 4),
            "scene_change": round(scene_score, 4),
            "keyword": round(keyword_score, 4),
            "replay": round(replay_score, 4),
        }
        reasons = tuple(name for name, value in component_scores.items() if value >= 0.55)
        if not reasons:
            reasons = (max(component_scores, key=component_scores.get),)
        candidates.append(HighlightSegment(start, segment_duration, score, reasons, component_scores, strategy))

    selected: list[HighlightSegment] = []
    for candidate in sorted(candidates, key=lambda item: (item.highlight_score, -item.start), reverse=True):
        if all(_overlap_ratio(candidate, current) <= 0.25 for current in selected):
            selected.append(candidate)
        if len(selected) >= max_segments:
            break

    if not selected:
        selected = [HighlightSegment(0.0, segment_duration, 0.0, ("uniform_fallback",), {}, strategy, True)]
    total = len(selected)
    return [segment.to_dict(index=index, total=total) for index, segment in enumerate(selected, 1)]


def analyze_video(
    media: Path,
    *,
    source_duration: float,
    max_segments: int = 3,
    max_duration: float = 30.0,
    strategy: str = "sports_highlight",
    transcript_path: Path | None = None,
    max_signal_duration: float = 900.0,
) -> list[dict[str, Any]]:
    if source_duration > max_signal_duration:
        return []
    audio = sample_audio_envelope(media)
    motion = sample_motion_intensity(media)
    scenes = detect_scene_changes(media)
    cues = parse_srt(transcript_path)
    if not audio and not motion and not scenes and not cues:
        return []
    return rank_highlight_windows(
        source_duration=source_duration,
        audio_points=audio,
        motion_points=motion,
        scene_times=scenes,
        transcript_cues=cues,
        max_segments=max_segments,
        max_duration=max_duration,
        strategy=strategy,
    )


def uniform_segments(
    source_duration: float, *, max_segments: int = 3, max_duration: float = 30.0, strategy: str = "uniform"
) -> list[dict[str, Any]]:
    duration = min(max(12.0, max_duration), source_duration)
    count = 1 if source_duration <= duration + 1 else min(max_segments, max(1, int(source_duration // duration)))
    starts = [0.0] if count == 1 else [index * (source_duration - duration) / (count - 1) for index in range(count)]
    segments = [
        HighlightSegment(
            start=start,
            duration=duration,
            highlight_score=0.0,
            highlight_reasons=("uniform_fallback",),
            signal_scores={},
            strategy=strategy,
            fallback=strategy != "uniform",
        )
        for start in starts
    ]
    return [segment.to_dict(index=index, total=len(segments)) for index, segment in enumerate(segments, 1)]
