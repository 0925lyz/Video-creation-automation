from pathlib import Path
import wave

import pytest
from PIL import Image

from jaguartv_factory.core import (
    brand_kit,
    candidate_market_rejection,
    choose_audio_strategy,
    enforce_dual_variant_remotion,
    format_srt_time,
    generate_funk_bgm,
    likely_language,
    load_config,
    mobile_review_format_needed,
    require_binary,
    remotion_canvas_for_source,
    render_endcard,
    should_ocr_blur_source_subtitles,
    source_filename_label,
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
    assert config["edit"]["source_subtitle_cleanup"] == "ocr_blur"
    assert config["edit"]["ocr_auto_lower_third_fallback"] is True
    assert config["edit"]["short_video_threshold_sec"] == 75
    assert config["selection"]["max_source_duration_sec"] == 1800
    assert config["brand"]["kits"]["jaguartv"]["endcard"]["mode"] == "orientation_image"
    assert config["brand"]["kits"]["jaguartv"]["endcard"]["duration_sec"] == 1.5
    assert config["mobile_review_format"]["target_resolution"] == [1080, 1440]


def test_standard_production_requires_remotion_dual_variant_assets():
    config = load_config(Path("config/pipeline.yaml"))
    enforce_dual_variant_remotion(config)
    broken = {
        **config,
        "edit": {**config["edit"], "render_engine": "ffmpeg"},
    }
    with pytest.raises(RuntimeError, match="render_engine=remotion"):
        enforce_dual_variant_remotion(broken)
    broken = {
        **config,
        "remotion": {**config["remotion"], "promo_duration_sec": 2},
    }
    with pytest.raises(RuntimeError, match="promo_duration_sec=1.5"):
        enforce_dual_variant_remotion(broken)


def test_tiktok_source_filename_label_is_clean():
    assert source_filename_label("tiktok") == "TikTko"


def test_require_binary_prefers_virtualenv_sibling(tmp_path: Path, monkeypatch):
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    yt_dlp = venv_bin / "yt-dlp"
    yt_dlp.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("sys.executable", str(venv_bin / "python"))
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    assert require_binary("yt-dlp") == str(yt_dlp)


def test_mobile_review_format_only_wraps_landscape_outputs():
    config = {
        "mobile_review_format": {
            "enabled": True,
            "landscape_min_aspect": 1.2,
        }
    }
    assert mobile_review_format_needed(1920, 1080, config) is True
    assert mobile_review_format_needed(1280, 720, config) is True
    assert mobile_review_format_needed(1080, 1440, config) is False
    assert mobile_review_format_needed(720, 1280, config) is False
    assert mobile_review_format_needed(1920, 1080, {"mobile_review_format": {"enabled": False}}) is False


def test_remotion_canvas_uses_3x4_black_letterbox_for_landscape():
    config = {
        "mobile_review_format": {
            "enabled": True,
            "landscape_min_aspect": 1.2,
            "target_resolution": [1080, 1440],
        }
    }
    landscape = remotion_canvas_for_source(config, 1920, 1080)
    assert landscape["width"] == 1080
    assert landscape["height"] == 1440
    assert landscape["source_fit"] == "contain"
    assert landscape["overlay_placement"] == "mobile_top_band"
    assert landscape["mobile_format"]["mode"] == "landscape_to_3x4_black_letterbox_remotion"
    vertical = remotion_canvas_for_source(config, 720, 1280)
    assert vertical["width"] == 720
    assert vertical["height"] == 1280
    assert vertical["source_fit"] == "cover"


def test_ocr_blur_runs_for_chinese_platform_even_without_external_subtitles():
    assert should_ocr_blur_source_subtitles(
        "ocr_blur",
        platform="bilibili",
        detected_language="unknown",
        title_text="巴西足球中文字幕",
        localization_profile={"subtitle_mode": "ptbr_subtitles", "chinese_subtitles": True},
    ) is True
    assert should_ocr_blur_source_subtitles(
        "ocr_blur",
        platform="douyin",
        detected_language="unknown",
        title_text="巴西足球",
        localization_profile={"subtitle_mode": "ptbr_subtitles", "chinese_on_screen": True},
    ) is True
    assert should_ocr_blur_source_subtitles(
        "ocr_blur",
        platform="bilibili",
        detected_language="zh",
        title_text="巴西足球中文字幕",
        localization_profile={"subtitle_mode": "none", "class": 3},
    ) is True
    assert should_ocr_blur_source_subtitles(
        "ocr_blur",
        platform="youtube",
        detected_language="unknown",
        title_text="阿根廷巴西球迷場上大鬥毆",
        localization_profile={"subtitle_mode": "none", "title_has_chinese": True},
    ) is True
    assert should_ocr_blur_source_subtitles(
        "ocr_blur",
        platform="youtube",
        detected_language="en",
        title_text="Brazil football highlights",
        localization_profile={"subtitle_mode": "none"},
    ) is False
    assert should_ocr_blur_source_subtitles(
        "crop",
        platform="bilibili",
        detected_language="zh",
        title_text="巴西足球",
        localization_profile={"subtitle_mode": "none"},
    ) is False


def test_market_filter_rejects_betting_but_keeps_brazil_football():
    config = {"selection": {"market_filter": {"enabled": True}}}
    assert candidate_market_rejection(
        config, {"title": "巴甲比分预测 稳胆 串关"}, keyword="巴甲"
    ) == "prediction_or_betting_content"
    assert candidate_market_rejection(
        config, {"title": "Flamengo gols melhores momentos"}, keyword="#brasileirao"
    ) == ""


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
