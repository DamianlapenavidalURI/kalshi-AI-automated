from __future__ import annotations

from typing import Any

from kalshi_weather.execution.models import FillPreview
from kalshi_weather.rt_collector.orderbook import OrderBookState


def orderbook_from_rest_body(market_ticker: str, body: dict[str, Any]) -> OrderBookState:
    return OrderBookState.from_rest_orderbook(market_ticker, body)


def simulate_taker_fill(
    *,
    book: OrderBookState,
    side: str,
    action: str,
    contracts: float,
) -> FillPreview:
    """
    Walk the consolidated book to estimate IOC execution.

    Kalshi books expose YES bids and NO bids; aggressive buys cross the complement side.
    """
    if contracts <= 0:
        return FillPreview(None, 0.0, None, True)

    s = side.lower()
    a = action.lower()
    remaining = contracts
    cost_times_qty = 0.0
    filled = 0.0
    worst: float | None = None

    if a == "buy" and s == "yes":
        # Buy YES: lift implied YES asks built from NO bid levels
        levels: list[tuple[float, float]] = []
        for pk, sz in book.no.items():
            p_no = float(pk)
            ask_yes = 1.0 - p_no
            levels.append((ask_yes, sz))
        levels.sort(key=lambda x: x[0])
        for price, sz in levels:
            if remaining <= 0:
                break
            take = min(remaining, sz)
            cost_times_qty += take * price
            filled += take
            remaining -= take
            worst = price
    elif a == "buy" and s == "no":
        levels = []
        for pk, sz in book.yes.items():
            p_yes = float(pk)
            ask_no = 1.0 - p_yes
            levels.append((ask_no, sz))
        levels.sort(key=lambda x: x[0])
        for price, sz in levels:
            if remaining <= 0:
                break
            take = min(remaining, sz)
            cost_times_qty += take * price
            filled += take
            remaining -= take
            worst = price
    elif a == "sell" and s == "yes":
        levels = sorted(((float(pk), sz) for pk, sz in book.yes.items()), key=lambda x: -x[0])
        for price, sz in levels:
            if remaining <= 0:
                break
            take = min(remaining, sz)
            cost_times_qty += take * price
            filled += take
            remaining -= take
            worst = price
    elif a == "sell" and s == "no":
        levels = sorted(((float(pk), sz) for pk, sz in book.no.items()), key=lambda x: -x[0])
        for price, sz in levels:
            if remaining <= 0:
                break
            take = min(remaining, sz)
            cost_times_qty += take * price
            filled += take
            remaining -= take
            worst = price
    else:
        return FillPreview(None, 0.0, None, False)

    vwap = (cost_times_qty / filled) if filled > 0 else None
    return FillPreview(
        vwap_dollars=vwap,
        filled_contracts=filled,
        worst_price_dollars=worst,
        fully_filled=remaining <= 1e-9 and contracts > 0,
    )


def simulate_post_only_entry(*, limit_price: float, side: str) -> FillPreview:
    """Resting maker orders do not receive immediate matches in this model."""
    _ = (limit_price, side)
    return FillPreview(vwap_dollars=None, filled_contracts=0.0, worst_price_dollars=None, fully_filled=False)
