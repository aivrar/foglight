# Foglight V2 Planning Research

Status: implementation reference
Research date: 2026-07-10
Scope: research and architecture planning only; this document does not mark any V2 feature as implemented.

## Product Invariant

Foglight is a local, civilian global situation monitor. Its defining delivery
contract is:

- Download one Windows executable and launch it.
- No Foglight account, hosted Foglight backend, registration, or mandatory API
  key.
- The default experience is useful before the user changes a setting.
- User preferences, history, watch regions, and annotations remain local.
- A failed provider degrades one source, not the application.
- The interface distinguishes observations, forecasts, media coverage, and
  confirmed incidents instead of implying that all signals have equal
  evidentiary weight.

Optional keyed integrations may exist only behind an explicit Advanced label.
They must never supply data required by the default Overview.

## Research Method

This plan was produced from:

1. A source inventory of the current server, client, HTML, settings, tests,
   build script, CI workflow, and documented providers.
2. Primary specifications for alert semantics, geometry, timestamps,
   accessibility, persistence, WebView behavior, and map rendering.
3. Current first-party provider documentation for endpoint shape, rate limits,
   terms, and authentication requirements.
4. Direct, bounded endpoint checks from the Foglight development environment
   on 2026-07-10. These checks inspected status, content type, and top-level
   shape; they did not create accounts or write external data.

Live checks are evidence of current compatibility, not a substitute for fixture
tests or a guarantee of provider availability.

## Current Codebase Baseline

### Shape

| File | Current size | Responsibility |
|---|---:|---|
| `foglight_server.py` | 1,738 lines | Settings, HTTP security, caching, all provider adapters, parsing, aggregation, routing, and direct-server lifecycle |
| `web/app.js` | 2,350 lines | API calls, map layers, view state, every renderer, timers, settings, audio, watchlists, briefing export, and startup |
| `index.html` | 1,319 lines | Entire document structure and styling |

The current application is operational, but these three files are too coupled
for the proposed incident model and multi-view UI. V2 should be a staged module
extraction, not an all-at-once rewrite.

### Current runtime behavior

- The browser starts roughly twenty independent refresh loops.
- Most provider payloads travel directly from an upstream through a thin proxy
  into provider-specific browser renderers.
- The browser maintains parallel global caches such as `LAST_USGS`,
  `GDACS_CACHE`, `FG_LAST_CONFLICT`, and `FG_LAST_WX`.
- Related observations are displayed separately; there is no canonical
  observation or incident identity.
- Freshness is carried in response headers, but there is no persistent provider
  health record, change cursor, or historical incident state.
- User state is a bounded JSON settings file; upstream response caching is
  file-based and hashed.
- Tests cover the local HTTP boundary, cache secrecy, static exposure, frontend
  ID contracts, native loopback binding, and log rotation. They do not yet
  cover provider adapters with fixtures, event correlation, browser behavior,
  accessibility, performance, or visual states.

### Constraints that must remain true

- Python 3.13 and the standard library remain the runtime foundation.
- The Windows application remains a single PyInstaller executable.
- The server remains bound to `127.0.0.1`.
- State-changing endpoints retain Host, same-origin, and per-launch token
  protection.
- Upstream reads, cache size, history size, request parameters, and concurrency
  remain bounded.
- Existing V1 endpoints remain available until every current view has migrated
  and equivalent contract tests pass.

## Standards Decisions

### Observation and alert semantics

Foglight must not reduce every alert to one ambiguous severity value. The
[OASIS Common Alerting Protocol 1.2](https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2.html)
separates:

- `severity`: impact (`Minor`, `Moderate`, `Severe`, `Extreme`, `Unknown`)
- `urgency`: time available to act (`Past`, `Future`, `Expected`, `Immediate`,
  `Unknown`)
- `certainty`: evidentiary confidence (`Unlikely`, `Possible`, `Likely`,
  `Observed`, `Unknown`)

The canonical Foglight schema will preserve these as separate fields. Providers
without CAP fields may map values only through documented, fixture-tested
adapter rules. Unknown values remain unknown; they are never silently upgraded.

### Geometry

Canonical geometries will follow
[RFC 7946 GeoJSON](https://www.rfc-editor.org/info/rfc7946/): WGS-84, decimal
degrees, and longitude before latitude. Each observation may have a Point,
LineString, Polygon, Multi-geometry, or `null`. Bounding box and centroid are
derived fields, not replacements for the source geometry.

GeoJSON does not represent uncertainty by itself. Foglight will therefore keep
source-provided radius/error/forecast-cone information as explicit metrics or
separate geometries with a documented role.

### Time

API timestamps will be UTC RFC 3339 strings. The model distinguishes:

- source event time
- effective time
- expiration time
- source update time
- Foglight ingestion time

Missing source times remain `null`. Ingestion time must not be presented as
event time.

### Persistence

V2 incident history should use Python's bundled SQLite rather than additional
JSON files or a new database dependency.

- SQLite UPSERT supports stable provider observation identities.
- WAL allows concurrent local readers while the ingestion worker writes.
- One short-lived connection per worker operation avoids sharing a connection
  across threads.
- R*Tree is appropriate for bounded-box candidate queries if the bundled
  SQLite build exposes it; startup must detect support and fall back to indexed
  latitude/longitude columns if it is absent.

References:

- [SQLite WAL](https://www.sqlite.org/wal.html)
- [SQLite UPSERT](https://www.sqlite.org/lang_upsert.html)
- [SQLite R*Tree](https://www.sqlite.org/rtree.html)
- [Python sqlite3 thread-safety and transactions](https://docs.python.org/3/library/sqlite3.html)

### Accessibility

The release target is WCAG 2.2 AA for the application surface, including:

- complete keyboard operation
- visible, unobscured focus
- minimum target sizing
- name, role, value for custom controls
- programmatic status messages
- non-color-only severity and state communication
- reduced-motion behavior
- a non-map path to every incident available on the map

Reference: [WCAG 2.2](https://www.w3.org/TR/WCAG22/).

### Map rendering and offline shell

Leaflet supports GeoJSON and a Canvas renderer for dense vector paths. V2 will
vendor the exact reviewed Leaflet distribution inside the executable rather
than require `unpkg.com` to start the UI.

The current CARTO raster background is not a universally safe zero-cost
default: CARTO currently documents that commercial basemap use requires an
Enterprise license. The standard OpenStreetMap tile service is also best-effort
and prohibits offline tile prefetching.

V2 will use a simplified bundled Natural Earth 1:110m world dataset as the
always-available base. Natural Earth publishes its raster and vector data in
the public domain. Hosted detail tiles may be considered later only after a
documented terms review and must never be required for core map operation.

References:

- [Leaflet reference](https://leafletjs.com/reference)
- [CARTO basemap licensing summary](https://docs.carto.com/faqs/carto-basemaps)
- [OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/)
- [Natural Earth terms](https://www.naturalearthdata.com/about/terms-of-use/)

## Canonical Domain Model

V2 needs two distinct concepts.

### Observation

An observation is one provider record. It does not claim that Foglight has
verified the underlying event.

Required fields:

```text
schema_version
observation_id          provider namespace + stable provider identifier
provider_id
provider_record_id
kind                    controlled Foglight taxonomy
headline
summary
status                  active | updated | ended | cancelled | unknown
severity                CAP-compatible label + adapter evidence
urgency                 CAP-compatible label + adapter evidence
certainty               CAP-compatible label + adapter evidence
event_at                 RFC 3339 UTC or null
effective_at             RFC 3339 UTC or null
expires_at               RFC 3339 UTC or null
source_updated_at        RFC 3339 UTC or null
ingested_at              RFC 3339 UTC
geometry                 RFC 7946 geometry or null
centroid                 [longitude, latitude] or null
bbox                     [west, south, east, north] or null
location_name
country_codes            ISO codes only when supplied or deterministically mapped
metrics                  typed values with units and provenance
source_url               safe HTTP(S) URL or null
content_hash             deterministic change detection
raw_fingerprint          hash only; raw body remains in bounded provider cache
```

### Incident

An incident is a local grouping of related observations. It has a stable local
identity and an inspectable relationship to every member observation.

Required fields:

```text
incident_id
kind
headline
summary                  deterministic template, never invented facts
status
severity
urgency
certainty
priority_score           0..100
priority_components      explicit component values and rule version
first_seen_at
last_changed_at
last_observed_at
geometry / centroid / bbox
observation_ids
relations                related_to | caused_by | affects | coverage_of
change_type               new | escalated | downgraded | updated | resolved
revision
```

The public API returns sources and score components so the interface can answer
“why is this important?” without claiming opaque intelligence.

## Correlation Rules

Correlation is deterministic, versioned, and category-specific.

1. Exact provider ID updates the same observation.
2. Exact authoritative cross-reference links observations without fuzzy
   matching.
3. Same-kind candidate matching uses bounded time and distance windows plus
   source-specific identifiers and normalized names.
4. Different kinds are related, not merged. For example, an earthquake and a
   tsunami bulletin may have `caused_by`/`related_to` links but remain separate
   incidents.
5. Media headlines create `coverage_of` links or a low-certainty coverage
   cluster. Media volume alone cannot convert a claim into an observed event.
6. Automatic merges store the rule version and evidence. Ambiguous candidates
   remain separate.

Initial thresholds must be derived from recorded fixtures and documented per
kind; they must not be one universal distance/time constant.

## Priority Model

The Overview needs a priority score, not a purported objective “risk” score.
The initial model will be deterministic and capped at 100:

```text
impact/severity       0..40
urgency               0..20
freshness             0..15
corroboration         0..15
watch-region relevance 0..10
stale/expired penalty  0..-40
```

Rules:

- The UI always shows the component breakdown.
- Unknown severity receives no severity bonus; it is not treated as safe.
- Independent corroboration is capped and source families are deduplicated.
- Official warnings and measurements, forecasts, media reports, community
  signals, market data, and internet activity occupy different lanes.
- Market or popularity spikes cannot displace an extreme life-safety alert from
  the top “Now” area.
- Score rule versions are persisted with incident revisions.

## Provider Tiers

| Tier | Purpose | Failure behavior |
|---|---|---|
| Core operational | Official hazard and emergency observations needed by Overview | Use bounded stale data, show source outage, never blank the app |
| Supporting | Humanitarian, institutional, and reputable media context | Remove missing source from corroboration; retain incident |
| Signal | Markets and public internet activity | Isolated optional lane; never drives life-safety priority |
| Experimental | Unstable community or undocumented services | Disabled or clearly badged; never required by a completion gate |
| Optional keyed | Explicit user enhancement such as FIRMS | Hidden from zero-config acceptance criteria |

Every registry entry must define owner, endpoint family, tier, adapter,
interval, timeout, body cap, retention, attribution, usage notes, key
requirement, and fallback behavior.

## Keyless Provider Research

### Existing core sources

- USGS offers real-time GeoJSON earthquake summary feeds and stable event IDs.
  [USGS real-time feeds](https://earthquake.usgs.gov/earthquakes/feed/)
- NWS alerts use CAP 1.2 fields and GeoJSON/JSON-LD. NWS recommends no more
  than one alerts request every 30 seconds; Foglight can poll substantially
  slower and use the required identifying User-Agent.
  [NWS alerts service](https://www.weather.gov/documentation/services-web-alerts)
- NASA EONET V3 is the current stable version and supplies event IDs,
  categories, sources, dates, and Point/Polygon GeoJSON.
  [EONET V3](https://eonet.gsfc.nasa.gov/docs/v3)
- GDACS currently publishes RSS, CAP, and GeoJSON resources. Prefer its
  documented GeoJSON API over scraping HTML-derived fields.
  [GDACS API quick start](https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v1.pdf)
- NHC publishes a documented current-storm JSON summary. The adapter must
  tolerate the valid no-active-storm shape.
  [NHC product examples](https://www.nhc.noaa.gov/productexamples/)

### Approved new keyless candidates

#### NOAA Aviation Weather

The public API provides worldwide SIGMETs and other aviation-weather products
in JSON/GeoJSON. It is a better operational layer than presenting a community
aircraft feed as dependable global coverage.

- Maximum documented rate: 100 requests/minute.
- Each endpoint should not be consumed more frequently than once per minute.
- Foglight plan: poll SIGMET GeoJSON every 5 minutes with conditional caching;
  add other products only after separate value and load review.

Reference: [Aviation Weather Data API](https://aviationweather.gov/data/api/).

#### OpenFEMA

OpenFEMA requires no registration and provides read-only disaster datasets.
Disaster declarations are official context, not a real-time detection feed.
Poll recent declarations every 30 minutes and relate them to existing incidents
without inflating urgency.

Reference: [OpenFEMA](https://www.fema.gov/about/reports-and-data/openfema).

#### NOAA NDBC and CO-OPS

NDBC publishes keyless RSS observations containing station identity, GeoRSS
coordinates, weather, wave, pressure, and water measurements. Its latest
observation file is regenerated every five minutes. CO-OPS provides current
water-level JSON with preliminary/verified quality flags.

Foglight should query stations only around active coastal incidents or saved
watch regions. It must not poll a global station inventory on every refresh.

References:

- [NDBC RSS observations](https://www.ndbc.noaa.gov/faq/rss_access.shtml)
- [CO-OPS data services](https://tidesandcurrents.noaa.gov/education/tech-assist/data/)
- [CO-OPS response fields](https://api.tidesandcurrents.noaa.gov/api/prod/responseHelp.html)

#### NASA/JPL fireballs

The JPL Fireball API is keyless and supplies date, optional location, altitude,
energy, and estimated impact energy. JPL's fair-use policy permits only one API
request at a time and describes the service as best effort. Poll no more than
every six hours and display it as a quiet space-event layer, not an emergency
warning.

References:

- [Fireball API](https://ssd-api.jpl.nasa.gov/doc/fireball.html)
- [JPL API fair-use policy](https://ssd-api.jpl.nasa.gov/doc/index.php)

### Conditional candidates

#### GDELT

GDELT offers multilingual news search and geographic coverage data, but a small
planning request returned HTTP 429 on 2026-07-10. It is therefore unsuitable as
a core or user-visible dependency.

If implemented, it must be:

- supporting-tier only
- disabled by circuit breaker after 429
- polled at low frequency
- used for coverage trends/corroboration, never event confirmation
- removable without changing an incident's authoritative facts

Reference: [GDELT DOC 2.0](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/amp/).

#### Open-Meteo expansion

Open-Meteo exposes marine, air-quality, and flood products without a key for
non-commercial use, under attribution and usage conditions. Foglight already
uses its current-weather service. Because the public free terms distinguish
commercial use, V2 must complete a release-licensing decision before making
additional Open-Meteo products core. Do not continuously poll global grids;
use contextual point queries only.

Reference: [Open-Meteo service and terms summary](https://open-meteo.com/).

#### USGS water data

Near-real-time streamflow and gauge-height data are valuable, but the legacy
Water Services API is scheduled for decommissioning in early 2027. Do not add a
new dependency on the legacy endpoint. Re-evaluate the replacement
`api.waterdata.usgs.gov` contract during the source-addition phase.

Reference: [USGS Water Services transition notice](https://waterservices.usgs.gov/).

### Explicit non-core decisions

- NASA FIRMS API and map services require an emailed MAP_KEY. FIRMS remains an
  optional Advanced integration and cannot appear in zero-config acceptance
  tests. [FIRMS key requirement](https://firms.modaps.eosdis.nasa.gov/api/map_key/)
- ADS-B.lol is useful community data but returned an upstream 502 during the
  audit. Keep aircraft positions experimental/off by default; aviation hazards
  are the dependable default layer.
- Yahoo Finance chart endpoints are not a documented operational data contract.
  Commodity output remains a signal-tier feature with failure isolation.
- YouTube, Reddit RSS, Open Notify, public media RSS, and similar feeds are
  best-effort supporting or signal sources, not core availability gates.

## Direct Endpoint Validation Results

Bounded requests executed on 2026-07-10:

| Provider | Result | Observed shape | Planning consequence |
|---|---|---|---|
| OpenFEMA declarations | 200 | object containing `metadata` and `DisasterDeclarationsSummaries[]` | Approved as slow official context |
| NOAA Aviation Weather SIGMET | 200 | GeoJSON FeatureCollection; 25 live features in sample | Approved as core aviation-hazard candidate |
| NOAA NDBC nearby observations | 200 | RSS 2.0 with GeoRSS points and station observations | Approved for incident/watch-region contextual queries |
| NOAA CO-OPS latest water level | 200 | `metadata` plus `data[]`; one current sample | Approved with QA flag preservation |
| NASA/JPL fireballs | 200 | `signature`, `fields[]`, and `data[]` | Approved as low-frequency signal |
| GDELT article list | 429 | rate limited | Conditional only; circuit breaker mandatory |
| Open-Meteo marine | 200 | current values plus units | Technically valid; licensing decision still required |

## Source Health and Scheduling Requirements

Move provider scheduling from many browser intervals to a server-side registry.
The scheduler must provide:

- bounded worker count
- provider-specific minimum interval
- one in-flight request per provider
- timeout and response-size cap
- conditional requests where supported
- exponential backoff with jitter
- `Retry-After` handling
- circuit breaker for repeated failure/429
- last attempt, last success, latency, status, consecutive failures, cached age,
  and next eligible attempt
- no retry storm at startup

The browser should poll one revision/cursor endpoint rather than initiate every
upstream fetch.

## Testing Research Conclusions

- Provider adapters require checked-in, sanitized fixtures. CI must not depend
  on internet availability.
- Live-provider checks are a separate opt-in command and report drift without
  blocking normal pull requests for transient outages.
- Node's stable built-in test runner is sufficient for pure JavaScript state,
  scoring-display, filtering, and time-format modules.
- Playwright can launch the local server for deterministic browser tests and
  can snapshot the accessibility tree.
- Browser fixtures must include loading, populated, partial, stale, empty,
  malformed, and offline states.

## Phase 8 implementation research addendum (2026-07-10 local)

Phase 8 keeps all watch, acknowledgement, snooze, and notification state in the
existing local settings/state boundary. It does not add geolocation, a hosted
geocoder, a cloud push service, or a background service worker.

- Web notifications must be requested only from the explicit Enable button.
  The Notifications API expects a user gesture and exposes `granted`, `denied`,
  and `default`; localhost is the only requesting origin. [MDN notification
  permission](https://developer.mozilla.org/docs/Web/API/Notification/requestPermission_static)
- WebView2 does not show its own notification permission prompt. The host must
  handle `PermissionRequested`, and push notifications are unavailable. The
  native host therefore grants only a user-initiated `Notifications` request
  from its exact loopback origin; every other permission/origin remains at the
  platform default. The in-app notification center is the guaranteed path.
  [Microsoft WebView2 permission kind](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/winrt/microsoft_web_webview2_core/corewebview2permissionkind)
- pywebview disables downloads by default. User-triggered Blob exports require
  its reviewed download setting so WebView2 presents the native save dialog;
  the app does not choose or overwrite a path. [Microsoft WebView2 download
  handling](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/overview-features-apis#downloads)
- Watch matching uses longitude unwrapping relative to the tested point and an
  inclusive boundary test. GeoJSON exports stay in WGS 84 longitude/latitude,
  retain RFC 7946 geometries, and preserve west-greater-than-east dateline
  bounds where applicable. [RFC 7946](https://datatracker.ietf.org/doc/rfc7946/)
- CSV is a human spreadsheet export: every cell is quoted, embedded quotes are
  doubled, line breaks are collapsed, and cells beginning with `=`, `+`, `-`,
  or `@` receive an in-cell tab prefix. [OWASP CSV
  Injection](https://owasp.org/www-community/attacks/CSV_Injection)
- Wall display advances only after explicit start, pauses when the document is
  hidden, has manual previous/next/stop controls, and becomes manual-only when
  reduced motion is requested. [Page Visibility
  API](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API),
  [reduced motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/%40media/prefers-reduced-motion)

Implementation order: (1) backward-compatible settings schema and pure watch,
geometry, quiet-hours, export, and notification models; (2) bounded SQLite
incident search plus last-revision metadata; (3) Overview watch/search/export,
notification-center, offline-history, and wall-display controllers; (4)
loopback-only native notification permission and user-triggered downloads; (5)
upgrade, offline packaged, permission, dedupe, dateline, export, accessibility,
performance, and rollback gates followed by the independent phase audit.

References:

- [Node test runner](https://nodejs.org/api/test.html)
- [Playwright local web server](https://playwright.dev/docs/test-webserver)
- [Playwright accessibility snapshots](https://playwright.dev/docs/aria-snapshots)

## Architecture Conclusions

## Phase 9 contract revalidation (2026-07-11 UTC)

The provider-addition phase was rechecked against the current first-party
contracts immediately before implementation. This review supersedes any older
endpoint-shape assumptions in this document.

### 9A — NOAA Aviation Weather

The Aviation Weather Center Data API still documents worldwide SIGMET output
in JSON and GeoJSON, a maximum of 100 requests per minute, no individual
endpoint more often than once per minute per thread, and a default maximum of
400 returned records for most services. HTTP 204 is an explicitly valid
no-data response for the API's non-GeoJSON formats; an empty GeoJSON
`FeatureCollection` is therefore also treated as a successful global no-data
batch rather than a source failure.

A bounded live contract probe of
`/api/data/airsigmet?format=geojson` returned a `FeatureCollection` whose
feature properties included `icaoId`, `airSigmetType`, `alphaChar`, `hazard`,
`seriesId`, `validTimeFrom`, `validTimeTo`, numeric `severity`, altitude and
movement fields, and `rawAirSigmet`. Foglight will preserve those source
values and both validity boundaries without inventing undocumented CAP
certainty or severity meanings. The five-minute cadence is twenty percent of
the per-endpoint minimum frequency and far below the global rate ceiling.

Implementation decision: model SIGMETs as the distinct
`aviation_hazard` kind. Volcanic-ash and severe-weather relationships may be
recorded conservatively, but different event kinds are never merged. Official
aviation hazards are enabled in the default incident view; community aircraft
positions are explicitly experimental and off by default.

References:

- [Aviation Weather Data API](https://connect.aviationweather.gov/data/api/)
- [SIGMET GeoJSON endpoint](https://aviationweather.gov/api/data/airsigmet?format=geojson)

### 9B — OpenFEMA

OpenFEMA remains a keyless, read-only API. The Disaster Declarations
Summaries dataset reports an update frequency of `R/PT20M` and includes the
declaration date, incident begin/end dates, state and county/FIPS fields,
incident/declaration types, title, disaster number, refresh time, and program
flags. A 30-minute cadence is compatible with that publication frequency.

Implementation decision: a declaration is administrative context with a
distinct `disaster_declaration` kind. `declarationDate` is not rewritten as
event onset, and declaration severity, urgency, and certainty are not inferred.
Only conservative geography, incident-type, and date evidence can relate it to
a real-time hazard; the relationship cannot raise that hazard's urgency.

Reference: [OpenFEMA Disaster Declarations Summaries](https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries)

### 9C — NOAA NDBC and CO-OPS

NDBC's official RSS documentation still says the latest-observation file is
regenerated every five minutes and warns that broad nearby-station responses
can exceed 500 KB. NDBC items expose station identity, GeoRSS position, and
weather/ocean measurements. The CO-OPS Data API continues to return station
metadata and water-level observations with documented quality flags and
explicit units/datum request parameters.

Implementation decision: neither service is globally polled. Query generation
must deduplicate and cap active coastal incident and saved-watch contexts;
cadence cannot be shorter than five minutes. Store reported units, datum, and
quality flags verbatim. An anomaly is allowed only when the same source payload
provides an explicit prediction or baseline—never from a lone measurement.
CO-OPS uses the documented `date=latest` option (the last point available within
18 minutes), and contextual measurement incidents are excluded from future
context derivation so they cannot sustain polling after their independent
incident or saved watch is removed.

References:

- [NDBC RSS access](https://www.ndbc.noaa.gov/faq/rss_access.shtml)
- [CO-OPS Data API](https://api.tidesandcurrents.noaa.gov/api/prod/datagetter)
- [CO-OPS response fields](https://api.tidesandcurrents.noaa.gov/api/prod/responseHelp.html)

### 9D — NASA/JPL Fireballs

The current JPL Fireball API identifies itself as version 1.2 and returns a
signature plus a `fields` array that defines positional rows. Date, observed
energy, and estimated impact energy are guaranteed; latitude, longitude,
direction, altitude, velocity, and other values are optional strings. JPL's
fair-use policy still permits only one request at a time and describes APIs as
best effort with formats subject to change.

A 2026-07-11 live structural probe confirmed signature version 1.2 and 20 rows,
but also found that the live service encodes `count` as a decimal string and
includes the CNEOS-described optional `vel` entry-velocity field even though
the API document's sample uses a numeric count and omits that field. The
adapter therefore accepts only the two explicitly documented/live field sets,
maps by validated field name rather than position assumptions, and fails closed
on any other signature or field contract.

Implementation decision: validate signature version and field names before
indexing a row, issue one request at a time no more often than every six hours,
and represent fireballs as a quiet `fireball` science signal. Missing location
is valid and no emergency urgency is inferred.

References:

- [JPL Fireball API](https://ssd-api.jpl.nasa.gov/doc/fireball.html)
- [JPL API fair-use policy](https://ssd-api.jpl.nasa.gov/doc/index.php)

### 9E — conditional-source decisions

- Open-Meteo's current free API terms limit the keyless service to
  non-commercial use and require attribution; stated limits include 10,000
  calls/day, 5,000/hour, and 600/minute. Because Foglight is distributed for
  unrestricted downstream use, no additional Open-Meteo product becomes a V2
  dependency without an explicit compatible distribution decision.
  [Open-Meteo terms](https://open-meteo.com/en/terms)
  A fresh 2026-07-11 review confirmed that commercial use requires a paid
  subscription and API key. The legacy click-weather route is therefore
  disabled by default and excluded from Overview.
- GDELT still lacks a sufficiently dependable formal rate contract for a core
  product, and the planning probe received HTTP 429. It remains disabled and
  removable; Overview has no dependency on it.
  [GDELT DOC 2.0](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/amp/)
  GDELT's official 2024 quota discussion confirms hosted API quotas but still
  does not publish a dependable per-client allowance; GDELT stays unregistered.
- USGS now documents modern water-data APIs and early-2027 legacy-service
  retirement. Its June 30, 2026 notice says new parameter codes are already
  modern-API-only, while the migration guide says more than a few requests per
  hour requires an API key. Foglight will not add the retiring endpoint or a
  new water provider to the zero-key default during Phase 9.
  [USGS Water Data APIs](https://api.waterdata.usgs.gov/),
  [OGC API status](https://api.waterdata.usgs.gov/docs/ogcapi),
  [WaterServices retirement](https://waterdata.usgs.gov/blog/api-waterservices-decom),
  [modern migration guide](https://api.waterdata.usgs.gov/docs/ogcapi/migration/)

The conditional-source gate is therefore satisfied by an explicit disabled
decision, not by adding fragile feeds. These decisions must be represented in
the provider registry and covered by a test proving that required Overview
capability is unchanged when every conditional/experimental source is absent.

1. Preserve V1 while building V2 beside it.
2. Introduce Observation and Incident models before redesigning the UI.
3. Normalize on the server; no new view may consume a raw provider payload.
4. Store bounded history and revisions in SQLite.
5. Make the server scheduler the only routine upstream caller.
6. Build Overview from incidents and changes, not from independent feeds.
7. Keep Standard/Command views for density; make Overview the default only
   after it passes parity and usability gates.
8. Use a bundled map base and vendored application assets.
9. Classify provider authority and availability explicitly.
10. Add new sources one at a time, each with fixtures, terms, rate, health, and
    failure tests.

The detailed implementation order and mandatory completion gates are in
`docs/FOGLIGHT_V2_EXECUTION_PLAN.md`.
