from __future__ import annotations

from typing import Any

from kalshi_weather.tools.http_cache import cached_loader, http_get_json


def duckduckgo_search(query: str, *, timeout_s: float = 8.0) -> dict[str, Any]:
    return cached_loader(
        key=f"ddg_news::{query.lower()}",
        ttl_s=900,
        loader=lambda: http_get_json(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": 1, "no_html": 1, "skip_disambig": 1},
            timeout_s=timeout_s,
        ),
    )


def entity_news(entities: list[str], *, timeout_s: float = 8.0) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for ent in entities:
        query = f"{ent} weather forecast latest"
        d = duckduckgo_search(query, timeout_s=timeout_s)
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
