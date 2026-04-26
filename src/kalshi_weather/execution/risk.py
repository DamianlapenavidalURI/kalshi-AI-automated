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


def evaluate_intent(
    intent: OrderIntent,
    *,
    limits: RiskLimits,
    ctx: RiskContext,
    category_batch_spent_dollars: dict[str, float] | None = None,
    event_batch_spent_dollars: dict[str, float] | None = None,
) -> list[str]:
    reasons: list[str] = []
    m = ctx.markets_by_ticker.get(intent.ticker)
    if not m:
        reasons.append("missing_market_metadata")
        return reasons

    if market_blocked_for_scalar_combo(m, allow=limits.allow_scalar_and_combo):
        reasons.append("scalar_or_combo_blocked")

    cnt = abs(_f(intent.count_fp))
    pos = _market_position_fp(ctx.positions, intent.ticker)
    if limits.per_market_max_contracts is not None:
        # Conservative: current net YES position + new BUY YES increases long exposure
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
