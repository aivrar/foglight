# Foglight V2 Phased Execution Plan

Status: authoritative implementation tasklist
Companion research: `docs/FOGLIGHT_V2_PLANNING_RESEARCH.md`

## Objective

Deliver a zero-configuration Foglight release that answers:

> What changed, what matters now, where is it happening, and what evidence
> supports it?

Completion means the full phase list and the 100-point release scorecard pass.
“Mostly complete,” hidden provider errors, skipped packaged testing, or a visual
demo without contract tests do not qualify.

## External Prerequisites and Decisions

These items require explicit release decisions. They do not require Foglight
users to obtain accounts, keys, or certificates:

- Authenticode signing is optional publisher-side provenance hardening. The
  mandatory gate is a checksummed one-file executable that passes every
  packaged profile and accurately records whether it is signed or unsigned.
- Any hosted detailed basemap must receive a written terms/licensing decision.
  No decision is needed for the bundled Natural Earth core map.
- Expanding Open-Meteo beyond the existing contextual weather request requires
  a documented decision about intended commercial/non-commercial distribution.
- Optional keyed sources remain outside the score. A missing FIRMS key is never
  a blocker.

Record each decision in an Architecture Decision Record; do not bury it in a
commit message.

## Non-Negotiable Product Gates

- [x] A clean Windows profile can launch `Foglight.exe` and reach a useful
      Overview without entering a key, creating an account, or completing a
      wizard.
- [x] Every default source is keyless and has documented terms, attribution,
      polling limits, and failure behavior.
- [x] No UI component consumes an unnormalized provider payload.
- [x] Provider failure cannot prevent shell startup or erase still-valid cached
      incidents.
- [x] Priority is deterministic and explainable; the UI never presents it as a
      prediction or opaque intelligence score.
- [x] Observation, forecast, media, community, and official alert provenance is
      visible.
- [x] All user state and incident history remain local.
- [x] The map and core incident list work without a hosted tile server.
- [x] Current security boundaries and single-executable packaging remain intact.
- [x] WCAG 2.2 AA, performance budgets, source tests, browser tests, and packaged
      smoke tests pass without waivers.

## Target Module Boundaries

The exact filenames may change only through a recorded architecture decision.
The intended shape is:

```text
foglight_core/
  config.py
  models.py
  taxonomy.py
  storage.py
  scoring.py
  correlation.py
  scheduler.py
  source_health.py
  fetch.py
  api.py
  providers/
    base.py
    usgs.py
    nws.py
    eonet.py
    gdacs.py
    nhc.py
    tsunami.py
    relief.py
    media.py
    aviation_weather.py
    fema.py
    ndbc.py
    coops.py
    jpl.py
    signals.py
web/
  app.js
  core/
    api.js
    store.js
    time.js
    filters.js
    accessibility.js
  views/
    overview.js
    map.js
    incident-drawer.js
    timeline.js
    source-health.js
    settings.js
  vendor/
    leaflet/
  data/
    natural-earth/
tests/
  fixtures/providers/
  unit/
  integration/
  browser/
```

`foglight_server.py` remains a compatibility entrypoint while routing and
provider logic move behind tested modules. `web/app.js` becomes a small boot
orchestrator after view extraction.

## V2 Local API Contract

All responses include `schema_version`, `generated_at`, and a monotonic local
`revision`. List endpoints enforce a maximum limit and validated filters.

Planned endpoints:

```text
GET /api/v2/bootstrap
GET /api/v2/incidents
GET /api/v2/incidents/{incident_id}
GET /api/v2/changes?cursor={n}
GET /api/v2/timeline
GET /api/v2/source-health
GET /api/v2/taxonomy
```

`bootstrap` supplies the minimum first-paint package: app settings safe for the
browser, current summary counts, top incidents, taxonomy, source-health summary,
and revision cursor. Large geometries and full timelines remain lazy-loaded.

V1 endpoints remain until Phase 11 removal review. Their existence must not
cause duplicate upstream polling after scheduler migration.

## Execution Rules

1. Execute phases in order. A phase starts only when its dependencies and the
   previous phase gate pass.
2. Keep the application runnable at every merge boundary.
3. Add or update tests before changing behavior.
4. Use sanitized provider fixtures in CI. Never make ordinary CI depend on live
   third-party responses.
5. A new provider is incomplete without registry metadata, adapter tests,
   attribution, terms, rate policy, health reporting, and failure behavior.
6. A new view is incomplete without loading, empty, partial, stale, error,
   keyboard, reduced-motion, and narrow-window states.
7. Record evidence for every gate in the phase checklist or release notes.
8. Do not add a framework merely to reorganize code. Prefer browser-native
   modules, Python standard-library facilities, and pinned development tools.
9. Do not remove a working V1 path until the V2 replacement has parity tests.
10. Update both planning documents if provider terms or architectural facts
    change during execution.

## Phase 0 — Baseline, Contracts, and Licensing

Dependencies: none.

### Tasks

- [x] Record baseline startup time, first shell paint, first incident paint,
      memory, executable size, API latency, map render time, and current source
      availability using a repeatable script.
- [x] Capture deterministic screenshots at 1500×950, 1280×800, 900×900, and
      520×900 using fixture data.
- [x] Add sanitized fixtures for every current provider, including empty and
      malformed examples.
- [x] Add provider-contract tests around current parsers before extraction.
- [x] Add branch coverage reporting. New `foglight_core` and pure JavaScript
      core modules target at least 90% branch coverage; scoring, correlation,
      migration, and security-boundary rule modules require complete coverage
      of documented rule branches.
- [x] Add `package.json` with pinned Playwright development tooling and lockfile;
      do not add Node to the runtime artifact.
- [x] Add browser smoke tests for startup, settings, mode switching, map/list
      selection, and error states.
- [x] Add automated accessibility-tree snapshots for primary views.
- [x] Add an opt-in live-source diagnostic command that never runs as a required
      CI gate.
- [x] Inventory terms, attribution, rate limits, and contact requirements for
      every current source.
- [x] Replace the universal CARTO assumption in planning and release docs.
- [x] Define data taxonomy, provider tier, retention, and UI lane tables as
      versioned fixtures.
- [x] Write Architecture Decision Records for SQLite, V2 compatibility, bundled
      base map, server-side scheduler, and priority semantics.

### Gate

- [x] Existing 16 tests still pass.
- [x] Every current provider has at least one fixture/parser test or is explicitly
      classified for retirement.
- [x] Browser baseline is reproducible without live internet data.
- [x] No unresolved mandatory-source licensing ambiguity remains.
- [x] Performance budgets are recorded using baseline evidence, not guesses.
- [x] Coverage reports identify the remaining untested legacy branches and the
      mandatory coverage floor for each extracted module.

### Phase 0 completion evidence — 2026-07-10

- Smoke: 68 Python tests passed with branch coverage reporting; Ruff, JavaScript
  syntax, one JavaScript contract test, and all eight browser scenarios passed.
  The browser scenarios were split across interaction/accessibility/visual runs
  only to stay within the command runner's per-call limit.
- Determinism: all external browser traffic is fulfilled from local fixtures or
  aborted; four committed viewport snapshots reproduced without changes.
- Direct self-audit: registry IDs, fixture IDs, and live-diagnostic IDs map 1:1
  across 25 providers; all machine-readable files parse; every required source
  is approved; conditional sources remain optional; package data excludes Node;
  generated coverage/test artifacts are ignored; no test server process remains.
- Defects found during the independent audit and corrected before closure:
  namespaced Atom entries were skipped, first-run USGS failure stayed on a
  loading placeholder, the alert close button had no useful accessible name,
  and the upstream User-Agent did not provide a viable contact URL.
- The in-app browser-control target was unavailable in this session. It was not
  replaced with another control backend; repository-owned Playwright Chromium
  provides the reproducible Phase 0 browser gate, while a native/manual pass
  remains part of the later packaged-release phase.

Rollback: test and planning infrastructure can be removed independently. The
four isolated runtime corrections are each regression-tested and can be
reverted separately only by updating the affected V1 contract evidence.

## Phase 1 — Behavior-Preserving Modularization

Dependencies: Phase 0.

### Tasks

- [x] Create `foglight_core` and extract settings, fetch/cache, parsing, and HTTP
      helpers without changing endpoint payloads.
- [x] Extract current providers behind a common `ProviderAdapter` interface.
- [x] Extract browser helpers, API access, time formatting, and settings into ES
      modules while preserving the current UI.
- [x] Replace shared implicit globals with an explicit application store.
- [x] Add import-boundary and circular-dependency tests.
- [x] Update PyInstaller collection and packaged smoke tests for new modules.
- [x] Keep compatibility functions in `foglight_server.py` only where current
      tests or routes require them.

### Gate

- [x] V1 contract fixtures are byte-equivalent or semantically equivalent where
      ordering is documented as irrelevant.
- [x] Current browser screenshots and interactions show no unapproved change.
- [x] Source and packaged test suites pass.
- [x] `foglight_server.py` and `web/app.js` are orchestration layers rather than
      provider/view monoliths.

### Phase 1 completion evidence — 2026-07-10

- Smoke: 88 Python tests, seven JavaScript unit tests, Ruff, syntax checks,
  interaction/error browser scenarios, four unchanged visual snapshots, and
  accessibility-tree checks passed. `foglight_core` reports 92% combined
  branch coverage; pure ES modules report 97.1% branch coverage.
- Packaging: PyInstaller's dependency graph includes the extracted settings,
  fetch, and provider modules. Rebuilt `Foglight.exe` served all seven ES module
  assets, passed authenticated settings smoke, and matched its manifest at
  SHA-256 `9f130743fcc9e1bbefea69bd5c76ca2c2c36b054cfd5db715ed81f4b7916fb47`.
- Direct self-audit: all 25 versioned provider IDs map registry → adapter →
  injected fetch runtime; every ordinary provider route uses the registry;
  derived HN-item and conflict-hotspot routes retain explicit compatibility
  paths. Python and browser dependency graphs are acyclic and core modules do
  not import lifecycle layers. Shared UI/user/lifecycle state is owned by the
  explicit store, while map and renderer caches remain module-private.
- Audit correction: the first extraction left hazard adapters using a separate
  RSS-size default. The configuration path now propagates the server limit into
  both provider modules and is regression-tested. The frontend security
  contract now inspects the complete shipped module graph instead of only the
  old single file.

Rollback: the V1 routes and names remain compatibility boundaries. Module
wiring can be reverted without changing endpoint payload contracts or user
state formats.

## Phase 2 — Canonical Observation Model and Storage

Dependencies: Phase 1.

### Tasks

- [x] Implement versioned Observation and Incident dataclasses with strict
      serialization and validation.
- [x] Implement the controlled event taxonomy and CAP-compatible severity,
      urgency, and certainty enums.
- [x] Normalize all timestamps to RFC 3339 UTC while preserving missing values.
- [x] Validate RFC 7946 geometry, coordinate ranges, bounding boxes, and
      centroids.
- [x] Implement deterministic observation IDs and content hashes.
- [x] Create SQLite migrations for providers, observations, incidents,
      incident-observation links, relations, revisions, and source health.
- [x] Enable WAL, foreign keys, bounded busy timeout, and explicit transaction
      behavior.
- [x] Detect R*Tree support at startup and provide a tested indexed fallback.
- [x] Implement retention and size caps with dry-run/reporting support.
- [x] Add migration recovery, corrupt-database quarantine, and clean-rebuild
      behavior without touching settings.

### Gate

- [x] Round-trip schema tests cover every field and enum.
- [x] Invalid timestamps, coordinates, units, IDs, and oversized text fail
      safely.
- [x] Concurrent read/write, restart, migration, retention, and corruption tests
      pass.
- [x] A synthetic 10,000-observation database meets the Phase 0 query budget.
- [x] Database growth remains within the declared retention cap.

### Phase 2 completion evidence — 2026-07-10

- Smoke after audit corrections: Ruff passed; all 119 Python tests passed at
  84% aggregate branch coverage, with both `models.py` and `storage.py` at 95%;
  seven JavaScript tests passed at 97.1% branch coverage; and all eight browser
  interaction, accessibility, error-state, and four-viewport visual scenarios
  passed without snapshot changes.
- Storage evidence: every documented table, foreign key, and index is present;
  connection-local foreign keys, WAL, busy timeout, rollback, restart, failed
  migration, future-schema rejection, and corrupt-file quarantine are tested.
  R*Tree and fallback queries are parity-tested, including fallback-to-R*Tree
  backfill. The fallback index returned a bounded result from 10,000 synthetic
  observations within the 50 ms Phase 0 query budget.
- Retention evidence: age, count, dry-run, and forced physical size overflow
  paths are tested. Size eviction compacts and removes oldest records until the
  declared cap is met, removes corresponding spatial rows and evidence-less
  incident history, and reports whether the cap was satisfied.
- Independent source audit mapped taxonomy → canonical serialization → SQL
  columns → spatial indexes → retention/recovery. It found and corrected stale
  R*Tree rows on deletion, missing R*Tree backfill after fallback operation,
  an unlocked size-eviction path, over-broad RFC 3339 and GeoJSON acceptance,
  raw-wire fingerprints incorrectly influencing canonical content hashes, and
  silent incident revision collisions. Each correction has a regression test.
- Structural recheck confirms exact taxonomy parity between the versioned JSON
  fixture and `EventKind`, clean SQLite `foreign_key_check`, all required
  tables/indexes, no diff whitespace errors, and no leftover browser/server
  test process.

Rollback: feature flag keeps V2 storage unused; V1 file cache remains active.

## Phase 3 — Current Core Provider Adapters

Dependencies: Phase 2.

Migrate operational sources before media and signal sources.

### Tasks

- [x] USGS earthquake adapter: stable IDs, magnitude, tsunami flag, geometry,
      status, update time, and detail URL.
- [x] NWS CAP adapter: severity, urgency, certainty, status, effective/expiry,
      instructions, areas, and polygon geometry.
- [x] NHC cyclone adapter: active/no-active shapes, storm identity, location,
      intensity metrics, and update time.
- [x] Tsunami adapter: bulletin identity, cancellation semantics, area, event
      time, and relation candidates.
- [x] GDACS adapter: documented GeoJSON API, alert level, event type, geometry,
      population/exposure metrics where present, and source links.
- [x] EONET V3 adapter: event ID, category, multi-date geometry, source list,
      status, and magnitude.
- [x] Smithsonian volcano and NOAA SWPC adapters with explicit observation vs
      outlook semantics.
- [x] ReliefWeb and humanitarian adapter with media/institutional provenance.
- [x] Keep raw provider bodies only in the existing bounded cache; persist
      normalized records and hashes, not unlimited raw copies.
- [x] Add adapter drift diagnostics that identify missing/renamed fields without
      logging full payloads or secrets.

### Gate

- [x] Golden fixture tests cover normal, empty, partial, malformed, and future
      unknown fields for each adapter.
- [x] Every normalized field has a documented source mapping.
- [x] No adapter invents certainty, coordinates, or event time.
- [x] Existing core panels can render from normalized fixtures with parity.

### Phase 3 completion evidence — 2026-07-10

- Smoke after audit corrections: Ruff passed; all 154 Python tests passed at
  86% aggregate branch coverage, with the canonical adapter module at 94%;
  seven JavaScript tests passed at 97.1% branch coverage; and all eight browser
  interaction, accessibility, error-state, and four-viewport visual scenarios
  passed without snapshot changes.
- Contract evidence: nine core providers have normal, empty, partial,
  malformed, and future-field golden cases. Canonical results round-trip into
  SQLite without raw bodies, and compatibility projections reproduce the
  existing panel payload shapes with JSON-safe values.
- Current-source evidence: official documentation was rechecked for USGS, NWS
  CAP, NHC, tsunami.gov, GDACS GeoJSON, EONET V3, SWPC, and ReliefWeb. An opt-in
  live run normalized USGS, NWS, NHC, both NOAA hazard formats, GDACS, EONET,
  Smithsonian, SWPC, and ReliefWeb. It caught and corrected SWPC's live
  object-row shape while retaining support for NOAA's documented header-row
  sample format.
- Direct self-audit mapped provider registry/auth → source URL → normalizer →
  canonical hash → SQLite → V1 projection. All nine adapters remain `auth=none`;
  all non-CAP providers preserve unknown certainty; timestamps and coordinates
  trace only to documented fields; raw payload values never enter drift output.
- Audit defects corrected with regressions: malformed USGS geometry no longer
  escapes batch isolation; invalid optional URLs no longer discard valid
  observations; tsunami center/series identity derives from live bulletin links
  when Atom IDs are UUIDs; GDACS flood/drought event codes survive projection;
  live GDACS nested URL/severity/country fields and offset-less source clocks are
  normalized explicitly. No test server process or diff whitespace error remains.

Rollback: each provider can individually fall back to its V1 adapter.

## Phase 4 — Correlation, Priority, Change Tracking, and Scheduler

Dependencies: Phase 3.

### Tasks

- [x] Implement exact-ID update handling and revision generation.
- [x] Implement category-specific candidate correlation with versioned rules.
- [x] Implement explicit cross-kind relations instead of unsafe merges.
- [x] Implement coverage-cluster title normalization and similarity tests for
      media only.
- [x] Implement explainable priority components and lane separation.
- [x] Implement change types: new, updated, escalated, downgraded, resolved,
      cancelled, and source-lost.
- [x] Implement the provider registry and bounded scheduler.
- [x] Add conditional request support, exponential backoff with jitter,
      `Retry-After`, one in-flight request per provider, and circuit breakers.
- [x] Persist source health and expose summary/detail endpoints.
- [x] Implement V2 bootstrap, incidents, incident detail, changes, timeline,
      taxonomy, and source-health APIs.
- [x] Add revision cursors so browser polling transfers only changes.
- [x] Prevent the V1 browser compatibility routes from triggering duplicate
      upstream requests once a provider is scheduler-managed.

### Gate

- [x] Correlation fixtures prove intended merges and intended non-merges.
- [x] Score fixtures prove stable results across restarts and rule versions.
- [x] A media spike alone cannot produce an observed/high-certainty incident.
- [x] Provider 429, timeout, malformed body, stale cache, system clock shift, and
      restart simulations pass.
- [x] Scheduler concurrency and every provider interval stay within registry
      limits.
- [x] V2 APIs meet pagination, query validation, body-size, and latency budgets.

### Phase 4 completion evidence — 2026-07-10

- Post-audit smoke: Ruff and all 178 Python tests passed at 89% aggregate branch
  coverage; correlation reports 97%, scheduler 96%, scoring 100%, service 99%,
  and storage 95%. Seven JavaScript tests passed at 97.1% branch coverage, and
  all eight deterministic Playwright interaction, accessibility, error-state,
  and four-viewport visual scenarios passed without snapshot changes.
- Scheduler/API evidence: all nine current canonical keyless adapters inherit
  their cadence and bounds from the 25-provider registry. Simulations cover
  304 validators, stale data, 429 and bounded `Retry-After`, timeouts, malformed
  payloads, clock rollback, persisted restart, real jitter, circuit opening,
  source loss, worker bounds, and one in-flight job per provider. A 5,000-row
  indexed incident page and every local V2 HTTP route remain within the recorded
  50 ms and response-size budgets. The global AUTOINCREMENT change cursor is
  used instead of per-incident revision numbers because revisions are not a
  globally monotonic polling token.
- Package evidence: the rebuilt 17.94 MiB `Foglight.exe` passed clean-profile V1
  startup, CSP, session authorization, invalid-Host rejection, and settings
  mutation smoke, plus isolated V2 bootstrap, bundled registry/taxonomy,
  25-source health, and SQLite creation smoke. Its manifest matches SHA-256
  `772f5bd1dfcb5e52bcf30dd814ade9aa77ce90475d5fd680930e196c8ff0503b`.
- Independent source audit mapped provider registry → scheduler → conditional
  fetch → canonical adapter → serialized correlation → scoring → SQLite
  incident/revision/change log → service → V2 and V1 compatibility routes.
  It found and corrected the top-1,000 correlation blind spot, concurrent
  revision races, unbounded media-age relations, partially committed multi-URL
  validators, constant rather than real jitter, unbounded HTTP-date retries,
  malformed persisted scheduler state, backward incident revisions, stale SQL
  kind columns, terminal-incident outage churn, and multi-source loss/recovery
  miscounting. Each defect has a regression test, including a 1,001-incident
  candidate fixture and simultaneous provider ingestion.
- Structural closure: versioned JSON rules parse and equal their code contracts;
  migration 4 passes SQLite `quick_check` and `foreign_key_check`; import and
  frontend boundary suites pass; diff whitespace is clean; package smoke uses
  disposable state; and no Foglight or test listener remains.

Rollback: stop scheduler and use V1 on-demand fetching; V2 database is additive.

## Phase 5 — Overview UI and Display Modes

Dependencies: Phase 4.

### Tasks

- [x] Build Overview as the default candidate behind an internal feature flag.
- [x] Add a “Now” list of five to eight priority incidents with kind, severity,
      age, location, provenance, change, and priority explanation.
- [x] Add Global, Natural Hazards, Severe Weather, Conflict/Humanitarian,
      Aviation/Marine, and Signals filters.
- [x] Add Overview, Standard, and Command density modes.
- [x] Preserve the current dense dashboard as Standard until V2 parity is
      accepted.
- [x] Add a global “What changed?” summary from the revision cursor.
- [x] Add persistent but unobtrusive source-health/freshness status.
- [x] Implement loading, empty, partial, stale, offline, and first-run states.
- [x] Use category icon/shape plus text; reserve color primarily for severity.
- [x] Implement full keyboard navigation, focus management, live regions, and
      reduced-motion styles.
- [x] Ensure every incident is usable without interacting with the map.

### Gate

- [x] Fixture-driven browser tests cover every view state and mode.
- [x] Accessibility-tree snapshots, keyboard-only flows, contrast, target size,
      zoom/reflow, and reduced-motion checks pass WCAG 2.2 AA criteria.
- [x] No critical information depends only on color, animation, hover, sound, or
      map position.
- [x] Overview first paint meets its measured budget with 1,000 fixture
      incidents.
- [x] Standard mode retains current core functionality.

### Phase 5 completion evidence — 2026-07-10

- Post-audit smoke: Ruff and all 179 Python tests passed at 89% aggregate branch
  coverage; 12 JavaScript tests passed with 100% line/94.59% branch coverage
  across the extracted pure modules, including 100% line/92.11% branch coverage
  for the Overview model. All 22 deterministic Playwright scenarios passed.
- UI evidence: fixture-driven browser coverage exercises loading, empty,
  partial, stale, offline, first-run, and ready states; Overview, Standard, and
  Command modes; all six filters; revision-change summaries; refreshed source
  health; priority expansion; human-readable provenance; successful and failed
  first-run persistence; and paginated keyboard access to incidents beyond the
  eight-item Now list. Overview does not issue any V1 data request until the
  user explicitly selects Standard.
- Accessibility/performance evidence: the complete Overview axe scan has no
  critical or serious violation; accessibility-tree, keyboard-only filter/card
  flows, settings focus return/trap, visible focus, 24 px target minimum,
  200% zoom/reflow, reduced motion, and non-color kind/severity/status checks
  pass. A 1,000-incident fixture paints the first bounded Now list inside the
  2,000 ms budget. Reviewed desktop and narrow-screen Overview snapshots pass,
  alongside all four unchanged Standard snapshots.
- Independent source audit mapped internal environment flag → local app-config
  endpoint → sanitized persisted mode → lazy Standard startup → V2 bootstrap →
  change/source-health polling → filters/Now/catalog → settings focus and live
  regions. It found and corrected an initial health-render reference error,
  legacy status badges leaking into Overview, unclear CSS category shapes,
  missing `unknown` filter routing, frozen source health, cached data labeled
  current, premature first-run completion, internal provider IDs shown as
  provenance, and the lack of a non-map path to paginated incidents. Each
  correction has a unit or rendered regression.
- Package evidence: clean-profile rollback keeps Overview and V2 disabled by
  default. With both internal flags set, the rebuilt executable reports V2 and
  Overview available, defaults clean settings to Overview, serves all three
  bundled Overview assets, loads 25-source bootstrap health, and creates its
  local database. The 18,822,445-byte executable matches SHA-256
  `83673bb952023b5b101091500714a3b0230669a7f902aa391def86124944f381`.
- Structural closure: taxonomy-to-filter/presentation parity, import/frontend
  boundaries, JavaScript syntax, diff whitespace, checksum, and two committed
  Overview snapshots pass; no Foglight process or test listener remains. The
  in-app browser target was unavailable after the prescribed connection audit,
  so repository-owned Playwright Chromium remains the deterministic rendered
  gate documented by the plan.

Rollback: internal flag returns startup to Standard mode.

## Phase 6 — Map V2 and Offline Base

Dependencies: Phase 5.

### Tasks

- [x] Vendor reviewed Leaflet assets locally and remove runtime CDN dependency.
- [x] Add simplified Natural Earth world boundaries as the default base.
- [x] Document dataset origin, version, simplification process, checksum, and
      public-domain terms.
- [x] Use Canvas rendering for dense vectors where it preserves interaction.
- [x] Implement deterministic viewport/grid clustering without a hosted service.
- [x] Add zoom-dependent detail and marker decluttering.
- [x] Add layer/filter controls consistent with Overview categories.
- [x] Link map selection bidirectionally with the Now list and incident drawer.
- [x] Render forecast/uncertainty geometry distinctly from observed geometry.
- [x] Fade old incidents and pulse only new/escalating incidents; disable pulses
      under reduced motion.
- [x] Add keyboard alternatives for map-only actions and a coordinate/pin form
      that does not require dragging.
- [x] Add graceful tile/provider failure behavior; optional detailed tiles must
      not obscure attribution or block the bundled base.

### Gate

- [x] Map opens and remains useful with all network tile requests blocked.
- [x] 5,000 mixed fixture geometries meet pan, zoom, selection, and memory
      budgets.
- [x] Antimeridian, poles, invalid geometry, huge polygons, and overlapping
      events have regression tests.
- [x] Map/list selection and all filters are keyboard-testable.
- [x] CSP no longer requires `unpkg.com` or CARTO for core operation.

Completion evidence (2026-07-10 local / 2026-07-11 UTC): Leaflet 1.9.4 is
vendored with file-by-file SHA-256 tests and its BSD-2-Clause license. The
pinned Natural Earth v5.1.1 source deterministically produces a 177-feature,
202,773-byte public-domain asset with SHA-256
`b853e8ab6412d655dbe2fe8719d7cfde24e266db347eeb694b4df0f627a2fdb8`;
`docs/MAP_ASSETS.md` records provenance and transformation details. Both
Standard and Overview render that base locally, while optional OpenStreetMap
tiles are explicit, attributed, and failure-isolated.

The initial exhaustive smoke passed 183 Python tests, 20 JavaScript tests, and
25 Playwright scenarios before the independent audit. The required direct
source audit then traced asset serving, packaging, Canvas/layer order,
clustering, geometry, selection, filtering, pins, optional tiles, CSP, and both
display-mode lifecycles. It found and fixed strict longitude validation,
geometry depth bounds, antimeridian long-path rendering, polygon-over-marker
paint order, saved-pin stacking, stale selection
feedback, pin/server limit mismatches, cluster age treatment,
and missing packaged-asset CI assertions. The post-audit gate passed Ruff, 184
Python tests at 89% aggregate coverage, 21 JavaScript tests (100% lines / 93.02%
branches overall; map model 100% lines / 91.90% branches), and all 29 Playwright
scenarios including offline/base/tile failure, optional-tile success,
accessibility, reduced motion, 5,000 incidents, memory/DOM/Canvas bounds,
pan/zoom/selection, reflow, and six visual baselines.

Post-audit performance remained inside every documented budget: 13.104 ms
local startup, 24.844 ms worst local-route p95, 202.98 ms shell paint, 363.764
ms offline-map paint, 377.072 ms first incident, 10,000,000-byte browser heap,
and 33,619,968-byte server RSS. The rebuilt unsigned Windows artifact is
18,957,010 bytes with SHA-256
`919fb713b63894857dac844bb350641b417869001ca78801d56725587bcead1b`.
Packaged default and Overview+V2 launches both served the exact local Leaflet
and world assets, returned a CDN-free CSP, met their feature-flag contracts,
and left no process or listener behind. The prescribed in-app browser target
remained unavailable, so the repository-owned Playwright Chromium gate was
used as the deterministic rendered fallback.

Rollback: V1 map remains selectable until browser and packaged gates pass.

## Phase 7 — Incident Drawer, Timeline, and “What Changed”

Dependencies: Phases 5 and 6.

### Tasks

- [x] Build incident drawer with facts, times, location, metrics, sources,
      related incidents, priority breakdown, and source health.
- [x] Label every item as observation, warning, forecast, media coverage,
      community signal, or market/internet signal.
- [x] Build a 24-hour timeline with accessible list equivalent.
- [x] Add time windows for 1 hour, 6 hours, 24 hours, and 7 days.
- [x] Add timeline scrubbing without destructive state changes.
- [x] Display new/escalated/downgraded/resolved sequences from persisted
      revisions.
- [x] Provide source-safe external links and copyable deterministic summaries.
- [x] Update printable briefing generation to use incident revisions and
      provenance.

### Gate

- [x] Known multi-step fixture incidents reproduce the exact expected sequence.
- [x] Timeline and drawer remain usable without map or animation.
- [x] Source links, timestamps, units, cancellations, and expired states are
      correct in browser tests.
- [x] Briefing contains no unescaped provider content and no unsupported claim.

Completion evidence (2026-07-10 local / 2026-07-11 UTC): the lazy incident
drawer presents current facts, explicit UTC times, location, score components,
bounded structured metrics, deduplicated evidence, related incidents, and
per-provider health. Main, related, and observation records use semantic
provenance labels. Chronological immutable revisions retain full previous-state
context across 1-hour, 6-hour, 24-hour, and 7-day windows; the range preview and
accessible list never replace the live incident. Detail, timeline, optional
collections, health, and relation failures are independently bounded and
degrade to current facts or the compact card.

The initial exhaustive smoke passed Ruff, 184 Python tests at 89% aggregate
coverage, 31 JavaScript tests at 100% lines / 94.05% branches overall, and all
32 Playwright scenarios. The required independent source audit then traced
catalog/map selection -> lazy detail/timeline APIs -> chronology/provenance ->
enrichment -> copy/print -> modal focus -> display-mode rollback -> packaged
assets. It found and corrected a dynamic-ID contract omission, wall-clock-based
visual nondeterminism, future timeline inclusion, a first-visible revision
incorrectly labeled as the initial record, a queued map-selection drawer race,
whole-drawer failure on malformed optional collections, whole-drawer failure
when only the timeline was unavailable, credential-bearing evidence links,
empty Standard briefing fallback from Overview, incomplete reverse-tab focus
containment, undersized drawer metadata/slider targets, redundant ended-state
copy, and newline field spoofing in deterministic summaries. Every correction
has a focused regression.

The complete post-audit gate passes Ruff, 184 Python tests, 31 JavaScript tests
(100% lines / 94.09% branches overall; incident model 100% lines / 96.95%
branches), and all 35 deterministic Playwright scenarios with a suite-wide
uncaught-page-error assertion. Browser evidence covers the exact
new -> escalated -> downgraded -> resolved sequence, prior-revision comparisons
across filtered windows, cancellations, expiry, units, safe links, escaped
print content, clipboard output, popup controls, detail/timeline/malformed
failure isolation, list-only use, mode-switch race cancellation, axe, keyboard
focus containment, 24 px targets, 200% drawer reflow, reduced motion, and
reviewed desktop/mobile drawer baselines alongside every existing visual.

Post-audit performance remains within every applicable budget: 16.375 ms local
startup, 4.109 ms worst local-route p95, 253.698 ms shell paint, 462.749 ms map
paint, 466.78 ms first incident, 10,000,000-byte browser heap, and
33,431,552-byte server RSS. The rebuilt unsigned Windows artifact is 18,967,946
bytes with SHA-256
`a9e8778ccf85fcabbe4bf427fc3cc6f1deb7707b5d7f1807abedfed802c686f5`.
At this phase's rollback gate, packaged default mode kept Overview/V2 disabled;
the final native release switches them on by default. Packaged Overview+V2 serves
all drawer assets, the 16-category taxonomy, and 25 source-health rows. Its
checksum matches and no Foglight process or listener remains. The prescribed
in-app browser target remained unavailable, so repository-owned Playwright
Chromium was the deterministic rendered fallback.

Rollback: Overview cards retain a compact details section if drawer is disabled.

## Phase 8 — Local Watch Regions, Notifications, Search, and Offline History

Dependencies: Phase 7.

Implementation order: schema/migration and pure models -> bounded local search
and revision metadata -> Overview controls and native permission/download seam
-> deterministic browser/packaged gates -> independent source audit -> complete
post-audit smoke. Each arrow is a stop point if its own tests are not clean.

### Tasks

- [x] Replace free-text-only watches with local watch regions and structured
      kind/severity thresholds; retain keyword migration.
- [x] Allow map click, coordinate entry, and named local label creation without
      geolocation or an external geocoder.
- [x] Add quiet hours, per-kind notification settings, deduplication, and
      acknowledge/snooze state.
- [x] Implement notifications only after explicit user action and verify
      WebView2 permission handling; provide in-app notifications as guaranteed
      fallback.
- [x] Add local incident search across bounded retained data.
- [x] Add CSV and GeoJSON export plus provenance-rich printable briefing.
- [x] Add an offline state that shows last successful revision, source ages, and
      cached incidents without suggesting they are live.
- [x] Add wall-display auto-cycle with pause, keyboard control, and reduced
      motion compliance.

### Gate

- [x] No notification appears before opt-in or during quiet hours.
- [x] Duplicate revisions do not produce duplicate notifications.
- [x] Watch-region matching handles polygons, boundaries, and dateline cases.
- [x] Exports validate against the canonical schema and escape spreadsheet
      formula prefixes where applicable.
- [x] A network-disabled packaged run starts and explains cached age accurately.

### Phase 8 completion evidence — 2026-07-11

The initial exhaustive smoke passed Ruff, all 187 Python tests, 41 pure
JavaScript tests, and all 42 rendered browser scenarios. The initial rebuilt
package also retained and rendered cached history through a dead upstream
proxy, supported local search, reported the failed refresh without relabeling
cached data as live, matched its checksum, and left no process or listener.

The mandatory independent source audit mapped settings validation and atomic
persistence -> legacy/structured watch normalization -> geometry and severity
matching -> revision-key deduplication -> serialized notification state ->
Overview polling -> map coordinate selection -> local FTS5 search -> canonical
CSV/GeoJSON/print output -> WebView2 permission handling -> packaged offline
rendering. It found and corrected coexistence loss between structured regions
and migrated keywords, formula injection in nominally numeric CSV cells,
interactive-control keyboard hijacking, lingering map-pick state, misleading
empty offline wording, concurrent notification/save races, request-size cap
drift, stale or missing place-name search connections, malformed-JSON upgrade
failure, and permissive default handling for non-notification WebView2 requests.
Every correction has a regression assertion, including migration 4 -> 6,
search-index insert/update/delete synchronization, duplicate concurrent change
delivery, persistence rollback, and explicit native permission allow/deny.

Post-audit smoke passed Ruff and all 190 Python tests at 88% aggregate
branch-aware coverage; correlation remains 97%, service 99%, storage 94%, and
the scheduler 96%. All 41 JavaScript tests passed at 100% line and 93.02%
branch coverage, and all 43 single-worker Playwright scenarios passed, including
notification opt-in/quiet/dedupe/fallback, map pick and cancellation, search and
export, save failure rollback, wall controls, offline history, axe/keyboard/
200% reflow, and reviewed desktop/mobile watch-center baselines. The isolated
5,000-incident API/search budget passed five consecutive runs.

Post-audit performance remained within every published budget: 13.101 ms local
startup, 25.075 ms worst local-route p95, 226.475 ms shell paint, 407.539 ms map
paint, 410.342 ms first incident, 10,000,000-byte browser heap, and
33,587,200-byte server RSS. The rebuilt unsigned one-file Windows artifact is
18,993,728 bytes with SHA-256
`b438b08bd3f4d2be5f83c15ce854d2dd903176a12a029eb721f144f7c45a5609`.
With upstream HTTP(S) forced through a dead proxy, the packaged rendered UI
showed `Cached local history — not live`, revision time, and a two-hour source
age; place-name search returned the retained incident, the blocked refresh was
recorded once, the manifest matched, and no Foglight process or listener
remained. The prescribed in-app browser target remained unavailable, so the
repository-owned Playwright Chromium fallback supplied deterministic rendered
evidence.

Rollback: structured watches migrate back to existing keyword/pin behavior
without deleting user data.

## Phase 9 — New Keyless Providers

Dependencies: Phases 4 and 7. Add one provider per independently reviewable
change; never merge this phase as one bulk integration.

### Phase 9A — NOAA Aviation Weather

- [x] Add SIGMET GeoJSON adapter, fixtures, registry metadata, and 5-minute
      schedule.
- [x] Preserve observed/forecast validity windows and hazard type.
- [x] Relate volcanic ash and severe weather advisories without merging kinds.
- [x] Make aviation hazards default; mark community aircraft positions
      experimental/off by default.

Gate: [x] documented limits respected; global no-data and 204 cases pass.

#### Phase 9A completion evidence — 2026-07-11

Working-tree reference: additive canonical V2 implementation in
`foglight_core/providers/canonical.py`, registry/taxonomy/rule revisions,
scheduler 204 handling, Overview/map/watch presentation, deterministic
fixtures, and the opt-in live diagnostic. The legacy provider catalog remains
separate; no fake V1 route was introduced.

The initial focused smoke passed 108 provider, scheduler, registry, and
correlation tests. The independent source audit then mapped registry → service
registration → scheduler job → bounded fetch → GeoJSON normalization →
canonical observation → distinct incident kind → conservative cross-kind
relation → scoring lane → Overview/map/drawer/watch/notification rendering →
attribution and live diagnostics. It found and corrected a stale backend watch
kind allow-list, a still-scheduled default community-aircraft poll, 204 error
freshness bypass, missing official-advisory provenance, incomplete docs and
credits, inverted validity-window acceptance, a hard-coded source-health test
count, and the live diagnostic's missing catalog/import connection. Each has a
regression or direct rendered assertion.

Post-audit smoke passed Ruff, all 196 Python tests at 88% aggregate
branch-aware coverage, all 41 JavaScript tests at 100% line and 93.04% branch
coverage, and all 44 single-worker Playwright scenarios. The rendered set
includes an official SIGMET in the default Aviation / marine view, absence of
any zero-config `/api/flights` request, full accessibility/reflow/error states,
5,000-map and 1,000-list budgets, and reviewed desktop/mobile Watch Center and
Standard baselines. The server left no listener on port 19876.

The opt-in live diagnostic at 2026-07-11T21:24:15Z returned HTTP 200 from NOAA
Aviation Weather in 925.7 ms, normalized 15 current SIGMET observations, and
reported no schema drift. The five-minute registry cadence remains five times
slower than the documented once-per-minute endpoint minimum and well below the
100-request/minute ceiling. Empty global GeoJSON and HTTP 204 are both tested
as successful zero-observation batches. Gate result: PASS. Phase 9B authorized:
yes.

### Phase 9B — OpenFEMA

- [x] Add recent disaster declaration adapter and 30-minute schedule.
- [x] Treat declaration as official administrative context, not event onset.
- [x] Relate declarations by geography/type/date with conservative rules.

Gate: [x] declaration updates do not falsely escalate real-time incident urgency.

#### Phase 9B completion evidence — 2026-07-11

Working-tree reference: `OpenFemaDeclarationAdapter`, the distinct
`disaster_declaration` taxonomy kind, 30-minute registry job, NWS UGC state
evidence, conservative declaration-relation rule, administrative drawer/list
presentation, fixtures, attribution, and live-diagnostic registration.

The current first-party contract was revalidated before coding: the official
dataset remains version 1, keyless/read-only, `R/PT20M`, and distinguishes
`declarationDate` from `incidentBeginDate`, `incidentEndDate`, and financial
closeout. The adapter accepts the documented historical/current aliases, keeps
event onset out of `event_at`, preserves incident dates as metrics, uses the
declaration date only as administrative effective time, and never infers CAP
severity, urgency, or certainty.

Initial focused smoke passed 134 Python and all 41 JavaScript tests plus the
rendered declaration scenario. The mandatory independent audit mapped OpenFEMA
registry/query -> bounded scheduler -> alias-aware normalization ->
administrative timestamps/status -> explicit UGC state and county evidence ->
incident-type/date-window match -> directed relation on the declaration only ->
unchanged real-time incident -> Overview/drawer/watch/attribution. It found and
corrected missing type validation, reverse-ingest gaps for earthquake/tsunami,
insufficient update/wrong-state/out-of-window assertions, and browser fixtures
that fabricated Immediate urgency and point geometry for null administrative
records. It also verified declarations never cross-kind merge and that
unsupported geography/type combinations remain unrelated.

Post-audit smoke passed Ruff, all 201 Python tests at 88% aggregate
branch-aware coverage, all 41 JavaScript tests at 100% line and 93.05% branch
coverage, and all 45 single-worker Playwright scenarios. A declaration refresh
retains Unknown urgency and its relation while the related weather incident's
revision, score, and Expected urgency remain byte-for-byte unchanged. Rendered
evidence labels the record `Administrative declaration`, shows Unknown /
Unknown rather than emergency semantics, preserves the no-location state, and
passes the full accessibility/reflow/performance/error-state and reviewed
visual matrix. No listener remained on port 19876.

A bounded live check on 2026-07-11 returned an HTTP error in 4314.2 ms while
FEMA's public site displayed a technical-difficulties page. This is external
availability rather than schema evidence: scheduler error/backoff/circuit
behavior is already deterministic and the official checked-in contract
fixture passes normal, empty, partial, malformed, alias, and future-field
cases. Gate result: PASS. Phase 9C authorized: yes.

### Phase 9C — NOAA NDBC and CO-OPS

- [x] Add NDBC GeoRSS/station observation adapter and CO-OPS water-level adapter.
- [x] Query only around active coastal incidents and saved watch regions.
- [x] Preserve units and preliminary/verified quality flags.
- [x] Compute anomalies only when an explicit source baseline/prediction exists;
      never infer one from a single value.

Gate: [x] query count is bounded by active contexts and respects five-minute
data cadence.

#### Phase 9C completion evidence — 2026-07-11

Working-tree reference: `NdbcObservationAdapter`,
`CoopsWaterLevelAdapter`, the bounded `CoastalContextPlanner`, the checked-in
301-station CO-OPS catalog and opt-in refresh script, contextual scheduler URL
contracts, distinct `marine_observation` / `water_level` taxonomy kinds, and
Overview/map/drawer/watch/attribution presentation.

The first-party contract was revalidated before and during implementation.
NDBC documents five-minute latest-observation generation and warns that broad
nearby queries can exceed 500 KiB. CO-OPS documents `date=latest` as the last
point available within 18 minutes, requires a datum for water levels, defines
MLLW for U.S. coastal waters, and publishes `p` / `v` quality plus data flags.
Foglight makes no global request: zero local context produces `idle` source
health and no network call. Saved geometric watches are considered before
independent active coastal incidents, contexts within 50 km are deduplicated,
and each provider is capped at six HTTPS requests every 300 seconds. Great
Lakes stations are excluded because their IGLD/LWD datum contract differs.

Initial smoke passed Ruff, 220 Python tests at 88% aggregate branch-aware
coverage, 41 JavaScript tests at 100% line / 93.07% branch coverage, and 46
Playwright scenarios after direct review and update of only the two Watch
Center baselines changed by the new category checkboxes.

The mandatory independent source audit mapped bundled station catalog and
settings → coastal context derivation → trusted dynamic URL validation →
atomic multi-URL scheduler batch → canonical station observation → stable
station incident revision → mobility filter/map/drawer metrics → watch and
notification settings → provider attribution/docs. It found and corrected
minute-precision CO-OPS UTC parsing, query-relative NDBC summaries and feed
generation times causing false revisions, unstable per-sample incident
identity, implicit CO-OPS response-order reliance, stale conditional validators
while idle, invalid-coordinate acceptance, Great Lakes datum mismatch, and a
self-sustaining polling loop where contextual measurement incidents could
become their own future context. It also added damaged-catalog startup
isolation, refreshed canonical field mappings, and removed a stale README
`defense/cyber` description inconsistent with the actual non-cyber product.

Post-audit smoke passed Ruff, all 221 Python tests at 88% aggregate
branch-aware coverage, all 41 JavaScript tests at 100% line / 93.07% branch
coverage, and all 46 single-worker Playwright scenarios. Browser evidence
shows NDBC and CO-OPS together in Aviation / marine, explicit `Source
measurement` provenance, preserved knots/feet/meters and preliminary QA, and
full accessibility/reflow/offline/error/performance coverage. Scheduler tests
prove idle/no-fetch, exact-host HTTPS enforcement, six-query caps, atomic
failure, duplicate suppression, validator pruning, and five-minute intervals.
No listener remained on port 19876.

Same-day bounded first-party probes previously returned an NDBC RSS station
sample and a CO-OPS San Francisco JSON sample with `t`, `v`, `s`, `f`, and `q`.
The final opt-in diagnostic attempt reached neither NOAA host from the test
environment and returned `URLError` after roughly 12 seconds per request. This
is treated as external reachability, not waived schema evidence: official
fixtures cover normal, empty, partial, malformed, future-field, wider
out-of-order, quality, unit, and explicit-prediction cases, while scheduler
timeout/backoff/circuit isolation is deterministic. Gate result: PASS. Phase
9D authorized: yes.

### Phase 9D — NASA/JPL Fireballs

- [x] Add one-at-a-time adapter and six-hour schedule.
- [x] Validate API signature version and field list before parsing.
- [x] Present as a low-frequency space signal with optional location.

Gate: [x] no concurrency and no emergency classification.

#### Phase 9D completion evidence — 2026-07-11

Working-tree reference: `JplFireballAdapter`, the `fireball` science taxonomy
kind, one fixed `limit=20` source URL, 21,600-second registry cadence,
versioned fixtures, Signals/map/drawer/watch presentation, NASA/JPL CNEOS
attribution, and opt-in live-diagnostic registration.

First-party research revalidated API version 1.2 and the SSD fair-use rule of
one request at a time. The implementation has exactly one fireball URL and
inherits the scheduler's no-double-inflight provider invariant. It validates
signature version, bounded count, supported field-name sets, row count and row
width before positional mapping. Date, radiated energy, and impact energy are
required; peak-brightness location, altitude, and entry velocity remain
optional. Records use normalized peak-brightness time as correction-stable
identity, status `ended`, and Unknown severity/urgency/certainty. The UI labels
them source measurements and repeats CNEOS's limitations that reports are not
real-time and not every fireball is reported.

The first live probe exposed a documented/live contract mismatch rather than
silently passing it: live v1.2 encodes `count` as a decimal string and includes
the CNEOS-described `vel` field, while the API example uses a numeric count and
omits `vel`. The adapter explicitly supports only those two verified variants
and maps by validated field name. A subsequent probe returned HTTP 200 in
534.7 ms, normalized 20 records, and reported no drift.

Initial smoke passed Ruff, 233 Python tests at 88% aggregate branch-aware
coverage, 41 JavaScript tests at 100% line / 93.07% branch coverage, and 47
Playwright scenarios after direct review of the two Watch Center baselines
changed by the Fireball selector.

The mandatory independent source audit mapped registry/fair-use policy →
single scheduler URL → signature/count/field validation → correction-stable
canonical observation → optional point/altitude/velocity → completed science
signal score → Signals/map/drawer/watch/attribution/docs. It found and fixed
non-string field names that could raise instead of fail closed, contradictory
non-empty data hidden behind `count=0`, coercion of booleans/numbers despite
the documented string field contract, and raw timestamp formatting in record
identity. Regression cases cover wrong versions, unknown/unhashable/duplicate
field contracts, count mismatch/cap, partial location, invalid directions and
ranges, negative/non-string measurements, documented/live field variants,
reordering, source corrections, and missing location.

Post-audit smoke passed Ruff, all 238 Python tests at 88% aggregate
branch-aware coverage, all 41 JavaScript tests at 100% line / 93.07% branch
coverage, and all 47 single-worker Playwright scenarios. The rendered Fireball
route shows NASA/JPL attribution, source-measurement provenance, exact energy
units, optional-location support, ended lifecycle, and Unknown / Unknown rather
than emergency semantics. A final live probe returned HTTP 200 in 10,856 ms,
normalized 20 observations, and reported zero drift. No listener remained on
port 19876. Gate result: PASS. Phase 9E authorized: yes.

### Phase 9E — Conditional Sources

- [x] Run a fresh terms/rate review before adding GDELT or new Open-Meteo
      products.
- [x] GDELT, if retained, is supporting-tier, low-frequency, circuit-broken,
      and excluded from authoritative facts.
- [x] Additional Open-Meteo products require an explicit distribution/use
      decision compatible with Foglight's intended users.
- [x] Re-evaluate the new USGS Water Data API; do not implement against the
      retiring endpoint.

Gate: conditional sources can be disabled entirely with no missing Overview
capability or failing required test.

Phase 9E evidence (2026-07-11): the fresh primary-source review confirmed that
Open-Meteo's free API is restricted to non-commercial use and publishes free
tier request limits, while commercial use requires the customer API. Its
existing V1 click-weather compatibility path is therefore disabled by default
behind the explicit `FOGLIGHT_OPEN_METEO_ENABLED` opt-in; no additional
Open-Meteo product was added. GDELT's official material confirms hosted API
quotas without a dependable per-client allowance suitable for a required
zero-configuration source, so GDELT was not registered. USGS currently plans
to decommission WaterServices in early 2027 and recommends the modern OGC API,
whose migration guidance asks users making more than a few requests per hour
to obtain an API key. Neither the retiring nor modern USGS water API was added.
The exact source decisions and first-party references are recorded in
`FOGLIGHT_V2_PLANNING_RESEARCH.md` and `DATA_SOURCES.md`.

Implementation smoke initially passed Ruff, all 241 Python tests at 89%
aggregate branch-aware coverage, all 41 JavaScript tests at 100% line / 93.07%
branch coverage, and all 48 Playwright scenarios. The mandatory independent
source audit then traced app configuration → feature gate → map listener,
registry → canonical scheduler → service health → Overview source state,
and registry → opt-in live diagnostic. It found and fixed two gaps the green
suite had not exposed: the opt-in live diagnostic still contacted disabled
Open-Meteo, and Standard mode lacked a browser-level proof that map clicks do
not contact it. The audit also added a positive server-route proof for explicit
enablement and confirmed that stored noncanonical health rows cannot leak into
Overview as permanent pending sources.

Post-audit smoke passed Ruff, all 241 Python tests at 89% aggregate
branch-aware coverage, all 41 JavaScript tests at 100% line / 93.07% branch
coverage, and all 49 single-worker Playwright scenarios, including visual,
responsive, accessibility, offline/degraded, and 1,000/5,000-item performance
cases. Direct runtime searches found no GDELT, `waterservices.usgs.gov`, or
`api.waterdata.usgs.gov` request path. Both Standard and Overview modes make no
Open-Meteo request by default, while the explicit opt-in route remains covered.
The browser server exited and no listener remained on port 19876. Gate result:
PASS. Phase 10 authorized: yes.

## Phase 10 — Hardening, Performance, Accessibility, and Privacy

Dependencies: Phases 0–9 selected for release.

### Tasks

- [x] Run malformed, oversized, decompression, timeout, redirect, DNS, and stale
      provider tests against the final fetch path.
- [x] Close the documented RSS DNS-resolution race by pinning validated public
      destinations or adopt a reviewed transport that provides equivalent
      protection with correct TLS hostname validation.
- [x] Add database backup/recovery and retention stress tests.
- [x] Run static secret scans, dependency audit, CSP review, and packaged Host/
      token checks.
- [x] Complete automated and manual WCAG 2.2 AA review.
- [x] Complete performance traces on minimum target hardware or an agreed
      representative Windows machine.
- [x] Verify no provider key, raw sensitive URL, or unbounded payload enters
      logs/history/exports.
- [x] Add a provider terms/attribution screen and ensure map attribution is
      never covered by controls.
- [x] Remove unreachable compatibility code only after coverage proves it is
      unused.

### Gate

- [x] No open critical/high defect.
- [x] No unbounded queue, response, database table, log, or browser collection.
- [x] All measured performance budgets pass at p95 over the documented run.
- [x] Accessibility review has no WCAG 2.2 A/AA blocker.
- [x] Dependency and secret audits are clean.

Completion evidence (2026-07-11): the final fetch path enforces both wire and
decompressed byte caps, fails malformed/truncated/trailing/unsupported
compression closed, validates every redirect, and gives user-configured RSS a
proxy-free DNS-pinned socket transport that rejects the whole resolution set
if any destination is non-public while preserving HTTPS SNI and hostname
verification. SQLite backup/restore validates integrity, schema, and foreign
keys, creates a safety copy, and rolls back a failed restore initialization.
Retention stress covers record, byte, compaction, spatial-index, backup, and
recovery behavior.

The initial exhaustive smoke passed Ruff, 256 Python tests at 88% aggregate
branch-aware coverage, 41 JavaScript tests at 100% lines / 93.12% branches,
and 50 Playwright scenarios. The required independent direct-source audit then
mapped configured and canonical network paths, redirects, decompression,
cache/storage/settings/config reads, logs, local-server request boundaries,
provider metadata, exports, browser collections, static files, optional SSE,
native logs, package assets, and active compatibility consumers. It found and
fixed user-RSS path disclosure in failure logs; malformed provider-catalog
handling; Windows newline cap drift; invalid environment response caps and
weak injected session tokens; unbounded static, cache, settings, and packaged
JSON reads; cache metadata/disk-budget and corrupt-timestamp gaps; optional
Wikimedia line/event/field retention and redirect bounds; and static/stream
exception-detail disclosure. Each correction has a focused regression. The
compatibility review removed nothing because Standard UI, tests, documentation,
or packaged tooling still consume every candidate path.

The complete post-audit gate passes Ruff, 267 Python tests at 89% aggregate
coverage, all 41 JavaScript tests (100% lines / 93.12% branches), and all 51
Playwright scenarios. Browser evidence includes serious/critical axe checks
with color contrast enabled, full keyboard operation, 24 px targets, 200%
reflow, reduced motion, offline/base/tile failure, 5,000 incidents, malformed
metadata isolation, attribution non-overlap geometry, and reviewed 1500x950 and
520x900 Standard visuals alongside every existing baseline. The WCAG 2.2
review has no A/AA blocker.

Representative-Windows post-audit p95 evidence is 12.732 ms local startup,
25.636 ms worst local route, 331.277 ms shell paint, 490.04 ms bundled map,
501.789 ms first incident, 33.8 MiB server RSS, 10 MB browser heap, and an
18.1 MiB executable; every documented budget passes. The repository secret
scanner reports no finding, pip-audit reports no known vulnerability, and npm
audit reports zero vulnerabilities. Packaged Host/token/offline checks pass,
the terms/attribution screen is bounded and credential-free, the map
attribution is unobscured, diff whitespace is clean, and no test listener
remains. Gate result: PASS. Phase 11 authorized: yes.

Rollback: release remains on the last fully gated phase; hardening failures are
not waived.

## Phase 11 — Packaged Release, Migration, and Documentation

Dependencies: Phase 10.

### Tasks

- [x] Migrate existing settings, keys, watchlist, pins, panels, audio, and TV
      preference without data loss.
- [x] Build and smoke-test the final one-file executable on a clean Windows
      profile.
- [x] Run first-launch online, first-launch offline, upgrade, corrupt-history,
      provider-outage, and shutdown/restart scenarios.
- [x] Verify executable checksum and record its Authenticode status. Never
      describe an unsigned artifact as signed.
- [x] Update README, Features, Data Sources, Credits, Security, Build, Release
      Checklist, screenshots, changelog, and file tree.
- [x] Publish provider poll intervals, freshness language, data limitations,
      attribution, and optional/experimental labels.
- [x] Review V1 endpoint usage. Remove only endpoints with zero remaining client,
      test, or documented compatibility use.
- [x] Produce a release evidence report mapping every scorecard item to test,
      measurement, screenshot, or review result.

Progress evidence (2026-07-11): a full legacy settings fixture proves keys,
audio choices, panel visibility, TV channel, watchlist, annotations/pins, and
RSS feeds survive additive V2 persistence. The release-profile runner covers
clean online-capable and offline rendered launches, upgrade, corrupt cache and
history, provider outage with retained data, loopback/Host/token checks, and
shutdown/restart with listener cleanup. Build tooling now streams its checksum,
auto-discovers Windows SDK signing tools, supports strict mandatory SHA-256
Authenticode plus RFC 3161 timestamping, and verifies the Windows policy chain.

The initial Phase 11 source smoke passed Ruff, 271 Python tests at 89%
aggregate coverage, 41 JavaScript tests at 100% lines / 93.12% branches, and
51 Playwright scenarios. The required independent audit mapped release scripts,
package/UI state transitions, signing and checksums, legacy persistence,
provider policy/cadence/docs, screenshots, CI, and remaining V1 consumers. It
found and corrected the failed OpenFEMA v1 live request, invalid NWS diagnostic
parameter and undersized peak alert cap, slow unbounded-duration SSE diagnostic,
an unresolved Yahoo terms decision that still caused default traffic, missing
rendered checks in clean/offline package profiles, absent listener-cleanup
assertion, incomplete release-contract documentation checks, and misleading
unsigned CI artifact naming. Post-audit source regressions are attached to each
fix. Production package gates remain open below; they are not waived.

The corrected final post-audit source gate passes Ruff, 288 Python tests at 89% aggregate
coverage, 43 JavaScript tests (100% lines / 93.32% branches), and all 52
Playwright scenarios. The first visual rerun deliberately failed four Standard
baselines after conditional Yahoo data was removed. Direct 1500x950 and 520x900
review confirmed the intended counter/ticker-only change, but that review also
exposed a pre-existing source counter that accumulated attempts until reaching
an arbitrary 80-entry window. It was replaced by tested per-source latest
state, ISS/RSS coverage was connected, deterministic 15/15 readiness was added,
the four baselines were reviewed/updated, and the complete browser gate then
passed. No Foglight listener remains.

The packaging self-audit also added an optional manual, `main`-restricted
`release-signing` workflow. It pins every GitHub action by commit, repeats all
source gates before accessing protected environment secrets, validates one
currently valid private-key code-signing certificate, deletes decoded PFX
bytes immediately after import, requires timestamped policy-verified signing,
runs both packaged profiles, rechecks the signed checksum, and removes imported
certificate material even on failure. Workflow contracts, actionlint, and the
PowerShell parser pass. This optional provenance channel is not a runtime or
zero-configuration requirement.

The release-profile and retained-outage runners subsequently pass through the
actual native source launcher in explicitly labeled `source-native` mode:
clean rendered start, first-launch offline, exact 14-provider scheduler parity,
legacy preference/key preservation, corrupt cache cleanup, corrupt SQLite
quarantine/rebuild, retained two-hour-old incident/search continuity during a
blocked refresh, Host/token boundaries, and shutdown/restart cleanup. This is
strong scenario evidence but does not replace the mandatory one-file executable
runs, which were subsequently executed and are recorded below.

Final packaged evidence (release metadata refreshed 2026-07-13): the
source-matching one-file executable is 19,050,146 bytes with SHA-256
`ce8798df2ed9a18821492c773fc88aa90e58bd63e408e076b3e68ef0fd114b55`.
The manifest matches and Windows reports `NotSigned`, accurately disclosed.
Both executable-mode profile suites pass every clean, offline, upgrade,
corruption, outage, security, restart, and listener-cleanup assertion. A real
pywebview launch created a maximized Foglight window, rendered live panels and
the bundled map without browser fallback, bound only `127.0.0.1`, and exited
cleanly through the normal close path.

The independent package audit then found and fixed three final gaps: the
offline harness lacked its own loopback/listener-stop assertions and bounded
reads, the PE lacked Windows file/product version resources, and the legacy
native minimum size could clip on scaled laptop work areas. The corrected
artifact includes deterministic 0.2.0 metadata, uses a maximized responsive
900x640-minimum window, and passed both exhaustive executable suites and the
native-shell test again after those changes.

User acceptance then exposed a default-path gap: normal native launches left
V2/Overview disabled and Standard could start dozens of upstream requests in
one burst. The release was reopened. The launcher now enables Overview/V2 by
default while preserving explicit `0` rollback overrides; new scheduler state
warms within five seconds; scheduler and compatibility requests share a global
six-request gate; Standard uses bounded initial batches and an early recovery
pass; and safe logs identify nested network failure types. A new no-flags
`--require-live` executable gate produced 50 incidents from two live keyless
sources. The existing user profile separately reached 50 incidents and seven
live sources without state reset. Closing its maximized window signaled the
scheduler before WebView teardown and released every process and listener in
3.22 seconds. Every packaged profile passed again. The final browser self-audit
also replaced routine `retain-on-failure` tracing with retry-scoped tracing after
trace finalization—not Foglight assertions—caused context teardown timeouts; all
52 browser scenarios then passed together.

### Gate

- [x] Clean-profile `Foglight.exe` passes all non-negotiable product gates.
- [x] Upgrade preserves user settings and can recover from old/corrupt cache.
- [x] Offline shell, bundled map, cached incidents, and freshness explanations
      work in the packaged application.
- [x] Final source and packaged artifacts match checksums and release docs.
- [x] The 100-point scorecard below is 100/100.

## 10/10 Release Scorecard

No partial credit is awarded for a failed mandatory item inside a category.

| Category | Points | Required evidence |
|---|---:|---|
| Product usefulness | 15 | Zero-config Overview, Now list, modes, changes, timeline, details, and useful offline shell |
| Data correctness and provenance | 15 | Canonical schema, adapter fixtures, units/times/geometry, source labels, conservative correlation |
| Reliability and provider isolation | 15 | Scheduler/backoff/circuit tests, stale behavior, source health, restart and corruption recovery |
| UX and information design | 10 | Priority hierarchy, map/list linkage, uncluttered default, all view states, approved screenshots |
| Accessibility | 10 | WCAG 2.2 AA automated and manual evidence, keyboard, reflow, reduced motion, non-map parity |
| Performance and bounded resources | 10 | Startup/render/API/DB budgets, 5k geometry stress, retention and memory evidence |
| Zero-key, licensing, and attribution | 10 | No mandatory key/account, reviewed terms, bundled base, visible attribution, optional-source labels |
| Security and privacy | 5 | Loopback/token/Host/CSP/SSRF/secret tests, local-only state, safe exports |
| Testing and maintainability | 5 | Python/JS/unit/integration/browser/package coverage, modular boundaries, fixture drift diagnostics |
| Packaging, migration, and documentation | 5 | Checksummed EXE with recorded signature status, clean upgrade, complete docs and release evidence |
| **Total** | **100** | **All mandatory gates pass; no critical/high defect or undocumented waiver** |

A release is “10/10 complete” only at 100/100. A lower score is a transparent
release-candidate status, not permission to relabel unfinished work.

## Cross-Phase Test Matrix

| Layer | Required tests |
|---|---|
| Model | validation, round trip, unknown fields, enum/timestamp/geometry boundaries |
| Adapter | golden fixtures, empty, partial, malformed, unknown future field, units, provenance |
| Correlation | intended merge, intended non-merge, cross-kind relation, media-only limitation |
| Storage | migration, UPSERT, concurrency, retention, restart, corruption, size cap |
| Scheduler | interval, one-in-flight, timeout, backoff, jitter, Retry-After, circuit breaker |
| API | filters, limits, pagination/cursor, revision, invalid query, body cap, Host/token boundary |
| State/UI | reducer/store, filters, priority explanation, time formatting, offline/stale transitions |
| Browser | startup, modes, Now, map/list, drawer, timeline, settings, watch, export, all error states |
| Accessibility | keyboard, focus, roles, live status, contrast, target size, reflow, reduced motion |
| Performance | large fixtures, map pan/zoom, first paint, DB query, memory, executable size |
| Packaged | clean profile, online/offline, upgrade, checksum, recorded signature status, loopback, shutdown cleanup |

## Risk Register

| Risk | Mitigation | Blocking condition |
|---|---|---|
| Provider schema/rate drift | Registry, fixtures, drift diagnostics, health, circuit breaker | Core provider has no tested adapter/fallback |
| Incorrect incident merges | Kind-specific conservative rules, evidence, rule version, non-merge tests | Ambiguous events are merged without trace |
| Misleading priority | Separate CAP dimensions, lanes, component explanation | UI presents opaque or media-driven emergency score |
| Monolithic refactor regression | Behavior-preserving extraction and V1 parity gates | V1 removed before V2 parity |
| Database growth/corruption | Retention cap, WAL discipline, recovery/quarantine tests | Unbounded table/WAL or settings loss |
| Map licensing/availability | Bundled Natural Earth base and local Leaflet | Core map requires unreviewed hosted tiles |
| Accessibility regression | WCAG AA gates and non-map equivalent | Any core action is pointer/map/color only |
| Notification spam | Explicit opt-in, thresholds, quiet hours, revision dedupe | Notification before permission or repeated revision |
| Conditional source becomes dependency | Tiering and disable tests | GDELT/Open-Meteo/experimental source required by Overview |
| Build/runtime bloat | Standard library first, pinned dev-only browser tools, size budget | New framework/runtime breaks single-EXE contract |

## Execution Evidence Log

During implementation, append one entry per completed phase:

```text
Phase:
Commit/working-tree reference:
Tasks completed:
Tests and measurements:
Screenshots/artifacts:
Provider live checks (if any):
Known limitations:
Gate result: PASS / FAIL
Next phase authorized: yes / no
```

Do not mark a phase complete without this evidence.
