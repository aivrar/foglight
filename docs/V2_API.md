# Local V2 API

The V2 API is local-only, read-only, and additive. The native executable enables
it by default with `FOGLIGHT_V2_ENABLED=1`; V1 compatibility remains available
through Standard mode and the compatibility routes. An explicit value of `0`
remains an internal rollback/testing override. Responses are capped at 2 MiB
and every collection has a bounded page size.

| Endpoint | Purpose | Query parameters |
|---|---|---|
| `GET /api/app-config` | Internal Overview feature availability and default mode | none |
| `GET /api/v2/bootstrap` | Initial incidents, taxonomy, source health, revision cursor | none |
| `GET /api/v2/incidents` | Priority-ordered incident page | `limit` 1..200, non-negative `cursor`, `lane`, `kind`, `bbox=west,south,east,north` |
| `GET /api/v2/incidents/{id}` | Incident, source references, and bounded observation detail | none |
| `GET /api/v2/incidents/{id}/timeline` | Newest-first immutable revisions | `limit` 1..200 |
| `GET /api/v2/changes` | Changes strictly after a durable cursor | `cursor`, `limit` 1..200 |
| `GET /api/v2/search` | Bounded local search over retained incident facts | `q` 2..100 characters, `limit` 1..200 |
| `GET /api/v2/taxonomy` | Versioned event/lane taxonomy | none |
| `GET /api/v2/source-health` | Summary and every registered source | none |
| `GET /api/v2/source-health/{provider}` | Attempts, success, backoff, circuit, latency, and cache age | none |

Unknown query parameters and invalid values return `400`; missing resources
return `404`; a disabled V2 service returns `503`. Change cursors come from an
AUTOINCREMENT log tied to immutable revisions, so retention and `VACUUM` cannot
reuse a cursor already observed by a client.

Incident detail includes `observation_count` and `observations_truncated`. At
most 200 normalized observations are embedded; raw upstream bodies are never
returned or stored in the incident database. Incident `sources` and source
health entries include both the stable `provider_id` and the registry's
human-readable `attribution`.

Bootstrap includes `last_revision_at` alongside `revision_cursor`, allowing an
offline client to identify the newest durable local snapshot without claiming
that it is live. Search covers retained incident headline, summary, local
location label, kind, and status; it never searches or returns raw upstream
bodies.

The Overview drawer lazy-loads incident detail and at most 200 immutable
revisions only after selection. It renders revisions chronologically even
though the API response is newest-first, rejects invalid revision numbers and
future timestamps from a selected time window, and never mutates the live
incident while a historical revision is previewed. A timeline failure is
isolated from current incident facts; a detail failure leaves the compact Now
card available. Related-incident and per-provider health lookups are deduplicated
and capped at ten each.

The native executable requests the incident-centered browser by default with
`FOGLIGHT_OVERVIEW_ENABLED=1`; it is exposed only while the V2 service is also
available. An explicit `0` is an internal rollback boundary; it does not weaken
the local-only API or state-change token requirements.

When the scheduler owns a provider, its V1 compatibility route projects the
latest normalized local records instead of triggering another upstream call.
