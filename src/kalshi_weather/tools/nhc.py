from __future__ import annotations

from typing import Any

from kalshi_weather.tools.http_cache import cached_loader, http_get_json


def current_storms(*, timeout_s: float = 8.0) -> dict[str, Any]:
    return cached_loader(
        key="nhc_current_storms",
        ttl_s=600,
        loader=lambda: http_get_json("https://www.nhc.noaa.gov/CurrentStorms.json", timeout_s=timeout_s),
    )
