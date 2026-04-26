from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _f(x: Any) -> float:
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return 0.0


def _price_key(p: Any) -> str:
    return f"{_f(p):.6f}".rstrip("0").rstrip(".")


def _parse_level_rows(rows: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price, qty = row[0], row[1]
        pk = _price_key(price)
        out[pk] = out.get(pk, 0.0) + _f(qty)
    return out


@dataclass
class OrderBookState:
    """In-memory aggregated YES / NO bid levels (Kalshi binary representation)."""

    market_ticker: str
    yes: dict[str, float] = field(default_factory=dict)
    no: dict[str, float] = field(default_factory=dict)

    def apply_snapshot_msg(self, msg: dict[str, Any]) -> None:
        m = msg.get("msg")
        if not isinstance(m, dict):
            return
        self.market_ticker = str(m.get("market_ticker") or self.market_ticker)
        self.yes = _parse_level_rows(m.get("yes_dollars_fp"))
        self.no = _parse_level_rows(m.get("no_dollars_fp"))

    def apply_delta_msg(self, msg: dict[str, Any]) -> None:
        m = msg.get("msg")
        if not isinstance(m, dict):
            return
        side = str(m.get("side") or "").lower()
        price = m.get("price_dollars") or m.get("price")
        delta = m.get("delta_fp") or m.get("delta")
        if price is None or delta is None:
            return
        pk = _price_key(price)
        d = _f(delta)
        book = self.yes if side == "yes" else self.no if side == "no" else None
        if book is None:
            return
        book[pk] = book.get(pk, 0.0) + d
        if abs(book[pk]) < 1e-9:
            book.pop(pk, None)

    def to_snapshot_dict(self) -> dict[str, Any]:
        return {
            "market_ticker": self.market_ticker,
            "yes_dollars_fp": sorted(self.yes.items()),
            "no_dollars_fp": sorted(self.no.items()),
        }

    @classmethod
    def from_rest_orderbook(cls, market_ticker: str, body: dict[str, Any]) -> "OrderBookState":
        ob = body.get("orderbook") or body.get("orderbook_fp") or {}
        yes_key = "yes_dollars" if "yes_dollars" in ob else "yes_dollars_fp"
        no_key = "no_dollars" if "no_dollars" in ob else "no_dollars_fp"
        return cls(
            market_ticker=market_ticker,
            yes=_parse_level_rows(ob.get(yes_key)),
            no=_parse_level_rows(ob.get(no_key)),
        )


def snapshot_json_for_persist(state: OrderBookState, *, source: str) -> str:
    return json.dumps({"source": source, "snapshot": state.to_snapshot_dict()}, default=str)
