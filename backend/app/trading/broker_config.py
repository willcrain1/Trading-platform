"""Broker configuration: persists to data/broker_config.json and activates
the correct broker on startup via apply_config().
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .broker import set_active_broker

log = logging.getLogger("broker.config")

CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "broker_config.json"

_DEFAULT: dict = {"broker": "paper"}


def load() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        return dict(_DEFAULT)
    except Exception as e:
        log.warning("broker_config: could not read %s (%s) — using defaults", CONFIG_PATH, e)
        return dict(_DEFAULT)


def save(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def apply_config(cfg: dict | None = None) -> str:
    """Instantiate and activate the configured broker. Returns the active broker name."""
    if cfg is None:
        cfg = load()

    broker_type = cfg.get("broker", "paper")

    if broker_type == "alpaca":
        alpaca_cfg = cfg.get("alpaca", {})
        api_key = alpaca_cfg.get("api_key", "")
        api_secret = alpaca_cfg.get("api_secret", "")
        paper_mode = alpaca_cfg.get("paper", True)
        if not api_key or not api_secret:
            log.warning("Alpaca configured but credentials missing — falling back to paper broker")
            set_active_broker(None)
            return "paper (no credentials)"
        try:
            from .alpaca_broker import AlpacaBroker
            broker = AlpacaBroker(api_key, api_secret, paper=paper_mode)
            set_active_broker(broker)
            log.info("Active broker: Alpaca (%s)", "paper" if paper_mode else "live")
            return f"alpaca_{'paper' if paper_mode else 'live'}"
        except Exception as e:
            log.error("Failed to initialize Alpaca broker (%s) — falling back to paper", e)
            set_active_broker(None)
            return f"paper (alpaca failed: {e})"

    set_active_broker(None)  # None → uses paper_broker fallback
    return "paper"
