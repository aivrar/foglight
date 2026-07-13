# ADR 0002: Staged V1 Compatibility

Status: accepted

## Context

The working V1 endpoints and dashboard are the only reliable behavioral
reference while provider, storage, and presentation layers are extracted.

## Decision

Keep every V1 route and payload available until its consumers migrate. Capture
valid, empty, malformed, and error contracts before extraction. Compatibility
wrappers may remain in `foglight_server.py`, but new views consume normalized
V2 APIs. Remove a V1 path only in a documented release with migration notes.

## Consequences

The migration is incremental and reversible. Some temporary duplication is
acceptable; silent payload drift and a big-bang rewrite are not.
