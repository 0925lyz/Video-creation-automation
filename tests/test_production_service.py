import json
from pathlib import Path
import threading

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.production import (
    CandidateProductionService,
    ProductionBusyError,
    ProductionGateError,
    file_sha256,
    validate_output_pair,
)


def config_for(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"provider": "disabled"},
        "edit": {"output_duration_sec": [12, 60]},
        "remotion": {"promo_duration_sec": 1.5},
    }


def insert_candidate(config: dict, candidate_id: str = "candidate-1", status: str = "DISCOVERED") -> None:
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO candidates(
          id,platform,source_id,url,title,description,duration,view_count,
          detected_language,score,status,metadata_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate_id, "facebook", candidate_id, f"https://example.test/{candidate_id}",
            "Football highlight", "", 30, 100, "pt", 80, status, "{}", timestamp, timestamp,
        ),
    )
    connection.commit()


def test_dashboard_and_agent_create_same_standard_production_contract(tmp_path: Path, monkeypatch):
    config = config_for(tmp_path)
    insert_candidate(config)
    source = tmp_path / "workspace" / "jobs" / "candidate-1" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    observed = []

    def fake_produce(config_arg, row, progress_callback=None, options=None):
        observed.append({key: value for key, value in options.items() if not key.startswith("_")})
        return tmp_path / "review"

    monkeypatch.setattr("jaguartv_factory.production.produce_candidate", fake_produce)
    monkeypatch.setattr(
        "jaguartv_factory.production.assert_candidate_ready_for_review",
        lambda *args, **kwargs: {"passed": True},
    )
    service = CandidateProductionService(config)
    dashboard = service.run("candidate-1", trigger_source="dashboard", options={"content_type": "auto"})
    connection = connect_db(config)
    connection.execute("UPDATE production_runs SET status='FAILED' WHERE id=?", (dashboard["run_id"],))
    connection.execute("UPDATE candidates SET status='DOWNLOADED' WHERE id='candidate-1'")
    connection.commit()
    agent = service.run("candidate-1", trigger_source="ai_agent", options={"content_type": "auto"})

    assert dashboard["contract_hash"] == agent["contract_hash"]
    assert dashboard["run_id"] == agent["run_id"]
    canonical_options = {
        "content_type": "auto",
        "segment_strategy": "auto",
        "audio_policy": "auto",
        "reaction_mode": "none",
        "source_volume": 0.72,
        "reaction_volume": 1.0,
        "reaction_position": "bottom_right",
    }
    assert observed == [canonical_options, canonical_options]
    triggers = [
        row[0]
        for row in connection.execute(
            "SELECT trigger_source FROM production_runs ORDER BY created_at"
        )
    ]
    assert triggers == ["ai_agent"]


def test_dashboard_and_agent_default_options_reuse_same_successful_run(tmp_path: Path, monkeypatch):
    config = config_for(tmp_path)
    insert_candidate(config, status="DOWNLOADED")
    source = tmp_path / "workspace" / "jobs" / "candidate-1" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    calls = []
    gated_run_ids = []

    def fake_produce(config_arg, row, progress_callback=None, options=None):
        calls.append({key: value for key, value in options.items() if not key.startswith("_")})
        return tmp_path / "review"

    monkeypatch.setattr("jaguartv_factory.production.produce_candidate", fake_produce)

    def fake_gate(*args, **kwargs):
        gated_run_ids.append(kwargs.get("run_id"))
        return {"passed": True}

    monkeypatch.setattr("jaguartv_factory.production.assert_candidate_ready_for_review", fake_gate)
    service = CandidateProductionService(config)
    dashboard = service.run(
        "candidate-1",
        trigger_source="dashboard",
        options={"rights_status": "VERIFIED"},
    )
    agent = service.run(
        "candidate-1",
        trigger_source="ai_agent",
        options={
            "content_type": "auto",
            "segment_strategy": "auto",
            "audio_policy": "auto",
            "reaction_mode": "none",
            "source_volume": 0.72,
            "reaction_volume": 1.0,
            "reaction_position": "bottom_right",
            "rights_status": "VERIFIED",
        },
    )

    assert dashboard["contract_hash"] == agent["contract_hash"]
    assert dashboard["run_id"] == agent["run_id"]
    assert agent["reused"] is True
    assert len(calls) == 1
    assert gated_run_ids == [dashboard["run_id"], dashboard["run_id"]]


def test_retry_reuses_download_and_slice_identity(tmp_path: Path, monkeypatch):
    config = config_for(tmp_path)
    insert_candidate(config, status="DOWNLOADED")
    source = tmp_path / "workspace" / "jobs" / "candidate-1" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    calls = {"download": 0, "produce": 0}

    def fake_download(*args, **kwargs):
        calls["download"] += 1
        return source

    def fake_produce(config_arg, row, progress_callback=None, options=None):
        calls["produce"] += 1
        if calls["produce"] == 1:
            raise RuntimeError("render failed")
        return tmp_path / "review"

    monkeypatch.setattr("jaguartv_factory.production.download_candidate", fake_download)
    monkeypatch.setattr("jaguartv_factory.production.produce_candidate", fake_produce)
    monkeypatch.setattr(
        "jaguartv_factory.production.assert_candidate_ready_for_review",
        lambda *args, **kwargs: {"passed": True},
    )
    service = CandidateProductionService(config)
    with pytest.raises(RuntimeError, match="render failed"):
        service.run("candidate-1", trigger_source="ai_agent")
    service.run("candidate-1", trigger_source="ai_agent")

    assert calls == {"download": 0, "produce": 2}
    assert connect_db(config).execute(
        "SELECT COUNT(*) FROM production_runs WHERE candidate_id='candidate-1'"
    ).fetchone()[0] == 1


def test_imported_pending_video_uses_standard_production_and_syncs_workflow_status(
    tmp_path: Path, monkeypatch
):
    config = config_for(tmp_path)
    insert_candidate(config, candidate_id="source-import-1", status="DOWNLOADED")
    timestamp = now_iso()
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO source_imports(
          id,candidate_id,source_type,import_method,source_platform,normalized_url,
          download_task_id,idempotency_key,target_area,actual_workflow_status,
          download_status,operator_id,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "import-task-1",
            "source-import-1",
            "source_import",
            "url",
            "facebook",
            "https://www.facebook.com/watch/1",
            "import-task-1",
            "import-idempotency-1",
            "pending_production",
            "DOWNLOADED",
            "COMPLETED",
            "operator-1",
            timestamp,
            timestamp,
        ),
    )
    connection.commit()
    source = tmp_path / "workspace" / "jobs" / "source-import-1" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    calls = []

    def fake_produce(config_arg, row, progress_callback=None, options=None):
        calls.append((row["id"], options.get("_production_contract")))
        return tmp_path / "review"

    monkeypatch.setattr("jaguartv_factory.production.produce_candidate", fake_produce)
    monkeypatch.setattr(
        "jaguartv_factory.production.assert_candidate_ready_for_review",
        lambda *args, **kwargs: {"passed": True},
    )

    CandidateProductionService(config).run("source-import-1", trigger_source="dashboard")

    saved = connect_db(config).execute(
        "SELECT actual_workflow_status FROM source_imports WHERE id='import-task-1'"
    ).fetchone()
    assert calls == [("source-import-1", "candidate-production-v1")]
    assert saved["actual_workflow_status"] == "READY_FOR_REVIEW"


def test_external_finished_import_cannot_enter_automatic_production(tmp_path: Path):
    config = config_for(tmp_path)
    insert_candidate(config, candidate_id="external-finished", status="APPROVED")
    timestamp = now_iso()
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO source_imports(
          id,candidate_id,source_type,import_method,source_platform,normalized_url,
          download_task_id,idempotency_key,target_area,actual_workflow_status,
          download_status,review_source,operator_id,reviewed_at,approval_reason,
          created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "external-import-task",
            "external-finished",
            "source_import",
            "url",
            "youtube",
            "https://www.youtube.com/watch?v=external-finished",
            "external-import-task",
            "external-import-key",
            "approved",
            "APPROVED",
            "COMPLETED",
            "manual_import",
            "dashboard_admin",
            timestamp,
            "manual finished asset",
            timestamp,
            timestamp,
        ),
    )
    connection.commit()

    with pytest.raises(ProductionGateError, match="external finished import"):
        CandidateProductionService(config).run(
            "external-finished",
            trigger_source="dashboard",
        )

    assert connect_db(config).execute(
        "SELECT COUNT(*) FROM production_runs WHERE candidate_id='external-finished'"
    ).fetchone()[0] == 0


def test_same_candidate_concurrent_production_is_locked(tmp_path: Path, monkeypatch):
    config = config_for(tmp_path)
    insert_candidate(config, status="DOWNLOADED")
    source = tmp_path / "workspace" / "jobs" / "candidate-1" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    entered = threading.Event()
    release = threading.Event()

    def blocking_produce(*args, **kwargs):
        entered.set()
        release.wait(3)
        return tmp_path / "review"

    monkeypatch.setattr("jaguartv_factory.production.produce_candidate", blocking_produce)
    monkeypatch.setattr(
        "jaguartv_factory.production.assert_candidate_ready_for_review",
        lambda *args, **kwargs: {"passed": True},
    )
    service = CandidateProductionService(config)
    thread = threading.Thread(target=lambda: service.run("candidate-1", trigger_source="dashboard"))
    thread.start()
    assert entered.wait(2)
    with pytest.raises(ProductionBusyError):
        service.run("candidate-1", trigger_source="ai_agent")
    release.set()
    thread.join(3)


@pytest.mark.parametrize("variants", [["通用版"], ["FB版"]])
def test_single_variant_cannot_pass_review_gate(tmp_path: Path, variants: list[str]):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    outputs = []
    for variant in variants:
        path = tmp_path / f"{variant}.mp4"
        path.write_bytes(variant.encode())
        outputs.append({
            "variant": variant,
            "path": str(path),
            "render_job_id": f"slice:{variant}",
            "qa": {"passed": True, "playable": True, "has_video": True, "has_audio": True},
            "endcard_count": 1 if variant == "通用版" else 0,
        })
    with pytest.raises(RuntimeError, match="paired 通用版 and FB版"):
        validate_output_pair({}, source, "slice-1", outputs)


def test_passthrough_original_cannot_pass_review_gate(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"same bytes")
    with pytest.raises(RuntimeError, match="original source"):
        validate_output_pair(
            {},
            source,
            "slice-1",
            [
                {"variant": "通用版", "path": str(source), "render_job_id": "g", "qa": {"passed": True}},
                {"variant": "FB版", "path": str(source), "render_job_id": "f", "qa": {"passed": True}},
            ],
        )


def install_fake_persisted_outputs(
    config: dict,
    candidate_id: str,
    run_id: str,
    variants: list[str],
) -> Path:
    workspace = Path(config["_root"]) / "workspace"
    source = workspace / "jobs" / candidate_id / "source.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source-media")
    package = workspace / "ready_for_review" / candidate_id
    package.mkdir(parents=True, exist_ok=True)
    timestamp = now_iso()
    connection = connect_db(config)
    slice_id = f"{run_id}:slice:01"
    connection.execute(
        "INSERT INTO production_slices(id,production_run_id,candidate_id,slice_index,start_sec,end_sec,duration_sec,status,analysis_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (slice_id, run_id, candidate_id, 1, 1.0, 13.0, 12.0, "COMPLETED", "{}", timestamp, timestamp),
    )
    for variant in variants:
        output = package / f"{candidate_id}-{variant}.mp4"
        output.write_bytes(f"rendered-{variant}".encode())
        render_job_id = f"{slice_id}:{variant}"
        layout = {"mode": "external_bottom_banner" if variant == "通用版" else "existing_fb_layout"}
        connection.execute(
            "INSERT INTO render_jobs(id,candidate_id,variant,engine,status,output_path,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (render_job_id, candidate_id, variant, "remotion", "COMPLETED", str(output), "{}", timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO production_outputs(
              id,production_run_id,slice_id,candidate_id,variant,status,path,sha256,source_sha256,
              size_bytes,duration_sec,width,height,fps,has_video,has_audio,render_job_id,
              endcard_count,layout_json,qa_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,'COMPLETED',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{slice_id}:{variant}", run_id, slice_id, candidate_id, variant, str(output),
                file_sha256(output), file_sha256(source), output.stat().st_size, 13.5 if variant == "通用版" else 12.0,
                1080, 1920, 30.0, 1, 1, render_job_id, 1 if variant == "通用版" else 0,
                json.dumps(layout), json.dumps({"passed": True}), timestamp, timestamp,
            ),
        )
    connection.execute(
        "UPDATE production_runs SET status='OUTPUTS_COMPLETE',stage='QUALITY_GATE' WHERE id=?",
        (run_id,),
    )
    connection.commit()
    return package


@pytest.mark.parametrize("variants", [["通用版"], ["FB版"]])
def test_service_does_not_finalize_incomplete_variant_pair(tmp_path: Path, monkeypatch, variants: list[str]):
    config = config_for(tmp_path)
    insert_candidate(config, status="DOWNLOADED")
    source = tmp_path / "workspace" / "jobs" / "candidate-1" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-media")

    def fake_produce(config_arg, row, progress_callback=None, options=None):
        return install_fake_persisted_outputs(config_arg, row["id"], options["_production_run_id"], variants)

    monkeypatch.setattr("jaguartv_factory.production.produce_candidate", fake_produce)
    with pytest.raises(RuntimeError, match="complete variant pair"):
        CandidateProductionService(config).run("candidate-1", trigger_source="test")

    row = connect_db(config).execute("SELECT status FROM candidates WHERE id='candidate-1'").fetchone()
    assert row["status"] == "PRODUCTION_FAILED"


def test_service_finalizes_only_after_complete_variant_pair(tmp_path: Path, monkeypatch):
    config = config_for(tmp_path)
    insert_candidate(config, status="DOWNLOADED")
    source = tmp_path / "workspace" / "jobs" / "candidate-1" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-media")

    def fake_produce(config_arg, row, progress_callback=None, options=None):
        return install_fake_persisted_outputs(
            config_arg, row["id"], options["_production_run_id"], ["通用版", "FB版"]
        )

    monkeypatch.setattr("jaguartv_factory.production.produce_candidate", fake_produce)
    result = CandidateProductionService(config).run("candidate-1", trigger_source="test")

    connection = connect_db(config)
    candidate = connection.execute("SELECT status FROM candidates WHERE id='candidate-1'").fetchone()
    run = connection.execute("SELECT status FROM production_runs WHERE id=?", (result["run_id"],)).fetchone()
    assert candidate["status"] == "READY_FOR_REVIEW"
    assert run["status"] == "SUCCEEDED"
    assert result["gate"]["passed"] is True
