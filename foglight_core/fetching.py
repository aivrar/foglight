"""Safe, bounded upstream HTTP fetching with stale-cache fallback."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http import HTTPStatus

from .settings import looks_like_http_url

# Both the V2 scheduler and compatibility endpoints share this gate. It keeps a
# normal dashboard launch from opening dozens of DNS/TLS connections at once.
MAX_CONCURRENT_UPSTREAM_REQUESTS = 6
UPSTREAM_REQUEST_GATE = threading.BoundedSemaphore(MAX_CONCURRENT_UPSTREAM_REQUESTS)


def _failure_label(error):
    reason = getattr(error, "reason", None)
    if reason is not None and reason is not error:
        return f"{type(error).__name__}/{type(reason).__name__}"
    return type(error).__name__


def _is_public_address(address):
    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolved_addresses(host, port):
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise OSError(f"dns lookup failed: {error}") from error
    else:
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        sockaddr = (str(literal), port, 0, 0) if literal.version == 6 else (str(literal), port)
        infos = [(family, socket.SOCK_STREAM, 0, "", sockaddr)]

    candidates = []
    seen = set()
    for family, socktype, proto, _canonname, sockaddr in infos:
        try:
            address = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        except (ValueError, TypeError, IndexError):
            continue
        identity = (family, socktype, proto, sockaddr)
        if identity not in seen:
            seen.add(identity)
            candidates.append((family, socktype, proto, sockaddr, address))
    if not candidates:
        raise OSError("dns lookup produced no usable addresses")
    if any(not _is_public_address(item[4]) for item in candidates):
        raise OSError("local/private network targets are not allowed")
    return candidates


def validate_external_fetch_url(url, allowed_ports=(80, 443)):
    if not looks_like_http_url(url):
        return False, "bad url"
    parsed = urllib.parse.urlparse(url)
    if parsed.username or parsed.password:
        return False, "credentials are not allowed in URLs"
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False, "bad port"
    if allowed_ports and port not in allowed_ports:
        return False, "only standard HTTP ports are allowed"

    host = (parsed.hostname or "").strip().strip("[]").lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        return False, "local hosts are not allowed"
    try:
        _resolved_addresses(host, port)
    except OSError as error:
        return False, str(error)
    return True, ""


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, reason = validate_external_fetch_url(newurl)
        if not ok:
            raise urllib.error.HTTPError(
                req.full_url,
                HTTPStatus.FORBIDDEN,
                f"unsafe redirect blocked: {reason}",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


URL_OPENER = urllib.request.build_opener(SafeRedirectHandler())


def _create_public_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                              source_address=None):
    """Connect only to addresses returned and validated by this DNS lookup."""
    host, port = address
    last_error = None
    for family, socktype, proto, sockaddr, _ip in _resolved_addresses(host, port):
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as error:
            last_error = error
            if sock is not None:
                sock.close()
    if last_error is not None:
        raise last_error
    raise OSError("dns lookup produced no usable addresses")


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _create_public_connection


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _create_public_connection


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(PinnedHTTPConnection, req)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PinnedHTTPSConnection, req, context=self._context)


# User-configured fetches bypass environment proxies: the transport must connect
# to the exact public addresses it validates. HTTPSConnection retains the
# original hostname and therefore performs normal SNI and certificate checks.
PINNED_URL_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    PinnedHTTPHandler(),
    PinnedHTTPSHandler(context=ssl.create_default_context()),
    SafeRedirectHandler(),
)


def redact_url(url, *, hide_path=False):
    try:
        parsed = urllib.parse.urlsplit(url)
        query = []
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            if re.search(r"(?:api[_-]?key|token|secret|password|appid)", key, re.I):
                value = "<redacted>"
            query.append((key, value))
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return urllib.parse.urlunsplit(
            (
                parsed.scheme,
                host,
                "/<user-configured>" if hide_path else parsed.path,
                "" if hide_path else urllib.parse.urlencode(query),
                "",
            )
        )
    except (TypeError, ValueError):
        return "<redacted-url>"


def _decompress_limited(data, max_bytes, *, wbits):
    decoder = zlib.decompressobj(wbits)
    output = bytearray()
    pending = data
    try:
        while pending:
            remaining = max_bytes + 1 - len(output)
            if remaining <= 0:
                raise ValueError("upstream response is too large after decompression")
            output.extend(decoder.decompress(pending, remaining))
            if len(output) > max_bytes:
                raise ValueError("upstream response is too large after decompression")
            pending = decoder.unconsumed_tail
            if not pending:
                break
        remaining = max_bytes + 1 - len(output)
        output.extend(decoder.flush(remaining))
    except zlib.error as error:
        raise ValueError("upstream response has invalid compression") from error
    if len(output) > max_bytes:
        raise ValueError("upstream response is too large after decompression")
    if not decoder.eof or decoder.unused_data:
        raise ValueError("upstream response has invalid compression")
    return bytes(output)


def read_limited(response, max_bytes):
    length = response.headers.get("Content-Length")
    if length:
        try:
            declared_length = int(length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            raise ValueError("upstream response is too large")
    data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("upstream response is too large")
    encoding = str(response.headers.get("Content-Encoding") or "").strip().lower()
    if encoding in ("", "identity"):
        return data
    if encoding == "gzip":
        return _decompress_limited(data, max_bytes, wbits=16 + zlib.MAX_WBITS)
    if encoding == "deflate":
        return _decompress_limited(data, max_bytes, wbits=zlib.MAX_WBITS)
    raise ValueError("unsupported upstream content encoding")


def combine_freshness(values):
    values = list(values)
    if not values:
        return "error"
    usable = [value for value in values if value != "error"]
    if not usable:
        return "error"
    if len(usable) != len(values) or "stale" in usable:
        return "stale"
    if "cached" in usable:
        return "cached"
    return "live"


def fetch(
    url,
    *,
    cache,
    opener=URL_OPENER,
    pinned_opener=PINNED_URL_OPENER,
    user_agent,
    max_upstream_bytes,
    ttl=120,
    ctype_hint=None,
    extra_headers=None,
    timeout=10,
    cache_key=None,
    log_url=None,
    max_bytes=None,
    validate_url=False,
):
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, text/xml, application/xml, */*",
    }
    if extra_headers:
        headers.update(extra_headers)
    if validate_url:
        ok, reason = validate_external_fetch_url(url)
        if not ok:
            return json.dumps({"error": reason}).encode(), "application/json", 0, "error"
        opener = pinned_opener

    cache_key = cache_key or url
    display_url = log_url or redact_url(url, hide_path=validate_url)
    cached, status, timestamp = cache.get(cache_key, ttl)
    if status == "hit":
        return (
            cached,
            ctype_hint or "application/json",
            int(time.time() - timestamp),
            "cached",
        )
    try:
        request = urllib.request.Request(url, headers=headers)
        with UPSTREAM_REQUEST_GATE:
            with opener.open(request, timeout=timeout) as response:
                data = read_limited(response, max_bytes or max_upstream_bytes)
                ctype = response.headers.get("Content-Type") or (
                    ctype_hint or "application/octet-stream"
                )
            cache.put(cache_key, data, ctype)
            return data, ctype, 0, "live"
    except Exception as error:
        sys.stderr.write(f"[fetch] {display_url} failed: {_failure_label(error)}\n")
        if cached is not None:
            return (
                cached,
                ctype_hint or "application/json",
                int(time.time() - timestamp),
                "stale",
            )
        body = json.dumps(
            {"error": "upstream request failed", "kind": type(error).__name__}
        ).encode()
        return body, "application/json", 0, "error"
