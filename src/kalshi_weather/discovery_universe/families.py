from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketFamily:
    """Priority universe slice for candidate discovery."""

    id: str
    priority: int
    description: str
    # Pass-through to GET /series (Kalshi accepts category + tags string)
    series_categories: tuple[str, ...] = ()
    series_tags: tuple[str, ...] = ()
    # Optional GET /milestones filters
    milestone_category: str | None = None
    milestone_competition: str | None = None
    # Boost if series title/ticker matches any substring (case-insensitive)
    title_hints: tuple[str, ...] = ()


# Weather-focused scope split by market archetype.
DEFAULT_FAMILIES: tuple[MarketFamily, ...] = (
    MarketFamily(
        id="hourly_temperature",
        priority=1,
        description="Hourly temperature threshold markets and nowcasts",
        series_categories=("Climate and Weather",),
        series_tags=(),
        milestone_category="Climate and Weather",
        title_hints=("hourly", "temperature", "temp", "degrees", "by ", ":00"),
    ),
    MarketFamily(
        id="daily_temperature",
        priority=2,
        description="Daily high/low temperature settlement markets",
        series_categories=("Climate and Weather",),
        series_tags=(),
        milestone_category="Climate and Weather",
        title_hints=("high temp", "low temp", "today", "tomorrow", "daily", "temperature"),
    ),
    MarketFamily(
        id="snow_and_rain",
        priority=3,
        description="Rain/snow/precipitation amount and occurrence markets",
        series_categories=("Climate and Weather",),
        series_tags=(),
        milestone_category="Climate and Weather",
        title_hints=("rain", "snow", "precip", "inches", "accumulation"),
    ),
    MarketFamily(
        id="hurricanes",
        priority=4,
        description="Hurricane landfall, intensity, and timing markets",
        series_categories=("Climate and Weather",),
        series_tags=(),
        milestone_category="Climate and Weather",
        title_hints=("hurricane", "tropical storm", "storm", "landfall", "nhc"),
    ),
    MarketFamily(
        id="natural_disasters",
        priority=5,
        description="Natural disaster event and impact probability markets",
        series_categories=("Climate and Weather",),
        series_tags=(),
        milestone_category="Climate and Weather",
        title_hints=("earthquake", "wildfire", "flood", "disaster", "eruption", "storm"),
    ),
    MarketFamily(
        id="climate_change",
        priority=6,
        description="Climate trend and climate-policy weather-linked markets",
        series_categories=("Climate and Weather",),
        series_tags=(),
        milestone_category="Climate and Weather",
        title_hints=("climate", "warming", "el nino", "la nina", "co2", "emissions"),
    ),
)


def family_by_id(fid: str) -> MarketFamily | None:
    for f in DEFAULT_FAMILIES:
        if f.id == fid:
            return f
    return None
