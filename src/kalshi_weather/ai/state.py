from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AIState:
    run_id: str
    now_utc_iso: str
    live_snapshots: list[dict[str, Any]] = field(default_factory=list)
    signal_advisory: dict[str, Any] = field(default_factory=dict)
    candidate_markets: list[str] = field(default_factory=list)
    live_analysis: dict[str, Any] = field(default_factory=dict)
    historical_context: dict[str, Any] = field(default_factory=dict)
    ev_analysis: dict[str, Any] = field(default_factory=dict)
    validity: dict[str, Any] = field(default_factory=dict)
    critic: dict[str, Any] = field(default_factory=dict)
    journal: dict[str, Any] = field(default_factory=dict)
    stop_reason: str | None = None

