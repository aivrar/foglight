#!/usr/bin/env python3
"""Foglight server --- a single Python HTTP server that:

  * Serves the dashboard UI (static files from the app directory).
  * Proxies every external data source so the browser doesn't have to deal
    with CORS, User-Agent requirements (NWS, SEC), or rate-limit fan-out.
  * Caches every response on disk under the configured cache directory
    so a panel refresh doesn't re-hit the upstream when nothing changed.
  * Persists settings (BYOK keys, audio toggles, layout) under the configured
    state directory.

The Windows-native launcher sets FOGLIGHT_APP_DIR, FOGLIGHT_CACHE_DIR,
FOGLIGHT_STATE_DIR, and FOGLIGHT_LOG_DIR before importing this module.
"""
import http.server
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from http import HTTPStatus

# -------- paths ------------------------------------------------------
APP_DIR       = os.environ.get("FOGLIGHT_APP_DIR", "/opt/app")
# Static assets are served live from the app directory. index.html lives at
# APP_DIR/index.html; app.js and future static assets live under APP_DIR/web/.
WEB_DIRS      = [os.path.join(APP_DIR, "web"), APP_DIR]
INDEX_PATH    = os.path.join(APP_DIR, "index.html")
CACHE_DIR     = os.environ.get("FOGLIGHT_CACHE_DIR", "/root/foglight/cache")
STATE_DIR     = os.environ.get("FOGLIGHT_STATE_DIR", "/root/foglight/state")
LOG_DIR       = os.environ.get("FOGLIGHT_LOG_DIR", "/root/foglight/logs")
SETTINGS_PATH = os.path.join(STATE_DIR, "settings.json")
STOP_SCRIPT   = os.path.join(APP_DIR, "stop.sh")

for d in (CACHE_DIR, STATE_DIR, LOG_DIR):
    os.makedirs(d, exist_ok=True)


# -------- log rotation (server.log can grow unbounded otherwise) --------
def _rotate_log_if_huge(path, max_bytes=1024 * 1024):
    """Truncate the log if it exceeds max_bytes, keeping the most recent half.
    Safe to call any time --- best-effort, swallows errors so a wedged log
    can't take down the server."""
    try:
        st = os.stat(path)
    except OSError:
        return
    if st.st_size <= max_bytes:
        return
    try:
        with open(path, "rb") as f:
            f.seek(-(max_bytes // 2), 2)
            f.readline()  # discard partial line
            tail = f.read()
        with open(path + ".tmp", "wb") as f:
            f.write(b"[log rotated --- older entries truncated]\n")
            f.write(tail)
        os.replace(path + ".tmp", path)
    except Exception:
        pass


# Rotate at startup, and every ~10 minutes on a background thread.
_SERVER_LOG = os.path.join(LOG_DIR, "server.log")

def _log_rotator():
    while True:
        try:
            time.sleep(600)
            _rotate_log_if_huge(_SERVER_LOG)
        except Exception:
            time.sleep(60)

_rotate_log_if_huge(_SERVER_LOG)
threading.Thread(target=_log_rotator, daemon=True).start()

# -------- user-agent --- NWS and SEC require an identifying UA --------
USER_AGENT = "Foglight/1.0 (https://foglight.local; contact via app)"

# -------- settings: BYOK keys + UI prefs --------
DEFAULT_SETTINGS = {
    "keys": {
        "aisstream":      "",
        "nasa_firms":     "",
        "opensky_id":     "",
        "opensky_secret": "",
        "openweathermap": "",
        "fred":           "",
        "finnhub":        "",
    },
    "audio": {
        "master":           False,
        "earthquake":       True,
        "tornado":          True,
        "hurricane":        True,
        "gdelt_conflict":   True,
        "bitcoin_block":    True,
        "breaking_news":    True,
        "iss_pass":         False,
    },
    "panels": {
        # Foglight is a sitrep tool: war, disaster, weather, conflict hotspots.
        # The crypto and internet-pulse panels are off by default --- users who
        # want them enable from Settings -> Panels.
        "tv":         True,
        "conflict":   True,
        "cyclones":   True,
        "relief":     True,
        "iss":        True,
        "btc":        False,
        "wiki":       False,
        "github":     False,
        "sec":        False,
        "talk":       False,
    },
    "tv_channel": "aljazeera",
    # Free-text keywords; matches across all streams highlight + alert.
    "watchlist": [],
    # User-pinned points on the world map. Each: {lat, lon, label, color}.
    "annotations": [],
    "rss_feeds": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://feeds.npr.org/1004/rss.xml",
        "https://rss.dw.com/rdf/rss-en-world",
        "https://www.france24.com/en/rss",
        "https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml"
    ],
    "first_run_done": False,
}

_settings_lock = threading.Lock()


def _clean_text(value, max_len):
    if not isinstance(value, str):
        return None
    return value.strip()[:max_len]


def _looks_like_http_url(value, max_len=2048):
    if not isinstance(value, str) or len(value) > max_len:
        return False
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def _sanitize_settings_patch(patch):
    """Accept only the known settings schema and clamp user-controlled sizes."""
    if not isinstance(patch, dict):
        return {}

    clean = {}

    keys = patch.get("keys")
    if isinstance(keys, dict):
        out = {}
        for k in DEFAULT_SETTINGS["keys"]:
            if k not in keys:
                continue
            v = keys[k]
            if v is None:
                out[k] = ""
            elif isinstance(v, str):
                out[k] = v.strip()[:512]
        if out:
            clean["keys"] = out

    audio = patch.get("audio")
    if isinstance(audio, dict):
        out = {k: v for k, v in audio.items()
               if k in DEFAULT_SETTINGS["audio"] and isinstance(v, bool)}
        if out:
            clean["audio"] = out

    panels = patch.get("panels")
    if isinstance(panels, dict):
        out = {k: v for k, v in panels.items()
               if k in DEFAULT_SETTINGS["panels"] and isinstance(v, bool)}
        if out:
            clean["panels"] = out

    tv_channel = _clean_text(patch.get("tv_channel"), 40)
    if tv_channel and re.match(r"^[A-Za-z0-9_-]+$", tv_channel):
        clean["tv_channel"] = tv_channel

    watchlist = patch.get("watchlist")
    if isinstance(watchlist, list):
        out = []
        for item in watchlist[:100]:
            text = _clean_text(item, 100)
            if text:
                out.append(text)
        clean["watchlist"] = out

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
            label = _clean_text(item.get("label"), 80) or "Pinned"
            out.append({"lat": lat, "lon": lon, "label": label})
        clean["annotations"] = out

    rss_feeds = patch.get("rss_feeds")
    if isinstance(rss_feeds, list):
        out = []
        seen = set()
        for item in rss_feeds[:20]:
            url = _clean_text(item, 2048)
            if not url or not _looks_like_http_url(url):
                continue
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
        clean["rss_feeds"] = out

    first_run_done = patch.get("first_run_done")
    if isinstance(first_run_done, bool):
        clean["first_run_done"] = first_run_done

    return clean


def load_settings():
    with _settings_lock:
        try:
            with open(SETTINGS_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {}
        data = _sanitize_settings_patch(data)
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        for k, v in data.items():
            if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                merged[k].update(v)
            elif k in merged:
                merged[k] = v
        return merged


def save_settings(patch):
    patch = _sanitize_settings_patch(patch)
    with _settings_lock:
        try:
            with open(SETTINGS_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {}
        data = _sanitize_settings_patch(data)
        for k, v in (patch or {}).items():
            if k in DEFAULT_SETTINGS:
                if isinstance(DEFAULT_SETTINGS[k], dict) and isinstance(v, dict):
                    data.setdefault(k, {})
                    data[k].update(v)
                else:
                    data[k] = v
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    return load_settings()


# -------- on-disk cache --- shared across panels --------

class DiskCache:
    """Tiny disk-backed TTL cache. Key is a URL (or any string); value is bytes.
    Caches go under CACHE_DIR keyed by a sanitized filename --- so a stuck
    upstream (eg GitHub rate limit) doesn't kill a panel; the user keeps
    seeing the last good payload with a 'stale' marker."""

    def __init__(self, root):
        self.root = root
        self._lock = threading.Lock()

    def _path(self, key):
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", key)[:180]
        return os.path.join(self.root, safe + ".bin")

    def _meta_path(self, key):
        return self._path(key) + ".meta"

    def get(self, key, ttl):
        p = self._path(key)
        m = self._meta_path(key)
        try:
            st = os.stat(p)
        except FileNotFoundError:
            return None, "miss", 0
        age = time.time() - st.st_mtime
        try:
            with open(p, "rb") as f:
                data = f.read()
            with open(m) as f:
                meta = json.load(f)
        except Exception:
            return None, "miss", 0
        if age < ttl:
            return data, "hit", meta.get("ts", st.st_mtime)
        return data, "stale", meta.get("ts", st.st_mtime)

    def put(self, key, data, ctype="application/octet-stream"):
        p = self._path(key)
        m = self._meta_path(key)
        with self._lock:
            try:
                with open(p + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(p + ".tmp", p)
                with open(m + ".tmp", "w") as f:
                    json.dump({"ts": time.time(), "ctype": ctype}, f)
                os.replace(m + ".tmp", m)
            except Exception as e:
                sys.stderr.write(f"[cache] put failed for {key}: {e}\n")


CACHE = DiskCache(CACHE_DIR)


# -------- HTTP fetcher with retries / UA / timeout --------

def _validate_external_fetch_url(url):
    """Reject local/private targets for user-controlled proxy fetches."""
    if not _looks_like_http_url(url):
        return False, "bad url"
    parsed = urllib.parse.urlparse(url)
    if parsed.username or parsed.password:
        return False, "credentials are not allowed in URLs"

    host = (parsed.hostname or "").strip().strip("[]").lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        return False, "local hosts are not allowed"

    try:
        candidates = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            candidates = []
            for info in infos:
                try:
                    candidates.append(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
                except ValueError:
                    pass
        except Exception as e:
            return False, f"dns lookup failed: {e}"
    if not candidates:
        return False, "dns lookup produced no usable addresses"

    for addr in candidates:
        if (addr.is_loopback or addr.is_private or addr.is_link_local or
                addr.is_multicast or addr.is_reserved or addr.is_unspecified):
            return False, "local/private network targets are not allowed"
    return True, ""


def fetch(url, ttl=120, ctype_hint=None, extra_headers=None, timeout=10):
    """Fetch a URL with the foglight UA, cache the body for ttl seconds.
    Returns (body_bytes, content_type, age_seconds, freshness)
    where freshness is one of: live, cached, stale, error."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/xml, application/xml, */*",
    }
    if extra_headers:
        headers.update(extra_headers)

    cached, status, ts = CACHE.get(url, ttl)
    if status == "hit":
        return cached, ctype_hint or "application/json", int(time.time() - ts), "cached"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            ctype = r.headers.get("Content-Type") or (ctype_hint or "application/octet-stream")
            CACHE.put(url, data, ctype)
            return data, ctype, 0, "live"
    except Exception as e:
        sys.stderr.write(f"[fetch] {url} failed: {e}\n")
        if cached is not None:
            return cached, ctype_hint or "application/json", int(time.time() - ts), "stale"
        msg = json.dumps({"error": str(e), "url": url}).encode("utf-8")
        return msg, "application/json", 0, "error"


# -------- data source helpers ----------------------------------------

def usgs_quakes(window="day"):
    # window: hour, day, week, month
    url = f"https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_{window}.geojson"
    return fetch(url, ttl=60, ctype_hint="application/geo+json")


def gdelt_recent():
    # Recent global events with lat/lon, last 24h, with sourceurl + actor + tone.
    # GDELT's "DOC" API returns JSON when format=json.
    url = ("https://api.gdeltproject.org/api/v2/doc/doc"
           "?query=sourcelang:eng&mode=ArtList"
           "&maxrecords=75&format=json&sort=DateDesc&timespan=24h")
    return fetch(url, ttl=180, ctype_hint="application/json", timeout=15)


def gdelt_geo():
    # Geocoded events map --- returns a feature collection from GDELT GEO API.
    url = ("https://api.gdeltproject.org/api/v2/geo/geo"
           "?query=sourcelang:eng&format=geojson&mode=PointData"
           "&maxpoints=200&timespan=24h")
    return fetch(url, ttl=240, ctype_hint="application/geo+json", timeout=15)


def nws_active():
    url = "https://api.weather.gov/alerts/active?status=actual&message_type=alert"
    return fetch(url, ttl=120, ctype_hint="application/geo+json")


def mempool_summary():
    """Aggregate a few mempool.space endpoints into one panel payload."""
    out = {}
    for k, u, t in [
        ("fees",       "https://mempool.space/api/v1/fees/recommended", 30),
        ("mempool",    "https://mempool.space/api/mempool",             20),
        ("blocks",     "https://mempool.space/api/v1/blocks",           20),
        ("difficulty", "https://mempool.space/api/v1/difficulty-adjustment", 300),
    ]:
        data, _, _, fresh = fetch(u, ttl=t, ctype_hint="application/json")
        try:
            out[k] = json.loads(data)
        except Exception:
            out[k] = None
        out.setdefault("_freshness", {})[k] = fresh
    return json.dumps(out).encode("utf-8"), "application/json", 0, "live"


def github_events():
    url = "https://api.github.com/events?per_page=40"
    return fetch(url, ttl=20, ctype_hint="application/json")


def iss_now():
    url = "http://api.open-notify.org/iss-now.json"
    return fetch(url, ttl=10, ctype_hint="application/json")


def crypto_prices():
    # CoinPaprika has CORS-friendly JSON, no key, and bundles 24h change.
    url = ("https://api.coinpaprika.com/v1/tickers"
           "?quotes=USD")
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
    url = "https://www.reddit.com/r/popular.json?limit=25"
    return fetch(url, ttl=120, ctype_hint="application/json")


def rss_proxy(url):
    ok, reason = _validate_external_fetch_url(url)
    if not ok:
        return (json.dumps({"error": reason}).encode("utf-8"),
                "application/json", 0, "error")
    return fetch(url, ttl=180, ctype_hint="application/rss+xml")


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
    body, _c, _a, fresh = fetch(url, ttl=300, ctype_hint="application/rss+xml", timeout=15)
    items = _parse_rss_items(body)
    out = {"articles": items[:40]}
    return json.dumps(out).encode("utf-8"), "application/json", 0, fresh


# -------- Conflict Watch --------
# The GDELT DOC API is the canonical source for global conflict news, but
# the foglight host hits ssl handshake timeouts and 429s often enough that
# we cannot rely on it as the primary signal. Instead we aggregate three
# RSS feeds that are stable, free, and naturally conflict-skewed:
#   - UN news / Peace and Security topic feed
#   - DW News world feed
#   - France 24 international feed
# The aggregator returns a flat JSON list of {ts, src, title, link} so the
# JS panel can render directly without per-source parsing logic.
import xml.etree.ElementTree as _ET
from email.utils import parsedate_to_datetime as _parse_date

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
    """Return the first matching child element, or None.
    Uses explicit None checks --- bare `parent.find(p) or ...` is deprecated
    in Python 3.12+ (Element truthiness compares to its children count, which
    is zero for leaf elements like <pubDate>2026-...</pubDate> -> falsy)."""
    for p in paths:
        el = parent.find(p)
        if el is not None:
            return el
    return None


def _parse_rss_items(xml_bytes):
    """Return list of {ts, title, link, summary} from RSS/Atom/RDF XML.
    Handles namespace-prefixed and bare elements via wildcard {*}foo."""
    out = []
    try:
        root = _ET.fromstring(xml_bytes)
    except _ET.ParseError:
        return out
    # Find <item> (RSS / RDF) or <entry> (Atom). Tag may be namespaced.
    items = list(root.iter("{*}item")) + list(root.iter("item")) \
          + list(root.iter("{*}entry")) + list(root.iter("entry"))
    seen = set()
    for it in items:
        if id(it) in seen:
            continue
        seen.add(id(it))
        title_el = _first_find(it, "{*}title", "title")
        link_el  = _first_find(it, "{*}link", "link")
        pub_el   = _first_find(it,
            "{*}pubDate", "pubDate",
            "{*}published", "published",
            "{*}updated", "updated",
            "{http://purl.org/dc/elements/1.1/}date",
            "{*}date", "date")
        desc_el  = _first_find(it,
            "{*}description", "description",
            "{*}summary", "summary")

        title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""
        link  = ""
        if link_el is not None:
            link = (link_el.text or link_el.get("href") or "").strip()
        ts = 0
        if pub_el is not None and pub_el.text:
            txt = pub_el.text.strip()
            # Try RFC 2822 (RSS pubDate) first, then ISO 8601 (Atom).
            try:
                ts = int(_parse_date(txt).timestamp())
            except Exception:
                try:
                    # Strip "Z" then parse as ISO8601-ish via fromisoformat.
                    iso = txt.replace("Z", "+00:00")
                    import datetime as _dt
                    ts = int(_dt.datetime.fromisoformat(iso).timestamp())
                except Exception:
                    ts = 0
        summary = ""
        if desc_el is not None and desc_el.text:
            summary = re.sub(r"<[^>]+>", "", desc_el.text).strip()[:280]
        if title:
            out.append({"ts": ts, "title": title, "link": link, "summary": summary})
    return out


def conflict_aggregate():
    """Aggregate the three RSS feeds, optionally filter to conflict keywords,
    and return a JSON list sorted by recency."""
    merged = []
    for src, url in CONFLICT_FEEDS:
        body, _ctype, _age, _fresh = fetch(url, ttl=240,
                                           ctype_hint="application/rss+xml",
                                           timeout=12)
        for it in _parse_rss_items(body):
            it["src"] = src
            # Keep UN/PEACE unconditionally (whole feed is on topic); keyword
            # filter the world-news feeds so we don't surface sports.
            if src == "UN/PEACE" or CONFLICT_KEYWORDS.search(it["title"] + " " + it.get("summary", "")):
                merged.append(it)
    merged.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return (json.dumps({"articles": merged[:60]}).encode("utf-8"),
            "application/json", 0, "live")


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
    body, _c, _a, _f = conflict_aggregate()
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
            "application/geo+json", 0, "live")


def usgs_volcanoes():
    """USGS Volcano Notice (Smithsonian-style daily report). Returns recent
    activity notices. Free, no key."""
    url = "https://volcanoes.usgs.gov/vsc/api/volcanoApi/elevatedVolcanoes"
    return fetch(url, ttl=1800, ctype_hint="application/json", timeout=10)


def space_weather():
    """NOAA Space Weather Prediction Center --- current K-index (geomagnetic
    storm level) and recent solar events. Free, no key."""
    url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    return fetch(url, ttl=600, ctype_hint="application/json", timeout=10)


def eonet_events():
    """NASA EONET (Earth Observatory Natural Event Tracker) --- open natural
    events worldwide: wildfires, volcanoes, severe storms, floods, drought,
    sea/lake ice. Returns the most recent point geometry per event so the
    client can plot. Free, no key, no signup."""
    url = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=200"
    body, _c, _a, fresh = fetch(url, ttl=600, ctype_hint="application/json", timeout=15)
    try:
        raw = json.loads(body)
    except Exception:
        return body, "application/json", 0, fresh
    out = []
    for ev in raw.get("events", []):
        if ev.get("closed"):
            continue
        geoms = ev.get("geometry") or []
        if not geoms:
            continue
        # Use most recent geometry point.
        g = geoms[-1]
        coords = g.get("coordinates")
        if g.get("type") != "Point" or not (isinstance(coords, list) and len(coords) == 2):
            # Polygon events --- use centroid (rough mean).
            if g.get("type") == "Polygon" and coords:
                ring = coords[0]
                if ring:
                    lon = sum(p[0] for p in ring) / len(ring)
                    lat = sum(p[1] for p in ring) / len(ring)
                    coords = [lon, lat]
                else:
                    continue
            else:
                continue
        cats = [c.get("id", "") for c in (ev.get("categories") or [])]
        srcs = [{"id": s.get("id", ""), "url": s.get("url", "")}
                for s in (ev.get("sources") or [])][:3]
        out.append({
            "id":     ev.get("id"),
            "title":  ev.get("title"),
            "date":   g.get("date"),
            "lat":    coords[1],
            "lon":    coords[0],
            "cats":   cats,
            "sources": srcs,
            "link":   ev.get("link") or (srcs[0]["url"] if srcs else ""),
        })
    return json.dumps({"events": out}).encode("utf-8"), "application/json", 0, fresh


def gdacs_disasters():
    """GDACS (Global Disaster Alert & Coordination System) RSS --- a
    machine-coded feed of major global disasters with severity color.
    Free, no key, no signup.

    Parsed via a real XML parser (ElementTree) with wildcard-namespace
    paths so GDACS's custom gdacs:* / geo:* tags work without us having
    to know the exact namespace URI. Falls back to empty on parse failure
    so a malformed feed never crashes the endpoint."""
    url = "https://www.gdacs.org/xml/rss.xml"
    body, _c, _a, fresh = fetch(url, ttl=300, ctype_hint="application/rss+xml", timeout=15)
    items = []
    try:
        root = _ET.fromstring(body)
    except Exception as e:
        return (json.dumps({"items": [], "error": f"parse: {e}"}).encode("utf-8"),
                "application/json", 0, "error")

    def _text(parent, *paths):
        for p in paths:
            el = parent.find(p)
            if el is not None and el.text:
                return el.text.strip()
        return ""

    for it in root.iter("item"):
        title   = _text(it, "title", "{*}title")
        descr   = _text(it, "description", "{*}description")
        descr   = re.sub(r"<[^>]+>", " ", descr).strip()[:400]
        link    = _text(it, "link", "{*}link")
        pub     = _text(it, "pubDate", "{*}pubDate")
        cat     = _text(it, "category", "{*}category")
        alert   = _text(it, "{*}alertlevel")
        etype   = _text(it, "{*}eventtype")
        country = _text(it, "{*}country")
        lat     = _text(it, "{*}lat")
        lon     = _text(it, "{*}long")
        # GeoRSS point: single tag with "lat lon" as text.
        if not (lat and lon):
            pt = _text(it, "{*}point")
            parts = pt.split()
            if len(parts) >= 2:
                lat, lon = parts[0], parts[1]
        ts = 0
        if pub:
            try:
                ts = int(_parse_date(pub).timestamp())
            except Exception:
                ts = 0
        try:
            lat_f = float(lat) if lat else None
            lon_f = float(lon) if lon else None
        except ValueError:
            lat_f = lon_f = None
        if not title:
            continue
        items.append({
            "title":   title,
            "descr":   descr,
            "link":    link,
            "ts":      ts,
            "alert":   alert or "Green",
            "etype":   etype or cat,
            "country": country,
            "lat":     lat_f,
            "lon":     lon_f,
        })
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return json.dumps({"items": items[:60]}).encode("utf-8"), "application/json", 0, fresh


def usgs_volcanoes_proper():
    """USGS Volcano Hazards Program --- volcanoes currently at NORMAL+
    aviation color code or above (i.e. anything not quiet). The endpoint
    returns a flat list of {name, lat, lon, alert_level, summit_elevation,
    last_eruption}.

    The actual USGS endpoint we use is the public Smithsonian-style report:
    https://volcano.si.edu/news/WeeklyVolcanoRSS.xml --- weekly volcanic
    activity report. Free, no key, no signup. We parse the RSS and pull
    geolocation hints from <description>.

    Falls back to an empty list on parse error so the endpoint never
    breaks the dashboard."""
    url = "https://volcano.si.edu/news/WeeklyVolcanoRSS.xml"
    body, _c, _a, fresh = fetch(url, ttl=3600, ctype_hint="application/rss+xml", timeout=15)
    out = []
    try:
        root = _ET.fromstring(body)
    except Exception:
        return json.dumps({"items": out}).encode("utf-8"), "application/json", 0, "error"
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc  = (it.findtext("description") or "")
        link  = (it.findtext("link") or "").strip()
        # description has GeoRSS-style point or embedded coords
        m = re.search(r"(-?\d+\.\d+)[,\s]+(-?\d+\.\d+)", desc)
        lat = lon = None
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
        # Strip HTML for clean summary
        summary = re.sub(r"<[^>]+>", " ", desc).strip()[:280]
        out.append({"name": title, "summary": summary, "link": link,
                    "lat": lat, "lon": lon})
    return (json.dumps({"items": out[:30]}).encode("utf-8"),
            "application/json", 0, fresh)


def tsunami_alerts():
    """NOAA Tsunami Warning System --- active alerts (warnings/advisories/
    watches). Aggregated from both PTWC (Pacific) and NTWC (US) atom feeds.
    Free, no key, no signup."""
    feeds = [
        ("PTWC", "https://www.tsunami.gov/events/xml/PHEBAtom.xml"),
        ("NTWC", "https://www.tsunami.gov/events/xml/PAAQAtom.xml"),
    ]
    out = []
    for label, url in feeds:
        body, _c, _a, _f = fetch(url, ttl=300, ctype_hint="application/atom+xml", timeout=12)
        try:
            root = _ET.fromstring(body)
        except Exception:
            continue
        for entry in root.iter("{*}entry"):
            title = (entry.findtext("{*}title") or "").strip()
            sum_  = (entry.findtext("{*}summary") or "").strip()
            updated = (entry.findtext("{*}updated") or "").strip()
            # GeoRSS where clauses
            where = entry.find("{*}where") or entry.find("{*}point")
            lat = lon = None
            pt = entry.findtext("{http://www.georss.org/georss}point")
            if pt:
                parts = pt.split()
                if len(parts) >= 2:
                    try: lat, lon = float(parts[0]), float(parts[1])
                    except ValueError: pass
            # Only surface non-cancellation entries.
            if not title or "cancellation" in title.lower():
                continue
            ts = 0
            if updated:
                try:
                    iso = updated.replace("Z", "+00:00")
                    import datetime as _dt
                    ts = int(_dt.datetime.fromisoformat(iso).timestamp())
                except Exception:
                    ts = 0
            out.append({"source": label, "title": title, "summary": sum_[:300],
                        "ts": ts, "lat": lat, "lon": lon})
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return (json.dumps({"items": out[:20]}).encode("utf-8"),
            "application/json", 0, "live")


def adsb_flights(lat, lon, dist_nm=250):
    """Free public ADS-B feed. No auth, no rate limit advertised.
    Returns aircraft in a {dist_nm}-nm radius around (lat, lon).
    https://api.adsb.lol is community-funded and globally distributed."""
    try:
        latf = float(lat); lonf = float(lon)
    except Exception:
        return (json.dumps({"error": "bad coords"}).encode("utf-8"),
                "application/json", 0, "error")
    latf = round(max(-89.9, min(89.9, latf)), 2)
    lonf = round(((lonf + 540) % 360) - 180, 2)
    dist_nm = max(10, min(500, int(dist_nm)))
    url = f"https://api.adsb.lol/v2/lat/{latf}/lon/{lonf}/dist/{dist_nm}"
    return fetch(url, ttl=20, ctype_hint="application/json", timeout=15)


def nasa_firms(key, days=1):
    """NASA FIRMS active wildfires (last N days, VIIRS S-NPP). Requires a
    free MAP_KEY pasted in settings. Returns CSV --- we parse to JSON."""
    if not key:
        return (json.dumps({"error": "key required",
                            "hint": "paste your free NASA FIRMS MAP_KEY in Settings"}).encode("utf-8"),
                "application/json", 0, "error")
    days = max(1, min(10, int(days)))
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/world/{days}"
    body, _c, _a, fresh = fetch(url, ttl=900, ctype_hint="text/csv", timeout=20)
    txt = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    lines = txt.strip().split("\n")
    if not lines or len(lines) < 2:
        return (json.dumps({"items": [], "raw_preview": txt[:200]}).encode("utf-8"),
                "application/json", 0, fresh)
    header = [h.strip() for h in lines[0].split(",")]
    out = []
    for ln in lines[1:5000]:  # cap rows
        parts = ln.split(",")
        if len(parts) < len(header):
            continue
        row = dict(zip(header, parts))
        try:
            lat = float(row.get("latitude", "0"))
            lon = float(row.get("longitude", "0"))
            frp = float(row.get("frp", "0"))  # fire radiative power
        except ValueError:
            continue
        out.append({"lat": lat, "lon": lon, "frp": frp,
                    "ts": row.get("acq_date", "") + " " + row.get("acq_time", ""),
                    "conf": row.get("confidence", "")})
    return (json.dumps({"items": out[:1500]}).encode("utf-8"),
            "application/json", 0, fresh)


def owm_global_alerts(key, lat, lon):
    """OpenWeatherMap "current weather + alerts" for a single point.
    Requires the user's free key. We use the One Call 3.0 free tier."""
    if not key:
        return (json.dumps({"error": "key required",
                            "hint": "paste your free OpenWeatherMap key in Settings"}).encode("utf-8"),
                "application/json", 0, "error")
    try:
        latf = float(lat); lonf = float(lon)
    except Exception:
        return (json.dumps({"error": "bad coords"}).encode("utf-8"),
                "application/json", 0, "error")
    url = (f"https://api.openweathermap.org/data/3.0/onecall"
           f"?lat={latf}&lon={lonf}&exclude=minutely,hourly,daily&appid={key}")
    return fetch(url, ttl=600, ctype_hint="application/json", timeout=12)


# -------- Defense Wire (military / strategic-analysis RSS aggregator) -------
DEFENSE_FEEDS = [
    ("CISA",     "https://www.cisa.gov/cybersecurity-advisories/all.xml"),
    ("DEFNEWS",  "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml"),
    ("STRIPES",  "https://www.stripes.com/news/rss"),
    ("WOTR",     "https://warontherocks.com/feed/"),
]


def defense_wire():
    """Aggregated military / strategic-analysis RSS feed.
    Each item: {ts, src, title, link}."""
    merged = []
    for src, url in DEFENSE_FEEDS:
        body, _c, _a, _f = fetch(url, ttl=600, ctype_hint="application/rss+xml", timeout=12)
        for it in _parse_rss_items(body):
            it["src"] = src
            merged.append(it)
    merged.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return (json.dumps({"articles": merged[:80]}).encode("utf-8"),
            "application/json", 0, "live")


# -------- Commodity prices (Stooq free CSV) ---------------------------------
COMMODITIES = [
    ("CL.F", "WTI"),    # Crude oil futures
    ("BZ.F", "BRENT"),  # Brent crude
    ("NG.F", "GAS"),    # Nat gas
    ("GC.F", "GOLD"),   # Gold
    ("SI.F", "SILVER"), # Silver
    ("HG.F", "COPPER"), # Copper
]


def commodities():
    """Fetch a batch of commodity tickers from Stooq's free CSV endpoint.
    No key, no signup, attribution polite. Returns latest close + 1-day chg."""
    syms = ",".join(s.lower() for s, _ in COMMODITIES)
    url = f"https://stooq.com/q/l/?s={syms}&f=sd2t2ohlc&h&e=csv"
    body, _c, _a, fresh = fetch(url, ttl=120, ctype_hint="text/csv", timeout=10)
    txt = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    rows = txt.strip().split("\n")
    out = {}
    if len(rows) < 2:
        return (json.dumps({"items": []}).encode("utf-8"),
                "application/json", 0, fresh)
    header = [h.strip().lower() for h in rows[0].split(",")]
    for ln in rows[1:]:
        parts = ln.split(",")
        if len(parts) < len(header):
            continue
        row = dict(zip(header, parts))
        sym = row.get("symbol", "").upper()
        try:
            close = float(row.get("close", "0"))
            open_ = float(row.get("open", "0"))
        except ValueError:
            continue
        chg = (close - open_) / open_ * 100 if open_ else 0
        label = next((l for s, l in COMMODITIES if s == sym), sym)
        out[label] = {"sym": sym, "close": close, "chg": chg}
    return (json.dumps({"items": out}).encode("utf-8"),
            "application/json", 0, fresh)


def openmeteo_current(lat, lon):
    """Open-Meteo current weather + 6h forecast for a single point.
    Free, no key, no signup."""
    try:
        latf = float(lat); lonf = float(lon)
    except Exception:
        return (json.dumps({"error": "bad lat/lon"}).encode("utf-8"),
                "application/json", 0, "error")
    # Clamp + round to 2dp so cache key reuses common points.
    latf = round(max(-89.9, min(89.9, latf)), 2)
    lonf = round(((lonf + 540) % 360) - 180, 2)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latf}&longitude={lonf}"
        "&current=temperature_2m,wind_speed_10m,relative_humidity_2m,"
        "weather_code,wind_direction_10m,apparent_temperature,pressure_msl,cloud_cover"
        "&hourly=temperature_2m,precipitation_probability,weather_code"
        "&forecast_hours=6"
        "&timezone=auto"
    )
    return fetch(url, ttl=600, ctype_hint="application/json", timeout=12)


# -------- Wikipedia EventStreams SSE -> WebSocket-ish polling --------
# The browser cannot connect directly to wikimedia's SSE endpoint via the
# WSL2 distro IP if the host has a strict TLS proxy --- so we tail the
# stream server-side, accumulate a rolling buffer, and the panel polls
# /api/wiki/recent for the last N events.

class WikiEditStream(threading.Thread):
    URL = "https://stream.wikimedia.org/v2/stream/recentchange"
    daemon = True

    def __init__(self):
        super().__init__(name="wiki-stream")
        self._buf = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_ok = 0.0

    def run(self):
        while not self._stop.is_set():
            try:
                req = urllib.request.Request(self.URL, headers={
                    "User-Agent": USER_AGENT, "Accept": "text/event-stream",
                })
                with urllib.request.urlopen(req, timeout=20) as r:
                    self._last_ok = time.time()
                    data_lines = []
                    for raw in r:
                        if self._stop.is_set():
                            return
                        line = raw.decode("utf-8", errors="replace").rstrip("\n")
                        if not line:
                            if data_lines:
                                payload = "\n".join(data_lines)
                                data_lines = []
                                try:
                                    obj = json.loads(payload)
                                    self._append(obj)
                                except Exception:
                                    pass
                            continue
                        if line.startswith("data: "):
                            data_lines.append(line[6:])
                        elif line.startswith("data:"):
                            data_lines.append(line[5:])
            except Exception as e:
                sys.stderr.write(f"[wiki] stream error: {e} --- reconnecting in 5s\n")
                self._stop.wait(5)

    def _append(self, obj):
        if obj.get("type") not in ("edit", "new"):
            return
        compact = {
            "wiki":    obj.get("wiki"),
            "title":   obj.get("title"),
            "user":    obj.get("user"),
            "bot":     obj.get("bot"),
            "type":    obj.get("type"),
            "ts":      obj.get("timestamp") or int(time.time()),
            "comment": (obj.get("comment") or "")[:140],
            "length":  (obj.get("length") or {}),
            "serverurl": obj.get("server_url"),
        }
        with self._lock:
            self._buf.append(compact)
            if len(self._buf) > 500:
                self._buf = self._buf[-500:]

    def snapshot(self, limit=60):
        with self._lock:
            return list(self._buf[-limit:])


WIKI = WikiEditStream()
WIKI.start()


# -------- shutdown helpers ------------------------------------------

def schedule_distro_termination(after_seconds=2.0):
    """Best-effort: ask wslservice to terminate our own distro.

    NOTE: WSL2 does NOT allow a process inside a distro to terminate that
    same distro --- the call is silently ignored. The actual termination
    is performed by the Foglight.cmd wrapper AFTER foglight.exe exits
    (when the call comes from the Windows host, where it works correctly).
    We still fire this call as a no-cost belt-and-suspenders attempt in
    case a future WSL build relaxes the restriction.

    Wrapped in `bash -c 'sleep N; wsl.exe ...'` so the 2-second countdown
    is owned by a fresh detached shell process --- a sleep inside *this*
    Python would be killed when we os._exit() a moment later."""
    name = os.environ.get("WSL_DISTRO_NAME", "linbox-foglight")
    try:
        subprocess.Popen(
            ["bash", "-c",
             f"sleep {after_seconds}; exec wsl.exe --terminate {name} >/dev/null 2>&1"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception as e:
        sys.stderr.write(f"[shutdown] could not schedule wsl --terminate: {e}\n")


def spawn_stop_async(delay=0.0, terminate_distro=True):
    """Fire stop.sh and (optionally) schedule wsl --terminate as detached
    children. Both survive this process's os._exit() so the cleanup chain
    can complete even after we're gone."""
    def worker():
        if delay:
            time.sleep(delay)
        try:
            subprocess.Popen(
                [STOP_SCRIPT],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception as e:
            sys.stderr.write(f"[shutdown] stop.sh spawn failed: {e}\n")
    threading.Thread(target=worker, daemon=True).start()
    if terminate_distro:
        # Schedule this BEFORE we exit, not inside the daemon thread, so the
        # `sleep N` countdown is owned by an independent shell process that
        # is unaffected when our Python dies.
        schedule_distro_termination(after_seconds=2.0)


# -------- HTTP request handler --------------------------------------

def _netloc_parts(netloc):
    netloc = (netloc or "").split("@")[-1].strip().lower()
    if netloc.startswith("["):
        host, _, rest = netloc[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
        return host.rstrip("."), port
    host, sep, port = netloc.rpartition(":")
    if not sep:
        host, port = netloc, ""
    return host.rstrip("."), port


def _origin_matches_host(url_value, host_header):
    try:
        parsed = urllib.parse.urlparse(url_value)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    origin_host, origin_port = _netloc_parts(parsed.netloc)
    request_host, request_port = _netloc_parts(host_header)
    default_port = "443" if parsed.scheme == "https" else "80"
    origin_port = origin_port or default_port
    request_port = request_port or default_port
    return origin_host == request_host and origin_port == request_port


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Foglight/1"
    protocol_version = "HTTP/1.1"

    # -- response helpers --

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8",
              extra_headers=None, freshness=None, age=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # YouTube embeds need a valid referrer/origin. This policy sends only
        # the app origin cross-site, avoiding full URL leakage while keeping
        # the Live TV panel playable.
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        origin = self.headers.get("Origin")
        if origin and _origin_matches_host(origin, self.headers.get("Host", "")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(body)))
        if freshness is not None:
            self.send_header("X-Foglight-Freshness", freshness)
        if age is not None:
            self.send_header("X-Foglight-Age", str(age))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            try:
                self.wfile.write(body)
            except Exception:
                pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > 256 * 1024:
            raise ValueError("request body too large")
        try:
            raw = self.rfile.read(n)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _same_origin_or_no_origin(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if origin:
            return _origin_matches_host(origin, host)
        referer = self.headers.get("Referer")
        if referer:
            return _origin_matches_host(referer, host)
        # Allow curl, WebView host integrations, and local smoke-test tooling.
        return True

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {fmt % args}\n")

    # -- static serving --

    # Only these extensions can be served as static assets. Root-level .json
    # such as app.json stays inaccessible even though APP_DIR is also a web root.
    _STATIC_EXTS = {".html", ".js", ".css", ".json", ".svg", ".png", ".ico",
                    ".wav", ".mp3", ".ogg", ".woff", ".woff2", ".webp", ".gif"}
    _CTYPE = {
        ".html": "text/html; charset=utf-8",
        ".js":   "application/javascript; charset=utf-8",
        ".css":  "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg":  "image/svg+xml",
        ".png":  "image/png", ".gif": "image/gif", ".webp": "image/webp",
        ".ico":  "image/x-icon",
        ".wav":  "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
        ".woff": "font/woff", ".woff2": "font/woff2",
    }

    def _serve_static(self, rel):
        # Root request --- always serve the live index.html.
        if rel in ("", "/"):
            try:
                with open(INDEX_PATH, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                return self._send(500, f"index read: {e}".encode())

        rel = rel.lstrip("/")
        ext = os.path.splitext(rel)[1].lower()
        # Hard reject anything not on the allowlist (the source dir also
        # contains .py / .sh files we MUST NOT expose).
        if ext not in self._STATIC_EXTS:
            return self._send(404, b"not found")

        # Try each web root in order until we find the file.
        for root in WEB_DIRS:
            if root == APP_DIR and ext == ".json":
                continue
            full = os.path.realpath(os.path.join(root, rel))
            root_real = os.path.realpath(root)
            if not (full == root_real or full.startswith(root_real + os.sep)):
                continue
            if os.path.isfile(full):
                try:
                    with open(full, "rb") as f:
                        return self._send(200, f.read(),
                                          self._CTYPE.get(ext, "application/octet-stream"))
                except Exception as e:
                    return self._send(500, f"read error: {e}".encode())
        return self._send(404, b"not found")

    # -- proxy helper --

    def _proxy(self, fn, *args, **kwargs):
        try:
            body, ctype, age, fresh = fn(*args, **kwargs)
            return self._send(200, body, ctype, freshness=fresh, age=age)
        except Exception as e:
            return self._send_json({"error": str(e)}, 502)

    # -- routing --

    def do_OPTIONS(self):
        if not self._same_origin_or_no_origin():
            return self._send(403, b"forbidden")
        return self._send(204, b"", extra_headers={
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        })

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        qs = urllib.parse.parse_qs(url.query)

        if path == "/api/ping":
            return self._send_json({"ok": True, "ts": time.time()})

        if path == "/api/settings":
            s = load_settings()
            # Mask keys --- never send full values back to the browser, just
            # the "is it set" bit. Audio + rss come back verbatim.
            masked = json.loads(json.dumps(s))
            for k, v in masked.get("keys", {}).items():
                masked["keys"][k] = bool(v)
            return self._send_json(masked)

        # ---- data sources ----
        if path == "/api/usgs":
            window = qs.get("window", ["day"])[0]
            return self._proxy(usgs_quakes, window if window in ("hour", "day", "week", "month") else "day")
        if path == "/api/gdelt":
            return self._proxy(gdelt_recent)
        if path == "/api/gdelt-geo":
            return self._proxy(gdelt_geo)
        if path == "/api/nws":
            return self._proxy(nws_active)
        if path == "/api/mempool":
            return self._proxy(mempool_summary)
        if path == "/api/github":
            return self._proxy(github_events)
        if path == "/api/iss":
            return self._proxy(iss_now)
        if path == "/api/crypto":
            return self._proxy(crypto_prices)
        if path == "/api/forex":
            return self._proxy(forex_latest)
        if path == "/api/sec":
            return self._proxy(sec_filings)
        if path == "/api/hn/top":
            return self._proxy(hn_top)
        if path.startswith("/api/hn/item/"):
            return self._proxy(hn_item, path.rsplit("/", 1)[-1])
        if path == "/api/reddit":
            return self._proxy(reddit_popular)
        if path == "/api/rss":
            url_arg = qs.get("url", [""])[0]
            if not url_arg or not url_arg.startswith(("http://", "https://")):
                return self._send_json({"error": "bad url"}, 400)
            return self._proxy(rss_proxy, url_arg)
        if path == "/api/cyclones":
            return self._proxy(nhc_storms)
        if path == "/api/relief":
            return self._proxy(reliefweb_rss)
        if path == "/api/gdelt-conflict":
            return self._proxy(conflict_aggregate)
        if path == "/api/conflict-hotspots":
            return self._proxy(conflict_hotspots)
        if path == "/api/volcanoes":
            return self._proxy(usgs_volcanoes)
        if path == "/api/space-weather":
            return self._proxy(space_weather)
        if path == "/api/eonet":
            return self._proxy(eonet_events)
        if path == "/api/gdacs":
            return self._proxy(gdacs_disasters)
        if path == "/api/openmeteo":
            lat = qs.get("lat", ["0"])[0]
            lon = qs.get("lon", ["0"])[0]
            return self._proxy(openmeteo_current, lat, lon)
        if path == "/api/tsunami":
            return self._proxy(tsunami_alerts)
        if path == "/api/volcanoes-real":
            return self._proxy(usgs_volcanoes_proper)
        if path == "/api/flights":
            lat = qs.get("lat", ["35"])[0]
            lon = qs.get("lon", ["20"])[0]
            dist = qs.get("dist", ["250"])[0]
            return self._proxy(adsb_flights, lat, lon, dist)
        if path == "/api/firms":
            # Use the user's saved MAP_KEY from settings (server-side --- we
            # never echo it to the browser).
            settings = load_settings()
            key = (settings.get("keys") or {}).get("nasa_firms", "")
            return self._proxy(nasa_firms, key)
        if path == "/api/defense-wire":
            return self._proxy(defense_wire)
        if path == "/api/commodities":
            return self._proxy(commodities)
        if path == "/api/owm-alerts":
            settings = load_settings()
            key = (settings.get("keys") or {}).get("openweathermap", "")
            lat = qs.get("lat", ["0"])[0]
            lon = qs.get("lon", ["0"])[0]
            return self._proxy(owm_global_alerts, key, lat, lon)
        if path == "/api/wiki/recent":
            limit = 60
            try:
                limit = max(1, min(200, int(qs.get("limit", ["60"])[0])))
            except Exception:
                pass
            return self._send_json({"events": WIKI.snapshot(limit)})

        # ---- static ----
        return self._serve_static(path)

    def do_HEAD(self):
        return self.do_GET()

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path

        if path in ("/api/shutdown", "/api/settings") and not self._same_origin_or_no_origin():
            return self._send_json({"error": "forbidden"}, 403)

        if path == "/api/shutdown":
            # Always return 200 first so the browser has a clean response
            # before the process tree starts dying. The actual kill runs
            # on a tiny delay so this response can flush.
            self._send_json({"ok": True, "shutdown": "in_progress"})
            try:
                self.wfile.flush()
            except Exception:
                pass
            spawn_stop_async(delay=0.25)
            # And as a final safety net inside this process, exit after
            # giving the response time to flush. stop.sh will sweep up.
            threading.Thread(
                target=lambda: (time.sleep(1.0), os._exit(0)),
                daemon=True,
            ).start()
            return

        if path == "/api/settings":
            try:
                patch = self._read_body() or {}
            except ValueError as e:
                return self._send_json({"error": str(e)}, 413)
            # If the patch has a `keys` dict, any value that is None or an
            # empty string clears the key; any truthy string replaces it.
            merged = save_settings(patch)
            masked = json.loads(json.dumps(merged))
            for k, v in masked.get("keys", {}).items():
                masked["keys"][k] = bool(v)
            return self._send_json(masked)

        return self._send(404, b"not found")


# -------- shutdown signals ------------------------------------------

def _on_signal(signum, _frame):
    sys.stderr.write(f"[foglight] signal {signum} --- shutting down\n")
    spawn_stop_async()
    time.sleep(0.5)
    os._exit(0)


def main():
    import signal
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9787

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)
    try:
        signal.signal(signal.SIGHUP, _on_signal)  # POSIX-only
    except (AttributeError, ValueError):
        pass

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write(f"[foglight] listening on 0.0.0.0:{port}\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _on_signal(2, None)


if __name__ == "__main__":
    main()
