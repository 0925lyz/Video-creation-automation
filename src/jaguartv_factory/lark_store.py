"""Lark (Feishu) Drive upload for finished review packages.

Uploads video.mp4 + cover.jpg + metadata.json from
workspace/ready_for_review/<candidate-id>/ into a configured Drive folder,
creating a per-candidate subfolder. Uses tenant_access_token auth and the
official open-platform endpoints:
  - POST /open-apis/auth/v3/tenant_access_token/internal
  - POST /open-apis/drive/v1/files/create_folder
  - POST /open-apis/drive/v1/files/upload_all           (files <= 20MB)
  - upload_prepare / upload_part / upload_finish        (larger files)

Config (pipeline.yaml):
  lark:
    enabled: true
    domain: https://open.feishu.cn      # or https://open.larksuite.com
    app_id: cli_xxx
    app_secret_env: LARK_APP_SECRET     # secret read from this env var
    folder_token: fldcnxxxx             # target folder shared with the app
    upload: [video, cover, metadata]
"""
from __future__ import annotations

import json
import math
import os
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request

SMALL_FILE_LIMIT = 20 * 1024 * 1024
PART_SIZE = 4 * 1024 * 1024

UPLOAD_TARGETS = {
    "video": "video.mp4",
    "cover": "cover.jpg",
    "metadata": "metadata.json",
}


class LarkError(RuntimeError):
    pass


def lark_settings(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("lark") or {}


def lark_enabled(config: dict[str, Any]) -> bool:
    settings = lark_settings(config)
    return bool(settings.get("enabled")) and bool(settings.get("app_id")) and bool(settings.get("folder_token"))


def _domain(settings: dict[str, Any]) -> str:
    return str(settings.get("domain") or "https://open.feishu.cn").rstrip("/")


def _post_json(url: str, body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as http_error:
        raise LarkError(f"lark http {http_error.code}: {http_error.read().decode('utf-8', 'replace')[:300]}") from http_error
    if payload.get("code") != 0:
        raise LarkError(f"lark api error {payload.get('code')}: {payload.get('msg')}")
    return payload.get("data") or {}


def _post_multipart(url: str, fields: dict[str, Any], file_field: str, file_name: str,
                    blob: bytes, token: str) -> dict[str, Any]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{file_name}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n".encode("utf-8")
    )
    parts.append(blob)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = request.Request(url, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Authorization": f"Bearer {token}",
    })
    try:
        with request.urlopen(req, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as http_error:
        raise LarkError(f"lark upload http {http_error.code}: {http_error.read().decode('utf-8', 'replace')[:300]}") from http_error
    if payload.get("code") != 0:
        raise LarkError(f"lark upload error {payload.get('code')}: {payload.get('msg')}")
    return payload.get("data") or {}


def tenant_token(settings: dict[str, Any]) -> str:
    """tenant_access_token lives at the top level of the response, not under data."""
    secret_env = str(settings.get("app_secret_env") or "LARK_APP_SECRET")
    secret = os.environ.get(secret_env, "").strip()
    if not secret:
        raise LarkError(f"missing app secret: set the {secret_env} environment variable")
    url = f"{_domain(settings)}/open-apis/auth/v3/tenant_access_token/internal"
    body = {"app_id": str(settings.get("app_id")), "app_secret": secret}
    req = request.Request(url, data=json.dumps(body).encode("utf-8"),
                          headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise LarkError(f"lark auth error {payload.get('code')}: {payload.get('msg')}")
    return str(payload.get("tenant_access_token") or "")


def create_folder(settings: dict[str, Any], token: str, name: str, parent_token: str) -> str:
    data = _post_json(
        f"{_domain(settings)}/open-apis/drive/v1/files/create_folder",
        {"name": name, "folder_token": parent_token},
        token,
    )
    return str(data.get("token") or "")


def upload_file(settings: dict[str, Any], token: str, path: Path, parent_token: str) -> str:
    size = path.stat().st_size
    domain = _domain(settings)
    if size <= SMALL_FILE_LIMIT:
        data = _post_multipart(
            f"{domain}/open-apis/drive/v1/files/upload_all",
            {"file_name": path.name, "parent_type": "explorer", "parent_node": parent_token, "size": size},
            "file", path.name, path.read_bytes(), token,
        )
        return str(data.get("file_token") or "")
    prepare = _post_json(
        f"{domain}/open-apis/drive/v1/files/upload_prepare",
        {"file_name": path.name, "parent_type": "explorer", "parent_node": parent_token, "size": size},
        token,
    )
    upload_id = prepare.get("upload_id")
    block_size = int(prepare.get("block_size") or PART_SIZE)
    block_count = int(prepare.get("block_num") or math.ceil(size / block_size))
    with path.open("rb") as handle:
        for seq in range(block_count):
            chunk = handle.read(block_size)
            _post_multipart(
                f"{domain}/open-apis/drive/v1/files/upload_part",
                {"upload_id": upload_id, "seq": seq, "size": len(chunk)},
                "file", path.name, chunk, token,
            )
    finish = _post_json(
        f"{domain}/open-apis/drive/v1/files/upload_finish",
        {"upload_id": upload_id, "block_num": block_count},
        token,
    )
    return str(finish.get("file_token") or "")


def file_url(settings: dict[str, Any], file_token: str) -> str:
    base = "https://www.feishu.cn" if "feishu" in _domain(settings) else "https://www.larksuite.com"
    return f"{base}/file/{file_token}"


def upload_review_package(config: dict[str, Any], candidate_id: str, review_dir: Path) -> dict[str, Any]:
    """Upload selected artifacts for one candidate; returns {files, folder_url}."""
    settings = lark_settings(config)
    if not lark_enabled(config):
        raise LarkError("lark upload is not enabled/configured")
    token = tenant_token(settings)
    wanted = settings.get("upload") or ["video", "cover", "metadata"]
    title_hint = ""
    metadata_path = review_dir / "metadata.json"
    if metadata_path.exists():
        try:
            title_hint = str(json.loads(metadata_path.read_text(encoding="utf-8")).get("source", {}).get("title") or "")[:20]
        except json.JSONDecodeError:
            pass
    folder_name = f"{candidate_id}_{title_hint}".strip("_")
    folder = create_folder(settings, token, folder_name, str(settings.get("folder_token")))
    uploaded = {}
    for key in wanted:
        filename = UPLOAD_TARGETS.get(str(key))
        if not filename:
            continue
        path = review_dir / filename
        if not path.exists():
            continue
        file_token = upload_file(settings, token, path, folder)
        uploaded[filename] = {"file_token": file_token, "url": file_url(settings, file_token)}
    return {"folder_token": folder, "files": uploaded}
