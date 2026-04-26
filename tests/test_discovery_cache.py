from __future__ import annotations

import time
from pathlib import Path

from kalshi_weather.discovery_universe.cache import LocalJsonCache, cache_key


def test_cache_key_stable() -> None:
    k1 = cache_key("p", {"a": 1, "b": "x"})
    k2 = cache_key("p", {"b": "x", "a": 1})
    assert k1 == k2


def test_cache_roundtrip(tmp_path: Path) -> None:
    c = LocalJsonCache(tmp_path, default_ttl_s=60.0)
    key = "test_k.json"
    c.set(key, {"hello": 1})
    assert c.get(key) == {"hello": 1}


def test_cache_expires(tmp_path: Path) -> None:
    c = LocalJsonCache(tmp_path, default_ttl_s=0.01)
    c.set("k.json", {"x": 1})
    time.sleep(0.05)
    assert c.get("k.json", ttl_s=0.01) is None


def test_cache_invalid_file(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not-json", encoding="utf-8")
    c = LocalJsonCache(tmp_path)
    assert c.get("bad.json") is None
