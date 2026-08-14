#!/usr/bin/env python3
"""Server-side batch producer for missing JaguarTV inventory outputs.

This script is intentionally operational and resumable: it re-checks output
files before each candidate, writes JSONL progress, and uses a lock file so two
batch producers do not render the same inventory at the same time.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import shutil
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

from jaguartv_factory.core import (
    candidate_has_review_outputs,
    connect_db,
    download_candidate,
    load_config,
    now_iso,
    produce_top,
    workspace_dir,
)


PROCESS_STATUSES = {
    "DISCOVERED",
    "DOWNLOAD_FAILED",
    "DOWNLOADED",
    "PRODUCTION_FAILED",
    "READY_FOR_REVIEW",
    "REVISION_REQUIRED",
    "BLOCKED_RIGHTS",
}
SKIP_STATUSES = {"TOO_LONG", "APPROVED", "SKIPPED", "LANGUAGE_REJECTED"}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov"}
PRODUCE_OPTIONS = {
    "content_type": "auto",
    "segment_strategy": "auto",
    "audio_policy": "source_plus_funk",
    "rights_status": "MANUAL_REVIEW",
    "batch_label": "",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/pipeline.yaml")
    parser.add_argument("--limit", type=int, default=0, help="maximum candidates to process; 0 means all")
    parser.add_argument(
        "--candidate-id",
        action="append",
        default=[],
        help="specific parent candidate to process; repeat for multiple candidates",
    )
    parser.add_argument("--min-free-gb", type=float, default=20.0)
    parser.add_argument("--include-child-rows", action="store_true", help="also process slice child rows")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def json_default(value: Any) -> str:
    return str(value)


def write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    payload = {"time": now_iso(), **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=json_default) + "\n")
    print(json.dumps(payload, ensure_ascii=False, default=json_default), flush=True)


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps({"updated_at": now_iso(), **payload}, ensure_ascii=False, indent=2, default=json_default),
        encoding="utf-8",
    )


def source_media_exists(config: dict[str, Any], candidate_id: str) -> bool:
    work = workspace_dir(config) / "jobs" / candidate_id
    return any(path.suffix.lower() in VIDEO_SUFFIXES for path in work.glob("source.*"))


def free_gb(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024 ** 3)


def row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def candidate_rows(
    config: dict[str, Any],
    include_child_rows: bool,
    limit: int,
    candidate_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    connection = connect_db(config)
    requested_ids = [str(value).strip() for value in (candidate_ids or []) if str(value).strip()]
    if requested_ids:
        rows = [
            row for candidate_id in dict.fromkeys(requested_ids)
            if (row := connection.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone())
        ]
    else:
        rows = connection.execute(
            "SELECT * FROM candidates ORDER BY created_at DESC"
        ).fetchall()
    plan: list[dict[str, Any]] = []
    for row in rows:
        item = row_dict(row)
        status = str(item.get("status") or "")
        if status in SKIP_STATUSES:
            continue
        if status not in PROCESS_STATUSES:
            continue
        if item.get("parent_id") and not include_child_rows:
            continue
        if candidate_has_review_outputs(config, str(item["id"])):
            continue
        plan.append(item)
        if limit and len(plan) >= limit:
            break
    return plan


def current_candidate(config: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    return row_dict(row) if row else None


def run() -> int:
    args = parse_args()
    config = load_config(args.config)
    workspace = workspace_dir(config)
    log_path = workspace / "produce_missing_inventory.jsonl"
    status_path = workspace / "produce_missing_inventory_status.json"
    lock_path = workspace / "produce_missing_inventory.lock"

    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            write_jsonl(log_path, {"event": "already_running", "lock": str(lock_path)})
            return 2

        plan = candidate_rows(config, args.include_child_rows, args.limit, args.candidate_id)
        counts = Counter(item["status"] for item in plan)
        write_jsonl(
            log_path,
            {
                "event": "start",
                "total": len(plan),
                "status_counts": dict(counts),
                "dry_run": args.dry_run,
                "include_child_rows": args.include_child_rows,
                "candidate_ids": args.candidate_id,
                "min_free_gb": args.min_free_gb,
            },
        )
        write_status(
            status_path,
            {
                "status": "DRY_RUN" if args.dry_run else "RUNNING",
                "total": len(plan),
                "completed": 0,
                "produced": 0,
                "failed": 0,
                "skipped": 0,
                "current_candidate": "",
                "status_counts": dict(counts),
            },
        )
        if args.dry_run:
            return 0

        totals = {"completed": 0, "produced": 0, "failed": 0, "skipped": 0}
        started = time.time()
        for index, planned in enumerate(plan, start=1):
            candidate_id = str(planned["id"])
            status_payload = {
                "status": "RUNNING",
                "total": len(plan),
                **totals,
                "current_candidate": candidate_id,
                "position": index,
                "elapsed_sec": round(time.time() - started, 1),
                "free_gb": round(free_gb(workspace), 2),
            }
            write_status(status_path, status_payload)

            if free_gb(workspace) < args.min_free_gb:
                write_jsonl(
                    log_path,
                    {
                        "event": "stopped_low_disk",
                        "candidate_id": candidate_id,
                        "free_gb": round(free_gb(workspace), 2),
                        "min_free_gb": args.min_free_gb,
                    },
                )
                write_status(status_path, {**status_payload, "status": "STOPPED_LOW_DISK"})
                return 3

            row = current_candidate(config, candidate_id)
            if not row:
                totals["skipped"] += 1
                write_jsonl(log_path, {"event": "skip_missing_row", "candidate_id": candidate_id})
                continue
            if row.get("status") in SKIP_STATUSES:
                totals["skipped"] += 1
                write_jsonl(log_path, {"event": "skip_status", "candidate_id": candidate_id, "status": row.get("status")})
                continue
            if row.get("parent_id") and not args.include_child_rows:
                totals["skipped"] += 1
                write_jsonl(log_path, {"event": "skip_child_row", "candidate_id": candidate_id, "parent_id": row.get("parent_id")})
                continue
            if candidate_has_review_outputs(config, candidate_id):
                totals["skipped"] += 1
                write_jsonl(log_path, {"event": "skip_has_outputs", "candidate_id": candidate_id})
                continue

            write_jsonl(log_path, {"event": "candidate_start", "candidate_id": candidate_id, "position": index, "total": len(plan)})
            try:
                if not source_media_exists(config, candidate_id):
                    write_jsonl(log_path, {"event": "download_start", "candidate_id": candidate_id})
                    download_candidate(config, row)
                    write_jsonl(log_path, {"event": "download_done", "candidate_id": candidate_id})

                def progress(percent: int, message: str) -> None:
                    write_status(
                        status_path,
                        {
                            "status": "RUNNING",
                            "total": len(plan),
                            **totals,
                            "current_candidate": candidate_id,
                            "position": index,
                            "progress_message": message,
                            "render_progress": percent,
                            "elapsed_sec": round(time.time() - started, 1),
                            "free_gb": round(free_gb(workspace), 2),
                        },
                    )

                result = produce_top(config, 1, candidate_id, progress_callback=progress, options=PRODUCE_OPTIONS)
                if int(result.get("produced", 0)) > 0 or candidate_has_review_outputs(config, candidate_id):
                    totals["produced"] += 1
                    write_jsonl(log_path, {"event": "candidate_done", "candidate_id": candidate_id, "result": result})
                else:
                    totals["failed"] += 1
                    write_jsonl(log_path, {"event": "candidate_failed", "candidate_id": candidate_id, "result": result})
            except Exception as error:
                totals["failed"] += 1
                write_jsonl(
                    log_path,
                    {
                        "event": "candidate_error",
                        "candidate_id": candidate_id,
                        "error": str(error),
                        "traceback": traceback.format_exc()[-4000:],
                    },
                )
            finally:
                totals["completed"] += 1
                write_status(
                    status_path,
                    {
                        "status": "RUNNING",
                        "total": len(plan),
                        **totals,
                        "current_candidate": "",
                        "position": index,
                        "elapsed_sec": round(time.time() - started, 1),
                        "free_gb": round(free_gb(workspace), 2),
                    },
                )

        write_jsonl(log_path, {"event": "finish", "total": len(plan), **totals})
        write_status(
            status_path,
            {
                "status": "COMPLETED",
                "total": len(plan),
                **totals,
                "current_candidate": "",
                "elapsed_sec": round(time.time() - started, 1),
                "free_gb": round(free_gb(workspace), 2),
            },
        )
        return 0


if __name__ == "__main__":
    sys.exit(run())
