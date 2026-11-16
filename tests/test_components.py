from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from fx_alert.ai_briefing import Briefing, extract_briefing, parse_response
from fx_alert.fetcher import FetchError, Quote, parse_exim, parse_frankfurter, parse_open_er_api
from fx_alert.impact import baseline_rate, compute_impact
from fx_alert.notifier import build_message
from fx_alert.rules import KST, Event

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=KST)


# ── fetcher ────────────────────────────────────────────
def test_parse_exim():
    data = [{"cur_unit": "JPY(100)", "deal_bas_r": "900"}, {"cur_unit": "USD", "deal_bas_r": "1,402.5"}]
    assert parse_exim(data).rate == 1402.5


def test_parse_exim_empty_on_holiday():
    with pytest.raises(FetchError):
        parse_exim([])


def test_parse_open_er_api():
    q = parse_open_er_api({"result": "success", "rates": {"KRW": 1338.2}, "time_last_update_utc": "x"})
    assert q.rate == 1338.2


def test_parse_frankfurter():
    assert parse_frankfurter({"date": "2026-11-13", "rates": {"KRW": 1340.1}}).rate == 1340.1


# ── impact ─────────────────────────────────────────────
def test_baseline_auto_uses_recent_average():
    history = [
        {"ts": (NOW - timedelta(days=40)).isoformat(), "rate": 1200},  # outside the window
        {"ts": (NOW - timedelta(days=10)).isoformat(), "rate": 1320},
        {"ts": (NOW - timedelta(days=1)).isoformat(), "rate": 1340},
    ]
    base, label = baseline_rate({"baseline": "auto", "baseline_days": 30}, history, NOW)
    assert base == 1330
    assert "30-day" in label


def test_compute_impact():
    lines = compute_impact(1400, 1330, [{"name": "Transfer", "usd": 500}])
    assert lines[0].diff == pytest.approx(35_000)


# ── AI response parsing ────────────────────────────────
def test_extract_briefing_tag():
    assert extract_briefing("Searching...<briefing>\nDrivers\n- A\n</briefing>") == "Drivers\n- A"


def test_parse_response_collects_citations():
    cite = SimpleNamespace(url="https://news.example/a", title="Article A")
    content = [
        SimpleNamespace(type="server_tool_use"),
        SimpleNamespace(type="text", text="<briefing>Drivers\n- Strong dollar", citations=[cite]),
        SimpleNamespace(type="text", text="</briefing>", citations=None),
    ]
    b = parse_response(content)
    assert b.text == "Drivers\n- Strong dollar"
    assert b.sources == [("Article A", "https://news.example/a")]


# ── message ────────────────────────────────────────────
def test_build_message_contains_key_parts():
    msg = build_message(
        Quote(1402.0, "test", "2026-11-16"),
        [Event("escalate", "[Warning] USD/KRW 1,402.0 — crossed 1,400")],
        "Warning",
        1.2,
        compute_impact(1402, 1330, [{"name": "Transfer", "usd": 500}]),
        "30-day average 1,330.0",
        Briefing("Drivers\n- Strong dollar", [("Article", "https://x")]),
    )
    assert "crossed 1,400" in msg
    assert "+36,000 KRW" in msg
    assert "AI briefing" in msg
    assert "Not investment advice" in msg


# ── email ──────────────────────────────────────────────
class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.login_args = (user, password)
        assert " " not in password

    def send_message(self, msg):
        FakeSMTP.sent.append((self.host, self.port, msg))


def test_send_email_gmail_defaults(monkeypatch):
    from fx_alert import notifier

    FakeSMTP.sent.clear()
    monkeypatch.setattr(notifier.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("SMTP_USER", "me@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.delenv("EMAIL_TO", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)

    assert notifier.send_email("[Warning] crossed 1,400", "body") is True
    host, port, msg = FakeSMTP.sent[0]
    assert (host, port) == ("smtp.gmail.com", 465)
    assert msg["To"] == "me@gmail.com"
    assert msg["Subject"] == "[Warning] crossed 1,400"


def test_send_email_skipped_without_credentials(monkeypatch):
    from fx_alert import notifier

    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    assert notifier.send_email("s", "b") is False
