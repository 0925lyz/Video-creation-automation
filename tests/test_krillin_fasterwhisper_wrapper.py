from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "krillin-fasterwhisper-wrapper.sh"


def _run_wrapper(tmp_path: Path, *args: str) -> list[str]:
    log = tmp_path / "args.log"
    real = tmp_path / "whisper-real"
    real.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$@\" > {log!s}\n",
        encoding="utf-8",
    )
    real.chmod(0o755)
    result = subprocess.run(
        ["bash", str(WRAPPER), *args],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "KRILLIN_FASTERWHISPER_REAL_BIN": str(real)},
    )
    assert result.returncode == 0, result.stderr
    return log.read_text(encoding="utf-8").splitlines()


def test_fasterwhisper_wrapper_omits_auto_language(tmp_path: Path) -> None:
    args = _run_wrapper(tmp_path, "--model", "tiny", "--language", "auto", "audio.wav")
    assert args == ["--model", "tiny", "audio.wav"]


def test_fasterwhisper_wrapper_preserves_explicit_language(tmp_path: Path) -> None:
    args = _run_wrapper(tmp_path, "--language", "pt", "audio.wav")
    assert args == ["--language", "pt", "audio.wav"]
