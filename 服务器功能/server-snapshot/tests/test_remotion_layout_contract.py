from pathlib import Path


TEMPLATE = Path("src/jaguartv_factory/remotion_template/src/index.tsx")


def test_generic_uses_external_bottom_banner_without_corner_overlays():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "GenericContentLayout" in source
    assert "imgBottomBanner" in source
    assert "CornerOverlays" not in source
    assert "DesignCopyOverlay" not in source


def test_bottom_banner_is_generic_only_and_fb_stays_clean():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert 'p.variant === "通用版"' in source
    assert "genericBannerHeight" in source
    assert "objectFit: \"contain\"" in source
    assert "FB版" in source


def test_endcard_remains_a_single_sequence_after_content():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert source.count("from={contentFrames}") == 1
    assert source.count("imgEndcard") >= 2
