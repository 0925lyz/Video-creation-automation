from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from jaguartv_factory.core import connect_db, inspect_url, now_iso
from jaguartv_factory.dashboard import candidate_page
from jaguartv_factory.source_imports import (
    DuplicateSourceImportError,
    SourceImportPermissionError,
    complete_source_import,
    create_source_import,
    fail_source_import,
    normalize_import_url,
    source_import_row,
)


def import_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media", "max_upload_bytes": 20 * 1024 * 1024},
        "selection": {"max_source_duration_sec": 1800},
    }


def insert_candidate(config: dict, candidate_id: str, *, status: str = "DISCOVERED") -> None:
    timestamp = now_iso()
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO candidates(
          id,platform,source_id,url,title,description,duration,view_count,
          detected_language,score,status,metadata_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate_id,
            "youtube",
            candidate_id,
            f"https://www.youtube.com/watch?v={candidate_id}",
            f"Video {candidate_id}",
            "",
            20,
            0,
            "",
            50,
            status,
            "{}",
            timestamp,
            timestamp,
        ),
    )
    connection.commit()


def public_dns(host: str, port: int, *args):
    return [(2, 1, 6, "", ("142.250.72.206", port))]


def media_result(path: Path, sha256: str = "a" * 64) -> dict:
    return {
        "path": str(path.resolve()),
        "relative_path": f"jobs/imported/{path.name}",
        "sha256": sha256,
        "mime_type": "video/mp4",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "size_bytes": path.stat().st_size,
        "duration_sec": 20.0,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "has_video": True,
        "has_audio": True,
        "decode_valid": True,
    }


def test_schema_is_additive_and_does_not_backfill_existing_candidates(tmp_path: Path):
    config = import_config(tmp_path)
    insert_candidate(config, "ordinary")

    connection = connect_db(config)
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    imported = connection.execute("SELECT COUNT(*) FROM source_imports").fetchone()[0]

    assert {"source_imports", "source_import_events"}.issubset(tables)
    assert imported == 0


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("file:///tmp/video.mp4", "http or https"),
        ("http://localhost/video", "supported platform"),
        ("http://127.0.0.1/video", "supported platform"),
        ("http://169.254.169.254/latest/meta-data", "supported platform"),
        ("https://youtube.com.evil.test/watch?v=1", "supported platform"),
        ("https://www.youtube.com/../../etc/passwd", "invalid URL path"),
        ("https://www.youtube.com/watch?v=abc%0a--exec", "control characters"),
        ("https://www.youtube.com/watch?v=abc&access_token=secret", "sensitive credentials"),
    ],
)
def test_import_url_rejects_unsafe_inputs(url: str, message: str):
    with pytest.raises(ValueError, match=message):
        normalize_import_url(url, "youtube", resolver=public_dns)


def test_import_url_rejects_private_dns_resolution():
    def private_dns(host: str, port: int, *args):
        return [(2, 1, 6, "", ("10.10.0.8", port))]

    with pytest.raises(ValueError, match="public internet"):
        normalize_import_url(
            "https://www.youtube.com/watch?v=abc123",
            "youtube",
            resolver=private_dns,
        )


def test_import_url_normalizes_supported_platform_and_rejects_platform_mismatch():
    normalized = normalize_import_url(
        "HTTPS://WWW.YouTube.com/watch?v=abc123#fragment",
        "youtube",
        resolver=public_dns,
    )

    assert normalized == "https://www.youtube.com/watch?v=abc123"
    with pytest.raises(ValueError, match="does not match"):
        normalize_import_url(
            "https://www.youtube.com/watch?v=abc123",
            "tiktok",
            resolver=public_dns,
        )


def test_shell_metacharacters_remain_one_downloader_argument(tmp_path: Path, monkeypatch):
    config = import_config(tmp_path)
    normalized = normalize_import_url(
        "https://www.youtube.com/watch?v=abc;touch%20tmp-owned",
        "youtube",
        resolver=public_dns,
    )
    captured: list[list[str]] = []

    def fake_run(args, **kwargs):
        captured.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps({
                "id": "abc",
                "extractor_key": "Youtube",
                "title": "Safe argument",
                "description": "",
                "duration": 20,
                "view_count": 1,
            }),
            stderr="",
        )

    monkeypatch.setattr("jaguartv_factory.core.require_binary", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("jaguartv_factory.core.run_command", fake_run)

    inspect_url(config, normalized, requested_platform="youtube")

    assert len(captured) == 1
    assert isinstance(captured[0], list)
    assert captured[0][-1] == normalized
    assert captured[0].count(normalized) == 1


def test_create_import_defaults_to_pending_production_and_is_idempotent(tmp_path: Path):
    config = import_config(tmp_path)

    first = create_source_import(
        config,
        platform="youtube",
        url="https://www.youtube.com/watch?v=abc123",
        operator_id="operator-1",
        resolver=public_dns,
    )
    second = create_source_import(
        config,
        platform="youtube",
        url="https://www.youtube.com/watch?v=abc123",
        operator_id="operator-1",
        resolver=public_dns,
    )

    assert first["target_area"] == "pending_production"
    assert first["id"] == second["id"]
    assert second["reused"] is True
    assert connect_db(config).execute("SELECT COUNT(*) FROM source_imports").fetchone()[0] == 1


def test_direct_approval_requires_backend_permission(tmp_path: Path):
    config = import_config(tmp_path)

    with pytest.raises(SourceImportPermissionError):
        create_source_import(
            config,
            platform="youtube",
            url="https://www.youtube.com/watch?v=abc123",
            target_area="approved",
            operator_id="viewer",
            can_direct_approve=False,
            resolver=public_dns,
        )


def test_source_filter_combines_with_status_and_excludes_other_candidates(tmp_path: Path):
    config = import_config(tmp_path)
    insert_candidate(config, "ordinary", status="DOWNLOADED")
    insert_candidate(config, "imported", status="DOWNLOADED")
    record = create_source_import(
        config,
        platform="youtube",
        url="https://www.youtube.com/watch?v=imported",
        operator_id="operator-1",
        resolver=public_dns,
    )
    connection = connect_db(config)
    connection.execute(
        "UPDATE source_imports SET candidate_id=?,actual_workflow_status='DOWNLOADED' WHERE id=?",
        ("imported", record["id"]),
    )
    connection.commit()

    page = candidate_page(
        config,
        status="DOWNLOADED",
        source_type="source_import",
        page=1,
        page_size=20,
    )

    assert page["total"] == 1
    assert [item["id"] for item in page["items"]] == ["imported"]
    assert page["items"][0]["source_type"] == "source_import"
    assert page["source_counts"]["source_import"] == 1


def test_pending_import_completion_enters_pending_production_only(tmp_path: Path, monkeypatch):
    config = import_config(tmp_path)
    insert_candidate(config, "pending-import")
    media = tmp_path / "workspace" / "jobs" / "pending-import" / "source.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"validated-video")
    record = create_source_import(
        config,
        platform="youtube",
        url="https://www.youtube.com/watch?v=pending-import",
        operator_id="operator-1",
        resolver=public_dns,
    )
    monkeypatch.setattr(
        "jaguartv_factory.source_imports.validate_imported_media",
        lambda config, candidate_id, path: media_result(path),
    )

    completed = complete_source_import(
        config,
        record["id"],
        candidate_id="pending-import",
        media_path=media,
        original_title="Imported title",
    )
    connection = connect_db(config)
    candidate = connection.execute(
        "SELECT status,title FROM candidates WHERE id='pending-import'"
    ).fetchone()
    events = [
        row[0]
        for row in connection.execute(
            "SELECT event_type FROM events WHERE candidate_id='pending-import' ORDER BY id"
        )
    ]

    assert completed["actual_workflow_status"] == "DOWNLOADED"
    assert candidate["status"] == "DOWNLOADED"
    assert candidate["title"] == "Imported title"
    assert "READY_FOR_REVIEW" not in events
    assert "APPROVED" not in events


def test_direct_approval_records_external_finished_asset_without_fake_production(
    tmp_path: Path, monkeypatch
):
    config = import_config(tmp_path)
    insert_candidate(config, "approved-import")
    media = tmp_path / "workspace" / "jobs" / "approved-import" / "source.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"validated-finished-video")
    record = create_source_import(
        config,
        platform="youtube",
        url="https://www.youtube.com/watch?v=approved-import",
        target_area="approved",
        operator_id="admin-1",
        can_direct_approve=True,
        resolver=public_dns,
    )
    monkeypatch.setattr(
        "jaguartv_factory.source_imports.validate_imported_media",
        lambda config, candidate_id, path: media_result(path, "b" * 64),
    )
    monkeypatch.setattr(
        "jaguartv_factory.source_imports.create_import_cover",
        lambda config, media_path, destination: destination.write_bytes(b"cover"),
    )

    completed = complete_source_import(
        config,
        record["id"],
        candidate_id="approved-import",
        media_path=media,
        original_title="Finished external video",
    )
    connection = connect_db(config)
    candidate = connection.execute(
        "SELECT status FROM candidates WHERE id='approved-import'"
    ).fetchone()
    production_count = connection.execute(
        "SELECT COUNT(*) FROM production_runs WHERE candidate_id='approved-import'"
    ).fetchone()[0]
    package = tmp_path / "workspace" / "server_media" / "review" / "approved-import"
    metadata = json.loads((package / "metadata.json").read_text(encoding="utf-8"))
    review = json.loads((package / "review.json").read_text(encoding="utf-8"))

    assert completed["actual_workflow_status"] == "APPROVED"
    assert candidate["status"] == "APPROVED"
    assert production_count == 0
    assert metadata["production_origin"] == "external_import"
    assert metadata["variant"] == "导入成片"
    assert metadata["smart_slice_completed"] is False
    assert metadata["automatic_review"] is False
    assert review["review_source"] == "manual_import"
    assert review["reviewer"] == "admin-1"


def test_media_validation_failure_never_enters_success_state(tmp_path: Path, monkeypatch):
    config = import_config(tmp_path)
    insert_candidate(config, "bad-import")
    media = tmp_path / "workspace" / "jobs" / "bad-import" / "source.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"bad-video")
    record = create_source_import(
        config,
        platform="youtube",
        url="https://www.youtube.com/watch?v=bad-import",
        target_area="approved",
        operator_id="admin-1",
        can_direct_approve=True,
        resolver=public_dns,
    )
    monkeypatch.setattr(
        "jaguartv_factory.source_imports.validate_imported_media",
        lambda config, candidate_id, path: (_ for _ in ()).throw(ValueError("audio stream is missing")),
    )

    with pytest.raises(ValueError, match="audio stream"):
        complete_source_import(
            config,
            record["id"],
            candidate_id="bad-import",
            media_path=media,
            original_title="Bad import",
        )

    saved = source_import_row(config, record["id"])
    candidate = connect_db(config).execute(
        "SELECT status FROM candidates WHERE id='bad-import'"
    ).fetchone()
    assert saved["actual_workflow_status"] == "IMPORT_FAILED"
    assert saved["error_category"] == "MEDIA_VALIDATION_FAILED"
    assert candidate["status"] == "IMPORT_FAILED"


def test_duplicate_file_hash_is_rejected_without_overwriting_existing_record(
    tmp_path: Path, monkeypatch
):
    config = import_config(tmp_path)
    media_hash = "c" * 64
    records = []
    for index in (1, 2):
        candidate_id = f"hash-import-{index}"
        insert_candidate(config, candidate_id)
        record = create_source_import(
            config,
            platform="youtube",
            url=f"https://www.youtube.com/watch?v={candidate_id}",
            operator_id="operator-1",
            resolver=public_dns,
        )
        media = tmp_path / "workspace" / "jobs" / candidate_id / "source.mp4"
        media.parent.mkdir(parents=True)
        media.write_bytes(f"video-{index}".encode())
        records.append((record, candidate_id, media))
    monkeypatch.setattr(
        "jaguartv_factory.source_imports.validate_imported_media",
        lambda config, candidate_id, path: media_result(path, media_hash),
    )

    complete_source_import(
        config,
        records[0][0]["id"],
        candidate_id=records[0][1],
        media_path=records[0][2],
        original_title="First",
    )
    with pytest.raises(DuplicateSourceImportError, match="same file"):
        complete_source_import(
            config,
            records[1][0]["id"],
            candidate_id=records[1][1],
            media_path=records[1][2],
            original_title="Second",
        )

    assert source_import_row(config, records[0][0]["id"])["actual_workflow_status"] == "DOWNLOADED"
    assert source_import_row(config, records[1][0]["id"])["actual_workflow_status"] == "IMPORT_FAILED"


def test_concurrent_idempotent_creation_produces_one_import_task(tmp_path: Path):
    config = import_config(tmp_path)
    results: list[str] = []
    errors: list[Exception] = []

    def create() -> None:
        try:
            result = create_source_import(
                config,
                platform="youtube",
                url="https://www.youtube.com/watch?v=concurrent",
                operator_id="operator-1",
                idempotency_key="browser-submit-1",
                resolver=public_dns,
            )
            results.append(result["id"])
        except Exception as error:  # pragma: no cover - assertion reports unexpected race
            errors.append(error)

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(set(results)) == 1
    assert connect_db(config).execute("SELECT COUNT(*) FROM source_imports").fetchone()[0] == 1


def test_concurrent_completion_validates_and_commits_same_import_once(tmp_path: Path, monkeypatch):
    config = import_config(tmp_path)
    insert_candidate(config, "concurrent-complete")
    record = create_source_import(
        config,
        platform="youtube",
        url="https://www.youtube.com/watch?v=concurrent-complete",
        operator_id="operator-1",
        resolver=public_dns,
    )
    media = tmp_path / "workspace" / "jobs" / "concurrent-complete" / "source.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"validated-video")
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    calls: list[str] = []
    results: list[str] = []
    errors: list[Exception] = []

    def validate(config_arg, candidate_id, path):
        calls.append(candidate_id)
        entered.set()
        release.wait(3)
        return media_result(path, "e" * 64)

    def complete(mark_started: bool = False) -> None:
        if mark_started:
            second_started.set()
        try:
            result = complete_source_import(
                config,
                record["id"],
                candidate_id="concurrent-complete",
                media_path=media,
                original_title="Concurrent import",
            )
            results.append(result["actual_workflow_status"])
        except Exception as error:  # pragma: no cover - assertion reports unexpected race
            errors.append(error)

    monkeypatch.setattr("jaguartv_factory.source_imports.validate_imported_media", validate)
    first = threading.Thread(target=complete)
    second = threading.Thread(target=lambda: complete(True))
    first.start()
    assert entered.wait(2)
    second.start()
    assert second_started.wait(2)
    assert calls == ["concurrent-complete"]
    release.set()
    first.join(3)
    second.join(3)

    assert errors == []
    assert results == ["DOWNLOADED", "DOWNLOADED"]
    assert calls == ["concurrent-complete"]


def test_failure_records_target_separately_from_actual_status(tmp_path: Path):
    config = import_config(tmp_path)
    record = create_source_import(
        config,
        platform="youtube",
        url="https://www.youtube.com/watch?v=failed",
        target_area="approved",
        operator_id="admin-1",
        can_direct_approve=True,
        resolver=public_dns,
    )

    fail_source_import(
        config,
        record["id"],
        category="DOWNLOAD_FAILED",
        summary="platform refused the download",
    )
    saved = source_import_row(config, record["id"])

    assert saved["target_area"] == "approved"
    assert saved["actual_workflow_status"] == "IMPORT_FAILED"
    assert saved["download_status"] == "FAILED"


def test_failure_before_candidate_creation_is_visible_in_import_inventory(tmp_path: Path):
    config = import_config(tmp_path)
    record = create_source_import(
        config,
        platform="youtube",
        url="https://www.youtube.com/watch?v=failed-before-inspect",
        operator_id="operator-1",
        resolver=public_dns,
    )
    fail_source_import(
        config,
        record["id"],
        category="DOWNLOAD_FAILED",
        summary="DNS changed before the downloader started",
    )

    page = candidate_page(
        config,
        status="IMPORT_FAILED",
        source_type="source_import",
        page=1,
        page_size=20,
    )

    assert page["total"] == 1
    assert page["items"][0]["source_import_id"] == record["id"]
    assert page["items"][0]["source_import_placeholder"] is True
    assert page["items"][0]["target_area"] == "pending_production"
    assert page["items"][0]["status"] == "IMPORT_FAILED"
