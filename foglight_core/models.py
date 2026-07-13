"""Versioned canonical observation and incident models."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
METRIC_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class EventKind(str, Enum):
    EARTHQUAKE = "earthquake"
    WEATHER_ALERT = "weather_alert"
    TROPICAL_CYCLONE = "tropical_cyclone"
    TSUNAMI = "tsunami"
    VOLCANO = "volcano"
    WILDFIRE = "wildfire"
    NATURAL_EVENT = "natural_event"
    DISASTER = "disaster"
    DISASTER_DECLARATION = "disaster_declaration"
    CONFLICT_REPORT = "conflict_report"
    HUMANITARIAN_REPORT = "humanitarian_report"
    AIRCRAFT = "aircraft"
    AVIATION_HAZARD = "aviation_hazard"
    MARINE_OBSERVATION = "marine_observation"
    WATER_LEVEL = "water_level"
    FIREBALL = "fireball"
    SPACE_WEATHER = "space_weather"
    ORBITAL_POSITION = "orbital_position"
    MARKET_SNAPSHOT = "market_snapshot"
    TECHNOLOGY_ACTIVITY = "technology_activity"
    NEWS_ITEM = "news_item"
    UNKNOWN = "unknown"


class Status(str, Enum):
    ACTIVE = "active"
    UPDATED = "updated"
    ENDED = "ended"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    EXTREME = "Extreme"
    SEVERE = "Severe"
    MODERATE = "Moderate"
    MINOR = "Minor"
    UNKNOWN = "Unknown"


class Urgency(str, Enum):
    IMMEDIATE = "Immediate"
    EXPECTED = "Expected"
    FUTURE = "Future"
    PAST = "Past"
    UNKNOWN = "Unknown"


class Certainty(str, Enum):
    OBSERVED = "Observed"
    LIKELY = "Likely"
    POSSIBLE = "Possible"
    UNLIKELY = "Unlikely"
    UNKNOWN = "Unknown"


class ChangeType(str, Enum):
    NEW = "new"
    ESCALATED = "escalated"
    DOWNGRADED = "downgraded"
    UPDATED = "updated"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    SOURCE_LOST = "source_lost"


class RelationType(str, Enum):
    RELATED_TO = "related_to"
    CAUSED_BY = "caused_by"
    AFFECTS = "affects"
    COVERAGE_OF = "coverage_of"


def normalize_timestamp(value: str | dt.datetime | None, *, required=False) -> str | None:
    if value is None:
        if required:
            raise ValueError("timestamp is required")
        return None
    if isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
            text,
        ):
            raise ValueError("timestamp must be RFC 3339 with a timezone")
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("invalid RFC 3339 timestamp") from error
    elif isinstance(value, dt.datetime):
        parsed = value
    else:
        raise TypeError("timestamp must be a string, datetime, or null")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    parsed = parsed.astimezone(dt.UTC)
    timespec = "microseconds" if parsed.microsecond else "seconds"
    return parsed.isoformat(timespec=timespec).replace("+00:00", "Z")


def _text(value: object, name: str, maximum: int, *, required=False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    clean = value.strip()
    if required and not clean:
        raise ValueError(f"{name} is required")
    if len(clean) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return clean


def _safe_url(value: str | None) -> str | None:
    if value is None:
        return None
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    clean = _text(value, "source_url", 2048, required=True)
    parsed = urlsplit(clean)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("source_url must be HTTP(S)")
    if parsed.username or parsed.password:
        raise ValueError("source_url cannot contain credentials")
    query = [
        (
            key,
            "<redacted>" if re.search(
                r"(?:api[_-]?key|token|secret|password|appid)", key, re.I
            ) else query_value,
        )
        for key, query_value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    fragment = (
        "<redacted>"
        if re.search(r"(?:api[_-]?key|token|secret|password|appid)", parsed.fragment, re.I)
        else parsed.fragment
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), fragment))


def _coordinates(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    geometry_type = geometry.get("type")
    if geometry_type == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list):
            raise ValueError("GeometryCollection.geometries must be a list")
        output = []
        for child in geometries:
            if not isinstance(child, dict):
                raise ValueError("geometry collection members must be objects")
            output.extend(_coordinates(child))
        return output
    dimensions = {
        "Point": 0,
        "MultiPoint": 1,
        "LineString": 1,
        "MultiLineString": 2,
        "Polygon": 2,
        "MultiPolygon": 3,
    }
    if geometry_type not in dimensions:
        raise ValueError(f"unsupported GeoJSON geometry type: {geometry_type!r}")
    coordinates = geometry.get("coordinates")

    def walk(value, depth):
        if depth:
            if not isinstance(value, list) or not value:
                raise ValueError("GeoJSON coordinate arrays cannot be empty")
            output = []
            for child in value:
                output.extend(walk(child, depth - 1))
            return output
        if not isinstance(value, list) or len(value) < 2:
            raise ValueError("GeoJSON positions require longitude and latitude")
        lon, lat = value[:2]
        if isinstance(lon, bool) or isinstance(lat, bool):
            raise ValueError("coordinates must be numeric")
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            raise ValueError("coordinates must be numeric")
        lon_value, lat_value = float(lon), float(lat)
        if not (math.isfinite(lon_value) and math.isfinite(lat_value)):
            raise ValueError("coordinates must be finite")
        if not (-180 <= lon_value <= 180 and -90 <= lat_value <= 90):
            raise ValueError("coordinates are outside RFC 7946 ranges")
        return [(lon_value, lat_value)]

    points = walk(coordinates, dimensions[geometry_type])
    lines = []
    if geometry_type == "LineString":
        lines = [coordinates]
    elif geometry_type == "MultiLineString":
        lines = coordinates
    if any(len(line) < 2 for line in lines):
        raise ValueError("line strings require at least two positions")
    if geometry_type in ("Polygon", "MultiPolygon"):
        polygons = coordinates if geometry_type == "MultiPolygon" else [coordinates]
        for polygon in polygons:
            for ring in polygon:
                if len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError("polygon rings must be closed with at least four positions")
    return points


def normalize_geometry(
    geometry: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, tuple[float, float] | None, tuple[float, float, float, float] | None]:
    if geometry is None:
        return None, None, None
    if not isinstance(geometry, dict):
        raise TypeError("geometry must be a GeoJSON object or null")
    normalized = json.loads(json.dumps(geometry, allow_nan=False))
    points = _coordinates(normalized)
    if not points:
        return normalized, None, None
    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    centroid = (sum(longitudes) / len(longitudes), sum(latitudes) / len(latitudes))
    bbox = (min(longitudes), min(latitudes), max(longitudes), max(latitudes))
    return normalized, centroid, bbox


@dataclass(frozen=True, slots=True)
class Metric:
    value: int | float | str | bool
    unit: str
    provenance: str

    def __post_init__(self):
        if not isinstance(self.value, (int, float, str, bool)):
            raise TypeError("metric value must be a scalar")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("metric values must be finite")
        if isinstance(self.value, str) and len(self.value) > 500:
            raise ValueError("metric text exceeds 500 characters")
        object.__setattr__(self, "unit", _text(self.unit, "metric unit", 32, required=True))
        object.__setattr__(
            self, "provenance", _text(self.provenance, "metric provenance", 200, required=True)
        )

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"value", "unit", "provenance"}:
            raise ValueError("metric must contain value, unit, and provenance")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class Relation:
    relation_type: RelationType
    target_incident_id: str

    def __post_init__(self):
        object.__setattr__(self, "relation_type", RelationType(self.relation_type))
        if not ID_PATTERN.fullmatch(self.target_incident_id):
            raise ValueError("invalid relation target incident id")

    def to_dict(self):
        return {"relation_type": self.relation_type.value, "target_incident_id": self.target_incident_id}


def observation_id(provider_id: str, provider_record_id: str) -> str:
    provider = _text(provider_id, "provider_id", 64, required=True)
    record = _text(provider_record_id, "provider_record_id", 512, required=True)
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", provider):
        raise ValueError("provider_id must be a lowercase registry id")
    digest = hashlib.sha256(record.encode()).hexdigest()[:24]
    return f"{provider}:{digest}"


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    provider_id: str
    provider_record_id: str
    kind: EventKind
    headline: str
    summary: str
    status: Status
    severity: Severity
    urgency: Urgency
    certainty: Certainty
    ingested_at: str
    event_at: str | None = None
    effective_at: str | None = None
    expires_at: str | None = None
    source_updated_at: str | None = None
    geometry: dict[str, Any] | None = None
    centroid: tuple[float, float] | None = None
    bbox: tuple[float, float, float, float] | None = None
    location_name: str = ""
    country_codes: tuple[str, ...] = ()
    metrics: dict[str, Metric] = field(default_factory=dict)
    source_url: str | None = None
    content_hash: str = ""
    raw_fingerprint: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported observation schema version")
        if not ID_PATTERN.fullmatch(self.observation_id):
            raise ValueError("invalid observation_id")
        expected_id = observation_id(self.provider_id, self.provider_record_id)
        if self.observation_id != expected_id:
            raise ValueError("observation_id does not match provider identity")
        object.__setattr__(self, "kind", EventKind(self.kind))
        object.__setattr__(self, "status", Status(self.status))
        object.__setattr__(self, "severity", Severity(self.severity))
        object.__setattr__(self, "urgency", Urgency(self.urgency))
        object.__setattr__(self, "certainty", Certainty(self.certainty))
        object.__setattr__(self, "headline", _text(self.headline, "headline", 300, required=True))
        object.__setattr__(self, "summary", _text(self.summary, "summary", 4000))
        object.__setattr__(self, "location_name", _text(self.location_name, "location_name", 300))
        for name in ("event_at", "effective_at", "expires_at", "source_updated_at"):
            object.__setattr__(self, name, normalize_timestamp(getattr(self, name)))
        object.__setattr__(self, "ingested_at", normalize_timestamp(self.ingested_at, required=True))
        geometry, centroid, bbox = normalize_geometry(self.geometry)
        if self.centroid is not None and tuple(self.centroid) != centroid:
            raise ValueError("centroid does not match geometry")
        if self.bbox is not None and tuple(self.bbox) != bbox:
            raise ValueError("bbox does not match geometry")
        object.__setattr__(self, "geometry", geometry)
        object.__setattr__(self, "centroid", centroid)
        object.__setattr__(self, "bbox", bbox)
        if not isinstance(self.country_codes, (tuple, list)):
            raise TypeError("country_codes must be a sequence")
        countries = tuple(dict.fromkeys(self.country_codes))
        if any(not COUNTRY_PATTERN.fullmatch(code) for code in countries):
            raise ValueError("country codes must be uppercase ISO alpha-2 values")
        object.__setattr__(self, "country_codes", countries)
        if not isinstance(self.metrics, dict):
            raise TypeError("metrics must be an object")
        metrics = {
            key: value if isinstance(value, Metric) else Metric.from_dict(value)
            for key, value in self.metrics.items()
        }
        if any(not METRIC_KEY_PATTERN.fullmatch(key) for key in metrics):
            raise ValueError("invalid metric key")
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "source_url", _safe_url(self.source_url))
        if not re.fullmatch(r"[0-9a-f]{64}", self.raw_fingerprint):
            raise ValueError("raw_fingerprint must be a SHA-256 hex digest")
        calculated = _hash_payload(self._content_payload())
        if self.content_hash and self.content_hash != calculated:
            raise ValueError("content_hash does not match observation content")
        object.__setattr__(self, "content_hash", calculated)

    def _content_payload(self):
        payload = self.to_dict()
        payload.pop("content_hash", None)
        payload.pop("ingested_at", None)
        payload.pop("raw_fingerprint", None)
        return payload

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "provider_id": self.provider_id,
            "provider_record_id": self.provider_record_id,
            "kind": self.kind.value,
            "headline": self.headline,
            "summary": self.summary,
            "status": self.status.value,
            "severity": self.severity.value,
            "urgency": self.urgency.value,
            "certainty": self.certainty.value,
            "event_at": self.event_at,
            "effective_at": self.effective_at,
            "expires_at": self.expires_at,
            "source_updated_at": self.source_updated_at,
            "ingested_at": self.ingested_at,
            "geometry": self.geometry,
            "centroid": list(self.centroid) if self.centroid else None,
            "bbox": list(self.bbox) if self.bbox else None,
            "location_name": self.location_name,
            "country_codes": list(self.country_codes),
            "metrics": {key: value.to_dict() for key, value in sorted(self.metrics.items())},
            "source_url": self.source_url,
            "content_hash": self.content_hash,
            "raw_fingerprint": self.raw_fingerprint,
        }

    @classmethod
    def create(cls, *, provider_id, provider_record_id, raw_body: bytes | str, **values):
        raw = raw_body.encode() if isinstance(raw_body, str) else raw_body
        if not isinstance(raw, bytes):
            raise TypeError("raw_body must be bytes or text")
        return cls(
            observation_id=observation_id(provider_id, provider_record_id),
            provider_id=provider_id,
            provider_record_id=provider_record_id,
            raw_fingerprint=hashlib.sha256(raw).hexdigest(),
            **values,
        )

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise TypeError("observation must be an object")
        allowed = {field.name for field in dataclasses.fields(cls)}
        if set(value) != allowed:
            raise ValueError(f"observation fields differ: {sorted(set(value) ^ allowed)}")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class Incident:
    incident_id: str
    kind: EventKind
    headline: str
    summary: str
    status: Status
    severity: Severity
    urgency: Urgency
    certainty: Certainty
    priority_score: int
    priority_components: dict[str, int | float | str]
    first_seen_at: str
    last_changed_at: str
    last_observed_at: str
    observation_ids: tuple[str, ...]
    change_type: ChangeType
    revision: int
    geometry: dict[str, Any] | None = None
    centroid: tuple[float, float] | None = None
    bbox: tuple[float, float, float, float] | None = None
    relations: tuple[Relation, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if (
            type(self.schema_version) is not int
            or self.schema_version != SCHEMA_VERSION
            or not isinstance(self.incident_id, str)
            or not ID_PATTERN.fullmatch(self.incident_id)
        ):
            raise ValueError("invalid incident identity or schema version")
        for name, enum in (
            ("kind", EventKind), ("status", Status), ("severity", Severity),
            ("urgency", Urgency), ("certainty", Certainty), ("change_type", ChangeType),
        ):
            object.__setattr__(self, name, enum(getattr(self, name)))
        object.__setattr__(self, "headline", _text(self.headline, "headline", 300, required=True))
        object.__setattr__(self, "summary", _text(self.summary, "summary", 4000))
        if type(self.priority_score) is not int or not 0 <= self.priority_score <= 100:
            raise ValueError("priority_score must be an integer in 0..100")
        if not isinstance(self.priority_components, dict) or not self.priority_components:
            raise ValueError("priority_components must be a non-empty object")
        rule_version = self.priority_components.get("rule_version")
        if not isinstance(rule_version, str) or not rule_version.strip() or len(rule_version) > 64:
            raise ValueError("priority_components requires a rule_version")
        for key, value in self.priority_components.items():
            if not isinstance(key, str) or len(key) > 64:
                raise ValueError("invalid priority component key")
            if not isinstance(value, (int, float, str)) or isinstance(value, bool):
                raise TypeError("priority component values must be numeric or text")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("priority component values must be finite")
            if isinstance(value, str) and len(value) > 500:
                raise ValueError("priority component text exceeds 500 characters")
        for name in ("first_seen_at", "last_changed_at", "last_observed_at"):
            object.__setattr__(self, name, normalize_timestamp(getattr(self, name), required=True))
        if self.first_seen_at > self.last_changed_at or self.first_seen_at > self.last_observed_at:
            raise ValueError("incident timestamps are out of order")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be positive")
        if not isinstance(self.observation_ids, (tuple, list)):
            raise TypeError("observation_ids must be a sequence")
        observations = tuple(dict.fromkeys(self.observation_ids))
        if not observations or any(not ID_PATTERN.fullmatch(item) for item in observations):
            raise ValueError("incident requires valid observation IDs")
        object.__setattr__(self, "observation_ids", observations)
        if not isinstance(self.relations, (tuple, list)):
            raise TypeError("relations must be a sequence")
        object.__setattr__(
            self,
            "relations",
            tuple(item if isinstance(item, Relation) else Relation(**item) for item in self.relations),
        )
        geometry, centroid, bbox = normalize_geometry(self.geometry)
        if self.centroid is not None and tuple(self.centroid) != centroid:
            raise ValueError("centroid does not match geometry")
        if self.bbox is not None and tuple(self.bbox) != bbox:
            raise ValueError("bbox does not match geometry")
        object.__setattr__(self, "geometry", geometry)
        object.__setattr__(self, "centroid", centroid)
        object.__setattr__(self, "bbox", bbox)

    def to_dict(self):
        output = dataclasses.asdict(self)
        for name in ("kind", "status", "severity", "urgency", "certainty", "change_type"):
            output[name] = getattr(self, name).value
        output["relations"] = [item.to_dict() for item in self.relations]
        output["centroid"] = list(self.centroid) if self.centroid else None
        output["bbox"] = list(self.bbox) if self.bbox else None
        output["observation_ids"] = list(self.observation_ids)
        return output

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise TypeError("incident must be an object")
        allowed = {field.name for field in dataclasses.fields(cls)}
        if set(value) != allowed:
            raise ValueError(f"incident fields differ: {sorted(set(value) ^ allowed)}")
        return cls(**value)
