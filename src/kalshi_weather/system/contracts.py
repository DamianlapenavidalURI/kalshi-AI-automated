from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class EvidenceBundle:
    market_ticker: str = ""
    family: str = ""
    market_title: str = ""
    event_ticker: str = ""
    close_time: str = ""
    yes_bid: float | None = None
    yes_ask: float | None = None
    implied_probability: float | None = None
    forecast_value: float | None = None
    threshold: float | None = None
    model_probability: float | None = None
    edge: float | None = None
    confidence: float = 0.0
    uncertainty: float = 1.0
    agreement_score: float = 0.0
    sources: list[dict[str, Any]] = field(default_factory=list)
    data_freshness_seconds: float | None = None
    thesis_key: str = ""
    timestamp: str = field(default_factory=_utc_now_iso)
    evidence_hash: str = ""


@dataclass(slots=True)
class HardRailFailure:
    rail_name: str
    severity: Literal["info", "warning", "critical"]
    current_value: Any
    threshold: Any
    hard_blocking: bool
    explanation: str


def make_thesis_key(
    *,
    market_ticker: str,
    family: str,
    threshold: float | None,
    side_hint: str = "",
) -> str:
    basis = {
        "market_ticker": str(market_ticker or "").strip().upper(),
        "family": str(family or "").strip().lower(),
        "threshold": None if threshold is None else round(float(threshold), 4),
        "side_hint": str(side_hint or "").strip().lower(),
    }
    raw = json.dumps(basis, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def compute_evidence_hash(bundle: EvidenceBundle) -> str:
    source_timestamps: list[str] = []
    for source in bundle.sources:
        if not isinstance(source, dict):
            continue
        for key in ("fetched_at", "timestamp", "updated_at"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                source_timestamps.append(value.strip())
                break
    fingerprint = {
        "forecast_value": None if bundle.forecast_value is None else round(bundle.forecast_value, 4),
        "threshold": None if bundle.threshold is None else round(bundle.threshold, 4),
        "model_probability": None if bundle.model_probability is None else round(bundle.model_probability, 4),
        "edge": None if bundle.edge is None else round(bundle.edge, 4),
        "source_timestamps": sorted(source_timestamps),
        "implied_probability": None
        if bundle.implied_probability is None
        else round(bundle.implied_probability, 4),
        "yes_bid": None if bundle.yes_bid is None else round(bundle.yes_bid, 4),
        "yes_ask": None if bundle.yes_ask is None else round(bundle.yes_ask, 4),
    }
    raw = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    evidence_bundle: EvidenceBundle | None = None
    market_state: dict[str, Any] = field(default_factory=dict)
    thesis_state: dict[str, Any] = field(default_factory=dict)
    recent_decisions: list[dict[str, Any]] = field(default_factory=list)
    exposure_context: dict[str, Any] = field(default_factory=dict)


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
    repeat_flag: bool = False
    exposure_flag: bool = False
    novelty_assessment: str = ""
    rejection_reasons: list[str] = field(default_factory=list)


