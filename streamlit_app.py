from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import altair as alt
import streamlit as st

from kalshi_weather.config import get_settings
from kalshi_weather.kalshi.auth import KalshiAuth
from kalshi_weather.kalshi.client import KalshiClient, KalshiHttpError


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


def _fmt_mmss(seconds: Any) -> str:
    total = max(0, int(round(_safe_float(seconds))))
    mins, secs = divmod(total, 60)
    return f"{mins:02d}:{secs:02d}"


def _compact_horizon_reason(value: Any) -> Any:
    raw = str(value or "").strip()
    if not raw:
        return value
    if raw.startswith("weather_short_horizon_fallback:close_within_"):
        return raw.replace("weather_short_horizon_fallback:close_within_", "short_horizon_fb:")
    if raw.startswith("weather_short_horizon:close_within_"):
        return raw.replace("weather_short_horizon:close_within_", "short_horizon:")
    return raw


def _drop_explanation(row: dict[str, Any]) -> str:
    reason = str(row.get("reason") or "").strip()
    if reason == "scout_reject":
        scout_reason = str(row.get("scout_reason") or "").strip()
        if scout_reason:
            return f"Scout reject: {scout_reason}"
        return "Scout reject: no reason text returned by model."
    if reason == "hard_rail_blocked":
        hard_rail = row.get("hard_rail_result")
        failures = hard_rail.get("failures") if isinstance(hard_rail, dict) else []
        if isinstance(failures, list):
            names = [
                str(f.get("rail_name") or "").strip()
                for f in failures
                if isinstance(f, dict) and str(f.get("rail_name") or "").strip()
            ]
            if names:
                return "Hard rail blocked: " + ", ".join(names)
        return "Hard rail blocked."
    if reason:
        return reason
    return "No drop reason recorded."


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
        decision = row.get("decision")
        reason = row.get("reason")
        orchestrator_decision = row.get("orchestrator_decision")
        effective_decision = (
            f"{decision}:{reason}" if decision and reason else decision or "unknown"
        )
        orchestrator_effective = (
            f"{orchestrator_decision} (vetoed by critique)"
            if str(reason or "") == "critique_veto" and orchestrator_decision
            else orchestrator_decision
        )
        out.append(
            {
                "ticker": row.get("ticker"),
                "event_ticker": row.get("event_ticker"),
                "event_title": row.get("event_title"),
                "market_title": row.get("market_title"),
                "market_family": row.get("market_family"),
                "market_option_kind": row.get("market_option_kind"),
                "market_option_label": row.get("market_option_label"),
                "event_market_count": row.get("event_market_count"),
                "decision": decision,
                "reason": reason,
                "drop_explanation": _drop_explanation(row),
                "effective_decision": effective_decision,
                "horizon_reason": _compact_horizon_reason(row.get("horizon_reason")),
                "intent_side": row.get("intent_side"),
                "intent_qty": row.get("intent_qty"),
                "orchestrator_decision": orchestrator_decision,
                "orchestrator_effective": orchestrator_effective,
                "orchestrator_confidence": row.get("orchestrator_confidence"),
                "reasoning_summary": row.get("reasoning_summary"),
                "liquidity_contracts": row.get("liquidity_contracts"),
                "scout_keep": row.get("scout_keep"),
                "scout_reason": row.get("scout_reason"),
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


def _query_rows(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


@st.cache_resource(show_spinner=False)
def _build_kalshi_client_for_dashboard(
    *,
    base_url: str,
    api_key_id: str | None,
    private_key_path: str | None,
) -> KalshiClient | None:
    if not api_key_id or not private_key_path:
        return None
    try:
        auth = KalshiAuth.from_pem_file(
            api_key_id=api_key_id,
            private_key_path=Path(private_key_path).expanduser(),
        )
        return KalshiClient(base_url=base_url, auth=auth)
    except Exception:
        return None


def _load_live_account_snapshot(
    *,
    settings: Any,
) -> dict[str, Any]:
    client = _build_kalshi_client_for_dashboard(
        base_url=str(settings.kalshi_base_url),
        api_key_id=settings.kalshi_api_key_id,
        private_key_path=(
            str(settings.kalshi_private_key_path)
            if settings.kalshi_private_key_path is not None
            else None
        ),
    )
    if client is None:
        return {"ok": False, "error": "missing_or_invalid_kalshi_auth"}
    try:
        positions_payload = client.get_positions(limit=500)
        fills_payload = client.get_fills(limit=300)
    except KalshiHttpError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # pragma: no cover - network/API runtime variability
        return {"ok": False, "error": str(e)}

    positions_raw = positions_payload.get("market_positions")
    positions = [x for x in positions_raw if isinstance(x, dict)] if isinstance(positions_raw, list) else []
    open_positions = [p for p in positions if abs(_safe_float(p.get("position_fp"))) > 0.0]
    fills_raw = fills_payload.get("fills")
    fills = [x for x in fills_raw if isinstance(x, dict)] if isinstance(fills_raw, list) else []
    unique_fill_order_ids = {
        str(f.get("order_id") or "").strip()
        for f in fills
        if str(f.get("order_id") or "").strip()
    }
    unique_fill_tickers = {
        str(f.get("ticker") or "").strip()
        for f in fills
        if str(f.get("ticker") or "").strip()
    }
    unique_fill_ticker_side = {
        (
            str(f.get("ticker") or "").strip(),
            str(f.get("side") or "").strip().lower(),
        )
        for f in fills
        if str(f.get("ticker") or "").strip()
    }
    total_abs_exposure = sum(abs(_safe_float(p.get("market_exposure_dollars"))) for p in open_positions)
    return {
        "ok": True,
        "open_positions_count": len(open_positions),
        "total_abs_exposure_dollars": total_abs_exposure,
        "recent_fills_count": len(fills),
        "recent_fills_unique_order_ids": len(unique_fill_order_ids),
        "recent_fills_unique_markets": len(unique_fill_tickers),
        "recent_fills_unique_market_sides": len(unique_fill_ticker_side),
        "open_positions": open_positions,
        "recent_fills": fills,
    }


def _load_trade_activity_rows(*, db_path: Path, limit: int = 150) -> list[dict[str, Any]]:
    sql = """
    SELECT
      d.created_at,
      d.updated_at,
      d.execution_run_id,
      d.proposal_id,
      d.market_ticker,
      d.event_ticker,
      d.side,
      d.dry_run,
      d.order_status,
      d.client_order_id,
      d.kalshi_order_id,
      d.block_reason,
      p.confidence,
      p.reason,
      p.proposed_limit_price_dollars,
      p.proposed_quantity
    FROM execution_orders d
    LEFT JOIN proposals p
      ON p.proposal_id = d.proposal_id
    ORDER BY d.created_at DESC
    LIMIT ?
    """
    return _query_rows(db_path, sql, (int(limit),))


def _load_market_state_rows(*, db_path: Path, limit: int = 500) -> list[dict[str, Any]]:
    sql = """
    SELECT
      ticker,
      event_ticker,
      family,
      last_decision,
      last_decision_time,
      open_position_side,
      open_position_size,
      open_position_entry_price,
      pnl_estimate
    FROM market_state
    ORDER BY last_decision_time DESC
    LIMIT ?
    """
    return _query_rows(db_path, sql, (int(limit),))


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        key = str(row.get("order_status") or "unknown").strip() or "unknown"
        out[key] = out.get(key, 0) + 1
    return out


def _render_event_market_structure(*, output_path: Path) -> None:
    st.subheader("Kalshi Event -> Market Structure")
    unified = _load_unified_agent_output(output_path)
    if unified is None:
        st.info("No unified output found yet to display event-market structure.")
        return

    entry = unified.get("entry") if isinstance(unified.get("entry"), dict) else {}
    entry_trace_raw = entry.get("decision_trace")
    entry_trace = entry_trace_raw if isinstance(entry_trace_raw, list) else []
    rows = _entry_trace_rows(entry_trace)
    if not rows:
        st.info("No entry trace rows available yet.")
        return

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        et = str(row.get("event_ticker") or "").strip()
        if not et:
            et = "unknown_event"
        grouped.setdefault(et, []).append(row)

    event_count = len(grouped)
    market_count = len(rows)
    avg_per_event = (market_count / event_count) if event_count else 0.0
    m = st.columns(3)
    m[0].metric("Events observed", event_count)
    m[1].metric("Market options observed", market_count)
    m[2].metric("Avg options per event", f"{avg_per_event:.2f}")

    option_kind_counts: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("market_option_kind") or "unknown")
        option_kind_counts[kind] = option_kind_counts.get(kind, 0) + 1
    st.markdown("**Option kind distribution**")
    _render_bar_chart_with_horizontal_labels(
        {k: v for k, v in sorted(option_kind_counts.items())},
        x_title="Option kind",
        y_title="Count",
    )

    event_rows: list[dict[str, Any]] = []
    for event_ticker, event_markets in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        first = event_markets[0]
        event_rows.append(
            {
                "event_ticker": event_ticker,
                "event_title": first.get("event_title"),
                "market_options_seen": len(event_markets),
                "declared_event_market_count": first.get("event_market_count"),
                "families": ", ".join(
                    sorted(
                        {
                            str(x.get("market_family") or "unknown")
                            for x in event_markets
                        }
                    )
                ),
            }
        )
    st.markdown("**Event summary**")
    st.dataframe(event_rows, use_container_width=True)

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
    c = st.columns(6)
    c[0].metric("Mode", str(unified.get("mode") or "n/a"))
    c[1].metric("Time since last run", _fmt_mmss(age_s))
    c[2].metric("Candidates seen", _safe_int(entry_d.get("candidates_seen")))
    c[3].metric("Buy intents", _safe_int(entry_d.get("intents_attempted")))
    c[4].metric("Open positions", _safe_int(portfolio_d.get("open_positions_seen")))
    c[5].metric("Total cycle", _fmt_mmss(total_cycle_s) if total_cycle_s else "n/a")

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

    c = st.columns(5)
    c[0].metric("Submitted buys", _safe_int(entry_d.get("submitted")))
    c[1].metric("Entry risk rejected", _safe_int(entry_d.get("risk_rejected")))
    c[2].metric("Exchange rejected", _safe_int(entry_d.get("exchange_rejected")))
    c[3].metric("Entry dropped", sum(_safe_int(v) for v in (entry_d.get("dropped_reasons") or {}).values()) if isinstance(entry_d.get("dropped_reasons"), dict) else 0)
    c[4].metric("Repeat blocks", _safe_int((entry_d.get("dropped_reasons") or {}).get("repeat_bet_blocked")) if isinstance(entry_d.get("dropped_reasons"), dict) else 0)

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


def _render_user_dashboard(*, output_path: Path, db_path: Path, settings: Any) -> None:
    st.subheader("Performance & Bets")
    unified = _load_unified_agent_output(output_path)
    trade_rows = _load_trade_activity_rows(db_path=db_path, limit=200)
    state_rows = _load_market_state_rows(db_path=db_path, limit=500)
    live_account = _load_live_account_snapshot(settings=settings)

    if not trade_rows and unified is None:
        st.info(
            "No trade/performance data found yet. Run a cycle with "
            "`python scripts/run_unified_weather_orchestrator.py --once`"
        )
        return

    live_trade_rows = [r for r in trade_rows if _safe_int(r.get("dry_run")) == 0]
    db_total_bets = len(live_trade_rows)
    db_executed_orders = sum(1 for r in live_trade_rows if str(r.get("order_status") or "") == "executed")
    db_active_bets = sum(1 for r in state_rows if abs(_safe_float(r.get("open_position_size"))) > 0.0)
    api_recent_fills = _safe_int(live_account.get("recent_fills_count")) if live_account.get("ok") else 0
    api_bets_deduped = _safe_int(live_account.get("recent_fills_unique_markets")) if live_account.get("ok") else 0
    api_executed_deduped = _safe_int(live_account.get("recent_fills_unique_markets")) if live_account.get("ok") else 0
    api_active_bets = _safe_int(live_account.get("open_positions_count")) if live_account.get("ok") else 0
    total_bets = max(db_total_bets, api_bets_deduped)
    executed_orders = min(total_bets, max(db_executed_orders, api_executed_deduped))
    active_bets = max(db_active_bets, api_active_bets)
    execution_success_rate = (executed_orders / total_bets) if total_bets > 0 else 0.0
    estimated_money_used = sum(
        _safe_float(r.get("proposed_limit_price_dollars")) * max(0.0, _safe_float(r.get("proposed_quantity")))
        for r in live_trade_rows
    )
    pnl_values = [_safe_float(r.get("pnl_estimate")) for r in state_rows if r.get("pnl_estimate") is not None]
    total_pnl_estimate = sum(pnl_values)
    estimated_roi = (total_pnl_estimate / estimated_money_used) if estimated_money_used > 0 else 0.0

    c = st.columns(5)
    c[0].metric("Total bets placed", total_bets)
    c[1].metric("Executed bets", executed_orders)
    c[2].metric("Active bets", active_bets)
    c[3].metric("Estimated PnL", f"${total_pnl_estimate:.2f}")
    c[4].metric("Estimated ROI", f"{estimated_roi * 100:.1f}%")
    if live_account.get("ok"):
        st.caption(
            "Live account snapshot: "
            f"open_positions={_safe_int(live_account.get('open_positions_count'))}, "
            f"fills={_safe_int(live_account.get('recent_fills_count'))}, "
            f"unique_markets={_safe_int(live_account.get('recent_fills_unique_markets'))}, "
            f"unique_order_ids={_safe_int(live_account.get('recent_fills_unique_order_ids'))}, "
            f"exposure=${_safe_float(live_account.get('total_abs_exposure_dollars')):.2f}"
        )
    else:
        st.caption(
            "Live account snapshot unavailable; showing DB-only metrics. "
            f"Reason: {live_account.get('error') or 'unknown'}"
        )

    if live_trade_rows:
        pnl_by_ticker = {str(r.get("ticker") or ""): _safe_float(r.get("pnl_estimate")) for r in state_rows}
        st.markdown("**Recent bets**")
        display_rows = [
            {
                "created_at": r.get("created_at"),
                "ticker": r.get("market_ticker"),
                "side": r.get("side"),
                "status": r.get("order_status"),
                "stake_estimate": round(
                    _safe_float(r.get("proposed_limit_price_dollars")) * max(0.0, _safe_float(r.get("proposed_quantity"))),
                    4,
                ),
                "pnl_estimate": pnl_by_ticker.get(str(r.get("market_ticker") or ""), 0.0),
                "confidence": r.get("confidence"),
            }
            for r in live_trade_rows
        ]
        st.dataframe(display_rows, use_container_width=True)
    else:
        st.info("No live bets recorded yet.")

    if unified is not None:
        entry = unified.get("entry") if isinstance(unified.get("entry"), dict) else {}
        st.caption(
            "Latest cycle: "
            f"submitted={_safe_int(entry.get('submitted'))}, "
            f"rejected={_safe_int(entry.get('rejected'))}, "
            f"errors={_safe_int(entry.get('errors'))}, "
            f"estimated_money_used=${estimated_money_used:.2f}"
        )


def _render_developer_dashboard(*, output_path: Path) -> None:
    st.subheader("Runtime Diagnostics")
    st.caption("Developer troubleshooting view for orchestration, stage timings, and decision trace.")
    _render_runtime_health(output_path=output_path)
    _render_entry_diagnostics(output_path=output_path)
    _render_event_market_structure(output_path=output_path)


def main() -> None:
    st.set_page_config(page_title="Kalshi Weather Dashboard", layout="wide")
    st.title("Kalshi Weather Dashboard")

    s = get_settings()
    output_path = Path(s.unified_agent_output_json).expanduser()
    db_path = Path(s.db_path).expanduser()
    dashboard_options = ("User", "Developer")
    if "dashboard_view" not in st.session_state:
        st.session_state.dashboard_view = "Developer"
    if st.session_state.dashboard_view not in dashboard_options:
        st.session_state.dashboard_view = "Developer"
    if "auto_refresh_5s" not in st.session_state:
        st.session_state.auto_refresh_5s = False

    top_controls = st.columns(4)
    with top_controls[0]:
        if st.button("Refresh now"):
            st.caption("Refreshed.")
    with top_controls[1]:
        st.session_state.auto_refresh_5s = st.toggle(
            "Auto-refresh 5s",
            value=bool(st.session_state.auto_refresh_5s),
            key="auto_refresh_5s_toggle",
        )
        st.caption("ON" if st.session_state.auto_refresh_5s else "OFF")
    with top_controls[2]:
        st.caption(f"Env: `{s.kalshi_env}`")
    with top_controls[3]:
        st.caption(f"Mode default: `{s.unified_mode}`")

    dashboard_view = st.radio(
        "Dashboard",
        dashboard_options,
        horizontal=True,
        index=dashboard_options.index(st.session_state.dashboard_view),
        key="dashboard_view_selector",
    )
    st.session_state.dashboard_view = dashboard_view

    if st.session_state.dashboard_view == "User":
        st.caption("User dashboard: trade visibility and system performance.")
        _render_user_dashboard(output_path=output_path, db_path=db_path, settings=s)
    else:
        st.caption(f"Unified output: `{output_path}`")
        st.caption(f"Database: `{db_path}`")
        st.caption("Developer dashboard: runtime troubleshooting and deep diagnostics.")
        _render_developer_dashboard(output_path=output_path)

    if bool(st.session_state.auto_refresh_5s):
        time.sleep(5)
        st.rerun()

if __name__ == "__main__":
    main()

