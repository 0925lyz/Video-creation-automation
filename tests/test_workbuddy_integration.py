import json
from pathlib import Path

import pytest

from jaguartv_factory.core import connect_db
from jaguartv_factory.mediacrawler import ingest_mediacrawler_jsonl, parse_metric
from jaguartv_factory.workbuddy_adapter import _merge_regions, _subtitle_band_regions, prepare_ocr_blurred_segment


def make_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "selection": {},
    }


def test_metric_parser_supports_chinese_and_compact_counts():
    assert parse_metric("1.2万") == 12_000
    assert parse_metric("3.5k") == 3_500
    assert parse_metric("2,100") == 2_100


def test_mediacrawler_jsonl_filters_and_deduplicates(tmp_path: Path):
    source = tmp_path / "douyin" / "jsonl" / "search_contents_2026-07-27.jsonl"
    source.parent.mkdir(parents=True)
    records = [
        {
            "aweme_id": "a1",
            "aweme_url": "https://www.douyin.com/video/a1",
            "video_download_url": "https://media.example/a1.mp4",
            "liked_count": "2.5万",
            "desc": "足球精彩进球",
        },
        {
            "aweme_id": "a2",
            "aweme_url": "https://www.douyin.com/video/a2",
            "liked_count": "10",
            "desc": "低热度样本",
        },
    ]
    source.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records), encoding="utf-8")
    config = make_config(tmp_path)
    first = ingest_mediacrawler_jsonl(config, source, min_likes=2_000)
    second = ingest_mediacrawler_jsonl(config, source, min_likes=2_000)
    assert first == {"read": 2, "inserted": 1, "duplicate": 0, "filtered": 1, "invalid": 0}
    assert second["duplicate"] == 1
    row = connect_db(config).execute("SELECT * FROM candidates").fetchone()
    metadata = json.loads(row["metadata_json"])
    assert row["platform"] == "douyin"
    assert metadata["direct_media_url"] == "https://media.example/a1.mp4"
    assert metadata["like_count"] == 25_000


def test_ocr_regions_merge_into_stable_horizontal_bands():
    regions = _merge_regions([
        (0.10, 0.70, 0.30, 0.75),
        (0.32, 0.71, 0.55, 0.76),
        (0.15, 0.20, 0.40, 0.26),
    ])
    assert len(regions) == 2
    bottom = max(regions, key=lambda item: item[1])
    assert bottom[0] == pytest.approx(0.09)
    assert bottom[2] >= 0.56


def test_ocr_subtitle_filter_drops_top_and_tiny_regions():
    regions = _subtitle_band_regions([
        [0.04, 0.10, 0.30, 0.16],
        [0.82, 0.70, 0.86, 0.74],
        [0.20, 0.72, 0.72, 0.80],
    ])
    assert regions == [[0.185, 0.712, 0.735, 0.808]]


def test_ocr_blur_uses_fallback_regions_when_detection_misses(tmp_path: Path, monkeypatch):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"video")
    output = tmp_path / "blurred.mp4"
    fallback = [[0.04, 0.70, 0.96, 0.94]]

    def fake_run(args, *, check=False):
        extracted = Path(args[-1])
        extracted.write_bytes(b"segment")
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    def fake_blur_static_regions(extracted, destination, regions, *, sigma=28):
        destination.write_bytes(b"blurred")
        assert regions == fallback
        return destination

    monkeypatch.setattr("jaguartv_factory.workbuddy_adapter._run", fake_run)
    monkeypatch.setattr("jaguartv_factory.workbuddy_adapter.detect_chinese_text_regions", lambda _path: [])
    monkeypatch.setattr("jaguartv_factory.workbuddy_adapter.blur_static_regions", fake_blur_static_regions)

    info = prepare_ocr_blurred_segment(
        media,
        output,
        start=0,
        duration=3,
        fallback_regions=fallback,
    )
    assert info["used"] is True
    assert info["regions"] == fallback
    assert info["reason"] == "fallback_regions_blurred_no_chinese_regions_detected"
