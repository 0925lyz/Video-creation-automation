from pathlib import Path

import pytest

from jaguartv_factory.compliance import assert_render_allowed
from jaguartv_factory.highlight import SignalPoint, TranscriptCue, rank_highlight_windows
from jaguartv_factory.strategy import classify_content, render_audio_mode, resolve_production_strategy


def test_classifier_selects_football_strategy_and_audio_policy():
    result = classify_content({
        "title": "Neymar marca um gol incrível na Champions League",
        "description": "football highlights",
    })
    assert result.content_type == "football"
    assert result.confidence >= 0.7
    assert result.suggested_segment_strategy == "sports_highlight"
    assert result.suggested_audio_policy == "source_plus_funk"


def test_operator_can_override_every_strategy_dimension():
    strategy = resolve_production_strategy(
        {"title": "football goal"},
        {
            "content_type": "commentary",
            "segment_strategy": "summary_cut",
            "audio_policy": "localize_ptbr",
            "max_segments": 2,
            "max_duration": 24,
        },
    )
    assert strategy.content_type == "commentary"
    assert strategy.operator_override is True
    assert strategy.max_segments == 2
    assert strategy.max_duration == 24
    assert render_audio_mode(strategy.audio_policy) == "localized"


def test_highlight_ranking_uses_audio_motion_scene_keyword_and_replay():
    segments = rank_highlight_windows(
        source_duration=180,
        audio_points=[SignalPoint(20, 0.1), SignalPoint(92, 1.0), SignalPoint(150, 0.2)],
        motion_points=[SignalPoint(20, 0.1), SignalPoint(93, 0.95), SignalPoint(150, 0.1)],
        scene_times=[89, 91, 93, 96],
        transcript_cues=[TranscriptCue(90, 96, "Gol! Veja o replay desse chute")],
        max_segments=2,
        max_duration=30,
    )
    top = segments[0]
    assert 75 <= top["start"] <= 95
    assert top["highlight_score"] >= 90
    assert {"audio_peak", "motion_peak", "scene_change", "keyword"}.issubset(top["highlight_reasons"])
    if len(segments) == 2:
        left_start, left_end = segments[0]["source_start"], segments[0]["source_end"]
        right_start, right_end = segments[1]["source_start"], segments[1]["source_end"]
        overlap = max(0, min(left_end, right_end) - max(left_start, right_start))
        assert overlap <= 7.5


def test_compliance_blocks_unknown_rights_and_allows_verified():
    config = {"compliance": {"require_verified_rights": True}}
    with pytest.raises(PermissionError, match="BLOCKED_RIGHTS"):
        assert_render_allowed(config, "football", {})
    result = assert_render_allowed(config, "football", {}, {"rights_status": "LICENSED"})
    assert result["decision"] == "ALLOW"
    assert result["risk_level"] == "medium"
