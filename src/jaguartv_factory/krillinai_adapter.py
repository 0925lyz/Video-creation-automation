from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any, Callable, Sequence

from .openai_responses_bridge import bridge_runtime


KRILLINAI_REVISION = "17c87b0ce59ee937b0658994718c07b9ca98c1d1"
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class KrillinAIError(RuntimeError):
    pass


def krillinai_settings(config: dict[str, Any]) -> dict[str, Any]:
    localization = config.get("localization", {}) or {}
    return localization.get("krillinai", {}) or {}


def _project_root(config: dict[str, Any]) -> Path:
    return Path(str(config.get("_root") or Path.cwd())).expanduser().resolve()


def krillinai_project_dir(config: dict[str, Any]) -> Path:
    configured = str(
        krillinai_settings(config).get("project_dir")
        or "workspace/external_tools/KrillinAI"
    )
    path = Path(configured).expanduser()
    return path.resolve() if path.is_absolute() else (_project_root(config) / path).resolve()


def krillinai_binary(config: dict[str, Any]) -> Path:
    configured = str(krillinai_settings(config).get("binary") or "build/krillinai-cli")
    path = Path(configured).expanduser()
    return path.resolve() if path.is_absolute() else (krillinai_project_dir(config) / path).resolve()


def _checkout_revision(project: Path) -> str:
    head = (project / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref_path = project / ".git" / head.removeprefix("ref: ").strip()
        if ref_path.is_file():
            return ref_path.read_text(encoding="utf-8").strip()
    elif len(head) == 40:
        return head
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project, check=False, text=True, capture_output=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def krillinai_available(config: dict[str, Any], *, require_config: bool = True) -> tuple[bool, str]:
    project = krillinai_project_dir(config)
    binary = krillinai_binary(config)
    if not (project / ".git").is_dir():
        return False, "project_dir_not_found"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return False, "binary_not_executable"
    expected_revision = str(krillinai_settings(config).get("revision") or KRILLINAI_REVISION).strip()
    if _checkout_revision(project) != expected_revision:
        return False, "revision_mismatch"
    if require_config and not (project / "config" / "config.toml").is_file():
        return False, "config_not_found"
    return True, str(binary)


def _last_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise KrillinAIError("KrillinAI did not return a JSON result")


def run_krillinai(
    config: dict[str, Any],
    args: Sequence[str],
    *,
    log_dir: Path,
    run_command: RunCommand | None = None,
    require_config: bool = True,
) -> dict[str, Any]:
    ready, reason = krillinai_available(config, require_config=require_config)
    if not ready:
        raise KrillinAIError(f"KrillinAI unavailable: {reason}")
    project = krillinai_project_dir(config)
    log_dir.mkdir(parents=True, exist_ok=True)
    timeout = max(30.0, float(krillinai_settings(config).get("timeout_sec") or 1800))
    runner = run_command or subprocess.run
    use_bridge = bool(krillinai_settings(config).get("responses_bridge"))
    try:
        runtime = bridge_runtime(project, log_dir, timeout=min(timeout, 120.0)) if use_bridge else None
        if runtime is None:
            result = runner(
                [str(krillinai_binary(config)), *[str(value) for value in args]],
                cwd=project,
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        else:
            with runtime as bridge:
                result = runner(
                    [str(krillinai_binary(config)), *[str(value) for value in args]],
                    cwd=bridge.cwd,
                    check=False,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                )
    except subprocess.TimeoutExpired as error:
        raise KrillinAIError(f"KrillinAI timed out after {error.timeout}s") from error
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        raise KrillinAIError(f"KrillinAI Responses bridge unavailable: {error}") from error
    (log_dir / "krillinai.stdout.log").write_text(result.stdout or "", encoding="utf-8")
    (log_dir / "krillinai.stderr.log").write_text(result.stderr or "", encoding="utf-8")
    payload = _last_json_line(result.stdout or "")
    (log_dir / "krillinai.result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result.returncode != 0 or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        detail = str(error.get("message") or result.stderr or "KrillinAI command failed").strip()
        kind = str(error.get("kind") or "internal")
        code = str(error.get("code") or "command_failed")
        raise KrillinAIError(f"KrillinAI {kind}/{code}: {detail[-2000:]}")
    return payload


def krillinai_subtitle(
    config: dict[str, Any], media: Path, work_dir: Path, *, task_id: str
) -> dict[str, Path]:
    settings = krillinai_settings(config)
    output_dir = work_dir / "krillinai" / "subtitle"
    args = [
        "subtitle",
        f"local:{media.resolve()}",
        "--origin-lang", str(settings.get("origin_language") or "auto"),
        "--target-lang", str(settings.get("target_language") or "pt"),
        "--caption-source", str(settings.get("caption_source") or "any"),
        "--max-word-one-line", str(max(1, int(settings.get("max_words_per_line") or 7))),
        "--workdir", str(output_dir.resolve()),
        "--task-id", task_id,
    ]
    style = str(settings.get("subtitle_style_file") or "").strip()
    if style:
        style_path = Path(style).expanduser()
        if not style_path.is_absolute():
            style_path = (_project_root(config) / style_path).resolve()
        args.extend(["--subtitle-style-file", str(style_path)])
    payload = run_krillinai(config, args, log_dir=output_dir)
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    origin = Path(str(outputs.get("origin_srt") or output_dir / "origin_language_srt.srt"))
    target = Path(str(outputs.get("target_srt") or output_dir / "target_language_srt.srt"))
    if not origin.is_file() or origin.stat().st_size <= 0:
        raise KrillinAIError("KrillinAI transcription produced no source SRT")
    if not target.is_file() or target.stat().st_size <= 0:
        raise KrillinAIError("KrillinAI translation produced no target SRT")
    return {"origin_srt": origin.resolve(), "target_srt": target.resolve()}


def krillinai_tts(
    config: dict[str, Any], subtitles: Path, video: Path, work_dir: Path, *, task_id: str, voice: str = ""
) -> Path:
    output_dir = work_dir / "krillinai" / "tts" / task_id
    args = [
        "tts",
        "--workdir", str(output_dir.resolve()),
        "--task-id", task_id,
        "--input-srt", str(subtitles.resolve()),
        "--line-mode", "target-only",
        "--video", str(video.resolve()),
    ]
    selected_voice = voice.strip() or str(krillinai_settings(config).get("voice") or "").strip()
    if selected_voice:
        args.extend(["--voice", selected_voice])
    payload = run_krillinai(config, args, log_dir=output_dir)
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    audio = Path(str(outputs.get("tts_audio") or output_dir / "tts_final_audio.wav"))
    if not audio.is_file() or audio.stat().st_size <= 0:
        raise KrillinAIError("KrillinAI TTS produced no audio")
    return audio.resolve()
