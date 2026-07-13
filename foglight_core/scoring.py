"""Versioned, deterministic, explainable incident priority rules."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .models import Observation, Severity, Status, Urgency, normalize_timestamp

SCORE_RULE_VERSION = "priority-v1"
PRIORITY_RULES = {
    "version": SCORE_RULE_VERSION,
    "freshness_hours": [1, 6, 24, 168],
    "corroboration_per_source": 5,
    "corroboration_cap": 15,
    "terminal_penalty": -40,
    "stale_penalty": -20,
}

SEVERITY_POINTS = {
    Severity.EXTREME: 40,
    Severity.SEVERE: 30,
    Severity.MODERATE: 20,
    Severity.MINOR: 10,
    Severity.UNKNOWN: 0,
}
URGENCY_POINTS = {
    Urgency.IMMEDIATE: 20,
    Urgency.EXPECTED: 15,
    Urgency.FUTURE: 8,
    Urgency.PAST: 0,
    Urgency.UNKNOWN: 0,
}
LANES = {
    "earthquake": "hazards",
    "weather_alert": "hazards",
    "tropical_cyclone": "hazards",
    "tsunami": "hazards",
    "volcano": "hazards",
    "wildfire": "hazards",
    "natural_event": "hazards",
    "disaster": "hazards",
    "disaster_declaration": "hazards",
    "conflict_report": "world_context",
    "humanitarian_report": "world_context",
    "news_item": "world_context",
    "aircraft": "mobility",
    "aviation_hazard": "mobility",
    "marine_observation": "mobility",
    "water_level": "mobility",
    "fireball": "science",
    "space_weather": "science",
    "orbital_position": "science",
    "market_snapshot": "markets",
    "technology_activity": "optional_signal",
}


@dataclass(frozen=True, slots=True)
class ScoreResult:
    total: int
    components: dict[str, int | float | str]


def _parse(value: str) -> dt.datetime:
    normalized = normalize_timestamp(value, required=True)
    return dt.datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def score_observations(
    observations: list[Observation] | tuple[Observation, ...],
    *,
    now: str,
    watch_region_relevance: int = 0,
) -> ScoreResult:
    if not observations:
        raise ValueError("at least one observation is required")
    if not 0 <= watch_region_relevance <= 10:
        raise ValueError("watch relevance must be in 0..10")
    now_value = _parse(now)
    severity = max(SEVERITY_POINTS[item.severity] for item in observations)
    urgency = max(URGENCY_POINTS[item.urgency] for item in observations)
    timestamps = [
        _parse(item.event_at or item.source_updated_at or item.ingested_at)
        for item in observations
    ]
    age_hours = max(0.0, (now_value - max(timestamps)).total_seconds() / 3600)
    hour, six_hours, day, stale_after = PRIORITY_RULES["freshness_hours"]
    freshness = 15 if age_hours <= hour else 10 if age_hours <= six_hours else 5 if age_hours <= day else 0
    independent_sources = len({item.provider_id for item in observations})
    corroboration = min(
        PRIORITY_RULES["corroboration_cap"],
        max(0, independent_sources - 1) * PRIORITY_RULES["corroboration_per_source"],
    )
    latest = max(
        observations,
        key=lambda item: (item.source_updated_at or item.event_at or item.ingested_at, item.observation_id),
    )
    expired = all(item.expires_at and _parse(item.expires_at) <= now_value for item in observations)
    terminal = latest.status in {Status.ENDED, Status.CANCELLED}
    penalty = (
        PRIORITY_RULES["terminal_penalty"]
        if expired or terminal
        else PRIORITY_RULES["stale_penalty"] if age_hours > stale_after else 0
    )
    raw_total = severity + urgency + freshness + corroboration + watch_region_relevance + penalty
    total = max(0, min(100, raw_total))
    lane = LANES.get(observations[0].kind.value, "optional_signal")
    return ScoreResult(
        total,
        {
            "rule_version": SCORE_RULE_VERSION,
            "lane": lane,
            "impact": severity,
            "urgency": urgency,
            "freshness": freshness,
            "corroboration": corroboration,
            "watch_region": watch_region_relevance,
            "penalty": penalty,
            "age_hours": round(age_hours, 3),
            "source_count": independent_sources,
        },
    )
