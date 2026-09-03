from pathlib import Path


TEMPLATE = Path("src/jaguartv_factory/remotion_template/src/index.tsx")


def test_generic_uses_top_brand_banner_without_legacy_bottom_banner():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "GenericContentLayout" in source
    assert "BrandBannerOverlay" in source
    assert "brandBannerRect" in source
    assert "imgBottomBanner" not in source
    assert "bottomBannerAspectRatio" not in source
    assert "CornerOverlays" not in source
    assert "DesignCopyOverlay" not in source


def test_single_generic_composition_has_no_fb_variant():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "objectFit: \"contain\"" in source
    assert "FB版" not in source


def test_cta_remains_a_single_sequence_after_content_and_supports_video():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert source.count("from={contentFrames}") == 1
    assert "ctaType === \"video\"" in source
    assert "ctaSeconds" in source
    assert "imgEndcard" not in source


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
