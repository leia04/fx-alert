"""Personal impact calculation. All numbers are computed here, never by the AI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class ImpactLine:
    name: str
    usd: float
    krw_now: float
    krw_base: float

    @property
    def diff(self) -> float:
        return self.krw_now - self.krw_base


def baseline_rate(impact_cfg: dict, history: list[dict], now: datetime) -> tuple[float | None, str]:
    base = impact_cfg.get("baseline", "auto")
    if base != "auto":
        return float(base), f"fixed baseline {float(base):,.1f}"
    days = int(impact_cfg.get("baseline_days", 30))
    since = now - timedelta(days=days)
    rates = [float(h["rate"]) for h in history if datetime.fromisoformat(h["ts"]) >= since]
    if not rates:
        return None, "not enough history"
    avg = sum(rates) / len(rates)
    return avg, f"{days}-day average {avg:,.1f}"


def compute_impact(rate: float, baseline: float | None, items: list[dict]) -> list[ImpactLine]:
    if baseline is None:
        return []
    return [
        ImpactLine(
            name=str(item["name"]),
            usd=float(item["usd"]),
            krw_now=float(item["usd"]) * rate,
            krw_base=float(item["usd"]) * baseline,
        )
        for item in items
    ]
