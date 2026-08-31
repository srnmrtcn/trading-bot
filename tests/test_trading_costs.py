from datetime import datetime
from decimal import Decimal

import pytest

from src.trading_costs import (
    SLIPPAGE_BPS,
    TAKER_FEE_RATE,
    fee_and_slippage_cost_in_r,
    funding_cost,
    round_trip_cost,
)


def test_constants_define_the_single_cost_model():
    assert TAKER_FEE_RATE == Decimal("0.0005")
    assert SLIPPAGE_BPS == Decimal("5")


def test_round_trip_cost_uses_each_legs_own_notional():
    assert round_trip_cost(
        Decimal("10"), Decimal("100"), Decimal("110")
    ) == Decimal("2.100")


def test_round_trip_cost_is_zero_for_zero_size():
    result = round_trip_cost(Decimal("0"), Decimal("100"), Decimal("110"))
    assert result == Decimal("0")
    assert isinstance(result, Decimal)


@pytest.mark.parametrize("stop", [Decimal("99"), Decimal("101")])
def test_cost_in_r_is_direction_agnostic(stop):
    assert fee_and_slippage_cost_in_r(Decimal("100"), stop) == Decimal("0.2")


def test_cost_in_r_exposes_fee_dominant_tight_stops():
    assert fee_and_slippage_cost_in_r(
        Decimal("100"), Decimal("99.9")
    ) == Decimal("2.0")


def test_cost_in_r_rejects_zero_risk():
    with pytest.raises(ValueError, match="stop equals entry"):
        fee_and_slippage_cost_in_r(Decimal("100"), Decimal("100"))


def test_funding_cost_long_pays_positive_and_receives_negative_rates():
    events = [
        (datetime(2026, 8, 31, 8), Decimal("0.0001"), Decimal("100")),
        (datetime(2026, 8, 31, 16), Decimal("-0.0002"), Decimal("100")),
    ]
    assert funding_cost("long", Decimal("10"), events) == Decimal("-0.1")


def test_funding_cost_short_has_the_opposite_sign():
    events = [
        (datetime(2026, 8, 31, 8), Decimal("0.0001"), Decimal("100")),
    ]
    assert funding_cost("short", Decimal("10"), events) == Decimal("-0.1")


def test_funding_cost_empty_events_is_decimal_zero():
    result = funding_cost("long", Decimal("10"), [])
    assert result == Decimal("0")
    assert isinstance(result, Decimal)


def test_funding_cost_validates_direction_even_when_events_are_empty():
    with pytest.raises(ValueError, match="direction"):
        funding_cost("invalid", Decimal("10"), [])
