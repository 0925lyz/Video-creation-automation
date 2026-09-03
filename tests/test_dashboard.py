import json
import http.client
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse
from pathlib import Path

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.dashboard import (
    admin_session_is_valid,
    copywriter_prompt,
    copywriter_request,
    candidate_design_info,
    candidate_rows,
    category_keyword_rows,
    dashboard_overview,
    DashboardApplication,
    DashboardHandler,
    extract_json_object,
    copywriter_ai_model_candidates,
    copywriter_ai_model_name,
    generate_copywriter_with_ai,
    initial_category_for_text,
    public_brand_asset_path,
    render_job_rows,
    save_metrics,
    save_publication,
    save_review,
    save_x_oauth_callback,
    save_youtube_oauth_callback,
    sign_admin_session,
    signed_upload_url,
    upload_kind_requires_token,
    update_x_auth,
    verify_x_auths,
    x_auth_rows,
    x_oauth_start_url,
    sign_youtube_auth_link,
    youtube_auth_link,
    youtube_auth_link_is_valid,
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


def test_copywriter_ai_model_name_defaults_to_gpt_52():
    assert copywriter_ai_model_name("") == "gpt-5.2"


def test_copywriter_ai_model_candidates_fallback_to_deepseek():
    assert copywriter_ai_model_candidates("gpt-5.2") == ["gpt-5.2", "deepseek-v4-flash"]


def test_copywriter_prompt_keeps_generic_mode_off_tv_product():
    prompt = copywriter_prompt(copywriter_request({
        "input": "足球，巴西街头足球挑战",
        "mode": "generic",
        "platform": "tiktok",
    }))

    assert "write directly about the user's keywords" in prompt
    assert "Do not mention JaguarTV" in prompt


def test_extract_json_object_accepts_fenced_json():
    result = extract_json_object('```json\n{"strategy":"ok","titles":["a"]}\n```')

    assert result["strategy"] == "ok"


def test_generate_copywriter_with_ai_uses_gpt_52_first(monkeypatch):
    monkeypatch.setenv("JAGUARTV_OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("JAGUARTV_DEEPSEEK_API_KEY", raising=False)
    response_payload = {
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
    }

    def fake_post(url, *, headers, data, timeout):
        body = json.loads(data.decode("utf-8"))
        assert url == "https://api.openai.com/v1/responses"
        assert headers["Authorization"] == "Bearer test-key"
        assert body["model"] == "gpt-5.2"
        assert "JaguarTV" in body["input"]
        return type("Response", (), {"status_code": 200, "json": lambda _self: {"output_text": json.dumps(response_payload)}})()

    monkeypatch.setattr("jaguartv_factory.publishing_copywriter.requests.post", fake_post)
    result = generate_copywriter_with_ai({
        "input": "足球，巴西街头足球挑战",
        "mode": "generic",
        "platform": "tiktok",
        "count": 3,
    })

    assert result["source"] == "openai"
    assert result["model"] == "gpt-5.2"
    assert result["titles"][0].startswith("Desafio")
    assert "JaguarTV" not in "\n".join(result["captions"])


def test_public_brand_asset_path_is_limited_to_brand_assets():
    asset = public_brand_asset_path("/assets/brand/dashboard_favicon.png")

    assert asset is not None
    assert asset.name == "dashboard_favicon.png"
    assert public_brand_asset_path("/assets/brand/../../config/pipeline.yaml") is None
    assert public_brand_asset_path("/assets/brand/missing.png") is None


def test_cta_media_is_served_from_managed_server_storage(tmp_path: Path):
    config = dashboard_config(tmp_path)
    cta = tmp_path / "workspace" / "server_media" / "cta" / "demo.jpg"
    cta.parent.mkdir(parents=True)
    cta.write_bytes(b"cta")
    server = DashboardApplication(("127.0.0.1", 0), config)
    try:
        handler = object.__new__(DashboardHandler)
        handler.server = server
        captured = {}
        handler.send_file = lambda path, **kwargs: captured.update(path=path, kwargs=kwargs)
        handler.send_error = lambda status: captured.update(error=status)
        handler.send_media("cta/demo.jpg")
    finally:
        server.server_close()
    assert captured["path"] == cta.resolve()
    assert "error" not in captured


def test_all_upload_kinds_require_upload_token():
    assert upload_kind_requires_token("design_image") is True
    assert upload_kind_requires_token("source") is True
    assert upload_kind_requires_token("reaction") is True
    assert upload_kind_requires_token("") is True


def test_dashboard_is_private_by_default_when_admin_token_exists(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "secret-token")
    app = DashboardApplication(("127.0.0.1", 0), dashboard_config(tmp_path))
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{app.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/api/tasks", timeout=5)
        assert error.value.code == 401
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def test_dashboard_browser_login_uses_signed_session_cookie(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "secret-token")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "0")
    app = DashboardApplication(("127.0.0.1", 0), dashboard_config(tmp_path))
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 302
        assert response.getheader("Location") == "/login"
        response.read()

        connection.request("GET", "/login")
        response = connection.getresponse()
        assert response.status == 200
        assert "访问密码" in response.read().decode("utf-8")

        connection.request(
            "POST",
            "/api/auth/login",
            body=json.dumps({"password": "wrong-token"}),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 401
        assert response.getheader("Set-Cookie") is None
        response.read()

        connection.request(
            "POST",
            "/api/auth/login",
            body=json.dumps({"password": "secret-token"}),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        cookie = response.getheader("Set-Cookie") or ""
        assert cookie.startswith("jaguartv_admin=")
        assert "secret-token" not in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert "Secure" in cookie
        session_cookie = cookie.split(";", 1)[0]
        response.read()

        connection.request("GET", "/api/tasks", headers={"Cookie": session_cookie})
        response = connection.getresponse()
        assert response.status == 200
        response.read()

        connection.request("POST", "/api/auth/logout", body=b"{}", headers={"Cookie": session_cookie})
        response = connection.getresponse()
        assert response.status == 200
        cleared = response.getheader("Set-Cookie") or ""
        assert cleared.startswith("jaguartv_admin=")
        assert "Max-Age=0" in cleared
        response.read()
    finally:
        connection.close()
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def test_dashboard_rejects_admin_token_in_url(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "secret-token")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "0")
    app = DashboardApplication(("127.0.0.1", 0), dashboard_config(tmp_path))
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=5)
    try:
        connection.request("GET", "/?admin_token=secret-token")
        response = connection.getresponse()
        assert response.status == 302
        assert response.getheader("Location") == "/login"
        assert response.getheader("Set-Cookie") is None
        response.read()
    finally:
        connection.close()
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def test_admin_session_rejects_expiry_and_tampering():
    session = sign_admin_session("secret-token", 1_000)

    assert admin_session_is_valid("secret-token", session, now=999)
    assert not admin_session_is_valid("secret-token", session, now=1_000)
    assert not admin_session_is_valid("other-token", session, now=999)
    assert not admin_session_is_valid("secret-token", session + "tampered", now=999)


def test_dashboard_login_rate_limits_repeated_failures(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "secret-token")
    app = DashboardApplication(("127.0.0.1", 0), dashboard_config(tmp_path))
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=5)
    try:
        for _ in range(5):
            connection.request(
                "POST",
                "/api/auth/login",
                body=b'{"password":"wrong"}',
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            assert response.status == 401
            response.read()
        connection.request(
            "POST",
            "/api/auth/login",
            body=b'{"password":"secret-token"}',
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 429
        response.read()
    finally:
        connection.close()
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def test_explicit_public_dashboard_is_read_only(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "secret-token")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "1")
    app = DashboardApplication(("127.0.0.1", 0), dashboard_config(tmp_path))
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{app.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/api/tasks", timeout=5) as response:
            assert response.status == 200
        request = urllib.request.Request(
            f"{base}/api/actions",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == 401
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def test_upload_requires_token_and_signed_download_rejects_tampering(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "1")
    monkeypatch.setenv("JAGUARTV_UPLOAD_TOKEN", "upload-secret")
    app = DashboardApplication(("127.0.0.1", 0), dashboard_config(tmp_path))
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{app.server_address[1]}"
    try:
        request = urllib.request.Request(
            f"{base}/api/uploads?kind=reaction&filename=clip.mp4",
            data=b"video",
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == 401

        request = urllib.request.Request(
            f"{base}/api/uploads?kind=reaction&filename=clip.mp4",
            data=b"video",
            headers={"X-Upload-Token": "upload-secret"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            uploaded = json.loads(response.read())
        assert "expires=" in uploaded["download_url"]
        assert "signature=" in uploaded["download_url"]
        with urllib.request.urlopen(base + uploaded["download_url"], timeout=5) as response:
            assert response.read() == b"video"
        tampered = uploaded["download_url"].replace("signature=", "signature=bad")
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + tampered, timeout=5)
        assert error.value.code == 401
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()

    assert signed_upload_url("not-an-upload-id") == ""


def test_password_session_has_full_dashboard_operator_permissions(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "dashboard-secret")
    monkeypatch.setenv("JAGUARTV_UPLOAD_TOKEN", "upload-secret")
    monkeypatch.setenv("JAGUARTV_EVENTS_TOKEN", "events-secret")
    monkeypatch.setenv("JAGUARTV_CALLBACK_TOKEN", "callback-secret")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "1")
    config = dashboard_config(tmp_path)
    insert_candidate(config, "full-access-candidate")
    app = DashboardApplication(("127.0.0.1", 0), config)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=5)
    try:
        connection.request(
            "POST",
            "/api/auth/login",
            body=json.dumps({"password": "dashboard-secret"}),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        session_cookie = (response.getheader("Set-Cookie") or "").split(";", 1)[0]
        response.read()

        connection.request(
            "POST",
            "/api/uploads?kind=reaction&filename=operator.mp4",
            body=b"operator-video",
            headers={"Cookie": session_cookie, "Content-Type": "video/mp4"},
        )
        response = connection.getresponse()
        assert response.status == 201
        uploaded = json.loads(response.read())

        connection.request("GET", "/api/uploads", headers={"Cookie": session_cookie})
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())[0]["id"] == uploaded["id"]

        connection.request(
            "GET", f"/api/uploads/{uploaded['id']}/link", headers={"Cookie": session_cookie}
        )
        response = connection.getresponse()
        assert response.status == 200
        assert "download_url" in json.loads(response.read())

        connection.request(
            "GET", f"/api/uploads/{uploaded['id']}/download", headers={"Cookie": session_cookie}
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b"operator-video"

        connection.request(
            "POST",
            "/api/events",
            body=json.dumps({
                "candidate_id": "full-access-candidate",
                "event_type": "landing_click",
                "platform": "youtube",
            }),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 201
        assert json.loads(response.read())["saved"] == 1

        connection.request(
            "POST",
            "/api/callback",
            body=json.dumps({
                "candidate_id": "full-access-candidate",
                "publisher": "dashboard-user",
                "platform": "youtube",
                "views": 12,
                "clicks": 3,
                "registrations": 1,
            }),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["success"] is True
    finally:
        connection.close()
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


def test_signed_youtube_auth_link_allows_known_account_until_expiry(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_GOOGLE_REDIRECT_URI", "https://factory.jarg.top/oauth/youtube/callback")
    monkeypatch.setenv("JAGUARTV_OAUTH_STATE_SECRET", "state-secret")
    expires_at = 1_800_000_000

    url = youtube_auth_link(config, "partner_embaixador", expires_at=expires_at)
    query = parse_qs(urlparse(url).query)

    assert url.startswith("https://factory.jarg.top/oauth/youtube/start?")
    assert query["account"] == ["partner_embaixador"]
    assert youtube_auth_link_is_valid(query, now=expires_at - 60)
    assert not youtube_auth_link_is_valid(query, now=expires_at)


def test_signed_youtube_auth_link_rejects_tampering_unknown_accounts_and_long_ttl(monkeypatch):
    monkeypatch.setenv("JAGUARTV_OAUTH_STATE_SECRET", "state-secret")
    expires_at = 1_800_000_000
    signature = sign_youtube_auth_link("consumer_main", expires_at)

    assert not youtube_auth_link_is_valid({
        "account": ["consumer_football"],
        "expires": [str(expires_at)],
        "signature": [signature],
    }, now=expires_at - 60)
    assert not youtube_auth_link_is_valid({
        "account": ["unknown_account"],
        "expires": [str(expires_at)],
        "signature": [signature],
    }, now=expires_at - 60)
    assert not youtube_auth_link_is_valid({
        "account": ["consumer_main"],
        "expires": [str(expires_at)],
        "signature": [signature],
    }, now=expires_at - (8 * 24 * 3600))


def test_signed_youtube_auth_link_bypasses_admin_cookie_for_oauth_start(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "dashboard-secret")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "0")
    monkeypatch.setenv("JAGUARTV_GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("JAGUARTV_GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("JAGUARTV_GOOGLE_REDIRECT_URI", "https://factory.jarg.top/oauth/youtube/callback")
    monkeypatch.setenv("JAGUARTV_OAUTH_TOKEN_KEY", "token-encryption-key")
    monkeypatch.setenv("JAGUARTV_OAUTH_STATE_SECRET", "state-secret")
    app = DashboardApplication(("127.0.0.1", 0), config)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        link = youtube_auth_link(config, "consumer_guide", expires_at=int(time.time()) + 300)
        parsed = urlparse(link)
        connection = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=5)
        connection.request("GET", f"{parsed.path}?{parsed.query}")
        response = connection.getresponse()
        assert response.status == 302
        assert response.getheader("Location", "").startswith("https://accounts.google.com/")
        connection.close()
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def test_publish_api_generates_account_bound_youtube_auth_link(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "dashboard-secret")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "0")
    monkeypatch.setenv("JAGUARTV_GOOGLE_REDIRECT_URI", "https://factory.jarg.top/oauth/youtube/callback")
    monkeypatch.setenv("JAGUARTV_OAUTH_STATE_SECRET", "state-secret")
    app = DashboardApplication(("127.0.0.1", 0), config)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps({"account": "JaguarTV Revendedor", "ttl_seconds": 120}).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=5)
        connection.request("POST", "/api/publish/youtube-auth-link", body=body, headers={
            "Content-Type": "application/json",
        })
        assert connection.getresponse().status == 401
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=5)
        connection.request("POST", "/api/publish/youtube-auth-link", body=body, headers={
            "Authorization": "Bearer dashboard-secret",
            "Content-Type": "application/json",
        })
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()

        assert response.status == 201
        assert payload["account"] == "partner_revendedor"
        assert payload["ttl_seconds"] == 120
        assert youtube_auth_link_is_valid(parse_qs(urlparse(payload["url"]).query), now=payload["expires_at"] - 1)
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


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
    assert "media.write" in query["scope"][0]
    assert "offline.access" in query["scope"][0]
    assert "scope=tweet.read%20users.read%20tweet.write%20media.write%20offline.access" in url
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


def test_x_oauth_start_rejects_unknown_account_slot(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_X_CLIENT_ID", "x-client-id")
    monkeypatch.setenv("JAGUARTV_OAUTH_TOKEN_KEY", "token-encryption-key")

    with pytest.raises(ValueError, match="unknown X account slot"):
        x_oauth_start_url(config, "unexpected-account")


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


def test_x_oauth_callback_refreshes_existing_slot_instead_of_creating_duplicate(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_X_CLIENT_ID", "x-client-id")
    monkeypatch.setenv("JAGUARTV_OAUTH_TOKEN_KEY", "token-encryption-key")
    timestamp = now_iso()
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO x_account_auths(
          account,x_user_id,username,display_name,scopes,encrypted_access_token,
          encrypted_refresh_token,status,authorized_at,confirmed_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "consumer_main", "same-user", "same", "Same", "tweet.write media.write offline.access",
            "old-access", "old-refresh", "AUTHORIZED", timestamp, timestamp, timestamp,
        ),
    )
    connection.commit()
    state = parse_qs(urlparse(x_oauth_start_url(config, "consumer_guide")).query)["state"][0]
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.post_form_json",
        lambda *args, **kwargs: {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "scope": "tweet.read users.read tweet.write media.write offline.access",
        },
    )
    monkeypatch.setattr(
        "jaguartv_factory.dashboard.get_authorized_x_user",
        lambda access_token: {"x_user_id": "same-user", "username": "same", "display_name": "Same"},
    )

    result = save_x_oauth_callback(config, {"code": ["code"], "state": [state]})

    rows = x_auth_rows(config)
    assert result["account"] == "consumer_main"
    assert result["duplicate"] is True
    assert [row["account"] for row in rows] == ["consumer_main"]
    assert rows[0]["status"] == "AUTHORIZED"


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
            "tweet.write media.write offline.access", "encrypted-access", "encrypted-refresh",
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


def test_x_auth_confirmation_rejects_missing_publish_scope(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    timestamp = now_iso()
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO x_account_auths(
          account,x_user_id,username,display_name,scopes,encrypted_access_token,
          encrypted_refresh_token,status,authorized_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "consumer_guide", "x-1", "guide", "Guide", "tweet.write offline.access",
            "encrypted-access", "encrypted-refresh", "PENDING_CONFIRMATION", timestamp, timestamp,
        ),
    )
    connection.commit()

    with pytest.raises(ValueError, match="media.write"):
        update_x_auth(config, {"account": "consumer_guide", "action": "confirm"})


def test_x_auth_confirmation_rejects_duplicate_authorized_user(tmp_path: Path):
    config = dashboard_config(tmp_path)
    timestamp = now_iso()
    connection = connect_db(config)
    for account, status, confirmed_at in (
        ("consumer_main", "AUTHORIZED", timestamp),
        ("consumer_guide", "PENDING_CONFIRMATION", ""),
    ):
        connection.execute(
            """
            INSERT INTO x_account_auths(
              account,x_user_id,username,display_name,scopes,encrypted_access_token,
              encrypted_refresh_token,status,authorized_at,confirmed_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                account, "same-user", "same", "Same", "tweet.write media.write offline.access",
                "encrypted-access", "encrypted-refresh", status, timestamp, confirmed_at, timestamp,
            ),
        )
    connection.commit()

    with pytest.raises(ValueError, match="already authorized"):
        update_x_auth(config, {"account": "consumer_guide", "action": "confirm"})


def test_verify_x_auths_refreshes_every_authorized_account(tmp_path: Path, monkeypatch):
    config = dashboard_config(tmp_path)
    timestamp = now_iso()
    connection = connect_db(config)
    for account, user_id in (("consumer_main", "x-1"), ("consumer_football", "x-2")):
        connection.execute(
            """
            INSERT INTO x_account_auths(
              account,x_user_id,username,display_name,scopes,encrypted_access_token,
              encrypted_refresh_token,status,authorized_at,confirmed_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                account, user_id, account, account, "tweet.write media.write offline.access",
                "encrypted-access", "encrypted-refresh", "AUTHORIZED", timestamp, timestamp, timestamp,
            ),
        )
    connection.commit()
    refreshed = []
    monkeypatch.setattr(
        "jaguartv_factory.x_publisher.x_access_token",
        lambda _config, account: refreshed.append(account) or {"username": account, "source": "oauth_refresh"},
    )

    result = verify_x_auths(config)

    assert refreshed == ["consumer_main", "consumer_football"]
    assert result["verified"] == 2
    assert result["failed"] == 6
    by_account = {item["account"]: item for item in result["results"]}
    assert by_account["consumer_main"]["status"] == "AVAILABLE"
    assert by_account["consumer_football"]["status"] == "AVAILABLE"
    assert by_account["consumer_guide"]["status"] == "UNAVAILABLE"


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
