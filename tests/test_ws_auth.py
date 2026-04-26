from __future__ import annotations

from cryptography.hazmat.primitives import serialization

from kalshi_weather.kalshi.auth import KalshiAuth
from kalshi_weather.rt_collector.ws_auth import KALSHI_WS_SIGN_PATH, kalshi_ws_handshake_headers


def test_ws_sign_path_matches_kalshi_docs() -> None:
    assert KALSHI_WS_SIGN_PATH == "/trade-api/ws/v2"


def test_handshake_headers_shape() -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    auth = KalshiAuth(key_id="kid", private_key_pem=pem)
    h = kalshi_ws_handshake_headers(auth)
    names = {x[0] for x in h}
    assert "KALSHI-ACCESS-KEY" in names
    assert "KALSHI-ACCESS-TIMESTAMP" in names
    assert "KALSHI-ACCESS-SIGNATURE" in names
