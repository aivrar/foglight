"""Application service for canonical ingestion and the local V2 read API."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from .correlation import CorrelationEngine
from .jsonfiles import load_bounded_json
from .models import EventKind, Incident, Observation
from .providers.canonical import CORE_CANONICAL_ADAPTERS, project_legacy_panel
from .scoring import LANES
from .storage import ObservationStore


class QueryError(ValueError):
    pass


class FoglightService:
    DETAIL_OBSERVATION_LIMIT = 200
    def __init__(
        self,
        store: ObservationStore,
        *,
        registry_path: str | Path,
        taxonomy_path: str | Path,
    ) -> None:
        self.store = store
        self.registry = load_bounded_json(registry_path)
        self.taxonomy = load_bounded_json(taxonomy_path)
        self.registry_by_id = {item["id"]: item for item in self.registry["providers"]}
        self.overview_provider_ids = frozenset(CORE_CANONICAL_ADAPTERS)
        for provider_id, metadata in self.registry_by_id.items():
            self.store.register_provider(provider_id, metadata)
        self.engine = CorrelationEngine(store)

    def ingest(self, observation: Observation):
        return self.engine.ingest(observation, now=observation.ingested_at)

    def mark_source_lost(self, provider_id: str, at: str):
        return self.engine.mark_provider_lost(provider_id, now=at)

    @staticmethod
    def _limit(value, *, default=50, maximum=200):
        if value in (None, ""):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise QueryError("limit must be an integer") from error
        if not 1 <= parsed <= maximum:
            raise QueryError(f"limit must be in 1..{maximum}")
        return parsed

    @staticmethod
    def _cursor(value):
        if value in (None, ""):
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise QueryError("cursor must be a non-negative integer") from error
        if parsed < 0:
            raise QueryError("cursor must be a non-negative integer")
        return parsed

    @staticmethod
    def _bbox(value):
        if value in (None, ""):
            return None
        try:
            west, south, east, north = (float(item) for item in str(value).split(","))
        except (TypeError, ValueError) as error:
            raise QueryError("bbox must be west,south,east,north") from error
        if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
            raise QueryError("bbox is outside valid longitude/latitude ranges")
        return west, south, east, north

    def _incident_dict(self, incident, *, include_observations=False, observations=None):
        output = incident.to_dict()
        if observations is None:
            observations = self.store.observations_for_incidents(
                [incident.incident_id], limit_per_incident=self.DETAIL_OBSERVATION_LIMIT
            ).get(incident.incident_id, [])
        output["sources"] = [
            {
                "provider_id": item.provider_id,
                "attribution": self.registry_by_id.get(item.provider_id, {}).get(
                    "attribution", item.provider_id
                ),
                "provider_record_id": item.provider_record_id,
                "source_url": item.source_url,
            }
            for item in observations
        ]
        output["lane"] = incident.priority_components.get(
            "lane", LANES.get(incident.kind.value, "optional_signal")
        )
        locations = [item for item in observations if item.location_name]
        output["location_name"] = (
            max(locations, key=lambda item: (item.ingested_at, item.observation_id)).location_name
            if locations else ""
        )
        output["observation_count"] = len(incident.observation_ids)
        output["observations_truncated"] = len(observations) < len(incident.observation_ids)
        if include_observations:
            output["observations"] = [item.to_dict() for item in observations]
        return output

    def incidents(self, *, limit=None, cursor=None, lane=None, kind=None, bbox=None):
        page_size = self._limit(limit)
        offset = self._cursor(cursor)
        parsed_bbox = self._bbox(bbox)
        if kind not in (None, ""):
            try:
                kind_value = EventKind(kind)
            except ValueError as error:
                raise QueryError("unknown incident kind") from error
        else:
            kind_value = None
        valid_lanes = set(LANES.values())
        if lane not in (None, "") and lane not in valid_lanes:
            raise QueryError("unknown incident lane")
        page, total = self.store.query_incidents(
            limit=page_size,
            offset=offset,
            kind=kind_value.value if kind_value else None,
            lane=lane,
            bbox=parsed_bbox,
        )
        observation_map = self.store.observations_for_incidents(
            [item.incident_id for item in page], limit_per_incident=20
        )
        next_cursor = offset + len(page) if offset + len(page) < total else None
        return {
            "items": [
                self._incident_dict(
                    item, observations=observation_map.get(item.incident_id, [])
                )
                for item in page
            ],
            "next_cursor": next_cursor,
            "total": total,
        }

    def incident_detail(self, incident_id: str):
        incident = self.store.get_incident(incident_id)
        return self._incident_dict(incident, include_observations=True) if incident else None

    def changes(self, *, cursor=None, limit=None):
        parsed_cursor = self._cursor(cursor)
        page_size = self._limit(limit, default=100)
        items = self.store.changes_after(parsed_cursor, limit=page_size)
        incident_ids = [item["incident"]["incident_id"] for item in items]
        observation_map = self.store.observations_for_incidents(
            incident_ids, limit_per_incident=20
        )
        for item in items:
            incident = Incident.from_dict(item["incident"])
            item["incident"] = self._incident_dict(
                incident, observations=observation_map.get(incident.incident_id, [])
            )
        return {
            "items": items,
            "next_cursor": items[-1]["cursor"] if items else parsed_cursor,
        }

    def timeline(self, incident_id: str, *, limit=None):
        if self.store.get_incident(incident_id) is None:
            return None
        return {"items": self.store.timeline(incident_id, limit=self._limit(limit, default=100))}

    def search(self, *, query=None, limit=None):
        value = " ".join(str(query or "").split())
        if not 2 <= len(value) <= 100:
            raise QueryError("q must contain 2..100 characters")
        page_size = self._limit(limit, default=50)
        incidents = self.store.search_incidents(value, limit=page_size)
        observation_map = self.store.observations_for_incidents(
            [item.incident_id for item in incidents], limit_per_incident=20
        )
        return {
            "query": value,
            "items": [
                self._incident_dict(
                    item, observations=observation_map.get(item.incident_id, [])
                )
                for item in incidents
            ],
            "count": len(incidents),
        }

    def source_health(self, provider_id=None):
        if provider_id is not None and provider_id not in self.overview_provider_ids:
            return None
        rows = self.store.source_health(provider_id)
        states = self.store.scheduler_states(provider_id)
        rows = [
            self._health_with_schedule(item, states.get(item["provider_id"]))
            for item in rows
        ]
        if provider_id is not None:
            return rows[0] if rows else self._health_with_schedule({
                "provider_id": provider_id,
                "status": "pending",
                "checked_at": None,
                "latency_ms": None,
                "detail": "not checked",
            }, states.get(provider_id))
        by_id = {
            item["provider_id"]: item
            for item in rows
            if item["provider_id"] in self.overview_provider_ids
        }
        sources = [
            by_id.get(provider_id, self._health_with_schedule({
                "provider_id": provider_id,
                "status": "pending",
                "checked_at": None,
                "latency_ms": None,
                "detail": "not checked",
            }, states.get(provider_id)))
            for provider_id in sorted(self.overview_provider_ids)
        ]
        counts = {}
        for item in sources:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {"counts": counts, "sources": sources}

    def _health_with_schedule(self, health, state=None):
        output = dict(health)
        output["attribution"] = self.registry_by_id.get(
            output.get("provider_id"), {}
        ).get("attribution", output.get("provider_id"))
        state = state or {}

        def timestamp(value):
            if not value:
                return None
            try:
                number = float(value)
                if not (0 <= number <= 253402300799):
                    return None
                return dt.datetime.fromtimestamp(number, tz=dt.UTC).isoformat(
                    timespec="seconds"
                ).replace("+00:00", "Z")
            except (TypeError, ValueError, OverflowError, OSError):
                return None

        try:
            last_success = float(state.get("last_success") or 0)
            if not (0 <= last_success <= 253402300799):
                last_success = 0
        except (TypeError, ValueError, OverflowError):
            last_success = 0
        try:
            failures = max(0, int(state.get("consecutive_failures") or 0))
        except (TypeError, ValueError, OverflowError):
            failures = 0
        etags = state.get("etags")
        output.update(
            consecutive_failures=failures,
            last_attempt_at=timestamp(state.get("last_attempt")),
            last_success_at=timestamp(last_success),
            cached_age_seconds=(
                max(0, int(time.time() - last_success)) if last_success else None
            ),
            next_attempt_at=timestamp(state.get("next_attempt")),
            circuit_open_until=timestamp(state.get("circuit_until")),
            conditional_cache_entries=len(etags) if isinstance(etags, dict) else 0,
        )
        return output

    def bootstrap(self):
        latest = self.store.latest_revision_metadata()
        return {
            "schema_version": 1,
            "incidents": self.incidents(limit=50),
            "taxonomy": self.taxonomy,
            "source_health": self.source_health(),
            **latest,
        }

    def legacy_payload(self, provider_id: str):
        observations = tuple(self.store.observations_for_provider(provider_id, limit=1000))
        return project_legacy_panel(provider_id, observations)
