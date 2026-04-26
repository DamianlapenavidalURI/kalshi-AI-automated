from __future__ import annotations

from kalshi_weather.system.contracts import CandidateContext
from kalshi_weather.system.web_research import enrich_candidates_with_research


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

    def _fake_brief(*, market_family: str, event_title: str, market_title: str, close_time: str | None, deep_search: bool, timeout_s: float):  # type: ignore[no-untyped-def]
        _ = market_family, event_title, market_title, close_time, timeout_s
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
