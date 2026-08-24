from pathlib import Path

import yaml

from jaguartv_factory.trends import fetch_rss_trending_keywords, run_trends_job


class FakeTrendReq:
    def __init__(self, *args, **kwargs):
        self.term = ""

    def build_payload(self, kw_list, **kwargs):
        self.term = kw_list[0]

    def interest_over_time(self):
        return {}

    def related_queries(self):
        return {
            self.term: {
                "rising": {
                    "query": ["flamengo hoje", "brasileirao tabela", "arrascaeta"],
                }
            }
        }


def test_google_trends_job_syncs_hot_keywords_to_runtime_library(tmp_path: Path):
    keyword_file = tmp_path / "config" / "keywords.demo.yaml"
    keyword_file.parent.mkdir()
    keyword_file.write_text("football:\n  terms:\n    pt: [brasileirão]\n", encoding="utf-8")
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "sources": {"keywords_file": "config/keywords.demo.yaml"},
        "trends": {
            "keywords": ["futebol Brasil"],
            "geo": "BR",
            "top_n": 2,
            "source": "google_trends",
            "sync_keywords_group": "google_trends_br_daily",
            "runtime_keywords_file": "workspace/runtime/keywords.trends.yaml",
            "schedule_timezone": "America/Sao_Paulo",
        },
    }

    original = keyword_file.read_text(encoding="utf-8")
    from unittest.mock import patch

    with patch(
        "jaguartv_factory.trends._fetch_rss_trending_keywords_with_source",
        return_value=(["brasileirao tabela", "copa do brasil"], "scrapling_google_trends_rss"),
    ):
        result = run_trends_job(config, client_factory=FakeTrendReq)
    runtime_file = tmp_path / "workspace" / "runtime" / "keywords.trends.yaml"
    data = yaml.safe_load(runtime_file.read_text(encoding="utf-8"))

    assert result["status"] == "updated"
    assert result["count"] == 2
    assert keyword_file.read_text(encoding="utf-8") == original
    assert data["google_trends_br_daily"]["terms"]["pt"] == ["flamengo hoje", "brasileirao tabela"]


def test_google_trends_job_combines_pytrends_and_scrapling(tmp_path: Path, monkeypatch):
    config = {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "trends": {
            "keywords": ["futebol Brasil"],
            "top_n": 4,
            "runtime_keywords_file": "workspace/runtime/keywords.trends.yaml",
        },
    }
    monkeypatch.setattr(
        "jaguartv_factory.trends._fetch_rss_trending_keywords_with_source",
        lambda _config: (["brasileirao tabela", "copa do brasil"], "scrapling_google_trends_rss"),
    )

    result = run_trends_job(config, client_factory=FakeTrendReq)

    assert result["status"] == "updated"
    assert result["count"] == 4
    assert result["source"] == "pytrends+scrapling_google_trends_rss"
    assert result["keywords"] == [
        "flamengo hoje", "brasileirao tabela", "arrascaeta", "copa do brasil"
    ]


def test_scrapling_runtime_is_used_for_google_trends_rss(tmp_path: Path, monkeypatch):
    python = tmp_path / "workspace" / "tool_venvs" / "scrapling" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return type("Result", (), {"returncode": 0, "stdout": '["flamengo hoje", "brasileirao"]', "stderr": ""})()

    monkeypatch.setattr("jaguartv_factory.trends.subprocess.run", fake_run)

    values = fetch_rss_trending_keywords({"_root": str(tmp_path), "trends": {"geo": "BR"}})

    assert values == ["flamengo hoje", "brasileirao"]
    assert calls[0][0] == str(python)
