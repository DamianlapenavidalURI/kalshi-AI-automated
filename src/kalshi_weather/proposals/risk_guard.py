from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3

from kalshi_weather.proposals.models import ProposalDraft, RiskResult


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_concurrent_approved: int
    max_stake_cents_per_market: int
    snapshot_max_age_minutes: int
    max_yes_spread: float
    duplicate_lookback_hours: int
    concurrent_window_hours: int = 24
    min_signal_score: float = 0.0


def _parse_observed_at(obs: str) -> datetime | None:
    try:
        return datetime.fromisoformat(obs.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_dollars(s: Any) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s))
    except ValueError:
        return None


def _stake_cents(limit_price: float, quantity: float) -> int:
    return int(round(limit_price * quantity * 100))


def evaluate_proposal(
    conn: sqlite3.Connection,
    draft: ProposalDraft,
    *,
    limits: RiskLimits,
    now_utc: datetime | None = None,
) -> RiskResult:
    now_utc = now_utc or datetime.now(timezone.utc)
    details: dict[str, Any] = {"checks": []}

    def ok(msg: str) -> None:
        details["checks"].append({"ok": True, "msg": msg})

    def fail(reason: str) -> RiskResult:
        details["checks"].append({"ok": False, "msg": reason})
        return RiskResult(outcome="rejected", rejection_reason=reason, details=details)

    if not draft.market_ticker:
        return fail("missing_market_ticker")
    ok("market_ticker_present")

    if draft.side not in {"YES", "NO"}:
        return fail("invalid_side")
    ok("side_valid")

    if draft.source_snapshot_id is None:
        return fail("missing_source_snapshot_id")
    ok("snapshot_id_present")

    cur = conn.execute(
        """
        SELECT observed_at, is_stale, market_json
        FROM live_monitor_snapshots
        WHERE id = ?
        """,
        (draft.source_snapshot_id,),
    )
    row = cur.fetchone()
    if not row:
        return fail("source_snapshot_not_found")
    snap_obs = row[0]
    is_stale = int(row[1] or 0)
    mjson_raw = row[2]
    try:
        mjson = json.loads(mjson_raw) if isinstance(mjson_raw, str) else {}
    except json.JSONDecodeError:
        mjson = {}
    if is_stale == 1:
        return fail("stale_snapshot")
    ok("snapshot_not_flagged_stale")

    obs_dt = _parse_observed_at(str(snap_obs))
    if obs_dt is None:
        return fail("bad_snapshot_observed_at")
    if obs_dt < now_utc - timedelta(minutes=limits.snapshot_max_age_minutes):
        return fail("snapshot_too_old")
    ok("snapshot_fresh")

    status = mjson.get("status")
    if status not in {"active", "initialized"}:
        return fail(f"market_not_open:{status}")
    ok("market_status_tradeable")

    yb = _parse_dollars(mjson.get("yes_bid_dollars"))
    ya = _parse_dollars(mjson.get("yes_ask_dollars"))
    if yb is None or ya is None:
        return fail("missing_yes_book")
    if ya <= yb:
        return fail("invalid_book")
    if ya - yb > limits.max_yes_spread:
        return fail("spread_too_wide")
    ok("book_and_spread_ok")

    if limits.min_signal_score > 0 and float(draft.signal_score or 0.0) < float(limits.min_signal_score):
        return fail("signal_score_below_threshold")

    px = _parse_dollars(draft.proposed_limit_price_dollars)
    qty = _parse_dollars(draft.proposed_quantity)
    if px is None or qty is None or qty <= 0:
        return fail("bad_proposal_price_or_qty")
    stake = _stake_cents(px, qty)
    if stake > limits.max_stake_cents_per_market:
        return fail("stake_exceeds_cap")
    ok("stake_within_cap")

    cur = conn.execute(
        f"""
        SELECT COUNT(*) FROM proposals
        WHERE guard_outcome = 'approved'
          AND datetime(created_at) > datetime('now', '-{int(limits.concurrent_window_hours)} hours')
        """
    )
    approved_recent = int(cur.fetchone()[0])
    if approved_recent >= limits.max_concurrent_approved:
        return fail("max_concurrent_approved")
    ok("under_concurrent_cap")

    cur = conn.execute(
        f"""
        SELECT COUNT(*) FROM proposals
        WHERE market_ticker = ?
          AND guard_outcome = 'approved'
          AND datetime(created_at) > datetime('now', '-{int(limits.duplicate_lookback_hours)} hours')
        """,
        (draft.market_ticker,),
    )
    dup = int(cur.fetchone()[0])
    if dup > 0:
        return fail("duplicate_active_market")
    ok("no_duplicate_approved_market")

    return RiskResult(outcome="approved", rejection_reason=None, details=details)
