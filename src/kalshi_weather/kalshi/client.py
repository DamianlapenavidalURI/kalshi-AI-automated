from __future__ import annotations

import json as jsonlib
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Union
from urllib.parse import quote, urlencode

import certifi
import kalshi_python_sync
from kalshi_python_sync.exceptions import ApiException

from kalshi_weather.kalshi.auth import KalshiAuth
from kalshi_weather.kalshi.models import MarketMoneyFields, PortfolioBalanceFpDollars


class KalshiHttpError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

@dataclass(slots=True)
class KalshiClient:
    """HTTP client for Kalshi Trade API v2 (demo or prod base URL)."""

    base_url: str
    auth: KalshiAuth | None = None
    timeout_s: float = 30.0
    max_retries: int = 5
    backoff_initial_s: float = 0.5
    backoff_max_s: float = 30.0
    _sdk: kalshi_python_sync.KalshiClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        cfg = kalshi_python_sync.Configuration(host=self.base_url.rstrip("/"))
        verify_ssl_raw = (os.getenv("KALSHI_SSL_VERIFY") or "true").strip().lower()
        verify_ssl = verify_ssl_raw not in {"0", "false", "no", "off"}
        cfg.verify_ssl = verify_ssl
        if verify_ssl:
            # Use certifi trust store by default to avoid local OpenSSL CA issues.
            cfg.ssl_ca_cert = (os.getenv("KALSHI_SSL_CA_CERT") or certifi.where()).strip()
        self._sdk = kalshi_python_sync.KalshiClient(cfg)
        if self.auth:
            self._sdk.kalshi_auth = self.auth

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Union[dict[str, Any], list[tuple[str, str]], None] = None,
        json: dict[str, Any] | None = None,
        require_auth: bool = False,
    ) -> dict[str, Any]:
        method_u = method.upper()
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")

        url = self.base_url.rstrip("/") + path
        if params:
            if isinstance(params, dict):
                query = urlencode(params, doseq=True)
            else:
                query = urlencode(params, doseq=True)
            if query:
                url = f"{url}?{query}"
        headers: dict[str, str] = {"Accept": "application/json"}

        if require_auth and not self.auth:
            raise ValueError("This request requires auth but no KalshiAuth is configured.")

        last_exc: Exception | None = None
        for attempt in range(max(1, self.max_retries)):
            try:
                resp = self._sdk.call_api(
                    method_u,
                    url,
                    header_params=headers,
                    body=json,
                    _request_timeout=self.timeout_s,
                )
                resp.read()
            except ApiException as e:
                last_exc = e
                status = getattr(e, "status", None)
                last_exc = KalshiHttpError(
                    f"{method_u} {path} failed: {status} {str(e)[:500]}",
                    status_code=status if isinstance(status, int) else None,
                    response_text=str(e),
                )
                if status == 429 or (isinstance(status, int) and 500 <= status <= 599):
                    if attempt >= self.max_retries - 1:
                        raise last_exc
                    self._sleep_backoff(attempt)
                    continue
                raise last_exc from e
            except Exception as e:
                last_exc = e
                if attempt >= self.max_retries - 1:
                    raise KalshiHttpError(
                        f"{method_u} {path} transport error: {e}",
                        status_code=None,
                        response_text=None,
                    ) from e
                self._sleep_backoff(attempt)
                continue

            if resp.status == 429 or 500 <= resp.status <= 599:
                text = resp.data.decode("utf-8", errors="replace")
                last_exc = KalshiHttpError(
                    f"{method_u} {path} failed: {resp.status} {text[:500]}",
                    status_code=resp.status,
                    response_text=text,
                )
                if attempt >= self.max_retries - 1:
                    raise last_exc
                self._sleep_backoff(attempt)
                continue

            if not (200 <= resp.status <= 299):
                text = resp.data.decode("utf-8", errors="replace")
                raise KalshiHttpError(
                    f"{method_u} {path} failed: {resp.status} {text}",
                    status_code=resp.status,
                    response_text=text,
                )

            data = jsonlib.loads(resp.data.decode("utf-8"))
            if not isinstance(data, dict):
                raise KalshiHttpError(
                    f"Unexpected response type: {type(data)}",
                    status_code=resp.status,
                    response_text=resp.data.decode("utf-8", errors="replace"),
                )
            return data

        assert last_exc is not None
        raise last_exc

    def _sleep_backoff(self, attempt: int) -> None:
        exp = min(self.backoff_max_s, self.backoff_initial_s * (2**attempt))
        jitter = random.random() * 0.25
        time.sleep(exp + jitter)

    # ---------- Portfolio (authenticated) ----------
    def get_balance(self) -> dict[str, Any]:
        return self.request("GET", "/portfolio/balance", require_auth=True)

    def get_balance_fp_dollars(self) -> PortfolioBalanceFpDollars:
        return PortfolioBalanceFpDollars.from_balance_response(self.get_balance())

    def get_positions(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        ticker: str | None = None,
        event_ticker: str | None = None,
        count_filter: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if ticker:
            params["ticker"] = ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if count_filter:
            params["count_filter"] = count_filter
        return self.request("GET", "/portfolio/positions", params=params, require_auth=True)

    def get_fills(
        self,
        *,
        limit: int = 100,
        offset: int | None = None,
        cursor: str | None = None,
        ticker: str | None = None,
        order_id: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
        subaccount: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if offset is not None:
            params["offset"] = offset
        if cursor:
            params["cursor"] = cursor
        if ticker:
            params["ticker"] = ticker
        if order_id:
            params["order_id"] = order_id
        if min_ts is not None:
            params["min_ts"] = min_ts
        if max_ts is not None:
            params["max_ts"] = max_ts
        if subaccount is not None:
            params["subaccount"] = subaccount
        return self.request("GET", "/portfolio/fills", params=params, require_auth=True)

    # ---------- Markets / events (public or mixed) ----------
    def get_markets(
        self,
        *,
        limit: int = 200,
        cursor: str | None = None,
        status: str | None = None,
        event_ticker: str | None = None,
        series_ticker: str | None = None,
        tickers: str | None = None,
        min_close_ts: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        if tickers:
            params["tickers"] = tickers
        if min_close_ts is not None:
            params["min_close_ts"] = min_close_ts
        return self.request("GET", "/markets", params=params)

    def get_market(self, ticker: str) -> dict[str, Any]:
        enc = quote(ticker, safe="")
        return self.request("GET", f"/markets/{enc}")

    def get_market_money_fields(self, ticker: str) -> MarketMoneyFields:
        body = self.get_market(ticker)
        market = body.get("market")
        if not isinstance(market, dict):
            raise KalshiHttpError(f"get_market missing market dict for {ticker!r}")
        return MarketMoneyFields.from_market(market)

    def get_market_orderbook(self, ticker: str, *, depth: int | None = None) -> dict[str, Any]:
        enc = quote(ticker, safe="")
        params: dict[str, Any] = {}
        if depth is not None:
            params["depth"] = depth
        return self.request("GET", f"/markets/{enc}/orderbook", params=params)

    def get_market_trades(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        ticker: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if ticker:
            params["ticker"] = ticker
        if min_ts is not None:
            params["min_ts"] = min_ts
        if max_ts is not None:
            params["max_ts"] = max_ts
        return self.request("GET", "/markets/trades", params=params)

    def get_events(
        self,
        *,
        limit: int = 200,
        cursor: str | None = None,
        with_nested_markets: bool = False,
        with_milestones: bool = False,
        status: str | None = None,
        series_ticker: str | None = None,
        min_close_ts: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if with_nested_markets:
            params["with_nested_markets"] = True
        if with_milestones:
            params["with_milestones"] = True
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        if series_ticker:
            params["series_ticker"] = series_ticker
        if min_close_ts is not None:
            params["min_close_ts"] = min_close_ts
        return self.request("GET", "/events", params=params)

    def get_event(
        self,
        event_ticker: str,
        *,
        with_nested_markets: bool = True,
        with_milestones: bool = False,
    ) -> dict[str, Any]:
        enc = quote(event_ticker, safe="")
        ev_params: dict[str, Any] = {}
        if with_nested_markets:
            ev_params["with_nested_markets"] = True
        if with_milestones:
            ev_params["with_milestones"] = True
        return self.request("GET", f"/events/{enc}", params=ev_params)

    def get_event_metadata(self, event_ticker: str) -> dict[str, Any]:
        enc = quote(event_ticker, safe="")
        return self.request("GET", f"/events/{enc}/metadata")

    def get_milestones(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        category: str | None = None,
        competition: str | None = None,
        milestone_type: str | None = None,
        related_event_ticker: str | None = None,
        minimum_start_date: str | None = None,
        min_updated_ts: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if category:
            params["category"] = category
        if competition:
            params["competition"] = competition
        if milestone_type:
            params["type"] = milestone_type
        if related_event_ticker:
            params["related_event_ticker"] = related_event_ticker
        if minimum_start_date:
            params["minimum_start_date"] = minimum_start_date
        if min_updated_ts is not None:
            params["min_updated_ts"] = min_updated_ts
        return self.request("GET", "/milestones", params=params)

    def get_live_data_for_milestone(
        self,
        milestone_id: str,
        *,
        include_player_stats: bool = False,
    ) -> dict[str, Any]:
        enc = quote(milestone_id, safe="")
        params: dict[str, Any] = {}
        if include_player_stats:
            params["include_player_stats"] = True
        return self.request("GET", f"/live_data/milestone/{enc}", params=params)

    def get_game_stats_for_milestone(self, milestone_id: str) -> dict[str, Any]:
        enc = quote(milestone_id, safe="")
        return self.request("GET", f"/live_data/milestone/{enc}/game_stats")

    def get_live_data_batch(
        self,
        milestone_ids: list[str],
        *,
        include_player_stats: bool = False,
    ) -> dict[str, Any]:
        if not milestone_ids:
            return {}
        params: list[tuple[str, str]] = [("milestone_ids", mid) for mid in milestone_ids[:100]]
        if include_player_stats:
            params.append(("include_player_stats", "true"))
        return self.request("GET", "/live_data/batch", params=params)

    def get_filters_by_sport(self) -> dict[str, Any]:
        return self.request("GET", "/search/filters_by_sport")

    def get_series(
        self,
        *,
        limit: int = 200,
        cursor: str | None = None,
        category: str | None = None,
        tags: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if category:
            params["category"] = category
        if tags:
            params["tags"] = tags
        return self.request("GET", "/series", params=params)

    def create_order(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/portfolio/orders", json=body, require_auth=True)

    def create_orders_batched(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/portfolio/orders/batched", json=body, require_auth=True)

    def batch_cancel_orders(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("DELETE", "/portfolio/orders/batched", json=body, require_auth=True)

    def get_order_queue_positions(
        self,
        *,
        market_tickers: str | None = None,
        event_ticker: str | None = None,
        subaccount: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if market_tickers:
            params["market_tickers"] = market_tickers
        if event_ticker:
            params["event_ticker"] = event_ticker
        if subaccount is not None:
            params["subaccount"] = subaccount
        return self.request("GET", "/portfolio/orders/queue_positions", params=params, require_auth=True)

    def create_order_group(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/portfolio/order_groups/create", json=body, require_auth=True)

    def get_order(self, order_id: str) -> dict[str, Any]:
        enc = quote(order_id, safe="")
        return self.request("GET", f"/portfolio/orders/{enc}", require_auth=True)
