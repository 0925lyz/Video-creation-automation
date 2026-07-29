from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .core import connect_db, now_iso


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
    target = date_value or datetime.now(timezone.utc).date().isoformat()
    connection = connect_db(config)
    return [
        dict(row)
        for row in connection.execute(
            "SELECT id,keyword,date,source,created_at FROM hot_keywords "
            "WHERE date=? ORDER BY id ASC",
            (target,),
        )
    ]


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
    today = datetime.now(timezone.utc).date().isoformat()
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
        connection = connect_db(config)
        for keyword in values:
            connection.execute(
                "INSERT OR IGNORE INTO hot_keywords(keyword,date,source,created_at) "
                "VALUES(?,?,?,?)",
                (keyword, today, source, now_iso()),
            )
        connection.commit()
        return {"status": "updated", "date": today, "count": len(values), "cached": False}
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

    def worker() -> None:
        last_run = ""
        while True:
            now = datetime.now(timezone.utc)
            day = now.date().isoformat()
            has_today = bool(list_hot_keywords(config, day))
            scheduled = now.hour == 8 and now.minute < 5 and last_run != day
            if (not has_today and not last_run) or scheduled:
                run_trends_job(config)
                last_run = day
            time.sleep(60)

    thread = threading.Thread(target=worker, name="google-trends", daemon=True)
    thread.start()
    return thread
