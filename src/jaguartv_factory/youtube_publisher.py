from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests

from .core import connect_db, now_iso, require_binary, run_command
from .publisher import read_json_file
from .server_store import storage_root
from .core import workspace_dir


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_RESUMABLE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"


def account_env_name(account: str, suffix: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in account.upper()).strip("_")
    return f"JAGUARTV_YOUTUBE_{safe}_{suffix}"


def decrypt_refresh_token(encrypted: str) -> str:
    key = os.environ.get("JAGUARTV_OAUTH_TOKEN_KEY", "").strip()
    if not key:
        raise RuntimeError("missing JAGUARTV_OAUTH_TOKEN_KEY")
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("missing openssl; cannot decrypt OAuth refresh token")
    result = subprocess.run(
        [
            openssl, "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-salt", "-base64", "-A",
            "-pass", "env:JAGUARTV_OAUTH_TOKEN_KEY",
        ],
        input=encrypted.encode("ascii", errors="strict"),
        capture_output=True,
        check=False,
        env={**os.environ, "JAGUARTV_OAUTH_TOKEN_KEY": key},
    )
    if result.returncode != 0:
        raise RuntimeError("openssl failed to decrypt OAuth refresh token")
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RuntimeError("openssl failed to decrypt OAuth refresh token") from error


def youtube_access_token(config: dict[str, Any], account: str) -> dict[str, str]:
    direct = os.environ.get(account_env_name(account, "ACCESS_TOKEN"), "").strip()
    if direct:
        return {"access_token": direct, "source": "env_access_token"}
    client_id = os.environ.get("JAGUARTV_GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("JAGUARTV_GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("missing Google OAuth client env vars")
    connection = connect_db(config)
    row = connection.execute(
        "SELECT * FROM youtube_channel_auths WHERE account=?", (account,)
    ).fetchone()
    if not row or not str(row["encrypted_refresh_token"] or "").strip():
        raise RuntimeError(f"YouTube account {account} is not authorized")
    refresh_token = decrypt_refresh_token(str(row["encrypted_refresh_token"]))
    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        try:
            oauth_error = str((response.json() or {}).get("error") or "")
        except (ValueError, TypeError):
            oauth_error = ""
        if oauth_error == "invalid_grant":
            raise RuntimeError("Google token refresh failed: invalid_grant")
        raise RuntimeError(f"Google token refresh failed: HTTP {response.status_code}")
    payload = response.json()
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise RuntimeError("Google token refresh did not return access_token")
    return {"access_token": access_token, "source": "oauth_refresh"}


def publication_video_path(config: dict[str, Any], publication: dict[str, Any]) -> Path:
    package_id = str(publication.get("package_id") or publication.get("candidate_id") or "")
    asset_id = str(publication.get("asset_id") or package_id)
    roots = [workspace_dir(config) / "ready_for_review", storage_root(config) / "review"]
    candidates: list[Path] = []
    if ":" in asset_id:
        _, stem = asset_id.split(":", 1)
        candidates.extend(root / package_id / f"{stem}.mp4" for root in roots)
    candidates.extend(root / package_id / "video.mp4" for root in roots)
    for root in roots:
        package = root / package_id
        if package.exists():
            metadata = read_json_file(package / "metadata.json")
            for variant in metadata.get("output_variants") or []:
                if isinstance(variant, dict) and variant.get("path"):
                    candidates.append(Path(str(variant["path"])).expanduser())
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(f"publication video file not found for {asset_id or package_id}")


def assert_ffprobe_readable(path: Path) -> dict[str, Any]:
    result = run_command(
        [
            require_binary("ffprobe"),
            "-v", "error",
            "-show_streams",
            "-show_format",
            "-of", "json",
            str(path),
        ],
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("ffprobe could not read publication video")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError("ffprobe returned invalid JSON") from error
    if not payload.get("streams"):
        raise RuntimeError("ffprobe found no media streams")
    return payload


def upload_video_resumable(
    access_token: str,
    video_path: Path,
    *,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str,
) -> dict[str, str]:
    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "17",
        },
        "status": {"privacyStatus": privacy_status or "public"},
    }
    init = requests.post(
        YOUTUBE_RESUMABLE_UPLOAD_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(video_path.stat().st_size),
        },
        data=json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
        timeout=30,
    )
    if init.status_code >= 400 or "Location" not in init.headers:
        raise RuntimeError(f"YouTube resumable upload init failed: HTTP {init.status_code}")
    with video_path.open("rb") as handle:
        upload = requests.put(
            init.headers["Location"],
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "video/mp4",
                "Content-Length": str(video_path.stat().st_size),
            },
            data=handle,
            timeout=900,
        )
    if upload.status_code >= 400:
        raise RuntimeError(f"YouTube upload failed: HTTP {upload.status_code}")
    payload = upload.json()
    video_id = str(payload.get("id") or "")
    if not video_id:
        raise RuntimeError("YouTube upload response did not include video id")
    return {
        "youtube_video_id": video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
    }


def upload_youtube_publication(config: dict[str, Any], publication_id: int) -> dict[str, Any]:
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    if not row:
        raise ValueError("publication does not exist")
    publication = dict(row)
    if publication["platform"] != "youtube":
        raise ValueError("only YouTube publications are supported")
    video_path = publication_video_path(config, publication)
    probe = assert_ffprobe_readable(video_path)
    token = youtube_access_token(config, str(publication["account"]))
    tags = []
    try:
        parsed_tags = json.loads(publication.get("tags_json") or "[]")
        tags = [str(item) for item in parsed_tags if str(item).strip()] if isinstance(parsed_tags, list) else []
    except json.JSONDecodeError:
        tags = []
    result = upload_video_resumable(
        token["access_token"],
        video_path,
        title=str(publication.get("title") or "JaguarTV"),
        description=str(publication.get("description") or ""),
        tags=tags,
        privacy_status=str(publication.get("privacy_status") or "public"),
    )
    return {
        **result,
        "video_path": str(video_path),
        "probe_streams": len(probe.get("streams") or []),
        "published_at": now_iso(),
    }
