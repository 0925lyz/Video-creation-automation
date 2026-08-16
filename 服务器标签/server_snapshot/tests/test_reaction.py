import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jaguartv_factory.reaction import ReactionSpec, compose_reaction


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe required"
)


def make_video(path: Path, color: str, duration: float, frequency: int) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=360x640:d={duration}:r=30",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True, capture_output=True)


@pytest.mark.parametrize("mode", ["picture_in_picture", "split_vertical", "side_by_side"])
def test_reaction_modes_render_playable_video(tmp_path: Path, mode: str):
    base = tmp_path / "base.mp4"
    reaction = tmp_path / "reaction.mp4"
    output = tmp_path / f"{mode}.mp4"
    make_video(base, "blue", 4, 440)
    make_video(reaction, "red", 2, 880)
    compose_reaction(
        base,
        output,
        ReactionSpec(mode=mode, source=str(reaction)),
        content_duration=3,
    )
    assert output.exists() and output.stat().st_size > 0
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
        "-of", "json", str(output),
    ], check=True, text=True, capture_output=True)
    payload = json.loads(probe.stdout)
    video = next(stream for stream in payload["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"]) == (360, 640)
    assert float(payload["format"]["duration"]) >= 3.9
