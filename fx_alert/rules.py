"""Alert decision logic.

Everything here is a pure function (no network, no file I/O), so it is easy
to unit-test.

Key ideas
1. Level: 0 = Normal, 1..n = configured alert levels.
2. Hysteresis: escalate as soon as a threshold is crossed, but only
   de-escalate once the rate falls below (threshold - band). Bouncing around
   a threshold therefore never produces repeated alerts.
3. Spike: if the rate moved more than X% versus N hours ago, alert at most
   once per (KST) day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Level:
    name: str
    threshold: float


@dataclass
class Event:
    kind: str  # "escalate" | "recover" | "spike"
    title: str
    detail: str = ""


@dataclass
class Evaluation:
    level: int
    events: list[Event] = field(default_factory=list)
    change_pct: float | None = None
    reference_rate: float | None = None
    spike_date: str | None = None  # KST date a spike alert was sent (persisted in state)

    @property
    def should_alert(self) -> bool:
        return bool(self.events)


def level_name(levels: list[Level], idx: int) -> str:
    return "Normal" if idx <= 0 else levels[idx - 1].name


def raw_level(rate: float, levels: list[Level]) -> int:
    """Level from a plain comparison, without hysteresis."""
    return sum(1 for level in levels if rate >= level.threshold)


def next_level(rate: float, prev: int, levels: list[Level], hysteresis: float) -> int:
    prev = max(0, min(prev, len(levels)))  # stay safe if the config changed
    raw = raw_level(rate, levels)
    if raw >= prev:
        return raw
    lvl = prev
    while lvl > 0 and rate < levels[lvl - 1].threshold - hysteresis:
        lvl -= 1
    return lvl


def find_reference(history: list[dict], now: datetime, lookback_hours: float) -> dict | None:
    """Most recent record that is at least `lookback_hours` old."""
    target = now - timedelta(hours=lookback_hours)
    candidates = [h for h in history if datetime.fromisoformat(h["ts"]) <= target]
    return candidates[-1] if candidates else None


def evaluate(rate: float, now: datetime, state: dict, cfg: dict) -> Evaluation:
    levels: list[Level] = cfg["levels"]
    prev = int(state.get("level", 0))
    new = next_level(rate, prev, levels, float(cfg["hysteresis"]))
    result = Evaluation(level=new)

    if new > prev:
        crossed = levels[new - 1]
        result.events.append(
            Event(
                kind="escalate",
                title=f"[{crossed.name}] USD/KRW {rate:,.1f} — crossed {crossed.threshold:,.0f}",
                detail=f"{level_name(levels, prev)} -> {crossed.name}",
            )
        )
    elif new < prev and cfg.get("notify_on_recovery", True):
        result.events.append(
            Event(
                kind="recover",
                title=f"[Cleared] USD/KRW {rate:,.1f} — {level_name(levels, prev)} level cleared",
                detail=f"{level_name(levels, prev)} -> {level_name(levels, new)}",
            )
        )

    spike = cfg.get("spike", {})
    ref = find_reference(state.get("history", []), now, float(spike.get("lookback_hours", 24)))
    if ref:
        ref_rate = float(ref["rate"])
        pct = (rate - ref_rate) / ref_rate * 100
        result.change_pct = pct
        result.reference_rate = ref_rate
        today = now.astimezone(KST).date().isoformat()
        if (
            spike.get("enabled", True)
            and abs(pct) >= float(spike.get("pct", 1.0))
            and state.get("last_spike_date") != today
        ):
            direction = "Spike up" if pct > 0 else "Sharp drop"
            result.events.append(
                Event(
                    kind="spike",
                    title=f"[{direction}] USD/KRW {pct:+.2f}% ({ref_rate:,.1f} -> {rate:,.1f})",
                    detail=f"vs. {spike.get('lookback_hours', 24)} hours ago",
                )
            )
            result.spike_date = today

    return result
