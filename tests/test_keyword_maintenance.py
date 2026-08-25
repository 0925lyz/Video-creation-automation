from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE hot_keywords("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, date TEXT NOT NULL, "
        "source TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(keyword,date,source))"
    )
    connection.commit()
    connection.close()


def test_daily_import_parses_categories_and_is_idempotent(tmp_path: Path):
    module = load_script("import_daily_keywords.py")
    db = tmp_path / "factory.db"
    source = tmp_path / "daily.txt"
    create_db(db)
    source.write_text("足球类：futebol Brasil、Brasileirao\n音乐类：无\n", encoding="utf-8")

    rows = module.parse_keyword_file(source, "2026-08-25")
    first = module.import_rows(db, rows)
    second = module.import_rows(db, rows)

    assert first == {"parsed": 2, "inserted": 2, "today_total": 2}
    assert second["inserted"] == 0


def test_carry_forward_and_clear_use_backups_on_temporary_db(tmp_path: Path):
    carry = load_script("carry_forward_tag_keywords.py")
    clear = load_script("clear_tag_keywords.py")
    db = tmp_path / "factory.db"
    backups = tmp_path / "backups"
    create_db(db)
    connection = sqlite3.connect(db)
    connection.execute(
        "INSERT INTO hot_keywords(keyword,date,source,created_at) VALUES(?,?,?,?)",
        ("futebol", "2026-08-24", "daily_keywords:足球类", "2026-08-24T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO hot_keywords(keyword,date,source,created_at) VALUES(?,?,?,?)",
        ("trend", "2026-08-24", "google_trends", "2026-08-24T00:00:00Z"),
    )
    connection.commit()
    connection.close()

    copied = carry.carry_forward(db, "2026-08-25", backups)
    cleared = clear.clear_daily_keywords(db, backups)

    assert copied["copied"] == 1
    assert Path(copied["backup"]).is_file()
    assert cleared["cleared"] == 2
    assert Path(cleared["backup"]).is_file()
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT COUNT(*) FROM hot_keywords WHERE source='google_trends'").fetchone()[0] == 1


def test_keyword_maintenance_installer_tracks_daily_and_two_day_timers():
    script = (ROOT / "scripts" / "install-keyword-maintenance.sh").read_text(encoding="utf-8")

    assert "jaguartv-import-daily-keywords.timer" in script
    assert "OnActiveSec=2d" in script
    assert "OnUnitActiveSec=2d" in script
    assert "jaguartv-carry-forward-tag-keywords.timer" in script
