#!/usr/bin/env python3
"""Exercise clean, offline, upgrade, corruption, and restart package profiles."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def request(
    base: str,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 3.0,
) -> tuple[int, dict[str, str], bytes]:
    url = f"{base}{path}"
    request_headers = {"Host": urllib.parse.urlsplit(base).netloc, **(headers or {})}
    req = urllib.request.Request(
        url, data=data, method=method, headers=request_headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise RuntimeError(f"oversized packaged response from {path}")
            return response.status, dict(response.headers.items()), body
    except urllib.error.HTTPError as error:
        body = error.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RuntimeError(f"oversized packaged error from {path}") from error
        return error.code, dict(error.headers.items()), body


def json_request(base: str, path: str, **kwargs) -> dict:
    status, _headers, body = request(base, path, **kwargs)
    if status != 200:
        raise RuntimeError(f"{path} returned HTTP {status}: {body[:200]!r}")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} did not return a JSON object")
    return value


def wait_ready(base: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if json_request(base, "/api/ping").get("ok") is True:
                return
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(0.1)
    raise RuntimeError(f"packaged server did not become ready: {last_error}")


def verify_loopback_listener(port: int) -> None:
    if os.name != "nt":
        return
    script = (
        f"$all = @(Get-NetTCPConnection -State Listen -LocalPort {port} "
        "-ErrorAction SilentlyContinue); "
        "if (-not $all) { exit 3 }; "
        "$bad = @($all | Where-Object { $_.LocalAddress -ne '127.0.0.1' }); "
        "if ($bad) { exit 2 }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
    )


def verify_listener_stopped(port: int) -> None:
    if os.name != "nt":
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"if (Get-NetTCPConnection -State Listen -LocalPort {port} "
                "-ErrorAction SilentlyContinue) { exit 1 }",
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return
        time.sleep(0.1)
    raise RuntimeError(f"packaged listener remained on port {port}")


def stop_process(process: subprocess.Popen[bytes], base: str, token: str) -> None:
    if process.poll() is None:
        with contextlib.suppress(Exception):
            request(
                base,
                "/api/shutdown",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Foglight-Token": token,
                },
                data=b"{}",
            )
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
        else:
            process.terminate()
        process.wait(timeout=10)


@contextmanager
def running_package(
    command: list[str],
    runtime: Path,
    port: int,
    *,
    offline: bool,
    v2: bool | None,
    overview: bool | None,
):
    token = "release-smoke-token-0123456789"
    env = os.environ.copy()
    env.update(
        {
            "LOCALAPPDATA": str(runtime),
            "APPDATA": str(runtime),
            "FOGLIGHT_NO_BROWSER": "1",
            "FOGLIGHT_PORT": str(port),
            "FOGLIGHT_SESSION_TOKEN": token,
        }
    )
    for name, value in (
        ("FOGLIGHT_V2_ENABLED", v2),
        ("FOGLIGHT_OVERVIEW_ENABLED", overview),
    ):
        if value is None:
            env.pop(name, None)
        else:
            env[name] = "1" if value else "0"
    if offline:
        env.update(
            {
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1,localhost",
            }
        )
    else:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            env.pop(name, None)
        env["NO_PROXY"] = "127.0.0.1,localhost"

    process = subprocess.Popen(
        command,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_ready(base)
        verify_loopback_listener(port)
        yield base, token
    finally:
        stop_process(process, base, token)
        if process.poll() is None:
            raise RuntimeError("packaged process did not stop")
        verify_listener_stopped(port)


def assert_shell(base: str) -> None:
    status, headers, body = request(base, "/")
    assert status == 200
    assert b'id="overview-surface"' in body
    assert "Content-Security-Policy" in headers
    status, _headers, world = request(
        base, "/assets/natural-earth-110m-countries.v5.1.1.geojson"
    )
    assert status == 200 and len(world) == 202_773
    status, _headers, leaflet = request(base, "/vendor/leaflet/leaflet.js")
    assert status == 200 and b"Leaflet 1.9.4" in leaflet[:500]


def assert_rendered_profile(base: str, profile: str) -> None:
    subprocess.run(
        [
            "node", str(ROOT / "scripts" / "assert_packaged_profile.mjs"),
            base, profile,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )


def wait_for_live_overview(base: str, timeout: float = 90.0) -> tuple[int, int]:
    deadline = time.monotonic() + timeout
    statuses: dict[str, str] = {}
    incident_count = 0
    while time.monotonic() < deadline:
        bootstrap = json_request(base, "/api/v2/bootstrap", timeout=5)
        sources = bootstrap.get("source_health", {}).get("sources", [])
        statuses = {
            item.get("provider_id", "unknown"): item.get("status", "missing")
            for item in sources
            if isinstance(item, dict)
        }
        live_count = sum(status == "live" for status in statuses.values())
        incident_count = len(bootstrap.get("incidents", {}).get("items", []))
        if live_count >= 2 and incident_count >= 1:
            return live_count, incident_count
        time.sleep(0.25)
    raise RuntimeError(
        "default packaged launch did not recover live data: "
        f"incidents={incident_count};statuses={json.dumps(statuses, sort_keys=True)}"
    )


def default_zero_configuration(
    command: list[str], root: Path, port: int, *, require_live: bool
) -> dict:
    runtime = root / "default-zero-config"
    with running_package(
        command, runtime, port, offline=False, v2=None, overview=None
    ) as (base, _token):
        assert_shell(base)
        config = json_request(base, "/api/app-config")
        assert config["v2_available"] is True
        assert config["overview_enabled"] is True
        assert config["default_mode"] == "overview"
        assert_rendered_profile(base, "overview-default")
        from foglight_core.providers import CORE_CANONICAL_ADAPTERS

        bootstrap = json_request(base, "/api/v2/bootstrap")
        actual_sources = {
            item["provider_id"] for item in bootstrap["source_health"]["sources"]
        }
        assert actual_sources == set(CORE_CANONICAL_ADAPTERS)
        live_count = incident_count = 0
        if require_live:
            live_count, incident_count = wait_for_live_overview(base)
    return {
        "default_zero_configuration": "pass",
        "default_live_incident_count": incident_count,
        "default_live_source_count": live_count,
    }


def clean_online_and_restart(command: list[str], root: Path, port: int) -> dict:
    runtime = root / "clean-online"
    with running_package(
        command, runtime, port, offline=False, v2=False, overview=False
    ) as (base, token):
        assert_shell(base)
        assert_rendered_profile(base, "standard")
        settings = json_request(base, "/api/settings")
        assert settings["first_run_done"] is False
        assert settings["keys"]["nasa_firms"] is False
        assert settings["watchlist"] == [] and settings["annotations"] == []
        assert json_request(base, "/api/app-config")["overview_enabled"] is False
        status, _headers, _body = request(
            base, "/api/settings", method="POST",
            headers={"Content-Type": "application/json"}, data=b"{}"
        )
        assert status == 403
        status, _headers, _body = request(
            base, "/api/settings", method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Foglight-Token": token,
            },
            data=b'{"tv_channel":"bbc"}',
        )
        assert status == 200

    with running_package(
        command, runtime, port, offline=False, v2=False, overview=False
    ) as (base, _token):
        assert json_request(base, "/api/settings")["tv_channel"] == "bbc"
    return {"clean_online": "pass", "shutdown_restart": "pass"}


def first_launch_offline(command: list[str], root: Path, port: int) -> dict:
    runtime = root / "first-offline"
    with running_package(
        command, runtime, port, offline=True, v2=True, overview=True
    ) as (base, _token):
        assert_shell(base)
        assert_rendered_profile(base, "first-offline")
        config = json_request(base, "/api/app-config")
        assert config["overview_enabled"] is True and config["v2_available"] is True
        bootstrap = json_request(base, "/api/v2/bootstrap")
        assert bootstrap["incidents"]["items"] == []
        from foglight_core.providers import CORE_CANONICAL_ADAPTERS

        actual_sources = {
            item["provider_id"] for item in bootstrap["source_health"]["sources"]
        }
        assert actual_sources == set(CORE_CANONICAL_ADAPTERS)
    return {
        "first_launch_offline": "pass",
        "scheduled_source_count": len(actual_sources),
    }


def upgrade_profile(command: list[str], root: Path, port: int) -> dict:
    runtime = root / "upgrade"
    state = runtime / "Foglight" / "state"
    cache = runtime / "Foglight" / "cache"
    state.mkdir(parents=True)
    cache.mkdir(parents=True)
    legacy = {
        "keys": {"nasa_firms": "preserve-this-key"},
        "audio": {"master": True, "earthquake": False},
        "panels": {"tv": False, "conflict": False, "wiki": True},
        "tv_channel": "dw",
        "watchlist": ["Typhoon", "Gulf"],
        "annotations": [{"lat": 19.43, "lon": -99.13, "label": "Home"}],
        "rss_feeds": ["https://example.test/legacy.xml"],
    }
    settings_path = state / "settings.json"
    settings_path.write_text(json.dumps(legacy), encoding="utf-8")
    (cache / "crash.bin.tmp").write_bytes(b"partial")
    (cache / "orphan.bin.meta").write_text("{}", encoding="utf-8")
    (cache / "bad.bin").write_bytes(b"payload")
    (cache / "bad.bin.meta").write_bytes(b"x" * (64 * 1024 + 1))

    with running_package(
        command, runtime, port, offline=True, v2=True, overview=True
    ) as (base, token):
        settings = json_request(base, "/api/settings")
        assert settings["keys"]["nasa_firms"] is True
        for field in ("audio", "panels", "tv_channel", "watchlist", "annotations", "rss_feeds"):
            if isinstance(legacy[field], dict):
                for name, value in legacy[field].items():
                    assert settings[field][name] == value
            else:
                assert settings[field] == legacy[field]
        status, _headers, _body = request(
            base, "/api/settings", method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Foglight-Token": token,
            },
            data=b'{"display_mode":"overview","first_run_done":true}',
        )
        assert status == 200

    persisted = json.loads(settings_path.read_text(encoding="utf-8"))
    assert persisted["keys"]["nasa_firms"] == "preserve-this-key"
    assert persisted["watchlist"] == legacy["watchlist"]
    assert persisted["annotations"] == legacy["annotations"]
    assert persisted["display_mode"] == "overview"
    assert not (cache / "crash.bin.tmp").exists()
    assert not (cache / "orphan.bin.meta").exists()
    assert not (cache / "bad.bin").exists()
    assert not (cache / "bad.bin.meta").exists()
    return {"upgrade_preservation": "pass", "corrupt_cache_recovery": "pass"}


def corrupt_history(command: list[str], root: Path, port: int) -> dict:
    runtime = root / "corrupt-history"
    state = runtime / "Foglight" / "state"
    state.mkdir(parents=True)
    database = state / "foglight-v2.sqlite3"
    database.write_bytes(b"not a sqlite database")
    settings_path = state / "settings.json"
    settings_path.write_text(json.dumps({"watchlist": ["preserved"]}), encoding="utf-8")

    with running_package(
        command, runtime, port, offline=True, v2=True, overview=True
    ) as (base, _token):
        bootstrap = json_request(base, "/api/v2/bootstrap")
        assert bootstrap["incidents"]["items"] == []
        assert json_request(base, "/api/settings")["watchlist"] == ["preserved"]

    assert list(state.glob("foglight-v2.sqlite3.corrupt-*"))
    assert database.is_file() and database.stat().st_size > len(b"not a sqlite database")
    assert json.loads(settings_path.read_text(encoding="utf-8"))["watchlist"] == ["preserved"]
    return {"corrupt_history_recovery": "pass"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=ROOT / "dist" / "Foglight.exe")
    parser.add_argument(
        "--source-native",
        action="store_true",
        help="exercise the same native launcher from source when an AV blocks the PE",
    )
    parser.add_argument("--port", type=int, default=19880)
    parser.add_argument(
        "--require-live",
        action="store_true",
        help="require the no-flags executable to ingest live keyless provider data",
    )
    args = parser.parse_args()
    exe = args.exe.resolve()
    if args.source_native:
        command = [sys.executable, str(ROOT / "foglight_native.py")]
    else:
        if not exe.is_file():
            raise SystemExit(f"missing packaged executable: {exe}")
        command = [str(exe)]
    if not 1024 <= args.port <= 65531:
        raise SystemExit("--port must leave room for the five release scenarios")

    root = Path(tempfile.mkdtemp(prefix="foglight-packaged-release-"))
    try:
        results = {
            "execution_mode": (
                "source-native" if args.source_native else "one-file-executable"
            )
        }
        results.update(
            default_zero_configuration(
                command, root, args.port, require_live=args.require_live
            )
        )
        results.update(clean_online_and_restart(command, root, args.port + 1))
        results.update(first_launch_offline(command, root, args.port + 2))
        results.update(upgrade_profile(command, root, args.port + 3))
        results.update(corrupt_history(command, root, args.port + 4))
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
