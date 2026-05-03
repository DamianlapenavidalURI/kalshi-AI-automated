from __future__ import annotations

from pathlib import Path

from kalshi_weather.db.db import Db


def test_thesis_state_repeat_detection(tmp_path: Path) -> None:
    db = Db(path=tmp_path / "test.sqlite3")
    db.init()
    key = "abc123"
    first = db.upsert_thesis_state(
        thesis_key=key,
        ticker="KXTEST",
        event_ticker="EVT",
        family="daily_temperature",
        decision="SKIP",
        reasoning="first",
        evidence_hash="hash-1",
        forecast_snapshot={"forecast_value": 71.0},
        bet_placed=False,
    )
    second = db.upsert_thesis_state(
        thesis_key=key,
        ticker="KXTEST",
        event_ticker="EVT",
        family="daily_temperature",
        decision="SKIP",
        reasoning="second",
        evidence_hash="hash-1",
        forecast_snapshot={"forecast_value": 71.0},
        bet_placed=False,
    )
    third = db.upsert_thesis_state(
        thesis_key=key,
        ticker="KXTEST",
        event_ticker="EVT",
        family="daily_temperature",
        decision="WAIT",
        reasoning="third",
        evidence_hash="hash-2",
        forecast_snapshot={"forecast_value": 74.0},
        bet_placed=False,
    )
    assert first["repeat_count"] == 0
    assert second["repeat_count"] == 1
    assert second["no_novelty"] is True
    assert third["repeat_count"] == 0
