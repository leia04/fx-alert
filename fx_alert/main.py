"""Entry point: fetch -> evaluate -> (on alert) AI briefing -> notify -> save state."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

from fx_alert.ai_briefing import generate_briefing
from fx_alert.config import load_config
from fx_alert.fetcher import FetchError, Quote, fetch_rate
from fx_alert.impact import baseline_rate, compute_impact
from fx_alert.notifier import build_message, notify
from fx_alert.rules import KST, evaluate, level_name
from fx_alert.state import append_history, load_state, save_state

log = logging.getLogger("fx_alert")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Automated USD/KRW exchange-rate alerts")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--state", default="state/state.json")
    p.add_argument("--dry-run", action="store_true", help="print only; do not send or save state")
    p.add_argument("--force-rate", type=float, help="use this rate instead of the API (e.g. 1405)")
    p.add_argument("--no-ai", action="store_true", help="disable the AI briefing")
    return p.parse_args(argv)


def write_step_summary(text: str) -> None:
    """Show a summary on the GitHub Actions run page."""
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    state = load_state(args.state)
    now = datetime.now(KST)

    # 1) Fetch the rate
    if args.force_rate:
        quote = Quote(args.force_rate, "manual (--force-rate)", now.strftime("%Y-%m-%d %H:%M KST"))
    else:
        try:
            quote = fetch_rate(cfg["providers"])
        except FetchError as exc:
            log.error("%s", exc)
            write_step_summary(f"### ❌ Rate fetch failed\n```\n{exc}\n```")
            return 1

    # 2) Decide with deterministic rules (code, not AI)
    result = evaluate(quote.rate, now, state, cfg)
    label = level_name(cfg["levels"], result.level)
    log.info("Rate %.1f -> level: %s, %d event(s)", quote.rate, label, len(result.events))

    # 3) Personal impact (computed in code)
    base, base_label = baseline_rate(cfg["impact"], state.get("history", []), now)
    impact = compute_impact(quote.rate, base, cfg["impact"].get("items", []))

    message = None
    if result.should_alert:
        # 4) Call the AI only when there is something to alert on (keeps cost low)
        briefing = None
        if not args.no_ai:
            facts = {
                "pair": "USD/KRW",
                "rate": round(quote.rate, 1),
                "as_of": quote.as_of,
                "level": label,
                "events": [e.title for e in result.events],
                "change_pct_24h": None if result.change_pct is None else round(result.change_pct, 2),
                "impact": [
                    {"name": i.name, "usd": i.usd, "krw_diff_vs_baseline": round(i.diff)}
                    for i in impact
                ],
                "baseline": base_label,
            }
            briefing = generate_briefing(facts, cfg["ai"])

        message = build_message(
            quote, result.events, label, result.change_pct, impact, base_label, briefing
        )
        print("\n" + "=" * 50 + "\n" + message + "\n" + "=" * 50 + "\n")

        if args.dry_run:
            log.info("[dry-run] Not sending notifications.")
        else:
            subject = " / ".join(e.title for e in result.events)
            sent = notify(subject, message)
            log.info("Delivered via: %s", ", ".join(sent) or "none (console)")
    else:
        log.info("No alert condition — exiting quietly.")

    write_step_summary(
        f"### USD/KRW {quote.rate:,.1f} · level **{label}**\n"
        f"- Source: {quote.source} ({quote.as_of})\n"
        f"- Events: {', '.join(e.title for e in result.events) or 'none'}\n"
        + (f"\n<details><summary>Message sent</summary>\n\n```\n{message}\n```\n</details>\n" if message else "")
    )

    # 5) Save state (skipped for dry-run / forced rates so demos don't pollute history)
    if args.dry_run or args.force_rate:
        log.info("dry-run/force-rate mode: state not saved.")
        return 0
    state["level"] = result.level
    state["last_rate"] = quote.rate
    if result.spike_date:
        state["last_spike_date"] = result.spike_date
    append_history(state, now, quote.rate, quote.source, int(cfg["history_limit"]))
    save_state(args.state, state)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    return run(parse_args(argv))
