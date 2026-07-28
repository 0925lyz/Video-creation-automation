"""Source adapters: one per platform, independently failing.

Each adapter implements:
  search(term, limit) -> list of yt-dlp-like info dicts
      (keys: id, title, description, url/webpage_url, duration, view_count,
       like_count, comment_count, timestamp, thumbnail, extractor_key)
  download(url, output_template) -> None (raises on failure)

youtube/bilibili/tiktok/facebook use yt-dlp where supported. douyin/xiaohongshu call self-hosted
services (Douyin_TikTok_Download_API, XHS-Downloader) over HTTP because
yt-dlp support for those platforms is unreliable; both need account cookies
configured on the service side.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class SourceError(RuntimeError):
    """Search/download failure local to one adapter."""


def yt_dlp_binary() -> str:
    path = shutil.which("yt-dlp")
    if path:
        return path
    sibling = Path(sys.executable).parent / "yt-dlp"
    if sibling.exists():
        return str(sibling)
    raise SourceError("yt-dlp binary is missing")


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, text=True, capture_output=True)


def http_json(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def http_download(url: str, destination: Path, timeout: int = 300) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


class YtDlpAdapter:
    """youtube + bilibili via yt-dlp. Bilibili search 412s are fixed in
    yt-dlp >= 2024.02 (auto buvid3); a real cookies file further improves
    reliability."""

    search_prefixes = {"youtube": "ytsearch", "bilibili": "bilisearch", "tiktok": "tiktoksearch"}

    def __init__(self, platform: str, options: dict[str, Any] | None = None):
        self.platform = platform
        self.options = options or {}

    def _cookie_args(self) -> list[str]:
        cookies = str(self.options.get("cookies_file") or "").strip()
        if cookies:
            configured = Path(cookies).expanduser()
            if not configured.is_absolute() and self.options.get("_root"):
                configured = Path(str(self.options["_root"])) / configured
            if configured.exists():
                return ["--cookies", str(configured.resolve())]
        browser = str(
            os.environ.get(f"JAGUARTV_{self.platform.upper()}_COOKIES_FROM_BROWSER")
            or os.environ.get("JAGUARTV_COOKIES_FROM_BROWSER")
            or self.options.get("cookies_from_browser")
            or ""
        ).strip()
        if browser:
            return ["--cookies-from-browser", browser]
        session_cookie = self._session_cookie_file()
        return ["--cookies", str(session_cookie)] if session_cookie else []

    def _session_cookie_file(self) -> Path | None:
        root_value = str(self.options.get("_root") or "").strip()
        if not root_value:
            return None
        root = Path(root_value).expanduser().resolve()
        workspace = Path(str(self.options.get("_workspace") or "workspace"))
        if not workspace.is_absolute():
            workspace = root / workspace
        session_root = workspace / "sessions" / self.platform
        if not session_root.exists():
            return None
        manifests = sorted(session_root.glob("*/manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for manifest_path in manifests:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(manifest.get("status") or "") not in {"READY", ""}:
                continue
            value = str(manifest.get("cookie_file_path") or "").strip()
            candidate = Path(value).expanduser() if value else manifest_path.parent / "cookies.txt"
            if not candidate.is_absolute():
                candidate = root / candidate
            if candidate.is_file() and candidate.stat().st_size > 80:
                return candidate.resolve()
        return None

    def _js_runtime_args(self) -> list[str]:
        runtime = str(
            os.environ.get(f"JAGUARTV_{self.platform.upper()}_YTDLP_JS_RUNTIME")
            or os.environ.get("JAGUARTV_YTDLP_JS_RUNTIME")
            or self.options.get("js_runtime")
            or ""
        ).strip()
        if runtime:
            return ["--js-runtimes", runtime]
        deno = shutil.which("deno")
        if deno:
            return ["--js-runtimes", f"deno:{deno}"]
        node = shutil.which("node")
        if node:
            result = run([node, "--version"])
            try:
                major = int((result.stdout or "").strip().lstrip("v").split(".", 1)[0])
            except ValueError:
                major = 0
            if major >= 22:
                return ["--js-runtimes", f"node:{node}"]
        return []

    def search(self, term: str, limit: int) -> list[dict[str, Any]]:
        yt_dlp = yt_dlp_binary()
        prefix = self.search_prefixes.get(self.platform)
        if not prefix:
            raise SourceError(f"{self.platform} has no yt-dlp search support")
        result = run([
            yt_dlp, "--force-ipv4", "--flat-playlist", "--dump-single-json",
            "--no-warnings", *self._cookie_args(), *self._js_runtime_args(), f"{prefix}{limit}:{term}",
        ])
        if result.returncode != 0:
            raise SourceError(result.stderr.strip()[-500:] or f"{self.platform} search failed")
        payload = json.loads(result.stdout)
        return [entry for entry in payload.get("entries", []) if entry]

    def download(self, url: str, output_template: str) -> None:
        args = [
            yt_dlp_binary(), "--force-ipv4", "--no-playlist", "--write-info-json",
            *self._cookie_args(), *self._js_runtime_args(),
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
            "-o", output_template, url,
        ]
        result = run(args)
        if result.returncode != 0:
            raise SourceError(result.stderr.strip()[-1000:] or "download failed")


class DouyinApiAdapter:
    """Self-hosted Douyin_TikTok_Download_API (Evil0ctal). Requires the
    service to be running with valid Douyin cookies in its config."""

    def __init__(self, platform: str, options: dict[str, Any] | None = None):
        self.platform = platform
        options = options or {}
        self.api_base = str(options.get("api_base") or "http://127.0.0.1:8000").rstrip("/")

    def search(self, term: str, limit: int) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"keyword": term, "count": limit, "offset": 0})
        try:
            payload = http_json(f"{self.api_base}/api/douyin/web/fetch_general_search_result?{query}")
        except Exception as error:
            raise SourceError(f"douyin search service unreachable: {error}") from error
        entries = []
        data = payload.get("data") or {}
        for item in (data.get("data") or [])[:limit]:
            aweme = item.get("aweme_info") or item
            stats = aweme.get("statistics") or {}
            video_id = str(aweme.get("aweme_id") or "")
            if not video_id:
                continue
            entries.append({
                "id": video_id,
                "title": str(aweme.get("desc") or ""),
                "description": str(aweme.get("desc") or ""),
                "webpage_url": f"https://www.douyin.com/video/{video_id}",
                "duration": (aweme.get("video") or {}).get("duration", 0) / 1000 or None,
                "view_count": int(stats.get("play_count") or 0),
                "like_count": int(stats.get("digg_count") or 0),
                "comment_count": int(stats.get("comment_count") or 0),
                "repost_count": int(stats.get("share_count") or 0),
                "timestamp": aweme.get("create_time"),
                "thumbnail": ((aweme.get("video") or {}).get("cover") or {}).get("url_list", [""])[0],
                "extractor_key": "douyin",
            })
        return entries

    def download(self, url: str, output_template: str) -> None:
        query = urllib.parse.urlencode({"url": url, "minimal": "false"})
        try:
            payload = http_json(f"{self.api_base}/api/hybrid/video_data?{query}")
        except Exception as error:
            raise SourceError(f"douyin resolve service unreachable: {error}") from error
        data = payload.get("data") or {}
        candidates = (
            [(data.get("video_data") or {}).get("nwm_video_url_HQ")]
            + ((data.get("video") or {}).get("play_addr") or {}).get("url_list", [])
        )
        video_url = next((item for item in candidates if item), None)
        if not video_url:
            raise SourceError("douyin service returned no downloadable URL")
        destination = Path(output_template.replace("%(ext)s", "mp4"))
        http_download(video_url, destination)
        info_path = destination.with_suffix(".info.json")
        info_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class XhsApiAdapter:
    """Self-hosted XHS-Downloader (JoeanAmier) in API-server mode.
    Search coverage depends on the deployed version; download works from
    explicit note URLs, which also enables the ingest workflow."""

    def __init__(self, platform: str, options: dict[str, Any] | None = None):
        self.platform = platform
        options = options or {}
        self.api_base = str(options.get("api_base") or "http://127.0.0.1:5556").rstrip("/")

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            f"{self.api_base}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def search(self, term: str, limit: int) -> list[dict[str, Any]]:
        raise SourceError(
            "xiaohongshu keyword search is not exposed by XHS-Downloader; "
            "use `jaguartv ingest <note-url>` for XHS notes instead"
        )

    def download(self, url: str, output_template: str) -> None:
        try:
            payload = self._post("/xhs/detail", {"url": url, "download": False})
        except Exception as error:
            raise SourceError(f"xhs service unreachable: {error}") from error
        data = payload.get("data") or {}
        video_url = ""
        downloads = data.get("下载地址") or data.get("download_url") or []
        if isinstance(downloads, list) and downloads:
            video_url = str(downloads[0])
        elif isinstance(downloads, str):
            video_url = downloads
        if not video_url:
            raise SourceError("xhs service returned no downloadable URL (note may be image-only)")
        destination = Path(output_template.replace("%(ext)s", "mp4"))
        http_download(video_url, destination)
        destination.with_suffix(".info.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


ADAPTERS = {
    "youtube": YtDlpAdapter,
    "bilibili": YtDlpAdapter,
    "tiktok": YtDlpAdapter,
    "facebook": YtDlpAdapter,
    "douyin": DouyinApiAdapter,
    "xiaohongshu": XhsApiAdapter,
}

SEARCHABLE_PLATFORMS = ("youtube", "bilibili", "douyin", "tiktok", "facebook")


def get_adapter(platform: str, config: dict[str, Any]) -> Any:
    factory = ADAPTERS.get(platform)
    if not factory:
        raise SourceError(f"unsupported platform: {platform}")
    options = dict((config.get("sources", {}).get("adapters") or {}).get(platform) or {})
    options["_root"] = str(config.get("_root") or "")
    options["_workspace"] = str((config.get("run", {}) or {}).get("workspace") or "workspace")
    return factory(platform, options)
