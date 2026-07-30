from fastapi import APIRouter, HTTPException

from ..analysis import macro

router = APIRouter(prefix="/api/macro", tags=["macro"])


@router.get("/snapshot")
def snapshot():
    snap = macro.snapshot()
    if not snap["assets"]:
        raise HTTPException(502, "no macro data available from data source")
    return snap
