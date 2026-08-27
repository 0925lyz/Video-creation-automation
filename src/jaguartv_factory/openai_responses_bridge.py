from __future__ import annotations

import hmac
import json
import re
import tempfile
import threading
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import requests


def responses_output_text(payload: dict[str, Any]) -> str:
    text = str(payload.get("output_text") or "").strip()
    if text:
        return text
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("text"):
                chunks.append(str(content["text"]))
    return "\n".join(chunks).strip()


def _responses_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/responses"):
        return normalized
    return f"{normalized}/responses"


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    chunks = []
    for part in content:
        if isinstance(part, dict) and part.get("text"):
            chunks.append(str(part["text"]))
    return "\n".join(chunks).strip()


def _responses_input(messages: Any) -> str:
    chunks: list[str] = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        message_text = _message_text(message)
        if message_text:
            chunks.append(f"{str(message.get('role') or 'user').upper()}:\n{message_text}")
    return "\n\n".join(chunks)


def _post_upstream(url: str, *, headers: dict[str, str], body: dict[str, Any], timeout: float):
    return requests.post(
        url,
        headers=headers,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )


class _BridgeHandler(BaseHTTPRequestHandler):
    server_version = "JaguarTVResponsesBridge/1.0"

    @property
    def bridge(self) -> "_BridgeServer":
        return self.server  # type: ignore[return-value]

    def _json_error(self, status: HTTPStatus, message: str) -> None:
        data = json.dumps({"error": {"message": message, "type": "responses_bridge_error"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._json_error(HTTPStatus.NOT_FOUND, "unsupported bridge route")
            return
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, self.bridge.api_key):
            self._json_error(HTTPStatus.UNAUTHORIZED, "invalid bridge authorization")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            prompt = _responses_input(request.get("messages"))
            if not prompt:
                raise ValueError("chat request contains no text")
            body = {
                "model": str(request.get("model") or self.bridge.model),
                "input": prompt,
                "store": False,
                "max_output_tokens": max(256, min(int(request.get("max_tokens") or 4096), 4096)),
            }
            response = _post_upstream(
                self.bridge.responses_url,
                headers={
                    "Authorization": f"Bearer {self.bridge.api_key}",
                    "Content-Type": "application/json",
                },
                body=body,
                timeout=self.bridge.timeout,
            )
            if response.status_code >= 400:
                self._json_error(HTTPStatus.BAD_GATEWAY, f"Responses API returned HTTP {response.status_code}")
                return
            content = responses_output_text(response.json())
            if not content:
                self._json_error(HTTPStatus.BAD_GATEWAY, "Responses API returned no output text")
                return
        except (ValueError, TypeError, json.JSONDecodeError, requests.RequestException) as error:
            self._json_error(HTTPStatus.BAD_GATEWAY, f"Responses bridge failed: {type(error).__name__}")
            return

        if request.get("stream"):
            chunks = [
                {
                    "id": "jaguartv-responses-bridge",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": content},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "jaguartv-responses-bridge",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            data = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks)
            data += "data: [DONE]\n\n"
            encoded = data.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return

        data = json.dumps(
            {
                "id": "jaguartv-responses-bridge",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class _BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float):
        super().__init__(("127.0.0.1", 0), _BridgeHandler)
        self.responses_url = _responses_url(base_url)
        self.api_key = api_key
        self.model = model
        self.timeout = timeout


def _rewrite_llm_base_url(source: str, base_url: str) -> str:
    lines = source.splitlines(keepends=True)
    in_llm = False
    replaced = False
    for index, line in enumerate(lines):
        section = re.match(r"^\s*\[([^]]+)]\s*$", line.strip())
        if section:
            in_llm = section.group(1) == "llm"
            continue
        if in_llm and re.match(r"^\s*base_url\s*=", line):
            ending = "\n" if line.endswith("\n") else ""
            lines[index] = f"base_url = {json.dumps(base_url)}{ending}"
            replaced = True
            break
    if not replaced:
        raise ValueError("KrillinAI [llm].base_url is missing")
    return "".join(lines)


@dataclass(frozen=True)
class BridgeRuntime:
    cwd: Path
    port: int


@contextmanager
def bridge_runtime(project: Path, log_dir: Path, *, timeout: float = 120.0) -> Iterator[BridgeRuntime]:
    source_config = project / "config" / "config.toml"
    raw = source_config.read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)
    llm = parsed.get("llm") or {}
    base_url = str(llm.get("base_url") or "").strip()
    api_key = str(llm.get("api_key") or "").strip()
    model = str(llm.get("model") or "").strip()
    if not base_url or not api_key or not model:
        raise ValueError("KrillinAI Responses bridge requires llm base_url, api_key, and model")

    log_dir.mkdir(parents=True, exist_ok=True)
    server = _BridgeServer(base_url=base_url, api_key=api_key, model=model, timeout=timeout)
    thread = threading.Thread(target=server.serve_forever, name="krillinai-responses-bridge", daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="jaguartv-krillinai-") as temporary:
            cwd = Path(temporary)
            for child in project.iterdir():
                if child.name in {".git", "config", "logs"}:
                    continue
                (cwd / child.name).symlink_to(child, target_is_directory=child.is_dir())
            config_dir = cwd / "config"
            config_dir.mkdir(mode=0o700)
            runtime_config = config_dir / "config.toml"
            runtime_config.write_text(
                _rewrite_llm_base_url(raw, f"http://127.0.0.1:{server.server_port}/v1"),
                encoding="utf-8",
            )
            runtime_config.chmod(0o600)
            yield BridgeRuntime(cwd=cwd, port=server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
