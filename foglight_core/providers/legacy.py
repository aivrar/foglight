
"""V1 provider adapters retained behind a stable compatibility boundary."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from ..fetching import combine_freshness as core_combine_freshness
from ..xmlfeeds import (
    first_find as core_first_find,
)
from ..xmlfeeds import (
    iter_local as core_iter_local,
)
from ..xmlfeeds import (
    parse_rss_items as core_parse_rss_items,
)
from .hazards import (
    COMMODITIES as COMMODITIES,
)
from .hazards import (
    DEFENSE_FEEDS as DEFENSE_FEEDS,
)
from .hazards import (
    adsb_flights as adsb_flights,
)
from .hazards import (
    commodities as commodities,
)
from .hazards import (
    configure as configure_hazards,
)
from .hazards import (
    defense_wire as defense_wire,
)
from .hazards import (
    eonet_events as eonet_events,
)
from .hazards import (
    gdacs_disasters as gdacs_disasters,
)
from .hazards import (
    nasa_firms as nasa_firms,
)
from .hazards import (
    openmeteo_current as openmeteo_current,
)
from .hazards import (
    tsunami_alerts as tsunami_alerts,
)
from .hazards import (
    usgs_volcanoes_proper as usgs_volcanoes_proper,
)
from .runtime import configure as configure_runtime
from .runtime import fetch

MAX_RSS_BYTES = 2 * 1024 * 1024


def configure(fetcher, max_rss_bytes: int) -> None:
    global MAX_RSS_BYTES
    configure_runtime(fetcher)
    configure_hazards(max_rss_bytes)
    MAX_RSS_BYTES = max_rss_bytes


def _combine_freshness(values):
    return core_combine_freshness(values)


# -------- data source helpers ----------------------------------------

def usgs_quakes(window="day"):
    # window: hour, day, week, month
    url = f"https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_{window}.geojson"
    return fetch(url, ttl=60, ctype_hint="application/geo+json")


def nws_active():
    url = "https://api.weather.gov/alerts/active?status=actual&message_type=alert"
    return fetch(url, ttl=120, ctype_hint="application/geo+json")


def mempool_summary():
    """Aggregate a few mempool.space endpoints into one panel payload."""
    out = {}
    endpoints = [
        ("fees",       "https://mempool.space/api/v1/fees/recommended", 30),
        ("mempool",    "https://mempool.space/api/mempool",             20),
        ("blocks",     "https://mempool.space/api/v1/blocks",           20),
        ("difficulty", "https://mempool.space/api/v1/difficulty-adjustment", 300),
    ]

    def load(entry):
        k, u, ttl = entry
        return k, fetch(u, ttl=ttl, ctype_hint="application/json", timeout=12)

    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        results = list(pool.map(load, endpoints))

    for k, result in results:
        data, _, _, fresh = result
        try:
            out[k] = json.loads(data)
        except Exception:
            out[k] = None
        out.setdefault("_freshness", {})[k] = fresh
    return (json.dumps(out).encode("utf-8"), "application/json", 0,
            _combine_freshness(out["_freshness"].values()))


def github_events():
    url = "https://api.github.com/events?per_page=40"
    return fetch(url, ttl=20, ctype_hint="application/json")


def iss_now():
    url = "http://api.open-notify.org/iss-now.json"
    return fetch(url, ttl=10, ctype_hint="application/json")


def crypto_prices():
    # Limit the payload server-side; the UI renders only the leading assets.
    url = ("https://api.coinpaprika.com/v1/tickers"
           "?quotes=USD&limit=50")
    return fetch(url, ttl=60, ctype_hint="application/json")


def forex_latest():
    url = "https://api.frankfurter.app/latest?from=USD"
    return fetch(url, ttl=3600, ctype_hint="application/json")


def sec_filings():
    # The full firehose; we filter+slice in the proxy so the browser only
    # parses the recent slice.
    url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=&company=&datea=&dateb=&owner=include&count=40&output=atom"
    return fetch(url, ttl=120, ctype_hint="application/atom+xml")


def hn_top():
    url = "https://hacker-news.firebaseio.com/v0/topstories.json"
    return fetch(url, ttl=120, ctype_hint="application/json")


def hn_item(item_id):
    if not re.match(r"^\d+$", str(item_id)):
        return b'{"error":"bad id"}', "application/json", 0, "error"
    url = f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
    return fetch(url, ttl=300, ctype_hint="application/json")


def reddit_popular():
    # Reddit's anonymous JSON endpoint rejects desktop clients. Its public Atom
    # feed remains available without OAuth, so normalize it to the panel schema.
    url = "https://www.reddit.com/r/popular/.rss"
    body, _ctype, _age, fresh = fetch(
        url, ttl=180, ctype_hint="application/atom+xml", max_bytes=MAX_RSS_BYTES,
    )
    items = _parse_rss_items(body)
    return (json.dumps({"items": items[:25]}).encode("utf-8"),
            "application/json", 0, fresh)


def rss_proxy(url):
    return fetch(
        url, ttl=180, ctype_hint="application/rss+xml",
        max_bytes=MAX_RSS_BYTES, validate_url=True,
    )


def nhc_storms():
    """NOAA National Hurricane Center active storms --- list of currently
    active tropical cyclones with tracks. Free, no key, CORS-friendly."""
    url = "https://www.nhc.noaa.gov/CurrentStorms.json"
    return fetch(url, ttl=600, ctype_hint="application/json", timeout=10)


# -------- ReliefWeb (humanitarian sitreps) --------
# The v1 API was decommissioned and v2 requires a registered appname. The
# public RSS feed has no such gate and surfaces the same updates ReliefWeb
# pushes through their main listing page. We parse it server-side and
# return JSON so the dashboard panel can render it the same as conflict.
def reliefweb_rss():
    url = "https://reliefweb.int/updates/rss.xml"
    body, _c, _a, fresh = fetch(
        url, ttl=300, ctype_hint="application/rss+xml", timeout=15,
        max_bytes=MAX_RSS_BYTES,
    )
    items = _parse_rss_items(body)
    out = {"articles": items[:40]}
    return json.dumps(out).encode("utf-8"), "application/json", 0, fresh


# -------- Conflict Watch --------
# Aggregate three public RSS feeds that are stable, free, and naturally
# conflict-skewed:
#   - UN news / Peace and Security topic feed
#   - DW News world feed
#   - France 24 international feed
# The aggregator returns a flat JSON list of {ts, src, title, link} so the
# JS panel can render directly without per-source parsing logic.

CONFLICT_FEEDS = [
    ("UN/PEACE",  "https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml"),
    ("DW/WORLD",  "https://rss.dw.com/rdf/rss-en-world"),
    ("FRANCE24",  "https://www.france24.com/en/rss"),
]
CONFLICT_KEYWORDS = re.compile(
    r"\b(war|conflict|attack|strike|missile|drone|troops?|forces|battle|"
    r"ceasefire|truce|killed|wounded|hostage|invasion|offensive|fighting|"
    r"clash|airstrike|sanction|military|army|navy|rebels?|junta|insurgen|"
    r"genocide|atrocit|massacre|coup|protest|riot)\b", re.I)


def _first_find(parent, *paths):
    return core_first_find(parent, *paths)


def _iter_local(parent, *names):
    return core_iter_local(parent, *names)


def _parse_rss_items(xml_bytes):
    return core_parse_rss_items(xml_bytes)



def conflict_aggregate():
    """Aggregate the three RSS feeds, optionally filter to conflict keywords,
    and return a JSON list sorted by recency."""
    merged = []
    freshness = []

    def load(entry):
        src, url = entry
        result = fetch(
            url, ttl=240, ctype_hint="application/rss+xml", timeout=12,
            max_bytes=MAX_RSS_BYTES,
        )
        return src, result

    with ThreadPoolExecutor(max_workers=len(CONFLICT_FEEDS)) as pool:
        results = list(pool.map(load, CONFLICT_FEEDS))

    for src, result in results:
        body, _ctype, _age, fresh = result
        freshness.append(fresh)
        for it in _parse_rss_items(body):
            it["src"] = src
            # Keep UN/PEACE unconditionally (whole feed is on topic); keyword
            # filter the world-news feeds so we don't surface sports.
            if src == "UN/PEACE" or CONFLICT_KEYWORDS.search(it["title"] + " " + it.get("summary", "")):
                merged.append(it)
    merged.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return (json.dumps({"articles": merged[:60]}).encode("utf-8"),
            "application/json", 0, _combine_freshness(freshness))


# --- Conflict hotspots (for map overlay) -----------------------------------
# A curated dictionary of conflict-relevant regions with (lat, lon) and a list
# of name variants the regex should match. The endpoint scans recent conflict
# article titles, counts mentions per zone, and returns ordered hotspots ---
# the map plots them as pulsing red markers sized by mention count.
#
# Coordinates point at the geographic center of the active theatre (not the
# capital) so the marker actually lands on the disputed/affected area.
CONFLICT_ZONES = [
    ("Ukraine",          49.0,  32.0,  ["ukrain", "kyiv", "donetsk", "kharkiv", "odesa", "mariupol", "kherson", "zaporizh"]),
    ("Russia",           58.0,  60.0,  ["russia", "moscow", "kremlin", "kursk", "belgorod"]),
    ("Gaza",             31.4,  34.4,  ["gaza", "rafah", "khan younis"]),
    ("Israel",           31.5,  35.0,  ["israel", "tel aviv", "jerusalem", "idf"]),
    ("Lebanon",          33.9,  35.5,  ["lebanon", "beirut", "hezbollah", "south lebanon"]),
    ("West Bank",        32.0,  35.4,  ["west bank", "ramallah", "jenin", "nablus", "palestinian"]),
    ("Yemen",            15.5,  48.5,  ["yemen", "sanaa", "houthi", "aden"]),
    ("Sudan",            15.5,  30.0,  ["sudan", "khartoum", "darfur", " rsf", "sudanese"]),
    ("South Sudan",       7.9,  29.7,  ["south sudan", "juba"]),
    ("Ethiopia",          9.0,  39.0,  ["ethiopia", "amhara", "tigray", "oromia"]),
    ("Myanmar",          21.9,  95.9,  ["myanmar", "burma", "rohingya", "junta"]),
    ("Syria",            35.0,  38.0,  ["syria", "damascus", "aleppo", "idlib"]),
    ("Iran",             32.4,  53.7,  ["iran", "tehran", "iranian"]),
    ("Iraq",             33.2,  43.7,  ["iraq", "baghdad", "iraqi"]),
    ("Afghanistan",      33.9,  67.7,  ["afghanistan", "kabul", "taliban", "afghan"]),
    ("Pakistan",         30.4,  69.3,  ["pakistan", "islamabad", "balochistan", "khyber"]),
    ("DR Congo",         -4.0,  21.7,  ["congo", "drc", "goma", "kinshasa", "m23"]),
    ("Mozambique",      -18.7,  35.5,  ["mozambique", "cabo delgado"]),
    ("Haiti",            18.9, -72.3,  ["haiti", "port-au-prince"]),
    ("Somalia",           5.2,  46.2,  ["somalia", "mogadishu", "al-shabaab", "shabaab"]),
    ("Mali",             17.6,  -4.0,  ["mali", "bamako"]),
    ("Burkina Faso",     12.2,  -1.5,  ["burkina faso", "ouagadougou"]),
    ("Niger",            17.6,   8.1,  ["niger"]),
    ("Sahel",            15.0,   0.0,  ["sahel"]),
    ("Nigeria",           9.1,   8.7,  ["nigeria", "boko haram", "abuja"]),
    ("Taiwan",           23.7, 121.0,  ["taiwan", "taipei"]),
    ("South China Sea",  15.0, 115.0,  ["south china sea", "spratly", "paracel"]),
    ("China",            35.9, 104.2,  [" china ", "beijing"]),
    ("North Korea",      40.3, 127.5,  ["north korea", "pyongyang", "dprk", "kim jong"]),
    ("Venezuela",         6.4, -66.6,  ["venezuela", "caracas", "maduro"]),
    ("Colombia",          4.6, -74.1,  ["colombia", "bogota"]),
    ("Mexico",           23.6,-102.6,  ["mexico", "cartel", "sinaloa", "jalisco"]),
    ("Armenia",          40.1,  45.0,  ["armenia", "yerevan", "nagorno"]),
    ("Azerbaijan",       40.4,  47.6,  ["azerbaijan", "baku"]),
    ("Georgia",          42.3,  43.4,  ["georgia (country)", "tbilisi"]),
    ("Kosovo",           42.6,  20.9,  ["kosovo", "serbia", "belgrade"]),
    ("Cyprus",           35.1,  33.4,  ["cyprus"]),
    ("Libya",            27.0,  17.2,  ["libya", "tripoli", "benghazi"]),
    ("Tunisia",          34.0,   9.5,  ["tunisia", "tunis"]),
    ("Egypt",            27.0,  30.0,  ["egypt", "cairo", "sinai"]),
    ("Cameroon",          5.7,  12.4,  ["cameroon", "anglophone"]),
    ("Central African Republic", 6.6, 20.9, ["central african", "bangui", " car "]),
    ("Ecuador",          -1.5, -78.5,  ["ecuador", "guayaquil"]),
    ("India",            21.0,  78.0,  ["india", "kashmir", "manipur"]),
]


def conflict_hotspots():
    """Build a list of hotspot points from the most recent conflict articles
    (last 24h). The output is GeoJSON-shaped so the Leaflet client can plot
    it directly with circleMarker. Each hotspot includes the most recent
    headline mentioning that zone."""
    # Reuse the aggregator's cached result so we don't double-fetch.
    body, _c, _a, fresh = conflict_aggregate()
    try:
        arts = json.loads(body).get("articles", [])
    except Exception:
        arts = []

    features = []
    for name, lat, lon, keywords in CONFLICT_ZONES:
        matches = []
        # Compile a regex of all keywords once per zone.
        pat = re.compile("|".join(re.escape(k) for k in keywords), re.I)
        for a in arts:
            txt = (a.get("title", "") + " " + a.get("summary", "")).lower()
            if pat.search(txt):
                matches.append(a)
        if not matches:
            continue
        matches.sort(key=lambda x: x.get("ts", 0), reverse=True)
        # Recency weight (newer = bigger marker).
        now = int(time.time())
        recent_score = 0
        for m in matches[:10]:
            age_h = max(1, (now - (m.get("ts") or now)) / 3600.0)
            recent_score += 1.0 / (1.0 + age_h / 6.0)  # decay over ~6h
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "name":   name,
                "count":  len(matches),
                "score":  round(recent_score, 2),
                "latest": matches[0].get("title", ""),
                "src":    matches[0].get("src", ""),
                "ts":     matches[0].get("ts", 0),
                "recent": [
                    {"title": m.get("title", ""),
                     "src":   m.get("src", ""),
                     "ts":    m.get("ts", 0),
                     "link":  m.get("link", "")} for m in matches[:5]
                ],
            },
        })
    # Sort by score so the client can size markers consistently.
    features.sort(key=lambda f: f["properties"]["score"], reverse=True)
    return (json.dumps({"type": "FeatureCollection", "features": features}).encode("utf-8"),
            "application/geo+json", 0, fresh)


def space_weather():
    """NOAA Space Weather Prediction Center --- current K-index (geomagnetic
    storm level) and recent solar events. Free, no key."""
    url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    return fetch(url, ttl=600, ctype_hint="application/json", timeout=10)
