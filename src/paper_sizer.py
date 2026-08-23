from __future__ import annotations

from decimal import Decimal


def size_position(equity: Decimal, entry_price: Decimal, stop_price: Decimal, risk_pct: Decimal) -> tuple:
    """Fixed-fractional position sizing.

    Sizes the position so that if price reaches `stop_price`, the loss equals
    exactly `equity * risk_pct`. Returns `(risk_amount, position_size)`.
    """
    if entry_price == stop_price:
        raise ValueError("entry_price and stop_price must differ")
    risk_amount = equity * risk_pct
    position_size = risk_amount / abs(entry_price - stop_price)
    return risk_amount, position_size
