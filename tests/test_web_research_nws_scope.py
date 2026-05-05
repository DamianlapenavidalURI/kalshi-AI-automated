from __future__ import annotations

from kalshi_weather.system import web_research


def test_weather_core_skips_nws_for_non_us_geocode(monkeypatch) -> None:
    monkeypatch.setattr(
        web_research,
        "_geocode_open_meteo",
        lambda query, timeout_s=8.0: {
            "ok": True,
            "query": query,
            "name": "Phnom Penh",
            "country": "Cambodia",
            "country_code": "KH",
            "latitude": 11.7302,
            "longitude": 104.4868,
        },
    )
    monkeypatch.setattr(
        web_research,
        "_open_meteo_history_brief",
        lambda **kwargs: {"ok": True, "daily_rows": []},
    )
    monkeypatch.setattr(web_research, "_entity_news", lambda entities, timeout_s: [])

    def _should_not_be_called(**kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("NWS should be skipped for non-US geocode")

    monkeypatch.setattr(web_research, "_nws_brief", _should_not_be_called)
    monkeypatch.setattr(web_research, "_nws_alerts_brief", _should_not_be_called)

    out = web_research._weather_core(  # noqa: SLF001
        event_title="Highest temperature in LA on May 6, 2026?",
        market_title="Will the high temp in LA be >64 on May 6, 2026?",
        market_ticker="KXHIGHLAX-26MAY06-T64",
        event_ticker="KXHIGHLAX-26MAY06",
        close_time="2026-05-06T22:00:00Z",
        timeout_s=0.1,
    )
    statuses = out.get("source_status")
    assert isinstance(statuses, list)
    status_map = {str(s.get("source") or ""): s for s in statuses if isinstance(s, dict)}
    assert status_map["nws_forecast"].get("ok") is True
    assert status_map["nws_forecast"].get("skipped") is True
    assert status_map["nws_alerts"].get("ok") is True
    assert status_map["nws_alerts"].get("skipped") is True
