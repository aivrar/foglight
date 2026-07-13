#!/usr/bin/env python3
"""Build the bounded offline Natural Earth base used by Foglight Map V2."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

SOURCE_VERSION = "5.1.1"
SOURCE_SHA256 = "6866c877d39cba9c357620878839b336d569f8c662d3cfab4cb1dbe2d39c977f"
SOURCE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "v5.1.1/geojson/ne_110m_admin_0_countries.geojson"
)
PRECISION = 3


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def quantize_coordinates(value):
    if isinstance(value, list) and value and isinstance(value[0], (int, float)):
        if len(value) < 2:
            raise ValueError("coordinate must contain longitude and latitude")
        longitude, latitude = (float(value[0]), float(value[1]))
        if not (math.isfinite(longitude) and math.isfinite(latitude)):
            raise ValueError("coordinate must be finite")
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("coordinate is outside WGS84 bounds")
        result = [round(longitude, PRECISION), round(latitude, PRECISION)]
        return [0 if item == 0 else item for item in result]
    if not isinstance(value, list):
        raise TypeError("coordinates must be nested arrays")
    return [quantize_coordinates(item) for item in value]


def build(source: dict) -> dict:
    if source.get("type") != "FeatureCollection" or not isinstance(
        source.get("features"), list
    ):
        raise ValueError("source must be a GeoJSON FeatureCollection")
    features = []
    for index, feature in enumerate(source["features"]):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"feature {index} has unsupported geometry")
        properties = feature.get("properties") or {}
        code = properties.get("ADM0_A3") or properties.get("ISO_A3") or f"NE{index:03d}"
        features.append(
            {
                "type": "Feature",
                "id": str(code),
                "properties": {
                    "name": str(properties.get("NAME_EN") or properties.get("NAME") or code),
                    "code": str(code),
                    "iso2": str(properties.get("ISO_A2") or "-99"),
                    "continent": str(properties.get("CONTINENT") or "Unknown"),
                },
                "geometry": {
                    "type": geometry["type"],
                    "coordinates": quantize_coordinates(geometry.get("coordinates")),
                },
            }
        )
    features.sort(key=lambda item: (item["properties"]["name"], item["id"]))
    return {
        "type": "FeatureCollection",
        "foglight": {
            "source": "Natural Earth Admin 0 Countries 1:110m",
            "version": SOURCE_VERSION,
            "source_url": SOURCE_URL,
            "source_sha256": SOURCE_SHA256,
            "coordinate_precision_degrees": PRECISION,
            "license": "Public domain",
        },
        "features": features,
    }


def serialized(document: dict) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    if digest(source_bytes) != SOURCE_SHA256:
        raise ValueError("Natural Earth source checksum does not match pinned v5.1.1")
    output = serialized(build(json.loads(source_bytes)))
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != output:
            raise SystemExit("generated map asset differs; rebuild it")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(output)
    print(
        json.dumps(
            {
                "features": len(json.loads(output)["features"]),
                "bytes": len(output),
                "sha256": digest(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
