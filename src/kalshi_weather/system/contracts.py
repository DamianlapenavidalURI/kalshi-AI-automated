from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class CandidateContext:
    market_ticker: str
    market_family: str
    market: dict[str, Any]
    event: dict[str, Any]
    orderbook: dict[str, Any]
    horizon_reason: str
    web_research: dict[str, Any] = field(default_factory=dict)
    evidence_quality: dict[str, Any] = field(default_factory=dict)
    freshness_meta: dict[str, Any] = field(default_factory=dict)
    source_reliability: dict[str, Any] = field(default_factory=dict)
    deterministic_inputs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EntryScoutOutput:
    keep: bool
    priority_0_100: float
    reason: str


@dataclass(slots=True)
class EntryEdgeOutput:
    edge_yes_prob: float
    confidence_0_1: float
    side: Literal["yes", "no"]
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EntryFusionOutput:
    proceed: bool
    trust_score_0_100: float
    side: Literal["yes", "no"]
    max_contracts: float
    rationale: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EntryFinalDecision:
    decision: Literal["ENTER", "SKIP", "WAIT", "REDUCE_SIZE"]
    confidence_score_0_1: float
    recommended_side: Literal["yes", "no"]
    recommended_size: float
    reasoning_summary: str
    key_risks: list[str] = field(default_factory=list)
    repeat_bet_assessment: str = ""
    exposure_assessment: str = ""
    required_follow_up_checks: list[str] = field(default_factory=list)
    structured_rejection_reasons: list[str] = field(default_factory=list)


