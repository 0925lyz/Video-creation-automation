import json
from pathlib import Path

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.dashboard import dashboard_overview, save_metrics, save_publication, save_review
from jaguartv_factory.sessions import check_session, list_sessions, save_session


def dashboard_config(tmp_path: Path) -> dict:
    return {"_root": str(tmp_path), "run": {"workspace": "workspace"}}


def insert_candidate(config: dict, candidate_id: str = "candidate-1") -> None:
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
            candidate_id, "youtube", "source-1", "https://example.test/video", "Demo",
            "", 20, 100, "en", 80, "READY_FOR_REVIEW",
            json.dumps({"keyword": "football skills"}), timestamp, timestamp,
        ),
    )
    connection.commit()


def test_dashboard_schema_and_overview(tmp_path: Path):
    config = dashboard_config(tmp_path)
    insert_candidate(config)
    save_review(config, {"candidate_id": "candidate-1", "decision": "APPROVED", "reviewer": "tester"})
    publication_id = save_publication(
        config,
        {"candidate_id": "candidate-1", "platform": "youtube", "account": "JaguarTV"},
    )
    metrics_id = save_metrics(
        config,
        {
            "candidate_id": "candidate-1", "platform": "youtube", "views": 10_000,
            "shares": 200, "clicks": 500, "installs": 100, "registrations": 50,
        },
    )

    overview = dashboard_overview(config)
    assert publication_id > 0
    assert metrics_id > 0
    assert overview["kpis"]["inventory"] == 1
    assert overview["kpis"]["scheduled"] == 1
    assert overview["kpis"]["views"] == 10_000
    assert overview["kpis"]["registrations"] == 50
    assert overview["keywords"][0]["keyword"] == "football skills"

    connection = connect_db(config)
    feedback = connection.execute("SELECT * FROM feedback_actions").fetchone()
    assert feedback["action_type"] == "BOOST_KEYWORD"


def test_session_manager_saves_and_checks_cookie_state(tmp_path: Path):
    config = dashboard_config(tmp_path)
    cookie_state = {
        "cookies": [
            {
                "name": "sessionid",
                "value": "demo",
                "domain": ".douyin.com",
                "path": "/",
                "expires": 4102444800,
                "secure": True,
            }
        ],
        "origins": [],
    }

    saved = save_session(
        config,
        {
            "platform": "douyin",
            "account": "douyin_01",
            "label": "抖音素材号",
            "owner": "Lucas",
            "cookies_json": json.dumps(cookie_state),
        },
    )
    checked = check_session(config, "douyin", "douyin_01")
    sessions = list_sessions(config)

    assert saved["status"] == "READY"
    assert checked["cookie_count"] == 1
    assert sessions[0]["platform"] == "douyin"
    assert "session_login" in sessions[0]["login_command"]
    assert (tmp_path / saved["cookie_file_path"]).read_text(encoding="utf-8").count("douyin.com") == 1


def test_session_manager_accepts_chrome_cookie_table_text(tmp_path: Path):
    config = dashboard_config(tmp_path)
    cookie_rows = "\n".join([
        "LOGIN_INFO\tdemo-value\t.youtube.com\t/\t2027-08-31T13:37:30.096Z\t20\t✓\t✓\tNone",
        "PREF\tf4=4000000&tz=Asia.Shanghai\t.youtube.com\t/\t2027-09-01T04:28:43.045Z\t31\t\t✓\tLax",
    ])

    saved = save_session(
        config,
        {
            "platform": "youtube",
            "account": "youtube_table",
            "cookies_json": cookie_rows,
        },
    )
    cookie_file = tmp_path / saved["cookie_file_path"]

    assert saved["status"] == "READY"
    assert saved["cookie_count"] == 2
    assert cookie_file.read_text(encoding="utf-8").count(".youtube.com") == 2


def test_session_manager_fills_default_domain_for_cookie_objects(tmp_path: Path):
    config = dashboard_config(tmp_path)
    state = {"cookies": [{"name": "sessionid", "value": "demo"}], "origins": []}
    saved = save_session(
        config,
        {
            "platform": "douyin",
            "account": "douyin_default_domain",
            "cookies_json": json.dumps(state),
        },
    )
    cookie_file = tmp_path / saved["cookie_file_path"]

    assert saved["status"] == "READY"
    assert ".douyin.com" in cookie_file.read_text(encoding="utf-8")


def test_session_manager_accepts_escaped_tab_cookie_table(tmp_path: Path):
    config = dashboard_config(tmp_path)
    row = "sid_guard\\tdemo-value\\t.tiktok.com\\t/\\t2027-08-31T13:37:30.096Z\\t20\\t✓\\t✓\\tNone"
    saved = save_session(
        config,
        {
            "platform": "tiktok",
            "account": "tiktok_escaped_tabs",
            "cookies_json": row,
        },
    )

    assert saved["status"] == "READY"
    assert saved["cookie_count"] == 1


def test_session_manager_accepts_cookie_header_text(tmp_path: Path):
    config = dashboard_config(tmp_path)
    saved = save_session(
        config,
        {
            "platform": "xiaohongshu",
            "account": "xhs_header",
            "cookies_json": "a1=demo-a1; web_session=demo-session; path=/; secure",
        },
    )
    cookie_file = tmp_path / saved["cookie_file_path"]

    assert saved["status"] == "READY"
    assert saved["cookie_count"] == 2
    assert cookie_file.read_text(encoding="utf-8").count(".xiaohongshu.com") == 2
