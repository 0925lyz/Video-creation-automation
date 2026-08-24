from __future__ import annotations

import json
from pathlib import Path

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.publish_worker import publish_due_once
from jaguartv_factory.x_publisher import (
    create_x_post,
    upload_x_video,
    x_post_text,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []
        self.status_checks = 0

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        data = kwargs.get("data") or {}
        if data.get("command") == "INIT":
            return FakeResponse(202, {"data": {"id": "media-1"}})
        if data.get("command") == "APPEND":
            return FakeResponse(204, {})
        if data.get("command") == "FINALIZE":
            return FakeResponse(201, {"data": {"id": "media-1", "processing_info": {"state": "pending", "check_after_secs": 0}}})
        if url.endswith("/2/tweets"):
            return FakeResponse(201, {"data": {"id": "post-1", "text": kwargs["json"]["text"]}})
        raise AssertionError((url, kwargs))

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        self.status_checks += 1
        return FakeResponse(200, {"data": {"id": "media-1", "processing_info": {"state": "succeeded"}}})


def test_x_post_text_contains_hook_copy_and_hashtags_under_limit():
    text = x_post_text(
        "Esse lance mudou tudo",
        "Veja o momento e conte o que você achou.",
        ["Jaguar TV", "Futebol", "Brasil"],
    )

    assert text.startswith("Esse lance mudou tudo")
    assert "#JaguarTV" in text
    assert "#Futebol" in text
    assert len(text) <= 280


def test_x_post_text_rejects_content_that_cannot_fit():
    with pytest.raises(ValueError, match="280"):
        x_post_text("T" * 100, "D" * 200, ["Jaguar TV", "Futebol"])


def test_chunked_video_upload_and_post_creation(tmp_path: Path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"abcdefghij")
    session = FakeSession()

    media_id = upload_x_video(
        "access-token",
        video,
        session=session,
        chunk_size=4,
        sleep=lambda _seconds: None,
    )
    result = create_x_post("access-token", "Texto curto #Futebol", media_id, session=session)

    append_calls = [call for call in session.calls if call[2].get("data", {}).get("command") == "APPEND"]
    assert media_id == "media-1"
    assert len(append_calls) == 3
    assert [call[2]["data"]["segment_index"] for call in append_calls] == [0, 1, 2]
    assert session.status_checks == 1
    assert result == {"x_post_id": "post-1", "x_url": "https://x.com/i/web/status/post-1"}


def test_x_worker_records_public_url_without_youtube_sync(tmp_path: Path):
    config = {"_root": str(tmp_path), "run": {"workspace": "workspace", "timezone": "America/Sao_Paulo"}}
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO candidates(id,platform,url,title,status,metadata_json,created_at,updated_at)
        VALUES('candidate-x','facebook','https://facebook.test/1','Demo','APPROVED','{}',?,?)
        """,
        (timestamp, timestamp),
    )
    connection.execute(
        """
        INSERT INTO x_account_auths(
          account,x_user_id,username,display_name,scopes,encrypted_access_token,
          encrypted_refresh_token,status,authorized_at,confirmed_at,updated_at
        ) VALUES('consumer_main','x-user-main','JaguarTVBrasil','JaguarTV Brasil',
          'tweet.read users.read tweet.write media.write offline.access','a','r','AUTHORIZED',?,?,?)
        """,
        (timestamp, timestamp, timestamp),
    )
    connection.execute(
        """
        INSERT INTO publications(
          candidate_id,platform,account,account_label,platform_account_id,
          scheduled_at,scheduled_utc_at,operation_type,status,privacy_status,
          public_status,title,description,tags_json,created_at,updated_at
        ) VALUES('candidate-x','x','consumer_main','JaguarTVBrasil','x-user-main',
          ?,?,'PUBLICATION','QUEUED','public','public','Hook','Copy','["Futebol"]',?,?)
        """,
        (timestamp, timestamp, timestamp, timestamp),
    )
    publication_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.commit()

    result = publish_due_once(
        config,
        uploader=lambda _config, _publication_id: {
            "x_post_id": "post-1",
            "x_url": "https://x.com/i/web/status/post-1",
            "published_at": timestamp,
        },
    )

    row = connect_db(config).execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    assert result["published"] == 1
    assert row["status"] == "PUBLISHED"
    assert row["platform_video_id"] == "post-1"
    assert row["public_url"] == "https://x.com/i/web/status/post-1"
    assert connect_db(config).execute("SELECT COUNT(*) FROM youtube_sync_states").fetchone()[0] == 0
