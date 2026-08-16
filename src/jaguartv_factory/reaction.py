from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .binaries import require_binary

REACTION_MODES = ("none", "picture_in_picture", "split_vertical", "side_by_side")


@dataclass(frozen=True)
class ReactionSpec:
    mode: str = "none"
    source: str = ""
    source_volume: float = 0.72
    reaction_volume: float = 1.0
    position: str = "bottom_right"
    size_ratio: float = 0.32
    border_width: int = 6

    def validate(self) -> "ReactionSpec":
        if self.mode not in REACTION_MODES:
            raise ValueError(f"unsupported reaction mode: {self.mode}")
        if self.mode != "none" and not self.source:
            raise ValueError("reaction source is required when reaction mode is enabled")
        if not 0 <= self.source_volume <= 2:
            raise ValueError("source_volume must be between 0 and 2")
        if not 0 <= self.reaction_volume <= 2:
            raise ValueError("reaction_volume must be between 0 and 2")
        if not 0.15 <= self.size_ratio <= 0.6:
            raise ValueError("size_ratio must be between 0.15 and 0.6")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reaction_spec(options: dict[str, Any] | None) -> ReactionSpec:
    options = options or {}
    return ReactionSpec(
        mode=str(options.get("reaction_mode") or "none").strip().lower(),
        source=str(options.get("reaction_source") or "").strip(),
        source_volume=float(options.get("source_volume", 0.72)),
        reaction_volume=float(options.get("reaction_volume", 1.0)),
        position=str(options.get("reaction_position") or "bottom_right").strip().lower(),
        size_ratio=float(options.get("reaction_size_ratio", 0.32)),
        border_width=max(0, min(30, int(options.get("reaction_border_width", 6)))),
    ).validate()


def _probe(path: Path) -> dict[str, Any]:
    ffprobe = require_binary("ffprobe")
    result = subprocess.run([
        ffprobe, "-v", "error", "-show_entries", "format=duration:stream=index,codec_type,width,height",
        "-of", "json", str(path),
    ], check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"unable to probe {path}")
    return json.loads(result.stdout)


def _overlay_position(position: str, margin: int = 32) -> tuple[str, str]:
    positions = {
        "top_left": (str(margin), str(margin)),
        "top_right": (f"W-w-{margin}", str(margin)),
        "bottom_left": (str(margin), f"H-h-{margin}"),
        "bottom_right": (f"W-w-{margin}", f"H-h-{margin}"),
    }
    return positions.get(position, positions["bottom_right"])


def _video_filter(spec: ReactionSpec, width: int, height: int, content_duration: float) -> str:
    enabled = f"between(t,0,{max(0.0, content_duration):.3f})"
    if spec.mode == "picture_in_picture":
        reaction_width = max(2, int(width * spec.size_ratio) // 2 * 2)
        x, y = _overlay_position(spec.position)
        border = spec.border_width
        return (
            f"[1:v]setpts=PTS-STARTPTS,scale={reaction_width}:-2:force_original_aspect_ratio=decrease,"
            f"pad=iw+{border * 2}:ih+{border * 2}:{border}:{border}:color=white[reaction];"
            f"[0:v][reaction]overlay={x}:{y}:enable='{enabled}':eof_action=pass[v]"
        )
    if spec.mode == "split_vertical":
        half_height = max(2, height // 2 // 2 * 2)
        return (
            f"[0:v]split=2[base][main];"
            f"[main]scale={width}:{half_height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{half_height}:(ow-iw)/2:(oh-ih)/2:color=black[mainhalf];"
            f"[1:v]setpts=PTS-STARTPTS,scale={width}:{half_height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{half_height}:(ow-iw)/2:(oh-ih)/2:color=black[reactionhalf];"
            f"[base][mainhalf]overlay=0:0:enable='{enabled}'[stacked];"
            f"[stacked][reactionhalf]overlay=0:{half_height}:enable='{enabled}':eof_action=pass[v]"
        )
    half_width = max(2, width // 2 // 2 * 2)
    return (
        f"[0:v]split=2[base][main];"
        f"[main]scale={half_width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={half_width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[mainhalf];"
        f"[1:v]setpts=PTS-STARTPTS,scale={half_width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={half_width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[reactionhalf];"
        f"[base][mainhalf]overlay=0:0:enable='{enabled}'[stacked];"
        f"[stacked][reactionhalf]overlay={half_width}:0:enable='{enabled}':eof_action=pass[v]"
    )


def compose_reaction(
    rendered_video: Path,
    output: Path,
    spec: ReactionSpec,
    *,
    content_duration: float,
) -> Path:
    spec.validate()
    if spec.mode == "none":
        if rendered_video != output:
            shutil.copy2(rendered_video, output)
        return output
    reaction_source = Path(spec.source).expanduser().resolve()
    if not reaction_source.is_file() or reaction_source.stat().st_size <= 0:
        raise ValueError(f"reaction source does not exist or is empty: {reaction_source}")
    ffmpeg = require_binary("ffmpeg")
    rendered_info = _probe(rendered_video)
    reaction_info = _probe(reaction_source)
    video_stream = next((stream for stream in rendered_info.get("streams", []) if stream.get("codec_type") == "video"), None)
    if not video_stream:
        raise RuntimeError("rendered video has no video stream")
    width = int(video_stream.get("width") or 1080)
    height = int(video_stream.get("height") or 1920)
    total_duration = float(rendered_info.get("format", {}).get("duration") or content_duration)
    reaction_has_audio = any(stream.get("codec_type") == "audio" for stream in reaction_info.get("streams", []))
    filters = [_video_filter(spec, width, height, content_duration)]
    map_args = ["-map", "[v]"]
    if reaction_has_audio:
        filters.append(
            f"[0:a]volume={spec.source_volume:.3f}[sourcea];"
            f"[1:a]atrim=duration={content_duration:.3f},asetpts=PTS-STARTPTS,volume={spec.reaction_volume:.3f}[reactiona];"
            "[sourcea][reactiona]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
        map_args.extend(["-map", "[a]"])
    else:
        map_args.extend(["-map", "0:a?"])
    output.parent.mkdir(parents=True, exist_ok=True)
    render_target = output.with_name(
        f".{output.stem}.{os.getpid()}.{threading.get_ident()}.reaction{output.suffix}"
    )
    args = [
        ffmpeg, "-y", "-i", str(rendered_video), "-stream_loop", "-1", "-i", str(reaction_source),
        "-filter_complex", ";".join(filters), *map_args,
        "-t", f"{total_duration:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(render_target),
    ]
    result = subprocess.run(args, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        render_target.unlink(missing_ok=True)
        raise RuntimeError("Reaction render failed:\n" + (result.stderr or result.stdout)[-6000:])
    render_target.replace(output)
    return output
