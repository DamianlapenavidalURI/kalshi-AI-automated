from __future__ import annotations

from typing import Any

from kalshi_weather.discovery_universe.families import MarketFamily
from kalshi_weather.discovery_universe.pipeline import DiscoveryOptions, run_discovery


class FakeClient:
    """Minimal stub; only methods used by pipeline are implemented."""

    def __init__(self) -> None:
        self.base_url = "https://demo-api.kalshi.co/trade-api/v2"
        self.auth = None
        self._series_calls = 0

    def get_series(self, **kwargs: Any) -> dict[str, Any]:
        self._series_calls += 1
        if self._series_calls > 1:
            return {"series": [], "cursor": None}
        return {
            "series": [
                {"ticker": "SERMACRO", "title": "Macro"},
            ],
            "cursor": None,
        }

    def get_milestones(self, **kwargs: Any) -> dict[str, Any]:
        return {"milestones": [], "cursor": None}

    def get_events(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "events": [
                {
                    "event_ticker": "EVT-1",
                    "category": "Economics",
                    "markets": [
                        {
                            "ticker": "MKT-1",
                            "title": "Clean binary",
                            "status": "open",
                            "yes_bid_dollars": "0.46",
                            "yes_ask_dollars": "0.49",
                            "yes_bid_size_fp": "10",
                            "yes_ask_size_fp": "10",
                            "volume_24h_fp": "100",
                            "close_time": "2030-06-01T00:00:00Z",
                            "market_type": "binary",
                        }
                    ],
                }
            ],
            "cursor": None,
        }

    def get_event_metadata(self, event_ticker: str) -> dict[str, Any]:
        return {"competition": "Economics", "settlement_sources": [{"x": 1}]}

    def get_markets(self, **kwargs: Any) -> dict[str, Any]:
        return {"markets": []}


def test_run_discovery_smoke() -> None:
    fam = (
        MarketFamily(
            id="macro_releases",
            priority=1,
            description="t",
            series_categories=("Economics",),
            series_tags=("Macroeconomics",),
        ),
    )
    client = FakeClient()
    opts = DiscoveryOptions(
        cache_dir=None,
        max_series_pages_per_tag=1,
        max_events_pages_per_series=1,
        max_milestone_pages=1,
        max_series_per_family=5,
        max_total_candidates=50,
        metadata_top_n=5,
    )
    r = run_discovery(client, families=fam, options=opts)
    assert len(r.candidates) >= 1
    assert r.candidates[0].market_ticker == "MKT-1"
    assert r.to_dict()["safe_phase_one_shortlist"] is not None
