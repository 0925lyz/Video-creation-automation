from __future__ import annotations

import json
from typing import Any, Callable

from .core import append_event, connect_db, now_iso
from .x_publisher import delete_x_publication
from .youtube_publisher import delete_youtube_publication


RemoteDelete = Callable[[dict[str, Any], dict[str, Any]], None]


def delete_remote_publication(config: dict[str, Any], publication: dict[str, Any]) -> None:
    platform = str(publication.get("platform") or "").lower()
    if platform == "youtube":
        delete_youtube_publication(config, publication)
        return
    if platform == "x":
        delete_x_publication(config, publication)
        return
    raise ValueError(f"published cancellation is not supported for {platform or 'this platform'}")


def _refresh_candidate_published_flag(connection: Any, candidate_id: str, timestamp: str) -> None:
    still_published = connection.execute(
        "SELECT 1 FROM publications WHERE candidate_id=? AND status='PUBLISHED' LIMIT 1",
        (candidate_id,),
    ).fetchone()
    connection.execute(
        "UPDATE candidates SET published_flag=?,updated_at=? WHERE id=?",
        (1 if still_published else 0, timestamp, candidate_id),
    )


def cancel_publication(
    config: dict[str, Any],
    publication_id: int,
    *,
    actor: str = "",
    remote_delete: RemoteDelete = delete_remote_publication,
) -> dict[str, Any]:
    if int(publication_id) <= 0:
        raise ValueError("publication_id must be a positive integer")
    connection = connect_db(config)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT * FROM publications WHERE id=?", (int(publication_id),)
        ).fetchone()
        if not row:
            raise ValueError("publication does not exist")
        publication = dict(row)
        status = str(publication.get("status") or "").upper()
        if status == "CANCELLED":
            connection.rollback()
            return {"publication_id": int(publication_id), "status": "CANCELLED", "remote_deleted": False}
        if status == "PUBLISHING":
            raise ValueError("publication is currently publishing and cannot be cancelled")
        if status not in {"QUEUED", "SCHEDULED", "PUBLISHED"}:
            raise ValueError(f"publication cannot be cancelled from status {status or 'unknown'}")
        if status == "PUBLISHED":
            if str(publication.get("publication_origin") or "") != "SYSTEM_AUTO_PUBLISH":
                raise ValueError("only factory-created publications can be removed from a platform")
            if str(publication.get("platform") or "").lower() not in {"youtube", "x"}:
                raise ValueError("published cancellation is supported only for YouTube and X")
            connection.execute(
                "UPDATE publications SET status='CANCELLING',updated_at=? WHERE id=? AND status='PUBLISHED'",
                (now_iso(), int(publication_id)),
            )
            connection.commit()
        else:
            timestamp = now_iso()
            cursor = connection.execute(
                """
                UPDATE publications
                SET status='CANCELLED',completed_at=?,error='',error_json='{}',updated_at=?
                WHERE id=? AND status IN ('QUEUED','SCHEDULED')
                """,
                (timestamp, timestamp, int(publication_id)),
            )
            if not cursor.rowcount:
                raise ValueError("publication status changed before cancellation")
            append_event(
                connection,
                str(publication["candidate_id"]),
                "PUBLICATION_CANCELLED",
                {"publication_id": int(publication_id), "actor": actor or "dashboard", "remote_deleted": False},
                commit=False,
            )
            connection.commit()
            return {"publication_id": int(publication_id), "status": "CANCELLED", "remote_deleted": False}
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise

    try:
        remote_delete(config, publication)
    except Exception as error:
        timestamp = now_iso()
        payload = {"type": error.__class__.__name__, "message": str(error)[:1000]}
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE publications
            SET status='PUBLISHED',error=?,error_json=?,updated_at=?
            WHERE id=? AND status='CANCELLING'
            """,
            (str(error)[:4000], json.dumps(payload, ensure_ascii=False), timestamp, int(publication_id)),
        )
        append_event(
            connection,
            str(publication["candidate_id"]),
            "PUBLICATION_CANCEL_FAILED",
            {"publication_id": int(publication_id), "actor": actor or "dashboard", **payload},
            commit=False,
        )
        connection.commit()
        raise

    timestamp = now_iso()
    connection.execute("BEGIN IMMEDIATE")
    try:
        cursor = connection.execute(
            """
            UPDATE publications
            SET status='CANCELLED',completed_at=?,error='',error_json='{}',updated_at=?
            WHERE id=? AND status='CANCELLING'
            """,
            (timestamp, timestamp, int(publication_id)),
        )
        if not cursor.rowcount:
            raise ValueError("publication status changed during remote cancellation")
        connection.execute(
            """
            UPDATE youtube_sync_states
            SET sync_status='CANCELLED',next_sync_at=NULL,lease_owner='',lease_expires_at=NULL,updated_at=?
            WHERE publication_id=?
            """,
            (timestamp, int(publication_id)),
        )
        _refresh_candidate_published_flag(connection, str(publication["candidate_id"]), timestamp)
        append_event(
            connection,
            str(publication["candidate_id"]),
            "PUBLICATION_CANCELLED",
            {
                "publication_id": int(publication_id),
                "actor": actor or "dashboard",
                "platform": publication.get("platform") or "",
                "remote_deleted": True,
            },
            commit=False,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"publication_id": int(publication_id), "status": "CANCELLED", "remote_deleted": True}
