"""Persist state between runs (current level, rate history) as a JSON file."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

EMPTY_STATE = {"level": 0, "last_rate": None, "last_spike_date": None, "history": []}


def load_state(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return json.loads(json.dumps(EMPTY_STATE))  # deep copy
    data = json.loads(p.read_text(encoding="utf-8"))
    return {**EMPTY_STATE, **data}


def save_state(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_history(state: dict, now: datetime, rate: float, source: str, limit: int) -> None:
    state.setdefault("history", []).append(
        {"ts": now.isoformat(timespec="seconds"), "rate": round(rate, 2), "source": source}
    )
    if len(state["history"]) > limit:
        state["history"] = state["history"][-limit:]
