from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from kalshi_weather.execution.fill_sim import (
    orderbook_from_rest_body,
    simulate_post_only_entry,
    simulate_taker_fill,
)
from kalshi_weather.execution.models import (
    BatchExecutionResult,
    ExecutionEngineConfig,
    FillPreview,
    OrderExecutionResult,
    OrderIntent,
)
from kalshi_weather.execution.risk import (
    RiskContext,
    evaluate_intent,
    rolling_batch_violation,
)
from kalshi_weather.kalshi.client import KalshiClient, KalshiHttpError


def _f(x: Any) -> float:
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return 0.0


def make_client_order_id(prefix: str, intent: OrderIntent) -> str:
    payload = {
        "ticker": intent.ticker,
        "side": intent.side,
        "action": intent.action,
        "count_fp": intent.count_fp,
        "limit_price_dollars": intent.limit_price_dollars,
        "policy": intent.policy,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(raw.encode()).hexdigest()[:20]
    return f"{prefix}-{h}"


def intent_to_create_body(intent: OrderIntent) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ticker": intent.ticker,
        "side": intent.side,
        "action": intent.action,
        "type": "limit",
        "count_fp": intent.count_fp,
        "client_order_id": intent.client_order_id,
    }
    if intent.side == "yes":
        body["yes_price_dollars"] = intent.limit_price_dollars
    else:
        body["no_price_dollars"] = intent.limit_price_dollars

    if intent.policy == "post_only_gtc":
        body["post_only"] = True
        body["time_in_force"] = "good_till_canceled"
    else:
        body["post_only"] = False
        body["time_in_force"] = "immediate_or_cancel"

    if intent.order_group_id:
        body["order_group_id"] = intent.order_group_id
    return body


def batch_endpoint_unavailable(exc: KalshiHttpError) -> bool:
    if exc.status_code == 403:
        return True
    if exc.status_code in (404, 405):
        return True
    text = (exc.response_text or "").lower()
    if exc.status_code == 400 and "batch" in text:
        return True
    return False


def _extract_cid_from_row(row: dict[str, Any]) -> str | None:
    cid = row.get("client_order_id")
    if isinstance(cid, str) and cid:
        return cid
    order_obj = row.get("order")
    if isinstance(order_obj, dict):
        # Common shape from batch endpoint.
        cid2 = order_obj.get("client_order_id")
        if isinstance(cid2, str) and cid2:
            return cid2
        # Some wrappers can return {"order": {...}}.
        inner = order_obj.get("order")
        if isinstance(inner, dict):
            cid3 = inner.get("client_order_id")
            if isinstance(cid3, str) and cid3:
                return cid3
    return None


def _normalize_order_payload(resp: dict[str, Any]) -> dict[str, Any]:
    if isinstance(resp.get("order"), dict):
        return resp["order"]
    return resp


def _preview_for_intent(
    intent: OrderIntent,
    orderbooks_by_ticker: dict[str, dict[str, Any]],
) -> FillPreview | None:
    ob = orderbooks_by_ticker.get(intent.ticker)
    if not ob:
        return None
    book = orderbook_from_rest_body(intent.ticker, ob)
    cnt = abs(_f(intent.count_fp))
    if intent.policy == "post_only_gtc":
        return simulate_post_only_entry(limit_price=_f(intent.limit_price_dollars), side=intent.side)
    return simulate_taker_fill(
        book=book,
        side=intent.side,
        action=intent.action,
        contracts=cnt,
    )


class KalshiExecutionEngine:
    """Execution orchestration with risk, batching, and dry-run / shadow modes."""

    def __init__(self, client: KalshiClient, config: ExecutionEngineConfig | None = None) -> None:
        self._client = client
        self._config = config or ExecutionEngineConfig()
        self._batch_capable: bool | None = None

    @property
    def config(self) -> ExecutionEngineConfig:
        return self._config

    def execute_batch(
        self,
        intents: list[OrderIntent],
        *,
        portfolio: dict[str, Any],
        markets_by_ticker: dict[str, dict[str, Any]],
        orderbooks_by_ticker: dict[str, dict[str, Any]],
        recent_fills: list[dict[str, Any]],
        now_ts: float | None = None,
    ) -> BatchExecutionResult:
        cfg = self._config
        mode = cfg.mode
        now = now_ts if now_ts is not None else time.time()

        prepared: list[OrderIntent] = []
        for it in intents:
            cid = it.client_order_id or make_client_order_id(cfg.client_order_id_prefix, it)
            prepared.append(
                OrderIntent(
                    ticker=it.ticker,
                    side=it.side,
                    action=it.action,
                    count_fp=it.count_fp,
                    policy=it.policy,
                    limit_price_dollars=it.limit_price_dollars,
                    client_order_id=cid,
                    order_group_id=it.order_group_id,
                )
            )

        ctx = RiskContext(
            positions=portfolio,
            markets_by_ticker=markets_by_ticker,
            recent_fills=recent_fills,
            now_ts=now,
        )

        rolling_hit = rolling_batch_violation(prepared, limits=cfg.risk, ctx=ctx)

        og_id: str | None = None
        lim = cfg.risk.rolling_matched_contracts_15s
        if (
            mode == "live"
            and cfg.use_order_groups_for_rolling
            and lim is not None
            and lim > 0
            and not rolling_hit
        ):
            try:
                og = self._client.create_order_group(
                    {"contracts_limit": max(1, int(lim)), "subaccount": 0}
                )
                oid = og.get("order_group_id")
                if isinstance(oid, str):
                    og_id = oid
                    prepared = [
                        OrderIntent(
                            ticker=i.ticker,
                            side=i.side,
                            action=i.action,
                            count_fp=i.count_fp,
                            policy=i.policy,
                            limit_price_dollars=i.limit_price_dollars,
                            client_order_id=i.client_order_id,
                            order_group_id=og_id,
                        )
                        for i in prepared
                    ]
            except KalshiHttpError:
                og_id = None

        results: list[OrderExecutionResult] = []

        if rolling_hit:
            for it in prepared:
                fp = _preview_for_intent(it, orderbooks_by_ticker)
                results.append(
                    OrderExecutionResult(
                        intent=it,
                        client_order_id=it.client_order_id or "",
                        status="risk_rejected",
                        reasons=["rolling_matched_contract_ceiling"],
                        fill_preview=fp,
                    )
                )
            return BatchExecutionResult(
                results=results,
                used_batch_endpoint=False,
                batch_fallback=None,
                order_group_id=og_id,
                mode=mode,
            )

        cat_spent: dict[str, float] = {}
        evt_spent: dict[str, float] = {}
        for it in prepared:
            reasons = evaluate_intent(
                it,
                limits=cfg.risk,
                ctx=ctx,
                category_batch_spent_dollars=cat_spent,
                event_batch_spent_dollars=evt_spent,
            )
            fp = _preview_for_intent(it, orderbooks_by_ticker)
            if reasons:
                results.append(
                    OrderExecutionResult(
                        intent=it,
                        client_order_id=it.client_order_id or "",
                        status="risk_rejected",
                        reasons=reasons,
                        fill_preview=fp,
                    )
                )
            elif mode == "live":
                m = ctx.markets_by_ticker.get(it.ticker)
                if m:
                    c = str(m.get("category") or "")
                    if c:
                        cat_spent[c] = cat_spent.get(c, 0.0) + abs(_f(it.count_fp)) * _f(
                            it.limit_price_dollars
                        )
                    e = str(m.get("event_ticker") or "")
                    if e:
                        evt_spent[e] = evt_spent.get(e, 0.0) + abs(_f(it.count_fp)) * _f(
                            it.limit_price_dollars
                        )
                results.append(
                    OrderExecutionResult(
                        intent=it,
                        client_order_id=it.client_order_id or "",
                        status="pending",
                        fill_preview=fp,
                    )
                )
            else:
                m = ctx.markets_by_ticker.get(it.ticker)
                if m:
                    c = str(m.get("category") or "")
                    if c:
                        cat_spent[c] = cat_spent.get(c, 0.0) + abs(_f(it.count_fp)) * _f(
                            it.limit_price_dollars
                        )
                    e = str(m.get("event_ticker") or "")
                    if e:
                        evt_spent[e] = evt_spent.get(e, 0.0) + abs(_f(it.count_fp)) * _f(
                            it.limit_price_dollars
                        )
                body = intent_to_create_body(it)
                results.append(
                    OrderExecutionResult(
                        intent=it,
                        client_order_id=it.client_order_id or "",
                        status="skipped_read_only",
                        fill_preview=fp,
                        dry_run_body=body,
                    )
                )

        if mode != "live":
            return BatchExecutionResult(
                results=results,
                used_batch_endpoint=False,
                batch_fallback=None,
                order_group_id=og_id,
                mode=mode,
            )

        pending = [r for r in results if r.status == "pending"]
        if not pending:
            return BatchExecutionResult(
                results=results,
                used_batch_endpoint=False,
                batch_fallback=None,
                order_group_id=og_id,
                mode=mode,
            )

        bodies = [intent_to_create_body(r.intent) for r in pending]
        batch_fallback: str | None = None
        used_batch = False

        def submit_single(body: dict[str, Any]) -> dict[str, Any]:
            return self._client.create_order(body)

        def submit_many(bs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal used_batch, batch_fallback
            if len(bs) == 1:
                resp = submit_single(bs[0])
                return [
                    {
                        "order": _normalize_order_payload(resp),
                        "client_order_id": bs[0].get("client_order_id"),
                    }
                ]
            prefer = cfg.prefer_batch and (self._batch_capable is not False)
            if not prefer:
                out: list[dict[str, Any]] = []
                for b in bs:
                    resp = submit_single(b)
                    out.append(
                        {
                            "order": _normalize_order_payload(resp),
                            "client_order_id": b.get("client_order_id"),
                        }
                    )
                return out
            try:
                resp = self._client.create_orders_batched({"orders": bs})
                used_batch = True
                self._batch_capable = True
                raw = resp.get("orders")
                return raw if isinstance(raw, list) else []
            except KalshiHttpError as e:
                if batch_endpoint_unavailable(e):
                    self._batch_capable = False
                    batch_fallback = f"batch_unavailable:{e.status_code}"
                    out = []
                    for b in bs:
                        resp = submit_single(b)
                        out.append(
                            {
                                "order": _normalize_order_payload(resp),
                                "client_order_id": b.get("client_order_id"),
                            }
                        )
                    return out
                raise

        try:
            raw_results = submit_many(bodies)
        except KalshiHttpError as e:
            for r in pending:
                r.status = "error"
                r.error = str(e)
            return BatchExecutionResult(
                results=results,
                used_batch_endpoint=used_batch,
                batch_fallback=batch_fallback,
                order_group_id=og_id,
                mode=mode,
            )

        by_cid: dict[str, dict[str, Any]] = {}
        for x in raw_results:
            if not isinstance(x, dict):
                continue
            cid = _extract_cid_from_row(x)
            if cid:
                by_cid[cid] = x

        for r in pending:
            cid = r.intent.client_order_id
            row = by_cid.get(str(cid)) if cid else None
            if row is None and len(raw_results) == len(pending):
                # Last-resort positional correlation when API omits client_order_id fields.
                row = raw_results[pending.index(r)] if isinstance(raw_results[pending.index(r)], dict) else None
            if not row:
                r.status = "error"
                r.error = "missing_batch_response_row"
                continue
            err = row.get("error")
            if isinstance(err, dict):
                r.status = "error"
                r.error = str(err.get("message") or err)
                r.api_response = row
                continue
            if isinstance(err, str) and err:
                r.status = "error"
                r.error = err
                r.api_response = row
                continue
            ord_payload = row.get("order")
            if ord_payload is None:
                r.status = "error"
                r.error = "no_order_in_response"
                r.api_response = row
                continue
            r.status = "submitted"
            r.api_response = row

        return BatchExecutionResult(
            results=results,
            used_batch_endpoint=used_batch,
            batch_fallback=batch_fallback,
            order_group_id=og_id,
            mode=mode,
        )
