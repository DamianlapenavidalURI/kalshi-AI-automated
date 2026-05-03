You are helping me build a 3-week project in Cursor: Automated Kalshi Weather Betting System.

Project constraints:
- Use Cursor and its AI coding agents to implement the system
- Use Python 3.11+
- Use OpenAI API for AI reasoning/orchestration where explicitly enabled
- Use Kalshi API for market data and controlled order execution (demo-first, live-supported)
- Use SQLite for persistence
- Use Streamlit for dashboard
- Keep the system local-first and runnable on my machine
- Prefer simple, modular, testable code
- Do not overengineer
- Do not add optional external APIs unless clearly justified first
- Do not stay in planning mode longer than necessary
- Choose sensible defaults and continue building
- Every milestone should end with runnable code, updated documentation, and exact commands for me to test locally

Core product goal:
- Build a robust, local-first Kalshi automation system that can discover markets, analyze candidates, apply deterministic safety filters, and support AI-assisted trade ideas in a controlled way.
- The system must be designed so that market selection, strategy logic, execution safety, and UI are cleanly separated.
- Preserve simplicity and delivery speed over unnecessary sophistication.

Important architecture rules:
- AI may help analyze markets, propose bets, critique ideas, summarize reasoning, and generate journal summaries
- Hard safety rules, market validation, and execution must remain deterministic and enforced in code
- Support both Kalshi demo and live environments, with demo as the default validation path
- The system must support category-aware logic rather than assuming all markets behave the same
- Do not assume every sports market is a simple two-team binary market
- UI and strategy code must fail safely when a market does not fit expected assumptions

Market scope rules:
- Primary scope is short-horizon, event-driven Kalshi markets that are suitable for automation
- Prioritize market families with clearer structure and more reliable data
- Soccer is allowed only as an experimental module, not as the system-wide default assumption
- If soccer data is sparse, inconsistent, or structurally incompatible, the code should reject or downgrade those markets explicitly instead of forcing support
- Exclude season-long, tournament-winner, and long-horizon markets in code with fail-safe filtering
- Prefer short-horizon markets resolving within a configurable time window

Phase-one market preference:
1. weather
2. macro/economic releases
3. financial index ranges
4. crypto ranges
5. soccer only if explicitly enabled as experimental and passing filters

Engineering requirements:
- Keep modules small and understandable
- Prefer deterministic scoring/filtering before any AI step
- Add explicit rejection reasons for skipped markets
- Use feature flags or config flags for experimental categories like soccer
- Separate:
  - Kalshi client
  - market discovery/filtering
  - strategy logic
  - execution/risk controls
  - dashboard/debug views
- The dashboard should help debug why markets are selected or rejected
- The dashboard should expose two views:
  - User dashboard (performance, bets, money outcomes)
  - Developer dashboard (diagnostics, traces, troubleshooting)

Execution and safety requirements:
- Support both demo and live; default operating workflow is demo-first
- Dry-run mode must exist
- Live mode must be explicitly selected and used only after validation in dry-run/demo
- No AI is allowed to override deterministic risk constraints
- Add deterministic checks for:
  - low liquidity
  - wide spreads
  - stale data
  - unsupported market structure
  - close time too soon
  - disallowed long-horizon markets
- All execution decisions must be logged clearly
- Runtime should support dual cadence:
  - heavier full-cycle orchestration loop
  - lightweight risk watcher loop

Milestone rules:
- Milestone 1: working Kalshi connectivity (demo-first, live-capable), config, market discovery, persistence, and dashboard skeleton
- Milestone 2: deterministic market filtering, rejection reasons, short-horizon filtering, and debug-friendly UI
- Milestone 3: baseline strategy proposals and controlled execution planning
- Milestone 4: deterministic risk guard + controlled execution (dry_run and live modes)
- AI reasoning/orchestration should only be added where it improves analysis, explanation, or journaling without weakening safety
- If a milestone constraint conflicts with a new feature request, preserve milestone safety and implement the safest version possible

Implementation behavior:
- Inspect the existing code before rewriting
- Preserve working parts when reasonable
- Refactor incrementally, not blindly
- Do not stay blocked on uncertainty; choose sensible defaults and continue
- When making a major change, also update documentation and local test instructions
- At the end of each milestone, provide:
  - what changed
  - why it changed
  - exact commands to run locally
  - what to test manually

Definition of success:
- The project runs locally
- The user dashboard clearly shows performance/bets outcomes
- The developer dashboard clearly shows market candidates and rejection reasons
- Unsupported soccer markets do not break the UI or strategy flow
- The system is structured so experimental categories can be enabled or disabled safely
- The codebase is modular enough to extend, but simple enough to finish within 3 weeks