"""Shared trading-cost calculations for paper trading and replay."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal


TAKER_FEE_RATE = Decimal("0.0005")
SLIPPAGE_BPS = Decimal("5")


def round_trip_cost(
    position_size: Decimal,
    entry_price: Decimal,
    exit_price: Decimal,
) -> Decimal:
    """Return entry and exit taker fees plus assumed slippage."""
    raise NotImplementedError


def fee_and_slippage_cost_in_r(
    entry_price: Decimal,
    stop_price: Decimal,
) -> Decimal:
    """Return approximate round-trip fee and slippage cost in risk units."""
    raise NotImplementedError


def funding_cost(
    direction: str,
    position_size: Decimal,
    events: list[tuple[datetime, Decimal, Decimal]],
) -> Decimal:
    """Return signed funding cost; positive means paid and negative received."""
    raise NotImplementedError
