from __future__ import annotations

import json
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .core import (
    append_event,
    connect_db,
    now_iso,
    platform_from_url,
    storage_root,
    workspace_dir,
)
from .publishing_copywriter import (
    generate_publishing_copy,
    source_material_from,
    youtube_description,
    youtube_title_with_hashtags,
)


QUEUE_STATUSES = {"QUEUED", "SCHEDULED", "PUBLISHING"}
FINAL_STATUSES = {"PUBLISHED"}
ACTIVE_PUBLICATION_STATUSES = QUEUE_STATUSES | FINAL_STATUSES
DEFAULT_ALLOWED_SOURCE_PLATFORMS = {"tiktok", "douyin", "facebook", "bilibili", "original"}
DEFAULT_BLOCKED_SOURCE_PLATFORMS = {"youtube"}
DEFAULT_SCHEDULE_TIMES = ("11:00", "15:30", "19:00")
FOOTBALL_TERMS = (
    "足球", "futebol", "football", "soccer", "neymar", "vinicius", "vini jr",
    "rodrygo", "endrick", "flamengo", "palmeiras", "corinthians", "botafogo",
    "cruzeiro", "brasileirão", "brasileirao", "libertadores", "copa do brasil",
)


def parse_json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return parse_json(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def publishing_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("publishing", {}) or {}


def publishing_enabled(config: dict[str, Any]) -> bool:
    settings = publishing_config(config)
    return bool(settings) and settings.get("enabled", False) is not False


def publishing_timezone(config: dict[str, Any], account: dict[str, Any] | None = None) -> str:
    return str(
        (account or {}).get("timezone")
        or publishing_config(config).get("timezone")
        or config.get("run", {}).get("timezone")
        or "America/Sao_Paulo"
    )


def youtube_accounts(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    accounts = publishing_config(config).get("accounts") or {}
    if not isinstance(accounts, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in accounts.items():
        if isinstance(value, dict) and str(value.get("platform") or "").lower() == "youtube":
            result[str(key)] = value
    return result


def normalized_set(values: Any) -> set[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return set()
    return {str(value).strip().lower() for value in values if str(value).strip()}


def list_values(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, list):
            result.extend(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            result.append(value.strip())
    return result


def infer_tags_from_text(*values: Any) -> list[str]:
    haystack = " ".join(str(value or "").lower() for value in values if str(value or "").strip())
    if haystack and any(term.lower() in haystack for term in FOOTBALL_TERMS):
        return ["足球类"]
    return []


def collect_publish_tags(
    candidate: dict[str, Any],
    metadata: dict[str, Any],
    review_metadata: dict[str, Any],
) -> list[str]:
    tags = list_values(
        metadata.get("tags"),
        metadata.get("content_tags"),
        metadata.get("category"),
        metadata.get("initial_category"),
        review_metadata.get("tags"),
        review_metadata.get("content_tags"),
        review_metadata.get("category"),
        review_metadata.get("initial_category"),
    )
    if not tags:
        source = review_metadata.get("source") if isinstance(review_metadata.get("source"), dict) else {}
        tags.extend(
            infer_tags_from_text(
                metadata.get("keyword"),
                metadata.get("category"),
                candidate.get("title"),
                candidate.get("description"),
                source.get("title"),
                source.get("description"),
            )
        )
    return list(dict.fromkeys(tags))


def review_package_dirs(config: dict[str, Any], candidate_id: str) -> list[Path]:
    roots = [workspace_dir(config) / "ready_for_review", storage_root(config) / "review"]
    package_names = {candidate_id}
    for root in roots:
        if root.exists():
            package_names.update(path.name for path in root.glob(f"{candidate_id}_part*") if path.is_dir())
    dirs: list[Path] = []
    for name in sorted(package_names):
        for root in roots:
            path = root / name
            if path.is_dir():
                dirs.append(path)
    return dirs


def review_metadata_for_candidate(config: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for package_dir in review_package_dirs(config, candidate_id):
        metadata = read_json_file(package_dir / "metadata.json")
        if metadata:
            return metadata
    return {}


def publication_source_context(connection: Any, candidate_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT id,parent_id,platform,url,title,description,metadata_json,status FROM candidates WHERE id=?",
        (candidate_id,),
    ).fetchone()
    if not row:
        return {}
    candidate = dict(row)
    parent = None
    if candidate.get("parent_id"):
        parent = connection.execute(
            "SELECT id,platform,url,title,description,metadata_json,status FROM candidates WHERE id=?",
            (candidate["parent_id"],),
        ).fetchone()
    parent_dict = dict(parent) if parent else {}
    metadata = parse_json(candidate.get("metadata_json") or "{}")
    parent_metadata = parse_json(parent_dict.get("metadata_json") or "{}")
    source_blob = metadata.get("source") if isinstance(metadata.get("source"), dict) else {}
    source_row = parent_dict or candidate
    source_url = str(source_row.get("url") or source_blob.get("url") or "")
    source_platform = (
        str(source_row.get("platform") or "")
        or str(source_blob.get("platform") or "")
        or platform_from_url(source_url)
    ).strip().lower()
    return {
        "candidate": candidate,
        "metadata": metadata,
        "parent_metadata": parent_metadata,
        "source_platform": source_platform or platform_from_url(source_url),
        "source_url": source_url,
        "source_title": str(source_row.get("title") or source_blob.get("title") or ""),
        "source_description": str(source_row.get("description") or source_blob.get("description") or ""),
    }


def publication_tracking_metadata(
    connection: Any,
    candidate_id: str,
    context: dict[str, Any],
    review: dict[str, Any],
    variant: str,
) -> dict[str, str]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    parent_metadata = context.get("parent_metadata") if isinstance(context.get("parent_metadata"), dict) else {}

    def first_value(sources: tuple[dict[str, Any], ...], keys: tuple[str, ...], default: str) -> str:
        for source in sources:
            for key in keys:
                value = str(source.get(key) or "").strip()
                if value:
                    return value
        return default

    category = first_value(
        (metadata, parent_metadata, review),
        ("initial_category", "category", "category_label"),
        "unknown",
    )
    keyword = first_value(
        (metadata, parent_metadata, review),
        ("keyword", "initial_keyword", "crawl_keyword", "search_keyword"),
        "unknown",
    )
    slice_id = first_value((review, metadata), ("slice_id",), "")
    version_id = first_value((review, metadata), ("version_id", "production_run_id"), "")
    output = connection.execute(
        """
        SELECT production_run_id,slice_id FROM production_outputs
        WHERE candidate_id=? AND (?='' OR variant=?)
        ORDER BY updated_at DESC LIMIT 1
        """,
        (candidate_id, variant, variant),
    ).fetchone()
    if output:
        slice_id = slice_id or str(output["slice_id"] or "")
        version_id = version_id or str(output["production_run_id"] or "")
    return {
        "source_category": category,
        "source_keyword": keyword,
        "slice_id": slice_id,
        "version_id": version_id,
    }


def source_allowed_for_publish(candidate: dict[str, Any], account_config: dict[str, Any]) -> tuple[bool, str]:
    source_platform = str(candidate.get("source_platform") or "").strip().lower()
    source_url = str(candidate.get("source_url") or "")
    url_platform = platform_from_url(source_url)
    blocked = normalized_set(account_config.get("blocked_source_platforms")) or DEFAULT_BLOCKED_SOURCE_PLATFORMS
    allowed = normalized_set(account_config.get("allowed_source_platforms")) or DEFAULT_ALLOWED_SOURCE_PLATFORMS
    effective_platform = source_platform or url_platform
    if source_platform in blocked or url_platform in blocked:
        return False, f"source platform blocked: {source_platform or url_platform}"
    if allowed and effective_platform not in allowed:
        return False, f"source platform not allowed: {effective_platform or 'unknown'}"
    return True, ""


def resolve_publish_routes(
    candidate: dict[str, Any],
    review: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not publishing_enabled(config):
        return []
    tags = set(collect_publish_tags(candidate, candidate.get("_metadata") or {}, review))
    if not tags:
        return []
    routes: list[dict[str, Any]] = []
    for account_key, account in youtube_accounts(config).items():
        if account.get("enabled", True) is False:
            continue
        content_tags = set(list_values(account.get("content_tags")))
        if content_tags and tags.intersection(content_tags):
            routes.append({"account_key": account_key, "account": account, "tags": sorted(tags)})
    return routes


def parse_schedule_times(account: dict[str, Any]) -> list[datetime_time]:
    raw_times = list_values(account.get("schedule_times")) or list(DEFAULT_SCHEDULE_TIMES)
    parsed: list[datetime_time] = []
    for value in raw_times:
        hour, separator, minute = value.partition(":")
        if not separator:
            continue
        parsed.append(datetime_time(hour=max(0, min(23, int(hour))), minute=max(0, min(59, int(minute)))))
    return sorted(parsed) or [datetime_time(11, 0), datetime_time(15, 30), datetime_time(19, 0)]


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def publication_dates_for_account(connection: Any, account_key: str, tz: ZoneInfo) -> dict[str, set[str]]:
    dates: dict[str, set[str]] = {}
    rows = connection.execute(
        """
        SELECT scheduled_at,published_at,status
        FROM publications
        WHERE platform='youtube' AND account=? AND status IN ('QUEUED','SCHEDULED','PUBLISHING','PUBLISHED')
        """,
        (account_key,),
    ).fetchall()
    for row in rows:
        parsed = parse_datetime(str(row["published_at"] or row["scheduled_at"] or ""))
        if not parsed:
            continue
        local = parsed.astimezone(tz)
        dates.setdefault(local.date().isoformat(), set()).add(local.strftime("%H:%M"))
    return dates


def next_publish_slot(
    connection: Any,
    account_key: str,
    account: dict[str, Any],
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[str, bool]:
    timezone_name = publishing_timezone(config, account)
    tz = ZoneInfo(timezone_name)
    local_now = (now or datetime.now(tz)).astimezone(tz)
    daily_limit = max(1, int(account.get("daily_limit") or len(DEFAULT_SCHEDULE_TIMES)))
    slots = parse_schedule_times(account)
    scheduled_by_date = publication_dates_for_account(connection, account_key, tz)
    for offset in range(0, 366):
        day = local_now.date() + timedelta(days=offset)
        date_key = day.isoformat()
        used_slots = scheduled_by_date.get(date_key, set())
        if len(used_slots) >= daily_limit:
            continue
        for slot in slots:
            scheduled = datetime.combine(day, slot, tzinfo=tz)
            if offset == 0 and scheduled <= local_now:
                continue
            if scheduled.strftime("%H:%M") in used_slots:
                continue
            shifted = day != local_now.date()
            return scheduled.isoformat(), shifted
    raise RuntimeError(f"no available publish slot for account {account_key}")


def youtube_publication_asset(config: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    for package_dir in review_package_dirs(config, candidate_id):
        metadata = read_json_file(package_dir / "metadata.json")
        for video in sorted(package_dir.glob("*.mp4")):
            if not video.is_file():
                continue
            variant = "通用版" if "通用版" in video.name or video.name == "video.mp4" else ("FB版" if "FB版" in video.name else "")
            priority = 0 if variant == "通用版" else 1 if video.name == "video.mp4" else 3 if variant == "FB版" else 2
            assets.append({
                "priority": priority,
                "path": video,
                "package_id": package_dir.name,
                "asset_id": package_dir.name if video.name == "video.mp4" else f"{package_dir.name}:{video.stem}",
                "variant": variant,
                "metadata": metadata,
            })
    allowed_assets = [asset for asset in assets if asset["variant"] != "FB版"]
    selected = sorted(allowed_assets or assets, key=lambda item: (item["priority"], item["package_id"], item["path"].name))
    return selected[0] if selected else {}


def publication_text(
    config: dict[str, Any],
    candidate: dict[str, Any],
    review: dict[str, Any],
    tags: list[str],
) -> dict[str, Any]:
    youtube = review.get("youtube") if isinstance(review.get("youtube"), dict) else {}
    source_material = source_material_from(candidate, candidate.get("_metadata") or {}, review, tags)
    generated = generate_publishing_copy(config, source_material)
    title = str(generated.get("title") or youtube.get("title") or candidate.get("title") or "Jaguar TV").strip()
    generated_tags = generated.get("tags") if isinstance(generated.get("tags"), list) else []
    from .publish_flow import youtube_copy_from_provenance

    return youtube_copy_from_provenance(
        {"title": title, "tags": [str(item) for item in generated_tags]},
        source_material,
        seed=str(candidate.get("id") or candidate.get("source_id") or title),
    )


def existing_publication(connection: Any, candidate_id: str, account_key: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT * FROM publications
        WHERE candidate_id=? AND platform='youtube' AND account=?
          AND status IN ('QUEUED','SCHEDULED','PUBLISHING','PUBLISHED')
        ORDER BY id DESC LIMIT 1
        """,
        (candidate_id, account_key),
    ).fetchone()
    return dict(row) if row else None


def enqueue_approved_publication(
    config: dict[str, Any],
    candidate_id: str,
    account_key: str = "",
    variant: str = "",
    *,
    reviewer: str = "",
    review_decision_at: str = "",
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    connection = connect_db(config)
    context = publication_source_context(connection, candidate_id)
    if not context:
        raise ValueError("candidate does not exist")
    candidate = {**context["candidate"], "_metadata": context["metadata"]}
    candidate["source_platform"] = context["source_platform"]
    candidate["source_url"] = context["source_url"]
    candidate["source_title"] = context.get("source_title") or ""
    candidate["source_description"] = context.get("source_description") or ""
    if str(candidate.get("status") or "") != "APPROVED":
        return {"candidate_id": candidate_id, "status": "SKIPPED", "reason": "candidate is not APPROVED"}
    asset = youtube_publication_asset(config, candidate_id)
    review = dict(asset.get("metadata") or review_metadata_for_candidate(config, candidate_id))
    routes = resolve_publish_routes(candidate, review, config)
    if account_key:
        account = youtube_accounts(config).get(account_key)
        routes = [{"account_key": account_key, "account": account or {}, "tags": collect_publish_tags(candidate, context["metadata"], review)}] if account else []
    if not routes:
        payload = {"reason": "no publish route", "tags": collect_publish_tags(candidate, context["metadata"], review)}
        if not dry_run:
            append_event(connection, candidate_id, "PUBLISH_BLOCKED_NO_ROUTE", payload)
        return {"candidate_id": candidate_id, "status": "BLOCKED", **payload}
    route = routes[0]
    account_key = route["account_key"]
    account = route["account"]
    allowed, reason = source_allowed_for_publish(candidate, account)
    if not allowed:
        payload = {
            "reason": reason,
            "account_key": account_key,
            "source_platform": candidate.get("source_platform") or "",
        }
        if not dry_run:
            append_event(connection, candidate_id, "PUBLISH_BLOCKED_SOURCE_PLATFORM", payload)
        return {"candidate_id": candidate_id, "status": "BLOCKED", **payload}
    if not asset:
        payload = {"reason": "no review video asset", "account_key": account_key}
        if not dry_run:
            append_event(connection, candidate_id, "PUBLISH_BLOCKED_NO_ASSET", payload)
        return {"candidate_id": candidate_id, "status": "BLOCKED", **payload}
    if variant and asset.get("variant") and variant != asset.get("variant"):
        return {"candidate_id": candidate_id, "status": "BLOCKED", "reason": f"requested variant not available: {variant}"}
    existing = existing_publication(connection, candidate_id, account_key)
    if existing:
        return {"candidate_id": candidate_id, "status": "EXISTS", "publication": existing}
    scheduled_at, shifted = next_publish_slot(connection, account_key, account, config, now=now)
    text = publication_text(config, candidate, review, route.get("tags") or [])
    auth = connection.execute(
        "SELECT channel_id,channel_title FROM youtube_channel_auths WHERE account=?",
        (account_key,),
    ).fetchone()
    account_label = str((auth["channel_title"] if auth else "") or account.get("label") or account_key)
    channel_id = str((auth["channel_id"] if auth else "") or account.get("channel_id") or "")
    scheduled_datetime = parse_datetime(scheduled_at)
    timezone_name = publishing_timezone(config, account)
    tracking = publication_tracking_metadata(
        connection,
        candidate_id,
        context,
        review,
        str(asset.get("variant") or "通用版"),
    )
    payload = {
        "candidate_id": candidate_id,
        "account_key": account_key,
        "account_label": account_label,
        "channel_id": channel_id,
        "scheduled_at": scheduled_at,
        "scheduled_local_at": scheduled_datetime.astimezone(ZoneInfo(timezone_name)).isoformat() if scheduled_datetime else scheduled_at,
        "scheduled_utc_at": scheduled_datetime.astimezone(timezone.utc).isoformat() if scheduled_datetime else "",
        "timezone": timezone_name,
        "asset_id": asset["asset_id"],
        "package_id": asset["package_id"],
        "variant": asset.get("variant") or "通用版",
        "source_platform": candidate.get("source_platform") or "",
        "title": text["title"],
        "description": text["description"],
        "tags": text["tags"],
        "privacy_status": str(account.get("default_privacy_status") or account.get("default_visibility") or "public"),
        "daily_limit_shifted": shifted,
        **tracking,
    }
    if dry_run:
        return {"status": "WOULD_QUEUE", **payload}
    timestamp = now_iso()
    cursor = connection.execute(
        """
        INSERT INTO publications(
          candidate_id,package_id,asset_id,variant,source_platform,platform,account,account_label,
          channel_id,scheduled_at,status,title,description,tags_json,privacy_status,timezone,
          reviewer,review_decision_at,platform_account_id,authorized_account_id,
          platform_username_snapshot,scheduled_local_at,scheduled_utc_at,source_category,
          source_keyword,slice_id,version_id,created_at,updated_at
        ) VALUES(
          :candidate_id,:package_id,:asset_id,:variant,:source_platform,'youtube',:account,:account_label,
          :channel_id,:scheduled_at,'SCHEDULED',:title,:description,:tags_json,:privacy_status,:timezone,
          :reviewer,:review_decision_at,:account,:account,:account_label,:scheduled_local_at,
          :scheduled_utc_at,:source_category,:source_keyword,:slice_id,:version_id,:created_at,:updated_at
        )
        """,
        {
            **payload,
            "account": account_key,
            "tags_json": json.dumps(payload["tags"], ensure_ascii=False),
            "reviewer": reviewer,
            "review_decision_at": review_decision_at or timestamp,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    )
    publication_id = int(cursor.lastrowid)
    connection.execute(
        "UPDATE publications SET publish_task_id=? WHERE id=?",
        (f"publication:{publication_id}", publication_id),
    )
    connection.commit()
    event_payload = {**payload, "publication_id": publication_id, "publish_task_id": f"publication:{publication_id}"}
    append_event(connection, candidate_id, "PUBLICATION_QUEUED", event_payload)
    if shifted:
        append_event(connection, candidate_id, "PUBLISH_BLOCKED_DAILY_LIMIT", event_payload)
    return {"status": "SCHEDULED", "publication_id": publication_id, **payload}


def auto_enqueue_approved_publication(
    config: dict[str, Any],
    candidate_id: str,
    *,
    reviewer: str = "",
    review_decision_at: str = "",
) -> dict[str, Any]:
    if not publishing_enabled(config):
        return {"candidate_id": candidate_id, "status": "SKIPPED", "reason": "publishing disabled"}
    return enqueue_approved_publication(
        config,
        candidate_id,
        reviewer=reviewer,
        review_decision_at=review_decision_at,
    )


def dry_run_approved_queue(config: dict[str, Any], *, candidate_id: str = "") -> list[dict[str, Any]]:
    connection = connect_db(config)
    if candidate_id:
        rows = connection.execute(
            "SELECT id FROM candidates WHERE id=? AND status='APPROVED'", (candidate_id,)
        ).fetchall()
    else:
        rows = connection.execute("SELECT id FROM candidates WHERE status='APPROVED' ORDER BY updated_at DESC").fetchall()
    return [
        enqueue_approved_publication(config, str(row["id"]), dry_run=True)
        for row in rows
    ]


def publication_state_for_candidates(config: dict[str, Any], candidate_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not candidate_ids:
        return {}
    connection = connect_db(config)
    placeholders = ",".join("?" for _ in candidate_ids)
    rows = connection.execute(
        f"""
        SELECT *
        FROM publications
        WHERE candidate_id IN ({placeholders}) AND platform='youtube'
        ORDER BY id DESC
        """,
        candidate_ids,
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        candidate_id = str(row["candidate_id"])
        if candidate_id in result:
            continue
        result[candidate_id] = dict(row)
    events = connection.execute(
        f"""
        SELECT candidate_id,event_type,payload_json,created_at
        FROM events
        WHERE candidate_id IN ({placeholders})
          AND event_type IN ('PUBLISH_BLOCKED_SOURCE_PLATFORM','PUBLISH_BLOCKED_NO_ROUTE','PUBLISH_BLOCKED_DAILY_LIMIT','PUBLISH_BLOCKED_NO_ASSET')
        ORDER BY id DESC
        """,
        candidate_ids,
    ).fetchall()
    for row in events:
        candidate_id = str(row["candidate_id"])
        if candidate_id in result and result[candidate_id].get("status") in ACTIVE_PUBLICATION_STATUSES:
            continue
        payload = parse_json(row["payload_json"])
        result.setdefault(candidate_id, {
            "candidate_id": candidate_id,
            "status": "BLOCKED",
            "event_type": row["event_type"],
            "reason": payload.get("reason") or "",
            "source_platform": payload.get("source_platform") or "",
            "created_at": row["created_at"],
        })
    return result
