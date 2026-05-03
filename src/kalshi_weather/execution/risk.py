from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from kalshi_weather.execution.models import OrderIntent, RiskLimits

_SCALAR_COMBO = re.compile(
    r"\b(scalar|multivariate|combo|parlay|same game|sgp|basket)\b",
    re.I,
)


def _f(x: Any) -> float:
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return 0.0


def market_blocked_for_scalar_combo(market: dict[str, Any], *, allow: bool) -> bool:
    if allow:
        return False
    title = str(market.get("title") or "")
    r1 = str(market.get("rules_primary") or "")
    r2 = str(market.get("rules_secondary") or "")
    blob = f"{title} {r1} {r2}"
    if _SCALAR_COMBO.search(blob):
        return True
    mt = str(market.get("market_type") or market.get("type") or "").lower()
    if "multivariate" in mt or "scalar" in mt:
        return True
    return False


def _parse_fill_ts(f: dict[str, Any]) -> float | None:
    ts = f.get("ts")
    if isinstance(ts, (int, float)):
        t = float(ts)
        # Defensive: some APIs/SDKs surface unix milliseconds.
        if t > 1e12:
            t = t / 1000.0
        return t
    if isinstance(ts, str):
        s = ts.strip()
        if s:
            try:
                t = float(s)
                if t > 1e12:
                    t = t / 1000.0
                return t
            except ValueError:
                pass
    ct = f.get("created_time")
    if isinstance(ct, str):
        try:
            dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def matched_contracts_in_window(fills: list[dict[str, Any]], *, now_ts: float, window_s: float) -> float:
    """Sum matched contracts (absolute) in (now - window, now]."""
    cutoff = now_ts - window_s
    total = 0.0
    for f in fills:
        t = _parse_fill_ts(f)
        if t is None or t < cutoff:
            continue
        total += abs(_f(f.get("count_fp")))
    return total


def _market_position_fp(positions_payload: dict[str, Any], ticker: str) -> float:
    mps = positions_payload.get("market_positions")
    if not isinstance(mps, list):
        return 0.0
    for mp in mps:
        if not isinstance(mp, dict):
            continue
        if str(mp.get("ticker") or "") == ticker:
            return _f(mp.get("position_fp"))
    return 0.0


def _category_exposure_dollars(
    positions_payload: dict[str, Any],
    markets_by_ticker: dict[str, dict[str, Any]],
    category: str,
) -> float:
    mps = positions_payload.get("market_positions")
    if not isinstance(mps, list):
        return 0.0
    s = 0.0
    for mp in mps:
        if not isinstance(mp, dict):
            continue
        t = str(mp.get("ticker") or "")
        m = markets_by_ticker.get(t)
        if not m:
            continue
        if str(m.get("category") or "") != category:
            continue
        s += abs(_f(mp.get("market_exposure_dollars")))
    return s


def _event_exposure_dollars(positions_payload: dict[str, Any], event_ticker: str) -> float:
    eps = positions_payload.get("event_positions")
    if not isinstance(eps, list):
        return 0.0
    for ep in eps:
        if not isinstance(ep, dict):
            continue
        if str(ep.get("event_ticker") or "") == event_ticker:
            return abs(_f(ep.get("event_exposure_dollars")))
    return 0.0


@dataclass(slots=True)
class RiskContext:
    positions: dict[str, Any]
    markets_by_ticker: dict[str, dict[str, Any]]
    recent_fills: list[dict[str, Any]]
    now_ts: float


def _rail_detail(
    *,
    rail_name: str,
    severity: str,
    current_value: Any,
    threshold: Any,
    hard_blocking: bool,
    explanation: str,
) -> dict[str, Any]:
    return {
        "rail_name": rail_name,
        "severity": severity,
        "current_value": current_value,
        "threshold": threshold,
        "hard_blocking": hard_blocking,
        "explanation": explanation,
    }


def _market_liquidity_contracts(market: dict[str, Any]) -> float:
    return max(
        0.0,
        (_f(market.get("yes_bid_size_fp")) or 0.0)
        + (_f(market.get("yes_ask_size_fp")) or 0.0)
        + (_f(market.get("no_bid_size_fp")) or 0.0)
        + (_f(market.get("no_ask_size_fp")) or 0.0),
    )


def _recent_fill_count_same_ticker(
    *, fills: list[dict[str, Any]], ticker: str, now_ts: float, cooldown_s: float
) -> int:
    cutoff = now_ts - cooldown_s
    hits = 0
    for f in fills:
        if not isinstance(f, dict):
            continue
        mt = str(f.get("ticker") or f.get("market_ticker") or "")
        if mt != ticker:
            continue
        t = _parse_fill_ts(f)
        if t is None:
            continue
        if t >= cutoff:
            hits += 1
    return hits


def evaluate_intent_with_details(
    intent: OrderIntent,
    *,
    limits: RiskLimits,
    ctx: RiskContext,
    category_batch_spent_dollars: dict[str, float] | None = None,
    event_batch_spent_dollars: dict[str, float] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    reasons: list[str] = []
    details: list[dict[str, Any]] = []
    m = ctx.markets_by_ticker.get(intent.ticker)
    if not m:
        reasons.append("missing_market_metadata")
        details.append(
            _rail_detail(
                rail_name="market_metadata",
                severity="critical",
                current_value=None,
                threshold="metadata_required",
                hard_blocking=True,
                explanation="Market metadata was missing for this ticker.",
            )
        )
        return reasons, details

    status = str(m.get("status") or "").lower()
    if status not in {"active", "initialized", "open"}:
        reasons.append("market_not_open")
        details.append(
            _rail_detail(
                rail_name="market_open",
                severity="critical",
                current_value=status,
                threshold="active|initialized|open",
                hard_blocking=True,
                explanation="Market status is not open.",
            )
        )

    if limits.min_market_liquidity_contracts is not None:
        liq = _market_liquidity_contracts(m)
        if liq < limits.min_market_liquidity_contracts:
            reasons.append("insufficient_liquidity")
            details.append(
                _rail_detail(
                    rail_name="basic_liquidity",
                    severity="critical",
                    current_value=liq,
                    threshold=limits.min_market_liquidity_contracts,
                    hard_blocking=True,
                    explanation="Visible orderbook liquidity is below configured floor.",
                )
            )

    if limits.repeat_market_cooldown_seconds is not None and limits.repeat_market_cooldown_seconds > 0:
        pos = abs(_market_position_fp(ctx.positions, intent.ticker))
        fills = _recent_fill_count_same_ticker(
            fills=ctx.recent_fills,
            ticker=intent.ticker,
            now_ts=ctx.now_ts,
            cooldown_s=limits.repeat_market_cooldown_seconds,
        )
        if pos > 0.0 or fills > 0:
            reasons.append("repeat_market_cooldown")
            details.append(
                _rail_detail(
                    rail_name="anti_repeat_buffer",
                    severity="critical",
                    current_value={"open_position_contracts": pos, "recent_fill_count": fills},
                    threshold={"cooldown_seconds": limits.repeat_market_cooldown_seconds},
                    hard_blocking=True,
                    explanation="Recent or open same-market exposure triggered anti-repeat guard.",
                )
            )

    # Backward-compatible optional limits; only enforced if explicitly configured.
    if market_blocked_for_scalar_combo(m, allow=limits.allow_scalar_and_combo):
        reasons.append("scalar_or_combo_blocked")

    cnt = abs(_f(intent.count_fp))
    pos = _market_position_fp(ctx.positions, intent.ticker)
    if limits.per_market_max_contracts is not None:
        projected = abs(pos) + cnt
        if projected > limits.per_market_max_contracts + 1e-6:
            reasons.append("per_market_max_contracts")

    cat = str(m.get("category") or "")
    incremental = cnt * _f(intent.limit_price_dollars)
    if limits.per_category_max_exposure_dollars is not None and cat:
        cat_exp = _category_exposure_dollars(ctx.positions, ctx.markets_by_ticker, cat)
        prior_batch = (category_batch_spent_dollars or {}).get(cat, 0.0)
        if cat_exp + prior_batch + incremental > limits.per_category_max_exposure_dollars + 1e-6:
            reasons.append("per_category_max_exposure")

    et = str(m.get("event_ticker") or "")
    if limits.per_event_max_loss_dollars is not None and et:
        ev = _event_exposure_dollars(ctx.positions, et)
        prior_ev = (event_batch_spent_dollars or {}).get(et, 0.0)
        if ev + prior_ev + incremental > limits.per_event_max_loss_dollars + 1e-6:
            reasons.append("per_event_max_loss")

    return reasons, details


def evaluate_intent(
    intent: OrderIntent,
    *,
    limits: RiskLimits,
    ctx: RiskContext,
    category_batch_spent_dollars: dict[str, float] | None = None,
    event_batch_spent_dollars: dict[str, float] | None = None,
) -> list[str]:
    reasons, _ = evaluate_intent_with_details(
        intent,
        limits=limits,
        ctx=ctx,
        category_batch_spent_dollars=category_batch_spent_dollars,
        event_batch_spent_dollars=event_batch_spent_dollars,
    )
    return reasons


def rolling_batch_violation(
    intents: list[OrderIntent],
    *,
    limits: RiskLimits,
    ctx: RiskContext,
) -> bool:
    if limits.rolling_matched_contracts_15s is None:
        return False
    total = sum(abs(_f(i.count_fp)) for i in intents)
    rolled = matched_contracts_in_window(ctx.recent_fills, now_ts=ctx.now_ts, window_s=15.0)
    return rolled + total > limits.rolling_matched_contracts_15s + 1e-6
