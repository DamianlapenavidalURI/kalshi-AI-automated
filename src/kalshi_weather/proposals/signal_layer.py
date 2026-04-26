from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kalshi_weather.config import Settings
    from kalshi_weather.db.db import Db

# Skip / rejection vocabulary (Milestone 7)
SKIP_INSUFFICIENT_HISTORY = "insufficient_recent_history"
SKIP_WIDE_SPREAD = "wide_spread"
SKIP_STALE_MARKET = "stale_market"
SKIP_UNSTABLE_PRICE = "unstable_price_behavior"
SKIP_INSUFFICIENT_SIGNAL = "insufficient_signal_quality"


def _parse_dollars(s: Any) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s))
    except ValueError:
        return None


def _parse_observed_at(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_json_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            o = json.loads(raw)
            return o if isinstance(o, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


@dataclass(frozen=True, slots=True)
class SignalParams:
    """Deterministic tunables for short-horizon feature extraction and scoring."""

    lookback_hours: float = 48.0
    max_snapshots_per_market: int = 40
    min_snapshots: int = 3
    max_yes_spread: float = 0.22
    stale_seconds: float = 1800.0
    max_mid_volatility: float = 0.12
    max_single_tick_jump: float = 0.2
    min_signal_score_to_propose: float = 38.0
    n_window: int = 10


@dataclass(frozen=True, slots=True)
class SignalEvaluation:
    market_ticker: str
    latest_snapshot_id: int
    features: dict[str, Any]
    signal_score: float
    candidate_quality_bucket: str
    skip_reason: str | None


def load_recent_snapshots_for_markets(
    conn: sqlite3.Connection,
    market_tickers: list[str],
    *,
    lookback_hours: float,
    max_per_market: int,
) -> dict[str, list[dict[str, Any]]]:
    """Load non-stale live monitor rows per market, oldest-first (for series math)."""
    if not market_tickers:
        return {}
    lookback_minutes = int(max(1, round(float(lookback_hours) * 60.0)))
    lookback = f"-{lookback_minutes} minutes"
    placeholders = ",".join("?" * len(market_tickers))
    cur = conn.execute(
        f"""
        SELECT market_ticker, id, observed_at, fingerprint, market_json
        FROM live_monitor_snapshots
        WHERE IFNULL(is_stale, 0) = 0
          AND market_ticker IN ({placeholders})
          AND datetime(observed_at) >= datetime('now', ?)
        ORDER BY market_ticker ASC, observed_at ASC
        """,
        (*market_tickers, lookback),
    )
    raw: dict[str, list[dict[str, Any]]] = {t: [] for t in market_tickers}
    for row in cur.fetchall():
        mt = str(row[0])
        if mt not in raw:
            continue
        raw[mt].append(
            {
                "id": int(row[1]),
                "observed_at": str(row[2]),
                "fingerprint": str(row[3] or ""),
                "market_json": _parse_json_obj(row[4]),
            }
        )
    out: dict[str, list[dict[str, Any]]] = {}
    for mt, rows in raw.items():
        if len(rows) > max_per_market:
            rows = rows[-max_per_market:]
        out[mt] = rows
    return out


def _series_from_rows(rows: list[dict[str, Any]]) -> dict[str, list[Any]]:
    mids: list[float] = []
    spreads: list[float] = []
    statuses: list[str] = []
    liquidity_flags: list[bool] = []
    observed_dts: list[datetime | None] = []
    fingerprints: list[str] = []
    for r in rows:
        mj = r.get("market_json") or {}
        yb = _parse_dollars(mj.get("yes_bid_dollars"))
        ya = _parse_dollars(mj.get("yes_ask_dollars"))
        if yb is not None and ya is not None and ya > yb:
            mid = (yb + ya) / 2.0
            sp = ya - yb
        else:
            mid = float("nan")
            sp = float("nan")
        mids.append(mid)
        spreads.append(sp)
        st = mj.get("status")
        statuses.append(str(st) if st is not None else "")
        ybs = mj.get("yes_bid_size_fp")
        yas = mj.get("yes_ask_size_fp")
        nob = mj.get("no_bid_size_fp")
        noa = mj.get("no_ask_size_fp")
        vol = mj.get("volume_fp")
        vol24 = mj.get("volume_24h_fp")
        oi = mj.get("open_interest_fp")
        # Book depth / activity from *_fp only (avoid deprecated liquidity / liquidity_dollars).
        liquidity_flags.append(
            bool(
                (ybs not in (None, "", "0"))
                or (yas not in (None, "", "0"))
                or (nob not in (None, "", "0"))
                or (noa not in (None, "", "0"))
                or (vol not in (None, "", "0"))
                or (vol24 not in (None, "", "0"))
                or (oi not in (None, "", "0"))
            )
        )
        observed_dts.append(_parse_observed_at(str(r.get("observed_at") or "")))
        fingerprints.append(str(r.get("fingerprint") or ""))
    return {
        "mid": mids,
        "spread": spreads,
        "status": statuses,
        "liquidity": liquidity_flags,
        "observed_at": observed_dts,
        "fingerprint": fingerprints,
    }


def _populated_mids(mids: list[float]) -> list[float]:
    return [m for m in mids if not math.isnan(m)]


def _volatility_of_mids(mids: list[float]) -> float:
    vals = _populated_mids(mids)
    if len(vals) < 2:
        return 0.0
    deltas = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    if not deltas:
        return 0.0
    mean = sum(deltas) / len(deltas)
    var = sum((d - mean) ** 2 for d in deltas) / len(deltas)
    return math.sqrt(max(0.0, var))


def _max_jump(mids: list[float]) -> float:
    vals = _populated_mids(mids)
    if len(vals) < 2:
        return 0.0
    return max(abs(vals[i + 1] - vals[i]) for i in range(len(vals) - 1))


def _updates_seen(rows: list[dict[str, Any]], series: dict[str, list[Any]]) -> int:
    fps = series["fingerprint"]
    mids = series["mid"]
    spreads = series["spread"]
    n = len(rows)
    if n == 0:
        return 0
    updates = 1
    for i in range(1, n):
        changed = fps[i] != fps[i - 1]
        if not math.isnan(mids[i]) and not math.isnan(mids[i - 1]):
            changed = changed or abs(mids[i] - mids[i - 1]) > 1e-9
        if not math.isnan(spreads[i]) and not math.isnan(spreads[i - 1]):
            changed = changed or abs(spreads[i] - spreads[i - 1]) > 1e-9
        if changed:
            updates += 1
    return updates


def _last_n_slice(series: dict[str, list[Any]], n: int) -> dict[str, list[Any]]:
    if n <= 0:
        return {k: [] for k in series}
    out: dict[str, list[Any]] = {}
    for k, v in series.items():
        out[k] = v[-n:] if len(v) > n else v[:]
    return out


def compute_signal_evaluation(
    *,
    market_ticker: str,
    latest_snapshot_id: int,
    rows_asc: list[dict[str, Any]],
    now_utc: datetime,
    params: SignalParams,
) -> SignalEvaluation:
    """
    Deterministic features + 0–100 signal_score + quality bucket.
    rows_asc: oldest→newest monitor rows for this market (same ticker).
    """
    n_all = len(rows_asc)
    series = _series_from_rows(rows_asc)
    window = _last_n_slice(series, params.n_window)
    mids_w = window["mid"]
    spreads_w = window["spread"]
    mids_pop = _populated_mids(list(mids_w))
    last_mid = mids_pop[-1] if mids_pop else float("nan")
    first_mid = mids_pop[0] if mids_pop else float("nan")
    price_change_last_n = (
        float(last_mid - first_mid) if mids_pop and len(mids_pop) >= 2 else 0.0
    )
    sp_pop = [s for s in spreads_w if not math.isnan(s)]
    last_sp = sp_pop[-1] if sp_pop else float("nan")
    first_sp = sp_pop[0] if sp_pop else float("nan")
    spread_change_last_n = (
        float(last_sp - first_sp) if sp_pop and len(sp_pop) >= 2 else 0.0
    )
    mid_price_change_last_n = price_change_last_n

    vol_all = _volatility_of_mids(list(series["mid"]))
    vol_w = _volatility_of_mids(list(mids_w))
    max_jump = _max_jump(list(series["mid"]))

    last_obs: datetime | None = None
    if rows_asc:
        last_obs = _parse_observed_at(str(rows_asc[-1].get("observed_at") or ""))
    seconds_since_last_update = (
        max(0.0, (now_utc - last_obs).total_seconds()) if last_obs else float("inf")
    )

    first_obs: datetime | None = None
    if rows_asc:
        first_obs = _parse_observed_at(str(rows_asc[0].get("observed_at") or ""))
    span_sec = (
        max(1.0, (last_obs - first_obs).total_seconds())
        if (last_obs and first_obs)
        else 1.0
    )
    snapshot_density = float(n_all) / span_sec

    status_ok = {s for s in series["status"] if s}
    status_consistent = bool(status_ok) and status_ok.issubset({"active", "initialized"})
    liquidity_present = any(series["liquidity"])

    updates_seen = _updates_seen(rows_asc, series)

    last_spread = sp_pop[-1] if sp_pop else float("nan")

    features: dict[str, Any] = {
        "n_snapshots_in_window": n_all,
        "n_window_used": min(params.n_window, n_all),
        "price_change_last_n": round(price_change_last_n, 6),
        "mid_price_change_last_n": round(mid_price_change_last_n, 6),
        "spread_change_last_n": round(spread_change_last_n, 6),
        "volatility_last_n": round(vol_w, 6),
        "volatility_all": round(vol_all, 6),
        "max_mid_jump": round(max_jump, 6),
        "updates_seen_last_n": updates_seen,
        "seconds_since_last_update": round(seconds_since_last_update, 3)
        if seconds_since_last_update != float("inf")
        else None,
        "snapshot_density": round(snapshot_density, 8),
        "status_consistent": status_consistent,
        "liquidity_present": liquidity_present,
        "last_yes_spread": round(last_spread, 6) if not math.isnan(last_spread) else None,
    }

    skip_reason: str | None = None
    if n_all < params.min_snapshots:
        skip_reason = SKIP_INSUFFICIENT_HISTORY
    elif seconds_since_last_update > params.stale_seconds:
        skip_reason = SKIP_STALE_MARKET
    elif not math.isnan(last_spread) and last_spread > params.max_yes_spread:
        skip_reason = SKIP_WIDE_SPREAD
    elif vol_all > params.max_mid_volatility or max_jump > params.max_single_tick_jump:
        skip_reason = SKIP_UNSTABLE_PRICE

    signal_score = _compute_signal_score(
        spread=last_spread if not math.isnan(last_spread) else params.max_yes_spread,
        seconds_since_last_update=seconds_since_last_update,
        n_snapshots=n_all,
        snapshot_density=snapshot_density,
        volatility=vol_all,
        liquidity_present=liquidity_present,
        status_consistent=status_consistent,
        max_yes_spread=params.max_yes_spread,
        min_snapshots=params.min_snapshots,
    )

    features["signal_score_raw"] = round(signal_score, 4)

    if skip_reason is None and signal_score < params.min_signal_score_to_propose:
        skip_reason = SKIP_INSUFFICIENT_SIGNAL

    bucket = _quality_bucket(signal_score)
    return SignalEvaluation(
        market_ticker=market_ticker,
        latest_snapshot_id=latest_snapshot_id,
        features=features,
        signal_score=round(signal_score, 4),
        candidate_quality_bucket=bucket,
        skip_reason=skip_reason,
    )


def _compute_signal_score(
    *,
    spread: float,
    seconds_since_last_update: float,
    n_snapshots: int,
    snapshot_density: float,
    volatility: float,
    liquidity_present: bool,
    status_consistent: bool,
    max_yes_spread: float,
    min_snapshots: int,
) -> float:
    """Weighted 0–100; fully deterministic."""
    # Freshness (0–25): full credit if update within 5 minutes; decay toward 0 at 30 minutes
    stale_scale = 1800.0
    if seconds_since_last_update == float("inf"):
        p_stale = 1.0
    else:
        p_stale = min(1.0, float(seconds_since_last_update) / stale_scale)
    freshness = 25.0 * (1.0 - p_stale)

    # Tight spread (0–25)
    cap = max_yes_spread if max_yes_spread > 0 else 0.22
    p_sp = min(1.0, max(0.0, spread / cap))
    spread_pts = 25.0 * (1.0 - p_sp)

    # History depth (0–15)
    depth = min(1.0, max(0.0, (n_snapshots - 1) / max(1.0, float(min_snapshots))))
    history_pts = 15.0 * depth

    # Observation rate (0–10): ~1 snapshot / 5 min over the span => density ~0.0033/sec
    dens_norm = min(1.0, snapshot_density / 0.002)
    density_pts = 10.0 * dens_norm

    # Stability (0–15): penalize mid volatility
    vol_cap = 0.15
    p_vol = min(1.0, volatility / vol_cap) if vol_cap > 0 else 1.0
    stability_pts = 15.0 * (1.0 - p_vol)

    # Book / status (0–10)
    book_pts = 10.0 if (liquidity_present and status_consistent) else (6.0 if status_consistent else 0.0)

    total = freshness + spread_pts + history_pts + density_pts + stability_pts + book_pts
    return max(0.0, min(100.0, round(total, 4)))


def _quality_bucket(score: float) -> str:
    if score >= 70.0:
        return "high"
    if score >= 45.0:
        return "medium"
    if score >= 25.0:
        return "low"
    return "skip"


def evaluation_to_json_summary(ev: SignalEvaluation) -> str:
    payload = {
        "market_ticker": ev.market_ticker,
        "latest_snapshot_id": ev.latest_snapshot_id,
        "signal_score": ev.signal_score,
        "candidate_quality_bucket": ev.candidate_quality_bucket,
        "skip_reason": ev.skip_reason,
        "features": ev.features,
    }
    return json.dumps(payload, ensure_ascii=False)[:12000]


def enrich_snapshots_with_signals(
    db: "Db",
    snapshots: list[dict[str, Any]],
    *,
    params: SignalParams,
    now_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Attach `_signal_evaluation` to each latest-per-market snapshot dict (mutates copies).
    `snapshots` entries: {id, observed_at, market_json}.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    tickers: list[str] = []
    seen: set[str] = set()
    for s in snapshots:
        mj = s.get("market_json") or {}
        mt = mj.get("ticker") or mj.get("market_ticker")
        if isinstance(mt, str) and mt not in seen:
            seen.add(mt)
            tickers.append(mt)

    with db.connect() as conn:
        grouped = load_recent_snapshots_for_markets(
            conn,
            tickers,
            lookback_hours=params.lookback_hours,
            max_per_market=params.max_snapshots_per_market,
        )

    out: list[dict[str, Any]] = []
    for s in snapshots:
        mj = s.get("market_json") or {}
        mt = mj.get("ticker") or mj.get("market_ticker")
        sid = int(s["id"]) if s.get("id") is not None else -1
        if not isinstance(mt, str):
            copy = dict(s)
            copy["_signal_evaluation"] = None
            out.append(copy)
            continue
        rows = grouped.get(mt) or []
        ev = compute_signal_evaluation(
            market_ticker=mt,
            latest_snapshot_id=sid,
            rows_asc=rows,
            now_utc=now_utc,
            params=params,
        )
        copy = dict(s)
        copy["_signal_evaluation"] = ev
        out.append(copy)
    return out


def signal_params_from_settings(s: "Settings") -> SignalParams:
    """Build tunables from `Settings` (single source for CLI + AI + pipeline)."""
    return SignalParams(
        lookback_hours=s.signal_lookback_hours,
        max_snapshots_per_market=s.signal_max_snapshots_per_market,
        min_snapshots=s.signal_min_snapshots,
        max_yes_spread=s.risk_max_yes_spread,
        stale_seconds=s.signal_stale_seconds,
        max_mid_volatility=s.signal_max_mid_volatility,
        max_single_tick_jump=s.signal_max_single_tick_jump,
        min_signal_score_to_propose=s.min_signal_score_pipeline,
        n_window=s.signal_n_window,
    )
