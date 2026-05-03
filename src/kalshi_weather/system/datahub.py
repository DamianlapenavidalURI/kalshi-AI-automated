from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from kalshi_weather.discovery_universe.families import DEFAULT_FAMILIES
from kalshi_weather.discovery_universe.pipeline import DiscoveryOptions, run_discovery
from kalshi_weather.discovery_universe.scoring import hours_to_close_from_market
from kalshi_weather.kalshi.client import KalshiClient
from kalshi_weather.system.contracts import CandidateContext

_WEATHER_DISCOVERY_PAT = re.compile(
    r"\b(high|low|temp|temperature|weather|rain|snow|degrees|precip|forecast|kxhigh|kxlow)\b",
    re.I,
)
_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hurricanes", re.compile(r"\b(hurricane|tropical storm|landfall|cyclone)\b", re.I)),
    ("natural_disasters", re.compile(r"\b(earthquake|wildfire|flood|tornado|disaster|eruption)\b", re.I)),
    ("snow_and_rain", re.compile(r"\b(rain|rainfall|snow|precip|precipitation|sleet|inch|inches)\b", re.I)),
    ("hourly_temperature", re.compile(r"\b(hourly|hour|by \d{1,2}(am|pm)|:00)\b", re.I)),
    ("daily_temperature", re.compile(r"\b(today|tomorrow|daily|high|low|temperature|degrees)\b", re.I)),
    ("climate_change", re.compile(r"\b(climate|warming|el nino|la nina|co2|emission)\b", re.I)),
)
_TEMP_STRONG_PAT = re.compile(
    r"\b(max(?:imum)? temperature|min(?:imum)? temperature|high temperature|low temperature|degrees?)\b",
    re.I,
)
_PRECIP_STRONG_PAT = re.compile(r"\b(rain|rainfall|snow|precip|precipitation|sleet|inch|inches)\b", re.I)


def _f(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(str(x).strip())
    except ValueError:
        return None


def _short_horizon_reason(hours_to_close: float | None, *, fallback: bool = False) -> str:
    htxt = f"{hours_to_close:.1f}" if hours_to_close is not None else "unknown"
    prefix = "short_horizon_fb" if fallback else "short_horizon"
    return f"{prefix}:{htxt}h"


def _detect_market_family(
    *, market: dict[str, Any], event: dict[str, Any] | None = None, fallback: str = "daily_temperature"
) -> str:
    ticker = str(market.get("ticker") or market.get("market_ticker") or "").upper()
    if ticker.startswith("KXHIGH") or ticker.startswith("KXLOW") or ticker.startswith("KXTEMP"):
        title_blob = " ".join(
            str(x or "")
            for x in (
                market.get("title"),
                market.get("rules_primary"),
                market.get("rules_secondary"),
                (event or {}).get("title") if isinstance(event, dict) else "",
            )
        )
        if re.search(r"\b(hour|hourly|by \d{1,2}(am|pm)|:00)\b", title_blob, re.I):
            return "hourly_temperature"
        return "daily_temperature"

    blob = " ".join(
        str(x or "")
        for x in (
            market.get("ticker"),
            market.get("title"),
            market.get("series_ticker"),
            market.get("rules_primary"),
            market.get("rules_secondary"),
            (event or {}).get("title") if isinstance(event, dict) else "",
            (event or {}).get("sub_title") if isinstance(event, dict) else "",
        )
    )
    has_temp = bool(_TEMP_STRONG_PAT.search(blob))
    has_precip = bool(_PRECIP_STRONG_PAT.search(blob))
    if has_temp and not has_precip:
        if re.search(r"\b(hour|hourly|by \d{1,2}(am|pm)|:00)\b", blob, re.I):
            return "hourly_temperature"
        return "daily_temperature"

    for family_id, pat in _FAMILY_PATTERNS:
        if pat.search(blob):
            return family_id
    return fallback


def _resolved_family_id(*, discovered_family: str | None, market: dict[str, Any], event: dict[str, Any]) -> str:
    # Keep one reconciliation rule for all families:
    # use discovery family when detection is unknown; otherwise trust
    # detection so we do not keep inconsistent family assignments.
    detected = _detect_market_family(market=market, event=event, fallback="unknown")
    if detected != "unknown":
        return detected
    if discovered_family:
        df = str(discovered_family).strip().lower()
        if df:
            return df
    return "daily_temperature"


def _looks_like_weather_market(market: dict[str, Any]) -> bool:
    blob = " ".join(
        str(market.get(k) or "")
        for k in (
            "ticker",
            "title",
            "series_ticker",
            "rules_primary",
            "rules_secondary",
            "yes_sub_title",
            "no_sub_title",
        )
    )
    return bool(_WEATHER_DISCOVERY_PAT.search(blob))


def _fallback_weather_markets(
    client: KalshiClient,
    *,
    min_close_ts: int,
    horizon_days: int,
    limit_markets: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    max_hours = float(horizon_days * 24)
    cursor: str | None = None
    for _ in range(6):
        try:
            body = client.get_markets(limit=200, cursor=cursor, status="open", min_close_ts=min_close_ts)
        except Exception:
            break
        rows = body.get("markets")
        if not isinstance(rows, list):
            rows = []
        for m in rows:
            if not isinstance(m, dict):
                continue
            mt = str(m.get("ticker") or m.get("market_ticker") or "")
            if not mt or mt in seen:
                continue
            if not _looks_like_weather_market(m):
                continue
            htc = hours_to_close_from_market(m)
            if htc is None or htc <= 0 or htc > max_hours:
                continue
            out.append(m)
            seen.add(mt)
            if len(out) >= limit_markets:
                return out
        cursor = body.get("cursor") or body.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            break
    return out


def _fetch_orderbook_or_none(client: KalshiClient, ticker: str) -> dict[str, Any] | None:
    try:
        ob = client.get_market_orderbook(ticker, depth=20)
    except Exception:
        return None
    return ob if isinstance(ob, dict) else None


def load_weather_candidates_within_days(
    client: KalshiClient,
    *,
    horizon_days: int,
    limit_markets: int,
    data_fetch_workers: int = 8,
    weather_series_tag: str | None = None,
) -> list[CandidateContext]:
    min_close_ts = int(datetime.now(timezone.utc).timestamp())
    normalized_weather_tag = (weather_series_tag or "").strip()
    families = (
        tuple(replace(f, series_tags=(normalized_weather_tag,)) for f in DEFAULT_FAMILIES)
        if normalized_weather_tag
        else DEFAULT_FAMILIES
    )
    result = run_discovery(
        client,
        families=families,
        options=DiscoveryOptions(
            min_close_ts=min_close_ts,
            max_total_candidates=max(40, limit_markets * 4),
            metadata_top_n=max(25, limit_markets * 2),
        ),
    )
    out: list[CandidateContext] = []
    max_hours = float(horizon_days * 24)
    shortlisted: list[tuple[str, dict[str, Any], dict[str, Any], float, str, float]] = []
    for c in result.candidates:
        market = c.market if isinstance(c.market, dict) else None
        if market is None:
            continue
        if c.hours_to_close is None or c.hours_to_close <= 0 or c.hours_to_close > max_hours:
            continue
        family_id = _resolved_family_id(
            discovered_family=c.family_id,
            market=market,
            event=c.event if isinstance(c.event, dict) else {},
        )
        shortlisted.append(
            (
                c.market_ticker,
                market,
                c.event if isinstance(c.event, dict) else {},
                c.hours_to_close,
                family_id,
                c.score,
            )
        )
        if len(shortlisted) >= limit_markets:
            break

    orderbooks: dict[str, dict[str, Any]] = {}
    if shortlisted:
        max_workers = max(1, min(int(data_fetch_workers), len(shortlisted)))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            fut_map = {
                ex.submit(_fetch_orderbook_or_none, client, mt): mt for mt, _, _, _, _, _ in shortlisted
            }
            for fut in as_completed(fut_map):
                mt = fut_map[fut]
                ob = fut.result()
                if isinstance(ob, dict):
                    orderbooks[mt] = ob

    now_iso = datetime.now(timezone.utc).isoformat()
    for mt, market, event, hours_to_close, market_family, prequal_score in shortlisted:
        ob = orderbooks.get(mt)
        if ob is None:
            continue
        event_market_count = int(market.get("event_market_count") or 0)
        market_option_kind = str(market.get("market_option_kind") or "").strip() or "unknown"
        market_option_label = str(market.get("market_option_label") or market.get("title") or "").strip()
        out.append(
            CandidateContext(
                market_ticker=mt,
                market_family=market_family,
                market=market,
                event=event,
                orderbook=ob,
                horizon_reason=_short_horizon_reason(hours_to_close),
                web_research={},
                evidence_quality={"source_count": 0, "score_0_100": 0.0},
                freshness_meta={"loaded_at": now_iso},
                source_reliability={},
                deterministic_inputs={
                    "prequal_score": prequal_score,
                    "family_source": "discovery",
                    "event_market_count": event_market_count,
                    "market_option_kind": market_option_kind,
                    "market_option_label": market_option_label,
                },
            )
        )
    if out:
        return out

    fallback_markets = _fallback_weather_markets(
        client,
        min_close_ts=min_close_ts,
        horizon_days=horizon_days,
        limit_markets=limit_markets,
    )
    enriched: dict[str, dict[str, Any]] = {}
    if fallback_markets:
        max_workers = max(1, min(int(data_fetch_workers), len(fallback_markets)))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            fut_map = {}
            for market in fallback_markets:
                mt = str(market.get("ticker") or market.get("market_ticker") or "")
                if mt:
                    fut_map[ex.submit(_fetch_orderbook_or_none, client, mt)] = mt
            for fut in as_completed(fut_map):
                mt = fut_map[fut]
                ob = fut.result()
                if isinstance(ob, dict):
                    enriched[mt] = ob

    for market in fallback_markets:
        mt = str(market.get("ticker") or market.get("market_ticker") or "")
        if not mt:
            continue
        ob = enriched.get(mt)
        if ob is None:
            continue
        htc = hours_to_close_from_market(market)
        family_id = _detect_market_family(market=market, event={})
        out.append(
            CandidateContext(
                market_ticker=mt,
                market_family=family_id,
                market=market,
                event={},
                orderbook=ob,
                horizon_reason=_short_horizon_reason(htc, fallback=True),
                web_research={},
                evidence_quality={"source_count": 0, "score_0_100": 0.0},
                freshness_meta={"loaded_at": now_iso},
                source_reliability={},
                deterministic_inputs={
                    "prequal_score": 0.0,
                    "family_source": "fallback_regex",
                    "event_market_count": int(market.get("event_market_count") or 0),
                    "market_option_kind": str(market.get("market_option_kind") or "").strip() or "unknown",
                    "market_option_label": str(
                        market.get("market_option_label") or market.get("title") or ""
                    ).strip(),
                },
            )
        )
        if len(out) >= limit_markets:
            break
    return out


def load_candidates_within_days(
    client: KalshiClient,
    *,
    horizon_days: int,
    limit_markets: int,
    data_fetch_workers: int = 8,
    weather_series_tag: str | None = None,
) -> list[CandidateContext]:
    return load_weather_candidates_within_days(
        client,
        horizon_days=horizon_days,
        limit_markets=limit_markets,
        data_fetch_workers=data_fetch_workers,
        weather_series_tag=weather_series_tag,
    )

