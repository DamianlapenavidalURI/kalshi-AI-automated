from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
import warnings

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
    openai_model_scout: str
    openai_model_context: str
    openai_model_edge: str
    openai_model_critique: str
    openai_model_final: str
    openweather_api_key: str | None
    openweather_ttl_seconds: int
    unified_mode: Literal["dry_run", "live"]
    unified_poll_seconds: int
    unified_full_cycle_seconds: int
    unified_risk_watcher_seconds: int
    unified_horizon_days: int
    unified_limit_candidates: int
    unified_max_entry_orders: int
    unified_max_contracts_per_order: float
    unified_autonomy_profile: Literal["safe", "balanced", "high"]
    unified_top_n_deep_search: int
    unified_deep_search_timeout_s: float
    unified_min_liquidity_contracts: float
    unified_repeat_market_cooldown_minutes: int
    unified_repeat_thesis_cooldown_minutes: int
    unified_final_orchestrator_temperature: float
    unified_candidate_scan_multiplier: int
    unified_candidate_selection_mode: str
    unified_candidate_selection_pool_multiplier: int
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

    unified_mode_raw = (_optional_str("UNIFIED_MODE") or "dry_run").lower()
    if unified_mode_raw == "shadow":
        warnings.warn(
            "UNIFIED_MODE=shadow is deprecated and now maps to dry_run. Use UNIFIED_MODE=dry_run.",
            stacklevel=2,
        )
        unified_mode_raw = "dry_run"
    if unified_mode_raw not in ("dry_run", "live"):
        raise ValueError(f"UNIFIED_MODE must be one of dry_run|live. Got: {unified_mode_raw!r}")
    unified_mode = cast(Literal["dry_run", "live"], unified_mode_raw)
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
    candidate_selection_mode_raw = (
        (_optional_str("UNIFIED_CANDIDATE_SELECTION_MODE") or "ranked").strip().lower()
    )
    if candidate_selection_mode_raw not in {"ranked", "random", "random_all"}:
        raise ValueError(
            "UNIFIED_CANDIDATE_SELECTION_MODE must be one of ranked|random|random_all. "
            f"Got: {candidate_selection_mode_raw!r}"
        )
    openai_model_env = _optional_str("OPENAI_MODEL")
    openai_model_default = openai_model_env or "gpt-4o-mini"
    openai_model_scout = _optional_str("OPENAI_MODEL_SCOUT") or openai_model_default
    openai_model_context = _optional_str("OPENAI_MODEL_CONTEXT") or openai_model_default
    openai_model_edge = _optional_str("OPENAI_MODEL_EDGE") or openai_model_env or "gpt-4.1"
    openai_model_critique = _optional_str("OPENAI_MODEL_CRITIQUE") or openai_model_default
    openai_model_final = _optional_str("OPENAI_MODEL_FINAL") or openai_model_env or "o4-mini"
    unified_poll_seconds = int(os.getenv("UNIFIED_POLL_SECONDS", "120"))
    full_cycle_raw = _optional_str("UNIFIED_FULL_CYCLE_SECONDS")
    if full_cycle_raw is not None:
        unified_full_cycle_seconds = int(full_cycle_raw)
    elif unified_poll_seconds != 120:
        unified_full_cycle_seconds = int(unified_poll_seconds)
    else:
        unified_full_cycle_seconds = 900
    unified_risk_watcher_seconds = int(_optional_str("UNIFIED_RISK_WATCHER_SECONDS") or "20")
    return Settings(
        kalshi_env=kalshi_env,
        kalshi_api_key_id=key_id,
        kalshi_private_key_path=priv_path,
        db_path=db_path,
        openai_model=openai_model_default,
        openai_model_scout=openai_model_scout,
        openai_model_context=openai_model_context,
        openai_model_edge=openai_model_edge,
        openai_model_critique=openai_model_critique,
        openai_model_final=openai_model_final,
        openweather_api_key=_optional_str("OPENWEATHER_API_KEY"),
        openweather_ttl_seconds=max(30, int(os.getenv("OPENWEATHER_TTL_SECONDS", "300"))),
        unified_mode=unified_mode,
        unified_poll_seconds=unified_poll_seconds,
        unified_full_cycle_seconds=max(30, unified_full_cycle_seconds),
        unified_risk_watcher_seconds=max(5, unified_risk_watcher_seconds),
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
        unified_repeat_thesis_cooldown_minutes=max(
            1, int(os.getenv("UNIFIED_REPEAT_THESIS_COOLDOWN_MINUTES", "90"))
        ),
        unified_final_orchestrator_temperature=float(
            _optional_str("UNIFIED_FINAL_ORCHESTRATOR_TEMPERATURE") or "0.35"
        ),
        unified_candidate_scan_multiplier=max(1, int(os.getenv("UNIFIED_CANDIDATE_SCAN_MULTIPLIER", "4"))),
        unified_candidate_selection_mode=candidate_selection_mode_raw,
        unified_candidate_selection_pool_multiplier=max(
            1,
            int(_optional_str("UNIFIED_CANDIDATE_SELECTION_POOL_MULTIPLIER") or "3"),
        ),
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

