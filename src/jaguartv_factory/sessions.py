from __future__ import annotations

import json
import re
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import now_iso, workspace_dir


PLATFORM_LOGIN_URLS = {
    "douyin": "https://www.douyin.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "bilibili": "https://www.bilibili.com/",
    "youtube": "https://www.youtube.com/",
    "tiktok": "https://www.tiktok.com/",
    "facebook": "https://www.facebook.com/",
}

PLATFORM_COOKIE_DOMAINS = {
    "douyin": ".douyin.com",
    "xiaohongshu": ".xiaohongshu.com",
    "bilibili": ".bilibili.com",
    "youtube": ".youtube.com",
    "tiktok": ".tiktok.com",
    "facebook": ".facebook.com",
}

SESSION_PLATFORMS = tuple(PLATFORM_LOGIN_URLS)


def safe_slug(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", value.strip())
    value = value.strip("._-")
    if not value:
        raise ValueError("account is required")
    return value[:80]


def sessions_root(config: dict[str, Any]) -> Path:
    return workspace_dir(config) / "sessions"


def session_dir(config: dict[str, Any], platform: str, account: str) -> Path:
    platform = normalize_platform(platform)
    return sessions_root(config) / platform / safe_slug(account)


def manifest_path(config: dict[str, Any], platform: str, account: str) -> Path:
    return session_dir(config, platform, account) / "manifest.json"


def normalize_platform(platform: str) -> str:
    platform = str(platform or "").strip().lower()
    if platform not in SESSION_PLATFORMS:
        raise ValueError(f"platform must be one of {SESSION_PLATFORMS}")
    return platform


def relative_to_root(config: dict[str, Any], path: Path) -> str:
    root = Path(str(config.get("_root") or ".")).resolve()
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def absolute_from_root(config: dict[str, Any], value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (Path(str(config.get("_root") or ".")).resolve() / path).resolve()


def load_storage_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"cookies": [], "origins": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"cookies": data, "origins": []}
    if not isinstance(data, dict):
        raise ValueError("storage state must be a JSON object or cookie list")
    cookies = data.get("cookies")
    if cookies is None:
        data["cookies"] = []
    if not isinstance(data["cookies"], list):
        raise ValueError("storage_state.cookies must be a list")
    data.setdefault("origins", [])
    return data


def normalize_storage_state(raw_text: str, default_domain: str = "") -> dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as error:
        try:
            return {"cookies": parse_cookie_text(raw_text, default_domain=default_domain), "origins": []}
        except ValueError:
            raise ValueError(f"cookies_json is not valid JSON or cookie table text: {error}") from error
    if isinstance(parsed, list):
        parsed = {"cookies": parsed, "origins": []}
    if not isinstance(parsed, dict):
        raise ValueError("cookies_json must be a Playwright storage_state object or cookies list")
    cookies = parsed.get("cookies") or []
    if not isinstance(cookies, list):
        raise ValueError("cookies_json.cookies must be a list")
    for cookie in cookies:
        if not isinstance(cookie, dict) or not cookie.get("name") or not cookie.get("value"):
            raise ValueError("each cookie must include name and value")
        if not cookie.get("domain") and not cookie.get("url"):
            if not default_domain:
                raise ValueError("each cookie must include domain or url")
            cookie["domain"] = default_domain
        if cookie.get("domain") and "." not in str(cookie.get("domain")):
            raise ValueError("cookie domain is invalid")
        cookie.setdefault("path", "/")
    parsed["cookies"] = cookies
    parsed.setdefault("origins", [])
    return parsed


def parse_cookie_expires(value: str) -> float:
    value = str(value or "").strip()
    if not value or value.lower() == "session":
        return -1
    try:
        return float(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError as error:
        raise ValueError(f"unsupported cookie expiry: {value}") from error


def parse_cookie_header_line(line: str, default_domain: str) -> list[dict[str, Any]]:
    if not default_domain:
        return []
    ignored = {
        "path", "domain", "expires", "max-age", "samesite", "secure", "httponly",
        "priority", "partitioned",
    }
    cookies: list[dict[str, Any]] = []
    for part in line.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name or name.lower() in ignored:
            continue
        cookies.append({
            "name": name,
            "value": value.strip(),
            "domain": default_domain,
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        })
    return cookies


def parse_cookie_text(raw_text: str, default_domain: str = "") -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    text = raw_text.replace("\\t", "\t")
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        http_only_prefix = "#HttpOnly_"
        http_only = line.startswith(http_only_prefix)
        if line.startswith("#") and not http_only:
            continue
        if http_only:
            raw_line = raw_line.replace(http_only_prefix, "", 1)
            line = line.replace(http_only_prefix, "", 1)
        fields = raw_line.split("\t")
        if (
            len(fields) >= 7
            and fields[1].upper() in {"TRUE", "FALSE"}
            and fields[2].startswith("/")
            and fields[3].upper() in {"TRUE", "FALSE"}
        ):
            domain, _include_subdomains, path, secure, expires, name, value = fields[:7]
            if not domain or "." not in domain or not path.startswith("/") or not name:
                continue
            cookies.append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
                "expires": parse_cookie_expires(expires),
                "httpOnly": http_only,
                "secure": secure.upper() == "TRUE",
                "sameSite": "Lax",
            })
            continue
        if len(fields) >= 5 and "." in fields[2]:
            name, value, domain, path, expires = fields[:5]
            if not domain or not path.startswith("/") or not name:
                continue
            flags = fields[5:]
            same_site = next((item for item in flags if item in {"Strict", "Lax", "None"}), "Lax")
            cookies.append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
                "expires": parse_cookie_expires(expires),
                "httpOnly": "✓" in flags[:3],
                "secure": "✓" in flags[1:4],
                "sameSite": same_site,
            })
            continue
        header_cookies = parse_cookie_header_line(line, default_domain)
        if header_cookies:
            cookies.extend(header_cookies)
            continue
        continue
    if not cookies:
        raise ValueError("no cookies found")
    return cookies


def cookie_expiry_summary(cookies: list[dict[str, Any]]) -> tuple[str, str]:
    timestamp = int(time.time())
    expiring = []
    persistent = []
    for cookie in cookies:
        expires = cookie.get("expires")
        try:
            expires_int = int(float(expires))
        except (TypeError, ValueError):
            expires_int = -1
        if expires_int > 0:
            persistent.append(expires_int)
            if expires_int <= timestamp:
                expiring.append(expires_int)
    if cookies and persistent and len(expiring) == len(persistent):
        return "EXPIRED", "cookie 已过期，需要重新扫码登录"
    if cookies:
        nearest = min(persistent) if persistent else 0
        expires_at = (
            datetime.fromtimestamp(nearest, timezone.utc).isoformat()
            if nearest else ""
        )
        return "READY", expires_at
    return "NEEDS_LOGIN", ""


def write_netscape_cookie_file(storage_state: dict[str, Any], destination: Path) -> int:
    cookies = storage_state.get("cookies") or []
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by JaguarTV Content Factory session manager",
    ]
    count = 0
    for cookie in cookies:
        domain = str(cookie.get("domain") or "")
        if not domain:
            continue
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        path = str(cookie.get("path") or "/")
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        try:
            expires = int(float(cookie.get("expires") or 0))
        except (TypeError, ValueError):
            expires = 0
        if expires < 0:
            expires = 0
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        lines.append("\t".join([domain, include_subdomains, path, secure, str(expires), name, value]))
        count += 1
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


def load_manifest(config: dict[str, Any], platform: str, account: str) -> dict[str, Any]:
    path = manifest_path(config, platform, account)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_session(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    platform = normalize_platform(str(payload.get("platform") or ""))
    account = safe_slug(str(payload.get("account") or ""))
    base = session_dir(config, platform, account)
    base.mkdir(parents=True, exist_ok=True)

    state_path = base / "storage_state.json"
    cookie_path = base / "cookies.txt"
    profile_dir = base / "browser_profile"

    cookies_json = str(payload.get("cookies_json") or "").strip()
    cookie_count = 0
    if cookies_json:
        storage_state = normalize_storage_state(cookies_json, default_domain=PLATFORM_COOKIE_DOMAINS.get(platform, ""))
        state_path.write_text(json.dumps(storage_state, ensure_ascii=False, indent=2), encoding="utf-8")
        cookie_count = write_netscape_cookie_file(storage_state, cookie_path)
    elif state_path.exists():
        storage_state = load_storage_state(state_path)
        cookie_count = write_netscape_cookie_file(storage_state, cookie_path)

    status = check_session_files(state_path)
    current = load_manifest(config, platform, account)
    manifest = {
        **current,
        "platform": platform,
        "account": account,
        "label": str(payload.get("label") or current.get("label") or account).strip(),
        "owner": str(payload.get("owner") or current.get("owner") or "").strip(),
        "purpose": str(payload.get("purpose") or current.get("purpose") or "source_discovery").strip(),
        "login_url": str(payload.get("login_url") or current.get("login_url") or PLATFORM_LOGIN_URLS[platform]).strip(),
        "notes": str(payload.get("notes") or current.get("notes") or "").strip(),
        "status": status["status"],
        "status_reason": status["reason"],
        "last_checked_at": now_iso(),
        "updated_at": now_iso(),
        "storage_state_path": relative_to_root(config, state_path),
        "cookie_file_path": relative_to_root(config, cookie_path),
        "profile_dir": relative_to_root(config, profile_dir),
        "cookie_count": cookie_count or status["cookie_count"],
        "expires_at": status["expires_at"],
        "login_command": login_command(config, platform, account),
    }
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def check_session_files(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {
            "status": "NEEDS_LOGIN",
            "reason": "尚未保存 storage_state.json；需要人工扫码登录或粘贴 cookies",
            "cookie_count": 0,
            "expires_at": "",
        }
    try:
        state = load_storage_state(state_path)
    except Exception as error:
        return {"status": "INVALID", "reason": str(error), "cookie_count": 0, "expires_at": ""}
    cookies = state.get("cookies") or []
    status, expires_at = cookie_expiry_summary(cookies)
    reason = "本地登录态文件存在，可供采集器复用" if status == "READY" else "未发现有效 cookies"
    if status == "EXPIRED":
        reason = "本地 cookie 过期，需要重新登录"
    return {"status": status, "reason": reason, "cookie_count": len(cookies), "expires_at": expires_at}


def check_session(config: dict[str, Any], platform: str, account: str) -> dict[str, Any]:
    platform = normalize_platform(platform)
    account = safe_slug(account)
    current = load_manifest(config, platform, account)
    state_path = absolute_from_root(
        config,
        str(current.get("storage_state_path") or relative_to_root(config, session_dir(config, platform, account) / "storage_state.json")),
    )
    status = check_session_files(state_path)
    if state_path.exists():
        write_netscape_cookie_file(load_storage_state(state_path), absolute_from_root(config, str(current.get("cookie_file_path") or relative_to_root(config, session_dir(config, platform, account) / "cookies.txt"))))
    current.update({
        "platform": platform,
        "account": account,
        "status": status["status"],
        "status_reason": status["reason"],
        "last_checked_at": now_iso(),
        "cookie_count": status["cookie_count"],
        "expires_at": status["expires_at"],
        "login_url": current.get("login_url") or PLATFORM_LOGIN_URLS[platform],
        "login_command": login_command(config, platform, account),
    })
    base = session_dir(config, platform, account)
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def list_sessions(config: dict[str, Any]) -> list[dict[str, Any]]:
    root = sessions_root(config)
    items: list[dict[str, Any]] = []
    if not root.exists():
        return items
    for path in sorted(root.glob("*/*/manifest.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        item["login_command"] = login_command(config, item.get("platform", ""), item.get("account", ""))
        items.append(item)
    return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def delete_session(config: dict[str, Any], platform: str, account: str) -> dict[str, Any]:
    base = session_dir(config, platform, account)
    if not base.exists():
        raise ValueError("session does not exist")
    for child in sorted(base.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    base.rmdir()
    return {"deleted": True, "platform": normalize_platform(platform), "account": safe_slug(account)}


def login_command(config: dict[str, Any], platform: str, account: str) -> str:
    try:
        platform = normalize_platform(platform)
        account = safe_slug(account)
    except ValueError:
        return ""
    root = Path(str(config.get("_root") or ".")).resolve()
    config_path = Path(str(config.get("_path") or "config/pipeline.yaml")).resolve()
    try:
        config_arg = str(config_path.relative_to(root))
    except ValueError:
        config_arg = str(config_path)
    python_bin = root / ".venv" / "bin" / "python"
    python_cmd = str(python_bin) if python_bin.exists() else "python3"
    return (
        f"cd {shlex.quote(str(root))} && "
        f"{shlex.quote(python_cmd)} -m jaguartv_factory.session_login "
        f"--config {shlex.quote(config_arg)} --platform {shlex.quote(platform)} --account {shlex.quote(account)}"
    )
