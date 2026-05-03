from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from kalshi_weather.tools.http_cache import cached_loader, http_get_json


def geocode_open_meteo(query: str, *, timeout_s: float = 8.0) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        d = http_get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 1, "language": "en", "format": "json"},
            timeout_s=timeout_s,
        )
        rows = d.get("results")
        if not isinstance(rows, list) or not rows:
            return {"ok": False, "error": "no_geocode_result", "query": query}
        row = rows[0] if isinstance(rows[0], dict) else {}
        lat = row.get("latitude")
        lon = row.get("longitude")
        if lat is None or lon is None:
            return {"ok": False, "error": "missing_lat_lon", "query": query}
        return {
            "ok": True,
            "query": query,
            "name": row.get("name"),
            "admin1": row.get("admin1"),
            "country": row.get("country"),
            "latitude": float(lat),
            "longitude": float(lon),
        }

    return cached_loader(key=f"open_meteo_geocode::{query.lower()}", ttl_s=3600, loader=_load)


def history_brief(*, lat: float, lon: float, event_day: str | None, timeout_s: float = 8.0) -> dict[str, Any]:
    if event_day:
        try:
            end = datetime.fromisoformat(event_day).date() - timedelta(days=1)
        except ValueError:
            end = datetime.now(timezone.utc).date() - timedelta(days=1)
    else:
        end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=6)

    def _load() -> dict[str, Any]:
        d = http_get_json(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "timezone": "UTC",
            },
            timeout_s=timeout_s,
        )
        daily = d.get("daily")
        if not isinstance(daily, dict):
            return {"ok": False, "error": "open_meteo_missing_daily"}
        dates = daily.get("time") if isinstance(daily.get("time"), list) else []
        maxes = daily.get("temperature_2m_max") if isinstance(daily.get("temperature_2m_max"), list) else []
        mins = daily.get("temperature_2m_min") if isinstance(daily.get("temperature_2m_min"), list) else []
        precip = daily.get("precipitation_sum") if isinstance(daily.get("precipitation_sum"), list) else []
        n = min(len(dates), len(maxes), len(mins), len(precip))
        rows: list[dict[str, Any]] = []
        for i in range(n):
            rows.append(
                {
                    "date": dates[i],
                    "temp_max_c": maxes[i],
                    "temp_min_c": mins[i],
                    "precip_mm": precip[i],
                }
            )
        return {"ok": True, "window_start": start.isoformat(), "window_end": end.isoformat(), "daily_rows": rows}

    return cached_loader(
        key=f"open_meteo_history::{lat:.4f}:{lon:.4f}:{event_day or 'none'}",
        ttl_s=21600,
        loader=_load,
    )
