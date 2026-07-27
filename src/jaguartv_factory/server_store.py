from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, BinaryIO


ALLOWED_MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def storage_root(config: dict[str, Any]) -> Path:
    settings = config.get("storage", {}) or {}
    configured = str(settings.get("root") or "workspace/server_media")
    root = Path(configured).expanduser()
    if not root.is_absolute():
        root = Path(str(config["_root"])) / root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def public_base_url(config: dict[str, Any]) -> str:
    return str((config.get("storage", {}) or {}).get("public_base_url") or "https://factory.jarg.top/media").rstrip("/")


def public_url(config: dict[str, Any], relative: str | Path) -> str:
    value = str(relative).replace(os.sep, "/").lstrip("/")
    return f"{public_base_url(config)}/{value}"


def archive_review_package(config: dict[str, Any], package_id: str, review_dir: Path) -> dict[str, Any]:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", package_id)
    destination = storage_root(config) / "review" / safe_id
    destination.mkdir(parents=True, exist_ok=True)
    copied: dict[str, dict[str, Any]] = {}
    for name in ("video.mp4", "cover.jpg", "metadata.json", "review.json"):
        source = review_dir / name
        if not source.is_file():
            continue
        target = destination / name
        shutil.copy2(source, target)
        relative = target.relative_to(storage_root(config))
        copied[name] = {
            "path": str(target),
            "url": public_url(config, relative),
            "size": target.stat().st_size,
        }
    result = {
        "provider": "factory_server",
        "package_id": package_id,
        "root": str(destination),
        "public_base_url": public_base_url(config),
        "files": copied,
    }
    metadata_path = destination / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["server_storage"] = result
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            shutil.copy2(metadata_path, review_dir / "metadata.json")
        except (json.JSONDecodeError, OSError):
            pass
    return result


def save_upload(
    config: dict[str, Any],
    stream: BinaryIO,
    *,
    filename: str,
    kind: str,
    content_length: int,
) -> dict[str, Any]:
    if kind not in {"source", "reaction"}:
        raise ValueError("upload kind must be source or reaction")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_MEDIA_EXTENSIONS:
        raise ValueError(f"unsupported media extension: {extension or 'missing'}")
    max_bytes = int((config.get("storage", {}) or {}).get("max_upload_bytes", 2 * 1024 * 1024 * 1024))
    if content_length <= 0:
        raise ValueError("empty upload")
    if content_length > max_bytes:
        raise ValueError(f"upload exceeds configured limit of {max_bytes} bytes")
    identifier = uuid.uuid4().hex
    directory = storage_root(config) / "uploads" / kind
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{identifier}{extension}"
    temporary = directory / f".{identifier}.uploading"
    remaining = content_length
    written = 0
    try:
        with temporary.open("wb") as handle:
            while remaining > 0:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                remaining -= len(chunk)
        if written != content_length:
            raise ValueError(f"incomplete upload: expected {content_length} bytes, received {written}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    relative = destination.relative_to(storage_root(config))
    return {
        "id": identifier,
        "kind": kind,
        "original_filename": Path(filename).name,
        "path": str(destination),
        "relative_path": str(relative),
        "url": "",
        "storage_uri": f"server://{str(relative).replace(os.sep, '/')}",
        "size": destination.stat().st_size,
    }
