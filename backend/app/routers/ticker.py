from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..analysis import elliott, indicators
from ..data import app_config, client

router = APIRouter(prefix="/api/ticker", tags=["ticker"])

VALID_PERIODS = {"3mo", "6mo", "1y", "2y", "5y", "10y", "max"}


ANALYST_MODELS = [
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]


class PolygonKeyRequest(BaseModel):
    api_key: str


class AnalystModelRequest(BaseModel):
    model: str


@router.get("/data-config")
def get_data_config():
    """Return current data-source and analyst configuration."""
    return {
        "polygonKeySet":  bool(app_config.get_polygon_key()),
        "successorMap":   client.SUCCESSOR_MAP,
        "analystModel":   app_config.get_analyst_model(),
        "analystModels":  ANALYST_MODELS,
    }


@router.post("/data-config")
def set_data_config(req: PolygonKeyRequest):
    """Save the Polygon.io / Massive API key."""
    app_config.set_polygon_key(req.api_key)
    return {"ok": True, "polygonKeySet": True}


@router.delete("/data-config")
def delete_data_config():
    """Remove the Polygon / Massive API key."""
    app_config.delete_polygon_key()
    return {"ok": True, "polygonKeySet": False}


@router.post("/analyst-config")
def set_analyst_config(req: AnalystModelRequest):
    """Save the Claude model used for trade review."""
    if req.model not in ANALYST_MODELS:
        raise HTTPException(400, f"model must be one of {ANALYST_MODELS}")
    app_config.set_analyst_model(req.model)
    return {"ok": True, "analystModel": req.model}


@router.get("/names")
def batch_names(symbols: str = Query(..., description="Comma-separated list of ticker symbols")):
    """Return {symbol: name} for each symbol. Names are cached 24h."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:50]
    result: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(client.get_name, s): s for s in syms}
        for fut in as_completed(futures):
            sym = futures[fut]
            result[sym] = fut.result()
    return {"names": result}


def _history_or_404(symbol: str, period: str, interval: str = "1d") -> dict:
    try:
        return client.get_history(symbol, period=period, interval=interval)
    except client.TickerNotFound:
        raise HTTPException(404, f"ticker '{symbol}' not found")
    except Exception as e:
        raise HTTPException(502, f"data source error: {e}")


@router.get("/{symbol}/candles")
def candles(symbol: str, period: str = Query("1y")):
    if period not in VALID_PERIODS:
        raise HTTPException(400, f"period must be one of {sorted(VALID_PERIODS)}")
    return _history_or_404(symbol, period)


@router.get("/{symbol}/indicators")
def ticker_indicators(symbol: str, period: str = Query("1y")):
    if period not in VALID_PERIODS:
        raise HTTPException(400, f"period must be one of {sorted(VALID_PERIODS)}")
    hist = _history_or_404(symbol, period)
    return {
        "symbol": hist["symbol"],
        "stale": hist.get("stale", False),
        **indicators.compute_indicators(hist["candles"]),
    }


@router.get("/{symbol}/vwap")
def ticker_vwap(symbol: str, days: int = Query(5, ge=1, le=30)):
    hist = _history_or_404(symbol, f"{days}d", interval="5m")
    return {
        "symbol": hist["symbol"],
        "stale": hist.get("stale", False),
        "candles": hist["candles"],
        **indicators.compute_vwap(hist["candles"]),
    }


@router.get("/{symbol}/signals")
def ticker_signals(symbol: str, period: str = Query("2y")):
    if period not in VALID_PERIODS:
        raise HTTPException(400, f"period must be one of {sorted(VALID_PERIODS)}")
    hist = _history_or_404(symbol, period)
    return {
        "symbol": hist["symbol"],
        "stale": hist.get("stale", False),
        **indicators.compute_signals(hist["candles"]),
    }


@router.get("/{symbol}/elliott-wave")
def ticker_elliott_wave(symbol: str, period: str = Query("2y"),
                        atrMult: float = Query(elliott.DEFAULT_ATR_MULT, gt=0, le=10)):
    """Best-effort Elliott Wave count — see analysis/elliott.py's module
    docstring for the subjectivity caveat this always carries."""
    if period not in VALID_PERIODS:
        raise HTTPException(400, f"period must be one of {sorted(VALID_PERIODS)}")
    hist = _history_or_404(symbol, period)
    return {
        "symbol": hist["symbol"],
        "stale": hist.get("stale", False),
        **elliott.compute_elliott_wave(hist["candles"], atrMult),
    }
