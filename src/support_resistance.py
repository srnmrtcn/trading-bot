from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class SwingPoint:
    open_time: datetime
    price: Decimal


def find_swing_points(klines: list, k: int = 3) -> tuple:
    swing_highs = []
    swing_lows = []
    n = len(klines)
    for i in range(k, n - k):
        neighborhood = klines[i - k:i] + klines[i + 1:i + k + 1]
        candle = klines[i]
        if all(candle["high"] > c["high"] for c in neighborhood):
            swing_highs.append(SwingPoint(open_time=candle["open_time"], price=candle["high"]))
        if all(candle["low"] < c["low"] for c in neighborhood):
            swing_lows.append(SwingPoint(open_time=candle["open_time"], price=candle["low"]))
    return swing_highs, swing_lows


def nearest_resistance(swing_highs: list, current_price: Decimal):
    candidates = [p.price for p in swing_highs if p.price > current_price]
    return min(candidates) if candidates else None


def nearest_support(swing_lows: list, current_price: Decimal):
    candidates = [p.price for p in swing_lows if p.price < current_price]
    return max(candidates) if candidates else None
