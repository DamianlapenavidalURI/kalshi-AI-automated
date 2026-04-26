"""Kalshi order execution: demo pipeline, batching engine, dry-run / shadow, fill simulation."""

from kalshi_weather.execution.engine import KalshiExecutionEngine, intent_to_create_body, make_client_order_id
from kalshi_weather.execution.models import (
    BatchExecutionResult,
    ExecutionEngineConfig,
    ExecutionMode,
    ExecutionPolicy,
    FillPreview,
    OrderExecutionResult,
    OrderIntent,
    RiskLimits,
)
from kalshi_weather.execution.service import ExecutionSummary, reconcile_demo_orders, run_demo_execution
from kalshi_weather.execution.snapshot import fetch_portfolio_positions, fetch_recent_fills_for_rolling_window

__all__ = [
    "BatchExecutionResult",
    "ExecutionEngineConfig",
    "ExecutionMode",
    "ExecutionPolicy",
    "ExecutionSummary",
    "FillPreview",
    "KalshiExecutionEngine",
    "OrderExecutionResult",
    "OrderIntent",
    "RiskLimits",
    "fetch_portfolio_positions",
    "fetch_recent_fills_for_rolling_window",
    "intent_to_create_body",
    "make_client_order_id",
    "reconcile_demo_orders",
    "run_demo_execution",
]
