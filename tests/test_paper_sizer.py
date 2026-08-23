from decimal import Decimal

import pytest

from src.paper_sizer import size_position


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
