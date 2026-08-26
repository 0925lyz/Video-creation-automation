from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.dashboard import DashboardApplication
from jaguartv_factory.publish_worker import publish_due_once
from jaguartv_factory.youtube_analytics import (
    ANALYTICS_SCOPE,
    COMPLETION_CALCULATION_VERSION,
    YouTubeAnalyticsClient,
    YouTubeApiError,
    analytics_accounts,
    analytics_history,
    analytics_ranking,
    analytics_summary,
    backfill_report,
    channel_import_status,
    date_filter_bounds,
    derive_completion_rate,
    import_channel_publications,
    parse_analytics_metrics,
    parse_data_api_video,
    publication_latest,
    restore_sync_tasks,
    retry_publication_sync,
    run_analytics_worker,
    run_channel_import_due,
    schedule_first_sync,
    set_backfill_status,
    store_metric_snapshot,
    sync_due_once,
)


SAO_PAULO = ZoneInfo("America/Sao_Paulo")


class FakeChannelImportClient:
    def __init__(self, channels: dict[str, dict], pages: dict[tuple[str, str], dict], videos: dict[str, dict], failures: set[str] | None = None):
        self.channels = channels
        self.pages = pages
        self.videos = videos
        self.failures = failures or set()

    def fetch_owned_channel(self, account: dict) -> dict:
        account_id = str(account["account"])
        if account_id in self.failures:
            raise YouTubeApiError("temporary failure", category="SERVER_ERROR", retryable=True)
        return self.channels[account_id]

    def fetch_uploads_page(self, account: dict, playlist_id: str, page_token: str = "") -> dict:
        return self.pages[(str(account["account"]), page_token)]

    def fetch_data_videos(self, account: dict, video_ids: list[str]) -> dict[str, dict]:
        return {video_id: self.videos[video_id] for video_id in video_ids if video_id in self.videos}


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict):
        self.payload = payload

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, payloads: list[dict]):
        self.payloads = list(payloads)
        self.requests: list[tuple[str, dict]] = []

    def get(self, url: str, *, params: dict, headers: dict, timeout: int) -> FakeResponse:
        self.requests.append((url, params))
        assert headers["Authorization"] == "Bearer fake-access-token"
        assert timeout == 30
        return FakeResponse(self.payloads.pop(0))


def config_for(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace", "timezone": "America/Sao_Paulo"},
        "youtube_analytics": {"poll_interval_minutes": 60, "batch_size": 50},
    }


def add_account(
    config: dict,
    account: str,
    channel_id: str,
    *,
    title: str | None = None,
    analytics_scope: bool = True,
) -> None:
    connection = connect_db(config)
    scopes = ["https://www.googleapis.com/auth/youtube.readonly"]
    if analytics_scope:
        scopes.append(ANALYTICS_SCOPE)
    connection.execute(
        """
        INSERT INTO youtube_channel_auths(
          account,channel_id,channel_title,scopes,encrypted_refresh_token,
          token_type,expires_in,authorized_at,updated_at,metadata_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            account,
            channel_id,
            title or account,
            " ".join(scopes),
            "encrypted-test-token",
            "Bearer",
            3600,
            "2026-07-01T00:00:00+00:00",
            "2026-07-01T00:00:00+00:00",
            "{}",
        ),
    )
    connection.commit()


def add_publication(
    config: dict,
    publication_id: int,
    *,
    account: str = "account-a",
    channel_id: str = "channel-a",
    video_id: str | None = None,
    published_at: str = "2026-07-01T12:00:00+00:00",
    status: str = "PUBLISHED",
    public_status: str = "public",
    source_platform: str = "facebook",
    category: str | None = "futebol",
    keyword: str | None = "neymar hoje",
    title: str | None = None,
) -> None:
    connection = connect_db(config)
    vid = video_id or f"video-{publication_id}"
    local = datetime.fromisoformat(published_at).astimezone(SAO_PAULO).isoformat()
    connection.execute(
        """
        INSERT INTO candidates(
          id,platform,source_id,url,title,description,duration,view_count,
          detected_language,score,status,metadata_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"candidate-{publication_id}",
            source_platform,
            f"source-{publication_id}",
            f"https://example.test/{publication_id}",
            title or f"Video {publication_id}",
            "",
            30,
            0,
            "pt",
            80,
            "APPROVED",
            json.dumps({"initial_category": category, "keyword": keyword}),
            published_at,
            published_at,
        ),
    )
    connection.execute(
        """
        INSERT INTO publications(
          id,candidate_id,platform,account,account_label,channel_id,published_at,status,
          title,source_platform,privacy_status,public_status,youtube_video_id,
          platform_video_id,youtube_url,public_url,post_url,platform_account_id,
          platform_username_snapshot,published_local_at,authorized_account_id,
          source_category,source_keyword,slice_id,version_id,publish_task_id,
          thumbnail_url,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            publication_id,
            f"candidate-{publication_id}",
            "youtube",
            account,
            f"Snapshot {account}",
            channel_id,
            published_at,
            status,
            title or f"Video {publication_id}",
            source_platform,
            "public",
            public_status,
            vid,
            vid,
            f"https://www.youtube.com/watch?v={vid}",
            f"https://www.youtube.com/watch?v={vid}",
            f"https://www.youtube.com/watch?v={vid}",
            account,
            f"Snapshot {account}",
            local,
            account,
            category or "unknown",
            keyword or "unknown",
            f"slice-{publication_id}",
            "v1",
            f"publish-{publication_id}",
            f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            published_at,
            published_at,
        ),
    )
    connection.commit()


def add_snapshot(
    config: dict,
    publication_id: int,
    *,
    fetched_at: str,
    views: int | None = 100,
    likes: int | None = 10,
    comments: int | None = 5,
    shares: int | None = 2,
    analytics_views: int | None = 80,
    average_view_duration: float | None = 20,
    average_view_percentage: float | None = 50,
    completion_rate: float | None = 40,
    completion_denominator: int | None = 80,
    sync_window: str | None = None,
) -> int:
    return store_metric_snapshot(
        config,
        publication_id,
        {
            "view_count": views,
            "like_count": likes,
            "comment_count": comments,
            "share_count": shares,
            "analytics_views": analytics_views,
            "average_view_duration": average_view_duration,
            "average_view_percentage": average_view_percentage,
            "completion_rate": completion_rate,
            "completion_raw_ratio": None if completion_rate is None else completion_rate / 100,
            "completion_bucket_ratio": None if completion_rate is None else 0.99,
            "completion_calculation_version": COMPLETION_CALCULATION_VERSION,
            "completion_weight_views": completion_denominator,
            "fetched_at": fetched_at,
            "data_through_date": fetched_at[:10],
            "data_api_source": "youtube_data_api_v3",
            "analytics_api_source": "youtube_analytics_api_v2",
            "retention_source": "youtube_analytics_audience_retention",
            "api_response_status": "SUCCESS",
            "sync_window": sync_window or fetched_at[:13] + ":00:00+00:00",
        },
    )


def test_schema_is_additive_and_nullable(tmp_path: Path):
    config = config_for(tmp_path)
    connection = connect_db(config)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "youtube_metric_snapshots", "youtube_sync_states", "youtube_backfill_runs",
        "youtube_channel_import_states", "youtube_channel_video_imports",
    } <= tables
    snapshot_columns = {row[1]: row for row in connection.execute("PRAGMA table_info(youtube_metric_snapshots)")}
    assert snapshot_columns["view_count"][3] == 0
    assert snapshot_columns["completion_rate"][3] == 0
    assert snapshot_columns["sync_stage"][4] == "''"
    sync_columns = {row[1]: row for row in connection.execute("PRAGMA table_info(youtube_sync_states)")}
    assert sync_columns["next_sync_stage"][4] == "''"
    assert sync_columns["schedule_version"][4] == "''"
    publication_columns = {row[1]: row for row in connection.execute("PRAGMA table_info(publications)")}
    assert publication_columns["publication_origin"][4] == "'SYSTEM_AUTO_PUBLISH'"


def test_channel_import_dry_run_filters_nonpublic_and_never_writes(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a", title="Current A")
    client = FakeChannelImportClient(
        channels={"account-a": {
            "channel_id": "channel-a", "channel_title": "Current A",
            "uploads_playlist_id": "uploads-a", "public_video_count": 2,
        }},
        pages={
            ("account-a", ""): {"video_ids": ["public-1", "private-1"], "next_page_token": "page-2"},
            ("account-a", "page-2"): {"video_ids": ["public-2"], "next_page_token": ""},
        },
        videos={
            "public-1": {"video_id": "public-1", "channel_id": "channel-a", "current_channel_title": "Current A", "title": "Public One", "description": "", "published_at": "2026-07-01T12:00:00Z", "privacy_status": "public", "thumbnail_url": "https://img.test/1.jpg"},
            "private-1": {"video_id": "private-1", "channel_id": "channel-a", "current_channel_title": "Current A", "title": "Private", "description": "", "published_at": "2026-07-01T13:00:00Z", "privacy_status": "private", "thumbnail_url": ""},
            "public-2": {"video_id": "public-2", "channel_id": "channel-a", "current_channel_title": "Current A", "title": "Public Two", "description": "", "published_at": "2026-07-03T12:00:00Z", "privacy_status": "public", "thumbnail_url": "https://img.test/2.jpg"},
        },
    )

    report = import_channel_publications(
        config, account_id="account-a", client=client, dry_run=True,
        now=datetime(2026, 7, 5, 12, tzinfo=timezone.utc),
    )

    assert report["public_video_count"] == 2
    assert report["skipped_nonpublic_count"] == 1
    assert report["pages"] == 2
    connection = connect_db(config)
    assert connection.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM youtube_channel_video_imports").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM youtube_channel_import_states").fetchone()[0] == 0


def test_channel_import_is_idempotent_auditable_and_schedules_immediate_data_then_analytics_after_24h(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a", title="Current A")
    client = FakeChannelImportClient(
        channels={"account-a": {
            "channel_id": "channel-a", "channel_title": "Current A",
            "uploads_playlist_id": "uploads-a", "public_video_count": 2,
        }},
        pages={("account-a", ""): {"video_ids": ["old-video", "new-video"], "next_page_token": ""}},
        videos={
            "old-video": {"video_id": "old-video", "channel_id": "channel-a", "current_channel_title": "Current A", "title": "Old", "description": "", "published_at": "2026-07-01T10:00:00Z", "privacy_status": "public", "thumbnail_url": "https://img.test/old.jpg"},
            "new-video": {"video_id": "new-video", "channel_id": "channel-a", "current_channel_title": "Current A", "title": "New", "description": "", "published_at": "2026-07-05T06:00:00Z", "privacy_status": "public", "thumbnail_url": "https://img.test/new.jpg"},
        },
    )
    now = datetime(2026, 7, 5, 12, tzinfo=timezone.utc)

    first = import_channel_publications(config, account_id="account-a", client=client, dry_run=False, now=now)
    second = import_channel_publications(config, account_id="account-a", client=client, dry_run=False, now=now)

    assert first["imported_count"] == 2
    assert second["imported_count"] == 0
    connection = connect_db(config)
    publications = connection.execute("SELECT * FROM publications ORDER BY youtube_video_id").fetchall()
    assert len(publications) == 2
    assert {row["publication_origin"] for row in publications} == {"YOUTUBE_CHANNEL_IMPORT"}
    assert {row["source_category"] for row in publications} == {"unknown"}
    assert {row["source_keyword"] for row in publications} == {"unknown"}
    assert {row["authorized_account_id"] for row in publications} == {"account-a"}
    states = {row["youtube_video_id"]: connection.execute(
        "SELECT * FROM youtube_sync_states WHERE publication_id=?", (row["id"],)
    ).fetchone() for row in publications}
    assert states["old-video"]["first_sync_due_at"] == "2026-07-01T22:00:00+00:00"
    assert states["old-video"]["next_sync_stage"] == "3d"
    assert states["old-video"]["next_sync_at"] == "2026-07-04T10:00:00+00:00"
    assert states["new-video"]["first_sync_due_at"] == "2026-07-05T18:00:00+00:00"
    assert states["new-video"]["next_sync_stage"] == "12h"
    assert states["new-video"]["next_sync_at"] == "2026-07-05T18:00:00+00:00"
    assert connection.execute("SELECT COUNT(*) FROM youtube_channel_video_imports").fetchone()[0] == 2
    status = channel_import_status(config)
    assert status[0]["sync_status"] == "SUCCESS"
    assert status[0]["next_scan_at"] == "2026-07-05T13:00:00+00:00"


def test_incremental_channel_import_scans_past_known_video_and_imports_older_long_video(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, video_id="known-video")
    client = FakeChannelImportClient(
        channels={"account-a": {"channel_id": "channel-a", "channel_title": "A", "uploads_playlist_id": "uploads-a", "public_video_count": 2}},
        pages={
            ("account-a", ""): {"video_ids": ["known-video"], "next_page_token": "page-2"},
            ("account-a", "page-2"): {"video_ids": ["older-long-video"], "next_page_token": ""},
        },
        videos={"older-long-video": {
            "video_id": "older-long-video", "channel_id": "channel-a", "current_channel_title": "A",
            "title": "Long form upload", "description": "", "published_at": "2026-06-01T12:00:00Z",
            "privacy_status": "public", "thumbnail_url": "", "duration_seconds": 7200,
        }},
    )
    connection = connect_db(config)
    connection.execute(
        "INSERT INTO youtube_channel_import_states(account_id,channel_id,last_successful_at,sync_status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        ("account-a", "channel-a", "2026-07-05T00:00:00+00:00", "SUCCESS", "2026-07-05T00:00:00+00:00", "2026-07-05T00:00:00+00:00"),
    )
    connection.commit()

    report = import_channel_publications(config, account_id="account-a", client=client, dry_run=False, now=datetime(2026, 7, 5, 1, tzinfo=timezone.utc))

    assert report["pages"] == 2
    assert report["imported_count"] == 1
    rows = connect_db(config).execute("SELECT youtube_video_id FROM publications ORDER BY id").fetchall()
    assert [row[0] for row in rows] == ["known-video", "older-long-video"]


def test_channel_import_reuses_system_publication_without_changing_origin(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, video_id="system-video")
    client = FakeChannelImportClient(
        channels={"account-a": {"channel_id": "channel-a", "channel_title": "Current A", "uploads_playlist_id": "uploads-a", "public_video_count": 1}},
        pages={("account-a", ""): {"video_ids": ["system-video"], "next_page_token": ""}},
        videos={"system-video": {"video_id": "system-video", "channel_id": "channel-a", "current_channel_title": "Current A", "title": "Current title", "description": "", "published_at": "2026-07-01T12:00:00Z", "privacy_status": "public", "thumbnail_url": ""}},
    )

    report = import_channel_publications(config, account_id="account-a", client=client, dry_run=False)

    connection = connect_db(config)
    assert report["imported_count"] == 0
    assert connection.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 1
    assert connection.execute("SELECT publication_origin FROM publications WHERE id=1").fetchone()[0] == "SYSTEM_AUTO_PUBLISH"
    assert connection.execute("SELECT publication_id FROM youtube_channel_video_imports").fetchone()[0] == 1


def test_due_channel_import_isolates_account_failures_and_recovers_from_state(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_account(config, "account-b", "channel-b")
    client = FakeChannelImportClient(
        channels={"account-b": {"channel_id": "channel-b", "channel_title": "B", "uploads_playlist_id": "uploads-b", "public_video_count": 1}},
        pages={("account-b", ""): {"video_ids": ["video-b"], "next_page_token": ""}},
        videos={"video-b": {"video_id": "video-b", "channel_id": "channel-b", "current_channel_title": "B", "title": "B", "description": "", "published_at": "2026-07-01T12:00:00Z", "privacy_status": "public", "thumbnail_url": ""}},
        failures={"account-a"},
    )

    result = run_channel_import_due(
        config, client=client, now=datetime(2026, 7, 5, 12, tzinfo=timezone.utc), limit_accounts=8,
    )

    assert result["accounts_due"] == 2
    assert result["accounts_successful"] == 1
    assert result["accounts_failed"] == 1
    statuses = {row["account_id"]: row for row in channel_import_status(config)}
    assert statuses["account-a"]["sync_status"] == "RETRY"
    assert statuses["account-b"]["sync_status"] == "SUCCESS"
    assert connect_db(config).execute("SELECT COUNT(*) FROM publications WHERE authorized_account_id='account-b'").fetchone()[0] == 1


def test_channel_import_continues_when_only_analytics_scope_is_missing(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    connection = connect_db(config)
    connection.execute("UPDATE youtube_channel_auths SET status='ANALYTICS_SCOPE_MISSING' WHERE account='account-a'")
    connection.commit()
    client = FakeChannelImportClient(
        channels={"account-a": {"channel_id": "channel-a", "channel_title": "A", "uploads_playlist_id": "uploads-a", "public_video_count": 1}},
        pages={("account-a", ""): {"video_ids": ["video-a"], "next_page_token": ""}},
        videos={"video-a": {"video_id": "video-a", "channel_id": "channel-a", "current_channel_title": "A", "title": "A", "description": "", "published_at": "2026-07-01T12:00:00Z", "privacy_status": "public", "thumbnail_url": ""}},
    )

    result = run_channel_import_due(config, client=client, now=datetime(2026, 7, 5, tzinfo=timezone.utc))

    assert result["accounts_successful"] == 1
    assert connect_db(config).execute("SELECT COUNT(*) FROM publications WHERE authorized_account_id='account-a'").fetchone()[0] == 1


def test_analytics_worker_scans_channels_before_metric_tasks(tmp_path: Path, monkeypatch, capsys):
    config = config_for(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        "jaguartv_factory.youtube_analytics.run_channel_import_due",
        lambda _config, limit_accounts=8: calls.append("channel_import") or {"accounts_due": 0},
    )
    monkeypatch.setattr(
        "jaguartv_factory.youtube_analytics.sync_due_once",
        lambda _config, limit=50: calls.append("metrics") or {"due": 0},
    )

    run_analytics_worker(config, once=True, limit=50)

    assert calls == ["channel_import", "metrics"]
    output = json.loads(capsys.readouterr().out)
    assert output == {"channel_import": {"accounts_due": 0}, "metrics": {"due": 0}}


def test_migration_is_idempotent_and_rollback_keeps_snapshots(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1)
    add_snapshot(config, 1, fetched_at="2026-07-03T12:00:00+00:00", views=321)
    db_path = tmp_path / "workspace" / "factory.db"
    backup_path = tmp_path / "backup" / "factory.db"
    command = [
        str(Path(__file__).parents[1] / ".venv" / "bin" / "python"),
        str(Path(__file__).parents[1] / "scripts" / "migrate_db.py"),
        str(db_path),
        "--backup",
        str(backup_path),
        "--verify-rollback",
    ]

    first = subprocess.run(command, check=True, text=True, capture_output=True)
    second = subprocess.run(command, check=True, text=True, capture_output=True)

    assert json.loads(first.stdout)["rollback_verified"] is True
    assert json.loads(second.stdout)["integrity"] == "ok"
    assert backup_path.exists()
    snapshot = sqlite3.connect(db_path).execute(
        "SELECT view_count FROM youtube_metric_snapshots WHERE publication_id=1"
    ).fetchone()
    assert snapshot == (321,)


def test_publication_first_sync_is_due_at_12_hours_and_restart_recovers_task(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, published_at="2026-07-01T12:00:00+00:00")
    schedule_first_sync(config, 1)

    before_first_due = restore_sync_tasks(config, now=datetime(2026, 7, 1, 23, 59, tzinfo=timezone.utc))
    at_first_due = restore_sync_tasks(config, now=datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc))

    assert before_first_due == []
    assert [row["publication_id"] for row in at_first_due] == [1]
    state = connect_db(config).execute("SELECT * FROM youtube_sync_states WHERE publication_id=1").fetchone()
    assert state["first_sync_due_at"] == "2026-07-02T00:00:00+00:00"
    assert state["next_sync_at"] == "2026-07-02T00:00:00+00:00"
    assert state["next_sync_stage"] == "12h"


def test_legacy_pending_tasks_are_migrated_to_four_checkpoint_schedule(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, published_at="2026-07-05T10:00:00+00:00")
    schedule_first_sync(config, 1)
    connection = connect_db(config)
    connection.execute("UPDATE youtube_sync_states SET schedule_version='',next_sync_stage='',first_sync_due_at='2026-07-06T10:00:00+00:00',next_sync_at='2026-07-06T10:00:00+00:00' WHERE publication_id=1")
    connection.commit()

    due = restore_sync_tasks(config, now=datetime(2026, 7, 5, 12, tzinfo=timezone.utc))

    state = connection.execute("SELECT * FROM youtube_sync_states WHERE publication_id=1").fetchone()
    assert due == []
    assert state["first_sync_due_at"] == "2026-07-05T22:00:00+00:00"
    assert state["next_sync_stage"] == "12h"
    assert state["next_sync_at"] == "2026-07-05T22:00:00+00:00"


def test_private_publication_never_creates_sync_task(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, public_status="private")

    with pytest.raises(ValueError, match="public"):
        schedule_first_sync(config, 1)

    assert connect_db(config).execute(
        "SELECT count(*) FROM youtube_sync_states"
    ).fetchone()[0] == 0


def test_date_filter_bounds_use_sao_paulo_and_inclusive_end_day():
    start, end = date_filter_bounds("custom", "2026-07-01", "2026-07-02", now=datetime(2026, 7, 20, tzinfo=SAO_PAULO))
    assert start == "2026-07-01T00:00:00-03:00"
    assert end == "2026-07-03T00:00:00-03:00"

    seven_start, seven_end = date_filter_bounds("7d", now=datetime(2026, 7, 20, 15, tzinfo=SAO_PAULO))
    thirty_start, _ = date_filter_bounds("30d", now=datetime(2026, 7, 20, 15, tzinfo=SAO_PAULO))
    all_start, all_end = date_filter_bounds("all", now=datetime(2026, 7, 20, 15, tzinfo=SAO_PAULO))
    assert seven_start.startswith("2026-07-14T00:00:00") and seven_end.startswith("2026-07-21T00:00:00")
    assert thirty_start.startswith("2026-06-21T00:00:00")
    assert all_start is None and all_end is None


def test_data_and_analytics_mapping_preserve_missing_values():
    mapped = parse_data_api_video({
        "id": "abc",
        "snippet": {"channelId": "channel-a", "title": "Demo"},
        "status": {"privacyStatus": "public"},
        "statistics": {"viewCount": "12", "likeCount": "3"},
    })
    analytics = parse_analytics_metrics({
        "columnHeaders": [
            {"name": "video"}, {"name": "views"}, {"name": "shares"},
            {"name": "averageViewDuration"}, {"name": "averageViewPercentage"},
        ],
        "rows": [["abc", 10, 2, 12.5, 41.2]],
    }, "abc")
    assert mapped["view_count"] == 12
    assert mapped["like_count"] == 3
    assert mapped["comment_count"] is None
    assert analytics == {
        "analytics_views": 10,
        "share_count": 2,
        "average_view_duration": 12.5,
        "average_view_percentage": 41.2,
    }


def test_official_channel_and_upload_playlist_responses_are_mapped():
    session = FakeSession([
        {"items": [{
            "id": "channel-a",
            "snippet": {"title": "Channel A"},
            "contentDetails": {"relatedPlaylists": {"uploads": "uploads-a"}},
            "statistics": {"videoCount": "2"},
        }]},
        {
            "items": [
                {"contentDetails": {"videoId": "video-1"}},
                {"contentDetails": {"videoId": "video-2"}},
            ],
            "nextPageToken": "next-page",
        },
    ])
    client = YouTubeAnalyticsClient(
        {}, session=session,
        token_provider=lambda _config, _account: {"access_token": "fake-access-token"},
    )
    account = {"account": "account-a"}

    channel = client.fetch_owned_channel(account)
    page = client.fetch_uploads_page(account, "uploads-a")

    assert channel == {
        "channel_id": "channel-a",
        "channel_title": "Channel A",
        "uploads_playlist_id": "uploads-a",
        "public_video_count": 2,
    }
    assert page == {"video_ids": ["video-1", "video-2"], "next_page_token": "next-page"}
    assert session.requests[0][1]["mine"] == "true"
    assert session.requests[1][1]["maxResults"] == "50"


def test_completion_uses_last_retention_bucket_and_preserves_over_one():
    result = derive_completion_rate({
        "columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}],
        "rows": [[0.5, 0.8], [0.99, 1.15], [0.9, 0.6]],
    })
    assert result == {
        "completion_rate": 115.0,
        "completion_raw_ratio": 1.15,
        "completion_bucket_ratio": 0.99,
        "completion_calculation_version": COMPLETION_CALCULATION_VERSION,
    }
    assert derive_completion_rate({"rows": []})["completion_rate"] is None


def test_snapshot_is_idempotent_per_window_and_keeps_time_series(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1)
    first = add_snapshot(config, 1, fetched_at="2026-07-03T12:10:00+00:00", views=10)
    duplicate = add_snapshot(config, 1, fetched_at="2026-07-03T12:20:00+00:00", views=11)
    second_window = add_snapshot(config, 1, fetched_at="2026-07-03T13:01:00+00:00", views=20)

    rows = connect_db(config).execute(
        "SELECT id,view_count FROM youtube_metric_snapshots WHERE publication_id=1 ORDER BY fetched_at"
    ).fetchall()
    assert first == duplicate
    assert second_window != first
    assert [(row["id"], row["view_count"]) for row in rows] == [(first, 11), (second_window, 20)]


def test_summary_uses_latest_snapshots_weighted_analytics_denominators_and_nulls(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_account(config, "account-b", "channel-b")
    add_publication(config, 1, account="account-a", channel_id="channel-a")
    add_publication(config, 2, account="account-b", channel_id="channel-b")
    add_publication(config, 3, account="account-b", channel_id="channel-b")
    add_snapshot(config, 1, fetched_at="2026-07-03T12:00:00+00:00", views=100, likes=10, comments=5, shares=4, analytics_views=80, average_view_duration=10, completion_rate=25, completion_denominator=80)
    add_snapshot(config, 1, fetched_at="2026-07-03T13:00:00+00:00", views=120, likes=12, comments=6, shares=5, analytics_views=100, average_view_duration=20, completion_rate=50, completion_denominator=100)
    add_snapshot(config, 2, fetched_at="2026-07-03T12:00:00+00:00", views=80, likes=8, comments=4, shares=3, analytics_views=20, average_view_duration=40, completion_rate=100, completion_denominator=20)
    add_snapshot(config, 3, fetched_at="2026-07-03T12:00:00+00:00", views=None, likes=None, comments=None, shares=None, analytics_views=None, average_view_duration=None, completion_rate=None, completion_denominator=None)

    summary = analytics_summary(config, range_name="all")
    assert summary["video_count"] == 3
    assert summary["account_count"] == 2
    assert (summary["view_count"], summary["like_count"], summary["comment_count"], summary["share_count"]) == (200, 20, 10, 8)
    assert summary["average_view_duration"] == pytest.approx((20 * 100 + 40 * 20) / 120)
    assert summary["completion_rate"] == pytest.approx((50 * 100 + 100 * 20) / 120)
    assert summary["missing_video_count"] == 1


@pytest.mark.parametrize("metric", ["views", "comments", "likes", "average_view_duration", "completion_rate", "shares", "published_at"])
def test_all_ranking_sorts_are_server_side_with_nulls_last(tmp_path: Path, metric: str):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, published_at="2026-07-01T12:00:00+00:00")
    add_publication(config, 2, published_at="2026-07-02T12:00:00+00:00")
    add_publication(config, 3, published_at="2026-07-03T12:00:00+00:00")
    add_snapshot(config, 1, fetched_at="2026-07-04T12:00:00+00:00", views=10, likes=10, comments=10, shares=10, average_view_duration=10, completion_rate=10)
    add_snapshot(config, 2, fetched_at="2026-07-04T12:00:00+00:00", views=10, likes=10, comments=10, shares=10, average_view_duration=10, completion_rate=10)
    add_snapshot(config, 3, fetched_at="2026-07-04T12:00:00+00:00", views=None, likes=None, comments=None, shares=None, average_view_duration=None, completion_rate=None)

    page = analytics_ranking(config, range_name="all", metric=metric, page=1, page_size=2)
    expected_first_page = [3, 2] if metric == "published_at" else [2, 1]
    assert [item["publication_id"] for item in page["items"]] == expected_first_page
    assert page["total"] == 3 and page["pages"] == 2
    last = analytics_ranking(config, range_name="all", metric=metric, page=2, page_size=2)
    assert [item["publication_id"] for item in last["items"]] == ([1] if metric == "published_at" else [3])


def test_account_filter_and_accounts_are_isolated(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a", title="Current A")
    add_account(config, "account-b", "channel-b", title="Current B")
    add_publication(config, 1, account="account-a", channel_id="channel-a")
    add_publication(config, 2, account="account-b", channel_id="channel-b")
    add_snapshot(config, 1, fetched_at="2026-07-03T12:00:00+00:00", views=10)
    add_snapshot(config, 2, fetched_at="2026-07-03T12:00:00+00:00", views=20)

    assert analytics_summary(config, range_name="all", account_id="account-a")["view_count"] == 10
    assert [row["account_id"] for row in analytics_accounts(config)] == ["account-a", "account-b"]
    assert analytics_accounts(config)[0]["current_channel_title"] == "Current A"


class FakeClient:
    def __init__(self, failures: dict[str, Exception] | None = None):
        self.failures = failures or {}
        self.data_calls: list[tuple[str, list[str]]] = []

    def fetch_data_videos(self, account: dict, video_ids: list[str]) -> dict[str, dict]:
        account_id = account["account"]
        if account_id in self.failures:
            raise self.failures[account_id]
        self.data_calls.append((account_id, video_ids))
        return {
            video_id: {
                "video_id": video_id,
                "channel_id": account["channel_id"],
                "current_channel_title": account["channel_title"],
                "privacy_status": "public",
                "view_count": 100,
                "like_count": 10,
                "comment_count": None,
                "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            }
            for video_id in video_ids
        }

    def fetch_analytics(self, account: dict, video_id: str, start_date: str, end_date: str) -> dict:
        return {"analytics_views": 80, "share_count": 4, "average_view_duration": 12.5, "average_view_percentage": 50.0}

    def fetch_retention(self, account: dict, video_id: str, start_date: str, end_date: str) -> dict:
        return {
            "completion_rate": 75.0,
            "completion_raw_ratio": 0.75,
            "completion_bucket_ratio": 0.99,
            "completion_calculation_version": COMPLETION_CALCULATION_VERSION,
        }


def test_12h_video_syncs_data_api_without_analytics_calls_and_schedules_24h(tmp_path: Path):
    class CountingClient(FakeClient):
        analytics_calls = 0
        retention_calls = 0

        def fetch_analytics(self, account: dict, video_id: str, start_date: str, end_date: str) -> dict:
            self.analytics_calls += 1
            return super().fetch_analytics(account, video_id, start_date, end_date)

        def fetch_retention(self, account: dict, video_id: str, start_date: str, end_date: str) -> dict:
            self.retention_calls += 1
            return super().fetch_retention(account, video_id, start_date, end_date)

    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, published_at="2026-07-05T10:00:00+00:00")
    state = schedule_first_sync(config, 1)

    assert state["first_sync_due_at"] == "2026-07-05T22:00:00+00:00"
    assert state["next_sync_at"] == "2026-07-05T22:00:00+00:00"

    client = CountingClient()
    result = sync_due_once(
        config,
        client=client,
        now=datetime(2026, 7, 5, 22, tzinfo=timezone.utc),
    )

    connection = connect_db(config)
    snapshot = connection.execute("SELECT * FROM youtube_metric_snapshots WHERE publication_id=1").fetchone()
    state = connection.execute("SELECT * FROM youtube_sync_states WHERE publication_id=1").fetchone()
    assert result["successful"] == 1
    assert client.data_calls == [("account-a", ["video-1"])]
    assert client.analytics_calls == 0
    assert client.retention_calls == 0
    assert snapshot["view_count"] == 100
    assert snapshot["like_count"] == 10
    assert snapshot["share_count"] is None
    assert snapshot["average_view_duration"] is None
    assert snapshot["completion_rate"] is None
    assert snapshot["data_through_date"] is None
    assert snapshot["api_response_status"] == "PARTIAL_ANALYTICS_PENDING_24H"
    assert snapshot["sync_stage"] == "12h"
    assert state["sync_status"] == "PARTIAL"
    assert state["last_completed_stage"] == "12h"
    assert state["next_sync_stage"] == "24h"
    assert state["next_sync_at"] == "2026-07-06T10:00:00+00:00"


def test_four_checkpoint_syncs_are_idempotent_and_stop_after_day_7(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, published_at="2026-07-01T00:00:00+00:00")
    schedule_first_sync(config, 1)
    client = FakeClient()

    for current in (
        datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
        datetime(2026, 7, 2, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 4, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 8, 0, tzinfo=timezone.utc),
    ):
        assert sync_due_once(config, client=client, now=current)["successful"] == 1

    connection = connect_db(config)
    snapshots = connection.execute("SELECT sync_stage,scheduled_for FROM youtube_metric_snapshots WHERE publication_id=1 ORDER BY fetched_at").fetchall()
    assert [row["sync_stage"] for row in snapshots] == ["12h", "24h", "3d", "7d"]
    assert [row["scheduled_for"] for row in snapshots] == [
        "2026-07-01T12:00:00+00:00", "2026-07-02T00:00:00+00:00",
        "2026-07-04T00:00:00+00:00", "2026-07-08T00:00:00+00:00",
    ]
    state = connection.execute("SELECT * FROM youtube_sync_states WHERE publication_id=1").fetchone()
    assert state["sync_status"] == "COMPLETE"
    assert state["last_completed_stage"] == "7d"
    assert state["next_sync_stage"] == ""
    assert state["next_sync_at"] is None
    assert sync_due_once(config, client=client, now=datetime(2026, 7, 9, tzinfo=timezone.utc))["due"] == 0
    assert connection.execute("SELECT COUNT(*) FROM youtube_metric_snapshots WHERE publication_id=1").fetchone()[0] == 4


def test_historical_video_starts_at_latest_due_checkpoint_without_fabricating_earlier_snapshots(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, published_at="2026-07-01T00:00:00+00:00")

    state = schedule_first_sync(config, 1, now=datetime(2026, 7, 5, tzinfo=timezone.utc))

    assert state["next_sync_stage"] == "3d"
    assert state["next_sync_at"] == "2026-07-04T00:00:00+00:00"


def test_sync_batches_per_account_and_one_account_failure_does_not_block_another(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_account(config, "account-b", "channel-b")
    add_publication(config, 1, account="account-a", channel_id="channel-a")
    add_publication(config, 2, account="account-a", channel_id="channel-a")
    add_publication(config, 3, account="account-b", channel_id="channel-b")
    for publication_id in (1, 2, 3):
        schedule_first_sync(config, publication_id)
    client = FakeClient({"account-a": YouTubeApiError("quota", status_code=429, category="QUOTA_EXHAUSTED", retryable=True)})

    result = sync_due_once(config, client=client, now=datetime(2026, 7, 3, tzinfo=timezone.utc))

    assert result["successful"] == 1 and result["failed"] == 2
    assert client.data_calls == [("account-b", ["video-3"])]
    states = {
        row["publication_id"]: dict(row)
        for row in connect_db(config).execute("SELECT * FROM youtube_sync_states ORDER BY publication_id")
    }
    assert states[1]["last_error_category"] == "QUOTA_EXHAUSTED"
    assert states[1]["retry_count"] == 1
    assert states[3]["sync_status"] == "SUCCESS"


@pytest.mark.parametrize(
    ("error", "expected_status", "retryable"),
    [
        (YouTubeApiError("forbidden", status_code=403, category="FORBIDDEN", retryable=False), "BLOCKED", False),
        (YouTubeApiError("rate", status_code=429, category="RATE_LIMIT", retryable=True), "RETRY", True),
        (YouTubeApiError("server", status_code=503, category="SERVER_ERROR", retryable=True), "RETRY", True),
        (YouTubeApiError("timeout", category="NETWORK_TIMEOUT", retryable=True), "RETRY", True),
        (YouTubeApiError("revoked", status_code=400, category="AUTH_REVOKED", retryable=False), "NEEDS_REAUTH", False),
        (YouTubeApiError("decrypt", category="AUTH_DECRYPT_FAILED", retryable=False), "NEEDS_REAUTH", False),
        (YouTubeApiError("scope", status_code=403, category="ANALYTICS_SCOPE_MISSING", retryable=False), "BLOCKED", False),
    ],
)
def test_sync_error_categories_control_retry(error: YouTubeApiError, expected_status: str, retryable: bool, tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1)
    schedule_first_sync(config, 1)
    sync_due_once(config, client=FakeClient({"account-a": error}), now=datetime(2026, 7, 3, tzinfo=timezone.utc))

    state = connect_db(config).execute("SELECT * FROM youtube_sync_states WHERE publication_id=1").fetchone()
    assert state["sync_status"] == expected_status
    assert (state["next_sync_at"] is not None) is retryable
    assert "token" not in state["last_error_summary"].lower()


def test_deleted_private_and_comments_missing_are_not_faked(tmp_path: Path):
    assert parse_data_api_video(None)["api_response_status"] == "VIDEO_UNAVAILABLE"
    private = parse_data_api_video({"id": "abc", "snippet": {"channelId": "channel-a"}, "status": {"privacyStatus": "private"}, "statistics": {}})
    assert private["api_response_status"] == "NOT_PUBLIC"
    assert private["comment_count"] is None


def test_history_returns_time_series_and_retry_is_safe(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1)
    schedule_first_sync(config, 1)
    add_snapshot(config, 1, fetched_at="2026-07-03T12:00:00+00:00", views=10)
    add_snapshot(config, 1, fetched_at="2026-07-03T13:00:00+00:00", views=20)

    assert [row["view_count"] for row in analytics_history(config, 1, limit=10)] == [10, 20]
    retried = retry_publication_sync(config, 1, now=datetime(2026, 7, 4, tzinfo=timezone.utc))
    assert retried["sync_status"] == "PENDING"
    assert retried["next_sync_at"] == "2026-07-04T00:00:00+00:00"


def test_backfill_dry_run_only_system_publications_and_never_writes(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, category=None, keyword=None)
    add_publication(config, 2, status="FAILED")
    add_publication(config, 3, public_status="private")
    before = connect_db(config).execute("SELECT count(*) FROM youtube_sync_states").fetchone()[0]

    report = backfill_report(config, now=datetime(2026, 7, 5, tzinfo=timezone.utc), dry_run=True)
    after = connect_db(config).execute("SELECT count(*) FROM youtube_sync_states").fetchone()[0]

    assert before == after == 0
    assert report["dry_run"] is True
    assert report["video_count"] == 1
    assert report["estimated_requests"]["data_api"] == 1
    assert report["missing_metadata_count"] == 1


def test_invalid_boundaries_and_sorting_are_rejected(tmp_path: Path):
    config = config_for(tmp_path)
    with pytest.raises(ValueError):
        date_filter_bounds("custom", "2026-07-02", "2026-07-01")
    with pytest.raises(ValueError):
        date_filter_bounds("custom", "bad", "2026-07-01")
    with pytest.raises(ValueError):
        analytics_ranking(config, range_name="all", metric="title desc; drop table publications", page=1, page_size=20)
    with pytest.raises(ValueError):
        analytics_ranking(config, range_name="all", metric="views", page=0, page_size=20)


def test_channel_mismatch_blocks_sync(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1, channel_id="channel-other")
    schedule_first_sync(config, 1)
    result = sync_due_once(config, client=FakeClient(), now=datetime(2026, 7, 3, tzinfo=timezone.utc))
    state = connect_db(config).execute("SELECT * FROM youtube_sync_states WHERE publication_id=1").fetchone()
    assert result["failed"] == 1
    assert state["last_error_category"] == "CHANNEL_MISMATCH"


def test_analytics_scope_missing_keeps_data_api_metrics_and_null_analytics(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a", analytics_scope=False)
    add_publication(config, 1)
    schedule_first_sync(config, 1)
    result = sync_due_once(config, client=FakeClient(), now=datetime(2026, 7, 3, tzinfo=timezone.utc))
    snapshot = connect_db(config).execute("SELECT * FROM youtube_metric_snapshots WHERE publication_id=1").fetchone()
    assert result["successful"] == 1
    assert snapshot["view_count"] == 100
    assert snapshot["share_count"] is None
    assert snapshot["completion_rate"] is None
    assert snapshot["api_response_status"] == "PARTIAL_ANALYTICS_SCOPE_MISSING"
    account = connect_db(config).execute(
        "SELECT status FROM youtube_channel_auths WHERE account='account-a'"
    ).fetchone()
    assert account["status"] == "ANALYTICS_SCOPE_MISSING"


def test_analytics_api_permission_error_degrades_to_data_api_snapshot(tmp_path: Path):
    class MissingAnalyticsPermissionClient(FakeClient):
        def fetch_analytics(self, account: dict, video_id: str, start_date: str, end_date: str) -> dict:
            raise YouTubeApiError(
                "insufficient Analytics permission",
                status_code=403,
                category="ANALYTICS_SCOPE_MISSING",
                retryable=False,
            )

    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1)
    schedule_first_sync(config, 1)

    result = sync_due_once(
        config,
        client=MissingAnalyticsPermissionClient(),
        now=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    snapshot = connect_db(config).execute(
        "SELECT * FROM youtube_metric_snapshots WHERE publication_id=1"
    ).fetchone()
    assert result["failed"] == 0
    assert snapshot["view_count"] == 100
    assert snapshot["average_view_duration"] is None
    assert snapshot["api_response_status"] == "PARTIAL_ANALYTICS_SCOPE_MISSING"


def test_disabled_analytics_api_keeps_data_metrics_and_advances_checkpoint(tmp_path: Path):
    class DisabledAnalyticsApiClient(FakeClient):
        analytics_calls = 0

        def fetch_analytics(self, account: dict, video_id: str, start_date: str, end_date: str) -> dict:
            self.analytics_calls += 1
            raise YouTubeApiError(
                "YouTube Analytics API is disabled",
                status_code=403,
                category="ANALYTICS_API_DISABLED",
                retryable=False,
            )

    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1)
    add_publication(config, 2)
    schedule_first_sync(config, 1)
    schedule_first_sync(config, 2)

    client = DisabledAnalyticsApiClient()
    result = sync_due_once(
        config,
        client=client,
        now=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    connection = connect_db(config)
    snapshot = connection.execute(
        "SELECT * FROM youtube_metric_snapshots ORDER BY publication_id"
    ).fetchall()
    state = connection.execute(
        "SELECT * FROM youtube_sync_states WHERE publication_id=1"
    ).fetchone()
    assert result["failed"] == 0
    assert result["successful"] == 2
    assert client.analytics_calls == 1
    assert len(snapshot) == 2
    assert all(row["view_count"] == 100 for row in snapshot)
    assert all(row["like_count"] == 10 for row in snapshot)
    assert all(row["comment_count"] is None for row in snapshot)
    assert all(row["share_count"] is None for row in snapshot)
    assert all(row["completion_rate"] is None for row in snapshot)
    assert all(row["api_response_status"] == "PARTIAL_ANALYTICS_API_DISABLED" for row in snapshot)
    assert state["sync_status"] == "PARTIAL"
    assert state["last_error_category"] == "ANALYTICS_API_DISABLED"
    assert state["next_sync_at"] == "2026-07-02T12:00:00+00:00"
    assert state["next_sync_stage"] == "24h"


def test_retention_unavailable_keeps_other_analytics_metrics(tmp_path: Path):
    class RetentionUnavailableClient(FakeClient):
        def fetch_retention(self, account: dict, video_id: str, start_date: str, end_date: str) -> dict:
            raise YouTubeApiError(
                "audience retention is not available",
                status_code=403,
                category="FORBIDDEN",
                retryable=False,
            )

    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1)
    schedule_first_sync(config, 1)

    result = sync_due_once(
        config,
        client=RetentionUnavailableClient(),
        now=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    snapshot = connect_db(config).execute(
        "SELECT * FROM youtube_metric_snapshots WHERE publication_id=1"
    ).fetchone()
    state = connect_db(config).execute(
        "SELECT * FROM youtube_sync_states WHERE publication_id=1"
    ).fetchone()
    assert result["successful"] == 1
    assert snapshot["share_count"] == 4
    assert snapshot["average_view_duration"] == 12.5
    assert snapshot["completion_rate"] is None
    assert snapshot["api_response_status"] == "PARTIAL_RETENTION_UNAVAILABLE"
    assert state["sync_status"] == "PARTIAL"
    assert state["last_error_category"] == "RETENTION_UNAVAILABLE"


def test_platform_recalculation_decrease_is_preserved_and_audited(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1)
    schedule_first_sync(config, 1)
    add_snapshot(config, 1, fetched_at="2026-07-02T23:00:00+00:00", views=200, likes=20)

    sync_due_once(config, client=FakeClient(), now=datetime(2026, 7, 3, tzinfo=timezone.utc))

    snapshot = connect_db(config).execute(
        "SELECT * FROM youtube_metric_snapshots WHERE publication_id=1 ORDER BY fetched_at DESC,id DESC LIMIT 1"
    ).fetchone()
    audit = json.loads(snapshot["raw_status_json"])
    assert snapshot["view_count"] == 100
    assert audit["decreases"]["view_count"] == {"previous": 200, "current": 100}


def test_backfill_pause_and_resume_controls_worker_visibility(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1)
    add_publication(config, 2)

    run = backfill_report(
        config,
        now=datetime(2026, 7, 5, tzinfo=timezone.utc),
        dry_run=False,
        rate_limit_per_minute=6,
    )
    set_backfill_status(config, run["run_id"], "pause")
    assert restore_sync_tasks(
        config, now=datetime(2026, 7, 5, 1, tzinfo=timezone.utc)
    ) == []

    set_backfill_status(config, run["run_id"], "resume")
    assert [row["publication_id"] for row in restore_sync_tasks(
        config, now=datetime(2026, 7, 5, 1, tzinfo=timezone.utc)
    )] == [1, 2]


def test_publication_detail_is_not_limited_to_first_ranking_page(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    for publication_id in range(1, 102):
        add_publication(config, publication_id)
        add_snapshot(
            config,
            publication_id,
            fetched_at="2026-07-03T12:00:00+00:00",
            views=publication_id,
        )

    detail = publication_latest(config, 1)
    assert detail is not None
    assert detail["publication_id"] == 1
    assert detail["view_count"] == 1


def test_successful_publish_atomically_creates_first_sync_task(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a", title="Current channel")
    connection = connect_db(config)
    published_at = "2026-07-01T12:00:00+00:00"
    connection.execute(
        """
        INSERT INTO candidates(
          id,platform,source_id,url,title,description,duration,view_count,detected_language,
          score,status,metadata_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "candidate-publish", "facebook", "source-publish", "https://facebook.test/1",
            "Demo", "", 30, 0, "pt", 80, "APPROVED",
            json.dumps({"initial_category": "futebol", "keyword": "flamengo hoje"}),
            published_at, published_at,
        ),
    )
    connection.execute(
        """
        INSERT INTO publications(
              candidate_id,platform,account,account_label,channel_id,scheduled_at,scheduled_utc_at,
              operation_type,review_status,status,privacy_status,public_status,source_platform,title,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "candidate-publish", "youtube", "account-a", "Snapshot channel", "channel-a",
                "2026-07-01T11:00:00+00:00", "2026-07-01T11:00:00+00:00", "PUBLICATION", "APPROVED", "QUEUED",
            "public", "public", "facebook", "Demo", published_at, published_at,
        ),
    )
    publication_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.commit()

    result = publish_due_once(
        config,
        now=datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
        uploader=lambda _config, _publication_id: {
            "published_at": published_at,
            "youtube_video_id": "published-video",
            "youtube_url": "https://www.youtube.com/watch?v=published-video",
        },
    )

    assert result["published"] == 1
    publication = connect_db(config).execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    state = connect_db(config).execute("SELECT * FROM youtube_sync_states WHERE publication_id=?", (publication_id,)).fetchone()
    assert publication["authorized_account_id"] == "account-a"
    assert publication["platform_username_snapshot"] == "Snapshot channel"
    assert publication["source_category"] == "futebol"
    assert publication["source_keyword"] == "flamengo hoje"
    assert publication["published_local_at"] == "2026-07-01T09:00:00-03:00"
    assert state["first_sync_due_at"] == "2026-07-02T00:00:00+00:00"
    assert state["next_sync_stage"] == "12h"


def test_failed_publish_does_not_create_sync_task(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO candidates(id,platform,url,title,status,metadata_json,created_at,updated_at)
        VALUES('candidate-failed','facebook','https://facebook.test/2','Demo','APPROVED','{}',?,?)
        """,
        (now_iso(), now_iso()),
    )
    connection.execute(
        """
        INSERT INTO publications(candidate_id,platform,account,channel_id,scheduled_at,status,privacy_status,created_at,updated_at)
        VALUES('candidate-failed','youtube','account-a','channel-a','2026-07-01T11:00:00+00:00','QUEUED','public',?,?)
        """,
        (now_iso(), now_iso()),
    )
    connection.commit()

    publish_due_once(
        config,
        now=datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
        uploader=lambda _config, _publication_id: (_ for _ in ()).throw(RuntimeError("upload failed")),
    )

    assert connect_db(config).execute("SELECT count(*) FROM youtube_sync_states").fetchone()[0] == 0


def _dashboard_request(base: str, path: str, *, method: str = "GET", payload: dict | None = None, token: str = "") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Dashboard-Token"] = token
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode())


def test_growth_analytics_http_apis_validate_and_return_data(tmp_path: Path):
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a", title="Current A")
    add_publication(config, 1)
    schedule_first_sync(config, 1)
    add_snapshot(config, 1, fetched_at="2026-07-03T12:00:00+00:00", views=123)
    app = DashboardApplication(("127.0.0.1", 0), config)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{app.server_address[1]}"
    try:
        status, summary = _dashboard_request(base, "/api/youtube-analytics/summary?range=all")
        assert status == 200 and summary["view_count"] == 123
        _, ranking = _dashboard_request(base, "/api/youtube-analytics/ranking?range=all&metric=views&page=1&page_size=20")
        assert ranking["items"][0]["publication_id"] == 1
        _, accounts = _dashboard_request(base, "/api/youtube-analytics/accounts")
        assert accounts[0]["account_id"] == "account-a"
        _, detail = _dashboard_request(base, "/api/youtube-analytics/publications/1")
        assert detail["publication_id"] == 1
        _, history = _dashboard_request(base, "/api/youtube-analytics/publications/1/history?limit=20")
        assert history[0]["view_count"] == 123
        with pytest.raises(urllib.error.HTTPError) as invalid_page:
            _dashboard_request(base, "/api/youtube-analytics/ranking?range=all&page=not-a-number")
        assert invalid_page.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as error:
            _dashboard_request(base, "/api/youtube-analytics/ranking?range=all&metric=drop_table")
        assert error.value.code == 400
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def test_retry_and_backfill_http_apis_always_require_dashboard_auth(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "admin-secret")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "1")
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.channel_import_report",
        lambda _config, **kwargs: {"dry_run": kwargs.get("dry_run", True), "account_count": 1},
    )
    config = config_for(tmp_path)
    add_account(config, "account-a", "channel-a")
    add_publication(config, 1)
    schedule_first_sync(config, 1)
    app = DashboardApplication(("127.0.0.1", 0), config)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{app.server_address[1]}"
    try:
        for path, payload in (
            ("/api/youtube-analytics/publications/1/retry", {}),
            ("/api/youtube-analytics/backfill", {"dry_run": True}),
            ("/api/youtube-analytics/channel-import", {"dry_run": True}),
        ):
            with pytest.raises(urllib.error.HTTPError) as error:
                _dashboard_request(base, path, method="POST", payload=payload)
            assert error.value.code == 401
        status, retried = _dashboard_request(
            base,
            "/api/youtube-analytics/publications/1/retry",
            method="POST",
            payload={},
            token="admin-secret",
        )
        assert status == 200 and retried["sync_status"] == "PENDING"
        status, report = _dashboard_request(
            base,
            "/api/youtube-analytics/backfill",
            method="POST",
            payload={"dry_run": True, "rate_limit_per_minute": 6},
            token="admin-secret",
        )
        assert status == 200 and report["dry_run"] is True
        status, import_report = _dashboard_request(
            base,
            "/api/youtube-analytics/channel-import",
            method="POST",
            payload={"dry_run": True, "account_id": "account-a", "max_pages": 10},
            token="admin-secret",
        )
        assert status == 200 and import_report == {"dry_run": True, "account_count": 1}
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def test_growth_analytics_frontend_contract_contains_complete_controls():
    web = Path(__file__).parents[1] / "src" / "jaguartv_factory" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    javascript = (web / "app.js").read_text(encoding="utf-8")
    styles = (web / "styles.css").read_text(encoding="utf-8")

    for element_id in (
        "youtubeGrowthFilters",
        "youtubeGrowthRange",
        "youtubeGrowthStart",
        "youtubeGrowthEnd",
        "youtubeGrowthAccount",
        "youtubeGrowthMetric",
        "youtubeGrowthSummary",
        "youtubeGrowthFreshness",
        "youtubeRankingBody",
        "youtubeGrowthLoading",
        "youtubeGrowthError",
        "youtubeGrowthEmpty",
        "youtubeGrowthRetry",
        "youtubeGrowthPrevious",
        "youtubeGrowthNext",
    ):
        assert f'id="{element_id}"' in html
    for value in ("all", "7d", "30d", "custom", "views", "comments", "likes", "average_view_duration", "completion_rate", "shares", "published_at"):
        assert f'value="{value}"' in html
    assert "基于视频末段留存的完播率估算" in html
    assert "可能超过 100%" in html
    assert "AbortController" in javascript
    assert "pt-BR" in javascript
    assert "授权刷新失败" in javascript
    assert "授权解密失败" in javascript
    assert "频道导入" in javascript
    assert "发布后 12 小时、24 小时、3 天、7 天" in html
    assert "/api/youtube-analytics/summary" in javascript
    assert "/api/youtube-analytics/ranking" in javascript
    assert "youtube-growth-table" in styles
    assert "@media (min-width: 761px) and (max-width: 900px)" in styles
    assert "@media (max-width: 760px)" in styles


def test_growth_page_omits_brazil_daily_hot_words_widget():
    web = Path(__file__).parents[1] / "src" / "jaguartv_factory" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    javascript = (web / "app.js").read_text(encoding="utf-8")
    styles = (web / "styles.css").read_text(encoding="utf-8")

    assert "巴西今日热词" not in html
    assert 'id="runTrendsNow"' not in html
    assert 'id="hotKeywordStrip"' not in html
    assert 'api("/api/hot-keywords?date=today")' not in javascript
    assert 'api("/api/trends/run"' not in javascript
    assert "renderHotKeywords" not in javascript
    assert ".hot-keyword-strip" not in styles
    assert "当日分类关键词" in html
    assert 'api("/api/category-keywords?date=today")' in javascript
