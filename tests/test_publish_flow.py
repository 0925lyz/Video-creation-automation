import json
from datetime import datetime
from pathlib import Path

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.publish_flow import (
    create_publish_operation,
    generate_publish_copy_preview,
    list_publish_accounts,
    platform_capabilities,
    validate_publish_copy,
)
from jaguartv_factory.youtube_publisher import publication_video_path
from jaguartv_factory.dashboard import publication_rows
from jaguartv_factory.publish_worker import publish_due_once


def config_for(tmp_path: Path) -> dict:
    return {"_root": str(tmp_path), "run": {"workspace": "workspace", "timezone": "America/Sao_Paulo"}}


def insert_candidate(config: dict, candidate_id: str = "cand-1", status: str = "APPROVED") -> None:
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
            "tiktok",
            f"src-{candidate_id}",
            f"https://example.test/{candidate_id}",
            "Demo source title",
            "Demo source description",
            20,
            100,
            "pt",
            80,
            status,
            json.dumps({"keyword": "Neymar melhores momentos", "initial_category": "足球类"}),
            timestamp,
            timestamp,
        ),
    )
    connection.commit()


def write_review_asset(config: dict, package_id: str = "cand-1") -> None:
    package = Path(config["_root"]) / "workspace" / "server_media" / "review" / package_id
    package.mkdir(parents=True, exist_ok=True)
    (package / "0803-YouTube-1-通用版.mp4").write_bytes(b"video")
    (package / "metadata.json").write_text(
        json.dumps(
            {
                "job_id": package_id,
                "source": {"platform": "tiktok", "title": "Origem", "description": "Fonte"},
                "output_variants": [
                    {
                        "variant": "通用版",
                        "path": str(package / "0803-YouTube-1-通用版.mp4"),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def authorize_youtube(config: dict, account: str = "consumer_football") -> None:
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO youtube_channel_auths(
          account,channel_id,channel_title,scopes,encrypted_refresh_token,
          token_type,expires_in,authorized_at,updated_at,metadata_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            account,
            "UC123",
            "Real Channel",
            "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
            "encrypted-secret",
            "Bearer",
            3600,
            timestamp,
            timestamp,
            json.dumps({"private_path": "/secret/not-returned"}),
        ),
    )
    connection.commit()


def authorize_x(
    config: dict,
    account: str = "consumer_football",
    *,
    user_id: str = "x-user-1",
    username: str = "jaguarfutebol",
) -> None:
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO x_account_auths(
          account,x_user_id,username,display_name,scopes,encrypted_access_token,
          encrypted_refresh_token,token_type,expires_in,status,authorized_at,confirmed_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            account,
            user_id,
            username,
            username,
            "tweet.read users.read tweet.write media.write offline.access",
            "encrypted-access",
            "encrypted-refresh",
            "bearer",
            7200,
            "AUTHORIZED",
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    connection.commit()


def insert_original_factory_item(config: dict, item_id: str = "a" * 32) -> Path:
    connection = connect_db(config)
    timestamp = now_iso()
    video = Path(config["_root"]) / "workspace/server_media/original_factory/videos" / f"{item_id}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"original-video-fixture")
    connection.execute(
        """INSERT INTO original_factory_items(
             id,name,file_key,thumbnail_key,original_name,mime_type,size_bytes,sha256,
             duration_sec,width,height,video_codec,generated_at,match_name,match_date,
             match_time_sao_paulo,channels_json,category,status,match_info_json,metadata_json,
             uploaded_by,approved_by,created_at,updated_at,approved_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'APPROVED',?,?,?,?,?,?,?)""",
        (
            item_id, "Palmeiras x Santos", f"videos/{item_id}.mp4", "", "match.mp4",
            "video/mp4", video.stat().st_size, "fixture-sha", 20, 1080, 1920, "h264",
            "2026-09-03T10:00:00-03:00", "Palmeiras x Santos", "2026-09-03",
            "2026-09-03T21:30:00-03:00", json.dumps(["Globo", "Premiere"]),
            "pre_match_prediction", json.dumps({"competition": "Brasileirao"}), "{}",
            "worker", "reviewer", timestamp, timestamp, timestamp,
        ),
    )
    connection.execute(
        """INSERT INTO original_factory_social_sources(
             id,item_id,source_url,platform,fetched_at,summary,confidence,uncertain,
             image_source_url,image_license_status,metadata_json,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "b" * 32, item_id, "https://www.youtube.com/watch?v=fixture", "youtube",
            "2026-09-03T09:00:00-03:00", "Treino confirmado pelo clube.", 0.9, 0,
            "", "not_collected", "{}", timestamp,
        ),
    )
    connection.commit()
    return video


def publish_payload(**overrides):
    tags = [f"#Conteudo{i}" for i in range(15)] + [
        "#JAGUARTV", "#JaguarTV", "#RecargaJAGUARTV", "#testeJAGUARTV", "#instalarJAGUARTV",
        "#baixarJAGUARTV", "#trocarUNITVporJAGUARTV", "#migrardaUNITVparaJAGUARTV",
        "#vantagensdaJAGUARTV", "#JAGUARTVvsUNITV", "#UNITVvsJAGUARTV",
    ]
    payload = {
        "candidate_id": "cand-1",
        "asset_id": "cand-1:0803-YouTube-1-通用版",
        "filename": "0803-YouTube-1-通用版.mp4",
        "variant": "通用版",
        "platform": "youtube",
        "account": "consumer_football",
        "schedule_mode": "scheduled",
        "scheduled_local_at": "2026-08-19T15:30",
        "title": "Esse lance mudou tudo",
        "description": " ".join(tags),
        "tags": tags,
    }
    payload.update(overrides)
    return payload


def test_publish_accounts_are_realtime_and_sanitized(tmp_path: Path):
    config = config_for(tmp_path)
    authorize_youtube(config)

    rows = list_publish_accounts(config, "youtube")

    assert rows == [
        {
            "id": "consumer_football",
            "platform": "youtube",
            "username": "Real Channel",
            "display_name": "Real Channel",
            "status": "AVAILABLE",
            "status_reason": "",
            "channel_id": "UC123",
            "authorized_at": rows[0]["authorized_at"],
            "updated_at": rows[0]["updated_at"],
        }
    ]
    assert "encrypted_refresh_token" not in rows[0]
    assert "private_path" not in json.dumps(rows, ensure_ascii=False)


def test_publication_rows_show_current_channel_name_not_internal_account(
    tmp_path: Path, monkeypatch
):
    config = config_for(tmp_path)
    authorize_youtube(config)
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO publications(
          candidate_id,asset_id,variant,platform,account,account_label,title,
          operation_type,status,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "cand-1",
            "cand-1:0803-YouTube-1-通用版",
            "通用版",
            "youtube",
            "consumer_football",
            "旧频道名",
            "旧标题",
            "PUBLICATION",
            "SCHEDULED",
            timestamp,
            timestamp,
        ),
    )
    connection.execute(
        "UPDATE youtube_channel_auths SET channel_title='Nuevo Canal Jaguar' WHERE account='consumer_football'"
    )
    connection.commit()

    rows = publication_rows(config)
    assert rows[0]["account"] == "consumer_football"
    assert rows[0]["account_label"] == "Nuevo Canal Jaguar"


def test_create_youtube_publish_operation_records_utc_and_is_idempotent(tmp_path: Path):
    config = config_for(tmp_path)
    insert_candidate(config)
    write_review_asset(config)
    authorize_youtube(config)

    first = create_publish_operation(config, publish_payload(), now=datetime.fromisoformat("2026-08-19T10:00:00-03:00"))
    second = create_publish_operation(config, publish_payload(), now=datetime.fromisoformat("2026-08-19T10:00:01-03:00"))

    assert first["publication_id"] == second["publication_id"]
    assert first["operation_type"] == "PUBLICATION"
    assert first["scheduled_local_at"] == "2026-08-19T15:30:00-03:00"
    assert first["scheduled_utc_at"] == "2026-08-19T18:30:00+00:00"
    connection = connect_db(config)
    count = connection.execute("SELECT COUNT(*) count FROM publications").fetchone()["count"]
    row = connection.execute("SELECT privacy_status,status,idempotency_key,title FROM publications").fetchone()
    assert count == 1
    assert row["privacy_status"] == "public"
    assert row["status"] == "SCHEDULED"
    assert row["idempotency_key"]
    assert row["title"] == "Esse lance mudou tudo"
    title = connection.execute("SELECT title FROM publications").fetchone()["title"]
    assert "#" not in title


def test_original_factory_reuses_copy_accounts_publication_table_and_worker_asset_path(
    tmp_path: Path, monkeypatch
):
    config = config_for(tmp_path)
    config["storage"] = {
        "root": "workspace/server_media",
        "original_factory_subdir": "original_factory",
    }
    item_id = "a" * 32
    video = insert_original_factory_item(config, item_id)
    authorize_youtube(config)
    monkeypatch.delenv("JAGUARTV_DEEPSEEK_API_KEY", raising=False)

    preview = generate_publish_copy_preview(
        config,
        {
            "source_kind": "original_factory",
            "candidate_id": item_id,
            "asset_id": item_id,
            "filename": "palmeiras-santos.mp4",
            "variant": "赛前预测",
            "platform": "youtube",
            "actor": "reviewer",
        },
    )
    assert "Palmeiras x Santos" in preview["title"]
    assert "03/09" in preview["title"]
    result = create_publish_operation(
        config,
        publish_payload(
            source_kind="original_factory",
            candidate_id=item_id,
            asset_id=item_id,
            filename="palmeiras-santos.mp4",
            variant="赛前预测",
            title=preview["title"],
            description=" ".join(preview["tags"]),
            tags=preview["tags"],
        ),
        now=datetime.fromisoformat("2026-08-19T10:00:00-03:00"),
    )

    row = dict(connect_db(config).execute(
        "SELECT * FROM publications WHERE id=?", (result["publication_id"],)
    ).fetchone())
    assert row["publication_origin"] == "ORIGINAL_FACTORY"
    assert row["source_category"] == "pre_match_prediction"
    assert row["source_keyword"] == "Palmeiras x Santos"
    assert publication_video_path(config, row) == video.resolve()
    item = connect_db(config).execute(
        "SELECT publish_status,last_publication_id FROM original_factory_items WHERE id=?", (item_id,)
    ).fetchone()
    assert tuple(item) == ("SCHEDULED", result["publication_id"])
    generation = connect_db(config).execute(
        "SELECT platform,model,source_snapshot_json FROM original_factory_copy_generations WHERE item_id=?",
        (item_id,),
    ).fetchone()
    assert generation["platform"] == "youtube"
    assert "Palmeiras x Santos" in generation["source_snapshot_json"]

    worker = publish_due_once(
        config,
        uploader=lambda _config, _publication_id: {
            "youtube_video_id": "qa-original-video",
            "youtube_url": "https://www.youtube.com/watch?v=qa-original-video",
            "published_at": "2026-08-19T16:00:00-03:00",
        },
        now=datetime.fromisoformat("2026-08-19T16:00:00-03:00"),
    )
    assert worker["published"] == 1
    item = connect_db(config).execute(
        "SELECT publish_status,last_publication_id FROM original_factory_items WHERE id=?", (item_id,)
    ).fetchone()
    assert tuple(item) == ("PUBLISHED", result["publication_id"])


def test_create_publish_operation_rejects_unapproved_candidate(tmp_path: Path):
    config = config_for(tmp_path)
    insert_candidate(config, status="READY_FOR_REVIEW")
    write_review_asset(config)
    authorize_youtube(config)

    with pytest.raises(ValueError, match="APPROVED"):
        create_publish_operation(config, publish_payload(), now=datetime.fromisoformat("2026-08-19T10:00:00-03:00"))


def test_non_youtube_publish_operation_creates_local_download_claim(tmp_path: Path):
    config = config_for(tmp_path)
    insert_candidate(config)
    write_review_asset(config)

    result = create_publish_operation(
        config,
        publish_payload(
            platform="facebook", account="", schedule_mode="now",
            title="", description="Esse lance merece atenção #Futebol #Brasil", tags=[],
        ),
        now=datetime.fromisoformat("2026-08-19T10:00:00-03:00"),
    )

    assert result["operation_type"] == "LOCAL_DOWNLOAD"
    assert result["status"] == "DOWNLOAD_READY"
    assert result["download_url"].endswith("download=1")
    connection = connect_db(config)
    claim = connection.execute("SELECT * FROM download_claims").fetchone()
    assert claim["publish_platform"] == "facebook"
    assert claim["publisher"] == "local_download"


def test_platform_capabilities_keep_youtube_publish_config_in_one_place():
    capabilities = platform_capabilities()

    assert capabilities["youtube"]["operation_type"] == "PUBLICATION"
    assert capabilities["youtube"]["requires_account"] is True
    assert capabilities["facebook"]["operation_type"] == "LOCAL_DOWNLOAD"


def test_x_accounts_require_distinct_users_and_publish_scopes(tmp_path: Path):
    config = config_for(tmp_path)
    authorize_x(config, "consumer_main", user_id="x-user-main", username="JaguarTVBrasil")
    authorize_x(config, "consumer_football", user_id="x-user-shared", username="jaguarfutebol")
    authorize_x(config, "consumer_guide", user_id="x-user-shared", username="jaguarfutebol")

    rows = list_publish_accounts(config, "x")
    by_id = {row["id"]: row for row in rows}

    assert by_id["consumer_main"]["status"] == "AVAILABLE"
    assert by_id["consumer_football"]["status"] == "UNAVAILABLE"
    assert by_id["consumer_guide"]["status"] == "UNAVAILABLE"
    assert "同一 X 账号" in by_id["consumer_guide"]["status_reason"]
    assert "encrypted" not in json.dumps(rows, ensure_ascii=False)


def test_revoked_duplicate_x_authorization_does_not_block_active_account(tmp_path: Path):
    config = config_for(tmp_path)
    authorize_x(config, "consumer_football", user_id="x-user-1", username="jaguarfutebol")
    authorize_x(config, "partner_academia", user_id="x-user-1", username="jaguarfutebol")
    connection = connect_db(config)
    connection.execute(
        "UPDATE x_account_auths SET status='REVOKED',confirmed_at='' WHERE account='partner_academia'"
    )
    connection.commit()

    rows = {row["id"]: row for row in list_publish_accounts(config, "x")}

    assert rows["consumer_football"]["status"] == "AVAILABLE"
    assert rows["partner_academia"]["status"] == "UNAVAILABLE"


def test_create_x_publish_operation_uses_real_authorized_account(tmp_path: Path):
    config = config_for(tmp_path)
    insert_candidate(config)
    write_review_asset(config)
    authorize_x(config)

    result = create_publish_operation(
        config,
        publish_payload(
            platform="x",
            title="Esse lance mudou tudo",
            description="Veja o momento e conte o que você achou.",
            tags=["Futebol", "Brasil", "Jaguar TV"],
            schedule_mode="now",
        ),
        now=datetime.fromisoformat("2026-08-19T10:00:00-03:00"),
    )

    assert result["operation_type"] == "PUBLICATION"
    assert result["status"] == "QUEUED"
    assert result["platform_account_id"] == "x-user-1"
    assert result["platform_username_snapshot"] == "jaguarfutebol"


def test_validate_x_copy_enforces_combined_250_character_limit():
    with pytest.raises(ValueError, match="250"):
        validate_publish_copy(
            "x",
            {
                "title": "",
                "description": "D" * 251,
                "tags": [],
            },
        )


def test_validate_publish_copy_enforces_platform_limits():
    with pytest.raises(ValueError, match="title"):
        validate_publish_copy("youtube", {"title": "x" * 91, "description": "#ok", "tags": ["ok"] * 25})
    with pytest.raises(ValueError, match="tags"):
        validate_publish_copy("youtube", {"title": "ok", "description": "ok", "tags": [str(i) for i in range(60)]})


def test_youtube_uses_tags_as_description_when_copy_field_is_blank():
    tags = [f"#Tag{i}" for i in range(25)]

    result = validate_publish_copy(
        "youtube", {"title": "Titulo em portugues", "description": "", "tags": tags}
    )

    assert result["description"] == " ".join(tags)


def test_generate_publish_copy_preview_uses_deepseek_and_platform_limits(
    tmp_path: Path, monkeypatch
):
    config = config_for(tmp_path)
    config["publishing"] = {"copywriter": {"deepseek_base_url": "https://ds.example.test/v1"}}
    insert_candidate(config)
    write_review_asset(config)
    monkeypatch.setenv("JAGUARTV_DEEPSEEK_API_KEY", "ds-test")
    monkeypatch.setenv("JAGUARTV_DEEPSEEK_MODEL", "deepseek-v4-flash")

    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "Esse lance virou assunto",
                                    "description": "Um momento perfeito para assistir e comentar.",
                                    "tags": ["Jaguar TV", "Futebol", "Brasil"],
                                }
                            )
                        }
                    }
                ]
            }

    def fake_post(url, *, headers, data, timeout):
        calls.append((url, headers, json.loads(data.decode("utf-8"))))
        return FakeResponse()

    monkeypatch.setattr("jaguartv_factory.publish_flow.requests.post", fake_post)

    result = generate_publish_copy_preview(
        config,
        {
            "candidate_id": "cand-1",
            "asset_id": "cand-1:0803-YouTube-1-通用版",
            "platform": "youtube",
            "variant": "通用版",
        },
    )

    assert result["title"] == "Esse lance virou assunto"
    assert "#" not in result["title"]
    assert len(result["tags"]) >= 25
    assert all(tag.startswith("#") for tag in result["tags"])
    assert len([tag for tag in result["tags"] if "jaguar" in tag.lower() or "unitv" in tag.lower() or "tv" in tag.lower()]) >= 10
    assert result["description"] == " ".join(result["tags"])
    assert calls[0][0] == "https://ds.example.test/v1/chat/completions"
    assert calls[0][2]["model"] == "deepseek-v4-flash"
    assert calls[0][1]["Authorization"] == "Bearer ds-test"


def test_generate_publish_copy_accepts_custom_hint_without_provenance(
    tmp_path: Path, monkeypatch
):
    config = config_for(tmp_path)
    config["publishing"] = {"copywriter": {"deepseek_base_url": "https://ds.example.test/v1"}}
    insert_candidate(config, status="APPROVED")
    connection = connect_db(config)
    connection.execute(
        "UPDATE candidates SET metadata_json='{}',title='',description='' WHERE id='cand-1'"
    )
    connection.commit()
    write_review_asset(config)
    metadata_path = (
        Path(config["_root"]) / "workspace" / "server_media" / "review" / "cand-1" / "metadata.json"
    )
    metadata_path.write_text(
        json.dumps(
            {
                "job_id": "cand-1",
                "output_variants": [
                    {
                        "variant": "通用版",
                        "path": str(
                            Path(config["_root"])
                            / "workspace"
                            / "server_media"
                            / "review"
                            / "cand-1"
                            / "0803-YouTube-1-通用版.mp4"
                        ),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JAGUARTV_DEEPSEEK_API_KEY", "ds-test")
    monkeypatch.setenv("JAGUARTV_DEEPSEEK_MODEL", "deepseek-v4-flash")
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "Aprenda a instalar em minutos",
                                    "description": "Um passo a passo rápido para instalar.",
                                    "tags": ["Tutorial", "Brasil"],
                                }
                            )
                        }
                    }
                ]
            }

    def fake_post(url, *, headers, data, timeout):
        captured["url"] = url
        captured["body"] = json.loads(data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("jaguartv_factory.publish_flow.requests.post", fake_post)

    result = generate_publish_copy_preview(
        config,
        {
            "candidate_id": "cand-1",
            "asset_id": "cand-1:0803-YouTube-1-通用版",
            "platform": "youtube",
            "variant": "通用版",
            "hint": "这是一段巴西安装APP的教程短视频，标题要突出快",
        },
    )

    assert result["title"] == "Aprenda a instalar em minutos"
    assert "巴西安装APP的教程短视频" in json.dumps(captured["body"]["messages"], ensure_ascii=False)


def test_generate_publish_copy_falls_back_to_deterministic_when_deepseek_unavailable(
    tmp_path: Path, monkeypatch
):
    config = config_for(tmp_path)
    config["publishing"] = {
        "copywriter": {
            "deepseek_base_url": "https://ds.example.test/v1",
        }
    }
    insert_candidate(config)
    write_review_asset(config)
    monkeypatch.setenv("JAGUARTV_DEEPSEEK_API_KEY", "ds-test")
    calls: list[str] = []

    class DeepSeekFailure:
        status_code = 503

        def json(self):
            return {}

    def fake_post(url, **kwargs):
        calls.append(url)
        return DeepSeekFailure()

    monkeypatch.setattr("jaguartv_factory.publish_flow.requests.post", fake_post)

    result = generate_publish_copy_preview(
        config,
        {
            "candidate_id": "cand-1",
            "asset_id": "cand-1:0803-YouTube-1-通用版",
            "platform": "youtube",
            "variant": "通用版",
        },
    )

    assert result["model"] == "deterministic-provenance"
    assert result["fallback_reason"] == "deepseek_unavailable"
    assert calls[0].endswith("/chat/completions")


def test_generate_publish_copy_uses_deepseek_primary_without_openai(
    tmp_path: Path, monkeypatch
):
    config = config_for(tmp_path)
    config["publishing"] = {"copywriter": {"deepseek_base_url": "https://ds.example.test/v1"}}
    insert_candidate(config)
    write_review_asset(config)
    monkeypatch.delenv("JAGUARTV_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("JAGUARTV_DEEPSEEK_API_KEY", "ds-test")
    monkeypatch.setenv("JAGUARTV_DEEPSEEK_MODEL", "deepseek-v4-flash")

    class DeepSeekResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "Esse lance é surreal",
                                    "description": "Veja o momento que parou a torcida.",
                                    "tags": ["Futebol", "Brasil"],
                                }
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr(
        "jaguartv_factory.publish_flow.requests.post",
        lambda *args, **kwargs: DeepSeekResponse(),
    )

    result = generate_publish_copy_preview(
        config,
        {
            "candidate_id": "cand-1",
            "asset_id": "cand-1:0803-YouTube-1-通用版",
            "platform": "youtube",
            "variant": "通用版",
        },
    )

    assert result["model"] == "deepseek-v4-flash"
    assert "fallback_reason" not in result


def test_generate_publish_copy_falls_back_when_deepseek_not_configured(
    tmp_path: Path, monkeypatch
):
    config = config_for(tmp_path)
    insert_candidate(config)
    write_review_asset(config)
    monkeypatch.delenv("JAGUARTV_DEEPSEEK_API_KEY", raising=False)

    result = generate_publish_copy_preview(
        config,
        {"candidate_id": "cand-1", "asset_id": "cand-1:0803-YouTube-1-通用版", "platform": "youtube"},
    )

    assert result["model"] == "deterministic-provenance"
    assert result["fallback_reason"] == "deepseek_not_configured"


def test_generate_publish_copy_without_ai_key_uses_provenance_fallback(tmp_path: Path, monkeypatch):
    config = config_for(tmp_path)
    insert_candidate(config)
    write_review_asset(config)
    monkeypatch.delenv("JAGUARTV_DEEPSEEK_API_KEY", raising=False)

    result = generate_publish_copy_preview(
        config,
        {
            "candidate_id": "cand-1",
            "asset_id": "cand-1:0803-YouTube-1-通用版",
            "platform": "youtube",
            "variant": "通用版",
        },
    )

    assert result["model"] == "deterministic-provenance"
    assert result["title"]
    assert result["title"] == "Esse lance de futebol merece ser visto até o fim"
    assert "Demo source title" not in result["title"]
    assert len(result["title"]) <= 90
    assert "#" not in result["title"]
    assert len(result["tags"]) >= 25
    assert result["description"] == " ".join(result["tags"])


def test_generate_publish_copy_accepts_legacy_package_with_approved_review_file(
    tmp_path: Path, monkeypatch
):
    config = config_for(tmp_path)
    insert_candidate(config, status="READY_FOR_REVIEW")
    write_review_asset(config)
    review_file = (
        Path(config["_root"])
        / "workspace"
        / "server_media"
        / "review"
        / "cand-1"
        / "review.json"
    )
    review_file.write_text(json.dumps({"decision": "approved"}), encoding="utf-8")
    monkeypatch.delenv("JAGUARTV_DEEPSEEK_API_KEY", raising=False)

    result = generate_publish_copy_preview(
        config,
        {
            "candidate_id": "cand-1",
            "asset_id": "cand-1:0803-YouTube-1-通用版",
            "platform": "youtube",
            "variant": "通用版",
        },
    )

    assert result["title"]
    assert len(result["tags"]) >= 25


def test_generate_publish_copy_rejects_nonapproved_legacy_review_file(tmp_path: Path):
    config = config_for(tmp_path)
    insert_candidate(config, status="READY_FOR_REVIEW")
    write_review_asset(config)
    review_file = (
        Path(config["_root"])
        / "workspace"
        / "server_media"
        / "review"
        / "cand-1"
        / "review.json"
    )
    review_file.write_text(json.dumps({"decision": "revision_required"}), encoding="utf-8")

    with pytest.raises(ValueError, match="APPROVED"):
        generate_publish_copy_preview(
            config,
            {
                "candidate_id": "cand-1",
                "asset_id": "cand-1:0803-YouTube-1-通用版",
                "platform": "youtube",
                "variant": "通用版",
            },
        )


@pytest.mark.parametrize("platform", ["x", "facebook", "tiktok"])
def test_social_copy_is_one_combined_pt_br_field_under_250_chars(tmp_path: Path, monkeypatch, platform: str):
    config = config_for(tmp_path)
    insert_candidate(config)
    write_review_asset(config)
    monkeypatch.delenv("JAGUARTV_DEEPSEEK_API_KEY", raising=False)

    result = generate_publish_copy_preview(
        config,
        {
            "candidate_id": "cand-1",
            "asset_id": "cand-1:0803-YouTube-1-通用版",
            "platform": platform,
            "variant": "通用版",
        },
    )

    assert result["title"] == ""
    assert result["tags"] == []
    assert result["description"]
    assert "#" in result["description"]
    assert len(result["description"]) <= 250


def test_publish_dialog_uses_final_field_names_and_platform_switching():
    html = Path("src/jaguartv_factory/web/index.html").read_text(encoding="utf-8")
    javascript = Path("src/jaguartv_factory/web/app.js").read_text(encoding="utf-8")

    assert 'id="publishHint"' in html
    assert "publishHint" in javascript
    assert "hint: document.querySelector(\"#publishHint\")?.value.trim() || \"\"" in javascript
    assert "item.account_label || item.account || \"未指定\"" in javascript
    assert 'app.js?v=20260831-original-factory-v1' in html
    assert 'source_kind: asset.source_kind || "candidate"' in javascript
    styles = Path("src/jaguartv_factory/web/styles.css").read_text(encoding="utf-8")

    assert "AI 标题" not in html
    assert "AI 文案" not in html
    assert "标题文案" in html
    assert "说明标签" in html
    assert "文案标签" in html
    assert 'new Set(["x", "facebook", "tiktok", "instagram", "kwai"])' in javascript
    assert "configurePublishCopyFields" in javascript
    assert 'candidate_id: String(asset.id).split(":", 1)[0]' in javascript
    assert 'document.querySelector("#publishDescription").value = platform === "youtube" ? ""' in javascript
    assert "[hidden] { display: none !important; }" in styles


def test_inventory_uses_compact_rows_and_concise_publication_status():
    javascript = Path("src/jaguartv_factory/web/app.js").read_text(encoding="utf-8")
    styles = Path("src/jaguartv_factory/web/styles.css").read_text(encoding="utf-8")

    assert "计划发布时间" not in javascript
    assert "${escapeHtml(state.youtube_video_id)}" not in javascript
    assert "已排队发布至 YouTube 账号" in javascript
    assert ".inventory-table th, .inventory-table td { padding-top: 7px; padding-bottom: 7px; }" in styles
    assert ".output-menu-row { min-height: 27px; padding: 2px 0;" in styles


def test_x_account_controls_use_real_accounts_without_content_roles():
    html = Path("src/jaguartv_factory/web/index.html").read_text(encoding="utf-8")
    javascript = Path("src/jaguartv_factory/web/app.js").read_text(encoding="utf-8")

    assert "const xAccountSlots" in javascript
    assert "JaguarTV Hoje" not in javascript
    assert "JaguarTV Futebol" not in javascript
    assert 'username ? `@${item.username}`' in javascript
    assert " · ${escapeHtml(item.status" not in javascript
    assert 'id="xAuthVerifyAll"' in html
    assert 'data-x-auth-action="start"' in javascript
    assert 'action: "verify_all"' in javascript
