from datetime import datetime, timedelta
from decimal import Decimal

from src.support_resistance import find_swing_points, nearest_resistance, nearest_support


def _kline(hour, high, low):
    return {"open_time": datetime(2026, 1, 1) + timedelta(hours=hour), "high": Decimal(str(high)), "low": Decimal(str(low))}


def test_find_swing_points_detects_a_clear_peak_and_trough():
    # Index 5 is a clear local high (100), index 10 a clear local low (80).
    highs = [90, 91, 92, 93, 94, 100, 94, 93, 92, 91, 80, 91, 92, 93, 94]
    lows = [h - 10 for h in highs]
    lows[10] = 70  # deepen the trough at index 10 so it's a clear local low
    klines = [_kline(i, highs[i], lows[i]) for i in range(len(highs))]

    swing_highs, swing_lows = find_swing_points(klines, k=3)

    assert any(p.price == Decimal("100") for p in swing_highs)
    assert any(p.price == Decimal("70") for p in swing_lows)


def test_find_swing_points_empty_for_monotonic_series():
    klines = [_kline(i, 100 + i, 90 + i) for i in range(10)]
    swing_highs, swing_lows = find_swing_points(klines, k=3)
    assert swing_highs == []
    assert swing_lows == []


def test_nearest_resistance_returns_lowest_price_above_current():
    from src.support_resistance import SwingPoint
    points = [
        SwingPoint(open_time=datetime(2026, 1, 1), price=Decimal("120")),
        SwingPoint(open_time=datetime(2026, 1, 2), price=Decimal("110")),
        SwingPoint(open_time=datetime(2026, 1, 3), price=Decimal("95")),  # below current, excluded
    ]
    assert nearest_resistance(points, current_price=Decimal("100")) == Decimal("110")


def test_nearest_resistance_none_when_nothing_above():
    from src.support_resistance import SwingPoint
    points = [SwingPoint(open_time=datetime(2026, 1, 1), price=Decimal("90"))]
    assert nearest_resistance(points, current_price=Decimal("100")) is None


def test_nearest_support_returns_highest_price_below_current():
    from src.support_resistance import SwingPoint
    points = [
        SwingPoint(open_time=datetime(2026, 1, 1), price=Decimal("80")),
        SwingPoint(open_time=datetime(2026, 1, 2), price=Decimal("90")),
        SwingPoint(open_time=datetime(2026, 1, 3), price=Decimal("105")),  # above current, excluded
    ]
    assert nearest_support(points, current_price=Decimal("100")) == Decimal("90")


def test_nearest_support_none_when_nothing_below():
    from src.support_resistance import SwingPoint
    points = [SwingPoint(open_time=datetime(2026, 1, 1), price=Decimal("110"))]
    assert nearest_support(points, current_price=Decimal("100")) is None
