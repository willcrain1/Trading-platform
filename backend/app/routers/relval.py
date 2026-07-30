from fastapi import APIRouter, HTTPException, Query

from ..analysis import relval
from ..data import client

router = APIRouter(prefix="/api/relval", tags=["relval"])


@router.get("/presets")
def presets():
    return {"pairs": relval.PRESET_PAIRS}


@router.get("")
def pair(a: str = Query(...), b: str = Query(...),
         period: str = Query("2y"), window: int = Query(60, ge=20, le=252)):
    try:
        return relval.analyze_pair(a, b, period=period, window=window)
    except client.TickerNotFound as e:
        raise HTTPException(404, f"ticker not found: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"data source error: {e}")
