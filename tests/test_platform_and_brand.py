import json
import time
from pathlib import Path

import pytest

from jaguartv_factory.core import (
    assert_script_is_portuguese,
    brand_kit,
    connect_db,
    load_config,
    now_iso,
    render_overlay_assets,
    terms_for_platform,
    write_srt,
)
from jaguartv_factory.dashboard import candidate_rows, skip_candidate
from jaguartv_factory.scoring import score_candidate_v2
from jaguartv_factory.server_store import archive_review_package
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
    terms = {"en": ["football skills"], "zh-CN": ["巴西足球"]}
    config = {"sources": {}}
    assert terms_for_platform(terms, "youtube", config) == ["football skills"]
    assert terms_for_platform(terms, "douyin", config) == ["巴西足球"]
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
