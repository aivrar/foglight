from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_map_assets import build, quantize_coordinates, serialized

ROOT = Path(__file__).resolve().parents[1]
MAP_ASSET = ROOT / "web/assets/natural-earth-110m-countries.v5.1.1.geojson"
EXPECTED_SHA256 = "b853e8ab6412d655dbe2fe8719d7cfde24e266db347eeb694b4df0f627a2fdb8"
LEAFLET_SHA256 = {
    "leaflet.js": "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a",
    "leaflet.css": "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6",
    "LICENSE": "53e8dc25862014e4324741ca18fbe3611e11d42ef69f59f86ea8c5389647d4cb",
    "images/layers.png": "1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6",
    "images/layers-2x.png": "066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf",
    "images/marker-icon.png": "574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437",
    "images/marker-icon-2x.png": (
        "00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528"
    ),
    "images/marker-shadow.png": (
        "264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da"
    ),
}


def test_bundled_natural_earth_asset_is_pinned_and_bounded():
    data = MAP_ASSET.read_bytes()
    document = json.loads(data)
    assert hashlib.sha256(data).hexdigest() == EXPECTED_SHA256
    assert len(data) == 202_773
    assert document["type"] == "FeatureCollection"
    assert len(document["features"]) == 177
    assert document["foglight"] == {
        "coordinate_precision_degrees": 3,
        "license": "Public domain",
        "source": "Natural Earth Admin 0 Countries 1:110m",
        "source_sha256": (
            "6866c877d39cba9c357620878839b336d569f8c662d3cfab4cb1dbe2d39c977f"
        ),
        "source_url": (
            "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
            "v5.1.1/geojson/ne_110m_admin_0_countries.geojson"
        ),
        "version": "5.1.1",
    }
    assert all(
        feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        and set(feature["properties"]) == {"name", "code", "iso2", "continent"}
        for feature in document["features"]
    )


def test_map_asset_builder_is_deterministic_and_rejects_bad_geometry():
    source = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "NAME_EN": "Fixture Land", "ADM0_A3": "FIX",
                "ISO_A2": "FX", "CONTINENT": "Fixture",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0004, -0.0], [1, 0], [1, 1], [0.0004, -0.0]]],
            },
        }],
    }
    output = build(source)
    assert output["features"][0]["geometry"]["coordinates"][0][0] == [0, 0]
    assert serialized(output) == serialized(build(source))
    with pytest.raises(ValueError, match="FeatureCollection"):
        build({"type": "Point"})
    with pytest.raises(ValueError, match="unsupported geometry"):
        build({
            "type": "FeatureCollection",
            "features": [{"geometry": {"type": "Point", "coordinates": [0, 0]}}],
        })
    with pytest.raises(ValueError, match="outside WGS84"):
        quantize_coordinates([181, 0])
    with pytest.raises(TypeError, match="nested arrays"):
        quantize_coordinates("invalid")


def test_vendored_leaflet_distribution_matches_reviewed_1_9_4_files():
    root = ROOT / "web/vendor/leaflet"
    actual = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert actual == LEAFLET_SHA256
