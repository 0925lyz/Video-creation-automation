from __future__ import annotations

import fcntl
import hashlib
import json
import os
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


class ProductionBusyError(RuntimeError):
    pass


class ProductionGateError(RuntimeError):
    pass


class SliceAnalysisError(RuntimeError):
    pass


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


def production_contract_hash(options: dict[str, Any] | None = None) -> str:
    public_options = {
        key: value
        for key, value in (options or {}).items()
        if not str(key).startswith("_") and key != "trigger_source"
    }
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
    output_hashes: set[str] = set()
    for variant in REQUIRED_VARIANTS:
        item = variants[variant]
        path = Path(str(item.get("path") or "")).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ProductionGateError(f"slice {slice_id} {variant} output is missing or empty")
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
            WHERE id=? AND candidate_id=? AND status IN ('RUNNING','SUCCEEDED')
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
    for slice_row in slices:
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
        public_options = {
            key: value for key, value in (options or {}).items() if not str(key).startswith("_")
        }
        contract_hash = production_contract_hash(public_options)
        idempotency_key = hashlib.sha256(
            f"{candidate_id}\x1f{trigger_source}\x1f{contract_hash}".encode("utf-8")
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
        existing = connection.execute(
            "SELECT * FROM production_runs WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing and existing["status"] == "SUCCEEDED":
            assert_candidate_ready_for_review(self.config, candidate_id)
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
              options_json=excluded.options_json,updated_at=excluded.updated_at
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
            connection.execute(
                "UPDATE production_runs SET status='SUCCEEDED',stage='READY_FOR_REVIEW',updated_at=? WHERE id=?",
                (now_iso(), run_id),
            )
            connection.commit()
            return {
                "candidate_id": candidate_id,
                "run_id": run_id,
                "contract_hash": contract_hash,
                "review": str(result_path),
                "gate": gate,
                "reused": False,
            }
        except Exception as error:
            status = "AWAITING_MANUAL_SLICE" if isinstance(error, SliceAnalysisError) else "PRODUCTION_FAILED"
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
                "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (
                    candidate_id,
                    status,
                    json.dumps({"run_id": run_id, "error": str(error)}, ensure_ascii=False),
                    now_iso(),
                ),
            )
            connection.commit()
            raise
