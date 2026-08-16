import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.dashboard import candidate_rows, save_review
from jaguartv_factory.publish_worker import publish_due_once
from jaguartv_factory.publisher import enqueue_approved_publication


def publishing_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "brand": {"default_cta": "https://copa.jarg.top/"},
        "tracking": {
            "base_url": "https://copa.jarg.top/baixar-o-app",
            "utm_medium": "organic_social",
            "campaign": "hoje_sports",
            "hook_version": "A",
        },
        "publishing": {
            "enabled": True,
            "timezone": "America/Sao_Paulo",
            "accounts": {
                "jaguartv_vivo": {
                    "platform": "youtube",
                    "label": "jaguartv vivo",
                    "channel_id": "UC8gCZXc5wbnjuZeco7qbKug",
                    "daily_limit": 3,
                    "schedule_times": ["11:00", "15:30", "19:00"],
                    "allowed_source_platforms": ["tiktok", "douyin", "facebook", "bilibili"],
                    "blocked_source_platforms": ["youtube"],
                    "content_tags": ["足球类"],
                    "default_privacy_status": "public",
                    "enabled": True,
                }
            },
        },
    }


def insert_candidate(
    config: dict,
    candidate_id: str,
    platform: str,
    status: str = "READY_FOR_REVIEW",
    keyword: str = "futebol Neymar",
    tags: list[str] | None = None,
) -> None:
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO candidates(
          id,platform,source_id,url,title,description,duration,view_count,
          detected_language,score,status,metadata_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate_id,
            platform,
            f"{candidate_id}-source",
            f"https://{platform}.example.test/video/{candidate_id}",
            keyword,
            "",
            20,
            100,
            "pt",
            80,
            status,
            json.dumps({"keyword": keyword, "content_tags": tags if tags is not None else ["足球类"]}, ensure_ascii=False),
            timestamp,
            timestamp,
        ),
    )
    connection.commit()


def write_review_package(
    config: dict,
    candidate_id: str,
    *,
    generic: bool = True,
    fb: bool = False,
    tags: list[str] | None = None,
) -> None:
    package = Path(config["_root"]) / "workspace" / "ready_for_review" / candidate_id
    package.mkdir(parents=True, exist_ok=True)
    if generic:
        (package / "0729-YouTube-1-通用版.mp4").write_bytes(b"generic-video")
    else:
        (package / "video.mp4").write_bytes(b"fallback-video")
    if fb:
        (package / "0729-YouTube-1-FB版.mp4").write_bytes(b"fb-video")
    (package / "metadata.json").write_text(
        json.dumps({
            "job_id": candidate_id,
            "content_tags": tags if tags is not None else ["足球类"],
            "source": {"platform": "tiktok", "title": "futebol Neymar"},
            "youtube": {"title": "Gol incrivel", "description": "Resumo em portugues."},
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def test_youtube_source_approved_is_blocked_from_youtube_queue(tmp_path: Path):
    config = publishing_config(tmp_path)
    insert_candidate(config, "yt-football", "youtube")
    write_review_package(config, "yt-football")

    result = save_review(config, {"candidate_id": "yt-football", "decision": "APPROVED", "reviewer": "tester"})

    assert result["publication"]["status"] == "BLOCKED"
    connection = connect_db(config)
    assert connection.execute("SELECT COUNT(*) count FROM publications").fetchone()["count"] == 0
    event = connection.execute("SELECT event_type FROM events ORDER BY id DESC LIMIT 1").fetchone()
    assert event["event_type"] == "PUBLISH_BLOCKED_SOURCE_PLATFORM"


def test_allowed_sources_approved_enter_jaguartv_vivo_queue(tmp_path: Path):
    for platform in ("tiktok", "douyin", "facebook", "bilibili"):
        config = publishing_config(tmp_path / platform)
        insert_candidate(config, f"{platform}-football", platform)
        write_review_package(config, f"{platform}-football")

        result = save_review(config, {"candidate_id": f"{platform}-football", "decision": "APPROVED", "reviewer": "tester"})

        assert result["publication"]["status"] == "SCHEDULED"
        connection = connect_db(config)
        row = connection.execute("SELECT account,source_platform,status FROM publications").fetchone()
        assert row["account"] == "jaguartv_vivo"
        assert row["source_platform"] == platform
        assert row["status"] == "SCHEDULED"


def test_non_football_approved_does_not_route(tmp_path: Path):
    config = publishing_config(tmp_path)
    insert_candidate(config, "music", "tiktok", keyword="novo hit musical", tags=[])
    package = Path(config["_root"]) / "workspace" / "ready_for_review" / "music"
    package.mkdir(parents=True, exist_ok=True)
    (package / "video.mp4").write_bytes(b"video")
    (package / "metadata.json").write_text(json.dumps({"job_id": "music"}), encoding="utf-8")

    result = save_review(config, {"candidate_id": "music", "decision": "APPROVED", "reviewer": "tester"})

    assert result["publication"]["status"] == "BLOCKED"
    assert result["publication"]["reason"] == "no publish route"


def test_repeated_approved_does_not_duplicate_queue(tmp_path: Path):
    config = publishing_config(tmp_path)
    insert_candidate(config, "tk-football", "tiktok")
    write_review_package(config, "tk-football")

    save_review(config, {"candidate_id": "tk-football", "decision": "APPROVED", "reviewer": "tester"})
    save_review(config, {"candidate_id": "tk-football", "decision": "APPROVED", "reviewer": "tester"})

    connection = connect_db(config)
    assert connection.execute("SELECT COUNT(*) count FROM publications").fetchone()["count"] == 1


def test_daily_limit_rolls_to_next_available_day(tmp_path: Path):
    config = publishing_config(tmp_path)
    insert_candidate(config, "next-day", "tiktok", status="APPROVED")
    write_review_package(config, "next-day")
    connection = connect_db(config)
    for index, scheduled_at in enumerate((
        "2026-08-15T11:00:00-03:00",
        "2026-08-15T15:30:00-03:00",
        "2026-08-15T19:00:00-03:00",
    )):
        connection.execute(
            """
            INSERT INTO publications(candidate_id,platform,account,scheduled_at,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (f"existing-{index}", "youtube", "jaguartv_vivo", scheduled_at, "SCHEDULED", now_iso(), now_iso()),
        )
    connection.commit()

    result = enqueue_approved_publication(
        config,
        "next-day",
        now=datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )

    assert result["scheduled_at"].startswith("2026-08-16T11:00:00")
    event_types = [
        row["event_type"] for row in connect_db(config).execute("SELECT event_type FROM events ORDER BY id").fetchall()
    ]
    assert "PUBLISH_BLOCKED_DAILY_LIMIT" in event_types


def test_youtube_publication_prefers_generic_over_fb_variant(tmp_path: Path):
    config = publishing_config(tmp_path)
    insert_candidate(config, "variant", "tiktok", status="APPROVED")
    write_review_package(config, "variant", generic=True, fb=True)

    result = enqueue_approved_publication(config, "variant")

    assert result["variant"] == "通用版"
    assert "通用版" in result["asset_id"]


def test_publish_success_updates_dashboard_candidate_row(tmp_path: Path):
    config = publishing_config(tmp_path)
    insert_candidate(config, "publish-me", "tiktok", status="APPROVED")
    write_review_package(config, "publish-me")
    result = enqueue_approved_publication(config, "publish-me")
    connection = connect_db(config)
    connection.execute(
        "UPDATE publications SET scheduled_at=? WHERE id=?",
        ("2026-08-15T10:00:00-03:00", result["publication_id"]),
    )
    connection.commit()

    def fake_uploader(_config: dict, publication_id: int) -> dict:
        return {
            "youtube_video_id": "yt123",
            "youtube_url": "https://www.youtube.com/watch?v=yt123",
            "published_at": "2026-08-15T11:00:00-03:00",
        }

    worker_result = publish_due_once(
        config,
        uploader=fake_uploader,
        now=datetime(2026, 8, 15, 11, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )

    assert worker_result["published"] == 1
    row = candidate_rows(config)[0]
    assert row["publication_state"]["status"] == "PUBLISHED"
    assert row["publication_state"]["account_label"] == "jaguartv vivo"
    assert row["publication_state"]["youtube_video_id"] == "yt123"
    assert row["publication_state"]["youtube_url"] == "https://www.youtube.com/watch?v=yt123"
