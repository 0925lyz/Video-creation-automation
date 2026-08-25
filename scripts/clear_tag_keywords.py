from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def timestamp() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y%m%d-%H%M%S")


def clear_daily_keywords(db_path: Path, backup_dir: Path | None = None) -> dict[str, int | str]:
    db_path = db_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    backup_root = (backup_dir or db_path.parent).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / f"{db_path.name}.bak-two-day-clear-{timestamp()}"
    shutil.copy2(db_path, backup_path)
    with sqlite3.connect(str(db_path)) as connection:
        before = connection.execute(
            "SELECT COUNT(1) FROM hot_keywords WHERE source LIKE 'daily_keywords:%'"
        ).fetchone()[0]
        connection.execute("DELETE FROM hot_keywords WHERE source LIKE 'daily_keywords:%'")
        after = connection.execute(
            "SELECT COUNT(1) FROM hot_keywords WHERE source LIKE 'daily_keywords:%'"
        ).fetchone()[0]
    return {"cleared": before - after, "remaining": after, "backup": str(backup_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear imported tag keywords every two days.")
    parser.add_argument("--db", type=Path, default=Path("workspace/factory.db"))
    parser.add_argument("--backup-dir", type=Path, default=None)
    args = parser.parse_args()
    print(clear_daily_keywords(args.db, args.backup_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
