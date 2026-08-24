from datetime import datetime, timedelta
from decimal import Decimal

from src.scenario_signal import evaluate_signal, MIN_CANDLES


def _flat_klines(count, price=Decimal("100"), volume=Decimal("1000")):
    return [
        {
            "open_time": datetime(2026, 1, 1) + timedelta(hours=i),
            "open": price, "high": price, "low": price, "close": price, "volume": volume,
        }
        for i in range(count)
    ]


def test_evaluate_signal_returns_none_with_insufficient_candles():
    klines = _flat_klines(MIN_CANDLES - 1)
    assert evaluate_signal(klines) is None


def test_evaluate_signal_returns_none_for_flat_uneventful_data():
    klines = _flat_klines(MIN_CANDLES)
    assert evaluate_signal(klines) is None


def test_evaluate_signal_detects_long_setup():
    # Build a long, gentle downtrend (pushes RSI well below 30, EMA9 below EMA21),
    # then a sharp reversal candle with a volume spike that pushes RSI back
    # above 30 and crosses EMA9 above EMA21.
    klines = _flat_klines(60, price=Decimal("100"))
    price = Decimal("100")
    downtrend = []
    for i in range(60):
        price -= Decimal("1")
        downtrend.append({
            "open_time": datetime(2026, 1, 1) + timedelta(hours=60 + i),
            "open": price, "high": price, "low": price, "close": price, "volume": Decimal("1000"),
        })
    klines = klines + downtrend
    last_price = downtrend[-1]["close"]
    reversal_price = last_price + Decimal("80")
    reversal = {
        "open_time": datetime(2026, 1, 1) + timedelta(hours=120),
        "open": last_price, "high": reversal_price, "low": last_price,
        "close": reversal_price, "volume": Decimal("5000"),
    }
    klines = klines + [reversal]

    signal = evaluate_signal(klines)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == reversal_price


def test_evaluate_signal_rejects_a_crossover_and_volume_spike_on_different_candles():
    # Same downtrend-then-reversal shape as test_evaluate_signal_detects_long_setup,
    # but the volume spike lands on the second-to-last downtrend candle while the
    # EMA crossover (and the RSI reversal) land on the reversal candle. Even though
    # both events fall inside the confluence window, they never share a candle, so
    # this must not count as confluence.
    klines = _flat_klines(60, price=Decimal("100"))
    price = Decimal("100")
    downtrend = []
    for i in range(60):
        price -= Decimal("1")
        volume = Decimal("5000") if i == 58 else Decimal("1000")
        downtrend.append({
            "open_time": datetime(2026, 1, 1) + timedelta(hours=60 + i),
            "open": price, "high": price, "low": price, "close": price, "volume": volume,
        })
    klines = klines + downtrend
    last_price = downtrend[-1]["close"]
    reversal_price = last_price + Decimal("80")
    reversal = {
        "open_time": datetime(2026, 1, 1) + timedelta(hours=120),
        "open": last_price, "high": reversal_price, "low": last_price,
        "close": reversal_price, "volume": Decimal("1000"),
    }
    klines = klines + [reversal]

    assert evaluate_signal(klines) is None


def test_evaluate_signal_detects_short_setup():
    klines = _flat_klines(60, price=Decimal("100"))
    price = Decimal("100")
    uptrend = []
    for i in range(60):
        price += Decimal("1")
        uptrend.append({
            "open_time": datetime(2026, 1, 1) + timedelta(hours=60 + i),
            "open": price, "high": price, "low": price, "close": price, "volume": Decimal("1000"),
        })
    klines = klines + uptrend
    last_price = uptrend[-1]["close"]
    reversal_price = last_price - Decimal("80")
    reversal = {
        "open_time": datetime(2026, 1, 1) + timedelta(hours=120),
        "open": last_price, "high": last_price, "low": reversal_price,
        "close": reversal_price, "volume": Decimal("5000"),
    }
    klines = klines + [reversal]

    signal = evaluate_signal(klines)

    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == reversal_price
