from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def today_sao_paulo() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()


def timestamp() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y%m%d-%H%M%S")


def carry_forward(db_path: Path, date_value: str, backup_dir: Path | None = None) -> dict[str, int | str]:
    db_path = db_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    backup_root = (backup_dir or db_path.parent).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / f"{db_path.name}.bak-carry-forward-{timestamp()}"
    shutil.copy2(db_path, backup_path)

    connection = sqlite3.connect(str(db_path))
    existing = connection.execute(
        "SELECT COUNT(1) FROM hot_keywords WHERE date=? AND source LIKE ?",
        (date_value, "daily_keywords:%"),
    ).fetchone()[0]
    if existing:
        return {"copied": 0, "target_existing": existing, "backup": str(backup_path)}

    source_date_row = connection.execute(
        "SELECT date FROM hot_keywords WHERE source LIKE ? AND date < ? GROUP BY date ORDER BY date DESC LIMIT 1",
        ("daily_keywords:%", date_value),
    ).fetchone()
    if not source_date_row:
        return {"copied": 0, "target_existing": 0, "backup": str(backup_path)}
    source_date = str(source_date_row[0])
    source_rows = connection.execute(
        "SELECT keyword,source,created_at FROM hot_keywords WHERE date=? AND source LIKE ?",
        (source_date, "daily_keywords:%"),
    ).fetchall()
    created_at = datetime.now(ZoneInfo("UTC")).isoformat()
    connection.executemany(
        "INSERT OR IGNORE INTO hot_keywords(keyword,date,source,created_at) VALUES(?,?,?,?)",
        [(keyword, date_value, source, created_at) for keyword, source, _ in source_rows],
    )
    connection.commit()
    copied = connection.execute(
        "SELECT COUNT(1) FROM hot_keywords WHERE date=? AND source LIKE ?",
        (date_value, "daily_keywords:%"),
    ).fetchone()[0]
    return {"copied": copied, "source_date": source_date, "backup": str(backup_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Carry latest JaguarTV tag keywords forward to today.")
    parser.add_argument("--db", type=Path, default=Path("workspace/factory.db"))
    parser.add_argument("--date", default="today")
    parser.add_argument("--backup-dir", type=Path, default=None)
    args = parser.parse_args()
    date_value = today_sao_paulo() if args.date == "today" else str(args.date)
    print(carry_forward(args.db, date_value, args.backup_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
