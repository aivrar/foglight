"""Canonical, side-effect-free normalizers for Foglight's current core sources.

Adapters accept bounded response bodies and return validated Observations. They
never fetch, persist raw payloads, or infer CAP confidence values that a source
did not provide.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

from ..models import (
    Certainty,
    EventKind,
    Metric,
    Observation,
    Severity,
    Status,
    Urgency,
    normalize_timestamp,
)


@dataclass(frozen=True, slots=True)
class DriftDiagnostic:
    """Payload-safe schema drift signal; field names only, never field values."""

    provider_id: str
    code: str
    record_id: str = ""
    fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.code not in {
            "malformed_body",
            "unexpected_root",
            "missing_fields",
            "unknown_fields",
            "invalid_record",
        }:
            raise ValueError("invalid adapter diagnostic code")
        safe_fields = tuple(
            sorted(
                {
                    re.sub(r"[^A-Za-z0-9_.:-]", "?", str(item))[:80]
                    for item in self.fields
                }
            )
        )
        object.__setattr__(self, "record_id", str(self.record_id)[:120])
        object.__setattr__(self, "fields", safe_fields[:40])


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    observations: tuple[Observation, ...] = ()
    diagnostics: tuple[DriftDiagnostic, ...] = ()


class CanonicalAdapter:
    provider_id = ""
    source_urls: tuple[str, ...] = ()
    contextual = False
    max_context_urls = 0
    allowed_context_hosts: frozenset[str] = frozenset()
    known_record_fields: frozenset[str] = frozenset()

    def normalize(self, body: bytes | str, *, ingested_at: str) -> NormalizationResult:
        raise NotImplementedError

    def _diagnostic(self, code: str, record_id="", fields=()) -> DriftDiagnostic:
        return DriftDiagnostic(self.provider_id, code, record_id, tuple(fields))

    def _unknown(self, record: dict[str, Any], record_id="") -> list[DriftDiagnostic]:
        fields = set(record) - self.known_record_fields
        return [self._diagnostic("unknown_fields", record_id, fields)] if fields else []

    def _observation(self, raw_body, diagnostics, record_id, **values):
        try:
            return Observation.create(
                provider_id=self.provider_id,
                provider_record_id=str(record_id),
                raw_body=raw_body,
                **values,
            )
        except (TypeError, ValueError):
            diagnostics.append(self._diagnostic("invalid_record", record_id))
            return None


def _decode_json(body, provider_id):
    try:
        text = body.decode("utf-8") if isinstance(body, bytes) else body
        value = json.loads(text)
    except (AttributeError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None, (DriftDiagnostic(provider_id, "malformed_body"),)
    if not isinstance(value, dict):
        return None, (DriftDiagnostic(provider_id, "unexpected_root"),)
    return value, ()


def _required(record, names):
    return tuple(name for name in names if record.get(name) in (None, ""))


def _timestamp_ms(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return normalize_timestamp(dt.datetime.fromtimestamp(value / 1000, tz=dt.UTC))
    except (OverflowError, OSError, ValueError):
        return None


def _clean_markup(value, maximum=4000):
    if not isinstance(value, str):
        return ""
    text = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return re.sub(r"\s+", " ", text).strip()[:maximum]


def _url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    return value


def _cap(enum, value):
    try:
        return enum(value)
    except (TypeError, ValueError):
        return enum.UNKNOWN


def _rss_time(value):
    if not value:
        return None
    try:
        return normalize_timestamp(parsedate_to_datetime(value))
    except (TypeError, ValueError, OverflowError):
        try:
            return normalize_timestamp(value)
        except (TypeError, ValueError):
            return None


def _provider_utc_time(value):
    """Normalize source fields whose published contract defines an offset-less UTC tag."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?", text
    ):
        text = text.replace(" ", "T")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text):
            text += ":00"
        text += "Z"
    try:
        return normalize_timestamp(text)
    except (TypeError, ValueError):
        return None


def _timestamp_state(start, end, reference):
    """Classify a documented validity window without inventing alert semantics."""
    try:
        start_value = normalize_timestamp(start, required=True)
        end_value = normalize_timestamp(end, required=True)
        reference_value = normalize_timestamp(reference, required=True)
        parsed = tuple(
            dt.datetime.fromisoformat(item.replace("Z", "+00:00"))
            for item in (start_value, end_value, reference_value)
        )
    except (TypeError, ValueError):
        return None
    if parsed[1] <= parsed[0]:
        return None
    if parsed[1] <= parsed[2]:
        return "expired"
    if parsed[0] > parsed[2]:
        return "future"
    return "current"


def _xml_text(parent, *names):
    for child in parent.iter():
        local = child.tag.rsplit("}", 1)[-1]
        if local in names:
            text = " ".join("".join(child.itertext()).split())
            if text:
                return text
    return ""


class UsgsEarthquakeAdapter(CanonicalAdapter):
    provider_id = "usgs_earthquakes"
    source_urls = (
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    )
    known_record_fields = frozenset({"type", "id", "geometry", "properties", "bbox"})

    def normalize(self, body, *, ingested_at):
        raw, initial = _decode_json(body, self.provider_id)
        if raw is None:
            return NormalizationResult(diagnostics=initial)
        features = raw.get("features")
        if not isinstance(features, list):
            return NormalizationResult(diagnostics=(self._diagnostic("missing_fields", fields=("features",)),))
        output, diagnostics = [], list(initial)
        for feature in features:
            if not isinstance(feature, dict):
                diagnostics.append(self._diagnostic("invalid_record"))
                continue
            record_id = feature.get("id", "")
            diagnostics.extend(self._unknown(feature, record_id))
            properties = feature.get("properties")
            missing = _required(feature, ("id", "geometry"))
            geometry = feature.get("geometry")
            if missing or not isinstance(properties, dict) or not isinstance(geometry, dict):
                fields = missing or (() if isinstance(properties, dict) else ("properties",))
                if not isinstance(geometry, dict):
                    fields += ("geometry",)
                diagnostics.append(self._diagnostic("missing_fields", record_id, fields))
                continue
            magnitude = properties.get("mag")
            place = str(properties.get("place") or "Unknown location")
            title = properties.get("title") or f"M {magnitude} — {place}"
            metrics = {}
            if isinstance(magnitude, (int, float)) and not isinstance(magnitude, bool):
                metrics["magnitude"] = Metric(magnitude, "Mw", "properties.mag")
            coordinates = geometry.get("coordinates", [])
            if (
                isinstance(coordinates, list)
                and len(coordinates) > 2
                and isinstance(coordinates[2], (int, float))
                and not isinstance(coordinates[2], bool)
            ):
                metrics["depth"] = Metric(coordinates[2], "km", "geometry.coordinates[2]")
            if properties.get("tsunami") in (0, 1):
                metrics["tsunami_flag"] = Metric(bool(properties["tsunami"]), "boolean", "properties.tsunami")
            if isinstance(properties.get("sig"), int) and not isinstance(properties["sig"], bool):
                metrics["significance"] = Metric(properties["sig"], "USGS", "properties.sig")
            status = Status.CANCELLED if properties.get("status") == "deleted" else Status.ACTIVE
            observation = self._observation(
                body, diagnostics, record_id,
                kind=EventKind.EARTHQUAKE,
                headline=str(title)[:300], summary=place,
                status=status, severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN,
                certainty=Certainty.UNKNOWN, event_at=_timestamp_ms(properties.get("time")),
                source_updated_at=_timestamp_ms(properties.get("updated")),
                ingested_at=ingested_at, geometry=geometry,
                location_name=place[:300], metrics=metrics, source_url=_url(properties.get("url")),
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class NwsAlertAdapter(CanonicalAdapter):
    provider_id = "nws_alerts"
    source_urls = (
        "https://api.weather.gov/alerts/active?status=actual&message_type=alert",
    )
    known_record_fields = frozenset({"id", "type", "geometry", "properties", "@context"})

    def normalize(self, body, *, ingested_at):
        raw, initial = _decode_json(body, self.provider_id)
        if raw is None:
            return NormalizationResult(diagnostics=initial)
        features = raw.get("features")
        if not isinstance(features, list):
            return NormalizationResult(diagnostics=(self._diagnostic("missing_fields", fields=("features",)),))
        output, diagnostics = [], list(initial)
        for feature in features:
            if not isinstance(feature, dict):
                diagnostics.append(self._diagnostic("invalid_record"))
                continue
            properties = feature.get("properties")
            record_id = feature.get("id", "")
            diagnostics.extend(self._unknown(feature, record_id))
            if not isinstance(properties, dict):
                diagnostics.append(self._diagnostic("missing_fields", record_id, ("properties",)))
                continue
            record_id = properties.get("id") or record_id
            missing = _required(properties, ("event", "headline"))
            if not record_id:
                missing += ("id",)
            if missing:
                diagnostics.append(self._diagnostic("missing_fields", record_id, missing))
                continue
            message_type = str(properties.get("messageType") or "Alert").lower()
            status = {
                "cancel": Status.CANCELLED,
                "update": Status.UPDATED,
            }.get(message_type, Status.ACTIVE)
            description = _clean_markup(properties.get("description"))
            instruction = _clean_markup(properties.get("instruction"), 1500)
            summary = description
            if instruction:
                summary = f"{description}\n\nInstructions: {instruction}".strip()[:4000]
            metrics = {
                "event_name": Metric(
                    str(properties["event"])[:500], "NWS event", "properties.event"
                )
            }
            area = str(properties.get("areaDesc") or "")
            if area:
                metrics["affected_area"] = Metric(area[:500], "text", "properties.areaDesc")
            if instruction and len(instruction) <= 500:
                metrics["instruction"] = Metric(instruction, "text", "properties.instruction")
            sender = str(properties.get("senderName") or "")
            if sender:
                metrics["sender"] = Metric(sender[:500], "text", "properties.senderName")
            geocode = properties.get("geocode")
            ugc_codes = geocode.get("UGC") if isinstance(geocode, dict) else None
            if isinstance(ugc_codes, list):
                state_codes = sorted({
                    code[:2]
                    for code in ugc_codes
                    if isinstance(code, str)
                    and re.fullmatch(r"[A-Z]{2}[CZ]\d{3}", code)
                })
                if state_codes:
                    metrics["state_codes"] = Metric(
                        ",".join(state_codes), "USPS codes", "properties.geocode.UGC"
                    )
            observation = self._observation(
                body, diagnostics, record_id,
                kind=EventKind.WEATHER_ALERT, headline=str(properties["headline"])[:300],
                summary=summary, status=status,
                severity=_cap(Severity, properties.get("severity")),
                urgency=_cap(Urgency, properties.get("urgency")),
                certainty=_cap(Certainty, properties.get("certainty")),
                event_at=properties.get("onset"), effective_at=properties.get("effective"),
                expires_at=properties.get("expires"), source_updated_at=properties.get("sent"),
                ingested_at=ingested_at, geometry=feature.get("geometry"),
                location_name=area[:300], metrics=metrics, source_url=_url(feature.get("id")),
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class AviationWeatherSigmetAdapter(CanonicalAdapter):
    """Normalize the worldwide AWC SIGMET GeoJSON product.

    SIGMET validity is preserved as an advisory window. The source's numeric
    severity is retained as a metric because AWC does not document it as a CAP
    severity value; Foglight therefore does not translate it into emergency
    severity, urgency, or certainty.
    """

    provider_id = "noaa_aviation_weather"
    source_urls = (
        "https://aviationweather.gov/api/data/airsigmet?format=geojson",
    )
    known_record_fields = frozenset({"type", "id", "geometry", "properties", "bbox"})
    known_property_fields = frozenset({
        "icaoId", "airSigmetType", "alphaChar", "hazard", "seriesId",
        "validTimeFrom", "validTimeTo", "severity", "altitudeHi1",
        "altitudeHi2", "altitudeLow1", "altitudeLow2", "movementDir",
        "movementSpd", "rawAirSigmet",
    })

    def normalize(self, body, *, ingested_at):
        raw, initial = _decode_json(body, self.provider_id)
        if raw is None:
            return NormalizationResult(diagnostics=initial)
        features = raw.get("features")
        if not isinstance(features, list):
            return NormalizationResult(diagnostics=(
                self._diagnostic("missing_fields", fields=("features",)),
            ))
        output, diagnostics = [], list(initial)
        for feature in features:
            if not isinstance(feature, dict):
                diagnostics.append(self._diagnostic("invalid_record"))
                continue
            properties = feature.get("properties")
            geometry = feature.get("geometry")
            if not isinstance(properties, dict):
                diagnostics.append(self._diagnostic("missing_fields", fields=("properties",)))
                continue
            valid_from = properties.get("validTimeFrom")
            valid_to = properties.get("validTimeTo")
            record_parts = (
                properties.get("icaoId"), properties.get("airSigmetType"),
                properties.get("seriesId"), valid_from,
            )
            record_id = ":".join(str(item) for item in record_parts if item not in (None, ""))
            diagnostics.extend(self._unknown(feature, record_id))
            unknown_properties = set(properties) - self.known_property_fields
            if unknown_properties:
                diagnostics.append(self._diagnostic(
                    "unknown_fields", record_id,
                    (f"properties.{item}" for item in unknown_properties),
                ))
            missing = _required(
                properties,
                ("airSigmetType", "hazard", "seriesId", "validTimeFrom", "validTimeTo"),
            )
            string_fields = (
                "airSigmetType", "hazard", "seriesId", "validTimeFrom", "validTimeTo"
            )
            missing += tuple(
                name for name in string_fields
                if not isinstance(properties.get(name), str)
            )
            if not isinstance(geometry, dict):
                missing += ("geometry",)
            if missing or not record_id:
                diagnostics.append(self._diagnostic("missing_fields", record_id, missing or ("identity",)))
                continue
            validity_state = _timestamp_state(valid_from, valid_to, ingested_at)
            if validity_state is None:
                diagnostics.append(self._diagnostic(
                    "invalid_record", record_id, ("validTimeFrom", "validTimeTo"),
                ))
                continue
            hazard = str(properties["hazard"]).strip()
            advisory_type = str(properties["airSigmetType"]).strip()
            series_id = str(properties["seriesId"]).strip()
            metrics = {
                "hazard_type": Metric(hazard[:500], "AWC code", "properties.hazard"),
                "advisory_type": Metric(
                    advisory_type[:500], "AWC product", "properties.airSigmetType"
                ),
                "series_id": Metric(series_id[:500], "AWC series", "properties.seriesId"),
                "valid_from": Metric(
                    str(valid_from)[:500], "RFC 3339", "properties.validTimeFrom"
                ),
                "valid_to": Metric(
                    str(valid_to)[:500], "RFC 3339", "properties.validTimeTo"
                ),
                "validity_state": Metric(
                    validity_state, "derived window", "validTimeFrom/validTimeTo"
                ),
                "product_semantics": Metric(
                    "aviation_advisory", "semantics", "AWC SIGMET contract"
                ),
            }
            for source, key, unit in (
                ("severity", "source_severity", "AWC value"),
                ("altitudeHi1", "altitude_high_1", "ft"),
                ("altitudeHi2", "altitude_high_2", "ft"),
                ("altitudeLow1", "altitude_low_1", "ft"),
                ("altitudeLow2", "altitude_low_2", "ft"),
                ("movementDir", "movement_direction", "degrees"),
                ("movementSpd", "movement_speed", "kn"),
            ):
                value = properties.get(source)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metrics[key] = Metric(value, unit, f"properties.{source}")
            for source, key, unit in (
                ("icaoId", "icao_id", "ICAO region"),
                ("alphaChar", "alpha_character", "AWC code"),
            ):
                value = properties.get(source)
                if value not in (None, ""):
                    metrics[key] = Metric(str(value)[:500], unit, f"properties.{source}")
            headline = " ".join(
                part for part in (hazard, advisory_type, series_id) if part
            )
            observation = self._observation(
                body, diagnostics, record_id,
                kind=EventKind.AVIATION_HAZARD,
                headline=headline[:300] or "Aviation weather advisory",
                summary=_clean_markup(properties.get("rawAirSigmet")),
                status=Status.ENDED if validity_state == "expired" else Status.ACTIVE,
                severity=Severity.UNKNOWN,
                urgency=Urgency.UNKNOWN,
                certainty=Certainty.UNKNOWN,
                event_at=None,
                effective_at=valid_from,
                expires_at=valid_to,
                source_updated_at=None,
                ingested_at=ingested_at,
                geometry=geometry,
                location_name=str(properties.get("icaoId") or "")[:300],
                metrics=metrics,
                source_url="https://aviationweather.gov/sigmet",
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class OpenFemaDeclarationAdapter(CanonicalAdapter):
    """Normalize official declarations as administrative context.

    Declaration time is effective administrative time, not event onset. FEMA's
    incident begin/end fields remain explicit metrics, and no emergency
    severity, urgency, or certainty is inferred from declaration type.
    """

    provider_id = "openfema_declarations"
    source_urls = (
        "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?"
        "$orderby=declarationDate%20desc&$top=100",
    )
    known_record_fields = frozenset({
        "femaDeclarationString", "disasterNumber", "state", "declarationType",
        "disasterType", "declarationDate", "fyDeclared", "incidentType",
        "declarationTitle", "title", "ihProgramDeclared", "iaProgramDeclared",
        "paProgramDeclared", "hmProgramDeclared", "incidentBeginDate",
        "incidentEndDate", "disasterCloseoutDate", "disasterCloseOutDate",
        "tribalRequest", "fipsStateCode", "fipsCountyCode", "placeCode",
        "designatedArea", "declaredCountyArea", "declarationRequestNumber",
        "lastIAFilingDate", "incidentId", "region", "designatedIncidentTypes",
        "lastRefresh", "hash", "id",
    })

    @staticmethod
    def _field(record, *names):
        return next((record.get(name) for name in names if record.get(name) not in (None, "")), None)

    def normalize(self, body, *, ingested_at):
        raw, initial = _decode_json(body, self.provider_id)
        if raw is None:
            return NormalizationResult(diagnostics=initial)
        records = raw.get("DisasterDeclarationsSummaries")
        if not isinstance(records, list):
            return NormalizationResult(diagnostics=(
                self._diagnostic(
                    "missing_fields", fields=("DisasterDeclarationsSummaries",)
                ),
            ))
        output, diagnostics = [], list(initial)
        for record in records:
            if not isinstance(record, dict):
                diagnostics.append(self._diagnostic("invalid_record"))
                continue
            record_id = record.get("id", "")
            diagnostics.extend(self._unknown(record, record_id))
            declaration_type = self._field(record, "declarationType", "disasterType")
            title = self._field(record, "declarationTitle", "title")
            area = self._field(record, "designatedArea", "declaredCountyArea")
            closeout = self._field(
                record, "disasterCloseoutDate", "disasterCloseOutDate"
            )
            required = {
                "id": record_id,
                "disasterNumber": record.get("disasterNumber"),
                "state": record.get("state"),
                "declarationDate": record.get("declarationDate"),
                "incidentType": record.get("incidentType"),
                "declarationType": declaration_type,
                "title": title,
            }
            missing = tuple(name for name, value in required.items() if value in (None, ""))
            if missing:
                diagnostics.append(self._diagnostic("missing_fields", record_id, missing))
                continue
            text_values = (
                record.get("state"), record.get("incidentType"), declaration_type, title
            )
            number = record.get("disasterNumber")
            if (
                any(not isinstance(value, str) for value in text_values)
                or not re.fullmatch(r"[A-Za-z]{2}", str(record["state"]))
                or isinstance(number, bool)
                or not isinstance(number, (int, str))
                or not str(number).isdigit()
                or (area not in (None, "") and not isinstance(area, str))
            ):
                diagnostics.append(self._diagnostic(
                    "invalid_record", record_id,
                    (
                        "state", "incidentType", "declarationType", "title",
                        "disasterNumber",
                    ),
                ))
                continue
            declaration_at = _provider_utc_time(record["declarationDate"])
            if declaration_at is None:
                diagnostics.append(self._diagnostic(
                    "invalid_record", record_id, ("declarationDate",)
                ))
                continue
            metrics = {
                "administrative_context": Metric(
                    "federal_disaster_declaration", "semantics", "OpenFEMA dataset"
                ),
                "disaster_number": Metric(
                    str(record["disasterNumber"])[:500], "FEMA number", "disasterNumber"
                ),
                "declaration_type": Metric(
                    str(declaration_type)[:500], "FEMA code", "declarationType"
                ),
                "incident_type": Metric(
                    str(record["incidentType"])[:500], "FEMA type", "incidentType"
                ),
                "state_code": Metric(
                    str(record["state"])[:500].upper(), "USPS code", "state"
                ),
                "declaration_date": Metric(
                    declaration_at, "RFC 3339", "declarationDate"
                ),
            }
            if area not in (None, ""):
                metrics["declared_area"] = Metric(
                    str(area)[:500], "FEMA area", "designatedArea/declaredCountyArea"
                )
            for source, key, unit in (
                ("placeCode", "place_code", "FEMA code"),
                ("fipsStateCode", "fips_state_code", "FIPS"),
                ("fipsCountyCode", "fips_county_code", "FIPS"),
                ("fyDeclared", "fiscal_year", "year"),
            ):
                value = record.get(source)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    metrics[key] = Metric(value, unit, source)
            for source, key in (
                ("incidentBeginDate", "incident_begin"),
                ("incidentEndDate", "incident_end"),
                ("lastRefresh", "last_refresh"),
                ("lastIAFilingDate", "last_ia_filing_date"),
            ):
                value = record.get(source)
                if value not in (None, ""):
                    normalized = _provider_utc_time(value)
                    if normalized is None:
                        diagnostics.append(self._diagnostic(
                            "invalid_record", record_id, (source,)
                        ))
                    else:
                        metrics[key] = Metric(normalized, "RFC 3339", source)
            for source, key in (
                ("ihProgramDeclared", "ih_program_declared"),
                ("iaProgramDeclared", "ia_program_declared"),
                ("paProgramDeclared", "pa_program_declared"),
                ("hmProgramDeclared", "hm_program_declared"),
                ("tribalRequest", "tribal_request"),
            ):
                value = record.get(source)
                if isinstance(value, bool):
                    metrics[key] = Metric(value, "boolean", source)
            closeout_at = _provider_utc_time(closeout) if closeout else None
            if closeout and closeout_at is None:
                diagnostics.append(self._diagnostic(
                    "invalid_record", record_id,
                    ("disasterCloseoutDate/disasterCloseOutDate",),
                ))
            state = str(record["state"]).upper()
            location = ", ".join(
                item for item in (str(area or "").strip(), state) if item
            )
            summary = (
                f"FEMA {declaration_type} declaration {record['disasterNumber']}"
                f" for {location or state}. Incident type: {record['incidentType']}."
            )
            observation = self._observation(
                body, diagnostics, record_id,
                kind=EventKind.DISASTER_DECLARATION,
                headline=str(title)[:300],
                summary=summary[:4000],
                status=Status.ENDED if closeout_at else Status.UNKNOWN,
                severity=Severity.UNKNOWN,
                urgency=Urgency.UNKNOWN,
                certainty=Certainty.UNKNOWN,
                event_at=None,
                effective_at=declaration_at,
                expires_at=None,
                source_updated_at=_provider_utc_time(record.get("lastRefresh")),
                ingested_at=ingested_at,
                geometry=None,
                location_name=location[:300],
                country_codes=("US",),
                metrics=metrics,
                source_url=f"https://www.fema.gov/disaster/{record['disasterNumber']}",
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class NdbcObservationAdapter(CanonicalAdapter):
    provider_id = "ndbc_observations"
    contextual = True
    max_context_urls = 6
    allowed_context_hosts = frozenset({"www.ndbc.noaa.gov"})

    @staticmethod
    def _metrics(description, station_id, source_record_id):
        fields = {
            _clean_markup(label, 100): _clean_markup(value, 500)
            for label, value in re.findall(
                r"<b>\s*([^<:]+):\s*</b>\s*(.*?)(?=<br\s*/?>|$)",
                html.unescape(description or ""),
                flags=re.IGNORECASE | re.DOTALL,
            )
        }
        metrics = {
            "station_id": Metric(station_id, "NDBC station", "item.guid"),
            "source_record_id": Metric(source_record_id, "NDBC record", "item.guid"),
            "product_semantics": Metric(
                "marine_observation", "semantics", "NDBC nearby-observation RSS"
            ),
        }
        mappings = {
            "Wind Speed": ("wind_speed", r"(-?\d+(?:\.\d+)?)\s*knots?", "kn"),
            "Wind Gust": ("wind_gust", r"(-?\d+(?:\.\d+)?)\s*knots?", "kn"),
            "Significant Wave Height": (
                "significant_wave_height", r"(-?\d+(?:\.\d+)?)\s*ft", "ft"
            ),
            "Dominant Wave Period": (
                "dominant_wave_period", r"(-?\d+(?:\.\d+)?)\s*sec", "s"
            ),
            "Average Period": ("average_wave_period", r"(-?\d+(?:\.\d+)?)\s*sec", "s"),
            "Visibility": ("visibility", r"(-?\d+(?:\.\d+)?)\s*(?:nmi|nautical miles?)", "nmi"),
            "Tide": ("tide", r"(-?\d+(?:\.\d+)?)\s*ft", "ft"),
        }
        for label, (key, pattern, unit) in mappings.items():
            match = re.search(pattern, fields.get(label, ""), flags=re.IGNORECASE)
            if match:
                metrics[key] = Metric(float(match.group(1)), unit, f"description.{label}")
        for label, key in (
            ("Air Temperature", "air_temperature"),
            ("Water Temperature", "water_temperature"),
            ("Dew Point", "dew_point"),
        ):
            match = re.search(r"\((-?\d+(?:\.\d+)?)\s*°C\)", fields.get(label, ""))
            if match:
                metrics[key] = Metric(float(match.group(1)), "°C", f"description.{label}")
        for label, key in (
            ("Atmospheric Pressure", "atmospheric_pressure"),
            ("Pressure Tendency", "pressure_tendency"),
        ):
            match = re.search(r"\((-?\d+(?:\.\d+)?)\s*mb\)", fields.get(label, ""))
            if match:
                metrics[key] = Metric(float(match.group(1)), "hPa", f"description.{label}")
        for label, key in (
            ("Wind Direction", "wind_direction"),
            ("Mean Wave Direction", "mean_wave_direction"),
        ):
            value = fields.get(label, "")
            match = re.search(r"\((-?\d+(?:\.\d+)?)°\)", value)
            if match:
                metrics[key] = Metric(float(match.group(1)), "degrees", f"description.{label}")
            cardinal = re.match(r"([A-Z]+)", value)
            if cardinal:
                metrics[f"{key}_cardinal"] = Metric(
                    cardinal.group(1), "cardinal", f"description.{label}"
                )
        recognized = set(mappings) | {
            "Air Temperature", "Water Temperature", "Dew Point",
            "Atmospheric Pressure", "Pressure Tendency", "Wind Direction",
            "Mean Wave Direction", "Location",
        }
        for label, value in fields.items():
            if label in recognized or not value:
                continue
            slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:48]
            if slug:
                metrics.setdefault(
                    f"source_{slug}", Metric(value, "source text", f"description.{label}")
                )
        return metrics

    def normalize(self, body, *, ingested_at):
        try:
            root = ET.fromstring(body)
        except (ET.ParseError, TypeError):
            return NormalizationResult(diagnostics=(self._diagnostic("malformed_body"),))
        output, diagnostics = [], []
        items = [item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "item"]
        for item in items:
            guid = _xml_text(item, "guid")
            title = _xml_text(item, "title")
            point = _xml_text(item, "point")
            missing = tuple(
                name for name, value in (("guid", guid), ("title", title), ("point", point))
                if not value
            )
            if missing:
                diagnostics.append(self._diagnostic("missing_fields", guid, missing))
                continue
            identity = re.fullmatch(r"NDBC-([A-Za-z0-9]+)-(\d{14})", guid)
            parts = point.split()
            if identity is None or len(parts) < 2:
                diagnostics.append(self._diagnostic("invalid_record", guid))
                continue
            try:
                event_at = normalize_timestamp(
                    dt.datetime.strptime(identity.group(2), "%Y%m%d%H%M%S").replace(tzinfo=dt.UTC)
                )
                latitude, longitude = float(parts[0]), float(parts[1])
            except (TypeError, ValueError):
                diagnostics.append(self._diagnostic("invalid_record", guid))
                continue
            description = _xml_text(item, "description")
            link = next(
                (
                    child.text
                    for child in item.iter()
                    if child.tag.rsplit("}", 1)[-1] == "link" and child.text
                ),
                None,
            )
            station_id = identity.group(1).upper()
            stable_description = re.sub(
                r"<b>\s*Location:\s*</b>.*?(?=<br\s*/?>|$)",
                "",
                html.unescape(description or ""),
                flags=re.IGNORECASE | re.DOTALL,
            )
            observation = self._observation(
                body, diagnostics, station_id,
                kind=EventKind.MARINE_OBSERVATION,
                headline=title[:300],
                summary=_clean_markup(stable_description),
                status=Status.ACTIVE,
                severity=Severity.UNKNOWN,
                urgency=Urgency.UNKNOWN,
                certainty=Certainty.UNKNOWN,
                event_at=event_at,
                effective_at=None,
                expires_at=None,
                # The item pubDate is the feed-generation time, not the buoy's
                # measurement time. Keep canonical revisions tied to the
                # station observation represented by the GUID.
                source_updated_at=event_at,
                ingested_at=ingested_at,
                geometry={"type": "Point", "coordinates": [longitude, latitude]},
                location_name=title[:300],
                metrics=self._metrics(description, station_id, guid),
                source_url=_url(link),
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class CoopsWaterLevelAdapter(CanonicalAdapter):
    provider_id = "noaa_coops_water_levels"
    contextual = True
    max_context_urls = 6
    allowed_context_hosts = frozenset({"api.tidesandcurrents.noaa.gov"})
    known_data_fields = frozenset({"t", "v", "s", "f", "q"})

    def normalize(self, body, *, ingested_at):
        raw, initial = _decode_json(body, self.provider_id)
        if raw is None:
            return NormalizationResult(diagnostics=initial)
        metadata = raw.get("metadata")
        rows = raw.get("data")
        if not isinstance(metadata, dict) or not isinstance(rows, list):
            return NormalizationResult(diagnostics=(self._diagnostic(
                "missing_fields", fields=("metadata", "data")
            ),))
        station_id = str(metadata.get("id") or "")
        station_name = str(metadata.get("name") or "")
        try:
            latitude = float(metadata["lat"])
            longitude = float(metadata["lon"])
        except (KeyError, TypeError, ValueError):
            return NormalizationResult(diagnostics=(self._diagnostic(
                "missing_fields", station_id, ("metadata.id/name/lat/lon",)
            ),))
        if not station_id or not station_name:
            return NormalizationResult(diagnostics=(self._diagnostic(
                "missing_fields", station_id, ("metadata.id/name",)
            ),))
        predictions = {}
        if isinstance(raw.get("predictions"), list):
            for prediction in raw["predictions"]:
                if not isinstance(prediction, dict):
                    continue
                try:
                    predictions[str(prediction["t"])] = float(prediction["v"])
                except (KeyError, TypeError, ValueError):
                    continue
        output, diagnostics = [], list(initial)
        for row in rows:
            if not isinstance(row, dict):
                diagnostics.append(self._diagnostic("invalid_record", station_id))
                continue
            source_record_id = f"{station_id}:{row.get('t') or ''}"
            unknown = set(row) - self.known_data_fields
            if unknown:
                diagnostics.append(self._diagnostic(
                    "unknown_fields", source_record_id,
                    (f"data.{item}" for item in unknown),
                ))
            missing = _required(row, ("t", "v"))
            event_at = _provider_utc_time(row.get("t"))
            try:
                value = float(row.get("v"))
            except (TypeError, ValueError):
                value = math.nan
            if missing or event_at is None or not math.isfinite(value):
                diagnostics.append(self._diagnostic(
                    "missing_fields" if missing else "invalid_record", source_record_id,
                    missing or ("t", "v"),
                ))
                continue
            quality = str(row.get("q") or "").strip().lower()
            quality_name = {"p": "preliminary", "v": "verified"}.get(
                quality, quality or "not reported"
            )
            metrics = {
                "source_record_id": Metric(
                    source_record_id, "CO-OPS record", "metadata.id + data.t"
                ),
                "water_level": Metric(value, "m", "data.v; units=metric"),
                "datum": Metric("MLLW", "datum", "request.datum"),
                "quality_flag": Metric(quality_name, "CO-OPS QA", "data.q"),
                "product_semantics": Metric(
                    "water_level_observation", "semantics", "CO-OPS water_level"
                ),
            }
            flags = str(row.get("f") or "").strip()
            if flags:
                metrics["data_flags"] = Metric(flags[:500], "CO-OPS flags", "data.f")
            try:
                sigma = float(row.get("s"))
            except (TypeError, ValueError):
                sigma = math.nan
            if math.isfinite(sigma):
                metrics["standard_deviation"] = Metric(sigma, "m", "data.s")
            prediction = predictions.get(str(row["t"]))
            if prediction is not None and math.isfinite(prediction):
                metrics["predicted_water_level"] = Metric(
                    prediction, "m", "predictions.v"
                )
                metrics["water_level_anomaly"] = Metric(
                    round(value - prediction, 4), "m", "data.v - predictions.v"
                )
            observation = self._observation(
                body, diagnostics, station_id,
                kind=EventKind.WATER_LEVEL,
                headline=f"Water level at {station_name}"[:300],
                summary=(
                    f"Observed water level {value:g} m MLLW; quality {quality_name}."
                ),
                status=Status.ACTIVE,
                severity=Severity.UNKNOWN,
                urgency=Urgency.UNKNOWN,
                certainty=Certainty.UNKNOWN,
                event_at=event_at,
                effective_at=None,
                expires_at=None,
                source_updated_at=event_at,
                ingested_at=ingested_at,
                geometry={"type": "Point", "coordinates": [longitude, latitude]},
                location_name=station_name[:300],
                metrics=metrics,
                source_url=f"https://tidesandcurrents.noaa.gov/stationhome.html?id={station_id}",
            )
            if observation:
                output.append(observation)
        # ``date=latest`` normally returns one row, but selecting explicitly
        # keeps station identity deterministic if the upstream ever returns a
        # wider or out-of-order batch.
        latest = max(output, key=lambda item: item.event_at or "", default=None)
        return NormalizationResult((latest,) if latest else (), tuple(diagnostics))


class JplFireballAdapter(CanonicalAdapter):
    provider_id = "nasa_jpl_fireballs"
    source_urls = ("https://ssd-api.jpl.nasa.gov/fireball.api?limit=20",)
    known_record_fields = frozenset({"signature", "count", "fields", "data"})
    required_fields = (
        "date", "lat", "lat-dir", "lon", "lon-dir", "alt", "energy", "impact-e",
    )

    def normalize(self, body, *, ingested_at):
        raw, initial = _decode_json(body, self.provider_id)
        if raw is None:
            return NormalizationResult(diagnostics=initial)
        diagnostics = list(initial) + self._unknown(raw)
        signature = raw.get("signature")
        raw_count = raw.get("count")
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = -1
        if (
            not isinstance(signature, dict)
            or signature.get("version") != "1.2"
            or isinstance(raw_count, bool)
            or not (
                isinstance(raw_count, int)
                or isinstance(raw_count, str) and raw_count.isdigit()
            )
            or not 0 <= count <= 20
        ):
            return NormalizationResult(diagnostics=tuple(diagnostics) + (
                self._diagnostic("unexpected_root", fields=("signature.version", "count")),
            ))
        if count == 0:
            if raw.get("data") not in (None, []) or raw.get("fields") not in (None, []):
                diagnostics.append(self._diagnostic(
                    "unexpected_root", fields=("fields", "data", "count")
                ))
            return NormalizationResult(diagnostics=tuple(diagnostics))
        fields = raw.get("fields")
        rows = raw.get("data")
        valid_fields = isinstance(fields, list) and all(
            isinstance(field, str) for field in fields
        )
        field_set = set(fields) if valid_fields else set()
        allowed_field_sets = {
            frozenset(self.required_fields),
            frozenset((*self.required_fields, "vel")),
        }
        if (
            not isinstance(fields, list)
            or not valid_fields
            or len(fields) != len(field_set)
            or frozenset(field_set) not in allowed_field_sets
            or not isinstance(rows, list)
            or len(rows) != count
        ):
            return NormalizationResult(diagnostics=tuple(diagnostics) + (
                self._diagnostic("unexpected_root", fields=("fields", "data", "count")),
            ))
        indexes = {name: fields.index(name) for name in field_set}
        output = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(fields):
                diagnostics.append(self._diagnostic("invalid_record"))
                continue
            values = {name: row[index] for name, index in indexes.items()}
            record_id = str(values["date"] or "")
            missing = _required(values, ("date", "energy", "impact-e"))
            event_at = _provider_utc_time(values["date"])
            try:
                energy = float(values["energy"]) if isinstance(values["energy"], str) else math.nan
                impact_energy = (
                    float(values["impact-e"])
                    if isinstance(values["impact-e"], str) else math.nan
                )
            except (TypeError, ValueError):
                energy = impact_energy = math.nan
            if (
                missing
                or event_at is None
                or not math.isfinite(energy)
                or energy < 0
                or not math.isfinite(impact_energy)
                or impact_energy < 0
            ):
                diagnostics.append(self._diagnostic(
                    "missing_fields" if missing else "invalid_record",
                    record_id,
                    missing or ("date", "energy", "impact-e"),
                ))
                continue
            location_values = tuple(
                values[name] for name in ("lat", "lat-dir", "lon", "lon-dir")
            )
            geometry = None
            location_name = ""
            if any(value is not None for value in location_values):
                try:
                    latitude = (
                        float(values["lat"])
                        if isinstance(values["lat"], str) else math.nan
                    )
                    longitude = (
                        float(values["lon"])
                        if isinstance(values["lon"], str) else math.nan
                    )
                except (TypeError, ValueError):
                    latitude = longitude = math.nan
                if (
                    not all(value not in (None, "") for value in location_values)
                    or values["lat-dir"] not in {"N", "S"}
                    or values["lon-dir"] not in {"E", "W"}
                    or not math.isfinite(latitude)
                    or not 0 <= latitude <= 90
                    or not math.isfinite(longitude)
                    or not 0 <= longitude <= 180
                ):
                    diagnostics.append(self._diagnostic(
                        "invalid_record", record_id,
                        ("lat", "lat-dir", "lon", "lon-dir"),
                    ))
                    continue
                latitude *= -1 if values["lat-dir"] == "S" else 1
                longitude *= -1 if values["lon-dir"] == "W" else 1
                geometry = {"type": "Point", "coordinates": [longitude, latitude]}
                location_name = f"{abs(latitude):g}°{values['lat-dir']}, {abs(longitude):g}°{values['lon-dir']}"
            metrics = {
                "radiated_energy": Metric(energy, "10^10 J", "data.energy"),
                "impact_energy": Metric(impact_energy, "kt", "data.impact-e"),
                "product_semantics": Metric(
                    "fireball_peak_brightness", "semantics", "NASA/JPL Fireball API"
                ),
            }
            if values["alt"] not in (None, ""):
                try:
                    altitude = (
                        float(values["alt"])
                        if isinstance(values["alt"], str) else math.nan
                    )
                except (TypeError, ValueError):
                    altitude = math.nan
                if not math.isfinite(altitude) or altitude < 0:
                    diagnostics.append(self._diagnostic(
                        "invalid_record", record_id, ("alt",)
                    ))
                    continue
                metrics["peak_brightness_altitude"] = Metric(
                    altitude, "km", "data.alt"
                )
            if values.get("vel") not in (None, ""):
                try:
                    velocity = (
                        float(values["vel"])
                        if isinstance(values["vel"], str) else math.nan
                    )
                except (TypeError, ValueError):
                    velocity = math.nan
                if not math.isfinite(velocity) or velocity < 0:
                    diagnostics.append(self._diagnostic(
                        "invalid_record", record_id, ("vel",)
                    ))
                    continue
                metrics["entry_velocity"] = Metric(velocity, "km/s", "data.vel")
            location_summary = (
                f"Peak brightness location {location_name}."
                if location_name else "Peak brightness location was not reported by the source."
            )
            observation = self._observation(
                body, diagnostics, event_at,
                kind=EventKind.FIREBALL,
                headline=f"Reported fireball {event_at.replace('T', ' ').removesuffix('Z')} UTC",
                summary=(
                    f"NASA/JPL CNEOS reports {energy:g} ×10^10 J radiated energy and "
                    f"{impact_energy:g} kt estimated impact energy. {location_summary} "
                    "These sensor reports are not real-time and not every fireball is reported."
                ),
                status=Status.ENDED,
                severity=Severity.UNKNOWN,
                urgency=Urgency.UNKNOWN,
                certainty=Certainty.UNKNOWN,
                event_at=event_at,
                effective_at=None,
                expires_at=None,
                source_updated_at=None,
                ingested_at=ingested_at,
                geometry=geometry,
                location_name=location_name,
                metrics=metrics,
                source_url="https://cneos.jpl.nasa.gov/fireballs/",
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class NhcStormAdapter(CanonicalAdapter):
    provider_id = "nhc_storms"
    source_urls = ("https://www.nhc.noaa.gov/CurrentStorms.json",)
    known_record_fields = frozenset({
        "id", "binNumber", "name", "classification", "intensity", "pressure",
        "latitude", "longitude", "latitude_numeric", "longitude_numeric",
        "movementDir", "movementSpeed", "lastUpdate", "publicAdvisory",
        "forecastAdvisory", "windSpeedProbabilities", "forecastDiscussion",
        "forecastGraphics", "forecastTrack", "windWatchesWarnings", "trackCone",
        "initialWindExtent", "forecastWindRadiiGIS", "bestTrackGIS",
        "earliestArrivalTimeTSWindsGIS", "mostLikelyTimeTSWindsGIS",
        "windSpeedProbabilitiesGIS", "stormSurgeWatchWarningGIS",
        "potentialStormSurgeFloodingGIS",
    })

    def normalize(self, body, *, ingested_at):
        raw, initial = _decode_json(body, self.provider_id)
        if raw is None:
            return NormalizationResult(diagnostics=initial)
        storms = raw.get("activeStorms")
        if not isinstance(storms, list):
            return NormalizationResult(diagnostics=(self._diagnostic("missing_fields", fields=("activeStorms",)),))
        output, diagnostics = [], list(initial)
        for storm in storms:
            if not isinstance(storm, dict):
                diagnostics.append(self._diagnostic("invalid_record"))
                continue
            record_id = storm.get("id", "")
            diagnostics.extend(self._unknown(storm, record_id))
            missing = _required(storm, ("id", "name", "lastUpdate", "latitude_numeric", "longitude_numeric"))
            if missing:
                diagnostics.append(self._diagnostic("missing_fields", record_id, missing))
                continue
            metrics = {}
            for key, unit, source in (
                ("intensity", "kn", "intensity"), ("pressure", "hPa", "pressure"),
                ("movementDir", "degrees", "movementDir"),
                ("movementSpeed", "mph", "movementSpeed"),
            ):
                if isinstance(storm.get(key), (int, float)) and not isinstance(storm[key], bool):
                    metrics[key.lower()] = Metric(storm[key], unit, source)
            classification = str(storm.get("classification") or "")
            metrics["storm_name"] = Metric(str(storm["name"])[:500], "name", "name")
            if classification:
                metrics["classification"] = Metric(classification, "NHC code", "classification")
            advisory = storm.get("publicAdvisory") if isinstance(storm.get("publicAdvisory"), dict) else {}
            if advisory.get("issuance"):
                metrics["advisory_issued_at"] = Metric(
                    str(advisory["issuance"])[:500], "RFC 3339", "publicAdvisory.issuance"
                )
            source_url = _url(advisory.get("url")) or "https://www.nhc.noaa.gov/"
            observation = self._observation(
                body, diagnostics, record_id,
                kind=EventKind.TROPICAL_CYCLONE,
                headline=f"{classification} {storm['name']}".strip(),
                summary=f"Active tropical cyclone {storm['name']}", status=Status.ACTIVE,
                severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN, certainty=Certainty.UNKNOWN,
                event_at=None, effective_at=storm.get("lastUpdate"),
                source_updated_at=storm.get("lastUpdate"),
                ingested_at=ingested_at,
                geometry={"type": "Point", "coordinates": [storm["longitude_numeric"], storm["latitude_numeric"]]},
                location_name=f"{storm.get('latitude', '')} {storm.get('longitude', '')}".strip(),
                metrics=metrics, source_url=source_url,
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class TsunamiAtomAdapter(CanonicalAdapter):
    provider_id = "noaa_tsunami"
    source_urls = (
        "https://www.tsunami.gov/events/xml/PHEBAtom.xml",
        "https://www.tsunami.gov/events/xml/PAAQAtom.xml",
    )

    def normalize(self, body, *, ingested_at):
        try:
            root = ET.fromstring(body)
        except (ET.ParseError, TypeError):
            return NormalizationResult(diagnostics=(self._diagnostic("malformed_body"),))
        output, diagnostics = [], []
        entries = [item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "entry"]
        for entry in entries:
            record_id = _xml_text(entry, "id", "identifier")
            title = _xml_text(entry, "title", "headline")
            missing = tuple(name for name, value in (("id", record_id), ("title", title)) if not value)
            if missing:
                diagnostics.append(self._diagnostic("missing_fields", record_id, missing))
                continue
            summary = _clean_markup(_xml_text(entry, "summary", "description"))
            updated = _xml_text(entry, "updated", "sent")
            effective = _xml_text(entry, "effective") or None
            point = _xml_text(entry, "point")
            geometry = None
            if point:
                parts = point.split()
                if len(parts) >= 2:
                    try:
                        geometry = {"type": "Point", "coordinates": [float(parts[1]), float(parts[0])]}
                    except ValueError:
                        diagnostics.append(self._diagnostic("invalid_record", record_id, ("point",)))
            if geometry is None:
                latitude = _xml_text(entry, "lat")
                longitude = _xml_text(entry, "long", "lon")
                if latitude and longitude:
                    try:
                        geometry = {
                            "type": "Point",
                            "coordinates": [float(longitude), float(latitude)],
                        }
                    except ValueError:
                        diagnostics.append(
                            self._diagnostic("invalid_record", record_id, ("lat", "long"))
                        )
            source_url = None
            link_urls = []
            for child in entry.iter():
                if child.tag.rsplit("}", 1)[-1] == "link":
                    link = _url(child.attrib.get("href") or child.text)
                    if link:
                        link_urls.append(link)
            source_url = link_urls[-1] if link_urls else None
            cancelled = "cancel" in f"{title} {summary}".lower()
            source_identity = f"{record_id} {' '.join(link_urls)}".upper()
            source = (
                "PTWC" if "PHEB" in source_identity
                else "NTWC" if "PAAQ" in source_identity else "NOAA"
            )
            relation_candidate = re.sub(r"(?:[/#_-]?\d+)?$", "", record_id)
            for link in link_urls:
                match = re.search(r"/events/(PAAQ|PHEB)/(\d{4}/\d{2}/\d{2}/[^/]+)", link)
                if match:
                    relation_candidate = f"{match.group(1)}/{match.group(2)}"
                    break
            area = _xml_text(entry, "areaDesc")
            if not area:
                match = re.search(
                    r"Affected Region:\s*(.+?)(?:\s+Note:|\s+Definition:|$)", summary, re.I
                )
                area = match.group(1).strip() if match else ""
            metrics = {
                "bulletin_source": Metric(source, "center", "feed identity"),
                "relation_candidate": Metric(
                    (relation_candidate or record_id)[:500], "series", "entry/link identity"
                ),
            }
            if area:
                metrics["affected_area"] = Metric(area[:500], "text", "areaDesc/summary")
            observation = self._observation(
                body, diagnostics, record_id,
                kind=EventKind.TSUNAMI, headline=title[:300], summary=summary,
                status=Status.CANCELLED if cancelled else Status.ACTIVE,
                severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN, certainty=Certainty.UNKNOWN,
                event_at=None, effective_at=effective, source_updated_at=_rss_time(updated),
                ingested_at=ingested_at, geometry=geometry, location_name=area[:300],
                metrics=metrics, source_url=source_url,
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class GdacsGeoJsonAdapter(CanonicalAdapter):
    provider_id = "gdacs"
    source_urls = (
        "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
    )
    known_record_fields = frozenset({"type", "id", "geometry", "properties", "bbox"})
    kind_map = {
        "EQ": EventKind.EARTHQUAKE,
        "TC": EventKind.TROPICAL_CYCLONE,
        "VO": EventKind.VOLCANO,
        "WF": EventKind.WILDFIRE,
        "FL": EventKind.DISASTER,
        "DR": EventKind.DISASTER,
    }

    def normalize(self, body, *, ingested_at):
        raw, initial = _decode_json(body, self.provider_id)
        if raw is None:
            return NormalizationResult(diagnostics=initial)
        features = raw.get("features")
        if not isinstance(features, list):
            return NormalizationResult(diagnostics=(self._diagnostic("missing_fields", fields=("features",)),))
        output, diagnostics = [], list(initial)
        for feature in features:
            if not isinstance(feature, dict):
                diagnostics.append(self._diagnostic("invalid_record"))
                continue
            properties = feature.get("properties")
            if not isinstance(properties, dict):
                diagnostics.append(self._diagnostic("missing_fields", fields=("properties",)))
                continue
            event_type = str(properties.get("eventtype") or properties.get("eventType") or "").upper()
            event_id = properties.get("eventid") or properties.get("eventId") or feature.get("id")
            record_id = f"{event_type}:{event_id}" if event_type and event_id else ""
            diagnostics.extend(self._unknown(feature, record_id))
            if not record_id:
                diagnostics.append(self._diagnostic("missing_fields", fields=("eventtype", "eventid")))
                continue
            metrics = {}
            alert_level = properties.get("alertlevel") or properties.get("alertLevel")
            if alert_level:
                metrics["alert_level"] = Metric(str(alert_level)[:500], "GDACS", "properties.alertlevel")
            for source_key, metric_key, unit in (
                ("population", "population_exposed", "people"),
                ("populationexposure", "population_exposed", "people"),
                ("severity", "source_severity", "GDACS"),
            ):
                value = properties.get(source_key)
                if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                    metrics.setdefault(metric_key, Metric(value, unit, f"properties.{source_key}"))
            severity_data = properties.get("severitydata")
            if isinstance(severity_data, dict):
                value = severity_data.get("severity")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metrics["source_severity"] = Metric(
                        value,
                        str(severity_data.get("severityunit") or "GDACS")[:32],
                        "properties.severitydata.severity",
                    )
            if properties.get("episodeid") not in (None, ""):
                metrics["episode_id"] = Metric(
                    str(properties["episodeid"])[:500], "GDACS", "properties.episodeid"
                )
            metrics["event_type"] = Metric(event_type, "GDACS code", "properties.eventtype")
            headline = properties.get("name") or properties.get("title") or f"GDACS {event_type} event"
            country = str(properties.get("country") or "")
            affected = properties.get("affectedcountries")
            country_codes = tuple(
                str(item["iso2"]).upper()
                for item in (affected if isinstance(affected, list) else [])
                if isinstance(item, dict)
                and re.fullmatch(r"[A-Za-z]{2}", str(item.get("iso2") or ""))
            )
            links = properties.get("url")
            if isinstance(links, dict):
                source_url = _url(links.get("report") or links.get("details"))
            else:
                source_url = _url(links or properties.get("link"))
            current = str(properties.get("iscurrent") or "true").lower() == "true"
            observation = self._observation(
                body, diagnostics, record_id,
                kind=self.kind_map.get(event_type, EventKind.DISASTER),
                headline=str(headline)[:300], summary=_clean_markup(properties.get("description")),
                status=Status.ACTIVE if current else Status.ENDED,
                severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN,
                certainty=Certainty.UNKNOWN,
                event_at=_provider_utc_time(properties.get("fromdate") or properties.get("fromDate")),
                expires_at=_provider_utc_time(properties.get("todate") or properties.get("toDate")),
                source_updated_at=_provider_utc_time(
                    properties.get("datemodified") or properties.get("lastupdate")
                    or properties.get("lastUpdate")
                ),
                ingested_at=ingested_at, geometry=feature.get("geometry"),
                location_name=country[:300], country_codes=country_codes,
                metrics=metrics, source_url=source_url,
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class EonetAdapter(CanonicalAdapter):
    provider_id = "nasa_eonet"
    source_urls = (
        "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=200",
    )
    known_record_fields = frozenset({
        "id", "title", "description", "link", "closed", "categories", "sources", "geometry",
    })
    kind_map = {
        "wildfires": EventKind.WILDFIRE,
        "volcanoes": EventKind.VOLCANO,
        "severeStorms": EventKind.NATURAL_EVENT,
        "earthquakes": EventKind.EARTHQUAKE,
    }

    def normalize(self, body, *, ingested_at):
        raw, initial = _decode_json(body, self.provider_id)
        if raw is None:
            return NormalizationResult(diagnostics=initial)
        events = raw.get("events")
        if not isinstance(events, list):
            return NormalizationResult(diagnostics=(self._diagnostic("missing_fields", fields=("events",)),))
        output, diagnostics = [], list(initial)
        for event in events:
            if not isinstance(event, dict):
                diagnostics.append(self._diagnostic("invalid_record"))
                continue
            record_id = event.get("id", "")
            diagnostics.extend(self._unknown(event, record_id))
            missing = _required(event, ("id", "title"))
            geometry_history = event.get("geometry")
            if missing or not isinstance(geometry_history, list) or not geometry_history:
                diagnostics.append(self._diagnostic("missing_fields", record_id, missing or ("geometry",)))
                continue
            latest = geometry_history[-1]
            if not isinstance(latest, dict):
                diagnostics.append(self._diagnostic("invalid_record", record_id, ("geometry",)))
                continue
            categories = event.get("categories") if isinstance(event.get("categories"), list) else []
            category_ids = [str(item.get("id")) for item in categories if isinstance(item, dict) and item.get("id")]
            sources = event.get("sources") if isinstance(event.get("sources"), list) else []
            source_ids = [str(item.get("id")) for item in sources if isinstance(item, dict) and item.get("id")]
            source_url = next((_url(item.get("url")) for item in sources if isinstance(item, dict) and _url(item.get("url"))), None)
            metrics = {
                "geometry_history_count": Metric(len(geometry_history), "observations", "geometry"),
            }
            if category_ids:
                metrics["category_ids"] = Metric(",".join(category_ids)[:500], "EONET", "categories[].id")
            if source_ids:
                metrics["source_ids"] = Metric(",".join(source_ids)[:500], "EONET", "sources[].id")
            magnitude = latest.get("magnitudeValue")
            if isinstance(magnitude, (int, float)) and not isinstance(magnitude, bool):
                metrics["magnitude"] = Metric(
                    magnitude, str(latest.get("magnitudeUnit") or "source unit")[:32],
                    "geometry[-1].magnitudeValue",
                )
            kind = next((self.kind_map[item] for item in category_ids if item in self.kind_map), EventKind.NATURAL_EVENT)
            observation = self._observation(
                body, diagnostics, record_id,
                kind=kind, headline=str(event["title"])[:300],
                summary=_clean_markup(event.get("description")),
                status=Status.ENDED if event.get("closed") else Status.ACTIVE,
                severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN, certainty=Certainty.UNKNOWN,
                event_at=latest.get("date"), expires_at=event.get("closed"),
                source_updated_at=latest.get("date"), ingested_at=ingested_at,
                geometry={key: latest[key] for key in ("type", "coordinates") if key in latest},
                metrics=metrics, source_url=source_url or _url(event.get("link")),
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class SmithsonianVolcanoAdapter(CanonicalAdapter):
    provider_id = "smithsonian_volcano"
    source_urls = ("https://volcano.si.edu/news/WeeklyVolcanoRSS.xml",)

    def normalize(self, body, *, ingested_at):
        try:
            root = ET.fromstring(body)
        except (ET.ParseError, TypeError):
            return NormalizationResult(diagnostics=(self._diagnostic("malformed_body"),))
        output, diagnostics = [], []
        items = [item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "item"]
        for item in items:
            title = _xml_text(item, "title")
            link = _xml_text(item, "link")
            record_id = _xml_text(item, "guid") or link or title
            if not title or not record_id:
                diagnostics.append(self._diagnostic("missing_fields", record_id, ("title", "guid")))
                continue
            description = _clean_markup(_xml_text(item, "description"))
            published = _rss_time(_xml_text(item, "pubDate", "published", "updated"))
            metrics = {
                "report_semantics": Metric(
                    "weekly_activity_report", "report type", "Smithsonian Weekly Volcano Report"
                )
            }
            observation = self._observation(
                body, diagnostics, record_id,
                kind=EventKind.VOLCANO, headline=title[:300], summary=description,
                status=Status.ACTIVE, severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN,
                certainty=Certainty.UNKNOWN, event_at=None, source_updated_at=published,
                ingested_at=ingested_at, geometry=None, metrics=metrics, source_url=_url(link),
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class SwpcKpAdapter(CanonicalAdapter):
    provider_id = "noaa_space_weather"
    source_urls = (
        "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    )
    known_record_fields = frozenset({"time_tag", "Kp", "a_running", "station_count"})

    def normalize(self, body, *, ingested_at):
        try:
            text = body.decode("utf-8") if isinstance(body, bytes) else body
            rows = json.loads(text)
        except (AttributeError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return NormalizationResult(diagnostics=(self._diagnostic("malformed_body"),))
        if not isinstance(rows, list) or not rows:
            return NormalizationResult() if rows == [] else NormalizationResult(
                diagnostics=(self._diagnostic("unexpected_root"),)
            )
        output, diagnostics = [], []
        if isinstance(rows[0], dict):
            records = rows
        elif isinstance(rows[0], list):
            header = rows[0]
            required = ("time_tag", "Kp")
            missing = tuple(item for item in required if item not in header)
            if missing:
                return NormalizationResult(
                    diagnostics=(self._diagnostic("missing_fields", fields=missing),)
                )
            records = []
            for row in rows[1:]:
                if not isinstance(row, list) or len(row) != len(header):
                    diagnostics.append(self._diagnostic("invalid_record"))
                    continue
                records.append(dict(zip(header, row)))
        else:
            return NormalizationResult(
                diagnostics=(self._diagnostic("missing_fields", fields=("header",)),)
            )
        for record in records:
            if not isinstance(record, dict):
                diagnostics.append(self._diagnostic("invalid_record"))
                continue
            record_id = str(record.get("time_tag") or "")
            diagnostics.extend(self._unknown(record, record_id))
            if not record_id:
                diagnostics.append(self._diagnostic("missing_fields", fields=("time_tag",)))
                continue
            if "Kp" not in record:
                diagnostics.append(self._diagnostic("missing_fields", record_id, ("Kp",)))
                continue
            event_at = _provider_utc_time(record_id)
            if event_at is None:
                diagnostics.append(self._diagnostic("invalid_record", record_id, ("time_tag",)))
                continue
            try:
                kp = float(record["Kp"])
            except (TypeError, ValueError):
                diagnostics.append(self._diagnostic("invalid_record", record_id, ("Kp",)))
                continue
            metrics = {
                "kp_index": Metric(kp, "Kp", "Kp"),
                "product_semantics": Metric("observation", "product type", "SWPC product name"),
            }
            if record.get("a_running") not in (None, ""):
                try:
                    metrics["a_running"] = Metric(float(record["a_running"]), "A-index", "a_running")
                except (TypeError, ValueError):
                    pass
            if record.get("station_count") not in (None, ""):
                try:
                    metrics["station_count"] = Metric(int(record["station_count"]), "stations", "station_count")
                except (TypeError, ValueError):
                    pass
            observation = self._observation(
                body, diagnostics, record_id,
                kind=EventKind.SPACE_WEATHER,
                headline=f"Planetary K-index {kp:g}",
                summary="NOAA SWPC observed planetary K-index.", status=Status.ACTIVE,
                severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN, certainty=Certainty.UNKNOWN,
                event_at=event_at, source_updated_at=event_at, ingested_at=ingested_at,
                geometry=None, metrics=metrics,
                source_url="https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


class ReliefWebRssAdapter(CanonicalAdapter):
    provider_id = "reliefweb_rss"
    source_urls = ("https://reliefweb.int/updates/rss.xml",)

    def normalize(self, body, *, ingested_at):
        try:
            root = ET.fromstring(body)
        except (ET.ParseError, TypeError):
            return NormalizationResult(diagnostics=(self._diagnostic("malformed_body"),))
        output, diagnostics = [], []
        items = [item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "item"]
        for item in items:
            title = _xml_text(item, "title")
            link = _xml_text(item, "link")
            record_id = _xml_text(item, "guid") or link or title
            if not title or not record_id:
                diagnostics.append(self._diagnostic("missing_fields", record_id, ("title", "guid")))
                continue
            publisher = _xml_text(item, "creator", "source", "author") or "ReliefWeb"
            published = _rss_time(_xml_text(item, "pubDate", "date", "published"))
            metrics = {
                "publisher": Metric(publisher[:500], "organization", "RSS creator/source"),
                "report_semantics": Metric("humanitarian_report", "content type", "ReliefWeb feed"),
            }
            observation = self._observation(
                body, diagnostics, record_id,
                kind=EventKind.HUMANITARIAN_REPORT, headline=title[:300],
                summary=_clean_markup(_xml_text(item, "description", "summary")),
                status=Status.ACTIVE, severity=Severity.UNKNOWN, urgency=Urgency.UNKNOWN,
                certainty=Certainty.UNKNOWN, event_at=None, source_updated_at=published,
                ingested_at=ingested_at, geometry=None, metrics=metrics, source_url=_url(link),
            )
            if observation:
                output.append(observation)
        return NormalizationResult(tuple(output), tuple(diagnostics))


CORE_CANONICAL_ADAPTERS = {
    adapter.provider_id: adapter
    for adapter in (
        UsgsEarthquakeAdapter(),
        NwsAlertAdapter(),
        AviationWeatherSigmetAdapter(),
        OpenFemaDeclarationAdapter(),
        NdbcObservationAdapter(),
        CoopsWaterLevelAdapter(),
        JplFireballAdapter(),
        NhcStormAdapter(),
        TsunamiAtomAdapter(),
        GdacsGeoJsonAdapter(),
        EonetAdapter(),
        SmithsonianVolcanoAdapter(),
        SwpcKpAdapter(),
        ReliefWebRssAdapter(),
    )
}


def normalize_provider(provider_id: str, body: bytes | str, *, ingested_at: str):
    try:
        adapter = CORE_CANONICAL_ADAPTERS[provider_id]
    except KeyError as error:
        raise KeyError(f"no canonical adapter for provider: {provider_id}") from error
    return adapter.normalize(body, ingested_at=ingested_at)


def _epoch(value, multiplier=1):
    if not value:
        return 0
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * multiplier)


def _metric_value(observation, key, default=None):
    metric = observation.metrics.get(key)
    return metric.value if metric else default


def project_legacy_panel(provider_id: str, observations: tuple[Observation, ...]):
    """Project canonical fixtures into the fields consumed by current V1 panels."""
    if provider_id == "usgs_earthquakes":
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": item.provider_record_id,
                    "geometry": item.geometry,
                    "properties": {
                        "mag": _metric_value(item, "magnitude"),
                        "place": item.location_name or item.headline,
                        "time": _epoch(item.event_at, 1000),
                        "updated": _epoch(item.source_updated_at, 1000),
                        "url": item.source_url,
                        "tsunami": int(bool(_metric_value(item, "tsunami_flag", False))),
                    },
                }
                for item in observations
            ],
        }
    if provider_id == "nws_alerts":
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": item.source_url,
                    "geometry": item.geometry,
                    "properties": {
                        "id": item.provider_record_id,
                        "event": _metric_value(item, "event_name", item.headline),
                        "headline": item.headline,
                        "description": item.summary,
                        "instruction": _metric_value(item, "instruction", ""),
                        "severity": item.severity.value,
                        "urgency": item.urgency.value,
                        "certainty": item.certainty.value,
                        "onset": item.event_at,
                        "effective": item.effective_at,
                        "expires": item.expires_at,
                        "sent": item.source_updated_at,
                        "areaDesc": _metric_value(item, "affected_area", item.location_name),
                        "senderName": _metric_value(item, "sender", ""),
                    },
                }
                for item in observations
            ],
        }
    if provider_id == "nhc_storms":
        return {
            "activeStorms": [
                {
                    "id": item.provider_record_id,
                    "name": _metric_value(item, "storm_name", item.headline),
                    "classification": _metric_value(item, "classification", ""),
                    "intensity": _metric_value(item, "intensity", 0),
                    "pressure": _metric_value(item, "pressure"),
                    "latitude": item.centroid[1] if item.centroid else None,
                    "longitude": item.centroid[0] if item.centroid else None,
                    "lastUpdate": item.source_updated_at,
                }
                for item in observations
            ]
        }
    if provider_id == "noaa_tsunami":
        return {
            "items": [
                {
                    "source": _metric_value(item, "bulletin_source", "NOAA"),
                    "title": item.headline,
                    "summary": item.summary,
                    "ts": _epoch(item.source_updated_at),
                    "lat": item.centroid[1] if item.centroid else None,
                    "lon": item.centroid[0] if item.centroid else None,
                }
                for item in observations
            ]
        }
    if provider_id == "gdacs":
        kind_codes = {
            EventKind.EARTHQUAKE: "EQ", EventKind.TROPICAL_CYCLONE: "TC",
            EventKind.VOLCANO: "VO", EventKind.WILDFIRE: "WF", EventKind.DISASTER: "DR",
        }
        return {
            "items": [
                {
                    "title": item.headline, "descr": item.summary, "link": item.source_url,
                    "ts": _epoch(item.event_at),
                    "alert": _metric_value(item, "alert_level", "Green"),
                    "etype": _metric_value(
                        item, "event_type", kind_codes.get(item.kind, "DR")
                    ),
                    "lat": item.centroid[1] if item.centroid else None,
                    "lon": item.centroid[0] if item.centroid else None,
                }
                for item in observations
            ]
        }
    if provider_id == "nasa_eonet":
        return {
            "events": [
                {
                    "id": item.provider_record_id, "title": item.headline,
                    "date": item.event_at,
                    "lat": item.centroid[1] if item.centroid else None,
                    "lon": item.centroid[0] if item.centroid else None,
                    "cats": str(_metric_value(item, "category_ids", "")).split(",") if _metric_value(item, "category_ids") else [],
                    "link": item.source_url,
                }
                for item in observations
            ]
        }
    if provider_id == "smithsonian_volcano":
        return {
            "items": [
                {
                    "name": item.headline, "summary": item.summary, "link": item.source_url,
                    "lat": item.centroid[1] if item.centroid else None,
                    "lon": item.centroid[0] if item.centroid else None,
                }
                for item in observations
            ]
        }
    if provider_id == "noaa_space_weather":
        return [
            {
                "time_tag": item.event_at, "Kp": _metric_value(item, "kp_index"),
                "a_running": _metric_value(item, "a_running"),
                "station_count": _metric_value(item, "station_count"),
            }
            for item in observations
        ]
    if provider_id == "reliefweb_rss":
        return {
            "articles": [
                {
                    "ts": _epoch(item.source_updated_at), "title": item.headline,
                    "summary": item.summary, "link": item.source_url,
                    "src": _metric_value(item, "publisher", "ReliefWeb"),
                }
                for item in observations
            ]
        }
    raise KeyError(f"no V1 panel projection for provider: {provider_id}")
