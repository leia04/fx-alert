"""Build the alert message and deliver it (email, Telegram, Slack, console)."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import requests

from fx_alert.ai_briefing import Briefing
from fx_alert.fetcher import Quote
from fx_alert.impact import ImpactLine
from fx_alert.rules import Event

log = logging.getLogger(__name__)
TELEGRAM_LIMIT = 4000  # Telegram's hard limit is 4096 characters


def build_message(
    quote: Quote,
    events: list[Event],
    level_label: str,
    change_pct: float | None,
    impact: list[ImpactLine],
    baseline_label: str,
    briefing: Briefing | None,
) -> str:
    lines: list[str] = []
    for ev in events:
        lines.append(f"✅ {ev.title}" if ev.kind == "recover" else f"🚨 {ev.title}")
    lines.append("")
    lines.append(f"Current rate: {quote.rate:,.1f} KRW (level: {level_label})")
    if change_pct is not None:
        lines.append(f"24h change: {change_pct:+.2f}%")
    lines.append(f"Source: {quote.source} · as of {quote.as_of}")

    if impact:
        lines.append("")
        lines.append(f"💸 Your impact (vs. {baseline_label})")
        for item in impact:
            lines.append(
                f"- {item.name} ${item.usd:,.0f}: {item.krw_now:,.0f} KRW ({item.diff:+,.0f} KRW)"
            )

    if briefing:
        lines.append("")
        lines.append("🤖 AI briefing")
        lines.append(briefing.text)
        if briefing.sources:
            lines.append("")
            lines.append("Sources")
            for title, url in briefing.sources[:3]:
                lines.append(f"- {title}: {url}")

    lines.append("")
    lines.append("For information only. Not investment advice.")
    return "\n".join(lines).strip()


def send_email(subject: str, text: str) -> bool:
    """Send via SMTP. Defaults to Gmail (smtp.gmail.com:465) with an App Password."""
    user = os.getenv("SMTP_USER")
    password = (os.getenv("SMTP_PASSWORD") or "").replace(" ", "")  # Gmail shows it with spaces
    if not (user and password):
        return False
    host = os.getenv("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.getenv("SMTP_PORT") or 465)
    recipients = [a.strip() for a in (os.getenv("EMAIL_TO") or user).split(",") if a.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"FX Alert <{user}>"
    msg["To"] = ", ".join(recipients)
    msg.set_content(text)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as server:
            server.login(user, password)
            server.send_message(msg)
    else:  # e.g. 587 with STARTTLS (Outlook, Naver, etc.)
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
    return True


def send_telegram(text: str) -> bool:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text[:TELEGRAM_LIMIT], "disable_web_page_preview": True},
        timeout=10,
    )
    resp.raise_for_status()
    return True


def send_slack(text: str) -> bool:
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        return False
    resp = requests.post(url, json={"text": text}, timeout=10)
    resp.raise_for_status()
    return True


def notify(subject: str, text: str) -> list[str]:
    """Send to every configured channel and return the names that succeeded."""
    channels = (
        ("email", lambda: send_email(subject, text)),
        ("telegram", lambda: send_telegram(text)),
        ("slack", lambda: send_slack(text)),
    )
    sent = []
    for name, fn in channels:
        try:
            if fn():
                sent.append(name)
        except Exception as exc:  # noqa: BLE001
            log.error("%s delivery failed: %s", name, exc)
    if not sent:
        log.warning("No notification channel configured; printed to console only.")
    return sent
