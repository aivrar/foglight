# ADR 0001: SQLite Storage

Status: accepted

## Context

V2 needs bounded observation history, stable updates, spatial queries, and
crash-safe local persistence without requiring a database service or API key.

## Decision

Use the Python standard-library SQLite driver. Enable WAL where supported,
foreign keys, explicit schema migrations, UPSERT by provider identity, and an
RTree index for point/bounding-box queries. Keep raw responses only in the
bounded provider cache; store normalized records and raw fingerprints in the
database. Fall back to ordinary indexed latitude/longitude columns if RTree is
unavailable.

## Consequences

Foglight remains a one-executable local app. Migrations, retention, backup, and
corruption recovery require first-class tests. No server database is introduced.
