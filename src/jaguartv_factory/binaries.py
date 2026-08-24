from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def common_binary_candidates(name: str) -> list[Path]:
    machine = "arm64" if os.uname().machine in {"arm64", "aarch64"} else "x64"
    system = {"darwin": "darwin", "linux": "linux", "win32": "win32"}.get(
        sys.platform, sys.platform
    )
    if name == "ffmpeg":
        return [
            ROOT / "node_modules" / "ffmpeg-static" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"),
            Path("/Applications/CapCut.app/Contents/Resources/ffmpeg"),
            Path("/Applications/VideoFusion-macOS.app/Contents/Resources/ffmpeg"),
            Path("/Applications/BlueStacks.app/Contents/MacOS/ffmpeg"),
        ]
    if name == "ffprobe":
        suffix = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
        return [ROOT / "node_modules" / "ffprobe-static" / "bin" / system / machine / suffix]
    return []


def require_binary(name: str) -> str:
    if name == "yt-dlp":
        try:
            from .sources import yt_dlp_binary

            return yt_dlp_binary()
        except Exception:
            pass
    sibling = Path(sys.executable).parent / name
    if sibling.is_file():
        return str(sibling)
    path = shutil.which(name)
    if path:
        return path
    for candidate in common_binary_candidates(name):
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(f"Missing required binary: {name}")
