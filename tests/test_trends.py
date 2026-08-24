from pathlib import Path

import yaml

from jaguartv_factory.trends import run_trends_job


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
    result = run_trends_job(config, client_factory=FakeTrendReq)
    runtime_file = tmp_path / "workspace" / "runtime" / "keywords.trends.yaml"
    data = yaml.safe_load(runtime_file.read_text(encoding="utf-8"))

    assert result["status"] == "updated"
    assert result["count"] == 2
    assert keyword_file.read_text(encoding="utf-8") == original
    assert data["google_trends_br_daily"]["terms"]["pt"] == ["flamengo hoje", "brasileirao tabela"]
