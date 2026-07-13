import json
from pathlib import Path

from scripts import check_live_sources

ROOT = Path(__file__).parents[1]


class FakeHeaders:
    @staticmethod
    def get_content_type():
        return "application/geo+json"


class FakeResponse:
    status = 200
    headers = FakeHeaders()

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return self.body


def test_live_diagnostic_catalog_exactly_tracks_the_provider_registry():
    registry = json.loads(
        (ROOT / "config" / "provider_registry.v1.json").read_text(encoding="utf-8")
    )
    assert {item["id"] for item in registry["providers"]} == (
        set(check_live_sources.URLS) | set(check_live_sources.SKIPPED)
    )


def test_live_diagnostic_can_report_canonical_drift_without_payload_values(monkeypatch):
    catalog = json.loads(
        (
            Path(__file__).parent / "fixtures" / "v2" / "core_providers.json"
        ).read_text(encoding="utf-8")
    )
    fixture = catalog["usgs_earthquakes"]["valid"]
    fixture["features"][0]["future_field"] = "a secret payload value"
    body = json.dumps(fixture).encode()
    monkeypatch.setattr(
        check_live_sources.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(body),
    )

    result = check_live_sources.check(
        ("usgs_earthquakes", "https://example.test/feed"),
        1,
        normalize_core=True,
    )

    assert result["ok"] is True
    assert result["observations"] == 1
    assert result["drift"] == [
        {
            "code": "unknown_fields",
            "record_id": "us7000fixture",
            "fields": ["future_field"],
        }
    ]
    assert "secret payload value" not in json.dumps(result)
