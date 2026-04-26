from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3

from kalshi_weather.kalshi.client import KalshiClient
from kalshi_weather.markets.execution_gate import market_passes_execution_gate


@dataclass(frozen=True, slots=True)
class PreflightResult:
    ok: bool
    block_reason: str | None
    details: dict[str, Any]


def _parse_dt(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_float(s: Any) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s))
    except ValueError:
        return None


def validate_limit_and_qty(*, limit_dollars: str | None, qty: str | None) -> tuple[bool, str | None]:
    px = _parse_float(limit_dollars)
    q = _parse_float(qty)
    if px is None or q is None:
        return False, "invalid_limit_or_qty_parse"
    if q <= 0 or q > 1_000_000:
        return False, "invalid_quantity_bounds"
    if px <= 0 or px >= 1:
        return False, "invalid_limit_price_bounds"
    return True, None


def preflight_demo_execution(
    conn: sqlite3.Connection,
    *,
    proposal_row: sqlite3.Row,
    market_from_api: dict[str, Any] | None,
    snapshot_max_age_minutes: int,
    max_proposal_age_minutes: int,
    min_signal_score_execution: float = 0.0,
    max_resolution_hours: float = 72.0,
    now_utc: datetime | None = None,
) -> PreflightResult:
    """
    Deterministic checks before placing a demo order. Does not call the network
    except via caller-provided market_from_api.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    details: dict[str, Any] = {"checks": []}

    def fail(reason: str) -> PreflightResult:
        return PreflightResult(ok=False, block_reason=reason, details=details)

    if str(proposal_row["guard_outcome"]) != "approved":
        return fail("proposal_not_approved")

    ok_px, px_reason = validate_limit_and_qty(
        limit_dollars=str(proposal_row["proposed_limit_price_dollars"] or ""),
        qty=str(proposal_row["proposed_quantity"] or ""),
    )
    if not ok_px:
        return fail(px_reason or "invalid_limit_qty")

    created = _parse_dt(str(proposal_row["created_at"]))
    if created is None:
        return fail("bad_proposal_created_at")
    if created < now_utc - timedelta(minutes=max_proposal_age_minutes):
        return fail("proposal_too_old")

    snap_id = proposal_row["source_snapshot_id"]
    if snap_id is None:
        return fail("missing_source_snapshot_id")

    cur = conn.execute(
        """
        SELECT is_stale, observed_at, market_json
        FROM live_monitor_snapshots
        WHERE id = ?
        """,
        (int(snap_id),),
    )
    srow = cur.fetchone()
    if not srow:
        return fail("snapshot_not_found")
    if int(srow[0] or 0) == 1:
        return fail("stale_snapshot")

    obs = _parse_dt(str(srow[1]))
    if obs is None:
        return fail("bad_snapshot_observed_at")
    if obs < now_utc - timedelta(minutes=snapshot_max_age_minutes):
        return fail("snapshot_too_old")

    if market_from_api is None:
        return fail("market_fetch_failed")

    status = market_from_api.get("status")
    if status not in {"active", "initialized"}:
        return fail(f"market_not_open:{status}")

    ok_scope, scope_reason = market_passes_execution_gate(
        market_from_api,
        now_utc=now_utc,
        max_resolution_hours=max_resolution_hours,
    )
    if not ok_scope:
        return fail(scope_reason)

    if min_signal_score_execution > 0:
        sig = _parse_float(proposal_row["signal_score"]) if "signal_score" in proposal_row.keys() else None
        pq = _parse_float(proposal_row["proposal_quality_score"])
        effective = float(sig) if sig is not None else (float(pq or 0.0) * 100.0)
        if effective < float(min_signal_score_execution):
            return fail("signal_score_below_execution_threshold")

    details["checks"].append({"ok": True, "msg": "preflight_passed"})
    return PreflightResult(ok=True, block_reason=None, details=details)


def fetch_market_for_ticker(client: KalshiClient, market_ticker: str) -> dict[str, Any] | None:
    try:
        data = client.get_markets(tickers=market_ticker, limit=5)
    except Exception:
        return None
    markets = data.get("markets") or []
    if not markets or not isinstance(markets[0], dict):
        return None
    return markets[0]
