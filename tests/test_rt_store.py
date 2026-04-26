from __future__ import annotations

import json
from pathlib import Path

from kalshi_weather.rt_collector.store import RtStore, make_dedupe_key


def test_make_dedupe_key_stable(tmp_path: Path) -> None:
    a = make_dedupe_key("s", "trade", 3, 9, "MKT", json.dumps({"x": 1}, sort_keys=True))
    b = make_dedupe_key("s", "trade", 3, 9, "MKT", json.dumps({"x": 1}, sort_keys=True))
    assert a == b


def test_insert_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    import sqlite3

    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE rt_trades (
          id INTEGER PRIMARY KEY,
          session_id TEXT,
          received_at TEXT,
          dedupe_key TEXT NOT NULL UNIQUE,
          server_ts TEXT,
          lag_ms REAL,
          msg_json TEXT
        );
        """
    )
    conn.close()

    s = RtStore(db)
    ok1 = s.insert_trade(
        session_id="sid",
        dedupe_key="k1",
        msg={"type": "trade"},
        lag_ms=1.0,
    )
    ok2 = s.insert_trade(
        session_id="sid",
        dedupe_key="k1",
        msg={"type": "trade"},
        lag_ms=2.0,
    )
    assert ok1 is True
    assert ok2 is False
