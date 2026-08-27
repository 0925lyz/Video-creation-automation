from __future__ import annotations

import json
import types
from pathlib import Path

from jaguartv_factory import binaries, cli


def test_require_binary_finds_static_ffprobe(tmp_path: Path, monkeypatch):
    ffprobe = tmp_path / "node_modules" / "ffprobe-static" / "bin" / "darwin" / "arm64" / "ffprobe"
    ffprobe.parent.mkdir(parents=True)
    ffprobe.write_text("#!/bin/sh\n", encoding="utf-8")
    ffprobe.chmod(0o755)

    monkeypatch.setattr(binaries, "ROOT", tmp_path)
    monkeypatch.setattr(binaries.sys, "platform", "darwin")
    monkeypatch.setattr(binaries.os, "uname", lambda: types.SimpleNamespace(machine="arm64"))
    monkeypatch.setattr(binaries.shutil, "which", lambda _name: None)
    monkeypatch.setattr(binaries.sys, "executable", str(tmp_path / ".venv" / "bin" / "python"))

    assert binaries.require_binary("ffprobe") == str(ffprobe)


def test_doctor_ready_with_optional_degraded_tools(tmp_path: Path, monkeypatch, capsys):
    def fake_require_binary(name: str) -> str:
        if name in {"yt-dlp", "ffmpeg", "ffprobe"}:
            return f"/opt/jaguartv/bin/{name}"
        raise RuntimeError(f"Missing required binary: {name}")

    def fake_which(name: str) -> str | None:
        if name == "python3":
            return "/usr/bin/python3"
        if name == "say":
            return "/usr/bin/say"
        return None

    monkeypatch.setattr(cli, "require_binary", fake_require_binary)
    monkeypatch.setattr(cli.shutil, "which", fake_which)
    monkeypatch.setattr(cli, "load_config", lambda _path: {"_root": str(tmp_path)})
    monkeypatch.setattr(cli, "krillinai_available", lambda _config: (True, "/opt/krillinai/build/krillinai-cli"))
    monkeypatch.setattr(cli, "yt_dlp_runtime_status", lambda: {
        "ok": True,
        "path": "/opt/jaguartv/bin/yt-dlp",
        "version": "2026.08.19",
        "extractor_count": 1752,
        "impersonation": True,
        "js_runtime": "node:/opt/node/bin/node",
    })
    monkeypatch.setattr(cli, "f2_runtime_status", lambda _options=None: {
        "ok": True,
        "path": "/opt/jaguartv/f2/bin/f2",
        "version": "0.0.1.7",
        "apps": {"douyin": {"ok": True}, "tiktok": {"ok": False, "error": "msToken failed"}},
    })

    assert cli.doctor(tmp_path / "config" / "pipeline.yaml") == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ready"] is True
    assert payload["required"]["ffprobe"]["path"] == "/opt/jaguartv/bin/ffprobe"
    assert payload["yt_dlp"]["extractor_count"] == 1752
    assert payload["yt_dlp"]["impersonation"] is True
    assert payload["f2"]["apps"]["douyin"]["ok"] is True
    assert payload["required"]["krillinai"]["ok"] is True
    assert "node" in payload["degraded"]
    assert "Core pipeline is ready" in payload["next_steps"]


def test_ytdlp_status_command_does_not_require_pipeline_config(monkeypatch, capsys):
    monkeypatch.setattr(cli, "yt_dlp_runtime_status", lambda: {
        "ok": True,
        "path": "/opt/jaguartv/bin/yt-dlp",
        "version": "2026.08.19",
        "extractor_count": 1752,
        "impersonation": True,
        "js_runtime": "node:/opt/node/bin/node",
    })

    assert cli.main(["--config", "/missing/pipeline.yaml", "ytdlp-status"]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == "2026.08.19"


def test_f2_status_command_reports_per_app_health(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda _path: {
        "_root": str(tmp_path),
        "sources": {"adapters": {"douyin": {"binary": "workspace/tool_venvs/f2/bin/f2"}}},
    })
    monkeypatch.setattr(cli, "f2_runtime_status", lambda options=None: {
        "ok": True,
        "path": str(tmp_path / "workspace" / "tool_venvs" / "f2" / "bin" / "f2"),
        "version": "0.0.1.7",
        "apps": {"douyin": {"ok": True}, "tiktok": {"ok": False, "error": "msToken failed"}},
    })

    assert cli.main(["--config", str(tmp_path / "pipeline.yaml"), "f2-status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["apps"]["douyin"]["ok"] is True
    assert payload["apps"]["tiktok"]["ok"] is False
