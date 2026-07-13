import http.client
import io
import json
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

import foglight_server as server


def test_wikimedia_sse_and_retained_events_are_bounded():
    valid = b'data: {"type":"edit","title":"ok"}\n\n'
    oversized = b"data: " + (b"x" * 80) + b"\n\n"
    payloads = list(
        server._iter_bounded_sse_payloads(
            io.BytesIO(oversized + valid), max_line=64, max_event=128
        )
    )
    assert payloads == ['{"type":"edit","title":"ok"}']

    stream = server.WikiEditStream()
    stream._append({
        "type": "edit",
        "wiki": "w" * 200,
        "title": "t" * 1000,
        "user": "u" * 400,
        "comment": "c" * 400,
        "server_url": "s" * 1000,
        "bot": ["not", "a", "boolean"],
        "timestamp": float("inf"),
        "length": {"old": "huge", "new": 42, "ignored": "x" * 1000},
    })
    item = stream.snapshot("invalid")[0]
    assert len(item["wiki"]) == 100
    assert len(item["title"]) == 500
    assert len(item["user"]) == 200
    assert len(item["comment"]) == 140
    assert len(item["serverurl"]) == 500
    assert item["bot"] is False
    assert item["length"] == {"old": 0, "new": 42}
    assert isinstance(item["ts"], int)


def test_cache_keys_are_secret_free_and_collision_resistant(tmp_path):
    cache = server.DiskCache(tmp_path)
    secret = "AUDIT_SECRET_VALUE"
    path = cache._path(f"https://example.test/feed?api_key={secret}")

    assert secret not in path
    assert path != cache._path("https://example.test/feed/api_key/AUDIT_SECRET_VALUE")
    assert path.endswith(".bin")


def test_environment_response_caps_cannot_be_disabled(monkeypatch):
    default = 2 * 1024 * 1024
    for value in ("invalid", "-1", str(1024 * 1024 * 1024)):
        monkeypatch.setenv("FOGLIGHT_TEST_CAP", value)
        assert server._bounded_env_int(
            "FOGLIGHT_TEST_CAP", default, minimum=64 * 1024, maximum=10 * 1024 * 1024
        ) == default
    monkeypatch.setenv("FOGLIGHT_TEST_CAP", str(128 * 1024))
    assert server._bounded_env_int(
        "FOGLIGHT_TEST_CAP", default, minimum=64 * 1024, maximum=10 * 1024 * 1024
    ) == 128 * 1024

    assert server._session_token("short") != "short"
    assert len(server._session_token("short")) >= 24
    configured = "x" * 24
    assert server._session_token(configured) == configured
    assert server._session_token("x" * 257) != "x" * 257


def test_external_url_policy_rejects_local_and_nonstandard_targets():
    for url in (
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://localhost/",
        "https://example.com:8443/feed.xml",
        "file:///etc/passwd",
    ):
        allowed, _reason = server._validate_external_fetch_url(url)
        assert not allowed, url


def test_redirects_are_revalidated_before_following():
    request = urllib.request.Request("https://example.com/feed")
    with pytest.raises(urllib.error.HTTPError) as error:
        server._SafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1/private",
        )
    assert error.value.code == 403


def test_fetch_does_not_echo_or_log_secret(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(server, "CACHE", server.DiskCache(tmp_path))

    def fail(*_args, **_kwargs):
        raise OSError("forced failure")

    monkeypatch.setattr(server._URL_OPENER, "open", fail)
    secret = "AUDIT_SECRET_VALUE"
    body, _ctype, _age, freshness = server.fetch(
        f"https://example.com/data/{secret}",
        cache_key="example:data",
        log_url="https://example.com/data/<redacted>",
    )

    assert freshness == "error"
    assert secret not in body.decode("utf-8")
    assert secret not in capsys.readouterr().err


def test_request_and_proxy_logs_never_include_query_or_exception_secrets(
    local_server, monkeypatch, capsys
):
    request(local_server, "GET", "/api/ping?token=QUERY_SECRET_VALUE")

    def provider(_provider_id):
        def fail(**_params):
            raise ValueError("EXCEPTION_SECRET_VALUE")

        return SimpleNamespace(fetch=fail)

    monkeypatch.setattr(server.PROVIDER_REGISTRY, "get", provider)
    status, _headers, body = request(local_server, "GET", "/api/usgs")
    assert status == 502
    assert json.loads(body) == {"error": "proxy processing failed"}
    logged = capsys.readouterr().err
    assert "QUERY_SECRET_VALUE" not in logged
    assert "EXCEPTION_SECRET_VALUE" not in logged
    assert "GET /api/ping 200" in logged
    assert "failed: ValueError" in logged


def test_upstream_reads_are_bounded():
    response = type(
        "Response",
        (),
        {"headers": {}, "read": lambda self, _size: b"x" * 11},
    )()
    with pytest.raises(ValueError, match="too large"):
        server._read_limited(response, 10)


def test_aggregate_freshness_reports_failure(monkeypatch):
    def failed(*_args, **_kwargs):
        return b'{"error":"offline"}', "application/json", 0, "error"

    monkeypatch.setattr(server, "fetch", failed)
    for aggregate in (
        server.mempool_summary,
        server.conflict_aggregate,
        server.defense_wire,
        server.tsunami_alerts,
    ):
        _body, _ctype, _age, freshness = aggregate()
        assert freshness == "error", aggregate.__name__


def test_aggregate_freshness_preserves_partial_results():
    assert server._combine_freshness(["live", "error"]) == "stale"
    assert server._combine_freshness(["cached", "live"]) == "cached"


@pytest.fixture
def local_server():
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(port, method, path, *, headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_local_http_boundary_requires_host_and_session_token(local_server):
    status, headers, body = request(local_server, "GET", "/api/session")
    assert status == 200
    assert json.loads(body)["token"] == server.SESSION_TOKEN
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "script-src-attr 'none'" in csp
    assert "worker-src 'none'" in csp
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "geolocation=()" in headers["Permissions-Policy"]
    assert "unpkg.com" not in csp
    assert "cartocdn.com" not in csp

    status, _headers, _body = request(
        local_server,
        "POST",
        "/api/settings",
        headers={"Content-Type": "application/json"},
        body=b"{}",
    )
    assert status == 403

    status, _headers, _body = request(
        local_server,
        "POST",
        "/api/settings",
        headers={
            "Content-Type": "application/json",
            "X-Foglight-Token": server.SESSION_TOKEN,
        },
        body=b'{"audio":{"master":true}}',
    )
    assert status == 200

    status, _headers, _body = request(
        local_server,
        "GET",
        "/api/ping",
        headers={"Host": "attacker.example"},
    )
    assert status == 421


def test_public_provider_catalog_is_bounded_attributed_and_secret_free(local_server):
    status, _headers, body = request(local_server, "GET", "/api/providers")
    assert status == 200
    payload = json.loads(body)
    assert payload["schema_version"] == 1
    assert 1 <= len(payload["items"]) <= 100
    providers = {item["id"]: item for item in payload["items"]}
    assert providers["usgs_earthquakes"]["attribution"] == "USGS"
    assert providers["usgs_earthquakes"]["overview"] is True
    assert providers["nasa_firms"]["overview"] is False
    assert providers["nasa_firms"]["auth"] == "user MAP_KEY"
    assert set(providers["nasa_firms"]) == {
        "id", "attribution", "terms", "decision", "auth", "tier", "overview"
    }
    assert server.SESSION_TOKEN.encode() not in body


def test_provider_catalog_fails_closed_when_local_registry_is_oversized(
    tmp_path, monkeypatch
):
    config = tmp_path / "config"
    config.mkdir()
    (config / "provider_registry.v1.json").write_bytes(b" " * (1024 * 1024 + 1))
    monkeypatch.setattr(server, "APP_DIR", str(tmp_path))
    assert server.public_provider_catalog() == {"schema_version": 1, "items": []}

    (config / "provider_registry.v1.json").write_text("[]", encoding="utf-8")
    assert server.public_provider_catalog() == {"schema_version": 1, "items": []}


def test_bundled_map_assets_are_served_with_local_types(local_server):
    status, headers, body = request(
        local_server,
        "GET",
        "/assets/natural-earth-110m-countries.v5.1.1.geojson",
    )
    assert status == 200
    assert headers["Content-Type"].startswith("application/geo+json")
    assert json.loads(body)["type"] == "FeatureCollection"

    status, headers, body = request(local_server, "GET", "/vendor/leaflet/leaflet.js")
    assert status == 200
    assert headers["Content-Type"].startswith("application/javascript")
    assert b"Leaflet 1.9.4" in body[:500]


def test_root_level_geojson_is_not_exposed(local_server, monkeypatch, tmp_path):
    secret = tmp_path / "private.geojson"
    secret.write_text('{"secret":true}', encoding="utf-8")
    monkeypatch.setattr(server, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(server, "WEB_DIRS", [str(tmp_path)])
    status, _headers, body = request(local_server, "GET", "/private.geojson")
    assert status == 404
    assert b"secret" not in body


def test_static_reads_have_a_last_resort_size_cap(local_server, monkeypatch, tmp_path):
    index = tmp_path / "index.html"
    index.write_bytes(b"x" * 11)
    monkeypatch.setattr(server, "INDEX_PATH", str(index))
    monkeypatch.setattr(server, "MAX_STATIC_BYTES", 10)
    status, _headers, body = request(local_server, "GET", "/")
    assert status == 413
    assert body == b"static asset too large"


def test_overview_feature_flag_requires_v2_service(local_server, monkeypatch):
    monkeypatch.setenv("FOGLIGHT_OVERVIEW_ENABLED", "1")
    monkeypatch.setattr(server, "V2_SERVICE", None)
    status, _headers, body = request(local_server, "GET", "/api/app-config")
    assert status == 200
    assert json.loads(body) == {
        "overview_enabled": False,
        "overview_requested": True,
        "v2_available": False,
        "default_mode": "overview",
        "open_meteo_enabled": False,
        "yahoo_finance_enabled": False,
    }

    monkeypatch.setattr(server, "V2_SERVICE", object())
    status, _headers, body = request(local_server, "GET", "/api/app-config")
    assert status == 200
    assert json.loads(body)["overview_enabled"] is True


def test_open_meteo_compatibility_route_is_disabled_by_default(
    local_server, monkeypatch
):
    monkeypatch.delenv("FOGLIGHT_OPEN_METEO_ENABLED", raising=False)
    monkeypatch.setattr(
        server.PROVIDER_REGISTRY,
        "get",
        lambda *_args: (_ for _ in ()).throw(AssertionError("upstream called")),
    )
    status, _headers, body = request(
        local_server, "GET", "/api/openmeteo?lat=35&lon=139"
    )
    assert status == 503
    assert json.loads(body)["error"] == "Open-Meteo compatibility source is disabled"

    monkeypatch.setenv("FOGLIGHT_OPEN_METEO_ENABLED", "true")
    status, _headers, body = request(local_server, "GET", "/api/app-config")
    assert status == 200
    assert json.loads(body)["open_meteo_enabled"] is True

    monkeypatch.setattr(
        server.PROVIDER_REGISTRY,
        "get",
        lambda _provider_id: SimpleNamespace(fetch=lambda **_params: (
            b'{"current":{}}', "application/json", 0, "live"
        )),
    )
    status, headers, body = request(
        local_server, "GET", "/api/openmeteo?lat=35&lon=139"
    )
    assert status == 200
    assert headers["X-Foglight-Freshness"] == "live"
    assert json.loads(body) == {"current": {}}


def test_yahoo_finance_compatibility_route_is_disabled_by_default(
    local_server, monkeypatch
):
    monkeypatch.delenv("FOGLIGHT_YAHOO_FINANCE_ENABLED", raising=False)
    monkeypatch.setattr(
        server.PROVIDER_REGISTRY,
        "get",
        lambda *_args: (_ for _ in ()).throw(AssertionError("upstream called")),
    )
    status, _headers, body = request(local_server, "GET", "/api/commodities")
    assert status == 503
    assert json.loads(body)["error"] == (
        "Yahoo Finance compatibility source is disabled"
    )

    monkeypatch.setenv("FOGLIGHT_YAHOO_FINANCE_ENABLED", "true")
    status, _headers, body = request(local_server, "GET", "/api/app-config")
    assert status == 200
    assert json.loads(body)["yahoo_finance_enabled"] is True

    monkeypatch.setattr(
        server.PROVIDER_REGISTRY,
        "get",
        lambda _provider_id: SimpleNamespace(fetch=lambda **_params: (
            b'{"items":{}}', "application/json", 0, "live"
        )),
    )
    status, headers, body = request(local_server, "GET", "/api/commodities")
    assert status == 200
    assert headers["X-Foglight-Freshness"] == "live"
    assert json.loads(body) == {"items": {}}


def test_invalid_json_is_rejected(local_server):
    status, _headers, _body = request(
        local_server,
        "POST",
        "/api/settings",
        headers={
            "Content-Type": "application/json",
            "X-Foglight-Token": server.SESSION_TOKEN,
        },
        body=b"not-json",
    )
    assert status == 400

    status, _headers, _body = request(
        local_server,
        "POST",
        "/api/settings",
        headers={
            "Content-Type": "application/json",
            "X-Foglight-Token": server.SESSION_TOKEN,
        },
        body=b"[]",
    )
    assert status == 400


def test_static_server_does_not_expose_source_files(local_server):
    for path in ("/foglight_server.py", "/../foglight_server.py", "/app.json"):
        status, _headers, _body = request(local_server, "GET", path)
        assert status == 404


def test_rejected_post_cannot_poison_next_persistent_request(local_server):
    connection = http.client.HTTPConnection("127.0.0.1", local_server, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/settings",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        rejected = connection.getresponse()
        assert rejected.status == 403
        assert rejected.getheader("Connection") == "close"
        rejected.read()

        connection.request(
            "POST",
            "/api/settings",
            body=b'{"audio":{"master":true}}',
            headers={
                "Content-Type": "application/json",
                "X-Foglight-Token": server.SESSION_TOKEN,
            },
        )
        accepted = connection.getresponse()
        assert accepted.status == 200
        accepted.read()
    finally:
        connection.close()
