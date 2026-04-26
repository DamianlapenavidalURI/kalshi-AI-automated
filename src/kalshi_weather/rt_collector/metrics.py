from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class CollectorMetrics:
    """Runtime metrics (updated from the asyncio collector loop)."""

    messages_in: int = 0
    messages_persisted: int = 0
    messages_dropped_queue: int = 0
    messages_duplicate: int = 0
    reconnect_count: int = 0
    max_queue_size: int = 0
    lag_ms_sum: float = 0.0
    lag_ms_max: float = 0.0
    lag_samples: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def record_lag_ms(self, lag: float | None) -> None:
        if lag is None:
            return
        async with self._lock:
            self.lag_samples += 1
            self.lag_ms_sum += lag
            self.lag_ms_max = max(self.lag_ms_max, lag)

    def lag_p50_approx(self) -> float | None:
        if self.lag_samples == 0:
            return None
        return self.lag_ms_sum / self.lag_samples

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "messages_in": self.messages_in,
            "messages_persisted": self.messages_persisted,
            "messages_dropped_queue": self.messages_dropped_queue,
            "messages_duplicate": self.messages_duplicate,
            "reconnect_count": self.reconnect_count,
            "max_queue_size": self.max_queue_size,
            "lag_ms_avg": self.lag_p50_approx(),
            "lag_ms_max": self.lag_ms_max if self.lag_samples else None,
            "lag_samples": self.lag_samples,
        }


def parse_server_ts(msg: dict) -> float | None:
    """Best-effort server timestamp from Kalshi WS payload (seconds since epoch)."""
    m = msg.get("msg")
    if not isinstance(m, dict):
        return None
    ts = m.get("ts")
    if isinstance(ts, str):
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            return None
    return None


def lag_ms_for_message(msg: dict, *, received_at: float) -> float | None:
    ts = parse_server_ts(msg)
    if ts is None:
        return None
    return max(0.0, (received_at - ts) * 1000.0)
