from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import yaml

from .core import (
    append_event,
    category_active_today,
    candidate_has_review_outputs,
    connect_db,
    discover,
    download_top,
    generate_review_index,
    inspect_url,
    ingest_uploaded_media,
    inventory_root,
    list_candidates,
    media_dimensions,
    now_iso,
    produce_top,
    resolve_config_path,
    tracking_links,
    workspace_dir,
)
from .sessions import check_session, delete_session, list_sessions, save_session
from .server_store import (
    complete_chunked_upload,
    find_upload,
    init_chunked_upload,
    list_uploads,
    public_url,
    save_upload,
    save_upload_chunk,
    storage_root,
)
from .source_outro import review_source_outro_summary


WEB_ROOT = Path(__file__).resolve().parent / "web"
BRAND_ASSET_ROOT = Path(__file__).resolve().parents[2] / "assets" / "brand"
PLATFORMS = ("youtube", "facebook", "tiktok", "kwai")
PUBLISH_TARGETS = (*PLATFORMS, "instagram", "other")
EVENT_TYPES = ("landing_click", "download_started", "install", "registration", "first_watch")
REVIEW_DECISIONS = ("APPROVED", "REVISION_REQUIRED")
PART_PACKAGE_PATTERN = re.compile(r"^(?P<parent>.+)_part(?P<number>\d+)$")
PUBLIC_UPLOAD_KINDS = {"design_image"}
INITIAL_CATEGORY_RULES = (
    ("足球类", (
        "futebol", "football", "soccer", "libertadores", "brasileirão", "brasileirao",
        "copa do brasil", "palmeiras", "flamengo", "cruzeiro", "corinthians", "botafogo",
        "são paulo", "sao paulo", "cerro porteño", "cerro porteno", "gols", "melhores momentos",
        "巴甲", "足球", "解放者杯", "南美杯", "巴西杯", "帕尔梅拉斯", "弗拉门戈",
    )),
    ("新闻类", (
        "notícia", "noticias", "notícias", "news", "g1", "cnn brasil", "eleições",
        "eleicoes", "presidente", "tse", "dólar", "dolar", "inflação", "inflacao",
        "previsão do tempo", "previsao do tempo", "tarifa", "congresso", "lula",
        "新闻", "大选", "总统", "通胀", "天气", "汇率",
    )),
    ("音乐类", (
        "música", "musica", "music", "funk", "sertanejo", "anitta", "ludmilla",
        "brega", "mpb", "spotify", "festival de música", "festival de musica", "show",
        "viral song", "歌曲", "音乐", "放克", "乡村音乐", "演唱会", "音乐节",
    )),
    ("肥皂剧", (
        "novela", "telenovela", "globoplay", "globo", "resumo da novela", "spoiler",
        "tela quente", "电视剧", "肥皂剧", "环球台", "剧情",
    )),
    ("少儿剧", (
        "infantil", "criança", "crianca", "kids", "children", "desenho", "cartoon",
        "animação", "animacao", "nursery", "儿童", "少儿", "动画", "卡通", "亲子",
    )),
    ("成人频道", ("adulto", "adult", "canal adulto", "18+", "nsfw", "sensual", "成人")),
    ("纪录片", (
        "documentário", "documentario", "documentary", "comida", "culinária", "culinaria",
        "gastronomia", "animal", "animais", "natureza", "desenvolvimento", "região",
        "regiao", "história", "historia", "纪录片", "美食", "动物", "自然", "地区发展",
    )),
    ("综艺", (
        "programa", "reality", "variedades", "show de tv", "entretenimento", "humor",
        "comédia", "comedia", "综艺", "娱乐", "真人秀", "喜剧",
    )),
    ("社交挑战", (
        "desafio", "challenge", "tiktok brasil", "#fyp", "para você", "para voce",
        "paravoce", "#viral", "reels", "meme", "trend", "tendência", "tendencia",
        "挑战", "热门挑战", "社交", "梗图", "爆款", "病毒",
    )),
    ("舞蹈", ("dança", "danca", "dance", "coreografia", "choreography", "passinho", "舞蹈", "跳舞")),
)
INITIAL_CATEGORY_LABELS = [label for label, _ in INITIAL_CATEGORY_RULES]
SOURCE_MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
PRODUCTION_RUNNING_STATUS = "PRODUCTION_RUNNING"


def public_brand_asset_path(requested: str) -> Path | None:
    relative = unquote(requested).lstrip("/")
    prefix = "assets/brand/"
    if not relative.startswith(prefix):
        return None
    root = BRAND_ASSET_ROOT.resolve()
    path = (root / relative.removeprefix(prefix)).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def upload_kind_requires_token(kind: str) -> bool:
    return False


def initial_category_for_text(*values: Any) -> str:
    chunks = [str(value or "").lower() for value in values if str(value or "").strip()]
    for label, needles in INITIAL_CATEGORY_RULES:
        if chunks and any(needle in chunks[0] for needle in needles):
            return label
    haystack = " ".join(chunks[1:] if len(chunks) > 1 else chunks)
    for label, needles in INITIAL_CATEGORY_RULES:
        if any(needle in haystack for needle in needles):
            return label
    return "未分类"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def int_value(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default



def signed_upload_url(upload_id: str, lifetime_sec: int = 24 * 3600) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", str(upload_id or "")):
        return ""
    return f"/api/uploads/{upload_id}/download"


def upload_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in list_uploads(config):
        row = dict(item)
        row["path"] = ""
        row["download_url"] = signed_upload_url(str(item.get("id") or ""))
        rows.append(row)
    return rows


def candidate_source_media(config: dict[str, Any], candidate_id: str) -> Path | None:
    candidate_id = unquote(candidate_id).strip()
    if not candidate_id:
        return None
    connection = connect_db(config)
    if not connection.execute("SELECT 1 FROM candidates WHERE id=?", (candidate_id,)).fetchone():
        return None
    work = workspace_dir(config) / "jobs" / candidate_id
    return next(
        (
            path for path in work.glob("source.*")
            if path.is_file() and path.suffix.lower() in SOURCE_MEDIA_SUFFIXES
        ),
        None,
    )


def production_recovery_status(config: dict[str, Any], candidate_id: str) -> str:
    if candidate_has_review_outputs(config, candidate_id):
        return "READY_FOR_REVIEW"
    if candidate_source_media(config, candidate_id):
        return "DOWNLOADED"
    return "PRODUCTION_FAILED"


def recover_interrupted_productions(config: dict[str, Any]) -> int:
    connection = connect_db(config)
    rows = connection.execute(
        "SELECT id FROM candidates WHERE status=?", (PRODUCTION_RUNNING_STATUS,)
    ).fetchall()
    recovered = 0
    for row in rows:
        candidate_id = str(row["id"])
        status = production_recovery_status(config, candidate_id)
        timestamp = now_iso()
        payload = {
            "recovered_from": PRODUCTION_RUNNING_STATUS,
            "status": status,
            "reason": "dashboard service restarted before production task finished",
        }
        connection.execute(
            "UPDATE candidates SET status=?,updated_at=? WHERE id=?",
            (status, timestamp, candidate_id),
        )
        append_event(connection, candidate_id, "PRODUCTION_RECOVERED", payload)
        recovered += 1
    connection.commit()
    return recovered


def review_output_asset_by_id(config: dict[str, Any], asset_id: str) -> dict[str, Any] | None:
    asset_id = unquote(asset_id).strip()
    if not asset_id:
        return None
    for assets in review_output_index(config).values():
        for asset in assets:
            if str(asset.get("id") or "") == asset_id:
                return asset
    return None


def candidate_design_info(config: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    requested_asset = ""
    if "::asset::" in candidate_id:
        candidate_id, requested_asset = candidate_id.split("::asset::", 1)
    output_asset = review_output_asset_by_id(config, requested_asset) if requested_asset else None
    output_path = Path(str((output_asset or {}).get("_path") or ""))
    if output_path.is_file():
        output_width, output_height = media_dimensions(output_path)
        return {
            "candidate_id": candidate_id,
            "source_preview_url": str((output_asset or {}).get("video_url") or ""),
            "design_canvas_width": int(output_width),
            "design_canvas_height": int(output_height),
            "source_fit": "contain",
            "design_base_asset_id": str((output_asset or {}).get("id") or ""),
            "design_base_variant": str((output_asset or {}).get("variant") or ""),
        }
    source_media = candidate_source_media(config, candidate_id)
    result = {
        "candidate_id": candidate_id,
        "source_preview_url": "",
        "design_canvas_width": 1080,
        "design_canvas_height": 1920,
        "source_fit": "contain",
        "design_base_asset_id": "",
        "design_base_variant": "",
    }
    if source_media is None:
        return result
    source_width, source_height = media_dimensions(source_media)
    result.update({
        "source_preview_url": f"/api/candidates/{quote(candidate_id, safe='')}/source?v={int(source_media.stat().st_mtime)}",
        "design_canvas_width": int(source_width),
        "design_canvas_height": int(source_height),
        "source_fit": "contain",
    })
    return result


def system_health(config: dict[str, Any]) -> dict[str, Any]:
    disk = shutil.disk_usage(storage_root(config))
    runtimes = {}
    for name in ("ffmpeg", "ffprobe", "yt-dlp", "deno", "node", "tesseract"):
        path = shutil.which(name)
        runtimes[name] = {"ok": bool(path), "path": path or ""}
    node_major = 0
    if runtimes["node"]["ok"]:
        result = subprocess.run([str(runtimes["node"]["path"]), "--version"], text=True, capture_output=True, check=False)
        try:
            node_major = int((result.stdout or "").strip().lstrip("v").split(".", 1)[0])
        except ValueError:
            node_major = 0
    runtime = "deno" if runtimes["deno"]["ok"] else "node" if node_major >= 22 else ""
    runtimes["node"]["version_major"] = node_major
    sessions = list_sessions(config)
    ready_sessions = sorted({str(item.get("platform") or "") for item in sessions if item.get("status") == "READY"})
    return {
        "status": "ok",
        "generated_at": now_iso(),
        "storage": {"free_bytes": disk.free, "total_bytes": disk.total},
        "runtimes": runtimes,
        "youtube_runtime": runtime,
        "ready_sessions": ready_sessions,
        "upload": {
            "enabled": True,
            "chunk_bytes": int((config.get("storage", {}) or {}).get("upload_chunk_bytes", 8 * 1024 * 1024)),
            "max_bytes": int((config.get("storage", {}) or {}).get("max_upload_bytes", 2 * 1024 * 1024 * 1024)),
        },
    }



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
    orphan_reviews = server_review_rows(config, exclude={
        row["id"] for row in connection.execute("SELECT id FROM candidates")
    })
    orphan_status_counts: dict[str, int] = {}
    for item in orphan_reviews:
        orphan_status_counts[item["status"]] = orphan_status_counts.get(item["status"], 0) + 1
    total += len(orphan_reviews)
    ready += orphan_status_counts.get("READY_FOR_REVIEW", 0)
    approved += orphan_status_counts.get("APPROVED", 0)
    merged_status_counts = dict(status_counts)
    for key, value in orphan_status_counts.items():
        merged_status_counts[key] = merged_status_counts.get(key, 0) + value
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
        "candidate_statuses": merged_status_counts,
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


def review_output_index(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    workspace_root = workspace_dir(config) / "ready_for_review"
    server_root = storage_root(config) / "review"
    package_ids: set[str] = set()
    for root in (workspace_root, server_root):
        if root.exists():
            package_ids.update(
                path.name for path in root.iterdir()
                if path.is_dir() and any(child.is_file() and child.suffix.lower() == ".mp4" for child in path.iterdir())
            )

    index: dict[str, list[dict[str, Any]]] = {}
    for package_id in sorted(package_ids):
        local_dir = workspace_root / package_id
        server_dir = server_root / package_id
        local_video = local_dir / "video.mp4"
        server_video = server_dir / "video.mp4"
        local_first_video = next((path for path in sorted(local_dir.glob("*.mp4")) if path.is_file()), None)
        server_first_video = next((path for path in sorted(server_dir.glob("*.mp4")) if path.is_file()), None)
        if local_video.is_file():
            media_dir = local_dir
            media_relative = f"{package_id}/video.mp4"
        elif server_video.is_file():
            media_dir = server_dir
            media_relative = f"review/{package_id}/video.mp4"
        elif local_first_video is not None:
            media_dir = local_dir
            media_relative = f"{package_id}/{local_first_video.name}"
        elif server_first_video is not None:
            media_dir = server_dir
            media_relative = f"review/{package_id}/{server_first_video.name}"
        else:
            continue

        metadata_path = local_dir / "metadata.json"
        if not metadata_path.is_file():
            metadata_path = server_dir / "metadata.json"
        server_files: dict[str, Any] = {}
        if metadata_path.is_file():
            try:
                review_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                server_files = review_metadata.get("server_storage", {}).get("files", {}) or {}
            except (json.JSONDecodeError, OSError):
                pass

        cover = media_dir / "cover.jpg"
        cover_url = ""
        if cover.is_file():
            cover_relative = media_relative.rsplit("/", 1)[0] + "/cover.jpg"
            cover_url = f"/media/{quote(cover_relative, safe='/')}?v={int(cover.stat().st_mtime)}"

        part_match = PART_PACKAGE_PATTERN.match(package_id)
        part_number = int(part_match.group("number")) if part_match else None
        variant_files = sorted(path for path in media_dir.glob("*.mp4") if path.name != "video.mp4")
        mp4_files = variant_files or [media_dir / "video.mp4"]
        for video in [path for path in mp4_files if path.is_file()]:
            version = int(video.stat().st_mtime)
            video_relative = media_relative.rsplit("/", 1)[0] + f"/{video.name}"
            video_url = f"/media/{quote(video_relative, safe='/')}?v={version}"
            server_url = str(server_files.get(video.name, {}).get("url") or "")
            if not server_url and server_video.is_file():
                server_url = public_url(config, f"review/{package_id}/{video.name}")
            variant = "通用版" if "通用版" in video.name or video.name == "video.mp4" else ("FB版" if "FB版" in video.name else "")
            base_label = f"片段 {part_number:02d}" if part_number is not None else "成片"
            asset = {
                "id": package_id if video.name == "video.mp4" else f"{package_id}:{video.stem}",
                "label": f"{base_label} · {variant}" if variant else base_label,
                "variant": variant,
                "part_number": part_number,
                "video_url": video_url,
                "download_url": f"{video_url}&download=1",
                "server_url": server_url,
                "cover_url": cover_url,
                "filename": f"{package_id}.mp4" if video.name == "video.mp4" else video.name,
                "_path": str(video),
                "_metadata_path": str(metadata_path) if metadata_path.is_file() else "",
            }
            index.setdefault(package_id, []).append(asset)
            if part_match:
                index.setdefault(part_match.group("parent"), []).append(asset)

    for assets in index.values():
        unique = {asset["id"]: asset for asset in assets}
        assets[:] = sorted(
            unique.values(),
            key=lambda asset: (
                asset["part_number"] is not None,
                asset["part_number"] if asset["part_number"] is not None else 0,
                0 if asset.get("variant") == "通用版" else 1 if asset.get("variant") == "FB版" else 2,
                asset["id"],
            ),
        )
    return index


def public_output_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in asset.items() if not key.startswith("_")}


def download_claim_rows(config: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
    connection = connect_db(config)
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT dc.*,c.title
            FROM download_claims dc
            LEFT JOIN candidates c ON c.id=dc.candidate_id
            ORDER BY dc.downloaded_at DESC,id DESC LIMIT ?
            """,
            (limit,),
        )
    ]


def save_download_claim(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    candidate = str(payload.get("candidate_id") or "").split(":", 1)[0].strip()
    asset_id = str(payload.get("asset_id") or candidate).strip()
    filename = Path(str(payload.get("filename") or "")).name
    variant = str(payload.get("variant") or "").strip()
    publisher = str(payload.get("publisher") or "").strip()
    publish_platform = str(payload.get("publish_platform") or "").strip().lower()
    if not candidate or not publisher:
        raise ValueError("candidate_id and publisher are required before downloading")
    if publish_platform and publish_platform not in PUBLISH_TARGETS:
        raise ValueError(f"publish_platform must be one of {PUBLISH_TARGETS}")
    connection = connect_db(config)
    row = connection.execute("SELECT id FROM candidates WHERE id=?", (candidate,)).fetchone()
    server_package = storage_root(config) / "review" / candidate
    local_package = workspace_dir(config) / "ready_for_review" / candidate
    if not row and not server_package.exists() and not local_package.exists():
        raise ValueError("candidate does not exist")
    timestamp = now_iso()
    cursor = connection.execute(
        """
        INSERT INTO download_claims(
          candidate_id,asset_id,filename,variant,publisher,publish_platform,note,downloaded_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            candidate,
            asset_id,
            filename,
            variant,
            publisher,
            publish_platform,
            str(payload.get("note") or "").strip(),
            timestamp,
        ),
    )
    connection.commit()
    return {"id": int(cursor.lastrowid), "candidate_id": candidate, "downloaded_at": timestamp}


def update_download_claim_metrics(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    claim_id = int_value(payload.get("claim_id"))
    if not claim_id:
        raise ValueError("claim_id is required")
    metrics = {field: int_value(payload.get(field)) for field in ("views", "clicks", "registrations")}
    extra = payload.get("extra_data") or {}
    if not isinstance(extra, dict):
        raise ValueError("extra_data must be an object")
    connection = connect_db(config)
    row = connection.execute("SELECT candidate_id,publish_platform FROM download_claims WHERE id=?", (claim_id,)).fetchone()
    if not row:
        raise ValueError("download claim does not exist")
    timestamp = now_iso()
    connection.execute(
        """
        UPDATE download_claims
        SET views=?,clicks=?,registrations=?,extra_data=?,metrics_updated_at=?
        WHERE id=?
        """,
        (
            metrics["views"], metrics["clicks"], metrics["registrations"],
            json.dumps(extra, ensure_ascii=False), timestamp, claim_id,
        ),
    )
    if row["publish_platform"] in PLATFORMS:
        connection.execute(
            """
            INSERT INTO performance_snapshots(
              candidate_id,platform,captured_at,views,clicks,registrations
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                row["candidate_id"], row["publish_platform"], timestamp,
                metrics["views"], metrics["clicks"], metrics["registrations"],
            ),
        )
    connection.commit()
    return {"id": claim_id, "updated_at": timestamp}


def safe_remove_tree(path: Path, allowed_roots: list[Path]) -> int:
    try:
        resolved = path.resolve()
    except OSError:
        return 0
    allowed = [root.resolve() for root in allowed_roots]
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise ValueError(f"refusing to delete outside managed storage: {path}")
    if resolved.is_dir():
        size = sum(child.stat().st_size for child in resolved.rglob("*") if child.is_file())
        shutil.rmtree(resolved)
        return size
    if resolved.is_file():
        size = resolved.stat().st_size
        resolved.unlink()
        return size
    return 0


def candidate_package_ids(candidate: str) -> set[str]:
    return {candidate}


def collect_package_ids_for_candidate(config: dict[str, Any], candidate: str) -> set[str]:
    ids = candidate_package_ids(candidate)
    roots = [workspace_dir(config) / "ready_for_review", storage_root(config) / "review"]
    for root in roots:
        if not root.exists():
            continue
        for path in root.iterdir():
            if path.is_dir() and (path.name == candidate or path.name.startswith(f"{candidate}_part")):
                ids.add(path.name)
    return ids


def inventory_paths_from_package(package_dir: Path) -> list[Path]:
    metadata_path = package_dir / "metadata.json"
    if not metadata_path.is_file():
        return []
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    paths: list[Path] = []
    for variant in metadata.get("output_variants") or []:
        if isinstance(variant, dict) and variant.get("inventory_path"):
            paths.append(Path(str(variant["inventory_path"])).expanduser())
    return paths


def delete_candidates(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    raw_ids = payload.get("candidate_ids")
    if raw_ids is None:
        raw_ids = [payload.get("candidate_id") or payload.get("id")]
    if not isinstance(raw_ids, list):
        raise ValueError("candidate_ids must be a list")
    candidate_ids = [
        str(candidate).split(":", 1)[0].strip()
        for candidate in raw_ids
        if str(candidate or "").strip()
    ]
    candidate_ids = list(dict.fromkeys(candidate_ids))
    if not candidate_ids:
        raise ValueError("select at least one candidate")

    workspace = workspace_dir(config)
    storage = storage_root(config)
    inventory = inventory_root(config)
    managed_roots = [
        workspace / "jobs",
        workspace / "ready_for_review",
        storage / "review",
        inventory,
    ]
    deleted_bytes = 0
    deleted_items: list[dict[str, Any]] = []
    connection = connect_db(config)
    for candidate in candidate_ids:
        package_ids = collect_package_ids_for_candidate(config, candidate)
        inventory_paths: list[Path] = []
        for package_id in package_ids:
            for root in (workspace / "ready_for_review", storage / "review"):
                package_dir = root / package_id
                if package_dir.exists():
                    inventory_paths.extend(inventory_paths_from_package(package_dir))
        removed_paths: list[str] = []
        for inventory_path in inventory_paths:
            if inventory_path.exists():
                deleted_bytes += safe_remove_tree(inventory_path, managed_roots)
                removed_paths.append(str(inventory_path))
                parent = inventory_path.parent.resolve()
                inventory_boundary = inventory.resolve()
                while parent != parent.parent and parent != inventory_boundary:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        for package_id in package_ids:
            for path in (workspace / "ready_for_review" / package_id, storage / "review" / package_id):
                if path.exists():
                    deleted_bytes += safe_remove_tree(path, managed_roots)
                    removed_paths.append(str(path))
        job_dir = workspace / "jobs" / candidate
        if job_dir.exists():
            deleted_bytes += safe_remove_tree(job_dir, managed_roots)
            removed_paths.append(str(job_dir))
        for table in (
            "events",
            "publications",
            "performance_snapshots",
            "conversion_events",
            "feedback_actions",
            "render_jobs",
        ):
            connection.execute(f"DELETE FROM {table} WHERE candidate_id=?", (candidate,))
        cursor = connection.execute("DELETE FROM candidates WHERE id=?", (candidate,))
        deleted_items.append({
            "candidate_id": candidate,
            "removed_from_db": bool(cursor.rowcount),
            "package_ids": sorted(package_ids),
            "paths": removed_paths,
        })
    connection.commit()
    return {
        "deleted": len(deleted_items),
        "bytes_freed": deleted_bytes,
        "items": deleted_items,
    }


def source_keyword_metadata(connection: Any, candidate_id: str) -> dict[str, str]:
    if not candidate_id:
        return {}
    source_row = connection.execute(
        "SELECT metadata_json FROM candidates WHERE id=?", (candidate_id,)
    ).fetchone()
    if not source_row:
        return {}
    try:
        source_metadata = json.loads(source_row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        return {}
    return {
        "keyword": str(source_metadata.get("keyword") or ""),
        "category": str(source_metadata.get("category") or ""),
    }


def candidate_rows(config: dict[str, Any], status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    rows = list_candidates(config, status, limit)
    result = []
    connection = connect_db(config)
    outputs_by_candidate = review_output_index(config)
    failures = {
        row["candidate_id"]: dict(row)
        for row in connection.execute(
            """
            SELECT e.candidate_id,e.event_type,e.payload_json,e.created_at
            FROM events e
            INNER JOIN (
              SELECT candidate_id,MAX(id) id FROM events
              WHERE event_type IN ('DOWNLOAD_FAILED','PRODUCTION_FAILED','QA_FAILED','BLOCKED_RIGHTS')
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
        parent_metadata = source_keyword_metadata(connection, str(item.get("parent_id") or ""))
        source_keyword = str(metadata.get("keyword") or parent_metadata.get("keyword") or "")
        source_category = str(metadata.get("category") or parent_metadata.get("category") or "")
        item.pop("metadata_json", None)
        item["published_flag"] = bool(item.get("published_flag"))
        item["keyword"] = source_keyword
        item["initial_category"] = initial_category_for_text(
            source_keyword,
            source_category,
            item.get("title"),
            item.get("description"),
        )
        item["initial_keyword"] = source_keyword or source_category
        item["score_breakdown"] = metadata.get("score_breakdown") or {}
        analysis = metadata.get("analysis") or {}
        strategy = analysis.get("strategy") or {}
        item["content_type"] = str(strategy.get("content_type") or "unknown")
        item["segment_strategy"] = str(strategy.get("segment_strategy") or "")
        item["audio_policy"] = str(strategy.get("audio_policy") or "")
        segments = analysis.get("segments") or []
        item["highlight_score"] = max((float(segment.get("highlight_score") or 0) for segment in segments), default=0.0)
        thumbnail = metadata.get("thumbnail") or ""
        if not thumbnail and isinstance(metadata.get("thumbnails"), list) and metadata["thumbnails"]:
            last = metadata["thumbnails"][-1]
            thumbnail = last.get("url", "") if isinstance(last, dict) else ""
        item["thumbnail_url"] = str(thumbnail)
        outputs = outputs_by_candidate.get(str(item["id"]), [])
        primary_output = outputs[0] if outputs else {}
        item["output_assets"] = [public_output_asset(asset) for asset in outputs]
        item["output_count"] = len(outputs)
        item["display_title"] = (
            str(primary_output.get("filename") or "").removesuffix(".mp4")
            if primary_output else str(item.get("title") or "")
        )
        item["cover_url"] = str(primary_output.get("cover_url") or "")
        item["video_url"] = str(primary_output.get("video_url") or "")
        item["download_url"] = str(primary_output.get("download_url") or "")
        item["server_url"] = str(primary_output.get("server_url") or "")
        metadata_path = Path(str(primary_output.get("_metadata_path") or ""))
        if metadata_path.is_file():
            try:
                review_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                item["server_url"] = str(
                    review_metadata.get("server_storage", {}).get("files", {}).get("video.mp4", {}).get("url") or ""
                )
                item["content_type"] = str(review_metadata.get("content_type") or item["content_type"])
                item["segment_strategy"] = str(review_metadata.get("segment_strategy") or item["segment_strategy"])
                item["audio_policy"] = str(review_metadata.get("audio_policy") or item["audio_policy"])
                item["batch_label"] = str(review_metadata.get("batch_label") or "")
                item["highlight_score"] = float(
                    review_metadata.get("segment", {}).get("highlight_score") or item["highlight_score"]
                )
                item["source_outro_trim"] = review_source_outro_summary(review_metadata.get("source_outro_trim"))
            except (json.JSONDecodeError, OSError):
                pass
        failure = failures.get(item["id"])
        item["failure_event"] = ""
        item["failure_detail"] = ""
        item["failure_at"] = ""
        if failure and item["status"] in {"DOWNLOAD_FAILED", "PRODUCTION_FAILED", "QA_FAILED", "BLOCKED_RIGHTS"}:
            try:
                failure_payload = json.loads(failure["payload_json"] or "{}")
            except json.JSONDecodeError:
                failure_payload = {}
            detail = str(failure_payload.get("error") or failure_payload.get("stderr") or "").strip()
            item["failure_event"] = failure["event_type"]
            item["failure_detail"] = detail[-4000:]
            item["failure_at"] = failure["created_at"]
        result.append(item)
    existing = {str(item.get("id") or "") for item in result}
    if status in {None, "", "READY_FOR_REVIEW", "APPROVED", "REVISION_REQUIRED"}:
        for item in server_review_rows(config, exclude=existing):
            if status and item["status"] != status:
                continue
            result.append(item)
            if len(result) >= limit:
                break
    return result


def server_review_rows(config: dict[str, Any], exclude: set[str] | None = None) -> list[dict[str, Any]]:
    exclude = exclude or set()
    review_root = storage_root(config) / "review"
    if not review_root.exists():
        return []
    connection = connect_db(config)
    outputs_by_candidate = review_output_index(config)
    items: list[dict[str, Any]] = []
    for metadata_path in sorted(review_root.glob("*/metadata.json"), key=lambda path: path.stat().st_mtime, reverse=True):
        package_dir = metadata_path.parent
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        candidate = str(metadata.get("job_id") or package_dir.name)
        if candidate in exclude:
            continue
        review_state = "READY_FOR_REVIEW"
        review_file = package_dir / "review.json"
        if review_file.exists():
            try:
                decision = str(json.loads(review_file.read_text(encoding="utf-8")).get("decision") or "").lower()
                if decision == "approved":
                    review_state = "APPROVED"
                elif decision in {"revision_required", "revision", "rejected"}:
                    review_state = "REVISION_REQUIRED"
            except (json.JSONDecodeError, OSError):
                pass
        source = metadata.get("source") or {}
        segment = metadata.get("segment") or {}
        strategy = {
            "content_type": metadata.get("content_type") or "unknown",
            "segment_strategy": metadata.get("segment_strategy") or "",
            "audio_policy": metadata.get("audio_policy") or "",
        }
        media_prefix = f"review/{package_dir.name}"
        video = package_dir / "video.mp4"
        cover = package_dir / "cover.jpg"
        outputs = outputs_by_candidate.get(package_dir.name, [])
        primary_output = outputs[0] if outputs else {}
        updated_at = datetime.fromtimestamp(metadata_path.stat().st_mtime, tz=timezone.utc).isoformat()
        source_keywords = source_keyword_metadata(connection, str(metadata.get("source_job_id") or ""))
        source_keyword = str(metadata.get("keyword") or source_keywords.get("keyword") or "")
        source_category = str(metadata.get("category") or source_keywords.get("category") or "")
        items.append({
            "id": candidate,
            "platform": str(source.get("platform") or "server"),
            "source_id": str(metadata.get("source_job_id") or ""),
            "url": str(source.get("url") or ""),
            "title": str(source.get("title") or candidate),
            "display_title": str(primary_output.get("filename") or candidate).removesuffix(".mp4"),
            "description": "",
            "duration": float(segment.get("duration_sec") or 0),
            "view_count": 0,
            "detected_language": "",
            "score": 0,
            "status": review_state,
            "created_at": updated_at,
            "updated_at": updated_at,
            "keyword": source_keyword,
            "initial_category": initial_category_for_text(
                source_keyword,
                source_category,
                source.get("title"),
                source.get("platform"),
            ),
            "initial_keyword": source_keyword or source_category,
            "score_breakdown": {},
            "batch_label": str(metadata.get("batch_label") or ""),
            "content_type": str(strategy["content_type"]),
            "segment_strategy": str(strategy["segment_strategy"]),
            "audio_policy": str(strategy["audio_policy"]),
            "highlight_score": float(segment.get("highlight_score") or 0),
            "thumbnail_url": "",
            "cover_url": str(primary_output.get("cover_url") or (
                f"/media/{media_prefix}/cover.jpg?v={int(cover.stat().st_mtime)}" if cover.exists() else ""
            )),
            "video_url": str(primary_output.get("video_url") or (
                f"/media/{media_prefix}/video.mp4?v={int(video.stat().st_mtime)}" if video.exists() else ""
            )),
            "download_url": str(primary_output.get("download_url") or ""),
            "server_url": str(primary_output.get("server_url") or (
                public_url(config, f"{media_prefix}/video.mp4") if video.exists() else ""
            )),
            "output_assets": [public_output_asset(asset) for asset in outputs],
            "output_count": len(outputs),
            "failure_event": "",
            "failure_detail": "",
            "failure_at": "",
            "published_flag": False,
            "source_outro_trim": review_source_outro_summary(metadata.get("source_outro_trim")),
        })
    return items


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


def render_job_rows(config: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    connection = connect_db(config)
    rows = []
    for row in connection.execute(
        """
        SELECT render_jobs.*,candidates.title
        FROM render_jobs
        LEFT JOIN candidates ON candidates.id=render_jobs.candidate_id
        ORDER BY render_jobs.updated_at DESC
        LIMIT ?
        """,
        (max(1, min(500, int(limit))),),
    ):
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json", "{}") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        rows.append(item)
    return rows


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


def update_publication_status(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    publication_id = int_value(payload.get("publication_id"))
    status = str(payload.get("status") or "").strip().upper()
    if not publication_id or status not in {"QUEUED", "SCHEDULED", "PUBLISHED", "FAILED"}:
        raise ValueError("publication_id and a supported status are required")
    connection = connect_db(config)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT candidate_id FROM publications WHERE id=?", (publication_id,)
        ).fetchone()
        if not row:
            raise ValueError("publication does not exist")
        timestamp = now_iso()
        connection.execute(
            "UPDATE publications SET status=?,published_at=?,updated_at=? WHERE id=?",
            (status, timestamp if status == "PUBLISHED" else None, timestamp, publication_id),
        )
        if status == "PUBLISHED":
            connection.execute(
                "UPDATE candidates SET published_flag=1,updated_at=? WHERE id=?",
                (timestamp, row["candidate_id"]),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"publication_id": publication_id, "candidate_id": row["candidate_id"], "status": status}


def save_callback(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    candidate = str(payload.get("candidate_id") or "").strip()
    publisher = str(payload.get("publisher") or "").strip()
    platform = str(payload.get("platform") or "").strip().lower()
    if not candidate or not publisher:
        raise ValueError("candidate_id and publisher are required")
    if platform not in PLATFORMS:
        raise ValueError(f"platform must be one of {PLATFORMS}")
    metrics: dict[str, int] = {}
    for field in ("views", "clicks", "registrations"):
        value = payload.get(field, 0)
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a non-negative integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field} must be a non-negative integer") from error
        if parsed < 0:
            raise ValueError(f"{field} must be a non-negative integer")
        metrics[field] = parsed
    callback_at = str(payload.get("timestamp") or now_iso()).strip()
    try:
        datetime.fromisoformat(callback_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO8601") from error
    extra = payload.get("extra_data") or {}
    if not isinstance(extra, dict):
        raise ValueError("extra_data must be an object")
    connection = connect_db(config)
    cursor = connection.execute(
        """
        INSERT INTO callback_logs(
          candidate_id,video_id,publisher,platform,views,clicks,registrations,extra_data,callback_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate, str(payload.get("video_id") or ""), publisher, platform,
            metrics["views"], metrics["clicks"], metrics["registrations"],
            json.dumps(extra, ensure_ascii=False), callback_at,
        ),
    )
    connection.commit()
    print(f"callback {callback_at} candidate={candidate} platform={platform}")
    return {"success": True, "log_id": int(cursor.lastrowid)}


def move_candidate_to_review(config: dict[str, Any], candidate: str) -> dict[str, Any]:
    candidate = candidate.strip()
    if not candidate:
        raise ValueError("candidate_id is required")
    local_root = workspace_dir(config) / "ready_for_review"
    server_root = storage_root(config) / "review"
    packages = [
        path
        for root in (local_root, server_root)
        if root.exists()
        for path in [root / candidate, *sorted(root.glob(f"{candidate}_part*"))]
        if path.is_dir()
        and any(item.is_file() and item.stat().st_size > 0 for item in path.glob("*.mp4"))
    ]
    if not packages:
        raise ValueError("candidate has no verified rendered output to move into review")
    connection = connect_db(config)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute("SELECT status FROM candidates WHERE id=?", (candidate,)).fetchone()
        if not row:
            raise ValueError("candidate does not exist")
        allowed = {"DOWNLOADED", "APPROVED", "REVISION_REQUIRED", "READY_FOR_REVIEW"}
        if row["status"] not in allowed:
            raise ValueError(f"candidate status {row['status']} cannot move to review")
        timestamp = now_iso()
        connection.execute(
            "UPDATE candidates SET status='READY_FOR_REVIEW',updated_at=? WHERE id=?",
            (timestamp, candidate),
        )
        connection.execute(
            "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (candidate, "READY_FOR_REVIEW", json.dumps({"packages": [p.name for p in packages]}), timestamp),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"candidate_id": candidate, "status": "READY_FOR_REVIEW", "packages": [p.name for p in packages]}


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
        review_file = storage_root(config) / "review" / candidate / "review.json"
        if not review_file.parent.exists():
            raise ValueError("candidate does not exist")
        timestamp = now_iso()
        review_file.write_text(
            json.dumps({
                "decision": decision.lower(),
                "note": str(payload.get("note") or ""),
                "reviewer": str(payload.get("reviewer") or ""),
                "reviewed_at": timestamp,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"candidate_id": candidate, "status": decision, "source": "server_review_package"}
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
        recovered = recover_interrupted_productions(config)
        if recovered:
            print(f"dashboard recovered {recovered} interrupted production candidate(s)")

    def start_action(self, payload: dict[str, Any]) -> str:
        candidate_ids = payload.get("candidate_ids") or []
        if not isinstance(candidate_ids, list):
            raise ValueError("candidate_ids must be a list")
        single = str(payload.get("candidate_id") or "").strip()
        candidate_ids = [str(value).strip() for value in candidate_ids if str(value).strip()]
        if single and single not in candidate_ids:
            candidate_ids.append(single)
        candidate_ids = list(dict.fromkeys(candidate_ids))
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

    def run_candidate_batch(
        self, task_id: str, action: str, candidate_ids: list[str], options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
                row = connect_db(self.config).execute(
                    "SELECT status FROM candidates WHERE id=?", (candidate,)
                ).fetchone()
                work = workspace_dir(self.config) / "jobs" / candidate
                source_missing = not any(
                    path.suffix.lower() in SOURCE_MEDIA_SUFFIXES
                    for path in work.glob("source.*")
                )
                if row and (row["status"] in {"DISCOVERED", "DOWNLOAD_FAILED"} or (row["status"] == "PRODUCTION_FAILED" and source_missing)):
                    self.update_task(task_id, message=f"先下载素材 · {index + 1}/{total}")
                    download_result = download_top(self.config, 1, candidate)
                    if int(download_result.get("failed", 0)) or int(download_result.get("downloaded", 0) == 0):
                        result = {"download": download_result, "produce": {"selected": 0, "produced": 0, "failed": 1}}
                        failed += 1
                        items.append({"candidate_id": candidate, "result": result, "failed": True})
                        self.update_task(task_id, completed=index + 1, progress=int((index + 1) / total * 95))
                        continue

                def production_progress(percent: int, message: str) -> None:
                    base = index / total * 95
                    share = 95 / total
                    self.update_task(
                        task_id, progress=int(base + percent / 100 * share),
                        message=f"{message} · {index + 1}/{total}",
                    )

                self.update_task(task_id, message=f"等待制作资源 · {index + 1}/{total}")
                with self.production_lock:
                    started = now_iso()
                    connection = connect_db(self.config)
                    connection.execute(
                        "UPDATE candidates SET status=?,updated_at=? WHERE id=?",
                        (PRODUCTION_RUNNING_STATUS, started, candidate),
                    )
                    append_event(connection, candidate, "PRODUCTION_STARTED", {"task_id": task_id})
                    connection.commit()
                    result = produce_top(
                        self.config, 1, candidate, progress_callback=production_progress, options=options
                    )
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
                keywords = payload.get("keywords") or []
                result = discover(
                    self.config,
                    platforms=[str(payload.get("platform") or "youtube")],
                    limit=int_value(payload.get("limit"), 3),
                    keyword_overrides=keywords if isinstance(keywords, list) else [],
                )
            elif action == "ingest":
                url = str(payload.get("url") or "").strip()
                if not url:
                    raise ValueError("url is required")
                self.update_task(task_id, progress=15, message="正在读取视频信息")
                candidate_id = inspect_url(
                    self.config,
                    url,
                    requested_platform=str(payload.get("platform") or ""),
                    allow_stub=True,
                )
                self.update_task(
                    task_id,
                    progress=45,
                    current_candidate=candidate_id,
                    candidate_ids=[candidate_id],
                    message="正在下载到服务器",
                )
                row = connect_db(self.config).execute(
                    "SELECT status FROM candidates WHERE id=?", (candidate_id,)
                ).fetchone()
                if row and row["status"] == "DOWNLOADED":
                    download_result = {"selected": 1, "downloaded": 1, "failed": 0, "already_downloaded": True}
                elif row and row["status"] == "TOO_LONG":
                    raise RuntimeError("URL 已导入，但视频超过 30 分钟，只能删除，不能进入待制作")
                else:
                    download_result = download_top(self.config, 1, candidate_id)
                if int(download_result.get("failed", 0)) or int(download_result.get("downloaded", 0) == 0):
                    failure = connect_db(self.config).execute(
                        """
                        SELECT payload_json FROM events
                        WHERE candidate_id=? AND event_type='DOWNLOAD_FAILED'
                        ORDER BY id DESC LIMIT 1
                        """,
                        (candidate_id,),
                    ).fetchone()
                    detail = ""
                    if failure:
                        try:
                            payload_json = json.loads(failure["payload_json"] or "{}")
                            detail = str(payload_json.get("stderr") or payload_json.get("error") or "").strip()
                        except json.JSONDecodeError:
                            detail = str(failure["payload_json"] or "").strip()
                    suffix = f"：{detail[-500:]}" if detail else ""
                    raise RuntimeError(f"URL 已导入，但服务器下载失败，请在下载失败列表重试或检查登录态{suffix}")
                result = {
                    "candidate_id": candidate_id,
                    "download": download_result,
                    "status": "DOWNLOADED",
                }
            elif action in {"download", "produce", "skip"}:
                result = self.run_candidate_batch(
                    task_id, action, payload.get("candidate_ids") or [], payload.get("options") or {}
                )
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
            if parsed.path == "/api/render-jobs":
                limit = int_value(query.get("limit", [100])[0], 100)
                return self.send_json(render_job_rows(self.server.config, limit))
            if parsed.path == "/api/feedback":
                return self.send_json(feedback_rows(self.server.config))
            if parsed.path == "/api/download-claims":
                limit = int_value(query.get("limit", [200])[0], 200)
                return self.send_json(download_claim_rows(self.server.config, limit))
            if parsed.path == "/api/keywords":
                return self.send_json(load_keyword_groups(self.server.config))
            if parsed.path == "/api/settings":
                return self.send_json(system_settings(self.server.config))
            if parsed.path == "/api/sessions":
                return self.send_json(list_sessions(self.server.config))
            if parsed.path == "/api/health":
                return self.send_json(system_health(self.server.config))
            if parsed.path == "/api/uploads":
                if not self.authorized_for_uploads():
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                return self.send_json(upload_rows(self.server.config))
            if parsed.path.startswith("/api/uploads/") and parsed.path.endswith("/link"):
                if not self.authorized_for_uploads():
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                upload_id = parsed.path.strip("/").split("/")[2]
                find_upload(self.server.config, upload_id)
                return self.send_json({"download_url": signed_upload_url(upload_id)})
            if parsed.path.startswith("/api/uploads/") and parsed.path.endswith("/download"):
                return self.send_private_upload(parsed, head_only=False)
            if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/design"):
                parts = parsed.path.strip("/").split("/")
                if len(parts) != 4:
                    return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return self.send_json(candidate_design_info(self.server.config, unquote(parts[2])))
            if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/source"):
                return self.send_candidate_source(parsed, head_only=False)
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
                download = str((query.get("download") or [""])[0]).lower() in {"1", "true", "yes"}
                return self.send_media(parsed.path.removeprefix("/media/"), download=download)
            return self.send_static(parsed.path)
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path.startswith("/api/uploads/") and parsed.path.endswith("/download"):
            return self.send_private_upload(parsed, head_only=True)
        if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/source"):
            return self.send_candidate_source(parsed, head_only=True)
        if parsed.path.startswith("/media/"):
            download = str((query.get("download") or [""])[0]).lower() in {"1", "true", "yes"}
            return self.send_media(parsed.path.removeprefix("/media/"), download=download)
        return self.send_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/uploads/init":
                payload = self.read_json()
                kind = str(payload.get("kind") or "").lower()
                if not self.authorized_for_upload_kind(kind):
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                result = init_chunked_upload(
                    self.server.config,
                    filename=str(payload.get("filename") or ""),
                    kind=kind,
                    content_length=int(payload.get("size") or 0),
                )
                return self.send_json(result, HTTPStatus.CREATED)
            if parsed.path == "/api/uploads/chunk":
                query = parse_qs(parsed.query)
                upload_id = str((query.get("upload_id") or [""])[0])
                if not self.authorized_for_upload_kind(self.pending_upload_kind(upload_id)):
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                index = int((query.get("index") or ["-1"])[0])
                length = int(self.headers.get("Content-Length") or 0)
                result = save_upload_chunk(self.server.config, upload_id, index, self.rfile, length)
                return self.send_json(result, HTTPStatus.CREATED)
            if parsed.path == "/api/uploads/complete":
                payload = self.read_json()
                upload_id = str(payload.get("upload_id") or "")
                if not self.authorized_for_upload_kind(self.pending_upload_kind(upload_id)):
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                result = complete_chunked_upload(self.server.config, upload_id)
                if result["kind"] == "source":
                    result["candidate_id"] = ingest_uploaded_media(self.server.config, result)
                result["download_url"] = signed_upload_url(result["id"])
                return self.send_json(result, HTTPStatus.CREATED)
            if parsed.path == "/api/uploads":
                if not self.authorized_for_uploads():
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                query = parse_qs(parsed.query)
                filename = str((query.get("filename") or [""])[0]).strip()
                kind = str((query.get("kind") or [""])[0]).strip().lower()
                length = int(self.headers.get("Content-Length") or 0)
                result = save_upload(
                    self.server.config,
                    self.rfile,
                    filename=filename,
                    kind=kind,
                    content_length=length,
                )
                if result["kind"] == "source":
                    result["candidate_id"] = ingest_uploaded_media(self.server.config, result)
                result["download_url"] = signed_upload_url(result["id"])
                return self.send_json(result, HTTPStatus.CREATED)
            payload = self.read_json()
            if parsed.path == "/api/actions":
                task_id = self.server.start_action(payload)
                return self.send_json({"task_id": task_id, "status": "RUNNING"}, HTTPStatus.ACCEPTED)
            if parsed.path == "/api/candidates/delete":
                return self.send_json(delete_candidates(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/publications":
                return self.send_json({"id": save_publication(self.server.config, payload)}, HTTPStatus.CREATED)
            if parsed.path == "/api/publications/status":
                return self.send_json(update_publication_status(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/download-claims":
                return self.send_json(save_download_claim(self.server.config, payload), HTTPStatus.CREATED)
            if parsed.path == "/api/download-claims/metrics":
                return self.send_json(update_download_claim_metrics(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/callback":
                return self.send_json(save_callback(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/metrics":
                return self.send_json({"id": save_metrics(self.server.config, payload)}, HTTPStatus.CREATED)
            if parsed.path == "/api/move-to-review":
                return self.send_json(
                    move_candidate_to_review(
                        self.server.config,
                        str(payload.get("candidate_id") or payload.get("id") or ""),
                    ),
                    HTTPStatus.OK,
                )
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

    def authorized_for_uploads(self) -> bool:
        return True

    def authorized_for_upload_kind(self, kind: str) -> bool:
        if not upload_kind_requires_token(kind):
            return True
        return self.authorized_for_uploads()

    def pending_upload_kind(self, upload_id: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", str(upload_id or "")):
            return ""
        try:
            base = storage_root(self.server.config) / "uploads" / ".pending" / upload_id
            manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
            return str(manifest.get("kind") or "").lower()
        except (OSError, ValueError, json.JSONDecodeError):
            return ""

    def valid_upload_signature(self, upload_id: str, query: dict[str, list[str]]) -> bool:
        return True

    def send_private_upload(self, parsed: Any, *, head_only: bool) -> None:
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 4:
            return self.send_error(HTTPStatus.NOT_FOUND)
        upload_id = parts[2]
        query = parse_qs(parsed.query)
        if not self.authorized_for_uploads() and not self.valid_upload_signature(upload_id, query):
            return self.send_json({"error": "private download link is invalid or expired"}, HTTPStatus.UNAUTHORIZED)
        try:
            item = find_upload(self.server.config, upload_id)
        except ValueError:
            return self.send_error(HTTPStatus.NOT_FOUND)
        original = str(item.get("original_filename") or Path(str(item["path"])).name)
        self.send_file(
            Path(str(item["path"])),
            cache="private, no-store",
            disposition=f"attachment; filename=media{Path(original).suffix}; filename*=UTF-8''{quote(original)}",
            head_only=head_only,
        )

    def send_candidate_source(self, parsed: Any, *, head_only: bool) -> None:
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 4:
            return self.send_error(HTTPStatus.NOT_FOUND)
        source = candidate_source_media(self.server.config, parts[2])
        if source is None:
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_file(source, cache="private, no-store", head_only=head_only)

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
        if requested.startswith("/assets/brand/"):
            path = public_brand_asset_path(requested)
            if path is None:
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.send_file(path, cache="public, max-age=3600")
        relative = "index.html" if requested in {"", "/"} else requested.lstrip("/")
        path = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in path.parents or not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_file(path, cache="no-cache")

    def send_media(self, relative: str, download: bool = False) -> None:
        relative = unquote(relative)
        if relative.startswith("review/"):
            root = storage_root(self.server.config)
            path = (root / relative).resolve()
        else:
            root = (workspace_dir(self.server.config) / "ready_for_review").resolve()
            path = (root / relative).resolve()
        if root not in path.parents or not path.is_file() or "uploads" in path.parts:
            return self.send_error(HTTPStatus.NOT_FOUND)
        disposition = ""
        if download:
            filename = path.name if path.name != "video.mp4" else f"{path.parent.name}{path.suffix}"
            disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        self.send_file(path, cache="private, max-age=60", disposition=disposition)

    def send_file(
        self,
        path: Path,
        cache: str,
        disposition: str = "",
        head_only: bool = False,
    ) -> None:
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
        if disposition:
            self.send_header("Content-Disposition", disposition)
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD" or head_only:
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
