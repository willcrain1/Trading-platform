import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..analysis import backtest
from ..data import client as data_client
from ..trading import engine, scheduler, store
from ..trading.broker import get_active_broker, paper_broker

router = APIRouter(prefix="/api/paper", tags=["paper"])


class InstanceCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    strategy: str
    params: dict = {}
    allocationUsd: float = Field(gt=0, le=1_000_000)


class InstanceUpdate(BaseModel):
    enabled: bool | None = None
    allocationUsd: float | None = Field(default=None, gt=0, le=1_000_000)
    params: dict | None = None


class RunRequest(BaseModel):
    kind: str = "manual"


@router.get("/account")
def account():
    mtm = get_active_broker().mark_to_market()
    acc = store.get_account()
    return {
        **mtm,
        "startingCash": acc["starting_cash"],
        "totalPnl": round(mtm["equity"] - acc["starting_cash"], 2),
        "totalPnlPct": round((mtm["equity"] / acc["starting_cash"] - 1) * 100, 3),
        "schedulerJobs": scheduler.job_info(),
    }


@router.get("/equity")
def equity():
    return {"curve": store.equity_curve()}


@router.get("/orders")
def orders(limit: int = 100):
    return {"orders": store.list_orders(min(limit, 500))}


@router.get("/runs")
def runs():
    return {"runs": store.list_runs()}


@router.get("/auto-select-log")
def auto_select_log(limit: int = 50):
    """Journal of every auto-selector invocation (10am/4pm scheduled runs and manual
    triggers) — what was decided and why, including skipped or empty cycles."""
    return {"runs": store.list_auto_select_runs(min(limit, 200))}


@router.get("/instances")
def instances():
    return {"instances": store.list_instances()}


@router.post("/instances")
def create_instance(req: InstanceCreate):
    if req.strategy not in backtest.STRATEGIES:
        raise HTTPException(400, f"unknown strategy '{req.strategy}'")
    iid = store.create_instance(req.symbol, req.strategy, req.params, req.allocationUsd)
    return {"id": iid}


@router.patch("/instances/{instance_id}")
def update_instance(instance_id: int, req: InstanceUpdate):
    ok = store.update_instance(
        instance_id, enabled=req.enabled, allocation_usd=req.allocationUsd, params=req.params
    )
    if not ok:
        raise HTTPException(404, "instance not found or nothing to update")
    return {"ok": True}


@router.delete("/instances/{instance_id}")
def delete_instance(instance_id: int):
    if not store.delete_instance(instance_id):
        raise HTTPException(404, "instance not found")
    return {"ok": True}


@router.post("/run")
def run_now(req: RunRequest | None = None):
    kind = (req.kind if req else "manual") or "manual"
    if kind not in ("manual", "close", "open"):
        raise HTTPException(400, "kind must be manual, close, or open")
    return engine.run_engine(kind)


class AutoDeployRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    allocationUsd: float = Field(gt=0, le=1_000_000)
    period: str = "2y"


@router.post("/auto-deploy")
def auto_deploy(req: AutoDeployRequest):
    """Backtest all strategies for a ticker, pick the best Sharpe, create instance."""
    symbol = req.symbol.upper().strip()
    period = req.period if req.period in {"1y", "2y", "3y", "5y"} else "2y"

    def _try(strategy_id: str) -> dict:
        try:
            r = backtest.run_backtest(symbol, strategy_id, period=period)
            m = r["metrics"]
            return {
                "strategy": strategy_id,
                "label": backtest.STRATEGIES[strategy_id]["label"],
                "sharpe": m.get("sharpe"),
                "totalReturnPct": m.get("totalReturnPct"),
                "maxDrawdownPct": m.get("maxDrawdownPct"),
                "winRatePct": m.get("winRatePct"),
            }
        except Exception as e:
            return {"strategy": strategy_id, "error": str(e), "sharpe": None}

    all_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_try, sid): sid for sid in backtest.STRATEGIES}
        for fut in as_completed(futures):
            all_results.append(fut.result())
    all_results.sort(key=lambda r: r.get("strategy", ""))

    valid = [r for r in all_results if r.get("sharpe") is not None and r["sharpe"] > 0]
    if not valid:
        raise HTTPException(
            400,
            f"No strategy backtested positively on {symbol} ({period}). "
            "Try a longer period or review manually.",
        )
    best = max(valid, key=lambda r: r["sharpe"])

    # check cash headroom
    cash = get_active_broker().get_cash()
    if req.allocationUsd > cash:
        raise HTTPException(
            400,
            f"Insufficient cash: requested ${req.allocationUsd:,.0f}, have ${cash:,.0f}. "
            "Reduce allocation or add funds.",
        )

    iid = store.create_instance(symbol, best["strategy"], {}, req.allocationUsd)
    return {
        "instanceId": iid,
        "symbol": symbol,
        "strategy": best["strategy"],
        "strategyLabel": best.get("label", best["strategy"]),
        "sharpe": best["sharpe"],
        "totalReturnPct": best.get("totalReturnPct"),
        "winRatePct": best.get("winRatePct"),
        "allocationUsd": req.allocationUsd,
        "period": period,
        "allBacktests": all_results,
    }


@router.post("/health-check")
def health_check():
    """Re-backtest all enabled instances and flag any whose strategy has decayed."""
    instances = store.list_instances()
    results: list[dict] = []

    def _check(inst: dict) -> dict:
        try:
            r = backtest.run_backtest(inst["symbol"], inst["strategy"], period="2y")
            m = r["metrics"]
            sharpe = m.get("sharpe")
            if sharpe is None or sharpe < 0.2:
                rec = "disable"
            elif sharpe < 0.5:
                rec = "watch"
            else:
                rec = "keep"
            return {
                "instanceId": inst["id"],
                "symbol": inst["symbol"],
                "strategy": inst["strategy"],
                "enabled": inst["enabled"],
                "sharpe": sharpe,
                "totalReturnPct": m.get("totalReturnPct"),
                "maxDrawdownPct": m.get("maxDrawdownPct"),
                "recommendation": rec,
            }
        except Exception as e:
            return {
                "instanceId": inst["id"],
                "symbol": inst["symbol"],
                "strategy": inst["strategy"],
                "enabled": inst["enabled"],
                "error": str(e),
                "recommendation": "error",
            }

    active = [i for i in instances if i["enabled"]]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_check, i): i for i in active}
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: r.get("symbol", ""))
    flagged = [r for r in results if r.get("recommendation") in ("disable", "watch", "error")]
    return {"results": results, "flagged": len(flagged), "checked": len(results)}


@router.post("/reset")
def reset():
    """Wipe all paper trading data and restore the account to STARTING_CASH."""
    store.reset_account()
    return {"ok": True, "cash": store.STARTING_CASH}


@router.get("/plans")
def plans(status: str = "all", limit: int = 100):
    if status not in ("all", "open", "closed"):
        raise HTTPException(400, "status must be all, open, or closed")
    return {"plans": store.list_plans(status=status, limit=min(limit, 500))}


def _trade_stats(closed: list[dict]) -> dict:
    """Realized-trade performance stats for a set of closed plans."""
    total = len(closed)
    wins = [p for p in closed if (p.get("realized_pnl") or 0) > 0]
    losses = [p for p in closed if p.get("realized_pnl") is not None and p["realized_pnl"] <= 0]

    win_rate = len(wins) / total * 100 if total else None
    avg_win_pct = sum(p["realized_pnl_pct"] for p in wins) / len(wins) if wins else None
    avg_loss_pct = sum(p["realized_pnl_pct"] for p in losses) / len(losses) if losses else None
    total_win_amt = sum(p["realized_pnl"] for p in wins)
    total_loss_amt = abs(sum(p["realized_pnl"] for p in losses))
    profit_factor = total_win_amt / total_loss_amt if total_loss_amt > 0 else None
    total_realized_pnl = sum(p.get("realized_pnl") or 0 for p in closed)

    hold_days = [
        (p["closed_at"] - p["opened_at"]) / 86400
        for p in closed if p.get("opened_at") and p.get("closed_at")
    ]
    avg_hold = round(sum(hold_days) / len(hold_days), 1) if hold_days else None

    by_reason: dict = {}
    for p in closed:
        r = p.get("exit_reason") or "unknown"
        rec = by_reason.setdefault(r, {"count": 0, "pnl": 0.0, "wins": 0})
        rec["count"] += 1
        rec["pnl"] = round(rec["pnl"] + (p.get("realized_pnl") or 0), 2)
        if (p.get("realized_pnl") or 0) > 0:
            rec["wins"] += 1

    return {
        "totalClosedTrades": total,
        "totalRealizedPnl": round(total_realized_pnl, 2),
        "winRatePct": round(win_rate, 1) if win_rate is not None else None,
        "avgWinPct": round(avg_win_pct, 2) if avg_win_pct is not None else None,
        "avgLossPct": round(avg_loss_pct, 2) if avg_loss_pct is not None else None,
        "profitFactor": round(profit_factor, 2) if profit_factor is not None else None,
        "avgHoldDays": avg_hold,
        "byExitReason": by_reason,
    }


def _annualized_return(start_value: float, end_value: float, start_ts: float, end_ts: float) -> dict:
    """CAGR between two equity points. Falls back to plain total return if the span
    is under a month, where annualizing would wildly exaggerate a short-lived move."""
    years = (end_ts - start_ts) / (365.25 * 86400)
    if start_value <= 0 or years <= 0:
        return {"totalReturnPct": None, "annualizedReturnPct": None, "years": None}

    total_return_pct = round((end_value / start_value - 1) * 100, 2)
    annualized_pct = (
        round(((end_value / start_value) ** (1 / years) - 1) * 100, 2) if years >= (30 / 365.25) else None
    )
    return {
        "totalReturnPct": total_return_pct,
        "annualizedReturnPct": annualized_pct,
        "years": round(years, 2),
    }


def _bucket_performance(closed: list[dict]) -> dict:
    """Sharpe / max drawdown / CAGR for one bucket's closed trades, approximated by
    compounding each trade's realized P&L% in chronological (exit) order. Unlike the
    whole-account cash equity curve, this is decomposable by source, so Smart Buy and
    Technical/Sustained get their own numbers that actually differ from "All"."""
    trades = sorted(
        (p for p in closed if p.get("realized_pnl_pct") is not None and p.get("opened_at") and p.get("closed_at")),
        key=lambda p: p["closed_at"],
    )
    empty = {
        "sharpe": None, "maxDrawdownPct": None, "annualizedReturnPct": None,
        "totalReturnPct": None, "periodYears": None, "periodStartTs": None, "periodEndTs": None,
    }
    if len(trades) < 2:
        return empty

    equity = [1.0]
    for t in trades:
        equity.append(equity[-1] * (1 + t["realized_pnl_pct"] / 100))
    eq_series = pd.Series(equity)
    rets = eq_series.pct_change().dropna()

    start_ts, end_ts = trades[0]["opened_at"], trades[-1]["closed_at"]
    years = (end_ts - start_ts) / (365.25 * 86400)

    max_dd_pct = round(float((eq_series / eq_series.cummax() - 1).min() * 100), 2)

    sharpe = None
    if len(rets) >= 4 and rets.std() > 0 and years > 0:
        trades_per_year = len(trades) / years
        sharpe = round(float(rets.mean() / rets.std() * np.sqrt(trades_per_year)), 2)

    total_return_pct = round((equity[-1] - 1) * 100, 2)
    annualized_pct = round((equity[-1] ** (1 / years) - 1) * 100, 2) if years >= (30 / 365.25) else None

    return {
        "sharpe": sharpe,
        "maxDrawdownPct": max_dd_pct,
        "annualizedReturnPct": annualized_pct,
        "totalReturnPct": total_return_pct,
        "periodYears": round(years, 2),
        "periodStartTs": start_ts,
        "periodEndTs": end_ts,
    }


def _spy_benchmark(start_ts: float, end_ts: float) -> dict:
    """S&P 500 (SPY) return over the same window as a bucket's own trade period."""
    try:
        hist = data_client.get_history("SPY", period="max", interval="1d")
        candles = [c for c in hist["candles"] if c["close"] is not None]
        in_window = [c for c in candles if c["time"] >= start_ts]
        start_candle = in_window[0] if in_window else (candles[0] if candles else None)
        end_candle = candles[-1] if candles else None
        if not start_candle or not end_candle or start_candle is end_candle:
            return {"totalReturnPct": None, "annualizedReturnPct": None, "years": None}
        return _annualized_return(
            start_candle["close"], end_candle["close"], start_candle["time"], end_candle["time"]
        )
    except Exception:
        return {"totalReturnPct": None, "annualizedReturnPct": None, "years": None}


def _bucket_stats_with_performance(closed: list[dict], unrealized_pnl: float) -> dict:
    stats = _trade_stats(closed)
    stats["unrealizedPnl"] = round(unrealized_pnl, 2)

    perf = _bucket_performance(closed)
    stats["sharpe"] = perf["sharpe"]
    stats["maxDrawdownPct"] = perf["maxDrawdownPct"]
    stats["annualizedReturnPct"] = perf["annualizedReturnPct"]
    stats["totalReturnPct"] = perf["totalReturnPct"]
    stats["periodYears"] = perf["periodYears"]

    bench = {"annualizedReturnPct": None, "totalReturnPct": None}
    if perf["periodStartTs"] is not None and perf["periodEndTs"] is not None:
        bench = _spy_benchmark(perf["periodStartTs"], perf["periodEndTs"])
    stats["benchmarkAnnualizedReturnPct"] = bench["annualizedReturnPct"]
    stats["benchmarkTotalReturnPct"] = bench["totalReturnPct"]
    return stats


@router.get("/stats")
def paper_stats():
    """Realized-trade stats broken out by portfolio-balance bucket (all / Smart Buy /
    Technical+Sustained), plus unrealized P&L on currently open positions per bucket.

    Sharpe, max drawdown, annualized return, and the S&P 500 comparison are each
    computed from that bucket's own closed trades (see _bucket_performance), so they
    differ across All / Smart Buy / Technical+Sustained rather than being frozen at
    a single whole-account number.
    """
    all_closed = store.list_plans(status="closed", limit=500)

    by_bucket: dict[str, list[dict]] = {store.BUCKET_SMART_BUY: [], store.BUCKET_TECHNICAL_SUSTAINED: []}
    for p in all_closed:
        by_bucket[store.bucket_of_tags(p.get("source_tags"))].append(p)

    # Unrealized P&L on currently open positions, joined to their bucket via source_tags
    mtm = get_active_broker().mark_to_market()
    plan_by_symbol = {p["symbol"]: p for p in store.open_plans()}
    unrealized = {store.BUCKET_SMART_BUY: 0.0, store.BUCKET_TECHNICAL_SUSTAINED: 0.0}
    for pos in mtm["positions"]:
        plan = plan_by_symbol.get(pos["symbol"])
        bucket = store.bucket_of_tags(plan.get("source_tags")) if plan else store.BUCKET_TECHNICAL_SUSTAINED
        unrealized[bucket] += pos["unrealizedPnl"]

    return {
        "all": _bucket_stats_with_performance(all_closed, sum(unrealized.values())),
        "smartBuy": _bucket_stats_with_performance(
            by_bucket[store.BUCKET_SMART_BUY], unrealized[store.BUCKET_SMART_BUY]
        ),
        "technicalSustained": _bucket_stats_with_performance(
            by_bucket[store.BUCKET_TECHNICAL_SUSTAINED], unrealized[store.BUCKET_TECHNICAL_SUSTAINED]
        ),
    }
