import json
from pathlib import Path

from jaguartv_factory.audit import audit_review_inventory
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
    for job_id, variant, path in (("good:generic", "通用版", generic), ("good:facebook", "FB版", facebook)):
        connection.execute(
            "INSERT INTO render_jobs(id,candidate_id,variant,engine,status,output_path,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (job_id, "good", variant, "remotion", "COMPLETED", str(path), "{}", timestamp, timestamp),
        )
    connection.commit()
    monkeypatch.setattr("jaguartv_factory.audit.probe_media", lambda path: {
        "playable": True, "has_video": True, "has_audio": True, "duration": 21.5 if "通用版" in str(path) else 20.0,
        "width": 1080, "height": 1920, "fps": 30.0,
    })

    report = audit_review_inventory(config, status="READY_FOR_REVIEW", dry_run=True)

    assert report["suspect_count"] == 0
    assert report["records"] == []
