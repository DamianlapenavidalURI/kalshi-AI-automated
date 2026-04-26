from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def make_dedupe_key(
    session_id: str, kind: str, seq: int | None, sid: int | None, market: str | None, raw: str
) -> str:
    if seq is not None and market:
        return f"{session_id}|{kind}|{seq}|{sid or 0}|{market}"
    h = hash(raw) % (10**18)
    return f"{session_id}|{kind}|{h}"


class RtStore:
    """SQLite persistence for real-time collector (replay-safe via UNIQUE dedupe_key)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def start_session(
        self,
        *,
        session_id: str,
        env: str,
        watchlist: list[str],
        meta: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO rt_ws_sessions(session_id, started_at, env, watchlist_json, meta_json)
                VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?)
                """,
                (
                    session_id,
                    env,
                    json.dumps(watchlist, ensure_ascii=False),
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
            conn.commit()

    def end_session(self, *, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE rt_ws_sessions SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE session_id = ?
                """,
                (session_id,),
            )
            conn.commit()

    def upsert_market_metadata(self, *, session_id: str, market_ticker: str, data: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO rt_market_metadata(session_id, observed_at, market_ticker, data_json)
                VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?)
                ON CONFLICT(session_id, market_ticker) DO UPDATE SET
                  observed_at = excluded.observed_at,
                  data_json = excluded.data_json
                """,
                (session_id, market_ticker, json.dumps(data, ensure_ascii=False)),
            )
            conn.commit()

    def insert_trade(
        self,
        *,
        session_id: str,
        dedupe_key: str,
        msg: dict[str, Any],
        lag_ms: float | None,
    ) -> bool:
        m = msg.get("msg") if isinstance(msg.get("msg"), dict) else {}
        server_ts = m.get("ts") if isinstance(m.get("ts"), str) else None
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO rt_trades(session_id, received_at, dedupe_key, server_ts, lag_ms, msg_json)
                VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?, ?)
                """,
                (
                    session_id,
                    dedupe_key,
                    server_ts,
                    lag_ms,
                    json.dumps(msg, ensure_ascii=False),
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def insert_ticker(self, *, session_id: str, dedupe_key: str, msg: dict[str, Any], lag_ms: float | None) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO rt_tickers(session_id, received_at, dedupe_key, lag_ms, msg_json)
                VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?)
                """,
                (session_id, dedupe_key, lag_ms, json.dumps(msg, ensure_ascii=False)),
            )
            conn.commit()
            return cur.rowcount > 0

    def insert_lifecycle(self, *, session_id: str, dedupe_key: str, msg: dict[str, Any], lag_ms: float | None) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO rt_lifecycle_events(session_id, received_at, dedupe_key, lag_ms, msg_json)
                VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?)
                """,
                (session_id, dedupe_key, lag_ms, json.dumps(msg, ensure_ascii=False)),
            )
            conn.commit()
            return cur.rowcount > 0

    def insert_orderbook_snapshot(
        self,
        *,
        session_id: str,
        source: str,
        market_ticker: str,
        seq: int | None,
        sid: int | None,
        dedupe_key: str,
        msg: dict[str, Any],
    ) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO rt_orderbook_snapshots(
                  session_id, observed_at, source, market_ticker, seq, sid, dedupe_key, msg_json
                ) VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    source,
                    market_ticker,
                    seq,
                    sid,
                    dedupe_key,
                    json.dumps(msg, ensure_ascii=False),
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def insert_orderbook_delta(
        self,
        *,
        session_id: str,
        market_ticker: str | None,
        seq: int | None,
        sid: int | None,
        dedupe_key: str,
        msg: dict[str, Any],
        lag_ms: float | None,
    ) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO rt_orderbook_deltas(
                  session_id, received_at, market_ticker, seq, sid, dedupe_key, lag_ms, msg_json
                ) VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    market_ticker,
                    seq,
                    sid,
                    dedupe_key,
                    lag_ms,
                    json.dumps(msg, ensure_ascii=False),
                ),
            )
            conn.commit()
            return cur.rowcount > 0


def now_ts() -> float:
    return time.time()
