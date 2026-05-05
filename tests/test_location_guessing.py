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


def test_location_guess_extracts_city_from_event_title() -> None:
    location_text, state_code, hints = _location_guess(
        event_title="Highest temperature in Las Vegas tomorrow?",
        market_title="Will the high temp in Las Vegas be <72 on May 5, 2026?",
        market_ticker="KXHIGHTLV-26MAY05-T72",
        event_ticker="KXHIGHTLV-26MAY05",
    )
    assert location_text == "Las Vegas, NV"
    assert state_code == "NV"
    assert "Las Vegas, NV" in hints


def test_location_guess_extracts_minneapolis_state() -> None:
    location_text, state_code, hints = _location_guess(
        event_title="Highest temperature in Minneapolis tomorrow?",
        market_title="Will the high temp in Minneapolis be <50 on May 5, 2026?",
        market_ticker="KXHIGHTMIN-26MAY05-B50.5",
        event_ticker="KXHIGHTMIN-26MAY05",
    )
    assert location_text == "Minneapolis, MN"
    assert state_code == "MN"
    assert "Minneapolis, MN" in hints


def test_location_guess_prefers_ticker_hint_for_la() -> None:
    location_text, state_code, hints = _location_guess(
        event_title="Highest temperature in LA on May 6, 2026?",
        market_title="Will the high temp in LA be >64 on May 6, 2026?",
        market_ticker="KXHIGHLAX-26MAY06-T64",
        event_ticker="KXHIGHLAX-26MAY06",
    )
    assert location_text == "Los Angeles, CA"
    assert state_code == "CA"
    assert "Los Angeles, CA" in hints
