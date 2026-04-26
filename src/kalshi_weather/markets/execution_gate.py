from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

def _parse_close_time(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def market_passes_weather_execution_gate(
    market: dict[str, Any],
    *,
    now_utc: datetime,
    max_resolution_hours: float,
) -> tuple[bool, str]:
    category = str(market.get("category") or "").strip().lower()
    title = str(market.get("title") or "").strip().lower()
    series = str(market.get("series_ticker") or "").strip().lower()
    if "weather" not in category and "weather" not in title and "high" not in series and "low" not in series:
        return False, "weather_gate:not_weather_scoped"
    close_dt = _parse_close_time(market.get("close_time"))
    if close_dt is None:
        return False, "weather_gate:missing_close_time"
    if close_dt <= now_utc:
        return False, "weather_gate:close_in_past"
    hours_to_close = (close_dt - now_utc).total_seconds() / 3600.0
    if hours_to_close > float(max_resolution_hours):
        return False, f"weather_gate:long_horizon_{hours_to_close:.1f}h"
    return True, ""


def market_passes_execution_gate(
    market: dict[str, Any],
    *,
    now_utc: datetime | None = None,
    max_resolution_hours: float = 72.0,
) -> tuple[bool, str]:
    now = now_utc or datetime.now(timezone.utc)
    return market_passes_weather_execution_gate(
        market,
        now_utc=now,
        max_resolution_hours=max_resolution_hours,
    )
