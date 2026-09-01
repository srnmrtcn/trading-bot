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
DELAYED_CONFIRM_WINDOW = 15


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


def crossed_up_recently(rsi_series: list, window: int) -> bool:
    """
    RSI serisinin SON `window` kapali mumunda asiri satim cizgisi yukari kesilmis mi?
    """
    for index in range(len(rsi_series) - 1, max(len(rsi_series) - 1 - window, 0), -1):
        current, previous = rsi_series[index], rsi_series[index - 1]
        if current is None or previous is None:
            continue
        if previous < RSI_OVERSOLD <= current:
            return True
    return False


def evaluate_signal_delayed(klines: list, window: int = DELAYED_CONFIRM_WINDOW):
    """
    Gecikmeli kural: RSI asiri satim cizgisini SON `window` kapali mum icinde yukari kesmis olsun,
    VE son 3 mumluk pencerede hacim destekli bogal EMA kesisimi olsun.
    """
    if len(klines) < MIN_CANDLES:
        return None
    closes = [c["close"] for c in klines]
    volumes = [c["volume"] for c in klines]
    rsi_series = compute_rsi(closes, RSI_PERIOD)
    if not crossed_up_recently(rsi_series, window):
        return None
    current_rsi, previous_rsi = rsi_series[-1], rsi_series[-2]
    if current_rsi is None or previous_rsi is None:
        return None
    crossover = detect_confluence_in_window(
        compute_ema(closes, EMA_FAST_PERIOD), compute_ema(closes, EMA_SLOW_PERIOD),
        volumes, VOLUME_LOOKBACK, VOLUME_MULTIPLIER, CONFLUENCE_WINDOW,
    )
    if crossover != "bullish":
        return None
    return SignalResult(direction="long", entry_price=closes[-1],
                        rsi=current_rsi, previous_rsi=previous_rsi)