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


def publish_payload(**overrides):
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
        "description": "Um momento forte para rever e comentar.",
        "tags": ["Jaguar TV", "Futebol", "Brasil"],
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
    assert row["title"] == "Esse lance mudou tudo #Futebol #Brasil"
    title = connection.execute("SELECT title FROM publications").fetchone()["title"]
    assert "#Futebol" in title
    assert "#Brasil" in title


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
        publish_payload(platform="facebook", account="", schedule_mode="now"),
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


def test_validate_publish_copy_enforces_platform_limits():
    with pytest.raises(ValueError, match="title"):
        validate_publish_copy("youtube", {"title": "x" * 101, "description": "ok", "tags": ["Jaguar TV"]})
    with pytest.raises(ValueError, match="tags"):
        validate_publish_copy("youtube", {"title": "ok", "description": "ok", "tags": [str(i) for i in range(30)]})


def test_generate_publish_copy_preview_uses_openai_and_platform_limits(tmp_path: Path, monkeypatch):
    config = config_for(tmp_path)
    config["publishing"] = {"copywriter": {"base_url": "https://relay.example.test/v1"}}
    insert_candidate(config)
    write_review_asset(config)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "output_text": json.dumps(
                    {
                        "title": "Esse lance virou assunto",
                        "description": "Um momento perfeito para assistir e comentar.",
                        "tags": ["Jaguar TV", "Futebol", "Brasil"],
                    }
                )
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

    assert result["title"] == "Esse lance virou assunto #Futebol #Brasil #NeymarMelhoresMomentos"
    assert result["tags"] == ["Jaguar TV", "Futebol", "Brasil"]
    assert calls[0][0] == "https://relay.example.test/v1/responses"
    assert calls[0][2]["model"] == "gpt-5.5"
    assert calls[0][2]["reasoning"]["effort"] == "high"
    assert calls[0][1]["Authorization"] == "Bearer sk-test"
