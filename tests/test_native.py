import http.server
import sys
import types

import pytest

import foglight_native as native


def test_native_window_contract_is_maximized_and_reflow_capable():
    assert native.WINDOW_OPTIONS == {
        "width": 1500,
        "height": 950,
        "min_size": (900, 640),
        "maximized": True,
        "text_select": False,
    }


def test_native_server_binds_loopback(monkeypatch):
    monkeypatch.setenv("FOGLIGHT_PORT", "0")
    with pytest.raises(ValueError, match="between 1 and 65535"):
        native.create_http_server(http.server.BaseHTTPRequestHandler)

    monkeypatch.delenv("FOGLIGHT_PORT")
    httpd, port = native.create_http_server(http.server.BaseHTTPRequestHandler, preferred=0)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
        assert port == httpd.server_address[1]
    finally:
        httpd.server_close()


def test_native_defaults_to_zero_configuration_v2_overview(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("FOGLIGHT_V2_ENABLED", raising=False)
    monkeypatch.delenv("FOGLIGHT_OVERVIEW_ENABLED", raising=False)
    _root, runtime = native.configure_environment()
    assert runtime == tmp_path / "Foglight"
    assert native.os.environ["FOGLIGHT_V2_ENABLED"] == "1"
    assert native.os.environ["FOGLIGHT_OVERVIEW_ENABLED"] == "1"

    monkeypatch.setenv("FOGLIGHT_V2_ENABLED", "0")
    monkeypatch.setenv("FOGLIGHT_OVERVIEW_ENABLED", "0")
    native.configure_environment()
    assert native.os.environ["FOGLIGHT_V2_ENABLED"] == "0"
    assert native.os.environ["FOGLIGHT_OVERVIEW_ENABLED"] == "0"


def test_native_window_close_signals_scheduler_before_teardown():
    class Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    calls = []
    window = types.SimpleNamespace(events=types.SimpleNamespace(closing=Event()))
    scheduler = types.SimpleNamespace(request_stop=lambda: calls.append("stop"))
    native.configure_scheduler_shutdown(window, scheduler)
    assert len(window.events.closing.handlers) == 1
    window.events.closing.handlers[0]()
    assert calls == ["stop"]

    empty = types.SimpleNamespace(events=types.SimpleNamespace(closing=Event()))
    native.configure_scheduler_shutdown(empty, None)
    assert empty.events.closing.handlers == []


def test_native_log_rotation_is_bounded(tmp_path):
    path = tmp_path / "native.log"
    path.write_bytes((b"old line\n" * 1000) + b"latest line\n")

    native.rotate_log(path, max_bytes=1024)

    content = path.read_bytes()
    assert content.startswith(b"[log rotated")
    assert content.endswith(b"latest line\n")
    assert len(content) < 1024


def test_native_log_writer_stays_bounded_during_long_session(tmp_path):
    path = tmp_path / "native.log"
    writer = native.BoundedLogWriter(path, max_bytes=512)
    try:
        for index in range(100):
            writer.write(f"entry {index:03d} " + ("x" * 60) + "\n")
        writer.flush()
    finally:
        writer.close()

    content = path.read_bytes()
    assert content.startswith(b"[log rotated")
    normalized = content.replace(b"\r\n", b"\n")
    assert b"entry 099 " in normalized
    assert normalized.endswith(b"x\n")
    assert len(content) <= 512

    with pytest.raises(ValueError, match="at least 512"):
        native.BoundedLogWriter(tmp_path / "too-small.log", max_bytes=511)


def test_native_log_writer_counts_newlines_as_exact_bytes(tmp_path):
    path = tmp_path / "native.log"
    writer = native.BoundedLogWriter(path, max_bytes=512)
    try:
        writer.write(("line\n" * 150))
        writer.flush()
    finally:
        writer.close()
    assert path.stat().st_size <= 512


def test_notification_permission_is_exact_loopback_user_gesture_only():
    origin = "http://127.0.0.1:9787/"
    assert native.notification_permission_allowed(
        "CoreWebView2PermissionKind.Notifications", True,
        "http://127.0.0.1:9787/", origin,
    )
    assert native.notification_permission_allowed(4, True, "http://127.0.0.1:9787/path", origin)
    assert not native.notification_permission_allowed("Notifications", False, origin, origin)
    assert not native.notification_permission_allowed("Geolocation", True, origin, origin)
    assert not native.notification_permission_allowed("Notifications", True, "http://127.0.0.1:9788/", origin)
    assert not native.notification_permission_allowed("Notifications", True, "http://localhost:9787/", origin)
    assert not native.notification_permission_allowed("Notifications", True, "https://127.0.0.1:9787/", origin)
    assert not native.notification_permission_allowed(
        "Notifications", True, "http://user@127.0.0.1:9787/", origin
    )
    assert not native.notification_permission_allowed("Notifications", True, "not a uri", origin)


def test_webview_permission_handler_explicitly_allows_only_notification_gestures(monkeypatch):
    class Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    loaded = Event()
    permission_requested = Event()

    class Control:
        InvokeRequired = False

    browser = types.SimpleNamespace(
        webview=types.SimpleNamespace(
            CoreWebView2=types.SimpleNamespace(PermissionRequested=permission_requested),
            Invoke=lambda action: action(),
            InvokeRequired=False,
        )
    )
    window = types.SimpleNamespace(uid="fixture", events=types.SimpleNamespace(loaded=loaded))
    permission_state = types.SimpleNamespace(Allow="allow", Deny="deny")
    modules = {
        "Microsoft": types.ModuleType("Microsoft"),
        "Microsoft.Web": types.ModuleType("Microsoft.Web"),
        "Microsoft.Web.WebView2": types.ModuleType("Microsoft.Web.WebView2"),
        "Microsoft.Web.WebView2.Core": types.SimpleNamespace(
            CoreWebView2PermissionState=permission_state
        ),
        "System": types.SimpleNamespace(Action=lambda action: action),
        "webview.platforms.winforms": types.SimpleNamespace(
            BrowserView=types.SimpleNamespace(instances={"fixture": types.SimpleNamespace(browser=browser)})
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    native.configure_webview2_permissions(window, "http://127.0.0.1:9787/")
    assert len(loaded.handlers) == 1
    loaded.handlers[0]()
    assert len(permission_requested.handlers) == 1
    handler = permission_requested.handlers[0]

    allowed = types.SimpleNamespace(
        PermissionKind="Notifications", IsUserInitiated=True,
        Uri="http://127.0.0.1:9787/", State="default",
    )
    handler(None, allowed)
    assert allowed.State == "allow"
    denied = types.SimpleNamespace(
        PermissionKind="Geolocation", IsUserInitiated=True,
        Uri="http://127.0.0.1:9787/", State="default",
    )
    handler(None, denied)
    assert denied.State == "deny"
