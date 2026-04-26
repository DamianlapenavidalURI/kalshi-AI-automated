from __future__ import annotations

import pytest

from kalshi_weather.kalshi.env import (
    KALSHI_DEMO_REST_BASE_URL,
    KALSHI_DEMO_WS_BASE_URL,
    KALSHI_PROD_REST_BASE_URL,
    KALSHI_PROD_WS_BASE_URL,
    kalshi_rest_base_url,
    kalshi_ws_base_url,
)


@pytest.mark.parametrize(
    "env,rest,ws",
    [
        (
            "demo",
            "https://demo-api.kalshi.co/trade-api/v2",
            "wss://demo-api.kalshi.co/trade-api/ws/v2",
        ),
        (
            "prod",
            "https://api.elections.kalshi.com/trade-api/v2",
            "wss://api.elections.kalshi.com/trade-api/ws/v2",
        ),
    ],
)
def test_env_urls_match_requirements(env: str, rest: str, ws: str) -> None:
    assert kalshi_rest_base_url(env) == rest  # type: ignore[arg-type]
    assert kalshi_ws_base_url(env) == ws  # type: ignore[arg-type]


def test_constants_exported() -> None:
    assert KALSHI_DEMO_REST_BASE_URL.startswith("https://demo-api.kalshi.co")
    assert KALSHI_DEMO_WS_BASE_URL.startswith("wss://demo-api.kalshi.co")
    assert KALSHI_PROD_REST_BASE_URL.startswith("https://api.elections.kalshi.com")
    assert KALSHI_PROD_WS_BASE_URL.startswith("wss://api.elections.kalshi.com")
