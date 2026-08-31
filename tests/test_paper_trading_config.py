from decimal import Decimal

import pytest

from src.paper_trading_config import (
    MAX_CONCURRENT_POSITIONS,
    MAX_LEVERAGE,
    MAX_TOTAL_NOTIONAL_MULTIPLE,
    MIN_EXPECTED_R,
    RISK_PCT,
    STARTING_EQUITY,
    TAKER_FEE_RATE,
)
from src.trading_costs import TAKER_FEE_RATE as TRADING_COSTS_TAKER_FEE_RATE


def test_taker_fee_rate_imported_from_trading_costs():
    assert TAKER_FEE_RATE is TRADING_COSTS_TAKER_FEE_RATE


def test_constants_have_correct_values():
    assert STARTING_EQUITY == Decimal("10000")
    assert RISK_PCT == Decimal("0.01")
    assert MIN_EXPECTED_R == Decimal("0.1")
    assert MAX_CONCURRENT_POSITIONS == 10
    assert MAX_LEVERAGE == Decimal("3")
    assert MAX_TOTAL_NOTIONAL_MULTIPLE == Decimal("10")