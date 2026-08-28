from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from PIL import Image

from .binaries import require_binary
from .server_store import ALLOWED_IMAGE_EXTENSIONS, ALLOWED_MEDIA_EXTENSIONS, public_url, storage_root


CTA_MEDIA_TYPES = {"image", "video"}
CTA_ORIENTATIONS = {"landscape", "portrait"}
CTA_ORIENTATION_NAMES = {"landscape": "横版", "portrait": "竖版"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    name = Path(str(value or "")).name.strip()
    if not name or name in {".", ".."}:
        raise ValueError("CTA filename is required")
    stem = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", Path(name).stem).strip("._") or "cta"
    return f"{stem}{Path(name).suffix.lower()}"


def _probe_video(path: Path) -> tuple[int, int, float]:
    import subprocess

    result = subprocess.run(
        [
            require_binary("ffprobe"), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("CTA video is not readable")
    try:
        payload = json.loads(result.stdout)
        stream = (payload.get("streams") or [])[0]
        width, height = int(stream["width"]), int(stream["height"])
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("CTA video metadata is invalid") from error
    if width <= 0 or height <= 0 or duration <= 0:
        raise ValueError("CTA video must have valid dimensions and duration")
    return width, height, duration


def _probe(path: Path) -> tuple[str, int, int, float, str]:
    suffix = path.suffix.lower()
    if suffix in ALLOWED_IMAGE_EXTENSIONS:
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
        except OSError as error:
            raise ValueError("CTA image is not readable") from error
        media_type, duration = "image", 2.0
    elif suffix in ALLOWED_MEDIA_EXTENSIONS:
        width, height, duration = _probe_video(path)
        media_type = "video"
    else:
        raise ValueError(f"unsupported CTA extension: {suffix or 'missing'}")
    orientation = "landscape" if width >= height else "portrait"
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return media_type, int(width), int(height), float(duration), mime_type


def _managed_destination(
    config: dict[str, Any], connection: Any, orientation: str, suffix: str
) -> tuple[str, Path]:
    directory = storage_root(config) / "cta"
    directory.mkdir(parents=True, exist_ok=True)
    prefix = CTA_ORIENTATION_NAMES[orientation]
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)(?:\.[^.]+)?$")
    used = {
        int(match.group(1))
        for row in connection.execute(
            "SELECT name FROM cta_assets WHERE status='ACTIVE' AND orientation=?", (orientation,)
        )
        if (match := pattern.fullmatch(Path(str(row["name"])).name))
    }
    index = max(used, default=0) + 1
    while True:
        name = f"{prefix}{index}{suffix.lower()}"
        destination = directory / name
        if index not in used and not destination.exists():
            return name, destination
        index += 1


def import_cta_path(
    config: dict[str, Any], source: Path, *, original_name: str | None = None, actor: str = "dashboard"
) -> dict[str, Any]:
    from .core import connect_db

    source = source.expanduser().resolve()
    if not source.is_file():
        raise ValueError("CTA file does not exist")
    media_type, width, height, duration, mime_type = _probe(source)
    digest = _sha256(source)
    connection = connect_db(config)
    temporary: Path | None = None
    destination: Path | None = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM cta_assets WHERE sha256=? AND status='ACTIVE' ORDER BY created_at DESC LIMIT 1",
            (digest,),
        ).fetchone()
        if existing:
            connection.commit()
            return cta_row(config, dict(existing))
        orientation = "landscape" if width >= height else "portrait"
        name, destination = _managed_destination(config, connection, orientation, source.suffix)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.importing")
        shutil.copy2(source, temporary)
        temporary.replace(destination)
        identifier = uuid.uuid4().hex
        timestamp = _now()
        connection.execute(
            """
            INSERT INTO cta_assets(
              id,name,original_name,file_path,media_type,orientation,mime_type,width,height,
              duration_sec,size_bytes,sha256,status,created_by,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'ACTIVE',?,?,?)
            """,
            (
                identifier, name, Path(original_name or source.name).name, str(destination), media_type,
                orientation, mime_type, width, height, duration, destination.stat().st_size, digest,
                str(actor or "dashboard")[:120], timestamp, timestamp,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if destination is not None and destination.exists() and not connection.execute(
            "SELECT 1 FROM cta_assets WHERE file_path=?", (str(destination),)
        ).fetchone():
            destination.unlink(missing_ok=True)
        raise
    row = connection.execute("SELECT * FROM cta_assets WHERE id=?", (identifier,)).fetchone()
    return cta_row(config, dict(row))


def import_cta_stream(
    config: dict[str, Any], stream: BinaryIO, *, filename: str, content_length: int, actor: str = "dashboard"
) -> dict[str, Any]:
    max_bytes = min(
        int((config.get("storage", {}) or {}).get("max_upload_bytes", 2 * 1024 * 1024 * 1024)),
        500 * 1024 * 1024,
    )
    if content_length <= 0 or content_length > max_bytes:
        raise ValueError("CTA upload is empty or exceeds the 500 MB limit")
    suffix = Path(_safe_name(filename)).suffix
    temporary_dir = storage_root(config) / "cta" / ".incoming"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary = temporary_dir / f"{uuid.uuid4().hex}{suffix}"
    written = 0
    try:
        with temporary.open("wb") as handle:
            while written < content_length:
                chunk = stream.read(min(1024 * 1024, content_length - written))
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
        if written != content_length:
            raise ValueError("CTA upload was incomplete")
        return import_cta_path(config, temporary, original_name=filename, actor=actor)
    finally:
        temporary.unlink(missing_ok=True)


def cta_row(config: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(row.get("file_path") or ""))
    result = dict(row)
    result.pop("file_path", None)
    if path.is_file():
        relative = path.resolve().relative_to(storage_root(config))
        result["preview_url"] = f"/media/{str(relative).replace(os.sep, '/')}"
        result["public_url"] = public_url(config, relative)
    else:
        result["preview_url"] = ""
        result["public_url"] = ""
    return result


def list_cta_assets(config: dict[str, Any]) -> list[dict[str, Any]]:
    from .core import connect_db

    rows = connect_db(config).execute(
        """
        SELECT * FROM cta_assets WHERE status='ACTIVE'
        ORDER BY CASE orientation WHEN 'landscape' THEN 0 ELSE 1 END,created_at,id
        """
    ).fetchall()
    return [cta_row(config, dict(row)) for row in rows if Path(str(row["file_path"])).is_file()]


def rename_active_cta_assets(config: dict[str, Any], *, actor: str = "system") -> list[dict[str, Any]]:
    """Rename active CTA files by orientation while preserving upload provenance."""
    from .core import connect_db

    del actor  # Reserved for a future CTA audit-event table.
    connection = connect_db(config)
    directory = (storage_root(config) / "cta").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[dict[str, Any], Path, Path, Path]] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM cta_assets WHERE status='ACTIVE'
                ORDER BY CASE orientation WHEN 'landscape' THEN 0 ELSE 1 END,created_at,id
                """
            )
        ]
        counters = {orientation: 0 for orientation in CTA_ORIENTATIONS}
        sources = {Path(str(row["file_path"])).resolve() for row in rows}
        for row in rows:
            orientation = str(row.get("orientation") or "")
            if orientation not in CTA_ORIENTATIONS:
                raise ValueError(f"CTA {row['id']} has an invalid orientation")
            source = Path(str(row["file_path"])).resolve()
            if source.parent != directory or source.is_symlink() or not source.is_file():
                raise ValueError(f"CTA {row['id']} is outside managed storage or missing")
            counters[orientation] += 1
            name = f"{CTA_ORIENTATION_NAMES[orientation]}{counters[orientation]}{source.suffix.lower()}"
            destination = directory / name
            if destination.exists() and destination.resolve() not in sources:
                raise FileExistsError(f"CTA rename target already exists: {destination.name}")
            temporary = directory / f".{row['id']}.{uuid.uuid4().hex}.renaming{source.suffix.lower()}"
            row["managed_name"] = name
            staged.append((row, source, temporary, destination))

        for _, source, temporary, destination in staged:
            if source != destination:
                source.replace(temporary)
        for _, source, temporary, destination in staged:
            if source != destination:
                temporary.replace(destination)

        timestamp = _now()
        for row, _, _, destination in staged:
            connection.execute(
                "UPDATE cta_assets SET name=?,file_path=?,updated_at=? WHERE id=?",
                (row["managed_name"], str(destination), timestamp, row["id"]),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        for _, source, temporary, destination in reversed(staged):
            current = destination if destination.exists() else temporary
            if source != destination and current.exists() and not source.exists():
                current.replace(source)
        raise

    return [
        cta_row(config, dict(row))
        for row in connection.execute(
            """
            SELECT * FROM cta_assets WHERE status='ACTIVE'
            ORDER BY CASE orientation WHEN 'landscape' THEN 0 ELSE 1 END,created_at,id
            """
        )
    ]


def get_cta_asset(config: dict[str, Any], asset_id: str, *, include_path: bool = False) -> dict[str, Any]:
    from .core import connect_db

    row = connect_db(config).execute(
        "SELECT * FROM cta_assets WHERE id=? AND status='ACTIVE'", (str(asset_id or ""),)
    ).fetchone()
    if not row:
        raise ValueError("CTA asset does not exist")
    result = dict(row)
    if not Path(str(result["file_path"])).is_file():
        raise ValueError("CTA asset file is missing")
    return result if include_path else cta_row(config, result)


def delete_cta_asset(config: dict[str, Any], asset_id: str, *, actor: str = "dashboard") -> dict[str, Any]:
    from .core import connect_db

    asset = get_cta_asset(config, asset_id, include_path=True)
    source = Path(asset["file_path"])
    trash = storage_root(config) / "cta" / ".deleted"
    trash.mkdir(parents=True, exist_ok=True)
    destination = trash / f"{asset_id}-{source.name}"
    source.replace(destination)
    timestamp = _now()
    connection = connect_db(config)
    connection.execute(
        "UPDATE cta_assets SET status='DELETED',file_path=?,updated_at=?,deleted_at=?,created_by=created_by WHERE id=?",
        (str(destination), timestamp, timestamp, asset_id),
    )
    connection.commit()
    return {"id": asset_id, "status": "DELETED", "deleted_by": str(actor or "dashboard")[:120]}


def select_random_cta(config: dict[str, Any], orientation: str) -> dict[str, Any]:
    from .core import connect_db

    orientation = str(orientation or "").strip().lower()
    if orientation not in CTA_ORIENTATIONS:
        raise ValueError("CTA orientation must be landscape or portrait")
    rows = connect_db(config).execute(
        "SELECT * FROM cta_assets WHERE status='ACTIVE' AND orientation=? ORDER BY id",
        (orientation,),
    ).fetchall()
    available = [dict(row) for row in rows if Path(str(row["file_path"])).is_file()]
    if not available:
        raise RuntimeError(f"no active {orientation} CTA asset is available")
    return secrets.SystemRandom().choice(available)
