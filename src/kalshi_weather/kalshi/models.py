"""Normalize Kalshi JSON using only *_fp and *_dollars fields (avoid deprecated liquidity fields)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_DEPRECATED_LIQUIDITY_KEYS = frozenset({"liquidity", "liquidity_dollars"})


def fp_dollars_subset(d: dict[str, Any]) -> dict[str, Any]:
    """Keep keys ending in ``_fp`` or ``_dollars`` only (excludes deprecated liquidity fields)."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k in _DEPRECATED_LIQUIDITY_KEYS:
            continue
        if k.endswith("_fp") or k.endswith("_dollars"):
            out[k] = v
    return out


@dataclass(frozen=True, slots=True)
class MarketMoneyFields:
    """Book / size / volume fields suitable for ranking (no ``liquidity`` / ``liquidity_dollars``)."""

    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_market(cls, market: dict[str, Any]) -> "MarketMoneyFields":
        return cls(raw=fp_dollars_subset(market))


@dataclass(frozen=True, slots=True)
class PortfolioBalanceFpDollars:
    """Parsed balance payload using only *_fp / *_dollars keys."""

    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_balance_response(cls, body: dict[str, Any]) -> "PortfolioBalanceFpDollars":
        return cls(raw=fp_dollars_subset(body))
