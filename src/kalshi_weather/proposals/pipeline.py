from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from kalshi_weather.db.db import Db
from kalshi_weather.proposals.baseline_engine import maybe_propose_from_snapshot
from kalshi_weather.proposals.models import ProposalDraft
from kalshi_weather.proposals.risk_guard import RiskLimits, evaluate_proposal
from kalshi_weather.proposals.signal_layer import (
    SignalParams,
    compute_signal_evaluation,
    load_recent_snapshots_for_markets,
)

log = logging.getLogger("kalshi_weather.proposals")


@dataclass(frozen=True, slots=True)
class PipelineResult:
    run_id: str
    drafts: list[ProposalDraft]
    approved: list[tuple[ProposalDraft, dict[str, Any]]]
    rejected: list[tuple[ProposalDraft, str, dict[str, Any]]]
    run_summary: dict[str, Any]


def snapshot_age_seconds(observed_at: str, *, now_utc: datetime | None = None) -> float:
    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return 1e9
    return max(0.0, (now_utc - dt).total_seconds())


def baseline_skip_category(code: str) -> str:
    """Group raw baseline skip codes into explainable buckets."""
    if code == "wide_spread":
        return "spread_too_wide"
    if code in ("missing_yes_book", "invalid_book_bounds"):
        return "invalid_order_book"
    if code in ("missing_ticker", "bad_status"):
        return "invalid_market_data"
    if code == "no_eligible_monitor_snapshots":
        return "no_monitor_snapshots"
    if code == "unknown_skip":
        return "unknown_baseline_skip"
    if code in (
        "insufficient_recent_history",
        "stale_market",
        "unstable_price_behavior",
        "insufficient_signal_quality",
    ):
        return "m7_signal_gate"
    return "other"


def aggregate_baseline_skip_categories(skip_counts: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for code, n in skip_counts.items():
        out[baseline_skip_category(code)] += n
    return dict(sorted(out.items(), key=lambda x: (-x[1], x[0])))


def _signal_histogram(scores: list[float]) -> dict[str, int]:
    bins: dict[str, int] = defaultdict(int)
    for sc in scores:
        b = int(max(0.0, min(99.0, sc)) // 10) * 10
        key = f"{b:02d}-{b + 9:02d}"
        bins[key] += 1
    return dict(sorted(bins.items(), key=lambda x: x[0]))


def build_run_summary(
    *,
    markets_scanned: int,
    drafts: int,
    approved: int,
    rejected: int,
    skip_counts: dict[str, int],
    guard_rejection_counts: dict[str, int],
    signal_scores: list[float],
    per_market_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    sorted_skips = sorted(skip_counts.items(), key=lambda x: (-x[1], x[0]))
    top_baseline = sorted_skips[:15]
    by_cat = aggregate_baseline_skip_categories(skip_counts)
    top_guard = sorted(guard_rejection_counts.items(), key=lambda x: (-x[1], x[0]))[:10]

    if markets_scanned == 0:
        headline = (
            "No eligible live monitor snapshots (no latest non-stale row per market in "
            "`live_monitor_snapshots`). Run "
            "`python scripts/run_unified_weather_orchestrator.py --once` "
            "(or keep it running with `--run loop`), then re-run this pipeline."
        )
    elif drafts == 0:
        parts = [f"{k}={v}" for k, v in top_baseline if v > 0]
        cat_parts = [f"{k}={v}" for k, v in by_cat.items() if v > 0]
        headline = (
            f"Milestone 7: 0 drafts from {markets_scanned} monitored market(s). "
            f"Grouped skips: {', '.join(cat_parts) if cat_parts else 'none'}. "
            f"Raw top skips: {', '.join(parts) if parts else 'none'}. "
            "Common causes: insufficient snapshot history for features, stale quotes, wide spread, "
            "unstable mid-price series, or signal_score below MIN_SIGNAL_SCORE."
        )
    else:
        headline = (
            f"Milestone 7 drafts: {drafts} from {markets_scanned} market(s). "
            f"Risk guard: approved={approved}, rejected={rejected}."
        )
    return {
        "milestone": "m7_signal_layer",
        "total_markets_evaluated": markets_scanned,
        "markets_scanned": markets_scanned,
        "drafts_created": drafts,
        "guard_approved": approved,
        "guard_rejected": rejected,
        "baseline_skip_counts": dict(skip_counts),
        "baseline_skip_by_category": by_cat,
        "top_baseline_rejection_reasons": top_baseline,
        "top_skip_reasons": top_baseline,
        "guard_rejection_counts_this_run": dict(guard_rejection_counts),
        "top_guard_rejection_reasons": top_guard,
        "no_draft_summary": headline,
        "signal_score_histogram": _signal_histogram(signal_scores),
        "per_market_evaluations": per_market_trace[:200],
    }


def load_latest_snapshots_per_market(db: Db, *, limit_markets: int = 200) -> list[dict[str, Any]]:
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


def run_proposal_pipeline(
    db: Db,
    *,
    limits: RiskLimits,
    signal_params: SignalParams,
    limit_markets: int = 200,
) -> PipelineResult:
    run_id = str(uuid.uuid4())
    meta = dict(asdict(limits))
    meta["signal_params"] = {
        "lookback_hours": signal_params.lookback_hours,
        "max_snapshots_per_market": signal_params.max_snapshots_per_market,
        "min_snapshots": signal_params.min_snapshots,
        "max_yes_spread": signal_params.max_yes_spread,
        "min_signal_score_to_propose": signal_params.min_signal_score_to_propose,
        "n_window": signal_params.n_window,
    }
    db.insert_proposal_pipeline_run(run_id=run_id, meta=meta)

    snaps = load_latest_snapshots_per_market(db, limit_markets=limit_markets)
    skip_counts: dict[str, int] = defaultdict(int)
    drafts: list[ProposalDraft] = []
    signal_scores: list[float] = []
    per_market_trace: list[dict[str, Any]] = []

    if not snaps:
        skip_counts["no_eligible_monitor_snapshots"] += 1

    tickers: list[str] = []
    for s in snaps:
        mj = s.get("market_json") or {}
        mt = mj.get("ticker") or mj.get("market_ticker")
        if isinstance(mt, str):
            tickers.append(mt)

    now_utc = datetime.now(timezone.utc)
    with db.connect() as conn:
        grouped = load_recent_snapshots_for_markets(
            conn,
            tickers,
            lookback_hours=signal_params.lookback_hours,
            max_per_market=signal_params.max_snapshots_per_market,
        )

    log.info(
        "proposal_pipeline m7 markets=%d tickers_with_history=%d",
        len(snaps),
        len([t for t in tickers if grouped.get(t)]),
    )

    for s in snaps:
        mj = s.get("market_json") or {}
        mt = mj.get("ticker") or mj.get("market_ticker")
        if not isinstance(mt, str):
            skip_counts["missing_ticker"] += 1
            continue

        rows = grouped.get(mt) or []
        ev = compute_signal_evaluation(
            market_ticker=mt,
            latest_snapshot_id=int(s["id"]),
            rows_asc=rows,
            now_utc=now_utc,
            params=signal_params,
        )
        signal_scores.append(ev.signal_score)
        per_market_trace.append(
            {
                "market_ticker": mt,
                "snapshot_id": s["id"],
                "signal_score": ev.signal_score,
                "candidate_quality_bucket": ev.candidate_quality_bucket,
                "skip_reason": ev.skip_reason,
                "n_snapshots": ev.features.get("n_snapshots_in_window"),
            }
        )

        if ev.skip_reason:
            skip_counts[ev.skip_reason] += 1
            log.info(
                "proposal_pipeline SKIP %s signal=%.2f reason=%s",
                mt,
                ev.signal_score,
                ev.skip_reason,
            )
            continue

        age_sec = snapshot_age_seconds(s["observed_at"], now_utc=now_utc)
        d, skip = maybe_propose_from_snapshot(
            snapshot_id=s["id"],
            observed_at=s["observed_at"],
            market_json=mj,
            signal_eval=ev,
            max_yes_spread=limits.max_yes_spread,
            snapshot_age_seconds=age_sec,
            snapshot_max_age_minutes=limits.snapshot_max_age_minutes,
        )
        if d:
            drafts.append(d)
            log.info(
                "proposal_pipeline DRAFT %s signal=%.2f bucket=%s",
                mt,
                ev.signal_score,
                ev.candidate_quality_bucket,
            )
        elif skip:
            skip_counts[skip] += 1
        else:
            skip_counts["unknown_skip"] += 1

    approved: list[tuple[ProposalDraft, dict[str, Any]]] = []
    rejected: list[tuple[ProposalDraft, str, dict[str, Any]]] = []

    with db.connect() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for d in drafts:
            risk = evaluate_proposal(conn, d, limits=limits)
            db.insert_proposal_on_connection(
                conn,
                pipeline_run_id=run_id,
                draft=d,
                guard_outcome=risk.outcome,
                rejection_reason=risk.rejection_reason,
                risk_details=risk.details,
            )
            if risk.outcome == "approved":
                approved.append((d, risk.details))
                log.info(
                    "APPROVED %s %s signal=%.2f",
                    d.market_ticker,
                    d.proposal_id,
                    d.signal_score,
                )
            else:
                rejected.append((d, risk.rejection_reason or "rejected", risk.details))
                log.info(
                    "REJECTED %s %s reason=%s signal=%.2f",
                    d.market_ticker,
                    d.proposal_id,
                    risk.rejection_reason,
                    d.signal_score,
                )
        conn.commit()

    guard_rejection_counts: dict[str, int] = defaultdict(int)
    for _, reason, _ in rejected:
        guard_rejection_counts[reason or "unknown"] += 1

    summary = build_run_summary(
        markets_scanned=len(snaps),
        drafts=len(drafts),
        approved=len(approved),
        rejected=len(rejected),
        skip_counts=dict(skip_counts),
        guard_rejection_counts=dict(guard_rejection_counts),
        signal_scores=signal_scores,
        per_market_trace=per_market_trace,
    )
    db.update_proposal_pipeline_run_summary(run_id=run_id, summary=summary)

    log.info(
        "proposal_pipeline run_id=%s markets_evaluated=%d drafts_created=%d guard_approved=%d guard_rejected=%d",
        run_id,
        summary["total_markets_evaluated"],
        summary["drafts_created"],
        summary["guard_approved"],
        summary["guard_rejected"],
    )
    log.info(
        "proposal_pipeline m7_signal_histogram=%s",
        summary.get("signal_score_histogram"),
    )
    log.info(
        "proposal_pipeline baseline_top_rejection_reasons=%s",
        summary.get("top_baseline_rejection_reasons"),
    )
    if summary["guard_rejection_counts_this_run"]:
        log.info(
            "proposal_pipeline guard_rejection_reasons_this_run=%s",
            summary["guard_rejection_counts_this_run"],
        )

    return PipelineResult(
        run_id=run_id,
        drafts=drafts,
        approved=approved,
        rejected=rejected,
        run_summary=summary,
    )
