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
    if position_size == Decimal("0"):
        return Decimal("0")

    cost_rate = TAKER_FEE_RATE + SLIPPAGE_BPS / Decimal("10000")
    entry_cost = position_size * entry_price * cost_rate
    exit_cost = position_size * exit_price * cost_rate
    return entry_cost + exit_cost


def fee_and_slippage_cost_in_r(
    entry_price: Decimal,
    stop_price: Decimal,
) -> Decimal:
    """Return approximate round-trip fee and slippage cost in risk units."""
    risk = abs(entry_price - stop_price)
    if risk == Decimal("0"):
        raise ValueError("stop equals entry")

    cost_rate = TAKER_FEE_RATE + SLIPPAGE_BPS / Decimal("10000")
    return Decimal("2") * entry_price * cost_rate / risk


def funding_cost(
    direction: str,
    position_size: Decimal,
    events: list[tuple[datetime, Decimal, Decimal]],
) -> Decimal:
    """Return signed funding cost; positive means paid and negative received."""
    if direction not in ("long", "short"):
        raise ValueError("direction")

    total_cost = Decimal("0")
    for funding_time, funding_rate, mark_price in events:
        payment = funding_rate * position_size * mark_price
        if direction == "short":
            payment = -payment
        total_cost += payment

    return total_cost
