from __future__ import annotations

import json
from pathlib import Path

from jaguartv_factory.openai_responses_bridge import (
    bridge_runtime,
    responses_output_text,
)


def test_responses_output_text_accepts_both_supported_shapes():
    assert responses_output_text({"output_text": '{"translations":[]}'}) == '{"translations":[]}'
    assert responses_output_text(
        {"output": [{"content": [{"type": "output_text", "text": "traducao"}]}]}
    ) == "traducao"


def test_bridge_runtime_rewrites_only_temporary_llm_base_url(tmp_path: Path):
    project = tmp_path / "KrillinAI"
    config_dir = project / "config"
    config_dir.mkdir(parents=True)
    original = config_dir / "config.toml"
    original.write_text(
        '[llm]\nbase_url = "https://relay.example/v1"\napi_key = "secret"\nmodel = "model"\n'
        '\n[tts]\nprovider = "edge-tts"\n',
        encoding="utf-8",
    )
    (project / "bin").mkdir()
    (project / "models").mkdir()

    with bridge_runtime(project, tmp_path / "logs") as runtime:
        rewritten = (runtime.cwd / "config" / "config.toml").read_text(encoding="utf-8")
        assert f'base_url = "http://127.0.0.1:{runtime.port}/v1"' in rewritten
        assert 'api_key = "secret"' in rewritten
        assert (runtime.cwd / "bin").is_symlink()
        assert (runtime.cwd / "models").is_symlink()

    assert original.read_text(encoding="utf-8").startswith(
        '[llm]\nbase_url = "https://relay.example/v1"'
    )


def test_bridge_converts_chat_request_to_responses_and_streams(monkeypatch, tmp_path: Path):
    project = tmp_path / "KrillinAI"
    (project / "config").mkdir(parents=True)
    (project / "config" / "config.toml").write_text(
        '[llm]\nbase_url = "https://relay.example/v1"\napi_key = "secret"\nmodel = "model"\n',
        encoding="utf-8",
    )
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"output_text": '{"translations":[{"index":1,"text":"Gol"}]}' }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("jaguartv_factory.openai_responses_bridge._post_upstream", fake_post)

    import requests

    with bridge_runtime(project, tmp_path / "logs") as runtime:
        response = requests.post(
            f"http://127.0.0.1:{runtime.port}/v1/chat/completions",
            headers={"Authorization": "Bearer secret"},
            json={
                "model": "model",
                "stream": True,
                "messages": [
                    {"role": "system", "content": "Translate subtitles."},
                    {"role": "user", "content": "Input JSON"},
                ],
            },
            timeout=5,
        )

    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    assert '"content": "{\\"translations\\"' in response.text
    assert captured["url"] == "https://relay.example/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    upstream = captured["body"]
    assert "Translate subtitles." in upstream["input"]
    assert "Input JSON" in upstream["input"]
    assert upstream["store"] is False
