from __future__ import annotations

import os
from pathlib import Path

import pytest

from jaguartv_factory import sources
from jaguartv_factory import core
from jaguartv_factory.core import yt_dlp_extra_args
from jaguartv_factory.pipeline import download as legacy_download
from jaguartv_factory.sources import SourceError, YtDlpAdapter


def test_yt_dlp_binary_prefers_explicit_environment_override(tmp_path: Path, monkeypatch):
    executable = tmp_path / "yt-dlp-custom"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("JAGUARTV_YTDLP_BINARY", str(executable))

    assert sources.yt_dlp_binary() == str(executable.resolve())


def test_yt_dlp_binary_rejects_invalid_environment_override(tmp_path: Path, monkeypatch):
    missing = tmp_path / "missing-yt-dlp"
    monkeypatch.setenv("JAGUARTV_YTDLP_BINARY", str(missing))

    with pytest.raises(SourceError, match="JAGUARTV_YTDLP_BINARY"):
        sources.yt_dlp_binary()


def test_tiktok_automatically_uses_impersonation_when_available(monkeypatch):
    adapter = YtDlpAdapter("tiktok", {"impersonate": "auto"})
    monkeypatch.setattr(adapter, "_js_runtime_args", lambda: [])
    monkeypatch.setattr(sources, "yt_dlp_supports_impersonation", lambda _binary: True)
    monkeypatch.setattr(sources, "yt_dlp_binary", lambda: "/opt/jaguartv/bin/yt-dlp")

    assert adapter._runtime_args() == ["--impersonate", "chrome"]


def test_youtube_does_not_force_automatic_impersonation(monkeypatch):
    adapter = YtDlpAdapter("youtube", {"impersonate": "auto"})
    monkeypatch.setattr(adapter, "_js_runtime_args", lambda: [])
    monkeypatch.setattr(sources, "yt_dlp_supports_impersonation", lambda _binary: True)

    assert adapter._runtime_args() == []


def test_explicit_impersonation_requires_supported_runtime(monkeypatch):
    adapter = YtDlpAdapter("youtube", {"impersonate": "safari"})
    monkeypatch.setattr(sources, "yt_dlp_supports_impersonation", lambda _binary: False)
    monkeypatch.setattr(sources, "yt_dlp_binary", lambda: "/opt/jaguartv/bin/yt-dlp")

    with pytest.raises(SourceError, match="curl-cffi"):
        adapter._impersonation_args()


def test_core_does_not_hide_explicit_impersonation_configuration_errors(monkeypatch):
    config = {
        "_root": "/tmp/project",
        "run": {"workspace": "workspace"},
        "sources": {"adapters": {"youtube": {"impersonate": "safari"}}},
    }
    monkeypatch.setattr(sources, "yt_dlp_supports_impersonation", lambda _binary: False)
    monkeypatch.setattr(sources, "yt_dlp_binary", lambda: "/opt/jaguartv/bin/yt-dlp")

    with pytest.raises(SourceError, match="curl-cffi"):
        yt_dlp_extra_args(config, "https://youtube.com/watch?v=test")


def test_yt_dlp_runtime_status_reports_capabilities(monkeypatch):
    class Result:
        def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def fake_run(args, *, timeout=None):
        if "--version" in args:
            return Result("2026.08.19\n")
        if "--list-extractors" in args:
            return Result("Youtube\nTikTok\nFacebook\n")
        if "--list-impersonate-targets" in args:
            return Result("Chrome-136 Macos-15 curl_cffi\n")
        raise AssertionError(args)

    monkeypatch.setattr(sources, "run", fake_run)
    monkeypatch.setattr(sources, "yt_dlp_binary", lambda: "/opt/jaguartv/bin/yt-dlp")
    monkeypatch.setattr(sources, "preferred_js_runtime", lambda: "node:/opt/node/bin/node")
    sources.yt_dlp_capabilities.cache_clear()

    status = sources.yt_dlp_runtime_status()

    assert status == {
        "ok": True,
        "path": "/opt/jaguartv/bin/yt-dlp",
        "version": "2026.08.19",
        "extractor_count": 3,
        "impersonation": True,
        "js_runtime": "node:/opt/node/bin/node",
    }


def test_yt_dlp_environment_override_does_not_accept_shell_arguments(monkeypatch):
    monkeypatch.setenv("JAGUARTV_YTDLP_BINARY", "/usr/bin/yt-dlp --verbose")

    with pytest.raises(SourceError, match="executable"):
        sources.yt_dlp_binary()


def test_yt_dlp_environment_override_allows_executable_path_with_spaces(tmp_path: Path, monkeypatch):
    executable = tmp_path / "tool directory" / "yt-dlp"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("JAGUARTV_YTDLP_BINARY", str(executable))

    assert sources.yt_dlp_binary() == str(executable.resolve())


def test_legacy_download_module_uses_canonical_download_service():
    assert legacy_download.download_candidate is core.download_candidate
    assert legacy_download.download_top is core.download_top


def teardown_module():
    sources.yt_dlp_capabilities.cache_clear()
    os.environ.pop("JAGUARTV_YTDLP_BINARY", None)
