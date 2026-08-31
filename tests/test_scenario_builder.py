from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import src.scenario_builder as scenario_builder_module
from src.scenario_builder import (
    MAX_EXPIRY_HOURS,
    MIN_EXPIRY_HOURS,
    MIN_STOP_PCT,
    build_scenario,
)
from src.scenario_signal import SignalResult
from src.support_resistance import SwingPoint


def _kline(hour, high, low, close=None):
    if close is None:
        close = (Decimal(str(high)) + Decimal(str(low))) / 2
    else:
        close = Decimal(str(close))
    return {
        "open_time": datetime(2026, 1, 1) + timedelta(hours=hour),
        "open": close, "high": Decimal(str(high)), "low": Decimal(str(low)), "close": close,
        "volume": Decimal("1000"),
    }


def _klines_with_swings():
    # A clear swing low at index 5 (90) below, a clear swing high at index 15
    # (130) above, entry sits between them.
    klines = [_kline(i, 105, 95) for i in range(30)]
    klines[5] = _kline(5, 91, 90)
    klines[15] = _kline(15, 130, 129)
    return klines


def test_build_scenario_long_uses_resistance_as_target_and_support_as_stop():
    klines = _klines_with_swings()
    signal = SignalResult(direction="long", entry_price=Decimal("100"), rsi=Decimal("35"), previous_rsi=Decimal("25"))
    now = datetime(2026, 1, 2)

    draft = build_scenario("BTCUSDT", signal, klines, now)

    assert draft is not None
    assert draft.symbol == "BTCUSDT"
    assert draft.direction == "long"
    assert draft.entry_price == Decimal("100")
    assert draft.target_price == Decimal("130")
    assert draft.stop_price == Decimal("90")
    assert draft.expected_return_pct == (Decimal("130") - Decimal("100")) / Decimal("100")
    assert Decimal("0") <= draft.confidence_score <= Decimal("1")
    assert draft.created_at == now
    assert MIN_EXPIRY_HOURS <= (draft.expires_at - now).total_seconds() / 3600 <= MAX_EXPIRY_HOURS


def test_build_scenario_short_uses_support_as_target_and_resistance_as_stop():
    klines = _klines_with_swings()
    signal = SignalResult(direction="short", entry_price=Decimal("100"), rsi=Decimal("65"), previous_rsi=Decimal("75"))
    now = datetime(2026, 1, 2)

    draft = build_scenario("BTCUSDT", signal, klines, now)

    assert draft is not None
    assert draft.target_price == Decimal("90")
    assert draft.stop_price == Decimal("130")
    assert draft.expected_return_pct == (Decimal("100") - Decimal("90")) / Decimal("100")


def test_build_scenario_returns_none_when_no_resistance_above_entry():
    # Entry above every swing high in the data -> no resistance found.
    klines = [_kline(i, 105, 95) for i in range(30)]
    klines[5] = _kline(5, 91, 90)
    signal = SignalResult(direction="long", entry_price=Decimal("200"), rsi=Decimal("35"), previous_rsi=Decimal("25"))
    now = datetime(2026, 1, 2)

    assert build_scenario("BTCUSDT", signal, klines, now) is None


def _build_with_stop(monkeypatch, stop_price):
    now = datetime(2026, 1, 2)
    monkeypatch.setattr(
        scenario_builder_module,
        "find_swing_points",
        lambda klines, k: (
            [SwingPoint(open_time=now, price=Decimal("110"))],
            [SwingPoint(open_time=now, price=stop_price)],
        ),
    )
    signal = SignalResult(
        direction="long",
        entry_price=Decimal("100"),
        rsi=Decimal("35"),
        previous_rsi=Decimal("25"),
    )
    return build_scenario("BTCUSDT", signal, _klines_with_swings(), now)


def test_min_stop_pct_is_the_shared_production_strategy_parameter():
    assert MIN_STOP_PCT == Decimal("0.005")


@pytest.mark.parametrize(
    ("stop_price", "accepted"),
    [
        (Decimal("99.6"), False),
        (Decimal("99.5"), True),
        (Decimal("99"), True),
    ],
)
def test_build_scenario_rejects_only_stops_narrower_than_the_minimum(
    monkeypatch, stop_price, accepted,
):
    draft = _build_with_stop(monkeypatch, stop_price)
    assert (draft is not None) is accepted
