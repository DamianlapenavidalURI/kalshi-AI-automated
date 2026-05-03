from __future__ import annotations

from unittest.mock import MagicMock, patch

from kalshi_weather.kalshi.client import KalshiClient


def test_retries_then_success_on_429() -> None:
    client = KalshiClient(
        base_url="https://demo-api.kalshi.co/trade-api/v2",
        auth=None,
        max_retries=4,
        backoff_initial_s=0.01,
    )
    bad = MagicMock()
    bad.status = 429
    bad.data = b"rate limited"
    good = MagicMock()
    good.status = 200
    good.data = b'{"markets": []}'
    with patch.object(client._sdk, "call_api", side_effect=[bad, good]) as m:
        out = client.request("GET", "/markets", params={"limit": 1})
    assert m.call_count == 2
    assert out == {"markets": []}


def test_post_json_sets_content_type_header() -> None:
    client = KalshiClient(
        base_url="https://demo-api.kalshi.co/trade-api/v2",
        auth=None,
    )
    ok = MagicMock()
    ok.status = 200
    ok.data = b"{}"
    with patch.object(client._sdk, "call_api", return_value=ok) as m:
        client.request("POST", "/portfolio/orders", json={"ticker": "M-1"})
    kwargs = m.call_args.kwargs
    headers = kwargs.get("header_params") or {}
    assert headers.get("Content-Type") == "application/json"
