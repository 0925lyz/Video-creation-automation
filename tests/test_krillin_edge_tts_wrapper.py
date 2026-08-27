from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "krillin-edge-tts-wrapper.sh"


def _executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_edge_tts_wrapper_converts_official_output_to_wav(tmp_path: Path) -> None:
    text = tmp_path / "input.txt"
    text.write_text("Teste seguro.", encoding="utf-8")
    edge_log = tmp_path / "edge.log"
    ffmpeg_log = tmp_path / "ffmpeg.log"
    fake_edge = _executable(
        tmp_path / "edge-tts",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$@\" > {edge_log!s}\n"
        "while (($#)); do\n"
        "  if [[ \"$1\" == '--write-media' ]]; then printf 'mp3' > \"$2\"; exit 0; fi\n"
        "  shift\n"
        "done\n"
        "exit 2\n",
    )
    fake_ffmpeg = _executable(
        tmp_path / "ffmpeg",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$@\" > {ffmpeg_log!s}\n"
        "printf 'wav' > \"${!#}\"\n",
    )
    output = tmp_path / "result.wav"
    env = {
        **os.environ,
        "KRILLIN_EDGE_TTS_BIN": str(fake_edge),
        "KRILLIN_FFMPEG_BIN": str(fake_ffmpeg),
    }

    result = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            "--text-file",
            str(text),
            "--voice",
            "pt-BR-FranciscaNeural",
            "--output",
            str(output),
            "--format",
            "wav",
            "--sample_rate",
            "24000",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == b"wav"
    assert "pt-BR-FranciscaNeural" in edge_log.read_text(encoding="utf-8")
    assert "24000" in ffmpeg_log.read_text(encoding="utf-8")


def test_edge_tts_wrapper_rejects_unsafe_sample_rate(tmp_path: Path) -> None:
    text = tmp_path / "input.txt"
    text.write_text("Teste.", encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            "--text-file",
            str(text),
            "--output",
            str(tmp_path / "result.wav"),
            "--sample_rate",
            "not-a-number",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Invalid sample rate" in result.stderr


def test_edge_tts_wrapper_resolves_app_root_when_invoked_through_runtime_symlink(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    installed = app / "workspace/external_tools/KrillinAI/bin/edge-tts"
    installed.parent.mkdir(parents=True)
    shutil.copy2(WRAPPER, installed)
    installed.chmod(0o755)
    official = app / "workspace/tool_venvs/krillin-edge-tts/bin/edge-tts"
    official.parent.mkdir(parents=True)
    _executable(
        official,
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "while (($#)); do\n"
        "  if [[ \"$1\" == '--write-media' ]]; then printf 'mp3' > \"$2\"; exit 0; fi\n"
        "  shift\n"
        "done\n"
        "exit 2\n",
    )
    fake_ffmpeg = _executable(
        tmp_path / "ffmpeg",
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'wav' > \"${!#}\"\n",
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "bin").symlink_to(installed.parent, target_is_directory=True)
    text = tmp_path / "input.txt"
    text.write_text("Teste.", encoding="utf-8")
    output = tmp_path / "result.wav"

    result = subprocess.run(
        [
            str(runtime / "bin/edge-tts"),
            "--text-file",
            str(text),
            "--output",
            str(output),
            "--sample_rate",
            "24000",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "KRILLIN_FFMPEG_BIN": str(fake_ffmpeg)},
    )

    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == b"wav"
