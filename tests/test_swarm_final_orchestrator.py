from __future__ import annotations

from kalshi_weather.system.contracts import CandidateContext, EntryEdgeOutput, EntryScoutOutput
from kalshi_weather.system.swarm import entry_final_orchestrator_agent


def test_entry_final_orchestrator_agent_parses_schema(monkeypatch) -> None:
    def _fake_call_llm_json(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        return {
            "decision": "REDUCE_SIZE",
            "confidence_score": 0.81,
            "recommended_side": "yes",
            "recommended_size": 3.5,
            "reasoning_summary": "edge present but repeat risk elevated",
            "key_risks": ["repeat risk"],
            "repeat_bet_assessment": "partially overlapping thesis",
            "exposure_assessment": "moderate",
            "required_follow_up_checks": ["recheck in 10 minutes"],
            "structured_rejection_reasons": [],
        }

    monkeypatch.setattr("kalshi_weather.system.swarm.call_llm_json", _fake_call_llm_json)
    c = CandidateContext(
        market_ticker="KXTEST",
        market_family="daily_temperature",
        market={"title": "Will NYC high exceed 70F?", "status": "active"},
        event={"title": "NYC temperatures"},
        orderbook={"yes": [], "no": []},
        horizon_reason="short_horizon",
        deterministic_inputs={"prequal_score": 77},
    )
    out = entry_final_orchestrator_agent(
        model="gpt-4o-mini",
        c=c,
        scout=EntryScoutOutput(keep=True, priority_0_100=80.0, reason="good setup"),
        context={"context_quality_0_100": 70},
        edge=EntryEdgeOutput(edge_yes_prob=0.03, confidence_0_1=0.7, side="yes", notes=[]),
        critique={"veto": False, "risks": ["repeat risk"]},
        portfolio_context={"open_positions_seen": 1},
        repeat_guard={"repeat_blocked": False},
        exposure_context={"total_abs_exposure_dollars": 20},
    )

    assert out.decision == "REDUCE_SIZE"
    assert out.confidence_score_0_1 == 0.81
    assert out.recommended_size == 3.5
    assert out.key_risks == ["repeat risk"]


def test_entry_final_orchestrator_skips_same_thesis_without_novelty(monkeypatch) -> None:
    def _should_not_call_llm(**kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("LLM call should be bypassed when repeat thesis has no novelty")

    monkeypatch.setattr("kalshi_weather.system.swarm.call_llm_json", _should_not_call_llm)
    c = CandidateContext(
        market_ticker="KXTEST",
        market_family="daily_temperature",
        market={"title": "Will NYC high exceed 70F?", "status": "active"},
        event={"title": "NYC temperatures"},
        orderbook={"yes": [], "no": []},
        horizon_reason="short_horizon",
        deterministic_inputs={"prequal_score": 77, "evidence_novelty": False},
    )
    out = entry_final_orchestrator_agent(
        model="gpt-4o-mini",
        c=c,
        scout=EntryScoutOutput(keep=True, priority_0_100=80.0, reason="good setup"),
        context={"context_quality_0_100": 70},
        edge=EntryEdgeOutput(edge_yes_prob=0.03, confidence_0_1=0.7, side="yes", notes=[]),
        critique={"veto": False, "risks": ["repeat risk"]},
        portfolio_context={"open_positions_seen": 1},
        repeat_guard={"repeat_blocked": True, "reasons": ["same_thesis_recent"]},
        exposure_context={"total_abs_exposure_dollars": 20},
    )
    assert out.decision == "SKIP"
    assert out.repeat_flag is True
    assert "same_thesis_no_new_information" in out.rejection_reasons
