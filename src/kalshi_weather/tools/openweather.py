from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class OpenWeatherConfig:
    api_key: str
    ttl_seconds: int = 300
    timeout_seconds: float = 8.0
    current_url: str = "https://api.openweathermap.org/data/2.5/weather"
    forecast_url: str = "https://api.openweathermap.org/data/2.5/forecast"


class OpenWeatherClient:
    """Small cached client for OpenWeather One Call 3.0."""

    def __init__(self, cfg: OpenWeatherConfig) -> None:
        self._cfg = cfg
        self._cache_lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        now = time.time()
        with self._cache_lock:
            row = self._cache.get(key)
        if row is None:
            return None
        ts, payload = row
        if now - ts > max(1, int(self._cfg.ttl_seconds)):
            with self._cache_lock:
                self._cache.pop(key, None)
            return None
        out = dict(payload)
        out["from_cache"] = True
        return out

    def _cache_set(self, key: str, payload: dict[str, Any]) -> None:
        with self._cache_lock:
            self._cache[key] = (time.time(), payload)

    def _request_json(
        self,
        *,
        url: str,
        lat: float,
        lon: float,
        units: str = "imperial",
    ) -> dict[str, Any]:
        params = {
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
            "appid": self._cfg.api_key,
            "units": units,
        }
        response = requests.get(url, params=params, timeout=self._cfg.timeout_seconds)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            message = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    message = str(body.get("message") or "").strip()
            except ValueError:
                message = ""
            details = message or f"http_{response.status_code}"
            raise RuntimeError(f"OpenWeather request failed ({response.status_code}): {details}") from exc
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def fetch_weather(self, *, lat: float, lon: float, units: str = "imperial") -> dict[str, Any]:
        cache_key = f"{lat:.4f}:{lon:.4f}:{units}"
        hit = self._cache_get(cache_key)
        if isinstance(hit, dict):
            return hit

        current_payload = self._request_json(
            url=self._cfg.current_url,
            lat=lat,
            lon=lon,
            units=units,
        )
        forecast_payload = self._request_json(
            url=self._cfg.forecast_url,
            lat=lat,
            lon=lon,
            units=units,
        )
        current_main = current_payload.get("main") if isinstance(current_payload.get("main"), dict) else {}
        current_wind = current_payload.get("wind") if isinstance(current_payload.get("wind"), dict) else {}
        forecast_rows = forecast_payload.get("list") if isinstance(forecast_payload.get("list"), list) else []

        hourly: list[dict[str, Any]] = []
        daily_by_day: dict[str, dict[str, Any]] = {}
        for row in forecast_rows:
            if not isinstance(row, dict):
                continue
            row_main = row.get("main") if isinstance(row.get("main"), dict) else {}
            row_wind = row.get("wind") if isinstance(row.get("wind"), dict) else {}
            rain_3h = _f((row.get("rain") or {}).get("3h")) if isinstance(row.get("rain"), dict) else None
            snow_3h = _f((row.get("snow") or {}).get("3h")) if isinstance(row.get("snow"), dict) else None
            rain_1h = (rain_3h / 3.0) if rain_3h is not None else None
            snow_1h = (snow_3h / 3.0) if snow_3h is not None else None
            hourly.append(
                {
                    "dt": row.get("dt"),
                    "temp": _f(row_main.get("temp")),
                    "pop": _f(row.get("pop")),
                    "humidity": _f(row_main.get("humidity")),
                    "wind_speed": _f(row_wind.get("speed")),
                    "rain_1h": rain_1h,
                    "snow_1h": snow_1h,
                }
            )
            day_key = str(row.get("dt_txt") or "").split(" ")[0]
            if not day_key:
                continue
            agg = daily_by_day.get(day_key)
            temp_min = _f(row_main.get("temp_min"))
            temp_max = _f(row_main.get("temp_max"))
            humidity = _f(row_main.get("humidity"))
            wind_speed = _f(row_wind.get("speed"))
            pop = _f(row.get("pop"))
            rain = rain_3h
            snow = snow_3h
            if agg is None:
                daily_by_day[day_key] = {
                    "dt": row.get("dt"),
                    "temp_min": temp_min,
                    "temp_max": temp_max,
                    "pop": pop,
                    "rain": rain,
                    "snow": snow,
                    "_humidity_sum": humidity or 0.0,
                    "_humidity_count": 1 if humidity is not None else 0,
                    "_wind_sum": wind_speed or 0.0,
                    "_wind_count": 1 if wind_speed is not None else 0,
                }
                continue
            if temp_min is not None:
                prev = _f(agg.get("temp_min"))
                agg["temp_min"] = temp_min if prev is None else min(prev, temp_min)
            if temp_max is not None:
                prev = _f(agg.get("temp_max"))
                agg["temp_max"] = temp_max if prev is None else max(prev, temp_max)
            if pop is not None:
                prev_pop = _f(agg.get("pop"))
                agg["pop"] = pop if prev_pop is None else max(prev_pop, pop)
            if rain is not None:
                agg["rain"] = (_f(agg.get("rain")) or 0.0) + rain
            if snow is not None:
                agg["snow"] = (_f(agg.get("snow")) or 0.0) + snow
            if humidity is not None:
                agg["_humidity_sum"] = float(agg.get("_humidity_sum") or 0.0) + humidity
                agg["_humidity_count"] = int(agg.get("_humidity_count") or 0) + 1
            if wind_speed is not None:
                agg["_wind_sum"] = float(agg.get("_wind_sum") or 0.0) + wind_speed
                agg["_wind_count"] = int(agg.get("_wind_count") or 0) + 1

        daily: list[dict[str, Any]] = []
        for day in sorted(daily_by_day.keys()):
            agg = daily_by_day[day]
            humidity_count = int(agg.get("_humidity_count") or 0)
            wind_count = int(agg.get("_wind_count") or 0)
            daily.append(
                {
                    "dt": agg.get("dt"),
                    "temp_min": _f(agg.get("temp_min")),
                    "temp_max": _f(agg.get("temp_max")),
                    "pop": _f(agg.get("pop")),
                    "rain": _f(agg.get("rain")),
                    "snow": _f(agg.get("snow")),
                    "humidity": (
                        float(agg.get("_humidity_sum") or 0.0) / float(humidity_count)
                        if humidity_count > 0
                        else None
                    ),
                    "wind_speed": (
                        float(agg.get("_wind_sum") or 0.0) / float(wind_count) if wind_count > 0 else None
                    ),
                }
            )

        current_rain_1h = _f((current_payload.get("rain") or {}).get("1h")) if isinstance(current_payload.get("rain"), dict) else None
        current_snow_1h = _f((current_payload.get("snow") or {}).get("1h")) if isinstance(current_payload.get("snow"), dict) else None
        current_coord = current_payload.get("coord") if isinstance(current_payload.get("coord"), dict) else {}
        forecast_city = forecast_payload.get("city") if isinstance(forecast_payload.get("city"), dict) else {}
        forecast_coord = forecast_city.get("coord") if isinstance(forecast_city.get("coord"), dict) else {}
        normalized = {
            "source": "openweather",
            "fetched_at": _utc_now_iso(),
            "lat": _f(current_coord.get("lat")) or _f(forecast_coord.get("lat")) or lat,
            "lon": _f(current_coord.get("lon")) or _f(forecast_coord.get("lon")) or lon,
            "timezone": str(current_payload.get("name") or forecast_city.get("name") or ""),
            "timezone_offset": current_payload.get("timezone") or forecast_city.get("timezone"),
            "current": {
                "dt": current_payload.get("dt"),
                "temp": _f(current_main.get("temp")),
                "feels_like": _f(current_main.get("feels_like")),
                "humidity": _f(current_main.get("humidity")),
                "pressure": _f(current_main.get("pressure")),
                "wind_speed": _f(current_wind.get("speed")),
                "rain_1h": current_rain_1h,
                "snow_1h": current_snow_1h,
            },
            "hourly": hourly[:16],
            "daily": daily[:8],
            "from_cache": False,
        }
        self._cache_set(cache_key, normalized)
        return normalized


_CLIENT_LOCK = threading.Lock()
_CLIENT: OpenWeatherClient | None = None
_CLIENT_KEY: str | None = None


def get_openweather_client(*, api_key: str, ttl_seconds: int = 300, timeout_seconds: float = 8.0) -> OpenWeatherClient:
    global _CLIENT, _CLIENT_KEY
    with _CLIENT_LOCK:
        if _CLIENT is not None and _CLIENT_KEY == api_key:
            return _CLIENT
        _CLIENT = OpenWeatherClient(
            OpenWeatherConfig(api_key=api_key, ttl_seconds=ttl_seconds, timeout_seconds=timeout_seconds)
        )
        _CLIENT_KEY = api_key
        return _CLIENT
