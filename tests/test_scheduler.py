import json
import threading
import time
from pathlib import Path

import pytest

from foglight_core.providers.canonical import CORE_CANONICAL_ADAPTERS
from foglight_core.scheduler import (
    FetchResult,
    ProviderJob,
    ProviderScheduler,
    ProviderState,
    jobs_from_registry,
)
from foglight_core.storage import ObservationStore

ROOT = Path(__file__).parents[1]
CATALOG = json.loads(
    (ROOT / "tests" / "fixtures" / "v2" / "core_providers.json").read_text(
        encoding="utf-8"
    )
)


def fixture_body(provider_id):
    value = CATALOG[provider_id]["valid"]
    return value.encode() if isinstance(value, str) else json.dumps(value).encode()


def scheduler_store(tmp_path, *provider_ids):
    store = ObservationStore(tmp_path / "scheduler.sqlite3")
    for provider_id in provider_ids:
        store.register_provider(provider_id, {})
    return store


def job(provider_id, interval=60):
    return ProviderJob(provider_id, CORE_CANONICAL_ADAPTERS[provider_id], interval)


def test_registry_builds_every_keyless_core_job_with_bounded_policy():
    jobs = jobs_from_registry(ROOT / "config" / "provider_registry.v1.json")
    assert {item.provider_id for item in jobs} == set(CORE_CANONICAL_ADAPTERS)
    assert all(item.interval_seconds >= 10 for item in jobs)
    assert all(item.timeout_seconds <= 60 for item in jobs)
    assert all(item.max_bytes <= 10 * 1024 * 1024 for item in jobs)
    fireball = next(item for item in jobs if item.provider_id == "nasa_jpl_fireballs")
    assert fireball.interval_seconds == 6 * 60 * 60
    assert fireball.adapter.source_urls == (
        "https://ssd-api.jpl.nasa.gov/fireball.api?limit=20",
    )
    nws = next(item for item in jobs if item.provider_id == "nws_alerts")
    assert nws.max_bytes == 5 * 1024 * 1024
    fema = next(item for item in jobs if item.provider_id == "openfema_declarations")
    assert fema.adapter.source_urls == (
        "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?"
        "$orderby=declarationDate%20desc&$top=100",
    )
    with pytest.raises(ValueError, match="interval"):
        ProviderJob("usgs_earthquakes", CORE_CANONICAL_ADAPTERS["usgs_earthquakes"], 1)
    with pytest.raises(ValueError, match="provider and adapter"):
        ProviderJob("nws_alerts", CORE_CANONICAL_ADAPTERS["usgs_earthquakes"], 60)
    with pytest.raises(ValueError, match="timeout"):
        ProviderJob(
            "usgs_earthquakes", CORE_CANONICAL_ADAPTERS["usgs_earthquakes"],
            60, timeout_seconds=0,
        )
    with pytest.raises(ValueError, match="body cap"):
        ProviderJob(
            "usgs_earthquakes", CORE_CANONICAL_ADAPTERS["usgs_earthquakes"],
            60, max_bytes=10,
        )


def test_new_profile_warmup_is_bounded_to_five_seconds(tmp_path):
    store = scheduler_store(tmp_path, "usgs_earthquakes")
    scheduler = ProviderScheduler(
        [job("usgs_earthquakes", interval=600)],
        store=store,
        fetcher=lambda *_args: FetchResult(304),
        sink=lambda _item: None,
        jitter=lambda: 1,
        clock=lambda: 1000,
    )
    assert scheduler.states["usgs_earthquakes"].next_attempt == 1005


def test_success_conditional_requests_intervals_stale_and_304(tmp_path):
    store = scheduler_store(tmp_path, "usgs_earthquakes")
    calls = []
    responses = [
        FetchResult(
            200,
            fixture_body("usgs_earthquakes"),
            {"etag": '"fixture"', "last-modified": "Fri, 10 Jul 2026 20:00:00 GMT"},
            "stale",
        ),
        FetchResult(304),
    ]

    def fetcher(url, headers, timeout, max_bytes):
        calls.append((url, headers, timeout, max_bytes))
        return responses.pop(0)

    sink = []
    scheduler = ProviderScheduler(
        [job("usgs_earthquakes")], store=store, fetcher=fetcher,
        sink=sink.append, jitter=lambda: 0, clock=lambda: 1000,
    )
    scheduler.force_due("usgs_earthquakes", now=1000)
    result = scheduler.run_due(now=1000)[0]
    assert result["status"] == "stale"
    assert len(sink) == 1
    assert scheduler.run_due(now=1059) == []
    assert scheduler.run_due(now=1060)[0]["status"] == "cached"
    assert calls[1][1] == {
        "If-None-Match": '"fixture"',
        "If-Modified-Since": "Fri, 10 Jul 2026 20:00:00 GMT",
    }
    assert store.source_health("usgs_earthquakes")[0]["status"] == "cached"


def test_documented_204_and_empty_geojson_are_successful_no_data_batches(tmp_path):
    store = scheduler_store(tmp_path, "noaa_aviation_weather")
    responses = [
        FetchResult(204, freshness="live"),
        FetchResult(200, b'{"type":"FeatureCollection","features":[]}'),
    ]
    scheduler = ProviderScheduler(
        [job("noaa_aviation_weather", interval=300)],
        store=store,
        fetcher=lambda *_args: responses.pop(0),
        sink=lambda _item: pytest.fail("a no-data batch must not emit an observation"),
        jitter=lambda: 0,
        clock=lambda: 1000,
    )
    scheduler.force_due("noaa_aviation_weather", now=1000)
    first = scheduler.run_due(now=1000)[0]
    assert first == {
        "provider_id": "noaa_aviation_weather", "ok": True, "status": "live",
        "observations": 0, "diagnostics": 0, "next_attempt": 1300,
    }
    scheduler.force_due("noaa_aviation_weather", now=1300)
    second = scheduler.run_due(now=1300)[0]
    assert second["ok"] is True
    assert second["status"] == "live"
    assert scheduler.states["noaa_aviation_weather"].consecutive_failures == 0


def test_multi_url_validators_commit_only_after_complete_provider_batch(tmp_path):
    store = scheduler_store(tmp_path, "noaa_tsunami")
    adapter = CORE_CANONICAL_ADAPTERS["noaa_tsunami"]
    attempts = []
    failed_once = False

    def fetcher(url, headers, _timeout, _max_bytes):
        nonlocal failed_once
        attempts.append((url, dict(headers)))
        if url == adapter.source_urls[1] and not failed_once:
            failed_once = True
            return FetchResult(500)
        return FetchResult(
            200, fixture_body("noaa_tsunami"), {"ETag": f'"{url[-8:]}"'}
        )

    scheduler = ProviderScheduler(
        [job("noaa_tsunami")], store=store, fetcher=fetcher,
        sink=lambda _item: None, jitter=lambda: 0, clock=lambda: 1000,
    )
    scheduler.force_due("noaa_tsunami", now=1000)
    assert scheduler.run_due(now=1000)[0]["ok"] is False
    assert scheduler.states["noaa_tsunami"].etags == {}
    scheduler.force_due("noaa_tsunami", now=1060)
    assert scheduler.run_due(now=1060)[0]["ok"] is True
    assert attempts[2][0] == adapter.source_urls[0]
    assert attempts[2][1] == {}
    assert len(scheduler.states["noaa_tsunami"].etags) == 2


def test_contextual_provider_idles_without_local_context_and_clears_validators(tmp_path):
    provider_id = "ndbc_observations"
    store = scheduler_store(tmp_path, provider_id)
    scheduler = ProviderScheduler(
        [job(provider_id, interval=300)], store=store,
        fetcher=lambda *_args: pytest.fail("idle providers must not contact NOAA"),
        sink=lambda _item: pytest.fail("idle providers must not emit observations"),
        context_urls=lambda _provider_id: (), jitter=lambda: 0, clock=lambda: 1000,
    )
    state = scheduler.states[provider_id]
    state.last_success = 900
    state.etags = {"https://www.ndbc.noaa.gov/old": '"old"'}
    state.last_modified = {"https://www.ndbc.noaa.gov/old": "old"}
    scheduler.force_due(provider_id, now=1000)

    assert scheduler.run_due(now=1000)[0] == {
        "provider_id": provider_id, "ok": True, "status": "idle",
        "observations": 0, "diagnostics": 0, "next_attempt": 1300,
    }
    assert state.last_success == 900
    assert state.etags == state.last_modified == {}
    assert store.source_health(provider_id)[0]["status"] == "idle"


@pytest.mark.parametrize("urls", [
    "not-a-sequence",
    ["http://www.ndbc.noaa.gov/rss/ndbc_obs_search.php"],
    ["https://example.com/rss"],
    ["https://user@www.ndbc.noaa.gov/rss"],
    ["https://www.ndbc.noaa.gov/rss#fragment"],
    ["x" * 2049],
    ["https://www.ndbc.noaa.gov/rss"] * 7,
])
def test_contextual_provider_rejects_unbounded_or_untrusted_urls(tmp_path, urls):
    provider_id = "ndbc_observations"
    store = scheduler_store(tmp_path, provider_id)
    calls = []
    scheduler = ProviderScheduler(
        [job(provider_id, interval=300)], store=store,
        fetcher=lambda *args: calls.append(args), sink=lambda _item: None,
        context_urls=lambda _provider_id: urls, jitter=lambda: 0, clock=lambda: 1000,
    )
    scheduler.force_due(provider_id, now=1000)

    result = scheduler.run_due(now=1000)[0]
    assert result["ok"] is False
    assert store.source_health(provider_id)[0]["detail"] == "fetch_error"
    assert calls == []


def test_contextual_batch_deduplicates_observations_and_prunes_rotated_validators(tmp_path):
    provider_id = "ndbc_observations"
    store = scheduler_store(tmp_path, provider_id)
    first_urls = (
        "https://www.ndbc.noaa.gov/rss/ndbc_obs_search.php?lat=37N&lon=122W&radius=50",
        "https://www.ndbc.noaa.gov/rss/ndbc_obs_search.php?lat=38N&lon=123W&radius=50",
    )
    current_urls = [first_urls]
    sinks = []

    def fetcher(url, _headers, _timeout, _max_bytes):
        return FetchResult(200, fixture_body(provider_id), {"ETag": f'"{url[-2:]}"'})

    scheduler = ProviderScheduler(
        [job(provider_id, interval=300)], store=store, fetcher=fetcher,
        sink=sinks.append, context_urls=lambda _provider_id: current_urls[0],
        jitter=lambda: 0, clock=lambda: 1000,
    )
    scheduler.force_due(provider_id, now=1000)
    assert scheduler.run_due(now=1000)[0]["observations"] == 1
    assert len(sinks) == 1
    assert set(scheduler.states[provider_id].etags) == set(first_urls)

    current_urls[0] = (first_urls[1],)
    scheduler.force_due(provider_id, now=1300)
    assert scheduler.run_due(now=1300)[0]["ok"] is True
    assert set(scheduler.states[provider_id].etags) == {first_urls[1]}


def test_concurrency_is_bounded_and_provider_is_never_double_inflight(tmp_path):
    providers = ("usgs_earthquakes", "nws_alerts", "nhc_storms")
    store = scheduler_store(tmp_path, *providers)
    active = 0
    maximum = 0
    lock = threading.Lock()

    def fetcher(url, _headers, _timeout, _max_bytes):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        provider_id = next(
            key for key, adapter in CORE_CANONICAL_ADAPTERS.items() if url in adapter.source_urls
        )
        with lock:
            active -= 1
        return FetchResult(200, fixture_body(provider_id))

    scheduler = ProviderScheduler(
        [job(item) for item in providers], store=store, fetcher=fetcher,
        sink=lambda _item: None, max_workers=2, jitter=lambda: 0, clock=lambda: 1000,
    )
    for provider in providers:
        scheduler.force_due(provider, now=1000)
    results = scheduler.run_due(now=1000)
    assert len(results) == 3
    assert maximum == 2
    assert not scheduler._inflight

    gate = threading.Event()

    def blocking_fetcher(*_args):
        gate.wait(1)
        return FetchResult(304)

    scheduler.fetcher = blocking_fetcher
    scheduler.force_due("usgs_earthquakes", now=2000)
    thread = threading.Thread(target=lambda: scheduler.run_due(now=2000))
    thread.start()
    while "usgs_earthquakes" not in scheduler._inflight:
        time.sleep(0.005)
    assert scheduler.run_due(now=2000) == []
    gate.set()
    thread.join(1)


def test_429_timeout_malformed_backoff_circuit_and_retry_after(tmp_path):
    store = scheduler_store(tmp_path, "usgs_earthquakes")
    responses = [
        FetchResult(429, headers={"retry-after": "120"}),
        TimeoutError(),
        FetchResult(200, b"{bad-json"),
    ]

    def fetcher(*_args):
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    lost = []
    scheduler = ProviderScheduler(
        [job("usgs_earthquakes")], store=store, fetcher=fetcher,
        sink=lambda _item: None, source_lost=lambda provider, at: lost.append((provider, at)),
        jitter=lambda: 0, clock=lambda: 1000,
    )
    for index, now in enumerate((1000, 1120, 1240), start=1):
        scheduler.force_due("usgs_earthquakes", now=now)
        result = scheduler.run_due(now=now)[0]
        assert result["ok"] is False
        assert scheduler.states["usgs_earthquakes"].consecutive_failures == index
    state = scheduler.states["usgs_earthquakes"]
    assert state.circuit_until >= 1540
    assert lost == [("usgs_earthquakes", "1970-01-01T00:20:40Z")]
    assert scheduler.run_due(now=state.circuit_until - 1) == []
    assert store.source_health("usgs_earthquakes")[0]["detail"] == "malformed_body"


def test_restart_clock_shift_state_validation_and_background_lifecycle(tmp_path):
    store = scheduler_store(tmp_path, "usgs_earthquakes")
    store.save_scheduler_state(
        "usgs_earthquakes",
        ProviderState(next_attempt=99_999, last_attempt=99_000).to_dict(),
    )
    scheduler = ProviderScheduler(
        [job("usgs_earthquakes")], store=store,
        fetcher=lambda *_args: FetchResult(304), sink=lambda _item: None,
        jitter=lambda: "invalid", clock=lambda: 1000,
    )
    assert scheduler.states["usgs_earthquakes"].next_attempt == 1030
    scheduler.force_due("usgs_earthquakes", now=1000)
    scheduler.start()
    scheduler.start()
    time.sleep(0.05)
    assert scheduler.stop() is True
    assert scheduler._thread and not scheduler._thread.is_alive()

    assert ProviderState.from_dict({"next_attempt": float("nan")}).next_attempt == 0
    assert ProviderState.from_dict("bad") == ProviderState()
    assert ProviderState.from_dict({"etags": [], "last_modified": {}}) == ProviderState()
    with pytest.raises(KeyError):
        scheduler.force_due("missing")
    with pytest.raises(ValueError, match="max_workers"):
        ProviderScheduler(
            [], store=store, fetcher=lambda *_args: FetchResult(304),
            sink=lambda _item: None, max_workers=0,
        )


def test_scheduler_stop_cancels_queued_provider_waves(tmp_path):
    provider_ids = list(CORE_CANONICAL_ADAPTERS)[:6]
    store = scheduler_store(tmp_path, *provider_ids)
    release = threading.Event()
    started = []
    lock = threading.Lock()

    def blocked_fetch(*_args):
        with lock:
            started.append(threading.get_ident())
        release.wait(2)
        return FetchResult(304)

    scheduler = ProviderScheduler(
        [job(provider_id) for provider_id in provider_ids],
        store=store,
        fetcher=blocked_fetch,
        sink=lambda _item: None,
        max_workers=2,
        jitter=lambda: 0,
        clock=lambda: 1000,
    )
    scheduler.start()
    deadline = time.monotonic() + 1
    while len(started) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(started) == 2
    assert scheduler.stop(timeout=0.5) is True
    assert len(started) == 2
    release.set()
    time.sleep(0.05)
    assert len(started) == 2


def test_scheduler_stop_aborts_remaining_context_urls(tmp_path):
    provider_id = "noaa_coops_water_levels"
    store = scheduler_store(tmp_path, provider_id)
    first_started = threading.Event()
    release = threading.Event()
    calls = []
    urls = (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?station=1",
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?station=2",
    )

    def fetcher(url, *_args):
        calls.append(url)
        first_started.set()
        release.wait(2)
        return FetchResult(204)

    scheduler = ProviderScheduler(
        [job(provider_id)],
        store=store,
        fetcher=fetcher,
        sink=lambda _item: None,
        context_urls=lambda _provider_id: urls,
        max_workers=1,
        jitter=lambda: 0,
        clock=lambda: 1000,
    )
    scheduler.start()
    assert first_started.wait(1)
    scheduler.request_stop()
    release.set()
    assert scheduler.stop(timeout=0.5) is True
    assert calls == [urls[0]]


def test_scheduler_validation_and_all_failure_categories(tmp_path):
    store = scheduler_store(tmp_path, "usgs_earthquakes")
    duplicate = job("usgs_earthquakes")
    with pytest.raises(ValueError, match="duplicate"):
        ProviderScheduler(
            [duplicate, duplicate], store=store,
            fetcher=lambda *_args: FetchResult(304), sink=lambda _item: None,
        )
    scheduler = ProviderScheduler(
        [duplicate], store=store, fetcher=lambda *_args: FetchResult(304),
        sink=lambda _item: None, jitter=lambda: 0, clock=lambda: 1000,
    )
    assert scheduler.managed_provider_ids == frozenset({"usgs_earthquakes"})
    with pytest.raises(ValueError, match="scheduler time"):
        scheduler.run_due(now=float("nan"))

    cases = (
        (object(), "fetch_error"),
        (FetchResult(500), "http_500"),
        (FetchResult(200, b"x" * (2 * 1024 * 1024 + 1)), "body_cap_exceeded"),
        (FetchResult(200, b"{}"), "malformed_body"),
        (FetchResult(200, fixture_body("usgs_earthquakes"), freshness="error"), "upstream_error"),
        (FetchResult(204, freshness="error"), "upstream_error"),
    )
    for response, expected in cases:
        scheduler.fetcher = lambda *_args, value=response: value
        scheduler.states["usgs_earthquakes"].circuit_until = 0
        scheduler.force_due("usgs_earthquakes", now=1000)
        result = scheduler.run_due(now=1000)[0]
        assert result["ok"] is False
        assert store.source_health("usgs_earthquakes")[0]["detail"] == expected

    assert ProviderScheduler._retry_after(None, 1000) is None
    assert ProviderScheduler._retry_after("bad", 1000) is None
    assert ProviderScheduler._retry_after(
        "Thu, 01 Jan 1970 00:20:00 GMT", 1000
    ) == 1200
    assert ProviderScheduler._retry_after(
        "Thu, 01 Jan 2099 00:20:00 GMT", 1000
    ) == 87400

    scheduler.states["usgs_earthquakes"].last_attempt = 2000
    scheduler.states["usgs_earthquakes"].next_attempt = 2060
    scheduler.states["usgs_earthquakes"].circuit_until = 2060
    scheduler.fetcher = lambda *_args: FetchResult(304)
    assert scheduler.run_due(now=1000)[0]["status"] == "cached"
    assert scheduler.states["usgs_earthquakes"].next_attempt == 1060
    scheduler.stop()
