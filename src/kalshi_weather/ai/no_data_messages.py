from __future__ import annotations

from typing import Any


def journal_for_no_live_monitor_snapshots() -> dict[str, Any]:
    """
    Deterministic, project-specific summary when live_monitor_snapshots has no eligible rows.
    Used instead of an LLM journal so output is never vague.
    """
    return {
        "reason": (
            "No short-horizon weather snapshots were available in the monitoring table "
            "(`live_monitor_snapshots`, latest non-stale row per market)."
        ),
        "interpretation": (
            "The AI workflow did not fail; it stopped because there was no current Kalshi snapshot "
            "data in the configured short-horizon weather window to analyze for this demo project."
        ),
        "likely_causes": [
            "No qualifying weather markets in Kalshi demo right now",
            "Horizon or scope filters excluded all candidates (watchlist empty)",
            "The unified orchestrator has not written fresh snapshots yet",
        ],
        "next_steps": [
            "Run `python scripts/run_unified_weather_orchestrator.py --once`",
            "or keep it running with `--run loop`.",
            "Then rerun the analysis path that depends on live monitor snapshots.",
        ],
    }
