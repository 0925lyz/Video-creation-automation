from pathlib import Path

import pytest

from jaguartv_factory.compliance import assert_render_allowed
from jaguartv_factory.highlight import SignalPoint, TranscriptCue, analyze_video, parse_srt, rank_highlight_windows
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


def test_long_video_analysis_uses_uniform_guard(monkeypatch, tmp_path: Path):
    media = tmp_path / "long.mp4"
    media.write_bytes(b"placeholder")

    def fail_signal_sampling(*args, **kwargs):
        raise AssertionError("long sources should not load full signal streams")

    monkeypatch.setattr("jaguartv_factory.highlight.sample_audio_envelope", fail_signal_sampling)
    monkeypatch.setattr("jaguartv_factory.highlight.sample_motion_intensity", fail_signal_sampling)
    segments = analyze_video(
        media,
        source_duration=1800,
        max_segments=3,
        max_duration=30,
        strategy="sports_highlight",
    )

    assert len(segments) == 3
    assert all(segment["fallback"] for segment in segments)
    assert all(segment["strategy"] == "sports_highlight_long_source_guard" for segment in segments)


def test_parse_srt_accepts_youtube_vtt_timing_settings(tmp_path: Path):
    subtitle = tmp_path / "source.en.vtt"
    subtitle.write_text(
        "WEBVTT\n\n"
        "00:00:07.860 --> 00:00:11.190 align:start position:0%\n"
        "Gol bonito\n\n",
        encoding="utf-8",
    )
    cues = parse_srt(subtitle)
    assert cues == [TranscriptCue(7.86, 11.19, "Gol bonito")]


def test_compliance_manual_review_does_not_block_unknown_rights():
    config = {"compliance": {"require_verified_rights": False}}
    result = assert_render_allowed(config, "football", {})
    assert result["decision"] == "REVIEW_REQUIRED"
    assert result["manual_review_required"] is True
    assert result["risk_level"] == "high"


def test_compliance_strict_mode_blocks_unknown_rights_and_allows_verified():
    config = {"compliance": {"require_verified_rights": True}}
    with pytest.raises(PermissionError, match="BLOCKED_RIGHTS"):
        assert_render_allowed(config, "football", {})
    result = assert_render_allowed(config, "football", {}, {"rights_status": "LICENSED"})
    assert result["decision"] == "ALLOW"
    assert result["risk_level"] == "medium"
