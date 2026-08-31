from __future__ import annotations

from decimal import Decimal

from src.db.models import PaperPosition
from src.paper_trading_config import STARTING_EQUITY
from src.strategy_version import STRATEGY_VERSION


def current_equity(session) -> Decimal:
    """The simulated portfolio's current equity.

    There is no separate running total to keep in sync: each closed
    PaperPosition row already records the equity it produced (`equity_after`),
    so the most recently closed position's `equity_after` IS the current
    equity. Before any position has ever closed, equity is the starting
    constant.
    
    Only considers positions with the current strategy version and status 'closed'.
    Legacy or other-version positions are ignored.
    """
    last_closed = (
        session.query(PaperPosition)
        .filter(
            PaperPosition.status == "closed",
            PaperPosition.strategy_version == STRATEGY_VERSION,
            # A closed row without an `equity_after` carries no equity
            # information; skipping it falls back to the last row that does
            # rather than returning None into every caller's arithmetic.
            PaperPosition.equity_after.isnot(None),
        )
        .order_by(PaperPosition.closed_at.desc(), PaperPosition.id.desc())
        .first()
    )
    return last_closed.equity_after if last_closed is not None else STARTING_EQUITY