import json
from pathlib import Path
import threading

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.production import (
    CandidateProductionService,
    ProductionBusyError,
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
    assert observed == [{"content_type": "auto"}, {"content_type": "auto"}]
    triggers = [
        row[0]
        for row in connection.execute(
            "SELECT trigger_source FROM production_runs ORDER BY created_at"
        )
    ]
    assert triggers == ["dashboard", "ai_agent"]


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
