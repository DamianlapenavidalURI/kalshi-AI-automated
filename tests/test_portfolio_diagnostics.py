from __future__ import annotations

from kalshi_weather.system.orchestrator import _portfolio_diagnostics


def test_portfolio_diagnostics_ignores_zero_positions() -> None:
    payload = {
        "market_positions": [
            {"ticker": "A", "position_fp": "0", "market_exposure_dollars": "100"},
            {"ticker": "B", "position_fp": 0, "market_exposure_dollars": 0},
            {"ticker": "C", "position_fp": None, "market_exposure_dollars": "0"},
        ]
    }
    out = _portfolio_diagnostics(payload)
    assert out["open_positions_seen"] == 0
    assert out["total_abs_contracts"] == 0.0
    assert out["total_abs_exposure_dollars"] == 0.0


def test_portfolio_diagnostics_counts_nonzero_positions() -> None:
    payload = {
        "market_positions": [
            {"ticker": "A", "position_fp": "2", "market_exposure_dollars": "-12.5"},
            {"ticker": "B", "position_fp": "-3", "market_exposure_dollars": "18.0"},
            {"ticker": "C", "position_fp": "0", "market_exposure_dollars": "999"},
        ]
    }
    out = _portfolio_diagnostics(payload)
    assert out["open_positions_seen"] == 2
    assert out["total_abs_contracts"] == 5.0
    assert out["total_abs_exposure_dollars"] == 30.5
