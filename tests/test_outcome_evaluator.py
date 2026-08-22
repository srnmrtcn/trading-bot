from datetime import datetime, timedelta
from decimal import Decimal

from src.outcome_evaluator import evaluate_outcome


def _kline(hour, high, low):
    return {"open_time": datetime(2026, 1, 1) + timedelta(hours=hour), "high": Decimal(str(high)), "low": Decimal(str(low))}


EXPIRES = datetime(2026, 1, 2)
NOW = datetime(2026, 1, 1, 12)


def test_long_hits_target():
    klines = [_kline(0, 105, 95), _kline(1, 112, 104)]  # candle 1 high >= 110 target
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, klines, NOW)
    assert result == ("hit_target", klines[1]["open_time"])


def test_long_hits_stop():
    klines = [_kline(0, 105, 95), _kline(1, 106, 88)]  # candle 1 low <= 90 stop
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, klines, NOW)
    assert result == ("hit_stop", klines[1]["open_time"])


def test_long_same_candle_hits_both_stop_wins():
    klines = [_kline(0, 112, 88)]  # high >= target AND low <= stop in one candle
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, klines, NOW)
    assert result == ("hit_stop", klines[0]["open_time"])


def test_long_neither_hit_and_not_expired_stays_pending():
    klines = [_kline(0, 105, 95)]
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, klines, NOW)
    assert result is None


def test_long_neither_hit_and_expired():
    klines = [_kline(0, 105, 95)]
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, klines, now=EXPIRES + timedelta(hours=1))
    assert result == ("expired", EXPIRES)


def test_short_hits_target():
    klines = [_kline(0, 95, 88)]  # low <= 90 target
    result = evaluate_outcome("short", Decimal("90"), Decimal("110"), EXPIRES, klines, NOW)
    assert result == ("hit_target", klines[0]["open_time"])


def test_short_hits_stop():
    klines = [_kline(0, 112, 100)]  # high >= 110 stop
    result = evaluate_outcome("short", Decimal("90"), Decimal("110"), EXPIRES, klines, NOW)
    assert result == ("hit_stop", klines[0]["open_time"])


def test_short_same_candle_hits_both_stop_wins():
    klines = [_kline(0, 112, 88)]  # high >= stop AND low <= target in one candle
    result = evaluate_outcome("short", Decimal("90"), Decimal("110"), EXPIRES, klines, NOW)
    assert result == ("hit_stop", klines[0]["open_time"])


def test_no_klines_and_not_expired_stays_pending():
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, [], NOW)
    assert result is None
