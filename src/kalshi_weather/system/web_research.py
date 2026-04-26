from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable

import requests

from kalshi_weather.system.contracts import CandidateContext

_VS_SPLIT = re.compile(r"\b(vs\.?|versus|v\.?|@)\b", re.I)
_SPACE = re.compile(r"\s+")
_DATE_YYYY_MM_DD = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_CITY_STATE = re.compile(r"\b([A-Za-z][A-Za-z .'-]{1,40}),\s*([A-Z]{2})\b")
_STATE_ONLY = re.compile(r"\b([A-Z]{2})\b")
_HEADERS = {
    "User-Agent": "kalshi-weather-research/2.0 (local demo project)",
    "Accept": "application/geo+json,application/json,text/plain,*/*",
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
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str, ttl_s: float) -> Any | None:
    now = time.time()
    with _CACHE_LOCK:
        row = _CACHE.get(key)
    if row is None:
        return None
    ts, value = row
    if now - ts > ttl_s:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)


def _clean_entity(s: str) -> str:
    return _SPACE.sub(" ", s.strip(" -,:;|"))


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


def _location_guess(*, event_title: str, market_title: str) -> tuple[str | None, str | None]:
    joined = " | ".join(x for x in (event_title, market_title) if x)
    cm = _CITY_STATE.search(joined)
    if cm:
        city = cm.group(1).strip()
        state = cm.group(2).strip()
        return f"{city}, {state}", state
    sm = _STATE_ONLY.search(joined)
    if sm:
        return None, sm.group(1).strip()
    return None, None


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


def _http_get_json(url: str, *, params: dict[str, Any] | None = None, timeout_s: float = 8.0) -> dict[str, Any]:
    r = requests.get(url, params=params, timeout=timeout_s, headers=_HEADERS)
    r.raise_for_status()
    payload = r.json()
    return payload if isinstance(payload, dict) else {}


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


def _cached_request(
    *,
    source: str,
    key: str,
    ttl_s: float,
    loader: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    hit = _cache_get(key, ttl_s=ttl_s)
    if isinstance(hit, dict):
        out = dict(hit)
        out["_from_cache"] = True
        return out
    payload = loader()
    if isinstance(payload, dict):
        _cache_set(key, payload)
    return payload


def _geocode_open_meteo(query: str, timeout_s: float = 8.0) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        d = _http_get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 1, "language": "en", "format": "json"},
            timeout_s=timeout_s,
        )
        rows = d.get("results")
        if not isinstance(rows, list) or not rows:
            return {"ok": False, "error": "no_geocode_result", "query": query}
        row = rows[0] if isinstance(rows[0], dict) else {}
        lat = row.get("latitude")
        lon = row.get("longitude")
        if lat is None or lon is None:
            return {"ok": False, "error": "missing_lat_lon", "query": query}
        return {
            "ok": True,
            "query": query,
            "name": row.get("name"),
            "admin1": row.get("admin1"),
            "country": row.get("country"),
            "latitude": float(lat),
            "longitude": float(lon),
        }

    return _cached_request(
        source="open_meteo_geocode",
        key=f"geocode::{query.lower()}",
        ttl_s=3600,
        loader=_load,
    )


def _nws_brief(*, lat: float, lon: float, timeout_s: float = 8.0) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        points = _http_get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", timeout_s=timeout_s)
        props = points.get("properties")
        if not isinstance(props, dict):
            return {"ok": False, "error": "nws_missing_properties"}
        forecast_hourly_url = props.get("forecastHourly")
        if not isinstance(forecast_hourly_url, str) or not forecast_hourly_url.strip():
            return {"ok": False, "error": "nws_missing_forecast_hourly"}
        hourly = _http_get_json(forecast_hourly_url, timeout_s=timeout_s)
        hprops = hourly.get("properties")
        periods = hprops.get("periods") if isinstance(hprops, dict) else None
        out_periods: list[dict[str, Any]] = []
        if isinstance(periods, list):
            for p in periods[:10]:
                if not isinstance(p, dict):
                    continue
                out_periods.append(
                    {
                        "startTime": p.get("startTime"),
                        "temperature": p.get("temperature"),
                        "temperatureUnit": p.get("temperatureUnit"),
                        "windSpeed": p.get("windSpeed"),
                        "shortForecast": p.get("shortForecast"),
                        "probabilityOfPrecipitation": (
                            p.get("probabilityOfPrecipitation", {}).get("value")
                            if isinstance(p.get("probabilityOfPrecipitation"), dict)
                            else None
                        ),
                    }
                )
        return {
            "ok": True,
            "grid_id": props.get("gridId"),
            "grid_x": props.get("gridX"),
            "grid_y": props.get("gridY"),
            "forecast_periods": out_periods,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    return _cached_request(
        source="nws_forecast",
        key=f"nws::{lat:.4f}::{lon:.4f}",
        ttl_s=900,
        loader=_load,
    )


def _open_meteo_history_brief(*, lat: float, lon: float, event_day: str | None, timeout_s: float = 8.0) -> dict[str, Any]:
    if event_day:
        try:
            end = datetime.fromisoformat(event_day).date() - timedelta(days=1)
        except ValueError:
            end = datetime.now(timezone.utc).date() - timedelta(days=1)
    else:
        end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=6)

    def _load() -> dict[str, Any]:
        d = _http_get_json(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "timezone": "UTC",
            },
            timeout_s=timeout_s,
        )
        daily = d.get("daily")
        if not isinstance(daily, dict):
            return {"ok": False, "error": "open_meteo_missing_daily"}
        dates = daily.get("time") if isinstance(daily.get("time"), list) else []
        maxes = daily.get("temperature_2m_max") if isinstance(daily.get("temperature_2m_max"), list) else []
        mins = daily.get("temperature_2m_min") if isinstance(daily.get("temperature_2m_min"), list) else []
        precip = daily.get("precipitation_sum") if isinstance(daily.get("precipitation_sum"), list) else []
        n = min(len(dates), len(maxes), len(mins), len(precip))
        rows: list[dict[str, Any]] = []
        for i in range(n):
            rows.append(
                {
                    "date": dates[i],
                    "temp_max_c": maxes[i],
                    "temp_min_c": mins[i],
                    "precip_mm": precip[i],
                }
            )
        return {"ok": True, "window_start": start.isoformat(), "window_end": end.isoformat(), "daily_rows": rows}

    return _cached_request(
        source="open_meteo_history",
        key=f"history::{lat:.4f}::{lon:.4f}::{event_day or 'none'}",
        ttl_s=21600,
        loader=_load,
    )


def _nws_alerts_brief(*, state_code: str | None, timeout_s: float = 8.0) -> dict[str, Any]:
    if not state_code:
        return {"ok": False, "error": "state_code_unavailable"}

    def _load() -> dict[str, Any]:
        alert_doc = _http_get_json(
            "https://api.weather.gov/alerts/active",
            params={"area": state_code.upper()},
            timeout_s=timeout_s,
        )
        features = alert_doc.get("features")
        count = len(features) if isinstance(features, list) else 0
        return {"ok": True, "state": state_code.upper(), "active_alert_count": count}

    return _cached_request(
        source="nws_alerts",
        key=f"alerts::{state_code.upper()}",
        ttl_s=300,
        loader=_load,
    )


def _duckduckgo_search(query: str, timeout_s: float = 8.0) -> dict[str, Any]:
    return _cached_request(
        source="duckduckgo_news",
        key=f"ddg::{query.lower()}",
        ttl_s=900,
        loader=lambda: _http_get_json(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": 1, "no_html": 1, "skip_disambig": 1},
            timeout_s=timeout_s,
        ),
    )


def _nhc_storms_brief(timeout_s: float = 8.0) -> dict[str, Any]:
    return _cached_request(
        source="nhc_storms",
        key="nhc::current_storms",
        ttl_s=600,
        loader=lambda: _http_get_json("https://www.nhc.noaa.gov/CurrentStorms.json", timeout_s=timeout_s),
    )


def _usgs_quake_brief(timeout_s: float = 8.0) -> dict[str, Any]:
    return _cached_request(
        source="usgs_quakes",
        key="usgs::all_day",
        ttl_s=600,
        loader=lambda: _http_get_json(
            "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
            timeout_s=timeout_s,
        ),
    )


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
    close_time: str | None,
    timeout_s: float,
) -> dict[str, Any]:
    event_day = _best_effort_event_day(event_title=event_title, market_title=market_title, close_time=close_time)
    location_text, state_code = _location_guess(event_title=event_title, market_title=market_title)
    geocode_query = location_text or event_title or market_title

    geocode, geocode_status = _timed_source(
        "open_meteo_geocode",
        lambda: _geocode_open_meteo(geocode_query, timeout_s=timeout_s),
    )
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
    freshness = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "sources_with_cache_hits": sum(1 for r in source_status if bool(r.get("from_cache"))),
    }
    reliability = {str(r.get("source") or ""): float(_SOURCE_WEIGHTS.get(str(r.get("source") or ""), 0.5)) for r in source_status}
    return quality, freshness, reliability


def build_market_research_brief(
    *,
    market_family: str,
    event_title: str,
    market_title: str,
    close_time: str | None = None,
    deep_search: bool = False,
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    core = _weather_core(
        event_title=event_title,
        market_title=market_title,
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
    quality, freshness, reliability = _evidence_quality(source_status)
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
        "source_status": source_status,
        "source_reliability": reliability,
        "freshness_meta": freshness,
        "evidence_quality": quality,
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
                close_time=str(c.market.get("close_time") or ""),
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
            )
        )
    return enriched

