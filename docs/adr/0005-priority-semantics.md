# ADR 0005: Explainable Priority Semantics

Status: accepted

## Context

Provider labels are not interchangeable: CAP severity, urgency, and certainty
have separate meanings, and different hazard domains use different scales.

## Decision

Preserve source semantics as separate normalized fields. Compute Foglight
priority from documented, versioned rules over impact, urgency, certainty,
freshness, proximity, and corroboration. Return the score version and a list of
human-readable contributing factors with every score. Unknown values remain
unknown rather than being promoted to an emergency.

## Consequences

Users can inspect why an incident ranks highly. Rule changes require fixtures,
complete branch coverage, migration/version notes, and before/after examples.
