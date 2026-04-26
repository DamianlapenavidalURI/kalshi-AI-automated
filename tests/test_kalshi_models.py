from __future__ import annotations

from kalshi_weather.kalshi.models import MarketMoneyFields, fp_dollars_subset


def test_fp_dollars_subset_filters_keys() -> None:
    raw = {
        "yes_bid_dollars": "0.48",
        "liquidity_dollars": "999",
        "liquidity": 1,
        "volume_fp": "100",
        "title": "x",
    }
    sub = fp_dollars_subset(raw)
    assert "yes_bid_dollars" in sub
    assert "volume_fp" in sub
    assert "liquidity_dollars" not in sub
    assert "title" not in sub


def test_market_money_fields_drops_deprecated_liquidity() -> None:
    m = MarketMoneyFields.from_market(
        {
            "yes_bid_dollars": "0.1",
            "liquidity_dollars": "500",
            "volume_fp": "10",
        }
    )
    assert "liquidity_dollars" not in m.raw
    assert m.raw.get("volume_fp") == "10"
