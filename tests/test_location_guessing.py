from __future__ import annotations

from kalshi_weather.system.web_research import _location_guess


def test_location_guess_uses_ticker_hint_for_sfo() -> None:
    location_text, state_code, hints = _location_guess(
        event_title="",
        market_title="Will the maximum temperature be 60-61 on Apr 27, 2026?",
        market_ticker="KXHIGHTSFO-26APR27-B60.5",
        event_ticker="KXHIGHTSFO-26APR27",
    )
    assert location_text == "San Francisco, CA"
    assert state_code == "CA"
    assert "SFO" in hints
