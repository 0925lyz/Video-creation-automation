from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .core import connect_db, require_binary, run_command, workspace_dir
from .server_store import storage_root


VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov"}


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
    roots = [workspace_dir(config) / "ready_for_review", storage_root(config) / "review"]
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
    connection = connect_db(config)
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
                for path in sorted((workspace_dir(config) / "jobs" / str(row["parent_id"] or row["id"])).glob("source.*"))
                if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
            ),
            None,
        )
        source_hash = hash_file(source) if source else ""
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
            render_job_id = str(item.get("render_job_id") or "")
            render = connection.execute(
                "SELECT status FROM render_jobs WHERE id=?", (render_job_id,)
            ).fetchone() if render_job_id else None
            if not render or render["status"] != "COMPLETED":
                reasons.append(_reason("MISSING_RENDER_RECORD", render_job_id or "not recorded", confirmed=False))
            if item.get("variant") == "通用版" and int(item.get("endcard_count") or 0) != 1:
                reasons.append(_reason("ENDCARD_NOT_VERIFIED", "generic endcard count is not exactly one", confirmed=False))
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
