#!/usr/bin/env python3
"""Verify the server archive without contacting production."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path


FORBIDDEN_NAMES = {
    ".env",
    "cookies.txt",
    "storage_state.json",
}
FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".mp4", ".mov", ".mkv", ".webm"}
SECRET_PATTERNS = [
    re.compile(r"JAGUARTV_UPLOAD_TOKEN\s*=\s*[\"']?[A-Za-z0-9_-]{32,}", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key|secret|password|authorization)\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{20,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
SECRET_REFERENCE_PATTERN = re.compile(r"credential_ref\s*:\s*secret://", re.IGNORECASE)
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path, nargs="?", default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    archive = args.archive.resolve()
    problems: list[str] = []

    for path in archive.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(archive)
        if any(part in IGNORED_DIRECTORY_NAMES for part in relative.parts):
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.startswith("._"):
            problems.append(f"forbidden file: {relative}")
            continue
        if path.stat().st_size > 25 * 1024 * 1024:
            problems.append(f"archive file exceeds 25 MiB: {relative}")
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".db"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in SECRET_PATTERNS:
            filtered_text = "\n".join(
                line for line in text.splitlines()
                if not SECRET_REFERENCE_PATTERN.search(line)
            )
            if pattern.search(filtered_text):
                problems.append(f"possible secret in {relative}: {pattern.pattern}")

    database = archive / "data" / "factory.sanitized.db"
    if not database.is_file():
        problems.append("missing data/factory.sanitized.db")
    else:
        connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        connection.close()
        if integrity != "ok":
            problems.append(f"database integrity check failed: {integrity}")

    manifest = archive / "archive-manifest.json"
    if not manifest.is_file():
        problems.append("missing archive-manifest.json")
    else:
        json.loads(manifest.read_text(encoding="utf-8"))

    if problems:
        raise SystemExit("Archive verification failed:\n- " + "\n- ".join(problems))
    print(f"Archive verification passed: {archive}")


if __name__ == "__main__":
    main()
