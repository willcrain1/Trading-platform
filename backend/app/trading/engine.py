"""Strategy evaluation engine for paper trading.

For each enabled instance: compute the strategy's desired position (−1, 0, or 1)
from the latest daily bars, diff against current holdings, and propose orders.
Each proposed order is reviewed by the Claude analyst before execution.

Position semantics
──────────────────
  desired =  1 → go long (or stay long)
  desired =  0 → go flat (exit any open position)
  desired = −1 → go short (or stay short)

Transitions handled each run (two-step for reversals — e.g. long→short takes
two runs: sell on the first, short on the second):
  flat  → long : BUY
  flat  → short: SHORT
  long  → flat : SELL
  short → flat : COVER
  long  → short: SELL (SHORT fires next run once flat)
  short → long : COVER (BUY fires next run once flat)

Exits (stop/target/time) are enforced by trading.exits before new signals are
evaluated; a strategy signal flipping off closes the plan with 'signal_exit'.
"""
from __future__ import annotations

import math

import pandas as pd

from ..analysis import backtest
from ..analysis import indicators as ind
from ..data import client
from . import analyst, exits, store
from .broker import OrderRejected, get_active_broker

STOP_ATR_MULT = 2.0
TARGET_ATR_MULT = 3.0
DEFAULT_MAX_HOLD_DAYS = 60

# Equities size in whole shares; crypto sizes fractionally, since a single coin
# can be worth tens of thousands of dollars and most allocations buy a fraction
# of one. 6 decimals matches the precision store.py already uses for position
# qty (get_positions()).
_CRYPTO_QTY_DECIMALS = 6
_CRYPTO_MIN_QTY = 1e-6


def _round_qty(raw_qty: float, portfolio_id: str) -> float:
    if portfolio_id == store.BUCKET_CRYPTO:
        return round(raw_qty, _CRYPTO_QTY_DECIMALS)
    return float(math.floor(raw_qty))


def _min_qty(portfolio_id: str) -> float:
    return _CRYPTO_MIN_QTY if portfolio_id == store.BUCKET_CRYPTO else 1.0


def _desired_position(instance: dict) -> tuple[float, dict]:
    """Run the strategy on latest daily bars; return (desired, signal context).

    desired is −1, 0, or 1. Context carries ATR and signal data so the trade
    plan can be built without re-fetching."""
    spec = backtest.STRATEGIES[instance["strategy"]]
    params = {**spec["params"], **(instance["params"] or {})}
    hist = client.get_history(instance["symbol"], period="2y")
    df = ind.to_df(hist["candles"])
    if len(df) < 60:
        raise ValueError(f"not enough history for {instance['symbol']}")
    pos_series = spec["fn"](df, params)
    desired = float(pos_series.iloc[-1])
    atr14 = ind.atr(df).iloc[-1]
    context = {
        "strategyLabel": spec["label"],
        "params": params,
        "lastClose": round(float(df["close"].iloc[-1]), 4),
        "atr14": round(float(atr14), 4) if not pd.isna(atr14) else None,
        "maxHoldDaysDefault": spec.get("maxHoldDays", DEFAULT_MAX_HOLD_DAYS),
        "signals": ind.compute_signals(hist["candles"]),
        "dataStale": hist.get("stale", False),
    }
    return desired, context


def _held_qty(symbol: str, portfolio_id: str) -> float:
    """Signed qty within one portfolio: positive = long, negative = short, 0 = flat.
    Scoped so two portfolios independently holding the same symbol never see each
    other's position."""
    return next(
        (p["qty"] for p in get_active_broker(portfolio_id).get_positions() if p["symbol"] == symbol.upper()),
        0.0,
    )


def _mechanical_defaults(price: float, context: dict, direction: str) -> dict:
    """ATR-scaled exit levels. For shorts, stop is above entry and target below."""
    atr = context.get("atr14") or price * 0.02
    if direction == "short":
        return {
            "stopLoss": round(price + STOP_ATR_MULT * atr, 2),
            "takeProfit": round(price - TARGET_ATR_MULT * atr, 2),
            "maxHoldDays": int(context.get("maxHoldDaysDefault", DEFAULT_MAX_HOLD_DAYS)),
        }
    return {
        "stopLoss": round(price - STOP_ATR_MULT * atr, 2),
        "takeProfit": round(price + TARGET_ATR_MULT * atr, 2),
        "maxHoldDays": int(context.get("maxHoldDaysDefault", DEFAULT_MAX_HOLD_DAYS)),
    }


def _mechanical_thesis(symbol: str, context: dict, defaults: dict, direction: str) -> tuple[str, str]:
    """Written reasoning from the signal summary, works for long and short."""
    s = context.get("signals", {})
    verb = "short" if direction == "short" else "long"
    parts = [
        f"Mechanical entry: {context['strategyLabel']} signaled {verb} on {symbol}"
        f" at ~{context['lastClose']}.",
        f"Trend read: {s.get('trend', 'unknown')}"
        + (f" (recent {s['recentCross'].replace('_', ' ')})" if s.get("recentCross") else "")
        + f"; RSI(14) {s.get('rsi', 'n/a')} ({s.get('momentum', 'n/a')});"
        f" MACD {'above' if s.get('macdAboveSignal') else 'below'} signal;"
        f" ATR {s.get('atrPct', 'n/a')}% of price.",
    ]
    if s.get("levels"):
        lv = s["levels"][0]
        parts.append(f"Nearest {lv['kind']} level ~{lv['price']} ({lv['touches']} touches).")
    if direction == "short":
        parts.append(
            "Exit levels are volatility-scaled defaults for a short: stop 2×ATR above entry,"
            " target 3×ATR below, no analyst refinement applied."
        )
        exit_plan = (
            f"Cover automatically if price rises to the stop ({defaults['stopLoss']}), falls to"
            f" the target ({defaults['takeProfit']}), the position exceeds"
            f" {defaults['maxHoldDays']} trading days, or the {context['strategyLabel']} signal"
            " turns off — whichever comes first."
        )
    else:
        parts.append(
            "Exit levels are volatility-scaled defaults: stop 2×ATR below entry,"
            " target 3×ATR above, no analyst refinement applied."
        )
        exit_plan = (
            f"Sell automatically if price hits the stop ({defaults['stopLoss']}), reaches the"
            f" target ({defaults['takeProfit']}), the position exceeds {defaults['maxHoldDays']}"
            f" trading days, or the {context['strategyLabel']} signal turns off — whichever"
            " comes first."
        )
    return " ".join(parts), exit_plan


def _clamp_levels(verdict: dict, price: float, context: dict, defaults: dict,
                  direction: str) -> tuple[dict, list[str]]:
    """Sanity-clamp analyst exit levels; fall back to mechanical per-field."""
    atr = context.get("atr14") or price * 0.02
    notes: list[str] = []
    out = dict(defaults)

    stop = verdict.get("stop_loss")
    target = verdict.get("take_profit")

    if direction == "short":
        # Stop must be above entry; target must be below entry
        if isinstance(stop, (int, float)) and (price + 0.5 * atr) <= stop <= (price + 4 * atr):
            out["stopLoss"] = round(float(stop), 2)
        elif stop is not None:
            notes.append(f"analyst stop {stop} outside short sanity range — using {defaults['stopLoss']}")
        if isinstance(target, (int, float)) and float(target) < price:
            out["takeProfit"] = round(float(target), 2)
        elif target is not None:
            notes.append(f"analyst target {target} not below entry — using {defaults['takeProfit']}")
    else:
        # Stop must be below entry; target must be above entry
        if isinstance(stop, (int, float)) and (price - 4 * atr) <= stop <= (price - 0.5 * atr):
            out["stopLoss"] = round(float(stop), 2)
        elif stop is not None:
            notes.append(f"analyst stop {stop} outside sanity range — using {defaults['stopLoss']}")
        if isinstance(target, (int, float)) and float(target) > price:
            out["takeProfit"] = round(float(target), 2)
        elif target is not None:
            notes.append(f"analyst target {target} not above entry — using {defaults['takeProfit']}")

    hold = verdict.get("max_hold_days")
    if isinstance(hold, (int, float)) and 1 <= int(hold) <= 365:
        out["maxHoldDays"] = int(hold)
    elif hold is not None:
        notes.append(f"analyst max-hold {hold} outside 1-365 — using {defaults['maxHoldDays']}")

    return out, notes


def evaluate_instance(instance: dict, run_kind: str) -> dict:
    """Evaluate one instance; return a result dict for the run log."""
    symbol = instance["symbol"]
    portfolio_id = instance.get("portfolio_id") or store.BUCKET_TECHNICAL_SUSTAINED
    desired, context = _desired_position(instance)
    held = _held_qty(symbol, portfolio_id)  # positive=long, negative=short, 0=flat

    # ── determine action ──────────────────────────────────────────────────────
    if desired > 0 and held == 0:
        direction, side = "long", "buy"
    elif desired < 0 and held == 0:
        direction, side = "short", "short"
    elif held > 0 and desired <= 0:
        direction, side = "long", "sell"   # close long (flat or pending short)
    elif held < 0 and desired >= 0:
        direction, side = "short", "cover" # close short (flat or pending long)
    else:
        return {"symbol": symbol, "action": "hold", "desired": desired, "held": held}

    # ── size the order ────────────────────────────────────────────────────────
    if side in ("sell", "cover"):
        price, qty = None, abs(held)
    else:
        price = client.get_quote(symbol)["last"]
        if not price or price <= 0:
            raise ValueError(f"no quote for {symbol}")
        qty = _round_qty(instance["allocation_usd"] / price, portfolio_id)
        if qty < _min_qty(portfolio_id):
            raise ValueError(
                f"allocation ${instance['allocation_usd']:,.0f} buys less than {_min_qty(portfolio_id)} of {symbol}"
            )

    order_id = store.create_order(
        instance["id"], symbol, side, qty, run_kind,
        note=f"{context['strategyLabel']} signal ({direction})",
    )

    defaults = _mechanical_defaults(price, context, direction) if price else None

    verdict = analyst.review_order(
        order={"id": order_id, "symbol": symbol, "side": side, "qty": qty, "runKind": run_kind},
        instance=instance,
        signal_context=context,
        mechanical_defaults=defaults,
    )
    store.record_decision(
        order_id, verdict["verdict"], verdict["sizeFactor"], verdict["rationale"], verdict.get("model")
    )

    if verdict["verdict"] == "veto":
        store.set_order_status(order_id, "vetoed")
        return {"symbol": symbol, "action": "vetoed", "orderId": order_id}

    sized_qty = abs(held) if side in ("sell", "cover") else max(_round_qty(qty * verdict["sizeFactor"], portfolio_id), 0.0)
    if sized_qty < _min_qty(portfolio_id):
        store.set_order_status(order_id, "vetoed", note="sized to zero by analyst")
        return {"symbol": symbol, "action": "vetoed", "orderId": order_id}

    store.set_order_status(order_id, "approved", qty=float(sized_qty))
    try:
        fill = get_active_broker(portfolio_id).submit_market_order(order_id, symbol, side, float(sized_qty))
    except OrderRejected as e:
        store.set_order_status(order_id, "error", note=str(e))
        raise

    if side in ("buy", "short"):
        # Opening a new position — create trade plan
        has_analyst_levels = verdict.get("model") is not None and verdict.get("thesis")
        if has_analyst_levels:
            levels, clamp_notes = _clamp_levels(verdict, fill["fillPrice"], context, defaults, direction)
            thesis = verdict["thesis"]
            exit_plan = verdict.get("exit_plan") or _mechanical_thesis(symbol, context, levels, direction)[1]
            if clamp_notes:
                thesis += "\n\n[Level adjustments: " + "; ".join(clamp_notes) + "]"
            source = "analyst"
        else:
            levels = defaults
            thesis, exit_plan = _mechanical_thesis(symbol, context, levels, direction)
            if verdict.get("model") is not None:
                thesis += f"\n\n[Analyst approval rationale: {verdict['rationale']}]"
            source = "mechanical"
        plan_id = store.create_plan(
            instance["id"], symbol, order_id, float(sized_qty), fill["fillPrice"],
            levels["stopLoss"], levels["takeProfit"], levels["maxHoldDays"],
            exit_plan, thesis, source, direction=direction,
        )
        return {"symbol": symbol, "action": "filled", "orderId": order_id,
                "planId": plan_id, "direction": direction, **fill}

    # Closing a position (sell or cover). Scoped to this instance's own portfolio —
    # portfolios can independently hold the same symbol now, so a plan in a
    # different portfolio for the same symbol must never be touched here.
    for plan in store.open_plans():
        if plan["symbol"] == symbol.upper() and plan.get("portfolio_id") == portfolio_id:
            store.close_plan(plan["id"], order_id, fill["fillPrice"], "signal_exit")
    return {"symbol": symbol, "action": "filled", "orderId": order_id,
            "direction": direction, **fill}


def run_engine(run_kind: str, portfolio_ids: list[str] | None = None) -> dict:
    """Evaluate all enabled instances. run_kind: close | open | manual | crypto.

    Enforces stops/targets/time-stops on open plans first, then evaluates
    new signals. portfolio_ids optionally scopes the run to specific
    portfolios (used by the 24/7 crypto cadence so it doesn't redundantly
    re-evaluate equity instances on every tick) — None evaluates everything,
    unchanged from the original behavior."""
    run_id = store.start_run(run_kind)
    exit_result = exits.check_exits(record_run=False, portfolio_ids=portfolio_ids)
    proposed = filled = vetoed = 0
    results, errors = [], list(exit_result["errors"])
    for instance in store.list_instances():
        if not instance["enabled"]:
            continue
        if portfolio_ids is not None and instance.get("portfolio_id") not in portfolio_ids:
            continue
        try:
            res = evaluate_instance(instance, run_kind)
        except Exception as e:
            errors.append(f"{instance['symbol']}/{instance['strategy']}: {e}")
            continue
        results.append(res)
        if res["action"] in ("filled", "vetoed"):
            proposed += 1
        filled += res["action"] == "filled"
        vetoed += res["action"] == "vetoed"
    proposed += len(exit_result["exited"])
    filled += len(exit_result["exited"])

    # Mark every in-scope portfolio to market — cheap (a handful of portfolios), and
    # simpler and more robust than tracking exactly which ones this run touched.
    # Scoped too, so a crypto-cadence tick doesn't write redundant equity_snapshot
    # rows for portfolios it never touched.
    portfolios = store.list_portfolios()
    if portfolio_ids is not None:
        portfolios = [p for p in portfolios if p["id"] in portfolio_ids]
    portfolios_mtm: dict[str, dict] = {}
    for p in portfolios:
        mtm = get_active_broker(p["id"]).mark_to_market()
        portfolios_mtm[p["id"]] = {"equity": mtm["equity"], "cash": mtm["cash"]}

    store.finish_run(run_id, proposed, filled, vetoed, errors)
    return {
        "runId": run_id,
        "kind": run_kind,
        "results": results,
        "exits": exit_result["exited"],
        "errors": errors,
        "portfolios": portfolios_mtm,
        "equity": round(sum(v["equity"] for v in portfolios_mtm.values()), 2),
        "cash": round(sum(v["cash"] for v in portfolios_mtm.values()), 2),
    }
