from __future__ import annotations

import threading
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
def browser_dashboard(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JAGUARTV_DASHBOARD_TOKEN", "e2e-admin-token")
    monkeypatch.setenv("JAGUARTV_UPLOAD_TOKEN", "e2e-upload-token")
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


def assert_box_inside_viewport(box: dict | None, width: int, height: int) -> None:
    assert box is not None
    assert box["x"] >= 0
    assert box["y"] >= 0
    assert box["x"] + box["width"] <= width + 1
    assert box["y"] + box["height"] <= height + 1


def test_finished_upload_exposes_original_source_and_submits_it():
    root = Path("src/jaguartv_factory/web")
    html = (root / "index.html").read_text(encoding="utf-8")
    javascript = (root / "app.js").read_text(encoding="utf-8")

    assert '<option value="original">原创</option>' in html
    assert "source_platform: platform" in javascript
    assert 'platformSelect.value = "original"' in javascript
    assert '<select id="discoverCategory" required>' in html
    assert '<option value="素材">素材</option>' in html
    assert "source_category: sourceCategory" in javascript
    assert 'id="discoverUploadFile" type="file" multiple' in html
    assert 'Array.from(document.querySelector("#discoverUploadFile").files || [])' in javascript
    assert 'for (let fileIndex = 0; fileIndex < files.length; fileIndex += 1)' in javascript


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_source_import_dialog_defaults_and_layout(browser_dashboard: str, viewport: dict):
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            page = browser.new_page(viewport=viewport)
            page.goto(browser_dashboard, wait_until="networkidle")
            page.locator("#discoverButton").click()

            assert page.locator('input[name="discoverTarget"][value="pending_production"]').is_checked()
            assert page.locator("#discoverCategory").input_value() == ""
            assert page.locator("#discoverCategory").get_attribute("required") is not None
            assert page.locator('input[name="discoverTarget"][value="approved"]').is_disabled()
            assert page.locator("#discoverApprovalWarning").is_hidden()
            assert_box_inside_viewport(
                page.locator("#discoverDialog").bounding_box(), viewport["width"], viewport["height"]
            )
            assert_box_inside_viewport(
                page.locator("#submitDiscovery").bounding_box(), viewport["width"], viewport["height"]
            )
            browser.close()
    except Exception as error:
        if "Executable doesn't exist" in str(error):
            pytest.skip("Playwright Chromium is not installed")
        raise


def test_inventory_category_filters_are_isolated_by_source_area(browser_dashboard: str):
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(browser_dashboard, wait_until="networkidle")
            page.locator('[data-view="inventory"]').click()

            factory_labels = page.locator("#categoryFilters [data-category-filter]").all_inner_texts()
            assert any("足球类" in label for label in factory_labels)
            assert any("素材" in label for label in factory_labels)
            assert not any("教程及优点展示类" in label for label in factory_labels)

            page.locator('#sourceFilters [data-source-type="source_import"]').click()
            page.wait_for_timeout(100)
            import_labels = page.locator("#categoryFilters [data-category-filter]").all_inner_texts()
            assert [label.rstrip("0") for label in import_labels] == [
                "全部分类",
                "教程及优点展示类",
                "官方性质类",
                "合作类",
                "运营教学类",
                "教程及答疑类",
                "素材",
            ]
            browser.close()
    except Exception as error:
        if "Executable doesn't exist" in str(error):
            pytest.skip("Playwright Chromium is not installed")
        raise


def test_authorized_direct_approval_is_mutually_exclusive_and_confirms(browser_dashboard: str):
    playwright = pytest.importorskip("playwright.sync_api")
    submitted: list[dict] = []
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"{browser_dashboard}/login", wait_until="networkidle")
            page.locator("#password").fill("e2e-admin-token")
            page.locator("#submitButton").click()
            page.wait_for_url(f"{browser_dashboard}/")
            page.locator("#discoverButton").click()
            approved = page.locator('input[name="discoverTarget"][value="approved"]')
            pending = page.locator('input[name="discoverTarget"][value="pending_production"]')

            assert approved.is_enabled()
            page.locator("#discoverApprovedTarget span").click()
            assert approved.is_checked()
            assert not pending.is_checked()
            assert page.locator("#discoverApprovalWarning").is_visible()

            page.route(
                "**/api/actions",
                lambda route: (
                    submitted.append(route.request.post_data_json),
                    route.fulfill(status=202, content_type="application/json", body='{"task_id":"mock-task","status":"RUNNING"}'),
                ),
            )
            page.on("dialog", lambda dialog: dialog.accept())
            page.locator("#discoverUrl").fill("https://www.youtube.com/watch?v=abc123")
            page.locator("#discoverCategory").select_option("素材")
            page.locator("#submitDiscovery").click()
            page.wait_for_timeout(100)

            assert submitted[0]["target_area"] == "approved"
            assert submitted[0]["source_category"] == "素材"
            assert not page.locator("#discoverDialog").is_visible()
            browser.close()
    except Exception as error:
        if "Executable doesn't exist" in str(error):
            pytest.skip("Playwright Chromium is not installed")
        raise


def test_authorized_upload_defaults_to_pending_production_for_secondary_creation(
    browser_dashboard: str,
):
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"{browser_dashboard}/login", wait_until="networkidle")
            page.locator("#password").fill("e2e-admin-token")
            page.locator("#submitButton").click()
            page.wait_for_url(f"{browser_dashboard}/")
            page.locator("#discoverButton").click()
            page.wait_for_function(
                "document.querySelector('input[name=\"discoverTarget\"][value=\"approved\"]')?.disabled === false"
            )
            page.locator("#discoverMode").select_option("upload")

            assert page.locator("#discoverPlatform").input_value() == "original"
            assert page.locator("#discoverPlatform").is_disabled()
            assert page.locator('input[name="discoverTarget"][value="pending_production"]').is_checked()
            assert page.locator('input[name="discoverTarget"][value="approved"]').is_enabled()
            assert page.locator("#discoverApprovalWarning").is_hidden()
            upload_init = page.evaluate(
                """async () => {
                    const response = await fetch('/api/uploads/init', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({filename: 'finished.mp4', kind: 'source', size: 1024}),
                    });
                    return {status: response.status, payload: await response.json()};
                }"""
            )
            assert upload_init["status"] == 201
            assert upload_init["payload"]["kind"] == "source"
            browser.close()
    except Exception as error:
        if "Executable doesn't exist" in str(error):
            pytest.skip("Playwright Chromium is not installed")
        raise
