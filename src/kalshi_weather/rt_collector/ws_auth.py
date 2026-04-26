"""Kalshi WebSocket handshake authentication (RSA-PSS, same path rule as REST)."""

from __future__ import annotations

from kalshi_weather.kalshi.auth import KalshiAuth

# Path used for signing the WS upgrade (must match the HTTP path component of the WS URL).
KALSHI_WS_SIGN_PATH = "/trade-api/ws/v2"


def kalshi_ws_handshake_headers(auth: KalshiAuth) -> list[tuple[str, str]]:
    headers = auth.create_auth_headers(method="GET", url=KALSHI_WS_SIGN_PATH)
    return list(headers.items())
