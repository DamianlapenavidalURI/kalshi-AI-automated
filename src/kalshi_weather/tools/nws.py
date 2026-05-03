from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from kalshi_weather.tools.http_cache import cached_loader, http_get_json


def forecast_brief(*, lat: float, lon: float, timeout_s: float = 8.0) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        points = http_get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", timeout_s=timeout_s)
        props = points.get("properties")
        if not isinstance(props, dict):
            return {"ok": False, "error": "nws_missing_properties"}
        forecast_hourly_url = props.get("forecastHourly")
        if not isinstance(forecast_hourly_url, str) or not forecast_hourly_url.strip():
            return {"ok": False, "error": "nws_missing_forecast_hourly"}
        hourly = http_get_json(forecast_hourly_url, timeout_s=timeout_s)
        hprops = hourly.get("properties")
        periods = hprops.get("periods") if isinstance(hprops, dict) else None
        out_periods: list[dict[str, Any]] = []
        if isinstance(periods, list):
            for p in periods[:10]:
                if not isinstance(p, dict):
                    continue
                out_periods.append(
                    {
                        "startTime": p.get("startTime"),
                        "temperature": p.get("temperature"),
                        "temperatureUnit": p.get("temperatureUnit"),
                        "windSpeed": p.get("windSpeed"),
                        "shortForecast": p.get("shortForecast"),
                        "probabilityOfPrecipitation": (
                            p.get("probabilityOfPrecipitation", {}).get("value")
                            if isinstance(p.get("probabilityOfPrecipitation"), dict)
                            else None
                        ),
                    }
                )
        return {
            "ok": True,
            "grid_id": props.get("gridId"),
            "grid_x": props.get("gridX"),
            "grid_y": props.get("gridY"),
            "forecast_periods": out_periods,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    return cached_loader(key=f"nws_forecast::{lat:.4f}:{lon:.4f}", ttl_s=900, loader=_load)


def alerts_brief(*, state_code: str | None, timeout_s: float = 8.0) -> dict[str, Any]:
    if not state_code:
        return {"ok": False, "error": "state_code_unavailable"}

    def _load() -> dict[str, Any]:
        alert_doc = http_get_json(
            "https://api.weather.gov/alerts/active",
            params={"area": state_code.upper()},
            timeout_s=timeout_s,
        )
        features = alert_doc.get("features")
        count = len(features) if isinstance(features, list) else 0
        return {"ok": True, "state": state_code.upper(), "active_alert_count": count}

    return cached_loader(key=f"nws_alerts::{state_code.upper()}", ttl_s=300, loader=_load)
