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
    monkeypatch.setattr(cli, "pyvideotrans_available", lambda _config: (False, "project_dir_not_found"))

    assert cli.doctor(tmp_path / "config" / "pipeline.yaml") == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ready"] is True
    assert payload["required"]["ffprobe"]["path"] == "/opt/jaguartv/bin/ffprobe"
    assert "tesseract" in payload["degraded"]
    assert "pyvideotrans" in payload["degraded"]
    assert "Core pipeline is ready" in payload["next_steps"]
