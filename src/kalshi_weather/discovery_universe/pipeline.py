from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kalshi_weather.discovery_universe.cache import LocalJsonCache, cache_key
from kalshi_weather.discovery_universe.families import DEFAULT_FAMILIES, MarketFamily
from kalshi_weather.discovery_universe.fetch import (
    collect_milestones_for_family,
    collect_open_events_for_series,
    collect_series_for_family,
    index_milestones_by_event,
)
from kalshi_weather.discovery_universe.models import DiscoveryResult, RankedCandidate
from kalshi_weather.discovery_universe.scoring import (
    detect_penalty_flags,
    hours_to_close_from_market,
    passes_safe_phase_one,
    score_market,
)
from kalshi_weather.kalshi.client import KalshiClient

_NUMERIC_TOKEN_PAT = re.compile(r"[-+]?\d+(?:\.\d+)?")


@dataclass(slots=True)
class DiscoveryOptions:
    cache_dir: Path | None = None
    cache_ttl_s: float = 300.0
    max_series_pages_per_tag: int = 4
    max_events_pages_per_series: int = 3
    max_milestone_pages: int = 3
    max_series_per_family: int = 35
    max_total_candidates: int = 600
    min_close_ts: int | None = None  # unix s; default now
    metadata_top_n: int = 40
    safe_min_score: float = 55.0


def _extract_event_markets(event: dict[str, Any]) -> list[dict[str, Any]]:
    # Kalshi can represent nested market contracts under different payload keys.
    for key in ("markets", "nested_markets", "contracts", "options"):
        mk = event.get(key)
        if isinstance(mk, list):
            return [m for m in mk if isinstance(m, dict)]
    return []


def _is_numeric_option(market: dict[str, Any]) -> bool:
    numeric_fields = (
        "strike",
        "strike_price",
        "floor_strike",
        "ceiling_strike",
        "threshold",
        "min_value",
        "max_value",
        "range_min",
        "range_max",
    )
    for key in numeric_fields:
        if market.get(key) is not None:
            return True
    blob = " ".join(
        str(market.get(k) or "")
        for k in (
            "title",
            "yes_sub_title",
            "no_sub_title",
            "subtitle",
            "rules_primary",
            "rules_secondary",
        )
    )
    return bool(_NUMERIC_TOKEN_PAT.search(blob))


def _market_option_kind(market: dict[str, Any]) -> str:
    mtype = str(market.get("market_type") or "").strip().lower()
    if mtype in {"binary", "yes_no", "yesno"}:
        return "binary_yes_no"
    if mtype in {"scalar", "range", "numeric"}:
        return "numeric_option"
    if _is_numeric_option(market):
        return "numeric_option"
    return "categorical_option"


def _market_option_label(market: dict[str, Any]) -> str:
    for key in ("title", "yes_sub_title", "subtitle"):
        val = market.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return str(market.get("ticker") or market.get("market_ticker") or "").strip()


def _normalize_event_market_bundle(event: dict[str, Any], markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    et_raw = event.get("event_ticker") or event.get("ticker")
    et = str(et_raw).strip() if et_raw is not None else ""
    total = len(markets)
    out: list[dict[str, Any]] = []
    for idx, market in enumerate(markets):
        m = dict(market)
        if et and not m.get("event_ticker"):
            m["event_ticker"] = et
        m["event_market_count"] = total
        m["market_index_in_event"] = idx
        m["market_option_kind"] = _market_option_kind(m)
        m["market_option_label"] = _market_option_label(m)
        out.append(m)
    return out


def _cached_metadata(
    client: KalshiClient,
    cache: LocalJsonCache | None,
    event_ticker: str,
) -> tuple[dict[str, Any] | None, bool, bool]:
    """Returns (metadata or None, cache_hit, fetch_ok)."""
    if not event_ticker:
        return None, False, False

    def load() -> dict[str, Any]:
        return client.get_event_metadata(event_ticker)

    if cache is None:
        try:
            return load(), False, True
        except Exception:
            return None, False, False
    payload = {"event_ticker": event_ticker}
    key = cache_key("event_metadata", payload)
    hit = cache.get(key, ttl_s=cache.default_ttl_s * 2)
    if hit is not None and isinstance(hit, dict):
        return hit, True, True
    try:
        data = load()
    except Exception:
        return None, False, False
    cache.set(key, data)
    return data, False, True


def _ensure_markets_for_event(
    client: KalshiClient,
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    mk = _extract_event_markets(event)
    if mk:
        return _normalize_event_market_bundle(event, mk)
    et = event.get("event_ticker") or event.get("ticker")
    if not isinstance(et, str) or not et:
        return []
    try:
        data = client.get_markets(event_ticker=et, limit=200, status="open")
    except Exception:
        return []
    out = data.get("markets") or []
    rows = [m for m in out if isinstance(m, dict)]
    return _normalize_event_market_bundle(event, rows)


def run_discovery(
    client: KalshiClient,
    *,
    families: tuple[MarketFamily, ...] | None = None,
    options: DiscoveryOptions | None = None,
) -> DiscoveryResult:
    opts = options or DiscoveryOptions()
    cache = LocalJsonCache(opts.cache_dir, default_ttl_s=opts.cache_ttl_s) if opts.cache_dir else None
    fams = families or DEFAULT_FAMILIES
    min_ts = opts.min_close_ts if opts.min_close_ts is not None else int(time.time())

    series_seen = events_seen = markets_seen = 0
    cache_hits = cache_misses = 0

    best: dict[str, tuple[MarketFamily, dict[str, Any], dict[str, Any] | None, list[str]]] = {}

    for family in sorted(fams, key=lambda f: f.priority):
        series_rows, sh, sm = collect_series_for_family(
            client,
            family,
            cache=cache,
            max_pages_per_query=opts.max_series_pages_per_tag,
        )
        cache_hits += sh
        cache_misses += sm
        series_seen += len(series_rows)

        milestone_rows, mh, mm = collect_milestones_for_family(
            client,
            family,
            cache=cache,
            max_pages=opts.max_milestone_pages,
        )
        cache_hits += mh
        cache_misses += mm
        m_index = index_milestones_by_event(milestone_rows)

        kept_series = 0
        for s in series_rows:
            if kept_series >= opts.max_series_per_family:
                break
            st = s.get("ticker")
            if not isinstance(st, str) or not st:
                continue
            kept_series += 1

            evs, eh, em = collect_open_events_for_series(
                client,
                st,
                cache=cache,
                max_pages=opts.max_events_pages_per_series,
                min_close_ts=min_ts,
                with_nested_markets=True,
            )
            cache_hits += eh
            cache_misses += em
            events_seen += len(evs)

            for ev in evs:
                et = ev.get("event_ticker") or ev.get("ticker")
                mids = m_index.get(str(et), []) if et else []
                markets = _ensure_markets_for_event(client, ev)
                for m in markets:
                    mt = m.get("ticker") or m.get("market_ticker")
                    if not isinstance(mt, str) or not mt:
                        continue
                    markets_seen += 1
                    if len(best) >= opts.max_total_candidates and mt not in best:
                        continue
                    prev = best.get(mt)
                    if prev is not None and family.priority >= prev[0].priority:
                        continue
                    best[mt] = (family, m, ev if isinstance(ev, dict) else None, mids)

    prelim: list[tuple[float, str, MarketFamily, dict, dict | None, list[str]]] = []
    for mt, (fam, m, ev, mids) in best.items():
        sc, _ = score_market(m, fam, event=ev, metadata=None)
        prelim.append((sc, mt, fam, m, ev, mids))
    prelim.sort(key=lambda x: -x[0])
    meta_keys = {t for _, t, _, _, _, _ in prelim[: opts.metadata_top_n]}

    metadata_by_event: dict[str, dict[str, Any] | None] = {}
    ranked: list[RankedCandidate] = []
    for _, mt, fam, m, ev, mids in prelim:
        et: str | None = None
        if ev:
            raw_et = ev.get("event_ticker") or ev.get("ticker")
            et = str(raw_et) if raw_et else None
        meta: dict[str, Any] | None = None
        if et and et in meta_keys:
            if et not in metadata_by_event:
                md, hit, ok = _cached_metadata(client, cache, et)
                if hit:
                    cache_hits += 1
                else:
                    cache_misses += 1
                metadata_by_event[et] = md if ok else None
            meta = metadata_by_event.get(et)

        score, expl = score_market(m, fam, event=ev, metadata=meta)
        tags: list[str] = []
        if isinstance(m.get("tags"), list):
            tags = [str(x) for x in m["tags"] if x is not None]
        if ev and isinstance(ev.get("tags"), list):
            tags.extend(str(x) for x in ev["tags"] if x is not None)

        ranked.append(
            RankedCandidate(
                family_id=fam.id,
                family_priority=fam.priority,
                market_ticker=mt,
                event_ticker=et,
                series_ticker=(str(m.get("series_ticker")) if m.get("series_ticker") else None),
                title=m.get("title") if isinstance(m.get("title"), str) else None,
                status=str(m.get("status")) if m.get("status") is not None else None,
                category=str(ev.get("category")) if ev and ev.get("category") else None,
                tags=tags,
                score=score,
                explanation=expl,
                market=m,
                event=ev,
                metadata=meta,
                hours_to_close=hours_to_close_from_market(m),
                milestone_ids=mids,
                event_market_count=(
                    int(m.get("event_market_count"))
                    if m.get("event_market_count") is not None
                    else None
                ),
                market_option_kind=(
                    str(m.get("market_option_kind"))
                    if m.get("market_option_kind") is not None
                    else None
                ),
                market_option_label=(
                    str(m.get("market_option_label"))
                    if m.get("market_option_label") is not None
                    else None
                ),
            )
        )

    ranked.sort(key=lambda r: (-r.score, r.family_priority, r.market_ticker))

    safe: list[RankedCandidate] = []
    for r in ranked:
        pids = set(detect_penalty_flags(r.market, r.event, metadata=r.metadata).keys())
        if passes_safe_phase_one(
            r.market,
            r.score,
            pids,
            min_score=opts.safe_min_score,
        ):
            safe.append(r)

    return DiscoveryResult(
        candidates=ranked,
        safe_phase_one=safe,
        series_seen=series_seen,
        events_seen=events_seen,
        markets_seen=markets_seen,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
    )
