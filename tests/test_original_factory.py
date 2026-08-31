import io
import base64
import http.client
import json
import subprocess
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

from jaguartv_factory.core import connect_db
from jaguartv_factory.dashboard import DashboardApplication
from jaguartv_factory.original_factory import (
    ORIGINAL_CATEGORIES,
    OriginalConflict,
    OriginalForbidden,
    approve_originals,
    delete_originals,
    import_original_video,
    list_original_items,
    original_counts,
    original_detail,
    original_publish_source_material,
    register_original_download,
    resolve_original_file,
)


def original_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace", "timezone": "America/Sao_Paulo"},
        "storage": {
            "root": "workspace/server_media",
            "original_factory_subdir": "original_factory",
            "original_max_upload_bytes": 20 * 1024 * 1024,
            "original_max_duration_sec": 120,
            "original_max_pixels": 8_500_000,
            "original_max_dimension": 4096,
        },
    }


def video_bytes(tmp_path: Path, *, width: int = 320, height: int = 180) -> bytes:
    destination = tmp_path / f"fixture-{width}x{height}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x087f5b:s={width}x{height}:d=0.4",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(destination),
        ],
        check=True,
        capture_output=True,
    )
    return destination.read_bytes()


def import_fixture(config: dict, tmp_path: Path, **overrides):
    payload = overrides.pop("payload", video_bytes(tmp_path))
    values = {
        "filename": "palmeiras-santos.mp4",
        "mime_type": "video/mp4",
        "content_length": len(payload),
        "category": "pre_match_prediction",
        "match_name": "Palmeiras x Santos",
        "match_date": "2026-09-03",
        "match_time_sao_paulo": "2026-09-03T21:30:00-03:00",
        "channels": ["Globo", "Premiere"],
        "match_info": {
            "competition": "Brasileirao",
            "home_team": "Palmeiras",
            "away_team": "Santos",
            "source_url": "https://example.test/matches/palmeiras-santos",
        },
        "social_sources": [
            {
                "source_url": "https://www.youtube.com/watch?v=fixture123",
                "platform": "youtube",
                "fetched_at": "2026-09-03T09:00:00-03:00",
                "summary": "Coletiva confirma treino com elenco principal.",
                "confidence": 0.85,
                "uncertain": False,
                "image_source_url": "",
                "image_license_status": "not_collected",
            },
            {
                "source_url": "https://example.test/possible-lineup",
                "platform": "web",
                "fetched_at": "2026-09-03T09:10:00-03:00",
                "summary": "Possivel escalação ainda sem confirmação oficial.",
                "confidence": 0.4,
                "uncertain": True,
                "image_source_url": "https://example.test/player-photo",
                "image_license_status": "unknown",
            },
        ],
        "generated_at": "2026-09-03T10:00:00-03:00",
        "actor": "daily-original-worker",
        "request_id": "upload-fixture-1",
    }
    values.update(overrides)
    return import_original_video(config, io.BytesIO(payload), **values)


def test_original_factory_schema_uses_shared_database(tmp_path: Path):
    connection = connect_db(original_config(tmp_path))
    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "original_factory_items",
        "original_factory_social_sources",
        "original_factory_audit_events",
        "original_factory_copy_generations",
    } <= tables
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(original_factory_items)")
    }
    assert {
        "file_key", "thumbnail_key", "generated_at", "match_name", "match_date",
        "match_time_sao_paulo", "channels_json", "category", "status",
        "publish_status", "download_status", "match_info_json", "approved_at",
        "deleted_at",
    } <= columns


def test_valid_video_import_is_persisted_pending_review_with_structured_sources(tmp_path: Path):
    config = original_config(tmp_path)
    item = import_fixture(config, tmp_path)

    assert item["status_id"] == "PENDING_REVIEW"
    assert item["category_id"] == "pre_match_prediction"
    assert item["match_name"] == "Palmeiras x Santos"
    assert item["channels"] == ["Globo", "Premiere"]
    assert item["duration_sec"] > 0
    assert item["width"] == 320 and item["height"] == 180
    assert resolve_original_file(config, item["id"]).is_file()
    assert resolve_original_file(config, item["id"], thumbnail=True).is_file()
    detail = original_detail(config, item["id"])
    assert len(detail["social_sources"]) == 2
    assert sum(source["uncertain"] for source in detail["social_sources"]) == 1
    audit = connect_db(config).execute(
        "SELECT action,to_status,actor FROM original_factory_audit_events WHERE item_id=?",
        (item["id"],),
    ).fetchone()
    assert tuple(audit) == ("IMPORT", "PENDING_REVIEW", "daily-original-worker")


def test_machine_import_retry_with_same_request_id_is_idempotent(tmp_path: Path):
    config = original_config(tmp_path)
    payload = video_bytes(tmp_path)
    first = import_fixture(config, tmp_path, payload=payload, request_id="stable-daily-upload")
    retried = import_fixture(config, tmp_path, payload=payload, request_id="stable-daily-upload")

    assert retried["id"] == first["id"]
    assert original_counts(config)["ALL"] == 1
    assert connect_db(config).execute(
        "SELECT COUNT(*) FROM original_factory_audit_events WHERE request_id='stable-daily-upload'"
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("filename", "mime_type", "payload", "message"),
    [
        ("../escape.mp4", "video/mp4", b"video", "filename"),
        ("fake.mp4", "text/plain", b"video", "MIME"),
        ("fake.mov", "video/quicktime", b"video", "extension"),
        ("fake.mp4", "video/mp4", b"not-a-video", "ffprobe"),
    ],
)
def test_import_rejects_dangerous_names_mime_and_unplayable_media(
    tmp_path: Path, filename: str, mime_type: str, payload: bytes, message: str
):
    config = original_config(tmp_path)
    with pytest.raises(ValueError, match=message):
        import_fixture(
            config,
            tmp_path,
            payload=payload,
            filename=filename,
            mime_type=mime_type,
            content_length=len(payload),
        )
    assert original_counts(config)["ALL"] == 0


def test_import_rejects_size_and_pixel_limits_without_orphan_files(tmp_path: Path):
    payload = video_bytes(tmp_path)
    size_config = original_config(tmp_path / "size")
    size_config["storage"]["original_max_upload_bytes"] = len(payload) - 1
    with pytest.raises(ValueError, match="size limit"):
        import_fixture(size_config, tmp_path, payload=payload)

    pixel_config = original_config(tmp_path / "pixels")
    pixel_config["storage"]["original_max_pixels"] = 10_000
    with pytest.raises(ValueError, match="pixel limit"):
        import_fixture(pixel_config, tmp_path, payload=payload)

    for config in (size_config, pixel_config):
        assert original_counts(config)["ALL"] == 0
        root = Path(config["_root"]) / "workspace/server_media/original_factory/videos"
        assert not list(root.glob("*.mp4")) if root.exists() else True

    with pytest.raises(ValueError, match="batch limit"):
        import_fixture(original_config(tmp_path / "batch"), tmp_path, payload=payload, batch_size=21)


def test_two_statuses_three_categories_and_combined_filters(tmp_path: Path):
    config = original_config(tmp_path)
    first = import_fixture(config, tmp_path, request_id="upload-a")
    second = import_fixture(
        config,
        tmp_path,
        payload=video_bytes(tmp_path, width=360, height=640),
        filename="discussion.mp4",
        category="pre_match_discussion",
        request_id="upload-b",
    )
    approve_originals(config, [second["id"]], actor="reviewer", request_id="batch-pass")

    page = list_original_items(
        config, status="APPROVED", category="pre_match_discussion", page=1, page_size=20
    )
    assert [item["id"] for item in page["items"]] == [second["id"]]
    assert page["counts"] == {"ALL": 2, "PENDING_REVIEW": 1, "APPROVED": 1}
    assert {entry["id"] for entry in page["categories"]} == set(ORIGINAL_CATEGORIES)
    assert original_detail(config, first["id"])["status_id"] == "PENDING_REVIEW"


def test_bulk_approval_is_atomic_idempotent_and_audited(tmp_path: Path):
    config = original_config(tmp_path)
    first = import_fixture(config, tmp_path, request_id="upload-1")
    second = import_fixture(
        config,
        tmp_path,
        payload=video_bytes(tmp_path, width=640, height=360),
        filename="second.mp4",
        request_id="upload-2",
    )
    result = approve_originals(
        config, [first["id"], second["id"]], actor="reviewer", request_id="approve-batch-1"
    )
    repeated = approve_originals(
        config, [first["id"], second["id"]], actor="reviewer", request_id="approve-batch-1"
    )

    assert result["approved"] == 2 and repeated["idempotent"] is True
    assert original_counts(config)["APPROVED"] == 2
    events = connect_db(config).execute(
        "SELECT item_id,action,from_status,to_status,request_id FROM original_factory_audit_events "
        "WHERE action='BULK_APPROVE' ORDER BY item_id"
    ).fetchall()
    assert len(events) == 2
    assert all(tuple(event)[1:] == ("BULK_APPROVE", "PENDING_REVIEW", "APPROVED", "approve-batch-1") for event in events)
    with pytest.raises(OriginalConflict, match="PENDING_REVIEW"):
        approve_originals(config, [first["id"]], request_id="approve-batch-2")


def test_download_registration_requires_approval_and_creates_no_publication(tmp_path: Path):
    config = original_config(tmp_path)
    item = import_fixture(config, tmp_path)
    with pytest.raises(OriginalForbidden, match="approved"):
        register_original_download(config, item["id"], actor="operator", request_id="download-1")

    approve_originals(config, [item["id"]], request_id="approve-1")
    registered = register_original_download(
        config, item["id"], actor="operator", request_id="download-2"
    )
    assert registered["download_url"].endswith("/file")
    detail = original_detail(config, item["id"])
    assert detail["download_status"] == "DOWNLOADED"
    connection = connect_db(config)
    assert connection.execute("SELECT COUNT(*) count FROM publications").fetchone()["count"] == 0
    assert connection.execute(
        "SELECT action FROM original_factory_audit_events WHERE item_id=? AND action='DOWNLOAD_REGISTERED'",
        (item["id"],),
    ).fetchone()


def test_publish_source_keeps_uncertain_claims_separate_from_public_facts(tmp_path: Path):
    config = original_config(tmp_path)
    item = import_fixture(config, tmp_path)
    source = original_publish_source_material(config, item["id"])

    assert source["match_name"] == "Palmeiras x Santos"
    assert source["match_time_sao_paulo"] == "2026-09-03T21:30:00-03:00"
    assert source["channels"] == ["Globo", "Premiere"]
    assert "Brasileirao" in source["keywords"]
    assert len(source["social_facts"]) == 1
    assert len(source["uncertain_facts"]) == 1
    assert "Possivel escalação" not in json.dumps(source["social_facts"], ensure_ascii=False)


def test_bulk_delete_is_scoped_audited_and_blocks_path_escape(tmp_path: Path):
    config = original_config(tmp_path)
    first = import_fixture(config, tmp_path, request_id="delete-upload-1")
    second = import_fixture(
        config,
        tmp_path,
        payload=video_bytes(tmp_path, width=720, height=720),
        filename="square.mp4",
        request_id="delete-upload-2",
    )
    result = delete_originals(
        config, [first["id"], second["id"]], actor="reviewer", request_id="delete-batch"
    )
    assert result["deleted"] == 2
    assert original_counts(config)["ALL"] == 0
    assert connect_db(config).execute(
        "SELECT COUNT(*) count FROM original_factory_audit_events WHERE action='BULK_DELETE'"
    ).fetchone()["count"] == 2

    connection = connect_db(config)
    connection.execute(
        "UPDATE original_factory_items SET deleted_at=NULL,file_key='../outside.mp4' WHERE id=?",
        (first["id"],),
    )
    connection.commit()
    with pytest.raises(OriginalForbidden, match="managed original factory storage"):
        resolve_original_file(config, first["id"])


def test_original_http_upload_review_preview_download_flow(tmp_path: Path, monkeypatch):
    config = original_config(tmp_path)
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "dashboard-secret")
    monkeypatch.setenv("JAGUARTV_UPLOAD_TOKEN", "upload-secret")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "0")
    app = DashboardApplication(("127.0.0.1", 0), config)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    payload = video_bytes(tmp_path)
    metadata = {
        "category": "pre_match_prediction",
        "match_name": "Palmeiras x Santos",
        "match_date": "2026-09-03",
        "match_time_sao_paulo": "2026-09-03T21:30:00-03:00",
        "channels": ["Premiere"],
        "match_info": {},
        "social_sources": [],
    }
    encoded = base64.urlsafe_b64encode(json.dumps(metadata).encode()).decode().rstrip("=")

    def request(method: str, path: str, *, body: bytes = b"", headers: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        result = (response.status, dict(response.getheaders()), raw)
        connection.close()
        return result

    try:
        unauthorized, _, _ = request("GET", "/api/originals")
        assert unauthorized == 401
        status, _, raw = request(
            "POST",
            "/api/originals/import?filename=fixture.mp4",
            body=payload,
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(payload)),
                "X-Upload-Token": "upload-secret",
                "X-Original-Metadata": encoded,
                "X-Request-ID": "http-import-1",
            },
        )
        imported = json.loads(raw)
        assert status == 201 and imported["status_id"] == "PENDING_REVIEW"
        item_id = quote(imported["id"])
        auth = {"X-Dashboard-Token": "dashboard-secret"}
        preview_status, preview_headers, preview = request("GET", f"/api/originals/{item_id}/preview", headers=auth)
        assert preview_status == 200 and preview
        assert preview_headers["Content-Type"] == "video/mp4"
        pending_download, _, raw = request("GET", f"/api/originals/{item_id}/file", headers=auth)
        assert pending_download == 403 and "approved" in json.loads(raw)["error"]
        approve_body = json.dumps({"item_ids": [imported["id"]], "actor": "qa"}).encode()
        approved, _, _ = request(
            "POST", "/api/originals/bulk-approve", body=approve_body,
            headers={**auth, "Content-Type": "application/json", "Content-Length": str(len(approve_body)), "X-Request-ID": "http-approve-1"},
        )
        assert approved == 200
        download_body = json.dumps({"actor": "qa"}).encode()
        registered, _, raw = request(
            "POST", f"/api/originals/{item_id}/download", body=download_body,
            headers={**auth, "Content-Type": "application/json", "Content-Length": str(len(download_body)), "X-Request-ID": "http-download-1"},
        )
        assert registered == 200 and json.loads(raw)["download_url"].endswith("/file")
        downloaded, headers, downloaded_payload = request("GET", f"/api/originals/{item_id}/file", headers=auth)
        assert downloaded == 200 and downloaded_payload == payload
        assert headers["Content-Disposition"].startswith("attachment;")
        assert headers["X-Content-Type-Options"] == "nosniff"
    finally:
        app.shutdown()
        app.server_close()
        thread.join(timeout=2)
