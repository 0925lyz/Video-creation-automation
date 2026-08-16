from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping


CONTENT_TYPES = (
    "football",
    "sports_highlight",
    "dance_music",
    "comedy_life",
    "cartoon_kids",
    "soap_opera",
    "commentary",
    "unknown",
)

SEGMENT_STRATEGIES = (
    "uniform",
    "sports_highlight",
    "rhythm_cut",
    "visual_peak",
    "story_safe",
    "dialogue_scene",
    "summary_cut",
)

AUDIO_POLICIES = (
    "source_plus_funk",
    "funk_or_source_music",
    "funk_only",
    "preserve_ptbr_voice_light_bgm",
    "localize_ptbr",
    "bgm_only",
)


@dataclass(frozen=True)
class Classification:
    content_type: str
    confidence: float
    matched_rules: tuple[str, ...]
    suggested_segment_strategy: str
    suggested_audio_policy: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["matched_rules"] = list(self.matched_rules)
        return payload


@dataclass(frozen=True)
class ProductionStrategy:
    content_type: str
    content_type_confidence: float
    matched_rules: tuple[str, ...]
    segment_strategy: str
    audio_policy: str
    max_segments: int
    max_duration: float
    operator_override: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["matched_rules"] = list(self.matched_rules)
        return payload


RULES: dict[str, tuple[str, ...]] = {
    "football": (
        r"\bfootball\b", r"\bsoccer\b", r"\bfutebol\b", r"\bgoal\b", r"\bgol\b",
        r"\bpenalt(?:y|i)\b", r"\bchampions league\b", r"\bworld cup\b", r"足球", r"进球",
        r"点球", r"世界杯", r"欧冠", r"内马尔", r"梅西", r"c罗", r"ronaldo", r"neymar",
    ),
    "sports_highlight": (
        r"\bbasketball\b", r"\bnba\b", r"\bvolleyball\b", r"\btennis\b", r"\bufc\b",
        r"\bknockout\b", r"\bhighlights?\b", r"集锦", r"体育", r"篮球", r"网球", r"排球",
    ),
    "dance_music": (
        r"\bdance\b", r"\bmusic\b", r"\bfunk\b", r"\bremix\b", r"\bdj\b", r"舞蹈",
        r"跳舞", r"音乐", r"歌曲", r"热舞",
    ),
    "comedy_life": (
        r"\bcomedy\b", r"\bfunny\b", r"\bprank\b", r"\blifestyle\b", r"搞笑", r"生活",
        r"日常", r"恶作剧", r"爆笑",
    ),
    "cartoon_kids": (
        r"\bcartoon\b", r"\banimation\b", r"\bkids?\b", r"\bchildren\b", r"动画", r"动漫",
        r"少儿", r"儿童",
    ),
    "soap_opera": (
        r"\bsoap opera\b", r"\bdrama\b", r"\bnovela\b", r"\bseries\b", r"短剧", r"电视剧",
        r"剧情", r"肥皂剧",
    ),
    "commentary": (
        r"\bcommentary\b", r"\bexplained\b", r"\bnews\b", r"\btutorial\b", r"\breview\b",
        r"解说", r"讲解", r"教程", r"新闻", r"测评", r"知识",
    ),
}

DEFAULTS: dict[str, tuple[str, str]] = {
    "football": ("sports_highlight", "source_plus_funk"),
    "sports_highlight": ("sports_highlight", "source_plus_funk"),
    "dance_music": ("rhythm_cut", "funk_or_source_music"),
    "comedy_life": ("visual_peak", "funk_only"),
    "cartoon_kids": ("story_safe", "preserve_ptbr_voice_light_bgm"),
    "soap_opera": ("dialogue_scene", "preserve_ptbr_voice_light_bgm"),
    "commentary": ("summary_cut", "localize_ptbr"),
    "unknown": ("uniform", "funk_or_source_music"),
}


def _candidate_text(candidate: Mapping[str, Any]) -> str:
    metadata = candidate.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    values: list[str] = [
        str(candidate.get("title") or ""),
        str(candidate.get("description") or ""),
        str(metadata.get("title") or ""),
        str(metadata.get("description") or ""),
        str(metadata.get("channel") or metadata.get("uploader") or ""),
        str(metadata.get("asr_text") or ""),
        str(metadata.get("ocr_text") or ""),
    ]
    tags = candidate.get("tags") or metadata.get("tags") or []
    if isinstance(tags, (list, tuple, set)):
        values.extend(str(tag) for tag in tags)
    else:
        values.append(str(tags))
    return " ".join(values).lower()


def classify_content(candidate: Mapping[str, Any]) -> Classification:
    text = _candidate_text(candidate)
    scored: list[tuple[int, str, tuple[str, ...]]] = []
    for content_type, patterns in RULES.items():
        matches = tuple(pattern for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE))
        scored.append((len(matches), content_type, matches))
    hit_count, content_type, matches = max(scored, key=lambda item: (item[0], item[1] == "football"))
    if hit_count == 0:
        content_type = "unknown"
        confidence = 0.25
        matches = ()
    else:
        second_best = sorted((score for score, _, _ in scored), reverse=True)[1]
        margin = max(0, hit_count - second_best)
        confidence = min(0.96, 0.52 + hit_count * 0.08 + margin * 0.05)
    segment_strategy, audio_policy = DEFAULTS[content_type]
    return Classification(
        content_type=content_type,
        confidence=round(confidence, 3),
        matched_rules=matches,
        suggested_segment_strategy=segment_strategy,
        suggested_audio_policy=audio_policy,
    )


def resolve_production_strategy(
    candidate: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
    *,
    default_max_segments: int = 3,
    default_max_duration: float = 30.0,
) -> ProductionStrategy:
    overrides = overrides or {}
    classification = classify_content(candidate)

    content_type = str(overrides.get("content_type") or "auto").strip().lower()
    if content_type == "auto":
        content_type = classification.content_type
    if content_type not in CONTENT_TYPES:
        raise ValueError(f"unsupported content_type: {content_type}")

    default_segment, default_audio = DEFAULTS[content_type]
    segment_strategy = str(overrides.get("segment_strategy") or "auto").strip().lower()
    if segment_strategy == "auto":
        segment_strategy = default_segment
    if segment_strategy not in SEGMENT_STRATEGIES:
        raise ValueError(f"unsupported segment_strategy: {segment_strategy}")

    audio_policy = str(overrides.get("audio_policy") or "auto").strip().lower()
    if audio_policy == "auto":
        audio_policy = default_audio
    if audio_policy not in AUDIO_POLICIES:
        raise ValueError(f"unsupported audio_policy: {audio_policy}")

    max_segments = max(1, min(10, int(overrides.get("max_segments") or default_max_segments)))
    max_duration = max(12.0, min(60.0, float(overrides.get("max_duration") or default_max_duration)))
    operator_override = any(
        str(overrides.get(field) or "auto").strip().lower() != "auto"
        for field in ("content_type", "segment_strategy", "audio_policy")
    ) or "max_segments" in overrides or "max_duration" in overrides

    return ProductionStrategy(
        content_type=content_type,
        content_type_confidence=classification.confidence if content_type == classification.content_type else 1.0,
        matched_rules=classification.matched_rules,
        segment_strategy=segment_strategy,
        audio_policy=audio_policy,
        max_segments=max_segments,
        max_duration=max_duration,
        operator_override=operator_override,
    )


def render_audio_mode(audio_policy: str) -> str:
    if audio_policy == "localize_ptbr":
        return "localized"
    if audio_policy in {"funk_only", "bgm_only"}:
        return "silent"
    return "preserve_source"
