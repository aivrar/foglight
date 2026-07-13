import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from foglight_core.models import EventKind, Status
from foglight_core.providers.coastal import (
    CoastalContextPlanner,
    CoastalStation,
    _geometry_point,
    load_coops_stations,
)
from scripts import update_coops_stations

ROOT = Path(__file__).parents[1]
CATALOG = ROOT / "config" / "coops_water_level_stations.v1.json"


class FakeStore:
    def __init__(self, incidents=()):
        self.incidents = tuple(incidents)

    def list_incidents(self, *, limit):
        assert limit == 1000
        return list(self.incidents)


def station(station_id, longitude, latitude, *, great_lakes=False):
    return CoastalStation(
        station_id, station_id, "CA", latitude, longitude, True, great_lakes
    )


def incident(
    incident_id, longitude, latitude, status=Status.ACTIVE, priority=50,
    kind="earthquake",
):
    return SimpleNamespace(
        incident_id=incident_id,
        centroid=(longitude, latitude),
        status=status,
        priority_score=priority,
        kind=kind,
    )


def test_bundled_coops_catalog_is_complete_unique_and_strict(tmp_path):
    stations = load_coops_stations(CATALOG)
    assert len(stations) >= 300
    assert len({item.station_id for item in stations}) == len(stations)
    assert all(-90 <= item.latitude <= 90 for item in stations)
    assert all(-180 <= item.longitude <= 180 for item in stations)

    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 1, "stations": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        load_coops_stations(bad)
    bad.write_text("not-json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_coops_stations(bad)


def test_geometry_center_validates_ranges_and_handles_dateline_polygons():
    assert _geometry_point({"type": "Point", "coordinates": [181, 0]}) is None
    assert _geometry_point({"type": "Point", "coordinates": [0, float("inf")]}) is None
    point = _geometry_point({
        "type": "Polygon",
        "coordinates": [[
            [179, 10], [-179, 10], [-179, 12], [179, 12], [179, 10],
        ]],
    })
    assert abs(abs(point[0]) - 180) < 1
    assert point[1] == pytest.approx(10.8)


def test_planner_is_idle_without_context_and_skips_invalid_disabled_or_inland_regions():
    settings = {"watch_regions": [
        {"id": "global", "enabled": True},
        {"id": "disabled", "enabled": False,
         "geometry": {"type": "Point", "coordinates": [-122.4, 37.8]}},
        {"id": "inland", "enabled": True, "radius_km": 100,
         "geometry": {"type": "Point", "coordinates": [-104.99, 39.74]}},
    ]}
    planner = CoastalContextPlanner(
        FakeStore(), lambda: settings, [station("9414290", -122.4659, 37.8063)]
    )
    assert planner.contexts() == ()
    assert planner.urls_for("ndbc_observations") == ()
    assert planner.urls_for("noaa_coops_water_levels") == ()


def test_saved_regions_precede_incidents_deduplicate_and_cap_contexts():
    stations = [station(str(index), -124 + index, 40) for index in range(8)]
    settings = {"watch_regions": [
        {"id": "saved", "enabled": True, "radius_km": 185.2,
         "geometry": {"type": "Point", "coordinates": [-124, 40]}},
        {"id": "near-duplicate", "enabled": True,
         "geometry": {"type": "Point", "coordinates": [-123.9, 40]}},
    ]}
    incidents = [
        incident(f"i-{index}", -123 + index, 40, priority=100 - index)
        for index in range(8)
    ] + [
        incident("ended", -122, 40, Status.ENDED, 999),
        incident(
            "self-sustaining", -122, 40, priority=1000,
            kind=EventKind.MARINE_OBSERVATION,
        ),
    ]
    planner = CoastalContextPlanner(
        FakeStore(incidents), lambda: settings, stations, max_contexts=6
    )

    contexts = planner.contexts()
    assert len(contexts) == 6
    assert contexts[0].source == "watch:saved"
    assert all(item.source != "watch:near-duplicate" for item in contexts)
    assert all(item.source != "incident:ended" for item in contexts)
    assert all(item.source != "incident:self-sustaining" for item in contexts)


def test_context_urls_are_bounded_keyless_deterministic_and_exclude_great_lakes():
    settings = {"watch_regions": [{
        "id": "bay", "enabled": True, "radius_km": 92.6,
        "geometry": {"type": "Point", "coordinates": [-122.4, 37.8]},
    }]}
    stations = [
        station("9414290", -122.4659, 37.8063),
        station("gl", -122.4, 37.8, great_lakes=True),
    ]
    planner = CoastalContextPlanner(FakeStore(), lambda: settings, stations)

    ndbc = planner.urls_for("ndbc_observations")
    coops = planner.urls_for("noaa_coops_water_levels")
    assert ndbc == (
        "https://www.ndbc.noaa.gov/rss/ndbc_obs_search.php?"
        "lat=37.8N&lon=122.4W&radius=50",
    )
    assert len(coops) == 1
    assert "station=9414290" in coops[0]
    assert "date=latest" in coops[0]
    assert "datum=MLLW" in coops[0]
    assert "station=gl" not in coops[0]
    assert "key=" not in ndbc[0] + coops[0]
    assert planner.urls_for("unknown") == ()


def test_station_updater_parses_official_shape_and_writes_atomically(tmp_path, monkeypatch):
    rows = [
        {
            "id": f"s{index:03}", "name": f"Station {index}", "state": "ca",
            "lat": 30 + index / 1000, "lng": -120 - index / 1000,
            "tidal": True, "greatlakes": False,
        }
        for index in range(300)
    ]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({"stations": rows}).encode()

    monkeypatch.setattr(update_coops_stations.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    document = update_coops_stations.fetch_catalog(attempts=1)
    output = tmp_path / "stations.json"
    update_coops_stations.write_catalog(document, output)

    assert len(document["stations"]) == 300
    assert document["stations"][0]["state"] == "CA"
    assert json.loads(output.read_text(encoding="utf-8")) == document
    assert not output.with_suffix(".json.tmp").exists()
