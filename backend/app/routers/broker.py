"""Broker configuration endpoints: read status, update credentials."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..trading import broker_config
from ..trading.broker import using_paper_broker

router = APIRouter(prefix="/api/broker", tags=["broker"])


class AlpacaConfig(BaseModel):
    api_key: str
    api_secret: str
    paper: bool = True


class BrokerConfigRequest(BaseModel):
    broker: str  # "paper" | "alpaca"
    alpaca: AlpacaConfig | None = None


@router.get("/config")
def get_config():
    cfg = broker_config.load()
    is_paper_sim = using_paper_broker()
    # strip secrets from response
    safe = {"broker": cfg.get("broker", "paper")}
    if cfg.get("alpaca"):
        safe["alpaca"] = {
            "api_key_set": bool(cfg["alpaca"].get("api_key")),
            "paper": cfg["alpaca"].get("paper", True),
        }
    return {**safe, "activeLabel": "paper_sim" if is_paper_sim else cfg.get("broker", "paper")}


@router.post("/config")
def set_config(req: BrokerConfigRequest):
    if req.broker not in ("paper", "alpaca"):
        raise HTTPException(400, "broker must be 'paper' or 'alpaca'")
    cfg: dict = {"broker": req.broker}
    if req.broker == "alpaca":
        if not req.alpaca:
            raise HTTPException(400, "alpaca credentials required when broker='alpaca'")
        cfg["alpaca"] = req.alpaca.model_dump()
    broker_config.save(cfg)
    active_label = broker_config.apply_config(cfg)
    return {"ok": True, "activeLabel": active_label}


@router.post("/config/reset")
def reset_config():
    broker_config.save({"broker": "paper"})
    broker_config.apply_config({"broker": "paper"})
    return {"ok": True, "activeLabel": "paper"}
