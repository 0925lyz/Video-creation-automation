import json
from pathlib import Path

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.dashboard import (
    copywriter_prompt,
    copywriter_request,
    candidate_design_info,
    candidate_rows,
    dashboard_overview,
    extract_json_object,
    gemini_model_candidates,
    gemini_model_name,
    generate_copywriter_with_gemini,
    initial_category_for_text,
    public_brand_asset_path,
    render_job_rows,
    save_metrics,
    save_publication,
    save_review,
    upload_kind_requires_token,
)
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


def test_copywriter_request_validates_mode_and_count():
    request = copywriter_request({
        "input": "足球，巴西街头足球挑战",
        "mode": "generic",
        "platform": "tiktok",
        "tone": "viral",
        "count": 3,
        "heat": 7,
        "cta": "Saiba mais",
    })

    assert request["mode"] == "generic"
    assert request["count"] == 3
    with pytest.raises(ValueError):
        copywriter_request({"input": "demo", "mode": "bad"})
    with pytest.raises(ValueError):
        copywriter_request({"input": "demo", "count": 4})


def test_gemini_model_name_maps_31_pro_alias():
    assert gemini_model_name("gemini-3.1-Pro") == "gemini-3.1-pro-preview"


def test_gemini_model_candidates_fallback_within_31_family():
    assert gemini_model_candidates("gemini-3.1-pro-preview")[:2] == [
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
    ]


def test_copywriter_prompt_keeps_generic_mode_off_tv_product():
    prompt = copywriter_prompt(copywriter_request({
        "input": "足球，巴西街头足球挑战",
        "mode": "generic",
        "platform": "tiktok",
    }))

    assert "write directly about the user's keywords" in prompt
    assert "Do not mention JaguarTV" in prompt


def test_extract_json_object_accepts_gemini_fenced_json():
    result = extract_json_object('```json\n{"strategy":"ok","titles":["a"]}\n```')

    assert result["strategy"] == "ok"


def test_generate_copywriter_with_gemini_normalizes_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    response_payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "strategy": "Tema principal em pt-BR: desafio de futebol de rua no Brasil",
                        "titles": [
                            "Desafio de futebol de rua no Brasil: quem ganha?",
                            "O lance que merece replay",
                            "Quando a rua vira campo",
                        ],
                        "captions": [
                            "1. [TikTok] A rua vira campo e cada drible decide.",
                            "2. [TikTok] Quem ficou com mais estilo nesse desafio?",
                            "3. [TikTok] Tecnica ou ousadia?",
                        ],
                        "cta": "Saiba mais",
                        "hashtags": "#FutebolDeRua #Desafio #Brasil",
                        "emails": [{
                            "name": "Abertura",
                            "subject": "Olha esse desafio",
                            "preview": "Rua, bola e disputa.",
                            "body": "A cena mostra futebol de rua no Brasil.",
                            "cta": "Ver o momento",
                        }],
                        "seo": {
                            "title": "Desafio de futebol de rua no Brasil",
                            "description": "Lances e reacoes de futebol de rua.",
                            "keywords": ["futebol de rua", "desafio", "Brasil"],
                        },
                        "zhAudit": {
                            "strategy": "围绕巴西街头足球挑战生成内容。",
                            "titles": ["巴西街头足球挑战：谁赢？", "值得回放的动作", "街道变球场"],
                            "captions": ["1. [TikTok] 街道变球场。", "2. [TikTok] 谁更有风格？", "3. [TikTok] 技术还是胆量？"],
                            "cta": "引导查看更多。",
                            "hashtags": "标签突出街头足球、挑战和巴西。",
                            "emails": [{
                                "name": "开场",
                                "subject": "看这个挑战",
                                "preview": "街头、足球和对决。",
                                "body": "这段内容展示巴西街头足球。",
                                "cta": "查看这个瞬间",
                            }],
                            "seo": {
                                "title": "巴西街头足球挑战",
                                "description": "街头足球动作和反应。",
                                "keywords": ["街头足球", "挑战", "巴西"],
                            },
                        },
                        "note": "Revise antes de publicar.",
                    })
                }]
            }
        }]
    }

    def fake_post_json(url, headers, body, timeout):
        assert headers["x-goog-api-key"] == "test-key"
        assert "generateContent" in url
        assert "JaguarTV" in body["contents"][0]["parts"][0]["text"]
        return 200, response_payload

    monkeypatch.setattr("jaguartv_factory.dashboard.post_json", fake_post_json)
    result = generate_copywriter_with_gemini({
        "input": "足球，巴西街头足球挑战",
        "mode": "generic",
        "platform": "tiktok",
        "count": 3,
    })

    assert result["source"] == "gemini"
    assert result["titles"][0].startswith("Desafio")
    assert "JaguarTV" not in "\n".join(result["captions"])


def test_public_brand_asset_path_is_limited_to_brand_assets():
    asset = public_brand_asset_path("/assets/brand/endcard_landscape_blue_v2.png")

    assert asset is not None
    assert asset.name == "endcard_landscape_blue_v2.png"
    assert public_brand_asset_path("/assets/brand/../../config/pipeline.yaml") is None
    assert public_brand_asset_path("/assets/brand/missing.png") is None


def test_design_image_uploads_do_not_require_upload_token():
    assert upload_kind_requires_token("design_image") is False
    assert upload_kind_requires_token("source") is True
    assert upload_kind_requires_token("reaction") is True
    assert upload_kind_requires_token("") is True


def test_initial_category_uses_discovery_keyword_first():
    assert initial_category_for_text("Palmeiras Cerro Porteño Libertadores", "random title") == "足球类"
    assert initial_category_for_text("Notícias de hoje Brasil", "Flamengo") == "新闻类"
    assert initial_category_for_text("Novela da Globo", "football reaction") == "肥皂剧"
    assert initial_category_for_text("Desafio TikTok Brasil", "dance") == "社交挑战"
    assert initial_category_for_text("Coreografia funk", "video") == "音乐类"
    assert initial_category_for_text("unknown topic") == "未分类"


def test_candidate_design_info_defaults_when_source_not_downloaded(tmp_path: Path):
    config = dashboard_config(tmp_path)
    insert_candidate(config)

    info = candidate_design_info(config, "candidate-1")

    assert info["source_preview_url"] == ""
    assert (info["design_canvas_width"], info["design_canvas_height"]) == (1080, 1920)


def test_child_candidate_inherits_parent_initial_category(tmp_path: Path):
    config = dashboard_config(tmp_path)
    insert_candidate(config, "parent-1")
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO candidates(
          id,parent_id,platform,source_id,url,title,description,duration,view_count,
          detected_language,score,status,metadata_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "child-1", "parent-1", "youtube", "source-1_slice1", "https://example.test/video",
            "Slice without keyword", "", 12, 0, "en", 70, "READY_FOR_REVIEW",
            json.dumps({}), timestamp, timestamp,
        ),
    )
    connection.commit()

    child = next(row for row in candidate_rows(config) if row["id"] == "child-1")

    assert child["initial_category"] == "足球类"
    assert child["initial_keyword"] == "football skills"


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
    rows = candidate_rows(config)
    assert rows[0]["initial_category"] == "足球类"
    assert rows[0]["initial_keyword"] == "football skills"

    connection = connect_db(config)
    feedback = connection.execute("SELECT * FROM feedback_actions").fetchone()
    assert feedback["action_type"] == "BOOST_KEYWORD"


def test_render_job_rows_include_candidate_title_and_metadata(tmp_path: Path):
    config = dashboard_config(tmp_path)
    insert_candidate(config)
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO render_jobs
          (id,candidate_id,variant,engine,status,progress,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            "job-1",
            "candidate-1",
            "通用版",
            "remotion_renderer_api",
            "RENDERING",
            0.5,
            json.dumps({"fps": 30}),
            now_iso(),
            now_iso(),
        ),
    )
    connection.commit()

    rows = render_job_rows(config)
    assert rows[0]["title"] == "Demo"
    assert rows[0]["metadata"]["fps"] == 30
    assert rows[0]["progress"] == 0.5


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
