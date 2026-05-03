from __future__ import annotations

from typing import Any

from kalshi_weather.tools.openweather import OpenWeatherClient, OpenWeatherConfig


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_openweather_normalizes_and_caches(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def _fake_get(url: str, *, params: dict[str, Any], timeout: float):  # type: ignore[no-untyped-def]
        _ = timeout
        calls.append((url, params))
        if url.endswith("/weather"):
            return _FakeResponse(
                {
                    "coord": {"lat": 41.8, "lon": -71.4},
                    "dt": 1,
                    "timezone": -14400,
                    "name": "Providence",
                    "main": {"temp": 67.1, "feels_like": 66.0, "humidity": 80, "pressure": 1007},
                    "wind": {"speed": 5.0},
                }
            )
        if url.endswith("/forecast"):
            return _FakeResponse(
                {
                    "city": {"name": "Providence", "timezone": -14400, "coord": {"lat": 41.8, "lon": -71.4}},
                    "list": [
                        {
                            "dt": 2,
                            "dt_txt": "2030-01-01 00:00:00",
                            "main": {"temp": 68.2, "temp_min": 67.0, "temp_max": 69.0, "humidity": 79},
                            "wind": {"speed": 6.0},
                            "pop": 0.2,
                            "rain": {"3h": 0.9},
                        },
                        {
                            "dt": 3,
                            "dt_txt": "2030-01-01 03:00:00",
                            "main": {"temp": 69.1, "temp_min": 68.0, "temp_max": 70.0, "humidity": 78},
                            "wind": {"speed": 7.0},
                            "pop": 0.4,
                        },
                    ],
                }
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("kalshi_weather.tools.openweather.requests.get", _fake_get)
    client = OpenWeatherClient(OpenWeatherConfig(api_key="k", ttl_seconds=600))
    first = client.fetch_weather(lat=41.8, lon=-71.4)
    second = client.fetch_weather(lat=41.8, lon=-71.4)

    assert first["source"] == "openweather"
    assert isinstance(first["current"], dict)
    assert isinstance(first["hourly"], list)
    assert isinstance(first["daily"], list)
    assert first["lat"] == 41.8
    assert first["lon"] == -71.4
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert len(calls) == 2
