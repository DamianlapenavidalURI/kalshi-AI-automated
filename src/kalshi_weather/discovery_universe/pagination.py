from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any


def next_cursor(page: dict[str, Any]) -> str | None:
    """Kalshi list endpoints may use ``cursor`` or ``next_cursor``."""
    c = page.get("cursor")
    if isinstance(c, str) and c:
        return c
    nc = page.get("next_cursor")
    if isinstance(nc, str) and nc:
        return nc
    return None


def iter_paginate(
    fetch_page: Callable[[str | None], dict[str, Any]],
    *,
    max_pages: int,
) -> Iterator[dict[str, Any]]:
    cursor: str | None = None
    for _ in range(max(1, max_pages)):
        page = fetch_page(cursor)
        if not isinstance(page, dict):
            break
        yield page
        cursor = next_cursor(page)
        if not cursor:
            break
