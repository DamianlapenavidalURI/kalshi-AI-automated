from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from kalshi_weather.db.schema import SCHEMA_SQL
from kalshi_weather.markets.models import ExcludedMarket, MarketSnapshot

_M4_TABLES = frozenset({"proposal_pipeline_runs", "proposals"})
_M6_MONITOR_TABLE = "live_monitor_snapshots"
_M6_MONITOR_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("live_monitor_snapshots", "horizon_state", "TEXT"),
    ("live_monitor_snapshots", "kickoff_at", "TEXT"),
    ("live_monitor_snapshots", "horizon_inclusion_reason", "TEXT"),
)

_M7_PROPOSAL_SIGNAL_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("proposals", "signal_score", "REAL"),
    ("proposals", "feature_summary_json", "TEXT"),
    ("proposals", "candidate_quality_bucket", "TEXT"),
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

_M4_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("proposal_pipeline_runs", "run_summary_json", "TEXT"),
    ("proposals", "implied_probability_yes_mid", "REAL"),
    ("proposals", "spread_dollars", "TEXT"),
    ("proposals", "spread_cents", "INTEGER"),
    ("proposals", "quality_score", "REAL"),
    ("proposals", "implied_probability", "REAL"),
    ("proposals", "spread", "REAL"),
    ("proposals", "mid_price", "REAL"),
    ("proposals", "snapshot_age_seconds", "REAL"),
    ("proposals", "proposal_quality_score", "REAL"),
)


def _migrate_m4_proposal_columns(conn: sqlite3.Connection) -> None:
    for table, col, sql_type in _M4_COLUMNS:
        if table not in _M4_TABLES:
            continue
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            continue
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if col in cols:
            continue
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {sql_type}')


def _migrate_m7_proposal_signal_columns(conn: sqlite3.Connection) -> None:
    for table, col, sql_type in _M7_PROPOSAL_SIGNAL_COLUMNS:
        if table not in _M4_TABLES:
            continue
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            continue
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if col in cols:
            continue
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {sql_type}')


def _ensure_watchlist_filter_audit_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist_filter_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          observed_at TEXT NOT NULL,
          session_id TEXT,
          audit_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_watchlist_filter_audit_observed
          ON watchlist_filter_audit(observed_at)
        """
    )


def _ensure_state_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_state (
          ticker TEXT PRIMARY KEY,
          event_ticker TEXT,
          family TEXT,
          last_decision TEXT,
          last_decision_time TEXT,
          last_reasoning TEXT,
          last_forecast_snapshot_json TEXT,
          last_price_seen REAL,
          open_position_side TEXT,
          open_position_size REAL,
          open_position_entry_price REAL,
          pnl_estimate REAL
        );

        CREATE INDEX IF NOT EXISTS idx_market_state_event_family
          ON market_state(event_ticker, family);

        CREATE TABLE IF NOT EXISTS thesis_state (
          thesis_key TEXT PRIMARY KEY,
          ticker TEXT NOT NULL,
          event_ticker TEXT,
          family TEXT,
          last_bet_time TEXT,
          last_decision_time TEXT,
          repeat_count INTEGER NOT NULL DEFAULT 0,
          last_reasoning TEXT,
          last_evidence_hash TEXT,
          last_forecast_snapshot_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_thesis_state_ticker
          ON thesis_state(ticker, last_decision_time);
        """
    )


def _migrate_m6_monitor_horizon_columns(conn: sqlite3.Connection) -> None:
    for table, col, sql_type in _M6_MONITOR_COLUMNS:
        if table != _M6_MONITOR_TABLE:
            continue
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            continue
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if col in cols:
            continue
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {sql_type}')


def _migrate_execution_orders_table(conn: sqlite3.Connection) -> None:
    demo_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='demo_orders'"
    ).fetchone()
    execution_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_orders'"
    ).fetchone()

    if demo_exists and not execution_exists:
        conn.execute("ALTER TABLE demo_orders RENAME TO execution_orders")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_execution_orders_market_status
              ON execution_orders(market_ticker, order_status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_execution_orders_proposal
              ON execution_orders(proposal_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_execution_orders_execution_run
              ON execution_orders(execution_run_id)
            """
        )
        return

    if demo_exists and execution_exists:
        exec_count = int(conn.execute("SELECT COUNT(*) FROM execution_orders").fetchone()[0])
        demo_count = int(conn.execute("SELECT COUNT(*) FROM demo_orders").fetchone()[0])
        if exec_count == 0 and demo_count > 0:
            conn.execute("DROP TABLE execution_orders")
            conn.execute("ALTER TABLE demo_orders RENAME TO execution_orders")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_execution_orders_market_status
                  ON execution_orders(market_ticker, order_status)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_execution_orders_proposal
                  ON execution_orders(proposal_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_execution_orders_execution_run
                  ON execution_orders(execution_run_id)
                """
            )
            return

        if exec_count > 0 and demo_count > 0:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                """
                INSERT OR IGNORE INTO execution_orders(
                  id, created_at, updated_at, execution_run_id, proposal_id, market_ticker,
                  event_ticker, side, action, dry_run, client_order_id, kalshi_order_id, order_status,
                  request_json, response_json, block_reason
                )
                SELECT
                  id, created_at, updated_at, execution_run_id, proposal_id, market_ticker,
                  event_ticker, side, action, dry_run, client_order_id, kalshi_order_id, order_status,
                  request_json, response_json, block_reason
                FROM demo_orders
                """
            )
            conn.execute("PRAGMA foreign_keys = ON")


@dataclass(frozen=True, slots=True)
class Db:
    path: Path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            _migrate_m4_proposal_columns(conn)
            _migrate_m6_monitor_horizon_columns(conn)
            _migrate_m7_proposal_signal_columns(conn)
            _ensure_watchlist_filter_audit_table(conn)
            _ensure_state_tables(conn)
            _migrate_execution_orders_table(conn)
            conn.commit()

    def get_market_state(self, *, ticker: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT ticker, event_ticker, family, last_decision, last_decision_time, last_reasoning,
                       last_forecast_snapshot_json, last_price_seen, open_position_side, open_position_size,
                       open_position_entry_price, pnl_estimate
                FROM market_state
                WHERE ticker = ?
                """,
                (ticker,),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        raw = out.get("last_forecast_snapshot_json")
        if isinstance(raw, str) and raw.strip():
            try:
                out["last_forecast_snapshot_json"] = json.loads(raw)
            except json.JSONDecodeError:
                pass
        return out

    def get_thesis_state(self, *, thesis_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT thesis_key, ticker, event_ticker, family, last_bet_time, last_decision_time,
                       repeat_count, last_reasoning, last_evidence_hash, last_forecast_snapshot_json
                FROM thesis_state
                WHERE thesis_key = ?
                """,
                (thesis_key,),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        raw = out.get("last_forecast_snapshot_json")
        if isinstance(raw, str) and raw.strip():
            try:
                out["last_forecast_snapshot_json"] = json.loads(raw)
            except json.JSONDecodeError:
                pass
        return out

    def upsert_market_state(
        self,
        *,
        ticker: str,
        event_ticker: str | None,
        family: str | None,
        last_decision: str | None = None,
        last_decision_time: str | None = None,
        last_reasoning: str | None = None,
        last_forecast_snapshot: dict[str, Any] | None = None,
        last_price_seen: float | None = None,
        open_position_side: str | None = None,
        open_position_size: float | None = None,
        open_position_entry_price: float | None = None,
        pnl_estimate: float | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO market_state(
                  ticker, event_ticker, family, last_decision, last_decision_time, last_reasoning,
                  last_forecast_snapshot_json, last_price_seen, open_position_side, open_position_size,
                  open_position_entry_price, pnl_estimate
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                  event_ticker=excluded.event_ticker,
                  family=excluded.family,
                  last_decision=COALESCE(excluded.last_decision, market_state.last_decision),
                  last_decision_time=COALESCE(excluded.last_decision_time, market_state.last_decision_time),
                  last_reasoning=COALESCE(excluded.last_reasoning, market_state.last_reasoning),
                  last_forecast_snapshot_json=COALESCE(excluded.last_forecast_snapshot_json, market_state.last_forecast_snapshot_json),
                  last_price_seen=COALESCE(excluded.last_price_seen, market_state.last_price_seen),
                  open_position_side=COALESCE(excluded.open_position_side, market_state.open_position_side),
                  open_position_size=COALESCE(excluded.open_position_size, market_state.open_position_size),
                  open_position_entry_price=COALESCE(excluded.open_position_entry_price, market_state.open_position_entry_price),
                  pnl_estimate=COALESCE(excluded.pnl_estimate, market_state.pnl_estimate)
                """,
                (
                    ticker,
                    event_ticker,
                    family,
                    last_decision,
                    last_decision_time or _utc_now_iso(),
                    last_reasoning,
                    json.dumps(last_forecast_snapshot, ensure_ascii=False)
                    if isinstance(last_forecast_snapshot, dict)
                    else None,
                    last_price_seen,
                    open_position_side,
                    open_position_size,
                    open_position_entry_price,
                    pnl_estimate,
                ),
            )
            conn.commit()

    def upsert_thesis_state(
        self,
        *,
        thesis_key: str,
        ticker: str,
        event_ticker: str | None,
        family: str | None,
        decision: str,
        reasoning: str | None,
        evidence_hash: str | None,
        forecast_snapshot: dict[str, Any] | None = None,
        bet_placed: bool = False,
    ) -> dict[str, Any]:
        now_iso = _utc_now_iso()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT repeat_count, last_evidence_hash
                FROM thesis_state
                WHERE thesis_key = ?
                """,
                (thesis_key,),
            ).fetchone()
            prev_repeat = int(row["repeat_count"]) if row and row["repeat_count"] is not None else 0
            prev_hash = str(row["last_evidence_hash"] or "") if row else ""
            no_novelty = bool(evidence_hash) and bool(prev_hash) and evidence_hash == prev_hash
            repeat_count = (prev_repeat + 1) if no_novelty else 0
            conn.execute(
                """
                INSERT INTO thesis_state(
                  thesis_key, ticker, event_ticker, family, last_bet_time, last_decision_time,
                  repeat_count, last_reasoning, last_evidence_hash, last_forecast_snapshot_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thesis_key) DO UPDATE SET
                  ticker=excluded.ticker,
                  event_ticker=excluded.event_ticker,
                  family=excluded.family,
                  last_bet_time=COALESCE(excluded.last_bet_time, thesis_state.last_bet_time),
                  last_decision_time=excluded.last_decision_time,
                  repeat_count=excluded.repeat_count,
                  last_reasoning=COALESCE(excluded.last_reasoning, thesis_state.last_reasoning),
                  last_evidence_hash=COALESCE(excluded.last_evidence_hash, thesis_state.last_evidence_hash),
                  last_forecast_snapshot_json=COALESCE(excluded.last_forecast_snapshot_json, thesis_state.last_forecast_snapshot_json)
                """,
                (
                    thesis_key,
                    ticker,
                    event_ticker,
                    family,
                    now_iso if bet_placed else None,
                    now_iso,
                    repeat_count,
                    reasoning,
                    evidence_hash,
                    json.dumps(forecast_snapshot, ensure_ascii=False)
                    if isinstance(forecast_snapshot, dict)
                    else None,
                ),
            )
            conn.commit()
        return {"repeat_count": repeat_count, "no_novelty": no_novelty, "last_evidence_hash": prev_hash}

    def recent_market_decisions(
        self,
        *,
        ticker: str | None = None,
        event_ticker: str | None = None,
        family: str | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        args: list[Any] = []
        if ticker:
            where.append("ticker = ?")
            args.append(ticker)
        if event_ticker:
            where.append("event_ticker = ?")
            args.append(event_ticker)
        if family:
            where.append("family = ?")
            args.append(family)
        sql = """
            SELECT ticker, event_ticker, family, last_decision, last_decision_time, last_reasoning
            FROM market_state
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_decision_time DESC LIMIT ?"
        args.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
        return [dict(r) for r in rows]

    def insert_run(self, *, run_id: str, status: str, meta: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs(run_id, status, meta_json)
                VALUES (?, ?, ?)
                """,
                (run_id, status, json.dumps(meta or {}, ensure_ascii=False)),
            )
            conn.commit()

    def end_run(self, *, run_id: str, status: str, meta: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), status = ?, meta_json = ?
                WHERE run_id = ?
                """,
                (status, json.dumps(meta or {}, ensure_ascii=False), run_id),
            )
            conn.commit()

    def insert_agent_log(
        self,
        *,
        run_id: str,
        level: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_logs(run_id, level, message, data_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, level, message, json.dumps(data or {}, ensure_ascii=False)),
            )
            conn.commit()

    def latest_run(self, *, status_any: bool = True) -> sqlite3.Row | None:
        with self.connect() as conn:
            if status_any:
                cur = conn.execute(
                    """
                    SELECT run_id, started_at, ended_at, status, meta_json
                    FROM runs
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                )
            else:
                cur = conn.execute(
                    """
                    SELECT run_id, started_at, ended_at, status, meta_json
                    FROM runs
                    WHERE status = 'completed'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                )
            return cur.fetchone()

    def agent_logs_for_run(self, *, run_id: str, limit: int = 200) -> list[sqlite3.Row]:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT created_at, level, message, data_json
                FROM agent_logs
                WHERE run_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (run_id, limit),
            )
            return list(cur.fetchall())

    def insert_market_snapshots(self, snapshots: Iterable[dict[str, Any]]) -> int:
        rows = []
        for s in snapshots:
            if not isinstance(s, dict):
                continue
            rows.append(
                (
                    s.get("event_ticker"),
                    s.get("ticker") or s.get("market_ticker"),
                    s.get("status"),
                    json.dumps(s, ensure_ascii=False),
                )
            )
        if not rows:
            return 0
        with self.connect() as conn:
            cur = conn.executemany(
                """
                INSERT INTO market_snapshots(event_ticker, market_ticker, status, data_json)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
            return cur.rowcount

    def latest_market_snapshots(self, *, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT observed_at, event_ticker, market_ticker, status, data_json
                FROM market_snapshots
                ORDER BY observed_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return list(cur.fetchall())

    def insert_normalized_market_snapshots(self, snapshots: Iterable[MarketSnapshot]) -> int:
        rows = []
        for s in snapshots:
            row = s.to_row()
            rows.append(
                (
                    row["observed_at"],
                    row["source"],
                    row["event_ticker"],
                    row["market_ticker"],
                    row["series_ticker"],
                    row["event_title"],
                    row["event_sub_title"],
                    row["market_title"],
                    row["yes_sub_title"],
                    row["no_sub_title"],
                    row["market_status"],
                    row["event_status"],
                    row["yes_bid_dollars"],
                    row["yes_ask_dollars"],
                    row["no_bid_dollars"],
                    row["no_ask_dollars"],
                    row["last_price_dollars"],
                    row["yes_bid_size_fp"],
                    row["yes_ask_size_fp"],
                    row["volume_fp"],
                    row["volume_24h_fp"],
                    row["open_interest_fp"],
                    row["liquidity_dollars"],
                    row["open_time"],
                    row["close_time"],
                    row["latest_expiration_time"],
                    json.dumps(row["event_meta_json"], ensure_ascii=False),
                    json.dumps(row["market_json"], ensure_ascii=False),
                )
            )
        if not rows:
            return 0
        with self.connect() as conn:
            cur = conn.executemany(
                """
                INSERT INTO market_snapshots_normalized(
                  observed_at, source, event_ticker, market_ticker, series_ticker,
                  event_title, event_sub_title, market_title, yes_sub_title, no_sub_title,
                  market_status, event_status,
                  yes_bid_dollars, yes_ask_dollars, no_bid_dollars, no_ask_dollars,
                  last_price_dollars, yes_bid_size_fp, yes_ask_size_fp,
                  volume_fp, volume_24h_fp, open_interest_fp, liquidity_dollars,
                  open_time, close_time, latest_expiration_time,
                  event_meta_json, market_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
            return cur.rowcount

    def insert_market_exclusions(self, excluded: Iterable[ExcludedMarket]) -> int:
        rows = []
        for e in excluded:
            row = e.to_row()
            rows.append(
                (
                    row["observed_at"],
                    row["source"],
                    row["reason"],
                    row["event_ticker"],
                    row["market_ticker"],
                    row["event_title"],
                    row["market_title"],
                    json.dumps(row["raw_json"], ensure_ascii=False),
                )
            )
        if not rows:
            return 0
        with self.connect() as conn:
            cur = conn.executemany(
                """
                INSERT INTO market_exclusions(
                  observed_at, source, reason, event_ticker, market_ticker, event_title, market_title, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
            return cur.rowcount

    def latest_normalized_snapshots(self, *, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT observed_at, event_ticker, market_ticker, event_title, market_title,
                       market_status, yes_bid_dollars, yes_ask_dollars, volume_fp, close_time
                FROM market_snapshots_normalized
                ORDER BY observed_at DESC, market_ticker ASC
                LIMIT ?
                """,
                (limit,),
            )
            return list(cur.fetchall())

    def latest_exclusions(self, *, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT observed_at, reason, event_ticker, market_ticker, event_title, market_title
                FROM market_exclusions
                ORDER BY observed_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return list(cur.fetchall())

    def insert_live_monitor_session(
        self, *, session_id: str, poll_interval_sec: int, meta: dict[str, Any] | None = None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO live_monitor_sessions(session_id, started_at, poll_interval_sec, meta_json)
                VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?)
                """,
                (session_id, poll_interval_sec, json.dumps(meta or {}, ensure_ascii=False)),
            )
            conn.commit()

    def insert_watchlist_filter_audit(
        self,
        *,
        session_id: str | None,
        observed_at: str,
        audit_json: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO watchlist_filter_audit(observed_at, session_id, audit_json)
                VALUES (?, ?, ?)
                """,
                (observed_at, session_id, audit_json),
            )
            conn.commit()

    def latest_watchlist_filter_audit(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT id, observed_at, session_id, audit_json
                FROM watchlist_filter_audit
                ORDER BY id DESC
                LIMIT 1
                """
            )
            return cur.fetchone()

    def replace_live_watchlist(
        self,
        *,
        session_id: str,
        rows: Iterable[tuple[str, str, str]],
    ) -> None:
        """rows: (market_ticker, event_ticker, source)"""
        with self.connect() as conn:
            conn.execute("DELETE FROM live_watchlist WHERE session_id = ?", (session_id,))
            conn.executemany(
                """
                INSERT INTO live_watchlist(session_id, market_ticker, event_ticker, source, added_at)
                VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                """,
                [(session_id, mt, et, src) for mt, et, src in rows],
            )
            conn.commit()

    def insert_live_monitor_snapshot(
        self,
        *,
        session_id: str,
        observed_at: str,
        market_ticker: str,
        fingerprint: str,
        skipped_duplicate: bool,
        is_stale: bool,
        stale_reason: str | None,
        market_json: dict[str, Any],
        horizon_state: str | None = None,
        kickoff_at: str | None = None,
        horizon_inclusion_reason: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO live_monitor_snapshots(
                  session_id, observed_at, market_ticker, fingerprint,
                  skipped_duplicate, is_stale, stale_reason,
                  horizon_state, kickoff_at, horizon_inclusion_reason,
                  market_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    observed_at,
                    market_ticker,
                    fingerprint,
                    1 if skipped_duplicate else 0,
                    1 if is_stale else 0,
                    stale_reason,
                    horizon_state,
                    kickoff_at,
                    horizon_inclusion_reason,
                    json.dumps(market_json, ensure_ascii=False),
                ),
            )
            conn.commit()

    def insert_proposal_pipeline_run(self, *, run_id: str, meta: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO proposal_pipeline_runs(run_id, meta_json)
                VALUES (?, ?)
                """,
                (run_id, json.dumps(meta or {}, ensure_ascii=False)),
            )
            conn.commit()

    def update_proposal_pipeline_run_summary(self, *, run_id: str, summary: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE proposal_pipeline_runs
                SET run_summary_json = ?
                WHERE run_id = ?
                """,
                (json.dumps(summary, ensure_ascii=False), run_id),
            )
            conn.commit()

    def insert_proposal_on_connection(
        self,
        conn: sqlite3.Connection,
        *,
        pipeline_run_id: str,
        draft: Any,
        guard_outcome: str,
        rejection_reason: str | None,
        risk_details: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO proposals(
              proposal_id, pipeline_run_id, market_ticker, event_ticker, side, confidence, reason,
              observed_at, source_snapshot_id, proposed_limit_price_dollars, proposed_quantity,
              implied_probability_yes_mid, spread_dollars, spread_cents, quality_score,
              implied_probability, spread, mid_price, snapshot_age_seconds, proposal_quality_score,
              signal_score, feature_summary_json, candidate_quality_bucket,
              guard_outcome, rejection_reason, risk_details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft.proposal_id,
                pipeline_run_id,
                draft.market_ticker,
                draft.event_ticker,
                draft.side,
                draft.confidence,
                draft.reason,
                draft.observed_at,
                draft.source_snapshot_id,
                draft.proposed_limit_price_dollars,
                draft.proposed_quantity,
                draft.implied_probability_yes_mid,
                draft.spread_dollars,
                draft.spread_cents,
                draft.quality_score,
                draft.implied_probability,
                draft.spread,
                draft.mid_price,
                draft.snapshot_age_seconds,
                draft.proposal_quality_score,
                draft.signal_score,
                draft.feature_summary_json,
                draft.candidate_quality_bucket,
                guard_outcome,
                rejection_reason,
                json.dumps(risk_details, ensure_ascii=False),
            ),
        )

    def recent_proposals(self, *, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT proposal_id, pipeline_run_id, market_ticker, event_ticker, side, confidence,
                       reason, observed_at, proposed_limit_price_dollars, proposed_quantity,
                       implied_probability_yes_mid, spread_dollars, spread_cents, quality_score,
                       implied_probability, spread, mid_price, snapshot_age_seconds, proposal_quality_score,
                       signal_score, feature_summary_json, candidate_quality_bucket,
                       guard_outcome, rejection_reason, created_at
                FROM proposals
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return list(cur.fetchall())

    def latest_proposal_pipeline_run(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT run_id, started_at, meta_json, run_summary_json
                FROM proposal_pipeline_runs
                ORDER BY started_at DESC
                LIMIT 1
                """
            )
            return cur.fetchone()

    def proposal_summary_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
            ap = conn.execute(
                "SELECT COUNT(*) FROM proposals WHERE guard_outcome = 'approved'"
            ).fetchone()[0]
            rj = conn.execute(
                "SELECT COUNT(*) FROM proposals WHERE guard_outcome = 'rejected'"
            ).fetchone()[0]
        return {"total": int(total), "approved": int(ap), "rejected": int(rj)}

    def list_proposals_eligible_for_execution(
        self, *, limit: int, max_proposal_age_minutes: int
    ) -> list[sqlite3.Row]:
        age_mod = f"-{int(max_proposal_age_minutes)} minutes"
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT p.* FROM proposals p
                WHERE p.guard_outcome = 'approved'
                  AND datetime(p.created_at) >= datetime('now', ?)
                  AND NOT EXISTS (
                    SELECT 1 FROM execution_orders d
                    WHERE d.proposal_id = p.proposal_id
                      AND d.dry_run = 0
                      AND d.order_status IN ('resting', 'executed')
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM execution_orders d
                    WHERE d.market_ticker = p.market_ticker
                      AND d.dry_run = 0
                      AND d.order_status = 'resting'
                  )
                ORDER BY p.created_at ASC
                LIMIT ?
                """,
                (age_mod, limit),
            )
            return list(cur.fetchall())

    def has_resting_execution_order_for_market(
        self, conn: sqlite3.Connection, market_ticker: str
    ) -> bool:
        cur = conn.execute(
            """
            SELECT 1 FROM execution_orders
            WHERE market_ticker = ?
              AND dry_run = 0
              AND order_status = 'resting'
            LIMIT 1
            """,
            (market_ticker,),
        )
        return cur.fetchone() is not None

    def insert_execution_order_on_connection(
        self,
        conn: sqlite3.Connection,
        *,
        execution_run_id: str,
        proposal_id: str,
        market_ticker: str,
        event_ticker: str | None,
        side: str,
        dry_run: bool,
        client_order_id: str | None,
        kalshi_order_id: str | None,
        order_status: str,
        request_json: str | None,
        response_json: str | None,
        block_reason: str | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO execution_orders(
              execution_run_id, proposal_id, market_ticker, event_ticker, side, action,
              dry_run, client_order_id, kalshi_order_id, order_status,
              request_json, response_json, block_reason
            )
            VALUES (?, ?, ?, ?, ?, 'buy', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution_run_id,
                proposal_id,
                market_ticker,
                event_ticker,
                side,
                1 if dry_run else 0,
                client_order_id,
                kalshi_order_id,
                order_status,
                request_json,
                response_json,
                block_reason,
            ),
        )

    def update_execution_order_status_on_connection(
        self,
        conn: sqlite3.Connection,
        *,
        internal_id: int,
        order_status: str,
        response_json: str,
    ) -> None:
        conn.execute(
            """
            UPDATE execution_orders
            SET order_status = ?, response_json = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (order_status, response_json, internal_id),
        )

    def list_execution_orders_for_reconciliation(self, *, limit: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT id, kalshi_order_id, order_status
                FROM execution_orders
                WHERE dry_run = 0
                  AND kalshi_order_id IS NOT NULL
                  AND order_status NOT IN ('executed', 'canceled', 'submit_failed', 'skipped_blocked', 'dry_run')
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return list(cur.fetchall())

    def recent_execution_orders(self, *, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT id, created_at, updated_at, execution_run_id, proposal_id, market_ticker,
                       event_ticker, side, dry_run, client_order_id, kalshi_order_id, order_status,
                       block_reason
                FROM execution_orders
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return list(cur.fetchall())

    def execution_orders_summary(self) -> dict[str, Any]:
        with self.connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM execution_orders").fetchone()[0])
            dry = int(
                conn.execute("SELECT COUNT(*) FROM execution_orders WHERE dry_run = 1").fetchone()[0]
            )
            real = total - dry
            by_st = conn.execute(
                """
                SELECT order_status, COUNT(*) AS n
                FROM execution_orders
                GROUP BY order_status
                ORDER BY n DESC
                """
            ).fetchall()
        return {
            "total": total,
            "dry_run_rows": dry,
            "real_submission_rows": real,
            "by_status": {str(r[0]): int(r[1]) for r in by_st},
        }

