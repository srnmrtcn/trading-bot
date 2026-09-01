import pytest

from src.scenario_signal import (
    DELAYED_CONFIRM_WINDOW,
    MIN_CANDLES,
    SignalResult,
    crossed_up_recently,
    evaluate_signal,
    evaluate_signal_delayed,
)
from decimal import Decimal


def test_crossed_up_recently_last_mum():
    rsi = [Decimal(40), Decimal(35), Decimal(29), Decimal(31)]
    assert crossed_up_recently(rsi, 1) is True


def test_crossed_up_recently_window_boundary():
    rsi = [Decimal(40), Decimal(29), Decimal(31), Decimal(40), Decimal(40), Decimal(40)]
    assert crossed_up_recently(rsi, 3) is False
    assert crossed_up_recently(rsi, 4) is True


def test_crossed_up_recently_no_crossing():
    rsi = [Decimal(40), Decimal(45), Decimal(50), Decimal(55)]
    assert crossed_up_recently(rsi, 3) is False


def test_crossed_up_recently_none_elements():
    rsi = [None, None, Decimal(29), Decimal(31)]
    assert crossed_up_recently(rsi, 3) is True


def test_evaluate_signal_delayed_signal_fires_when_normal_signal_does_not():
    closes = [Decimal(100)]
    for _ in range(90):
        closes.append(closes[-1] * Decimal('0.99'))
    for _ in range(9):
        closes.append(closes[-1] * Decimal('1.02'))

    volumes = [Decimal(100)] * 100
    volumes[-1] = Decimal(400)
    volumes[-2] = Decimal(400)

    klines = []
    for i, (close, volume) in enumerate(zip(closes, volumes)):
        klines.append({
            'open_time': i,
            'open': close,
            'high': close,
            'low': close,
            'close': close,
            'volume': volume,
            'flagged': False
        })

    # evaluate_signal should return None because the RSI crossover is not in the last candle
    assert evaluate_signal(klines) is None

    # evaluate_signal_delayed should return a SignalResult because the conditions are met
    result = evaluate_signal_delayed(klines)
    assert result is not None
    assert result.direction == 'long'
    assert result.entry_price == klines[-1]['close']


def test_evaluate_signal_delayed_window_matters():
    closes = [Decimal(100)]
    for _ in range(90):
        closes.append(closes[-1] * Decimal('0.99'))
    for _ in range(9):
        closes.append(closes[-1] * Decimal('1.02'))

    volumes = [Decimal(100)] * 100
    volumes[-1] = Decimal(400)
    volumes[-2] = Decimal(400)

    klines = []
    for i, (close, volume) in enumerate(zip(closes, volumes)):
        klines.append({
            'open_time': i,
            'open': close,
            'high': close,
            'low': close,
            'close': close,
            'volume': volume,
            'flagged': False
        })

    # The RSI crossover is 6 candles back, so with window=6 it should not trigger
    assert evaluate_signal_delayed(klines, window=6) is None

    # With window=7 it should trigger
    result = evaluate_signal_delayed(klines, window=7)
    assert result is not None
    assert result.direction == 'long'


def test_evaluate_signal_delayed_no_volume_spike():
    closes = [Decimal(100)]
    for _ in range(90):
        closes.append(closes[-1] * Decimal('0.99'))
    for _ in range(9):
        closes.append(closes[-1] * Decimal('1.02'))

    volumes = [Decimal(100)] * 100

    klines = []
    for i, (close, volume) in enumerate(zip(closes, volumes)):
        klines.append({
            'open_time': i,
            'open': close,
            'high': close,
            'low': close,
            'close': close,
            'volume': volume,
            'flagged': False
        })

    # No volume spike, so should return None
    assert evaluate_signal_delayed(klines) is None


def test_evaluate_signal_delayed_insufficient_candles():
    closes = [Decimal(100)]
    for _ in range(49):
        closes.append(closes[-1] * Decimal('0.99'))
    for _ in range(9):
        closes.append(closes[-1] * Decimal('1.02'))

    volumes = [Decimal(100)] * 50
    volumes[-1] = Decimal(400)
    volumes[-2] = Decimal(400)

    klines = []
    for i, (close, volume) in enumerate(zip(closes, volumes)):
        klines.append({
            'open_time': i,
            'open': close,
            'high': close,
            'low': close,
            'close': close,
            'volume': volume,
            'flagged': False
        })

    # Not enough candles, so should return None
    assert evaluate_signal_delayed(klines) is None
