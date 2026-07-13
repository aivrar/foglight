"""Validated, atomic local settings persistence."""

from __future__ import annotations

import copy
import datetime as dt
import json
import math
import os
import re
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from .models import EventKind

WATCH_KINDS = {kind.value for kind in EventKind}
WATCH_KIND_LIMIT = 32
SEVERITIES = {"Unknown", "Minor", "Moderate", "Severe", "Extreme"}
NOTIFICATION_CHANGES = {"new", "escalated", "downgraded", "updated", "resolved", "cancelled"}
NOTIFICATION_KEY_LIMIT = 400
WATCH_REGIONS_JSON_LIMIT = 180 * 1024
WALL_INTERVALS = (10, 30, 60, 120, 300)

DEFAULT_SETTINGS = {
    "keys": {"nasa_firms": ""},
    "audio": {
        "master": False,
        "earthquake": True,
        "tornado": True,
        "hurricane": True,
        "bitcoin_block": True,
    },
    "panels": {
        "tv": True,
        "conflict": True,
        "cyclones": True,
        "relief": True,
        "iss": True,
        "btc": False,
        "wiki": False,
        "github": False,
        "sec": False,
        "talk": False,
    },
    "tv_channel": "aljazeera",
    "watchlist": [],
    "watch_regions": [],
    "notifications": {
        "enabled": False,
        "in_app": True,
        "system": True,
        "quiet_start": "22:00",
        "quiet_end": "07:00",
        "minimum_severity": "Moderate",
        "kinds": sorted(WATCH_KINDS - {"unknown"}),
        "changes": ["new", "escalated"],
    },
    "notification_state": {
        "seen_revision_keys": [],
        "acknowledged_keys": [],
        "snoozed": [],
    },
    "wall_display": {"interval_seconds": 30},
    "annotations": [],
    "rss_feeds": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://feeds.npr.org/1004/rss.xml",
        "https://rss.dw.com/rdf/rss-en-world",
        "https://www.france24.com/en/rss",
        "https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml",
    ],
    "display_mode": "overview",
    "first_run_done": False,
}


def clean_text(value: object, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:max_len]


def looks_like_http_url(value: object, max_len: int = 2048) -> bool:
    if not isinstance(value, str) or len(value) > max_len:
        return False
    try:
        parsed = urllib.parse.urlparse(value)
    except (TypeError, ValueError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def _watch_geometry(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("type") not in {"Point", "Polygon", "MultiPolygon"}:
        return None
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    if len(encoded) > 50_000:
        return None

    point_count = 0

    def position(item: object) -> list[float]:
        nonlocal point_count
        if not isinstance(item, list) or len(item) < 2:
            raise ValueError
        lon, lat = item[:2]
        if isinstance(lon, bool) or isinstance(lat, bool):
            raise ValueError
        lon, lat = float(lon), float(lat)
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -85 <= lat <= 85):
            raise ValueError
        point_count += 1
        if point_count > 500:
            raise ValueError
        return [lon, lat]

    def ring(item: object) -> list[list[float]]:
        if not isinstance(item, list):
            raise ValueError
        result = [position(child) for child in item]
        if len(result) < 4 or result[0] != result[-1]:
            raise ValueError
        return result

    try:
        geometry_type = value["type"]
        coordinates = value.get("coordinates")
        if geometry_type == "Point":
            clean_coordinates: Any = position(coordinates)
        elif geometry_type == "Polygon":
            if not isinstance(coordinates, list) or not coordinates:
                raise ValueError
            clean_coordinates = [ring(item) for item in coordinates]
        else:
            if not isinstance(coordinates, list) or not coordinates:
                raise ValueError
            clean_coordinates = []
            for polygon in coordinates:
                if not isinstance(polygon, list) or not polygon:
                    raise ValueError
                clean_coordinates.append([ring(item) for item in polygon])
    except (TypeError, ValueError, OverflowError):
        return None
    return {"type": geometry_type, "coordinates": clean_coordinates}


def _timestamp(value: object) -> str | None:
    text = clean_text(value, 40)
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _string_list(value: object, *, limit: int, item_limit: int, allowed=None) -> list[str]:
    if not isinstance(value, list):
        return []
    output = []
    for item in value[:limit]:
        text = clean_text(item, item_limit)
        if not text or (allowed is not None and text not in allowed) or text in output:
            continue
        output.append(text)
    return output


def sanitize_settings_patch(
    patch: object, defaults: dict[str, Any] = DEFAULT_SETTINGS
) -> dict[str, Any]:
    """Accept only the known settings schema and clamp user-controlled sizes."""
    if not isinstance(patch, dict):
        return {}
    clean: dict[str, Any] = {}

    keys = patch.get("keys")
    if isinstance(keys, dict):
        out = {}
        for key in defaults["keys"]:
            if key not in keys:
                continue
            value = keys[key]
            if value is None:
                out[key] = ""
            elif isinstance(value, str):
                out[key] = value.strip()[:512]
        if out:
            clean["keys"] = out

    for section in ("audio", "panels"):
        candidate = patch.get(section)
        if isinstance(candidate, dict):
            out = {
                key: value
                for key, value in candidate.items()
                if key in defaults[section] and isinstance(value, bool)
            }
            if out:
                clean[section] = out

    tv_channel = clean_text(patch.get("tv_channel"), 40)
    if tv_channel and re.fullmatch(r"[A-Za-z0-9_-]+", tv_channel):
        clean["tv_channel"] = tv_channel

    display_mode = clean_text(patch.get("display_mode"), 20)
    if display_mode in {"overview", "standard", "command"}:
        clean["display_mode"] = display_mode

    watchlist = patch.get("watchlist")
    if isinstance(watchlist, list):
        out = []
        for item in watchlist[:100]:
            text = clean_text(item, 100)
            if text:
                out.append(text)
        clean["watchlist"] = out

    watch_regions = patch.get("watch_regions")
    if isinstance(watch_regions, list):
        regions = []
        region_bytes = 2
        seen_ids = set()
        structured_count = 0
        for item in watch_regions[:51]:
            if not isinstance(item, dict):
                continue
            region_id = clean_text(item.get("id"), 80)
            label = clean_text(item.get("label"), 80)
            geometry = _watch_geometry(item.get("geometry"))
            scope = item.get("scope") if item.get("scope") in {"region", "global"} else "region"
            if (
                not region_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", region_id)
                or region_id in seen_ids or not label or (scope == "region" and geometry is None)
            ):
                continue
            is_legacy = region_id == "legacy:keywords" and scope == "global"
            if not is_legacy and structured_count >= 50:
                continue
            radius = item.get("radius_km", 100)
            try:
                radius = float(radius)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(radius):
                continue
            region = {
                "id": region_id,
                "label": label,
                "scope": scope,
                "geometry": geometry,
                "radius_km": max(1.0, min(2000.0, radius)),
                "kinds": _string_list(
                    item.get("kinds"), limit=WATCH_KIND_LIMIT,
                    item_limit=40, allowed=WATCH_KINDS,
                ),
                "minimum_severity": (
                    item.get("minimum_severity")
                    if item.get("minimum_severity") in SEVERITIES else "Moderate"
                ),
                "keywords": _string_list(item.get("keywords"), limit=20, item_limit=100),
                "enabled": item.get("enabled") if isinstance(item.get("enabled"), bool) else True,
            }
            encoded_bytes = len(
                json.dumps(region, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            )
            if region_bytes + encoded_bytes + (1 if regions else 0) > WATCH_REGIONS_JSON_LIMIT:
                break
            regions.append(region)
            region_bytes += encoded_bytes + (1 if len(regions) > 1 else 0)
            seen_ids.add(region_id)
            if not is_legacy:
                structured_count += 1
        clean["watch_regions"] = regions

    notifications = patch.get("notifications")
    if isinstance(notifications, dict):
        out = {}
        for name in ("enabled", "in_app", "system"):
            if isinstance(notifications.get(name), bool):
                out[name] = notifications[name]
        for name in ("quiet_start", "quiet_end"):
            value = clean_text(notifications.get(name), 5)
            if value and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                out[name] = value
        if notifications.get("minimum_severity") in SEVERITIES:
            out["minimum_severity"] = notifications["minimum_severity"]
        if isinstance(notifications.get("kinds"), list):
            out["kinds"] = _string_list(
                notifications["kinds"], limit=WATCH_KIND_LIMIT,
                item_limit=40, allowed=WATCH_KINDS
            )
        if isinstance(notifications.get("changes"), list):
            out["changes"] = _string_list(
                notifications["changes"], limit=10, item_limit=20, allowed=NOTIFICATION_CHANGES
            )
        if out:
            clean["notifications"] = out

    notification_state = patch.get("notification_state")
    if isinstance(notification_state, dict):
        out = {
            "seen_revision_keys": _string_list(
                notification_state.get("seen_revision_keys"),
                limit=NOTIFICATION_KEY_LIMIT,
                item_limit=220,
            ),
            "acknowledged_keys": _string_list(
                notification_state.get("acknowledged_keys"),
                limit=NOTIFICATION_KEY_LIMIT,
                item_limit=220,
            ),
            "snoozed": [],
        }
        snoozed = notification_state.get("snoozed")
        if isinstance(snoozed, list):
            for item in snoozed[:200]:
                if not isinstance(item, dict):
                    continue
                incident_id = clean_text(item.get("incident_id"), 200)
                until = _timestamp(item.get("until"))
                if incident_id and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", incident_id) and until:
                    out["snoozed"].append({"incident_id": incident_id, "until": until})
        clean["notification_state"] = out

    wall_display = patch.get("wall_display")
    if isinstance(wall_display, dict):
        try:
            interval = int(wall_display.get("interval_seconds"))
        except (TypeError, ValueError, OverflowError):
            interval = None
        if interval is not None:
            selected = min(WALL_INTERVALS, key=lambda value: (abs(value - interval), value))
            clean["wall_display"] = {"interval_seconds": selected}

    annotations = patch.get("annotations")
    if isinstance(annotations, list):
        out = []
        for item in annotations[:100]:
            if not isinstance(item, dict):
                continue
            try:
                lat = float(item.get("lat"))
                lon = float(item.get("lon"))
            except (TypeError, ValueError):
                continue
            lat = max(-85.0, min(85.0, lat))
            lon = ((lon + 540.0) % 360.0) - 180.0
            label = clean_text(item.get("label"), 80) or "Pinned"
            out.append({"lat": lat, "lon": lon, "label": label})
        clean["annotations"] = out

    rss_feeds = patch.get("rss_feeds")
    if isinstance(rss_feeds, list):
        out = []
        seen = set()
        for item in rss_feeds[:20]:
            url = clean_text(item, 2048)
            if not url or not looks_like_http_url(url) or url in seen:
                continue
            seen.add(url)
            out.append(url)
        clean["rss_feeds"] = out

    if isinstance(patch.get("first_run_done"), bool):
        clean["first_run_done"] = patch["first_run_done"]
    return clean


class SettingsStore:
    """Thread-safe settings store with schema validation and atomic writes."""

    def __init__(
        self, path: str | os.PathLike[str], defaults: dict[str, Any] = DEFAULT_SETTINGS
    ) -> None:
        self.path = Path(path)
        self.defaults = defaults
        self._lock = threading.Lock()

    def _read_sanitized(self) -> dict[str, Any]:
        try:
            if self.path.stat().st_size > 1024 * 1024:
                return {}
            with self.path.open(encoding="utf-8") as handle:
                raw = handle.read(1024 * 1024 + 1)
            data = json.loads(raw)
        except (OSError, ValueError):
            data = {}
        return sanitize_settings_patch(data, self.defaults)

    def load(self) -> dict[str, Any]:
        with self._lock:
            data = self._read_sanitized()
        merged = copy.deepcopy(self.defaults)
        for key, value in data.items():
            if isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key].update(value)
            elif key in merged:
                merged[key] = value
        return merged

    def save(self, patch: object) -> dict[str, Any]:
        clean_patch = sanitize_settings_patch(patch, self.defaults)
        with self._lock:
            data = self._read_sanitized()
            for key, value in clean_patch.items():
                if key not in self.defaults:
                    continue
                if isinstance(self.defaults[key], dict) and isinstance(value, dict):
                    data.setdefault(key, {}).update(value)
                else:
                    data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        return self.load()
