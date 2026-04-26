from __future__ import annotations

import json
from pathlib import Path

from kalshi_weather.discovery_universe.models import DiscoveryResult


def export_json(result: DiscoveryResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
