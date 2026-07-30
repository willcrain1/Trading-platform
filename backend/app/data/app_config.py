"""App-level config for third-party API keys (stored in data/app_config.json)."""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "app_config.json"


def load() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save(cfg: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get_quiver_key() -> str:
    return load().get("quiver_api_key", "")


def set_quiver_key(key: str) -> None:
    cfg = load()
    cfg["quiver_api_key"] = key.strip()
    save(cfg)


def get_polygon_key() -> str:
    """Returns the Polygon.io / Massive API key (UI-set takes priority over env var)."""
    import os
    return (load().get("polygon_api_key")
            or os.environ.get("POLYGON_API_KEY", "")
            or os.environ.get("MASSIVE_API_KEY", ""))


def set_polygon_key(key: str) -> None:
    cfg = load()
    cfg["polygon_api_key"] = key.strip()
    save(cfg)


def delete_polygon_key() -> None:
    cfg = load()
    cfg.pop("polygon_api_key", None)
    save(cfg)


def get_analyst_model() -> str:
    """Returns the Claude model used for trade analysis (UI-set > env var > default)."""
    import os
    return (load().get("analyst_model")
            or os.environ.get("CLAUDE_ANALYST_MODEL", "claude-opus-4-8"))


def set_analyst_model(model: str) -> None:
    cfg = load()
    cfg["analyst_model"] = model.strip()
    save(cfg)
