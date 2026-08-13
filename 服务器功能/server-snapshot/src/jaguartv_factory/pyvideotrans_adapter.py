from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


def _root(config: dict[str, Any]) -> Path:
    return Path(str(config.get("_root") or Path.cwd())).expanduser().resolve()


def _resolve_path(config: dict[str, Any], value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return _root(config) / path


def pyvideotrans_settings(config: dict[str, Any]) -> dict[str, Any]:
    localization = config.get("localization", {}) or {}
    return localization.get("pyvideotrans", {}) or {}


def pyvideotrans_enabled(config: dict[str, Any], capability: str) -> bool:
    settings = pyvideotrans_settings(config)
    if not bool(settings.get("enabled", False)):
        return False
    return bool(settings.get(f"{capability}_enabled", False))


def pyvideotrans_project_dir(config: dict[str, Any]) -> Path:
    settings = pyvideotrans_settings(config)
    configured = str(settings.get("project_dir") or "workspace/external_tools/pyvideotrans")
    return _resolve_path(config, configured)


def pyvideotrans_uv_bin(config: dict[str, Any]) -> str:
    settings = pyvideotrans_settings(config)
    configured = str(settings.get("uv_bin") or os.environ.get("UV_BIN") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path if path.is_absolute() else _root(config) / path)
    return shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")


def pyvideotrans_timeout(config: dict[str, Any]) -> float:
    settings = pyvideotrans_settings(config)
    return float(settings.get("timeout_sec") or (config.get("run", {}) or {}).get("timeout_sec", 360))


def pyvideotrans_available(config: dict[str, Any]) -> tuple[bool, str]:
    project_dir = pyvideotrans_project_dir(config)
    uv_bin = Path(pyvideotrans_uv_bin(config)).expanduser()
    if not project_dir.is_dir():
        return False, f"project_dir_not_found:{project_dir}"
    if not (project_dir / "cli.py").is_file():
        return False, f"cli_not_found:{project_dir / 'cli.py'}"
    if not uv_bin.is_file() and not shutil.which(str(uv_bin)):
        return False, f"uv_not_found:{uv_bin}"
    return True, "available"


def _record_run(
    output_dir: Path,
    task: str,
    args: list[str],
    result: subprocess.CompletedProcess[str] | None,
    *,
    error: str = "",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "task": task,
        "args": args,
        "returncode": None if result is None else result.returncode,
        "error": error,
    }
    (output_dir / f"pyvideotrans_{task}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result is not None:
        (output_dir / f"pyvideotrans_{task}.stdout.log").write_text(result.stdout or "", encoding="utf-8")
        (output_dir / f"pyvideotrans_{task}.stderr.log").write_text(result.stderr or "", encoding="utf-8")


def run_pyvideotrans(
    config: dict[str, Any],
    task: str,
    name: Path,
    output_dir: Path,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    ok, reason = pyvideotrans_available(config)
    if not ok:
        raise RuntimeError(reason)
    project_dir = pyvideotrans_project_dir(config)
    args = [
        pyvideotrans_uv_bin(config),
        "run",
        "cli.py",
        "--task",
        task,
        "--name",
        str(name.expanduser().resolve()),
        "--output-dir",
        str(output_dir.expanduser().resolve()),
        *(extra_args or []),
    ]
    env = os.environ.copy()
    env.setdefault("PYVIDEOTRANS_LANG", "en")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            args,
            cwd=project_dir,
            env=env,
            text=True,
            capture_output=True,
            timeout=pyvideotrans_timeout(config),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        _record_run(output_dir, task, args, None, error=f"timeout:{error.timeout}")
        raise RuntimeError(f"pyvideotrans {task} timed out after {error.timeout}s") from error
    _record_run(output_dir, task, args, result)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"pyvideotrans {task} failed: {detail[-2000:]}")
    return result


def _newest_output(output_dir: Path, suffixes: set[str], *, after: float) -> Path | None:
    candidates = [
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in suffixes and path.stat().st_mtime >= after
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime, default=None)


def pyvideotrans_stt(config: dict[str, Any], media: Path, destination: Path) -> Path:
    settings = pyvideotrans_settings(config)
    output_dir = destination.parent / "pyvideotrans_stt"
    started = time.time() - 1
    extra = [
        "--recogn_type",
        str(int(settings.get("recogn_type", 0))),
        "--model_name",
        str(settings.get("model_name") or (config.get("localization", {}) or {}).get("whisper_model", "tiny")),
        "--detect_language",
        str(settings.get("detect_language") or "auto"),
    ]
    if bool(settings.get("cuda", False)):
        extra.append("--cuda")
    if bool(settings.get("remove_noise", False)):
        extra.append("--remove_noise")
    if bool(settings.get("enable_diariz", False)):
        extra.append("--enable_diariz")
    run_pyvideotrans(config, "stt", media, output_dir, extra)
    output = _newest_output(output_dir, {".srt"}, after=started)
    if not output:
        raise RuntimeError("pyvideotrans stt finished without an SRT output")
    shutil.copy2(output, destination)
    return destination


def pyvideotrans_translate_srt(config: dict[str, Any], source: Path, destination: Path) -> Path:
    settings = pyvideotrans_settings(config)
    output_dir = destination.parent / "pyvideotrans_sts"
    started = time.time() - 1
    target = str(settings.get("target_language_code") or "pt")
    source_lang = str(settings.get("source_language_code") or "auto")
    extra = [
        "--translate_type",
        str(int(settings.get("translate_type", 0))),
        "--source_language_code",
        source_lang,
        "--target_language_code",
        target,
    ]
    run_pyvideotrans(config, "sts", source, output_dir, extra)
    output = _newest_output(output_dir, {".srt"}, after=started)
    if not output:
        raise RuntimeError("pyvideotrans sts finished without an SRT output")
    shutil.copy2(output, destination)
    return destination


def pyvideotrans_tts(config: dict[str, Any], subtitles: Path, destination: Path) -> Path:
    settings = pyvideotrans_settings(config)
    output_dir = destination.parent / "pyvideotrans_tts"
    started = time.time() - 1
    extra = [
        "--tts_type",
        str(int(settings.get("tts_type", 0))),
        "--voice_role",
        str(settings.get("voice_role") or (config.get("localization", {}) or {}).get("edge_tts_voice", "pt-BR-AntonioNeural")),
        "--voice_rate",
        str(settings.get("voice_rate") or "+0%"),
        "--target_language_code",
        str(settings.get("target_language_code") or "pt"),
    ]
    if bool(settings.get("voice_autorate", True)):
        extra.append("--voice_autorate")
    run_pyvideotrans(config, "tts", subtitles, output_dir, extra)
    output = _newest_output(output_dir, {".wav", ".m4a", ".mp3", ".aac"}, after=started)
    if not output:
        raise RuntimeError("pyvideotrans tts finished without an audio output")
    if output.suffix.lower() == destination.suffix.lower():
        shutil.copy2(output, destination)
    else:
        converter = shutil.which("ffmpeg")
        if not converter:
            raise RuntimeError("ffmpeg is required to convert pyvideotrans TTS output")
        result = subprocess.run(
            [converter, "-y", "-i", str(output), "-ar", "48000", "-ac", "2", str(destination)],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("pyvideotrans tts conversion failed:\n" + result.stderr[-2000:])
    return destination


def pyvideotrans_vtv(config: dict[str, Any], media: Path, output_dir: Path) -> Path:
    settings = pyvideotrans_settings(config)
    started = time.time() - 1
    extra = [
        "--source_language_code",
        str(settings.get("source_language_code") or "zh-cn"),
        "--target_language_code",
        str(settings.get("target_language_code") or "pt"),
        "--translate_type",
        str(int(settings.get("translate_type", 0))),
        "--subtitle_type",
        str(int(settings.get("subtitle_type", 1))),
    ]
    voice_role = str(settings.get("voice_role") or "").strip()
    if voice_role:
        extra.extend(["--voice_role", voice_role])
    run_pyvideotrans(config, "vtv", media, output_dir, extra)
    output = _newest_output(output_dir, {".mp4", ".mov", ".mkv", ".webm"}, after=started)
    if not output:
        raise RuntimeError("pyvideotrans vtv finished without a video output")
    return output
