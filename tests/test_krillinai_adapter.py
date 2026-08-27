from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from jaguartv_factory.krillinai_adapter import (
    KRILLINAI_REVISION,
    KrillinAIError,
    krillinai_available,
    krillinai_subtitle,
    krillinai_tts,
)


def make_config(tmp_path: Path) -> dict:
    project = tmp_path / "workspace" / "external_tools" / "KrillinAI"
    (project / ".git").mkdir(parents=True)
    (project / ".git" / "HEAD").write_text(f"{KRILLINAI_REVISION}\n", encoding="utf-8")
    (project / "build").mkdir()
    (project / "config").mkdir()
    binary = project / "build" / "krillinai-cli"
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    (project / "config" / "config.toml").write_text("[app]\n", encoding="utf-8")
    return {
        "_root": str(tmp_path),
        "localization": {
            "krillinai": {
                "project_dir": "workspace/external_tools/KrillinAI",
                "binary": "build/krillinai-cli",
                "origin_language": "auto",
                "target_language": "pt",
                "caption_source": "whisper",
                "max_words_per_line": 7,
                "voice": "",
            }
        },
    }


def test_availability_requires_binary_and_runtime_config(tmp_path: Path):
    config = make_config(tmp_path)
    assert krillinai_available(config)[0] is True
    (tmp_path / "workspace/external_tools/KrillinAI/config/config.toml").unlink()
    assert krillinai_available(config) == (False, "config_not_found")
    assert krillinai_available(config, require_config=False)[0] is True


def test_subtitle_and_tts_use_structured_outputs_and_optional_voice(tmp_path: Path):
    config = make_config(tmp_path)
    media = tmp_path / "source.mp4"
    media.write_bytes(b"video")
    work = tmp_path / "job"
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        output_dir = Path(args[args.index("--workdir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        if args[1] == "subtitle":
            origin = output_dir / "origin_language_srt.srt"
            target = output_dir / "target_language_srt.srt"
            origin.write_text("1\n00:00:00,000 --> 00:00:01,000\nGoal\n", encoding="utf-8")
            target.write_text("1\n00:00:00,000 --> 00:00:01,000\nGol\n", encoding="utf-8")
            payload = {"ok": True, "outputs": {"origin_srt": str(origin), "target_srt": str(target)}}
        else:
            audio = output_dir / "tts_final_audio.wav"
            audio.write_bytes(b"audio")
            payload = {"ok": True, "outputs": {"tts_audio": str(audio)}}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    import jaguartv_factory.krillinai_adapter as adapter

    original = adapter.subprocess.run
    adapter.subprocess.run = fake_run
    try:
        subtitles = krillinai_subtitle(config, media, work, task_id="candidate")
        voice = krillinai_tts(
            config, subtitles["target_srt"], media, work, task_id="part01", voice="pt-BR-Custom"
        )
    finally:
        adapter.subprocess.run = original

    assert subtitles["origin_srt"].name == "origin_language_srt.srt"
    assert voice.name == "tts_final_audio.wav"
    assert "--target-lang" in calls[0] and "pt" in calls[0]
    assert "--voice" in calls[1] and "pt-BR-Custom" in calls[1]


def test_command_failure_never_generates_fallback_content(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    media = tmp_path / "source.mp4"
    media.write_bytes(b"video")

    def failed(args, **kwargs):
        payload = {
            "ok": False,
            "error": {"kind": "retryable", "code": "translation_failed", "message": "provider unavailable"},
        }
        return subprocess.CompletedProcess(args, 2, json.dumps(payload), "")

    monkeypatch.setattr("jaguartv_factory.krillinai_adapter.subprocess.run", failed)
    with pytest.raises(KrillinAIError, match="translation_failed"):
        krillinai_subtitle(config, media, tmp_path / "job", task_id="candidate")
    assert not list(tmp_path.rglob("script_ptbr.json"))


def test_run_uses_temporary_responses_bridge_runtime(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    config["localization"]["krillinai"]["responses_bridge"] = True
    media = tmp_path / "source.mp4"
    media.write_bytes(b"video")
    seen = {}

    class Runtime:
        cwd = tmp_path / "runtime"

    class Context:
        def __enter__(self):
            Runtime.cwd.mkdir()
            return Runtime()

        def __exit__(self, *_args):
            return False

    def fake_bridge(project, log_dir, **_kwargs):
        seen["project"] = project
        seen["log_dir"] = log_dir
        return Context()

    def fake_run(args, **kwargs):
        seen["cwd"] = kwargs["cwd"]
        output_dir = Path(args[args.index("--workdir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        origin = output_dir / "origin_language_srt.srt"
        target = output_dir / "target_language_srt.srt"
        origin.write_text("source", encoding="utf-8")
        target.write_text("target", encoding="utf-8")
        payload = {"ok": True, "outputs": {"origin_srt": str(origin), "target_srt": str(target)}}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    monkeypatch.setattr("jaguartv_factory.krillinai_adapter.bridge_runtime", fake_bridge)
    monkeypatch.setattr("jaguartv_factory.krillinai_adapter.subprocess.run", fake_run)
    krillinai_subtitle(config, media, tmp_path / "job", task_id="candidate")
    assert seen["cwd"] == Runtime.cwd
    assert seen["project"].name == "KrillinAI"
