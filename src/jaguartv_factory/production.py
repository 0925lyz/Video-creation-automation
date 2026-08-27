from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from .core import (
    connect_db,
    download_candidate,
    media_duration,
    now_iso,
    produce_candidate,
    workspace_dir,
)
from .source_imports import sync_source_import_workflow_status


PRODUCTION_CONTRACT = "candidate-production-v1"
REQUIRED_VARIANTS = ("通用版", "FB版")
ALLOWED_TRIGGER_SOURCES = {
    "dashboard",
    "ai_agent",
    "cli",
    "scheduled_worker",
    "history_repair",
    "test",
}
SOURCE_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov"}
PRODUCTION_OPTION_DEFAULTS: dict[str, Any] = {
    "content_type": "auto",
    "segment_strategy": "auto",
    "audio_policy": "auto",
    "reaction_mode": "none",
    "source_volume": 0.72,
    "reaction_volume": 1.0,
    "reaction_position": "bottom_right",
}


class ProductionBusyError(RuntimeError):
    pass


class ProductionGateError(RuntimeError):
    pass


class SliceAnalysisError(RuntimeError):
    pass


def quarantine_failed_review_outputs(
    config: dict[str, Any], candidate_id: str, run_id: str
) -> list[str]:
    review_root = workspace_dir(config) / "ready_for_review"
    quarantine_root = workspace_dir(config) / "failed_production" / run_id
    moved: list[str] = []
    for package in [review_root / candidate_id, *sorted(review_root.glob(f"{candidate_id}_part*"))]:
        metadata_path = package / "metadata.json"
        if not package.is_dir() or not metadata_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if str(metadata.get("production_run_id") or "") != run_id:
            continue
        quarantine_root.mkdir(parents=True, exist_ok=True)
        destination = quarantine_root / package.name
        if destination.exists():
            shutil.rmtree(destination)
        package.replace(destination)
        moved.append(str(destination))
    return moved


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def source_media_for(config: dict[str, Any], candidate_id: str) -> Path | None:
    work = workspace_dir(config) / "jobs" / candidate_id
    return next(
        (
            path.resolve()
            for path in sorted(work.glob("source.*"))
            if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES and path.stat().st_size > 0
        ),
        None,
    )


def normalize_production_options(options: dict[str, Any] | None = None) -> dict[str, Any]:
    public_options = {
        str(key): value
        for key, value in (options or {}).items()
        if not str(key).startswith("_") and key != "trigger_source" and value is not None
    }
    for key, default in PRODUCTION_OPTION_DEFAULTS.items():
        public_options.setdefault(key, default)
    for key in ("content_type", "segment_strategy", "audio_policy", "reaction_mode", "reaction_position"):
        public_options[key] = str(public_options[key] or PRODUCTION_OPTION_DEFAULTS[key]).strip().lower()
    for key in ("source_volume", "reaction_volume", "reaction_size_ratio"):
        if key in public_options:
            public_options[key] = float(public_options[key])
    for key in ("max_segments", "reaction_border_width"):
        if key in public_options:
            public_options[key] = int(public_options[key])
    if "max_duration" in public_options:
        public_options["max_duration"] = float(public_options["max_duration"])
    for key in ("reaction_source", "batch_label", "krillinai_voice"):
        if key in public_options:
            value = str(public_options[key] or "").strip()
            if value:
                public_options[key] = value
            else:
                public_options.pop(key)
    if "rights_status" in public_options:
        value = str(public_options["rights_status"] or "").strip().upper()
        if value:
            public_options["rights_status"] = value
        else:
            public_options.pop("rights_status")
    return public_options


def production_contract_hash(options: dict[str, Any] | None = None) -> str:
    public_options = normalize_production_options(options)
    serialized = json.dumps(
        {"contract": PRODUCTION_CONTRACT, "options": public_options},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_output_pair(
    config: dict[str, Any],
    source: Path,
    slice_id: str,
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    variants = {str(item.get("variant") or ""): item for item in outputs}
    if set(variants) != set(REQUIRED_VARIANTS) or len(outputs) != 2:
        raise ProductionGateError(
            f"slice {slice_id} requires paired 通用版 and FB版 outputs"
        )

    source = source.resolve()
    source_hash = file_sha256(source)
    allowed_roots: list[Path] = []
    if config.get("_root"):
        workspace = workspace_dir(config)
        allowed_roots = [
            (workspace / "production_staging").resolve(),
            (workspace / "ready_for_review").resolve(),
        ]
    output_hashes: set[str] = set()
    for variant in REQUIRED_VARIANTS:
        item = variants[variant]
        path = Path(str(item.get("path") or "")).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ProductionGateError(f"slice {slice_id} {variant} output is missing or empty")
        if allowed_roots and not any(path.is_relative_to(root) for root in allowed_roots):
            raise ProductionGateError(f"slice {slice_id} {variant} output is outside an allowed production directory")
        if path == source or os.path.samefile(path, source):
            raise ProductionGateError(f"slice {slice_id} {variant} output is the original source")
        output_hash = file_sha256(path)
        if output_hash == source_hash:
            raise ProductionGateError(f"slice {slice_id} {variant} output matches original source hash")
        if output_hash in output_hashes:
            raise ProductionGateError(f"slice {slice_id} variants are not independent files")
        output_hashes.add(output_hash)
        qa = item.get("qa") if isinstance(item.get("qa"), dict) else {}
        if not qa.get("passed"):
            raise ProductionGateError(f"slice {slice_id} {variant} media QA did not pass")
        if "has_video" in qa and not qa.get("has_video"):
            raise ProductionGateError(f"slice {slice_id} {variant} has no video stream")
        if "has_audio" in qa and not qa.get("has_audio"):
            raise ProductionGateError(f"slice {slice_id} {variant} has no audio stream")
        if qa.get("duration") is not None and float(qa.get("duration") or 0) <= 0:
            raise ProductionGateError(f"slice {slice_id} {variant} has invalid duration")
        if qa.get("width") is not None and int(qa.get("width") or 0) <= 0:
            raise ProductionGateError(f"slice {slice_id} {variant} has invalid width")
        if qa.get("height") is not None and int(qa.get("height") or 0) <= 0:
            raise ProductionGateError(f"slice {slice_id} {variant} has invalid height")
        if qa.get("fps") is not None and float(qa.get("fps") or 0) < 20:
            raise ProductionGateError(f"slice {slice_id} {variant} has invalid frame rate")
        if not str(item.get("render_job_id") or "").strip():
            raise ProductionGateError(f"slice {slice_id} {variant} is missing a render job")

    generic = variants["通用版"]
    facebook = variants["FB版"]
    if int(generic.get("endcard_count") or 0) != 1:
        raise ProductionGateError(f"slice {slice_id} 通用版 must contain exactly one endcard")
    if int(facebook.get("endcard_count") or 0) != 0:
        raise ProductionGateError(f"slice {slice_id} FB版 must not contain the generic endcard")
    generic_layout = generic.get("layout") if isinstance(generic.get("layout"), dict) else {}
    if generic_layout.get("mode") != "external_bottom_banner":
        raise ProductionGateError(f"slice {slice_id} 通用版 layout gate failed")
    facebook_layout = facebook.get("layout") if isinstance(facebook.get("layout"), dict) else {}
    if facebook_layout.get("mode") == "external_bottom_banner":
        raise ProductionGateError(f"slice {slice_id} FB版 must keep its existing clean layout")
    return {
        "passed": True,
        "slice_id": slice_id,
        "source_sha256": source_hash,
        "output_sha256": sorted(output_hashes),
        "variants": list(REQUIRED_VARIANTS),
    }


def assert_candidate_ready_for_review(
    config: dict[str, Any], candidate_id: str, *, run_id: str = ""
) -> dict[str, Any]:
    connection = connect_db(config)
    if run_id:
        run = connection.execute(
            """
            SELECT * FROM production_runs
            WHERE id=? AND candidate_id=? AND status IN ('RUNNING','OUTPUTS_COMPLETE','SUCCEEDED')
            """,
            (run_id, candidate_id),
        ).fetchone()
    else:
        run = connection.execute(
            """
            SELECT * FROM production_runs
            WHERE candidate_id=? AND status='SUCCEEDED'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
    if not run:
        raise ProductionGateError("candidate has no successful standard production run")
    slices = connection.execute(
        "SELECT * FROM production_slices WHERE production_run_id=? ORDER BY slice_index",
        (run["id"],),
    ).fetchall()
    if not slices:
        raise ProductionGateError("candidate has no persisted smart-slice records")
    source = source_media_for(config, candidate_id)
    if source is None:
        raise ProductionGateError("candidate source file is missing")
    source_hash = file_sha256(source)
    review_root = (workspace_dir(config) / "ready_for_review").resolve()
    for slice_row in slices:
        if slice_row["status"] != "COMPLETED":
            raise ProductionGateError(f"slice {slice_row['id']} is not complete")
        outputs = connection.execute(
            "SELECT * FROM production_outputs WHERE slice_id=? AND status='COMPLETED'",
            (slice_row["id"],),
        ).fetchall()
        if {row["variant"] for row in outputs} != set(REQUIRED_VARIANTS):
            raise ProductionGateError(f"slice {slice_row['id']} does not have a complete variant pair")
        for output in outputs:
            path = Path(output["path"])
            if not path.is_file() or path.stat().st_size <= 0:
                raise ProductionGateError(f"missing completed output: {path}")
            if not path.resolve().is_relative_to(review_root):
                raise ProductionGateError(f"completed output is outside the review directory: {path}")
            if path.resolve() == source.resolve() or os.path.samefile(path, source):
                raise ProductionGateError(f"completed output is the original source: {path}")
            if output["sha256"] == source_hash or output["source_sha256"] != source_hash:
                raise ProductionGateError(f"completed output hash gate failed: {path}")
            if not output["has_video"] or not output["has_audio"]:
                raise ProductionGateError(f"completed output streams are incomplete: {path}")
            if output["duration_sec"] <= 0 or output["width"] <= 0 or output["height"] <= 0 or output["fps"] < 20:
                raise ProductionGateError(f"completed output media metadata is invalid: {path}")
            layout = json.loads(output["layout_json"] or "{}")
            if output["variant"] == "通用版":
                if output["endcard_count"] != 1 or layout.get("mode") != "external_bottom_banner":
                    raise ProductionGateError(f"generic layout gate failed: {path}")
            elif output["endcard_count"] != 0 or layout.get("mode") == "external_bottom_banner":
                raise ProductionGateError(f"FB layout gate failed: {path}")
            render = connection.execute(
                "SELECT status FROM render_jobs WHERE id=?",
                (output["render_job_id"],),
            ).fetchone()
            if not render or render["status"] != "COMPLETED":
                raise ProductionGateError(f"render job is not complete: {output['render_job_id']}")
    return {"run_id": run["id"], "slice_count": len(slices), "passed": True}


class CandidateProductionService:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def run(
        self,
        candidate_id: str,
        *,
        trigger_source: str,
        options: dict[str, Any] | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        candidate_id = str(candidate_id or "").strip()
        trigger_source = str(trigger_source or "").strip().lower()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        if trigger_source not in ALLOWED_TRIGGER_SOURCES:
            raise ValueError(f"unsupported trigger_source: {trigger_source}")
        public_options = normalize_production_options(options)
        contract_hash = production_contract_hash(public_options)
        idempotency_key = hashlib.sha256(
            f"{candidate_id}\x1f{contract_hash}".encode("utf-8")
        ).hexdigest()
        lock_dir = workspace_dir(self.config) / "production_locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{candidate_id}.lock"
        with lock_path.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ProductionBusyError(f"candidate {candidate_id} is already being produced") from error
            return self._run_locked(
                candidate_id,
                trigger_source=trigger_source,
                options=public_options,
                contract_hash=contract_hash,
                idempotency_key=idempotency_key,
                progress_callback=progress_callback,
            )

    def _run_locked(
        self,
        candidate_id: str,
        *,
        trigger_source: str,
        options: dict[str, Any],
        contract_hash: str,
        idempotency_key: str,
        progress_callback: Callable[[int, str], None] | None,
    ) -> dict[str, Any]:
        connection = connect_db(self.config)
        row = connection.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if not row:
            raise ValueError("candidate does not exist")
        source_import = connection.execute(
            "SELECT target_area FROM source_imports WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if source_import and str(source_import["target_area"]) == "approved":
            raise ProductionGateError("external finished import cannot enter automatic production")
        existing = connection.execute(
            "SELECT * FROM production_runs WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing and existing["status"] == "SUCCEEDED":
            assert_candidate_ready_for_review(self.config, candidate_id, run_id=str(existing["id"]))
            return {
                "candidate_id": candidate_id,
                "run_id": existing["id"],
                "contract_hash": contract_hash,
                "reused": True,
            }
        run_id = str(existing["id"]) if existing else hashlib.sha256(
            f"{idempotency_key}\x1f{now_iso()}".encode("utf-8")
        ).hexdigest()[:24]
        timestamp = now_iso()
        connection.execute(
            """
            INSERT INTO production_runs(
              id,candidate_id,trigger_source,idempotency_key,contract_version,contract_hash,
              status,stage,error_code,error,options_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
              status='RUNNING',stage='INPUT_VALIDATION',error_code='',error='',
              trigger_source=excluded.trigger_source,options_json=excluded.options_json,
              updated_at=excluded.updated_at
            """,
            (
                run_id, candidate_id, trigger_source, idempotency_key, PRODUCTION_CONTRACT,
                contract_hash, "RUNNING", "INPUT_VALIDATION", "", "",
                json.dumps(options, ensure_ascii=False, sort_keys=True), timestamp, timestamp,
            ),
        )
        connection.execute(
            "UPDATE candidates SET status='PRODUCTION_RUNNING',updated_at=? WHERE id=?",
            (timestamp, candidate_id),
        )
        sync_source_import_workflow_status(
            connection,
            candidate_id,
            "PRODUCTION_RUNNING",
            event_type="PRODUCTION_STARTED",
            actor=trigger_source,
            payload={"run_id": run_id},
        )
        connection.execute(
            "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (
                candidate_id,
                "PRODUCTION_STARTED",
                json.dumps({"run_id": run_id, "trigger_source": trigger_source, "contract_hash": contract_hash}),
                timestamp,
            ),
        )
        connection.commit()
        try:
            source = source_media_for(self.config, candidate_id)
            if source is None:
                source = download_candidate(self.config, row)
                row = connect_db(self.config).execute(
                    "SELECT * FROM candidates WHERE id=?", (candidate_id,)
                ).fetchone()
            if source is None or not Path(source).is_file():
                raise RuntimeError("download did not produce a valid source file")
            runtime_options = {**options, "_production_run_id": run_id, "_production_contract": PRODUCTION_CONTRACT}
            result_path = produce_candidate(
                self.config,
                row,
                progress_callback=progress_callback,
                options=runtime_options,
            )
            gate = assert_candidate_ready_for_review(self.config, candidate_id, run_id=run_id)
            connection = connect_db(self.config)
            timestamp = now_iso()
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "UPDATE production_runs SET status='SUCCEEDED',stage='READY_FOR_REVIEW',updated_at=? WHERE id=?",
                    (timestamp, run_id),
                )
                connection.execute(
                    "UPDATE candidates SET status='READY_FOR_REVIEW',updated_at=? WHERE id=? OR parent_id=?",
                    (timestamp, candidate_id, candidate_id),
                )
                connection.execute(
                    "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                    (
                        candidate_id,
                        "READY_FOR_REVIEW",
                        json.dumps({"run_id": run_id, "gate": gate}, ensure_ascii=False),
                        timestamp,
                    ),
                )
                sync_source_import_workflow_status(
                    connection,
                    candidate_id,
                    "READY_FOR_REVIEW",
                    event_type="READY_FOR_REVIEW",
                    actor=trigger_source,
                    payload={"run_id": run_id},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return {
                "candidate_id": candidate_id,
                "run_id": run_id,
                "contract_hash": contract_hash,
                "review": str(result_path),
                "gate": gate,
                "reused": False,
            }
        except Exception as error:
            if isinstance(error, SliceAnalysisError):
                status = "AWAITING_MANUAL_SLICE"
            elif isinstance(error, PermissionError) and str(error).startswith("BLOCKED_RIGHTS"):
                status = "BLOCKED_RIGHTS"
            else:
                status = "PRODUCTION_FAILED"
            quarantined = quarantine_failed_review_outputs(self.config, candidate_id, run_id)
            connection = connect_db(self.config)
            connection.execute(
                "UPDATE production_runs SET status='FAILED',stage=?,error_code=?,error=?,updated_at=? WHERE id=?",
                (status, type(error).__name__, str(error)[-4000:], now_iso(), run_id),
            )
            connection.execute(
                "UPDATE candidates SET status=?,updated_at=? WHERE id=?",
                (status, now_iso(), candidate_id),
            )
            connection.execute(
                "UPDATE candidates SET status='PRODUCTION_FAILED',updated_at=? WHERE parent_id=? AND status='PRODUCTION_STAGED'",
                (now_iso(), candidate_id),
            )
            connection.execute(
                "UPDATE production_outputs SET status='FAILED_RETAINED' WHERE production_run_id=?",
                (run_id,),
            )
            sync_source_import_workflow_status(
                connection,
                candidate_id,
                status,
                event_type=status,
                actor=trigger_source,
                payload={"run_id": run_id, "error": str(error)[-2000:]},
            )
            connection.execute(
                "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (
                    candidate_id,
                    status,
                    json.dumps({"run_id": run_id, "error": str(error), "quarantined": quarantined}, ensure_ascii=False),
                    now_iso(),
                ),
            )
            connection.commit()
            raise
