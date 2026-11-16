"""Fetch the USD/KRW rate. Providers are tried in order with automatic fallback."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import requests

from fx_alert.rules import KST

log = logging.getLogger(__name__)
TIMEOUT = 10


@dataclass
class Quote:
    rate: float
    source: str
    as_of: str  # timestamp/date reported by the provider


class FetchError(RuntimeError):
    pass


def _get_json(url: str, **params) -> dict | list:
    resp = requests.get(url, params=params or None, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def parse_exim(data: list) -> Quote:
    for row in data or []:
        if str(row.get("cur_unit", "")).upper() == "USD":
            rate = float(str(row["deal_bas_r"]).replace(",", ""))
            return Quote(rate, "Export-Import Bank of Korea", "today's published rate")
    raise FetchError("No USD row in EXIM response (weekend/holiday, or before ~11:00 KST)")


def fetch_exim() -> Quote:
    key = os.getenv("EXIM_API_KEY")
    if not key:
        raise FetchError("EXIM_API_KEY not set, skipping")
    today = datetime.now(KST).strftime("%Y%m%d")
    data = _get_json(
        "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON",
        authkey=key,
        searchdate=today,
        data="AP01",
    )
    return parse_exim(data)


def parse_open_er_api(data: dict) -> Quote:
    if data.get("result") != "success":
        raise FetchError(f"open.er-api error: {data.get('error-type')}")
    return Quote(float(data["rates"]["KRW"]), "open.er-api.com", data.get("time_last_update_utc", ""))


def fetch_open_er_api() -> Quote:
    return parse_open_er_api(_get_json("https://open.er-api.com/v6/latest/USD"))


def parse_frankfurter(data: dict) -> Quote:
    return Quote(float(data["rates"]["KRW"]), "Frankfurter (ECB)", data.get("date", ""))


def fetch_frankfurter() -> Quote:
    return parse_frankfurter(
        _get_json("https://api.frankfurter.dev/v1/latest", base="USD", symbols="KRW")
    )


PROVIDERS: dict[str, Callable[[], Quote]] = {
    "exim": fetch_exim,
    "open_er_api": fetch_open_er_api,
    "frankfurter": fetch_frankfurter,
}


def fetch_rate(provider_names: list[str]) -> Quote:
    errors = []
    for name in provider_names:
        fn = PROVIDERS.get(name)
        if fn is None:
            errors.append(f"{name}: unknown provider")
            continue
        try:
            quote = fn()
            log.info("Fetched rate: %s = %.2f", quote.source, quote.rate)
            return quote
        except Exception as exc:  # noqa: BLE001 — catch everything so we can fall back
            log.warning("%s failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
    raise FetchError("All rate providers failed\n" + "\n".join(errors))
