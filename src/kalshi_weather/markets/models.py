from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    observed_at: str
    source: str
    event_ticker: str
    market_ticker: str
    series_ticker: str | None
    event_title: str | None
    event_sub_title: str | None
    market_title: str | None
    yes_sub_title: str | None
    no_sub_title: str | None
    market_status: str | None
    event_status: str | None
    yes_bid_dollars: str | None
    yes_ask_dollars: str | None
    no_bid_dollars: str | None
    no_ask_dollars: str | None
    last_price_dollars: str | None
    yes_bid_size_fp: str | None
    yes_ask_size_fp: str | None
    volume_fp: str | None
    volume_24h_fp: str | None
    open_interest_fp: str | None
    liquidity_dollars: str | None
    open_time: str | None
    close_time: str | None
    latest_expiration_time: str | None
    event_meta_json: dict[str, Any]
    market_json: dict[str, Any]

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExcludedMarket:
    observed_at: str
    source: str
    reason: str
    event_ticker: str | None
    market_ticker: str | None
    event_title: str | None
    market_title: str | None
    raw_json: dict[str, Any]

    def to_row(self) -> dict[str, Any]:
        return asdict(self)

