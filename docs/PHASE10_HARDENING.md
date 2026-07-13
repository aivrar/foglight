# Phase 10 Hardening Evidence

Evidence date: 2026-07-11. This report records implementation-time results;
the execution plan records the final post-self-audit gate.

## Network boundary

- Upstream bodies have both wire-byte and post-decompression caps. Identity,
  gzip, and zlib-wrapped deflate are accepted; unsupported, malformed,
  truncated, trailing, or expanding-over-cap encodings fail closed.
- User-configured RSS uses a direct pinned transport. DNS is resolved inside
  connection establishment, every returned address must be public, and the
  socket connects to that exact validated address. Environment proxies are
  bypassed for this boundary. HTTPS retains the original hostname for SNI and
  default certificate/hostname verification. Redirects repeat validation and
  pinning.
- Fixed canonical providers retain bounded timeout, body, concurrency,
  cadence, retry, cache, stale, and circuit-breaker behavior.
- Python references used for the transport review:
  [http.client](https://docs.python.org/3/library/http.client.html) and
  [ssl](https://docs.python.org/3/library/ssl.html).

## Storage and boundedness

- SQLite backups use the SQLite backup API and are verified with
  `integrity_check`, exact schema version, and `foreign_key_check` before
  publication or restore.
- Restore validates first, creates a pre-restore safety backup, verifies the
  restored database, reinitializes application indexes, and rolls back to the
  safety copy if post-restore initialization fails.
- Retention stress covers 700 large observations, physical compaction, record
  and byte caps, spatial-index consistency, backup, and recovery.
- Database observations (100,000 / 256 MiB), cache (1,000 / 128 MiB), provider
  responses, JSON responses, scheduler workers/context URLs, map points,
  incident pages/timelines, Watch Center data, exports, annotations, and UI
  freshness buffers all have explicit caps. Cache pruning counts both payload
  and metadata bytes, rejects oversized/corrupt sidecars, and bounds its scan
  heap. Settings and packaged JSON catalogs use bounded reads. The optional
  Wikimedia stream bounds line, event, field, and retained-event sizes. Native
  logging enforces its 2 MiB cap during the active process, including Windows
  newline translation.

## Privacy and local server

- Settings returns only booleans for saved keys. Provider catalog metadata is
  bounded to 100 entries and contains no stored credential.
- Credential-like URL query/fragment values are redacted before canonical
  history and again before browser links, print views, CSV, or GeoJSON.
- Local request logs contain method, path, and status only—never the query.
  Proxy logs contain exception type, never provider-controlled exception text.
- Static read failures and optional-stream reconnects do not echo filesystem or
  upstream exception text to HTTP clients or logs.
- The server binds `127.0.0.1`. Invalid Host returns 421; mutations require
  same-origin/no-browser-origin plus a constant-time per-launch token check.
- CSP limits scripts and connections to self, disables script attributes,
  workers, objects, forms, base URLs, and framing, and allowlists only the
  explicit map tile/image and YouTube frame hosts. Inline style remains allowed
  because the legacy single-file shell deliberately contains inline CSS and
  style attributes; no inline script is allowed. COOP, CORP, frame denial,
  MIME sniffing denial, referrer policy, and a restrictive Permissions Policy
  are emitted on every response.

## Supply chain and secrets

- `pip-audit 2.10.1` against `requirements-build.txt`: no known
  vulnerabilities.
- `npm audit --audit-level=high`: zero vulnerabilities.
- `scripts/scan_secrets.py` scans tracked and non-ignored untracked files for
  high-confidence provider credential and private-key signatures without
  printing matched values: clean.
- Gitleaks was not installed on the representative machine; the repository
  scanner is checked in so the release gate does not silently depend on that
  external installation.
- Dependency audit reference: [PyPA pip-audit](https://github.com/pypa/pip-audit).

## Accessibility and performance

- WCAG review: `WCAG_2_2_REVIEW.md`; normative reference:
  [WCAG 2.2](https://www.w3.org/TR/WCAG22/).
- Performance evidence: `baselines/phase10-2026-07-11.json` and
  `PERFORMANCE_BUDGETS.md`. Every published budget passes.

## Compatibility-code decision

No V1 compatibility path was removed in Phase 10. Coverage and current-client
searches show the server routes, Standard UI adapters, provider registry, and
packaged smoke tooling are still exercised. Removing code merely because V2
has an equivalent would break supported Standard mode. Phase 11 will remove an
endpoint only if client, test, and documentation searches all prove zero use.
