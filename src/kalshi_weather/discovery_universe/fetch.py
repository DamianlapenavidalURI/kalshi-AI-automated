from __future__ import annotations

from typing import Any

from kalshi_weather.discovery_universe.cache import LocalJsonCache, cache_key
from kalshi_weather.discovery_universe.families import MarketFamily
from kalshi_weather.discovery_universe.pagination import iter_paginate
from kalshi_weather.kalshi.client import KalshiClient


def _cached_series_page(
    client: KalshiClient,
    cache: LocalJsonCache | None,
    *,
    family_id: str,
    category: str | None,
    tags: str | None,
    cursor: str | None,
) -> tuple[dict[str, Any], bool]:
    payload = {
        "family_id": family_id,
        "category": category or "",
        "tags": tags or "",
        "cursor": cursor or "",
    }

    def load() -> dict[str, Any]:
        return client.get_series(limit=200, cursor=cursor, category=category, tags=tags)

    if cache is None:
        return load(), False
    key = cache_key("series", payload)
    hit = cache.get(key)
    if hit is not None and isinstance(hit, dict):
        return hit, True
    data = load()
    cache.set(key, data)
    return data, False


def _cached_events_page(
    client: KalshiClient,
    cache: LocalJsonCache | None,
    *,
    series_ticker: str,
    min_close_ts: int | None,
    cursor: str | None,
    with_nested_markets: bool,
) -> tuple[dict[str, Any], bool]:
    payload = {
        "series_ticker": series_ticker,
        "min_close_ts": min_close_ts,
        "cursor": cursor or "",
        "nested": with_nested_markets,
    }

    def load() -> dict[str, Any]:
        return client.get_events(
            limit=200,
            cursor=cursor,
            series_ticker=series_ticker,
            status="open",
            min_close_ts=min_close_ts,
            with_nested_markets=with_nested_markets,
        )

    if cache is None:
        return load(), False
    key = cache_key("events", payload)
    hit = cache.get(key)
    if hit is not None and isinstance(hit, dict):
        return hit, True
    data = load()
    cache.set(key, data)
    return data, False


def _cached_milestones_page(
    client: KalshiClient,
    cache: LocalJsonCache | None,
    *,
    family_id: str,
    category: str | None,
    competition: str | None,
    cursor: str | None,
) -> tuple[dict[str, Any], bool]:
    payload = {
        "family_id": family_id,
        "category": category or "",
        "competition": competition or "",
        "cursor": cursor or "",
    }

    def load() -> dict[str, Any]:
        return client.get_milestones(
            limit=200,
            cursor=cursor,
            category=category,
            competition=competition,
        )

    if cache is None:
        return load(), False
    key = cache_key("milestones", payload)
    hit = cache.get(key)
    if hit is not None and isinstance(hit, dict):
        return hit, True
    data = load()
    cache.set(key, data)
    return data, False


def collect_series_for_family(
    client: KalshiClient,
    family: MarketFamily,
    *,
    cache: LocalJsonCache | None,
    max_pages_per_query: int,
) -> tuple[list[dict[str, Any]], int, int]:
    seen: dict[str, dict[str, Any]] = {}
    hits = misses = 0
    categories = family.series_categories or ("",)
    tags = family.series_tags or ("",)
    for cat in categories:
        for tag in tags:
            cat_arg = cat if cat else None
            tag_arg = tag if tag else None
            cursor: str | None = None
            for _ in range(max(1, max_pages_per_query)):
                page, hit = _cached_series_page(
                    client,
                    cache,
                    family_id=family.id,
                    category=cat_arg,
                    tags=tag_arg,
                    cursor=cursor,
                )
                if hit:
                    hits += 1
                else:
                    misses += 1
                for s in page.get("series", []) or []:
                    if not isinstance(s, dict):
                        continue
                    t = s.get("ticker")
                    if isinstance(t, str) and t:
                        seen.setdefault(t, s)
                cursor = page.get("cursor") or page.get("next_cursor")
                if not cursor:
                    break
    return list(seen.values()), hits, misses


def collect_open_events_for_series(
    client: KalshiClient,
    series_ticker: str,
    *,
    cache: LocalJsonCache | None,
    max_pages: int,
    min_close_ts: int | None,
    with_nested_markets: bool = True,
) -> tuple[list[dict[str, Any]], int, int]:
    events_out: list[dict[str, Any]] = []
    hits = misses = 0
    cursor: str | None = None
    for _ in range(max(1, max_pages)):
        page, hit = _cached_events_page(
            client,
            cache,
            series_ticker=series_ticker,
            min_close_ts=min_close_ts,
            cursor=cursor,
            with_nested_markets=with_nested_markets,
        )
        if hit:
            hits += 1
        else:
            misses += 1
        for ev in page.get("events", []) or []:
            if isinstance(ev, dict):
                events_out.append(ev)
        cursor = page.get("cursor") or page.get("next_cursor")
        if not cursor:
            break
    return events_out, hits, misses


def collect_milestones_for_family(
    client: KalshiClient,
    family: MarketFamily,
    *,
    cache: LocalJsonCache | None,
    max_pages: int,
) -> tuple[list[dict[str, Any]], int, int]:
    if not family.milestone_category and not family.milestone_competition:
        return [], 0, 0
    rows: list[dict[str, Any]] = []
    hits = misses = 0
    cursor: str | None = None
    for _ in range(max(1, max_pages)):
        page, hit = _cached_milestones_page(
            client,
            cache,
            family_id=family.id,
            category=family.milestone_category,
            competition=family.milestone_competition,
            cursor=cursor,
        )
        if hit:
            hits += 1
        else:
            misses += 1
        raw = page.get("milestones")
        if raw is None:
            raw = page.get("milestone")
        if isinstance(raw, dict):
            raw = [raw]
        if isinstance(raw, list):
            for m in raw:
                if isinstance(m, dict):
                    rows.append(m)
        cursor = page.get("cursor") or page.get("next_cursor")
        if not cursor:
            break
    return rows, hits, misses


def index_milestones_by_event(milestones: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in milestones:
        et = m.get("related_event_ticker") or m.get("event_ticker")
        mid = m.get("id") or m.get("milestone_id") or m.get("ticker")
        if not isinstance(et, str) or not et:
            continue
        if not isinstance(mid, str):
            continue
        out.setdefault(et, []).append(mid)
    return out
