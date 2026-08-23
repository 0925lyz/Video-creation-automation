from __future__ import annotations

import json
import threading
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from jaguartv_factory.core import connect_db
from jaguartv_factory.dashboard import DashboardApplication
from jaguartv_factory.youtube_analytics import ANALYTICS_SCOPE, store_metric_snapshot


def seed_growth_dashboard(tmp_path: Path) -> tuple[DashboardApplication, threading.Thread, str]:
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace", "timezone": "America/Sao_Paulo"},
    }
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO youtube_channel_auths(
          account,channel_id,channel_title,scopes,encrypted_refresh_token,token_type,
          expires_in,authorized_at,updated_at,metadata_json
        ) VALUES('account-a','channel-a','Canal atual',?,'encrypted','Bearer',3600,?,?, '{}')
        """,
        (ANALYTICS_SCOPE, "2026-08-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
    )
    for publication_id, title, views, comments in (
        (1, "Primeiro video", 1200, 14),
        (2, "Segundo video", 800, 31),
    ):
        candidate_id = f"candidate-{publication_id}"
        published_at = f"2026-08-1{publication_id}T12:00:00+00:00"
        connection.execute(
            """
            INSERT INTO candidates(
              id,platform,source_id,url,title,status,metadata_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,'APPROVED',?,?,?)
            """,
            (
                candidate_id,
                "facebook",
                f"source-{publication_id}",
                f"https://facebook.test/{publication_id}",
                title,
                json.dumps({"initial_category": "futebol", "keyword": "flamengo hoje"}),
                published_at,
                published_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO publications(
              id,candidate_id,platform,account,account_label,channel_id,published_at,
              published_local_at,status,title,source_platform,privacy_status,public_status,
              youtube_video_id,platform_video_id,youtube_url,public_url,post_url,
              authorized_account_id,platform_username_snapshot,source_category,source_keyword,
              thumbnail_url,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                publication_id,
                candidate_id,
                "youtube",
                "account-a",
                "Canal no envio",
                "channel-a",
                published_at,
                f"2026-08-1{publication_id}T09:00:00-03:00",
                "PUBLISHED",
                title,
                "facebook",
                "public",
                "public",
                f"video-{publication_id}",
                f"video-{publication_id}",
                f"https://www.youtube.com/watch?v=video-{publication_id}",
                f"https://www.youtube.com/watch?v=video-{publication_id}",
                f"https://www.youtube.com/watch?v=video-{publication_id}",
                "account-a",
                "Canal no envio",
                "futebol",
                "flamengo hoje",
                f"https://i.ytimg.com/vi/video-{publication_id}/hqdefault.jpg",
                published_at,
                published_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO youtube_sync_states(
              publication_id,account_id,first_sync_due_at,last_attempted_at,last_successful_at,
              next_sync_at,sync_status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?, 'SUCCESS',?,?)
            """,
            (
                publication_id,
                "account-a",
                "2026-08-12T12:00:00+00:00",
                "2026-08-21T12:00:00+00:00",
                "2026-08-21T12:00:00+00:00",
                "2026-08-21T13:00:00+00:00",
                "2026-08-21T12:00:00+00:00",
                "2026-08-21T12:00:00+00:00",
            ),
        )
        connection.commit()
        store_metric_snapshot(config, publication_id, {
            "view_count": views,
            "like_count": publication_id * 20,
            "comment_count": comments,
            "share_count": publication_id * 3,
            "analytics_views": views - 100,
            "average_view_duration": 18.5 + publication_id,
            "average_view_percentage": 62.0,
            "completion_rate": 70.0 + publication_id,
            "completion_raw_ratio": 0.70 + publication_id / 100,
            "completion_bucket_ratio": 0.99,
            "completion_calculation_version": "end_retention_bucket_v1",
            "completion_weight_views": views - 100,
            "fetched_at": "2026-08-21T12:00:00+00:00",
            "data_through_date": "2026-08-20",
            "data_api_source": "youtube_data_api_v3",
            "analytics_api_source": "youtube_analytics_api_v2",
            "retention_source": "youtube_analytics_audience_retention",
            "api_response_status": "SUCCESS",
            "sync_window": f"2026-08-21T1{publication_id}:00:00+00:00",
        })
    app = DashboardApplication(("127.0.0.1", 0), config)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    return app, thread, f"http://127.0.0.1:{app.server_address[1]}"


def assert_no_page_overflow_or_filter_overlap(page: Page) -> None:
    result = page.evaluate(
        """
        () => {
          const visible = [...document.querySelectorAll('#youtubeGrowthFilters > label, #youtubeGrowthFilters > button')]
            .filter((element) => !element.hidden && getComputedStyle(element).display !== 'none')
            .map((element) => {
              const rect = element.getBoundingClientRect();
              return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
            });
          const overlaps = [];
          for (let i = 0; i < visible.length; i += 1) {
            for (let j = i + 1; j < visible.length; j += 1) {
              const a = visible[i], b = visible[j];
              if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) overlaps.push([i, j]);
            }
          }
          return {
            bodyOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
            overlaps,
          };
        }
        """
    )
    assert result == {"bodyOverflow": False, "overlaps": []}


def test_growth_dashboard_key_flow_desktop_and_mobile(tmp_path: Path):
    app, thread, base = seed_growth_dashboard(tmp_path)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.goto(base, wait_until="networkidle")
            page.locator('.nav-item[data-view="analytics"]').click()
            page.locator("#youtubeGrowthTable").wait_for(state="visible")
            assert page.locator("#youtubeGrowthRange").input_value() == "30d"
            assert "2.000" in page.locator("#youtubeGrowthSummary").inner_text()
            assert "Primeiro video" in page.locator("#youtubeRankingBody").inner_text()
            assert_no_page_overflow_or_filter_overlap(page)

            page.locator("#youtubeGrowthMetric").select_option("comments")
            page.wait_for_function("document.querySelector('#youtubeRankingBody tr:first-child')?.innerText.includes('Segundo video')")

            def fail_ranking(route):
                route.fulfill(status=500, content_type="application/json", body='{"error":"temporary"}')

            page.route("**/api/youtube-analytics/ranking?*", fail_ranking)
            page.locator("#youtubeGrowthMetric").select_option("likes")
            page.locator("#youtubeGrowthError").wait_for(state="visible")
            page.unroute("**/api/youtube-analytics/ranking?*", fail_ranking)
            page.locator("#youtubeGrowthRetry").click()
            page.locator("#youtubeGrowthTable").wait_for(state="visible")
            page.screenshot(path=str(tmp_path / "youtube-growth-desktop.png"), full_page=True)

            page.set_viewport_size({"width": 375, "height": 812})
            page.reload(wait_until="networkidle")
            page.locator('.nav-item[data-view="analytics"]').click()
            page.locator("#youtubeGrowthTable").wait_for(state="visible")
            assert_no_page_overflow_or_filter_overlap(page)
            page.screenshot(path=str(tmp_path / "youtube-growth-mobile.png"), full_page=True)
            context.close()
            browser.close()
    finally:
        app.shutdown()
        thread.join(timeout=5)
        app.server_close()
