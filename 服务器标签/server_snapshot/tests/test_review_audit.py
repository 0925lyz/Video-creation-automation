import json
from pathlib import Path

from jaguartv_factory.audit import (
    audit_review_inventory,
    repair_review_inventory,
    set_repair_run_status,
)
from jaguartv_factory.core import connect_db, now_iso


def make_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media"},
    }


def insert_candidate(config: dict, candidate_id: str, metadata: dict) -> None:
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
            candidate_id, "", 30, 1, "pt", 50, "READY_FOR_REVIEW",
            json.dumps(metadata), timestamp, timestamp,
        ),
    )
    connection.commit()


def test_audit_flags_passthrough_and_missing_pair_without_mutation(tmp_path: Path):
    config = make_config(tmp_path)
    source = tmp_path / "workspace" / "jobs" / "bad" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    review = tmp_path / "workspace" / "ready_for_review" / "bad"
    review.mkdir(parents=True)
    output = review / "video.mp4"
    output.write_bytes(b"source")
    insert_candidate(config, "bad", {
        "segment_strategy": "passthrough_original",
        "output_variants": [{"variant": "原视频", "path": str(output)}],
    })
    before_db = (tmp_path / "workspace" / "factory.db").read_bytes()
    before_media = output.read_bytes()

    report = audit_review_inventory(config, status="READY_FOR_REVIEW", dry_run=True)

    assert report["suspect_count"] == 1
    assert report["records"][0]["candidate_id"] == "bad"
    assert {reason["code"] for reason in report["records"][0]["reasons"]} >= {
        "PASSTHROUGH_ORIGINAL", "MISSING_VARIANT_PAIR", "OUTPUT_MATCHES_SOURCE_HASH"
    }
    assert (tmp_path / "workspace" / "factory.db").read_bytes() == before_db
    assert output.read_bytes() == before_media


def test_audit_does_not_flag_complete_production_pair(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    source = tmp_path / "workspace" / "jobs" / "good" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    review = tmp_path / "workspace" / "ready_for_review" / "good"
    review.mkdir(parents=True)
    generic = review / "good-通用版.mp4"
    facebook = review / "good-FB版.mp4"
    generic.write_bytes(b"generic-render")
    facebook.write_bytes(b"facebook-render")
    metadata = {
        "production_contract": "candidate-production-v1",
        "production_run_id": "good-run",
        "segment_strategy": "sports_highlight",
        "segment": {"slice_id": "good:slice:01", "start_sec": 2.0, "end_sec": 22.0, "duration_sec": 20.0},
        "output_variants": [
            {"variant": "通用版", "path": str(generic), "render_job_id": "good:generic", "endcard_count": 1},
            {"variant": "FB版", "path": str(facebook), "render_job_id": "good:facebook", "endcard_count": 0},
        ],
    }
    insert_candidate(config, "good", metadata)
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        "INSERT INTO production_runs(id,candidate_id,trigger_source,idempotency_key,contract_version,contract_hash,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("good-run", "good", "test", "good-key", "candidate-production-v1", "good-hash", "SUCCEEDED", "READY_FOR_REVIEW", timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO production_slices(id,production_run_id,candidate_id,slice_index,start_sec,end_sec,duration_sec,status,analysis_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("good:slice:01", "good-run", "good", 1, 2.0, 22.0, 20.0, "COMPLETED", "{}", timestamp, timestamp),
    )
    for job_id, variant, path in (("good:generic", "通用版", generic), ("good:facebook", "FB版", facebook)):
        connection.execute(
            "INSERT INTO render_jobs(id,candidate_id,variant,engine,status,output_path,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (job_id, "good", variant, "remotion", "COMPLETED", str(path), "{}", timestamp, timestamp),
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
                f"good:{variant}", "good-run", "good:slice:01", "good", variant, str(path),
                f"hash-{variant}", "source-hash", path.stat().st_size, 20.0, 1080, 1920,
                30.0, 1, 1, job_id, 1 if variant == "通用版" else 0, "{}", "{}", timestamp, timestamp,
            ),
        )
    connection.commit()
    monkeypatch.setattr("jaguartv_factory.audit.probe_media", lambda path: {
        "playable": True, "has_video": True, "has_audio": True, "duration": 21.5 if "通用版" in str(path) else 20.0,
        "width": 1080, "height": 1920, "fps": 30.0,
    })

    report = audit_review_inventory(config, status="READY_FOR_REVIEW", dry_run=True)

    assert report["suspect_count"] == 0
    assert report["records"] == []


def test_confirmed_repair_backs_up_quarantines_and_uses_production_service(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    source = tmp_path / "workspace" / "jobs" / "bad" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    review = tmp_path / "workspace" / "ready_for_review" / "bad"
    review.mkdir(parents=True)
    (review / "video.mp4").write_bytes(b"source")
    insert_candidate(config, "bad", {"segment_strategy": "passthrough_original"})
    calls = []

    monkeypatch.setattr("jaguartv_factory.audit.audit_review_inventory", lambda *args, **kwargs: {
        "records": [{"candidate_id": "bad", "confirmed": True}],
    })

    def fake_run(self, candidate_id, *, trigger_source, options=None, progress_callback=None):
        calls.append((candidate_id, trigger_source, dict(options or {})))
        return {"candidate_id": candidate_id, "reused": False}

    monkeypatch.setattr("jaguartv_factory.audit.CandidateProductionService.run", fake_run)
    report = repair_review_inventory(config, candidate_ids=["bad"], limit=1, execute=True)

    assert report["status"] == "COMPLETED"
    assert Path(report["backup_path"]).is_file()
    assert not review.exists()
    quarantined = tmp_path / "workspace" / "historical_quarantine" / report["run_id"] / "local" / "bad"
    assert (quarantined / "video.mp4").is_file()
    assert calls[0][0:2] == ("bad", "history_repair")


def test_repair_run_can_pause_and_resume(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    for candidate_id in ("bad-1", "bad-2"):
        source = tmp_path / "workspace" / "jobs" / candidate_id / "source.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"source")
        review = tmp_path / "workspace" / "ready_for_review" / candidate_id
        review.mkdir(parents=True)
        (review / "video.mp4").write_bytes(b"source")
        insert_candidate(config, candidate_id, {"segment_strategy": "passthrough_original"})
    monkeypatch.setattr("jaguartv_factory.audit.audit_review_inventory", lambda *args, **kwargs: {
        "records": [
            {"candidate_id": "bad-1", "confirmed": True},
            {"candidate_id": "bad-2", "confirmed": True},
        ],
    })
    monkeypatch.setattr(
        "jaguartv_factory.audit.CandidateProductionService.run",
        lambda self, candidate_id, **kwargs: {"candidate_id": candidate_id},
    )

    first = repair_review_inventory(
        config, candidate_ids=["bad-1", "bad-2"], limit=1, execute=True
    )
    paused = set_repair_run_status(config, first["run_id"], "pause")
    no_progress = repair_review_inventory(config, execute=True, run_id=first["run_id"], limit=1)
    set_repair_run_status(config, first["run_id"], "resume")
    completed = repair_review_inventory(config, execute=True, run_id=first["run_id"], limit=1)

    assert first["status"] == "RUNNING"
    assert paused["status"] == "PAUSED"
    assert no_progress["status"] == "PAUSED"
    assert completed["status"] == "COMPLETED"
