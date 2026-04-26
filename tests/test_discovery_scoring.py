from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_weather.discovery_universe.families import DEFAULT_FAMILIES
from kalshi_weather.discovery_universe.scoring import (
    detect_penalty_flags,
    hours_to_close_from_market,
    passes_safe_phase_one,
    score_market,
    spread_width_dollars,
)


def _macro_family():
    return DEFAULT_FAMILIES[0]


def test_spread_width() -> None:
    assert spread_width_dollars({"yes_bid_dollars": "0.40", "yes_ask_dollars": "0.44"}) == pytest.approx(0.04)
    assert spread_width_dollars({"yes_bid_dollars": None}) is None


def test_hours_to_close() -> None:
    now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    close = (now + timedelta(hours=10)).isoformat().replace("+00:00", "Z")
    h = hours_to_close_from_market({"close_time": close}, now=now)
    assert h is not None and abs(h - 10.0) < 0.1


def test_combo_penalty() -> None:
    m = {"title": "Same game combo YES", "status": "open"}
    p = detect_penalty_flags(m, None)
    assert "combo_or_multivariate" in p


def test_score_market_open_binary() -> None:
    fam = _macro_family()
    m = {
        "ticker": "X-1",
        "title": "Test market",
        "status": "open",
        "yes_bid_dollars": "0.45",
        "yes_ask_dollars": "0.48",
        "yes_bid_size_fp": "100",
        "yes_ask_size_fp": "100",
        "volume_24h_fp": "5000",
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat().replace("+00:00", "Z"),
        "market_type": "binary",
    }
    score, expl = score_market(m, fam, event={"title": "Event"})
    assert 0 <= score <= 100
    assert expl.components["spread_quality"] > 0


def test_safe_phase_one() -> None:
    fam = _macro_family()
    m = {
        "status": "open",
        "yes_bid_dollars": "0.45",
        "yes_ask_dollars": "0.50",
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
    }
    score, _ = score_market(m, fam, event=None)
    pids = set(detect_penalty_flags(m, None).keys())
    assert passes_safe_phase_one(m, score, pids, min_score=30.0)
