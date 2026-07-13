import copy
import json
from pathlib import Path

import pytest

from foglight_core.models import Certainty, EventKind, Severity, Status, Urgency
from foglight_core.providers.canonical import (
    CORE_CANONICAL_ADAPTERS,
    CanonicalAdapter,
    DriftDiagnostic,
    _cap,
    _clean_markup,
    _decode_json,
    _provider_utc_time,
    _rss_time,
    _timestamp_ms,
    normalize_provider,
    project_legacy_panel,
)
from foglight_core.storage import ObservationStore

INGESTED_AT = "2026-07-10T22:00:00Z"
CATALOG = json.loads(
    (Path(__file__).parent / "fixtures" / "v2" / "core_providers.json").read_text(
        encoding="utf-8"
    )
)


def encode(value):
    return value.encode() if isinstance(value, str) else json.dumps(value).encode()


def empty_body(provider_id, fixture):
    if fixture["format"] == "xml":
        return b"<feed/>" if provider_id == "noaa_tsunami" else b"<rss/>"
    if provider_id in {"usgs_earthquakes", "nws_alerts", "noaa_aviation_weather", "gdacs"}:
        return b'{"type":"FeatureCollection","features":[]}'
    if provider_id == "nhc_storms":
        return b'{"activeStorms":[]}'
    if provider_id == "nasa_eonet":
        return b'{"events":[]}'
    if provider_id == "openfema_declarations":
        return b'{"DisasterDeclarationsSummaries":[]}'
    if provider_id == "noaa_coops_water_levels":
        return b'{"metadata":{"id":"9414290","name":"San Francisco","lat":"37.8","lon":"-122.4"},"data":[]}'
    if provider_id == "nasa_jpl_fireballs":
        return b'{"signature":{"version":"1.2"},"count":0}'
    return b"[]"


def partial_body(provider_id, fixture):
    if fixture["format"] == "xml":
        return b"<feed><entry><title>Missing identity</title></entry></feed>" if provider_id == "noaa_tsunami" else b"<rss><channel><item><description>Missing identity</description></item></channel></rss>"
    value = copy.deepcopy(fixture["valid"])
    if provider_id == "noaa_aviation_weather":
        value["features"] = value["features"][:1]
    if provider_id in {"usgs_earthquakes", "nws_alerts", "noaa_aviation_weather", "gdacs"}:
        value["features"][0].pop("properties")
    elif provider_id == "nhc_storms":
        value["activeStorms"][0].pop("id")
    elif provider_id == "nasa_eonet":
        value["events"][0].pop("geometry")
    elif provider_id == "openfema_declarations":
        value["DisasterDeclarationsSummaries"][0].pop("id")
    elif provider_id == "noaa_coops_water_levels":
        value.pop("metadata")
    elif provider_id == "nasa_jpl_fireballs":
        value["data"] = value["data"][:1]
        value["count"] = 1
        value["data"][0][value["fields"].index("energy")] = None
    else:
        value[0] = ["future_field"]
    return encode(value)


def future_body(provider_id, fixture):
    if fixture["format"] == "xml":
        return fixture["valid"].replace("</item>", "<futureField>ignored</futureField></item>").replace(
            "</entry>", "<futureField>ignored</futureField></entry>"
        ).encode()
    value = copy.deepcopy(fixture["valid"])
    if provider_id in {"usgs_earthquakes", "nws_alerts", "noaa_aviation_weather", "gdacs"}:
        value["features"][0]["futureField"] = {"ignored": True}
    elif provider_id == "nhc_storms":
        value["activeStorms"][0]["futureField"] = "ignored"
    elif provider_id == "nasa_eonet":
        value["events"][0]["futureField"] = "ignored"
    elif provider_id == "openfema_declarations":
        value["DisasterDeclarationsSummaries"][0]["futureField"] = "ignored"
    elif provider_id == "noaa_coops_water_levels":
        value["data"][0]["futureField"] = "ignored"
    elif provider_id == "nasa_jpl_fireballs":
        value["futureField"] = "ignored"
    else:
        value[0]["future_field"] = "ignored"
    return encode(value)


@pytest.mark.parametrize("provider_id", sorted(CATALOG))
def test_core_adapter_golden_normal_empty_partial_malformed_and_future(provider_id):
    fixture = CATALOG[provider_id]
    valid = normalize_provider(provider_id, encode(fixture["valid"]), ingested_at=INGESTED_AT)
    assert len(valid.observations) == fixture["expected_count"]
    assert not valid.diagnostics
    assert all(item.provider_id == provider_id for item in valid.observations)
    assert all(item.raw_fingerprint and item.content_hash for item in valid.observations)

    empty = normalize_provider(provider_id, empty_body(provider_id, fixture), ingested_at=INGESTED_AT)
    assert empty.observations == ()

    partial = normalize_provider(provider_id, partial_body(provider_id, fixture), ingested_at=INGESTED_AT)
    assert partial.observations == ()
    assert {item.code for item in partial.diagnostics} & {"missing_fields", "invalid_record"}

    malformed = normalize_provider(
        provider_id,
        b"<not-closed" if fixture["format"] == "xml" else b"{not-json",
        ingested_at=INGESTED_AT,
    )
    assert malformed.observations == ()
    assert malformed.diagnostics[0].code == "malformed_body"

    future = normalize_provider(provider_id, future_body(provider_id, fixture), ingested_at=INGESTED_AT)
    assert len(future.observations) == fixture["expected_count"]
    assert [item.content_hash for item in future.observations] == [
        item.content_hash for item in valid.observations
    ]


def test_canonical_registry_is_exact_and_unknown_provider_is_explicit():
    assert set(CORE_CANONICAL_ADAPTERS) == set(CATALOG)
    assert all(
        adapter.source_urls or adapter.contextual
        for adapter in CORE_CANONICAL_ADAPTERS.values()
    )
    assert all(
        url.startswith("https://")
        for adapter in CORE_CANONICAL_ADAPTERS.values()
        for url in adapter.source_urls
    )
    assert all(
        adapter.max_context_urls and adapter.allowed_context_hosts
        for adapter in CORE_CANONICAL_ADAPTERS.values()
        if adapter.contextual
    )
    with pytest.raises(KeyError, match="no canonical adapter"):
        normalize_provider("missing", b"{}", ingested_at=INGESTED_AT)
    with pytest.raises(ValueError, match="diagnostic code"):
        DriftDiagnostic("fixture", "payload-value")
    diagnostic = DriftDiagnostic("fixture", "unknown_fields", "x" * 200, ("bad field!",))
    assert len(diagnostic.record_id) == 120
    assert diagnostic.fields == ("bad?field?",)


def test_usgs_mapping_uses_source_times_geometry_metrics_and_no_invented_cap_values():
    item = normalize_provider(
        "usgs_earthquakes", encode(CATALOG["usgs_earthquakes"]["valid"]),
        ingested_at=INGESTED_AT,
    ).observations[0]
    assert item.kind is EventKind.EARTHQUAKE
    assert item.event_at == "2026-07-10T20:00:00Z"
    assert item.source_updated_at == "2026-07-10T20:05:00Z"
    assert item.centroid == (139.7, 35.6)
    assert item.metrics["magnitude"].value == 6.2
    assert item.metrics["tsunami_flag"].value is True
    assert (item.severity, item.urgency, item.certainty) == (
        Severity.UNKNOWN, Urgency.UNKNOWN, Certainty.UNKNOWN,
    )


def test_nws_mapping_preserves_cap_fields_instructions_area_and_polygon():
    item = normalize_provider(
        "nws_alerts", encode(CATALOG["nws_alerts"]["valid"]), ingested_at=INGESTED_AT
    ).observations[0]
    assert (item.severity, item.urgency, item.certainty) == (
        Severity.SEVERE, Urgency.IMMEDIATE, Certainty.OBSERVED,
    )
    assert item.status is Status.ACTIVE
    assert item.effective_at == "2026-07-10T19:55:00Z"
    assert item.expires_at == "2026-07-10T22:00:00Z"
    assert "Move indoors" in item.summary
    assert item.metrics["affected_area"].value == "Fixture County"
    assert item.metrics["state_codes"].value == "CO"
    assert item.geometry["type"] == "Polygon"


def test_aviation_weather_preserves_advisory_contract_without_cap_invention():
    items = normalize_provider(
        "noaa_aviation_weather",
        encode(CATALOG["noaa_aviation_weather"]["valid"]),
        ingested_at=INGESTED_AT,
    ).observations
    assert len(items) == 2
    convective, volcanic = items
    assert convective.kind is EventKind.AVIATION_HAZARD
    assert convective.event_at is None
    assert convective.effective_at == "2026-07-10T21:00:00Z"
    assert convective.expires_at == "2026-07-11T01:00:00Z"
    assert convective.metrics["hazard_type"].value == "CONVECTIVE"
    assert convective.metrics["source_severity"].value == 5
    assert convective.metrics["validity_state"].value == "current"
    assert convective.metrics["movement_speed"].unit == "kn"
    assert (convective.severity, convective.urgency, convective.certainty) == (
        Severity.UNKNOWN, Urgency.UNKNOWN, Certainty.UNKNOWN,
    )
    assert volcanic.metrics["hazard_type"].value == "VOLCANIC ASH"
    assert volcanic.geometry["type"] == "Polygon"

    expired_body = copy.deepcopy(CATALOG["noaa_aviation_weather"]["valid"])
    expired_body["features"] = [expired_body["features"][0]]
    expired_body["features"][0]["properties"]["validTimeTo"] = "2026-07-10T21:30:00Z"
    expired = normalize_provider(
        "noaa_aviation_weather", encode(expired_body), ingested_at=INGESTED_AT
    ).observations[0]
    assert expired.status is Status.ENDED
    assert expired.metrics["validity_state"].value == "expired"

    invalid_body = copy.deepcopy(CATALOG["noaa_aviation_weather"]["valid"])
    invalid_body["features"] = [invalid_body["features"][0]]
    invalid_body["features"][0]["properties"]["validTimeFrom"] = "2026-07-11T02:00:00Z"
    invalid_body["features"][0]["properties"]["validTimeTo"] = "2026-07-11T01:00:00Z"
    invalid = normalize_provider(
        "noaa_aviation_weather", encode(invalid_body), ingested_at=INGESTED_AT
    )
    assert invalid.observations == ()
    assert invalid.diagnostics[0].code == "invalid_record"

    future_body = copy.deepcopy(CATALOG["noaa_aviation_weather"]["valid"])
    future_body["features"] = [future_body["features"][0]]
    future_body["features"][0]["properties"]["validTimeFrom"] = "2026-07-11T00:00:00Z"
    future_body["features"][0]["properties"]["validTimeTo"] = "2026-07-11T04:00:00Z"
    future = normalize_provider(
        "noaa_aviation_weather", encode(future_body), ingested_at=INGESTED_AT
    ).observations[0]
    assert future.status is Status.ACTIVE
    assert future.metrics["validity_state"].value == "future"


def test_openfema_declaration_is_administrative_context_not_event_onset():
    item = normalize_provider(
        "openfema_declarations",
        encode(CATALOG["openfema_declarations"]["valid"]),
        ingested_at=INGESTED_AT,
    ).observations[0]
    assert item.kind is EventKind.DISASTER_DECLARATION
    assert item.event_at is None
    assert item.effective_at == "2026-07-10T18:00:00Z"
    assert item.source_updated_at == "2026-07-10T18:20:00Z"
    assert item.status is Status.UNKNOWN
    assert item.location_name == "Adams (County), CO"
    assert item.country_codes == ("US",)
    assert item.metrics["incident_begin"].value == "2026-07-05T00:00:00Z"
    assert item.metrics["incident_end"].value == "2026-07-09T23:59:00Z"
    assert item.metrics["administrative_context"].value == "federal_disaster_declaration"
    assert item.metrics["state_code"].value == "CO"
    assert (item.severity, item.urgency, item.certainty) == (
        Severity.UNKNOWN, Urgency.UNKNOWN, Certainty.UNKNOWN,
    )
    assert item.source_url == "https://www.fema.gov/disaster/4999"

    alias_body = copy.deepcopy(CATALOG["openfema_declarations"]["valid"])
    record = alias_body["DisasterDeclarationsSummaries"][0]
    record["disasterType"] = record.pop("declarationType")
    record["title"] = record.pop("declarationTitle")
    record["declaredCountyArea"] = record.pop("designatedArea")
    record["disasterCloseOutDate"] = "2026-07-11T00:00:00Z"
    aliased = normalize_provider(
        "openfema_declarations", encode(alias_body), ingested_at=INGESTED_AT
    ).observations[0]
    assert aliased.status is Status.ENDED
    assert aliased.metrics["declaration_type"].value == "DR"
    assert aliased.location_name == "Adams (County), CO"

    invalid_body = copy.deepcopy(CATALOG["openfema_declarations"]["valid"])
    invalid_body["DisasterDeclarationsSummaries"][0]["state"] = ["CO"]
    invalid = normalize_provider(
        "openfema_declarations", encode(invalid_body), ingested_at=INGESTED_AT
    )
    assert invalid.observations == ()
    assert invalid.diagnostics[0].code == "invalid_record"


def test_ndbc_and_coops_preserve_measurement_units_quality_and_explicit_anomaly():
    ndbc = normalize_provider(
        "ndbc_observations", encode(CATALOG["ndbc_observations"]["valid"]),
        ingested_at=INGESTED_AT,
    ).observations[0]
    assert ndbc.kind is EventKind.MARINE_OBSERVATION
    assert ndbc.event_at == "2026-07-11T21:10:00Z"
    assert ndbc.centroid == (-122.408, 36.787)
    assert ndbc.provider_record_id == "46042"
    assert ndbc.source_updated_at == ndbc.event_at
    assert ndbc.metrics["source_record_id"].value == "NDBC-46042-20260711211000"
    assert "miles" not in ndbc.summary.lower()
    assert ndbc.metrics["station_id"].value == "46042"
    assert ndbc.metrics["wind_speed"].value == 19
    assert ndbc.metrics["wind_speed"].unit == "kn"
    assert ndbc.metrics["significant_wave_height"].unit == "ft"
    assert ndbc.metrics["atmospheric_pressure"].value == 1013.6
    assert ndbc.metrics["air_temperature"].unit == "°C"
    assert (ndbc.severity, ndbc.urgency, ndbc.certainty) == (
        Severity.UNKNOWN, Urgency.UNKNOWN, Certainty.UNKNOWN,
    )

    coops = normalize_provider(
        "noaa_coops_water_levels",
        encode(CATALOG["noaa_coops_water_levels"]["valid"]),
        ingested_at=INGESTED_AT,
    ).observations[0]
    assert coops.kind is EventKind.WATER_LEVEL
    assert coops.provider_record_id == "9414290"
    assert coops.metrics["source_record_id"].value == "9414290:2026-07-11 21:36"
    assert coops.event_at == "2026-07-11T21:36:00Z"
    assert coops.metrics["water_level"].value == 1.099
    assert coops.metrics["water_level"].unit == "m"
    assert coops.metrics["datum"].value == "MLLW"
    assert coops.metrics["quality_flag"].value == "preliminary"
    assert coops.metrics["data_flags"].value == "1,0,0,0"
    assert "water_level_anomaly" not in coops.metrics

    wider = copy.deepcopy(CATALOG["noaa_coops_water_levels"]["valid"])
    wider["data"].append({
        "t": "2026-07-11 20:00", "v": "0.75", "s": "0.03",
        "f": "0,0,0,0", "q": "v",
    })
    latest = normalize_provider(
        "noaa_coops_water_levels", encode(wider), ingested_at=INGESTED_AT
    ).observations
    assert len(latest) == 1
    assert latest[0].event_at == "2026-07-11T21:36:00Z"
    assert latest[0].metrics["water_level"].value == 1.099

    contextual_copy = CATALOG["ndbc_observations"]["valid"].replace(
        "3 nm SE of search location", "99 nm NW of another search location"
    ).replace(
        "Sat, 11 Jul 2026 21:15:00 GMT", "Sat, 11 Jul 2026 21:20:00 GMT"
    )
    same_station_sample = normalize_provider(
        "ndbc_observations", encode(contextual_copy), ingested_at=INGESTED_AT
    ).observations[0]
    assert same_station_sample.content_hash == ndbc.content_hash

    explicit = copy.deepcopy(CATALOG["noaa_coops_water_levels"]["valid"])
    explicit["predictions"] = [{"t": "2026-07-11 21:36", "v": "0.899"}]
    compared = normalize_provider(
        "noaa_coops_water_levels", encode(explicit), ingested_at=INGESTED_AT
    ).observations[0]
    assert compared.metrics["predicted_water_level"].value == 0.899
    assert compared.metrics["water_level_anomaly"].value == 0.2
    assert compared.metrics["water_level_anomaly"].provenance == "data.v - predictions.v"


def test_jpl_fireballs_validate_signature_fields_optional_location_and_units():
    result = normalize_provider(
        "nasa_jpl_fireballs", encode(CATALOG["nasa_jpl_fireballs"]["valid"]),
        ingested_at=INGESTED_AT,
    )
    located, unlocated = result.observations
    assert located.kind is EventKind.FIREBALL
    assert located.provider_record_id == "2026-07-01T12:34:56Z"
    assert located.event_at == "2026-07-01T12:34:56Z"
    assert located.centroid == (-20.25, 10.5)
    assert located.metrics["radiated_energy"].value == 2.3
    assert located.metrics["radiated_energy"].unit == "10^10 J"
    assert located.metrics["impact_energy"].value == 0.082
    assert located.metrics["impact_energy"].unit == "kt"
    assert located.metrics["peak_brightness_altitude"].unit == "km"
    assert located.metrics["entry_velocity"].value == 14.2
    assert located.metrics["entry_velocity"].unit == "km/s"
    assert "not real-time" in located.summary
    assert unlocated.geometry is None
    assert "not reported" in unlocated.summary
    assert (located.status, located.severity, located.urgency, located.certainty) == (
        Status.ENDED, Severity.UNKNOWN, Urgency.UNKNOWN, Certainty.UNKNOWN,
    )

    reordered = copy.deepcopy(CATALOG["nasa_jpl_fireballs"]["valid"])
    order = list(reversed(range(len(reordered["fields"]))))
    reordered["fields"] = [reordered["fields"][index] for index in order]
    reordered["data"] = [
        [row[index] for index in order] for row in reordered["data"]
    ]
    assert [item.content_hash for item in normalize_provider(
        "nasa_jpl_fireballs", encode(reordered), ingested_at=INGESTED_AT
    ).observations] == [item.content_hash for item in result.observations]

    documented = copy.deepcopy(CATALOG["nasa_jpl_fireballs"]["valid"])
    velocity_index = documented["fields"].index("vel")
    documented["fields"].pop(velocity_index)
    for row in documented["data"]:
        row.pop(velocity_index)
    documented["count"] = 2
    documented_items = normalize_provider(
        "nasa_jpl_fireballs", encode(documented), ingested_at=INGESTED_AT
    ).observations
    assert len(documented_items) == 2
    assert "entry_velocity" not in documented_items[0].metrics

    corrected = copy.deepcopy(CATALOG["nasa_jpl_fireballs"]["valid"])
    corrected["data"][0][corrected["fields"].index("energy")] = "2.4"
    corrected_item = normalize_provider(
        "nasa_jpl_fireballs", encode(corrected), ingested_at=INGESTED_AT
    ).observations[0]
    assert corrected_item.observation_id == located.observation_id
    assert corrected_item.content_hash != located.content_hash


@pytest.mark.parametrize("mutate", [
    lambda body: body["signature"].update(version="2.0"),
    lambda body: body["fields"].append("future-field"),
    lambda body: body["fields"].append({"not": "a field name"}),
    lambda body: body.update(count=3),
    lambda body: body.update(count=21),
    lambda body: body.update(count=0),
])
def test_jpl_fireball_contract_drift_fails_closed(mutate):
    body = copy.deepcopy(CATALOG["nasa_jpl_fireballs"]["valid"])
    mutate(body)
    result = normalize_provider(
        "nasa_jpl_fireballs", encode(body), ingested_at=INGESTED_AT
    )
    assert result.observations == ()
    assert "unexpected_root" in {item.code for item in result.diagnostics}


@pytest.mark.parametrize(("field", "value"), [
    ("lat-dir", None),
    ("lon-dir", "Q"),
    ("lat", "91"),
    ("alt", "not-a-number"),
    ("vel", "-1"),
    ("energy", "-1"),
    ("energy", True),
    ("lat", True),
    ("date", True),
])
def test_jpl_fireball_invalid_records_are_isolated(field, value):
    body = copy.deepcopy(CATALOG["nasa_jpl_fireballs"]["valid"])
    body["data"] = [body["data"][0]]
    body["count"] = 1
    body["data"][0][body["fields"].index(field)] = value
    result = normalize_provider(
        "nasa_jpl_fireballs", encode(body), ingested_at=INGESTED_AT
    )
    assert result.observations == ()
    assert {item.code for item in result.diagnostics} == {"invalid_record"}


def test_nhc_tsunami_gdacs_and_eonet_source_specific_semantics():
    nhc = normalize_provider("nhc_storms", encode(CATALOG["nhc_storms"]["valid"]), ingested_at=INGESTED_AT).observations[0]
    assert nhc.kind is EventKind.TROPICAL_CYCLONE
    assert nhc.metrics["intensity"].unit == "kn"
    assert nhc.metrics["pressure"].value == 970

    tsunami = normalize_provider("noaa_tsunami", encode(CATALOG["noaa_tsunami"]["valid"]), ingested_at=INGESTED_AT).observations
    assert [item.status for item in tsunami] == [Status.ACTIVE, Status.CANCELLED]
    assert tsunami[0].event_at is None
    assert tsunami[0].metrics["relation_candidate"].value

    gdacs = normalize_provider("gdacs", encode(CATALOG["gdacs"]["valid"]), ingested_at=INGESTED_AT).observations[0]
    assert gdacs.kind is EventKind.EARTHQUAKE
    assert gdacs.metrics["alert_level"].value == "Orange"
    assert gdacs.metrics["population_exposed"].value == 12000
    assert gdacs.metrics["source_severity"].unit == "Mw"
    assert gdacs.country_codes == ("JP",)
    assert gdacs.event_at == "2026-07-10T20:00:00Z"

    eonet = normalize_provider("nasa_eonet", encode(CATALOG["nasa_eonet"]["valid"]), ingested_at=INGESTED_AT).observations[0]
    assert eonet.kind is EventKind.WILDFIRE
    assert eonet.metrics["geometry_history_count"].value == 2
    assert eonet.metrics["source_ids"].value == "InciWeb"
    assert eonet.geometry["coordinates"] == [-105, 39]


def test_report_and_measurement_adapters_label_semantics_and_provenance():
    volcano = normalize_provider("smithsonian_volcano", encode(CATALOG["smithsonian_volcano"]["valid"]), ingested_at=INGESTED_AT).observations[0]
    assert volcano.event_at is None
    assert volcano.metrics["report_semantics"].value == "weekly_activity_report"

    swpc = normalize_provider("noaa_space_weather", encode(CATALOG["noaa_space_weather"]["valid"]), ingested_at=INGESTED_AT).observations[0]
    assert swpc.metrics["product_semantics"].value == "observation"
    assert swpc.metrics["kp_index"].value == 5.33

    relief = normalize_provider("reliefweb_rss", encode(CATALOG["reliefweb_rss"]["valid"]), ingested_at=INGESTED_AT).observations[0]
    assert relief.event_at is None
    assert relief.metrics["publisher"].value == "Fixture Humanitarian Organization"
    assert relief.certainty is Certainty.UNKNOWN


def test_normalizer_helper_failure_paths_are_bounded_and_non_inventive():
    with pytest.raises(NotImplementedError):
        CanonicalAdapter().normalize(b"{}", ingested_at=INGESTED_AT)
    assert _decode_json(b"[]", "fixture")[1][0].code == "unexpected_root"
    assert _decode_json(object(), "fixture")[1][0].code == "malformed_body"
    assert _timestamp_ms("not numeric") is None
    assert _timestamp_ms(10**30) is None
    assert _clean_markup(None) == ""
    assert _clean_markup("<b>A</b>  &amp; B") == "A & B"
    assert _cap(Severity, "future") is Severity.UNKNOWN
    assert _rss_time("") is None
    assert _rss_time("2026-07-10T20:00:00Z") == "2026-07-10T20:00:00Z"
    assert _rss_time("invalid") is None
    assert _provider_utc_time(None) is None
    assert _provider_utc_time("invalid") is None
    assert _provider_utc_time("2026-07-10 20:00:00.000") == "2026-07-10T20:00:00Z"


@pytest.mark.parametrize(
    ("provider_id", "body"),
    [
        ("usgs_earthquakes", {"features": "renamed"}),
        ("nws_alerts", {"features": "renamed"}),
        ("noaa_aviation_weather", {"features": "renamed"}),
        ("openfema_declarations", {"DisasterDeclarationsSummaries": "renamed"}),
        ("noaa_coops_water_levels", {"metadata": {}, "data": "renamed"}),
        ("nhc_storms", {"activeStorms": "renamed"}),
        ("gdacs", {"features": "renamed"}),
        ("nasa_eonet", {"events": "renamed"}),
    ],
)
def test_json_adapters_report_renamed_collection_fields(provider_id, body):
    result = normalize_provider(provider_id, encode(body), ingested_at=INGESTED_AT)
    assert result.observations == ()
    assert result.diagnostics[0].code == "missing_fields"


def test_record_level_invalid_and_optional_branches_do_not_drop_the_batch():
    usgs = copy.deepcopy(CATALOG["usgs_earthquakes"]["valid"])
    feature = usgs["features"][0]
    feature["properties"] = {"time": "bad", "updated": 10**30, "status": "deleted"}
    feature["geometry"] = {"type": "Point", "coordinates": [999, 0]}
    usgs["features"].insert(0, "invalid")
    result = normalize_provider("usgs_earthquakes", encode(usgs), ingested_at=INGESTED_AT)
    assert result.observations == ()
    assert {item.code for item in result.diagnostics} == {"invalid_record"}

    nws = copy.deepcopy(CATALOG["nws_alerts"]["valid"])
    properties = nws["features"][0]["properties"]
    properties.update(
        {
            "messageType": "Update",
            "severity": "FutureValue",
            "urgency": None,
            "certainty": None,
            "description": None,
            "instruction": None,
            "areaDesc": None,
            "senderName": None,
        }
    )
    updated = normalize_provider("nws_alerts", encode(nws), ingested_at=INGESTED_AT)
    assert updated.observations[0].status is Status.UPDATED
    assert updated.observations[0].severity is Severity.UNKNOWN
    properties["messageType"] = "Cancel"
    cancelled = normalize_provider("nws_alerts", encode(nws), ingested_at=INGESTED_AT)
    assert cancelled.observations[0].status is Status.CANCELLED

    nhc = copy.deepcopy(CATALOG["nhc_storms"]["valid"])
    storm = nhc["activeStorms"][0]
    for field in ("classification", "intensity", "pressure", "movementDir", "movementSpeed"):
        storm.pop(field, None)
    storm["publicAdvisory"] = "not-an-object"
    sparse = normalize_provider("nhc_storms", encode(nhc), ingested_at=INGESTED_AT)
    assert len(sparse.observations) == 1
    assert set(sparse.observations[0].metrics) == {"storm_name"}


def test_tsunami_gdacs_eonet_and_swpc_partial_record_branches():
    tsunami_body = (
        "<feed><entry><id>PHEB-fixture-1</id><title>Notice</title>"
        "<updated>bad</updated><point>bad point</point><link>https://example.test/a</link>"
        "</entry><entry><id>OTHER-fixture-1</id><title>Notice</title></entry></feed>"
    )
    tsunami = normalize_provider("noaa_tsunami", tsunami_body, ingested_at=INGESTED_AT)
    assert len(tsunami.observations) == 2
    assert tsunami.observations[0].metrics["bulletin_source"].value == "PTWC"
    assert tsunami.observations[1].metrics["bulletin_source"].value == "NOAA"
    assert any(item.code == "invalid_record" for item in tsunami.diagnostics)

    gdacs = copy.deepcopy(CATALOG["gdacs"]["valid"])
    gdacs["features"].insert(0, "invalid")
    gdacs["features"].insert(1, {"type": "Feature"})
    properties = gdacs["features"][2]["properties"]
    properties["eventtype"] = "XX"
    properties["iscurrent"] = "false"
    properties.pop("affectedcountries")
    properties.pop("severitydata")
    properties["url"] = "https://www.gdacs.org/report.aspx?eventid=1001"
    normalized = normalize_provider("gdacs", encode(gdacs), ingested_at=INGESTED_AT)
    assert len(normalized.observations) == 1
    assert normalized.observations[0].kind is EventKind.DISASTER
    assert normalized.observations[0].status is Status.ENDED

    eonet = copy.deepcopy(CATALOG["nasa_eonet"]["valid"])
    eonet["events"].insert(0, "invalid")
    eonet["events"].insert(1, {"id": "bad", "title": "Bad", "geometry": ["not-object"]})
    event = eonet["events"][2]
    event.update({"closed": "2026-07-10T21:00:00Z", "categories": None, "sources": None})
    event["geometry"][-1].pop("magnitudeValue")
    normalized = normalize_provider("nasa_eonet", encode(eonet), ingested_at=INGESTED_AT)
    assert len(normalized.observations) == 1
    assert normalized.observations[0].status is Status.ENDED
    assert normalized.observations[0].kind is EventKind.NATURAL_EVENT

    for rows, code in (
        ({"not": "rows"}, "unexpected_root"),
        (["bad-header"], "missing_fields"),
        ([[]], "missing_fields"),
        ([["time_tag", "Kp"], "bad-row", ["", "1"], ["bad", "1"], ["2026-07-10 20:00:00", "bad"]], "invalid_record"),
    ):
        result = normalize_provider("noaa_space_weather", encode(rows), ingested_at=INGESTED_AT)
        assert result.observations == ()
        assert code in {item.code for item in result.diagnostics}

    optional_bad = [["time_tag", "Kp", "a_running", "station_count"], ["2026-07-10 20:00:00", "1", "bad", "bad"]]
    result = normalize_provider("noaa_space_weather", encode(optional_bad), ingested_at=INGESTED_AT)
    assert len(result.observations) == 1
    assert set(result.observations[0].metrics) == {"kp_index", "product_semantics"}


def test_xml_reports_fall_back_to_title_identity_publisher_and_missing_time():
    volcano = normalize_provider(
        "smithsonian_volcano",
        "<rss><item><title>Title identity</title><description/></item></rss>",
        ingested_at=INGESTED_AT,
    )
    assert len(volcano.observations) == 1
    relief = normalize_provider(
        "reliefweb_rss",
        "<rss><item><title>Title identity</title><description/></item></rss>",
        ingested_at=INGESTED_AT,
    )
    assert relief.observations[0].metrics["publisher"].value == "ReliefWeb"
    assert relief.observations[0].source_updated_at is None


@pytest.mark.parametrize(
    ("provider_id", "collection_key"),
    [
        ("usgs_earthquakes", "features"),
        ("nws_alerts", "features"),
        ("nhc_storms", "activeStorms"),
        ("noaa_tsunami", "items"),
        ("gdacs", "items"),
        ("nasa_eonet", "events"),
        ("smithsonian_volcano", "items"),
        ("reliefweb_rss", "articles"),
    ],
)
def test_current_panels_receive_their_existing_shapes_from_canonical_fixtures(
    provider_id, collection_key
):
    result = normalize_provider(
        provider_id, encode(CATALOG[provider_id]["valid"]), ingested_at=INGESTED_AT
    )
    payload = project_legacy_panel(provider_id, result.observations)
    assert len(payload[collection_key]) == CATALOG[provider_id]["expected_count"]
    json.dumps(payload, allow_nan=False)
    assert all("raw_body" not in item.to_dict() for item in result.observations)


def test_panel_projections_preserve_primary_visible_values_and_reject_unknowns():
    nws = normalize_provider(
        "nws_alerts", encode(CATALOG["nws_alerts"]["valid"]), ingested_at=INGESTED_AT
    )
    assert project_legacy_panel("nws_alerts", nws.observations)["features"][0]["properties"][
        "event"
    ] == "Severe Thunderstorm Warning"
    nhc = normalize_provider(
        "nhc_storms", encode(CATALOG["nhc_storms"]["valid"]), ingested_at=INGESTED_AT
    )
    assert project_legacy_panel("nhc_storms", nhc.observations)["activeStorms"][0][
        "name"
    ] == "ALPHA"
    swpc = normalize_provider(
        "noaa_space_weather",
        encode(CATALOG["noaa_space_weather"]["valid"]),
        ingested_at=INGESTED_AT,
    )
    assert project_legacy_panel("noaa_space_weather", swpc.observations)[0]["Kp"] == 5.33
    with pytest.raises(KeyError, match="no V1 panel projection"):
        project_legacy_panel("missing", ())


def test_every_core_fixture_persists_only_canonical_records_and_hashes(tmp_path):
    store = ObservationStore(tmp_path / "canonical.sqlite3")
    expected = 0
    for provider_id, fixture in CATALOG.items():
        store.register_provider(provider_id, {"phase": 3})
        result = normalize_provider(
            provider_id, encode(fixture["valid"]), ingested_at=INGESTED_AT
        )
        for observation in result.observations:
            assert store.upsert_observation(observation)
            expected += 1

    with store.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == expected
        documents = [
            row[0] for row in connection.execute("SELECT document_json FROM observations")
        ]
    assert expected == sum(item["expected_count"] for item in CATALOG.values())
    assert all("raw_body" not in document for document in documents)
    assert all("raw_fingerprint" in document for document in documents)


def test_audit_regressions_isolate_bad_geometry_links_centers_and_gdacs_types():
    usgs = copy.deepcopy(CATALOG["usgs_earthquakes"]["valid"])
    usgs["features"][0]["geometry"] = "renamed-shape"
    result = normalize_provider("usgs_earthquakes", encode(usgs), ingested_at=INGESTED_AT)
    assert result.observations == ()
    assert result.diagnostics[0].fields == ("geometry",)

    usgs = copy.deepcopy(CATALOG["usgs_earthquakes"]["valid"])
    usgs["features"][0]["properties"]["url"] = "https://"
    result = normalize_provider("usgs_earthquakes", encode(usgs), ingested_at=INGESTED_AT)
    assert len(result.observations) == 1
    assert result.observations[0].source_url is None

    tsunami = normalize_provider(
        "noaa_tsunami",
        "<feed><entry><id>urn:uuid:fixture</id><title>Notice</title>"
        "<link href='https://www.tsunami.gov/events/PHEB/2026/07/10/code/1/a.txt'/>"
        "</entry></feed>",
        ingested_at=INGESTED_AT,
    )
    assert tsunami.observations[0].metrics["bulletin_source"].value == "PTWC"
    assert tsunami.observations[0].metrics["relation_candidate"].value == (
        "PHEB/2026/07/10/code"
    )

    gdacs = copy.deepcopy(CATALOG["gdacs"]["valid"])
    gdacs["features"][0]["properties"]["eventtype"] = "FL"
    result = normalize_provider("gdacs", encode(gdacs), ingested_at=INGESTED_AT)
    projection = project_legacy_panel("gdacs", result.observations)
    assert projection["items"][0]["etype"] == "FL"
