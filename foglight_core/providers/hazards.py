"""Normalized V1 hazard, mobility, defense, and market adapters."""

from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

from ..fetching import combine_freshness
from ..xmlfeeds import iter_local, parse_rss_items
from .runtime import fetch

MAX_RSS_BYTES = 2 * 1024 * 1024


def configure(max_rss_bytes):
    global MAX_RSS_BYTES
    MAX_RSS_BYTES = max_rss_bytes


def eonet_events():
    url = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=200"
    body, _ctype, _age, fresh = fetch(
        url, ttl=600, ctype_hint="application/json", timeout=15
    )
    try:
        raw = json.loads(body)
    except (TypeError, ValueError):
        return body, "application/json", 0, fresh
    output = []
    for event in raw.get("events", []):
        if event.get("closed"):
            continue
        geometries = event.get("geometry") or []
        if not geometries:
            continue
        geometry = geometries[-1]
        coordinates = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not (
            isinstance(coordinates, list) and len(coordinates) == 2
        ):
            if geometry.get("type") != "Polygon" or not coordinates:
                continue
            ring = coordinates[0]
            if not ring:
                continue
            coordinates = [
                sum(point[0] for point in ring) / len(ring),
                sum(point[1] for point in ring) / len(ring),
            ]
        categories = [item.get("id", "") for item in (event.get("categories") or [])]
        sources = [
            {"id": item.get("id", ""), "url": item.get("url", "")}
            for item in (event.get("sources") or [])
        ][:3]
        output.append(
            {
                "id": event.get("id"),
                "title": event.get("title"),
                "date": geometry.get("date"),
                "lat": coordinates[1],
                "lon": coordinates[0],
                "cats": categories,
                "sources": sources,
                "link": event.get("link") or (sources[0]["url"] if sources else ""),
            }
        )
    return json.dumps({"events": output}).encode(), "application/json", 0, fresh


def gdacs_disasters():
    body, _ctype, _age, fresh = fetch(
        "https://www.gdacs.org/xml/rss.xml",
        ttl=300,
        ctype_hint="application/rss+xml",
        timeout=15,
        max_bytes=MAX_RSS_BYTES,
    )
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, TypeError) as error:
        return (
            json.dumps({"items": [], "error": f"parse: {error}"}).encode(),
            "application/json",
            0,
            "error",
        )

    def text(parent, *paths):
        for path in paths:
            element = parent.find(path)
            if element is not None and element.text:
                return element.text.strip()
        return ""

    output = []
    for item in root.iter("item"):
        title = text(item, "title", "{*}title")
        description = re.sub(
            r"<[^>]+>", " ", text(item, "description", "{*}description")
        ).strip()[:400]
        published = text(item, "pubDate", "{*}pubDate")
        lat = text(item, "{*}lat")
        lon = text(item, "{*}long")
        if not (lat and lon):
            point = text(item, "{*}point").split()
            if len(point) >= 2:
                lat, lon = point[:2]
        timestamp = 0
        if published:
            try:
                timestamp = int(parsedate_to_datetime(published).timestamp())
            except (TypeError, ValueError, OverflowError):
                pass
        try:
            lat_value = float(lat) if lat else None
            lon_value = float(lon) if lon else None
        except ValueError:
            lat_value = lon_value = None
        if title:
            output.append(
                {
                    "title": title,
                    "descr": description,
                    "link": text(item, "link", "{*}link"),
                    "ts": timestamp,
                    "alert": text(item, "{*}alertlevel") or "Green",
                    "etype": text(item, "{*}eventtype")
                    or text(item, "category", "{*}category"),
                    "country": text(item, "{*}country"),
                    "lat": lat_value,
                    "lon": lon_value,
                }
            )
    output.sort(key=lambda item: item.get("ts", 0), reverse=True)
    return json.dumps({"items": output[:60]}).encode(), "application/json", 0, fresh


def usgs_volcanoes_proper():
    body, _ctype, _age, fresh = fetch(
        "https://volcano.si.edu/news/WeeklyVolcanoRSS.xml",
        ttl=3600,
        ctype_hint="application/rss+xml",
        timeout=15,
        max_bytes=MAX_RSS_BYTES,
    )
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, TypeError):
        return b'{"items": []}', "application/json", 0, "error"
    output = []
    for item in root.iter("item"):
        description = item.findtext("description") or ""
        match = re.search(r"(-?\d+\.\d+)[,\s]+(-?\d+\.\d+)", description)
        lat = lon = None
        if match:
            lat, lon = float(match.group(1)), float(match.group(2))
        output.append(
            {
                "name": (item.findtext("title") or "").strip(),
                "summary": re.sub(r"<[^>]+>", " ", description).strip()[:280],
                "link": (item.findtext("link") or "").strip(),
                "lat": lat,
                "lon": lon,
            }
        )
    return json.dumps({"items": output[:30]}).encode(), "application/json", 0, fresh


def tsunami_alerts():
    feeds = [
        ("PTWC", "https://www.tsunami.gov/events/xml/PHEBAtom.xml"),
        ("NTWC", "https://www.tsunami.gov/events/xml/PAAQAtom.xml"),
    ]
    output = []
    freshness = []
    for label, url in feeds:
        body, _ctype, _age, fresh = fetch(
            url,
            ttl=300,
            ctype_hint="application/atom+xml",
            timeout=12,
            max_bytes=MAX_RSS_BYTES,
        )
        freshness.append(fresh)
        try:
            root = ET.fromstring(body)
        except (ET.ParseError, TypeError):
            continue
        for entry in iter_local(root, "entry"):
            title = (entry.findtext("{*}title") or "").strip()
            if not title or "cancellation" in title.lower():
                continue
            summary = (entry.findtext("{*}summary") or "").strip()
            updated = (entry.findtext("{*}updated") or "").strip()
            lat = lon = None
            point = entry.findtext("{http://www.georss.org/georss}point")
            if point:
                parts = point.split()
                if len(parts) >= 2:
                    try:
                        lat, lon = float(parts[0]), float(parts[1])
                    except ValueError:
                        pass
            timestamp = 0
            if updated:
                try:
                    import datetime as dt

                    timestamp = int(
                        dt.datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
            output.append(
                {
                    "source": label,
                    "title": title,
                    "summary": summary[:300],
                    "ts": timestamp,
                    "lat": lat,
                    "lon": lon,
                }
            )
    output.sort(key=lambda item: item.get("ts", 0), reverse=True)
    return (
        json.dumps({"items": output[:20]}).encode(),
        "application/json",
        0,
        combine_freshness(freshness),
    )


def adsb_flights(lat, lon, dist_nm=250):
    try:
        lat_value = float(lat)
        lon_value = float(lon)
    except (TypeError, ValueError):
        return b'{"error": "bad coords"}', "application/json", 0, "error"
    lat_value = round(max(-89.9, min(89.9, lat_value)), 2)
    lon_value = round(((lon_value + 540) % 360) - 180, 2)
    distance = max(10, min(500, int(dist_nm)))
    url = f"https://api.adsb.lol/v2/lat/{lat_value}/lon/{lon_value}/dist/{distance}"
    return fetch(url, ttl=20, ctype_hint="application/json", timeout=15)


def nasa_firms(key, days=1):
    if not key:
        return (
            json.dumps(
                {
                    "error": "key required",
                    "hint": "paste your free NASA FIRMS MAP_KEY in Settings",
                }
            ).encode(),
            "application/json",
            0,
            "error",
        )
    days = max(1, min(10, int(days)))
    url = (
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"{key}/VIIRS_SNPP_NRT/world/{days}"
    )
    body, _ctype, _age, fresh = fetch(
        url,
        ttl=900,
        ctype_hint="text/csv",
        timeout=20,
        cache_key=f"firms:VIIRS_SNPP_NRT:world:{days}",
        log_url=(
            "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
            f"<redacted>/VIIRS_SNPP_NRT/world/{days}"
        ),
    )
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return (
            json.dumps({"items": [], "error": "FIRMS returned no data"}).encode(),
            "application/json",
            0,
            fresh,
        )
    header = [column.strip() for column in lines[0].split(",")]
    output = []
    for line in lines[1:5000]:
        parts = line.split(",")
        if len(parts) < len(header):
            continue
        row = dict(zip(header, parts))
        try:
            lat = float(row.get("latitude", "0"))
            lon = float(row.get("longitude", "0"))
            frp = float(row.get("frp", "0"))
        except ValueError:
            continue
        output.append(
            {
                "lat": lat,
                "lon": lon,
                "frp": frp,
                "ts": row.get("acq_date", "") + " " + row.get("acq_time", ""),
                "conf": row.get("confidence", ""),
            }
        )
    return json.dumps({"items": output[:1500]}).encode(), "application/json", 0, fresh


DEFENSE_FEEDS = [
    (
        "DOW",
        "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx"
        "?ContentType=1&Site=945&max=20",
    ),
    (
        "DEFNEWS",
        "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
    ),
    ("WOTR", "https://warontherocks.com/feed/"),
]


def defense_wire():
    freshness = []
    merged = []

    def load(entry):
        source, url = entry
        result = fetch(
            url,
            ttl=600,
            ctype_hint="application/rss+xml",
            timeout=12,
            max_bytes=MAX_RSS_BYTES,
        )
        return source, result

    with ThreadPoolExecutor(max_workers=len(DEFENSE_FEEDS)) as pool:
        results = list(pool.map(load, DEFENSE_FEEDS))
    for source, result in results:
        body, _ctype, _age, fresh = result
        freshness.append(fresh)
        for item in parse_rss_items(body):
            item["src"] = source
            merged.append(item)
    merged.sort(key=lambda item: item.get("ts", 0), reverse=True)
    return (
        json.dumps({"articles": merged[:80]}).encode(),
        "application/json",
        0,
        combine_freshness(freshness),
    )


COMMODITIES = [
    ("CL=F", "WTI"),
    ("BZ=F", "BRENT"),
    ("NG=F", "GAS"),
    ("GC=F", "GOLD"),
    ("SI=F", "SILVER"),
    ("HG=F", "COPPER"),
]


def commodities():
    def quote(symbol, label):
        encoded = urllib.parse.quote(symbol, safe="")
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{encoded}?interval=1d&range=5d"
        )
        body, _ctype, _age, fresh = fetch(
            url,
            ttl=300,
            ctype_hint="application/json",
            timeout=12,
            max_bytes=512 * 1024,
        )
        if fresh == "error":
            return label, None, fresh
        try:
            raw = json.loads(body)
            result = raw["chart"]["result"][0]
            closes = [
                value
                for value in result["indicators"]["quote"][0]["close"]
                if isinstance(value, (int, float))
            ]
            if not closes:
                raise ValueError("no closes")
            close = float(closes[-1])
            previous = float(closes[-2]) if len(closes) > 1 else close
            change = (close - previous) / previous * 100 if previous else 0.0
            return label, {"sym": symbol, "close": close, "chg": change}, fresh
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            return label, None, "error"

    with ThreadPoolExecutor(max_workers=len(COMMODITIES)) as pool:
        results = list(pool.map(lambda pair: quote(*pair), COMMODITIES))
    output = {}
    freshness = []
    for label, item, fresh in results:
        freshness.append(fresh)
        if item is not None:
            output[label] = item
    return (
        json.dumps({"items": output}).encode(),
        "application/json",
        0,
        combine_freshness(freshness),
    )


def openmeteo_current(lat, lon):
    try:
        lat_value = float(lat)
        lon_value = float(lon)
    except (TypeError, ValueError):
        return b'{"error": "bad lat/lon"}', "application/json", 0, "error"
    lat_value = round(max(-89.9, min(89.9, lat_value)), 2)
    lon_value = round(((lon_value + 540) % 360) - 180, 2)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat_value}&longitude={lon_value}"
        "&current=temperature_2m,wind_speed_10m,relative_humidity_2m,"
        "weather_code,wind_direction_10m,apparent_temperature,pressure_msl,cloud_cover"
        "&hourly=temperature_2m,precipitation_probability,weather_code"
        "&forecast_hours=6"
        "&timezone=auto"
    )
    return fetch(url, ttl=600, ctype_hint="application/json", timeout=12)
