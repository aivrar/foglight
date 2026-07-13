import concurrent.futures
import gzip
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import pytest

import foglight_core.fetching as fetching
from foglight_core.cache import DiskCache
from foglight_core.fetching import combine_freshness, read_limited, redact_url
from foglight_core.jsonfiles import load_bounded_json
from foglight_core.providers import FunctionProviderAdapter, ProviderRegistry
from foglight_core.providers import runtime as provider_runtime
from foglight_core.settings import DEFAULT_SETTINGS, SettingsStore, sanitize_settings_patch
from foglight_core.xmlfeeds import parse_rss_items
from scripts import scan_secrets


def test_secret_scanner_finds_credentials_without_echoing_values(tmp_path):
    clean = tmp_path / "clean.py"
    clean.write_text('token = os.environ.get("TOKEN")\n', encoding="utf-8")
    secret = tmp_path / "secret.py"
    credential = "real-" + "production-credential"
    secret_name = "api_" + "key"
    secret.write_text(f'{secret_name} = "{credential}"\n', encoding="utf-8")

    findings = scan_secrets.scan_paths([clean, secret], root=tmp_path)
    assert findings == [{
        "path": "secret.py",
        "line": 1,
        "rule": "literal-secret-assignment",
    }]
    assert "real-production-credential" not in json.dumps(findings)


def test_bounded_json_loader_rejects_oversized_and_invalid_caps(tmp_path):
    source = tmp_path / "config.json"
    source.write_text('{"ok": true}', encoding="utf-8")
    assert load_bounded_json(source, max_bytes=20) == {"ok": True}
    with pytest.raises(ValueError, match="exceeds"):
        load_bounded_json(source, max_bytes=5)
    with pytest.raises(ValueError, match="positive"):
        load_bounded_json(source, max_bytes=0)


def test_settings_store_round_trip_is_sanitized_atomic_and_isolated(tmp_path):
    store = SettingsStore(tmp_path / "nested" / "settings.json")
    first = store.load()
    first["panels"]["tv"] = False
    assert store.load()["panels"]["tv"] is True

    saved = store.save(
        {
            "panels": {"tv": False, "unknown": True},
            "watchlist": ["  storm  ", 3, ""],
            "annotations": [
                {"lat": 100, "lon": 540, "label": "  test pin  "},
                {"lat": "bad", "lon": 1},
            ],
            "rss_feeds": ["https://example.test/feed", "https://example.test/feed"],
            "display_mode": "command",
            "unknown": "discard",
        }
    )
    assert saved["panels"]["tv"] is False
    assert saved["watchlist"] == ["storm"]
    assert saved["annotations"] == [{"lat": 85.0, "lon": -180.0, "label": "test pin"}]
    assert saved["rss_feeds"] == ["https://example.test/feed"]
    assert saved["display_mode"] == "command"
    assert "unknown" not in json.loads((tmp_path / "nested" / "settings.json").read_text())


def test_v1_profile_upgrade_preserves_every_supported_user_preference(tmp_path):
    path = tmp_path / "settings.json"
    legacy = {
        "keys": {"nasa_firms": "legacy-firms-key"},
        "audio": {
            "master": True,
            "earthquake": False,
            "tornado": False,
            "hurricane": True,
            "bitcoin_block": False,
        },
        "panels": {
            "tv": False,
            "conflict": False,
            "cyclones": True,
            "relief": False,
            "iss": True,
            "btc": True,
            "wiki": True,
            "github": True,
            "sec": True,
            "talk": True,
        },
        "tv_channel": "dw",
        "watchlist": ["Typhoon", "Gulf"],
        "annotations": [{"lat": 19.43, "lon": -99.13, "label": "Home"}],
        "rss_feeds": ["https://example.test/legacy.xml"],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    store = SettingsStore(path)

    upgraded = store.save({
        "display_mode": "overview",
        "first_run_done": True,
        "wall_display": {"interval_seconds": 60},
    })

    for field in (
        "keys", "audio", "panels", "tv_channel", "watchlist", "annotations",
        "rss_feeds",
    ):
        assert upgraded[field] == legacy[field]
    assert upgraded["display_mode"] == "overview"
    assert upgraded["first_run_done"] is True
    assert upgraded["wall_display"] == {"interval_seconds": 60}

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["keys"]["nasa_firms"] == "legacy-firms-key"
    assert persisted["panels"]["tv"] is False
    assert persisted["annotations"] == legacy["annotations"]


def test_settings_store_replaces_oversized_local_state_with_bounded_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_bytes(b" " * (1024 * 1024 + 1))
    store = SettingsStore(path)
    assert store.load() == DEFAULT_SETTINGS
    saved = store.save({"watchlist": ["bounded"]})
    assert saved["watchlist"] == ["bounded"]
    assert path.stat().st_size < 1024 * 1024


def test_settings_sanitizer_rejects_wrong_types_and_clamps_known_values():
    assert sanitize_settings_patch(None) == {}
    clean = sanitize_settings_patch(
        {
            "keys": {"nasa_firms": " x ", "other": "no"},
            "audio": {"master": True, "earthquake": "yes"},
            "tv_channel": "bad channel!",
            "first_run_done": True,
        }
    )
    assert clean == {
        "keys": {"nasa_firms": "x"},
        "audio": {"master": True},
        "first_run_done": True,
    }
    assert DEFAULT_SETTINGS["keys"]["nasa_firms"] == ""
    assert DEFAULT_SETTINGS["display_mode"] == "overview"
    assert sanitize_settings_patch({"display_mode": "standard"}) == {
        "display_mode": "standard"
    }
    assert sanitize_settings_patch({"display_mode": "invalid"}) == {}


def test_phase8_settings_sanitize_regions_notifications_and_local_state():
    clean = sanitize_settings_patch(
        {
            "watch_regions": [
                {
                    "id": "watch:dateline",
                    "label": " Dateline watch ",
                    "scope": "region",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[170, -10], [-170, -10], [-170, 10], [170, 10], [170, -10]]],
                    },
                    "radius_km": 9999,
                    "kinds": ["earthquake", "invalid", "earthquake"],
                    "minimum_severity": "Severe",
                    "keywords": [" tsunami ", ""],
                    "enabled": False,
                },
                {
                    "id": "legacy:keywords",
                    "label": "Migrated keywords",
                    "scope": "global",
                    "geometry": None,
                    "keywords": ["storm"],
                },
                {"id": "bad space", "label": "bad", "geometry": {"type": "Point", "coordinates": [0, 0]}},
            ],
            "notifications": {
                "enabled": True,
                "in_app": True,
                "system": False,
                "quiet_start": "23:30",
                "quiet_end": "06:15",
                "minimum_severity": "Moderate",
                "kinds": ["earthquake", "bad"],
                "changes": ["new", "escalated", "bad"],
            },
            "notification_state": {
                "seen_revision_keys": ["incident:x@1", "incident:x@1", 4],
                "acknowledged_keys": ["incident:x@1"],
                "snoozed": [
                    {"incident_id": "incident:x", "until": "2026-07-11T05:00:00+00:00"},
                    {"incident_id": "bad/id", "until": "bad"},
                ],
            },
            "wall_display": {"interval_seconds": 2},
        }
    )
    assert clean["watch_regions"][0] == {
        "id": "watch:dateline",
        "label": "Dateline watch",
        "scope": "region",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[170.0, -10.0], [-170.0, -10.0], [-170.0, 10.0], [170.0, 10.0], [170.0, -10.0]]],
        },
        "radius_km": 2000.0,
        "kinds": ["earthquake"],
        "minimum_severity": "Severe",
        "keywords": ["tsunami"],
        "enabled": False,
    }
    assert clean["watch_regions"][1]["scope"] == "global"
    assert clean["watch_regions"][1]["geometry"] is None
    assert clean["notifications"]["kinds"] == ["earthquake"]
    assert clean["notifications"]["changes"] == ["new", "escalated"]
    assert clean["notification_state"] == {
        "seen_revision_keys": ["incident:x@1"],
        "acknowledged_keys": ["incident:x@1"],
        "snoozed": [{"incident_id": "incident:x", "until": "2026-07-11T05:00:00Z"}],
    }
    assert clean["wall_display"] == {"interval_seconds": 10}


def test_notification_kind_allow_list_tracks_the_canonical_event_taxonomy():
    from foglight_core.models import EventKind
    from foglight_core.settings import DEFAULT_SETTINGS, WATCH_KINDS

    assert WATCH_KINDS == {kind.value for kind in EventKind}
    assert "aviation_hazard" in DEFAULT_SETTINGS["notifications"]["kinds"]


def test_phase8_settings_reject_excessive_or_invalid_watch_geometry():
    invalid = [
        {"id": "open", "label": "Open", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}},
        {"id": "range", "label": "Range", "geometry": {"type": "Point", "coordinates": [181, 0]}},
        {"id": "nan", "label": "NaN", "geometry": {"type": "Point", "coordinates": [float("nan"), 0]}},
    ]
    assert sanitize_settings_patch({"watch_regions": invalid}) == {"watch_regions": []}
    keys = [f"incident:{index}:" + ("x" * 210) for index in range(500)]
    bounded = sanitize_settings_patch({
        "notification_state": {
            "seen_revision_keys": keys,
            "acknowledged_keys": keys,
            "snoozed": [],
        }
    })["notification_state"]
    assert len(bounded["seen_revision_keys"]) == 400
    assert len(bounded["acknowledged_keys"]) == 400
    assert len(json.dumps({"notification_state": bounded}).encode()) < 256 * 1024

    large_ring = [[-170 + (index % 340), -40 + (index % 80)] for index in range(499)]
    large_ring.append(large_ring[0])
    large_regions = [{
        "id": f"watch:large:{index}",
        "label": f"Large {index}",
        "geometry": {"type": "Polygon", "coordinates": [large_ring]},
    } for index in range(50)]
    bounded_regions = sanitize_settings_patch({"watch_regions": large_regions})["watch_regions"]
    assert len(bounded_regions) < 50
    assert len(json.dumps(
        {"watch_regions": bounded_regions}, separators=(",", ":")
    ).encode()) < 185 * 1024


def test_disk_cache_hit_stale_expired_and_prune(tmp_path, monkeypatch):
    cache = DiskCache(tmp_path, max_bytes=512, max_entries=1)
    cache.put("first", b"1234", "text/plain")
    data, status, timestamp = cache.get("first", 60)
    assert (data, status) == (b"1234", "hit")
    assert timestamp > 0

    path = cache._path("first")
    old = time.time() - 120
    monkeypatch.setattr("foglight_core.cache.time.time", lambda: old + 120)
    # Filesystem mtime is forced so both stale and max-stale branches are deterministic.
    os.utime(path, (old, old))
    assert cache.get("first", 60, max_stale=180)[1] == "stale"
    assert cache.get("first", 60, max_stale=90)[1] == "miss"


class _Response:
    def __init__(self, body, content_length=None):
        self.body = body
        self.headers = {} if content_length is None else {"Content-Length": content_length}

    def read(self, count):
        return self.body[:count]


def test_fetch_helpers_cover_redaction_limits_and_freshness():
    assert "secret" not in redact_url("https://example.test/x?api_key=secret")
    assert "%3Credacted%3E" in redact_url("https://example.test/x?token=value")
    assert read_limited(_Response(b"123"), 3) == b"123"
    with pytest.raises(ValueError, match="too large"):
        read_limited(_Response(b"1234"), 3)
    with pytest.raises(ValueError, match="too large"):
        read_limited(_Response(b"1", "10"), 3)
    assert combine_freshness([]) == "error"
    assert combine_freshness(["error"]) == "error"
    assert combine_freshness(["live", "error"]) == "stale"
    assert combine_freshness(["cached", "live"]) == "cached"
    assert combine_freshness(["live"]) == "live"


def test_xml_parser_handles_atom_dates_bad_dates_and_html_summaries():
    payload = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Fixture</title><link href="https://example.test"/>
      <updated>2026-07-10T20:00:00Z</updated>
      <summary>&lt;b&gt;Summary&lt;/b&gt;</summary></entry>
      <entry><title>Bad date</title><updated>not-a-date</updated></entry>
    </feed>"""
    items = parse_rss_items(payload)
    assert items[0]["ts"] > 0
    assert items[0]["summary"] == "Summary"
    assert items[1]["ts"] == 0
    assert parse_rss_items(b"<broken>") == []


def test_provider_registry_rejects_duplicates_and_dispatches_parameters():
    calls = []

    def provider(*, value):
        calls.append(value)
        return b"ok", "text/plain", 0, "live"

    adapter = FunctionProviderAdapter("fixture", provider)
    registry = ProviderRegistry([adapter])
    assert registry.ids() == ("fixture",)
    assert registry.fetch("fixture", value=3) == (b"ok", "text/plain", 0, "live")
    assert calls == [3]
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(adapter)
    with pytest.raises(ValueError, match="empty"):
        registry.register(FunctionProviderAdapter("", provider))
    with pytest.raises(KeyError, match="unknown provider"):
        registry.get("missing")


def test_provider_runtime_requires_configuration(monkeypatch):
    monkeypatch.setattr(provider_runtime, "_fetcher", None)
    with pytest.raises(RuntimeError, match="not configured"):
        provider_runtime.fetch("https://example.test")
    provider_runtime.configure(lambda *_args, **_kwargs: (b"ok", "text/plain", 0, "live"))
    assert provider_runtime.fetch("fixture") == (b"ok", "text/plain", 0, "live")


@pytest.mark.parametrize(
    ("url", "expected_reason"),
    [
        ("not-a-url", "bad url"),
        ("https://user:pass@example.test/", "credentials"),
        ("https://example.test:bad/", "bad port"),
        ("https://example.test:444/", "standard HTTP ports"),
        ("http://localhost/", "local hosts"),
        ("http://127.0.0.1/", "local/private"),
    ],
)
def test_external_url_validation_rejects_unsafe_shapes(url, expected_reason):
    allowed, reason = fetching.validate_external_fetch_url(url)
    assert allowed is False
    assert expected_reason in reason


def test_external_url_validation_covers_dns_results(monkeypatch):
    public = [(None, None, None, None, ("8.8.8.8", 443))]
    monkeypatch.setattr(fetching.socket, "getaddrinfo", lambda *_args, **_kwargs: public)
    assert fetching.validate_external_fetch_url("https://example.test/") == (True, "")

    monkeypatch.setattr(fetching.socket, "getaddrinfo", lambda *_args, **_kwargs: [])
    assert "no usable" in fetching.validate_external_fetch_url("https://example.test/")[1]

    def fail_dns(*_args, **_kwargs):
        raise OSError("fixture DNS failure")

    monkeypatch.setattr(fetching.socket, "getaddrinfo", fail_dns)
    assert "dns lookup failed" in fetching.validate_external_fetch_url("https://example.test/")[1]


def test_redaction_hides_entire_user_configured_path_and_query():
    redacted = fetching.redact_url(
        "https://feeds.example.test/private/path-secret?token=query-secret",
        hide_path=True,
    )
    assert redacted == "https://feeds.example.test/<user-configured>"
    assert "path-secret" not in redacted
    assert "query-secret" not in redacted


def test_pinned_connection_uses_the_exact_validated_address(monkeypatch):
    public = [(fetching.socket.AF_INET, fetching.socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    connected = []

    class Socket:
        def settimeout(self, value):
            self.timeout = value

        def connect(self, address):
            connected.append(address)

        def close(self):
            raise AssertionError("successful socket must remain open")

    monkeypatch.setattr(fetching.socket, "getaddrinfo", lambda *_args, **_kwargs: public)
    monkeypatch.setattr(fetching.socket, "socket", lambda *_args: Socket())
    result = fetching._create_public_connection(("example.test", 443), timeout=3)

    assert connected == [("8.8.8.8", 443)]
    assert result.timeout == 3


def test_pinned_connection_rejects_mixed_public_private_dns_before_connect(monkeypatch):
    mixed = [
        (fetching.socket.AF_INET, fetching.socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (fetching.socket.AF_INET, fetching.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    monkeypatch.setattr(fetching.socket, "getaddrinfo", lambda *_args, **_kwargs: mixed)
    monkeypatch.setattr(
        fetching.socket, "socket", lambda *_args: (_ for _ in ()).throw(AssertionError("connected"))
    )

    with pytest.raises(OSError, match="local/private"):
        fetching._create_public_connection(("example.test", 443), timeout=3)


def test_pinned_connection_closes_failed_candidate_and_tries_next(monkeypatch):
    addresses = [
        (fetching.socket.AF_INET, fetching.socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (fetching.socket.AF_INET, fetching.socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
    ]
    sockets = []

    class Socket:
        def __init__(self):
            self.closed = False
            sockets.append(self)

        def settimeout(self, _value):
            pass

        def connect(self, address):
            if address[0] == "8.8.8.8":
                raise OSError("fixture first-address failure")

        def close(self):
            self.closed = True

    monkeypatch.setattr(fetching.socket, "getaddrinfo", lambda *_args, **_kwargs: addresses)
    monkeypatch.setattr(fetching.socket, "socket", lambda *_args: Socket())
    connected = fetching._create_public_connection(("example.test", 443), timeout=3)
    assert sockets[0].closed is True
    assert connected is sockets[1]
    assert connected.closed is False


def test_pinned_https_preserves_original_hostname_for_tls(monkeypatch):
    class RawSocket:
        def setsockopt(self, *_args):
            pass

    raw_socket = RawSocket()
    wrapped_socket = object()

    class Context:
        def wrap_socket(self, sock, *, server_hostname):
            assert sock is raw_socket
            assert server_hostname == "example.test"
            return wrapped_socket

    connection = fetching.PinnedHTTPSConnection(
        "example.test", context=Context(), timeout=3
    )
    connection._create_connection = lambda *_args, **_kwargs: raw_socket
    connection.connect()
    assert connection.sock is wrapped_socket


def test_redirect_handler_blocks_private_and_allows_public(monkeypatch):
    request = urllib.request.Request("https://example.test/start")
    handler = fetching.SafeRedirectHandler()
    with pytest.raises(urllib.error.HTTPError) as blocked:
        handler.redirect_request(
            request, None, 302, "Found", {}, "http://127.0.0.1/private"
        )
    assert blocked.value.code == 403

    monkeypatch.setattr(
        fetching, "validate_external_fetch_url", lambda _url: (True, "")
    )
    redirected = handler.redirect_request(
        request, None, 302, "Found", {}, "https://example.test/next"
    )
    assert redirected.full_url == "https://example.test/next"


class _FakeCache:
    def __init__(self, result):
        self.result = result
        self.puts = []

    def get(self, key, ttl):
        self.get_call = (key, ttl)
        return self.result

    def put(self, key, data, ctype):
        self.puts.append((key, data, ctype))


class _ContextResponse(_Response):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def open(self, request, timeout):
        self.call = (request, timeout)
        if self.error:
            raise self.error
        return self.response


def _core_fetch(cache, opener, **kwargs):
    return fetching.fetch(
        "https://example.test/data?token=secret",
        cache=cache,
        opener=opener,
        user_agent="Foglight/Test",
        max_upstream_bytes=1024,
        **kwargs,
    )


def test_core_fetch_covers_live_cached_stale_error_and_validation(capsys):
    live_cache = _FakeCache((None, "miss", 0))
    response = _ContextResponse(b'{"ok":true}')
    response.headers = {"Content-Type": "application/json"}
    live_opener = _FakeOpener(response=response)
    live = _core_fetch(
        live_cache,
        live_opener,
        ttl=9,
        cache_key="safe-key",
        extra_headers={"X-Fixture": "yes"},
        max_bytes=100,
    )
    assert live == (b'{"ok":true}', "application/json", 0, "live")
    assert live_cache.get_call == ("safe-key", 9)
    assert live_cache.puts == [("safe-key", b'{"ok":true}', "application/json")]
    assert live_opener.call[0].get_header("X-fixture") == "yes"

    cached_cache = _FakeCache((b"cached", "hit", time.time() - 2))
    assert _core_fetch(cached_cache, _FakeOpener(), ctype_hint="text/plain")[3] == "cached"

    stale_cache = _FakeCache((b"stale", "stale", time.time() - 3))
    stale = _core_fetch(
        stale_cache,
        _FakeOpener(error=OSError("https://example.test/data?token=secret failed")),
        ctype_hint="application/json",
        log_url="https://example.test/<redacted>",
    )
    assert stale[0] == b"stale" and stale[3] == "stale"
    assert "secret" not in capsys.readouterr().err

    error_cache = _FakeCache((None, "miss", 0))
    error = _core_fetch(error_cache, _FakeOpener(error=TimeoutError("late")))
    assert json.loads(error[0]) == {
        "error": "upstream request failed",
        "kind": "TimeoutError",
    }
    assert error[3] == "error"

    invalid = _core_fetch(
        error_cache, _FakeOpener(), validate_url=True
    )
    assert invalid[3] == "error"

    nested = _core_fetch(
        error_cache,
        _FakeOpener(error=urllib.error.URLError(TimeoutError("private detail"))),
    )
    assert nested[3] == "error"
    logged = capsys.readouterr().err
    assert "URLError/TimeoutError" in logged
    assert "private detail" not in logged


def test_core_fetch_global_gate_caps_simultaneous_upstream_requests(monkeypatch):
    class TrackingOpener:
        def __init__(self):
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def open(self, _request, timeout):
            assert timeout == 10
            owner = self

            class Response(_ContextResponse):
                def __enter__(self):
                    with owner.lock:
                        owner.active += 1
                        owner.peak = max(owner.peak, owner.active)
                    return self

                def read(self, size=-1):
                    time.sleep(0.02)
                    return super().read(size)

                def __exit__(self, *_args):
                    with owner.lock:
                        owner.active -= 1
                    return False

            response = Response(b'{"ok":true}')
            response.headers = {"Content-Type": "application/json"}
            return response

    opener = TrackingOpener()
    monkeypatch.setattr(fetching, "UPSTREAM_REQUEST_GATE", threading.BoundedSemaphore(2))

    def one_fetch(index):
        return fetching.fetch(
            f"https://example.test/{index}",
            cache=_FakeCache((None, "miss", 0)),
            opener=opener,
            user_agent="Foglight/Test",
            max_upstream_bytes=100,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one_fetch, range(6)))
    assert all(result[3] == "live" for result in results)
    assert opener.peak == 2


def test_validated_fetch_uses_pinned_transport(monkeypatch):
    monkeypatch.setattr(
        fetching.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (fetching.socket.AF_INET, fetching.socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        ],
    )
    response = _ContextResponse(b"safe")
    response.headers = {"Content-Type": "text/plain"}
    pinned = _FakeOpener(response=response)
    default = _FakeOpener(error=AssertionError("unpinned transport used"))

    result = fetching.fetch(
        "https://example.test/feed",
        cache=_FakeCache((None, "miss", 0)),
        opener=default,
        pinned_opener=pinned,
        user_agent="Foglight/Test",
        max_upstream_bytes=100,
        validate_url=True,
    )
    assert result == (b"safe", "text/plain", 0, "live")


def test_validated_fetch_failure_log_hides_user_path_and_exception(monkeypatch, capsys):
    monkeypatch.setattr(
        fetching.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (fetching.socket.AF_INET, fetching.socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        ],
    )
    result = fetching.fetch(
        "https://example.test/private/path-secret?token=query-secret",
        cache=_FakeCache((None, "miss", 0)),
        pinned_opener=_FakeOpener(error=OSError("exception-secret")),
        user_agent="Foglight/Test",
        max_upstream_bytes=100,
        validate_url=True,
    )
    assert result[3] == "error"
    logged = capsys.readouterr().err
    assert "path-secret" not in logged
    assert "query-secret" not in logged
    assert "exception-secret" not in logged
    assert "https://example.test/<user-configured> failed: OSError" in logged


@pytest.mark.parametrize(
    ("encoding", "encoded"),
    [
        ("gzip", gzip.compress(b"bounded payload")),
        ("deflate", zlib.compress(b"bounded payload")),
    ],
)
def test_bounded_reads_decode_supported_compression(encoding, encoded):
    response = _ContextResponse(encoded)
    response.headers = {"Content-Encoding": encoding}
    assert read_limited(response, 100) == b"bounded payload"


def test_bounded_reads_reject_decompression_bombs_and_malformed_encodings():
    bomb = _ContextResponse(gzip.compress(b"x" * 1000))
    bomb.headers = {"Content-Encoding": "gzip"}
    with pytest.raises(ValueError, match="after decompression"):
        read_limited(bomb, 100)

    malformed = _ContextResponse(b"not-gzip")
    malformed.headers = {"Content-Encoding": "gzip"}
    with pytest.raises(ValueError, match="invalid compression"):
        read_limited(malformed, 100)

    unsupported = _ContextResponse(b"payload")
    unsupported.headers = {"Content-Encoding": "br"}
    with pytest.raises(ValueError, match="unsupported"):
        read_limited(unsupported, 100)

    trailing = _ContextResponse(gzip.compress(b"ok") + b"trailing")
    trailing.headers = {"Content-Encoding": "gzip"}
    with pytest.raises(ValueError, match="invalid compression"):
        read_limited(trailing, 100)

    truncated = _ContextResponse(gzip.compress(b"ok")[:-2])
    truncated.headers = {"Content-Encoding": "gzip"}
    with pytest.raises(ValueError, match="invalid compression"):
        read_limited(truncated, 100)


def test_cache_corruption_pruning_and_io_failures_are_nonfatal(tmp_path, monkeypatch):
    cache = DiskCache(tmp_path, max_entries=1, max_bytes=512)
    assert cache.get("missing", 60) == (None, "miss", 0)
    cache.put("first", b"1234")
    os.remove(cache._meta_path("first"))
    assert cache.get("first", 60)[1] == "miss"

    cache.put("first", b"1234")
    old = time.time() - 61
    os.utime(cache._path("first"), (old, old))
    assert cache.get("first", 60)[1] == "stale"
    cache.put("second", b"5678")
    (tmp_path / "ignored.txt").write_text("ignored")
    cache.prune()
    assert len(list(tmp_path.glob("*.bin"))) == 1
    cache.prune(locked=True)

    cache._last_prune = 0
    cache.put("automatic-prune", b"1")

    orphan = DiskCache(tmp_path / "orphan", max_entries=0)
    (tmp_path / "orphan" / "orphan.bin").write_bytes(b"x")
    (tmp_path / "orphan" / "missing.bin.meta").write_text("{}")
    (tmp_path / "orphan" / "crash.bin.tmp").write_bytes(b"x")
    orphan.prune()
    assert not (tmp_path / "orphan" / "orphan.bin").exists()
    assert not (tmp_path / "orphan" / "missing.bin.meta").exists()
    assert not (tmp_path / "orphan" / "crash.bin.tmp").exists()

    original_scandir = os.scandir
    monkeypatch.setattr(os, "scandir", lambda _root: (_ for _ in ()).throw(OSError("scan")))
    cache.prune()
    monkeypatch.setattr(os, "scandir", original_scandir)

    original_remove = os.remove
    monkeypatch.setattr(os, "remove", lambda _path: (_ for _ in ()).throw(OSError("remove")))
    cache.max_entries = 0
    cache.prune()
    monkeypatch.setattr(os, "remove", original_remove)

    def fail_open(*_args, **_kwargs):
        raise OSError("write")

    monkeypatch.setattr("builtins.open", fail_open)
    cache.put("nonfatal", b"x")


def test_cache_rejects_oversized_or_invalid_entries(tmp_path):
    bounded = DiskCache(
        tmp_path / "bounded", max_bytes=20, max_entries=2, max_entry_bytes=5
    )
    assert bounded.put("too-large", b"123456") is False
    assert not os.path.exists(bounded._path("too-large"))
    Path(bounded._path("tampered")).write_bytes(b"123456")
    Path(bounded._meta_path("tampered")).write_text('{"ts": 1}', encoding="utf-8")
    assert bounded.get("tampered", 60)[1] == "miss"
    with pytest.raises(ValueError, match="cache caps"):
        DiskCache(tmp_path / "invalid", max_entry_bytes=0)


def test_cache_caps_metadata_and_recovers_from_invalid_timestamps(tmp_path):
    cache = DiskCache(
        tmp_path / "metadata", max_bytes=120, max_entries=10, max_entry_bytes=50
    )
    cache.put("first", b"a" * 10)
    cache.put("second", b"b" * 10)
    assert len(list((tmp_path / "metadata").glob("*.bin"))) == 1

    key = "survivor"
    cache.put(key, b"ok")
    metadata = Path(cache._meta_path(key))
    metadata.write_text('{"ts": NaN}', encoding="utf-8")
    _data, status, timestamp = cache.get(key, 60)
    assert status == "hit"
    assert timestamp == pytest.approx(os.path.getmtime(cache._path(key)))

    metadata.write_bytes(b"x" * (64 * 1024 + 1))
    cache.prune()
    assert not Path(cache._path(key)).exists()
    assert not metadata.exists()
