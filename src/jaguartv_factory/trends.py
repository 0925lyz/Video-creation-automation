from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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


DEFAULT_TREND_EXCLUDED_CATEGORIES = (
    "教程及优点展示类",
    "官方性质类",
    "合作类",
    "运营教学类",
    "教程及答疑类",
)

DEFAULT_TREND_CATEGORY_QUERIES = {
    "ai短剧": ["AI short drama Brasil", "novela IA Brasil"],
    "明星名人歌手": ["celebridades Brasil", "cantores famosos Brasil"],
    "足球球星": ["jogadores brasileiros futebol", "Neymar Vini Jr Brasil"],
    "足球类": ["futebol Brasil", "Brasileirão hoje"],
    "新闻类": ["notícias Brasil hoje", "tendências Brasil"],
    "音乐类": ["música viral Brasil", "funk sertanejo Brasil"],
    "肥皂剧（电视剧、电影）": ["novelas Globo Brasil", "filmes séries Brasil"],
    "少儿剧": ["conteúdo infantil Brasil", "desenho infantil Brasil"],
    "纪录片（美食、动物、地区发展）": ["documentário Brasil comida natureza", "gastronomia Brasil"],
    "综艺": ["reality show Brasil", "programa entretenimento Brasil"],
    "社交挑战": ["TikTok Brasil viral", "desafio viral Brasil"],
    "舞蹈": ["dança viral Brasil", "coreografia Brasil"],
}

GENERIC_KEYWORD_FRAGMENTS = {
    "brazil",
    "brasil",
    "trending",
    "trend",
    "trends",
    "searches",
    "search",
    "hot",
    "today",
    "hoje",
    "news",
    "noticias",
    "notícias",
    "viral",
    "google",
    "youtube",
    "reddit",
    "tiktok",
    "instagram",
    "web",
    "article",
    "discussion",
    "thread",
    "result",
    "results",
}


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


def _resolve_executable(config: dict[str, Any], value: str) -> str:
    configured = str(value or "").strip()
    if not configured:
        return ""
    path = Path(configured).expanduser()
    if path.is_absolute() or "/" in configured:
        resolved = path if path.is_absolute() else _resolve_path(config, configured)
        return str(resolved) if resolved.is_file() and os.access(resolved, os.X_OK) else ""
    return shutil.which(configured) or ""


def _configured_category_queries(settings: dict[str, Any]) -> dict[str, list[str]]:
    configured = settings.get("category_queries")
    if not configured:
        return {}
    rows: dict[str, list[str]] = {}
    if isinstance(configured, dict):
        iterator = configured.items()
    elif isinstance(configured, list):
        iterator = (
            (item.get("category") or item.get("label"), item.get("queries") or item.get("terms") or item.get("query"))
            for item in configured
            if isinstance(item, dict)
        )
    else:
        return {}
    for category, queries in iterator:
        label = str(category or "").strip()
        if not label:
            continue
        if isinstance(queries, str):
            values = [queries]
        elif isinstance(queries, list):
            values = [str(query).strip() for query in queries if str(query).strip()]
        else:
            values = []
        if values:
            rows[label] = values
    return rows


def trend_category_queries(config: dict[str, Any]) -> dict[str, list[str]]:
    settings = config.get("trends", {}) or {}
    queries = _configured_category_queries(settings)
    if not queries and settings.get("use_default_category_queries"):
        queries = DEFAULT_TREND_CATEGORY_QUERIES
    excluded = {
        str(value).strip()
        for value in settings.get("excluded_categories", DEFAULT_TREND_EXCLUDED_CATEGORIES)
        if str(value).strip()
    }
    return {
        category: values
        for category, values in queries.items()
        if category not in excluded
    }


def _runtime_keywords_file(config: dict[str, Any]) -> Path:
    return _resolve_path(
        config,
        str(
            (config.get("trends", {}) or {}).get("runtime_keywords_file")
            or "workspace/runtime/keywords.trends.yaml"
        ),
    )


def sync_hot_keywords_to_keyword_file(config: dict[str, Any], keywords: list[str]) -> dict[str, Any]:
    settings = config.get("trends", {}) or {}
    group_name = str(settings.get("sync_keywords_group") or "").strip()
    if not group_name:
        return {"synced": False, "reason": "trends.sync_keywords_group is empty"}
    cleaned = list(dict.fromkeys(str(keyword).strip() for keyword in keywords if str(keyword).strip()))
    if not cleaned:
        return {"synced": False, "reason": "no keywords"}
    path = _runtime_keywords_file(config)
    path.parent.mkdir(parents=True, exist_ok=True)
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


def sync_categorized_hot_keywords_to_keyword_file(
    config: dict[str, Any], categorized: dict[str, list[str]]
) -> dict[str, Any]:
    settings = config.get("trends", {}) or {}
    if not settings.get("sync_by_category", True):
        flattened: list[str] = []
        for keywords in categorized.values():
            flattened.extend(keywords)
        return sync_hot_keywords_to_keyword_file(config, flattened)
    cleaned_by_category = {
        category: list(dict.fromkeys(str(keyword).strip() for keyword in keywords if str(keyword).strip()))
        for category, keywords in categorized.items()
    }
    cleaned_by_category = {key: value for key, value in cleaned_by_category.items() if value}
    if not cleaned_by_category:
        return {"synced": False, "reason": "no keywords"}
    path = _runtime_keywords_file(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    prefix = str(settings.get("sync_keywords_group") or "google_trends_br_daily").strip() or "google_trends_br_daily"
    for category, keywords in cleaned_by_category.items():
        data[f"{prefix}:{category}"] = {
            "weight": 1.2,
            "enabled": True,
            "category": category,
            "terms": {"pt": keywords},
            "source": "multi_source_trends",
            "updated_at": now_iso(),
        }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {
        "synced": True,
        "groups": len(cleaned_by_category),
        "count": sum(len(value) for value in cleaned_by_category.values()),
        "path": str(path),
    }


def _fetch_rss_trending_keywords_with_source(config: dict[str, Any]) -> tuple[list[str], str]:
    geo = str((config.get("trends", {}) or {}).get("geo") or "BR").strip() or "BR"
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    settings = config.get("trends", {}) or {}
    configured_python = str(settings.get("scrapling_python") or "workspace/tool_venvs/scrapling/bin/python")
    scrapling_python = _resolve_path(config, configured_python)
    if scrapling_python.is_file():
        script = (
            "import json,sys; from scrapling.fetchers import Fetcher; "
            "page=Fetcher.get(sys.argv[1], stealthy_headers=True, timeout=20); "
            "print(json.dumps(page.xpath('//item/title/text()').getall(), ensure_ascii=False))"
        )
        try:
            result = subprocess.run(
                [str(scrapling_python), "-c", script, url],
                check=False,
                text=True,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            try:
                values = json.loads(result.stdout)
            except json.JSONDecodeError:
                values = []
            cleaned = [str(value).strip() for value in values if str(value).strip()]
            if cleaned:
                return cleaned, "scrapling_google_trends_rss"
    request = Request(
        url,
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
    return values, "google_trends_rss_urllib"


def fetch_rss_trending_keywords(config: dict[str, Any]) -> list[str]:
    return _fetch_rss_trending_keywords_with_source(config)[0]


def _clean_keyword_candidate(value: str) -> str:
    text = re.sub(r"https?://\S+", " ", str(value or ""))
    text = re.sub(r"^[#*\-\d\.\s]+", "", text)
    text = re.split(r"\s[\-|•–—]\s|[|:：]", text, maxsplit=1)[0]
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n\"'“”‘’.,;()[]{}")
    if not (3 <= len(text) <= 80):
        return ""
    lowered = text.casefold()
    if lowered in GENERIC_KEYWORD_FRAGMENTS:
        return ""
    if sum(1 for part in re.split(r"\s+", lowered) if part in GENERIC_KEYWORD_FRAGMENTS) >= 4:
        return ""
    return text


def _keyword_candidates_from_text(text: str, *, limit: int) -> list[str]:
    values: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith(("url:", "author:", "published:", "highlights:", "generated:")):
            continue
        if line.lower().startswith("title:"):
            line = line.split(":", 1)[1].strip()
        elif line.startswith("#"):
            line = line.lstrip("#").strip()
        elif not re.match(r"^[A-Za-zÀ-ÿ0-9#@].*", line):
            continue
        candidate = _clean_keyword_candidate(line)
        if candidate and candidate.casefold() not in {value.casefold() for value in values}:
            values.append(candidate)
        if len(values) >= limit:
            break
    return values


def _json_from_mixed_stdout(stdout: str) -> dict[str, Any]:
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            payload = json.loads(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _pytrends_keywords(
    config: dict[str, Any],
    query_terms: list[str],
    *,
    client_factory: Callable[..., Any] | None = None,
) -> list[str]:
    settings = config.get("trends", {}) or {}
    factory = client_factory or _trend_req_class(config)
    client = factory(hl="pt-BR", tz=180)
    client.build_payload(
        kw_list=query_terms,
        cat=0,
        timeframe=str(settings.get("timeframe") or "now 1-d"),
        geo=str(settings.get("geo") or "BR"),
        gprop="",
    )
    client.interest_over_time()
    related = client.related_queries() or {}
    values: list[str] = []
    for term in query_terms:
        rising = (related.get(term) or {}).get("rising")
        if rising is None:
            continue
        values.extend(
            str(value).strip()
            for value in list(rising["query"])
            if str(value).strip()
        )
    return list(dict.fromkeys(values))


def _last30days_script(config: dict[str, Any], settings: dict[str, Any]) -> str:
    explicit = os.environ.get("JAGUARTV_LAST30DAYS_SCRIPT") or str(settings.get("script") or "")
    if explicit:
        path = _resolve_path(config, explicit)
        return str(path) if path.is_file() else ""
    configured_skill = os.environ.get("JAGUARTV_LAST30DAYS_SKILL_DIR") or str(settings.get("skill_dir") or "")
    candidates = [configured_skill] if configured_skill else []
    candidates.extend(
        [
            "workspace/external_tools/last30days-skill",
            "workspace/external_tools/last30days-skill/skills/last30days",
            "/home/ubuntu/.agents/skills/last30days",
            "/home/ubuntu/.codex/skills/last30days",
            "/Users/jaguar/.agents/skills/last30days",
            "/Users/jaguar/.codex/skills/last30days",
        ]
    )
    for candidate in candidates:
        if not candidate:
            continue
        root = _resolve_path(config, candidate) if not Path(candidate).expanduser().is_absolute() else Path(candidate).expanduser()
        script = root / "scripts" / "last30days.py"
        if script.is_file():
            return str(script)
    return ""


def fetch_last30days_keywords(
    config: dict[str, Any], category: str, query_terms: list[str]
) -> dict[str, Any]:
    settings = ((config.get("trends", {}) or {}).get("last30days") or {})
    if settings.get("enabled", True) is False:
        return {"status": "disabled", "keywords": []}
    script = _last30days_script(config, settings)
    python = _resolve_executable(config, str(settings.get("python") or os.environ.get("LAST30DAYS_PYTHON") or ".venv/bin/python"))
    if not script or not python:
        return {"status": "unavailable", "keywords": [], "error": "last30days script or Python 3.12 runtime not found"}
    memory_dir = _resolve_path(config, str(settings.get("save_dir") or "workspace/runtime/last30days"))
    memory_dir.mkdir(parents=True, exist_ok=True)
    topic = str(settings.get("topic_template") or "{category} Brasil tendências").format(
        category=category,
        query=", ".join(query_terms),
    )
    command = [
        python, script, topic,
        "--emit=json",
        "--json-profile=agent",
        "--quick",
        "--no-browser-cookies",
        "--days", str(int(settings.get("days", 30))),
        "--save-dir", str(memory_dir),
    ]
    source_list = str(settings.get("sources") or "").strip()
    if source_list:
        command.extend(["--search", source_list])
    if settings.get("auto_resolve", True):
        command.append("--auto-resolve")
    try:
        result = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            timeout=int(settings.get("timeout_sec", 240)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "error", "keywords": [], "error": str(error)}
    payload = _json_from_mixed_stdout(result.stdout)
    text_parts = [
        str(item.get("title") or "") + "\n" + str(item.get("summary") or "")
        for item in payload.get("results", [])
        if isinstance(item, dict)
    ]
    text_parts.extend(str(item.get("title") or "") for item in payload.get("clusters", []) if isinstance(item, dict))
    keywords = _keyword_candidates_from_text(
        "\n".join(text_parts),
        limit=int(settings.get("max_per_category", 4)),
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "keywords": keywords,
        "returncode": result.returncode,
        "source_status": payload.get("source_status", {}),
        "error": (result.stderr or result.stdout)[-1000:] if result.returncode else "",
    }


def fetch_agent_reach_keywords(
    config: dict[str, Any], category: str, query_terms: list[str]
) -> dict[str, Any]:
    settings = ((config.get("trends", {}) or {}).get("agent_reach") or {})
    if settings.get("enabled", True) is False:
        return {"status": "disabled", "keywords": []}
    agent_reach = _resolve_executable(config, str(settings.get("binary") or "workspace/tool_venvs/agent-reach/bin/agent-reach"))
    if not agent_reach:
        agent_reach = _resolve_executable(config, "agent-reach")
    doctor_status = "not_run"
    if agent_reach:
        try:
            doctor = subprocess.run(
                [agent_reach, "doctor", "--json"],
                check=False,
                text=True,
                capture_output=True,
                timeout=int(settings.get("doctor_timeout_sec", 20)),
            )
            doctor_status = "ok" if doctor.returncode == 0 else "warn"
        except (OSError, subprocess.TimeoutExpired):
            doctor_status = "error"
    mcporter = _resolve_executable(config, str(settings.get("mcporter_binary") or "mcporter"))
    if not mcporter:
        return {"status": "unavailable", "keywords": [], "doctor": doctor_status, "error": "mcporter not found"}
    query = str(settings.get("query_template") or "Brazil {category} trending topics {query}").format(
        category=category,
        query=" ".join(query_terms[:2]),
    )
    try:
        result = subprocess.run(
            [mcporter, "call", "exa.web_search_exa", f"query={query}", f"numResults={int(settings.get('num_results', 5))}"],
            check=False,
            text=True,
            capture_output=True,
            timeout=int(settings.get("timeout_sec", 45)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "error", "keywords": [], "doctor": doctor_status, "error": str(error)}
    keywords = _keyword_candidates_from_text(
        result.stdout,
        limit=int(settings.get("max_per_category", 4)),
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "keywords": keywords,
        "doctor": doctor_status,
        "returncode": result.returncode,
        "error": (result.stderr or result.stdout)[-1000:] if result.returncode else "",
    }


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
    category_queries = trend_category_queries(config)
    if category_queries:
        today = trends_today(config)
        per_category_limit = max(1, int(settings.get("top_n_per_category") or settings.get("top_n", 10)))
        categorized: dict[str, list[str]] = {}
        diagnostics: dict[str, Any] = {}
        all_entries: list[dict[str, str]] = []
        for category, query_terms in category_queries.items():
            values: list[dict[str, str]] = []
            try:
                for keyword in _pytrends_keywords(config, query_terms, client_factory=client_factory):
                    values.append({"keyword": keyword, "source": f"google_trends:{category}"})
            except Exception as error:
                diagnostics.setdefault(category, {})["google_trends_error"] = str(error)
            last30days = fetch_last30days_keywords(config, category, query_terms)
            diagnostics.setdefault(category, {})["last30days"] = {
                key: value for key, value in last30days.items() if key != "keywords"
            }
            values.extend(
                {"keyword": keyword, "source": f"last30days-skill:{category}"}
                for keyword in last30days.get("keywords", [])
            )
            agent_reach = fetch_agent_reach_keywords(config, category, query_terms)
            diagnostics.setdefault(category, {})["agent_reach"] = {
                key: value for key, value in agent_reach.items() if key != "keywords"
            }
            values.extend(
                {"keyword": keyword, "source": f"agent-reach:{category}"}
                for keyword in agent_reach.get("keywords", [])
            )

            seen_category: set[str] = set()
            for item in values:
                keyword = _clean_keyword_candidate(item["keyword"])
                if not keyword:
                    continue
                normalized = keyword.casefold()
                if normalized in seen_category:
                    continue
                seen_category.add(normalized)
                entry = {"keyword": keyword, "category": category, "source": item["source"]}
                all_entries.append(entry)
                categorized.setdefault(category, []).append(keyword)
                if len(categorized[category]) >= per_category_limit:
                    break

        if not all_entries:
            cached = list_hot_keywords(config, today)
            return {
                "status": "cached" if cached else "unavailable",
                "date": today,
                "count": len(cached),
                "cached": True,
                "sources": ["google_trends", "last30days-skill", "agent-reach"],
                "diagnostics": diagnostics,
            }
        connection = connect_db(config)
        for item in all_entries:
            connection.execute(
                "INSERT OR IGNORE INTO hot_keywords(keyword,date,source,created_at) "
                "VALUES(?,?,?,?)",
                (item["keyword"], today, item["source"], now_iso()),
            )
        connection.commit()
        keyword_sync = sync_categorized_hot_keywords_to_keyword_file(config, categorized)
        return {
            "status": "updated",
            "date": today,
            "count": len(all_entries),
            "categories": {category: len(values) for category, values in categorized.items()},
            "keywords": categorized,
            "sources": ["google_trends", "last30days-skill", "agent-reach"],
            "excluded_categories": list(settings.get("excluded_categories", DEFAULT_TREND_EXCLUDED_CATEGORIES)),
            "cached": False,
            "keyword_sync": keyword_sync,
            "diagnostics": diagnostics,
        }

    query_terms = [
        str(value).strip() for value in settings.get("keywords", ["futebol Brasil"])
        if str(value).strip()
    ]
    if not query_terms:
        raise ValueError("trends.keywords must contain at least one query")
    today = trends_today(config)
    source = str(settings.get("source") or "pytrends")
    pytrends_values: list[str] = []
    rss_values: list[str] = []
    pytrends_error: Exception | None = None
    rss_error: Exception | None = None
    try:
        pytrends_values = _pytrends_keywords(config, query_terms, client_factory=client_factory)
        if not pytrends_values:
            raise RuntimeError("pytrends returned no rising queries")
    except Exception as error:
        pytrends_error = error

    try:
        rss_values, rss_source = _fetch_rss_trending_keywords_with_source(config)
        if not rss_values:
            raise RuntimeError("Google Trends RSS returned no keywords")
    except Exception as error:
        rss_error = error
        rss_source = ""

    values: list[str] = []
    seen: set[str] = set()
    for value in [*pytrends_values, *rss_values]:
        normalized = value.casefold()
        if normalized not in seen:
            seen.add(normalized)
            values.append(value)
    values = values[: max(1, int(settings.get("top_n", 10)))]
    if pytrends_values and rss_values:
        source = f"pytrends+{rss_source}"
    elif rss_values:
        source = rss_source
    if not values:
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
            "keywords": values,
            "source": source,
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
