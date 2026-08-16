from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable

from .core import append_event, connect_db, now_iso
from .publisher import parse_datetime, publishing_timezone
from .youtube_publisher import upload_youtube_publication


Uploader = Callable[[dict[str, Any], int], dict[str, Any]]


def due_publications(config: dict[str, Any], *, limit: int = 3, now: datetime | None = None) -> list[dict[str, Any]]:
    connection = connect_db(config)
    rows = connection.execute(
        """
        SELECT *
        FROM publications
        WHERE platform='youtube' AND status IN ('QUEUED','SCHEDULED')
        ORDER BY scheduled_at ASC,id ASC
        LIMIT 100
        """
    ).fetchall()
    now_by_timezone: dict[str, datetime] = {}
    due: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        timezone_name = str(item.get("timezone") or publishing_timezone(config, None))
        scheduled = parse_datetime(str(item.get("scheduled_at") or ""))
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
        SET status='FAILED',error=?,error_json=?,updated_at=?
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
    uploader: Uploader = upload_youtube_publication,
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
            SET status='PUBLISHING',updated_at=?
            WHERE id=? AND status IN ('QUEUED','SCHEDULED')
            """,
            (timestamp, publication["id"]),
        )
        connection.commit()
        if not cursor.rowcount:
            continue
        append_event(connection, str(publication["candidate_id"]), "PUBLICATION_STARTED", {
            "publication_id": publication["id"],
            "account": publication.get("account") or "",
        })
        try:
            upload = uploader(config, int(publication["id"]))
            published_at = str(upload.get("published_at") or now_iso())
            youtube_video_id = str(upload.get("youtube_video_id") or "")
            youtube_url = str(upload.get("youtube_url") or "")
            connection.execute(
                """
                UPDATE publications
                SET status='PUBLISHED',published_at=?,youtube_video_id=?,youtube_url=?,post_url=?,
                    error='',error_json='{}',updated_at=?
                WHERE id=?
                """,
                (published_at, youtube_video_id, youtube_url, youtube_url, published_at, publication["id"]),
            )
            connection.execute(
                "UPDATE candidates SET published_flag=1,updated_at=? WHERE id=?",
                (published_at, publication["candidate_id"]),
            )
            connection.commit()
            append_event(connection, str(publication["candidate_id"]), "PUBLICATION_PUBLISHED", {
                "publication_id": publication["id"],
                "account": publication.get("account") or "",
                "youtube_video_id": youtube_video_id,
                "youtube_url": youtube_url,
                "published_at": published_at,
            })
            published += 1
            results.append({"publication_id": publication["id"], "status": "PUBLISHED", "youtube_url": youtube_url})
        except Exception as error:
            failed += 1
            mark_publication_failed(connection, publication, error)
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
