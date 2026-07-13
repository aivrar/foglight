import json
from pathlib import Path

import pytest

import foglight_server as server
from foglight_core.providers import hazards as hazard_providers
from foglight_core.providers import legacy as legacy_providers

CATALOG = json.loads(
    (Path(__file__).parent / "fixtures" / "v1" / "provider_contracts.json").read_text(
        encoding="utf-8"
    )
)["providers"]
REGISTRY = json.loads(
    (Path(__file__).parents[1] / "config" / "provider_registry.v1.json").read_text(
        encoding="utf-8"
    )
)["providers"]


def _payload(provider_id, variant="valid"):
    value = CATALOG[provider_id][variant]
    if isinstance(value, str):
        return value.encode()
    return json.dumps(value).encode()


@pytest.mark.parametrize("provider_id", sorted(CATALOG))
def test_every_provider_has_three_sanitized_contract_variants(provider_id):
    fixture = CATALOG[provider_id]
    assert fixture["format"] in {"json", "geojson", "xml", "csv", "sse"}
    assert {"valid", "empty", "malformed"} <= fixture.keys()


def test_provider_registry_and_contract_catalog_are_complete_and_aligned():
    registry_by_id = {provider["id"]: provider for provider in REGISTRY}
    # The versioned registry is shared by the legacy proxy surface and the
    # additive canonical scheduler. Legacy fixtures cover only legacy callable
    # adapters; V2-only canonical providers must not need a fake V1 route.
    assert CATALOG.keys() <= registry_by_id.keys()
    assert all(provider["decision"] for provider in REGISTRY)
    assert all(provider["terms"] for provider in REGISTRY)
    assert all(provider["attribution"] for provider in REGISTRY)
    assert all(provider["cadence_seconds"] >= 10 for provider in REGISTRY)
    assert all(
        provider["decision"] == "approved"
        for provider in REGISTRY
        if provider["required"]
    )
    assert set(server.PROVIDER_REGISTRY.ids()) == set(CATALOG)
    assert hazard_providers.MAX_RSS_BYTES == server.MAX_RSS_BYTES


@pytest.mark.parametrize(
    ("call", "expected_host", "expected_type"),
    [
        (lambda: server.usgs_quakes(), "earthquake.usgs.gov", "application/geo+json"),
        (server.nws_active, "api.weather.gov", "application/geo+json"),
        (server.github_events, "api.github.com", "application/json"),
        (server.iss_now, "api.open-notify.org", "application/json"),
        (server.crypto_prices, "api.coinpaprika.com", "application/json"),
        (server.forex_latest, "api.frankfurter.app", "application/json"),
        (server.sec_filings, "www.sec.gov", "application/atom+xml"),
        (server.hn_top, "hacker-news.firebaseio.com", "application/json"),
        (lambda: server.hn_item("123"), "hacker-news.firebaseio.com", "application/json"),
        (server.nhc_storms, "www.nhc.noaa.gov", "application/json"),
        (server.space_weather, "services.swpc.noaa.gov", "application/json"),
        (lambda: server.adsb_flights(35, 139), "api.adsb.lol", "application/json"),
        (lambda: server.openmeteo_current(35, 139), "api.open-meteo.com", "application/json"),
    ],
)
def test_pass_through_provider_contract(monkeypatch, call, expected_host, expected_type):
    captured = {}

    def fake_fetch(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        return b"{}", kwargs["ctype_hint"], 0, "live"

    monkeypatch.setattr(server, "fetch", fake_fetch)
    body, ctype, _age, fresh = call()
    assert expected_host in captured["url"]
    assert captured["kwargs"]["ctype_hint"] == expected_type
    assert (body, ctype, fresh) == (b"{}", expected_type, "live")


def test_rss_parser_contract_handles_rss_atom_empty_and_malformed():
    rss = server._parse_rss_items(_payload("rss_proxy"))
    atom = server._parse_rss_items(_payload("reddit_popular"))
    assert rss[0]["title"] == "Fixture headline"
    assert rss[0]["ts"] > 0
    assert atom[0]["title"] == "Fixture post"
    assert atom[0]["link"].startswith("https://")
    assert server._parse_rss_items(_payload("rss_proxy", "empty")) == []
    assert server._parse_rss_items(_payload("rss_proxy", "malformed")) == []


@pytest.mark.parametrize(
    ("call", "provider_id", "output_key"),
    [
        (server.reddit_popular, "reddit_popular", "items"),
        (server.reliefweb_rss, "reliefweb_rss", "articles"),
        (server.conflict_aggregate, "conflict_rss", "articles"),
        (server.defense_wire, "defense_rss", "articles"),
    ],
)
def test_rss_adapter_contracts(monkeypatch, call, provider_id, output_key):
    monkeypatch.setattr(
        server,
        "fetch",
        lambda *_args, **_kwargs: (_payload(provider_id), "application/xml", 0, "live"),
    )
    body, ctype, _age, fresh = call()
    parsed = json.loads(body)
    assert ctype == "application/json"
    assert fresh == "live"
    assert parsed[output_key]


def test_mempool_contract_survives_one_malformed_subresponse(monkeypatch):
    def fake_fetch(url, **_kwargs):
        if "/mempool" in url and "/api/mempool" not in url:
            return b"not-json", "application/json", 0, "error"
        return b"{}", "application/json", 0, "live"

    monkeypatch.setattr(server, "fetch", fake_fetch)
    body, ctype, _age, fresh = server.mempool_summary()
    parsed = json.loads(body)
    assert ctype == "application/json"
    assert set(parsed) == {"fees", "mempool", "blocks", "difficulty", "_freshness"}
    assert fresh == "stale"


def test_eonet_contract_normalizes_point_and_rejects_malformed(monkeypatch):
    monkeypatch.setattr(
        server,
        "fetch",
        lambda *_args, **_kwargs: (_payload("nasa_eonet"), "application/json", 0, "live"),
    )
    body, ctype, _age, fresh = server.eonet_events()
    item = json.loads(body)["events"][0]
    assert (item["lat"], item["lon"], item["cats"]) == (39.0, -105.0, ["wildfires"])
    assert (ctype, fresh) == ("application/json", "live")

    monkeypatch.setattr(
        server,
        "fetch",
        lambda *_args, **_kwargs: (b"not-json", "application/json", 0, "error"),
    )
    malformed, _, _, malformed_fresh = server.eonet_events()
    assert malformed == b"not-json"
    assert malformed_fresh == "error"


@pytest.mark.parametrize(
    ("call", "provider_id", "expected_key"),
    [
        (server.gdacs_disasters, "gdacs", "items"),
        (server.usgs_volcanoes_proper, "smithsonian_volcano", "items"),
        (server.tsunami_alerts, "noaa_tsunami", "items"),
    ],
)
def test_xml_adapter_contracts_and_malformed_fallback(
    monkeypatch, call, provider_id, expected_key
):
    payload = _payload(provider_id)
    monkeypatch.setattr(
        server, "fetch", lambda *_args, **_kwargs: (payload, "application/xml", 0, "live")
    )
    body, ctype, _age, _fresh = call()
    assert json.loads(body)[expected_key]
    assert ctype == "application/json"

    malformed = _payload(provider_id, "malformed")
    monkeypatch.setattr(
        server,
        "fetch",
        lambda *_args, **_kwargs: (malformed, "application/xml", 0, "live"),
    )
    malformed_body, malformed_type, _age, _fresh = call()
    assert json.loads(malformed_body)[expected_key] == []
    assert malformed_type == "application/json"


def test_firms_contract_parses_valid_and_skips_malformed_rows(monkeypatch):
    payload = _payload("nasa_firms")
    monkeypatch.setattr(
        server, "fetch", lambda *_args, **_kwargs: (payload, "text/csv", 0, "live")
    )
    body, ctype, _age, fresh = server.nasa_firms("sanitized-fixture-key")
    item = json.loads(body)["items"][0]
    assert (item["lat"], item["lon"], item["frp"]) == (35.0, 139.0, 12.5)
    assert (ctype, fresh) == ("application/json", "live")

    monkeypatch.setattr(
        server,
        "fetch",
        lambda *_args, **_kwargs: (
            _payload("nasa_firms", "malformed"),
            "text/csv",
            0,
            "live",
        ),
    )
    malformed, _, _, _ = server.nasa_firms("sanitized-fixture-key")
    assert json.loads(malformed)["items"] == []


def test_commodities_contract_calculates_change_and_drops_bad_quotes(monkeypatch):
    valid = _payload("yahoo_finance")
    monkeypatch.setattr(
        server, "fetch", lambda *_args, **_kwargs: (valid, "application/json", 0, "live")
    )
    body, ctype, _age, fresh = server.commodities()
    parsed = json.loads(body)["items"]
    assert len(parsed) == len(server.COMMODITIES)
    assert parsed["GOLD"]["close"] == 101.5
    assert parsed["GOLD"]["chg"] == pytest.approx(1.5)
    assert (ctype, fresh) == ("application/json", "live")


def test_input_contracts_reject_invalid_values_without_fetch(monkeypatch):
    monkeypatch.setattr(server, "fetch", lambda *_args, **_kwargs: pytest.fail("unexpected fetch"))
    assert json.loads(server.hn_item("1 OR 1=1")[0]) == {"error": "bad id"}
    assert json.loads(server.adsb_flights("north", 20)[0]) == {"error": "bad coords"}
    assert json.loads(server.openmeteo_current(10, "east")[0]) == {"error": "bad lat/lon"}
    assert json.loads(server.nasa_firms("")[0])["error"] == "key required"


def test_conflict_hotspot_derivation_scores_matches_and_survives_bad_aggregate(monkeypatch):
    article = {
        "title": "Ukraine fixture update",
        "summary": "Kyiv",
        "src": "FIXTURE",
        "ts": 1783700000,
        "link": "https://example.test/story",
    }
    monkeypatch.setattr(
        legacy_providers,
        "conflict_aggregate",
        lambda: (
            json.dumps({"articles": [article]}).encode(),
            "application/json",
            0,
            "live",
        ),
    )
    body, ctype, _age, fresh = server.conflict_hotspots()
    features = json.loads(body)["features"]
    assert features[0]["properties"]["name"] == "Ukraine"
    assert features[0]["properties"]["count"] == 1
    assert features[0]["properties"]["recent"][0]["src"] == "FIXTURE"
    assert (ctype, fresh) == ("application/geo+json", "live")

    monkeypatch.setattr(
        legacy_providers,
        "conflict_aggregate",
        lambda: (b"bad-json", "application/json", 0, "stale"),
    )
    malformed, _, _, malformed_fresh = server.conflict_hotspots()
    assert json.loads(malformed)["features"] == []
    assert malformed_fresh == "stale"
