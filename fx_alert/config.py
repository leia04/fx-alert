"""Load config.yaml and fill in defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from fx_alert.rules import Level

DEFAULTS: dict[str, Any] = {
    "levels": [{"name": "Warning", "threshold": 1400}],
    "hysteresis": 10,
    "notify_on_recovery": True,
    "spike": {"enabled": True, "pct": 1.0, "lookback_hours": 24},
    "impact": {"baseline": "auto", "baseline_days": 30, "items": []},
    "ai": {
        "enabled": True,
        "model": "claude-sonnet-5-5",
        "max_searches": 3,
        "max_tokens": 1200,
        "language": "English",
    },
    "providers": ["exim", "open_er_api", "frankfurter"],
    "history_limit": 500,
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cfg = _merge(DEFAULTS, raw)
    levels = sorted(
        (Level(str(item["name"]), float(item["threshold"])) for item in cfg["levels"]),
        key=lambda level: level.threshold,
    )
    if not levels:
        raise ValueError("config.yaml must define at least one level.")
    cfg["levels"] = levels
    return cfg
