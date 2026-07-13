# Changelog

All notable source-level changes for Foglight should be tracked here.

## 0.2.0 - 2026-07-13

- Fixed the real packaged first-run path so V2/Overview are enabled without
  feature flags, new providers begin within five seconds, outbound work is
  globally bounded, Standard refreshes start in bounded batches, and transient
  launch failures receive an early recovery pass.
- Added local Watch Center regions, severity/category thresholds, opt-in quiet
  hour notifications, deduplication/acknowledgement/snooze state, local search,
  safe CSV/GeoJSON export, and offline-history freshness explanations.
- Added keyless official aviation, FEMA declaration, NOAA marine/coastal, and
  NASA/JPL fireball context with bounded scheduling, provenance, and explicit
  observation/advisory/administrative semantics.
- Added SQLite backup/verified restore, cache/config/stream bounds, DNS-pinned
  user RSS transport, provider terms UI, WCAG 2.2 review, p95 performance
  evidence, secret/dependency audits, and comprehensive release-profile smoke
  tooling.
- Added strict production Authenticode mode with RFC 3161 timestamping and
  Windows policy verification; unsigned artifacts are labeled explicitly.
- Added a feature-gated, zero-key incident Overview with explainable Now cards,
  six category filters, live source/change status, accessible pagination, and
  Overview/Standard/Command display modes, with Overview now the native default
  and explicit environment overrides retained for rollback and compatibility.
- Replaced the runtime Leaflet/CARTO dependency with vendored Leaflet 1.9.4 and
  a checksummed Natural Earth 1:110m offline world base in both map modes.
- Added deterministic Canvas incident clustering, count-bearing keyboard
  clusters, uncertainty geometry, freshness fading, reduced-motion change
  pulses, map/list selection, coordinate pin entry, and optional detailed-tile
  failure fallback.
- Added an accessible incident drawer with facts, metrics, relations, source
  health, semantic provenance, safe evidence links, immutable revision windows,
  non-destructive history preview, deterministic copy summaries, and escaped
  printable incident briefings.
- Hardened the localhost server with loopback-only defaults, Host validation,
  per-launch state-change tokens, CSP, locally vendored map assets, bounded upstream reads/cache size,
  redirect revalidation, and secret-safe cache/log/error handling.
- Added regression tests, Ruff, dependency auditing, Dependabot, and a Windows
  CI workflow that builds and smoke-tests the packaged executable.
- Replaced broken Reddit JSON and Stooq integrations with Reddit RSS and small
  Yahoo Finance futures queries, and corrected aggregate freshness reporting.
- Repaired the defense feed set, parallelized multi-source requests, bounded
  native log growth, and fixed rejected POST bodies poisoning keep-alive
  connections.
- Integrated Smithsonian volcano and tsunami notices into Major Hazards.
- Removed settings and documentation for optional integrations that were not
  implemented end-to-end.
- Pinned build dependencies, upgraded vulnerable transitive packages, added
  SHA-256 release checksums, and added optional Authenticode signing support.
- Converted the app into a native Windows desktop package using a single
  PyInstaller executable.
- Added `foglight_native.py` to start the bundled server and open the dashboard
  inside a WebView2 desktop window.
- Added deterministic icon generation and Windows `.ico` bundling.
- Kept `FOGLIGHT_NO_BROWSER=1` for automated packaged smoke tests.
- Updated app copy and documentation away from the earlier WSL/Linux runtime.
- Added repository documentation, source credits, GitHub templates, and release
  checklist docs.
- Added MIT License and GitHub Release instructions for `Foglight.exe`.

## 0.1.0

- Initial local-first dashboard prototype with live event panels, map overlays,
  RSS proxying, settings, and local cache/state directories.
