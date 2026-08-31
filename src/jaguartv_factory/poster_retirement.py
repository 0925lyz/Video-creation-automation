from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .core import connect_db, workspace_dir
from .posters import PosterNotFound, _managed_poster_path, poster_storage_root


PROTECTED_TABLES = (
    "candidates",
    "publications",
    "youtube_channel_auths",
    "x_account_auths",
    "source_imports",
)


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def _protected_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in PROTECTED_TABLES
        if table in tables
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _backup_database(source_path: Path, destination: Path) -> None:
    with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
        integrity = str(target.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise RuntimeError(f"poster retirement database backup failed integrity check: {integrity}")


def backup_and_clear_poster_inventory(
    config: dict[str, Any], backup_dir: Path
) -> dict[str, Any]:
    """Back up then hard-clear only the retired poster inventory and its managed files."""
    backup_dir = backup_dir.expanduser().resolve()
    if backup_dir.exists() and any(backup_dir.iterdir()):
        raise FileExistsError(f"backup directory is not empty: {backup_dir}")
    backup_dir.mkdir(parents=True, exist_ok=True)

    connection = connect_db(config)
    posters = _rows(connection, "posters")
    attachments = _rows(connection, "poster_attachments")
    audit_events = _rows(connection, "poster_audit_events")
    protected_before = _protected_counts(connection)
    database_path = workspace_dir(config) / "factory.db"
    _backup_database(database_path, backup_dir / "factory-before-poster-retirement.db")

    _write_json(backup_dir / "posters.json", posters)
    _write_json(backup_dir / "poster-attachments.json", attachments)
    _write_json(backup_dir / "poster-audit-events.json", audit_events)
    with (backup_dir / "posters.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("id", "title", "status", "tags", "created_at", "file_path"),
        )
        writer.writeheader()
        for row in posters:
            writer.writerow(
                {
                    "id": row.get("id", ""),
                    "title": row.get("name", ""),
                    "status": row.get("status", ""),
                    "tags": row.get("content_tags_json", "[]"),
                    "created_at": row.get("created_at", ""),
                    "file_path": row.get("file_key", ""),
                }
            )

    keys = sorted(
        {
            str(value)
            for value in (
                *(row.get("file_key") for row in posters),
                *(row.get("thumbnail_key") for row in posters),
                *(row.get("file_key") for row in attachments),
            )
            if value
        }
    )
    paths: list[tuple[str, Path]] = []
    media_lines: list[str] = []
    for key in keys:
        try:
            path = _managed_poster_path(config, key, must_exist=True, verify_image=False)
            paths.append((key, path))
            media_lines.append(f"PRESENT\t{key}")
        except PosterNotFound:
            media_lines.append(f"MISSING\t{key}")
    (backup_dir / "media-paths.txt").write_text(
        ("\n".join(media_lines) + ("\n" if media_lines else "")), encoding="utf-8"
    )
    if len(json.loads((backup_dir / "posters.json").read_text(encoding="utf-8"))) != len(posters):
        raise RuntimeError("poster JSON backup verification failed")

    root = poster_storage_root(config)
    quarantine = root / f".retirement-{uuid.uuid4().hex}"
    moved: list[tuple[Path, Path]] = []
    try:
        for index, (_, source) in enumerate(paths):
            quarantine.mkdir(parents=True, exist_ok=True)
            destination = quarantine / f"{index}-{source.name}"
            source.replace(destination)
            moved.append((source, destination))
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM poster_attachments")
        connection.execute("DELETE FROM poster_audit_events")
        connection.execute("DELETE FROM posters")
        protected_after = _protected_counts(connection)
        if protected_after != protected_before:
            raise RuntimeError("protected table counts changed during poster retirement")
        connection.commit()
    except Exception:
        connection.rollback()
        for source, quarantined in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            if quarantined.exists():
                quarantined.replace(source)
        if quarantine.exists():
            shutil.rmtree(quarantine, ignore_errors=True)
        raise

    cleanup_error = ""
    try:
        if quarantine.exists():
            shutil.rmtree(quarantine)
    except OSError as error:
        cleanup_error = str(error)
    protected_after = _protected_counts(connection)
    result = {
        "backup_directory": str(backup_dir),
        "records_backed_up": len(posters),
        "attachments_backed_up": len(attachments),
        "audit_events_backed_up": len(audit_events),
        "files_removed": len(moved),
        "missing_files": sum(line.startswith("MISSING") for line in media_lines),
        "remaining_posters": int(connection.execute("SELECT COUNT(*) FROM posters").fetchone()[0]),
        "protected_counts_before": protected_before,
        "protected_counts_after": protected_after,
        "protected_counts_unchanged": protected_before == protected_after,
        "cleanup_error": cleanup_error,
    }
    _write_json(backup_dir / "retirement-report.json", result)
    return result
