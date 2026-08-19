import json
import threading
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse
from pathlib import Path

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.dashboard import (
    copywriter_prompt,
    copywriter_request,
    candidate_design_info,
    candidate_rows,
    category_keyword_rows,
    dashboard_overview,
    DashboardApplication,
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
    save_x_oauth_callback,
    save_youtube_oauth_callback,
    upload_kind_requires_token,
    update_x_auth,
    x_auth_rows,
    x_oauth_start_url,
    youtube_oauth_start_url,
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


def insert_publish_candidate(
    config: dict,
    *,
    candidate_id: str,
    platform: str,
    keyword: str,
    status: str = "APPROVED",
    parent_id: str = "",
) -> None:
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
            candidate_id,
            parent_id or None,
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
            json.dumps({"keyword": keyword}),
            timestamp,
            timestamp,
        ),
    )
    connection.commit()


def write_review_package(config: dict, package_id: str, *, source_job_id: str = "") -> None:
    package = Path(str(config["_root"])) / "workspace" / "server_media" / "review" / package_id
    package.mkdir(parents=True, exist_ok=True)
    (package / "video.mp4").write_bytes(b"video")
    (package / "metadata.json").write_text(
        json.dumps({
            "job_id": package_id,
            "source_job_id": source_job_id,
            "source": {"platform": "facebook", "title": "Athletico-PR 1-1 RB Bragantino"},
        }),
        encoding="utf-8",
    )


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


def test_uploads_do_not_require_upload_token():
    assert upload_kind_requires_token("design_image") is False
    assert upload_kind_requires_token("source") is False
    assert upload_kind_requires_token("reaction") is False
    assert upload_kind_requires_token("") is False


def test_dashboard_is_public_by_default_even_with_admin_token(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "secret-token")
    app = DashboardApplication(("127.0.0.1", 0), dashboard_config(tmp_path))
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{app.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/api/tasks", timeout=5) as response:
            assert response.status == 200
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def test_dashboard_admin_token_can_be_required_when_public_flag_is_off(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "secret-token")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "0")
    app = DashboardApplication(("127.0.0.1", 0), dashboard_config(tmp_path))
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{app.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/api/tasks", timeout=5)
        assert error.value.code == 401

        request = urllib.request.Request(
            f"{base}/api/tasks",
            headers={"X-Dashboard-Token": "secret-token"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200

        with urllib.request.urlopen(f"{base}/api/health", timeout=5) as response:
            assert response.status == 200
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def test_initial_category_uses_discovery_keyword_first():
    assert initial_category_for_text("AI short drama Brasil", "random title") == "ai短剧"
    assert initial_category_for_text("Anitta show viral", "football reaction") == "明星名人歌手"
    assert initial_category_for_text("Neymar melhores momentos", "football reaction") == "足球球星"
    assert initial_category_for_text("Palmeiras Cerro Porteño Libertadores", "random title") == "足球类"
    assert initial_category_for_text("sccp fiel torcedor", "google_trends") == "足球类"
    assert initial_category_for_text("isis valverde", "google_trends") == "明星名人歌手"
    assert initial_category_for_text("Notícias de hoje Brasil", "Flamengo") == "新闻类"
    assert initial_category_for_text("Novela da Globo", "football reaction") == "肥皂剧（电视剧、电影）"
    assert initial_category_for_text("Desafio TikTok Brasil", "dance") == "社交挑战"
    assert initial_category_for_text("Coreografia funk", "video") == "音乐类"
    assert initial_category_for_text("Documentário comida Brasil", "video") == "纪录片（美食、动物、地区发展）"
    assert initial_category_for_text("Tutorial completo como usar", "video") == "教程及优点展示类"
    assert initial_category_for_text("Comunicado oficial", "video") == "官方性质类"
    assert initial_category_for_text("Parceria com cupom", "video") == "合作类"
    assert initial_category_for_text("账号运营 教程", "video") == "运营教学类"
    assert initial_category_for_text("FAQ dúvidas suporte", "video") == "教程及答疑类"
    assert initial_category_for_text("unknown topic") == "未分类"


def test_category_keyword_rows_group_today_hot_keywords(tmp_path: Path):
    keyword_file = tmp_path / "config" / "keywords.demo.yaml"
    keyword_file.parent.mkdir()
    keyword_file.write_text(
        """
football_stars:
  terms:
    pt: [neymar]
ai_drama:
  terms:
    pt: [AI short drama]
""",
        encoding="utf-8",
    )
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "sources": {"keywords_file": "config/keywords.demo.yaml"},
        "trends": {"schedule_timezone": "America/Sao_Paulo"},
    }
    connection = connect_db(config)
    for keyword, source in [
        ("neymar", "agent-reach"),
        ("AI short drama", "last30days-skill"),
        ("flamengo hoje", "google_trends"),
        ("isis valverde", "google_trends"),
        ("novo hit", "agent-reach:音乐类"),
        ("sem termos agora", "daily_keywords:教程及优点展示类"),
    ]:
        connection.execute(
            "INSERT INTO hot_keywords(keyword,date,source,created_at) VALUES(?,?,?,?)",
            (keyword, "2026-08-14", source, now_iso()),
        )
    connection.commit()

    rows = category_keyword_rows(config, "2026-08-14")["rows"]
    by_label = {row["label"]: row for row in rows}

    assert len(rows) == 18
    assert by_label["足球球星"]["keywords"][0]["keyword"] == "neymar"
    assert by_label["ai短剧"]["keywords"][0]["keyword"] == "AI short drama"
    assert by_label["足球类"]["keywords"][0]["keyword"] == "flamengo hoje"
    assert by_label["明星名人歌手"]["keywords"][0]["keyword"] == "isis valverde"
    assert by_label["音乐类"]["keywords"][0]["keyword"] == "novo hit"
    assert by_label["教程及优点展示类"]["keywords"][0]["keyword"] == "sem termos agora"
    assert by_label["官方性质类"]["count"] == 0
    assert by_label["合作类"]["count"] == 0
    assert by_label["运营教学类"]["count"] == 0
    assert by_label["教程及答疑类"]["count"] == 0


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
    assert dashboard_overview(config)["kpis"]["inventory"] == 1


def test_candidate_rows_collapses_review_slice_children_under_parent(tmp_path: Path):
    config = dashboard_config(tmp_path)
    insert_publish_candidate(
        config,
        candidate_id="source-facebook",
        platform="facebook",
        keyword="Brasileirão",
        status="APPROVED",
    )
    for part in ("source-facebook_part01", "source-facebook_part02", "source-facebook_part03"):
        insert_publish_candidate(
            config,
            candidate_id=part,
            platform="facebook",
            keyword="Brasileirão",
            status="READY_FOR_REVIEW",
            parent_id="source-facebook",
        )
        write_review_package(config, part, source_job_id="source-facebook")

    pending_rows = candidate_rows(config, "READY_FOR_REVIEW", 20)
    approved_rows = candidate_rows(config, "APPROVED", 20)

    assert [row["id"] for row in pending_rows] == []
    parent = next(row for row in approved_rows if row["id"] == "source-facebook")
    assert parent["output_count"] == 3
    assert [asset["label"] for asset in parent["output_assets"]] == [
        "片段 01 · 通用版",
        "片段 02 · 通用版",
        "片段 03 · 通用版",
    ]


def test_save_review_syncs_slice_child_statuses(tmp_path: Path):
    config = dashboard_config(tmp_path)
    insert_publish_candidate(
        config,
        candidate_id="source-facebook",
        platform="facebook",
        keyword="Brasileirão",
        status="READY_FOR_REVIEW",
    )
    insert_publish_candidate(
        config,
        candidate_id="source-facebook_part01",
        platform="facebook",
        keyword="Brasileirão",
        status="READY_FOR_REVIEW",
        parent_id="source-facebook",
    )
    insert_publish_candidate(
        config,
        candidate_id="source-facebook_part02",
        platform="facebook",
        keyword="Brasileirão",
        status="READY_FOR_REVIEW",
        parent_id="source-facebook",
    )

    result = save_review(config, {
        "candidate_id": "source-facebook",
        "decision": "APPROVED",
        "reviewer": "tester",
    })

    connection = connect_db(config)
    statuses = {
        row["id"]: row["status"]
        for row in connection.execute(
            "SELECT id,status FROM candidates WHERE id LIKE 'source-facebook%' ORDER BY id"
        )
    }
    assert result["synced_children"] == ["source-facebook_part01", "source-facebook_part02"]
    assert statuses == {
        "source-facebook": "APPROVED",
        "source-facebook_part01": "APPROVED",
        "source-facebook_part02": "APPROVED",
    }


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


def test_youtube_publication_auto_routes_category_to_account(tmp_path: Path):
    config = dashboard_config(tmp_path)
    insert_publish_candidate(
        config,
        candidate_id="tk-football",
        platform="tiktok",
        keyword="Neymar melhores momentos",
    )

    publication_id = save_publication(config, {"candidate_id": "tk-football", "platform": "youtube"})

    connection = connect_db(config)
    row = connection.execute("SELECT account FROM publications WHERE id=?", (publication_id,)).fetchone()
    assert row["account"] == "consumer_football"


def test_youtube_publication_blocks_youtube_source_for_consumer_routes(tmp_path: Path):
    config = dashboard_config(tmp_path)
    insert_publish_candidate(
        config,
        candidate_id="yt-football",
        platform="youtube",
        keyword="Neymar melhores momentos",
    )

    with pytest.raises(ValueError, match="YouTube source candidates cannot be scheduled"):
        save_publication(config, {"candidate_id": "yt-football", "platform": "youtube"})


def test_youtube_publication_inherits_parent_source_for_slices(tmp_path: Path):
    config = dashboard_config(tmp_path)
    insert_publish_candidate(
        config,
        candidate_id="source-youtube",
        platform="youtube",
        keyword="Novela da Globo",
    )
    insert_publish_candidate(
        config,
        candidate_id="source-youtube_part01",
        platform="youtube",
        keyword="",
        parent_id="source-youtube",
    )

    with pytest.raises(ValueError, match="YouTube source candidates cannot be scheduled"):
        save_publication(config, {"candidate_id": "source-youtube_part01", "platform": "youtube"})


def test_non_youtube_source_can_route_to_entertainment_account(tmp_path: Path):
    config = dashboard_config(tmp_path)
    insert_publish_candidate(
        config,
        candidate_id="tk-novela",
        platform="tiktok",
        keyword="Novela da Globo",
    )

    publication_id = save_publication(config, {"candidate_id": "tk-novela", "platform": "youtube"})

    connection = connect_db(config)
    row = connection.execute("SELECT account FROM publications WHERE id=?", (publication_id,)).fetchone()
    assert row["account"] == "consumer_entertainment"


def test_youtube_oauth_start_url_includes_offline_state(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("JAGUARTV_GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("JAGUARTV_GOOGLE_REDIRECT_URI", "https://factory.jarg.top/oauth/youtube/callback")
    monkeypatch.setenv("JAGUARTV_OAUTH_TOKEN_KEY", "token-encryption-key")
    monkeypatch.setenv("JAGUARTV_OAUTH_STATE_SECRET", "state-secret")

    url = youtube_oauth_start_url(config, "consumer_football")
    query = parse_qs(urlparse(url).query)

    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["https://factory.jarg.top/oauth/youtube/callback"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert "https://www.googleapis.com/auth/youtube.upload" in query["scope"][0]
    assert query["state"][0]


def test_youtube_oauth_callback_encrypts_refresh_token(tmp_path: Path, monkeypatch):
    from jaguartv_factory.dashboard import make_oauth_state

    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("JAGUARTV_GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("JAGUARTV_GOOGLE_REDIRECT_URI", "https://factory.jarg.top/oauth/youtube/callback")
    monkeypatch.setenv("JAGUARTV_OAUTH_TOKEN_KEY", "token-encryption-key")
    monkeypatch.setenv("JAGUARTV_OAUTH_STATE_SECRET", "state-secret")

    def fake_post_form_json(url, form, *, timeout=20):
        assert form["code"] == "auth-code"
        assert form["grant_type"] == "authorization_code"
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token-secret",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
        }

    monkeypatch.setattr("jaguartv_factory.dashboard.post_form_json", fake_post_form_json)
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.get_authorized_youtube_channel",
        lambda access_token: {"channel_id": "UC123", "channel_title": "jaguartv vivo"},
    )

    result = save_youtube_oauth_callback(config, {
        "code": ["auth-code"],
        "state": [make_oauth_state("consumer_football")],
    })

    assert result["account"] == "consumer_football"
    assert result["channel_id"] == "UC123"
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM youtube_channel_auths WHERE account='consumer_football'").fetchone()
    assert row["channel_title"] == "jaguartv vivo"
    assert "refresh-token-secret" not in row["encrypted_refresh_token"]


def test_x_oauth_start_url_uses_pkce_and_expected_scopes(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_X_CLIENT_ID", "x-client-id")
    monkeypatch.setenv("JAGUARTV_X_REDIRECT_URI", "https://factory.jarg.top/oauth/x/callback")
    monkeypatch.setenv("JAGUARTV_OAUTH_TOKEN_KEY", "token-encryption-key")

    url = x_oauth_start_url(config, "consumer_football")
    query = parse_qs(urlparse(url).query)

    assert query["client_id"] == ["x-client-id"]
    assert query["redirect_uri"] == ["https://factory.jarg.top/oauth/x/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert "tweet.write" in query["scope"][0]
    assert "media.write" not in query["scope"][0]
    assert "offline.access" in query["scope"][0]
    assert "scope=tweet.read%20users.read%20tweet.write%20offline.access" in url
    connection = connect_db(config)
    row = connection.execute("SELECT account,code_verifier FROM x_oauth_states WHERE state=?", (query["state"][0],)).fetchone()
    assert row["account"] == "consumer_football"
    assert len(row["code_verifier"]) >= 43


def test_x_oauth_start_url_can_use_configured_scopes(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_X_CLIENT_ID", "x-client-id")
    monkeypatch.setenv("JAGUARTV_OAUTH_TOKEN_KEY", "token-encryption-key")
    monkeypatch.setenv("JAGUARTV_X_SCOPES", "tweet.read users.read tweet.write media.write offline.access")

    url = x_oauth_start_url(config, "consumer_football")
    query = parse_qs(urlparse(url).query)

    assert query["scope"] == ["tweet.read users.read tweet.write media.write offline.access"]


def test_x_auth_rows_creates_missing_auth_table(tmp_path: Path):
    config = dashboard_config(tmp_path)
    connection = connect_db(config)
    connection.execute("DROP TABLE x_account_auths")
    connection.commit()

    assert x_auth_rows(config) == []
    row = connect_db(config).execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='x_account_auths'"
    ).fetchone()
    assert row["name"] == "x_account_auths"


def test_x_oauth_callback_saves_pending_confirmation_without_plain_tokens(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_X_CLIENT_ID", "x-client-id")
    monkeypatch.setenv("JAGUARTV_X_REDIRECT_URI", "https://factory.jarg.top/oauth/x/callback")
    monkeypatch.setenv("JAGUARTV_OAUTH_TOKEN_KEY", "token-encryption-key")
    url = x_oauth_start_url(config, "consumer_football")
    state = parse_qs(urlparse(url).query)["state"][0]

    def fake_post_form_json(url, form, *, timeout=20, headers=None):
        assert form["code"] == "x-auth-code"
        assert form["grant_type"] == "authorization_code"
        assert form["code_verifier"]
        return {
            "access_token": "x-access-token-secret",
            "refresh_token": "x-refresh-token-secret",
            "token_type": "bearer",
            "expires_in": 7200,
            "scope": "tweet.read users.read tweet.write media.write offline.access",
        }

    monkeypatch.setattr("jaguartv_factory.dashboard.post_form_json", fake_post_form_json)
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.get_authorized_x_user",
        lambda access_token: {"x_user_id": "123456", "username": "JaguarTVFutebol", "display_name": "JaguarTV Futebol"},
    )

    result = save_x_oauth_callback(config, {"code": ["x-auth-code"], "state": [state]})

    assert result["account"] == "consumer_football"
    assert result["status"] == "PENDING_CONFIRMATION"
    rows = x_auth_rows(config)
    assert rows[0]["username"] == "JaguarTVFutebol"
    assert rows[0]["status"] == "PENDING_CONFIRMATION"
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM x_account_auths WHERE account='consumer_football'").fetchone()
    assert "x-access-token-secret" not in row["encrypted_access_token"]
    assert "x-refresh-token-secret" not in row["encrypted_refresh_token"]


def test_x_auth_can_be_confirmed_and_revoked(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_X_CLIENT_ID", "x-client-id")
    monkeypatch.setenv("JAGUARTV_OAUTH_TOKEN_KEY", "token-encryption-key")
    timestamp = now_iso()
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO x_account_auths(
          account,x_user_id,username,display_name,scopes,encrypted_access_token,
          encrypted_refresh_token,token_type,expires_in,status,authorized_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "consumer_main", "42", "JaguarTVHoje", "JaguarTV Hoje",
            "tweet.write offline.access", "encrypted-access", "encrypted-refresh",
            "bearer", 7200, "PENDING_CONFIRMATION", timestamp, timestamp,
        ),
    )
    connection.commit()

    confirmed = update_x_auth(config, {"account": "consumer_main", "action": "confirm"})
    assert confirmed["status"] == "AUTHORIZED"
    revoked = update_x_auth(config, {"account": "consumer_main", "action": "revoke"})
    assert revoked["status"] == "REVOKED"
    row = connect_db(config).execute("SELECT encrypted_access_token,encrypted_refresh_token FROM x_account_auths WHERE account='consumer_main'").fetchone()
    assert row["encrypted_access_token"] == ""
    assert row["encrypted_refresh_token"] == ""


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
