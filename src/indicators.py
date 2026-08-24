from __future__ import annotations

from decimal import Decimal


def compute_rsi(closes: list, period: int = 14) -> list:
    rsi_values = [None] * len(closes)
    if len(closes) <= period:
        return rsi_values

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, Decimal("0")))
        losses.append(max(-change, Decimal("0")))

    for i in range(period, len(closes)):
        window_gains = gains[i - period:i]
        window_losses = losses[i - period:i]
        avg_gain = sum(window_gains) / period
        avg_loss = sum(window_losses) / period
        if avg_loss == 0:
            rsi_values[i] = Decimal("100")
        else:
            rs = avg_gain / avg_loss
            rsi_values[i] = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    return rsi_values


def compute_ema(values: list, period: int) -> list:
    ema_values = [None] * len(values)
    if len(values) < period:
        return ema_values

    k = Decimal("2") / (Decimal(period) + Decimal("1"))
    sma = sum(values[:period]) / period
    ema_values[period - 1] = sma
    prev = sma
    for i in range(period, len(values)):
        current = values[i] * k + prev * (Decimal("1") - k)
        ema_values[i] = current
        prev = current
    return ema_values


def detect_ema_crossover(fast: list, slow: list) -> str:
    if len(fast) < 2 or len(slow) < 2:
        return "none"
    f_prev, f_curr = fast[-2], fast[-1]
    s_prev, s_curr = slow[-2], slow[-1]
    if f_prev is None or f_curr is None or s_prev is None or s_curr is None:
        return "none"
    if f_prev <= s_prev and f_curr > s_curr:
        return "bullish"
    if f_prev >= s_prev and f_curr < s_curr:
        return "bearish"
    return "none"


def detect_volume_spike(volumes: list, lookback: int = 20, multiplier: Decimal = Decimal("2")) -> bool:
    if len(volumes) < lookback + 1:
        return False
    current = volumes[-1]
    window = volumes[-lookback - 1:-1]
    avg = sum(window) / lookback
    if avg == 0:
        return False
    return current > avg * multiplier


def detect_confluence_in_window(
    fast: list, slow: list, volumes: list, lookback: int, multiplier: Decimal, window: int
) -> str:
    n = len(fast)
    for offset in range(window):
        end = n - offset
        if end < 2 or end < lookback + 1:
            break
        crossover = detect_ema_crossover(fast[:end], slow[:end])
        if crossover != "none" and detect_volume_spike(volumes[:end], lookback, multiplier):
            return crossover
    return "none"


def average_true_range(klines: list, period: int = 20) -> Decimal:
    if len(klines) < period + 1:
        return Decimal("0")
    true_ranges = []
    for i in range(1, len(klines)):
        high = klines[i]["high"]
        low = klines[i]["low"]
        prev_close = klines[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    recent = true_ranges[-period:]
    return sum(recent) / period
