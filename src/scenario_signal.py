from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.indicators import compute_ema, compute_rsi, detect_confluence_in_window

RSI_PERIOD = 14
EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 21
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLIER = Decimal("2")
RSI_OVERSOLD = Decimal("30")
RSI_OVERBOUGHT = Decimal("70")
MIN_CANDLES = 100
CONFLUENCE_WINDOW = 3


@dataclass
class SignalResult:
    direction: str
    entry_price: Decimal
    rsi: Decimal
    previous_rsi: Decimal


def evaluate_signal(klines: list):
    if len(klines) < MIN_CANDLES:
        return None

    closes = [c["close"] for c in klines]
    volumes = [c["volume"] for c in klines]

    rsi_series = compute_rsi(closes, RSI_PERIOD)
    ema_fast = compute_ema(closes, EMA_FAST_PERIOD)
    ema_slow = compute_ema(closes, EMA_SLOW_PERIOD)
    crossover = detect_confluence_in_window(
        ema_fast, ema_slow, volumes, VOLUME_LOOKBACK, VOLUME_MULTIPLIER, CONFLUENCE_WINDOW
    )

    current_rsi = rsi_series[-1]
    previous_rsi = rsi_series[-2]
    if current_rsi is None or previous_rsi is None:
        return None

    entry_price = closes[-1]

    if previous_rsi < RSI_OVERSOLD <= current_rsi and crossover == "bullish":
        return SignalResult(direction="long", entry_price=entry_price, rsi=current_rsi, previous_rsi=previous_rsi)
    if previous_rsi > RSI_OVERBOUGHT >= current_rsi and crossover == "bearish":
        return SignalResult(direction="short", entry_price=entry_price, rsi=current_rsi, previous_rsi=previous_rsi)
    return None
