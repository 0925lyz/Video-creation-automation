import http.client
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from PIL import Image

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.dashboard import DashboardApplication
from jaguartv_factory.posters import (
    POSTER_CATEGORIES,
    PosterConflict,
    PosterForbidden,
    PosterNotFound,
    approve_poster,
    delete_poster,
    list_posters,
    poster_counts,
    poster_detail,
    resolve_poster_file,
)


def poster_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media", "poster_subdir": "posters"},
    }


def write_image(path: Path, *, size: tuple[int, int] = (80, 120)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(8, 127, 91)).save(path)


def insert_poster(
    config: dict,
    poster_id: str,
    *,
    status: str = "PENDING_SCREENING",
    category: str = "time_location",
    file_key: str | None = None,
    thumbnail_key: str = "",
) -> None:
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO posters(
          id,name,file_key,thumbnail_key,category,status,source_candidate_id,
          metadata_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            poster_id,
            f"Poster {poster_id}",
            file_key or f"original/{poster_id}.png",
            thumbnail_key,
            category,
            status,
            "candidate-1",
            "{}",
            timestamp,
            timestamp,
        ),
    )
    connection.commit()


def test_poster_schema_is_migrated_in_shared_factory_db(tmp_path: Path):
    config = poster_config(tmp_path)
    connection = connect_db(config)

    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    poster_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(posters)")
    }

    assert {"posters", "poster_audit_events"} <= tables
    assert {
        "id", "name", "file_key", "thumbnail_key", "category", "status",
        "source_candidate_id", "created_at", "updated_at", "screened_at",
        "approved_at", "deleted_at", "metadata_json",
    } <= poster_columns


def test_poster_schema_compatibly_upgrades_legacy_tables(tmp_path: Path):
    config = poster_config(tmp_path)
    database = tmp_path / "workspace/factory.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE posters (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          file_key TEXT NOT NULL,
          category TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE poster_audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          poster_id TEXT NOT NULL
        );
        """
    )
    connection.close()

    migrated = connect_db(config)
    poster_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(posters)")}
    audit_columns = {
        row["name"] for row in migrated.execute("PRAGMA table_info(poster_audit_events)")
    }

    assert {"thumbnail_key", "screened_at", "approved_at", "deleted_at"} <= poster_columns
    assert {"action", "from_status", "to_status", "actor", "request_id", "metadata_json", "created_at"} <= audit_columns


def test_poster_list_counts_and_combined_filters(tmp_path: Path):
    config = poster_config(tmp_path)
    insert_poster(config, "screening-time", category="time_location")
    insert_poster(config, "review-time", status="PENDING_REVIEW", category="time_location")
    insert_poster(config, "approved-factor", status="APPROVED", category="factor_analysis")
    insert_poster(config, "legacy", status="APPROVED", category="old_import_value")

    page = list_posters(
        config,
        status="PENDING_REVIEW",
        category="time_location",
        page=1,
        page_size=10,
    )
    counts = poster_counts(config)

    assert [item["id"] for item in page["items"]] == ["review-time"]
    assert page["pagination"] == {"page": 1, "page_size": 10, "total": 1, "pages": 1}
    assert counts == {
        "ALL": 4,
        "PENDING_SCREENING": 1,
        "PENDING_REVIEW": 1,
        "APPROVED": 2,
    }
    legacy = poster_detail(config, "legacy")
    assert legacy["category_id"] == "old_import_value"
    assert legacy["category_known"] is False
    assert "old_import_value" in legacy["category_label"]
    assert {item["id"] for item in page["categories"]} == set(POSTER_CATEGORIES)


def test_poster_status_pass_is_atomic_sequential_and_audited(tmp_path: Path):
    config = poster_config(tmp_path)
    insert_poster(config, "poster-flow")

    first = approve_poster(config, "poster-flow", actor="reviewer-a", request_id="request-1")
    second = approve_poster(config, "poster-flow", actor="reviewer-b", request_id="request-2")

    assert first["status_id"] == "PENDING_REVIEW"
    assert second["status_id"] == "APPROVED"
    row = connect_db(config).execute(
        "SELECT status,screened_at,approved_at FROM posters WHERE id='poster-flow'"
    ).fetchone()
    assert row["status"] == "APPROVED"
    assert row["screened_at"]
    assert row["approved_at"]
    events = connect_db(config).execute(
        """
        SELECT from_status,to_status,actor,request_id
        FROM poster_audit_events WHERE poster_id='poster-flow' ORDER BY id
        """
    ).fetchall()
    assert [tuple(event) for event in events] == [
        ("PENDING_SCREENING", "PENDING_REVIEW", "reviewer-a", "request-1"),
        ("PENDING_REVIEW", "APPROVED", "reviewer-b", "request-2"),
    ]

    with pytest.raises(PosterConflict, match="cannot be approved"):
        approve_poster(config, "poster-flow", actor="reviewer-b", request_id="request-3")


def test_poster_pass_rejects_deleted_and_unknown_records(tmp_path: Path):
    config = poster_config(tmp_path)
    insert_poster(config, "deleted-poster")
    connection = connect_db(config)
    connection.execute(
        "UPDATE posters SET deleted_at=? WHERE id='deleted-poster'", (now_iso(),)
    )
    connection.commit()

    with pytest.raises(PosterConflict, match="deleted"):
        approve_poster(config, "deleted-poster")
    with pytest.raises(PosterNotFound):
        approve_poster(config, "missing-poster")


def test_concurrent_passes_from_same_visible_status_cannot_advance_two_stages(tmp_path: Path):
    config = poster_config(tmp_path)
    insert_poster(config, "concurrent-poster")
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def run(request_id: str) -> None:
        barrier.wait()
        try:
            result = approve_poster(
                config,
                "concurrent-poster",
                actor="reviewer",
                request_id=request_id,
                expected_status="PENDING_SCREENING",
            )
            outcomes.append(result["status_id"])
        except PosterConflict:
            outcomes.append("CONFLICT")

    threads = [threading.Thread(target=run, args=(f"concurrent-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(outcomes) == ["CONFLICT", "PENDING_REVIEW"]
    assert poster_detail(config, "concurrent-poster")["status_id"] == "PENDING_REVIEW"


def test_only_approved_poster_resolves_for_download(tmp_path: Path):
    config = poster_config(tmp_path)
    root = tmp_path / "workspace/server_media/posters"
    write_image(root / "original/pending.png")
    write_image(root / "original/approved.png")
    insert_poster(config, "pending", file_key="original/pending.png")
    insert_poster(config, "approved", status="APPROVED", file_key="original/approved.png")

    with pytest.raises(PosterForbidden, match="approved"):
        resolve_poster_file(config, "pending", require_approved=True)
    assert resolve_poster_file(config, "approved", require_approved=True) == (
        root / "original/approved.png"
    ).resolve()


@pytest.mark.parametrize("unsafe_key", ["../outside.png", "/tmp/outside.png", "original/../../outside.png"])
def test_poster_file_access_rejects_paths_outside_managed_root(tmp_path: Path, unsafe_key: str):
    config = poster_config(tmp_path)
    insert_poster(config, "unsafe", status="APPROVED", file_key=unsafe_key)

    with pytest.raises(PosterForbidden, match="managed poster storage"):
        resolve_poster_file(config, "unsafe", require_approved=True)


def test_poster_file_access_rejects_symlink_components_and_missing_files(tmp_path: Path):
    config = poster_config(tmp_path)
    root = tmp_path / "workspace/server_media/posters"
    outside = tmp_path / "outside"
    write_image(outside / "secret.png")
    root.mkdir(parents=True)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    insert_poster(config, "linked", status="APPROVED", file_key="linked/secret.png")
    insert_poster(config, "missing", status="APPROVED", file_key="original/missing.png")

    with pytest.raises(PosterForbidden, match="symbolic link"):
        resolve_poster_file(config, "linked", require_approved=True)
    with pytest.raises(PosterNotFound, match="file does not exist"):
        resolve_poster_file(config, "missing", require_approved=True)


def test_poster_file_access_rejects_invalid_image_content(tmp_path: Path):
    config = poster_config(tmp_path)
    root = tmp_path / "workspace/server_media/posters"
    invalid = root / "original/invalid.png"
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_bytes(b"not-an-image")
    insert_poster(config, "invalid", status="APPROVED", file_key="original/invalid.png")

    with pytest.raises(ValueError, match="invalid or unreadable"):
        resolve_poster_file(config, "invalid", require_approved=True)


def test_poster_file_access_rejects_mime_extension_mismatch(tmp_path: Path):
    config = poster_config(tmp_path)
    root = tmp_path / "workspace/server_media/posters"
    mismatched = root / "original/mismatched.png"
    mismatched.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 120), color=(8, 127, 91)).save(mismatched, format="JPEG")
    insert_poster(config, "mismatched", status="APPROVED", file_key="original/mismatched.png")

    with pytest.raises(ValueError, match="does not match"):
        resolve_poster_file(config, "mismatched", require_approved=True)


@pytest.mark.parametrize("status", ["PENDING_SCREENING", "PENDING_REVIEW", "APPROVED"])
def test_all_active_poster_statuses_can_be_deleted(tmp_path: Path, status: str):
    config = poster_config(tmp_path)
    poster_id = f"delete-{status.lower()}"
    insert_poster(config, poster_id, status=status)

    result = delete_poster(config, poster_id, request_id=f"request-{status}")

    assert result["deleted"] is True
    with pytest.raises(PosterNotFound):
        poster_detail(config, poster_id)


def test_delete_soft_deletes_a_poster_and_does_not_remove_shared_file(tmp_path: Path):
    config = poster_config(tmp_path)
    root = tmp_path / "workspace/server_media/posters"
    shared = root / "original/shared.png"
    write_image(shared)
    insert_poster(config, "shared-a", file_key="original/shared.png")
    insert_poster(config, "shared-b", file_key="original/shared.png")

    result = delete_poster(config, "shared-a", actor="operator", request_id="delete-1")

    assert result["deleted"] is True
    assert result["file_disposition"] == "shared_preserved"
    assert shared.is_file()
    assert poster_counts(config)["ALL"] == 1
    with pytest.raises(PosterNotFound):
        poster_detail(config, "shared-a")
    audit = connect_db(config).execute(
        "SELECT action,actor,request_id FROM poster_audit_events WHERE poster_id='shared-a'"
    ).fetchone()
    assert tuple(audit) == ("DELETE", "operator", "delete-1")


def request_json(
    port: int,
    method: str,
    path: str,
    *,
    token: str = "",
    body: dict | None = None,
) -> tuple[int, dict, dict[str, str]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Dashboard-Token"] = token
    raw = json.dumps(body).encode() if body is not None else None
    connection.request(method, path, body=raw, headers=headers)
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, payload, response_headers


def test_poster_http_api_uses_dashboard_auth_and_rejects_repeat_pass(tmp_path: Path, monkeypatch):
    config = poster_config(tmp_path)
    insert_poster(config, "api-poster")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "dashboard-secret")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "0")
    app = DashboardApplication(("127.0.0.1", 0), config)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        unauthorized, _, _ = request_json(app.server_address[1], "GET", "/api/posters")
        listed, payload, headers = request_json(
            app.server_address[1], "GET", "/api/posters?page=1&page_size=20", token="dashboard-secret"
        )
        first, first_payload, _ = request_json(
            app.server_address[1],
            "POST",
            "/api/posters/api-poster/approve",
            token="dashboard-secret",
            body={"actor": "api-reviewer", "request_id": "api-pass-1", "expected_status": "PENDING_SCREENING"},
        )
        second, second_payload, _ = request_json(
            app.server_address[1],
            "POST",
            "/api/posters/api-poster/approve",
            token="dashboard-secret",
            body={"actor": "api-reviewer", "request_id": "api-pass-2", "expected_status": "PENDING_REVIEW"},
        )
        repeat, repeat_payload, _ = request_json(
            app.server_address[1],
            "POST",
            "/api/posters/api-poster/approve",
            token="dashboard-secret",
            body={"actor": "api-reviewer", "request_id": "api-pass-3", "expected_status": "APPROVED"},
        )
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()

    assert unauthorized == 401
    assert listed == 200
    assert payload["items"][0]["id"] == "api-poster"
    assert headers["cache-control"] == "no-store"
    assert first == second == 200
    assert first_payload["status_id"] == "PENDING_REVIEW"
    assert second_payload["status_id"] == "APPROVED"
    assert repeat == 409
    assert "cannot be approved" in repeat_payload["error"]


def test_approved_download_has_safe_headers_and_pending_download_is_forbidden(tmp_path: Path, monkeypatch):
    config = poster_config(tmp_path)
    root = tmp_path / "workspace/server_media/posters"
    write_image(root / "original/approved.png")
    write_image(root / "original/pending.png")
    insert_poster(config, "approved", status="APPROVED", file_key="original/approved.png")
    insert_poster(config, "pending", file_key="original/pending.png")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "1")
    app = DashboardApplication(("127.0.0.1", 0), config)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=5)
        connection.request("GET", "/api/posters/approved/download")
        response = connection.getresponse()
        approved_body = response.read()
        approved_headers = {key.lower(): value for key, value in response.getheaders()}
        approved_status = response.status
        connection.close()

        forbidden, forbidden_payload, _ = request_json(
            app.server_address[1], "GET", "/api/posters/pending/download"
        )
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()

    assert approved_status == 200
    assert approved_body
    assert approved_headers["content-type"] == "image/png"
    assert "attachment" in approved_headers["content-disposition"]
    assert "Poster%20approved.png" in approved_headers["content-disposition"]
    assert approved_headers["x-content-type-options"] == "nosniff"
    assert forbidden == 403
    assert "approved" in forbidden_payload["error"]


def test_poster_frontend_contract_contains_inventory_view_and_preview_controls():
    web_root = Path(__file__).parents[1] / "src/jaguartv_factory/web"
    html = (web_root / "index.html").read_text(encoding="utf-8")
    script = (web_root / "app.js").read_text(encoding="utf-8")
    styles = (web_root / "styles.css").read_text(encoding="utf-8")

    assert 'data-view="posters"' in html
    assert 'id="view-posters"' in html
    assert 'id="posterPreviewDialog"' in html
    assert all(label in html for label in ("全部", "待筛选", "待审核", "审核通过"))
    assert all(label in script for label in ("时间地点", "因素分析", "预测比赛", "多赛程", "球星球迷"))
    assert "posterActionButtons" in script
    assert "posterPendingActions" in script
    assert 'classList.toggle("poster-table-empty"' in script
    assert "@media (max-width: 760px)" in styles
    assert ".poster-preview-stage" in styles
    assert ".poster-table-wrap.poster-table-empty .poster-table" in styles
    assert ".poster-table-wrap.poster-table-empty .poster-table thead" in styles
