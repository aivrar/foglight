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
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus

from foglight_core.cache import DiskCache
from foglight_core.fetching import (
    UPSTREAM_REQUEST_GATE,
    URL_OPENER,
    SafeRedirectHandler,
)
from foglight_core.fetching import (
    combine_freshness as core_combine_freshness,
)
from foglight_core.fetching import (
    fetch as core_fetch,
)
from foglight_core.fetching import (
    read_limited as core_read_limited,
)
from foglight_core.fetching import (
    redact_url as core_redact_url,
)
from foglight_core.fetching import (
    validate_external_fetch_url as core_validate_external_fetch_url,
)
from foglight_core.jsonfiles import load_bounded_json
from foglight_core.providers import (
    CORE_CANONICAL_ADAPTERS,
    FunctionProviderAdapter,
    ProviderRegistry,
)
from foglight_core.providers.coastal import (
    CoastalContextPlanner,
    load_coops_stations,
)
from foglight_core.providers.legacy import (
    COMMODITIES as COMMODITIES,
)
from foglight_core.providers.legacy import (
    CONFLICT_FEEDS as CONFLICT_FEEDS,
)
from foglight_core.providers.legacy import (
    CONFLICT_KEYWORDS as CONFLICT_KEYWORDS,
)
from foglight_core.providers.legacy import (
    CONFLICT_ZONES as CONFLICT_ZONES,
)
from foglight_core.providers.legacy import (
    DEFENSE_FEEDS as DEFENSE_FEEDS,
)
from foglight_core.providers.legacy import (
    adsb_flights,
    commodities,
    conflict_aggregate,
    conflict_hotspots,
    crypto_prices,
    defense_wire,
    eonet_events,
    forex_latest,
    gdacs_disasters,
    github_events,
    hn_item,
    hn_top,
    iss_now,
    mempool_summary,
    nasa_firms,
    nhc_storms,
    nws_active,
    openmeteo_current,
    reddit_popular,
    reliefweb_rss,
    rss_proxy,
    sec_filings,
    space_weather,
    tsunami_alerts,
    usgs_quakes,
    usgs_volcanoes_proper,
)
from foglight_core.providers.legacy import (
    configure as configure_provider_fetching,
)
from foglight_core.scheduler import (
    FetchResult as SchedulerFetchResult,
)
from foglight_core.scheduler import (
    ProviderScheduler,
    jobs_from_registry,
)
from foglight_core.service import FoglightService, QueryError
from foglight_core.settings import (
    DEFAULT_SETTINGS,
    SettingsStore,
)
from foglight_core.settings import (
    clean_text as core_clean_text,
)
from foglight_core.settings import (
    looks_like_http_url as core_looks_like_http_url,
)
from foglight_core.settings import (
    sanitize_settings_patch as core_sanitize_settings_patch,
)
from foglight_core.storage import ObservationStore
from foglight_core.xmlfeeds import (
    first_find as core_first_find,
)
from foglight_core.xmlfeeds import (
    iter_local as core_iter_local,
)
from foglight_core.xmlfeeds import (
    parse_rss_items as core_parse_rss_items,
)

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

# The web UI is intentionally local-only. A per-process token protects the two
# state-changing endpoints from browser CSRF, while the host allowlist blocks
# DNS-rebinding origins from treating Foglight as their own server.
def _session_token(value):
    return value if isinstance(value, str) and 24 <= len(value) <= 256 else secrets.token_urlsafe(32)


SESSION_TOKEN = _session_token(os.environ.get("FOGLIGHT_SESSION_TOKEN"))
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_HOSTS.update(
    host.strip().lower().rstrip(".")
    for host in os.environ.get("FOGLIGHT_ALLOWED_HOSTS", "").split(",")
    if host.strip()
)


def _bounded_env_int(name, default, *, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if minimum <= value <= maximum else default


MAX_UPSTREAM_BYTES = _bounded_env_int(
    "FOGLIGHT_MAX_UPSTREAM_BYTES",
    5 * 1024 * 1024,
    minimum=64 * 1024,
    maximum=10 * 1024 * 1024,
)
MAX_RSS_BYTES = _bounded_env_int(
    "FOGLIGHT_MAX_RSS_BYTES",
    2 * 1024 * 1024,
    minimum=64 * 1024,
    maximum=5 * 1024 * 1024,
)
MAX_JSON_RESPONSE_BYTES = _bounded_env_int(
    "FOGLIGHT_MAX_JSON_RESPONSE_BYTES",
    2 * 1024 * 1024,
    minimum=64 * 1024,
    maximum=10 * 1024 * 1024,
)
MAX_STATIC_BYTES = 16 * 1024 * 1024

for d in (CACHE_DIR, STATE_DIR, LOG_DIR):
    os.makedirs(d, exist_ok=True)


# -------- user-agent --- NWS and SEC require an identifying UA --------
USER_AGENT = "Foglight/1.0 (+https://github.com/aivrar/foglight)"

# -------- settings: BYOK keys + UI prefs --------
_SETTINGS_STORE = SettingsStore(SETTINGS_PATH, DEFAULT_SETTINGS)


def _clean_text(value, max_len):
    return core_clean_text(value, max_len)


def _looks_like_http_url(value, max_len=2048):
    return core_looks_like_http_url(value, max_len)


def _sanitize_settings_patch(patch):
    return core_sanitize_settings_patch(patch, DEFAULT_SETTINGS)


def load_settings():
    return _SETTINGS_STORE.load()


def save_settings(patch):
    return _SETTINGS_STORE.save(patch)


def public_provider_catalog():
    """Return the bounded, non-secret provider metadata shown in Settings."""
    path = os.path.join(APP_DIR, "config", "provider_registry.v1.json")
    try:
        registry = load_bounded_json(path, max_bytes=1024 * 1024)
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return {"schema_version": 1, "items": []}
    if not isinstance(registry, dict):
        return {"schema_version": 1, "items": []}
    providers = registry.get("providers", [])
    if not isinstance(providers, list):
        return {"schema_version": 1, "items": []}
    items = []
    for raw in providers[:100]:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        items.append({
            "id": raw["id"][:100],
            "attribution": str(raw.get("attribution") or raw["id"])[:300],
            "terms": str(raw.get("terms") or "")[:1000],
            "decision": str(raw.get("decision") or "")[:100],
            "auth": str(raw.get("auth") or "none")[:100],
            "tier": raw.get("tier") if isinstance(raw.get("tier"), int) else None,
            "overview": raw["id"] in CORE_CANONICAL_ADAPTERS,
        })
    return {"schema_version": 1, "items": sorted(items, key=lambda item: item["id"])}



# -------- on-disk cache --- shared across panels --------

CACHE = DiskCache(CACHE_DIR)



# -------- HTTP fetcher with retries / UA / timeout --------
_SafeRedirectHandler = SafeRedirectHandler
_URL_OPENER = URL_OPENER


def _validate_external_fetch_url(url, allowed_ports=(80, 443)):
    return core_validate_external_fetch_url(url, allowed_ports)


def _redact_url(url):
    return core_redact_url(url)


def _read_limited(response, max_bytes):
    return core_read_limited(response, max_bytes)


def _combine_freshness(values):
    return core_combine_freshness(values)


def fetch(
    url,
    ttl=120,
    ctype_hint=None,
    extra_headers=None,
    timeout=10,
    *,
    cache_key=None,
    log_url=None,
    max_bytes=MAX_UPSTREAM_BYTES,
    validate_url=False,
):
    return core_fetch(
        url,
        cache=CACHE,
        opener=_URL_OPENER,
        user_agent=USER_AGENT,
        max_upstream_bytes=MAX_UPSTREAM_BYTES,
        ttl=ttl,
        ctype_hint=ctype_hint,
        extra_headers=extra_headers,
        timeout=timeout,
        cache_key=cache_key,
        log_url=log_url,
        max_bytes=max_bytes,
        validate_url=validate_url,
    )



# -------- data source helpers ----------------------------------------


def _provider_fetch(*args, **kwargs):
    return fetch(*args, **kwargs)


configure_provider_fetching(_provider_fetch, MAX_RSS_BYTES)


def _first_find(parent, *paths):
    return core_first_find(parent, *paths)


def _iter_local(parent, *names):
    return core_iter_local(parent, *names)


def _parse_rss_items(xml_bytes):
    return core_parse_rss_items(xml_bytes)



PROVIDER_REGISTRY = ProviderRegistry(
    [
        FunctionProviderAdapter("usgs_earthquakes", usgs_quakes),
        FunctionProviderAdapter("nws_alerts", nws_active),
        FunctionProviderAdapter("mempool_space", mempool_summary),
        FunctionProviderAdapter("github_events", github_events),
        FunctionProviderAdapter("open_notify_iss", iss_now),
        FunctionProviderAdapter("coinpaprika", crypto_prices),
        FunctionProviderAdapter("frankfurter", forex_latest),
        FunctionProviderAdapter("sec_edgar", sec_filings),
        FunctionProviderAdapter("hacker_news", hn_top),
        FunctionProviderAdapter("reddit_popular", reddit_popular),
        FunctionProviderAdapter("rss_proxy", rss_proxy),
        FunctionProviderAdapter("nhc_storms", nhc_storms),
        FunctionProviderAdapter("reliefweb_rss", reliefweb_rss),
        FunctionProviderAdapter("conflict_rss", conflict_aggregate),
        FunctionProviderAdapter("noaa_space_weather", space_weather),
        FunctionProviderAdapter("nasa_eonet", eonet_events),
        FunctionProviderAdapter("gdacs", gdacs_disasters),
        FunctionProviderAdapter("smithsonian_volcano", usgs_volcanoes_proper),
        FunctionProviderAdapter("noaa_tsunami", tsunami_alerts),
        FunctionProviderAdapter("adsb_lol", adsb_flights),
        FunctionProviderAdapter("nasa_firms", nasa_firms),
        FunctionProviderAdapter("defense_rss", defense_wire),
        FunctionProviderAdapter("yahoo_finance", commodities),
        FunctionProviderAdapter("open_meteo", openmeteo_current),
    ]
)



# -------- Wikipedia EventStreams SSE -> local polling -----------------
# Tail the stream server-side, accumulate a rolling buffer, and let the panel
# poll /api/wiki/recent. This keeps provider-specific connection behavior out
# of the browser and avoids starting the stream while the panel is disabled.

def _bounded_text(value, limit):
    return value[:limit] if isinstance(value, str) else ""


def _bounded_number(value, default):
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) <= 10**15:
        return value
    return default


def _iter_bounded_sse_payloads(response, *, max_line=64 * 1024,
                               max_event=512 * 1024):
    """Yield complete SSE data payloads without retaining unbounded lines/events."""
    if max_line < 1 or max_event < 1:
        raise ValueError("SSE caps must be positive")
    parts = []
    event_bytes = 0
    discard_event = False
    discard_line = False
    while True:
        raw = response.readline(max_line + 1)
        if not raw:
            return
        has_newline = raw.endswith(b"\n")
        if discard_line:
            discard_line = not has_newline
            continue
        if len(raw) > max_line:
            parts.clear()
            event_bytes = 0
            discard_event = True
            discard_line = not has_newline
            continue
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if parts and not discard_event:
                yield "\n".join(parts)
            parts.clear()
            event_bytes = 0
            discard_event = False
            continue
        if discard_event:
            continue
        if line.startswith("data: "):
            value = line[6:]
        elif line.startswith("data:"):
            value = line[5:]
        else:
            continue
        event_bytes += len(raw)
        if event_bytes > max_event:
            parts.clear()
            discard_event = True
            continue
        parts.append(value)

class WikiEditStream(threading.Thread):
    URL = "https://stream.wikimedia.org/v2/stream/recentchange"
    daemon = True

    def __init__(self):
        super().__init__(name="wiki-stream")
        self._buf = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_ok = 0.0

    def run(self):
        while not self._stop_event.is_set():
            try:
                req = urllib.request.Request(self.URL, headers={
                    "User-Agent": USER_AGENT, "Accept": "text/event-stream",
                })
                with URL_OPENER.open(req, timeout=20) as r:
                    self._last_ok = time.time()
                    for payload in _iter_bounded_sse_payloads(r):
                        if self._stop_event.is_set():
                            return
                        try:
                            self._append(json.loads(payload))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            pass
            except Exception as error:
                sys.stderr.write(
                    f"[wiki] stream error: {type(error).__name__}"
                    " --- reconnecting in 5s\n"
                )
                self._stop_event.wait(5)

    def _append(self, obj):
        if not isinstance(obj, dict) or obj.get("type") not in ("edit", "new"):
            return
        lengths = obj.get("length") if isinstance(obj.get("length"), dict) else {}
        compact = {
            "wiki":    _bounded_text(obj.get("wiki"), 100),
            "title":   _bounded_text(obj.get("title"), 500),
            "user":    _bounded_text(obj.get("user"), 200),
            "bot":     obj.get("bot") is True,
            "type":    obj.get("type"),
            "ts":      _bounded_number(obj.get("timestamp"), int(time.time())),
            "comment": _bounded_text(obj.get("comment"), 140),
            "length":  {
                "old": _bounded_number(lengths.get("old"), 0),
                "new": _bounded_number(lengths.get("new"), 0),
            },
            "serverurl": _bounded_text(obj.get("server_url"), 500),
        }
        with self._lock:
            self._buf.append(compact)
            if len(self._buf) > 500:
                self._buf = self._buf[-500:]

    def snapshot(self, limit=60):
        try:
            limit = min(max(int(limit), 1), 500)
        except (TypeError, ValueError):
            limit = 60
        with self._lock:
            return list(self._buf[-limit:])


_WIKI = None
_wiki_start_lock = threading.Lock()


def wiki_stream():
    """Start the optional Wikipedia stream only when its panel is requested."""
    global _WIKI
    with _wiki_start_lock:
        if _WIKI is None:
            _WIKI = WikiEditStream()
            _WIKI.start()
        return _WIKI


def wiki_recent_provider(limit=60):
    body = json.dumps({"events": wiki_stream().snapshot(limit)}).encode("utf-8")
    return body, "application/json", 0, "live"


PROVIDER_REGISTRY.register(
    FunctionProviderAdapter("wikimedia_recentchange", wiki_recent_provider)
)


V2_SERVICE = None
V2_SCHEDULER = None


def configure_v2(service=None, scheduler=None):
    """Inject or clear the additive V2 service; primarily used by startup/tests."""
    global V2_SERVICE, V2_SCHEDULER
    V2_SERVICE = service
    V2_SCHEDULER = scheduler


def _scheduler_fetch(url, headers, timeout, max_bytes):
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json, application/json, application/xml, text/xml, */*",
        **headers,
    }
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with UPSTREAM_REQUEST_GATE:
            with URL_OPENER.open(request, timeout=timeout) as response:
                body = core_read_limited(response, max_bytes)
                return SchedulerFetchResult(
                    response.status,
                    body,
                    dict(response.headers.items()),
                )
    except urllib.error.HTTPError as error:
        body = b""
        if error.code not in (304, 429):
            try:
                body = core_read_limited(error, max_bytes)
            except (OSError, ValueError):
                body = b""
        return SchedulerFetchResult(error.code, body, dict(error.headers.items()))


def start_v2_if_enabled():
    if os.environ.get("FOGLIGHT_V2_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return None
    if V2_SERVICE is not None and V2_SCHEDULER is not None:
        return V2_SCHEDULER
    service = FoglightService(
        ObservationStore(os.path.join(STATE_DIR, "foglight-v2.sqlite3")),
        registry_path=os.path.join(APP_DIR, "config", "provider_registry.v1.json"),
        taxonomy_path=os.path.join(APP_DIR, "config", "data_taxonomy.v1.json"),
    )
    try:
        coastal_stations = load_coops_stations(
            os.path.join(APP_DIR, "config", "coops_water_level_stations.v1.json")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        # The contextual providers are optional. A damaged catalog must not
        # prevent the rest of the zero-config application from starting.
        coastal_stations = ()
    coastal_planner = CoastalContextPlanner(
        service.store, load_settings, coastal_stations
    )
    scheduler = ProviderScheduler(
        jobs_from_registry(os.path.join(APP_DIR, "config", "provider_registry.v1.json")),
        store=service.store,
        fetcher=_scheduler_fetch,
        sink=service.ingest,
        source_lost=service.mark_source_lost,
        context_urls=coastal_planner.urls_for,
    )
    configure_v2(service, scheduler)
    scheduler.start()
    return scheduler


def _feature_enabled(name):
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def spawn_stop_async(delay=0.0, terminate_distro=True):
    """Exit the direct server after the response has had time to flush.

    The packaged launcher replaces this function with its own equivalent.
    ``terminate_distro`` remains for compatibility with older callers.
    """
    def worker():
        if delay:
            time.sleep(delay)
        os._exit(0)
    threading.Thread(target=worker, daemon=True).start()


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
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; script-src-attr 'none'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://tile.openstreetmap.org; "
            "connect-src 'self'; "
            "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
            "media-src 'self'; font-src 'self' data:; object-src 'none'; "
            "worker-src 'none'; manifest-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'",
        )
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
        if self.close_connection:
            self.send_header("Connection", "close")
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
        if len(body) > MAX_JSON_RESPONSE_BYTES:
            code = 507
            body = b'{"error":"response exceeds local API size cap"}'
        self._send(code, body, "application/json; charset=utf-8")

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError as e:
            raise ValueError("invalid Content-Length") from e
        if n <= 0:
            return {}
        if n > 256 * 1024:
            raise ValueError("request body too large")
        try:
            raw = self.rfile.read(n)
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError("invalid JSON body") from e
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        return body

    def _host_allowed(self):
        host, _port = _netloc_parts(self.headers.get("Host", ""))
        return host in ALLOWED_HOSTS

    def _session_token_valid(self):
        supplied = self.headers.get("X-Foglight-Token", "")
        return bool(supplied) and secrets.compare_digest(supplied, SESSION_TOKEN)

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

    def _state_change_allowed(self):
        return (self._host_allowed() and self._same_origin_or_no_origin()
                and self._session_token_valid())

    def log_message(self, fmt, *args):
        del fmt
        status = str(args[1]) if len(args) > 1 else "-"
        path = urllib.parse.urlsplit(self.path).path[:500]
        sys.stderr.write(
            f"[{time.strftime('%H:%M:%S')}] {self.command} {path} {status}\n"
        )

    # -- static serving --

    # Only these extensions can be served as static assets. Root-level .json
    # such as app.json stays inaccessible even though APP_DIR is also a web root.
    _STATIC_EXTS = {".html", ".js", ".css", ".json", ".geojson", ".svg", ".png", ".ico",
                    ".wav", ".mp3", ".ogg", ".woff", ".woff2", ".webp", ".gif"}
    _CTYPE = {
        ".html": "text/html; charset=utf-8",
        ".js":   "application/javascript; charset=utf-8",
        ".css":  "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".geojson": "application/geo+json; charset=utf-8",
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
                    body = f.read(MAX_STATIC_BYTES + 1)
                if len(body) > MAX_STATIC_BYTES:
                    return self._send(413, b"static asset too large")
                return self._send(200, body, "text/html; charset=utf-8")
            except Exception as e:
                del e
                return self._send(500, b"index read failed")

        rel = rel.lstrip("/")
        ext = os.path.splitext(rel)[1].lower()
        # Hard reject anything not on the allowlist (the source dir also
        # contains .py / .sh files we MUST NOT expose).
        if ext not in self._STATIC_EXTS:
            return self._send(404, b"not found")

        # Try each web root in order until we find the file.
        for root in WEB_DIRS:
            if root == APP_DIR and ext in {".json", ".geojson"}:
                continue
            full = os.path.realpath(os.path.join(root, rel))
            root_real = os.path.realpath(root)
            if not (full == root_real or full.startswith(root_real + os.sep)):
                continue
            if os.path.isfile(full):
                try:
                    with open(full, "rb") as f:
                        body = f.read(MAX_STATIC_BYTES + 1)
                    if len(body) > MAX_STATIC_BYTES:
                        return self._send(413, b"static asset too large")
                    return self._send(
                        200, body, self._CTYPE.get(ext, "application/octet-stream")
                    )
                except Exception as e:
                    del e
                    return self._send(500, b"static read failed")
        return self._send(404, b"not found")

    # -- proxy helper --

    def _proxy(self, fn, *args, **kwargs):
        try:
            body, ctype, age, fresh = fn(*args, **kwargs)
            code = 502 if fresh == "error" else 200
            return self._send(code, body, ctype, freshness=fresh, age=age)
        except Exception as e:
            sys.stderr.write(
                f"[proxy] {getattr(fn, '__name__', 'call')} failed: {type(e).__name__}\n"
            )
            return self._send_json({"error": "proxy processing failed"}, 502)

    def _proxy_provider(self, provider_id, **params):
        if (
            V2_SERVICE is not None
            and V2_SCHEDULER is not None
            and provider_id in V2_SCHEDULER.managed_provider_ids
        ):
            payload = V2_SERVICE.legacy_payload(provider_id)
            health = V2_SERVICE.source_health(provider_id) or {}
            freshness = health.get("status", "cached")
            if freshness == "pending":
                freshness = "cached"
            return self._send(
                200,
                json.dumps(payload).encode("utf-8"),
                "application/json; charset=utf-8",
                freshness=freshness,
            )
        return self._proxy(PROVIDER_REGISTRY.get(provider_id).fetch, **params)

    def _handle_v2(self, path, qs):
        if not path.startswith("/api/v2/"):
            return False
        if V2_SERVICE is None:
            self._send_json({"error": "V2 service is disabled"}, 503)
            return True
        first = lambda name, default=None: qs.get(name, [default])[0]
        def validate_params(*allowed):
            unknown = set(qs) - set(allowed)
            if unknown:
                raise QueryError(f"unknown query parameter: {sorted(unknown)[0]}")
        try:
            if path == "/api/v2/bootstrap":
                validate_params()
                payload = V2_SERVICE.bootstrap()
            elif path == "/api/v2/incidents":
                validate_params("limit", "cursor", "lane", "kind", "bbox")
                payload = V2_SERVICE.incidents(
                    limit=first("limit"), cursor=first("cursor"), lane=first("lane"),
                    kind=first("kind"), bbox=first("bbox"),
                )
            elif path == "/api/v2/changes":
                validate_params("cursor", "limit")
                payload = V2_SERVICE.changes(
                    cursor=first("cursor"), limit=first("limit")
                )
            elif path == "/api/v2/search":
                validate_params("q", "limit")
                payload = V2_SERVICE.search(query=first("q"), limit=first("limit"))
            elif path == "/api/v2/taxonomy":
                validate_params()
                payload = V2_SERVICE.taxonomy
            elif path == "/api/v2/source-health":
                validate_params()
                payload = V2_SERVICE.source_health()
            elif path.startswith("/api/v2/source-health/"):
                validate_params()
                provider_id = urllib.parse.unquote(path.removeprefix("/api/v2/source-health/"))
                payload = V2_SERVICE.source_health(provider_id)
                if payload is None:
                    self._send_json({"error": "source not found"}, 404)
                    return True
            elif path.startswith("/api/v2/incidents/"):
                suffix = path.removeprefix("/api/v2/incidents/")
                if suffix.endswith("/timeline"):
                    validate_params("limit")
                    incident_id = urllib.parse.unquote(suffix.removesuffix("/timeline"))
                    payload = V2_SERVICE.timeline(incident_id, limit=first("limit"))
                else:
                    validate_params()
                    incident_id = urllib.parse.unquote(suffix)
                    payload = V2_SERVICE.incident_detail(incident_id)
                if payload is None:
                    self._send_json({"error": "incident not found"}, 404)
                    return True
            else:
                self._send_json({"error": "V2 endpoint not found"}, 404)
                return True
        except QueryError as error:
            self._send_json({"error": str(error)}, 400)
            return True
        self._send_json(payload)
        return True

    # -- routing --

    def do_OPTIONS(self):
        if not self._host_allowed():
            return self._send(HTTPStatus.MISDIRECTED_REQUEST, b"invalid host")
        if not self._same_origin_or_no_origin():
            return self._send(403, b"forbidden")
        return self._send(204, b"", extra_headers={
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Foglight-Token",
        })

    def do_GET(self):
        if not self._host_allowed():
            return self._send(HTTPStatus.MISDIRECTED_REQUEST, b"invalid host")
        url = urllib.parse.urlparse(self.path)
        path = url.path
        qs = urllib.parse.parse_qs(url.query)

        if path == "/api/ping":
            return self._send_json({"ok": True, "ts": time.time()})

        if path == "/api/session":
            return self._send_json({"token": SESSION_TOKEN})

        if path == "/api/settings":
            s = load_settings()
            # Mask keys --- never send full values back to the browser, just
            # the "is it set" bit. Audio + rss come back verbatim.
            masked = json.loads(json.dumps(s))
            for k, v in masked.get("keys", {}).items():
                masked["keys"][k] = bool(v)
            return self._send_json(masked)

        if path == "/api/providers":
            return self._send_json(public_provider_catalog())

        if path == "/api/app-config":
            requested = _feature_enabled("FOGLIGHT_OVERVIEW_ENABLED")
            return self._send_json({
                "overview_enabled": requested and V2_SERVICE is not None,
                "overview_requested": requested,
                "v2_available": V2_SERVICE is not None,
                "default_mode": "overview",
                "open_meteo_enabled": _feature_enabled("FOGLIGHT_OPEN_METEO_ENABLED"),
                "yahoo_finance_enabled": _feature_enabled(
                    "FOGLIGHT_YAHOO_FINANCE_ENABLED"
                ),
            })

        if self._handle_v2(path, qs):
            return

        # ---- data sources ----
        if path == "/api/usgs":
            window = qs.get("window", ["day"])[0]
            return self._proxy_provider(
                "usgs_earthquakes",
                window=window if window in ("hour", "day", "week", "month") else "day",
            )
        if path == "/api/nws":
            return self._proxy_provider("nws_alerts")
        if path == "/api/mempool":
            return self._proxy_provider("mempool_space")
        if path == "/api/github":
            return self._proxy_provider("github_events")
        if path == "/api/iss":
            return self._proxy_provider("open_notify_iss")
        if path == "/api/crypto":
            return self._proxy_provider("coinpaprika")
        if path == "/api/forex":
            return self._proxy_provider("frankfurter")
        if path == "/api/sec":
            return self._proxy_provider("sec_edgar")
        if path == "/api/hn/top":
            return self._proxy_provider("hacker_news")
        if path.startswith("/api/hn/item/"):
            return self._proxy(hn_item, path.rsplit("/", 1)[-1])
        if path == "/api/reddit":
            return self._proxy_provider("reddit_popular")
        if path == "/api/rss":
            url_arg = qs.get("url", [""])[0]
            if not url_arg or not url_arg.startswith(("http://", "https://")):
                return self._send_json({"error": "bad url"}, 400)
            return self._proxy_provider("rss_proxy", url=url_arg)
        if path == "/api/cyclones":
            return self._proxy_provider("nhc_storms")
        if path == "/api/relief":
            return self._proxy_provider("reliefweb_rss")
        if path == "/api/conflict":
            return self._proxy_provider("conflict_rss")
        if path == "/api/conflict-hotspots":
            return self._proxy(conflict_hotspots)
        if path == "/api/space-weather":
            return self._proxy_provider("noaa_space_weather")
        if path == "/api/eonet":
            return self._proxy_provider("nasa_eonet")
        if path == "/api/gdacs":
            return self._proxy_provider("gdacs")
        if path == "/api/openmeteo":
            if not _feature_enabled("FOGLIGHT_OPEN_METEO_ENABLED"):
                return self._send_json({
                    "error": "Open-Meteo compatibility source is disabled"
                }, 503)
            lat = qs.get("lat", ["0"])[0]
            lon = qs.get("lon", ["0"])[0]
            return self._proxy_provider("open_meteo", lat=lat, lon=lon)
        if path == "/api/tsunami":
            return self._proxy_provider("noaa_tsunami")
        if path == "/api/volcanoes-real":
            return self._proxy_provider("smithsonian_volcano")
        if path == "/api/flights":
            lat = qs.get("lat", ["35"])[0]
            lon = qs.get("lon", ["20"])[0]
            dist = qs.get("dist", ["250"])[0]
            return self._proxy_provider("adsb_lol", lat=lat, lon=lon, dist_nm=dist)
        if path == "/api/firms":
            # Use the user's saved MAP_KEY from settings (server-side --- we
            # never echo it to the browser).
            settings = load_settings()
            key = (settings.get("keys") or {}).get("nasa_firms", "")
            return self._proxy_provider("nasa_firms", key=key)
        if path == "/api/defense-wire":
            return self._proxy_provider("defense_rss")
        if path == "/api/commodities":
            if not _feature_enabled("FOGLIGHT_YAHOO_FINANCE_ENABLED"):
                return self._send_json({
                    "error": "Yahoo Finance compatibility source is disabled"
                }, 503)
            return self._proxy_provider("yahoo_finance")
        if path == "/api/wiki/recent":
            limit = 60
            try:
                limit = max(1, min(200, int(qs.get("limit", ["60"])[0])))
            except Exception:
                pass
            return self._proxy_provider("wikimedia_recentchange", limit=limit)

        # ---- static ----
        return self._serve_static(path)

    def do_HEAD(self):
        return self.do_GET()

    def do_POST(self):
        if not self._host_allowed():
            self.close_connection = True
            return self._send(HTTPStatus.MISDIRECTED_REQUEST, b"invalid host")
        url = urllib.parse.urlparse(self.path)
        path = url.path

        if path in ("/api/shutdown", "/api/settings") and not self._state_change_allowed():
            # Do not leave an unread request body on a persistent connection;
            # otherwise its bytes could be parsed as the next HTTP method.
            self.close_connection = True
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
            # giving the response time to flush.
            threading.Thread(
                target=lambda: (time.sleep(1.0), os._exit(0)),
                daemon=True,
            ).start()
            return

        if path == "/api/settings":
            try:
                patch = self._read_body() or {}
            except ValueError as e:
                code = 413 if str(e) == "request body too large" else 400
                self.close_connection = True
                return self._send_json({"error": str(e)}, code)
            # If the patch has a `keys` dict, any value that is None or an
            # empty string clears the key; any truthy string replaces it.
            merged = save_settings(patch)
            masked = json.loads(json.dumps(merged))
            for k, v in masked.get("keys", {}).items():
                masked["keys"][k] = bool(v)
            return self._send_json(masked)

        self.close_connection = True
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
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    bind_host = "127.0.0.1"

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)
    try:
        signal.signal(signal.SIGHUP, _on_signal)  # POSIX-only
    except (AttributeError, ValueError):
        pass

    scheduler = start_v2_if_enabled()
    httpd = http.server.ThreadingHTTPServer((bind_host, port), Handler)
    sys.stderr.write(f"[foglight] listening on {bind_host}:{port}\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _on_signal(2, None)
    finally:
        if scheduler:
            scheduler.stop()


if __name__ == "__main__":
    main()
