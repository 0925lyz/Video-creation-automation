from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .analytics_schedule import POST_PUBLISH_SYNC_SCHEDULE_VERSION
from .core import append_event, connect_db, now_iso
from .publisher import parse_datetime, publishing_timezone
from .youtube_publisher import upload_youtube_publication
from .x_publisher import upload_x_publication


Uploader = Callable[[dict[str, Any], int], dict[str, Any]]


def _publication_source_metadata(connection: Any, candidate_id: str) -> tuple[str, str]:
    row = connection.execute(
        "SELECT metadata_json FROM candidates WHERE id=?", (candidate_id,)
    ).fetchone()
    try:
        metadata = json.loads(str(row["metadata_json"] or "{}")) if row else {}
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    category = next(
        (str(metadata.get(key) or "").strip() for key in ("initial_category", "category", "category_label") if str(metadata.get(key) or "").strip()),
        "unknown",
    )
    keyword = next(
        (str(metadata.get(key) or "").strip() for key in ("keyword", "initial_keyword", "crawl_keyword", "search_keyword") if str(metadata.get(key) or "").strip()),
        "unknown",
    )
    return category, keyword


def due_publications(config: dict[str, Any], *, limit: int = 3, now: datetime | None = None) -> list[dict[str, Any]]:
    connection = connect_db(config)
    rows = connection.execute(
        """
        SELECT *
        FROM publications
        WHERE platform IN ('youtube','x')
          AND operation_type='PUBLICATION'
          AND review_status='APPROVED'
          AND status IN ('QUEUED','SCHEDULED')
        ORDER BY COALESCE(scheduled_utc_at, scheduled_at) ASC,id ASC
        LIMIT 100
        """
    ).fetchall()
    now_by_timezone: dict[str, datetime] = {}
    due: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        timezone_name = str(item.get("timezone") or publishing_timezone(config, None))
        scheduled_values = [
            parsed
            for parsed in (
                parse_datetime(str(item.get("scheduled_utc_at") or "")),
                parse_datetime(str(item.get("scheduled_at") or "")),
            )
            if parsed is not None
        ]
        scheduled = min(scheduled_values) if scheduled_values else None
        if not scheduled:
            due.append(item)
        else:
            local_now = now or now_by_timezone.get(timezone_name)
            if local_now is None:
                local_now = datetime.now(scheduled.tzinfo)
                now_by_timezone[timezone_name] = local_now
            if scheduled <= local_now.astimezone(scheduled.tzinfo):
                due.append(item)
        if len(due) >= limit:
            break
    return due


def mark_publication_failed(connection: Any, publication: dict[str, Any], error: Exception) -> None:
    timestamp = now_iso()
    payload = {"error": str(error), "type": error.__class__.__name__}
    connection.execute(
        """
        UPDATE publications
        SET status='FAILED',error=?,error_json=?,retry_count=retry_count+1,updated_at=?
        WHERE id=?
        """,
        (str(error)[:4000], json.dumps(payload, ensure_ascii=False), timestamp, publication["id"]),
    )
    connection.commit()
    append_event(connection, str(publication["candidate_id"]), "PUBLICATION_FAILED", {
        "publication_id": publication["id"],
        **payload,
    })


def publish_due_once(
    config: dict[str, Any],
    *,
    limit: int = 3,
    dry_run: bool = False,
    uploader: Uploader | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    due = due_publications(config, limit=limit, now=now)
    if dry_run:
        return {"dry_run": True, "due": due, "published": 0, "failed": 0}
    connection = connect_db(config)
    published = 0
    failed = 0
    results: list[dict[str, Any]] = []
    for publication in due:
        timestamp = now_iso()
        cursor = connection.execute(
            """
            UPDATE publications
            SET status='PUBLISHING',started_at=COALESCE(started_at,?),updated_at=?
            WHERE id=? AND review_status='APPROVED' AND status IN ('QUEUED','SCHEDULED')
            """,
            (timestamp, timestamp, publication["id"]),
        )
        connection.commit()
        if not cursor.rowcount:
            continue
        if str(publication.get("publication_origin") or "").upper() == "ORIGINAL_FACTORY":
            from .original_factory import set_original_publication_state

            set_original_publication_state(
                config,
                str(publication["candidate_id"]),
                status="PUBLISHING",
                publication_id=int(publication["id"]),
            )
        append_event(connection, str(publication["candidate_id"]), "PUBLICATION_STARTED", {
            "publication_id": publication["id"],
            "account": publication.get("account") or "",
        })
        try:
            selected_uploader = uploader or (
                upload_x_publication if publication.get("platform") == "x" else upload_youtube_publication
            )
            upload = selected_uploader(config, int(publication["id"]))
            published_at = str(upload.get("published_at") or now_iso())
            platform = str(publication.get("platform") or "")
            platform_video_id = str(
                upload.get("platform_video_id")
                or upload.get("x_post_id")
                or upload.get("youtube_video_id")
                or ""
            )
            public_url = str(upload.get("public_url") or upload.get("x_url") or upload.get("youtube_url") or "")
            youtube_video_id = str(upload.get("youtube_video_id") or "")
            youtube_url = str(upload.get("youtube_url") or "")
            if not platform_video_id:
                raise RuntimeError(f"{platform or 'platform'} publish did not return a content id")
            published_dt = parse_datetime(published_at)
            if not published_dt:
                raise RuntimeError(f"{platform or 'platform'} publish returned an invalid published_at")
            published_utc = published_dt.astimezone(timezone.utc)
            timezone_name = str(publication.get("timezone") or publishing_timezone(config, None))
            published_local = published_utc.astimezone(ZoneInfo(timezone_name)).isoformat()
            first_sync_due = (published_utc + timedelta(hours=12)).isoformat()
            discovered_category, discovered_keyword = _publication_source_metadata(
                connection, str(publication["candidate_id"])
            )
            category = str(publication.get("source_category") or "").strip()
            keyword = str(publication.get("source_keyword") or "").strip()
            if not category or category.lower() == "unknown":
                category = discovered_category
            if not keyword or keyword.lower() == "unknown":
                keyword = discovered_keyword
            account_id = str(publication.get("authorized_account_id") or publication.get("account") or "")
            if platform == "youtube":
                auth = connection.execute(
                    "SELECT channel_id,channel_title FROM youtube_channel_auths WHERE account=?",
                    (account_id,),
                ).fetchone()
                auth_username = str(auth["channel_title"] if auth else "")
                channel_id = str((auth["channel_id"] if auth else "") or publication.get("channel_id") or "")
            else:
                auth = connection.execute(
                    "SELECT x_user_id,username FROM x_account_auths WHERE account=?",
                    (account_id,),
                ).fetchone()
                auth_username = str(auth["username"] if auth else "")
                channel_id = ""
            username_snapshot = str(
                publication.get("platform_username_snapshot")
                or publication.get("account_label")
                or auth_username
                or account_id
            )
            thumbnail_url = f"https://i.ytimg.com/vi/{youtube_video_id}/hqdefault.jpg" if youtube_video_id else ""
            publish_task_id = str(publication.get("publish_task_id") or publication.get("idempotency_key") or f"publication:{publication['id']}")
            connection.execute(
                """
                UPDATE publications
                SET status='PUBLISHED',published_at=?,youtube_video_id=?,youtube_url=?,post_url=?,
                    platform_video_id=?,public_url=?,completed_at=?,error='',error_json='{}',updated_at=?
                    ,authorized_account_id=?,platform_account_id=?,platform_username_snapshot=?,
                    channel_id=?,published_local_at=?,source_category=?,source_keyword=?,
                    publish_task_id=?,thumbnail_url=?,public_status=COALESCE(NULLIF(public_status,''),privacy_status)
                WHERE id=?
                """,
                (
                    published_at, youtube_video_id, youtube_url, public_url,
                    platform_video_id, public_url, published_at, published_at,
                    account_id, str(publication.get("platform_account_id") or account_id), username_snapshot, channel_id, published_local,
                    category, keyword, publish_task_id, thumbnail_url, publication["id"],
                ),
            )
            connection.execute(
                "UPDATE candidates SET published_flag=1,updated_at=? WHERE id=?",
                (published_at, publication["candidate_id"]),
            )
            public_status = str(
                publication.get("public_status") or publication.get("privacy_status") or ""
            ).lower()
            if platform == "youtube" and public_status == "public":
                connection.execute(
                    """
                    INSERT INTO youtube_sync_states(
                      publication_id,account_id,first_sync_due_at,next_sync_at,sync_status,
                      next_sync_stage,schedule_version,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(publication_id) DO NOTHING
                    """,
                    (
                        publication["id"], account_id, first_sync_due, first_sync_due,
                        "PENDING", "12h", POST_PUBLISH_SYNC_SCHEDULE_VERSION,
                        published_at, published_at,
                    ),
                )
            connection.commit()
            if str(publication.get("publication_origin") or "").upper() == "ORIGINAL_FACTORY":
                from .original_factory import set_original_publication_state

                set_original_publication_state(
                    config,
                    str(publication["candidate_id"]),
                    status="PUBLISHED",
                    publication_id=int(publication["id"]),
                )
            append_event(connection, str(publication["candidate_id"]), "PUBLICATION_PUBLISHED", {
                "publication_id": publication["id"],
                "account": publication.get("account") or "",
                "platform_video_id": platform_video_id,
                "public_url": public_url,
                "published_at": published_at,
            })
            published += 1
            results.append({"publication_id": publication["id"], "status": "PUBLISHED", "public_url": public_url})
        except Exception as error:
            failed += 1
            mark_publication_failed(connection, publication, error)
            if str(publication.get("publication_origin") or "").upper() == "ORIGINAL_FACTORY":
                from .original_factory import set_original_publication_state

                set_original_publication_state(
                    config,
                    str(publication["candidate_id"]),
                    status="FAILED",
                    publication_id=int(publication["id"]),
                    error=str(error),
                )
            results.append({"publication_id": publication["id"], "status": "FAILED", "error": str(error)})
    return {"dry_run": False, "due": len(due), "published": published, "failed": failed, "results": results}


def run_publish_worker(
    config: dict[str, Any],
    *,
    once: bool = False,
    sleep_sec: int = 60,
    limit: int = 3,
) -> None:
    while True:
        result = publish_due_once(config, limit=limit)
        print(json.dumps(result, ensure_ascii=False))
        if once:
            return
        time.sleep(max(5, int(sleep_sec)))
