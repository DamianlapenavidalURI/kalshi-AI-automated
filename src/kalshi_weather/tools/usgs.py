from __future__ import annotations

from typing import Any

from kalshi_weather.tools.http_cache import cached_loader, http_get_json


def all_day_quakes(*, timeout_s: float = 8.0) -> dict[str, Any]:
    return cached_loader(
        key="usgs_all_day_quakes",
        ttl_s=600,
        loader=lambda: http_get_json(
            "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
            timeout_s=timeout_s,
        ),
    )
