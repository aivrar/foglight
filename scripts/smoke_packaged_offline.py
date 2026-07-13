#!/usr/bin/env python3
"""Exercise the packaged V2 app against retained data with upstream networking disabled."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def read_bounded(response, label: str) -> bytes:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"oversized packaged response from {label}")
    return body


def fixture_body(provider_id: str) -> bytes:
    catalog = json.loads(
        (ROOT / "tests" / "fixtures" / "v2" / "core_providers.json").read_text(
            encoding="utf-8"
        )
    )
    value = catalog[provider_id]["valid"]
    return value.encode() if isinstance(value, str) else json.dumps(value).encode()


def read_json(url: str, *, timeout: float = 2.0) -> dict:
    request = urllib.request.Request(url, headers={"Host": urllib.parse.urlparse(url).netloc})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(read_bounded(response, url))
    if not isinstance(value, dict):
        raise RuntimeError(f"{url} did not return a JSON object")
    return value


def request_status(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, read_bounded(response, url)
    except urllib.error.HTTPError as error:
        return error.code, read_bounded(error, url)


def wait_json(url: str, *, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return read_json(url)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
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
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
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


def shutdown(base: str, token: str) -> None:
    request = urllib.request.Request(
        f"{base}/api/shutdown",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "X-Foglight-Token": token},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        response.read()


def seed(local_app_data: Path) -> None:
    from foglight_core.providers.canonical import normalize_provider
    from foglight_core.service import FoglightService
    from foglight_core.storage import ObservationStore

    state = local_app_data / "Foglight" / "state"
    state.mkdir(parents=True, exist_ok=True)
    service = FoglightService(
        ObservationStore(state / "foglight-v2.sqlite3"),
        registry_path=ROOT / "config" / "provider_registry.v1.json",
        taxonomy_path=ROOT / "config" / "data_taxonomy.v1.json",
    )
    now = time.time()
    ingested_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 7200))
    result = normalize_provider(
        "usgs_earthquakes", fixture_body("usgs_earthquakes"), ingested_at=ingested_at
    )
    for observation in result.observations:
        service.ingest(observation)
    service.store.update_source_health(
        "usgs_earthquakes", "cached", ingested_at, detail="retained packaged fixture"
    )
    service.store.save_scheduler_state(
        "usgs_earthquakes",
        {
            "last_attempt": now - 7200,
            "last_success": now - 7200,
            "next_attempt": now - 1,
            "circuit_until": 0,
            "consecutive_failures": 0,
            "etags": {},
            "last_modified": {},
        },
    )
    (state / "settings.json").write_text(
        json.dumps({"display_mode": "overview", "first_run_done": True}),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=ROOT / "dist" / "Foglight.exe")
    parser.add_argument(
        "--source-native",
        action="store_true",
        help="exercise the native launcher from source when an AV blocks the PE",
    )
    parser.add_argument("--port", type=int, default=19879)
    args = parser.parse_args()
    exe = args.exe.resolve()
    if not args.source_native and not exe.is_file():
        raise SystemExit(f"missing packaged executable: {exe}")
    command = (
        [sys.executable, str(ROOT / "foglight_native.py")]
        if args.source_native else [str(exe)]
    )

    runtime = Path(tempfile.mkdtemp(prefix="foglight-packaged-offline-"))
    process: subprocess.Popen[bytes] | None = None
    base = ""
    token = secrets.token_urlsafe(24)
    try:
        seed(runtime)
        env = os.environ.copy()
        env.update(
            {
                "LOCALAPPDATA": str(runtime),
                "APPDATA": str(runtime),
                "FOGLIGHT_NO_BROWSER": "1",
                "FOGLIGHT_PORT": str(args.port),
                "FOGLIGHT_V2_ENABLED": "1",
                "FOGLIGHT_OVERVIEW_ENABLED": "1",
                "FOGLIGHT_SESSION_TOKEN": token,
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1,localhost",
            }
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(command, env=env, creationflags=creationflags)
        base = f"http://127.0.0.1:{args.port}"
        wait_json(f"{base}/api/ping")
        verify_loopback_listener(args.port)
        assert read_json(f"{base}/api/session")["token"] == token
        status, _body = request_status(
            f"{base}/api/ping", headers={"Host": "attacker.example"}
        )
        assert status == 421, "packaged server accepted an invalid Host"
        status, _body = request_status(
            f"{base}/api/settings",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=b"{}",
        )
        assert status == 403, "packaged mutation succeeded without session token"
        status, _body = request_status(
            f"{base}/api/settings",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Foglight-Token": token,
            },
            data=b'// invalid JSON proves authorization precedes parsing',
        )
        assert status == 400, "packaged valid token did not reach request validation"

        deadline = time.monotonic() + 15
        bootstrap = {}
        source = {}
        while time.monotonic() < deadline:
            try:
                bootstrap = read_json(f"{base}/api/v2/bootstrap")
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                time.sleep(0.1)
                continue
            source = next(
                (
                    item
                    for item in bootstrap.get("source_health", {}).get("sources", [])
                    if item.get("provider_id") == "usgs_earthquakes"
                ),
                {},
            )
            if source.get("consecutive_failures", 0) >= 1:
                break
            time.sleep(0.1)

        incident_items = bootstrap.get("incidents", {}).get("items", [])
        assert incident_items, "retained incident was not served"
        assert bootstrap.get("revision_cursor", 0) >= 1
        assert bootstrap.get("last_revision_at")
        assert source.get("status") == "error", source
        assert source.get("detail") == "fetch_error", source
        assert source.get("cached_age_seconds", 0) >= 7100
        assert source.get("consecutive_failures", 0) >= 1, "blocked fetch was not observed"
        search = read_json(f"{base}/api/v2/search?q=Fixture%20Coast&limit=10")
        assert search.get("count", 0) >= 1
        with urllib.request.urlopen(f"{base}/", timeout=2) as response:
            shell = read_bounded(response, f"{base}/").decode("utf-8")
        assert "watch-center" in shell and "overview-history-status" in shell
        rendered = subprocess.run(
            ["node", str(ROOT / "scripts" / "assert_packaged_offline.mjs"), base],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        print(rendered.stdout.strip())
        print(
            json.dumps(
                {
                    "cached_age_seconds": source["cached_age_seconds"],
                    "consecutive_failures": source["consecutive_failures"],
                    "execution_mode": (
                        "source-native" if args.source_native
                        else "one-file-executable"
                    ),
                    "incident_count": len(incident_items),
                    "last_revision_at": bootstrap["last_revision_at"],
                    "revision_cursor": bootstrap["revision_cursor"],
                    "search_count": search["count"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        if base and token:
            with contextlib.suppress(Exception):
                shutdown(base, token)
        if process is not None and process.poll() is None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
        if process is not None and process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                )
            else:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        verify_listener_stopped(args.port)
        shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
