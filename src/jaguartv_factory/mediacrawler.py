from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .core import (
    candidate_id,
    candidate_market_rejection,
    candidate_too_long,
    connect_db,
    likely_language,
    now_iso,
    scoring_config_path,
    source_duration_limit,
)
from .scoring import score_candidate_v2


PLATFORM_FIELDS = {
    "douyin": {
        "aliases": ("douyin", "dy"),
        "id": ("aweme_id", "video_id"),
        "page": ("aweme_url", "webpage_url"),
        "media": ("video_download_url", "video_url"),
        "views": ("video_play_count", "view_count"),
    },
    "bilibili": {
        "aliases": ("bilibili", "bili"),
        "id": ("video_id", "bvid"),
        "page": ("webpage_url", "video_url"),
        "media": ("video_download_url",),
        "views": ("video_play_count", "view_count"),
    },
    "xiaohongshu": {
        "aliases": ("xiaohongshu", "xhs"),
        "id": ("note_id", "video_id"),
        "page": ("note_url", "webpage_url"),
        "media": ("video_url", "video_download_url"),
        "views": ("view_count", "video_play_count"),
    },
    "tiktok": {
        "aliases": ("tiktok", "tk"),
        "id": ("video_id", "aweme_id", "id"),
        "page": ("webpage_url", "video_url", "share_url"),
        "media": ("video_download_url", "video_url", "download_url"),
        "views": ("video_play_count", "view_count", "play_count"),
    },
}


def parse_metric(value: Any) -> int:
    text = str(value or "0").strip().lower().replace(",", "")
    multipliers = {"万": 10_000, "亿": 100_000_000, "k": 1_000, "m": 1_000_000}
    multiplier = next((factor for suffix, factor in multipliers.items() if suffix in text), 1)
    match = re.search(r"\d+(?:\.\d+)?", text)
    return int(float(match.group(0)) * multiplier) if match else 0


def _first(record: dict[str, Any], keys: Iterable[str]) -> Any:
    return next((record[key] for key in keys if record.get(key) not in (None, "")), None)


def infer_jsonl_platform(path: Path, explicit: str | None = None) -> str:
    candidate = (explicit or "").strip().lower()
    parts = {part.lower() for part in path.parts}
    for platform, fields in PLATFORM_FIELDS.items():
        if candidate in fields["aliases"] or parts.intersection(fields["aliases"]):
            return platform
    raise ValueError("Cannot infer platform; pass --platform douyin, bilibili, xiaohongshu, or tiktok")


def ingest_mediacrawler_jsonl(
    config: dict[str, Any],
    path: Path,
    *,
    platform: str | None = None,
    min_likes: int = 0,
    min_views: int = 0,
) -> dict[str, int]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    normalized_platform = infer_jsonl_platform(path, platform)
    fields = PLATFORM_FIELDS[normalized_platform]
    connection = connect_db(config)
    stats = {
        "read": 0, "inserted": 0, "duplicate": 0, "filtered": 0,
        "market_rejected": 0, "invalid": 0,
    }
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            stats["read"] += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                stats["invalid"] += 1
                continue
            source_id = str(_first(record, fields["id"]) or "").strip()
            page_url = str(_first(record, fields["page"]) or "").strip()
            media_url = str(_first(record, fields["media"]) or "").strip()
            if not source_id or not (page_url or media_url):
                stats["invalid"] += 1
                continue
            likes = parse_metric(_first(record, ("liked_count", "like_count", "likes")))
            views = parse_metric(_first(record, fields["views"]))
            if likes < min_likes or views < min_views:
                stats["filtered"] += 1
                continue
            url = page_url or media_url
            title = str(_first(record, ("title", "desc", "description")) or "")[:500]
            description = str(_first(record, ("desc", "description")) or "")[:4000]
            duration = _first(record, ("duration", "duration_sec", "video_duration"))
            try:
                duration = float(duration) if duration not in (None, "") else None
            except (TypeError, ValueError):
                duration = None
            info = {
                **record,
                "id": source_id,
                "webpage_url": url,
                "view_count": views,
                "like_count": likes,
                "duration": duration,
                "direct_media_url": media_url,
                "ingest_source": "mediacrawler_jsonl",
                "ingest_file": str(path),
            }
            market_rejection = candidate_market_rejection(
                config, info, keyword=str(record.get("source_keyword") or "")
            )
            if market_rejection:
                stats["market_rejected"] += 1
                continue
            language, _ = likely_language(f"{title} {description}")
            score, breakdown = score_candidate_v2(info, title, scoring_config_path(config))
            too_long = candidate_too_long(config, duration)
            timestamp = now_iso()
            cursor = connection.execute(
                """INSERT OR IGNORE INTO candidates
                (id,platform,source_id,url,title,description,duration,view_count,detected_language,
                 score,status,metadata_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate_id(normalized_platform, source_id, url), normalized_platform, source_id, url,
                    title, description, duration, views, language, score, "TOO_LONG" if too_long else "DISCOVERED",
                    json.dumps({
                        **info,
                        "duration_gate": {
                            "max_source_duration_sec": source_duration_limit(config),
                            "too_long": too_long,
                        },
                        "score_breakdown": breakdown,
                    }, ensure_ascii=False), timestamp, timestamp,
                ),
            )
            if cursor.rowcount:
                stats["inserted"] += 1
            else:
                stats["duplicate"] += 1
    connection.commit()
    return stats
