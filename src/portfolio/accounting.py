"""Sizing and P&L for the dollar-neutral momentum book.

Costs are NOT here. src/trading_costs.py already owns round-trip fees and
funding, and the funding one is the better implementation: it charges each
settlement against the mark price that was actually printed, rather than
against the entry price for the whole window. Two copies of cost arithmetic
in one repository is how a fee change lands in one path and not the other.
"""
from __future__ import annotations

from decimal import Decimal


def position_sizes(equity, symbols: list, prices: dict, leg_exposure) -> dict:
    """
    Bir bacaktaki her sembol icin kac BIRIM alinacagini/satilacagini dondurur.
    """
    if not symbols or equity <= 0:
        return {}
    per_name = Decimal(equity) * Decimal(leg_exposure) / Decimal(len(symbols))
    sizes = {}
    for symbol in symbols:
        price = prices.get(symbol)
        if price is None or price <= 0:
            continue
        sizes[symbol] = per_name / price
    return sizes


def position_pnl(direction: str, entry_price, exit_price, size) -> Decimal:
    """
    Komisyon ve funding HARIC ham kar/zarar.
    """
    if direction == "long":
        return (exit_price - entry_price) * size
    if direction == "short":
        return (entry_price - exit_price) * size
    raise ValueError("unknown direction: %r" % direction)
