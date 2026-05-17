#!/usr/bin/env python3
"""Windows-native Foglight launcher.

This is the entry point for the single-file Windows desktop build. It bundles
the Python server and web assets, starts the HTTP server on localhost, opens a
WebView desktop window, and stores runtime state under LocalAppData.
"""
from __future__ import annotations

import http.server
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


APP_NAME = "Foglight"
DEFAULT_PORT = 9787
_LOG_HANDLE = None


def bundled_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def state_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def find_port(preferred: int = DEFAULT_PORT) -> int:
    forced = os.environ.get("FOGLIGHT_PORT")
    if forced:
        port = int(forced)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
        return port

    for port in [preferred, *range(19787, 19850)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free localhost port found for Foglight")


def configure_environment() -> tuple[Path, Path]:
    root = bundled_root()
    runtime = state_root()
    for name in ("cache", "state", "logs"):
        (runtime / name).mkdir(parents=True, exist_ok=True)

    os.environ["FOGLIGHT_APP_DIR"] = str(root)
    os.environ["FOGLIGHT_CACHE_DIR"] = str(runtime / "cache")
    os.environ["FOGLIGHT_STATE_DIR"] = str(runtime / "state")
    os.environ["FOGLIGHT_LOG_DIR"] = str(runtime / "logs")
    os.environ["FOGLIGHT_NATIVE"] = "1"
    return root, runtime


def patch_server_for_native(foglight_server):
    def native_stop_async(delay=0.0, terminate_distro=True):
        def exit_later():
            if delay:
                time.sleep(delay)
            os._exit(0)

        threading.Thread(target=exit_later, daemon=True).start()

    foglight_server.spawn_stop_async = native_stop_async


def main() -> int:
    global _LOG_HANDLE
    _root, runtime = configure_environment()
    log_path = runtime / "logs" / "native.log"
    try:
        _LOG_HANDLE = log_path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = _LOG_HANDLE
        sys.stderr = _LOG_HANDLE
    except OSError:
        pass

    import foglight_server

    patch_server_for_native(foglight_server)

    port = find_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), foglight_server.Handler)
    url = f"http://127.0.0.1:{port}/"

    try:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] listening {url}", flush=True)
    except OSError:
        pass

    if os.environ.get("FOGLIGHT_NO_BROWSER") == "1":
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            httpd.server_close()
        return 0

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    try:
        try:
            import webview

            webview.create_window(
                APP_NAME,
                url,
                width=1500,
                height=950,
                min_size=(1100, 760),
                text_select=False,
            )
            webview.start(gui="edgechromium", debug=False)
            return 0
        except Exception as e:
            print(f"[native] WebView startup failed: {e}", flush=True)
            webbrowser.open(url)
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
