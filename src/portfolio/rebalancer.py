from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import dataclass
from decimal import Decimal

from src.db.models import Kline, PortfolioSnapshot
from src.integrity import floor_to_timeframe
from src.portfolio.config import REBALANCE_DAYS, STRATEGY_VERSION
from src.funding_collector import funding_events_between
from src.portfolio.accounting import position_sizes
from src.portfolio.book import close_position, open_positions, portfolio_equity, record_open
from src.portfolio.config import (
    LEG_EXPOSURE,
    LIQUIDITY_WINDOW_DAYS,
    LOOKBACK_DAYS,
    MIN_DOLLAR_VOLUME,
    MIN_UNIVERSE,
    SIGNAL_SKIP_DAYS,
    TOP_FRACTION,
)
from src.portfolio.selection import book_for, closes_by_day
from src.timeutil import utc_now


def is_rebalance_due(session, now: datetime) -> bool:
    """
    Returns True if a rebalance is due based on the last snapshot and current time.
    """
    last = (
        session.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.strategy_version == STRATEGY_VERSION)
        .order_by(PortfolioSnapshot.as_of.desc(), PortfolioSnapshot.id.desc())
        .first()
    )
    if last is None:
        return True
    return now - last.as_of >= timedelta(days=REBALANCE_DAYS)


def load_daily_bars(session, now: datetime, days: int) -> dict:
    """
    Loads daily bars for symbols within the specified date range.
    """
    boundary = floor_to_timeframe(now, "1d")
    rows = (
        session.query(Kline)
        .filter(
            Kline.timeframe == "1d",
            Kline.open_time < boundary,
            Kline.open_time >= boundary - timedelta(days=days),
        )
        .order_by(Kline.symbol.asc(), Kline.open_time.asc())
        .all()
    )
    bars = {}
    for row in rows:
        bars.setdefault(row.symbol, []).append(
            {"open_time": row.open_time, "close": row.close, "volume": row.volume}
        )
    return bars


def record_snapshot(session, as_of: datetime, equity, closed: int, opened: int):
    """
    Records a portfolio snapshot to the database.
    """
    snapshot = PortfolioSnapshot(
        strategy_version=STRATEGY_VERSION, as_of=as_of, equity=equity,
        positions_closed=closed, positions_opened=opened,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


@dataclass
class RebalanceResult:
    acted: bool
    closed: int
    opened: int
    equity: Decimal
    universe: int


def run_rebalance(session, now: datetime = None) -> RebalanceResult:
    """
    Executes a rebalance operation, closing existing positions and opening new ones.
    """
    raise NotImplementedError
