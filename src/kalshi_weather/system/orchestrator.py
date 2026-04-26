from __future__ import annotations

import collections
import logging
import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from kalshi_weather.execution import (
    ExecutionEngineConfig,
    KalshiExecutionEngine,
    OrderIntent,
    RiskLimits,
    fetch_portfolio_positions,
    fetch_recent_fills_for_rolling_window,
)
from kalshi_weather.kalshi.client import KalshiClient
from kalshi_weather.system.datahub import load_candidates_within_days
from kalshi_weather.system.web_research import enrich_candidates_with_research
from kalshi_weather.system.swarm import (
    entry_context_agent,
    entry_critique_agent,
    entry_edge_agent,
    entry_final_orchestrator_agent,
    entry_scout_agent,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UnifiedWeatherOrchestratorConfig:
    model: str
    kalshi_env: str = "demo"
    mode: str = "dry_run"
    horizon_days: int = 2
    limit_candidate_markets: int = 12
    max_entry_orders_per_cycle: int = 4
    max_contracts_per_order: float = 8.0
    rolling_matched_contracts_15s: float = 40.0
    candidate_scan_multiplier: int = 4
    scout_override_priority_0_100: float = 70.0
    data_fetch_workers: int = 8
    weather_series_tag: str | None = None
    category_scope: str = "weather_only"
    restricted_to_live_bets: bool = False
    restricted_to_weather_family: bool = True
    selection_policy_notes: str = (
        "Broad discovery then top-N deep research; minimal hard rails for entry validity."
    )
    autonomy_profile: str = "high"
    top_n_deep_search: int = 6
    deep_search_timeout_s: float = 8.0
    min_liquidity_contracts: float = 8.0
    repeat_market_cooldown_minutes: int = 60
    final_orchestrator_temperature: float = 0.35


def _f(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def _to_json_obj(x: Any) -> Any:
    if is_dataclass(x):
        return asdict(x)
    model_dump = getattr(x, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    to_dict = getattr(x, "dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(x, (dict, list, str, int, float, bool)) or x is None:
        return x
    return str(x)


def _is_open_status(market: dict[str, Any]) -> bool:
    return str(market.get("status") or "").lower() in {"active", "initialized", "open"}


def _yes_spread_dollars(market: dict[str, Any]) -> float | None:
    yb = _f(market.get("yes_bid_dollars"))
    ya = _f(market.get("yes_ask_dollars"))
    if yb is None or ya is None:
        return None
    return max(0.0, ya - yb)


def _market_liquidity_contracts(market: dict[str, Any]) -> float:
    yb = _f(market.get("yes_bid_size_fp")) or 0.0
    ya = _f(market.get("yes_ask_size_fp")) or 0.0
    nb = _f(market.get("no_bid_size_fp")) or 0.0
    na = _f(market.get("no_ask_size_fp")) or 0.0
    return max(0.0, yb + ya + nb + na)


def _position_rows(positions_payload: dict[str, Any]) -> list[dict[str, Any]]:
    mps = positions_payload.get("market_positions")
    return [x for x in mps if isinstance(x, dict)] if isinstance(mps, list) else []


def _same_market_position_contracts(*, positions_payload: dict[str, Any], ticker: str) -> float:
    for row in _position_rows(positions_payload):
        mt = str(row.get("ticker") or "")
        if mt != ticker:
            continue
        return abs(_f(row.get("position_fp")) or 0.0)
    return 0.0


def _recent_fill_count_same_ticker(*, recent_fills: list[dict[str, Any]], ticker: str) -> int:
    hits = 0
    for row in recent_fills:
        if not isinstance(row, dict):
            continue
        mt = str(row.get("ticker") or row.get("market_ticker") or "")
        if mt == ticker:
            hits += 1
    return hits


def _build_repeat_guard(
    *,
    ticker: str,
    positions_payload: dict[str, Any],
    recent_fills: list[dict[str, Any]],
    cooldown_minutes: int,
) -> dict[str, Any]:
    same_pos = _same_market_position_contracts(positions_payload=positions_payload, ticker=ticker)
    same_fills = _recent_fill_count_same_ticker(recent_fills=recent_fills, ticker=ticker)
    blocked = same_pos > 0.0 or same_fills > 0
    reasons: list[str] = []
    if same_pos > 0.0:
        reasons.append("existing_open_position_same_market")
    if same_fills > 0:
        reasons.append("recent_fill_same_market")
    return {
        "repeat_blocked": blocked,
        "reasons": reasons,
        "same_market_open_contracts": same_pos,
        "recent_same_market_fill_count": same_fills,
        "cooldown_minutes": max(1, int(cooldown_minutes)),
    }


def _family_exposure_context(
    *,
    positions_payload: dict[str, Any],
    market_family: str,
) -> dict[str, Any]:
    active = _position_rows(positions_payload)
    total_abs_contracts = 0.0
    total_abs_exposure = 0.0
    for row in active:
        total_abs_contracts += abs(_f(row.get("position_fp")) or 0.0)
        total_abs_exposure += abs(_f(row.get("market_exposure_dollars")) or 0.0)
    return {
        "market_family": market_family,
        "open_positions_count": len(active),
        "total_abs_contracts": total_abs_contracts,
        "total_abs_exposure_dollars": total_abs_exposure,
    }


def _research_summary(web_research: dict[str, Any]) -> dict[str, Any]:
    source_status = web_research.get("source_status")
    sources: list[dict[str, Any]] = source_status if isinstance(source_status, list) else []
    ok = 0
    fail = 0
    source_names: list[str] = []
    for row in sources:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or "").strip()
        if source:
            source_names.append(source)
        if bool(row.get("ok")):
            ok += 1
        else:
            fail += 1
    hist = web_research.get("historical_weather")
    hist_rows = 0
    if isinstance(hist, dict):
        dr = hist.get("daily_rows")
        if isinstance(dr, list):
            hist_rows = len(dr)
    entities = web_research.get("entities")
    return {
        "sources_attempted": len(sources),
        "sources_ok": ok,
        "sources_failed": fail,
        "source_names": source_names,
        "historical_rows": hist_rows,
        "entities_count": len(entities) if isinstance(entities, list) else 0,
    }


def _build_entry_intent(*, ticker: str, side: str, qty: float, market: dict[str, Any], idx: int) -> OrderIntent | None:
    if side == "yes":
        px = _f(market.get("yes_ask_dollars"))
    else:
        px = _f(market.get("no_ask_dollars"))
    if px is None:
        return None
    return OrderIntent(
        ticker=ticker,
        side="no" if side == "no" else "yes",
        action="buy",
        count_fp=f"{max(1.0, qty):.2f}",
        policy="taker_ioc",
        limit_price_dollars=f"{px:.4f}",
        client_order_id=f"unified-entry-{int(time.time() * 1000)}-{idx}",
    )


def _aggregate_research(candidates: list[Any]) -> tuple[list[dict[str, Any]], set[str], int, int, int]:
    candidate_research = [
        _research_summary(c.web_research) for c in candidates if isinstance(c.web_research, dict)
    ]
    research_sources: set[str] = set()
    research_ok = 0
    research_failed = 0
    research_hist_rows = 0
    for rs in candidate_research:
        source_names = rs.get("source_names")
        if isinstance(source_names, list):
            for sn in source_names:
                research_sources.add(str(sn))
        research_ok += int(rs.get("sources_ok") or 0)
        research_failed += int(rs.get("sources_failed") or 0)
        research_hist_rows += int(rs.get("historical_rows") or 0)
    return candidate_research, research_sources, research_ok, research_failed, research_hist_rows


def _plan_entry_intents(
    candidates: list[Any],
    *,
    cfg: UnifiedWeatherOrchestratorConfig,
    positions_payload: dict[str, Any],
    recent_fills: list[dict[str, Any]],
) -> tuple[list[OrderIntent], collections.Counter[str], list[dict[str, Any]]]:
    dropped_entry: collections.Counter[str] = collections.Counter()
    entry_intents: list[OrderIntent] = []
    entry_trace: list[dict[str, Any]] = []
    for idx, c in enumerate(candidates):
        trace_row: dict[str, Any] = {
            "ticker": c.market_ticker,
            "phase": "entry",
            "market_family": getattr(c, "market_family", "unknown"),
        }
        trace_row["horizon_reason"] = c.horizon_reason
        trace_row["research_summary"] = (
            _research_summary(c.web_research)
            if isinstance(c.web_research, dict)
            else {"sources_attempted": 0}
        )
        if isinstance(c.web_research, dict):
            trace_row["research_context"] = c.web_research
        if len(entry_intents) >= cfg.max_entry_orders_per_cycle:
            break
        if not _is_open_status(c.market):
            dropped_entry["market_not_open"] += 1
            trace_row["decision"] = "drop"
            trace_row["reason"] = "market_not_open"
            entry_trace.append(trace_row)
            continue
        liq = _market_liquidity_contracts(c.market)
        trace_row["liquidity_contracts"] = liq
        if liq < max(0.0, float(cfg.min_liquidity_contracts)):
            dropped_entry["insufficient_liquidity"] += 1
            trace_row["decision"] = "drop"
            trace_row["reason"] = "insufficient_liquidity"
            entry_trace.append(trace_row)
            continue

        repeat_guard = _build_repeat_guard(
            ticker=c.market_ticker,
            positions_payload=positions_payload,
            recent_fills=recent_fills,
            cooldown_minutes=cfg.repeat_market_cooldown_minutes,
        )
        trace_row["repeat_guard"] = repeat_guard
        if bool(repeat_guard.get("repeat_blocked")):
            dropped_entry["repeat_bet_blocked"] += 1
            trace_row["decision"] = "drop"
            trace_row["reason"] = "repeat_bet_blocked"
            entry_trace.append(trace_row)
            continue

        scout = entry_scout_agent(model=cfg.model, c=c)
        trace_row["agent_outputs"] = {
            "scout": _to_json_obj(scout),
        }
        trace_row["scout_keep"] = scout.keep
        trace_row["scout_priority"] = scout.priority_0_100
        scout_overridden = False
        if (not scout.keep) and scout.priority_0_100 >= cfg.scout_override_priority_0_100:
            scout_overridden = True
            trace_row["scout_override"] = True
            trace_row["scout_override_reason"] = "priority_above_override_threshold"
        if not scout.keep and not scout_overridden:
            dropped_entry["scout_reject"] += 1
            trace_row["decision"] = "drop"
            trace_row["reason"] = "scout_reject"
            trace_row["scout_reason"] = scout.reason
            entry_trace.append(trace_row)
            continue

        context = entry_context_agent(model=cfg.model, c=c)
        edge = entry_edge_agent(model=cfg.model, c=c)
        critique = entry_critique_agent(model=cfg.model, c=c, edge=edge)
        final_decision = entry_final_orchestrator_agent(
            model=cfg.model,
            c=c,
            scout=scout,
            context=context,
            edge=edge,
            critique=critique,
            portfolio_context=_portfolio_diagnostics(positions_payload),
            repeat_guard=repeat_guard,
            exposure_context=_family_exposure_context(
                positions_payload=positions_payload,
                market_family=str(getattr(c, "market_family", "unknown")),
            ),
            temperature=max(0.0, min(1.0, float(cfg.final_orchestrator_temperature))),
        )
        trace_row["agent_outputs"] = {
            "scout": _to_json_obj(scout),
            "context": context,
            "edge": _to_json_obj(edge),
            "critique": critique,
            "final_orchestrator": _to_json_obj(final_decision),
        }
        trace_row["edge_side"] = edge.side
        trace_row["edge_prob"] = edge.edge_yes_prob
        trace_row["edge_confidence"] = edge.confidence_0_1
        trace_row["orchestrator_decision"] = final_decision.decision
        trace_row["orchestrator_confidence"] = final_decision.confidence_score_0_1
        if final_decision.decision in {"SKIP", "WAIT"}:
            dropped_entry["final_orchestrator_skip"] += 1
            trace_row["decision"] = "drop"
            trace_row["reason"] = "final_orchestrator_skip"
            trace_row["structured_rejection_reasons"] = final_decision.structured_rejection_reasons
            entry_trace.append(trace_row)
            continue

        if bool(critique.get("veto")):
            dropped_entry["critique_veto"] += 1
            trace_row["decision"] = "drop"
            trace_row["reason"] = "critique_veto"
            entry_trace.append(trace_row)
            continue

        recommended_qty = max(0.0, float(final_decision.recommended_size))
        if final_decision.decision == "REDUCE_SIZE":
            recommended_qty = max(1.0, min(recommended_qty or cfg.max_contracts_per_order, cfg.max_contracts_per_order * 0.5))
        qty = min(cfg.max_contracts_per_order, max(1.0, recommended_qty))
        intent = _build_entry_intent(
            ticker=c.market_ticker,
            side=final_decision.recommended_side,
            qty=qty,
            market=c.market,
            idx=idx,
        )
        if intent is None:
            dropped_entry["missing_limit_price"] += 1
            trace_row["decision"] = "drop"
            trace_row["reason"] = "missing_limit_price"
            entry_trace.append(trace_row)
            continue
        entry_intents.append(intent)
        trace_row["decision"] = "intent"
        trace_row["reason"] = "passed_final_orchestrator_and_hard_rails"
        trace_row["intent_side"] = intent.side
        trace_row["intent_qty"] = intent.count_fp
        trace_row["reasoning_summary"] = final_decision.reasoning_summary
        trace_row["key_risks"] = final_decision.key_risks
        entry_trace.append(trace_row)
    return entry_intents, dropped_entry, entry_trace


def _portfolio_diagnostics(positions_payload: dict[str, Any]) -> dict[str, float | int]:
    market_positions = positions_payload.get("market_positions")
    active_positions = (
        [x for x in market_positions if isinstance(x, dict)]
        if isinstance(market_positions, list)
        else []
    )
    total_abs_contracts = 0.0
    total_abs_exposure = 0.0
    for mp in active_positions:
        p = _f(mp.get("position_fp")) or 0.0
        e = _f(mp.get("market_exposure_dollars")) or 0.0
        total_abs_contracts += abs(p)
        total_abs_exposure += abs(e)
    return {
        "open_positions_seen": len(active_positions),
        "total_abs_contracts": total_abs_contracts,
        "total_abs_exposure_dollars": total_abs_exposure,
    }


def run_unified_weather_cycle(
    client: KalshiClient,
    *,
    cfg: UnifiedWeatherOrchestratorConfig,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "mode": cfg.mode,
        "horizon_days": cfg.horizon_days,
        "category_scope": cfg.category_scope,
        "weather_series_tag": cfg.weather_series_tag,
        "stage_timing_s": {},
        "selection_policy": {
            "restricted_to_live_bets": bool(cfg.restricted_to_live_bets),
            "restricted_to_weather_family": bool(cfg.restricted_to_weather_family),
            "limit_driver": "horizon_days",
            "notes": cfg.selection_policy_notes,
        },
        "entry": {},
        "portfolio": {},
    }
    t0 = time.perf_counter()

    # Entry path: deterministic weather shortlist -> specialist agents -> deterministic execution guard.
    candidate_scan_target = max(
        int(cfg.limit_candidate_markets),
        int(cfg.max_entry_orders_per_cycle) * max(1, int(cfg.candidate_scan_multiplier)),
    )
    t_entry_load0 = time.perf_counter()
    logger.info(
        "[PRE-AGENTS][ENTRY] loading candidates horizon_days=%s target=%s",
        cfg.horizon_days,
        candidate_scan_target,
    )
    candidates_raw = load_candidates_within_days(
        client,
        horizon_days=cfg.horizon_days,
        limit_markets=candidate_scan_target,
        data_fetch_workers=max(1, int(cfg.data_fetch_workers)),
        weather_series_tag=cfg.weather_series_tag,
    )
    diagnostics["stage_timing_s"]["entry_candidate_load"] = round(time.perf_counter() - t_entry_load0, 3)
    logger.info(
        "[PRE-AGENTS][ENTRY] loaded candidates=%s in %.3fs",
        len(candidates_raw),
        diagnostics["stage_timing_s"]["entry_candidate_load"],
    )
    t_portfolio0 = time.perf_counter()
    positions_payload = fetch_portfolio_positions(client)
    recent_fills = fetch_recent_fills_for_rolling_window(client, now_ts=time.time())
    diagnostics["stage_timing_s"]["portfolio_and_fills_load"] = round(
        time.perf_counter() - t_portfolio0, 3
    )

    t_deep0 = time.perf_counter()
    candidates = enrich_candidates_with_research(
        candidates=candidates_raw,
        top_n_deep_search=max(0, int(cfg.top_n_deep_search)),
        max_workers=max(1, int(cfg.data_fetch_workers)),
        timeout_s=max(2.0, float(cfg.deep_search_timeout_s)),
    )
    diagnostics["stage_timing_s"]["entry_deep_research"] = round(time.perf_counter() - t_deep0, 3)
    (
        candidate_research,
        research_sources,
        research_ok,
        research_failed,
        research_hist_rows,
    ) = _aggregate_research(candidates)
    logger.info(
        "[PRE-AGENTS][ENTRY] research markets=%s sources=%s checks_ok=%s checks_failed=%s historical_rows=%s",
        len(candidate_research),
        len(research_sources),
        research_ok,
        research_failed,
        research_hist_rows,
    )
    logger.info(
        "[AGENTS][ENTRY] planning intents candidates=%s max_orders=%s",
        len(candidates),
        cfg.max_entry_orders_per_cycle,
    )
    entry_intents, dropped_entry, entry_trace = _plan_entry_intents(
        candidates,
        cfg=cfg,
        positions_payload=positions_payload,
        recent_fills=recent_fills,
    )

    exec_cfg = ExecutionEngineConfig(
        mode=cfg.mode,  # type: ignore[arg-type]
        prefer_batch=True,
        risk=RiskLimits(
            per_market_max_contracts=max(1.0, cfg.max_contracts_per_order * 2.0),
            per_category_max_exposure_dollars=500.0,
            per_event_max_loss_dollars=200.0,
            rolling_matched_contracts_15s=cfg.rolling_matched_contracts_15s,
            allow_scalar_and_combo=False,
        ),
        use_order_groups_for_rolling=True,
    )
    eng = KalshiExecutionEngine(client, config=exec_cfg)

    entry_markets = {c.market_ticker: c.market for c in candidates if isinstance(c.market, dict)}
    entry_orderbooks = {c.market_ticker: c.orderbook for c in candidates if isinstance(c.orderbook, dict)}
    t_entry_exec0 = time.perf_counter()
    entry_result = eng.execute_batch(
        entry_intents,
        portfolio=positions_payload,
        markets_by_ticker=entry_markets,
        orderbooks_by_ticker=entry_orderbooks,
        recent_fills=recent_fills,
        now_ts=time.time(),
    )
    diagnostics["stage_timing_s"]["entry_execution"] = round(time.perf_counter() - t_entry_exec0, 3)

    diagnostics["entry"] = {
        "candidates_scan_target": candidate_scan_target,
        "candidates_seen": len(candidates_raw),
        "candidates_after_research": len(candidates),
        "top_n_deep_search": max(0, int(cfg.top_n_deep_search)),
        "autonomy_profile": cfg.autonomy_profile,
        "research": {
            "markets_with_research": len(candidate_research),
            "sources_used": sorted(research_sources),
            "source_checks_ok": research_ok,
            "source_checks_failed": research_failed,
            "historical_rows_collected": research_hist_rows,
        },
        "intents_attempted": len(entry_intents),
        "dropped_reasons": dict(dropped_entry),
        "submitted": sum(1 for r in entry_result.results if r.status == "submitted"),
        "risk_rejected": sum(1 for r in entry_result.results if r.status == "risk_rejected"),
        "errors": sum(1 for r in entry_result.results if r.status == "error"),
        "skipped_read_only": sum(1 for r in entry_result.results if r.status == "skipped_read_only"),
        "decision_trace": entry_trace,
    }

    diagnostics["portfolio"] = _portfolio_diagnostics(positions_payload)
    diagnostics["stage_timing_s"]["total_cycle"] = round(time.perf_counter() - t0, 3)
    return diagnostics

