from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

import yaml


class IntegrationError(RuntimeError):
    """Raised when an external integration cannot be synchronized safely."""


def load_integration_manifest(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise IntegrationError(f"integration manifest not found: {resolved}")
    data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1 or not isinstance(data.get("external_tools"), dict):
        raise IntegrationError("integration manifest must contain version 1 and external_tools")
    for name, item in data["external_tools"].items():
        if not isinstance(item, dict):
            raise IntegrationError(f"integration {name} must be a mapping")
        missing = [field for field in ("repository", "revision", "checkout", "license") if not item.get(field)]
        if missing:
            raise IntegrationError(f"integration {name} is missing: {', '.join(missing)}")
    data["_path"] = str(resolved)
    return data


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 180,
    attempts: int = 1,
) -> str:
    last_error = "git command failed"
    for attempt in range(max(1, attempts)):
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            last_error = f"git {' '.join(args[:2])} timed out after {error.timeout}s"
        else:
            if result.returncode == 0:
                return result.stdout.strip()
            last_error = (result.stderr or result.stdout or "git command failed").strip()[-1200:]
        if attempt + 1 < attempts:
            time.sleep(min(2 ** attempt, 4))
    raise IntegrationError(last_error)


def integration_checkout(project_root: Path, item: dict[str, Any]) -> Path:
    root = project_root.expanduser().resolve()
    external_root = (root / "workspace" / "external_tools").resolve()
    configured = Path(str(item["checkout"])).expanduser()
    destination = configured.resolve() if configured.is_absolute() else (root / configured).resolve()
    if destination != external_root and external_root not in destination.parents:
        raise IntegrationError(f"checkout must stay under {external_root}: {destination}")
    return destination


def _selected_tools(manifest: dict[str, Any], names: Iterable[str] | None) -> list[tuple[str, dict[str, Any]]]:
    tools = manifest["external_tools"]
    requested = list(dict.fromkeys(names or tools.keys()))
    unknown = sorted(set(requested) - set(tools))
    if unknown:
        raise IntegrationError(f"unknown integrations: {', '.join(unknown)}")
    return [(name, tools[name]) for name in requested]


def integration_status(
    project_root: Path,
    manifest: dict[str, Any],
    names: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, item in _selected_tools(manifest, names):
        destination = integration_checkout(project_root, item)
        installed = (destination / ".git").is_dir()
        current_revision = ""
        dirty = False
        error = ""
        if installed:
            try:
                current_revision = _run_git(["rev-parse", "HEAD"], cwd=destination, timeout=20)
                dirty = bool(_run_git(["status", "--porcelain"], cwd=destination, timeout=20))
            except IntegrationError as status_error:
                error = str(status_error)
        rows.append({
            "name": name,
            "repository": str(item["repository"]),
            "checkout": str(destination),
            "license": str(item["license"]),
            "expected_revision": str(item["revision"]),
            "current_revision": current_revision,
            "installed": installed,
            "dirty": dirty,
            "ready": installed and not dirty and current_revision == str(item["revision"]) and not error,
            "workflow": str(item.get("workflow") or ""),
            "error": error,
        })
    return rows


def sync_integrations(
    project_root: Path,
    manifest: dict[str, Any],
    names: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    for name, item in _selected_tools(manifest, names):
        destination = integration_checkout(project_root, item)
        repository = str(item["repository"])
        revision = str(item["revision"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        created = False
        if destination.exists() and not (destination / ".git").is_dir():
            raise IntegrationError(f"refusing to replace non-git path for {name}: {destination}")
        if not destination.exists():
            destination.mkdir(parents=True)
            _run_git(["init"], cwd=destination, timeout=20)
            _run_git(["remote", "add", "origin", repository], cwd=destination, timeout=20)
            created = True
        origin = _run_git(["remote", "get-url", "origin"], cwd=destination, timeout=20)
        if origin.removesuffix(".git") != repository.removesuffix(".git"):
            raise IntegrationError(f"origin mismatch for {name}: {origin}")
        if not created and _run_git(["status", "--porcelain"], cwd=destination, timeout=20):
            raise IntegrationError(f"refusing to overwrite dirty integration checkout: {destination}")
        _run_git(["fetch", "--depth=1", "origin", revision], cwd=destination, attempts=3)
        _run_git(["checkout", "--detach", revision], cwd=destination, timeout=300)
    return integration_status(project_root, manifest, names)
