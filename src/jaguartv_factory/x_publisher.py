from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests

from .core import connect_db, now_iso
from .youtube_publisher import assert_ffprobe_readable, decrypt_refresh_token, publication_video_path


X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
X_MEDIA_UPLOAD_URL = "https://api.x.com/2/media/upload"
X_CREATE_POST_URL = "https://api.x.com/2/tweets"
X_POST_MAX_CHARS = 280


def _hashtag(value: Any) -> str:
    words = re.findall(r"[\wÀ-ÖØ-öø-ÿ]+", str(value or "").replace("#", " "), flags=re.UNICODE)
    return "#" + "".join(word[:1].upper() + word[1:] for word in words) if words else ""


def x_post_text(title: str, description: str, tags: list[str]) -> str:
    hook = re.sub(r"\s+", " ", str(title or "")).strip()
    copy = re.sub(r"\s+", " ", str(description or "")).strip()
    hashtags: list[str] = []
    for value in tags:
        tag = _hashtag(value)
        if tag and tag.lower() not in {item.lower() for item in hashtags}:
            hashtags.append(tag)
    text = "\n\n".join(part for part in (hook, copy, " ".join(hashtags)) if part)
    if not hook or not copy or not hashtags:
        raise ValueError("X post requires a hook, copy, and hashtags")
    if len(text) > X_POST_MAX_CHARS:
        raise ValueError(f"X post exceeds the {X_POST_MAX_CHARS} character limit")
    return text


def _response_json(response: requests.Response, action: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(f"X {action} failed: HTTP {response.status_code}")
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"X {action} returned invalid JSON") from error
    return payload if isinstance(payload, dict) else {}


def _token_headers(client_id: str, client_secret: str) -> dict[str, str]:
    if not client_secret:
        return {}
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def x_access_token(config: dict[str, Any], account: str) -> dict[str, str]:
    client_id = os.environ.get("JAGUARTV_X_CLIENT_ID", "").strip()
    client_secret = os.environ.get("JAGUARTV_X_CLIENT_SECRET", "").strip()
    if not client_id:
        raise RuntimeError("missing JAGUARTV_X_CLIENT_ID")
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM x_account_auths WHERE account=?", (account,)).fetchone()
    if not row or str(row["status"] or "") != "AUTHORIZED":
        raise RuntimeError(f"X account {account} is not authorized")
    scopes = set(str(row["scopes"] or "").split())
    missing = {"tweet.write", "media.write", "offline.access"} - scopes
    if missing:
        raise RuntimeError("X account is missing required scopes: " + ", ".join(sorted(missing)))
    encrypted_refresh = str(row["encrypted_refresh_token"] or "").strip()
    if not encrypted_refresh:
        raise RuntimeError(f"X account {account} has no refresh token")
    refresh_token = decrypt_refresh_token(encrypted_refresh)
    response = requests.post(
        X_TOKEN_URL,
        headers=_token_headers(client_id, client_secret),
        data={
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "client_id": client_id,
        },
        timeout=30,
    )
    payload = _response_json(response, "token refresh")
    access_token = str(payload.get("access_token") or "").strip()
    rotated_refresh = str(payload.get("refresh_token") or refresh_token).strip()
    if not access_token:
        raise RuntimeError("X token refresh did not return access_token")
    from .dashboard import encrypt_oauth_secret

    timestamp = now_iso()
    expires_in = int(payload.get("expires_in") or 0)
    connection.execute(
        """
        UPDATE x_account_auths
        SET encrypted_access_token=?,encrypted_refresh_token=?,expires_in=?,updated_at=?
        WHERE account=?
        """,
        (
            encrypt_oauth_secret(access_token),
            encrypt_oauth_secret(rotated_refresh),
            expires_in,
            timestamp,
            account,
        ),
    )
    connection.commit()
    return {"access_token": access_token, "username": str(row["username"] or ""), "source": "oauth_refresh"}


def _wait_for_media(
    access_token: str,
    media_id: str,
    processing_info: dict[str, Any],
    *,
    session: Any,
    sleep: Callable[[float], None],
    max_checks: int = 60,
) -> None:
    info = processing_info
    for _ in range(max_checks):
        state = str(info.get("state") or "succeeded")
        if state == "succeeded":
            return
        if state == "failed":
            raise RuntimeError("X media processing failed")
        sleep(max(0, min(10, float(info.get("check_after_secs") or 1))))
        response = session.get(
            X_MEDIA_UPLOAD_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"command": "STATUS", "media_id": media_id},
            timeout=30,
        )
        payload = _response_json(response, "media status")
        info = (payload.get("data") or {}).get("processing_info") or {}
    raise RuntimeError("X media processing timed out")


def upload_x_video(
    access_token: str,
    video_path: Path,
    *,
    session: Any = requests,
    chunk_size: int = 4 * 1024 * 1024,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    if not video_path.is_file() or video_path.stat().st_size <= 0:
        raise FileNotFoundError(video_path)
    headers = {"Authorization": f"Bearer {access_token}"}
    init = session.post(
        X_MEDIA_UPLOAD_URL,
        headers=headers,
        data={
            "command": "INIT",
            "media_type": "video/mp4",
            "total_bytes": video_path.stat().st_size,
            "media_category": "tweet_video",
        },
        timeout=30,
    )
    init_payload = _response_json(init, "media INIT")
    media_id = str((init_payload.get("data") or {}).get("id") or init_payload.get("media_id_string") or "")
    if not media_id:
        raise RuntimeError("X media INIT did not return media id")
    with video_path.open("rb") as handle:
        segment_index = 0
        while chunk := handle.read(chunk_size):
            append = session.post(
                X_MEDIA_UPLOAD_URL,
                headers=headers,
                data={"command": "APPEND", "media_id": media_id, "segment_index": segment_index},
                files={"media": (video_path.name, chunk, "application/octet-stream")},
                timeout=120,
            )
            if append.status_code >= 400:
                raise RuntimeError(f"X media APPEND failed: HTTP {append.status_code}")
            segment_index += 1
    finalize = session.post(
        X_MEDIA_UPLOAD_URL,
        headers=headers,
        data={"command": "FINALIZE", "media_id": media_id},
        timeout=30,
    )
    final_payload = _response_json(finalize, "media FINALIZE")
    processing_info = (final_payload.get("data") or {}).get("processing_info") or {}
    _wait_for_media(access_token, media_id, processing_info, session=session, sleep=sleep)
    return media_id


def create_x_post(access_token: str, text: str, media_id: str, *, session: Any = requests) -> dict[str, str]:
    if not text or len(text) > X_POST_MAX_CHARS:
        raise ValueError(f"X post text must contain 1-{X_POST_MAX_CHARS} characters")
    response = session.post(
        X_CREATE_POST_URL,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"text": text, "media": {"media_ids": [media_id]}},
        timeout=30,
    )
    payload = _response_json(response, "post creation")
    post_id = str((payload.get("data") or {}).get("id") or "")
    if not post_id:
        raise RuntimeError("X post creation did not return post id")
    return {"x_post_id": post_id, "x_url": f"https://x.com/i/web/status/{post_id}"}


def upload_x_publication(config: dict[str, Any], publication_id: int) -> dict[str, Any]:
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    if not row:
        raise ValueError("publication does not exist")
    publication = dict(row)
    if publication["platform"] != "x":
        raise ValueError("only X publications are supported")
    video_path = publication_video_path(config, publication)
    probe = assert_ffprobe_readable(video_path)
    token = x_access_token(config, str(publication["account"]))
    try:
        tags_raw = json.loads(publication.get("tags_json") or "[]")
    except json.JSONDecodeError:
        tags_raw = []
    tags = [str(item) for item in tags_raw] if isinstance(tags_raw, list) else []
    text = x_post_text(
        str(publication.get("title") or ""),
        str(publication.get("description") or ""),
        tags,
    )
    media_id = upload_x_video(token["access_token"], video_path)
    result = create_x_post(token["access_token"], text, media_id)
    return {
        **result,
        "platform_video_id": result["x_post_id"],
        "public_url": result["x_url"],
        "video_path": str(video_path),
        "probe_streams": len(probe.get("streams") or []),
        "published_at": now_iso(),
    }
