from __future__ import annotations

from kalshi_weather.system.contracts import EvidenceBundle, compute_evidence_hash, make_thesis_key


def test_thesis_key_generation_is_stable() -> None:
    a = make_thesis_key(market_ticker="kxtest", family="daily_temperature", threshold=70.0, side_hint="yes")
    b = make_thesis_key(market_ticker="KXTEST", family="daily_temperature", threshold=70.0, side_hint="yes")
    c = make_thesis_key(market_ticker="KXTEST", family="daily_temperature", threshold=71.0, side_hint="yes")
    assert a == b
    assert a != c


def test_evidence_hash_changes_with_material_inputs() -> None:
    base = EvidenceBundle(
        market_ticker="KXTEST",
        family="daily_temperature",
        forecast_value=72.0,
        threshold=70.0,
        model_probability=0.62,
        edge=0.08,
        yes_bid=0.50,
        yes_ask=0.52,
        implied_probability=0.51,
        sources=[{"source": "openweather", "fetched_at": "2026-01-01T00:00:00Z"}],
    )
    h1 = compute_evidence_hash(base)
    base.forecast_value = 74.0
    h2 = compute_evidence_hash(base)
    assert h1 != h2
