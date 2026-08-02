"""Alpaca broker adapter — implements the Broker protocol using alpaca-py.

Supports both Alpaca Paper Trading (free, no real money) and Alpaca Live
Trading. Configure via broker_config.json or environment variables.

Why Alpaca instead of Fidelity/E*Trade:
- Fidelity has no public API for automated retail trading.
- E*Trade (Morgan Stanley self-directed) has an OAuth API but requires
  developer registration and is XML-heavy.
- Alpaca has a clean REST+WebSocket API, free paper trading, a good Python
  SDK (alpaca-py), and fractional share support.

Fills are recorded in paper.db so the trade journal and equity curve work
identically regardless of broker. The fill price comes from Alpaca's actual
fill notification, not the yfinance quote.
"""
from __future__ import annotations

import logging
import time

from . import store

log = logging.getLogger("broker.alpaca")

_ALPACA_IMPORT_ERROR: Exception | None = None
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
except ImportError as e:
    _ALPACA_IMPORT_ERROR = e
    TradingClient = None  # type: ignore[assignment,misc]


class AlpacaBrokerError(Exception):
    pass


class AlpacaBroker:
    """Live / paper broker backed by Alpaca's API.

    paper=True routes to Alpaca's paper trading environment (separate from live).
    All orders are market orders with day time-in-force, matching the paper
    simulator's behavior.
    """

    def __init__(self, api_key: str, api_secret: str, *, paper: bool = True) -> None:
        if _ALPACA_IMPORT_ERROR:
            raise AlpacaBrokerError(
                f"alpaca-py is not installed: {_ALPACA_IMPORT_ERROR}. "
                "Run: pip install alpaca-py"
            )
        self._client = TradingClient(api_key, api_secret, paper=paper)
        self._paper_mode = paper
        log.info("AlpacaBroker initialized (paper=%s)", paper)

    def get_positions(self) -> list[dict]:
        positions = self._client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avgCost": float(p.avg_entry_price),
            }
            for p in positions
            if float(p.qty) > 0
        ]

    def get_cash(self) -> float:
        acct = self._client.get_account()
        return float(acct.cash)

    def submit_market_order(self, order_id: int, symbol: str, side: str, qty: float) -> dict:
        alpaca_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=alpaca_side,
            time_in_force=TimeInForce.DAY,
        )
        try:
            order = self._client.submit_order(req)
        except Exception as e:
            raise AlpacaBrokerError(f"Alpaca order submission failed: {e}") from e

        # Poll until filled (paper orders fill nearly instantly)
        fill_price = self._wait_for_fill(str(order.id))
        store.record_fill(order_id, symbol, side, qty, fill_price)
        self.mark_to_market()
        return {"orderId": order_id, "fillPrice": fill_price, "qty": qty}

    def _wait_for_fill(self, alpaca_order_id: str, timeout: float = 30.0) -> float:
        deadline = time.time() + timeout
        while time.time() < deadline:
            o = self._client.get_order_by_id(alpaca_order_id)
            if o.status.value == "filled" and o.filled_avg_price is not None:
                return float(o.filled_avg_price)
            time.sleep(0.5)
        raise AlpacaBrokerError(f"order {alpaca_order_id} did not fill within {timeout}s")

    def mark_to_market(self) -> dict:
        acct = self._client.get_account()
        cash = float(acct.cash)
        equity = float(acct.portfolio_value)
        marked = []
        for p in self.get_positions():
            try:
                pos = self._client.get_open_position(p["symbol"])
                last = float(pos.current_price)
            except Exception:
                last = p["avgCost"]
            value = p["qty"] * last
            marked.append({
                **p,
                "last": last,
                "value": round(value, 2),
                "unrealizedPnl": round(value - p["qty"] * p["avgCost"], 2),
            })
        # Alpaca is a single real brokerage account, not split into per-strategy
        # portfolios — snapshot it under a fixed sentinel id.
        store.snapshot_equity("alpaca_live", round(equity, 2), round(cash, 2))
        return {"equity": round(equity, 2), "cash": round(cash, 2), "positions": marked}
