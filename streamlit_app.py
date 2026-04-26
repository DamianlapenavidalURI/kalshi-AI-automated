from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import altair as alt
import streamlit as st

from kalshi_weather.config import get_settings


def _safe_int(x: Any) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(x: Any) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _load_unified_agent_output(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return doc if isinstance(doc, dict) else None


def _entry_trace_rows(trace: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in trace:
        if not isinstance(row, dict):
            continue
        rs = row.get("research_summary")
        rsd = rs if isinstance(rs, dict) else {}
        out.append(
            {
                "ticker": row.get("ticker"),
                "market_family": row.get("market_family"),
                "decision": row.get("decision"),
                "reason": row.get("reason"),
                "horizon_reason": row.get("horizon_reason"),
                "intent_side": row.get("intent_side"),
                "intent_qty": row.get("intent_qty"),
                "orchestrator_decision": row.get("orchestrator_decision"),
                "orchestrator_confidence": row.get("orchestrator_confidence"),
                "reasoning_summary": row.get("reasoning_summary"),
                "liquidity_contracts": row.get("liquidity_contracts"),
                "scout_keep": row.get("scout_keep"),
                "repeat_blocked": (
                    (row.get("repeat_guard") or {}).get("repeat_blocked")
                    if isinstance(row.get("repeat_guard"), dict)
                    else None
                ),
                "research_sources": rsd.get("sources_attempted"),
                "research_ok": rsd.get("sources_ok"),
                "historical_rows": rsd.get("historical_rows"),
            }
        )
    return out


def _reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _render_bar_chart_with_horizontal_labels(
    counts: dict[str, float], *, x_title: str = "Category", y_title: str = "Count"
) -> None:
    if not counts:
        return
    rows = [{"label": key, "value": value} for key, value in counts.items()]
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar()
        .encode(
            x=alt.X(
                "label:N",
                sort="-y",
                axis=alt.Axis(title=x_title, labelAngle=0, titleAngle=0, labelLimit=0),
            ),
            y=alt.Y("value:Q", axis=alt.Axis(title=y_title)),
            tooltip=[
                alt.Tooltip("label:N", title=x_title),
                alt.Tooltip("value:Q", title=y_title),
            ],
        )
        .properties(height=320)
    )
    st.altair_chart(chart, use_container_width=True)


def _render_entry_drop_section(*, output_path: Path) -> None:
    st.subheader("Entry Drops")
    unified = _load_unified_agent_output(output_path)
    if unified is None:
        st.info("No unified output found yet to analyze entry drops.")
        return

    entry = unified.get("entry") if isinstance(unified.get("entry"), dict) else {}
    entry_trace_raw = entry.get("decision_trace")
    entry_trace = entry_trace_raw if isinstance(entry_trace_raw, list) else []

    entry_dropped: list[dict[str, Any]] = []
    for row in entry_trace:
        if not isinstance(row, dict):
            continue
        decision = str(row.get("decision") or "")
        if decision == "intent":
            continue
        entry_dropped.append(
            {
                "ticker": row.get("ticker"),
                "decision": decision or "unknown",
                "reason": row.get("reason"),
                "horizon_reason": row.get("horizon_reason"),
                "scout_keep": row.get("scout_keep"),
                "scout_priority": row.get("scout_priority"),
                "scout_reason": row.get("scout_reason"),
                "spread_yes": row.get("spread_yes"),
                "fusion_proceed": row.get("fusion_proceed"),
                "fusion_trust": row.get("fusion_trust"),
                "edge_side": row.get("edge_side"),
                "edge_confidence": row.get("edge_confidence"),
            }
        )

    c = st.columns(2)
    c[0].metric("Entry dropped", len(entry_dropped))
    c[1].metric("Entry risk rejected", _safe_int(entry.get("risk_rejected")))
    st.markdown("**Entry candidates dropped before order intent creation**")
    st.json(_reason_counts(entry_dropped))
    if entry_dropped:
        st.dataframe(entry_dropped, use_container_width=True)
    else:
        st.success("No entry drops in the latest unified cycle.")

def _render_runtime_health(*, output_path: Path) -> None:
    st.subheader("Runtime Health")
    unified = _load_unified_agent_output(output_path)
    if unified is None:
        st.info(
            "No unified agent output file found yet. Run: "
            "`python scripts/run_unified_weather_orchestrator.py --once`"
        )
        return

    entry = unified.get("entry")
    entry_d = entry if isinstance(entry, dict) else {}
    portfolio = unified.get("portfolio")
    portfolio_d = portfolio if isinstance(portfolio, dict) else {}
    timing = unified.get("stage_timing_s")
    timing_d = timing if isinstance(timing, dict) else {}
    total_cycle_s = _safe_float(timing_d.get("total_cycle"))
    stage_timing_d = {k: _safe_float(v) for k, v in timing_d.items() if k != "total_cycle"}
    deep_research_s = _safe_float(stage_timing_d.get("entry_deep_research"))

    modified_at = datetime.fromtimestamp(output_path.stat().st_mtime, tz=timezone.utc)
    age_s = max(0.0, (datetime.now(timezone.utc) - modified_at).total_seconds())
    c = st.columns(7)
    c[0].metric("Mode", str(unified.get("mode") or "n/a"))
    c[1].metric("Cycle age (s)", f"{age_s:.0f}")
    c[2].metric("Candidates seen", _safe_int(entry_d.get("candidates_seen")))
    c[3].metric("Buy intents", _safe_int(entry_d.get("intents_attempted")))
    c[4].metric("Open positions", _safe_int(portfolio_d.get("open_positions_seen")))
    c[5].metric("Deep research (s)", f"{deep_research_s:.3f}" if deep_research_s else "n/a")
    c[6].metric("Total cycle (s)", f"{total_cycle_s:.3f}" if total_cycle_s else "n/a")

    if stage_timing_d:
        st.caption("Latest stage timings (seconds)")
        _render_bar_chart_with_horizontal_labels(
            {k: v for k, v in sorted(stage_timing_d.items())},
            x_title="Stage",
            y_title="Seconds",
        )


def _render_entry_diagnostics(*, output_path: Path) -> None:
    st.subheader("Entry Diagnostics")
    unified = _load_unified_agent_output(output_path)
    if unified is None:
        return

    entry = unified.get("entry")
    entry_d = entry if isinstance(entry, dict) else {}
    research = entry_d.get("research")
    research_d = research if isinstance(research, dict) else {}
    entry_trace = entry_d.get("decision_trace")
    entry_rows = _entry_trace_rows(entry_trace if isinstance(entry_trace, list) else [])

    st.caption(f"Source: `{output_path}`")
    st.markdown(
        f"- Candidate scope: `horizon_days={unified.get('horizon_days')}`  \n"
        f"- Autonomy profile: `{entry_d.get('autonomy_profile', 'n/a')}`  \n"
        f"- Top-N deep search: `{entry_d.get('top_n_deep_search', 'n/a')}`"
    )

    c = st.columns(4)
    c[0].metric("Submitted buys", _safe_int(entry_d.get("submitted")))
    c[1].metric("Entry risk rejected", _safe_int(entry_d.get("risk_rejected")))
    c[2].metric("Entry dropped", sum(_safe_int(v) for v in (entry_d.get("dropped_reasons") or {}).values()) if isinstance(entry_d.get("dropped_reasons"), dict) else 0)
    c[3].metric("Repeat blocks", _safe_int((entry_d.get("dropped_reasons") or {}).get("repeat_bet_blocked")) if isinstance(entry_d.get("dropped_reasons"), dict) else 0)

    st.markdown("**Entry dropped reasons**")
    st.json(entry_d.get("dropped_reasons", {}))

    sources = research_d.get("sources_used")
    if isinstance(sources, list) and sources:
        st.caption("Research sources used: " + ", ".join(str(x) for x in sources))

    if entry_rows:
        by_family: dict[str, int] = {}
        for row in entry_rows:
            fam = str(row.get("market_family") or "unknown")
            by_family[fam] = by_family.get(fam, 0) + 1
        st.markdown("**Entry trace by market family**")
        _render_bar_chart_with_horizontal_labels(
            {k: v for k, v in sorted(by_family.items())},
            x_title="Market family",
            y_title="Count",
        )

    if entry_rows:
        st.markdown("**Entry decision trace**")
        st.dataframe(entry_rows, use_container_width=True)
    else:
        st.info("No entry decision trace in latest unified output.")

    with st.expander("Raw unified JSON (debug)", expanded=False):
        st.json(unified)


def main() -> None:
    st.set_page_config(page_title="Kalshi Weather Unified Dashboard", layout="wide")
    st.title("Kalshi Weather Unified Dashboard")

    s = get_settings()
    output_path = Path(s.unified_agent_output_json).expanduser()

    top_controls = st.columns(4)
    with top_controls[0]:
        auto_refresh = st.checkbox("Auto-refresh (10s)", value=False)
    with top_controls[1]:
        if st.button("Refresh now"):
            st.rerun()
    with top_controls[2]:
        st.caption(f"Env: `{s.kalshi_env}`")
    with top_controls[3]:
        st.caption(f"Mode default: `{s.unified_mode}`")

    st.caption(f"Unified output: `{output_path}`")
    st.caption("Performance and behavior view for the entry-first weather runtime.")

    _render_runtime_health(output_path=output_path)
    _render_entry_diagnostics(output_path=output_path)
    _render_entry_drop_section(output_path=output_path)

    if auto_refresh:
        time.sleep(10)
        st.rerun()


if __name__ == "__main__":
    main()

