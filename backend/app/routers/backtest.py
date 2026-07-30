from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..analysis import backtest
from ..data import client

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    strategy: str
    params: dict | None = None
    period: str = "5y"


@router.get("/strategies")
def strategies():
    return {
        "strategies": [
            {"id": key, "label": spec["label"], "defaultParams": spec["params"]}
            for key, spec in backtest.STRATEGIES.items()
        ]
    }


@router.post("")
def run(req: BacktestRequest):
    try:
        return backtest.run_backtest(req.symbol, req.strategy, req.params, req.period)
    except client.TickerNotFound:
        raise HTTPException(404, f"ticker '{req.symbol}' not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"data source error: {e}")


class BatchBacktestRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=30)
    strategies: list[str] | None = None
    period: str = "2y"


@router.post("/batch")
def batch(req: BatchBacktestRequest):
    tickers = [t.upper() for t in req.tickers[:30]]
    strategies = req.strategies or list(backtest.STRATEGIES.keys())
    period = req.period if req.period in {"1y", "2y", "3y", "5y"} else "2y"

    def run_one(symbol: str, strategy: str) -> dict:
        try:
            r = backtest.run_backtest(symbol, strategy, period=period)
            m = r["metrics"]
            return {
                "symbol": symbol, "strategy": strategy,
                "sharpe": m.get("sharpe"),
                "totalReturnPct": m.get("totalReturnPct"),
                "maxDrawdownPct": m.get("maxDrawdownPct"),
                "winRatePct": m.get("winRatePct"),
                "cagrPct": m.get("cagrPct"),
            }
        except Exception as e:
            return {"symbol": symbol, "strategy": strategy, "error": str(e)}

    tasks = [(t, s) for t in tickers for s in strategies]
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(run_one, t, s): (t, s) for t, s in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: (r.get("symbol", ""), r.get("strategy", "")))
    return {
        "results": results,
        "tickers": tickers,
        "strategies": strategies,
        "strategyLabels": {k: v["label"] for k, v in backtest.STRATEGIES.items()},
        "period": period,
    }
