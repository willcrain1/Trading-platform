"""Automatic exit checker: enforces each open trade plan's stop-loss,
take-profit, and time-stop.

Exits are mechanical and immediate — no analyst review. Risk-reducing sells
are never gated on anything; the plan's levels were already reasoned about at
entry, and this module just enforces them. Called every 30 minutes during
market hours by the scheduler, and at the start of every engine run.

Quotes are ~15-min delayed, so a breach fills at the delayed quote, not the
exact stop price — gaps through a level fill wherever the quote is.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import numpy as np

from ..data import client
from . import store
from .broker import OrderRejected, get_active_broker

log = logging.getLogger("paper.exits")


def _held_trading_days(opened_at: float) -> int:
    opened = datetime.fromtimestamp(opened_at).date()
    return int(np.busday_count(opened, date.today()))


def _breach(plan: dict, last: float) -> tuple[str, str] | None:
    """Return (reason, note) if an exit condition is met, else None.

    Short positions invert stop/target: stop triggers if price RISES above the
    stop level; target triggers if price FALLS below the take-profit level.
    Priority: stop first."""
    direction = plan.get("direction", "long")
    stop, target = plan["stop_loss"], plan["take_profit"]
    entry = plan["entry_price"]

    if direction == "short":
        if last >= stop:
            return "stop_loss", (
                f"short stop-loss breached: quote {last:.2f} >= stop {stop:.2f}"
                f" (shorted at {entry:.2f})"
            )
        if last <= target:
            return "take_profit", (
                f"short take-profit reached: quote {last:.2f} <= target {target:.2f}"
                f" (shorted at {entry:.2f})"
            )
    else:
        if last <= stop:
            return "stop_loss", (
                f"stop-loss breached: quote {last:.2f} <= stop {stop:.2f}"
                f" (entry {entry:.2f})"
            )
        if last >= target:
            return "take_profit", (
                f"take-profit reached: quote {last:.2f} >= target {target:.2f}"
                f" (entry {entry:.2f})"
            )

    held = _held_trading_days(plan["opened_at"])
    if held >= plan["max_hold_days"]:
        return "time_stop", (
            f"time-stop expired: held {held} trading days >= max {plan['max_hold_days']}"
            f" (quote {last:.2f}, entry {entry:.2f})"
        )
    return None


def check_exits(record_run: bool = True) -> dict:
    """Check every open plan; sell and close any that breached a level.

    Records a run row (kind='stops') only when something fired or errored,
    so the twice-per-hour no-op checks don't drown the run log.
    """
    exited, errors = [], []
    for plan in store.open_plans():
        symbol = plan["symbol"]
        try:
            last = client.get_quote(symbol)["last"]
        except Exception as e:
            errors.append(f"{symbol}: quote failed: {e}")
            continue
        if not last or last <= 0:
            errors.append(f"{symbol}: no usable quote")
            continue

        hit = _breach(plan, float(last))
        if hit is None:
            continue
        reason, note = hit

        direction = plan.get("direction", "long")
        portfolio_id = plan.get("portfolio_id") or store.BUCKET_TECHNICAL_SUSTAINED
        positions = store.get_positions(portfolio_id)
        held_long = next((p["qty"] for p in positions if p["symbol"] == symbol and p["qty"] > 0), 0.0)
        held_short = abs(next((p["qty"] for p in positions if p["symbol"] == symbol and p["qty"] < 0), 0.0))
        held = held_short if direction == "short" else held_long
        qty = min(plan["qty"], held)
        if qty <= 0:
            # No real position to sell — closing at the current quote would fabricate a
            # realized P&L for a trade that never happened. See close_plan_untracked().
            store.close_plan_untracked(plan["id"], reason="manual")
            errors.append(f"{symbol}: plan {plan['id']} open with no position — closed without order")
            continue

        exit_side = "cover" if direction == "short" else "sell"
        order_id = store.create_order(plan["instance_id"], symbol, exit_side, qty, "stops", note=note)
        try:
            # Bug fix: this previously hardcoded "sell" regardless of exit_side, which
            # meant a short position's cover exit (stop/target/time-stop) would hit the
            # broker's "sell" branch, find 0 long shares held, and raise OrderRejected —
            # short positions likely couldn't mechanically exit at all before this fix.
            fill = get_active_broker(portfolio_id).submit_market_order(order_id, symbol, exit_side, qty)
        except OrderRejected as e:
            store.set_order_status(order_id, "error", note=f"{note} — REJECTED: {e}")
            errors.append(f"{symbol}: exit rejected: {e}")
            continue
        store.close_plan(plan["id"], order_id, fill["fillPrice"], reason)
        exited.append({"symbol": symbol, "planId": plan["id"], "reason": reason,
                       "qty": qty, "fillPrice": fill["fillPrice"]})
        log.info("exit fired: %s %s @ %.2f (%s)", symbol, reason, fill["fillPrice"], note)

    if record_run and (exited or errors):
        run_id = store.start_run("stops")
        store.finish_run(run_id, proposed=len(exited), filled=len(exited),
                         vetoed=0, errors=errors)
    return {"exited": exited, "errors": errors}
