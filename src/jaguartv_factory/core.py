from __future__ import annotations

import hashlib
import html
import json
import math
import os
import copy
import random
import re
import selectors
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import yaml
from PIL import Image, ImageDraw, ImageFont

from .compliance import assert_render_allowed
from .highlight import analyze_video
from .reaction import compose_reaction, reaction_spec
from .scoring import score_candidate_v2
from .server_store import archive_review_package, storage_root
from .source_outro import detect_source_outro, review_source_outro_summary
from .strategy import render_audio_mode, resolve_production_strategy
from .pyvideotrans_adapter import (
    pyvideotrans_enabled,
    pyvideotrans_stt,
    pyvideotrans_translate_srt,
    pyvideotrans_tts,
)
from .workbuddy_adapter import (
    classify_chinese_audio,
    demucs_backing_track,
    detect_chinese_text_regions,
    edge_tts_ptbr,
    prepare_ocr_blurred_segment,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "pipeline.yaml"
LOCALIZABLE_CHINESE_AUDIO_PLATFORMS = {"bilibili", "douyin"}


def scoring_config_path(config: dict[str, Any]) -> str | None:
    configured = str(config.get("selection", {}).get("scoring_file", "config/scoring.yaml"))
    path = resolve_config_path(config, configured)
    return str(path) if path.exists() else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_command(
    args: list[str], *, cwd: Path | None = None, check: bool = True, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    if args and args[0] in {"ffmpeg", "ffprobe", "yt-dlp"}:
        args = [require_binary(args[0]), *args[1:]]
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True, timeout=timeout)


def common_binary_candidates(name: str) -> list[Path]:
    machine = "arm64" if os.uname().machine in {"arm64", "aarch64"} else "x64"
    system = {"darwin": "darwin", "linux": "linux", "win32": "win32"}.get(sys.platform, sys.platform)
    if name == "ffmpeg":
        return [
            ROOT / "node_modules" / "ffmpeg-static" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"),
            Path("/Applications/CapCut.app/Contents/Resources/ffmpeg"),
            Path("/Applications/VideoFusion-macOS.app/Contents/Resources/ffmpeg"),
            Path("/Applications/BlueStacks.app/Contents/MacOS/ffmpeg"),
        ]
    if name == "ffprobe":
        suffix = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
        return [ROOT / "node_modules" / "ffprobe-static" / "bin" / system / machine / suffix]
    return []


def require_binary(name: str) -> str:
    if name == "yt-dlp":
        try:
            from .sources import yt_dlp_binary

            return yt_dlp_binary()
        except Exception:
            pass
    sibling = Path(sys.executable).parent / name
    if sibling.exists():
        return str(sibling)
    path = shutil.which(name)
    if path:
        return path
    for candidate in common_binary_candidates(name):
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(f"Missing required binary: {name}")


def platform_from_url(url: str) -> str:
    lowered = url.lower()
    if "youtu.be" in lowered or "youtube.com" in lowered:
        return "youtube"
    if "bilibili.com" in lowered or "b23.tv" in lowered:
        return "bilibili"
    if "douyin.com" in lowered:
        return "douyin"
    if "xiaohongshu.com" in lowered or "xhslink.com" in lowered:
        return "xiaohongshu"
    if "facebook.com" in lowered or "fb.watch" in lowered:
        return "facebook"
    if "tiktok.com" in lowered:
        return "tiktok"
    return ""


def yt_dlp_extra_args(config: dict[str, Any], url: str = "", platform_hint: str = "") -> list[str]:
    platform = platform_from_url(url) or str(platform_hint or "").strip().lower()
    if not platform:
        return []
    try:
        from .sources import YtDlpAdapter, get_adapter

        adapter = get_adapter(platform, config)
        if isinstance(adapter, YtDlpAdapter):
            return [*adapter._cookie_args(), *adapter._js_runtime_args()]
    except Exception:
        pass
    return []


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    config_path = Path(path or DEFAULT_CONFIG).expanduser().resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    config["_path"] = str(config_path)
    config["_root"] = str(config_path.parent.parent)
    return config


def resolve_config_path(config: dict[str, Any], value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return Path(config["_root"]) / path


def workspace_dir(config: dict[str, Any]) -> Path:
    value = config.get("run", {}).get("workspace", "workspace")
    path = resolve_config_path(config, value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def connect_db(config: dict[str, Any]) -> sqlite3.Connection:
    db_path = workspace_dir(config) / "factory.db"
    connection = sqlite3.connect(db_path, timeout=float(config.get("run", {}).get("sqlite_timeout_sec", 30)))
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={int(float(config.get('run', {}).get('sqlite_timeout_sec', 30)) * 1000)}")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidates (
          id TEXT PRIMARY KEY,
          parent_id TEXT,
          platform TEXT NOT NULL,
          source_id TEXT,
          url TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          duration REAL,
          view_count INTEGER NOT NULL DEFAULT 0,
          detected_language TEXT,
          score REAL NOT NULL DEFAULT 0,
          status TEXT NOT NULL,
          published_flag INTEGER NOT NULL DEFAULT 0,
          metadata_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS candidates_platform_source
          ON candidates(platform, source_id) WHERE source_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS seen_sources (
          platform TEXT NOT NULL,
          source_key TEXT NOT NULL,
          source_id TEXT,
          url TEXT NOT NULL DEFAULT '',
          first_candidate_id TEXT NOT NULL DEFAULT '',
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          PRIMARY KEY(platform, source_key)
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS publications (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          package_id TEXT NOT NULL DEFAULT '',
          asset_id TEXT NOT NULL DEFAULT '',
          variant TEXT NOT NULL DEFAULT '',
          source_platform TEXT NOT NULL DEFAULT '',
          platform TEXT NOT NULL,
          account TEXT NOT NULL DEFAULT '',
          account_label TEXT NOT NULL DEFAULT '',
          channel_id TEXT NOT NULL DEFAULT '',
          scheduled_at TEXT,
          published_at TEXT,
          status TEXT NOT NULL DEFAULT 'QUEUED',
          title TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          tags_json TEXT NOT NULL DEFAULT '[]',
          privacy_status TEXT NOT NULL DEFAULT '',
          youtube_video_id TEXT NOT NULL DEFAULT '',
          youtube_url TEXT NOT NULL DEFAULT '',
          post_url TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '',
          error_json TEXT NOT NULL DEFAULT '{}',
          timezone TEXT NOT NULL DEFAULT '',
          reviewer TEXT NOT NULL DEFAULT '',
          review_decision_at TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(candidate_id, platform, account, scheduled_at)
        );
        CREATE TABLE IF NOT EXISTS performance_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          platform TEXT NOT NULL,
          captured_at TEXT NOT NULL,
          views INTEGER NOT NULL DEFAULT 0,
          likes INTEGER NOT NULL DEFAULT 0,
          comments INTEGER NOT NULL DEFAULT 0,
          shares INTEGER NOT NULL DEFAULT 0,
          clicks INTEGER NOT NULL DEFAULT 0,
          installs INTEGER NOT NULL DEFAULT 0,
          registrations INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS performance_candidate_platform
          ON performance_snapshots(candidate_id, platform, captured_at DESC);
        CREATE TABLE IF NOT EXISTS workers (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          role TEXT NOT NULL,
          host TEXT NOT NULL,
          status TEXT NOT NULL,
          current_job TEXT NOT NULL DEFAULT '',
          last_seen TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS render_jobs (
          id TEXT PRIMARY KEY,
          candidate_id TEXT NOT NULL,
          variant TEXT NOT NULL DEFAULT '',
          engine TEXT NOT NULL,
          status TEXT NOT NULL,
          progress REAL NOT NULL DEFAULT 0,
          output_path TEXT NOT NULL DEFAULT '',
          cancel_file TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS render_jobs_candidate
          ON render_jobs(candidate_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS conversion_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          platform TEXT NOT NULL DEFAULT '',
          hook_version TEXT NOT NULL DEFAULT '',
          event_type TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          visitor_id TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS conversion_candidate_type
          ON conversion_events(candidate_id, event_type, occurred_at DESC);
        CREATE TABLE IF NOT EXISTS feedback_actions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT,
          keyword TEXT NOT NULL DEFAULT '',
          action_type TEXT NOT NULL,
          reason TEXT NOT NULL,
          score REAL NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'PROPOSED',
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hot_keywords (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          keyword TEXT NOT NULL,
          date TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'google_trends',
          created_at TEXT NOT NULL,
          UNIQUE(keyword, date, source)
        );
        CREATE TABLE IF NOT EXISTS callback_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          video_id TEXT NOT NULL DEFAULT '',
          publisher TEXT NOT NULL,
          platform TEXT NOT NULL,
          views INTEGER NOT NULL DEFAULT 0,
          clicks INTEGER NOT NULL DEFAULT 0,
          registrations INTEGER NOT NULL DEFAULT 0,
          extra_data TEXT NOT NULL DEFAULT '{}',
          callback_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS callback_candidate
          ON callback_logs(candidate_id);
        CREATE INDEX IF NOT EXISTS callback_at
          ON callback_logs(callback_at);
        CREATE TABLE IF NOT EXISTS download_claims (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          asset_id TEXT NOT NULL DEFAULT '',
          filename TEXT NOT NULL DEFAULT '',
          variant TEXT NOT NULL DEFAULT '',
          publisher TEXT NOT NULL,
          publish_platform TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          downloaded_at TEXT NOT NULL,
          metrics_updated_at TEXT,
          views INTEGER NOT NULL DEFAULT 0,
          clicks INTEGER NOT NULL DEFAULT 0,
          registrations INTEGER NOT NULL DEFAULT 0,
          extra_data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS download_claim_candidate
          ON download_claims(candidate_id, downloaded_at DESC);
        CREATE TABLE IF NOT EXISTS youtube_channel_auths (
          account TEXT PRIMARY KEY,
          channel_id TEXT NOT NULL DEFAULT '',
          channel_title TEXT NOT NULL DEFAULT '',
          scopes TEXT NOT NULL DEFAULT '',
          encrypted_refresh_token TEXT NOT NULL DEFAULT '',
          token_type TEXT NOT NULL DEFAULT '',
          expires_in INTEGER NOT NULL DEFAULT 0,
          authorized_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS x_oauth_states (
          state TEXT PRIMARY KEY,
          account TEXT NOT NULL,
          code_verifier TEXT NOT NULL,
          redirect_uri TEXT NOT NULL DEFAULT '',
          scopes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS x_account_auths (
          account TEXT PRIMARY KEY,
          x_user_id TEXT NOT NULL DEFAULT '',
          username TEXT NOT NULL DEFAULT '',
          display_name TEXT NOT NULL DEFAULT '',
          scopes TEXT NOT NULL DEFAULT '',
          encrypted_access_token TEXT NOT NULL DEFAULT '',
          encrypted_refresh_token TEXT NOT NULL DEFAULT '',
          token_type TEXT NOT NULL DEFAULT '',
          expires_in INTEGER NOT NULL DEFAULT 0,
          expires_at TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'PENDING_CONFIRMATION',
          authorized_at TEXT NOT NULL,
          confirmed_at TEXT,
          revoked_at TEXT,
          updated_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    candidate_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(candidates)")
    }
    if "published_flag" not in candidate_columns:
        connection.execute(
            "ALTER TABLE candidates ADD COLUMN published_flag INTEGER NOT NULL DEFAULT 0"
        )
        connection.commit()
    publication_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(publications)")
    }
    publication_column_sql = {
        "package_id": "ALTER TABLE publications ADD COLUMN package_id TEXT NOT NULL DEFAULT ''",
        "asset_id": "ALTER TABLE publications ADD COLUMN asset_id TEXT NOT NULL DEFAULT ''",
        "variant": "ALTER TABLE publications ADD COLUMN variant TEXT NOT NULL DEFAULT ''",
        "source_platform": "ALTER TABLE publications ADD COLUMN source_platform TEXT NOT NULL DEFAULT ''",
        "account_label": "ALTER TABLE publications ADD COLUMN account_label TEXT NOT NULL DEFAULT ''",
        "channel_id": "ALTER TABLE publications ADD COLUMN channel_id TEXT NOT NULL DEFAULT ''",
        "title": "ALTER TABLE publications ADD COLUMN title TEXT NOT NULL DEFAULT ''",
        "description": "ALTER TABLE publications ADD COLUMN description TEXT NOT NULL DEFAULT ''",
        "tags_json": "ALTER TABLE publications ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'",
        "privacy_status": "ALTER TABLE publications ADD COLUMN privacy_status TEXT NOT NULL DEFAULT ''",
        "youtube_video_id": "ALTER TABLE publications ADD COLUMN youtube_video_id TEXT NOT NULL DEFAULT ''",
        "youtube_url": "ALTER TABLE publications ADD COLUMN youtube_url TEXT NOT NULL DEFAULT ''",
        "error_json": "ALTER TABLE publications ADD COLUMN error_json TEXT NOT NULL DEFAULT '{}'",
        "timezone": "ALTER TABLE publications ADD COLUMN timezone TEXT NOT NULL DEFAULT ''",
        "reviewer": "ALTER TABLE publications ADD COLUMN reviewer TEXT NOT NULL DEFAULT ''",
        "review_decision_at": "ALTER TABLE publications ADD COLUMN review_decision_at TEXT",
    }
    for column, statement in publication_column_sql.items():
        if column not in publication_columns:
            connection.execute(statement)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS publications_account_status ON publications(account,status,scheduled_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS publications_candidate_account ON publications(candidate_id,platform,account,status)"
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO seen_sources
        (platform,source_key,source_id,url,first_candidate_id,first_seen_at,last_seen_at)
        SELECT platform,
               CASE
                 WHEN source_id IS NOT NULL AND source_id != '' THEN 'id:' || source_id
                 ELSE 'url:' || url
               END,
               source_id,
               url,
               id,
               created_at,
               updated_at
        FROM candidates
        """
    )
    connection.commit()
    return connection


def append_event(connection: sqlite3.Connection, candidate_id: str, event_type: str, payload: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (candidate_id, event_type, json.dumps(payload, ensure_ascii=False), now_iso()),
    )
    connection.commit()


def upsert_render_job(
    config: dict[str, Any],
    job_id: str,
    *,
    candidate_id: str,
    variant: str,
    engine: str,
    status: str,
    progress: float = 0.0,
    output_path: str = "",
    cancel_file: str = "",
    error: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    timestamp = now_iso()
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO render_jobs
          (id,candidate_id,variant,engine,status,progress,output_path,cancel_file,error,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          candidate_id=excluded.candidate_id,
          variant=excluded.variant,
          engine=excluded.engine,
          status=excluded.status,
          progress=excluded.progress,
          output_path=excluded.output_path,
          cancel_file=excluded.cancel_file,
          error=excluded.error,
          metadata_json=excluded.metadata_json,
          updated_at=excluded.updated_at
        """,
        (
            job_id,
            candidate_id,
            variant,
            engine,
            status,
            max(0.0, min(1.0, float(progress))),
            output_path,
            cancel_file,
            error,
            json.dumps(metadata or {}, ensure_ascii=False),
            timestamp,
            timestamp,
        ),
    )
    connection.commit()
    connection.close()


def update_render_job(
    config: dict[str, Any],
    job_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    error: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
) -> None:
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM render_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        connection.close()
        return
    metadata = json.loads(row["metadata_json"] or "{}")
    if metadata_patch:
        metadata.update(metadata_patch)
    connection.execute(
        """
        UPDATE render_jobs
        SET status=?,progress=?,error=?,metadata_json=?,updated_at=?
        WHERE id=?
        """,
        (
            status or row["status"],
            max(0.0, min(1.0, float(progress if progress is not None else row["progress"]))),
            error if error is not None else row["error"],
            json.dumps(metadata, ensure_ascii=False),
            now_iso(),
            job_id,
        ),
    )
    connection.commit()
    connection.close()


def candidate_id(platform: str, source_id: str | None, url: str) -> str:
    stable = f"{platform}:{source_id or url}"
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def seen_source_key(source_id: str | None, url: str) -> str:
    source = str(source_id or "").strip()
    if source:
        return f"id:{source}"
    return f"url:{normalize_source_url(url)}"


def normalize_source_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").strip()
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    kept = [
        (key, value)
        for key, value in query
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urllib.parse.urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or parsed.path,
        "",
        urllib.parse.urlencode(kept, doseq=True),
        "",
    ))


def remember_seen_source(
    connection: sqlite3.Connection,
    platform: str,
    source_id: str | None,
    url: str,
    candidate: str,
    *,
    timestamp: str | None = None,
) -> bool:
    """Record source discovery permanently; returns False when seen before."""
    stamp = timestamp or now_iso()
    key = seen_source_key(source_id, url)
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO seen_sources
        (platform,source_key,source_id,url,first_candidate_id,first_seen_at,last_seen_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        (platform, key, source_id, normalize_source_url(url), candidate, stamp, stamp),
    )
    if cursor.rowcount:
        return True
    connection.execute(
        "UPDATE seen_sources SET last_seen_at=? WHERE platform=? AND source_key=?",
        (stamp, platform, key),
    )
    return False


def infer_platform(info: dict[str, Any]) -> str:
    extractor = str(info.get("extractor_key") or info.get("extractor") or "unknown").lower()
    webpage = str(info.get("webpage_url") or info.get("url") or "").lower()
    for name, needles in {
        "youtube": ("youtube", "youtu.be"),
        "bilibili": ("bilibili", "b23.tv"),
        "douyin": ("douyin",),
        "tiktok": ("tiktok",),
        "facebook": ("facebook", "fb.watch"),
        "kwai": ("kwai", "kuaishou"),
    }.items():
        if any(needle in extractor or needle in webpage for needle in needles):
            return name
    return extractor.split(":", 1)[0] or "unknown"


def likely_language(text: str) -> tuple[str, float]:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return "unknown", 0.0
    if re.search(r"[\u4e00-\u9fff]", compact):
        return "zh", 0.92
    lowered = compact.lower()
    pt_tokens = (
        "você", "vocês", "não", "que", "para", "com", "uma", "brasil", "futebol",
        "olha", "aconteceu", "aqui", "gostou", "descubra", "conteúdo", "conteúdos",
        "hoje", "incrível", "incríveis", "mais", "este", "esta", "isso", "só",
    )
    hits = sum(token in lowered for token in pt_tokens)
    if hits >= 3:
        return "pt", min(0.95, 0.55 + hits * 0.08)
    if re.search(r"[a-zA-Z]", compact):
        return "en", 0.55
    return "unknown", 0.2


def yt_dlp_search(query: str, platform: str, limit: int) -> list[dict[str, Any]]:
    yt_dlp = require_binary("yt-dlp")
    prefix = "ytsearch" if platform == "youtube" else "bilisearch"
    target = f"{prefix}{limit}:{query}"
    result = run_command(
        [yt_dlp, "--force-ipv4", "--flat-playlist", "--dump-single-json", "--no-warnings", target],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{platform} search failed")
    payload = json.loads(result.stdout)
    return [entry for entry in payload.get("entries", []) if entry]


DEFAULT_PLATFORM_LANGUAGE = {
    "youtube": ["en", "es", "pt"],
    "bilibili": ["zh-CN", "zh"],
    "douyin": ["zh-CN", "zh"],
    "xiaohongshu": ["zh-CN", "zh"],
}


def terms_for_platform(
    terms_by_language: dict[str, list[str]], platform: str, config: dict[str, Any]
) -> list[str]:
    """Route keyword languages to platforms (zh terms -> douyin/bilibili,
    en/es terms -> youtube). Falls back to every configured term."""
    routing = {**DEFAULT_PLATFORM_LANGUAGE, **(config.get("sources", {}).get("platform_language") or {})}
    wanted = routing.get(platform)
    if not wanted:
        return [term for terms in terms_by_language.values() for term in terms]
    selected: list[str] = []
    for language in wanted:
        selected.extend(terms_by_language.get(language, []))
    if not selected:  # nothing in the preferred languages: use everything
        selected = [term for terms in terms_by_language.values() for term in terms]
    return selected


WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def category_active_today(category_config: dict[str, Any], today_index: int | None = None) -> bool:
    """Keyword groups can be paused (enabled: false) or scheduled to
    specific weekdays (days: [mon, ...]) for daily rotation."""
    if category_config.get("enabled") is False:
        return False
    days = category_config.get("days")
    if not days:
        return True
    index = datetime.now().weekday() if today_index is None else today_index
    return WEEKDAY_KEYS[index] in {str(day).strip().lower()[:3] for day in days}


def discover(
    config: dict[str, Any],
    *,
    platforms: Iterable[str] | None = None,
    limit: int | None = None,
    keyword_overrides: Iterable[str] | None = None,
) -> dict[str, Any]:
    from .sources import SEARCHABLE_PLATFORMS, SourceError, get_adapter

    connection = connect_db(config)
    keyword_path = resolve_config_path(config, config["sources"]["keywords_file"])
    keywords = yaml.safe_load(keyword_path.read_text(encoding="utf-8")) or {}
    override_terms = [str(term).strip() for term in (keyword_overrides or []) if str(term).strip()]
    if override_terms:
        keywords = {
            "hot_keyword_discovery": {
                "weight": 1.0,
                "enabled": True,
                "terms": {"pt": override_terms},
            }
        }
    enabled = list(platforms or config.get("sources", {}).get("enabled", []))
    supported = [platform for platform in enabled if platform in SEARCHABLE_PLATFORMS]
    per_query = int(limit or config.get("discovery", {}).get("max_candidates_per_keyword", 10))
    excluded_languages = {
        str(language).strip().lower()
        for language in (config.get("sources", {}) or {}).get("exclude_languages", [])
        if str(language).strip()
    }
    stats = {
        "discovered": 0, "inserted": 0, "language_rejected": 0,
        "market_rejected": 0, "too_long": 0, "errors": 0, "categories_skipped": 0,
        "duplicates": 0, "error_details": [],
    }
    for platform in enabled:
        if platform not in SEARCHABLE_PLATFORMS:
            stats["errors"] += 1
            stats["error_details"].append({
                "platform": platform,
                "keyword": "",
                "reason": f"{platform} does not support keyword discovery; paste a concrete video URL instead",
            })
    for category, category_config in keywords.items():
        if not category_active_today(category_config or {}):
            stats["categories_skipped"] += 1
            continue
        terms_by_language = category_config.get("terms", {})
        for platform in supported:
            try:
                adapter = get_adapter(platform, config)
            except SourceError as error:
                stats["errors"] += 1
                detail = str(error)
                stats["error_details"].append({"platform": platform, "keyword": "", "reason": detail})
                print(f"WARN {platform}: {detail}")
                continue
            for term in terms_for_platform(terms_by_language, platform, config):
                try:
                    entries = adapter.search(str(term), per_query)
                except Exception as error:
                    stats["errors"] += 1
                    detail = str(error)
                    stats["error_details"].append({"platform": platform, "keyword": str(term), "reason": detail})
                    print(f"WARN {platform} search {term!r}: {detail}")
                    continue
                for info in entries:
                    stats["discovered"] += 1
                    url = str(info.get("webpage_url") or info.get("url") or "")
                    source_id = str(info.get("id") or "") or None
                    actual_platform = infer_platform(info) or platform
                    cid = candidate_id(actual_platform, source_id, url)
                    title = str(info.get("title") or "")
                    timestamp = now_iso()
                    if not remember_seen_source(connection, actual_platform, source_id, url, cid, timestamp=timestamp):
                        stats["duplicates"] += 1
                        continue
                    market_rejection = candidate_market_rejection(config, info, keyword=str(term))
                    if market_rejection:
                        stats["market_rejected"] += 1
                        continue
                    language, confidence = likely_language(f"{title} {info.get('description') or ''}")
                    rejected = language.lower() in excluded_languages and confidence >= 0.7
                    too_long = candidate_too_long(config, info.get("duration"))
                    status = "LANGUAGE_REJECTED" if rejected else ("TOO_LONG" if too_long else "DISCOVERED")
                    if rejected:
                        stats["language_rejected"] += 1
                    if too_long:
                        stats["too_long"] += 1
                    score, breakdown = score_candidate_v2(info, str(term), scoring_config_path(config))
                    values = (
                        cid,
                        actual_platform,
                        source_id,
                        url,
                        title,
                        str(info.get("description") or ""),
                        info.get("duration"),
                        int(info.get("view_count") or 0),
                        language,
                        score,
                        status,
                        json.dumps({
                            **info, "category": category, "keyword": term,
                            "duration_gate": {
                                "max_source_duration_sec": source_duration_limit(config),
                                "too_long": too_long,
                            },
                            "score_breakdown": breakdown,
                        }, ensure_ascii=False),
                        timestamp,
                        timestamp,
                    )
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO candidates
                        (id,platform,source_id,url,title,description,duration,view_count,detected_language,score,status,metadata_json,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        values,
                    )
                    stats["inserted"] += cursor.rowcount
    connection.commit()
    return stats


def list_candidates(config: dict[str, Any], status: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    connection = connect_db(config)
    if status:
        return connection.execute(
            "SELECT * FROM candidates WHERE status=? ORDER BY score DESC, created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return connection.execute(
        "SELECT * FROM candidates ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()


def register_url_stub_candidate(
    config: dict[str, Any],
    url: str,
    *,
    requested_platform: str | None = None,
    inspect_error: str = "",
) -> str:
    platform = platform_from_url(url) or str(requested_platform or "").strip().lower() or "unknown"
    cid = candidate_id(platform, None, url)
    parsed = urllib.parse.urlparse(url)
    title = f"{platform} URL import".strip()
    timestamp = now_iso()
    metadata = {
        "webpage_url": url,
        "requested_platform": str(requested_platform or "").strip().lower(),
        "detected_platform": platform,
        "ingest_mode": "url_stub_after_inspect_failure",
        "inspect_error": inspect_error[-2000:],
        "duration_gate": {
            "max_source_duration_sec": source_duration_limit(config),
            "too_long": False,
            "duration_unknown": True,
        },
        "score_breakdown": {"total": 50, "source": "manual_url_stub"},
    }
    connection = connect_db(config)
    existing = connection.execute("SELECT created_at FROM candidates WHERE id=?", (cid,)).fetchone()
    created_at = existing["created_at"] if existing else timestamp
    remember_seen_source(connection, platform, None, url, cid, timestamp=timestamp)
    connection.execute(
        """INSERT OR REPLACE INTO candidates
        (id,platform,source_id,url,title,description,duration,view_count,detected_language,score,status,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            cid,
            platform,
            None,
            url,
            title,
            f"Manual URL import from {parsed.netloc or platform}; metadata probe failed.",
            None,
            0,
            "",
            50,
            "DISCOVERED",
            json.dumps(metadata, ensure_ascii=False),
            created_at,
            timestamp,
        ),
    )
    append_event(connection, cid, "URL_STUB_INGESTED", {"url": url, "inspect_error": inspect_error[-2000:]})
    connection.commit()
    return cid


def inspect_url(
    config: dict[str, Any],
    url: str,
    requested_platform: str | None = None,
    *,
    allow_stub: bool = False,
) -> str:
    if "xiaohongshu.com" in url or "xhslink.com" in url:
        return inspect_xhs_url(config, url)
    yt_dlp = require_binary("yt-dlp")
    platform_hint = str(requested_platform or "").strip().lower()
    result = run_command([
        yt_dlp, "--force-ipv4", "--dump-single-json", "--skip-download",
        "--no-warnings", *yt_dlp_extra_args(config, url, platform_hint), url,
    ], check=False)
    if result.returncode != 0:
        if allow_stub:
            return register_url_stub_candidate(
                config,
                url,
                requested_platform=requested_platform,
                inspect_error=result.stderr.strip() or "Unable to inspect URL",
            )
        raise RuntimeError(result.stderr.strip() or "Unable to inspect URL")
    info = json.loads(result.stdout)
    platform = infer_platform(info)
    requested_platform = str(requested_platform or "").strip().lower()
    if requested_platform and platform == "unknown":
        platform = requested_platform
    source_id = str(info.get("id") or "") or None
    cid = candidate_id(platform, source_id, url)
    title = str(info.get("title") or "")
    language, _ = likely_language(f"{title} {info.get('description') or ''}")
    timestamp = now_iso()
    score, breakdown = score_candidate_v2(info, title, scoring_config_path(config))
    connection = connect_db(config)
    seen = remember_seen_source(connection, platform, source_id, url, cid, timestamp=timestamp)
    if not seen:
        existing = connection.execute(
            "SELECT id FROM candidates WHERE platform=? AND (source_id=? OR url=?) ORDER BY created_at LIMIT 1",
            (platform, source_id, url),
        ).fetchone()
        if existing:
            connection.commit()
            return str(existing["id"])
    connection.execute(
        """INSERT OR REPLACE INTO candidates
        (id,platform,source_id,url,title,description,duration,view_count,detected_language,score,status,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            cid, platform, source_id, url, title, str(info.get("description") or ""), info.get("duration"),
            int(info.get("view_count") or 0), language, score, "DISCOVERED",
            json.dumps({
                **info,
                "requested_platform": requested_platform,
                "detected_platform": platform,
                "duration_gate": {
                    "max_source_duration_sec": source_duration_limit(config),
                    "too_long": candidate_too_long(config, info.get("duration")),
                },
                "score_breakdown": breakdown,
            }, ensure_ascii=False), timestamp, timestamp,
        ),
    )
    if candidate_too_long(config, info.get("duration")):
        connection.execute("UPDATE candidates SET status='TOO_LONG',updated_at=? WHERE id=?", (now_iso(), cid))
    connection.commit()
    return cid


def ingest_uploaded_media(config: dict[str, Any], upload: dict[str, Any]) -> str:
    """Register a private server upload as an already-downloaded candidate."""
    media = Path(str(upload.get("path") or "")).expanduser().resolve()
    if not media.is_file():
        raise ValueError("uploaded media file does not exist")
    upload_id = str(upload.get("id") or "").strip()
    if not upload_id:
        raise ValueError("upload id is required")
    cid = f"upload_{upload_id[:16]}"
    work = workspace_dir(config) / "jobs" / cid
    work.mkdir(parents=True, exist_ok=True)
    source = work / f"source{media.suffix.lower()}"
    if not source.exists():
        try:
            os.link(media, source)
        except OSError:
            shutil.copy2(media, source)
    duration = media_duration(media)
    too_long = candidate_too_long(config, duration)
    timestamp = now_iso()
    title = str(upload.get("original_filename") or media.name)
    metadata = {
        "upload_id": upload_id,
        "original_filename": title,
        "server_upload": True,
        "uploaded_at": upload.get("uploaded_at") or timestamp,
        "private_storage_uri": upload.get("storage_uri") or "",
        "duration": duration,
        "duration_gate": {
            "max_source_duration_sec": source_duration_limit(config),
            "too_long": too_long,
        },
        "score_breakdown": {"total": 100, "source": "manual_server_upload"},
    }
    connection = connect_db(config)
    existing = connection.execute("SELECT created_at FROM candidates WHERE id=?", (cid,)).fetchone()
    created_at = existing["created_at"] if existing else timestamp
    connection.execute(
        """INSERT OR REPLACE INTO candidates
        (id,platform,source_id,url,title,description,duration,view_count,detected_language,score,status,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            cid,
            "server_upload",
            upload_id,
            f"server-upload://{upload_id}",
            title,
            "Private source uploaded through factory.jarg.top",
            duration,
            0,
            "",
            100,
            "TOO_LONG" if too_long else "DOWNLOADED",
            json.dumps(metadata, ensure_ascii=False),
            created_at,
            timestamp,
        ),
    )
    append_event(connection, cid, "UPLOAD_INGESTED", {
        "upload_id": upload_id,
        "path": str(media),
        "bytes": media.stat().st_size,
        "duration": duration,
    })
    connection.commit()
    return cid


def inspect_xhs_url(config: dict[str, Any], url: str) -> str:
    """Register a Xiaohongshu note as a candidate via the XHS-Downloader service."""
    from .sources import get_adapter

    adapter = get_adapter("xiaohongshu", config)
    try:
        payload = adapter._post("/xhs/detail", {"url": url, "download": False})
    except Exception:
        from .browser_scraper import resolve_xiaohongshu_video

        payload = {"data": resolve_xiaohongshu_video(config, url)}
    data = payload.get("data") or {}
    title = str(data.get("作品标题") or data.get("title") or "")
    description = str(data.get("作品描述") or data.get("desc") or "")
    source_id = str(data.get("作品ID") or data.get("note_id") or "") or None
    cid = candidate_id("xiaohongshu", source_id, url)
    language, _ = likely_language(f"{title} {description}")
    info = {
        "id": source_id, "title": title, "description": description,
        "view_count": int(data.get("浏览量") or 0), "like_count": int(data.get("点赞数量") or 0),
        "comment_count": int(data.get("评论数量") or 0), "webpage_url": url,
    }
    duration = data.get("duration") or data.get("视频时长") or data.get("video_duration")
    score, breakdown = score_candidate_v2(info, title, scoring_config_path(config))
    timestamp = now_iso()
    connection = connect_db(config)
    connection.execute(
        """INSERT OR REPLACE INTO candidates
        (id,platform,source_id,url,title,description,duration,view_count,detected_language,score,status,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            cid, "xiaohongshu", source_id, url, title, description, duration,
            info["view_count"], language, score, "DISCOVERED",
            json.dumps({
                **data,
                "duration_gate": {
                    "max_source_duration_sec": source_duration_limit(config),
                    "too_long": candidate_too_long(config, duration),
                },
                "score_breakdown": breakdown,
            }, ensure_ascii=False), timestamp, timestamp,
        ),
    )
    if candidate_too_long(config, duration):
        connection.execute("UPDATE candidates SET status='TOO_LONG',updated_at=? WHERE id=?", (now_iso(), cid))
    connection.commit()
    return cid


def download_candidate(config: dict[str, Any], row: sqlite3.Row) -> Path:
    from .sources import SourceError, get_adapter

    work = workspace_dir(config) / "jobs" / row["id"]
    work.mkdir(parents=True, exist_ok=True)
    output = work / "source.%(ext)s"
    connection = connect_db(config)
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    duration = row["duration"] or metadata.get("duration")
    if candidate_too_long(config, duration):
        connection.execute("UPDATE candidates SET status='TOO_LONG',updated_at=? WHERE id=?", (now_iso(), row["id"]))
        append_event(connection, row["id"], "TOO_LONG", {
            "duration": duration,
            "max_source_duration_sec": source_duration_limit(config),
        })
        raise RuntimeError(
            f"source duration {duration}s exceeds max_source_duration_sec={source_duration_limit(config):.0f}"
        )
    direct_url = str(metadata.get("direct_media_url") or "").strip()
    direct_error = ""
    try:
        if direct_url:
            direct_output = work / "source.mp4"
            request = urllib.request.Request(direct_url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(request, timeout=120) as response, direct_output.open("wb") as handle:
                    shutil.copyfileobj(response, handle, length=1024 * 1024)
                if direct_output.stat().st_size <= 0:
                    raise RuntimeError("direct media response was empty")
            except Exception as error:
                direct_output.unlink(missing_ok=True)
                direct_error = str(error)
        if not next(work.glob("source.*"), None):
            adapter = get_adapter(row["platform"], config)
            adapter.download(row["url"], str(output))
    except (SourceError, RuntimeError) as error:
        connection.execute("UPDATE candidates SET status='DOWNLOAD_FAILED',updated_at=? WHERE id=?", (now_iso(), row["id"]))
        detail = f"direct={direct_error}; adapter={error}" if direct_error else str(error)
        append_event(connection, row["id"], "DOWNLOAD_FAILED", {"stderr": detail[-4000:]})
        raise RuntimeError(str(error)) from error
    media = next((path for path in work.glob("source.*") if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
    if not media:
        raise RuntimeError("download completed but no media file was found")
    try:
        probed_duration = media_duration(media)
    except Exception as error:
        media.unlink(missing_ok=True)
        connection.execute("UPDATE candidates SET status='DOWNLOAD_FAILED',updated_at=? WHERE id=?", (now_iso(), row["id"]))
        append_event(connection, row["id"], "DOWNLOAD_FAILED", {
            "stderr": f"downloaded media failed ffprobe validation: {error}"[-4000:],
        })
        connection.commit()
        raise RuntimeError("download completed but media file is not a valid video") from error
    if probed_duration <= 0:
        media.unlink(missing_ok=True)
        connection.execute("UPDATE candidates SET status='DOWNLOAD_FAILED',updated_at=? WHERE id=?", (now_iso(), row["id"]))
        append_event(connection, row["id"], "DOWNLOAD_FAILED", {"stderr": "downloaded media has zero duration"})
        connection.commit()
        raise RuntimeError("download completed but media file has zero duration")
    subtitle_result = run_command([
        require_binary("yt-dlp"), "--force-ipv4", "--skip-download", "--write-auto-subs", "--write-subs",
        "--sub-langs", "en,zh-Hans,zh-Hant,es,fr,de,ja,ko", "--convert-subs", "srt",
        *yt_dlp_extra_args(config, row["url"]), "-o", str(output), row["url"],
    ], check=False)
    connection.execute("UPDATE candidates SET status='DOWNLOADED',updated_at=? WHERE id=?", (now_iso(), row["id"]))
    append_event(connection, row["id"], "DOWNLOADED", {
        "path": str(media),
        "bytes": media.stat().st_size,
        "subtitles": "available" if subtitle_result.returncode == 0 else "unavailable",
    })
    return media


def download_top(config: dict[str, Any], limit: int, candidate: str | None = None) -> dict[str, int]:
    connection = connect_db(config)
    minimum = float(config.get("selection", {}).get("min_score", 0))
    if candidate:
        rows = connection.execute(
            "SELECT * FROM candidates WHERE id=? AND status IN ('DISCOVERED','DOWNLOAD_FAILED')", (candidate,)
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM candidates WHERE status='DISCOVERED' AND score>=? ORDER BY score DESC LIMIT ?",
            (minimum, limit),
        ).fetchall()
    stats = {"selected": len(rows), "downloaded": 0, "failed": 0}
    for row in rows:
        try:
            download_candidate(config, row)
            stats["downloaded"] += 1
        except Exception as error:
            stats["failed"] += 1
            print(f"WARN download {row['id']}: {error}")
    return stats


def parse_srt(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        stripped = re.sub(r"<[^>]+>", "", stripped)
        if not lines or stripped != lines[-1]:
            lines.append(stripped)
    return " ".join(lines)


def transcribe_with_whisper(media: Path, output_dir: Path, config: dict[str, Any] | None = None) -> str:
    if config and pyvideotrans_enabled(config, "stt"):
        try:
            srt = pyvideotrans_stt(config, media, output_dir / "source.pyvideotrans.srt")
            transcript = parse_srt(srt)
            if transcript.strip():
                (output_dir / "transcript_source.json").write_text(
                    json.dumps({"provider": "pyvideotrans", "subtitle": str(srt), "text": transcript}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return transcript
        except RuntimeError as error:
            (output_dir / "pyvideotrans_stt_fallback.json").write_text(
                json.dumps({"error": str(error)}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError("No subtitles found and faster-whisper is not installed") from error
    model_name = os.environ.get("JAGUARTV_WHISPER_MODEL", "tiny")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(media), vad_filter=True)
    transcript = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    (output_dir / "transcript_source.json").write_text(
        json.dumps({"model": model_name, "language": info.language, "probability": info.language_probability, "text": transcript}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return transcript


def source_text(
    work: Path,
    media: Path,
    fallback: str,
    *,
    enable_asr: bool = True,
    config: dict[str, Any] | None = None,
    allow_metadata_fallback: bool = True,
) -> str:
    subtitles = sorted([*work.glob("source*.srt"), *work.glob("source*.vtt")])
    for subtitle in subtitles:
        text = parse_srt(subtitle)
        if len(text) >= 30:
            return text
    if enable_asr:
        try:
            return transcribe_with_whisper(media, work, config)
        except RuntimeError:
            pass
    if not allow_metadata_fallback:
        payload = {
            "provider": "speech_required",
            "language": "unknown",
            "text": "",
            "fallback_rejected": fallback[:500],
        }
        (work / "transcript_source.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise RuntimeError(
            "Chinese localization requires real source subtitles or ASR transcript; "
            "metadata fallback is disabled to avoid repeated generic pt-BR narration"
        )
    payload = {"provider": "metadata_fallback", "language": "unknown", "text": fallback}
    (work / "transcript_source.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return fallback


def media_has_audio(path: Path) -> bool:
    result = run_command([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
        "stream=index", "-of", "csv=p=0", str(path),
    ], check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def choose_audio_strategy(
    config: dict[str, Any], work: Path, media: Path
) -> tuple[str, str, str]:
    """Return (mode, transcript, reason) for the render audio policy.

    `auto` only localizes when subtitles or ASR provide speech evidence. This
    prevents music-only clips from receiving invented narration and captions.
    """
    requested = str(config.get("audio", {}).get("source_mode", "auto")).strip().lower()
    if requested not in {"auto", "localize", "preserve"}:
        raise RuntimeError("audio.source_mode must be auto, localize, or preserve")

    subtitle_files = sorted([*work.glob("source*.srt"), *work.glob("source*.vtt")])
    for subtitle in subtitle_files:
        transcript = parse_srt(subtitle)
        if len(transcript) >= 30:
            return "localized", transcript, f"subtitle:{subtitle.name}"

    if requested != "preserve" and bool(config.get("localization", {}).get("asr_enabled", False)):
        try:
            transcript = transcribe_with_whisper(media, work, config).strip()
        except RuntimeError:
            transcript = ""
        if len(transcript) >= 30:
            return "localized", transcript, "asr_speech_detected"

    has_audio = media_has_audio(media)
    if requested == "localize":
        return "localized", "", "forced_localization_without_transcript"
    if has_audio:
        return "preserve_source", "", "no_speech_evidence_source_audio_preserved"
    return "silent", "", "no_speech_evidence_source_has_no_audio"


def chinese_audio_evidence(
    config: dict[str, Any] | None,
    media: Path,
) -> tuple[bool | None, dict[str, Any]]:
    settings = (config or {}).get("localization", {}) or {}
    if not bool(settings.get("chinese_audio_detection_enabled", True)):
        return None, {"enabled": False, "reason": "disabled"}
    model_name = str(settings.get("chinese_audio_detection_model") or settings.get("whisper_model") or "tiny")
    threshold = int(settings.get("chinese_audio_threshold") or 3)
    try:
        result = classify_chinese_audio(media, model_name=model_name, threshold=threshold)
    except Exception as error:
        return None, {"enabled": True, "reason": f"unavailable:{error}"}
    return bool(result.get("chinese_audio")), {
        "enabled": True,
        "model": model_name,
        "language": result.get("language", "unknown"),
        "language_probability": result.get("language_probability", 0.0),
        "chinese_characters": result.get("chinese_characters", 0),
        "transcript_preview": str(result.get("transcript") or "")[:500],
        "reason": "chinese_audio_detected" if result.get("chinese_audio") else "no_chinese_audio_detected",
    }


def localization_class_id(profile: dict[str, Any]) -> int:
    try:
        return int(profile.get("class") or 0)
    except (TypeError, ValueError):
        return 0


def localization_profile_for_candidate(
    row: sqlite3.Row,
    metadata: dict[str, Any],
    work: Path,
    media: Path,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    subtitle_files = sorted([*work.glob("source*.srt"), *work.glob("source*.vtt")])
    subtitle_text = ""
    for subtitle in subtitle_files[:3]:
        try:
            subtitle_text += " " + parse_srt(subtitle)
        except OSError:
            pass
    chinese_subtitles = bool(re.search(r"[\u4e00-\u9fff]", subtitle_text))
    detected_language = str(row["detected_language"] or metadata.get("language") or "").lower()
    title_text = f"{row['title']} {row['description']} {metadata.get('title') or ''} {metadata.get('description') or ''}"
    title_has_chinese = bool(re.search(r"[\u4e00-\u9fff]", title_text))
    has_audio = media_has_audio(media)
    platform = str(row["platform"] or "").strip().lower()
    ocr_regions: list[list[float]] = []
    ocr_reason = ""
    can_localize_chinese_audio = platform in LOCALIZABLE_CHINESE_AUDIO_PLATFORMS
    if not chinese_subtitles and can_localize_chinese_audio:
        try:
            ocr_regions = detect_chinese_text_regions(
                media,
                sample_count=8,
                backend=str(((config or {}).get("edit", {}) or {}).get("ocr_backend", "tesseract")),
            )
            ocr_reason = "ocr_screen_chinese_detected" if ocr_regions else "ocr_no_screen_chinese"
        except RuntimeError as error:
            ocr_reason = f"ocr_unavailable:{error}"
    chinese_on_screen = chinese_subtitles or bool(ocr_regions)
    detected_chinese_audio, audio_detection = (
        chinese_audio_evidence(config, media) if can_localize_chinese_audio and has_audio else (False, {"reason": "not_checked"})
    )
    inferred_chinese_audio = bool(detected_chinese_audio)
    if detected_chinese_audio is None:
        inferred_chinese_audio = bool(chinese_on_screen and has_audio)
    if can_localize_chinese_audio and chinese_on_screen and has_audio:
        class_id = 1
        mode = "localized"
        subtitle_mode = "ptbr_subtitles"
        reason = "chinese_subtitles_detected" if chinese_subtitles else (ocr_reason or "ocr_screen_chinese_detected")
    elif can_localize_chinese_audio and inferred_chinese_audio:
        class_id = 3
        mode = "localized"
        subtitle_mode = "none"
        reason = "chinese_audio_detected_without_screen_subtitles"
    else:
        class_id = 2
        mode = "preserve_source" if has_audio else "silent"
        subtitle_mode = "none"
        reason = "no_chinese_speech_or_subtitle_evidence"
    return {
        "class": class_id,
        "audio_mode": mode,
        "subtitle_mode": subtitle_mode,
        "chinese_subtitles": chinese_subtitles,
        "chinese_on_screen": chinese_on_screen,
        "ocr_regions": ocr_regions,
        "audio_detection": audio_detection,
        "title_has_chinese": title_has_chinese,
        "detected_language": detected_language or "unknown",
        "subtitle_files": [path.name for path in subtitle_files],
        "reason": reason,
    }


def should_ocr_blur_source_subtitles(
    cleanup_mode: str,
    *,
    platform: str,
    detected_language: str,
    title_text: str,
    localization_profile: dict[str, Any],
) -> bool:
    if str(cleanup_mode).strip().lower() != "ocr_blur":
        return False
    chinese_platforms = LOCALIZABLE_CHINESE_AUDIO_PLATFORMS
    normalized_platform = platform.strip().lower()
    if normalized_platform not in chinese_platforms:
        return False
    if (
        str(localization_profile.get("subtitle_mode") or "") == "ptbr_subtitles"
        and bool(localization_profile.get("chinese_on_screen", localization_profile.get("chinese_subtitles")))
    ):
        return True
    return False


DEFAULT_HOOK = "Olha só o que aconteceu aqui."
PUBLISH_PLATFORMS = ("youtube", "tiktok", "kwai", "facebook")


def active_hook(config: dict[str, Any]) -> tuple[str, str]:
    """Return (hook_version, hook_text) selected by tracking.hook_version."""
    version = str(config.get("tracking", {}).get("hook_version", "A")).strip() or "A"
    hooks = config.get("localization", {}).get("hooks") or {}
    text = str(hooks.get(version) or DEFAULT_HOOK)
    return version, text


def build_tracking_url(config: dict[str, Any], candidate: str, platform: str) -> str:
    """Unique attribution link per candidate x platform (utm_content=<cid>_<hook>)."""
    tracking = config.get("tracking", {})
    base = str(
        tracking.get("base_url")
        or config.get("brand", {}).get("default_cta", "https://copa.jarg.top/")
    ).strip()
    version, _ = active_hook(config)
    params = urllib.parse.urlencode({
        "utm_source": platform,
        "utm_medium": str(tracking.get("utm_medium", "organic_social")),
        "utm_campaign": str(tracking.get("campaign", "content_factory")),
        "utm_content": f"{candidate}_{version}",
    })
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{params}"


def tracking_links(config: dict[str, Any], candidate: str) -> dict[str, str]:
    return {platform: build_tracking_url(config, candidate, platform) for platform in PUBLISH_PLATFORMS}


def translate_to_ptbr(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()[:1600]
    if not clean:
        return "Veja este momento incrível e descubra mais no JaguarTV Hoje."
    query = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "pt", "dt": "t", "q": clean})
    request = urllib.request.Request(
        f"https://translate.googleapis.com/translate_a/single?{query}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        translated = "".join(part[0] for part in payload[0] if part and part[0])
    except Exception as error:
        # Never fall back to the untranslated source text: a Chinese/English
        # script would otherwise be narrated by the pt-BR voice unnoticed.
        raise RuntimeError(f"pt-BR translation failed: {error}") from error
    translated = translated.strip()
    if not translated:
        raise RuntimeError("pt-BR translation returned empty text")
    return translated


def source_subtitle_file(work: Path) -> Path | None:
    return next(iter(sorted([*work.glob("source*.srt"), *work.glob("source*.vtt")])), None)


def translate_source_subtitles_to_ptbr(config: dict[str, Any], work: Path, destination: Path) -> Path | None:
    source = source_subtitle_file(work)
    if not source or source.suffix.lower() != ".srt":
        return None
    if not pyvideotrans_enabled(config, "sts"):
        return None
    try:
        return pyvideotrans_translate_srt(config, source, destination)
    except RuntimeError as error:
        (work / "pyvideotrans_sts_fallback.json").write_text(
            json.dumps({"source": str(source), "error": str(error)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return None


def build_ptbr_script(source: str, *, hook: str = DEFAULT_HOOK) -> str:
    clean_source = re.sub(r"https?://\S+", "", source)
    clean_source = re.sub(r"欢迎.{0,8}(订阅|关注).*$", "", clean_source).strip()
    translated = translate_to_ptbr(clean_source)
    if re.search(r"[\u4e00-\u9fff]", translated):
        candidates = [
            part.strip()
            for part in re.split(r"[\n\r#│|｜]+", clean_source)
            if len(part.strip()) >= 8 and not re.search(r"(訂閱|订阅|追蹤|关注|粉絲團|频道|頻道|http)", part)
        ]
        for candidate in candidates[:4]:
            translated = translate_to_ptbr(candidate)
            if not re.search(r"[\u4e00-\u9fff]", translated):
                break
    if re.search(r"[\u4e00-\u9fff]", translated):
        translated = (
            "Uma história forte do futebol brasileiro ganhou atenção hoje. "
            "O momento envolve uma grande figura do esporte e merece ser acompanhado até o final."
        )
    words = translated.split()
    body = " ".join(words[:105])
    closing = "Gostou? Descubra mais conteúdos no JaguarTV Hoje."
    return f"{hook} {body} {closing}".strip()


def assert_script_is_portuguese(script: str) -> None:
    """Language gate: block production when the script clearly is not pt."""
    language, confidence = likely_language(script)
    if language == "zh":
        raise RuntimeError(
            "Language gate: pt-BR script still contains Chinese text; "
            "translation likely failed, refusing to narrate it"
        )
    if language == "pt":
        return
    # Latin-script text without Portuguese stopwords: long texts should have
    # matched pt tokens, so treat confident non-pt as a failure.
    if len(script) > 120 and confidence >= 0.5:
        raise RuntimeError(
            f"Language gate: script detected as '{language}' instead of pt-BR"
        )


def media_duration(path: Path) -> float:
    result = run_command([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)
    ])
    return float(result.stdout.strip())


def media_dimensions(path: Path) -> tuple[int, int]:
    result = run_command([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height", "-of", "json", str(path)
    ])
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    width = int(stream.get("width") or 1080)
    height = int(stream.get("height") or 1920)
    return width - width % 2, height - height % 2


def mobile_review_format_needed(width: int, height: int, config: dict[str, Any]) -> bool:
    mobile = config.get("mobile_review_format", {}) or {}
    if not bool(mobile.get("enabled", True)):
        return False
    if width <= 0 or height <= 0:
        return False
    min_aspect = float(mobile.get("landscape_min_aspect", 1.2))
    return (width / height) >= min_aspect


def mobile_review_target_size(config: dict[str, Any]) -> tuple[int, int]:
    mobile = config.get("mobile_review_format", {}) or {}
    target = mobile.get("target_resolution", [1080, 1440])
    target_width = int(target[0]) if isinstance(target, (list, tuple)) and len(target) >= 2 else 1080
    target_height = int(target[1]) if isinstance(target, (list, tuple)) and len(target) >= 2 else 1440
    target_width = max(360, target_width - target_width % 2)
    target_height = max(480, target_height - target_height % 2)
    return target_width, target_height


def remotion_canvas_for_source(config: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    if mobile_review_format_needed(width, height, config):
        target_width, target_height = mobile_review_target_size(config)
        return {
            "width": target_width,
            "height": target_height,
            "source_fit": "contain",
            "overlay_placement": "mobile_top_band",
            "mobile_format": {
                "applied": True,
                "mode": "landscape_to_3x4_black_letterbox_remotion",
                "source_width": width,
                "source_height": height,
                "target_width": target_width,
                "target_height": target_height,
            },
        }
    return {
        "width": width,
        "height": height,
        "source_fit": "cover",
        "overlay_placement": "video_corners",
        "mobile_format": {
            "applied": False,
            "mode": "unchanged",
            "source_width": width,
            "source_height": height,
            "reason": "not_landscape_or_disabled",
        },
    }


def normalize_mobile_review_video(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    mobile = config.get("mobile_review_format", {}) or {}
    width, height = media_dimensions(path)
    if not mobile_review_format_needed(width, height, config):
        return {
            "applied": False,
            "mode": "unchanged",
            "source_width": width,
            "source_height": height,
            "reason": "not_landscape_or_disabled",
        }

    target_width, target_height = mobile_review_target_size(config)
    background = str(mobile.get("background", "#000000")).strip() or "#000000"
    timeout = float((config.get("run", {}) or {}).get("timeout_sec", 360))
    tmp = path.with_name(f".mobile3x4-{os.getpid()}-{threading.get_ident()}-{random.randrange(1_000_000)}.mp4")
    vf = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color={background},"
        "setsar=1"
    )
    try:
        run_command([
            "ffmpeg", "-y",
            "-i", str(path),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(tmp),
        ], timeout=timeout)
        if not tmp.is_file() or tmp.stat().st_size <= 0:
            raise RuntimeError("mobile 3:4 render produced an empty file")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
    final_width, final_height = media_dimensions(path)
    return {
        "applied": True,
        "mode": "landscape_to_3x4_black_letterbox",
        "source_width": width,
        "source_height": height,
        "target_width": final_width,
        "target_height": final_height,
    }


def render_output_size(config: dict[str, Any], media: Path) -> tuple[int, int]:
    layout = str(config.get("edit", {}).get("layout_mode", "vertical")).strip().lower()
    if layout == "original":
        return media_dimensions(media)
    resolution = config.get("edit", {}).get("resolution", [1080, 1920])
    return int(resolution[0]), int(resolution[1])


def write_srt(text: str, duration: float, destination: Path) -> None:
    raw_sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    sentences: list[str] = []
    for sentence in raw_sentences:
        words = sentence.split()
        chunk: list[str] = []
        for word in words:
            proposed = " ".join([*chunk, word])
            if chunk and (len(chunk) >= 7 or len(proposed) > 30):
                sentences.append(" ".join(chunk))
                chunk = [word]
            else:
                chunk.append(word)
        if chunk:
            sentences.append(" ".join(chunk))
    if not sentences:
        sentences = [text]
    total_chars = sum(max(1, len(sentence)) for sentence in sentences)
    cursor = 0.0
    blocks = []
    for index, sentence in enumerate(sentences, start=1):
        share = max(1, len(sentence)) / total_chars
        length = max(1.2, duration * share)
        end = min(duration, cursor + length)
        blocks.append(f"{index}\n{format_srt_time(cursor)} --> {format_srt_time(end)}\n{sentence}\n")
        cursor = end
    destination.write_text("\n".join(blocks), encoding="utf-8")


def tts_rate_percent(config: dict[str, Any]) -> str:
    raw = (config.get("localization", {}) or {}).get("tts_rate", 1.08)
    if isinstance(raw, str):
        value = raw.strip()
        if value.endswith("%"):
            return value
        try:
            raw = float(value)
        except ValueError:
            return "+8%"
    percent = int(round((float(raw) - 1.0) * 100))
    return f"{percent:+d}%"


def format_srt_time(seconds: float) -> str:
    milliseconds = max(0, int(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def tts_ptbr(
    text: str,
    destination: Path,
    *,
    provider: str = "auto",
    edge_voice: str = "pt-BR-AntonioNeural",
    edge_rate: str = "+8%",
    config: dict[str, Any] | None = None,
    subtitles: Path | None = None,
) -> None:
    """Generate pt-BR narration with WorkBuddy's edge-tts path first.

    Local system voices remain the offline fallback for tests and degraded
    server operation.
    """
    provider = provider.strip().lower()
    if provider not in {"auto", "edge", "system", "pyvideotrans"}:
        raise ValueError("localization.tts_provider must be auto, edge, system or pyvideotrans")
    if config and subtitles and provider in {"auto", "pyvideotrans"} and pyvideotrans_enabled(config, "tts"):
        try:
            pyvideotrans_tts(config, subtitles, destination)
            return
        except RuntimeError:
            if provider == "pyvideotrans":
                raise
    if provider in {"auto", "edge"}:
        try:
            edge_tts_ptbr(text, destination, voice=edge_voice, rate=edge_rate)
            return
        except RuntimeError:
            if provider == "edge":
                raise
    if shutil.which("say"):
        run_command(["say", "-v", "Luciana", "-r", "185", "-o", str(destination), text])
        return
    for binary in ("espeak-ng", "espeak"):
        if shutil.which(binary):
            wav = destination.with_suffix(".wav")
            run_command([binary, "-v", "pt-br", "-s", "165", "-w", str(wav), text])
            if wav != destination:
                if shutil.which("ffmpeg"):
                    run_command(["ffmpeg", "-y", "-i", str(wav), str(destination)])
                else:
                    wav.replace(destination)
            return
    raise RuntimeError("No TTS backend found: install edge-tts, macOS `say`, or espeak-ng")


def generate_funk_bgm(destination: Path, duration: float = 32.0, bpm: int = 150) -> Path:
    """Generate an original, voice-free Brazilian funk-inspired demo beat."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 44_100
    total_samples = int(duration * sample_rate)
    step_seconds = 60.0 / bpm / 4.0
    kick_steps = {0, 3, 6, 10, 12, 15}
    clap_steps = {4, 12}
    bass_notes = (55.0, 55.0, 65.41, 49.0)
    rng = random.Random(20260720)

    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = bytearray()
        for index in range(total_samples):
            time = index / sample_rate
            step = int(time / step_seconds)
            position = time - step * step_seconds
            pattern_step = step % 16
            value = 0.0

            if pattern_step in kick_steps and position < 0.20:
                envelope = math.exp(-position * 20.0)
                frequency = 48.0 + 85.0 * math.exp(-position * 30.0)
                value += 0.92 * envelope * math.sin(2.0 * math.pi * frequency * position)
            if pattern_step in clap_steps and position < 0.12:
                envelope = math.exp(-position * 35.0)
                value += 0.28 * envelope * rng.uniform(-1.0, 1.0)
            if step % 2 == 0 and position < 0.045:
                envelope = math.exp(-position * 75.0)
                value += 0.10 * envelope * rng.uniform(-1.0, 1.0)
            if position < step_seconds * 0.82:
                note = bass_notes[(step // 4) % len(bass_notes)]
                value += 0.12 * math.sin(2.0 * math.pi * note * time)

            sample = max(-1.0, min(1.0, value))
            frames.extend(struct.pack("<h", int(sample * 26_000)))
            if len(frames) >= 131_072:
                output.writeframesraw(frames)
                frames.clear()
        if frames:
            output.writeframesraw(frames)
    return destination


def select_bgm(config: dict[str, Any], candidate: str) -> tuple[Path, str]:
    audio = config.get("audio", {})
    configured_path = str(audio.get("bgm_path") or "").strip()
    if configured_path:
        path = resolve_config_path(config, configured_path)
        if not path.exists():
            raise RuntimeError(f"Configured BGM does not exist: {path}")
        return path, "configured"

    bgm_dir = resolve_config_path(config, str(audio.get("bgm_dir", "assets/bgm")))
    tracks = sorted(
        path for path in bgm_dir.glob("*")
        if path.is_file() and path.suffix.lower() in {".mp3", ".m4a", ".aac", ".wav", ".flac"}
    )
    if tracks:
        track_index = int(hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:8], 16) % len(tracks)
        return tracks[track_index], "library"

    generated = workspace_dir(config) / "generated_audio" / "funk_150bpm_original.wav"
    if not generated.exists():
        generate_funk_bgm(generated)
    return generated, "generated"


def parse_srt_blocks(path: Path) -> list[tuple[float, float, str]]:
    blocks = []
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    for raw_block in re.split(r"\n\s*\n", content):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_text, end_text = [part.strip() for part in lines[1].split("-->", 1)]
        blocks.append((parse_srt_time(start_text), parse_srt_time(end_text), " ".join(lines[2:])))
    return blocks


def parse_srt_time(value: str) -> float:
    hours, minutes, remainder = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(remainder)


def _caption_region_for_interval(
    start: float,
    end: float,
    timed_regions: Sequence[dict[str, Any]] | None,
) -> list[float] | None:
    if not timed_regions:
        return None
    midpoint = (start + end) / 2
    candidates: list[tuple[float, list[float]]] = []
    for event in timed_regions:
        event_start = float(event.get("start") or 0.0)
        event_end = float(event.get("end") or event_start)
        overlap = max(0.0, min(end, event_end) - max(start, event_start))
        distance = abs(midpoint - ((event_start + event_end) / 2))
        score = overlap - distance * 0.08
        for region in event.get("regions") or []:
            if len(region) != 4:
                continue
            candidates.append((score, [float(value) for value in region]))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def remotion_caption_cues(
    subtitles: Path | None,
    *,
    max_end: float | None = None,
    max_cues: int = 500,
    max_text_chars: int = 180,
    timed_regions: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not subtitles:
        return []
    path = subtitles.expanduser()
    if not path.is_file():
        return []

    try:
        blocks = parse_srt_blocks(path)
    except (OSError, ValueError):
        return []

    cues: list[dict[str, Any]] = []
    for start, end, text in blocks:
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        if max_end is not None:
            if start >= max_end:
                continue
            end = min(end, max_end)
        if end <= start:
            continue
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            continue
        cue = {
            "startSeconds": round(max(0.0, start), 3),
            "endSeconds": round(max(0.0, end), 3),
            "text": cleaned[:max_text_chars],
        }
        region = _caption_region_for_interval(start, end, timed_regions)
        if region:
            cue["region"] = region
        cues.append(cue)
        if len(cues) >= max_cues:
            break
    return cues


def clamp_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def remotion_caption_style(config: dict[str, Any]) -> dict[str, Any]:
    captions = ((config.get("remotion", {}) or {}).get("captions", {}) or {})
    position = str(captions.get("position", "bottom")).strip().lower()
    if position not in {"top", "bottom"}:
        position = "bottom"
    return {
        "position": position,
        "maxWidthRatio": clamp_float(captions.get("max_width_ratio"), 0.82, 0.45, 0.96),
        "fontSizeRatio": clamp_float(captions.get("font_size_ratio"), 0.052, 0.02, 0.085),
        "backgroundOpacity": clamp_float(captions.get("background_opacity"), 0.74, 0.0, 0.95),
        "maxLines": int(clamp_float(captions.get("max_lines"), 2, 1, 2)),
        "textColor": str(captions.get("text_color", "#ffffff")).strip() or "#ffffff",
        "backgroundColor": str(captions.get("background_color", "#050505")).strip() or "#050505",
        "accentColor": str(captions.get("accent_color", "#f2d14b")).strip() or "#f2d14b",
    }


def remotion_captions_enabled_for_variant(config: dict[str, Any], variant: str) -> bool:
    captions = ((config.get("remotion", {}) or {}).get("captions", {}) or {})
    if not bool(captions.get("enabled", False)):
        return False
    variants = captions.get("variants", ["通用版"])
    if isinstance(variants, str):
        variants = [part.strip() for part in variants.split(",")]
    if not isinstance(variants, list):
        return False
    return variant in {str(item).strip() for item in variants}


def hyperframes_packaging_enabled(config: dict[str, Any]) -> bool:
    settings = config.get("hyperframes", {}) or {}
    return bool(settings.get("enabled", False))


def safe_hyperframes_dir_name(value: Any, *, default: str = "hyperframes") -> str:
    name = str(value or default).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("hyperframes.project_dir must be a simple directory name")
    return name


def write_hyperframes_package(
    config: dict[str, Any],
    work: Path,
    package_id: str,
    clean_media: Path,
    subtitles: Path | None,
    title: str,
    publishing_text: str,
    duration: float,
) -> dict[str, Any]:
    settings = config.get("hyperframes", {}) or {}
    project_root = work / safe_hyperframes_dir_name(settings.get("project_dir"))
    safe_package_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", package_id).strip("._-") or "package"
    project = project_root / safe_package_id
    media_dir = project / "media"
    vendor_dir = project / "vendor"
    media_dir.mkdir(parents=True, exist_ok=True)
    vendor_dir.mkdir(parents=True, exist_ok=True)
    source_copy = media_dir / "source.mp4"
    shutil.copy2(clean_media, source_copy)
    gsap_asset = local_hyperframes_gsap_asset()
    if not gsap_asset:
        raise RuntimeError("Hyperframes packaging requires a local gsap.min.js asset from installed skills")
    shutil.copy2(gsap_asset, vendor_dir / "gsap.min.js")

    cues = remotion_caption_cues(subtitles, max_end=duration)
    manifest = {
        "engine": "hyperframes",
        "status": "project_ready",
        "variant": str(settings.get("variant_label", "HF包装版")),
        "project_dir": str(project),
        "index": str(project / "index.html"),
        "source": "media/source.mp4",
        "gsap": "vendor/gsap.min.js",
        "durationSeconds": round(max(0.0, float(duration)), 3),
        "title": title[:140],
        "captionCount": len(cues),
        "allowExternalRender": bool(settings.get("allow_external_render", False)),
    }
    (project / "manifest.json").write_text(json.dumps({**manifest, "captions": cues}, ensure_ascii=False, indent=2), encoding="utf-8")
    (project / "index.html").write_text(
        render_hyperframes_html(manifest, cues, publishing_text),
        encoding="utf-8",
    )
    return manifest


def local_hyperframes_gsap_asset() -> Path | None:
    candidates = [
        ROOT / ".agents" / "skills" / "talking-head-recut" / "assets" / "vendor" / "gsap.min.js",
        ROOT / ".agents" / "skills" / "music-to-video" / "references" / "motion-primitives" / "assets" / "gsap.min.js",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def render_hyperframes_html(manifest: dict[str, Any], cues: list[dict[str, Any]], publishing_text: str) -> str:
    title = html.escape(str(manifest.get("title") or "JaguarTV"))
    text = html.escape(publishing_text[:260])
    duration = max(1.0, float(manifest.get("durationSeconds") or 0))
    gsap_src = html.escape(str(manifest.get("gsap") or ""))
    gsap_tag = f'<script src="{gsap_src}"></script>' if gsap_src else ""
    title_duration = min(4.0, duration)
    dek_start = max(0.0, duration - min(4.0, duration))
    caption_clips = "\n".join(
        (
            f'    <section id="hf-caption-{index}" class="clip caption" '
            f'data-start="{max(0.0, float(cue["startSeconds"])):.3f}" '
            f'data-duration="{max(0.001, float(cue["endSeconds"]) - float(cue["startSeconds"])):.3f}" '
            f'data-track-index="3">{html.escape(str(cue["text"]))}</section>'
        )
        for index, cue in enumerate(cues)
    )
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=1080, height=1920" />
  <title>{title}</title>
  {gsap_tag}
  <style>
    html, body {{
      width: 1080px;
      height: 1920px;
      margin: 0;
      overflow: hidden;
      background: #050505;
      font-family: Arial, Helvetica, sans-serif;
    }}
    #root {{
      position: relative;
      width: 1080px;
      height: 1920px;
      overflow: hidden;
      color: #fff;
    }}
    .clip {{
      position: absolute;
      box-sizing: border-box;
    }}
    .base {{
      inset: 0;
      width: 1080px;
      height: 1920px;
      background: #050505;
    }}
    video {{
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: #000;
    }}
    .title {{
      left: 54px;
      top: 82px;
      width: 820px;
      font-size: 66px;
      line-height: 1.04;
      font-weight: 900;
      text-shadow: 0 3px 12px rgba(0,0,0,.72);
    }}
    .caption {{
      left: 50%;
      bottom: 134px;
      transform: translateX(-50%);
      width: 886px;
      border-left: 12px solid #f2d14b;
      padding: 28px 34px;
      background: rgba(5, 5, 5, .74);
      font-size: 48px;
      line-height: 1.18;
      font-weight: 800;
      text-align: center;
      text-shadow: 0 2px 6px rgba(0,0,0,.55);
      overflow: hidden;
    }}
    .dek {{
      right: 44px;
      bottom: 58px;
      width: 470px;
      font-size: 24px;
      line-height: 1.22;
      opacity: .88;
      text-align: right;
      text-shadow: 0 2px 8px rgba(0,0,0,.72);
    }}
  </style>
</head>
<body>
  <div id="root" data-composition-id="jaguartv-hf" data-start="0" data-width="1080" data-height="1920" data-duration="{duration:.3f}">
    <section id="hf-base" class="clip base" data-start="0" data-duration="{duration:.3f}" data-track-index="0"></section>
    <video id="hf-video" class="clip" src="media/source.mp4" data-start="0" data-duration="{duration:.3f}" data-track-index="1" muted playsinline></video>
    <audio id="hf-audio" src="media/source.mp4" data-start="0" data-duration="{duration:.3f}" data-track-index="10" data-volume="1"></audio>
    <section id="hf-title" class="clip title" data-start="0" data-duration="{title_duration:.3f}" data-track-index="2"><span id="hf-title-text">{title}</span></section>
{caption_clips}
    <section id="hf-dek" class="clip dek" data-start="{dek_start:.3f}" data-duration="{duration - dek_start:.3f}" data-track-index="4">{text}</section>
  </div>
  <script>
    window.__timelines = window.__timelines || {{}};
    const timeline = window.gsap
      ? gsap.timeline({{ paused: true }})
      : {{ fromTo() {{ return this; }}, to() {{ return this; }} }};
    timeline.fromTo("#hf-title-text", {{ opacity: 0, y: -30 }}, {{ opacity: 1, y: 0, duration: 0.45, ease: "power3.out" }}, 0.15);
    timeline.fromTo("#hf-dek", {{ opacity: 0, y: 18 }}, {{ opacity: 0.88, y: 0, duration: 0.45, ease: "power2.out" }}, {dek_start:.3f});
    window.__timelines["jaguartv-hf"] = timeline;
  </script>
</body>
</html>
"""


def load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        # Linux fallbacks so pt-BR accents render correctly off-macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default(size=size)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        proposed = f"{current} {word}".strip()
        width = draw.textbbox((0, 0), proposed, font=font, stroke_width=2)[2]
        if current and width > max_width:
            lines.append(current)
            current = word
        else:
            current = proposed
    if current:
        lines.append(current)
    return lines[:2]


def brand_kit(config: dict[str, Any], kit_name: str | None = None) -> dict[str, Any]:
    """Resolve the active brand kit (watermark + endcard) from config."""
    brand = config.get("brand", {})
    kits = brand.get("kits") or {}
    name = str(kit_name or brand.get("default_kit") or "").strip()
    kit = dict(kits.get(name) or {})
    kit.setdefault("watermark", {})
    kit.setdefault("endcard", {})
    kit.setdefault("cover", {})
    kit["_name"] = name or "builtin"
    return kit


def render_cover_image(config: dict[str, Any], kit: dict[str, Any], source_video: Path, destination: Path) -> str:
    """Create the review cover from a configured image or a video frame."""
    settings = kit.get("cover", {})
    image_path = str(settings.get("image") or "").strip()
    mode = str(settings.get("mode") or "frame").strip().lower()
    if mode == "image" and image_path:
        source = resolve_config_path(config, image_path)
        if source.exists():
            width, height = media_dimensions(source_video)
            cover = Image.open(source).convert("RGB").resize((width, height), Image.LANCZOS)
            cover.save(destination, quality=92)
            return "configured_image"
    run_command(["ffmpeg", "-y", "-ss", "1", "-i", str(source_video), "-frames:v", "1", "-q:v", "2", str(destination)])
    return "video_frame"


def hex_color(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 6:
        try:
            return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            pass
    return fallback


def watermark_position(position: str, width: int, height: int, canvas_width: int = 1080, canvas_height: int = 1920) -> tuple[int, int]:
    margin = 34
    return {
        "top_left": (margin, 40),
        "top_right": (canvas_width - width - margin, 40),
        "bottom_left": (margin, canvas_height - height - 90),
        "bottom_right": (canvas_width - width - margin, canvas_height - height - 90),
    }.get(position, (margin, 40))


def render_watermark(config: dict[str, Any], kit: dict[str, Any], destination: Path, size: tuple[int, int] = (1080, 1920)) -> Path:
    settings = kit.get("watermark", {})
    canvas_width, canvas_height = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    opacity = max(0.0, min(1.0, float(settings.get("opacity", 0.9))))
    mode = str(settings.get("mode", "text"))
    image_path = str(settings.get("image") or "").strip()
    placed = False
    if mode == "image" and image_path:
        source = resolve_config_path(config, image_path)
        if source.exists():
            logo = Image.open(source).convert("RGBA")
            base_width = int(settings.get("width", 170))
            width = max(90, int(base_width * min(canvas_width / 1080, canvas_height / 1920)))
            ratio = width / logo.width
            logo = logo.resize((width, max(1, int(logo.height * ratio))), Image.LANCZOS)
            if opacity < 1.0:
                alpha = logo.getchannel("A").point(lambda value: int(value * opacity))
                logo.putalpha(alpha)
            canvas.alpha_composite(logo, watermark_position(str(settings.get("position", "top_left")), *logo.size, canvas_width, canvas_height))
            placed = True
    if not placed:
        text = str(settings.get("text") or "JaguarTV Hoje")
        font_brand = load_font(42)
        draw = ImageDraw.Draw(canvas)
        box = draw.textbbox((0, 0), text, font=font_brand)
        pad = 24
        width, height = box[2] - box[0] + pad * 2, box[3] - box[1] + pad * 2
        x, y = watermark_position(str(settings.get("position", "top_left")), width, height, canvas_width, canvas_height)
        draw.rounded_rectangle((x, y, x + width, y + height), radius=12, fill=(0, 0, 0, int(145 * opacity)))
        draw.text((x + pad, y + pad - box[1]), text, font=font_brand, fill=(255, 255, 255, int(255 * opacity)))
    output = destination / "logo.png"
    canvas.save(output)
    return output


def render_endcard(config: dict[str, Any], kit: dict[str, Any], destination: Path, size: tuple[int, int] = (1080, 1920)) -> Path:
    settings = kit.get("endcard", {})
    output = destination / "endcard.png"
    canvas_width, canvas_height = size
    scale = min(canvas_width / 1080, canvas_height / 1920)
    mode = str(settings.get("mode", "generated")).strip().lower()
    image_path = str(settings.get("image") or "").strip()
    if mode == "orientation_image":
        image_path = str(
            settings.get("portrait_image") if canvas_height >= canvas_width else settings.get("landscape_image")
            or image_path
        ).strip()
    if mode in {"image", "orientation_image"} and image_path:
        source = resolve_config_path(config, image_path)
        if source.exists():
            card = Image.open(source).convert("RGBA").resize(size, Image.LANCZOS)
            card.putalpha(255)
            card.save(output)
            return output
    background = hex_color(str(settings.get("background", "#04220E")), (4, 34, 14))
    accent = hex_color(str(settings.get("accent", "#8CE522")), (140, 229, 34))
    card = Image.new("RGBA", size, (*background, 255))
    draw = ImageDraw.Draw(card)
    y = int(canvas_height * 0.12)
    # Brand logo centered on top when available
    logo_path = str(kit.get("watermark", {}).get("image") or "").strip()
    if logo_path:
        source = resolve_config_path(config, logo_path)
        if source.exists():
            logo = Image.open(source).convert("RGBA")
            logo_width = max(90, int(260 * scale))
            ratio = logo_width / logo.width
            logo = logo.resize((logo_width, max(1, int(logo.height * ratio))), Image.LANCZOS)
            card.alpha_composite(logo, ((canvas_width - logo.width) // 2, y))
            y += logo.height + int(48 * scale)
    title = str(settings.get("title") or "Jaguar TV")
    tagline = str(settings.get("tagline") or "")
    title_font = load_font(max(36, int(96 * scale)))
    tagline_font = load_font(max(22, int(42 * scale)))
    title_box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((canvas_width - (title_box[2] - title_box[0])) / 2, y), title, font=title_font, fill="white")
    y += (title_box[3] - title_box[1]) + int(30 * scale)
    if tagline:
        for line in wrap_text(draw, tagline, tagline_font, int(canvas_width * 0.82)):
            box = draw.textbbox((0, 0), line, font=tagline_font)
            draw.text(((canvas_width - (box[2] - box[0])) / 2, y), line, font=tagline_font, fill=(*accent, 255))
            y += (box[3] - box[1]) + int(16 * scale)
    y += int(36 * scale)
    # Feature cards: [name, description]
    feature_title_font = load_font(max(24, int(46 * scale)))
    feature_text_font = load_font(max(18, int(34 * scale)))
    features = [item for item in (settings.get("features") or []) if item][:4]
    for feature in features:
        name = str(feature[0] if isinstance(feature, (list, tuple)) and feature else feature)
        detail = str(feature[1]) if isinstance(feature, (list, tuple)) and len(feature) > 1 else ""
        row_height = max(72, int(132 * scale))
        left = int(canvas_width * 0.08)
        right = int(canvas_width * 0.92)
        draw.rounded_rectangle(
            (left, y, right, y + row_height), radius=max(8, int(20 * scale)),
            fill=(18, 62, 31, 255), outline=(*accent, 210), width=2,
        )
        dot_x = left + int(32 * scale)
        text_x = left + int(80 * scale)
        dot = max(5, int(9 * scale))
        draw.ellipse((dot_x, y + row_height // 2 - dot, dot_x + dot * 2, y + row_height // 2 + dot), fill=(*accent, 255))
        draw.text((text_x, y + int(18 * scale)), name, font=feature_title_font, fill=(*accent, 255))
        if detail:
            draw.text((text_x, y + int(72 * scale)), detail[:52], font=feature_text_font, fill=(224, 236, 224, 255))
        y += row_height + int(20 * scale)
    site = str(settings.get("site") or "").strip()
    if site:
        y = min(max(y + int(30 * scale), int(canvas_height * 0.84)), canvas_height - int(110 * scale))
        site_font = load_font(max(28, int(56 * scale)))
        box = draw.textbbox((0, 0), site, font=site_font)
        width = box[2] - box[0]
        draw.rounded_rectangle(((canvas_width - width) // 2 - int(44 * scale), y - int(20 * scale), (canvas_width + width) // 2 + int(44 * scale), y + (box[3] - box[1]) + int(34 * scale)),
                               radius=max(8, int(16 * scale)), fill=(*accent, 255))
        draw.text(((canvas_width - width) / 2, y), site, font=site_font, fill=(6, 40, 16, 255))
    # The end card is a full-frame ad, never a translucent overlay over source.
    card.putalpha(255)
    card.save(output)
    return output


def render_overlay_assets(
    subtitles: Path | None, destination: Path, config: dict[str, Any] | None = None, kit_name: str | None = None,
    size: tuple[int, int] = (1080, 1920),
) -> tuple[Path, list[tuple[Path, float, float]], Path]:
    destination.mkdir(parents=True, exist_ok=True)
    config = config or {}
    kit = brand_kit(config, kit_name)
    canvas_width, canvas_height = size
    scale = min(canvas_width / 1080, canvas_height / 1920)
    font_large = load_font(max(28, int(54 * scale)))
    logo = render_watermark(config, kit, destination, size)

    subtitle_assets: list[tuple[Path, float, float]] = []
    subtitle_blocks = parse_srt_blocks(subtitles) if subtitles else []
    for index, (start, end, text) in enumerate(subtitle_blocks, start=1):
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        lines = wrap_text(draw, text, font_large, int(canvas_width * 0.84))
        spacing = 16
        boxes = [draw.textbbox((0, 0), line, font=font_large, stroke_width=3) for line in lines]
        heights = [box[3] - box[1] for box in boxes]
        total_height = sum(heights) + spacing * max(0, len(lines) - 1)
        y = int(canvas_height * 0.78) - total_height // 2
        max_line_width = max((box[2] - box[0] for box in boxes), default=0)
        draw.rounded_rectangle(
            ((canvas_width - max_line_width) // 2 - 30, y - 22, (canvas_width + max_line_width) // 2 + 30, y + total_height + 24),
            radius=18,
            fill=(0, 0, 0, 165),
        )
        for line, height, box in zip(lines, heights, boxes):
            width = box[2] - box[0]
            draw.text(((canvas_width - width) // 2, y), line, font=font_large, fill="white", stroke_width=3, stroke_fill="black")
            y += height + spacing
        path = destination / f"subtitle_{index:03d}.png"
        image.save(path)
        subtitle_assets.append((path, start, end))

    endcard = render_endcard(config, kit, destination, size)
    return logo, subtitle_assets, endcard


def render_video_ffmpeg(
    config: dict[str, Any], media: Path, voice: Path | None, bgm: Path | None,
    subtitles: Path | None, output: Path, duration: float, audio_mode: str = "localized",
    start_time: float = 0.0,
) -> None:
    render_target = output.with_name(
        f".{output.stem}.{os.getpid()}.{threading.get_ident()}.rendering{output.suffix}"
    )
    overlays_dir = output.parent / "overlays"
    kit = brand_kit(config)
    output_width, output_height = render_output_size(config, media)
    layout_mode = str(config.get("edit", {}).get("layout_mode", "vertical")).strip().lower()
    logo, subtitle_assets, endcard = render_overlay_assets(subtitles, overlays_dir, config, size=(output_width, output_height))
    image_inputs = [logo, *(path for path, _, _ in subtitle_assets), endcard]
    args = ["ffmpeg", "-y", "-ss", f"{max(0.0, start_time):.3f}", "-t", f"{duration:.3f}", "-i", str(media)]
    if audio_mode == "localized":
        if not voice:
            raise RuntimeError("Localized render requires pt-BR voice")
        args.extend(["-i", str(voice)])
        if bgm:
            args.extend(["-stream_loop", "-1", "-i", str(bgm)])
            image_input_start = 3
        else:
            image_input_start = 2
    elif audio_mode == "preserve_source":
        image_input_start = 1
    elif audio_mode in {"bgm_only", "silent"}:
        args.extend(["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
        image_input_start = 2
    else:
        raise RuntimeError(f"Unsupported audio mode: {audio_mode}")
    for image in image_inputs:
        args.extend(["-loop", "1", "-i", str(image)])

    cleanup_mode = str(config.get("edit", {}).get("source_subtitle_cleanup", "crop"))
    crop_ratio = float(config.get("edit", {}).get("source_subtitle_crop_bottom_ratio", 0.18))
    crop_ratio = max(0.0, min(0.35, crop_ratio))
    if layout_mode == "original":
        foreground_filter = (
            f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
            f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2"
        )
    elif cleanup_mode == "crop" and crop_ratio > 0:
        foreground_filter = (
            f"crop=iw:trunc(ih*{1.0 - crop_ratio:.4f}/2)*2:0:0,"
            f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease"
        )
    else:
        foreground_filter = f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease"

    if layout_mode == "original":
        chains = [
            f"[0:v]{foreground_filter}[v0]",
            f"[v0][{image_input_start}:v]overlay=0:0[v1]",
        ]
    else:
        chains = [
            "[0:v]split=2[base][front]",
            f"[base]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,crop={output_width}:{output_height},gblur=sigma=24[bg]",
            f"[front]{foreground_filter}[fg]",
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]",
            f"[v0][{image_input_start}:v]overlay=0:0[v1]",
        ]
    current = "v1"
    input_index = image_input_start + 1
    for overlay_index, (_, start, end) in enumerate(subtitle_assets, start=2):
        next_label = f"v{overlay_index}"
        chains.append(f"[{current}][{input_index}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[{next_label}]")
        current = next_label
        input_index += 1
    endcard_seconds = max(1.0, min(6.0, float(kit.get("endcard", {}).get("duration_sec", 3))))
    chains.append(
        f"[{current}][{input_index}:v]overlay=0:0:enable='gte(t,{max(0, duration - endcard_seconds):.3f})'[v]"
    )
    voice_volume = float(config.get("audio", {}).get("voice_volume", 1.0))
    bgm_volume = float(config.get("audio", {}).get("bgm_volume", 0.62))
    fade_out_start = max(0.0, duration - 1.0)
    if audio_mode == "localized":
        if bgm:
            chains.append(f"[1:a]volume={voice_volume},apad,asplit=2[voice_sc][voice_mix]")
            chains.append(
                f"[2:a]volume={bgm_volume},atrim=0:{duration:.3f},"
                f"afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out_start:.3f}:d=1[backing]"
            )
            chains.append("[backing][voice_sc]sidechaincompress=threshold=0.060:ratio=4:attack=12:release=220[backing_ducked]")
            chains.append("[backing_ducked][voice_mix]amix=inputs=2:duration=first:dropout_transition=1:normalize=0[a]")
        else:
            chains.append(
                f"[1:a]volume={voice_volume},apad,atrim=0:{duration:.3f},"
                f"afade=t=out:st={fade_out_start:.3f}:d=1[a]"
            )
    elif audio_mode == "preserve_source":
        source_volume = float(config.get("audio", {}).get("source_music_volume", 1.0))
        chains.append(
            f"[0:a]volume={source_volume},atrim=0:{duration:.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d=1[a]"
        )
    else:
        chains.append(
            f"[1:a]atrim=0:{duration:.3f}[a]"
        )
    args.extend([
        "-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
        "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(render_target),
    ])
    result = run_command(args, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-6000:])
    render_target.replace(output)


def remotion_template_dir() -> Path:
    return Path(__file__).resolve().parent / "remotion_template"


def remotion_runtime_is_healthy(runtime: Path) -> bool:
    remotion_bin = runtime / "node_modules" / ".bin" / "remotion"
    if not remotion_bin.exists():
        return False
    probe = run_command([str(remotion_bin), "versions"], cwd=runtime, check=False, timeout=20)
    if probe.returncode != 0:
        return False
    node_probe = run_command(
        [
            "node",
            "-e",
            "\n".join([
                "const opts = {paths: [process.cwd()]};",
                "require.resolve('remotion', opts);",
                "require.resolve('@remotion/renderer', opts);",
                "require.resolve('webpack/lib/dependencies/CriticalDependencyWarning', opts);",
            ]),
        ],
        cwd=runtime,
        check=False,
        timeout=20,
    )
    return node_probe.returncode == 0


def ensure_remotion_runtime(config: dict[str, Any]) -> Path:
    """Prepare a workspace-local Remotion runtime.

    The source package stays immutable; Node dependencies live under
    workspace/remotion_runtime so a zipped deployment can bootstrap itself on
    first Remotion render.
    """
    require_binary("node")
    template = remotion_template_dir()
    runtime = workspace_dir(config) / "remotion_runtime"
    shutil.copytree(template, runtime, dirs_exist_ok=True, ignore=shutil.ignore_patterns("node_modules"))
    installed = remotion_runtime_is_healthy(runtime)
    if not installed:
        shutil.rmtree(runtime / "node_modules", ignore_errors=True)
        lockfile = runtime / "package-lock.json"
        lockfile.unlink(missing_ok=True)
    if not installed:
        installer = shutil.which("npm")
        args = [installer, "install", "--no-audit", "--no-fund"] if installer else []
        if not args and shutil.which("pnpm"):
            args = [shutil.which("pnpm") or "pnpm", "install", "--ignore-scripts"]
        if not args:
            raise RuntimeError("Remotion dependencies are missing and neither npm nor pnpm is available")
        result = run_command(args, cwd=runtime, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                "Remotion dependencies install failed. Ensure Node.js/npm network access works.\n"
                + (result.stderr or result.stdout)[-4000:]
            )
        if not remotion_runtime_is_healthy(runtime):
            raise RuntimeError("Remotion dependencies install finished but runtime health check still failed")
    return runtime


SOURCE_FILENAME_LABELS = {
    "tiktok": "TikTko",
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "bilibili": "B站",
    "youtube": "YouTube",
    "facebook": "Facebook",
    "server_upload": "自传视频",
}


def source_filename_label(platform: str) -> str:
    return SOURCE_FILENAME_LABELS.get(platform.strip().lower(), re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", platform) or "source")


def candidate_date_label(row: sqlite3.Row) -> str:
    for field in ("created_at", "updated_at"):
        try:
            raw = str(row[field] or "")
            if raw:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%m%d")
        except (KeyError, ValueError):
            pass
    return datetime.now(timezone.utc).strftime("%m%d")


def inventory_root(config: dict[str, Any]) -> Path:
    configured = str((config.get("storage", {}) or {}).get("inventory_dir") or "inventory")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = storage_root(config) / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def next_inventory_index(config: dict[str, Any], date_label: str, source_label: str) -> int:
    pattern = re.compile(rf"^{re.escape(date_label)}-{re.escape(source_label)}-(\d+)-.+\.mp4$")
    highest = 0
    root = inventory_root(config)
    for path in root.glob(f"**/{date_label}-{source_label}-*.mp4"):
        match = pattern.match(path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def batch_label_for_output(options: dict[str, Any]) -> str:
    label = str(options.get("batch_label") or "").strip()
    if not label:
        return ""
    label = re.sub(r'[\\/:*?"<>|\s]+', "-", label).strip(".-")
    return label[:36]


def production_design_config(config: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    design = options.get("design") if isinstance(options, dict) else None
    if not isinstance(design, dict) or not design:
        return config
    patched = copy.deepcopy(config)
    patched.setdefault("edit", {})["render_engine"] = "remotion"
    remotion = patched.setdefault("remotion", {})
    remotion.setdefault("dual_variant", {})
    remotion["dual_variant"]["enabled"] = True
    if "layers" in design:
        patched.setdefault("edit", {})["layout_mode"] = "original"
        raw_layers = design.get("layers") if isinstance(design.get("layers"), list) else []
        raw_variants = design.get("variants") if isinstance(design.get("variants"), list) else ["通用版", "FB版"]
        variants = [str(item).strip() for item in raw_variants if str(item).strip() in {"通用版", "FB版"}]
        if not variants:
            variants = ["通用版", "FB版"]
        layers: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_layers):
            if not isinstance(raw, dict):
                continue
            layer_type = str(raw.get("type") or "").strip().lower()
            base = {
                "id": str(raw.get("id") or f"layer-{index + 1}")[:80],
                "type": layer_type,
                "x": max(0.0, min(1.0, float(raw.get("x", 0.0)))),
                "y": max(0.0, min(1.0, float(raw.get("y", 0.0)))),
            }
            if layer_type == "text":
                text = str(raw.get("text") or "")
                if not text.strip():
                    continue
                color = str(raw.get("color") or "#ffffff").strip()
                if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                    color = "#ffffff"
                layers.append({
                    **base,
                    "text": text,
                    "color": color,
                    "font_size_ratio": max(0.01, min(0.25, float(raw.get("font_size_ratio", 0.05)))),
                    "max_width": max(0.1, min(1.0, float(raw.get("max_width", 0.9)))),
                    "font_weight": max(100, min(900, int(raw.get("font_weight", 800)))),
                })
            elif layer_type == "image":
                path = str(raw.get("path") or "").strip()
                if not path:
                    continue
                layers.append({
                    **base,
                    "path": path,
                    "width": max(0.03, min(1.0, float(raw.get("width", 0.2)))),
                })
        base_video_path = str(design.get("base_video_path") or "").strip()
        custom_design_payload = {
            "enabled": True,
            "layers": layers,
            "variants": variants,
            "preserve_source_canvas": True,
            "whole_source": True,
        }
        if base_video_path:
            custom_design_payload["base_video_path"] = base_video_path
        base_asset_id = str(design.get("base_asset_id") or "").strip()
        if base_asset_id:
            custom_design_payload["base_asset_id"] = base_asset_id
        base_asset_ids = design.get("base_asset_ids") if isinstance(design.get("base_asset_ids"), dict) else {}
        filtered_asset_ids = {
            str(key): str(value).strip()
            for key, value in base_asset_ids.items()
            if str(key) in {"通用版", "FB版"} and str(value).strip()
        }
        if filtered_asset_ids:
            custom_design_payload["base_asset_ids"] = filtered_asset_ids
        remotion["custom_design"] = custom_design_payload
        return patched
    if str(design.get("overlay_left") or "").strip():
        remotion["dual_variant"]["tu_yi"] = str(design.get("overlay_left")).strip()
    if str(design.get("overlay_right") or "").strip():
        remotion["dual_variant"]["tu_er"] = str(design.get("overlay_right")).strip()
    if str(design.get("endcard_portrait") or "").strip():
        remotion["dual_variant"]["lv_tu"] = str(design.get("endcard_portrait")).strip()
    if str(design.get("endcard_landscape") or "").strip():
        remotion["dual_variant"]["lan_tu"] = str(design.get("endcard_landscape")).strip()
    for source, target in (
        ("top_badge", "top_badge"),
        ("headline", "bottom_headline"),
        ("subline", "bottom_subline"),
        ("cta", "endcard_cta"),
    ):
        value = str(design.get(source) or "").strip()
        if value:
            remotion[target] = value
    logo = str(design.get("logo") or "").strip()
    if logo:
        brand = patched.setdefault("brand", {})
        kit_name = str(brand.get("default_kit") or "jaguartv")
        kit = brand.setdefault("kits", {}).setdefault(kit_name, {})
        kit.setdefault("watermark", {})["image"] = logo
    return patched


def design_image_path(config: dict[str, Any], value: str) -> Path:
    path = resolve_config_path(config, value).expanduser().resolve()
    allowed_roots = [
        (storage_root(config) / "uploads" / "design_image").resolve(),
        resolve_config_path(config, "assets/brand").resolve(),
    ]
    if not path.is_file() or not any(root in path.parents for root in allowed_roots):
        raise ValueError(f"design image must be an uploaded image or brand asset: {path.name}")
    return path


def design_base_video_path(config: dict[str, Any], value: str) -> Path:
    path = resolve_config_path(config, value).expanduser().resolve()
    allowed_roots = [
        (workspace_dir(config) / "ready_for_review").resolve(),
        (storage_root(config) / "review").resolve(),
        inventory_root(config).resolve(),
    ]
    if not path.is_file() or path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        raise ValueError("design base video must be an existing server-produced video")
    if not any(root == path or root in path.parents for root in allowed_roots):
        raise ValueError("design base video must come from server output inventory")
    return path


def review_output_video_path_by_id(config: dict[str, Any], asset_id: str) -> Path | None:
    asset_id = urllib.parse.unquote(str(asset_id or "").strip())
    if not asset_id:
        return None
    roots = [workspace_dir(config) / "ready_for_review", storage_root(config) / "review"]
    for root in roots:
        if not root.exists():
            continue
        for package_dir in root.iterdir():
            if not package_dir.is_dir():
                continue
            for video in sorted(path for path in package_dir.glob("*.mp4") if path.is_file()):
                current_id = package_dir.name if video.name == "video.mp4" else f"{package_dir.name}:{video.stem}"
                if current_id == asset_id:
                    return video.resolve()
    return None


def remotion_output_variants(config: dict[str, Any]) -> list[str]:
    custom_design = ((config.get("remotion", {}) or {}).get("custom_design", {}) or {})
    raw_variants = custom_design.get("variants") if custom_design.get("enabled") else None
    if isinstance(raw_variants, str):
        raw_variants = [part.strip() for part in raw_variants.split(",")]
    if not isinstance(raw_variants, list):
        return ["通用版", "FB版"]
    variants = [str(item).strip() for item in raw_variants if str(item).strip() in {"通用版", "FB版"}]
    return variants or ["通用版", "FB版"]


def custom_design_preserves_source(config: dict[str, Any]) -> bool:
    custom_design = ((config.get("remotion", {}) or {}).get("custom_design", {}) or {})
    return bool(custom_design.get("enabled")) and bool(custom_design.get("preserve_source_canvas", False))


def remotion_design_base_video(config: dict[str, Any], variant: str | None = None) -> Path | None:
    custom_design = ((config.get("remotion", {}) or {}).get("custom_design", {}) or {})
    if not custom_design.get("enabled"):
        return None
    base_asset_ids = custom_design.get("base_asset_ids") if isinstance(custom_design.get("base_asset_ids"), dict) else {}
    asset_id = ""
    if variant:
        asset_id = str(base_asset_ids.get(variant) or "").strip()
    if not asset_id:
        asset_id = str(custom_design.get("base_asset_id") or "").strip()
    if asset_id:
        path = review_output_video_path_by_id(config, asset_id)
        if path is None:
            raise ValueError(f"design base asset does not exist: {asset_id}")
        return path
    value = str(custom_design.get("base_video_path") or "").strip()
    if not value:
        return None
    return design_base_video_path(config, value)


def configured_remotion_asset(config: dict[str, Any], key: str) -> Path:
    settings = config.get("remotion", {}).get("dual_variant", {}) or {}
    path = resolve_config_path(config, str(settings.get(key) or ""))
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"missing Remotion asset {key}: {path}")
    return path


def selected_endcard_asset(config: dict[str, Any], width: int, height: int) -> tuple[Path, str]:
    aspect = width / max(1, height)
    settings = config.get("aspect_thresholds", {}) or {}
    vertical_min = float(settings.get("vertical_min", 0.5))
    vertical_max = float(settings.get("vertical_max", 0.75))
    horizontal_min = float(settings.get("horizontal_min", 1.6))
    horizontal_max = float(settings.get("horizontal_max", 1.9))
    if vertical_min <= aspect <= vertical_max:
        return configured_remotion_asset(config, "lv_tu"), "9:16"
    if horizontal_min <= aspect <= horizontal_max:
        return configured_remotion_asset(config, "lan_tu"), "16:9"
    if aspect < 1.0:
        return configured_remotion_asset(config, "lv_tu"), "9:16_fallback"
    return configured_remotion_asset(config, "lan_tu"), "16:9_fallback"


def enforce_dual_variant_remotion(config: dict[str, Any]) -> None:
    """The production contract requires Remotion-rendered 通用版 + FB版 outputs.

    通用版 carries corner overlays and a 1.5s full-frame endcard. FB版 is clean
    throughout. Falling back to the old FFmpeg copy path would silently ship the
    wrong package, so fail loudly instead.
    """
    if str(config.get("edit", {}).get("render_engine", "ffmpeg")).strip().lower() != "remotion":
        raise RuntimeError("standard production requires edit.render_engine=remotion")
    remotion_settings = config.get("remotion", {}) or {}
    if not (remotion_settings.get("dual_variant", {}) or {}).get("enabled", False):
        raise RuntimeError("standard production requires remotion.dual_variant.enabled=true")
    promo = float(remotion_settings.get("promo_duration_sec", 1.5))
    if abs(promo - 1.5) > 0.01:
        raise RuntimeError("standard production requires remotion.promo_duration_sec=1.5")
    for key in ("tu_yi", "tu_er", "lv_tu", "lan_tu"):
        configured_remotion_asset(config, key)


def render_clean_segment(
    config: dict[str, Any], media: Path, voice: Path | None, bgm: Path | None,
    output: Path, duration: float, audio_mode: str, start_time: float,
) -> None:
    """Render only the clean video/audio segment, with no logo/endcard overlays."""
    render_target = output.with_name(
        f".{output.stem}.{os.getpid()}.{threading.get_ident()}.rendering{output.suffix}"
    )
    output_width, output_height = render_output_size(config, media)
    layout_mode = str(config.get("edit", {}).get("layout_mode", "vertical")).strip().lower()
    cleanup_mode = str(config.get("edit", {}).get("source_subtitle_cleanup", "crop"))
    crop_ratio = max(
        0.0, min(0.35, float(config.get("edit", {}).get("source_subtitle_crop_bottom_ratio", 0.18)))
    )
    if layout_mode == "original":
        foreground_filter = (
            f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
            f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2"
        )
        video_chains = [f"[0:v]{foreground_filter}[v]"]
    elif cleanup_mode == "crop" and crop_ratio > 0:
        foreground_filter = (
            f"crop=iw:trunc(ih*{1.0 - crop_ratio:.4f}/2)*2:0:0,"
            f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease"
        )
        video_chains = [
            "[0:v]split=2[base][front]",
            f"[base]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,crop={output_width}:{output_height},gblur=sigma=24[bg]",
            f"[front]{foreground_filter}[fg]",
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]",
        ]
    else:
        foreground_filter = f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease"
        video_chains = [
            "[0:v]split=2[base][front]",
            f"[base]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,crop={output_width}:{output_height},gblur=sigma=24[bg]",
            f"[front]{foreground_filter}[fg]",
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]",
        ]

    args = ["ffmpeg", "-y", "-ss", f"{max(0.0, start_time):.3f}", "-t", f"{duration:.3f}", "-i", str(media)]
    audio_chains: list[str] = []
    voice_volume = float(config.get("audio", {}).get("voice_volume", 1.0))
    bgm_volume = float(config.get("audio", {}).get("bgm_volume", 0.62))
    source_volume = float(config.get("audio", {}).get("source_music_volume", 1.0))
    fade_out_start = max(0.0, duration - 1.0)
    if audio_mode == "localized":
        if not voice:
            raise RuntimeError("Localized clean render requires pt-BR voice")
        args.extend(["-i", str(voice)])
        if bgm:
            args.extend(["-stream_loop", "-1", "-i", str(bgm)])
            audio_chains.extend([
                f"[1:a]volume={voice_volume},apad,asplit=2[voice_sc][voice_mix]",
                f"[2:a]volume={bgm_volume},atrim=0:{duration:.3f},afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out_start:.3f}:d=1[backing]",
                "[backing][voice_sc]sidechaincompress=threshold=0.060:ratio=4:attack=12:release=220[backing_ducked]",
                "[backing_ducked][voice_mix]amix=inputs=2:duration=first:dropout_transition=1:normalize=0[a]",
            ])
        else:
            audio_chains.append(
                f"[1:a]volume={voice_volume},apad,atrim=0:{duration:.3f},"
                f"afade=t=out:st={fade_out_start:.3f}:d=1[a]"
            )
    elif audio_mode == "preserve_source":
        audio_chains.append(
            f"[0:a]volume={source_volume},atrim=0:{duration:.3f},afade=t=out:st={fade_out_start:.3f}:d=1[a]"
        )
    elif audio_mode in {"bgm_only", "silent"}:
        args.extend(["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
        audio_chains.append(f"[1:a]atrim=0:{duration:.3f}[a]")
    else:
        raise RuntimeError(f"Unsupported audio mode: {audio_mode}")

    args.extend([
        "-filter_complex", ";".join(video_chains + audio_chains),
        "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}", "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(render_target),
    ])
    result = run_command(args, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-6000:])
    render_target.replace(output)


def copy_remotion_public_asset(source: Path, public_dir: Path, name: str) -> str:
    destination = public_dir / name
    shutil.copy2(source, destination)
    return f"renders/{public_dir.name}/{name}"


def run_remotion_cli_render(
    config: dict[str, Any],
    runtime: Path,
    props: dict[str, Any],
    output: Path,
    render_target: Path,
) -> dict[str, Any]:
    remotion_bin = runtime / "node_modules" / ".bin" / "remotion"
    try:
        result = run_command([
            str(remotion_bin), "render", "src/index.tsx", "JaguarTVVariant",
            str(render_target), "--props", json.dumps(props, ensure_ascii=False), "--log", "error",
        ], cwd=runtime, check=False, timeout=float((config.get("run", {}) or {}).get("timeout_sec", 360)))
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Remotion render timed out after {error.timeout}s") from error
    if result.returncode != 0:
        raise RuntimeError("Remotion render failed:\n" + (result.stderr or result.stdout)[-6000:])
    if not render_target.is_file() or render_target.stat().st_size <= 0:
        raise RuntimeError("Remotion render finished without output:\n" + (result.stderr or result.stdout)[-6000:])
    render_target.replace(output)
    return {"runner": "cli"}


def run_remotion_renderer_api(
    config: dict[str, Any],
    runtime: Path,
    props: dict[str, Any],
    output: Path,
    render_target: Path,
    *,
    job_id: str,
    candidate_id: str,
    variant: str,
) -> dict[str, Any]:
    timeout = float((config.get("run", {}) or {}).get("timeout_sec", 360))
    script = runtime / "scripts" / "render.mjs"
    if not script.is_file():
        raise RuntimeError(f"Remotion renderer script is missing: {script}")
    cancel_file = output.with_name(f"{output.stem}_render.cancel")
    cancel_file.unlink(missing_ok=True)
    payload = {
        "entryPoint": "src/index.tsx",
        "compositionId": "JaguarTVVariant",
        "props": props,
        "outputLocation": str(render_target),
        "cancelFile": str(cancel_file),
        "timeoutMs": int(timeout * 1000),
        "codec": "h264",
        "pixelFormat": "yuv420p",
        "x264Preset": "veryfast",
        "crf": 22,
    }
    payload_path = output.with_name(f"{output.stem}_renderer_payload.json")
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    upsert_render_job(
        config,
        job_id,
        candidate_id=candidate_id,
        variant=variant,
        engine="remotion_renderer_api",
        status="STARTED",
        progress=0.0,
        output_path=str(output),
        cancel_file=str(cancel_file),
        metadata={"payload_path": str(payload_path), "target_path": str(render_target)},
    )

    args = [require_binary("node"), str(script), str(payload_path)]
    process = subprocess.Popen(
        args,
        cwd=runtime,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    selector = selectors.DefaultSelector()
    if process.stdout is not None:
        selector.register(process.stdout, selectors.EVENT_READ)
    lines: list[str] = []
    started = time.monotonic()
    try:
        while process.poll() is None:
            if time.monotonic() - started > timeout:
                process.kill()
                update_render_job(config, job_id, status="TIMED_OUT", error=f"Timed out after {timeout:.0f}s")
                raise RuntimeError(f"Remotion renderer API timed out after {timeout:.0f}s")
            for key, _ in selector.select(timeout=0.25):
                line = key.fileobj.readline()
                if line:
                    lines.append(line.rstrip())
                    handle_remotion_renderer_event(config, job_id, line)
        if process.stdout is not None:
            for line in process.stdout:
                lines.append(line.rstrip())
                handle_remotion_renderer_event(config, job_id, line)
    finally:
        selector.close()

    output_text = "\n".join(lines)
    if process.returncode != 0:
        detail = output_text[-6000:]
        update_render_job(config, job_id, status="FAILED", error=detail)
        raise RuntimeError("Remotion renderer API failed:\n" + detail)
    if not render_target.is_file() or render_target.stat().st_size <= 0:
        detail = output_text[-6000:]
        update_render_job(config, job_id, status="FAILED", error=detail)
        raise RuntimeError("Remotion renderer API finished without output:\n" + detail)
    render_target.replace(output)
    update_render_job(
        config,
        job_id,
        status="COMPLETED",
        progress=1.0,
        metadata_patch={"completed_output": str(output), "size": output.stat().st_size},
    )
    return {"runner": "renderer_api", "render_job_id": job_id, "cancel_file": str(cancel_file)}


def handle_remotion_renderer_event(config: dict[str, Any], job_id: str, line: str) -> None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        update_render_job(config, job_id, metadata_patch={"last_log": line[-1000:]})
        return
    name = str(event.get("event") or "")
    if name == "bundle_progress":
        update_render_job(config, job_id, status="BUNDLING", progress=float(event.get("progress") or 0) * 0.12)
    elif name == "bundle_completed":
        update_render_job(config, job_id, status="SELECTING_COMPOSITION", progress=0.14)
    elif name == "composition_selected":
        update_render_job(
            config,
            job_id,
            status="RENDERING",
            progress=0.16,
            metadata_patch={
                "width": event.get("width"),
                "height": event.get("height"),
                "fps": event.get("fps"),
                "durationInFrames": event.get("durationInFrames"),
            },
        )
    elif name == "render_progress":
        render_progress = max(0.0, min(1.0, float(event.get("progress") or 0)))
        update_render_job(config, job_id, status="RENDERING", progress=0.16 + render_progress * 0.82)
    elif name == "cancel_requested":
        update_render_job(config, job_id, status="CANCEL_REQUESTED", metadata_patch={"cancel_event": event})
    elif name == "completed":
        update_render_job(config, job_id, status="FINALIZING", progress=0.99, metadata_patch={"renderer_completed": event})
    elif name == "error":
        update_render_job(config, job_id, status="FAILED", error=str(event.get("message") or line)[-4000:])


def render_video_remotion_variant(
    config: dict[str, Any],
    clean_media: Path,
    output: Path,
    *,
    variant: str,
    subtitles: Path | None = None,
    caption_regions: Sequence[dict[str, Any]] | None = None,
    job_id: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    clean_media = clean_media.expanduser().resolve()
    output = output.expanduser().resolve()
    runtime = ensure_remotion_runtime(config)
    source_width, source_height = media_dimensions(clean_media)
    if custom_design_preserves_source(config):
        canvas = {
            "width": source_width,
            "height": source_height,
            "source_fit": "cover",
            "overlay_placement": "video_corners",
            "mobile_format": {
                "applied": False,
                "mode": "source_canvas_design",
                "source_width": source_width,
                "source_height": source_height,
            },
        }
    else:
        canvas = remotion_canvas_for_source(config, source_width, source_height)
    width, height = int(canvas["width"]), int(canvas["height"])
    content_duration = media_duration(clean_media)
    remotion_settings = config.get("remotion", {}) or {}
    custom_design = remotion_settings.get("custom_design", {}) or {}
    custom_design_enabled = bool(custom_design.get("enabled"))
    promo_seconds = 0.0 if custom_design_enabled else max(
        1.0, min(6.0, float(remotion_settings.get("promo_duration_sec", 1.5)))
    )
    public_dir = runtime / "public" / "renders" / output.stem
    if public_dir.exists():
        shutil.rmtree(public_dir)
    public_dir.mkdir(parents=True, exist_ok=True)
    source_asset = copy_remotion_public_asset(clean_media, public_dir, "source.mp4")

    props: dict[str, Any] = {
        "variant": variant,
        "sourceVideo": source_asset,
        "width": width,
        "height": height,
        "fps": 30,
        "contentSeconds": content_duration,
        "promoSeconds": promo_seconds if variant == "通用版" else 0,
        "durationSeconds": content_duration + (promo_seconds if variant == "通用版" else 0),
        "overlayMaxWidthRatio": float(remotion_settings.get("overlay_max_width_ratio", 0.18)),
        "overlayLeftMaxWidthRatio": float(remotion_settings.get("mobile_overlay_left_width_ratio", 0.22)),
        "overlayRightMaxWidthRatio": float(remotion_settings.get("mobile_overlay_right_width_ratio", 0.36)),
        "overlayMarginHRatio": float(remotion_settings.get("overlay_margin_h_ratio", 0.03)),
        "overlayMarginVRatio": float(remotion_settings.get("overlay_margin_v_ratio", 0.05)),
        "sourceFit": str(canvas["source_fit"]),
        "endcardFit": "contain" if (canvas.get("mobile_format") or {}).get("applied") else "cover",
        "overlayPlacement": str(canvas["overlay_placement"]),
        "sourceAspectRatio": source_width / max(1, source_height),
        "customDesign": custom_design_enabled,
    }
    if remotion_captions_enabled_for_variant(config, variant):
        caption_cues = remotion_caption_cues(subtitles, max_end=content_duration, timed_regions=caption_regions)
        if caption_cues:
            props["captions"] = caption_cues
            props["captionStyle"] = remotion_caption_style(config)
    if custom_design_enabled:
        design_layers: list[dict[str, Any]] = []
        for index, layer in enumerate(custom_design.get("layers") or []):
            rendered_layer = dict(layer)
            if rendered_layer.get("type") == "image":
                image_path = design_image_path(config, str(rendered_layer.pop("path", "")))
                rendered_layer["src"] = copy_remotion_public_asset(
                    image_path,
                    public_dir,
                    f"design_{index + 1}{image_path.suffix or '.png'}",
                )
            design_layers.append(rendered_layer)
        props["designLayers"] = design_layers
    else:
        logo_path = str(brand_kit(config).get("watermark", {}).get("image") or "").strip()
        if logo_path and variant == "通用版":
            logo = resolve_config_path(config, logo_path)
            if logo.is_file():
                props["imgLogo"] = copy_remotion_public_asset(logo, public_dir, f"logo{logo.suffix or '.png'}")
        for key, prop_key in (
            ("top_badge", "topBadge"),
            ("bottom_headline", "bottomHeadline"),
            ("bottom_subline", "bottomSubline"),
            ("endcard_cta", "endcardCta"),
        ):
            value = str(remotion_settings.get(key) or "").strip()
            if value:
                props[prop_key] = value
    endcard_class = ""
    if variant == "通用版" and not custom_design_enabled:
        tu_yi = configured_remotion_asset(config, "tu_yi")
        tu_er = configured_remotion_asset(config, "tu_er")
        props["imgTuYi"] = copy_remotion_public_asset(tu_yi, public_dir, f"tu_yi{tu_yi.suffix or '.png'}")
        props["imgTuEr"] = copy_remotion_public_asset(tu_er, public_dir, f"tu_er{tu_er.suffix or '.png'}")
        endcard_path, endcard_class = selected_endcard_asset(config, source_width, source_height)
        props["imgEndcard"] = copy_remotion_public_asset(endcard_path, public_dir, f"endcard{endcard_path.suffix or '.png'}")

    props_path = output.with_name(f"{output.stem}_remotion_props.json")
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    render_target = output.with_name(
        f".remotion-{os.getpid()}-{threading.get_ident()}-{random.randrange(1_000_000)}{output.suffix}"
    )
    render_runner = str(remotion_settings.get("render_runner", "renderer_api")).strip().lower()
    if render_runner == "cli":
        runner_info = run_remotion_cli_render(config, runtime, props, output, render_target)
    elif render_runner == "renderer_api":
        stable_job_id = job_id or output.stem
        runner_info = run_remotion_renderer_api(
            config,
            runtime,
            props,
            output,
            render_target,
            job_id=f"{stable_job_id}:{variant}",
            candidate_id=candidate_id or stable_job_id,
            variant=variant,
        )
    else:
        raise RuntimeError("remotion.render_runner must be renderer_api or cli")
    return {
        "variant": variant,
        "path": str(output),
        "filename": output.name,
        **runner_info,
        "endcard_class": endcard_class,
        "duration": media_duration(output),
        "size": output.stat().st_size,
        "mobile_format": canvas["mobile_format"],
    }


def render_video_remotion(
    config: dict[str, Any], media: Path, voice: Path | None, bgm: Path | None,
    subtitles: Path | None, output: Path, duration: float, audio_mode: str,
    start_time: float = 0.0,
) -> None:
    clean = output.with_name(f"{output.stem}_clean_input.mp4")
    render_clean_segment(config, media, voice, bgm, clean, duration, audio_mode, start_time)
    render_video_remotion_variant(config, clean, output, variant="通用版", subtitles=subtitles, job_id=output.stem, candidate_id=output.stem)


def render_video(
    config: dict[str, Any], media: Path, voice: Path | None, bgm: Path | None,
    subtitles: Path | None, output: Path, duration: float, audio_mode: str = "localized",
    start_time: float = 0.0,
) -> None:
    engine = str(config.get("edit", {}).get("render_engine", "ffmpeg")).strip().lower()
    if engine == "remotion":
        return render_video_remotion(config, media, voice, bgm, subtitles, output, duration, audio_mode, start_time)
    if engine != "ffmpeg":
        raise RuntimeError("edit.render_engine must be ffmpeg or remotion")
    return render_video_ffmpeg(config, media, voice, bgm, subtitles, output, duration, audio_mode, start_time)


def qa_video(path: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    result = run_command([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,codec_name:format=duration", "-of", "json", str(path)
    ])
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    duration = float(payload["format"]["duration"])
    checks = {
        "playable": True,
        "width": stream.get("width"),
        "height": stream.get("height"),
        "duration": duration,
        "codec": stream.get("codec_name"),
    }
    layout = str((config or {}).get("edit", {}).get("layout_mode", "vertical")).strip().lower()
    minimum, maximum = short_duration_bounds(config or {})
    extra_duration = 0.0
    if (config or {}).get("remotion", {}).get("dual_variant", {}).get("enabled", False):
        extra_duration = max(0.0, float((config or {}).get("remotion", {}).get("promo_duration_sec", 1.5)))
    if layout == "original":
        checks["passed"] = (
            int(checks["width"] or 0) >= 360
            and int(checks["height"] or 0) >= 360
            and minimum <= duration <= maximum + extra_duration + 0.5
        )
    else:
        checks["passed"] = checks["width"] == 1080 and checks["height"] == 1920 and minimum <= duration <= maximum + extra_duration + 0.5
    return checks


def short_duration_bounds(config: dict[str, Any]) -> tuple[float, float]:
    configured = config.get("edit", {}).get("output_duration_sec", [20, 60])
    if isinstance(configured, (list, tuple)) and len(configured) >= 2:
        minimum = float(configured[0])
        maximum = float(configured[1])
    else:
        minimum = 20.0
        maximum = float(configured)
    return max(12.0, minimum), min(60.0, max(minimum, maximum))


def short_video_threshold(config: dict[str, Any]) -> float:
    return max(45.0, min(90.0, float(config.get("edit", {}).get("short_video_threshold_sec", 75))))


def source_duration_limit(config: dict[str, Any]) -> float:
    return max(60.0, float(config.get("selection", {}).get("max_source_duration_sec", 1800)))


def candidate_too_long(config: dict[str, Any], duration: float | int | None) -> bool:
    try:
        return bool(duration and float(duration) > source_duration_limit(config))
    except (TypeError, ValueError):
        return False


MARKET_INCLUDE_TERMS = (
    "brasileirão", "brasileirao", "flamengo", "palmeiras", "corinthians", "santos",
    "são paulo", "sao paulo", "botafogo", "vasco", "grêmio", "gremio", "fluminense",
    "arrascaeta", "neymar", "vinicius", "vinícius", "futebol", "gols", "gol ",
    "melhores momentos", "cazétv", "cazetv", "tempo real cazé", "tempo real caze",
    "巴甲", "巴西足球", "弗拉门戈", "帕尔梅拉斯", "科林蒂安", "桑托斯", "圣保罗",
    "内马尔", "足球", "进球", "集锦",
)

MARKET_REJECT_TERMS = (
    "竞彩", "足彩", "盘口", "比分预测", "稳胆", "串关", "单关", "推荐比分", "剧本参考",
    "命中", "红单", "黑单", "不中返", "亚盘", "大小球", "投注技巧", "赛前观察",
    "betting", "odds", "tips", "predictions", "palpite", "aposta", "apostas",
)


def candidate_market_rejection(
    config: dict[str, Any], info: dict[str, Any], *, keyword: str = ""
) -> str:
    """Reject low-value finds before they pollute the inventory.

    The default target is strict Brazil-football inventory. Broader Brazil trend
    crawls still reject betting/prediction spam, but they do not require a
    football term in every title.
    """
    market_filter = (config.get("selection") or {}).get("market_filter") or {}
    if market_filter.get("enabled") is False:
        return ""
    target = str(market_filter.get("target") or "brazil_football").strip().lower()
    text = " ".join(
        str(value or "")
        for value in (
            keyword,
            info.get("title"),
            info.get("description"),
            info.get("desc"),
            " ".join(str(tag) for tag in (info.get("tags") or []) if tag),
        )
    ).lower()
    if any(term.lower() in text for term in MARKET_REJECT_TERMS):
        return "prediction_or_betting_content"
    if target in {"brazil_trends", "broad_brazil", "brasil_trends"}:
        return ""
    if any(term.lower() in text for term in MARKET_INCLUDE_TERMS):
        return ""
    return "not_brazil_football"


def edge_rate_from_config(config: dict[str, Any]) -> str:
    raw = config.get("localization", {}).get("tts_rate", 1.08)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    percent = int(round((value - 1.0) * 100))
    return f"{percent:+d}%"


def whole_source_segment(source_duration: float, strategy: str) -> dict[str, Any]:
    return {
        "start": 0.0,
        "duration": round(source_duration, 3),
        "highlight_score": 50.0,
        "highlight_reasons": ["short_source_no_smart_slice"],
        "signal_scores": {
            "audio_peak": 0.0, "audio_surge": 0.0, "motion_peak": 0.0,
            "scene_change": 0.0, "keyword": 0.0, "replay": 0.0,
        },
        "strategy": strategy,
        "fallback": False,
        "index": 1,
        "total": 1,
        "source_start": 0.0,
        "source_end": round(source_duration, 3),
    }


def short_segments(config: dict[str, Any], source_duration: float, preferred_duration: float) -> list[dict[str, Any]]:
    """Build YouTube Shorts/TikTok-compatible segment windows.

    A normal source produces one package. Longer sources can produce multiple
    review packages, capped so a batch remains practical on a local machine.
    """
    minimum, maximum = short_duration_bounds(config)
    target = max(minimum, min(maximum, preferred_duration, source_duration))
    if source_duration <= maximum + 2.0:
        return [{"index": 1, "total": 1, "start": 0.0, "duration": max(minimum, min(maximum, source_duration))}]

    edit = config.get("edit", {})
    max_segments = max(1, int(edit.get("max_segments_per_source", 3)))
    overlap = max(0.0, float(edit.get("segment_overlap_sec", 3)))
    stride = max(5.0, target - overlap)
    possible = max(1, int(math.ceil((source_duration - target) / stride)) + 1)
    count = min(max_segments, possible)
    segments = []
    for index in range(count):
        if count == 1:
            start = 0.0
        else:
            start = min(max(0.0, source_duration - target), index * stride)
        duration = min(target, source_duration - start)
        if duration >= minimum:
            segments.append({"index": index + 1, "total": count, "start": start, "duration": duration})
    return segments or [{"index": 1, "total": 1, "start": 0.0, "duration": min(maximum, source_duration)}]


def produce_design_overlay_from_base(
    config: dict[str, Any],
    row: sqlite3.Row,
    options: dict[str, Any],
    progress: Callable[[int, str], None],
) -> Path:
    work = workspace_dir(config) / "jobs" / row["id"]
    work.mkdir(parents=True, exist_ok=True)
    progress(12, "正在读取服务器成片底视频")
    candidate_metadata: dict[str, Any] = {}
    try:
        candidate_metadata = json.loads(row["metadata_json"] or "{}")
    except (json.JSONDecodeError, KeyError):
        pass
    date_label = candidate_date_label(row)
    source_label = source_filename_label(str(row["platform"]))
    batch_label = batch_label_for_output(options) or "文案设计版"
    filename_source_label = f"{source_label}-{batch_label}"
    inventory_index = next_inventory_index(config, date_label, filename_source_label)
    filename_stem = f"{date_label}-{filename_source_label}-{inventory_index}"
    variant_outputs: list[dict[str, Any]] = []
    for variant in remotion_output_variants(config):
        base_video = remotion_design_base_video(config, variant)
        if base_video is None:
            raise RuntimeError("文案设计需要先选择一条服务器成片作为底视频")
        variant_output = work / f"{filename_stem}-{variant}.mp4"
        progress(32, f"正在基于服务器成片叠加文案设计：{variant}")
        info = render_video_remotion_variant(
            config,
            base_video,
            variant_output,
            variant=variant,
            subtitles=None,
            job_id=f"{row['id']}-design",
            candidate_id=row["id"],
        )
        info["duration"] = media_duration(variant_output)
        info["size"] = variant_output.stat().st_size
        info["source_label"] = source_label
        info["batch_label"] = batch_label
        inventory_dir = inventory_root(config) / batch_label / variant / source_label
        inventory_dir.mkdir(parents=True, exist_ok=True)
        inventory_path = inventory_dir / variant_output.name
        shutil.copy2(variant_output, inventory_path)
        info["inventory_path"] = str(inventory_path)
        qa_variant = qa_video(variant_output, config)
        qa_variant["variant"] = variant
        info["qa"] = qa_variant
        if not qa_variant["passed"]:
            raise RuntimeError(f"QA failed for design overlay {variant}: {qa_variant}")
        variant_outputs.append(info)
    output = Path(str(variant_outputs[0]["path"]))
    review_root = workspace_dir(config) / "ready_for_review"
    package_id = row["id"]
    review = review_root / package_id
    review.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output, review / "video.mp4")
    for info in variant_outputs:
        variant_path = Path(str(info["path"]))
        if variant_path.is_file():
            shutil.copy2(variant_path, review / variant_path.name)
    cover = review / "cover.jpg"
    cover_source = render_cover_image(config, brand_kit(config), output, cover)
    duration = media_duration(output)
    metadata = {
        "job_id": package_id,
        "keyword": str(candidate_metadata.get("keyword") or ""),
        "category": str(candidate_metadata.get("category") or ""),
        "source": {
            "candidate_id": row["id"],
            "platform": row["platform"],
            "url": row["url"],
            "title": row["title"],
            "base_video": str(remotion_design_base_video(config, remotion_output_variants(config)[0]) or ""),
        },
        "segment": {
            "index": 1,
            "total": 1,
            "start_sec": 0.0,
            "duration_sec": duration,
            "highlight_score": 0,
            "highlight_reasons": ["design_overlay_on_server_output"],
        },
        "strategy": {
            "content_type": "design_overlay",
            "segment_strategy": "server_output_overlay",
            "audio_policy": "preserve_output_audio",
        },
        "publishing_text": str(candidate_metadata.get("title") or row["title"] or ""),
        "tracking": tracking_links(config, package_id),
        "outputs": {
            "video": str(output),
            "cover": str(cover),
            "cover_source": cover_source,
            "variants": variant_outputs,
        },
        "batch_label": batch_label,
        "rights_status": str(options.get("rights_status") or "MANUAL_REVIEW"),
        "rights_note": "Design overlay generated from existing server-produced output.",
        "qa": variant_outputs[0].get("qa", {}),
        "created_at": now_iso(),
    }
    (review / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    connection = connect_db(config)
    connection.execute(
        "UPDATE candidates SET status='READY_FOR_REVIEW',updated_at=? WHERE id=?",
        (now_iso(), row["id"]),
    )
    append_event(connection, row["id"], "READY_FOR_REVIEW", {"package": package_id, "mode": "design_overlay"})
    connection.commit()
    storage_result = archive_review_package(config, package_id, review)
    append_event(connection, row["id"], "SERVER_ARCHIVED", storage_result)
    progress(100, "文案设计版已基于服务器成片生成")
    return review


def produce_passthrough_review_package(
    config: dict[str, Any],
    row: sqlite3.Row,
    media: Path,
    metadata: dict[str, Any],
    localization_profile: dict[str, Any],
    source_outro_detection: dict[str, Any],
    strategy: Any,
    compliance: dict[str, Any],
    progress: Callable[[int, str], None],
) -> Path:
    progress(62, "第 2 类素材：无中文字幕/中文声音，直接保留原视频")
    review_root = workspace_dir(config) / "ready_for_review"
    review = review_root / row["id"]
    review.mkdir(parents=True, exist_ok=True)
    output = review / "video.mp4"
    shutil.copy2(media, output)
    cover = review / "cover.jpg"
    cover_source = render_cover_image(config, brand_kit(config), output, cover)
    qa = qa_video(output, config)
    links = tracking_links(config, row["id"])
    duration = media_duration(output)
    width, height = media_dimensions(output)
    variant_outputs = [{
        "variant": "原视频",
        "path": str(output),
        "filename": output.name,
        "duration": duration,
        "size": output.stat().st_size,
        "source_label": source_filename_label(str(row["platform"])),
        "mobile_format": {
            "applied": False,
            "mode": "passthrough_original",
            "source_width": width,
            "source_height": height,
        },
        "batch_label": "",
        "qa": qa,
    }]
    metadata_payload = {
        "job_id": row["id"],
        "source_job_id": row["id"],
        "keyword": str(metadata.get("keyword") or ""),
        "category": str(metadata.get("category") or ""),
        "source": {"platform": row["platform"], "url": row["url"], "title": row["title"]},
        "content_type": strategy.content_type,
        "content_type_confidence": strategy.content_type_confidence,
        "matched_rules": list(strategy.matched_rules),
        "segment_strategy": "passthrough_original",
        "audio_policy": "preserve_output_audio",
        "operator_override": strategy.operator_override,
        "ptbr_script": "",
        "youtube": {
            "title": str(row["title"])[:100],
            "description": f"{str(row['description'] or '')[:500]}\n\n▶ {links['youtube']}",
            "hashtags": ["JaguarTV", "Brasil"],
            "cta_url": links["youtube"],
        },
        "tiktok": {"caption": str(row["title"])[:220], "hashtags": ["JaguarTV"], "cta_url": links["tiktok"]},
        "kwai": {"caption": str(row["title"])[:220], "hashtags": ["JaguarTV"], "cta_url": links["kwai"]},
        "facebook": {
            "text": f"{str(row['description'] or row['title'])[:500]}\n\n▶ {links['facebook']}",
            "hashtags": ["JaguarTV"],
            "cta_url": links["facebook"],
        },
        "brand_kit": brand_kit(config)["_name"],
        "brand_assets": {"cover_source": cover_source, "watermark": "", "endcard": ""},
        "output_variants": variant_outputs,
        "render_engine": "passthrough",
        "reaction": {"mode": "none"},
        "compliance": compliance,
        "tracking_links": links,
        "audio": {
            "mode": "preserve_source",
            "reason": f"localization_class_2:{localization_profile.get('reason')}",
            "source_audio_removed": False,
            "source_audio_preserved": True,
            "voice": "",
            "bgm": "",
            "bgm_source": "source_music",
        },
        "localization_profile": localization_profile,
        "visual_cleanup": {
            "layout_mode": "passthrough_original",
            "source_subtitle_mode": "off",
            "source_subtitle_crop_bottom_ratio": 0,
            "ocr": {"used": False, "regions": [], "reason": "class_2_passthrough", "media": str(output)},
        },
        "source_outro_trim": source_outro_detection,
        "source_outro_trim_summary": review_source_outro_summary(source_outro_detection),
        "qa": qa,
    }
    (review / "metadata.json").write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (review / "review.json").write_text(
        json.dumps({"decision": "pending", "note": "", "reviewed_at": ""}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "job_id": row["id"],
        "status": "READY_FOR_REVIEW",
        "created_at": now_iso(),
        "mode": "passthrough_original",
        "assets": {"source": str(media), "reviews": [str(review)]},
        "localization_profile": localization_profile,
        "qa": [qa],
        "source_outro_trim": source_outro_detection,
    }
    work = workspace_dir(config) / "jobs" / row["id"]
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    storage_result = archive_review_package(config, row["id"], review)
    connection = connect_db(config)
    connection.execute("UPDATE candidates SET status='READY_FOR_REVIEW',updated_at=? WHERE id=?", (now_iso(), row["id"]))
    append_event(connection, row["id"], "READY_FOR_REVIEW", manifest)
    append_event(connection, row["id"], "SERVER_ARCHIVED", storage_result)
    connection.commit()
    progress(100, "第 2 类原视频审核包已生成")
    return review


def produce_candidate(
    config: dict[str, Any], row: sqlite3.Row,
    progress_callback: Callable[[int, str], None] | None = None,
    options: dict[str, Any] | None = None,
) -> Path:
    def progress(value: int, message: str) -> None:
        if progress_callback:
            progress_callback(value, message)

    require_binary("ffmpeg")
    require_binary("ffprobe")
    options = options or {}
    config = production_design_config(config, options)
    custom_design = ((config.get("remotion", {}) or {}).get("custom_design", {}) or {})
    if custom_design.get("enabled") and (
        custom_design.get("base_video_path") or custom_design.get("base_asset_id") or custom_design.get("base_asset_ids")
    ):
        return produce_design_overlay_from_base(config, row, options, progress)
    progress(5, "正在读取源素材")
    work = workspace_dir(config) / "jobs" / row["id"]
    media = next((path for path in work.glob("source.*") if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
    if not media:
        raise RuntimeError(f"No downloaded media for {row['id']}")
    original_media = media
    try:
        candidate_metadata = json.loads(row["metadata_json"] or "{}")
    except (json.JSONDecodeError, KeyError):
        candidate_metadata = {}
    original_source_duration = media_duration(media)
    if candidate_too_long(config, original_source_duration):
        raise RuntimeError(
            f"source duration {original_source_duration:.1f}s exceeds max_source_duration_sec={source_duration_limit(config):.0f}"
        )
    source_outro_detection = detect_source_outro(
        config,
        media,
        work,
        duration=original_source_duration,
        options=options,
        run_command=run_command,
    )
    clean_source = Path(str(source_outro_detection.get("clean_source_path") or ""))
    if bool(source_outro_detection.get("applied")) and clean_source.is_file():
        media = clean_source
        progress(
            8,
            f"已裁剪原素材尾部宣传尾卡 {float(source_outro_detection.get('trim_end_sec') or 0):.1f}s",
        )
    source_duration = media_duration(media)
    candidate_payload = {**dict(row), "metadata": candidate_metadata}
    strategy = resolve_production_strategy(
        candidate_payload,
        options,
        default_max_segments=int(config.get("edit", {}).get("max_segments_per_source", 3)),
        default_max_duration=float(short_duration_bounds(config)[1]),
    )
    compliance = assert_render_allowed(config, strategy.content_type, candidate_metadata, options)
    reaction = reaction_spec(options)
    progress(10, f"内容类型：{strategy.content_type} · 切片：{strategy.segment_strategy}")
    os.environ.setdefault(
        "JAGUARTV_WHISPER_MODEL", str(config.get("localization", {}).get("whisper_model", "tiny"))
    )
    hook_version, hook_text = active_hook(config)
    audio_mode = render_audio_mode(strategy.audio_policy)
    localization_profile = localization_profile_for_candidate(row, candidate_metadata, work, media, config)
    audio_override = str(options.get("audio_policy") or "auto").strip().lower() != "auto"
    if (
        not audio_override
        and localization_class_id(localization_profile) == 2
        and bool(config.get("edit", {}).get("passthrough_clean_sources", True))
        and reaction.mode == "none"
    ):
        return produce_passthrough_review_package(
            config,
            row,
            original_media,
            candidate_metadata,
            localization_profile,
            source_outro_detection,
            strategy,
            compliance,
            progress,
        )
    if not audio_override:
        audio_mode = str(localization_profile["audio_mode"])
    transcript = ""
    audio_reason = f"content_policy:{strategy.content_type}->{strategy.audio_policy}"
    audio_reason += f":localization_class_{localization_profile['class']}:{localization_profile['reason']}"
    if audio_mode == "localized":
        require_source_transcript = (
            not audio_override
            and localization_class_id(localization_profile) in {1, 3}
        )
        transcript = source_text(
            work,
            media,
            f"{row['title']}. {row['description']}".strip(),
            enable_asr=bool(config.get("localization", {}).get("asr_enabled", False)),
            config=config,
            allow_metadata_fallback=not require_source_transcript,
        )
    elif audio_mode == "preserve_source" and not media_has_audio(media):
        audio_mode = "silent"
        audio_reason += ":source_has_no_audio"
    elif audio_mode in {"bgm_only", "silent"} and media_has_audio(media):
        audio_mode = "preserve_source"
        audio_reason += ":fixed_bgm_policy_disabled_source_audio_preserved"
    progress(18, f"音轨策略：{audio_mode}")
    script = ""
    voice: Path | None = None
    subtitles: Path | None = None
    bgm: Path | None = None
    bgm_source = "source_music" if audio_mode == "preserve_source" else "none"
    if audio_mode == "localized":
        fallback_text = f"{row['title']}. {row['description']}".strip()
        script = build_ptbr_script(transcript or fallback_text, hook=hook_text)
        assert_script_is_portuguese(script)
        progress(32, "葡语脚本检查通过")
        localization_settings = config.get("localization", {}) or {}
        translated_subtitles = None
        if localization_profile.get("subtitle_mode") == "ptbr_subtitles":
            translated_subtitles = translate_source_subtitles_to_ptbr(config, work, work / "subtitles_ptbr.srt")
        if bool(localization_settings.get("voice_enabled", False)):
            voice = work / "voice_ptbr.aiff"
            subtitles_for_tts = translated_subtitles or work / "subtitles_ptbr_script.srt"
            if not translated_subtitles:
                write_srt(script, min(60.0, source_duration), subtitles_for_tts)
            tts_ptbr(
                script,
                voice,
                provider=str(localization_settings.get("tts_provider", "auto")),
                edge_voice=str(localization_settings.get("edge_tts_voice", "pt-BR-AntonioNeural")),
                edge_rate=tts_rate_percent(config),
                config=config,
                subtitles=subtitles_for_tts,
            )
            progress(48, "pt-BR 配音已生成")
        else:
            voice = None
            audio_mode = "preserve_source" if media_has_audio(media) else "silent"
            audio_reason += ":voice_disabled_source_audio_preserved"
            progress(48, "葡语脚本检查通过，未启用配音时保留源音且不添加固定音频")
        if audio_mode == "localized" and voice and bool(config.get("localization", {}).get("preserve_backing_track", False)):
            try:
                bgm = demucs_backing_track(media, work)
                bgm_source = "demucs_no_vocals"
            except RuntimeError as error:
                if bool(config.get("localization", {}).get("require_backing_track", False)):
                    raise RuntimeError(f"background track separation required but failed: {error}") from error
                bgm = None
                bgm_source = "none:demucs_unavailable"
        progress(55, "葡语音轨已准备，未添加固定 BGM")
    elif audio_mode in {"bgm_only", "silent"}:
        audio_mode = "silent"
        progress(55, "源素材无可保留音轨，输出静音且不添加固定音频")
    else:
        progress(55, "无对白证据，保留源音乐且不生成旁白字幕")
    script_payload = {
        "hook": hook_text,
        "hook_version": hook_version,
        "text": script,
        "title_source": row["title"],
        "audio_mode": audio_mode,
        "audio_reason": audio_reason,
    }
    (work / "script_ptbr.json").write_text(json.dumps(script_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if audio_mode in {"bgm_only", "silent"} and subtitles:
        preferred_duration = min(60.0, source_duration)
    elif audio_mode == "localized" and voice and (work / "subtitles_ptbr.srt").is_file():
        voice_duration = media_duration(voice)
        preferred_duration = min(60.0, source_duration, max(20.0, voice_duration + 3.0))
        subtitles = work / "subtitles_ptbr.srt"
    elif audio_mode == "localized" and voice and localization_profile.get("subtitle_mode") == "ptbr_subtitles":
        voice_duration = media_duration(voice)
        preferred_duration = min(60.0, source_duration, max(20.0, voice_duration + 3.0))
        subtitles = work / "subtitles_ptbr.srt"
        write_srt(script, max(1.0, voice_duration), subtitles)
    elif audio_mode == "localized" and voice:
        voice_duration = media_duration(voice)
        preferred_duration = min(60.0, source_duration, max(20.0, voice_duration + 3.0))
    else:
        preferred_duration = min(60.0, source_duration)
        stale_paths = [work / "voice_ptbr.aiff"]
        if subtitles is None:
            stale_paths.append(work / "subtitles_ptbr.srt")
        for stale in stale_paths:
            stale.unlink(missing_ok=True)
    render_engine = str(config.get("edit", {}).get("render_engine", "ffmpeg")).strip().lower()
    enforce_dual_variant_remotion(config)
    transcript_file = next(iter(sorted([*work.glob("source*.srt"), *work.glob("source*.vtt")])), None)
    custom_design = ((config.get("remotion", {}) or {}).get("custom_design", {}) or {})
    if bool(custom_design.get("enabled")) and bool(custom_design.get("whole_source")):
        segments = [whole_source_segment(source_duration, "whole_source_design")]
    elif source_duration <= short_video_threshold(config):
        segment_duration = min(source_duration, max(float(short_duration_bounds(config)[1]), preferred_duration))
        segments = [whole_source_segment(segment_duration, strategy.segment_strategy)]
    else:
        segments = analyze_video(
            media,
            source_duration=source_duration,
            max_segments=strategy.max_segments,
            max_duration=min(strategy.max_duration, preferred_duration),
            strategy=strategy.segment_strategy,
            transcript_path=transcript_file,
        )
    (work / "analysis.json").write_text(
        json.dumps({"strategy": strategy.to_dict(), "segments": segments}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    progress(60, f"智能切片：{len(segments)} 段 · {strategy.segment_strategy}")
    cleanup_mode = str(config.get("edit", {}).get("source_subtitle_cleanup", "crop"))
    crop_ratio = max(
        0.0, min(0.35, float(config.get("edit", {}).get("source_subtitle_crop_bottom_ratio", 0.18)))
    )
    publishing_text = script or "Assista aos melhores momentos no Jaguar TV."
    reviews: list[Path] = []
    qa_results: list[dict[str, Any]] = []
    review_root = workspace_dir(config) / "ready_for_review"
    stale_single_review = review_root / row["id"]
    if len(segments) > 1 and stale_single_review.exists():
        shutil.rmtree(stale_single_review)
    if len(segments) == 1:
        for stale_part in review_root.glob(f"{row['id']}_part*"):
            if stale_part.is_dir():
                shutil.rmtree(stale_part)
    for segment in segments:
        segment_index = int(segment["index"])
        segment_total = int(segment["total"])
        package_id = row["id"] if segment_total == 1 else f"{row['id']}_part{segment_index:02d}"
        source_label = source_filename_label(str(row["platform"]))
        batch_label = batch_label_for_output(options)
        filename_source_label = f"{source_label}-{batch_label}" if batch_label else source_label
        date_label = candidate_date_label(row)
        inventory_index = next_inventory_index(config, date_label, filename_source_label)
        filename_stem = f"{date_label}-{filename_source_label}-{inventory_index}"
        output = work / f"{filename_stem}-通用版.mp4"
        progress(62 + int((segment_index - 1) * 24 / max(1, segment_total)), f"正在渲染第 {segment_index}/{segment_total} 个 Short")
        render_media = media
        render_start = float(segment["start"])
        ocr_cleanup: dict[str, Any] = {
            "used": False,
            "regions": [],
            "reason": "not_requested",
            "media": str(media),
        }
        should_ocr_cleanup = should_ocr_blur_source_subtitles(
            cleanup_mode,
            platform=str(row["platform"]),
            detected_language=str(row["detected_language"] or candidate_metadata.get("language") or ""),
            title_text=(
                f"{row['title']} {row['description']} "
                f"{candidate_metadata.get('title') or ''} {candidate_metadata.get('description') or ''}"
            ),
            localization_profile=localization_profile,
        )
        if should_ocr_cleanup:
            preprocessed = work / f"ocr_blurred_part{segment_index:02d}.mp4"
            fallback_regions = (
                config.get("edit", {}).get("ocr_fallback_regions")
                if config.get("edit", {}).get("ocr_use_fallback_regions", False)
                else None
            )
            if fallback_regions is None:
                chinese_hard_subtitle_hint = (
                    bool(localization_profile.get("title_has_chinese"))
                    or str(localization_profile.get("detected_language") or "").lower().startswith("zh")
                )
                if (
                    chinese_hard_subtitle_hint
                    and bool(config.get("edit", {}).get("ocr_auto_lower_third_fallback", True))
                ):
                    fallback_regions = config.get("edit", {}).get(
                        "ocr_lower_third_fallback_regions",
                        [[0.0, 0.68, 1.0, 0.96]],
                    )
                else:
                    fallback_regions = []
            ocr_cleanup = prepare_ocr_blurred_segment(
                media,
                preprocessed,
                start=render_start,
                duration=float(segment["duration"]),
                sigma=int(config.get("edit", {}).get("ocr_blur_sigma", 28)),
                fallback_regions=fallback_regions,
                backend=str(config.get("edit", {}).get("ocr_backend", "tesseract")),
            )
            render_media = preprocessed
            render_start = 0.0
        title_suffix = f" - Parte {segment_index}" if segment_total > 1 else ""
        variant_outputs: list[dict[str, Any]] = []
        hyperframes_package: dict[str, Any] | None = None
        if render_engine == "remotion" and (config.get("remotion", {}).get("dual_variant", {}) or {}).get("enabled", False):
            clean = work / f"{filename_stem}_clean_input.mp4"
            render_clean_segment(
                config, render_media, voice, bgm, clean, float(segment["duration"]),
                audio_mode=audio_mode, start_time=render_start,
            )
            for variant in remotion_output_variants(config):
                variant_output = work / f"{filename_stem}-{variant}.mp4"
                info = render_video_remotion_variant(
                    config,
                    clean,
                    variant_output,
                    variant=variant,
                    subtitles=subtitles,
                    caption_regions=ocr_cleanup.get("timed_regions") if ocr_cleanup.get("used") else None,
                    job_id=package_id,
                    candidate_id=row["id"],
                )
                if reaction.mode != "none":
                    compose_reaction(
                        variant_output,
                        variant_output,
                        reaction,
                        content_duration=float(segment["duration"]),
                    )
                mobile_format = (
                    dict(info["mobile_format"])
                    if custom_design_preserves_source(config)
                    else normalize_mobile_review_video(variant_output, config)
                )
                if not mobile_format.get("applied") and info.get("mobile_format", {}).get("applied"):
                    mobile_format = dict(info["mobile_format"])
                info["mobile_format"] = mobile_format
                info["duration"] = media_duration(variant_output)
                info["size"] = variant_output.stat().st_size
                inventory_dir = inventory_root(config) / batch_label / variant / source_label if batch_label else inventory_root(config) / variant / source_label
                inventory_dir.mkdir(parents=True, exist_ok=True)
                inventory_path = inventory_dir / variant_output.name
                shutil.copy2(variant_output, inventory_path)
                qa_variant = qa_video(variant_output, config)
                qa_variant["variant"] = variant
                info.update({
                    "path": str(variant_output),
                    "inventory_path": str(inventory_path),
                    "qa": qa_variant,
                    "source_label": source_label,
                    "batch_label": batch_label,
                })
                if not qa_variant["passed"]:
                    raise RuntimeError(f"QA failed for {package_id} {variant}: {qa_variant}")
                variant_outputs.append(info)
            if hyperframes_packaging_enabled(config):
                hyperframes_package = write_hyperframes_package(
                    config,
                    work,
                    package_id,
                    clean,
                    subtitles,
                    f"{filename_stem}{title_suffix}",
                    publishing_text,
                    float(segment["duration"]),
                )
            output = Path(str(variant_outputs[0]["path"]))
        else:
            render_video(
                config, render_media, voice, bgm, subtitles, output, float(segment["duration"]),
                audio_mode=audio_mode, start_time=render_start,
            )
            if reaction.mode != "none":
                endcard_seconds = max(
                    1.0,
                    min(
                        6.0,
                        float(brand_kit(config).get("endcard", {}).get("duration_sec", 3)),
                        max(1.0, float(segment["duration"]) - 1.0),
                    ),
                )
                compose_reaction(
                    output,
                    output,
                    reaction,
                    content_duration=max(1.0, float(segment["duration"]) - endcard_seconds),
                )
            mobile_format = normalize_mobile_review_video(output, config)
            variant_outputs.append({
                "variant": "通用版",
                "path": str(output),
                "filename": output.name,
                "duration": media_duration(output),
                "size": output.stat().st_size,
                "source_label": source_label,
                "mobile_format": mobile_format,
                "batch_label": batch_label,
            })
        qa = qa_video(output, config)
        qa["segment"] = {
            "index": segment_index,
            "total": segment_total,
            "start": segment["start"],
            "duration": segment["duration"],
            "highlight_score": segment.get("highlight_score", 0),
            "highlight_reasons": segment.get("highlight_reasons", []),
        }
        qa_results.append(qa)
        (work / ("qa.json" if segment_total == 1 else f"qa_part{segment_index:02d}.json")).write_text(
            json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not qa["passed"]:
            raise RuntimeError(f"QA failed for {package_id}: {qa}")
        review = review_root / package_id
        review.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output, review / "video.mp4")
        for info in variant_outputs:
            variant_path = Path(str(info["path"]))
            if variant_path.is_file():
                shutil.copy2(variant_path, review / variant_path.name)
        cover = review / "cover.jpg"
        active_kit = brand_kit(config)
        cover_source = render_cover_image(config, active_kit, output, cover)
        links = tracking_links(config, package_id)
        metadata = {
            "job_id": package_id,
            "source_job_id": row["id"],
            "keyword": str(candidate_metadata.get("keyword") or ""),
            "category": str(candidate_metadata.get("category") or ""),
            "source": {"platform": row["platform"], "url": row["url"], "title": row["title"]},
            "batch_label": batch_label,
            "content_type": strategy.content_type,
            "content_type_confidence": strategy.content_type_confidence,
            "matched_rules": list(strategy.matched_rules),
            "segment_strategy": strategy.segment_strategy,
            "audio_policy": strategy.audio_policy,
            "operator_override": strategy.operator_override,
            "segment": {
                "index": segment_index,
                "total": segment_total,
                "start_sec": segment["start"],
                "end_sec": float(segment["start"]) + float(segment["duration"]),
                "duration_sec": segment["duration"],
                "highlight_score": segment.get("highlight_score", 0),
                "highlight_reasons": segment.get("highlight_reasons", []),
                "signal_scores": segment.get("signal_scores", {}),
                "fallback": bool(segment.get("fallback", False)),
            },
            "ptbr_script": script,
            "hook_version": hook_version,
            "youtube": {
                "title": f"{filename_stem}{title_suffix}"[:100],
                "description": f"{publishing_text[:500]}\n\n▶ {links['youtube']}",
                "hashtags": ["JaguarTV", "Brasil", "Shorts"],
                "cta_url": links["youtube"],
            },
            "tiktok": {"caption": publishing_text[:220], "hashtags": ["JaguarTV", "ParaVoce"], "cta_url": links["tiktok"]},
            "kwai": {"caption": publishing_text[:220], "hashtags": ["JaguarTV", "Brasil"], "cta_url": links["kwai"]},
            "facebook": {
                "text": f"{publishing_text[:500]}\n\n▶ {links['facebook']}",
                "hashtags": ["JaguarTV"],
                "cta_url": links["facebook"],
            },
            "cta_url": config.get("brand", {}).get("default_cta", "https://copa.jarg.top/"),
            "brand_kit": brand_kit(config)["_name"],
            "brand_assets": {
                "cover_source": cover_source,
                "watermark": str(brand_kit(config).get("watermark", {}).get("image") or ""),
                "endcard": str(brand_kit(config).get("endcard", {}).get("image") or ""),
            },
            "output_variants": variant_outputs,
            "hyperframes_package": hyperframes_package,
            "render_engine": render_engine,
            "reaction": reaction.to_dict(),
            "compliance": compliance,
            "tracking_links": links,
            "audio": {
                "mode": audio_mode,
                "reason": audio_reason,
                "source_audio_removed": audio_mode == "localized",
                "source_audio_preserved": audio_mode == "preserve_source",
                "voice": "pt-BR/Luciana" if voice else "",
                "bgm": str(bgm) if bgm else "",
                "bgm_source": bgm_source,
            },
            "localization_profile": localization_profile,
            "visual_cleanup": {
                "layout_mode": str(config.get("edit", {}).get("layout_mode", "vertical")),
                "source_subtitle_mode": cleanup_mode,
                "source_subtitle_crop_bottom_ratio": crop_ratio,
                "ocr": ocr_cleanup,
            },
            "source_outro_trim": source_outro_detection,
            "source_outro_trim_summary": review_source_outro_summary(source_outro_detection),
            "qa": qa,
        }
        (review / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        (review / "review.json").write_text(
            json.dumps({"decision": "pending", "note": "", "reviewed_at": ""}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        storage_result = archive_review_package(config, package_id, review)
        append_event(connect_db(config), row["id"], "SERVER_ARCHIVED", storage_result)
        reviews.append(review)

        if segment_total > 1:
            conn = connect_db(config)
            child_title = f"{row['title']} (Slice {segment_index})"
            conn.execute(
                """
                INSERT OR REPLACE INTO candidates
                (id, parent_id, platform, source_id, url, title, description, duration, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'READY_FOR_REVIEW', ?, ?, ?)
                """,
                (
                    package_id, row["id"], row["platform"], f"{row['source_id']}_slice{segment_index}",
                    row["url"], child_title, row["description"], segment["duration"],
                    json.dumps(metadata, ensure_ascii=False), now_iso(), now_iso()
                )
            )
            conn.commit()

    progress(94, "质量检查通过，正在打包")
    manifest = {
        "job_id": row["id"], "status": "READY_FOR_REVIEW", "created_at": now_iso(),
        "assets": {
            "source": str(media),
            "original_source": str(original_media),
            "voice": str(voice) if voice else "", "bgm": str(bgm) if bgm else "",
            "reviews": [str(path) for path in reviews],
        },
        "segments": segments,
        "strategy": strategy.to_dict(),
        "reaction": reaction.to_dict(),
        "compliance": compliance,
        "render_engine": render_engine,
        "audio_policy": {
            "mode": audio_mode,
            "reason": audio_reason,
            "source_audio_removed": audio_mode == "localized",
            "bgm_source": bgm_source,
        },
        "qa": qa_results,
        "source_outro_trim": source_outro_detection,
    }
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    connection = connect_db(config)
    connection.execute("UPDATE candidates SET status='READY_FOR_REVIEW',updated_at=? WHERE id=?", (now_iso(), row["id"]))
    append_event(connection, row["id"], "READY_FOR_REVIEW", manifest)
    progress(100, "审核包已生成")
    return reviews[0]


def analyze_candidate(
    config: dict[str, Any], candidate: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        raise ValueError("candidate does not exist")
    work = workspace_dir(config) / "jobs" / row["id"]
    media = next(
        (path for path in work.glob("source.*") if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}),
        None,
    )
    if not media:
        raise RuntimeError(f"No downloaded media for {row['id']}")
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    strategy = resolve_production_strategy(
        {**dict(row), "metadata": metadata},
        options,
        default_max_segments=int(config.get("edit", {}).get("max_segments_per_source", 3)),
        default_max_duration=float(short_duration_bounds(config)[1]),
    )
    source_duration = media_duration(media)
    if candidate_too_long(config, source_duration):
        raise RuntimeError(
            f"source duration {source_duration:.1f}s exceeds max_source_duration_sec={source_duration_limit(config):.0f}"
        )
    transcript_file = next(iter(sorted([*work.glob("source*.srt"), *work.glob("source*.vtt")])), None)
    if source_duration <= short_video_threshold(config):
        segments = [whole_source_segment(min(source_duration, strategy.max_duration), strategy.segment_strategy)]
    else:
        segments = analyze_video(
            media,
            source_duration=source_duration,
            max_segments=strategy.max_segments,
            max_duration=strategy.max_duration,
            strategy=strategy.segment_strategy,
            transcript_path=transcript_file,
        )
    result = {
        "candidate_id": row["id"],
        "media": str(media),
        "source_duration": source_duration,
        "strategy": strategy.to_dict(),
        "segments": segments,
    }
    (work / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata["analysis"] = result
    connection.execute(
        "UPDATE candidates SET metadata_json=?,updated_at=? WHERE id=?",
        (json.dumps(metadata, ensure_ascii=False), now_iso(), row["id"]),
    )
    append_event(connection, row["id"], "ANALYZED", result)
    return result


def candidate_has_review_outputs(config: dict[str, Any], candidate_id: str) -> bool:
    """Return true when at least one review package already has a rendered video."""
    review_root = workspace_dir(config) / "ready_for_review"
    package_dirs = [review_root / candidate_id, *review_root.glob(f"{candidate_id}_part*")]
    for package in package_dirs:
        if not package.is_dir():
            continue
        if (package / "video.mp4").is_file():
            return True
        if any(package.glob("*-通用版.mp4")) or any(package.glob("*-FB版.mp4")):
            return True
    return False


def produce_top(
    config: dict[str, Any], limit: int, candidate: str | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, int]:
    connection = connect_db(config)
    if candidate:
        rows = connection.execute("SELECT * FROM candidates WHERE id=?", (candidate,)).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM candidates WHERE status IN ('DOWNLOADED','PRODUCTION_FAILED') ORDER BY score DESC LIMIT ?", (limit,)
        ).fetchall()
    stats = {"selected": len(rows), "produced": 0, "failed": 0}
    for row in rows:
        try:
            produce_candidate(config, row, progress_callback=progress_callback, options=options)
            stats["produced"] += 1
        except Exception as error:
            stats["failed"] += 1
            blocked = isinstance(error, PermissionError) and str(error).startswith("BLOCKED_RIGHTS")
            status = "BLOCKED_RIGHTS" if blocked else "PRODUCTION_FAILED"
            if not blocked and candidate_has_review_outputs(config, row["id"]):
                status = "READY_FOR_REVIEW"
            connection.execute("UPDATE candidates SET status=?,updated_at=? WHERE id=?", (status, now_iso(), row["id"]))
            event_type = "PRODUCTION_PARTIAL_FAILED" if status == "READY_FOR_REVIEW" else status
            append_event(connection, row["id"], event_type, {"error": str(error)})
            print(f"WARN produce {row['id']}: {error}")
    return stats


def generate_review_index(config: dict[str, Any]) -> Path:
    root = workspace_dir(config) / "ready_for_review"
    root.mkdir(parents=True, exist_ok=True)
    cards = []
    for directory in sorted((path for path in root.iterdir() if path.is_dir()), reverse=True):
        metadata_path = directory / "metadata.json"
        if not metadata_path.exists():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        source = metadata.get("source", {})
        audio = metadata.get("audio", {})
        cleanup = metadata.get("visual_cleanup", {})
        if audio.get("mode") == "preserve_source":
            audio_summary = "música original preservada · sem narração/legendas"
        elif audio.get("mode") in {"bgm_only", "silent"}:
            audio_summary = "sem áudio fixo adicionado"
        else:
            audio_summary = "narração pt-BR · áudio original removido"
        video_path = directory / "video.mp4"
        version = int(video_path.stat().st_mtime) if video_path.exists() else 0
        cards.append(
            f"<article><video controls preload='metadata' src='{directory.name}/video.mp4?v={version}'></video>"
            f"<div><h2>{html_escape(metadata.get('youtube', {}).get('title', directory.name))}</h2>"
            f"<p>{html_escape(source.get('platform', ''))} · <a href='{html_escape(source.get('url', ''))}'>源视频</a></p>"
            f"<p>Audio: {html_escape(audio_summary)}<br>"
            f"Visual cleanup: {html_escape(cleanup.get('source_subtitle_mode', 'off'))}</p>"
            f"<p>{html_escape(metadata.get('ptbr_script', '')[:420])}</p>"
            f"<a class='button' href='{directory.name}/metadata.json'>查看发布包</a></div></article>"
        )
    html = f"""<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>JaguarTV Review</title><style>
body{{margin:0;font-family:Arial,sans-serif;background:#f4f5f7;color:#17191c}}header{{padding:24px 5vw;background:#111;color:#fff;position:sticky;top:0;z-index:2}}main{{max-width:1180px;margin:28px auto;padding:0 22px;display:grid;gap:22px}}article{{display:grid;grid-template-columns:260px 1fr;background:#fff;border:1px solid #ddd;border-radius:8px;overflow:hidden}}video{{width:260px;aspect-ratio:9/16;background:#000}}article div{{padding:24px}}h1,h2{{margin:0 0 12px}}p{{line-height:1.55}}a{{color:#087f5b}}.button{{display:inline-block;padding:10px 14px;background:#111;color:#fff;text-decoration:none;border-radius:6px}}@media(max-width:720px){{article{{grid-template-columns:1fr}}video{{width:100%;max-height:70vh}}}}
</style></head><body><header><h1>JaguarTV · Fila de revisão</h1><p>{len(cards)} vídeos prontos</p></header><main>{''.join(cards) or '<p>Nenhum vídeo pronto.</p>'}</main></body></html>"""
    index = root / "index.html"
    index.write_text(html, encoding="utf-8")
    return index


def html_escape(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
