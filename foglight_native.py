#!/usr/bin/env python3
"""Windows-native Foglight launcher.

This is the entry point for the single-file Windows desktop build. It bundles
the Python server and web assets, starts the HTTP server on localhost, opens a
WebView desktop window, and stores runtime state under LocalAppData.
"""
from __future__ import annotations

import http.server
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

APP_NAME = "Foglight"
WINDOW_OPTIONS = {
    "width": 1500,
    "height": 950,
    "min_size": (900, 640),
    "maximized": True,
    "text_select": False,
}
DEFAULT_PORT = 9787
_LOG_HANDLE = None
_NOTIFICATION_PERMISSION_HANDLERS = []


def rotate_log(path: Path, max_bytes: int = 2 * 1024 * 1024) -> None:
    """Keep the native launcher log bounded without risking app startup."""
    try:
        if path.stat().st_size <= max_bytes:
            return
        with path.open("rb") as source:
            source.seek(-(max_bytes // 2), 2)
            source.readline()
            tail = source.read()
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("wb") as target:
            target.write(b"[log rotated --- older entries truncated]\n")
            target.write(tail)
        temporary.replace(path)
    except OSError:
        pass


class BoundedLogWriter:
    """Line-buffered UTF-8 log sink that enforces its cap during a session."""

    encoding = "utf-8"

    def __init__(self, path: Path, max_bytes: int = 2 * 1024 * 1024) -> None:
        if max_bytes < 512:
            raise ValueError("log cap must be at least 512 bytes")
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        rotate_log(path, max_bytes)
        self._handle = path.open("a", encoding=self.encoding, buffering=1, newline="")

    def write(self, value: object) -> int:
        text = str(value)
        encoded = text.encode(self.encoding, errors="replace")
        if len(encoded) > self.max_bytes // 2:
            encoded = encoded[-(self.max_bytes // 2):]
            text = encoded.decode(self.encoding, errors="replace")
        with self._lock:
            try:
                current = self.path.stat().st_size
            except OSError:
                current = 0
            if current + len(encoded) > self.max_bytes:
                self._handle.close()
                retention_cap = max(256, self.max_bytes - (2 * len(encoded)))
                rotate_log(self.path, retention_cap)
                self._handle = self.path.open(
                    "a", encoding=self.encoding, buffering=1, newline=""
                )
                try:
                    current = self.path.stat().st_size
                except OSError:
                    current = 0
                available = max(0, self.max_bytes - current)
                if len(encoded) > available:
                    encoded = encoded[-available:] if available else b""
                    text = encoded.decode(self.encoding, errors="replace")
            return self._handle.write(text)

    def flush(self) -> None:
        with self._lock:
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()

    def isatty(self) -> bool:
        return False


def bundled_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def state_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def create_http_server(handler, preferred: int = DEFAULT_PORT):
    """Bind once and keep the socket, avoiding the probe-then-bind race."""
    forced = os.environ.get("FOGLIGHT_PORT")
    if forced:
        port = int(forced)
        if not 1 <= port <= 65535:
            raise ValueError("FOGLIGHT_PORT must be between 1 and 65535")
        return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler), port

    for port in [preferred, *range(19787, 19850)]:
        try:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
            return httpd, httpd.server_address[1]
        except OSError:
            continue
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
    # V2 is the completed zero-configuration product. Explicit 0 values remain
    # available to compatibility tests and emergency rollback launches.
    os.environ.setdefault("FOGLIGHT_V2_ENABLED", "1")
    os.environ.setdefault("FOGLIGHT_OVERVIEW_ENABLED", "1")
    return root, runtime


def patch_server_for_native(foglight_server):
    def native_stop_async(delay=0.0, terminate_distro=True):
        def exit_later():
            if delay:
                time.sleep(delay)
            os._exit(0)

        threading.Thread(target=exit_later, daemon=True).start()

    foglight_server.spawn_stop_async = native_stop_async


def notification_permission_allowed(kind, user_initiated: bool, uri: str, origin: str) -> bool:
    """Allow only an explicit notification request from this launch's loopback origin."""
    if not user_initiated or str(kind).rsplit(".", 1)[-1] not in {"Notifications", "4"}:
        return False
    try:
        requested = urllib.parse.urlsplit(uri)
        expected = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    return (
        requested.scheme == expected.scheme == "http"
        and requested.hostname == expected.hostname == "127.0.0.1"
        and requested.port == expected.port
        and requested.username is None
        and requested.password is None
    )


def configure_webview2_permissions(window, origin: str) -> None:
    """Attach the narrow WebView2 permission handler after pywebview initializes."""
    def loaded():
        try:
            from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState
            from System import Action
            from webview.platforms.winforms import BrowserView

            browser = BrowserView.instances[window.uid].browser

            def attach():
                core = browser.webview.CoreWebView2
                if getattr(browser, "_foglight_permission_handler", None):
                    return

                def permission_requested(_sender, args):
                    allowed = notification_permission_allowed(
                        args.PermissionKind, bool(args.IsUserInitiated), str(args.Uri), origin
                    )
                    args.State = (
                        CoreWebView2PermissionState.Allow
                        if allowed else CoreWebView2PermissionState.Deny
                    )

                browser._foglight_permission_handler = permission_requested
                _NOTIFICATION_PERMISSION_HANDLERS.append(permission_requested)
                core.PermissionRequested += permission_requested

            if browser.webview.InvokeRequired:
                browser.webview.Invoke(Action(attach))
            else:
                attach()
        except Exception as error:
            print(f"[native] WebView2 notification permission unavailable: {error}", flush=True)

    window.events.loaded += loaded


def configure_scheduler_shutdown(window, scheduler) -> None:
    """Signal provider cancellation as soon as the native window starts closing."""
    if scheduler is not None:
        window.events.closing += scheduler.request_stop


def main() -> int:
    global _LOG_HANDLE
    _root, runtime = configure_environment()
    log_path = runtime / "logs" / "native.log"
    try:
        _LOG_HANDLE = BoundedLogWriter(log_path)
        sys.stdout = _LOG_HANDLE
        sys.stderr = _LOG_HANDLE
    except OSError:
        pass

    import foglight_server

    patch_server_for_native(foglight_server)
    scheduler = foglight_server.start_v2_if_enabled()

    httpd, port = create_http_server(foglight_server.Handler)
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
            if scheduler:
                scheduler.stop()
        return 0

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    try:
        try:
            import webview

            webview.settings["ALLOW_DOWNLOADS"] = True
            window = webview.create_window(
                APP_NAME,
                url,
                **WINDOW_OPTIONS,
            )
            configure_scheduler_shutdown(window, scheduler)
            configure_webview2_permissions(window, url)
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
        if scheduler:
            scheduler.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
