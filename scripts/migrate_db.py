#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from jaguartv_factory.core import connect_db
from jaguartv_factory.youtube_analytics import rollback_youtube_analytics_schema


def database_config(db_path: Path) -> dict:
    resolved = db_path.expanduser().resolve()
    if resolved.name != "factory.db":
        raise ValueError("the shared database must be named factory.db")
    return {
        "_root": str(resolved.parent),
        "run": {"workspace": ".", "sqlite_timeout_sec": 30},
    }


def backup_database(source: Path, destination: Path) -> None:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_connection, sqlite3.connect(destination) as backup_connection:
        source_connection.backup(backup_connection)
        result = backup_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"backup integrity check failed: {result}")


def migrate(db_path: Path, *, backup_path: Path | None = None) -> dict:
    db_path = db_path.expanduser().resolve()
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    if backup_path:
        backup_database(db_path, backup_path)
    connection = connect_db(database_config(db_path))
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    required = {
        "publications",
        "youtube_channel_auths",
        "youtube_metric_snapshots",
        "youtube_sync_states",
        "youtube_backfill_runs",
        "youtube_channel_import_states",
        "youtube_channel_video_imports",
        "source_imports",
        "source_import_events",
        "production_runs",
        "production_slices",
        "production_outputs",
        "cta_assets",
        "repair_runs",
        "original_factory_items",
        "original_factory_social_sources",
        "original_factory_audit_events",
        "original_factory_copy_generations",
    }
    missing = sorted(required - tables)
    connection.close()
    if integrity != "ok" or missing:
        raise RuntimeError(f"migration verification failed: integrity={integrity}, missing={missing}")
    return {
        "database": str(db_path),
        "backup": str(backup_path.expanduser().resolve()) if backup_path else None,
        "integrity": integrity,
        "required_tables": sorted(required),
        "rollback": (
            "restore the --backup SQLite snapshot for a full rollback, or deploy the previous "
            "code while retaining the additive source import tables"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the shared JaguarTV factory.db in place")
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument(
        "--verify-rollback",
        action="store_true",
        help="verify the non-destructive code rollback path without dropping analytics snapshots",
    )
    args = parser.parse_args()
    report = migrate(args.db_path, backup_path=args.backup)
    if args.verify_rollback:
        rollback_youtube_analytics_schema(database_config(args.db_path))
        report["rollback_verified"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
