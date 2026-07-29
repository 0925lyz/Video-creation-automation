import json
from io import BytesIO
import time
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import pytest

from jaguartv_factory.core import (
    assert_script_is_portuguese,
    brand_kit,
    connect_db,
    ingest_uploaded_media,
    load_config,
    now_iso,
    render_overlay_assets,
    terms_for_platform,
    write_srt,
)
from jaguartv_factory.dashboard import DashboardApplication, candidate_rows, skip_candidate
from jaguartv_factory.scoring import score_candidate_v2
from jaguartv_factory.server_store import (
    archive_review_package,
    complete_chunked_upload,
    find_upload,
    init_chunked_upload,
    list_uploads,
    save_upload_chunk,
)
from jaguartv_factory.sources import SourceError, XhsApiAdapter, YtDlpAdapter, get_adapter, yt_dlp_binary


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
    with pytest.raises(SourceError):
        adapter.search("足球", 3)
    with pytest.raises(SourceError):
        XhsApiAdapter("xiaohongshu", {}).search("足球", 3)
    with pytest.raises(SourceError):
        get_adapter("facebook", config).search("football", 1)
    assert get_adapter("tiktok", config).platform == "tiktok"
    with pytest.raises(SourceError):
        get_adapter("unknown-platform", config)


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


def test_brand_kit_and_endcard_render(tmp_path: Path):
    config = load_config(Path("config/pipeline.yaml"))
    kit = brand_kit(config)
    assert kit["_name"] == "jaguartv"
    assert kit["watermark"]["mode"] == "image"
    srt = tmp_path / "s.srt"
    write_srt("Primeira frase. Segunda frase!", 4.0, srt)
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


def test_source_upload_becomes_downloaded_candidate(tmp_path: Path, monkeypatch):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media"},
        "selection": {"max_source_duration_sec": 1800},
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


def test_inventory_exposes_dual_variant_outputs(tmp_path: Path):
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
    assert row["output_count"] == 2
    assert [asset["variant"] for asset in row["output_assets"]] == ["通用版", "FB版"]
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
