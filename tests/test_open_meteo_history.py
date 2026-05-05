from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_weather.tools import open_meteo


def test_history_brief_clamps_future_event_day(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_http_get_json(url: str, *, params: dict[str, object] | None = None, timeout_s: float = 8.0):  # type: ignore[no-untyped-def]
        _ = timeout_s
        captured["url"] = url
        captured["params"] = dict(params or {})
        return {
            "daily": {
                "time": [],
                "temperature_2m_max": [],
                "temperature_2m_min": [],
                "precipitation_sum": [],
            }
        }

    monkeypatch.setattr(open_meteo, "cached_loader", lambda *, key, ttl_s, loader: loader())
    monkeypatch.setattr(open_meteo, "http_get_json", _fake_http_get_json)

    open_meteo.history_brief(
        lat=39.7392,
        lon=-104.9847,
        event_day="2099-01-01",
        timeout_s=0.1,
    )

    params = captured["params"]
    assert isinstance(params, dict)
    latest_available_day = datetime.now(timezone.utc).date() - timedelta(days=1)
    assert params.get("end_date") == latest_available_day.isoformat()
