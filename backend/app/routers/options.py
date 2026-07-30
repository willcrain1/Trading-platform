from fastapi import APIRouter, HTTPException, Query

from ..analysis import gex
from ..data import client

router = APIRouter(prefix="/api/options", tags=["options"])


@router.get("/{symbol}/gex")
def gamma_exposure(symbol: str, expiration: str | None = Query(None)):
    try:
        return gex.compute_gex(symbol, expiration)
    except client.TickerNotFound as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"options data error: {e}")
