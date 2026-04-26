from __future__ import annotations

import base64
from pathlib import Path
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_weather.kalshi.auth import KalshiAuth


def test_create_auth_headers_message_format_and_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    auth = KalshiAuth(key_id="test-key", private_key_pem=private_pem)

    monkeypatch.setattr(time, "time", lambda: 1745000000.123)
    ts = "1745000000123"
    path = "/trade-api/v2/portfolio/balance"
    headers = auth.create_auth_headers(method="get", url=path)
    sig_b64 = headers["KALSHI-ACCESS-SIGNATURE"]
    assert headers["KALSHI-ACCESS-KEY"] == "test-key"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == ts

    message = f"{ts}GET{path}".encode("utf-8")
    sig = base64.b64decode(sig_b64)
    public_key = private_key.public_key()
    public_key.verify(
        sig,
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )


def test_create_auth_headers_strips_query_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """PSS is non-deterministic; both signatures must verify against the same message."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    auth = KalshiAuth(key_id="test-key", private_key_pem=private_pem)
    monkeypatch.setattr(time, "time", lambda: 1 / 1000)
    ts = "1"
    msg = f"{ts}GET/foo".encode("utf-8")
    pub = private_key.public_key()
    for path in ("/foo?bar=baz", "/foo"):
        sig = base64.b64decode(
            auth.create_auth_headers(method="GET", url=path)["KALSHI-ACCESS-SIGNATURE"]
        )
        pub.verify(
            sig,
            msg,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )


@pytest.mark.parametrize(
    "method",
    [
        "get",
        "POST",
    ],
)
def test_headers_include_expected_keys(method: str) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    auth = KalshiAuth(key_id="kid", private_key_pem=private_pem)
    h = auth.create_auth_headers(method=method, url="/x")
    assert h["KALSHI-ACCESS-KEY"] == "kid"
    assert "KALSHI-ACCESS-TIMESTAMP" in h
    assert "KALSHI-ACCESS-SIGNATURE" in h


def test_from_pem_file_constructor(tmp_path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "kalshi.pem"
    key_path.write_bytes(pem)
    auth = KalshiAuth.from_pem_file(api_key_id="kid", private_key_path=key_path)
    headers = auth.create_auth_headers(method="GET", url="/foo")
    assert headers["KALSHI-ACCESS-KEY"] == "kid"
