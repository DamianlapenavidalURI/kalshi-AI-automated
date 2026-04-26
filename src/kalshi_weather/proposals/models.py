from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Side = Literal["YES", "NO"]
GuardOutcome = Literal["approved", "rejected"]


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    proposal_id: str
    market_ticker: str
    event_ticker: str | None
    side: Side
    confidence: float
    reason: str
    observed_at: str
    source_snapshot_id: int | None
    proposed_limit_price_dollars: str
    proposed_quantity: str
    snapshot_market_status: str | None
    snapshot_yes_bid: str | None
    snapshot_yes_ask: str | None
    implied_probability: float
    spread: float
    mid_price: float
    snapshot_age_seconds: float
    proposal_quality_score: float
    implied_probability_yes_mid: float
    spread_dollars: str
    spread_cents: int
    quality_score: float
    signal_score: float = 0.0
    feature_summary_json: str = "{}"
    candidate_quality_bucket: str = "unknown"

    def to_row_meta(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RiskResult:
    outcome: GuardOutcome
    rejection_reason: str | None
    details: dict[str, Any]
