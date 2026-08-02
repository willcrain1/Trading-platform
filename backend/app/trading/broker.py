"""Broker layer: paper simulator + swappable real-broker seam.

get_active_broker(portfolio_id) is the single call site for all order execution.
Each strategy portfolio gets its own PaperBroker instance with its own cash/positions/
equity curve (see store.py's portfolios table) — a candidate from one portfolio can
never affect another's capital. A real broker (Alpaca), if configured via
broker_config.py, stays a single global account regardless of portfolio_id — a real
brokerage account doesn't naturally split into virtual sub-ledgers, so multi-portfolio
support is paper-trading only.
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


def get_active_broker(portfolio_id: str) -> "Broker":
    return _active_broker if _active_broker is not None else get_paper_broker(portfolio_id)


def using_paper_broker() -> bool:
    """Whether the active broker is the paper simulator (vs. a configured real
    broker like Alpaca) — independent of any specific portfolio, since that's a
    global config choice, not a per-portfolio one."""
    return _active_broker is None


def set_active_broker(broker: "Broker | None") -> None:
    global _active_broker
    _active_broker = broker


class PaperBroker:
    """Fills instantly at the last delayed quote. No partial fills, no fees.
    Scoped to one portfolio — every read/write filters to self.portfolio_id."""

    def __init__(self, portfolio_id: str) -> None:
        self.portfolio_id = portfolio_id

    def get_positions(self) -> list[dict]:
        return store.get_positions(self.portfolio_id)

    def get_cash(self) -> float:
        return float(store.get_portfolio(self.portfolio_id)["cash"])

    def submit_market_order(self, order_id: int, symbol: str, side: str, qty: float) -> dict:
        if qty <= 0:
            raise OrderRejected("quantity must be positive")
        price = client.get_quote(symbol)["last"]
        if price is None or price <= 0:
            raise OrderRejected(f"no quote available for {symbol}")
        positions = store.get_positions(self.portfolio_id)

        # Cash reads and writes go through store.adjust_cash(), a single atomic
        # read-modify-write — see its docstring for why a get-then-set two-step
        # was a lost-update race between concurrent orders.
        if side == "buy":
            cost = qty * price
            try:
                store.adjust_cash(-cost, self.portfolio_id, min_cash=0.0)
            except ValueError:
                cash = self.get_cash()
                raise OrderRejected(f"insufficient cash: need ${cost:,.2f}, have ${cash:,.2f}")

        elif side == "sell":
            held = next((p["qty"] for p in positions
                         if p["symbol"] == symbol.upper() and p["qty"] > 0), 0.0)
            if qty > held + 1e-6:
                raise OrderRejected(f"cannot sell {qty} {symbol}: only {held:.4f} held")
            store.adjust_cash(qty * price, self.portfolio_id)

        elif side == "short":
            # Receive short-sale proceeds; no cash headroom check in paper trading
            store.adjust_cash(qty * price, self.portfolio_id)

        elif side == "cover":
            held_short = abs(next((p["qty"] for p in positions
                                   if p["symbol"] == symbol.upper() and p["qty"] < 0), 0.0))
            if qty > held_short + 1e-6:
                raise OrderRejected(
                    f"cannot cover {qty} {symbol}: only {held_short:.4f} shares short"
                )
            store.adjust_cash(-qty * price, self.portfolio_id)

        else:
            raise OrderRejected(f"unknown side '{side}'")

        store.record_fill(order_id, symbol, side, qty, price)
        self.mark_to_market()
        return {"orderId": order_id, "fillPrice": price, "qty": qty}

    def mark_to_market(self) -> dict:
        """Value this portfolio's positions at latest quotes and snapshot its equity."""
        cash = self.get_cash()
        equity = cash
        marked = []
        for p in store.get_positions(self.portfolio_id):
            try:
                last = client.get_quote(p["symbol"])["last"]
            except Exception:
                last = p["avgCost"]  # fall back to cost basis if quote fails
            value = p["qty"] * (last or p["avgCost"])
            equity += value
            marked.append({**p, "last": last, "value": round(value, 2),
                           "unrealizedPnl": round(value - p["qty"] * p["avgCost"], 2)})
        store.snapshot_equity(self.portfolio_id, round(equity, 2), round(cash, 2))
        return {"equity": round(equity, 2), "cash": round(cash, 2), "positions": marked}


_paper_brokers: dict[str, PaperBroker] = {}


def get_paper_broker(portfolio_id: str) -> PaperBroker:
    """Lazily create and cache one PaperBroker per portfolio id."""
    if portfolio_id not in _paper_brokers:
        _paper_brokers[portfolio_id] = PaperBroker(portfolio_id)
    return _paper_brokers[portfolio_id]
