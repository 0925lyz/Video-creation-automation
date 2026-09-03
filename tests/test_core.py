from pathlib import Path
import json
import shutil
import sys
from types import SimpleNamespace
import wave

import pytest
from PIL import Image
from jaguartv_factory import cli

from jaguartv_factory.core import (
    analyze_visual_quality,
    brand_kit,
    candidate_market_rejection,
    connect_db,
    design_image_path,
    enforce_generic_remotion,
    format_srt_time,
    generate_funk_bgm,
    likely_language,
    load_config,
    mobile_review_format_needed,
    now_iso,
    platform_from_url,
    produce_candidate,
    production_design_config,
    register_url_stub_candidate,
    require_binary,
    ensure_remotion_runtime,
    render_video_remotion_generic,
    run_remotion_renderer_api,
    run_command,
    remotion_canvas_for_source,
    remotion_caption_cues,
    remotion_caption_style,
    remotion_captions_enabled,
    render_endcard,
    render_clean_segment,
    short_duration_bounds,
    update_render_job,
    source_filename_label,
    upsert_render_job,
    write_srt_blocks,
)


def test_short_duration_bounds_never_exceed_or_invert_thirty_seconds():
    assert short_duration_bounds({"edit": {"output_duration_sec": [5, 8]}}) == (12.0, 12.0)
    assert short_duration_bounds({"edit": {"output_duration_sec": [50, 90]}}) == (30.0, 30.0)


def test_visual_quality_rejects_sustained_black_frames(tmp_path: Path, monkeypatch):
    frame = bytes([0, 0, 0]) * (32 * 32)
    monkeypatch.setattr(
        "jaguartv_factory.core.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=frame * 4, stderr=b""),
    )

    result = analyze_visual_quality(
        tmp_path / "video.mp4", {"quality": {"visual": {"sample_fps": 2}}}
    )

    assert result["black_screen"] is True
    assert result["passed"] is False


def test_visual_quality_rejects_frozen_frames_but_ignores_short_repeat(tmp_path: Path, monkeypatch):
    red = bytes([180, 25, 20]) * (32 * 32)
    blue = bytes([20, 25, 180]) * (32 * 32)
    responses = iter((red + red + blue, red * 8))
    monkeypatch.setattr(
        "jaguartv_factory.core.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=next(responses), stderr=b""),
    )

    short = analyze_visual_quality(tmp_path / "short.mp4")
    frozen = analyze_visual_quality(tmp_path / "frozen.mp4")

    assert short["frozen_screen"] is False
    assert frozen["frozen_screen"] is True


def test_language_detection():
    assert likely_language("这是一个足球视频")[0] == "zh"
    assert likely_language("Você não vai acreditar que o Brasil marcou")[0] == "pt"


def test_freeform_design_preserves_newlines_and_ignores_blank_text(tmp_path: Path):
    config = {"_root": str(tmp_path), "edit": {}, "remotion": {}}
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
    assert "variants" not in patched["remotion"]["custom_design"]
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


def test_freeform_design_keeps_only_generic_variant(tmp_path: Path):
    config = {"_root": str(tmp_path), "edit": {}, "remotion": {}}
    patched = production_design_config(config, {"design": {"variants": ["FB版"], "layers": [
        {"id": "text", "type": "text", "text": "FB only", "x": 0.2, "y": 0.3},
    ]}})

    assert "variants" not in patched["remotion"]["custom_design"]


def test_design_overlay_cannot_use_single_variant_archive_bypass(tmp_path: Path, monkeypatch):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media"},
        "edit": {},
        "remotion": {},
        "brand": {"default_kit": "jaguartv", "kits": {"jaguartv": {}}},
    }
    base_video = tmp_path / "workspace" / "server_media" / "review" / "base-package" / "base.mp4"
    base_video.parent.mkdir(parents=True)
    base_video.write_bytes(b"base")
    candidate_id = register_url_stub_candidate(config, "https://www.facebook.com/watch/?v=design-archive")
    archive_calls = []

    def fake_archive(config_arg, package_id, review_dir):
        archive_calls.append((package_id, Path(review_dir)))
        return {"enabled": True, "files": {"video.mp4": {"url": "https://example.test/video.mp4"}}}

    monkeypatch.setattr("jaguartv_factory.core.require_binary", lambda name: name)
    monkeypatch.setattr("jaguartv_factory.core.media_duration", lambda path: 12.0)
    monkeypatch.setattr("jaguartv_factory.core.archive_review_package", fake_archive)
    patched = production_design_config(config, {"design": {
        "base_video_path": str(base_video),
        "variants": ["通用版"],
        "layers": [{"type": "text", "text": "Texto", "x": 0.1, "y": 0.1}],
    }})

    assert "variants" not in patched["remotion"]["custom_design"]
    assert archive_calls == []


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
    write_srt_blocks([(0.0, 2.0, "Primeira frase."), (2.0, 4.0, "Segunda frase!")], destination)
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


def test_remotion_caption_config():
    config = {
        "remotion": {
            "captions": {
                "enabled": True,
                "font_size_ratio": 0.5,
                "position": "middle",
            }
        }
    }
    assert remotion_captions_enabled(config) is True
    style = remotion_caption_style(config)
    assert style["position"] == "bottom"
    assert style["fontSizeRatio"] == 0.07
    assert style["maxLines"] == 2
    assert style["safeInsetRatio"] == 0.12


def test_pipeline_does_not_auto_add_remotion_promo_copy():
    config = load_config(Path("config/pipeline.yaml"))
    remotion = config["remotion"]

    assert remotion.get("top_badge", "") == ""
    assert remotion.get("bottom_headline", "") == ""
    assert remotion.get("bottom_subline", "") == ""
    assert remotion.get("endcard_cta", "") == ""


def test_remotion_generic_adds_top_banner_and_matching_cta(tmp_path: Path, monkeypatch):
    clean = tmp_path / "clean.mp4"
    clean.write_bytes(b"video")
    cta = tmp_path / "cta-portrait.jpg"
    Image.new("RGB", (720, 1280), "white").save(cta)
    brand_banner = tmp_path / "brand-banner.jpg"
    Image.new("RGB", (928, 129), "black").save(brand_banner)
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
    monkeypatch.setattr("jaguartv_factory.cta.select_random_cta", lambda config, orientation: {
        "id": "cta-1", "name": cta.name, "file_path": str(cta), "media_type": "image",
        "orientation": orientation, "duration_sec": 2.0,
    })

    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "edit": {"layout_mode": "original"},
        "mobile_review_format": {"enabled": False},
        "aspect_thresholds": {"vertical_min": 0.5, "vertical_max": 0.75, "horizontal_min": 1.6, "horizontal_max": 1.9},
        "remotion": {"render_runner": "renderer_api", "brand_banner_image": str(brand_banner)},
    }

    render_video_remotion_generic(config, clean, tmp_path / "generic.mp4")

    assert captured["通用版"]["ctaSrc"].endswith("cta.jpg")
    assert captured["通用版"]["ctaType"] == "image"
    assert captured["通用版"]["ctaSeconds"] == 2.0
    assert captured["通用版"]["durationSeconds"] == 22.0
    assert captured["通用版"]["brandBannerSrc"].endswith("brand-banner.jpg")
    assert captured["通用版"]["brandBannerAspectRatio"] == pytest.approx(928 / 129)
    assert "imgBottomBanner" not in captured["通用版"]


def test_remotion_renderer_cleans_isolated_tmpdir_after_failure(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    scripts = runtime / "scripts"
    scripts.mkdir(parents=True)
    renderer = scripts / "render.mjs"
    renderer.write_text(
        "\n".join([
            "import json, os, pathlib, sys",
            "payload = json.loads(pathlib.Path(sys.argv[1]).read_text())",
            "tmpdir = pathlib.Path(os.environ['TMPDIR'])",
            "(tmpdir / 'remotion-webpack-bundle-test').mkdir()",
            "pathlib.Path(payload['outputLocation']).with_name('renderer-tmpdir.txt').write_text(str(tmpdir))",
            "print(json.dumps({'event': 'error', 'message': 'expected failure'}), flush=True)",
            "raise SystemExit(1)",
        ]),
        encoding="utf-8",
    )
    output = tmp_path / "output.mp4"
    render_target = tmp_path / ".render.mp4"
    expected_tmp = tmp_path / "renderer-tmp"
    monkeypatch.setattr(
        "jaguartv_factory.core.tempfile.mkdtemp",
        lambda **kwargs: str(expected_tmp.mkdir() or expected_tmp),
    )
    monkeypatch.setattr("jaguartv_factory.core.require_binary", lambda name: sys.executable)
    monkeypatch.setattr("jaguartv_factory.core.upsert_render_job", lambda *args, **kwargs: None)
    monkeypatch.setattr("jaguartv_factory.core.update_render_job", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="expected failure"):
        run_remotion_renderer_api(
            {"run": {"timeout_sec": 10}},
            runtime,
            {},
            output,
            render_target,
            job_id="cleanup-test",
            candidate_id="candidate",
            variant="通用版",
        )

    renderer_tmp = Path((tmp_path / "renderer-tmpdir.txt").read_text(encoding="utf-8"))
    assert renderer_tmp == expected_tmp
    assert not renderer_tmp.exists()


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
    assert config["localization"]["krillinai"]["target_language"] == "pt"
    assert config["localization"]["krillinai"]["caption_source"] == "any"
    assert config["audio"]["bgm_volume"] == 0.0
    assert config["audio"]["source_mode"] == "localize"
    assert "add_bgm_under_source" not in config["remotion"]
    assert "endcard" not in config["brand"]["kits"]["jaguartv"]
    assert config["edit"]["render_engine"] == "remotion"
    assert config["edit"]["layout_mode"] == "original"
    assert config["remotion"]["captions"]["max_lines"] == 2
    assert config["selection"]["max_source_duration_sec"] == 900
    assert config["edit"]["segment_overlap_sec"] == 3
    assert config["mobile_review_format"]["target_resolution"] == [1080, 1440]
    assert config["sources"]["enabled"] == [
        "youtube", "bilibili", "douyin", "xiaohongshu", "tiktok",
        "facebook", "x", "instagram", "kwai",
    ]
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


def test_standard_production_requires_remotion_and_both_cta_orientations(monkeypatch):
    config = load_config(Path("config/pipeline.yaml"))
    requested = []
    monkeypatch.setattr("jaguartv_factory.cta.select_random_cta", lambda config, orientation: requested.append(orientation) or {})
    enforce_generic_remotion(config)
    assert requested == ["portrait", "landscape"]
    broken = {
        **config,
        "edit": {**config["edit"], "render_engine": "ffmpeg"},
    }
    with pytest.raises(RuntimeError, match="render_engine=remotion"):
        enforce_generic_remotion(broken)


def test_tiktok_source_filename_label_is_clean():
    assert source_filename_label("tiktok") == "TikTko"


def test_require_binary_prefers_virtualenv_sibling(tmp_path: Path, monkeypatch):
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    yt_dlp = venv_bin / "yt-dlp"
    yt_dlp.write_text("#!/bin/sh\n", encoding="utf-8")
    yt_dlp.chmod(0o755)
    monkeypatch.setattr("sys.executable", str(venv_bin / "python"))
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    assert require_binary("yt-dlp") == str(yt_dlp)


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://x.com/jaguartv/status/1", "x"),
        ("https://twitter.com/jaguartv/status/1", "x"),
        ("https://www.instagram.com/reel/abc/", "instagram"),
        ("https://www.kwai.com/short-video/abc", "kwai"),
    ],
)
def test_platform_from_url_supports_overseas_ytdlp_platforms(url: str, platform: str):
    assert platform_from_url(url) == platform


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


def test_localized_clean_render_keeps_demucs_backing_at_source_volume(tmp_path: Path, monkeypatch):
    media = tmp_path / "source.mp4"
    voice = tmp_path / "voice.wav"
    backing = tmp_path / "no_vocals.wav"
    output = tmp_path / "out.mp4"
    for path in (media, voice, backing):
        path.write_bytes(b"data")
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
            "edit": {"layout_mode": "original"},
            "audio": {"bgm_volume": 0.0, "source_music_volume": 0.73},
            "mobile_review_format": {"enabled": False},
            "aspect_thresholds": {"vertical_min": 0.5, "vertical_max": 0.75},
        },
        media, voice, backing, output, 12.0, "localized", 0.0,
        source_backing=True,
    )

    args = captured["args"]
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "[2:a]volume=0.73" in filter_complex
    assert "[2:a]volume=0.0" not in filter_complex


def test_generated_endcard_is_fully_opaque(tmp_path: Path):
    config = load_config(Path("config/pipeline.yaml"))
    output = render_endcard(config, brand_kit(config), tmp_path)
    alpha = Image.open(output).convert("RGBA").getchannel("A")
    assert alpha.getextrema() == (255, 255)
