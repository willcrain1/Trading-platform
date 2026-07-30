"""Broker layer: paper simulator + swappable real-broker seam.

get_active_broker() is the single call site for all order execution. At startup
broker_config.py reads the config file and calls set_active_broker() to swap in
a real broker; until then, everything falls through to PaperBroker.
"""
from __future__ import annotations

from typing import Protocol

from ..data import client
from . import store


class Broker(Protocol):
    def get_positions(self) -> list[dict]: ...
    def get_cash(self) -> float: ...
    def submit_market_order(self, order_id: int, symbol: str, side: str, qty: float) -> dict: ...
    def mark_to_market(self) -> dict: ...


class OrderRejected(Exception):
    pass


_active_broker: "Broker | None" = None


def get_active_broker() -> "Broker":
    return _active_broker if _active_broker is not None else paper_broker


def set_active_broker(broker: "Broker | None") -> None:
    global _active_broker
    _active_broker = broker


class PaperBroker:
    """Fills instantly at the last delayed quote. No partial fills, no fees."""

    def get_positions(self) -> list[dict]:
        return store.get_positions()

    def get_cash(self) -> float:
        return float(store.get_account()["cash"])

    def submit_market_order(self, order_id: int, symbol: str, side: str, qty: float) -> dict:
        if qty <= 0:
            raise OrderRejected("quantity must be positive")
        price = client.get_quote(symbol)["last"]
        if price is None or price <= 0:
            raise OrderRejected(f"no quote available for {symbol}")
        cash = self.get_cash()
        positions = store.get_positions()

        if side == "buy":
            cost = qty * price
            if cost > cash + 1e-6:
                raise OrderRejected(f"insufficient cash: need ${cost:,.2f}, have ${cash:,.2f}")
            store.set_cash(cash - cost)

        elif side == "sell":
            held = next((p["qty"] for p in positions
                         if p["symbol"] == symbol.upper() and p["qty"] > 0), 0.0)
            if qty > held + 1e-6:
                raise OrderRejected(f"cannot sell {qty} {symbol}: only {held:.4f} held")
            store.set_cash(cash + qty * price)

        elif side == "short":
            # Receive short-sale proceeds; no cash headroom check in paper trading
            store.set_cash(cash + qty * price)

        elif side == "cover":
            held_short = abs(next((p["qty"] for p in positions
                                   if p["symbol"] == symbol.upper() and p["qty"] < 0), 0.0))
            if qty > held_short + 1e-6:
                raise OrderRejected(
                    f"cannot cover {qty} {symbol}: only {held_short:.4f} shares short"
                )
            store.set_cash(cash - qty * price)

        else:
            raise OrderRejected(f"unknown side '{side}'")

        store.record_fill(order_id, symbol, side, qty, price)
        self.mark_to_market()
        return {"orderId": order_id, "fillPrice": price, "qty": qty}

    def mark_to_market(self) -> dict:
        """Value all positions at latest quotes and snapshot equity."""
        cash = self.get_cash()
        equity = cash
        marked = []
        for p in store.get_positions():
            try:
                last = client.get_quote(p["symbol"])["last"]
            except Exception:
                last = p["avgCost"]  # fall back to cost basis if quote fails
            value = p["qty"] * (last or p["avgCost"])
            equity += value
            marked.append({**p, "last": last, "value": round(value, 2),
                           "unrealizedPnl": round(value - p["qty"] * p["avgCost"], 2)})
        store.snapshot_equity(round(equity, 2), round(cash, 2))
        return {"equity": round(equity, 2), "cash": round(cash, 2), "positions": marked}


paper_broker = PaperBroker()
