from pathlib import Path
import wave

import pytest
from PIL import Image

from jaguartv_factory.core import (
    brand_kit,
    candidate_market_rejection,
    choose_audio_strategy,
    connect_db,
    design_image_path,
    enforce_dual_variant_remotion,
    format_srt_time,
    generate_funk_bgm,
    likely_language,
    load_config,
    mobile_review_format_needed,
    production_design_config,
    require_binary,
    ensure_remotion_runtime,
    remotion_canvas_for_source,
    remotion_caption_cues,
    remotion_caption_style,
    remotion_captions_enabled_for_variant,
    render_endcard,
    render_hyperframes_html,
    update_render_job,
    should_ocr_blur_source_subtitles,
    source_filename_label,
    safe_hyperframes_dir_name,
    upsert_render_job,
    write_srt,
)
from jaguartv_factory import cli
from jaguartv_factory.source_outro import SourceOutroSettings, classify_outro_detection


def test_language_detection():
    assert likely_language("这是一个足球视频")[0] == "zh"
    assert likely_language("Você não vai acreditar que o Brasil marcou")[0] == "pt"


def test_freeform_design_preserves_newlines_and_ignores_blank_text(tmp_path: Path):
    config = {"_root": str(tmp_path), "edit": {}, "remotion": {"dual_variant": {}}}
    patched = production_design_config(config, {"design": {"layers": [
        {"id": "text", "type": "text", "text": "Linha um\nLinha dois", "x": 0.2, "y": 0.3,
         "font_size_ratio": 0.06, "max_width": 0.7, "color": "#12ab34"},
        {"id": "blank", "type": "text", "text": "\n  ", "x": 0, "y": 0},
        {"id": "logo", "type": "image", "path": "/srv/logo.png", "x": 0.75, "y": 0.04, "width": 0.2},
    ]}})

    assert patched is not config
    assert patched["edit"]["render_engine"] == "remotion"
    layers = patched["remotion"]["custom_design"]["layers"]
    assert [layer["id"] for layer in layers] == ["text", "logo"]
    assert layers[0]["text"] == "Linha um\nLinha dois"
    assert layers[1]["path"] == "/srv/logo.png"
    assert patched["remotion"]["custom_design"]["variants"] == ["通用版", "FB版"]
    assert patched["edit"]["layout_mode"] == "original"
    assert patched["remotion"]["custom_design"]["preserve_source_canvas"] is True
    assert patched["remotion"]["custom_design"]["whole_source"] is True


def test_remotion_runtime_reinstalls_when_deep_dependency_is_missing(tmp_path: Path, monkeypatch):
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "package.json").write_text('{"dependencies":{"remotion":"4.0.508"}}', encoding="utf-8")
    workspace = tmp_path / "workspace"
    runtime = workspace / "remotion_runtime"
    remotion_bin = runtime / "node_modules" / ".bin" / "remotion"
    remotion_bin.parent.mkdir(parents=True)
    remotion_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    (runtime / "node_modules" / "webpack").mkdir(parents=True)

    calls: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int = 0):
            self.returncode = returncode
            self.stderr = ""
            self.stdout = ""

    def fake_run_command(args, **kwargs):
        calls.append([str(part) for part in args])
        if args[:2] == ["/usr/bin/npm", "install"]:
            remotion_bin.parent.mkdir(parents=True, exist_ok=True)
            remotion_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            (runtime / "node_modules" / "webpack" / "lib" / "dependencies").mkdir(parents=True, exist_ok=True)
            (runtime / "node_modules" / "webpack" / "lib" / "dependencies" / "CriticalDependencyWarning.js").write_text(
                "module.exports = function CriticalDependencyWarning() {};",
                encoding="utf-8",
            )
        if args[:2] == ["node", "-e"]:
            return Result(1 if len([call for call in calls if call[:2] == ["node", "-e"]]) == 1 else 0)
        return Result(0)

    monkeypatch.setattr("jaguartv_factory.core.remotion_template_dir", lambda: template)
    monkeypatch.setattr("jaguartv_factory.core.require_binary", lambda name: name)
    monkeypatch.setattr("jaguartv_factory.core.shutil.which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr("jaguartv_factory.core.run_command", fake_run_command)

    assert ensure_remotion_runtime({"_root": str(tmp_path), "run": {"workspace": "workspace"}}) == runtime
    assert any(call[:2] == ["/usr/bin/npm", "install"] for call in calls)


def test_freeform_design_accepts_single_variant(tmp_path: Path):
    config = {"_root": str(tmp_path), "edit": {}, "remotion": {"dual_variant": {}}}
    patched = production_design_config(config, {"design": {"variants": ["FB版"], "layers": [
        {"id": "text", "type": "text", "text": "FB only", "x": 0.2, "y": 0.3},
    ]}})

    assert patched["remotion"]["custom_design"]["variants"] == ["FB版"]


def test_design_image_path_is_limited_to_uploads_and_brand_assets(tmp_path: Path):
    config = {"_root": str(tmp_path), "storage": {"root": "workspace/server_media"}}
    uploaded = tmp_path / "workspace" / "server_media" / "uploads" / "design_image" / "logo.png"
    uploaded.parent.mkdir(parents=True)
    uploaded.write_bytes(b"image")
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"secret")

    assert design_image_path(config, str(uploaded)) == uploaded
    with pytest.raises(ValueError, match="uploaded image or brand asset"):
        design_image_path(config, str(secret))


def test_srt_generation(tmp_path: Path):
    destination = tmp_path / "captions.srt"
    write_srt("Primeira frase. Segunda frase!", 4.0, destination)
    content = destination.read_text(encoding="utf-8")
    assert "00:00:00,000" in content
    assert "Primeira frase." in content
    assert format_srt_time(61.2) == "00:01:01,200"


def test_remotion_caption_cues_are_safe_and_clamped(tmp_path: Path):
    destination = tmp_path / "captions.srt"
    destination.write_text(
        "1\n00:00:00,000 --> 00:00:02,500\nOlá Brasil!\n\n"
        "2\n00:00:03,000 --> 00:00:09,000\nFinal precisa cortar.\n",
        encoding="utf-8",
    )
    assert remotion_caption_cues(destination, max_end=4.0) == [
        {"startSeconds": 0.0, "endSeconds": 2.5, "text": "Olá Brasil!"},
        {"startSeconds": 3.0, "endSeconds": 4.0, "text": "Final precisa cortar."},
    ]


def test_remotion_caption_variant_config():
    config = {
        "remotion": {
            "captions": {
                "enabled": True,
                "variants": ["通用版"],
                "font_size_ratio": 0.5,
                "position": "middle",
            }
        }
    }
    assert remotion_captions_enabled_for_variant(config, "通用版") is True
    assert remotion_captions_enabled_for_variant(config, "FB版") is False
    style = remotion_caption_style(config)
    assert style["position"] == "bottom"
    assert style["fontSizeRatio"] == 0.075


def outro_settings(**overrides):
    values = {
        "enabled": True,
        "scan_tail_sec": 15,
        "min_trim_sec": 2,
        "max_trim_sec": 12,
        "min_confidence": 0.72,
        "preserve_if_unsure": True,
        "ocr_enabled": True,
        "audio_guard_enabled": True,
        "save_evidence_frames": True,
        "promo_terms": {"en": ["follow", "download"]},
    }
    values.update(overrides)
    return SourceOutroSettings(**values)


def test_source_outro_classifier_trims_static_promo_tail():
    result = classify_outro_detection(
        duration=40.0,
        settings=outro_settings(),
        visual={"tail_static": True, "style_shift": True, "last_cut_sec": 34.0},
        promo_hits=["follow", "download"],
        audio_guard={"enabled": True, "silent_tail": True, "abrupt_drop": False, "allow_trim": True},
    )

    assert result["should_trim"] is True
    assert result["trim_end_sec"] == 6.0
    assert result["confidence"] >= 0.72


def test_source_outro_classifier_preserves_normal_tail_without_promo():
    result = classify_outro_detection(
        duration=40.0,
        settings=outro_settings(),
        visual={"tail_static": False, "style_shift": False, "last_cut_sec": 36.0},
        promo_hits=[],
        audio_guard={"enabled": True, "loud_continuity": True, "allow_trim": False},
    )

    assert result["should_trim"] is False
    assert result["reason"] == "no_strong_promo_or_qr_evidence"


def test_source_outro_classifier_low_confidence_does_not_trim():
    result = classify_outro_detection(
        duration=40.0,
        settings=outro_settings(min_confidence=0.9),
        visual={"tail_static": True, "style_shift": False, "last_cut_sec": 36.0},
        promo_hits=["follow"],
        audio_guard={"enabled": True, "allow_trim": True},
    )

    assert result["should_trim"] is False
    assert result["confidence"] < 0.9


def test_source_outro_classifier_short_video_does_not_trim():
    result = classify_outro_detection(
        duration=9.0,
        settings=outro_settings(),
        visual={"tail_static": True, "style_shift": True, "last_cut_sec": 5.0},
        promo_hits=["follow", "download"],
        audio_guard={"enabled": True, "silent_tail": True, "allow_trim": True},
    )

    assert result["should_trim"] is False
    assert result["reason"] == "source_too_short"


def test_source_outro_classifier_platform_watermark_alone_does_not_trim():
    result = classify_outro_detection(
        duration=35.0,
        settings=outro_settings(),
        visual={"tail_static": True, "style_shift": False, "last_cut_sec": 30.0},
        promo_hits=[],
        audio_guard={"enabled": True, "allow_trim": True},
    )

    assert result["should_trim"] is False
    assert result["reason"] == "no_strong_promo_or_qr_evidence"


def test_source_outro_classifier_force_and_disable_overrides():
    forced = classify_outro_detection(
        duration=35.0,
        settings=outro_settings(),
        visual={"tail_static": False, "style_shift": False, "last_cut_sec": 34.0},
        promo_hits=[],
        force_trim_end_sec=4.0,
    )
    disabled = classify_outro_detection(
        duration=35.0,
        settings=outro_settings(),
        visual={"tail_static": True, "style_shift": True, "last_cut_sec": 28.0},
        promo_hits=["follow", "download"],
        disabled=True,
    )

    assert forced["should_trim"] is True
    assert forced["reason"] == "force_trim_end_sec"
    assert disabled["should_trim"] is False
    assert disabled["reason"] == "disabled_by_operator"


def test_pipeline_does_not_auto_add_remotion_promo_copy():
    config = load_config(Path("config/pipeline.yaml"))
    remotion = config["remotion"]

    assert remotion.get("top_badge", "") == ""
    assert remotion.get("bottom_headline", "") == ""
    assert remotion.get("bottom_subline", "") == ""
    assert remotion.get("endcard_cta", "") == ""


def test_hyperframes_package_rejects_nested_project_dir():
    assert safe_hyperframes_dir_name("hf_pack") == "hf_pack"
    with pytest.raises(ValueError):
        safe_hyperframes_dir_name("../outside")


def test_hyperframes_html_uses_local_media_and_captions():
    html = render_hyperframes_html(
        {"title": "JaguarTV", "durationSeconds": 3.0, "gsap": "vendor/gsap.min.js"},
        [{"startSeconds": 0.0, "endSeconds": 1.0, "text": "Legenda"}],
        "Resumo",
    )
    assert 'src="media/source.mp4"' in html
    assert 'data-composition-id="jaguartv-hf"' in html
    assert 'data-start="0"' in html
    assert 'data-width="1080"' in html
    assert 'data-track-index="3"' in html
    assert 'window.__timelines["jaguartv-hf"]' in html
    assert "Legenda" in html


def test_render_job_progress_is_persistent(tmp_path: Path):
    config = {"_root": str(tmp_path), "run": {"workspace": "workspace"}}
    upsert_render_job(
        config,
        "job-1",
        candidate_id="candidate-1",
        variant="通用版",
        engine="remotion_renderer_api",
        status="STARTED",
        output_path="/tmp/out.mp4",
    )
    update_render_job(config, "job-1", status="RENDERING", progress=0.5, metadata_patch={"fps": 30})
    row = connect_db(config).execute("SELECT * FROM render_jobs WHERE id='job-1'").fetchone()
    assert row["status"] == "RENDERING"
    assert row["progress"] == 0.5
    assert '"fps": 30' in row["metadata_json"]


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
    assert config["sources"]["enabled"] == ["douyin", "tiktok", "facebook"]
    assert config["sources"]["keywords_file"] == "config/keywords.brazil.yaml"


def test_cli_discover_accepts_keyword_overrides(monkeypatch, tmp_path: Path, capsys):
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text("run:\n  workspace: workspace\nsources:\n  enabled: []\n", encoding="utf-8")
    captured = {}

    def fake_load_config(path: Path):
        captured["config_path"] = path
        return {"_root": str(tmp_path), "run": {"workspace": "workspace"}, "sources": {"enabled": []}}

    def fake_discover(config, *, platforms=None, limit=None, keyword_overrides=None):
        captured["platforms"] = platforms
        captured["limit"] = limit
        captured["keyword_overrides"] = keyword_overrides
        return {"inserted": 0}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    monkeypatch.setattr(cli, "discover", fake_discover)

    assert cli.main([
        "--config", str(config_path),
        "discover",
        "--platform", "douyin",
        "--keyword", "Brasileirão",
        "--keyword", "TikTok Brasil",
        "--limit", "1",
    ]) == 0
    assert captured["config_path"] == config_path
    assert captured["platforms"] == ["douyin"]
    assert captured["limit"] == 1
    assert captured["keyword_overrides"] == ["Brasileirão", "TikTok Brasil"]
    assert '"inserted": 0' in capsys.readouterr().out


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


def test_ocr_blur_runs_only_for_chinese_source_platforms():
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
        platform="xiaohongshu",
        detected_language="zh",
        title_text="巴西足球中文字幕",
        localization_profile={"subtitle_mode": "none", "class": 3},
    ) is True
    assert should_ocr_blur_source_subtitles(
        "ocr_blur",
        platform="facebook",
        detected_language="zh",
        title_text="巴西足球中文字幕",
        localization_profile={
            "subtitle_mode": "ptbr_subtitles",
            "chinese_on_screen": True,
            "title_has_chinese": True,
        },
    ) is False
    assert should_ocr_blur_source_subtitles(
        "ocr_blur",
        platform="youtube",
        detected_language="unknown",
        title_text="阿根廷巴西球迷場上大鬥毆",
        localization_profile={"subtitle_mode": "none", "title_has_chinese": True},
    ) is False
    assert should_ocr_blur_source_subtitles(
        "ocr_blur",
        platform="tiktok",
        detected_language="zh",
        title_text="巴西足球",
        localization_profile={"subtitle_mode": "ptbr_subtitles", "chinese_on_screen": True},
    ) is False
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


def test_broad_brazil_market_filter_keeps_non_football_trends():
    config = {"selection": {"market_filter": {"enabled": True, "target": "brazil_trends"}}}
    assert candidate_market_rejection(
        config, {"title": "Resumo da novela das nove"}, keyword="Novela da Globo"
    ) == ""
    assert candidate_market_rejection(
        config, {"title": "Spotify Brasil música viral"}, keyword="Funk brasileiro"
    ) == ""
    assert candidate_market_rejection(
        config, {"title": "巴甲 比分预测 串关 稳胆"}, keyword="巴甲"
    ) == "prediction_or_betting_content"


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
