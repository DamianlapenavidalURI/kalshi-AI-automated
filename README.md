# Kalshi Weather Market Analysis and Trading Automation

Local-first, weather-focused Kalshi trading system for discovery, AI-assisted idea generation, deterministic risk filtering, and dashboard monitoring.

This project is designed for fast iteration and safe automation:

- AI helps with research and trade reasoning.
- Deterministic code enforces risk constraints and execution safety.
- SQLite stores runtime state and diagnostics locally.
- Streamlit provides two views: **User** and **Developer** dashboards.

## Course Project Summary

### 1) Project Partner(s)

- **Damian Lapena Vidal** (current repo owner)
- Team status: **solo project**.

### 2) Project Title

- **Kalshi Weather Market Analysis and Trading Automation**  
AI-assisted, deterministic, local-first weather market automation for Kalshi demo/live trading.

### 3) Clear Project Description

Problem addressed:

- Manually scanning weather markets, checking data quality, and applying consistent execution safety is slow and error-prone.
- Users need a repeatable automation workflow that is explainable, debuggable, and safe for operation.

Solution and core functionality:

- Discover short-horizon weather markets from Kalshi.
- Enrich candidates with external weather/context sources.
- Run multi-agent reasoning (Scout/Context/Edge/Critique/Final Orchestrator) for entry decisions.
- Enforce deterministic hard rails before any submission (market status, liquidity, repeat protection, rolling limits).
- Persist diagnostics and state in SQLite and JSON artifacts.
- Provide:
  - **User dashboard** focused on performance, bets, and money outcomes.
  - **Developer dashboard** focused on runtime diagnostics and troubleshooting.

Practical impact / innovation:

- Combines AI-assisted analysis with strict deterministic guardrails.
- Supports operational transparency (decision traces, stage timing, rejection reasons).
- Designed for rapid local iteration while preserving safety and modularity.

### 4) Resources Used

Open-source repositories / libraries:

- Kalshi SDK and ecosystem tooling (official Kalshi Python integration through project wrappers).
- Python ecosystem libraries used in this project (for example Streamlit, LangChain/OpenAI client integrations, SQLite tooling).

Data sources:

- **Kalshi market/orderbook/position/fill APIs** (demo/live environment).
- **OpenWeather** API for weather context.
- Additional adapters currently integrated: Open-Meteo, NOAA/NWS, NHC, USGS, and news search sources.
- No fixed offline training dataset is required; this system is primarily API/data-feed driven.

Class/lab adaptation:

- Applies class themes of AI-assisted software systems, observability, and safe automation.
- Integrates milestone-based build/test/document workflow aligned with course project progression.

### 5) Deliverables and Timeline

Planned / implemented deliverables:

- Runnable local codebase with one-command orchestration entrypoint.
- Working dual-cadence runtime loop:
  - heavy full-cycle orchestration
  - lightweight risk watcher
- Two dashboards:
  - user-facing performance/bets dashboard
  - developer troubleshooting dashboard
- Deterministic risk controls and execution safety checks.
- Documentation and exact local run instructions.
- Demonstration outputs:
  - cycle diagnostics (`data/agent_outputs_latest.json`)
  - SQLite records for proposals/orders/state
  - dashboard evidence of performance and system behavior.

Milestone timeline (evolved from initial plan):

- **Milestone 1:** connectivity, config, persistence, dashboard skeleton.
- **Milestone 2:** deterministic filtering + rejection reasons + short-horizon focus.
- **Milestone 3:** AI-assisted proposal/orchestration layer and decision traces.
- **Milestone 4:** deterministic execution rails + controlled execution + user/developer dashboards.
- **Final presentation:** end-to-end loop, diagnostics, and performance-focused monitoring.

## Project Purpose

The final project is a practical automation loop that:

- discovers short-horizon weather markets,
- enriches candidates with external weather context,
- runs multi-agent analysis to propose entry actions,
- enforces hard deterministic rails before any order submission,
- tracks outcomes and state over time for monitoring and debugging.

**Important:** this system supports both Kalshi demo and live environments. Start in demo first, validate behavior, then move to live only when comfortable with risk controls and monitoring.

## Core Functionality (Start to End)

1. Load settings and credentials from `.env`.
2. Build Kalshi SDK-backed client.
3. Discover and hydrate weather market candidates.
4. Enrich top candidates with deeper research.
5. Run specialist agents:
  - Scout
  - Context
  - Edge
  - Critique
  - Final Orchestrator
6. Apply deterministic rails (market status, liquidity floor, anti-repeat, rolling checks).
7. Execute through deterministic execution engine (dry-run/live modes).
8. Persist output diagnostics and state to local files/SQLite.
9. In loop mode, run:
  - heavy full cycle on `full_cycle_seconds`,
  - lightweight risk watcher on `risk_watcher_seconds`.

## Quick Start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
cp .env.example .env
```

Fill required secrets in `.env`:

- `KALSHI_API_KEY_ID (demo and live environments)`
- `KALSHI_PRIVATE_KEY_PATH (demo and live environments)`
- `OPENAI_API_KEY`
- `OPENWEATHER_API_KEY` (recommended for weather enrichment)

## LangChain and Tracing

The project currently uses `langchain-openai` for LLM invocation (`ChatOpenAI`) in the AI orchestration layer.

- LangChain usage in code: `src/kalshi_weather/ai/llm.py`
- LangSmith tracing: **optional** and controlled by environment variables:
  - `LANGSMITH_TRACING=false` (default)
  - `LANGSMITH_API_KEY`
  - `LANGSMITH_PROJECT`

When tracing is enabled, it is useful for debugging prompt behavior, latency, and agent outputs.  
When tracing is disabled, the system runs normally without LangSmith telemetry.

## Main Commands

```bash
# One full cycle
python scripts/run_unified_weather_orchestrator.py # (UNIFIED_RUN=once in .env file)

# Continuous loop
python scripts/run_unified_weather_orchestrator.py # (UNIFIED_RUN=loop in .env file)

# Open dashboard
streamlit run streamlit_app.py
```

## Scheduler Model

The runtime supports two independent cadences in loop mode:

- `--full-cycle-seconds`  
Heavy path: discovery + deep research + agent orchestration + entry planning.
- `--risk-watcher-seconds`  
Lightweight path: open-position and rolling-risk checks.

Legacy:

- `--poll-seconds` is supported as a backward-compatible alias for full-cycle cadence.

## Per-Agent Model Routing

You can assign different OpenAI models per role:

- `OPENAI_MODEL_SCOUT`
- `OPENAI_MODEL_CONTEXT`
- `OPENAI_MODEL_EDGE`
- `OPENAI_MODEL_CRITIQUE`
- `OPENAI_MODEL_FINAL`

Fallback behavior:

- If a role-specific variable is missing, the system uses `OPENAI_MODEL`.
- If `OPENAI_MODEL` is missing, role defaults are used.

Recommended baseline:

- Scout/Context/Critique: lightweight model
- Edge: stronger pricing/edge model
- Final Orchestrator: strongest reasoning model

## Dashboards

The app includes two modes:

- **User dashboard**
  - Focused on outcomes: bets placed, executed bets, active bets, estimated PnL/ROI, recent bets.
  - Minimal UI intended for non-technical monitoring.
- **Developer dashboard**
  - Runtime troubleshooting: cycle timing, entry diagnostics, event-market structure, decision traces.
  - Designed for tuning, debugging, and behavior inspection.

UI controls:

- Manual refresh button.
- Optional auto-refresh every 5 seconds.

## Safety and Determinism

Execution remains deterministic and safety-first:

- market must be open,
- minimum liquidity threshold enforced,
- anti-repeat cooldown checks,
- rolling matched-contract velocity guard,
- optional category/event exposure controls,
- deterministic execution engine decisioning before submission.

AI never bypasses deterministic hard rails.

## Data and Outputs

Primary artifacts:

- `data/agent_outputs_latest.json`  
Latest full-cycle diagnostics (stage timings, entry decisions, execution outcomes).
- SQLite DB at `DB_PATH` (default `data/kalshi_weather.sqlite3`)  
Stores proposals, order/execution records (`execution_orders`), market/thesis state, monitoring snapshots, and audit data.

## Operational Modes

Set via `UNIFIED_MODE`:

- `dry_run`: no exchange submission
- `live`: submit orders through execution engine

## Important Environment Variables

Below is the full list of variables currently used in `.env`, grouped by function.

Kalshi connectivity and environment:
- `KALSHI_ENV`: target Kalshi environment (`demo` or `prod`).
- `KALSHI_API_KEY_ID`: Kalshi API key id for the selected environment.
- `KALSHI_PRIVATE_KEY_PATH`: absolute/relative path to Kalshi PEM private key.
- `KALSHI_SSL_VERIFY`: enable/disable TLS certificate verification for Kalshi requests.

Local persistence:
- `DB_PATH`: SQLite file path for runtime state, orders, proposals, and diagnostics.

AI models and weather enrichment:
- `OPENAI_API_KEY`: OpenAI API key used by all AI agents.
- `OPENAI_MODEL`: global fallback model for agents.
- `OPENAI_MODEL_SCOUT`: model used by Scout agent.
- `OPENAI_MODEL_CONTEXT`: model used by Context agent.
- `OPENAI_MODEL_EDGE`: model used by Edge agent.
- `OPENAI_MODEL_CRITIQUE`: model used by Critique agent.
- `OPENAI_MODEL_FINAL`: model used by Final Orchestrator agent.
- `OPENWEATHER_API_KEY`: OpenWeather API key for weather data enrichment.
- `OPENWEATHER_TTL_SECONDS`: cache TTL for OpenWeather responses.

LangSmith tracing (optional observability):
- `LANGSMITH_TRACING`: enables/disables LangSmith tracing.
- `LANGSMITH_API_KEY`: LangSmith API key when tracing is enabled.
- `LANGSMITH_PROJECT`: LangSmith project name for trace grouping.

Unified runtime controls:
- `UNIFIED_MODE`: execution mode (`dry_run` or `live`).
- `UNIFIED_RUN`: scheduler mode (`once` or `loop`).
- `UNIFIED_POLL_SECONDS`: legacy alias for full-cycle cadence.
- `UNIFIED_FULL_CYCLE_SECONDS`: cadence for heavy full cycles (discovery + research + entry).
- `UNIFIED_RISK_WATCHER_SECONDS`: cadence for lightweight risk watcher checks.
- `UNIFIED_HORIZON_DAYS`: max days-to-close horizon for candidate discovery.
- `UNIFIED_LIMIT_CANDIDATES`: max candidates considered per cycle.
- `UNIFIED_MAX_ENTRY_ORDERS`: max new entry intents per cycle.
- `UNIFIED_MAX_CONTRACTS_PER_ORDER`: max contracts allowed in a single entry order.
- `UNIFIED_AUTONOMY_PROFILE`: preset aggressiveness profile (`safe`, `balanced`, `high`).
- `UNIFIED_TOP_N_DEEP_SEARCH`: number of candidates receiving expensive deep research.
- `UNIFIED_DEEP_SEARCH_TIMEOUT_S`: timeout per deep research source call.
- `UNIFIED_MIN_LIQUIDITY_CONTRACTS`: minimum liquidity floor required before entry.
- `UNIFIED_REPEAT_MARKET_COOLDOWN_MINUTES`: cooldown before re-entering same market.
- `UNIFIED_REPEAT_THESIS_COOLDOWN_MINUTES`: cooldown before repeating same thesis.
- `UNIFIED_FINAL_ORCHESTRATOR_TEMPERATURE`: temperature for final orchestrator model call.
- `UNIFIED_CANDIDATE_SCAN_MULTIPLIER`: breadth multiplier before narrowing to final entry set.
- `UNIFIED_CANDIDATE_SELECTION_MODE`: candidate slice policy (`ranked`, `random`, or `random_all`) after prefiltering.
- `UNIFIED_CANDIDATE_SELECTION_POOL_MULTIPLIER`: in `random` mode, sample from top `limit * multiplier` (ignored in `ranked` and `random_all`).
- `UNIFIED_SCOUT_OVERRIDE_PRIORITY`: threshold allowing Scout soft-reject override.
- `UNIFIED_DATA_FETCH_WORKERS`: worker count for market/orderbook hydration.
- `UNIFIED_WEATHER_SERIES_TAG`: optional series filter (blank = no filter).
- `UNIFIED_AGENT_OUTPUT_JSON`: output path for latest full-cycle agent diagnostics JSON.
- `UNIFIED_PRINT_AGENT_OUTPUTS`: print full agent output JSON to stdout/logs.

## Architecture Map

Entrypoint:

- `scripts/run_unified_weather_orchestrator.py`

Core orchestration:

- `src/kalshi_weather/system/orchestrator.py`
- `src/kalshi_weather/system/datahub.py`
- `src/kalshi_weather/system/swarm.py`
- `src/kalshi_weather/system/web_research.py`

Execution + risk:

- `src/kalshi_weather/execution/engine.py`
- `src/kalshi_weather/execution/risk.py`
- `src/kalshi_weather/execution/models.py`

Persistence + config:

- `src/kalshi_weather/db/db.py`
- `src/kalshi_weather/db/schema.py`
- `src/kalshi_weather/config.py`

Kalshi client integration:

- `src/kalshi_weather/kalshi/client.py`
- `src/kalshi_weather/kalshi/auth.py`
- `src/kalshi_weather/kalshi/env.py`

## macOS SSL Note

If you hit certificate errors with Kalshi calls:

```bash
export KALSHI_SSL_VERIFY=true
export KALSHI_SSL_CA_CERT="$(python -c 'import certifi; print(certifi.where())')"
```

Debug-only fallback (not recommended):

```bash
export KALSHI_SSL_VERIFY=false
```

