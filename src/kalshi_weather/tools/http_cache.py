from __future__ import annotations

import threading
import time
from typing import Any

import requests

_HEADERS = {
    "User-Agent": "kalshi-weather-tools/1.0 (local demo project)",
    "Accept": "application/json,text/plain,*/*",
}

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def cache_get(key: str, *, ttl_s: float) -> dict[str, Any] | None:
    now = time.time()
    with _LOCK:
        row = _CACHE.get(key)
    if row is None:
        return None
    ts, payload = row
    if now - ts > ttl_s:
        with _LOCK:
            _CACHE.pop(key, None)
        return None
    out = dict(payload)
    out["_from_cache"] = True
    return out


def cache_set(key: str, payload: dict[str, Any]) -> None:
    with _LOCK:
        _CACHE[key] = (time.time(), payload)


def cached_loader(*, key: str, ttl_s: float, loader: Any) -> dict[str, Any]:
    hit = cache_get(key, ttl_s=ttl_s)
    if isinstance(hit, dict):
        return hit
    payload = loader()
    if isinstance(payload, dict):
        cache_set(key, payload)
    return payload


def http_get_json(url: str, *, params: dict[str, Any] | None = None, timeout_s: float = 8.0) -> dict[str, Any]:
    r = requests.get(url, params=params, timeout=timeout_s, headers=_HEADERS)
    r.raise_for_status()
    payload = r.json()
    return payload if isinstance(payload, dict) else {}
