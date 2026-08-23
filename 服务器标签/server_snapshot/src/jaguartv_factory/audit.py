from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .core import connect_db, now_iso, require_binary, run_command
from .production import CandidateProductionService


VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov"}


def _configured_path(config: dict[str, Any], value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(str(config["_root"])) / path
    return path.resolve()


def _workspace_path(config: dict[str, Any]) -> Path:
    return _configured_path(config, str((config.get("run", {}) or {}).get("workspace") or "workspace"))


def _storage_path(config: dict[str, Any]) -> Path:
    return _configured_path(config, str((config.get("storage", {}) or {}).get("root") or "workspace/server_media"))


def _readonly_connection(config: dict[str, Any]) -> sqlite3.Connection:
    database = _workspace_path(config) / "factory.db"
    if not database.is_file():
        raise FileNotFoundError(database)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def probe_media(path: Path) -> dict[str, Any]:
    try:
        result = run_command([
            require_binary("ffprobe"), "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate:format=duration,size",
            "-of", "json", str(path),
        ])
        payload = json.loads(result.stdout)
        streams = payload.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        rate = str(video.get("r_frame_rate") or "0/1")
        numerator, denominator = (rate.split("/", 1) + ["1"])[:2]
        fps = float(numerator) / max(1.0, float(denominator))
        return {
            "playable": bool(video),
            "has_video": bool(video),
            "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
            "duration": float((payload.get("format") or {}).get("duration") or 0),
            "size": int((payload.get("format") or {}).get("size") or path.stat().st_size),
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "fps": fps,
        }
    except Exception as error:
        return {"playable": False, "error": str(error)}


def _metadata_for_package(config: dict[str, Any], candidate_id: str, candidate_meta: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    roots = [_workspace_path(config) / "ready_for_review", _storage_path(config) / "review"]
    for root in roots:
        package = root / candidate_id
        metadata_path = package / "metadata.json"
        if metadata_path.is_file():
            try:
                return json.loads(metadata_path.read_text(encoding="utf-8")), package
            except json.JSONDecodeError:
                return candidate_meta, package
    return candidate_meta, None


def _reason(code: str, detail: str, *, confirmed: bool = True) -> dict[str, Any]:
    return {"code": code, "detail": detail, "confirmed": confirmed}


def audit_review_inventory(
    config: dict[str, Any],
    *,
    status: str = "READY_FOR_REVIEW",
    limit: int = 0,
    dry_run: bool = True,
) -> dict[str, Any]:
    if not dry_run:
        raise ValueError("audit_review_inventory is read-only; use the repair command for confirmed records")
    connection = _readonly_connection(config)
    rows = connection.execute(
        "SELECT * FROM candidates WHERE status=? ORDER BY updated_at DESC",
        (status,),
    ).fetchall()
    records: list[dict[str, Any]] = []
    scanned = 0
    for row in rows:
        if limit and scanned >= limit:
            break
        scanned += 1
        try:
            candidate_meta = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            candidate_meta = {}
        metadata, package = _metadata_for_package(config, row["id"], candidate_meta)
        reasons: list[dict[str, Any]] = []
        strategy = str(metadata.get("segment_strategy") or (metadata.get("strategy") or {}).get("segment_strategy") or "")
        render_engine = str(metadata.get("render_engine") or "")
        variants = metadata.get("output_variants") or (metadata.get("outputs") or {}).get("variants") or []
        variants = [item for item in variants if isinstance(item, dict)]
        variant_names = {str(item.get("variant") or "") for item in variants}
        segment = metadata.get("segment") if isinstance(metadata.get("segment"), dict) else {}
        production_contract = str(metadata.get("production_contract") or "")
        production_run_id = str(metadata.get("production_run_id") or "")
        mode_text = " ".join([
            strategy,
            render_engine,
            *(str((item.get("mobile_format") or {}).get("mode") or "") for item in variants),
        ]).lower()
        if "passthrough" in mode_text or "原视频" in variant_names:
            reasons.append(_reason("PASSTHROUGH_ORIGINAL", mode_text or "original variant"))
        if not segment.get("slice_id") and not (
            segment.get("start_sec") is not None and segment.get("end_sec") is not None
        ):
            reasons.append(_reason("MISSING_SLICE_RECORD", "slice id or source time range is absent"))
        if variant_names != {"通用版", "FB版"}:
            reasons.append(_reason("MISSING_VARIANT_PAIR", f"variants={sorted(variant_names)}"))
        source = next(
            (
                path.resolve()
                for path in sorted((_workspace_path(config) / "jobs" / str(row["parent_id"] or row["id"])).glob("source.*"))
                if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
            ),
            None,
        )
        source_hash = hash_file(source) if source else ""
        source_media = probe_media(source) if source else {}
        for item in variants:
            path = Path(str(item.get("path") or ""))
            if not path.is_file() and package:
                path = package / str(item.get("filename") or path.name)
            if not path.is_file():
                reasons.append(_reason("MISSING_OUTPUT_FILE", str(path)))
                continue
            if source:
                if path.resolve() == source or os.path.samefile(path, source):
                    reasons.append(_reason("OUTPUT_MATCHES_SOURCE_PATH", str(path)))
                elif hash_file(path) == source_hash:
                    reasons.append(_reason("OUTPUT_MATCHES_SOURCE_HASH", str(path)))
            media = probe_media(path)
            if not media.get("playable") or not media.get("has_video") or not media.get("has_audio"):
                reasons.append(_reason("MEDIA_QA_FAILED", json.dumps(media, ensure_ascii=False)))
            if source_media.get("playable") and media.get("playable"):
                duration_delta = abs(float(media.get("duration") or 0) - float(source_media.get("duration") or 0))
                size_ratio = float(media.get("size") or 0) / max(1.0, float(source_media.get("size") or 0))
                same_geometry = (
                    int(media.get("width") or 0) == int(source_media.get("width") or 0)
                    and int(media.get("height") or 0) == int(source_media.get("height") or 0)
                )
                if duration_delta <= 0.15 and 0.98 <= size_ratio <= 1.02 and same_geometry:
                    reasons.append(_reason(
                        "SOURCE_OUTPUT_HIGH_SIMILARITY",
                        f"duration_delta={duration_delta:.3f},size_ratio={size_ratio:.4f},same_geometry=true",
                        confirmed=False,
                    ))
            render_job_id = str(item.get("render_job_id") or "")
            render = connection.execute(
                "SELECT status FROM render_jobs WHERE id=?", (render_job_id,)
            ).fetchone() if render_job_id else None
            if not render or render["status"] != "COMPLETED":
                reasons.append(_reason("MISSING_RENDER_RECORD", render_job_id or "not recorded", confirmed=False))
            if item.get("variant") == "通用版" and int(item.get("endcard_count") or 0) != 1:
                reasons.append(_reason("ENDCARD_NOT_VERIFIED", "generic endcard count is not exactly one", confirmed=False))
        if production_contract == "candidate-production-v1":
            run = connection.execute(
                "SELECT status FROM production_runs WHERE id=? AND candidate_id=?",
                (production_run_id, str(row["parent_id"] or row["id"])),
            ).fetchone() if production_run_id else None
            if not run or run["status"] != "SUCCEEDED":
                reasons.append(_reason("MISSING_STANDARD_PRODUCTION_RUN", production_run_id or "not recorded"))
            persisted_slice = connection.execute(
                "SELECT status FROM production_slices WHERE id=?",
                (str(segment.get("slice_id") or ""),),
            ).fetchone() if segment.get("slice_id") else None
            if not persisted_slice or persisted_slice["status"] != "COMPLETED":
                reasons.append(_reason("MISSING_PERSISTED_SLICE", str(segment.get("slice_id") or "not recorded")))
            persisted_variants = {
                str(output["variant"])
                for output in connection.execute(
                    "SELECT variant FROM production_outputs WHERE slice_id=? AND status='COMPLETED'",
                    (str(segment.get("slice_id") or ""),),
                )
            }
            if persisted_variants != {"通用版", "FB版"}:
                reasons.append(_reason("MISSING_PERSISTED_OUTPUT_PAIR", f"variants={sorted(persisted_variants)}"))
        failure_after_ready = connection.execute(
            """
            SELECT 1 FROM events failed
            WHERE failed.candidate_id=?
              AND failed.event_type IN ('PRODUCTION_FAILED','PRODUCTION_PARTIAL_FAILED')
              AND failed.created_at >= COALESCE((
                SELECT MAX(ready.created_at) FROM events ready
                WHERE ready.candidate_id=failed.candidate_id AND ready.event_type='READY_FOR_REVIEW'
              ), '')
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
        if failure_after_ready:
            reasons.append(_reason("FAILED_AFTER_REVIEW_READY", "production failure was logged after review readiness"))
        if reasons:
            records.append({
                "candidate_id": row["id"],
                "parent_id": row["parent_id"] or "",
                "slice_id": str(segment.get("slice_id") or ""),
                "platform": row["platform"],
                "title": row["title"],
                "reasons": reasons,
                "confirmed": any(reason["confirmed"] for reason in reasons),
                "suggested_action": "quarantine_and_requeue" if any(reason["confirmed"] for reason in reasons) else "manual_inspection",
            })
    return {
        "dry_run": True,
        "status": status,
        "scanned_count": scanned,
        "suspect_count": len(records),
        "confirmed_count": sum(1 for record in records if record["confirmed"]),
        "records": records,
    }


def set_repair_run_status(config: dict[str, Any], run_id: str, action: str) -> dict[str, Any]:
    action = str(action or "").strip().lower()
    if action not in {"pause", "resume"}:
        raise ValueError("repair action must be pause or resume")
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM repair_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise ValueError("repair run does not exist")
    status = "PAUSED" if action == "pause" else "RUNNING"
    connection.execute(
        "UPDATE repair_runs SET status=?,updated_at=? WHERE id=?",
        (status, now_iso(), run_id),
    )
    connection.commit()
    return {"run_id": run_id, "status": status}


def _backup_database(config: dict[str, Any], run_id: str) -> Path:
    database = _workspace_path(config) / "factory.db"
    backup_dir = _workspace_path(config) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"factory-before-repair-{run_id}.db"
    source = sqlite3.connect(database)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return backup


def _quarantine_candidate_packages(config: dict[str, Any], candidate_id: str, run_id: str) -> list[str]:
    destination_root = _workspace_path(config) / "historical_quarantine" / run_id
    moved: list[str] = []
    roots = (
        ("local", _workspace_path(config) / "ready_for_review"),
        ("server", _storage_path(config) / "review"),
    )
    for label, root in roots:
        if not root.exists():
            continue
        for package in [root / candidate_id, *sorted(root.glob(f"{candidate_id}_part*"))]:
            if not package.is_dir():
                continue
            destination = destination_root / label / package.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.rmtree(destination)
            package.replace(destination)
            moved.append(str(destination))
    return moved


def repair_review_inventory(
    config: dict[str, Any],
    *,
    candidate_ids: list[str] | None = None,
    limit: int = 1,
    execute: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    if not execute:
        raise ValueError("repair requires explicit execute=True")
    requested = [str(value).strip() for value in (candidate_ids or []) if str(value).strip()]
    connection = connect_db(config)
    if run_id:
        repair = connection.execute("SELECT * FROM repair_runs WHERE id=?", (run_id,)).fetchone()
        if not repair:
            raise ValueError("repair run does not exist")
        requested = list(json.loads(repair["requested_json"] or "[]"))
        processed = list(json.loads(repair["processed_json"] or "[]"))
        failed = list(json.loads(repair["failed_json"] or "[]"))
        if repair["status"] == "PAUSED":
            return {"run_id": run_id, "status": "PAUSED", "processed": processed, "failed": failed}
    else:
        if not requested:
            raise ValueError("repair requires at least one explicit candidate id")
        audit = audit_review_inventory(config, dry_run=True)
        confirmed = {
            str(record["candidate_id"])
            for record in audit["records"]
            if record.get("confirmed")
        }
        unconfirmed = sorted(set(requested) - confirmed)
        if unconfirmed:
            raise ValueError(f"candidates are not confirmed by dry-run audit: {', '.join(unconfirmed)}")
        run_id = uuid.uuid4().hex[:16]
        processed = []
        failed = []
        backup = _backup_database(config, run_id)
        timestamp = now_iso()
        connection.execute(
            "INSERT INTO repair_runs(id,status,requested_json,processed_json,failed_json,backup_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                run_id, "RUNNING", json.dumps(requested), "[]", "[]", str(backup),
                timestamp, timestamp,
            ),
        )
        connection.commit()

    remaining = [candidate for candidate in requested if candidate not in processed]
    service = CandidateProductionService(config)
    for candidate_id in remaining[: max(1, int(limit))]:
        state = connection.execute("SELECT status FROM repair_runs WHERE id=?", (run_id,)).fetchone()
        if not state or state["status"] != "RUNNING":
            break
        row = connection.execute(
            "SELECT id,parent_id FROM candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if not row:
            failed.append({"candidate_id": candidate_id, "error": "candidate does not exist"})
            connection.execute(
                "UPDATE repair_runs SET failed_json=?,updated_at=? WHERE id=?",
                (json.dumps(failed, ensure_ascii=False), now_iso(), run_id),
            )
            connection.commit()
            continue
        root_candidate = str(row["parent_id"] or row["id"])
        quarantined = _quarantine_candidate_packages(config, root_candidate, run_id)
        timestamp = now_iso()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE candidates SET status='DOWNLOADED',updated_at=? WHERE id=?",
            (timestamp, root_candidate),
        )
        connection.execute(
            "UPDATE candidates SET status='HISTORICAL_QUARANTINED',updated_at=? WHERE parent_id=?",
            (timestamp, root_candidate),
        )
        connection.execute(
            "UPDATE production_outputs SET status='HISTORICAL_QUARANTINED' WHERE candidate_id=?",
            (root_candidate,),
        )
        connection.commit()
        try:
            result = service.run(
                root_candidate,
                trigger_source="history_repair",
                options={"repair_run_id": run_id, "rights_status": "MANUAL_REVIEW"},
            )
            processed.append(candidate_id)
            failed = [item for item in failed if item.get("candidate_id") != candidate_id]
        except Exception as error:
            failed.append({"candidate_id": candidate_id, "root_candidate_id": root_candidate, "error": str(error), "quarantined": quarantined})
        connection = connect_db(config)
        connection.execute(
            "UPDATE repair_runs SET processed_json=?,failed_json=?,updated_at=? WHERE id=?",
            (json.dumps(processed), json.dumps(failed, ensure_ascii=False), now_iso(), run_id),
        )
        connection.commit()

    remaining_count = len([candidate for candidate in requested if candidate not in processed])
    final_status = "COMPLETED" if remaining_count == 0 and not failed else "PARTIAL" if remaining_count == 0 else "RUNNING"
    connection.execute(
        "UPDATE repair_runs SET status=?,updated_at=? WHERE id=? AND status='RUNNING'",
        (final_status, now_iso(), run_id),
    )
    connection.commit()
    repair = connection.execute("SELECT * FROM repair_runs WHERE id=?", (run_id,)).fetchone()
    return {
        "run_id": run_id,
        "status": repair["status"],
        "backup_path": repair["backup_path"],
        "requested": requested,
        "processed": processed,
        "failed": failed,
        "remaining_count": remaining_count,
    }
