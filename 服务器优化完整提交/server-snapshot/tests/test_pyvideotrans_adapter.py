from pathlib import Path

from jaguartv_factory.pyvideotrans_adapter import (
    pyvideotrans_available,
    pyvideotrans_enabled,
    pyvideotrans_stt,
    pyvideotrans_translate_srt,
    pyvideotrans_tts,
)


def make_config(tmp_path: Path) -> dict:
    project = tmp_path / "pyvideotrans"
    project.mkdir()
    (project / "cli.py").write_text("print('ok')\n", encoding="utf-8")
    uv = tmp_path / "uv"
    uv.write_text("#!/bin/sh\n", encoding="utf-8")
    uv.chmod(0o755)
    return {
        "_root": str(tmp_path),
        "run": {"timeout_sec": 30},
        "localization": {
            "pyvideotrans": {
                "enabled": True,
                "project_dir": str(project),
                "uv_bin": str(uv),
                "stt_enabled": True,
                "sts_enabled": True,
                "tts_enabled": True,
                "model_name": "tiny",
                "voice_role": "pt-BR-AntonioNeural",
            }
        },
    }


def test_pyvideotrans_availability_and_capability_flags(tmp_path: Path):
    config = make_config(tmp_path)
    assert pyvideotrans_available(config) == (True, "available")
    assert pyvideotrans_enabled(config, "stt") is True
    assert pyvideotrans_enabled(config, "vtv") is False


def test_pyvideotrans_stt_copies_newest_srt(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    media = tmp_path / "source.mp4"
    media.write_bytes(b"video")
    destination = tmp_path / "job" / "source.pyvideotrans.srt"

    def fake_run(config, task, name, output_dir, extra_args):
        assert task == "stt"
        produced = output_dir / "source.srt"
        produced.parent.mkdir(parents=True)
        produced.write_text("1\n00:00:00,000 --> 00:00:01,000\nOla\n", encoding="utf-8")

    monkeypatch.setattr("jaguartv_factory.pyvideotrans_adapter.run_pyvideotrans", fake_run)
    assert pyvideotrans_stt(config, media, destination) == destination
    assert "Ola" in destination.read_text(encoding="utf-8")


def test_pyvideotrans_sts_and_tts_outputs(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nOi\n", encoding="utf-8")
    translated = tmp_path / "job" / "subtitles_ptbr.srt"
    voice = tmp_path / "job" / "voice.wav"

    def fake_run(config, task, name, output_dir, extra_args):
        output_dir.mkdir(parents=True, exist_ok=True)
        if task == "sts":
            (output_dir / "source.pt.srt").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        elif task == "tts":
            (output_dir / "source.wav").write_bytes(b"voice")
        else:
            raise AssertionError(task)

    monkeypatch.setattr("jaguartv_factory.pyvideotrans_adapter.run_pyvideotrans", fake_run)
    assert pyvideotrans_translate_srt(config, source, translated) == translated
    assert pyvideotrans_tts(config, translated, voice) == voice
    assert voice.read_bytes() == b"voice"
