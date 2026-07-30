from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import yaml

from .core import connect_db, now_iso

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore[assignment]


def _trend_req_class(config: dict[str, Any]) -> type:
    configured = str(
        os.environ.get("JAGUARTV_PYTRENDS_PATH")
        or (config.get("trends", {}) or {}).get("pytrends_path")
        or ""
    ).strip()
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.append(Path("/opt/pytrends"))
    candidates.extend(Path.home().glob("Documents/Codex/*/*/pytrends"))
    for path in candidates:
        if path.is_dir() and (path / "pytrends").is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
            break
    from pytrends.request import TrendReq

    return TrendReq


def list_hot_keywords(
    config: dict[str, Any], date_value: str | None = None
) -> list[dict[str, Any]]:
    target = date_value or trends_today(config)
    connection = connect_db(config)
    return [
        dict(row)
        for row in connection.execute(
            "SELECT id,keyword,date,source,created_at FROM hot_keywords "
            "WHERE date=? ORDER BY id ASC",
            (target,),
        )
    ]


def _config_root(config: dict[str, Any]) -> Path:
    return Path(str(config.get("_root") or Path.cwd())).expanduser().resolve()


def _resolve_path(config: dict[str, Any], value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return _config_root(config) / path


def _keywords_file(config: dict[str, Any]) -> Path:
    return _resolve_path(config, str((config.get("sources", {}) or {}).get("keywords_file") or "config/keywords.demo.yaml"))


def sync_hot_keywords_to_keyword_file(config: dict[str, Any], keywords: list[str]) -> dict[str, Any]:
    settings = config.get("trends", {}) or {}
    group_name = str(settings.get("sync_keywords_group") or "").strip()
    if not group_name:
        return {"synced": False, "reason": "trends.sync_keywords_group is empty"}
    cleaned = list(dict.fromkeys(str(keyword).strip() for keyword in keywords if str(keyword).strip()))
    if not cleaned:
        return {"synced": False, "reason": "no keywords"}
    path = _keywords_file(config)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    data[group_name] = {
        "weight": 1.2,
        "enabled": True,
        "terms": {"pt": cleaned},
        "source": "google_trends_br_daily",
        "updated_at": now_iso(),
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"synced": True, "group": group_name, "count": len(cleaned), "path": str(path)}


def fetch_rss_trending_keywords(config: dict[str, Any]) -> list[str]:
    geo = str((config.get("trends", {}) or {}).get("geo") or "BR").strip() or "BR"
    request = Request(
        f"https://trends.google.com/trending/rss?geo={geo}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=20) as response:
        payload = response.read()
    root = ElementTree.fromstring(payload)
    values: list[str] = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        title = title.strip()
        if title:
            values.append(title)
    return values


def trends_today(config: dict[str, Any]) -> str:
    settings = config.get("trends", {}) or {}
    timezone_name = str(settings.get("schedule_timezone") or config.get("run", {}).get("timezone") or "UTC")
    tz = ZoneInfo(timezone_name) if ZoneInfo else timezone.utc
    return datetime.now(tz).date().isoformat()


def run_trends_job(
    config: dict[str, Any],
    client_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    settings = config.get("trends", {}) or {}
    query_terms = [
        str(value).strip() for value in settings.get("keywords", ["futebol Brasil"])
        if str(value).strip()
    ]
    if not query_terms:
        raise ValueError("trends.keywords must contain at least one query")
    today = trends_today(config)
    source = str(settings.get("source") or "google_trends")
    try:
        factory = client_factory or _trend_req_class(config)
        client = factory(hl="pt-BR", tz=180)
        client.build_payload(
            kw_list=query_terms,
            cat=0,
            timeframe="now 1-d",
            geo=str(settings.get("geo") or "BR"),
            gprop="",
        )
        client.interest_over_time()
        related = client.related_queries() or {}
        rising = (related.get(query_terms[0]) or {}).get("rising")
        if rising is None:
            raise RuntimeError("pytrends returned no rising queries")
        values = [
            str(value).strip()
            for value in list(rising["query"])
            if str(value).strip()
        ][: max(1, int(settings.get("top_n", 10)))]
        if not values:
            raise RuntimeError("pytrends returned an empty rising query list")
    except Exception as pytrends_error:
        try:
            values = fetch_rss_trending_keywords(config)[: max(1, int(settings.get("top_n", 10)))]
            if not values:
                raise RuntimeError("Google Trends RSS returned no keywords")
        except Exception as rss_error:
            cached = list_hot_keywords(config, today)
            print(f"WARN google trends: pytrends={pytrends_error}; rss={rss_error}; cached={len(cached)}")
            return {
                "status": "cached" if cached else "unavailable",
                "date": today,
                "count": len(cached),
                "cached": True,
                "error": f"pytrends={pytrends_error}; rss={rss_error}",
            }

    try:
        connection = connect_db(config)
        for keyword in values:
            connection.execute(
                "INSERT OR IGNORE INTO hot_keywords(keyword,date,source,created_at) "
                "VALUES(?,?,?,?)",
                (keyword, today, source, now_iso()),
            )
        connection.commit()
        keyword_sync = sync_hot_keywords_to_keyword_file(config, values)
        return {
            "status": "updated",
            "date": today,
            "count": len(values),
            "cached": False,
            "keyword_sync": keyword_sync,
        }
    except Exception as error:
        cached = list_hot_keywords(config, today)
        print(f"WARN google trends: {error}; cached={len(cached)}")
        return {
            "status": "cached" if cached else "unavailable",
            "date": today,
            "count": len(cached),
            "cached": True,
            "error": str(error),
        }


def start_trends_scheduler(config: dict[str, Any]) -> threading.Thread | None:
    settings = config.get("trends", {}) or {}
    if settings.get("enabled", True) is False:
        return None
    cron = str(settings.get("cron") or "0 8 * * *").split()
    scheduled_minute = int(cron[0]) if len(cron) >= 2 and cron[0].isdigit() else 0
    scheduled_hour = int(cron[1]) if len(cron) >= 2 and cron[1].isdigit() else 8
    timezone_name = str(settings.get("schedule_timezone") or config.get("run", {}).get("timezone") or "UTC")
    schedule_tz = ZoneInfo(timezone_name) if ZoneInfo else timezone.utc

    def worker() -> None:
        last_run = ""
        while True:
            now = datetime.now(schedule_tz)
            day = now.date().isoformat()
            has_today = bool(list_hot_keywords(config, day))
            scheduled = now.hour == scheduled_hour and scheduled_minute <= now.minute < scheduled_minute + 5 and last_run != day
            if (not has_today and not last_run) or scheduled:
                run_trends_job(config)
                last_run = day
            time.sleep(60)

    thread = threading.Thread(target=worker, name="google-trends", daemon=True)
    thread.start()
    return thread
