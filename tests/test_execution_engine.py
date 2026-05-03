from __future__ import annotations

import time
from unittest.mock import patch

from kalshi_weather.execution.engine import KalshiExecutionEngine, make_client_order_id
from kalshi_weather.execution.fill_sim import orderbook_from_rest_body, simulate_taker_fill
from kalshi_weather.execution.models import ExecutionEngineConfig, OrderIntent, RiskLimits
from kalshi_weather.execution.risk import matched_contracts_in_window
from kalshi_weather.kalshi.client import KalshiClient, KalshiHttpError


def _market(ticker: str, *, category: str = "Sports", event: str = "EVT-1") -> dict:
    return {
        "ticker": ticker,
        "category": category,
        "event_ticker": event,
        "status": "active",
        "yes_bid_size_fp": "5",
        "yes_ask_size_fp": "5",
        "no_bid_size_fp": "5",
        "no_ask_size_fp": "5",
        "title": "NBA game",
        "rules_primary": "yes if team wins",
    }


def _ob() -> dict:
    return {
        "orderbook": {
            "yes_dollars": [["0.50", "10"], ["0.48", "5"]],
            "no_dollars": [["0.45", "20"]],
        }
    }


def test_make_client_order_id_idempotent() -> None:
    a = OrderIntent(
        ticker="M-1",
        side="yes",
        action="buy",
        count_fp="2.00",
        policy="taker_ioc",
        limit_price_dollars="0.55",
    )
    b = OrderIntent(
        ticker="M-1",
        side="yes",
        action="buy",
        count_fp="2.00",
        policy="taker_ioc",
        limit_price_dollars="0.55",
    )
    assert make_client_order_id("p", a) == make_client_order_id("p", b)


def test_simulate_taker_buy_yes() -> None:
    book = orderbook_from_rest_body("M-1", _ob())
    prev = simulate_taker_fill(book=book, side="yes", action="buy", contracts=3.0)
    assert prev.filled_contracts == 3.0
    assert prev.vwap_dollars is not None
    assert prev.fully_filled


def test_dry_run_no_posts() -> None:
    client = KalshiClient(base_url="https://demo-api.kalshi.co/trade-api/v2", auth=None)
    eng = KalshiExecutionEngine(
        client,
        config=ExecutionEngineConfig(mode="dry_run", risk=RiskLimits(per_market_max_contracts=100.0)),
    )
    with patch.object(KalshiClient, "request") as m:
        out = eng.execute_batch(
            [
                OrderIntent(
                    ticker="M-1",
                    side="yes",
                    action="buy",
                    count_fp="1.00",
                    policy="taker_ioc",
                    limit_price_dollars="0.60",
                )
            ],
            portfolio={"market_positions": [], "event_positions": []},
            markets_by_ticker={"M-1": _market("M-1")},
            orderbooks_by_ticker={"M-1": _ob()},
            recent_fills=[],
            now_ts=time.time(),
        )
    m.assert_not_called()
    assert out.mode == "dry_run"
    assert out.results[0].status == "skipped_read_only"
    assert out.results[0].dry_run_body is not None
    assert out.results[0].dry_run_body["ticker"] == "M-1"


def test_live_batch_fallback_to_single() -> None:
    client = KalshiClient(base_url="https://demo-api.kalshi.co/trade-api/v2", auth=None)
    eng = KalshiExecutionEngine(
        client,
        config=ExecutionEngineConfig(
            mode="live",
            prefer_batch=True,
            risk=RiskLimits(per_market_max_contracts=100.0, rolling_matched_contracts_15s=None),
            use_order_groups_for_rolling=False,
        ),
    )
    calls: list[tuple[str, str]] = []

    def fake_request(inst: KalshiClient, method: str, path: str, **kwargs: object) -> dict:
        calls.append((method, path))
        if method == "POST" and path == "/portfolio/orders/batched":
            raise KalshiHttpError("forbidden", status_code=403, response_text="no batch")
        if method == "POST" and path == "/portfolio/orders":
            body = kwargs.get("json") or {}
            cid = body.get("client_order_id")
            return {
                "order": {
                    "order_id": "o1",
                    "client_order_id": cid,
                    "ticker": body.get("ticker"),
                }
            }
        return {}

    with patch.object(KalshiClient, "request", fake_request):
        out = eng.execute_batch(
            [
                OrderIntent(
                    ticker="M-1",
                    side="yes",
                    action="buy",
                    count_fp="1.00",
                    policy="taker_ioc",
                    limit_price_dollars="0.60",
                ),
                OrderIntent(
                    ticker="M-1",
                    side="yes",
                    action="buy",
                    count_fp="2.00",
                    policy="taker_ioc",
                    limit_price_dollars="0.61",
                ),
            ],
            portfolio={"market_positions": [], "event_positions": []},
            markets_by_ticker={"M-1": _market("M-1")},
            orderbooks_by_ticker={"M-1": _ob()},
            recent_fills=[],
            now_ts=time.time(),
        )

    assert out.batch_fallback is not None
    assert out.used_batch_endpoint is False
    posts = [c for c in calls if c[0] == "POST" and c[1] == "/portfolio/orders"]
    assert len(posts) == 2
    assert all(r.status == "submitted" for r in out.results)


def test_live_single_submit_4xx_is_exchange_rejected() -> None:
    client = KalshiClient(base_url="https://demo-api.kalshi.co/trade-api/v2", auth=None)
    eng = KalshiExecutionEngine(
        client,
        config=ExecutionEngineConfig(
            mode="live",
            prefer_batch=False,
            risk=RiskLimits(per_market_max_contracts=100.0, rolling_matched_contracts_15s=None),
            use_order_groups_for_rolling=False,
        ),
    )

    def fake_request(inst: KalshiClient, method: str, path: str, **kwargs: object) -> dict:
        if method == "POST" and path == "/portfolio/orders":
            raise KalshiHttpError(
                "bad request",
                status_code=400,
                response_text='{"error":"market is not open"}',
            )
        return {}

    with patch.object(KalshiClient, "request", fake_request):
        out = eng.execute_batch(
            [
                OrderIntent(
                    ticker="M-1",
                    side="yes",
                    action="buy",
                    count_fp="1.00",
                    policy="taker_ioc",
                    limit_price_dollars="0.60",
                )
            ],
            portfolio={"market_positions": [], "event_positions": []},
            markets_by_ticker={"M-1": _market("M-1")},
            orderbooks_by_ticker={"M-1": _ob()},
            recent_fills=[],
            now_ts=time.time(),
        )

    assert out.results[0].status == "exchange_rejected"
    assert out.results[0].reasons == ["market_not_open"]
    assert out.results[0].error is None


def test_live_single_retry_on_ioc_4xx_price_then_submit() -> None:
    client = KalshiClient(base_url="https://demo-api.kalshi.co/trade-api/v2", auth=None)
    eng = KalshiExecutionEngine(
        client,
        config=ExecutionEngineConfig(
            mode="live",
            prefer_batch=False,
            risk=RiskLimits(per_market_max_contracts=100.0, rolling_matched_contracts_15s=None),
            use_order_groups_for_rolling=False,
        ),
    )
    calls: list[tuple[str, str, dict]] = []

    def fake_request(inst: KalshiClient, method: str, path: str, **kwargs: object) -> dict:
        body = kwargs.get("json")
        calls.append((method, path, body if isinstance(body, dict) else {}))
        if method == "POST" and path == "/portfolio/orders":
            if len([c for c in calls if c[0] == "POST" and c[1] == "/portfolio/orders"]) == 1:
                raise KalshiHttpError(
                    "bad request",
                    status_code=400,
                    response_text='{"message":"invalid price"}',
                )
            return {
                "order": {
                    "order_id": "o2",
                    "client_order_id": (body or {}).get("client_order_id"),
                    "ticker": (body or {}).get("ticker"),
                }
            }
        if method == "GET" and path.startswith("/markets/"):
            return {"market": {"yes_ask_dollars": "0.1700", "no_ask_dollars": "0.8300"}}
        return {}

    with patch.object(KalshiClient, "request", fake_request):
        out = eng.execute_batch(
            [
                OrderIntent(
                    ticker="M-1",
                    side="yes",
                    action="buy",
                    count_fp="1.00",
                    policy="taker_ioc",
                    limit_price_dollars="0.15",
                )
            ],
            portfolio={"market_positions": [], "event_positions": []},
            markets_by_ticker={"M-1": _market("M-1")},
            orderbooks_by_ticker={"M-1": _ob()},
            recent_fills=[],
            now_ts=time.time(),
        )

    posts = [c for c in calls if c[0] == "POST" and c[1] == "/portfolio/orders"]
    assert len(posts) == 2
    # Retry should switch from IOC to GTC and refresh limit.
    assert posts[1][2].get("time_in_force") == "good_till_canceled"
    assert posts[1][2].get("post_only") is False
    assert posts[1][2].get("yes_price_dollars") == "0.1700"
    assert out.results[0].status == "submitted"


def test_risk_per_market() -> None:
    client = KalshiClient(base_url="https://demo-api.kalshi.co/trade-api/v2", auth=None)
    eng = KalshiExecutionEngine(
        client,
        config=ExecutionEngineConfig(
            mode="dry_run",
            risk=RiskLimits(per_market_max_contracts=1.0),
        ),
    )
    out = eng.execute_batch(
        [
            OrderIntent(
                ticker="M-1",
                side="yes",
                action="buy",
                count_fp="5.00",
                policy="taker_ioc",
                limit_price_dollars="0.60",
            )
        ],
        portfolio={
            "market_positions": [
                {
                    "ticker": "M-1",
                    "position_fp": "0.00",
                    "market_exposure_dollars": "0.00",
                }
            ],
            "event_positions": [],
        },
        markets_by_ticker={"M-1": _market("M-1")},
        orderbooks_by_ticker={"M-1": _ob()},
        recent_fills=[],
        now_ts=time.time(),
    )
    assert out.results[0].status == "risk_rejected"
    assert "per_market_max_contracts" in out.results[0].reasons


def test_scalar_combo_blocked() -> None:
    client = KalshiClient(base_url="https://demo-api.kalshi.co/trade-api/v2", auth=None)
    eng = KalshiExecutionEngine(client, config=ExecutionEngineConfig(mode="dry_run"))
    m = _market("M-1")
    m["rules_primary"] = "multivariate settlement rules"
    out = eng.execute_batch(
        [
            OrderIntent(
                ticker="M-1",
                side="yes",
                action="buy",
                count_fp="1.00",
                policy="taker_ioc",
                limit_price_dollars="0.60",
            )
        ],
        portfolio={"market_positions": [], "event_positions": []},
        markets_by_ticker={"M-1": m},
        orderbooks_by_ticker={"M-1": _ob()},
        recent_fills=[],
        now_ts=time.time(),
    )
    assert "scalar_or_combo_blocked" in out.results[0].reasons


def test_rolling_window_sum() -> None:
    now = 1_000_000.0
    fills = [
        {"count_fp": "3.0", "ts": int(now - 5)},
        {"count_fp": "2.0", "ts": int(now - 20)},
    ]
    assert matched_contracts_in_window(fills, now_ts=now, window_s=15.0) == 3.0


def test_batch_category_running_total() -> None:
    client = KalshiClient(base_url="https://demo-api.kalshi.co/trade-api/v2", auth=None)
    eng = KalshiExecutionEngine(
        client,
        config=ExecutionEngineConfig(
            mode="dry_run",
            risk=RiskLimits(per_category_max_exposure_dollars=50.0),
        ),
    )
    out = eng.execute_batch(
        [
            OrderIntent(
                ticker="M-1",
                side="yes",
                action="buy",
                count_fp="10.00",
                policy="taker_ioc",
                limit_price_dollars="0.50",
            ),
            OrderIntent(
                ticker="M-2",
                side="yes",
                action="buy",
                count_fp="10.00",
                policy="taker_ioc",
                limit_price_dollars="0.60",
            ),
        ],
        portfolio={
            "market_positions": [
                {
                    "ticker": "M-0",
                    "position_fp": "0.00",
                    "market_exposure_dollars": "40.00",
                }
            ],
            "event_positions": [],
        },
        markets_by_ticker={
            "M-0": _market("M-0", category="Sports"),
            "M-1": _market("M-1", category="Sports"),
            "M-2": _market("M-2", category="Sports"),
        },
        orderbooks_by_ticker={"M-1": _ob(), "M-2": _ob()},
        recent_fills=[],
        now_ts=time.time(),
    )
    assert out.results[0].status == "skipped_read_only"
    assert out.results[1].status == "risk_rejected"
    assert "per_category_max_exposure" in out.results[1].reasons


def test_dry_run_mode_is_read_only() -> None:
    client = KalshiClient(base_url="https://demo-api.kalshi.co/trade-api/v2", auth=None)
    eng = KalshiExecutionEngine(
        client,
        config=ExecutionEngineConfig(mode="dry_run", risk=RiskLimits(per_market_max_contracts=50.0)),
    )
    with patch.object(KalshiClient, "request") as m:
        out = eng.execute_batch(
            [
                OrderIntent(
                    ticker="M-1",
                    side="yes",
                    action="buy",
                    count_fp="1.00",
                    policy="post_only_gtc",
                    limit_price_dollars="0.50",
                )
            ],
            portfolio={"market_positions": [], "event_positions": []},
            markets_by_ticker={"M-1": _market("M-1")},
            orderbooks_by_ticker={"M-1": _ob()},
            recent_fills=[],
            now_ts=time.time(),
        )
    m.assert_not_called()
    assert out.mode == "dry_run"
    assert out.results[0].status == "skipped_read_only"
