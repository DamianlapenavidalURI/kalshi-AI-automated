from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ExecutionMode = Literal["live", "dry_run"]

ExecutionPolicy = Literal["post_only_gtc", "taker_ioc"]


@dataclass(slots=True)
class OrderIntent:
    """Single exchange order intent (maps to CreateOrderRequest)."""

    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    count_fp: str
    policy: ExecutionPolicy
    # Limit price on the traded side (yes_price_dollars if side=yes else no_price_dollars)
    limit_price_dollars: str
    client_order_id: str | None = None
    order_group_id: str | None = None


@dataclass(slots=True)
class FillPreview:
    """Simulated aggressive execution against the current book (taker IOC)."""

    vwap_dollars: float | None
    filled_contracts: float
    worst_price_dollars: float | None
    fully_filled: bool


@dataclass(slots=True)
class RiskLimits:
    per_market_max_contracts: float | None = None
    per_category_max_exposure_dollars: float | None = None
    per_event_max_loss_dollars: float | None = None
    rolling_matched_contracts_15s: float | None = None
    allow_scalar_and_combo: bool = False
    min_market_liquidity_contracts: float | None = None
    repeat_market_cooldown_seconds: float | None = None


@dataclass(slots=True)
class ExecutionEngineConfig:
    mode: ExecutionMode = "dry_run"
    prefer_batch: bool = True
    risk: RiskLimits = field(default_factory=RiskLimits)
    client_order_id_prefix: str = "kexec"
    use_order_groups_for_rolling: bool = True


@dataclass(slots=True)
class OrderExecutionResult:
    intent: OrderIntent
    client_order_id: str
    status: Literal[
        "pending",
        "submitted",
        "risk_rejected",
        "exchange_rejected",
        "skipped_read_only",
        "error",
    ]
    reasons: list[str] = field(default_factory=list)
    rejection_details: list[dict[str, Any]] = field(default_factory=list)
    api_response: dict[str, Any] | None = None
    fill_preview: FillPreview | None = None
    dry_run_body: dict[str, Any] | None = None
    error: str | None = None


@dataclass(slots=True)
class BatchExecutionResult:
    results: list[OrderExecutionResult]
    used_batch_endpoint: bool
    batch_fallback: str | None
    order_group_id: str | None
    mode: ExecutionMode
