from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from kalshi_weather.ai.agents import (
    bet_validity_agent,
    critic_agent,
    expected_value_agent,
    historical_context_agent,
    journal_agent,
    live_market_analysis_agent,
    signal_advisory_agent,
)
from kalshi_weather.ai.graph import build_ai_graph
from kalshi_weather.ai.no_data_messages import journal_for_no_live_monitor_snapshots
from kalshi_weather.ai.state import AIState
from kalshi_weather.config import get_settings
from kalshi_weather.db.db import Db
from kalshi_weather.proposals.signal_layer import enrich_snapshots_with_signals, signal_params_from_settings


def _coerce_invoke_result(result: Any, fallback: AIState) -> AIState:
    """LangGraph may return a plain dict for dataclass state; normalize to AIState."""
    if isinstance(result, AIState):
        return result
    if isinstance(result, dict):
        return AIState(
            run_id=str(result.get("run_id") or fallback.run_id),
            now_utc_iso=str(result.get("now_utc_iso") or fallback.now_utc_iso),
            live_snapshots=list(result.get("live_snapshots") or []),
            signal_advisory=dict(result.get("signal_advisory") or {}),
            candidate_markets=list(result.get("candidate_markets") or []),
            live_analysis=dict(result.get("live_analysis") or {}),
            historical_context=dict(result.get("historical_context") or {}),
            ev_analysis=dict(result.get("ev_analysis") or {}),
            validity=dict(result.get("validity") or {}),
            critic=dict(result.get("critic") or {}),
            journal=dict(result.get("journal") or {}),
            stop_reason=result.get("stop_reason"),
        )
    raise TypeError(f"Unexpected graph.invoke() return type: {type(result)}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")


def _load_latest_snapshots_per_market(db: Db, *, limit_markets: int = 200) -> list[dict[str, Any]]:
    with db.connect() as conn:
        cur = conn.execute(
            """
            SELECT s.id, s.observed_at, s.market_json
            FROM live_monitor_snapshots s
            INNER JOIN (
              SELECT market_ticker, MAX(id) AS mid
              FROM live_monitor_snapshots
              WHERE IFNULL(is_stale, 0) = 0
              GROUP BY market_ticker
            ) t ON s.market_ticker = t.market_ticker AND s.id = t.mid
            ORDER BY s.observed_at DESC
            LIMIT ?
            """,
            (limit_markets,),
        )
        rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            mj = json.loads(r[2]) if isinstance(r[2], str) else {}
        except json.JSONDecodeError:
            mj = {}
        out.append({"id": int(r[0]), "observed_at": str(r[1]), "market_json": mj})
    return out


def run_ai_workflow_once(
    db: Db,
    *,
    model: str,
    limit_markets: int = 200,
    snapshot_max_age_seconds: int = 1800,
) -> AIState:
    run_id = str(uuid.uuid4())
    state = AIState(run_id=run_id, now_utc_iso=_now_iso())
    settings = get_settings(load_dotenv_file=True)
    sig_params = signal_params_from_settings(settings)

    db.insert_run(run_id=run_id, status="running", meta={"type": "ai_workflow", "model": model})

    def log_node(name: str, payload: dict[str, Any]) -> None:
        db.insert_agent_log(run_id=run_id, level="INFO", message=name, data=payload)

    def load_data(s: AIState) -> AIState:
        snaps = _load_latest_snapshots_per_market(db, limit_markets=limit_markets)
        s.live_snapshots = enrich_snapshots_with_signals(db, snaps, params=sig_params)
        log_node("load_data", {"markets": len(s.live_snapshots)})
        if not s.live_snapshots:
            s.stop_reason = "no_live_monitor_snapshots"
        return s

    def signal_advisory_step(s: AIState) -> AIState:
        if s.stop_reason:
            return s
        out = signal_advisory_agent(model=model, enriched_snapshots=s.live_snapshots)
        s.signal_advisory = out
        log_node(
            "signal_advisory",
            {
                "advisory_tickers": list((out.get("advisory_by_ticker") or {}).keys())[:40],
                "overall_note": (out.get("overall_note") or "")[:500],
            },
        )
        return s

    def live_analysis(s: AIState) -> AIState:
        if s.stop_reason:
            return s
        out = live_market_analysis_agent(model=model, live_snapshots=s.live_snapshots)
        s.live_analysis = out
        cands = []
        for c in out.get("candidates") or []:
            if isinstance(c, dict) and isinstance(c.get("ticker"), str):
                cands.append(c["ticker"])
        s.candidate_markets = list(dict.fromkeys(cands))
        log_node("live_analysis", {"candidates": s.candidate_markets, "raw": out})
        if not s.candidate_markets:
            s.stop_reason = "no_candidates"
        return s

    def historical_context(s: AIState) -> AIState:
        if s.stop_reason:
            return s
        cset = set(s.candidate_markets)
        props = []
        for r in db.recent_proposals(limit=200):
            mt = r["market_ticker"]
            if isinstance(mt, str) and mt in cset:
                props.append(
                    {
                        "created_at": r["created_at"],
                        "proposal_id": r["proposal_id"],
                        "market_ticker": r["market_ticker"],
                        "outcome": r["guard_outcome"],
                        "limit_px": r["proposed_limit_price_dollars"],
                        "qty": r["proposed_quantity"],
                        "rejection_reason": r["rejection_reason"],
                    }
                )
        orders = []
        for r in db.recent_demo_orders(limit=200):
            mt = r["market_ticker"]
            if isinstance(mt, str) and mt in cset:
                orders.append(
                    {
                        "created_at": r["created_at"],
                        "proposal_id": r["proposal_id"],
                        "market_ticker": r["market_ticker"],
                        "dry_run": bool(r["dry_run"]),
                        "order_status": r["order_status"],
                        "block_reason": r["block_reason"],
                    }
                )
        out = historical_context_agent(
            model=model, proposals_recent=props, demo_orders_recent=orders, candidates=s.candidate_markets
        )
        s.historical_context = out
        log_node("historical_context", out)
        return s

    def ev(s: AIState) -> AIState:
        if s.stop_reason:
            return s
        out = expected_value_agent(
            model=model,
            live_snapshots=s.live_snapshots,
            context=s.historical_context,
            candidates=s.candidate_markets,
        )
        s.ev_analysis = out
        log_node("expected_value", out)
        return s

    def validity(s: AIState) -> AIState:
        if s.stop_reason:
            return s
        out = bet_validity_agent(
            model=model,
            candidates=s.candidate_markets,
            live_snapshots=s.live_snapshots,
            snapshot_max_age_seconds=snapshot_max_age_seconds,
        )
        s.validity = out
        log_node("bet_validity", out)
        return s

    def critic(s: AIState) -> AIState:
        if s.stop_reason:
            return s
        out = critic_agent(model=model, ev=s.ev_analysis, validity=s.validity)
        s.critic = out
        log_node("critic", out)
        return s

    def journal(s: AIState) -> AIState:
        if s.stop_reason == "no_live_monitor_snapshots":
            out = journal_for_no_live_monitor_snapshots()
            s.journal = out
            log_node("journal", out)
            return s
        out = journal_agent(
            model=model,
            live_analysis=s.live_analysis,
            context=s.historical_context,
            ev=s.ev_analysis,
            validity=s.validity,
            critic=s.critic,
            signal_advisory=s.signal_advisory,
        )
        s.journal = out
        log_node("journal", out)
        return s

    graph = build_ai_graph(
        {
            "load_data": load_data,
            "signal_advisory": signal_advisory_step,
            "live_analysis": live_analysis,
            "historical_context": historical_context,
            "ev": ev,
            "validity": validity,
            "critic": critic,
            "journal": journal,
        }
    )

    raw = graph.invoke(state)
    final_state = _coerce_invoke_result(raw, state)
    end_meta: dict[str, Any] = {
        "type": "ai_workflow",
        "model": model,
        "stop_reason": final_state.stop_reason,
        "candidates": final_state.candidate_markets,
        "milestone": "m7_signal_layer",
        "signal_advisory": final_state.signal_advisory,
    }
    if final_state.stop_reason == "no_live_monitor_snapshots" and final_state.journal:
        end_meta["no_data_explanation"] = final_state.journal
    db.end_run(
        run_id=run_id,
        status="completed" if not final_state.stop_reason else "stopped",
        meta=end_meta,
    )
    return final_state

