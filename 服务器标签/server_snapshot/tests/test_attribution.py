import json
from pathlib import Path

import pytest

from jaguartv_factory.core import (
    assert_script_is_portuguese,
    build_tracking_url,
    connect_db,
    now_iso,
    tracking_links,
)
from jaguartv_factory.dashboard import (
    attribution_report,
    dashboard_overview,
    save_callback,
    save_events,
    save_publication,
    save_review,
)


def make_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "tracking": {
            "base_url": "https://copa.jarg.top/baixar-o-app",
            "utm_medium": "organic_social",
            "campaign": "hoje_sports",
            "hook_version": "A",
        },
    }


def insert_candidate(config: dict, candidate_id: str = "cand-1", status: str = "READY_FOR_REVIEW") -> None:
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
            candidate_id, "youtube", f"src-{candidate_id}", "https://example.test/v", "Demo",
            "", 20, 100, "en", 80, status,
            json.dumps({"keyword": "football skills"}), timestamp, timestamp,
        ),
    )
    connection.commit()


def test_tracking_url_contains_candidate_and_hook(tmp_path: Path):
    config = make_config(tmp_path)
    url = build_tracking_url(config, "abc123", "tiktok")
    assert "utm_source=tiktok" in url
    assert "utm_campaign=hoje_sports" in url
    assert "utm_content=abc123_A" in url
    links = tracking_links(config, "abc123")
    assert set(links) == {"youtube", "tiktok", "kwai", "facebook"}
    assert len({*links.values()}) == 4  # one unique link per platform


def test_language_gate_blocks_chinese():
    with pytest.raises(RuntimeError):
        assert_script_is_portuguese("这是一个中文脚本，不应该被葡语音色朗读出来")
    assert_script_is_portuguese("Olha só! Você não vai acreditar que o Brasil marcou um gol para vencer.")


def test_review_gate_blocks_unapproved_publication(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config)
    with pytest.raises(ValueError, match="APPROVED"):
        save_publication(config, {"candidate_id": "cand-1", "platform": "youtube"})
    result = save_review(config, {"candidate_id": "cand-1", "decision": "APPROVED", "reviewer": "tester"})
    assert result["status"] == "APPROVED"
    assert save_publication(
        config,
        {"candidate_id": "cand-1", "platform": "youtube", "account": "manual_review_channel"},
    ) > 0


def test_review_rejects_invalid_decision(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config)
    with pytest.raises(ValueError):
        save_review(config, {"candidate_id": "cand-1", "decision": "MAYBE"})


def test_events_ingest_and_attribution(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config)
    result = save_events(config, {"events": [
        {"event_type": "landing_click", "utm_content": "cand-1_A", "utm_source": "tiktok", "visitor_id": "v1"},
        {"event_type": "download_started", "utm_content": "cand-1_A", "utm_source": "tiktok", "visitor_id": "v1"},
        {"event_type": "registration", "candidate_id": "cand-1", "platform": "tiktok", "visitor_id": "v1"},
        {"event_type": "first_watch", "candidate_id": "cand-1", "platform": "tiktok", "visitor_id": "v1"},
        {"event_type": "landing_click", "utm_content": "cand-1_A", "utm_source": "tiktok", "visitor_id": "v1"},  # duplicate visitor
        {"event_type": "landing_click", "utm_content": "unknown-cand_A"},  # unknown candidate skipped
    ]})
    assert result["saved"] == 5
    assert result["skipped"] == 1

    report = attribution_report(config, "cand-1")
    assert report["funnel"]["landing_click"] == 1  # deduplicated by visitor
    assert report["funnel"]["registration"] == 1
    assert report["funnel"]["first_watch"] == 1
    assert "utm_content=cand-1_A" in report["tracking_links"]["tiktok"]
    assert report["by_platform"]["tiktok"]["first_watch"] == 1

    overview = dashboard_overview(config)
    labels = [step["label"] for step in overview["funnel"]]
    assert "首次观看" in labels
    assert overview["kpis"]["first_watch"] == 1
    assert overview["kpis"]["registrations"] == 1
    keyword = overview["keywords"][0]
    assert keyword["first_watch"] == 1


def test_events_parse_utm_content_from_last_underscore(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config, "upload_abc123")
    insert_candidate(config, "candidate_part01")
    result = save_events(config, {"events": [
        {"event_type": "landing_click", "utm_content": "upload_abc123_A", "utm_source": "facebook"},
        {"event_type": "registration", "utm_content": "candidate_part01_B", "utm_source": "tiktok"},
    ]})

    assert result == {"saved": 2, "skipped": 0}
    assert attribution_report(config, "upload_abc123")["funnel"]["landing_click"] == 1
    assert attribution_report(config, "candidate_part01")["funnel"]["registration"] == 1


def test_callback_updates_dashboard_kpis(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config)

    result = save_callback(config, {
        "candidate_id": "cand-1",
        "publisher": "operator",
        "platform": "youtube",
        "views": 1000,
        "clicks": 30,
        "registrations": 4,
        "timestamp": "2026-08-14T00:00:00+00:00",
    })

    assert result["success"] is True
    assert result["snapshot_id"] > 0
    assert result["registration_events"] == 4
    overview = dashboard_overview(config)
    assert overview["kpis"]["views"] == 1000
    assert overview["kpis"]["clicks"] == 30
    assert overview["kpis"]["registrations"] == 4


def test_events_reject_all_invalid(tmp_path: Path):
    config = make_config(tmp_path)
    insert_candidate(config)
    with pytest.raises(ValueError):
        save_events(config, {"event_type": "unknown_event", "candidate_id": "cand-1"})
