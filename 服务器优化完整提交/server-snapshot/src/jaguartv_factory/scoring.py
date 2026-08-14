"""Candidate scoring v2.

Six normalized dimensions combined by configurable weights
(config/scoring.yaml). Every score ships with a breakdown so reviewers can
see *why* a candidate ranks where it does, and so weights can be
recalibrated against first_watch attribution data later.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "version": "v2",
    "weights": {
        "velocity": 0.15, "engagement": 0.20, "relevance": 0.20,
        "editability": 0.15, "brazil_fit": 0.15, "freshness": 0.15,
    },
    "baselines": {
        "like_rate": 0.04, "comment_rate": 0.004, "share_rate": 0.002,
        "velocity_log10_cap": 6,
    },
    "editability": {
        "ideal_duration": [15, 180], "max_duration": 600,
        "min_duration": 8, "min_height_bonus": 720,
    },
    "freshness": {"tiers": [[3, 1.0], [14, 0.8], [60, 0.5], [99999, 0.25]]},
    "brazil_vocabulary": {"strong": ["brasil", "巴西"], "medium": ["futebol", "football", "足球"]},
}


@lru_cache(maxsize=4)
def load_scoring_config(path: str | None = None) -> dict[str, Any]:
    if path and Path(path).exists():
        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        merged = {**DEFAULTS, **loaded}
        for key in ("weights", "baselines", "editability", "freshness", "brazil_vocabulary"):
            merged[key] = {**DEFAULTS[key], **(loaded.get(key) or {})}
        return merged
    return DEFAULTS


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def published_days_ago(info: dict[str, Any]) -> float | None:
    timestamp = info.get("timestamp") or info.get("release_timestamp")
    if timestamp:
        try:
            published = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - published).total_seconds() / 86400)
        except (ValueError, OSError, OverflowError):
            pass
    upload_date = str(info.get("upload_date") or "")
    if re.fullmatch(r"\d{8}", upload_date):
        try:
            published = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - published).total_seconds() / 86400)
        except ValueError:
            pass
    return None


def score_velocity(info: dict[str, Any], scoring: dict[str, Any]) -> tuple[float, bool]:
    views = int(info.get("view_count") or 0)
    days = published_days_ago(info)
    estimated = days is None
    daily = views / max(1.0, days if days is not None else 30.0)
    cap = float(scoring["baselines"].get("velocity_log10_cap", 6))
    return clamp(math.log10(1 + daily) / cap), estimated


def score_engagement(info: dict[str, Any], scoring: dict[str, Any]) -> tuple[float, bool]:
    views = int(info.get("view_count") or 0)
    if views <= 0:
        return 0.5, True
    baselines = scoring["baselines"]
    rates = []
    missing = 0
    for field, baseline_key in (("like_count", "like_rate"), ("comment_count", "comment_rate"), ("repost_count", "share_rate")):
        raw = info.get(field)
        if raw is None:
            missing += 1
            continue
        rates.append(clamp((int(raw) / views) / float(baselines[baseline_key])))
    if not rates:
        return 0.5, True
    return sum(rates) / len(rates), missing > 0


def score_relevance(info: dict[str, Any], keyword: str) -> float:
    parts = [part.lower() for part in re.split(r"\s+", keyword or "") if len(part) > 1]
    if not parts:
        return 0.5
    title = str(info.get("title") or "").lower()
    description = str(info.get("description") or "").lower()
    tags = " ".join(str(tag).lower() for tag in (info.get("tags") or []))
    hit = 0.0
    for part in parts:
        if part in title:
            hit += 1.0
        elif part in tags:
            hit += 0.7
        elif part in description:
            hit += 0.5
    return clamp(hit / len(parts))


def score_editability(info: dict[str, Any], scoring: dict[str, Any]) -> float:
    settings = scoring["editability"]
    duration = float(info.get("duration") or 0)
    ideal_low, ideal_high = settings.get("ideal_duration", [15, 180])
    max_duration = float(settings.get("max_duration", 600))
    min_duration = float(settings.get("min_duration", 8))
    if not duration:
        base = 0.6  # unknown duration: neutral-ish
    elif ideal_low <= duration <= ideal_high:
        base = 1.0
    elif ideal_high < duration <= max_duration:
        base = 1.0 - 0.6 * (duration - ideal_high) / (max_duration - ideal_high)
    elif min_duration <= duration < ideal_low:
        base = 0.7
    else:
        base = 0.15
    height = int(info.get("height") or 0)
    if height >= int(settings.get("min_height_bonus", 720)):
        base = clamp(base + 0.1)
    if info.get("subtitles") or info.get("automatic_captions"):
        base = clamp(base + 0.1)
    return clamp(base)


def score_brazil_fit(info: dict[str, Any], scoring: dict[str, Any]) -> float:
    text = f"{info.get('title') or ''} {info.get('description') or ''}".lower()
    vocabulary = scoring["brazil_vocabulary"]
    if any(term in text for term in vocabulary.get("strong", [])):
        return 1.0
    if any(term in text for term in vocabulary.get("medium", [])):
        return 0.6
    return 0.2


def score_freshness(info: dict[str, Any], scoring: dict[str, Any]) -> tuple[float, bool]:
    days = published_days_ago(info)
    if days is None:
        return 0.5, True
    for max_age, score in scoring["freshness"].get("tiers", []):
        if days <= float(max_age):
            return float(score), False
    return 0.25, False


def score_candidate_v2(
    info: dict[str, Any], keyword: str, config_path: str | None = None
) -> tuple[float, dict[str, Any]]:
    """Return (total 0-100, breakdown dict)."""
    scoring = load_scoring_config(config_path)
    velocity, velocity_estimated = score_velocity(info, scoring)
    engagement, engagement_estimated = score_engagement(info, scoring)
    freshness, freshness_estimated = score_freshness(info, scoring)
    dimensions = {
        "velocity": velocity,
        "engagement": engagement,
        "relevance": score_relevance(info, keyword),
        "editability": score_editability(info, scoring),
        "brazil_fit": score_brazil_fit(info, scoring),
        "freshness": freshness,
    }
    weights = scoring["weights"]
    total = round(100 * sum(dimensions[name] * float(weights.get(name, 0)) for name in dimensions), 2)
    breakdown = {name: round(value, 3) for name, value in dimensions.items()}
    breakdown["total"] = total
    breakdown["algorithm_version"] = str(scoring.get("version", "v2"))
    estimated = [
        name for name, flag in (
            ("velocity", velocity_estimated), ("engagement", engagement_estimated), ("freshness", freshness_estimated),
        ) if flag
    ]
    if estimated:
        breakdown["estimated"] = estimated
    return total, breakdown
