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
import tempfile
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

from .binaries import require_binary

class SourceError(RuntimeError):
    """Search/download failure local to one adapter."""


def yt_dlp_binary() -> str:
    configured = str(os.environ.get("JAGUARTV_YTDLP_BINARY") or "").strip()
    if configured:
        expanded = Path(configured).expanduser()
        if expanded.is_file() and os.access(expanded, os.X_OK):
            return str(expanded.resolve())
        if any(character.isspace() for character in configured):
            raise SourceError("JAGUARTV_YTDLP_BINARY must contain one executable path without arguments")
        located = shutil.which(configured) if expanded.name == configured else None
        if located:
            return located
        raise SourceError(f"JAGUARTV_YTDLP_BINARY is not an executable: {configured}")
    sibling = Path(sys.executable).parent / "yt-dlp"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    path = shutil.which("yt-dlp")
    if path:
        return path
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        yt_dlp = None
    if yt_dlp is not None:
        launcher = Path(sys.executable).parent / "yt-dlp"
        try:
            launcher.write_text(
                f"#!{sys.executable}\n"
                "import runpy\n"
                "runpy.run_module('yt_dlp', run_name='__main__')\n",
                encoding="utf-8",
            )
            launcher.chmod(0o755)
            return str(launcher)
        except OSError:
            return "yt-dlp"
    raise SourceError("yt-dlp binary is missing")


def run(args: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, text=True, capture_output=True, timeout=timeout)


def preferred_js_runtime() -> str:
    configured = str(os.environ.get("JAGUARTV_YTDLP_JS_RUNTIME") or "").strip()
    if configured:
        return configured
    for name in ("deno", "node", "qjs", "bun"):
        executable = shutil.which(name)
        if not executable:
            continue
        if name == "node":
            result = run([executable, "--version"], timeout=5)
            try:
                major = int((result.stdout or "").strip().lstrip("v").split(".", 1)[0])
            except ValueError:
                major = 0
            if result.returncode != 0 or major < 22:
                continue
        runtime_name = "quickjs" if name == "qjs" else name
        return f"{runtime_name}:{executable}"
    return ""


@lru_cache(maxsize=8)
def yt_dlp_capabilities(binary: str) -> dict[str, Any]:
    try:
        version_result = run([binary, "--version"], timeout=15)
        extractor_result = run([binary, "--list-extractors"], timeout=30)
        impersonation_result = run([binary, "--list-impersonate-targets"], timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "version": "",
            "extractor_count": 0,
            "impersonation": False,
            "error": str(error),
        }
    version = version_result.stdout.strip() if version_result.returncode == 0 else ""
    extractors = [line for line in extractor_result.stdout.splitlines() if line.strip()]
    impersonation_output = f"{impersonation_result.stdout}\n{impersonation_result.stderr}"
    return {
        "version": version,
        "extractor_count": len(extractors) if extractor_result.returncode == 0 else 0,
        "impersonation": impersonation_result.returncode == 0 and "curl_cffi" in impersonation_output,
        "error": "" if version else (version_result.stderr.strip() or "yt-dlp version check failed"),
    }


def yt_dlp_supports_impersonation(binary: str) -> bool:
    return bool(yt_dlp_capabilities(binary).get("impersonation"))


def yt_dlp_runtime_status() -> dict[str, Any]:
    try:
        binary = yt_dlp_binary()
    except SourceError as error:
        return {
            "ok": False,
            "path": "",
            "version": "",
            "extractor_count": 0,
            "impersonation": False,
            "js_runtime": preferred_js_runtime(),
            "error": str(error),
        }
    capabilities = yt_dlp_capabilities(binary)
    status = {
        "ok": bool(capabilities.get("version") and capabilities.get("extractor_count")),
        "path": binary,
        "version": str(capabilities.get("version") or ""),
        "extractor_count": int(capabilities.get("extractor_count") or 0),
        "impersonation": bool(capabilities.get("impersonation")),
        "js_runtime": preferred_js_runtime(),
    }
    if capabilities.get("error"):
        status["error"] = str(capabilities["error"])
    return status


def http_json(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def http_download(url: str, destination: Path, timeout: int = 300, headers: dict[str, str] | None = None) -> None:
    request_headers = {"User-Agent": "Mozilla/5.0"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def ffmpeg_download(url: str, destination: Path, timeout: int = 300, headers: dict[str, str] | None = None) -> None:
    try:
        ffmpeg = require_binary("ffmpeg")
    except RuntimeError as error:
        raise SourceError("ffmpeg binary is missing for HLS media download") from error
    header_lines = {"User-Agent": "Mozilla/5.0", **(headers or {})}
    header_blob = "".join(f"{key}: {value}\r\n" for key, value in header_lines.items())
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-headers", header_blob,
        "-i", url,
        "-c", "copy",
        str(destination),
    ], timeout=timeout)
    if result.returncode != 0:
        raise SourceError(result.stderr.strip()[-1000:] or "ffmpeg media download failed")


class YtDlpAdapter:
    """youtube + bilibili via yt-dlp. Bilibili search 412s are fixed in
    yt-dlp >= 2024.02 (auto buvid3); a real cookies file further improves
    reliability."""

    search_prefixes = {"youtube": "ytsearch", "bilibili": "bilisearch", "tiktok": "tiktoksearch"}

    def __init__(self, platform: str, options: dict[str, Any] | None = None):
        self.platform = platform
        self.options = options or {}

    def _scrape_config(self) -> dict[str, Any]:
        return {
            "_root": str(self.options.get("_root") or ""),
            "run": {"workspace": str(self.options.get("_workspace") or "workspace")},
        }

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
            or self.options.get("js_runtime")
            or preferred_js_runtime()
        ).strip()
        return ["--js-runtimes", runtime] if runtime else []

    def _impersonation_args(self) -> list[str]:
        value = str(
            os.environ.get(f"JAGUARTV_{self.platform.upper()}_YTDLP_IMPERSONATE")
            or os.environ.get("JAGUARTV_YTDLP_IMPERSONATE")
            or self.options.get("impersonate")
            or ""
        ).strip()
        if not value or value.lower() in {"false", "none", "off", "disabled"}:
            return []
        automatic = value.lower() == "auto"
        if automatic and self.platform not in {"tiktok", "facebook", "x", "instagram", "kwai"}:
            return []
        binary = yt_dlp_binary()
        if not yt_dlp_supports_impersonation(binary):
            if automatic:
                return []
            raise SourceError("yt-dlp impersonation requires the curl-cffi optional dependency")
        return ["--impersonate", "chrome" if automatic else value]

    def _runtime_args(self) -> list[str]:
        return [*self._cookie_args(), *self._js_runtime_args(), *self._impersonation_args()]

    def search(self, term: str, limit: int) -> list[dict[str, Any]]:
        yt_dlp = yt_dlp_binary()
        prefix = self.search_prefixes.get(self.platform)
        if not prefix:
            if self.platform == "facebook":
                try:
                    from .browser_scraper import search_facebook

                    return search_facebook(self._scrape_config(), term, limit)
                except Exception as browser_error:
                    raise SourceError(f"facebook browser search failed: {browser_error}") from browser_error
            raise SourceError(f"{self.platform} has no yt-dlp search support")
        timeout = float(self.options.get("search_timeout_sec") or 90)
        try:
            result = run([
                yt_dlp, "--ignore-config", "--force-ipv4", "--flat-playlist", "--dump-single-json",
                "--no-warnings", *self._runtime_args(), f"{prefix}{limit}:{term}",
            ], timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise SourceError(f"{self.platform} search timed out after {error.timeout}s") from error
        if result.returncode != 0:
            if self.platform == "tiktok":
                try:
                    from .browser_scraper import search_tiktok

                    return search_tiktok(self._scrape_config(), term, limit)
                except Exception as browser_error:
                    raise SourceError(
                        f"{result.stderr.strip()[-500:]}; browser fallback failed: {browser_error}"
                    ) from browser_error
            raise SourceError(result.stderr.strip()[-500:] or f"{self.platform} search failed")
        payload = json.loads(result.stdout)
        entries = [entry for entry in payload.get("entries", []) if entry]
        if not entries and self.platform == "tiktok":
            try:
                from .browser_scraper import search_tiktok

                return search_tiktok(self._scrape_config(), term, limit)
            except Exception as browser_error:
                raise SourceError(f"tiktok search returned no entries; browser fallback failed: {browser_error}") from browser_error
        return entries

    def download(self, url: str, output_template: str) -> None:
        args = [
            yt_dlp_binary(), "--ignore-config", "--force-ipv4", "--no-playlist", "--write-info-json",
            *self._runtime_args(),
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
            "-o", output_template, url,
        ]
        timeout = float(self.options.get("download_timeout_sec") or 300)
        try:
            result = run(args, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise SourceError(f"{self.platform} download timed out after {error.timeout}s") from error
        if result.returncode != 0:
            if self.platform == "tiktok":
                self._download_tiktok_with_browser_fallback(
                    url,
                    output_template,
                    timeout,
                    result.stderr.strip()[-1000:] or "download failed",
                )
                return
            raise SourceError(result.stderr.strip()[-1000:] or "download failed")

    def _download_tiktok_with_browser_fallback(
        self,
        url: str,
        output_template: str,
        timeout: float,
        original_error: str,
    ) -> None:
        try:
            from .browser_scraper import download_tiktok_video, resolve_tiktok_video

            destination = Path(output_template.replace("%(ext)s", "mp4"))
            try:
                data = download_tiktok_video(self._scrape_config(), url, destination)
                info_path = destination.with_suffix(".info.json")
                info_path.write_text(
                    json.dumps({"webpage_url": url, "browser_fallback": data}, ensure_ascii=False),
                    encoding="utf-8",
                )
                return
            except Exception:
                destination.unlink(missing_ok=True)
            data = resolve_tiktok_video(self._scrape_config(), url)
            video_url = str(data.get("video_url") or "").strip()
            if not video_url:
                raise SourceError("browser fallback returned no video URL")
            headers = {"Referer": "https://www.tiktok.com/"}
            if ".m3u8" in video_url.lower():
                ffmpeg_download(video_url, destination, int(timeout), headers=headers)
            else:
                http_download(video_url, destination, int(timeout), headers=headers)
            info_path = destination.with_suffix(".info.json")
            info_path.write_text(
                json.dumps({"webpage_url": url, "browser_fallback": data}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as fallback_error:
            raise SourceError(
                f"{original_error}; tiktok browser download fallback failed: {fallback_error}"
            ) from fallback_error


class F2DouyinAdapter:
    """Discover Douyin through the existing source service and download only with f2."""

    def __init__(self, platform: str, options: dict[str, Any] | None = None):
        self.platform = platform
        options = options or {}
        self.options = options
        self.api_base = str(options.get("api_base") or "http://127.0.0.1:8000").rstrip("/")

    def _binary(self) -> str:
        configured = str(os.environ.get("JAGUARTV_F2_BINARY") or self.options.get("binary") or "").strip()
        if configured:
            path = Path(configured).expanduser()
            if not path.is_absolute() and self.options.get("_root"):
                path = Path(str(self.options["_root"])) / path
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.resolve())
            located = shutil.which(configured) if Path(configured).name == configured else None
            if located:
                return located
            raise SourceError(f"f2 binary is not executable: {configured}")
        located = shutil.which("f2")
        if located:
            return located
        raise SourceError("f2 binary is missing; Douyin downloads must not fall back to yt-dlp")

    def _config_args(self) -> list[str]:
        configured = str(
            os.environ.get("JAGUARTV_F2_CONFIG_FILE") or self.options.get("config_file") or ""
        ).strip()
        if not configured:
            return []
        path = Path(configured).expanduser()
        if not path.is_absolute() and self.options.get("_root"):
            path = Path(str(self.options["_root"])) / path
        if not path.is_file():
            raise SourceError(f"f2 config file does not exist: {path}")
        return ["--config", str(path.resolve())]

    def _scrape_config(self) -> dict[str, Any]:
        return {
            "_root": str(self.options.get("_root") or ""),
            "run": {"workspace": str(self.options.get("_workspace") or "workspace")},
        }

    def search(self, term: str, limit: int) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"keyword": term, "count": limit, "offset": 0})
        try:
            payload = http_json(f"{self.api_base}/api/douyin/web/fetch_general_search_result?{query}")
        except Exception as error:
            try:
                from .browser_scraper import search_douyin

                return search_douyin(self._scrape_config(), term, limit)
            except Exception as browser_error:
                if "playwright is not installed" in str(browser_error).lower():
                    return []
                raise SourceError(
                    f"douyin search service unreachable: {error}; browser fallback failed: {browser_error}"
                ) from browser_error
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
        destination = Path(output_template.replace("%(ext)s", "mp4")).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix=".f2-douyin-", dir=destination.parent))
        args = [
            self._binary(),
            "dy",
            "--url",
            url,
            "--path",
            str(temp_root),
            "--mode",
            "one",
            "--naming",
            "{aweme_id}",
            *self._config_args(),
        ]
        timeout = float(self.options.get("download_timeout_sec") or 300)
        try:
            result = run(args, timeout=timeout)
            if result.returncode != 0:
                raise SourceError(result.stderr.strip()[-1000:] or "f2 Douyin download failed")
            videos = sorted(path for path in temp_root.rglob("*.mp4") if path.is_file())
            if not videos:
                raise SourceError("f2 completed without producing an mp4 file")
            selected = max(videos, key=lambda path: path.stat().st_size)
            shutil.move(str(selected), str(destination))
            destination.with_suffix(".info.json").write_text(
                json.dumps(
                    {
                        "webpage_url": url,
                        "downloader": "f2",
                        "source_service": self.api_base,
                        "command_status": "success",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except subprocess.TimeoutExpired as error:
            raise SourceError(f"douyin f2 download timed out after {error.timeout}s") from error
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


class XhsApiAdapter:
    """Self-hosted XHS-Downloader (JoeanAmier) in API-server mode.
    Search coverage depends on the deployed version; download works from
    explicit note URLs, which also enables the ingest workflow."""

    def __init__(self, platform: str, options: dict[str, Any] | None = None):
        self.platform = platform
        options = options or {}
        self.options = options
        self.api_base = str(options.get("api_base") or "http://127.0.0.1:5556").rstrip("/")

    def _scrape_config(self) -> dict[str, Any]:
        return {
            "_root": str(self.options.get("_root") or ""),
            "run": {"workspace": str(self.options.get("_workspace") or "workspace")},
        }

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
        try:
            from .browser_scraper import search_xiaohongshu

            return search_xiaohongshu(self._scrape_config(), term, limit)
        except Exception as error:
            raise SourceError(f"xiaohongshu browser search failed: {error}") from error

    def download(self, url: str, output_template: str) -> None:
        payload: Any = {}
        try:
            payload = self._post("/xhs/detail", {"url": url, "download": False})
        except Exception:
            payload = {}
        data = payload.get("data") or {}
        video_url = ""
        downloads = data.get("下载地址") or data.get("download_url") or []
        if isinstance(downloads, list) and downloads:
            video_url = str(downloads[0])
        elif isinstance(downloads, str):
            video_url = downloads
        if not video_url:
            try:
                from .browser_scraper import resolve_xiaohongshu_video

                data = {**data, **resolve_xiaohongshu_video(self._scrape_config(), url)}
                video_url = str(data.get("video_url") or "")
            except Exception as error:
                raise SourceError(
                    f"xhs service returned no downloadable URL; browser fallback failed: {error}"
                ) from error
        if not video_url:
            raise SourceError("xhs service returned no downloadable URL (note may be image-only)")
        destination = Path(output_template.replace("%(ext)s", "mp4"))
        http_download(video_url, destination, headers={"Referer": "https://www.xiaohongshu.com/"})
        destination.with_suffix(".info.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


ADAPTERS = {
    "youtube": YtDlpAdapter,
    "bilibili": YtDlpAdapter,
    "tiktok": YtDlpAdapter,
    "facebook": YtDlpAdapter,
    "x": YtDlpAdapter,
    "instagram": YtDlpAdapter,
    "kwai": YtDlpAdapter,
    "douyin": F2DouyinAdapter,
    "xiaohongshu": XhsApiAdapter,
}

SEARCHABLE_PLATFORMS = (
    "youtube", "bilibili", "douyin", "xiaohongshu", "tiktok", "facebook",
)


def get_adapter(platform: str, config: dict[str, Any]) -> Any:
    factory = ADAPTERS.get(platform)
    if not factory:
        raise SourceError(f"unsupported platform: {platform}")
    options = dict((config.get("sources", {}).get("adapters") or {}).get(platform) or {})
    run_config = config.get("run", {}) or {}
    options.setdefault("search_timeout_sec", run_config.get("source_search_timeout_sec", 90))
    options.setdefault("download_timeout_sec", run_config.get("source_download_timeout_sec", 300))
    options["_root"] = str(config.get("_root") or "")
    options["_workspace"] = str((config.get("run", {}) or {}).get("workspace") or "workspace")
    return factory(platform, options)
