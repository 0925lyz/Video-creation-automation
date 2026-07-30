#!/usr/bin/env python3
"""Enrich Bilibili candidates and remove non-football inventory.

This is an operator script for the factory server.  It fixes Bilibili rows
that were discovered with only an av id, then optionally deletes candidates
that do not match the Brazil/football content lane.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from jaguartv_factory.core import connect_db, load_config, now_iso, workspace_dir
from jaguartv_factory.dashboard import delete_candidates


FOOTBALL_TEXT = re.compile(
    r"足球|巴西|巴甲|巴乙|世界杯|内马尔|小罗|罗纳尔多|贝利|维尼修斯|罗德里戈|"
    r"梅西|姆巴佩|亚马尔|瓜迪奥拉|曼城|欧冠|解放者杯|进球|集锦|球星|"
    r"flamengo|palmeiras|brasileir|são paulo|sao paulo|santos|corinthians|"
    r"atlético mineiro|atletico mineiro|"
    r"corinthians|vasco|botafogo|fluminense|neymar|arrascaeta|cazé|caze|gol|gols|"
    r"football|futebol",
    re.I,
)
REJECT_TEXT = re.compile(
    r"王者荣耀|和平精英|电竞|ag超玩会|成都ag|旅行|旅游|美食|穿搭|小说|短剧|"
    r"电影解说|电视剧|动画|音乐|舞蹈|原神|崩坏|搞笑|生活技巧|科普|奇闻|"
    r"BLG|NOVA|EDG|DRG|XLG|TYL|5FW|Boaster|K1ra|SiuFatBB|无畏契约|"
    r"奥丁|幻影|五杀|残局|排位|训练赛|POKEMON|Pokemon|Team Liquid|FaZe|"
    r"Ninjas In Pyjamas|弗拉门戈曲|FLAMENCO|Flamenco|弗拉明戈|恋人\\(Lover\\)|"
    r"vlog|辩论|川沙中学|foryoupage|fypviral|red light|红灯街|贱人TV|"
    r"PES\\d*|实况足球|FIFA 游戏|FIFA游戏|二串|公推|野鸡|这里是小妤|老家依旧|视频三连",
    re.I,
)
FOOTBALL_KEYWORDS = {
    "巴西足球",
    "巴甲",
    "巴甲集锦",
    "#brasileirao",
    "#brasileirão",
    "#flamengo",
    "#sãopaulofc",
    "#sao paulofc",
    "#temporealcazétv",
    "#temporealcazetv",
    "#arrascaeta",
}


def best_thumbnail(payload: dict[str, Any]) -> str:
    thumbnail = str(payload.get("thumbnail") or "")
    if thumbnail:
        return thumbnail
    thumbnails = payload.get("thumbnails")
    if isinstance(thumbnails, list) and thumbnails:
        for item in reversed(thumbnails):
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])
    return ""


def latest_cookie(config: dict[str, Any]) -> list[str]:
    root = workspace_dir(config) / "sessions" / "bilibili"
    cookies = sorted(root.glob("*/cookies.txt"), key=lambda path: path.stat().st_mtime, reverse=True)
    return ["--cookies", str(cookies[0])] if cookies else []


def fetch_bilibili_info(config: dict[str, Any], url: str, timeout: int) -> dict[str, Any]:
    args = [
        ".venv/bin/yt-dlp",
        "--dump-single-json",
        "--skip-download",
        "--no-playlist",
        *latest_cookie(config),
        url,
    ]
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode != 0 or not result.stdout.strip():
        return {"_fetch_error": (result.stderr or result.stdout)[-800:]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        return {"_fetch_error": f"invalid yt-dlp json: {error}"}


def job_info(config: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    path = workspace_dir(config) / "jobs" / candidate_id / "source.info.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def classify(
    title: str,
    description: str,
    keyword: str,
    category: str,
    duration: Any,
    max_duration: float,
) -> tuple[bool, str]:
    try:
        if duration and float(duration) > max_duration:
            return False, "too_long_for_source_gate"
    except (TypeError, ValueError):
        pass
    title_text = " ".join([title, description])
    all_text = " ".join([title, description, keyword, category])
    if REJECT_TEXT.search(all_text):
        return False, "reject_keyword"
    if FOOTBALL_TEXT.search(title_text):
        return True, "football_title_or_description"
    if not title:
        return False, "missing_title_after_enrich"
    if keyword.strip().lower() in FOOTBALL_KEYWORDS:
        return False, "keyword_only_not_enough"
    return False, "no_brazil_football_signal"


def update_review_metadata(config: dict[str, Any], source_job_id: str, title: str, thumbnail: str) -> int:
    if not title:
        return 0
    roots = [
        workspace_dir(config) / "ready_for_review",
        Path(config.get("storage", {}).get("root", "workspace/server_media")) / "review",
    ]
    changed = 0
    for root in roots:
        if not root.is_absolute():
            root = Path(config.get("_root", ".")) / root
        for metadata_path in root.glob("*/metadata.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if str(metadata.get("source_job_id") or metadata.get("job_id") or "") != source_job_id:
                continue
            source = metadata.setdefault("source", {})
            source["title"] = title
            if thumbnail:
                source["thumbnail"] = thumbnail
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            changed += 1
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/pipeline.yaml")
    parser.add_argument("--apply", action="store_true", help="write title/thumbnail metadata changes")
    parser.add_argument("--delete", action="store_true", help="delete rows classified as non-football")
    parser.add_argument("--fetch-timeout", type=int, default=25)
    parser.add_argument("--max-duration", type=float, default=1800.0)
    parser.add_argument("--report", default="/tmp/bili_cleanup_report.json")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    connection = connect_db(config)
    rows = connection.execute(
        "SELECT * FROM candidates WHERE platform='bilibili' ORDER BY created_at DESC"
    ).fetchall()
    report: list[dict[str, Any]] = []
    delete_ids: list[str] = []
    metadata_updates = 0
    review_updates = 0

    for row in rows:
        metadata: dict[str, Any]
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}

        info = job_info(config, row["id"])
        title = str(row["title"] or metadata.get("title") or info.get("title") or "").strip()
        description = str(row["description"] or metadata.get("description") or info.get("description") or "").strip()
        duration = row["duration"] or metadata.get("duration") or info.get("duration")
        thumbnail = str(metadata.get("thumbnail") or best_thumbnail(info) or "")

        if not title or not thumbnail or not duration:
            fetched = fetch_bilibili_info(config, row["url"], args.fetch_timeout)
            if fetched.get("_fetch_error"):
                metadata["_fetch_error"] = fetched["_fetch_error"]
            title = title or str(fetched.get("title") or "").strip()
            description = description or str(fetched.get("description") or "").strip()
            duration = duration or fetched.get("duration")
            thumbnail = thumbnail or best_thumbnail(fetched)

        keyword = str(metadata.get("keyword") or "")
        category = str(metadata.get("category") or "")
        keep, reason = classify(title, description, keyword, category, duration, args.max_duration)
        if not keep:
            delete_ids.append(str(row["id"]))

        if args.apply:
            changed = False
            if title and title != row["title"]:
                changed = True
            if description and description != row["description"]:
                changed = True
            if duration and duration != row["duration"]:
                changed = True
            if thumbnail and thumbnail != metadata.get("thumbnail"):
                changed = True
            if changed or metadata.get("_fetch_error"):
                metadata.update({
                    key: value
                    for key, value in {
                        "title": title,
                        "description": description,
                        "duration": duration,
                        "thumbnail": thumbnail,
                    }.items()
                    if value not in ("", None)
                })
                connection.execute(
                    """
                    UPDATE candidates
                    SET title=?, description=?, duration=COALESCE(?, duration),
                        metadata_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (title, description, duration, json.dumps(metadata, ensure_ascii=False), now_iso(), row["id"]),
                )
                metadata_updates += 1
            review_updates += update_review_metadata(config, str(row["id"]), title, thumbnail)

        report.append({
            "id": row["id"],
            "status": row["status"],
            "source_id": row["source_id"],
            "title": title,
            "keyword": keyword,
            "category": category,
            "duration": duration,
            "thumbnail": bool(thumbnail),
            "keep": keep,
            "reason": reason,
            "fetch_error": metadata.get("_fetch_error", ""),
        })
        print(f"{'KEEP' if keep else 'DROP'} {row['id']} {reason}: {title or '[no title]'}")

    if args.apply:
        connection.commit()
    if args.apply and args.delete and delete_ids:
        delete_result = delete_candidates(config, {"candidate_ids": delete_ids})
    else:
        delete_result = {"deleted": 0, "bytes_freed": 0, "items": []}

    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "total": len(rows),
        "keep": len(rows) - len(delete_ids),
        "drop": len(delete_ids),
        "metadata_updates": metadata_updates,
        "review_metadata_updates": review_updates,
        "deleted": delete_result["deleted"],
        "bytes_freed": delete_result["bytes_freed"],
        "report": args.report,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
