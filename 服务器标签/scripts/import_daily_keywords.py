from __future__ import annotations

import argparse
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


EMPTY_VALUES = {"", "无", "暂无", "none", "null", "n/a", "-"}


def today_sao_paulo() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()


def parse_keyword_file(path: Path, date_value: str) -> list[tuple[str, str, str, str]]:
    created_at = datetime.now(ZoneInfo("UTC")).isoformat()
    rows: list[tuple[str, str, str, str]] = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or "：" not in line:
            continue
        label, raw_terms = line.split("：", 1)
        label = label.strip()
        source = f"daily_keywords:{label}"
        for term in re.split(r"[、；;]", raw_terms):
            keyword = re.sub(r"\s+", " ", term.strip())
            if keyword.lower() in EMPTY_VALUES:
                continue
            rows.append((keyword, date_value, source, created_at))
    return rows


def import_rows(db_path: Path, rows: list[tuple[str, str, str, str]]) -> dict[str, int]:
    connection = sqlite3.connect(str(db_path))
    before = connection.execute(
        "SELECT COUNT(*) FROM hot_keywords WHERE date=?",
        (rows[0][1] if rows else "",),
    ).fetchone()[0] if rows else 0
    connection.executemany(
        "INSERT OR IGNORE INTO hot_keywords(keyword,date,source,created_at) VALUES(?,?,?,?)",
        rows,
    )
    connection.commit()
    after = connection.execute(
        "SELECT COUNT(*) FROM hot_keywords WHERE date=?",
        (rows[0][1] if rows else "",),
    ).fetchone()[0] if rows else 0
    return {"parsed": len(rows), "inserted": after - before, "today_total": after}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import JaguarTV daily categorized keywords.")
    parser.add_argument("keyword_file", type=Path)
    parser.add_argument("--db", type=Path, default=Path("workspace/factory.db"))
    parser.add_argument("--date", default="today")
    args = parser.parse_args()

    date_value = today_sao_paulo() if args.date == "today" else str(args.date)
    rows = parse_keyword_file(args.keyword_file, date_value)
    result = import_rows(args.db, rows)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
