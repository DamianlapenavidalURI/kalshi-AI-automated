from __future__ import annotations

import uuid
from typing import Any

from kalshi_weather.execution.preflight import _parse_float


def new_client_order_id() -> str:
    return str(uuid.uuid4())


def build_demo_limit_order_request(
    *,
    market_ticker: str,
    side: str,
    limit_price_dollars: str,
    quantity: str,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    """Kalshi POST /portfolio/orders body for a limit order (demo)."""
    px = _parse_float(limit_price_dollars)
    qty = _parse_float(quantity)
    if px is None or qty is None:
        raise ValueError("invalid price or quantity")
    s = side.upper()
    if s not in {"YES", "NO"}:
        raise ValueError("side must be YES or NO")
    cid = client_order_id or new_client_order_id()
    px_s = f"{px:.4f}"
    q_fp = f"{qty:.2f}"
    body: dict[str, Any] = {
        "ticker": market_ticker,
        "action": "buy",
        "side": "yes" if s == "YES" else "no",
        "type": "limit",
        "count_fp": q_fp,
        "client_order_id": cid,
        "time_in_force": "good_till_canceled",
    }
    if s == "YES":
        body["yes_price_dollars"] = px_s
    else:
        body["no_price_dollars"] = px_s
    return body
