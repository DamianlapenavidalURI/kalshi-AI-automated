from __future__ import annotations
from kalshi_weather.system.orchestrator import UnifiedWeatherOrchestratorConfig, run_unified_weather_cycle
from kalshi_weather.kalshi.client import KalshiClient
from kalshi_weather.kalshi.auth import KalshiAuth
from kalshi_weather.config import get_settings

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import time

# Allow direct script execution without requiring editable install.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _build_client() -> KalshiClient:
    s = get_settings(load_dotenv_file=True)
    if not s.kalshi_api_key_id or not s.kalshi_private_key_path:
        raise SystemExit(
            "Missing Kalshi auth in .env (KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH).")
    auth = KalshiAuth.from_pem_file(
        api_key_id=s.kalshi_api_key_id, private_key_path=s.kalshi_private_key_path)
    return KalshiClient(base_url=s.kalshi_base_url, auth=auth)


def main() -> None:
    settings = get_settings(load_dotenv_file=True)
    p = argparse.ArgumentParser(
        description="Unified weather multi-agent cycle: entry specialist swarm with deterministic risk."
    )
    p.add_argument("--run", choices=("once", "loop"),
                   default=settings.unified_run)
    p.add_argument("--poll-seconds", type=int,
                   default=settings.unified_poll_seconds)
    p.add_argument("--mode", choices=("dry_run", "shadow",
                   "live"), default=settings.unified_mode)
    p.add_argument("--horizon-days", type=int,
                   default=settings.unified_horizon_days)
    p.add_argument("--limit-candidates", type=int,
                   default=settings.unified_limit_candidates)
    p.add_argument("--max-entry-orders", type=int,
                   default=settings.unified_max_entry_orders)
    p.add_argument("--max-contracts-per-order", type=float,
                   default=settings.unified_max_contracts_per_order)
    p.add_argument("--autonomy-profile", choices=("safe", "balanced", "high"),
                   default=settings.unified_autonomy_profile)
    p.add_argument("--top-n-deep-search", type=int,
                   default=settings.unified_top_n_deep_search)
    p.add_argument("--deep-search-timeout-s", type=float,
                   default=settings.unified_deep_search_timeout_s)
    p.add_argument("--min-liquidity-contracts", type=float,
                   default=settings.unified_min_liquidity_contracts)
    p.add_argument("--repeat-market-cooldown-minutes", type=int,
                   default=settings.unified_repeat_market_cooldown_minutes)
    p.add_argument("--final-orchestrator-temperature", type=float,
                   default=settings.unified_final_orchestrator_temperature)
    p.add_argument("--candidate-scan-multiplier", type=int,
                   default=settings.unified_candidate_scan_multiplier)
    p.add_argument("--scout-override-priority", type=float,
                   default=settings.unified_scout_override_priority)
    p.add_argument(
        "--weather-series-tag",
        type=str,
        default=settings.unified_weather_series_tag or "",
        help=(
            "Optional weather series tag filter for discovery "
            "(empty disables tag filtering, recommended default)."
        ),
    )
    p.add_argument(
        "--data-fetch-workers",
        type=int,
        default=int(os.getenv("UNIFIED_DATA_FETCH_WORKERS", "8")),
        help="Parallel SDK workers used for per-cycle market/orderbook hydration.",
    )
    p.add_argument(
        "--agent-output-json",
        type=str,
        default=settings.unified_agent_output_json,
        help="Write full per-agent outputs from each unified cycle.",
    )
    p.add_argument(
        "--print-agent-outputs",
        action=argparse.BooleanOptionalAction,
        default=settings.unified_print_agent_outputs,
        help="Print full agent output JSON to stdout after each cycle.",
    )
    args = p.parse_args()
    run_mode = str(args.run)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    # Keep runtime logs focused on orchestrator signals, not transport chatter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    client = _build_client()
    cfg = UnifiedWeatherOrchestratorConfig(
        model=settings.openai_model,
        kalshi_env=settings.kalshi_env,
        mode=args.mode,
        horizon_days=args.horizon_days,
        limit_candidate_markets=args.limit_candidates,
        max_entry_orders_per_cycle=args.max_entry_orders,
        max_contracts_per_order=args.max_contracts_per_order,
        autonomy_profile=str(args.autonomy_profile),
        top_n_deep_search=max(0, int(args.top_n_deep_search)),
        deep_search_timeout_s=max(2.0, float(args.deep_search_timeout_s)),
        min_liquidity_contracts=max(0.0, float(args.min_liquidity_contracts)),
        repeat_market_cooldown_minutes=max(1, int(args.repeat_market_cooldown_minutes)),
        final_orchestrator_temperature=max(0.0, min(1.0, float(args.final_orchestrator_temperature))),
        candidate_scan_multiplier=max(1, int(args.candidate_scan_multiplier)),
        scout_override_priority_0_100=float(args.scout_override_priority),
        data_fetch_workers=max(1, int(args.data_fetch_workers)),
        weather_series_tag=(str(args.weather_series_tag).strip() or None),
        category_scope=settings.unified_category_scope,
        restricted_to_live_bets=settings.unified_restricted_to_live_bets,
        restricted_to_weather_family=settings.unified_restricted_to_weather_family,
        selection_policy_notes=settings.unified_selection_policy_notes,
    )
    logging.info(
        "[CONFIG] kalshi_env=%s base_url=%s mode=%s autonomy=%s top_n_deep_search=%s weather_series_tag=%s",
        settings.kalshi_env,
        settings.kalshi_base_url,
        cfg.mode,
        cfg.autonomy_profile,
        cfg.top_n_deep_search,
        cfg.weather_series_tag or "(off)",
    )

    def _fmt_top(counter: dict[str, int], k: int = 5) -> str:
        items = sorted(counter.items(), key=lambda x: (-x[1], x[0]))[:k]
        return ", ".join(f"{a}={b}" for a, b in items) if items else "none"

    def _summarize_trace(rows: list[dict[str, object]], *, key: str) -> str:
        picks = [r for r in rows if r.get("decision") == key][:5]
        if not picks:
            return "none"
        return ", ".join(str(r.get("ticker") or "?") for r in picks)

    def run_once() -> None:
        cycle_started = time.time()
        logging.info(
            "[CYCLE] starting unified weather cycle mode=%s",
            cfg.mode,
        )
        out = run_unified_weather_cycle(client, cfg=cfg)
        output_path = Path(args.agent_output_json).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(
            out, ensure_ascii=False, indent=2), encoding="utf-8")
        entry = out.get("entry") if isinstance(out.get("entry"), dict) else {}
        portfolio = out.get("portfolio") if isinstance(
            out.get("portfolio"), dict) else {}
        logging.info(
            "[UNIFIED-WEATHER] mode=%s horizon_days=%s workers=%s | entry intents=%s submitted=%s rejected=%s errors=%s | open_pos=%s exposure=$%.2f",
            cfg.mode,
            cfg.horizon_days,
            cfg.data_fetch_workers,
            entry.get("intents_attempted", 0),
            entry.get("submitted", 0),
            entry.get("risk_rejected", 0),
            entry.get("errors", 0),
            portfolio.get("open_positions_seen", 0),
            float(portfolio.get("total_abs_exposure_dollars", 0.0) or 0.0),
        )
        logging.info("[AGENTS] wrote full outputs to %s", output_path)
        timing = out.get("stage_timing_s") if isinstance(
            out.get("stage_timing_s"), dict) else {}
        if timing:
            logging.info("[TIMING] %s", ", ".join(
                f"{k}={v}s" for k, v in sorted(timing.items())))
        logging.info(
            "[ENTRY] scan_target=%s seen=%s dropped: %s | intent tickers: %s",
            entry.get("candidates_scan_target", 0),
            entry.get("candidates_seen", 0),
            _fmt_top(entry.get("dropped_reasons", {}) if isinstance(
                entry.get("dropped_reasons"), dict) else {}),
            _summarize_trace(
                entry.get("decision_trace", []) if isinstance(
                    entry.get("decision_trace"), list) else [],
                key="intent",
            ),
        )
        logging.info("[CYCLE] completed in %.2fs",
                     max(0.0, time.time() - cycle_started))
        if args.print_agent_outputs:
            print(json.dumps(out, ensure_ascii=False, indent=2))

        logging.debug("unified_weather_cycle_full_json=%s",
                      json.dumps(out, ensure_ascii=False))

    if run_mode == "once":
        logging.info("[RUNNER] one-shot mode enabled")
        run_once()
        return

    logging.info(
        "[RUNNER] loop mode enabled poll_seconds=%s",
        max(10, int(args.poll_seconds)),
    )
    while True:
        try:
            run_once()
        except Exception as e:
            logging.exception("unified weather cycle failed: %s", e)
        time.sleep(max(10, int(args.poll_seconds)))


if __name__ == "__main__":
    main()
