import json
import sqlite3
from pathlib import Path

from PIL import Image

from jaguartv_factory.core import connect_db, now_iso
from jaguartv_factory.poster_retirement import backup_and_clear_poster_inventory


def test_backup_and_clear_only_retires_legacy_poster_scope(tmp_path: Path):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media", "poster_subdir": "posters"},
    }
    connection = connect_db(config)
    timestamp = now_iso()
    connection.execute(
        "INSERT INTO candidates(id,platform,url,status,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("keep-candidate", "youtube", "https://example.test/video", "DISCOVERED", "{}", timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO posters(id,name,file_key,thumbnail_key,category,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        ("retire-me", "旧海报", "original/old.png", "thumbs/old.png", "match_prediction", "APPROVED", timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO poster_audit_events(poster_id,action,from_status,to_status,actor,request_id,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("retire-me", "APPROVE", "PENDING_REVIEW", "APPROVED", "reviewer", "req-1", "{}", timestamp),
    )
    connection.commit()
    root = tmp_path / "workspace/server_media/posters"
    for relative in ("original/old.png", "thumbs/old.png"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (24, 24), "green").save(target)

    backup_dir = tmp_path / "backups/poster-retirement"
    report = backup_and_clear_poster_inventory(config, backup_dir)

    assert report["records_backed_up"] == 1
    assert report["files_removed"] == 2
    assert report["protected_counts_unchanged"] is True
    assert not (root / "original/old.png").exists()
    database = connect_db(config)
    assert database.execute("SELECT COUNT(*) FROM posters").fetchone()[0] == 0
    assert database.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1
    assert json.loads((backup_dir / "posters.json").read_text(encoding="utf-8"))[0]["id"] == "retire-me"
    assert "original/old.png" in (backup_dir / "media-paths.txt").read_text(encoding="utf-8")
    with sqlite3.connect(backup_dir / "factory-before-poster-retirement.db") as snapshot:
        assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert snapshot.execute("SELECT COUNT(*) FROM posters").fetchone()[0] == 1
