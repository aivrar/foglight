"""Deterministic incident correlation, lifecycle, and revision generation."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import threading
import unicodedata
from dataclasses import dataclass, replace

from .models import (
    Certainty,
    ChangeType,
    EventKind,
    Incident,
    Observation,
    Relation,
    RelationType,
    Severity,
    Status,
    Urgency,
)
from .scoring import score_observations
from .storage import ObservationStore

CORRELATION_RULE_VERSION = "correlation-v1"
CORRELATION_RULES = {
    "version": CORRELATION_RULE_VERSION,
    "earthquake": {"hours": 0.25, "kilometers": 150},
    "tropical_cyclone": {"hours": 168},
    "tsunami_relation": {"hours": 6, "kilometers": 1000},
    "named_hazard": {"hours": 48, "kilometers": 250},
    "media": {"hours": 36, "similarity": 0.72},
    "coverage_similarity": 0.5,
    "aviation_volcanic_ash_relation": {"hours": 48, "kilometers": 750},
    "aviation_severe_weather_relation": {"hours": 6, "kilometers": 400},
    "declaration_relation": {"padding_hours": 72, "open_end_days": 30},
}

SEVERITY_RANK = {value: index for index, value in enumerate(reversed(tuple(Severity)))}
URGENCY_RANK = {value: index for index, value in enumerate(reversed(tuple(Urgency)))}
CERTAINTY_RANK = {value: index for index, value in enumerate(reversed(tuple(Certainty)))}
CORRELATABLE_KINDS = {
    EventKind.EARTHQUAKE,
    EventKind.TROPICAL_CYCLONE,
    EventKind.TSUNAMI,
    EventKind.WILDFIRE,
    EventKind.VOLCANO,
    EventKind.NATURAL_EVENT,
    EventKind.DISASTER,
    EventKind.NEWS_ITEM,
    EventKind.CONFLICT_REPORT,
    EventKind.HUMANITARIAN_REPORT,
}
DECLARATION_TARGET_KINDS = {
    EventKind.EARTHQUAKE,
    EventKind.WEATHER_ALERT,
    EventKind.TROPICAL_CYCLONE,
    EventKind.TSUNAMI,
    EventKind.VOLCANO,
    EventKind.WILDFIRE,
    EventKind.NATURAL_EVENT,
    EventKind.DISASTER,
}


@dataclass(frozen=True, slots=True)
class CorrelationDecision:
    merge: bool
    rule: str
    evidence: tuple[str, ...] = ()


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", value.lower())
    stop = {"a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "update"}
    return " ".join(word for word in words if word not in stop)


def title_similarity(left: str, right: str) -> float:
    left_tokens = set(normalize_title(left).split())
    right_tokens = set(normalize_title(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _time(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _distance_km(left, right) -> float | None:
    if left is None or right is None:
        return None
    lon1, lat1 = map(math.radians, left)
    lon2, lat2 = map(math.radians, right)
    delta_lon, delta_lat = lon2 - lon1, lat2 - lat1
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(value)))


def _hours(left: Observation, right: Observation) -> float | None:
    left_time = _time(left.event_at or left.source_updated_at or left.effective_at)
    right_time = _time(right.event_at or right.source_updated_at or right.effective_at)
    if left_time is None or right_time is None:
        return None
    return abs((left_time - right_time).total_seconds()) / 3600


def _metric(item: Observation, key: str):
    metric = item.metrics.get(key)
    return metric.value if metric else None


def _aviation_relation_matches(aviation, target, *, hours, distance):
    if hours is None or distance is None:
        return False
    hazard = str(_metric(aviation, "hazard_type") or "").upper()
    if target.kind is EventKind.VOLCANO:
        rule = CORRELATION_RULES["aviation_volcanic_ash_relation"]
        is_ash = "VOLCAN" in hazard or hazard.strip() in {"VA", "ASH"}
        return is_ash and hours <= rule["hours"] and distance <= rule["kilometers"]
    if target.kind is EventKind.WEATHER_ALERT:
        rule = CORRELATION_RULES["aviation_severe_weather_relation"]
        is_severe_weather = any(token in hazard for token in (
            "CONVECT", "THUNDER", "TURB", "ICING", "ICE", "HAIL", "SEV",
        ))
        return (
            is_severe_weather
            and hours <= rule["hours"]
            and distance <= rule["kilometers"]
        )
    return False


def _normalized_area(value):
    words = normalize_title(str(value or ""))
    ignored = {
        "county", "parish", "borough", "census", "area", "municipality",
        "city", "and", "of",
    }
    return " ".join(word for word in words.split() if word not in ignored)


def _declaration_relation_matches(declaration, target):
    if target.kind not in DECLARATION_TARGET_KINDS:
        return False
    state = str(_metric(declaration, "state_code") or "").upper()
    target_states = {
        item.strip().upper()
        for key in ("state_code", "state_codes")
        for item in str(_metric(target, key) or "").split(",")
        if item.strip()
    }
    if not state or state not in target_states:
        return False
    declared_area = str(_metric(declaration, "declared_area") or "")
    if declared_area and "statewide" not in declared_area.lower():
        target_area = str(_metric(target, "affected_area") or target.location_name or "")
        declared_name = _normalized_area(declared_area)
        target_name = _normalized_area(target_area)
        if not declared_name or not target_name or declared_name not in target_name:
            return False
    incident_type = str(_metric(declaration, "incident_type") or "").upper()
    kind_groups = (
        (("EARTHQUAKE",), {EventKind.EARTHQUAKE}),
        (("HURRICANE", "TROPICAL STORM", "TYPHOON"), {EventKind.TROPICAL_CYCLONE}),
        (("TSUNAMI",), {EventKind.TSUNAMI}),
        (("VOLCAN",), {EventKind.VOLCANO}),
        (("FIRE",), {EventKind.WILDFIRE}),
        (
            ("SEVERE STORM", "FLOOD", "TORNADO", "SNOW", "FREEZ", "DROUGHT"),
            {EventKind.WEATHER_ALERT, EventKind.NATURAL_EVENT, EventKind.DISASTER},
        ),
    )
    allowed = next(
        (kinds for tokens, kinds in kind_groups if any(token in incident_type for token in tokens)),
        set(),
    )
    if target.kind not in allowed:
        return False
    begin = _time(str(_metric(declaration, "incident_begin") or "") or None)
    end = _time(str(_metric(declaration, "incident_end") or "") or None)
    target_time = _time(target.event_at or target.source_updated_at or target.effective_at)
    if begin is None or target_time is None:
        return False
    rule = CORRELATION_RULES["declaration_relation"]
    padding = dt.timedelta(hours=rule["padding_hours"])
    latest = (end + padding) if end else (
        begin + dt.timedelta(days=rule["open_end_days"])
    )
    return begin - padding <= target_time <= latest


def correlation_decision(candidate: Observation, member: Observation) -> CorrelationDecision:
    if candidate.observation_id == member.observation_id:
        return CorrelationDecision(True, "exact_observation_id", (candidate.observation_id,))
    if candidate.kind is not member.kind:
        return CorrelationDecision(False, "different_kind")
    hours = _hours(candidate, member)
    distance = _distance_km(candidate.centroid, member.centroid)
    if candidate.kind is EventKind.EARTHQUAKE:
        rule = CORRELATION_RULES["earthquake"]
        merge = hours is not None and hours <= rule["hours"] and distance is not None and distance <= rule["kilometers"]
        return CorrelationDecision(merge, "earthquake_time_distance", (f"hours={hours}", f"km={distance}"))
    if candidate.kind is EventKind.TROPICAL_CYCLONE:
        left = str(_metric(candidate, "storm_name") or candidate.headline)
        right = str(_metric(member, "storm_name") or member.headline)
        merge = (
            normalize_title(left) == normalize_title(right)
            and hours is not None
            and hours <= CORRELATION_RULES["tropical_cyclone"]["hours"]
        )
        return CorrelationDecision(merge, "cyclone_name_time", (normalize_title(left),))
    if candidate.kind is EventKind.TSUNAMI:
        left = _metric(candidate, "relation_candidate")
        right = _metric(member, "relation_candidate")
        return CorrelationDecision(bool(left and left == right), "tsunami_bulletin_series", (str(left),))
    if candidate.kind in {
        EventKind.WILDFIRE, EventKind.VOLCANO, EventKind.NATURAL_EVENT, EventKind.DISASTER,
    }:
        names_match = normalize_title(candidate.headline) == normalize_title(member.headline)
        rule = CORRELATION_RULES["named_hazard"]
        time_match = hours is not None and hours <= rule["hours"]
        distance_match = distance is None or distance <= rule["kilometers"]
        return CorrelationDecision(names_match and time_match and distance_match, "hazard_name_time_distance")
    if candidate.kind in {
        EventKind.NEWS_ITEM, EventKind.CONFLICT_REPORT, EventKind.HUMANITARIAN_REPORT,
    }:
        similarity = title_similarity(candidate.headline, member.headline)
        return CorrelationDecision(
            similarity >= CORRELATION_RULES["media"]["similarity"]
            and hours is not None and hours <= CORRELATION_RULES["media"]["hours"],
            "media_title_similarity",
            (f"similarity={similarity:.3f}",),
        )
    return CorrelationDecision(False, "exact_only")


def _incident_id(observation: Observation) -> str:
    digest = hashlib.sha256(observation.observation_id.encode()).hexdigest()[:24]
    return f"incident:{observation.kind.value}:{digest}"


def _pick(values, ranks):
    return max(values, key=lambda value: ranks[value])


def _status(observations):
    latest = max(
        observations,
        key=lambda item: (item.source_updated_at or item.event_at or item.ingested_at, item.observation_id),
    )
    if latest.status in {Status.CANCELLED, Status.ENDED}:
        return latest.status
    statuses = {item.status for item in observations}
    if statuses & {Status.ACTIVE, Status.UPDATED}:
        return Status.ACTIVE
    return Status.UNKNOWN


class CorrelationEngine:
    def __init__(self, store: ObservationStore) -> None:
        self.store = store
        # Scheduler jobs normalize concurrently. Correlation is a read/modify/
        # revise operation and must be atomic at the engine boundary.
        self._lock = threading.RLock()

    def ingest(self, observation: Observation, *, now: str) -> Incident:
        with self._lock:
            return self._ingest(observation, now=now)

    def _ingest(self, observation: Observation, *, now: str) -> Incident:
        existing = self.store.incident_for_observation(observation.observation_id)
        changed = self.store.upsert_observation(observation)
        existing_lost_sources = {
            item
            for item in str(
                (existing.priority_components.get("lost_sources") if existing else "")
                or ""
            ).split(",")
            if item
        }
        if (
            existing
            and not changed
            and observation.provider_id not in existing_lost_sources
        ):
            return existing
        decision = CorrelationDecision(True, "exact_observation_id") if existing else None
        if existing is None:
            existing, decision = self._find_candidate(observation)
        member_ids = list(existing.observation_ids) if existing else []
        if observation.observation_id not in member_ids:
            member_ids.append(observation.observation_id)
        observations = [self.store.get_observation(item) for item in member_ids]
        members = [item for item in observations if item is not None]
        unavailable_sources = existing_lost_sources
        unavailable_sources.discard(observation.provider_id)
        incident = self._build(
            members,
            previous_incident=existing,
            now=now,
            correlation=decision or CorrelationDecision(False, "new_incident"),
            unavailable_sources=unavailable_sources,
        )
        self.store.upsert_incident(incident)
        return self._apply_cross_kind_relations(incident, observation, now=incident.last_changed_at)

    def _apply_cross_kind_relations(self, incident, observation, *, now):
        if observation.kind not in {
            EventKind.TSUNAMI,
            EventKind.EARTHQUAKE,
            EventKind.NEWS_ITEM,
            EventKind.CONFLICT_REPORT,
            EventKind.HUMANITARIAN_REPORT,
            EventKind.AVIATION_HAZARD,
            EventKind.WEATHER_ALERT,
            EventKind.VOLCANO,
            EventKind.DISASTER_DECLARATION,
            *DECLARATION_TARGET_KINDS,
        }:
            return incident
        if observation.kind is EventKind.TSUNAMI:
            targets = self.store.correlation_incidents(
                include_kinds={
                    EventKind.EARTHQUAKE.value,
                    EventKind.DISASTER_DECLARATION.value,
                }
            )
        elif observation.kind is EventKind.EARTHQUAKE:
            targets = self.store.correlation_incidents(
                include_kinds={
                    EventKind.TSUNAMI.value,
                    EventKind.DISASTER_DECLARATION.value,
                }
            )
        elif observation.kind is EventKind.AVIATION_HAZARD:
            targets = self.store.correlation_incidents(include_kinds={
                EventKind.WEATHER_ALERT.value,
                EventKind.VOLCANO.value,
            })
        elif observation.kind in {EventKind.WEATHER_ALERT, EventKind.VOLCANO}:
            targets = self.store.correlation_incidents(include_kinds={
                EventKind.AVIATION_HAZARD.value,
                EventKind.DISASTER_DECLARATION.value,
            })
        elif observation.kind is EventKind.DISASTER_DECLARATION:
            targets = self.store.correlation_incidents(
                include_kinds={kind.value for kind in DECLARATION_TARGET_KINDS}
            )
        elif observation.kind in DECLARATION_TARGET_KINDS:
            targets = self.store.correlation_incidents(
                include_kinds={EventKind.DISASTER_DECLARATION.value}
            )
        else:
            targets = self.store.correlation_incidents(
                exclude_kinds={
                    EventKind.NEWS_ITEM.value,
                    EventKind.CONFLICT_REPORT.value,
                    EventKind.HUMANITARIAN_REPORT.value,
                }
            )
        evidence = self.store.observations_by_ids(
            member_id for target in targets for member_id in target.observation_ids
        )
        for target in targets:
            if target.incident_id == incident.incident_id or target.kind is incident.kind:
                continue
            members = [evidence[item] for item in target.observation_ids if item in evidence]
            if not members:
                continue
            representative = max(
                members,
                key=lambda item: item.source_updated_at or item.event_at or item.ingested_at,
            )
            hours = _hours(observation, representative)
            distance = _distance_km(observation.centroid, representative.centroid)
            if observation.kind is EventKind.TSUNAMI and target.kind is EventKind.EARTHQUAKE:
                rule = CORRELATION_RULES["tsunami_relation"]
                if hours is not None and hours <= rule["hours"] and (
                    distance is None or distance <= rule["kilometers"]
                ):
                    incident = self.relate(
                        incident, target, RelationType.CAUSED_BY, now=now
                    )
            elif observation.kind is EventKind.EARTHQUAKE and target.kind is EventKind.TSUNAMI:
                rule = CORRELATION_RULES["tsunami_relation"]
                if hours is not None and hours <= rule["hours"] and (
                    distance is None or distance <= rule["kilometers"]
                ):
                    self.relate(target, incident, RelationType.CAUSED_BY, now=now)
            elif observation.kind in {
                EventKind.NEWS_ITEM,
                EventKind.CONFLICT_REPORT,
                EventKind.HUMANITARIAN_REPORT,
            } and target.kind not in {
                EventKind.NEWS_ITEM,
                EventKind.CONFLICT_REPORT,
                EventKind.HUMANITARIAN_REPORT,
            }:
                if (
                    hours is not None
                    and hours <= CORRELATION_RULES["media"]["hours"]
                    and title_similarity(observation.headline, representative.headline)
                    >= CORRELATION_RULES["coverage_similarity"]
                ):
                    incident = self.relate(
                        incident, target, RelationType.COVERAGE_OF, now=now
                    )
            elif observation.kind is EventKind.AVIATION_HAZARD:
                if _aviation_relation_matches(
                    observation, representative, hours=hours, distance=distance
                ):
                    incident = self.relate(
                        incident, target, RelationType.RELATED_TO, now=now
                    )
            elif (
                observation.kind in {EventKind.WEATHER_ALERT, EventKind.VOLCANO}
                and target.kind is EventKind.AVIATION_HAZARD
            ):
                if _aviation_relation_matches(
                    representative, observation, hours=hours, distance=distance
                ):
                    self.relate(
                        target, incident, RelationType.RELATED_TO, now=now
                    )
            elif observation.kind is EventKind.DISASTER_DECLARATION:
                if _declaration_relation_matches(observation, representative):
                    incident = self.relate(
                        incident, target, RelationType.RELATED_TO, now=now
                    )
            elif target.kind is EventKind.DISASTER_DECLARATION:
                if _declaration_relation_matches(representative, observation):
                    self.relate(
                        target, incident, RelationType.RELATED_TO, now=now
                    )
        return incident

    def _find_candidate(self, observation):
        if observation.kind not in CORRELATABLE_KINDS:
            return None, None
        candidates = []
        incidents = self.store.correlation_incidents(
            include_kinds={observation.kind.value}
        )
        evidence = self.store.observations_by_ids(
            member_id for incident in incidents for member_id in incident.observation_ids
        )
        for incident in incidents:
            for member_id in incident.observation_ids:
                member = evidence.get(member_id)
                if member is None:
                    continue
                decision = correlation_decision(observation, member)
                if decision.merge:
                    candidates.append((incident.incident_id, incident, decision))
                    break
        if not candidates:
            return None, None
        _identifier, incident, decision = min(candidates, key=lambda item: item[0])
        return incident, decision

    def _build(
        self,
        observations,
        *,
        previous_incident,
        now,
        correlation,
        unavailable_sources=(),
    ):
        observations = sorted(observations, key=lambda item: item.observation_id)
        available = [
            item for item in observations if item.provider_id not in unavailable_sources
        ]
        if not available:
            raise ValueError("incident rebuild requires an available observation")
        effective_now = max(
            now,
            max(item.ingested_at for item in observations),
            previous_incident.last_changed_at if previous_incident else now,
        )
        latest = max(
            available,
            key=lambda item: (item.source_updated_at or item.event_at or item.ingested_at, item.observation_id),
        )
        score = score_observations(available, now=effective_now)
        components = dict(score.components)
        components["correlation_rule"] = correlation.rule
        components["correlation_version"] = CORRELATION_RULE_VERSION
        components["correlation_evidence"] = ";".join(correlation.evidence)[:500]
        if unavailable_sources:
            components["lost_sources"] = ",".join(sorted(unavailable_sources))
        status = _status(available)
        if previous_incident is None:
            change_type = ChangeType.NEW
        elif status is Status.CANCELLED:
            change_type = ChangeType.CANCELLED
        elif status is Status.ENDED:
            change_type = ChangeType.RESOLVED
        elif previous_incident.change_type is ChangeType.SOURCE_LOST:
            change_type = ChangeType.UPDATED
        elif score.total >= previous_incident.priority_score + 10:
            change_type = ChangeType.ESCALATED
        elif score.total <= previous_incident.priority_score - 10:
            change_type = ChangeType.DOWNGRADED
        else:
            change_type = ChangeType.UPDATED
        first_seen = (
            previous_incident.first_seen_at if previous_incident
            else min(item.ingested_at for item in observations)
        )
        geometry_source = max(
            (item for item in available if item.geometry),
            key=lambda item: (
                item.source_updated_at or item.event_at or item.ingested_at,
                item.observation_id,
            ),
            default=latest,
        )
        return Incident(
            incident_id=previous_incident.incident_id if previous_incident else _incident_id(observations[0]),
            kind=latest.kind,
            headline=latest.headline,
            summary=latest.summary,
            status=status,
            severity=_pick((item.severity for item in available), SEVERITY_RANK),
            urgency=_pick((item.urgency for item in available), URGENCY_RANK),
            certainty=_pick((item.certainty for item in available), CERTAINTY_RANK),
            priority_score=score.total,
            priority_components=components,
            first_seen_at=first_seen,
            last_changed_at=effective_now,
            last_observed_at=max(item.ingested_at for item in observations),
            observation_ids=tuple(item.observation_id for item in observations),
            change_type=change_type,
            revision=previous_incident.revision + 1 if previous_incident else 1,
            geometry=geometry_source.geometry,
            relations=previous_incident.relations if previous_incident else (),
        )

    def relate(self, source: Incident, target: Incident, relation_type: RelationType, *, now: str):
        with self._lock:
            return self._relate(source, target, relation_type, now=now)

    def _relate(self, source: Incident, target: Incident, relation_type: RelationType, *, now: str):
        if source.kind is target.kind:
            raise ValueError("same-kind incidents should be correlated, not related")
        relation = Relation(relation_type, target.incident_id)
        relations = tuple(dict.fromkeys((*source.relations, relation)))
        if relations == source.relations:
            return source
        updated = replace(
            source,
            relations=relations,
            revision=source.revision + 1,
            last_changed_at=max(now, source.last_changed_at),
            change_type=ChangeType.UPDATED,
        )
        self.store.upsert_incident(updated)
        return updated

    def mark_source_lost(
        self, incident: Incident, provider_id: str, *, now: str
    ) -> Incident:
        with self._lock:
            return self._mark_source_lost(incident, provider_id, now=now)

    def mark_provider_lost(self, provider_id: str, *, now: str) -> list[Incident]:
        """Atomically mark every current incident supported by a failed source."""
        with self._lock:
            return [
                self._mark_source_lost(incident, provider_id, now=now)
                for incident in self.store.incidents_for_provider(provider_id)
                if incident.status not in {Status.CANCELLED, Status.ENDED}
                if provider_id not in {
                    item
                    for item in str(
                        incident.priority_components.get("lost_sources") or ""
                    ).split(",")
                    if item
                }
            ]

    def _mark_source_lost(
        self, incident: Incident, provider_id: str, *, now: str
    ) -> Incident:
        effective_now = max(now, incident.last_changed_at)
        observations = [
            self.store.get_observation(item) for item in incident.observation_ids
        ]
        if not any(
            item is not None and item.provider_id == provider_id
            for item in observations
        ):
            return incident
        remaining = [
            item for item in observations
            if item is not None and item.provider_id != provider_id
        ]
        components = dict(incident.priority_components)
        lost_sources = {
            item for item in str(components.get("lost_sources") or "").split(",") if item
        }
        lost_sources.add(provider_id)
        components["lost_sources"] = ",".join(sorted(lost_sources))
        available = [
            item for item in remaining if item.provider_id not in lost_sources
        ]
        if available:
            score = score_observations(available, now=effective_now)
            score_components = dict(score.components)
            score_components.update(
                {
                    "correlation_rule": components.get("correlation_rule", "source_lost"),
                    "correlation_version": CORRELATION_RULE_VERSION,
                    "correlation_evidence": components.get("correlation_evidence", ""),
                    "lost_sources": components["lost_sources"],
                }
            )
            priority_score = score.total
            components = score_components
            status = _status(available)
        else:
            priority_score = 0
            components.update(
                impact=0,
                urgency=0,
                freshness=0,
                corroboration=0,
                penalty=-40,
                source_count=0,
            )
            status = Status.UNKNOWN
        updated = replace(
            incident,
            status=status,
            priority_score=priority_score,
            priority_components=components,
            revision=incident.revision + 1,
            last_changed_at=effective_now,
            change_type=ChangeType.SOURCE_LOST,
        )
        self.store.upsert_incident(updated)
        return updated
