"""Kalshi Trade API v2 base URLs for demo and production."""

from __future__ import annotations

from typing import Literal

KalshiEnv = Literal["demo", "prod"]

# REST Trade API v2
KALSHI_DEMO_REST_BASE_URL: str = "https://demo-api.kalshi.co/trade-api/v2"
KALSHI_PROD_REST_BASE_URL: str = "https://api.elections.kalshi.com/trade-api/v2"

# WebSocket Trade API v2
KALSHI_DEMO_WS_BASE_URL: str = "wss://demo-api.kalshi.co/trade-api/ws/v2"
KALSHI_PROD_WS_BASE_URL: str = "wss://api.elections.kalshi.com/trade-api/ws/v2"


def kalshi_rest_base_url(env: KalshiEnv) -> str:
    if env == "demo":
        return KALSHI_DEMO_REST_BASE_URL
    return KALSHI_PROD_REST_BASE_URL


def kalshi_ws_base_url(env: KalshiEnv) -> str:
    if env == "demo":
        return KALSHI_DEMO_WS_BASE_URL
    return KALSHI_PROD_WS_BASE_URL
