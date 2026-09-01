from __future__ import annotations

from datetime import datetime, timedelta

from src.db.models import Kline, PortfolioSnapshot
from src.integrity import floor_to_timeframe
from src.portfolio.config import REBALANCE_DAYS, STRATEGY_VERSION


def is_rebalance_due(session, now: datetime) -> bool:
    """
    Returns True if a rebalance is due based on the last snapshot and current time.
    """
    raise NotImplementedError


def load_daily_bars(session, now: datetime, days: int) -> dict:
    """
    Loads daily bars for symbols within the specified date range.
    """
    raise NotImplementedError


def record_snapshot(session, as_of: datetime, equity, closed: int, opened: int):
    """
    Records a portfolio snapshot to the database.
    """
    raise NotImplementedError
