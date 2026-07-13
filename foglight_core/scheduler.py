"""Bounded provider scheduler with persistent backoff and health state."""

from __future__ import annotations

import concurrent.futures
import dataclasses
import email.utils
import math
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from .jsonfiles import load_bounded_json
from .models import Observation
from .providers.canonical import CORE_CANONICAL_ADAPTERS, CanonicalAdapter
from .storage import ObservationStore


@dataclasses.dataclass(frozen=True, slots=True)
class FetchResult:
    status: int
    body: bytes = b""
    headers: dict[str, str] = dataclasses.field(default_factory=dict)
    freshness: str = "live"


@dataclasses.dataclass(frozen=True, slots=True)
class ProviderJob:
    provider_id: str
    adapter: CanonicalAdapter
    interval_seconds: int
    timeout_seconds: float = 15
    max_bytes: int = 2 * 1024 * 1024

    def __post_init__(self):
        if self.provider_id != self.adapter.provider_id:
            raise ValueError("job provider and adapter differ")
        if self.interval_seconds < 10:
            raise ValueError("provider interval must be at least 10 seconds")
        if not 1 <= self.timeout_seconds <= 60:
            raise ValueError("provider timeout must be in 1..60 seconds")
        if not 1024 <= self.max_bytes <= 10 * 1024 * 1024:
            raise ValueError("provider body cap is outside safe bounds")


@dataclasses.dataclass(slots=True)
class ProviderState:
    etags: dict[str, str] = dataclasses.field(default_factory=dict)
    last_modified: dict[str, str] = dataclasses.field(default_factory=dict)
    consecutive_failures: int = 0
    next_attempt: float = 0
    last_attempt: float = 0
    last_success: float = 0
    circuit_until: float = 0

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            return cls()
        allowed = {field.name for field in dataclasses.fields(cls)}
        clean = {key: value[key] for key in allowed if key in value}
        try:
            state = cls(**clean)
            for name in (
                "next_attempt", "last_attempt", "last_success", "circuit_until"
            ):
                number = float(getattr(state, name))
                setattr(state, name, number if math.isfinite(number) and number >= 0 else 0)
            state.consecutive_failures = max(0, int(state.consecutive_failures))
            if not isinstance(state.etags, dict) or not isinstance(state.last_modified, dict):
                return cls()
            state.etags = {
                str(key)[:2048]: str(item)[:500] for key, item in state.etags.items()
            }
            state.last_modified = {
                str(key)[:2048]: str(item)[:500]
                for key, item in state.last_modified.items()
            }
            return state
        except (TypeError, ValueError, OverflowError):
            return cls()


FetchCallable = Callable[[str, dict[str, str], float, int], FetchResult]
ObservationSink = Callable[[Observation], object]
SourceLostCallback = Callable[[str, str], object]
ContextURLProvider = Callable[[str], tuple[str, ...] | list[str]]


def jobs_from_registry(path: str | Path) -> tuple[ProviderJob, ...]:
    registry = load_bounded_json(path)
    entries = {item["id"]: item for item in registry["providers"]}
    jobs = []
    for provider_id, adapter in sorted(CORE_CANONICAL_ADAPTERS.items()):
        entry = entries.get(provider_id)
        if entry is None:
            raise ValueError(f"canonical provider missing from registry: {provider_id}")
        if entry.get("auth") != "none":
            raise ValueError(f"core provider unexpectedly requires credentials: {provider_id}")
        jobs.append(
            ProviderJob(
                provider_id,
                adapter,
                int(entry["cadence_seconds"]),
                timeout_seconds=float(entry.get("timeout_seconds", 15)),
                max_bytes=int(entry.get("body_cap_bytes", 2 * 1024 * 1024)),
            )
        )
    return tuple(jobs)


class ProviderScheduler:
    def __init__(
        self,
        jobs: tuple[ProviderJob, ...] | list[ProviderJob],
        *,
        store: ObservationStore,
        fetcher: FetchCallable,
        sink: ObservationSink,
        source_lost: SourceLostCallback | None = None,
        context_urls: ContextURLProvider | None = None,
        max_workers: int = 4,
        jitter: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be in 1..16")
        self.jobs = {job.provider_id: job for job in jobs}
        if len(self.jobs) != len(jobs):
            raise ValueError("duplicate scheduler provider")
        self.store = store
        self.fetcher = fetcher
        self.sink = sink
        self.source_lost = source_lost
        self.context_urls = context_urls
        self.max_workers = max_workers
        self.jitter = jitter
        self.clock = clock
        self._lock = threading.RLock()
        self._inflight: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        now = self.clock()
        self.states = {}
        for provider_id, job in self.jobs.items():
            state = ProviderState.from_dict(self.store.load_scheduler_state(provider_id))
            if state.next_attempt == 0:
                # Spread first requests briefly without leaving a new install
                # blank for a full provider interval (up to several hours).
                state.next_attempt = now + min(
                    5.0, job.interval_seconds * self._jitter_value()
                )
            elif state.next_attempt > now + max(3600, job.interval_seconds * 4):
                # A backward wall-clock shift must not suspend a source indefinitely.
                state.next_attempt = now + job.interval_seconds * self._jitter_value()
            self.states[provider_id] = state

    @property
    def managed_provider_ids(self) -> frozenset[str]:
        return frozenset(self.jobs)

    def _jitter_value(self) -> float:
        try:
            return max(0.0, min(1.0, float(self.jitter())))
        except (TypeError, ValueError):
            return 0.5

    def force_due(self, provider_id: str, *, now: float | None = None) -> None:
        if provider_id not in self.states:
            raise KeyError(provider_id)
        self.states[provider_id].next_attempt = self.clock() if now is None else now

    def run_due(self, *, now: float | None = None) -> list[dict]:
        now = self.clock() if now is None else float(now)
        if not math.isfinite(now) or now < 0:
            raise ValueError("scheduler time must be finite and non-negative")
        with self._lock:
            for provider_id, job in self.jobs.items():
                state = self.states[provider_id]
                if now < state.last_attempt - 60:
                    state.next_attempt = min(
                        state.next_attempt,
                        now + job.interval_seconds * self._jitter_value(),
                    )
                    state.circuit_until = min(state.circuit_until, state.next_attempt)
            due = [
                job for provider_id, job in self.jobs.items()
                if provider_id not in self._inflight
                and self.states[provider_id].next_attempt <= now
                and self.states[provider_id].circuit_until <= now
            ]
            for job in due:
                self._inflight.add(job.provider_id)
        results = []
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
        future_jobs = {pool.submit(self._run_job, job, now): job for job in due}
        pending = set(future_jobs)
        try:
            while pending and not self._stop.is_set():
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=0.1,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    results.append(future.result())
            if self._stop.is_set():
                for future in pending:
                    future.cancel()
        finally:
            # Do not wait through multiple queued timeout waves during app
            # shutdown. Running requests retain their normal timeout; queued
            # work is cancelled before it can start.
            pool.shutdown(
                wait=not self._stop.is_set(),
                cancel_futures=self._stop.is_set(),
            )
            with self._lock:
                for job in future_jobs.values():
                    self._inflight.discard(job.provider_id)
        return sorted(results, key=lambda item: item["provider_id"])

    def _conditional_headers(self, state, url):
        headers = {}
        if state.etags.get(url):
            headers["If-None-Match"] = state.etags[url]
        if state.last_modified.get(url):
            headers["If-Modified-Since"] = state.last_modified[url]
        return headers

    def _source_urls(self, job):
        if not job.adapter.contextual:
            return job.adapter.source_urls
        values = self.context_urls(job.provider_id) if self.context_urls else ()
        if not isinstance(values, (tuple, list)):
            raise TypeError("context URL provider must return a sequence")
        maximum = int(job.adapter.max_context_urls)
        if not 1 <= maximum <= 32 or len(values) > maximum:
            raise ValueError("context URL count exceeds provider bound")
        urls = []
        for value in values:
            if not isinstance(value, str) or len(value) > 2048:
                raise ValueError("invalid contextual provider URL")
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in job.adapter.allowed_context_hosts
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError("contextual provider URL is outside its contract")
            urls.append(value)
        return tuple(dict.fromkeys(urls))

    @staticmethod
    def _header(headers, name):
        target = name.lower()
        return next(
            (str(value) for key, value in headers.items() if str(key).lower() == target),
            None,
        )

    def _run_job(self, job: ProviderJob, now: float) -> dict:
        state = self.states[job.provider_id]
        if self._stop.is_set():
            return self._stopped_result(job, state)
        pending_etags = dict(state.etags)
        pending_last_modified = dict(state.last_modified)
        state.last_attempt = now
        started = time.perf_counter()
        observations = []
        diagnostics = []
        freshnesses = []
        try:
            urls = self._source_urls(job)
            if not urls and job.adapter.contextual:
                state.etags = {}
                state.last_modified = {}
                state.consecutive_failures = 0
                state.circuit_until = 0
                state.next_attempt = now + job.interval_seconds
                status, detail, ok = "idle", "contexts=0;observations=0", True
                latency = (time.perf_counter() - started) * 1000
                checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
                self.store.update_source_health(
                    job.provider_id, status, checked_at, latency_ms=latency, detail=detail
                )
                self.store.save_scheduler_state(job.provider_id, state.to_dict())
                return {
                    "provider_id": job.provider_id,
                    "ok": ok,
                    "status": status,
                    "observations": 0,
                    "diagnostics": 0,
                    "next_attempt": state.next_attempt,
                }
            for url in urls:
                if self._stop.is_set():
                    raise _SchedulerStopped
                response = self.fetcher(
                    url,
                    self._conditional_headers(state, url),
                    job.timeout_seconds,
                    job.max_bytes,
                )
                if not isinstance(response, FetchResult):
                    raise TypeError("fetcher returned an invalid result")
                if response.freshness == "error":
                    raise _ProviderFailure("upstream_error")
                if response.status == 304:
                    freshnesses.append("cached")
                    continue
                if response.status == 204:
                    # Several documented data APIs use 204 to represent a
                    # successful no-data batch. There is no body to normalize,
                    # but this is a live provider check, not a parse failure.
                    freshnesses.append(response.freshness)
                    continue
                if response.status == 429:
                    retry_after = self._retry_after(
                        self._header(response.headers, "Retry-After"), now
                    )
                    raise _ProviderFailure("rate_limited", retry_after=retry_after)
                if not 200 <= response.status < 300:
                    raise _ProviderFailure(f"http_{response.status}")
                if len(response.body) > job.max_bytes:
                    raise _ProviderFailure("body_cap_exceeded")
                normalized = job.adapter.normalize(
                    response.body,
                    ingested_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                )
                fatal = [item for item in normalized.diagnostics if item.code in {
                    "malformed_body", "unexpected_root",
                }]
                if not normalized.observations and any(
                    item.code == "missing_fields" for item in normalized.diagnostics
                ):
                    fatal.append(normalized.diagnostics[0])
                if fatal:
                    raise _ProviderFailure("malformed_body")
                observations.extend(normalized.observations)
                diagnostics.extend(normalized.diagnostics)
                freshnesses.append(response.freshness)
                etag = self._header(response.headers, "ETag")
                modified = self._header(response.headers, "Last-Modified")
                if etag:
                    pending_etags[url] = etag[:500]
                if modified:
                    pending_last_modified[url] = modified[:500]
            if self._stop.is_set():
                raise _SchedulerStopped
            observations = list({
                observation.observation_id: observation for observation in observations
            }.values())
            for observation in observations:
                self.sink(observation)
            # Validators describe a successfully committed provider batch. If
            # another URL or the sink fails, retry every changed response.
            state.etags = {url: pending_etags[url] for url in urls if url in pending_etags}
            state.last_modified = {
                url: pending_last_modified[url]
                for url in urls
                if url in pending_last_modified
            }
            state.consecutive_failures = 0
            state.last_success = now
            state.circuit_until = 0
            state.next_attempt = now + job.interval_seconds
            status = "stale" if "stale" in freshnesses else "cached" if freshnesses and all(
                item == "cached" for item in freshnesses
            ) else "live"
            detail = f"observations={len(observations)};drift={len(diagnostics)}"
            ok = True
        except _SchedulerStopped:
            return self._stopped_result(job, state)
        except _ProviderFailure as error:
            ok, status, detail = False, "error", error.code
            self._schedule_failure(job, state, now, retry_after=error.retry_after)
        except (OSError, TimeoutError, TypeError, ValueError):
            ok, status, detail = False, "error", "fetch_error"
            self._schedule_failure(job, state, now)
        except Exception:
            ok, status, detail = False, "error", "processing_error"
            self._schedule_failure(job, state, now)
        latency = (time.perf_counter() - started) * 1000
        checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        self.store.update_source_health(
            job.provider_id, status, checked_at, latency_ms=latency, detail=detail
        )
        self.store.save_scheduler_state(job.provider_id, state.to_dict())
        return {
            "provider_id": job.provider_id,
            "ok": ok,
            "status": status,
            "observations": len(observations),
            "diagnostics": len(diagnostics),
            "next_attempt": state.next_attempt,
        }

    @staticmethod
    def _stopped_result(job, state):
        return {
            "provider_id": job.provider_id,
            "ok": True,
            "status": "stopped",
            "observations": 0,
            "diagnostics": 0,
            "next_attempt": state.next_attempt,
        }

    def _schedule_failure(self, job, state, now, *, retry_after=None):
        state.consecutive_failures += 1
        exponent = min(6, state.consecutive_failures - 1)
        backoff = min(3600, job.interval_seconds * (2**exponent))
        backoff *= 0.75 + self._jitter_value() * 0.5
        state.next_attempt = max(now + backoff, retry_after or 0)
        if state.consecutive_failures >= 3:
            state.circuit_until = max(
                state.next_attempt,
                now + max(300, job.interval_seconds * 4),
            )
            state.next_attempt = state.circuit_until
            if state.consecutive_failures == 3 and self.source_lost is not None:
                checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
                try:
                    self.source_lost(job.provider_id, checked_at)
                except Exception:
                    pass

    @staticmethod
    def _retry_after(value, now):
        if not value:
            return None
        try:
            seconds = max(0, min(86400, int(value)))
            return now + seconds
        except (TypeError, ValueError):
            try:
                parsed = email.utils.parsedate_to_datetime(value)
                return min(now + 86400, max(now, parsed.timestamp()))
            except (TypeError, ValueError, OverflowError):
                return None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="foglight-scheduler", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.run_due()
            except Exception:
                # One unexpected provider/runtime failure must not permanently
                # terminate scheduling for every other source.
                pass
            self._stop.wait(0.5)

    def stop(self, timeout=None) -> bool:
        self.request_stop()
        if self._thread:
            if timeout is None:
                timeout = max(
                    (job.timeout_seconds for job in self.jobs.values()), default=1
                ) + 1
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def request_stop(self) -> None:
        """Signal cancellation without blocking the UI close event."""
        self._stop.set()


class _ProviderFailure(Exception):
    def __init__(self, code, *, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class _SchedulerStopped(Exception):
    pass
