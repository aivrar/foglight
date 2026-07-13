"""Bounded coastal context planning for keyless NOAA observation queries."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..jsonfiles import load_bounded_json
from ..models import EventKind, Status
from ..storage import ObservationStore


@dataclass(frozen=True, slots=True)
class CoastalStation:
    station_id: str
    name: str
    state: str
    latitude: float
    longitude: float
    tidal: bool
    great_lakes: bool


@dataclass(frozen=True, slots=True)
class CoastalContext:
    latitude: float
    longitude: float
    radius_km: float
    source: str


def distance_km(left, right):
    lon1, lat1 = map(math.radians, left)
    lon2, lat2 = map(math.radians, right)
    delta_lon, delta_lat = lon2 - lon1, lat2 - lat1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(value)))


def load_coops_stations(path: str | Path) -> tuple[CoastalStation, ...]:
    document = load_bounded_json(path)
    if document.get("schema_version") != 1 or not isinstance(document.get("stations"), list):
        raise ValueError("invalid CO-OPS station catalog")
    output = []
    seen = set()
    for item in document["stations"]:
        if not isinstance(item, dict):
            raise ValueError("invalid CO-OPS station record")
        station_id = str(item.get("id") or "")
        name = str(item.get("name") or "").strip()
        state = str(item.get("state") or "").strip().upper()
        try:
            latitude = float(item["lat"])
            longitude = float(item["lon"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid CO-OPS station coordinates") from error
        if (
            not station_id.isalnum()
            or station_id in seen
            or not name
            or not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            raise ValueError("invalid CO-OPS station record")
        seen.add(station_id)
        output.append(CoastalStation(
            station_id, name[:200], state[:20], latitude, longitude,
            item.get("tidal") is True, item.get("great_lakes") is True,
        ))
    if len(output) < 300:
        raise ValueError("CO-OPS station catalog is unexpectedly incomplete")
    return tuple(sorted(output, key=lambda item: item.station_id))


def _geometry_point(geometry):
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Point":
        if (
            isinstance(coordinates, list)
            and len(coordinates) >= 2
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in coordinates[:2])
        ):
            longitude, latitude = float(coordinates[0]), float(coordinates[1])
            if (
                math.isfinite(longitude)
                and math.isfinite(latitude)
                and -180 <= longitude <= 180
                and -90 <= latitude <= 90
            ):
                return longitude, latitude
        return None
    points = []

    def visit(value):
        if (
            isinstance(value, list)
            and len(value) >= 2
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value[:2])
        ):
            longitude, latitude = float(value[0]), float(value[1])
            if (
                math.isfinite(longitude)
                and math.isfinite(latitude)
                and -180 <= longitude <= 180
                and -90 <= latitude <= 90
            ):
                points.append((longitude, latitude))
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(coordinates)
    if not points:
        return None
    sine = sum(math.sin(math.radians(point[0])) for point in points)
    cosine = sum(math.cos(math.radians(point[0])) for point in points)
    longitude = math.degrees(math.atan2(sine, cosine))
    latitude = sum(point[1] for point in points) / len(points)
    return longitude, latitude


class CoastalContextPlanner:
    """Derive a small deterministic set of NOAA URLs from local active context."""

    def __init__(
        self,
        store: ObservationStore,
        settings_loader: Callable[[], dict[str, Any]],
        stations: tuple[CoastalStation, ...] | list[CoastalStation],
        *,
        max_contexts=6,
        coastal_distance_km=250,
    ):
        if not 1 <= int(max_contexts) <= 12:
            raise ValueError("coastal context cap must be in 1..12")
        self.store = store
        self.settings_loader = settings_loader
        # The water-level request uses the coastal MLLW datum. Great Lakes
        # stations require IGLD and therefore are intentionally out of scope.
        self.stations = tuple(item for item in stations if not item.great_lakes)
        self.max_contexts = int(max_contexts)
        self.coastal_distance_km = max(25.0, min(500.0, float(coastal_distance_km)))

    def _nearest_station(self, longitude, latitude):
        if not self.stations:
            return None, math.inf
        return min(
            (
                (station, distance_km(
                    (longitude, latitude), (station.longitude, station.latitude)
                ))
                for station in self.stations
            ),
            key=lambda item: (item[1], item[0].station_id),
        )

    def contexts(self):
        candidates = []
        try:
            settings = self.settings_loader()
        except Exception:
            settings = {}
        regions = settings.get("watch_regions") if isinstance(settings, dict) else []
        for region in regions if isinstance(regions, list) else []:
            if not isinstance(region, dict) or region.get("enabled") is False:
                continue
            point = _geometry_point(region.get("geometry"))
            if point is None:
                continue
            radius = region.get("radius_km", 100)
            try:
                radius = float(radius)
            except (TypeError, ValueError):
                radius = 100
            candidates.append(CoastalContext(
                point[1], point[0], max(25.0, min(185.2, radius)),
                f"watch:{str(region.get('id') or '')[:120]}",
            ))
        incidents = sorted(
            self.store.list_incidents(limit=1000),
            key=lambda item: (-item.priority_score, item.incident_id),
        )
        for incident in incidents:
            if (
                incident.status not in {Status.ACTIVE, Status.UPDATED}
                or not incident.centroid
                # Contextual measurements must never bootstrap or perpetuate
                # their own upstream polling after the originating watch or
                # independent incident disappears.
                or getattr(incident, "kind", None) in {
                    EventKind.MARINE_OBSERVATION,
                    EventKind.WATER_LEVEL,
                }
            ):
                continue
            candidates.append(CoastalContext(
                incident.centroid[1], incident.centroid[0], 100.0,
                f"incident:{incident.incident_id}",
            ))
        output = []
        for candidate in candidates:
            _station, distance = self._nearest_station(
                candidate.longitude, candidate.latitude
            )
            if distance > self.coastal_distance_km:
                continue
            if any(distance_km(
                (candidate.longitude, candidate.latitude),
                (item.longitude, item.latitude),
            ) <= 50 for item in output):
                continue
            output.append(candidate)
            if len(output) >= self.max_contexts:
                break
        return tuple(output)

    @staticmethod
    def _cardinal(value, positive, negative):
        suffix = positive if value >= 0 else negative
        text = f"{abs(value):.4f}".rstrip("0").rstrip(".")
        return f"{text}{suffix}"

    def urls_for(self, provider_id):
        contexts = self.contexts()
        if provider_id == "ndbc_observations":
            return tuple(
                "https://www.ndbc.noaa.gov/rss/ndbc_obs_search.php?"
                f"lat={self._cardinal(item.latitude, 'N', 'S')}&"
                f"lon={self._cardinal(item.longitude, 'E', 'W')}&"
                f"radius={round(max(25, min(100, item.radius_km / 1.852)))}"
                for item in contexts
            )
        if provider_id == "noaa_coops_water_levels":
            selected = []
            for item in contexts:
                station, distance = self._nearest_station(item.longitude, item.latitude)
                if station is None or distance > self.coastal_distance_km:
                    continue
                if station.station_id not in {value.station_id for value in selected}:
                    selected.append(station)
            return tuple(
                "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?"
                f"date=latest&station={station.station_id}&product=water_level&"
                "datum=MLLW&time_zone=gmt&units=metric&application=Foglight&format=json"
                for station in selected[:self.max_contexts]
            )
        return ()
