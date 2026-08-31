import json
from pathlib import Path

from jaguartv_factory.original_uploader import upload_original


class FakeResponse:
    status_code = 201

    def raise_for_status(self):
        return None

    def json(self):
        return {"id": "server-item", "status_id": "PENDING_REVIEW"}


class FakeSession:
    def __init__(self):
        self.call = None

    def post(self, url, **kwargs):
        self.call = (url, kwargs)
        return FakeResponse()


def test_daily_uploader_uses_existing_upload_token_contract_and_stable_request_id(tmp_path: Path):
    video = tmp_path / "safe match.mp4"
    video.write_bytes(b"mp4 fixture")
    metadata = {
        "category": "pre_match_prediction",
        "match_name": "Palmeiras x Santos",
        "match_date": "2026-09-03",
        "match_time_sao_paulo": "2026-09-03T21:30:00-03:00",
        "channels": ["Premiere"],
        "match_info": {},
        "social_sources": [],
    }
    session = FakeSession()

    result = upload_original(
        video,
        metadata,
        base_url="https://factory.example.test",
        upload_token="secret-value",
        session=session,
    )

    assert result["status_id"] == "PENDING_REVIEW"
    url, request = session.call
    assert url == "https://factory.example.test/api/originals/import"
    assert request["params"] == {"filename": "safe match.mp4"}
    assert request["headers"]["X-Upload-Token"] == "secret-value"
    assert request["headers"]["X-Request-ID"].startswith("daily-original-")
    assert request["headers"]["X-Original-Batch-Size"] == "1"
    encoded = request["headers"]["X-Original-Metadata"]
    assert "secret-value" not in encoded
    assert json.loads(__import__("base64").urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))) == metadata
