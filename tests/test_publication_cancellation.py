from __future__ import annotations

from pathlib import Path

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.publication_cancellation import cancel_publication
from jaguartv_factory.x_publisher import delete_x_publication
from jaguartv_factory.youtube_publisher import delete_youtube_publication


def cancellation_config(tmp_path: Path) -> dict:
    return {"_root": str(tmp_path), "run": {"workspace": "workspace"}}


def add_candidate(config: dict, candidate_id: str = "candidate-1") -> None:
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO candidates(
          id,platform,source_id,url,title,description,duration,view_count,
          detected_language,score,status,metadata_json,created_at,updated_at,published_flag
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate_id,
            "facebook",
            f"{candidate_id}-source",
            "https://example.test/video",
            "Demo",
            "",
            20,
            100,
            "pt",
            80,
            "APPROVED",
            "{}",
            timestamp,
            timestamp,
            1,
        ),
    )
    connection.commit()


def add_publication(
    config: dict,
    *,
    status: str,
    origin: str = "SYSTEM_AUTO_PUBLISH",
    platform: str = "youtube",
) -> int:
    connection = connect_db(config)
    timestamp = now_iso()
    cursor = connection.execute(
        """
        INSERT INTO publications(
          candidate_id,platform,account,status,operation_type,review_status,
          youtube_video_id,platform_video_id,publication_origin,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "candidate-1",
            platform,
            "account-a",
            status,
            "PUBLICATION",
            "APPROVED",
            "youtube-123" if platform == "youtube" else "",
            "youtube-123" if platform == "youtube" else "x-123",
            origin,
            timestamp,
            timestamp,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def test_cancel_queued_publication_never_calls_remote_platform(tmp_path: Path):
    config = cancellation_config(tmp_path)
    add_candidate(config)
    publication_id = add_publication(config, status="QUEUED")
    remote_calls: list[int] = []

    result = cancel_publication(
        config,
        publication_id,
        actor="operator",
        remote_delete=lambda _config, publication: remote_calls.append(publication["id"]),
    )

    row = connect_db(config).execute(
        "SELECT status FROM publications WHERE id=?", (publication_id,)
    ).fetchone()
    assert result == {"publication_id": publication_id, "status": "CANCELLED", "remote_deleted": False}
    assert row["status"] == "CANCELLED"
    assert remote_calls == []


def test_cancel_published_factory_video_deletes_remote_and_updates_inventory(tmp_path: Path):
    config = cancellation_config(tmp_path)
    add_candidate(config)
    publication_id = add_publication(config, status="PUBLISHED")
    remote_calls: list[dict] = []

    result = cancel_publication(
        config,
        publication_id,
        actor="operator",
        remote_delete=lambda _config, publication: remote_calls.append(publication),
    )

    connection = connect_db(config)
    publication = connection.execute(
        "SELECT status FROM publications WHERE id=?", (publication_id,)
    ).fetchone()
    candidate = connection.execute(
        "SELECT published_flag FROM candidates WHERE id='candidate-1'"
    ).fetchone()
    assert result == {"publication_id": publication_id, "status": "CANCELLED", "remote_deleted": True}
    assert publication["status"] == "CANCELLED"
    assert candidate["published_flag"] == 0
    assert remote_calls[0]["youtube_video_id"] == "youtube-123"


def test_cancel_rejects_imported_or_in_progress_publications(tmp_path: Path):
    config = cancellation_config(tmp_path)
    add_candidate(config)
    imported_id = add_publication(config, status="PUBLISHED", origin="YOUTUBE_CHANNEL_IMPORT")
    in_progress_id = add_publication(config, status="PUBLISHING")

    with pytest.raises(ValueError, match="factory-created"):
        cancel_publication(config, imported_id)
    with pytest.raises(ValueError, match="currently publishing"):
        cancel_publication(config, in_progress_id)


def test_publication_queue_ui_exposes_cancel_action() -> None:
    web = Path(__file__).parents[1] / "src" / "jaguartv_factory" / "web"
    app_js = (web / "app.js").read_text(encoding="utf-8")

    assert "data-publication-cancel" in app_js
    assert "/cancel" in app_js


def test_youtube_remote_delete_uses_authorized_account(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    class Session:
        @staticmethod
        def delete(url: str, *, params: dict, headers: dict, timeout: int):
            calls.append((url, params, headers))
            return type("Response", (), {"status_code": 204})()

    monkeypatch.setattr(
        "jaguartv_factory.youtube_publisher.youtube_access_token",
        lambda _config, account: {"access_token": f"token-for-{account}"},
    )

    delete_youtube_publication(
        cancellation_config(tmp_path),
        {"account": "account-a", "youtube_video_id": "youtube-123"},
        session=Session,
    )

    assert calls[0][1] == {"id": "youtube-123"}
    assert calls[0][2]["Authorization"] == "Bearer token-for-account-a"


def test_x_remote_delete_requires_platform_confirmation(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"data": {"deleted": True}}

    class Session:
        @staticmethod
        def delete(url: str, *, headers: dict, timeout: int):
            calls.append(url)
            assert headers["Authorization"] == "Bearer x-token"
            return Response()

    monkeypatch.setattr(
        "jaguartv_factory.x_publisher.x_access_token",
        lambda _config, _account: {"access_token": "x-token"},
    )

    delete_x_publication(
        cancellation_config(tmp_path),
        {"account": "account-a", "platform_video_id": "x-123"},
        session=Session,
    )

    assert calls == ["https://api.x.com/2/tweets/x-123"]
