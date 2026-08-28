from pathlib import Path


TEMPLATE = Path("src/jaguartv_factory/remotion_template/src/index.tsx")


def test_generic_uses_external_bottom_banner_without_corner_overlays():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "GenericContentLayout" in source
    assert "imgBottomBanner" in source
    assert "CornerOverlays" not in source
    assert "DesignCopyOverlay" not in source


def test_single_generic_composition_uses_bottom_banner():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "genericBannerHeight" in source
    assert "objectFit: \"contain\"" in source
    assert "FB版" not in source


def test_endcard_remains_a_single_sequence_after_content():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert source.count("from={contentFrames}") == 1
    assert source.count("imgEndcard") >= 2


def test_captions_have_no_background_box_by_default():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "backgroundOpacity: 0" in source
    assert "style?.backgroundOpacity ?? 0" in source


def test_captions_prefer_a_compact_low_single_line_rail():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "maxWidthRatio: 0.90" in source
    assert "fontSizeRatio: 0.028" in source
    assert "safeInsetRatio: 0.05" in source
    assert "Math.max(20" in source
    assert "rect.width * 0.90" in source
