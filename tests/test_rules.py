from datetime import datetime, timedelta

from fx_alert.rules import KST, Level, evaluate, next_level

LEVELS = [Level("Watch", 1380), Level("Warning", 1400), Level("Critical", 1450)]
CFG = {
    "levels": LEVELS,
    "hysteresis": 10,
    "notify_on_recovery": True,
    "spike": {"enabled": True, "pct": 1.0, "lookback_hours": 24},
}
NOW = datetime(2026, 11, 16, 10, 0, tzinfo=KST)


def state(level=0, history=None, last_spike_date=None):
    return {"level": level, "history": history or [], "last_spike_date": last_spike_date}


def test_crossing_up_triggers_escalation():
    result = evaluate(1401, NOW, state(level=1), CFG)
    assert result.level == 2
    assert [e.kind for e in result.events] == ["escalate"]
    assert "Warning" in result.events[0].title


def test_staying_above_does_not_repeat_alert():
    result = evaluate(1410, NOW, state(level=2), CFG)
    assert result.level == 2
    assert result.events == []


def test_hysteresis_blocks_flapping():
    # After crossing 1400, dipping to 1395 (inside the 1390-1400 band) keeps the level
    assert next_level(1395, 2, LEVELS, 10) == 2
    # It only clears once the rate falls below 1390
    assert next_level(1389, 2, LEVELS, 10) == 1


def test_recovery_event():
    result = evaluate(1360, NOW, state(level=2), CFG)
    assert result.level == 0
    assert [e.kind for e in result.events] == ["recover"]


def test_jump_multiple_levels_reports_highest():
    result = evaluate(1460, NOW, state(level=0), CFG)
    assert result.level == 3
    assert "Critical" in result.events[0].title


def test_spike_detection_once_per_day():
    history = [{"ts": (NOW - timedelta(hours=25)).isoformat(), "rate": 1330.0}]
    first = evaluate(1347, NOW, state(history=history), CFG)  # +1.28%
    assert any(e.kind == "spike" for e in first.events)
    assert first.spike_date == "2026-11-16"

    again = evaluate(1348, NOW, state(history=history, last_spike_date="2026-11-16"), CFG)
    assert not any(e.kind == "spike" for e in again.events)


def test_small_move_is_not_spike():
    history = [{"ts": (NOW - timedelta(hours=25)).isoformat(), "rate": 1330.0}]
    result = evaluate(1335, NOW, state(history=history), CFG)
    assert result.events == []
    assert round(result.change_pct, 2) == 0.38
