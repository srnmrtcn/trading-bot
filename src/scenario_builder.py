from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.indicators import average_true_range
from src.scenario_signal import RSI_OVERBOUGHT, RSI_OVERSOLD, SignalResult
from src.support_resistance import find_swing_points, nearest_resistance, nearest_support

MIN_EXPIRY_HOURS = 6
MAX_EXPIRY_HOURS = 168
ATR_PERIOD = 20
SWING_LOOKBACK_K = 3
RISK_REWARD_CAP = Decimal("3")


@dataclass
class ScenarioDraft:
    symbol: str
    direction: str
    entry_price: Decimal
    target_price: Decimal
    stop_price: Decimal
    expected_return_pct: Decimal
    confidence_score: Decimal
    created_at: datetime
    expires_at: datetime


def build_scenario(symbol: str, signal: SignalResult, klines: list, now: datetime):
    swing_highs, swing_lows = find_swing_points(klines, k=SWING_LOOKBACK_K)
    entry = signal.entry_price

    if signal.direction == "long":
        target = nearest_resistance(swing_highs, entry)
        stop = nearest_support(swing_lows, entry)
    else:
        target = nearest_support(swing_lows, entry)
        stop = nearest_resistance(swing_highs, entry)

    if target is None or stop is None:
        return None

    reward = abs(target - entry)
    risk = abs(entry - stop)
    if risk == 0:
        return None

    if signal.direction == "long":
        expected_return_pct = (target - entry) / entry
        strength = min(max((RSI_OVERSOLD - signal.previous_rsi) / RSI_OVERSOLD, Decimal("0")), Decimal("1"))
    else:
        expected_return_pct = (entry - target) / entry
        strength = min(max((signal.previous_rsi - RSI_OVERBOUGHT) / (Decimal("100") - RSI_OVERBOUGHT), Decimal("0")), Decimal("1"))

    risk_reward_score = min(reward / risk / RISK_REWARD_CAP, Decimal("1"))
    confidence_score = (Decimal("0.5") * risk_reward_score) + (Decimal("0.5") * strength)

    atr = average_true_range(klines, ATR_PERIOD)
    if atr > 0:
        hours = reward / atr
    else:
        hours = Decimal(MAX_EXPIRY_HOURS)
    hours = min(max(hours, Decimal(MIN_EXPIRY_HOURS)), Decimal(MAX_EXPIRY_HOURS))
    expires_at = now + timedelta(hours=float(hours))

    return ScenarioDraft(
        symbol=symbol,
        direction=signal.direction,
        entry_price=entry,
        target_price=target,
        stop_price=stop,
        expected_return_pct=expected_return_pct,
        confidence_score=confidence_score,
        created_at=now,
        expires_at=expires_at,
    )
