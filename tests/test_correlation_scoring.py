import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from foglight_core.correlation import (
    CORRELATION_RULE_VERSION,
    CORRELATION_RULES,
    CorrelationEngine,
    correlation_decision,
    normalize_title,
    title_similarity,
)
from foglight_core.models import (
    Certainty,
    ChangeType,
    EventKind,
    Incident,
    Metric,
    Observation,
    RelationType,
    Severity,
    Status,
    Urgency,
)
from foglight_core.scoring import PRIORITY_RULES, SCORE_RULE_VERSION, score_observations
from foglight_core.storage import ObservationStore

NOW = "2026-07-10T20:00:00Z"
ROOT = Path(__file__).parents[1]


def observation(provider="usgs_earthquakes", record="one", **overrides):
    values = {
        "provider_id": provider,
        "provider_record_id": record,
        "raw_body": f"raw-{provider}-{record}-{overrides}",
        "kind": EventKind.EARTHQUAKE,
        "headline": "M 6.0 Fixture Coast",
        "summary": "Fixture summary",
        "status": Status.ACTIVE,
        "severity": Severity.SEVERE,
        "urgency": Urgency.EXPECTED,
        "certainty": Certainty.UNKNOWN,
        "event_at": "2026-07-10T19:30:00Z",
        "source_updated_at": "2026-07-10T19:35:00Z",
        "ingested_at": NOW,
        "geometry": {"type": "Point", "coordinates": [139.7, 35.6]},
        "metrics": {},
        "source_url": "https://example.test/item",
    }
    values.update(overrides)
    return Observation.create(**values)


def store_for(tmp_path, *providers):
    store = ObservationStore(tmp_path / "incidents.sqlite3")
    for provider in providers or ("usgs_earthquakes",):
        store.register_provider(provider, {})
    return store


def test_title_normalization_similarity_and_category_specific_decisions():
    assert json.loads((ROOT / "config" / "correlation_rules.v1.json").read_text()) == (
        CORRELATION_RULES
    )
    assert json.loads((ROOT / "config" / "priority_rules.v1.json").read_text()) == (
        PRIORITY_RULES
    )
    assert normalize_title("The Café Update: Fire on Ridge") == "cafe fire ridge"
    assert title_similarity("Major fire reaches Ridge", "Ridge major fire reaches") == 1
    assert title_similarity("", "anything") == 0

    base = observation()
    exact = correlation_decision(base, base)
    assert exact.merge and exact.rule == "exact_observation_id"
    other_kind = observation("nws_alerts", "weather", kind=EventKind.WEATHER_ALERT)
    assert correlation_decision(base, other_kind).rule == "different_kind"

    nearby = observation(
        "gdacs", "quake", event_at="2026-07-10T19:40:00Z",
        geometry={"type": "Point", "coordinates": [140.0, 35.6]},
    )
    assert correlation_decision(base, nearby).merge
    far = observation(
        "gdacs", "far", geometry={"type": "Point", "coordinates": [-70, 10]}
    )
    assert not correlation_decision(base, far).merge

    cyclone_a = observation(
        record="storm-a", kind=EventKind.TROPICAL_CYCLONE,
        headline="HU Alpha", metrics={"storm_name": Metric("Alpha", "name", "fixture")},
    )
    cyclone_b = observation(
        "nhc_storms", "storm-b", kind=EventKind.TROPICAL_CYCLONE,
        headline="Hurricane Alpha", metrics={"storm_name": Metric("ALPHA", "name", "fixture")},
    )
    assert correlation_decision(cyclone_a, cyclone_b).merge

    tsunami_a = observation(
        record="ts-a", kind=EventKind.TSUNAMI,
        metrics={"relation_candidate": Metric("series-1", "series", "fixture")},
    )
    tsunami_b = observation(
        "noaa_tsunami", "ts-b", kind=EventKind.TSUNAMI,
        metrics={"relation_candidate": Metric("series-1", "series", "fixture")},
    )
    assert correlation_decision(tsunami_a, tsunami_b).merge

    hazard_a = observation(record="fire-a", kind=EventKind.WILDFIRE, headline="Ridge Fire")
    hazard_b = observation(
        "nasa_eonet", "fire-b", kind=EventKind.WILDFIRE,
        headline="The Ridge Fire Update",
    )
    assert correlation_decision(hazard_a, hazard_b).merge

    media_a = observation(
        record="news-a", kind=EventKind.NEWS_ITEM,
        headline="Major quake strikes Fixture Coast",
    )
    media_b = observation(
        "reliefweb_rss", "news-b", kind=EventKind.NEWS_ITEM,
        headline="Major quake strikes the Fixture Coast update",
    )
    assert correlation_decision(media_a, media_b).merge
    unrelated = observation(
        "reliefweb_rss", "news-c", kind=EventKind.NEWS_ITEM,
        headline="Election results announced",
    )
    assert not correlation_decision(media_a, unrelated).merge
    market = observation(record="market", kind=EventKind.MARKET_SNAPSHOT)
    assert correlation_decision(market, market.__class__.create(
        provider_id="gdacs", provider_record_id="market2", raw_body="x",
        kind=EventKind.MARKET_SNAPSHOT, headline="Market", summary="",
        status=Status.ACTIVE, severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN,
        certainty=Certainty.UNKNOWN, ingested_at=NOW,
    )).rule == "exact_only"


def test_priority_components_boundaries_penalties_and_media_lane():
    severe = observation(certainty=Certainty.OBSERVED, urgency=Urgency.IMMEDIATE)
    score = score_observations([severe], now=NOW, watch_region_relevance=10)
    assert score.total == 75
    assert score.components == {
        "rule_version": SCORE_RULE_VERSION,
        "lane": "hazards",
        "impact": 30,
        "urgency": 20,
        "freshness": 15,
        "corroboration": 0,
        "watch_region": 10,
        "penalty": 0,
        "age_hours": 0.5,
        "source_count": 1,
    }
    corroborated = observation("gdacs", "two")
    assert score_observations([severe, corroborated], now=NOW).components[
        "corroboration"
    ] == 5
    ended = observation(status=Status.ENDED, expires_at="2026-07-10T19:00:00Z")
    assert score_observations([ended], now=NOW).components["penalty"] == -40
    stale = observation(event_at="2026-07-01T00:00:00Z")
    assert score_observations([stale], now=NOW).components["penalty"] == -20
    media = observation(
        kind=EventKind.NEWS_ITEM, severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN,
    )
    media_score = score_observations([media] * 10, now=NOW)
    assert media_score.total == 15
    assert media_score.components["lane"] == "world_context"
    media_spike = [
        observation(
            f"media_source_{index}", f"media-{index}", kind=EventKind.NEWS_ITEM,
            severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN,
            certainty=Certainty.UNKNOWN,
        )
        for index in range(10)
    ]
    spike_score = score_observations(media_spike, now=NOW)
    assert spike_score.total == 30
    assert all(item.certainty is Certainty.UNKNOWN for item in media_spike)
    with pytest.raises(ValueError, match="at least one"):
        score_observations([], now=NOW)
    with pytest.raises(ValueError, match="watch relevance"):
        score_observations([severe], now=NOW, watch_region_relevance=11)


def test_engine_exact_updates_revisions_changes_timeline_and_restart(tmp_path):
    store = store_for(tmp_path)
    engine = CorrelationEngine(store)
    first = observation()
    incident = engine.ingest(first, now=NOW)
    assert incident.change_type is ChangeType.NEW
    assert incident.revision == 1
    assert incident.priority_components["correlation_version"] == CORRELATION_RULE_VERSION
    assert engine.ingest(first, now=NOW) == incident

    stronger = observation(
        severity=Severity.EXTREME,
        urgency=Urgency.IMMEDIATE,
        source_updated_at="2026-07-10T20:05:00Z",
        ingested_at="2026-07-10T20:05:00Z",
    )
    escalated = engine.ingest(stronger, now="2026-07-10T20:05:00Z")
    assert escalated.incident_id == incident.incident_id
    assert escalated.revision == 2
    assert escalated.change_type is ChangeType.ESCALATED

    active_low = observation(
        status=Status.ACTIVE,
        severity=Severity.MINOR,
        urgency=Urgency.PAST,
        source_updated_at="2026-07-10T20:10:00Z",
        ingested_at="2026-07-10T20:10:00Z",
    )
    downgraded = engine.ingest(active_low, now="2026-07-10T20:10:00Z")
    assert downgraded.change_type is ChangeType.DOWNGRADED

    cancelled_observation = observation(
        status=Status.CANCELLED,
        severity=Severity.UNKNOWN,
        urgency=Urgency.PAST,
        source_updated_at="2026-07-10T20:15:00Z",
        ingested_at="2026-07-10T20:15:00Z",
    )
    cancelled = engine.ingest(cancelled_observation, now="2026-07-10T20:15:00Z")
    assert cancelled.change_type is ChangeType.CANCELLED
    assert cancelled.status is Status.CANCELLED

    ended_observation = observation(
        status=Status.ENDED,
        severity=Severity.UNKNOWN,
        urgency=Urgency.PAST,
        source_updated_at="2026-07-10T20:20:00Z",
        ingested_at="2026-07-10T20:20:00Z",
    )
    resolved = engine.ingest(ended_observation, now="2026-07-10T20:20:00Z")
    assert resolved.change_type is ChangeType.RESOLVED
    assert engine.mark_provider_lost(
        "usgs_earthquakes", now="2026-07-10T20:21:00Z"
    ) == []

    assert store.get_incident(resolved.incident_id) == resolved
    assert store.incident_for_observation(first.observation_id) == resolved
    assert store.list_incidents() == [resolved]
    assert [item["revision"] for item in store.timeline(resolved.incident_id)] == [5, 4, 3, 2, 1]
    changes = store.changes_after(0)
    assert [item["revision"] for item in changes] == [1, 2, 3, 4, 5]
    assert store.changes_after(changes[-1]["cursor"]) == []

    restarted = CorrelationEngine(ObservationStore(store.path))
    assert restarted.ingest(ended_observation, now="2026-07-10T20:20:00Z") == resolved
    last_cursor = changes[-1]["cursor"]
    with restarted.store.transaction(immediate=True) as connection:
        connection.execute("DELETE FROM incidents WHERE incident_id=?", (resolved.incident_id,))
    reborn = restarted.ingest(ended_observation, now="2026-07-10T20:21:00Z")
    assert reborn.change_type is ChangeType.NEW
    new_changes = restarted.store.changes_after(last_cursor)
    assert len(new_changes) == 1
    assert new_changes[0]["cursor"] > last_cursor


def test_engine_merges_intended_candidates_and_relates_cross_kind(tmp_path):
    store = store_for(tmp_path, "usgs_earthquakes", "gdacs", "noaa_tsunami")
    engine = CorrelationEngine(store)
    quake = engine.ingest(observation(), now=NOW)
    corroborating = observation(
        "gdacs", "gdacs-quake", event_at="2026-07-10T19:35:00Z",
        geometry={"type": "Point", "coordinates": [139.8, 35.6]},
    )
    merged = engine.ingest(corroborating, now="2026-07-10T20:01:00Z")
    assert merged.incident_id == quake.incident_id
    assert len(merged.observation_ids) == 2
    assert merged.priority_components["corroboration"] == 5
    partially_lost = engine.mark_source_lost(
        merged, "gdacs", now="2026-07-10T20:01:30Z"
    )
    assert partially_lost.status is Status.ACTIVE
    assert partially_lost.priority_components["corroboration"] == 0
    assert partially_lost.priority_components["lost_sources"] == "gdacs"
    merged = partially_lost

    tsunami_observation = observation(
        "noaa_tsunami", "bulletin", kind=EventKind.TSUNAMI,
        headline="Tsunami warning", certainty=Certainty.UNKNOWN,
        metrics={"relation_candidate": Metric("series", "series", "fixture")},
    )
    tsunami = engine.ingest(tsunami_observation, now="2026-07-10T20:02:00Z")
    assert tsunami.relations[0].relation_type is RelationType.CAUSED_BY
    related = engine.relate(tsunami, merged, RelationType.CAUSED_BY, now="2026-07-10T20:03:00Z")
    assert related.relations[0].target_incident_id == merged.incident_id
    assert engine.relate(related, merged, RelationType.CAUSED_BY, now=NOW) == related
    with pytest.raises(ValueError, match="same-kind"):
        engine.relate(merged, merged, RelationType.RELATED_TO, now=NOW)
    lost = engine.mark_source_lost(
        related, "noaa_tsunami", now="2026-07-10T20:04:00Z"
    )
    assert lost.change_type is ChangeType.SOURCE_LOST
    assert lost.status is Status.UNKNOWN
    assert engine.mark_source_lost(
        lost, "provider-not-in-incident", now="2026-07-10T20:05:00Z"
    ) == lost


def test_multiple_source_losses_and_recovery_keep_independent_state(tmp_path):
    store = store_for(tmp_path, "usgs_earthquakes", "gdacs")
    engine = CorrelationEngine(store)
    first = engine.ingest(observation(), now=NOW)
    second_observation = observation(
        "gdacs", "second-source", event_at="2026-07-10T19:35:00Z",
        geometry={"type": "Point", "coordinates": [139.8, 35.6]},
    )
    merged = engine.ingest(second_observation, now=NOW)
    assert merged.priority_components["source_count"] == 2

    one_lost = engine.mark_provider_lost("usgs_earthquakes", now=NOW)[0]
    assert one_lost.priority_components["source_count"] == 1
    assert one_lost.priority_components["lost_sources"] == "usgs_earthquakes"
    both_lost = engine.mark_provider_lost("gdacs", now=NOW)[0]
    assert both_lost.priority_score == 0
    assert both_lost.priority_components["source_count"] == 0
    assert both_lost.priority_components["lost_sources"] == "gdacs,usgs_earthquakes"

    gdacs_recovered = engine.ingest(second_observation, now=NOW)
    assert gdacs_recovered.status is Status.ACTIVE
    assert gdacs_recovered.priority_components["source_count"] == 1
    assert gdacs_recovered.priority_components["lost_sources"] == "usgs_earthquakes"
    all_recovered = engine.ingest(observation(), now=NOW)
    assert all_recovered.priority_components["source_count"] == 2
    assert "lost_sources" not in all_recovered.priority_components
    assert all_recovered.incident_id == first.incident_id


def test_correlation_is_not_limited_to_top_thousand_incidents(tmp_path):
    store = store_for(tmp_path, "usgs_earthquakes", "gdacs")
    engine = CorrelationEngine(store)
    target_observation = observation(record="low-priority-target")
    target = engine.ingest(target_observation, now=NOW)

    observation_rows = []
    incident_rows = []
    link_rows = []
    for index in range(1000):
        item = observation(
            record=f"filler-{index}",
            headline=f"Unrelated earthquake {index}",
            geometry={"type": "Point", "coordinates": [-70, 10]},
        )
        document = json.dumps(item.to_dict(), sort_keys=True, separators=(",", ":"))
        min_lon, min_lat, max_lon, max_lat = item.bbox
        observation_rows.append((
            item.observation_id, item.provider_id, item.provider_record_id,
            item.kind.value, item.status.value, item.event_at, item.ingested_at,
            item.content_hash, min_lon, min_lat, max_lon, max_lat, document,
        ))
        filler = Incident(
            incident_id=f"incident:earthquake:filler{index:06d}",
            kind=EventKind.EARTHQUAKE,
            headline=item.headline,
            summary=item.summary,
            status=Status.ACTIVE,
            severity=Severity.SEVERE,
            urgency=Urgency.EXPECTED,
            certainty=Certainty.UNKNOWN,
            priority_score=100,
            priority_components={"rule_version": "fixture", "lane": "hazards"},
            first_seen_at=NOW,
            last_changed_at=NOW,
            last_observed_at=NOW,
            observation_ids=(item.observation_id,),
            change_type=ChangeType.NEW,
            revision=1,
            geometry=item.geometry,
        )
        min_lon, min_lat, max_lon, max_lat = filler.bbox
        incident_rows.append((
            filler.incident_id, filler.kind.value, filler.status.value,
            filler.priority_score, filler.first_seen_at, filler.last_changed_at,
            filler.last_observed_at, filler.revision,
            json.dumps(filler.to_dict(), sort_keys=True, separators=(",", ":")),
            "hazards", min_lon, min_lat, max_lon, max_lat,
        ))
        link_rows.append((filler.incident_id, item.observation_id))
    with store.transaction(immediate=True) as connection:
        connection.executemany(
            "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            observation_rows,
        )
        connection.executemany(
            "INSERT INTO incidents(incident_id, kind, status, priority_score, first_seen_at, "
            "last_changed_at, last_observed_at, revision, document_json, lane, min_lon, "
            "min_lat, max_lon, max_lat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            incident_rows,
        )
        connection.executemany(
            "INSERT INTO incident_observations VALUES (?, ?)", link_rows
        )

    corroborating = observation(
        "gdacs", "late-candidate", event_at="2026-07-10T19:35:00Z",
        geometry={"type": "Point", "coordinates": [139.8, 35.6]},
    )
    merged = engine.ingest(corroborating, now="2026-07-10T20:01:00Z")
    assert merged.incident_id == target.incident_id
    assert len(merged.observation_ids) == 2


def test_engine_serializes_concurrent_revision_work(tmp_path):
    store = store_for(tmp_path, "usgs_earthquakes", "gdacs")
    engine = CorrelationEngine(store)
    first = observation(record="concurrent-a")
    second = observation(
        "gdacs", "concurrent-b", event_at="2026-07-10T19:35:00Z",
        geometry={"type": "Point", "coordinates": [139.8, 35.6]},
    )
    gate = threading.Barrier(3)

    def ingest_at_once(item):
        gate.wait()
        return engine.ingest(item, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ingest_at_once, item) for item in (first, second)]
        gate.wait()
        results = [future.result() for future in futures]
    incidents = store.correlation_incidents(
        include_kinds={EventKind.EARTHQUAKE.value}
    )
    assert len(incidents) == 1
    assert len(incidents[0].observation_ids) == 2
    assert results[-1].incident_id == results[0].incident_id


def test_scheduler_state_and_source_health_round_trip(tmp_path):
    store = store_for(tmp_path)
    assert store.load_scheduler_state("usgs_earthquakes") is None
    state = {"etag": '"fixture"', "consecutive_failures": 2, "next_attempt": 123.0}
    store.save_scheduler_state("usgs_earthquakes", state)
    assert store.load_scheduler_state("usgs_earthquakes") == state
    store.update_source_health("usgs_earthquakes", "stale", NOW, detail="timeout")
    assert store.source_health()[0]["status"] == "stale"
    assert store.source_health("usgs_earthquakes")[0]["detail"] == "timeout"
    assert store.source_health("missing") == []


def test_reverse_cross_kind_and_media_coverage_relations_are_explicit(tmp_path):
    store = store_for(
        tmp_path, "noaa_tsunami", "usgs_earthquakes", "reliefweb_rss"
    )
    engine = CorrelationEngine(store)
    tsunami_observation = observation(
        "noaa_tsunami", "first-tsunami", kind=EventKind.TSUNAMI,
        headline="Fixture Coast tsunami",
        metrics={"relation_candidate": Metric("series", "series", "fixture")},
    )
    tsunami = engine.ingest(tsunami_observation, now=NOW)
    assert tsunami.relations == ()
    quake = engine.ingest(observation(), now=NOW)
    updated_tsunami = store.get_incident(tsunami.incident_id)
    assert updated_tsunami.relations[0].target_incident_id == quake.incident_id

    media_observation = observation(
        "reliefweb_rss", "coverage", kind=EventKind.NEWS_ITEM,
        headline="M 6.0 quake at Fixture Coast",
        severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN,
    )
    media = engine.ingest(media_observation, now=NOW)
    assert media.relations[0].relation_type is RelationType.COVERAGE_OF
    assert media.relations[0].target_incident_id == quake.incident_id

    old_media = engine.ingest(
        observation(
            "reliefweb_rss", "old-coverage", kind=EventKind.CONFLICT_REPORT,
            headline="M 6.0 quake at Fixture Coast",
            event_at="2026-06-01T00:00:00Z",
            source_updated_at="2026-06-01T00:00:00Z",
            severity=Severity.UNKNOWN,
            urgency=Urgency.UNKNOWN,
        ),
        now=NOW,
    )
    assert old_media.relations == ()

    unknown = engine.ingest(
        observation(
            record="unknown-aircraft",
            kind=EventKind.AIRCRAFT,
            status=Status.UNKNOWN,
            severity=Severity.UNKNOWN,
            urgency=Urgency.UNKNOWN,
            geometry=None,
        ),
        now=NOW,
    )
    assert unknown.status is Status.UNKNOWN

    far_tsunami = engine.ingest(
        observation(
            "noaa_tsunami", "far-tsunami", kind=EventKind.TSUNAMI,
            headline="Distant tsunami",
            event_at="2026-07-01T00:00:00Z",
            source_updated_at="2026-07-01T00:00:00Z",
            geometry={"type": "Point", "coordinates": [-70, 10]},
            metrics={"relation_candidate": Metric("other-series", "series", "fixture")},
        ),
        now=NOW,
    )
    assert far_tsunami.relations == ()
    far_quake = engine.ingest(
        observation(
            record="far-quake",
            event_at="2026-06-01T00:00:00Z",
            source_updated_at="2026-06-01T00:00:00Z",
            geometry={"type": "Point", "coordinates": [-30, -20]},
        ),
        now=NOW,
    )
    assert far_quake.relations == ()


def test_aviation_advisories_relate_conservatively_without_cross_kind_merge(tmp_path):
    store = store_for(
        tmp_path, "noaa_aviation_weather", "nws_alerts", "smithsonian_volcano"
    )
    engine = CorrelationEngine(store)
    aviation_observation = observation(
        "noaa_aviation_weather", "sigmet-convective",
        kind=EventKind.AVIATION_HAZARD,
        headline="CONVECTIVE SIGMET 3E",
        severity=Severity.UNKNOWN,
        urgency=Urgency.UNKNOWN,
        event_at=None,
        source_updated_at=None,
        effective_at="2026-07-10T19:30:00Z",
        expires_at="2026-07-10T23:30:00Z",
        geometry={"type": "Point", "coordinates": [-99.0, 40.0]},
        metrics={"hazard_type": Metric("CONVECTIVE", "AWC code", "fixture")},
    )
    aviation = engine.ingest(aviation_observation, now=NOW)
    original_score = aviation.priority_score
    weather = engine.ingest(
        observation(
            "nws_alerts", "severe-weather",
            kind=EventKind.WEATHER_ALERT,
            headline="Severe Thunderstorm Warning",
            geometry={"type": "Point", "coordinates": [-98.8, 40.1]},
        ),
        now=NOW,
    )
    related_aviation = store.get_incident(aviation.incident_id)
    assert len(related_aviation.relations) == 1
    assert related_aviation.relations[0].relation_type is RelationType.RELATED_TO
    assert related_aviation.relations[0].target_incident_id == weather.incident_id
    assert related_aviation.priority_score == original_score
    assert related_aviation.kind is EventKind.AVIATION_HAZARD
    assert weather.kind is EventKind.WEATHER_ALERT
    assert related_aviation.observation_ids == (aviation_observation.observation_id,)
    assert len(store.correlation_incidents()) == 2

    volcano = engine.ingest(
        observation(
            "smithsonian_volcano", "volcano",
            kind=EventKind.VOLCANO,
            headline="Fixture Volcano",
            event_at="2026-07-10T18:00:00Z",
            source_updated_at="2026-07-10T18:00:00Z",
            geometry={"type": "Point", "coordinates": [139.7, 35.6]},
        ),
        now=NOW,
    )
    ash = engine.ingest(
        observation(
            "noaa_aviation_weather", "sigmet-ash",
            kind=EventKind.AVIATION_HAZARD,
            headline="VOLCANIC ASH SIGMET 2A",
            severity=Severity.UNKNOWN,
            urgency=Urgency.UNKNOWN,
            event_at=None,
            source_updated_at=None,
            effective_at="2026-07-10T19:00:00Z",
            geometry={"type": "Point", "coordinates": [140.0, 35.8]},
            metrics={"hazard_type": Metric("VOLCANIC ASH", "AWC code", "fixture")},
        ),
        now=NOW,
    )
    assert ash.relations[0].target_incident_id == volcano.incident_id

    unrelated = engine.ingest(
        observation(
            "noaa_aviation_weather", "sigmet-not-severe",
            kind=EventKind.AVIATION_HAZARD,
            headline="IFR SIGMET 4A",
            severity=Severity.UNKNOWN,
            urgency=Urgency.UNKNOWN,
            event_at=None,
            source_updated_at=None,
            effective_at="2026-07-10T19:00:00Z",
            geometry={"type": "Point", "coordinates": [-99.0, 40.0]},
            metrics={"hazard_type": Metric("IFR", "AWC code", "fixture")},
        ),
        now=NOW,
    )
    assert unrelated.relations == ()


def test_fema_declarations_relate_by_type_area_and_date_without_escalation(tmp_path):
    store = store_for(tmp_path, "openfema_declarations", "nws_alerts")
    engine = CorrelationEngine(store)

    hazard_observation = observation(
        "nws_alerts", "adams-storm",
        kind=EventKind.WEATHER_ALERT,
        headline="Severe Thunderstorm Warning for Adams County",
        event_at="2026-07-08T18:00:00Z",
        source_updated_at="2026-07-08T18:05:00Z",
        location_name="Adams County, Colorado",
        metrics={
            "state_codes": Metric("CO", "USPS codes", "fixture"),
            "affected_area": Metric("Adams County", "text", "fixture"),
        },
    )
    hazard = engine.ingest(hazard_observation, now=NOW)
    declaration_observation = observation(
        "openfema_declarations", "declaration-adams",
        kind=EventKind.DISASTER_DECLARATION,
        headline="SEVERE STORMS AND FLOODING",
        status=Status.UNKNOWN,
        severity=Severity.UNKNOWN,
        urgency=Urgency.UNKNOWN,
        certainty=Certainty.UNKNOWN,
        event_at=None,
        effective_at="2026-07-10T18:00:00Z",
        source_updated_at="2026-07-10T18:20:00Z",
        geometry=None,
        location_name="Adams (County), CO",
        metrics={
            "administrative_context": Metric(
                "federal_disaster_declaration", "semantics", "fixture"
            ),
            "incident_type": Metric("Severe Storm", "FEMA type", "fixture"),
            "state_code": Metric("CO", "USPS code", "fixture"),
            "declared_area": Metric("Adams (County)", "FEMA area", "fixture"),
            "incident_begin": Metric("2026-07-05T00:00:00Z", "RFC 3339", "fixture"),
            "incident_end": Metric("2026-07-09T23:59:00Z", "RFC 3339", "fixture"),
        },
    )
    declaration = engine.ingest(declaration_observation, now=NOW)
    assert len(declaration.relations) == 1
    assert declaration.relations[0].target_incident_id == hazard.incident_id
    assert declaration.relations[0].relation_type is RelationType.RELATED_TO
    unchanged_hazard = store.get_incident(hazard.incident_id)
    assert unchanged_hazard.revision == hazard.revision
    assert unchanged_hazard.priority_score == hazard.priority_score
    assert unchanged_hazard.urgency is Urgency.EXPECTED
    assert declaration.urgency is Urgency.UNKNOWN
    assert declaration.priority_components["urgency"] == 0
    assert declaration.observation_ids == (declaration_observation.observation_id,)

    updated_declaration = engine.ingest(
        observation(
            "openfema_declarations", "declaration-adams",
            kind=EventKind.DISASTER_DECLARATION,
            headline="SEVERE STORMS AND FLOODING — UPDATED ADMINISTRATION",
            status=Status.UNKNOWN,
            severity=Severity.UNKNOWN,
            urgency=Urgency.UNKNOWN,
            certainty=Certainty.UNKNOWN,
            event_at=None,
            effective_at="2026-07-10T18:00:00Z",
            source_updated_at="2026-07-10T19:20:00Z",
            geometry=None,
            location_name="Adams (County), CO",
            metrics=declaration_observation.metrics,
        ),
        now="2026-07-10T20:30:00Z",
    )
    assert updated_declaration.urgency is Urgency.UNKNOWN
    assert updated_declaration.relations == declaration.relations
    hazard_after_update = store.get_incident(hazard.incident_id)
    assert hazard_after_update.revision == hazard.revision
    assert hazard_after_update.priority_score == hazard.priority_score
    assert hazard_after_update.urgency is hazard.urgency

    reverse_declaration = engine.ingest(
        observation(
            "openfema_declarations", "declaration-boulder",
            kind=EventKind.DISASTER_DECLARATION,
            headline="SEVERE STORMS",
            status=Status.UNKNOWN,
            severity=Severity.UNKNOWN,
            urgency=Urgency.UNKNOWN,
            certainty=Certainty.UNKNOWN,
            event_at=None,
            effective_at="2026-07-10T18:00:00Z",
            source_updated_at="2026-07-10T18:20:00Z",
            geometry=None,
            location_name="Boulder (County), CO",
            metrics={
                "incident_type": Metric("Severe Storm", "FEMA type", "fixture"),
                "state_code": Metric("CO", "USPS code", "fixture"),
                "declared_area": Metric("Boulder (County)", "FEMA area", "fixture"),
                "incident_begin": Metric("2026-07-05T00:00:00Z", "RFC 3339", "fixture"),
            },
        ),
        now=NOW,
    )
    assert reverse_declaration.relations == ()
    boulder_hazard = engine.ingest(
        observation(
            "nws_alerts", "boulder-storm",
            kind=EventKind.WEATHER_ALERT,
            headline="Severe Thunderstorm Warning for Boulder County",
            event_at="2026-07-08T18:00:00Z",
            source_updated_at="2026-07-08T18:05:00Z",
            location_name="Boulder County, Colorado",
            metrics={
                "state_codes": Metric("CO", "USPS codes", "fixture"),
                "affected_area": Metric("Boulder County", "text", "fixture"),
            },
        ),
        now=NOW,
    )
    updated_reverse = store.get_incident(reverse_declaration.incident_id)
    assert updated_reverse.relations[0].target_incident_id == boulder_hazard.incident_id

    mismatched = engine.ingest(
        observation(
            "openfema_declarations", "declaration-mismatch",
            kind=EventKind.DISASTER_DECLARATION,
            headline="WILDFIRE DECLARATION",
            status=Status.UNKNOWN,
            severity=Severity.UNKNOWN,
            urgency=Urgency.UNKNOWN,
            certainty=Certainty.UNKNOWN,
            event_at=None,
            effective_at="2026-07-10T18:00:00Z",
            source_updated_at="2026-07-10T18:20:00Z",
            geometry=None,
            metrics={
                "incident_type": Metric("Fire", "FEMA type", "fixture"),
                "state_code": Metric("CO", "USPS code", "fixture"),
                "declared_area": Metric("Adams (County)", "FEMA area", "fixture"),
                "incident_begin": Metric("2026-07-05T00:00:00Z", "RFC 3339", "fixture"),
            },
        ),
        now=NOW,
    )
    assert mismatched.relations == ()

    for record, state, begin in (
        ("declaration-wrong-state", "WY", "2026-07-05T00:00:00Z"),
        ("declaration-too-old", "CO", "2026-05-01T00:00:00Z"),
    ):
        rejected = engine.ingest(
            observation(
                "openfema_declarations", record,
                kind=EventKind.DISASTER_DECLARATION,
                headline="SEVERE STORMS",
                status=Status.UNKNOWN,
                severity=Severity.UNKNOWN,
                urgency=Urgency.UNKNOWN,
                certainty=Certainty.UNKNOWN,
                event_at=None,
                effective_at="2026-07-10T18:00:00Z",
                source_updated_at="2026-07-10T18:20:00Z",
                geometry=None,
                location_name=f"Adams (County), {state}",
                metrics={
                    "incident_type": Metric("Severe Storm", "FEMA type", "fixture"),
                    "state_code": Metric(state, "USPS code", "fixture"),
                    "declared_area": Metric("Adams (County)", "FEMA area", "fixture"),
                    "incident_begin": Metric(begin, "RFC 3339", "fixture"),
                    "incident_end": Metric(begin, "RFC 3339", "fixture"),
                },
            ),
            now=NOW,
        )
        assert rejected.relations == ()
