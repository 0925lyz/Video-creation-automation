from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO


ALLOWED_MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
DEFAULT_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
UPLOAD_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


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


def remote_review_settings(config: dict[str, Any]) -> dict[str, str]:
    storage = config.get("storage", {}) or {}
    return {
        "host": str(os.environ.get("JAGUARTV_REMOTE_REVIEW_HOST") or storage.get("remote_review_host") or "").strip(),
        "user": str(os.environ.get("JAGUARTV_REMOTE_REVIEW_USER") or storage.get("remote_review_user") or "").strip(),
        "key_file": str(os.environ.get("JAGUARTV_REMOTE_REVIEW_KEY") or storage.get("remote_review_key") or "").strip(),
        "root": str(os.environ.get("JAGUARTV_REMOTE_REVIEW_ROOT") or storage.get("remote_review_root") or "").strip(),
    }


def sync_review_package_to_remote(config: dict[str, Any], package_id: str, destination: Path) -> dict[str, Any]:
    settings = remote_review_settings(config)
    if not (settings["host"] and settings["user"] and settings["key_file"] and settings["root"]):
        return {"enabled": False, "reason": "remote_review_* is not configured"}
    key = Path(settings["key_file"]).expanduser()
    if not key.exists():
        return {"enabled": False, "reason": f"ssh key does not exist: {key}"}
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", package_id)
    remote_base = settings["root"].rstrip("/")
    remote_dir = f"{remote_base}/{safe_id}"
    ssh_target = f"{settings['user']}@{settings['host']}"
    ssh_base = ["ssh", "-i", str(key), "-o", "StrictHostKeyChecking=accept-new", ssh_target]
    mkdir = subprocess.run([*ssh_base, "mkdir", "-p", remote_dir], text=True, capture_output=True, check=False)
    if mkdir.returncode != 0:
        return {"enabled": True, "ok": False, "error": (mkdir.stderr or mkdir.stdout)[-1000:]}
    files = [
        str(path) for path in [
            destination / "video.mp4",
            *sorted(path for path in destination.glob("*.mp4") if path.name != "video.mp4"),
            destination / "cover.jpg",
            destination / "metadata.json",
            destination / "review.json",
        ]
        if path.is_file()
    ]
    scp = subprocess.run(
        [
            "scp", "-i", str(key), "-o", "StrictHostKeyChecking=accept-new",
            *files,
            f"{ssh_target}:{remote_dir}/",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if scp.returncode != 0:
        return {"enabled": True, "ok": False, "error": (scp.stderr or scp.stdout)[-1000:]}
    return {"enabled": True, "ok": True, "remote_dir": remote_dir}


def archive_review_package(config: dict[str, Any], package_id: str, review_dir: Path) -> dict[str, Any]:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", package_id)
    destination = storage_root(config) / "review" / safe_id
    destination.mkdir(parents=True, exist_ok=True)
    copied: dict[str, dict[str, Any]] = {}
    names = ["video.mp4", *sorted(path.name for path in review_dir.glob("*.mp4") if path.name != "video.mp4")]
    names.extend(["cover.jpg", "metadata.json", "review.json"])
    for name in names:
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
    remote_result = sync_review_package_to_remote(config, package_id, destination)
    result["remote_sync"] = remote_result
    metadata_path = destination / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["server_storage"] = result
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            shutil.copy2(metadata_path, review_dir / "metadata.json")
            if remote_result.get("ok"):
                settings = remote_review_settings(config)
                key = Path(settings["key_file"]).expanduser()
                ssh_target = f"{settings['user']}@{settings['host']}"
                subprocess.run(
                    [
                        "scp", "-i", str(key), "-o", "StrictHostKeyChecking=accept-new",
                        str(metadata_path), f"{ssh_target}:{str(remote_result['remote_dir']).rstrip('/')}/metadata.json",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
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
    return write_upload_manifest(config, identifier, destination, kind=kind, original_filename=filename)


def validate_upload(config: dict[str, Any], *, filename: str, kind: str, content_length: int) -> str:
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
    return extension


def upload_manifest_path(media_path: Path) -> Path:
    return media_path.with_suffix(media_path.suffix + ".json")


def write_upload_manifest(
    config: dict[str, Any],
    identifier: str,
    destination: Path,
    *,
    kind: str,
    original_filename: str,
) -> dict[str, Any]:
    relative = destination.relative_to(storage_root(config))
    result = {
        "id": identifier,
        "kind": kind,
        "original_filename": Path(original_filename).name,
        "path": str(destination),
        "relative_path": str(relative),
        "url": "",
        "storage_uri": f"server://{str(relative).replace(os.sep, '/')}",
        "size": destination.stat().st_size,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    upload_manifest_path(destination).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def pending_upload_dir(config: dict[str, Any], upload_id: str) -> Path:
    if not UPLOAD_ID_PATTERN.fullmatch(upload_id):
        raise ValueError("invalid upload id")
    return storage_root(config) / "uploads" / ".pending" / upload_id


def init_chunked_upload(
    config: dict[str, Any], *, filename: str, kind: str, content_length: int
) -> dict[str, Any]:
    extension = validate_upload(config, filename=filename, kind=kind, content_length=content_length)
    upload_id = uuid.uuid4().hex
    base = pending_upload_dir(config, upload_id)
    base.mkdir(parents=True, exist_ok=False)
    chunk_bytes = int((config.get("storage", {}) or {}).get("upload_chunk_bytes", DEFAULT_UPLOAD_CHUNK_BYTES))
    chunk_bytes = max(1024 * 1024, min(chunk_bytes, 16 * 1024 * 1024))
    chunk_count = (content_length + chunk_bytes - 1) // chunk_bytes
    manifest = {
        "id": upload_id,
        "filename": Path(filename).name,
        "kind": kind,
        "extension": extension,
        "size": content_length,
        "chunk_bytes": chunk_bytes,
        "chunk_count": chunk_count,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def load_pending_manifest(config: dict[str, Any], upload_id: str) -> tuple[Path, dict[str, Any]]:
    base = pending_upload_dir(config, upload_id)
    path = base / "manifest.json"
    if not path.is_file():
        raise ValueError("upload session does not exist or has expired")
    return base, json.loads(path.read_text(encoding="utf-8"))


def save_upload_chunk(
    config: dict[str, Any], upload_id: str, index: int, stream: BinaryIO, content_length: int
) -> dict[str, Any]:
    base, manifest = load_pending_manifest(config, upload_id)
    chunk_count = int(manifest["chunk_count"])
    chunk_bytes = int(manifest["chunk_bytes"])
    if index < 0 or index >= chunk_count:
        raise ValueError("chunk index is out of range")
    expected = chunk_bytes if index < chunk_count - 1 else int(manifest["size"]) - index * chunk_bytes
    if content_length != expected:
        raise ValueError(f"chunk {index} expected {expected} bytes, received {content_length}")
    parts = base / "parts"
    parts.mkdir(exist_ok=True)
    destination = parts / f"{index:08d}.part"
    if destination.is_file() and destination.stat().st_size == expected:
        return {"id": upload_id, "index": index, "size": expected, "duplicate": True}
    temporary = parts / f".{index:08d}.uploading"
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
            raise ValueError(f"incomplete chunk: expected {content_length} bytes, received {written}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"id": upload_id, "index": index, "size": written, "duplicate": False}


def complete_chunked_upload(config: dict[str, Any], upload_id: str) -> dict[str, Any]:
    base, manifest = load_pending_manifest(config, upload_id)
    parts = base / "parts"
    expected_parts = [parts / f"{index:08d}.part" for index in range(int(manifest["chunk_count"]))]
    missing = [path.name for path in expected_parts if not path.is_file()]
    if missing:
        raise ValueError(f"upload is incomplete; missing {len(missing)} chunks")
    directory = storage_root(config) / "uploads" / str(manifest["kind"])
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{upload_id}{manifest['extension']}"
    temporary = directory / f".{upload_id}.assembling"
    written = 0
    try:
        with temporary.open("wb") as output:
            for part in expected_parts:
                with part.open("rb") as handle:
                    shutil.copyfileobj(handle, output, length=1024 * 1024)
                written += part.stat().st_size
        if written != int(manifest["size"]):
            raise ValueError(f"assembled upload expected {manifest['size']} bytes, received {written}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    result = write_upload_manifest(
        config,
        upload_id,
        destination,
        kind=str(manifest["kind"]),
        original_filename=str(manifest["filename"]),
    )
    shutil.rmtree(base)
    return result


def list_uploads(config: dict[str, Any]) -> list[dict[str, Any]]:
    root = storage_root(config) / "uploads"
    items: list[dict[str, Any]] = []
    for kind in ("source", "reaction"):
        directory = root / kind
        if not directory.exists():
            continue
        for manifest_path in directory.glob("*.json"):
            try:
                item = json.loads(manifest_path.read_text(encoding="utf-8"))
                media_path = Path(str(item.get("path") or ""))
                if not media_path.is_file():
                    continue
                item["size"] = media_path.stat().st_size
                items.append(item)
            except (OSError, json.JSONDecodeError):
                continue
    return sorted(items, key=lambda item: str(item.get("uploaded_at") or ""), reverse=True)


def find_upload(config: dict[str, Any], upload_id: str) -> dict[str, Any]:
    if not UPLOAD_ID_PATTERN.fullmatch(upload_id):
        raise ValueError("invalid upload id")
    for item in list_uploads(config):
        if item.get("id") == upload_id:
            return item
    raise ValueError("upload does not exist")
