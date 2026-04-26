from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv
import os

from kalshi_weather.kalshi.env import KalshiEnv, kalshi_rest_base_url, kalshi_ws_base_url


@dataclass(frozen=True, slots=True)
class Settings:
    kalshi_env: KalshiEnv
    kalshi_api_key_id: str | None
    kalshi_private_key_path: Path | None
    db_path: Path
    openai_model: str
    # Milestone 7: deterministic signals (aligned with risk guard spread cap)
    risk_max_yes_spread: float
    signal_lookback_hours: float
    signal_max_snapshots_per_market: int
    signal_min_snapshots: int
    signal_stale_seconds: float
    signal_max_mid_volatility: float
    signal_max_single_tick_jump: float
    min_signal_score_pipeline: float
    min_signal_score_execution: float
    min_signal_score_guard: float
    signal_n_window: int
    unified_mode: Literal["dry_run", "shadow", "live"]
    unified_poll_seconds: int
    unified_horizon_days: int
    unified_limit_candidates: int
    unified_max_entry_orders: int
    unified_max_contracts_per_order: float
    unified_autonomy_profile: Literal["safe", "balanced", "high"]
    unified_top_n_deep_search: int
    unified_deep_search_timeout_s: float
    unified_min_liquidity_contracts: float
    unified_repeat_market_cooldown_minutes: int
    unified_final_orchestrator_temperature: float
    unified_candidate_scan_multiplier: int
    unified_scout_override_priority: float
    unified_weather_series_tag: str | None
    unified_category_scope: str
    unified_restricted_to_live_bets: bool
    unified_restricted_to_weather_family: bool
    unified_selection_policy_notes: str
    unified_run: Literal["once", "loop"]
    unified_agent_output_json: str
    unified_print_agent_outputs: bool

    @property
    def kalshi_rest_base_url(self) -> str:
        """HTTPS base for Trade API v2 (demo or prod)."""
        return kalshi_rest_base_url(self.kalshi_env)

    @property
    def kalshi_ws_base_url(self) -> str:
        """WebSocket base for Trade API v2 (demo or prod)."""
        return kalshi_ws_base_url(self.kalshi_env)

    @property
    def kalshi_base_url(self) -> str:
        """Alias for :meth:`kalshi_rest_base_url` (backward compatible)."""
        return self.kalshi_rest_base_url


def _optional_str(name: str) -> str | None:
    v = os.getenv(name)
    v = v.strip() if v else ""
    return v or None


def _bool_env(name: str, default: bool) -> bool:
    raw = _optional_str(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _normalized_optional_tag(name: str) -> str | None:
    """Read optional tag-like env vars, tolerating accidental inline comments."""
    raw = _optional_str(name)
    if raw is None:
        return None
    trimmed = raw.strip()
    if not trimmed or trimmed.startswith("#"):
        return None
    return trimmed


def get_settings(*, load_dotenv_file: bool = True) -> Settings:
    if load_dotenv_file:
        # Prefer current .env values over potentially stale shell exports.
        # This avoids accidental prod/demo drift when users edit .env mid-session.
        load_dotenv(override=True)

    env_raw = (_optional_str("KALSHI_ENV") or "demo").lower()
    if env_raw not in ("demo", "prod"):
        raise ValueError(f"KALSHI_ENV must be 'demo' or 'prod'. Got: {env_raw!r}")
    kalshi_env = cast(KalshiEnv, env_raw)

    key_id = _optional_str("KALSHI_API_KEY_ID")

    priv_path_raw = _optional_str("KALSHI_PRIVATE_KEY_PATH")
    priv_path = Path(priv_path_raw).expanduser() if priv_path_raw else None

    db_path_raw = _optional_str("DB_PATH") or "./data/kalshi_weather.sqlite3"
    db_path = Path(db_path_raw).expanduser()

    risk_spread = float(_optional_str("RISK_MAX_YES_SPREAD") or "0.22")
    min_sig_pipe = float(_optional_str("MIN_SIGNAL_SCORE") or "38")
    min_sig_exec = float(_optional_str("MIN_SIGNAL_SCORE_EXECUTION") or str(min_sig_pipe))
    min_sig_guard = float(_optional_str("MIN_SIGNAL_SCORE_GUARD") or str(min_sig_pipe))
    unified_mode_raw = (_optional_str("UNIFIED_MODE") or "dry_run").lower()
    if unified_mode_raw not in ("dry_run", "shadow", "live"):
        raise ValueError(f"UNIFIED_MODE must be one of dry_run|shadow|live. Got: {unified_mode_raw!r}")
    unified_mode = cast(Literal["dry_run", "shadow", "live"], unified_mode_raw)
    unified_run_raw = (_optional_str("UNIFIED_RUN") or "once").lower()
    if unified_run_raw not in ("once", "loop"):
        raise ValueError(f"UNIFIED_RUN must be 'once' or 'loop'. Got: {unified_run_raw!r}")
    unified_run = cast(Literal["once", "loop"], unified_run_raw)
    autonomy_profile_raw = (_optional_str("UNIFIED_AUTONOMY_PROFILE") or "high").lower()
    if autonomy_profile_raw not in ("safe", "balanced", "high"):
        raise ValueError(
            f"UNIFIED_AUTONOMY_PROFILE must be one of safe|balanced|high. Got: {autonomy_profile_raw!r}"
        )
    autonomy_profile = cast(Literal["safe", "balanced", "high"], autonomy_profile_raw)
    return Settings(
        kalshi_env=kalshi_env,
        kalshi_api_key_id=key_id,
        kalshi_private_key_path=priv_path,
        db_path=db_path,
        openai_model=_optional_str("OPENAI_MODEL") or "gpt-4o-mini",
        risk_max_yes_spread=risk_spread,
        signal_lookback_hours=float(_optional_str("SIGNAL_LOOKBACK_HOURS") or "48"),
        signal_max_snapshots_per_market=int(os.getenv("SIGNAL_MAX_SNAPSHOTS_PER_MARKET", "40")),
        signal_min_snapshots=int(os.getenv("SIGNAL_MIN_SNAPSHOTS", "3")),
        signal_stale_seconds=float(_optional_str("SIGNAL_STALE_SECONDS") or "1800"),
        signal_max_mid_volatility=float(_optional_str("SIGNAL_MAX_MID_VOLATILITY") or "0.12"),
        signal_max_single_tick_jump=float(_optional_str("SIGNAL_MAX_SINGLE_TICK_JUMP") or "0.2"),
        min_signal_score_pipeline=min_sig_pipe,
        min_signal_score_execution=min_sig_exec,
        min_signal_score_guard=min_sig_guard,
        signal_n_window=int(os.getenv("SIGNAL_N_WINDOW", "10")),
        unified_mode=unified_mode,
        unified_poll_seconds=int(os.getenv("UNIFIED_POLL_SECONDS", "120")),
        unified_horizon_days=int(os.getenv("UNIFIED_HORIZON_DAYS", "2")),
        unified_limit_candidates=int(os.getenv("UNIFIED_LIMIT_CANDIDATES", "24")),
        unified_max_entry_orders=int(os.getenv("UNIFIED_MAX_ENTRY_ORDERS", "4")),
        unified_max_contracts_per_order=float(_optional_str("UNIFIED_MAX_CONTRACTS_PER_ORDER") or "8"),
        unified_autonomy_profile=autonomy_profile,
        unified_top_n_deep_search=max(0, int(os.getenv("UNIFIED_TOP_N_DEEP_SEARCH", "6"))),
        unified_deep_search_timeout_s=float(_optional_str("UNIFIED_DEEP_SEARCH_TIMEOUT_S") or "8"),
        unified_min_liquidity_contracts=float(_optional_str("UNIFIED_MIN_LIQUIDITY_CONTRACTS") or "8"),
        unified_repeat_market_cooldown_minutes=max(
            1, int(os.getenv("UNIFIED_REPEAT_MARKET_COOLDOWN_MINUTES", "60"))
        ),
        unified_final_orchestrator_temperature=float(
            _optional_str("UNIFIED_FINAL_ORCHESTRATOR_TEMPERATURE") or "0.35"
        ),
        unified_candidate_scan_multiplier=max(1, int(os.getenv("UNIFIED_CANDIDATE_SCAN_MULTIPLIER", "4"))),
        unified_scout_override_priority=float(_optional_str("UNIFIED_SCOUT_OVERRIDE_PRIORITY") or "70"),
        unified_weather_series_tag=_normalized_optional_tag("UNIFIED_WEATHER_SERIES_TAG"),
        unified_category_scope=_optional_str("UNIFIED_CATEGORY_SCOPE") or "weather_only",
        unified_restricted_to_live_bets=_bool_env("UNIFIED_RESTRICTED_TO_LIVE_BETS", False),
        unified_restricted_to_weather_family=_bool_env("UNIFIED_RESTRICTED_TO_WEATHER_FAMILY", True),
        unified_selection_policy_notes=_optional_str("UNIFIED_SELECTION_POLICY_NOTES")
        or "Broad discovery then top-N deep research; minimal hard rails for entry validity.",
        unified_run=unified_run,
        unified_agent_output_json=_optional_str("UNIFIED_AGENT_OUTPUT_JSON") or "./data/agent_outputs_latest.json",
        unified_print_agent_outputs=_bool_env("UNIFIED_PRINT_AGENT_OUTPUTS", False),
    )

