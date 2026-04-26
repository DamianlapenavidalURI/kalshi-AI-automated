from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, END

from kalshi_weather.ai.state import AIState


def build_ai_graph(nodes: dict[str, Any]) -> Any:
    """
    Build the LangGraph. `nodes` is a dict of callables keyed by node name.
    Each node takes and returns an AIState (or dict updates compatible with AIState).
    """
    g: StateGraph = StateGraph(AIState)

    g.add_node("load_data", nodes["load_data"])
    g.add_node("signal_advisory", nodes["signal_advisory"])
    g.add_node("live_analysis", nodes["live_analysis"])
    g.add_node("historical_context", nodes["historical_context"])
    g.add_node("ev", nodes["ev"])
    g.add_node("validity", nodes["validity"])
    g.add_node("critic", nodes["critic"])
    g.add_node("journal", nodes["journal"])

    g.set_entry_point("load_data")
    g.add_edge("load_data", "signal_advisory")
    g.add_edge("signal_advisory", "live_analysis")
    g.add_edge("live_analysis", "historical_context")
    g.add_edge("historical_context", "ev")
    g.add_edge("ev", "validity")
    g.add_edge("validity", "critic")
    g.add_edge("critic", "journal")
    g.add_edge("journal", END)

    return g.compile()

