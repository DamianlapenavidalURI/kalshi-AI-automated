from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


def cache_key(prefix: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{h}.json"


class LocalJsonCache:
    """File-backed JSON cache with TTL (seconds)."""

    def __init__(self, directory: Path, *, default_ttl_s: float = 300.0) -> None:
        self.directory = directory
        self.default_ttl_s = default_ttl_s

    def _path(self, key: str) -> Path:
        return self.directory / key

    def get(self, key: str, *, ttl_s: float | None = None) -> Any | None:
        ttl = self.default_ttl_s if ttl_s is None else ttl_s
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(raw, dict) or "saved_at" not in raw or "data" not in raw:
            return None
        if time.time() - float(raw["saved_at"]) > ttl:
            return None
        return raw["data"]

    def set(self, key: str, data: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {"saved_at": time.time(), "data": data}
        path = self._path(key)
        path.write_text(json.dumps(payload, default=str), encoding="utf-8")
