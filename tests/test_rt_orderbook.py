from __future__ import annotations

from kalshi_weather.rt_collector.orderbook import OrderBookState


def test_snapshot_and_delta() -> None:
    st = OrderBookState(market_ticker="X")
    st.apply_snapshot_msg(
        {
            "type": "orderbook_snapshot",
            "msg": {
                "market_ticker": "X",
                "yes_dollars_fp": [["0.5", "10"], ["0.6", "5"]],
                "no_dollars_fp": [["0.4", "2"]],
            },
        }
    )
    assert st.yes["0.5"] == 10.0
    st.apply_delta_msg(
        {
            "type": "orderbook_delta",
            "msg": {
                "market_ticker": "X",
                "side": "yes",
                "price_dollars": "0.5",
                "delta_fp": "-4",
            },
        }
    )
    assert st.yes["0.5"] == 6.0


def test_from_rest_shape() -> None:
    body = {
        "orderbook_fp": {
            "yes_dollars": [["0.1", "1"]],
            "no_dollars": [["0.2", "3"]],
        }
    }
    st = OrderBookState.from_rest_orderbook("T-1", body)
    assert st.market_ticker == "T-1"
    assert st.yes
