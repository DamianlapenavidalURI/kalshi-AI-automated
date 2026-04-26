from __future__ import annotations

from kalshi_weather.discovery_universe.families import DEFAULT_FAMILIES, family_by_id


def test_weather_default_families_cover_six_market_types() -> None:
    ids = {f.id for f in DEFAULT_FAMILIES}
    assert ids == {
        "hourly_temperature",
        "daily_temperature",
        "snow_and_rain",
        "hurricanes",
        "natural_disasters",
        "climate_change",
    }
    assert family_by_id("weather") is None
