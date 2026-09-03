from pathlib import Path
from types import SimpleNamespace

from jaguartv_factory.source_media import (
    contains_chinese,
    demucs_backing_track,
    detect_source_caption_regions,
    localized_backing_required,
    parse_tesseract_tsv,
)


def test_caption_scan_identifies_subtitle_bands_and_chinese_text():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t100\t500\t250\t50\t91\t这是\n"
        "5\t1\t1\t1\t1\t2\t360\t500\t300\t50\t88\t中文字幕\n"
        "5\t1\t2\t1\t1\t1\t10\t10\t20\t15\t95\t角标\n"
    )

    regions, has_chinese = parse_tesseract_tsv(tsv, width=1000, height=1000)

    assert has_chinese is True
    assert regions == [[0.09, 0.49, 0.67, 0.56]]


def test_localized_backing_requires_chinese_voice_and_screen_subtitles():
    assert contains_chinese("这是中文人声") is True
    assert localized_backing_required("这是中文人声", True) is True
    assert localized_backing_required("这是中文人声", False) is False
    assert localized_backing_required("fala em português", True) is False


def test_caption_scan_uses_one_chinese_fallback_probe(tmp_path: Path, monkeypatch):
    calls = []
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t100\t500\t600\t50\t90\tlegenda\n"
    )

    def fake_run(args, **kwargs):
        calls.append([str(value) for value in args])
        if args[0] == "ffmpeg":
            from PIL import Image
            Image.new("RGB", (1000, 1000), "black").save(args[-1])
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "tsv" in args:
            return SimpleNamespace(returncode=0, stdout=tsv, stderr="")
        return SimpleNamespace(returncode=0, stdout="这是中文字幕", stderr="")

    monkeypatch.setattr("jaguartv_factory.source_media.require_binary", lambda _: "ffmpeg")
    monkeypatch.setattr("jaguartv_factory.source_media.shutil.which", lambda _: "tesseract")
    monkeypatch.setattr("jaguartv_factory.source_media.subprocess.run", fake_run)

    result = detect_source_caption_regions(tmp_path / "source.mp4", start=0, duration=10, sample_count=3)

    assert result["has_chinese_text"] is True
    assert sum("--psm" in call and call[call.index("--psm") + 1] == "6" for call in calls) == 1


def test_demucs_extracts_only_the_no_vocals_track(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    calls = []

    def fake_run(args, **kwargs):
        calls.append([str(value) for value in args])
        if "-m" in args and "demucs" in args:
            expected = tmp_path / "audio" / "demucs" / "htdemucs" / "source_audio" / "no_vocals.wav"
            expected.parent.mkdir(parents=True)
            expected.write_bytes(b"backing")
        else:
            Path(args[-1]).write_bytes(b"wav")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("jaguartv_factory.source_media.subprocess.run", fake_run)
    output = demucs_backing_track(source, tmp_path / "audio", start=3, duration=20)

    assert output.read_bytes() == b"backing"
    assert any("--two-stems=vocals" in call for call in calls)
    assert any("-ss" in call and "-t" in call for call in calls)
