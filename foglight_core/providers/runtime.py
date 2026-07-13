"""Injected provider runtime dependencies, isolated to avoid import cycles."""

from __future__ import annotations

from typing import Callable

FetchCallable = Callable[..., tuple[bytes, str, int, str]]
_fetcher: FetchCallable | None = None


def configure(fetcher: FetchCallable) -> None:
    global _fetcher
    _fetcher = fetcher


def fetch(*args, **kwargs):
    if _fetcher is None:
        raise RuntimeError("provider adapters are not configured")
    return _fetcher(*args, **kwargs)
