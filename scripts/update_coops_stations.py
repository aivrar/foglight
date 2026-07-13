#!/usr/bin/env python3
"""Refresh the bounded CO-OPS water-level station catalog from official MDAPI."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.request
from pathlib import Path

SOURCE_URL = (
    "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/"
    "stations.json?type=waterlevels&units=metric"
)
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config" / "coops_water_level_stations.v1.json"


def fetch_catalog(*, timeout=30, attempts=4):
    request = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "Foglight/1 (+https://github.com/aivrar/foglight)"},
    )
    last_error = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise ValueError("CO-OPS station catalog exceeds 2 MiB")
            raw = json.loads(body)
            rows = raw.get("stations") or raw.get("stationList")
            if not isinstance(rows, list) or not rows:
                raise ValueError("CO-OPS station catalog is empty or malformed")
            stations = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                station_id = str(row.get("id") or "")
                name = str(row.get("name") or "").strip()
                state = str(row.get("state") or "").strip().upper()
                try:
                    latitude = float(row.get("lat"))
                    longitude = float(row.get("lng"))
                except (TypeError, ValueError):
                    continue
                if (
                    not station_id.isalnum()
                    or not name
                    or not math.isfinite(latitude)
                    or not math.isfinite(longitude)
                    or not -90 <= latitude <= 90
                    or not -180 <= longitude <= 180
                ):
                    continue
                stations.append({
                    "id": station_id,
                    "name": name[:200],
                    "state": state[:20],
                    "lat": latitude,
                    "lon": longitude,
                    "tidal": row.get("tidal") is True,
                    "great_lakes": row.get("greatlakes") is True,
                })
            stations.sort(key=lambda item: item["id"])
            if len(stations) < 300:
                raise ValueError("CO-OPS station catalog unexpectedly incomplete")
            if len({item["id"] for item in stations}) != len(stations):
                raise ValueError("CO-OPS station catalog contains duplicate station IDs")
            return {
                "schema_version": 1,
                "source_url": SOURCE_URL,
                "stations": stations,
            }
        except (OSError, ValueError, json.JSONDecodeError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("unable to refresh CO-OPS station catalog") from last_error


def write_catalog(document, output=OUTPUT):
    encoded = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required; this contacts NOAA CO-OPS")
    document = fetch_catalog()
    write_catalog(document, args.output)
    print(f"wrote {len(document['stations'])} stations to {args.output}")


if __name__ == "__main__":
    main()
