from __future__ import annotations

from typing import Any

from kalshi_weather.kalshi.client import KalshiClient

# CLI shortcuts -> (GET /series category, tags string)
CATEGORY_PRESETS: dict[str, tuple[str, str]] = {
    "weather": ("Climate and Weather", "Weather"),
    "macro": ("Economics", "Macroeconomics"),
    "crypto": ("Crypto", "Bitcoin"),
    "finance": ("Financials", "Financials"),
    "baseball": ("Sports", "Baseball"),
    "basketball": ("Sports", "Basketball"),
}


def _next_cursor(page: dict[str, Any]) -> str | None:
    return page.get("cursor") or page.get("next_cursor") or None


def _collect_markets_for_series(
    client: KalshiClient,
    series_ticker: str,
    *,
    max_pages: int,
    cap: int,
    out: list[str],
) -> None:
    cursor: str | None = None
    for _ in range(max_pages):
        if len(out) >= cap:
            return
        page = client.get_markets(
            limit=200,
            cursor=cursor,
            series_ticker=series_ticker,
            status="open",
        )
        for m in page.get("markets") or []:
            if not isinstance(m, dict):
                continue
            t = m.get("ticker") or m.get("market_ticker")
            if isinstance(t, str) and t and t not in out:
                out.append(t)
            if len(out) >= cap:
                return
        cursor = _next_cursor(page)
        if not cursor:
            break


def resolve_market_tickers(
    client: KalshiClient,
    *,
    category: str | None,
    series: str | None,
    tickers_csv: str | None,
    max_markets: int = 80,
    max_series_pages: int = 8,
    max_market_pages: int = 4,
) -> list[str]:
    """Resolve a bounded list of open market tickers from CLI selectors."""
    out: list[str] = []
    if tickers_csv:
        for p in tickers_csv.split(","):
            p = p.strip()
            if p and p not in out:
                out.append(p)
        return out[:max_markets]

    if series:
        _collect_markets_for_series(
            client, series, max_pages=max_market_pages, cap=max_markets, out=out
        )
        return out

    if category:
        key = category.strip().lower()
        if key in CATEGORY_PRESETS:
            cat, tags = CATEGORY_PRESETS[key]
        else:
            cat = category.strip()
            tags = None
        series_tickers: list[str] = []
        cursor: str | None = None
        for _ in range(max_series_pages):
            skw: dict[str, Any] = {"limit": 200, "cursor": cursor, "category": cat}
            if tags:
                skw["tags"] = tags
            page = client.get_series(**skw)
            for s in page.get("series") or []:
                if isinstance(s, dict):
                    st = s.get("ticker")
                    if isinstance(st, str) and st:
                        series_tickers.append(st)
            cursor = _next_cursor(page)
            if not cursor:
                break

        for st in series_tickers:
            if len(out) >= max_markets:
                break
            _collect_markets_for_series(
                client, st, max_pages=max_market_pages, cap=max_markets, out=out
            )
        return out

    raise ValueError("Specify one of: --tickers, --series, or --category")
