import concurrent.futures
import contextlib
import datetime as dt
import json
import math
import sqlite3
import time

import pytest

from foglight_core import storage
from foglight_core.models import (
    Certainty,
    ChangeType,
    EventKind,
    Incident,
    Metric,
    Observation,
    Relation,
    RelationType,
    Severity,
    Status,
    Urgency,
    normalize_geometry,
    normalize_timestamp,
    observation_id,
)
from foglight_core.storage import ObservationStore

NOW = "2026-07-10T20:00:00Z"
RAW_HASH = "a" * 64


def make_observation(record="fixture-1", **overrides):
    values = {
        "provider_id": "usgs_earthquakes",
        "provider_record_id": record,
        "raw_body": f"raw-{record}",
        "kind": EventKind.EARTHQUAKE,
        "headline": "M 6.2 — Fixture Coast",
        "summary": "Fixture summary",
        "status": Status.ACTIVE,
        "severity": Severity.SEVERE,
        "urgency": Urgency.IMMEDIATE,
        "certainty": Certainty.OBSERVED,
        "event_at": "2026-07-10T18:00:00-02:00",
        "effective_at": NOW,
        "expires_at": "2026-07-11T00:00:00Z",
        "source_updated_at": "2026-07-10T20:01:00Z",
        "ingested_at": NOW,
        "geometry": {"type": "Point", "coordinates": [139.7, 35.6, 20]},
        "location_name": "Fixture Coast",
        "country_codes": ("JP",),
        "metrics": {
            "magnitude": Metric(6.2, "Mw", "USGS properties.mag"),
            "tsunami_flag": Metric(False, "boolean", "USGS properties.tsunami"),
        },
        "source_url": "https://earthquake.usgs.gov/earthquakes/eventpage/fixture",
    }
    values.update(overrides)
    return Observation.create(**values)


def make_incident(observation, incident_id="incident.fixture", **overrides):
    values = {
        "incident_id": incident_id,
        "kind": observation.kind,
        "headline": observation.headline,
        "summary": "Deterministic fixture summary",
        "status": observation.status,
        "severity": observation.severity,
        "urgency": observation.urgency,
        "certainty": observation.certainty,
        "priority_score": 72,
        "priority_components": {"rule_version": "1", "severity": 30, "urgency": 15},
        "first_seen_at": NOW,
        "last_changed_at": NOW,
        "last_observed_at": NOW,
        "geometry": observation.geometry,
        "observation_ids": (observation.observation_id,),
        "relations": (),
        "change_type": ChangeType.NEW,
        "revision": 1,
    }
    values.update(overrides)
    return Incident(**values)


def test_observation_round_trip_covers_every_field_and_is_deterministic():
    observation = make_observation()
    payload = observation.to_dict()
    assert payload["schema_version"] == 1
    assert payload["event_at"] == NOW
    assert payload["centroid"] == [139.7, 35.6]
    assert payload["bbox"] == [139.7, 35.6, 139.7, 35.6]
    assert len(payload["content_hash"]) == 64
    assert Observation.from_dict(payload) == observation

    later = make_observation(ingested_at="2026-07-10T21:00:00Z")
    assert later.content_hash == observation.content_hash
    reformatted_raw = make_observation(raw_body="same canonical data, different wire format")
    assert reformatted_raw.raw_fingerprint != observation.raw_fingerprint
    assert reformatted_raw.content_hash == observation.content_hash
    changed = make_observation(headline="M 6.3 — Fixture Coast")
    assert changed.content_hash != observation.content_hash
    assert observation.observation_id == observation_id("usgs_earthquakes", "fixture-1")


def test_incident_round_trip_covers_every_field_enum_and_relation():
    observation = make_observation()
    target = make_incident(observation, "incident.target")
    incident = make_incident(
        observation,
        relations=(Relation(RelationType.RELATED_TO, target.incident_id),),
    )
    payload = incident.to_dict()
    assert payload["relations"] == [
        {"relation_type": "related_to", "target_incident_id": "incident.target"}
    ]
    assert Incident.from_dict(payload) == incident


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-10",
        "2026-07-10T20:00:00",
        "2026-07-10 20:00:00Z",
        "2026-07-10T20:00Z",
        "not-a-date",
        123,
    ],
)
def test_invalid_timestamps_fail_safely(value):
    with pytest.raises((TypeError, ValueError)):
        normalize_timestamp(value)
    with pytest.raises(ValueError):
        normalize_timestamp(None, required=True)
    assert normalize_timestamp(None) is None
    assert normalize_timestamp(dt.datetime(2026, 7, 10, 20, tzinfo=dt.UTC)) == NOW


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [181, 0]},
        {"type": "Point", "coordinates": [0, 91]},
        {"type": "Point", "coordinates": [math.nan, 0]},
        {"type": "LineString", "coordinates": []},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1]]]},
        {"type": "Unknown", "coordinates": [0, 0]},
        {"type": "GeometryCollection", "geometries": ["bad"]},
    ],
)
def test_invalid_geojson_fails_safely(geometry):
    with pytest.raises((TypeError, ValueError)):
        normalize_geometry(geometry)


def test_polygon_and_geometry_collection_centroids_are_computed():
    polygon = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 0]]],
    }
    geometry, centroid, bbox = normalize_geometry(polygon)
    assert geometry == polygon
    assert centroid == (1.0, 0.5)
    assert bbox == (0.0, 0.0, 2.0, 2.0)
    collection = {
        "type": "GeometryCollection",
        "geometries": [
            {"type": "Point", "coordinates": [0, 0]},
            {"type": "Point", "coordinates": [2, 2]},
        ],
    }
    assert normalize_geometry(collection)[1] == (1.0, 1.0)


def test_models_reject_invalid_ids_enums_urls_metrics_text_and_derived_geometry():
    with pytest.raises(ValueError, match="provider_id"):
        observation_id("Bad Provider", "x")
    with pytest.raises(ValueError, match="finite"):
        Metric(float("inf"), "m", "fixture")
    with pytest.raises(ValueError, match="metric"):
        make_observation(metrics={"Bad Key": Metric(1, "m", "fixture")})
    with pytest.raises(ValueError, match="country"):
        make_observation(country_codes=("jpn",))
    with pytest.raises(ValueError, match="HTTP"):
        make_observation(source_url="javascript:alert(1)")
    with pytest.raises(ValueError, match="headline"):
        make_observation(headline="x" * 301)
    with pytest.raises(ValueError, match="centroid"):
        make_observation(centroid=(0, 0))
    with pytest.raises(ValueError, match="content_hash"):
        make_observation(content_hash="b" * 64)
    with pytest.raises(ValueError):
        Observation.from_dict({"unknown": True})
    with pytest.raises(ValueError, match="priority_score"):
        make_incident(make_observation(), priority_score=101)
    with pytest.raises(ValueError, match="priority_score"):
        make_incident(make_observation(), priority_score=1.5)
    with pytest.raises(ValueError, match="rule_version"):
        make_incident(make_observation(), priority_components={"severity": 1})
    with pytest.raises(ValueError, match="revision"):
        make_incident(make_observation(), revision=0)
    with pytest.raises(ValueError, match="observation IDs"):
        make_incident(make_observation(), observation_ids=())
    with pytest.raises(ValueError, match="identity"):
        make_incident(make_observation(), incident_id="bad id")


def test_model_type_and_integrity_guards_cover_strict_deserialization():
    observation = make_observation()
    redacted = make_observation(
        "sensitive-source-url",
        source_url="https://example.test/evidence?token=do-not-store&view=full#api_key=hidden",
    )
    assert "do-not-store" not in redacted.source_url
    assert "hidden" not in redacted.source_url
    assert "view=full" in redacted.source_url
    with pytest.raises(ValueError, match="schema"):
        Observation.from_dict({**observation.to_dict(), "schema_version": 2})
    with pytest.raises(ValueError, match="observation_id"):
        Observation.from_dict({**observation.to_dict(), "observation_id": "bad id"})
    with pytest.raises(ValueError, match="provider identity"):
        Observation.from_dict({**observation.to_dict(), "observation_id": "other:123"})
    with pytest.raises(ValueError, match="raw_fingerprint"):
        Observation.from_dict({**observation.to_dict(), "raw_fingerprint": "bad"})
    with pytest.raises(TypeError, match="raw_body"):
        Observation.create(
            provider_id="usgs_earthquakes",
            provider_record_id="bad-raw",
            raw_body=object(),
            kind=EventKind.EARTHQUAKE,
            headline="Fixture",
            summary="",
            status=Status.UNKNOWN,
            severity=Severity.UNKNOWN,
            urgency=Urgency.UNKNOWN,
            certainty=Certainty.UNKNOWN,
            ingested_at=NOW,
        )
    with pytest.raises(TypeError, match="observation must"):
        Observation.from_dict([])
    with pytest.raises(TypeError, match="incident must"):
        Incident.from_dict([])
    with pytest.raises(ValueError, match="fields differ"):
        Incident.from_dict({"incident_id": "only"})
    with pytest.raises(ValueError, match="bbox"):
        make_observation(bbox=(0, 0, 1, 1))
    with pytest.raises(TypeError, match="metrics"):
        make_observation(metrics=[])
    with pytest.raises(TypeError, match="country_codes"):
        make_observation(country_codes="JP")
    with pytest.raises(TypeError, match="scalar"):
        Metric([], "unit", "fixture")
    with pytest.raises(ValueError, match="500"):
        Metric("x" * 501, "unit", "fixture")
    with pytest.raises(ValueError, match="metric must contain"):
        Metric.from_dict({"value": 1})
    with pytest.raises(ValueError, match="relation target"):
        Relation(RelationType.RELATED_TO, "bad id")


def test_geometry_and_timestamp_edge_type_guards():
    with pytest.raises(ValueError, match="invalid RFC"):
        normalize_timestamp("2026-13-40T20:00:00Z")
    with pytest.raises(ValueError, match="timezone"):
        normalize_timestamp(dt.datetime(2026, 7, 10, 20))
    with pytest.raises(TypeError, match="geometry"):
        normalize_geometry([])
    with pytest.raises(ValueError, match="geometries"):
        normalize_geometry({"type": "GeometryCollection", "geometries": {}})
    assert normalize_geometry(None) == (None, None, None)
    assert normalize_geometry({"type": "GeometryCollection", "geometries": []}) == (
        {"type": "GeometryCollection", "geometries": []}, None, None,
    )
    with pytest.raises(ValueError, match="positions"):
        normalize_geometry({"type": "Point", "coordinates": [1]})
    with pytest.raises(ValueError, match="numeric"):
        normalize_geometry({"type": "Point", "coordinates": [True, 1]})
    with pytest.raises(ValueError, match="numeric"):
        normalize_geometry({"type": "Point", "coordinates": ["x", 1]})
    with pytest.raises(ValueError, match="numeric"):
        normalize_geometry({"type": "Point", "coordinates": ["1", 1]})
    with pytest.raises(ValueError, match="at least two"):
        normalize_geometry({"type": "LineString", "coordinates": [[1, 1]]})
    with pytest.raises(ValueError, match="closed"):
        normalize_geometry(
            {
                "type": "Polygon",
                "coordinates": [[[0, 0, 1], [1, 0], [1, 1], [0, 0, 2]]],
            }
        )


def test_incident_sequence_version_and_component_guards():
    observation = make_observation()
    with pytest.raises(TypeError, match="observation_ids"):
        make_incident(observation, observation_ids=observation.observation_id)
    with pytest.raises(TypeError, match="relations"):
        make_incident(observation, relations={})
    with pytest.raises(ValueError, match="revision"):
        make_incident(observation, revision=True)
    with pytest.raises(ValueError, match="schema"):
        make_incident(observation, schema_version=True)
    with pytest.raises(ValueError, match="rule_version"):
        make_incident(observation, priority_components={"rule_version": ""})
    with pytest.raises(ValueError, match="500"):
        make_incident(
            observation,
            priority_components={"rule_version": "1", "explanation": "x" * 501},
        )


def test_storage_round_trip_restart_relations_revisions_and_pragmas(tmp_path):
    path = tmp_path / "foglight.sqlite3"
    store = ObservationStore(path)
    store.register_provider("usgs_earthquakes", {"tier": 1})
    observation = make_observation()
    assert store.upsert_observation(observation) is True
    assert store.upsert_observation(observation) is False
    assert store.get_observation(observation.observation_id) == observation
    assert store.query_bbox(130, 30, 150, 40) == [observation]
    assert store.query_bbox(-10, -10, 10, 10) == []
    reclassified = make_observation(kind=EventKind.NATURAL_EVENT)
    assert reclassified.observation_id == observation.observation_id
    assert store.upsert_observation(reclassified) is True
    with store.transaction() as connection:
        assert connection.execute(
            "SELECT kind FROM observations WHERE observation_id=?",
            (observation.observation_id,),
        ).fetchone()[0] == EventKind.NATURAL_EVENT.value
    assert store.upsert_observation(observation) is True

    target = make_incident(observation, "incident.target")
    source = make_incident(
        observation,
        relations=(Relation(RelationType.RELATED_TO, target.incident_id),),
    )
    store.upsert_incident(target)
    store.upsert_incident(source)
    with pytest.raises(ValueError, match="revision already exists"):
        store.upsert_incident(make_incident(observation, headline="Changed without revision"))
    revised_target = make_incident(
        observation,
        "incident.target",
        revision=2,
        change_type=ChangeType.UPDATED,
        first_seen_at="2026-07-09T20:00:00Z",
    )
    store.upsert_incident(revised_target)
    with pytest.raises(ValueError, match="move backwards"):
        store.upsert_incident(target)
    store.update_source_health(
        "usgs_earthquakes", "live", NOW, latency_ms=12.5, detail="fixture"
    )
    with contextlib.closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM source_health").fetchone()[0] == "live"
        assert connection.execute(
            "SELECT first_seen_at FROM incidents WHERE incident_id='incident.target'"
        ).fetchone()[0] == "2026-07-09T20:00:00Z"
    with store.transaction() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    restarted = ObservationStore(path)
    assert restarted.get_observation(observation.observation_id) == observation


def test_incident_search_index_tracks_updates_and_deletes(tmp_path):
    store = ObservationStore(tmp_path / "search-sync.sqlite3")
    store.register_provider("usgs_earthquakes", {"tier": 1})
    observation = make_observation()
    store.upsert_observation(observation)
    original = make_incident(observation, headline="Original coastal shaking")
    store.upsert_incident(original)
    assert [item.incident_id for item in store.search_incidents("original coast")] == [
        original.incident_id
    ]
    assert [item.incident_id for item in store.search_incidents("fixture coast")] == [
        original.incident_id
    ]

    updated = make_incident(
        observation,
        headline="Revised inland shaking",
        revision=2,
        change_type=ChangeType.UPDATED,
    )
    store.upsert_incident(updated)
    assert store.search_incidents("original") == []
    assert [item.incident_id for item in store.search_incidents("revised inland")] == [
        updated.incident_id
    ]
    with store.transaction(immediate=True) as connection:
        connection.execute("DELETE FROM incidents WHERE incident_id=?", (updated.incident_id,))
    assert store.search_incidents("revised") == []


def test_storage_validation_no_geometry_and_rtree_detection_failures(tmp_path):
    with pytest.raises(ValueError, match="busy_timeout"):
        ObservationStore(tmp_path / "bad-timeout.sqlite3", busy_timeout_ms=1)
    with pytest.raises(ValueError, match="caps"):
        ObservationStore(tmp_path / "bad-cap.sqlite3", max_observations=0)

    class NoRtree:
        def execute(self, _statement):
            raise sqlite3.OperationalError("no rtree")

    assert ObservationStore._initialize_rtree(NoRtree()) is False

    store = ObservationStore(tmp_path / "validation.sqlite3")
    with pytest.raises(TypeError, match="metadata"):
        store.register_provider("fixture", [])
    with pytest.raises(ValueError, match="provider_id"):
        store.register_provider("Bad Provider", {})
    with pytest.raises(ValueError):
        store.register_provider("fixture", {"bad": float("nan")})
    with pytest.raises(ValueError, match="64 KiB"):
        store.register_provider("fixture", {"oversized": "x" * (64 * 1024)})
    store.register_provider("usgs_earthquakes", {})
    no_geometry = make_observation("no-geometry", geometry=None)
    assert store.upsert_observation(no_geometry)
    assert store.get_observation(no_geometry.observation_id) == no_geometry
    with pytest.raises(TypeError, match="canonical"):
        store.upsert_observation({})
    with pytest.raises(TypeError, match="canonical"):
        store.upsert_incident({})
    with pytest.raises(ValueError, match="bounding box"):
        store.query_bbox(10, 10, -10, -10)
    with pytest.raises(ValueError, match="health"):
        store.update_source_health("usgs_earthquakes", "bad", NOW)
    with pytest.raises(ValueError, match="negative"):
        store.update_source_health("usgs_earthquakes", "live", NOW, latency_ms=-1)
    with pytest.raises(TypeError, match="numeric"):
        store.update_source_health("usgs_earthquakes", "live", NOW, latency_ms="slow")
    with pytest.raises(ValueError, match="finite"):
        store.update_source_health("usgs_earthquakes", "live", NOW, latency_ms=float("nan"))
    with pytest.raises(ValueError, match="retain_days"):
        store.enforce_retention(retain_days=0)


def test_fallback_spatial_index_and_transaction_rollback(tmp_path, monkeypatch):
    monkeypatch.setattr(ObservationStore, "_initialize_rtree", staticmethod(lambda _connection: False))
    store = ObservationStore(tmp_path / "fallback.sqlite3")
    assert store.rtree_enabled is False
    store.register_provider("usgs_earthquakes", {})
    observation = make_observation()
    store.upsert_observation(observation)
    assert store.query_bbox(139, 35, 140, 36) == [observation]

    with pytest.raises(RuntimeError):
        with store.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO providers VALUES (?, ?, ?)", ("rolled_back", "{}", NOW)
            )
            raise RuntimeError("rollback")
    with contextlib.closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM providers WHERE provider_id='rolled_back'"
        ).fetchone()[0] == 0


def test_rtree_is_backfilled_after_fallback_and_retention_removes_entries(tmp_path, monkeypatch):
    original = ObservationStore._initialize_rtree
    monkeypatch.setattr(
        ObservationStore,
        "_initialize_rtree",
        staticmethod(lambda _connection: False),
    )
    path = tmp_path / "rtree-backfill.sqlite3"
    fallback = ObservationStore(path, max_observations=2)
    fallback.register_provider("usgs_earthquakes", {})
    observations = []
    for index in range(3):
        observation = make_observation(
            f"backfill-{index}",
            ingested_at=f"2026-07-10T20:00:0{index}Z",
        )
        observations.append(observation)
        fallback.upsert_observation(observation)
    fallback.upsert_incident(make_incident(observations[0]))

    monkeypatch.setattr(ObservationStore, "_initialize_rtree", staticmethod(original))
    restored = ObservationStore(path, max_observations=2)
    assert restored.rtree_enabled
    assert len(restored.query_bbox(130, 30, 150, 40)) == 3
    restored.enforce_retention(retain_days=365)
    with restored.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM observation_rtree").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0


def test_concurrent_writes_are_serialized_without_lost_observations(tmp_path):
    store = ObservationStore(tmp_path / "concurrent.sqlite3")
    store.register_provider("usgs_earthquakes", {})

    def write(index):
        return store.upsert_observation(make_observation(f"concurrent-{index}"))

    seed = make_observation("concurrent-seed")
    assert store.upsert_observation(seed)

    def read(_index):
        return store.get_observation(seed.observation_id) == seed

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        operations = [pool.submit(write, index) for index in range(100)]
        operations.extend(pool.submit(read, index) for index in range(100))
        assert all(operation.result() for operation in operations)
    with contextlib.closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 101


def test_migration_failure_rolls_back_without_quarantining_valid_database(tmp_path, monkeypatch):
    path = tmp_path / "migration.sqlite3"
    monkeypatch.setattr(
        storage,
        "MIGRATIONS",
        {1: "CREATE TABLE staged(value INTEGER); INSERT INTO missing VALUES (1);"},
    )
    with pytest.raises(sqlite3.OperationalError):
        ObservationStore(path, recover_corruption=True)
    assert not list(tmp_path.glob("*.corrupt-*"))
    with contextlib.closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='staged'"
        ).fetchone()[0] == 0


def test_search_migration_tolerates_a_structurally_valid_legacy_row_with_bad_json(
    tmp_path, monkeypatch
):
    path = tmp_path / "legacy-bad-document.sqlite3"
    with monkeypatch.context() as context:
        context.setattr(storage, "SCHEMA_VERSION", 4)
        context.setattr(
            storage, "MIGRATIONS", {key: value for key, value in storage.MIGRATIONS.items() if key <= 4}
        )
        ObservationStore(path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO incidents(incident_id, kind, status, priority_score, first_seen_at, "
            "last_changed_at, last_observed_at, revision, document_json, lane) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("incident:legacy:bad", "earthquake", "active", 1, NOW, NOW, NOW, 1,
             "not-json", "hazards"),
        )
        connection.commit()

    ObservationStore(path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 6
        assert connection.execute(
            "SELECT headline FROM incident_search WHERE incident_id='incident:legacy:bad'"
        ).fetchone()[0] == ""


def test_corrupt_database_is_quarantined_and_rebuilt_without_other_state(tmp_path):
    path = tmp_path / "foglight.sqlite3"
    settings = tmp_path / "settings.json"
    settings.write_text('{"keep": true}', encoding="utf-8")
    path.write_bytes(b"not a sqlite database")
    store = ObservationStore(path)
    assert store.last_quarantine and store.last_quarantine.read_bytes() == b"not a sqlite database"
    assert settings.read_text(encoding="utf-8") == '{"keep": true}'
    assert store.get_observation("missing") is None

    second = tmp_path / "no-recovery.sqlite3"
    second.write_bytes(b"also corrupt")
    with pytest.raises(storage.CorruptDatabaseError):
        ObservationStore(second, recover_corruption=False)


def test_newer_schema_version_fails_without_corruption_quarantine(tmp_path):
    path = tmp_path / "future.sqlite3"
    ObservationStore(path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (storage.SCHEMA_VERSION + 1, NOW),
        )
        connection.commit()
    with pytest.raises(sqlite3.DatabaseError, match="unexpected schema"):
        ObservationStore(path)
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_retention_dry_run_and_apply_respect_age_and_count_caps(tmp_path):
    store = ObservationStore(
        tmp_path / "retention.sqlite3", max_observations=2, max_bytes=1024 * 1024
    )
    store.register_provider("usgs_earthquakes", {})
    old = "2020-01-01T00:00:00Z"
    for index in range(4):
        store.upsert_observation(
            make_observation(
                f"retention-{index}",
                ingested_at=old if index == 0 else f"2026-07-10T20:00:0{index}Z",
            )
        )
    dry = store.enforce_retention(retain_days=365, dry_run=True)
    assert dry.expired_observations == 1
    assert dry.overflow_observations == 1
    assert dry.size_cap_observations == 0
    assert dry.size_cap_satisfied
    assert dry.max_bytes == store.max_bytes
    assert dry.deleted_observations == 0
    applied = store.enforce_retention(retain_days=365)
    assert applied.deleted_observations == 2
    with contextlib.closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_retention_enforces_database_size_cap_and_reports_size_evictions(tmp_path):
    store = ObservationStore(
        tmp_path / "size-cap.sqlite3",
        max_observations=10_000,
        max_bytes=1024 * 1024,
    )
    store.register_provider("usgs_earthquakes", {})
    for index in range(700):
        store.upsert_observation(
            make_observation(
                f"large-{index}",
                summary=f"{index:04d}-" + ("x" * 3900),
                ingested_at=f"2026-07-{1 + (index % 9):02d}T20:00:00Z",
            )
        )
    assert store.database_bytes() > store.max_bytes

    dry = store.enforce_retention(retain_days=365, dry_run=True)
    assert not dry.size_cap_satisfied
    assert dry.before_bytes == dry.after_bytes
    with store.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 700

    report = store.enforce_retention(retain_days=365)

    assert report.size_cap_observations > 0
    assert report.deleted_observations == report.size_cap_observations
    assert report.after_bytes <= store.max_bytes
    assert report.size_cap_satisfied
    with store.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM observation_rtree").fetchone()[0] == (
            connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        )

    backup_path = store.backup(tmp_path / "post-retention.sqlite3")
    recovered = ObservationStore(backup_path, recover_corruption=False)
    assert recovered.database_bytes() <= recovered.max_bytes
    with recovered.transaction() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None


def test_database_backup_restore_and_pre_restore_safety_copy(tmp_path):
    store = ObservationStore(tmp_path / "live.sqlite3")
    store.register_provider("usgs_earthquakes", {})
    first = make_observation("before-backup")
    store.upsert_observation(first)
    backup_path = store.backup(tmp_path / "backups" / "known-good.sqlite3")

    second = make_observation("after-backup", ingested_at="2026-07-10T21:00:00Z")
    store.upsert_observation(second)
    safety_path = store.restore_from_backup(backup_path)

    assert store.get_observation(first.observation_id) == first
    assert store.get_observation(second.observation_id) is None
    safety = ObservationStore(safety_path, recover_corruption=False)
    assert safety.get_observation(second.observation_id) == second


def test_restore_rejects_corruption_without_changing_live_data(tmp_path):
    store = ObservationStore(tmp_path / "live.sqlite3")
    store.register_provider("usgs_earthquakes", {})
    observation = make_observation("keep-live")
    store.upsert_observation(observation)
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a database")

    with pytest.raises(storage.CorruptDatabaseError):
        store.restore_from_backup(corrupt)
    assert store.get_observation(observation.observation_id) == observation
    assert not list(tmp_path.glob("*.pre-restore-*"))


def test_failed_restore_rolls_back_to_safety_copy(tmp_path, monkeypatch):
    store = ObservationStore(tmp_path / "live.sqlite3")
    store.register_provider("usgs_earthquakes", {})
    before = make_observation("backup-version")
    store.upsert_observation(before)
    backup_path = store.backup(tmp_path / "known-good.sqlite3")
    after = make_observation("live-version", ingested_at="2026-07-10T21:00:00Z")
    store.upsert_observation(after)
    original_initialize = store._initialize
    calls = 0

    def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("post-restore initialization failed")
        return original_initialize()

    monkeypatch.setattr(store, "_initialize", fail_once)
    with pytest.raises(RuntimeError, match="initialization failed"):
        store.restore_from_backup(backup_path)

    assert store.get_observation(before.observation_id) == before
    assert store.get_observation(after.observation_id) == after


def test_retention_removes_dangling_relations_and_records_source_revision(tmp_path):
    store = ObservationStore(tmp_path / "relation-retention.sqlite3")
    store.register_provider("usgs_earthquakes", {})
    expired_observation = make_observation(
        "expired-target", ingested_at="2020-01-01T00:00:00Z"
    )
    current_observation = make_observation(
        "current-source", ingested_at="2026-07-10T20:00:00Z"
    )
    store.upsert_observation(expired_observation)
    store.upsert_observation(current_observation)
    target = make_incident(expired_observation, "incident.expired")
    source = make_incident(
        current_observation,
        "incident.current",
        relations=(Relation(RelationType.RELATED_TO, target.incident_id),),
    )
    store.upsert_incident(target)
    store.upsert_incident(source)

    store.enforce_retention(retain_days=365)

    assert store.get_incident(target.incident_id) is None
    updated = store.get_incident(source.incident_id)
    assert updated.relations == ()
    assert updated.revision == 2
    assert updated.change_type is ChangeType.UPDATED
    assert [item["revision"] for item in store.timeline(source.incident_id)] == [2, 1]


def test_ten_thousand_observation_fallback_query_meets_local_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(ObservationStore, "_initialize_rtree", staticmethod(lambda _connection: False))
    store = ObservationStore(
        tmp_path / "performance.sqlite3", max_observations=20_000, max_bytes=64 * 1024 * 1024
    )
    store.register_provider("usgs_earthquakes", {})
    observations = [
        make_observation(
            f"perf-{index}",
            geometry={
                "type": "Point",
                "coordinates": [float(index % 200) - 100, float((index // 200) % 100) - 50],
            },
        )
        for index in range(10_000)
    ]
    rows = []
    spatial = []
    for observation in observations:
        west, south, east, north = observation.bbox
        rows.append(
            (
                observation.observation_id, observation.provider_id,
                observation.provider_record_id, observation.kind.value,
                observation.status.value, observation.event_at, observation.ingested_at,
                observation.content_hash, west, south, east, north,
                json.dumps(observation.to_dict(), separators=(",", ":")),
            )
        )
        spatial.append((observation.observation_id, west, east, south, north))
    with store.transaction(immediate=True) as connection:
        connection.executemany(
            "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        connection.executemany("INSERT INTO observation_spatial VALUES (?, ?, ?, ?, ?)", spatial)
    started = time.perf_counter()
    result = store.query_bbox_ids(-10, -10, 10, 10, limit=100)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result
    assert elapsed_ms < 50, f"bbox query took {elapsed_ms:.1f} ms"
    assert store.database_bytes() <= store.max_bytes
