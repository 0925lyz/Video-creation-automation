from __future__ import annotations

import json
import mimetypes
import os
import socket
import threading
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from .core import (
    category_active_today,
    connect_db,
    discover,
    download_top,
    generate_review_index,
    inspect_url,
    list_candidates,
    now_iso,
    produce_top,
    resolve_config_path,
    tracking_links,
    workspace_dir,
)
from .sessions import check_session, delete_session, list_sessions, save_session


WEB_ROOT = Path(__file__).resolve().parent / "web"
PLATFORMS = ("youtube", "facebook", "tiktok", "kwai")
EVENT_TYPES = ("landing_click", "download_started", "install", "registration", "first_watch")
REVIEW_DECISIONS = ("APPROVED", "REVISION_REQUIRED")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def int_value(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def dashboard_overview(config: dict[str, Any]) -> dict[str, Any]:
    connection = connect_db(config)
    status_counts = {
        row["status"]: row["count"]
        for row in connection.execute("SELECT status,COUNT(*) count FROM candidates GROUP BY status")
    }
    publication_counts = {
        row["status"]: row["count"]
        for row in connection.execute("SELECT status,COUNT(*) count FROM publications GROUP BY status")
    }
    latest_metrics = connection.execute(
        """
        WITH ranked AS (
          SELECT *,ROW_NUMBER() OVER (
            PARTITION BY candidate_id,platform ORDER BY captured_at DESC,id DESC
          ) rank
          FROM performance_snapshots
        )
        SELECT
          COALESCE(SUM(views),0) views,
          COALESCE(SUM(likes),0) likes,
          COALESCE(SUM(comments),0) comments,
          COALESCE(SUM(shares),0) shares,
          COALESCE(SUM(clicks),0) clicks,
          COALESCE(SUM(installs),0) installs,
          COALESCE(SUM(registrations),0) registrations
        FROM ranked WHERE rank=1
        """
    ).fetchone()
    total = sum(status_counts.values())
    ready = status_counts.get("READY_FOR_REVIEW", 0)
    approved = status_counts.get("APPROVED", 0)
    published = connection.execute(
        "SELECT COUNT(DISTINCT candidate_id) count FROM publications WHERE status='PUBLISHED'"
    ).fetchone()["count"]
    metrics = dict(latest_metrics)
    conversions = conversion_totals(connection)
    # Attributed events (server-side postbacks) take precedence over manually
    # entered platform snapshots wherever both exist.
    clicks = conversions["landing_click"] or metrics["clicks"]
    installs = conversions["install"] or metrics["installs"]
    registrations = conversions["registration"] or metrics["registrations"]
    first_watch = conversions["first_watch"]
    conversion_rate = registrations / clicks if clicks else 0.0
    activation_rate = first_watch / registrations if registrations else 0.0
    return {
        "generated_at": now_iso(),
        "kpis": {
            "inventory": total,
            "ready": ready,
            "approved": approved,
            "scheduled": publication_counts.get("QUEUED", 0) + publication_counts.get("SCHEDULED", 0),
            "published": published,
            **metrics,
            "clicks": clicks,
            "installs": installs,
            "registrations": registrations,
            "first_watch": first_watch,
            "download_started": conversions["download_started"],
            "conversion_rate": conversion_rate,
            "activation_rate": activation_rate,
        },
        "funnel": [
            {"label": "发现素材", "value": total},
            {"label": "完成制作", "value": ready + approved + published},
            {"label": "审核通过", "value": approved + published},
            {"label": "已发布", "value": published},
            {"label": "落地页点击", "value": clicks},
            {"label": "开始下载", "value": conversions["download_started"]},
            {"label": "安装", "value": installs},
            {"label": "注册", "value": registrations},
            {"label": "首次观看", "value": first_watch},
        ],
        "candidate_statuses": status_counts,
        "publication_statuses": publication_counts,
        "platforms": platform_performance(connection),
        "keywords": keyword_performance(connection),
    }


def platform_performance(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        WITH ranked AS (
          SELECT *,ROW_NUMBER() OVER (
            PARTITION BY candidate_id,platform ORDER BY captured_at DESC,id DESC
          ) rank
          FROM performance_snapshots
        ), metric AS (
          SELECT platform,SUM(views) views,SUM(clicks) clicks,SUM(installs) installs,
                 SUM(registrations) registrations,SUM(likes+comments+shares) engagements
          FROM ranked WHERE rank=1 GROUP BY platform
        ), posted AS (
          SELECT platform,COUNT(*) posts FROM publications WHERE status='PUBLISHED' GROUP BY platform
        )
        SELECT COALESCE(metric.platform,posted.platform) platform,
               COALESCE(posts,0) posts,COALESCE(views,0) views,
               COALESCE(clicks,0) clicks,COALESCE(installs,0) installs,
               COALESCE(registrations,0) registrations,COALESCE(engagements,0) engagements
        FROM metric LEFT JOIN posted ON posted.platform=metric.platform
        UNION ALL
        SELECT posted.platform,posts,0,0,0,0,0 FROM posted
        WHERE posted.platform NOT IN (SELECT platform FROM metric)
        """
    ).fetchall()
    mapped = {row["platform"]: dict(row) for row in rows}
    return [mapped.get(platform, {"platform": platform, "posts": 0, "views": 0, "clicks": 0, "installs": 0, "registrations": 0, "engagements": 0}) for platform in PLATFORMS]


def keyword_performance(connection: Any) -> list[dict[str, Any]]:
    metric_rows = connection.execute(
        """
        WITH ranked AS (
          SELECT *,ROW_NUMBER() OVER (
            PARTITION BY candidate_id,platform ORDER BY captured_at DESC,id DESC
          ) rank
          FROM performance_snapshots
        )
        SELECT candidate_id,SUM(views) views,SUM(clicks) clicks,SUM(registrations) registrations
        FROM ranked WHERE rank=1 GROUP BY candidate_id
        """
    ).fetchall()
    metrics = {row["candidate_id"]: dict(row) for row in metric_rows}
    conversion_rows = connection.execute(
        "SELECT candidate_id,event_type,COUNT(*) count FROM conversion_events GROUP BY candidate_id,event_type"
    ).fetchall()
    conversions: dict[str, dict[str, int]] = {}
    for row in conversion_rows:
        conversions.setdefault(row["candidate_id"], {})[row["event_type"]] = row["count"]
    grouped: dict[str, dict[str, Any]] = {}
    for row in connection.execute("SELECT id,status,metadata_json FROM candidates"):
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        keyword = str(metadata.get("keyword") or "未标记关键词")
        entry = grouped.setdefault(keyword, {
            "keyword": keyword, "candidates": 0, "produced": 0, "views": 0,
            "clicks": 0, "registrations": 0, "first_watch": 0,
        })
        entry["candidates"] += 1
        entry["produced"] += int(row["status"] in {"READY_FOR_REVIEW", "APPROVED"})
        candidate_metrics = metrics.get(row["id"], {})
        candidate_conversions = conversions.get(row["id"], {})
        entry["views"] += int(candidate_metrics.get("views", 0))
        entry["clicks"] += int(candidate_conversions.get("landing_click", 0) or candidate_metrics.get("clicks", 0))
        entry["registrations"] += int(candidate_conversions.get("registration", 0) or candidate_metrics.get("registrations", 0))
        entry["first_watch"] += int(candidate_conversions.get("first_watch", 0))
    values = list(grouped.values())
    for value in values:
        value["score"] = round(
            value["first_watch"] * 40 + value["registrations"] * 20
            + value["clicks"] * 0.5 + value["views"] * 0.001,
            2,
        )
    return sorted(values, key=lambda item: (item["score"], item["candidates"]), reverse=True)[:12]


def candidate_rows(config: dict[str, Any], status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    rows = list_candidates(config, status, limit)
    result = []
    root = workspace_dir(config)
    connection = connect_db(config)
    failures = {
        row["candidate_id"]: dict(row)
        for row in connection.execute(
            """
            SELECT e.candidate_id,e.event_type,e.payload_json,e.created_at
            FROM events e
            INNER JOIN (
              SELECT candidate_id,MAX(id) id FROM events
              WHERE event_type IN ('DOWNLOAD_FAILED','PRODUCTION_FAILED','QA_FAILED')
              GROUP BY candidate_id
            ) latest ON latest.id=e.id
            """
        )
    }
    for row in rows:
        item = dict(row)
        metadata: dict[str, Any] = {}
        try:
            metadata = json.loads(item.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            pass
        item.pop("metadata_json", None)
        item["keyword"] = str(metadata.get("keyword") or "")
        item["score_breakdown"] = metadata.get("score_breakdown") or {}
        thumbnail = metadata.get("thumbnail") or ""
        if not thumbnail and isinstance(metadata.get("thumbnails"), list) and metadata["thumbnails"]:
            last = metadata["thumbnails"][-1]
            thumbnail = last.get("url", "") if isinstance(last, dict) else ""
        item["thumbnail_url"] = str(thumbnail)
        review = root / "ready_for_review" / item["id"]
        if (review / "cover.jpg").exists():
            item["cover_url"] = f"/media/{item['id']}/cover.jpg?v={int((review / 'cover.jpg').stat().st_mtime)}"
            item["video_url"] = f"/media/{item['id']}/video.mp4?v={int((review / 'video.mp4').stat().st_mtime)}"
        else:
            item["cover_url"] = ""
            item["video_url"] = ""
        item["lark_url"] = ""
        metadata_path = review / "metadata.json"
        if metadata_path.exists():
            try:
                item["lark_url"] = str(json.loads(metadata_path.read_text(encoding="utf-8")).get("lark", {}).get("video_url") or "")
            except (json.JSONDecodeError, OSError):
                pass
        failure = failures.get(item["id"])
        item["failure_event"] = ""
        item["failure_detail"] = ""
        item["failure_at"] = ""
        if failure and item["status"] in {"DOWNLOAD_FAILED", "PRODUCTION_FAILED", "QA_FAILED"}:
            try:
                failure_payload = json.loads(failure["payload_json"] or "{}")
            except json.JSONDecodeError:
                failure_payload = {}
            detail = str(failure_payload.get("error") or failure_payload.get("stderr") or "").strip()
            item["failure_event"] = failure["event_type"]
            item["failure_detail"] = detail[-4000:]
            item["failure_at"] = failure["created_at"]
        result.append(item)
    return result


def publication_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    connection = connect_db(config)
    return [
        dict(row) for row in connection.execute(
            """
            SELECT publications.*,candidates.title FROM publications
            LEFT JOIN candidates ON candidates.id=publications.candidate_id
            ORDER BY COALESCE(publications.scheduled_at,publications.created_at) DESC LIMIT 200
            """
        )
    ]


def worker_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    connection = connect_db(config)
    return [dict(row) for row in connection.execute("SELECT * FROM workers ORDER BY last_seen DESC")]


def feedback_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    connection = connect_db(config)
    return [dict(row) for row in connection.execute("SELECT * FROM feedback_actions ORDER BY id DESC LIMIT 100")]


def register_coordinator(config: dict[str, Any], port: int) -> None:
    connection = connect_db(config)
    host = socket.gethostname()
    connection.execute(
        """
        INSERT INTO workers(id,name,role,host,status,current_job,last_seen,metadata_json)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET status=excluded.status,last_seen=excluded.last_seen,
          current_job=excluded.current_job,metadata_json=excluded.metadata_json
        """,
        (f"{host}-dashboard", "本地控制台", "coordinator", host, "ONLINE", "", now_iso(), json.dumps({"port": port})),
    )
    connection.commit()


def save_publication(config: dict[str, Any], payload: dict[str, Any]) -> int:
    candidate = str(payload.get("candidate_id") or "").strip()
    platform = str(payload.get("platform") or "").strip().lower()
    if not candidate or platform not in PLATFORMS:
        raise ValueError("candidate_id and a supported platform are required")
    connection = connect_db(config)
    row = connection.execute("SELECT status FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        raise ValueError("candidate does not exist")
    if row["status"] != "APPROVED":
        raise ValueError(
            f"candidate must be APPROVED before scheduling (current status: {row['status']}); "
            "submit a review decision via POST /api/review first"
        )
    timestamp = now_iso()
    cursor = connection.execute(
        """
        INSERT INTO publications(candidate_id,platform,account,scheduled_at,status,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        (candidate, platform, str(payload.get("account") or ""), payload.get("scheduled_at") or None, "QUEUED", timestamp, timestamp),
    )
    connection.commit()
    return int(cursor.lastrowid)


def save_metrics(config: dict[str, Any], payload: dict[str, Any]) -> int:
    candidate = str(payload.get("candidate_id") or "").strip()
    platform = str(payload.get("platform") or "").strip().lower()
    if not candidate or platform not in PLATFORMS:
        raise ValueError("candidate_id and a supported platform are required")
    fields = ("views", "likes", "comments", "shares", "clicks", "installs", "registrations")
    values = [int_value(payload.get(field)) for field in fields]
    connection = connect_db(config)
    cursor = connection.execute(
        f"INSERT INTO performance_snapshots(candidate_id,platform,captured_at,{','.join(fields)}) VALUES(?,?,?,{','.join('?' for _ in fields)})",
        (candidate, platform, payload.get("captured_at") or now_iso(), *values),
    )
    connection.commit()
    propose_feedback(connection, candidate, platform, dict(zip(fields, values)))
    return int(cursor.lastrowid)


def propose_feedback(connection: Any, candidate: str, platform: str, metrics: dict[str, int]) -> None:
    views = metrics["views"]
    if views < 1_000:
        return
    registration_rate = metrics["registrations"] / views
    share_rate = metrics["shares"] / views
    if registration_rate < 0.002 and share_rate < 0.01:
        return
    row = connection.execute("SELECT metadata_json FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        return
    try:
        keyword = str(json.loads(row["metadata_json"] or "{}").get("keyword") or "")
    except json.JSONDecodeError:
        keyword = ""
    reason = f"{platform}: 注册率 {registration_rate:.2%}，分享率 {share_rate:.2%}"
    score = registration_rate * 1000 + share_rate * 100
    connection.execute(
        "INSERT INTO feedback_actions(candidate_id,keyword,action_type,reason,score,status,created_at) VALUES(?,?,?,?,?,'PROPOSED',?)",
        (candidate, keyword, "BOOST_KEYWORD", reason, score, now_iso()),
    )
    connection.commit()


def conversion_totals(connection: Any, candidate_id: str | None = None) -> dict[str, int]:
    """Deduplicated conversion counts from JaguarTV postback events.

    A visitor is counted once per (candidate, event_type); anonymous events
    (empty visitor_id) fall back to raw row counts.
    """
    where = "WHERE candidate_id=?" if candidate_id else ""
    args = (candidate_id,) if candidate_id else ()
    rows = connection.execute(
        f"""
        SELECT event_type,
               COUNT(DISTINCT CASE WHEN visitor_id!='' THEN candidate_id||':'||visitor_id END)
                 + SUM(CASE WHEN visitor_id='' THEN 1 ELSE 0 END) AS total
        FROM conversion_events {where} GROUP BY event_type
        """,
        args,
    ).fetchall()
    totals = {event_type: 0 for event_type in EVENT_TYPES}
    for row in rows:
        if row["event_type"] in totals:
            totals[row["event_type"]] = int(row["total"] or 0)
    return totals


def save_events(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, int]:
    """Ingest one event or a batch: {"events": [...]} or a single event object."""
    events = payload.get("events") if isinstance(payload.get("events"), list) else [payload]
    connection = connect_db(config)
    known = {
        row["id"] for row in connection.execute("SELECT id FROM candidates")
    }
    saved, skipped = 0, 0
    for event in events:
        if not isinstance(event, dict):
            skipped += 1
            continue
        event_type = str(event.get("event_type") or "").strip().lower()
        utm_content = str(event.get("utm_content") or "").strip()
        candidate = str(event.get("candidate_id") or "").strip()
        hook_version = str(event.get("hook_version") or "").strip()
        if utm_content and not candidate:
            candidate, _, parsed_hook = utm_content.partition("_")
            hook_version = hook_version or parsed_hook
        if event_type not in EVENT_TYPES or not candidate:
            skipped += 1
            continue
        if candidate not in known:
            skipped += 1
            continue
        connection.execute(
            """
            INSERT INTO conversion_events
              (candidate_id,platform,hook_version,event_type,occurred_at,visitor_id,payload_json)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                candidate,
                str(event.get("platform") or event.get("utm_source") or "").strip().lower(),
                hook_version,
                event_type,
                str(event.get("occurred_at") or now_iso()),
                str(event.get("visitor_id") or ""),
                json.dumps(event.get("payload") or {}, ensure_ascii=False),
            ),
        )
        saved += 1
    connection.commit()
    if not saved:
        raise ValueError("no valid events; require event_type in "
                         f"{EVENT_TYPES} and a known candidate_id/utm_content")
    return {"saved": saved, "skipped": skipped}


def attribution_report(config: dict[str, Any], candidate: str) -> dict[str, Any]:
    connection = connect_db(config)
    row = connection.execute("SELECT id,title,status FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        raise ValueError("candidate does not exist")
    totals = conversion_totals(connection, candidate)
    by_platform: dict[str, dict[str, int]] = {}
    for event in connection.execute(
        "SELECT platform,event_type,COUNT(*) count FROM conversion_events WHERE candidate_id=? GROUP BY platform,event_type",
        (candidate,),
    ):
        entry = by_platform.setdefault(event["platform"] or "unknown", {})
        entry[event["event_type"]] = event["count"]
    recent = [
        dict(item) for item in connection.execute(
            "SELECT event_type,platform,hook_version,occurred_at,visitor_id FROM conversion_events "
            "WHERE candidate_id=? ORDER BY occurred_at DESC LIMIT 50",
            (candidate,),
        )
    ]
    return {
        "candidate_id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "tracking_links": tracking_links(config, candidate),
        "funnel": totals,
        "by_platform": by_platform,
        "recent_events": recent,
    }


def save_review(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    candidate = str(payload.get("candidate_id") or "").strip()
    decision = str(payload.get("decision") or "").strip().upper()
    if not candidate or decision not in REVIEW_DECISIONS:
        raise ValueError(f"candidate_id and decision in {REVIEW_DECISIONS} are required")
    connection = connect_db(config)
    row = connection.execute("SELECT id,status FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        raise ValueError("candidate does not exist")
    if row["status"] not in {"READY_FOR_REVIEW", "APPROVED", "REVISION_REQUIRED"}:
        raise ValueError(f"candidate status {row['status']} cannot be reviewed")
    timestamp = now_iso()
    connection.execute(
        "UPDATE candidates SET status=?,updated_at=? WHERE id=?", (decision, timestamp, candidate)
    )
    connection.execute(
        "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (candidate, f"REVIEW_{decision}", json.dumps({
            "note": str(payload.get("note") or ""),
            "reviewer": str(payload.get("reviewer") or ""),
        }, ensure_ascii=False), timestamp),
    )
    connection.commit()
    review_file = workspace_dir(config) / "ready_for_review" / candidate / "review.json"
    if review_file.parent.exists():
        review_file.write_text(
            json.dumps({
                "decision": decision.lower(),
                "note": str(payload.get("note") or ""),
                "reviewer": str(payload.get("reviewer") or ""),
                "reviewed_at": timestamp,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return {"candidate_id": candidate, "status": decision}


def keywords_file_path(config: dict[str, Any]) -> Path:
    return resolve_config_path(config, config.get("sources", {}).get("keywords_file", "config/keywords.jaguartv.yaml"))


def load_keyword_groups(config: dict[str, Any]) -> list[dict[str, Any]]:
    path = keywords_file_path(config)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    groups = []
    for name, group in (data or {}).items():
        group = group or {}
        groups.append({
            "name": name,
            "weight": float(group.get("weight", 1.0)),
            "days": group.get("days") or [],
            "enabled": group.get("enabled", True) is not False,
            "active_today": category_active_today(group),
            "terms": group.get("terms") or {},
        })
    return groups


def save_keyword_group(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Create/update/delete one keyword group; keeps YAML on disk as the
    single source of truth so CLI and UI stay in sync."""
    name = str(payload.get("name") or "").strip()
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError("valid group name is required (letters/digits/underscores)")
    path = keywords_file_path(config)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    if payload.get("delete"):
        if name not in data:
            raise ValueError("group does not exist")
        del data[name]
    else:
        terms = payload.get("terms") or {}
        if not isinstance(terms, dict) or not any(isinstance(v, list) and v for v in terms.values()):
            raise ValueError("terms must map languages to non-empty lists, e.g. {\"en\": [\"goal\"]}")
        group: dict[str, Any] = {
            "weight": max(0.0, min(2.0, float(payload.get("weight", 1.0)))),
            "terms": {str(k): [str(t).strip() for t in v if str(t).strip()] for k, v in terms.items()},
        }
        days = payload.get("days") or []
        valid_days = [d for d in (str(x).strip().lower()[:3] for x in days) if d in
                      {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}]
        if valid_days:
            group["days"] = valid_days
        if payload.get("enabled") is False:
            group["enabled"] = False
        data[name] = group
    backup = path.with_suffix(".yaml.bak")
    if path.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"saved": name, "groups": len(data)}


def skip_candidate(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    candidate = str(payload.get("candidate_id") or "").strip()
    if not candidate:
        raise ValueError("candidate_id is required")
    connection = connect_db(config)
    row = connection.execute("SELECT status FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        raise ValueError("candidate does not exist")
    if row["status"] not in {"DISCOVERED", "SKIPPED"}:
        raise ValueError(f"only DISCOVERED candidates can be skipped (current: {row['status']})")
    connection.execute("UPDATE candidates SET status='SKIPPED',updated_at=? WHERE id=?", (now_iso(), candidate))
    connection.commit()
    return {"candidate_id": candidate, "status": "SKIPPED"}


def system_settings(config: dict[str, Any]) -> dict[str, Any]:
    kit_name = str(config.get("brand", {}).get("default_kit") or "jaguartv")
    kit = (config.get("brand", {}).get("kits") or {}).get(kit_name) or {}
    edit = config.get("edit", {})
    return {
        "config_path": str(config.get("_path") or ""),
        "workspace": str(config.get("run", {}).get("workspace", "workspace")),
        "sources_enabled": config.get("sources", {}).get("enabled", []),
        "edit": {
            "render_engine": str(edit.get("render_engine", "ffmpeg")),
            "output_duration_sec": edit.get("output_duration_sec", [12, 30]),
            "max_segments_per_source": int(edit.get("max_segments_per_source", 3)),
            "layout_mode": str(edit.get("layout_mode", "original")),
        },
        "remotion": config.get("remotion", {}),
        "brand": {
            "kit": kit_name,
            "cta": str(config.get("brand", {}).get("default_cta") or ""),
            "watermark": kit.get("watermark", {}),
            "cover": kit.get("cover", {"mode": "frame", "image": ""}),
            "endcard": kit.get("endcard", {}),
        },
    }


def save_system_settings(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    config_path = Path(str(config.get("_path") or "")).expanduser().resolve()
    if not config_path.exists():
        raise ValueError("config file does not exist")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data.setdefault("edit", {})
    data.setdefault("brand", {}).setdefault("kits", {})
    kit_name = str(data.get("brand", {}).get("default_kit") or "jaguartv")
    kit = data["brand"]["kits"].setdefault(kit_name, {})
    kit.setdefault("watermark", {})
    kit.setdefault("cover", {})
    kit.setdefault("endcard", {})

    edit_payload = payload.get("edit") or {}
    if "output_duration_sec" in edit_payload:
        values = edit_payload.get("output_duration_sec") or [12, 30]
        if not isinstance(values, list) or len(values) < 2:
            raise ValueError("output_duration_sec must be [min,max]")
        minimum = max(5, int(values[0]))
        maximum = min(60, max(minimum, int(values[1])))
        data["edit"]["output_duration_sec"] = [minimum, maximum]
    if "max_segments_per_source" in edit_payload:
        data["edit"]["max_segments_per_source"] = max(1, min(10, int(edit_payload.get("max_segments_per_source") or 3)))
    if "layout_mode" in edit_payload:
        layout = str(edit_payload.get("layout_mode") or "original").strip().lower()
        if layout not in {"original", "vertical"}:
            raise ValueError("layout_mode must be original or vertical")
        data["edit"]["layout_mode"] = layout
        data["edit"]["aspect_ratio"] = "source" if layout == "original" else "9:16"
    if "render_engine" in edit_payload:
        engine = str(edit_payload.get("render_engine") or "ffmpeg").strip().lower()
        if engine not in {"ffmpeg", "remotion"}:
            raise ValueError("render_engine must be ffmpeg or remotion")
        data["edit"]["render_engine"] = engine

    remotion_payload = payload.get("remotion") or {}
    if isinstance(remotion_payload, dict):
        data.setdefault("remotion", {})
        for field in ("top_badge", "bottom_headline", "bottom_subline", "endcard_cta"):
            if field in remotion_payload:
                data["remotion"][field] = str(remotion_payload.get(field) or "").strip()
        for field in ("content_bgm_volume", "endcard_bgm_volume"):
            if field in remotion_payload:
                data["remotion"][field] = max(0.0, min(1.0, float(remotion_payload.get(field) or 0)))
        if "add_bgm_under_source" in remotion_payload:
            data["remotion"]["add_bgm_under_source"] = bool(remotion_payload.get("add_bgm_under_source"))

    brand_payload = payload.get("brand") or {}
    if "cta" in brand_payload:
        data["brand"]["default_cta"] = str(brand_payload.get("cta") or "").strip()
    for key in ("watermark", "cover", "endcard"):
        incoming = brand_payload.get(key)
        if not isinstance(incoming, dict):
            continue
        target = kit.setdefault(key, {})
        for field in ("mode", "image", "text", "position", "site", "title", "tagline"):
            if field in incoming:
                target[field] = str(incoming.get(field) or "").strip()
        for field in ("opacity",):
            if field in incoming:
                target[field] = max(0.0, min(1.0, float(incoming.get(field) or 0)))
        for field in ("width", "duration_sec"):
            if field in incoming:
                target[field] = max(1, int(incoming.get(field) or target.get(field) or 1))

    backup = config_path.with_suffix(".yaml.bak")
    backup.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config.clear()
    config.update(data)
    config["_path"] = str(config_path)
    config["_root"] = str(config_path.parent.parent)
    return system_settings(config)


class DashboardApplication(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], config: dict[str, Any]):
        super().__init__(address, DashboardHandler)
        self.config = config
        self.tasks: dict[str, dict[str, Any]] = {}
        self.tasks_lock = threading.Lock()
        self.production_lock = threading.Lock()

    def start_action(self, payload: dict[str, Any]) -> str:
        candidate_ids = payload.get("candidate_ids") or []
        if not isinstance(candidate_ids, list):
            raise ValueError("candidate_ids must be a list")
        single = str(payload.get("candidate_id") or "").strip()
        candidate_ids = [str(value).strip() for value in candidate_ids if str(value).strip()]
        if single and single not in candidate_ids:
            candidate_ids.append(single)
        candidate_ids = list(dict.fromkeys(candidate_ids))
        if len(candidate_ids) > 100:
            raise ValueError("a batch can contain at most 100 candidates")
        task_id = uuid.uuid4().hex[:12]
        task = {
            "id": task_id, "action": payload.get("action"), "status": "RUNNING",
            "started_at": now_iso(), "result": None, "error": "", "progress": 0,
            "completed": 0, "total": len(candidate_ids) or 1, "current_candidate": "",
            "message": "任务已进入队列", "candidate_ids": candidate_ids,
        }
        with self.tasks_lock:
            requested = set(candidate_ids)
            for active in self.tasks.values():
                if active.get("status") == "RUNNING" and requested.intersection(active.get("candidate_ids") or []):
                    raise ValueError("selected candidate is already running in another task")
            self.tasks[task_id] = task
        enriched = {**payload, "candidate_ids": candidate_ids}
        threading.Thread(target=self._run_action, args=(task_id, enriched), daemon=True).start()
        return task_id

    def update_task(self, task_id: str, **values: Any) -> None:
        with self.tasks_lock:
            self.tasks[task_id].update(values)

    def run_candidate_batch(self, task_id: str, action: str, candidate_ids: list[str]) -> dict[str, Any]:
        if not candidate_ids:
            raise ValueError("select at least one candidate")
        items = []
        failed = 0
        total = len(candidate_ids)
        for index, candidate in enumerate(candidate_ids):
            self.update_task(
                task_id, current_candidate=candidate, completed=index,
                progress=max(2, int(index / total * 95)),
                message=f"正在{('下载' if action == 'download' else '制作' if action == 'produce' else '忽略')} {index + 1}/{total}",
            )
            if action == "download":
                result = download_top(self.config, 1, candidate)
                item_failed = int(result.get("failed", 0)) or int(result.get("selected", 0) == 0)
            elif action == "produce":
                def production_progress(percent: int, message: str) -> None:
                    base = index / total * 95
                    share = 95 / total
                    self.update_task(
                        task_id, progress=int(base + percent / 100 * share),
                        message=f"{message} · {index + 1}/{total}",
                    )

                self.update_task(task_id, message=f"等待制作资源 · {index + 1}/{total}")
                with self.production_lock:
                    result = produce_top(self.config, 1, candidate, progress_callback=production_progress)
                item_failed = int(result.get("failed", 0)) or int(result.get("selected", 0) == 0)
            else:
                try:
                    result = skip_candidate(self.config, {"candidate_id": candidate})
                    item_failed = 0
                except ValueError as error:
                    result = {"error": str(error)}
                    item_failed = 1
            failed += int(bool(item_failed))
            items.append({"candidate_id": candidate, "result": result, "failed": bool(item_failed)})
            self.update_task(task_id, completed=index + 1, progress=int((index + 1) / total * 95))
        return {"selected": total, "completed": total - failed, "failed": failed, "items": items}

    def _run_action(self, task_id: str, payload: dict[str, Any]) -> None:
        action = str(payload.get("action") or "")
        try:
            if action == "discover":
                self.update_task(task_id, progress=10, message=f"正在搜索 {payload.get('platform') or 'youtube'}")
                result = discover(self.config, platforms=[str(payload.get("platform") or "youtube")], limit=int_value(payload.get("limit"), 3))
            elif action == "ingest":
                url = str(payload.get("url") or "").strip()
                if not url:
                    raise ValueError("url is required")
                self.update_task(task_id, progress=15, message="正在读取小红书作品信息")
                result = {"candidate_id": inspect_url(self.config, url)}
            elif action in {"download", "produce", "skip"}:
                result = self.run_candidate_batch(task_id, action, payload.get("candidate_ids") or [])
            elif action == "review":
                result = {"index": str(generate_review_index(self.config))}
            else:
                raise ValueError(f"unsupported action: {action}")
            failed = int(result.get("failed", 0)) if isinstance(result, dict) else 0
            message = f"任务完成，失败 {failed} 条" if failed else "任务完成"
            state = {"status": "COMPLETED", "result": result, "error": "", "progress": 100,
                     "completed": self.tasks[task_id].get("total", 1), "current_candidate": "",
                     "message": message, "finished_at": now_iso()}
        except Exception as error:
            state = {"status": "FAILED", "result": None, "error": str(error), "message": str(error),
                     "progress": 100, "current_candidate": "", "finished_at": now_iso()}
        with self.tasks_lock:
            self.tasks[task_id].update(state)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardApplication

    def log_message(self, format: str, *args: Any) -> None:
        print(f"dashboard {self.address_string()} {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/overview":
                return self.send_json(dashboard_overview(self.server.config))
            if parsed.path == "/api/candidates":
                status = query.get("status", [None])[0]
                limit = int_value(query.get("limit", [100])[0], 100)
                return self.send_json(candidate_rows(self.server.config, status, limit))
            if parsed.path == "/api/publications":
                return self.send_json(publication_rows(self.server.config))
            if parsed.path == "/api/workers":
                return self.send_json(worker_rows(self.server.config))
            if parsed.path == "/api/feedback":
                return self.send_json(feedback_rows(self.server.config))
            if parsed.path == "/api/keywords":
                return self.send_json(load_keyword_groups(self.server.config))
            if parsed.path == "/api/settings":
                return self.send_json(system_settings(self.server.config))
            if parsed.path == "/api/sessions":
                return self.send_json(list_sessions(self.server.config))
            if parsed.path == "/api/attribution":
                candidate = (query.get("candidate_id") or [""])[0].strip()
                if not candidate:
                    return self.send_json({"error": "candidate_id is required"}, HTTPStatus.BAD_REQUEST)
                return self.send_json(attribution_report(self.server.config, candidate))
            if parsed.path == "/api/tasks":
                with self.server.tasks_lock:
                    tasks = list(self.server.tasks.values())[-50:]
                return self.send_json(tasks)
            if parsed.path.startswith("/media/"):
                return self.send_media(parsed.path.removeprefix("/media/"))
            return self.send_static(parsed.path)
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/media/"):
            return self.send_media(parsed.path.removeprefix("/media/"))
        return self.send_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/actions":
                task_id = self.server.start_action(payload)
                return self.send_json({"task_id": task_id, "status": "RUNNING"}, HTTPStatus.ACCEPTED)
            if parsed.path == "/api/publications":
                return self.send_json({"id": save_publication(self.server.config, payload)}, HTTPStatus.CREATED)
            if parsed.path == "/api/metrics":
                return self.send_json({"id": save_metrics(self.server.config, payload)}, HTTPStatus.CREATED)
            if parsed.path == "/api/review":
                return self.send_json(save_review(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/skip":
                return self.send_json(skip_candidate(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/keywords":
                return self.send_json(save_keyword_group(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/settings":
                return self.send_json(save_system_settings(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/sessions":
                if payload.get("delete"):
                    return self.send_json(
                        delete_session(
                            self.server.config,
                            str(payload.get("platform") or ""),
                            str(payload.get("account") or ""),
                        ),
                        HTTPStatus.OK,
                    )
                if payload.get("check"):
                    return self.send_json(
                        check_session(
                            self.server.config,
                            str(payload.get("platform") or ""),
                            str(payload.get("account") or ""),
                        ),
                        HTTPStatus.OK,
                    )
                return self.send_json(save_session(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/events":
                if not self.authorized_for_events():
                    return self.send_json(
                        {"error": "missing or invalid bearer token (set JAGUARTV_EVENTS_TOKEN)"},
                        HTTPStatus.UNAUTHORIZED,
                    )
                return self.send_json(save_events(self.server.config, payload), HTTPStatus.CREATED)
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def authorized_for_events(self) -> bool:
        """JaguarTV postbacks must present the shared bearer token.

        The token comes from the JAGUARTV_EVENTS_TOKEN environment variable.
        If it is unset, only loopback clients are accepted (local testing).
        """
        token = os.environ.get("JAGUARTV_EVENTS_TOKEN", "").strip()
        if not token:
            return self.client_address[0] in {"127.0.0.1", "::1"}
        header = self.headers.get("Authorization", "")
        return header.removeprefix("Bearer ").strip() == token

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, requested: str) -> None:
        relative = "index.html" if requested in {"", "/"} else requested.lstrip("/")
        path = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in path.parents or not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_file(path, cache="no-cache")

    def send_media(self, relative: str) -> None:
        root = (workspace_dir(self.server.config) / "ready_for_review").resolve()
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_file(path, cache="private, max-age=60")

    def send_file(self, path: Path, cache: str) -> None:
        size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        partial = False
        if range_header and range_header.startswith("bytes="):
            requested = range_header.removeprefix("bytes=").split(",", 1)[0]
            start_text, end_text = requested.split("-", 1)
            start = int(start_text) if start_text else 0
            end = min(size - 1, int(end_text)) if end_text else size - 1
            if start > end or start >= size:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            partial = True
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining and (chunk := handle.read(min(256 * 1024, remaining))):
                self.wfile.write(chunk)
                remaining -= len(chunk)


def serve_dashboard(config: dict[str, Any], host: str = "127.0.0.1", port: int = 8787) -> None:
    register_coordinator(config, port)
    server = DashboardApplication((host, port), config)
    print(f"JaguarTV Content OS: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
