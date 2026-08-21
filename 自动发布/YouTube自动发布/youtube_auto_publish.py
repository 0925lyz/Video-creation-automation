from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests


YOUTUBE_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
GOOGLE_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def connect_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    connection.executescript(schema_path.read_text(encoding="utf-8"))
    connection.commit()


def canonical_account_id(account: str) -> str:
    aliases = {
        "jaguartv vivo": "jaguartv_vivo",
        "jaguartv futebol": "jaguartv_vivo",
        "consumer_football": "jaguartv_vivo",
    }
    normalized = " ".join(str(account or "").strip().lower().split())
    return aliases.get(normalized, normalized or "jaguartv_vivo")


def oauth_redirect_uri() -> str:
    explicit = os.environ.get("JAGUARTV_GOOGLE_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    public_base = os.environ.get("JAGUARTV_PUBLIC_BASE_URL", "https://factory.jarg.top").strip()
    return public_base.rstrip("/") + "/oauth/youtube/callback"


def require_oauth_env() -> dict[str, str]:
    values = {
        "client_id": os.environ.get("JAGUARTV_GOOGLE_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("JAGUARTV_GOOGLE_CLIENT_SECRET", "").strip(),
        "redirect_uri": oauth_redirect_uri(),
        "token_key": os.environ.get("JAGUARTV_OAUTH_TOKEN_KEY", "").strip(),
        "state_secret": os.environ.get("JAGUARTV_OAUTH_STATE_SECRET", "").strip()
        or os.environ.get("JAGUARTV_DASHBOARD_TOKEN", "").strip(),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise RuntimeError("missing OAuth configuration: " + ", ".join(missing))
    return values


def sign_state(payload: str) -> str:
    secret = require_oauth_env()["state_secret"]
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_state(account: str) -> str:
    payload = f"{canonical_account_id(account)}:{int(time.time())}:{secrets.token_urlsafe(12)}"
    signed = f"{payload}:{sign_state(payload)}"
    return base64.urlsafe_b64encode(signed.encode("utf-8")).decode("ascii").rstrip("=")


def parse_state(value: str) -> str:
    if not value:
        raise ValueError("missing OAuth state; start from /oauth/youtube/start")
    padded = value + ("=" * (-len(value) % 4))
    decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    account, timestamp, nonce, signature = decoded.rsplit(":", 3)
    payload = f"{account}:{timestamp}:{nonce}"
    if not hmac.compare_digest(signature, sign_state(payload)):
        raise ValueError("invalid OAuth state signature")
    if abs(int(time.time()) - int(timestamp)) > 3600:
        raise ValueError("OAuth state expired")
    return canonical_account_id(account)


def encrypt_secret(secret_value: str) -> str:
    require_oauth_env()
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("missing openssl")
    result = subprocess.run(
        [
            openssl,
            "enc",
            "-aes-256-cbc",
            "-pbkdf2",
            "-salt",
            "-base64",
            "-A",
            "-pass",
            "env:JAGUARTV_OAUTH_TOKEN_KEY",
        ],
        input=secret_value,
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )
    if result.returncode:
        raise RuntimeError("openssl failed to encrypt OAuth token")
    return result.stdout.strip()


def decrypt_secret(encrypted_value: str) -> str:
    require_oauth_env()
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("missing openssl")
    result = subprocess.run(
        [
            openssl,
            "enc",
            "-d",
            "-aes-256-cbc",
            "-pbkdf2",
            "-base64",
            "-A",
            "-pass",
            "env:JAGUARTV_OAUTH_TOKEN_KEY",
        ],
        input=encrypted_value,
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )
    if result.returncode:
        raise RuntimeError("openssl failed to decrypt OAuth token")
    return result.stdout.strip()


def youtube_oauth_start_url(account: str = "consumer_football") -> str:
    config = require_oauth_env()
    params = {
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(YOUTUBE_OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": make_state(account),
    }
    return f"{GOOGLE_OAUTH_AUTH_URL}?{urlencode(params)}"


def get_authorized_channel(access_token: str) -> dict[str, str]:
    response = requests.get(
        YOUTUBE_CHANNELS_URL,
        params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    response.raise_for_status()
    items = response.json().get("items") or []
    if not items:
        raise RuntimeError("authorized account did not return a YouTube channel")
    item = items[0]
    return {
        "channel_id": str(item.get("id") or ""),
        "channel_title": str((item.get("snippet") or {}).get("title") or ""),
    }


def save_oauth_callback(db_path: Path, callback_url: str) -> dict[str, str]:
    load_dotenv()
    parsed = urlparse(callback_url)
    query = parse_qs(parsed.query)
    if error := str((query.get("error") or [""])[0]).strip():
        raise RuntimeError(f"Google OAuth returned error: {error}")
    code = str((query.get("code") or [""])[0]).strip()
    account = parse_state(str((query.get("state") or [""])[0]).strip())
    config = require_oauth_env()
    response = requests.post(
        GOOGLE_OAUTH_TOKEN_URL,
        data={
            "code": code,
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "redirect_uri": config["redirect_uri"],
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    response.raise_for_status()
    token = response.json()
    refresh_token = str(token.get("refresh_token") or "")
    access_token = str(token.get("access_token") or "")
    if not refresh_token:
        raise RuntimeError("Google did not return refresh_token; restart authorization with prompt=consent")
    if not access_token:
        raise RuntimeError("Google did not return access_token")
    channel = get_authorized_channel(access_token)
    timestamp = now_iso()
    connection = connect_db(db_path)
    ensure_schema(connection)
    connection.execute(
        """
        INSERT INTO youtube_channel_auths(
          account, channel_id, channel_title, scopes, encrypted_refresh_token,
          token_type, expires_in, authorized_at, updated_at, metadata_json
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(account) DO UPDATE SET
          channel_id=excluded.channel_id,
          channel_title=excluded.channel_title,
          scopes=excluded.scopes,
          encrypted_refresh_token=excluded.encrypted_refresh_token,
          token_type=excluded.token_type,
          expires_in=excluded.expires_in,
          updated_at=excluded.updated_at,
          metadata_json=excluded.metadata_json
        """,
        (
            account,
            channel["channel_id"],
            channel["channel_title"],
            str(token.get("scope") or " ".join(YOUTUBE_OAUTH_SCOPES)),
            encrypt_secret(refresh_token),
            str(token.get("token_type") or ""),
            int(token.get("expires_in") or 0),
            timestamp,
            timestamp,
            json.dumps({"provider": "google_oauth", "redirect_uri": config["redirect_uri"]}, ensure_ascii=False),
        ),
    )
    connection.commit()
    return {"account": account, **channel, "authorized_at": timestamp}


def refresh_access_token(connection: sqlite3.Connection, account: str) -> str:
    config = require_oauth_env()
    row = connection.execute(
        "SELECT encrypted_refresh_token FROM youtube_channel_auths WHERE account=?",
        (canonical_account_id(account),),
    ).fetchone()
    if not row:
        raise RuntimeError(f"missing YouTube authorization for account: {account}")
    refresh_token = decrypt_secret(row["encrypted_refresh_token"])
    response = requests.post(
        GOOGLE_OAUTH_TOKEN_URL,
        data={
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()
    access_token = response.json().get("access_token")
    if not access_token:
        raise RuntimeError("token refresh did not return access_token")
    return str(access_token)


def record_failure(connection: sqlite3.Connection, candidate_id: str, publication_id: int, message: str) -> None:
    timestamp = now_iso()
    if publication_id:
        connection.execute(
            "UPDATE publications SET status=?, error=?, updated_at=? WHERE id=?",
            ("FAILED", message[-1000:], timestamp, publication_id),
        )
    connection.execute(
        "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (
            candidate_id,
            "YOUTUBE_PRIVATE_TEST_FAILED",
            json.dumps({"error": message[-2000:]}, ensure_ascii=False),
            timestamp,
        ),
    )
    connection.commit()


def upload_private_video(
    db_path: Path,
    *,
    candidate_id: str,
    account: str,
    video_path: Path,
    title: str,
    description: str,
    source_platform: str,
) -> dict[str, str | int]:
    load_dotenv()
    account = canonical_account_id(account)
    source_platform = source_platform.strip().lower()
    if source_platform == "youtube":
        raise RuntimeError("refusing to upload a YouTube-source video back to YouTube")
    if not video_path.is_file():
        raise RuntimeError(f"video file does not exist: {video_path}")
    connection = connect_db(db_path)
    ensure_schema(connection)
    existing = connection.execute(
        "SELECT id, post_url FROM publications WHERE candidate_id=? AND platform=? AND account=? AND status=? LIMIT 1",
        (candidate_id, "youtube", account, "PUBLISHED"),
    ).fetchone()
    if existing:
        return {"status": "already_uploaded", "publication_id": existing["id"], "post_url": existing["post_url"]}
    timestamp = now_iso()
    cursor = connection.execute(
        "INSERT INTO publications(candidate_id,platform,account,scheduled_at,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (candidate_id, "youtube", account, timestamp, "QUEUED", timestamp, timestamp),
    )
    publication_id = int(cursor.lastrowid)
    connection.commit()
    try:
        access_token = refresh_access_token(connection, account)
        size = video_path.stat().st_size
        init_response = requests.post(
            YOUTUBE_UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status", "notifySubscribers": "false"},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": "video/mp4",
            },
            json={
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": ["JaguarTV", "PrivateTest"],
                    "categoryId": "17",
                },
                "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
            },
            timeout=30,
        )
        init_response.raise_for_status()
        upload_url = init_response.headers.get("Location")
        if not upload_url:
            raise RuntimeError("YouTube did not return resumable upload URL")
        connection.execute("UPDATE publications SET status=?, updated_at=? WHERE id=?", ("SCHEDULED", now_iso(), publication_id))
        connection.commit()
        with video_path.open("rb") as handle:
            upload_response = requests.put(
                upload_url,
                data=handle,
                headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
                timeout=300,
            )
        upload_response.raise_for_status()
        video_id = upload_response.json().get("id")
        if not video_id:
            raise RuntimeError("YouTube upload response did not include video id")
        post_url = f"https://www.youtube.com/watch?v={video_id}"
        timestamp = now_iso()
        connection.execute(
            "UPDATE publications SET status=?, published_at=?, post_url=?, error=?, updated_at=? WHERE id=?",
            ("PUBLISHED", timestamp, post_url, "", timestamp, publication_id),
        )
        connection.execute("UPDATE candidates SET published_flag=1, updated_at=? WHERE id=?", (timestamp, candidate_id))
        connection.execute(
            "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (
                candidate_id,
                "YOUTUBE_PRIVATE_TEST_UPLOADED",
                json.dumps(
                    {
                        "publication_id": publication_id,
                        "account": account,
                        "youtube_video_id": video_id,
                        "post_url": post_url,
                        "privacy_status": "private",
                        "asset": str(video_path),
                        "source_platform": source_platform,
                    },
                    ensure_ascii=False,
                ),
                timestamp,
            ),
        )
        connection.commit()
        return {"status": "uploaded_private", "publication_id": publication_id, "video_id": video_id, "post_url": post_url}
    except Exception as error:
        record_failure(connection, candidate_id, publication_id, str(error))
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="JaguarTV YouTube OAuth and private upload helper")
    parser.add_argument("--env", default=".env")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start-url")
    start.add_argument("--account", default="jaguartv_vivo")

    callback = subparsers.add_parser("callback")
    callback.add_argument("--db", default="workspace/factory.db")
    callback.add_argument("--url", required=True)

    upload = subparsers.add_parser("private-upload")
    upload.add_argument("--db", default="workspace/factory.db")
    upload.add_argument("--candidate", required=True)
    upload.add_argument("--account", default="jaguartv_vivo")
    upload.add_argument("--video", required=True)
    upload.add_argument("--title", required=True)
    upload.add_argument("--description", default="")
    upload.add_argument("--source-platform", required=True)

    args = parser.parse_args()
    load_dotenv(Path(args.env))
    if args.command == "start-url":
        print(youtube_oauth_start_url(args.account))
    elif args.command == "callback":
        print(json.dumps(save_oauth_callback(Path(args.db), args.url), ensure_ascii=False, indent=2))
    elif args.command == "private-upload":
        print(json.dumps(
            upload_private_video(
                Path(args.db),
                candidate_id=args.candidate,
                account=args.account,
                video_path=Path(args.video),
                title=args.title,
                description=args.description,
                source_platform=args.source_platform,
            ),
            ensure_ascii=False,
            indent=2,
        ))


if __name__ == "__main__":
    main()
