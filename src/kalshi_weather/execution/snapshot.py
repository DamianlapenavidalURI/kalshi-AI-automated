from __future__ import annotations

from typing import Any

from kalshi_weather.kalshi.client import KalshiClient


def fetch_fills_since(client: KalshiClient, *, min_ts: int, limit: int = 500) -> list[dict[str, Any]]:
    data = client.get_fills(limit=limit, min_ts=min_ts)
    fills = data.get("fills")
    return fills if isinstance(fills, list) else []


def fetch_recent_fills_for_rolling_window(
    client: KalshiClient,
    *,
    now_ts: float,
    lookback_extra_s: float = 5.0,
) -> list[dict[str, Any]]:
    """Pull fills since slightly before the 15s rolling window for client-side checks."""
    min_ts = int(now_ts - 15.0 - lookback_extra_s)
    return fetch_fills_since(client, min_ts=min_ts)


def fetch_portfolio_positions(client: KalshiClient) -> dict[str, Any]:
    return client.get_positions(limit=500)
