from __future__ import annotations

import uuid
from typing import Any

from kalshi_weather.proposals.models import ProposalDraft
from kalshi_weather.proposals.signal_layer import SignalEvaluation, evaluation_to_json_summary


def _parse_dollars(s: Any) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s))
    except ValueError:
        return None


def compute_proposal_quality_score(
    *,
    spread: float,
    max_yes_spread: float,
    snapshot_age_seconds: float,
    snapshot_max_age_minutes: int,
    signal_score: float,
) -> float:
    """
    Deterministic 0–1 score blending spread/staleness with Milestone 7 signal_score (0–100).
    """
    max_age_sec = float(max(1, snapshot_max_age_minutes)) * 60.0
    p_spread = min(1.0, spread / max_yes_spread) if max_yes_spread > 0 else 1.0
    p_age = min(1.0, snapshot_age_seconds / max_age_sec)
    p_sig = 1.0 - min(1.0, signal_score / 100.0)
    score = 1.0 - 0.35 * p_spread - 0.35 * p_age - 0.3 * p_sig
    return max(0.0, min(1.0, round(score, 4)))


def _confidence_from_signal(signal_score: float) -> float:
    """Deterministic mapping 0–100 signal → confidence in [0.18, 0.82]."""
    x = max(0.0, min(100.0, signal_score))
    return round(0.18 + (x / 100.0) * 0.64, 4)


def maybe_propose_from_snapshot(
    *,
    snapshot_id: int | None,
    observed_at: str,
    market_json: dict[str, Any],
    signal_eval: SignalEvaluation,
    max_yes_spread: float = 0.22,
    min_bid: float = 0.02,
    max_ask: float = 0.98,
    snapshot_age_seconds: float = 0.0,
    snapshot_max_age_minutes: int = 30,
) -> tuple[ProposalDraft | None, str | None]:
    """
    Deterministic YES limit-at-yes_ask proposal when the book is usable and `signal_eval` passed
    pre-checks (caller skips earlier when `signal_eval.skip_reason` is set).
    """
    if signal_eval.skip_reason:
        return None, signal_eval.skip_reason

    mt = market_json.get("ticker") or market_json.get("market_ticker")
    if not isinstance(mt, str):
        return None, "missing_ticker"
    evt = market_json.get("event_ticker")
    status = market_json.get("status")
    if status not in {"active", "initialized"}:
        return None, "bad_status"

    yb = _parse_dollars(market_json.get("yes_bid_dollars"))
    ya = _parse_dollars(market_json.get("yes_ask_dollars"))
    if yb is None or ya is None:
        return None, "missing_yes_book"
    if yb < min_bid or ya > max_ask or ya <= yb:
        return None, "invalid_book_bounds"
    spread = ya - yb
    if spread > max_yes_spread:
        return None, "wide_spread"

    mid = (yb + ya) / 2.0
    implied_prob = max(0.0, min(1.0, mid))
    spread_cents = int(round(spread * 100.0))
    spread_dollars = f"{spread:.4f}"
    pq = compute_proposal_quality_score(
        spread=spread,
        max_yes_spread=max_yes_spread,
        snapshot_age_seconds=snapshot_age_seconds,
        snapshot_max_age_minutes=snapshot_max_age_minutes,
        signal_score=signal_eval.signal_score,
    )

    proposal_id = str(uuid.uuid4())
    feat_json = evaluation_to_json_summary(signal_eval)
    conf = _confidence_from_signal(signal_eval.signal_score)

    return (
        ProposalDraft(
            proposal_id=proposal_id,
            market_ticker=mt,
            event_ticker=evt if isinstance(evt, str) else None,
            side="YES",
            confidence=conf,
            reason="m7_deterministic_yes_limit_at_yes_ask_signal_scored",
            observed_at=observed_at,
            source_snapshot_id=snapshot_id,
            proposed_limit_price_dollars=f"{ya:.4f}",
            proposed_quantity="1.00",
            snapshot_market_status=status if isinstance(status, str) else None,
            snapshot_yes_bid=market_json.get("yes_bid_dollars"),
            snapshot_yes_ask=market_json.get("yes_ask_dollars"),
            implied_probability=round(implied_prob, 6),
            spread=spread,
            mid_price=round(mid, 6),
            snapshot_age_seconds=round(snapshot_age_seconds, 3),
            proposal_quality_score=pq,
            implied_probability_yes_mid=round(implied_prob, 6),
            spread_dollars=spread_dollars,
            spread_cents=spread_cents,
            quality_score=pq,
            signal_score=signal_eval.signal_score,
            feature_summary_json=feat_json,
            candidate_quality_bucket=signal_eval.candidate_quality_bucket,
        ),
        None,
    )
