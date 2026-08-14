#!/usr/bin/env python3
"""Export a reproducible, sanitized snapshot of the production SQLite database."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


URL_RE = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
ABSOLUTE_PATH_RE = re.compile(r"/(?:opt|home|Users|tmp|var)/[^\s\"']+")
REDACT_KEY_RE = re.compile(
    r"(cookie|token|secret|password|passwd|authorization|api[_-]?key|private[_-]?key|session|"
    r"(?:^|_)(?:title|description|caption|transcript|text|name|filename|path|url|source_id|"
    r"author|uploader|creator|publisher|account|note)(?:$|_))",
    re.IGNORECASE,
)
EXCLUDED_TABLES = ("hot_keywords", "trend_runs")


def token(kind: str, value: Any) -> str | None:
    if value is None:
        return None
    if value == "":
        return ""
    digest = hashlib.sha256(f"{kind}:{value}".encode("utf-8", errors="replace")).hexdigest()
    return f"{kind}_{digest[:16]}"


def sanitize_text(value: Any) -> str:
    text = str(value or "")
    text = URL_RE.sub("https://redacted.invalid/resource", text)
    text = ABSOLUTE_PATH_RE.sub("/redacted/path", text)
    return text[:2000]


def sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if REDACT_KEY_RE.search(str(key)):
                result[str(key)] = "<redacted>"
            else:
                result[str(key)] = sanitize_json(item)
        return result
    if isinstance(value, list):
        return [sanitize_json(item) for item in value[:200]]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def sanitize_json_column(value: Any) -> str:
    if not value:
        return "{}"
    try:
        return json.dumps(sanitize_json(json.loads(str(value))), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError, json.JSONDecodeError):
        return json.dumps({"message": sanitize_text(value)}, ensure_ascii=False)


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def update_rows(connection: sqlite3.Connection, table: str, updates: dict[str, Any]) -> None:
    if not table_exists(connection, table):
        return
    columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    selected = {name: transform for name, transform in updates.items() if name in columns}
    if not selected:
        return
    row_id = "rowid"
    query_columns = ", ".join(f'"{name}"' for name in selected)
    rows = connection.execute(f'SELECT {row_id}, {query_columns} FROM "{table}"').fetchall()
    for row in rows:
        values = []
        for index, transform in enumerate(selected.values(), start=1):
            values.append(transform(row[index]) if callable(transform) else transform)
        assignments = ", ".join(f'"{name}"=?' for name in selected)
        connection.execute(
            f'UPDATE "{table}" SET {assignments} WHERE {row_id}=?', (*values, row[0])
        )


def sanitize_database(source: Path, destination: Path) -> dict[str, int]:
    shutil.copy2(source, destination)
    connection = sqlite3.connect(destination)
    connection.execute("PRAGMA foreign_keys=OFF")
    for table in EXCLUDED_TABLES:
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')

    candidate_id = lambda value: token("candidate", value)
    source_id = lambda value: token("source", value)
    asset_id = lambda value: token("asset", value)
    person_id = lambda value: token("person", value)

    update_rows(
        connection,
        "candidates",
        {
            "id": candidate_id,
            "parent_id": candidate_id,
            "source_id": source_id,
            "url": lambda value: f"https://redacted.invalid/source/{source_id(value)}" if value else "",
            "title": lambda value: f"Sanitized candidate {token('title', value)[-8:]}" if value else "",
            "description": "",
            "metadata_json": "{}",
        },
    )
    update_rows(
        connection,
        "events",
        {"candidate_id": candidate_id, "payload_json": "{}"},
    )
    update_rows(
        connection,
        "seen_sources",
        {
            "source_key": source_id,
            "source_id": source_id,
            "url": lambda value: f"https://redacted.invalid/source/{source_id(value)}" if value else "",
            "first_candidate_id": candidate_id,
        },
    )
    update_rows(
        connection,
        "download_claims",
        {
            "candidate_id": candidate_id,
            "asset_id": asset_id,
            "filename": lambda value: f"{asset_id(value)}{Path(str(value or '')).suffix}" if value else "",
            "publisher": person_id,
            "note": "",
            "extra_data": "{}",
        },
    )
    update_rows(
        connection,
        "render_jobs",
        {
            "id": lambda value: token("render", value),
            "candidate_id": candidate_id,
            "output_path": lambda value: f"/redacted/output/{asset_id(value)}" if value else "",
            "cancel_file": lambda value: f"/redacted/cancel/{asset_id(value)}" if value else "",
            "error": "",
            "metadata_json": "{}",
        },
    )
    update_rows(
        connection,
        "workers",
        {
            "id": lambda value: token("worker", value),
            "name": lambda value: token("worker_name", value),
            "host": lambda value: token("host", value),
            "current_job": lambda value: token("job", value),
            "metadata_json": "{}",
        },
    )
    update_rows(
        connection,
        "publications",
        {
            "candidate_id": candidate_id,
            "account": person_id,
            "post_url": lambda value: "https://redacted.invalid/post" if value else "",
            "error": "",
        },
    )
    update_rows(
        connection,
        "callback_logs",
        {
            "candidate_id": candidate_id,
            "video_id": lambda value: token("video", value),
            "publisher": person_id,
            "extra_data": "{}",
        },
    )
    update_rows(
        connection,
        "conversion_events",
        {
            "candidate_id": candidate_id,
            "visitor_id": lambda value: token("visitor", value),
            "payload_json": "{}",
        },
    )
    update_rows(connection, "performance_snapshots", {"candidate_id": candidate_id})
    update_rows(
        connection,
        "feedback_actions",
        {"candidate_id": candidate_id, "keyword": "<redacted>", "reason": ""},
    )
    connection.commit()
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"sanitized database integrity check failed: {integrity}")
    table_counts = {
        row[0]: connection.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    }
    connection.execute("VACUUM")
    connection.close()
    for suffix in ("-shm", "-wal"):
        destination.with_name(destination.name + suffix).unlink(missing_ok=True)
    return table_counts


def export_schema(database: Path, output: Path) -> None:
    connection = sqlite3.connect(database)
    rows = connection.execute(
        """
        SELECT type, name, sql
        FROM sqlite_master
        WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
        ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name
        """
    ).fetchall()
    lines = ["-- Sanitized production schema snapshot.", "PRAGMA foreign_keys=OFF;", "BEGIN;"]
    lines.extend(f"{sql.rstrip(';')};" for _, _, sql in rows)
    lines.extend(["COMMIT;", ""])
    output.write_text("\n\n".join(lines), encoding="utf-8")
    connection.close()


def export_candidate_summary(database: Path, output: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    rows = connection.execute(
        """
        SELECT platform, status,
               CASE
                 WHEN duration IS NULL THEN 'unknown'
                 WHEN duration < 60 THEN '<60s'
                 WHEN duration <= 1800 THEN '60-1800s'
                 ELSE '>1800s'
               END AS duration_bucket,
               COUNT(*)
        FROM candidates
        GROUP BY platform, status, duration_bucket
        ORDER BY platform, status, duration_bucket
        """
    ).fetchall()
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["platform", "status", "duration_bucket", "count"])
        writer.writerows(rows)
    summary = {
        "total_candidates": sum(int(row[3]) for row in rows),
        "by_platform": dict(Counter(row[0] or "unknown" for row in connection.execute("SELECT platform FROM candidates"))),
        "by_status": dict(Counter(row[0] or "unknown" for row in connection.execute("SELECT status FROM candidates"))),
    }
    connection.close()
    return summary


def export_media_manifest(source: Path | None, output: Path) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    total_bytes = 0
    rows: list[list[Any]] = []
    if source and source.is_file():
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            relative_path, size_text, mtime_text = parts[0], parts[1], parts[2]
            suffix = Path(relative_path).suffix.lower() or "[no-extension]"
            try:
                size = int(size_text)
            except ValueError:
                continue
            try:
                mtime = datetime.fromtimestamp(float(mtime_text), timezone.utc).isoformat()
            except ValueError:
                mtime = ""
            category = relative_path.split("/", 1)[0] if "/" in relative_path else "workspace-root"
            rows.append([token("path", relative_path), category, suffix, size, mtime])
            counts[suffix] += 1
            total_bytes += size
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path_token", "category", "extension", "size_bytes", "mtime_utc"])
        writer.writerows(rows)
    media_extensions = {".mp4", ".mov", ".mkv", ".webm", ".wav", ".mp3"}
    return {
        "file_count": len(rows),
        "media_file_count": sum(counts[extension] for extension in media_extensions),
        "total_bytes": total_bytes,
        "by_extension": dict(counts),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--media-list", type=Path)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / "factory.sanitized.db"
    table_counts = sanitize_database(args.database, destination)
    export_schema(destination, args.output_dir / "schema.sql")
    candidate_summary = export_candidate_summary(destination, args.output_dir / "candidate-summary.csv")
    media_summary = export_media_manifest(args.media_list, args.output_dir / "media-inventory.csv")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_database_sha256": sha256_file(args.database),
        "sanitized_database_sha256": sha256_file(destination),
        "redaction": {
            "credentials": "removed",
            "cookies_and_sessions": "removed",
            "people": "deterministically pseudonymized",
            "candidate_ids_and_source_ids": "deterministically pseudonymized",
            "urls_and_absolute_paths": "replaced",
            "titles_and_descriptions": "replaced or removed",
            "freeform_metadata_and_error_text": "removed",
            "media_binaries": "not included; see anonymous inventory",
            "excluded_tables": list(EXCLUDED_TABLES),
        },
        "table_counts": table_counts,
        "candidate_summary": candidate_summary,
        "media_summary": media_summary,
    }
    (args.output_dir / "snapshot-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for suffix in ("-shm", "-wal"):
        destination.with_name(destination.name + suffix).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
