from pathlib import Path

from jaguartv_factory.core import load_config, tts_rate_percent


def test_pipeline_enables_pyvideotrans_adapter_by_default():
    config = load_config(Path("config/pipeline.yaml"))

    assert config["edit"]["ocr_backend"] == "auto"
    assert config["localization"]["voice_enabled"] is True
    assert config["localization"]["pyvideotrans"]["enabled"] is True


def test_tts_rate_percent_accepts_multiplier_and_percent():
    assert tts_rate_percent({"localization": {"tts_rate": 1.08}}) == "+8%"
    assert tts_rate_percent({"localization": {"tts_rate": "+12%"}}) == "+12%"
