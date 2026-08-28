from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jaguartv_factory.dashboard import DashboardApplication


def dashboard_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media"},
    }


@pytest.fixture
def dashboard_server(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "test-admin-token")
    monkeypatch.setenv("JAGUARTV_UPLOAD_TOKEN", "test-upload-token")
    monkeypatch.setenv("JAGUARTV_DASHBOARD_PUBLIC", "1")
    app = DashboardApplication(("127.0.0.1", 0), dashboard_config(tmp_path))
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{app.server_address[1]}"
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()


def request_json(url: str, *, payload: dict | None = None, headers: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read())


def test_import_capabilities_reflect_existing_admin_permission(dashboard_server: str):
    status, anonymous = request_json(f"{dashboard_server}/api/import-capabilities")
    _, administrator = request_json(
        f"{dashboard_server}/api/import-capabilities",
        headers={"X-Dashboard-Token": "test-admin-token"},
    )

    assert status == 200
    assert anonymous["default_target_area"] == "pending_production"
    assert anonymous["source_category_labels"][-1] == "素材"
    assert anonymous["can_direct_approve"] is False
    assert administrator["can_direct_approve"] is True


def test_anonymous_direct_approval_is_rejected_before_business_logic(dashboard_server: str):
    with pytest.raises(urllib.error.HTTPError) as error:
        request_json(
            f"{dashboard_server}/api/actions",
            payload={
                "action": "ingest",
                "platform": "youtube",
                "url": "https://www.youtube.com/watch?v=abc123",
                "source_category": "素材",
                "target_area": "approved",
            },
        )

    assert error.value.code == 401
    assert "authentication required" in json.loads(error.value.read())["error"]


def test_dashboard_admin_can_initialize_source_upload_without_separate_upload_token(
    dashboard_server: str,
):
    status, payload = request_json(
        f"{dashboard_server}/api/uploads/init",
        headers={"X-Dashboard-Token": "test-admin-token"},
        payload={"filename": "finished.mp4", "kind": "source", "size": 1024},
    )

    assert status == 201
    assert payload["kind"] == "source"


def test_anonymous_source_upload_still_requires_authorization(dashboard_server: str):
    with pytest.raises(urllib.error.HTTPError) as error:
        request_json(
            f"{dashboard_server}/api/uploads/init",
            payload={"filename": "finished.mp4", "kind": "source", "size": 1024},
        )

    assert error.value.code == 401


def test_target_area_is_a_backend_whitelist(dashboard_server: str):
    with pytest.raises(urllib.error.HTTPError) as error:
        request_json(
            f"{dashboard_server}/api/actions",
            headers={"X-Dashboard-Token": "test-admin-token"},
            payload={
                "action": "ingest",
                "platform": "youtube",
                "url": "https://www.youtube.com/watch?v=abc123",
                "source_category": "素材",
                "target_area": "READY_FOR_REVIEW",
            },
        )

    assert error.value.code == 400
    assert "target_area" in json.loads(error.value.read())["error"]


def test_ingest_api_requires_source_category_before_starting_task(dashboard_server: str):
    with pytest.raises(urllib.error.HTTPError) as error:
        request_json(
            f"{dashboard_server}/api/actions",
            headers={"X-Dashboard-Token": "test-admin-token"},
            payload={
                "action": "ingest",
                "platform": "youtube",
                "url": "https://www.youtube.com/watch?v=abc123",
                "target_area": "pending_production",
            },
        )

    assert error.value.code == 400
    assert "source_category" in json.loads(error.value.read())["error"]


def test_paginated_inventory_response_has_source_count_and_empty_state_data(
    dashboard_server: str,
):
    status, payload = request_json(
        f"{dashboard_server}/api/candidates?paginated=1&source_type=source_import&page=1&page_size=20"
    )

    assert status == 200
    assert payload["items"] == []
    assert payload["total"] == 0
    assert payload["page"] == 1
    assert payload["source_counts"]["source_import"] == 0
