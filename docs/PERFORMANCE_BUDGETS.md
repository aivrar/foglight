# Performance Budgets

These budgets are release regression limits, not claims about third-party feed
latency. They are deliberately above the measured Phase 0 baseline in
`docs/baselines/phase0-2026-07-10.json` so normal CI variance does not create
noise while material regressions remain visible.

| Metric | Phase 0 evidence | V2 budget |
|---|---:|---:|
| Local server startup to `/api/ping` | 18.4 ms | 250 ms |
| Local endpoint p95, fixture-free shell routes | 13.6 ms worst | 50 ms |
| First shell paint, deterministic browser | 235 ms | 1,000 ms |
| Base map render, deterministic browser | 280 ms | 1,500 ms |
| First incident paint, deterministic browser | 305 ms | 2,000 ms |
| Server resident memory after local samples | 28.4 MiB | 100 MiB |
| Browser used JavaScript heap | 9.5 MiB | 100 MiB |
| Windows executable | 17.0 MiB | 30 MiB |

The scripts print JSON and make no live-provider requests. The separate
`check_live_sources.py --confirm-live` diagnostic is opt-in and must never
become a required CI gate because provider availability is external state.

When a budget is exceeded, record a new baseline only after explaining the
user-visible benefit and reviewing memory, startup, and packaged size together.

## Phase 10 release candidate

The representative-Windows release-candidate run is recorded in
`baselines/phase10-2026-07-11.json`. It uses 100 local HTTP samples and 20
fresh Chromium pages. Every budget passes at p95 where repeated samples apply:
12.732 ms local startup, 25.636 ms worst local-route p95, 331.277 ms shell p95,
490.04 ms map p95, 501.789 ms incident p95, 33.8 MiB server RSS, 10 MB browser
heap p95, and an 18.1 MiB executable. The separately reported 679.697 ms
process-spawn measurement includes launching system Python and is not the
in-process local-server startup metric defined by the budget.
