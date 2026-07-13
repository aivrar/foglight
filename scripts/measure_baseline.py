#!/usr/bin/env python3
"""Measure reproducible local-server and artifact baselines without live feeds."""

from __future__ import annotations

import argparse
import ctypes
import http.client
import http.server
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percent)))
    return ordered[index]


def request(port: int, path: str) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        started = time.perf_counter()
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status != 200:
            raise RuntimeError(f"{path} returned {response.status}")
        return round(elapsed_ms, 3), body
    finally:
        connection.close()


def process_rss_bytes() -> int | None:
    """Return resident memory using only the standard library."""
    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        ctypes.windll.psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            process, ctypes.byref(counters), counters.cb
        ):
            return int(counters.WorkingSetSize)
        return None
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)
    except (ImportError, OSError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix="foglight-baseline-") as runtime:
        os.environ["FOGLIGHT_APP_DIR"] = str(root)
        os.environ["FOGLIGHT_CACHE_DIR"] = str(Path(runtime) / "cache")
        os.environ["FOGLIGHT_STATE_DIR"] = str(Path(runtime) / "state")
        os.environ["FOGLIGHT_LOG_DIR"] = str(Path(runtime) / "logs")

        import foglight_server

        class QuietHandler(foglight_server.Handler):
            def log_message(self, _format: str, *_args: object) -> None:
                pass

        started = time.perf_counter()
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        request(port, "/api/ping")
        startup_ms = (time.perf_counter() - started) * 1000

        try:
            paths = ("/api/ping", "/api/settings", "/")
            timings: dict[str, list[float]] = {path: [] for path in paths}
            sizes: dict[str, int] = {}
            for path in paths:
                for _ in range(max(1, args.samples)):
                    elapsed, body = request(port, path)
                    timings[path].append(elapsed)
                    sizes[path] = len(body)

            result = {
                "schema_version": 1,
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "python": os.sys.version.split()[0],
                "server_startup_to_ping_ms": round(startup_ms, 3),
                "server_process_rss_bytes": process_rss_bytes(),
                "samples": args.samples,
                "local_http": {
                    path: {
                        "median_ms": round(statistics.median(values), 3),
                        "p95_ms": round(percentile(values, 0.95), 3),
                        "body_bytes": sizes[path],
                    }
                    for path, values in timings.items()
                },
                "source_bytes": {
                    name: (root / name).stat().st_size
                    for name in ("foglight_server.py", "web/app.js", "index.html")
                },
                "artifact_bytes": (
                    (root / "dist" / "Foglight.exe").stat().st_size
                    if (root / "dist" / "Foglight.exe").is_file()
                    else None
                ),
            }
            print(json.dumps(result, indent=2, sort_keys=True))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
