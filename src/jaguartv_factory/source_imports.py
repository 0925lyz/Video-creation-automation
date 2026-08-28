from __future__ import annotations

import hashlib
import fcntl
import ipaddress
import json
import mimetypes
import os
import shutil
import socket
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from .core import (
    append_event,
    connect_db,
    now_iso,
    require_binary,
    run_command,
    source_duration_limit,
    workspace_dir,
)
from .publisher import auto_enqueue_approved_publication
from .server_store import storage_root


SOURCE_TYPE = "source_import"
UPLOAD_SOURCE_PLATFORMS = {
    "original",
    "youtube",
    "bilibili",
    "douyin",
    "xiaohongshu",
    "tiktok",
    "facebook",
    "x",
    "instagram",
    "kwai",
}
TARGET_PENDING_PRODUCTION = "pending_production"
TARGET_APPROVED = "approved"
ALLOWED_TARGET_AREAS = {TARGET_PENDING_PRODUCTION, TARGET_APPROVED}
TARGET_ALIASES = {
    "": TARGET_PENDING_PRODUCTION,
    "pending_production": TARGET_PENDING_PRODUCTION,
    "downloaded": TARGET_PENDING_PRODUCTION,
    "待制作": TARGET_PENDING_PRODUCTION,
    "approved": TARGET_APPROVED,
    "审核通过": TARGET_APPROVED,
}
TARGET_LABELS = {
    TARGET_PENDING_PRODUCTION: "待制作",
    TARGET_APPROVED: "审核通过",
}
SUPPORTED_PLATFORM_HOSTS = {
    "youtube": ("youtube.com", "youtu.be"),
    "bilibili": ("bilibili.com", "b23.tv"),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "tiktok": ("tiktok.com",),
    "facebook": ("facebook.com", "fb.watch"),
}
ALLOWED_MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
ALLOWED_MEDIA_MIME_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/x-matroska",
    "video/webm",
    "video/x-m4v",
}
APPROVAL_REASON = "用户确认该外部视频已是可直接使用的完整成片"


class SourceImportError(RuntimeError):
    pass


class SourceImportPermissionError(PermissionError):
    pass


class DuplicateSourceImportError(SourceImportError):
    pass


def normalize_target_area(value: Any) -> str:
    target = TARGET_ALIASES.get(str(value or "").strip().lower())
    if target not in ALLOWED_TARGET_AREAS:
        raise ValueError("target_area must be pending_production or approved")
    return target


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def platform_for_host(host: str) -> str:
    for platform, suffixes in SUPPORTED_PLATFORM_HOSTS.items():
        if any(_host_matches(host, suffix) for suffix in suffixes):
            return platform
    return ""


def _public_resolved_addresses(
    host: str,
    port: int,
    resolver: Callable[..., list[tuple[Any, ...]]],
) -> tuple[str, ...]:
    try:
        records = resolver(host, port, 0, socket.SOCK_STREAM)
    except OSError as error:
        raise ValueError("source host could not be resolved") from error
    addresses: set[str] = set()
    for record in records:
        address = str(record[4][0]).split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as error:
            raise ValueError("source host resolved to an invalid address") from error
        if not parsed.is_global:
            raise ValueError("source host must resolve only to the public internet")
        addresses.add(parsed.compressed)
    if not addresses:
        raise ValueError("source host did not resolve to a public address")
    return tuple(sorted(addresses))


def normalize_import_url(
    value: str,
    requested_platform: str,
    *,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> str:
    raw = str(value or "").strip()
    decoded_raw = unquote(raw)
    if len(raw) > 4096:
        raise ValueError("source URL is too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded_raw):
        raise ValueError("source URL must not contain control characters")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("source URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("source URL must not contain credentials")
    host = str(parsed.hostname or "").rstrip(".").lower()
    platform = platform_for_host(host)
    if not platform:
        raise ValueError("source URL is not a supported platform URL")
    requested = str(requested_platform or "").strip().lower()
    if requested not in SUPPORTED_PLATFORM_HOSTS:
        raise ValueError("unsupported source platform")
    if requested != platform:
        raise ValueError("source URL platform does not match the selected platform")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("source URL port is not allowed")
    decoded_segments = [unquote(segment) for segment in parsed.path.split("/")]
    if any(segment in {".", ".."} for segment in decoded_segments):
        raise ValueError("invalid URL path")
    sensitive_query_names = {
        "access_token", "auth", "authorization", "cookie", "oauth_token", "session", "token"
    }
    query_names = {name.strip().lower() for name, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if query_names.intersection(sensitive_query_names):
        raise ValueError("source URL must not contain sensitive credentials")
    _public_resolved_addresses(host, parsed.port or (443 if parsed.scheme.lower() == "https" else 80), resolver)
    netloc = host
    if parsed.port and parsed.port != (443 if parsed.scheme.lower() == "https" else 80):
        netloc = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def _event(
    connection: sqlite3.Connection,
    import_id: str,
    event_type: str,
    *,
    candidate_id: str = "",
    from_status: str = "",
    to_status: str = "",
    actor: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO source_import_events(
          import_id,candidate_id,event_type,from_status,to_status,actor,payload_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            import_id,
            candidate_id,
            event_type,
            from_status,
            to_status,
            actor,
            json.dumps(payload or {}, ensure_ascii=False),
            now_iso(),
        ),
    )


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        raise ValueError("source import does not exist")
    result = dict(row)
    try:
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
    except json.JSONDecodeError:
        result["metadata"] = {}
    result["has_video"] = bool(result.get("has_video"))
    result["has_audio"] = bool(result.get("has_audio"))
    result["target_label"] = TARGET_LABELS.get(result.get("target_area"), result.get("target_area"))
    return result


def source_import_row(config: dict[str, Any], import_id: str) -> dict[str, Any]:
    row = connect_db(config).execute(
        "SELECT * FROM source_imports WHERE id=?", (str(import_id or "").strip(),)
    ).fetchone()
    return _row_dict(row)


def sync_source_import_workflow_status(
    connection: sqlite3.Connection,
    candidate_id: str,
    status: str,
    *,
    event_type: str,
    actor: str = "system",
    payload: dict[str, Any] | None = None,
) -> bool:
    row = connection.execute(
        "SELECT * FROM source_imports WHERE candidate_id=?", (candidate_id,)
    ).fetchone()
    if not row:
        return False
    previous = str(row["actual_workflow_status"] or "")
    connection.execute(
        "UPDATE source_imports SET actual_workflow_status=?,updated_at=? WHERE id=?",
        (status, now_iso(), row["id"]),
    )
    _event(
        connection,
        str(row["id"]),
        event_type,
        candidate_id=candidate_id,
        from_status=previous,
        to_status=status,
        actor=actor,
        payload=payload,
    )
    return True


def create_source_import(
    config: dict[str, Any],
    *,
    platform: str,
    url: str,
    target_area: Any = TARGET_PENDING_PRODUCTION,
    operator_id: str = "",
    can_direct_approve: bool = False,
    idempotency_key: str = "",
    import_method: str = "url",
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> dict[str, Any]:
    target = normalize_target_area(target_area)
    if target == TARGET_APPROVED and not can_direct_approve:
        raise SourceImportPermissionError("direct approval requires dashboard administrator permission")
    method = str(import_method or "url").strip().lower()
    if method != "url":
        raise ValueError("URL source imports must use import_method=url")
    normalized_url = normalize_import_url(url, platform, resolver=resolver)
    actor = str(operator_id or "dashboard").strip()[:200] or "dashboard"
    supplied_key = str(idempotency_key or "").strip()[:200]
    key = supplied_key or hashlib.sha256(
        f"{SOURCE_TYPE}\x1f{normalized_url}\x1f{target}".encode("utf-8")
    ).hexdigest()
    connection = connect_db(config)
    timestamp = now_iso()
    import_id = uuid.uuid4().hex
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM source_imports WHERE idempotency_key=? OR normalized_url=? ORDER BY created_at LIMIT 1",
            (key, normalized_url),
        ).fetchone()
        if existing:
            if str(existing["target_area"]) != target:
                connection.rollback()
                raise DuplicateSourceImportError("this URL already has an import with a different target area")
            connection.commit()
            return {**_row_dict(existing), "reused": True}
        connection.execute(
            """
            INSERT INTO source_imports(
              id,source_type,import_method,source_platform,normalized_url,download_task_id,
              idempotency_key,target_area,actual_workflow_status,download_status,operator_id,
              metadata_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                import_id,
                SOURCE_TYPE,
                method,
                str(platform).strip().lower(),
                normalized_url,
                import_id,
                key,
                target,
                "IMPORT_PENDING",
                "PENDING",
                actor,
                json.dumps({"requested_target": target, "source_type": SOURCE_TYPE}, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        _event(
            connection,
            import_id,
            "IMPORT_REQUESTED",
            to_status="IMPORT_PENDING",
            actor=actor,
            payload={"target_area": target, "platform": platform},
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        existing = connection.execute(
            "SELECT * FROM source_imports WHERE idempotency_key=? OR normalized_url=? ORDER BY created_at LIMIT 1",
            (key, normalized_url),
        ).fetchone()
        if existing and str(existing["target_area"]) == target:
            return {**_row_dict(existing), "reused": True}
        raise DuplicateSourceImportError("this URL already has an import task")
    return {**source_import_row(config, import_id), "reused": False}


def create_uploaded_source_import(
    config: dict[str, Any],
    *,
    upload_id: str,
    source_platform: str = "original",
    target_area: Any = TARGET_PENDING_PRODUCTION,
    operator_id: str = "",
    can_direct_approve: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    target = normalize_target_area(target_area)
    if target == TARGET_APPROVED and not can_direct_approve:
        raise SourceImportPermissionError("direct approval requires dashboard administrator permission")
    upload_id = str(upload_id or "").strip()
    if not upload_id:
        raise ValueError("upload_id is required")
    platform = str(source_platform or "original").strip().lower()
    if platform not in UPLOAD_SOURCE_PLATFORMS:
        raise ValueError("source_platform is not allowed for uploaded media")
    actor = str(operator_id or "dashboard").strip()[:200] or "dashboard"
    key = str(idempotency_key or "").strip()[:200] or hashlib.sha256(
        f"{SOURCE_TYPE}\x1fupload\x1f{upload_id}\x1f{target}".encode("utf-8")
    ).hexdigest()
    connection = connect_db(config)
    timestamp = now_iso()
    import_id = uuid.uuid4().hex
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM source_imports WHERE idempotency_key=? ORDER BY created_at LIMIT 1", (key,)
        ).fetchone()
        if existing:
            connection.commit()
            return {**_row_dict(existing), "reused": True}
        connection.execute(
            """
            INSERT INTO source_imports(
              id,source_type,import_method,source_platform,download_task_id,idempotency_key,
              target_area,actual_workflow_status,download_status,operator_id,metadata_json,
              created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                import_id,
                SOURCE_TYPE,
                "upload",
                platform,
                import_id,
                key,
                target,
                "IMPORT_PENDING",
                "UPLOADED",
                actor,
                json.dumps(
                    {
                        "upload_id": upload_id,
                        "source_type": SOURCE_TYPE,
                        "source_platform": platform,
                    },
                    ensure_ascii=False,
                ),
                timestamp,
                timestamp,
            ),
        )
        _event(connection, import_id, "IMPORT_REQUESTED", to_status="IMPORT_PENDING", actor=actor)
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        existing = connection.execute(
            "SELECT * FROM source_imports WHERE idempotency_key=?", (key,)
        ).fetchone()
        if existing:
            return {**_row_dict(existing), "reused": True}
        raise
    return {**source_import_row(config, import_id), "reused": False}


def attach_source_import_candidate(
    config: dict[str, Any], import_id: str, candidate_id: str, *, original_title: str = ""
) -> dict[str, Any]:
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM source_imports WHERE id=?", (import_id,)).fetchone()
    if not row:
        raise ValueError("source import does not exist")
    timestamp = now_iso()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE source_imports
            SET candidate_id=?,original_title=COALESCE(NULLIF(?,''),original_title),
                download_status='DOWNLOADING',actual_workflow_status='IMPORT_DOWNLOADING',updated_at=?
            WHERE id=?
            """,
            (candidate_id, original_title, timestamp, import_id),
        )
        _event(
            connection,
            import_id,
            "DOWNLOAD_STARTED",
            candidate_id=candidate_id,
            from_status=str(row["actual_workflow_status"]),
            to_status="IMPORT_DOWNLOADING",
            actor=str(row["operator_id"]),
        )
        connection.commit()
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise DuplicateSourceImportError("candidate is already linked to another import") from error
    return source_import_row(config, import_id)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fps(value: Any) -> float:
    text = str(value or "0")
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        return float(numerator) / max(float(denominator), 1.0)
    return float(text)


def validate_imported_media(config: dict[str, Any], candidate_id: str, path: Path) -> dict[str, Any]:
    media = Path(path).expanduser().resolve()
    jobs_root = (workspace_dir(config) / "jobs").resolve()
    candidate_root = (jobs_root / str(candidate_id or "")).resolve()
    if jobs_root not in candidate_root.parents or candidate_root not in media.parents:
        raise ValueError("downloaded media path is outside the candidate media directory")
    if media.is_symlink() or not media.is_file():
        raise ValueError("downloaded media path is not a regular managed file")
    if media.suffix.lower() not in ALLOWED_MEDIA_EXTENSIONS:
        raise ValueError("downloaded media file type is not allowed")
    max_bytes = int((config.get("storage", {}) or {}).get("max_upload_bytes", 2 * 1024 * 1024 * 1024))
    size_bytes = media.stat().st_size
    if size_bytes <= 0 or size_bytes > max_bytes:
        raise ValueError("downloaded media size is outside the configured limit")
    mime_type = mimetypes.guess_type(media.name)[0] or ""
    if mime_type not in ALLOWED_MEDIA_MIME_TYPES:
        raise ValueError("downloaded media MIME type is not allowed")
    probe = run_command(
        [
            require_binary("ffprobe"),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(media),
        ],
        check=False,
    )
    if probe.returncode != 0:
        raise ValueError("downloaded media is not decodable by ffprobe")
    try:
        payload = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("downloaded media probe returned invalid data") from error
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError("video stream is missing")
    if not audio:
        raise ValueError("audio stream is missing")
    duration = float((payload.get("format") or {}).get("duration") or video.get("duration") or 0)
    if duration <= 0 or duration > source_duration_limit(config):
        raise ValueError("video duration is outside the configured source limit")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    if width <= 0 or height <= 0:
        raise ValueError("video resolution is invalid")
    if fps <= 0 or fps > 240:
        raise ValueError("video frame rate is invalid")
    decode = run_command(
        [
            require_binary("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(media),
            "-t",
            str(min(2.0, duration)),
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    if decode.returncode != 0:
        raise ValueError("downloaded media failed decode validation")
    relative = media.relative_to(workspace_dir(config).resolve())
    return {
        "path": str(media),
        "relative_path": str(relative).replace(os.sep, "/"),
        "sha256": _sha256(media),
        "mime_type": mime_type,
        "format_name": str((payload.get("format") or {}).get("format_name") or ""),
        "size_bytes": size_bytes,
        "duration_sec": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_video": True,
        "has_audio": True,
        "decode_valid": True,
    }


def create_import_cover(config: dict[str, Any], media_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = run_command(
        [
            require_binary("ffmpeg"),
            "-y",
            "-ss",
            "0.5",
            "-i",
            str(media_path),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(720,iw)':-2",
            str(destination),
        ],
        check=False,
    )
    if result.returncode != 0 or not destination.is_file():
        raise ValueError("unable to create imported video thumbnail")


def _write_external_review_package(
    config: dict[str, Any],
    import_row: sqlite3.Row,
    candidate_id: str,
    media_path: Path,
    validation: dict[str, Any],
    title: str,
) -> Path:
    root = storage_root(config)
    review_root = (root / "review").resolve()
    package = (review_root / candidate_id).resolve()
    staging = (review_root / f".{candidate_id}.importing-{uuid.uuid4().hex}").resolve()
    if root not in package.parents or review_root not in staging.parents:
        raise ValueError("review package path escaped managed storage")
    review_root.mkdir(parents=True, exist_ok=True)
    if package.exists():
        raise ValueError("a review package already exists for this candidate")
    staging.mkdir(parents=False)
    try:
        video = staging / "video.mp4"
        temporary = staging / ".video.importing.mp4"
        if media_path.suffix.lower() == ".mp4":
            shutil.copy2(media_path, temporary)
        else:
            converted = run_command(
                [
                    require_binary("ffmpeg"),
                    "-y",
                    "-i",
                    str(media_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "18",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                    str(temporary),
                ],
                check=False,
            )
            if converted.returncode != 0 or not temporary.is_file():
                raise ValueError("unable to normalize the imported finished asset to MP4")
        temporary.replace(video)
        create_import_cover(config, video, staging / "cover.jpg")
        timestamp = now_iso()
        metadata = {
            "job_id": candidate_id,
            "source_job_id": candidate_id,
            "source_import_id": str(import_row["id"]),
            "title": title,
            "batch_label": "导入成片",
            "content_type": "external_import",
            "variant": "导入成片",
            "production_origin": "external_import",
            "source_type": SOURCE_TYPE,
            "target_area": TARGET_APPROVED,
            "smart_slice_completed": False,
            "standard_render_completed": False,
            "automatic_review": False,
            "source": {
                "platform": str(import_row["source_platform"]),
                "url": str(import_row["normalized_url"]),
                "title": title,
            },
            "media_validation": {
                key: value for key, value in validation.items() if key != "path"
            },
            "created_at": timestamp,
        }
        review = {
            "decision": "approved",
            "reviewer": str(import_row["operator_id"]),
            "review_source": "manual_import",
            "reason": APPROVAL_REASON,
            "reviewed_at": timestamp,
            "automatic": False,
        }
        (staging / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (staging / "review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        staging.replace(package)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return package


def _remove_owned_review_package(package: Path | None, import_id: str) -> None:
    if package is None or not package.is_dir():
        return
    metadata_path = package / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if str(metadata.get("source_import_id") or "") == import_id:
        shutil.rmtree(package)


def fail_source_import(
    config: dict[str, Any],
    import_id: str,
    *,
    category: str,
    summary: str,
    candidate_id: str = "",
) -> dict[str, Any]:
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM source_imports WHERE id=?", (import_id,)).fetchone()
    if not row:
        raise ValueError("source import does not exist")
    linked_candidate = str(candidate_id or row["candidate_id"] or "")
    timestamp = now_iso()
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        """
        UPDATE source_imports SET candidate_id=COALESCE(NULLIF(?,''),candidate_id),
          actual_workflow_status='IMPORT_FAILED',download_status='FAILED',error_category=?,
          error_summary=?,updated_at=? WHERE id=?
        """,
        (linked_candidate, str(category)[:100], str(summary)[-2000:], timestamp, import_id),
    )
    if linked_candidate and connection.execute(
        "SELECT 1 FROM candidates WHERE id=?", (linked_candidate,)
    ).fetchone():
        connection.execute(
            "UPDATE candidates SET status='IMPORT_FAILED',updated_at=? WHERE id=?",
            (timestamp, linked_candidate),
        )
        append_event(
            connection,
            linked_candidate,
            "IMPORT_FAILED",
            {"category": str(category)[:100], "error": str(summary)[-2000:], "import_id": import_id},
            commit=False,
        )
    _event(
        connection,
        import_id,
        "IMPORT_FAILED",
        candidate_id=linked_candidate,
        from_status=str(row["actual_workflow_status"]),
        to_status="IMPORT_FAILED",
        actor=str(row["operator_id"]),
        payload={"category": str(category)[:100], "summary": str(summary)[-2000:]},
    )
    connection.commit()
    return source_import_row(config, import_id)


def complete_source_import(
    config: dict[str, Any],
    import_id: str,
    *,
    candidate_id: str,
    media_path: Path,
    original_title: str,
) -> dict[str, Any]:
    lock_root = workspace_dir(config) / "source_import_locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(str(import_id or "").encode("utf-8")).hexdigest()
    with (lock_root / f"{lock_name}.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _complete_source_import_locked(
                config,
                import_id,
                candidate_id=candidate_id,
                media_path=media_path,
                original_title=original_title,
            )
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _complete_source_import_locked(
    config: dict[str, Any],
    import_id: str,
    *,
    candidate_id: str,
    media_path: Path,
    original_title: str,
) -> dict[str, Any]:
    connection = connect_db(config)
    import_row = connection.execute("SELECT * FROM source_imports WHERE id=?", (import_id,)).fetchone()
    if not import_row:
        raise ValueError("source import does not exist")
    if str(import_row["actual_workflow_status"]) in {"DOWNLOADED", "APPROVED"}:
        completed = source_import_row(config, import_id)
        if str(import_row["actual_workflow_status"]) == "APPROVED":
            completed["publication"] = auto_enqueue_approved_publication(
                config,
                str(import_row["candidate_id"] or candidate_id),
                reviewer=str(import_row["operator_id"] or ""),
                review_decision_at=str(import_row["reviewed_at"] or ""),
            )
        return completed
    if not connection.execute("SELECT 1 FROM candidates WHERE id=?", (candidate_id,)).fetchone():
        raise ValueError("candidate does not exist")
    try:
        validation = validate_imported_media(config, candidate_id, Path(media_path))
    except Exception as error:
        fail_source_import(
            config,
            import_id,
            category="MEDIA_VALIDATION_FAILED",
            summary=str(error),
            candidate_id=candidate_id,
        )
        raise

    duplicate = connection.execute(
        "SELECT id,candidate_id FROM source_imports WHERE file_sha256=? AND id!=?",
        (validation["sha256"], import_id),
    ).fetchone()
    if duplicate:
        error = DuplicateSourceImportError(f"the same file already exists in import {duplicate['id']}")
        fail_source_import(
            config,
            import_id,
            category="DUPLICATE_FILE",
            summary=str(error),
            candidate_id=candidate_id,
        )
        raise error

    target = normalize_target_area(import_row["target_area"])
    review_package: Path | None = None
    if target == TARGET_APPROVED:
        try:
            review_package = _write_external_review_package(
                config,
                import_row,
                candidate_id,
                Path(media_path).resolve(),
                validation,
                str(original_title or "Imported video"),
            )
        except Exception as error:
            fail_source_import(
                config,
                import_id,
                category="REVIEW_PACKAGE_FAILED",
                summary=str(error),
                candidate_id=candidate_id,
            )
            raise

    actual_status = "APPROVED" if target == TARGET_APPROVED else "DOWNLOADED"
    timestamp = now_iso()
    connection = connect_db(config)
    candidate = connection.execute(
        "SELECT metadata_json FROM candidates WHERE id=?", (candidate_id,)
    ).fetchone()
    try:
        candidate_metadata = json.loads(candidate["metadata_json"] or "{}") if candidate else {}
    except json.JSONDecodeError:
        candidate_metadata = {}
    candidate_metadata.update(
        {
            "source_type": SOURCE_TYPE,
            "source_import_id": import_id,
            "import_method": str(import_row["import_method"]),
            "target_area": target,
            "review_source": "manual_import" if target == TARGET_APPROVED else "",
            "external_finished_asset": target == TARGET_APPROVED,
        }
    )
    event_type = "MANUAL_IMPORT_APPROVED" if target == TARGET_APPROVED else "IMPORT_READY_FOR_PRODUCTION"
    event_payload = {
        "import_id": import_id,
        "source_type": SOURCE_TYPE,
        "target_area": target,
        "review_source": "manual_import" if target == TARGET_APPROVED else "",
        "file_sha256": validation["sha256"],
    }
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE source_imports SET candidate_id=?,original_title=?,download_completed_at=?,
              file_relative_path=?,file_sha256=?,file_mime_type=?,file_size_bytes=?,duration_sec=?,
              width=?,height=?,fps=?,has_video=?,has_audio=?,actual_workflow_status=?,
              download_status='COMPLETED',review_source=?,reviewed_at=?,approval_reason=?,
              error_category='',error_summary='',updated_at=? WHERE id=?
            """,
            (
                candidate_id,
                str(original_title or "")[:1000],
                timestamp,
                validation["relative_path"],
                validation["sha256"],
                validation["mime_type"],
                int(validation["size_bytes"]),
                float(validation["duration_sec"]),
                int(validation["width"]),
                int(validation["height"]),
                float(validation["fps"]),
                int(bool(validation["has_video"])),
                int(bool(validation["has_audio"])),
                actual_status,
                "manual_import" if target == TARGET_APPROVED else "",
                timestamp if target == TARGET_APPROVED else None,
                APPROVAL_REASON if target == TARGET_APPROVED else "",
                timestamp,
                import_id,
            ),
        )
        connection.execute(
            "UPDATE candidates SET title=?,duration=?,status=?,metadata_json=?,updated_at=? WHERE id=?",
            (
                str(original_title or "Imported video")[:1000],
                float(validation["duration_sec"]),
                actual_status,
                json.dumps(candidate_metadata, ensure_ascii=False),
                timestamp,
                candidate_id,
            ),
        )
        append_event(connection, candidate_id, event_type, event_payload, commit=False)
        _event(
            connection,
            import_id,
            event_type,
            candidate_id=candidate_id,
            from_status=str(import_row["actual_workflow_status"]),
            to_status=actual_status,
            actor=str(import_row["operator_id"]),
            payload=event_payload,
        )
        connection.commit()
    except sqlite3.IntegrityError as error:
        connection.rollback()
        _remove_owned_review_package(review_package, import_id)
        duplicate_error = DuplicateSourceImportError("the same file already exists in another import")
        fail_source_import(
            config,
            import_id,
            category="DUPLICATE_FILE",
            summary=str(duplicate_error),
            candidate_id=candidate_id,
        )
        raise duplicate_error from error
    except Exception as error:
        connection.rollback()
        _remove_owned_review_package(review_package, import_id)
        fail_source_import(
            config,
            import_id,
            category="IMPORT_COMMIT_FAILED",
            summary=str(error),
            candidate_id=candidate_id,
        )
        raise
    completed = source_import_row(config, import_id)
    if target == TARGET_APPROVED:
        completed["publication"] = auto_enqueue_approved_publication(
            config,
            candidate_id,
            reviewer=str(import_row["operator_id"] or ""),
            review_decision_at=str(completed.get("reviewed_at") or timestamp),
        )
    return completed
