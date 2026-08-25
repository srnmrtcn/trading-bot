from decimal import Decimal

import pytest

from src.paper_sizer import size_position
from src.paper_trading_config import MAX_LEVERAGE


def test_size_position_long():
    risk_amount, position_size = size_position(
        equity=Decimal("10000"), entry_price=Decimal("100"), stop_price=Decimal("90"), risk_pct=Decimal("0.01"),
    )
    assert risk_amount == Decimal("100")
    assert position_size == Decimal("10")  # 100 / |100 - 90|


def test_size_position_short():
    risk_amount, position_size = size_position(
        equity=Decimal("10000"), entry_price=Decimal("100"), stop_price=Decimal("110"), risk_pct=Decimal("0.01"),
    )
    assert risk_amount == Decimal("100")
    assert position_size == Decimal("10")  # 100 / |100 - 110|


def test_size_position_scales_with_equity():
    risk_amount, position_size = size_position(
        equity=Decimal("20000"), entry_price=Decimal("100"), stop_price=Decimal("90"), risk_pct=Decimal("0.01"),
    )
    assert risk_amount == Decimal("200")
    assert position_size == Decimal("20")


def test_size_position_raises_when_entry_equals_stop():
    with pytest.raises(ValueError):
        size_position(equity=Decimal("10000"), entry_price=Decimal("100"), stop_price=Decimal("100"), risk_pct=Decimal("0.01"))


def test_size_position_caps_notional_at_max_leverage():
    """Fixed-fractional sizing divides by the stop distance, and nothing in
    the scenario builder puts a floor under that distance: `nearest_support`
    can sit a rounding error below entry. A 0.01%-wide stop asks for 100x
    notional, where booked taker fees alone cost 10% of equity per round trip
    and a single stop-out is no longer bounded by the intended 1% risk.
    """
    risk_amount, position_size = size_position(
        equity=Decimal("10000"), entry_price=Decimal("100"),
        stop_price=Decimal("99.99"), risk_pct=Decimal("0.01"),
    )

    assert position_size * Decimal("100") == Decimal("10000") * MAX_LEVERAGE
    assert risk_amount == Decimal("100"), "the intended risk is unchanged; only the size is clamped"


def test_size_position_refuses_to_size_against_non_positive_equity():
    """A blown-up portfolio must not keep trading. Without this the negative
    equity flows into `risk_amount`, `position_size` comes back negative, and
    `_realized_pnl` starts booking wins as losses."""
    with pytest.raises(ValueError):
        size_position(
            equity=Decimal("0"), entry_price=Decimal("100"),
            stop_price=Decimal("90"), risk_pct=Decimal("0.01"),
        )
