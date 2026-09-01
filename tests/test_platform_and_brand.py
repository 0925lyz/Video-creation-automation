import json
from io import BytesIO
import time
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import pytest

from jaguartv_factory.browser_scraper import (
    _looks_like_tiktok_media_url,
    _ordered_tiktok_media_urls,
)
from jaguartv_factory.core import (
    assert_script_is_portuguese,
    brand_kit,
    candidate_id,
    connect_db,
    discover,
    download_candidate,
    ingest_uploaded_media,
    load_config,
    now_iso,
    produce_candidate,
    produce_top,
    render_overlay_assets,
    terms_for_platform,
    write_srt_blocks,
)
from jaguartv_factory.dashboard import (
    DashboardApplication,
    candidate_design_info,
    candidate_page,
    candidate_rows,
    delete_candidates,
    download_claim_rows,
    recover_interrupted_productions,
    save_download_claim,
    skip_candidate,
    update_download_claim_metrics,
)
from jaguartv_factory.scoring import score_candidate_v2
from jaguartv_factory.server_store import (
    archive_review_package,
    complete_chunked_upload,
    find_upload,
    init_chunked_upload,
    list_uploads,
    save_upload_chunk,
)
from jaguartv_factory.source_imports import create_source_import
from jaguartv_factory.sources import (
    F2DouyinAdapter,
    SourceError,
    XhsApiAdapter,
    YtDlpAdapter,
    f2_runtime_status,
    get_adapter,
    yt_dlp_binary,
)


def make_config(tmp_path: Path) -> dict:
    return {"_root": str(tmp_path), "run": {"workspace": "workspace"}}


def insert_candidate(config: dict, candidate_id: str = "c1", status: str = "DISCOVERED") -> None:
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """INSERT INTO candidates(id,platform,source_id,url,title,description,duration,view_count,
        detected_language,score,status,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (candidate_id, "youtube", "s1", "https://example.test/v", "Demo", "", 30, 100, "en", 70,
         status, json.dumps({"keyword": "football", "thumbnail": "https://img.test/t.jpg",
                             "score_breakdown": {"velocity": 0.5, "total": 70}}), timestamp, timestamp),
    )
    connection.commit()


def create_test_source_import(config: dict, url: str = "https://youtu.be/demo") -> dict:
    return create_source_import(
        config,
        platform="youtube",
        url=url,
        source_category="素材",
        operator_id="test-operator",
        resolver=lambda host, port, *args: [(2, 1, 6, "", ("142.250.72.206", port))],
    )


def validated_test_media(path: Path) -> dict:
    return {
        "path": str(path.resolve()),
        "relative_path": f"jobs/{path.parent.name}/{path.name}",
        "sha256": "d" * 64,
        "mime_type": "video/mp4",
        "format_name": "mp4",
        "size_bytes": path.stat().st_size,
        "duration_sec": 30.0,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "has_video": True,
        "has_audio": True,
        "decode_valid": True,
    }


def test_scoring_v2_prefers_fresh_engaging_content():
    now = time.time()
    fresh = {"view_count": 300_000, "like_count": 20_000, "comment_count": 2_000,
             "duration": 40, "title": "brasil football goal", "timestamp": now - 86400}
    stale = {"view_count": 9_000_000, "duration": 1500, "title": "compilation",
             "timestamp": now - 800 * 86400}
    fresh_total, fresh_breakdown = score_candidate_v2(fresh, "football")
    stale_total, stale_breakdown = score_candidate_v2(stale, "football")
    assert fresh_total > stale_total
    assert fresh_breakdown["freshness"] == 1.0
    assert stale_breakdown["editability"] < 0.5
    assert 0 <= fresh_total <= 100


def test_scoring_marks_estimated_dimensions():
    total, breakdown = score_candidate_v2({"view_count": 100, "title": "x", "duration": 30}, "kw")
    assert "estimated" in breakdown
    assert "engagement" in breakdown["estimated"]


def test_keyword_platform_routing():
    terms = {"en": ["football skills"], "pt": ["brasileirão"], "zh-CN": ["巴西足球"]}
    config = {"sources": {}}
    assert terms_for_platform(terms, "youtube", config) == ["football skills", "brasileirão"]
    assert terms_for_platform(terms, "douyin", config) == ["巴西足球"]
    routed = terms_for_platform(
        terms,
        "tiktok",
        {"sources": {"platform_language": {"tiktok": ["en", "pt"]}}},
    )
    assert routed == ["football skills", "brasileirão"]
    # platform with no preferred-language terms falls back to everything
    assert set(terms_for_platform({"fr": ["but incroyable"]}, "douyin", config)) == {"but incroyable"}


def test_portuguese_gate_accepts_short_localized_samba_copy():
    assert_script_is_portuguese(
        "Olha só o que aconteceu aqui. Equipe profissional de samba. "
        "Gostou? Descubra mais conteúdos no JaguarTV Hoje."
    )


def test_source_adapters_resolve_and_fail_cleanly():
    config = {"sources": {"adapters": {"douyin": {"api_base": "http://127.0.0.1:1"}}}}
    adapter = get_adapter("douyin", config)
    assert isinstance(adapter.search("足球", 3), list)
    assert get_adapter("tiktok", config).platform == "tiktok"
    with pytest.raises(SourceError):
        get_adapter("unknown-platform", config)


def test_xhs_adapter_uses_browser_search(monkeypatch):
    monkeypatch.setattr(
        "jaguartv_factory.browser_scraper.search_xiaohongshu",
        lambda config, term, limit: [{"id": "note1", "webpage_url": "https://www.xiaohongshu.com/explore/note1"}],
    )
    adapter = XhsApiAdapter("xiaohongshu", {"_root": "/tmp/app", "_workspace": "workspace"})
    assert adapter.search("弗拉门戈", 1)[0]["id"] == "note1"


def test_douyin_adapter_falls_back_to_browser_search(monkeypatch):
    monkeypatch.setattr(
        "jaguartv_factory.browser_scraper.search_douyin",
        lambda config, term, limit: [{"id": "123", "webpage_url": "https://www.douyin.com/video/123"}],
    )
    adapter = get_adapter(
        "douyin",
        {"_root": "/tmp/app", "run": {"workspace": "workspace"}, "sources": {"adapters": {"douyin": {"api_base": "http://127.0.0.1:1"}}}},
    )
    assert adapter.search("巴甲", 1)[0]["id"] == "123"


def test_facebook_adapter_uses_browser_search(monkeypatch):
    monkeypatch.setattr(
        "jaguartv_factory.browser_scraper.search_facebook",
        lambda config, term, limit: [{"id": "fb1", "webpage_url": "https://www.facebook.com/reel/fb1"}],
    )
    adapter = get_adapter(
        "facebook",
        {"_root": "/tmp/app", "run": {"workspace": "workspace"}, "sources": {}},
    )
    assert adapter.search("Brasileirão", 1)[0]["id"] == "fb1"


def test_tiktok_download_uses_browser_fallback_when_ytdlp_fails(tmp_path: Path, monkeypatch):
    def fake_run(args, *, timeout=None):
        class Result:
            returncode = 1
            stderr = "Unexpected response from webpage request"
            stdout = ""

        return Result()

    def fake_browser_download(config, url, destination):
        destination.write_bytes(b"mp4")
        return {
            "video_url": "https://v16-webapp-prime.tiktokcdn.com/video.mp4",
            "title": "TikTok Brasil",
        }

    monkeypatch.setattr("jaguartv_factory.sources.yt_dlp_binary", lambda: "/usr/bin/yt-dlp")
    monkeypatch.setattr("jaguartv_factory.sources.run", fake_run)
    monkeypatch.setattr("jaguartv_factory.browser_scraper.download_tiktok_video", fake_browser_download)
    destination = tmp_path / "source.%(ext)s"

    adapter = YtDlpAdapter("tiktok", {"_root": str(tmp_path), "_workspace": "workspace"})
    adapter.download("https://www.tiktok.com/@demo/video/123", str(destination))

    assert (tmp_path / "source.mp4").read_bytes() == b"mp4"
    info = json.loads((tmp_path / "source.info.json").read_text(encoding="utf-8"))
    assert info["browser_fallback"]["title"] == "TikTok Brasil"


def test_kwai_download_resolves_page_media_then_uses_ytdlp(tmp_path: Path, monkeypatch):
    calls = []

    def fake_run(args, *, timeout=None, cwd=None):
        calls.append(args)
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr("jaguartv_factory.sources.yt_dlp_binary", lambda: "/usr/bin/yt-dlp")
    monkeypatch.setattr("jaguartv_factory.sources.run", fake_run)
    monkeypatch.setattr(
        "jaguartv_factory.sources.resolve_kwai_video_url",
        lambda url, timeout=30: "https://aws-br-cdn.kwai.net/upic/source.mp4?tag=verified",
    )

    adapter = YtDlpAdapter("kwai", {})
    adapter.download(
        "https://www.kwai.com/@KwaiBrasilOficial/video/5233051404659401808",
        str(tmp_path / "source.%(ext)s"),
    )

    download_call = next(args for args in calls if "-o" in args)
    assert download_call[-1] == "https://aws-br-cdn.kwai.net/upic/source.mp4?tag=verified"


def test_kwai_page_parser_matches_requested_photo_id():
    from jaguartv_factory.sources import kwai_media_url_from_html

    html = '''
      <a-video-player photo-id="other" src="https://cdn.test/other.mp4"></a-video-player>
      <a-video-player photo-id="5233051404659401808"
        src="https://aws-br-cdn.kwai.net/upic/source.mp4?tag=verified"></a-video-player>
    '''

    assert kwai_media_url_from_html(html, "5233051404659401808") == (
        "https://aws-br-cdn.kwai.net/upic/source.mp4?tag=verified"
    )


def test_tiktok_media_filter_rejects_login_page_animation():
    login_animation = (
        "https://sf16-website-login.neutral.ttwstatic.com/obj/"
        "tiktok_web_login_static/tiktok/webapp/main/webapp-desktop/playback1.mp4"
    )
    real_stream = "https://v16-webapp-prime.tiktokcdn.com/video/tos/useast2a/tos-useast2a-ve-0068c001.mp4"

    assert not _looks_like_tiktok_media_url(login_animation)
    assert _looks_like_tiktok_media_url(real_stream)


def test_tiktok_media_candidates_prefer_real_streams_and_dedupe():
    login_animation = (
        "https://sf16-website-login.neutral.ttwstatic.com/obj/"
        "tiktok_web_login_static/tiktok/webapp/main/webapp-desktop/playback1.mp4"
    )
    generic_mp4 = "https://cdn.example.test/media/video.mp4"
    real_stream = "https://v16-webapp-prime.tiktokcdn.com/video/tos/useast2a/real.mp4"

    assert _ordered_tiktok_media_urls([
        login_animation,
        generic_mp4,
        real_stream,
        real_stream,
    ]) == [real_stream, generic_mp4]


def test_yt_dlp_adapter_supports_browser_cookie_env(monkeypatch):
    monkeypatch.setenv("JAGUARTV_YOUTUBE_COOKIES_FROM_BROWSER", "chrome")
    adapter = YtDlpAdapter("youtube", {})
    assert adapter._cookie_args() == ["--cookies-from-browser", "chrome"]


def test_yt_dlp_adapter_supports_js_runtime_env(monkeypatch):
    monkeypatch.setenv("JAGUARTV_YTDLP_JS_RUNTIME", "node:/tmp/node")
    adapter = YtDlpAdapter("youtube", {})
    assert adapter._js_runtime_args() == ["--js-runtimes", "node:/tmp/node"]


def test_yt_dlp_adapter_uses_latest_managed_session_cookie(tmp_path: Path):
    session = tmp_path / "workspace" / "sessions" / "youtube" / "account"
    session.mkdir(parents=True)
    cookie = session / "cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\t" + "x" * 80, encoding="utf-8")
    (session / "manifest.json").write_text(
        json.dumps({
            "status": "READY",
            "cookie_file_path": "workspace/sessions/youtube/account/cookies.txt",
        }),
        encoding="utf-8",
    )
    adapter = get_adapter("youtube", {"_root": str(tmp_path), "run": {"workspace": "workspace"}, "sources": {}})
    assert adapter._cookie_args() == ["--cookies", str(cookie.resolve())]


def test_yt_dlp_binary_can_resolve_from_virtualenv():
    assert Path(yt_dlp_binary()).name == "yt-dlp"


def test_six_overseas_platforms_use_ytdlp_and_douyin_uses_f2(tmp_path: Path):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "sources": {"adapters": {}},
    }

    for platform in ("youtube", "tiktok", "facebook", "x", "instagram", "kwai"):
        assert isinstance(get_adapter(platform, config), YtDlpAdapter)
    assert isinstance(get_adapter("douyin", config), F2DouyinAdapter)


def test_external_discovery_only_platforms_are_not_sent_to_ytdlp_search():
    from jaguartv_factory.sources import SEARCHABLE_PLATFORMS

    assert "x" not in SEARCHABLE_PLATFORMS
    assert "instagram" not in SEARCHABLE_PLATFORMS
    assert "kwai" not in SEARCHABLE_PLATFORMS


def test_copy_source_material_reads_canonical_source_keyword_and_category_tags():
    from jaguartv_factory.publishing_copywriter import source_material_from

    material = source_material_from(
        {"title": "Fonte", "source_keyword": "copa do mundo", "source_category": "futebol"},
        {"source_keyword": "torcida brasileira", "category_tags": ["Brasil", "Seleção"]},
        {},
        [],
    )

    assert material["keywords"] == ["torcida brasileira", "copa do mundo"]
    assert material["category_tags"] == ["Brasil", "Seleção", "futebol"]


def test_f2_douyin_download_uses_config_file_without_cookie_argument(tmp_path: Path, monkeypatch):
    executable = tmp_path / "bin" / "f2"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    f2_config = tmp_path / "secrets" / "f2.yaml"
    f2_config.parent.mkdir()
    f2_config.write_text("cookie: redacted\n", encoding="utf-8")
    output = tmp_path / "downloads" / "candidate.%(ext)s"
    calls = []

    def fake_run(args, *, timeout=None, cwd=None):
        calls.append(args)
        if args[0].endswith("python"):
            return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
        output_dir = Path(args[args.index("--path") + 1])
        (output_dir / "123.mp4").write_bytes(b"video")
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr("jaguartv_factory.sources.run", fake_run)
    adapter = F2DouyinAdapter(
        "douyin",
        {"binary": str(executable), "config_file": str(f2_config), "_root": str(tmp_path)},
    )

    adapter.download("https://www.douyin.com/video/123", str(output))

    assert (tmp_path / "downloads" / "candidate.mp4").read_bytes() == b"video"
    f2_call = next(call for call in calls if call[0] == str(executable.resolve()))
    assert f2_call[:3] == [str(executable.resolve()), "dy", "--url"]
    assert "--config" in f2_call
    assert "--cookie" not in f2_call
    assert "-k" not in f2_call


def test_f2_douyin_prepares_upstream_cache_schema_before_download(tmp_path: Path, monkeypatch):
    executable = tmp_path / "bin" / "f2"
    python = executable.with_name("python")
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    python.chmod(0o755)
    output = tmp_path / "downloads" / "candidate.%(ext)s"
    calls = []

    def fake_run(args, *, timeout=None, cwd=None):
        calls.append((args, cwd))
        if args[0] == str(python.resolve()):
            return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
        output_dir = Path(args[args.index("--path") + 1])
        (output_dir / "123.mp4").write_bytes(b"video")
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr("jaguartv_factory.sources.run", fake_run)
    adapter = F2DouyinAdapter("douyin", {"binary": str(executable), "_root": str(tmp_path)})

    adapter.download("https://www.douyin.com/video/123", str(output))

    assert calls[0][0][0] == str(python.resolve())
    assert 'for missing in ("caption", "caption_raw")' in calls[0][0][2]
    assert "ALTER TABLE video_info ADD COLUMN {missing} TEXT" in calls[0][0][2]
    assert calls[0][1] == calls[1][1]


def test_f2_douyin_download_allows_missing_optional_config(tmp_path: Path, monkeypatch):
    executable = tmp_path / "bin" / "f2"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    output = tmp_path / "downloads" / "candidate.%(ext)s"
    calls = []

    def fake_run(args, *, timeout=None, cwd=None):
        calls.append((args, cwd))
        output_dir = Path(args[args.index("--path") + 1])
        (output_dir / "123.mp4").write_bytes(b"video")
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr("jaguartv_factory.sources.run", fake_run)
    adapter = F2DouyinAdapter(
        "douyin",
        {"binary": str(executable), "config_file": "", "_root": str(tmp_path)},
    )

    adapter.download("https://www.douyin.com/video/123", str(output))

    assert (tmp_path / "downloads" / "candidate.mp4").read_bytes() == b"video"
    assert "--config" not in calls[0][0]
    assert Path(calls[0][1]).name.startswith(".f2-douyin-")


def test_f2_douyin_uses_managed_cookie_via_private_temp_config(tmp_path: Path, monkeypatch):
    executable = tmp_path / "bin" / "f2"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    session = tmp_path / "workspace" / "sessions" / "douyin" / "account"
    session.mkdir(parents=True)
    cookie_file = session / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".douyin.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\tsecret-value\n",
        encoding="utf-8",
    )
    (session / "manifest.json").write_text(
        json.dumps({"status": "READY", "cookie_file_path": str(cookie_file)}),
        encoding="utf-8",
    )
    observed = {}

    def fake_run(args, *, timeout=None, cwd=None):
        config_path = Path(args[args.index("--config") + 1])
        observed["args"] = list(args)
        observed["config"] = config_path.read_text(encoding="utf-8")
        observed["mode"] = config_path.stat().st_mode & 0o777
        output_dir = Path(args[args.index("--path") + 1])
        (output_dir / "123.mp4").write_bytes(b"video")
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr("jaguartv_factory.sources.run", fake_run)
    adapter = F2DouyinAdapter(
        "douyin",
        {"binary": str(executable), "config_file": "", "_root": str(tmp_path), "_workspace": "workspace"},
    )

    adapter.download(
        "https://www.douyin.com/video/123",
        str(tmp_path / "downloads" / "candidate.%(ext)s"),
    )

    assert "secret-value" in observed["config"]
    assert "secret-value" not in " ".join(observed["args"])
    assert observed["mode"] == 0o600


def test_f2_douyin_rejects_non_douyin_url(tmp_path: Path):
    adapter = F2DouyinAdapter("douyin", {"_root": str(tmp_path)})

    with pytest.raises(SourceError, match="Douyin URL"):
        adapter.download("https://example.com/video/123", str(tmp_path / "candidate.%(ext)s"))


def test_f2_runtime_status_probes_douyin_and_tiktok_separately(tmp_path: Path, monkeypatch):
    executable = tmp_path / "bin" / "f2"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    def fake_run(args, *, timeout=None, cwd=None):
        if args[1:] == ["--version"]:
            return type("Result", (), {"returncode": 0, "stdout": "Version 0.0.1.7\n", "stderr": ""})()
        if args[1:3] == ["dy", "-h"]:
            return type("Result", (), {"returncode": 0, "stdout": "douyin help", "stderr": ""})()
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "msToken failed"})()

    monkeypatch.setattr("jaguartv_factory.sources.run", fake_run)

    status = f2_runtime_status({"binary": str(executable), "_root": str(tmp_path)})

    assert status["ok"] is True
    assert status["version"] == "0.0.1.7"
    assert status["apps"]["douyin"]["ok"] is True
    assert status["apps"]["tiktok"]["ok"] is False
    assert "msToken" in status["apps"]["tiktok"]["error"]


def test_brand_kit_and_endcard_render(tmp_path: Path):
    config = load_config(Path("config/pipeline.yaml"))
    kit = brand_kit(config)
    assert kit["_name"] == "jaguartv"
    assert kit["watermark"]["mode"] == "image"
    srt = tmp_path / "s.srt"
    write_srt_blocks([(0.0, 2.0, "Primeira frase."), (2.0, 4.0, "Segunda frase!")], srt)
    logo, subtitles, endcard = render_overlay_assets(srt, tmp_path / "overlays", config)
    assert logo.exists() and endcard.exists() and subtitles
    # generated endcard should be full-frame
    from PIL import Image
    assert Image.open(endcard).size == (1080, 1920)


def test_review_package_archives_to_factory_server_storage(tmp_path: Path):
    config = {
        "_root": str(tmp_path),
        "storage": {
            "root": "workspace/server_media",
            "public_base_url": "https://factory.jarg.top/media",
        },
    }
    review = tmp_path / "review"
    review.mkdir()
    (review / "video.mp4").write_bytes(b"video")
    (review / "metadata.json").write_text('{"job_id":"c1"}', encoding="utf-8")
    result = archive_review_package(config, "c1", review)
    assert result["provider"] == "factory_server"
    assert result["files"]["video.mp4"]["url"] == "https://factory.jarg.top/media/review/c1/video.mp4"
    metadata = json.loads((review / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["server_storage"]["package_id"] == "c1"
    assert metadata["server_storage"]["remote_sync"]["enabled"] is False


def test_chunked_upload_assembles_and_lists_private_asset(tmp_path: Path):
    config = {
        "_root": str(tmp_path),
        "storage": {
            "root": "workspace/server_media",
            "max_upload_bytes": 3 * 1024 * 1024,
            "upload_chunk_bytes": 1024 * 1024,
        },
    }
    payload = b"a" * (1024 * 1024) + b"b" * 12345
    started = init_chunked_upload(
        config, filename="reaction.mp4", kind="reaction", content_length=len(payload)
    )
    chunk_size = started["chunk_bytes"]
    for index in range(started["chunk_count"]):
        part = payload[index * chunk_size:(index + 1) * chunk_size]
        saved = save_upload_chunk(config, started["id"], index, BytesIO(part), len(part))
        assert saved["size"] == len(part)
    completed = complete_chunked_upload(config, started["id"])
    assert Path(completed["path"]).read_bytes() == payload
    assert list_uploads(config)[0]["original_filename"] == "reaction.mp4"
    assert find_upload(config, started["id"])["size"] == len(payload)


def test_design_image_upload_accepts_png_and_rejects_video(tmp_path: Path):
    config = {"_root": str(tmp_path), "storage": {"root": "workspace/server_media"}}
    started = init_chunked_upload(config, filename="logo.png", kind="design_image", content_length=4)
    save_upload_chunk(config, started["id"], 0, BytesIO(b"logo"), 4)
    completed = complete_chunked_upload(config, started["id"])

    assert completed["kind"] == "design_image"
    assert find_upload(config, started["id"])["original_filename"] == "logo.png"
    with pytest.raises(ValueError, match="unsupported design_image extension"):
        init_chunked_upload(config, filename="logo.mp4", kind="design_image", content_length=4)


def write_server_review_package(config: dict, package_id: str, source_candidate_id: str) -> Path:
    package = Path(config["_root"]) / "workspace" / "server_media" / "review" / package_id
    package.mkdir(parents=True, exist_ok=True)
    (package / "video.mp4").write_bytes(b"review-video")
    (package / "metadata.json").write_text(
        json.dumps({
            "job_id": package_id,
            "source_job_id": source_candidate_id,
            "source": {
                "platform": "tiktok",
                "url": "https://example.test/source",
                "title": "Part package",
            },
            "segment": {"duration_sec": 30, "highlight_score": 10},
        }),
        encoding="utf-8",
    )
    return package


def test_server_review_part_rows_collapse_under_source_candidate(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config, "source-parent", "READY_FOR_REVIEW")
    write_server_review_package(config, "source-parent_part01", "source-parent")

    rows = candidate_rows(config, "READY_FOR_REVIEW", 20)
    ids = [row["id"] for row in rows]
    parent = next(row for row in rows if row["id"] == "source-parent")

    assert "source-parent_part01" not in ids
    assert parent["output_assets"][0]["id"] == "source-parent_part01"


def test_design_production_resolves_part_package_to_source_candidate(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    insert_candidate(config, "source-parent", "READY_FOR_REVIEW")
    write_server_review_package(config, "source-parent_part01", "source-parent")
    calls = []

    def fake_produce(config_arg, limit, candidate, progress_callback=None, options=None):
        calls.append((candidate, options))
        connection = connect_db(config)
        connection.execute(
            "UPDATE candidates SET status='READY_FOR_REVIEW',updated_at=? WHERE id=?",
            (now_iso(), candidate),
        )
        connection.commit()
        return {"selected": 1, "produced": 1, "failed": 0}

    monkeypatch.setattr("jaguartv_factory.dashboard.produce_top", fake_produce)
    app = DashboardApplication(("127.0.0.1", 0), config)
    try:
        app.tasks["design-task"] = {"status": "RUNNING"}
        result = app.run_candidate_batch(
            "design-task",
            "produce",
            ["source-parent_part01"],
            {"design": {"layers": [], "base_asset_id": "source-parent_part01"}},
        )
    finally:
        app.server_close()

    assert result["failed"] == 0
    assert calls[0][0] == "source-parent"


def test_design_production_materializes_server_review_package_without_parent(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    write_server_review_package(config, "orphan_part01", "missing-parent")
    calls = []

    def fake_produce(config_arg, limit, candidate, progress_callback=None, options=None):
        calls.append(candidate)
        connection = connect_db(config)
        connection.execute(
            "UPDATE candidates SET status='READY_FOR_REVIEW',updated_at=? WHERE id=?",
            (now_iso(), candidate),
        )
        connection.commit()
        return {"selected": 1, "produced": 1, "failed": 0}

    monkeypatch.setattr("jaguartv_factory.dashboard.produce_top", fake_produce)
    app = DashboardApplication(("127.0.0.1", 0), config)
    try:
        app.tasks["design-task"] = {"status": "RUNNING"}
        result = app.run_candidate_batch(
            "design-task",
            "produce",
            ["orphan_part01"],
            {"design": {"layers": [], "base_asset_id": "orphan_part01"}},
        )
    finally:
        app.server_close()

    row = connect_db(config).execute("SELECT parent_id,status FROM candidates WHERE id='orphan_part01'").fetchone()
    assert result["failed"] == 0
    assert calls == ["orphan_part01"]
    assert row["parent_id"] == "missing-parent"


def test_design_production_materializes_asset_package_when_parent_was_deleted(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    package = write_server_review_package(config, "deleted-parent_part01", "deleted-parent")
    (package / "0803-YouTube-7-通用版.mp4").write_bytes(b"named-review-video")
    calls = []

    def fake_produce(config_arg, limit, candidate, progress_callback=None, options=None):
        calls.append((candidate, options["design"]["base_asset_id"]))
        connection = connect_db(config)
        connection.execute(
            "UPDATE candidates SET status='READY_FOR_REVIEW',updated_at=? WHERE id=?",
            (now_iso(), candidate),
        )
        connection.commit()
        return {"selected": 1, "produced": 1, "failed": 0}

    monkeypatch.setattr("jaguartv_factory.dashboard.produce_top", fake_produce)
    app = DashboardApplication(("127.0.0.1", 0), config)
    try:
        app.tasks["design-task"] = {"status": "RUNNING"}
        result = app.run_candidate_batch(
            "design-task",
            "produce",
            ["deleted-parent"],
            {
                "design": {
                    "layers": [],
                    "base_asset_id": "deleted-parent_part01:0803-YouTube-7-通用版",
                }
            },
        )
    finally:
        app.server_close()

    row = connect_db(config).execute(
        "SELECT parent_id,status FROM candidates WHERE id='deleted-parent_part01'"
    ).fetchone()
    assert result["failed"] == 0
    assert calls == [("deleted-parent_part01", "deleted-parent_part01:0803-YouTube-7-通用版")]
    assert row["parent_id"] == "deleted-parent"


def test_design_info_returns_asset_package_when_parent_was_deleted(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    package = write_server_review_package(config, "deleted-parent_part01", "deleted-parent")
    asset = package / "0803-YouTube-7-通用版.mp4"
    asset.write_bytes(b"named-review-video")
    monkeypatch.setattr("jaguartv_factory.dashboard.media_dimensions", lambda path: (1080, 1440))

    info = candidate_design_info(
        config,
        "deleted-parent::asset::deleted-parent_part01:0803-YouTube-7-通用版",
    )

    assert info["source_candidate_id"] == "deleted-parent_part01"
    assert info["design_base_asset_id"] == "deleted-parent_part01:0803-YouTube-7-通用版"
    assert info["source_preview_url"].startswith("/media/review/deleted-parent_part01/")


def test_source_upload_becomes_downloaded_candidate(tmp_path: Path, monkeypatch):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media"},
        "selection": {"max_source_duration_sec": 900},
    }
    source = tmp_path / "workspace" / "server_media" / "uploads" / "source" / ("a" * 32 + ".mp4")
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    monkeypatch.setattr("jaguartv_factory.core.media_duration", lambda _: 61.0)
    candidate = ingest_uploaded_media(config, {
        "id": "a" * 32,
        "path": str(source),
        "original_filename": "owned-source.mp4",
        "storage_uri": "server://uploads/source/demo.mp4",
    })
    row = connect_db(config).execute("SELECT * FROM candidates WHERE id=?", (candidate,)).fetchone()
    assert row["status"] == "DOWNLOADED"
    assert row["platform"] == "server_upload"
    assert (tmp_path / "workspace" / "jobs" / candidate / "source.mp4").is_file()


def test_download_candidate_rejects_invalid_media_file(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    insert_candidate(config)

    class FakeAdapter:
        def download(self, url, output_template):
            Path(output_template.replace("%(ext)s", "mp4")).write_bytes(b"not a video")

    monkeypatch.setattr("jaguartv_factory.sources.get_adapter", lambda platform, config: FakeAdapter())
    monkeypatch.setattr(
        "jaguartv_factory.core.media_duration",
        lambda path: (_ for _ in ()).throw(RuntimeError("ffprobe failed")),
    )
    row = connect_db(config).execute("SELECT * FROM candidates WHERE id=?", ("c1",)).fetchone()

    with pytest.raises(RuntimeError, match="not a valid video"):
        download_candidate(config, row)

    stored = connect_db(config).execute("SELECT status FROM candidates WHERE id=?", ("c1",)).fetchone()
    assert stored["status"] == "DOWNLOAD_FAILED"
    assert not (tmp_path / "workspace" / "jobs" / "c1" / "source.mp4").exists()


def test_inventory_scans_server_review_packages_without_db_row(tmp_path: Path):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {
            "root": "workspace/server_media",
            "public_base_url": "https://factory.jarg.top/media",
        },
    }
    review = tmp_path / "workspace" / "server_media" / "review" / "pkg1"
    review.mkdir(parents=True)
    (review / "video.mp4").write_bytes(b"video")
    (review / "cover.jpg").write_bytes(b"cover")
    (review / "review.json").write_text('{"decision":"pending"}', encoding="utf-8")
    (review / "metadata.json").write_text(
        json.dumps({
            "job_id": "pkg1",
            "source": {"platform": "youtube", "url": "https://example.test/v", "title": "Server only"},
            "content_type": "football",
            "segment_strategy": "sports_highlight",
            "audio_policy": "localize_ptbr",
            "segment": {"duration_sec": 30, "highlight_score": 77},
        }),
        encoding="utf-8",
    )
    rows = candidate_rows(config)
    assert rows[0]["id"] == "pkg1"
    assert rows[0]["status"] == "READY_FOR_REVIEW"
    assert rows[0]["server_url"] == "https://factory.jarg.top/media/review/pkg1/video.mp4"
    assert rows[0]["download_url"].endswith("&download=1")
    assert rows[0]["output_count"] == 1


def test_inventory_can_sort_by_crawl_time_instead_of_update_time(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config, candidate_id="older-crawl", status="DISCOVERED")
    connection = connect_db(config)
    connection.execute(
        "UPDATE candidates SET source_id=? WHERE id=?",
        ("older-source", "older-crawl"),
    )
    connection.commit()
    insert_candidate(config, candidate_id="newer-crawl", status="DISCOVERED")
    connection = connect_db(config)
    connection.execute(
        "UPDATE candidates SET source_id=?,created_at=?,updated_at=? WHERE id=?",
        ("newer-source", "2026-02-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", "newer-crawl"),
    )
    connection.execute(
        "UPDATE candidates SET created_at=?,updated_at=? WHERE id=?",
        ("2026-01-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00", "older-crawl"),
    )
    connection.commit()

    time_sorted = candidate_page(config, sort="time", page_size=10)["items"]
    updated_sorted = candidate_page(config, sort="updated", page_size=10)["items"]

    assert [item["id"] for item in time_sorted[:2]] == ["newer-crawl", "older-crawl"]
    assert [item["id"] for item in updated_sorted[:2]] == ["older-crawl", "newer-crawl"]


def test_inventory_parent_candidate_exposes_all_segment_outputs(tmp_path: Path):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {
            "root": "workspace/server_media",
            "public_base_url": "https://factory.jarg.top/media",
        },
    }
    insert_candidate(config, candidate_id="source1", status="APPROVED")
    review_root = tmp_path / "workspace" / "ready_for_review"
    for number in (1, 2):
        package_id = f"source1_part{number:02d}"
        package = review_root / package_id
        package.mkdir(parents=True)
        (package / "video.mp4").write_bytes(f"video-{number}".encode())
        if number == 1:
            (package / "cover.jpg").write_bytes(b"cover")
        (package / "metadata.json").write_text(
            json.dumps({
                "server_storage": {
                    "files": {
                        "video.mp4": {
                            "url": f"https://factory.jarg.top/media/review/{package_id}/video.mp4"
                        }
                    }
                }
            }),
            encoding="utf-8",
        )

    row = next(item for item in candidate_rows(config) if item["id"] == "source1")
    assert row["output_count"] == 2
    assert [asset["id"] for asset in row["output_assets"]] == ["source1_part01", "source1_part02"]
    assert row["video_url"].startswith("/media/source1_part01/video.mp4")
    assert row["download_url"].endswith("&download=1")
    assert row["server_url"].endswith("/review/source1_part01/video.mp4")
    assert row["output_assets"][1]["filename"] == "source1_part02.mp4"


def test_review_output_label_prefers_design_batch_name_for_part_assets(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config, "source-parent", "READY_FOR_REVIEW")
    package = write_server_review_package(config, "source-parent_part03", "source-parent")
    (package / "0816-TikTko-文案设计版-2-通用版.mp4").write_bytes(b"design-video")
    metadata = json.loads((package / "metadata.json").read_text(encoding="utf-8"))
    metadata["batch_label"] = "文案设计版"
    (package / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    row = next(item for item in candidate_rows(config) if item["id"] == "source-parent")
    design_asset = next(
        asset for asset in row["output_assets"]
        if asset["filename"] == "0816-TikTko-文案设计版-2-通用版.mp4"
    )

    assert design_asset["label"] == "文案设计版 · 通用版"
    assert design_asset["batch_label"] == "文案设计版"


def test_produce_never_keeps_ready_status_when_partial_outputs_exist(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    insert_candidate(config, "partial-candidate", "DOWNLOADED")
    review = tmp_path / "workspace" / "ready_for_review" / "partial-candidate_part01"
    review.mkdir(parents=True)
    (review / "video.mp4").write_bytes(b"already-rendered")
    work = tmp_path / "workspace" / "jobs" / "partial-candidate"
    work.mkdir(parents=True)
    (work / "source.mp4").write_bytes(b"source")

    def fail_after_partial(*_args, **_kwargs):
        raise RuntimeError("later segment failed")

    monkeypatch.setattr("jaguartv_factory.production.produce_candidate", fail_after_partial)
    result = produce_top(config, 1, "partial-candidate")

    assert result == {"selected": 1, "produced": 0, "failed": 1}
    connection = connect_db(config)
    row = connection.execute("SELECT status FROM candidates WHERE id='partial-candidate'").fetchone()
    event = connection.execute(
        "SELECT event_type,payload_json FROM events WHERE candidate_id='partial-candidate' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "PRODUCTION_FAILED"
    assert event["event_type"] == "PRODUCTION_FAILED"
    assert "later segment failed" in event["payload_json"]


def test_inventory_exposes_only_generic_and_hides_historical_fb_output(tmp_path: Path):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {
            "root": "workspace/server_media",
            "public_base_url": "https://factory.jarg.top/media",
        },
    }
    insert_candidate(config, candidate_id="source2", status="APPROVED")
    package = tmp_path / "workspace" / "server_media" / "review" / "source2"
    package.mkdir(parents=True)
    (package / "video.mp4").write_bytes(b"default")
    (package / "0729-YouTube-1-通用版.mp4").write_bytes(b"generic")
    (package / "0729-YouTube-1-FB版.mp4").write_bytes(b"facebook")
    (package / "metadata.json").write_text(
        json.dumps({
            "job_id": "source2",
            "server_storage": {
                "files": {
                    "0729-YouTube-1-通用版.mp4": {
                        "url": "https://factory.jarg.top/media/review/source2/0729-YouTube-1-通用版.mp4"
                    },
                    "0729-YouTube-1-FB版.mp4": {
                        "url": "https://factory.jarg.top/media/review/source2/0729-YouTube-1-FB版.mp4"
                    },
                }
            },
        }),
        encoding="utf-8",
    )

    row = next(item for item in candidate_rows(config) if item["id"] == "source2")
    assert row["output_count"] == 1
    assert [asset["variant"] for asset in row["output_assets"]] == ["通用版"]
    assert row["output_assets"][0]["download_url"].endswith("&download=1")
    assert "%E9%80%9A%E7%94%A8%E7%89%88" in row["output_assets"][0]["video_url"]


def test_media_download_forces_candidate_filename(tmp_path: Path):
    config = make_config(tmp_path)
    package = tmp_path / "workspace" / "ready_for_review" / "candidate_part01"
    package.mkdir(parents=True)
    (package / "video.mp4").write_bytes(b"finished-video")
    server = DashboardApplication(("127.0.0.1", 0), config)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(
            f"http://127.0.0.1:{port}/media/candidate_part01/video.mp4?download=1",
            timeout=5,
        ) as response:
            assert response.read() == b"finished-video"
            assert response.headers["Content-Disposition"] == (
                "attachment; filename*=UTF-8''candidate_part01.mp4"
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_download_claim_records_owner_and_metrics(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config, candidate_id="claim1", status="APPROVED")
    claim = save_download_claim(config, {
        "candidate_id": "claim1:0730-TikTok-1-通用版",
        "asset_id": "claim1:0730-TikTok-1-通用版",
        "filename": "0730-TikTok-1-通用版.mp4",
        "variant": "通用版",
        "publisher": "Lucas",
        "publish_platform": "facebook",
    })
    assert claim["candidate_id"] == "claim1"

    rows = download_claim_rows(config)
    assert rows[0]["publisher"] == "Lucas"
    assert rows[0]["variant"] == "通用版"
    assert rows[0]["filename"] == "0730-TikTok-1-通用版.mp4"

    update_download_claim_metrics(config, {
        "claim_id": claim["id"],
        "views": 1200,
        "clicks": 34,
        "registrations": 5,
    })
    connection = connect_db(config)
    snapshot = connection.execute("SELECT * FROM performance_snapshots WHERE candidate_id='claim1'").fetchone()
    assert snapshot["platform"] == "facebook"
    assert snapshot["views"] == 1200


def test_skip_candidate_and_inventory_fields(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config)
    rows = candidate_rows(config)
    assert rows[0]["keyword"] == "football"
    assert rows[0]["thumbnail_url"] == "https://img.test/t.jpg"
    assert rows[0]["score_breakdown"]["total"] == 70
    result = skip_candidate(config, {"candidate_id": "c1"})
    assert result["status"] == "SKIPPED"
    # skipping again stays idempotent; other statuses are protected
    assert skip_candidate(config, {"candidate_id": "c1"})["status"] == "SKIPPED"
    with pytest.raises(ValueError):
        skip_candidate(config, {"candidate_id": "missing"})


def test_delete_candidate_clears_db_review_job_and_inventory_files(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config, candidate_id="c-delete", status="APPROVED")
    connection = connect_db(config)
    for table in ("events", "publications", "performance_snapshots", "conversion_events", "feedback_actions", "render_jobs"):
        if table == "events":
            connection.execute(
                "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                ("c-delete", "READY_FOR_REVIEW", "{}", now_iso()),
            )
        elif table == "publications":
            connection.execute(
                "INSERT INTO publications(candidate_id,platform,created_at,updated_at) VALUES(?,?,?,?)",
                ("c-delete", "youtube", now_iso(), now_iso()),
            )
        elif table == "performance_snapshots":
            connection.execute(
                "INSERT INTO performance_snapshots(candidate_id,platform,captured_at) VALUES(?,?,?)",
                ("c-delete", "youtube", now_iso()),
            )
        elif table == "conversion_events":
            connection.execute(
                "INSERT INTO conversion_events(candidate_id,event_type,occurred_at) VALUES(?,?,?)",
                ("c-delete", "install", now_iso()),
            )
        elif table == "feedback_actions":
            connection.execute(
                "INSERT INTO feedback_actions(candidate_id,action_type,reason,created_at) VALUES(?,?,?,?)",
                ("c-delete", "BOOST_KEYWORD", "demo", now_iso()),
            )
        else:
            connection.execute(
                """
                INSERT INTO render_jobs
                  (id,candidate_id,variant,engine,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                ("render-delete", "c-delete", "通用版", "remotion_renderer_api", "COMPLETED", now_iso(), now_iso()),
            )
    connection.commit()

    job_dir = tmp_path / "workspace" / "jobs" / "c-delete"
    review_dir = tmp_path / "workspace" / "ready_for_review" / "c-delete_part01"
    inventory_file = tmp_path / "workspace" / "server_media" / "inventory" / "通用版" / "youtube" / "demo.mp4"
    job_dir.mkdir(parents=True)
    review_dir.mkdir(parents=True)
    inventory_file.parent.mkdir(parents=True)
    (job_dir / "source.mp4").write_bytes(b"source")
    inventory_file.write_bytes(b"inventory-video")
    (review_dir / "video.mp4").write_bytes(b"review-video")
    (review_dir / "metadata.json").write_text(
        json.dumps({
            "job_id": "c-delete_part01",
            "output_variants": [{"inventory_path": str(inventory_file)}],
        }),
        encoding="utf-8",
    )

    result = delete_candidates(config, {"candidate_ids": ["c-delete"]})
    assert result["deleted"] == 1
    assert result["bytes_freed"] >= len(b"source") + len(b"review-video") + len(b"inventory-video")
    assert not job_dir.exists()
    assert not review_dir.exists()
    assert not inventory_file.exists()
    connection = connect_db(config)
    assert connection.execute("SELECT COUNT(*) count FROM candidates WHERE id='c-delete'").fetchone()["count"] == 0
    assert connection.execute(
        "SELECT COUNT(*) count FROM seen_sources WHERE platform='youtube' AND source_key='id:s1'"
    ).fetchone()["count"] == 1
    for table in ("events", "publications", "performance_snapshots", "conversion_events", "feedback_actions", "render_jobs"):
        assert connection.execute(f"SELECT COUNT(*) count FROM {table} WHERE candidate_id='c-delete'").fetchone()["count"] == 0


def test_delete_candidate_removes_server_review_package_linked_by_metadata(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config, candidate_id="source-delete", status="APPROVED")
    review_dir = tmp_path / "workspace" / "server_media" / "review" / "rendered-package"
    inventory_file = tmp_path / "workspace" / "server_media" / "inventory" / "通用版" / "youtube" / "metadata-linked.mp4"
    review_dir.mkdir(parents=True)
    inventory_file.parent.mkdir(parents=True)
    (review_dir / "video.mp4").write_bytes(b"server-review")
    inventory_file.write_bytes(b"inventory-video")
    (review_dir / "metadata.json").write_text(
        json.dumps({
            "job_id": "rendered-package",
            "source_job_id": "source-delete",
            "output_variants": [{"inventory_path": str(inventory_file)}],
        }),
        encoding="utf-8",
    )

    result = delete_candidates(config, {"candidate_ids": ["source-delete"]})

    assert result["deleted"] == 1
    assert not review_dir.exists()
    assert not inventory_file.exists()


def test_delete_100_candidates_keeps_seen_history(tmp_path: Path):
    config = make_config(tmp_path)
    connection = connect_db(config)
    timestamp = now_iso()
    for index in range(100):
        source_id = f"s{index}"
        connection.execute(
            """INSERT INTO candidates(id,platform,source_id,url,title,description,duration,view_count,
            detected_language,score,status,metadata_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                candidate_id("youtube", source_id, f"https://example.test/{source_id}"),
                "youtube",
                source_id,
                f"https://example.test/{source_id}",
                "Demo",
                "",
                30,
                10,
                "en",
                50,
                "DISCOVERED",
                "{}",
                timestamp,
                timestamp,
            ),
        )
    connection.commit()
    connect_db(config).close()

    ids = [
        row["id"]
        for row in connect_db(config).execute("SELECT id FROM candidates ORDER BY id").fetchall()
    ]
    result = delete_candidates(config, {"candidate_ids": ids})

    connection = connect_db(config)
    assert result["deleted"] == 100
    assert connection.execute("SELECT COUNT(*) count FROM candidates").fetchone()["count"] == 0
    assert connection.execute("SELECT COUNT(*) count FROM seen_sources").fetchone()["count"] == 100


def test_discover_skips_sources_seen_before_even_after_delete(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    keywords = tmp_path / "keywords.yaml"
    keywords.write_text(
        "demo:\n  enabled: true\n  terms:\n    pt:\n      - brasil\n",
        encoding="utf-8",
    )
    config["sources"] = {"keywords_file": str(keywords), "enabled": ["youtube"]}
    config["discovery"] = {"max_candidates_per_keyword": 1}

    class Adapter:
        def search(self, term: str, limit: int) -> list[dict]:
            return [{
                "id": "same-video",
                "webpage_url": "https://www.youtube.com/watch?v=same-video",
                "title": "Brasil futebol hoje",
                "description": "Você não vai acreditar no Brasil hoje",
                "duration": 30,
                "view_count": 1000,
                "extractor_key": "Youtube",
            }]

    monkeypatch.setattr("jaguartv_factory.sources.SEARCHABLE_PLATFORMS", {"youtube"})
    monkeypatch.setattr("jaguartv_factory.sources.get_adapter", lambda platform, config: Adapter())

    first = discover(config)
    cid = candidate_id("youtube", "same-video", "https://www.youtube.com/watch?v=same-video")
    delete_candidates(config, {"candidate_ids": [cid]})
    second = discover(config)

    connection = connect_db(config)
    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["duplicates"] == 1
    assert connection.execute("SELECT COUNT(*) count FROM candidates").fetchone()["count"] == 0
    assert connection.execute("SELECT COUNT(*) count FROM seen_sources").fetchone()["count"] == 1


def test_discover_rejects_unknown_and_over_15_minute_sources_before_database_insert(
    tmp_path: Path, monkeypatch
):
    config = make_config(tmp_path)
    keywords = tmp_path / "keywords.yaml"
    keywords.write_text(
        "demo:\n  enabled: true\n  terms:\n    pt:\n      - brasil\n",
        encoding="utf-8",
    )
    config["sources"] = {"keywords_file": str(keywords), "enabled": ["youtube"]}
    config["selection"] = {"max_source_duration_sec": 1800}

    class Adapter:
        def search(self, _term: str, _limit: int) -> list[dict]:
            return [
                {"id": "unknown", "webpage_url": "https://example.test/unknown", "title": "Brasil futebol", "duration": None},
                {"id": "long", "webpage_url": "https://example.test/long", "title": "Brasil futebol", "duration": 901},
                {"id": "allowed", "webpage_url": "https://example.test/allowed", "title": "Brasil futebol", "duration": 900},
            ]

    monkeypatch.setattr("jaguartv_factory.sources.SEARCHABLE_PLATFORMS", {"youtube"})
    monkeypatch.setattr("jaguartv_factory.sources.get_adapter", lambda _platform, _config: Adapter())

    result = discover(config)

    assert result["duration_unknown"] == 1
    assert result["too_long"] == 1
    assert result["inserted"] == 1
    rows = connect_db(config).execute("SELECT source_id,duration FROM candidates").fetchall()
    assert [(row["source_id"], row["duration"]) for row in rows] == [("allowed", 900.0)]


def test_discover_merges_runtime_hot_keywords_without_modifying_base_file(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    keywords = tmp_path / "config" / "keywords.yaml"
    runtime_keywords = tmp_path / "workspace" / "runtime" / "keywords.trends.yaml"
    keywords.parent.mkdir(parents=True)
    runtime_keywords.parent.mkdir(parents=True)
    keywords.write_text(
        "base:\n  enabled: true\n  terms:\n    pt:\n      - futebol base\n",
        encoding="utf-8",
    )
    runtime_keywords.write_text(
        "'google_trends_br_daily:足球类':\n  enabled: true\n  category: 足球类\n  terms:\n    pt:\n      - flamengo agora\n",
        encoding="utf-8",
    )
    original = keywords.read_text(encoding="utf-8")
    config["sources"] = {"keywords_file": str(keywords), "enabled": ["youtube"]}
    config["trends"] = {"runtime_keywords_file": str(runtime_keywords)}
    config["discovery"] = {"max_candidates_per_keyword": 1}
    searched: list[str] = []

    class Adapter:
        def search(self, term: str, limit: int) -> list[dict]:
            searched.append(term)
            return []

    monkeypatch.setattr("jaguartv_factory.sources.SEARCHABLE_PLATFORMS", {"youtube"})
    monkeypatch.setattr("jaguartv_factory.sources.get_adapter", lambda platform, config: Adapter())

    discover(config)

    assert searched == ["futebol base", "flamengo agora"]
    assert keywords.read_text(encoding="utf-8") == original


def test_delete_server_only_review_package(tmp_path: Path):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media"},
    }
    review_dir = tmp_path / "workspace" / "server_media" / "review" / "server-only"
    review_dir.mkdir(parents=True)
    (review_dir / "video.mp4").write_bytes(b"review-video")
    (review_dir / "metadata.json").write_text(json.dumps({"job_id": "server-only"}), encoding="utf-8")
    assert candidate_rows(config)[0]["id"] == "server-only"
    result = delete_candidates(config, {"candidate_ids": ["server-only"]})
    assert result["deleted"] == 1
    assert not review_dir.exists()
    assert candidate_rows(config) == []


def test_inventory_exposes_latest_failure_reason(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config, status="DOWNLOAD_FAILED")
    connection = connect_db(config)
    connection.execute(
        "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        ("c1", "DOWNLOAD_FAILED", json.dumps({"stderr": "HTTP Error 403: Forbidden"}), now_iso()),
    )
    connection.commit()
    row = candidate_rows(config)[0]
    assert row["failure_event"] == "DOWNLOAD_FAILED"
    assert row["failure_detail"] == "HTTP Error 403: Forbidden"


def test_retry_production_redownloads_when_failed_source_is_missing(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    insert_candidate(config, status="PRODUCTION_FAILED")
    calls = []

    def fake_download(config_arg, limit, candidate):
        calls.append(("download", candidate))
        return {"selected": 1, "downloaded": 1, "failed": 0}

    def fake_produce(config_arg, limit, candidate, progress_callback=None, options=None):
        calls.append(("produce", candidate))
        return {"selected": 1, "produced": 1, "failed": 0}

    monkeypatch.setattr("jaguartv_factory.dashboard.download_top", fake_download)
    monkeypatch.setattr("jaguartv_factory.dashboard.produce_top", fake_produce)
    app = DashboardApplication(("127.0.0.1", 0), config)
    try:
        app.tasks["retry-task"] = {"status": "RUNNING"}
        result = app.run_candidate_batch("retry-task", "produce", ["c1"], {})
    finally:
        app.server_close()

    assert result["failed"] == 0
    assert calls == [("produce", "c1")]


def test_interrupted_production_recovers_to_downloaded_when_source_exists(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config, "interrupted", "PRODUCTION_RUNNING")
    work = tmp_path / "workspace" / "jobs" / "interrupted"
    work.mkdir(parents=True)
    (work / "source.mp4").write_bytes(b"video")

    recovered = recover_interrupted_productions(config)

    assert recovered == 1
    row = connect_db(config).execute("SELECT status FROM candidates WHERE id=?", ("interrupted",)).fetchone()
    assert row["status"] == "DOWNLOADED"


def test_dashboard_delegates_status_transition_to_production_service(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    insert_candidate(config, "to-produce", "DOWNLOADED")
    work = tmp_path / "workspace" / "jobs" / "to-produce"
    work.mkdir(parents=True)
    (work / "source.mp4").write_bytes(b"video")
    observed = []

    def fake_produce(config_arg, limit, candidate, progress_callback=None, options=None):
        row = connect_db(config).execute("SELECT status FROM candidates WHERE id=?", ("to-produce",)).fetchone()
        observed.append((row["status"], options.get("trigger_source")))
        connection = connect_db(config)
        connection.execute(
            "UPDATE candidates SET status='READY_FOR_REVIEW',updated_at=? WHERE id=?",
            (now_iso(), "to-produce"),
        )
        connection.commit()
        return {"selected": 1, "produced": 1, "failed": 0}

    monkeypatch.setattr("jaguartv_factory.dashboard.produce_top", fake_produce)
    app = DashboardApplication(("127.0.0.1", 0), config)
    try:
        app.tasks["produce-task"] = {"status": "RUNNING"}
        result = app.run_candidate_batch("produce-task", "produce", ["to-produce"], {})
    finally:
        app.server_close()

    assert result["failed"] == 0
    assert observed == [("DOWNLOADED", "dashboard")]


def test_url_ingest_downloads_to_waiting_for_production(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    calls = []
    source_import = create_test_source_import(config)
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.normalize_import_url",
        lambda url, platform: url,
    )

    def fake_inspect(config_arg, url, requested_platform="", allow_stub=False):
        calls.append(("inspect", url, requested_platform, allow_stub))
        insert_candidate(config_arg, "url-candidate", "DISCOVERED")
        return "url-candidate"

    def fake_download(config_arg, limit, candidate):
        calls.append(("download", candidate))
        media = tmp_path / "workspace" / "jobs" / candidate / "source.mp4"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"source")
        return {"selected": 1, "downloaded": 1, "failed": 0}

    monkeypatch.setattr("jaguartv_factory.dashboard.inspect_url", fake_inspect)
    monkeypatch.setattr("jaguartv_factory.dashboard.download_top", fake_download)
    monkeypatch.setattr(
        "jaguartv_factory.source_imports.validate_imported_media",
        lambda config_arg, candidate_id, path: validated_test_media(path),
    )
    app = DashboardApplication(("127.0.0.1", 0), config)
    try:
        app.tasks["ingest-task"] = {"status": "RUNNING", "total": 1}
        app._run_action("ingest-task", {
            "action": "ingest",
            "platform": "youtube",
            "source_import_id": source_import["id"],
        })
        task = app.tasks["ingest-task"]
    finally:
        app.server_close()

    assert task["status"] == "COMPLETED"
    assert task["result"]["candidate_id"] == "url-candidate"
    assert task["result"]["status"] == "DOWNLOADED"
    assert task["result"]["download"]["downloaded"] == 1
    assert calls == [
        ("inspect", "https://youtu.be/demo", "youtube", True),
        ("download", "url-candidate"),
    ]


def test_url_ingest_is_successful_when_candidate_is_already_downloaded(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    insert_candidate(config, "already-downloaded", "DOWNLOADED")
    source_import = create_test_source_import(config)
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.normalize_import_url",
        lambda url, platform: url,
    )
    media = tmp_path / "workspace" / "jobs" / "already-downloaded" / "source.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"source")
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.inspect_url",
        lambda config_arg, url, requested_platform="", allow_stub=False: "already-downloaded",
    )

    def fail_download(*args, **kwargs):
        raise AssertionError("already-downloaded URL should not be downloaded again")

    monkeypatch.setattr("jaguartv_factory.dashboard.download_top", fail_download)
    monkeypatch.setattr(
        "jaguartv_factory.source_imports.validate_imported_media",
        lambda config_arg, candidate_id, path: validated_test_media(path),
    )
    app = DashboardApplication(("127.0.0.1", 0), config)
    try:
        app.tasks["ingest-task"] = {"status": "RUNNING", "total": 1}
        app._run_action("ingest-task", {
            "action": "ingest",
            "platform": "youtube",
            "source_import_id": source_import["id"],
        })
        task = app.tasks["ingest-task"]
    finally:
        app.server_close()

    assert task["status"] == "COMPLETED"
    assert task["result"]["candidate_id"] == "already-downloaded"
    assert task["result"]["status"] == "DOWNLOADED"
    assert task["result"]["download"]["already_downloaded"] is True


def test_url_ingest_failure_reports_download_reason(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    insert_candidate(config, "download-failed", "DOWNLOAD_FAILED")
    source_import = create_test_source_import(config)
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.normalize_import_url",
        lambda url, platform: url,
    )
    connection = connect_db(config)
    connection.execute(
        "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        ("download-failed", "DOWNLOAD_FAILED", json.dumps({"stderr": "Sign in to confirm you are not a bot"}), now_iso()),
    )
    connection.commit()
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.inspect_url",
        lambda config_arg, url, requested_platform="", allow_stub=False: "download-failed",
    )
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.download_top",
        lambda config_arg, limit, candidate: {"selected": 1, "downloaded": 0, "failed": 1},
    )
    app = DashboardApplication(("127.0.0.1", 0), config)
    try:
        app.tasks["ingest-task"] = {"status": "RUNNING", "total": 1}
        app._run_action("ingest-task", {
            "action": "ingest",
            "platform": "youtube",
            "source_import_id": source_import["id"],
        })
        task = app.tasks["ingest-task"]
    finally:
        app.server_close()

    assert task["status"] == "FAILED"
    assert "服务器下载失败" in task["error"]
    assert "not a bot" in task["error"]
