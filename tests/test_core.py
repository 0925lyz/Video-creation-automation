from pathlib import Path
import wave

from PIL import Image

from jaguartv_factory.core import (
    brand_kit,
    choose_audio_strategy,
    format_srt_time,
    generate_funk_bgm,
    likely_language,
    load_config,
    render_endcard,
    write_srt,
)


def test_language_detection():
    assert likely_language("这是一个足球视频")[0] == "zh"
    assert likely_language("Você não vai acreditar que o Brasil marcou")[0] == "pt"


def test_srt_generation(tmp_path: Path):
    destination = tmp_path / "captions.srt"
    write_srt("Primeira frase. Segunda frase!", 4.0, destination)
    content = destination.read_text(encoding="utf-8")
    assert "00:00:00,000" in content
    assert "Primeira frase." in content
    assert format_srt_time(61.2) == "00:01:01,200"


def test_demo_config_loads():
    config = load_config(Path("config/pipeline.yaml"))
    assert config["localization"]["target"] == "pt-BR"
    assert config["audio"]["bgm_dir"] == "assets/bgm"
    assert config["audio"]["bgm_volume"] >= 0.40
    assert config["audio"]["source_mode"] == "auto"
    assert config["brand"]["kits"]["jaguartv"]["endcard"]["site"] == "Jarg.top"
    assert config["edit"]["render_engine"] == "remotion"
    assert config["edit"]["layout_mode"] == "original"
    assert config["edit"]["source_subtitle_cleanup"] == "off"


def test_generate_funk_bgm(tmp_path: Path):
    destination = generate_funk_bgm(tmp_path / "funk.wav", duration=0.25)
    with wave.open(str(destination), "rb") as track:
        assert track.getnchannels() == 1
        assert track.getframerate() == 44_100
        assert track.getnframes() > 10_000


def test_auto_audio_strategy_preserves_music_without_speech(tmp_path: Path, monkeypatch):
    config = {"audio": {"source_mode": "auto"}, "localization": {"asr_enabled": False}}
    media = tmp_path / "source.mp4"
    media.touch()
    monkeypatch.setattr("jaguartv_factory.core.media_has_audio", lambda _path: True)
    mode, transcript, reason = choose_audio_strategy(config, tmp_path, media)
    assert mode == "preserve_source"
    assert transcript == ""
    assert "no_speech_evidence" in reason


def test_auto_audio_strategy_localizes_when_subtitles_exist(tmp_path: Path):
    config = {"audio": {"source_mode": "auto"}, "localization": {"asr_enabled": False}}
    media = tmp_path / "source.mp4"
    media.touch()
    (tmp_path / "source.en.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:04.000\nThis spoken sentence is long enough to prove narration exists.",
        encoding="utf-8",
    )
    mode, transcript, reason = choose_audio_strategy(config, tmp_path, media)
    assert mode == "localized"
    assert "spoken sentence" in transcript
    assert reason == "subtitle:source.en.vtt"


def test_generated_endcard_is_fully_opaque(tmp_path: Path):
    config = load_config(Path("config/pipeline.yaml"))
    output = render_endcard(config, brand_kit(config), tmp_path)
    alpha = Image.open(output).convert("RGBA").getchannel("A")
    assert alpha.getextrema() == (255, 255)
