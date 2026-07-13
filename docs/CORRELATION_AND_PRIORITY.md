# Correlation, Lifecycle, and Priority Rules

The machine-readable rules are `config/correlation_rules.v1.json` and
`config/priority_rules.v1.json`. Tests require exact parity with the constants
used by the engine.

## Correlation order

1. An exact observation ID updates its existing incident.
2. Same-kind rules may merge bounded candidates:
   - earthquakes: 15 minutes and 150 km;
   - cyclones: normalized storm name within seven days;
   - tsunami messages: exact bulletin-series identity;
   - named hazards: exact normalized name, 48 hours, and 250 km;
   - media only: Jaccard title similarity of at least 0.72 within 36 hours.
3. Different kinds never merge. Nearby earthquake/tsunami incidents receive a
   `caused_by` relation; sufficiently similar media receives `coverage_of`.
4. Ambiguous candidates remain separate. Deterministic incident IDs and
   lexical tie-breaking produce stable results across restarts.

Every revision records the correlation rule/version/evidence and one of:
`new`, `updated`, `escalated`, `downgraded`, `resolved`, `cancelled`, or
`source_lost`. A recovered source creates a normal update. Losing one of
several sources removes its corroboration without erasing the incident;
losing the only source marks the incident unknown until recovery.

## Priority

Priority is a display-order score, not a prediction or objective risk claim.
It is capped to 0..100 and returns every component:

- impact from exact CAP severity: 0..40;
- urgency: 0..20;
- freshness: 0..15;
- independent-provider corroboration: 0..15;
- watch relevance: 0..10 (used when local watch regions arrive);
- terminal or stale penalty: 0 to -40.

Unknown severity contributes zero rather than being treated as safe. Provider
volume is deduplicated, media stays in the world-context lane, and a media
spike cannot manufacture observed certainty or a life-safety score.
