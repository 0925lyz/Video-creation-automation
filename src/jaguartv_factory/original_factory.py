from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .core import connect_db, now_iso, require_binary, run_command
from .server_store import storage_root


ORIGINAL_CATEGORIES = {
    "pre_match_prediction": "赛前预测",
    "pre_match_discussion": "赛前讨论",
    "post_match_score": "赛后比分",
}
ORIGINAL_CATEGORY_PT = {
    "pre_match_prediction": "palpite pre-jogo",
    "pre_match_discussion": "debate pre-jogo",
    "post_match_score": "placar pos-jogo",
}
ORIGINAL_STATUSES = {
    "PENDING_REVIEW": "待审核",
    "APPROVED": "审核通过",
}
ORIGINAL_PUBLISH_STATUSES = {
    "NOT_PUBLISHED", "QUEUED", "SCHEDULED", "PUBLISHING", "PUBLISHED", "FAILED"
}
ORIGINAL_DOWNLOAD_STATUSES = {"NOT_DOWNLOADED", "DOWNLOADED"}
ORIGINAL_SOCIAL_PLATFORMS = {
    "youtube", "x", "twitter", "reddit", "facebook", "instagram", "bilibili",
    "xiaohongshu", "web", "rss",
}
ORIGINAL_IMAGE_LICENSE_STATUSES = {
    "", "not_collected", "unknown", "authorized", "licensed", "public_domain", "restricted"
}
ORIGINAL_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
AUDIT_VALUE_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{0,160}$")
DEFAULT_MAX_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_DURATION_SEC = 15 * 60
DEFAULT_MAX_PIXELS = 16_000_000
DEFAULT_MAX_DIMENSION = 7680
DEFAULT_BATCH_LIMIT = 20


class OriginalFactoryError(ValueError):
    status = 400


class OriginalNotFound(OriginalFactoryError):
    status = 404


class OriginalConflict(OriginalFactoryError):
    status = 409


class OriginalForbidden(OriginalFactoryError):
    status = 403


class OriginalInvalidMedia(OriginalFactoryError):
    status = 422


def validate_original_id(value: Any) -> str:
    item_id = str(value or "").strip().lower()
    if not ORIGINAL_ID_PATTERN.fullmatch(item_id):
        raise OriginalFactoryError("original item id is invalid")
    return item_id


def validate_category(value: Any, *, optional: bool = True) -> str:
    category = str(value or "").strip()
    if optional and not category:
        return ""
    if category not in ORIGINAL_CATEGORIES:
        raise OriginalFactoryError(f"category must be one of {tuple(ORIGINAL_CATEGORIES)}")
    return category


def validate_status(value: Any, *, optional: bool = True) -> str:
    status = str(value or "").strip().upper()
    if optional and not status:
        return ""
    if status not in ORIGINAL_STATUSES:
        raise OriginalFactoryError(f"status must be one of {tuple(ORIGINAL_STATUSES)}")
    return status


def validate_page(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value if value not in {None, ""} else default)
    except (TypeError, ValueError) as error:
        raise OriginalFactoryError("pagination values must be integers") from error
    if parsed < 1 or parsed > maximum:
        raise OriginalFactoryError(f"pagination value must be between 1 and {maximum}")
    return parsed


def validate_audit_value(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not AUDIT_VALUE_PATTERN.fullmatch(text):
        raise OriginalFactoryError(f"{field} contains unsupported characters")
    return text


def original_upload_limits(config: dict[str, Any]) -> dict[str, int]:
    settings = config.get("storage", {}) or {}
    return {
        "max_bytes": max(1, int(settings.get("original_max_upload_bytes", DEFAULT_MAX_BYTES))),
        "max_duration_sec": max(1, int(settings.get("original_max_duration_sec", DEFAULT_MAX_DURATION_SEC))),
        "max_pixels": max(1, int(settings.get("original_max_pixels", DEFAULT_MAX_PIXELS))),
        "max_dimension": max(1, int(settings.get("original_max_dimension", DEFAULT_MAX_DIMENSION))),
        "max_batch": max(1, min(100, int(settings.get("original_max_batch", DEFAULT_BATCH_LIMIT)))),
    }


def original_storage_root(config: dict[str, Any]) -> Path:
    raw = str((config.get("storage", {}) or {}).get("original_factory_subdir") or "original_factory").strip()
    relative = PurePosixPath(raw)
    if not raw or raw.startswith(("/", "\\")) or "\\" in raw or ".." in relative.parts:
        raise OriginalForbidden("original_factory_subdir must stay inside managed media storage")
    storage = storage_root(config)
    root = storage.joinpath(*relative.parts).resolve()
    if storage not in root.parents:
        raise OriginalForbidden("original factory storage must stay inside managed media storage")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_filename(filename: str) -> str:
    value = str(filename or "").strip()
    if (
        not value
        or Path(value).name != value
        or "/" in value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise OriginalFactoryError("filename is invalid or contains a path")
    if Path(value).suffix.lower() != ".mp4":
        raise OriginalInvalidMedia("video extension must be .mp4")
    return value[:240]


def _clean_text(value: Any, field: str, *, maximum: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise OriginalFactoryError(f"{field} is required")
    if len(text) > maximum or any(ord(character) < 32 and character not in "\n\t" for character in text):
        raise OriginalFactoryError(f"{field} is invalid or too long")
    return text


def _normalize_match_time(value: Any, match_date: str) -> str:
    raw = _clean_text(value, "match_time_sao_paulo", maximum=64)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise OriginalFactoryError("match_time_sao_paulo must be an ISO datetime") from error
    timezone = ZoneInfo("America/Sao_Paulo")
    parsed = parsed.replace(tzinfo=timezone) if parsed.tzinfo is None else parsed.astimezone(timezone)
    if parsed.date().isoformat() != match_date:
        raise OriginalFactoryError("match_time_sao_paulo date must match match_date")
    return parsed.isoformat()


def _normalize_generated_at(value: Any) -> str:
    raw = str(value or "").strip() or now_iso()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise OriginalFactoryError("generated_at must be an ISO datetime") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
    return parsed.isoformat()


def _normalize_channels(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 30:
        raise OriginalFactoryError("channels must be a non-empty list with at most 30 items")
    channels: list[str] = []
    for item in value:
        channel = _clean_text(item, "channel", maximum=120)
        if channel not in channels:
            channels.append(channel)
    return channels


def _normalize_json_object(value: Any, field: str, *, maximum_bytes: int = 64_000) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise OriginalFactoryError(f"{field} must be an object")
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise OriginalFactoryError(f"{field} is too large")
    return value


def _normalize_source_url(value: Any, field: str, *, optional: bool = False) -> str:
    raw = str(value or "").strip()
    if optional and not raw:
        return ""
    if len(raw) > 2048 or any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise OriginalFactoryError(f"{field} is invalid")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise OriginalFactoryError(f"{field} must be an http or https URL")
    if parsed.username or parsed.password:
        raise OriginalFactoryError(f"{field} must not contain credentials")
    sensitive = {"access_token", "auth", "authorization", "cookie", "oauth_token", "session", "token"}
    if {name.lower() for name, _ in parse_qsl(parsed.query, keep_blank_values=True)} & sensitive:
        raise OriginalFactoryError(f"{field} must not contain credentials")
    host = str(parsed.hostname).rstrip(".").lower()
    try:
        port = parsed.port
    except ValueError as error:
        raise OriginalFactoryError(f"{field} contains an invalid port") from error
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def _normalize_social_sources(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise OriginalFactoryError("social_sources must be a list with at most 100 items")
    sources: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise OriginalFactoryError("each social source must be an object")
        platform = str(raw.get("platform") or "").strip().lower()
        if platform not in ORIGINAL_SOCIAL_PLATFORMS:
            raise OriginalFactoryError("social source platform is unsupported")
        fetched_at = _normalize_generated_at(raw.get("fetched_at"))
        try:
            confidence = float(raw.get("confidence", 0))
        except (TypeError, ValueError) as error:
            raise OriginalFactoryError("social source confidence must be numeric") from error
        if confidence < 0 or confidence > 1:
            raise OriginalFactoryError("social source confidence must be between 0 and 1")
        license_status = str(raw.get("image_license_status") or "").strip().lower()
        if license_status not in ORIGINAL_IMAGE_LICENSE_STATUSES:
            raise OriginalFactoryError("image license status is unsupported")
        sources.append(
            {
                "source_url": _normalize_source_url(raw.get("source_url"), "source_url"),
                "platform": platform,
                "fetched_at": fetched_at,
                "summary": _clean_text(raw.get("summary"), "summary", maximum=2_000),
                "confidence": confidence,
                "uncertain": bool(raw.get("uncertain")),
                "image_source_url": _normalize_source_url(
                    raw.get("image_source_url"), "image_source_url", optional=True
                ),
                "image_license_status": license_status,
                "metadata": _normalize_json_object(raw.get("metadata"), "social source metadata", maximum_bytes=16_000),
            }
        )
    return sources


def _probe_video(path: Path) -> dict[str, Any]:
    result = run_command(
        [
            require_binary("ffprobe"), "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ],
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise OriginalInvalidMedia("ffprobe could not read uploaded video")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as error:
        raise OriginalInvalidMedia("ffprobe returned invalid video metadata") from error
    format_names = {part.strip().lower() for part in str((payload.get("format") or {}).get("format_name") or "").split(",")}
    if "mp4" not in format_names:
        raise OriginalInvalidMedia("ffprobe detected a non-MP4 container")
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    videos = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"]
    if not videos:
        raise OriginalInvalidMedia("ffprobe found no playable video stream")
    video = videos[0]
    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        duration = float(video.get("duration") or (payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError) as error:
        raise OriginalInvalidMedia("ffprobe returned invalid dimensions or duration") from error
    if width <= 0 or height <= 0 or duration <= 0:
        raise OriginalInvalidMedia("video dimensions or duration are invalid")
    return {
        "width": width,
        "height": height,
        "duration_sec": duration,
        "video_codec": str(video.get("codec_name") or ""),
    }


def _write_and_validate_video(
    config: dict[str, Any], stream: BinaryIO, *, filename: str, mime_type: str, content_length: int
) -> tuple[Path, dict[str, Any]]:
    original_name = _safe_filename(filename)
    declared_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if declared_mime != "video/mp4":
        raise OriginalInvalidMedia("video MIME type must be video/mp4")
    limits = original_upload_limits(config)
    if content_length <= 0:
        raise OriginalInvalidMedia("video file is empty")
    if content_length > limits["max_bytes"]:
        raise OriginalInvalidMedia(f"video exceeds size limit of {limits['max_bytes']} bytes")
    pending = original_storage_root(config) / ".pending"
    pending.mkdir(parents=True, exist_ok=True)
    temporary = pending / f"{uuid.uuid4().hex}.uploading"
    digest = hashlib.sha256()
    written = 0
    try:
        with temporary.open("xb") as handle:
            while written < content_length:
                chunk = stream.read(min(1024 * 1024, content_length - written))
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                written += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if written != content_length:
            raise OriginalInvalidMedia(
                f"incomplete video upload: expected {content_length} bytes, received {written}"
            )
        probe = _probe_video(temporary)
        if max(probe["width"], probe["height"]) > limits["max_dimension"]:
            raise OriginalInvalidMedia("video exceeds configured dimension limit")
        if probe["width"] * probe["height"] > limits["max_pixels"]:
            raise OriginalInvalidMedia("video exceeds configured pixel limit")
        if probe["duration_sec"] > limits["max_duration_sec"]:
            raise OriginalInvalidMedia("video exceeds configured duration limit")
        return temporary, {
            "original_name": original_name,
            "mime_type": "video/mp4",
            "size_bytes": written,
            "sha256": digest.hexdigest(),
            **probe,
        }
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _move_uploaded_video(config: dict[str, Any], temporary: Path) -> tuple[str, Path]:
    relative = PurePosixPath("videos") / f"{uuid.uuid4().hex}.mp4"
    destination = original_storage_root(config).joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, destination)
    return str(relative), destination


def _create_thumbnail(config: dict[str, Any], source: Path, duration_sec: float) -> tuple[str, Path]:
    relative = PurePosixPath("thumbnails") / f"{uuid.uuid4().hex}.jpg"
    destination = original_storage_root(config).joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.creating.jpg")
    seek = min(max(duration_sec * 0.1, 0.05), max(duration_sec - 0.05, 0.05))
    result = run_command(
        [
            require_binary("ffmpeg"), "-y", "-ss", f"{seek:.3f}", "-i", str(source),
            "-frames:v", "1", "-vf", "scale=640:640:force_original_aspect_ratio=decrease",
            "-q:v", "3", str(temporary),
        ],
        check=False,
        timeout=90,
    )
    if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
        temporary.unlink(missing_ok=True)
        raise OriginalInvalidMedia("thumbnail generation failed for uploaded video")
    os.replace(temporary, destination)
    return str(relative), destination


def _audit(
    connection: Any,
    *,
    item_id: str,
    action: str,
    from_status: str = "",
    to_status: str = "",
    actor: str = "",
    request_id: str = "",
    metadata: dict[str, Any] | None = None,
    created_at: str,
) -> None:
    connection.execute(
        """INSERT INTO original_factory_audit_events(
             item_id,action,from_status,to_status,actor,request_id,metadata_json,created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            item_id, action, from_status, to_status, actor, request_id,
            json.dumps(metadata or {}, ensure_ascii=False), created_at,
        ),
    )


def _active_row(connection: Any, item_id: str) -> Any:
    row = connection.execute(
        "SELECT * FROM original_factory_items WHERE id=? AND deleted_at IS NULL", (item_id,)
    ).fetchone()
    if not row:
        raise OriginalNotFound("original factory item does not exist")
    return row


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        parsed = []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _serialize_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    item_id = str(item["id"])
    status = str(item.get("status") or "")
    category = str(item.get("category") or "")
    return {
        "id": item_id,
        "name": str(item.get("name") or item.get("match_name") or "未命名原创视频"),
        "original_name": str(item.get("original_name") or ""),
        "mime_type": str(item.get("mime_type") or "video/mp4"),
        "size_bytes": int(item.get("size_bytes") or 0),
        "duration_sec": float(item.get("duration_sec") or 0),
        "width": int(item.get("width") or 0),
        "height": int(item.get("height") or 0),
        "video_codec": str(item.get("video_codec") or ""),
        "generated_at": str(item.get("generated_at") or ""),
        "match_name": str(item.get("match_name") or ""),
        "match_date": str(item.get("match_date") or ""),
        "match_time_sao_paulo": str(item.get("match_time_sao_paulo") or ""),
        "channels": [str(channel) for channel in _json_list(item.get("channels_json"))],
        "category_id": category,
        "category_label": ORIGINAL_CATEGORIES.get(category, f"未知标签（{category or '空'}）"),
        "category_known": category in ORIGINAL_CATEGORIES,
        "status_id": status,
        "status_label": ORIGINAL_STATUSES.get(status, status or "未知状态"),
        "publish_status": str(item.get("publish_status") or "NOT_PUBLISHED"),
        "download_status": str(item.get("download_status") or "NOT_DOWNLOADED"),
        "last_downloaded_at": item.get("last_downloaded_at"),
        "last_publication_id": item.get("last_publication_id"),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "approved_at": item.get("approved_at"),
        "preview_url": f"/api/originals/{quote(item_id, safe='')}/preview",
        "thumbnail_url": f"/api/originals/{quote(item_id, safe='')}/thumbnail",
        "download_url": f"/api/originals/{quote(item_id, safe='')}/file" if status == "APPROVED" else "",
        "publish_asset": {
            "id": item_id,
            "filename": _download_name_from_row(item),
            "variant": ORIGINAL_CATEGORIES.get(category, category),
            "download_url": f"/api/originals/{quote(item_id, safe='')}/file" if status == "APPROVED" else "",
            "source_kind": "original_factory",
        },
    }


def _social_sources(connection: Any, item_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM original_factory_social_sources WHERE item_id=? ORDER BY created_at,id",
        (item_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["uncertain"] = bool(item.get("uncertain"))
        item["metadata"] = _json_object(item.pop("metadata_json", "{}"))
        result.append(item)
    return result


def original_detail(config: dict[str, Any], item_id: str) -> dict[str, Any]:
    item_id = validate_original_id(item_id)
    connection = connect_db(config)
    row = _active_row(connection, item_id)
    result = _serialize_item(row)
    result["match_info"] = _json_object(row["match_info_json"])
    result["metadata"] = _json_object(row["metadata_json"])
    result["social_sources"] = _social_sources(connection, item_id)
    return result


def original_counts(config: dict[str, Any], *, category: str = "") -> dict[str, int]:
    category = validate_category(category)
    where = "deleted_at IS NULL"
    params: list[Any] = []
    if category:
        where += " AND category=?"
        params.append(category)
    rows = connect_db(config).execute(
        f"SELECT status,COUNT(*) count FROM original_factory_items WHERE {where} GROUP BY status",
        params,
    ).fetchall()
    by_status = {str(row["status"]): int(row["count"]) for row in rows}
    return {
        "ALL": sum(by_status.values()),
        "PENDING_REVIEW": by_status.get("PENDING_REVIEW", 0),
        "APPROVED": by_status.get("APPROVED", 0),
    }


def list_original_items(
    config: dict[str, Any], *, status: str = "PENDING_REVIEW", category: str = "",
    page: int = 1, page_size: int = 24,
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
        f"SELECT COUNT(*) count FROM original_factory_items WHERE {clause}", params
    ).fetchone()["count"])
    rows = connection.execute(
        f"SELECT * FROM original_factory_items WHERE {clause} "
        "ORDER BY generated_at DESC,created_at DESC,id LIMIT ? OFFSET ?",
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": [_serialize_item(row) for row in rows],
        "counts": original_counts(config),
        "categories": [{"id": key, "label": label} for key, label in ORIGINAL_CATEGORIES.items()],
        "pagination": {"page": page, "page_size": page_size, "total": total, "pages": pages},
    }


def import_original_video(
    config: dict[str, Any], stream: BinaryIO, *, filename: str, mime_type: str,
    content_length: int, category: Any, match_name: Any, match_date: Any,
    match_time_sao_paulo: Any, channels: Any, match_info: Any = None,
    social_sources: Any = None, generated_at: Any = None, actor: str = "",
    request_id: str = "", metadata: Any = None, batch_size: Any = 1,
) -> dict[str, Any]:
    try:
        batch_size_value = int(batch_size)
    except (TypeError, ValueError) as error:
        raise OriginalFactoryError("batch_size must be an integer") from error
    if batch_size_value < 1 or batch_size_value > original_upload_limits(config)["max_batch"]:
        raise OriginalFactoryError("batch_size exceeds the configured batch limit")
    category_value = validate_category(category, optional=False)
    match_name_value = _clean_text(match_name, "match_name", maximum=240)
    match_date_value = _clean_text(match_date, "match_date", maximum=10)
    try:
        date.fromisoformat(match_date_value)
    except ValueError as error:
        raise OriginalFactoryError("match_date must be an ISO date") from error
    match_time_value = _normalize_match_time(match_time_sao_paulo, match_date_value)
    generated_at_value = _normalize_generated_at(generated_at)
    channel_values = _normalize_channels(channels)
    match_info_value = _normalize_json_object(match_info, "match_info")
    social_values = _normalize_social_sources(social_sources)
    metadata_value = _normalize_json_object(metadata, "metadata", maximum_bytes=32_000)
    actor_value = validate_audit_value(actor, "actor") or "original-worker"
    request_value = validate_audit_value(request_id, "request_id")

    temporary, media = _write_and_validate_video(
        config, stream, filename=filename, mime_type=mime_type, content_length=content_length
    )
    created_paths: list[Path] = []
    connection = connect_db(config)
    try:
        if request_value:
            prior = connection.execute(
                """SELECT e.item_id,i.sha256,i.deleted_at
                   FROM original_factory_audit_events e
                   JOIN original_factory_items i ON i.id=e.item_id
                   WHERE e.request_id=? AND e.action='IMPORT' LIMIT 1""",
                (request_value,),
            ).fetchone()
            if prior:
                temporary.unlink(missing_ok=True)
                if prior["deleted_at"] or str(prior["sha256"]) != media["sha256"]:
                    raise OriginalConflict("request_id was already used for another upload")
                return original_detail(config, str(prior["item_id"]))
        duplicate = connection.execute(
            "SELECT id FROM original_factory_items WHERE sha256=? AND deleted_at IS NULL LIMIT 1",
            (media["sha256"],),
        ).fetchone()
        if duplicate:
            raise OriginalConflict(f"the same video already exists as original item {duplicate['id']}")
        file_key, video_path = _move_uploaded_video(config, temporary)
        created_paths.append(video_path)
        thumbnail_key, thumbnail_path = _create_thumbnail(config, video_path, media["duration_sec"])
        created_paths.append(thumbnail_path)
        item_id = uuid.uuid4().hex
        timestamp = now_iso()
        connection.execute("BEGIN IMMEDIATE")
        if request_value and connection.execute(
            "SELECT 1 FROM original_factory_audit_events WHERE request_id=?", (request_value,)
        ).fetchone():
            raise OriginalConflict("request_id was already used")
        connection.execute(
            """INSERT INTO original_factory_items(
                 id,name,file_key,thumbnail_key,original_name,mime_type,size_bytes,sha256,
                 duration_sec,width,height,video_codec,generated_at,match_name,match_date,
                 match_time_sao_paulo,channels_json,category,status,match_info_json,metadata_json,
                 uploaded_by,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING_REVIEW',?,?,?,?,?)""",
            (
                item_id, match_name_value, file_key, thumbnail_key, media["original_name"],
                media["mime_type"], media["size_bytes"], media["sha256"], media["duration_sec"],
                media["width"], media["height"], media["video_codec"], generated_at_value,
                match_name_value, match_date_value, match_time_value,
                json.dumps(channel_values, ensure_ascii=False), category_value,
                json.dumps(match_info_value, ensure_ascii=False),
                json.dumps(metadata_value, ensure_ascii=False), actor_value, timestamp, timestamp,
            ),
        )
        for source in social_values:
            connection.execute(
                """INSERT INTO original_factory_social_sources(
                     id,item_id,source_url,platform,fetched_at,summary,confidence,uncertain,
                     image_source_url,image_license_status,metadata_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex, item_id, source["source_url"], source["platform"],
                    source["fetched_at"], source["summary"], source["confidence"],
                    int(source["uncertain"]), source["image_source_url"],
                    source["image_license_status"], json.dumps(source["metadata"], ensure_ascii=False),
                    timestamp,
                ),
            )
        _audit(
            connection, item_id=item_id, action="IMPORT", to_status="PENDING_REVIEW",
            actor=actor_value, request_id=request_value,
            metadata={"sha256": media["sha256"], "social_source_count": len(social_values)},
            created_at=timestamp,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        temporary.unlink(missing_ok=True)
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    return original_detail(config, item_id)


def _validated_ids(values: Any) -> list[str]:
    if not isinstance(values, list) or not values or len(values) > 100:
        raise OriginalFactoryError("item_ids must be a non-empty list with at most 100 items")
    ids = [validate_original_id(value) for value in values]
    if len(ids) != len(set(ids)):
        raise OriginalFactoryError("item_ids must not contain duplicates")
    return ids


def approve_originals(
    config: dict[str, Any], item_ids: Any, *, actor: str = "", request_id: str = ""
) -> dict[str, Any]:
    ids = _validated_ids(item_ids)
    actor_value = validate_audit_value(actor, "actor") or "dashboard"
    request_value = validate_audit_value(request_id, "request_id")
    connection = connect_db(config)
    connection.execute("BEGIN IMMEDIATE")
    try:
        if request_value:
            previous = connection.execute(
                "SELECT item_id,action FROM original_factory_audit_events WHERE request_id=?",
                (request_value,),
            ).fetchall()
            if previous:
                if {str(row["action"]) for row in previous} != {"BULK_APPROVE"} or {
                    str(row["item_id"]) for row in previous
                } != set(ids):
                    raise OriginalConflict("request_id was already used for another operation")
                connection.commit()
                return {"approved": len(ids), "item_ids": ids, "idempotent": True}
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"SELECT * FROM original_factory_items WHERE id IN ({placeholders}) AND deleted_at IS NULL",
            ids,
        ).fetchall()
        if len(rows) != len(ids):
            raise OriginalNotFound("one or more original factory items do not exist")
        invalid = [str(row["id"]) for row in rows if str(row["status"]) != "PENDING_REVIEW"]
        if invalid:
            raise OriginalConflict("all selected items must be PENDING_REVIEW")
        timestamp = now_iso()
        for row in rows:
            cursor = connection.execute(
                """UPDATE original_factory_items SET status='APPROVED',approved_at=?,approved_by=?,
                   updated_at=? WHERE id=? AND status='PENDING_REVIEW' AND deleted_at IS NULL""",
                (timestamp, actor_value, timestamp, row["id"]),
            )
            if cursor.rowcount != 1:
                raise OriginalConflict("original item status changed before approval completed")
            _audit(
                connection, item_id=str(row["id"]), action="BULK_APPROVE",
                from_status="PENDING_REVIEW", to_status="APPROVED", actor=actor_value,
                request_id=request_value, metadata={"batch_size": len(ids)}, created_at=timestamp,
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"approved": len(ids), "item_ids": ids, "idempotent": False}


def _managed_path(
    config: dict[str, Any], file_key: str, *, expected_suffixes: set[str], must_exist: bool = True
) -> Path:
    key = str(file_key or "").strip()
    relative = PurePosixPath(key)
    if (
        not key
        or key.startswith(("/", "\\"))
        or "\\" in key
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.suffix.lower() not in expected_suffixes
    ):
        raise OriginalForbidden("file must stay inside managed original factory storage")
    root = original_storage_root(config)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise OriginalForbidden("original factory file symbolic links are not allowed")
    try:
        resolved = root.joinpath(*relative.parts).resolve(strict=must_exist)
    except FileNotFoundError as error:
        raise OriginalNotFound("original factory file does not exist") from error
    if root not in resolved.parents or resolved == root:
        raise OriginalForbidden("file must stay inside managed original factory storage")
    if must_exist and not resolved.is_file():
        raise OriginalNotFound("original factory file does not exist")
    return resolved


def resolve_original_file(
    config: dict[str, Any], item_id: str, *, require_approved: bool = False,
    thumbnail: bool = False,
) -> Path:
    item_id = validate_original_id(item_id)
    row = _active_row(connect_db(config), item_id)
    if require_approved and str(row["status"]) != "APPROVED":
        raise OriginalForbidden("only approved original videos can be downloaded")
    key = str(row["thumbnail_key"] if thumbnail else row["file_key"])
    return _managed_path(
        config, key, expected_suffixes={".jpg", ".jpeg", ".png", ".webp"} if thumbnail else {".mp4"}
    )


def _download_name_from_row(row: Any) -> str:
    value = re.sub(r"[\x00-\x1f\x7f/\\]+", "_", str(row.get("match_name") or row.get("name") or row["id"]))
    value = value.strip(" .")[:180]
    return f"{value or row['id']}.mp4"


def original_download_name(config: dict[str, Any], item_id: str) -> str:
    row = dict(_active_row(connect_db(config), validate_original_id(item_id)))
    return _download_name_from_row(row)


def register_original_download(
    config: dict[str, Any], item_id: str, *, actor: str = "", request_id: str = ""
) -> dict[str, Any]:
    item_id = validate_original_id(item_id)
    actor_value = validate_audit_value(actor, "actor") or "dashboard"
    request_value = validate_audit_value(request_id, "request_id")
    connection = connect_db(config)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = _active_row(connection, item_id)
        if str(row["status"]) != "APPROVED":
            raise OriginalForbidden("only approved original videos can be registered for download")
        if request_value:
            previous = connection.execute(
                "SELECT action FROM original_factory_audit_events WHERE item_id=? AND request_id=?",
                (item_id, request_value),
            ).fetchone()
            if previous:
                if str(previous["action"]) != "DOWNLOAD_REGISTERED":
                    raise OriginalConflict("request_id was already used for another operation")
                connection.commit()
                return {
                    "id": item_id,
                    "download_url": f"/api/originals/{quote(item_id, safe='')}/file",
                    "idempotent": True,
                }
        timestamp = now_iso()
        connection.execute(
            """UPDATE original_factory_items SET download_status='DOWNLOADED',last_downloaded_at=?,
               updated_at=? WHERE id=? AND status='APPROVED' AND deleted_at IS NULL""",
            (timestamp, timestamp, item_id),
        )
        _audit(
            connection, item_id=item_id, action="DOWNLOAD_REGISTERED", from_status="APPROVED",
            to_status="APPROVED", actor=actor_value, request_id=request_value, created_at=timestamp,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "id": item_id,
        "download_url": f"/api/originals/{quote(item_id, safe='')}/file",
        "idempotent": False,
    }


def delete_originals(
    config: dict[str, Any], item_ids: Any, *, actor: str = "", request_id: str = ""
) -> dict[str, Any]:
    ids = _validated_ids(item_ids)
    actor_value = validate_audit_value(actor, "actor") or "dashboard"
    request_value = validate_audit_value(request_id, "request_id")
    connection = connect_db(config)
    if request_value:
        previous = connection.execute(
            "SELECT item_id,action FROM original_factory_audit_events WHERE request_id=?", (request_value,)
        ).fetchall()
        if previous:
            if {str(row["action"]) for row in previous} == {"BULK_DELETE"} and {
                str(row["item_id"]) for row in previous
            } == set(ids):
                return {"deleted": len(ids), "item_ids": ids, "idempotent": True}
            raise OriginalConflict("request_id was already used for another operation")
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"SELECT * FROM original_factory_items WHERE id IN ({placeholders}) AND deleted_at IS NULL", ids
    ).fetchall()
    if len(rows) != len(ids):
        raise OriginalNotFound("one or more original factory items do not exist")
    root = original_storage_root(config)
    quarantine = root / ".trash" / f"bulk-{uuid.uuid4().hex}"
    moved: list[tuple[Path, Path]] = []
    try:
        for row in rows:
            for key, suffixes in (
                (str(row["file_key"]), {".mp4"}),
                (str(row["thumbnail_key"] or ""), {".jpg", ".jpeg", ".png", ".webp"}),
            ):
                if not key:
                    continue
                try:
                    source = _managed_path(config, key, expected_suffixes=suffixes)
                except OriginalNotFound:
                    continue
                shared = connection.execute(
                    """SELECT 1 FROM original_factory_items
                       WHERE id<>? AND deleted_at IS NULL AND (file_key=? OR thumbnail_key=?) LIMIT 1""",
                    (row["id"], key, key),
                ).fetchone()
                if shared:
                    continue
                quarantine.mkdir(parents=True, exist_ok=True)
                destination = quarantine / f"{len(moved)}-{source.name}"
                os.replace(source, destination)
                moved.append((source, destination))
        connection.execute("BEGIN IMMEDIATE")
        timestamp = now_iso()
        for row in rows:
            cursor = connection.execute(
                """UPDATE original_factory_items SET deleted_at=?,deleted_by=?,updated_at=?
                   WHERE id=? AND deleted_at IS NULL""",
                (timestamp, actor_value, timestamp, row["id"]),
            )
            if cursor.rowcount != 1:
                raise OriginalConflict("original item changed before deletion completed")
            _audit(
                connection, item_id=str(row["id"]), action="BULK_DELETE",
                from_status=str(row["status"]), to_status="DELETED", actor=actor_value,
                request_id=request_value, metadata={"batch_size": len(ids)}, created_at=timestamp,
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
    cleanup_pending = False
    try:
        if quarantine.exists():
            shutil.rmtree(quarantine)
    except OSError as error:
        cleanup_pending = True
        log = connect_db(config)
        timestamp = now_iso()
        for item_id in ids:
            _audit(
                log, item_id=item_id, action="DELETE_FILE_CLEANUP_FAILED", actor=actor_value,
                metadata={"quarantine": str(quarantine.relative_to(root)), "error": str(error)[:500]},
                created_at=timestamp,
            )
        log.commit()
    return {
        "deleted": len(ids), "item_ids": ids, "idempotent": False,
        "cleanup_pending": cleanup_pending,
    }


def original_publish_source_material(config: dict[str, Any], item_id: str) -> dict[str, Any]:
    detail = original_detail(config, item_id)
    trusted_match: dict[str, Any] = {}
    uncertain_match: dict[str, Any] = {}
    for key, value in detail["match_info"].items():
        if isinstance(value, dict) and bool(value.get("uncertain")):
            uncertain_match[key] = value
        else:
            trusted_match[key] = value
    trusted_social = [source for source in detail["social_sources"] if not source["uncertain"]]
    uncertain_social = [source for source in detail["social_sources"] if source["uncertain"]]
    keyword_fields = {
        "competition", "home_team", "away_team", "teams", "players", "player_names",
        "tactics", "key_points", "controversy", "starting_lineup", "missing_players",
    }
    structured_keywords: list[str] = []
    for source in (trusted_match, *(item.get("metadata") or {} for item in trusted_social)):
        for key, value in source.items():
            if key not in keyword_fields:
                continue
            values = value if isinstance(value, list) else [value]
            for raw in values:
                text = str(raw or "").strip()
                if text and len(text) <= 120 and text not in structured_keywords:
                    structured_keywords.append(text)
    return {
        "source_type": "original_factory",
        "source_title": detail["match_name"],
        "source_description": "",
        "match_name": detail["match_name"],
        "match_date": detail["match_date"],
        "match_time_sao_paulo": detail["match_time_sao_paulo"],
        "channels": detail["channels"],
        "video_type": detail["category_label"],
        "video_type_pt": ORIGINAL_CATEGORY_PT.get(detail["category_id"], "destaques do jogo"),
        "category_tags": [detail["category_label"]],
        "keywords": [detail["match_name"], *detail["channels"], detail["category_label"], *structured_keywords],
        "match_info": trusted_match,
        "social_facts": trusted_social,
        "uncertain_match_info": uncertain_match,
        "uncertain_facts": uncertain_social,
        "factual_policy": (
            "Only match_info and social_facts may be stated as facts. uncertain_match_info and "
            "uncertain_facts are internal review context and must not be stated as confirmed facts."
        ),
    }


def log_original_copy_generation(
    config: dict[str, Any], item_id: str, *, platform: str,
    source_snapshot: dict[str, Any], output: dict[str, Any], model: str,
    fallback_reason: str = "", actor: str = "",
) -> None:
    item_id = validate_original_id(item_id)
    actor_value = validate_audit_value(actor, "actor") or "dashboard"
    connection = connect_db(config)
    _active_row(connection, item_id)
    connection.execute(
        """INSERT INTO original_factory_copy_generations(
             item_id,platform,source_snapshot_json,output_json,model,fallback_reason,actor,created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            item_id, str(platform)[:40], json.dumps(source_snapshot, ensure_ascii=False),
            json.dumps(output, ensure_ascii=False), str(model)[:120], str(fallback_reason)[:200],
            actor_value, now_iso(),
        ),
    )
    connection.commit()


def set_original_publication_state(
    config: dict[str, Any], item_id: str, *, status: str, publication_id: int | None = None,
    actor: str = "publish-worker", error: str = "",
) -> None:
    item_id = validate_original_id(item_id)
    status_value = str(status or "").strip().upper()
    if status_value not in ORIGINAL_PUBLISH_STATUSES:
        raise OriginalFactoryError("publish status is invalid")
    connection = connect_db(config)
    row = _active_row(connection, item_id)
    timestamp = now_iso()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """UPDATE original_factory_items SET publish_status=?,last_publication_id=COALESCE(?,last_publication_id),
               updated_at=? WHERE id=? AND deleted_at IS NULL""",
            (status_value, publication_id, timestamp, item_id),
        )
        _audit(
            connection, item_id=item_id, action="PUBLICATION_STATUS",
            from_status=str(row["publish_status"] or "NOT_PUBLISHED"), to_status=status_value,
            actor=validate_audit_value(actor, "actor") or "publish-worker",
            metadata={"publication_id": publication_id, **({"error": error[:500]} if error else {})},
            created_at=timestamp,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
