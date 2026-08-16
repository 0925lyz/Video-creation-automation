from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .browser_scraper import latest_cookie_file, resolve_xiaohongshu_video, search_douyin
from .core import load_config
from .sources import yt_dlp_binary


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


def _run(args: list[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, text=True, capture_output=True, timeout=timeout)


def resolve_douyin_video(config: dict[str, Any], url: str) -> dict[str, Any]:
    args = [
        yt_dlp_binary(),
        "--force-ipv4",
        "--no-playlist",
        "--no-warnings",
        "--get-url",
    ]
    cookie = latest_cookie_file(config, "douyin")
    if cookie:
        args.extend(["--cookies", str(cookie)])
    args.append(url)
    try:
        result = _run(args, timeout=120)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"yt-dlp douyin resolve timed out after {error.timeout}s") from error
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-1000:] or "yt-dlp douyin resolve failed")
    urls = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("http")]
    if not urls:
        raise RuntimeError("yt-dlp returned no douyin media URL")
    return {"video_data": {"nwm_video_url_HQ": urls[0]}, "video": {"play_addr": {"url_list": urls}}}


class SourceServiceHandler(BaseHTTPRequestHandler):
    server_version = "JaguarTVSourceService/1.0"

    def send_payload(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_error_payload(self, error: Exception, status: HTTPStatus = HTTPStatus.BAD_GATEWAY) -> None:
        self.send_payload({"status": "error", "message": str(error)}, status)

    @property
    def config(self) -> dict[str, Any]:
        return self.server.config  # type: ignore[attr-defined]

    @property
    def mode(self) -> str:
        return self.server.mode  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/health":
            return self.send_payload({"status": "ok", "mode": self.mode})
        if self.mode in {"douyin", "all"} and parsed.path == "/api/douyin/web/fetch_general_search_result":
            keyword = str((query.get("keyword") or [""])[0]).strip()
            count = int((query.get("count") or ["10"])[0] or 10)
            try:
                entries = search_douyin(self.config, keyword, max(1, min(count, 20)))
                items = []
                for entry in entries:
                    video_id = str(entry.get("id") or "")
                    items.append({
                        "aweme_info": {
                            "aweme_id": video_id,
                            "desc": entry.get("title") or entry.get("description") or "",
                            "create_time": entry.get("timestamp"),
                            "statistics": {
                                "play_count": int(entry.get("view_count") or 0),
                                "digg_count": int(entry.get("like_count") or 0),
                                "comment_count": int(entry.get("comment_count") or 0),
                                "share_count": int(entry.get("repost_count") or 0),
                            },
                            "video": {
                                "duration": int(float(entry.get("duration") or 0) * 1000),
                                "cover": {"url_list": [entry.get("thumbnail") or ""]},
                            },
                        }
                    })
                return self.send_payload({"status": "success", "data": {"data": items}})
            except Exception as error:
                return self.send_error_payload(error)
        if self.mode in {"douyin", "all"} and parsed.path == "/api/hybrid/video_data":
            url = str((query.get("url") or [""])[0]).strip()
            try:
                return self.send_payload({"status": "success", "data": resolve_douyin_video(self.config, url)})
            except Exception as error:
                return self.send_error_payload(error)
        return self.send_payload({"status": "error", "message": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if self.mode in {"xhs", "all"} and parsed.path == "/xhs/detail":
            url = str(payload.get("url") or "").strip()
            try:
                detail = resolve_xiaohongshu_video(self.config, url)
                return self.send_payload({
                    "status": "success",
                    "data": {
                        "作品标题": detail.get("title") or "",
                        "作品描述": detail.get("description") or "",
                        "作品ID": Path(urllib.parse.urlparse(url).path).name,
                        "duration": detail.get("duration"),
                        "下载地址": [detail.get("video_url")],
                        "video_url": detail.get("video_url"),
                    },
                })
            except Exception as error:
                return self.send_error_payload(error)
        return self.send_payload({"status": "error", "message": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def serve(config_path: Path, host: str, port: int, mode: str) -> None:
    config = load_config(config_path)
    server = ThreadingHTTPServer((host, port), SourceServiceHandler)
    server.config = config  # type: ignore[attr-defined]
    server.mode = mode  # type: ignore[attr-defined]
    print(f"JaguarTV source service mode={mode} listening on {host}:{port}", flush=True)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JaguarTV local source helper service")
    parser.add_argument("--config", default="config/pipeline.yaml", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5556, type=int)
    parser.add_argument("--mode", choices=("xhs", "douyin", "all"), default="all")
    args = parser.parse_args(argv)
    serve(args.config, args.host, args.port, args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
