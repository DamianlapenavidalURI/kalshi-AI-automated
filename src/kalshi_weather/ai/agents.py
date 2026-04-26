from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from kalshi_weather.ai.llm import call_llm_json


def _compact_snapshot(s: dict[str, Any]) -> dict[str, Any]:
    mj = s.get("market_json") if isinstance(s, dict) else {}
    if not isinstance(mj, dict):
        mj = {}
    out: dict[str, Any] = {
        "snapshot_id": s.get("id"),
        "observed_at": s.get("observed_at"),
        "ticker": mj.get("ticker") or mj.get("market_ticker"),
        "status": mj.get("status"),
        "yes_bid_dollars": mj.get("yes_bid_dollars"),
        "yes_ask_dollars": mj.get("yes_ask_dollars"),
        "updated_time": mj.get("updated_time"),
        "title": mj.get("title"),
        "event_ticker": mj.get("event_ticker"),
        "series_ticker": mj.get("series_ticker"),
        "close_time": mj.get("close_time"),
        "occurrence_datetime": mj.get("occurrence_datetime"),
    }
    ev = s.get("_signal_evaluation")
    if ev is not None and hasattr(ev, "signal_score"):
        out["deterministic_signal_score"] = getattr(ev, "signal_score", None)
        out["deterministic_quality_bucket"] = getattr(ev, "candidate_quality_bucket", None)
        out["deterministic_skip_reason"] = getattr(ev, "skip_reason", None)
        feat = getattr(ev, "features", None)
        if isinstance(feat, dict):
            out["deterministic_features"] = {
                k: feat[k]
                for k in (
                    "n_snapshots_in_window",
                    "volatility_last_n",
                    "seconds_since_last_update",
                    "snapshot_density",
                    "updates_seen_last_n",
                    "liquidity_present",
                    "status_consistent",
                    "last_yes_spread",
                )
                if k in feat
            }
    return out


def signal_advisory_agent(
    *,
    model: str,
    enriched_snapshots: list[dict[str, Any]],
    max_tickers: int = 20,
) -> dict[str, Any]:
    """
    Advisory-only LLM output: narratives and caution flags. Does not approve execution.
    """
    sys = (
        "You are the Signal Advisory Agent for a Kalshi DEMO short-horizon soccer system. "
        "You MUST NOT approve or reject trades, must NOT instruct execution, and must NOT override risk rules. "
        "Use the provided deterministic_signal fields as facts. Return STRICT JSON with keys: "
        "advisory_by_ticker (object keyed by ticker; each value has: narrative (string), "
        "recent_movement (string), caution_flags (array of strings), mispricing_notes (array of strings)), "
        "overall_note (string). If uncertain, use empty arrays and conservative language."
    )
    compact = [_compact_snapshot(s) for s in enriched_snapshots[: max_tickers * 3]]
    user = (
        "Latest monitored markets with deterministic short-horizon features (local SQLite history only):\n"
        f"{json.dumps(compact, ensure_ascii=False)[:14000]}\n"
    )
    out = call_llm_json(model=model, system=sys, user=user, temperature=0.2)
    adv = out.get("advisory_by_ticker")
    if not isinstance(adv, dict):
        adv = {}
    out["advisory_by_ticker"] = adv
    return out


def live_market_analysis_agent(
    *,
    model: str,
    live_snapshots: list[dict[str, Any]],
    max_candidates: int = 8,
) -> dict[str, Any]:
    sys = (
        "You are the Live Market Analysis Agent for a Kalshi DEMO soccer-only project. "
        "You must not recommend non-soccer, season, futures, or ambiguous markets. "
        "Deterministic signal_score / features are advisory inputs; you still must respect scope. "
        "Return STRICT JSON with keys: summary, candidates (array of {ticker, why}), "
        "and notes. If there are no good candidates, return candidates=[]."
    )
    compact = [_compact_snapshot(s) for s in live_snapshots][:80]
    user = (
        "Here are latest monitored market snapshots (one per market), including deterministic_signal_*:\n"
        f"{json.dumps(compact, ensure_ascii=False)[:12000]}\n\n"
        f"Pick up to {max_candidates} candidate tickers worth deeper review. "
        "Prefer: higher deterministic_signal_score when scope is clearly soccer and short-horizon, "
        "active/initialized, tighter last_yes_spread in features, fresher seconds_since_last_update."
    )
    out = call_llm_json(model=model, system=sys, user=user, temperature=0.2)
    cands = out.get("candidates") or []
    if not isinstance(cands, list):
        cands = []
    out["candidates"] = cands[:max_candidates]
    return out


def historical_context_agent(
    *,
    model: str,
    proposals_recent: list[dict[str, Any]],
    demo_orders_recent: list[dict[str, Any]],
    candidates: list[str],
) -> dict[str, Any]:
    sys = (
        "You are the Historical Context Agent. Use ONLY the provided local history. "
        "Do not invent stats or external facts. Return STRICT JSON with keys: "
        "context_by_ticker (object keyed by ticker), and warnings."
    )
    user = (
        "Candidate tickers:\n"
        f"{json.dumps(candidates)}\n\n"
        "Recent proposals rows (local DB):\n"
        f"{json.dumps(proposals_recent, ensure_ascii=False)[:10000]}\n\n"
        "Recent demo_orders rows (local DB):\n"
        f"{json.dumps(demo_orders_recent, ensure_ascii=False)[:10000]}\n"
    )
    return call_llm_json(model=model, system=sys, user=user, temperature=0.2)


def expected_value_agent(
    *,
    model: str,
    live_snapshots: list[dict[str, Any]],
    context: dict[str, Any],
    candidates: list[str],
) -> dict[str, Any]:
    sys = (
        "You are the Expected Value Agent. You may estimate EV heuristically using ONLY "
        "the current market prices (yes_bid/yes_ask) and provided context. "
        "Do not claim real-world match probabilities unless explicitly in context; instead "
        "use conservative heuristics and uncertainty. Return STRICT JSON with keys: "
        "ev_by_ticker (object keyed by ticker with fields implied_p_mid, heuristic_edge, "
        "ev_comment, confidence_0_1), and overall_note."
    )
    compact = [_compact_snapshot(s) for s in live_snapshots if _compact_snapshot(s).get("ticker") in set(candidates)]
    user = (
        "Candidates:\n"
        f"{json.dumps(candidates)}\n\n"
        "Current snapshots:\n"
        f"{json.dumps(compact, ensure_ascii=False)[:12000]}\n\n"
        "Local context:\n"
        f"{json.dumps(context, ensure_ascii=False)[:12000]}\n"
    )
    return call_llm_json(model=model, system=sys, user=user, temperature=0.2)


def bet_validity_agent(
    *,
    model: str,
    candidates: list[str],
    live_snapshots: list[dict[str, Any]],
    snapshot_max_age_seconds: int,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    by_ticker: dict[str, Any] = {}
    for s in live_snapshots:
        c = _compact_snapshot(s)
        t = c.get("ticker")
        if not t or t not in set(candidates):
            continue
        obs = c.get("observed_at") or ""
        age_ok = False
        try:
            dt = datetime.fromisoformat(str(obs).replace("Z", "+00:00"))
            age_s = max(0.0, (now - dt).total_seconds())
            age_ok = age_s <= float(snapshot_max_age_seconds)
        except ValueError:
            age_s = None
        yb = c.get("yes_bid_dollars")
        ya = c.get("yes_ask_dollars")
        try:
            spread = float(ya) - float(yb) if (yb is not None and ya is not None) else None
        except ValueError:
            spread = None
        by_ticker[t] = {
            "ticker": t,
            "status": c.get("status"),
            "snapshot_age_seconds": age_s,
            "snapshot_age_ok": age_ok,
            "has_yes_book": yb is not None and ya is not None,
            "spread": spread,
            "pass": bool(age_ok and (c.get("status") in ("active", "initialized")) and (yb is not None and ya is not None)),
            "reasons": [],
        }
        if not age_ok:
            by_ticker[t]["reasons"].append("stale_snapshot")
        if c.get("status") not in ("active", "initialized"):
            by_ticker[t]["reasons"].append(f"market_not_open:{c.get('status')}")
        if yb is None or ya is None:
            by_ticker[t]["reasons"].append("missing_yes_book")

    sys = (
        "You are the Bet Validity Agent. Confirm scope constraints (soccer, short-horizon / event-driven) "
        "from ticker patterns and provided snapshot fields; if ambiguous, fail closed. "
        "Return STRICT JSON with keys: validity_by_ticker, overall_pass, overall_note."
    )
    user = (
        "Deterministic pre-checks (already computed):\n"
        f"{json.dumps(by_ticker, ensure_ascii=False)[:12000]}\n\n"
        "Now add any scope-related fails (soccer-only, short-horizon event-driven) based on tickers/series_ticker if present. "
        "If uncertain, set pass=false with a clear reason."
    )
    out = call_llm_json(model=model, system=sys, user=user, temperature=0.1)
    out.setdefault("validity_by_ticker", by_ticker)
    return out


def critic_agent(*, model: str, ev: dict[str, Any], validity: dict[str, Any]) -> dict[str, Any]:
    sys = (
        "You are the Critic Agent. Be skeptical. Look for stale info, thin liquidity, "
        "circular reasoning, or missing evidence. Return STRICT JSON with keys: "
        "objections (array), and objections_by_ticker (object)."
    )
    user = f"EV analysis:\n{json.dumps(ev, ensure_ascii=False)[:12000]}\n\nValidity:\n{json.dumps(validity, ensure_ascii=False)[:12000]}"
    return call_llm_json(model=model, system=sys, user=user, temperature=0.2)


def journal_agent(
    *,
    model: str,
    live_analysis: dict[str, Any],
    context: dict[str, Any],
    ev: dict[str, Any],
    validity: dict[str, Any],
    critic: dict[str, Any],
    signal_advisory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sys = (
        "You are the Journal/Review Agent. Summarize what happened for debugging and presentation. "
        "Return STRICT JSON with keys: summary, decisions, next_steps."
    )
    user = (
        "Inputs:\n"
        f"signal_advisory={json.dumps(signal_advisory or {}, ensure_ascii=False)[:6000]}\n\n"
        f"live_analysis={json.dumps(live_analysis, ensure_ascii=False)[:8000]}\n\n"
        f"historical_context={json.dumps(context, ensure_ascii=False)[:8000]}\n\n"
        f"ev={json.dumps(ev, ensure_ascii=False)[:8000]}\n\n"
        f"validity={json.dumps(validity, ensure_ascii=False)[:8000]}\n\n"
        f"critic={json.dumps(critic, ensure_ascii=False)[:8000]}\n\n"
        "Reminder: deterministic risk/proposal/execution gates are enforced in code; this journal is for debugging."
    )
    return call_llm_json(model=model, system=sys, user=user, temperature=0.2)

