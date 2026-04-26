# Kalshi Weather Betting System

Weather-only, local-first Kalshi demo trading workflow.
Kalshi REST connectivity is implemented via the official `kalshi_python_sync` SDK.

## Quick Start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
cp .env.example .env
```

## Main Commands

```bash
python scripts/run_unified_weather_orchestrator.py --once
python scripts/run_unified_weather_orchestrator.py --run loop
python scripts/run_unified_weather_orchestrator.py --once --autonomy-profile high --top-n-deep-search 8
streamlit run streamlit_app.py
```

Use `python scripts/run_unified_weather_orchestrator.py --once` for a single end-to-end run.
Use `python scripts/run_unified_weather_orchestrator.py --run loop` to run the whole system continuously in one command.
Full per-agent outputs are written to `data/agent_outputs_latest.json` by default.
Default behavior is entry-first (`--entry-only`), with emergency exits disabled unless explicitly enabled.

## Continuous One-Command Run

Recommended command for full continuous operation:

```bash
python scripts/run_unified_weather_orchestrator.py --run loop --data-fetch-workers 8
```

What this single command does continuously:

- runs six-family weather discovery and candidate hydration,
- runs top-N deep research and family-specific entry agents,
- runs a Final Orchestrator Agent to decide `ENTER | SKIP | WAIT | REDUCE_SIZE`,
- applies minimal deterministic hard rails (market open, liquidity floor, anti-repeat),
- repeats with `--poll-seconds` cadence.

Useful knobs:

- `--poll-seconds <n>`: loop interval between cycles.
- `--data-fetch-workers <n>`: parallel SDK fetch workers for market/orderbook hydration.
- `--top-n-deep-search <n>`: number of prequalified candidates that receive expensive deep research.
- `--autonomy-profile <safe|balanced|high>`: controls entry aggressiveness defaults.
- `--entry-only` / `--no-entry-only`: keep focus on entry automation.
- `--enable-emergency-exit` / `--no-enable-emergency-exit`: turn emergency exit automation on/off.
- `--weather-series-tag <tag>`: optional weather series tag filter; leave empty/off by default.

Environment variable alternative for workers:

```bash
export UNIFIED_DATA_FETCH_WORKERS=8
export UNIFIED_WEATHER_SERIES_TAG=
```

## SSL / Certificates (macOS)

If you see `SSLCertVerificationError` when calling Kalshi endpoints, use:

```bash
export KALSHI_SSL_VERIFY=true
export KALSHI_SSL_CA_CERT="$(python -c 'import certifi; print(certifi.where())')"
```

Emergency fallback for local debugging only (not recommended):

```bash
export KALSHI_SSL_VERIFY=false
```

## Orchestration Pattern (SDK + Multi-Agent)

The unified orchestrator is now organized as explicit stages with timing diagnostics:

1. Entry candidate load (six weather families, SDK-backed).
2. Portfolio/fills load.
3. Top-N deep research enrichment.
4. Entry specialist agents + Final Orchestrator Agent.
5. Minimal hard-rail checks (open market, liquidity floor, anti-repeat).
6. Entry execution through deterministic execution engine.
7. Optional emergency-only exit path.

Per-stage timings are logged and included in outputs under `stage_timing_s`.

## End-to-End Runtime (Current)

The project now runs through one operational entrypoint:

```bash
python scripts/run_unified_weather_orchestrator.py --run loop --data-fetch-workers 8
```

What happens in each cycle:

1. Load config and credentials from `.env` via `kalshi_weather.config.get_settings`.
2. Build Kalshi SDK-backed client (`kalshi_weather.kalshi.auth` + `kalshi_weather.kalshi.client`).
3. Discover candidates across hourly/daily/snow-rain/hurricanes/natural-disasters/climate-change and hydrate orderbooks (`kalshi_weather.system.datahub` + `kalshi_weather.discovery_universe`).
4. Enrich only top-N prequalified candidates with deep research (`kalshi_weather.system.web_research`).
5. Run specialist agents + Final Orchestrator Agent for final entry action (`kalshi_weather.system.swarm`).
6. Execute entries through deterministic risk rails (`kalshi_weather.execution`).
7. Optionally run emergency-only exit checks.
8. Persist diagnostics and write full outputs to `data/agent_outputs_latest.json`.
9. Sleep for `--poll-seconds` and repeat in loop mode.

Design principle: AI performs deep research and final entry reasoning; deterministic code enforces objective invalid-condition rails and execution constraints.

## Must-Know Architecture Map

Use this as the primary navigation map for day-to-day development.

### 1) Runtime entrypoint

- `scripts/run_unified_weather_orchestrator.py`
  - CLI flags, loop scheduling, top-level logging, and the one-command runtime flow.

### 2) Core orchestration

- `src/kalshi_weather/system/orchestrator.py`
  - Entry-first stage orchestration, top-N deep-search gating, minimal hard rails, emergency exit path, diagnostics.
- `src/kalshi_weather/system/datahub.py`
  - Candidate and position loading; family routing and parallel market/orderbook hydration.
- `src/kalshi_weather/system/swarm.py`
  - Family-specialist entry agents and Final Orchestrator Agent.
- `src/kalshi_weather/system/contracts.py`
  - Typed contracts passed between datahub, swarm, and orchestrator.
- `src/kalshi_weather/system/web_research.py`
  - Modular family-aware research adapters with caching, timeouts, reliability weights, and top-N deep-search enrichment.

### 3) Kalshi integration (SDK-backed)

- `src/kalshi_weather/kalshi/client.py`
  - Project Kalshi API wrapper around official SDK transport/auth, retries, and error shaping.
- `src/kalshi_weather/kalshi/auth.py`
  - Auth helper for loading PEM keys and signing through SDK auth object.
- `src/kalshi_weather/kalshi/env.py`
  - Demo/prod REST and WS base URL mapping.
- `src/kalshi_weather/kalshi/models.py`
  - Money/portfolio normalization helpers.

### 4) Discovery + scoring

- `src/kalshi_weather/discovery_universe/pipeline.py`
  - Weather market discovery pipeline orchestration.
- `src/kalshi_weather/discovery_universe/fetch.py`
  - Cached API fetch + pagination for series/events/metadata.
- `src/kalshi_weather/discovery_universe/families.py`
  - Market family scope definitions (weather-focused).
- `src/kalshi_weather/discovery_universe/scoring.py`
  - Deterministic ranking and safe-phase filtering.

### 5) Deterministic execution + risk

- `src/kalshi_weather/execution/engine.py`
  - Executes batches of order intents with hard risk checks.
- `src/kalshi_weather/execution/risk.py`
  - Deterministic guardrails (spread, rolling limits, structure constraints).
- `src/kalshi_weather/execution/preflight.py`
  - Pre-submit validation against current market state.
- `src/kalshi_weather/execution/service.py`
  - Demo execution/reconciliation service layer.
- `src/kalshi_weather/execution/models.py`
  - Shared execution dataclasses/configs.

### 6) Proposal pipeline

- `src/kalshi_weather/proposals/pipeline.py`
  - Proposal generation and run summaries.
- `src/kalshi_weather/proposals/signal_layer.py`
  - Deterministic signal computation used by proposals.
- `src/kalshi_weather/proposals/baseline_engine.py`
  - Baseline proposal logic.
- `src/kalshi_weather/proposals/risk_guard.py`
  - Hard proposal-level risk checks.

### 7) Persistence and settings

- `src/kalshi_weather/db/db.py`
  - SQLite access/mutations for runtime tables.
- `src/kalshi_weather/db/schema.py`
  - Canonical schema and migrations.
- `src/kalshi_weather/config.py`
  - Environment-driven configuration for all subsystems.

## System Scope

- Six-family weather candidate discovery and market monitoring.
- Entry-first automation with Final Orchestrator Agent decisions.
- Minimal deterministic hard rails for objective invalid conditions.
- Emergency exit automation optional and disabled by default.
- Demo order execution with dry-run default.
