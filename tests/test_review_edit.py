import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.review_edit import manual_cut_review_output, replace_review_output_design


def make_review(config: dict, *, status: str = "READY_FOR_REVIEW") -> tuple[Path, Path]:
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO candidates(id,platform,url,title,status,metadata_json,created_at,updated_at)
        VALUES('cand-1','youtube','https://example.test/video','Video',?,'{}',?,?)
        """,
        (status, timestamp, timestamp),
    )
    connection.commit()
    local = Path(config["_root"]) / "workspace" / "ready_for_review" / "cand-1"
    server = Path(config["_root"]) / "workspace" / "server_media" / "review" / "cand-1"
    for directory in (local, server):
        directory.mkdir(parents=True)
        (directory / "video.mp4").write_bytes(b"old-video")
        (directory / "cand-1-通用版.mp4").write_bytes(b"old-video")
        (directory / "metadata.json").write_text(json.dumps({
            "source_job_id": "cand-1",
            "segment": {"start_sec": 0, "end_sec": 20, "duration_sec": 20},
            "output_variants": [{
                "variant": "通用版", "filename": "cand-1-通用版.mp4",
                "layout": {
                    "mode": "content_then_cta", "content_duration_sec": 20,
                    "source_orientation": "portrait",
                    "cta": {"asset_id": "cta-1", "media_type": "image", "orientation": "portrait", "duration_sec": 2},
                },
            }],
        }), encoding="utf-8")
    return local, server


def make_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media"},
        "edit": {"layout_mode": "original", "output_duration_sec": [12, 60], "render_engine": "remotion"},
        "remotion": {},
    }


def passed_qa(duration: float = 19) -> dict:
    return {
        "passed": True, "duration": duration, "width": 720, "height": 1280, "fps": 30,
        "has_video": True, "has_audio": True, "visual_quality": {"passed": True},
    }


def test_manual_cut_only_removes_middle_content_and_preserves_asset_identity(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    local, server = make_review(config)
    monkeypatch.setattr("jaguartv_factory.review_edit.media_duration", lambda path: 22.0)
    monkeypatch.setattr("jaguartv_factory.review_edit.qa_video", lambda *args, **kwargs: passed_qa())

    def fake_run(args, **kwargs):
        Path(args[-1]).write_bytes(b"cut-video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("jaguartv_factory.review_edit.run_command", fake_run)

    result = manual_cut_review_output(config, "cand-1:cand-1-通用版", 5, 8)

    assert result["content_duration_sec"] == 17
    assert (local / "cand-1-通用版.mp4").read_bytes() == b"cut-video"
    assert (local / "video.mp4").read_bytes() == b"cut-video"
    assert (server / "cand-1-通用版.mp4").read_bytes() == b"cut-video"
    metadata = json.loads((local / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["segment"]["duration_sec"] == 17
    assert metadata["manual_edits"][-1]["type"] == "middle_cut"


def test_manual_cut_rejects_non_review_status_and_cta_range(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    make_review(config, status="APPROVED")
    with pytest.raises(ValueError, match="pending review"):
        manual_cut_review_output(config, "cand-1:cand-1-通用版", 5, 8)


def test_design_replaces_generic_file_and_scopes_layers_to_content(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    local, _ = make_review(config)
    monkeypatch.setattr("jaguartv_factory.review_edit.media_duration", lambda path: 22.0)
    monkeypatch.setattr("jaguartv_factory.review_edit.qa_video", lambda *args, **kwargs: passed_qa(24))

    def fake_render(config_arg, source, output, **kwargs):
        assert kwargs["content_duration_override"] == 20
        output.write_bytes(b"designed-video")
        return {
            "variant": "通用版", "path": str(output), "endcard_count": 1,
            "cta_asset_id": "cta-new", "cta_media_type": "video", "cta_orientation": "portrait",
            "cta_duration_sec": 4,
            "layout": {
                "mode": "content_then_cta", "content_duration_sec": 20,
                "source_orientation": "portrait", "design_scope": "content_only",
                "cta": {"asset_id": "cta-new", "media_type": "video", "orientation": "portrait", "duration_sec": 4},
            },
        }

    monkeypatch.setattr("jaguartv_factory.review_edit.render_video_remotion_generic", fake_render)

    result = replace_review_output_design(
        config, "cand-1:cand-1-通用版", [{"id": "text", "type": "text", "text": "Oi", "x": 0.1, "y": 0.2}]
    )

    assert result["replaced"] is True
    assert result["layout"]["design_scope"] == "content_only"
    assert (local / "cand-1-通用版.mp4").read_bytes() == b"designed-video"
    assert sorted(path.name for path in local.glob("*.mp4")) == ["cand-1-通用版.mp4", "video.mp4"]
