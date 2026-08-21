from __future__ import annotations

import json
import math
import re
import socket
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from .core import connect_db, now_iso
from .youtube_publisher import youtube_access_token


SAO_PAULO = ZoneInfo("America/Sao_Paulo")
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
COMPLETION_CALCULATION_VERSION = "end_retention_bucket_v1"
DATA_API_SOURCE = "youtube_data_api_v3"
ANALYTICS_API_SOURCE = "youtube_analytics_api_v2"
RETENTION_SOURCE = "youtube_analytics_audience_retention"
DATA_API_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
ANALYTICS_REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
RANKING_FIELDS = {
    "views": "s.view_count",
    "comments": "s.comment_count",
    "likes": "s.like_count",
    "average_view_duration": "s.average_view_duration",
    "completion_rate": "s.completion_rate",
    "shares": "s.share_count",
}


class YouTubeApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        category: str = "API_ERROR",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.retryable = retryable


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scope_set(value: str) -> set[str]:
    return {item for item in re.split(r"[\s,]+", value or "") if item}


def _safe_error(error: Exception) -> str:
    text = str(error)
    text = re.sub(r"(?i)(access_token|refresh_token|authorization|client_secret)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", text)
    return text[:500]


def _publication_local_time(connection: Any, publication_id: int, published_at: str) -> str:
    row = connection.execute(
        "SELECT published_local_at FROM publications WHERE id=?", (publication_id,)
    ).fetchone()
    if row and row["published_local_at"]:
        return str(row["published_local_at"])
    parsed = _parse_datetime(published_at)
    local = parsed.astimezone(SAO_PAULO).isoformat() if parsed else ""
    if local:
        connection.execute(
            "UPDATE publications SET published_local_at=? WHERE id=? AND (published_local_at IS NULL OR published_local_at='')",
            (local, publication_id),
        )
        connection.commit()
    return local


def backfill_publication_local_times(config: dict[str, Any]) -> int:
    connection = connect_db(config)
    rows = connection.execute(
        "SELECT id,published_at FROM publications WHERE published_at IS NOT NULL AND (published_local_at IS NULL OR published_local_at='')"
    ).fetchall()
    changed = 0
    for row in rows:
        if _publication_local_time(connection, int(row["id"]), str(row["published_at"] or "")):
            changed += 1
    return changed


def schedule_first_sync(config: dict[str, Any], publication_id: int) -> dict[str, Any]:
    connection = connect_db(config)
    row = connection.execute(
        """
        SELECT id,account,authorized_account_id,published_at,status,platform,youtube_video_id,
               platform_video_id,public_status,privacy_status
        FROM publications WHERE id=?
        """,
        (int(publication_id),),
    ).fetchone()
    if not row:
        raise ValueError("publication does not exist")
    video_id = str(row["youtube_video_id"] or row["platform_video_id"] or "")
    if row["platform"] != "youtube" or row["status"] != "PUBLISHED" or not video_id:
        raise ValueError("publication is not a successful YouTube publication")
    public_status = str(row["public_status"] or row["privacy_status"] or "").lower()
    if public_status != "public":
        raise ValueError("publication is not public")
    published = _parse_datetime(str(row["published_at"] or ""))
    if not published:
        raise ValueError("publication is missing a valid published_at")
    account_id = str(row["authorized_account_id"] or row["account"] or "")
    if not account_id:
        raise ValueError("publication is missing authorized account id")
    due = published.astimezone(timezone.utc) + timedelta(hours=24)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO youtube_sync_states(
          publication_id,account_id,first_sync_due_at,next_sync_at,sync_status,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(publication_id) DO NOTHING
        """,
        (int(publication_id), account_id, _iso(due), _iso(due), "PENDING", timestamp, timestamp),
    )
    _publication_local_time(connection, int(publication_id), str(row["published_at"]))
    connection.commit()
    return dict(connection.execute("SELECT * FROM youtube_sync_states WHERE publication_id=?", (int(publication_id),)).fetchone())


def restore_sync_tasks(
    config: dict[str, Any],
    *,
    now: datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    connection = connect_db(config)
    current = _iso(now or datetime.now(timezone.utc))
    rows = connection.execute(
        """
        SELECT s.*,p.youtube_video_id,p.platform_video_id,p.channel_id,p.published_at,
               p.public_status,p.privacy_status,p.status AS publication_status,
               a.channel_id AS authorized_channel_id,a.channel_title,a.scopes,a.status AS account_status
        FROM youtube_sync_states s
        JOIN publications p ON p.id=s.publication_id
        LEFT JOIN youtube_channel_auths a ON a.account=s.account_id
        LEFT JOIN youtube_backfill_runs b ON b.id=s.backfill_run_id
        WHERE p.platform='youtube' AND p.status='PUBLISHED'
          AND (s.backfill_run_id IS NULL OR b.status='RUNNING')
          AND (
            (s.sync_status IN ('PENDING','RETRY','SUCCESS','PARTIAL') AND s.next_sync_at IS NOT NULL AND s.next_sync_at<=?)
            OR (s.sync_status='IN_PROGRESS' AND s.lease_expires_at IS NOT NULL AND s.lease_expires_at<=?)
          )
        ORDER BY s.next_sync_at ASC,s.account_id ASC,s.publication_id ASC
        LIMIT ?
        """,
        (current, current, int(limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def parse_data_api_video(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {
            "video_id": "",
            "channel_id": "",
            "current_channel_title": "",
            "privacy_status": "",
            "view_count": None,
            "like_count": None,
            "comment_count": None,
            "thumbnail_url": "",
            "api_response_status": "VIDEO_UNAVAILABLE",
        }
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    thumbnail = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
    privacy = str(status.get("privacyStatus") or "")
    return {
        "video_id": str(item.get("id") or ""),
        "channel_id": str(snippet.get("channelId") or ""),
        "current_channel_title": str(snippet.get("channelTitle") or ""),
        "privacy_status": privacy,
        "view_count": _integer(statistics.get("viewCount")),
        "like_count": _integer(statistics.get("likeCount")),
        "comment_count": _integer(statistics.get("commentCount")),
        "thumbnail_url": str(thumbnail.get("url") or ""),
        "api_response_status": "SUCCESS" if privacy == "public" else "NOT_PUBLIC",
    }


def _response_rows_by_headers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    headers = [str(item.get("name") or "") for item in payload.get("columnHeaders") or []]
    return [dict(zip(headers, row)) for row in payload.get("rows") or []]


def parse_analytics_metrics(payload: dict[str, Any], video_id: str) -> dict[str, Any]:
    rows = _response_rows_by_headers(payload)
    row = next((item for item in rows if not item.get("video") or str(item.get("video")) == video_id), {})
    return {
        "analytics_views": _integer(row.get("views")),
        "share_count": _integer(row.get("shares")),
        "average_view_duration": _number(row.get("averageViewDuration")),
        "average_view_percentage": _number(row.get("averageViewPercentage")),
    }


def derive_completion_rate(payload: dict[str, Any]) -> dict[str, Any]:
    rows = _response_rows_by_headers(payload)
    valid = [
        (_number(row.get("elapsedVideoTimeRatio")), _number(row.get("audienceWatchRatio")))
        for row in rows
    ]
    valid = [(bucket, ratio) for bucket, ratio in valid if bucket is not None and ratio is not None]
    if not valid:
        return {
            "completion_rate": None,
            "completion_raw_ratio": None,
            "completion_bucket_ratio": None,
            "completion_calculation_version": COMPLETION_CALCULATION_VERSION,
        }
    bucket, ratio = max(valid, key=lambda item: item[0])
    return {
        "completion_rate": round(ratio * 100, 10),
        "completion_raw_ratio": ratio,
        "completion_bucket_ratio": bucket,
        "completion_calculation_version": COMPLETION_CALCULATION_VERSION,
    }


def _api_error(response: requests.Response) -> YouTubeApiError:
    status = response.status_code
    reason = ""
    try:
        payload = response.json()
        errors = ((payload.get("error") or {}).get("errors") or []) if isinstance(payload, dict) else []
        reason = str((errors[0] if errors else {}).get("reason") or "")
    except (ValueError, TypeError):
        reason = ""
    if status == 429 or reason in {"quotaExceeded", "dailyLimitExceeded"}:
        return YouTubeApiError("YouTube quota or rate limit reached", status_code=status, category="QUOTA_EXHAUSTED", retryable=True)
    if status >= 500:
        return YouTubeApiError("YouTube service unavailable", status_code=status, category="SERVER_ERROR", retryable=True)
    if status in {400, 401} and reason in {"authError", "invalidCredentials", "unauthorized"}:
        return YouTubeApiError("YouTube authorization revoked", status_code=status, category="AUTH_REVOKED", retryable=False)
    if status == 403 and reason in {"insufficientPermissions", "forbidden"}:
        return YouTubeApiError("YouTube permission is insufficient", status_code=status, category="ANALYTICS_SCOPE_MISSING", retryable=False)
    return YouTubeApiError(f"YouTube API request failed: HTTP {status}", status_code=status, category="FORBIDDEN" if status == 403 else "API_ERROR", retryable=False)


class YouTubeAnalyticsClient:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        session: requests.Session | None = None,
        token_provider: Callable[[dict[str, Any], str], dict[str, str]] = youtube_access_token,
        timeout: int = 30,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.token_provider = token_provider
        self.timeout = timeout

    def _token(self, account: dict[str, Any]) -> str:
        try:
            return str(self.token_provider(self.config, str(account["account"]))["access_token"])
        except Exception as error:
            message = _safe_error(error)
            category = "AUTH_REVOKED" if any(term in message.lower() for term in ("invalid_grant", "revoked", "unauthorized")) else "AUTH_REFRESH_FAILED"
            raise YouTubeApiError("YouTube authorization refresh failed", category=category, retryable=category != "AUTH_REVOKED") from error

    def _get(self, url: str, account: dict[str, Any], params: dict[str, str]) -> dict[str, Any]:
        try:
            response = self.session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._token(account)}"},
                timeout=self.timeout,
            )
        except (requests.Timeout, socket.timeout) as error:
            raise YouTubeApiError("YouTube API timeout", category="NETWORK_TIMEOUT", retryable=True) from error
        except requests.RequestException as error:
            raise YouTubeApiError("YouTube API network error", category="NETWORK_ERROR", retryable=True) from error
        if response.status_code >= 400:
            raise _api_error(response)
        try:
            payload = response.json()
        except ValueError as error:
            raise YouTubeApiError("YouTube API returned invalid JSON", category="INVALID_RESPONSE", retryable=True) from error
        return payload if isinstance(payload, dict) else {}

    def fetch_data_videos(self, account: dict[str, Any], video_ids: list[str]) -> dict[str, dict[str, Any]]:
        payload = self._get(
            DATA_API_VIDEOS_URL,
            account,
            {"part": "snippet,status,statistics", "id": ",".join(video_ids), "maxResults": "50"},
        )
        return {
            item["video_id"]: item
            for item in (parse_data_api_video(raw) for raw in payload.get("items") or [])
            if item["video_id"]
        }

    def fetch_analytics(self, account: dict[str, Any], video_id: str, start_date: str, end_date: str) -> dict[str, Any]:
        payload = self._get(
            ANALYTICS_REPORTS_URL,
            account,
            {
                "ids": "channel==MINE",
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": "video",
                "filters": f"video=={video_id}",
                "metrics": "views,shares,averageViewDuration,averageViewPercentage",
            },
        )
        return parse_analytics_metrics(payload, video_id)

    def fetch_retention(self, account: dict[str, Any], video_id: str, start_date: str, end_date: str) -> dict[str, Any]:
        payload = self._get(
            ANALYTICS_REPORTS_URL,
            account,
            {
                "ids": "channel==MINE",
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": "elapsedVideoTimeRatio",
                "filters": f"video=={video_id}",
                "metrics": "audienceWatchRatio",
                "sort": "elapsedVideoTimeRatio",
            },
        )
        return derive_completion_rate(payload)


def store_metric_snapshot(config: dict[str, Any], publication_id: int, values: dict[str, Any]) -> int:
    connection = connect_db(config)
    fetched_at = str(values.get("fetched_at") or now_iso())
    sync_window = str(values.get("sync_window") or fetched_at[:13] + ":00:00+00:00")
    fields = (
        "view_count", "like_count", "comment_count", "share_count", "analytics_views",
        "average_view_duration", "average_view_percentage", "completion_rate",
        "completion_raw_ratio", "completion_bucket_ratio", "completion_calculation_version",
        "completion_weight_views", "fetched_at", "data_through_date", "data_api_source",
        "analytics_api_source", "retention_source", "api_response_status", "raw_status_json",
    )
    payload = {field: values.get(field) for field in fields}
    payload["completion_calculation_version"] = str(payload["completion_calculation_version"] or "")
    payload["api_response_status"] = str(payload["api_response_status"] or "UNKNOWN")
    payload["raw_status_json"] = json.dumps(payload["raw_status_json"] or {}, ensure_ascii=False) if not isinstance(payload["raw_status_json"], str) else payload["raw_status_json"]
    placeholders = ",".join("?" for _ in fields)
    updates = ",".join(f"{field}=excluded.{field}" for field in fields)
    connection.execute(
        f"""
        INSERT INTO youtube_metric_snapshots(publication_id,sync_window,{','.join(fields)})
        VALUES(?,?,{placeholders})
        ON CONFLICT(publication_id,sync_window) DO UPDATE SET {updates}
        """,
        (int(publication_id), sync_window, *(payload[field] for field in fields)),
    )
    connection.commit()
    row = connection.execute(
        "SELECT id FROM youtube_metric_snapshots WHERE publication_id=? AND sync_window=?",
        (int(publication_id), sync_window),
    ).fetchone()
    return int(row["id"])


def _retry_delay_minutes(retry_count: int, category: str) -> int:
    base = 180 if category in {"QUOTA_EXHAUSTED", "RATE_LIMIT"} else 5
    return min(24 * 60, base * (2 ** min(max(retry_count - 1, 0), 5)))


def _record_sync_error(connection: Any, rows: list[dict[str, Any]], error: YouTubeApiError, now: datetime) -> None:
    timestamp = _iso(now)
    for row in rows:
        retry_count = int(row.get("retry_count") or 0) + 1
        if error.category == "AUTH_REVOKED":
            status = "NEEDS_REAUTH"
        elif error.retryable:
            status = "RETRY"
        else:
            status = "BLOCKED"
        next_sync = _iso(now + timedelta(minutes=_retry_delay_minutes(retry_count, error.category))) if error.retryable else None
        connection.execute(
            """
            UPDATE youtube_sync_states
            SET last_attempted_at=?,next_sync_at=?,retry_count=?,sync_status=?,
                last_error_category=?,last_error_summary=?,lease_owner='',lease_expires_at=NULL,updated_at=?
                ,backfill_run_id=CASE WHEN ? THEN backfill_run_id ELSE NULL END
            WHERE publication_id=?
            """,
            (
                timestamp, next_sync, retry_count, status, error.category,
                _safe_error(error), timestamp, int(error.retryable), row["publication_id"],
            ),
        )
    if error.category == "AUTH_REVOKED" and rows:
        connection.execute(
            """
            UPDATE youtube_channel_auths SET status='NEEDS_REAUTH',last_error_category=?,
              last_error_summary=?,updated_at=? WHERE account=?
            """,
            (error.category, _safe_error(error), timestamp, rows[0]["account_id"]),
        )
    connection.commit()


def _claim_due_tasks(config: dict[str, Any], now: datetime, limit: int) -> list[dict[str, Any]]:
    due = restore_sync_tasks(config, now=now, limit=limit)
    if not due:
        return []
    connection = connect_db(config)
    owner = uuid.uuid4().hex
    lease_expires = _iso(now + timedelta(minutes=15))
    claimed: list[dict[str, Any]] = []
    connection.execute("BEGIN IMMEDIATE")
    for row in due:
        cursor = connection.execute(
            """
            UPDATE youtube_sync_states SET sync_status='IN_PROGRESS',lease_owner=?,lease_expires_at=?,updated_at=?
            WHERE publication_id=? AND (
              sync_status IN ('PENDING','RETRY','SUCCESS','PARTIAL')
              OR (sync_status='IN_PROGRESS' AND lease_expires_at<=?)
            )
              AND (backfill_run_id IS NULL OR EXISTS(
                SELECT 1 FROM youtube_backfill_runs b
                WHERE b.id=youtube_sync_states.backfill_run_id AND b.status='RUNNING'
              ))
            """,
            (owner, lease_expires, _iso(now), row["publication_id"], _iso(now)),
        )
        if cursor.rowcount:
            claimed.append(row)
    connection.commit()
    return claimed


def sync_due_once(
    config: dict[str, Any],
    *,
    client: Any | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    settings = config.get("youtube_analytics", {}) or {}
    batch_limit = min(50, int(limit or settings.get("batch_size") or 50))
    if batch_limit < 1:
        raise ValueError("limit must be positive")
    poll_minutes = max(15, int(settings.get("poll_interval_minutes") or 60))
    api = client or YouTubeAnalyticsClient(config)
    claimed = _claim_due_tasks(config, current, batch_limit)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in claimed:
        groups[str(row["account_id"])].append(row)
    successful = 0
    failed = 0
    results: list[dict[str, Any]] = []
    connection = connect_db(config)
    for account_id, rows in groups.items():
        account_row = connection.execute(
            "SELECT * FROM youtube_channel_auths WHERE account=?", (account_id,)
        ).fetchone()
        if not account_row:
            error = YouTubeApiError("YouTube account is not authorized", category="AUTH_MISSING", retryable=False)
            _record_sync_error(connection, rows, error, current)
            failed += len(rows)
            continue
        account = dict(account_row)
        valid_rows: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("channel_id") or "") != str(account.get("channel_id") or ""):
                _record_sync_error(connection, [row], YouTubeApiError("Publication channel does not match authorized account", category="CHANNEL_MISMATCH", retryable=False), current)
                failed += 1
            else:
                valid_rows.append(row)
        if not valid_rows:
            continue
        video_ids = [str(row.get("youtube_video_id") or row.get("platform_video_id") or "") for row in valid_rows]
        try:
            data_items = api.fetch_data_videos(account, video_ids)
        except YouTubeApiError as error:
            _record_sync_error(connection, valid_rows, error, current)
            failed += len(valid_rows)
            continue
        except Exception as error:
            wrapped = YouTubeApiError(_safe_error(error), category="UNEXPECTED_ERROR", retryable=True)
            _record_sync_error(connection, valid_rows, wrapped, current)
            failed += len(valid_rows)
            continue
        has_analytics_scope = ANALYTICS_SCOPE in _scope_set(str(account.get("scopes") or ""))
        for row, video_id in zip(valid_rows, video_ids):
            data = data_items.get(video_id) or parse_data_api_video(None)
            if data.get("channel_id") and data["channel_id"] != account["channel_id"]:
                _record_sync_error(connection, [row], YouTubeApiError("YouTube video belongs to a different channel", category="CHANNEL_MISMATCH", retryable=False), current)
                failed += 1
                continue
            api_status = str(
                data.get("api_response_status")
                or ("SUCCESS" if data.get("video_id") or data.get("channel_id") else "VIDEO_UNAVAILABLE")
            )
            analytics = {"analytics_views": None, "share_count": None, "average_view_duration": None, "average_view_percentage": None}
            completion = derive_completion_rate({"rows": []})
            published = _parse_datetime(str(row.get("published_at") or "")) or current
            start_date = published.astimezone(SAO_PAULO).date().isoformat()
            end_day = max(current.astimezone(SAO_PAULO).date() - timedelta(days=1), date.fromisoformat(start_date))
            end_date = end_day.isoformat()
            retention_error: YouTubeApiError | None = None
            if api_status == "SUCCESS" and has_analytics_scope:
                try:
                    analytics = api.fetch_analytics(account, video_id, start_date, end_date)
                    try:
                        completion = api.fetch_retention(account, video_id, start_date, end_date)
                    except YouTubeApiError as error:
                        if error.category in {
                            "AUTH_REVOKED", "QUOTA_EXHAUSTED", "RATE_LIMIT",
                            "SERVER_ERROR", "NETWORK_TIMEOUT", "NETWORK_ERROR",
                        }:
                            raise
                        retention_error = error
                        api_status = "PARTIAL_RETENTION_UNAVAILABLE"
                except YouTubeApiError as error:
                    if error.category == "ANALYTICS_SCOPE_MISSING":
                        has_analytics_scope = False
                        api_status = "PARTIAL_ANALYTICS_SCOPE_MISSING"
                    else:
                        _record_sync_error(connection, [row], error, current)
                        failed += 1
                        continue
            elif api_status == "SUCCESS":
                api_status = "PARTIAL_ANALYTICS_SCOPE_MISSING"
            fetched_at = _iso(current)
            sync_window = current.replace(minute=0, second=0, microsecond=0).isoformat()
            previous = connection.execute(
                "SELECT * FROM youtube_metric_snapshots WHERE publication_id=? ORDER BY fetched_at DESC,id DESC LIMIT 1",
                (int(row["publication_id"]),),
            ).fetchone()
            decreases: dict[str, dict[str, int]] = {}
            if previous:
                for field in ("view_count", "like_count", "comment_count", "share_count", "analytics_views"):
                    old_value = previous[field]
                    new_value = ({**data, **analytics}).get(field)
                    if old_value is not None and new_value is not None and int(new_value) < int(old_value):
                        decreases[field] = {"previous": int(old_value), "current": int(new_value)}
            snapshot_id = store_metric_snapshot(config, int(row["publication_id"]), {
                **data,
                **analytics,
                **completion,
                "completion_weight_views": analytics.get("analytics_views") if completion.get("completion_rate") is not None else None,
                "fetched_at": fetched_at,
                "data_through_date": end_date,
                "data_api_source": DATA_API_SOURCE,
                "analytics_api_source": ANALYTICS_API_SOURCE if has_analytics_scope else None,
                "retention_source": RETENTION_SOURCE if has_analytics_scope else None,
                "api_response_status": api_status,
                "sync_window": sync_window,
                "raw_status_json": {
                    "analytics_scope": has_analytics_scope,
                    "decreases": decreases,
                    "retention_error_category": retention_error.category if retention_error else None,
                },
            })
            if data.get("current_channel_title"):
                connection.execute(
                    """
                    UPDATE youtube_channel_auths SET channel_title=?,last_verified_at=?,
                      status=CASE WHEN ?='SUCCESS' THEN 'AUTHORIZED' ELSE status END,updated_at=?
                    WHERE account=?
                    """,
                    (data["current_channel_title"], fetched_at, api_status, fetched_at, account_id),
                )
            if api_status == "PARTIAL_ANALYTICS_SCOPE_MISSING":
                connection.execute(
                    """
                    UPDATE youtube_channel_auths SET status='ANALYTICS_SCOPE_MISSING',
                      last_error_category='ANALYTICS_SCOPE_MISSING',
                      last_error_summary='Reauthorization with YouTube Analytics read scope is required',
                      updated_at=? WHERE account=?
                    """,
                    (fetched_at, account_id),
                )
            if data.get("thumbnail_url") or data.get("privacy_status"):
                connection.execute(
                    "UPDATE publications SET thumbnail_url=COALESCE(NULLIF(?,''),thumbnail_url),public_status=COALESCE(NULLIF(?,''),public_status),updated_at=? WHERE id=?",
                    (data.get("thumbnail_url") or "", data.get("privacy_status") or "", fetched_at, row["publication_id"]),
                )
            sync_status = "SUCCESS" if api_status == "SUCCESS" else "PARTIAL" if api_status.startswith("PARTIAL") else "BLOCKED"
            next_sync = _iso(current + timedelta(minutes=poll_minutes)) if sync_status in {"SUCCESS", "PARTIAL"} else None
            error_category = ""
            error_summary = ""
            if api_status == "PARTIAL_ANALYTICS_SCOPE_MISSING":
                error_category = "ANALYTICS_SCOPE_MISSING"
                error_summary = "Analytics permission is missing; Data API metrics remain available"
            elif api_status == "PARTIAL_RETENTION_UNAVAILABLE":
                error_category = "RETENTION_UNAVAILABLE"
                error_summary = _safe_error(retention_error) if retention_error else "Audience retention is temporarily unavailable"
            elif sync_status == "BLOCKED":
                error_category = api_status
                error_summary = {
                    "NOT_PUBLIC": "Video is no longer public",
                    "VIDEO_UNAVAILABLE": "Video was deleted or is inaccessible",
                }.get(api_status, "YouTube metrics are unavailable")
            connection.execute(
                """
                UPDATE youtube_sync_states SET last_attempted_at=?,
                  last_successful_at=CASE WHEN ? IN ('SUCCESS','PARTIAL') THEN ? ELSE last_successful_at END,
                  next_sync_at=?,
                  retry_count=0,sync_status=?,last_error_category=?,last_error_summary=?,
                  lease_owner='',lease_expires_at=NULL,backfill_run_id=NULL,updated_at=? WHERE publication_id=?
                """,
                (
                    fetched_at,
                    sync_status,
                    fetched_at,
                    next_sync,
                    sync_status,
                    error_category,
                    error_summary,
                    fetched_at,
                    row["publication_id"],
                ),
            )
            connection.commit()
            if sync_status in {"SUCCESS", "PARTIAL"}:
                successful += 1
            else:
                failed += 1
            results.append({"publication_id": row["publication_id"], "snapshot_id": snapshot_id, "status": sync_status})
    connection.execute(
        """
        UPDATE youtube_backfill_runs
        SET status='COMPLETED',completed_at=?,updated_at=?
        WHERE status='RUNNING' AND NOT EXISTS(
          SELECT 1 FROM youtube_sync_states s WHERE s.backfill_run_id=youtube_backfill_runs.id
        )
        """,
        (_iso(current), _iso(current)),
    )
    connection.commit()
    return {"due": len(claimed), "successful": successful, "failed": failed, "results": results}


def date_filter_bounds(
    range_name: str = "30d",
    start_date: str = "",
    end_date: str = "",
    *,
    now: datetime | None = None,
) -> tuple[str | None, str | None]:
    current = (now or datetime.now(SAO_PAULO)).astimezone(SAO_PAULO)
    today = current.date()
    if range_name == "all":
        return None, None
    if range_name == "7d":
        first, last = today - timedelta(days=6), today
    elif range_name == "30d":
        first, last = today - timedelta(days=29), today
    elif range_name == "custom":
        try:
            first, last = date.fromisoformat(start_date), date.fromisoformat(end_date)
        except ValueError as error:
            raise ValueError("dates must use YYYY-MM-DD") from error
        if first > last:
            raise ValueError("start_date must not be after end_date")
        if (last - first).days > 3660:
            raise ValueError("date range is too large")
    else:
        raise ValueError("range must be all, 7d, 30d, or custom")
    start = datetime.combine(first, datetime.min.time(), tzinfo=SAO_PAULO)
    end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=SAO_PAULO)
    return start.isoformat(), end.isoformat()


def _filters(
    range_name: str,
    start_date: str,
    end_date: str,
    account_id: str,
) -> tuple[str, list[Any]]:
    start, end = date_filter_bounds(range_name, start_date, end_date)
    clauses = [
        "p.platform='youtube'",
        "p.status='PUBLISHED'",
        "COALESCE(NULLIF(p.public_status,''),NULLIF(p.privacy_status,''),'public')='public'",
        "COALESCE(NULLIF(p.youtube_video_id,''),p.platform_video_id,'')!=''",
    ]
    params: list[Any] = []
    if start:
        clauses.append("p.published_local_at>=?")
        params.append(start)
    if end:
        clauses.append("p.published_local_at<?")
        params.append(end)
    if account_id:
        if len(account_id) > 128 or not re.fullmatch(r"[A-Za-z0-9_.@-]+", account_id):
            raise ValueError("invalid account_id")
        clauses.append("COALESCE(NULLIF(p.authorized_account_id,''),p.account)=?")
        params.append(account_id)
    return " AND ".join(clauses), params


def _latest_cte() -> str:
    return """
      latest AS (
        SELECT ranked.* FROM (
          SELECT s.*,ROW_NUMBER() OVER(PARTITION BY publication_id ORDER BY fetched_at DESC,id DESC) AS rank_no
          FROM youtube_metric_snapshots s
          WHERE api_response_status='SUCCESS' OR api_response_status LIKE 'PARTIAL_%'
        ) ranked WHERE rank_no=1
      )
    """


def analytics_summary(
    config: dict[str, Any],
    *,
    range_name: str = "30d",
    start_date: str = "",
    end_date: str = "",
    account_id: str = "",
) -> dict[str, Any]:
    backfill_publication_local_times(config)
    where, params = _filters(range_name, start_date, end_date, account_id)
    connection = connect_db(config)
    row = connection.execute(
        f"""
        WITH {_latest_cte()}
        SELECT COUNT(*) AS video_count,
               COUNT(DISTINCT COALESCE(NULLIF(p.authorized_account_id,''),p.account)) AS account_count,
               SUM(s.view_count) AS view_count,SUM(s.like_count) AS like_count,
               SUM(s.comment_count) AS comment_count,SUM(s.share_count) AS share_count,
               CASE WHEN SUM(CASE WHEN s.average_view_duration IS NOT NULL AND s.analytics_views>0 THEN s.analytics_views END)>0
                 THEN SUM(CASE WHEN s.average_view_duration IS NOT NULL AND s.analytics_views>0 THEN s.average_view_duration*s.analytics_views END)
                      / SUM(CASE WHEN s.average_view_duration IS NOT NULL AND s.analytics_views>0 THEN s.analytics_views END)
               END AS average_view_duration,
               CASE WHEN SUM(CASE WHEN s.completion_rate IS NOT NULL AND s.completion_weight_views>0 THEN s.completion_weight_views END)>0
                 THEN SUM(CASE WHEN s.completion_rate IS NOT NULL AND s.completion_weight_views>0 THEN s.completion_rate*s.completion_weight_views END)
                      / SUM(CASE WHEN s.completion_rate IS NOT NULL AND s.completion_weight_views>0 THEN s.completion_weight_views END)
               END AS completion_rate,
               MAX(s.fetched_at) AS last_successful_update,
               COALESCE(SUM(CASE WHEN s.id IS NULL OR s.view_count IS NULL OR s.like_count IS NULL OR s.comment_count IS NULL
                         OR s.share_count IS NULL OR s.average_view_duration IS NULL OR s.completion_rate IS NULL
                         OR st.sync_status IN ('RETRY','BLOCKED','NEEDS_REAUTH')
                         OR (st.next_sync_at IS NOT NULL AND st.next_sync_at<?)
                         THEN 1 ELSE 0 END)
                 ,0) AS missing_video_count
        FROM publications p
        LEFT JOIN latest s ON s.publication_id=p.id
        LEFT JOIN youtube_sync_states st ON st.publication_id=p.id
        WHERE {where}
        """,
        (_iso(datetime.now(timezone.utc)), *params),
    ).fetchone()
    result = dict(row)
    result["range"] = range_name
    result["timezone"] = "America/Sao_Paulo"
    result["completion_note"] = "Estimate based on the final available audience-retention bucket; repeated viewing can exceed 100%."
    return result


def analytics_ranking(
    config: dict[str, Any],
    *,
    range_name: str = "30d",
    start_date: str = "",
    end_date: str = "",
    account_id: str = "",
    metric: str = "views",
    page: int = 1,
    page_size: int = 20,
    _publication_id: int | None = None,
) -> dict[str, Any]:
    if metric not in RANKING_FIELDS:
        raise ValueError("invalid ranking metric")
    if page < 1 or page_size < 1 or page_size > 100:
        raise ValueError("invalid pagination")
    backfill_publication_local_times(config)
    where, params = _filters(range_name, start_date, end_date, account_id)
    if _publication_id is not None:
        where += " AND p.id=?"
        params.append(int(_publication_id))
    connection = connect_db(config)
    total = int(connection.execute(f"SELECT COUNT(*) FROM publications p WHERE {where}", params).fetchone()[0])
    field = RANKING_FIELDS[metric]
    rows = connection.execute(
        f"""
        WITH {_latest_cte()}
        SELECT p.id AS publication_id,p.youtube_video_id,p.channel_id,p.title,p.thumbnail_url,
               COALESCE(NULLIF(p.public_url,''),NULLIF(p.youtube_url,''),p.post_url) AS public_url,
               COALESCE(NULLIF(p.authorized_account_id,''),p.account) AS authorized_account_id,
               COALESCE(a.channel_title,p.account_label,p.account) AS current_channel_title,
               COALESCE(NULLIF(p.platform_username_snapshot,''),p.account_label,p.account) AS published_channel_title,
               p.published_local_at,p.source_platform,
               COALESCE(NULLIF(p.source_category,''),'unknown') AS source_category,
               COALESCE(NULLIF(p.source_keyword,''),'unknown') AS source_keyword,
               p.candidate_id,p.slice_id,p.version_id,p.publish_task_id,
               s.view_count,s.like_count,s.comment_count,s.share_count,s.analytics_views,
               s.average_view_duration,s.average_view_percentage,s.completion_rate,
               s.completion_raw_ratio,s.completion_bucket_ratio,s.completion_calculation_version,
               s.fetched_at,s.data_through_date,s.api_response_status,
               st.sync_status,st.last_attempted_at,st.last_successful_at,st.next_sync_at,
               st.last_error_category,st.last_error_summary
        FROM publications p
        LEFT JOIN latest s ON s.publication_id=p.id
        LEFT JOIN youtube_sync_states st ON st.publication_id=p.id
        LEFT JOIN youtube_channel_auths a ON a.account=COALESCE(NULLIF(p.authorized_account_id,''),p.account)
        WHERE {where}
        ORDER BY CASE WHEN {field} IS NULL THEN 1 ELSE 0 END,{field} DESC,p.published_at DESC,p.id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, int(page_size), int((page - 1) * page_size)),
    ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
        "metric": metric,
    }


def analytics_accounts(config: dict[str, Any]) -> list[dict[str, Any]]:
    connection = connect_db(config)
    rows = connection.execute(
        """
        SELECT a.account AS account_id,a.channel_id,a.channel_title AS current_channel_title,
               a.status,a.last_verified_at,
               SUM(CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END) AS publication_count
        FROM youtube_channel_auths a
        LEFT JOIN publications p ON p.platform='youtube' AND COALESCE(NULLIF(p.authorized_account_id,''),p.account)=a.account
        GROUP BY a.account,a.channel_id,a.channel_title,a.status,a.last_verified_at
        ORDER BY a.account
        """
    ).fetchall()
    return [dict(row) for row in rows]


def analytics_history(config: dict[str, Any], publication_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    connection = connect_db(config)
    rows = connection.execute(
        """
        SELECT * FROM (
          SELECT * FROM youtube_metric_snapshots
          WHERE publication_id=? ORDER BY fetched_at DESC,id DESC LIMIT ?
        ) recent ORDER BY fetched_at ASC,id ASC
        """,
        (int(publication_id), int(limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def publication_latest(config: dict[str, Any], publication_id: int) -> dict[str, Any] | None:
    page = analytics_ranking(
        config,
        range_name="all",
        page=1,
        page_size=1,
        _publication_id=int(publication_id),
    )
    return page["items"][0] if page["items"] else None


def retry_publication_sync(config: dict[str, Any], publication_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM youtube_sync_states WHERE publication_id=?", (int(publication_id),)).fetchone()
    if not row:
        schedule_first_sync(config, int(publication_id))
    timestamp = _iso(now or datetime.now(timezone.utc))
    cursor = connection.execute(
        """
        UPDATE youtube_sync_states SET sync_status='PENDING',next_sync_at=?,retry_count=0,
          last_error_category='',last_error_summary='',lease_owner='',lease_expires_at=NULL,
          backfill_run_id=NULL,updated_at=?
        WHERE publication_id=? AND sync_status!='IN_PROGRESS'
        """,
        (timestamp, timestamp, int(publication_id)),
    )
    connection.commit()
    if not cursor.rowcount:
        raise ValueError("publication sync is already running")
    return dict(connection.execute("SELECT * FROM youtube_sync_states WHERE publication_id=?", (int(publication_id),)).fetchone())


def _backfill_candidates(config: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    connection = connect_db(config)
    cutoff = _iso(now - timedelta(hours=24))
    rows = connection.execute(
        """
        SELECT p.*,a.scopes,a.channel_title
        FROM publications p
        LEFT JOIN youtube_channel_auths a ON a.account=COALESCE(NULLIF(p.authorized_account_id,''),p.account)
        WHERE p.platform='youtube' AND p.status='PUBLISHED' AND p.published_at<=?
          AND COALESCE(NULLIF(p.youtube_video_id,''),p.platform_video_id,'')!=''
          AND COALESCE(NULLIF(p.public_status,''),NULLIF(p.privacy_status,''),'public')='public'
          AND NOT EXISTS(
            SELECT 1 FROM youtube_metric_snapshots s
            WHERE s.publication_id=p.id
              AND (s.api_response_status='SUCCESS' OR s.api_response_status LIKE 'PARTIAL_%')
          )
        ORDER BY COALESCE(NULLIF(p.authorized_account_id,''),p.account),p.published_at,p.id
        """,
        (cutoff,),
    ).fetchall()
    return [dict(row) for row in rows]


def backfill_report(
    config: dict[str, Any],
    *,
    now: datetime | None = None,
    dry_run: bool = True,
    rate_limit_per_minute: int = 6,
) -> dict[str, Any]:
    if rate_limit_per_minute < 1 or rate_limit_per_minute > 60:
        raise ValueError("rate_limit_per_minute must be between 1 and 60")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = _backfill_candidates(config, current)
    per_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        per_account[str(row.get("authorized_account_id") or row.get("account") or "unknown")].append(row)
    analytics_videos = sum(
        len(account_rows)
        for account_rows in per_account.values()
        if account_rows and ANALYTICS_SCOPE in _scope_set(str(account_rows[0].get("scopes") or ""))
    )
    report = {
        "dry_run": dry_run,
        "generated_at": _iso(current),
        "video_count": len(rows),
        "accounts": [
            {"account_id": account, "video_count": len(account_rows)}
            for account, account_rows in sorted(per_account.items())
        ],
        "estimated_requests": {
            "data_api": sum(math.ceil(len(account_rows) / 50) for account_rows in per_account.values()),
            "analytics_metrics": analytics_videos,
            "analytics_retention": analytics_videos,
        },
        "missing_metadata_count": sum(
            1
            for row in rows
            if str(row.get("source_category") or "unknown").lower() == "unknown"
            or str(row.get("source_keyword") or "unknown").lower() == "unknown"
        ),
        "rate_limit_per_minute": rate_limit_per_minute,
    }
    if dry_run:
        return report
    connection = connect_db(config)
    timestamp = _iso(current)
    cursor = connection.execute(
        "INSERT INTO youtube_backfill_runs(status,dry_run,rate_limit_per_minute,report_json,created_at,updated_at,started_at) VALUES('RUNNING',0,?,?,?,?,?)",
        (rate_limit_per_minute, json.dumps(report, ensure_ascii=False), timestamp, timestamp, timestamp),
    )
    run_id = int(cursor.lastrowid)
    connection.commit()
    interval = 60 / rate_limit_per_minute
    account_offsets: dict[str, int] = defaultdict(int)
    for row in rows:
        account_id = str(row.get("authorized_account_id") or row.get("account") or "")
        due = current + timedelta(seconds=account_offsets[account_id] * interval)
        published = _parse_datetime(str(row.get("published_at") or ""))
        if not published:
            continue
        first_due = published.astimezone(timezone.utc) + timedelta(hours=24)
        connection.execute(
            """
            INSERT INTO youtube_sync_states(
              publication_id,account_id,backfill_run_id,first_sync_due_at,next_sync_at,
              sync_status,created_at,updated_at
            ) VALUES(?,?,?,?,?,'PENDING',?,?)
            ON CONFLICT(publication_id) DO UPDATE SET
              account_id=excluded.account_id,backfill_run_id=excluded.backfill_run_id,
              next_sync_at=excluded.next_sync_at,sync_status='PENDING',retry_count=0,
              last_error_category='',last_error_summary='',updated_at=excluded.updated_at
            WHERE youtube_sync_states.sync_status!='IN_PROGRESS'
            """,
            (row["id"], account_id, run_id, _iso(first_due), _iso(due), timestamp, timestamp),
        )
        if not row.get("published_local_at"):
            connection.execute(
                "UPDATE publications SET published_local_at=? WHERE id=?",
                (published.astimezone(SAO_PAULO).isoformat(), row["id"]),
            )
        account_offsets[account_id] += 1
    connection.commit()
    return {**report, "run_id": run_id, "status": "RUNNING"}


def set_backfill_status(config: dict[str, Any], run_id: int, action: str) -> dict[str, Any]:
    statuses = {"pause": "PAUSED", "resume": "RUNNING", "cancel": "CANCELLED"}
    if action not in statuses:
        raise ValueError("action must be pause, resume, or cancel")
    connection = connect_db(config)
    timestamp = now_iso()
    cursor = connection.execute(
        "UPDATE youtube_backfill_runs SET status=?,updated_at=? WHERE id=? AND status NOT IN ('COMPLETED','CANCELLED')",
        (statuses[action], timestamp, int(run_id)),
    )
    connection.commit()
    if not cursor.rowcount:
        raise ValueError("backfill run is not mutable")
    if action == "cancel":
        connection.execute(
            """
            UPDATE youtube_sync_states SET sync_status='CANCELLED',next_sync_at=NULL,
              lease_owner='',lease_expires_at=NULL,updated_at=?
            WHERE backfill_run_id=? AND sync_status!='IN_PROGRESS'
            """,
            (timestamp, int(run_id)),
        )
        connection.commit()
    return dict(connection.execute("SELECT * FROM youtube_backfill_runs WHERE id=?", (int(run_id),)).fetchone())


def run_analytics_worker(
    config: dict[str, Any],
    *,
    once: bool = False,
    sleep_sec: int = 60,
    limit: int = 50,
) -> None:
    while True:
        print(json.dumps(sync_due_once(config, limit=limit), ensure_ascii=False), flush=True)
        if once:
            return
        time.sleep(max(15, int(sleep_sec)))


def rollback_youtube_analytics_schema(config: dict[str, Any]) -> None:
    # Older application versions ignore these additive tables and columns. Keeping
    # them is the rollback path because successful snapshots must survive a code rollback.
    connect_db(config).close()
