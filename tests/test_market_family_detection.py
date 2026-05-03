from __future__ import annotations

from kalshi_weather.system.datahub import _detect_market_family, _resolved_family_id


def test_detect_temperature_family_from_kxhigh_ticker() -> None:
    market = {
        "ticker": "KXHIGHTSFO-26APR27-B60.5",
        "title": "Will the maximum temperature be 60-61 on Apr 27, 2026?",
        "rules_primary": "Temperature settlement rules.",
    }
    family = _detect_market_family(market=market, event={})
    assert family in {"daily_temperature", "hourly_temperature"}
    assert family != "snow_and_rain"


def test_detect_precip_family_when_precip_signals_dominate() -> None:
    market = {
        "ticker": "KXRAINNYCM-26APR-1",
        "title": "Will rainfall exceed 1 inch today?",
        "rules_primary": "Settles on precipitation.",
    }
    family = _detect_market_family(market=market, event={})
    assert family == "snow_and_rain"


def test_resolved_family_prefers_detected_when_discovery_disagrees() -> None:
    market = {
        "ticker": "KXLOWTDAL-26APR26-B68.5",
        "title": "Will the minimum temperature be 68-69 on Apr 26, 2026?",
        "rules_primary": "Settles on official daily minimum temperature.",
    }
    family = _resolved_family_id(
        discovered_family="hourly_temperature",
        market=market,
        event={"title": "Lowest temperature in Dallas on Apr 26, 2026?"},
    )
    assert family == "daily_temperature"


def test_resolved_family_keeps_discovered_when_detection_unknown() -> None:
    market = {
        "ticker": "GENERIC-UNKNOWN-1",
        "title": "Some custom probability market",
        "rules_primary": "No weather wording here.",
    }
    family = _resolved_family_id(
        discovered_family="natural_disasters",
        market=market,
        event={"title": "Unclear event"},
    )
    assert family == "natural_disasters"
