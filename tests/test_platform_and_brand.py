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
    candidate_id,
    connect_db,
    discover,
    ingest_uploaded_media,
    load_config,
    now_iso,
    produce_top,
    render_overlay_assets,
    terms_for_platform,
    write_srt,
)
from jaguartv_factory.dashboard import (
    DashboardApplication,
    candidate_rows,
    delete_candidates,
    download_claim_rows,
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

    def fake_http_download(url, destination, timeout=300, headers=None):
        destination.write_bytes(b"mp4")

    monkeypatch.setattr("jaguartv_factory.sources.yt_dlp_binary", lambda: "/usr/bin/yt-dlp")
    monkeypatch.setattr("jaguartv_factory.sources.run", fake_run)
    monkeypatch.setattr(
        "jaguartv_factory.browser_scraper.resolve_tiktok_video",
        lambda config, url: {
            "video_url": "https://v16-webapp-prime.tiktokcdn.com/video.mp4",
            "title": "TikTok Brasil",
        },
    )
    monkeypatch.setattr("jaguartv_factory.sources.http_download", fake_http_download)
    destination = tmp_path / "source.%(ext)s"

    adapter = YtDlpAdapter("tiktok", {"_root": str(tmp_path), "_workspace": "workspace"})
    adapter.download("https://www.tiktok.com/@demo/video/123", str(destination))

    assert (tmp_path / "source.mp4").read_bytes() == b"mp4"
    info = json.loads((tmp_path / "source.info.json").read_text(encoding="utf-8"))
    assert info["browser_fallback"]["title"] == "TikTok Brasil"


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


def test_produce_keeps_ready_status_when_partial_outputs_exist(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    insert_candidate(config, "partial-candidate", "DOWNLOADED")
    review = tmp_path / "workspace" / "ready_for_review" / "partial-candidate_part01"
    review.mkdir(parents=True)
    (review / "video.mp4").write_bytes(b"already-rendered")

    def fail_after_partial(*_args, **_kwargs):
        raise RuntimeError("later segment failed")

    monkeypatch.setattr("jaguartv_factory.core.produce_candidate", fail_after_partial)
    result = produce_top(config, 1, "partial-candidate")

    assert result == {"selected": 1, "produced": 0, "failed": 1}
    connection = connect_db(config)
    row = connection.execute("SELECT status FROM candidates WHERE id='partial-candidate'").fetchone()
    event = connection.execute(
        "SELECT event_type,payload_json FROM events WHERE candidate_id='partial-candidate' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "READY_FOR_REVIEW"
    assert event["event_type"] == "PRODUCTION_PARTIAL_FAILED"
    assert "later segment failed" in event["payload_json"]


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
    assert calls == [("download", "c1"), ("produce", "c1")]
