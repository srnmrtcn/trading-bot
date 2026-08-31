from __future__ import annotations

from decimal import Decimal

from src.paper_trading_config import MAX_LEVERAGE


def size_position(equity: Decimal, entry_price: Decimal, stop_price: Decimal, risk_pct: Decimal) -> tuple:
    """Fixed-fractional position sizing.

    Sizes the position so that if price reaches `stop_price`, the loss equals
    exactly `equity * risk_pct` — unless that would need more notional than
    `MAX_LEVERAGE` allows, in which case the size is clamped and the position
    simply risks less than the target. Returns `(risk_amount, position_size)`.
    """
    if entry_price == stop_price:
        raise ValueError("entry_price and stop_price must differ")
    if equity <= 0:
        # Negative equity would flow through to a negative position_size, and
        # from there `_realized_pnl` books every win as a loss.
        raise ValueError("cannot size a position against non-positive equity")

    target_risk = equity * risk_pct
    stop_distance = abs(entry_price - stop_price)
    requested_size = target_risk / stop_distance
    max_position_size = equity * MAX_LEVERAGE / entry_price
    position_size = min(requested_size, max_position_size)
    risk_amount = position_size * stop_distance
    return risk_amount, position_size
