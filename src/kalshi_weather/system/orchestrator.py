from __future__ import annotations

import collections
import logging
import time
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kalshi_weather.db.db import Db
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
from kalshi_weather.system.contracts import CandidateContext, HardRailFailure
from kalshi_weather.system.web_research import enrich_candidates_with_research
from kalshi_weather.system.swarm import (
    entry_context_agent,
    entry_critique_agent,
    entry_edge_agent,
    entry_final_orchestrator_agent,
    entry_scout_agent,
)
from kalshi_weather.execution.risk import matched_contracts_in_window

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UnifiedWeatherOrchestratorConfig:
    model_scout: str
    model_context: str
    model_edge: str
    model_critique: str
    model_final: str
    kalshi_env: str = "demo"
    mode: str = "dry_run"
    horizon_days: int = 2
    limit_candidate_markets: int = 12
    max_entry_orders_per_cycle: int = 4
    max_contracts_per_order: float = 8.0
    rolling_matched_contracts_15s: float = 40.0
    candidate_scan_multiplier: int = 4
    candidate_selection_mode: str = "ranked"
    candidate_selection_pool_multiplier: int = 3
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
    repeat_thesis_cooldown_minutes: int = 90
    final_orchestrator_temperature: float = 0.35
    db_path: Path | None = None


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


def _parse_iso(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _seconds_since(iso_ts: Any) -> float | None:
    dt = _parse_iso(iso_ts)
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _is_open_status(market: dict[str, Any]) -> bool:
    return str(market.get("status") or "").lower() in {"active", "initialized", "open"}


def _market_liquidity_contracts(market: dict[str, Any]) -> float:
    yb = _f(market.get("yes_bid_size_fp")) or 0.0
    ya = _f(market.get("yes_ask_size_fp")) or 0.0
    nb = _f(market.get("no_bid_size_fp")) or 0.0
    na = _f(market.get("no_ask_size_fp")) or 0.0
    return max(0.0, yb + ya + nb + na)


def _market_quote(market: dict[str, Any], *, side: str, level: str) -> float | None:
    side_key = "no" if side == "no" else "yes"
    lvl_key = "ask" if level == "ask" else "bid"
    px = _f(market.get(f"{side_key}_{lvl_key}_dollars"))
    if px is not None:
        return px
    # Some payloads expose direct price fields without *_dollars suffix.
    return _f(market.get(f"{side_key}_{lvl_key}"))


def _yes_spread_dollars(market: dict[str, Any]) -> float | None:
    yb = _market_quote(market, side="yes", level="bid")
    ya = _market_quote(market, side="yes", level="ask")
    if yb is None or ya is None:
        return None
    return max(0.0, ya - yb)


def _deterministic_fallback_entry_eval(
    *,
    market: dict[str, Any],
    repeat_guard: dict[str, Any],
    edge_side: str,
    edge_prob: float,
    edge_confidence: float,
    critique: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    # Keep fallback strict so it only unlocks high-quality opportunities.
    details: dict[str, Any] = {
        "repeat_blocked": bool(repeat_guard.get("repeat_blocked")),
        "critique_veto": bool(critique.get("veto")),
        "edge_confidence": float(edge_confidence),
        "edge_abs_prob": abs(float(edge_prob)),
    }
    if details["repeat_blocked"]:
        return False, {**details, "failed_on": "repeat_blocked"}
    if details["critique_veto"] and not (edge_confidence >= 0.8 and abs(edge_prob) >= 0.25):
        return False, {**details, "failed_on": "critique_veto"}
    if edge_confidence < 0.6:
        return False, {**details, "failed_on": "edge_confidence"}
    if abs(edge_prob) < 0.15:
        return False, {**details, "failed_on": "edge_abs_prob"}
    spread = _yes_spread_dollars(market)
    details["yes_spread_dollars"] = spread
    if spread is None or spread > 0.35:
        return False, {**details, "failed_on": "yes_spread"}
    side = "no" if edge_side == "no" else "yes"
    ask = _market_quote(market, side=side, level="ask")
    details["side"] = side
    details["side_ask"] = ask
    if ask is None:
        return False, {**details, "failed_on": "side_ask_missing"}
    # Avoid degenerate tail pricing for fallback auto-entry.
    if not (0.02 <= ask <= 0.98):
        return False, {**details, "failed_on": "side_ask_range"}
    return True, {**details, "failed_on": None}


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


def _enrich_market_state_candidates(
    *,
    db: Db | None,
    candidates: list[CandidateContext],
    positions_payload: dict[str, Any],
) -> list[CandidateContext]:
    if not candidates:
        return candidates
    out: list[CandidateContext] = []
    for c in candidates:
        market_state = db.get_market_state(ticker=c.market_ticker) if db else None
        bundle = c.evidence_bundle
        thesis_key = bundle.thesis_key if bundle else ""
        thesis_state = db.get_thesis_state(thesis_key=thesis_key) if (db and thesis_key) else None
        recent = (
            db.recent_market_decisions(
                ticker=c.market_ticker,
                event_ticker=str(c.market.get("event_ticker") or c.event.get("event_ticker") or ""),
                family=c.market_family,
                limit=8,
            )
            if db
            else []
        )
        decision_s = _seconds_since((market_state or {}).get("last_decision_time"))
        bet_s = _seconds_since((thesis_state or {}).get("last_bet_time"))
        repeat_count = int((thesis_state or {}).get("repeat_count") or 0)
        same_thesis_recently = bool(repeat_count > 0 and (bet_s is None or bet_s < 6 * 3600))
        same_pos = _same_market_position_contracts(
            positions_payload=positions_payload,
            ticker=c.market_ticker,
        )
        enriched_inputs = dict(c.deterministic_inputs)
        enriched_inputs.update(
            {
                "market_state": market_state or {},
                "thesis_state": thesis_state or {},
                "time_since_last_decision_seconds": decision_s,
                "time_since_last_bet_seconds": bet_s,
                "repeat_count": repeat_count,
                "same_thesis_recently": same_thesis_recently,
                "open_exposure_contracts_same_market": same_pos,
                "evidence_novelty_available": thesis_state is not None,
            }
        )
        out.append(
            CandidateContext(
                market_ticker=c.market_ticker,
                market_family=c.market_family,
                market=c.market,
                event=c.event,
                orderbook=c.orderbook,
                horizon_reason=c.horizon_reason,
                web_research=c.web_research,
                evidence_quality=c.evidence_quality,
                freshness_meta=c.freshness_meta,
                source_reliability=c.source_reliability,
                deterministic_inputs=enriched_inputs,
                evidence_bundle=bundle,
                market_state=market_state or {},
                thesis_state=thesis_state or {},
                recent_decisions=recent,
                exposure_context={"open_same_market_contracts": same_pos},
            )
        )
    return out


def _hard_rail_failure(
    *,
    rail_name: str,
    severity: str,
    current_value: Any,
    threshold: Any,
    explanation: str,
    hard_blocking: bool = True,
) -> dict[str, Any]:
    return asdict(
        HardRailFailure(
            rail_name=rail_name,
            severity="critical" if severity == "critical" else "warning" if severity == "warning" else "info",
            current_value=current_value,
            threshold=threshold,
            hard_blocking=hard_blocking,
            explanation=explanation,
        )
    )


def _evaluate_hard_rails(
    *,
    c: CandidateContext,
    cfg: UnifiedWeatherOrchestratorConfig,
    repeat_guard: dict[str, Any],
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    if not _is_open_status(c.market):
        failures.append(
            _hard_rail_failure(
                rail_name="market_open",
                severity="critical",
                current_value=str(c.market.get("status") or ""),
                threshold="active|initialized|open",
                explanation="Market is not open for entry.",
            )
        )
    liq = _market_liquidity_contracts(c.market)
    if liq < max(0.0, float(cfg.min_liquidity_contracts)):
        failures.append(
            _hard_rail_failure(
                rail_name="basic_liquidity",
                severity="critical",
                current_value=liq,
                threshold=max(0.0, float(cfg.min_liquidity_contracts)),
                explanation="Combined displayed liquidity is below the minimum floor.",
            )
        )
    if bool(repeat_guard.get("repeat_blocked")):
        failures.append(
            _hard_rail_failure(
                rail_name="anti_repeat_buffer",
                severity="critical",
                current_value=repeat_guard,
                threshold={
                    "market_cooldown_minutes": cfg.repeat_market_cooldown_minutes,
                    "thesis_cooldown_minutes": cfg.repeat_thesis_cooldown_minutes,
                },
                explanation="Recent same-market or same-thesis activity triggered repeat protection.",
            )
        )
    return {"passed": len(failures) == 0, "failures": failures}


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
    px = _market_quote(market, side=side, level="ask")
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
    db: Db | None,
) -> tuple[list[OrderIntent], collections.Counter[str], list[dict[str, Any]]]:
    dropped_entry: collections.Counter[str] = collections.Counter()
    entry_intents: list[OrderIntent] = []
    entry_trace: list[dict[str, Any]] = []
    for idx, c in enumerate(candidates):
        trace_row: dict[str, Any] = {
            "ticker": c.market_ticker,
            "phase": "entry",
            "market_family": getattr(c, "market_family", "unknown"),
            "event_ticker": c.market.get("event_ticker") or c.event.get("event_ticker") or c.event.get("ticker"),
            "event_title": c.event.get("title") or c.market.get("event_title"),
            "market_title": c.market.get("title"),
            "event_market_count": c.deterministic_inputs.get("event_market_count"),
            "market_option_kind": c.deterministic_inputs.get("market_option_kind"),
            "market_option_label": c.deterministic_inputs.get("market_option_label"),
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
        repeat_guard = _build_repeat_guard(
            ticker=c.market_ticker,
            positions_payload=positions_payload,
            recent_fills=recent_fills,
            cooldown_minutes=cfg.repeat_market_cooldown_minutes,
        )
        thesis_state = c.thesis_state if isinstance(c.thesis_state, dict) else {}
        t_last_decision_s = _seconds_since(thesis_state.get("last_decision_time"))
        t_last_bet_s = _seconds_since(thesis_state.get("last_bet_time"))
        repeat_count = int(thesis_state.get("repeat_count") or 0)
        if repeat_count > 0 and (t_last_decision_s is None or t_last_decision_s < cfg.repeat_thesis_cooldown_minutes * 60):
            repeat_guard["repeat_blocked"] = True
            reasons = repeat_guard.get("reasons")
            if not isinstance(reasons, list):
                reasons = []
            reasons.append("same_thesis_recent")
            repeat_guard["reasons"] = sorted({str(r) for r in reasons})
            repeat_guard["repeat_count"] = repeat_count
            repeat_guard["time_since_last_decision_seconds"] = t_last_decision_s
            repeat_guard["time_since_last_bet_seconds"] = t_last_bet_s
        trace_row["repeat_guard"] = repeat_guard
        trace_row["liquidity_contracts"] = _market_liquidity_contracts(c.market)
        hard_rail = _evaluate_hard_rails(c=c, cfg=cfg, repeat_guard=repeat_guard)
        trace_row["hard_rail_result"] = hard_rail
        if not bool(hard_rail.get("passed")):
            names = [str(f.get("rail_name") or "") for f in hard_rail.get("failures", []) if isinstance(f, dict)]
            if "market_open" in names:
                dropped_entry["market_not_open"] += 1
            elif "basic_liquidity" in names:
                dropped_entry["insufficient_liquidity"] += 1
            else:
                dropped_entry["repeat_bet_blocked"] += 1
            trace_row["decision"] = "drop"
            trace_row["reason"] = "hard_rail_blocked"
            entry_trace.append(trace_row)
            if db is not None:
                db.upsert_market_state(
                    ticker=c.market_ticker,
                    event_ticker=str(c.market.get("event_ticker") or c.event.get("event_ticker") or ""),
                    family=c.market_family,
                    last_decision="SKIP",
                    last_reasoning="Hard rail blocked entry.",
                    last_price_seen=_f(c.market.get("yes_ask_dollars")),
                )
            continue

        scout = entry_scout_agent(model=cfg.model_scout, c=c)
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
            # Scout is advisory, not terminal. FinalOrchestrator still makes the
            # go/no-go decision with richer context and deterministic constraints.
            trace_row["scout_soft_reject"] = True
            trace_row["scout_reason"] = str(scout.reason or "").strip() or "Scout rejected candidate."

        context = entry_context_agent(model=cfg.model_context, c=c)
        edge = entry_edge_agent(model=cfg.model_edge, c=c)
        critique = entry_critique_agent(model=cfg.model_critique, c=c, edge=edge)
        final_decision = entry_final_orchestrator_agent(
            model=cfg.model_final,
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
            evidence_bundle=_to_json_obj(c.evidence_bundle),
            market_state=c.market_state if isinstance(c.market_state, dict) else {},
            thesis_state=thesis_state,
            recent_decisions=c.recent_decisions if isinstance(c.recent_decisions, list) else [],
            freshness_meta=c.freshness_meta if isinstance(c.freshness_meta, dict) else {},
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
        trace_row["thesis_state"] = thesis_state
        trace_row["market_state"] = c.market_state if isinstance(c.market_state, dict) else {}
        trace_row["evidence_hash"] = (
            c.evidence_bundle.evidence_hash if c.evidence_bundle else ""
        )
        if final_decision.decision in {"SKIP", "WAIT"}:
            fallback_ok, fallback_eval = _deterministic_fallback_entry_eval(
                market=c.market,
                repeat_guard=repeat_guard,
                edge_side=edge.side,
                edge_prob=float(edge.edge_yes_prob),
                edge_confidence=float(edge.confidence_0_1),
                critique=critique if isinstance(critique, dict) else {},
            )
            trace_row["fallback_eval"] = fallback_eval
            if fallback_ok:
                qty = min(cfg.max_contracts_per_order, 1.0)
                intent = _build_entry_intent(
                    ticker=c.market_ticker,
                    side=edge.side,
                    qty=qty,
                    market=c.market,
                    idx=idx,
                )
                if intent is not None:
                    entry_intents.append(intent)
                    trace_row["decision"] = "intent"
                    trace_row["reason"] = "deterministic_fallback_after_orchestrator_skip"
                    trace_row["intent_side"] = intent.side
                    trace_row["intent_qty"] = intent.count_fp
                    trace_row["fallback_reason"] = (
                        "final_orchestrator_skip_or_wait_but_high_confidence_edge_and_clean_rails"
                    )
                    trace_row["reasoning_summary"] = final_decision.reasoning_summary
                    trace_row["key_risks"] = final_decision.key_risks
                    trace_row["execution_intent"] = _to_json_obj(intent)
                    entry_trace.append(trace_row)
                    if db is not None:
                        bundle_snapshot = _to_json_obj(c.evidence_bundle) if c.evidence_bundle else {}
                        db.upsert_market_state(
                            ticker=c.market_ticker,
                            event_ticker=str(c.market.get("event_ticker") or c.event.get("event_ticker") or ""),
                            family=c.market_family,
                            last_decision="ENTER",
                            last_reasoning=(
                                "Deterministic fallback intent after orchestrator skip/wait under strict quality checks."
                            ),
                            last_forecast_snapshot=bundle_snapshot if isinstance(bundle_snapshot, dict) else {},
                            last_price_seen=_f(c.market.get("yes_ask_dollars")),
                        )
                        if c.evidence_bundle and c.evidence_bundle.thesis_key:
                            db.upsert_thesis_state(
                                thesis_key=c.evidence_bundle.thesis_key,
                                ticker=c.market_ticker,
                                event_ticker=str(c.market.get("event_ticker") or c.event.get("event_ticker") or ""),
                                family=c.market_family,
                                decision="ENTER",
                                reasoning=(
                                    "Deterministic fallback intent after orchestrator skip/wait under strict quality checks."
                                ),
                                evidence_hash=c.evidence_bundle.evidence_hash,
                                forecast_snapshot=bundle_snapshot if isinstance(bundle_snapshot, dict) else {},
                                bet_placed=True,
                            )
                    continue
            dropped_entry["final_orchestrator_skip"] += 1
            trace_row["decision"] = "drop"
            trace_row["reason"] = "final_orchestrator_skip"
            trace_row["structured_rejection_reasons"] = final_decision.structured_rejection_reasons
            entry_trace.append(trace_row)
            if db is not None:
                bundle_snapshot = _to_json_obj(c.evidence_bundle) if c.evidence_bundle else {}
                db.upsert_market_state(
                    ticker=c.market_ticker,
                    event_ticker=str(c.market.get("event_ticker") or c.event.get("event_ticker") or ""),
                    family=c.market_family,
                    last_decision=final_decision.decision,
                    last_reasoning=final_decision.reasoning_summary,
                    last_forecast_snapshot=bundle_snapshot if isinstance(bundle_snapshot, dict) else {},
                    last_price_seen=_f(c.market.get("yes_ask_dollars")),
                )
                if c.evidence_bundle and c.evidence_bundle.thesis_key:
                    db.upsert_thesis_state(
                        thesis_key=c.evidence_bundle.thesis_key,
                        ticker=c.market_ticker,
                        event_ticker=str(c.market.get("event_ticker") or c.event.get("event_ticker") or ""),
                        family=c.market_family,
                        decision=final_decision.decision,
                        reasoning=final_decision.reasoning_summary,
                        evidence_hash=c.evidence_bundle.evidence_hash,
                        forecast_snapshot=bundle_snapshot if isinstance(bundle_snapshot, dict) else {},
                        bet_placed=False,
                    )
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
        trace_row["execution_intent"] = _to_json_obj(intent)
        entry_trace.append(trace_row)

        if db is not None:
            bundle_snapshot = _to_json_obj(c.evidence_bundle) if c.evidence_bundle else {}
            db.upsert_market_state(
                ticker=c.market_ticker,
                event_ticker=str(c.market.get("event_ticker") or c.event.get("event_ticker") or ""),
                family=c.market_family,
                last_decision=final_decision.decision,
                last_reasoning=final_decision.reasoning_summary,
                last_forecast_snapshot=bundle_snapshot if isinstance(bundle_snapshot, dict) else {},
                last_price_seen=_f(c.market.get("yes_ask_dollars")),
            )
            if c.evidence_bundle and c.evidence_bundle.thesis_key:
                db.upsert_thesis_state(
                    thesis_key=c.evidence_bundle.thesis_key,
                    ticker=c.market_ticker,
                    event_ticker=str(c.market.get("event_ticker") or c.event.get("event_ticker") or ""),
                    family=c.market_family,
                    decision=final_decision.decision,
                    reasoning=final_decision.reasoning_summary,
                    evidence_hash=c.evidence_bundle.evidence_hash,
                    forecast_snapshot=bundle_snapshot if isinstance(bundle_snapshot, dict) else {},
                    bet_placed=final_decision.decision in {"ENTER", "REDUCE_SIZE"},
                )
    return entry_intents, dropped_entry, entry_trace


def _portfolio_diagnostics(positions_payload: dict[str, Any]) -> dict[str, float | int]:
    market_positions = positions_payload.get("market_positions")
    raw_positions = (
        [x for x in market_positions if isinstance(x, dict)]
        if isinstance(market_positions, list)
        else []
    )
    active_positions: list[dict[str, Any]] = []
    for mp in raw_positions:
        p = _f(mp.get("position_fp")) or 0.0
        if abs(p) > 0.0:
            active_positions.append(mp)
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
            "candidate_selection_mode": cfg.candidate_selection_mode,
            "candidate_selection_pool_multiplier": cfg.candidate_selection_pool_multiplier,
            "notes": cfg.selection_policy_notes,
        },
        "entry": {},
        "portfolio": {},
    }
    t0 = time.perf_counter()
    db: Db | None = None
    if cfg.db_path is not None:
        db = Db(path=cfg.db_path)
        db.init()

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
        candidate_selection_mode=cfg.candidate_selection_mode,
        candidate_selection_pool_multiplier=max(1, int(cfg.candidate_selection_pool_multiplier)),
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

    t_state0 = time.perf_counter()
    candidates_state = _enrich_market_state_candidates(
        db=db,
        candidates=candidates_raw,
        positions_payload=positions_payload,
    )
    diagnostics["stage_timing_s"]["market_state_enrichment"] = round(time.perf_counter() - t_state0, 3)

    t_deep0 = time.perf_counter()
    candidates = enrich_candidates_with_research(
        candidates=candidates_state,
        top_n_deep_search=max(0, int(cfg.top_n_deep_search)),
        max_workers=max(1, int(cfg.data_fetch_workers)),
        timeout_s=max(2.0, float(cfg.deep_search_timeout_s)),
    )
    # Evidence novelty is known only after deep research returns a bundle/evidence hash.
    if db is not None:
        updated: list[CandidateContext] = []
        for c in candidates:
            thesis = c.thesis_state if isinstance(c.thesis_state, dict) else {}
            bundle = c.evidence_bundle
            novelty = True
            if bundle and thesis:
                prior_hash = str(thesis.get("last_evidence_hash") or "")
                novelty = not (prior_hash and prior_hash == bundle.evidence_hash)
            di = dict(c.deterministic_inputs)
            di["evidence_novelty"] = novelty
            updated.append(
                CandidateContext(
                    market_ticker=c.market_ticker,
                    market_family=c.market_family,
                    market=c.market,
                    event=c.event,
                    orderbook=c.orderbook,
                    horizon_reason=c.horizon_reason,
                    web_research=c.web_research,
                    evidence_quality=c.evidence_quality,
                    freshness_meta=c.freshness_meta,
                    source_reliability=c.source_reliability,
                    deterministic_inputs=di,
                    evidence_bundle=bundle,
                    market_state=c.market_state,
                    thesis_state=thesis,
                    recent_decisions=c.recent_decisions,
                    exposure_context=c.exposure_context,
                )
            )
        candidates = updated
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
        db=db,
    )

    exec_cfg = ExecutionEngineConfig(
        mode=cfg.mode,  # type: ignore[arg-type]
        prefer_batch=True,
        risk=RiskLimits(
            per_market_max_contracts=None,
            per_category_max_exposure_dollars=None,
            per_event_max_loss_dollars=None,
            rolling_matched_contracts_15s=cfg.rolling_matched_contracts_15s,
            allow_scalar_and_combo=True,
            min_market_liquidity_contracts=max(0.0, cfg.min_liquidity_contracts),
            repeat_market_cooldown_seconds=max(60.0, float(cfg.repeat_market_cooldown_minutes * 60)),
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
        "exchange_rejected": sum(1 for r in entry_result.results if r.status == "exchange_rejected"),
        "rejected": sum(
            1 for r in entry_result.results if r.status in {"risk_rejected", "exchange_rejected"}
        ),
        "errors": sum(1 for r in entry_result.results if r.status == "error"),
        "skipped_read_only": sum(1 for r in entry_result.results if r.status == "skipped_read_only"),
        "execution_outcomes": [
            {
                "ticker": r.intent.ticker,
                "client_order_id": r.client_order_id,
                "status": r.status,
                "reasons": list(r.reasons),
                "error": r.error,
                "api_response": r.api_response if isinstance(r.api_response, dict) else {},
            }
            for r in entry_result.results
        ],
        "decision_trace": entry_trace,
        "market_state_enrichment": [
            {
                "ticker": c.market_ticker,
                "market_state": c.market_state,
                "thesis_state": c.thesis_state,
                "evidence_hash": c.evidence_bundle.evidence_hash if c.evidence_bundle else "",
            }
            for c in candidates
        ],
    }

    diagnostics["portfolio"] = _portfolio_diagnostics(positions_payload)
    diagnostics["stage_timing_s"]["total_cycle"] = round(time.perf_counter() - t0, 3)
    return diagnostics


def run_unified_weather_risk_watch(
    client: KalshiClient,
    *,
    cfg: UnifiedWeatherOrchestratorConfig,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    now_ts = time.time()
    positions_payload = fetch_portfolio_positions(client)
    recent_fills = fetch_recent_fills_for_rolling_window(client, now_ts=now_ts)

    market_positions = positions_payload.get("market_positions")
    open_rows = [x for x in market_positions if isinstance(x, dict)] if isinstance(market_positions, list) else []
    open_tickers = sorted(
        {
            str(row.get("ticker") or "").strip()
            for row in open_rows
            if abs(_f(row.get("position_fp")) or 0.0) > 0.0 and str(row.get("ticker") or "").strip()
        }
    )

    alerts: list[dict[str, Any]] = []
    checked_markets = 0
    for ticker in open_tickers:
        body = client.get_market(ticker)
        market = body.get("market") if isinstance(body, dict) else None
        if not isinstance(market, dict):
            alerts.append(
                {
                    "ticker": ticker,
                    "alert_type": "market_metadata_missing",
                    "severity": "warning",
                    "message": "Could not load market metadata for open position.",
                }
            )
            continue
        checked_markets += 1
        if not _is_open_status(market):
            alerts.append(
                {
                    "ticker": ticker,
                    "alert_type": "market_not_open",
                    "severity": "critical",
                    "status": str(market.get("status") or ""),
                    "message": "Open position is in a market that is no longer open.",
                }
            )
        liq = _market_liquidity_contracts(market)
        if liq < max(0.0, float(cfg.min_liquidity_contracts)):
            alerts.append(
                {
                    "ticker": ticker,
                    "alert_type": "insufficient_liquidity",
                    "severity": "critical",
                    "current_liquidity_contracts": liq,
                    "threshold": max(0.0, float(cfg.min_liquidity_contracts)),
                    "message": "Open-position market liquidity dropped below configured floor.",
                }
            )

    rolling_now = matched_contracts_in_window(recent_fills, now_ts=now_ts, window_s=15.0)
    rolling_limit = max(0.0, float(cfg.rolling_matched_contracts_15s))
    if rolling_now > rolling_limit:
        alerts.append(
            {
                "alert_type": "rolling_matched_contract_ceiling",
                "severity": "critical",
                "current_value": rolling_now,
                "threshold": rolling_limit,
                "message": "Recent fill velocity exceeded rolling matched contracts limit.",
            }
        )

    return {
        "checked_markets": checked_markets,
        "open_positions_count": len(open_tickers),
        "portfolio": _portfolio_diagnostics(positions_payload),
        "recent_fills_seen": len(recent_fills),
        "rolling_15s_matched_contracts": rolling_now,
        "rolling_15s_limit": rolling_limit,
        "alerts": alerts,
        "alerts_count": len(alerts),
        "stage_timing_s": {"risk_watch_total": round(time.perf_counter() - t0, 3)},
    }

