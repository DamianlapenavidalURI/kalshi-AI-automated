from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from kalshi_weather.db.db import Db
from kalshi_weather.execution.orders import build_demo_limit_order_request, new_client_order_id
from kalshi_weather.execution.preflight import fetch_market_for_ticker, preflight_demo_execution
from kalshi_weather.kalshi.client import KalshiClient, KalshiHttpError

log = logging.getLogger("kalshi_weather.execution")


@dataclass
class ExecutionSummary:
    execution_run_id: str
    eligible: int = 0
    submitted: int = 0
    dry_run_recorded: int = 0
    skipped_blocked: int = 0
    block_reasons: dict[str, int] = field(default_factory=dict)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")


def run_demo_execution(
    db: Db,
    client: KalshiClient,
    *,
    dry_run: bool = True,
    limit_proposals: int = 25,
    max_proposal_age_minutes: int = 120,
    snapshot_max_age_minutes: int = 30,
    min_signal_score_execution: float = 0.0,
    max_resolution_hours: float = 72.0,
) -> ExecutionSummary:
    """
    Load risk-approved proposals, run preflight, optionally POST demo orders.
    Default dry_run=True: persist intended requests without submitting.
    """
    run_id = str(uuid.uuid4())
    summary = ExecutionSummary(execution_run_id=run_id)

    rows = db.list_proposals_eligible_for_demo_execution(
        limit=limit_proposals,
        max_proposal_age_minutes=max_proposal_age_minutes,
    )
    summary.eligible = len(rows)
    log.info(
        "demo_execution start run_id=%s dry_run=%s eligible_proposals=%d",
        run_id,
        dry_run,
        summary.eligible,
    )

    with db.connect() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for row in rows:
            proposal_id = str(row["proposal_id"])
            market_ticker = str(row["market_ticker"])
            event_ticker = row["event_ticker"]
            event_ticker_s = str(event_ticker) if event_ticker else None

            if db.has_resting_demo_order_for_market(conn, market_ticker):
                reason = "duplicate_active_market_order_local"
                summary.skipped_blocked += 1
                summary.block_reasons[reason] = summary.block_reasons.get(reason, 0) + 1
                log.info(
                    "demo_execution BLOCKED proposal_id=%s market=%s reason=%s",
                    proposal_id,
                    market_ticker,
                    reason,
                )
                db.insert_demo_order_on_connection(
                    conn,
                    execution_run_id=run_id,
                    proposal_id=proposal_id,
                    market_ticker=market_ticker,
                    event_ticker=event_ticker_s,
                    side=str(row["side"]),
                    dry_run=dry_run,
                    client_order_id=None,
                    kalshi_order_id=None,
                    order_status="skipped_blocked",
                    request_json=None,
                    response_json=None,
                    block_reason=reason,
                )
                continue

            market = fetch_market_for_ticker(client, market_ticker)
            pf = preflight_demo_execution(
                conn,
                proposal_row=row,
                market_from_api=market,
                snapshot_max_age_minutes=snapshot_max_age_minutes,
                max_proposal_age_minutes=max_proposal_age_minutes,
                min_signal_score_execution=min_signal_score_execution,
                max_resolution_hours=max_resolution_hours,
            )

            if not pf.ok:
                reason = pf.block_reason or "blocked"
                summary.skipped_blocked += 1
                summary.block_reasons[reason] = summary.block_reasons.get(reason, 0) + 1
                log.info(
                    "demo_execution BLOCKED proposal_id=%s market=%s reason=%s",
                    proposal_id,
                    market_ticker,
                    reason,
                )
                db.insert_demo_order_on_connection(
                    conn,
                    execution_run_id=run_id,
                    proposal_id=proposal_id,
                    market_ticker=market_ticker,
                    event_ticker=event_ticker_s,
                    side=str(row["side"]),
                    dry_run=dry_run,
                    client_order_id=None,
                    kalshi_order_id=None,
                    order_status="skipped_blocked",
                    request_json=None,
                    response_json=None,
                    block_reason=reason,
                )
                continue

            cid = new_client_order_id()
            try:
                req = build_demo_limit_order_request(
                    market_ticker=market_ticker,
                    side=str(row["side"]),
                    limit_price_dollars=str(row["proposed_limit_price_dollars"] or ""),
                    quantity=str(row["proposed_quantity"] or ""),
                    client_order_id=cid,
                )
            except ValueError as e:
                reason = f"build_order:{e}"
                summary.skipped_blocked += 1
                summary.block_reasons[reason] = summary.block_reasons.get(reason, 0) + 1
                db.insert_demo_order_on_connection(
                    conn,
                    execution_run_id=run_id,
                    proposal_id=proposal_id,
                    market_ticker=market_ticker,
                    event_ticker=event_ticker_s,
                    side=str(row["side"]),
                    dry_run=dry_run,
                    client_order_id=cid,
                    kalshi_order_id=None,
                    order_status="skipped_blocked",
                    request_json=None,
                    response_json=None,
                    block_reason=reason,
                )
                continue

            req_json = json.dumps(req, ensure_ascii=False)

            if dry_run:
                db.insert_demo_order_on_connection(
                    conn,
                    execution_run_id=run_id,
                    proposal_id=proposal_id,
                    market_ticker=market_ticker,
                    event_ticker=event_ticker_s,
                    side=str(row["side"]),
                    dry_run=True,
                    client_order_id=cid,
                    kalshi_order_id=None,
                    order_status="dry_run",
                    request_json=req_json,
                    response_json=json.dumps({"note": "dry_run_no_http"}, ensure_ascii=False),
                    block_reason=None,
                )
                summary.dry_run_recorded += 1
                log.info(
                    "demo_execution DRY_RUN proposal_id=%s market=%s client_order_id=%s",
                    proposal_id,
                    market_ticker,
                    cid,
                )
                continue

            try:
                resp = client.create_order(req)
            except KalshiHttpError as e:
                summary.skipped_blocked += 1
                br = f"http_error:{e}"
                summary.block_reasons[br] = summary.block_reasons.get(br, 0) + 1
                log.warning(
                    "demo_execution SUBMIT_FAILED proposal_id=%s market=%s err=%s",
                    proposal_id,
                    market_ticker,
                    e,
                )
                db.insert_demo_order_on_connection(
                    conn,
                    execution_run_id=run_id,
                    proposal_id=proposal_id,
                    market_ticker=market_ticker,
                    event_ticker=event_ticker_s,
                    side=str(row["side"]),
                    dry_run=False,
                    client_order_id=cid,
                    kalshi_order_id=None,
                    order_status="submit_failed",
                    request_json=req_json,
                    response_json=json.dumps({"error": str(e)}, ensure_ascii=False),
                    block_reason=str(e)[:2000],
                )
                continue

            order = resp.get("order") if isinstance(resp, dict) else None
            oid = None
            ost = "unknown"
            if isinstance(order, dict):
                oid = order.get("order_id")
                ost = str(order.get("status") or "unknown")
            db.insert_demo_order_on_connection(
                conn,
                execution_run_id=run_id,
                proposal_id=proposal_id,
                market_ticker=market_ticker,
                event_ticker=event_ticker_s,
                side=str(row["side"]),
                dry_run=False,
                client_order_id=cid,
                kalshi_order_id=str(oid) if oid else None,
                order_status=ost,
                request_json=req_json,
                response_json=json.dumps(resp, ensure_ascii=False),
                block_reason=None,
            )
            summary.submitted += 1
            log.info(
                "demo_execution SUBMITTED proposal_id=%s market=%s kalshi_order_id=%s status=%s",
                proposal_id,
                market_ticker,
                oid,
                ost,
            )

        conn.commit()

    log.info(
        "demo_execution done run_id=%s eligible=%d submitted=%d dry_run_recorded=%d skipped_blocked=%d top_blocks=%s",
        run_id,
        summary.eligible,
        summary.submitted,
        summary.dry_run_recorded,
        summary.skipped_blocked,
        sorted(summary.block_reasons.items(), key=lambda x: -x[1])[:10],
    )
    return summary


def reconcile_demo_orders(db: Db, client: KalshiClient, *, limit: int = 50) -> int:
    """Refresh status from Kalshi for recent non-dry orders with a kalshi_order_id."""
    n = 0
    rows = db.list_demo_orders_for_reconciliation(limit=limit)
    with db.connect() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for row in rows:
            oid = row["kalshi_order_id"]
            if not oid:
                continue
            try:
                data = client.get_order(str(oid))
            except KalshiHttpError as e:
                log.warning("reconcile GET order failed id=%s err=%s", oid, e)
                continue
            order = data.get("order") if isinstance(data, dict) else None
            if not isinstance(order, dict):
                continue
            st = str(order.get("status") or "unknown")
            db.update_demo_order_status_on_connection(
                conn,
                internal_id=int(row["id"]),
                order_status=st,
                response_json=json.dumps(data, ensure_ascii=False),
            )
            n += 1
        conn.commit()
    log.info("demo_execution reconcile updated=%d", n)
    return n
