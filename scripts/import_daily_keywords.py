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
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        separator = "：" if "：" in line else ":" if ":" in line else ""
        if not line or not separator:
            continue
        label, raw_terms = line.split(separator, 1)
        label = re.sub(r"\s+", " ", label).strip()
        if not label:
            continue
        source = f"daily_keywords:{label}"
        for term in re.split(r"[、；;,，]", raw_terms):
            keyword = re.sub(r"\s+", " ", term).strip()
            if keyword.lower() in EMPTY_VALUES:
                continue
            rows.append((keyword, date_value, source, created_at))
    return rows


def import_rows(db_path: Path, rows: list[tuple[str, str, str, str]]) -> dict[str, int]:
    date_value = rows[0][1] if rows else ""
    with sqlite3.connect(str(db_path)) as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM hot_keywords WHERE date=?", (date_value,)
        ).fetchone()[0] if rows else 0
        connection.executemany(
            "INSERT OR IGNORE INTO hot_keywords(keyword,date,source,created_at) VALUES(?,?,?,?)",
            rows,
        )
        after = connection.execute(
            "SELECT COUNT(*) FROM hot_keywords WHERE date=?", (date_value,)
        ).fetchone()[0] if rows else 0
    return {"parsed": len(rows), "inserted": after - before, "today_total": after}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import categorized JaguarTV daily keywords.")
    parser.add_argument(
        "keyword_file",
        nargs="?",
        type=Path,
        default=Path("workspace/runtime/daily_keywords.txt"),
    )
    parser.add_argument("--db", type=Path, default=Path("workspace/factory.db"))
    parser.add_argument("--date", default="today")
    args = parser.parse_args()
    date_value = today_sao_paulo() if args.date == "today" else str(args.date)
    print(import_rows(args.db, parse_keyword_file(args.keyword_file, date_value)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
