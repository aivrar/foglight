# Foglight V2 Release Evidence

Evidence date: 2026-07-12. This report maps the 100-point release scorecard to
repeatable evidence. A category is awarded only when every mandatory item in
that category passes. Source, browser, native-window, and one-file executable
gates are complete. Authenticode signing is optional publisher-side provenance,
not a Foglight user or runtime requirement.

## Scorecard Map

| Category | Points | Evidence | Status |
|---|---:|---|---|
| Product usefulness | 15 | Zero-key Overview/Standard/Command modes, Now list, catalog, offline map, drawer, timelines, change polling, Watch Center, and all empty/partial/stale/offline states; 52 Playwright scenarios | Pass |
| Data correctness and provenance | 15 | Versioned taxonomy/registry/rules, golden adapter fixtures, canonical mapping report, unit/time/geometry boundaries, conservative correlation and explicit semantic lanes; Python suite | Pass |
| Reliability and provider isolation | 15 | Scheduler cadence/backoff/jitter/validator/circuit tests, stale behavior, per-source health, SQLite restart/migration/corruption/backup/restore, malformed-provider isolation | Pass |
| UX and information design | 10 | Explainable priority hierarchy, category filters, map/list linkage, non-map catalog, every view state, reviewed desktop/mobile snapshots | Pass |
| Accessibility | 10 | `WCAG_2_2_REVIEW.md`, axe serious/critical gate with color contrast, keyboard/focus, 24 px targets, 200% reflow, reduced motion, non-map parity | Pass |
| Performance and bounded resources | 10 | `baselines/phase10-2026-07-11.json`, 100 local samples, 20 browser samples, 5,000-geometry stress, database/cache/log/stream/static/config caps | Pass |
| Zero-key, licensing, and attribution | 10 | Required Overview providers need no account/key; optional FIRMS is isolated; reviewed registry terms, local Leaflet, public-domain Natural Earth base, visible provider/map attribution | Pass |
| Security and privacy | 5 | Loopback/Host/token/CSP/SSRF/DNS-pin/compression/log/export/secret regressions, local-only state, clean secret scan and dependency audits | Pass |
| Testing and maintainability | 5 | Ruff; 288 Python tests; 43 JS tests at 100% line coverage; 52 browser tests; modular core/UI boundaries; versioned fixtures and drift diagnostic | Pass |
| Packaging, migration, and documentation | 5 | Checksummed one-file PE with recorded signature status and version metadata; executable clean/offline/upgrade/corruption/outage/restart profiles; native-window evidence; complete docs | Pass |

Final release score: **100/100 (10/10)**. Every mandatory category passes
without a skipped package scenario or undocumented waiver.

## Complete Automated Evidence

- Ruff: clean.
- Python: 288 tests passed in the corrected final post-audit smoke at 89% aggregate
  line/branch coverage.
- JavaScript: 43 tests passed, 100% lines and 93.32% branches overall.
- Browser: 52 Playwright scenarios passed, including all visual baselines and
  proof that conditional Yahoo traffic occurs only after explicit opt-in.
- Supply chain: `pip-audit` found no known vulnerability; `npm audit` found
  zero vulnerability.
- Secrets: tracked plus non-ignored untracked scan returned no finding.
- Performance: 12.732 ms server startup, 25.636 ms worst local-route p95,
  331.277 ms shell p95, 490.04 ms map p95, 501.789 ms incident p95,
  33.8 MiB server RSS, and 10 MB browser heap p95.
- An intermediate unsigned local-QA build produced a 19,046,151-byte PE and SHA-256
  `6b03b13a893f244983fb25e9a5a4c5c9b048bbfc58cb27db2cb604086b023ba2`.
  It predates the final post-audit source corrections and is recorded only as
  quarantine evidence, not as a releasable or source-matching artifact.
- The opt-in live diagnostic reached NWS, FEMA v2, Open Notify, Wikimedia, and
  every other available core normalizer without schema drift after correcting
  an unsupported NWS diagnostic parameter and the retired-in-practice FEMA v1
  request. SEC returned an isolated optional-source 403 under its fair-access
  bot policy; external availability is not treated as deterministic CI proof.
- Final one-file executable: 19,050,146 bytes, SHA-256
  `ce8798df2ed9a18821492c773fc88aa90e58bd63e408e076b3e68ef0fd114b55`,
  exact `SHA256SUMS.txt` match, Windows product/file version 0.2.0/0.2.0.0,
  original filename `Foglight.exe`, and accurately recorded Authenticode status
  `NotSigned`.

## Production Artifact Evidence

The final PE was built from the post-audit worktree after narrowly allowing only
the ignored `dist` build-output directory in the local endpoint-protection
configuration. The exact artifact then passed both executable-mode suites; no
source-native result is substituted for package proof.

The corrected source-level post-audit gate passes Ruff, 288 Python tests at 89%
aggregate coverage, 43 JavaScript tests at 100% lines / 93.32% branches, all
52 Playwright scenarios, every reviewed visual baseline, dependency audits,
secret scanning, Markdown/provider/release contracts, and diff whitespace.
The independent visual review also found and fixed an existing counter bug:
Standard mode now retains one latest freshness value per named source instead
of accumulating refresh attempts toward a meaningless 80-entry total.

Executable evidence covers rendered clean and first-offline launches, exact
parity with all 14 canonical scheduled providers, settings/key/panel/audio/TV/
watch/pin/RSS upgrade preservation, corrupt cache cleanup, corrupt SQLite
quarantine and rebuild without settings loss, retained two-hour-old incident
history during a blocked provider refresh, local search, Host/token boundaries,
bounded responses, loopback-only listeners, shutdown/restart, and no remaining
listener. The real desktop path separately proved a maximized visible Foglight
window, bundled-map/live-panel rendering, no external-browser fallback, normal
window close, and complete process/listener cleanup.

The independent package audit found and corrected missing offline listener
assertions/bounds, absent PE version resources, and a native minimum size that
could clip on scaled laptop displays. Both complete executable suites and the
native-window check passed again after the corrections. The protected signed
candidate workflow remains available to publishers who later obtain a trusted
certificate, but users never need one.

### Real-launch correction

User acceptance on 2026-07-12 exposed a release-wiring defect that the earlier
flag-controlled suites missed: the native launcher did not enable V2/Overview,
so normal users entered Standard, whose many simultaneous first refreshes could
all fail during a transient network window and remain on slow retry cadences.
The 100/100 claim was reopened rather than treating the shell as sufficient.

The corrected launcher enables V2/Overview with override-preserving defaults,
new scheduler profiles begin within five seconds, all scheduler and compatibility
HTTP work shares a six-request upstream gate, Standard starts refresh functions
in bounded batches, and it performs a cache-friendly early recovery pass.
Nested `URLError` causes are now logged by safe type (for example,
`URLError/TimeoutError`) without exposing exception text or secrets.

The final rebuilt no-flags executable passed the new `--require-live` gate with
50 incidents and two live keyless sources, then passed Standard compatibility,
first-offline, upgrade, corrupt-cache, corrupt-SQLite, outage, restart, Host,
token, and listener cleanup. The existing real user profile separately reached
50 incidents and seven live sources without resetting its settings. Its
maximized window closed through the normal title-bar path in 3.22 seconds, with
all Foglight processes exited and the listener released. This live
gate is now mandatory in the release checklist and protected signed-candidate
workflow; deterministic CI still avoids treating third-party availability as a
unit-test invariant.

The final browser self-audit also isolated a Windows Playwright trace-finalization
stall: assertions passed, but routine `retain-on-failure` tracing could exceed
context teardown. Tracing now uses `on-first-retry`, retaining CI retry evidence
without recording every green case; all 52 cases then passed in one clean run.

## Compatibility Review

No V1 route is removed. Direct client/test/document searches show active
consumers for Standard panels, settings, RSS, Wikimedia, and the canonical V2
surface. Removal without a zero-consumer result would break a supported mode.

## Evidence Index

- Architecture and phases: `FOGLIGHT_V2_PLANNING_RESEARCH.md`,
  `FOGLIGHT_V2_EXECUTION_PLAN.md`
- Provider decisions: `DATA_SOURCES.md`, `CANONICAL_SOURCE_MAPPINGS.md`,
  `../config/provider_registry.v1.json`
- Priority/correlation: `CORRELATION_AND_PRIORITY.md`, ADR 0005
- Local API: `V2_API.md`
- Accessibility: `WCAG_2_2_REVIEW.md`
- Performance: `PERFORMANCE_BUDGETS.md`,
  `baselines/phase10-2026-07-11.json`
- Hardening: `PHASE10_HARDENING.md`, `../SECURITY.md`
- Packaging: `BUILD_WINDOWS.md`, `RELEASE_CHECKLIST.md`
- Map provenance: `MAP_ASSETS.md`
