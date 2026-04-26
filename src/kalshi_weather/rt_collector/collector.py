from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any

import websockets

from kalshi_weather.kalshi.auth import KalshiAuth
from kalshi_weather.kalshi.client import KalshiClient
from kalshi_weather.rt_collector.metrics import CollectorMetrics, lag_ms_for_message
from kalshi_weather.rt_collector.orderbook import OrderBookState
from kalshi_weather.rt_collector.store import RtStore, make_dedupe_key
from kalshi_weather.rt_collector.ws_auth import kalshi_ws_handshake_headers

log = logging.getLogger("kalshi_weather.rt_collector")

GLOBAL_CHANNELS = ("ticker", "trade", "market_lifecycle_v2")
OB_CHANNELS = ("orderbook_delta",)
ORDERBOOK_SUB_BATCH = 32


@dataclass(slots=True)
class CollectorConfig:
    ws_url: str
    rest_base_url: str
    env: str
    db_path: Path
    tickers: list[str]
    queue_maxsize: int = 10_000
    reconnect_max_s: float = 60.0
    reconnect_initial_s: float = 1.0


def _market_ticker_from(data: dict[str, Any]) -> str | None:
    m = data.get("msg")
    if isinstance(m, dict):
        mt = m.get("market_ticker")
        if isinstance(mt, str) and mt:
            return mt
    return None


def bootstrap_rest_snapshots(
    client: KalshiClient,
    store: RtStore,
    *,
    session_id: str,
    tickers: list[str],
) -> dict[str, OrderBookState]:
    books: dict[str, OrderBookState] = {}
    for t in tickers:
        try:
            mr = client.get_market(t)
        except Exception as e:
            log.warning("get_market failed %s: %s", t, e)
            continue
        m = mr.get("market") if isinstance(mr, dict) else None
        if isinstance(m, dict):
            store.upsert_market_metadata(session_id=session_id, market_ticker=t, data=m)
        try:
            obr = client.get_market_orderbook(t)
        except Exception as e:
            log.warning("get_market_orderbook failed %s: %s", t, e)
            continue
        books[t] = OrderBookState.from_rest_orderbook(t, obr)
        snap = books[t].to_snapshot_dict()
        syn: dict[str, Any] = {
            "type": "orderbook_snapshot",
            "msg": {
                "market_ticker": t,
                "market_id": (m or {}).get("id") if isinstance(m, dict) else None,
                "yes_dollars_fp": [[p, str(q)] for p, q in sorted(books[t].yes.items())],
                "no_dollars_fp": [[p, str(q)] for p, q in sorted(books[t].no.items())],
            },
        }
        dk = make_dedupe_key(session_id, "ob_snap", None, None, t, json.dumps(syn, sort_keys=True))
        store.insert_orderbook_snapshot(
            session_id=session_id,
            source="rest",
            market_ticker=t,
            seq=None,
            sid=None,
            dedupe_key=dk,
            msg=syn,
        )
        log.info("REST bootstrap %s levels yes=%d no=%d", t, len(books[t].yes), len(books[t].no))
    return books


async def _send_json(ws: Any, payload: dict[str, Any]) -> None:
    await ws.send(json.dumps(payload, separators=(",", ":")))


async def _subscribe_globals(ws: Any, ids: count) -> None:
    await _send_json(
        ws,
        {
            "id": next(ids),
            "cmd": "subscribe",
            "params": {"channels": list(GLOBAL_CHANNELS)},
        },
    )


async def _subscribe_orderbook_batches(ws: Any, ids: count, tickers: list[str]) -> None:
    for i in range(0, len(tickers), ORDERBOOK_SUB_BATCH):
        batch = tickers[i : i + ORDERBOOK_SUB_BATCH]
        await _send_json(
            ws,
            {
                "id": next(ids),
                "cmd": "subscribe",
                "params": {"channels": list(OB_CHANNELS), "market_tickers": batch},
            },
        )


def _process_ws_message(
    data: dict[str, Any],
    *,
    session_id: str,
    watch: set[str],
    store: RtStore,
    books: dict[str, OrderBookState],
    last_seq: dict[tuple[str, int], int],
    metrics: CollectorMetrics,
    received_at: float,
) -> None:
    typ = str(data.get("type") or "")
    lag = lag_ms_for_message(data, received_at=received_at)

    mt = _market_ticker_from(data)
    sid = data.get("sid")
    seq = data.get("seq")
    sid_i = int(sid) if sid is not None else None
    seq_i = int(seq) if seq is not None else None

    raw_s = json.dumps(data, sort_keys=True, default=str)
    dk_raw = make_dedupe_key(session_id, typ or "msg", seq_i, sid_i, mt, raw_s)

    def _ob_seq_ok() -> bool:
        if mt is None or sid_i is None or seq_i is None:
            return True
        key = (mt, sid_i)
        prev = last_seq.get(key, -1)
        if seq_i <= prev:
            metrics.messages_duplicate += 1
            return False
        last_seq[key] = seq_i
        return True

    if typ == "orderbook_snapshot" and mt:
        if not _ob_seq_ok():
            return
        st = books.setdefault(mt, OrderBookState(market_ticker=mt))
        st.apply_snapshot_msg(data)
        if store.insert_orderbook_snapshot(
            session_id=session_id,
            source="ws",
            market_ticker=mt,
            seq=seq_i,
            sid=sid_i,
            dedupe_key=dk_raw,
            msg=data,
        ):
            metrics.messages_persisted += 1
        return

    if typ == "orderbook_delta" and mt:
        if not _ob_seq_ok():
            return
        st = books.setdefault(mt, OrderBookState(market_ticker=mt))
        st.apply_delta_msg(data)
        if store.insert_orderbook_delta(
            session_id=session_id,
            market_ticker=mt,
            seq=seq_i,
            sid=sid_i,
            dedupe_key=dk_raw,
            msg=data,
            lag_ms=lag,
        ):
            metrics.messages_persisted += 1
        return

    if typ == "ticker":
        if not mt or mt not in watch:
            return
        if store.insert_ticker(session_id=session_id, dedupe_key=dk_raw, msg=data, lag_ms=lag):
            metrics.messages_persisted += 1
        return

    if typ == "trade":
        if not mt or mt not in watch:
            return
        if store.insert_trade(session_id=session_id, dedupe_key=dk_raw, msg=data, lag_ms=lag):
            metrics.messages_persisted += 1
        return

    if typ in ("market_lifecycle_v2", "market_lifecycle"):
        if not mt or mt not in watch:
            return
        if store.insert_lifecycle(session_id=session_id, dedupe_key=dk_raw, msg=data, lag_ms=lag):
            metrics.messages_persisted += 1
        return

    if typ == "error":
        log.warning("WS error frame: %s", data)


async def run_collector_loop(
    *,
    auth: KalshiAuth,
    client: KalshiClient,
    cfg: CollectorConfig,
    stop_event: asyncio.Event,
    on_metrics: Callable[[CollectorMetrics], None] | None = None,
) -> None:
    store = RtStore(cfg.db_path)
    session_id = str(uuid.uuid4())
    watch = set(cfg.tickers)
    store.start_session(
        session_id=session_id,
        env=cfg.env,
        watchlist=list(cfg.tickers),
        meta={"ws_url": cfg.ws_url, "queue_maxsize": cfg.queue_max},
    )

    metrics = CollectorMetrics()
    backoff = cfg.reconnect_initial_s

    while not stop_event.is_set():
        books = await asyncio.to_thread(
            bootstrap_rest_snapshots,
            client,
            store,
            session_id,
            cfg.tickers,
        )
        last_seq: dict[tuple[str, int], int] = {}

        headers = kalshi_ws_handshake_headers(auth)
        try:
            async with websockets.connect(
                cfg.ws_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=90,
                max_size=16 * 1024 * 1024,
            ) as ws:
                backoff = cfg.reconnect_initial_s

                ids = count(1)
                await _subscribe_globals(ws, ids)
                await _subscribe_orderbook_batches(ws, ids, cfg.tickers)

                queue: asyncio.Queue[tuple[float, str] | None] = asyncio.Queue(
                    maxsize=cfg.queue_maxsize
                )

                async def reader() -> None:
                    try:
                        async for raw in ws:
                            if stop_event.is_set():
                                break
                            metrics.messages_in += 1
                            try:
                                queue.put_nowait((time.time(), raw))
                            except asyncio.QueueFull:
                                metrics.messages_dropped_queue += 1
                            metrics.max_queue_size = max(metrics.max_queue_size, queue.qsize())
                    finally:
                        try:
                            queue.put_nowait(None)
                        except asyncio.QueueFull:
                            pass

                async def worker() -> None:
                    while True:
                        item = await queue.get()
                        if item is None:
                            break
                        received_at, raw = item
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(data, dict):
                            continue
                        lag = lag_ms_for_message(data, received_at=received_at)
                        await metrics.record_lag_ms(lag)
                        _process_ws_message(
                            data,
                            session_id=session_id,
                            watch=watch,
                            store=store,
                            books=books,
                            last_seq=last_seq,
                            metrics=metrics,
                            received_at=received_at,
                        )
                        if on_metrics:
                            on_metrics(metrics)

                await asyncio.gather(reader(), worker())
        except Exception as e:
            log.warning("WebSocket session ended: %s", e)
            if stop_event.is_set():
                break
            metrics.reconnect_count += 1
            await asyncio.sleep(min(cfg.reconnect_max_s, backoff))
            backoff = min(cfg.reconnect_max_s, backoff * 2)
            continue

    store.end_session(session_id=session_id)
    if on_metrics:
        on_metrics(metrics)
