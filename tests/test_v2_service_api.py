import http.client
import json
import threading
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

import foglight_server as server
from foglight_core.models import (
    Certainty,
    ChangeType,
    EventKind,
    Incident,
    Severity,
    Status,
    Urgency,
)
from foglight_core.providers.canonical import CORE_CANONICAL_ADAPTERS, normalize_provider
from foglight_core.scheduler import FetchResult, ProviderJob, ProviderScheduler
from foglight_core.service import FoglightService, QueryError
from foglight_core.storage import ObservationStore

ROOT = Path(__file__).parents[1]
CATALOG = json.loads(
    (ROOT / "tests" / "fixtures" / "v2" / "core_providers.json").read_text(
        encoding="utf-8"
    )
)
OVERVIEW_PROVIDER_COUNT = len(CORE_CANONICAL_ADAPTERS)
NOW = "2026-07-10T22:00:00Z"


def body(provider_id):
    value = CATALOG[provider_id]["valid"]
    return value.encode() if isinstance(value, str) else json.dumps(value).encode()


def make_service(tmp_path):
    return FoglightService(
        ObservationStore(tmp_path / "v2.sqlite3"),
        registry_path=ROOT / "config" / "provider_registry.v1.json",
        taxonomy_path=ROOT / "config" / "data_taxonomy.v1.json",
    )


def ingest(service, provider_id):
    result = normalize_provider(provider_id, body(provider_id), ingested_at=NOW)
    return [service.ingest(item) for item in result.observations]


def test_service_bootstrap_filters_pagination_detail_changes_and_timeline(tmp_path):
    service = make_service(tmp_path)
    quake = ingest(service, "usgs_earthquakes")[0]
    weather = ingest(service, "nws_alerts")[0]
    report = ingest(service, "reliefweb_rss")[0]

    page = service.incidents(limit=2)
    assert len(page["items"]) == 2
    assert page["next_cursor"] == 2
    assert page["items"][0]["location_name"]
    second = service.incidents(limit=2, cursor=page["next_cursor"])
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    assert service.incidents(kind="earthquake")["items"][0]["incident_id"] == quake.incident_id
    assert service.incidents(lane="world_context")["items"][0]["incident_id"] == report.incident_id
    assert service.incidents(bbox="130,30,150,40")["total"] >= 1
    assert service.incidents(bbox="-10,-10,10,10")["total"] == 0

    detail = service.incident_detail(weather.incident_id)
    assert detail["observations"][0]["provider_id"] == "nws_alerts"
    assert detail["sources"][0]["attribution"] == "National Weather Service"
    assert detail["sources"][0]["source_url"].startswith("https://")
    assert service.incident_detail("missing") is None
    assert service.timeline(weather.incident_id)["items"][0]["revision"] == 1
    assert service.timeline("missing") is None

    changes = service.changes(cursor=0, limit=2)
    assert len(changes["items"]) == 2
    assert changes["items"][0]["incident"]["location_name"]
    assert changes["items"][0]["incident"]["sources"]
    following = service.changes(cursor=changes["next_cursor"])
    assert len(following["items"]) == 1
    bootstrap = service.bootstrap()
    assert bootstrap["revision_cursor"] >= following["items"][-1]["cursor"]
    assert bootstrap["last_revision_at"] == NOW
    assert bootstrap["taxonomy"]["schema_version"] == 1
    assert bootstrap["source_health"]["counts"]["pending"] == OVERVIEW_PROVIDER_COUNT
    assert "open_meteo" not in {
        item["provider_id"] for item in bootstrap["source_health"]["sources"]
    }
    search = service.search(query="earthquake")
    assert search["count"] == 1
    assert search["items"][0]["incident_id"] == quake.incident_id
    assert service.search(query="fixture coast", limit=1)["count"] <= 1
    assert service.search(query="100% missing")["items"] == []
    assert service.search(query='"" -- ++')["items"] == []
    assert service.search(query="__")["items"] == []
    lost = service.mark_source_lost("usgs_earthquakes", "2026-07-10T22:05:00Z")
    assert lost[0].change_type.value == "source_lost"
    assert service.mark_source_lost("usgs_earthquakes", "2026-07-10T22:06:00Z") == []
    restored = ingest(service, "usgs_earthquakes")[0]
    assert restored.status.value == "active"
    assert restored.change_type.value == "updated"


def test_service_validation_health_and_legacy_projection(tmp_path):
    service = make_service(tmp_path)
    ingest(service, "usgs_earthquakes")
    assert service.legacy_payload("usgs_earthquakes")["features"]
    pending = service.source_health("usgs_earthquakes")
    assert pending["status"] == "pending"
    assert pending["attribution"] == "USGS"
    assert pending["consecutive_failures"] == 0
    assert pending["next_attempt_at"] is None
    service.store.update_source_health("usgs_earthquakes", "live", NOW, latency_ms=3)
    assert service.source_health("usgs_earthquakes")["status"] == "live"
    assert service.source_health("missing") is None
    assert service.source_health("open_meteo") is None

    for kwargs in (
        {"limit": "bad"}, {"limit": 0}, {"cursor": -1},
        {"kind": "bad"}, {"lane": "bad"}, {"bbox": "bad"},
        {"bbox": "10,10,-10,-10"},
    ):
        with pytest.raises(QueryError):
            service.incidents(**kwargs)
    with pytest.raises(QueryError):
        service.changes(cursor="bad")
    for query in (None, "x", "x" * 101):
        with pytest.raises(QueryError):
            service.search(query=query)


def test_malformed_persisted_scheduler_state_fails_open(tmp_path):
    service = make_service(tmp_path)
    service.store.save_scheduler_state("usgs_earthquakes", {
        "last_success": "not-a-number",
        "last_attempt": "invalid",
        "next_attempt": 10**30,
        "circuit_until": -1,
        "consecutive_failures": "invalid",
        "etags": [],
    })
    health = service.source_health("usgs_earthquakes")
    assert health["last_success_at"] is None
    assert health["last_attempt_at"] is None
    assert health["next_attempt_at"] is None
    assert health["circuit_open_until"] is None
    assert health["consecutive_failures"] == 0
    assert health["conditional_cache_entries"] == 0

    with service.store.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE scheduler_state SET state_json='not-json' WHERE provider_id=?",
            ("usgs_earthquakes",),
        )
    assert service.store.load_scheduler_state("usgs_earthquakes") is None
    assert "usgs_earthquakes" not in service.store.scheduler_states()


def test_v2_startup_wires_context_planner_and_damaged_catalog_fails_open(
    tmp_path, monkeypatch
):
    server.configure_v2()
    monkeypatch.setenv("FOGLIGHT_V2_ENABLED", "1")
    monkeypatch.setattr(server, "APP_DIR", str(ROOT))
    monkeypatch.setattr(server, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        server, "load_coops_stations",
        lambda _path: (_ for _ in ()).throw(ValueError("damaged fixture catalog")),
    )
    monkeypatch.setattr(ProviderScheduler, "start", lambda _self: None)

    scheduler = server.start_v2_if_enabled()
    try:
        assert scheduler is server.V2_SCHEDULER
        assert server.V2_SERVICE is not None
        assert scheduler.jobs["ndbc_observations"].interval_seconds == 300
        assert scheduler.jobs["noaa_coops_water_levels"].interval_seconds == 300
        assert scheduler.context_urls("ndbc_observations") == ()
        assert scheduler.context_urls.__self__.stations == ()
    finally:
        server.configure_v2()


@pytest.fixture
def v2_server(tmp_path):
    service = make_service(tmp_path)
    ingest(service, "usgs_earthquakes")
    scheduler = SimpleNamespace(managed_provider_ids=frozenset({"usgs_earthquakes"}))
    server.configure_v2(service, scheduler)
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], service
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        server.configure_v2()


def request(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_v2_http_surface_validation_latency_size_and_v1_scheduler_reuse(v2_server, monkeypatch):
    port, service = v2_server
    incident_id = service.incidents(limit=1)["items"][0]["incident_id"]
    encoded_id = urllib.parse.quote(incident_id, safe="")
    paths = (
        "/api/v2/bootstrap",
        "/api/v2/incidents?limit=1",
        f"/api/v2/incidents/{encoded_id}",
        f"/api/v2/incidents/{encoded_id}/timeline",
        "/api/v2/changes?cursor=0&limit=10",
        "/api/v2/search?q=earthquake&limit=10",
        "/api/v2/taxonomy",
        "/api/v2/source-health",
        "/api/v2/source-health/usgs_earthquakes",
    )
    for path in paths:
        started = time.perf_counter()
        status, headers, response_body = request(port, path)
        assert status == 200, path
        assert (time.perf_counter() - started) * 1000 < 50
        assert len(response_body) < 256 * 1024
        assert headers["Content-Type"].startswith("application/json")
        json.loads(response_body)

    assert request(port, "/api/v2/incidents?limit=1000")[0] == 400
    assert request(port, "/api/v2/incidents?limti=1")[0] == 400
    assert request(port, "/api/v2/search")[0] == 400
    assert request(port, "/api/v2/search?q=x")[0] == 400
    assert request(port, "/api/v2/search?q=earthquake&unknown=1")[0] == 400
    assert request(port, "/api/v2/incidents/missing")[0] == 404
    assert request(port, "/api/v2/source-health/missing")[0] == 404
    assert request(port, "/api/v2/not-found")[0] == 404

    monkeypatch.setattr(
        server.PROVIDER_REGISTRY,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("upstream called")),
    )
    status, headers, response_body = request(port, "/api/usgs?window=day")
    assert status == 200
    assert headers["X-Foglight-Freshness"] in {"cached", "live", "stale", "error"}
    assert json.loads(response_body)["features"]
    monkeypatch.setattr(server, "MAX_JSON_RESPONSE_BYTES", 10)
    status, _headers, response_body = request(port, "/api/v2/bootstrap")
    assert status == 507
    assert json.loads(response_body)["error"] == "response exceeds local API size cap"


def test_v2_disabled_is_explicit(v2_server):
    port, _service = v2_server
    server.configure_v2()
    status, _headers, response_body = request(port, "/api/v2/bootstrap")
    assert status == 503
    assert json.loads(response_body)["error"] == "V2 service is disabled"


def test_indexed_five_thousand_incident_page_meets_local_api_budget(tmp_path):
    service = make_service(tmp_path)
    rows = []
    for index in range(5000):
        incident = Incident(
            incident_id=f"incident:earthquake:perf{index:06d}",
            kind=EventKind.EARTHQUAKE,
            headline=f"Fixture incident {index}",
            summary="Synthetic performance fixture",
            status=Status.ACTIVE,
            severity=Severity.MODERATE,
            urgency=Urgency.EXPECTED,
            certainty=Certainty.UNKNOWN,
            priority_score=index % 101,
            priority_components={"rule_version": "fixture", "lane": "hazards"},
            first_seen_at=NOW,
            last_changed_at=NOW,
            last_observed_at=NOW,
            observation_ids=(f"fixture:{index:024x}",),
            change_type=ChangeType.NEW,
            revision=1,
            geometry={"type": "Point", "coordinates": [index % 180, index % 80]},
        )
        west, south, east, north = incident.bbox
        rows.append(
            (
                incident.incident_id, incident.kind.value, incident.status.value,
                incident.priority_score, incident.first_seen_at, incident.last_changed_at,
                incident.last_observed_at, incident.revision,
                json.dumps(incident.to_dict(), separators=(",", ":")),
                "hazards", west, south, east, north,
            )
        )
    with service.store.transaction(immediate=True) as connection:
        connection.executemany(
            "INSERT INTO incidents(incident_id, kind, status, priority_score, first_seen_at, "
            "last_changed_at, last_observed_at, revision, document_json, lane, min_lon, "
            "min_lat, max_lon, max_lat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    started = time.perf_counter()
    page = service.incidents(limit=50, lane="hazards")
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert len(page["items"]) == 50
    assert page["total"] == 5000
    assert page["next_cursor"] == 50
    assert elapsed_ms < 50, f"incident page took {elapsed_ms:.1f} ms"
    assert len(json.dumps(page).encode()) < 256 * 1024

    started = time.perf_counter()
    search = service.search(query="incident 4999", limit=50)
    search_ms = (time.perf_counter() - started) * 1000
    assert [item["incident_id"] for item in search["items"]] == [
        "incident:earthquake:perf004999"
    ]
    assert search_ms < 50, f"incident search took {search_ms:.1f} ms"


def test_scheduler_to_normalizer_to_incident_to_projection_connection(tmp_path):
    service = make_service(tmp_path)
    adapter = CORE_CANONICAL_ADAPTERS["usgs_earthquakes"]
    scheduler = ProviderScheduler(
        [ProviderJob("usgs_earthquakes", adapter, 60)],
        store=service.store,
        fetcher=lambda *_args: FetchResult(200, body("usgs_earthquakes")),
        sink=service.ingest,
        source_lost=service.mark_source_lost,
        jitter=lambda: 0,
        clock=lambda: 1000,
    )
    scheduler.force_due("usgs_earthquakes", now=1000)
    assert scheduler.run_due(now=1000)[0]["observations"] == 1
    assert service.incidents(kind="earthquake")["total"] == 1
    assert service.legacy_payload("usgs_earthquakes")["features"][0]["properties"][
        "mag"
    ] == 6.2
    health = service.source_health("usgs_earthquakes")
    assert health["last_attempt_at"] == "1970-01-01T00:16:40Z"
    assert health["last_success_at"] == "1970-01-01T00:16:40Z"
    assert health["consecutive_failures"] == 0
