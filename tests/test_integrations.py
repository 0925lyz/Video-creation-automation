from pathlib import Path

import pytest
import yaml

from jaguartv_factory.integrations import (
    IntegrationError,
    integration_checkout,
    integration_status,
    load_integration_manifest,
    sync_integrations,
)


def test_repository_integration_manifest_is_valid():
    manifest = load_integration_manifest(Path("config/integrations.yaml"))

    assert "mediacrawler" in manifest["external_tools"]
    assert "google_trends" not in manifest["external_tools"]
    assert all(len(item["revision"]) == 40 for item in manifest["external_tools"].values())


def test_every_declared_agent_skill_exists_and_has_one_entrypoint():
    manifest = yaml.safe_load(Path("config/agent-skills.yaml").read_text(encoding="utf-8"))
    names = {
        name
        for group in manifest["skills"].values()
        for name in group
    }

    assert manifest["entrypoint"] == "jaguartv-content-factory"
    assert names == {
        path.parent.name for path in Path(".agents/skills").glob("*/SKILL.md")
    }
    assert all((Path(".agents/skills") / name / "SKILL.md").is_file() for name in names)


def test_checkout_is_restricted_to_runtime_workspace(tmp_path: Path):
    item = {"checkout": "workspace/external_tools/example"}

    assert integration_checkout(tmp_path, item) == tmp_path / "workspace/external_tools/example"
    with pytest.raises(IntegrationError):
        integration_checkout(tmp_path, {"checkout": "../outside"})


def test_status_does_not_require_external_repositories_to_be_installed(tmp_path: Path):
    manifest = {
        "external_tools": {
            "example": {
                "repository": "https://example.test/example.git",
                "revision": "a" * 40,
                "checkout": "workspace/external_tools/example",
                "license": "Apache-2.0",
                "workflow": "test",
            }
        }
    }

    rows = integration_status(tmp_path, manifest)

    assert rows == [{
        "name": "example",
        "repository": "https://example.test/example.git",
        "checkout": str(tmp_path / "workspace/external_tools/example"),
        "license": "Apache-2.0",
        "expected_revision": "a" * 40,
        "current_revision": "",
        "installed": False,
        "dirty": False,
        "ready": False,
        "workflow": "test",
        "error": "",
    }]


def test_sync_allows_initial_no_checkout_clone(tmp_path: Path, monkeypatch):
    revision = "b" * 40
    repository = "https://example.test/example.git"
    manifest = {
        "external_tools": {
            "example": {
                "repository": repository,
                "revision": revision,
                "checkout": "workspace/external_tools/example",
                "license": "Apache-2.0",
                "workflow": "test",
            }
        }
    }
    checked_out = False

    def fake_git(args, *, cwd=None, timeout=180, attempts=1):
        nonlocal checked_out
        if args[0] == "init":
            (Path(cwd) / ".git").mkdir(parents=True)
            return ""
        if args[:3] == ["remote", "add", "origin"]:
            return ""
        if args[:3] == ["remote", "get-url", "origin"]:
            return repository
        if args[0] == "checkout":
            checked_out = True
            return ""
        if args[:2] == ["rev-parse", "HEAD"]:
            return revision
        if args[:2] == ["status", "--porcelain"]:
            return "" if checked_out else " D file-present-in-index"
        return ""

    monkeypatch.setattr("jaguartv_factory.integrations._run_git", fake_git)

    rows = sync_integrations(tmp_path, manifest)

    assert checked_out is True
    assert rows[0]["ready"] is True
