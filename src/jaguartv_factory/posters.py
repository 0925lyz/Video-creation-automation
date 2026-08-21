from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from PIL import Image, UnidentifiedImageError

from .core import connect_db, now_iso
from .server_store import storage_root


POSTER_CATEGORIES = {
    "time_location": "时间地点",
    "factor_analysis": "因素分析",
    "match_prediction": "预测比赛",
    "multi_schedule": "多赛程",
    "star_fans": "球星球迷",
}
POSTER_STATUSES = {
    "PENDING_SCREENING": "待筛选",
    "PENDING_REVIEW": "待审核",
    "APPROVED": "审核通过",
}
POSTER_TRANSITIONS = {
    "PENDING_SCREENING": "PENDING_REVIEW",
    "PENDING_REVIEW": "APPROVED",
}
POSTER_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
POSTER_IMAGE_FORMATS = {"PNG", "JPEG", "WEBP"}
POSTER_EXTENSION_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
}
POSTER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
AUDIT_VALUE_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{0,160}$")


class PosterError(ValueError):
    status = 400


class PosterNotFound(PosterError):
    status = 404


class PosterConflict(PosterError):
    status = 409


class PosterForbidden(PosterError):
    status = 403


class PosterInvalidMedia(PosterError):
    status = 422


def validate_poster_id(value: Any) -> str:
    poster_id = str(value or "").strip()
    if not POSTER_ID_PATTERN.fullmatch(poster_id):
        raise PosterError("poster id is invalid")
    return poster_id


def validate_status(value: Any, *, optional: bool = True) -> str:
    status = str(value or "").strip().upper()
    if optional and not status:
        return ""
    if status not in POSTER_STATUSES:
        raise PosterError(f"status must be one of {tuple(POSTER_STATUSES)}")
    return status


def validate_category(value: Any, *, optional: bool = True) -> str:
    category = str(value or "").strip()
    if optional and not category:
        return ""
    if category not in POSTER_CATEGORIES:
        raise PosterError(f"category must be one of {tuple(POSTER_CATEGORIES)}")
    return category


def validate_page(value: Any, *, default: int, maximum: int) -> int:
    try:
        number = int(value if value not in {None, ""} else default)
    except (TypeError, ValueError) as error:
        raise PosterError("pagination values must be integers") from error
    if number < 1 or number > maximum:
        raise PosterError(f"pagination value must be between 1 and {maximum}")
    return number


def validate_audit_value(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not AUDIT_VALUE_PATTERN.fullmatch(text):
        raise PosterError(f"{field} contains unsupported characters")
    return text


def poster_storage_root(config: dict[str, Any]) -> Path:
    raw = str((config.get("storage", {}) or {}).get("poster_subdir") or "posters").strip()
    relative = PurePosixPath(raw)
    if not raw or raw.startswith(("/", "\\")) or "\\" in raw or ".." in relative.parts:
        raise PosterForbidden("poster_subdir must stay inside managed media storage")
    root = (storage_root(config) / Path(*relative.parts)).resolve()
    storage = storage_root(config)
    if storage not in root.parents:
        raise PosterForbidden("poster storage must stay inside managed media storage")
    root.mkdir(parents=True, exist_ok=True)
    return root


def category_fields(category: str) -> dict[str, Any]:
    known = category in POSTER_CATEGORIES
    return {
        "category_id": category,
        "category_label": POSTER_CATEGORIES.get(category, f"未知分类（{category or '空'}）"),
        "category_known": known,
    }


def status_fields(status: str) -> dict[str, str]:
    return {"status_id": status, "status_label": POSTER_STATUSES.get(status, status or "未知状态")}


def category_options() -> list[dict[str, str]]:
    return [{"id": key, "label": label} for key, label in POSTER_CATEGORIES.items()]


def _active_poster_row(connection: Any, poster_id: str) -> Any:
    row = connection.execute(
        "SELECT * FROM posters WHERE id=? AND deleted_at IS NULL", (poster_id,)
    ).fetchone()
    if not row:
        raise PosterNotFound("poster does not exist")
    return row


def _serialize_poster(row: Any) -> dict[str, Any]:
    item = dict(row)
    poster_id = str(item["id"])
    result = {
        "id": poster_id,
        "name": str(item.get("name") or "未命名海报"),
        "source_candidate_id": str(item.get("source_candidate_id") or ""),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "screened_at": item.get("screened_at"),
        "approved_at": item.get("approved_at"),
        "preview_url": f"/api/posters/{quote(poster_id, safe='')}/preview",
        "thumbnail_url": f"/api/posters/{quote(poster_id, safe='')}/thumbnail",
    }
    status = str(item.get("status") or "")
    result.update(category_fields(str(item.get("category") or "")))
    result.update(status_fields(status))
    result["download_url"] = (
        f"/api/posters/{quote(poster_id, safe='')}/download" if status == "APPROVED" else ""
    )
    return result


def poster_counts(config: dict[str, Any], *, category: str = "") -> dict[str, int]:
    category = validate_category(category)
    connection = connect_db(config)
    where = "deleted_at IS NULL"
    params: list[Any] = []
    if category:
        where += " AND category=?"
        params.append(category)
    rows = connection.execute(
        f"SELECT status,COUNT(*) count FROM posters WHERE {where} GROUP BY status", params
    ).fetchall()
    by_status = {str(row["status"]): int(row["count"]) for row in rows}
    return {
        "ALL": sum(by_status.values()),
        **{status: by_status.get(status, 0) for status in POSTER_STATUSES},
    }


def list_posters(
    config: dict[str, Any],
    *,
    status: str = "",
    category: str = "",
    page: int = 1,
    page_size: int = 24,
) -> dict[str, Any]:
    status = validate_status(status)
    category = validate_category(category)
    page = validate_page(page, default=1, maximum=1_000_000)
    page_size = validate_page(page_size, default=24, maximum=100)
    where = ["deleted_at IS NULL"]
    params: list[Any] = []
    if status:
        where.append("status=?")
        params.append(status)
    if category:
        where.append("category=?")
        params.append(category)
    clause = " AND ".join(where)
    connection = connect_db(config)
    total = int(connection.execute(
        f"SELECT COUNT(*) count FROM posters WHERE {clause}", params
    ).fetchone()["count"])
    rows = connection.execute(
        f"""
        SELECT * FROM posters WHERE {clause}
        ORDER BY created_at DESC,id ASC LIMIT ? OFFSET ?
        """,
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": [_serialize_poster(row) for row in rows],
        "counts": poster_counts(config),
        "categories": category_options(),
        "pagination": {"page": page, "page_size": page_size, "total": total, "pages": pages},
    }


def poster_detail(config: dict[str, Any], poster_id: str) -> dict[str, Any]:
    poster_id = validate_poster_id(poster_id)
    row = _active_poster_row(connect_db(config), poster_id)
    return _serialize_poster(row)


def _audit(
    connection: Any,
    *,
    poster_id: str,
    action: str,
    from_status: str,
    to_status: str,
    actor: str,
    request_id: str,
    metadata: dict[str, Any] | None = None,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO poster_audit_events(
          poster_id,action,from_status,to_status,actor,request_id,metadata_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            poster_id,
            action,
            from_status,
            to_status,
            actor,
            request_id,
            json.dumps(metadata or {}, ensure_ascii=False),
            created_at,
        ),
    )


def approve_poster(
    config: dict[str, Any],
    poster_id: str,
    *,
    actor: str = "",
    request_id: str = "",
    expected_status: str = "",
) -> dict[str, Any]:
    poster_id = validate_poster_id(poster_id)
    actor = validate_audit_value(actor, "actor") or "dashboard"
    request_id = validate_audit_value(request_id, "request_id")
    expected_status = validate_status(expected_status)
    connection = connect_db(config)
    connection.execute("BEGIN IMMEDIATE")
    try:
        if request_id:
            previous = connection.execute(
                "SELECT action FROM poster_audit_events WHERE poster_id=? AND request_id=?",
                (poster_id, request_id),
            ).fetchone()
            if previous:
                if previous["action"] != "PASS":
                    raise PosterConflict("request_id was already used for another action")
                connection.commit()
                result = poster_detail(config, poster_id)
                result["idempotent"] = True
                return result
        row = connection.execute("SELECT * FROM posters WHERE id=?", (poster_id,)).fetchone()
        if not row:
            raise PosterNotFound("poster does not exist")
        if row["deleted_at"]:
            raise PosterConflict("deleted poster cannot be approved")
        current = str(row["status"])
        if expected_status and current != expected_status:
            raise PosterConflict(
                f"poster status changed from expected {expected_status} to {current}"
            )
        target = POSTER_TRANSITIONS.get(current)
        if not target:
            raise PosterConflict(f"poster status {current} cannot be approved")
        timestamp = now_iso()
        time_column = "screened_at" if current == "PENDING_SCREENING" else "approved_at"
        cursor = connection.execute(
            f"""
            UPDATE posters SET status=?,updated_at=?,updated_by=?,{time_column}=?
            WHERE id=? AND status=? AND deleted_at IS NULL
            """,
            (target, timestamp, actor, timestamp, poster_id, current),
        )
        if cursor.rowcount != 1:
            raise PosterConflict("poster status changed before approval completed")
        _audit(
            connection,
            poster_id=poster_id,
            action="PASS",
            from_status=current,
            to_status=target,
            actor=actor,
            request_id=request_id,
            created_at=timestamp,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return poster_detail(config, poster_id)


def _managed_poster_path(
    config: dict[str, Any], file_key: str, *, must_exist: bool, verify_image: bool
) -> Path:
    key = str(file_key or "").strip()
    relative = PurePosixPath(key)
    if (
        not key
        or key.startswith(("/", "\\"))
        or "\\" in key
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.suffix.lower() not in POSTER_IMAGE_EXTENSIONS
    ):
        raise PosterForbidden("poster file must stay inside managed poster storage")
    root = poster_storage_root(config)
    unresolved = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PosterForbidden("poster file symbolic links are not allowed")
    try:
        path = unresolved.resolve(strict=must_exist)
    except FileNotFoundError as error:
        raise PosterNotFound("poster file does not exist") from error
    if root not in path.parents or path == root:
        raise PosterForbidden("poster file must stay inside managed poster storage")
    if must_exist and not path.is_file():
        raise PosterNotFound("poster file does not exist")
    if verify_image and must_exist:
        try:
            with Image.open(path) as image:
                image.verify()
                image_format = str(image.format or "").upper()
                if image_format not in POSTER_IMAGE_FORMATS:
                    raise PosterInvalidMedia("poster image format is not supported")
                if image_format != POSTER_EXTENSION_FORMATS[relative.suffix.lower()]:
                    raise PosterInvalidMedia("poster image format does not match its file extension")
        except (UnidentifiedImageError, OSError) as error:
            raise PosterInvalidMedia("poster image is invalid or unreadable") from error
    return path


def resolve_poster_file(
    config: dict[str, Any],
    poster_id: str,
    *,
    require_approved: bool = False,
    thumbnail: bool = False,
) -> Path:
    poster_id = validate_poster_id(poster_id)
    row = _active_poster_row(connect_db(config), poster_id)
    if require_approved and row["status"] != "APPROVED":
        raise PosterForbidden("only approved posters can be downloaded")
    key = str(row["thumbnail_key"] or row["file_key"]) if thumbnail else str(row["file_key"])
    return _managed_poster_path(config, key, must_exist=True, verify_image=True)


def poster_download_name(config: dict[str, Any], poster_id: str) -> str:
    poster_id = validate_poster_id(poster_id)
    row = _active_poster_row(connect_db(config), poster_id)
    extension = PurePosixPath(str(row["file_key"])).suffix.lower()
    name = re.sub(r"[\x00-\x1f\x7f/\\]+", "_", str(row["name"] or poster_id)).strip(" .")
    return f"{name or poster_id}{extension}"


def delete_poster(
    config: dict[str, Any],
    poster_id: str,
    *,
    actor: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    poster_id = validate_poster_id(poster_id)
    actor = validate_audit_value(actor, "actor") or "dashboard"
    request_id = validate_audit_value(request_id, "request_id")
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM posters WHERE id=?", (poster_id,)).fetchone()
    if not row:
        raise PosterNotFound("poster does not exist")
    if row["deleted_at"]:
        if request_id and connection.execute(
            "SELECT 1 FROM poster_audit_events WHERE poster_id=? AND request_id=? AND action='DELETE'",
            (poster_id, request_id),
        ).fetchone():
            return {"id": poster_id, "deleted": True, "idempotent": True, "file_disposition": "unchanged"}
        raise PosterConflict("poster is already deleted")

    keys = list(dict.fromkeys(filter(None, (str(row["file_key"]), str(row["thumbnail_key"] or "")))))
    shared_keys: set[str] = set()
    paths: list[tuple[str, Path]] = []
    for key in keys:
        shared = connection.execute(
            """
            SELECT 1 FROM posters
            WHERE id<>? AND deleted_at IS NULL AND (file_key=? OR thumbnail_key=?) LIMIT 1
            """,
            (poster_id, key, key),
        ).fetchone()
        if shared:
            shared_keys.add(key)
            continue
        try:
            paths.append((key, _managed_poster_path(config, key, must_exist=True, verify_image=False)))
        except PosterNotFound:
            continue

    root = poster_storage_root(config)
    quarantine = root / ".trash" / f"{poster_id}-{uuid.uuid4().hex}"
    moved: list[tuple[Path, Path]] = []
    try:
        for index, (_, source) in enumerate(paths):
            quarantine.mkdir(parents=True, exist_ok=True)
            destination = quarantine / f"{index}-{source.name}"
            os.replace(source, destination)
            moved.append((source, destination))
        connection.execute("BEGIN IMMEDIATE")
        if request_id and connection.execute(
            "SELECT 1 FROM poster_audit_events WHERE poster_id=? AND request_id=?",
            (poster_id, request_id),
        ).fetchone():
            raise PosterConflict("request_id was already used")
        timestamp = now_iso()
        cursor = connection.execute(
            """
            UPDATE posters SET deleted_at=?,updated_at=?,updated_by=?,deleted_by=?
            WHERE id=? AND deleted_at IS NULL
            """,
            (timestamp, timestamp, actor, actor, poster_id),
        )
        if cursor.rowcount != 1:
            raise PosterConflict("poster changed before deletion completed")
        _audit(
            connection,
            poster_id=poster_id,
            action="DELETE",
            from_status=str(row["status"]),
            to_status="DELETED",
            actor=actor,
            request_id=request_id,
            metadata={"shared_keys": sorted(shared_keys), "quarantined_files": len(moved)},
            created_at=timestamp,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        for source, destination in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                os.replace(destination, source)
        if quarantine.exists():
            shutil.rmtree(quarantine, ignore_errors=True)
        raise

    cleanup_failed = ""
    try:
        if quarantine.exists():
            shutil.rmtree(quarantine)
    except OSError as error:
        cleanup_failed = str(error)
        timestamp = now_iso()
        log_connection = connect_db(config)
        _audit(
            log_connection,
            poster_id=poster_id,
            action="DELETE_FILE_CLEANUP_FAILED",
            from_status=str(row["status"]),
            to_status="DELETED",
            actor=actor,
            request_id="",
            metadata={"quarantine": str(quarantine.relative_to(root)), "error": cleanup_failed},
            created_at=timestamp,
        )
        log_connection.commit()

    if shared_keys and not moved:
        disposition = "shared_preserved"
    elif moved:
        disposition = "removed" if not cleanup_failed else "quarantined_cleanup_pending"
    else:
        disposition = "missing"
    return {
        "id": poster_id,
        "deleted": True,
        "file_disposition": disposition,
        "cleanup_pending": bool(cleanup_failed),
    }
