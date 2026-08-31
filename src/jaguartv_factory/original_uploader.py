from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class OriginalUploadError(RuntimeError):
    pass


def _validated_base_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("dashboard URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("dashboard URL must not contain credentials, query, or fragment")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def upload_original(
    video_path: Path,
    metadata: dict[str, Any],
    *,
    base_url: str,
    upload_token: str,
    session: Any = requests,
    timeout_sec: int = 900,
) -> dict[str, Any]:
    path = video_path.expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".mp4":
        raise ValueError("video must be an existing .mp4 file")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    token = str(upload_token or "").strip()
    if not token:
        raise ValueError("upload token is required")
    payload = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    digest = hashlib.sha256()
    digest.update(payload)
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    request_id = f"daily-original-{digest.hexdigest()[:32]}"
    headers = {
        "Content-Type": "video/mp4",
        "X-Upload-Token": token,
        "X-Original-Metadata": encoded,
        "X-Request-ID": request_id,
        "X-Operator": "daily-original-worker",
        "X-Original-Batch-Size": "1",
    }
    try:
        with path.open("rb") as source:
            response = session.post(
                f"{_validated_base_url(base_url)}/api/originals/import",
                params={"filename": path.name},
                data=source,
                headers=headers,
                timeout=timeout_sec,
            )
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as error:
        detail = ""
        response = getattr(error, "response", None)
        if response is not None:
            try:
                detail = str(response.json().get("error") or "")[:300]
            except (ValueError, AttributeError):
                detail = ""
        raise OriginalUploadError(detail or "original video upload failed") from error
    if not isinstance(result, dict) or not result.get("id"):
        raise OriginalUploadError("upload server returned an invalid response")
    return result
