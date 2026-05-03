from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from kalshi_weather.ai.llm import call_llm_json
from kalshi_weather.system.contracts import (
    CandidateContext,
    EntryEdgeOutput,
    EntryFinalDecision,
    EntryScoutOutput,
)


def _f(x: Any) -> float:
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return 0.0


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


def _family_brief(family: str) -> str:
    briefs = {
        "hourly_temperature": "hourly nowcast and short-window forecast shifts",
        "daily_temperature": "daily high/low confidence and station relevance",
        "snow_and_rain": "precipitation threshold probabilities and alert overlays",
        "hurricanes": "track/advisory intensity and landfall timing uncertainty",
        "natural_disasters": "hazard event validation, recency, and severity signal quality",
        "climate_change": "policy/news regime shifts and climate trend relevance",
    }
    return briefs.get(family, "weather market context and settlement clarity")


def _clamp01(x: Any) -> float:
    return max(0.0, min(1.0, _f(x)))


def entry_scout_agent(*, model: str, c: CandidateContext) -> EntryScoutOutput:
    sys = (
        "You are ScoutAgent for weather betting. Decide quick triage only. "
        f"Market-family focus: {c.market_family} ({_family_brief(c.market_family)}). "
        "Return strict JSON with keep(bool), priority_0_100(number), reason(string)."
    )
    user = json.dumps(
        {
            "ticker": c.market_ticker,
            "title": c.market.get("title"),
            "status": c.market.get("status"),
            "yes_bid_dollars": c.market.get("yes_bid_dollars"),
            "yes_ask_dollars": c.market.get("yes_ask_dollars"),
            "horizon_reason": c.horizon_reason,
            "market_family": c.market_family,
            "deterministic_inputs": c.deterministic_inputs,
        },
        ensure_ascii=False,
    )
    out = call_llm_json(
        model=model,
        system=sys,
        user=user,
        temperature=0.1,
        trace_label=f"[ENTRY][SCOUT][{c.market_ticker}]",
    )
    return EntryScoutOutput(
        keep=bool(out.get("keep")),
        priority_0_100=_f(out.get("priority_0_100")),
        reason=str(out.get("reason") or ""),
    )


def entry_context_agent(*, model: str, c: CandidateContext) -> dict[str, Any]:
    sys = (
        "You are ContextAgent. Evaluate event relevance and settlement clarity. "
        f"Market-family focus: {c.market_family} ({_family_brief(c.market_family)}). "
        "Incorporate provided web_research context (teams/players/news snippets) conservatively. "
        "Return strict JSON with context_quality_0_100, concerns(array), key_points(array)."
    )
    user = json.dumps(
        {
            "ticker": c.market_ticker,
            "event": c.event,
            "market_rules": {
                "rules_primary": c.market.get("rules_primary"),
                "rules_secondary": c.market.get("rules_secondary"),
            },
            "market_family": c.market_family,
            "web_research": c.web_research,
            "evidence_bundle": _to_json_obj(c.evidence_bundle),
            "evidence_quality": c.evidence_quality,
            "freshness_meta": c.freshness_meta,
        },
        ensure_ascii=False,
    )[:12000]
    return call_llm_json(
        model=model,
        system=sys,
        user=user,
        temperature=0.1,
        trace_label=f"[ENTRY][CONTEXT][{c.market_ticker}]",
    )


def entry_edge_agent(*, model: str, c: CandidateContext) -> EntryEdgeOutput:
    sys = (
        "You are EdgeAgent. Estimate directional edge from prices/depth plus provided web_research context. "
        f"Market-family focus: {c.market_family} ({_family_brief(c.market_family)}). "
        "When available, use normalized evidence_bundle and openweather payload as primary weather signal for "
        "daily_temperature, hourly_temperature, and snow_and_rain families. "
        "Return strict JSON with edge_yes_prob(number in -0.2..0.2), confidence_0_1, side(yes|no), notes(array)."
    )
    user = json.dumps(
        {
            "ticker": c.market_ticker,
            "prices": {
                "yes_bid_dollars": c.market.get("yes_bid_dollars"),
                "yes_ask_dollars": c.market.get("yes_ask_dollars"),
                "no_bid_dollars": c.market.get("no_bid_dollars"),
                "no_ask_dollars": c.market.get("no_ask_dollars"),
            },
            "sizes": {
                "yes_bid_size_fp": c.market.get("yes_bid_size_fp"),
                "yes_ask_size_fp": c.market.get("yes_ask_size_fp"),
            },
            "orderbook": c.orderbook,
            "market_family": c.market_family,
            "evidence_bundle": _to_json_obj(c.evidence_bundle),
            "web_research": c.web_research,
            "evidence_quality": c.evidence_quality,
            "deterministic_inputs": c.deterministic_inputs,
        },
        ensure_ascii=False,
    )[:12000]
    out = call_llm_json(
        model=model,
        system=sys,
        user=user,
        temperature=0.1,
        trace_label=f"[ENTRY][EDGE][{c.market_ticker}]",
    )
    notes_raw = out.get("notes")
    notes = [str(x) for x in notes_raw] if isinstance(notes_raw, list) else []
    side = str(out.get("side") or "yes").lower()
    return EntryEdgeOutput(
        edge_yes_prob=_f(out.get("edge_yes_prob")),
        confidence_0_1=max(0.0, min(1.0, _f(out.get("confidence_0_1")))),
        side="no" if side == "no" else "yes",
        notes=notes,
    )


def entry_critique_agent(*, model: str, c: CandidateContext, edge: EntryEdgeOutput) -> dict[str, Any]:
    sys = (
        "You are CritiqueAgent. Be skeptical and list why this bet could be wrong. "
        f"Market-family focus: {c.market_family} ({_family_brief(c.market_family)}). "
        "Return strict JSON with veto(bool), risks(array), confidence_penalty_0_1."
    )
    user = json.dumps(
        {
            "ticker": c.market_ticker,
            "edge": {
                "edge_yes_prob": edge.edge_yes_prob,
                "confidence_0_1": edge.confidence_0_1,
                "side": edge.side,
            },
            "market": {
                "status": c.market.get("status"),
                "yes_bid_dollars": c.market.get("yes_bid_dollars"),
                "yes_ask_dollars": c.market.get("yes_ask_dollars"),
                "close_time": c.market.get("close_time"),
            },
            "market_family": c.market_family,
            "web_research": c.web_research,
            "freshness_meta": c.freshness_meta,
        },
        ensure_ascii=False,
    )
    return call_llm_json(
        model=model,
        system=sys,
        user=user,
        temperature=0.1,
        trace_label=f"[ENTRY][CRITIQUE][{c.market_ticker}]",
    )


def entry_final_orchestrator_agent(
    *,
    model: str,
    c: CandidateContext,
    scout: EntryScoutOutput,
    context: dict[str, Any],
    edge: EntryEdgeOutput,
    critique: dict[str, Any],
    portfolio_context: dict[str, Any],
    repeat_guard: dict[str, Any],
    exposure_context: dict[str, Any],
    evidence_bundle: dict[str, Any] | None = None,
    market_state: dict[str, Any] | None = None,
    thesis_state: dict[str, Any] | None = None,
    recent_decisions: list[dict[str, Any]] | None = None,
    freshness_meta: dict[str, Any] | None = None,
    temperature: float = 0.35,
) -> EntryFinalDecision:
    evidence_bundle = evidence_bundle or {}
    market_state = market_state or {}
    thesis_state = thesis_state or {}
    recent_decisions = recent_decisions or []
    freshness_meta = freshness_meta or {}
    if bool(repeat_guard.get("repeat_blocked")) and (
        "same_thesis_recent" in {str(x) for x in (repeat_guard.get("reasons") or [])}
        or not bool(c.deterministic_inputs.get("evidence_novelty", True))
    ):
        return EntryFinalDecision(
            decision="SKIP",
            confidence_score_0_1=0.9,
            recommended_side=edge.side,
            recommended_size=0.0,
            reasoning_summary="Same thesis was evaluated recently without meaningful new evidence.",
            key_risks=["repeat_thesis", "low_novelty"],
            repeat_bet_assessment="repeat_blocked",
            exposure_assessment="unchanged",
            required_follow_up_checks=["wait_for_new_forecast_update"],
            structured_rejection_reasons=["same_thesis_no_new_information"],
            repeat_flag=True,
            exposure_flag=False,
            novelty_assessment="no_new_information",
            rejection_reasons=["same_thesis_no_new_information"],
        )

    sys = (
        "You are FinalOrchestratorAgent for weather market entries. "
        "You decide final action using structured specialist outputs and constraints. "
        "Allowed decisions: ENTER, SKIP, WAIT, REDUCE_SIZE. "
        "You must be especially careful about repeated betting, novelty decay, and concentration risk. "
        "If same thesis has no new information, choose SKIP. If repeated too recently choose SKIP or WAIT. "
        "If confidence is borderline choose WAIT. If edge is positive but exposure is high choose REDUCE_SIZE or SKIP. "
        "Do not ignore deterministic hard-rail signals supplied in repeat_guard and exposure_context. "
        "Return strict JSON with keys: decision, side, size, confidence, reasoning, repeat_flag, exposure_flag, "
        "novelty_assessment, risks(array), required_follow_up_checks(array), rejection_reasons(array)."
    )
    user = json.dumps(
        {
            "ticker": c.market_ticker,
            "market_family": c.market_family,
            "family_focus": _family_brief(c.market_family),
            "market": {
                "status": c.market.get("status"),
                "title": c.market.get("title"),
                "yes_bid_dollars": c.market.get("yes_bid_dollars"),
                "yes_ask_dollars": c.market.get("yes_ask_dollars"),
                "no_bid_dollars": c.market.get("no_bid_dollars"),
                "no_ask_dollars": c.market.get("no_ask_dollars"),
                "close_time": c.market.get("close_time"),
            },
            "orderbook": c.orderbook,
            "research": c.web_research,
            "evidence_quality": c.evidence_quality,
            "freshness_meta": freshness_meta,
            "deterministic_inputs": c.deterministic_inputs,
            "evidence_bundle": evidence_bundle,
            "specialists": {
                "scout": _to_json_obj(scout),
                "context": context,
                "edge": _to_json_obj(edge),
                "critique": critique,
            },
            "portfolio_context": portfolio_context,
            "repeat_guard": repeat_guard,
            "exposure_context": exposure_context,
            "market_state": market_state,
            "thesis_state": thesis_state,
            "recent_decisions": recent_decisions,
        },
        ensure_ascii=False,
    )[:16000]
    out = call_llm_json(
        model=model,
        system=sys,
        user=user,
        temperature=temperature,
        trace_label=f"[ENTRY][FINAL_ORCH][{c.market_ticker}]",
    )
    decision = str(out.get("decision") or "SKIP").upper()
    if decision not in {"ENTER", "SKIP", "WAIT", "REDUCE_SIZE"}:
        decision = "SKIP"
    side = str(out.get("side") or out.get("recommended_side") or edge.side).lower()
    risks_raw = out.get("risks") if isinstance(out.get("risks"), list) else out.get("key_risks")
    checks_raw = out.get("required_follow_up_checks")
    reject_raw = out.get("rejection_reasons")
    if not isinstance(reject_raw, list):
        reject_raw = out.get("structured_rejection_reasons")
    confidence = out.get("confidence")
    if confidence is None:
        confidence = out.get("confidence_score")
    size = out.get("size")
    if size is None:
        size = out.get("recommended_size")
    reasoning = out.get("reasoning")
    if reasoning is None:
        reasoning = out.get("reasoning_summary")
    return EntryFinalDecision(
        decision=decision,  # type: ignore[arg-type]
        confidence_score_0_1=_clamp01(confidence),
        recommended_side="no" if side == "no" else "yes",
        recommended_size=max(0.0, _f(size)),
        reasoning_summary=str(reasoning or ""),
        key_risks=[str(x) for x in risks_raw] if isinstance(risks_raw, list) else [],
        repeat_bet_assessment=str(out.get("repeat_bet_assessment") or ""),
        exposure_assessment=str(out.get("exposure_assessment") or ""),
        required_follow_up_checks=[str(x) for x in checks_raw] if isinstance(checks_raw, list) else [],
        structured_rejection_reasons=[str(x) for x in reject_raw] if isinstance(reject_raw, list) else [],
        repeat_flag=bool(out.get("repeat_flag")),
        exposure_flag=bool(out.get("exposure_flag")),
        novelty_assessment=str(out.get("novelty_assessment") or ""),
        rejection_reasons=[str(x) for x in reject_raw] if isinstance(reject_raw, list) else [],
    )


