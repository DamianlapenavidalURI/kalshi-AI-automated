from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import re
from typing import Any, Callable

from kalshi_weather.config import get_settings
from kalshi_weather.system.contracts import (
    CandidateContext,
    EvidenceBundle,
    compute_evidence_hash,
    make_thesis_key,
)
from kalshi_weather.tools.news_search import duckduckgo_search
from kalshi_weather.tools.nhc import current_storms
from kalshi_weather.tools.nws import alerts_brief, forecast_brief
from kalshi_weather.tools.open_meteo import geocode_open_meteo, history_brief
from kalshi_weather.tools.openweather import get_openweather_client
from kalshi_weather.tools.usgs import all_day_quakes

_VS_SPLIT = re.compile(r"\b(vs\.?|versus|v\.?|@)\b", re.I)
_SPACE = re.compile(r"\s+")
_DATE_YYYY_MM_DD = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_CITY_STATE = re.compile(r"\b([A-Za-z][A-Za-z .'-]{1,40}),\s*([A-Z]{2})\b")
_STATE_ONLY = re.compile(r"\b([A-Z]{2})\b")
_AIRPORT_HINTS: dict[str, tuple[str, str]] = {
    "SFO": ("San Francisco, CA", "CA"),
    "NYC": ("New York, NY", "NY"),
    "CHI": ("Chicago, IL", "IL"),
    "DEN": ("Denver, CO", "CO"),
    "ATL": ("Atlanta, GA", "GA"),
    "AUS": ("Austin, TX", "TX"),
    "PHIL": ("Philadelphia, PA", "PA"),
    "NOLA": ("New Orleans, LA", "LA"),
    "HOU": ("Houston, TX", "TX"),
    "MIA": ("Miami, FL", "FL"),
    "BOS": ("Boston, MA", "MA"),
    "SEA": ("Seattle, WA", "WA"),
    "LAX": ("Los Angeles, CA", "CA"),
}
_SOURCE_WEIGHTS = {
    "open_meteo_geocode": 1.0,
    "nws_forecast": 1.0,
    "nws_alerts": 0.95,
    "open_meteo_history": 0.9,
    "duckduckgo_news": 0.65,
    "nhc_storms": 0.95,
    "usgs_quakes": 0.95,
}
_THRESHOLD_PAT = re.compile(r"\b(?:above|over|exceed|greater than|below|under|less than)\s+(-?\d+(?:\.\d+)?)")
_ANY_NUMBER_PAT = re.compile(r"(-?\d+(?:\.\d+)?)")


def _clean_entity(s: str) -> str:
    return _SPACE.sub(" ", s.strip(" -,:;|"))


def _f(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def _parse_threshold_from_title(title: str) -> float | None:
    m = _THRESHOLD_PAT.search(title.lower())
    if m:
        return _f(m.group(1))
    m2 = _ANY_NUMBER_PAT.search(title)
    if m2:
        return _f(m2.group(1))
    return None


def _model_probability_from_delta(delta: float) -> float:
    # Bounded, deterministic mapping from forecast-threshold delta to confidence-like probability.
    score = 0.5 + max(-0.49, min(0.49, delta / 20.0))
    return max(0.01, min(0.99, score))


def _build_evidence_bundle(
    *,
    market_family: str,
    market_ticker: str,
    market_title: str,
    event_ticker: str,
    close_time: str,
    yes_bid: float | None,
    yes_ask: float | None,
    source_status: list[dict[str, Any]],
    freshness_meta: dict[str, Any],
    openweather: dict[str, Any] | None,
) -> EvidenceBundle:
    implied = None
    if yes_bid is not None and yes_ask is not None:
        implied = max(0.0, min(1.0, (yes_bid + yes_ask) / 2.0))
    threshold = _parse_threshold_from_title(market_title)
    forecast_value: float | None = None
    if isinstance(openweather, dict):
        if market_family == "hourly_temperature":
            hourly = openweather.get("hourly") if isinstance(openweather.get("hourly"), list) else []
            if hourly and isinstance(hourly[0], dict):
                forecast_value = _f(hourly[0].get("temp"))
        elif market_family == "snow_and_rain":
            daily = openweather.get("daily") if isinstance(openweather.get("daily"), list) else []
            if daily and isinstance(daily[0], dict):
                rain = _f(daily[0].get("rain")) or 0.0
                snow = _f(daily[0].get("snow")) or 0.0
                forecast_value = rain + snow
        else:
            daily = openweather.get("daily") if isinstance(openweather.get("daily"), list) else []
            if daily and isinstance(daily[0], dict):
                forecast_value = _f(daily[0].get("temp_max")) or _f(daily[0].get("temp_min"))
    model_probability = None
    if forecast_value is not None and threshold is not None:
        model_probability = _model_probability_from_delta(forecast_value - threshold)
    edge = None
    if model_probability is not None and implied is not None:
        edge = model_probability - implied
    confidence = max(0.0, min(1.0, float((freshness_meta or {}).get("source_ok_ratio", 0.0) or 0.0)))
    uncertainty = max(0.0, min(1.0, 1.0 - confidence))
    thesis_key = make_thesis_key(
        market_ticker=market_ticker,
        family=market_family,
        threshold=threshold,
        side_hint="yes",
    )
    bundle = EvidenceBundle(
        market_ticker=market_ticker,
        family=market_family,
        market_title=market_title,
        event_ticker=event_ticker,
        close_time=close_time,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        implied_probability=implied,
        forecast_value=forecast_value,
        threshold=threshold,
        model_probability=model_probability,
        edge=edge,
        confidence=confidence,
        uncertainty=uncertainty,
        agreement_score=confidence,
        sources=source_status,
        data_freshness_seconds=_f((freshness_meta or {}).get("data_freshness_seconds")),
        thesis_key=thesis_key,
    )
    bundle.evidence_hash = compute_evidence_hash(bundle)
    return bundle


def _best_effort_event_day(*, event_title: str, market_title: str, close_time: str | None) -> str | None:
    joined = " | ".join(x for x in (event_title, market_title) if x)
    m = _DATE_YYYY_MM_DD.search(joined)
    if m:
        return m.group(1)
    if close_time:
        try:
            dt = datetime.fromisoformat(close_time.replace("Z", "+00:00")).astimezone(timezone.utc)
            return dt.date().isoformat()
        except ValueError:
            return None
    return None


def _ticker_location_hints(*, market_ticker: str, event_ticker: str) -> tuple[str | None, str | None, list[str]]:
    stems = []
    for raw in (event_ticker, market_ticker):
        token = str(raw or "").split("-")[0].strip().upper()
        if token:
            stems.append(token)
    candidates: list[str] = []
    for stem in stems:
        alpha = re.sub(r"[^A-Z]", "", stem)
        if len(alpha) >= 3:
            candidates.append(alpha[-3:])
        if len(alpha) >= 4:
            candidates.append(alpha[-4:])
    candidates = [c for c in dict.fromkeys(candidates) if c]
    for c in candidates:
        if c in _AIRPORT_HINTS:
            city, st = _AIRPORT_HINTS[c]
            return city, st, [city, c]
    return None, None, candidates


def _location_guess(
    *,
    event_title: str,
    market_title: str,
    market_ticker: str = "",
    event_ticker: str = "",
) -> tuple[str | None, str | None, list[str]]:
    joined = " | ".join(x for x in (event_title, market_title) if x)
    cm = _CITY_STATE.search(joined)
    if cm:
        city = cm.group(1).strip()
        state = cm.group(2).strip()
        return f"{city}, {state}", state, [f"{city}, {state}"]
    sm = _STATE_ONLY.search(joined)
    if sm:
        state = sm.group(1).strip()
        hint_city, _, hint_queries = _ticker_location_hints(
            market_ticker=market_ticker, event_ticker=event_ticker
        )
        return hint_city, state, ([hint_city] if hint_city else []) + hint_queries
    hint_city, hint_state, hint_queries = _ticker_location_hints(
        market_ticker=market_ticker, event_ticker=event_ticker
    )
    return hint_city, hint_state, hint_queries


def extract_entities(*, event_title: str, market_title: str, max_entities: int = 4) -> list[str]:
    text = " | ".join(x for x in (event_title, market_title) if x)
    if not text:
        return []
    parts = _VS_SPLIT.split(text)
    candidates: list[str] = []
    for p in parts:
        item = _clean_entity(p)
        if not item:
            continue
        low = item.lower()
        if any(k in low for k in ("will", "market", "yes", "no", "close", "settle", "contract")):
            continue
        if len(item) < 3:
            continue
        candidates.append(item)
    return list(dict.fromkeys(candidates))[:max_entities]


def _timed_source(
    source: str,
    fn: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    t0 = time.perf_counter()
    try:
        payload = fn()
        ok = bool(payload.get("ok", True))
        return payload, {
            "source": source,
            "ok": ok,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "from_cache": bool(payload.get("_from_cache", False)),
        }
    except Exception as e:  # pragma: no cover - best-effort public web calls
        return {"ok": False, "error": str(e)}, {
            "source": source,
            "ok": False,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "error": str(e),
        }


def _geocode_open_meteo(query: str, timeout_s: float = 8.0) -> dict[str, Any]:
    return geocode_open_meteo(query, timeout_s=timeout_s)


def _nws_brief(*, lat: float, lon: float, timeout_s: float = 8.0) -> dict[str, Any]:
    return forecast_brief(lat=lat, lon=lon, timeout_s=timeout_s)


def _open_meteo_history_brief(*, lat: float, lon: float, event_day: str | None, timeout_s: float = 8.0) -> dict[str, Any]:
    return history_brief(lat=lat, lon=lon, event_day=event_day, timeout_s=timeout_s)


def _nws_alerts_brief(*, state_code: str | None, timeout_s: float = 8.0) -> dict[str, Any]:
    return alerts_brief(state_code=state_code, timeout_s=timeout_s)


def _duckduckgo_search(query: str, timeout_s: float = 8.0) -> dict[str, Any]:
    return duckduckgo_search(query, timeout_s=timeout_s)


def _nhc_storms_brief(timeout_s: float = 8.0) -> dict[str, Any]:
    return current_storms(timeout_s=timeout_s)


def _usgs_quake_brief(timeout_s: float = 8.0) -> dict[str, Any]:
    return all_day_quakes(timeout_s=timeout_s)


def _entity_news(entities: list[str], *, timeout_s: float) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for ent in entities:
        query = f"{ent} weather forecast latest"
        d, _ = _timed_source("duckduckgo_news", lambda q=query: _duckduckgo_search(q, timeout_s=timeout_s))
        topics = d.get("RelatedTopics")
        related_headlines: list[str] = []
        if isinstance(topics, list):
            for t in topics[:8]:
                if not isinstance(t, dict):
                    continue
                txt = t.get("Text")
                if isinstance(txt, str) and txt.strip():
                    related_headlines.append(txt.strip())
        results.append(
            {
                "entity": ent,
                "query": query,
                "abstract": str(d.get("AbstractText") or ""),
                "heading": str(d.get("Heading") or ""),
                "related": related_headlines[:5],
            }
        )
    return results


def _topic_queries(
    *,
    market_family: str,
    event_title: str,
    market_title: str,
) -> list[str]:
    family_terms = {
        "hourly_temperature": ["hourly forecast", "temperature nowcast", "model update"],
        "daily_temperature": ["daily temperature forecast", "today tomorrow high low"],
        "hurricanes": ["hurricane advisory", "landfall forecast", "nhc update"],
        "natural_disasters": ["natural disaster update", "severe weather alerts", "earthquake latest"],
        "snow_and_rain": ["precipitation forecast", "snowfall accumulation", "rainfall update"],
        "climate_change": ["climate change policy", "warming report", "noaa climate news"],
    }
    head = f"{event_title} {market_title}".strip()
    seeds = family_terms.get(market_family, family_terms["daily_temperature"])
    return [f"{head} {term}".strip() for term in seeds[:3]]


def _weather_core(
    *,
    event_title: str,
    market_title: str,
    market_ticker: str = "",
    event_ticker: str = "",
    close_time: str | None,
    timeout_s: float,
) -> dict[str, Any]:
    event_day = _best_effort_event_day(event_title=event_title, market_title=market_title, close_time=close_time)
    location_text, state_code, location_hints = _location_guess(
        event_title=event_title,
        market_title=market_title,
        market_ticker=market_ticker,
        event_ticker=event_ticker,
    )
    geocode_queries = [q for q in [location_text, event_title, market_title, *location_hints] if q]
    geocode: dict[str, Any] = {"ok": False, "error": "no_query"}
    geocode_status: dict[str, Any] = {"source": "open_meteo_geocode", "ok": False, "error": "no_query"}
    for geocode_query in geocode_queries:
        geocode, geocode_status = _timed_source(
            "open_meteo_geocode",
            lambda q=geocode_query: _geocode_open_meteo(q, timeout_s=timeout_s),
        )
        geocode_status["query"] = geocode_query
        if bool(geocode.get("ok")):
            break
    if bool(geocode.get("ok")):
        nws, nws_status = _timed_source(
            "nws_forecast",
            lambda: _nws_brief(lat=float(geocode["latitude"]), lon=float(geocode["longitude"]), timeout_s=timeout_s),
        )
        history, history_status = _timed_source(
            "open_meteo_history",
            lambda: _open_meteo_history_brief(
                lat=float(geocode["latitude"]),
                lon=float(geocode["longitude"]),
                event_day=event_day,
                timeout_s=timeout_s,
            ),
        )
    else:
        nws, nws_status = {"ok": False, "error": "geocode_required"}, {"source": "nws_forecast", "ok": False}
        history, history_status = {
            "ok": False,
            "error": "geocode_required",
        }, {"source": "open_meteo_history", "ok": False}

    alerts, alerts_status = _timed_source(
        "nws_alerts",
        lambda: _nws_alerts_brief(state_code=state_code, timeout_s=timeout_s),
    )
    entities = extract_entities(event_title=event_title, market_title=market_title, max_entities=4)
    news_results = _entity_news(entities, timeout_s=timeout_s)
    return {
        "event_day": event_day,
        "location_guess": location_text,
        "location_hints": location_hints,
        "state_code": state_code,
        "geocode": geocode,
        "nws": nws,
        "nws_alerts": alerts,
        "historical_weather": history,
        "entities": entities,
        "results": news_results,
        "source_status": [geocode_status, nws_status, alerts_status, history_status],
    }


def _family_specific_sources(
    *,
    market_family: str,
    event_title: str,
    market_title: str,
    timeout_s: float,
    deep_search: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    extras: dict[str, Any] = {}
    statuses: list[dict[str, Any]] = []
    if market_family == "hurricanes":
        nhc, st = _timed_source("nhc_storms", lambda: _nhc_storms_brief(timeout_s=timeout_s))
        extras["nhc_storms"] = nhc
        statuses.append(st)
    elif market_family == "natural_disasters":
        usgs, st = _timed_source("usgs_quakes", lambda: _usgs_quake_brief(timeout_s=timeout_s))
        extras["usgs_quakes"] = usgs
        statuses.append(st)

    if deep_search:
        topic_rows: list[dict[str, Any]] = []
        for query in _topic_queries(
            market_family=market_family,
            event_title=event_title,
            market_title=market_title,
        ):
            payload, st = _timed_source("duckduckgo_news", lambda q=query: _duckduckgo_search(q, timeout_s=timeout_s))
            topic_rows.append(
                {
                    "query": query,
                    "heading": str(payload.get("Heading") or ""),
                    "abstract": str(payload.get("AbstractText") or ""),
                }
            )
            statuses.append(st)
        extras["topic_search"] = topic_rows

    return extras, statuses


def _openweather_family_fetch(
    *,
    market_family: str,
    geocode: dict[str, Any],
    timeout_s: float,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if market_family not in {"daily_temperature", "hourly_temperature", "snow_and_rain"}:
        return {}, None
    if not bool(geocode.get("ok")):
        return {}, {"source": "openweather", "ok": False, "error": "geocode_required"}
    settings = get_settings(load_dotenv_file=False)
    if not settings.openweather_api_key:
        return {}, {"source": "openweather", "ok": False, "error": "openweather_api_key_missing"}
    lat = _f(geocode.get("latitude"))
    lon = _f(geocode.get("longitude"))
    if lat is None or lon is None:
        return {}, {"source": "openweather", "ok": False, "error": "missing_lat_lon"}
    t0 = time.perf_counter()
    try:
        ow_client = get_openweather_client(
            api_key=settings.openweather_api_key,
            ttl_seconds=settings.openweather_ttl_seconds,
            timeout_seconds=timeout_s,
        )
        payload = ow_client.fetch_weather(lat=lat, lon=lon, units="imperial")
        status = {
            "source": "openweather",
            "ok": True,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "from_cache": bool(payload.get("from_cache")),
            "fetched_at": payload.get("fetched_at"),
        }
        return payload, status
    except Exception as e:  # pragma: no cover - network best-effort
        return {}, {
            "source": "openweather",
            "ok": False,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "error": str(e),
        }


def _evidence_quality(source_status: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    total = len(source_status)
    ok_rows = [r for r in source_status if bool(r.get("ok"))]
    ok = len(ok_rows)
    score_weighted = 0.0
    weight_total = 0.0
    for row in source_status:
        source = str(row.get("source") or "")
        w = float(_SOURCE_WEIGHTS.get(source, 0.5))
        weight_total += w
        if bool(row.get("ok")):
            score_weighted += w
    weighted_ratio = (score_weighted / weight_total) if weight_total > 0 else 0.0
    quality = {
        "source_count": total,
        "source_ok_count": ok,
        "source_ok_ratio": (ok / total) if total > 0 else 0.0,
        "score_0_100": round(weighted_ratio * 100.0, 2),
    }
    latencies = [float(r.get("latency_ms") or 0.0) for r in source_status if bool(r.get("ok"))]
    avg_latency_ms = (sum(latencies) / len(latencies)) if latencies else None
    freshness = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "sources_with_cache_hits": sum(1 for r in source_status if bool(r.get("from_cache"))),
        "source_ok_ratio": quality["source_ok_ratio"],
        "avg_source_latency_ms": avg_latency_ms,
        "data_freshness_seconds": 0.0,
    }
    reliability = {str(r.get("source") or ""): float(_SOURCE_WEIGHTS.get(str(r.get("source") or ""), 0.5)) for r in source_status}
    return quality, freshness, reliability


def build_market_research_brief(
    *,
    market_family: str,
    event_title: str,
    market_title: str,
    market_ticker: str = "",
    event_ticker: str = "",
    close_time: str | None = None,
    yes_bid: float | None = None,
    yes_ask: float | None = None,
    deep_search: bool = False,
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    core = _weather_core(
        event_title=event_title,
        market_title=market_title,
        market_ticker=market_ticker,
        event_ticker=event_ticker,
        close_time=close_time,
        timeout_s=timeout_s,
    )
    extras, extra_statuses = _family_specific_sources(
        market_family=market_family,
        event_title=event_title,
        market_title=market_title,
        timeout_s=timeout_s,
        deep_search=deep_search,
    )
    source_status = list(core.get("source_status") or []) + extra_statuses
    openweather_payload, openweather_status = _openweather_family_fetch(
        market_family=market_family,
        geocode=core.get("geocode") if isinstance(core.get("geocode"), dict) else {},
        timeout_s=timeout_s,
    )
    if isinstance(openweather_status, dict):
        source_status.append(openweather_status)
    quality, freshness, reliability = _evidence_quality(source_status)
    bundle = _build_evidence_bundle(
        market_family=market_family,
        market_ticker=market_ticker,
        market_title=market_title,
        event_ticker=event_ticker,
        close_time=close_time or "",
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        source_status=source_status,
        freshness_meta=freshness,
        openweather=openweather_payload,
    )
    return {
        "market_family": market_family,
        "deep_search": deep_search,
        "event_day": core.get("event_day"),
        "location_guess": core.get("location_guess"),
        "state_code": core.get("state_code"),
        "entities": core.get("entities") or [],
        "results": core.get("results") or [],
        "geocode": core.get("geocode") or {},
        "nws": core.get("nws") or {},
        "nws_alerts": core.get("nws_alerts") or {},
        "historical_weather": core.get("historical_weather") or {},
        "openweather": openweather_payload,
        "source_status": source_status,
        "source_reliability": reliability,
        "freshness_meta": freshness,
        "evidence_quality": quality,
        "evidence_bundle": asdict(bundle),
        "family_extras": extras,
    }


def build_market_web_brief(
    *,
    event_title: str,
    market_title: str,
    close_time: str | None = None,
    max_entities: int = 4,
) -> dict[str, Any]:
    # Backward-compatible wrapper; max_entities is retained for caller compatibility.
    _ = max_entities
    return build_market_research_brief(
        market_family="daily_temperature",
        event_title=event_title,
        market_title=market_title,
        close_time=close_time,
        deep_search=False,
    )


def enrich_candidates_with_research(
    *,
    candidates: list[CandidateContext],
    top_n_deep_search: int,
    max_workers: int = 6,
    timeout_s: float = 8.0,
) -> list[CandidateContext]:
    if not candidates:
        return candidates
    ranked = sorted(
        candidates,
        key=lambda c: float(c.deterministic_inputs.get("prequal_score", 0.0) or 0.0),
        reverse=True,
    )
    deep_tickers = {c.market_ticker for c in ranked[: max(0, int(top_n_deep_search))]}
    work = []
    for c in candidates:
        event_title = str(c.event.get("title") or "")
        market_title = str(c.market.get("title") or "")
        work.append((c, c.market_ticker in deep_tickers, event_title, market_title))

    updates: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(work)))) as ex:
        fut_map = {
            ex.submit(
                build_market_research_brief,
                market_family=c.market_family,
                event_title=event_title,
                market_title=market_title,
                market_ticker=c.market_ticker,
                event_ticker=str(c.market.get("event_ticker") or c.event.get("event_ticker") or ""),
                close_time=str(c.market.get("close_time") or ""),
                yes_bid=_f(c.market.get("yes_bid_dollars")),
                yes_ask=_f(c.market.get("yes_ask_dollars")),
                deep_search=deep,
                timeout_s=timeout_s,
            ): c.market_ticker
            for c, deep, event_title, market_title in work
        }
        for fut in as_completed(fut_map):
            ticker = fut_map[fut]
            try:
                row = fut.result()
            except Exception as e:  # pragma: no cover - best effort enrichment
                row = {
                    "source_status": [],
                    "source_reliability": {},
                    "freshness_meta": {"error": str(e)},
                    "evidence_quality": {"score_0_100": 0.0, "source_count": 0, "source_ok_count": 0},
                }
            updates[ticker] = (
                row,
                row.get("evidence_quality") if isinstance(row.get("evidence_quality"), dict) else {},
                row.get("freshness_meta") if isinstance(row.get("freshness_meta"), dict) else {},
                row.get("source_reliability") if isinstance(row.get("source_reliability"), dict) else {},
            )

    enriched: list[CandidateContext] = []
    for c in candidates:
        row, quality, fresh, reliab = updates.get(c.market_ticker, ({}, {}, {}, {}))
        bundle_payload = row.get("evidence_bundle") if isinstance(row, dict) else None
        bundle = EvidenceBundle(**bundle_payload) if isinstance(bundle_payload, dict) else c.evidence_bundle
        enriched.append(
            CandidateContext(
                market_ticker=c.market_ticker,
                market_family=c.market_family,
                market=c.market,
                event=c.event,
                orderbook=c.orderbook,
                horizon_reason=c.horizon_reason,
                web_research=row,
                evidence_quality=quality,
                freshness_meta=fresh or c.freshness_meta,
                source_reliability=reliab,
                deterministic_inputs=c.deterministic_inputs,
                evidence_bundle=bundle,
                market_state=c.market_state,
                thesis_state=c.thesis_state,
                recent_decisions=c.recent_decisions,
                exposure_context=c.exposure_context,
            )
        )
    return enriched

