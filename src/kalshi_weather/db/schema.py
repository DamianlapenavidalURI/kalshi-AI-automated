from __future__ import annotations

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  ended_at TEXT,
  status TEXT NOT NULL,
  meta_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  data_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS market_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  event_ticker TEXT,
  market_ticker TEXT,
  status TEXT,
  data_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_market_ticker
  ON market_snapshots(market_ticker, observed_at);

CREATE TABLE IF NOT EXISTS market_snapshots_normalized (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  source TEXT NOT NULL,
  event_ticker TEXT NOT NULL,
  market_ticker TEXT NOT NULL,
  series_ticker TEXT,
  event_title TEXT,
  event_sub_title TEXT,
  market_title TEXT,
  yes_sub_title TEXT,
  no_sub_title TEXT,
  market_status TEXT,
  event_status TEXT,
  yes_bid_dollars TEXT,
  yes_ask_dollars TEXT,
  no_bid_dollars TEXT,
  no_ask_dollars TEXT,
  last_price_dollars TEXT,
  yes_bid_size_fp TEXT,
  yes_ask_size_fp TEXT,
  volume_fp TEXT,
  volume_24h_fp TEXT,
  open_interest_fp TEXT,
  liquidity_dollars TEXT,
  open_time TEXT,
  close_time TEXT,
  latest_expiration_time TEXT,
  event_meta_json TEXT NOT NULL,
  market_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_norm_ticker_observed
  ON market_snapshots_normalized(market_ticker, observed_at);

CREATE INDEX IF NOT EXISTS idx_snapshots_norm_close_time
  ON market_snapshots_normalized(close_time);

CREATE TABLE IF NOT EXISTS market_exclusions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  source TEXT NOT NULL,
  reason TEXT NOT NULL,
  event_ticker TEXT,
  market_ticker TEXT,
  event_title TEXT,
  market_title TEXT,
  raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_exclusions_observed
  ON market_exclusions(observed_at, reason);

CREATE TABLE IF NOT EXISTS live_watchlist (
  session_id TEXT NOT NULL,
  market_ticker TEXT NOT NULL,
  event_ticker TEXT NOT NULL,
  source TEXT NOT NULL,
  added_at TEXT NOT NULL,
  PRIMARY KEY (session_id, market_ticker)
);

CREATE TABLE IF NOT EXISTS live_monitor_sessions (
  session_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  poll_interval_sec INTEGER NOT NULL,
  meta_json TEXT
);

CREATE TABLE IF NOT EXISTS watchlist_filter_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  session_id TEXT,
  audit_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_watchlist_filter_audit_observed
  ON watchlist_filter_audit(observed_at);

CREATE TABLE IF NOT EXISTS live_monitor_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  market_ticker TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  skipped_duplicate INTEGER NOT NULL DEFAULT 0,
  is_stale INTEGER NOT NULL DEFAULT 0,
  stale_reason TEXT,
  horizon_state TEXT,
  kickoff_at TEXT,
  horizon_inclusion_reason TEXT,
  market_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_live_monitor_snapshots_ticker_time
  ON live_monitor_snapshots(market_ticker, observed_at);

CREATE TABLE IF NOT EXISTS proposal_pipeline_runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  meta_json TEXT,
  run_summary_json TEXT
);

CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  proposal_id TEXT NOT NULL UNIQUE,
  pipeline_run_id TEXT NOT NULL,
  market_ticker TEXT NOT NULL,
  event_ticker TEXT,
  side TEXT NOT NULL,
  confidence REAL NOT NULL,
  reason TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  source_snapshot_id INTEGER,
  proposed_limit_price_dollars TEXT,
  proposed_quantity TEXT,
  implied_probability_yes_mid REAL,
  spread_dollars TEXT,
  spread_cents INTEGER,
  quality_score REAL,
  implied_probability REAL,
  spread REAL,
  mid_price REAL,
  snapshot_age_seconds REAL,
  proposal_quality_score REAL,
  signal_score REAL,
  feature_summary_json TEXT,
  candidate_quality_bucket TEXT,
  guard_outcome TEXT NOT NULL,
  rejection_reason TEXT,
  risk_details_json TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  FOREIGN KEY(pipeline_run_id) REFERENCES proposal_pipeline_runs(run_id),
  FOREIGN KEY(source_snapshot_id) REFERENCES live_monitor_snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_proposals_market_created
  ON proposals(market_ticker, created_at);

CREATE INDEX IF NOT EXISTS idx_proposals_outcome
  ON proposals(guard_outcome, created_at);

CREATE TABLE IF NOT EXISTS trade_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  run_id TEXT,
  market_ticker TEXT,
  side TEXT,
  action TEXT,
  price_dollars TEXT,
  quantity TEXT,
  rationale TEXT,
  proposal_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  run_id TEXT,
  market_ticker TEXT,
  client_order_id TEXT,
  kalshi_order_id TEXT,
  status TEXT,
  request_json TEXT,
  response_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS demo_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  execution_run_id TEXT NOT NULL,
  proposal_id TEXT NOT NULL,
  market_ticker TEXT NOT NULL,
  event_ticker TEXT,
  side TEXT NOT NULL,
  action TEXT NOT NULL DEFAULT 'buy',
  dry_run INTEGER NOT NULL,
  client_order_id TEXT,
  kalshi_order_id TEXT,
  order_status TEXT NOT NULL,
  request_json TEXT,
  response_json TEXT,
  block_reason TEXT,
  FOREIGN KEY(proposal_id) REFERENCES proposals(proposal_id)
);

CREATE INDEX IF NOT EXISTS idx_demo_orders_market_status
  ON demo_orders(market_ticker, order_status);

CREATE INDEX IF NOT EXISTS idx_demo_orders_proposal
  ON demo_orders(proposal_id);

CREATE INDEX IF NOT EXISTS idx_demo_orders_execution_run
  ON demo_orders(execution_run_id);

CREATE TABLE IF NOT EXISTS rt_ws_sessions (
  session_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  env TEXT NOT NULL,
  watchlist_json TEXT NOT NULL,
  meta_json TEXT,
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS rt_market_metadata (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  market_ticker TEXT NOT NULL,
  data_json TEXT NOT NULL,
  UNIQUE(session_id, market_ticker)
);

CREATE TABLE IF NOT EXISTS rt_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  received_at TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  server_ts TEXT,
  lag_ms REAL,
  msg_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rt_trades_session ON rt_trades(session_id, received_at);

CREATE TABLE IF NOT EXISTS rt_orderbook_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  source TEXT NOT NULL,
  market_ticker TEXT NOT NULL,
  seq INTEGER,
  sid INTEGER,
  dedupe_key TEXT NOT NULL UNIQUE,
  msg_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rt_ob_snap_session ON rt_orderbook_snapshots(session_id, market_ticker);

CREATE TABLE IF NOT EXISTS rt_orderbook_deltas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  received_at TEXT NOT NULL,
  market_ticker TEXT,
  seq INTEGER,
  sid INTEGER,
  dedupe_key TEXT NOT NULL UNIQUE,
  lag_ms REAL,
  msg_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rt_ob_delta_session ON rt_orderbook_deltas(session_id, received_at);

CREATE TABLE IF NOT EXISTS rt_lifecycle_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  received_at TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  lag_ms REAL,
  msg_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rt_lifecycle_session ON rt_lifecycle_events(session_id, received_at);

CREATE TABLE IF NOT EXISTS rt_tickers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  received_at TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  lag_ms REAL,
  msg_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rt_tickers_session ON rt_tickers(session_id, received_at);
"""

