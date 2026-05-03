from __future__ import annotations

import time

from kalshi_weather.execution.models import OrderIntent, RiskLimits
from kalshi_weather.execution.risk import RiskContext, evaluate_intent_with_details


def test_anti_repeat_hard_rail_blocks_same_market() -> None:
    now = time.time()
    intent = OrderIntent(
        ticker="M-1",
        side="yes",
        action="buy",
        count_fp="1.00",
        policy="taker_ioc",
        limit_price_dollars="0.55",
    )
    limits = RiskLimits(repeat_market_cooldown_seconds=3600, min_market_liquidity_contracts=2.0)
    ctx = RiskContext(
        positions={"market_positions": [{"ticker": "M-1", "position_fp": "0.00"}], "event_positions": []},
        markets_by_ticker={
            "M-1": {
                "ticker": "M-1",
                "status": "active",
                "yes_bid_size_fp": "2",
                "yes_ask_size_fp": "2",
                "no_bid_size_fp": "2",
                "no_ask_size_fp": "2",
            }
        },
        recent_fills=[{"ticker": "M-1", "count_fp": "1", "ts": now - 30}],
        now_ts=now,
    )
    reasons, details = evaluate_intent_with_details(intent, limits=limits, ctx=ctx)
    assert "repeat_market_cooldown" in reasons
    assert any(d.get("rail_name") == "anti_repeat_buffer" for d in details)
