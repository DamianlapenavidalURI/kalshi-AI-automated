from __future__ import annotations

from kalshi_weather.discovery_universe.pagination import iter_paginate, next_cursor


def test_next_cursor() -> None:
    assert next_cursor({"cursor": "a"}) == "a"
    assert next_cursor({"next_cursor": "b"}) == "b"
    assert next_cursor({}) is None


def test_iter_paginate_single_page() -> None:
    calls: list[str | None] = []

    def fetch(c: str | None) -> dict:
        calls.append(c)
        return {"x": 1, "cursor": None}

    out = list(iter_paginate(fetch, max_pages=5))
    assert len(out) == 1
    assert calls == [None]


def test_iter_paginate_two_pages() -> None:
    seq = [
        {"n": 1, "cursor": "c2"},
        {"n": 2, "cursor": None},
    ]
    i = 0

    def fetch(c: str | None) -> dict:
        nonlocal i
        p = seq[i]
        i += 1
        return p

    out = list(iter_paginate(fetch, max_pages=5))
    assert [p["n"] for p in out] == [1, 2]
