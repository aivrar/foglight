# File Tree

```text
foglight/
|-- .github/
|   |-- ISSUE_TEMPLATE/
|   |   |-- bug_report.yml
|   |   |-- config.yml
|   |   `-- feature_request.yml
|   |-- workflows/
|   |   `-- ci.yml
|   |-- dependabot.yml
|   `-- pull_request_template.md
|-- assets/
|   |-- foglight.ico
|   `-- foglight-icon.png
|-- config/
|   |-- coops_water_level_stations.v1.json
|   |-- correlation_rules.v1.json
|   |-- data_taxonomy.v1.json
|   |-- priority_rules.v1.json
|   `-- provider_registry.v1.json
|-- foglight_core/
|   |-- providers/
|   |   |-- base.py
|   |   |-- canonical.py
|   |   |-- coastal.py
|   |   |-- hazards.py
|   |   |-- legacy.py
|   |   `-- runtime.py
|   |-- cache.py
|   |-- correlation.py
|   |-- fetching.py
|   |-- jsonfiles.py
|   |-- models.py
|   |-- scheduler.py
|   |-- scoring.py
|   |-- service.py
|   |-- settings.py
|   |-- storage.py
|   `-- xmlfeeds.py
|-- docs/
|   |-- adr/
|   |   |-- 0001-sqlite-storage.md
|   |   |-- 0002-v1-compatibility.md
|   |   |-- 0003-bundled-base-map.md
|   |   |-- 0004-server-scheduler.md
|   |   `-- 0005-priority-semantics.md
|   |-- baselines/
|   |   |-- phase0-2026-07-10.json
|   |   `-- phase10-2026-07-11.json
|   |-- screenshots/
|   |   |-- hero.PNG
|   |   |-- standard.PNG
|   |   `-- command.PNG
|   |-- BUILD_WINDOWS.md
|   |-- COVERAGE_POLICY.md
|   |-- CANONICAL_SOURCE_MAPPINGS.md
|   |-- CORRELATION_AND_PRIORITY.md
|   |-- DATA_SOURCES.md
|   |-- FEATURES.md
|   |-- FILE_TREE.md
|   |-- FOGLIGHT_V2_EXECUTION_PLAN.md
|   |-- FOGLIGHT_V2_PLANNING_RESEARCH.md
|   |-- PERFORMANCE_BUDGETS.md
|   |-- PHASE10_HARDENING.md
|   |-- MAP_ASSETS.md
|   |-- RELEASE_EVIDENCE.md
|   |-- RELEASE_CHECKLIST.md
|   |-- REPOSITORY_SETUP.md
|   |-- V2_API.md
|   `-- WCAG_2_2_REVIEW.md
|-- web/
|   |-- assets/
|   |   `-- natural-earth-110m-countries.v5.1.1.geojson
|   |-- vendor/
|   |   `-- leaflet/
|   |-- api.js
|   |-- app.js
|   |-- community.js
|   |-- core.js
|   |-- incident-drawer.css
|   |-- incident-drawer.js
|   |-- incident-model.js
|   |-- map-model.js
|   |-- map-v2.js
|   |-- overview-model.js
|   |-- overview.js
|   |-- overview.css
|   |-- settings.js
|   |-- store.js
|   `-- tickers.js
|-- scripts/
|   |-- check_live_sources.py
|   |-- assert_packaged_profile.mjs
|   |-- scan_secrets.py
|   |-- smoke_packaged_offline.py
|   |-- smoke_packaged_release.py
|   |-- measure_baseline.py
|   |-- measure_browser_baseline.mjs
|   |-- build_map_assets.py
|   `-- run_test_server.py
|-- tests/
|   |-- browser/
|   |-- fixtures/
|   |-- js/
|   |-- conftest.py
|   |-- test_frontend_contract.py
|   |-- test_native.py
|   |-- test_build_windows.py
|   |-- test_provider_contracts.py
|   `-- test_server_security.py
|-- .gitattributes
|-- .gitignore
|-- CHANGELOG.md
|-- CONTRIBUTING.md
|-- CREDITS.md
|-- LICENSE
|-- README.md
|-- SECURITY.md
|-- build_windows.py
|-- foglight_native.py
|-- foglight_native.spec
|-- foglight_server.py
|-- foglight_spec.md
|-- index.html
|-- package-lock.json
|-- package.json
|-- playwright.config.mjs
|-- pyproject.toml
|-- requirements-build.txt
`-- requirements-dev.txt
```

## Important Files

| Path | Purpose |
|---|---|
| `foglight_native.py` | Windows desktop entrypoint. Starts the local server and opens WebView2. |
| `foglight_server.py` | Local HTTP server, API proxy layer, caching, settings, shutdown endpoint. |
| `foglight_core/` | Side-effect-light canonical models, SQLite storage, settings, cache, safe fetch, feed parsing, and provider adapters. |
| `index.html` | Main dashboard HTML and CSS. |
| `web/app.js` | Dashboard orchestration, map, and primary incident views. |
| `web/api.js`, `web/core.js`, `web/settings.js`, `web/store.js` | Tested browser infrastructure and explicit shared state. |
| `web/overview-model.js`, `web/overview.js`, `web/overview.css` | Tested incident filtering/presentation rules, accessible Overview controller, and responsive display modes. |
| `web/incident-model.js`, `web/incident-drawer.js`, `web/incident-drawer.css` | Pure timeline/provenance/briefing rules and the accessible, failure-isolated incident detail drawer. |
| `web/map-model.js`, `web/map-v2.js` | Deterministic geometry/clustering rules and the Canvas-based offline incident map controller. |
| `web/assets/`, `web/vendor/leaflet/` | Pinned Natural Earth world base and reviewed local Leaflet distribution. |
| `web/community.js`, `web/tickers.js` | Optional panel and ticker view controllers. |
| `build_windows.py` | Generates icon/spec and builds the one-file exe. |
| `foglight_native.spec` | PyInstaller spec committed for reproducible release builds. |
| `tests/` | Security-boundary, HTTP, cache, and frontend-contract regression tests. |
| `.github/workflows/ci.yml` | Windows lint, tests, dependency audit, build, and packaged smoke test. |
| `docs/FOGLIGHT_V2_PLANNING_RESEARCH.md` | Verified research, constraints, provider decisions, and target architecture. |
| `docs/FOGLIGHT_V2_EXECUTION_PLAN.md` | Ordered V2 implementation tasklist, phase gates, risks, and 10/10 scorecard. |
| `docs/RELEASE_EVIDENCE.md` | Scorecard-to-test mapping and explicit production artifact closure status. |
| `config/provider_registry.v1.json` | Versioned provider tier, cadence, attribution, terms, and release decisions. |
| `config/data_taxonomy.v1.json` | Versioned categories, retention defaults, and UI lanes. |
| `config/coops_water_level_stations.v1.json` | Bundled, validated active CO-OPS station index used for bounded contextual lookup. |
| `config/correlation_rules.v1.json`, `config/priority_rules.v1.json` | Versioned correlation thresholds and explainable score rules. |
| `docs/CANONICAL_SOURCE_MAPPINGS.md` | Auditable provider-to-Observation field mappings and upstream contracts. |
| `docs/V2_API.md`, `docs/CORRELATION_AND_PRIORITY.md` | Local API, durable cursors, lifecycle, relation, and priority contracts. |
| `docs/MAP_ASSETS.md` | Map dataset provenance, transformation, checksums, licenses, and optional-tile behavior. |
| `scripts/` | Repeatable baselines, secret scan, packaged profile gates, isolated test server, and opt-in live diagnostics. |
| `assets/` | App icon sources generated by the build script. |
| `docs/` | Repo docs for data sources, build, releases, and setup. |

## Ignored Runtime And Build Outputs

These are intentionally not committed:

- `dist/`
- `build/`
- `cache/`
- `state/`
- `logs/`
- `__pycache__/`
- `webview2_data/`
- `node_modules/`
- `test-results/`
- `.coverage` and `coverage.xml`
