from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ScoreExplanation:
    """Human-readable scoring breakdown (deterministic)."""

    components: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, float] = field(default_factory=dict)
    family_weight: float = 1.0
    notes: list[str] = field(default_factory=list)

    def total_components(self) -> float:
        return sum(self.components.values())

    def total_penalties(self) -> float:
        return sum(self.penalties.values())

    def adjusted_score(self) -> float:
        base = self.total_components() * self.family_weight
        return max(0.0, base + self.total_penalties())

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": dict(self.components),
            "penalties": dict(self.penalties),
            "family_weight": self.family_weight,
            "component_sum": self.total_components(),
            "penalty_sum": self.total_penalties(),
            "adjusted_score": self.adjusted_score(),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class RankedCandidate:
    """One market (+ event context) ready for automation triage."""

    family_id: str
    family_priority: int
    market_ticker: str
    event_ticker: str | None
    series_ticker: str | None
    title: str | None
    status: str | None
    category: str | None
    tags: list[str]
    score: float
    explanation: ScoreExplanation
    market: dict[str, Any]
    event: dict[str, Any] | None
    metadata: dict[str, Any] | None
    hours_to_close: float | None
    milestone_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "family_priority": self.family_priority,
            "market_ticker": self.market_ticker,
            "event_ticker": self.event_ticker,
            "series_ticker": self.series_ticker,
            "title": self.title,
            "status": self.status,
            "category": self.category,
            "tags": self.tags,
            "score": self.score,
            "score_explanation": self.explanation.to_dict(),
            "hours_to_close": self.hours_to_close,
            "milestone_ids": self.milestone_ids,
            "market": self.market,
            "event": self.event,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class DiscoveryResult:
    """Full discovery output."""

    candidates: list[RankedCandidate]
    safe_phase_one: list[RankedCandidate]
    series_seen: int
    events_seen: int
    markets_seen: int
    cache_hits: int
    cache_misses: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_seen": self.series_seen,
            "events_seen": self.events_seen,
            "markets_seen": self.markets_seen,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "candidates": [c.to_dict() for c in self.candidates],
            "safe_phase_one_shortlist": [c.to_dict() for c in self.safe_phase_one],
        }
