from decimal import Decimal

from src.indicators import (
    compute_rsi, compute_ema, detect_ema_crossover, detect_volume_spike, average_true_range,
    detect_confluence_in_window,
)


def _decimals(values):
    return [Decimal(str(v)) for v in values]


def test_compute_rsi_matches_hand_calculation():
    # 15 closes: 14 up-moves of +1 each starting at 100 -> RSI should be 100
    # (all gains, zero losses).
    closes = _decimals([100 + i for i in range(15)])
    rsi = compute_rsi(closes, period=14)
    assert rsi[:14] == [None] * 14
    assert rsi[14] == Decimal("100")


def test_compute_rsi_is_50_for_equal_gains_and_losses():
    # Alternating +1/-1 moves of equal size over the period -> avg gain == avg loss -> RSI 50.
    closes = _decimals([100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100])
    rsi = compute_rsi(closes, period=14)
    assert rsi[14] == Decimal("50")


def test_compute_ema_seeds_with_sma_then_smooths():
    values = _decimals([1, 2, 3, 4, 5])
    ema = compute_ema(values, period=3)
    assert ema[0] is None
    assert ema[1] is None
    assert ema[2] == Decimal("2")  # SMA of [1,2,3]
    k = Decimal("2") / Decimal("4")  # 2/(period+1)
    expected_3 = values[3] * k + ema[2] * (Decimal("1") - k)
    assert ema[3] == expected_3


def test_detect_ema_crossover_bullish():
    fast = [Decimal("9"), Decimal("11")]
    slow = [Decimal("10"), Decimal("10")]
    assert detect_ema_crossover(fast, slow) == "bullish"


def test_detect_ema_crossover_bearish():
    fast = [Decimal("11"), Decimal("9")]
    slow = [Decimal("10"), Decimal("10")]
    assert detect_ema_crossover(fast, slow) == "bearish"


def test_detect_ema_crossover_none_when_no_cross():
    fast = [Decimal("12"), Decimal("13")]
    slow = [Decimal("10"), Decimal("10")]
    assert detect_ema_crossover(fast, slow) == "none"


def test_detect_volume_spike_true_when_current_exceeds_multiplier():
    volumes = _decimals([100] * 20 + [250])  # avg of first 20 = 100, current 250 > 2x
    assert detect_volume_spike(volumes, lookback=20, multiplier=Decimal("2")) is True


def test_detect_volume_spike_false_when_below_multiplier():
    volumes = _decimals([100] * 20 + [150])
    assert detect_volume_spike(volumes, lookback=20, multiplier=Decimal("2")) is False


def test_average_true_range_computes_expected_value():
    klines = [
        {"high": Decimal("110"), "low": Decimal("90"), "close": Decimal("100")},
        {"high": Decimal("115"), "low": Decimal("95"), "close": Decimal("105")},
    ]
    # true range for the 2nd candle: max(115-95, |115-100|, |95-100|) = max(20, 15, 5) = 20
    atr = average_true_range(klines, period=1)
    assert atr == Decimal("20")


def test_average_true_range_returns_zero_with_insufficient_data():
    klines = [{"high": Decimal("110"), "low": Decimal("90"), "close": Decimal("100")}]
    assert average_true_range(klines, period=20) == Decimal("0")


def test_detect_confluence_in_window_finds_cross_and_spike_together_two_candles_back():
    fast = _decimals([9, 9, 11, 11, 11])
    slow = _decimals([10, 10, 10, 10, 10])
    volumes = _decimals([100, 100, 300, 100, 100])
    assert detect_confluence_in_window(fast, slow, volumes, lookback=2, multiplier=Decimal("2"), window=3) == "bullish"


def test_detect_confluence_in_window_still_detects_same_candle_match():
    fast = _decimals([9, 11])
    slow = _decimals([10, 10])
    volumes = _decimals([100, 300])
    assert detect_confluence_in_window(fast, slow, volumes, lookback=1, multiplier=Decimal("2"), window=3) == "bullish"


def test_detect_confluence_in_window_returns_none_when_match_is_outside_window():
    fast = _decimals([9, 9, 11, 11, 11, 11])
    slow = _decimals([10, 10, 10, 10, 10, 10])
    volumes = _decimals([100, 100, 300, 100, 100, 100])
    assert detect_confluence_in_window(fast, slow, volumes, lookback=2, multiplier=Decimal("2"), window=3) == "none"


def test_detect_confluence_in_window_requires_cross_and_spike_on_the_same_candle():
    # Crossover happens two candles back; the volume spike happens on the current
    # candle instead. Neither the crossover nor the spike is stale on its own, but
    # they never land on the same candle, so this must not count as confluence.
    fast = _decimals([9, 9, 11, 11, 11])
    slow = _decimals([10, 10, 10, 10, 10])
    volumes = _decimals([100, 100, 100, 100, 300])
    assert detect_confluence_in_window(fast, slow, volumes, lookback=2, multiplier=Decimal("2"), window=3) == "none"
