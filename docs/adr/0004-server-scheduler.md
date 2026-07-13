# ADR 0004: Server-Side Provider Scheduler

Status: accepted

## Context

Independent browser timers duplicate work across views, hide rate policy, and
make backoff and source health inconsistent.

## Decision

Run provider polling in a server-side registry with one in-flight request per
provider, provider-specific cadence, jitter, cache reuse, exponential backoff,
`Retry-After` support, and circuit breakers. Browser clients poll normalized
local state and never schedule third-party requests.

## Consequences

Rate behavior and health become observable and testable. The scheduler must
start and stop cleanly with the local process and must never block HTTP serving.
