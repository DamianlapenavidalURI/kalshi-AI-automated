from __future__ import annotations

from kalshi_weather.system.contracts import CandidateContext
from kalshi_weather.system.web_research import build_market_research_brief, enrich_candidates_with_research


def _candidate(ticker: str, family: str, score: float) -> CandidateContext:
    return CandidateContext(
        market_ticker=ticker,
        market_family=family,
        market={"ticker": ticker, "title": f"{ticker} title", "close_time": "2030-01-01T00:00:00Z"},
        event={"title": f"{ticker} event"},
        orderbook={"yes": [], "no": []},
        horizon_reason="test",
        deterministic_inputs={"prequal_score": score},
    )


def test_enrich_candidates_runs_deep_search_only_for_top_n(monkeypatch) -> None:
    calls: dict[str, bool] = {}

    def _fake_brief(*, market_family: str, event_title: str, market_title: str, close_time: str | None, deep_search: bool, timeout_s: float, **kwargs):  # type: ignore[no-untyped-def]
        _ = market_family, event_title, market_title, close_time, timeout_s, kwargs
        calls[market_title] = deep_search
        return {
            "source_status": [{"source": "fake", "ok": True}],
            "evidence_quality": {"score_0_100": 80.0, "source_count": 1, "source_ok_count": 1},
            "freshness_meta": {"collected_at": "now"},
            "source_reliability": {"fake": 1.0},
        }

    monkeypatch.setattr("kalshi_weather.system.web_research.build_market_research_brief", _fake_brief)
    rows = [
        _candidate("A", "daily_temperature", 90.0),
        _candidate("B", "snow_and_rain", 30.0),
    ]
    out = enrich_candidates_with_research(candidates=rows, top_n_deep_search=1, max_workers=2, timeout_s=0.1)

    assert len(out) == 2
    assert calls["A title"] is True
    assert calls["B title"] is False
    assert out[0].evidence_quality.get("score_0_100") == 80.0


def test_build_market_research_brief_builds_evidence_bundle(monkeypatch) -> None:
    monkeypatch.setattr(
        "kalshi_weather.system.web_research._weather_core",
        lambda **kwargs: {
            "event_day": "2030-01-01",
            "location_guess": "Providence, RI",
            "state_code": "RI",
            "geocode": {"ok": True, "latitude": 41.82, "longitude": -71.41},
            "nws": {},
            "nws_alerts": {},
            "historical_weather": {},
            "entities": [],
            "results": [],
            "source_status": [{"source": "nws_forecast", "ok": True}],
        },
    )
    monkeypatch.setattr(
        "kalshi_weather.system.web_research._family_specific_sources",
        lambda **kwargs: ({}, []),
    )
    monkeypatch.setattr(
        "kalshi_weather.system.web_research._openweather_family_fetch",
        lambda **kwargs: (
            {
                "source": "openweather",
                "fetched_at": "2030-01-01T00:00:00Z",
                "lat": 41.82,
                "lon": -71.41,
                "current": {"temp": 66.0},
                "hourly": [{"temp": 67.0}],
                "daily": [{"temp_max": 72.0, "temp_min": 60.0, "rain": 0.1, "snow": 0.0}],
            },
            {"source": "openweather", "ok": True, "fetched_at": "2030-01-01T00:00:00Z"},
        ),
    )
    row = build_market_research_brief(
        market_family="daily_temperature",
        event_title="Providence weather",
        market_title="Will Providence high exceed 70?",
        market_ticker="KXTEST",
        event_ticker="EVT",
        close_time="2030-01-01T00:00:00Z",
        yes_bid=0.48,
        yes_ask=0.52,
        deep_search=False,
        timeout_s=0.1,
    )
    bundle = row.get("evidence_bundle")
    assert isinstance(bundle, dict)
    assert bundle.get("market_ticker") == "KXTEST"
    assert bundle.get("thesis_key")
