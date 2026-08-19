from pathlib import Path
import shutil
from types import SimpleNamespace
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
    localization_profile_for_candidate,
    mobile_review_format_needed,
    now_iso,
    produce_candidate,
    production_design_config,
    register_url_stub_candidate,
    require_binary,
    ensure_remotion_runtime,
    render_video_remotion_variant,
    run_command,
    remotion_canvas_for_source,
    remotion_caption_cues,
    remotion_caption_style,
    remotion_captions_enabled_for_variant,
    render_endcard,
    render_clean_segment,
    render_hyperframes_html,
    tts_rate_percent,
    update_render_job,
    should_ocr_blur_source_subtitles,
    source_text,
    source_filename_label,
    safe_hyperframes_dir_name,
    upsert_render_job,
    write_srt,
)
from jaguartv_factory import cli
from jaguartv_factory.source_outro import (
    SourceOutroSettings,
    classify_outro_detection,
    detect_source_outro,
    visual_tail_signals,
)


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


def test_design_overlay_archives_review_package_with_package_id(tmp_path: Path, monkeypatch):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media"},
        "edit": {},
        "remotion": {"dual_variant": {}},
        "brand": {"default_kit": "jaguartv", "kits": {"jaguartv": {}}},
    }
    base_video = tmp_path / "workspace" / "server_media" / "review" / "base-package" / "base.mp4"
    base_video.parent.mkdir(parents=True)
    base_video.write_bytes(b"base")
    candidate_id = register_url_stub_candidate(config, "https://www.facebook.com/watch/?v=design-archive")
    row = connect_db(config).execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    archive_calls = []

    def fake_render(config_arg, clean_media, output, *, variant, subtitles=None, job_id=None, candidate_id=None):
        output.write_bytes(f"{variant}-design-render".encode())
        return {"variant": variant, "path": str(output), "filename": output.name}

    def fake_archive(config_arg, package_id, review_dir):
        archive_calls.append((package_id, Path(review_dir)))
        return {"enabled": True, "files": {"video.mp4": {"url": "https://example.test/video.mp4"}}}

    monkeypatch.setattr("jaguartv_factory.core.require_binary", lambda name: name)
    monkeypatch.setattr("jaguartv_factory.core.remotion_output_variants", lambda config_arg: ["通用版"])
    monkeypatch.setattr("jaguartv_factory.core.render_video_remotion_variant", fake_render)
    monkeypatch.setattr("jaguartv_factory.core.media_duration", lambda path: 12.0)
    monkeypatch.setattr("jaguartv_factory.core.qa_video", lambda path, config_arg=None: {"passed": True})
    monkeypatch.setattr("jaguartv_factory.core.render_cover_image", lambda config_arg, kit, source_video, destination: destination.write_bytes(b"cover") or "fake_cover")
    monkeypatch.setattr("jaguartv_factory.core.archive_review_package", fake_archive)

    review = produce_candidate(config, row, options={
        "batch_label": "文案设计版",
        "design": {
            "base_video_path": str(base_video),
            "variants": ["通用版"],
            "layers": [{"type": "text", "text": "Texto", "x": 0.1, "y": 0.1}],
        },
    })

    assert archive_calls == [(candidate_id, review)]
    assert (review / "video.mp4").is_file()
    assert (review / "metadata.json").is_file()
    archived_event = connect_db(config).execute(
        "SELECT 1 FROM events WHERE candidate_id=? AND event_type='SERVER_ARCHIVED'",
        (candidate_id,),
    ).fetchone()
    assert archived_event is not None


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


def test_remotion_caption_cues_attach_matching_ocr_region(tmp_path: Path):
    destination = tmp_path / "captions.srt"
    destination.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nQue golaço!\n",
        encoding="utf-8",
    )
    cues = remotion_caption_cues(
        destination,
        timed_regions=[
            {"start": 0.0, "end": 0.8, "regions": [[0.1, 0.7, 0.4, 0.8]]},
            {"start": 1.1, "end": 2.8, "regions": [[0.28, 0.36, 0.72, 0.44]]},
        ],
    )
    assert cues == [{
        "startSeconds": 1.0,
        "endSeconds": 3.0,
        "text": "Que golaço!",
        "region": [0.28, 0.36, 0.72, 0.44],
    }]


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
    assert style["fontSizeRatio"] == 0.085
    assert style["maxLines"] == 2


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


def test_visual_tail_signals_measure_stability_after_last_scene_cut(tmp_path: Path):
    frames = []
    for at_sec, color in ((9.0, "red"), (10.0, "blue"), (12.0, "green"), (15.0, "black"), (17.0, "black")):
        path = tmp_path / f"frame-{at_sec}.png"
        Image.new("RGB", (160, 90), color).save(path)
        frames.append((at_sec, path))

    signals = visual_tail_signals(frames, tail_start=9.0)

    assert signals["last_cut_sec"] == 15.0
    assert signals["tail_static"] is True
    assert signals["style_shift"] is True


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


def test_detect_source_outro_smoke_trims_static_promo_tail(tmp_path: Path):
    if not shutil.which("tesseract"):
        pytest.skip("tesseract is required for OCR promo-tail smoke")
    ffmpeg = require_binary("ffmpeg")
    source = tmp_path / "source.mp4"
    work = tmp_path / "job"
    work.mkdir()
    command = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", "testsrc2=s=320x180:d=14:r=12",
        "-f", "lavfi", "-i", "color=c=black:s=320x180:d=4:r=12",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=14",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100:d=4",
        "-filter_complex",
        "[1:v]drawtext=text='FOLLOW DOWNLOAD WHATSAPP':fontcolor=white:fontsize=24:x=12:y=78[promo];"
        "[0:v][promo]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(source),
    ]
    result = run_command(command, check=False)
    assert result.returncode == 0, result.stderr[-1000:]

    config = {
        "_root": str(tmp_path),
        "source_outro_trim": {
            "enabled": True,
            "scan_tail_sec": 8,
            "min_trim_sec": 2,
            "max_trim_sec": 6,
            "min_confidence": 0.72,
            "preserve_if_unsure": True,
            "ocr_enabled": True,
            "audio_guard_enabled": True,
            "save_evidence_frames": True,
            "promo_terms": {"en": ["follow", "download", "whatsapp"]},
        },
    }
    result = detect_source_outro(config, source, work, duration=18.0, options={}, run_command=run_command)

    assert result["applied"] is True
    assert result["trim_end_sec"] >= 2.0
    assert (work / "source_outro_trimmed.mp4").is_file()
    assert (work / "source_outro_detection.json").is_file()


def test_pipeline_does_not_auto_add_remotion_promo_copy():
    config = load_config(Path("config/pipeline.yaml"))
    remotion = config["remotion"]

    assert remotion.get("top_badge", "") == ""
    assert remotion.get("bottom_headline", "") == ""
    assert remotion.get("bottom_subline", "") == ""
    assert remotion.get("endcard_cta", "") == ""


def test_remotion_generic_keeps_our_endcard_and_fb_has_none(tmp_path: Path, monkeypatch):
    clean = tmp_path / "clean.mp4"
    clean.write_bytes(b"video")
    assets = tmp_path / "assets" / "brand"
    assets.mkdir(parents=True)
    for name in ("logo.png", "overlay_tu_yi.jpg", "overlay_tu_er.png", "endcard_portrait_green_v2.png", "endcard_landscape_blue_v2.png"):
        (assets / name).write_bytes(b"asset")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    captured: dict[str, dict] = {}

    def fake_runner(config, runtime_arg, props, output, render_target, **kwargs):
        captured[props["variant"]] = dict(props)
        render_target.write_bytes(b"rendered")
        render_target.replace(output)
        return {"runner": "fake"}

    monkeypatch.setattr("jaguartv_factory.core.ensure_remotion_runtime", lambda config: runtime)
    monkeypatch.setattr("jaguartv_factory.core.media_dimensions", lambda path: (1080, 1920))
    monkeypatch.setattr("jaguartv_factory.core.media_duration", lambda path: 20.0)
    monkeypatch.setattr("jaguartv_factory.core.run_remotion_renderer_api", fake_runner)

    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "edit": {"layout_mode": "original"},
        "mobile_review_format": {"enabled": False},
        "aspect_thresholds": {"vertical_min": 0.5, "vertical_max": 0.75, "horizontal_min": 1.6, "horizontal_max": 1.9},
        "brand": {"default_kit": "jaguartv", "kits": {"jaguartv": {"watermark": {"image": "assets/brand/logo.png"}}}},
        "remotion": {
            "render_runner": "renderer_api",
            "promo_duration_sec": 1.5,
            "dual_variant": {
                "enabled": True,
                "tu_yi": "assets/brand/overlay_tu_yi.jpg",
                "tu_er": "assets/brand/overlay_tu_er.png",
                "lv_tu": "assets/brand/endcard_portrait_green_v2.png",
                "lan_tu": "assets/brand/endcard_landscape_blue_v2.png",
            },
        },
    }

    render_video_remotion_variant(config, clean, tmp_path / "generic.mp4", variant="通用版")
    render_video_remotion_variant(config, clean, tmp_path / "fb.mp4", variant="FB版")

    assert "imgEndcard" in captured["通用版"]
    assert captured["通用版"]["promoSeconds"] == 1.5
    assert captured["通用版"]["durationSeconds"] == 21.5
    assert "imgEndcard" not in captured["FB版"]
    assert captured["FB版"]["promoSeconds"] == 0
    assert captured["FB版"]["durationSeconds"] == 20.0


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
    assert config["edit"]["ocr_backend"] == "auto"
    assert config["localization"]["pyvideotrans"]["enabled"] is True
    assert config["localization"]["voice_enabled"] is True
    assert config["audio"]["bgm_volume"] == 0.0
    assert config["audio"]["source_mode"] == "auto"
    assert config["remotion"]["add_bgm_under_source"] is False
    assert config["brand"]["kits"]["jaguartv"]["endcard"]["site"] == "Jarg.top"
    assert config["edit"]["render_engine"] == "remotion"
    assert config["edit"]["layout_mode"] == "original"
    assert config["edit"]["source_subtitle_cleanup"] == "ocr_blur"
    assert config["edit"]["ocr_auto_lower_third_fallback"] is False
    assert config["edit"]["ocr_blur_sigma"] >= 50
    assert config["localization"]["asr_enabled"] is True
    assert config["localization"]["preserve_backing_track"] is True
    assert config["localization"]["require_backing_track"] is True
    assert config["remotion"]["captions"]["max_lines"] == 2
    assert config["edit"]["short_video_threshold_sec"] == 75
    assert config["selection"]["max_source_duration_sec"] == 1800
    assert config["brand"]["kits"]["jaguartv"]["endcard"]["mode"] == "orientation_image"
    assert config["brand"]["kits"]["jaguartv"]["endcard"]["duration_sec"] == 1.5
    assert config["mobile_review_format"]["target_resolution"] == [1080, 1440]
    assert config["sources"]["enabled"] == ["douyin", "tiktok", "facebook"]
    assert config["sources"]["keywords_file"] == "config/keywords.brazil.yaml"


def test_tts_rate_percent_accepts_multiplier_and_percent():
    assert tts_rate_percent({"localization": {"tts_rate": 1.08}}) == "+8%"
    assert tts_rate_percent({"localization": {"tts_rate": "+12%"}}) == "+12%"


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
    ) is False
    assert should_ocr_blur_source_subtitles(
        "ocr_blur",
        platform="bilibili",
        detected_language="zh",
        title_text="中文歌足球混剪",
        localization_profile={"subtitle_mode": "none", "class": 3, "chinese_on_screen": False},
    ) is False
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


def test_auto_audio_strategy_uses_silence_when_source_has_no_audio(tmp_path: Path, monkeypatch):
    config = {"audio": {"source_mode": "auto"}, "localization": {"asr_enabled": False}}
    media = tmp_path / "source.mp4"
    media.touch()
    monkeypatch.setattr("jaguartv_factory.core.media_has_audio", lambda _path: False)
    mode, transcript, reason = choose_audio_strategy(config, tmp_path, media)
    assert mode == "silent"
    assert transcript == ""
    assert reason == "no_speech_evidence_source_has_no_audio"


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


def test_chinese_localization_rejects_metadata_fallback_without_speech(tmp_path: Path):
    media = tmp_path / "source.mp4"
    media.touch()

    with pytest.raises(RuntimeError, match="metadata fallback is disabled"):
        source_text(
            tmp_path,
            media,
            "中文标题只能作为检索信息，不能拿来生成重复配音。",
            enable_asr=False,
            config={"localization": {"asr_enabled": False}},
            allow_metadata_fallback=False,
        )

    payload = (tmp_path / "transcript_source.json").read_text(encoding="utf-8")
    assert '"provider": "speech_required"' in payload
    assert "metadata_fallback" not in payload


def test_chinese_localization_uses_asr_transcript_before_metadata_fallback(tmp_path: Path, monkeypatch):
    media = tmp_path / "source.mp4"
    media.touch()

    monkeypatch.setattr(
        "jaguartv_factory.core.transcribe_with_whisper",
        lambda media_arg, work_arg, config_arg: "中文解说正在介绍巴西本土电商平台和 Casas Bahia 的区别。",
    )

    assert source_text(
        tmp_path,
        media,
        "中文标题只能作为检索信息，不能拿来生成重复配音。",
        enable_asr=True,
        config={"localization": {"asr_enabled": True}},
        allow_metadata_fallback=False,
    ) == "中文解说正在介绍巴西本土电商平台和 Casas Bahia 的区别。"


def test_localization_profile_only_localizes_chinese_audio_from_bilibili_or_douyin(tmp_path: Path, monkeypatch):
    config = {"_root": str(tmp_path), "run": {"workspace": "workspace"}, "edit": {"ocr_backend": "tesseract"}}
    connection = connect_db(config)
    timestamp = now_iso()
    rows = []
    for platform in ("facebook", "bilibili", "douyin"):
        candidate = f"{platform}-candidate"
        work = tmp_path / "workspace" / "jobs" / candidate
        work.mkdir(parents=True)
        (work / "source.zh.srt").write_text(
            "1\n00:00:00,000 --> 00:00:04,000\n中文解说正在介绍这个精彩片段。\n",
            encoding="utf-8",
        )
        media = work / "source.mp4"
        media.touch()
        connection.execute(
            """INSERT INTO candidates(id,platform,source_id,url,title,description,duration,view_count,
            detected_language,score,status,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                candidate,
                platform,
                candidate,
                f"https://example.test/{candidate}",
                "中文解说",
                "",
                30,
                100,
                "zh-CN",
                70,
                "DOWNLOADED",
                "{}",
                timestamp,
                timestamp,
            ),
        )
        rows.append((platform, candidate, work, media))
    connection.commit()
    monkeypatch.setattr("jaguartv_factory.core.media_has_audio", lambda _path: True)

    profiles = {}
    for platform, candidate, work, media in rows:
        row = connection.execute("SELECT * FROM candidates WHERE id=?", (candidate,)).fetchone()
        profiles[platform] = localization_profile_for_candidate(row, {}, work, media, config)

    assert profiles["facebook"]["audio_mode"] == "preserve_source"
    assert profiles["facebook"]["reason"] == "no_chinese_speech_or_subtitle_evidence"
    assert profiles["bilibili"]["audio_mode"] == "localized"
    assert profiles["douyin"]["audio_mode"] == "localized"


def test_class_two_candidate_routes_to_original_passthrough(tmp_path: Path, monkeypatch):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "edit": {"passthrough_clean_sources": True},
        "selection": {},
        "brand": {"default_kit": "jaguartv", "kits": {"jaguartv": {}}},
    }
    candidate_id = register_url_stub_candidate(config, "https://www.bilibili.com/video/BVclean")
    work = tmp_path / "workspace" / "jobs" / candidate_id
    work.mkdir(parents=True, exist_ok=True)
    source = work / "source.mp4"
    source.write_bytes(b"original")
    row = connect_db(config).execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    strategy = SimpleNamespace(
        content_type="sports",
        content_type_confidence=1.0,
        matched_rules=[],
        segment_strategy="whole_source",
        audio_policy="preserve_output_audio",
        operator_override=False,
    )
    calls: dict[str, Path] = {}

    def fake_passthrough(config_arg, row_arg, media, *args, **kwargs):
        calls["media"] = Path(media)
        review = tmp_path / "review"
        review.mkdir()
        return review

    monkeypatch.setattr("jaguartv_factory.core.require_binary", lambda name: name)
    monkeypatch.setattr("jaguartv_factory.core.media_duration", lambda path: 18.0)
    monkeypatch.setattr("jaguartv_factory.core.detect_source_outro", lambda *args, **kwargs: {"applied": False})
    monkeypatch.setattr("jaguartv_factory.core.resolve_production_strategy", lambda *args, **kwargs: strategy)
    monkeypatch.setattr("jaguartv_factory.core.assert_render_allowed", lambda *args, **kwargs: {"allowed": True})
    monkeypatch.setattr(
        "jaguartv_factory.core.localization_profile_for_candidate",
        lambda *args, **kwargs: {"class": 2, "audio_mode": "preserve_source", "reason": "no_chinese_speech_or_subtitle_evidence"},
    )
    monkeypatch.setattr("jaguartv_factory.core.produce_passthrough_review_package", fake_passthrough)

    review = produce_candidate(config, row)
    assert review == tmp_path / "review"
    assert calls["media"] == source


def test_localized_clean_render_uses_ptbr_voice_without_fixed_bgm(tmp_path: Path, monkeypatch):
    media = tmp_path / "source.mp4"
    voice = tmp_path / "voice.aiff"
    output = tmp_path / "out.mp4"
    media.write_bytes(b"media")
    voice.write_bytes(b"voice")
    captured: dict[str, list[str]] = {}

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run_command(args, **kwargs):
        captured["args"] = [str(part) for part in args]
        Path(args[-1]).write_bytes(b"rendered")
        return Result()

    monkeypatch.setattr("jaguartv_factory.core.media_dimensions", lambda path: (1080, 1920))
    monkeypatch.setattr("jaguartv_factory.core.run_command", fake_run_command)

    render_clean_segment(
        {
            "_root": str(tmp_path),
            "run": {"workspace": "workspace"},
            "edit": {"layout_mode": "original", "source_subtitle_cleanup": "off"},
            "mobile_review_format": {"enabled": False},
            "aspect_thresholds": {"vertical_min": 0.5, "vertical_max": 0.75},
        },
        media,
        voice,
        None,
        output,
        12.0,
        "localized",
        0.0,
    )

    args = captured["args"]
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "-stream_loop" not in args
    assert "amix" not in filter_complex
    assert "asplit" not in filter_complex
    assert "[1:a]volume" in filter_complex
    assert output.read_bytes() == b"rendered"


def test_generated_endcard_is_fully_opaque(tmp_path: Path):
    config = load_config(Path("config/pipeline.yaml"))
    output = render_endcard(config, brand_kit(config), tmp_path)
    alpha = Image.open(output).convert("RGBA").getchannel("A")
    assert alpha.getextrema() == (255, 255)
