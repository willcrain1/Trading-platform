"""CFTC Commitments of Traders API.

Endpoints
─────────
GET  /api/cot/snapshot          – current crowding signals for all instruments
GET  /api/cot/history/{code}    – historical net position for one instrument
POST /api/cot/refresh           – trigger a fresh download from CFTC
GET  /api/cot/instruments       – list of tracked instruments
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..data import cot

router = APIRouter(prefix="/api/cot", tags=["cot"])


@router.get("/instruments")
def instruments():
    return {"instruments": [
        {"code": i["code"], "label": i["label"], "etf": i["etf"], "report": i.get("report", "tff")}
        for i in cot.ALL_INSTRUMENTS
    ]}


@router.get("/snapshot")
def snapshot():
    data = cot.get_snapshot()
    return {
        "instruments": data,
        "lastRefresh": cot.last_refresh(),
        "hasData": cot.has_data(),
    }


@router.get("/history/{code}")
def history(code: str, weeks: int = 156):
    code = code.upper()
    if code not in cot.CODE_SET:
        raise HTTPException(404, f"Unknown instrument code: {code}")
    rows = cot.get_history(code, weeks)
    return {"code": code, "weeks": len(rows), "history": rows}


@router.post("/refresh")
def refresh(background_tasks: BackgroundTasks, years_back: int = 3):
    """Trigger a CFTC download in the background. Returns immediately."""
    def _do():
        result = cot.refresh(years_back)
        import logging
        logging.getLogger("cot").info("Refresh complete: %s", result)

    background_tasks.add_task(_do)
    return {"status": "refresh started", "yearsBack": years_back}


@router.get("/debug")
def debug(year: int = 2024):
    """Inspect the raw TFF CFTC file to diagnose format issues."""
    return cot.debug_download(year)


@router.get("/debug-disagg")
def debug_disagg(year: int = 2024):
    """Inspect the raw Disaggregated CFTC file."""
    return cot.debug_disagg_download(year)


@router.get("/debug-disagg-current")
def debug_disagg_current():
    """Inspect the raw current-week Disaggregated file to diagnose format."""
    import requests as req, io, pandas as pd
    url = cot.DISAGG_CURRENT_URL
    try:
        resp = req.get(url, headers=cot.HEADERS, timeout=45)
        result = {
            "url": url,
            "status_code": resp.status_code,
            "content_type": resp.headers.get("Content-Type", ""),
            "content_length": len(resp.content),
            "first_500_bytes": resp.content[:500].decode("utf-8", errors="replace"),
        }
        for sep in (",", "|", "\t", ";"):
            try:
                df = pd.read_csv(io.BytesIO(resp.content), sep=sep, dtype=str,
                                 low_memory=False, nrows=2)
                df.columns = [c.strip() for c in df.columns]
                result["delimiter"] = repr(sep)
                result["column_count"] = len(df.columns)
                result["columns_first_10"] = list(df.columns)[:10]
                result["first_row_name_col"] = df.iloc[0, 0] if len(df) > 0 else None
                result["has_header"] = cot._NAME_COL in df.columns
                break
            except Exception as e:
                result.setdefault("parse_attempts", []).append(f"{sep!r}: {e}")
        return result
    except Exception as e:
        return {"url": url, "error": str(e)}


@router.get("/debug-names")
def debug_names():
    """Return all distinct market names currently stored in the DB (for name matching diagnostics)."""
    import sqlite3
    conn = cot._get_conn()
    rows = conn.execute(
        "SELECT DISTINCT code, COUNT(*) as weeks FROM cot_history GROUP BY code ORDER BY code"
    ).fetchall()
    return {"instruments_in_db": [{"code": r[0], "weeks": r[1]} for r in rows]}


@router.get("/debug-current")
def debug_current():
    """Inspect the raw current-week CFTC file."""
    import requests, io, pandas as pd
    url = cot.CURRENT_URL
    headers = cot.HEADERS
    try:
        resp = requests.get(url, headers=headers, timeout=45)
        result = {
            "url": url,
            "status_code": resp.status_code,
            "content_type": resp.headers.get("Content-Type", ""),
            "content_length": len(resp.content),
            "raw_preview": resp.content[:300].decode("utf-8", errors="replace"),
        }
        for sep in (",", "|", "\t", ";"):
            try:
                df = pd.read_csv(io.BytesIO(resp.content), sep=sep, dtype=str,
                                 low_memory=False, nrows=2)
                df.columns = [c.strip() for c in df.columns]
                result["delimiter"] = repr(sep)
                result["columns"] = list(df.columns)
                result["first_row"] = df.iloc[0].to_dict() if len(df) > 0 else {}
                break
            except Exception as e:
                result.setdefault("parse_attempts", []).append(f"{sep!r}: {e}")
        return result
    except Exception as e:
        return {"url": url, "error": str(e)}
